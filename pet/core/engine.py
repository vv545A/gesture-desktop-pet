# -*- coding: utf-8 -*-
"""
手势引擎 —— 把「这一帧看到几只手、什么手势」变成「用户想做一件事」。

它只做判定，**不做任何执行**，也不碰界面。输出是一个纯数据的 EngineOut，
谁拿走都行：主程序拿它发按键，桌宠拿它放动画，测试拿它做回归。

为什么要把这段逻辑从 main.py 里搬出来：
    main.py 里判定和执行是缠在一起的（`if fired: actions.run_action(...)`），
    想加"同一套手势驱动桌宠"就只能复制一遍。复制出去的判定逻辑一定会分叉，
    于是同一台电脑上两个程序的手感会慢慢变得不一样——这是维护噩梦的开始。

两只手走两条完全不同的路（2026-11 重构）
    1 只手 → 窗口操作：离散命令，走「多数表决 → 静止门控 → 悬停确认」三道闸门
    2 只手 → 音乐控制：两只手做**同一个**手势才算数，走「配对表决 → 悬停确认」，
             并且要求左右手势一致（不一致什么都不做，避免"路过一只手"乱触发）
    双手同时捏合保持约 1 秒 → 总开关（紧急停止）

    以前双手做的是"捏合后拉开 = 网页缩放"，那是个连续量：两只手必须同时捏住
    并同步移动，摄像头丢一帧整段位移就清零，实测基本做不出来。现在双手只做
    离散的同手势配对，抗噪能力高一个数量级——这才是双手真正可用的形态。
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
    analyze_two_hands,
    TwoHandState,
)
from hand_tracker import Hand

from .config import Config


@dataclass
class Fired:
    """一次确认过的手势（还没翻译成动作）。side 为 "both" 表示双手。"""

    gesture: str
    side: str


@dataclass
class EngineOut:
    state: TwoHandState
    gesture: Optional[str] = None          # 当前单手候选（已表决）
    side: str = ""
    pair: Optional[str] = None             # 双手共同的候选手势（已表决）
    point: Optional[Tuple[float, float]] = None   # 归一化掌心位置（镜像后）
    progress: float = 0.0                  # 悬停进度：单手看 deb，双手看配对/捏合保持
    moving: bool = False
    speed: float = 0.0
    fired: List[Fired] = field(default_factory=list)
    continuous: List[Tuple[str, float]] = field(default_factory=list)  # 预留（缩放/滚动）
    toggled: Optional[bool] = None         # 本次是否切换了总开关


class GestureEngine:
    """无状态输入、按时间推进的手势判定器。必须在**单线程**里调用 update()。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # 阈值是模块级常量，命令行改过的值要在建对象之前写回去（同 main.py）
        gestures.PINCH_RATIO = cfg.pinch
        gestures.THUMB_AWAY = cfg.thumb

        # 单手：三道闸门
        self.deb = Debouncer(cfg.hold, cfg.cooldown, grace=cfg.grace)
        self.smoother = GestureSmoother(window=5, need=3)
        self.gate = MotionGate(max_speed=cfg.still_speed) if cfg.still_gate else None

        # 双手：配对也要表决 + 悬停确认。
        # 以前双手没有任何滤波（直接读每帧 recognize 的结果），一帧翻车就
        # 把累计状态清掉，这是"双手操作实现不了"的真正原因，不只是手感问题。
        self.deb_pair = Debouncer(cfg.pair_hold, cfg.pair_cooldown, grace=cfg.grace)
        self.pair_smoother = GestureSmoother(window=5, need=3)

        # 双手张开上下移动 = 网页/文档滚动。
        # 用**相对变化**而不是绝对位置：手往前伸一点、摄像头换个角度，绝对坐标
        # 就漂了；相对变化天然免疫这种漂移，还自带平滑。
        self.scroll_map = ContinuousMapper(gain=26.0, max_step=3.0,
                                           deadzone=0.004)

        self.enabled = True
        self._last_toggle = -1e9

        # 游戏模式的手势拦截器。返回 True = "这一下被游戏吃掉了"，
        # 不再走正常映射——否则玩石头剪刀布时比个 ✊，既出了石头又让它睡觉。
        # 用回调而不是让引擎直接持有 RPSGame：core 不该反过来依赖玩法层，
        # headless 模式和单元测试也就不必拖上游戏代码。
        self._game_hook = None
        self._pinch_since: Optional[float] = None   # 双手捏合开始计时点

    # ---------------------------------------------------------------- 开关
    def set_enabled(self, on: bool) -> bool:
        """外部（托盘/桌宠按钮/面板/双手捏合）开关。返回切换后的状态。"""
        self.enabled = bool(on)
        self._reset_all()
        self._last_toggle = 0.0
        return self.enabled

    def _reset_all(self) -> None:
        self.deb.reset()
        self.smoother.reset()
        self.deb_pair.reset()
        self.pair_smoother.reset()
        self.scroll_map.reset()
        self._pinch_since = None
        if self.gate is not None:
            self.gate.reset()

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

        # ---------------- 安全：双手捏合保持 -> 总开关 ----------------
        # 必须两只手同时捏住并且**保持**一会儿：这是紧急停止，绝不能挥手就触发。
        # 放在 enabled 判断之前，所以关掉之后还能用同一招开回来。
        both_pinch = (st.count == 2 and st.left_gesture == "PINCH"
                      and st.right_gesture == "PINCH")
        if both_pinch:
            if self._pinch_since is None:
                self._pinch_since = now
            held = now - self._pinch_since
            out.progress = min(1.0, held / max(self.cfg.toggle_hold, 1e-6))
            if held >= self.cfg.toggle_hold and now - self._last_toggle > 2.0:
                self.enabled = not self.enabled
                self._last_toggle = now
                self._reset_all()
                out.toggled = self.enabled
                return out
        else:
            self._pinch_since = None

        if not self.enabled:
            self.deb.update(None, now)
            self.deb_pair.update(None, now)
            return out

        if st.count == 1:
            self._single(st, out, now)
        elif st.count == 2:
            self._double(st, out, now)
        else:
            self.deb.update(None, now)
            self.smoother.reset()
            self.deb_pair.reset()
            self.pair_smoother.reset()
            if self.gate is not None:
                self.gate.reset()

        # 游戏模式：确认过的手势先给游戏过一遍，没被吃掉的才当正常意图。
        # 放在最后统一处理，而不是塞进 _single/_double 里——那两处各有分支，
        # 漏一条就会出现"玩着游戏还在切窗口"。
        if self._game_hook is not None and out.fired:
            out.fired = [f for f in out.fired
                         if not self._game_hook(f.gesture, now)]

        return out

    # ---------------------------------------------------------------- 游戏模式
    def set_game_hook(self, hook) -> None:
        """挂上/摘掉手势拦截器。传 None = 退出游戏，手势恢复正常含义。"""
        self._game_hook = hook

    # ---------------------------------------------------------------- 单手：窗口
    def _single(self, st: TwoHandState, out: EngineOut, now: float) -> None:
        # 只剩一只手：双手配对作废（否则下次举起两只手会继承旧进度）
        self.deb_pair.reset()
        self.pair_smoother.reset()

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

    # ---------------------------------------------------------------- 双手：音乐
    def _double(self, st: TwoHandState, out: EngineOut, now: float) -> None:
        self.deb.update(None, now)         # 双手在场时抑制单手指令
        if self.gate is not None:
            self.gate.reset()              # 双手模式不判静止

        l_g, r_g = st.left_gesture, st.right_gesture

        # 双手捏合是总开关专用（上面已计时），这里不参与音乐配对
        if l_g == "PINCH" and r_g == "PINCH":
            self.deb_pair.update(None, now)
            self.pair_smoother.reset()
            out.pair = None
            return

        # ---- 双手张开并上下移动 = 网页 / 文档滚动 ----
        # 张开**不动**仍然归"播放/暂停"（走下面的配对逻辑），只有真的产生位移
        # 才转成滚动。不加这个区分的话，想滚一下页面，音乐会被顺手停掉。
        if l_g == "OPEN_PALM" and r_g == "OPEN_PALM" \
                and st.left is not None and st.right is not None:
            mid_y = float((st.left.center[1] + st.right.center[1]) / 2)
            step = self.scroll_map.update(mid_y)
            if abs(step) > 0.08:
                out.continuous.append(("scroll", float(step)))
                # 移动中不判配对，否则"边滚边触发播放/暂停"
                self.deb_pair.update(None, now)
                self.pair_smoother.reset()
                out.pair = None
                return
        else:
            self.scroll_map.reset()

        # 只认"两只手同一个手势"：左右不一致就当作没有意图。
        # 这是刻意的取舍 —— 一只手路过画面时不该改变正在播放的音乐。
        common = l_g if (l_g == r_g and l_g and l_g != "UNKNOWN") else None
        voted = self.pair_smoother.update(common)
        out.pair = voted or common

        stable, fired = self.deb_pair.update(voted, now)
        out.progress = self.deb_pair.progress
        if fired and stable:
            out.fired.append(Fired(gesture=stable, side="both"))

    # ---------------------------------------------------------------- 诊断
    def stats(self) -> dict:
        return {
            "fired": self.deb.n_fired,
            "pair_fired": self.deb_pair.n_fired,
            "gap_reset": self.deb.n_gap_reset,
            "gap_tolerated": self.deb.n_gap_tolerated,
            "restart": self.deb.n_restart,
            "motion_paused": self.deb.n_motion_paused,
        }
