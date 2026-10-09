"""CalculatedRisk Cloud Functions.

Reads the profit boosts each user saves in the app, compares them with live
DraftKings and FanDuel odds from The Odds API, and writes hedges back to
Firestore for the app to display.

Firestore layout
  users/{uid}/boosts/{boostId}         written by the app (expired ones are
                                       deleted here automatically)
  users/{uid}/hedge_groups/{groupId}   written here: one doc per boost
                                       combination, with its best hedges for
                                       each rounding mode
  users/{uid}/meta/status              written here: last check, messages
  users/{uid}/meta/plan                written here: the best way to use the
                                       boosts, per rounding mode and goal
  users/{uid}/settings/preferences     written by the app: rounding mode and
                                       whether same-sportsbook hedges are allowed
  odds_cache/...                       written and read here only

Functions
  scheduled_refresh       7 AM-7 PM Denver time, every 3 hours
  on_boost_changed        whenever a boost is added, edited, or deleted
  refresh_opportunities   callable from the app (refresh / pull to refresh)
  list_games              callable from the app (game picker, uses no credits)
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable
from zoneinfo import ZoneInfo

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
ODDS_API_KEY = SecretParam("ODDS_API_KEY")
API_BASE = "https://api.the-odds-api.com/v4"
BOOKMAKERS = ("draftkings", "fanduel")
GAME_MARKETS = ("h2h", "spreads", "totals")  # moneyline, spread, over/under

ODDS_CACHE_MINUTES = 20      # odds younger than this are reused (saves credits)
EVENTS_CACHE_MINUTES = 30    # game lists for the game picker (free calls)
SPORTS_LIST_CACHE_HOURS = 6  # the API's list of in-season leagues (free call)
MAX_BETS_PER_LIST = 20       # per boost combination, per ranking goal

# Per-game markets (player props, and soccer's game markets) cost 1 credit per
# market per game. They're fetched only for games inside an unused boost's
# window. To protect the game-line odds, they stop being fetched when fewer
# than PROP_CREDIT_RESERVE credits remain.
FETCH_PLAYER_PROPS = True
PROP_CREDIT_RESERVE = 100

# App league (BetType.name in Dart) -> The Odds API sport keys.
# A key ending in "*" matches every key with that prefix.
LEAGUE_KEYS = {
    "nfl": ["americanfootball_nfl"],
    "ncaaf": ["americanfootball_ncaaf"],
    "cfl": ["americanfootball_cfl"],
    "ufl": ["americanfootball_ufl"],
    "nba": ["basketball_nba"],
    "wnba": ["basketball_wnba"],
    "ncaab": ["basketball_ncaab"],
    "wncaab": ["basketball_wncaab"],
    "euroleague": ["basketball_euroleague"],
    "mlb": ["baseball_mlb"],
    "collegeBaseball": ["baseball_ncaa"],
    "kbo": ["baseball_kbo"],
    "npb": ["baseball_npb"],
    "nhl": ["icehockey_nhl"],
    "ahl": ["icehockey_ahl"],
    "shl": ["icehockey_sweden_hockey_league"],
    "liiga": ["icehockey_liiga"],
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
    "atp": ["tennis_atp*"],
    "wta": ["tennis_wta*"],
    "tennis": ["tennis_*"],
    "mma": ["mma_mixed_martial_arts"],
    "boxing": ["boxing_boxing"],
    "afl": ["aussierules_afl"],
    "nrl": ["rugbyleague_nrl"],
    "cricket": ["cricket_*"],
}

# One player prop market per sport for now (more can be added per league once
# the API plan is upgraded; each extra market costs 1 credit per game).
#
# Soccer also gets two game markets here. DraftKings and FanDuel post only the
# three-way moneyline (with a draw) in the main odds feed for soccer, which
# can't be hedged with two bets, so soccer needs Both Teams to Score (Yes/No)
# and alternate goal totals (Over/Under) from the per-game endpoint.
_FOOTBALL = ["player_reception_yds"]
_BASKETBALL = ["player_points"]
_SOCCER = ["btts", "alternate_totals", "player_shots_on_target"]
EVENT_MARKETS = {
    "nfl": _FOOTBALL, "ncaaf": _FOOTBALL, "cfl": _FOOTBALL,
    "nba": _BASKETBALL, "wnba": _BASKETBALL, "ncaab": _BASKETBALL,
    "mlb": ["pitcher_strikeouts"],
    "nhl": ["player_shots_on_goal"],
    **{league: _SOCCER for league in (
        "epl", "efl", "laLiga", "serieA", "bundesliga", "ligue1", "ucl", "uel",
        "mls", "ligaMx", "eredivisie", "worldCup",
    )},
}
MARKET_LABELS = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
    "btts": "both teams to score",
    "player_reception_yds": "receiving yards",
    "player_points": "points",
    "pitcher_strikeouts": "strikeouts",
    "player_shots_on_goal": "shots on goal",
    "player_shots_on_target": "shots on target",
}

# What a boost can be limited to (PropType.name in Dart) -> the markets it
# covers, as they appear on a matchup (alternate_totals is merged into
# "totals"). A boost with no prop types selected applies to every market.
# Keep in sync with PropType in the app's profit_boost.dart.
PROP_TYPE_MARKETS = {
    "moneyline": ("h2h",),
    "spread": ("spreads",),
    "total": ("totals",),
    "bothTeamsToScore": ("btts",),
    "receivingYards": ("player_reception_yds",),
    "points": ("player_points",),
    "strikeouts": ("pitcher_strikeouts",),
    "shotsOnGoal": ("player_shots_on_goal",),
    "shotsOnTarget": ("player_shots_on_target",),
}

OBJECTIVES = ("guaranteed", "max")  # what hedges and the plan are ranked by

_db = None


def db():
    """Firestore client, created on first use (not at import time)."""
    global _db
    if _db is None:
        _db = firestore.client()
    return _db


class OddsApiError(Exception):
    pass


# --------------------------------------------------------------------------
# Reading boosts saved by the app
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
    event_id: str | None = None  # set when the boost is for one specific game
    event_name: str | None = None
    prop_types: tuple[str, ...] = ()  # limited to these bet types; empty = any

    def is_expired(self, now: datetime) -> bool:
        return now > self.valid_until

    def is_active(self, now: datetime) -> bool:
        """Its window has started and not ended (bets can be placed now)."""
        return self.valid_from <= now <= self.valid_until

    def covers_game(self, event_id: str, commence: datetime, now: datetime) -> bool:
        """The game starts inside this boost's window, hasn't started yet, and
        (for a single-game boost) is that game."""
        return (
            not self.is_expired(now)
            and commence > now
            and self.valid_from <= commence <= self.valid_until
            and (self.event_id is None or self.event_id == event_id)
        )

    def allows_market(self, market: str) -> bool:
        """The boost can be used on this market: any market when no bet types
        were chosen, otherwise only the chosen ones. A bet type this code
        doesn't know allows nothing, so it never matches the wrong bets."""
        if not self.prop_types:
            return True
        return any(market in PROP_TYPE_MARKETS.get(t, ()) for t in self.prop_types)

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
            "validFrom": self.valid_from,
            "validUntil": self.valid_until,
            "eventName": self.event_name,
            "propTypes": list(self.prop_types),
        }


