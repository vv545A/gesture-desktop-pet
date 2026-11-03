# -*- coding: utf-8 -*-
"""
系统动作执行层 —— 直接调 Windows API，不依赖 pyautogui / pynput。

为什么不用 pyautogui：
    它发不了媒体键（播放/暂停、音量加减、静音），而这几个恰好是手势控制里
    最高频的操作。媒体键只能用虚拟键码 VK 走 keybd_event。
    顺带还省掉了 pyautogui 的屏幕尺寸检测和「故障安全」机制（指针甩到角落
    就抛异常），那种行为在手势场景里只会添乱。

注意：所有动作作用于当前前台窗口。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

# 平台守卫：按键注入（keybd_event / mouse_event）只有 Windows 有。
# 这里**故意不抛异常**——桌宠的窗口、动画、手势识别全是跨平台的，
# 只有"往系统里发按键"这一层做不到。缺了它也应当能跑，只是动作降级成
# "识别出来了但发不出去"，而不是整个程序拒绝启动。
WINDOWS = sys.platform == "win32"
user32 = ctypes.windll.user32 if WINDOWS else None


def has_system_actions() -> bool:
    """当前平台能不能真的发按键。给界面/日志用，避免"看起来能用其实没用"。"""
    return user32 is not None

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120          # Windows 标准滚轮一格

# 虚拟键码
VK = {
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "prev_track": 0xB1,
    "vol_up": 0xAF,
    "vol_down": 0xAE,
    "vol_mute": 0xAD,
    "tab": 0x09,
    "alt": 0x12,
    "ctrl": 0x11,
    "shift": 0x10,
    "win": 0x5B,
    "escape": 0x1B,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "space": 0x20,
    "enter": 0x0D,
    "pageup": 0x21,
    "pagedown": 0x22,
    "0": 0x30,
    "d": 0x44,
    "plus": 0xBB,
    "minus": 0xBD,
}


def _down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def _up(vk: int) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def tap(vk: int, times: int = 1, interval: float = 0.008) -> None:
    """敲击一个键若干次。"""
    for _ in range(max(1, int(times))):
        _down(vk)
        _up(vk)
        if interval:
            time.sleep(interval)


def combo(modifier: int, key: int, hold: float = 0.02) -> None:
    """组合键，如 Alt+Tab。必须按住修饰键再敲，松开修饰键才生效。"""
    _down(modifier)
    time.sleep(hold)
    tap(key)
    time.sleep(hold)
    _up(modifier)


def scroll(steps: float) -> None:
    """滚动滚轮。正数向上，负数向下，1 步 = 一格的 1/3。"""
    if not WINDOWS:
        return
    delta = int(steps * WHEEL_DELTA)
    if delta == 0:
        return
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_long(delta), 0)


# ------------------------------------------------------------------ 音量节流
_last_vol = [0.0]


def volume_step(direction: int, min_interval: float = 0.04) -> bool:
    """
    音量微调，带节流。Windows 每次 +2%，连续调用会形成平滑调节。
    direction: +1 增, -1 减。返回是否真的发出了按键。
    """
    now = time.perf_counter()
    if now - _last_vol[0] < min_interval:
        return False
    _last_vol[0] = now
    tap(VK["vol_up"] if direction > 0 else VK["vol_down"])
    return True


# ------------------------------------------------------------------ 桌面图标保护
# Ctrl+滚轮在浏览器里是「缩放网页」，但在**桌面和资源管理器里是「改图标大小」**。
# 手势扫到桌面时会把图标放大，而且关掉程序也不会自动还原 —— 这属于程序改了
# 用户没让它改的系统状态，必须拦住。
#
# 拦截点在发事件之前：先判断 Ctrl+滚轮这一下会落到谁身上，如果目标是桌面或
# 文件夹窗口，直接跳过，宁可不缩放也不动图标。
# 注意滚轮消息的去向有两套：老逻辑给**焦点窗口**，Win10+ 的「悬停滚动」给
# **光标下的窗口**，所以两个都查。

user32.GetForegroundWindow.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

# 命中这些窗口类的 Ctrl+滚轮 = 改图标大小（或干脆是桌面本身）
_ICON_SURFACE_CLASSES = frozenset({
    "Progman",           # 桌面根窗口
    "WorkerW",           # Win10/11 桌面的实际宿主，图标视图挂在它下面
    "SHELLDLL_DefView",  # 桌面图标视图容器
    "SysListView32",     # 桌面 / 文件夹的图标列表
    "DirectUIHWND",      # Win11 资源管理器视图
    "CabinetWClass",     # 资源管理器窗口
    "ExploreWClass",     # 老式资源管理器
})


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _looks_like_icon_surface(hwnd: int) -> bool:
    """沿父窗口链往上走，命中图标类名就算。"""
    h = hwnd
    for _ in range(8):
        if not h:
            return False
        if _class_of(h) in _ICON_SURFACE_CLASSES:
            return True
        h = user32.GetParent(h)
    return False


def icon_zoom_target() -> bool:
    """Ctrl+滚轮是否会落到「改图标大小」的窗口上。"""
    pt = wintypes.POINT()
    hover = 0
    if user32.GetCursorPos(ctypes.byref(pt)):
        hover = user32.WindowFromPoint(pt) or 0
    fg = user32.GetForegroundWindow() or 0
    return _looks_like_icon_surface(hover) or _looks_like_icon_surface(fg)


# ------------------------------------------------------------------ 缩放节流
_last_zoom = [0.0]
_last_block_log = [0.0]


def zoom(notches: int, min_interval: float = 0.06) -> str:
    """
    Ctrl + 滚轮缩放（浏览器里就是"放大网页"）。notches 正数放大、负数缩小。

    返回状态字符串：
        "ok"        真的发出去了
        "blocked"   目标是桌面 / 资源管理器，跳过（否则会改图标大小）
        "throttled" 节流中，没发
        "none"      步长为 0
        "unsupported" 当前平台没有按键注入能力

    关键：Ctrl 必须在 finally 里释放。一旦 Ctrl 卡在按下状态，之后所有按键
    都会变成组合键，等于把键盘搞坏——这是必须兜住的。
    """
    if not WINDOWS:
        return "unsupported"
    if not notches:
        return "none"

    # 顺序很重要：先挡图标面，再谈节流。被挡住时不能更新 _last_zoom，
    # 否则从桌面移回浏览器后还要白等一个节流周期才能缩。
    if icon_zoom_target():
        now = time.perf_counter()
        if now - _last_block_log[0] > 3.0:
            _last_block_log[0] = now
            log.info("跳过缩放：桌面/资源管理器里 Ctrl+滚轮会改图标大小")
        return "blocked"

    now = time.perf_counter()
    if now - _last_zoom[0] < min_interval:
        return "throttled"
    _last_zoom[0] = now

    direction = 1 if notches > 0 else -1
    _down(VK["ctrl"])
    try:
        for _ in range(min(abs(int(notches)), 3)):
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0,
                               ctypes.c_long(WHEEL_DELTA * direction), 0)
            time.sleep(0.02)
    finally:
        _up(VK["ctrl"])
    return "ok"


def zoom_reset() -> str:
    """
    网页缩放恢复 100%（浏览器里就是 Ctrl+0）。

    和 zoom() 一样，Ctrl 必须在 finally 里松开——Ctrl 卡住会把键盘搞坏。
    """
    if not WINDOWS:
        return "unsupported"
    if icon_zoom_target():
        now = time.perf_counter()
        if now - _last_block_log[0] > 3.0:
            _last_block_log[0] = now
            log.info("跳过缩放复位：桌面/资源管理器里 Ctrl+0 没有意义")
        return "blocked"
    _down(VK["ctrl"])
    try:
        tap(VK["0"])
    finally:
        _up(VK["ctrl"])
    return "ok"


# ------------------------------------------------------------------ 窗口操作
# 单手手势操作的是**当前前台窗口**（Alt+Tab 切到的那个），全部走 Win32，
# 不依赖 pywin32。每个函数返回一个很短的"结果码"，上层用它决定桌宠说哪句话
# （见 pet/intent/mapping.py::window_bubble）——「做成了什么」比「做了什么」重要。
#
# 为什么不用「Win+方向键」做最大化/最小化：
#   组合键是**盲发**的，窗口是不是已经最大化、是不是置顶，程序并不知道，
#   于是"最大化"会变成"还原"，按一下看起来像没反应。这里直接查窗口状态
#   (IsZoomed / WS_EX_TOPMOST) 再决定发哪个命令，同一个手势就能做成开关。
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

# 这些不是"可以摆放的窗口"：桌面本身、任务栏、输入法候选窗
_NOT_A_WINDOW = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
})


def _own_pid() -> int:
    return os.getpid()


def target_window() -> int:
    """
    取当前该被操作的窗口；没有合适的就返回 0。

    排除三类，都是实际踩过的坑：
        桌面/任务栏 —— 最小化桌面没有意义，Win+D 才是那个动作
        自己的窗口 —— 用户点了桌宠，前台就变成桌宠；这时做手势会把桌宠自己缩掉，
                      看起来就像程序坏了
    """
    hwnd = user32.GetForegroundWindow() or 0
    if not hwnd:
        return 0
    if _class_of(hwnd) in _NOT_A_WINDOW:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == _own_pid():
        return 0
    return int(hwnd)


def maximize_restore() -> str:
    """最大化 / 还原（同一个手势，看窗口当前状态决定做哪一半）。"""
    h = target_window()
    if not h:
        return "no_window"
    if user32.IsZoomed(h):
        user32.ShowWindow(h, SW_RESTORE)
        return "restore"
    user32.ShowWindow(h, SW_MAXIMIZE)
    return "max"


def minimize() -> str:
    h = target_window()
    if not h:
        return "no_window"
    user32.ShowWindow(h, SW_MINIMIZE)
    return "min"


def topmost_toggle() -> str:
    """置顶 / 取消置顶。写资料、看视频时把窗口钉在最上层很好用。"""
    h = target_window()
    if not h:
        return "no_window"
    style = user32.GetWindowLongW(h, GWL_EXSTYLE)
    on = bool(style & WS_EX_TOPMOST)
    user32.SetWindowPos(h, HWND_NOTOPMOST if on else HWND_TOPMOST,
                        0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    return "untop" if on else "top"


def snap(side: str) -> str:
    """
    贴边分屏（Win+←/→）。交给系统做而不是自己 SetWindowPos：
    只有系统的 Aero Snap 处理得对多显示器、任务栏和各种 DPI。
    """
    if target_window() == 0:
        return "no_window"
    combo(VK["win"], VK["left"] if side == "left" else VK["right"])
    return "left" if side == "left" else "right"


def show_desktop() -> str:
    """全部收起 / 再按一次恢复（Win+D）。是开关，不会丢东西。"""
    combo(VK["win"], VK["d"])
    return "desktop"


# 名字 -> 函数。返回结果码的这套单独放，因为键名动作返回 bool、这套返回语义串。
WINDOW_ACTIONS = {
    "win_max": maximize_restore,
    "win_min": minimize,
    "win_topmost": topmost_toggle,
    "win_snap_left": lambda: snap("left"),
    "win_snap_right": lambda: snap("right"),
    "win_desktop": show_desktop,
}

NO_WINDOW_HINT = "没有可以操作的窗口（先在任务栏点一下目标窗口）"


def run_window_action(name: str) -> str:
    """执行一个窗口动作，返回结果码；未知名字返回 "unknown"。"""
    if not WINDOWS:
        return "unsupported"
    fn = WINDOW_ACTIONS.get(name)
    if fn is None:
        log.warning("未知窗口动作: %s", name)
        return "unknown"
    try:
        return fn() or "ok"
    except Exception as e:
        log.error("窗口动作 %s 失败: %s", name, e)
        return "failed"


# ------------------------------------------------------------------ 浏览器
# 白名单：打开网址算是"对系统产生副作用"的动作，只放行这里列出的站点。
# 为什么一定要白名单：配置是明文文件、谁都能改；如果 `open_browser` 接受任意
# URL，等于把"比个手势"变成"任何写着配置的人都能让你打开任意页面"的入口。
# 安全边界写在代码里，配置只能在这个集合内部挑。
BROWSER_ALLOWLIST = (
    "https://www.bing.com/",
    "https://cn.bing.com/",
    "https://www.baidu.com/",
    "https://www.google.com/",
)

#: 👍 默认打开的页面（可在配置里用 url 覆盖，但必须命中白名单）
DEFAULT_BROWSER_URL = "https://www.bing.com/"

#: 配置覆盖值。空串 = 用默认页。
_override_url = ""


def set_browser_url(url: str) -> None:
    """
    启动时由配置覆盖 👍 打开的页面。

    只做最基本的形态检查（必须是 http/https），真正的白名单校验仍然在
    open_url 里——配置写错了不该变成"绕过安全检查"的后门。
    """
    global _override_url
    u = (url or "").strip()
    _override_url = u if u.lower().startswith(("http://", "https://")) else ""


def _browser_target() -> str:
    return _override_url or DEFAULT_BROWSER_URL


def open_url(url: str) -> bool:
    """
    用系统默认浏览器打开白名单内的网址。

    webbrowser 是标准库、三大平台都有，所以这一个动作**不需要 Windows**。
    返回 False 表示"被拦下了"或"没打开"，调用方据此给出提示，
    而不是静默失败让人以为手势没识别到。
    """
    target = (url or "").strip()
    if not target:
        return False

    low = target.lower()
    if not low.startswith(("http://", "https://")):
        return False                    # 挡掉 file: / javascript: 等非网页协议

    if not any(low.startswith(allowed.lower()) for allowed in BROWSER_ALLOWLIST):
        log.warning("网址不在白名单里，已拒绝: %s", target)
        return False

    try:
        import webbrowser
        return bool(webbrowser.open(target))
    except Exception as e:
        log.error("打开浏览器失败: %s", e)
        return False


# ------------------------------------------------------------------ 动作表
# 键名 -> 执行函数。想改映射，只动这里（或 main.py 里的 GESTURE_ACTIONS）。
ACTIONS = {
    # 唯一一个跨平台动作：不注入按键，只调系统默认浏览器
    "open_browser": lambda: open_url(_browser_target()),
    "play_pause": lambda: tap(VK["play_pause"]),
    "mute": lambda: tap(VK["vol_mute"]),
    "vol_up": lambda: tap(VK["vol_up"], 3),
    "vol_down": lambda: tap(VK["vol_down"], 3),
    "next_track": lambda: tap(VK["next_track"]),
    "prev_track": lambda: tap(VK["prev_track"]),
    "alt_tab": lambda: combo(VK["alt"], VK["tab"]),
    "page_down": lambda: tap(VK["pagedown"]),
    "page_up": lambda: tap(VK["pageup"]),
    "escape": lambda: tap(VK["escape"]),
    "space": lambda: tap(VK["space"]),
    "enter": lambda: tap(VK["enter"]),
    "scroll_up": lambda: scroll(+3),
    "scroll_down": lambda: scroll(-3),
}


def run_action(name: str) -> bool:
    """执行一个具名动作，返回是否成功。"""
    fn = ACTIONS.get(name)
    if fn is None:
        log.warning("未知动作: %s", name)
        return False
    # open_browser 走 webbrowser，非 Windows 也能用；其余全靠 keybd_event
    if not WINDOWS and name != "open_browser":
        log.warning("当前平台不支持按键动作: %s", name)
        return False
    try:
        fn()
        return True
    except Exception as e:
        log.error("执行 %s 失败: %s", name, e)
        return False
