# -*- coding: utf-8 -*-
"""
手势对照表 —— 一个能看、也能改的页面。

为什么不再是"打一行文字到控制台"：
    对照表是用户唯一能确认"我比这个手势会怎样"的地方。以前它只读、还只
    打到控制台（打包成无控制台的程序后就彻底看不见了），用户想换个手势
    只能翻 README 改代码。现在把它做成真页面：左边手势、右边下拉框，
    改完点保存，立刻生效并存进 pet_config.json。

布局为什么是"两页 + 左右手两列"：
    单手有 7 个手势、左右手各一套映射，全平铺是 14 行，屏幕矮一点就放不下。
    按左右手分列之后是 7 行 —— 一眼能看完，而且左右手对照着看很直观。
    双手只有一套映射，单独一页。

单一事实来源：
    选项列表来自 intent/mapping.py 的 ACTION_CATALOG，当前值来自
    current_bindings()。这里不硬编码任何一条映射，否则改一处漏一处。
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Dict

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout,
                             QLabel, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from ..core.events import PetSay
from ..intent import mapping

# 下拉框的分组顺序：先窗口（默认主线），再桌宠互动，最后媒体/键盘。
# 顺序本身就是一种引导 —— 用户改单手手势时，最可能想选的就在最上面。
# 桌宠互动没删，只是退到第二组：它仍然可以整张表切过去，只是不是默认玩法。
_ORDER = (
    "win_max", "win_min", "win_topmost", "win_snap_left", "win_snap_right",
    "win_desktop",
    "pet:dance", "pet:sleep", "pet:wake", "pet:follow", "pet:pat",
    "open_browser",
    "play_pause", "mute", "vol_up", "vol_down", "next_track", "prev_track",
    "alt_tab", "page_down", "page_up",
    "escape", "space", "enter", "scroll_up", "scroll_down",
)

_STYLE = """
/* 颜色全部对齐 ui/theme.py（从立绘采样的暖米白 + 玫瑰粉），
   和圆按钮 / 控制面板是同一套；以前这里是冷蓝，点进来像换了个软件。 */
