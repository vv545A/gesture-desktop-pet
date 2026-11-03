# -*- coding: utf-8 -*-
"""
把「桌宠形象」里的整图素材，处理成桌宠能用的透明立绘。

    python tools/prep_sprites.py            # 处理 ..\桌宠形象 → pet/assets/girl

源图长什么样（这决定了下文每一步为什么这么做）
    * 2048x2048，**奶油色不透明底**，右下角有「豆包AI生成」水印
    * 角色外面依次是：奶油底 → **白色贴纸描边** → **深棕线稿** → 角色
    * 脚下有一团**投影椭圆**，且投影**长在描边外面**
    * 打瞌睡的 Zzz、思考的 ~、生气的爆血管记号都画在**背景上**，不带描边

所以流程是：
    1. 先把水印那块涂成底色（位置固定，涂掉最省事）
    2. 把"亮且不饱和"的像素（底 / 投影 / 白描边）都当成**可通行**，
       从四边灌水；真正拦住水的只有**深棕线稿** —— 它比白描边粗得多、
       也连续得多。**别拿白描边当堤坝**：sit.png 那圈白边有一处 1px 的缝，
       水直接灌进身体，整张图变成只剩一条白轮廓（踩过这个坑）
    3. 只保留**最大连通块** -> 角色本体（Zzz 之类背景记号自动消失）
    4. 用所有帧的**并集包围盒**裁同一块区域 -> 切帧时不会跳
    5. 统一缩放 + 留白 -> 输出 RGBA

第 4 步是这里最要紧的一条：每帧各自按内容裁，切帧时角色会左右横跳；
用同一个裁剪框，7 张图的相对位置就天然一致（美术本来就是同一套骨架画的）。
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(os.path.dirname(HERE), "桌宠形象")
DEFAULT_DST = os.path.join(HERE, "pet", "assets", "girl")

# 源文件名（中文） -> 输出帧名。输出名只用 ASCII，免得 zip/跨平台出岔子。
FRAMES = {
    "stand1.png": "stand",
    "开心.png": "happy",
    "打招呼.png": "wave",
    "思考.png": "think",
    "打瞌睡(待机）.png": "sleepy",
    "生气 .png": "angry",
    "sit.png": "sit",
}

# 水印区域（原图坐标）。x1 之后、y1 之后一律涂底 —— 角色脚只到 x≈1450，安全。
WATERMARK = (1580, 1840, 2048, 2048)

# 「可通行」判定：三个通道都够亮。190 这个数是从实测里挑的：
#   底(252,243,232)、投影(~238,214,206)、白描边(255,255,255) 都 >= 190；
#   线稿最暗处只有 60~90。取 175 就会从线稿的抗锯齿边缘漏进角色（实测）。
PASSABLE = 190
# 把"不可通行"（线稿）加粗 1px：抗锯齿过渡可能有 1px 的缝。
# 加粗的代价是切口比线稿外沿多出 1px 淡边 —— 缩到 560 高之后是 0.3px，看不出来。
BARRIER_DILATE = 1


def load_rgb(path: str) -> np.ndarray:
    im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise IOError(path)
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def kill_watermark(rgb: np.ndarray) -> np.ndarray:
    """把右下角水印涂成四周的底色。固定位置，一行搞定，比识别文字稳。"""
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = WATERMARK
    x0, y0 = min(x0, w), min(y0, h)
    x1, y1 = min(x1, w), min(y1, h)
    if x1 <= x0 or y1 <= y0:
        return rgb
    # 取水印正上方一条干净背景当底色，避免和画面整体有细微色差
    ref = rgb[max(0, y0 - 40):y0, x0:x1].reshape(-1, 3)
    bg = np.median(ref, axis=0).astype(np.uint8)
    out = rgb.copy()
    out[y0:y1, x0:x1] = bg
    return out


def bg_color(rgb: np.ndarray) -> np.ndarray:
    """底色：取最外一圈的中位数，比取四角更稳（四角有压缩噪点）。"""
    ring = np.concatenate([rgb[0:8].reshape(-1, 3), rgb[-8:].reshape(-1, 3),
                           rgb[:, 0:8].reshape(-1, 3), rgb[:, -8:].reshape(-1, 3)])
    return np.median(ring, axis=0)


def passable_mask(rgb: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """
    哪些像素算"外面"（灌水可以流过去的）。

    两条规则并起来：
        1. **够亮**：底 / 白描边 / 浅投影，三通道都 >= 190
        2. **等比压暗**：投影靠脚尖那一圈会深到 190 以下，但它有个特征——
           三个通道是**同一个比例**压下来的（实测 k≈0.52~0.84，通道间差 <0.035）。
           肤色的 R/G/B 比例不一致（脸颊更红），所以不会被误判。

    只判"可否通行"、不直接删像素：角色身上的奶油色部件（帽檐、拖鞋绒球）
    同样满足规则 1，但它们被线稿圈着，水根本流不进去。所以这里宁可放宽。
    """
    bright = np.all(rgb >= PASSABLE, axis=2)

    ratio = rgb.astype(np.float32) / np.maximum(bg, 1.0)
    spread = ratio.max(axis=2) - ratio.min(axis=2)
    k = ratio.mean(axis=2)
    shadow = (spread < 0.035) & (k > 0.45) & (k < 0.97)
    return bright | shadow


def outside_mask(rgb: np.ndarray) -> np.ndarray:
    """从四边灌水，得到"线稿之外"的区域（含底色、投影、白描边、背景记号）。"""
    passable = passable_mask(rgb, bg_color(rgb)).astype(np.uint8)
    barrier = (1 - passable).astype(np.uint8)
    if BARRIER_DILATE > 0:
        k = np.ones((BARRIER_DILATE * 2 + 1,) * 2, np.uint8)
        barrier = cv2.dilate(barrier, k)
    canvas = (1 - barrier).astype(np.uint8)                       # 1 = 可通行
    h, w = canvas.shape
    flood = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(canvas, flood, (0, 0), 2)                       # 2 = 已灌到
    return canvas == 2


def character_alpha(rgb: np.ndarray) -> np.ndarray:
    """角色本体的 alpha（0/255）。顺带把背景记号、投影全部剔掉。"""
    outside = outside_mask(rgb)
    alpha = np.where(outside, 0, 255).astype(np.uint8)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(alpha, connectivity=8)
    if n <= 1:
        return alpha
    # 最大块 = 角色；Zzz / ~ / 爆血管这些没连上的小块自动丢掉
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + int(np.argmax(areas))
    return np.where(labels == keep, 255, 0).astype(np.uint8)


def trim_rim(alpha: np.ndarray, px: int) -> np.ndarray:
    """
    把 alpha 往里腐蚀一点，用来削掉切口上的 1px 淡边。

    默认 0：白色贴纸描边已经在灌水那一步整圈去掉了（它属于"可通行"），
    所以不需要再削。留着这个参数是给"想保留贴纸感"的人用的 ——
    把 PASSABLE 调到 246 让白边挡水，再用 --trim 10 把白边削薄。
    """
    if px <= 0:
        return alpha
    k = np.ones((px * 2 + 1,) * 2, np.uint8)
    return cv2.erode(alpha, k)


def bbox(alpha: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(alpha)
    if xs.size == 0:
        raise ValueError("空图")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--dst", default=DEFAULT_DST)
    ap.add_argument("--height", type=int, default=560, help="输出立绘高度（像素）")
    ap.add_argument("--trim", type=int, default=0, help="额外腐蚀几像素（默认 0，白边已整圈去掉）")
    ap.add_argument("--pad", type=int, default=10, help="输出四周留白（像素）")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        print(f"[x] 找不到素材目录：{args.src}")
        return 2
    os.makedirs(args.dst, exist_ok=True)

    loaded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    box = [10 ** 9, 10 ** 9, -1, -1]
    for src_name, frame in FRAMES.items():
        path = os.path.join(args.src, src_name)
        if not os.path.exists(path):
            print(f"[!] 缺少 {src_name}，跳过")
            continue
        rgb = kill_watermark(load_rgb(path))
        alpha = trim_rim(character_alpha(rgb), args.trim)
        # 腐蚀之后边缘要重新按 alpha 收一下，否则会留一圈半透明毛边
        loaded[frame] = (rgb, alpha)
        x0, y0, x1, y1 = bbox(alpha)
        box = [min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)]
        print(f"  {frame:8s} 内容框 {x1 - x0:5d} x {y1 - y0:5d}")

    if not loaded:
        print("[x] 一张都没读到")
        return 1

    cx0, cy0, cx1, cy1 = box
    # 统一裁剪框：宽高比留到目标画布，之后整体缩放；四边各留一点余量，
    # 免得描边正好压在裁剪边上被切掉一像素。
    margin = 6
    cx0, cy0 = max(0, cx0 - margin), max(0, cy0 - margin)
    cx1, cy1 = cx1 + margin, cy1 + margin
    cw, ch = cx1 - cx0, cy1 - cy0
    print(f"\n共用裁剪框 {cw} x {ch}（{cx0},{cy0}) -> ({cx1},{cy1})")

    scale = args.height / ch
    tw, th = int(round(cw * scale)), args.height
    print(f"缩放 {scale:.3f} -> {tw} x {th}，留白 {args.pad}")

    canvas_w = tw + args.pad * 2
    canvas_h = th + args.pad * 2
    for frame, (rgb, alpha) in loaded.items():
        crop_rgb = rgb[cy0:cy1, cx0:cx1]
        crop_a = alpha[cy0:cy1, cx0:cx1]
        rgba = np.dstack([crop_rgb, crop_a])
        small = cv2.resize(rgba, (tw, th), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((canvas_h, canvas_w, 4), np.uint8)
        canvas[args.pad:args.pad + th, args.pad:args.pad + tw] = small
        out = os.path.join(args.dst, f"{frame}.png")
        bgra = cv2.cvtColor(canvas, cv2.COLOR_RGBA2BGRA)
        ok, buf = cv2.imencode(".png", bgra)
        if not ok:
            raise IOError(out)
        buf.tofile(out)
        print(f"  -> {frame}.png  {canvas_w}x{canvas_h}  "
              f"{os.path.getsize(out) // 1024} KB")

    print(f"\n[ok] 输出到 {args.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
