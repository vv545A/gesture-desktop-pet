# -*- coding: utf-8 -*-
"""
情绪判定的纯函数测试 —— 不需要摄像头、不需要模型。

跑法：
    python tests/test_emotion.py

为什么只测 score_emotion：
    blendshapes 到"情绪"这一步是唯一有判断的地方，也是最容易调坏的地方
    （阈值一动，原本"在笑"的人就变成"平静"）。模型加载和推理属于外部依赖，
    单独测它只会得到一个"能跑"的结论，没有信息量。

真实阈值必须靠真人校准，这里测的是**逻辑关系**：谁优先、缺字段怎么办。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pet.perception.emotion import (FROWN, HAPPY, NEUTRAL, TIRED,   # noqa: E402
                                    score_emotion)

PASS = []


def check(name: str, cond: bool) -> None:
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        raise AssertionError(name)


def shapes(**kw) -> dict:
    """只填需要的系数，其余默认 0 —— 和真实 blendshape 表一样是稀疏的。"""
    base = {"mouthSmileLeft": 0.0, "mouthSmileRight": 0.0,
            "mouthFrownLeft": 0.0, "mouthFrownRight": 0.0,
            "browDownLeft": 0.0, "browDownRight": 0.0,
            "eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.0,
            "jawOpen": 0.0}
    base.update(kw)
    return base


print("== 基本判定 ==")
check("全零 -> 平静", score_emotion(shapes())[0] == NEUTRAL)
check("嘴角两边都上扬 -> 开心",
      score_emotion(shapes(mouthSmileLeft=0.6, mouthSmileRight=0.6))[0] == HAPPY)
check("只有一边笑也算笑（很多人笑起来不对称）",
      score_emotion(shapes(mouthSmileLeft=0.7, mouthSmileRight=0.1))[0] == HAPPY)

check("闭眼 + 张嘴（打哈欠）-> 疲惫",
      score_emotion(shapes(eyeBlinkLeft=0.9, eyeBlinkRight=0.9, jawOpen=0.5))[0] == TIRED)
check("只是眨眼不算疲惫",
      score_emotion(shapes(eyeBlinkLeft=0.9, eyeBlinkRight=0.9))[0] == NEUTRAL)

check("嘴角下垂 + 皱眉 -> 皱眉",
      score_emotion(shapes(mouthFrownLeft=0.4, mouthFrownRight=0.4,
                           browDownLeft=0.4, browDownRight=0.4))[0] == FROWN)

print("== 优先级 ==")
# 又累又在笑：多半是在硬撑，这时候凑上去安慰反而尴尬
mixed = shapes(mouthSmileLeft=0.8, mouthSmileRight=0.8,
               eyeBlinkLeft=0.9, eyeBlinkRight=0.9, jawOpen=0.6)
check("又累又笑时判开心（不去打扰）", score_emotion(mixed)[0] == HAPPY)

# 皱眉和笑同时出现：嘴角上扬更有决定性
mixed2 = shapes(mouthSmileLeft=0.5, mouthSmileRight=0.5,
                browDownLeft=0.8, browDownRight=0.8)
check("笑着皱眉仍判开心", score_emotion(mixed2)[0] == HAPPY)

print("== 健壮性 ==")
check("空字典不崩", score_emotion({})[0] == NEUTRAL)
check("缺字段不崩", score_emotion({"jawOpen": 0.1})[0] == NEUTRAL)
# 只给一边嘴角也要能判：模型裁剪/遮挡时经常只回来一半系数
check("只剩半边系数也能判笑", score_emotion({"mouthSmileLeft": 0.9})[0] == HAPPY)
check("强度在 0~1 之间", 0.0 <= score_emotion(shapes(mouthSmileLeft=0.99,
                                                     mouthSmileRight=0.99))[1] <= 1.0)

print("== 情绪名不写错 ==")
check("四个情绪常量都在", {NEUTRAL, HAPPY, TIRED, FROWN} ==
      {"neutral", "happy", "tired", "frown"})

print(f"\n[RESULT] {len(PASS)} 项全部通过")
