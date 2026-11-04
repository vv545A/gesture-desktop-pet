# -*- coding: utf-8 -*-
"""
桌宠界面的配色令牌 —— 全部从立绘 `pet/assets/girl/*.png` 里采样得到。

为什么单独立一个文件
    这些颜色以前散落在 window / panel / orbit 三处，各写各的 `QColor(77,107,254)`。
    那套蓝是给**旧形象（蓝发鲸鱼娘）**配的；换成粉发女孩之后，界面还是一片冷蓝，
    看起来像"别人的软件长在她旁边"。
    现在只有这一份令牌，三处统一取色 —— 改一个值，整个界面跟着走。

采样依据（都在 386×580 的 stand.png 上取平均）
    帽子            #FCF1E6    -> CREAM
    腮红/发色       #EEADAC    -> PINK
    开衫            #D5E3D0    -> MINT
    裙子            #C0CEE2    -> BLUE
    线稿            #563430    -> INK（提亮到 #5D403A，纯线稿色直接当正文太闷）

对比度（WCAG，底色按 CREAM #FDF7EE 算）
    INK       8.72:1   正文/图标，过 AAA
    INK_DIM   4.57:1   次级文字，刚过 AA（再淡就不合格了，别再往下调）
    三个强调色 3.47~3.63:1  图形元素（图标/描边）过 1.4.11 的 3:1

一条约定
    强调色分三档：`*_TINT`（背景）、`*`（原色，画块面）、`*_DEEP`（描边/图标）。
    **描边和图标永远用 _DEEP**：浅粉压在米白上只有 1.3:1，等于看不见。
"""

from __future__ import annotations

from PySide6.QtGui import QColor

# ---------------------------------------------------------------- 底色（米白系）
CREAM = QColor(253, 247, 238)        # 卡片/圆按钮底色
CREAM_HI = QColor(255, 252, 246)     # 高光侧（径向渐变左上）
CREAM_LO = QColor(247, 236, 223)     # 暗部侧（右下）
CARD = QColor(253, 247, 238, 246)    # 带一点透明的卡片（浮在壁纸上）

# ---------------------------------------------------------------- 墨色（暖棕，取自线稿）
INK = QColor(93, 64, 58)             # #5D403A 正文 / 图标
INK_DIM = QColor(138, 106, 98)       # #8A6A62 次级文字（4.57:1，别调更淡）
LINE = QColor(226, 203, 192)         # 描边（头发丝那么细的一道暖灰）
WHITE = QColor(255, 255, 255)

# ---------------------------------------------------------------- 强调三色（全部来自她的衣服和头发）
PINK = QColor(238, 173, 172)         # 腮红粉
PINK_DEEP = QColor(196, 100, 122)    # 描边/图标用的深粉
PINK_TINT = QColor(252, 232, 234)    # 浅粉底

MINT = QColor(213, 227, 208)         # 开衫薄荷
MINT_DEEP = QColor(90, 140, 116)
MINT_TINT = QColor(238, 246, 238)

BLUE = QColor(192, 206, 226)         # 牛仔裙蓝
BLUE_DEEP = QColor(106, 135, 174)
BLUE_TINT = QColor(234, 241, 249)

# ---------------------------------------------------------------- 语义色
GOOD = QColor(108, 150, 122)         # 摄像头工作中
WARN = QColor(168, 130, 58)          # 摄像头已关闭
DANGER = QColor(196, 92, 96)         # 退出程序
TRACK_OFF = QColor(226, 215, 206)    # 关掉的胶囊开关轨道
ROW_HOVER = QColor(242, 226, 210)    # 列表行悬停（比选区底色再深一档，不然看不出悬停）

#: 阴影统一用暖棕，不用蓝黑 —— 蓝黑阴影压在暖色卡片下面会发脏。
SHADOW = QColor(93, 64, 58)


def shadow(alpha: int) -> QColor:
    """阴影层：暖棕 + 指定透明度。"""
    return QColor(SHADOW.red(), SHADOW.green(), SHADOW.blue(), alpha)


def tint(color: QColor, t: float) -> QColor:
    """朝白色提亮 t（0~1），用来做渐变的另一头。"""
    t = max(0.0, min(1.0, t))
    return QColor(int(color.red() + (255 - color.red()) * t),
                  int(color.green() + (255 - color.green()) * t),
                  int(color.blue() + (255 - color.blue()) * t))
