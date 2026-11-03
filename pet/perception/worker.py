# -*- coding: utf-8 -*-
"""
感知线程 —— 摄像头、模型、判定，全程在后台跑。

线程职责边界（很重要）
    只做三件事：读帧 → 检测 → 喂给引擎。
    检测到的事件往总线上扔，**自己不碰界面、不决定显示什么**。
    桌宠卡住（比如用户拖着窗口、系统弹了个大对话框）时，感知照跑，
    事件在总线里排队，界面恢复后一次性消化——不会因为界面慢而漏掉手势。

暂停（2026-11 加）
    "关闭手势识别"必须真的把摄像头**释放**掉。只把引擎置为 disabled 的话，
    摄像头还在被占用、指示灯还亮着，用户会以为没关掉；别的程序也用不了这个
    摄像头。所以 set_active(False) 会 release() 摄像头，恢复时重新打开。
    模型（MediaPipe）不销毁：重新加载要 1~2 秒，而 release/重开只要几十毫秒。

退出
    QThread 那套会要求所有对象在 Qt 线程里创建，MediaPipe 的模型不适合
    那样搬来搬去。这里用 threading.Thread + Event 标志，退出时 join 一下即可；
    tracker.close() 内部已经是"丢后台线程、最多等 0.5 秒"（见 hand_tracker.py）。
"""

from __future__ import annotations

import logging
import threading
import time

import cv2

from camera import open_camera
from face_guard import FaceGuard
from hand_tracker import HandTracker

from ..core.bus import EventBus
from ..core.config import (EMOTION_MODEL, FACE_MODEL, GESTURE_MODEL,
                           HAND_MODEL, Config)
from ..core.engine import GestureEngine
from ..core.events import (ControlToggled, EmotionChanged, HandsSeen,
                           PerceptionFailed, PerceptionReady, PetGame,
                           PresenceChanged)
from ..game.rps import CHOICES, LABEL, RPSGame
from ..intent.router import IntentRouter
from .emotion import EmotionDetector
from .presence import PresenceDetector

log = logging.getLogger(__name__)


