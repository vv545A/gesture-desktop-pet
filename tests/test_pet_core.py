# -*- coding: utf-8 -*-
"""
桌宠骨架的纯逻辑测试 —— 不需要摄像头、不需要模型、**不需要 Qt**。

跑法：
    python tests/test_pet_core.py

覆盖四件最容易悄悄坏掉的事：
    1. 事件总线：跨线程投递、类型过滤、单个订阅者出错不影响别人
    2. 手势引擎：单手悬停触发一次 / 松手才能再触发 / 手在动不触发
    3. 双手：同手势配对才触发音乐、左右不一致什么都不做、捏合保持 = 总开关
    4. 映射与配置：单手=窗口、双手=音乐、pet: 前缀绝不碰系统、配置能存能读

界面部分不做自动化测试（PySide6 需要真实桌面），用 `python -m pet --snapshot` 出图人工看。
"""

import os
import sys
import json
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

THUMB_TIP, INDEX_TIP = 4, 8
INDEX_MCP, PINKY_MCP = 5, 17
#: 掌长（腕 -> 中指根）。BASE 里 y 从 0.75 到 0.60，正好 0.15。
#: 翻掌测试要把"翻了多少"换算成两个掌指关节的高度差，用的就是它。
PALM_LEN = 0.15


def check(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        raise AssertionError(name)


def mkhand(dx=0.0, dy=0.0, side="right", gesture="", score=0.9, pinch=False,
           tilt=0.0):
    lm = np.zeros((21, 3), np.float32)
    for i, (x, y) in enumerate(BASE):
        lm[i] = (x + dx, y + dy, 0)
    if tilt:
        # 翻掌：让小指根相对食指根出现高度差（单位：掌长）。
        # 负值 = 小指侧抬起 = 往"掌心向下"的方向翻，和真实手腕内旋一致。
        lm[PINKY_MCP][1] = lm[INDEX_MCP][1] + tilt * PALM_LEN
    if pinch:
        # 拇指尖贴到食指尖上 -> 几何判据认出 PINCH（手势模型没有这个类别）
        lm[THUMB_TIP] = lm[INDEX_TIP]
    return Hand(landmarks=lm, score=0.9, aspect=1280 / 720, side=side,
                model_gesture=gesture, model_score=score)


def fresh_engine(**kw):
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return GestureEngine(cfg)


def two(g_left, g_right, pinch=False):
    return [mkhand(dx=-0.25, side="left", gesture=g_left, pinch=pinch),
            mkhand(dx=0.25, side="right", gesture=g_right, pinch=pinch)]


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

# ---------------------------------------------------------------- 单手（窗口）
print("== GestureEngine：单手悬停确认 ==")
eng = fresh_engine()
t, fires = 0.0, []
for _ in range(30):                      # 1 秒，远超过 0.5 秒悬停
    out = eng.update([mkhand(gesture="VICTORY")], t)
    fires += out.fired
    t += DT
check("举着 1 秒只触发一次", len(fires) == 1, f"({len(fires)} 次)")
check("单手触发带手别", len(fires) == 1 and fires[0].side == "right")

for _ in range(15):                      # 离开画面 0.5s > grace 0.35s，才算真的松手
    eng.update([], t)
    t += DT
fires = []
for _ in range(30):
    out = eng.update([mkhand(gesture="VICTORY")], t)
    fires += out.fired
    t += DT
check("松手后可以再次触发", len(fires) == 1, f"({len(fires)} 次)")

print("== GestureEngine：手在动不触发 ==")
eng2 = fresh_engine()
t, n = 0.0, 0
for i in range(45):
    out = eng2.update([mkhand(dx=0.004 * i, gesture="VICTORY")], t)
    n += len(out.fired)
    t += DT
check("持续横移 1.5 秒不触发", n == 0, f"({n} 次)")

# ---------------------------------------------------------------- 挥手 / 滚轮 / 长按
print("== GestureEngine：挥手翻页 ==")
eng_s = fresh_engine()
t, got = 0.0, []
for i in range(12):
    out = eng_s.update([mkhand(dx=0.022 * i, gesture="OPEN_PALM")], t)
    got += [f.gesture for f in out.fired]
    t += DT
check("张开手掌右划 -> SWIPE_RIGHT", "SWIPE_RIGHT" in got, f"({got})")

eng_sl = fresh_engine()
t, got = 0.0, []
for i in range(12):
    out = eng_sl.update([mkhand(dx=0.242 - 0.022 * i, gesture="OPEN_PALM")], t)
    got += [f.gesture for f in out.fired]
    t += DT
check("张开手掌左划 -> SWIPE_LEFT", "SWIPE_LEFT" in got, f"({got})")

# ✌️ 横移不能被当成挥手 —— 否则每次切窗口都会顺手翻一页
eng_sv = fresh_engine()
t, got = 0.0, []
for i in range(20):
    out = eng_sv.update([mkhand(dx=0.02 * i, gesture="VICTORY")], t)
    got += [f.gesture for f in out.fired]
    t += DT
check("剪刀手横移不会翻页",
      not any(g.startswith("SWIPE_") for g in got), f"({got})")

eng_sg = fresh_engine()
eng_sg.set_game_mode(True)
t, got = 0.0, []
for i in range(12):
    out = eng_sg.update([mkhand(dx=0.022 * i, gesture="OPEN_PALM")], t)
    got += [f.gesture for f in out.fired]
    t += DT
check("游戏模式里不判挥手", not any(g.startswith("SWIPE_") for g in got), f"({got})")

print("== GestureEngine：食指滚轮 ==")
eng_w = fresh_engine()
t, steps, vals = 0.0, 0, []
for i in range(36):
    out = eng_w.update([mkhand(dy=-0.010 * i, gesture="POINT")], t)
    for kind, v in out.continuous:
        if kind == "scroll":
            steps += 1
            vals.append(v)
    t += DT
check("食指上移会连续滚动", steps >= 3, f"({steps} 格)")
check("手往上 -> 值为负（执行层取反后是向上滚）", all(v < 0 for v in vals),
      f"({vals[:4]})")

eng_wn = fresh_engine()
t, n = 0.0, 0
for i in range(36):
    out = eng_wn.update([mkhand(dy=-0.010 * i, gesture="VICTORY")], t)
    n += len(out.continuous)
    t += DT
check("不是食指时不滚动", n == 0, f"({n} 次)")

# 单手滚 -> 双手（音乐）-> 回单手：累计位移必须清掉。
# 不清的话，回来第一帧会拿"双手模式前"的旧位置算差值，一口气滚好几格。
eng_xp = fresh_engine()
t = 0.0
for i in range(20):
    eng_xp.update([mkhand(dy=-0.010 * i, gesture="POINT")], t)
    t += DT
for _ in range(10):
    eng_xp.update(two("OPEN_PALM", "OPEN_PALM"), t)
    t += DT
n = 0
for _ in range(3):
    out = eng_xp.update([mkhand(dy=-0.55, gesture="POINT")], t)
    n += len(out.continuous)
    t += DT
check("从双手切回单手不会一口气滚一大段", n == 0, f"({n} 格)")

print("== GestureEngine：长按进入第二层 ==")
eng_l = fresh_engine()
t, singles, longs = 0.0, 0, 0
for i in range(60):                       # 2 秒
    out = eng_l.update([mkhand(gesture="VICTORY")], t)
    for f in out.fired:
        if f.long:
            longs += 1
        else:
            singles += 1
    t += DT
check("举着 2 秒只单击一次", singles == 1, f"({singles} 次)")
check("举着 2 秒进入第二层一次", longs == 1, f"({longs} 次)")

eng_ln = fresh_engine(long_press=0)
t, longs = 0.0, 0
for i in range(60):
    out = eng_ln.update([mkhand(gesture="VICTORY")], t)
    longs += sum(1 for f in out.fired if f.long)
    t += DT
check("long_press=0 关掉长按", longs == 0, f"({longs} 次)")

# 玩家在游戏倒数那三秒本来就举着手，长按必须在游戏里彻底失效
eng_lg = fresh_engine()
eng_lg.set_game_mode(True)
t, longs = 0.0, 0
for i in range(60):
    out = eng_lg.update([mkhand(gesture="VICTORY")], t)
    longs += sum(1 for f in out.fired if f.long)
    t += DT
check("游戏模式里不判长按", longs == 0, f"({longs} 次)")

print("== GestureEngine：翻掌滚动 ==")
# 张开手掌 + 手腕转动让手掌上下翻 = 页面上下滚。
# 信号是小指根与食指根的**高度差**（单位掌长），往下翻时它变小。
eng_f = fresh_engine()
t, vals = 0.0, []
for i in range(13):
    out = eng_f.update([mkhand(gesture="OPEN_PALM", tilt=-0.70 * i / 12)], t)
    vals += [v for k, v in out.continuous if k == "scroll"]
    t += DT
check("往下翻会滚动页面", len(vals) >= 2, f"({len(vals)} 格)")
check("往下翻 -> 值为正（执行层取反后向下滚）", all(v > 0 for v in vals),
      f"({vals[:4]})")

eng_fb = fresh_engine()
t, vals = 0.0, []
for i in range(13):
    out = eng_fb.update(
        [mkhand(gesture="OPEN_PALM", tilt=-0.70 + 0.70 * i / 12)], t)
    vals += [v for k, v in out.continuous if k == "scroll"]
    t += DT
check("翻回来 -> 值为负（向上滚）", len(vals) >= 2 and all(v < 0 for v in vals),
      f"({vals[:4]})")

# 翻完手往往还张着看页面滚到哪。此时绝不能因为"张开手掌举了半秒"
# 就把"最大化"也执行了 —— 用户根本猜不到是自己翻掌翻出来的。
eng_fm = fresh_engine()
t, fired = 0.0, []
for i in range(13):
    out = eng_fm.update([mkhand(gesture="OPEN_PALM", tilt=-0.70 * i / 12)], t)
    fired += [f.gesture for f in out.fired]
    t += DT
for _ in range(45):                  # 翻完停在原位举 1.5 秒
    out = eng_fm.update([mkhand(gesture="OPEN_PALM", tilt=-0.70)], t)
    fired += [f.gesture for f in out.fired]
    t += DT
check("翻完不松手也不会顺手触发「张开手掌」的动作",
      "OPEN_PALM" not in fired, f"({fired})")

for _ in range(8):                   # 手离开画面 = 松手
    eng_fm.update([], t)
    t += DT
for _ in range(25):
    out = eng_fm.update([mkhand(gesture="OPEN_PALM")], t)
    fired += [f.gesture for f in out.fired]
    t += DT
check("松手再张开，悬停动作恢复正常", "OPEN_PALM" in fired, f"({fired})")

# 只有张开手掌才判：握拳翻腕不该滚动（否则做 ✊ 静音时会顺手滚一下）
eng_ff = fresh_engine()
t, n = 0.0, 0
for i in range(13):
    out = eng_ff.update([mkhand(gesture="FIST", tilt=-0.70 * i / 12)], t)
    n += len(out.continuous)
    t += DT
check("握拳翻腕不会滚动", n == 0, f"({n} 格)")

# 举手时手掌轻微来回晃是常态：不能滚、也不能触发悬停
eng_fj = fresh_engine()
t, n = 0.0, 0
for i in range(90):
    out = eng_fj.update(
        [mkhand(gesture="OPEN_PALM", tilt=-0.02 if i % 2 else -0.20)], t)
    n += len(out.continuous) + len(out.fired)
    t += DT
check("手掌小幅来回翻，既不滚也不触发悬停", n == 0, f"({n} 次)")

eng_fo = fresh_engine(flip_on=False)
t, n = 0.0, 0
for i in range(13):
    out = eng_fo.update([mkhand(gesture="OPEN_PALM", tilt=-0.70 * i / 12)], t)
    n += len(out.continuous)
    t += DT
check("flip_on=False 关掉翻掌", n == 0, f"({n} 格)")

eng_fg = fresh_engine()
eng_fg.set_game_mode(True)
t, n = 0.0, 0
for i in range(13):
    out = eng_fg.update([mkhand(gesture="OPEN_PALM", tilt=-0.70 * i / 12)], t)
    n += len(out.continuous)
    t += DT
check("游戏模式里不判翻掌", n == 0, f"({n} 格)")

# ---------------------------------------------------------------- 游戏模式
print("== GestureEngine：游戏模式（出拳要能动、要能重新武装）==")
# 出拳本来就是"动"出来的。桌面那条"手必须停住"的闸门如果不关，
# 玩家比着 ✊ 挥一下就永远出不了拳 —— 这是"点开游戏也玩不了"的直接原因。
eng_g = fresh_engine()
eng_g.set_game_mode(True)
t, n = 0.0, 0
for i in range(20):
    out = eng_g.update([mkhand(dx=0.004 * i, gesture="FIST")], t)
    n += len(out.fired)
    t += DT
check("游戏模式下手在动也能出拳", n >= 1, f"({n} 次)")

# 倒数 3 秒里玩家多半已经摆好手势了，那一下会被"确认器 fire 掉 + 游戏忽略"。
# 进入 "出！" 时必须 rearm，否则玩家举着手永远等不到第二次触发。
eng_r = fresh_engine()
eng_r.set_game_mode(True)
t, first = 0.0, []
for _ in range(20):                      # 举着 0.66s，足够触发一次
    out = eng_r.update([mkhand(gesture="FIST")], t)
    first += out.fired
    t += DT
check("倒数期间已经触发过一次（会被游戏吃掉）", len(first) == 1, f"({len(first)})")
eng_r.rearm()
second = []
for _ in range(20):                      # 重新投票 3 帧 + 悬停 0.30s，留足余量
    out = eng_r.update([mkhand(gesture="FIST")], t)
    second += out.fired
    t += DT
check("重新武装后举着的手势能再次确认", len(second) == 1, f"({len(second)})")

eng_g.set_game_mode(False)
check("退出游戏后确认时间还原", abs(eng_g.deb.hold - Config().hold) < 1e-6,
      f"({eng_g.deb.hold})")

# ---------------------------------------------------------------- 游戏端到端
print("== 石头剪刀布：从倒数到出拳（整条链路）==")
from pet.game.rps import RPSGame                                  # noqa: E402

game = RPSGame()
game.start(1000.0)
eng_play = fresh_engine()
eng_play.set_game_mode(True)
eng_play.set_game_hook(lambda gesture, now: game.play(gesture, now) is not None)

t = 1000.0
while game.phase == "countdown":           # 走完 3 秒倒数
    t += DT
    game.tick(t)
check("倒数结束后进入可以出拳的状态", game.phase == "armed", f"({game.phase})")

# 玩家在这一刻已经举着 ✊ 了 —— worker 进入 armed 时会 rearm，这里照样来一次
eng_play.rearm()
round_ = None
for _ in range(20):
    out = eng_play.update([mkhand(gesture="FIST")], t)
    for f in out.fired:
        eng_play._game_hook(f.gesture, t)
    round_ = game.round
    if round_ is not None:
        break
    t += DT
check("倒数结束后比 ✊ 真的出石头",
      round_ is not None and round_.player == "FIST",
      f"({round_.player if round_ else '-'})")
check("出一拳就有比分", round_ is not None and sum(round_.score) == 1,
      f"({round_.score if round_ else '-'})")

# ---------------------------------------------------------------- 双手（音乐）
print("== GestureEngine：双手同手势 -> 音乐 ==")
eng3 = fresh_engine()
t, fires = 0.0, []
for _ in range(30):
    out = eng3.update(two("VICTORY", "VICTORY"), t)
    fires += out.fired
    t += DT
check("双手剪刀手触发一次", len(fires) == 1, f"({len(fires)} 次)")
check("双手触发 side=both", fires and fires[0].side == "both", f"({fires[0].side if fires else '-'})")
check("双手触发的手势是 VICTORY", fires and fires[0].gesture == "VICTORY")

print("== GestureEngine：双手不一致什么都不做 ==")
eng4 = fresh_engine()
t, n = 0.0, 0
for _ in range(40):
    out = eng4.update(two("VICTORY", "OPEN_PALM"), t)
    n += len(out.fired)
    t += DT
check("左右手势不同 -> 不触发", n == 0, f"({n} 次)")

print("== GestureEngine：双手在场抑制单手指令 ==")
eng5 = fresh_engine()
t, n = 0.0, 0
for _ in range(40):
    out = eng5.update(two("VICTORY", "OPEN_PALM"), t)
    n += len(out.fired)
    t += DT
check("双手在场抑制单手指令", n == 0, f"({n} 次)")

print("== GestureEngine：双手捏合保持 = 总开关 ==")
eng6 = fresh_engine()
t, toggled = 0.0, None
for _ in range(40):                      # 40 帧 ≈ 1.33s > toggle_hold 1.0s
    out = eng6.update(two("PINCH", "PINCH", pinch=True), t)
    if out.toggled is not None:
        toggled = out.toggled
    t += DT
check("双手捏合保持 -> 关闭控制", toggled is False, f"(toggled={toggled})")
check("捏合瞬间不当作音乐手势",
      mapping.resolve("both", "PINCH") is None)

# ---------------------------------------------------------------- 双手滚动
print("== 双手张开上下移动 -> 滚动 ==")


def mk_hand(side: str, y: float, gesture: str = "OPEN_PALM") -> Hand:
    """造一只"张开的手"。关键是拇指尖和食指尖要拉远，否则几何会判成捏合。"""
    lm = np.zeros((21, 3))
    lm[:, 0] = 0.5
    lm[:, 1] = y
    lm[4] = [0.95, y, 0.0]
    lm[8] = [0.05, y, 0.0]
    return Hand(landmarks=lm, score=0.9, side=side, aspect=1.78,
                model_gesture=gesture, model_score=0.95)


def run_scroll(eng: GestureEngine, ys, t0: float = 1000.0):
    steps, fired = [], []
    for i, y in enumerate(ys):
        out = eng.update([mk_hand("left", y), mk_hand("right", y)], t0 + i * 0.05)
        steps += [v for k, v in out.continuous if k == "scroll"]
        fired += [f for f in out.fired if f.side == "both"]
    return steps, fired


eng = GestureEngine(Config())
steps, fired = run_scroll(eng, [0.60 - i * 0.02 for i in range(12)])
check("双手上移会产出滚动量", len(steps) > 0)
check("上移是负值（上层翻符号后=页面上滚）", all(v < 0 for v in steps))
# 这条是"滚动"能用的前提：边滚边触发播放/暂停，音乐就被顺手停了
check("滚动过程中不触发播放/暂停", not fired)

eng = GestureEngine(Config())
steps, _ = run_scroll(eng, [0.60 + i * 0.02 for i in range(12)])
check("双手下移产出反向滚动", len(steps) > 0 and all(v > 0 for v in steps))

# 张开**不动**仍然归"播放/暂停"：这是原有功能，不能被滚动抢走
eng = GestureEngine(Config())
steps, fired = run_scroll(eng, [0.50] * 15)
check("张开不动不产生滚动", not steps)
check("张开不动仍然触发配对（播放/暂停）", bool(fired))

# ---------------------------------------------------------------- 映射
print("== 映射表：单手默认=操作窗口 ==")
r = mapping.resolve("right", "POINT")
l = mapping.resolve("left", "POINT")
check("右手食指 -> 贴右半屏", r is not None and r.key == "win_snap_right", f"({r.key})")
check("左手食指 -> 贴左半屏（指哪边贴哪边）",
      l is not None and l.key == "win_snap_left", f"({l.key})")
mx = mapping.resolve("right", "OPEN_PALM")
check("张开手掌 -> 最大化/还原", mx is not None and mx.key == "win_max", f"({mx.key})")
fist = mapping.resolve("left", "FIST")
check("握拳 -> 最小化", fist is not None and fist.key == "win_min", f"({fist.key})")
vic = mapping.resolve("right", "VICTORY")
check("剪刀手 -> 切换窗口", vic is not None and vic.key == "alt_tab", f"({vic.key})")
thumb = mapping.resolve("right", "THUMB_UP")
check("点赞 -> 窗口置顶", thumb is not None and thumb.key == "win_topmost", f"({thumb.key})")

pat = mapping.resolve("right", "PINCH")
check("单手捏合只摸宠物（不碰系统）", pat is not None and pat.key.startswith("pet:"),
      f"({pat.key})")

print("== 单手预设：两条路都能走 ==")
check("当前预设是操作窗口", mapping.current_single_preset() == "window",
      f"({mapping.current_single_preset()})")
_w = mapping.preset_bindings("window")
_p = mapping.preset_bindings("pet")


def _hands(rows):
    """只取左右手的格子（长按键是单维的，不算在 14 格里）。"""
    return {k: v for k, v in rows.items() if k.split(":")[0] in ("left", "right")}


check("两套预设的左右手都是 14 条（7 手势 × 左右手）",
      len(_hands(_w)) == 14 and len(_hands(_p)) == 14,
      f"({len(_hands(_w))}/{len(_hands(_p))})")
check("两套预设都带长按（第二层）",
      any(k.startswith("long:") for k in _w)
      and any(k.startswith("long:") for k in _p))
check("两套预设动作都在目录里",
      all(v in mapping.ACTION_CATALOG for v in list(_w.values()) + list(_p.values())))

# 切到"逗桌宠"：整套生效，不用逐行改
mapping.apply_bindings(
    {k: v for k, v in _p.items() if _w.get(k) != v})
check("切到桌宠预设后 ✌️ 是跳舞",
      mapping.resolve("right", "VICTORY").key == "pet:dance")
check("切到桌宠预设后 ✋ 是唤醒",
      mapping.resolve("right", "OPEN_PALM").key == "pet:wake")
check("识别出当前用的是桌宠预设", mapping.current_single_preset() == "pet",
      f"({mapping.current_single_preset()})")
# 长按必须跟着预设走：窗口预设里长按 ✌️ 是"收起桌面"，但玩桌宠时
# 举着剪刀手看它跳舞超过 1.5 秒太正常了，那时收桌面就是纯误伤。
_lp = mapping.resolve("right", "VICTORY", long=True)
check("桌宠预设下长按 ✌️ 是抚摸，不是收桌面",
      _lp is not None and _lp.key == "pet:pat", f"({getattr(_lp, 'key', None)})")

# 老用户的配置里根本没有 long: 键（长按是后加的功能）。这时长按的默认值
# 必须跟着"单手表像哪套预设"走，否则玩着逗桌宠举着 ✌️ 看跳舞，
# 1.5 秒后桌面会被收掉 —— 这是最典型的误伤。
mapping.apply_bindings({
    "right:VICTORY": "pet:dance", "left:VICTORY": "pet:dance",
    "right:FIST": "pet:sleep", "left:FIST": "pet:sleep",
    "right:OPEN_PALM": "pet:wake", "left:OPEN_PALM": "pet:wake",
    "right:POINT": "pet:follow", "left:POINT": "pet:follow",
    "right:THUMB_UP": "open_browser", "left:THUMB_UP": "open_browser",
})
_lold = mapping.resolve("right", "VICTORY", long=True)
check("老配置没写长按键也跟逗宠预设走",
      _lold is not None and _lold.key == "pet:pat",
      f"({getattr(_lold, 'key', None)})")
mapping.apply_bindings({})
_ldef = mapping.resolve("right", "VICTORY", long=True)
check("恢复默认后长按回到窗口预设", _ldef is not None and _ldef.key == "win_desktop",
      f"({getattr(_ldef, 'key', None)})")

mapping.apply_bindings({})
check("切回默认后 ✌️ 又是切换窗口",
      mapping.resolve("right", "VICTORY").key == "alt_tab")
check("切回默认后 ✋ 又是最大化",
      mapping.resolve("right", "OPEN_PALM").key == "win_max")

print("== 映射表：双手=音乐 ==")
pp = mapping.resolve("both", "OPEN_PALM")
check("双手张开 -> 播放/暂停", pp is not None and pp.key == "play_pause", f"({pp.key})")
nt = mapping.resolve("both", "VICTORY")
check("双手剪刀手 -> 下一首", nt is not None and nt.key == "next_track", f"({nt.key})")
check("未映射的手势返回 None", mapping.resolve("right", "UNKNOWN") is None)

print("== 动态台词：动作结果决定说什么 ==")
b_max = mapping.window_bubble("win_max", "max")
b_res = mapping.window_bubble("win_max", "restore")
b_none = mapping.window_bubble("win_max", "no_window")
check("最大化与还原台词不同", b_max and b_res and b_max != b_res, f"({b_max} / {b_res})")
check("没窗口时给出提示", "窗口" in b_none, f"({b_none})")

# ---------------------------------------------------------------- 吸附到窗口
print("== 吸附：哪些窗口能贴、贴在哪 ==")
from pet.core import windowinfo                                   # noqa: E402
from pet.core.windowinfo import WindowInfo, dock_xy, short_title  # noqa: E402

# 桌宠尺寸取实测值：scale=1 时窗口 222x302，身体中心 (111, 202)，体宽 118.6
PW, PH, PCX, PCY, BW = 222.0, 302.0, 111.0, 202.0, 118.6
SCREEN = (0.0, 0.0, 1920.0, 1040.0)
PAD, OVERLAP = 4.0, 0.16
REACH = BW * (0.5 - OVERLAP)          # 身体往外让出的距离 ≈ 40.3

win = WindowInfo(1, "Chrome - 测试", 300, 100, 1000, 700, "normal")
check("窗口信息：宽高与中心", (win.width, win.height) == (700, 600)
      and win.center == (650.0, 400.0))
check("正常窗口可以贴", win.dockable)
for st in ("minimized", "maximized", "fullscreen"):
    check(f"{st} 不贴（没边/会挡内容）", not WindowInfo(1, "x", 0, 0, 1920, 1040, st).dockable)

x, y, side = dock_xy(win, PW, PH, PCX, PCY, SCREEN, body_w=BW,
                     pad=PAD, y_ratio=0.28)
check("默认贴窗口右边", side == "right", f"({side})")
check("右边：身体压住边界一点点",
      abs((x + PCX) - (win.right + PAD + REACH)) < 0.01, f"(x={x:.1f})")
check("竖直：身体中心在窗口高度 28% 处",
      abs(y - (win.top + win.height * 0.28 - PCY)) < 0.01, f"(y={y:.1f})")

x2, y2, side2 = dock_xy(WindowInfo(2, "x", 800, 100, 1912, 700, "normal"),
                        PW, PH, PCX, PCY, SCREEN, body_w=BW, pad=PAD)
check("窗口贴屏幕右缘 -> 自动翻到左边", side2 == "left"
      and x2 >= SCREEN[0] and x2 + PW <= SCREEN[2], f"({side2}, x={x2:.1f})")

x3, _, side3 = dock_xy(win, PW, PH, PCX, PCY, SCREEN, prefer="left")
check("可以指定优先站左边", side3 == "left", f"({side3})")

_, y4, _ = dock_xy(WindowInfo(3, "x", 300, 100, 1000, 390, "normal"),
                   PW, PH, PCX, PCY, (0.0, 0.0, 1920.0, 400.0))
check("屏幕矮时竖直夹在屏幕内", 0.0 <= y4 and y4 + PH <= 400.0, f"(y={y4:.1f})")

xa, _, _ = dock_xy(win, PW, PH, PCX, PCY, SCREEN)
xb, _, _ = dock_xy(win, PW, PH, PCX, PCY, SCREEN, body_w=BW)
check("没有立绘信息时退化成身体贴边（不压窗口）", xb > xa, f"({xa:.1f} -> {xb:.1f})")

check("标题截断加省略号", short_title("Chrome - 隔空手势桌宠 - GitHub", 12).endswith("…")
      and len(short_title("Chrome - 隔空手势桌宠 - GitHub", 12)) <= 12)
check("空标题有兜底", short_title("", 12) == "窗口")

real = windowinfo.query()
check("查前台窗口不抛异常", real is None or (real.right > real.left and real.state in
      ("normal", "maximized", "minimized", "fullscreen")),
      f"({real.state if real else 'None'})")

# ---------------------------------------------------------------- 配置
print("== 配置读写 ==")
cfg_path = os.path.join(tempfile.gettempdir(), "pet_cfg_test.json")
cfg = Config(pet_name="测试团子", hold=0.42, follow="cursor")
cfg.save(cfg_path)
back = Config.load(cfg_path)
check("round-trip 保留字段", back.pet_name == "测试团子" and abs(back.hold - 0.42) < 1e-9)
check("双手参数有默认值", back.pair_hold > 0 and back.toggle_hold > 0)
back.override(pet_name="覆盖后", follow=None)
check("override 跳过 None", back.pet_name == "覆盖后" and back.follow == "cursor")
try:
    os.remove(cfg_path)
except OSError:
    pass

# ---------------------------------------------------------------- 手势绑定
print("== 手势绑定（对照表页面）==")
mapping.apply_bindings({})
check("默认 右手✋ = 最大化", mapping.resolve("right", "OPEN_PALM").key == "win_max")
check("默认 双手✊ = 静音", mapping.resolve("both", "FIST").key == "mute")

mapping.apply_bindings({"right:OPEN_PALM": "pet:wake", "both:FIST": "play_pause"})
check("改绑 右手✋ 到宠物动作", mapping.resolve("right", "OPEN_PALM").key == "pet:wake")
check("改绑 双手✊", mapping.resolve("both", "FIST").key == "play_pause")
check("没改的 左手✋ 仍是默认", mapping.resolve("left", "OPEN_PALM").key == "win_max")
check("台词跟着动作走", "醒啦" in mapping.resolve("right", "OPEN_PALM").bubble)

# 左手有一条"没有就用右手"的 fallback，所以取消绑定必须能被真正关掉
mapping.apply_bindings({"left:VICTORY": ""})
check("取消绑定不会被右手继承", mapping.resolve("left", "VICTORY") is None)
check("取消后右手✌️ 照旧", mapping.resolve("right", "VICTORY").key == "alt_tab")

mapping.apply_bindings({"right:FIST": "根本没有这个动作"})
check("动作名拼错时保持默认", mapping.resolve("right", "FIST").key == "win_min")

mapping.apply_bindings({"both:FIST": ""})
check("双手也能取消", mapping.resolve("both", "FIST") is None)

mapping.apply_bindings({})
check("恢复默认", mapping.resolve("right", "OPEN_PALM").key == "win_max"
      and mapping.resolve("both", "FIST").key == "mute")

# 宠物动作没从目录里删掉，只是默认不绑——"两条路"的另一条随时能切过去
check("宠物动作仍可选用", "pet:dance" in mapping.ACTION_CATALOG
      and "pet:follow" in mapping.ACTION_CATALOG)

default_map = mapping.default_bindings()
from collections import Counter                                 # noqa: E402
_kinds = Counter(k.split(":")[0] for k in default_map)
check("默认含 14 个单手格子", _kinds["left"] + _kinds["right"] == 14,
      f"({_kinds['left'] + _kinds['right']})")
check("默认含 6 个双手格子", _kinds["both"] == 6, f"({_kinds['both']})")
check("默认带长按与挥手", _kinds["long"] >= 1 and _kinds["swipe"] == 2,
      f"(long {_kinds['long']} / swipe {_kinds['swipe']})")
check("默认动作都在目录里", all(v in mapping.ACTION_CATALOG for v in default_map.values()))
check("目录带中文名", mapping.describe_action("win_max") == "最大化 / 还原"
      and mapping.describe_action("") == "不绑定")

bind_path = os.path.join(tempfile.gettempdir(), "pet_bind_test.json")
Config(bindings={"right:OPEN_PALM": "win_min"}).save(bind_path)
check("bindings 能存能读",
      Config.load(bind_path).bindings.get("right:OPEN_PALM") == "win_min")
try:
    os.remove(bind_path)
except OSError:
    pass

check("跟鼠标有安全距离参数", Config().cursor_trail >= 80)
check("吸附默认正面朝前", Config().dock_face_front is True)

# ---------------------------------------------------------------- 表情与动作
# 动作系统是纯数据 + 纯函数（anim.py 不 import Qt），所以能在这里全测掉。
print("== 表情与动作 ==")
from pet.ui import anim                                        # noqa: E402

FRAME_NAMES = {"stand", "happy", "wave", "think", "sleepy", "angry", "sit"}

check("每个情绪都能落到一张立绘上",
      all(anim.frame_for_mood(m) in FRAME_NAMES for m in anim.MOOD_FRAME))
check("没见过的情绪有兜底", anim.frame_for_mood("不存在的情绪") == anim.FALLBACK_FRAME)
check("害羞用抱胸那张（比笑更像害羞）", anim.frame_for_mood("shy") == "think")
check("惊讶用举手那张", anim.frame_for_mood("surprised") == "wave")

bad = []
for name, c in anim.CLIPS.items():
    ts = [f.t for f in c.frames]
    if c.dur <= 0:
        bad.append(f"{name}:时长")
    if not ts or abs(ts[0]) > 1e-9 or abs(ts[-1] - 1.0) > 1e-9:
        bad.append(f"{name}:首尾")
    if any(b <= a for a, b in zip(ts, ts[1:])):
        bad.append(f"{name}:时间不倒序")
    if any(f.frame and f.frame not in FRAME_NAMES for f in c.frames):
        bad.append(f"{name}:帧名")
check("所有动作的关键帧都合法（首尾 0/1、时间递增、帧名存在）", not bad, str(bad[:3]))
check("动作库里至少有 10 个动作", len(anim.CLIPS) >= 10, f"({len(anim.CLIPS)})")

# "动作看不见"是这个项目返工最多的一类 bug：位移只有 2~3px、又不换立绘，
# 靠肉眼根本判断不出有没有播。这条把门槛写死：要么换帧，要么位移够大。
dim = []
for name, c in anim.CLIPS.items():
    move = max(max(abs(f.dy), abs(f.dx)) for f in c.frames)
    frames = {f.frame for f in c.frames if f.frame}
    if move < 0.030 and not frames:
        dim.append(f"{name}({move:.3f})")
check("每个动作都看得见（换帧 或 位移≥3% 身高）", not dim, str(dim))

p = anim.AnimPlayer()
check("动作名拼错不会播", p.play("没有这个动作", 0.0) is False and not p.active)

p.play("hop", 0.0)
m0 = p.sample(0.0)
check("起手是原样（不动）", m0 is not None and abs(m0.dy) < 1e-9 and abs(m0.sx - 1) < 1e-9)
m_mid = p.sample(0.58 * 0.32)
check("跳到最高点时向上位移", m_mid is not None and m_mid.dy < -0.08, f"(dy={m_mid.dy:.3f})")
check("跳起来时被拉长", m_mid.sy > 1.0 and m_mid.sx < 1.0)
m_land = p.sample(0.58 * 0.80)
check("落地时压扁", m_land is not None and m_land.sy < 1.0 and m_land.sx > 1.0)
check("播完返回 None", p.sample(0.60) is None and not p.active)

p.stop()
p.play("wave", 0.0)
check("招手会换成 wave 帧", p.sample(1.30 * 0.30).frame == "wave")
p.stop()
p.play("sit_down", 0.0)
check("坐下最后停在 sit 帧", p.sample(0.55 * 0.90).frame == "sit")
check("坐下播到一半不重播（独占）", p.play("sit_down", 0.10) is False)
check("强推可以重播", p.play("sit_down", 0.10, force=True) is True)
check("换个动作可以随时打断", p.play("stand_up", 0.12) is True)
p3 = anim.AnimPlayer()
check("没在播的时候进度是 0 且不叠加变换",
      p3.progress(9.9) == 0.0 and p3.sample(9.9) is None)
p2 = anim.AnimPlayer()
p2.play("nod", 0.0, speed=2.0)
check("二倍速让 0.9 秒的动作在 0.225 秒走完一半",
      abs(p2.progress(0.225) - 0.5) < 0.01, f"(p={p2.progress(0.225):.3f})")

# ---------------------------------------------------------------- 配置迁移
# 换形象最容易踩的坑：用户机器上的 pet_config.json 写死了旧立绘路径，
# 新版默认值永远轮不到生效 —— 打开还是老形象，看着像"根本没更新"。
print("== 配置迁移（换新形象）==")
CHECK_VERSION = Config().config_version
mig_path = os.path.join(tempfile.gettempdir(), "pet_cfg_migrate.json")


def write_cfg(data):
    with open(mig_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


write_cfg({"sprite": "assets/whale_front.png", "sprite_side": "assets/whale_side.png",
           "pet_name": "小鲸", "pet_scale": 1.0, "follow": "cursor"})
mig = Config.load(mig_path)
check("旧立绘被换成新形象", mig.sprite == Config().sprite, mig.sprite)
check("旧侧视图被清掉（留着是坏路径）", mig.sprite_side == "")
check("旧名字升到新默认", mig.pet_name == Config().pet_name)
check("旧体型升到新默认", abs(mig.pet_scale - Config().pet_scale) < 1e-9)
check("用户改过的跟随方式没被动", mig.follow == "cursor")
check("版本号写回磁盘", mig.config_version == CHECK_VERSION)

write_cfg({"sprite": "assets/whale_front.png", "pet_name": "小鲸"})
Config.load(mig_path)
with open(mig_path, "r", encoding="utf-8") as f:
    check("迁移结果已回存到文件", json.load(f)["sprite"] == Config().sprite)

write_cfg({"sprite": "assets/girl/wave.png", "pet_name": "阿黄", "pet_scale": 1.3,
           "config_version": 1})
custom = Config.load(mig_path)
check("用户自己选的形象绝不覆盖", custom.sprite == "assets/girl/wave.png")
check("用户自己起的名字绝不覆盖", custom.pet_name == "阿黄")
check("用户自己调的体型绝不覆盖", abs(custom.pet_scale - 1.3) < 1e-9)
check("但版本号照样升级", custom.config_version == CHECK_VERSION)

write_cfg({"config_version": CHECK_VERSION, "sprite": "assets/girl/sit.png"})
v2 = Config.load(mig_path)
check("已经是最新版本就什么都不动", v2.sprite == "assets/girl/sit.png")

write_cfg({"sprite": ""})
check("空立绘（矢量版用户）也会被升级", Config.load(mig_path).sprite == Config().sprite)

try:
    os.remove(mig_path)
except OSError:
    pass

print(f"\n[RESULT] {len(PASS)} 项全部通过")
