# -*- coding: utf-8 -*-
"""
桌宠左键单击弹出的「圆形按键组」—— 三个圆，摆在宠物身侧。

为什么要多这一层（以前是点一下直接弹控制面板）：
    控制面板有 8 行、十几项。可用户点桌宠时想要的通常只有两件事之一：
    "我要调设置" 或者 "我要换个玩法"。为这两种意图各弹一个十几行的
    面板，等于每次都让人在一张表里找路。先用三个圆把意图分一次流：
        ⚙ 设置 -> 才打开原来那张控制面板
        🐾 互动 -> 单手手势整套切成"逗桌宠"
        ▭ 操作 -> 单手手势整套切成"操作窗口"

视觉（和立绘对齐，色值见 ui/theme.py）
    圆按钮做成**奶油糖**：米白径向渐变（左上高光、右下压暗）+ 一圈暖棕细边，
    阴影也是暖棕的。原来那版是白底 + 深蓝描边，那套蓝是给旧形象（蓝发鲸鱼娘）
    配的，落在粉发女孩旁边像另一个软件。
    悬停/选中时，圆染上该按钮自己的浅色：设置配薄荷（她的开衫）、互动配粉
    （腮红与发色）、操作配裙蓝。三种浅色都从她身上取，所以三个圆放一起是
    "一套"，不是三色随机。
    图标一律**矢量线稿**，不用 emoji：emoji 的颜色由字体决定，⚙ 是单色字形、
    🎮 是彩色位图，三个圆里会同时出现两种画风；而且换个系统就换一张脸。
    线稿用墨色 #5D403A —— 和立绘的暖棕描边同族，缩小到 19px 也不会糊。

交互
    悬停：圆上浮 1.6px、底色转成该按钮的浅色、边框转深、旁边滑出名称胶囊。
    按下：缩 0.8px，像按进去一点。
    选中（当前正在用的那套玩法）：浅色底 + 2.2px 深色边 + 外圈一道淡光环。
    入场：三个圆依次弹出（各差 55ms，整体 240ms），从宠物那一侧"长"出来。

实现要点
    Qt.Popup        —— 点外面自动收，不用监听全局点击。
    弧线排布        —— 三个圆落在以宠物中心为圆心的圆弧上，朝左或朝右
                       （哪边有地方朝哪边），菜单永远在宠物"身侧"，不盖住它。
    名称在弧外侧    —— 胶囊竖着摆在整条弧外面一列；挂在圆下面必然和相邻的圆打架。
    不存状态        —— 当前是哪套手势，每次显示时从 mapping 现问，
                       所以从对照表里改过之后，这里的高亮也是对的。
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import (QColor, QFontMetrics, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

from . import theme
from .paint import ui_font

D = 46              # 圆的直径
R = 92              # 弧半径（宠物中心 -> 圆心）
SPREAD = 40.0       # 三个圆之间各差多少度
PAD = 13            # 给阴影 / 选中光环留的边
PILL_H = 22
PILL_MIN_W = 48.0

#: 每个按钮自己的强调色 (浅底, 深描边)。三色都取自她身上：开衫 / 腮红 / 裙子。
_ACCENT: Dict[str, Tuple[QColor, QColor]] = {
    "settings": (theme.MINT_TINT, theme.MINT_DEEP),
    "pet": (theme.PINK_TINT, theme.PINK_DEEP),
    "window": (theme.BLUE_TINT, theme.BLUE_DEEP),
}

#: (key, 图标, 名称)。顺序 = 从弧顶到弧底。
_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("settings", "gear", "设置"),
    ("pet", "paw", "互动"),
    ("window", "frame", "操作"),
)

_APPEAR_MS = 240        # 入场总时长
_STAGGER = 0.55         # 三个圆依次延迟多少（占整段的比例）


def _rgba(c: QColor, a: int) -> QColor:
    return QColor(c.red(), c.green(), c.blue(), a)


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
        self._hover = ""
        self._press = ""
        self._side = "left"
        self._centers: List[Tuple[str, str, str, QPointF]] = []
        self._origin = QPointF()
        # 弧半径。别用模块常量 R —— 换成 260×390 的动画素材之后，宠物的
        # 显示宽度从 113 涨到 146，固定 92 的弧会让三个圆压到身体边上。
        # 每次弹出按当前体宽重算（见 popup_near），窗口大小变了也跟着走。
        self._r = R
        self._pill_w = PILL_MIN_W
        self._appear = 1.0            # 入场进度 0~1（1 = 完全展开）
        self._build("left")

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(_APPEAR_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_appear)

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

        # 弧半径跟着体宽走：留 26% 的余量，圆的半径 23，加起来刚好落在
        # 身体轮廓外面一点（太贴会像贴在身上的贴纸，太远又够不着）。
        self._r = max(R, getattr(self.win, "body_w", 0.0) * 0.74)

        # 名称胶囊的宽度按最长的名字量，而不是写死 —— 换成两个字以外的名字
        # （或用户系统字体偏宽）时不会把字挤掉。
        fm = QFontMetrics(self._font_name)
        self._pill_w = max(PILL_MIN_W,
                           max(fm.horizontalAdvance(n) for _k, _i, n in _ITEMS) + 22)

        raw = []
        for (key, icon, name), deg in zip(_ITEMS, self._angles(side)):
            rad = math.radians(deg)
            raw.append((key, icon, name,
                        QPointF(self._r * math.cos(rad),
                                self._r * math.sin(rad))))

        pad = D / 2 + PAD
        minx = min(c.x() for _, _, _, c in raw) - pad
        maxx = max(c.x() for _, _, _, c in raw) + pad
        miny = min(c.y() for _, _, _, c in raw) - pad
        maxy = max(c.y() for _, _, _, c in raw) + pad

        # 名称胶囊固定占弧外侧一列，所以那一侧的边界要再放宽
        if side == "left":
            minx -= self._pill_w + 8
        else:
            maxx += self._pill_w + 8

        w = int(maxx - minx)
        h = int(maxy - miny)
        self.resize(max(w, 40), max(h, 40))

        dx, dy = -minx, -miny
        self._centers = [(k, i, n, QPointF(c.x() + dx, c.y() + dy))
                         for k, i, n, c in raw]
        # 宠物中心（弧的圆心）在本地坐标里的位置。它落在控件外或边缘都
        # 正常 —— 弧是往一侧弯的，圆心自然在那一侧的外面。
        self._origin = QPointF(dx, dy)

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

        # 先按当前体宽更新弧半径：下面的 reach 判断和 _build 都要用它，
        # 晚更新一步就会"按旧半径判断放不放得下、按新半径去摆"，边上会露出去。
        self._r = max(R, getattr(anchor, "body_w", 0.0) * 0.74)

        # 弧从宠物中心往一侧伸出去的距离（含圆的半径和名称列）
        reach = self._r + D / 2 + PAD + self._pill_w + 8

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

    # ---------------------------------------------------------------- 入场动画
    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self._appear = 0.0
        self._anim.stop()
        self._anim.start()

    def _on_appear(self, value) -> None:
        self._appear = float(value)
        self.update()

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
        for i, (key, icon, _name, c) in enumerate(self._centers):
            # 依次弹出：第 i 个的进度 = 整体进度往前挪一小段
            t = (self._appear - i * _STAGGER / len(self._centers)) / (
                1.0 - _STAGGER * (len(self._centers) - 1) / len(self._centers))
            t = max(0.0, min(1.0, t))
            if t <= 0.01:
                continue
            hot = key == self._hover
            pressed = key == self._press and hot
            self._paint_circle(p, c, key, icon,
                               on=key == active, hot=hot, pressed=pressed, t=t)

        # 名称胶囊：只在悬停时出现，摆在弧外侧那一列
        if self._hover:
            for key, _i, name, c in self._centers:
                if key == self._hover:
                    self._paint_pill(p, c, name, key)
                    break
        p.end()

    def _paint_circle(self, p: QPainter, c: QPointF, key: str, icon: str,
                      on: bool, hot: bool, pressed: bool, t: float) -> None:
        lit = hot or on                          # 有没有"上色"
        bg, deep = _ACCENT[key]                  # 该按钮的浅底 / 深描边

        p.save()
        p.setOpacity(t)
        # 从宠物那一侧朝外长出来：起点朝圆心收 9px，边长大边归位
        k = 0.82 + 0.18 * t
        p.translate(c.x() + (self._origin.x() - c.x()) * (1.0 - t) * 0.14,
                    c.y() + (self._origin.y() - c.y()) * (1.0 - t) * 0.14)
        p.scale(k, k)
        p.translate(-c.x(), -c.y())

        cx = c.x()
        cy = c.y() - (1.6 if lit else 0.0) - (0.8 if pressed else 0.0)
        r = D / 2 - (0.6 if pressed else 0.0)
        ctr = QPointF(cx, cy)

        # 阴影：三层暖棕，越靠下越淡（比 QGraphicsDropShadowEffect 稳，
        # 那种 effect 加在透明顶层窗口上时某些驱动会整块变黑底）
        p.setPen(Qt.PenStyle.NoPen)
        for dy, a in ((5.0, 9), (3.4, 13), (2.0, 18)):
            p.setBrush(theme.shadow(a))
            p.drawEllipse(QPointF(cx, cy + dy), r - 1.2, r - 1.2)

        # 选中：外圈一道淡光环，和"悬停"区分开（悬停只有描边变深）
        if on:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_rgba(deep, 105), 1.6))
            p.drawEllipse(ctr, r + 4.2, r + 4.2)

        # 底：左上亮、右下暗的径向渐变，看起来像一颗有体积的奶油糖
        g = QRadialGradient(QPointF(cx - r * 0.38, cy - r * 0.52), r * 1.9)
        if lit:
            g.setColorAt(0.0, theme.tint(bg, 0.62))
            g.setColorAt(1.0, bg)
        else:
            g.setColorAt(0.0, theme.CREAM_HI)
            g.setColorAt(1.0, theme.CREAM_LO)
        p.setBrush(g)
        p.setPen(QPen(deep if lit else theme.LINE,
                      2.2 if on else (1.8 if hot else 1.3)))
        p.drawEllipse(ctr, r, r)

        # 顶部一道很淡的高光弧：奶油糖的"釉面"，也是勾边的收尾
        if not pressed:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 185 if lit else 140), 1.4,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx - r * 0.62, cy - r * 0.78, r * 1.24, r * 1.24),
                      int(38 * 16), int(104 * 16))

        self._paint_icon(p, cx, cy, icon, deep if lit else theme.INK,
                         theme.tint(bg, 0.75) if lit else theme.CREAM_HI)
        p.restore()

    # ---------------------------------------------------------------- 图标
    def _paint_icon(self, p: QPainter, cx: float, cy: float, icon: str,
                    col: QColor, fill: QColor) -> None:
        """
        三个图标全部用路径画，不依赖任何字体。

        为什么不用 emoji：同一个系统的字体里 ⚙ 可能是单色字形、🎮 却是彩色位图，
        三个圆里会同时出现两种画风；换台机器还会变样。线稿图标在哪儿都一样，
        而且能和立绘的暖棕描边对上。
        """
        pen = QPen(col, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if icon == "gear":
            # 齿：八条短粗线（圆头），比抠一整圈齿轮路径省事，缩小也不糊。
            # 内环要**厚、齿要短**：细环长齿在 19px 上会读成"太阳"。
            p.setPen(QPen(col, 2.2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            for i in range(8):
                a = math.radians(i * 45.0 + 22.5)
                p.drawLine(QPointF(cx + math.cos(a) * 6.2, cy + math.sin(a) * 6.2),
                           QPointF(cx + math.cos(a) * 8.2, cy + math.sin(a) * 8.2))
            p.setPen(QPen(col, 2.6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawEllipse(QPointF(cx, cy), 5.6, 5.6)

        elif icon == "paw":
            # 猫爪：她帽子上就绣着一枚，是这套界面里最好用的"品牌记号"。
            # **只画三颗趾**：在 19px 上，四颗趾会横向挤成一条"眉毛"，
            # 整枚读成表情符号；三颗中间留白，才是爪印。
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(cx, cy + 3.2), 4.3, 3.3)
            for dx, dy in ((-3.8, -2.4), (0.0, -4.6), (3.8, -2.4)):
                p.drawEllipse(QPointF(cx + dx, cy + dy), 2.05, 2.15)

        else:                                    # frame：两个叠起来的窗口
            # 偏移必须**同时**横竖挪同样多，才是"叠起来"；只挪一个方向会读成"8"。
            front = QRectF(cx - 8.0, cy - 1.2, 10.2, 8.0)
            back = front.translated(3.5, -3.6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(back, 2.0, 2.0)
            p.setBrush(fill)                     # 前面那块要挡住后面，才读得出"两个"
            p.drawRoundedRect(front, 2.0, 2.0)
            # 顶上一道实心标题栏：没有它，两个圆角方块只是"复制"图标，
            # 有它才一眼是"窗口"（19px 下这是唯一还认得出的窗口特征）
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(front, 2.0, 2.0)
            p.setClipPath(clip)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRect(QRectF(front.left(), front.top(), front.width(), 2.6))
            p.restore()

    # ---------------------------------------------------------------- 名称
    def _paint_pill(self, p: QPainter, c: QPointF, name: str, key: str) -> None:
        """
        悬停名称：奶油色药丸 + 一颗该按钮的深色小点 + 指向圆心的尾巴。

        横向位置**贴着当前这颗圆算**，不放在固定的那一列：弧是弯的，上下两个圆
        比中间那个靠里 21px，固定列会让胶囊离圆忽远忽近，尾巴够不着。
        """
        _bg, deep = _ACCENT[key]
        w = self._pill_w
        gap = 6.0      # 和尾巴一样长 -> 尾尖正好落在圆边上，读作"从圆里长出来"
        if self._side == "left":
            x = c.x() - D / 2 - gap - w
        else:
            x = c.x() + D / 2 + gap
        x = max(1.0, min(x, self.width() - w - 1.0))
        y = max(1.0, min(c.y() - PILL_H / 2, self.height() - PILL_H - 1.0))
        rect = QRectF(x, y, w, PILL_H)
        cy = max(rect.top() + 5.5, min(c.y(), rect.bottom() - 5.5))

        # 尾巴先画：它要被药丸本体压住一半，看着才是"长出来"的
        to_right = self._side == "left"
        edge = rect.right() if to_right else rect.left()
        tip_x = edge + (6.0 if to_right else -6.0)
        tail = QPainterPath()
        tail.moveTo(edge, cy - 3.6)
        tail.lineTo(tip_x, cy)
        tail.lineTo(edge, cy + 3.6)
        tail.closeSubpath()
        p.setPen(QPen(theme.LINE, 1.0, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(theme.CREAM)
        p.drawPath(tail)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.shadow(20))
        p.drawRoundedRect(rect.adjusted(0, 1.4, 0, 2.2), PILL_H / 2, PILL_H / 2)
        p.setBrush(theme.CREAM)
        p.setPen(QPen(theme.LINE, 1.0))
        p.drawRoundedRect(rect, PILL_H / 2, PILL_H / 2)

        # 左侧小点：和圆的强调色呼应，一眼知道说的是哪一个
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(deep)
        dot_x = rect.left() + 10.5
        p.drawEllipse(QPointF(dot_x, rect.center().y()), 2.6, 2.6)

        p.setPen(theme.INK)
        p.setFont(self._font_name)
        p.drawText(QRectF(dot_x + 6.5, rect.y(), w - 22, PILL_H),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   name)

    # ---------------------------------------------------------------- 状态
    def _active_preset(self) -> str:
        """现在单手是哪套玩法 —— 用来给对应的圆加高亮。"""
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
