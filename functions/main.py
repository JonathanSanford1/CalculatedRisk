"""CalculatedRisk Cloud Functions.

Reads the profit boosts each user saves in the app, compares them with live
DraftKings and FanDuel odds from The Odds API, and writes guaranteed-profit
hedges back to Firestore for the app to display.

Firestore layout
  users/{uid}/boosts/{boostId}         written by the app
  users/{uid}/hedge_groups/{groupId}   written here, read by the app
                                       (one doc per boost combination, holding
                                       its best hedges)
  users/{uid}/meta/status              written here, read by the app
  users/{uid}/meta/plan                written here: the best way to use the boosts
                                       (for each rounding mode)
  users/{uid}/settings/preferences     written by the app: selected rounding mode
  odds_cache/{sportKey}                written and read here only
  odds_cache/_sports                   the API's league list, cached
  odds_cache/_quota                    credits used/remaining this month

Functions
  scheduled_refresh       every 2 hours, for every user with boosts
  on_boost_changed        whenever a boost is added, edited, or deleted
  refresh_opportunities   callable from the app ("check now" / pull to refresh)
"""


import hashlib
import math
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from firebase_admin import firestore, initialize_app
from firebase_functions import firestore_fn, https_fn, options, scheduler_fn
from firebase_functions.params import SecretParam

initialize_app()
options.set_global_options(max_instances=10)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# The Odds API key lives in Google Cloud Secret Manager, not in this file.
# Set it with:  firebase functions:secrets:set ODDS_API_KEY
# Every function that can call the API lists it in secrets=[...] below, and
# the key is read with ODDS_API_KEY.value only while a function is running.
ODDS_API_KEY = SecretParam("ODDS_API_KEY")
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORTS_LIST_URL = "https://api.the-odds-api.com/v4/sports"
BOOKMAKERS = ("draftkings", "fanduel")
MARKETS = "h2h,spreads,totals"  # moneyline, spreads, over/under

# Odds younger than this are reused instead of calling the API again.
# Protects your API quota when boosts are added quickly or refresh is tapped often.
ODDS_CACHE_MINUTES = 20

STAKE_INCREMENT = 0.50  # hedge stakes are rounded to the nearest 50 cents

# How many hedges to keep per boost combination (the app shows the top 5;
# the rest are there so searching can find more).
MAX_BETS_PER_GROUP = 25
SPORTS_LIST_CACHE_HOURS = 6

# App league (BetType.name in Dart) -> The Odds API sport keys.
# A key ending in "*" matches every key with that prefix (tennis tournaments
# change weekly, so they're matched by prefix). Leagues missing here (golf,
# motorsports, "other soccer", other) are saved in the app but not checked.
# Only leagues currently in season are fetched, using the API's free sports list.
LEAGUE_KEYS = {
    # Football
    "nfl": ["americanfootball_nfl"],
    "ncaaf": ["americanfootball_ncaaf"],
    "cfl": ["americanfootball_cfl"],
    "ufl": ["americanfootball_ufl"],
    # Basketball
    "nba": ["basketball_nba"],
    "wnba": ["basketball_wnba"],
    "ncaab": ["basketball_ncaab"],
    "wncaab": ["basketball_wncaab"],
    "euroleague": ["basketball_euroleague"],
    # Baseball
    "mlb": ["baseball_mlb"],
    "collegeBaseball": ["baseball_ncaa"],
    "kbo": ["baseball_kbo"],
    "npb": ["baseball_npb"],
    # Hockey
    "nhl": ["icehockey_nhl"],
    "ahl": ["icehockey_ahl"],
    "shl": ["icehockey_sweden_hockey_league"],
    "liiga": ["icehockey_liiga"],
    # Soccer (moneylines have a draw, so only spreads and totals are hedged)
    "epl": ["soccer_epl"],
    "efl": ["soccer_efl_champ"],
    "laLiga": ["soccer_spain_la_liga"],
    "serieA": ["soccer_italy_serie_a"],
    "bundesliga": ["soccer_germany_bundesliga"],
    "ligue1": ["soccer_france_ligue_one"],
    "ucl": ["soccer_uefa_champs_league"],
    "uel": ["soccer_uefa_europa_league"],
    "mls": ["soccer_usa_mls"],
    "ligaMx": ["soccer_mexico_ligamx"],
    "eredivisie": ["soccer_netherlands_eredivisie"],
    "worldCup": ["soccer_fifa_world_cup"],
    # Tennis
    "atp": ["tennis_atp*"],
    "wta": ["tennis_wta*"],
    "tennis": ["tennis_*"],
    # Combat sports
    "mma": ["mma_mixed_martial_arts"],
    "boxing": ["boxing_boxing"],
    # Other
    "afl": ["aussierules_afl"],
    "nrl": ["rugbyleague_nrl"],
    "cricket": ["cricket_*"],
}

_db = None


def db():
    """Firestore client, created on first use (not at import time)."""
    global _db
    if _db is None:
        _db = firestore.client()
    return _db


# --------------------------------------------------------------------------
# Odds math
# --------------------------------------------------------------------------


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


