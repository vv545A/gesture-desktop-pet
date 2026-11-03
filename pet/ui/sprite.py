# -*- coding: utf-8 -*-
"""
贴图形象 —— 直接画现成的透明立绘，而不是程序化绘制。

素材来源（务必保留署名）
    1190fasheqi/dafeiyu-pet · MIT License
    https://github.com/1190fasheqi/dafeiyu-pet
    「大肥鱼」是 DeepSeek 鲸鱼形象的二创桌宠，非官方。本项目只取其中的
    正面 / 侧面立绘作为形象，未使用其代码。

为什么贴图和矢量两套都留着：
    * 贴图好看，但表情/口型是死的，只有整体位移、压扁、倾斜这类"卡通运动"
    * 矢量版能改表情（眨眼、笑眼、圆嘴），调试和二次创作都方便
    两者对外是同一个 draw 接口，`pet_config.json` 里把 `sprite` 留空就切回矢量版。

性能
    立绘原图 255×340，直接每帧缩放会浪费；这里按目标高度**预缩放并缓存**，
    缓存键带上 devicePixelRatio，高 DPI 屏上不会糊。
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap

from ..core.config import Config, sprite_path
from .paint import PetPose


class SpriteSet:
    """一组立绘（正面 + 侧面）。侧面用来做"走向左/右"的朝向切换。"""

    def __init__(self, front: str, side: str = "") -> None:
        self.front_path = front
        self.side_path = side if side and os.path.exists(side) else ""
        self._front = QPixmap(front) if os.path.exists(front) else QPixmap()
        self._side = QPixmap(self.side_path) if self.side_path else QPixmap()
        self._cache: Dict[Tuple[int, bool, int], QPixmap] = {}

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_config(cls, cfg: Config) -> Optional["SpriteSet"]:
        """配置里有可用立绘就返回，否则 None（调用方退回矢量版）。"""
        front = sprite_path(cfg.sprite)
        if not front or not os.path.exists(front):
            return None
        s = cls(front, sprite_path(cfg.sprite_side))
        return s if s.ok else None

    @property
    def ok(self) -> bool:
        return not self._front.isNull()

    @property
    def aspect(self) -> float:
        """宽/高。窗口按这个比例算宽，立绘才不会被拉扁。"""
        if not self.ok:
            return 0.78
        return self._front.width() / max(1, self._front.height())

    @property
    def has_side(self) -> bool:
        return not self._side.isNull()

    # ---------------------------------------------------------------- 缩放缓存
    def _scaled(self, height: float, side: bool, dpr: float) -> QPixmap:
        key = (int(height * 4), side, int(dpr * 100))     # 高度量化到 0.25px，避免浮点抖动刷缓存
        pm = self._cache.get(key)
        if pm is not None:
            return pm
        src = self._side if (side and self.has_side) else self._front
        target_h = max(8, int(height * dpr))
        pm = src.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        if len(self._cache) > 24:          # 换缩放倍率时别把缓存撑爆
            self._cache.clear()
        self._cache[key] = pm
        return pm

    # ---------------------------------------------------------------- 绘制
    def draw(self, p: QPainter, cx: float, cy: float, w: float, h: float,
             pose: PetPose, facing: int = 0) -> None:
        """
        在 (cx, cy) 处画出高 h 的立绘。

        facing: 0 正面、+1 朝右、-1 朝左。有侧面图才生效；
                侧面原图朝左，朝右时水平镜像。
        """
        if not self.ok:
            return
        squash = max(-1.0, min(1.0, pose.squash))
        sx = 1.0 + 0.16 * max(0.0, -squash) + 0.05 * max(0.0, squash)
        sy = 1.0 + 0.14 * max(0.0, squash) - 0.12 * max(0.0, -squash)

        dpr = QGuiApplication.primaryScreen().devicePixelRatio() \
            if QGuiApplication.primaryScreen() else 1.0
        use_side = self.has_side and facing != 0
        pm = self._scaled(h, use_side, dpr)
        dw = pm.width() / dpr
        dh = pm.height() / dpr

        p.save()
        p.translate(cx, cy + pose.bob * h * 0.5)
        p.rotate(pose.tilt)
        p.scale(sx, sy)
        if use_side and facing > 0:
            p.scale(-1.0, 1.0)
        p.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), pm, QRectF(pm.rect()))
        p.restore()

    # ---------------------------------------------------------------- 托盘图标
    def head_icon(self, size: int = 64) -> QPixmap:
        """裁头部当托盘图标：整只立绘缩到 16px 会糊成一个色块。"""
        if not self.ok:
            return QPixmap()
        src = self._front
        # 取"头 + 一点肩"的方形区域。只按 0.46 高度裁会切到眼睛，
        # 立绘里脸大概在 0.2~0.55H 这一带，所以裁到 0.62H。
        ch = int(src.height() * 0.62)
        cw = min(src.width(), ch)
        x = (src.width() - cw) // 2
        y = int(src.height() * 0.01)
        return src.copy(x, y, cw, ch).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
