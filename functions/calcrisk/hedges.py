"""Hedges for a matchup: build each hedge, check which boosts fit it, and
group the results by boost combination."""

import hashlib
from datetime import datetime
from typing import Any

from calcrisk.boosts import Boost
from calcrisk.config import BOOKMAKERS, MARKET_LABELS, MAX_BETS_PER_LIST
from calcrisk.matchups import Matchup, Side
from calcrisk.odds_math import DEFAULT_ROUNDING_MODE, payout_multiplier
from calcrisk.stakes import optimize_versions


def _opportunity_id(opp: dict[str, Any]) -> str:
    parts = [opp["eventId"], opp["market"]]
    parts += [f"{leg['bookmaker']}:{leg['selection']}:{leg['boostId']}" for leg in opp["legs"]]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:24]


def _build_opportunity(
    matchup: Matchup,
    kind: str,
    legs: list[tuple[Side, float, Boost | None]],
    versions: dict[str, tuple[tuple[float, float], float, float]],
) -> dict[str, Any]:
    """legs: (bet, payout multiplier, boost or None), in display order."""
    version_list, seen = [], set()
    for key in ("safest", "balanced", "upside"):
        stakes, guaranteed, possible = versions[key]
        if stakes in seen:
            continue
        seen.add(stakes)
        version_list.append({
            "key": key,
            "legs": [{"stake": stake, "payout": round(stake * mult, 2)}
                     for stake, (_, mult, _) in zip(stakes, legs)],
            "totalStake": round(sum(stakes), 2),
            "guaranteedProfit": round(guaranteed, 2),
            "maxProfit": round(possible, 2),
        })
    safest = version_list[0]
    result = {
        "betType": matchup.league,
        "eventId": matchup.event_id,
        "game": matchup.game,
        "commenceTime": matchup.commence_time,
        "market": matchup.market,
        "marketLabel": MARKET_LABELS.get(matchup.market, matchup.market.replace("_", " ")),
        "type": kind,
        "boostIds": sorted(boost.id for _, _, boost in legs if boost),
        "legs": [
            {
                "bookmaker": side.book,
                "selection": side.selection,
                "odds": side.odds,
                "stake": leg["stake"],
                "payout": leg["payout"],
                "boostPercent": boost.percent if boost else 0,
                "boostId": boost.id if boost else None,
            }
            for (side, _, boost), leg in zip(legs, safest["legs"])
        ],
        "totalStake": safest["totalStake"],
        "guaranteedProfit": safest["guaranteedProfit"],
        "maxProfit": safest["maxProfit"],
        "roiPercent": round(safest["guaranteedProfit"] / safest["totalStake"] * 100, 2),
        "bestMaxProfit": max(v["maxProfit"] for v in version_list),
        "versions": version_list,
        "middle": matchup.middle,
    }
    result["id"] = _opportunity_id(result)
    return result


def boost_fits(boost: Boost, side: Side, matchup: Matchup, now: datetime) -> bool:
    """A boost can be used on this bet: right book and league, the game starts
    inside the boost's window (and is the boost's game, if it has one), the
    bet is a type the boost allows, and its odds are inside the boost's range."""
    return (
        boost.bookmaker == side.book
        and boost.bet_type == matchup.league
        and boost.covers_game(matchup.event_id, matchup.commence_time, now)
        and boost.allows_market(matchup.market)
        and boost.min_odds <= side.odds <= boost.max_odds
    )


def find_opportunities(
    matchup: Matchup, boosts: list[Boost], now: datetime, mode: str = DEFAULT_ROUNDING_MODE
) -> list[dict[str, Any]]:
    a, b = matchup.side_a, matchup.side_b
    boosts_a = [x for x in boosts if boost_fits(x, a, matchup, now)]
    boosts_b = [x for x in boosts if boost_fits(x, b, matchup, now)]
    if not boosts_a and not boosts_b:
        return []
    flipped = tuple((rb, ra) for ra, rb in matchup.scenarios)
    results = []

    # One boosted bet, hedged with an unboosted bet on the other side.
    for boosted, hedge, boost, scenarios in (
        [(a, b, x, matchup.scenarios) for x in boosts_a]
        + [(b, a, x, flipped) for x in boosts_b]
    ):
        mults = (payout_multiplier(boosted.odds, boost.percent), payout_multiplier(hedge.odds))
        versions = optimize_versions(mode, mults, (boost.max_bet, None), (0,), scenarios)
        if versions:
            results.append(_build_opportunity(
                matchup, "one_way",
                [(boosted, mults[0], boost), (hedge, mults[1], None)], versions))

    # Both bets boosted.
    for x in boosts_a:
        for y in boosts_b:
            if x.id == y.id:
                continue
            mults = (payout_multiplier(a.odds, x.percent), payout_multiplier(b.odds, y.percent))
            versions = optimize_versions(
                mode, mults, (x.max_bet, y.max_bet), (0, 1), matchup.scenarios)
            if versions:
                results.append(_build_opportunity(
                    matchup, "two_way", [(a, mults[0], x), (b, mults[1], y)], versions))
    return results


def group_opportunities(
    opportunities: list[dict[str, Any]], boosts_by_id: dict[str, Boost], now: datetime
) -> dict[str, dict[str, Any]]:
    """One group per boost combination (one boost, or a pair), holding its
    best hedges by guaranteed profit and by possible profit."""
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for opp in opportunities:
        buckets.setdefault(tuple(opp["boostIds"]), []).append(opp)

    groups = {}
    for boost_ids, bets in buckets.items():
        by_guaranteed = sorted(bets, key=lambda o: -o["guaranteedProfit"])
        by_possible = sorted(bets, key=lambda o: -o["bestMaxProfit"])
        keep = {o["id"]: o for o in by_guaranteed[:MAX_BETS_PER_LIST] + by_possible[:MAX_BETS_PER_LIST]}
        boosts = sorted((boosts_by_id[i] for i in boost_ids),
                        key=lambda b: (BOOKMAKERS.index(b.bookmaker), b.id))
        not_yet_open = [b for b in boosts if b.valid_from > now]
        group_id = hashlib.sha1("|".join(boost_ids).encode()).hexdigest()[:24]
        groups[group_id] = {
            "type": "two_way" if len(boost_ids) == 2 else "one_way",
            "boostIds": list(boost_ids),
            "boosts": [b.summary() for b in boosts],
            "usedBoostIds": [b.id for b in boosts if b.used],
            "upcoming": bool(not_yet_open),
            "availableFrom": max((b.valid_from for b in not_yet_open), default=None),
            "bestProfit": by_guaranteed[0]["guaranteedProfit"],
            "bestMaxProfit": by_possible[0]["bestMaxProfit"],
            "betCount": len(bets),
            "bets": sorted(keep.values(), key=lambda o: -o["guaranteedProfit"]),
        }
    return groups
