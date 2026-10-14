"""Tests for the credit-saving odds code (stage 1).

Run from the folder that holds functions/ and backend_tests/ (keep this folder
outside functions/ so it isn't deployed):

    cd ~/Desktop/CalculatedRisk
    functions/venv/bin/python -m unittest discover -s backend_tests -v

Nothing here touches the network or a real Firestore: fake_firestore.py and
fake_odds_api.py stand in for them. The boost, matchup, hedge and plan code is
replaced by tiny fakes, since these tests are about when odds are fetched.
"""

import os
import sys
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(os.environ.get("FUNCTIONS_DIR", HERE.parent / "functions"))))
sys.path.insert(0, str(HERE))

from calcrisk import config, odds_api, refresh, schedule  # noqa: E402
from fake_firestore import FakeDB  # noqa: E402
from fake_odds_api import FakeOddsApi  # noqa: E402

NBA = "basketball_nba"


class FakeBoost:
    """Just enough of calcrisk.boosts.Boost for the fetch logic."""

    def __init__(self, boost_id, data):
        self.id = boost_id
        self.bet_type = data["betType"]
        self.used = data.get("used", False)
        self.prop_types = tuple(data.get("markets", ()))

    def is_expired(self, now):
        return False

    def covers_game(self, event_id, commence, now):
        return True

    def allows_market(self, market):
        return not self.prop_types or market in self.prop_types

    def is_active(self, now):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        config._db = self.db
        self.addCleanup(setattr, config, "_db", None)
        self.api = FakeOddsApi()
        self.matchup_games = []  # (league, game id) for every game a hedge was built for

        def build_matchups(game, league, allow_same_book):
            self.matchup_games.append((league, game["id"]))
            return [{"game": game["id"]}]

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch("requests.get", self.api))
        stack.enter_context(mock.patch.object(odds_api, "ODDS_API_KEY", mock.Mock(value="k")))
        stack.enter_context(mock.patch.object(odds_api, "LOCK_POLL_SECONDS", 0.02))
        stack.enter_context(mock.patch.object(refresh, "parse_boost", FakeBoost))
        stack.enter_context(mock.patch.object(refresh, "build_matchups", build_matchups))
        stack.enter_context(mock.patch.object(refresh, "find_opportunities", lambda *a: []))
        stack.enter_context(mock.patch.object(refresh, "group_opportunities", lambda *a: {}))
        stack.enter_context(mock.patch.object(
            refresh, "build_best_plan", lambda *a: {"steps": [], "totalProfit": 0}))
        stack.enter_context(mock.patch("builtins.print"))

    # --- helpers -----------------------------------------------------------------
    def add_boost(self, uid, boost_id, league="nba", used=False, markets=()):
        self.db.collection("users").document(uid).collection("boosts").document(boost_id).set(
            {"betType": league, "used": used, "markets": list(markets)})

    def age(self, cache_id, minutes):
        """Make a cached odds document look like it was fetched `minutes` ago."""
        ref = self.db.collection("odds_cache").document(cache_id)
        data = ref.get().to_dict()
        data["fetchedAt"] = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        ref.set(data)

    def status(self, uid):
        return self.db.collection("users").document(uid).collection("meta").document(
            "status").get().to_dict()

    def event_ids_fetched(self):
        return {c[2] for c in self.api.calls_of("event")}


class TierTests(Base):
    def test_event_cache_tiers(self):
        now = datetime.now(timezone.utc)
        cases = [(None, 25), (-1, 25), (0, 25), (2.99, 25), (3, 110), (23.9, 110),
                 (24, 350), (47.9, 350), (48, 1430), (100, 1430)]
        for hours, expected in cases:
            commence = None if hours is None else now + timedelta(hours=hours)
            self.assertEqual(odds_api.event_cache_minutes(commence, now), expected, hours)


class FreshnessTests(Base):
    def setUp(self):
        super().setUp()
        self.api.add_league(NBA, games=[("g2", 2), ("g10", 10), ("g30", 30), ("g60", 60)])
        self.add_boost("u", "b1")
        refresh.refresh_user("u")  # cold start: fetches everything
        self.assertEqual(len(self.api.calls_of("games")), 1)
        self.assertEqual(self.event_ids_fetched(), {"g2", "g10", "g30", "g60"})
        self.api.calls.clear()

    def test_just_under_each_limit_reuses_just_over_refetches(self):
        # game lines: 20 min old is fresh (limit 25). Per game, the limit
        # depends on how soon it starts: 25 / 110 / 350 / 1430 minutes.
        self.age(NBA, 20)
        for game, minutes in (("g2", 30), ("g10", 100), ("g30", 340), ("g60", 1450)):
            self.age(f"event_{game}_player_points", minutes)
        refresh.refresh_user("u")
        self.assertEqual(len(self.api.calls_of("games")), 0)
        self.assertEqual(self.event_ids_fetched(), {"g2", "g60"})

        self.api.calls.clear()
        self.age(NBA, 30)  # over 25 minutes
        for game, minutes in (("g2", 20), ("g10", 115), ("g30", 360), ("g60", 1400)):
            self.age(f"event_{game}_player_points", minutes)
        refresh.refresh_user("u")
        self.assertEqual(len(self.api.calls_of("games")), 1)
        self.assertEqual(self.event_ids_fetched(), {"g10", "g30"})

    def test_odds_updated_at_is_when_game_lines_were_fetched(self):
        now = datetime.now(timezone.utc)
        self.assertLess(abs((self.status("u")["oddsUpdatedAt"] - now).total_seconds()), 10)
        self.age(NBA, 10)
        refresh.refresh_user("u")  # fresh enough: nothing fetched
        stamp = self.status("u")["oddsUpdatedAt"]
        self.assertLess(abs((now - timedelta(minutes=10) - stamp).total_seconds()), 10)