def round_stake(amount: float, cap: float | None = None) -> float:
    """Round to the nearest STAKE_INCREMENT without exceeding cap."""
    rounded = round(amount / STAKE_INCREMENT) * STAKE_INCREMENT
    if cap is not None and rounded > cap:
        rounded = math.floor(cap / STAKE_INCREMENT) * STAKE_INCREMENT
    return round(rounded, 2)


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Boost:
    id: str
    bookmaker: str  # "draftkings" or "fanduel"
    percent: float
    bet_type: str  # league, e.g. "nfl", "epl", "atp"
    min_odds: int
    max_odds: int
    valid_from: datetime
    valid_until: datetime
    max_bet: float
    nickname: str | None = None
    used: bool = False  # the user marked this boost as already used

    def is_active(self, now: datetime) -> bool:
        return self.valid_from <= now <= self.valid_until

    def applies_to(self, bookmaker: str, bet_type: str, odds: int, now: datetime) -> bool:
        return (
            self.bookmaker == bookmaker
            and self.bet_type == bet_type
            and self.is_active(now)
            and self.min_odds <= odds <= self.max_odds
        )

    def summary(self) -> dict[str, Any]:
        """What the app needs to label this boost on a hedge card."""
        return {
            "id": self.id,
            "bookmaker": self.bookmaker,
            "nickname": self.nickname,
            "percentBoost": self.percent,
            "betType": self.bet_type,
            "maxBet": self.max_bet,
            "used": self.used,
        }


@dataclass(frozen=True)
class Leg:
    bookmaker: str
    selection: str  # readable, e.g. "Kansas City Chiefs -3.5"
    odds: int


@dataclass(frozen=True)
class Matchup:
    """Two bets at different books that cover opposite outcomes exactly."""

    bet_type: str
    game: str
    commence_time: datetime
    market: str
    leg_a: Leg  # DraftKings side
    leg_b: Leg  # FanDuel side


class OddsApiError(Exception):
    pass


# --------------------------------------------------------------------------
# Reading boosts saved by the app
# --------------------------------------------------------------------------


def _to_utc(value: Any) -> datetime | None:
    """Firestore timestamps arrive as timezone-aware datetimes. Early test
    boosts stored local-time strings with no timezone; those can't be placed
    in time reliably, so they return None and get skipped."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    return None


def parse_boost(doc_id: str, data: dict[str, Any]) -> Boost | None:
    bookmaker = data.get("bookmaker") or {
        "green": "draftkings",
        "blue": "fanduel",
    }.get(data.get("section", ""))
    valid_from = _to_utc(data.get("validFrom"))
    valid_until = _to_utc(data.get("validUntil"))
    if bookmaker not in BOOKMAKERS or valid_from is None or valid_until is None:
        return None
    nickname = data.get("nickname")
    try:
        return Boost(
            id=doc_id,
            bookmaker=bookmaker,
            percent=float(data["percentBoost"]),
            bet_type=str(data["betType"]),
            min_odds=int(data["minOdds"]),
            max_odds=int(data["maxOdds"]),
            valid_from=valid_from,
            valid_until=valid_until,
            max_bet=float(data["maxBet"]),
            nickname=nickname.strip() if isinstance(nickname, str) and nickname.strip() else None,
            used=bool(data.get("used", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Leagues currently in season (free API call, cached)
# --------------------------------------------------------------------------


def _record_quota(headers: Any) -> None:
    """Save the credits The Odds API reports in every response's headers, so
    the app can show how much of the monthly quota is left."""
    remaining = headers.get("x-requests-remaining")
    if remaining is None:
        return
    try:
        data: dict[str, Any] = {
            "remaining": int(float(remaining)),
            "updatedAt": datetime.now(timezone.utc),
        }
        used = headers.get("x-requests-used")
        if used is not None:
            data["used"] = int(float(used))
    except (TypeError, ValueError):
        return
    db().collection("odds_cache").document("_quota").set(data)


def load_active_sport_keys(memo: dict[str, Any]) -> set[str]:
    """Sport keys The Odds API currently has games for, excluding
    futures/outright markets. This call doesn't count against the quota."""
    if "_sports" in memo:
        return memo["_sports"]

    cache_ref = db().collection("odds_cache").document("_sports")
    snapshot = cache_ref.get()
    if snapshot.exists:
        cached = snapshot.to_dict() or {}
        fetched_at = cached.get("fetchedAt")
        if fetched_at and datetime.now(timezone.utc) - fetched_at < timedelta(
            hours=SPORTS_LIST_CACHE_HOURS
        ):
            memo["_sports"] = set(cached.get("keys", []))
            return memo["_sports"]

    try:
        response = requests.get(SPORTS_LIST_URL, params={"apiKey": ODDS_API_KEY.value}, timeout=20)
    except requests.RequestException as error:
        raise OddsApiError(f"Couldn't reach The Odds API: {error}") from error
    _record_quota(response.headers)
    if response.status_code != 200:
        raise OddsApiError(
            f"The Odds API returned {response.status_code} for the league list: "
            f"{response.text[:200]}"
        )

    keys = sorted(
        sport["key"]
        for sport in response.json()
        if sport.get("active") and not sport.get("has_outrights")
    )
    cache_ref.set({"fetchedAt": datetime.now(timezone.utc), "keys": keys})
    memo["_sports"] = set(keys)
    return memo["_sports"]


