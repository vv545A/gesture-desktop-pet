# -*- coding: utf-8 -*-
"""小游戏。都是纯逻辑，不依赖 Qt / 摄像头 / 线程。"""

from .rps import BEATS, CHOICES, LABEL, RPSGame, Round, Snapshot

__all__ = ["RPSGame", "Round", "Snapshot", "CHOICES", "LABEL", "BEATS"]
