# -*- coding: utf-8 -*-
"""
情绪感知 —— 从面部表情推断"用户现在是什么状态"，用来切换陪伴方式。

只关心四件事，不做"读心"：
    neutral  平静     → 正常互动
    happy    在笑     → 它也跟着高兴一下
    tired    在打哈欠 → 安静陪伴：动作变轻、不主动说话
    frown    皱眉     → 关怀一次（有冷却，不当复读机）

为什么用 blendshapes 而不是训练一个情绪分类器：
    MediaPipe FaceLandmarker 直接给 52 个面部动作系数（ARKit 那套命名），
    "嘴角上扬""眯眼""皱眉"这些信号本身就在里面。再训一个模型属于用大炮打蚊子，
    而且小模型判"开心/难过"的准确率未必比几个系数加权高。

判据为什么这么设：
    只用**动作**不用**身份**——不看你是谁，只看这一帧嘴角/眼睛/眉毛的形状。
    这也是隐私边界：不存图、不建人脸档案，只在内存里过一个系数向量。

开销与降频
    FaceLandmarker + blendshapes 单帧约 4~9ms（CPU），比手部模型贵。
    但表情是**秒级**变化，每 1 秒看一次完全够，摊下来可以忽略。
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import cv2

import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

#: 判定阈值。都是"系数"（0~1），不是概率，所以这里的数只能靠实测调。
SMILE_ON = 0.30          # 嘴角上扬到这个程度算在笑
BLINK_HEAVY = 0.55       # 闭眼超过这个程度（眨眼是瞬间的，持续就算疲惫）
JAW_OPEN = 0.25          # 张嘴（打哈欠）
FROWN_ON = 0.35          # 嘴角下垂 + 皱眉

#: 稳定判定：连续这么多次一致才切换情绪。
#: 表情会在"平静/微笑"之间反复横跳，不防抖的话宠物的表情也会跟着抽搐。
CONFIRM_COUNT = 2

NEUTRAL, HAPPY, TIRED, FROWN = "neutral", "happy", "tired", "frown"


def _avg(shapes: Dict[str, float], *names: str) -> float:
    """取一组系数名的平均值。名字缺失按 0 算——模型升级改了命名也不会崩。"""
    vals = [shapes[n] for n in names if n in shapes]
    return sum(vals) / len(vals) if vals else 0.0


def score_emotion(shapes: Dict[str, float]) -> Tuple[str, float]:
    """
    系数表 -> (情绪, 强度 0~1)。纯函数，方便单测和调阈值。

    优先级：笑 > 疲惫 > 皱眉 > 平静。
    为什么笑排最前：又累又在笑的人，多半是想显得没事，这时候不该去安慰他。
    """
    smile = _avg(shapes, "mouthSmileLeft", "mouthSmileRight")
    frown = _avg(shapes, "mouthFrownLeft", "mouthFrownRight")
    brow = _avg(shapes, "browDownLeft", "browDownRight")
    blink = _avg(shapes, "eyeBlinkLeft", "eyeBlinkRight")
    jaw = shapes.get("jawOpen", 0.0)

    if smile >= SMILE_ON:
        return HAPPY, min(1.0, smile)

    # 疲惫 = **闭眼** 且（张嘴打哈欠 或 皱眉）。
    # 单独一个 blink 高不算：眨眼只要 0.1~0.4 秒，被抓到的概率不低，
    # 而"累"是持续状态。加上第二个条件才够区分。
    if blink >= BLINK_HEAVY and (jaw >= JAW_OPEN or brow >= 0.40):
        return TIRED, min(1.0, 0.5 * blink + 0.5 * jaw + 0.2 * brow)

    if frown + brow >= FROWN_ON:
        return FROWN, min(1.0, frown + brow)

    return NEUTRAL, 0.0


class EmotionDetector:
    """把摄像头帧变成"用户情绪"，自带降频与防抖。"""

    def __init__(self, model_path: str, interval: float = 1.0,
                 min_conf: float = 0.5) -> None:
        self.interval = max(0.3, float(interval))
        self.enabled = False
        self.emotion = NEUTRAL
        self.strength = 0.0
        self._det = None
        self._next_check = 0.0
        self._candidate = NEUTRAL
        self._streak = 0

        if not os.path.exists(model_path):
            return

        try:
            options = vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=min_conf,
                output_face_blendshapes=True,
            )
            self._det = vision.FaceLandmarker.create_from_options(options)
            self.enabled = True
        except Exception:
            self._det = None

    def update(self, frame_bgr, now: float) -> Optional[Tuple[str, float]]:
        """
        喂一帧。返回**情绪变化**（(情绪, 强度) 或 None）。

        没检测到脸时保持上一次情绪不动：人扭头看别处不代表他情绪变了，
        这时候把状态清成 neutral 会让"安静陪伴"莫名其妙地断掉。
        """
        if not self.enabled or now < self._next_check:
            return None
        self._next_check = now + self.interval

        try:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._det.detect_for_video(img, int(now * 1000))
        except Exception:
            return None

        if not result.face_blendshapes:
            return None

        shapes = {c.category_name: float(c.score)
                  for c in result.face_blendshapes[0]}
        emotion, strength = score_emotion(shapes)

        # 防抖：连续 CONFIRM_COUNT 次一致才真的切换
        if emotion == self._candidate:
            self._streak += 1
        else:
            self._candidate = emotion
            self._streak = 1

        if self._streak >= CONFIRM_COUNT and emotion != self.emotion:
            self.emotion = emotion
            self.strength = strength
            return emotion, strength
        return None

    def close(self) -> None:
        det, self._det = self._det, None
        self.enabled = False
        if det is not None:
            try:
                det.close()
            except Exception:
                pass
