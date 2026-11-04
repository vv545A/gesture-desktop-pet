# -*- coding: utf-8 -*-
"""
总开关的唯一入口。

为什么单独抽一个类：
    现在关手势识别有四个入口——托盘菜单、桌宠右上角的关闭键、点桌宠弹出的
    控制面板、双手捏合保持。如果每个入口各写一遍"改引擎状态 + 停摄像头 +
    发事件"，迟早会漏掉其中一步（最容易漏的是**停摄像头**，摄像头指示灯
    一直亮着，用户会以为程序没关掉）。
    所有入口只调 set_enabled()，要补的步骤也只补在这一处。

它不依赖 Qt，也不依赖 MediaPipe：headless 模式照样能用。
"""

from __future__ import annotations

import logging

from .bus import EventBus
from .config import Config
from .engine import GestureEngine
from .events import ControlToggled, HandsSeen, PetSay


class ControlCenter:
    def __init__(self, bus: EventBus, engine: GestureEngine, cfg: Config) -> None:
        self.bus = bus
        self.engine = engine
        self.cfg = cfg
        self.worker = None          # PerceptionWorker，demo 模式下为 None
        self._on_quit = None        # 由 app 挂上来（退出要一起停线程、关窗口）

    def attach_worker(self, worker) -> None:
        """感知线程建好之后挂上来（它可以暂停/恢复摄像头）。"""
        self.worker = worker
        if worker is not None and hasattr(worker, "set_active"):
            worker.set_active(self.engine.enabled)

    def attach_quit(self, fn) -> None:
        self._on_quit = fn

    def request_quit(self) -> None:
        if self._on_quit is not None:
            self._on_quit()

    @property
    def enabled(self) -> bool:
        return bool(self.engine.enabled)

    def set_enabled(self, on: bool) -> bool:
        """开/关手势识别（引擎状态 + 摄像头 + 广播事件）。返回切换后的状态。"""
        on = self.engine.set_enabled(bool(on))
        if self.worker is not None and hasattr(self.worker, "set_active"):
            self.worker.set_active(on)
        if not on:
            # 关掉之后立刻把"看到手"的状态清掉：不然桌宠会停在"盯着手看"
            # 的表情上，而屏幕上其实已经没手了。
            self.bus.post(HandsSeen(count=0))
        self.bus.post(ControlToggled(enabled=on))
        return on

    def toggle(self) -> bool:
        return self.set_enabled(not self.engine.enabled)

    # ------------------------------------------------- 看人的两个能力（运行时开关）
    # 为什么放在这里而不是各自在 UI 里改 cfg：
    #   托盘菜单和控制面板都有这两个开关，两处各写一遍"改配置 + 落盘 + 说句话"，
    #   迟早有一处漏掉；而且"开关拨上去了但检测器其实不存在"这种情况，
    #   必须由知道 worker 内部状态的地方来判断，UI 层猜不出来。
    def set_emotion(self, on: bool) -> bool:
        """
        情绪感知开关。返回"打开之后真的能读表情吗"。

        False 的含义：配置已改（用户意图要记住），但没有可用的表情检测器——
        `models/face_landmarker.task` 缺失时 `EmotionDetector` 会把自己禁用。
        这时必须说清楚，否则用户会以为"开了但没效果"是程序坏了。
        """
        self.cfg.emotion = bool(on)
        try:
            self.cfg.save()
        except Exception:
            pass                    # 配置目录只读也不该让开关失效
        if not on:
            self.bus.post(PetSay(text="好，不看你的表情了", mood="calm"))
            return True
        det = getattr(self.worker, "emotion", None) if self.worker is not None else None
        if self.worker is None:
            self.bus.post(PetSay(text="演示模式里没有摄像头，读不了表情", mood="shy"))
            return False
        if det is None:
            self.bus.post(PetSay(text="表情模型没装好，我暂时读不懂表情（见 README）",
                                mood="shy"))
            return False
        self._ensure_looking("看清你的表情")
        self.bus.post(PetSay(text="好，我读读你的表情", mood="happy"))
        return True

    def set_presence(self, on: bool) -> bool:
        """在场检测开关。返回 False 表示没有可用的人脸检测器。"""
        self.cfg.presence = bool(on)
        try:
            self.cfg.save()
        except Exception:
            pass
        if not on:
            self.bus.post(PetSay(text="我就在这儿等着，不打盹了", mood="calm"))
            return True
        det = getattr(self.worker, "presence", None) if self.worker is not None else None
        if self.worker is None:
            self.bus.post(PetSay(text="演示模式里没有摄像头，看不到你在不在", mood="shy"))
            return False
        if det is None:
            self.bus.post(PetSay(text="人脸模型没装好，我不知道你走没走开", mood="shy"))
            return False
        self._ensure_looking("看见你在不在")
        self.bus.post(PetSay(text="你走了我会打盹", mood="happy"))
        return True

    def _ensure_looking(self, why: str) -> None:
        """
        需要摄像头的能力（游戏 / 情绪 / 在场）在识别关着的时候要用它。

        为什么必须顺手打开：识别关掉之后感知线程的暂停分支每轮直接 `continue`，
        `_game_pending` 这种"待启动标志"永远轮不到被消费 —— 菜单上勾着、
        游戏却不开始，表现就是"点了没反应"。摄像头本来就是要开的那一步，
        与其静默失败，不如打开它并说一句为什么。
        """
        if self.enabled:
            return
        self.set_enabled(True)
        self.bus.post(PetSay(text=f"先把摄像头打开，才能{why}", mood="happy"))

    # ---------------------------------------------------------------- 小游戏
    def set_game(self, on: bool) -> bool:
        """
        进入/退出石头剪刀布。

        没有感知线程（--demo、--headless、摄像头起不来）时**明确说一句**，
        而不是静默失败——点菜单没反应是最让人恼火的一种交互。

        注意"开摄像头"和"说三秒后出拳"的先后：如果识别本来是关着的，
        摄像头的打开是异步的，几秒内根本不会有画面。这时先说"正在开摄像头"
        才是真话；一上来就喊"三秒后出拳"，用户会盯着一个不出拳的宠物干等。
        """
        if self.worker is None or not hasattr(self.worker, "set_game"):
            self.bus.post(PetSay(text="得先有摄像头才能玩哦", mood="shy"))
            return False
        was_looking = self.enabled
        if on:
            self._ensure_looking("跟我玩石头剪刀布")
        ok = self.worker.set_game(on)
        if not on:
            # 关闭和开启必须分开判：worker.set_game(False) 也会返回 True，
            # 用 "not ok" 去兜会让"不玩了"被说成"三秒后出拳"。
            self.bus.post(PetSay(text="不玩啦～", mood="calm"))
        elif not ok:
            self.bus.post(PetSay(text="游戏没起来，先看看摄像头是不是被占着",
                                 mood="surprised"))
        elif not was_looking:
            self.bus.post(PetSay(text="摄像头起来了就开始倒数，先把 ✊ ✌️ ✋ 准备好",
                                 mood="happy"))
        else:
            self.bus.post(PetSay(text="三秒后出拳！✊石头 ✌️剪刀 ✋布", mood="happy"))
        return ok

    def toggle_game(self) -> bool:
        """
        切换游戏。`_game_pending` 也算"在玩"：刚点开时 game.active 还是 False，
        只看 active 的话，连点两下会变成"再开一次"，永远关不掉。
        """
        worker = self.worker
        playing = bool(getattr(getattr(worker, "game", None), "active", False)) \
            or bool(getattr(worker, "_game_pending", False))
        return self.set_game(not playing)

    # ---------------------------------------------------------------- 单手表
    def set_single_preset(self, name: str) -> bool:
        """
        单手手势整套切换（"操作窗口" / "逗桌宠"）。

        为什么放在 ControlCenter 而不是各个界面里：
            现在有两个入口会调它——桌宠旁边那三个圆按钮、对照表顶部的胶囊。
            如果各写一遍"取预设 + 比对默认 + 写 cfg + 落盘 + 通知 mapping"，
            迟早有一处漏掉 `mapping.apply_bindings`，表现就是"点了、按钮高亮
            也变了、但手势还是旧的"。

        只动 left/right 的 14 个键，`both:`（双手音乐）和用户其它自定义原样
        保留：换单手的玩法不该顺手把双手的绑定也清掉。
        """
        from ..intent import mapping       # 延迟导入：core 层不必常驻依赖 intent

        rows = mapping.preset_bindings(name)
        if not rows:
            return False
        if name == mapping.current_single_preset():
            # 已经是这套了。仍然说一句，不然点了没反应像坏了；
            # 但不说"已切换"——那是在骗人。
            label = mapping.SINGLE_PRESET_LABEL.get(name, name)
            self.bus.post(PetSay(text=f"现在就是「{label}」呀", mood="calm"))
            return True

        default = mapping.default_bindings()
        # 单手的 left/right 和长按的 long 都归"换玩法"管，先清掉再从预设重建；
        # 挥手（swipe）不随预设变，原样保留。
        merged = {k: v for k, v in dict(self.cfg.bindings or {}).items()
                  if str(k).split(":")[0] not in ("left", "right", "long")}
        for key, action in rows.items():
            # 只存"和默认不一样"的：预设窗口那套就是出厂表，落盘后 bindings
            # 回到空，以后默认表升级也能吃到（同 binding 页的 _apply）。
            if default.get(key) != action:
                merged[key] = action

        self.cfg.bindings = merged
        mapping.apply_bindings(merged)
        try:
            self.cfg.save()
        except Exception as e:      # 落盘失败不影响本次会话，别把点击卡住
            logging.getLogger(__name__).error("保存配置失败：%s", e)

        label = mapping.SINGLE_PRESET_LABEL.get(name, name)
        text = "好，单手改成操作电脑啦" if name == "window" else "好，单手陪你玩～"
        if not self.enabled:
            # 识别关着时切表等于"改了但不会发生"。不说清楚，用户会以为切换失败。
            text += "（识别关着，点右上角 ▶ 打开）"
        self.bus.post(PetSay(text=f"{text}（{label}）", mood="happy", anim="nod"))
        return True