def _to_utc(value: Any) -> datetime | None:
    """Firestore timestamps arrive as timezone-aware datetimes. Early test
    boosts stored local-time strings with no timezone; those are skipped."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    return None


def _clean_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_prop_types(value: Any) -> tuple[str, ...]:
    """The boost's propTypes list; missing, empty, or containing "any" means
    the boost applies to every bet."""
    if not isinstance(value, list):
        return ()
    names = {v for v in value if isinstance(v, str)}
    if "any" in names:
        return ()
    return tuple(sorted(names))


def parse_boost(doc_id: str, data: dict[str, Any]) -> Boost | None:
    bookmaker = data.get("bookmaker") or {
        "green": "draftkings",
        "blue": "fanduel",
    }.get(data.get("section", ""))
    valid_from = _to_utc(data.get("validFrom"))
    valid_until = _to_utc(data.get("validUntil"))
    if bookmaker not in BOOKMAKERS or valid_from is None or valid_until is None:
        return None
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
            nickname=_clean_text(data.get("nickname")),
            used=bool(data.get("used", False)),
            event_id=_clean_text(data.get("eventId")),
            event_name=_clean_text(data.get("eventName")),
            prop_types=_parse_prop_types(data.get("propTypes")),
        )
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# The Odds API: requests, quota, and caching
# --------------------------------------------------------------------------


def _record_quota(headers: Any) -> None:
    """Save the credits The Odds API reports in each response's headers."""
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


def _api_get(path: str, what: str, ok_missing: bool = False, **params: Any) -> Any:
    """GET from The Odds API. With ok_missing, a 404/422 (game gone, or the
    market isn't offered for it) returns None instead of raising."""
    try:
        response = requests.get(
            f"{API_BASE}{path}",
            params={"apiKey": ODDS_API_KEY.value, **params},
            timeout=20,
        )
    except requests.RequestException as error:
        raise OddsApiError(f"Couldn't reach The Odds API: {error}") from error
    _record_quota(response.headers)
    if ok_missing and response.status_code in (404, 422):
        return None
    if response.status_code != 200:
        raise OddsApiError(
            f"The Odds API returned {response.status_code} for {what}: {response.text[:200]}"
        )
    return response.json()


