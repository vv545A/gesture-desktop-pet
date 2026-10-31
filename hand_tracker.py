# -*- coding: utf-8 -*-
"""
MediaPipe HandLandmarker 封装 —— 双手 21 关键点检测。

重要：mediapipe 1.x 已移除旧的 ``mp.solutions.hands`` API，只能用 ``mp.tasks``。
本模块用 VIDEO 模式（同步、时间戳单调），结果与帧严格对齐，比 LIVE_STREAM
的异步回调更适合做实时控制（异步结果会滞后一帧以上，控制类应用受不了）。

左右手判定：**不信 mediapipe 的 handedness 标签**。摄像头画面是镜像的，
它判的是「图像里的手」而非「你的手」，标签经常反。这里直接按 x 坐标排序：
画面左侧 = 左手，右侧 = 右手。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


# ------------------------------------------------------------ 21 关键点索引
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

# 骨架连线（用于可视化）
HAND_CONNECTIONS = [
    (WRIST, THUMB_CMC), (THUMB_CMC, THUMB_MCP), (THUMB_MCP, THUMB_IP), (THUMB_IP, THUMB_TIP),
    (WRIST, INDEX_MCP), (INDEX_MCP, INDEX_PIP), (INDEX_PIP, INDEX_DIP), (INDEX_DIP, INDEX_TIP),
    (WRIST, MIDDLE_MCP), (MIDDLE_MCP, MIDDLE_PIP), (MIDDLE_PIP, MIDDLE_DIP), (MIDDLE_DIP, MIDDLE_TIP),
    (WRIST, RING_MCP), (RING_MCP, RING_PIP), (RING_PIP, RING_DIP), (RING_DIP, RING_TIP),
    (WRIST, PINKY_MCP), (PINKY_MCP, PINKY_PIP), (PINKY_PIP, PINKY_DIP), (PINKY_DIP, PINKY_TIP),
    (INDEX_MCP, MIDDLE_MCP), (MIDDLE_MCP, RING_MCP), (RING_MCP, PINKY_MCP),
]


@dataclass
class Hand:
    """一只手的检测结果。landmarks 为 (21, 3) 归一化坐标，x/y ∈ [0,1]。"""

    landmarks: np.ndarray
    score: float
    side: str = ""            # "left" / "right"（最终判定，见 assign_sides）
    mp_side: str = ""         # MediaPipe handedness 换算出的左右（可能为空）
    mp_score: float = 0.0     # 上面那个判定的置信度
    side_source: str = ""     # 这个 side 是怎么来的："model" / "model+pos" / "position"
    aspect: float = 1.0       # 帧的宽高比（w/h）。归一化坐标 x/y 不同尺度，
                              # 算几何前必须乘上去，否则 16:9 画面横向被压 1.78 倍
    model_gesture: str = ""   # 手势模型给的类别（已换成项目内部名字，空=模型没给）
    model_score: float = 0.0  # 上面那个类别的置信度

    def px(self, idx: int, w: int, h: int):
        """把归一化坐标转成像素坐标。"""
        lm = self.landmarks[idx]
        return int(lm[0] * w), int(lm[1] * h)

    @property
    def center(self) -> np.ndarray:
        """掌心中心（归一化坐标）。"""
        pts = self.landmarks[[WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP]][:, :2]
        return pts.mean(axis=0)

    @property
    def center_px(self):
        return self.center

    def palm_size(self) -> float:
        """腕部到中指根的距离，作为尺度归一化基准（手离摄像头远近不影响判断）。"""
        d = np.linalg.norm(self.landmarks[MIDDLE_MCP][:2] - self.landmarks[WRIST][:2])
        return float(max(d, 1e-6))

    def bbox_px(self, w: int, h: int) -> Tuple[int, int, int, int]:
        """21 个关键点的像素外接框 (x0, y0, x1, y1)。"""
        xs = self.landmarks[:, 0] * w
        ys = self.landmarks[:, 1] * h
        return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


MP_SIDE = {"Left": "left", "Right": "right"}

# 手势模型类别 -> 项目内部手势名。
# 模型的类别是先验固定的（见官方文档）：None / Closed_Fist / Open_Palm /
# Pointing_Up / Thumb_Down / Thumb_Up / Victory / ILoveYou。
# 注意它**没有「捏合」**，捏合仍然靠几何判据（is_pinch）。
MODEL_GESTURE = {
    "Closed_Fist": "FIST",
    "Open_Palm": "OPEN_PALM",
    "Pointing_Up": "POINT",
    "Thumb_Down": "THUMB_DOWN",
    "Thumb_Up": "THUMB_UP",
    "Victory": "VICTORY",
    "ILoveYou": "UNKNOWN",
    "None": "",
}

# 左右判定来源统计（--trace 用）
SIDE_STATS = {"model": 0, "model+pos": 0, "position": 0, "disagree": 0}


def assign_sides(
    hands: List[Hand],
    mode: str = "auto",
    swap: bool = False,
    mp_min: float = 0.75,
) -> List[Hand]:
    """
    判定每只手是左手还是右手，结果写进 ``hand.side``。

    两个信号：
        位置   —— 画面**已经镜像**（main 里先 cv2.flip 再检测），所以画面左边
                  就是用户的左手。双手不交叉时这是几何事实，不会错。
        模型   —— MediaPipe 的 handedness 标签。它按"自拍（已镜像）"的约定
                  训练，所以喂镜像画面时理论上直接可用；但实测分数波动很大
                  （同一张图 0.57 也出现过），单独用不可靠。

    模式：
        auto      位置为准；模型高置信且与位置一致时标成 "model+pos"（更可信），
                  不一致时仍按位置，并计一次 disagree —— 用统计去发现"模型反了"
        position  只用位置
        model     只用模型（双手交叉、位置失效时用这个）

    为什么默认不是 model：位置在镜像画面里是几何事实，模型是概率，
    出错的方向还不可预测。宁可少用一个信号，也不要让左右随机翻转。
    """
    if not hands:
        return hands

    hands.sort(key=lambda h: float(h.center[0]))

    # 位置先验（最多两只手）：左边是左手
    if len(hands) == 1:
        pos = ["left" if hands[0].center[0] < 0.5 else "right"]
    else:
        pos = ["left", "right"][: len(hands)]

    mp = [h.mp_side for h in hands]
    mp_ok = (all(mp) and len(set(mp)) == len(mp)
             and all(h.mp_score >= mp_min for h in hands))

    if mode == "position" or not mp_ok:
        sides, src = pos, "position"
    elif mode == "model":
        sides, src = mp, "model"
    else:                                   # auto
        if mp == pos:
            sides, src = mp, "model+pos"
        else:
            SIDE_STATS["disagree"] += 1
            sides, src = pos, "position"

    if swap:
        sides = ["right" if s == "left" else "left" for s in sides]

    for h, s in zip(hands, sides):
        h.side, h.side_source = s, src
    SIDE_STATS[src] = SIDE_STATS.get(src, 0) + 1
    return hands


class HandTracker:
    """双手关键点检测器。

    两种识别后端：
        手势模型（默认）  官方 GestureRecognizer，一次搞定「关键点 + 手势类别」。
                          它认得 Closed_Fist / Thumb_Up / Thumb_Down / Open_Palm /
                          Pointing_Up / Victory，是训练过的分类器，比自己写的几何
                          规则稳得多（握拳 vs 点赞这种几何上几乎一样的姿势，手工
                          规则最多只能做到 69~76%）。
        几何规则          没有模型文件 / 加 --no-gesture-model 时退回老路。
    """

    def __init__(
        self,
        model_path: str,
        num_hands: int = 2,
        min_detect: float = 0.5,
        min_presence: float = 0.5,
        min_tracking: float = 0.5,
        hand_mode: str = "auto",
        swap_hands: bool = False,
        gesture_model: Optional[str] = None,
    ) -> None:
        self.hand_mode = hand_mode
        self.swap_hands = swap_hands
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"手部模型不存在: {model_path}")

        # mediapipe 的 C++ 层用窄字符 API 打开文件，遇到中文路径会报
        # FileNotFoundError（文件明明存在）。所以不能用 model_asset_path，
        # 改成把模型读成 bytes 传进去，彻底绕开路径编码问题。
        with open(model_path, "rb") as f:
            self._model_buf = f.read()

        self.num_hands = num_hands
        self._last_ts = -1
        self.gesture_recognizer = None
        self.use_model = False

        if gesture_model and os.path.exists(gesture_model):
            try:
                with open(gesture_model, "rb") as f:
                    buf = f.read()
                opts = vision.GestureRecognizerOptions(
                    base_options=mp_python.BaseOptions(model_asset_buffer=buf),
                    running_mode=vision.RunningMode.VIDEO,
                    num_hands=num_hands,
                    min_hand_detection_confidence=min_detect,
                    min_hand_presence_confidence=min_presence,
                    min_tracking_confidence=min_tracking,
                )
                self.gesture_recognizer = vision.GestureRecognizer.create_from_options(opts)
                self.use_model = True
            except Exception as e:
                print(f"[warn] 手势模型加载失败（{e}），退回几何规则")

        if not self.use_model:
            opts = vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=self._model_buf),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=min_detect,
                min_hand_presence_confidence=min_presence,
                min_tracking_confidence=min_tracking,
            )
            self.landmarker = vision.HandLandmarker.create_from_options(opts)

    def detect(self, bgr: np.ndarray, ts_ms: int) -> List[Hand]:
        """输入 BGR 帧，返回按画面位置排序的手列表（左→右）。"""
        # VIDEO 模式要求时间戳严格递增，否则会抛错
        if ts_ms <= self._last_ts:
            ts_ms = self._last_ts + 1
        self._last_ts = ts_ms

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        if self.use_model:
            res = self.gesture_recognizer.recognize_for_video(mp_image, ts_ms)
        else:
            res = self.landmarker.detect_for_video(mp_image, ts_ms)

        hands: List[Hand] = []
        aspect = bgr.shape[1] / max(bgr.shape[0], 1)
        if res.hand_landmarks:
            for i, lms in enumerate(res.hand_landmarks):
                arr = np.array([[p.x, p.y, p.z] for p in lms], dtype=np.float32)
                score = 1.0
                mp_side = ""
                try:
                    if res.handedness and i < len(res.handedness):
                        cat = res.handedness[i][0]
                        score = float(cat.score)
                        mp_side = MP_SIDE.get(str(cat.category_name), "")
                except Exception:
                    pass

                # 手势类别（只有 GestureRecognizer 后端才有）
                model_gesture, model_score = "", 0.0
                try:
                    gs = getattr(res, "gestures", None)
                    if gs and i < len(gs) and gs[i]:
                        c = gs[i][0]
                        model_gesture = MODEL_GESTURE.get(str(c.category_name), "")
                        model_score = float(c.score)
                except Exception:
                    pass

                hands.append(Hand(landmarks=arr, score=score,
                                  mp_side=mp_side, mp_score=score, aspect=aspect,
                                  model_gesture=model_gesture, model_score=model_score))

        # 左右判定：位置先验 + handedness 标签交叉验证，见 assign_sides
        return assign_sides(hands, self.hand_mode, self.swap_hands)

    def close(self) -> None:
        """
        关闭 landmarker。

        注意：实测 mediapipe 的 close() 在本机会卡 10 秒以上（等内部线程池退出）。
        放在主线程会让人以为程序卡死，所以丢到后台线程，最多等 0.5 秒就走，
        剩余资源由进程退出时回收。
        """
        try:
            t = threading.Thread(target=self._close_silent, daemon=True)
            t.start()
            t.join(timeout=0.5)
        except Exception:
            pass

    def _close_silent(self) -> None:
        for obj in (getattr(self, "gesture_recognizer", None), getattr(self, "landmarker", None)):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass


def draw_hand(img: np.ndarray, hand: Hand, color=(0, 255, 0), radius: int = 3) -> None:
    """在图上画手部骨架。"""
    h, w = img.shape[:2]
    pts = [hand.px(i, w, h) for i in range(21)]
    for a, b in HAND_CONNECTIONS:
        cv2.line(img, pts[a], pts[b], color, 2, cv2.LINE_AA)
    for i, p in enumerate(pts):
        # 指尖画大一点，方便肉眼确认
        r = radius + 2 if i in (4, 8, 12, 16, 20) else radius
        cv2.circle(img, p, r, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(img, p, r, color, 1, cv2.LINE_AA)
