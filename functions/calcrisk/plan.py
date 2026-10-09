"""Best plan: how to use the boosts for the most total profit."""

from datetime import datetime
from functools import lru_cache
from typing import Any

from calcrisk.boosts import Boost

# Every unused boost whose window is open now is used once: alone (boost + an
# unboosted hedge) or paired with another boost on the same hedge. Starting
# from "every boost alone", pairing boosts x and y changes the total by
#     gain = together(x, y) - alone(x) - alone(y)
# and the plan is the set of non-overlapping pairs with the largest total
# gain, found exactly. It's computed twice: once maximizing guaranteed
# profit, and once maximizing possible profit.

MAX_EXACT_PLAN_BOOSTS = 18


def _best_matching(gains: dict[frozenset, float]) -> list[tuple[str, str]]:
    """Non-overlapping pairs of boosts with the largest total gain."""
    ids = sorted({i for pair in gains for i in pair})
    if not ids:
        return []
    if len(ids) > MAX_EXACT_PLAN_BOOSTS:
        chosen, taken = [], set()
        for pair, _ in sorted(gains.items(), key=lambda kv: -kv[1]):
            if not pair & taken:
                chosen.append(tuple(sorted(pair)))
                taken |= pair
        return chosen
    index = {boost_id: n for n, boost_id in enumerate(ids)}
    gain = {}
    for pair, g in gains.items():
        x, y = sorted(index[i] for i in pair)
        gain[(x, y)] = g

    @lru_cache(maxsize=None)
    def best(mask: int) -> tuple[float, tuple[tuple[str, str], ...]]:
        if mask == 0:
            return 0.0, ()
        i = (mask & -mask).bit_length() - 1  # lowest boost left
        rest = mask & ~(1 << i)
        result = best(rest)  # boost i used alone
        j_mask = rest
        while j_mask:
            j = (j_mask & -j_mask).bit_length() - 1
            j_mask &= j_mask - 1
            g = gain.get((i, j))
            if g is not None:
                total, pairs = best(rest & ~(1 << j))
                if total + g > result[0] + 1e-12:
                    result = (total + g, pairs + ((ids[i], ids[j]),))
        return result

    return list(best((1 << len(ids)) - 1)[1])


def build_best_plan(
    groups: dict[str, dict[str, Any]], boosts: list[Boost], now: datetime, objective: str
) -> dict[str, Any]:
    usable = {b.id: b for b in boosts if not b.used and b.is_active(now)}
    by_guaranteed = objective == "guaranteed"
    group_value = "bestProfit" if by_guaranteed else "bestMaxProfit"

    def bet_value(bet: dict[str, Any]) -> float:
        return bet["guaranteedProfit"] if by_guaranteed else bet["bestMaxProfit"]

    def chosen_version(bet: dict[str, Any]) -> dict[str, Any]:
        if by_guaranteed:
            return bet["versions"][0]  # safest
        return max(bet["versions"], key=lambda v: (v["maxProfit"], v["guaranteedProfit"]))

    alone: dict[str, tuple[str, dict[str, Any]]] = {}
    together: dict[frozenset, tuple[str, dict[str, Any]]] = {}
    for gid, group in groups.items():
        ids = frozenset(group["boostIds"])
        if not ids <= usable.keys():
            continue
        if len(ids) == 1:
            alone[next(iter(ids))] = (gid, group)
        else:
            together[ids] = (gid, group)

    def alone_value(boost_id: str) -> float:
        return alone[boost_id][1][group_value] if boost_id in alone else 0.0

    gains = {}
    for ids, (_, group) in together.items():
        g = group[group_value] - sum(alone_value(i) for i in ids)
        if g > 0.004:  # pairing must beat using both alone by at least a cent
            gains[ids] = g
    pairs = _best_matching(gains)
    paired = {i for pair in pairs for i in pair}

    def step(gid: str, group: dict[str, Any], **extra: Any) -> dict[str, Any]:
        bet = max(group["bets"], key=bet_value)
        version = chosen_version(bet)
        return {
            "type": group["type"],
            "groupId": gid,
            "boosts": group["boosts"],
            "bet": bet,
            "versionKey": version["key"],
            "profit": bet_value(bet),
            "guaranteedProfit": version["guaranteedProfit"],
            "maxProfit": version["maxProfit"],
            **extra,
        }

    steps = []
    for pair in pairs:
        gid, group = together[frozenset(pair)]
        steps.append(step(gid, group, separateProfit=round(sum(alone_value(i) for i in pair), 2)))

    idle = []
    for boost_id, boost in usable.items():
        if boost_id in paired:
            continue
        if boost_id not in alone:
            idle.append(boost.summary())
            continue
        alternative = None
        options = [(ids, group[group_value]) for ids, (_, group) in together.items() if boost_id in ids]
        if options:
            ids, best_together = max(options, key=lambda o: o[1])
            partner = next(i for i in ids if i != boost_id)
            alternative = {
                "partner": usable[partner].summary(),
                "togetherProfit": best_together,
                "separateProfit": round(alone_value(boost_id) + alone_value(partner), 2),
            }
        gid, group = alone[boost_id]
        steps.append(step(gid, group, separateProfit=None, alternative=alternative))

    steps.sort(key=lambda s: s["profit"], reverse=True)
    return {
        "objective": objective,
        "totalProfit": round(sum(s["profit"] for s in steps), 2),
        "allSeparateProfit": round(sum(alone_value(i) for i in usable), 2),
        "steps": steps,
        "idleBoosts": idle,
    }
