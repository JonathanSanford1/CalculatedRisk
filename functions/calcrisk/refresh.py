"""Refreshing one user's results: read their boosts, find hedges, and write
them to Firestore for the app."""

from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore

from calcrisk.boosts import Boost, parse_boost
from calcrisk.config import (
    EVENT_MARKETS,
    FETCH_PLAYER_PROPS,
    LEAGUE_KEYS,
    LEAGUE_NAMES,
    OBJECTIVES,
    PROP_CREDIT_RESERVE,
    db,
)
from calcrisk.hedges import find_opportunities, group_opportunities
from calcrisk.matchups import build_matchups
from calcrisk.odds_api import (
    OddsApiError,
    game_start,
    load_active_sport_keys,
    load_event_markets,
    load_games,
    resolve_sport_keys,
)
from calcrisk.odds_math import (
    DEFAULT_ROUNDING_MODE,
    ROUNDING_MODES,
    normalize_rounding_mode,
)
from calcrisk.plan import build_best_plan


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
                commence, event_id = game_start(game), str(game.get("id") or "")
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


def record_failure(uid: str, error: Exception) -> None:
    print(f"Refresh failed for {uid}: {error!r}")
    try:
        db().collection("users").document(uid).collection("meta").document("status").set(
            {"lastRunAt": firestore.SERVER_TIMESTAMP, "ok": False,
             "message": f"Refresh failed: {error}"},
            merge=True,
        )
    except Exception as write_error:  # noqa: BLE001
        print(f"Couldn't record failure for {uid}: {write_error!r}")
