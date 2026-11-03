# -*- coding: utf-8 -*-
"""
石头剪刀布 —— 纯逻辑，不依赖 Qt、摄像头、线程。

为什么单独成模块而不是写进 window：
    这个游戏的规则（谁赢谁、几比几、什么时候该倒数）和界面完全无关。
    放进窗口类里，就只能靠截图验证；留在这里，`tests/test_rps.py` 可以
    用假时钟把一整局走完——时钟是参数，不是 time.monotonic()。

为什么要"倒数"：
    手势是**持续动作**，没有天然的"出拳瞬间"。如果随时比一下就算数，
    玩家举着手犹豫的两秒里会被判好几次。所以规则是：倒数结束后的时间窗内，
    第一个稳定的手势算作这一轮的出拳，只认一次。

为什么宠物要随机而不是"看着玩家出"：
    能作弊的对手没人愿意玩第二局。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: 三个能出的手势。刻意选这三个：它们形状差异最大，识别率最高，
#: 而且正好对应"拳头=石头、剪刀=剪刀、张开=布"的直觉。
CHOICES: Tuple[str, ...] = ("FIST", "VICTORY", "OPEN_PALM")

LABEL: Dict[str, str] = {
    "FIST": "石头",
    "VICTORY": "剪刀",
    "OPEN_PALM": "布",
}

#: 谁能赢谁：FIST 赢 VICTORY，VICTORY 赢 OPEN_PALM，OPEN_PALM 赢 FIST
BEATS: Dict[str, str] = {
    "FIST": "VICTORY",
    "VICTORY": "OPEN_PALM",
    "OPEN_PALM": "FIST",
}

WIN, LOSE, DRAW = 0, 1, 2


@dataclass(frozen=True)
class Round:
    """一轮的结果。frozen 是刻意的：界面拿到后不该再改它。"""

    player: str                 # 玩家出的手势
    pet: str                    # 宠物出的手势
    result: int                 # WIN / LOSE / DRAW
    score: Tuple[int, int, int]  # (赢, 输, 平)

    @property
    def player_label(self) -> str:
        return LABEL.get(self.player, self.player)

    @property
    def pet_label(self) -> str:
        return LABEL.get(self.pet, self.pet)

    @property
    def score_text(self) -> str:
        return f"{self.score[0]} : {self.score[1]}"


@dataclass
class Snapshot:
    """一帧游戏状态，给界面画倒数/提示用。"""

    phase: str = "idle"         # idle / countdown / armed / result
    count: int = 0              # 倒数剩余秒数（countdown 阶段）
    round: Optional[Round] = None
    score: Tuple[int, int, int] = (0, 0, 0)
    active: bool = False


class RPSGame:
    """
    状态机：idle → countdown → armed → result → countdown → …

    armed 是唯一接受输入的状态。其它状态下来的手势一律忽略，
    这样玩家在倒计时里提前比手势不会"抢跑"。
    """

    COUNTDOWN = 3.0        # 倒数时长（秒）
    ARM_TIMEOUT = 6.0      # armed 多久没出手就重来，避免卡死在等手势
    RESULT_HOLD = 2.2      # 出结果后停留多久，让玩家看清输赢
    ROUNDS_TO_WIN = 5      # 先赢 5 局算赢下整场（0 = 不限）

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self._rng = rng or random.Random()
        self.active = False
        self.phase = "idle"
        self.score: List[int] = [0, 0, 0]
        self.round: Optional[Round] = None
        self._t0 = 0.0              # 当前阶段开始时刻
        self._rounds = 0

    # ---------------------------------------------------------------- 生命周期
    def start(self, now: float) -> None:
        self.active = True
        self.phase = "countdown"
        self.score = [0, 0, 0]
        self.round = None
        self._rounds = 0
        self._t0 = now

    def stop(self) -> None:
        self.active = False
        self.phase = "idle"
        self.round = None

    # ---------------------------------------------------------------- 每帧
    def tick(self, now: float) -> Snapshot:
        """
        按时间推进状态机。**每秒被调用 30 次**，所以这里不能做任何耗时的事。

        返回的快照是纯数据，界面拿它决定画什么，不反过来改游戏状态。
        """
        if not self.active:
            return Snapshot(active=False, score=tuple(self.score))

        elapsed = now - self._t0

        if self.phase == "countdown":
            left = self.COUNTDOWN - elapsed
            if left <= 0:
                self.phase = "armed"
                self._t0 = now
                return Snapshot("armed", 0, None, tuple(self.score), True)
            return Snapshot("countdown", int(math.ceil(left)), None,
                            tuple(self.score), True)

        if self.phase == "armed":
            if elapsed > self.ARM_TIMEOUT:
                # 一直没出手：回到倒数，别让宠物干等着
                self.phase = "countdown"
                self._t0 = now
            return Snapshot(self.phase, 0, None, tuple(self.score), True)

        if self.phase == "result" and elapsed > self.RESULT_HOLD:
            self.phase = "countdown"
            self._t0 = now
            self.round = None

        return Snapshot(self.phase, 0, self.round, tuple(self.score), True)

    # ---------------------------------------------------------------- 出拳
    def play(self, gesture: str, now: float) -> Optional[Round]:
        """
        玩家出拳。只有 armed 状态、且是三种有效手势之一才算数。

        返回 None 表示"这一下不算"（提前比了、或者比了个无关手势）——
        调用方据此决定要不要提示，而不是静默吞掉。
        """
        if not self.active or self.phase != "armed":
            return None
        if gesture not in CHOICES:
            return None

        pet = self._rng.choice(CHOICES)
        if gesture == pet:
            result = DRAW
        elif BEATS[gesture] == pet:
            result = WIN
        else:
            result = LOSE

        self.score[result] += 1
        self._rounds += 1
        self.round = Round(gesture, pet, result, tuple(self.score))
        self.phase = "result"
        self._t0 = now
        return self.round

    # ---------------------------------------------------------------- 查询
    def match_over(self) -> Optional[bool]:
        """整场是否结束。None = 还没结束（或没设局数上限）。"""
        if self.ROUNDS_TO_WIN <= 0:
            return None
        if self.score[WIN] >= self.ROUNDS_TO_WIN:
            return True
        if self.score[LOSE] >= self.ROUNDS_TO_WIN:
            return False
        return None
