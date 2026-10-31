# -*- coding: utf-8 -*-
"""
人脸否决（Face Guard）—— 专治「把我的脸识别成手势」。

问题
    MediaPipe 的 HandLandmarker 在正面人脸上会产生稳定的误检，而且
    ``handedness`` 分数高达 **0.92~0.99**，比真手还高（实测样本见 README）。
    也就是说：**靠置信度阈值根本挡不住**，调高 min-detect 只会先伤到真手。

思路
    手部模型分不清脸和手，但人脸检测器分得清。每帧对每一个检测到的
    「手」，把它的外接框向外扩 50% 抠出来，缩放到 256×256，再问一次
    「这块区域里有没有脸」。判为脸需要同时满足三条：

        1. 21 个关键点里 >50% 落在那张脸的框里      （实测幻影手 0.67~0.95）
        2. 手的框有 >40% 被脸框包住                  （实测 0.54~0.86）
        3. 手的投影面积小于脸的 0.85 倍             （实测 0.44~0.69）

    第 3 条是最硬的物理判据：手要举到脸前面，它离镜头比脸近，投影一定更大。
    比脸还小的"手"只可能是脸上的一块（下巴、脸颊）。

为什么必须局部放大再问
    实测 BlazeFace short-range 直接整帧输入时，只能认出占画面高度 ≥25%
    的脸，更小的脸会漏。而把疑似区域抠出来放大到 256×256 后，脸占画面
    12%~45% 的各种尺寸 **10/10 全部命中**（0.87~0.92）。
    误检出来的那只「手」本来就长在脸上，所以按它的框去抠，永远抠得到脸。

开销
    每只疑似手一次 256×256 推理，约 0.3~0.5 ms，且只在检测到手时才跑，
    对 16~21 ms 的检测耗时来说可以忽略。

模型
    models/blaze_face_short_range.tflite（229 KB，Apache-2.0，
    取自 mediapipe 0.10.21 官方 wheel，与原版 BlazeFace short-range 一致）。
    文件缺失时本模块自动关闭，程序行为退回到没有否决的状态。
"""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from hand_tracker import Hand


