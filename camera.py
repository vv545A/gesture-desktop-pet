# -*- coding: utf-8 -*-
"""
摄像头打开 —— 手势控制（main.py）和桌宠（pet/）共用。

单独抽出来的原因：这里面全是"踩坑换来的经验"，两个程序各写一份必然会分叉。
主程序对着 HUD 调好的后端、分辨率、超时，桌宠必须一模一样，否则同一台机器上
会出现"命令行能跑、桌宠打不开摄像头"这种最难查的问题。
"""

from __future__ import annotations

import time

import cv2


def open_camera(index: int, w: int = 1280, h: int = 720):
    """
    按「后端 × 分辨率」逐个尝试，**实测帧率达标才用**。

    实测这台机器：MSMF@720p 正常 30FPS，但偶尔会打不开；一旦降级到
    DSHOW@720p 就只有 1–10 FPS，等于程序废掉。所以不能只看"能打开"，
    还要量一下真实帧率；DSHOW 则退到 640x480 才跑得动。

    返回 cv2.VideoCapture，或 None（全部组合都失败）。
    """
    cands = [
        (cv2.CAP_MSMF, "MSMF", w, h),
        (cv2.CAP_MSMF, "MSMF", 640, 480),
        (cv2.CAP_DSHOW, "DSHOW", 640, 480),
        (cv2.CAP_DSHOW, "DSHOW", w, h),
    ]
    for be, name, cw, ch in cands:
        cap = cv2.VideoCapture()
        # 超时必须在 open 之前设：摄像头被别的程序占用时，read() 会一直阻塞，
        # 进程既不报错也不退出，看着就像"卡死 + 占着内存"。
        # MSMF 认这两个属性，DSHOW 不认（设了也无害）。
        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
        except Exception:
            pass
        cap.open(index, be)
        if not cap.isOpened():
            cap.release()
            print(f"[camera] {name} {cw}x{ch}: 打开失败")
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cw)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ch)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, _ = cap.read()
        if not ok:
            cap.release()
            print(f"[camera] {name} {cw}x{ch}: 读帧失败")
            continue

        for _ in range(3):      # 丢掉启动瞬间的几帧
            cap.read()
        t0 = time.perf_counter()
        n = 10
        for _ in range(n):
            if not cap.read()[0]:
                break
        fps = n / max(1e-6, time.perf_counter() - t0)

        aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if fps >= 8.0:
            print(f"[camera] {name} {aw}x{ah} @ {fps:.1f} FPS")
            return cap
        print(f"[camera] {name} {aw}x{ah} 只有 {fps:.1f} FPS，换下一个组合")
        cap.release()
    return None
