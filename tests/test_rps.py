# -*- coding: utf-8 -*-
"""
石头剪刀布的规则测试 —— 用假时钟把整场走完，不碰摄像头也不用等 3 秒。

跑法：
    python tests/test_rps.py

为什么这里几乎每一条都在测"不该触发的时候没触发"：
    游戏逻辑最容易写的不是规则，是边界。倒数期间比手势算不算？比个 ☝️
    要不要判负？一直不出手会不会卡死？这些错了不会报错，只会让玩家觉得
    "这游戏有 bug 但说不上哪不对"。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pet.game.rps import BEATS, CHOICES, DRAW, LOSE, WIN, RPSGame   # noqa: E402

PASS = []


def check(name: str, cond: bool) -> None:
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name}")
        raise AssertionError(name)


class FixedRng:
    """固定出拳的假随机源：让"谁赢"可以被断言。"""

    def __init__(self, choice: str) -> None:
        self.fixed = choice

    def choice(self, seq):
        assert self.fixed in seq, f"{self.fixed} 不在 {seq} 里"
        return self.fixed


print("== 规则表自洽 ==")
check("三种手势都在赢负关系里", all(g in BEATS for g in CHOICES))
check("赢关系是闭环（不会出现两个手势互相都赢）",
      all(BEATS[BEATS[BEATS[g]]] == g for g in CHOICES))
check("没有手势能赢自己", all(BEATS[g] != g for g in CHOICES))

print("== 开始与倒数 ==")
g = RPSGame(rng=FixedRng("FIST"))
check("初始是 idle", g.phase == "idle" and not g.active)
g.start(now=0.0)
check("开始后进入倒数", g.active and g.phase == "countdown")
snap = g.tick(1.0)
check("倒数显示剩余秒数", snap.phase == "countdown" and snap.count == 2)
g.tick(2.9)
check("倒数期间比手势不算数", g.play("VICTORY", 2.9) is None)
g.tick(3.2)
check("倒数结束进入 armed", g.phase == "armed")

print("== 出拳判定 ==")
g = RPSGame(rng=FixedRng("FIST"))       # 宠物永远出石头
g.start(0.0)
g.tick(3.5)                             # 跳过倒数
r = g.play("OPEN_PALM", 3.5)            # 布赢石头
check("布赢石头", r is not None and r.result == WIN)
check("结果带双方出拳", r.player == "OPEN_PALM" and r.pet == "FIST")
check("比分记到赢", r.score == (1, 0, 0))
check("出完进入 result", g.phase == "result")

g2 = RPSGame(rng=FixedRng("FIST"))
g2.start(0.0)
g2.tick(3.5)
r2 = g2.play("VICTORY", 3.5)            # 剪刀输石头
check("剪刀输石头", r2 is not None and r2.result == LOSE)

g3 = RPSGame(rng=FixedRng("FIST"))
g3.start(0.0)
g3.tick(3.5)
r3 = g3.play("FIST", 3.5)
check("同样手势平局", r3 is not None and r3.result == DRAW)

print("== 不该接受的输入 ==")
g = RPSGame(rng=FixedRng("FIST"))
g.start(0.0)
g.tick(3.5)
check("☝️ 这种无关手势不算出拳", g.play("POINT", 3.5) is None)
check("无关手势不改变状态", g.phase == "armed")
check("👍 也不算", g.play("THUMB_UP", 3.5) is None)
r = g.play("VICTORY", 3.6)
check("之后正常出手仍然有效", r is not None)

# 同一轮不能出两次拳，否则举着手会被连判
check("同一轮出第二次不算", g.play("FIST", 3.7) is None)

print("== 节奏：结果停留后自动进入下一轮 ==")
g = RPSGame(rng=FixedRng("FIST"))
g.start(0.0)
g.tick(3.5)
g.play("OPEN_PALM", 3.5)
g.tick(4.0)
check("结果期间还在 result", g.phase == "result")
g.tick(3.5 + RPSGame.RESULT_HOLD + 0.1)
check("停留时间到了自动回倒数", g.phase == "countdown")
check("新一局清掉上一轮结果", g.round is None)

print("== 卡住保护：一直不出手不会永远 armed ==")
g = RPSGame(rng=FixedRng("FIST"))
g.start(0.0)
g.tick(3.5)
check("先进入 armed", g.phase == "armed")
g.tick(3.5 + RPSGame.ARM_TIMEOUT + 0.1)
check("超时退回倒数", g.phase == "countdown")

print("== 比分累计与整场胜负 ==")
g = RPSGame(rng=FixedRng("FIST"))
g.start(0.0)
now = 0.0
for i in range(RPSGame.ROUNDS_TO_WIN):
    now += 3.5
    g.tick(now)
    g.play("OPEN_PALM", now)            # 每局都赢
    now += RPSGame.RESULT_HOLD + 0.1
    g.tick(now)
check(f"连赢 {RPSGame.ROUNDS_TO_WIN} 局后整场结束", g.match_over() is True)
check("比分对得上", g.score[WIN] == RPSGame.ROUNDS_TO_WIN)

print("== 停止 ==")
g.stop()
check("停止后回到 idle", g.phase == "idle" and not g.active)
check("停止后 tick 不再推进", g.tick(1e6).active is False)
check("停止后出拳无效", g.play("FIST", 1e6) is None)

print(f"\n[RESULT] {len(PASS)} 项全部通过")