class FaceGuard:
    """判断某个手部检测结果其实是人脸，并把它剔掉。"""

    def __init__(
        self,
        model_path: str,
        min_conf: float = 0.5,
        pad: float = 0.5,
        patch_size: int = 256,
        land_ratio: float = 0.5,
        contain_ratio: float = 0.40,
        size_ratio: float = 0.85,
    ) -> None:
        self.pad = pad
        self.patch_size = patch_size
        self.land_ratio = land_ratio
        self.contain_ratio = contain_ratio
        self.size_ratio = size_ratio
        self.detector = None
        self.enabled = False
        self.total_dropped = 0
        self._last_log = 0.0
        self._last_ms = 0.0

        if not os.path.exists(model_path):
            print(f"[face] 未找到人脸模型 {model_path}，人脸否决已关闭")
            return

        # 与手部模型同样的坑：mediapipe C++ 层用窄字符 API 读文件，
        # 中文路径会报 FileNotFoundError，所以走 model_asset_buffer。
        try:
            with open(model_path, "rb") as f:
                buf = f.read()
            opts = vision.FaceDetectorOptions(
                base_options=mp_python.BaseOptions(model_asset_buffer=buf),
                running_mode=vision.RunningMode.IMAGE,
                min_detection_confidence=min_conf,
            )
            self.detector = vision.FaceDetector.create_from_options(opts)
            self.enabled = True
        except Exception as e:                     # 模型损坏/版本不兼容都不该拖垮主程序
            print(f"[face] 人脸模型加载失败（{e}），人脸否决已关闭")

    # ------------------------------------------------------------ 单只手
    def check(self, bgr: np.ndarray, hand: Hand) -> bool:
        """True = 这只「手」其实是一张脸，应当丢弃。"""
        if not self.enabled:
            return False

        h, w = bgr.shape[:2]
        x0, y0, x1, y1 = hand.bbox_px(w, h)
        bw, bh = x1 - x0, y1 - y0
        if bw < 12 or bh < 12:      # 太小，抠出来没有信息量
            return False

        # 外扩一圈再抠：脸框通常比 21 个关键点的分布更大
        m = int(max(bw, bh) * self.pad)
        px0, py0 = max(0, x0 - m), max(0, y0 - m)
        px1, py1 = min(w, x1 + m), min(h, y1 + m)
        patch = bgr[py0:py1, px0:px1]
        if patch.size == 0:
            return False

        # 补成正方形：人脸检测器对极端长宽比不友好
        side = max(patch.shape[:2])
        sq = np.zeros((side, side, 3), np.uint8)
        sq[: patch.shape[0], : patch.shape[1]] = patch
        n = self.patch_size
        sq = cv2.resize(sq, (n, n), interpolation=cv2.INTER_LINEAR)

        _t = time.perf_counter()
        res = self.detector.detect(mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(sq, cv2.COLOR_BGR2RGB),
        ))
        self._last_ms = (time.perf_counter() - _t) * 1000.0

        if not res.detections:
            return False

        # 21 点换算到 256×256 的 patch 坐标系，直接和脸框比
        scale = n / float(side)
        lx = (hand.landmarks[:, 0] * w - px0) * scale
        ly = (hand.landmarks[:, 1] * h - py0) * scale
        # 手的外接框也换到同一坐标系，否则和脸框比会串空间
        hx0, hy0 = (x0 - px0) * scale, (y0 - py0) * scale
        hx1, hy1 = (x1 - px0) * scale, (y1 - py0) * scale
        hbw, hbh = max(hx1 - hx0, 1e-6), max(hy1 - hy0, 1e-6)

        for det in res.detections:
            b = det.bounding_box
            # 脸框外扩 10%：BlazeFace 的框常常切掉下巴和额头
            mx, my = b.width * 0.1, b.height * 0.1
            fx0, fy0 = b.origin_x - mx, b.origin_y - my
            fx1, fy1 = b.origin_x + b.width + mx, b.origin_y + b.height + my
            inside = ((lx >= fx0) & (lx <= fx1) & (ly >= fy0) & (ly <= fy1)).mean()
            if inside < self.land_ratio:
                continue

            # 第二道条件：这只「手」的框也得基本被脸框包住。
            # 误检出来的幻影手就是那张脸的一块（实测包住率 0.54~0.86）；
            # 而**真人把手举到脸前面**时，手指/手腕通常伸出脸框外 —— 不满足就别误杀。
            ix = max(0.0, min(fx1, hx1) - max(fx0, hx0))
            iy = max(0.0, min(fy1, hy1) - max(fy0, hy0))
            contain = (ix * iy) / (hbw * hbh)
            if contain < self.contain_ratio:
                continue

            # 第三道条件：体积关系。手要举到脸前面，它离镜头比脸更近，
            # 投影面积一定大于脸。实测幻影手只有脸的 0.44~0.69 倍；
            # 真手压过来通常是 1 倍以上 —— 比脸还小就别当成手，那是脸上的一块。
            size_ratio = (hbw * hbh) / max(float(b.width * b.height), 1e-6)
            if size_ratio <= self.size_ratio:
                return True
        return False

    # ------------------------------------------------------------ 整帧
    def filter(self, bgr: np.ndarray, hands: List[Hand]) -> Tuple[List[Hand], int]:
        """返回 (保留的手, 被否决的数量)。被否决后会重算左右手。"""
        if not self.enabled or not hands:
            return hands, 0

        kept: List[Hand] = []
        dropped = 0
        for hd in hands:
            if self.check(bgr, hd):
                dropped += 1
            else:
                kept.append(hd)

        if dropped:
            self.total_dropped += dropped
            # 少了一只手，左右关系要重排 —— 交给调用方用同一套规则重算，
            # 这里不自己调 assign_sides()，避免两处逻辑各写一遍。
            now = time.perf_counter()
            if now - self._last_log > 3.0:
                self._last_log = now
                print(f"[face] 忽略了 {dropped} 个「长得像手的脸」")
        return kept, dropped
