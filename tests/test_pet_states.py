# -*- coding: utf-8 -*-
"""
宠物状态机测试 —— 手势触发的**持久状态**是否真的改变了行为。

跑法：
    python tests/test_pet_states.py

为什么单独一个文件、而且用 offscreen 平台：
    这几个状态（睡觉 / 唤醒 / 跟随 / 跳舞）只在窗口层存在，纯逻辑测试
    覆盖不到；但真开一个窗口去截图验证又不可靠——demo 里宠物一直在移动，
    截图时机差 200ms 就拍空。
    用 Qt 的 offscreen 后端直接驱动状态迁移，既不需要显示器，也能断言
    "睡了之后不再跟手"这种真正会坏掉的行为。

注意：窗口只构造、不 show()。我们验证的是状态和每帧计算，
不是像素——像素有 --snapshot 管。
"""

import os
import sys
import tempfile
import time

# 必须在导入 QtWidgets 之前设，否则会尝试连真实显示服务
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF                                 # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from pet.core.bus import EventBus                                # noqa: E402
from pet.core.config import Config                               # noqa: E402
from pet.core.events import ActionDone, HandsSeen                # noqa: E402
from pet.ui.window import PetWindow                              # noqa: E402

PASS = []


def check(name: str, cond: bool) -> None:
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        raise AssertionError(name)


def make_window(bond=None) -> PetWindow:
    """最小可用窗口：不用贴图（免得依赖 assets），但走完整的真实构造路径。"""
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.sprite = ""            # 空串 = 用程序化矢量版，且不读任何文件
    cfg.sprite_side = ""
    cfg.click_through = False
    bus = EventBus()
    win = PetWindow(bus, cfg, bond=bond)
    win._asleep = False
    return win


def fire(win: PetWindow, key: str) -> None:
    """模拟一次"手势 -> 动作完成"的事件，走 _on_action 的真实入口。"""
    win._on_action(ActionDone(intent=key, label=key, ok=True,
                              detail="test", bubble="", mood="happy"))


app = QApplication.instance() or QApplication([])

print("== 睡觉 / 唤醒 ==")
win = make_window()
fire(win, "pet:sleep")
check("✊ 之后进入睡眠锁", win._asleep is True)
check("睡觉时表情切成 sleepy", win._mood == "sleepy")

# 这是整个改动里最容易坏的一条：睡着之后，手在画面里晃不该把它拖着走
before = win.x(), win.y()
win._on_hands(HandsSeen(count=1, point=(0.1, 0.1), gesture=None,
                        side="right", progress=0.0, moving=False, speed=0.0))
after = win.x(), win.y()
check("睡着时不跟手（位置不变）", before == after)

fire(win, "pet:wake")
check("✋ 之后解除睡眠", win._asleep is False)
check("醒来时表情是 happy", win._mood == "happy")

print("== 跳舞 ==")
win = make_window()
fire(win, "pet:dance")
check("✌️ 设定了跳舞到期时刻", win._dance_until > time.monotonic())
check("跳舞时表情是 dance", win._mood == "dance")
# 姿态表里必须真的有 dance 这一档，否则会静默退回 idle 造型
from pet.ui.paint import PetPose                                 # noqa: E402
dance_pose = PetPose.for_mood("dance")
check("dance 姿态不是默认值", abs(dance_pose.tilt) > 0.1 and dance_pose.mouth > 0.5)

print("== 跟随鼠标（可进可退）==")
win = make_window()
win.cfg.follow = "hand"
fire(win, "pet:follow")
check("☝️ 切到跟随鼠标", win.cfg.follow == "cursor")
check("记住了切换前的模式", win._follow_backup == "hand")
fire(win, "pet:follow")
check("再比一次退出跟随", win.cfg.follow == "hand")

print("== 睡觉优先于跟随 ==")
win = make_window()
win.cfg.follow = "corner"        # 显式设定，不依赖 Config 默认值（它改过一次）
fire(win, "pet:follow")
fire(win, "pet:sleep")
check("跟随时也能睡", win._asleep is True)
fire(win, "pet:wake")
# 「跟鼠标」是临时态（☝️ 是一次性指令），睡一觉醒来回到原来的方式，
# 而不是继续保持一个用户可能已经不想要的跟随状态
check("醒来回到睡前的方式", win.cfg.follow == "corner")
check("备份已清空，不会再次误还原", win._follow_backup == "")

print("== 失败的动作不该改状态 ==")
win = make_window()
win._on_action(ActionDone(intent="pet:sleep", label="睡觉", ok=False,
                          detail="故意失败", bubble="", mood="surprised"))
