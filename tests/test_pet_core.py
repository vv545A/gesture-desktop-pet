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

THUMB_TIP, INDEX_TIP = 4, 8


def check(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        raise AssertionError(name)


def mkhand(dx=0.0, dy=0.0, side="right", gesture="", score=0.9, pinch=False):
    lm = np.zeros((21, 3), np.float32)
    for i, (x, y) in enumerate(BASE):
        lm[i] = (x + dx, y + dy, 0)
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

# ---------------------------------------------------------------- 映射
print("== 映射表：单手=宠物互动 ==")
r = mapping.resolve("right", "POINT")
l = mapping.resolve("left", "POINT")
check("食指 -> 跟随鼠标", r is not None and r.key == "pet:follow", f"({r.key})")
check("左手食指同样跟随", l is not None and l.key == "pet:follow", f"({l.key})")
mx = mapping.resolve("right", "OPEN_PALM")
check("张开手掌 -> 唤醒", mx is not None and mx.key == "pet:wake", f"({mx.key})")
fist = mapping.resolve("left", "FIST")
check("握拳 -> 睡觉", fist is not None and fist.key == "pet:sleep", f"({fist.key})")
vic = mapping.resolve("right", "VICTORY")
check("剪刀手 -> 跳舞", vic is not None and vic.key == "pet:dance", f"({vic.key})")
thumb = mapping.resolve("right", "THUMB_UP")
check("点赞 -> 打开浏览器（默认表唯一带系统副作用的手势）",
      thumb is not None and thumb.key == "open_browser", f"({thumb.key})")

# 这条是产品红线：三个纯宠物手势不该牵动任何系统状态，
# 将来谁手滑把 pet:sleep 换成 win_min，这里要立刻红。
_pet_only = ("VICTORY", "FIST", "OPEN_PALM", "POINT")
check("宠物互动手势不碰系统",
      all(mapping.resolve(s, g).key.startswith("pet:")
          for s in ("left", "right") for g in _pet_only))

pat = mapping.resolve("right", "PINCH")
check("单手捏合只摸宠物（不碰系统）", pat is not None and pat.key.startswith("pet:"),
      f"({pat.key})")

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
check("默认 右手✋ = 唤醒", mapping.resolve("right", "OPEN_PALM").key == "pet:wake")
check("默认 双手✊ = 静音", mapping.resolve("both", "FIST").key == "mute")

mapping.apply_bindings({"right:OPEN_PALM": "win_min", "both:FIST": "play_pause"})
check("改绑 右手✋ 到窗口动作", mapping.resolve("right", "OPEN_PALM").key == "win_min")
check("改绑 双手✊", mapping.resolve("both", "FIST").key == "play_pause")
check("没改的 左手✋ 仍是默认", mapping.resolve("left", "OPEN_PALM").key == "pet:wake")
check("台词跟着动作走", "收起来" in mapping.resolve("right", "OPEN_PALM").bubble)

# 左手有一条"没有就用右手"的 fallback，所以取消绑定必须能被真正关掉
mapping.apply_bindings({"left:VICTORY": ""})
check("取消绑定不会被右手继承", mapping.resolve("left", "VICTORY") is None)
check("取消后右手✌️ 照旧", mapping.resolve("right", "VICTORY").key == "pet:dance")

mapping.apply_bindings({"right:FIST": "根本没有这个动作"})
check("动作名拼错时保持默认", mapping.resolve("right", "FIST").key == "pet:sleep")

mapping.apply_bindings({"both:FIST": ""})
check("双手也能取消", mapping.resolve("both", "FIST") is None)

mapping.apply_bindings({})
check("恢复默认", mapping.resolve("right", "OPEN_PALM").key == "pet:wake"
      and mapping.resolve("both", "FIST").key == "mute")

# 窗口动作没从目录里删掉，只是默认不绑——用户随时能改回去
check("窗口动作仍可选用", "win_max" in mapping.ACTION_CATALOG
      and "win_snap_left" in mapping.ACTION_CATALOG)

default_map = mapping.default_bindings()
check("默认共 20 条（14 单手 + 6 双手）", len(default_map) == 20)
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

print(f"\n[RESULT] {len(PASS)} 项全部通过")
