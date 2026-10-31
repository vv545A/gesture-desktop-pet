# -*- coding: utf-8 -*-
"""
pet —— 隔空手势桌宠

分层（详见 ../ARCHITECTURE.md）
    core/        事件总线、配置、手势引擎（纯逻辑，不依赖界面）
    perception/  摄像头 + MediaPipe，跑在后台线程
    intent/      手势 -> 意图 -> 执行，并回报结果
    ui/          透明桌宠窗口、程序化绘制、托盘

其他模块只应该依赖 core.events 里的事件类型，不要直接 import ui。
"""

__version__ = "0.1.0"
