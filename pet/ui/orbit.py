# -*- coding: utf-8 -*-
"""
桌宠左键单击弹出的「圆形按键组」—— 三个圆圈，摆在宠物侧面。

为什么要多这一层（以前是点一下直接弹控制面板）：
    控制面板有 8 行、十几项。可用户点桌宠时想要的通常只有两件事之一：
    "我要调设置" 或者 "我要换个玩法"。为这两种意图各弹一个十几行的
    面板，等于每次都让人在一张表里找路。
    现在先用三个圆按钮把意图分一次流：
        ⚙ 设置 -> 才打开原来那张控制面板
        ♥ 互动 -> 单手手势整套切成"逗桌宠"
        ▣ 操作 -> 单手手势整套切成"操作窗口"
    圆形 + 单个图标，是因为这三个键要能被"看一眼就认出"，而不是读字；
    名称放在悬停时才出现的胶囊里（鼠标放上去才显示），不占地方。

实现要点
    Qt.Popup        —— 点外面自动收，不用监听全局点击。
    弧线排布        —— 三个圆落在以宠物中心为圆心的圆弧上，朝左或朝右
                       （哪边有地方朝哪边），这样菜单永远在宠物"身侧"，
                       不会盖住它本身。
    名称在弧外侧    —— 胶囊竖着摆在整条弧的外面一列，不挂在圆下面；
                       挂在下面必然和最邻近的那个圆打架。
    不存状态        —— 当前是哪套手势，每次显示时从 mapping 现问，
                       所以从对照表里改过之后，这里的高亮也是对的。
"""

from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QWidget

from .paint import ui_font

# 配色沿用桌宠/面板那一套（DeepSeek 蓝 + 浅色卡片），不引入 QSS：
# QSS 在透明窗口上会把圆角画成方的。
CARD = QColor(252, 253, 255, 244)
BORDER = QColor(208, 217, 232, 255)
TEXT = QColor(38, 48, 70)
TEXT_ON = QColor(255, 255, 255)
ACCENT = QColor(77, 107, 254)
ACCENT_SOFT = QColor(232, 238, 255, 250)
SHADOW = QColor(30, 42, 70, 26)

D = 46              # 圆的直径
R = 92              # 弧半径（宠物中心 -> 圆心）
SPREAD = 40.0       # 三个圆之间各差多少度
PAD = 10            # 给高亮环/阴影留的边
PILL_W = 58         # 悬停名称胶囊的宽（竖着摆在弧外侧，所以留固定一列）
PILL_H = 22

#: (key, 表情, 名称)。顺序 = 从弧顶到弧底。
_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("settings", "⚙", "设置"),
    ("pet", "🎮", "互动"),
    ("window", "🖥", "操作"),
)

# 表情字体只注册一次。不注册就得赌系统里"Segoe UI Emoji"能被按家族名查到——
# 在离屏渲染/精简系统上查不到（families() 直接是 0），表情会变成一排方框。
_EMOJI_FAMILY: Optional[str] = None
_EMOJI_TRIED = False


