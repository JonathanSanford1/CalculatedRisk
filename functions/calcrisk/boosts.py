"""Boosts saved by the app, and reading them from Firestore."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from calcrisk.config import BOOKMAKERS, PROP_TYPE_MARKETS


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
