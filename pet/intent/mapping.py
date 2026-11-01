# -*- coding: utf-8 -*-
"""
意图映射 —— 手势只是"形状"，这里才决定"意思"。

单独一层的理由：
    同一个 ✌️ 在朋友 A 那里是切换窗口，在朋友 B 那里可能是"召唤桌宠"。
    如果把映射写死在判定代码里，每次改主意都要动核心逻辑。
    现在改这张表就行（一行一个功能）。

**分工按手的数量来**（2026-11 调整）：
    单手 = 操作窗口（这是"隔空操控电脑"的主线，一个手就能做完，最稳）
    双手 = 音乐播放（两只手做同一个手势，是有仪式感的"第二层"，
           也和单手彻底分开，不会互相压到）

为什么把窗口操作放在单手：
    双手做「连续量」（捏合后拉开缩放）在真实摄像头上很难成立——两只手要
    同时保持捏合并同步移动，丢一帧就断。而窗口操作是**离散命令**，单手
    悬停 0.5 秒就够，抗噪得多。双手这边只留同手势配对，两个手都做 FIST
    才静音，两只手一起出现才算数，误触概率很低。

Intent.key 的四种取值：
    actions.WINDOW_ACTIONS 里的窗口动作   （win_max / win_snap_left …）
    actions.ACTIONS 里的按键动作          （alt_tab / play_pause / next_track …）
    "zoom"/"zoom_reset"/"scroll"          连续量动作（当前未接手势，动作实现仍保留）
    "pet:xxx"                             只让桌宠反应，不发任何系统事件（最安全）
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


# ------------------------------------------------------------------ 单手：窗口
# 左右手分开写：☝️ 指哪边、窗口就贴哪边——这是"区分左右手"最直观的用法。
SINGLE_HINT = "单手 = 操作窗口"

_SINGLE: Dict[Tuple[str, str], Intent] = {
    # ✋ 张开 -> 最大化 / 还原（同一个手势，按窗口当前状态决定做哪一半）
    ("right", "OPEN_PALM"): Intent("win_max", "最大化/还原", "变——大！"),
    ("left", "OPEN_PALM"): Intent("win_max", "最大化/还原", "变——大！"),
    # ✊ 握拳 -> 最小化
    ("right", "FIST"): Intent("win_min", "最小化", "先收起来～"),
    ("left", "FIST"): Intent("win_min", "最小化", "先收起来～"),
    # ✌️ 剪刀手 -> 切换窗口
    ("right", "VICTORY"): Intent("alt_tab", "切换窗口", "换一个窗口～"),
    ("left", "VICTORY"): Intent("alt_tab", "切换窗口", "换一个窗口～"),
    # ☝️ 食指 -> 贴左/右半屏（左手贴左、右手贴右，指哪边贴哪边）
    ("left", "POINT"): Intent("win_snap_left", "贴左半屏", "靠左边站好"),
    ("right", "POINT"): Intent("win_snap_right", "贴右半屏", "靠右边站好"),
    # 👍 点赞 -> 置顶 / 取消置顶
    ("right", "THUMB_UP"): Intent("win_topmost", "窗口置顶", "钉在最上层！"),
    ("left", "THUMB_UP"): Intent("win_topmost", "窗口置顶", "钉在最上层！"),
    # 👎 踩 -> 全部收起（显示桌面，再比一次就回来）
    ("right", "THUMB_DOWN"): Intent("win_desktop", "收起所有窗口", "都先让让…"),
    ("left", "THUMB_DOWN"): Intent("win_desktop", "收起所有窗口", "都先让让…"),
    # 🤏 捏合不驱动任何系统功能，只让桌宠有反应 ——
    # 这是"氛围感优先、绝不添乱"的示范：捏一下就是撸它一下。
    ("right", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
    ("left", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
}

# ------------------------------------------------------------------ 双手：音乐
# 两只手做**同一个**手势才算数（左右不一样就什么也不做，见 engine._double）。
# 为什么不用"左手 A + 右手 B"的复合键：两只手各做不同手势时识别率明显下降，
# 而且记不住。同手势 → 一个功能，是唯一能被记住的双手玩法。
PAIR_HINT = "双手 = 音乐（两手同一个手势）"

_PAIR: Dict[str, Intent] = {
    "OPEN_PALM": Intent("play_pause", "播放/暂停", "音乐，走你～"),
    "FIST": Intent("mute", "静音", "嘘——安静啦"),
    "THUMB_UP": Intent("vol_up", "音量+", "大声一点！"),
    "THUMB_DOWN": Intent("vol_down", "音量-", "小声一点…"),
    "VICTORY": Intent("next_track", "下一首", "换一首听听～"),
    "POINT": Intent("prev_track", "上一首", "回到上一首…"),
}

# ------------------------------------------------------------------ 连续量
# 缩放/滚动这一路当前**没有接手势**（双手留给音乐了），但动作实现完整保留：
# 想加回来，在 engine 里发 continuous("zoom", n) 就行，界面这边不用改。
_CONTINUOUS: Dict[str, Intent] = {
    "zoom": Intent("zoom", "缩放", "", "surprised"),
    "zoom_reset": Intent("zoom_reset", "缩放复位", "恢复原样！", "proud"),
    "scroll": Intent("scroll", "滚动", "", "calm"),
}

# ------------------------------------------------------------------ 动态台词
# 窗口动作的结果是"做完才知道"的（最大化 vs 还原、置顶 vs 取消），
# 所以台词不能写死在 Intent 里，得按结果码现查。桌宠的说法要贴着结果走，
# 不然会出现"窗口缩回去了，它却在喊变大啦"这种离谱反馈。
_WIN_BUBBLE: Dict[Tuple[str, str], str] = {
    ("win_max", "max"): "铺满整屏！",
    ("win_max", "restore"): "缩回来啦～",
    ("win_min", "min"): "先躲起来…",
    ("win_topmost", "top"): "钉住！谁也挡不住",
    ("win_topmost", "untop"): "放回普通层～",
    ("win_snap_left", "left"): "靠左边站好",
    ("win_snap_right", "right"): "靠右边站好",
    ("win_desktop", "desktop"): "桌面清空——再比一次就回来",
}


def window_bubble(intent_key: str, result: str) -> str:
    """窗口动作的结果 -> 那句台词。查不到就返回空串（沿用 Intent 的默认台词）。"""
    if result in ("no_window", "unknown", "failed"):
        return "这儿没有能动的窗口…先点一下目标窗口"
    return _WIN_BUBBLE.get((intent_key, result), "")


def resolve(side: str, gesture: str) -> Optional[Intent]:
    """(手, 手势) -> 意图。side 为 "both" 时查双手表。查不到返回 None。"""
    if side == "both":
        return _PAIR.get(gesture)
    return _SINGLE.get((side, gesture)) or _SINGLE.get(("right", gesture))


def continuous(kind: str) -> Optional[Intent]:
    return _CONTINUOUS.get(kind)


def _pad(text: str, width: int) -> str:
    """
    按**显示宽度**补空格（中文算 2 列）。

    用 str.ljust 的话，"最小化" 和 "最小化/还原" 都按 1 字符算，表就歪了 ——
    这张表要直接打到控制台给人看，歪掉比没对齐更难受。
    """
    import unicodedata

    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - w)


def describe_table() -> str:
    """给 --list-intents / 托盘"手势对照表"用：一眼看清现在每个手势干了什么。"""
    rows = [SINGLE_HINT, _pad("手", 4) + _pad("手势", 16) + _pad("动作", 18) + "桌宠台词"]
    for (side, g), it in sorted(_SINGLE.items()):
        rows.append(_pad("左" if side == "left" else "右", 4)
                    + _pad(g, 16) + _pad(it.label, 18) + it.bubble)
    rows.append("")
    rows.append(PAIR_HINT)
    rows.append(_pad("手势", 16) + _pad("动作", 18) + "桌宠台词")
    for g, it in _PAIR.items():
        rows.append(_pad(g, 16) + _pad(it.label, 18) + it.bubble)
    rows.append("")
    rows.append("双手同时捏合 保持约 1 秒 = 手势控制开关（紧急停止）")
    return "\n".join(rows)