check("动作失败时保持清醒", win._asleep is False)

print("== 隔空撸宠 ==")


def hands_at(speed: float):
    """造一帧"看到手"的事件。point 固定，只改速度。"""
    return HandsSeen(count=1, point=(0.5, 0.5), gesture=None, side="right",
                     progress=0.0, moving=speed > 0.5, speed=speed)


def hands_at(speed: float, point=(0.5, 0.5)):
    """造一帧"看到手"的事件。"""
    return HandsSeen(count=1, point=point, gesture=None, side="right",
                     progress=0.0, moving=speed > 0.5, speed=speed)


def park_at_center(win: PetWindow) -> None:
    """把宠物摆到屏幕正中央，好让 point=(0.5,0.5) 的手正好落在它身上。"""
    r = win._screen_rect()
    win._pos = QPointF(r.center().x() - win.pet_cx,
                       r.center().y() - win.pet_cy)
    win._target = QPointF(win._pos)


win = make_window()
park_at_center(win)
win._update_petting(100.0, hands_at(0.1))
check("刚开始摸还没反应（要等 pet_delay）", win._petting is False)
win._update_petting(100.0 + win.cfg.pet_delay + 0.1, hands_at(0.1))
check("手落在它身上轻抚后进入被摸状态", win._petting is True)

win._update_petting(101.0, hands_at(2.5))
check("手快速划过立刻中断", win._petting is False)

win._update_petting(102.0, None)
check("手离开画面也中断", win._petting is False)
check("离开后计时清零", win._pet_since == 0.0)

# 手在画面另一头慢慢晃不算摸。这条是改成 corner 之后最关键的边界：
# 它守在角落不动，如果还按"宠物有没有追上手"判断，这个条件会恒为真。
win = make_window()
park_at_center(win)
win._update_petting(100.0, hands_at(0.1, point=(0.05, 0.05)))
win._update_petting(100.0 + win.cfg.pet_delay + 0.1, hands_at(0.1, point=(0.05, 0.05)))
check("手不在它身上时不算摸", win._petting is False)
check("不在身上时计时也被清掉", win._pet_since == 0.0)

# 睡着的时候不该被"摸"——睡眠锁优先
win = make_window()
park_at_center(win)
fire(win, "pet:sleep")
win._update_petting(100.0, hands_at(0.1))
win._update_petting(100.0 + win.cfg.pet_delay + 0.1, hands_at(0.1))
check("睡着时不会被摸醒", win._petting is False)

# 摸够久要主动说一句，否则用户会怀疑功能坏了
win = make_window()
park_at_center(win)
win._update_petting(100.0, hands_at(0.1))
win._update_petting(100.0 + win.cfg.pet_delay + 0.1, hands_at(0.1))
check("第一次被摸会应一声", win._pet_said is False)
win._update_petting(104.0, hands_at(0.1))
check("摸够 3.5 秒说第二句", win._pet_said is True)

print("== 亲密度接入 ==")
from pet.core.bond import Bond, BondTracker                      # noqa: E402

bond_path = os.path.join(tempfile.mkdtemp(prefix="bondst_"), "bond.json")
bt = BondTracker(bond_path, Bond())
win = make_window(bond=bt)

before = win.bond.bond.points
fire(win, "pet:dance")
check("动作成功会加分", win.bond.bond.points > before)
check("加了分标记为待保存", win._bond_dirty is True)
check("互动次数也记上了", win.bond.bond.interactions == 1)

# 跨过门槛要庆祝：这是"关系在推进"唯一能被感知到的反馈
bt.bond.points = 29.5
win.bond.bond.points = 29.5
fire(win, "pet:pat")
check("跨门槛后等级变成熟悉", win.bond.bond.level_name == "熟悉")
check("升级时切到得意表情", win._mood == "proud")

# 没有存档时（测试 / headless / 目录只读）一切照旧，不能崩
win = make_window()
fire(win, "pet:dance")
check("没有亲密度存档时也不崩", win.bond is None)

print("== 情绪反应 ==")
from pet.core.events import EmotionChanged                         # noqa: E402
from pet.core.events import PetSay                                 # noqa: E402

win = make_window()
win._on_emotion(EmotionChanged(emotion="tired", strength=0.8))
check("用户累了切安静模式", win._user_mood == "tired" and win._mood == "calm")

win = make_window()
win._on_emotion(EmotionChanged(emotion="frown", strength=0.6))
check("用户皱眉会说一句关心", win._bubble != "")
first_until = win._care_until
check("关怀设了冷却", first_until > time.monotonic())