def resolve_sport_keys(bet_type: str, active_keys: set[str]) -> list[str]:
    """In-season API sport keys for one app league."""
    matched = set()
    for pattern in LEAGUE_KEYS.get(bet_type, []):
        if pattern.endswith("*"):
            matched.update(k for k in active_keys if k.startswith(pattern[:-1]))
        elif pattern in active_keys:
            matched.add(pattern)
    return sorted(matched)


# --------------------------------------------------------------------------
# Fetching odds (with a shared Firestore cache)
# --------------------------------------------------------------------------


def _parse_game(game: dict[str, Any]) -> dict[str, Any]:
    """Keep only what we need from one API game, grouped by book and market."""
    books: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for bookmaker in game.get("bookmakers", []):
        key = bookmaker.get("key")
        if key not in BOOKMAKERS:
            continue
        markets: dict[str, list[dict[str, Any]]] = {}
        for market in bookmaker.get("markets", []):
            markets[market.get("key")] = [
                {
                    "name": outcome.get("name"),
                    "price": outcome.get("price"),
                    "point": outcome.get("point"),
                }
                for outcome in market.get("outcomes", [])
            ]
        books[key] = markets
    return {
        "home": game.get("home_team"),
        "away": game.get("away_team"),
        "commenceTime": game.get("commence_time"),
        "books": books,
    }


def fetch_odds(sport_key: str) -> tuple[list[dict[str, Any]], str | None]:
    """One API call returns both books for every upcoming game in a league."""
    try:
        response = requests.get(
            ODDS_API_URL.format(sport=sport_key),
            params={
                "apiKey": ODDS_API_KEY.value,
                "markets": MARKETS,
                "oddsFormat": "american",
                "bookmakers": ",".join(BOOKMAKERS),
            },
            timeout=20,
        )
    except requests.RequestException as error:
        raise OddsApiError(f"Couldn't reach The Odds API: {error}") from error

    _record_quota(response.headers)
    if response.status_code != 200:
        raise OddsApiError(
            f"The Odds API returned {response.status_code} for {sport_key}: "
            f"{response.text[:200]}"
        )

    remaining = response.headers.get("x-requests-remaining")
    print(f"Fetched {sport_key} odds. API requests remaining: {remaining}")
    return [_parse_game(game) for game in response.json()], remaining


def load_games(
    sport_key: str, memo: dict[str, Any], allow_fetch: bool = True
) -> tuple[list[dict[str, Any]], str | None]:
    """Games for a league: from this run's memo, else Firestore cache, else the
    API. With allow_fetch=False the API is never called (no credits spent):
    cached odds of any age are used, or none."""
    if sport_key in memo:
        return memo[sport_key]

    cache_ref = db().collection("odds_cache").document(sport_key)
    snapshot = cache_ref.get()
    if snapshot.exists:
        cached = snapshot.to_dict() or {}
        fetched_at = cached.get("fetchedAt")
        fresh = fetched_at and datetime.now(timezone.utc) - fetched_at < timedelta(
            minutes=ODDS_CACHE_MINUTES
        )
        if fresh or not allow_fetch:
            memo[sport_key] = (cached.get("games", []), cached.get("requestsRemaining"))
            return memo[sport_key]

    if not allow_fetch:
        return [], None

    games, remaining = fetch_odds(sport_key)
    cache_ref.set(
        {
            "fetchedAt": datetime.now(timezone.utc),
            "games": games,
            "requestsRemaining": remaining,
        }
    )
    memo[sport_key] = (games, remaining)
    return memo[sport_key]


# --------------------------------------------------------------------------
# Bet rounding: round stakes so they look like ordinary bets
# --------------------------------------------------------------------------
#
#              under $10    $10 to $50    over $50
#   light        $0.50         $1            $5
#   heavy        $1            $2            $10
#
# "none" keeps the original behavior: bet the boost's max, round the hedge to
# the nearest 50 cents. Light and heavy apply to every stake (boosted and
# hedge), and the stakes are chosen to maximize guaranteed profit under those
# rules, which can mean betting a little under the boost's max.

ROUNDING_MODES = ("none", "light", "heavy")
ROUNDING_STEPS = {  # mode: (step under $10, step $10-$50, step over $50)
    "light": (0.5, 1.0, 5.0),
    "heavy": (1.0, 2.0, 10.0),
}
_EPS = 1e-9


def _is_multiple(x: float, step: float) -> bool:
    return abs(x / step - round(x / step)) < 1e-6


def round_stake_down(mode: str, x: float) -> float:
    """Largest allowed stake <= x (0 if none)."""
    small, mid, large = ROUNDING_STEPS[mode]
    if x > 50:
        v = math.floor(x / large + _EPS) * large
        if v > 50:
            return round(v, 2)
        x = 50.0
    if x >= 10:
        v = math.floor(x / mid + _EPS) * mid
        if v >= 10:
            return round(v, 2)
        x = 10 - _EPS
    return round(max(0.0, math.floor(x / small + _EPS) * small), 2)


