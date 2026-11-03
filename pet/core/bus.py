# -*- coding: utf-8 -*-
"""
事件总线 —— 线程安全的"投递 + 主线程分发"。

设计要点（这几条决定了整个骨架好不好扩展）：

1. post() 可以在**任意线程**调用，只做入队，永不阻塞。
   感知线程 30FPS 地发事件，绝不能因为界面卡一下就把它拖住。

2. drain() 只允许在**拥有界面的线程**调用（Qt 主线程）。
   分发是同步的，订阅者可以安全地改控件、发信号，不需要自己加锁。

3. 订阅按类型匹配，用 isinstance，所以可以订阅基类 Event 一次收全部
   （调试时 `bus.subscribe(Event, print)` 就能看到所有事件）。

4. 单个订阅者抛异常**不会**影响其它订阅者，也不会杀死主循环。
   界面代码写错不该让手势系统停摆。
"""

from __future__ import annotations

import logging
import queue
from typing import Callable, List, Tuple, Type

from .events import Event

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, verbose: bool = False) -> None:
        self._q: "queue.SimpleQueue[Event]" = queue.SimpleQueue()
        self._subs: List[Tuple[Type[Event], Callable[[Event], None]]] = []
        self.verbose = verbose
        self.n_posted = 0
        self.n_dropped = 0
        self._max_queue = 512          # 界面卡死时的保险丝

    # ---------------------------------------------------------------- 订阅
    def subscribe(self, etype: Type[Event], fn: Callable[[Event], None]) -> None:
        self._subs.append((etype, fn))

    # ---------------------------------------------------------------- 投递
    def post(self, ev: Event) -> None:
        # 队列过长说明主线程没在消化（界面卡住/已退出）。
        # 直接丢弃比无上限堆积好：手势事件是"此刻"的，攒着没有意义。
        if self._q.qsize() > self._max_queue:
            self.n_dropped += 1
            return
        self.n_posted += 1
        self._q.put(ev)

    # ---------------------------------------------------------------- 分发
    def drain(self, limit: int = 256) -> int:
        """把队列里的事件分发给订阅者。返回本次处理的数量。"""
        n = 0
        while n < limit:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            self._deliver(ev)
            n += 1
        return n

    def _deliver(self, ev: Event) -> None:
        if self.verbose:
            log.debug("%s %s", type(ev).__name__, ev)
        for etype, fn in self._subs:
            if isinstance(ev, etype):
                try:
                    fn(ev)
                except Exception:
                    # 界面层出错不能反噬感知层：记下来，继续下一个订阅者。
                    # exc_info=True 会带上完整栈——这类错误最难复现，
                    # 只留一句 "出错了" 等于没记。
                    log.error("订阅者 %s 处理 %s 时出错",
                              getattr(fn, "__qualname__", fn),
                              type(ev).__name__, exc_info=True)

    # ---------------------------------------------------------------- 诊断
    @property
    def pending(self) -> int:
        return self._q.qsize()
