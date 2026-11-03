# -*- coding: utf-8 -*-
"""
在场检测 —— 判断摄像头前有没有人。

用途
    人离开 → 宠物打盹（比"没看到手"更沉、更安静）
    人回来 → 主动迎接，而不是傻等到对方举起手

为什么单独一个检测器，不复用 face_guard
    FaceGuard 是"检测到手才跑"的**否决器**，输入是手的外接框，用途是剔掉
    "长在脸上的幻影手"。在场检测要的是"整帧里有没有脸"，跟手毫无关系。
    输入输出都不搭，硬塞进去只会把两个正交的东西缠在一起。

为什么降到 2Hz
    整帧 BlazeFace 一次约 1~2ms，但人来人往是**秒级**事件。每 0.5 秒看
    一眼足够，省下的算力留给手部模型（那个才是 30FPS 的刚需）。

隐私
    只判断"这一帧里有没有脸"，不存图、不识别是谁、不落盘。和 face_guard
    共用同一个 229KB 的本地模型，全程不出本机。
"""

from __future__ import annotations

import os
import time
from typing import Optional

import cv2

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


class PresenceDetector:
    """
    在座/离开的判定器，自带防抖。

    判定方式刻意做得很朴素：**最后一次看到人的时间**。
    人脸检测偶尔漏一帧是常态（低头、转头、手挡脸），如果"这一帧没脸 =
    人走了"，宠物会疯狂在"打盹"和"迎接"之间横跳。所以用"多久没看到"，
    阈值 away_after 默认 6 秒——够容忍一个低头捡笔的动作。
    """

    def __init__(self, model_path: str, min_conf: float = 0.5,
                 interval: float = 0.5, away_after: float = 6.0) -> None:
        self.interval = max(0.1, float(interval))
        self.away_after = max(1.0, float(away_after))
        self.present = True
        self.enabled = False
        self._det = None
        self._next_check = 0.0
        self._last_seen = time.perf_counter()   # 从"在场"开始算，避免开局就误判离开

        if not os.path.exists(model_path):
            return                              # 没模型就静默关闭，不影响主流程

        try:
            options = vision.FaceDetectorOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.VIDEO,
                min_detection_confidence=min_conf,
            )
            self._det = vision.FaceDetector.create_from_options(options)
            self.enabled = True
        except Exception:
            self._det = None                    # 模型坏了也不该让桌宠起不来

    def update(self, frame_bgr, now: float,
               has_hand: bool = False) -> Optional[bool]:
        """
        喂一帧，返回**状态变化**：True=刚回来，False=刚离开，None=没变化。

        返回"变化"而不是"当前状态"，是因为调用方只在变化时需要做事
        （说一句欢迎、切一次表情）。每帧都问一遍会让上层多写一层判断。

        时间戳在内部用 `now` 换算，不接外面的 ts：摄像头上限/重连会重置
        帧计时，MediaPipe 的 VIDEO 模式一旦看到时间戳回退就会抛异常。
        """
        if not self.enabled:
            return None

        # 检测到手 = 一定有人在场。这条比人脸检测更可靠（手在动、脸可能转开），
        # 而且能省掉一次推理。
        if has_hand:
            self._last_seen = now

        if now >= self._next_check:
            self._next_check = now + self.interval
            try:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = self._det.detect_for_video(img, int(now * 1000))
                if result.detections:
                    self._last_seen = now
            except Exception:
                # 单帧推理失败（尺寸异常、时间戳回退）不该中断整条感知链路
                pass

        present = (now - self._last_seen) < self.away_after
        if present != self.present:
            self.present = present
            return present
        return None

    def close(self) -> None:
        det, self._det = self._det, None
        self.enabled = False
        if det is not None:
            try:
                det.close()
            except Exception:
                pass
