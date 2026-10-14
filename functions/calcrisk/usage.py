"""Credit usage for one run: what each Odds API call cost, and the credits
left. Collected in memory during the run and written to Firestore in one go
(flush), so a run with many calls still makes only two writes.

  odds_cache/_quota         credits left (the lowest value reported in the run)
  odds_usage/YYYY-MM-DD     credits and calls so far that UTC day, in total,
                            by what triggered the run, and by kind of call
"""

import threading
from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore

from calcrisk.config import db

USAGE_KEY = "__usage__"  # where a run's RunUsage lives in its memo


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class RunUsage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[tuple[str, int]] = []  # (kind, credits)
        self._lowest: tuple[int, int | None] | None = None  # (remaining, used)

    def record(self, kind: str, headers: Any) -> None:
        """Note one API response. kind: sports, events, games, or event."""
        credits = _int(headers.get("x-requests-last")) or 0
        remaining = _int(headers.get("x-requests-remaining"))
        used = _int(headers.get("x-requests-used"))
        with self._lock:
            self._calls.append((kind, credits))
            # Parallel calls can finish out of order; the lowest figure is the
            # true one.
            if remaining is not None and (self._lowest is None or remaining < self._lowest[0]):
                self._lowest = (remaining, used)

    def lowest_remaining(self) -> int | None:
        """The fewest credits left in any response since the last flush."""
        with self._lock:
            return self._lowest[0] if self._lowest else None

    def flush(self, trigger: str) -> None:
        """Write everything recorded since the last flush. Never raises:
        bookkeeping must not break a refresh."""
        with self._lock:
            calls, lowest = self._calls, self._lowest
            self._calls, self._lowest = [], None
        if not calls:
            return
        try:
            now = datetime.now(timezone.utc)
            if lowest is not None:
                quota: dict[str, Any] = {"remaining": lowest[0], "updatedAt": now}
                if lowest[1] is not None:
                    quota["used"] = lowest[1]
                db().collection("odds_cache").document("_quota").set(quota)

            by_kind: dict[str, dict[str, Any]] = {}
            total = 0
            for kind, credits in calls:
                total += credits
                entry = by_kind.setdefault(
                    kind, {"credits": firestore.Increment(0), "calls": firestore.Increment(0)})
                entry["credits"] = firestore.Increment(entry["credits"].value + credits)
                entry["calls"] = firestore.Increment(entry["calls"].value + 1)
            db().collection("odds_usage").document(now.strftime("%Y-%m-%d")).set({
                "credits": firestore.Increment(total),
                "calls": firestore.Increment(len(calls)),
                "byTrigger": {trigger: {"credits": firestore.Increment(total),
                                        "calls": firestore.Increment(len(calls))}},
                "byKind": by_kind,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }, merge=True)
        except Exception as error:  # noqa: BLE001
            print(f"Couldn't record credit usage: {error!r}")
