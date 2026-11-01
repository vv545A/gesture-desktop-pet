# -*- coding: utf-8 -*-
"""
桌宠的弹出式控制面板 —— 点一下桌宠就出来，再点空白处自己消失。

为什么需要它：
    桌宠没有标题栏、没有菜单栏，用户第一次用根本不知道"开关在哪"。
    托盘菜单虽然全，但藏在右下角小图标里；而"点桌宠"是任何人都会先试的动作。
    所以这里把最常用的四件事（关手势、穿透、跟随、对照表）放在一次点击之内。

实现要点
    Qt.Popup        —— 点面板外面自动关闭，不用自己监听全局点击。
    全自绘          —— 和桌宠用同一套字体/配色，不引入 QSS 那套字符串样式
                       （QSS 在透明窗口上会把圆角画成方的）。
    状态不存这里    —— 开关的真实状态在 ControlCenter / Config 里，面板每帧
                       从它们那里读，所以托盘改了什么这边立刻同步。
"""

from __future__ import annotations

from typing import List, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ..core.events import PetSay
from .paint import ui_font

# 配色沿用桌宠的 DeepSeek 蓝，浅色卡片 + 一点点阴影
CARD = QColor(252, 253, 255, 246)
BORDER = QColor(208, 217, 232, 255)
TEXT = QColor(38, 48, 70)
TEXT_DIM = QColor(128, 140, 160)
ACCENT = QColor(77, 107, 254)
TRACK_OFF = QColor(206, 213, 226)
ROW_HOVER = QColor(240, 244, 252, 255)
DANGER = QColor(214, 78, 92)

MARGIN = 9          # 给阴影留的边
PAD = 12
WIDTH = 236         # 四段式跟随方式要放得下"跟鼠标"三个字，比三段式宽一圈


