"""Stakes: three versions of every hedge."""

from functools import lru_cache

from calcrisk.odds_math import (
    EPS,
    allowed_stakes_up_to,
    round_stake_down,
    round_stake_up,
)

#   safest    the most guaranteed profit
#   balanced  the most possible profit while keeping at least half of the
#             safest version's guaranteed profit
#   upside    the most possible profit while still guaranteeing a profit
#
# Every version guarantees at least $0.01 whatever happens.

MIN_GUARANTEE = 0.01


def _leg_return(result: str, mult: float) -> float:
    return mult if result == "win" else (1.0 if result == "push" else 0.0)


def _nearby(mode: str, ideal: float, cap: float | None) -> set[float]:
    """Allowed stakes just below and above ideal, within cap."""
    options = {round_stake_down(mode, ideal), round_stake_up(mode, ideal)}
    if cap is not None:
        options.add(round_stake_down(mode, cap))
        options = {o for o in options if o <= cap + EPS}
    return {o for o in options if o > 0}


@lru_cache(maxsize=20000)
def optimize_versions(
    mode: str,
    mults: tuple[float, float],
    caps: tuple[float | None, float | None],
    anchors: tuple[int, ...],
    scenarios: tuple[tuple[str, str], ...],
) -> dict[str, tuple[tuple[float, float], float, float]] | None:
    """Stakes (leg 0, leg 1) for each version, with (guaranteed, possible)
    profit. Anchors are boosted legs whose stake is chosen from their own
    allowed amounts (every allowed amount up to the max bet); the other leg is
    fitted to it. Caps are boost max bets (None for an unboosted hedge)."""
    returns = [(_leg_return(ra, mults[0]), _leg_return(rb, mults[1])) for ra, rb in scenarios]
    tried: dict[tuple[float, float], tuple[float, float]] = {}

    def attempt(stakes: tuple[float, float]) -> None:
        stakes = (round(stakes[0], 2), round(stakes[1], 2))
        if stakes in tried or min(stakes) <= 0:
            return
        for stake, cap in zip(stakes, caps):
            if cap is not None and stake > cap + EPS:
                return
        profits = [ra * stakes[0] + rb * stakes[1] - stakes[0] - stakes[1] for ra, rb in returns]
        tried[stakes] = (min(profits), max(profits))

    def anchor_amounts(i: int) -> list[float]:
        return allowed_stakes_up_to(mode, caps[i])

    def place(i: int, anchor: float, other: float) -> None:
        attempt((anchor, other) if i == 0 else (other, anchor))

    def leaning(i: int, anchor: float, floor: float) -> list[float]:
        """The least and most the other leg can stake while every outcome
        still profits at least `floor`. Possible profit is highest at one of
        these two ends: lean toward whichever bet pays more."""
        j = 1 - i
        least = (anchor + floor) / (mults[j] - 1)  # if only the other leg wins
        most = anchor * (mults[i] - 1) - floor  # if only the anchor leg wins
        if caps[j] is not None:
            most = min(most, caps[j])
        options = [round_stake_up(mode, least), round_stake_down(mode, most)]
        return [o for o in options if o > 0 and (caps[j] is None or o <= caps[j] + EPS)]

    for i in anchors:
        j = 1 - i
        for anchor in anchor_amounts(i):
            for other in _nearby(mode, anchor * mults[i] / mults[j], caps[j]):
                place(i, anchor, other)
            for other in leaning(i, anchor, MIN_GUARANTEE):
                place(i, anchor, other)

    def best(candidates, key):
        return max(candidates, key=key) if candidates else None

    safe = [(s, g, m) for s, (g, m) in tried.items() if g >= MIN_GUARANTEE - EPS]
    safest = best(safe, key=lambda c: (round(c[1], 6), -sum(c[0])))
    if safest is None:
        return None

    floor = max(MIN_GUARANTEE, safest[1] / 2)
    for i in anchors:
        for anchor in anchor_amounts(i):
            for other in leaning(i, anchor, floor):
                place(i, anchor, other)

    safe = [(s, g, m) for s, (g, m) in tried.items() if g >= MIN_GUARANTEE - EPS]
    upside = best(safe, key=lambda c: (round(c[2], 6), round(c[1], 6)))
    balanced = best([c for c in safe if c[1] >= floor - EPS],
                    key=lambda c: (round(c[2], 6), round(c[1], 6)))
    return {"safest": safest, "balanced": balanced or safest, "upside": upside}
