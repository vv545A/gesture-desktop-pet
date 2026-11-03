# -*- coding: utf-8 -*-
"""复现用户报的 6 个现象：用假时钟驱动 _tick，不依赖真摄像头。"""
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

app = QApplication([])

from pet.core.bus import EventBus
from pet.core.config import Config
from pet.core.events import HandsSeen, PetSay
from pet.ui import window as W


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t


clock = Clock()
W.time = clock                     # window.py 里的 time.monotonic 全部走假钟


class Ctl:
    enabled = True


cfg = Config.load("pet_config.json")
w = W.PetWindow(EventBus(), cfg, control=Ctl())
w.resize(w.win_w, w.win_h)
w._bubble_t0 = clock.t


def step(n, hands=0.0, hz=20.0):
    """推进 n 秒；每 1/hz 秒来一条 HandsSeen(count=0)（真机常态）。"""
    out = []
    dt = 1 / 60
    nxt = 0.0
    for i in range(int(n * 60)):
        clock.t += dt
        if hands and clock.t >= nxt:
            nxt = clock.t + 1 / hz
            w._on_hands(HandsSeen(count=0))
        w._tick()
        m = w._motion
        out.append((round(clock.t - 1000, 2), w._mood, w._frame,
                    w._anim.name, None if m is None else (round(m.dy, 3), round(m.sy, 3)),
                    w._sit, w._yawned))
    return out


def summarise(tag, rows):
    seen = []
    for r in rows:
        key = (r[1], r[2], r[3], r[5])
        if not seen or seen[-1][1] != key:
            seen.append((r[0], key))
    print(f"\n== {tag} ==")
    for t, k in seen:
        print(f"  t={t:6.2f}  mood={k[0]:9s} frame={k[1]:7s} anim={k[2] or '-':9s} sit={k[3]}")


print("base frames:", sorted(w._sprite.frames), "body_h", w.body_h)

# 1) 待机 12 秒（摄像头开着，一直没手）—— 该有哈欠
rows = step(12, hands=1)
summarise("待机 12s（摄像头开着，无手）", rows)

# 2) 待机中点它一下
w.pet_me()
rows = step(1.5, hands=1)
print("\n== 待机中戳一下 ==")
print("  " + " | ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}" for r in rows[::12]))

# 3) 保存对照表（PetSay anim=nod）
w._on_say(PetSay(text="对照表改好啦", mood="happy", anim="nod"))
rows = step(1.2, hands=1)
print("\n== 保存对照表 ==")
print("  " + " | ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}" for r in rows[::10]))

# 4) 拖动惊吓
w._anim.play("startle", clock.t)
rows = step(0.6, hands=1)
print("\n== 拖动 startle ==")
print("  " + " | ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}/{r[4]}" for r in rows[::6]))

# 5) 长待机 50 秒 —— 该坐下；再久该伸懒腰
rows = step(85, hands=1)
summarise("长待机 85s", rows)

print("\n== 连戳升级 ==")
w._pats.clear(); w._sit = False; w._mood = "idle"
seq = []
for i in range(6):
    clock.t += 0.15
    w.pet_me()
    for _ in range(6):
        clock.t += 1 / 60
        w._tick()
    seq.append((i + 1, w._anim.name, w._mood, w._frame))
print("  " + " | ".join(f"{n}下:{a}/{m}/{f}" for n, a, m, f in seq))

print("\n== 坐下之后戳一下 ==")
w._pats.clear()
w._sit = True; w._mood = "sit"; w._mood_until = 1e18
w._frame = "sit"
w.pet_me()
rows = step(0.6, hands=1)
print("  " + " ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}/sit={r[5]}" for r in rows[::9]))

print("\n== 手离开 8 秒后回来 ==")
w._last_hand = clock.t - 8.0
w._hand_seen = False
clock.t += 1 / 60
w._on_hands(HandsSeen(count=1, point=(0.5, 0.5), progress=0.0))
rows = step(1.6, hands=1)
print("  " + " ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}" for r in rows[::15]))

print("\n== 手势识别关闭状态 ==")
from pet.core.events import ControlToggled
w._on_toggle(ControlToggled(enabled=False))
rows = step(3, hands=0)
print("  " + " ".join(f"{r[0]}:{r[1]}/{r[2]}/{r[3]}" for r in rows[::60]))