# 皱着眉头工作半小时，不该被关怀三十遍
win._on_emotion(EmotionChanged(emotion="frown", strength=0.6))
check("冷却期内不重复关怀", win._care_until == first_until)

win = make_window()
win._on_emotion(EmotionChanged(emotion="happy", strength=0.7))
check("用户笑时跟着高兴", win._user_mood == "happy" and win._cheer_until > 0)

print("== 跟随方式：corner 不再跟手 ==")
# 默认 follow 改成 corner 之后，"手在画面里动 == 宠物跟着跑"必须彻底停掉。
# 这条用截图验证不了（demo 里 ☝️ 会切到跟鼠标，看起来还在动），只能锁逻辑。
win = make_window()
check("默认是守在角落", win.cfg.follow == "corner")
before = QPointF(win._target)
win._on_hands(hands_at(0.1, point=(0.15, 0.15)))
check("corner 模式下手动不会带着它跑", win._target == before)

win.cfg.follow = "hand"
win._on_hands(hands_at(0.1, point=(0.8, 0.8)))
check("切回跟手后它会动（功能没被删）", win._target != before)

print("== 右键入口：右键它要能弹出功能菜单 ==")
# 用户报过"原本右键弹出的窗口里能玩石头剪刀布/看情绪，现在没法触发"。
# 这类问题截图看不出来（菜单要真点一次才出现），只能锁事件分支：
#   * 落在身体上 -> 吃掉事件并弹菜单（没托盘时退回控制面板）
#   * 落在透明空白 -> 必须 ignore，让桌面弹自己的右键菜单
from PySide6.QtCore import QPoint                                    # noqa: E402
from PySide6.QtGui import QContextMenuEvent                         # noqa: E402

win = make_window()
win.show()
body = QPoint(int(win.pet_cx), int(win.pet_cy))
ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, body, win.mapToGlobal(body))
win.contextMenuEvent(ev)
check("右键它 = 有反应（没托盘时兜底弹控制面板）",
      win._panel is not None and win._panel.isVisible())

blank = QPoint(2, 2)
ev2 = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, blank, win.mapToGlobal(blank))
win.contextMenuEvent(ev2)
check("点在透明空白不吞右键（交给桌面）", ev2.isAccepted() is False)

print("== 控制面板：玩法入口真的在，而且点了会改状态 ==")
from PySide6.QtCore import QEvent, Qt                                 # noqa: E402
from PySide6.QtGui import QMouseEvent                                # noqa: E402

from pet.core.control import ControlCenter                           # noqa: E402
from pet.core.engine import GestureEngine                            # noqa: E402
from pet.ui.panel import ControlPanel                                # noqa: E402

cfg = Config()
cfg.sprite = ""
cfg.click_through = False
cfg.save = lambda *a, **k: None          # 测试绝不写用户真实的 gestures.yaml
bus = EventBus()
engine = GestureEngine(cfg)
control = ControlCenter(bus, engine, cfg)
win = PetWindow(bus, cfg, control)
win.show()
panel = ControlPanel(win)
keys = [k for k, _ in panel._hits]
check("面板里有『石头剪刀布』", "game" in keys)
check("面板里有『情绪感知』", "emotion" in keys)

rect = dict(panel._hits)["emotion"]
pos = rect.center()
click = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, pos,
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier)
before = cfg.emotion
panel.mouseReleaseEvent(click)
check("点一下就切换了情绪感知", cfg.emotion is not before)

# 刚点下"玩游戏"时 game.active 还是 False（要等感知线程下一帧），
# 只看 active 的话勾选会弹回去，用户会以为没点上。
class _FakeWorker:
    _game_pending = True
    game = type("G", (), {"active": False})()

control.worker = _FakeWorker()
check("刚点下游戏（还没开跑）时开关不会弹回去", panel._game_active() is True)
check("没有感知线程时点游戏会说明原因", ControlCenter(bus, engine, cfg).set_game(True) is False)

print("== 识别关着时开玩法：必须顺手打开摄像头 ==")
# 这是"点了没反应"的根源：识别关掉后感知线程的暂停分支每轮 continue，
# `_game_pending` 永远轮不到被消费——菜单勾着、游戏不开始。
# 截图和日志都看不出来（没有任何报错），只能锁状态。


class _FakeGameWorker:
    _game_pending = False
    emotion = object()          # 假装表情/人脸检测器都在
    presence = object()

    def set_game(self, on: bool) -> bool:
        self._game_pending = bool(on)
        return bool(on)


