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

import os
import tempfile
from typing import Dict

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap, QPolygonF
from PyQt6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout,
                             QLabel, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from ..core.events import PetSay
from ..intent import mapping

# 下拉框的分组顺序：先窗口（单手主线），再媒体/键盘，最后桌宠专属。
# 顺序本身就是一种引导 —— 用户改单手手势时，最可能想选的就在最上面。
_ORDER = (
    "win_max", "win_min", "win_topmost", "win_snap_left", "win_snap_right",
    "win_desktop",
    "play_pause", "mute", "vol_up", "vol_down", "next_track", "prev_track",
    "alt_tab", "page_down", "page_up",
    "escape", "space", "enter", "scroll_up", "scroll_down",
    "pet:pat",
)

_STYLE = """
QDialog { background: #F6F8FD; }QLabel#title { color: #2A3550; font-size: 15px; font-weight: 600; }
QLabel#hint  { color: #7C88A0; font-size: 12px; }
QLabel#row   { color: #39445E; font-size: 13px; }
QLabel#head  { color: #8B95AC; font-size: 12px; }
QTabWidget::pane { border: 1px solid #DDE4F1; border-radius: 9px; background: #FFFFFF; top: -1px; }
QTabBar::tab {
    background: transparent; color: #7C88A0; padding: 7px 18px;
    border: none; font-size: 13px;
}
QTabBar::tab:selected { color: #4D6BFE; font-weight: 600; }
QTabBar::tab:hover { color: #4D6BFE; }
QComboBox {
    background: #FFFFFF; border: 1px solid #D3DCEC; border-radius: 6px;
    padding: 4px 8px; color: #313B54; min-height: 20px; font-size: 13px;
}
QComboBox:hover { border-color: #A9B8E0; }
QComboBox:focus { border-color: #4D6BFE; }
QComboBox::drop-down {
    width: 18px; border: none;
    subcontrol-origin: padding; subcontrol-position: center right;
}
QComboBox::down-arrow {
    image: url("__ARROW__"); width: 14px; height: 14px; margin-right: 4px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #D3DCEC;
    selection-background-color: #E8EEFF; selection-color: #2A3550;
    outline: none; padding: 2px;
}
QPushButton {
    background: #EEF2FB; border: 1px solid #D8E0F0; border-radius: 7px;
    color: #39445E; padding: 6px 16px; font-size: 13px;
}
QPushButton:hover { background: #E4EBFA; border-color: #B9C6E6; }
QPushButton#primary {
    background: #4D6BFE; border: 1px solid #4D6BFE; color: #FFFFFF; font-weight: 600;
}
QPushButton#primary:hover { background: #3F5CEC; }
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
        pa.setBrush(QColor("#8794B0"))
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
        tabs.addTab(self._build_single(), "单手 · 窗口")
        tabs.addTab(self._build_pair(), "双手 · 音乐")
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
        grid = QGridLayout(page)
        grid.setContentsMargins(14, 12, 14, 12)
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
        grid.setRowStretch(len(mapping.SINGLE_GESTURES) + 1, 1)
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

    def _center(self) -> None:
        from PyQt6.QtGui import QGuiApplication

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
            print(f"[bindings] 保存配置失败：{e}")

    def _save(self) -> None:
        self._apply(self._collect())
        self.bus.post(PetSay(text="对照表改好啦，马上生效", mood="happy"))
        self.accept()

    def _reset(self) -> None:
        self.cfg.bindings = {}
        mapping.apply_bindings({})
        self._sync_from_mapping()
        try:
            self.cfg.save()
        except Exception as e:
            print(f"[bindings] 保存配置失败：{e}")
        self.bus.post(PetSay(text="都改回出厂设置啦", mood="calm"))
