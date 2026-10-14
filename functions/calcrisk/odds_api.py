"""The Odds API: requests, the shared Firestore cache, and credit tracking."""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests
from google.api_core.exceptions import AlreadyExists

from calcrisk.config import (
    API_BASE,
    BOOKMAKERS,
    EVENT_CACHE_FAR_MINUTES,
    EVENT_CACHE_TIERS,
    EVENTS_CACHE_MINUTES,
    GAME_LINE_CACHE_MINUTES,
    GAME_MARKETS,
    LEAGUE_KEYS,
    LOCK_POLL_SECONDS,
    LOCK_STALE_SECONDS,
    LOCK_WAIT_SECONDS,
    ODDS_API_KEY,
    SPORTS_LIST_CACHE_HOURS,
    db,
)
from calcrisk.usage import USAGE_KEY, RunUsage


class OddsApiError(Exception):
    pass


def game_start(game: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(game["commenceTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def flush_usage(memo: dict[str, Any], trigger: str) -> None:
    """Write the credits spent so far in this run (see usage.py)."""
    usage = memo.get(USAGE_KEY)
    if usage is not None:
        usage.flush(trigger)


def _api_get(path: str, what: str, memo: dict[str, Any], kind: str,
             ok_missing: bool = False, **params: Any) -> Any:
    """GET from The Odds API. With ok_missing, a 404/422 (game gone, or the
    market isn't offered for it) returns None instead of raising. kind is
    what the call is for (sports, events, games, event), for the usage log."""
    try:
        response = requests.get(
            f"{API_BASE}{path}",
            params={"apiKey": ODDS_API_KEY.value, **params},
            timeout=20,
        )
    except requests.RequestException as error:
        raise OddsApiError(f"Couldn't reach The Odds API: {error}") from error
    memo.setdefault(USAGE_KEY, RunUsage()).record(kind, response.headers)
    if ok_missing and response.status_code in (404, 422):
        return None
    if response.status_code != 200:
        raise OddsApiError(
            f"The Odds API returned {response.status_code} for {what}: {response.text[:200]}"
        )
    return response.json()


# --- This run's memo ----------------------------------------------------------
# The memo remembers what this run has already loaded, so no odds are loaded
# twice. An entry says whether its data was fresh enough to fetch nothing
# more: cache-only readers (used boosts, which never spend credits) accept
# old data, but a reader that may fetch must not be handed that old data from
# the memo. Failed fetches are remembered too, so a failing endpoint is
# called once per run, not once per user.


@dataclass(frozen=True)
class _Entry:
    data: Any
    fresh: bool
    fetched_at: datetime | None


@dataclass(frozen=True)
class _Failed:
    message: str


def _read_cache(ref) -> tuple[Any, datetime | None] | None:
    """(data, fetchedAt) from a cache document, or None if there is none."""
    snapshot = ref.get()
    if not snapshot.exists:
        return None
    cached = snapshot.to_dict() or {}
    if "data" not in cached:
        return None
    return cached["data"], cached.get("fetchedAt")


def _is_fresh(fetched_at: datetime | None, max_age: timedelta) -> bool:
    return bool(fetched_at) and datetime.now(timezone.utc) - fetched_at < max_age


# --- Fetch claims ---------------------------------------------------------------
# Two refreshes running at once (say a scheduled run and a pull to refresh)
# would both fetch the same odds. The first to create odds_locks/<id> fetches;
# the other waits for its result. Claims that go stale are taken over, and
# after LOCK_WAIT_SECONDS a waiter fetches anyway, so a stuck claim can cost
# a few credits but never blocks a refresh.


def _claim(lock_ref) -> bool:
    """True if this caller now holds the claim."""
    for _ in range(2):
        now = datetime.now(timezone.utc)
        try:
            lock_ref.create({"claimedAt": now})
            return True
        except AlreadyExists:
            pass
        snapshot = lock_ref.get()
        if not snapshot.exists:
            continue  # released just now: try again
        claimed_at = (snapshot.to_dict() or {}).get("claimedAt")
        if claimed_at is None or now - claimed_at > timedelta(seconds=LOCK_STALE_SECONDS):
            lock_ref.set({"claimedAt": now})  # abandoned: take it over
            return True
        return False
    return False


def _fetch_once(cache_id: str, ref, max_age: timedelta,
                fetch: Callable[[], Any]) -> tuple[Any, datetime]:
    """fetch() the data and store it, unless another refresh does it first.
    Returns (data, fetchedAt)."""
    lock_ref = db().collection("odds_locks").document(cache_id)
    claimed = _claim(lock_ref)
    waited = False
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not claimed and time.monotonic() < deadline:
        waited = True
        time.sleep(LOCK_POLL_SECONDS)
        cached = _read_cache(ref)
        if cached is not None and _is_fresh(cached[1], max_age):
            return cached[0], cached[1]
        claimed = _claim(lock_ref)  # the other fetch ended without data, or went stale
    try:
        if claimed and waited:
            cached = _read_cache(ref)  # it may have finished while we were claiming
            if cached is not None and _is_fresh(cached[1], max_age):
                return cached[0], cached[1]
        data = fetch()
        fetched_at = datetime.now(timezone.utc)
        ref.set({"fetchedAt": fetched_at, "data": data})
        return data, fetched_at
    finally:
        if claimed:
            try:
                lock_ref.delete()
            except Exception as error:  # noqa: BLE001 - it goes stale on its own
                print(f"Couldn't release fetch claim {cache_id}: {error!r}")


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
    entry = memo.get(cache_id)
    if isinstance(entry, _Failed):
        if allow_fetch:
            raise OddsApiError(entry.message)
        entry = None  # a cache-only reader can still use whatever is stored
    if isinstance(entry, _Entry) and (entry.fresh or not allow_fetch):
        return entry.data

    ref = db().collection("odds_cache").document(cache_id)
    cached = _read_cache(ref)
    if cached is not None:
        data, fetched_at = cached
        if _is_fresh(fetched_at, max_age):
            memo[cache_id] = _Entry(data, True, fetched_at)
            return data
        if not allow_fetch:
            memo.setdefault(cache_id, _Entry(data, False, fetched_at))
            return data
    elif not allow_fetch:
        memo.setdefault(cache_id, _Entry(None, False, None))
        return None

    try:
        data, fetched_at = _fetch_once(cache_id, ref, max_age, fetch)
    except Exception as error:  # noqa: BLE001 - remember it for the rest of the run
        memo[cache_id] = _Failed(str(error) or repr(error))
        raise
    memo[cache_id] = _Entry(data, True, fetched_at)
    return data


def odds_fetched_at(cache_id: str, memo: dict[str, Any]) -> datetime | None:
    """When the odds this run used for cache_id were fetched (None if unknown)."""
    entry = memo.get(cache_id)
    return entry.fetched_at if isinstance(entry, _Entry) else None


def event_cache_minutes(commence: datetime | None, now: datetime | None = None) -> int:
    """How old per-game odds may be before they're fetched again: the closer
    the game, the fresher (see EVENT_CACHE_TIERS)."""
    if commence is None:
        return EVENT_CACHE_TIERS[0][1]
    hours = (commence - (now or datetime.now(timezone.utc))).total_seconds() / 3600
    for limit_hours, minutes in EVENT_CACHE_TIERS:
        if hours < limit_hours:
            return minutes
    return EVENT_CACHE_FAR_MINUTES


def load_active_sport_keys(memo: dict[str, Any]) -> set[str]:
    """Sport keys currently in season, excluding futures. Free (no credits)."""

    def fetch() -> list[str]:
        sports = _api_get("/sports", "the league list", memo, "sports")
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
            f"/sports/{sport_key}/odds", sport_key, memo, "games",
            markets=",".join(GAME_MARKETS), oddsFormat="american",
            bookmakers=",".join(BOOKMAKERS),
        )
        print(f"Fetched {sport_key} game odds ({len(games)} games).")
        return [_parse_game(g) for g in games]

    return _cached(sport_key, timedelta(minutes=GAME_LINE_CACHE_MINUTES), memo, allow_fetch, fetch) or []


def load_event_markets(
    sport_key: str, event_id: str, markets: list[str], memo: dict[str, Any], allow_fetch: bool,
    commence: datetime | None = None, now: datetime | None = None,
) -> dict[str, dict[str, list]]:
    """Per-game markets (props, soccer game markets) for one game. Costs 1
    credit per market the books actually offer for that game. How long the
    odds are reused depends on how soon the game starts (commence)."""

    def fetch() -> dict[str, dict[str, list]]:
        event = _api_get(
            f"/sports/{sport_key}/events/{event_id}/odds", f"{', '.join(markets)} for one game",
            memo, "event", ok_missing=True, markets=",".join(markets), oddsFormat="american",
            bookmakers=",".join(BOOKMAKERS),
        )
        return _parse_books(event.get("bookmakers", [])) if event else {}

    cache_id = f"event_{event_id}_{'-'.join(markets)}"
    max_age = timedelta(minutes=event_cache_minutes(commence, now))
    return _cached(cache_id, max_age, memo, allow_fetch, fetch) or {}


def load_events(sport_key: str, memo: dict[str, Any]) -> list[dict[str, Any]]:
    """Upcoming games in a league, for the game picker. Free (no credits)."""

    def fetch() -> list[dict[str, Any]]:
        events = _api_get(f"/sports/{sport_key}/events", f"{sport_key} games", memo, "events")
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