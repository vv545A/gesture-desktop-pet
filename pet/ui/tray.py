# -*- coding: utf-8 -*-
"""
托盘菜单 —— 桌宠的"常驻控制面板"。

桌宠没有主窗口、没有标题栏，用户找不到"设置"在哪里，所以托盘必须承担：
    * 开关手势（等效于桌宠右上角的 ×、点桌宠弹出的面板、双手捏合保持，
      四个入口调用的是同一个 ControlCenter，不会出现"这边开了那边还显示关"）
    * 切换点击穿透（宠物挡住东西时唯一的解法——穿透之后桌宠点不到了，
      只能从这里或从面板里关掉）
    * 切换跟随方式
    * 查看手势表（不用翻 README）
    * 退出

菜单项全部是"状态镜像 + 回调"，不在这里保存业务状态——
真正的状态在 ControlCenter / Config 里，菜单只是它们的一面镜子。
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..core.bus import EventBus
from ..core.engine import GestureEngine
from ..core.events import ControlToggled, PetSay
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
        self.control = getattr(window, "control", None)
        self._on_quit = on_quit

        # 托盘只显示 16~20px，画全身会糊成一个点：这里用"头像"版本
        sp = SpriteSet.from_config(window.cfg)
        icon = QIcon(sp.head_icon(64) if sp is not None else tray_icon(64))
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip(f"{window.cfg.pet_name} · 隔空手势桌宠")

        menu = QMenu()

        self.act_enabled = QAction("手势识别", menu, checkable=True)
        self.act_enabled.setChecked(self._enabled())
        self.act_enabled.triggered.connect(self._toggle_enabled)
        menu.addAction(self.act_enabled)

        act_panel = QAction("打开控制面板（也点一下桌宠）", menu)
        act_panel.triggered.connect(self.window.open_panel)
        menu.addAction(act_panel)

        self.act_through = QAction("点击穿透（宠物不挡鼠标）", menu, checkable=True)
        self.act_through.setChecked(window.cfg.click_through)
        self.act_through.triggered.connect(self._toggle_through)
        menu.addAction(self.act_through)

        menu.addSeparator()

        follow = menu.addMenu("跟随方式")
        group = QActionGroup(follow)
        group.setExclusive(True)
        self._follow_actions = {}
        for key, label in (("hand", "跟着手掌"), ("cursor", "跟着鼠标"),
                           ("window", "吸附到窗口（跟着前台窗口）"),
                           ("corner", "守在角落")):
            a = QAction(label, follow, checkable=True)
            a.setChecked(window.cfg.follow == key)
            a.triggered.connect(lambda _c, k=key: self._set_follow(k))
            group.addAction(a)
            follow.addAction(a)
            self._follow_actions[key] = a

        self.act_game = QAction("玩石头剪刀布（✊ ✌️ ✋）", menu, checkable=True)
        self.act_game.triggered.connect(self._toggle_game)
        menu.addAction(self.act_game)

        menu.addSeparator()

        act_help = QAction("手势对照表（可修改）", menu)
        act_help.triggered.connect(self.window.open_bindings)
        menu.addAction(act_help)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._on_quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        # 状态镜像：不管是谁改的（桌宠按钮 / 面板 / 双手捏合），菜单跟着变。
        # 事件 + 每次展开菜单时再同步一次 —— 面板改的"穿透/跟随方式"不走事件，
        # 展开时补一次最省事，也不会漏。
        self._menu = menu
        menu.aboutToShow.connect(self._sync_menu)
        bus.subscribe(ControlToggled, self._on_toggle_event)

    # ---------------------------------------------------------------- 显示
    def show(self) -> None:
        self.tray.show()

    def _enabled(self) -> bool:
        return bool(self.control.enabled) if self.control is not None else self.engine.enabled

    def _on_activated(self, reason) -> None:
        # 左键点托盘：把宠物叫回来（常见需求：不知道跑哪去了）
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window._place_home(immediate=False)
            self.window.say("在这儿呢～", "happy")

    # ---------------------------------------------------------------- 菜单
    def _on_toggle_event(self, ev) -> None:
        assert isinstance(ev, ControlToggled)
        self.act_enabled.setChecked(ev.enabled)

    def _sync_menu(self) -> None:
        """展开菜单时把所有勾选状态对齐到真实状态（唯一事实来源是 ControlCenter/Config）。"""
        self.act_enabled.setChecked(self._enabled())
        self.act_through.setChecked(bool(self.window.cfg.click_through))
        for key, act in self._follow_actions.items():
            act.setChecked(self.window.cfg.follow == key)
        # 游戏可能因为退出/超时自己结束，勾选要跟着真实状态，不能只信点击
        game = getattr(getattr(self.control, "worker", None), "game", None)
        self.act_game.setChecked(bool(getattr(game, "active", False)))

    def _toggle_game(self, checked: bool) -> None:
        if self.control is None:
            self.act_game.setChecked(False)
            self.bus.post(PetSay(text="得先有摄像头才能玩哦", mood="shy"))
            return
        ok = self.control.set_game(bool(checked))
        # 没进得去（演示模式/没摄像头）就把勾去掉，别留个假勾让人以为在玩
        self.act_game.setChecked(bool(checked) and ok)

    def _toggle_enabled(self, checked: bool) -> None:
        if self.control is not None:
            self.control.set_enabled(checked)      # 它负责发 ControlToggled
        else:
            self.engine.set_enabled(checked)
            self.bus.post(ControlToggled(enabled=checked))

    def _toggle_through(self, checked: bool) -> None:
        self.window.cfg.click_through = checked
        self.window.set_click_through(checked)
        if checked:
            self.bus.post(PetSay(text="我先隐形啦，右键托盘能叫我回来", mood="shy"))

    def _set_follow(self, key: str) -> None:
        self.window.cfg.follow = key
        label = {"hand": "好，我跟着你的手", "cursor": "我吊在你鼠标后头～",
                 "window": "好，我贴着窗口边站", "corner": "我在这儿待着"}.get(key, "")
        self.bus.post(PetSay(text=label, mood="happy"))
