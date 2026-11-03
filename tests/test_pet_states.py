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
import time

# 必须在导入 QtWidgets 之前设，否则会尝试连真实显示服务
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def make_window() -> PetWindow:
    """最小可用窗口：不用贴图（免得依赖 assets），但走完整的真实构造路径。"""
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.sprite = ""            # 空串 = 用程序化矢量版，且不读任何文件
    cfg.sprite_side = ""
    cfg.click_through = False
    bus = EventBus()
    win = PetWindow(bus, cfg)
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
fire(win, "pet:follow")
fire(win, "pet:sleep")
check("跟随时也能睡", win._asleep is True)
fire(win, "pet:wake")
# 「跟鼠标」是临时态（☝️ 是一次性指令），睡一觉醒来回到原来的跟手，
# 而不是继续保持一个用户可能已经不想要的跟随状态
check("醒来回到睡前的手势跟随", win.cfg.follow == "hand")
check("备份已清空，不会再次误还原", win._follow_backup == "")

print("== 失败的动作不该改状态 ==")
win = make_window()
win._on_action(ActionDone(intent="pet:sleep", label="睡觉", ok=False,
                          detail="故意失败", bubble="", mood="surprised"))
check("动作失败时保持清醒", win._asleep is False)

print(f"\n[RESULT] {len(PASS)} 项全部通过")
