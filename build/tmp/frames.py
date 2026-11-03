# -*- coding: utf-8 -*-
"""把 7 张立绘并排拼一张，用来确认每张到底长什么样。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication([])
names = ["stand", "happy", "wave", "think", "sleepy", "angry", "sit"]
imgs = []
for n in names:
    pm = QPixmap(f"pet/assets/girl/{n}.png")
    imgs.append(pm.scaledToHeight(230))

pad = 10
W = sum(i.width() for i in imgs) + pad * (len(imgs) + 1)
H = 230 + 40
out = QPixmap(W, H)
out.fill(QColor(246, 248, 250))
p = QPainter(out)
x = pad
for n, im in zip(names, imgs):
    p.drawPixmap(x, 10, im)
    p.setPen(QColor(60, 70, 90))
    p.drawText(x, H - 14, im.width(), 20, 0x84, n)
    x += im.width() + pad
p.end()
out.save("build/tmp/frames.png")
print("saved", W, H)
