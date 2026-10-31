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
import math
import sys
import time
from typing import Optional

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QCursor, QColor, QFont, QGuiApplication, QPainter
from PyQt6.QtWidgets import QWidget

from ..core.bus import EventBus
from ..core.config import Config
from ..core.events import (ActionDone, ContinuousAction, ControlToggled, Event,
                           HandsSeen, PerceptionFailed, PerceptionReady, PetSay)
from .paint import (PetPose, draw_aura, draw_bubble, draw_glow,
                    draw_mood_fx, draw_pet, draw_shadow, ui_font)
from .sprite import SpriteSet

# ---- Win32 常量（只在 Windows 上有意义） ----
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


class PetWindow(QWidget):
    def __init__(self, bus: EventBus, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.bus = bus
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

    def start(self) -> None:
        self.show()
        self._timer.start()
        if self.cfg.click_through:
            self.set_click_through(True)
        if sys.platform == "win32":
            # 有些环境（多显示器/远程桌面）窗口创建后会被重置，show 之后再顶一次
            self.raise_()

    def set_click_through(self, on: bool) -> None:
        """全屏鼠标穿透。开着的时候宠物不可点、也不挡你点别的东西。"""
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
            print(f"[pet] 设置穿透失败：{e}")

    def _screen_rect(self) -> QRectF:
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
        self.bus.subscribe(PerceptionReady, self._on_ready)
        self.bus.subscribe(PerceptionFailed, self._on_failed)

    def _on_hands(self, ev: Event) -> None:
        assert isinstance(ev, HandsSeen)
        now = time.monotonic()
        if ev.count and ev.point:
            self._last_hand = now
            self._hand_pt = ev.point
            self._follow_hand(ev.point)
            # 手在画面里移动 -> 视线跟着手，是最便宜也最有效的"活着"信号
            self._look = (_clamp((ev.point[0] - 0.5) * 2.2, -1.0, 1.0),
                          _clamp((ev.point[1] - 0.5) * 2.0, -1.0, 1.0))
        else:
            self._hand_pt = None
            self._look = (0.0, 0.0)

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
            print(f"[action] {ev.label} 失败：{ev.detail}")

    def _on_continuous(self, ev: Event) -> None:
        assert isinstance(ev, ContinuousAction)
        self._pose.squash = 0.10
        self._set_mood("surprised" if ev.intent == "zoom" else "calm", hold=0.3)

    def _on_toggle(self, ev: Event) -> None:
        assert isinstance(ev, ControlToggled)
        if ev.enabled:
            self.say("我回来啦～", "happy")
            self._set_mood("happy", hold=1.2)
        else:
            self.say("先歇一会儿…", "sleepy")
            self._set_mood("sleepy", hold=999)
        self._aura = 0.0

    def _on_say(self, ev: Event) -> None:
        assert isinstance(ev, PetSay)
        self.say(ev.text, ev.mood)

    def _on_ready(self, ev: Event) -> None:
        assert isinstance(ev, PerceptionReady)
        self.say("摄像头好啦，把手抬起来试试～", "happy")

    def _on_failed(self, ev: Event) -> None:
        assert isinstance(ev, PerceptionFailed)
        print(f"[pet] {ev.message}")
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

    def _follow_cursor(self) -> None:
        if self.cfg.follow != "cursor":
            return
        if time.monotonic() < self._park_until:
            return
        r = self._screen_rect()
        c = QCursor.pos()
        x = _clamp(c.x() - self.win_w / 2, r.left(), r.right() - self.win_w)
        y = _clamp(c.y() - self.win_h * 0.85, r.top(), r.bottom() - self.win_h)
        self._target = QPointF(x, y)

    # ---------------------------------------------------------------- 每帧
    def _tick(self) -> None:
        now = time.monotonic()
        dt = _clamp(now - self._last_tick, 0.001, 0.1)
        self._last_tick = now
        self._phase = now

        # 1) 一段时间没看到手 -> 回角落待机。
        #    "没人的时候别赖在屏幕中间"是桌宠的基本礼貌。
        quiet = now - self._last_hand
        if quiet > self.cfg.idle_after and not self._dragging and now > self._park_until:
            if self._mood not in ("sleepy", "shy"):
                self._set_mood("sleepy" if quiet > self.cfg.idle_after * 2 else "idle", 0.5)
            self._place_home(immediate=False)

        self._follow_cursor()

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

        # 3) 情绪超时后退回 idle
        if now > self._mood_until and self._mood not in ("idle", "sleepy"):
            self._set_mood("idle", 0.5)

        # 4) 呼吸与晃动：一直存在的微小运动，静止时也不像一张贴图
        target = PetPose.for_mood(self._mood)
        target.squash += 0.030 * math.sin(now * 2.1)
        target.bob += 0.012 * math.sin(now * 1.7 + 0.6)

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

        # 关闭状态下画一条淡淡的"待机"提示，免得用户以为程序挂了
        if self._mood == "sleepy":
            p.setPen(QColor(120, 140, 150, 150))
            p.setFont(ui_font(8))
            p.drawText(QRectF(0, self.win_h - 18, self.win_w, 16),
                       int(Qt.AlignmentFlag.AlignHCenter), "待机中")
        p.end()

    # ---------------------------------------------------------------- 鼠标
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_grab = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._dragging:
            pos = ev.globalPosition().toPoint() - self._drag_grab
            self.move(pos)
            self._pos = QPointF(pos)
            self._target = QPointF(pos)
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.pet_me()
            # 用户刚把它摆好，10 秒内别自己跑走（否则"我放这儿"的意图被无视）
            self._park_until = time.monotonic() + 10.0
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self.pet_me()
        ev.accept()
