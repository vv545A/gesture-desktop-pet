# -*- coding: utf-8 -*-
"""
配置 —— 一份 dataclass 作为唯一事实来源，JSON 只是它的持久化形式。

为什么不用纯字典 / 命令行参数：
    调一个桌宠的手感会动到十几个数值（悬停时长、跟随增益、跟手灵敏度……），
    全都塞命令行会变成一屏乱麻；全塞 JSON 又没法在代码里补默认值。
    做法是 dataclass 定字段和默认值，JSON 覆盖，命令行再覆盖一次。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(HERE, "pet_config.json")

# 模型路径沿用主程序那一套（桌面文件夹相对定位，打包后也不会错）
MODEL_DIR = os.path.join(HERE, "models")
HAND_MODEL = os.path.join(MODEL_DIR, "hand_landmarker.task")
GESTURE_MODEL = os.path.join(MODEL_DIR, "gesture_recognizer.task")
FACE_MODEL = os.path.join(MODEL_DIR, "blaze_face_short_range.tflite")


def sprite_path(name: str) -> str:
    """立绘路径：相对路径按 pet/ 解析，绝对路径原样返回，空串表示不用贴图。"""
    if not name:
        return ""
    return name if os.path.isabs(name) else os.path.join(HERE, "pet", name)


@dataclass
class Config:
    # ---------------- 感知 ----------------
    cam: int = 0
    width: int = 1280
    height: int = 720
    hands: str = "auto"              # auto / position / model
    swap_hands: bool = False
    face_guard: bool = True
    use_gesture_model: bool = True

    # ---------------- 判定（与 main.py 的默认值保持一致，手感才不会割裂） ----
    hold: float = 0.5                # 悬停多久确认（单手窗口操作）
    cooldown: float = 0.6
    grace: float = 0.35              # 丢帧宽容
    still_speed: float = 0.6         # 掌长/秒，超过就算"手在动"
    still_gate: bool = True
    # 双手（音乐）：两只手同手势要保持多久才算数。
    # 比单手短一点 —— 双手本来就难维持，而且"两只手同一个手势"本身已经很难误触。
    pair_hold: float = 0.4
    pair_cooldown: float = 0.7
    # 双手同时捏合保持多久 = 总开关（紧急停止）
    toggle_hold: float = 1.0
    zoom_step: float = 0.35          # 预留：双手间距每变多少掌长走一格缩放
    pinch: float = 0.45
    thumb: float = 1.12

    # ---------------- 形象 ----------------
    # 立绘路径相对 pet/ 目录。留空则退回程序化矢量版（paint.draw_pet）。
    sprite: str = "assets/whale_front.png"
    sprite_side: str = "assets/whale_side.png"
    sprite_walk: bool = True         # 走动时切侧面立绘（有侧图才生效）

    # ---------------- 桌宠 ----------------
    pet_name: str = "小鲸"
    pet_scale: float = 1.0
    follow: str = "hand"             # hand / cursor / corner
    follow_gain: float = 1.6         # 手掌位移 -> 屏幕位移的放大倍数
    always_on_top: bool = True
    click_through: bool = False      # 全屏穿透（桌宠不挡点击）
    home_corner: str = "br"          # br / bl / tr / tl
    idle_after: float = 5.0          # 多久没看到手就回角落
    bubble_ms: int = 2200

    # ---------------- 调试 ----------------
    preview: bool = False            # 预留：摄像头预览窗
    dry_run: bool = False            # 只判不发（等价 main.py --test）

    # ---------------------------------------------------------------- 持久化
    def save(self, path: str = DEFAULT_CONFIG) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG) -> "Config":
        """读 JSON；文件不存在就写一份默认的，方便用户直接改。"""
        cfg = cls()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data: Dict[str, Any] = json.load(f)
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:                 # 忽略改名/删除后残留的旧键
                        setattr(cfg, k, v)
            except Exception as e:
                print(f"[config] 读取失败，用默认值：{e}")
        else:
            try:
                cfg.save(path)
                print(f"[config] 已生成默认配置 {path}")
            except Exception as e:
                print(f"[config] 生成配置失败：{e}")
        return cfg

    def override(self, **kw: Any) -> "Config":
        """命令行覆盖：值为 None 的键直接跳过（argparse 没给的就是 None）。"""
        for k, v in kw.items():
            if v is not None and hasattr(self, k):
                setattr(self, k, v)
        return self
