# -*- coding: utf-8 -*-
"""
核心逻辑的单元测试（不需要摄像头、不需要模型）。

跑法：
    python tests/test_gestures.py

为什么放在仓库里而不是临时目录：之前这些验证脚本躺在 %TEMP%，被 Windows
清理之后就没法复跑了。踩过的坑用测试固化下来，比写在 README 里更可靠。
"""
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gestures as G                     # noqa: E402
from hand_tracker import Hand, assign_sides, WRIST, MIDDLE_MCP   # noqa: E402

DT = 1 / 30
PASS = []


def check(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        raise AssertionError(name)


def mkhand(dx=0.0, dy=0.0, aspect=1280 / 720):
    """一只张开的手，整体平移"""
    lm = np.zeros((21, 3), np.float32)
    base = [
        (0.50, 0.75), (0.44, 0.70), (0.42, 0.66), (0.43, 0.62), (0.45, 0.58),
        (0.455, 0.60), (0.45, 0.53), (0.45, 0.47), (0.46, 0.43),
        (0.50, 0.60), (0.50, 0.52), (0.50, 0.46), (0.50, 0.42),
        (0.545, 0.60), (0.55, 0.53), (0.55, 0.47), (0.55, 0.44),
        (0.585, 0.62), (0.59, 0.55), (0.59, 0.50), (0.58, 0.47),
    ]
    for i, (x, y) in enumerate(base):
        lm[i] = (x + dx, y + dy, 0)
    return Hand(landmarks=lm, score=0.9, aspect=aspect)


# ---------------------------------------------------------------- 悬停防抖
print("== Debouncer：悬停 / 冷却 / 宽容窗口 ==")
d = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
t, fired_at = 0.0, None
for _ in range(45):
    _, f = d.update("FIST", t)
    if f and fired_at is None:
        fired_at = t
    t += DT
check("悬停 0.5 秒触发", fired_at is not None and 0.45 <= fired_at <= 0.6, f"({fired_at:.2f}s)")

d2 = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
t, n = 0.0, 0
for _ in range(90):                       # 一直举着 3 秒
    _, f = d2.update("FIST", t)
    n += bool(f)
    t += DT
check("举着不动只触发一次", n == 1, f"(触发 {n} 次)")

d3 = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
t, n = 0.0, 0
seq = [("FIST", 0.6), (None, 0.2), ("FIST", 0.6)]   # 中途丢 0.2 秒
for raw, dur in seq:
    for _ in range(int(dur / DT)):
        _, f = d3.update(raw, t)
        n += bool(f)
        t += DT
check("丢 0.2 秒不清零（宽容 0.35）", n == 1, f"(触发 {n} 次)")

# ---------------------------------------------------------------- 静止门控
print("== MotionGate：手在动就冻结计时 ==")
gate = G.MotionGate(max_speed=0.6)
deb = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
t, fired = 0.0, False
for i in range(60):                       # 每秒扫过 0.35 画面宽
    h = mkhand(dx=-0.18 + 0.35 * (t / 2.0))
    still = gate.update(t, h)
    _, f = deb.update("OPEN_PALM", t, moving=not still)
    fired |= f
    t += DT
check("挥手经过不触发", not fired, f"(速度 {gate.speed:.1f} 掌长/秒)")

gate2 = G.MotionGate(max_speed=0.6)
deb2 = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
t, fired = 0.0, False
for i in range(90):                       # 1.5 秒后停下
    h = mkhand(dx=(-0.18 + 0.24 * t) if t < 1.5 else 0.18)
    still = gate2.update(t, h)
    _, f = deb2.update("OPEN_PALM", t, moving=not still)
    fired |= f
    t += DT
check("停下后继续计时并触发", fired)

# ---------------------------------------------------------------- 缩放
print("== StepAccumulator：按累计位移出格 ==")
acc = G.StepAccumulator(step_size=0.35)
n, dist = 0, 4.0
for _ in range(30):                       # 1 秒拉开 1.5 掌长
    dist += 1.5 * DT
    n += abs(acc.update(dist))
check("1.5 掌长 = 4 格", n == 4, f"({n} 格)")

acc2 = G.StepAccumulator(step_size=0.35)
rng = np.random.default_rng(0)
n, dist = 0, 4.0
for _ in range(60):                       # 2 秒手抖
    dist += 0.005 * rng.standard_normal()
    n += abs(acc2.update(dist))
check("手抖不出格", n == 0, f"({n} 格)")

print("== PinchTapDetector：连捏两次才重置 ==")


def pinch(tap, t, hold=0.33, d=4.02, drift=0.0):
    out = False
    n = int(hold / DT)
    for i in range(n):
        tap.update(True, d + drift * (i / max(n, 1)), t)
        t += DT
    for _ in range(3):
        out |= tap.update(False, d + drift, t)
        t += DT
    return out, t


tap = G.PinchTapDetector()
f1, t = pinch(tap, 0.0)
check("只捏一次不重置", not f1)
f2, t = pinch(tap, t + 0.3)
check("第二捏在 1 秒内 -> 重置", f2)

tap = G.PinchTapDetector()
f1, t = pinch(tap, 0.0)
f2, t = pinch(tap, t + 2.0)
check("两次间隔 2 秒 -> 不重置", not (f1 or f2))

tap = G.PinchTapDetector()
f1, t = pinch(tap, 0.0, hold=1.5, drift=0.4)
check("捏住 1.5 秒（正常缩放）-> 不算捏合", not f1)

# ---------------------------------------------------------------- 总开关
print("== SequenceDetector：双手 ✊✋✊✋ ==")
STEPS = [("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM"),
         ("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM")]
F, O = ("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM")


def runseq(seq, timeline):
    fired, t = False, 0.0
    for pair, dur in timeline:
        for _ in range(max(1, int(dur / DT))):
            fired |= seq.update(pair, t)
            t += DT
    return fired


check("完整做一遍触发", runseq(G.SequenceDetector(STEPS), [(F, .3), (O, .3), (F, .3), (O, .3)]))
check("只做半套不触发", not runseq(G.SequenceDetector(STEPS), [(F, .3), (O, .3)]))
check("太慢不触发", not runseq(G.SequenceDetector(STEPS),
                              [(F, 1.5), (O, 1.5), (F, 1.5), (O, 1.5)]))
check("单手不同步不触发", not runseq(G.SequenceDetector(STEPS),
                                    [(("FIST", "OPEN_PALM"), .3), (O, .3),
                                     (("FIST", "OPEN_PALM"), .3), (O, .3)]))

# ---------------------------------------------------------------- 捏合判定
print("== 捏合 / 握拳 ==")
check("握拳不判成捏合（ti 大）", not G.is_pinch(0.50, 0.75))
check("捏合判成捏合", G.is_pinch(0.15, 0.15))
check("拇指贴整排手指不算捏合", not G.is_pinch(0.30, 0.72))

# ---------------------------------------------------------------- 挥手
print("== SwipeDetector：挥手 ==")


def swipe_path(points, arm=True):
    """按给定轨迹喂帧，返回触发方向（触发不了返回 None）。"""
    sw = G.SwipeDetector()
    if arm:
        sw.arm()
    t, got = 0.0, None
    for dx, dy in points:
        got = sw.update(t, mkhand(dx=dx, dy=dy)) or got
        t += DT
    return got


check("张开手掌快速右划 -> right",
      swipe_path([(0.022 * i, 0.0) for i in range(12)]) == "right")
check("反向划 -> left",
      swipe_path([(0.242 - 0.022 * i, 0.0) for i in range(12)]) == "left")
check("慢慢挪过去不算挥手",
      swipe_path([(0.008 * i, 0.0) for i in range(12)]) is None)
check("上下移动不算挥手",
      swipe_path([(0.0, 0.022 * i) for i in range(12)]) is None)
# 手在 0 和 0.2 之间来回摆：首尾差够大，但路径长度远超直线 —— 这不是"划过去"
check("来回摆手不算挥手",
      swipe_path([(0.0 if i % 2 else 0.2, 0.0) for i in range(12)]) is None)
check("没张开手掌（没 arm）不判定",
      swipe_path([(0.022 * i, 0.0) for i in range(12)], arm=False) is None)

# 冷却：同一段动作不能翻两页
_sw = G.SwipeDetector()
_sw.arm()
_t, _first, _second = 0.0, None, None
for i in range(12):
    _first = _sw.update(_t, mkhand(dx=0.022 * i)) or _first
    _t += DT
for i in range(12):
    _second = _sw.update(_t, mkhand(dx=0.242 - 0.022 * i)) or _second
    _t += DT
check("触发后有冷却，划一下不会翻两页", _first == "right" and _second is None,
      f"({_first}/{_second})")

# ---------------------------------------------------------------- 长按
print("== Debouncer：长按（第二层）==")
_dl = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
_dl.long_after = 1.0
_t, _fires, _longs = 0.0, 0, 0
for _i in range(60):                       # 2 秒，远超 0.5 + 1.0
    _s, _f = _dl.update("VICTORY", _t)
    _fires += bool(_f)
    _longs += bool(_dl.long_fired)
    _t += DT
check("长按只单击一次", _fires == 1, f"({_fires} 次)")
check("长按只进入第二层一次", _longs == 1, f"({_longs} 次)")

# 长按之后继续举着，不该无限重复
_t2, _more = _t, 0
for _i in range(90):
    _dl.update("VICTORY", _t2)
    _more += bool(_dl.long_fired)
    _t2 += DT
check("继续举着也不会反复长按", _more == 0, f"({_more} 次)")

_dl2 = G.Debouncer(hold_seconds=0.5, cooldown=0.6, grace=0.35)
_dl2.long_after = 0.0                      # 关闭长按
_t3, _n3 = 0.0, 0
for _i in range(60):
    _dl2.update("VICTORY", _t3)
    _n3 += bool(_dl2.long_fired)
    _t3 += DT
check("long_after=0 时完全不长按", _n3 == 0)

# ---------------------------------------------------------------- 左右手
print("== 左右手判定 ==")
hs = [mkhand(dx=0.2), mkhand(dx=-0.2)]
assign_sides(hs, "position")
check("画面左侧 = 左手", [h.side for h in hs] == ["left", "right"])
hs = [mkhand(dx=0.2), mkhand(dx=-0.2)]
assign_sides(hs, "position", swap=True)
check("--swap-hands 翻转", [h.side for h in hs] == ["right", "left"])

print(f"\n[RESULT] {len(PASS)} 项全部通过")