def _cached(
    cache_id: str,
    max_age: timedelta,
    memo: dict[str, Any],
    allow_fetch: bool,
    fetch: Callable[[], Any],
) -> Any:
    """Data from this run's memo, else the Firestore cache if fresh, else
    fetch(). With allow_fetch=False nothing is fetched (no credits spent):
    cached data of any age is used, or None."""
    if cache_id in memo:
        return memo[cache_id]
    ref = db().collection("odds_cache").document(cache_id)
    snapshot = ref.get()
    if snapshot.exists:
        cached = snapshot.to_dict() or {}
        fetched_at = cached.get("fetchedAt")
        fresh = bool(fetched_at) and datetime.now(timezone.utc) - fetched_at < max_age
        if "data" in cached and (fresh or not allow_fetch):
            memo[cache_id] = cached["data"]
            return memo[cache_id]
    if not allow_fetch:
        memo[cache_id] = None
        return None
    data = fetch()
    ref.set({"fetchedAt": datetime.now(timezone.utc), "data": data})
    memo[cache_id] = data
    return data


def load_active_sport_keys(memo: dict[str, Any]) -> set[str]:
    """Sport keys currently in season, excluding futures. Free (no credits)."""

    def fetch() -> list[str]:
        sports = _api_get("/sports", "the league list")
        return sorted(
            s["key"] for s in sports if s.get("active") and not s.get("has_outrights")
        )

    data = _cached("_sports", timedelta(hours=SPORTS_LIST_CACHE_HOURS), memo, True, fetch)
    return set(data or [])


def resolve_sport_keys(bet_type: str, active_keys: set[str]) -> list[str]:
    """In-season API sport keys for one app league."""
    matched = set()
    for pattern in LEAGUE_KEYS.get(bet_type, []):
        if pattern.endswith("*"):
            matched.update(k for k in active_keys if k.startswith(pattern[:-1]))
        elif pattern in active_keys:
            matched.add(pattern)
    return sorted(matched)


def _parse_books(bookmakers: list[dict[str, Any]]) -> dict[str, dict[str, list]]:
    """{book: {market: [outcome, ...]}} for DraftKings and FanDuel only."""
    books: dict[str, dict[str, list]] = {}
    for bookmaker in bookmakers or []:
        key = bookmaker.get("key")
        if key not in BOOKMAKERS:
            continue
        markets = books.setdefault(key, {})
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            outcomes = [
                {
                    "name": o.get("name"),
                    "description": o.get("description"),  # player, for props
                    "price": o.get("price"),
                    "point": o.get("point"),
                }
                for o in market.get("outcomes", [])
            ]
            if market_key == "alternate_totals":
                # Same bet as a total, at more lines: merge, skipping repeats.
                existing = markets.setdefault("totals", [])
                seen = {(o["name"], o["point"]) for o in existing}
                existing.extend(o for o in outcomes if (o["name"], o["point"]) not in seen)
            else:
                markets[market_key] = outcomes
    return books


def _parse_game(game: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": game.get("id"),
        "home": game.get("home_team"),
        "away": game.get("away_team"),
        "commenceTime": game.get("commence_time"),
        "books": _parse_books(game.get("bookmakers", [])),
    }


def load_games(sport_key: str, memo: dict[str, Any], allow_fetch: bool) -> list[dict[str, Any]]:
    """Moneyline, spread, and total odds for every upcoming game in a league.
    One call covers all games: 1 credit per market (3 credits)."""

    def fetch() -> list[dict[str, Any]]:
        games = _api_get(
            f"/sports/{sport_key}/odds", sport_key,
            markets=",".join(GAME_MARKETS), oddsFormat="american",
            bookmakers=",".join(BOOKMAKERS),
        )
        print(f"Fetched {sport_key} game odds ({len(games)} games).")
        return [_parse_game(g) for g in games]

    return _cached(sport_key, timedelta(minutes=ODDS_CACHE_MINUTES), memo, allow_fetch, fetch) or []


def load_event_markets(
    sport_key: str, event_id: str, markets: list[str], memo: dict[str, Any], allow_fetch: bool
) -> dict[str, dict[str, list]]:
    """Per-game markets (props, soccer game markets) for one game. Costs 1
    credit per market the books actually offer for that game."""

    def fetch() -> dict[str, dict[str, list]]:
        event = _api_get(
            f"/sports/{sport_key}/events/{event_id}/odds", f"{', '.join(markets)} for one game",
            ok_missing=True, markets=",".join(markets), oddsFormat="american",
            bookmakers=",".join(BOOKMAKERS),
        )
        return _parse_books(event.get("bookmakers", [])) if event else {}

    cache_id = f"event_{event_id}_{'-'.join(markets)}"
    return _cached(cache_id, timedelta(minutes=ODDS_CACHE_MINUTES), memo, allow_fetch, fetch) or {}


def load_events(sport_key: str, memo: dict[str, Any]) -> list[dict[str, Any]]:
    """Upcoming games in a league, for the game picker. Free (no credits)."""

    def fetch() -> list[dict[str, Any]]:
        events = _api_get(f"/sports/{sport_key}/events", f"{sport_key} games")
        return [
            {
                "id": e.get("id"),
                "home": e.get("home_team"),
                "away": e.get("away_team"),
                "commenceTime": e.get("commence_time"),
            }
            for e in events
        ]

    cache_id = f"events_{sport_key}"
    return _cached(cache_id, timedelta(minutes=EVENTS_CACHE_MINUTES), memo, True, fetch) or []


