# -*- coding: utf-8 -*-
"""
桌宠的动作系统 —— 把"一张张不能动的立绘"变成会跳、会摇头、会打哈欠的宠物。

为什么要有这一层
    贴图立绘的表情是画死的：stand 永远是那张微笑脸，happy 永远张着嘴。
    想让 7 张图看起来像"活的"，只剩两条路：
        A. 做序列帧动画（每个动作几十张图，美术成本爆炸）
        B. 用**变换 + 换帧 + 特效**拼出动作（这一层）
    选 B。一只 150px 高的桌宠，"跳一下"其实不需要真的画 24 帧 ——
    压扁 0.12 秒、弹起 0.2 秒、落地再压一下，人眼就认定它跳了。
    这是传统 2D 动画的"预备-动作-跟随"三拍，做对了比序列帧还自然。

单位约定（很重要，改数值前先看）
    dy / dx   按**身高的比例**给，不是像素。-0.08 = 往上跳身高的 8%。
              跟着 pet_scale 自动缩放，放大桌宠时动作幅度不会显得变小。
    sx / sy   缩放倍率，1.0 = 原样。压扁就 sy<1 且 sx>1（体积守恒）。
    rot       角度。
    frame     要换的立绘帧名；"" = 保持当前情绪的帧不动。

时间轴
    每帧的 t 是 0~1 的**归一化进度**，不是秒。这样同一个动作换个速度
    （--speed）不用重画时间轴，也让"节奏"这件事集中在一处调。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 情绪 → 立绘帧
# 立绘只有 7 张，情绪比 7 个多，所以只能"就近取材"：
#   shy（害羞）用 think：双手抱在胸前、闭眼、脸红 —— 比 happy 更像害羞
#   surprised 用 wave：举手 + 张嘴，正好是"欸？！"的样子
MOOD_FRAME: Dict[str, str] = {
    "idle": "stand",
    "listen": "stand",
    "calm": "stand",
    "happy": "happy",
    "proud": "happy",
    "shy": "think",
    "think": "think",
    "surprised": "wave",
    "wave": "wave",
    "angry": "angry",
    "sleepy": "sleepy",
    "sit": "sit",
    "love": "happy",
}

# 兜底帧：配置里指定的那张，情绪表里查不到就用它
FALLBACK_FRAME = "stand"


def frame_for_mood(mood: str) -> str:
    return MOOD_FRAME.get(mood, FALLBACK_FRAME)


# ---------------------------------------------------------------- 关键帧
@dataclass(frozen=True)
class Frame:
    t: float                 # 归一化时间 0~1
    dy: float = 0.0
    dx: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    frame: str = ""          # "" = 不换帧


@dataclass(frozen=True)
class Clip:
    """一个动作：若干关键帧 + 总时长。"""

    name: str
    dur: float
    frames: Tuple[Frame, ...]
    # 同一个动作能不能被自己重新触发（连点两下 = 连跳两下）
    restartable: bool = True


@dataclass
class Motion:
    """采样结果：某一时刻要叠加到姿态上的变换。"""

    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    frame: str = ""


# ---------------------------------------------------------------- 动作库
# 每个动作都遵循「预备 → 动作 → 跟随」：
#   预备（anticipation）：先朝**反方向**动一点，人才不会觉得突然
#   动作（action）：主位移
#   跟随（follow-through）：回弹时轻微过冲，再收住
CLIPS: Dict[str, Clip] = {
    # 成功执行一个手势 -> 开心地蹦一下
    "hop": Clip("hop", 0.55, (
        Frame(0.00, sy=1.00),
        Frame(0.14, sy=0.90, sx=1.07),                    # 预备：下蹲
        Frame(0.32, dy=-0.105, sy=1.08, sx=0.94),         # 起跳
        Frame(0.56, dy=-0.058, sy=1.03, sx=0.97),
        Frame(0.78, dy=0.008, sy=0.91, sx=1.07),          # 落地压扁
        Frame(1.00, sy=1.00),
    )),
    # 手势失败 / 被戳烦了 -> 摇头（左右晃 + 小角度转）
    "shake": Clip("shake", 0.62, (
        Frame(0.00),
        Frame(0.12, dx=0.035, rot=5.0),
        Frame(0.30, dx=-0.035, rot=-5.0),
        Frame(0.48, dx=0.026, rot=3.6),
        Frame(0.66, dx=-0.017, rot=-2.4),
        Frame(0.84, dx=0.007, rot=1.0),
        Frame(1.00),
    )),
    # 招手：手（也就是整只宠物）轻轻摆，配 wave 帧
    "wave": Clip("wave", 1.30, (
        Frame(0.00, frame="wave"),
        Frame(0.18, rot=-4.0, dy=-0.010, frame="wave"),
        Frame(0.42, rot=4.0, dy=0.000, frame="wave"),
        Frame(0.66, rot=-3.0, dy=-0.008, frame="wave"),
        Frame(0.86, rot=2.0, frame="wave"),
        Frame(1.00),
    )),
    # 点头：承认、收到
    "nod": Clip("nod", 0.70, (
        Frame(0.00),
        Frame(0.22, dy=0.022, sy=0.985),
        Frame(0.44, dy=-0.004),
        Frame(0.66, dy=0.016, sy=0.99),
        Frame(1.00),
    )),
    # 打哈欠：往上伸、再塌下来
    "yawn": Clip("yawn", 1.70, (
        Frame(0.00, frame="sleepy"),
        Frame(0.30, dy=-0.012, sy=1.05, sx=0.97, frame="sleepy"),
        Frame(0.62, dy=-0.006, sy=1.06, sx=0.96, frame="sleepy"),
        Frame(0.82, dy=0.012, sy=0.95, sx=1.04, frame="sleepy"),
        Frame(1.00, frame="sleepy"),
    )),
    # 吓了一跳：猛地一弹
    "startle": Clip("startle", 0.44, (
        Frame(0.00, sy=1.10, sx=0.94, dy=-0.020, frame="wave"),
        Frame(0.30, sy=0.97, sx=1.03, dy=0.004, frame="wave"),
        Frame(0.65, sy=1.015),
        Frame(1.00),
    )),
    # 坐下 / 站起（帧由情绪决定，这里只管"塌下去"这个过渡）
    "sit_down": Clip("sit_down", 0.55, (
        Frame(0.00),
        Frame(0.35, dy=-0.010, sy=1.03),
        Frame(0.75, dy=0.012, sy=0.95, sx=1.04),
        Frame(1.00, frame="sit"),
    ), restartable=False),
    "stand_up": Clip("stand_up", 0.50, (
        Frame(0.00, sy=0.96, sx=1.03, frame="sit"),
        Frame(0.40, sy=1.04, sx=0.98, frame="stand"),
        Frame(0.75, sy=0.99),
        Frame(1.00),
    ), restartable=False),
    # 伸懒腰（坐久了活动一下）
    "stretch": Clip("stretch", 1.50, (
        Frame(0.00),
        Frame(0.30, dy=-0.028, sy=1.07, sx=0.95, rot=-2.0),
        Frame(0.55, dy=-0.030, sy=1.08, sx=0.94, rot=2.0),
        Frame(0.80, dy=0.006, sy=0.97, sx=1.02),
        Frame(1.00),
    )),
    # 被摸：轻轻一缩，配爱心特效
    "pat": Clip("pat", 0.46, (
        Frame(0.00),
        Frame(0.22, dy=0.014, sy=0.93, sx=1.05),
        Frame(0.52, dy=-0.006, sy=1.02, sx=0.99),
        Frame(1.00),
    )),
}


# 动作的中文名 —— 给动作表预览图、日志和以后的"动作调试页"共用一份，
# 免得同一个动作在三个地方有三种叫法。
CLIP_LABEL: Dict[str, str] = {
    "hop": "蹦一下",
    "shake": "摇头",
    "wave": "招手",
    "nod": "点头",
    "yawn": "打哈欠",
    "startle": "吓一跳",
    "sit_down": "坐下",
    "stand_up": "站起来",
    "stretch": "伸懒腰",
    "pat": "被摸",
}


def _smooth(x: float) -> float:
    """smoothstep：两头慢中间快，比线性自然。所有片段都过这一层。"""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)

class AnimPlayer:
    """
    同一时刻只播一个动作。新动作可以打断旧动作 —— 桌宠被戳一下就该立刻有反应，
    排队播完再反应是"游戏感"，不是"活物感"。
    """

    def __init__(self) -> None:
        self._clip: Optional[Clip] = None
        self._t0 = 0.0
        self._speed = 1.0

    # ---------------------------------------------------------------- 控制
    def play(self, name: str, now: float, speed: float = 1.0,
             force: bool = False) -> bool:
        """开始播。返回是否真的播了（同一动作在播且不允许重播时返回 False）。"""
        clip = CLIPS.get(name)
        if clip is None:
            return False
        if (not force and self._clip is clip
                and not clip.restartable and self.progress(now) < 1.0):
            return False
        self._clip = clip
        self._t0 = now
        self._speed = max(0.2, speed)
        return True

    def stop(self) -> None:
        self._clip = None

    @property
    def name(self) -> str:
        return self._clip.name if self._clip else ""

    @property
    def active(self) -> bool:
        return self._clip is not None

    def progress(self, now: float) -> float:
        if self._clip is None:
            return 0.0
        return max(0.0, min(1.0, (now - self._t0) * self._speed / self._clip.dur))

    # ---------------------------------------------------------------- 采样
    def sample(self, now: float) -> Optional[Motion]:
        """返回此刻的变换；动作播完了返回 None（调用方就不再叠加）。"""
        clip = self._clip
        if clip is None:
            return None
        p = self.progress(now)
        if p >= 1.0:
            self._clip = None
            return None
        a, b = _bracket(clip.frames, p)
        if b is None:
            return Motion(frame=a.frame)

        w = _smooth((p - a.t) / max(1e-6, b.t - a.t))
        return Motion(
            dx=_lerp(a.dx, b.dx, w),
            dy=_lerp(a.dy, b.dy, w),
            sx=_lerp(a.sx, b.sx, w),
            sy=_lerp(a.sy, b.sy, w),
            rot=_lerp(a.rot, b.rot, w),
            # 帧是离散的：按"当前落在哪一段"取左边那个关键帧的帧名，
            # 不去插值（插值帧名没有意义）。取 b 的，因为 b 的帧名代表
            # 这一段的"到达状态"，换帧都发生在关键帧上。
            frame=b.frame or a.frame,
        )


def _lerp(a: float, b: float, w: float) -> float:
    return a + (b - a) * w


def _bracket(frames: Tuple[Frame, ...], p: float) -> Tuple[Frame, Optional[Frame]]:
    """找到 p 落在哪两个关键帧之间。"""
    if not frames:
        return Frame(0.0), None
    if p <= frames[0].t:
        return frames[0], None
    for i in range(len(frames) - 1):
        a, b = frames[i], frames[i + 1]
        if a.t <= p <= b.t:
            return a, b
    return frames[-1], None


# ---------------------------------------------------------------- 自检
def dump() -> List[str]:
    """给测试/调试用：一行一个动作，标出时长和关键帧数。"""
    out = []
    for name, c in sorted(CLIPS.items()):
        out.append(f"{name:10s} {CLIP_LABEL.get(name, ''):5s} {c.dur:.2f}s  "
                   f"{len(c.frames)} 关键帧  "
                   f"{'可重播' if c.restartable else '独占'}")
    return out
