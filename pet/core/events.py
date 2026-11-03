# -*- coding: utf-8 -*-
"""
事件定义 —— 层与层之间**只通过这几种对象说话**。

为什么要有独立的事件类型（而不是直接回调）：
    感知跑在后台线程、桌宠跑在 Qt 主线程，两边生命周期完全不同。
    只要接口是"事件对象"，将来想把界面换成网页 / Electron / 原生，
    感知层一行都不用改——这是这个骨架最重要的解耦点。

命名约定
    *Seen   每帧的高频状态（会被节流，只给界面做动画用，不承载业务）
    *Fired  一次明确的用户意图（已经过防抖确认，可以执行）
    *Done   执行结果（带上要显示的话，桌宠拿它做反馈）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple


def _now() -> float:
    """统一用单调时钟：改系统时间不会让动画和超时判定错乱。"""
    return time.monotonic()


@dataclass
class Event:
    """所有事件的基类。ts 供界面做淡出、超时判断。"""

    ts: float = field(default_factory=_now)


# ------------------------------------------------------------------ 生命周期
@dataclass
class PerceptionReady(Event):
    """摄像头 + 模型都就绪，可以开始接收手势了。"""

    message: str = ""


@dataclass
class PerceptionFailed(Event):
    """启动失败（没摄像头 / 模型缺失 / 被占用），界面层要给出可读提示。"""

    message: str = ""


# ------------------------------------------------------------------ 感知状态
@dataclass
class HandsSeen(Event):
    """
    每帧的手部状态，**仅用于表现层**（跟随、进度环、表情）。

    刻意做得很"轻"：不塞 Hand 对象、不塞关键点数组。
    一旦把原始数据放进事件，界面层就会开始偷用它们做判定，
    分层就白做了——判定只允许发生在 GestureEngine 里。
    """

    count: int = 0
    point: Optional[Tuple[float, float]] = None   # 掌心归一化坐标（已镜像，0~1）
    gesture: Optional[str] = None                 # 当前单手候选（已多数表决）
    side: str = ""                                # left / right
    progress: float = 0.0                         # 悬停进度 0~1
    moving: bool = False                          # 手在动 -> 计时冻结
    speed: float = 0.0                            # 掌心速度（掌长/秒）


# ------------------------------------------------------------------ 意图与执行
@dataclass
class GestureFired(Event):
    """一次待执行的手势意图。比 HandsSeen 晚，但比 ActionDone 早。"""

    gesture: str = ""
    side: str = ""
    intent: str = ""                              # 见 intent/mapping.py
    label: str = ""


@dataclass
class ActionDone(Event):
    """动作执行结果。成功与否都要报——失败时桌宠要"尴尬一下"，而不是装没事。"""

    intent: str = ""
    label: str = ""
    ok: bool = True
    detail: str = ""
    bubble: str = ""                              # 要说的话（空=不说话）
    mood: str = "happy"


@dataclass
class ContinuousAction(Event):
    """连续量（缩放 / 滚动）。高频，桌宠只用它抖一下，不弹气泡。"""

    intent: str = ""
    label: str = ""
    value: float = 0.0


@dataclass
class ControlToggled(Event):
    """总开关状态变化（双手 ✊✋✊✋，或托盘菜单）。"""

    enabled: bool = True


@dataclass
class PetSay(Event):
    """让桌宠说一句话（demo、托盘、失败提示都走这里）。"""

    text: str = ""
    mood: str = "happy"
    # 说这句话的时候顺带播哪个动作（见 ui/anim.py 的 CLIPS），"" = 不播。
    # 加这个字段是因为"说话"和"做动作"经常是同一件事的两半：
    # 保存成功该点一下头、被关掉该瘫下去 —— 分开传两条事件反而容易错位。
    anim: str = ""