QDialog { background: #FDF7EE; }
QLabel#title { color: #5D403A; font-size: 15px; font-weight: 600; }
QLabel#hint  { color: #8A6A62; font-size: 12px; }
QLabel#row   { color: #5D403A; font-size: 13px; }
QLabel#head  { color: #8A6A62; font-size: 12px; }
QTabWidget::pane { border: 1px solid #E2CBC0; border-radius: 9px; background: #FFFCF6; top: -1px; }
QTabBar::tab {
    background: transparent; color: #8A6A62; padding: 7px 18px;
    border: none; font-size: 13px;
}
QTabBar::tab:selected { color: #C4647A; font-weight: 600; }
QTabBar::tab:hover { color: #C4647A; }
QComboBox {
    background: #FFFCF6; border: 1px solid #E2CBC0; border-radius: 6px;
    padding: 4px 8px; color: #5D403A; min-height: 20px; font-size: 13px;
}
QComboBox:hover { border-color: #D9A9B4; }
QComboBox:focus { border-color: #C4647A; }
QComboBox::drop-down {
    width: 18px; border: none;
    subcontrol-origin: padding; subcontrol-position: center right;
}
QComboBox::down-arrow {
    image: url("__ARROW__"); width: 14px; height: 14px; margin-right: 4px;
}
QComboBox QAbstractItemView {
    background: #FFFCF6; border: 1px solid #E2CBC0;
    selection-background-color: #FCE8EA; selection-color: #5D403A;
    outline: none; padding: 2px;
}
QPushButton {
    background: #F7EBDE; border: 1px solid #E2CBC0; border-radius: 7px;
    color: #5D403A; padding: 6px 16px; font-size: 13px;
}
QPushButton:hover { background: #F2E2D2; border-color: #D9A9B4; }
QPushButton#primary {
    background: #C4647A; border: 1px solid #C4647A; color: #FFFFFF; font-weight: 600;
}
QPushButton#primary:hover { background: #B2556B; }
QPushButton#preset {
    background: #FFFCF6; border: 1px solid #E2CBC0; color: #8A6A62;
    padding: 4px 14px; border-radius: 13px; font-size: 12px;
}
QPushButton#preset:hover { border-color: #D9A9B4; color: #5D403A; }
QPushButton#preset:checked {
    background: #FCE8EA; border: 1px solid #C4647A; color: #A44E63; font-weight: 600;
}
"""


def _arrow_uri() -> str:
    """
    现画一个下拉箭头贴到 QSS 里。

    为什么不用 QSS 的 border 三角技巧：那招在 Qt6 的 QComboBox 子控件上会
    被画成一个实心方块（试过），比没有箭头更难看。自己画一张 14x14 的 PNG
    写到临时目录，最可控，也不往仓库里塞二进制资源。
    """
    try:
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        pa = QPainter(pm)
        pa.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pa.setPen(Qt.PenStyle.NoPen)
        pa.setBrush(QColor("#8A6A62"))      # 暖棕，和 theme.INK_DIM 一致
        pa.drawPolygon(QPolygonF([QPointF(4.0, 5.2), QPointF(10.0, 5.2), QPointF(7.0, 10.0)]))
        pa.end()
        path = os.path.join(tempfile.gettempdir(), "pet_combo_arrow.png")
        if pm.save(path):
            return path.replace("\\", "/")
    except Exception:
        pass
    return ""


class BindingsDialog(QDialog):
    """手势 -> 动作 的可编辑对照表。非模态：开着它也能继续比手势。"""

    def __init__(self, window) -> None:
        super().__init__(None)
        self.win = window
        self.cfg = window.cfg
        self.bus = window.bus
        self._combos: Dict[str, QComboBox] = {}

        self.setWindowTitle(f"{self.cfg.pet_name} · 手势对照表")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        arrow = _arrow_uri()
        style = _STYLE.replace("__ARROW__", arrow)
        if not arrow:
            style = style.replace('image: url(""); width: 14px; height: 14px;', "")
        self.setStyleSheet(style)
        self.setFont(QFont(self.font().family(), 9))
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        title = QLabel("手势 → 功能")
        title.setObjectName("title")
        root.addWidget(title)

        hint = QLabel("选好之后点保存，立刻生效，不用重开程序。"
                      "「不绑定」= 这个手势什么都不做。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        tabs = QTabWidget()
        tabs.addTab(self._build_single(), "单手 · 窗口/桌宠")
        tabs.addTab(self._build_pair(), "双手 · 音乐")
        tabs.addTab(self._build_extra(), "长按 / 挥手")
        root.addWidget(tabs, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset)
        foot.addWidget(btn_reset)
        foot.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        foot.addWidget(btn_cancel)
        btn_ok = QPushButton("保存")
        btn_ok.setObjectName("primary")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._save)
        foot.addWidget(btn_ok)
        root.addLayout(foot)

        self._sync_from_mapping()
        self.adjustSize()
        self._center()

    # ---------------------------------------------------------------- 布局
    def _combo(self, key: str, value: str) -> QComboBox:
        cb = QComboBox()
        cb.setMinimumWidth(148)
        cb.addItem("不绑定", "")
        for action in _ORDER:
            meta = mapping.ACTION_CATALOG.get(action)
            if meta is not None:
                cb.addItem(meta[0], action)
        idx = cb.findData(value)
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._combos[key] = cb
        return cb

    def _build_single(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        # 整表切换：单手有 14 个格子，逐个点下拉框换"玩法"是纯体力活。
        # 这两个按钮就是"两条路"的入口 —— 点一下整套换掉，立刻生效。
        bar = QHBoxLayout()
        bar.setSpacing(6)
        cap = QLabel("单手整表：")
        cap.setObjectName("head")
        bar.addWidget(cap)
        self._preset_btns: Dict[str, QPushButton] = {}
        for name in mapping.SINGLE_PRESETS:
            b = QPushButton(mapping.SINGLE_PRESET_LABEL.get(name, name))
            b.setObjectName("preset")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, n=name: self._apply_preset(n))
            bar.addWidget(b)
            self._preset_btns[name] = b
        bar.addStretch(1)
        outer.addLayout(bar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)

        for col, text in ((0, "手势"), (1, "左手"), (2, "右手")):
            lab = QLabel(text)
            lab.setObjectName("head")
            grid.addWidget(lab, 0, col)

        cur = mapping.current_bindings()
        for row, g in enumerate(mapping.SINGLE_GESTURES, start=1):
            lab = QLabel(mapping.GESTURE_LABEL.get(g, g))
            lab.setObjectName("row")
            grid.addWidget(lab, row, 0)
            for col, side in ((1, "left"), (2, "right")):
                key = mapping.binding_key(side, g)
                grid.addWidget(self._combo(key, cur.get(key, "")), row, col)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        outer.addLayout(grid)
        outer.addStretch(1)
        return page

    def _build_pair(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)

        for col, text in ((0, "手势"), (1, "两只手都做这个手势时")):
            lab = QLabel(text)
            lab.setObjectName("head")
            grid.addWidget(lab, 0, col)

        cur = mapping.current_bindings()
        for row, g in enumerate(mapping.PAIR_GESTURES, start=1):
            lab = QLabel(mapping.GESTURE_LABEL.get(g, g))
            lab.setObjectName("row")
            grid.addWidget(lab, row, 0)
            key = mapping.binding_key("both", g)
            grid.addWidget(self._combo(key, cur.get(key, "")), row, 1)

        note = QLabel("双手同时捏合并保持约 1 秒 = 手势识别总开关（这个是固定的）")
        note.setObjectName("hint")
        grid.addWidget(note, len(mapping.PAIR_GESTURES) + 1, 0, 1, 2)

        grid.setColumnStretch(1, 1)
        grid.setRowStretch(len(mapping.PAIR_GESTURES) + 2, 1)
        return page

    def _build_extra(self) -> QWidget:
        """
        长按 / 挥手 —— 单手的"第二层"和"动能形态"。

        为什么单独一页而不是塞进单手表：这两类没有"哪只手"这一维
        （长按只认手势，挥手只认方向），和 14 格子的左右手表不是同一种结构，
        混在一起会让"哪一格对应哪个下拉框"变得难解释。

        为什么要把"再按住 1 秒"写出来：长按是**看不见的**动作——
        用户不做一次永远不知道有这层。这一页本身就是说明书。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(7)

        sec = max(0.5, float(getattr(self.cfg, "long_press", 1.0)) + self.cfg.hold)

        tip_long = QLabel(f"长按 —— 稳住手势后继续别松手，约 {sec:.1f} 秒进入第二层。"
                          "只挂「结果能盖掉」的动作，多做一次也不会出事。")
        tip_long.setObjectName("hint")
        tip_long.setWordWrap(True)
        outer.addWidget(tip_long)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(7)
        for col, text in ((0, "手势"), (1, "按住不放时")):
            lab = QLabel(text)
            lab.setObjectName("head")
            grid.addWidget(lab, 0, col)

        cur = mapping.current_bindings()
        for row, g in enumerate(mapping.SINGLE_GESTURES, start=1):
            lab = QLabel(mapping.GESTURE_LABEL.get(g, g))
            lab.setObjectName("row")
            grid.addWidget(lab, row, 0)
            key = mapping.LONG_PREFIX + g
            grid.addWidget(self._combo(key, cur.get(key, "")), row, 1)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        outer.addSpacing(10)

        tip_swipe = QLabel("挥手 —— 张开手掌横向划过去，0.2 秒就完成，"
                           "不需要稳住。做握拳或剪刀手时晃手不会误触发。")
        tip_swipe.setObjectName("hint")
        tip_swipe.setWordWrap(True)
        outer.addWidget(tip_swipe)

        g2 = QGridLayout()
        g2.setHorizontalSpacing(10)
        g2.setVerticalSpacing(7)
        for col, text in ((0, "方向"), (1, "动作")):
            lab = QLabel(text)
            lab.setObjectName("head")
            g2.addWidget(lab, 0, col)
        for row, (direction, name) in enumerate(
                (("left", "往左划"), ("right", "往右划")), start=1):
            lab = QLabel(name)
            lab.setObjectName("row")
            g2.addWidget(lab, row, 0)
            key = mapping.SWIPE_PREFIX + direction
            g2.addWidget(self._combo(key, cur.get(key, "")), row, 1)
        g2.setColumnStretch(1, 1)
        outer.addLayout(g2)

        outer.addStretch(1)
        return page

    def _center(self) -> None:
        from PySide6.QtGui import QGuiApplication

        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        a = scr.availableGeometry()
        self.move(a.center().x() - self.width() // 2,
                  a.center().y() - self.height() // 2)

    # ---------------------------------------------------------------- 数据
    def showEvent(self, ev) -> None:  # noqa: N802
        # 每次显示都重新读一次生效值：用户可能上次是"没保存就关掉了"，
        # 再打开却看到一堆没生效的旧选择，那是真的会让人以为设置丢了。
        super().showEvent(ev)
        self._sync_from_mapping()

    def _sync_from_mapping(self) -> None:
        """把现在真正生效的绑定灌回下拉框（打开页面 / 恢复默认都走这里）。"""
        cur = mapping.current_bindings()
        for key, cb in self._combos.items():
            idx = cb.findData(cur.get(key, ""))
            cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._sync_preset_buttons()

    def _sync_preset_buttons(self) -> None:
        """
        高亮"现在用的是哪套预设"。

        用"命中过半"判断而不是逐字比对（见 mapping.current_single_preset）：
        用户改了一条就整套都不高亮，会让人以为自己把表搞坏了。
        """
        cur = mapping.current_single_preset()
        for name, btn in getattr(self, "_preset_btns", {}).items():
            btn.setChecked(name == cur)

    def _apply_preset(self, name: str) -> None:
        """
        整套换掉单手表，并**立刻生效**（不用再点保存）。

        为什么不等"保存"：预设按钮的语义是"我现在换一种玩法"，
        换完还要再点一次保存，中间那段时间界面和实际行为是不一致的——
        用户比个 ✊，看到的还是旧动作。宁可多存一次盘。
        """
        rows = mapping.preset_bindings(name)
        if not rows:
            return
        for key, action in rows.items():
            cb = self._combos.get(key)
            if cb is None:
                continue
            idx = cb.findData(action)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        self._apply(self._collect())
        self._sync_preset_buttons()
        label = mapping.SINGLE_PRESET_LABEL.get(name, name)
        text = "好，单手都改成操作窗口啦" if name == "window" else "好，单手陪你玩～"
        self.bus.post(PetSay(text=f"{text}（{label}）", mood="happy", anim="nod"))

    def _collect(self) -> Dict[str, str]:
        return {key: (cb.currentData() or "") for key, cb in self._combos.items()}

    def _apply(self, rows: Dict[str, str]) -> None:
        # 只存"和默认不一样"的条目：没改的继续跟默认表走，
        # 以后默认值升级也能吃到（见 mapping.apply_bindings 的注释）。
        default = mapping.default_bindings()
        overrides = {k: v for k, v in rows.items() if default.get(k) != v}
        self.cfg.bindings = overrides
        mapping.apply_bindings(overrides)
        try:
            self.cfg.save()
        except Exception as e:
            logging.getLogger(__name__).error("保存配置失败：%s", e)

    def _save(self) -> None:
        self._apply(self._collect())
        # 带一个点头动作：确认类反馈光说话不够，"点头"这个动作才是"我记住了"
        self.bus.post(PetSay(text="对照表改好啦，马上生效", mood="happy",
                             anim="nod"))
        self.accept()

    def _reset(self) -> None:
        self.cfg.bindings = {}
        mapping.apply_bindings({})
        self._sync_from_mapping()
        try:
            self.cfg.save()
        except Exception as e:
            logging.getLogger(__name__).error("保存配置失败：%s", e)
        self.bus.post(PetSay(text="都改回出厂设置啦", mood="calm", anim="nod"))
