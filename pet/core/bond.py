# -*- coding: utf-8 -*-
"""
亲密度 —— 记录"你和它处了多久、互动过多少次"。

为什么不塞进 gestures.yaml：
    配置是**用户意图**（我要它怎么表现），亲密度是**运行时数据**（它记住了什么）。
    混在一起的话，用户点一次"恢复默认配置"就把相处记录清空了——那是数据，
    不是设置，不该被配置操作波及。

为什么不做衰减：
    桌宠是"陪伴"向的东西。三天没开电脑回来发现关系归零，只会让人不想再开。
    真实的关系本来也不会因为出差一周就清零。

隐私：
    只存计数和日期（互动了几次、第一次是哪天），不存画面、不存内容、
    不记录你做了什么具体的事。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional, Tuple

#: 等级表：(累计亲密度门槛, 名字, 见面时的招呼)
#: 门槛是"手感值"不是科学值：第一天随便玩玩就能到"熟悉"，
#: 真正到"黏人"需要持续用一两周——这个节奏和养一只电子宠物接近。
LEVELS: Tuple[Tuple[int, str, str], ...] = (
    (0, "陌生", "……"),
    (30, "熟悉", "是你呀～"),
    (100, "亲近", "你来啦！"),
    (250, "黏人", "我等你好久了"),
)

#: 不同互动的权重。为什么摸一下比比个手势贵：
#: 手势是"下指令"，撸宠是"看着它、对着它花时间"，后者更像真的在相处。
WEIGHTS: Dict[str, float] = {
    "gesture": 1.0,     # 手势触发一次宠物反应
    "pet": 2.0,         # 撸满一次（有完整过程，不是划过）
    "game": 3.0,        # 玩完一局石头剪刀布
    "click": 1.0,       # 点它一下
}


def level_of(points: float) -> Tuple[int, str, str]:
    """累计值 -> (等级序号, 名字, 招呼语)。"""
    idx = 0
    for i, (need, _name, _greet) in enumerate(LEVELS):
        if points >= need:
            idx = i
    return idx, LEVELS[idx][1], LEVELS[idx][2]


def next_threshold(points: float) -> Optional[int]:
    """距离下一级还差多少点。已满级返回 None（界面别显示"还差 -5"）。"""
    for need, _name, _greet in LEVELS:
        if points < need:
            return int(need - points)
    return None


@dataclass
class Bond:
    """
    一段关系的全部数据。

    刻意做成"可序列化的扁平结构"：只有数字和日期，没有对象引用。
    这样文件被改坏、被删掉，最多丢一段记忆，不会让程序起不来。
    """

    points: float = 0.0
    interactions: int = 0
    first_seen: str = ""                        # ISO 日期
    last_seen: str = ""
    by_kind: Dict[str, int] = field(default_factory=dict)

    # ---------------------------------------------------------------- 派生
    @property
    def level(self) -> Tuple[int, str, str]:
        return level_of(self.points)

    @property
    def level_name(self) -> str:
        return self.level[1]

    @property
    def greeting(self) -> str:
        return self.level[2]

    @property
    def remaining(self) -> Optional[int]:
        return next_threshold(self.points)


class BondTracker:
    """负责读、写、加分。加分返回"这次有没有升级"，上层据此决定要不要庆祝。"""

    def __init__(self, path: str, bond: Optional[Bond] = None) -> None:
        self.path = path
        self.bond = bond or Bond()
        # 新建的关系要立刻有"第一次见面"的日期，否则 save() 会写进空串，
        # 下次 load 再补今天的日期，"第几天"就从今天重新数了
        if not self.bond.first_seen:
            self.bond.first_seen = date.today().isoformat()
        if not self.bond.last_seen:
            self.bond.last_seen = self.bond.first_seen

    # ---------------------------------------------------------------- 读写
    @classmethod
    def load(cls, path: str) -> "BondTracker":
        bond = Bond()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    bond.points = float(data.get("points", 0) or 0)
                    bond.interactions = int(data.get("interactions", 0) or 0)
                    bond.first_seen = str(data.get("first_seen", "") or "")
                    bond.last_seen = str(data.get("last_seen", "") or "")
                    raw = data.get("by_kind") or {}
                    if isinstance(raw, dict):
                        bond.by_kind = {str(k): int(v) for k, v in raw.items()
                                        if isinstance(v, (int, float))}
            except Exception:
                # 文件坏了就当作新朋友，而不是让桌宠打不开
                bond = Bond()

        today = date.today().isoformat()
        if not bond.first_seen:
            bond.first_seen = today
        bond.last_seen = today
        return cls(path, bond)

    def save(self) -> None:
        """
        原子写入：先写临时文件再 replace。

        进程被杀（关机、任务管理器结束）时，直接覆写可能留下半截 JSON，
        下次启动就成了"数据损坏"。os.replace 在三大平台都是原子的。
        """
        try:
            folder = os.path.dirname(os.path.abspath(self.path)) or "."
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({
                        "points": round(self.bond.points, 2),
                        "interactions": self.bond.interactions,
                        "first_seen": self.bond.first_seen,
                        "last_seen": self.bond.last_seen,
                        "by_kind": self.bond.by_kind,
                    }, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception:
            # 写不进去（只读目录、磁盘满）不该影响正在进行的互动
            pass

    # ---------------------------------------------------------------- 加分
    def add(self, kind: str, amount: Optional[float] = None) -> bool:
        """
        记一次互动。返回 True 表示**刚好跨过了一个等级**（上层可以庆祝一下）。

        未知 kind 按 1 分算：加新玩法时忘了登记权重，也不至于悄悄不加分。
        """
        before = self.bond.level[0]
        gain = WEIGHTS.get(kind, 1.0) if amount is None else float(amount)
        self.bond.points += max(0.0, gain)
        self.bond.interactions += 1
        self.bond.by_kind[kind] = self.bond.by_kind.get(kind, 0) + 1
        self.bond.last_seen = date.today().isoformat()
        return self.bond.level[0] > before

    # ---------------------------------------------------------------- 展示
    def summary(self) -> str:
        """一行摘要，给托盘 tooltip / 控制面板用。"""
        days = self._days_together()
        parts = [f"亲密度 {self.bond.points:.0f}", self.bond.level_name]
        if days > 1:
            parts.append(f"第 {days} 天")
        return " · ".join(parts)

    def days_together(self) -> int:
        """相处第几天（从第一次见面算）。界面层用它，不用碰私有方法。"""
        return self._days_together()

    def _days_together(self) -> int:
        try:
            first = date.fromisoformat(self.bond.first_seen)
            return max(1, (date.today() - first).days + 1)
        except Exception:
            return 1
