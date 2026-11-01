# -*- coding: utf-8 -*-
"""
打发布包：生成别人解压就能用的 zip。

    python make_release.py            # 输出到 dist/hand-control-portable-<日期>.zip

不打包的东西（都有意排除）：
    .venv/          350 MB，和本机 Python 绑定，别人必须自己建
    __pycache__/    字节码，没用
    .workbuddy/     会话记忆，属于开发环境
    calibration/    本机采的手势数据
    dist/           输出目录
"""
import os
import sys
import zipfile
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist")

INCLUDES = [
    "main.py", "gestures.py", "hand_tracker.py", "actions.py", "face_guard.py",
    "camera.py", "calibrate.py", "requirements.txt", "run.bat", "run_pet.bat",
    "setup.bat", "README.md", "ARCHITECTURE.md", "START_HERE.txt",
    "pet_poses.png", "pet_ui_panel.png", "pet_dock.png",
    "pet_cursor_follow.png", "pet_bindings.png",
    "models/hand_landmarker.task",
    "models/gesture_recognizer.task",
    "models/blaze_face_short_range.tflite",
    "models/blaze_face_full_range.tflite",
    "tests/test_gestures.py",
    "tests/test_pet_core.py",
]

# 整个目录一起打包（桌宠那一层文件多，逐个列容易漏）
DIR_INCLUDES = ["pet"]

SKIP_DIRS = {".venv", "__pycache__", ".workbuddy", "dist", "calibration", ".git"}


def collect() -> list[str]:
    """返回相对路径列表：显式文件 + 目录（递归，跳过缓存）。"""
    out = list(INCLUDES)
    for d in DIR_INCLUDES:
        base = os.path.join(BASE, d)
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in sorted(files):
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(root, f)
                out.append(os.path.relpath(full, BASE))
    return out


def main() -> int:
    files = collect()
    missing = [p for p in INCLUDES if not os.path.exists(os.path.join(BASE, p))]
    if missing:
        print("[error] 缺少文件:", ", ".join(missing))
        return 1

    os.makedirs(DIST, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = os.path.join(DIST, f"hand-control-portable-{stamp}.zip")

    # 所有文件放在同一个顶层文件夹里：解压出来是「一个项目文件夹」，
    # 而不是把十几个文件散落在解压目标目录（解压到下载文件夹会一团乱）
    root = "hand-control-portable"

    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            full = os.path.join(BASE, p)
            # zip 内部统一用 / 分隔，解压到任何系统都正常
            z.write(full, f"{root}/{p.replace(os.sep, '/')}")
            size = os.path.getsize(full)
            total += size
            print(f"  + {p:<45}{size / 1048576:6.2f} MB")

    zsize = os.path.getsize(out)
    print(f"\n[ok] {out}")
    print(f"     原始 {total / 1048576:.1f} MB → 压缩后 {zsize / 1048576:.1f} MB，"
          f"{len(files)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
