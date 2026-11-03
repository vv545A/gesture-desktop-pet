# -*- coding: utf-8 -*-
"""
贴图形象 —— 直接画现成的透明立绘，而不是程序化绘制。

素材
    pet/assets/girl/*.png —— 粉发猫耳帽 Q 版少女（7 帧：站立/开心/招手/思考/
    打瞌睡/生气/坐下）。由 tools/prep_sprites.py 从「桌宠形象」原图统一抠出，
    所以**每帧画布尺寸完全一致、角色位置也一致** —— 切帧时不会横跳。

    pet/assets/whale_*.png —— 上一版的鲸鱼娘（保留兼容，配置里指过去就能换回来）。

为什么要支持"多帧"
    一张图只能改整体位移和形变；有了 7 帧，情绪才有真正的表情：
    开心张嘴、生气鼓脸、打瞌睡闭眼。帧名就是文件名，往 assets/girl/ 里
    丢一张 news.png 就能在 anim.MOOD_FRAME 里直接用，不用改代码。

性能
    立绘原始 386×580，每帧缩放很浪费；这里按目标高度**预缩放并缓存**，
    缓存键带上帧名和 devicePixelRatio，高 DPI 屏上不会糊，切帧也不会掉帧。
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap

from ..core.config import Config, sprite_path
from .anim import Motion
from .paint import PetPose


class SpriteSet:
    """一组同尺寸立绘帧（文件名 = 帧名）。"""

    def __init__(self, front: str, side: str = "", frame_dir: str = "") -> None:
        self.front_path = front
        self.side_path = side if side and os.path.exists(side) else ""
        self._front = QPixmap(front) if os.path.exists(front) else QPixmap()
        self._side = QPixmap(self.side_path) if self.side_path else QPixmap()
        self.frames: Dict[str, QPixmap] = {}
        self.default_frame = os.path.splitext(os.path.basename(front))[0]
        if not self._front.isNull():
            self.frames[self.default_frame] = self._front
        self._load_frames(frame_dir or os.path.dirname(front))
        self._cache: Dict[Tuple[int, str, int], QPixmap] = {}

    # ---------------------------------------------------------------- 构造
    @classmethod
    def from_config(cls, cfg: Config) -> Optional["SpriteSet"]:
        """配置里有可用立绘就返回，否则 None（调用方退回矢量版）。"""
        front = sprite_path(cfg.sprite)
        if not front or not os.path.exists(front):
            return None
        s = cls(front, sprite_path(cfg.sprite_side), sprite_path(cfg.sprite_frames))
        return s if s.ok else None

    def _load_frames(self, folder: str) -> None:
        """
        把同目录的 PNG 全当帧加载。

        为什么按目录扫而不是写死清单：加一张新表情只要丢文件进去，
        不用回来改代码；同时也兼容"只有一张图"的老配置（frames 里只有它自己）。
        """
        if not folder or not os.path.isdir(folder):
            return
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".png"):
                continue
            stem = os.path.splitext(name)[0]
            if stem in self.frames:
                continue
            pm = QPixmap(os.path.join(folder, name))
            if not pm.isNull():
                self.frames[stem] = pm

    @property
    def ok(self) -> bool:
        return not self._front.isNull()

    @property
    def frame_names(self) -> Tuple[str, ...]:
        return tuple(sorted(self.frames))

    def has(self, frame: str) -> bool:
        return frame in self.frames

    def pick(self, frame: str) -> str:
        """要的帧没有就退到主立绘 —— 少一张图不该让桌宠变成空白。"""
        return frame if frame in self.frames else self.default_frame

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
    def _scaled(self, height: float, frame: str, dpr: float) -> QPixmap:
        key = (int(height * 4), frame, int(dpr * 100))   # 高度量化到 0.25px，避免刷缓存
        pm = self._cache.get(key)
        if pm is not None:
            return pm
        src = self.frames.get(frame) or self.frames[self.default_frame]
        target_h = max(8, int(height * dpr))
        pm = src.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        if len(self._cache) > 48:          # 7 帧 x 几个缩放档，别把缓存撑爆
            self._cache.clear()
        self._cache[key] = pm
        return pm

    # ---------------------------------------------------------------- 绘制
    def draw(self, p: QPainter, cx: float, cy: float, w: float, h: float,
             pose: PetPose, frame: str = "", motion: Optional[Motion] = None,
             facing: int = 0) -> None:
        """
        在 (cx, cy) 处画出高 h 的立绘。

        frame   要画的帧名；不存在就退到主立绘
        motion  动作片段叠加的变换（位移/缩放/旋转），None = 只按 pose 画
        facing  0 正面、±1 朝向。**只有配了侧视图才真的翻面**；没有侧视图时
                由调用方改用倾斜来表达"往哪边走"，这里忽略。
        """
        if not self.ok:
            return
        squash = max(-1.0, min(1.0, pose.squash))
        sx = 1.0 + 0.16 * max(0.0, -squash) + 0.05 * max(0.0, squash)
        sy = 1.0 + 0.14 * max(0.0, squash) - 0.12 * max(0.0, -squash)

        dpr = QGuiApplication.primaryScreen().devicePixelRatio() \
            if QGuiApplication.primaryScreen() else 1.0
        use_side = self.has_side and facing != 0
        pm = self._scaled(h, self.pick(frame), dpr)
        dw = pm.width() / dpr
        dh = pm.height() / dpr

        dx = dy = rot = 0.0
        if motion is not None:
            # 片段的位移按身高比例给，这样放大桌宠时幅度跟着一起放大
            dx, dy, rot = motion.dx * w, motion.dy * h, motion.rot
            sx *= motion.sx
            sy *= motion.sy

        p.save()
        p.translate(cx + dx, cy + dy + pose.bob * h * 0.5)
        p.rotate(pose.tilt + rot)
        p.scale(sx, sy)
        if use_side and facing > 0:
            p.scale(-1.0, 1.0)
        p.drawPixmap(QRectF(-dw / 2, -dh / 2, dw, dh), pm, QRectF(pm.rect()))
        p.restore()

    # ---------------------------------------------------------------- 托盘图标
    def head_icon(self, size: int = 64) -> QPixmap:
        """
        裁头部当托盘图标：整只立绘缩到 16px 会糊成一个色块。

        裁剪比例按"粉发少女"这套立绘量的：帽子顶在 2% 处，下巴约 55%，
        所以取 0→62% 高、居中取正方形 —— 正好一顶帽子 + 一张脸。
        换成别的立绘（比如鲸鱼娘），把这两个数改一下就行。
        """
        if not self.ok:
            return QPixmap()
        src = self.frames[self.default_frame]
        ch = int(src.height() * 0.62)
        cw = min(src.width(), ch)
        x = (src.width() - cw) // 2
        y = int(src.height() * 0.02)
        return src.copy(x, y, cw, ch).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
