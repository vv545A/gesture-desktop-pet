# -*- coding: utf-8 -*-
"""
手势引擎 —— 把「这一帧看到两只手」变成「用户想做一件事」。

它只做判定，**不做任何执行**，也不碰界面。输出是一个纯数据的 EngineOut，
谁拿走都行：主程序拿它发按键，桌宠拿它放动画，测试拿它做回归。

为什么要把这段逻辑从 main.py 里搬出来：
    main.py 里判定和执行是缠在一起的（`if fired: actions.run_action(...)`），
    想加"同一套手势驱动桌宠"就只能复制一遍。复制出去的判定逻辑一定会分叉，
    于是同一台电脑上两个程序的手感会慢慢变得不一样——这是维护噩梦的开始。

分层（判定的三道闸门，缺一不可）
    1. GestureSmoother  5 帧多数表决，压掉分类器的零星翻车
    2. MotionGate       手在动时冻结计时，挥手经过不会触发
    3. Debouncer        悬停够久才确认，且必须松手才能再触发
  这三层都住在 gestures.py 里，引擎只负责按正确顺序喂它们。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import gestures
from gestures import (
    ContinuousMapper,
    Debouncer,
    GestureSmoother,
    MotionGate,
    PinchTapDetector,
    SequenceDetector,
    StepAccumulator,
    analyze_two_hands,
    TwoHandState,
)
from hand_tracker import Hand

from .config import Config


@dataclass
class Fired:
    """一次确认过的手势（还没翻译成动作）。"""

    gesture: str
    side: str


@dataclass
class EngineOut:
    state: TwoHandState
    gesture: Optional[str] = None          # 当前单手候选（已表决）
    side: str = ""
    point: Optional[Tuple[float, float]] = None   # 归一化掌心位置（镜像后）
    progress: float = 0.0
    moving: bool = False
    speed: float = 0.0
    fired: List[Fired] = field(default_factory=list)
    continuous: List[Tuple[str, float]] = field(default_factory=list)  # ("zoom", n) 等
    toggled: Optional[bool] = None         # 本次是否切换了总开关


class GestureEngine:
    """无状态输入、按时间推进的手势判定器。必须在**单线程**里调用 update()。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # 阈值是模块级常量，命令行改过的值要在建对象之前写回去（同 main.py）
        gestures.PINCH_RATIO = cfg.pinch
        gestures.THUMB_AWAY = cfg.thumb

        self.deb = Debouncer(cfg.hold, cfg.cooldown, grace=cfg.grace)
        self.smoother = GestureSmoother(window=5, need=3)
        self.gate = MotionGate(max_speed=cfg.still_speed) if cfg.still_gate else None

        # 总开关：双手 ✊ → ✋ → ✊ → ✋（快速连做两遍）。
        # 单手做不出来，所以日常用 ✊ 静音、✋ 播放时不会误触。
        self.toggle_seq = SequenceDetector(
            [("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM"),
             ("FIST", "FIST"), ("OPEN_PALM", "OPEN_PALM")],
            window=cfg.toggle_window,
        )
        self.zoom_map = StepAccumulator(step_size=cfg.zoom_step)
        self.zoom_map_tap = PinchTapDetector()
        self.scroll_map = ContinuousMapper(gain=26.0, max_step=3.0, deadzone=0.004)

        self.enabled = True
        self._last_toggle = -1e9

    # ---------------------------------------------------------------- 开关
    def set_enabled(self, on: bool) -> bool:
        """外部（托盘/快捷键）开关。返回切换后的状态。"""
        self.enabled = bool(on)
        self._reset_all()
        self._last_toggle = 0.0
        return self.enabled

    def _reset_all(self) -> None:
        self.deb.reset()
        self.smoother.reset()
        if self.gate is not None:
            self.gate.reset()
        self.zoom_map.reset()
        self.scroll_map.reset()
        self.zoom_map_tap.reset()

    # ---------------------------------------------------------------- 主入口
    def update(self, hands: List[Hand], now: float) -> EngineOut:
        st = analyze_two_hands(hands)
        out = EngineOut(state=st)

        # 掌心位置：两只手取中点，一只手就是它自己（桌宠跟随用）
        if st.count == 1 and (st.left or st.right):
            c = (st.left or st.right).center
            out.point = (float(c[0]), float(c[1]))
        elif st.count == 2 and st.left is not None and st.right is not None:
            out.point = (float((st.left.center[0] + st.right.center[0]) / 2),
                         float((st.left.center[1] + st.right.center[1]) / 2))

        # ---------------- 安全：双手序列 -> 总开关 ----------------
        pair = (st.left_gesture, st.right_gesture) if st.count == 2 else None
        if self.toggle_seq.update(pair, now) and now - self._last_toggle > 2.0:
            self.enabled = not self.enabled
            self._last_toggle = now
            self.toggle_seq.reset()
            self._reset_all()
            out.toggled = self.enabled
            return out

        if not self.enabled:
            self.deb.update(None, now)
            return out

        if self.toggle_seq.step > 0:
            # 序列进行中：别执行普通手势。序列里有两步是"双手张开"，
            # 那正好也是滚动的起点，不掐掉的话做开关动作会顺手滚一页。
            self.deb.update(None, now)
            self.zoom_map.reset()
            self.scroll_map.reset()
            return out

        if st.count == 1:
            self._single(st, out, now)
        elif st.count == 2:
            self._double(st, out, now)
        else:
            self.deb.update(None, now)
            self.smoother.reset()
            self.zoom_map_tap.reset()
            self.zoom_map.reset()
            self.scroll_map.reset()
            if self.gate is not None:
                self.gate.reset()
        return out

    # ---------------------------------------------------------------- 单手
    def _single(self, st: TwoHandState, out: EngineOut, now: float) -> None:
        self.zoom_map_tap.reset()          # 只剩一只手，捏合双击作废
        hand = st.left or st.right
        side = "left" if st.left is not None else "right"
        g = st.left_gesture if st.left is not None else st.right_gesture

        if g == "UNKNOWN":
            g = None                       # 判不出来就别占着悬停计时
        g = self.smoother.update(g)
        out.gesture, out.side = g, side

        still = True
        if self.gate is not None:
            still = self.gate.update(now, hand)
            out.speed = self.gate.speed
        out.moving = not still

        stable, fired = self.deb.update(g, now, moving=not still)
        out.progress = self.deb.progress
        if fired and stable:
            out.fired.append(Fired(gesture=stable, side=side))

    # ---------------------------------------------------------------- 双手
    def _double(self, st: TwoHandState, out: EngineOut, now: float) -> None:
        self.deb.update(None, now)         # 双手在场时抑制单手指令
        if self.gate is not None:
            self.gate.reset()              # 双手模式不判静止

        l, r = st.left, st.right
        both_pinch = st.left_gesture == "PINCH" and st.right_gesture == "PINCH"
        both_open = st.left_gesture == "OPEN_PALM" and st.right_gesture == "OPEN_PALM"

        # 双手快捏两下 -> 缩放恢复 100%
        if self.zoom_map_tap.update(both_pinch, st.distance or 0.0, now):
            self.zoom_map.reset()
            out.continuous.append(("zoom_reset", 0.0))

        if both_pinch and st.distance:
            n = self.zoom_map.update(st.distance)
            if n:
                out.continuous.append(("zoom", float(n)))
        elif both_open:
            self.zoom_map.reset()
            mid_y = float((l.center[1] + r.center[1]) / 2)
            step = self.scroll_map.update(mid_y)
            if abs(step) > 0.08:
                out.continuous.append(("scroll", float(step)))
        else:
            self.zoom_map.reset()
            self.scroll_map.reset()

    # ---------------------------------------------------------------- 诊断
    def stats(self) -> dict:
        return {
            "fired": self.deb.n_fired,
            "gap_reset": self.deb.n_gap_reset,
            "gap_tolerated": self.deb.n_gap_tolerated,
            "restart": self.deb.n_restart,
            "motion_paused": self.deb.n_motion_paused,
        }
