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
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:                     # 极小概率：装依赖时漏了 PyYAML
    yaml = None

log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认配置文件用 YAML 而不是 JSON。
#: 理由很实际：调手感要动十几个数值（悬停时长、跟随增益、冷却……），
#: JSON 不能写注释，改完过两个月自己都看不懂当初为什么设成 0.45。
DEFAULT_CONFIG = os.path.join(HERE, "gestures.yaml")

#: 旧版配置文件。存在时自动读它并落成 YAML，不用用户手动搬。
LEGACY_CONFIG = os.path.join(HERE, "pet_config.json")

#: 亲密度的存档。刻意和配置分开放：配置是"用户意图"，亲密度是"它记住的事"，
#: 点一次"恢复默认配置"不该把相处记录一起清掉。
BOND_FILE = os.path.join(HERE, "bond.json")

#: 生成新配置时写进文件头的说明。yaml.safe_dump 不会保留注释，
#: 所以这段是拼在 dump 结果前面的纯文本。
CONFIG_HEADER = """\
# ============================================================
#  GesturePet 配置文件
#
#  改完保存、重启程序即可生效，不需要动代码。
#  只写你想改的项；没写的会自动用程序里的默认值。
#  bindings 留空 = 用默认手势表（✌️跳舞 / ✊睡觉 / ✋唤醒 / ☝️跟随 / 👍浏览器）
# ============================================================

"""

# 配置文件格式版本。1 = 鲸鱼娘那版（没有这个键就按 1 算），2 = 换新立绘之后。
CURRENT_CONFIG_VERSION = 2

