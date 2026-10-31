# -*- coding: utf-8 -*-
"""python -m pet —— 等价于 python -m pet.app

用 pythonw.exe 启动时 sys.stdout 是 None，任何 print 都会抛 AttributeError。
桌宠最终是要"双击即用、没有黑框"的，所以这里先兜一层：没有标准输出就丢进 devnull。
"""

import os
import sys

if sys.stdout is None or sys.stderr is None:
    _null = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _null
    sys.stderr = sys.stderr or _null

# 输出被重定向到管道/文件时 Python 默认块缓冲，进程被杀会一个字都看不到。
# 调试桌宠时经常要靠日志判断，强制行缓冲。
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from .app import main

if __name__ == "__main__":
    _rc = main()
    # 同 main.py：mediapipe / Qt 的原生收尾偶发卡住，该释放的都释放过了，
    # 这里直接硬退出，避免留下一个吃内存的僵尸进程。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(_rc)
