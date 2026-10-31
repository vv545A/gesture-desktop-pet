# -*- coding: utf-8 -*-
"""
手势自测 / 校准工具。

流程：准备 → 动作 A 保持 → **缓冲（放松，不计数据）** → 动作 B 保持 → ...
缓冲段的意义：切换动作时手会经过中间姿态，那些帧会被记到上一个动作名下，
把统计彻底污染（之前就被这个坑过，握拳的"误判"其实是切换瞬间的帧）。

用法：
    python calibrate.py                          # 默认测 握拳/点赞/捏合/张开
    python calibrate.py --poses FIST,THUMB_UP    # 只测两个
    python calibrate.py --hold 4 --buffer 2      # 放慢
    python calibrate.py --no-window              # 不开窗口（只跑逻辑，调试用）

结果：
    终端打印每个动作被识别成什么（分布百分比）
    原始关键点存到 calibration/ 下，之后调阈值可以直接离线复算，不用再让人重做动作
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

import cv2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from hand_tracker import HandTracker                  # noqa: E402
from gestures import recognize, fingers_up, pinch_ratio  # noqa: E402
from main import TextCache, put_cn                    # noqa: E402

DEFAULT_MODEL = os.path.join(BASE_DIR, "models", "hand_landmarker.task")
DEFAULT_GESTURE_MODEL = os.path.join(BASE_DIR, "models", "gesture_recognizer.task")

# 动作名 -> 屏幕提示
POSE_HINT = {
    "FIST": "握拳（拇指收进拳内）",
    "THUMB_UP": "点赞（拇指竖直翘起）",
    "THUMB_DOWN": "踩（拇指朝下）",
    "PINCH": "拇指食指捏合",
    "OPEN_PALM": "张开手掌",
    "VICTORY": "剪刀手",
    "POINT": "只伸食指",
}
POSE_ORDER = ["FIST", "THUMB_UP", "PINCH", "OPEN_PALM", "VICTORY", "POINT", "THUMB_DOWN"]


def parse_args():
    p = argparse.ArgumentParser(description="手势自测：动作之间带缓冲段")
    p.add_argument("--poses", default="FIST,THUMB_UP,PINCH,OPEN_PALM",
                   help=f"要测的动作，逗号分隔（可选：{','.join(POSE_ORDER)}）")
    p.add_argument("--hold", type=float, default=3.5, help="每个动作保持几秒")
    p.add_argument("--buffer", type=float, default=1.0,
                   help="两个动作之间的休息秒数（放松手，不记录数据，默认 1）")
    p.add_argument("--prep", type=float, default=4.0, help="开始前的准备秒数")
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--no-window", action="store_true", help="不开窗口（无头自检）")
    p.add_argument("--out", default=None, help="结果 JSON 路径（默认 calibration/时间戳.json）")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    poses = [x.strip().upper() for x in args.poses.split(",") if x.strip()]
    bad = [x for x in poses if x not in POSE_HINT]
    if bad:
        print(f"[error] 未知动作: {bad}\n可选: {POSE_ORDER}")
        return 1

    tracker = HandTracker(DEFAULT_MODEL, num_hands=1,
                          gesture_model=DEFAULT_GESTURE_MODEL)
    print(f"[backend] {'手势模型' if getattr(tracker, 'use_model', False) else '几何规则'}")

    cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    if not cap.isOpened():
        print("[error] 打不开摄像头")
        return 1

    tc = TextCache(22)
    win = "Calibration"
    if not args.no_window:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 720, 405)

    # 阶段表：(类型, 屏幕提示, 时长, 这个阶段属于哪个动作)
    phases = [("prep", "准备：把手抬到镜头前", args.prep, None)]
    for i, pose in enumerate(poses):
        phases.append(("pose", f"{i + 1}/{len(poses)}  {POSE_HINT[pose]}", args.hold, pose))
        if i < len(poses) - 1:      # 最后一个动作后面不用缓冲
            phases.append(("buffer", "缓冲：放松手，准备下一个", args.buffer, None))

    rows = []
    ts = 0
    idx = 0                      # 注意别从 -1 起：phases[-1] 会取到最后一段
    SETTLE = 0.5                 # 动作摆好后先等 0.5 秒再记数据，避开手移入画面的过渡帧
    t0 = time.perf_counter()
    print(f"[plan] 共 {len(phases)} 段，预计 {sum(p[2] for p in phases):.1f} 秒")
    print(f"[{phases[0][0]}] {phases[0][1]}")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[warn] 读帧失败")
            break
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        ts += 33
        now = time.perf_counter()
        elapsed = now - t0

        if elapsed >= phases[idx][2]:
            idx += 1
            if idx >= len(phases):
                break
            t0 = now
            elapsed = 0.0
            print(f"[{phases[idx][0]}] {phases[idx][1]}")

        kind, label, dur, cur_pose = phases[idx]
        left = dur - elapsed

        hands = tracker.detect(frame, ts)
        # 只有 pose 段才记数据；缓冲段（两个动作之间的休息）什么都不记。
        # 逐帧记录，起步 0.5 秒不计（手还在移入画面，那些帧不代表姿势）。
        if kind == "pose" and hands and elapsed >= SETTLE:
            hd = hands[0]
            rows.append(dict(pose=cur_pose, t=round(elapsed, 2),
                             pred=recognize(hd),
                             model=hd.model_gesture, model_score=round(hd.model_score, 3),
                             pattern=list(fingers_up(hd)),
                             pinch=round(pinch_ratio(hd), 3),
                             aspect=round(w / h, 4),
                             lm=hd.landmarks.tolist()))

        # ---------------- 绘制 ----------------
        vis = frame
        color = {"prep": (0, 220, 255), "pose": (0, 255, 120), "buffer": (150, 150, 150)}[kind]
        cv2.rectangle(vis, (0, 0), (w, 84), (28, 28, 28), -1)
        put_cn(vis, label, 16, 10, tc, color)
        cv2.putText(vis, f"{left:4.1f}s", (16, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (210, 210, 210), 2)
        if kind == "buffer":
            put_cn(vis, "（这段不计数据）", w // 2 - 90, 10, tc, (150, 150, 150))
        if hands:
            for i in range(21):
                cv2.circle(vis, hands[0].px(i, w, h), 3, (0, 255, 0), -1)
            put_cn(vis, f"识别：{recognize(hands[0])}", 16, h - 60, tc, (0, 200, 255))

        if not args.no_window:
            cv2.imshow(win, vis)
            if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break

    cap.release()
    if not args.no_window:
        cv2.destroyAllWindows()
    tracker.close()

    # ---------------- 统计 ----------------
    by_pose = defaultdict(list)
    for r in rows:
        by_pose[r["pose"]].append(r)

    print("\n=== 结果 ===")
    print(f"{'动作':<12}{'帧数':>6}   识别分布")
    ok_total = n_total = 0
    for pose in poses:
        items = by_pose.get(pose, [])
        if not items:
            print(f"{pose:<12}{0:>6}   （无数据：手没被检测到？）")
            continue
        c = Counter(r["pred"] for r in items)
        top = ", ".join(f"{k} {v / len(items) * 100:.0f}%" for k, v in c.most_common(4))
        acc = c.get(pose, 0) / len(items) * 100
        ok_total += c.get(pose, 0)
        n_total += len(items)
        print(f"{pose:<12}{len(items):>6}   {top}     [正确 {acc:.0f}%]")
    if n_total:
        print(f"\n整体正确率 {ok_total / n_total * 100:.1f}%（{n_total} 帧）")

    out = args.out or os.path.join(
        BASE_DIR, "calibration", datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"poses": poses, "hold": args.hold, "buffer": args.buffer, "rows": rows}, f)
    print(f"[saved] {out}    （原始关键点已存，调阈值不用重做动作）")
    return 0


if __name__ == "__main__":
    _rc = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(_rc)      # 同上：别让 mediapipe 的原生收尾把进程吊住