# --------------------------------------------------------------------------
# Odds math and bet rounding
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
_EPS = 1e-9
 
 
def _tiers(mode: str) -> list[tuple[float, float, float]]:
    """(step, low, high) for each tier: stakes in (low, high] are multiples
    of step."""
    lows = (0.0,) + TIER_LIMITS
    highs = TIER_LIMITS + (math.inf,)
    return list(zip(ROUNDING_STEPS[mode], lows, highs))
 
 
def _is_multiple(x: float, step: float) -> bool:
    return abs(x / step - round(x / step)) < 1e-6
 
 
def round_stake_down(mode: str, x: float) -> float:
    """Largest allowed stake <= x (0 if none)."""
    for step, low, high in reversed(_tiers(mode)):
        if x <= low + _EPS:
            continue
        v = math.floor(min(x, high) / step + _EPS) * step
        if v > low + _EPS:
            return round(v, 2)
    return 0.0
 
 
def round_stake_up(mode: str, x: float) -> float:
    """Smallest allowed stake >= x."""
    for step, low, high in _tiers(mode):
        if x > high + _EPS:
            continue
        v = math.ceil(x / step - _EPS) * step
        if v <= low + _EPS:  # x is at or below this tier: first stake in it
            v = (math.floor(low / step + _EPS) + 1) * step
        if v <= high + _EPS:
            return round(v, 2)
    raise ValueError(f"no allowed stake >= {x}")  # unreachable: last tier is unbounded
 
 
def is_allowed_stake(mode: str, x: float) -> bool:
    if x <= 0:
        return False
    for step, _, high in _tiers(mode):
        if x <= high + _EPS:
            return _is_multiple(x, step)
    return False
 
 
def allowed_stakes_up_to(mode: str, limit: float) -> list[float]:
    """Every allowed stake from the smallest up to limit, ascending."""
    values, v = [], round_stake_up(mode, 0.01)
    while v <= limit + _EPS:
        values.append(v)
        v = round_stake_up(mode, v + 0.001)
    return values


# --------------------------------------------------------------------------
# Bets, and which pairs of bets form a hedge
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Side:
    """One bet you could place."""

    book: str
    market: str  # "h2h", "spreads", "totals", or a player prop market
    kind: str  # "team" (moneyline), "spread", "over", "under", "yes", "no"
    odds: int
    selection: str  # readable, e.g. "Chiefs -3.5", "Travis Kelce Over 62.5"
    team: str | None = None
    point: float | None = None
    player: str | None = None


@dataclass(frozen=True)
class Matchup:
    """Two bets on one game that can't both lose."""

    league: str
    event_id: str
    game: str
    commence_time: datetime
    market: str
    side_a: Side
    side_b: Side
    # Every possible way the two bets can settle: (result a, result b), each
    # "win", "push" (stake refunded), or "lose".
    scenarios: tuple[tuple[str, str], ...]
    middle: str | None  # when both bets win, if they can


def _sides(game: dict[str, Any], book: str, market: str) -> list[Side]:
    outcomes = game.get("books", {}).get(book, {}).get(market, [])
    sides = []
    if market == "h2h":
        # A moneyline with a draw has three outcomes and can't be hedged with
        # two bets, so it's skipped.
        if len(outcomes) != 2:
            return []
        for o in outcomes:
            if o.get("price") is not None and o.get("name"):
                sides.append(Side(book, market, "team", int(o["price"]),
                                  f"{o['name']} moneyline", team=o["name"]))
        return sides
    if market == "btts":
        for o in outcomes:
            if o.get("price") is not None and o.get("name") in ("Yes", "No"):
                sides.append(Side(book, market, o["name"].lower(), int(o["price"]),
                                  f"Both teams to score: {o['name']}"))
        return sides
    for o in outcomes:
        price, point, name = o.get("price"), o.get("point"), o.get("name")
        if price is None or point is None or not name:
            continue
        point = float(point)
        # Quarter lines (-0.25, 2.75) split the bet in two; not supported.
        if not _is_multiple(point, 0.5):
            continue
        if market == "spreads":
            sides.append(Side(book, market, "spread", int(price),
                              f"{name} {point:+g}", team=name, point=point))
        elif name in ("Over", "Under"):
            kind = name.lower()
            if market == "totals":
                sides.append(Side(book, market, kind, int(price), f"{name} {point:g}", point=point))
            else:
                player = o.get("description")
                if player:
                    label = MARKET_LABELS.get(market, market.replace("_", " "))
                    sides.append(Side(book, market, kind, int(price),
                                      f"{player} {name} {point:g} {label}",
                                      point=point, player=player))
    return sides


def _settle_over_under(kind: str, point: float, value: int) -> str:
    if value == point:
        return "push"
    if kind == "over":
        return "win" if value > point else "lose"
    return "win" if value < point else "lose"


def _settle_spread(point: float, margin: int) -> str:
    """margin = this team's score minus the other team's."""
    adjusted = margin + point
    return "win" if adjusted > 0 else ("push" if adjusted == 0 else "lose")


