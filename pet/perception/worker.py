# -*- coding: utf-8 -*-
"""
感知线程 —— 摄像头、模型、判定，全程在后台跑。

线程职责边界（很重要）
    只做三件事：读帧 → 检测 → 喂给引擎。
    检测到的事件往总线上扔，**自己不碰界面、不决定显示什么**。
    桌宠卡住（比如用户拖着窗口、系统弹了个大对话框）时，感知照跑，
    事件在总线里排队，界面恢复后一次性消化——不会因为界面慢而漏掉手势。

退出
    QThread 那套会要求所有对象在 Qt 线程里创建，MediaPipe 的模型不适合
    那样搬来搬去。这里用 threading.Thread + Event 标志，退出时 join 一下即可；
    tracker.close() 内部已经是"丢后台线程、最多等 0.5 秒"（见 hand_tracker.py）。
"""

from __future__ import annotations

import threading
import time

import cv2

from camera import open_camera
from face_guard import FaceGuard
from hand_tracker import HandTracker

from ..core.bus import EventBus
from ..core.config import GESTURE_MODEL, HAND_MODEL, FACE_MODEL, Config
from ..core.engine import GestureEngine
from ..core.events import HandsSeen, PerceptionFailed, PerceptionReady, ControlToggled
from ..intent.router import IntentRouter


class PerceptionWorker(threading.Thread):
    def __init__(self, cfg: Config, bus: EventBus, router: IntentRouter,
                 engine: GestureEngine) -> None:
        super().__init__(daemon=True, name="perception")
        self.cfg = cfg
        self.bus = bus
        self.router = router
        self.engine = engine
        self._stop = threading.Event()
        self.fps = 0.0
        self.n_frames = 0

    # ---------------------------------------------------------------- 生命周期
    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        tracker = None
        cap = None
        try:
            print("[perception] 加载模型…")
            t0 = time.perf_counter()
            tracker = HandTracker(
                HAND_MODEL, num_hands=2, min_detect=0.5,
                hand_mode=self.cfg.hands, swap_hands=self.cfg.swap_hands,
                gesture_model=GESTURE_MODEL if self.cfg.use_gesture_model else None,
            )
            print(f"[perception] 就绪 {time.perf_counter() - t0:.2f}s"
                  f"（{'手势模型' if tracker.use_model else '几何规则'}）")

            face_guard = None
            if self.cfg.face_guard:
                face_guard = FaceGuard(FACE_MODEL, min_conf=0.5)
                if not face_guard.enabled:
                    face_guard = None

            cap = open_camera(self.cfg.cam, self.cfg.width, self.cfg.height)
            if cap is None:
                self.bus.post(PerceptionFailed(
                    message=f"打不开摄像头 {self.cfg.cam}（被其它程序占用？）"))
                return

            self.bus.post(PerceptionReady(message="摄像头就绪"))
            self._loop(cap, tracker, face_guard)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.bus.post(PerceptionFailed(message=f"感知线程异常：{e}"))
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if tracker is not None:
                tracker.close()
            print("[perception] 已停止")

    # ---------------------------------------------------------------- 主循环
    def _loop(self, cap, tracker: HandTracker, face_guard: FaceGuard | None) -> None:
        ts = 0
        last_ui = 0.0
        t_start = time.perf_counter()
        frames_at_last = 0
        slow = 0

        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                print("[perception] 读帧失败")
                break

            # 镜像：手往右，屏幕上的手也往右。桌宠跟随靠的就是这个坐标系，
            # 少了这一步，桌宠会往反方向跑（最典型的"手感不对"）。
            frame = cv2.flip(frame, 1)
            ts += 33

            hands = tracker.detect(frame, ts)
            if face_guard is not None:
                hands, _dropped = face_guard.filter(frame, hands)   # 先剔掉"脸被当成手"

            now = time.perf_counter()
            out = self.engine.update(hands, now)
            self.n_frames += 1

            # 结果 -> 事件。执行放在这里（不是界面线程），避免动画掉帧。
            if out.toggled is not None:
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
            if now - t_start >= 2.0:
                self.fps = (self.n_frames - frames_at_last) / (now - t_start)
                frames_at_last = self.n_frames
                t_start = now
                if self.fps < 5:
                    slow += 1
                    if slow >= 3:
                        print(f"[perception] 帧率只有 {self.fps:.1f}，摄像头可能被占用")
                        slow = 0
