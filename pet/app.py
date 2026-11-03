# -*- coding: utf-8 -*-
"""
应用装配 —— 把感知、判定、执行、界面四层接起来。

这里刻意写得像"说明书"：任何一个功能想加到哪一层，看这个文件就够了。

    PerceptionWorker ──post──▶ EventBus ──drain(QTimer)──▶ PetWindow / Tray
            │                     ▲
            └──▶ IntentRouter ────┘   (执行完发 ActionDone)

三种运行方式
    python -m pet              正常：桌宠 + 摄像头
    python -m pet --demo       无摄像头演示：手是假的，动画是真的（改外观时用）
    python -m pet --snapshot x.png          离屏渲染全部立绘帧，做美术评审
    python -m pet --snapshot-actions y.png  离屏渲染动作表（每个动作抽 5 帧）
    后两条都不需要摄像头和真实桌面，换素材 / 调动作时靠它们一眼验收。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

from .core.bus import EventBus
from .core.config import DEFAULT_CONFIG, Config
from .core.control import ControlCenter
from .core.engine import GestureEngine
from .core.events import (ActionDone, ControlToggled, GestureFired,
                          HandsSeen, PerceptionFailed, PerceptionReady, PetSay)
from .intent import mapping
from .intent.router import IntentRouter


# ------------------------------------------------------------------ 命令行
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pet", description="隔空手势桌宠（gesture desktop pet）")
    p.add_argument("--config", default=None, help="配置文件路径（默认 pet_config.json）")
    p.add_argument("--cam", type=int, default=None, help="摄像头序号")
    p.add_argument("--scale", dest="pet_scale", type=float, default=None, help="桌宠大小倍数")
    p.add_argument("--follow", choices=["hand", "cursor", "window", "corner"],
                   default=None,
                   help="跟随方式：手掌 / 鼠标 / 吸附到前台窗口 / 守在角落")
    p.add_argument("--gain", dest="follow_gain", type=float, default=None,
                   help="手掌跟随增益（默认 1.6，越大越灵敏）")
    p.add_argument("--home", dest="home_corner", choices=["br", "bl", "tr", "tl"],
                   default=None, help="待机时待在哪个角")
    p.add_argument("--hold", type=float, default=None, help="手势悬停秒数（默认 0.5）")
    p.add_argument("--click-through", dest="click_through", action="store_true",
                   default=None, help="启动即开启鼠标穿透")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                   help="只识别不执行（安全调试）")
    p.add_argument("--demo", action="store_true", help="无摄像头演示：模拟手势与手的移动")
    p.add_argument("--headless", action="store_true", help="不开界面，只把事件打到控制台")
    p.add_argument("--snapshot", metavar="PNG", default=None,
                   help="离屏渲染表情表到 PNG（不需要摄像头）")
    p.add_argument("--snapshot-actions", metavar="PNG", default=None,
                   dest="snapshot_actions",
                   help="离屏渲染动作表到 PNG：每个动作抽 5 帧并排（不需要摄像头）")
    p.add_argument("--list-intents", action="store_true", help="打印手势对照表后退出")
    p.add_argument("--verbose", action="store_true", help="打印所有事件（排查用）")
    return p.parse_args(argv)


# ------------------------------------------------------------------ 离屏渲染
def _snapshot_actions(cfg: Config, path: str) -> int:
    """
    把 10 个动作各抽 5 个瞬间，排成一张"动作表"。

    为什么需要这张图：动作是**随时间变化**的，单帧截图看不出对不对 ——
    跳起来那一帧和落地压扁那一帧都是"变了形的立绘"，只有并排摆出来
    才能判断"这像不像跳"。改完关键帧跑一条命令，一眼看全套。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication

    from .ui import anim
    from .ui.paint import PetPose, draw_shadow, ui_font
    from .ui.sprite import SpriteSet

    _app = QApplication.instance() or QApplication([])   # noqa: F841
    sp = SpriteSet.from_config(cfg)
    if sp is None:
        print("[snapshot] 当前配置走的是矢量版（sprite 为空），动作表只对贴图版有意义")
        return 1

    steps = (0.08, 0.30, 0.52, 0.74, 0.94)
    label_w, cell_w, cell_h = 176, 134, 182
    names = sorted(anim.CLIPS)

    pm = QPixmap(label_w + cell_w * len(steps), cell_h * len(names))
    pm.fill(QColor(246, 248, 250))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    body_h = 132.0
    body_w = body_h * sp.aspect

    for row, name in enumerate(names):
        clip = anim.CLIPS[name]
        top = cell_h * row
        # 隔行浅底，眼睛更容易横着读一行
        if row % 2 == 0:
            p.fillRect(0, top, pm.width(), cell_h, QColor(238, 242, 245))

        p.setPen(QColor(56, 74, 84))
        p.setFont(ui_font(10, bold=True))
        p.drawText(14, top + int(cell_h * 0.34), cell_w, 20, 0x80, name)
        p.setPen(QColor(112, 132, 142))
        p.setFont(ui_font(9))
        p.drawText(14, top + int(cell_h * 0.34) + 22, cell_w, 20, 0x80,
                   f"{anim.CLIP_LABEL.get(name, '')} {clip.dur:.2f}s")

        player = anim.AnimPlayer()
        player.play(name, 0.0)
        for i, s in enumerate(steps):
            motion = player.sample(s * clip.dur)
            frame = (motion.frame if motion is not None and motion.frame
                     else anim.frame_for_mood("idle"))
            cx = label_w + cell_w * (i + 0.5)
            cy = top + cell_h * 0.48
            pose = PetPose.for_mood("idle")
            # 影子和真实窗口里一样随跳跃缩放 —— 影子是"钉在地上"的
            sh = 1.0
            if motion is not None:
                sh = max(0.55, min(1.15, 1.0 + motion.dy * 2.2))
            draw_shadow(p, cx, cy, body_w * sh, body_h * sh)
            sp.draw(p, cx, cy, body_w, body_h, pose, frame=frame, motion=motion)
            if i:
                p.setPen(QColor(210, 218, 224))
                p.drawLine(label_w + cell_w * i, top + 12,
                           label_w + cell_w * i, top + cell_h - 12)
    p.end()
    pm.save(path)
    print(f"[snapshot] {path}  ({pm.width()}x{pm.height()})  "
          f"{len(names)} 个动作 × {len(steps)} 帧")
    return 0