def _join_values(values: list[str]) -> str:
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " or " + values[-1]


def pair_scenarios(a: Side, b: Side) -> tuple[tuple[tuple[str, str], ...], str | None] | None:
    """How bets a and b can settle together, and when both win (a middle).
    None unless every possible result wins at least one of the two bets.

    Lines don't have to match. Over 52.5 with Under 53.5 is kept: every total
    wins one bet, and a total of exactly 53 wins both. Over 53.5 with
    Under 52.5 is rejected: a total of 53 would lose both."""
    if a.market != b.market:
        return None
    results: set[tuple[str, str]] = set()
    both_win: list[int] = []
    if a.kind == "team" and b.kind == "team":
        if a.team == b.team:
            return None
        return (("lose", "win"), ("win", "lose")), None
    if {a.kind, b.kind} == {"yes", "no"}:
        return (("lose", "win"), ("win", "lose")), None
    if a.kind == "spread" and b.kind == "spread":
        if a.team == b.team:
            return None
        reach = int(abs(a.point) + abs(b.point)) + 3
        for margin in range(-reach, reach + 1):  # a's team score minus b's
            outcome = (_settle_spread(a.point, margin), _settle_spread(b.point, -margin))
            results.add(outcome)
            if outcome == ("win", "win"):
                both_win.append(margin)
        middle = None
        if both_win:
            words = []
            a_by = [str(m) for m in both_win if m > 0]
            b_by = [str(-m) for m in both_win if m < 0]
            if a_by:
                words.append(f"{a.team} win by {_join_values(a_by)}")
            if b_by:
                words.append(f"{b.team} win by {_join_values(b_by)}")
            if 0 in both_win:
                words.append("the game ties")
            middle = "Both bets win if " + " or ".join(words)
    elif {a.kind, b.kind} == {"over", "under"} and a.player == b.player:
        low = max(0, math.floor(min(a.point, b.point)) - 2)
        high = math.ceil(max(a.point, b.point)) + 2
        for value in range(low, high + 1):
            outcome = (_settle_over_under(a.kind, a.point, value),
                       _settle_over_under(b.kind, b.point, value))
            results.add(outcome)
            if outcome == ("win", "win"):
                both_win.append(value)
        middle = None
        if both_win:
            numbers = _join_values([str(v) for v in both_win])
            if a.player:
                label = MARKET_LABELS.get(a.market, a.market.replace("_", " "))
                middle = f"Both bets win if {a.player} has exactly {numbers} {label}"
            else:
                middle = f"Both bets win if the total is exactly {numbers}"
    else:
        return None
    if any("win" not in outcome for outcome in results):
        return None  # some result loses both bets (or only refunds them)
    return tuple(sorted(results)), middle


