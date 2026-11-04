# -*- coding: utf-8 -*-
"""
意图映射 —— 手势只是"形状"，这里才决定"意思"。

单独一层的理由：
    同一个 ✌️ 在朋友 A 那里是切换窗口，在朋友 B 那里可能是"召唤桌宠"。
    如果把映射写死在判定代码里，每次改主意都要动核心逻辑。
    现在改这张表就行（一行一个功能）。

**分工按手的数量来**：
    单手 = 操作窗口（✋最大化 / ✊最小化 / ✌️切窗口 / ☝️贴边 / 👍置顶）
    双手 = 音乐播放（两只手做同一个手势，是有仪式感的"第二层"，
           也和单手彻底分开，不会互相压到）

**单手有两条路，默认是"操作窗口"**：
    单手是高频动作，用户装上手势桌宠的第一诉求几乎都是"替我省几次
    找鼠标的工夫"，所以出厂状态必须是窗口操作。宠物互动（✌️跳舞 /
    ✊睡觉 / ✋唤醒 / ☝️跟随 / 🤏抚摸）本身很受欢迎，但它是"娱乐向"的，
    误触代价是宠物多跳一段舞——适合作为**主动切换**的第二条路，
    不适合替用户决定。
    两套表都在这里（`_WINDOW_SINGLE` / `_PET_SINGLE`），对照表页面顶部
    一键整套切换，不用逐行改 14 个下拉框。
    逐条改绑仍然支持 —— 预设只决定"一次改一整张"的快捷方式。

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


# ------------------------------------------------------------------ 单手：两张预设
# 左右手各写一份：对照表里能看到"左手/右手"两行，用户想给两只手设不同动作时
# 也有地方改（apply_bindings 支持 left:XXX 单独覆盖）。
SINGLE_HINT = "单手 = 操作窗口"

#: 单手能用的手势（对照表页面的左列按这个顺序排）
SINGLE_GESTURES = ("OPEN_PALM", "FIST", "VICTORY", "POINT",
                   "THUMB_UP", "THUMB_DOWN", "PINCH")
#: 双手能用的手势（捏合留给"总开关"，不算音乐手势）
PAIR_GESTURES = ("OPEN_PALM", "FIST", "VICTORY", "POINT",
                 "THUMB_UP", "THUMB_DOWN")

#: 手势的显示名。用中文而不是 emoji：Qt 在有些机器上根本没有 emoji 字体，
#: 显示成方块比不显示更糟。
GESTURE_LABEL = {
    "OPEN_PALM": "张开手掌",
    "FIST": "握拳",
    "VICTORY": "剪刀手",
    "POINT": "食指",
    "THUMB_UP": "点赞",
    "THUMB_DOWN": "踩",
    "PINCH": "捏合",
}

#: 动作目录 —— 对照表页面的下拉框就是这份。key 与 actions / router 里的动作键
#: 一一对应，value 是 (中文名, 表情)。加新动作只改这里和 actions.py。
ACTION_CATALOG: Dict[str, Tuple[str, str]] = {
    # ---- 窗口（单手主线）----
    "win_max": ("最大化 / 还原", "happy"),
    "win_min": ("最小化", "happy"),
    "win_topmost": ("置顶 / 取消置顶", "proud"),
    "win_snap_left": ("贴左半屏", "happy"),
    "win_snap_right": ("贴右半屏", "happy"),
    "win_desktop": ("收起所有窗口", "calm"),
    # ---- 媒体 / 键盘 ----
    "play_pause": ("播放 / 暂停", "happy"),
    "mute": ("静音", "calm"),
    "vol_up": ("音量 +", "happy"),
    "vol_down": ("音量 −", "calm"),
    "next_track": ("下一首", "happy"),
    "prev_track": ("上一首", "calm"),
    "alt_tab": ("切换窗口", "happy"),
    "page_down": ("下一页", "calm"),
    "page_up": ("上一页", "calm"),
    "escape": ("Esc 键", "calm"),
    "space": ("空格", "calm"),
    "enter": ("回车", "calm"),
    "scroll_up": ("向上滚", "calm"),
    "scroll_down": ("向下滚", "calm"),
    # ---- 只让桌宠反应，不碰系统（宠物互动主线，永远安全）----
    # 这五个是 GesturePet 的 MVP 主线：手势先让宠物有反应，
    # 系统副作用只留 👍 一个，且过白名单。
    "pet:dance": ("跳舞", "dance"),
    "pet:sleep": ("睡觉", "sleepy"),
    "pet:wake": ("唤醒", "happy"),
    "pet:follow": ("跟随鼠标", "listen"),
    "pet:pat": ("摸摸桌宠", "shy"),
    # ---- 带系统副作用的动作（受 BROWSER_ALLOWLIST 限制）----
    "open_browser": ("打开浏览器", "proud"),
}

_WINDOW_SINGLE: Dict[Tuple[str, str], Intent] = {
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

#: 另一条路：单手 = 逗桌宠。整套手势只让宠物有反应，只有 👍 会开浏览器
#: （过白名单，可关）。用户想"纯玩"时一键切过来，切回去也一样一键。
_PET_SINGLE: Dict[Tuple[str, str], Intent] = {
    # ✌️ 剪刀手 -> 跳舞。宠物最先要有的反应，也是五指里最好认的形状
    ("right", "VICTORY"): Intent("pet:dance", "跳舞", "看我跳一个！"),
    ("left", "VICTORY"): Intent("pet:dance", "跳舞", "看我跳一个！"),
    # ✊ 握拳 -> 睡觉。这是**持久状态**：睡着后不理手，直到 ✋ 唤醒它
    ("right", "FIST"): Intent("pet:sleep", "睡觉", "呼…先睡会儿", "sleepy"),
    ("left", "FIST"): Intent("pet:sleep", "睡觉", "呼…先睡会儿", "sleepy"),
    # ✋ 张开 -> 唤醒。顺带解除睡眠锁，让宠物重新跟手
    ("right", "OPEN_PALM"): Intent("pet:wake", "唤醒", "我醒啦！"),
    ("left", "OPEN_PALM"): Intent("pet:wake", "唤醒", "我醒啦！"),
    # ☝️ 食指 -> 切换「跟随鼠标」。再比一次或 ✊ 睡觉都会退出
    ("right", "POINT"): Intent("pet:follow", "跟随鼠标", "跟着你走～", "listen"),
    ("left", "POINT"): Intent("pet:follow", "跟随鼠标", "跟着你走～", "listen"),
    # 👍 点赞 -> 打开浏览器。**这张表里唯一带系统副作用的手势**，且过白名单
    ("right", "THUMB_UP"): Intent("open_browser", "打开浏览器", "帮你开个网页～",
                                  "proud"),
    ("left", "THUMB_UP"): Intent("open_browser", "打开浏览器", "帮你开个网页～",
                                 "proud"),
    # 👎 踩 -> 收起所有窗口。纯玩表里也留一个"收拾桌面"的后门，
    #          免得切过来之后连收窗口都得切回去
    ("right", "THUMB_DOWN"): Intent("win_desktop", "收起所有窗口", "都先让让…",
                                    "calm"),
    ("left", "THUMB_DOWN"): Intent("win_desktop", "收起所有窗口", "都先让让…",
                                   "calm"),
    # 🤏 捏合 -> 抚摸
    ("right", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
    ("left", "PINCH"): Intent("pet:pat", "抚摸", "嘿嘿…好痒", "shy"),
}

#: 出厂状态：单手 = 操作窗口
_DEFAULT_SINGLE: Dict[Tuple[str, str], Intent] = dict(_WINDOW_SINGLE)

#: 单手预设的显示名。顺序就是对照表页面里按钮的顺序（默认的那个在最前）。
SINGLE_PRESET_LABEL: Dict[str, str] = {"window": "操作窗口", "pet": "逗桌宠"}
SINGLE_PRESETS: Tuple[str, ...] = ("window", "pet")
_PRESET_TABLES = {"window": _WINDOW_SINGLE, "pet": _PET_SINGLE}

# ------------------------------------------------------------------ 双手：音乐
# 两只手做**同一个**手势才算数（左右不一样就什么也不做，见 engine._double）。
# 为什么不用"左手 A + 右手 B"的复合键：两只手各做不同手势时识别率明显下降，
# 而且记不住。同手势 → 一个功能，是唯一能被记住的双手玩法。
PAIR_HINT = "双手 = 音乐（两手同一个手势）"

_DEFAULT_PAIR: Dict[str, Intent] = {
    "OPEN_PALM": Intent("play_pause", "播放/暂停", "音乐，走你～"),
    "FIST": Intent("mute", "静音", "嘘——安静啦"),
    "THUMB_UP": Intent("vol_up", "音量+", "大声一点！"),
    "THUMB_DOWN": Intent("vol_down", "音量-", "小声一点…"),
    "VICTORY": Intent("next_track", "下一首", "换一首听听～"),
    "POINT": Intent("prev_track", "上一首", "回到上一首…"),
}

# 运行时生效的两张表。默认就是上面那两张的副本，apply_bindings() 会在副本上
# 改，所以"恢复默认"只要把副本重新 fill 一次，不会污染默认值。
_SINGLE: Dict[Tuple[str, str], Intent] = dict(_DEFAULT_SINGLE)
_PAIR: Dict[str, Intent] = dict(_DEFAULT_PAIR)

# 被用户明确设成"不绑定"的单手组合。
# 为什么需要这个集合：resolve() 对左手有一条 fallback（左手没写就用右手那套），
# 只从 _SINGLE 里删掉的话，左手会立刻继承右手，等于"关不掉"。
_DISABLED: set = set()

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
    if (side, gesture) in _DISABLED:
        return None                     # 用户明确关掉了这个手势
    return _SINGLE.get((side, gesture)) or _SINGLE.get(("right", gesture))


def continuous(kind: str) -> Optional[Intent]:
    return _CONTINUOUS.get(kind)


# ------------------------------------------------------------------ 手势绑定
# 用户在"手势对照表"页面里改的东西落在这里。
#
# 为什么是"覆盖层"而不是直接改 _SINGLE：
#     用户可能只改一两条，剩下的应该继续跟着默认表走。以后默认值升级
#     （比如把 ✋ 从"最大化"改成别的），没改过的条目会自动吃到新默认值，
#     而不是永远停在用户第一次运行时的快照上。

def binding_key(side: str, gesture: str) -> str:
    """(手, 手势) -> 配置里存的那把 key，例如 "right:OPEN_PALM" / "both:FIST"。"""
    return f"{side}:{gesture}"


#: 窗口/键盘动作的台词兜底表。
#: 这些动作默认已不绑在手势上（主线改成宠物互动了），但用户在对照表里
#: 把它们改绑回来时，仍然要有一句对应的台词——否则会出现"动作做了、
#: 桌宠一声不吭"的割裂感。_bubble_of 查不到默认表就落在这里。
_FALLBACK_BUBBLE: Dict[str, str] = {
    "win_max": "变——大！",
    "win_min": "先收起来～",
    "win_topmost": "钉在最上层！",
    "win_snap_left": "靠左边站好",
    "win_snap_right": "靠右边站好",
    "win_desktop": "都先让让…",
    "alt_tab": "换一个窗口～",
    "open_browser": "帮你开个网页～",
}


def _bubble_of(action: str) -> str:
    """同一个动作在默认表里的台词 —— 换绑之后台词也该跟着动作走。"""
    for table in (_DEFAULT_SINGLE.values(), _PET_SINGLE.values(), _PAIR.values()):
        for it in table:
            if it.key == action:
                return it.bubble
    return _FALLBACK_BUBBLE.get(action, "")


def _intent_for(action: str) -> Optional[Intent]:
    """动作 key -> Intent。目录里没有的 key 一律忽略（配置文件被手改坏了也不崩）。"""
    meta = ACTION_CATALOG.get(action)
    if meta is None:
        return None
    label, mood = meta
    return Intent(action, label, _bubble_of(action), mood)


def default_bindings() -> Dict[str, str]:
    """出厂状态：每一个 (手, 手势) 对应的动作。对照表页面的"恢复默认"用它。"""
    out: Dict[str, str] = {}
    for (side, g), it in _DEFAULT_SINGLE.items():
        out[binding_key(side, g)] = it.key
    for g, it in _DEFAULT_PAIR.items():
        out[binding_key("both", g)] = it.key
    return out


def current_bindings() -> Dict[str, str]:
    """现在实际生效的绑定（默认 + 用户改过的）。"""
    out: Dict[str, str] = {}
    for (side, g), it in _SINGLE.items():
        out[binding_key(side, g)] = it.key
    for g, it in _PAIR.items():
        out[binding_key("both", g)] = it.key
    return out


# ------------------------------------------------------------------ 单手预设
# 为什么要有"预设"这一层：
#     单手有 14 个格子（7 手势 × 左右手）。想从"操作窗口"换到"逗桌宠"，
#     逐个点 14 次下拉框是纯体力活，而且改到第 9 个时必然会忘记前面改过什么。
#     预设 = 一次性把整张表换掉，两条路才真的都是"路"，不是"要折腾半天"。

def preset_bindings(name: str) -> Dict[str, str]:
    """某一套单手预设 -> {binding_key: action}。未知名字返回空字典。"""
    table = _PRESET_TABLES.get(name)
    if table is None:
        return {}
    return {binding_key(side, g): it.key for (side, g), it in table.items()}


def current_single_preset() -> str:
    """
    现在生效的单手表**更像**哪套预设。

    判据是"命中多少个动作"而不是"是否完全相等"：用户可能只改了其中一条，
    这时仍应高亮原来那套（他只微调，没换赛道）。完全不像任何一套时返回 ""，
    界面上两个按钮都不选中 —— 诚实地说"这是自定义"。
    """
    cur = current_bindings()
    best, best_hit = "", -1
    for name in SINGLE_PRESETS:
        rows = preset_bindings(name)
        hit = sum(1 for k, v in rows.items() if cur.get(k) == v)
        if hit > best_hit:
            best, best_hit = name, hit
    # 至少命中一半才算"这套预设"，否则就是用户自己配的
    return best if best_hit * 2 >= len(_PRESET_TABLES[best]) else ""


def apply_bindings(overrides: Optional[Dict[str, str]]) -> None:
    """
    把用户的覆盖应用到运行时表上。

    overrides 里"没有出现"的键 = 用默认；值为空串 = 明确取消这个手势
    （页面上选"不绑定"）。动作名拼错/已被删掉时**保持默认**而不是清空：
    配置文件被手改坏不该让一个手势凭空失效。
    """
    _SINGLE.clear()
    _SINGLE.update(_DEFAULT_SINGLE)
    _PAIR.clear()
    _PAIR.update(_DEFAULT_PAIR)
    _DISABLED.clear()

    for key, action in (overrides or {}).items():
        side, _, gesture = str(key).partition(":")
        if side not in ("left", "right", "both") or not gesture:
            continue
        action = str(action or "")

        if side == "both":
            if not action:
                _PAIR.pop(gesture, None)
                continue
            it = _intent_for(action)
            if it is not None:
                _PAIR[gesture] = it
            continue

        if not action:
            _SINGLE.pop((side, gesture), None)
            _DISABLED.add((side, gesture))
            continue
        it = _intent_for(action)
        if it is not None:
            _SINGLE[(side, gesture)] = it
            _DISABLED.discard((side, gesture))


def describe_action(action: str) -> str:
    """动作 key -> 中文名（日志 / 提示用）。空串表示不绑定。"""
    if not action:
        return "不绑定"
    return ACTION_CATALOG.get(action, (action, "happy"))[0]


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
