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
    美术给的 GIF 不是一次画完的：五套里角色身高从 437px 到 553px，差 26%。
    第一版想当然地"五套共用一个缩放系数"（因为来自同一画布、同一镜头），
    结果是比心那套凭空大一圈、挥手次之 —— 屏幕上就是"人物大小不一致"。

    所以现在**每套按自己的角色身高单独归一**：
        1. 量出每套的"角色典型身高"（各帧包围盒高度的中位数，
           不用最大值：举手、抬腿都会把包围盒撑高）；
        2. 各自求系数，让缩放后的角色身高都等于 TARGET_BODY_H；
        3. 水平锚点用**各自的脚底中心**（不是包围盒中心 —— 挥手时手臂
           伸向一侧，包围盒中心会带着身体歪掉 20px）；
        4. 那 7 张静态立绘按 stand 的角色身高对齐到同一个 TARGET_BODY_H。

    anchor 每套只取一个固定值（不逐帧各自对齐），否则动画里的跳/抬腿
    会被"每帧拉回原位"抹平，播起来就只剩原地抖。

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

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = r"C:/Users/Asus/WorkBuddy/2026-11-06-21-16-05"
ASSETS = os.path.join(HERE, "pet", "assets", "girl")
OUT_DIR = os.path.join(ASSETS, "anim")

#: 7 张静态立绘的**原图**（386x580）。重排是就地覆盖 pet/assets/girl/*.png，
#: 如果每次都拿"上次的结果"当输入，就是在二次、三次重采样，跑几轮就糊了。
#: 所以原图单独留一份，每次都从它一次缩放到目标尺寸。
SRC_SPRITES = os.path.join(HERE, "tools", "sprites_src")

#: 目标画布。比例 0.667 和旧的 386x580 完全一致 —— 窗口宽度、气泡位置、
#: 吸附边距全是按 body_w = body_h * aspect 推的，比例一变全都要重调。
CANVAS_W, CANVAS_H = 260, 390

#: 画布内边距（像素）。留 6px 是给"挥手时指尖贴边"和"阴影"留余量。
PAD = 6

#: 画布内**角色身高**（不是画布高度）。这个数是量出来的：待机那套 437px
#: 的角色，按 0.674 缩放后是 294px —— 也就是现在屏幕上看到的那个大小。
#: 五套动画、七张静态图全部对齐到它，切换时才不会忽大忽小。
#: 改这个值 = 同时改所有素材的角色高度，窗口的 body_h 不用动。
TARGET_BODY_H = 294.0

#: 量"脚底中心"时取底部多高的一条带（占该帧角色身高的比例）。
#: 太小会被单只鞋的像素带偏，太大就把小腿算进来。
FOOT_BAND = 0.06

#: 文件名关键字 -> 动画名。**顺序有意义**：先匹配到的先算。
NAME_RULES: List[Tuple[str, str]] = [
    ("打瞌睡", "idle"),      # 困了才播，不是默认待机（见 pet/ui/anim.py）
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


def frame_profile(im: Image.Image, thr: int = 8):
    """一帧的 (包围盒, 角色身高, 脚底中心 x)。

    脚底中心用"最下面一条带里所有不透明像素的 x 均值"，而不是包围盒中心：
    挥手那套手臂伸向一侧，包围盒中心比真正的站姿偏了 20px，用它当锚点
    整只角色会往另一边歪。
    """
    a = np.asarray(im.convert("RGBA").split()[3]) > thr
    ys, xs = np.nonzero(a)
    if not len(ys):
        return None
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    band = max(3, int((y1 - y0) * FOOT_BAND))
    foot = ys >= (y1 - band)
    foot_x = float(xs[foot].mean()) if foot.any() else (x0 + x1) / 2
    return (x0, y0, x1, y1), y1 - y0, foot_x


def scan(path: str) -> Dict:
    """扫一个 GIF：逐帧量身高和脚底，汇总成一套的构图参数。"""
    im = Image.open(path)
    boxes: List[Tuple[int, int, int, int]] = []
    heights: List[int] = []
    foots: List[float] = []
    durs: List[int] = []
    while True:
        p = frame_profile(im.convert("RGBA"))
        if p is not None:
            boxes.append(p[0])
            heights.append(p[1])
            foots.append(p[2])
        durs.append(int(im.info.get("duration", 80) or 80))
        try:
            im.seek(im.tell() + 1)
        except EOFError:
            break
    if not boxes:
        raise ValueError(f"读不出不透明像素：{path}")
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes))
    return {
        "union": union,
        # 中位数而不是最大值：跳舞那套有一帧抬腿、挥手那套每帧手都在往上，
        # 用最大值等于让"最高的那一帧"决定整套大小，角色会偏小。
        "body_h": float(np.median(heights)),
        "foot_x": float(np.median(foots)),
        "floor_y": float(union[3]),
        "frames": len(durs),
        "durs": durs,
    }


def scale_for(info: Dict, label: str) -> float:
    """这套动画的缩放系数：让角色身高正好等于 TARGET_BODY_H。"""
    u = info["union"]
    uw, uh = u[2] - u[0], u[3] - u[1]
    k = TARGET_BODY_H / max(1.0, info["body_h"])
    # 安全网：宁可小一点也不能出画布（挥手那套手臂最宽）
    cap = min((CANVAS_W - 2 * PAD) / max(1, uw), (CANVAS_H - 2 * PAD) / max(1, uh))
    if k > cap:
        print(f"  [提示] {label} 会被画布裁到，系数 {k:.3f} -> {cap:.3f}")
        k = cap
    return k