def _emoji_font(size: int) -> Optional[QFont]:
    """
    取能画彩色表情的字体；取不到就返回 None，调用方改画矢量图标。

    为什么要有矢量兜底：表情是"锦上添花"，但按钮不能因为它没了就变成
    三个空圈——那比不画图标更糟（用户完全看不出哪个是设置）。
    """
    global _EMOJI_FAMILY, _EMOJI_TRIED
    if not _EMOJI_TRIED:
        _EMOJI_TRIED = True
        for path in (r"C:\Windows\Fonts\seguiemj.ttf",
                     "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                     "/System/Library/Fonts/Apple Color Emoji.ttc"):
            if not os.path.exists(path):
                continue
            fid = QFontDatabase.addApplicationFont(path)
            if fid < 0:
                continue
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                _EMOJI_FAMILY = fams[0]
                break

    if not _EMOJI_FAMILY:
        return None
    f = QFont(_EMOJI_FAMILY)
    f.setPointSize(size)
    # 字体在、但缺这个字形（比如只有黑白符号）时不硬用，交给矢量兜底
    if not QFontMetrics(f).inFontUcs4(ord(_ITEMS[0][1])):
        return None
    return f


class OrbitMenu(QWidget):
    """桌宠左键单击 -> 三个圆形按键。只做"点击转发"，不存状态。"""

    def __init__(self, window) -> None:
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.win = window
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setWindowTitle("快捷按键")

        self._font_name = ui_font(9, bold=True)
        self._font_icon = _emoji_font(15)
        self._hover = ""
        self._press = ""
        self._side = "left"
        self._centers: List[Tuple[str, str, str, QPointF]] = []
        self._origin = QPointF()
        self._build("left")

    # ---------------------------------------------------------------- 布局
    def _angles(self, side: str) -> Tuple[float, ...]:
        if side == "left":
            base = 180.0
            return (base + SPREAD, base, base - SPREAD)
        # 右侧镜像。角度取反（而不是简单把基角设成 0）：这样"设置"仍在
        # 最上面。不这么做的话，宠物从屏幕右边挪到左边，三个键的上下顺序
        # 会整个倒过来——同一个按钮位置天天变，等于让人重学一次。
        return (-SPREAD, 0.0, SPREAD)

    def _build(self, side: str) -> None:
        """按方向算三个圆心，并把整个控件裁到刚好包住它们 + 名称那一列。"""
        self._side = side
        raw = []
        for (key, icon, name), deg in zip(_ITEMS, self._angles(side)):
            rad = math.radians(deg)
            raw.append((key, icon, name,
                        QPointF(R * math.cos(rad), R * math.sin(rad))))

        pad = D / 2 + PAD
        minx = min(c.x() for _, _, _, c in raw) - pad
        maxx = max(c.x() for _, _, _, c in raw) + pad
        miny = min(c.y() for _, _, _, c in raw) - pad
        maxy = max(c.y() for _, _, _, c in raw) + pad

        # 名称胶囊固定占弧外侧一列，所以那一侧的边界要再放宽 PILL_W
        if side == "left":
            minx -= PILL_W + 8
        else:
            maxx += PILL_W + 8

        w = int(maxx - minx)
        h = int(maxy - miny)
        self.resize(max(w, 40), max(h, 40))

        dx = -minx
        dy = -miny
        self._centers = [(k, i, n, QPointF(c.x() + dx, c.y() + dy))
                         for k, i, n, c in raw]
        # 宠物中心（弧的圆心）在本地坐标里的位置。它落在控件外或边缘都
        # 正常 —— 弧是往一侧弯的，圆心自然在那一侧的外面。
        self._origin = QPointF(dx, dy)

        # 名称列：整条弧外侧的竖条
        if side == "left":
            self._pill_x = 2.0
        else:
            self._pill_x = self.width() - PILL_W - 2.0

    # ---------------------------------------------------------------- 弹出
    def popup_near(self, anchor) -> None:
        """摆在宠物身侧：默认朝左，左边放不下就朝右，再夹进屏幕。"""
        from PySide6.QtGui import QGuiApplication

        scr = QGuiApplication.primaryScreen()
        area = scr.availableGeometry() if scr else None
        pos = getattr(anchor, "_pos", None)
        if pos is None:
            g = anchor.frameGeometry()
            cx, cy = g.center().x(), g.center().y()
        else:
            cx = int(pos.x() + anchor.pet_cx)
            cy = int(pos.y() + anchor.pet_cy)

        # 弧从宠物中心往一侧伸出去的距离（含圆的半径和名称列）
        reach = R + D / 2 + PAD + PILL_W + 8

        side = "left"
        if area is not None and cx - reach < area.left():
            side = "right"
        self._build(side)
        x, y = self._place(cx, cy)

        if area is not None:
            if side == "left" and x < area.left():
                side = "right"
            elif side == "right" and x + self.width() > area.right():
                side = "left"
            if side != self._side:
                self._build(side)
                x, y = self._place(cx, cy)
            x = max(area.left(), min(x, area.right() - self.width()))
            y = max(area.top(), min(y, area.bottom() - self.height()))

        self.move(int(x), int(y))
        self.show()
        self.raise_()

    def _place(self, cx: int, cy: int) -> Tuple[int, int]:
        """让弧的圆心（宠物中心）落在 (cx, cy) 上。"""
        return (int(cx - self._origin.x()), int(cy - self._origin.y()))

    # ---------------------------------------------------------------- 命中
    def _hit(self, pos: QPointF) -> str:
        for key, _i, _n, c in self._centers:
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= D / 2 + 3:
                return key
        return ""

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        active = self._active_preset()
        for key, icon, name, c in self._centers:
            hot = key == self._hover
            pressed = key == self._press and hot
            self._paint_circle(p, c, key, icon,
                               on=key == active, hot=hot, pressed=pressed)

        # 名称胶囊：只在悬停时出现，摆在弧外侧那一列
        if self._hover:
            for key, _i, name, c in self._centers:
                if key == self._hover:
                    self._paint_pill(p, c, name)
                    break
        p.end()

    def _paint_circle(self, p: QPainter, c: QPointF, key: str, icon: str,
                      on: bool, hot: bool, pressed: bool) -> None:
        r = D / 2 - (1.0 if pressed else 0.0)

        # 阴影压在下沿，让圆"浮"在壁纸上，不至于像一张贴纸
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(SHADOW)
        p.drawEllipse(QPointF(c.x(), c.y() + 2.0), r, r)

        if on:
            p.setBrush(ACCENT_SOFT)
        elif hot:
            p.setBrush(QColor(245, 248, 255, 250))
        else:
            p.setBrush(CARD)
        p.setPen(QPen(ACCENT if (on or hot) else BORDER, 2.0 if hot else 1.0))
        p.drawEllipse(c, r, r)

        if self._font_icon is not None:
            p.setFont(self._font_icon)
            p.setPen(QPen(TEXT))
            # 字形视觉重心偏下，上抬 1px 才不会看着掉出圆外
            box = QRectF(c.x() - r, c.y() - r - 1, r * 2, r * 2)
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), icon)
        else:
            self._paint_vector_icon(p, c, key)

    def _paint_vector_icon(self, p: QPainter, c: QPointF, key: str) -> None:
        """
        没有彩色表情字体时的兜底图标 —— 全部用路径画，不依赖任何字形。

        三个形状各自对应一个语义，隔着屏幕也能认出来：
            设置 = 三条推子（粗细不等，带滑块）
            互动 = 一颗心
            操作 = 两个叠起来的窗口
        """
        s = D * 0.26                      # 图标半尺寸
        col = ACCENT
        pen = QPen(col, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if key == "settings":
            for i, dy in enumerate((-s * 0.72, 0.0, s * 0.72)):
                y = c.y() + dy
                p.drawLine(QPointF(c.x() - s, y), QPointF(c.x() + s, y))
                # 滑块位置错开，才像"在调"，不是三条横线
                kx = c.x() + (-s * 0.35 if i == 0 else (s * 0.4 if i == 1
                                                        else -s * 0.05))
                p.setBrush(QColor(255, 255, 255))
                p.drawEllipse(QPointF(kx, y), 3.2, 3.2)
                p.setBrush(Qt.BrushStyle.NoBrush)
        elif key == "pet":
            path = QPainterPath()
            top = c.y() - s * 0.75
            path.moveTo(c.x(), c.y() + s * 0.85)
            path.cubicTo(c.x() - s * 1.9, c.y() - s * 0.1,
                         c.x() - s * 0.95, top - s * 0.55,
                         c.x(), c.y() - s * 0.38)
            path.cubicTo(c.x() + s * 0.95, top - s * 0.55,
                         c.x() + s * 1.9, c.y() - s * 0.1,
                         c.x(), c.y() + s * 0.85)
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)
        else:
            back = QRectF(c.x() - s * 0.55, c.y() - s * 0.95, s * 1.5, s * 1.15)
            front = QRectF(c.x() - s * 0.95, c.y() - s * 0.2, s * 1.5, s * 1.15)
            p.setBrush(QColor(255, 255, 255))
            p.drawRoundedRect(back, 2.5, 2.5)
            p.setBrush(ACCENT_SOFT)
            p.drawRoundedRect(front, 2.5, 2.5)

    def _paint_pill(self, p: QPainter, c: QPointF, name: str) -> None:
        p.setFont(self._font_name)
        x = self._pill_x
        y = c.y() - PILL_H / 2
        y = max(1.0, min(y, self.height() - PILL_H - 1.0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 48, 70, 236))
        p.drawRoundedRect(QRectF(x, y, PILL_W, PILL_H), PILL_H / 2, PILL_H / 2)
        p.setPen(QPen(TEXT_ON))
        p.drawText(QRectF(x, y, PILL_W, PILL_H),
                   int(Qt.AlignmentFlag.AlignCenter), name)

    # ---------------------------------------------------------------- 状态
    def _active_preset(self) -> str:
        """现在单手是哪套玩法 —— 用来给对应的圆加高亮环。"""
        try:
            from ..intent import mapping
            return mapping.current_single_preset()
        except Exception:
            return ""

    # ---------------------------------------------------------------- 事件
    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        key = self._hit(ev.position())
        if key != self._hover:
            self._hover = key
            self.update()
        # 自绘胶囊是给人看的；真 tooltip 再设一份，指针不动时也能看到名字
        self.setToolTip(next((n for k, _i, n, _c in self._centers if k == key), ""))

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        if self._hover:
            self._hover = ""
            self.update()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self._press = (self._hit(ev.position())
                       if ev.button() == Qt.MouseButton.LeftButton else "")
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        key = self._hit(ev.position())
        pressed = self._press
        self._press = ""
        self.update()
        ev.accept()
        if ev.button() != Qt.MouseButton.LeftButton or not key or key != pressed:
            return
        self.activate(key)

    def activate(self, key: str) -> None:
        """
        点中某个圆做什么。

        先 close() 再执行：设置要弹控制面板，两个 Popup 同时开着会互相抢焦点
        （谁后 show 谁在上，先弹的那个看着像"闪了一下"）。
        """
        self.close()
        win = self.win
        if key == "settings":
            win.open_panel()
        elif key in ("pet", "window"):
            control = getattr(win, "control", None)
            if control is not None and hasattr(control, "set_single_preset"):
                control.set_single_preset(key)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        # Esc 收起。Popup 本身点外面就会关，但键盘用户没有"外面"可点。
        if ev.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(ev)
