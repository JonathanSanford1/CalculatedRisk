"""Odds conversion, payouts, and bet rounding."""

import math


def american_to_decimal(odds: float) -> float:
    """-110 -> 1.909, +150 -> 2.5"""
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


def payout_multiplier(odds: float, boost_percent: float = 0.0) -> float:
    """Total return per $1 staked (stake included). A profit boost multiplies
    only the winnings, not the stake."""
    winnings = american_to_decimal(odds) - 1
    return 1 + winnings * (1 + boost_percent / 100)


ROUNDING_MODES = ("small", "medium", "large")
DEFAULT_ROUNDING_MODE = "small"
# Values older versions of the app saved. "none" is gone; small replaces it.
LEGACY_ROUNDING_MODES = {"none": "small", "light": "medium", "heavy": "large"}
 
 
def normalize_rounding_mode(value) -> str:
    """The app's saved roundingMode as one of ROUNDING_MODES."""
    if value in ROUNDING_MODES:
        return value
    return LEGACY_ROUNDING_MODES.get(value, DEFAULT_ROUNDING_MODE)
 
#   Stake size:   $0-10     $10-25    $25-50    over $50
#   small         $0.50     $1        $2.50     $5
#   medium        $1        $2.50     $5        $10
#   large         $1        $5        $10       $25
#
# A stake on a boundary ($10, $25, $50) uses the lower tier's step. Every
# boundary is a multiple of its own tier's step, so the boundaries are
# always allowed stakes.
TIER_LIMITS = (10.0, 25.0, 50.0)
ROUNDING_STEPS = {
    "small": (0.5, 1.0, 2.5, 5.0),
    "medium": (1.0, 2.5, 5.0, 10.0),
    "large": (1.0, 5.0, 10.0, 25.0),
}
EPS = 1e-9
 
 
def _tiers(mode: str) -> list[tuple[float, float, float]]:
    """(step, low, high) for each tier: stakes in (low, high] are multiples
    of step."""
    lows = (0.0,) + TIER_LIMITS
    highs = TIER_LIMITS + (math.inf,)
    return list(zip(ROUNDING_STEPS[mode], lows, highs))
 
 
def is_multiple(x: float, step: float) -> bool:
    return abs(x / step - round(x / step)) < 1e-6
 
 
def round_stake_down(mode: str, x: float) -> float:
    """Largest allowed stake <= x (0 if none)."""
    for step, low, high in reversed(_tiers(mode)):
        if x <= low + EPS:
            continue
        v = math.floor(min(x, high) / step + EPS) * step
        if v > low + EPS:
            return round(v, 2)
    return 0.0
 
 
def round_stake_up(mode: str, x: float) -> float:
    """Smallest allowed stake >= x."""
    for step, low, high in _tiers(mode):
        if x > high + EPS:
            continue
        v = math.ceil(x / step - EPS) * step
        if v <= low + EPS:  # x is at or below this tier: first stake in it
            v = (math.floor(low / step + EPS) + 1) * step
        if v <= high + EPS:
            return round(v, 2)
    raise ValueError(f"no allowed stake >= {x}")  # unreachable: last tier is unbounded
 
 
def is_allowed_stake(mode: str, x: float) -> bool:
    if x <= 0:
        return False
    for step, _, high in _tiers(mode):
        if x <= high + EPS:
            return is_multiple(x, step)
    return False
 
 
def allowed_stakes_up_to(mode: str, limit: float) -> list[float]:
    """Every allowed stake from the smallest up to limit, ascending."""
    values, v = [], round_stake_up(mode, 0.01)
    while v <= limit + EPS:
        values.append(v)
        v = round_stake_up(mode, v + 0.001)
    return values
