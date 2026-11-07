# -*- coding: utf-8 -*-
"""
把美术给的 GIF 动作转成程序吃的 PNG 帧序列，并统一构图。

为什么不在运行时直接放 GIF（QMovie）：
    1. QMovie 只管"自己播自己的"，拿不到"第 N 帧"——桌宠要把动画叠在
       跳跃/挤压这些程序化动作上，必须能按时间取样。
    2. GIF 只有 1-bit 透明，QMovie 解码出来的边缘没有中间 alpha，
       缩到 170px 时锯齿比预缩放的 PNG 明显。
    3. 预转一次，启动时零解码成本。

**统一构图**（这段别删，是全部尺寸问题的根）
    美术给的 GIF 里，角色只占画布 61% 高，而旧的 PNG 立绘占 95%。
    直接替换的后果是：换表情那一刻角色会突然缩小三分之一、位置还偏。
    所以这里做两件事：
        1. 5 套动画全部用**同一个缩放系数**（同一画布、同一镜头，不能各缩各的，
           否则挥手那套会莫名变大、被拎起那套会莫名变小）；
        2. 那 7 张静态 PNG 也按同一个"角色身高"重排，跟着一起换构图。
    缩放系数由**最大那套**（挥手的手臂最宽）决定，保证极端姿势不被裁。

用法：
    python tools/import_pet_gifs.py                  # 用默认源目录
    python tools/import_pet_gifs.py --src D:/xx     # 换目录
    python tools/import_pet_gifs.py --only anim     # 只转动画，不动静态立绘
    python tools/import_pet_gifs.py --dry            # 只算参数、不写文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = r"C:/Users/Asus/WorkBuddy/2026-11-06-21-16-05"
ASSETS = os.path.join(HERE, "pet", "assets", "girl")
OUT_DIR = os.path.join(ASSETS, "anim")

#: 目标画布。比例 0.667 和旧的 386x580 完全一致 —— 窗口宽度、气泡位置、
#: 吸附边距全是按 body_w = body_h * aspect 推的，比例一变全都要重调。
#: 尺寸取 260x390 是为了在"清晰"和"体积"之间折中：
#: 待机角色约 281px 高，200% 缩放的屏幕上实际显示 161px，够用且不糊。
CANVAS_W, CANVAS_H = 260, 390

#: 画布内边距（像素）。留 6px 是给"挥手时指尖贴边"和"阴影"留余量。
PAD = 6

#: 文件名关键字 -> 动画名。**顺序有意义**：先匹配到的先算。
NAME_RULES: List[Tuple[str, str]] = [
    ("打瞌睡", "idle"),      # 待机：站着打瞌睡，一直循环
    ("拎起", "drag"),        # 鼠标拖着走的时候
    ("跳舞", "dance"),       # ✌️ 跳舞
    ("比心", "heart"),       # 点它一下（比心）
    ("挥手", "wave"),        # 打招呼
]

#: 静态立绘的帧名 -> 情绪（和 anim.MOOD_FRAME 的键一致）
STATIC_FRAMES = ("stand", "happy", "wave", "sleepy", "sit",
                 "think", "angry")


def anim_name(filename: str) -> str:
    for key, name in NAME_RULES:
        if key in filename:
            return name
    return ""


def alpha_bbox(im: Image.Image, thr: int = 8) -> Tuple[int, int, int, int]:
    """不透明区域的包围盒。GIF 的 alpha 是二值的，阈值只要不把边缘当背景。"""
    a = im.convert("RGBA").split()[3].point(lambda v: 255 if v > thr else 0)
    return a.getbbox() or (0, 0, im.width, im.height)


def union_bbox(boxes: List[Tuple[int, int, int, int]]) -> Tuple[int, int, int, int]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def paste_centered(dst: Image.Image, src: Image.Image,
                   bx: float, by: float, k: float) -> None:
    """把 src 按 k 缩放后，让它的 (bx,by) 落在画布中线上、底边贴 PAD。

    "中线上"用**包围盒的中线**而不是图片中线：角色的头可能歪、手可能伸向一侧，
    图片中线并不代表"它站在哪儿"。用包围盒中线，五套动作的站位才是统一的。
    """
    w, h = int(round(src.width * k)), int(round(src.height * k))
    if w <= 0 or h <= 0:
        return
    scaled = src.resize((w, h), Image.LANCZOS) if (w, h) != src.size else src
    # 让源图里的锚点 (bx, by) 落到"画布中线 / 底边往上一格内边距"的位置。
    # 注意 by*k 是**缩放后**的坐标，直接减它，不要再减一次图高（第一版就是
    # 这么写错的，整个角色被推出画布，生成的帧全是空白）。
    dx = CANVAS_W / 2 - bx * k
    dy = (CANVAS_H - PAD) - by * k
    dst.alpha_composite(scaled, (int(round(dx)), int(round(dy))))


def convert_anim(path: str, out_dir: str, k: float,
                 anchor_x: float, anchor_y: float) -> Dict:
    """转一个 GIF。返回 {"frames": n, "durs": [...], "size": [w,h]}。"""
    im = Image.open(path)
    durs: List[int] = []
    i = 0
    while True:
        frame = im.convert("RGBA")
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        paste_centered(canvas, frame, anchor_x, anchor_y, k)
        canvas.save(os.path.join(out_dir, f"{i:02d}.png"), optimize=True)
        durs.append(int(im.info.get("duration", 80) or 80))
        i += 1
        try:
            im.seek(im.tell() + 1)
        except EOFError:
            break
    return {"frames": i, "durs": durs, "size": [CANVAS_W, CANVAS_H]}


def convert_static(path: str, out_path: str, k: float,
                   anchor_x: float, anchor_y: float) -> None:
    src = Image.open(path).convert("RGBA")
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    paste_centered(canvas, src, anchor_x, anchor_y, k)
    canvas.save(out_path, optimize=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GIF 动作 -> PNG 帧序列（统一构图）")
    ap.add_argument("--src", default=DEFAULT_SRC, help="GIF 所在目录")
    ap.add_argument("--out", default=OUT_DIR, help="动画帧输出目录")
    ap.add_argument("--only", choices=("all", "anim", "static"), default="all",
                    help="all=动画+静态立绘，anim=只转动画，static=只重排立绘")
    ap.add_argument("--dry", action="store_true", help="只算参数不写文件")
    args = ap.parse_args(argv)

    if args.only in ("all", "anim") and not os.path.isdir(args.src):
        print(f"[错误] 源目录不存在：{args.src}")
        return 1

    # ---------------- 1) 扫动画，求"所有动作的联合包围盒" ----------------
    jobs: List[Tuple[str, str]] = []
    boxes: List[Tuple[int, int, int, int]] = []
    idle_box: Optional[Tuple[int, int, int, int]] = None
    idle_h = 0.0
    if args.only in ("all", "anim"):
        for n in sorted(os.listdir(args.src)):
            low = n.lower()
            if not low.endswith((".gif", ".webp", ".png")):
                continue
            name = anim_name(n)
            if not name:
                continue
            im = Image.open(os.path.join(args.src, n))
            fbox: List[Tuple[int, int, int, int]] = []
            i = 0
            while True:
                fbox.append(alpha_bbox(im.convert("RGBA")))
                i += 1
                try:
                    im.seek(im.tell() + 1)
                except EOFError:
                    break
            ub = union_bbox(fbox)
            jobs.append((name, os.path.join(args.src, n)))
            boxes.append(ub)
            if name == "idle":
                idle_box, idle_h = ub, ub[3] - ub[1]
        if not jobs:
            print(f"[错误] {args.src} 里没有认得出的动作文件。"
                  f"关键字：{[k for k, _ in NAME_RULES]}")
            return 1
        union = union_bbox(boxes)
    else:
        # 只重排立绘时没有 GIF，用旧 meta 里的参数
        meta_path = os.path.join(args.out, "meta.json")
        with open(meta_path, encoding="utf-8") as f:
            old = json.load(f)
        union = tuple(old["_layout"]["union"])
        idle_box = tuple(old["_layout"]["idle"])
        idle_h = idle_box[3] - idle_box[1]

    union_w, union_h = union[2] - union[0], union[3] - union[1]
    k = min((CANVAS_W - 2 * PAD) / union_w, (CANVAS_H - 2 * PAD) / union_h)
    # 锚点取**待机**那套的包围盒：待机是待得最久的姿势，以它为准最不容易显歪。
    # 用联合包围盒当锚点的话，会被"被拎起"那套的悬空高度带偏。
    anchor_x = (idle_box[0] + idle_box[2]) / 2
    anchor_y = idle_box[3]

    print(f"联合包围盒 {union_w}x{union_h}  待机角色高 {idle_h:.0f}px")
    print(f"缩放系数 {k:.4f} -> 待机角色在画布里高 {idle_h * k:.0f}px "
          f"(占 {idle_h * k / CANVAS_H:.0%})")
    print(f"锚点 ({anchor_x:.0f}, {anchor_y:.0f}) -> 画布中线 "
          f"x={CANVAS_W / 2:.0f}, 底边 y={CANVAS_H - PAD}")
    if args.dry:
        return 0

    # ---------------- 2) 转动画 ----------------
    meta_path = os.path.join(args.out, "meta.json")
    meta: Dict[str, Dict] = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    total = 0
    for name, path in jobs:
        out_dir = os.path.join(args.out, name)
        os.makedirs(out_dir, exist_ok=True)
        # 先清旧帧：改成 30 帧后如果还留着第 31 帧，播放时会闪一帧旧图
        for old in os.listdir(out_dir):
            if old.endswith(".png"):
                os.remove(os.path.join(out_dir, old))
        info = convert_anim(path, out_dir, k, anchor_x, anchor_y)
        info["src"] = os.path.basename(path)
        meta[name] = info
        size = sum(os.path.getsize(os.path.join(out_dir, f))
                   for f in os.listdir(out_dir))
        total += size
        print(f"  {name:<6} {info['frames']:>2} 帧 / "
              f"{sum(info['durs']) / 1000:.2f}s / {size / 1024 / 1024:.1f} MB")

    # ---------------- 3) 重排静态立绘 ----------------
    k_static = 0.0
    if args.only in ("all", "static"):
        # 静态 PNG 是另一套分辨率（386x580），要按"角色身高"反推自己的系数，
        # 这样两类素材的角色在屏幕上才一样高。
        stand = os.path.join(ASSETS, "stand.png")
        if not os.path.exists(stand):
            print("[警告] 找不到 stand.png，跳过静态立绘重排")
        else:
            stand_box = alpha_bbox(Image.open(stand).convert("RGBA"))
            stand_h = stand_box[3] - stand_box[1]
            k_static = (idle_h * k) / max(stand_h, 1)
            sx = (stand_box[0] + stand_box[2]) / 2
            sy = stand_box[3]
            print(f"  静态立绘：角色高 {stand_h}px -> 系数 {k_static:.4f}")
            for name in STATIC_FRAMES:
                p = os.path.join(ASSETS, f"{name}.png")
                if not os.path.exists(p):
                    continue
                convert_static(p, p, k_static, sx, sy)
            print(f"  {len(STATIC_FRAMES)} 张静态立绘已统一构图")

    meta["_layout"] = {
        "canvas": [CANVAS_W, CANVAS_H],
        "k_anim": round(k, 6),
        "k_static": round(k_static, 6),
        "union": list(union),
        "idle": list(idle_box),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")

    count = sum(v.get("frames", 0) for v in meta.values() if isinstance(v, dict))
    print(f"\n{len(jobs)} 套动作共 {count} 帧，{total / 1024 / 1024:.1f} MB -> "
          f"{os.path.relpath(args.out, HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
