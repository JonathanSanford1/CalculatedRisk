"""Refreshing one user's results: read their boosts, find hedges, and write
them to Firestore for the app."""

from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore

from calcrisk.boosts import Boost, parse_boost
from calcrisk.config import (
    LEAGUE_KEYS,
    LEAGUE_NAMES,
    OBJECTIVES,
    PROP_CREDIT_RESERVE,
    FETCH_PLAYER_PROPS,
    db,
)
from calcrisk.hedges import find_opportunities, group_opportunities
from calcrisk.matchups import build_matchups
from calcrisk.odds_api import (
    OddsApiError,
    flush_usage,
    game_start,
    load_active_sport_keys,
    load_event_markets,
    load_games,
    odds_fetched_at,
    resolve_sport_keys,
)
from calcrisk.odds_math import (
    DEFAULT_ROUNDING_MODE,
    ROUNDING_MODES,
    normalize_rounding_mode,
)
from calcrisk.plan import build_best_plan
from calcrisk.prefetch import UserInputs, plan_game, prefetch_odds, props_allowed


def _only_three_way(game: dict[str, Any]) -> bool:
    """Both books posted nothing but a moneyline with a draw."""
    books = game.get("books", {})
    if not books:
        return False
    for markets in books.values():
        if set(markets) - {"h2h"} or len(markets.get("h2h", [])) not in (0, 3):
            return False
    return True


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


def _load_inputs(uid: str, now: datetime, snaps=None) -> UserInputs:
    """Read a user's settings and boosts. snaps: their boost documents, if the
    caller already has them. Changes nothing."""
    user_ref = db().collection("users").document(uid)
    settings = user_ref.collection("settings").document("preferences").get().to_dict() or {}
    boosts: list[Boost] = []
    expired_ids: list[str] = []
    unreadable = unsupported = 0
    for snap in (snaps if snaps is not None else user_ref.collection("boosts").stream()):
        boost = parse_boost(snap.id, snap.to_dict() or {})
        if boost is None:
            unreadable += 1
        elif boost.is_expired(now):
            expired_ids.append(snap.id)
        elif boost.bet_type not in LEAGUE_KEYS:
            unsupported += 1
        else:
            boosts.append(boost)
    return UserInputs(
        uid=uid,
        selected=normalize_rounding_mode(settings.get("roundingMode")),
        allow_same_book=bool(settings.get("allowSameBook", False)),
        boosts=boosts,
        expired_ids=expired_ids,
        unreadable=unreadable,
        unsupported=unsupported,
    )


def refresh_user(
    uid: str,
    memo: dict | None = None,
    trigger: str = "manual",
    prefetch: bool = True,
    inputs: UserInputs | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recalculate one user's hedges.

    trigger     what asked for this run, for the credit usage log
                (manual, boost, scheduled, ...)
    prefetch    fetch the odds the user needs, in parallel, first. A scheduled
                run turns this off because it already fetched for everyone.
    inputs, now the user's boosts and the clock, when the caller already has them
    """
    memo = {} if memo is None else memo
    try:
        return _refresh_user(uid, memo, trigger, prefetch, inputs, now)
    finally:
        flush_usage(memo, trigger)


def refresh_all_users(trigger: str = "scheduled") -> dict[str, dict[str, Any]]:
    """Recalculate every user who has boosts, fetching each odds feed once for
    all of them."""
    now = datetime.now(timezone.utc)
    snaps_by_user: dict[str, list] = {}
    for snap in db().collection_group("boosts").stream():
        snaps_by_user.setdefault(snap.reference.parent.parent.id, []).append(snap)

    memo: dict[str, Any] = {}
    results: dict[str, dict[str, Any]] = {}
    inputs_by_user: dict[str, UserInputs] = {}
    try:
        for uid in sorted(snaps_by_user):
            try:
                inputs_by_user[uid] = _load_inputs(uid, now, snaps_by_user[uid])
            except Exception as error:  # noqa: BLE001 - one user's failure shouldn't stop the rest
                record_failure(uid, error)
        try:
            prefetch_odds(list(inputs_by_user.values()), memo, now, trigger)
        except Exception as error:  # noqa: BLE001 - each user's pass will retry and report it
            print(f"Prefetch failed: {error!r}")
        for uid, inputs in inputs_by_user.items():
            try:
                results[uid] = refresh_user(uid, memo, trigger, prefetch=False,
                                            inputs=inputs, now=now)
            except Exception as error:  # noqa: BLE001
                record_failure(uid, error)
    finally:
        flush_usage(memo, trigger)
    return results


def _refresh_user(uid, memo, trigger, prefetch, inputs, now) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    user_ref = db().collection("users").document(uid)
    inputs = inputs or _load_inputs(uid, now)
    selected = inputs.selected
    allow_same_book = inputs.allow_same_book
    boosts = inputs.boosts
    expired_ids = inputs.expired_ids
    unreadable, unsupported = inputs.unreadable, inputs.unsupported

    # Delete boosts whose window has ended.
    for start in range(0, len(expired_ids), 400):
        batch = db().batch()
        for boost_id in expired_ids[start: start + 400]:
            batch.delete(user_ref.collection("boosts").document(boost_id))
        batch.commit()

    # Fetch everything needed from The Odds API first, in parallel.
    if prefetch:
        prefetch_odds([inputs], memo, now, trigger)
    fetchable = inputs.fetchable
    allow_props = props_allowed(memo)

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

    odds_times: list[datetime] = []  # when the odds used were fetched
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
            fetched_at = odds_fetched_at(sport_key, memo)
            if fetched_at is not None and league in fetchable:
                odds_times.append(fetched_at)
            for game in games:
                commence, event_id = game_start(game), str(game.get("id") or "")
                if commence is None:
                    continue
                fits, fetch_now, cache_only = plan_game(league, league_boosts, game, now)
                if not fits:
                    continue  # not inside any boost's window (or not its game)
                in_window += 1
                if allow_props:
                    for markets, may_fetch in ((fetch_now, league in fetchable),
                                               (cache_only, False)):
                        if not markets:
                            continue
                        try:
                            props = load_event_markets(sport_key, event_id, markets, memo,
                                                       may_fetch, commence, now)
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
    if not allow_props and FETCH_PLAYER_PROPS:
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
        # When the game odds behind these results were fetched (the oldest of
        # the leagues checked), for the app's "Odds updated X min ago".
        "oddsUpdatedAt": min(odds_times) if odds_times else None,
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