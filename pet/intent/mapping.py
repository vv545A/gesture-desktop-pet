# -*- coding: utf-8 -*-
"""
意图映射 —— 手势只是"形状"，这里才决定"意思"。

单独一层的理由：
    同一个 ✌️ 在朋友 A 那里是切换窗口，在朋友 B 那里可能是"召唤桌宠"。
    如果把映射写死在判定代码里，每次改主意都要动核心逻辑。
    现在改 `pet_config.json` 之外的映射表（或将来做成配置）就行。

Intent.key 的三种取值：
    actions.ACTIONS 里的名字   发给 Windows 的离散动作
    "zoom"/"zoom_reset"/"scroll" 连续量动作（缩放、滚动）
    "pet:xxx"                 只让桌宠反应，不发任何系统事件（最安全的一类）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Intent:
    key: str        # 见模块注释
    label: str      # 短标签（日志 / 状态条）
    bubble: str     # 桌宠说的话
    mood: str = "happy"


# ------------------------------------------------------------------ 单手
# 左右手分开写：同一种手势在两只手上做不同的事，这才对得起"区分左右手"。
_SINGLE: Dict[Tuple[str, str], Intent] = {
    ("right", "OPEN_PALM"): Intent("play_pause", "播放/暂停", "音乐，走你～"),
    ("left", "OPEN_PALM"): Intent("play_pause", "播放/暂停", "音乐，走你～"),
    ("right", "FIST"): Intent("mute", "静音", "嘘——安静啦"),
    ("left", "FIST"): Intent("mute", "静音", "嘘——安静啦"),
    ("right", "THUMB_UP"): Intent("vol_up", "音量+", "大声一点！"),
    ("left", "THUMB_UP"): Intent("vol_up", "音量+", "大声一点！"),
    ("right", "THUMB_DOWN"): Intent("vol_down", "音量-", "小声一点…"),
    ("left", "THUMB_DOWN"): Intent("vol_down", "音量-", "小声一点…"),
    ("right", "VICTORY"): Intent("alt_tab", "切换窗口", "换一个窗口～"),
    ("left", "VICTORY"): Intent("alt_tab", "切换窗口", "换一个窗口～"),
    ("right", "POINT"): Intent("page_down", "下一页", "往下翻～"),
    ("left", "POINT"): Intent("page_up", "上一页", "往上翻～"),
    # 单手捏合不驱动任何系统功能，只让桌宠有反应 ——
    # 这是"氛围感优先、绝不添乱"的示范：捏一下就是撸它一下。
    ("right", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
    ("left", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
}

# ------------------------------------------------------------------ 连续量
# 缩放/滚动是高频的，不弹气泡（会刷屏），只让桌宠抖一下。
_CONTINUOUS: Dict[str, Intent] = {
    "zoom": Intent("zoom", "缩放", "", "surprised"),
    "zoom_reset": Intent("zoom_reset", "缩放复位", "恢复原样！", "proud"),
    "scroll": Intent("scroll", "滚动", "", "calm"),
}


def resolve(side: str, gesture: str) -> Optional[Intent]:
    """(手, 手势) -> 意图。查不到就返回 None（说明这个手势还没分配功能）。"""
    return _SINGLE.get((side, gesture)) or _SINGLE.get(("right", gesture))


def continuous(kind: str) -> Optional[Intent]:
    return _CONTINUOUS.get(kind)


def describe_table() -> str:
    """给 --list-intents 用：一眼看清现在每个手势干了什么。"""
    rows = [f"{'手':<4}{'手势':<12}{'动作':<10}桌宠台词"]
    for (side, g), it in sorted(_SINGLE.items()):
        rows.append(f"{('左' if side == 'left' else '右'):<5}{g:<14}{it.label:<12}{it.bubble}")
    return "\n".join(rows)
