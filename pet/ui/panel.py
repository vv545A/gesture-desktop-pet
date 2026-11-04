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

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.bond import LEVELS
from ..core.events import PetSay
from . import theme
from .paint import ui_font

# 配色全部来自 ui/theme.py（从立绘采样）。以前这里是 DeepSeek 蓝 —— 那套色是
# 给旧形象（蓝发鲸鱼娘）配的，换成粉发女孩之后，面板冷得像另一个软件。
CARD = theme.CARD
BORDER = theme.LINE
TEXT = theme.INK
TEXT_DIM = theme.INK_DIM
ACCENT = theme.PINK_DEEP          # 强调色换成她发色的深调
TRACK_OFF = theme.TRACK_OFF
ROW_HOVER = theme.ROW_HOVER
DANGER = theme.DANGER
SEG_OFF = QColor(247, 235, 222)   # 没选中的分段 / 次级按钮底（要能和卡片底分开）
BOND_TRACK = QColor(240, 228, 218)  # 亲密度进度条的底槽

MARGIN = 9          # 给阴影留的边
PAD = 12
WIDTH = 236         # 四段式跟随方式要放得下"跟鼠标"三个字，比三段式宽一圈

#: 用「胶囊开关」画法的行；其余 key 一律画成按钮。绘制和点击共用这一份，
#: 不会出现"看着像开关、点了却按按钮逻辑走"的错位。
_SWITCHES = ("enabled", "through", "game", "emotion")


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

        # 亲密度显示板：一行文字 + 一条进度条。
        # 放在最上面，因为它是"只读的展示"，和下面的开关是两类东西，
        # 混在开关堆里会让人以为能点。
        self._bond_line = QRectF(x, y, w, 16)
        y += 19
        self._bond_bar = QRectF(x, y, w, 5)
        y += 17

        for key in ("enabled", "through"):
            self._hits.append((key, QRectF(x, y, w, 30)))
            y += 40

        # 跟随方式：先一行小标题，再三段式选择条
        self._seg_label = QRectF(x + 2, y, w, 14)
        y += 18
        self._seg = QRectF(x, y, w, 26)
        y += 36

        # 玩法：这两项以前只在托盘右键菜单里有，用户点开面板找不到，
        # 就会以为功能没了。摆在同一块面板里，一次点击就够。
        self._play_label = QRectF(x + 2, y, w, 14)
        y += 18
        for key in ("game", "emotion"):
            self._hits.append((key, QRectF(x, y, w, 30)))
            y += 40

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
            p.setBrush(theme.shadow(a))
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
            if key in _SWITCHES:
                self._paint_switch_row(p, key, rect)
            else:
                self._paint_button(p, key, rect)

        self._paint_bond(p)
        self._paint_segment(p)
        self._paint_play_label(p)

        # 底部状态：摄像头到底有没有在工作，是用户最关心的一件事
        p.setFont(self._font_small)
        if getattr(self.control, "enabled", True):
            p.setPen(theme.GOOD)
            note = "摄像头工作中"
        else:
            p.setPen(theme.WARN)
            note = "摄像头已关闭 · 打开开关恢复"
        p.drawText(self._footer, int(Qt.AlignmentFlag.AlignCenter), note)
        p.end()

    def _paint_switch_row(self, p: QPainter, key: str, rect: QRectF) -> None:
        on, label = self._switch_state(key)

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

    def _switch_state(self, key: str) -> Tuple[bool, str]:
        """一行开关的「状态 + 文案」。状态一律从真源头读，面板自己不存。"""
        if key == "enabled":
            # control 可以为 None（单元测试 / headless）：面板照样能画，
            # 只是这个开关没有可拨的东西，不能因此抛异常把整个界面带崩。
            return bool(getattr(self.control, "enabled", True)), "手势识别"
        if key == "through":
            return bool(self.cfg.click_through), "点击穿透"
        if key == "game":
            return self._game_active(), "石头剪刀布"
        return bool(self.cfg.emotion), "情绪感知"

    def _game_active(self) -> bool:
        """
        游戏是不是（即将）在玩。

        `game.active` 要等感知线程下一帧才变 True，而点下去的瞬间只是置了
        `_game_pending`。只看 active 的话，打开面板会看到勾弹回去，
        像"点了没生效"。
        """
        worker = getattr(self.control, "worker", None)
        game = getattr(worker, "game", None)
        return bool(getattr(game, "active", False)) or bool(
            getattr(worker, "_game_pending", False))

    def _paint_play_label(self, p: QPainter) -> None:
        p.setPen(TEXT_DIM)
        p.setFont(self._font_small)
        p.drawText(self._play_label,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "玩法")

    def _paint_bond(self, p: QPainter) -> None:
        """
        亲密度：等级 + 分数 + 相处天数 + 一条到下一级的进度条。

        只读，不参与命中检测——"关系"不是一个能点的开关。
        进度条是这个面板里唯一会动的东西，所以它比数字更能说明"还差多少"。
        """
        bond = getattr(self.win, "bond", None)
        if bond is None:
            return
        b = bond.bond
        idx, name, _greet = b.level

        p.setFont(self._font_small)
        p.setPen(TEXT)
        p.drawText(self._bond_line,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"亲密度 · {name}")
        p.setPen(TEXT_DIM)
        p.drawText(self._bond_line,
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   f"{b.points:.0f} 分 · 第 {bond.days_together()} 天")

        lo = LEVELS[idx][0]
        hi = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else lo
        frac = 1.0 if hi <= lo else max(0.0, min(1.0, (b.points - lo) / (hi - lo)))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BOND_TRACK)
        p.drawRoundedRect(self._bond_bar, 2.5, 2.5)
        if frac > 0:
            fill = QRectF(self._bond_bar)
            fill.setWidth(self._bond_bar.width() * frac)
            p.setBrush(ACCENT)
            p.drawRoundedRect(fill, 2.5, 2.5)

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
                p.setBrush(ROW_HOVER)
            else:
                p.setBrush(SEG_OFF)
            p.drawRoundedRect(rect, 7, 7)
            p.setPen(QColor(255, 255, 255) if active else TEXT)
            p.setFont(self._font_small)
            label = {"hand": "跟手", "cursor": "跟鼠标",
                     "window": "吸附", "corner": "角落"}[key]
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_button(self, p: QPainter, key: str, rect: QRectF) -> None:
        hover = self._hover == key
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ROW_HOVER if hover else SEG_OFF)
        p.drawRoundedRect(rect, 7, 7)
        p.setPen(DANGER if key == "quit" else TEXT)
        p.setFont(self._font)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                   "退出程序" if key == "quit" else "手势对照表")

    # ---------------------------------------------------------------- 交互
    def popup_near(self, anchor) -> None:
        """放在桌宠旁边：优先左边，左边没地方就右边。"""
        from PySide6.QtGui import QGuiApplication

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
            if self.control is not None:
                self.control.toggle()
        elif key == "through":
            on = not bool(self.cfg.click_through)
            self.cfg.click_through = on
            self.win.set_click_through(on)
            if on:
                # 穿透一开就点不到桌宠了，面板必须马上收起来，并用托盘提示
                self.close()
                self.bus.post(PetSay(text="我先隐形啦，右键托盘图标能叫我回来", mood="shy"))
        elif key == "game":
            if self.control is None:
                self.bus.post(PetSay(text="得先有摄像头才能玩哦", mood="shy"))
            else:
                self.control.set_game(not self._game_active())
        elif key == "emotion":
            if self.control is not None:
                # 由 ControlCenter 判断检测器在不在；缺模型时它会说清楚
                self.control.set_emotion(not bool(self.cfg.emotion))
            else:
                self.cfg.emotion = not bool(self.cfg.emotion)
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