control2 = ControlCenter(bus, engine, cfg)
control2.worker = _FakeGameWorker()
control2.set_enabled(False)
check("关掉之后引擎是停的", engine.enabled is False)
check("点游戏能进得去", control2.set_game(True) is True)
check("顺手把摄像头打开了（不然游戏永远起不来）", engine.enabled is True)
check("游戏确实挂上了待启动", control2.worker._game_pending is True)

control2.set_enabled(False)
check("情绪感知也会顺手开摄像头", control2.set_emotion(True) is True and engine.enabled is True)

print("== 石头剪刀布：点了就有回应（不能静默）==")
# 用户报"点开石头剪刀布没法玩"。除了摄像头和判定链路，还有一层是**说的话**：
# 关闭时说成"三秒后出拳"、识别关着时说"三秒后出拳"然后干等，都会让人以为坏了。
says2 = []
bus.subscribe(PetSay, lambda e: says2.append(e.text))
control2.set_enabled(True)
bus.drain()
says2.clear()

control2.set_game(False)
bus.drain()
check("关掉游戏说『不玩啦』", bool(says2) and "不玩" in says2[-1])

says2.clear()
control2.set_game(True)
bus.drain()
check("识别开着时直接报倒数", bool(says2) and "三秒后出拳" in says2[-1])

says2.clear()
control2.set_enabled(False)
bus.drain()
says2.clear()
control2.set_game(True)
bus.drain()
check("识别关着时先说在开摄像头（不谎报倒数）",
      bool(says2) and "摄像头" in says2[-1] and "三秒后出拳" not in says2[-1])
check("这条路上摄像头也真的开了", engine.enabled is True)

# 刚点开（还在 pending）再点一次应该能关掉，而不是"再开一次"
control2.set_game(True)
check("pending 状态下能关掉游戏", control2.toggle_game() is False
      and control2.worker._game_pending is False)

print("== 左键单击：弹三个圆按钮，不再直接弹面板 ==")
# 用户要求：左键点它弹"设置/互动/操作"三个圆，设置才开面板，
# 另外两个直接切换单手表。右键保持不变（上面那组用例还在锁它）。
from pet.intent import mapping                                       # noqa: E402

# 这个窗口要真的能改配置，所以带上真正的 ControlCenter（make_window 没挂）
cfg3 = Config()
cfg3.sprite = ""
cfg3.sprite_side = ""
cfg3.click_through = False
cfg3.save = lambda *a, **k: None      # 绝不写用户真实的 gestures.yaml
bus3 = EventBus()
engine3 = GestureEngine(cfg3)
control3 = ControlCenter(bus3, engine3, cfg3)
win3 = PetWindow(bus3, cfg3, control3)
win3.show()
p3 = QPointF(win3.pet_cx, win3.pet_cy)
click_body = QMouseEvent(QEvent.Type.MouseButtonPress, p3,  # noqa: E402
                         win3.mapToGlobal(p3.toPoint()),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
win3.mousePressEvent(click_body)
win3.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, p3,
                                   win3.mapToGlobal(p3.toPoint()),
                                   Qt.MouseButton.LeftButton,
                                   Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.NoModifier))
check("左键单击弹出三个圆按钮", win3._orbit is not None and win3._orbit.isVisible())
check("…而且没有直接弹控制面板", win3._panel is None or not win3._panel.isVisible())
check("圆按钮是三个", len(win3._orbit._centers) == 3)
check("三个键分别是 设置 / 互动 / 操作",
      [n for _k, _i, n, _c in win3._orbit._centers] == ["设置", "互动", "操作"])
check("每个圆都带一个图标",
      all(i for _k, i, _n, _c in win3._orbit._centers))

# 视觉上的三条硬约束（都是踩过的坑）：图标要真能画出来、名称不能被裁、
# 入场动画要能走完（走不完就是一排空白圆，截图/用户都会以为坏了）
from PySide6.QtGui import QFontMetrics                           # noqa: E402

# 截图前先把入场动画推到终态：动画停在第 0 帧时本来就是一排空白圆
win3._orbit._anim.stop()
win3._orbit._appear = 1.0
_img = win3._orbit.grab().toImage()
_c = win3._orbit._centers[1][3]
check("三个圆真的画出来了（不是一张空白图）",
      _img.width() > 0 and _img.pixelColor(int(_c.x()), int(_c.y())).alpha() > 0)
_fm = QFontMetrics(win3._orbit._font_name)
check(f"名称胶囊放得下最长的名字（袋宽 {win3._orbit._pill_w:.0f}px）",
      win3._orbit._pill_w >= max(_fm.horizontalAdvance(n)
                                 for _k, _i, n, _c in win3._orbit._centers) + 12)
