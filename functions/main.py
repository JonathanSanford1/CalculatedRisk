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

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from firebase_admin import initialize_app
from firebase_functions import firestore_fn, https_fn, options, scheduler_fn

from calcrisk.config import LEAGUE_KEYS, ODDS_API_KEY, db
from calcrisk.odds_api import (
    OddsApiError,
    game_start,
    load_active_sport_keys,
    load_events,
    resolve_sport_keys,
)
from calcrisk.refresh import record_failure, refresh_user

# These must run before the functions below are defined.
initialize_app()
options.set_global_options(max_instances=10)


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
            record_failure(uid, error)


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
        record_failure(uid, error)


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
        record_failure(req.auth.uid, error)
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
                commence = game_start(event)
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