# 模型路径沿用主程序那一套（桌面文件夹相对定位，打包后也不会错）
MODEL_DIR = os.path.join(HERE, "models")
HAND_MODEL = os.path.join(MODEL_DIR, "hand_landmarker.task")
GESTURE_MODEL = os.path.join(MODEL_DIR, "gesture_recognizer.task")
FACE_MODEL = os.path.join(MODEL_DIR, "blaze_face_short_range.tflite")
# 表情模型（3.6MB，带 52 个 blendshape 系数）。缺失时情绪感知自动关闭。
# 不在仓库里，需要单独下载（见 README 的"情绪感知"一节）。
EMOTION_MODEL = os.path.join(MODEL_DIR, "face_landmarker.task")


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
    # 在场检测：用本地人脸检测判断人在不在，人走了宠物打盹、人回来主动迎接。
    # 只判断"有没有脸"，不存图、不识别身份。
    presence: bool = True
    presence_interval: float = 0.5   # 多久查一次（调低反应快、更费 CPU）
    away_after: float = 6.0          # 多久没看到人算离开（要能容忍低头捡东西）
    # 情绪感知：从面部动作系数推断"在笑/累了/皱眉"，用来切换陪伴方式。
    # 开销比在场检测高（约 4~9ms），所以默认 1 秒一次。
    emotion: bool = True
    emotion_interval: float = 1.0

    # ---------------- 判定（与 main.py 的默认值保持一致，手感才不会割裂） ----
    hold: float = 0.5                # 悬停多久确认（单手窗口操作）
    cooldown: float = 0.6
    grace: float = 0.35              # 丢帧宽容
    still_speed: float = 0.6         # 掌长/秒，超过就算"手在动"
    still_gate: bool = True

    # 挥手（张开手掌左右划）= 翻页。
    # 挥手是"动能"：手划过去那一下就完成，200ms 足够；静态手势要稳住半秒，
    # 这是手势唯一能比鼠标快的形态。位移用掌长归一化，手离镜头远近不影响。
    swipe_on: bool = True
    swipe_travel: float = 1.6        # 最小位移（掌长）。调小=更容易触发，也更容易误触
    # 长按（触发后**继续按住**）= 第二层动作，0 = 关闭。
    # 用长按而不是"连做两次"：连做两次的第一下已经执行过单击动作，撤不回来；
    # 长按是"等够了才进入第二层"，没有副作用。总时长 = hold + 这个值。
    long_press: float = 1.0
    # 食指滚轮：掌心上下挪多少掌长滚一格。调小滚得快。
    scroll_step: float = 0.30
    # 双手（音乐）：两只手同手势要保持多久才算数。
    # 比单手短一点 —— 双手本来就难维持，而且"两只手同一个手势"本身已经很难误触。
    pair_hold: float = 0.4
    pair_cooldown: float = 0.7
    # 双手同时捏合保持多久 = 总开关（紧急停止）
    toggle_hold: float = 1.0
    # 隔空撸宠：手速低于这个值（掌长/秒）、且宠物已经贴到手边，就算"在摸它"。
    # 调低会更难摸到（手稍微一动就断），调高则容易误判成"一直在被摸"。
    pet_speed: float = 0.9
    # 摸多久才开始有反应（秒）。太短会在手路过时误触发，太长又让人觉得没反馈。
    pet_delay: float = 0.7
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
    pet_scale: float = 1.12          # 新立绘比旧鲸鱼娘细一圈，1.0 会显得太小
    # 👍 打开的页面。改成 https://www.bing.com/search?q=关键词 就变成"隔空搜索"。
    # 必须命中 actions.BROWSER_ALLOWLIST 的前缀，配错了会被拒绝并记一条警告。
    browser_url: str = "https://www.bing.com/"
    # hand = 跟着手掌跑 / cursor = 跟在鼠标后 / window = 吸附前台窗口 / corner = 守在角落
    # 默认 corner：绝大多数时候用户只是要它在旁边待着，
    # "跟着手到处跑"更适合演示，长期用会显得挡事。
    follow: str = "corner"
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
    @staticmethod
    def _read(path: str) -> Dict[str, Any]:
        """按扩展名选解析器：.yaml/.yml 走 YAML，其余走 JSON。"""
        use_yaml = path.lower().endswith((".yaml", ".yml")) and yaml is not None
        with open(path, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) if use_yaml else json.load(f)) or {}

    @staticmethod
    def _dump(data: Dict[str, Any], path: str) -> None:
        use_yaml = path.lower().endswith((".yaml", ".yml")) and yaml is not None
        with open(path, "w", encoding="utf-8") as f:
            if use_yaml:
                f.write(CONFIG_HEADER)
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False,
                               default_flow_style=False)
            else:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self, path: str = DEFAULT_CONFIG) -> None:
        self._dump(asdict(self), path)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        """
        读配置。查找顺序：
            1. 显式传入的 path（命令行 --config）
            2. gestures.yaml（新默认）
            3. pet_config.json（旧版，读到就顺手迁移成 YAML）
            4. 都没有 -> 生成一份带注释的 gestures.yaml

        读取失败**不让程序崩**：退回默认值 + 一句能看懂的话。
        配置文件被手改坏是常态，那不该等于"打不开程序"。
        """
        cfg = cls()
        target = path or DEFAULT_CONFIG

        source: Optional[str] = None
        if path:
            source = path if os.path.exists(path) else None
        elif os.path.exists(target):
            source = target
        elif os.path.exists(LEGACY_CONFIG):
            source = LEGACY_CONFIG

        if source is None:
            try:
                cfg.save(target)
                log.info("已生成默认配置 %s", target)
            except Exception as e:
                log.error("生成配置失败，用默认值：%s", e)
            return cfg

        data: Dict[str, Any] = {}
        try:
            data = cls._read(source)
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:                 # 忽略改名/删除后残留的旧键
                    setattr(cfg, k, v)
        except Exception as e:
            log.warning("读取 %s 失败，用默认值：%s", os.path.basename(source), e)

        # 形象升级迁移：老配置文件里 sprite 写死的是上一代立绘，
        # 不迁的话改了默认值也看不见效果。只动"仍是旧默认值"的字段。
        # 没有 config_version 字段 = 这份配置比"版本号"这个概念还老，按 1 算。
        if _migrate(cfg, data.get("config_version", 1)):
            try:
                cfg.save(target)
                log.info("已把形象升级为新版默认值")
            except Exception as e:
                log.warning("迁移后保存失败：%s", e)

        # 旧 JSON 自动迁移。只在"没指定路径、且新文件还不存在"时做，
        # 免得把用户显式指定的路径也复制一份出来。
        if source == LEGACY_CONFIG and not os.path.exists(target):
            try:
                cfg.save(target)
                log.info("已把旧配置迁移到 %s", os.path.basename(target))
            except Exception as e:
                log.warning("迁移配置失败（不影响运行）：%s", e)

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
