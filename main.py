# -*- coding: utf-8 -*-
"""
手势控制电脑 —— HUD 版主程序（带摄像头预览窗口的调试/演示用界面）

功能（2026-11 按"单手管窗口、双手管音乐"重排）
    单手（操作当前前台窗口）
        ✋ 张开      最大化 / 还原
        ✊ 握拳      最小化
        ✌️ 剪刀手    切换窗口（Alt+Tab）
        ☝️ 食指      贴左/右半屏（左手贴左、右手贴右）
        👍 点赞      置顶 / 取消置顶
        👎 踩        收起所有窗口（Win+D）
        🤏 捏合      只摸桌宠，不动系统
    双手（音乐，两只手必须同一个手势）
        ✋✋ 播放/暂停   ✊✊ 静音   👍👍 音量+   👎👎 音量-   ✌️✌️ 下一首   ☝️☝️ 上一首
    安全
        双手同时捏合保持 1 秒 -> 开启/关闭全部控制（也是紧急停止）

用法
    python main.py                 # 正常模式（会真的发送系统按键）
    python main.py --test          # 只识别不执行，安全调试用
    python main.py --cam 1         # 换摄像头

运行时按键
    q / ESC  退出          d  显示/隐藏骨架
    a  切换是否执行动作（动态开关，随时能停）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, Optional

import cv2
import numpy as np

from hand_tracker import HandTracker, draw_hand, assign_sides, SIDE_STATS
from face_guard import FaceGuard
from camera import open_camera          # 摄像头打开逻辑与桌宠（pet/）共用
import gestures
from gestures import (
    GESTURE_CN,
    Debouncer,
    GestureSmoother,
    MotionGate,
    analyze_two_hands,
    fingers_up,
    pinch_ratio,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(BASE_DIR, "models", "hand_landmarker.task")
DEFAULT_FACE_MODEL = os.path.join(BASE_DIR, "models", "blaze_face_short_range.tflite")
DEFAULT_GESTURE_MODEL = os.path.join(BASE_DIR, "models", "gesture_recognizer.task")

# stdout 重定向到管道时 Python 默认块缓冲，进程异常退出会丢掉全部日志，
# 排查问题时会看到「一片空白」。强制行缓冲，保证 print 立刻可见。
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# --------------------------------------------------------------- 手势 → 动作
# 单手 = 操作窗口（左右手分开写：☝️ 指哪边、窗口就往哪边贴）。
# 动作名见 actions.WINDOW_ACTIONS / actions.ACTIONS，改一行就能换功能。
GESTURE_ACTIONS_RIGHT: Dict[str, str] = {
    "OPEN_PALM": "win_max",        # ✋ 最大化 / 还原
    "FIST": "win_min",             # ✊ 最小化
    "VICTORY": "alt_tab",          # ✌️ 切换窗口
    "POINT": "win_snap_right",     # ☝️ 贴右半屏
    "THUMB_UP": "win_topmost",     # 👍 置顶 / 取消置顶
    "THUMB_DOWN": "win_desktop",   # 👎 收起所有窗口
}

GESTURE_ACTIONS_LEFT: Dict[str, str] = {
    "OPEN_PALM": "win_max",
    "FIST": "win_min",
    "VICTORY": "alt_tab",
    "POINT": "win_snap_left",      # ☝️ 左手贴左半屏
    "THUMB_UP": "win_topmost",
    "THUMB_DOWN": "win_desktop",
}

# 双手 = 音乐。两只手做**同一个**手势才算数（见主循环里的 common 判定）：
# 左右不一样什么都不做，避免"一只手路过镜头"把音乐切了。
PAIR_ACTIONS: Dict[str, str] = {
    "OPEN_PALM": "play_pause",
    "FIST": "mute",
    "THUMB_UP": "vol_up",
    "THUMB_DOWN": "vol_down",
    "VICTORY": "next_track",
    "POINT": "prev_track",
}

# 兼容旧引用
GESTURE_ACTIONS = GESTURE_ACTIONS_RIGHT

# 动作的中文描述，仅用于 HUD 显示
ACTION_CN: Dict[str, str] = {
    "win_max": "最大化/还原",
    "win_min": "最小化",
    "win_topmost": "置顶",
    "win_snap_left": "贴左半屏",
    "win_snap_right": "贴右半屏",
    "win_desktop": "显示桌面",
    "play_pause": "播放/暂停",
    "mute": "静音",
    "vol_up": "音量+",
    "vol_down": "音量-",
    "alt_tab": "切换窗口",
    "next_track": "下一首",
    "prev_track": "上一首",
    "page_down": "下一页",
    "page_up": "上一页",
}

# 窗口动作的结果码 -> HUD 上显示的字（同一个手势可能做两件相反的事）
WIN_RES_CN: Dict[str, str] = {
    "max": "最大化", "restore": "还原", "min": "最小化",
    "top": "置顶", "untop": "取消置顶",
    "left": "贴左半屏", "right": "贴右半屏", "desktop": "显示桌面",
}


# --------------------------------------------------------------- 中文文字缓存
class TextCache:
    """PIL 预渲染中文并缓存，避免每帧重复排版（cv2.putText 画不了中文）。"""

    FONTS = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]

    def __init__(self, size: int = 18) -> None:
        from PIL import ImageFont

        self.font = None
        for p in self.FONTS:
            if os.path.exists(p):
                try:
                    self.font = ImageFont.truetype(p, size)
                    break
                except Exception:
                    continue
        self.cache: Dict[tuple, np.ndarray] = {}
        self._pad = 4

    @property
    def ok(self) -> bool:
        return self.font is not None

    def get(self, text: str, color=(255, 255, 255), bg=None) -> Optional[np.ndarray]:
        if not self.ok:
            return None
        key = (text, color, bg)
        if key in self.cache:
            return self.cache[key]

        from PIL import Image, ImageDraw

        tmp = Image.new("RGBA", (8, 8))
        bb = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=self.font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        bgc = (0, 0, 0, 0) if bg is None else (bg[2], bg[1], bg[0], 255)
        img = Image.new("RGBA", (tw + self._pad * 2, th + self._pad * 2), bgc)
        ImageDraw.Draw(img).text(
            (self._pad - bb[0], self._pad - bb[1]), text, font=self.font,
            fill=(color[2], color[1], color[0], 255),
        )
        arr = np.array(img)
        self.cache[key] = arr
        return arr


def blend(frame: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    """RGBA 贴图 alpha 混合到 BGR 帧，自动裁剪越界。"""
    ph, pw = patch.shape[:2]
    H, W = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + pw), min(H, y + ph)
    if x0 >= x1 or y0 >= y1:
        return
    sub = patch[y0 - y: y1 - y, x0 - x: x1 - x]
    a = sub[:, :, 3:4].astype(np.float32) / 255.0
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (sub[:, :, :3].astype(np.float32) * a + roi * (1 - a)).astype(np.uint8)


def put_cn(frame, text, x, y, tc: TextCache, color=(255, 255, 255), bg=None) -> None:
    p = tc.get(text, color, bg)
    if p is not None:
        blend(frame, p, x, y)
    else:
        cv2.putText(frame, text.encode("ascii", "ignore").decode(), (x, y + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)


def build_legend(tc: "TextCache") -> Optional[np.ndarray]:
    """
    把「手势 → 功能」图例一次性渲染成整张 RGBA。

    为什么整张渲染：如果每帧逐行画十几段中文，PIL 排版会吃掉好几毫秒；
    合成一张图后每帧只做一次 alpha 贴图，开销可以忽略。
    """
    if not tc.ok:
        return None

    from PIL import Image, ImageDraw

    rows = [
        ("张开手掌  单手", "最大化 / 还原"),
        ("握拳  单手", "最小化"),
        ("剪刀手  单手", "切换窗口"),
        ("食指  右手", "窗口贴右半屏"),
        ("食指  左手", "窗口贴左半屏"),
        ("点赞  单手", "窗口置顶 / 取消"),
        ("踩  单手", "收起所有窗口"),
        ("捏合  单手", "只摸桌宠，不动系统"),
        ("双手 都张开", "播放 / 暂停"),
        ("双手 都握拳", "静音"),
        ("双手 都点赞", "音量 +"),
        ("双手 都踩", "音量 -"),
        ("双手 都剪刀手", "下一首"),
        ("双手 都食指", "上一首"),
        ("双手 同时捏合保持 1 秒", "总开关（紧急停止）"),
        ("握拳要收拇指", "伸在外面算点赞"),
    ]
    font = tc.font
    pad, gap, line_h = 14, 20, 26
    title = "手势 → 功能（单手窗口 · 双手音乐）"

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    left_w = max(probe.textlength(a, font=font) for a, _ in rows)
    right_w = max(probe.textlength(b, font=font) for _, b in rows)
    title_w = probe.textlength(title, font=font)

    W = int(pad * 2 + max(left_w + gap + right_w, title_w))
    H = int(pad * 2 + line_h * (len(rows) + 1) + 6)

    img = Image.new("RGBA", (W, H), (22, 24, 26, 218))
    dr = ImageDraw.Draw(img)
    dr.text((pad, pad), title, font=font, fill=(0, 230, 160, 255))

    y = pad + line_h + 2
    dr.line([(pad, y - 7), (W - pad, y - 7)], fill=(95, 100, 105, 255), width=1)
    for a, b in rows:
        dr.text((pad, y), a, font=font, fill=(228, 230, 235, 255))
        dr.text((pad + left_w + gap, y), b, font=font, fill=(255, 200, 80, 255))
        y += line_h
    return np.array(img)


# --------------------------------------------------------------- 摄像头
# open_camera() 已抽到 camera.py（桌宠也要用同一套，见该文件注释）


# --------------------------------------------------------------- 参数
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="手势控制电脑（MediaPipe + Windows API）")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--test", action="store_true", help="只识别不执行（安全调试）")
    p.add_argument("--debug", action="store_true", help="显示五指状态与捏合比，便于校准阈值")
    p.add_argument("--toggle-hold", type=float, default=1.0,
                   help="双手同时捏合保持多少秒 = 总开关（默认 1.0，越小事越容易误触）")
    p.add_argument("--hold", type=float, default=0.5,
                   help="手势需悬停多少秒才触发（默认 0.5）")
    p.add_argument("--cooldown", type=float, default=0.6, help="同一手势冷却秒数")
    p.add_argument("--grace", type=float, default=0.35,
                   help="短暂丢检宽容秒数（默认 0.35：丢失不超过这么久不清零悬停计时）")
    p.add_argument("--pair-hold", type=float, default=0.4,
                   help="双手同手势要保持多少秒才触发音乐（默认 0.4）")
    p.add_argument("--still-speed", type=float, default=0.6,
                   help="手移动多快就不算「稳住」（单位：掌长/秒，默认 0.6）。"
                        "调小手更难触发、但更不容易误触；调大更容易触发")
    p.add_argument("--no-still-gate", action="store_true",
                   help="关掉「手要稳住才计数」（手在动时也累计悬停时间）")
    p.add_argument("--trace", action="store_true",
                   help="统计并打印「为什么没触发」，排查手势被忽略")
    p.add_argument("--min-detect", type=float, default=0.5)
    p.add_argument("--pinch", type=float, default=0.45,
                   help="捏合判定的距离阈值（等比空间，默认 0.45）。"
                        "还要同时满足「拇指更靠食指而不是中指」，见 --debug 的 p= / a=")
    p.add_argument("--thumb", type=float, default=1.12,
                   help="竖拇指判定：拇指尖离食指 PIP 至少多少倍掌长（默认 1.12）。"
                        "调小更容易触发点赞、但握拳可能被误判")
    p.add_argument("--gesture-model", default=DEFAULT_GESTURE_MODEL,
                   help="官方手势分类模型路径（默认 models/gesture_recognizer.task）")
    p.add_argument("--no-gesture-model", action="store_true",
                   help="不用手势模型，退回几何规则（模型文件缺失时会自动退回）")
    p.add_argument("--hands", choices=["auto", "position", "model"], default="auto",
                   help="左右手判定方式：auto 位置为先、模型交叉验证（默认）；"
                        "position 只看画面位置；model 只用 MediaPipe 标签（双手交叉时用）")
    p.add_argument("--swap-hands", action="store_true",
                   help="左右判反了就加这个，把它整体翻过来")
    p.add_argument("--no-face-guard", action="store_true",
                   help="关闭人脸否决（默认开启：防止把脸识别成手势）")
    p.add_argument("--face-model", default=DEFAULT_FACE_MODEL)
    p.add_argument("--face-conf", type=float, default=0.5,
                   help="人脸检测置信度（默认 0.4，低一点更保险）")
    p.add_argument("--no-window", action="store_true", help="不开窗口（仅自检）")
    p.add_argument("--snapshot", default=None, help="自检：跑若干帧后存图退出")
    p.add_argument("--frames", type=int, default=40, help="自检跑多少帧（配 --snapshot）")
    p.add_argument("--profile", action="store_true", help="打印分段耗时，用于排查性能")
    return p.parse_args()


# --------------------------------------------------------------- 主流程
def main() -> int:
    args = parse_args()
    gestures.PINCH_RATIO = args.pinch      # --pinch 允许运行时覆盖阈值
    gestures.THUMB_AWAY = args.thumb       # --thumb 同上

    if not os.path.exists(args.model):
        print(f"[error] 模型不存在: {args.model}")
        return 1

    import actions  # 延迟导入，--test 模式下也安全

    print(f"[model] 加载 {args.model}")
    t0 = time.perf_counter()
    tracker = HandTracker(args.model, num_hands=2, min_detect=args.min_detect,
                          hand_mode=args.hands, swap_hands=args.swap_hands,
                          gesture_model=None if args.no_gesture_model else args.gesture_model)
    if getattr(tracker, "use_model", False):
        print("[gesture] 使用官方手势分类模型（握拳/点赞等由模型判定）")
    else:
        print("[gesture] 使用几何规则判定手势（--no-gesture-model 或模型缺失）")
    print(f"[model] 就绪 {time.perf_counter() - t0:.2f}s")

    # 人脸否决：手部模型会把脸当手（分数还很高），靠人脸检测器兜底
    face_guard = None
    if not args.no_face_guard:
        face_guard = FaceGuard(args.face_model, min_conf=args.face_conf)
        if face_guard.enabled:
            print("[face] 人脸否决已启用（防止把脸识别成手势）")

    cap = open_camera(args.cam, args.width, args.height)
    if cap is None:
        print(f"[error] 打不开摄像头 {args.cam}")
        return 1

    tc = TextCache(18)
    if not tc.ok:
        print("[warn] 没找到中文字体，HUD 会退化成英文")

    legend = build_legend(tc)   # 启动时渲染一次，之后每帧只贴图
    # 提示文字按实际参数生成（改 --hold 后 HUD 也要跟着变，
    # 写死字符串的话改完参数界面上还在显示旧值，很容易看错）
    hint = (f"手势悬停 {args.hold:g}s 触发   |   "
            f"单手=窗口 · 双手=音乐 · 双手捏合 {args.toggle_hold:g}s 开关")

    # 状态机
    deb_single = Debouncer(args.hold, args.cooldown, grace=args.grace)
    smoother = GestureSmoother(window=5, need=3)   # 多数表决，压掉模型零星翻车
    gate = None if args.no_still_gate else MotionGate(max_speed=args.still_speed)
    # 双手（音乐）：配对也要表决 + 悬停确认。
    # 以前双手没有任何滤波，一帧翻车就把累计状态清掉 —— 这是"双手操作做不出来"
    # 的真正原因，不只是手感问题。
    deb_pair = Debouncer(args.pair_hold, 0.7, grace=args.grace)
    pair_smoother = GestureSmoother(window=5, need=3)
    pinch_since: Optional[float] = None      # 双手捏合开始计时（总开关）

    enabled = True
    do_action = not args.test
    last_toggle = -1e9
    face_dropped_at = -1e9      # 最近一次否决人脸的时刻（HUD 提示用）
    show_skeleton = True
    fps_hist = []
    slow_reads = 0                    # 连续读帧超时计数

    # --trace：把「为什么没触发」的每一环都数一遍
    trace = {"frames": 0, "only1": 0, "two": 0, "none": 0,
             "unknown": 0, "vetoed": 0, "fired": 0}

    win = "Hand Control"
    ts = 0
    frame_idx = 0
    last_action = ""
    last_action_at = 0.0
    start = time.perf_counter()
    prof = {"det": 0.0, "n": 0}

    if args.test:
        print("[run] 测试模式：只识别，不发送任何系统按键")

    try:
        while True:
            _tf0 = time.perf_counter()
            ok, frame = cap.read()
            _read_ms = (time.perf_counter() - _tf0) * 1000.0
            if not ok:
                print("[warn] 读帧失败")
                break
            if _read_ms > 3000:
                slow_reads += 1
                print(f"[warn] 读帧耗时 {_read_ms:.0f} ms，摄像头可能被其它程序占用")
                if slow_reads >= 3:
                    print("[warn] 连续读取超时，退出")
                    break

            # 镜像：不翻转的话你举右手、屏幕却显示在左边，操作完全反直觉
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            ts += 33
            now = time.perf_counter()

            _td = time.perf_counter()
            hands = tracker.detect(frame, ts)
            _det_ms = (time.perf_counter() - _td) * 1000.0
            if args.profile:
                prof["det"] += _det_ms
                prof["n"] += 1

            # 关键一步：先确认这些「手」不是脸，再交给手势识别
            if face_guard is not None:
                _tf = time.perf_counter()
                hands, dropped = face_guard.filter(frame, hands)
                if args.profile:
                    prof["face"] = prof.get("face", 0.0) + (time.perf_counter() - _tf) * 1000.0
                if dropped:
                    face_dropped_at = now
                    if args.trace:
                        trace["vetoed"] += dropped
                    # 少了一只手，左右关系变了，用同一套规则重排
                    assign_sides(hands, args.hands, args.swap_hands)

            st = analyze_two_hands(hands)
            if args.trace:
                trace["frames"] += 1
                trace["none" if st.count == 0 else
                      ("only1" if st.count == 1 else "two")] += 1

            # ---------------- 安全：双手同时捏合保持 -> 开关控制 ----------------
            # 必须两只手一起捏住并**保持** --toggle-hold 秒：这是紧急停止，
            # 不能挥手就触发。
            # 为什么不做成 ✊✋✊✋ 序列了：双手握拳/张开现在本身就是音乐的
            # 静音和播放/暂停，序列会被这两个动作吃掉一半，永远走不完。
            both_pinch = (st.count == 2 and st.left_gesture == "PINCH"
                          and st.right_gesture == "PINCH")
            toggle_progress = 0.0
            if both_pinch:
                if pinch_since is None:
                    pinch_since = now
                toggle_progress = min(1.0, (now - pinch_since)
                                      / max(args.toggle_hold, 1e-6))
                if toggle_progress >= 1.0 and now - last_toggle > 2.0:
                    enabled = not enabled
                    last_toggle = now
                    pinch_since = None
                    deb_single.reset()
                    deb_pair.reset()
                    print(f"[{'ON ' if enabled else 'OFF'}] 手势控制"
                          f"{'已开启' if enabled else '已关闭'}")
            else:
                pinch_since = None

            action_now = ""
            moving_now = False
            pair_now = None

            if enabled:
                if st.count == 1:
                    # ---------------- 单手：操作窗口 ----------------
                    deb_pair.reset()      # 只剩一只手，双手配对作废
                    pair_smoother.reset()
                    hand = st.left or st.right
                    g = st.left_gesture if st.left is not None else st.right_gesture
                    if g == "UNKNOWN":
                        if args.trace:
                            trace["unknown"] += 1
                        g = None   # 未识别不算手势，否则悬停计时会被它一直占着
                    g = smoother.update(g)    # 5 帧多数表决
                    # 手相对画面还在动 -> 冻结计时，稳住之后才继续累计
                    still = True
                    if gate is not None:
                        still = gate.update(now, hand)
                    moving_now = not still
                    stable, fired = deb_single.update(g, now, moving=not still)
                    if fired and stable:
                        if args.trace:
                            trace["fired"] += 1
                        # 按手的左右取不同的动作表（☝️ 左手贴左、右手贴右）
                        table = (GESTURE_ACTIONS_LEFT if hand.side == "left"
                                 else GESTURE_ACTIONS_RIGHT)
                        act = table.get(stable)
                        if act:
                            label = ACTION_CN.get(act, act)
                            if not do_action:
                                action_now = "（测试）" + label
                            elif act in actions.WINDOW_ACTIONS:
                                # 窗口动作返回"做成了什么"，而不是布尔：
                                # 同一个 ✋ 可能是最大化也可能是还原
                                res = actions.run_window_action(act)
                                action_now = WIN_RES_CN.get(res, "没有可操作的窗口")
                            else:
                                actions.run_action(act)
                                action_now = label
                elif st.count == 2:
                    # ---------------- 双手：音乐 ----------------
                    deb_single.update(None, now)   # 抑制单手指令
                    if gate is not None:
                        gate.reset()               # 双手模式下不判静止

                    if both_pinch:
                        # 捏合是总开关专用（上面正在计时），不参与音乐配对
                        deb_pair.update(None, now)
                        pair_smoother.reset()
                    else:
                        # 只认"两只手同一个手势"：左右不一致就当作没有意图，
                        # 一只手路过画面不该把正在放的音乐切了
                        l_g, r_g = st.left_gesture, st.right_gesture
                        common = (l_g if (l_g == r_g and l_g and l_g != "UNKNOWN")
                                  else None)
                        pair_now = pair_smoother.update(common)
                        stable_p, fired_p = deb_pair.update(pair_now, now)
                        if fired_p and stable_p:
                            act = PAIR_ACTIONS.get(stable_p)
                            if act:
                                if do_action:
                                    actions.run_action(act)
                                action_now = ACTION_CN.get(act, act)
                else:
                    deb_single.update(None, now)
                    deb_pair.reset()
                    pair_smoother.reset()
                    smoother.reset()      # 手离开画面就清空表决缓冲，免得旧票影响下次
                    if gate is not None:
                        gate.reset()

                if action_now:
                    last_action = action_now
                    last_action_at = now
            else:
                deb_single.update(None, now)
                deb_pair.update(None, now)

            # ---------------- 绘制 ----------------
            vis = frame
            # 进度条：单手看悬停计时，双手看配对确认 / 捏合保持
            bar = 0.0
            if enabled:
                if st.count == 2:
                    bar = toggle_progress if both_pinch else deb_pair.progress
                elif st.count == 1 and deb_single.stable is None:
                    bar = deb_single.progress
            if show_skeleton:
                for hd in hands:
                    draw_hand(vis, hd, (0, 220, 120))

            # 每只手旁边标手势
            for idx_h, hd in enumerate(hands):
                g = GESTURE_CN.get(
                    (st.left_gesture if hd is st.left else st.right_gesture) or "UNKNOWN", "")
                px, py = hd.px(0, w, h)
                if g:
                    side_cn = "左手" if hd.side == "left" else ("右手" if hd.side == "right" else "手")
                    put_cn(vis, f"{side_cn}·{g}", px - 20, py + 18, tc,
                           (255, 200, 0) if hd.side == "left" else (0, 255, 160))
                if args.debug:
                    # 校准用：模型给的手势、五指状态、捏合判据
                    mg = (f"{hd.model_gesture or '-'} {hd.model_score:.2f}"
                          if getattr(hd, "model_gesture", "") else "-")
                    cv2.putText(vis, f"{mg} | {fingers_up(hd)} p={pinch_ratio(hd):.2f}",
                                (max(0, px - 20), py + 44), cv2.FONT_HERSHEY_SIMPLEX,
                                0.45, (0, 255, 255), 1, cv2.LINE_AA)
                    # 左右判定：模型说什么、最终用了谁
                    cv2.putText(vis, f"{hd.mp_side or '-'} {hd.mp_score:.2f} -> {hd.side_source}",
                                (max(0, px - 20), max(12, py - 10)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.4, (180, 180, 255), 1, cv2.LINE_AA)

                # 悬停进度条：举着手时能看见还差多少才触发
                if idx_h == 0 and enabled and bar > 0:
                    bw = 76
                    bx = max(0, min(w - bw, px - bw // 2))
                    by = max(0, min(h - 10, py + 58))
                    cv2.rectangle(vis, (bx, by), (bx + bw, by + 7), (58, 58, 62), -1)
                    cv2.rectangle(vis, (bx, by),
                                  (bx + int(bw * bar), by + 7),
                                  (0, 225, 120), -1)
                    if moving_now:
                        # 进度条变灰的另一种提示：手在动，计时是冻结的
                        put_cn(vis, "手在动·稳住才计时", bx - 10, by + 12, tc, (0, 170, 255))

            # 左上状态条
            fps = 1.0 / max(1e-6, now - (fps_hist[-1] if fps_hist else now))
            fps_hist.append(now)
            if len(fps_hist) > 30:
                fps_hist.pop(0)
            fps = (len(fps_hist) - 1) / max(1e-6, fps_hist[-1] - fps_hist[0]) if len(fps_hist) > 2 else 0

            overlay = vis.copy()
            cv2.rectangle(overlay, (0, 0), (w, 74), (28, 28, 28), -1)
            cv2.addWeighted(overlay, 0.6, vis, 0.4, 0, vis)
            # 静态中文走 PIL 缓存；动态数字用 cv2 画。
            # 若把 FPS 塞进中文串，缓存每帧都 miss，等于每帧重跑一次 PIL 排版。
            put_cn(vis, "控制中" if enabled else "已停止", 10, 6, tc,
                   (0, 255, 120) if enabled else (60, 60, 255))
            cv2.putText(vis, f"FPS {fps:4.1f}   hands {st.count}", (108, 27),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
            if now - face_dropped_at < 1.2:
                put_cn(vis, "忽略疑似人脸", 310, 4, tc, (0, 170, 255))
            if args.trace:
                cv2.putText(vis, f"raw={deb_single._cand or '-'} hold={deb_single.progress * 100:3.0f}%",
                            (360, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 255, 120), 1, cv2.LINE_AA)
            put_cn(vis, hint, 10, 38, tc, (200, 200, 200))
            if enabled and both_pinch and toggle_progress > 0:
                put_cn(vis, f"总开关 {toggle_progress * 100:3.0f}%"
                            f"（保持 {args.toggle_hold:g}s 切换）", 10, 64, tc, (0, 170, 255))
            elif st.count == 2 and pair_now:
                put_cn(vis, f"双手 · {GESTURE_CN.get(pair_now, pair_now)}", 10, 64, tc,
                       (120, 255, 120))

            # 右侧「手势 -> 功能」图例
            if legend is not None:
                lh, lw = legend.shape[:2]
                blend(vis, legend, w - lw - 12, 84)

            if last_action and now - last_action_at < 1.2:
                put_cn(vis, "→ " + last_action, w - 220, 10, tc, (0, 220, 255))

            # 底部提示
            put_cn(vis, "q 退出   a 动作开关   d 骨架   --test 只识别不执行",
                   10, h - 34, tc, (170, 170, 170))

            # 自检模式跑够帧数就退出（--no-window 也适用，避免无限空转）
            if (args.snapshot or args.no_window) and frame_idx >= args.frames:
                if args.snapshot:
                    cv2.imwrite(args.snapshot, vis)
                    print(f"[snapshot] {args.snapshot}")
                break

            if args.no_window:
                if args.profile:
                    _tot = (time.perf_counter() - _tf0) * 1000.0
                    print(f"  #{frame_idx:02d} read={_read_ms:6.0f}ms det={_det_ms:5.1f}ms"
                          f" total={_tot:6.0f}ms")
                frame_idx += 1
                continue

            cv2.imshow(win, vis)
            frame_idx += 1
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("a"):
                enabled = not enabled
                last_toggle = now
                print(f"[{'ON ' if enabled else 'OFF'}] 手势控制{'已开启' if enabled else '已关闭'}")
            elif key == ord("d"):
                show_skeleton = not show_skeleton
    finally:
        # 计时放在这里，才能把「退出清理」从帧率里剥离出去
        _loop_end = time.perf_counter()
        _tc0 = time.perf_counter()
        cap.release()
        _tc1 = time.perf_counter()
        tracker.close()
        _tc2 = time.perf_counter()
        if args.profile:
            print(f"[cleanup] cap.release={( _tc1 - _tc0) * 1000:.0f}ms"
                  f"  tracker.close={(_tc2 - _tc1) * 1000:.0f}ms")
        if not args.no_window:
            cv2.destroyAllWindows()

    dur = _loop_end - start
    print(f"[done] {frame_idx} 帧 / {dur:.1f}s = {frame_idx / max(dur, 1e-6):.1f} FPS"
          f"   平均 {dur / max(frame_idx, 1) * 1000:.0f} ms/帧")

    if args.trace:
        f = max(1, trace["frames"])
        print("\n[trace] 手势为什么被忽略 —— 各环节计数")
        print(f"  总帧数                  {trace['frames']}")
        print(f"  没看到手（丢检/被否决） {trace['none']:5d}  {trace['none'] / f * 100:5.1f}%")
        print(f"  只看到 1 只手           {trace['only1']:5d}  {trace['only1'] / f * 100:5.1f}%")
        print(f"  同时看到 2 只手         {trace['two']:5d}  {trace['two'] / f * 100:5.1f}%  ← 这时单手指令被抑制")
        print(f"  手在但判成 UNKNOWN      {trace['unknown']:5d}  {trace['unknown'] / max(trace['only1'], 1) * 100:5.1f}%  (占单手帧)")
        print(f"  被「人脸否决」丢掉      {trace['vetoed']:5d}  ")
        print(f"  悬停成功触发            {trace['fired']:5d}  ")
        print(f"  悬停被清零              {deb_single.n_gap_reset:5d}  次（丢失超过 {args.grace}s 才算松手）")
        print(f"  短暂丢失但保持住        {deb_single.n_gap_tolerated:5d}  帧")
        print(f"  候选手势切换            {deb_single.n_restart:5d}  次")
        print(f"  因手在动而冻结          {deb_single.n_motion_paused:5d}  帧"
              f"（阈值 {args.still_speed:g} 掌长/秒）")

        # 左右手判定：模型和位置这两个信号的一致率
        n_mp = SIDE_STATS["model+pos"] + SIDE_STATS["model"]
        n_pos = SIDE_STATS["position"]
        print(f"  左右判定  位置 {n_pos} 帧 / 模型 {SIDE_STATS['model']} 帧 / 两者一致 {SIDE_STATS['model+pos']} 帧")
        if n_mp + SIDE_STATS["disagree"]:
            rate = SIDE_STATS["disagree"] / max(n_mp + SIDE_STATS["disagree"], 1) * 100
            print(f"            模型与位置冲突 {SIDE_STATS['disagree']} 次（{rate:.0f}%）")
            if rate > 80 and SIDE_STATS["disagree"] > 20:
                print("            → 冲突率极高，MediaPipe 的 handedness 与镜像约定相反："
                      "\n              想用模型判定请加 --swap-hands --hands model")

        print("\n  判读：")
        if trace["two"] > 0.3 * f:
            print("  → 经常同时看到两只手。如果那是脸被当成了手，单手指令会一直被抑制。")
        if trace["vetoed"] > 0.05 * f:
            print("  → 人脸否决战得比较频繁：手可能压在脸附近，试试 --no-face-guard 对比一次。")
        if trace["unknown"] > 0.3 * max(trace["only1"], 1):
            print("  → 大量 UNKNOWN：手在但五指判定不出来，开 --debug 看 fingers_up 和捏合比。")
        if deb_single.n_gap_reset > 3:
            print(f"  → 悬停被清零 {deb_single.n_gap_reset} 次：手可能频繁离开画面，"
                  f"或者按得太轻；可加大 --grace 或降低 --hold。")
        if trace["fired"] == 0 and trace["only1"] > 0:
            print(f"  → 一次都没触发：多半是悬停时间不够，先试 --hold 0.3。")
    if args.profile and prof["n"]:
        # prof 里累加的是毫秒，dur 是秒，这里要统一单位再算占比
        print(f"[profile] detect 平均 {prof['det'] / prof['n']:.1f} ms/帧"
              f"，占循环时间 {prof['det'] / max(dur * 1000.0, 1e-9) * 100:.0f}%")
        if "face" in prof:
            print(f"[profile] 人脸否决 {prof['face'] / prof['n']:.2f} ms/帧")
    return 0


if __name__ == "__main__":
    _rc = main()
    # mediapipe / cv2 的原生收尾在长跑后有概率挂住：日志已经打完 [done]，
    # 进程却几十秒到几十分钟不退出，一直占着 100~300 MB。
    # 我们自己该释放的（摄像头、模型、背景线程）都在 main 里做完了，
    # 这里直接硬退出，保证不留残影。
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(_rc)
