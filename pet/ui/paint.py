# -*- coding: utf-8 -*-
"""
桌宠外观 —— 全部用 QPainter 现画，不依赖任何图片资源。

现在的分工（重要）：
    **默认形象是贴图**（`pet/assets/girl/*.png`，见 ui/sprite.py），
    这个文件负责的是「贴图之外的一切」以及「贴图缺失时的兜底」：
        * 影子、柔光、悬停进度环、气泡、情绪特效（星星/爱心/怒气/Zzz）
        * 矢量版整只宠物（`sprite: ""` 时才会画出来）

为什么保留矢量版：
    * 素材丢了 / 只想跑逻辑时，程序仍然是个能看的桌宠，不会变成一个空窗口
    * 加情绪特效（`draw_mood_fx`）时它是最好的试验台：改数值立刻见效
    * 矢量绘制在高 DPI 屏上永远清晰，不依赖图片分辨率

矢量版的形象设定：DeepSeek 鲸鱼娘「小鲸」——Q 版少女，深蓝渐变长发、
鳍状耳、鲸尾、头顶一簇水花。配色取 DeepSeek 的品牌蓝（#4D6BFE）为骨架，
肤色/裙装压到接近白，让蓝发成为视觉重心。

坐标约定（很重要，改之前先看这个）
    draw_pet() 收到的 (cx, cy) 是**整体中心**，w/h 是整体外接框。
    所有内部坐标都写成 w/h 的比例，所以任何尺寸下比例都不会走形：
        水花   y ≈ -0.50h      头顶 y ≈ -0.39h      眼睛 y ≈ -0.19h
        下巴   y ≈ -0.02h      裙摆 y ≈ +0.30h      鲸尾 y ≈ +0.14h…+0.47h
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields
from typing import Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)

# ---------------------------------------------------------------- 字体
_FAMILY = ""


def ui_font(size: int = 10, bold: bool = False) -> QFont:
    """
    取一个**确实带中文字形**的字体。

    为什么不用 QFont("Microsoft YaHei", 10)：字号写了不一定生效——有些环境
    （特别是离屏渲染、精简系统）按家族名查不到字体，中文会变成一排方框。
    直接注册字体文件（和 main.py 的 TextCache 同一套思路）最稳。
    """
    global _FAMILY
    if not _FAMILY:
        for path in (r"C:\Windows\Fonts\msyh.ttc",
                     r"C:\Windows\Fonts\msyhbd.ttc",
                     r"C:\Windows\Fonts\simhei.ttf"):
            if not os.path.exists(path):
                continue
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    _FAMILY = fams[0]
                    break
        if not _FAMILY:
            _FAMILY = "Microsoft YaHei"
    f = QFont(_FAMILY, size)
    f.setBold(bold)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


# ---------------------------------------------------------------- 调色板（DeepSeek 蓝）
OUTLINE = QColor(36, 46, 92)          # 线稿色：深蓝而不是纯黑，和蓝发同族
HAIR_HI = QColor(146, 168, 255)
HAIR_MID = QColor(94, 122, 246)
HAIR_LO = QColor(52, 78, 210)
SKIN = QColor(255, 233, 224)
SKIN_SH = QColor(246, 204, 196)
DRESS_HI = QColor(245, 249, 255)
DRESS_LO = QColor(186, 205, 255)
TAIL_HI = QColor(124, 150, 255)
TAIL_LO = QColor(58, 85, 226)
EYE_HI = QColor(150, 182, 255)
EYE_MID = QColor(62, 92, 220)
EYE_LO = QColor(30, 42, 118)
PUPIL = QColor(22, 30, 74)
ACCENT = QColor(77, 107, 254)         # 进度环 / 品牌蓝
WATER = QColor(150, 208, 255)
BLUSH = QColor(255, 148, 168)
GLOW = QColor(120, 160, 255)
SHADOW = QColor(26, 44, 96, 55)


@dataclass
class PetPose:
    """
    一帧的全部可动参数。界面每帧算出新值再 lerp 过来，所以动作是连续的。

    squash  -1 = 压扁（像被拍了一下），+1 = 拉长（像被拎起来）
    wag     尾巴/头发摆动量 -1~1（由界面按时间给正弦）
    """

    squash: float = 0.0
    tilt: float = 0.0                 # 身体倾斜（度）
    eye_open: float = 1.0             # 1 睁 / 0 闭
    look: Tuple[float, float] = (0.0, 0.0)   # 视线方向 -1~1
    mouth: float = 0.35               # 0 平 / 1 张嘴笑
    blush: float = 0.30
    aura: float = 0.0                 # 悬停进度环 0~1
    glow: float = 0.0                 # 感知到手的柔光
    bob: float = 0.0                  # 上下浮动
    wag: float = 0.0                  # 鲸尾/长发摆动
    happy_eyes: bool = False          # 弯弯的笑眼
    surprised: bool = False           # 圆嘴

    @classmethod
    def for_mood(cls, mood: str) -> "PetPose":
        """情绪 -> 姿态。加新情绪就在这里加一行。"""
        table = {
            "idle":    cls(),
            "listen":  cls(eye_open=1.0, mouth=0.30, glow=1.0, look=(0.0, -0.15)),
            "happy":   cls(mouth=0.85, happy_eyes=True, blush=0.75, squash=0.12),
            "proud":   cls(mouth=0.75, happy_eyes=True, blush=0.5, squash=0.22,
                           bob=0.06),
            "shy":     cls(mouth=0.45, blush=1.0, eye_open=0.55, tilt=-6.0,
                           squash=-0.15, look=(-0.5, 0.35)),
            "surprised": cls(eye_open=1.0, mouth=0.9, surprised=True, squash=0.28,
                             bob=0.08),
            "sleepy":  cls(eye_open=0.12, mouth=0.15, glow=0.0, bob=-0.02),
            "calm":    cls(mouth=0.30, blush=0.22),
            # 跳舞的基调：开心 + 张嘴 + 大幅度歪头。真正的动感由 window._tick
            # 叠相位（tilt/bob/squash 的正弦），这里只决定"定格在哪一帧"。
            "dance":   cls(mouth=0.80, happy_eyes=True, blush=0.85,
                           squash=0.14, bob=0.10, tilt=6.0),
        }
        return table.get(mood, cls())

    def lerp_to(self, target: "PetPose", k: float) -> None:
        """朝目标姿态插值。k 是每帧比例（0.2 左右比较跟手，又不抖）。"""
        k = max(0.0, min(1.0, k))
        for f in fields(self):
            a, b = getattr(self, f.name), getattr(target, f.name)
            if isinstance(a, float):
                setattr(self, f.name, a + (b - a) * k)
            elif isinstance(a, bool):
                # 布尔量不做插值，过了半分位就翻牌
                setattr(self, f.name, b if k > 0.5 else a)
            elif isinstance(a, tuple):
                setattr(self, f.name, tuple(x + (y - x) * k for x, y in zip(a, b)))


# ---------------------------------------------------------------- 绘制工具
def _grad(p: QPainter, x1: float, y1: float, x2: float, y2: float,
          top: QColor, bottom: QColor) -> QLinearGradient:
    g = QLinearGradient(x1, y1, x2, y2)
    g.setColorAt(0.0, top)
    g.setColorAt(1.0, bottom)
    return g


def _tint(c: QColor, t: float) -> QColor:
    """朝白色提亮 t（0~1）。"""
    t = max(0.0, min(1.0, t))
    return QColor(int(c.red() + (255 - c.red()) * t),
                  int(c.green() + (255 - c.green()) * t),
                  int(c.blue() + (255 - c.blue()) * t))


def hair_fill(p: QPainter, hy: float, h: float,
              highlight: float = 0.0) -> QLinearGradient:
    """
    整头共用的一条竖直渐变。

    后发、两侧长发、刘海都取这一条，颜色在接缝处天然对齐——
    否则刘海和后发各自一条渐变，交界线会显形成"泳帽边"。
    """
    g = QLinearGradient(0, hy - h * 0.235, 0, hy + h * 0.235)
    g.setColorAt(0.0, _tint(HAIR_MID, 0.10 + highlight))
    g.setColorAt(0.55, _tint(HAIR_MID, highlight * 0.6))
    g.setColorAt(1.0, _tint(HAIR_LO, highlight * 0.4))
    return g


def _shape(p: QPainter, path: QPainterPath, fill, line: QColor | None,
           lw: float) -> None:
    """填 + 描边。fill 可以是颜色，也可以是 QGradient。"""
    p.setBrush(QBrush(fill) if not hasattr(fill, "setColorAt") else fill)
    if line is None:
        p.setPen(Qt.PenStyle.NoPen)
    else:
        p.setPen(QPen(line, lw, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)


# ---------------------------------------------------------------- 各部件
def draw_shadow(p: QPainter, cx: float, cy: float, w: float, h: float) -> None:
    """脚下阴影。贴图版和矢量版都要用，所以做成公开函数、自己算坐标。"""
    p.save()
    p.translate(cx, cy + h * 0.485)
    g = QRadialGradient(QPointF(0, 0), w * 0.40)
    g.setColorAt(0.0, SHADOW)
    g.setColorAt(1.0, QColor(SHADOW.red(), SHADOW.green(), SHADOW.blue(), 0))
    p.setBrush(QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(0, 0), w * 0.40, h * 0.045)
    p.restore()


def draw_glow(p: QPainter, cx: float, cy: float, w: float, h: float,
              strength: float) -> None:
    """感知到手的柔光，画在角色**背后**。"""
    if strength <= 0.02:
        return
    p.save()
    p.translate(cx, cy)
    r = max(w, h) * 0.72
    g = QRadialGradient(QPointF(0, 0), r)
    g.setColorAt(0.30, QColor(GLOW.red(), GLOW.green(), GLOW.blue(),
                             int(70 * strength)))
    g.setColorAt(1.0, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), 0))
    p.setBrush(QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(0, 0), r, r)
    p.restore()


def _tail(p: QPainter, w: float, h: float, lw: float, wag: float) -> None:
    """
    鲸尾：一截尾柄 + 一对**横向**展开的尾鳍，中间一个 V 形凹口。

    两个坑（都踩过）：
        1. 没有凹口 -> 只是一块蓝色豆子；
        2. 鳍叶太薄、尖端朝下 -> 立刻变成"两条腿"。
    所以鳍叶要做厚、上缘接近水平、尖端朝两侧而不是朝下。
    """
    p.save()
    p.translate(0, h * 0.28)
    p.rotate(wag * 7.0)
    p.translate(0, -h * 0.28)

    # 尾柄（从裙摆下面伸出来）
    stalk = QPainterPath()
    stalk.moveTo(-w * 0.075, h * 0.225)
    stalk.cubicTo(-w * 0.068, h * 0.270, -w * 0.058, h * 0.300,
                  -w * 0.050, h * 0.325)
    stalk.lineTo(w * 0.050, h * 0.325)
    stalk.cubicTo(w * 0.058, h * 0.300, w * 0.068, h * 0.270,
                  w * 0.075, h * 0.225)
    stalk.closeSubpath()
    _shape(p, stalk, _grad(p, 0, h * 0.22, 0, h * 0.34, TAIL_HI, TAIL_LO),
           OUTLINE, lw)

    fluke = QPainterPath()
    fluke.moveTo(0.0, h * 0.315)
    # 左上缘：几乎水平地向外扫出去
    fluke.cubicTo(-w * 0.100, h * 0.330, -w * 0.240, h * 0.340,
                  -w * 0.355, h * 0.372)
    # 左尖端：圆头，只微微下垂
    fluke.cubicTo(-w * 0.405, h * 0.390, -w * 0.395, h * 0.437,
                  -w * 0.330, h * 0.425)
    # 左下缘收回中间的凹口
    fluke.cubicTo(-w * 0.200, h * 0.410, -w * 0.080, h * 0.400, 0.0, h * 0.382)
    fluke.cubicTo(w * 0.080, h * 0.400, w * 0.200, h * 0.410, w * 0.330, h * 0.425)
    fluke.cubicTo(w * 0.395, h * 0.437, w * 0.405, h * 0.390, w * 0.355, h * 0.372)
    fluke.cubicTo(w * 0.240, h * 0.340, w * 0.100, h * 0.330, 0.0, h * 0.315)
    _shape(p, fluke, _grad(p, 0, h * 0.31, 0, h * 0.44, TAIL_HI, TAIL_LO),
           OUTLINE, lw)

    # 鳍上的两道分叉，让它读起来是"鳍"而不是"心"
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 120), lw * 0.9,
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for s in (-1, 1):
        line = QPainterPath()
        line.moveTo(s * w * 0.060, h * 0.345)
        line.quadTo(s * w * 0.200, h * 0.380, s * w * 0.315, h * 0.402)
        p.drawPath(line)
    p.restore()


def _back_hair(p: QPainter, w: float, h: float, hy: float, lw: float) -> None:
    """后层头发：一个大发包 + 两侧垂到腰的长发。"""
    hrx, hry = w * 0.355, h * 0.235

    crown = QPainterPath()
    crown.addEllipse(QPointF(0.0, hy - h * 0.008), hrx, hry)
    _shape(p, crown, hair_fill(p, hy, h), OUTLINE, lw)

    for s in (-1, 1):
        lock = QPainterPath()
        x0 = s * hrx * 0.72
        lock.moveTo(x0, hy - hry * 0.20)
        lock.cubicTo(s * hrx * 1.28, hy + hry * 0.42,
                     s * hrx * 1.05, hy + hry * 1.15,
                     s * hrx * 0.74, hy + hry * 1.62)
        lock.cubicTo(s * hrx * 0.52, hy + hry * 1.10,
                     s * hrx * 0.58, hy + hry * 0.25,
                     s * hrx * 0.58, hy - hry * 0.30)
        lock.closeSubpath()
        _shape(p, lock, hair_fill(p, hy, h), OUTLINE, lw)


def _body(p: QPainter, w: float, h: float, lw: float) -> None:
    """脖子 + 小手臂 + 连衣裙。鲸鱼娘不画腿（尾巴接管了下半身）。"""
    # 脖子
    neck = QPainterPath()
    neck.addRect(QRectF(-w * 0.050, -h * 0.060, w * 0.100, h * 0.080))
    _shape(p, neck, SKIN_SH, None, 0.0)

    # 连衣裙：领口窄、裙摆宽，下摆三瓣波浪
    dress = QPainterPath()
    dress.moveTo(-w * 0.150, -h * 0.015)
    dress.cubicTo(-w * 0.272, h * 0.095, -w * 0.305, h * 0.205,
                  -w * 0.280, h * 0.285)
    dress.quadTo(-w * 0.205, h * 0.320, -w * 0.115, h * 0.292)
    dress.quadTo(-w * 0.030, h * 0.328, 0.0, h * 0.290)
    dress.quadTo(w * 0.030, h * 0.328, w * 0.115, h * 0.292)
    dress.quadTo(w * 0.205, h * 0.320, w * 0.280, h * 0.285)
    dress.cubicTo(w * 0.305, h * 0.205, w * 0.272, h * 0.095,
                  w * 0.150, -h * 0.015)
    dress.closeSubpath()
    _shape(p, dress, _grad(p, 0, -h * 0.02, 0, h * 0.31, DRESS_HI, DRESS_LO),
           OUTLINE, lw)

    # 手臂：画在裙子**之上**，垂在身侧。
    # 第一版画在裙子后面，结果只露出两个小圆点，看起来像"没画手"。
    # 用"粗线 + 圆头"画四肢比抠路径简单得多，两头自然是圆的。
    for sd in (-1, 1):
        # 手必须**露出裙子轮廓**，否则会被读成裙子上的两条装饰带
        a = QPointF(sd * w * 0.152, h * 0.030)
        b = QPointF(sd * w * 0.330, h * 0.178)
        p.setPen(QPen(OUTLINE, w * 0.092, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)
        p.setPen(QPen(SKIN, w * 0.068, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)

    # 领结：一点品牌蓝，把视线拦在胸口
    bow = QPainterPath()
    bow.moveTo(0.0, -h * 0.010)
    bow.lineTo(-w * 0.070, -h * 0.032)
    bow.lineTo(-w * 0.070, h * 0.008)
    bow.closeSubpath()
    bow.moveTo(0.0, -h * 0.010)
    bow.lineTo(w * 0.070, -h * 0.032)
    bow.lineTo(w * 0.070, h * 0.008)
    bow.closeSubpath()
    _shape(p, bow, ACCENT, OUTLINE, lw * 0.7)
    p.setBrush(QBrush(QColor(255, 255, 255, 220)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(0.0, -h * 0.010), w * 0.020, h * 0.013)


def _eye(p: QPainter, cx: float, cy: float, rx: float, ry: float,
         pose: PetPose, lw: float) -> None:
    """一只二次元眼睛：眼白 + 渐变虹膜 + 瞳孔 + 两点高光 + 上眼睑线。"""
    lx, ly = pose.look

    if pose.happy_eyes:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(OUTLINE, lw * 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        arc = QPainterPath()
        arc.moveTo(cx - rx * 0.95, cy + ry * 0.30)
        arc.quadTo(cx, cy - ry * 0.95, cx + rx * 0.95, cy + ry * 0.30)
        p.drawPath(arc)
        return

    ry_eff = ry * max(0.06, min(1.0, pose.eye_open))
    if ry_eff < ry * 0.20:
        # 闭眼：一条微微下垂的弧，比直线自然
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(OUTLINE, lw * 1.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        arc = QPainterPath()
        arc.moveTo(cx - rx * 0.85, cy)
        arc.quadTo(cx, cy + ry * 0.55, cx + rx * 0.85, cy)
        p.drawPath(arc)
        return

    eye = QPainterPath()
    eye.addEllipse(QPointF(cx, cy), rx, ry_eff)

    # 眼白
    p.setBrush(QBrush(QColor(253, 253, 255)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(eye)

    # 虹膜（裁在眼白里）
    p.save()
    p.setClipPath(eye)
    ix = cx + lx * rx * 0.26
    iy = cy + ly * ry_eff * 0.24 + ry_eff * 0.06
    iris = QRadialGradient(QPointF(ix - rx * 0.18, iy - ry_eff * 0.35), rx * 1.15)
    iris.setColorAt(0.0, EYE_HI)
    iris.setColorAt(0.55, EYE_MID)
    iris.setColorAt(1.0, EYE_LO)
    p.setBrush(QBrush(iris))
    p.drawEllipse(QPointF(ix, iy), rx * 0.80, ry_eff * 0.94)

    p.setBrush(QBrush(PUPIL))
    p.drawEllipse(QPointF(ix, iy + ry_eff * 0.05), rx * 0.40, ry_eff * 0.52)

    # 高光：左上大、右下小，眼睛立刻"活"过来
    p.setBrush(QBrush(QColor(255, 255, 255, 245)))
    p.drawEllipse(QPointF(cx - rx * 0.30, cy - ry_eff * 0.42),
                  rx * 0.30, rx * 0.26)
    p.setBrush(QBrush(QColor(255, 255, 255, 170)))
    p.drawEllipse(QPointF(cx + rx * 0.24, cy + ry_eff * 0.34),
                  rx * 0.15, rx * 0.13)
    p.restore()

    # 上眼睑：比下缘粗，眼睛才有"重量"
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(OUTLINE, lw * 1.7, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    lid = QPainterPath()
    lid.moveTo(cx - rx * 1.02, cy - ry_eff * 0.18)
    lid.quadTo(cx, cy - ry_eff * 1.42, cx + rx * 1.02, cy - ry_eff * 0.24)
    p.drawPath(lid)

    # 外眼角的一根睫毛
    p.setPen(QPen(OUTLINE, lw * 1.3, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(cx + rx * 0.94, cy - ry_eff * 0.26),
               QPointF(cx + rx * 1.16, cy - ry_eff * 0.44))


def _face(p: QPainter, w: float, h: float, hy: float, pose: PetPose,
          lw: float) -> None:
    rx, ry = w * 0.285, h * 0.180

    skin = QPainterPath()
    skin.addEllipse(QPointF(0.0, hy), rx, ry)
    _shape(p, skin, SKIN, OUTLINE, lw)

    # 腮红（先画，被刘海压住一点更自然）
    if pose.blush > 0.02:
        p.setBrush(QBrush(QColor(BLUSH.red(), BLUSH.green(), BLUSH.blue(),
                                 int(150 * min(1.0, pose.blush)))))
        p.setPen(Qt.PenStyle.NoPen)
        for s in (-1, 1):
            p.drawEllipse(QPointF(s * w * 0.185, hy + h * 0.062),
                          w * 0.055, h * 0.026)

    # 眼睛
    ex = w * 0.128
    ey = hy + h * 0.008
    for s in (-1, 1):
        _eye(p, s * ex, ey, w * 0.078, h * 0.062, pose, lw)

    # 嘴（惊讶时是小圆口）
    if pose.surprised:
        p.setBrush(QBrush(QColor(196, 96, 110)))
        p.setPen(QPen(OUTLINE, lw * 0.8))
        p.drawEllipse(QPointF(0.0, hy + h * 0.098), w * 0.028, h * 0.020)
    else:
        mw = w * (0.030 + 0.028 * max(0.0, min(1.0, pose.mouth)))
        mh = h * (0.008 + 0.026 * max(0.0, min(1.0, pose.mouth)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(OUTLINE, lw * 0.95, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        mouth = QPainterPath()
        mouth.moveTo(-mw, hy + h * 0.098)
        mouth.quadTo(0.0, hy + h * 0.098 + mh, mw, hy + h * 0.098)
        p.drawPath(mouth)


def _bangs(p: QPainter, w: float, h: float, hy: float, pose: PetPose,
           lw: float) -> None:
    """
    刘海：盖住额头，下缘做成尖角，露出一对眼睛。

    注意这里**只给下缘描边**：如果整条外轮廓都描，头顶那圈线会和后层头发
    的轮廓叠成一道缝，看起来像戴了个头盔（第一版就是这样）。
    """
    rxx, ryy = w * 0.300, h * 0.188
    bangs = QPainterPath()
    # 顶弧必须**留在后发轮廓里面**：一旦画到外面，它的填充会盖住后发的描边，
    # 头顶就没线了，看起来像戴了个泳帽（第一版就是这个毛病）。
    bangs.moveTo(-rxx * 0.96, hy - h * 0.020)
    bangs.cubicTo(-rxx * 1.02, hy - ryy * 1.34,
                  rxx * 1.02, hy - ryy * 1.34,
                  rxx * 0.96, hy - h * 0.026)
    bangs.lineTo(rxx * 0.74, hy - h * 0.128)
    bangs.lineTo(rxx * 0.42, hy - h * 0.066)
    bangs.lineTo(rxx * 0.10, hy - h * 0.126)
    bangs.lineTo(-rxx * 0.24, hy - h * 0.068)
    bangs.lineTo(-rxx * 0.56, hy - h * 0.124)
    bangs.lineTo(-rxx * 0.96, hy - h * 0.062)
    bangs.closeSubpath()
    # 和后面同一条渐变：两块的接缝才不会显形
    _shape(p, bangs, hair_fill(p, hy, h), None, 0.0)

    # 只描下缘（锯齿那一段）
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(OUTLINE, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    fringe = QPainterPath()
    fringe.moveTo(rxx * 0.96, hy - h * 0.026)
    fringe.lineTo(rxx * 0.74, hy - h * 0.128)
    fringe.lineTo(rxx * 0.42, hy - h * 0.066)
    fringe.lineTo(rxx * 0.10, hy - h * 0.126)
    fringe.lineTo(-rxx * 0.24, hy - h * 0.068)
    fringe.lineTo(-rxx * 0.56, hy - h * 0.124)
    fringe.lineTo(-rxx * 0.96, hy - h * 0.062)
    fringe.lineTo(-rxx * 0.96, hy - h * 0.020)
    p.drawPath(fringe)

    # 头发高光：一道弧形亮带
    p.setPen(QPen(QColor(255, 255, 255, 155), lw * 1.7,
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    shine = QPainterPath()
    shine.moveTo(-rxx * 0.70, hy - ryy * 0.95)
    shine.quadTo(0.0, hy - ryy * 1.34, rxx * 0.64, hy - ryy * 0.88)
    p.drawPath(shine)


def _fins(p: QPainter, w: float, h: float, hy: float, lw: float,
          wag: float) -> None:
    """鳍状耳：鲸鱼娘的辨识点，没有它就只是个普通 Q 版女孩。"""
    for s in (-1, 1):
        fin = QPainterPath()
        fin.moveTo(s * w * 0.232, hy + 0.018 * h)
        fin.cubicTo(s * w * 0.312, hy - 0.002 * h + wag * h * 0.004,
                    s * w * 0.400, hy + 0.034 * h,
                    s * w * 0.408, hy + 0.086 * h)
        fin.cubicTo(s * w * 0.356, hy + 0.104 * h,
                    s * w * 0.284, hy + 0.086 * h,
                    s * w * 0.238, hy + 0.058 * h)
        fin.closeSubpath()
        _shape(p, fin, _grad(p, s * w * 0.22, hy, s * w * 0.40, hy + h * 0.11,
                             TAIL_HI, TAIL_LO), OUTLINE, lw)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 110), lw * 0.8,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for k in (0.0, 0.45):
            rib = QPainterPath()
            rib.moveTo(s * w * (0.262 + 0.012 * k), hy + h * (0.014 + 0.006 * k))
            rib.quadTo(s * w * 0.318, hy + h * (0.030 + 0.020 * k),
                       s * w * (0.362 + 0.006 * k), hy + h * (0.062 + 0.024 * k))
            p.drawPath(rib)


def _spout(p: QPainter, w: float, h: float, pose: PetPose, lw: float) -> None:
    """头顶那簇水花：鲸鱼喷水，也是这只宠物的"信号灯"。"""
    top = -h * 0.500 + pose.bob * h * 0.8
    drop = QPainterPath()
    drop.moveTo(0.0, top - h * 0.052)
    drop.cubicTo(w * 0.048, top - h * 0.010, w * 0.036, top + h * 0.026,
                 0.0, top + h * 0.030)
    drop.cubicTo(-w * 0.036, top + h * 0.026, -w * 0.048, top - h * 0.010,
                 0.0, top - h * 0.052)
    _shape(p, drop, WATER, OUTLINE, lw * 0.8)

    # 两颗小水珠，一大一小，飘在两侧
    for s, r in ((-1, 0.020), (1, 0.014)):
        p.setBrush(QBrush(QColor(WATER.red(), WATER.green(), WATER.blue(), 210)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(s * w * 0.085, top + h * 0.012 - abs(s) * 0),
                      w * r, w * r)


# ---------------------------------------------------------------- 主绘制
def draw_pet(p: QPainter, cx: float, cy: float, w: float, h: float,
             pose: PetPose, motion=None) -> None:
    """
    在 (cx, cy) 处画一只宽 w 高 h 的鲸鱼娘。

    调用方负责句柄状态之外的一切（背景、气泡）。这里只画角色本体，
    所以它在窗口、托盘图标、离屏快照三种场景里都能直接复用。

    motion 是 ui/anim.py 的动作变换（可选）。矢量版同样要跟着跳、跟着摇头，
    所以和贴图版一样把它叠进来 —— 两套形象的动作幅度才一致。
    """
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)

    # 体积守恒：压扁就变宽，拉长就变窄，看起来才像有弹性的生物
    squash = max(-1.0, min(1.0, pose.squash))
    w *= 1.0 + 0.34 * max(0.0, -squash) + 0.08 * max(0.0, squash)
    h *= 1.0 + 0.26 * max(0.0, squash) - 0.22 * max(0.0, -squash)
    cy += pose.bob * h * 0.5
    lw = max(1.0, w * 0.013)          # 线稿粗细跟着尺寸走，缩小时才不会糊成一团

    dx = dy = rot = 0.0
    if motion is not None:
        dx, dy, rot = motion.dx * w, motion.dy * h, motion.rot
        w *= motion.sx
        h *= motion.sy

    p.translate(cx + dx, cy + dy)
    draw_shadow(p, 0, 0, w, h)
    draw_glow(p, 0, 0, w, h, pose.glow)

    p.rotate(pose.tilt + rot)
    hy = -h * 0.195                  # 头部中心

    _tail(p, w, h, lw, pose.wag)
    _back_hair(p, w, h, hy, lw)
    _body(p, w, h, lw)
    _face(p, w, h, hy, pose, lw)
    _fins(p, w, h, hy, lw, pose.wag)     # 鳍状耳在头发之上，才不会像被头发吞掉
    _bangs(p, w, h, hy, pose, lw)
    _spout(p, w, h, pose, lw)

    p.restore()

    # 进度环跟着身体走（跳起来时环也该一起跳，不然像是钉在桌面上）
    draw_aura(p, cx + dx, cy + dy, w, h, pose.aura)


def draw_aura(p: QPainter, cx: float, cy: float, w: float, h: float,
              aura: float) -> None:
    """
    悬停进度环。画在最外层、不随身体旋转，贴图版和矢量版共用。

    Qt 的角度单位是 1/16 度，正向逆时针；从 12 点开始顺时针扫，
    符合"计时条"的直觉。
    """
    if aura <= 0.01:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rx, ry = w * 0.52 + 2, h * 0.54 + 2
    rect = QRectF(cx - rx, cy - ry, rx * 2, ry * 2)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(58, 88, 120, 55), max(2.0, ry * 0.028)))
    p.drawArc(rect, 0, 360 * 16)
    p.setPen(QPen(ACCENT, max(2.4, ry * 0.034), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, int(90 * 16), int(-360 * 16 * min(1.0, aura)))
    p.restore()


# ---------------------------------------------------------------- 气泡
def draw_bubble(p: QPainter, rect: QRectF, text: str, alpha: float,
                font: QFont) -> None:
    """在 rect 里画一个圆角气泡 + 底部小尾巴，alpha 控制淡入淡出。"""
    if alpha <= 0.01 or not text:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    a = int(235 * alpha)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, a)))
    path = QPainterPath()
    radius = min(rect.height() / 2, 14.0)
    path.addRoundedRect(rect, radius, radius)
    # 尾巴指向宠物脑袋
    tail = QPainterPath()
    bx = rect.center().x() - 6
    by = rect.bottom()
    tail.moveTo(bx, by - 1)
    tail.lineTo(bx + 12, by - 1)
    tail.lineTo(bx + 4, by + 9)
    tail.closeSubpath()
    p.drawPath(path.united(tail))

    p.setPen(QPen(QColor(30, 70, 80, a), 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path.united(tail))

    p.setPen(QPen(QColor(26, 52, 60, min(255, int(255 * alpha)))))
    p.setFont(font)
    p.drawText(rect.adjusted(10, 6, -10, -6),
               int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    p.restore()


# ---------------------------------------------------------------- 情绪特效
def _sparkle(p: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
    """四角星：两段交叉的细菱形，比画多边形省事。"""
    path = QPainterPath()
    path.moveTo(cx, cy - r)
    path.quadTo(cx + r * 0.18, cy - r * 0.18, cx + r, cy)
    path.quadTo(cx + r * 0.18, cy + r * 0.18, cx, cy + r)
    path.quadTo(cx - r * 0.18, cy + r * 0.18, cx - r, cy)
    path.quadTo(cx - r * 0.18, cy - r * 0.18, cx, cy - r)
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)


def _zed(p: QPainter, x: float, y: float, size: float, alpha: int) -> None:
    """字母 Z：三段折线，不用字体（字体每帧排版更贵，而且不同机器字形不一样）。"""
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(96, 118, 170, alpha), max(1.4, size * 0.20),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + size, y)
    path.lineTo(x, y + size)
    path.lineTo(x + size, y + size)
    p.drawPath(path)


def _heart(p: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
    """爱心：两个圆 + 一个尖。比贝塞尔好调，也不会出现尖角接不上的缝。"""
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(cx - r * 0.45, cy - r * 0.22), r * 0.52, r * 0.52)
    p.drawEllipse(QPointF(cx + r * 0.45, cy - r * 0.22), r * 0.52, r * 0.52)
    tri = QPainterPath()
    tri.moveTo(cx - r * 0.96, cy - r * 0.06)
    tri.lineTo(cx + r * 0.96, cy - r * 0.06)
    tri.lineTo(cx, cy + r * 0.98)
    tri.closeSubpath()
    p.drawPath(tri)


def _vein(p: QPainter, cx: float, cy: float, r: float, alpha: int) -> None:
    """
    漫画里的"爆血管"记号：四条短弧围成一个十字。

    为什么不用 ``#``：那个字符在不同字体里粗细差得很远，而且得为它再引一次
    字体加载。四段圆弧在哪台机器上都长一样。
    """
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(226, 92, 96, alpha), max(1.6, r * 0.34),
                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        path = QPainterPath()
        path.moveTo(cx + sx * r * 0.28, cy + sy * r * 0.92)
        path.quadTo(cx + sx * r * 0.92, cy + sy * r * 0.92,
                    cx + sx * r * 0.92, cy + sy * r * 0.28)
        p.drawPath(path)


def _dots(p: QPainter, x: float, y: float, size: float, alpha: int,
          phase: float) -> None:
    """省略号：三个点依次亮起来，像"正在想"。"""
    for i in range(3):
        f = 0.35 + 0.65 * max(0.0, math.sin(phase * 2.6 - i * 0.7))
        p.setBrush(QBrush(QColor(140, 158, 196, int(alpha * f))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x + i * size * 1.35, y), size * 0.30, size * 0.30)


def draw_mood_fx(p: QPainter, cx: float, cy: float, w: float, h: float,
                 mood: str, phase: float) -> None:
    """
    情绪特效。立绘帧已经能换表情了，但"星星 / 爱心 / 怒气 / Zzz"这类
    **浮在身体外面的符号**画不进立绘（它们得能动），所以留在这里补。

    现有：happy/proud 星星 · shy 粉色光点 · surprised 感叹号 ·
         sleepy Zzz · love 爱心 · angry 爆血管 · think 省略号

    尺度约定（换立绘时最容易在这里翻车）
        横向一律用 ox 当"身体外沿"，纵向用 h 当参考。
        **不能拿 w 当所有尺寸的基准**：这套立绘比上一版细（宽高比 0.67 vs 0.75），
        早先按 w 算的符号只有 4~6px，画出来几乎看不见。
        符号大小统一挂在 h 上 —— 宠物放大缩小时，符号跟着一起变。

        另一条实测经验：符号一开始摆得太靠外（ox = w*0.86），飘在离身体 40px
        的空白里，不像"从她头上冒出来的"。0.64 刚好贴着头两侧，也不会出窗口。
    """
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    ox = max(w * 0.64, h * 0.34)      # 横向"出界"参考：贴着头的两侧
    if mood in ("happy", "proud"):
        # 四颗星星分布在她脑袋两侧：太靠内会被头发吃掉，太靠外又会飘出窗口
        pts = ((-0.95, -0.28), (0.95, -0.22), (0.80, -0.50), (-0.76, -0.48))
        for i, (dx, dy) in enumerate(pts):
            tw = 0.45 + 0.55 * math.sin(phase * 3.2 + i * 1.9)
            if tw <= 0.08:
                continue
            r = h * 0.052 * tw
            _sparkle(p, cx + dx * ox, cy + dy * h, r * 1.9,
                     QColor(255, 214, 120, int(60 * tw)))     # 外圈柔光
            _sparkle(p, cx + dx * ox, cy + dy * h, r,
                     QColor(255, 236, 168, int(235 * tw)))    # 内芯

    elif mood == "shy":
        for i, sgn in enumerate((-1, 1)):
            f = 0.6 + 0.4 * math.sin(phase * 2.2 + i * 1.7)
            _sparkle(p, cx + sgn * ox * 0.92, cy - h * (0.30 + 0.05 * i),
                     h * 0.046 * f, QColor(255, 150, 178, int(215 * f)))

    elif mood == "surprised":
        # 感叹号：以前只有 5×10px，缩到桌宠尺寸就成了一条橙线，看不出是"！"
        x, y = cx + ox * 0.90, cy - h * 0.44
        p.setBrush(QBrush(QColor(255, 170, 70, 240)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(x - h * 0.026, y - h * 0.070, h * 0.052,
                                 h * 0.090), h * 0.022, h * 0.022)
        p.drawEllipse(QPointF(x, y + h * 0.044), h * 0.024, h * 0.024)

    elif mood == "sleepy":
        for i in range(3):
            f = (phase * 0.45 + i * 0.33) % 1.0
            alpha = int(185 * (1.0 - f))
            if alpha <= 6:
                continue
            size = h * 0.058 * (0.7 + 0.8 * f)
            _zed(p, cx + ox * (0.66 + 0.30 * f), cy - h * (0.30 + 0.18 * f),
                 size, alpha)

    elif mood == "love":
        # 被摸：三颗心交替往上飘，越飘越淡
        for i in range(3):
            f = (phase * 0.7 + i * 0.34) % 1.0
            alpha = int(235 * (1.0 - f))
            if alpha <= 8:
                continue
            r = h * 0.046 * (0.75 + 0.45 * f)
            sway = math.sin((phase * 2.0 + i) * 1.7) * h * 0.030
            _heart(p, cx + ox * (0.76 + 0.12 * i) + sway,
                   cy - h * (0.12 + 0.30 * f), r,
                   QColor(255, 138, 166, alpha))

    elif mood == "angry":
        # 怒气记号：头右上一枚，配两缕往上飘的红点
        _vein(p, cx + ox * 0.86, cy - h * 0.30, h * 0.060,
              int(220 * (0.6 + 0.4 * math.sin(phase * 6.0))))
        for i in range(2):
            f = (phase * 0.9 + i * 0.5) % 1.0
            alpha = int(150 * (1.0 - f))
            if alpha <= 8:
                continue
            r = h * 0.020 * (0.8 + 0.6 * f)
            p.setBrush(QBrush(QColor(232, 140, 140, alpha)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx - ox * (0.80 + 0.10 * i),
                                  cy - h * (0.26 + 0.24 * f)), r, r)

    elif mood == "think":
        _dots(p, cx + ox * 0.72, cy - h * 0.36, h * 0.038, 215, phase)

    p.restore()


# ---------------------------------------------------------------- 离屏渲染
def render_pet(width: int, height: int, pose: PetPose | None = None,
               background: QColor | None = None,
               scale: float = 1.0) -> QPixmap:
    """
    把一只鲸鱼娘渲染成 QPixmap。托盘图标、`--snapshot` 自检、文档配图都走这里。

    background=None 表示透明；给了颜色就填底色（眼睛看不见深色背景上的
    透明效果，自检时用得上）。
    """
    pm = QPixmap(int(width), int(height))
    pm.fill(background or QColor(0, 0, 0, 0))
    p = QPainter(pm)
    if pose is None:
        pose = PetPose()
    # 角色是竖长条（水花 + 头 + 身 + 尾），按高度定尺寸、宽度跟着走，横竖屏都不会变形
    body_h = height * 0.74 * scale
    body_w = body_h * 0.78
    draw_pet(p, width / 2, height / 2, body_w, body_h, pose)
    p.end()
    return pm


def tray_icon(size: int = 64) -> QPixmap:
    """
    托盘图标：只取**头部**。

    整只画进 16px 的托盘格子里只会糊成一个蓝点——托盘图标在 100% 缩放下
    实际就只有 16~20px，能读出来的只有"一张脸"。所以先画大、再裁头。
    """
    big = QPixmap(256, 256)
    big.fill(QColor(0, 0, 0, 0))
    p = QPainter(big)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body_h = 256 * 0.92
    body_w = body_h * 0.78
    draw_pet(p, 128, 168, body_w, body_h, PetPose())
    p.end()

    # 头中心 y ≈ 168 - 0.195·body_h；冠部半宽 0.355·body_w、半高 0.235·body_h
    hy = 168 - 0.195 * body_h
    w2 = 0.355 * body_w * 1.24
    h2 = 0.235 * body_h * 1.24
    crop = big.copy(int(128 - w2), int(hy - h2 * 1.30),
                    int(w2 * 2), int(h2 * 2.30))
    return crop.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