def round_stake_up(mode: str, x: float) -> float:
    """Smallest allowed stake >= x."""
    small, mid, large = ROUNDING_STEPS[mode]
    if x < 10:
        v = max(small, math.ceil(x / small - _EPS) * small)
        if v < 10:
            return round(v, 2)
        return 10.0
    if x <= 50:
        v = math.ceil(x / mid - _EPS) * mid
        if v <= 50:
            return round(v, 2)
    v = math.ceil(max(x, 50 + _EPS) / large - _EPS) * large
    if v <= 50:
        v += large
    return round(v, 2)


def is_allowed_stake(mode: str, x: float) -> bool:
    small, mid, large = ROUNDING_STEPS[mode]
    if x <= 0:
        return False
    if x < 10:
        return _is_multiple(x, small)
    if x <= 50:
        return _is_multiple(x, mid)
    return _is_multiple(x, large)


def allowed_stakes_up_to(mode: str, limit: float) -> list[float]:
    """Every allowed stake from the smallest up to limit, ascending."""
    values, v = [], round_stake_up(mode, 0.01)
    while v <= limit + _EPS:
        values.append(v)
        v = round_stake_up(mode, v + 0.001)
    return values


def _hedge_candidates(mode: str, ideal: float, cap: float | None = None) -> set[float]:
    """Allowed stakes just below and above the ideal hedge (within cap)."""
    options = {round_stake_down(mode, ideal), round_stake_up(mode, ideal)}
    if cap is not None:
        options.add(round_stake_down(mode, cap))
        options = {o for o in options if o <= cap + _EPS}
    return {o for o in options if o > 0}


def _profit(stakes: list[float], mults: list[float]) -> float:
    return min(s * m for s, m in zip(stakes, mults)) - sum(stakes)


def best_rounded_one_way(
    mode: str, boosted_mult: float, hedge_mult: float, max_bet: float
) -> tuple[float, float] | None:
    """(boosted stake, hedge stake) with the most guaranteed profit, all
    stakes allowed under the rounding mode and boosted stake <= max_bet."""
    best, best_key = None, None
    for stake in allowed_stakes_up_to(mode, max_bet):
        for hedge in _hedge_candidates(mode, stake * boosted_mult / hedge_mult):
            profit = _profit([stake, hedge], [boosted_mult, hedge_mult])
            key = (round(profit, 6), -(stake + hedge))  # then prefer less staked
            if best_key is None or key > best_key:
                best, best_key = (stake, hedge), key
    return best


def best_rounded_two_way(
    mode: str, mult_a: float, mult_b: float, max_a: float, max_b: float
) -> tuple[float, float] | None:
    """(stake a, stake b) with the most guaranteed profit, both within their
    boost's max bet and allowed under the rounding mode."""
    best, best_key = None, None
    for stake_a in allowed_stakes_up_to(mode, max_a):
        for stake_b in _hedge_candidates(mode, stake_a * mult_a / mult_b, cap=max_b):
            profit = _profit([stake_a, stake_b], [mult_a, mult_b])
            key = (round(profit, 6), -(stake_a + stake_b))
            if best_key is None or key > best_key:
                best, best_key = (stake_a, stake_b), key
    return best


# --------------------------------------------------------------------------
# Finding hedges
# --------------------------------------------------------------------------


def _describe(market: str, outcome: dict[str, Any]) -> str:
    name, point = outcome["name"], outcome.get("point")
    if market == "h2h":
        return f"{name} moneyline"
    if market == "spreads":
        return f"{name} {point:+g}"
    return f"{name} {point:g}"  # totals: "Over 47.5"