def build_matchups(game: dict[str, Any], league: str, allow_same_book: bool) -> list[Matchup]:
    try:
        commence = datetime.fromisoformat(game["commenceTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    label = f"{game.get('away')} @ {game.get('home')}"
    markets = sorted({m for book in game.get("books", {}).values() for m in book})
    matchups = []
    for market in markets:
        sides = [s for book in BOOKMAKERS for s in _sides(game, book, market)]
        for i in range(len(sides)):
            for j in range(i + 1, len(sides)):
                a, b = sides[i], sides[j]
                if a.book == b.book and not allow_same_book:
                    continue
                paired = pair_scenarios(a, b)
                if paired is None:
                    continue
                scenarios, middle = paired
                matchups.append(Matchup(league, str(game.get("id") or label), label,
                                        commence, market, a, b, scenarios, middle))
    return matchups


# --------------------------------------------------------------------------
# Stakes: three versions of every hedge
# --------------------------------------------------------------------------
#
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
        options = {o for o in options if o <= cap + _EPS}
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
            if cap is not None and stake > cap + _EPS:
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
        return [o for o in options if o > 0 and (caps[j] is None or o <= caps[j] + _EPS)]

    for i in anchors:
        j = 1 - i
        for anchor in anchor_amounts(i):
            for other in _nearby(mode, anchor * mults[i] / mults[j], caps[j]):
                place(i, anchor, other)
            for other in leaning(i, anchor, MIN_GUARANTEE):
                place(i, anchor, other)

    def best(candidates, key):
        return max(candidates, key=key) if candidates else None

    safe = [(s, g, m) for s, (g, m) in tried.items() if g >= MIN_GUARANTEE - _EPS]
    safest = best(safe, key=lambda c: (round(c[1], 6), -sum(c[0])))
    if safest is None:
        return None

    floor = max(MIN_GUARANTEE, safest[1] / 2)
    for i in anchors:
        for anchor in anchor_amounts(i):
            for other in leaning(i, anchor, floor):
                place(i, anchor, other)

    safe = [(s, g, m) for s, (g, m) in tried.items() if g >= MIN_GUARANTEE - _EPS]
    upside = best(safe, key=lambda c: (round(c[2], 6), round(c[1], 6)))
    balanced = best([c for c in safe if c[1] >= floor - _EPS],
                    key=lambda c: (round(c[2], 6), round(c[1], 6)))
    return {"safest": safest, "balanced": balanced or safest, "upside": upside}


# --------------------------------------------------------------------------
# Hedges for a matchup
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Best plan: how to use the boosts for the most total profit
# --------------------------------------------------------------------------
#
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


# --------------------------------------------------------------------------
# Refreshing one user's results
# --------------------------------------------------------------------------

LEAGUE_NAMES = {
    "nfl": "NFL", "ncaaf": "College Football", "cfl": "CFL", "ufl": "UFL",
    "nba": "NBA", "wnba": "WNBA", "ncaab": "Men's College Basketball",
    "wncaab": "Women's College Basketball", "euroleague": "EuroLeague",
    "mlb": "MLB", "collegeBaseball": "College Baseball", "kbo": "KBO", "npb": "NPB",
    "nhl": "NHL", "ahl": "AHL", "shl": "SHL", "liiga": "Liiga",
    "epl": "Premier League", "efl": "EFL Championship", "laLiga": "La Liga",
    "serieA": "Serie A", "bundesliga": "Bundesliga", "ligue1": "Ligue 1",
    "ucl": "Champions League", "uel": "Europa League", "mls": "MLS",
    "ligaMx": "Liga MX", "eredivisie": "Eredivisie", "worldCup": "World Cup",
    "atp": "ATP", "wta": "WTA", "tennis": "Tennis", "mma": "UFC/MMA",
    "boxing": "Boxing", "afl": "AFL", "nrl": "NRL", "cricket": "Cricket",
}


def _commence(game: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(game["commenceTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _only_three_way(game: dict[str, Any]) -> bool:
    """Both books posted nothing but a moneyline with a draw."""
    books = game.get("books", {})
    if not books:
        return False
    for markets in books.values():
        if set(markets) - {"h2h"} or len(markets.get("h2h", [])) not in (0, 3):
            return False
    return True


def _matchup_market(api_market: str) -> str:
    """The market name a fetched per-game market ends up under on a matchup."""
    return "totals" if api_market == "alternate_totals" else api_market


def _wanted_event_markets(
    event_markets: list[str], boosts: list[Boost]
) -> list[str]:
    """The per-game markets that at least one of these boosts can use."""
    return [
        m for m in event_markets
        if any(b.allows_market(_matchup_market(m)) for b in boosts)
    ]


def _with_props(game: dict[str, Any], props: dict[str, dict[str, list]]) -> dict[str, Any]:
    books = {book: dict(markets) for book, markets in game.get("books", {}).items()}
    for book, markets in props.items():
        books.setdefault(book, {}).update(markets)
    return {**game, "books": books}


def _replace_collection(collection, new_docs: dict[str, dict[str, Any]]) -> None:
    """Make the collection contain exactly new_docs (by document ID)."""
    old_ids = [snap.id for snap in collection.stream()]
    operations = [("delete", doc_id, None) for doc_id in old_ids if doc_id not in new_docs]
    operations += [("set", doc_id, data) for doc_id, data in new_docs.items()]
    for start in range(0, len(operations), 400):
        batch = db().batch()
        for op, doc_id, data in operations[start: start + 400]:
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

    settings = user_ref.collection("settings").document("preferences").get().to_dict() or {}
    selected = normalize_rounding_mode(settings.get("roundingMode"))
    allow_same_book = bool(settings.get("allowSameBook", False))

    # Read boosts; delete any whose window has ended.
    boosts: list[Boost] = []
    expired_ids: list[str] = []
    unreadable = unsupported = 0
    for snap in user_ref.collection("boosts").stream():
        boost = parse_boost(snap.id, snap.to_dict() or {})
        if boost is None:
            unreadable += 1
        elif boost.is_expired(now):
            expired_ids.append(snap.id)
        elif boost.bet_type not in LEAGUE_KEYS:
            unsupported += 1
        else:
            boosts.append(boost)
    for start in range(0, len(expired_ids), 400):
        batch = db().batch()
        for boost_id in expired_ids[start: start + 400]:
            batch.delete(user_ref.collection("boosts").document(boost_id))
        batch.commit()

    # Leagues with an unused boost may spend credits; leagues where every boost
    # is used only reuse cached odds (their hedge cards stay visible).
    fetchable = {b.bet_type for b in boosts if not b.used}
    quota = db().collection("odds_cache").document("_quota").get().to_dict() or {}
    remaining = quota.get("remaining")
    props_allowed = FETCH_PLAYER_PROPS and (remaining is None or remaining >= PROP_CREDIT_RESERVE)

    opportunities: dict[str, list[dict[str, Any]]] = {mode: [] for mode in ROUNDING_MODES}
    errors: list[str] = []
    league_notes: list[str] = []
    leagues = sorted({b.bet_type for b in boosts})
    active_keys: set[str] = set()
    if leagues:
        try:
            active_keys = load_active_sport_keys(memo)
        except OddsApiError as error:
            errors.append(str(error))
            leagues = []

    for league in leagues:
        name = LEAGUE_NAMES.get(league, league)
        league_boosts = [b for b in boosts if b.bet_type == league]
        sport_keys = resolve_sport_keys(league, active_keys)
        if not sport_keys:
            if league in fetchable:
                league_notes.append(f"{name}: no upcoming games (out of season).")
            continue
        in_window = three_way_only = pairs = hedges = 0
        for sport_key in sport_keys:
            try:
                games = load_games(sport_key, memo, allow_fetch=league in fetchable)
            except OddsApiError as error:
                errors.append(str(error))
                continue
            for game in games:
                commence, event_id = _commence(game), str(game.get("id") or "")
                if commence is None:
                    continue
                fits = [b for b in league_boosts if b.covers_game(event_id, commence, now)]
                if not fits:
                    continue  # not inside any boost's window (or not its game)
                in_window += 1
                event_markets = EVENT_MARKETS.get(league)
                if event_markets and props_allowed and event_id:
                    # Credits are spent only on markets an unused boost can use.
                    # Markets only a used boost wants come from the cache alone,
                    # so that boost's hedge cards stay visible.
                    fetch_now = _wanted_event_markets(
                        event_markets, [b for b in fits if not b.used])
                    cache_only = [m for m in _wanted_event_markets(event_markets, fits)
                                  if m not in fetch_now]
                    for markets, may_fetch in ((fetch_now, league in fetchable),
                                               (cache_only, False)):
                        if not markets:
                            continue
                        try:
                            props = load_event_markets(sport_key, event_id, markets, memo, may_fetch)
                        except OddsApiError as error:
                            errors.append(str(error))
                            props = {}
                        if props:
                            game = _with_props(game, props)
                if _only_three_way(game):
                    three_way_only += 1
                matchups = build_matchups(game, league, allow_same_book)
                pairs += len(matchups)
                for matchup in matchups:
                    for mode in ROUNDING_MODES:
                        found = find_opportunities(matchup, fits, now, mode)
                        opportunities[mode].extend(found)
                        if mode == selected:
                            hedges += len(found)
        print(f"{name}: {in_window} games in boost windows, {pairs} hedgeable bet pairs, "
              f"{hedges} profitable hedges ({selected} rounding).")
        if league in fetchable and hedges == 0:
            if in_window == 0:
                league_notes.append(f"{name}: no games start inside your boost windows.")
            elif three_way_only == in_window:
                league_notes.append(
                    f"{name}: {_plural(in_window, 'game')} checked, but DraftKings and FanDuel "
                    "only posted moneylines with a draw (no Both Teams to Score, totals, or "
                    "props yet), which can't be hedged with two bets.")
            elif pairs == 0:
                league_notes.append(
                    f"{name}: {_plural(in_window, 'game')} checked; no bets at the two "
                    "sportsbooks cover each other.")
            else:
                limits = ("odds ranges, bet types," if any(b.prop_types for b in league_boosts)
                          else "odds ranges")
                league_notes.append(
                    f"{name}: {_plural(pairs, 'bet pair')} checked; none guarantee a profit "
                    f"with your boosts' {limits} and current odds.")

    boosts_by_id = {b.id: b for b in boosts}
    groups_by_mode = {
        mode: group_opportunities(opportunities[mode], boosts_by_id, now) for mode in ROUNDING_MODES
    }
    plans = {
        mode: {obj: build_best_plan(groups_by_mode[mode], boosts, now, obj) for obj in OBJECTIVES}
        for mode in ROUNDING_MODES
    }

    # One doc per boost combination, with results for every rounding mode.
    # Top-level bets/bestProfit/betCount are "small" (for older app versions).
    merged: dict[str, dict[str, Any]] = {}
    for mode in ROUNDING_MODES:
        for gid, group in groups_by_mode[mode].items():
            doc = merged.setdefault(gid, {
                key: group[key] for key in
                ("type", "boostIds", "boosts", "usedBoostIds", "upcoming", "availableFrom")
            } | {"bestProfit": 0, "betCount": 0, "bets": [], "modes": {}})
            doc["modes"][mode] = {
                key: group[key] for key in ("bestProfit", "bestMaxProfit", "betCount", "bets")
            }
            if mode == DEFAULT_ROUNDING_MODE:
                doc.update(bestProfit=group["bestProfit"], betCount=group["betCount"],
                           bets=group["bets"])
    _replace_collection(user_ref.collection("hedge_groups"), merged)

    user_ref.collection("meta").document("plan").set({
        **plans[DEFAULT_ROUNDING_MODE]["guaranteed"],
        "modes": {mode: plans[mode]["guaranteed"] for mode in ROUNDING_MODES},  # older apps
        "plans": plans,
        "computedAt": firestore.SERVER_TIMESTAMP,
    })

    plan = plans[selected]["guaranteed"]
    unused_active = [b for b in boosts if not b.used and b.is_active(now)]
    if not boosts and not unreadable and not unsupported:
        message = "Add a boost to start finding hedges."
    elif boosts and not [b for b in boosts if not b.used]:
        message = "All of your boosts are marked used."
    elif plan["steps"]:
        message = (f"Best plan: ${plan['totalProfit']:.2f} guaranteed from "
                   f"{_plural(len(plan['steps']), 'bet')}"
                   + f" ({selected} rounding).")
    elif not unused_active:
        message = "Your boosts' windows haven't opened yet; their hedges are shown in advance."
    else:
        message = "No profitable hedges with current odds."
    if league_notes:
        message += " " + " ".join(league_notes)
    if expired_ids:
        message += f" Removed {_plural(len(expired_ids), 'expired boost')}."
    if unsupported:
        message += f" {_plural(unsupported, 'boost')} not checked (league not supported)."
    if unreadable:
        message += f" {_plural(unreadable, 'boost')} couldn't be read; delete and re-add them."
    if not props_allowed and FETCH_PLAYER_PROPS:
        message += (f" Player props and soccer game markets paused: under "
                    f"{PROP_CREDIT_RESERVE} API credits left.")
    if errors:
        message += " Odds error: " + " ".join(dict.fromkeys(errors))

    quota = db().collection("odds_cache").document("_quota").get().to_dict() or {}
    user_ref.collection("meta").document("status").set({
        "lastRunAt": firestore.SERVER_TIMESTAMP,
        "ok": not errors,
        "message": message,
        "opportunityCount": len(opportunities[selected]),
        "groupCount": len(groups_by_mode[selected]),
        "quotaRemaining": quota.get("remaining"),
        "quotaUsed": quota.get("used"),
        "quotaUpdatedAt": quota.get("updatedAt"),
    })
    print(f"Refreshed {uid}: {message}")
    return {"ok": not errors, "message": message,
            "opportunityCount": len(opportunities[selected])}


def _record_failure(uid: str, error: Exception) -> None:
    print(f"Refresh failed for {uid}: {error!r}")
    try:
        db().collection("users").document(uid).collection("meta").document("status").set(
            {"lastRunAt": firestore.SERVER_TIMESTAMP, "ok": False,
             "message": f"Refresh failed: {error}"},
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
    timeout_sec=540,
    memory=options.MemoryOption.GB_1,
    cpu=1,
    secrets=[ODDS_API_KEY],
)
def scheduled_refresh(event: scheduler_fn.ScheduledEvent) -> None:
    """Re-check every user who has boosts."""
    user_ids = {snap.reference.parent.parent.id for snap in db().collection_group("boosts").stream()}
    memo: dict = {}
    for uid in sorted(user_ids):
        try:
            refresh_user(uid, memo)
        except Exception as error:  # noqa: BLE001 - one user's failure shouldn't stop the rest
            _record_failure(uid, error)


@firestore_fn.on_document_written(
    document="users/{userId}/boosts/{boostId}",
    timeout_sec=300,
    memory=options.MemoryOption.GB_1,
    cpu=1,
    secrets=[ODDS_API_KEY],
)
def on_boost_changed(event: firestore_fn.Event) -> None:
    """Recalculate as soon as a boost is added, edited, or deleted."""
    uid = event.params["userId"]
    try:
        refresh_user(uid)
    except Exception as error:  # noqa: BLE001
        _record_failure(uid, error)


@https_fn.on_call(timeout_sec=300, memory=options.MemoryOption.GB_1, cpu=1, secrets=[ODDS_API_KEY])
def refresh_opportunities(req: https_fn.CallableRequest) -> dict[str, Any]:
    """Called from the app's refresh button and pull-to-refresh, and after
    changing the same-sportsbook setting."""
    if req.auth is None:
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
                                  message="Sign in before refreshing.")
    try:
        return refresh_user(req.auth.uid)
    except Exception as error:  # noqa: BLE001
        _record_failure(req.auth.uid, error)
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.INTERNAL,
                                  message=f"Refresh failed: {error}") from error


@https_fn.on_call(timeout_sec=60, secrets=[ODDS_API_KEY])
def list_games(req: https_fn.CallableRequest) -> dict[str, Any]:
    """Upcoming games in a league, for the boost form's game picker.
    Uses The Odds API's free events list, so it costs no credits."""
    if req.auth is None:
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
                                  message="Sign in first.")
    league = (req.data or {}).get("league")
    if league not in LEAGUE_KEYS:
        return {"games": []}
    memo: dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    try:
        sport_keys = resolve_sport_keys(league, load_active_sport_keys(memo))
        games = []
        for sport_key in sport_keys:
            for event in load_events(sport_key, memo):
                commence = _commence(event)
                if commence and commence > now and event.get("id"):
                    games.append({
                        "id": event["id"],
                        "name": f"{event.get('away')} @ {event.get('home')}",
                        "commenceTime": commence.isoformat(),
                    })
    except OddsApiError as error:
        raise https_fn.HttpsError(code=https_fn.FunctionsErrorCode.UNAVAILABLE,
                                  message=str(error)) from error
    games.sort(key=lambda g: g["commenceTime"])
    return {"games": games[:200]}