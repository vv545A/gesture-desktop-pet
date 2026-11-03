# -*- coding: utf-8 -*-
"""
统一日志配置 —— 一个进程只配一次，其余模块只管 getLogger。

为什么不用 print：
    桌宠是"后台常驻、出问题用户也说不清"的那类程序。print 没有级别、没有
    时间戳、没法重定向到文件。用户反馈"比了个 ✌️ 没反应"时，一份带时间戳的
    日志能把排查从"猜"变成"看"。

为什么不用 basicConfig 就完事：
    pythonw.exe 启动时 sys.stdout 是 None（双击 run_pet.bat 正是这种情况），
    logging 往 None 上挂 StreamHandler 会直接抛异常。所以这里先判断再挂。
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

#: 时间在前、级别对齐、模块名留 18 列——手工 tail 日志时一眼能扫到来源
FMT = "%(asctime)s %(levelname)-5s %(name)-18s %(message)s"
DATEFMT = "%H:%M:%S"


def setup(verbose: bool = False, log_file: str = "") -> None:
    """
    配置根 logger。重复调用无副作用（只生效第一次）。

    verbose=True 开 DEBUG（每帧判定细节），默认 INFO（关键节点）。
    log_file 默认空 = 不落盘：日志里会出现窗口标题之类的东西，
    隐私优先，要留档由用户显式开。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    handlers: list = []
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    if not handlers:
        # 既没有 stdout 也不落盘：挂空 handler，免得 logging 抱怨
        # "no handlers could be found"
        handlers.append(logging.NullHandler())

    formatter = logging.Formatter(FMT, datefmt=DATEFMT)
    for h in handlers:
        h.setFormatter(formatter)
        root.addHandler(h)

    _CONFIGURED = True


def get(name: str) -> logging.Logger:
    """取 logger 的简写，省得每个文件都 import logging 再写一行。"""
    return logging.getLogger(name)
