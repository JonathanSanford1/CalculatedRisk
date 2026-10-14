"""Fetching odds before any hedge is calculated.

Everything a run needs from The Odds API is worked out first and fetched in
parallel, so the hedge calculations never wait on the network. A scheduled run
gathers the needs of every user and fetches each odds feed once.

Two phases, because the per-game calls depend on the games found by the first:
  1. every league's game-line odds (one call per league)
  2. per-game markets (props, soccer game markets) for games inside an unused
     boost's window
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, Callable

from calcrisk.boosts import Boost
from calcrisk.config import (
    EVENT_MARKETS,
    FETCH_PLAYER_PROPS,
    FETCH_WORKERS,
    PROP_CREDIT_RESERVE,
    db,
)
from calcrisk.odds_api import (
    OddsApiError,
    flush_usage,
    game_start,
    load_active_sport_keys,
    load_event_markets,
    load_games,
    resolve_sport_keys,
)
from calcrisk.usage import USAGE_KEY

PROPS_ALLOWED_KEY = "__props_allowed__"


@dataclass
class UserInputs:
    """One user's settings and boosts, as read from Firestore."""

    uid: str
    selected: str  # rounding mode
    allow_same_book: bool
    boosts: list[Boost]
    expired_ids: list[str]
    unreadable: int
    unsupported: int

    @property
    def fetchable(self) -> set[str]:
        """Leagues with an unused boost: these may spend credits. Leagues where
        every boost is used only reuse cached odds (their hedge cards stay
        visible)."""
        return {b.bet_type for b in self.boosts if not b.used}


def props_allowed(memo: dict[str, Any]) -> bool:
    """Whether per-game markets may be fetched: not when credits run low, to
    protect the game-line odds. Decided once per run, counting the credits
    this run has already seen reported."""
    if PROPS_ALLOWED_KEY not in memo:
        quota = db().collection("odds_cache").document("_quota").get().to_dict() or {}
        known = [r for r in (quota.get("remaining"), _run_remaining(memo)) if r is not None]
        memo[PROPS_ALLOWED_KEY] = FETCH_PLAYER_PROPS and (
            not known or min(known) >= PROP_CREDIT_RESERVE)
    return memo[PROPS_ALLOWED_KEY]


def _run_remaining(memo: dict[str, Any]) -> int | None:
    usage = memo.get(USAGE_KEY)
    return usage.lowest_remaining() if usage is not None else None


def _matchup_market(api_market: str) -> str:
    """The market name a fetched per-game market ends up under on a matchup."""
    return "totals" if api_market == "alternate_totals" else api_market


def wanted_event_markets(event_markets: list[str], boosts: list[Boost]) -> list[str]:
    """The per-game markets that at least one of these boosts can use."""
    return [
        m for m in event_markets
        if any(b.allows_market(_matchup_market(m)) for b in boosts)
    ]


def plan_game(league: str, league_boosts: list[Boost], game: dict[str, Any], now: datetime):
    """For one game: (boosts whose window covers it, per-game markets to fetch,
    per-game markets to take from the cache only). Credits are spent only on
    markets an unused boost can use; markets only a used boost wants come from
    the cache alone, so that boost's hedge cards stay visible."""
    commence, event_id = game_start(game), str(game.get("id") or "")
    if commence is None:
        return [], [], []
    fits = [b for b in league_boosts if b.covers_game(event_id, commence, now)]
    event_markets = EVENT_MARKETS.get(league)
    if not fits or not event_markets or not event_id:
        return fits, [], []
    fetch_now = wanted_event_markets(event_markets, [b for b in fits if not b.used])
    cache_only = [m for m in wanted_event_markets(event_markets, fits) if m not in fetch_now]
    return fits, fetch_now, cache_only


def _run_parallel(calls: list[Callable[[], Any]]) -> None:
    """Run the calls on a thread pool. A failure is remembered in the run's
    memo by the loader itself; the calculation pass reports it later."""
    if not calls:
        return
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(calls))) as pool:
        futures = [pool.submit(call) for call in calls]
        for future in futures:
            try:
                future.result()
            except Exception as error:  # noqa: BLE001
                print(f"Odds fetch failed: {error!r}")


def prefetch_odds(users: list[UserInputs], memo: dict[str, Any], now: datetime,
                  trigger: str) -> None:
    """Fetch, in parallel, every odds feed these users need and that is out of date."""
    leagues = sorted({b.bet_type for user in users for b in user.boosts})
    if not leagues:
        return
    try:
        active_keys = load_active_sport_keys(memo)
    except OddsApiError:
        return  # remembered in the memo; the calculation pass reports it

    # Phase 1: game-line odds for every league that may spend credits.
    sport_keys = sorted({
        sport_key
        for league in leagues
        if any(league in user.fetchable for user in users)
        for sport_key in resolve_sport_keys(league, active_keys)
    })
    _run_parallel([partial(load_games, key, memo, True) for key in sport_keys])

    # Phase 2: per-game markets, from the games phase 1 found.
    if not props_allowed(memo):
        flush_usage(memo, trigger)
        return
    event_calls: dict[tuple[str, str, tuple[str, ...]], Any] = {}
    for user in users:
        for league in sorted({b.bet_type for b in user.boosts} & user.fetchable):
            league_boosts = [b for b in user.boosts if b.bet_type == league]
            for sport_key in resolve_sport_keys(league, active_keys):
                try:
                    games = load_games(sport_key, memo, True)  # already loaded: no call
                except OddsApiError:
                    continue
                for game in games:
                    _, fetch_now, _ = plan_game(league, league_boosts, game, now)
                    if fetch_now:
                        key = (sport_key, str(game.get("id")), tuple(fetch_now))
                        event_calls[key] = game_start(game)
    _run_parallel([
        partial(load_event_markets, sport_key, event_id, list(markets), memo, True, commence, now)
        for (sport_key, event_id, markets), commence in sorted(event_calls.items())
    ])
    flush_usage(memo, trigger)
