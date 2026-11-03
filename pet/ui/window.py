# -*- coding: utf-8 -*-
"""
桌宠窗口 —— 一个透明、无边框、永远置顶、不抢焦点的小窗口。

Windows 上做"桌宠"的几个关键点，都在这一个文件里解决：

1. 逐像素透明      Qt.WA_TranslucentBackground + 无边框。用色键（transparentcolor）
                   那种方案边缘会有锯齿，看起来就像贴了张廉价 PNG。
2. 不进任务栏      Qt.Tool 标志，任务栏只留托盘图标。
3. 不抢焦点        WA_ShowWithoutActivating：弹出时不会把你正在打的字打断。
4. 可选全屏穿透    WS_EX_TRANSPARENT：宠物不挡点击（托盘里可切换）。
5. 不遮挡工作区    定位一律夹在 availableGeometry 内，不会跑到任务栏底下。

动画在 60FPS 的 QTimer 里推进，所有运动量都是"插值接近目标值"，
所以窗口卡一帧也不会跳变——桌宠最怕的就是一顿一顿。
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import time
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QCursor, QColor, QGuiApplication, QPainter,
                         QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from ..core import windowinfo
from ..core.bus import EventBus
from ..core.config import Config
from ..core.events import (ActionDone, ContinuousAction, ControlToggled,
                           EmotionChanged, Event, HandsSeen, PerceptionFailed,
                           PerceptionReady, PetGame, PetSay, PresenceChanged)
from .paint import (PetPose, draw_aura, draw_bubble, draw_glow,
                    draw_mood_fx, draw_pet, draw_shadow, ui_font)
from .sprite import SpriteSet

log = logging.getLogger(__name__)

# ---- Win32 常量（只在 Windows 上有意义） ----
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


class PetWindow(QWidget):
    def __init__(self, bus: EventBus, cfg: Config, control=None,
                 bond=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.bus = bus
        # ControlCenter：关手势识别、退出程序这类"要动全局状态"的事都通过它
        # 走，窗口自己不改引擎/摄像头的状态（见 core/control.py 的注释）
        self.control = control

        # 亲密度存档，可以为 None（headless / 单元测试 / 目录不可写）
        self.bond = bond
        self._bond_dirty = False
        self._bond_saved = 0.0
        self._enabled = bool(getattr(control, "enabled", True))
        self.scale = max(0.6, min(2.0, cfg.pet_scale))
        # 有立绘就用立绘，没有就退回程序化矢量版（两套共用同一套姿态参数）
        self._sprite = SpriteSet.from_config(cfg)
        self._aspect = self._sprite.aspect if self._sprite else 0.78

        # ---- 尺寸：上面留气泡，下面站宠物 ----
        # 鲸鱼娘是竖长条（水花 + 头 + 身 + 尾），高度定尺寸、宽度按比例推，
        # 和 paint.render_pet 里的 0.78 保持一致，换成贴图时也不会走形。
        self.body_h = 152.0 * self.scale
        self.body_w = self.body_h * self._aspect
        self.win_w = int(self.body_w * 1.95)
        # 高度 = 身体 + 上方气泡空间 + 下方余量（进度环会画到身体外面，
        # 底部留十几 px 才不会被窗口边缘切掉）
        self.win_h = int(self.body_h + 150 * self.scale)
        self.pet_cx = self.win_w / 2.0
        self.pet_cy = self.win_h - self.body_h * 0.55 - 16

        # ---- 右上角的关闭键 ----
        # 半径跟着缩放走，但夹在 9~13px：太小点不中，太大会盖住头顶的水花。
        # 位置贴着窗口右上角 —— 和普通窗口的关闭键同一个位置，用户不用学。
        self._btn_r = _clamp(11.0 * self.scale, 9.0, 13.0)
        self._btn_c = QPointF(self.win_w - self._btn_r - 6, self._btn_r + 6)
        self._hover_btn = False

        # ---- 跟鼠标时的"自动穿透"状态 ----
        # 跟鼠标的宠物整天在指针附近晃，如果窗口一直吃鼠标事件，用户点什么都点不到
        # （这是用户报的 bug）。所以指针不在宠物身上时窗口整体鼠标穿透；
        # 指针停在宠物身体上一小会儿，才解除穿透让人点它。
        self._auto_through = False
        self._hover_dwell = 0.0            # 指针在身体上已经停了多久

        self._panel = None                  # 弹出式控制面板（懒创建）
        self._bindings = None               # 手势对照表页面（懒创建）
        self._panel_anchor = QPointF(0, 0)  # 开面板时宠物的位置（跟随用）
        self._press_at: Optional[QPointF] = None
        self._press_target = ""             # "btn" / "body" / ""
        self._drag_moved = False

        self._setup_window()

        # ---- 运动状态 ----
        self._pos = QPointF(0, 0)          # 窗口左上角（当前）
        self._target = QPointF(0, 0)       # 想去的位置
        self._dragging = False
        self._drag_grab = QPoint()
        self._facing = 0                   # 0 正面 / +1 朝右 / -1 朝左
        self._facing_raw = 0
        self._facing_since = 0.0
        self._last_x = 0.0
        self._park_until = 0.0             # 用户手动拖过之后，暂时别自己跑
        self._last_hand = time.monotonic()  # 用"现在"初始化：刚启动不该立刻困成待机
        self._hand_pt: Optional[tuple] = None

        # ---- 吸附到窗口（follow == "window"） ----
        self._dock: Optional[windowinfo.WindowInfo] = None
        self._dock_side = "right"          # 宠物站在窗口的哪一侧（right/left）
        self._dock_seen = 0.0              # 最后一次确认目标窗口的时刻
        self._dock_next = 0.0              # 下次轮询的时刻（限流用）
        self._dock_poll = 0.12             # 轮询间隔：窗口被拖动时也能跟上

        # ---- 表现状态 ----
        self._pose = PetPose()
        self._look = (0.0, 0.0)
        self._aura = 0.0                   # 当前显示值
        self._aura_target = 0.0            # 目标值（由事件写入，逐帧靠拢）
        self._glow = 0.0
        self._glow_target = 0.0
        self._mood = "idle"
        self._mood_until = 0.0
        self._phase = 0.0                  # 单调时钟，特效（星星/Zzz）靠它走相位

        # ---- 手势驱动的持久状态（宠物互动线）----
        # 与 mood 的区别：mood 是"表情"，几秒后自己退回 idle；
        # 这几个是"状态"，必须另一个手势或一次点击才解除。
        # 混在一起的后果很直观：比个 ✊ 让它睡，两秒后它自己醒了——那就不叫睡觉。
        self._asleep = False               # ✊ 之后的硬锁
        self._napping = False              # 人不在，自动打盹（和 _asleep 是两回事）
        self._dance_until = 0.0            # ✌️ 跳舞的结束时刻（限时，不是永动）
        self._follow_backup = ""           # 切到跟鼠标前的模式，醒来时还原
        self._game_on = False              # 正在玩石头剪刀布（游戏时不回角落）

        # ---- 隔空撸宠 ----
        self._pet_since = 0.0              # 从什么时候开始"被摸"（0 = 当前没在摸）
        self._petting = False
        self._pet_said = False             # 这一轮摸够了有没有说过话

        # ---- 用户情绪（来自面部表情）----
        # 这是"用户的状态"，不是宠物的表情。宠物的反应由下面几个冷却控制，
        # 免得用户皱着眉头工作半小时，被关怀三十遍。
        self._user_mood = "neutral"
        self._care_until = 0.0             # 关怀台词的冷却
        self._cheer_until = 0.0            # 跟着高兴的冷却

        # ---- 气泡 ----
        self._bubble = ""
        self._bubble_t0 = 0.0
        self._bubble_alpha = 0.0

        self._font = ui_font(10)

        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)        # ~60FPS
        self._timer.timeout.connect(self._tick)

        self._subscribe()
        self._place_home(immediate=True)

    # ---------------------------------------------------------------- 窗口
    def _setup_window(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle(self.cfg.pet_name)
        self.resize(self.win_w, self.win_h)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # 鼠标跟踪：不按键也要收到 mouseMoveEvent，关闭键才能做悬停高亮
        self.setMouseTracking(True)

    def start(self) -> None:
        self.show()
        self._timer.start()
        if self.cfg.click_through:
            self.set_click_through(True)
        if sys.platform == "win32":
            # 有些环境（多显示器/远程桌面）窗口创建后会被重置，show 之后再顶一次
            self.raise_()

    def set_click_through(self, on: bool) -> None:
        """
        手动穿透开关（面板 / 托盘调用）。

        注意这里只写 cfg 不做判断：跟鼠标模式还有一个**自动**穿透，
        两者是"或"的关系 —— 手动开了就一定穿透，手动关着时由自动逻辑决定。
        """
        self.cfg.click_through = bool(on)
        self._apply_through()

    def _apply_through(self) -> None:
        """把"手动穿透 or 自动穿透"合并成窗口的真实状态。"""
        self._win_set_through(bool(self.cfg.click_through) or self._auto_through)

    def _win_set_through(self, on: bool) -> None:
        if sys.platform != "win32":
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if on:
                style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
            else:
                style &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception as e:
            log.warning("设置穿透失败：%s", e)

    def _screen_rect(self) -> QRectF:
        scr = QGuiApplication.primaryScreen()
        g = scr.availableGeometry() if scr else None
        if g is None:
            return QRectF(0, 0, 1920, 1080)
        return QRectF(g.x(), g.y(), g.width(), g.height())

    def _screen_rect_for(self, x: float, y: float) -> QRectF:
        """
        某个点落在哪块屏幕上，就返回那块屏幕的可用区域。

        多显示器时不能一律用 primaryScreen：目标窗口在副屏、宠物却按主屏
        去夹坐标，会被拽回主屏，等于吸附失败。
        """
        scr = QGuiApplication.screenAt(QPoint(int(x), int(y)))
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        g = scr.availableGeometry() if scr else None
        if g is None:
            return QRectF(0, 0, 1920, 1080)
        return QRectF(g.x(), g.y(), g.width(), g.height())

    def _place_home(self, immediate: bool = False) -> None:
        """回"家"：默认右下角，离任务栏留一点距离。"""
        r = self._screen_rect()
        pad = 24.0
        x = {"br": r.right() - self.win_w - pad, "bl": r.left() + pad,
             "tr": r.right() - self.win_w - pad, "tl": r.left() + pad
             }.get(self.cfg.home_corner, r.right() - self.win_w - pad)
        y = (r.bottom() - self.win_h - pad) if self.cfg.home_corner in ("br", "bl") \
            else (r.top() + pad)
        self._target = QPointF(x, y)
        if immediate:
            self._pos = QPointF(x, y)
            self.move(int(x), int(y))

    # ---------------------------------------------------------------- 事件订阅
    def _subscribe(self) -> None:
        self.bus.subscribe(HandsSeen, self._on_hands)
        self.bus.subscribe(ActionDone, self._on_action)
        self.bus.subscribe(ContinuousAction, self._on_continuous)
        self.bus.subscribe(ControlToggled, self._on_toggle)
        self.bus.subscribe(PetSay, self._on_say)
        self.bus.subscribe(PetGame, self._on_game)
        self.bus.subscribe(PresenceChanged, self._on_presence)
        self.bus.subscribe(EmotionChanged, self._on_emotion)
        self.bus.subscribe(PerceptionReady, self._on_ready)
        self.bus.subscribe(PerceptionFailed, self._on_failed)

    def _on_hands(self, ev: Event) -> None:
        assert isinstance(ev, HandsSeen)
        now = time.monotonic()
        if ev.count:
            # 手都举起来了，人肯定在——打盹立刻结束，不用等下一次人脸检测
            self._napping = False
        if ev.count and ev.point:
            self._last_hand = now
            self._hand_pt = ev.point
            # 睡着时不跟手：手在画面里晃来晃去不该把睡着的宠物拖着走。
            # 但仍然记录手的位置和时间，醒来时能立刻接上。
            if not self._asleep:
                self._follow_hand(ev.point)
            self._update_petting(now, ev)
            # 手在画面里移动 -> 视线跟着手，是最便宜也最有效的"活着"信号
            self._look = (_clamp((ev.point[0] - 0.5) * 2.2, -1.0, 1.0),
                          _clamp((ev.point[1] - 0.5) * 2.0, -1.0, 1.0))
        else:
            self._hand_pt = None
            self._look = (0.0, 0.0)
            self._update_petting(now, None)

        # 悬停进度环：这 0.5 秒的等待画在宠物身上，比画在屏幕角落直观得多
        self._aura_target = float(ev.progress) if ev.count else 0.0
        self._glow_target = 1.0 if ev.count else 0.0
        if ev.count and self._mood in ("idle", "sleepy"):
            self._set_mood("listen", hold=0.8)

    def _on_action(self, ev: Event) -> None:
        assert isinstance(ev, ActionDone)
        if ev.bubble:
            self.say(ev.bubble, ev.mood if ev.ok else "surprised")
        # 说完话抖一下：动作反馈要"有重量"，不能只是文字变一下
        self._pose.squash = 0.22 if ev.ok else -0.20
        self._pose.bob = 0.10
        if not ev.ok and ev.detail:
            log.warning("动作 %s 失败：%s", ev.label, ev.detail)

        if ev.ok:
            self._apply_pet_intent(ev.intent)
            self._bond_add("gesture")

    def _apply_pet_intent(self, key: str) -> None:
        """
        宠物互动手势的**状态迁移**。

        为什么单独抽一层而不是在 _on_action 里堆 if：
            这几个动作里三个是"改变状态"（睡 / 醒 / 换跟随方式），
            只有一个（跳舞）是"放一段动画"。混着写的话，以后每加一个
            状态手势都要动一遍原有分支，"睡醒"和"跟随"的先后顺序很容易搞乱。
        """
        now = time.monotonic()

        if key == "pet:sleep":
            self._asleep = True
            self._dance_until = 0.0
            self._set_mood("sleepy", hold=1e9)   # 由状态负责解除，不靠超时
        elif key == "pet:wake":
            self._asleep = False
            self._set_mood("happy", hold=1.6)
            if self._follow_backup:
                # 醒来时还原睡前那套跟随方式，别把用户的选择弄丢
                self.cfg.follow = self._follow_backup
                self._follow_backup = ""
        elif key == "pet:follow":
            if self.cfg.follow == "cursor":
                # 再比一次 ☝️ = 退出跟随，回到原来的方式
                self.cfg.follow = self._follow_backup or "hand"
                self._follow_backup = ""
            else:
                self._follow_backup = self.cfg.follow
                self.cfg.follow = "cursor"
        elif key == "pet:dance":
            self._asleep = False
            self._dance_until = now + 3.5
            # 立刻切表情，不等下一帧：_tick 里那条只是"续命"，防止
            # say() 的 1.6 秒超时把后两秒掐成"站着抖"
            self._set_mood("dance", 3.5)

    def _on_continuous(self, ev: Event) -> None:
        assert isinstance(ev, ContinuousAction)
        self._pose.squash = 0.10
        self._set_mood("surprised" if ev.intent == "zoom" else "calm", hold=0.3)

    def _on_toggle(self, ev: Event) -> None:
        assert isinstance(ev, ControlToggled)
        self._enabled = ev.enabled
        if ev.enabled:
            self.say("我回来啦～", "happy")
            self._set_mood("happy", hold=1.2)
        else:
            self.say("手势识别关啦，点右上角 ▶ 再开", "sleepy")
            self._set_mood("sleepy", hold=999)
        self._aura = 0.0
        if self._panel is not None and self._panel.isVisible():
            self._panel.update()

    def _on_say(self, ev: Event) -> None:
        assert isinstance(ev, PetSay)
        self.say(ev.text, ev.mood)

    def _on_game(self, ev: Event) -> None:
        """
        石头剪刀布的**显示**。这里一个 if 都不涉及规则。

        谁赢谁输、几比几，都是感知线程算好传过来的。分开的好处很实际：
        想改成五局三胜只动 `rps.py`，想改说法只动这里，两边互不牵扯。
        """
        assert isinstance(ev, PetGame)
        self._game_on = bool(ev.active)

        if not ev.active:
            # 刚退出游戏：把"倒数/惊讶"这类临时表情收回来
            if self._mood in ("listen", "surprised"):
                self._set_mood("happy", 1.2)
            return

        if ev.phase == "countdown":
            # 倒数用短气泡：一秒一个数字，长句子会被气泡宽度切掉
            self.say(str(ev.count) if ev.count else "…", "listen")
        elif ev.phase == "armed":
            self.say("出！", "surprised")
        elif ev.phase == "result" and ev.result >= 0:
            score = f"{ev.score[0]}:{ev.score[1]}"
            if ev.result == 0:
                self.say(f"{ev.player} vs {ev.pet}，你赢啦　{score}", "shy")
            elif ev.result == 1:
                self.say(f"{ev.player} vs {ev.pet}，我赢咯　{score}", "proud")
            else:
                self.say(f"都是{ev.player}，平局　{score}", "calm")
            self._bond_add("game")

    def _on_presence(self, ev: Event) -> None:
        """
        人在 / 人走。注意和 `_asleep` 是**两件不同的事**：

        _asleep 是用户比 ✊ 让它睡，必须 ✋ 或点一下才醒，是"听你的"；
        _napping 是"人不在所以打盹"，人一回来自己就醒，是"懂事"。

        合成一个状态会出问题：用户让它睡下、走开、再回来，宠物会自己醒，
        和"它听我的"这个预期正好相反。
        """
        assert isinstance(ev, PresenceChanged)
        if ev.present:
            if self._napping:
                self._napping = False
                self.say("你回来啦！", "happy")
            return
        if not self._asleep:
            self._napping = True
            self._set_mood("sleepy", 1e9)

    def _on_emotion(self, ev: Event) -> None:
        """
        用户情绪的**反应策略**。

        三种反应刻意做得不一样：
            累  -> 什么都不说，只是把动作变轻（安静陪伴）。累了的人不想被搭话。
            皱眉-> 说一句关心的，但有 90 秒冷却，不当复读机。
            笑  -> 跟着高兴一下，同样有冷却。

        阈值和情绪本身在 `pet/perception/emotion.py`，这里只管"怎么表现"。
        """
        assert isinstance(ev, EmotionChanged)
        self._user_mood = ev.emotion
        now = time.monotonic()

        if ev.emotion == "tired":
            # 安静陪伴靠的是"它变轻了"，不是"它说我在陪你"
            self._set_mood("calm", 1e9)
        elif ev.emotion == "frown" and now > self._care_until:
            self._care_until = now + 90.0
            self.say("要不要歇会儿？", "shy")
        elif ev.emotion == "happy" and now > self._cheer_until:
            self._cheer_until = now + 45.0
            self.say("看你笑我也想笑～", "happy")

    def _on_ready(self, ev: Event) -> None:
        assert isinstance(ev, PerceptionReady)
        # 熟了就换个招呼：这是"记得你"最便宜也最有效的表达
        greet = self.bond.bond.greeting if self.bond is not None else ""
        if greet and greet != "……":
            self.say(f"{greet} 摄像头好啦～", "happy")
        else:
            self.say("摄像头好啦，把手抬起来试试～", "happy")

    def _on_failed(self, ev: Event) -> None:
        assert isinstance(ev, PerceptionFailed)
        log.info("%s", ev.message)
        self.say(ev.message, "surprised")

    # ---------------------------------------------------------------- 对外动作
    def say(self, text: str, mood: str = "happy") -> None:
        self._bubble = text
        self._bubble_t0 = time.monotonic()
        self._set_mood(mood, hold=1.6)

    def _set_mood(self, mood: str, hold: float = 1.5) -> None:
        self._mood = mood
        self._mood_until = time.monotonic() + hold

    def pet_me(self) -> None:
        """被点/被摸：这是唯一不需要手势也能建立的"关系"。"""
        self.say("嘿嘿…再摸一下", "shy")
        self._pose.squash = -0.25
        self._bond_add("click")

    def _bond_add(self, kind: str) -> None:
        """
        记一次互动。跨过等级门槛时庆祝一下。

        故意把 bond 做成可空的：单元测试和 headless 模式不该为了加个分
        去准备一个可写目录。没有存档就当作"不记账"，功能照常。
        """
        if self.bond is None:
            return
        try:
            leveled_up = self.bond.add(kind)
            self._bond_dirty = True
            if leveled_up:
                self.say(f"我们更熟了——{self.bond.bond.level_name}", "proud")
        except Exception:
            pass                        # 记账失败不该影响正在进行的互动

    # ---------------------------------------------------------------- 跟随
    def _follow_hand(self, pt: tuple) -> None:
        """
        手掌位置 -> 屏幕位置。

        gain > 1 是刻意的：摄像头视野里挪 10cm，屏幕上只走 10% 宽度会显得很迟钝；
        放大 1.6 倍之后，小幅移动就能让宠物跑到屏幕另一头，"隔空驱赶"的感觉才出来。
        """
        if self.cfg.follow != "hand":
            return
        now = time.monotonic()
        if now < self._park_until:      # 刚被用户拖过，别抢
            return
        r = self._screen_rect()
        gain = self.cfg.follow_gain
        gx = 0.5 + (pt[0] - 0.5) * gain
        gy = 0.5 + (pt[1] - 0.5) * gain
        x = r.left() + _clamp(gx, 0.02, 0.98) * r.width() - self.win_w / 2
        y = r.top() + _clamp(gy, 0.08, 0.94) * r.height() - self.win_h * 0.55
        x = _clamp(x, r.left(), r.right() - self.win_w)
        y = _clamp(y, r.top(), r.bottom() - self.win_h)
        self._target = QPointF(x, y)

    def _update_petting(self, now: float, ev) -> None:
        """
        隔空撸宠：判断"手是不是在摸它"。

        为什么不用"手掌坐标离宠物多远"当判据：
            宠物本来就跟着手走，手到哪它到哪——距离永远很近，这个条件恒真，
            等于没判据。真正有信息量的是**两件事同时成立**：
              1. 宠物已经追上来了（_pos 贴近 _target）→ 说明手停住了；
              2. 手还在缓慢移动（速度低于 pet_speed）→ 说明不是在发呆。
            手停住、宠物贴上来、手又轻轻动，这三件事凑齐，
            主观感受就是"在它身上摸"。
        """
        if ev is None or not ev.count:
            self._pet_since = 0.0
            self._petting = False
            self._pet_said = False
            return

        settled = (self._pos - self._target).manhattanLength() < 40.0
        gentle = ev.speed < self.cfg.pet_speed

        if self._asleep or not (settled and gentle):
            self._pet_since = 0.0
            self._petting = False
            self._pet_said = False
            return

        if self._pet_since <= 0.0:
            self._pet_since = now

        if not self._petting and now - self._pet_since > self.cfg.pet_delay:
            self._petting = True
            self._pose.squash = 0.14          # 被摸到的那一下"弹"一下
            self.say("唔…", "shy")

        # 摸够久主动说一句：一直干摸没有任何反馈，会让人以为功能没生效
        if self._petting and not self._pet_said and now - self._pet_since > 3.5:
            self._pet_said = True
            self.say("嘿嘿…好舒服", "shy")
            # 摸着摸着加分（权重比手势高：这是"花时间陪着"，不是"下指令"）
            self._bond_add("pet")

    def _cursor_on_pet(self, c: Optional[QPoint] = None) -> bool:
        """指针是不是正压在宠物身上（身体矩形 + 一圈余量）。"""
        c = QCursor.pos() if c is None else c
        local = self.mapFromGlobal(c)
        if not self.rect().contains(local):
            return False
        pad = 12.0
        return (abs(local.x() - self.pet_cx) <= self.body_w / 2 + pad
                and abs(local.y() - self.pet_cy) <= self.body_h / 2 + pad)

    def _follow_cursor(self, dt: float) -> None:
        """
        鼠标尾随 —— 宠物被一根绳子拴在指针后面，而不是"坐"在指针上。

        老做法是把窗口中心对准指针，结果一个 222x302 的窗口整个压在鼠标底下：
        点什么都点不到，桌宠自己也被指针盖着点不着（用户报的 bug）。

        现在的规则：
            * 指针和身体中心距离超过 trail，才追过去，追到 trail 就停 ——
              所以宠物总是"跟在后面"，不会盖住指针。
            * 指针已经压在宠物身上时**一步也不动**，人可以稳稳地点它。
            * 指针不在身上时窗口整体鼠标穿透（见 _update_cursor_through），
              所以宠物窗口再大也挡不了别人家的点击。
        """
        if self.cfg.follow != "cursor":
            return
        if self._dragging or time.monotonic() < self._park_until:
            return
        # 面板开着的时候绝对不许动：面板是相对宠物定位的，宠物一挪面板就跑，
        # 用户伸鼠标去点，反而永远点不中（这也是"点不到设置窗口"的一部分原因）。
        if self._ui_open():
            return

        c = QCursor.pos()
        if self._cursor_on_pet(c):
            return                       # 手指已经搭上来了，别动

        trail = max(40.0, self.cfg.cursor_trail)
        cx = self._target.x() + self.pet_cx
        cy = self._target.y() + self.pet_cy
        dx, dy = cx - c.x(), cy - c.y()
        dist = math.hypot(dx, dy)
        if dist <= trail:
            return                       # 在绳子半径内：原地待着，等它自己靠近
        if dist < 1e-3:
            dx, dy, dist = 0.5, 0.87, 1.0
        k = trail / dist
        x = c.x() + dx * k - self.pet_cx
        y = c.y() + dy * k - self.pet_cy
        r = self._screen_rect()
        self._target = QPointF(_clamp(x, r.left(), r.right() - self.win_w),
                               _clamp(y, r.top(), r.bottom() - self.win_h))

    def _update_cursor_through(self, dt: float) -> None:
        """
        跟鼠标模式下自动穿透。

        判据是"指针在不在宠物身体上"，不是"在不在窗口矩形内" ——
        窗口上方一大片是透明气泡区，用户根本看不见，却会吃点击。
        指针停在身体上超过 cursor_click_delay 才解除穿透：划过一下不算，
        停住就是"我要点它"。
        """
        if self.cfg.follow != "cursor":
            if self._auto_through:
                self._auto_through = False
                self._hover_dwell = 0.0
                self._apply_through()
            return

        if self._cursor_on_pet():
            self._hover_dwell += dt
            if (self._hover_dwell >= self.cfg.cursor_click_delay
                    and self._auto_through):
                self._auto_through = False          # 让人点它
                self._apply_through()
        else:
            self._hover_dwell = 0.0
            if not self._auto_through:
                self._auto_through = True           # 别挡别人的点击
                self._apply_through()

    # ---------------------------------------------------------------- 吸附窗口
    def _follow_window(self, now: float) -> None:
        """
        贴着"手势要操作的那个窗口"站着。

        取数口径和 `actions.target_window()` 完全一致（同一个前台窗口、同样排除
        桌面/任务栏/自己进程），所以宠物贴着谁，手势就动谁 —— 这正是这个功能
        要解决的问题：不用事后猜刚才那一下动了哪个窗口。

        三个"别乱动"的时刻
            * 用户刚把宠物拖走（_park_until）：尊重"我就要放这儿"。
            * 正在拖 / 面板开着：手在宠物身上，脚下换目标会让人点错。
            * 前台暂时变成桌宠自己：读不到目标时先撑 1.5 秒，别一丢就跳回角落。
        """
        if now < self._park_until or self._dragging:
            return
        if self._ui_open():
            return
        if now < self._dock_next:
            return
        self._dock_next = now + self._dock_poll

        scr = QGuiApplication.primaryScreen()
        info = windowinfo.query(self_hwnd=int(self.winId()),
                                dpr=scr.devicePixelRatio() if scr else 1.0)

        if info is None:
            if self._dock is not None and now - self._dock_seen < 1.5:
                return                      # 只是短暂读不到，原地待着
            if self._dock is not None:
                self._dock = None
                self._place_home()
            return

        self._dock = info
        self._dock_seen = now

        if not info.dockable:
            # 最小化/最大化/全屏：没有边可贴，守在角落，等窗口恢复再贴回去
            self._place_home()
            return

        area = self._screen_rect_for(*info.center)
        x, y, side = windowinfo.dock_xy(
            info, self.win_w, self.win_h, self.pet_cx, self.pet_cy,
            (area.left(), area.top(), area.right(), area.bottom()),
            body_w=self.body_w, overlap=self.cfg.dock_overlap,
            prefer=self.cfg.dock_side,
            pad=self.cfg.dock_pad, y_ratio=self.cfg.dock_y)
        self._dock_side = side
        self._target = QPointF(x, y)

    # ---------------------------------------------------------------- 每帧
    def _tick(self) -> None:
        now = time.monotonic()
        dt = _clamp(now - self._last_tick, 0.001, 0.1)
        self._last_tick = now
        self._phase = now

        # 亲密度定期落盘：30 秒一次。每次互动都写磁盘太重，
        # 只在内存里累加、退出时再补一次，掉电最多丢半分钟。
        if self._bond_dirty and now - self._bond_saved > 30.0:
            self._bond_saved = now
            self._bond_dirty = False
            if self.bond is not None:
                self.bond.save()

        # 1) 一段时间没看到手 -> 回角落待机。
        #    "没人的时候别赖在屏幕中间"是桌宠的基本礼貌。
        #    吸附/跟鼠标两种模式下不回家：一个要守在操作目标旁边，
        #    一个位置本来就由指针决定，抢位置只会让两边打架。
        #
        #    ✊ 睡着的优先级高于以上全部：它睡在哪儿就是哪儿。
        #    不特判的话，"没手"逻辑会在两秒内把它叫醒并赶回角落——
        #    用户明明刚让它睡下，看着就像手势没生效。
        quiet = now - self._last_hand
        if self._asleep:
            if self._mood != "sleepy":
                self._set_mood("sleepy", 1e9)
        elif self._napping:
            # 人不在：打盹，并且飘回角落——等他回来一眼就看见它在睡觉
            if self._mood != "sleepy":
                self._set_mood("sleepy", 1e9)
            if (self.cfg.follow not in ("window", "cursor") and not self._dragging
                    and not self._ui_open() and now > self._park_until):
                self._place_home(immediate=False)
        elif quiet > self.cfg.idle_after and not self._game_on:
            if self._mood not in ("sleepy", "shy"):
                self._set_mood("sleepy" if quiet > self.cfg.idle_after * 2 else "idle", 0.5)
            if (self.cfg.follow not in ("window", "cursor") and not self._dragging
                    and not self._ui_open() and now > self._park_until):
                self._place_home(immediate=False)

        # 睡着时不跟手也不跟鼠标：睡眠的语义就是"别动"
        if not self._asleep:
            if self.cfg.follow == "window":
                self._follow_window(now)
            self._follow_cursor(dt)
        self._update_cursor_through(dt)

        # 2) 平滑移动：指数趋近，和帧率无关（掉帧只会变慢，不会瞬移）
        if not self._dragging:
            k = _clamp(1.0 - pow(0.0001, dt * 1.5), 0.02, 0.35)
            self._pos += (self._target - self._pos) * k
            if (self._pos - self._target).manhattanLength() > 0.5:
                self.move(int(self._pos.x()), int(self._pos.y()))

        # 2b) 朝向：横移够快才切侧面，并且要持续 0.25 秒。
        #     不加迟滞的话，跟随手掌时左右微抖会让立绘疯狂翻面。
        vx = (self._pos.x() - self._last_x) / max(dt, 1e-3)
        self._last_x = self._pos.x()
        raw = 1 if vx > 70 else (-1 if vx < -70 else 0)
        if raw != self._facing_raw:
            self._facing_raw = raw
            self._facing_since = now
        if self.cfg.sprite_walk and now - self._facing_since > 0.25:
            self._facing = raw

        # 2c) 吸附时正面朝前。
        #     老版本让它"回头看窗口"（侧身），本意是暗示在看守谁；实际看下来
        #     侧脸配一个贴边的小身板很像"被门夹住了"，而且吸附时人本来就知道
        #     在操作哪个窗口（头顶还有标题标签）。所以这里统一回正面。
        if self.cfg.follow == "window" and self.cfg.dock_face_front:
            self._facing = 0

        # 3) 情绪超时后退回 idle。
        #    跳舞是例外：它由 _dance_until 统管，不能被 say() 那 1.6 秒的
        #    mood 超时提前掐断，否则后两秒会变成"站着抖"。
        if now < self._dance_until and not self._asleep:
            self._set_mood("dance", self._dance_until - now)
        elif self._petting and not self._asleep:
            # 被摸时表情锁在 shy。不每帧续上的话，shy 那 0.6 秒一过
            # 就被下面的超时逻辑拉回 idle——摸到一半突然不脸红了，很出戏。
            self._set_mood("shy", 0.6)
        elif self._user_mood == "tired" and not (self._asleep or self._napping):
            # 安静陪伴：他累了就安静待着，别用活泼的表情去打扰
            self._set_mood("calm", 1e9)
        elif now > self._mood_until and self._mood not in ("idle", "sleepy"):
            self._set_mood("idle", 0.5)

        # 3b) 面板跟着宠物走：跟手模式下宠物会一直移动，面板要是钉在原地，
        #     几秒后就和宠物隔了半个屏幕，看着像两个不相关的东西。
        if self._panel is not None and self._panel.isVisible():
            if (self._pos - self._panel_anchor).manhattanLength() > 40:
                self._panel.popup_near(self)
                self._panel_anchor = QPointF(self._pos)

        # 4) 呼吸与晃动：一直存在的微小运动，静止时也不像一张贴图
        target = PetPose.for_mood(self._mood)
        target.squash += 0.030 * math.sin(now * 2.1)
        target.bob += 0.012 * math.sin(now * 1.7 + 0.6)

        # 4b) 跳舞：在时间轴上叠一层高频摆动。
        #     姿态表只给"基调"——静态的 dance 姿态看着只是"摆了个造型"，
        #     必须有相位变化才叫跳舞。最后 0.6 秒幅度收敛，避免戛然而止。
        if now < self._dance_until:
            amp = min(1.0, (self._dance_until - now) / 0.6)
            target.tilt += 8.0 * amp * math.sin(now * 9.0)
            target.bob += 0.06 * amp * abs(math.sin(now * 5.2))
            target.squash += 0.07 * amp * math.sin(now * 6.4)

        # 4c) 被摸：尾摆加快。比"说句话"更让人上瘾的是"它真的在动"，
        #     所以撸宠的反馈主要落在尾巴上，台词只做点缀。
        if self._petting:
            target.wag += 0.50 * math.sin(now * 7.5)

        # 4d) 用户累了：所有微动幅度压到四成。
        #     "安静陪伴"落到像素上就是——存在感变低，但不是消失。
        #     直接停掉呼吸会像一张贴图卡住了，那比不陪伴更糟。
        if self._user_mood == "tired":
            target.bob *= 0.4
            target.squash *= 0.4
            target.wag *= 0.4

        # 5) 进度环 / 柔光：目标值来自事件，这里追上去，避免一帧闪
        self._aura += (self._aura_target - self._aura) * _clamp(dt * 10, 0, 1)
        self._glow += (self._glow_target - self._glow) * _clamp(dt * 8, 0, 1)
        target.aura = self._aura
        target.glow = self._glow
        target.look = self._look

        # 6) 姿态插值；挤压是脉冲量，按指数自然回弹
        self._pose.lerp_to(target, _clamp(dt * 9, 0.05, 0.6))
        self._pose.squash *= pow(0.02, dt)
        # 尾巴/长发的摆动直接给正弦。不走插值：插值会把振幅压扁，
        # 摆起来就只剩"微微动一下"，反而更像静止图片。
        self._pose.wag = math.sin(now * 1.7) * 0.55 + math.sin(now * 0.9) * 0.25

        # 7) 气泡淡入/保持/淡出
        age = now - self._bubble_t0
        hold = self.cfg.bubble_ms / 1000.0
        if age < 0.16:
            self._bubble_alpha = age / 0.16
        elif age < hold:
            self._bubble_alpha = 1.0
        elif age < hold + 0.35:
            self._bubble_alpha = max(0.0, 1.0 - (age - hold) / 0.35)
        else:
            self._bubble_alpha = 0.0
            self._bubble = ""

        self.update()

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # 吸附时先把"窗口边框"这条线画在下面：立绘会挡住它，
        # 看上去是边界从宠物身后延伸出来，而不是一道线横在脸上。
        if (self.cfg.follow == "window" and self._dock is not None
                and self._dock.dockable):
            self._paint_dock_seam(p)

        if self._sprite is not None:
            # 贴图版没有独立的影子和柔光层，这里补上，构图才和矢量版一致
            draw_shadow(p, self.pet_cx, self.pet_cy, self.body_w, self.body_h)
            draw_glow(p, self.pet_cx, self.pet_cy, self.body_w, self.body_h,
                      self._glow)
            self._sprite.draw(p, self.pet_cx, self.pet_cy, self.body_w,
                              self.body_h, self._pose, self._facing)
            # 贴图的脸换不了，情绪靠特效补（星星 / 感叹号 / Zzz）
            draw_mood_fx(p, self.pet_cx, self.pet_cy, self.body_w, self.body_h,
                         self._mood, self._phase)
            draw_aura(p, self.pet_cx, self.pet_cy, self.body_w, self.body_h,
                      self._aura)
        else:
            draw_pet(p, self.pet_cx, self.pet_cy, self.body_w, self.body_h,
                     self._pose)

        if self._bubble_alpha > 0.01 and self._bubble:
            # 气泡贴在头顶水花上方，尾巴刚好戳到脑袋
            top = max(2.0, self.pet_cy - self.body_h * 0.50 - 78)
            rect = QRectF(8, top, self.win_w - 16, 66)
            draw_bubble(p, rect, self._bubble, self._bubble_alpha, self._font)
        elif (self.cfg.follow == "window" and self._dock is not None
              and self._dock.dockable):
            # 说话的时候优先让气泡，标签等说完再回来
            self._paint_dock_tag(p)

        # 关闭状态下画一条淡淡的提示，免得用户以为程序挂了。
        # 两种"不在干活"要区分开：手势被关掉（要告诉用户怎么开回来）和
        # 只是暂时没看到手（待机中）。
        if not self._enabled:
            p.setPen(QColor(150, 160, 175, 190))
            p.setFont(ui_font(8))
            p.drawText(QRectF(0, self.win_h - 18, self.win_w, 16),
                       int(Qt.AlignmentFlag.AlignHCenter), "手势识别已关闭")
        elif self._mood == "sleepy":
            p.setPen(QColor(120, 140, 150, 150))
            p.setFont(ui_font(8))
            p.drawText(QRectF(0, self.win_h - 18, self.win_w, 16),
                       int(Qt.AlignmentFlag.AlignHCenter), "待机中")

        # 右上角的关闭键要画在最上层：气泡/水花都不该盖住它
        self._paint_power_button(p)
        p.end()

    def _paint_dock_tag(self, p: QPainter) -> None:
        """
        吸附时头顶的小标签：窗口标题。

        为什么必须有标签：吸附只说明"我贴着某个东西"，说明不了贴的是谁。
        窗口标题一摆出来，"现在手势动的是这个窗口"就不用猜了 —— 这正是用户
        提这个需求的理由。
        """
        info = self._dock
        if info is None:
            return
        accent = QColor(77, 107, 254)

        # 标签：夹在左边和右上角关闭键之间，宽度不够就不画（宁缺勿糊）
        x0 = 7.0
        x1 = self.win_w - self._btn_r * 2 - 12
        if x1 - x0 < 44:
            return
        rect = QRectF(x0, 4.0, x1 - x0, 18.0)
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 95), 1.0))
        p.setBrush(QColor(255, 255, 255, 210))
        p.drawRoundedRect(rect, 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 235))
        p.drawEllipse(QPointF(x0 + 9.0, rect.center().y()), 3.0, 3.0)
        p.setPen(QColor(46, 60, 92))
        p.setFont(ui_font(8))
        p.drawText(rect.adjusted(16, 0, -6, 0),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   windowinfo.short_title(info.title, self.cfg.dock_title_len))

    def _paint_dock_seam(self, p: QPainter) -> None:
        """
        一条沿着窗口边框的细线，**画在立绘之前**，所以会被身体挡住。

        这样看上去是"窗口的边一直延伸到宠物身后"，而不是一道划痕从脸上划过。
        只画在宠物这一侧，长度略超身体上下沿，断开的两头刚好从身侧露出来。
        """
        if not self.cfg.dock_side:
            return
        accent = QColor(77, 107, 254)
        # 边线的本地 x：身体往窗口外让了多少，边线就往里退多少
        reach = self.body_w * (0.5 - _clamp(self.cfg.dock_overlap, 0.0, 0.5))
        seam_x = (self.pet_cx - self.cfg.dock_pad - reach if self._dock_side == "right"
                  else self.pet_cx + self.cfg.dock_pad + reach)
        h = self.body_h * 1.36
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 115))
        p.drawRoundedRect(QRectF(seam_x - 1.3, self.pet_cy - h / 2, 2.6, h), 1.3, 1.3)

    def _paint_power_button(self, p: QPainter) -> None:
        """
        右上角那颗按钮：运行中是 ×（点一下关手势识别），已关闭是 ▶（点一下开）。

        为什么不直接做成"退出程序"：闭摄像头和退程序是两件事。用户想暂时
        不看摄像头时不该被迫连桌宠一起关掉；真要退出，控制面板和托盘里都有。
        """
        c, r = self._btn_c, self._btn_r
        hover = self._hover_btn
        p.setPen(Qt.PenStyle.NoPen)
        if self._enabled:
            p.setBrush(QColor(30, 38, 58, 205 if hover else 135))
        else:
            p.setBrush(QColor(58, 168, 120, 235 if hover else 190))
        p.drawEllipse(c, r, r)

        if hover:   # 悬停加一圈淡光：让"这里能点"一眼看出来
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 80), 2.0))
            p.drawEllipse(c, r + 1.2, r + 1.2)

        if self._enabled:
            p.setPen(QPen(QColor(255, 255, 255, 245 if hover else 200),
                          1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            d = r * 0.42
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        else:
            d = r * 0.40
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawPolygon(QPolygonF([
                QPointF(c.x() - d * 0.62, c.y() - d),
                QPointF(c.x() - d * 0.62, c.y() + d),
                QPointF(c.x() + d, c.y()),
            ]))

    # ---------------------------------------------------------------- 命中区
    def _in_button(self, pos) -> bool:
        c = self._btn_c
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= self._btn_r + 3

    def _in_body(self, pos) -> bool:
        """
        只有"宠物身上"才算点击/拖拽。

        窗口是 1.95 倍身宽的透明方框，如果整块都响应鼠标，用户点它旁边的
        桌面空白也会被吞掉（还会顺手把宠物拖走）——那种"点哪儿都被吃"的
        手感最劝退。
        """
        dx = (pos.x() - self.pet_cx) / max(self.body_w * 0.58, 1.0)
        dy = (pos.y() - self.pet_cy) / max(self.body_h * 0.58, 1.0)
        return dx * dx + dy * dy <= 1.0

    # ---------------------------------------------------------------- 控制面板
    def open_panel(self) -> None:
        """点桌宠 -> 弹出开关面板（延迟导入：不开面板就不加载这套代码）。"""
        from .panel import ControlPanel
        if self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.popup_near(self)
        self._panel_anchor = QPointF(self._pos)

    def open_bindings(self) -> None:
        """打开"手势对照表"页面（能看也能改）。已经开着就提到前面。"""
        from .bindings import BindingsDialog
        if self._bindings is None:
            self._bindings = BindingsDialog(self)
        self._bindings.show()
        self._bindings.raise_()
        self._bindings.activateWindow()

    def _ui_open(self) -> bool:
        """
        是不是有自绘界面（控制面板 / 对照表）正开着。

        开着的时候一切跟随都要停：面板相对宠物定位、对照表是模态工作流，
        宠物在用户手底下乱跑，点哪个都点不中。
        """
        for w in (self._panel, self._bindings):
            if w is not None and w.isVisible():
                return True
        return False

    def _close_panel(self) -> None:
        if self._panel is not None and self._panel.isVisible():
            self._panel.close()

    def _on_power_click(self) -> None:
        if self.control is not None:
            self.control.toggle()

    # ---------------------------------------------------------------- 鼠标
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.accept()
            return
        pos = ev.position()
        self._press_at = pos
        self._drag_moved = False

        if self._in_button(pos):
            self._press_target = "btn"
        elif self._in_body(pos):
            self._press_target = "body"
            self._dragging = True
            self._drag_grab = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
            self._close_panel()
        else:
            self._press_target = ""
            self._close_panel()      # 点透明空白 = 收面板
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        pos = ev.position()

        # 区分"点一下"和"拖着走"：位移超过 5px 才算拖。
        # 不区分的话，点桌宠开面板会顺带把它挪几像素，手感很差。
        if self._press_at is not None and not self._drag_moved:
            if (pos - self._press_at).manhattanLength() > 5:
                self._drag_moved = True

        if self._dragging and self._drag_moved:
            p = ev.globalPosition().toPoint() - self._drag_grab
            self.move(p)
            self._pos = QPointF(p)
            self._target = QPointF(p)
        elif self._press_at is None:
            over = self._in_button(pos)
            if over != self._hover_btn:
                self._hover_btn = over
                self.setToolTip("")
                if over:
                    self.setToolTip("关闭手势识别" if self._enabled else "开启手势识别")
                self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.accept()
            return
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        target, moved = self._press_target, self._drag_moved
        self._press_at = None
        self._press_target = ""

        if not moved:
            if target == "btn":
                self._on_power_click()
            elif target == "body":
                if self._asleep:
                    # 睡着时点一下 = 摸醒。
                    # 摄像头关掉之后这是唯一的唤醒途径，否则它就一直睡着，
                    # 只能去托盘退出重开——那也太笨了。
                    self._asleep = False
                    self.say("唔…我醒了", "happy")
                else:
                    self.pet_me()
                    self.open_panel()
        else:
            # 用户刚把它摆好，10 秒内别自己跑走（否则"我放这儿"的意图被无视）
            self._park_until = time.monotonic() + 10.0
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self.pet_me()
        ev.accept()