def _are_opposites(market: str, a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True only when exactly one of the two bets must win (ignoring pushes,
    which refund both bets). Bets on different lines are never paired: a
    -3.5 vs +3 hedge can lose both sides."""
    if a.get("price") is None or b.get("price") is None:
        return False
    if market == "h2h":
        return a["name"] != b["name"]
    point_a, point_b = a.get("point"), b.get("point")
    if point_a is None or point_b is None:
        return False
    if market == "spreads":
        return a["name"] != b["name"] and abs(point_a + point_b) < 1e-9
    if market == "totals":
        return {a["name"], b["name"]} == {"Over", "Under"} and abs(point_a - point_b) < 1e-9
    return False


def build_matchups(game: dict[str, Any], bet_type: str) -> list[Matchup]:
    try:
        commence = datetime.fromisoformat(game["commenceTime"])
    except (KeyError, TypeError, ValueError):
        return []

    draftkings = game.get("books", {}).get("draftkings", {})
    fanduel = game.get("books", {}).get("fanduel", {})
    label = f"{game.get('away')} @ {game.get('home')}"
    matchups = []

    for market in ("h2h", "spreads", "totals"):
        dk_outcomes = draftkings.get(market, [])
        fd_outcomes = fanduel.get(market, [])
        # Moneylines with a draw option can't be fully hedged with two bets.
        if market == "h2h" and (len(dk_outcomes) != 2 or len(fd_outcomes) != 2):
            continue
        for dk in dk_outcomes:
            for fd in fd_outcomes:
                if _are_opposites(market, dk, fd):
                    matchups.append(
                        Matchup(
                            bet_type=bet_type,
                            game=label,
                            commence_time=commence,
                            market=market,
                            leg_a=Leg("draftkings", _describe(market, dk), int(dk["price"])),
                            leg_b=Leg("fanduel", _describe(market, fd), int(fd["price"])),
                        )
                    )
    return matchups


def _build_result(
    matchup: Matchup,
    kind: str,
    legs: list[tuple[Leg, float, float, Boost | None]],
) -> dict[str, Any] | None:
    """legs: (leg, stake, payout multiplier, boost or None)."""
    if any(stake <= 0 for _, stake, _, _ in legs):
        return None
    total_stake = sum(stake for _, stake, _, _ in legs)
    payouts = [stake * mult for _, stake, mult, _ in legs]
    profit = min(payouts) - total_stake
    result = {
        "betType": matchup.bet_type,
        "game": matchup.game,
        "commenceTime": matchup.commence_time,
        "market": matchup.market,
        "type": kind,
        "boostIds": sorted(boost.id for _, _, _, boost in legs if boost),
        "legs": [
            {
                "bookmaker": leg.bookmaker,
                "selection": leg.selection,
                "odds": leg.odds,
                "stake": round(stake, 2),
                "payout": round(stake * mult, 2),
                "boostPercent": boost.percent if boost else 0,
                "boostId": boost.id if boost else None,
            }
            for leg, stake, mult, boost in legs
        ],
        "totalStake": round(total_stake, 2),
        "guaranteedProfit": round(profit, 2),
        "roiPercent": round(profit / total_stake * 100, 2),
    }
    result["id"] = _opportunity_id(result)
    return result


def one_way(
    matchup: Matchup, boosted: Leg, hedge: Leg, boost: Boost, mode: str = "none"
) -> dict[str, Any] | None:
    """Boost one side, hedge the other side unboosted."""
    boosted_mult = payout_multiplier(boosted.odds, boost.percent)
    hedge_mult = payout_multiplier(hedge.odds)
    if mode == "none":
        # Original behavior: bet the max, hedge to the nearest 50 cents.
        stake = round(boost.max_bet, 2)
        hedge_stake = round_stake(stake * boosted_mult / hedge_mult)
    else:
        stakes = best_rounded_one_way(mode, boosted_mult, hedge_mult, boost.max_bet)
        if stakes is None:
            return None
        stake, hedge_stake = stakes
    return _build_result(
        matchup,
        "one_way",
        [(boosted, stake, boosted_mult, boost), (hedge, hedge_stake, hedge_mult, None)],
    )


def two_way(
    matchup: Matchup, boost_a: Boost, boost_b: Boost, mode: str = "none"
) -> dict[str, Any] | None:
    """Both sides boosted. Balance payouts while keeping each stake within its
    boost's max bet (the boost doesn't apply above it)."""
    a, b = matchup.leg_a, matchup.leg_b
    mult_a = payout_multiplier(a.odds, boost_a.percent)
    mult_b = payout_multiplier(b.odds, boost_b.percent)

    if mode != "none":
        stakes = best_rounded_two_way(mode, mult_a, mult_b, boost_a.max_bet, boost_b.max_bet)
        if stakes is None:
            return None
        stake_a, stake_b = stakes
    else:
        stake_a = round(boost_a.max_bet, 2)
        needed_b = stake_a * mult_a / mult_b
        if needed_b <= boost_b.max_bet:
            stake_b = round_stake(needed_b, cap=boost_b.max_bet)
        else:
            stake_b = round(boost_b.max_bet, 2)
            stake_a = round_stake(stake_b * mult_b / mult_a, cap=boost_a.max_bet)

    return _build_result(
        matchup,
        "two_way",
        [(a, stake_a, mult_a, boost_a), (b, stake_b, mult_b, boost_b)],
    )


def find_opportunities(
    matchup: Matchup, boosts: list[Boost], now: datetime, mode: str = "none"
) -> list[dict[str, Any]]:
    a, b = matchup.leg_a, matchup.leg_b
    boosts_a = [x for x in boosts if x.applies_to(a.bookmaker, matchup.bet_type, a.odds, now)]
    boosts_b = [x for x in boosts if x.applies_to(b.bookmaker, matchup.bet_type, b.odds, now)]

    candidates = [one_way(matchup, a, b, boost, mode) for boost in boosts_a]
    candidates += [one_way(matchup, b, a, boost, mode) for boost in boosts_b]
    candidates += [two_way(matchup, x, y, mode) for x in boosts_a for y in boosts_b]
    return [c for c in candidates if c and c["guaranteedProfit"] > 0]


def _opportunity_id(opp: dict[str, Any]) -> str:
    parts = [opp["game"], opp["commenceTime"].isoformat(), opp["market"]]
    parts += [f"{leg['bookmaker']}:{leg['selection']}:{leg['boostId']}" for leg in opp["legs"]]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:24]


def group_opportunities(
    opportunities: list[dict[str, Any]], boosts_by_id: dict[str, Boost]
) -> dict[str, dict[str, Any]]:
    """One group per boost combination (a single boost, or a DraftKings +
    FanDuel pair), holding that combination's best hedges."""
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for opp in opportunities:
        buckets.setdefault(tuple(opp["boostIds"]), []).append(opp)

    groups = {}
    for boost_ids, bets in buckets.items():
        bets.sort(key=lambda o: o["guaranteedProfit"], reverse=True)
        boosts = sorted(
            (boosts_by_id[i] for i in boost_ids),
            key=lambda b: BOOKMAKERS.index(b.bookmaker),  # DraftKings first
        )
        group_id = hashlib.sha1("|".join(boost_ids).encode()).hexdigest()[:24]
        groups[group_id] = {
            "type": "two_way" if len(boost_ids) == 2 else "one_way",
            "boostIds": list(boost_ids),
            "boosts": [b.summary() for b in boosts],
            "usedBoostIds": [b.id for b in boosts if b.used],
            "bestProfit": bets[0]["guaranteedProfit"],
            "betCount": len(bets),
            "bets": bets[:MAX_BETS_PER_GROUP],
        }
    return groups