def paste_at(dst: Image.Image, src: Image.Image,
             bx: float, by: float, k: float) -> None:
    """把 src 按 k 缩放后，让它的 (bx,by) 落在画布中线上、底边贴 PAD。"""
    w, h = int(round(src.width * k)), int(round(src.height * k))
    if w <= 0 or h <= 0:
        return
    scaled = src.resize((w, h), Image.LANCZOS) if (w, h) != src.size else src
    # by*k 是**缩放后**的坐标，直接减它，不要再减一次图高（第一版就是这么
    # 写错的，整个角色被推出画布，生成的帧全是空白）。
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
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        paste_at(canvas, im.convert("RGBA"), anchor_x, anchor_y, k)
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
    paste_at(canvas, src, anchor_x, anchor_y, k)
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

    # ---------------- 1) 逐套量身高，各自求系数 ----------------
    jobs: List[Tuple[str, str, Dict, float]] = []
    if args.only in ("all", "anim"):
        for n in sorted(os.listdir(args.src)):
            if not n.lower().endswith((".gif", ".webp", ".png")):
                continue
            name = anim_name(n)
            if not name:
                continue
            path = os.path.join(args.src, n)
            info = scan(path)
            k = scale_for(info, name)
            jobs.append((name, path, info, k))
        if not jobs:
            print(f"[错误] {args.src} 里没有认得出的动作文件。"
                  f"关键字：{[k for k, _ in NAME_RULES]}")
            return 1
        jobs.sort(key=lambda j: j[0])
        print(f"目标角色身高 {TARGET_BODY_H:.0f}px（画布 {CANVAS_W}x{CANVAS_H}，"
              f"占 {TARGET_BODY_H / CANVAS_H:.0%}）")
        for name, _, info, k in jobs:
            u = info["union"]
            print(f"  {name:<6} 原身高 {info['body_h']:>5.0f}px -> 系数 {k:.4f}"
                  f"  缩放后 {info['body_h'] * k:>5.0f}px"
                  f"  包围盒 {u[2] - u[0]}x{u[3] - u[1]}")
    else:
        meta_path = os.path.join(args.out, "meta.json")
        with open(meta_path, encoding="utf-8") as f:
            old = json.load(f)
        print(f"只重排立绘：沿用 meta 里的目标角色身高")

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
    meta.pop("_layout", None)

    total = 0
    for name, path, info, k in jobs:
        out_dir = os.path.join(args.out, name)
        os.makedirs(out_dir, exist_ok=True)
        # 先清旧帧：改成 30 帧后如果还留着第 31 帧，播放时会闪一帧旧图
        for old in os.listdir(out_dir):
            if old.endswith(".png"):
                os.remove(os.path.join(out_dir, old))
        res = convert_anim(path, out_dir, k, info["foot_x"], info["floor_y"])
        res["src"] = os.path.basename(path)
        res["k"] = round(k, 6)
        res["body_h"] = round(info["body_h"], 1)
        res["anchor"] = [round(info["foot_x"], 1), round(info["floor_y"], 1)]
        meta[name] = res
        size = sum(os.path.getsize(os.path.join(out_dir, f))
                   for f in os.listdir(out_dir))
        total += size
        print(f"  {name:<6} {res['frames']:>2} 帧 / "
              f"{sum(res['durs']) / 1000:.2f}s / {size / 1024 / 1024:.1f} MB")

    # ---------------- 3) 重排静态立绘 ----------------
    k_static = 0.0
    if args.only in ("all", "static"):
        # 静态 PNG 是另一套分辨率（386x580），要按"角色身高"反推自己的系数。
        # 七张是同一副骨架、同一画布，所以**共用**这一个系数 ——
        # 逐个按身高归一的话，坐姿（本来就矮）会被放大成一尊巨人。
        # 输入一律取 tools/sprites_src/ 里的原图，保证反复跑也不会越来越糊。
        stand = os.path.join(SRC_SPRITES, "stand.png")
        if not os.path.exists(stand):
            stand = os.path.join(ASSETS, "stand.png")
            print(f"[提示] 没有原图 {SRC_SPRITES}，退回就地重排（会二次缩放）")
        if not os.path.exists(stand):
            print("[警告] 找不到 stand.png，跳过静态立绘重排")
        else:
            sb, sh, fx = frame_profile(Image.open(stand).convert("RGBA"))
            k_static = TARGET_BODY_H / max(1, sh)
            print(f"  静态立绘：角色高 {sh}px -> 系数 {k_static:.4f}")
            for name in STATIC_FRAMES:
                src = os.path.join(SRC_SPRITES, f"{name}.png")
                if not os.path.exists(src):
                    src = os.path.join(ASSETS, f"{name}.png")
                if not os.path.exists(src):
                    continue
                convert_static(src, os.path.join(ASSETS, f"{name}.png"),
                               k_static, fx, sb[3])
            print(f"  {len(STATIC_FRAMES)} 张静态立绘已统一构图")

    if jobs:
        meta["_layout"] = {
            "canvas": [CANVAS_W, CANVAS_H],
            "target_body_h": TARGET_BODY_H,
            "k_static": round(k_static, 6),
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
