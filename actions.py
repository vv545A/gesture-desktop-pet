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
import sys
import time
from ctypes import wintypes

if sys.platform != "win32":
    raise RuntimeError("本项目依赖 Windows API（keybd_event / mouse_event）")

user32 = ctypes.windll.user32

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

    关键：Ctrl 必须在 finally 里释放。一旦 Ctrl 卡在按下状态，之后所有按键
    都会变成组合键，等于把键盘搞坏——这是必须兜住的。
    """
    if not notches:
        return "none"

    # 顺序很重要：先挡图标面，再谈节流。被挡住时不能更新 _last_zoom，
    # 否则从桌面移回浏览器后还要白等一个节流周期才能缩。
    if icon_zoom_target():
        now = time.perf_counter()
        if now - _last_block_log[0] > 3.0:
            _last_block_log[0] = now
            print("[zoom] 跳过：桌面/资源管理器里 Ctrl+滚轮会改图标大小")
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
    if icon_zoom_target():
        now = time.perf_counter()
        if now - _last_block_log[0] > 3.0:
            _last_block_log[0] = now
            print("[zoom] 跳过重置：桌面/资源管理器里 Ctrl+0 没有意义")
        return "blocked"
    _down(VK["ctrl"])
    try:
        tap(VK["0"])
    finally:
        _up(VK["ctrl"])
    return "ok"


# ------------------------------------------------------------------ 动作表
# 键名 -> 执行函数。想改映射，只动这里（或 main.py 里的 GESTURE_ACTIONS）。
ACTIONS = {
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
        print(f"[warn] 未知动作: {name}")
        return False
    try:
        fn()
        return True
    except Exception as e:
        print(f"[error] 执行 {name} 失败: {e}")
        return False
