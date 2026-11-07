# -*- coding: utf-8 -*-
"""
贴图形象 —— 直接画现成的透明立绘/动画，而不是程序化绘制。

素材
    pet/assets/girl/*.png —— 粉发猫耳帽 Q 版少女的 7 个静态表情。
    pet/assets/girl/anim/<动作>/00.png … —— 5 套循环动画（待机打瞌睡、被拎起、
    跳舞、比心、挥手），由 tools/import_pet_gifs.py 从美术给的 GIF 转出来。

    这两批是**同一套构图**：画布都是 260×390，角色脚底落在同一条线上、
    身体中线都在画布中线上。所以静态表情和动画之间来回切，角色不会跳位置、
    也不会突然变大。"一样大"这件事全靠转换脚本里的统一缩放系数，别在这里补。

为什么要动画
    一张图只能改整体位移和形变；GIF 转出来的帧能让"待机"真的在打瞌睡、
    "被拎起"真的在乱蹬腿——这是程序化变换做不出来的东西。

性能
    动画是 5 × 36 = 180 张 260×390 的图，全解码进内存要 70 MB 以上。
    所以**按需加载**：只有真的播到某套动作才读它的 36 张，且最多同时留 2 套
    （当前 + 上一个，来回切不会反复读盘）。
    缩放结果另做缓存，键里带帧序号和 devicePixelRatio，高 DPI 屏上不会糊。
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap

from ..core.config import Config, sprite_path
from .anim import Motion
from .paint import PetPose


class AnimClip:
    """一套循环动画：若干张等尺寸的帧 + 每帧停留时长（秒）。"""

    __slots__ = ("name", "frames", "durs", "total")

    def __init__(self, name: str, frames: List[QPixmap],
                 durs: List[float]) -> None:
        self.name = name
        self.frames = frames
        # 帧数对不上时按平均时长补齐，别让播放直接崩
        if len(durs) != len(frames):
            avg = (sum(durs) / len(durs)) if durs else 0.08
            durs = [avg] * len(frames)
        self.durs = durs
        self.total = sum(durs) or 0.08

    def index_at(self, t: float) -> int:
        """时间 -> 帧序号。超出总长就循环，动画自己接得上。"""
        if len(self.frames) <= 1:
            return 0
        t = t % self.total
        acc = 0.0
        for i, d in enumerate(self.durs):
            acc += d
            if t < acc:
                return i
        return len(self.frames) - 1

    def at(self, t: float) -> QPixmap:
        return self.frames[self.index_at(t)]


class SpriteSet:
    """一组同尺寸立绘帧（文件名 = 帧名）+ 若干套循环动画。"""

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

        # ---- 动画 ----
        # 约定：动画目录就在立绘旁边（assets/girl/anim），不往配置里加路径。
        # 配置项已经够多了，而"立绘和它的动画放在一起"是天然的组织方式。
        self._anim_dir = os.path.join(os.path.dirname(front), "anim")
        self._anim_meta: Dict[str, dict] = self._read_meta()
        self._clips: "OrderedDict[str, AnimClip]" = OrderedDict()
        self._clip_budget = 2            # 同时留在内存里的套数（当前 + 上一个）

    def _read_meta(self) -> Dict[str, dict]:
        path = os.path.join(self._anim_dir, "meta.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        # "_layout" 是转换脚本记的构图参数（不是动画），别当成一套动作
        return {k: v for k, v in data.items()
                if isinstance(v, dict) and k != "_layout"}

    @property
    def has_anim(self) -> bool:
        return bool(self._anim_meta)

    @property
    def anim_names(self) -> Tuple[str, ...]:
        return tuple(self._anim_meta)

    def clip(self, name: str) -> Optional[AnimClip]:
        """
        取一套动画（首次调用才读盘）。没有就返回 None，调用方退回静态帧。

        为什么要淘汰：5 套 36 帧全留着是 70MB 显存级别的开销，而桌宠同一时刻
        只会播一套。留 2 套是为了"待机 -> 跳舞 -> 待机"这种来回切不用反复解码。
        """
        if not name or name not in self._anim_meta:
            return None
        if name in self._clips:
            self._clips.move_to_end(name)
            return self._clips[name]

        folder = os.path.join(self._anim_dir, name)
        if not os.path.isdir(folder):
            return None
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".png"))
        frames = [QPixmap(os.path.join(folder, f)) for f in files]
        frames = [pm for pm in frames if not pm.isNull()]
        if not frames:
            return None

        durs = [max(0.01, float(d) / 1000.0)
                for d in self._anim_meta[name].get("durs", [])]
        clip = AnimClip(name, frames, durs)
        self._clips[name] = clip
        while len(self._clips) > self._clip_budget:
            old, _ = self._clips.popitem(last=False)
            # 帧被回收了，它那些缩放结果也别留着占地方
            self._cache = {k: v for k, v in self._cache.items()
                           if not k[1].startswith(old + "#")}
        return clip

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
    def _scaled_pm(self, height: float, src: QPixmap, key: str,
                   dpr: float) -> QPixmap:
        ck = (int(height * 4), key, int(dpr * 100))   # 高度量化到 0.25px，避免刷缓存
        pm = self._cache.get(ck)
        if pm is not None:
            return pm
        target_h = max(8, int(height * dpr))
        pm = src.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        # 上限给到 120：一套动画 36 帧 x 两个缩放档就 72 了，48 会在播动画时
        # 反复清空缓存，每帧都重新缩放，帧率肉眼可见地掉。
        if len(self._cache) > 120:
            self._cache.clear()
        self._cache[ck] = pm
        return pm

    def _scaled(self, height: float, frame: str, dpr: float) -> QPixmap:
        src = self.frames.get(frame) or self.frames[self.default_frame]
        return self._scaled_pm(height, src, frame, dpr)

    # ---------------------------------------------------------------- 绘制
    def draw(self, p: QPainter, cx: float, cy: float, w: float, h: float,
             pose: PetPose, frame: str = "", motion: Optional[Motion] = None,
             facing: int = 0, anim: str = "", t: float = 0.0) -> None:
        """
        在 (cx, cy) 处画出高 h 的立绘。

        frame   要画的静态帧名；不存在就退到主立绘
        anim    要播的动画名（见 assets/girl/anim/）。给了就**优先播动画**，
                frame 只在动画不存在时兜底 —— 动画是美术画的完整动作，
                比"换一张表情图 + 缩放"更像活的。
        t       动画时间（秒），传入的是单调时钟，内部自己取模循环
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

        src: Optional[QPixmap] = None
        key = self.pick(frame)
        # 侧视图是"只有一张"的老素材，和动画不通用；走侧视时退回静态
        if anim and not use_side:
            clip = self.clip(anim)
            if clip is not None:
                idx = clip.index_at(t)
                src, key = clip.frames[idx], f"{anim}#{idx}"
        if src is None:
            src = self.frames.get(key) or self.frames[self.default_frame]
        pm = self._scaled_pm(h, src, key, dpr)
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

        裁剪比例按当前 260×390 画布量的：帽子顶在 25% 高处、下巴约 62%，
        所以取那一段、居中取正方形 —— 正好一顶帽子 + 一张脸。
        换立绘或换画布尺寸时，把这两个数改一下就行。
        """
        if not self.ok:
            return QPixmap()
        src = self.frames[self.default_frame]
        ch = int(src.height() * 0.38)
        cw = min(src.width(), ch)
        x = (src.width() - cw) // 2
        y = int(src.height() * 0.24)
        return src.copy(x, y, cw, ch).scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
