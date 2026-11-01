# -*- coding: utf-8 -*-
"""
前台窗口的"身份 + 几何" —— 桌宠吸附到窗口边时唯一的取数来源。

为什么要有这个模块
    手势动作打的是"当前前台窗口"（见 `actions.target_window()`），但用户看不
    出这一点：桌宠在屏幕角落里飘着，比个 ✋ 之后是浏览器变大了还是记事本变大了，
    只能事后看结果。把桌宠贴到那个窗口的边上，再挂一个小标题，这件事就变成
    一眼可见的 —— 这也是"吸附"存在的全部理由。

    所以判定口径必须和 `actions.target_window()` **完全一致**（同一个前台窗口、
    同样排除桌面/任务栏/自己进程），否则会出现"桌宠贴着 A，手势却动了 B"，
    那比不吸附更让人困惑。

两块内容，故意分开
    `query()`   —— 唯一碰 Win32 的部分，非 Windows 直接返回 None。
    `dock_xy()` —— 纯算术，没有 IO。吸附位置最容易出错（贴出屏幕、压住标题栏、
                   窗口贴边时没地方站），全在这一个函数里，可以在测试里喂数字
                   反复验证，不用真的开窗口。

关于 DPI
    GetWindowRect 给的是**物理像素**，而 Qt 的窗口坐标是**逻辑像素**，在 125%/
    150% 缩放的屏幕上两者差 1.25/1.5 倍 —— 不换算的话桌宠会贴在离窗口很远的
    地方，而且缩放比例越大偏得越多。这里用 GetDpiForWindow 逐窗口取（多显示器
    混合 DPI 也正确），拿不到时退回调用方给的 Qt devicePixelRatio。
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

_IS_WIN = sys.platform == "win32"

# ---- Win32 常量 ----
DWMWA_EXTENDED_FRAME_BOUNDS = 9      # 不含窗口阴影的真实边界（Win10+）
MONITOR_DEFAULTTONEAREST = 2

# 这些不是"可以吸附的窗口"：桌面本身、任务栏
_NOT_A_WINDOW = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
})

# 太小的窗口不值得贴（悬浮提示、输入法候选框都会落到这里）
_MIN_W, _MIN_H = 120.0, 80.0


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


if _IS_WIN:
    _user32 = ctypes.windll.user32
    _dwm = ctypes.WinDLL("dwmapi")

    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_ulong)]
    _user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    _user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
    _user32.GetWindowRect.restype = ctypes.c_int
    _user32.IsIconic.argtypes = [ctypes.c_void_p]
    _user32.IsIconic.restype = ctypes.c_int
    _user32.IsZoomed.argtypes = [ctypes.c_void_p]
    _user32.IsZoomed.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    _user32.IsWindowVisible.restype = ctypes.c_int
    _user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    _user32.MonitorFromWindow.restype = ctypes.c_void_p
    _user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
    _user32.GetMonitorInfoW.restype = ctypes.c_int
    _dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                           ctypes.c_void_p, ctypes.c_ulong]
    _dwm.DwmGetWindowAttribute.restype = ctypes.c_long


@dataclass(frozen=True)
class WindowInfo:
    """一个前台窗口的快照。坐标已换算成 Qt 的逻辑像素。"""

    hwnd: int
    title: str
    left: float
    top: float
    right: float
    bottom: float
    state: str = "normal"        # normal / maximized / minimized / fullscreen

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center(self) -> Tuple[float, float]:
        return (self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0

    @property
    def dockable(self) -> bool:
        """
        能不能贴着站。

        最小化：窗口没有边可贴；最大化 / 无边框全屏：四条边都在屏幕外或正中，
        贴过去只会挡住内容 —— 三种情况都退回"守在角落"，等窗口恢复再贴回来。
        """
        return self.state == "normal"


# ------------------------------------------------------------------ 纯几何
def _clamp(v: float, lo: float, hi: float) -> float:
    if hi < lo:
        return lo
    return lo if v < lo else (hi if v > hi else v)


def dock_xy(win: WindowInfo, pet_w: float, pet_h: float,
            pet_cx: float, pet_cy: float,
            screen: Tuple[float, float, float, float],
            body_w: float = 0.0, overlap: float = 0.16,
            prefer: str = "right", pad: float = 4.0,
            y_ratio: float = 0.28) -> Tuple[float, float, str]:
    """
    算出桌宠窗口该待在哪儿。返回 (x, y, side)。

    规则
        竖直：把宠物**身体中心**放在窗口高度的 y_ratio 处（默认 28%）——
              避开标题栏和右上角的关闭键，看起来像站在窗口的"肩"上。
        水平：身体**压住边界一点点**（overlap，默认 16% 体宽），大部分站在
              窗口外面。压太多会盖住滚动条和内容，完全贴在外面又看不出
              "贴着谁"，16% 是这两者之间的折中，看着像"抱着窗口的边"。
              右边放不下（窗口贴着屏幕右缘）自动翻到左边；两边都没地方时
              贴右边并夹进屏幕，至少不会跑出去。
    坐标是桌宠窗口的左上角。body_w 传 0 表示没有立绘信息，退化成
    "身体中心落在边界上"的老行为。
    """
    sl, st, sr, sb = screen
    body_y = win.top + win.height * _clamp(y_ratio, 0.0, 1.0)
    y = _clamp(body_y - pet_cy, st, sb - pet_h)

    reach = max(0.0, body_w) * (0.5 - _clamp(overlap, 0.0, 0.5))
    sides = ("right", "left") if prefer != "left" else ("left", "right")
    fallback: Optional[float] = None
    for side in sides:
        if side == "right":
            cx = win.right + pad + reach
        else:
            cx = win.left - pad - reach
        x = cx - pet_cx
        if sl - 0.5 <= x and x + pet_w <= sr + 0.5:
            return x, y, side
        if fallback is None:                  # 第一个方向的结果留作兜底
            fallback = _clamp(x, sl, sr - pet_w)
    return (sl if fallback is None else fallback), y, sides[0]


def short_title(title: str, limit: int = 12) -> str:
    """标题压到一行能画下的长度；空标题给个兜底，免得标签画成一条空框。"""
    t = " ".join((title or "").split())
    if not t:
        return "窗口"
    return t if len(t) <= limit else t[: max(1, limit - 1)] + "…"


# ------------------------------------------------------------------ Win32 查询
def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    if _user32.GetClassNameW(hwnd, buf, 256):
        return buf.value
    return ""


def _title_of(hwnd: int, limit: int = 120) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()[:limit]


def _frame_rect(hwnd: int) -> Optional[Tuple[float, float, float, float]]:
    """窗口真实边界（物理像素）。优先用 DWM 的扩展边界，不含阴影。"""
    r = _RECT()
    try:
        ok = _dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                        ctypes.byref(r), ctypes.sizeof(r))
        if ok == 0 and r.right > r.left and r.bottom > r.top:
            return float(r.left), float(r.top), float(r.right), float(r.bottom)
    except Exception:
        pass
    if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return float(r.left), float(r.top), float(r.right), float(r.bottom)
    return None


def _monitor_rect(hwnd: int) -> Optional[Tuple[float, float, float, float]]:
    mon = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    m = mi.rcMonitor
    return float(m.left), float(m.top), float(m.right), float(m.bottom)


def _dpi_scale(hwnd: int, fallback: float) -> float:
    """逐窗口的 DPI 缩放（1.0 = 100%）。取不到就用调用方给的 Qt 值。"""
    try:
        dpi = _user32.GetDpiForWindow(hwnd)      # Win10 1607+
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return max(1.0, float(fallback or 1.0))


def _own_pid() -> int:
    return os.getpid()


def query(self_hwnd: int = 0, dpr: float = 0.0) -> Optional[WindowInfo]:
    """
    取当前该被操作的那个窗口。

    返回 None 的几种情况都是"这会儿没得贴"：没有前台窗口、前台是桌面/任务栏、
    前台是桌宠自己（点了桌宠）、窗口太小或不可见、不是 Windows。
    """
    if not _IS_WIN:
        return None
    try:
        hwnd = int(_user32.GetForegroundWindow() or 0)
        if not hwnd:
            return None
        if self_hwnd and hwnd == int(self_hwnd):
            return None
        if _class_of(hwnd) in _NOT_A_WINDOW:
            return None
        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _own_pid():
            return None
        if not _user32.IsWindowVisible(hwnd):
            return None

        rect = _frame_rect(hwnd)
        if rect is None:
            return None
        l, t, r, b = rect
        if (r - l) < _MIN_W or (b - t) < _MIN_H:
            return None

        if _user32.IsIconic(hwnd):
            state = "minimized"
        elif _user32.IsZoomed(hwnd):
            state = "maximized"
        else:
            mon = _monitor_rect(hwnd)
            # 无边框全屏（视频播放器、游戏）没有最大化标志，但铺满了整块屏
            if mon and (r - l) >= (mon[2] - mon[0]) * 0.98 \
                    and (b - t) >= (mon[3] - mon[1]) * 0.98:
                state = "fullscreen"
            else:
                state = "normal"

        s = _dpi_scale(hwnd, dpr)
        return WindowInfo(hwnd=hwnd, title=_title_of(hwnd),
                          left=l / s, top=t / s, right=r / s, bottom=b / s,
                          state=state)
    except Exception:
        # 吸附只是锦上添花的功能，任何一步失败都不该影响桌宠本身
        return None
