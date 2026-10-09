"""The Odds API: requests, credit quota, and the shared Firestore cache."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from calcrisk.config import (
    API_BASE,
    BOOKMAKERS,
    EVENTS_CACHE_MINUTES,
    GAME_MARKETS,
    LEAGUE_KEYS,
    ODDS_API_KEY,
    ODDS_CACHE_MINUTES,
    SPORTS_LIST_CACHE_HOURS,
    db,
)


class OddsApiError(Exception):
    pass


def game_start(game: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(game["commenceTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


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