class ControlPanel(QWidget):
    """点桌宠弹出的开关面板。只做"读状态 + 回调"，不存状态。"""

    def __init__(self, window) -> None:
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.win = window
        self.bus = window.bus
        self.cfg = window.cfg
        self.control = window.control
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setWindowTitle("控制台")

        self._font = ui_font(9)
        self._font_small = ui_font(8)
        self._font_title = ui_font(10, bold=True)
        self._hover = ""
        self._hits: List[Tuple[str, QRectF]] = []
        self._build()
        self.resize(WIDTH, self._height)

    # ---------------------------------------------------------------- 布局
    def _build(self) -> None:
        """算一次行位置，绘制和命中检测共用，永远不会对不上。"""
        self._hits = []
        x = MARGIN + PAD
        w = WIDTH - 2 * (MARGIN + PAD)
        y = MARGIN + PAD + 26         # 标题占掉 26

        for key in ("enabled", "through"):
            self._hits.append((key, QRectF(x, y, w, 30)))
            y += 40

        # 跟随方式：先一行小标题，再三段式选择条
        self._seg_label = QRectF(x + 2, y, w, 14)
        y += 18
        self._seg = QRectF(x, y, w, 26)
        y += 36

        for key in ("help", "quit"):
            self._hits.append((key, QRectF(x, y, w, 27)))
            y += 34

        # 底部状态行：预留在布局里（而不是关掉时临时插一行），
        # 否则开关一拨整个面板就跳一下，看着像出 bug 了
        self._footer = QRectF(x, y, w, 16)
        y += 18

        self._height = int(y + PAD + MARGIN - 6)

    def _seg_rects(self) -> List[Tuple[str, QRectF]]:
        keys = (("hand", "跟手"), ("cursor", "跟鼠标"),
                ("window", "吸附"), ("corner", "角落"))
        gap = 5
        seg_w = (self._seg.width() - gap * (len(keys) - 1)) / len(keys)
        out = []
        for i, (k, _label) in enumerate(keys):
            out.append((k, QRectF(self._seg.x() + i * (seg_w + gap),
                                  self._seg.y(), seg_w, self._seg.height())))
        return out

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        card = QRectF(MARGIN, MARGIN, self.width() - 2 * MARGIN,
                      self.height() - 2 * MARGIN)

        # 阴影：几层递减的圆角矩形，比 QGraphicsDropShadowEffect 稳
        # （effect 加在透明顶层窗口上时，有的驱动会把整块变成黑底）
        for i, a in ((4, 16), (3, 22), (2, 30)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(24, 34, 60, a))
            p.drawRoundedRect(card.adjusted(-i, -i + 2, i, i + 3), 14 + i, 14 + i)

        p.setBrush(CARD)
        p.setPen(QPen(BORDER, 1))
        p.drawRoundedRect(card, 14, 14)

        # 标题
        p.setPen(TEXT_DIM)
        p.setFont(self._font_title)
        p.drawText(QRectF(card.x() + PAD, card.y() + PAD - 2, card.width() - 2 * PAD, 20),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"{self.cfg.pet_name} · 控制台")

        for key, rect in self._hits:
            if key in ("enabled", "through"):
                self._paint_switch_row(p, key, rect)
            else:
                self._paint_button(p, key, rect)

        self._paint_segment(p)

        # 底部状态：摄像头到底有没有在工作，是用户最关心的一件事
        p.setFont(self._font_small)
        if self.control.enabled:
            p.setPen(QColor(108, 150, 122))
            note = "摄像头工作中"
        else:
            p.setPen(QColor(168, 130, 58))
            note = "摄像头已关闭 · 打开开关恢复"
        p.drawText(self._footer, int(Qt.AlignmentFlag.AlignCenter), note)
        p.end()

    def _paint_switch_row(self, p: QPainter, key: str, rect: QRectF) -> None:
        on = self.control.enabled if key == "enabled" else bool(self.cfg.click_through)
        label = "手势识别" if key == "enabled" else "点击穿透"

        if self._hover == key:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ROW_HOVER)
            p.drawRoundedRect(rect, 8, 8)

        p.setPen(TEXT)
        p.setFont(self._font)
        p.drawText(QRectF(rect.x() + 6, rect.y(), rect.width() - 60, rect.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)

        # 开关本体：34x18 的胶囊 + 会滑动的圆钮
        tw, th = 34.0, 18.0
        tx = rect.right() - tw - 6
        ty = rect.center().y() - th / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT if on else TRACK_OFF)
        p.drawRoundedRect(QRectF(tx, ty, tw, th), th / 2, th / 2)
        p.setBrush(QColor(255, 255, 255))
        d = th - 4
        p.drawEllipse(QPointF(tx + (tw - d - 2 if on else 3) + d / 2, ty + th / 2), d / 2, d / 2)

    def _paint_segment(self, p: QPainter) -> None:
        p.setPen(TEXT_DIM)
        p.setFont(self._font_small)
        p.drawText(self._seg_label,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "跟随方式")
        for key, rect in self._seg_rects():
            active = (self.cfg.follow == key)
            p.setPen(Qt.PenStyle.NoPen)
            if active:
                p.setBrush(ACCENT)
            elif self._hover == key:
                p.setBrush(QColor(226, 232, 245))
            else:
                p.setBrush(QColor(238, 242, 250))
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QColor(255, 255, 255) if active else TEXT)
            p.setFont(self._font_small)
            label = {"hand": "跟手", "cursor": "跟鼠标",
                     "window": "吸附", "corner": "角落"}[key]
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_button(self, p: QPainter, key: str, rect: QRectF) -> None:
        hover = self._hover == key
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ROW_HOVER if hover else QColor(243, 246, 252))
        p.drawRoundedRect(rect, 7, 7)
        p.setPen(DANGER if key == "quit" else TEXT)
        p.setFont(self._font)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                   "退出程序" if key == "quit" else "手势对照表")

    # ---------------------------------------------------------------- 交互
    def popup_near(self, anchor) -> None:
        """放在桌宠旁边：优先左边，左边没地方就右边。"""
        from PyQt6.QtGui import QGuiApplication

        scr = QGuiApplication.primaryScreen()
        area = scr.availableGeometry() if scr else None
        g = anchor.frameGeometry()
        x = g.left() - self.width() + 12
        if area is not None and x < area.left():
            x = g.right() - 12
        y = g.top() + int(g.height() * 0.28)
        if area is not None:
            y = max(area.top(), min(y, area.bottom() - self.height()))
            x = max(area.left(), min(x, area.right() - self.width()))
        self.move(int(x), int(y))
        self.show()

    def _hit(self, pos: QPointF) -> str:
        for key, rect in self._hits:
            if rect.contains(pos):
                return key
        for key, rect in self._seg_rects():
            if rect.contains(pos):
                return key
        return ""

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        key = self._hit(ev.position())
        if key != self._hover:
            self._hover = key
            self.update()
        ev.accept()

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self._hover = ""
        self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        key = self._hit(ev.position())
        if not key:
            ev.accept()
            return
        if key == "enabled":
            self.control.toggle()
        elif key == "through":
            on = not bool(self.cfg.click_through)
            self.cfg.click_through = on
            self.win.set_click_through(on)
            if on:
                # 穿透一开就点不到桌宠了，面板必须马上收起来，并用托盘提示
                self.close()
                self.bus.post(PetSay(text="我先隐形啦，右键托盘图标能叫我回来", mood="shy"))
        elif key in ("hand", "cursor", "window", "corner"):
            self.cfg.follow = key
            say = {"hand": "好，我跟着你的手",
                   "cursor": "我吊在你鼠标后头，鼠标停我身上才点得到我",
                   "window": "好，我贴着窗口边站，你按哪个窗口我就站哪个边上",
                   "corner": "我在这儿待着"}.get(key, "")
            self.bus.post(PetSay(text=say, mood="happy"))
        elif key == "help":
            # 打开可编辑的对照表页面（不再是"打一行字到控制台"——
            # 打包成无控制台的程序之后，那行字用户根本看不见）
            self.close()
            self.win.open_bindings()
            return
        elif key == "quit":
            self.control.request_quit()
            return
        self.update()
        ev.accept()
