# -*- coding: utf-8 -*-
"""
总开关的唯一入口。

为什么单独抽一个类：
    现在关手势识别有四个入口——托盘菜单、桌宠右上角的关闭键、点桌宠弹出的
    控制面板、双手捏合保持。如果每个入口各写一遍"改引擎状态 + 停摄像头 +
    发事件"，迟早会漏掉其中一步（最容易漏的是**停摄像头**，摄像头指示灯
    一直亮着，用户会以为程序没关掉）。
    所有入口只调 set_enabled()，要补的步骤也只补在这一处。

它不依赖 Qt，也不依赖 MediaPipe：headless 模式照样能用。
"""

from __future__ import annotations

from .bus import EventBus
from .config import Config
from .engine import GestureEngine
from .events import ControlToggled, HandsSeen, PetSay


class ControlCenter:
    def __init__(self, bus: EventBus, engine: GestureEngine, cfg: Config) -> None:
        self.bus = bus
        self.engine = engine
        self.cfg = cfg
        self.worker = None          # PerceptionWorker，demo 模式下为 None
        self._on_quit = None        # 由 app 挂上来（退出要一起停线程、关窗口）

    def attach_worker(self, worker) -> None:
        """感知线程建好之后挂上来（它可以暂停/恢复摄像头）。"""
        self.worker = worker
        if worker is not None and hasattr(worker, "set_active"):
            worker.set_active(self.engine.enabled)

    def attach_quit(self, fn) -> None:
        self._on_quit = fn

    def request_quit(self) -> None:
        if self._on_quit is not None:
            self._on_quit()

    @property
    def enabled(self) -> bool:
        return bool(self.engine.enabled)

    def set_enabled(self, on: bool) -> bool:
        """开/关手势识别（引擎状态 + 摄像头 + 广播事件）。返回切换后的状态。"""
        on = self.engine.set_enabled(bool(on))
        if self.worker is not None and hasattr(self.worker, "set_active"):
            self.worker.set_active(on)
        if not on:
            # 关掉之后立刻把"看到手"的状态清掉：不然桌宠会停在"盯着手看"
            # 的表情上，而屏幕上其实已经没手了。
            self.bus.post(HandsSeen(count=0))
        self.bus.post(ControlToggled(enabled=on))
        return on

    def toggle(self) -> bool:
        return self.set_enabled(not self.engine.enabled)

    # ---------------------------------------------------------------- 小游戏
    def set_game(self, on: bool) -> bool:
        """
        进入/退出石头剪刀布。

        没有感知线程（--demo、--headless、摄像头起不来）时**明确说一句**，
        而不是静默失败——点菜单没反应是最让人恼火的一种交互。
        """
        if self.worker is None or not hasattr(self.worker, "set_game"):
            self.bus.post(PetSay(text="得先有摄像头才能玩哦", mood="shy"))
            return False
        ok = self.worker.set_game(on)
        self.bus.post(PetSay(text="三秒后出拳！✊石头 ✌️剪刀 ✋布" if ok else "不玩啦～",
                             mood="happy" if ok else "calm"))
        return ok

    def toggle_game(self) -> bool:
        playing = getattr(getattr(self.worker, "game", None), "active", False)
        return self.set_game(not playing)