class CacheOnlyTests(Base):
    def test_used_boosts_never_fetch_but_use_old_cache(self):
        self.api.add_league(NBA, games=[])
        start = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.collection("odds_cache").document(NBA).set({
            "fetchedAt": datetime.now(timezone.utc) - timedelta(days=3),
            "data": [{"id": "old", "home": "H", "away": "A", "commenceTime": start, "books": {}}]})
        self.add_boost("u", "b1", used=True)
        refresh.refresh_user("u")
        self.assertEqual(self.api.calls_of("games"), [])
        self.assertEqual(self.api.calls_of("event"), [])
        self.assertEqual(self.matchup_games, [("nba", "old")])

    def test_cache_only_miss_or_stale_read_is_not_remembered(self):
        memo = {}
        fetches = []

        def fetch():
            fetches.append(1)
            return {"fresh": True}

        age = timedelta(minutes=25)
        self.assertIsNone(odds_api._cached("k", age, memo, False, fetch))  # nothing cached
        self.assertEqual(odds_api._cached("k", age, memo, True, fetch), {"fresh": True})
        self.assertEqual(len(fetches), 1)

        memo = {}
        self.db.collection("odds_cache").document("j").set({
            "fetchedAt": datetime.now(timezone.utc) - timedelta(days=1), "data": {"old": True}})
        self.assertEqual(odds_api._cached("j", age, memo, False, fetch), {"old": True})
        self.assertEqual(odds_api._cached("j", age, memo, True, fetch), {"fresh": True})
        self.assertEqual(len(fetches), 2)

    def test_user_with_only_used_boosts_does_not_hide_games_from_the_next_user(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        self.add_boost("a", "b1", used=True)  # sorts first, may only read the cache
        self.add_boost("b", "b1")
        refresh.refresh_all_users()
        self.assertEqual(self.matchup_games.count(("nba", "g10")), 2)


class ScheduledRunTests(Base):
    def test_each_feed_is_fetched_once_for_all_users(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        for uid in ("a", "b", "c"):
            self.add_boost(uid, "b1")
        results = refresh.refresh_all_users()
        self.assertEqual(sorted(results), ["a", "b", "c"])
        self.assertEqual(len(self.api.calls_of("games")), 1)
        self.assertEqual(len(self.api.calls_of("event")), 1)
        self.assertEqual(self.matchup_games.count(("nba", "g10")), 3)

    def test_failing_endpoint_is_called_once_per_run(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        self.api.fail[("games", NBA)] = 500
        self.add_boost("a", "b1")
        self.add_boost("b", "b1")
        refresh.refresh_all_users()
        self.assertEqual(len(self.api.calls_of("games")), 1)
        for uid in ("a", "b"):
            self.assertFalse(self.status(uid)["ok"])
            self.assertIn("Odds error", self.status(uid)["message"])

    def test_two_users_wanting_different_markets_each_get_theirs(self):
        self.api.add_league("soccer_epl", games=[("s1", 5)])
        self.add_boost("a", "b1", league="epl", markets=["btts"])
        self.add_boost("b", "b1", league="epl", markets=["player_shots_on_target"])
        refresh.refresh_all_users()
        fetched = {c[3] for c in self.api.calls_of("event")}
        self.assertEqual(fetched, {("btts",), ("player_shots_on_target",)})


class UsageTests(Base):
    def test_credits_are_tallied_and_quota_written_once(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        self.add_boost("u", "b1")
        refresh.refresh_user("u", trigger="pull")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = self.db.collection("odds_usage").document(today).get().to_dict()
        self.assertEqual(usage["credits"], 4)  # 3 game-line markets + 1 player_points
        self.assertEqual(usage["calls"], 3)  # /sports (free), game lines, one game
        self.assertEqual(usage["byTrigger"]["pull"], {"credits": 4, "calls": 3})
        self.assertEqual(usage["byKind"]["games"], {"credits": 3, "calls": 1})
        self.assertEqual(usage["byKind"]["event"], {"credits": 1, "calls": 1})
        self.assertEqual(self.db.writes_to("odds_cache", "_quota"), ["set"])
        self.assertEqual(self.db.writes_to("odds_usage", today), ["set"])
        quota = self.db.collection("odds_cache").document("_quota").get().to_dict()
        self.assertEqual(quota["remaining"], self.api.remaining)
        self.assertEqual(self.status("u")["quotaRemaining"], self.api.remaining)

        self.age(NBA, 30)  # a later run on the same day adds to the same document
        refresh.refresh_user("u", trigger="boost")
        usage = self.db.collection("odds_usage").document(today).get().to_dict()
        self.assertEqual(usage["credits"], 7)
        self.assertEqual(usage["byTrigger"]["boost"]["credits"], 3)

    def test_cached_run_spends_nothing_and_writes_no_usage(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        self.add_boost("u", "b1")
        refresh.refresh_user("u")
        writes = len(self.db.log)
        self.api.calls.clear()
        refresh.refresh_user("u")
        self.assertEqual(self.api.calls_of("games") + self.api.calls_of("event"), [])
        quota_and_usage = [p for _, p in self.db.log[writes:] if p[0] in ("odds_usage",)]
        self.assertEqual(quota_and_usage, [])

    def test_player_props_pause_when_credits_are_low(self):
        self.api.add_league(NBA, games=[("g10", 10)])
        self.db.collection("odds_cache").document("_quota").set({"remaining": 50})
        self.add_boost("u", "b1")
        refresh.refresh_user("u")
        self.assertEqual(len(self.api.calls_of("games")), 1)
        self.assertEqual(self.api.calls_of("event"), [])
        self.assertIn("paused", self.status("u")["message"])


class ParallelTests(Base):
    def test_odds_are_fetched_in_parallel(self):
        keys = {"nfl": "americanfootball_nfl", "nba": NBA, "mlb": "baseball_mlb",
                "nhl": "icehockey_nhl", "ncaaf": "americanfootball_ncaaf",
                "wnba": "basketball_wnba"}
        for league, key in keys.items():
            self.api.add_league(key)
            self.add_boost("u", f"b_{league}", league=league)
        self.api.delay = 0.2
        started = time.monotonic()
        refresh.refresh_user("u")
        elapsed = time.monotonic() - started
        self.assertEqual(len(self.api.calls_of("games")), 6)
        self.assertLess(elapsed, 1.0)  # one after another would be about 1.4 s
        quota = self.db.collection("odds_cache").document("_quota").get().to_dict()
        self.assertEqual(quota["remaining"], self.api.remaining)  # the lowest, whatever the order


class FetchClaimTests(Base):
    def test_two_refreshes_fetch_the_same_odds_once(self):
        fetches, results = [], []

        def fetch():
            fetches.append(1)
            time.sleep(0.3)
            return {"x": 1}

        def refresh_once(delay):
            time.sleep(delay)
            results.append(odds_api._cached("k", timedelta(minutes=25), {}, True, fetch))

        threads = [threading.Thread(target=refresh_once, args=(d,)) for d in (0, 0.05)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(fetches), 1)
        self.assertEqual(results, [{"x": 1}, {"x": 1}])
        self.assertFalse(self.db.collection("odds_locks").document("k").get().exists)

    def test_abandoned_claim_is_taken_over(self):
        self.db.collection("odds_locks").document("k").set(
            {"claimedAt": datetime.now(timezone.utc) - timedelta(seconds=200)})
        self.assertEqual(
            odds_api._cached("k", timedelta(minutes=25), {}, True, lambda: {"x": 1}), {"x": 1})
        self.assertFalse(self.db.collection("odds_locks").document("k").get().exists)

    def test_waiter_fetches_anyway_if_the_claim_never_finishes(self):
        self.db.collection("odds_locks").document("k").set({"claimedAt": datetime.now(timezone.utc)})
        fetches = []

        def fetch():
            fetches.append(1)
            return {"x": 1}

        with mock.patch.object(odds_api, "LOCK_WAIT_SECONDS", 0.2):
            self.assertEqual(odds_api._cached("k", timedelta(minutes=25), {}, True, fetch), {"x": 1})
        self.assertEqual(len(fetches), 1)
        self.assertTrue(self.db.collection("odds_locks").document("k").get().exists)  # not ours

    def test_failed_fetch_releases_the_claim_and_is_remembered(self):
        memo, fetches = {}, []

        def fetch():
            fetches.append(1)
            raise odds_api.OddsApiError("down")

        for _ in range(2):
            with self.assertRaises(odds_api.OddsApiError):
                odds_api._cached("k", timedelta(minutes=25), memo, True, fetch)
        self.assertEqual(len(fetches), 1)  # the second call came from the memo
        self.assertFalse(self.db.collection("odds_locks").document("k").get().exists)


class ScheduleTests(unittest.TestCase):
    def test_quiet_hours(self):
        def at(hour, minute):
            return datetime(2026, 10, 13, hour, minute)

        with mock.patch.object(schedule, "QUIET_HOURS_HOURLY", False):
            self.assertFalse(schedule.skip_for_quiet_hours(at(3, 30)))
        with mock.patch.object(schedule, "QUIET_HOURS_HOURLY", True):
            self.assertTrue(schedule.skip_for_quiet_hours(at(3, 30)))
            self.assertFalse(schedule.skip_for_quiet_hours(at(3, 0)))
            self.assertFalse(schedule.skip_for_quiet_hours(at(7, 30)))
            self.assertTrue(schedule.skip_for_quiet_hours(at(0, 30)))


if __name__ == "__main__":
    unittest.main()