class PerceptionWorker(threading.Thread):
    def __init__(self, cfg: Config, bus: EventBus, router: IntentRouter,
                 engine: GestureEngine, control=None) -> None:
        super().__init__(daemon=True, name="perception")
        self.cfg = cfg
        self.bus = bus
        self.router = router
        self.engine = engine
        self.control = control
        self._stop = threading.Event()
        self._active = threading.Event()
        self._active.set()
        self.fps = 0.0
        self.n_frames = 0

        # ---- 小游戏 ----
        # 游戏状态住在感知线程：它每帧要 tick 一次，而判定的输入（手势）
        # 也在这条线程上产生。放在界面线程反而要跨线程传手势。
        self.game = RPSGame()
        self.engine.set_game_hook(self._on_game_gesture)
        self._game_sig = None          # 上一次广播过的状态签名（用来节流）
        self._game_pending = False     # 待启动：由主循环用统一时钟启动
        self._game_was_active = False  # 上一帧是否在游戏里（用来发"结束"那一次）

        # ---- 在场检测 / 情绪感知 ----
        # 检测器**常驻创建**，是否真的调用由 cfg 决定（每帧读一次）。
        # 为什么不按 cfg 决定创建：右键菜单能随时开关，若关闭时不创建对象，
        # 打开开关之后就没有东西可用了，只能重启程序——那就不叫开关了。
        self.presence = None
        det = PresenceDetector(
            FACE_MODEL, min_conf=0.5,
            interval=self.cfg.presence_interval,
            away_after=self.cfg.away_after,
        )
        self.presence = det if det.enabled else None

        self.emotion = None
        emo = EmotionDetector(EMOTION_MODEL, interval=self.cfg.emotion_interval)
        self.emotion = emo if emo.enabled else None

    # ---------------------------------------------------------------- 生命周期
    def stop(self) -> None:
        self._stop.set()
        self._active.set()          # 别卡在暂停等待里

    def set_active(self, on: bool) -> None:
        """开/关识别。关 = 释放摄像头（指示灯灭、别的程序能用）。"""
        if on:
            self._active.set()
        else:
            self._active.clear()

    @property
    def active(self) -> bool:
        return self._active.is_set()

    # ---------------------------------------------------------------- 小游戏
    def set_game(self, on: bool) -> bool:
        """
        进入/退出石头剪刀布。

        注意 `on=True` 只是**置一个待启动标志**，真正 start() 在主循环里做。
        因为游戏时钟必须和引擎用同一个时间源（主循环用 perf_counter），
        而这里是被 Qt 主线程调用的——直接取时间会混进两种时钟，
        倒数会算出负数或者几百万秒。
        """
        if on:
            self._game_pending = True
        else:
            self.game.stop()
            self._game_pending = False
            self._game_sig = None
        return bool(on)

    def _on_game_gesture(self, gesture: str, now: float) -> bool:
        """
        游戏模式下拦截手势。返回 True = 这一下归游戏，不再走正常映射。

        只拦三种"出拳"手势。不拦的话，比个 ✊ 会一边出石头一边让它睡觉——
        同一次动作两个结果，看起来就是 bug。而 ☝️ 👍 🤏 和游戏不冲突，
        没必要一起关掉，留着还能随时开浏览器或退出跟随。
        """
        if not self.game.active:
            return False
        if gesture not in CHOICES:
            return False
        self.game.play(gesture, now)
        return True

    # ---------------------------------------------------------------- 主流程
    def run(self) -> None:
        tracker = None
        try:
            log.info("加载模型…")
            t0 = time.perf_counter()
            tracker = HandTracker(
                HAND_MODEL, num_hands=2, min_detect=0.5,
                hand_mode=self.cfg.hands, swap_hands=self.cfg.swap_hands,
                gesture_model=GESTURE_MODEL if self.cfg.use_gesture_model else None,
            )
            log.info("就绪 %.2fs（%s）", time.perf_counter() - t0,
                     "手势模型" if tracker.use_model else "几何规则")

            face_guard = None
            if self.cfg.face_guard:
                face_guard = FaceGuard(FACE_MODEL, min_conf=0.5)
                if not face_guard.enabled:
                    face_guard = None

            self._loop(tracker, face_guard)
        except Exception as e:
            # exception 会带上完整栈：感知线程的崩溃是最难复现的一类
            log.exception("感知线程异常")
            self.bus.post(PerceptionFailed(message=f"感知线程异常：{e}"))
        finally:
            if tracker is not None:
                tracker.close()
            if self.presence is not None:
                self.presence.close()
            if self.emotion is not None:
                self.emotion.close()
            log.info("已停止")

    # ---------------------------------------------------------------- 主循环
    def _loop(self, tracker: HandTracker, face_guard) -> None:
        cap = None
        t0 = time.perf_counter()
        last_ui = 0.0
        frames_at_last = 0
        t_stat = t0
        slow = 0
        first_ready = True
        failed_shown = False

        while not self._stop.is_set():
            # ---- 暂停中：把摄像头还回去，等恢复 ----
            if not self._active.is_set():
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    log.info("已释放摄像头（手势识别关闭）")
                last_ui = 0.0
                time.sleep(0.1)
                continue

            # ---- 摄像头懒打开：启动、以及每次从暂停恢复 ----
            if cap is None:
                cap = open_camera(self.cfg.cam, self.cfg.width, self.cfg.height)
                if cap is None:
                    if not failed_shown:
                        failed_shown = True
                        self.bus.post(PerceptionFailed(
                            message=f"打不开摄像头 {self.cfg.cam}（被其它程序占用？）"))
                    time.sleep(1.0)          # 隔一秒重试，插上摄像头就能自己好
                    continue
                log.info("摄像头已打开")
                if first_ready:
                    first_ready = False
                    self.bus.post(PerceptionReady(message="摄像头就绪"))
                t0 = time.perf_counter()
                t_stat = t0
                frames_at_last = self.n_frames
                failed_shown = False

            ok, frame = cap.read()
            if not ok:
                log.warning("读帧失败，重连摄像头")
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                time.sleep(0.3)
                continue

            # 镜像：手往右，屏幕上的手也往右。桌宠跟随靠的就是这个坐标系，
            # 少了这一步，桌宠会往反方向跑（最典型的"手感不对"）。
            frame = cv2.flip(frame, 1)
            # 时间戳用**真实经过的毫秒**，不是帧计数：暂停/掉帧之后帧计数会
            # 落后于墙上时间，MediaPipe 的 VIDEO 模式对时间戳很敏感。
            ts = int((time.perf_counter() - t0) * 1000)

            hands = tracker.detect(frame, ts)
            if face_guard is not None:
                hands, _dropped = face_guard.filter(frame, hands)   # 先剔掉"脸被当成手"

            now = time.perf_counter()

            # 在场检测：内部自己降到 2Hz，只在"人来了/人走了"那一刻返回非 None
            if self.cfg.presence and self.presence is not None:
                changed = self.presence.update(frame, now, has_hand=bool(hands))
                if changed is not None:
                    self.bus.post(PresenceChanged(present=changed))

            # 情绪感知：内部 1Hz，只在情绪真的变了时返回
            if self.cfg.emotion and self.emotion is not None:
                emo = self.emotion.update(frame, now)
                if emo is not None:
                    self.bus.post(EmotionChanged(emotion=emo[0], strength=emo[1]))

            # 待启动的游戏在这里起：和引擎共用同一个时钟（perf_counter）
            if self._game_pending:
                self._game_pending = False
                self.game.start(now)
                self._game_sig = None

            out = self.engine.update(hands, now)
            self.n_frames += 1

            # 小游戏：每帧推进一次，但只在快照真的变了才广播——
            # 倒数每秒变一格、结果每局变一次，30FPS 全发纯属浪费带宽。
            snap = self.game.tick(now)
            sig = (snap.phase, snap.count, snap.round)
            if sig != self._game_sig:
                self._game_sig = sig
                if snap.active or self._game_was_active:
                    r = snap.round
                    self.bus.post(PetGame(
                        phase=snap.phase, count=snap.count,
                        player=LABEL.get(r.player, r.player) if r else "",
                        pet=LABEL.get(r.pet, r.pet) if r else "",
                        result=r.result if r else -1,
                        score=snap.score, active=snap.active,
                    ))
                self._game_was_active = snap.active

            # 结果 -> 事件。执行放在这里（不是界面线程），避免动画掉帧。
            if out.toggled is not None:
                if self.control is not None:
                    # 走统一入口：同时停/开摄像头，不然手势关掉了摄像头还亮着
                    self.control.set_enabled(out.toggled)
                else:
                    self.bus.post(ControlToggled(enabled=out.toggled))
            for fired in out.fired:
                self.router.dispatch_fired(fired)
            for kind, value in out.continuous:
                self.router.dispatch_continuous(kind, value)

            # 状态事件节流到 20Hz：它只服务动画，30FPS 全发纯属浪费，
            # 而且 Qt 排队重绘也会跟着变密。
            if now - last_ui >= 0.05:
                last_ui = now
                self.bus.post(HandsSeen(
                    count=out.state.count, point=out.point,
                    gesture=out.gesture, side=out.side,
                    progress=out.progress, moving=out.moving,
                    speed=out.speed,
                ))

            # 帧率（每 2 秒算一次，避免数字乱跳）
            if now - t_stat >= 2.0:
                self.fps = (self.n_frames - frames_at_last) / (now - t_stat)
                frames_at_last = self.n_frames
                t_stat = now
                if self.fps < 5:
                    slow += 1
                    if slow >= 3:
                        log.warning("帧率只有 %.1f，摄像头可能被占用", self.fps)
                        slow = 0

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
