# -*- coding: utf-8 -*-
"""
生成文档配图（README 里那几张示意图）。

    python tools/make_docs_images.py

为什么要有这个脚本：
    桌宠换形象之后（鲸鱼娘 → 小桃），文档里的截图会一起过期 —— 读者看到的
    是"一只已经不存在的宠物"，而手工重截图再拼版，下次换形象又得重来一遍。
    所以把拼版写成脚本：图里的桌宠永远是**当前配置里那只**。

生成三张（输出到仓库根目录）：
    pet_dock.png           吸附到窗口（窗口边线、压边比例、标签）
    pet_cursor_follow.png  跟鼠标的绳子模型（110px 尾随距离）
    pet_ui_panel.png       右上角关闭键 + 点桌宠弹出的控制面板

另外两张不在这里，用的是运行时同一份绘制代码：
    python -m pet --snapshot pet_poses.png          七帧立绘 + 悬停环
    python -m pet --snapshot-actions pet_actions.png 10 个动作 × 5 帧
"""

from __future__ import annotations

import math
import os
import tempfile
import time
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PySide6.QtCore import QPointF, QRectF, Qt                          # noqa: E402
from PySide6.QtGui import (QColor, QImage, QPainter, QPen, QPixmap,     # noqa: E402
                         QPolygonF)
from PySide6.QtWidgets import QApplication                              # noqa: E402

from pet.core.bus import EventBus                                     # noqa: E402
from pet.core.config import Config                                    # noqa: E402
from pet.ui.paint import ui_font                                      # noqa: E402
from pet.ui.window import PetWindow                                   # noqa: E402

BG = QColor(239, 243, 247)
CARD = QColor(255, 255, 255)
LINE = QColor(226, 232, 239)
TEXT = QColor(52, 68, 78)
MUTED = QColor(132, 148, 158)
BODY = QColor(90, 108, 120)
ACCENT = QColor(77, 107, 254)


# ---------------------------------------------------------------- 通用画法
class _FakeControl:
    """
    截图用的假控制中心。

    为什么必须有：`ControlCenter` 会开摄像头、起线程 —— 出文档图不该碰硬件。
    但 `ControlPanel.paintEvent` 会读 `control.enabled`，读到 None 会抛异常；
    **PySide6 里虚拟函数抛异常是直接 abort 进程**（不是弹 traceback），
    所以少了这个桩，脚本会一声不响地 0 退出、图却是旧的。
    """

    enabled = True

    def set_enabled(self, _v: bool) -> None:
        self.enabled = _v


def canvas(w: int, h: int) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(w, h)
    pm.fill(BG)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    return pm, p


def text(p: QPainter, x: float, y: float, w: float, s: str, size: int = 10,
         color: QColor = TEXT, bold: bool = False) -> None:
    """画多行文字：\\n 自己换行（drawText 带 TextWordWrap 会算不准行距）。"""
    p.setPen(color)
    p.setFont(ui_font(size, bold=bold))
    for i, line in enumerate(s.split("\n")):
        p.drawText(int(x), int(y) + i * int(size * 2.05), int(w), 28, 0x80, line)