win3._orbit._appear = 0.0
win3._orbit.showEvent(None)
check("弹出时入场动画从 0 开始播",
      win3._orbit._appear < 0.5 and win3._orbit._anim.state()
      == win3._orbit._anim.State.Running)
win3._orbit._anim.stop()
win3._orbit._appear = 1.0

# 圆按钮的几何：三个圆不该压到宠物身上（压上去点不到宠物，
# 看着也像贴错位置的贴纸）
_ox, _oy = win3._orbit._origin.x(), win3._orbit._origin.y()
_nearest = min(((c.x() - _ox) ** 2 + (c.y() - _oy) ** 2) ** 0.5
               for _k, _i, _n, c in win3._orbit._centers)
check(f"圆按钮在宠物身外（最近圆心 {_nearest:.0f}px > 半身宽 {win3.body_w / 2:.0f}px）",
      _nearest - 23 > win3.body_w / 2)

# 名称：悬停才出现，鼠标移开就消失
_c = win3._orbit._centers[1][3]
win3._orbit.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, _c, _c,
                                       Qt.MouseButton.NoButton,
                                       Qt.MouseButton.NoButton,
                                       Qt.KeyboardModifier.NoModifier))
check("指针停在圆上 = 高亮它", win3._orbit._hover == "pet")
check("悬停时给出名称", win3._orbit.toolTip() == "互动")
win3._orbit.leaveEvent(None)
check("移开就收起名称", win3._orbit._hover == "")

# 点「互动」-> 整套切成逗桌宠，并且**立刻写进配置**
says3 = []
bus3.subscribe(PetSay, lambda e: says3.append(e.text))
win3._orbit.popup_near(win3)
win3._orbit.activate("pet")
bus3.drain()
check("点『互动』= 单手切成逗桌宠",
      mapping.resolve("right", "VICTORY").key == "pet:dance"
      and mapping.current_single_preset() == "pet")
check("…改的是左手右手共 14 格，不是只改一个",
      mapping.resolve("left", "FIST").key == "pet:sleep"
      and mapping.resolve("left", "OPEN_PALM").key == "pet:wake")
check("…并且说一句（不能点了没反应）",
      bool(says3) and "陪你玩" in says3[-1])
check("…切完把圆按钮收起来",
      not win3._orbit.isVisible())

# 点「操作」-> 切回窗口那套，配置回到"没有覆盖"（=出厂表）
win3._orbit.popup_near(win3)
win3._orbit.activate("window")
bus3.drain()
check("点『操作』= 单手切成操作窗口",
      mapping.resolve("right", "VICTORY").key == "alt_tab"
      and mapping.current_single_preset() == "window")
check("…窗口那套是出厂表，覆盖清单清空",
      not win3.cfg.bindings)

# 点「设置」-> 才打开原来那张面板
win3._orbit.popup_near(win3)
win3._orbit.activate("settings")
check("点『设置』才打开控制面板",
      win3._panel is not None and win3._panel.isVisible())
check("…开面板时圆按钮已收起（两个浮层别打架）", not win3._orbit.isVisible())

# 已经是这套时再点，不谎报"已切换"，但要给回应
says3.clear()
win3.control.set_single_preset("window")
bus3.drain()
check("重复点同一套 = 说明本来就这套，不假装又切了一次",
      bool(says3) and "就是" in says3[-1])

# 双手（音乐）的绑定不能被单表切换顺手清掉
win3.cfg.bindings = {"both:FIST": "play_pause"}
win3.control.set_single_preset("pet")
check("切单手不动双手的绑定",
      mapping.resolve("both", "FIST").key == "play_pause", )
check("…双手那条仍留在配置里", win3.cfg.bindings.get("both:FIST") == "play_pause")
win3.control.set_single_preset("window")
check("切回来也不动双手",
      mapping.resolve("both", "FIST").key == "play_pause"
      and win3.cfg.bindings.get("both:FIST") == "play_pause")
# 识别关着时切表要提醒"改了但不会发生"，否则用户以为切换失败了
engine3.set_enabled(False)
says3.clear()
win3.control.set_single_preset("pet")
bus3.drain()
check("识别关着时切表会提醒去打开",
      bool(says3) and "识别关着" in says3[-1])
check("…但表真的切了（不是什么都不做）",
      mapping.resolve("right", "VICTORY").key == "pet:dance")

check("不认识的名字不瞎切", win3.control.set_single_preset("火星版") is False)

mapping.apply_bindings({})           # 收尾：把运行时表还原，别污染后面

print(f"\n[RESULT] {len(PASS)} 项全部通过")