def _snapshot(cfg: Config, path: str) -> int:
    """
    把几种表情并排渲染成一张图。

    这是"没有摄像头也能验证改动"的关键一环：改配色、改眼睛、改嘴，
    跑一条命令就能看到结果，不用举着手对着屏幕反复试。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    from PyQt6.QtWidgets import QApplication

    from .ui.paint import PetPose, draw_aura, draw_glow, draw_shadow, draw_pet, ui_font
    from .ui.sprite import SpriteSet

    app = QApplication.instance() or QApplication([])   # noqa: F841  (Qt 需要)
    sp = SpriteSet.from_config(cfg)

    # 贴图版和矢量版的"能看的东西"不一样：
    #   矢量版看表情（眼睛/嘴是程序画的）
    #   贴图版看**帧**：把 assets 目录里每一帧都摆出来 + 一个悬停进度环
    # 这样换素材之后，一条命令就能确认"7 张图都加载到了、都对得齐"。
    if sp is not None:
        cells = [(f, 0, 0.0) for f in sp.frame_names]
        cells.append(("hover", 0, 0.72))
    else:
        cells = [("idle", 0, 0.0), ("listen", 0, 0.65), ("happy", 0, 0.0),
                 ("surprised", 0, 0.0), ("shy", 0, 0.0), ("sleepy", 0, 0.0)]

    cell_w, cell_h = 210, 268
    pm = QPixmap(cell_w * len(cells), cell_h)
    pm.fill(QColor(244, 247, 249))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for i, (name, facing, aura) in enumerate(cells):
        cx, cy = cell_w * (i + 0.5), cell_h * 0.50
        pose = PetPose.for_mood("listen" if name == "hover" else name)
        pose.aura = aura
        if sp is not None:
            body_h = 158.0
            body_w = body_h * sp.aspect
            draw_shadow(p, cx, cy, body_w, body_h)
            draw_glow(p, cx, cy, body_w, body_h, pose.glow)
            sp.draw(p, cx, cy, body_w, body_h, pose,
                    frame="" if name == "hover" else name, facing=facing)
            draw_aura(p, cx, cy, body_w, body_h, aura)
        else:
            draw_pet(p, cx, cy, 118, 158, pose)
        p.setPen(QColor(90, 110, 120))
        p.setFont(ui_font(9))
        if sp is None:
            label = name
        elif name == "hover":
            label = "hover 悬停环"
        else:
            label = name
        p.drawText(cell_w * i, int(cell_h * 0.92), cell_w, 24, 0x84, label)
    p.end()
    pm.save(path)
    print(f"[snapshot] {path}  ({pm.width()}x{pm.height()})")
    return 0


# ------------------------------------------------------------------ 演示源
class DemoSource(threading.Thread):
    """
    假的手 —— 不做任何识别，只按剧本往总线发事件。

    用途：① 没摄像头也能看动画；② 改界面的反馈循环从 30 秒缩到 2 秒；
    ③ 给朋友演示时不怕现场光线/摄像头出问题。
    """

    GESTURES = ["OPEN_PALM", "VICTORY", "POINT", "FIST", "THUMB_UP", "THUMB_DOWN"]

    def __init__(self, bus: EventBus) -> None:
        super().__init__(daemon=True, name="demo")
        self.bus = bus
        self._stop = threading.Event()
        self.i = 0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.bus.post(PerceptionReady(message="演示模式（没有摄像头）"))
        self.bus.post(PetSay(text="演示模式：看我怎么跟着手跑～", mood="happy"))
        t0 = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic() - t0
            x = 0.5 + 0.34 * math.sin(t * 0.55)
            y = 0.5 + 0.18 * math.sin(t * 0.9 + 0.7)
            phase = t % 4.5
            idx = int(t // 4.5)
            g = self.GESTURES[idx % len(self.GESTURES)]
            # 每四轮来一次"双手"：这样不开摄像头也能看到双手=音乐那条链路
            two_hand = (idx % 4 == 3)
            side = "both" if two_hand else "right"
            progress = min(1.0, max(0.0, (phase - 1.2) / 1.6))
            gesture = None if (two_hand or progress <= 0) else g

            self.bus.post(HandsSeen(count=2 if two_hand else 1, point=(x, y),
                                    gesture=gesture, side=side, progress=progress,
                                    moving=progress <= 0, speed=0.2))
            if phase >= 2.8 and not getattr(self, "_fired_this", False):
                self._fired_this = True
                intent = mapping.resolve(side, g)
                if intent:
                    self.bus.post(GestureFired(gesture=g, side=side,
                                               intent=intent.key, label=intent.label))
                    self.bus.post(ActionDone(intent=intent.key, label=intent.label,
                                             ok=True, detail="demo",
                                             bubble=intent.bubble, mood=intent.mood))
            if phase < 2.8:
                self._fired_this = False
            time.sleep(1 / 20)


# ------------------------------------------------------------------ 无界面
def _headless(cfg: Config, bus: EventBus, engine: GestureEngine,
              router: IntentRouter, demo: bool) -> int:
    """控制台模式：适合在没有桌面/远程终端里验证链路是否通。"""
    from .perception.worker import PerceptionWorker

    control = ControlCenter(bus, engine, cfg)
    bus.subscribe(GestureFired, lambda e: print(f"[gesture] {e.side} {e.gesture} -> {e.label}"))
    bus.subscribe(ActionDone, lambda e: print(
        f"[action ] {e.label} {'ok' if e.ok else 'FAIL ' + e.detail}"))
    bus.subscribe(ControlToggled, lambda e: print(f"[toggle ] {'ON' if e.enabled else 'OFF'}"))
    bus.subscribe(PerceptionFailed, lambda e: print(f"[error  ] {e.message}"))
    bus.subscribe(PerceptionReady, lambda e: print(f"[ready  ] {e.message}"))

    thread = DemoSource(bus) if demo else PerceptionWorker(cfg, bus, router, engine, control)
    if isinstance(thread, PerceptionWorker):
        control.attach_worker(thread)
    thread.start()
    print("[run] Ctrl+C 退出")
    try:
        while thread.is_alive():
            bus.drain()
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[run] 收到中断")
    finally:
        if hasattr(thread, "stop"):
            thread.stop()
        if isinstance(thread, PerceptionWorker):
            thread.join(timeout=2.0)
        print(f"[stats] {engine.stats()}")
    return 0


# ------------------------------------------------------------------ 图形界面
def _gui(cfg: Config, bus: EventBus, engine: GestureEngine,
         router: IntentRouter, demo: bool) -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from .perception.worker import PerceptionWorker
    from .ui.tray import PetTray
    from .ui.window import PetWindow

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)      # 关掉宠物窗口 != 退出程序
    app.setApplicationName(cfg.pet_name)

    # 所有"开关"的唯一入口：桌宠右上角的 ×、面板、托盘、双手捏合都走它
    control = ControlCenter(bus, engine, cfg)

    window = PetWindow(bus, cfg, control)
    window.start()

    worker = None if demo else PerceptionWorker(cfg, bus, router, engine, control)
    demo_src = DemoSource(bus) if demo else None

    def quit_all() -> None:
        if worker is not None:
            worker.stop()
        if demo_src is not None:
            demo_src.stop()
        app.quit()

    control.attach_worker(worker)
    control.attach_quit(quit_all)

    tray = PetTray(app, window, bus, engine, quit_all)   # noqa: F841  (保持引用)
    tray.show()

    # 总线要到主线程来消化：16ms 一次，和渲染同频
    pump = QTimer()
    pump.timeout.connect(bus.drain)
    pump.start(16)

    if worker is not None:
        worker.start()
    if demo_src is not None:
        demo_src.start()

    if cfg.dry_run:
        bus.post(PetSay(text="安全模式：只说不动手", mood="calm"))

    rc = app.exec()

    # 收拾干净再走：不 join 的话进程会带着摄像头的句柄退出
    if worker is not None:
        worker.stop()
        worker.join(timeout=2.0)
    if demo_src is not None:
        demo_src.stop()
    print(f"[stats] {engine.stats()}")
    return rc


# ------------------------------------------------------------------ 入口
def main(argv=None) -> int:
    args = parse_args(argv)

    cfg = Config.load(args.config or DEFAULT_CONFIG)
    cfg.override(cam=args.cam, pet_scale=args.pet_scale, hold=args.hold,
                 follow=args.follow, follow_gain=args.follow_gain,
                 home_corner=args.home_corner, click_through=args.click_through,
                 dry_run=args.dry_run)
    if args.demo:
        cfg.dry_run = True          # 演示绝不真的发按键

    # 用户在"手势对照表"页面里改过的绑定，必须赶在任何一次 resolve 之前装上 ——
    # 否则第一下手势用的还是默认表，看起来就像"改了没生效"。
    mapping.apply_bindings(cfg.bindings)

    if args.list_intents:
        print(mapping.describe_table())
        return 0

    if args.snapshot:
        return _snapshot(cfg, args.snapshot)

    if args.snapshot_actions:
        return _snapshot_actions(cfg, args.snapshot_actions)

    bus = EventBus(verbose=args.verbose)
    engine = GestureEngine(cfg)
    router = IntentRouter(bus, dry_run=cfg.dry_run)

    if args.headless:
        return _headless(cfg, bus, engine, router, demo=args.demo)
    return _gui(cfg, bus, engine, router, demo=args.demo)
