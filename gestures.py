# -*- coding: utf-8 -*-
"""
手势识别：基于 21 个关键点的几何规则 + 时间防抖。

为什么不训练分类模型：
    手指是否伸直是纯几何问题，用「指尖到腕部距离 > 指间关节(PIP)到腕部距离」
    即可判定，再叠加拇指的掌心距离判定。零训练、可解释、加手势只改几行规则。
    所有距离都用 palm_size（腕→中指根）做尺度归一化，所以手离摄像头远近不影响识别。

三层结构：
    recognize()  单帧原始判定（噪声大，会抖）
    Debouncer    连续 N 帧一致才确认 + 冷却时间，把抖动过滤掉
    SequenceDetector 检测有序手势序列（总开关：握拳-张开-握拳-张开）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from hand_tracker import (
    Hand,
    WRIST,
    THUMB_IP,
    THUMB_TIP,
    INDEX_TIP,
    INDEX_PIP,
    MIDDLE_MCP,
    MIDDLE_TIP,
    MIDDLE_PIP,
    RING_TIP,
    RING_PIP,
    PINKY_TIP,
    PINKY_PIP,
)

# 手势名 -> 中文显示
GESTURE_CN: Dict[str, str] = {
    "OPEN_PALM": "张开手掌",
    "FIST": "握拳",
    "THUMB_UP": "点赞",
    "THUMB_DOWN": "踩",
    "POINT": "指",
    "VICTORY": "剪刀手",
    "GUN": "L 形",
    "THREE": "三",
    "PINKY": "小指",
    "PINCH": "捏合",
    "UNKNOWN": "未知",
}

# 五指伸展模式 -> 手势名
# 顺序：(拇指, 食指, 中指, 无名指, 小指)，1=伸直
PATTERN_NAMES: Dict[Tuple[int, int, int, int, int], str] = {
    (1, 1, 1, 1, 1): "OPEN_PALM",
    (0, 0, 0, 0, 0): "FIST",
    (1, 0, 0, 0, 0): "THUMB",      # 再看方向决定 THUMB_UP / THUMB_DOWN
    (0, 1, 0, 0, 0): "POINT",
    (0, 1, 1, 0, 0): "VICTORY",
    (1, 1, 0, 0, 0): "GUN",
    (1, 1, 1, 0, 0): "THREE",
    (0, 0, 0, 0, 1): "PINKY",
    (1, 1, 1, 1, 0): "FOUR",
    (0, 1, 1, 1, 0): "THREE_ALT",
}


def _dist(a, b) -> float:
    """两点的 2D 欧氏距离（只用 x/y，z 在单手场景下噪声大）。"""
    pa = np.asarray(a, dtype=np.float32)[:2]
    pb = np.asarray(b, dtype=np.float32)[:2]
    return float(np.linalg.norm(pa - pb))


def _dist_metric(hand: Hand, i: int, j: int) -> float:
    """
    关键点 i、j 之间的**等比距离**。

    归一化坐标里 x 除以画面宽、y 除以画面高，两者尺度不同：1280×720 下
    横向被压缩了 1.78 倍。直接算距离会把"横向张开"的动作算小，
    捏合判定就被这个误差带偏（详见 pinch_ratio 的注释）。
    """
    a = hand.aspect
    lm = hand.landmarks
    dx = (float(lm[i][0]) - float(lm[j][0])) * a
    dy = float(lm[i][1]) - float(lm[j][1])
    return float(np.hypot(dx, dy))


def thumb_extended(hand: Hand, min_gap: Optional[float] = None) -> bool:
    """
    拇指是否真的伸出来了。

    原来的判据是「拇指尖离掌心比拇指 IP 离掌心远 15%」。问题是：握拳时拇指
    横搭在食指旁边，指尖离掌心也很远，比值照样超过 1.15 —— 于是握拳被当成
    「竖拇指」，触发音量键。

    改成量「拇指尖离食指近端指节(PIP)多远」：握拳时拇指贴着食指，这个距离小；
    真竖拇指时拇指是支出去的，距离明显变大。
    实测（197 帧握拳 / 107 帧点赞，单位：掌长）：
        握拳 中位 1.09，最大 ≈1.10      点赞 中位 1.18，p10 1.07
    阈值 1.12：握拳误判 0%，点赞命中 62.6%。
    宁可漏掉几次点赞，也不能把握拳变成乱改音量。
    """
    if min_gap is None:
        min_gap = THUMB_AWAY
    palm = _dist_metric(hand, WRIST, MIDDLE_MCP)
    return _dist_metric(hand, THUMB_TIP, INDEX_PIP) > min_gap * palm


THUMB_AWAY = 1.12        # 拇指尖离食指 PIP 的最小距离（单位：掌长）


def fingers_up(hand: Hand) -> Tuple[int, int, int, int, int]:
    """
    判断五指是否伸直，返回 (拇指, 食指, 中指, 无名指, 小指)。

    原理：手指伸直时，指尖会被推得比中间的 PIP 关节离腕部更远。
    拇指方向特殊，改用「离食指 PIP 的距离」，见 thumb_extended()。
    """
    lm = hand.landmarks
    wrist = lm[WRIST][:2]

    ext: List[int] = []
    for tip, pip in (
        (INDEX_TIP, INDEX_PIP),
        (MIDDLE_TIP, MIDDLE_PIP),
        (RING_TIP, RING_PIP),
        (PINKY_TIP, PINKY_PIP),
    ):
        ext.append(1 if _dist(lm[tip], wrist) > _dist(lm[pip], wrist) * 1.08 else 0)

    thumb = 1 if thumb_extended(hand) else 0
    return (thumb, ext[0], ext[1], ext[2], ext[3])


def pinch_ratio(hand: Hand) -> float:
    """
    拇指尖到食指尖的距离，除以掌长做尺度归一化。

    **必须用等比距离**（x 乘宽高比），这是区分握拳和捏合的关键：
        握拳时拇指横搭在食指前面 → 两点间距主要是**横向**的，
            归一化坐标把横向压掉 1.78 倍，0.50 的间距被算成 0.28，
            一头撞进捏合阈值，于是「握拳」永远识别成「捏合」。
        捏合时两指尖对在一起 → 间距**双向都小**，纵向分量为主，
            压缩几乎不影响。
    换成等比距离后实测（同一只手 213 帧）：
        握拳 0.44 [0.39, 0.78]     捏合 0.13 [0.08, 0.40]   中位 [10%,90%]
    """
    return _dist_metric(hand, THUMB_TIP, INDEX_TIP) / max(
        _dist_metric(hand, WRIST, MIDDLE_MCP), 1e-6)


def pinch_affinity(hand: Hand) -> float:
    """
    拇指尖到食指尖的距离 ÷ 拇指尖到中指尖的距离。

    只看距离还不够：紧握拳时拇指也贴在食指上，间距一样小。
    区别在**拇指贴着谁**——
        握拳：拇指横搭过整排手指，到食指和中指差不多 → 比值 ~0.7
        捏合：拇指只对食指，中指在下面                → 比值 ~0.13
    实测：握拳 0.73 [0.68, 0.77]，捏合 0.13 [0.11, 0.74]。
    """
    ti = _dist_metric(hand, THUMB_TIP, INDEX_TIP)
    tm = _dist_metric(hand, THUMB_TIP, MIDDLE_TIP)
    return ti / max(tm, 1e-6)


# 捏合判定阈值：两条都要满足。
PINCH_RATIO = 0.45       # 拇指尖-食指尖 距离 / 掌长
PINCH_AFFINITY = 0.50    # (拇指尖-食指尖) / (拇指尖-中指尖)

# 实测（单只手 213 帧：握拳 107 / 捏合 106）：
#   两条都用             → 握拳误判 0.0%，捏合命中 74.5%
#   只用 PINCH_RATIO 0.33 → 握拳误判 1.9%
# 捏合漏掉的那 25%，是那几帧里模型把捏合解成了握拳（姿态本身模棱两可），
# 任何单帧几何判据都救不了，靠防抖 + 宽容窗口兜住。


def is_pinch(ti: float, affinity: float,
             ti_max: Optional[float] = None,
             affinity_max: Optional[float] = None) -> bool:
    """
    判据本体，单独拎出来方便用采集数据回归。

    注意默认值必须用 None 在调用时再取全局常量：写成
    ``ti_max: float = PINCH_RATIO`` 的话，默认值在函数定义那一刻就固定了，
    之后 `--pinch` 修改模块变量不会生效 —— 这种坑很难发现，因为代码看起来完全正常。
    """
    if ti_max is None:
        ti_max = PINCH_RATIO
    if affinity_max is None:
        affinity_max = PINCH_AFFINITY
    return ti < ti_max and affinity < affinity_max


def thumb_vertical(hand: Hand) -> str:
    """拇指朝向。图像坐标 y 向下，所以 tip.y 更小 = 朝上。"""
    return "up" if hand.landmarks[THUMB_TIP][1] < hand.landmarks[WRIST][1] else "down"


def recognize(hand: Hand) -> str:
    """单只手的静态手势判定。识别不出返回 "UNKNOWN"。"""
    # 1) 捏合优先，而且只用几何：手势模型没有 pinch 这个类别，
    #    捏合和握拳在模型眼里都是 Closed_Fist。
    if is_pinch(pinch_ratio(hand), pinch_affinity(hand)):
        return "PINCH"

    # 2) 有手势模型的结果就听模型的。
    #    握拳 vs 点赞这种，两者在几何上几乎是同一个形状（实测线性判别只有 69%），
    #    手工阈值怎么调都是在"把拳认成赞"和"赞认不出"之间来回摆。
    #    模型是在大量真实手上训练的分类器，这类姿势靠它。
    mg = getattr(hand, "model_gesture", "")
    if mg and getattr(hand, "model_score", 0.0) >= MODEL_MIN_SCORE:
        return mg

    # 3) 没有模型（或模型给的是 None）才退回几何规则
    pattern = fingers_up(hand)
    name = PATTERN_NAMES.get(pattern)
    if name is None:
        return "UNKNOWN"
    if name == "THUMB":
        return "THUMB_UP" if thumb_vertical(hand) == "up" else "THUMB_DOWN"
    return name


MODEL_MIN_SCORE = 0.55   # 手势模型置信度低于此值时退回几何规则


class GestureSmoother:
    """
    对最近几帧的手势做多数表决，压掉分类器的零星翻车。

    实测同一只手做握拳，模型逐帧正确率约 64%（其余帧在 Thumb_Up / Open_Palm 之间跳）。
    这类错误是零散分布的，表决 5 帧取多数能明显抬高一整段动作的正确率，
    而且不需要再引入任何模型。

    update() 收到 None（没看到手/判不出来）时**不清空**缓冲：
    短暂丢帧由 Debouncer 的 grace 兜，这里只管投票。
    """

    def __init__(self, window: int = 5, need: int = 3) -> None:
        from collections import deque
        self.window = window
        self.need = need
        self._buf = deque(maxlen=window)

    def update(self, g: Optional[str]) -> Optional[str]:
        if g is None:
            return None
        self._buf.append(g)
        if len(self._buf) < self.need:
            return None
        counts: Dict[str, int] = {}
        for x in self._buf:
            counts[x] = counts.get(x, 0) + 1
        top, n = max(counts.items(), key=lambda kv: kv[1])
        return top if n >= self.need else None

    def reset(self) -> None:
        self._buf.clear()


# ------------------------------------------------------------------ 双手状态
@dataclass
class TwoHandState:
    """一帧里双手的综合状态。"""

    left: Optional[Hand] = None
    right: Optional[Hand] = None
    left_gesture: Optional[str] = None
    right_gesture: Optional[str] = None
    distance: Optional[float] = None      # 双掌心距离（归一化，已除以平均掌宽）

    @property
    def count(self) -> int:
        return (self.left is not None) + (self.right is not None)


def analyze_two_hands(hands: List[Hand]) -> TwoHandState:
    """把 HandTracker 输出的手列表整理成双手状态。"""
    st = TwoHandState()
    for h in hands:
        if h.side == "left" and st.left is None:
            st.left = h
            st.left_gesture = recognize(h)
        elif h.side == "right" and st.right is None:
            st.right = h
            st.right_gesture = recognize(h)

    if st.left is not None and st.right is not None:
        scale = (st.left.palm_size() + st.right.palm_size()) / 2.0
        st.distance = _dist(st.left.center, st.right.center) / max(scale, 1e-6)
    return st


# ------------------------------------------------------------------ 时间防抖
class MotionGate:
    """
    判断手相对画面是否基本静止。

    为什么需要：悬停判定原来只看"手势认得对不对"，不看手在不在动。
    手从镜头前挥过去时，中间几帧也可能认出同一个手势，时间照样累计——
    于是"挥一下"就触发了，用户会觉得系统在乱猜。

    做法：用掌心在最近 window 秒内的位移除以时长得到速度（单位：掌长/秒，
    这样手离镜头远近不影响判定），超过 max_speed 就算"在动"。
    在动的那些帧不累计悬停时间（由 Debouncer 冻结），停下后重新累计。
    """

    def __init__(self, window: float = 0.25, max_speed: float = 0.6) -> None:
        self.window = window
        self.max_speed = max_speed
        self._q: Deque[Tuple[float, float, float, float]] = deque()
        self.speed = 0.0

    def update(self, now: float, hand: Hand) -> bool:
        """返回 True = 静止（可以开始累计悬停）。"""
        cx, cy = float(hand.center[0]), float(hand.center[1])
        palm = max(_dist_metric(hand, WRIST, MIDDLE_MCP), 1e-6)
        self._q.append((now, cx, cy, palm))
        while self._q and now - self._q[0][0] > self.window:
            self._q.popleft()

        if len(self._q) < 2:
            self.speed = 0.0
            return True

        t0, x0, y0, _p0 = self._q[0]
        dt = max(now - t0, 1e-3)
        dx = (cx - x0) * hand.aspect      # x 换算到等比空间
        dy = cy - y0
        self.speed = float(np.hypot(dx, dy)) / palm / dt
        return self.speed <= self.max_speed

    def reset(self) -> None:
        self._q.clear()
        self.speed = 0.0


class Debouncer:
    """
    手势防抖 + 悬停确认 + 边沿触发。

    为什么按时间而不是按帧数：帧率在 15~30 之间波动，同样「5 帧」在 15fps 下
    是 0.33 秒、在 30fps 下只有 0.17 秒，手感会忽快忽慢。用时间才稳定。

    三条规则：
    1) 同一手势**持续保持 hold_seconds 秒**才确认（悬停触发）
    2) 触发一次后必须**松开手势**才能再次触发（避免举着不动连发）
    3) 仍保留 cooldown 作为额外保险

    第四条（2026-10 加）：**短暂丢失不算松手**。
    原来任何一帧 raw=None 都会把计时清零，但 MediaPipe 偶发丢检、手指抖动导致
    单帧判成 UNKNOWN、或者被其它过滤器拦一帧，都是几帧的事。清零点太低，
    结果就是"举着不动，进度条反复从头开始，永远触发不了"。
    现在丢失时间 < grace 秒时冻结计时（既不清零也不推进），超过才算松手。
    """

    def __init__(self, hold_seconds: float = 0.5, cooldown: float = 0.6,
                 grace: float = 0.35) -> None:
        self.hold = hold_seconds
        self.cooldown = cooldown
        self.grace = grace
        self._cand: Optional[str] = None
        self._held: float = 0.0          # 已累计的悬停秒数（只统计"这一帧看到了同一手势"）
        self._last_call_at: Optional[float] = None
        self._fired_at: Dict[str, float] = {}
        self._armed: bool = True
        self._last_raw_at: float = 0.0
        self.stable: Optional[str] = None
        self.progress: float = 0.0      # 0~1，用于画悬停进度条

        # 诊断计数（--trace 用），不参与判定
        self.n_fired = 0
        self.n_restart = 0
        self.n_gap_tolerated = 0
        self.n_gap_reset = 0
        self.n_motion_paused = 0

    def update(self, raw: Optional[str], now: float,
               moving: bool = False) -> Tuple[Optional[str], bool]:
        """
        返回 (稳定手势, 是否应当触发)。
        「触发」是边沿事件：悬停满 hold 秒的那一刻触发一次。

        moving=True 表示手相对画面还在动：这一帧**冻结**计时（不清零也不累计），
        因为要求是"手稳住才算数"。停下之后接着从原来的进度继续，
        所以挥手经过镜头不会触发，而停下来继续悬停仍然有效。

        计时用的是**帧间隔累加**，不是 now - 起点：只有"这一帧真的看到了同一个
        手势"才把这一帧的间隔加进去。这样丢掉的帧天然不计时，不需要事后做减法
        （减法很容易多扣一帧，抖动场景下时间会永远爬不动 —— 这个坑踩过）。
        """
        # 注意：dt 必须在"运动判定"之前算好并更新 _last_call_at。
        # 如果提前 return 而不更新，恢复静止的第一帧会算出巨大的 dt
        # （把整段移动时间一次性加进悬停），举手挥一下就直接触发了。
        dt = 0.0 if self._last_call_at is None else now - self._last_call_at
        self._last_call_at = now

        if moving and raw is not None:
            self.n_motion_paused += 1
            return None, False

        if raw is None:
            # 短暂丢失：冻结计时，别把已经攒了几百毫秒的进度扔掉
            if self._cand is not None and now - self._last_raw_at <= self.grace:
                self.n_gap_tolerated += 1
                return None, False
            if self._cand is not None:
                self.n_gap_reset += 1
            self._cand, self.stable, self.progress = None, None, 0.0
            self._held = 0.0
            self._armed = True
            return None, False

        self._last_raw_at = now

        if raw != self._cand:
            self._cand, self._held, self.stable = raw, 0.0, None
            self.progress = 0.0
            self._armed = True
            self.n_restart += 1
            return None, False

        self._held += dt
        self.progress = min(1.0, self._held / self.hold) if self.hold > 0 else 1.0

        if self._held < self.hold:
            self.stable = None
            return None, False

        self.stable = raw
        if self._armed and now - self._fired_at.get(raw, -1e9) >= self.cooldown:
            self._fired_at[raw] = now
            self._armed = False          # 必须松手才重新武装
            self.n_fired += 1
            return raw, True
        return raw, False

    def reset(self) -> None:
        self._cand, self.stable, self.progress = None, None, 0.0
        self._held = 0.0
        self._last_call_at = None
        self._armed = True
        self._last_raw_at = 0.0
        self._fired_at.clear()


class SequenceDetector:
    """
    检测一串手势**按顺序**出现（例如：双手握拳 → 双手张开 → 握拳 → 张开）。

    用来做总开关这类"绝不能误触"的操作。三条约束缺一不可：
        1. 每一步都要**持续 dwell 秒**（默认 0.1）才算数 —— 分类器偶尔抖一帧，
           不能让序列乱跳；
        2. 相邻两步必须落在 window 秒内（默认 0.8）—— 所以是"快速连做"，
           慢慢比划不会触发；
        3. 完成后自动回到第一步，必须整串重做才认第二次。
    """

    def __init__(self, steps, dwell: float = 0.1, window: float = 0.8) -> None:
        self.steps = list(steps)
        self.dwell = dwell
        self.window = window
        self._i = 0                       # 已完成到第几步
        self._cand = None                 # 当前正在观察的候选状态
        self._cand_since = 0.0
        self._t = 0.0                     # 上一步完成的时刻

    @property
    def step(self) -> int:
        """已经完成了几步（用于 HUD 显示进度）。"""
        return self._i

    def update(self, value, now: float) -> bool:
        """value 为当前状态（可哈希，None 表示不满足）。返回 True = 整串完成。"""
        # 超时：这一步拖太久就从头来
        if self._i > 0 and now - self._t > self.window:
            self._i = 0
            self._cand = None

        if value is None:
            self._cand = None
            return False

        if value != self._cand:
            self._cand = value
            self._cand_since = now
            return False
        if now - self._cand_since < self.dwell:
            return False
        if value != self.steps[self._i]:
            return False

        self._i += 1
        self._t = now
        self._cand = None                 # 下一步必须"变过去"，同状态连着不算
        if self._i >= len(self.steps):
            self._i = 0
            return True
        return False

    def reset(self) -> None:
        self._i = 0
        self._cand = None


class StepAccumulator:
    """
    按**累计位移**出步：两手间距每变化 step_size（单位：掌长）才发一格。

    为什么不用 ContinuousMapper 做缩放：它每帧比一次，delta 一超过死区就出步，
    正常速度张开双手每帧都能出 1~2 格，一秒能滚 30 次——完全没法控制。
    改成累计位移后，"手要移动多远才缩放一格"变成可预期的物理量：
    step_size=0.35 掌长 ≈ 3cm，挪 3cm 才走一格。
    """

    def __init__(self, step_size: float = 0.35, max_steps: int = 3) -> None:
        self.step_size = step_size
        self.max_steps = max_steps
        self.prev: Optional[float] = None
        self.acc: float = 0.0

    def update(self, value: float) -> int:
        if self.prev is None:
            self.prev = value
            self.acc = 0.0
            return 0
        self.acc += value - self.prev
        self.prev = value
        n = int(self.acc / self.step_size)      # 朝零取整，攒够整格才走
        if n:
            self.acc -= n * self.step_size      # 余量留着，不浪费
            n = max(-self.max_steps, min(self.max_steps, n))
        return n

    def reset(self) -> None:
        self.prev = None
        self.acc = 0.0


class PinchTapDetector:
    """
    双手「快速捏合**两次**」= 重置网页缩放（像双击）。

    一次「捏合」的定义：双手同时捏住、持续时间 <= max_hold、且两手基本没拉开
    （真正的缩放一定伴随明显的间距变化，这条把缩放动作排除掉）。

    两次这样的捏合之间不超过 max_gap 秒，第二次松手的那一刻才触发重置。

    为什么要"两次"而不是一次：一次捏合太容易误触——刚开始缩放、临时想停手，
    捏一下就松开的情况很常见。两次快捏是有明确意图的动作。
    """

    def __init__(self, max_hold: float = 0.7, move_tol: float = 0.45,
                 max_gap: float = 1.0) -> None:
        self.max_hold = max_hold
        self.move_tol = move_tol
        self.max_gap = max_gap
        self._t0: Optional[float] = None
        self._d0: float = 0.0
        self._max_dev: float = 0.0
        self._last_tap_at: Optional[float] = None
        self.taps = 0          # 诊断：累计识别到几次有效捏合

    def _tap_ok(self, now: float) -> bool:
        if self._t0 is None:
            return False
        held = now - self._t0
        return held <= self.max_hold and self._max_dev <= self.move_tol

    def update(self, both_pinched: bool, distance: float, now: float) -> bool:
        """返回 True 表示"刚完成第二次快速捏合"，应当重置。"""
        if both_pinched:
            if self._t0 is None:
                self._t0, self._d0, self._max_dev = now, distance, 0.0
            else:
                self._max_dev = max(self._max_dev, abs(distance - self._d0))
            return False

        # 松手这一刻结算：这一次算不算有效捏合
        if self._t0 is None:
            return False
        ok = self._tap_ok(now)
        self._t0 = None
        if not ok:
            return False

        self.taps += 1
        if self._last_tap_at is not None and now - self._last_tap_at <= self.max_gap:
            self._last_tap_at = None      # 连上两次 -> 触发并清空
            return True
        self._last_tap_at = now
        return False

    def reset(self) -> None:
        self._t0 = None
        self._last_tap_at = None


class ContinuousMapper:
    """
    把双手距离的「相对变化」映射成步进量，用于音量/缩放这类连续控制。

    为什么用相对变化而不是绝对距离：手往前伸一点、摄像头换个角度，绝对距离
    就漂了。相对变化率天然免疫这种漂移，也自带平滑效果。
    """

    def __init__(self, gain: float = 14.0, max_step: float = 1.0, deadzone: float = 0.004) -> None:
        self.gain = gain
        self.max_step = max_step
        self.deadzone = deadzone
        self.prev: Optional[float] = None

    def update(self, value: float) -> float:
        if self.prev is None:
            self.prev = value
            return 0.0
        delta = value - self.prev
        if abs(delta) < self.deadzone:
            # 注意：这里**不更新 prev**，让微小变化累积起来。
            # 如果先更新 prev 再判断 deadzone，缓慢拉开时每帧的变化都会被吃掉，
            # 永远攒不够触发量 —— 只有猛拉才有反应，手感会很怪。
            return 0.0
        self.prev = value
        return float(np.clip(delta * self.gain, -self.max_step, self.max_step))

    def reset(self) -> None:
        self.prev = None
