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
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(HERE, "pet_config.json")

# 配置文件格式版本。1 = 鲸鱼娘那版（没有这个键就按 1 算），2 = 换新立绘之后。
CURRENT_CONFIG_VERSION = 2

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
    # 配置文件的"格式版本"。老配置文件里没这个键（默认 1），load() 会据此迁移。
    # 加这个字段是为了解决一个很实际的问题：用户机器上的 pet_config.json 一旦生成，
    # 里面就写死了旧的立绘路径，换形象之后**新版默认值永远轮不到生效**。
    config_version: int = CURRENT_CONFIG_VERSION

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
    # sprite 指向哪张，哪张就是"兜底帧"；同目录下的其它 PNG 会被当作帧一起加载
    # （文件名 = 帧名，见 ui/anim.py 的 MOOD_FRAME）。
    sprite: str = "assets/girl/stand.png"
    sprite_frames: str = ""          # 留空 = 用 sprite 所在目录
    sprite_side: str = ""            # 这套立绘没有侧视图，走动时改用倾斜表达方向
    sprite_walk: bool = True

    # ---------------- 桌宠 ----------------
    pet_name: str = "小桃"
    pet_scale: float = 1.12
    follow: str = "hand"             # hand / cursor / window / corner
    follow_gain: float = 1.6         # 手掌位移 -> 屏幕位移的放大倍数
    # 跟鼠标：身体中心与指针保持多远（太近会盖住指针，点什么都点不到）
    cursor_trail: float = 110.0
    # 跟鼠标：指针在宠物身上停多久才允许点击。
    # 只留一点点防抖（120ms）：停住就是"我要点它"，拖太久会让人觉得点了没反应。
    cursor_click_delay: float = 0.12
    always_on_top: bool = True
    click_through: bool = False      # 全屏穿透（桌宠不挡点击）
    home_corner: str = "br"          # br / bl / tr / tl
    idle_after: float = 5.0          # 多久没看到手就回角落
    idle_sit_after: float = 40.0     # 再久一点就坐下（有人的时候站着更精神）
    pet_pat_limit: int = 5           # 连点几下会生气（每下间隔 < 3 秒才累计）
    pet_pat_window: float = 3.0      # 连点的判定窗口
    bubble_ms: int = 2200

    # ---- 吸附到窗口（follow == "window"）----
    # 贴着"手势要操作的那个窗口"站，用窗口标题当标签 —— 一眼看出在操作谁。
    # 只在窗口"正常大小"时贴：最小化没有边，最大化/全屏四条边都在屏幕外。
    dock_pad: float = 4.0            # 身体压出窗口边界多少像素
    dock_overlap: float = 0.16       # 身体压进窗口的比例（16% 体宽，不挡内容又能看出在贴边）
    dock_y: float = 0.28             # 竖直锚点：0.28 = 窗口高度的 28% 处（避开标题栏）
    dock_side: str = "right"         # 优先站窗口右边（右边放不下会自动翻到左边）
    dock_title_len: int = 12         # 标签里窗口标题最多几个字
    dock_face_front: bool = True     # 吸附时正面朝前（关掉会侧身看窗口）

    # ---------------- 手势绑定（可在"手势对照表"页面里改） ----------------
    # 键形如 "right:OPEN_PALM" / "both:FIST"，值是 action key（见 intent/mapping.py）。
    # 只存"被改过的"条目：没改的走默认表，这样以后默认表升级也能吃到。
    bindings: Dict[str, str] = field(default_factory=dict)

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
                if _migrate(cfg, data.get("config_version", 1)):
                    try:
                        cfg.save(path)
                        print("[config] 已把形象升级为新版默认值（旧配置已迁移）")
                    except Exception as e:
                        print(f"[config] 迁移后保存失败：{e}")
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


# ------------------------------------------------------------------ 迁移
# 为什么需要它：默认值只在"配置文件不存在"时才生效。用户机器上那份
# pet_config.json 一旦生成，sprite 就写死成 assets/whale_front.png 了 ——
# 于是无论怎么改代码里的默认立绘，打开还是那只鲸鱼，看着就像"没更新"。
# 做法是按格式版本号显式迁移一次，而且**只动还是旧默认值的字段**：
# 用户自己改过的名字、体型、跟随方式，一律不碰。

# 旧版默认值（用来判断"这个字段是不是从没被改过"）
_LEGACY_SPRITES = ("assets/whale_front.png", "assets/whale_side.png")
_LEGACY_NAMES = ("小鲸", "鲸鱼娘")
_LEGACY_SCALES = (1.0,)


def _rel(p: str) -> str:
    """归一化路径用于比对：反斜杠转正斜杠、去掉开头的 ./、统一小写。"""
    s = (p or "").replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s.lower()


def _migrate(cfg: "Config", ver: int) -> bool:
    """把旧配置迁到当前版本。返回 True 表示改过了、需要回存。"""
    if ver >= CURRENT_CONFIG_VERSION:
        return False

    fresh = Config()          # 只借它的默认值做比对，不参与别的逻辑
    changed = False

    sprite = _rel(cfg.sprite)
    # 空着（没设过）或仍指向鲸鱼那套，都算"还在旧主题上"
    legacy_theme = (not sprite) or sprite in _LEGACY_SPRITES

    if legacy_theme:
        if cfg.sprite != fresh.sprite:
            cfg.sprite = fresh.sprite
            changed = True
        # 侧视图是鲸鱼那套独有的；换成新立绘后它必然是个坏路径，留着会让
        # SpriteSet 加载时刷一堆告警，走动也没人看侧身 —— 直接清掉。
        if cfg.sprite_side:
            cfg.sprite_side = ""
            changed = True
        # 帧目录同理：旧版没有多帧立绘，有值就说明是手写的实验配置
        if _rel(cfg.sprite_frames) in _LEGACY_SPRITES:
            cfg.sprite_frames = ""
            changed = True
        if cfg.pet_name in _LEGACY_NAMES:
            cfg.pet_name = fresh.pet_name
            changed = True
        try:
            if round(float(cfg.pet_scale), 3) in _LEGACY_SCALES:
                cfg.pet_scale = fresh.pet_scale
                changed = True
        except (TypeError, ValueError):
            cfg.pet_scale = fresh.pet_scale
            changed = True

    # 版本号本身也要落到新值，否则每次启动都会重复走一遍迁移
    if cfg.config_version != CURRENT_CONFIG_VERSION:
        cfg.config_version = CURRENT_CONFIG_VERSION
        changed = True

    return changed
