# -*- coding: utf-8 -*-
"""
桌宠骨架的纯逻辑测试 —— 不需要摄像头、不需要模型、**不需要 Qt**。

跑法：
    python tests/test_pet_core.py

覆盖三件最容易悄悄坏掉的事：
    1. 事件总线：跨线程投递、类型过滤、单个订阅者出错不影响别人
    2. 手势引擎：悬停触发一次 / 松手才能再触发 / 手在动不触发 / 双手序列开关
    3. 映射与配置：左右手分开映射、pet: 前缀绝不碰系统、配置能存能读

界面部分不做自动化测试（PyQt6 需要真实桌面），用 `python -m pet --snapshot` 出图人工看。
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hand_tracker import Hand                                    # noqa: E402
from pet.core.bus import EventBus                                # noqa: E402
from pet.core.config import Config                               # noqa: E402
from pet.core.engine import GestureEngine                        # noqa: E402
from pet.core.events import (GestureFired, HandsSeen,            # noqa: E402
                            PerceptionFailed)
from pet.intent import mapping                                   # noqa: E402

DT = 1 / 30
PASS = []

# 一只"张开的手"，坐标取自 tests/test_gestures.py 的同一份基准
BASE = [
    (0.50, 0.75), (0.44, 0.70), (0.42, 0.66), (0.43, 0.62), (0.45, 0.58),
    (0.455, 0.60), (0.45, 0.53), (0.45, 0.47), (0.46, 0.43),
    (0.50, 0.60), (0.50, 0.52), (0.50, 0.46), (0.50, 0.42),
    (0.545, 0.60), (0.55, 0.53), (0.55, 0.47), (0.55, 0.44),
    (0.585, 0.62), (0.59, 0.55), (0.59, 0.50), (0.58, 0.47),
]


def check(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        raise AssertionError(name)


def mkhand(dx=0.0, dy=0.0, side="right", gesture="", score=0.9):
    lm = np.zeros((21, 3), np.float32)
    for i, (x, y) in enumerate(BASE):
        lm[i] = (x + dx, y + dy, 0)
    return Hand(landmarks=lm, score=0.9, aspect=1280 / 720, side=side,
                model_gesture=gesture, model_score=score)


def fresh_engine(**kw):
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return GestureEngine(cfg)


# ---------------------------------------------------------------- 事件总线
print("== EventBus：投递 / 类型过滤 / 隔离 ==")
bus = EventBus()
got = []
bus.subscribe(HandsSeen, got.append)
bus.post(HandsSeen(count=1))
bus.post(GestureFired(gesture="FIST", side="right", intent="mute"))
bus.drain()
check("只收到订阅的类型", len(got) == 1 and isinstance(got[0], HandsSeen), f"({len(got)} 条)")

other = []


def boom(_ev):
    raise RuntimeError("故意炸一下")


bus.subscribe(HandsSeen, boom)
bus.subscribe(PerceptionFailed, other.append)
bus.post(PerceptionFailed(message="x"))
bus.post(HandsSeen(count=2))
n = bus.drain()
check("订阅者出错不影响其它订阅者", len(other) == 1 and n == 2, f"(处理 {n} 条)")

# ---------------------------------------------------------------- 手势引擎
print("== GestureEngine：悬停确认 ==")
eng = fresh_engine()
h = mkhand(gesture="VICTORY")
t, fires = 0.0, 0
for _ in range(30):                      # 1 秒，远超过 0.5 秒悬停
    out = eng.update([mkhand(gesture="VICTORY")], t)
    fires += len(out.fired)
    t += DT
check("举着 1 秒只触发一次", fires == 1, f"({fires} 次)")

for _ in range(15):                      # 离开画面 0.5s > grace 0.35s，才算真的松手
    eng.update([], t)
    t += DT
fires = 0
for _ in range(30):
    out = eng.update([mkhand(gesture="VICTORY")], t)
    fires += len(out.fired)
    t += DT
check("松手后可以再次触发", fires == 1, f"({fires} 次)")

print("== GestureEngine：手在动不触发 ==")
eng2 = fresh_engine()
t, fires = 0.0, 0
for i in range(45):
    out = eng2.update([mkhand(dx=0.004 * i, gesture="VICTORY")], t)
    fires += len(out.fired)
    t += DT
check("持续横移 1.5 秒不触发", fires == 0, f"({fires} 次)")

print("== GestureEngine：双手总开关 ==")
eng3 = fresh_engine()
t = 0.0


def two(g_left, g_right):
    return [mkhand(dx=-0.25, side="left", gesture=g_left),
            mkhand(dx=0.25, side="right", gesture=g_right)]


toggled = None
seq = [("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM"),
       ("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM")]
for g in seq:
    for _ in range(5):                   # 每步停留 ~0.17s > dwell 0.1s
        out = eng3.update(two(*g), t)
        if out.toggled is not None:
            toggled = out.toggled
        t += DT
check("✊✋✊✋ 两遍 -> 关闭控制", toggled is False, f"(toggled={toggled})")

print("== GestureEngine：双手时不误发单手指令 ==")
eng4 = fresh_engine()
t, fires = 0.0, 0
for _ in range(40):
    out = eng4.update(two("VICTORY", "OPEN_PALM"), t)
    fires += len(out.fired)
    t += DT
check("双手在场抑制单手指令", fires == 0, f"({fires} 次)")

# ---------------------------------------------------------------- 映射
print("== 映射表 ==")
r = mapping.resolve("right", "POINT")
l = mapping.resolve("left", "POINT")
check("右手食指下一页", r is not None and r.key == "page_down", f"({r.key})")
check("左手食指上一页", l is not None and l.key == "page_up", f"({l.key})")
pat = mapping.resolve("right", "PINCH")
check("单手捏合只摸宠物（不碰系统）", pat is not None and pat.key.startswith("pet:"),
      f"({pat.key})")
check("未映射的手势返回 None", mapping.resolve("right", "UNKNOWN") is None)

# ---------------------------------------------------------------- 配置
print("== 配置读写 ==")
cfg_path = os.path.join(tempfile.gettempdir(), "pet_cfg_test.json")
cfg = Config(pet_name="测试团子", hold=0.42, follow="cursor")
cfg.save(cfg_path)
back = Config.load(cfg_path)
check("round-trip 保留字段", back.pet_name == "测试团子" and abs(back.hold - 0.42) < 1e-9)
back.override(pet_name="覆盖后", follow=None)
check("override 跳过 None", back.pet_name == "覆盖后" and back.follow == "cursor")
try:
    os.remove(cfg_path)
except OSError:
    pass

print(f"\n[RESULT] {len(PASS)} 项全部通过")
