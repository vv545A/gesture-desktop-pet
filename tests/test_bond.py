# -*- coding: utf-8 -*-
"""
亲密度的持久化与等级测试。

跑法：
    python tests/test_bond.py

重点不在"加分会加"，而在**坏情况下人不会丢数据**：
文件被手改坏、写到一半断电、目录只读。这几条错了不会崩，
只会在某天早上发现"怎么又变回陌生人了"。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pet.core.bond import (LEVELS, WEIGHTS, Bond, BondTracker,   # noqa: E402
                           level_of, next_threshold)

PASS = []
TMP = tempfile.mkdtemp(prefix="bondtest_")


def check(name: str, cond: bool) -> None:
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        raise AssertionError(name)


def path(name: str = "bond.json") -> str:
    return os.path.join(TMP, name)


print("== 等级门槛 ==")
check("0 分是陌生", level_of(0)[1] == "陌生")
check("刚好 30 分升到熟悉", level_of(30)[1] == "熟悉")
check("29 分还是陌生", level_of(29)[1] == "陌生")
check("250 分到黏人", level_of(250)[1] == "黏人")
check("超出门槛不会越界", level_of(99999)[1] == LEVELS[-1][1])
check("0 分时差 30 到下一级", next_threshold(0) == 30)
check("满级后没有下一级", next_threshold(9999) is None)

print("== 加分与升级信号 ==")
t = BondTracker(path())
up = t.add("gesture")
check("第一次手势不加出等级", up is False and t.bond.points == WEIGHTS["gesture"])
check("互动次数记上了", t.bond.interactions == 1)
check("分类计数记上了", t.bond.by_kind.get("gesture") == 1)

t = BondTracker(path())
for _ in range(30):
    up = t.add("gesture")           # 每次 1 分，第 30 次正好升到"熟悉"
check("跨过门槛那一次返回 True", up is True)
check("等级变成熟悉", t.bond.level_name == "熟悉")

check("撸一次比手势加分多", WEIGHTS["pet"] > WEIGHTS["gesture"])
t = BondTracker(path())
t.add("不认识的玩法")
check("未知类型也给分（不会悄悄不加）", t.bond.points >= 1.0)

print("== 持久化 ==")
p = path("save.json")
t = BondTracker(p)
for _ in range(5):
    t.add("game")
t.save()
check("文件写出来了", os.path.exists(p))

t2 = BondTracker.load(p)
check("分数读回来了", abs(t2.bond.points - t.bond.points) < 1e-6)
check("次数读回来了", t2.bond.interactions == 5)
check("分类明细读回来了", t2.bond.by_kind.get("game") == 5)
check("首次见面日期保留了", t2.bond.first_seen == t.bond.first_seen)

print("== 坏数据不能让人丢东西 ==")
bad = path("bad.json")
with open(bad, "w", encoding="utf-8") as f:
    f.write("{ 这不是合法 JSON ")
t3 = BondTracker.load(bad)
check("坏文件退回全新关系而不是崩溃", t3.bond.points == 0 and t3.bond.interactions == 0)
check("但仍然记下了今天的日期", bool(t3.bond.first_seen))

weird = path("weird.json")
with open(weird, "w", encoding="utf-8") as f:
    json.dump({"points": "很多", "by_kind": [1, 2, 3]}, f)
t4 = BondTracker.load(weird)
check("字段类型不对也不崩", t4.bond.points == 0)

empty = path("empty.json")
with open(empty, "w", encoding="utf-8") as f:
    f.write("")
t5 = BondTracker.load(empty)
check("空文件不崩", t5.bond.points == 0)

print("== 写入失败不影响互动 ==")
# 指向一个不存在的目录：save 会失败，但 add 仍然要能加
t6 = BondTracker(os.path.join(TMP, "no_such_dir", "x", "bond.json"))
t6.add("gesture")
t6.save()                       # 不抛异常
check("写不进去也不影响加分", t6.bond.points >= 1.0)

print("== 摘要 ==")
t7 = BondTracker(path("s.json"), Bond(points=120, interactions=40,
                                      first_seen="2020-01-01"))
s = t7.summary()
check("摘要含亲密度", "120" in s)
check("摘要含等级", "亲近" in s)
check("摘要含相处天数", "第" in s)

print(f"\n[RESULT] {len(PASS)} 项全部通过")