def mock_window(p: QPainter, rect: QRectF, title: str, rows: int = 6,
                buttons: bool = False) -> None:
    """一个"普通窗口"的骨架：圆角白底 + 标题栏 + 几行占位条。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 14))
    p.drawRoundedRect(rect.adjusted(2, 4, 2, 5), 10, 10)
    p.setBrush(CARD)
    p.setPen(QPen(LINE, 1))
    p.drawRoundedRect(rect, 10, 10)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(246, 248, 251))
    p.drawRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), 40), 10, 10)
    p.drawRect(QRectF(rect.left(), rect.top() + 30, rect.width(), 10))
    text(p, rect.left() + 16, rect.top() + 10, rect.width() - 32, title, 10, TEXT)

    y = rect.top() + 58
    widths = (0.62, 0.70, 0.50, 0.74, 0.56, 0.66, 0.44)
    for i in range(rows):
        w = (rect.width() - 64) * widths[i % len(widths)]
        p.setBrush(QColor(222, 230, 238))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(rect.left() + 32, y, w, 13), 6, 6)
        y += 30
        if y > rect.bottom() - 56:
            break

    if buttons:
        for i in range(3):
            r = QRectF(rect.left() + 32 + i * 96, rect.bottom() - 44, 80, 28)
            p.setBrush(QColor(246, 248, 251))
            p.setPen(QPen(LINE, 1))
            p.drawRoundedRect(r, 7, 7)
            text(p, r.left(), r.top() + 6, r.width(), "按钮", 9, MUTED)


def _cfg() -> Config:
    """读本机真实配置。

    早先这里硬写 pet_config.json —— 那是上一代配置格式，现在已经迁到
    gestures.yaml。显式传一个不存在的路径会**新建**一份默认配置，
    于是文档图里画的是一只"代码默认值里的宠物"，而不是你正在用的那只。
    """
    return Config.load()


def _demo_bond():
    """造一份"已经相处了一阵子"的存档，只为把面板顶部的亲密度板画出来。

    真实运行里没有存档时面板不显示这一块（关系不该是编出来的），
    但文档图需要展示它的样子。
    """
    from datetime import date, timedelta

    from pet.core.bond import Bond, BondTracker

    started = (date.today() - timedelta(days=8)).isoformat()
    bond = Bond(points=132.0, interactions=96, first_seen=started,
                last_seen=date.today().isoformat())
    return BondTracker(os.path.join(tempfile.gettempdir(), "docs_bond.json"), bond)


def _make_window(mood: str = "idle", bubble: str = "", dock_title: str = "",
                 sit: bool = False, enabled: bool = True) -> PetWindow:
    """搭一个"和真实运行一模一样"的桌宠窗口，摆好状态，等着被截。"""
    from pet.core.windowinfo import WindowInfo

    cfg = _cfg()
    win = PetWindow(EventBus(), cfg, control=_FakeControl())
    win.resize(win.win_w, win.win_h)
    win._enabled = enabled
    win._mood = mood
    win._mood_until = 1e18
    win._sit = sit
    if dock_title:
        # 吸附标签用的是"前台窗口快照"，这里造一个假的，只为把标签画出来
        win._dock = WindowInfo(hwnd=1, title=dock_title,
                               left=100, top=100, right=700, bottom=500)
        win._dock_side = "right"
    if bubble:
        win._bubble = bubble
        # 把"开始说话的时刻"往前推 0.6 秒：跳过淡入，气泡直接是满不透明度。
        # 别用 -1e9 —— _tick 会算出巨大的 age，判定成"早该消失了"，气泡直接没了。
        win._bubble_t0 = time.monotonic() - 0.6
    win._tick()
    win._phase = 1.2                       # 定住一个好看的相位，截图才稳定
    return win


def pet_image(mood: str = "idle", bubble: str = "") -> QImage:
    """截一整只桌宠窗口（含气泡和关闭键）——"运行起来长什么样"就是这个。"""
    return _make_window(mood, bubble).grab().toImage()


def pet_body(mood: str = "idle", bubble: str = "", dock_title: str = "",
             sit: bool = False, pad: int = 14) -> QImage:
    """
    只截"人"，把窗口里那些 UI（右上角 ×、吸附标签）裁掉，用于示意图拼版。

    为什么要裁：桌宠窗口比身体宽 1.95 倍（宽出来的地方是给气泡和进度环留的），
    整窗贴进示意图会带上一大片空白和一个飘在半空的关闭键 —— 看着像 bug。
    """
    win = _make_window(mood, bubble, dock_title, sit, True)
    img = win.grab().toImage()
    bw, bh = win.body_w, win.body_h
    left = win.pet_cx - bw / 2 - pad
    top = win.pet_cy - bh * 0.55
    w = bw + pad * 2
    h = bh * 1.12
    left = max(0.0, min(left, img.width() - w))
    top = max(0.0, min(top, img.height() - h))
    return img.copy(int(left), int(top), int(w), int(h))


def paste_body(p: QPainter, img: QImage, cx: float, cy: float,
               body_h: float) -> None:
    """把"只有人"的图贴到 (cx, cy)，按目标身体高度等比缩放。"""
    scale = body_h / (img.height() / 1.12)
    w, h = img.width() * scale, img.height() * scale
    p.drawImage(QRectF(cx - w / 2, cy - h / 2, w, h), img)


def chip(p: QPainter, cx: float, cy: float, w: float, s: str) -> None:
    """吸附时头顶那块小标签（和 window.py 的 _paint_dock_tag 同一个样子）。"""
    r = QRectF(cx - w / 2, cy, w, 19)
    p.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 95), 1))
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawRoundedRect(r, 9.5, 9.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ACCENT)
    p.drawEllipse(QPointF(r.left() + 10, r.center().y()), 3.0, 3.0)
    p.setPen(QColor(46, 60, 92))
    p.setFont(ui_font(8))
    p.drawText(r.adjusted(18, 0, -8, 0),
               int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), s)


def arrow(p: QPainter, a: QPointF, b: QPointF, color: QColor = MUTED) -> None:
    p.setPen(QPen(color, 1.4, Qt.PenStyle.DashLine))
    p.drawLine(a, b)
    ang = math.atan2(b.y() - a.y(), b.x() - a.x())
    d = 7.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([
        b,
        QPointF(b.x() - d * math.cos(ang - 0.42), b.y() - d * math.sin(ang - 0.42)),
        QPointF(b.x() - d * math.cos(ang + 0.42), b.y() - d * math.sin(ang + 0.42)),
    ]))


def cursor_arrow(p: QPainter, at: QPointF) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(38, 50, 60))
    p.drawPolygon(QPolygonF([
        at, QPointF(at.x(), at.y() + 22),
        QPointF(at.x() + 6, at.y() + 15), QPointF(at.x() + 13, at.y() + 15),
    ]))


# ---------------------------------------------------------------- 吸附
def make_dock(path: str) -> None:
    pm, p = canvas(920, 520)
    win = QRectF(28, 150, 356, 252)
    mock_window(p, win, "隔空手势桌宠 · GitHub", rows=6)

    # 窗口右缘那条蓝线 = "窗口边线"，桌宠身体压上去 16%
    p.setPen(QPen(ACCENT, 2))
    p.drawLine(QPointF(win.right(), win.top() + 8),
               QPointF(win.right(), win.bottom() - 8))

    body_h = 188.0
    body_w = body_h * 0.667
    # 中心落在"窗口边线外面一点"，让身体压进窗口 dock_overlap（16%）那么宽
    cx = win.right() + body_w * (0.5 - 0.16)
    cy = win.center().y() + 34
    paste_body(p, pet_body(), cx, cy, body_h)
    chip(p, cx, cy - body_h * 0.5 - 26, 110, "隔空手势桌宠 · Gi")

    text(p, 566, 42, 330, "吸附到窗口", 15, TEXT, bold=True)
    y = 100
    for s in ("① 头顶标签写的是\n      “现在手势动的那个窗口”",
              "② 蓝色竖线是窗口边线，\n      身体压进 16%，不挡内容",
              "③ 窗口最小化 / 最大化 / 全屏\n      时不贴，回角落待命"):
        text(p, 566, y, 330, s, 10, BODY)
        y += 86
    text(p, 566, 436, 330, "标签 = 窗口标题前 12 个字（dock_title_len）", 9, MUTED)
    p.end()
    pm.save(path)
    print(f"[docs] {path}")


# ---------------------------------------------------------------- 跟鼠标
def make_cursor(path: str) -> None:
    pm, p = canvas(1020, 540)
    win = QRectF(56, 44, 470, 330)
    mock_window(p, win, "我正在操作的窗口", rows=7, buttons=True)

    # 指针在右上、桌宠在左下：两者错开，虚线才不被身体吃掉
    pet = QPointF(392, 372)
    cur = QPointF(506, 232)
    paste_body(p, pet_body(), pet.x(), pet.y(), 182)
    cursor_arrow(p, cur)
    arrow(p, QPointF(cur.x() - 4, cur.y() + 20), QPointF(pet.x() + 58, pet.y() - 76))
    text(p, cur.x() + 8, cur.y() + 34, 60, "110px", 9, MUTED)

    text(p, 566, 56, 430, "跟鼠标 = 跟在指针后面", 15, TEXT, bold=True)
    y = 116
    for s in ("① 身体中心离指针 110px，\n      追到就停，不会压在指针上",
              "② 指针不在它身上时窗口穿透，\n      下面的按钮照原样点得到",
              "③ 指针停在它身上，穿透解除，\n      点它就弹面板"):
        text(p, 566, y, 430, s, 10, BODY)
        y += 86
    text(p, 566, 390, 430, "虚线 = cursor_trail（默认 110）", 9, MUTED)
    p.end()
    pm.save(path)
    print(f"[docs] {path}")


# ---------------------------------------------------------------- 关闭键 + 面板
def make_panel(path: str) -> None:
    """三格：运行中 / 关掉后 / 控制面板。全部是真实控件的截图，不画假 UI。"""
    from pet.ui.panel import ControlPanel

    cfg = _cfg()
    win = PetWindow(EventBus(), cfg, control=_FakeControl(),
                    bond=_demo_bond())
    win.resize(win.win_w, win.win_h)
    win._mood = "idle"
    win._tick()
    win._phase = 1.2
    pet_on = win.grab().toImage()

    win._enabled = False
    win._mood = "sleepy"
    win._mood_until = 1e18
    win._tick()
    pet_off = win.grab().toImage()

    panel_img = ControlPanel(win).grab().toImage()

    cell_w, cell_h, gap = 262, 384, 18
    pm, p = canvas(cell_w * 3 + gap * 2 + 40, 470)
    p.setPen(TEXT)
    p.setFont(ui_font(14, bold=True))
    p.drawText(20, 14, pm.width() - 40, 34, 0x80, "桌宠的关闭键与控制面板")

    top = 52
    for i, (img, cap) in enumerate((
            (pet_on, "① 运行中：右上角 ×"),
            (pet_off, "② 关掉后：按钮变绿色 ▶，摄像头已释放"),
            (panel_img, "③ 点桌宠：控制面板（开关/跟随/玩法/退出）"))):
        x = 20 + i * (cell_w + gap)
        p.setPen(QPen(LINE, 1))
        p.setBrush(QColor(250, 252, 254))
        p.drawRoundedRect(QRectF(x, top, cell_w, cell_h), 8, 8)
        # 等比缩放塞进卡片，居中；不拉伸（拉伸会把圆角和文字压扁）
        scale = min((cell_w - 16) / img.width(), (cell_h - 16) / img.height())
        w, h = img.width() * scale, img.height() * scale
        p.drawImage(QRectF(x + (cell_w - w) / 2, top + (cell_h - h) / 2, w, h), img)
        text(p, x, top + cell_h + 8, cell_w, cap, 9, BODY)

    p.end()
    pm.save(path)
    print(f"[docs] {path}")


# ---------------------------------------------------------------- 情绪一览
def make_moods(path: str) -> None:
    """六种情绪并排（带气泡），README「形象与动作」那一节的头图。"""
    states = (("idle", "今天也要加油哦"), ("happy", "搞定～最大化啦"),
              ("love", "嘿嘿…好痒"), ("angry", "别、别再戳啦！"),
              ("think", "我想想…"), ("surprised", "欸？！"))
    imgs = [pet_image(m, b) for m, b in states]

    cw, ch = imgs[0].width(), imgs[0].height()
    pad_b, label_h, side = 16, 24, 10
    pm, p = canvas(cw * len(imgs) + side * 2, ch + pad_b + label_h)
    for i, img in enumerate(imgs):
        p.drawImage(side + cw * i, 0, img)
    p.setPen(QColor(120, 138, 148))
    p.setFont(ui_font(9))
    for i, (mood, _) in enumerate(states):
        p.drawText(QRectF(side + cw * i, ch + pad_b, cw, label_h),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                   mood)
    p.end()
    pm.save(path)
    print(f"[docs] {path}")


def make_bindings(path: str) -> None:
    """
    手势对照表页面。

    这张图以前是手工截的，换了页签名/换了立绘就永久过期 —— 读者看到的
    是"上一版的界面"。这里改成和别的一样现场渲染，写完代码跑一次即可。
    """
    from PySide6.QtGui import QPixmap as _QPixmap
    from PySide6.QtCore import Qt as _Qt

    from pet.ui.bindings import BindingsDialog

    win = PetWindow(EventBus(), _cfg(), control=_FakeControl())
    win.resize(win.win_w, win.win_h)

    dlg = BindingsDialog(win)
    dlg.setAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground, False)
    dlg.adjustSize()
    pm = _QPixmap(dlg.size())
    pm.fill(_Qt.GlobalColor.white)
    dlg.render(pm)
    pm.save(path)
    print(f"[docs] {path}  ({pm.width()}x{pm.height()})")


def main() -> int:
    _app = QApplication.instance() or QApplication([])
    make_dock(os.path.join(HERE, "pet_dock.png"))
    make_cursor(os.path.join(HERE, "pet_cursor_follow.png"))
    make_panel(os.path.join(HERE, "pet_ui_panel.png"))
    make_moods(os.path.join(HERE, "pet_moods.png"))
    make_bindings(os.path.join(HERE, "pet_bindings.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
