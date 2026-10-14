"""A stand-in for The Odds API: answers requests.get() with canned games,
charges credits the way the real API does, and records every call."""

import threading
import time
from datetime import datetime, timedelta, timezone


class FakeResponse:
    def __init__(self, status_code, payload, headers):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers
        self.text = str(payload)[:200]

    def json(self):
        return self._payload


def _iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeOddsApi:
    def __init__(self, credits=10_000):
        self.lock = threading.Lock()
        self.calls: list[tuple] = []  # (kind, sport_key, event_id, markets)
        self.sports: list[str] = []
        self.games: dict[str, list[tuple]] = {}  # sport_key -> [(id, start)]
        self.fail: dict[tuple, int] = {}  # (kind, sport_key) -> HTTP status
        self.delay = 0.0
        self.remaining = credits
        self.total = credits

    def add_league(self, sport_key, games=()):
        """games: [(event_id, hours_from_now), ...]"""
        now = datetime.now(timezone.utc)
        self.sports.append(sport_key)
        self.games[sport_key] = [(gid, now + timedelta(hours=h)) for gid, h in games]

    def calls_of(self, kind):
        return [c for c in self.calls if c[0] == kind]

    # --- requests.get replacement ---------------------------------------------
    def __call__(self, url, params=None, timeout=None):
        params = params or {}
        parts = url.split("/v4", 1)[1].strip("/").split("/")
        if self.delay:
            time.sleep(self.delay)
        if parts == ["sports"]:
            kind, sport_key, event_id, markets, cost = "sports", None, None, (), 0
        elif len(parts) == 3 and parts[2] == "odds":
            kind, sport_key, event_id = "games", parts[1], None
            markets = tuple(params["markets"].split(","))
            cost = len(markets)
        elif len(parts) == 3 and parts[2] == "events":
            kind, sport_key, event_id, markets, cost = "events", parts[1], None, (), 0
        elif len(parts) == 5 and parts[4] == "odds":
            kind, sport_key, event_id = "event", parts[1], parts[3]
            markets = tuple(params["markets"].split(","))
            cost = len(markets)
        else:
            raise AssertionError(f"unexpected URL {url}")

        with self.lock:
            self.calls.append((kind, sport_key, event_id, markets))
            status = self.fail.get((kind, sport_key))
            if status is None:
                self.remaining -= cost
            remaining = self.remaining
        headers = {
            "x-requests-last": str(0 if status else cost),
            "x-requests-remaining": str(remaining),
            "x-requests-used": str(self.total - remaining),
        }
        if status:
            return FakeResponse(status, {"message": "boom"}, headers)

        if kind == "sports":
            payload = [{"key": k, "active": True, "has_outrights": False} for k in self.sports]
        elif kind == "events":
            payload = [self._event(sport_key, g) for g in self.games[sport_key]]
        elif kind == "games":
            payload = [self._event(sport_key, g, markets) for g in self.games[sport_key]]
        else:
            game = next((g for g in self.games[sport_key] if g[0] == event_id), None)
            payload = self._event(sport_key, game, markets) if game else None
            if payload is None:
                return FakeResponse(404, {}, headers)
        return FakeResponse(200, payload, headers)

    @staticmethod
    def _event(sport_key, game, markets=()):
        event_id, start = game
        books = [
            {"key": book, "markets": [
                {"key": m, "outcomes": [
                    {"name": "Over", "description": "Player", "price": -110, "point": 20.5},
                    {"name": "Under", "description": "Player", "price": -110, "point": 20.5},
                ]} for m in markets]}
            for book in ("draftkings", "fanduel")
        ] if markets else []
        return {"id": event_id, "home_team": f"Home {event_id}", "away_team": f"Away {event_id}",
                "commence_time": _iso(start), "bookmakers": books}