# --------------------------------------------------------------------------
# Best plan: how to use the boosts for the most total profit
# --------------------------------------------------------------------------
#
# Each unused, active boost can be used once: alone (boost + an unboosted
# hedge) or paired with a boost at the other book on the same bet. Pairing
# A+C can block a better A+D and B+C split, so every possible pairing is
# considered, not just one pair at a time.
#
# Start from "every boost alone" (the sum of each boost's best one-way hedge).
# Pairing DraftKings boost d with FanDuel boost f changes the total by
#     gain = together(d, f) - alone(d) - alone(f)
# so the best plan is the set of non-overlapping pairs with the largest total
# gain (a maximum-weight bipartite matching), solved exactly below.

MAX_EXACT_PLAN_SIDE = 14  # 2^14 states; beyond this, fall back to greedy


def _best_pairing(gains: dict[tuple[str, str], float]) -> list[tuple[str, str]]:
    """Non-overlapping (dk, fd) pairs with the largest total gain."""
    if not gains:
        return []
    dk_ids = sorted({d for d, _ in gains})
    fd_ids = sorted({f for _, f in gains})

    # Bitmask over the smaller side, loop over the larger side.
    flip = len(fd_ids) > len(dk_ids)
    small, large = (dk_ids, fd_ids) if flip else (fd_ids, dk_ids)

    def gain(large_id: str, small_id: str) -> float | None:
        key = (small_id, large_id) if flip else (large_id, small_id)
        return gains.get(key)

    def as_pair(large_id: str, small_id: str) -> tuple[str, str]:
        return (small_id, large_id) if flip else (large_id, small_id)

    if len(small) > MAX_EXACT_PLAN_SIDE:
        chosen, taken = [], set()
        for (d, f), _ in sorted(gains.items(), key=lambda kv: -kv[1]):
            if d not in taken and f not in taken:
                chosen.append((d, f))
                taken.update((d, f))
        return chosen

    best: dict[int, tuple[float, list[tuple[str, str]]]] = {0: (0.0, [])}
    for large_id in large:
        updated = dict(best)
        for mask, (total, pairs) in best.items():
            for bit, small_id in enumerate(small):
                if mask & (1 << bit):
                    continue
                g = gain(large_id, small_id)
                if g is None:
                    continue
                new_mask = mask | (1 << bit)
                new_total = total + g
                if new_mask not in updated or new_total > updated[new_mask][0]:
                    updated[new_mask] = (new_total, pairs + [as_pair(large_id, small_id)])
        best = updated
    return max(best.values(), key=lambda entry: entry[0])[1]


def build_best_plan(
    groups: dict[str, dict[str, Any]], boosts: list[Boost]
) -> dict[str, Any]:
    """Pick, for every unused active boost, whether to use it alone or paired
    (and with which boost) to maximize total guaranteed profit."""
    usable = {b.id: b for b in boosts if not b.used}
    by_ids = {frozenset(g["boostIds"]): (gid, g) for gid, g in groups.items()}

    alone: dict[str, tuple[str, dict[str, Any]]] = {}
    together: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for ids, (gid, group) in by_ids.items():
        if not ids <= usable.keys():
            continue
        if len(ids) == 1:
            alone[next(iter(ids))] = (gid, group)
        else:
            d, f = sorted(ids, key=lambda i: BOOKMAKERS.index(usable[i].bookmaker))
            together[(d, f)] = (gid, group)

    def alone_profit(boost_id: str) -> float:
        return alone[boost_id][1]["bestProfit"] if boost_id in alone else 0.0

    gains = {}
    for (d, f), (_, group) in together.items():
        g = group["bestProfit"] - alone_profit(d) - alone_profit(f)
        if g > 0.004:  # pairing must beat using both alone by at least a cent
            gains[(d, f)] = g
    pairs = _best_pairing(gains)
    paired_ids = {i for pair in pairs for i in pair}

    def step(gid: str, group: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "type": group["type"],
            "groupId": gid,
            "boosts": group["boosts"],
            "bet": group["bets"][0],
            "profit": group["bestProfit"],
            **extra,
        }

    steps = []
    for d, f in pairs:
        gid, group = together[(d, f)]
        steps.append(
            step(gid, group, separateProfit=round(alone_profit(d) + alone_profit(f), 2))
        )

    idle = []
    for boost_id, boost in usable.items():
        if boost_id in paired_ids:
            continue
        if boost_id not in alone:
            idle.append(boost.summary())
            continue
        # Explain why it's alone: its best possible pairing, if any.
        alternative = None
        options = [
            (pair, group["bestProfit"])
            for pair, (_, group) in together.items()
            if boost_id in pair
        ]
        if options:
            (d, f), best_together = max(options, key=lambda o: o[1])
            partner = f if d == boost_id else d
            alternative = {
                "partner": usable[partner].summary(),
                "togetherProfit": best_together,
                "separateProfit": round(alone_profit(boost_id) + alone_profit(partner), 2),
            }
        gid, group = alone[boost_id]
        steps.append(step(gid, group, separateProfit=None, alternative=alternative))

    steps.sort(key=lambda s: s["profit"], reverse=True)
    return {
        "totalProfit": round(sum(s["profit"] for s in steps), 2),
        "allSeparateProfit": round(sum(alone_profit(i) for i in usable), 2),
        "steps": steps,
        "idleBoosts": idle,
    }


