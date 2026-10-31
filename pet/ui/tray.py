# -*- coding: utf-8 -*-
"""
托盘菜单 —— 桌宠的"控制面板"。

桌宠没有主窗口、没有标题栏，用户找不到"设置"在哪里，所以托盘必须承担：
    * 开关手势（和"双手 ✊✋✊✋"等效，是鼠标党的备用通道）
    * 切换点击穿透（宠物挡住东西时唯一的解法）
    * 切换跟随方式
    * 查看手势表（不用翻 README）
    * 退出

菜单项全部是"状态镜像 + 回调"，不在这里保存业务状态——
真正的状态在 GestureEngine 和 Config 里，菜单只是它们的一面镜子。
"""

from __future__ import annotations

from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core.bus import EventBus
from ..core.engine import GestureEngine
from ..core.events import ControlToggled, PetSay
from ..intent import mapping
from .paint import tray_icon
from .sprite import SpriteSet
from .window import PetWindow


class PetTray:
    def __init__(self, app: QApplication, window: PetWindow, bus: EventBus,
                 engine: GestureEngine, on_quit) -> None:
        self.app = app
        self.window = window
        self.bus = bus
        self.engine = engine
        self._on_quit = on_quit

        # 托盘只显示 16~20px，画全身会糊成一个点：这里用"头像"版本
        sp = SpriteSet.from_config(window.cfg)
        icon = QIcon(sp.head_icon(64) if sp is not None else tray_icon(64))
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip(f"{window.cfg.pet_name} · 隔空手势桌宠")

        menu = QMenu()

        self.act_enabled = QAction("手势控制", menu, checkable=True)
        self.act_enabled.setChecked(engine.enabled)
        self.act_enabled.triggered.connect(self._toggle_enabled)
        menu.addAction(self.act_enabled)

        self.act_through = QAction("点击穿透（宠物不挡鼠标）", menu, checkable=True)
        self.act_through.setChecked(window.cfg.click_through)
        self.act_through.triggered.connect(self._toggle_through)
        menu.addAction(self.act_through)

        menu.addSeparator()

        follow = menu.addMenu("跟随方式")
        group = QActionGroup(follow)
        group.setExclusive(True)
        for key, label in (("hand", "跟着手掌"), ("cursor", "跟着鼠标"),
                           ("corner", "守在角落")):
            a = QAction(label, follow, checkable=True)
            a.setChecked(window.cfg.follow == key)
            a.triggered.connect(lambda _c, k=key: self._set_follow(k))
            group.addAction(a)
            follow.addAction(a)

        act_help = QAction("手势对照表", menu)
        act_help.triggered.connect(self._show_help)
        menu.addAction(act_help)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._on_quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    # ---------------------------------------------------------------- 显示
    def show(self) -> None:
        self.tray.show()

    def _on_activated(self, reason) -> None:
        # 左键点托盘：把宠物叫回来（常见需求：不知道跑哪去了）
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window._place_home(immediate=False)
            self.window.say("在这儿呢～", "happy")

    # ---------------------------------------------------------------- 菜单
    def _toggle_enabled(self, checked: bool) -> None:
        self.engine.set_enabled(checked)
        self.bus.post(ControlToggled(enabled=checked))

    def _toggle_through(self, checked: bool) -> None:
        self.window.cfg.click_through = checked
        self.window.set_click_through(checked)
        if checked:
            self.bus.post(PetSay(text="我先隐形啦，右键托盘能叫我回来", mood="shy"))

    def _set_follow(self, key: str) -> None:
        self.window.cfg.follow = key
        label = {"hand": "好，我跟着你的手", "cursor": "跟着鼠标走～",
                 "corner": "我在这儿待着"}.get(key, "")
        self.bus.post(PetSay(text=label, mood="happy"))

    def _show_help(self) -> None:
        text = (mapping.describe_table()
                + "\n\n双手：捏合拉开 = 缩放 · 两下捏合 = 复位 · "
                  "同时张开上下移 = 滚动\n双手 ✊✋✊✋ 两遍 = 开关全部控制")
        self.bus.post(PetSay(text=text[:60] + "…（完整表在 README）", mood="calm"))
        print("\n" + text + "\n")
