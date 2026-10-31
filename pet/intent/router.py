# -*- coding: utf-8 -*-
"""
意图路由 —— 把意图变成"真的发生了什么"，并把结果回报给界面。

为什么执行放在后台线程：
    Alt+Tab、Ctrl+滚轮这些调用会 sleep 20~40ms，放在 Qt 主线程上做，
    桌宠的动画就会一顿一顿的。手势本来就是 30FPS 的节拍，卡一下很明显。
    执行完只往总线发一个 ActionDone，界面拿到时是纯数据。
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple

from ..core.bus import EventBus
from ..core.engine import Fired
from ..core.events import ActionDone, ContinuousAction, GestureFired, PetSay
from . import mapping
from .mapping import Intent


class IntentRouter:
    def __init__(self, bus: EventBus, dry_run: bool = False) -> None:
        self.bus = bus
        self.dry_run = dry_run
        self._actions = None
        self._load_error = ""
        self._loaded = False

    # ---------------------------------------------------------------- 懒加载
    def _mod(self):
        """
        延迟导入 actions：它一 import 就会检查平台并加载 user32。
        没有装 Windows 依赖（或将来做了跨平台）时，这里失败也不该让桌宠起不来。
        """
        if not self._loaded:
            self._loaded = True
            if sys.platform != "win32":
                self._load_error = "当前系统不是 Windows，系统动作不可用"
            else:
                try:
                    import actions as _a
                    self._actions = _a
                except Exception as e:      # 极少见，但别让骨架直接崩
                    self._load_error = str(e)
        return self._actions

    # ---------------------------------------------------------------- 离散
    def dispatch_fired(self, fired: Fired) -> None:
        intent = mapping.resolve(fired.side, fired.gesture)
        if intent is None:
            return

        self.bus.post(GestureFired(gesture=fired.gesture, side=fired.side,
                                   intent=intent.key, label=intent.label))
        ok, detail, bubble = self._perform(intent)
        self.bus.post(ActionDone(intent=intent.key, label=intent.label, ok=ok,
                                 detail=detail,
                                 bubble=bubble,
                                 mood=intent.mood if ok else "surprised"))

    # ---------------------------------------------------------------- 连续
    def dispatch_continuous(self, kind: str, value: float) -> None:
        intent = mapping.continuous(kind)
        if intent is None:
            return
        ok, detail, bubble = self._perform(intent, value)
        # 连续量走独立事件：界面只抖一下，不弹气泡（缩放一秒能触发十几次）
        self.bus.post(ContinuousAction(intent=intent.key, label=intent.label,
                                       value=value))
        if not ok and bubble:
            # 例外：被"桌面图标保护"挡住时值得说一句，否则用户以为失灵了
            self.bus.post(PetSay(text=bubble, mood="surprised"))
        elif bubble:
            # 只有"复位"这类一次性连续量才有台词（zoom/scroll 的 bubble 是空的）
            self.bus.post(PetSay(text=bubble, mood=intent.mood))

    # ---------------------------------------------------------------- 执行
    def _perform(self, intent: Intent, value: float = 0.0) -> Tuple[bool, str, str]:
        """返回 (成功, 细节, 要显示的话)。"""
        # 1) 纯桌宠反应：不碰系统，永远安全
        if intent.key.startswith("pet:"):
            return True, "pet-only", intent.bubble

        # 2) 演示模式：只走流程，不发按键
        if self.dry_run:
            return True, "dry-run（未发送按键）", intent.bubble

        mod = self._mod()
        if mod is None:
            return False, self._load_error or "动作模块不可用", "我发不出按键…"

        try:
            # 3) 连续量
            if intent.key == "zoom":
                res = mod.zoom(int(value))
                if res == "blocked":
                    return False, "桌面图标保护", "这里缩放会动你的图标哦"
                return (res == "ok"), res, intent.bubble
            if intent.key == "zoom_reset":
                res = mod.zoom_reset()
                if res == "blocked":
                    return False, "桌面图标保护", "这儿没法复位～"
                return (res == "ok"), res, intent.bubble
            if intent.key == "scroll":
                mod.scroll(-value)         # 手往上 -> 页面向下（同 main.py）
                return True, "scroll", intent.bubble

            # 4) 离散动作
            ok = mod.run_action(intent.key)
            return ok, "" if ok else "run_action 返回 False", intent.bubble
        except Exception as e:
            return False, str(e), "唔…手滑了"