# --------------------------------------------------------------------------
# Refreshing one user's results
# --------------------------------------------------------------------------


def _replace_collection(collection, new_docs: dict[str, dict[str, Any]]) -> None:
    """Make the collection contain exactly new_docs (by document ID)."""
    old_ids = [snap.id for snap in collection.stream()]
    operations = [("delete", doc_id, None) for doc_id in old_ids if doc_id not in new_docs]
    operations += [("set", doc_id, data) for doc_id, data in new_docs.items()]

    for start in range(0, len(operations), 400):
        batch = db().batch()
        for op, doc_id, data in operations[start : start + 400]:
            ref = collection.document(doc_id)
            if op == "delete":
                batch.delete(ref)
            else:
                batch.set(ref, {**data, "computedAt": firestore.SERVER_TIMESTAMP})
        batch.commit()


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def refresh_user(uid: str, memo: dict | None = None) -> dict[str, Any]:
    memo = {} if memo is None else memo
    now = datetime.now(timezone.utc)
    user_ref = db().collection("users").document(uid)

    boosts: list[Boost] = []
    unreadable = unsupported = 0
    for snap in user_ref.collection("boosts").stream():
        boost = parse_boost(snap.id, snap.to_dict() or {})
        if boost is None:
            unreadable += 1
        elif boost.bet_type not in LEAGUE_KEYS:
            unsupported += 1
        else:
            boosts.append(boost)

    active = [b for b in boosts if b.is_active(now)]
    # Hedges are computed for every rounding mode, so the app can switch
    # between them instantly (no extra API calls: same odds, different math).
    opportunities: dict[str, list[dict[str, Any]]] = {mode: [] for mode in ROUNDING_MODES}
    errors: list[str] = []
    out_of_season: list[str] = []
    requests_remaining = None

    league_types = sorted({b.bet_type for b in active})
    # Leagues where every active boost is already used don't spend credits;
    # their hedge cards use whatever odds are already cached.
    fetchable = {b.bet_type for b in active if not b.used}
    if league_types:
        try:
            active_keys = load_active_sport_keys(memo)
        except OddsApiError as error:
            print(f"League list error: {error}")
            errors.append(str(error))
            league_types = []

        for bet_type in league_types:
            sport_keys = resolve_sport_keys(bet_type, active_keys)
            if not sport_keys:
                if bet_type in fetchable:
                    out_of_season.append(bet_type)
                continue
            for sport_key in sport_keys:
                try:
                    games, remaining = load_games(
                        sport_key, memo, allow_fetch=bet_type in fetchable
                    )
                    requests_remaining = remaining or requests_remaining
                except OddsApiError as error:
                    print(f"Odds error for {sport_key}: {error}")
                    errors.append(str(error))
                    continue
                for game in games:
                    for matchup in build_matchups(game, bet_type):
                        if matchup.commence_time > now:
                            for mode in ROUNDING_MODES:
                                opportunities[mode].extend(
                                    find_opportunities(matchup, active, now, mode)
                                )

    boosts_by_id = {b.id: b for b in active}
    groups_by_mode = {
        mode: group_opportunities(opportunities[mode], boosts_by_id)
        for mode in ROUNDING_MODES
    }
    plans = {
        mode: build_best_plan(groups_by_mode[mode], active) for mode in ROUNDING_MODES
    }

    # One doc per boost combination. Top-level bets/bestProfit/betCount are the
    # "none" results (so older app versions keep working); "modes" has all three.
    merged: dict[str, dict[str, Any]] = {}
    for mode in ROUNDING_MODES:
        for gid, group in groups_by_mode[mode].items():
            doc = merged.setdefault(
                gid,
                {
                    "type": group["type"],
                    "boostIds": group["boostIds"],
                    "boosts": group["boosts"],
                    "usedBoostIds": group["usedBoostIds"],
                    "bestProfit": 0,
                    "betCount": 0,
                    "bets": [],
                    "modes": {},
                },
            )
            doc["modes"][mode] = {
                "bestProfit": group["bestProfit"],
                "betCount": group["betCount"],
                "bets": group["bets"],
            }
            if mode == "none":
                doc.update(bestProfit=group["bestProfit"], betCount=group["betCount"], bets=group["bets"])
    _replace_collection(user_ref.collection("hedge_groups"), merged)
    _replace_collection(user_ref.collection("opportunities"), {})  # old format, now unused

    user_ref.collection("meta").document("plan").set(
        {**plans["none"], "modes": plans, "computedAt": firestore.SERVER_TIMESTAMP}
    )

    # The status message describes the rounding mode the user has selected.
    settings = user_ref.collection("settings").document("preferences").get().to_dict() or {}
    selected = settings.get("roundingMode")
    selected = selected if selected in ROUNDING_MODES else "none"
    plan = plans[selected]

    if not boosts and not unreadable and not unsupported:
        message = "Add a boost to start finding hedges."
    elif not active:
        message = "None of your boosts are active right now."
    elif not [b for b in active if not b.used]:
        message = "All of your active boosts are marked used."
    elif plan["steps"]:
        message = (
            f"Best plan: ${plan['totalProfit']:.2f} guaranteed from "
            f"{_plural(len(plan['steps']), 'bet')}"
            + ("." if selected == "none" else f" ({selected} rounding).")
        )
    else:
        message = "No profitable hedges with current odds."
    if out_of_season:
        message += f" No upcoming games for: {', '.join(out_of_season)}."
    if unsupported:
        message += f" {_plural(unsupported, 'boost')} not checked (league not supported)."
    if unreadable:
        message += f" {_plural(unreadable, 'boost')} couldn't be read; delete and re-add them."
    if errors:
        message += " Odds error: " + " ".join(errors)

    quota = db().collection("odds_cache").document("_quota").get().to_dict() or {}
    status = {
        "lastRunAt": firestore.SERVER_TIMESTAMP,
        "ok": not errors,
        "message": message,
        "opportunityCount": len(opportunities[selected]),
        "groupCount": len(groups_by_mode[selected]),
        "requestsRemaining": requests_remaining,
        # Odds API credits for the month, shown as a bar in the app.
        "quotaRemaining": quota.get("remaining"),
        "quotaUsed": quota.get("used"),
        "quotaUpdatedAt": quota.get("updatedAt"),
    }
    user_ref.collection("meta").document("status").set(status)
    print(f"Refreshed {uid}: {message}")

    return {
        "ok": not errors,
        "message": message,
        "opportunityCount": len(opportunities[selected]),
    }


def _record_failure(uid: str, error: Exception) -> None:
    print(f"Refresh failed for {uid}: {error!r}")
    try:
        db().collection("users").document(uid).collection("meta").document("status").set(
            {
                "lastRunAt": firestore.SERVER_TIMESTAMP,
                "ok": False,
                "message": f"Refresh failed: {error}",
            },
            merge=True,
        )
    except Exception as write_error:  # noqa: BLE001
        print(f"Couldn't record failure for {uid}: {write_error!r}")


# --------------------------------------------------------------------------
# Cloud Functions
# --------------------------------------------------------------------------


@scheduler_fn.on_schedule(
    # Minute 0 of hours 7, 10, 13, 16, and 19 (7 AM to 7 PM, every 3 hours),
    # Denver time. Daylight saving time is handled automatically.
    schedule="0 7-19/3 * * *",
    timezone=ZoneInfo("America/Denver"),
    timeout_sec=300,
    secrets=[ODDS_API_KEY],
)
def scheduled_refresh(event: scheduler_fn.ScheduledEvent) -> None:
    """Re-check every user who has boosts, 5 times a day. Odds are fetched
    once per league per run (3 credits each)."""
    user_ids = {
        snap.reference.parent.parent.id
        for snap in db().collection_group("boosts").stream()
    }
    memo: dict = {}
    for uid in sorted(user_ids):
        try:
            refresh_user(uid, memo)
        except Exception as error:  # noqa: BLE001 - one user's failure shouldn't stop the rest
            _record_failure(uid, error)


@firestore_fn.on_document_written(
    document="users/{userId}/boosts/{boostId}", timeout_sec=120, secrets=[ODDS_API_KEY]
)
def on_boost_changed(event: firestore_fn.Event) -> None:
    """Recalculate as soon as a boost is added, edited, or deleted in the app."""
    uid = event.params["userId"]
    try:
        refresh_user(uid)
    except Exception as error:  # noqa: BLE001
        _record_failure(uid, error)


@https_fn.on_call(timeout_sec=120, secrets=[ODDS_API_KEY])
def refresh_opportunities(req: https_fn.CallableRequest) -> dict[str, Any]:
    """Called from the app's refresh button and pull-to-refresh."""
    if req.auth is None:
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
            message="Sign in before refreshing.",
        )
    try:
        return refresh_user(req.auth.uid)
    except Exception as error:  # noqa: BLE001
        _record_failure(req.auth.uid, error)
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.INTERNAL,
            message=f"Refresh failed: {error}",
        ) from error
