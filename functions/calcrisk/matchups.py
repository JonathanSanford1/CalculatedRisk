"""Bets, and which pairs of bets form a hedge."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from calcrisk.config import BOOKMAKERS, MARKET_LABELS
from calcrisk.odds_math import is_multiple


@dataclass(frozen=True)
class Side:
    """One bet you could place."""

    book: str
    market: str  # "h2h", "spreads", "totals", or a player prop market
    kind: str  # "team" (moneyline), "spread", "over", "under", "yes", "no"
    odds: int
    selection: str  # readable, e.g. "Chiefs -3.5", "Travis Kelce Over 62.5"
    team: str | None = None
    point: float | None = None
    player: str | None = None


@dataclass(frozen=True)
class Matchup:
    """Two bets on one game that can't both lose."""

    league: str
    event_id: str
    game: str
    commence_time: datetime
    market: str
    side_a: Side
    side_b: Side
    # Every possible way the two bets can settle: (result a, result b), each
    # "win", "push" (stake refunded), or "lose".
    scenarios: tuple[tuple[str, str], ...]
    middle: str | None  # when both bets win, if they can


def _sides(game: dict[str, Any], book: str, market: str) -> list[Side]:
    outcomes = game.get("books", {}).get(book, {}).get(market, [])
    sides = []
    if market == "h2h":
        # A moneyline with a draw has three outcomes and can't be hedged with
        # two bets, so it's skipped.
        if len(outcomes) != 2:
            return []
        for o in outcomes:
            if o.get("price") is not None and o.get("name"):
                sides.append(Side(book, market, "team", int(o["price"]),
                                  f"{o['name']} moneyline", team=o["name"]))
        return sides
    if market == "btts":
        for o in outcomes:
            if o.get("price") is not None and o.get("name") in ("Yes", "No"):
                sides.append(Side(book, market, o["name"].lower(), int(o["price"]),
                                  f"Both teams to score: {o['name']}"))
        return sides
    for o in outcomes:
        price, point, name = o.get("price"), o.get("point"), o.get("name")
        if price is None or point is None or not name:
            continue
        point = float(point)
        # Quarter lines (-0.25, 2.75) split the bet in two; not supported.
        if not is_multiple(point, 0.5):
            continue
        if market == "spreads":
            sides.append(Side(book, market, "spread", int(price),
                              f"{name} {point:+g}", team=name, point=point))
        elif name in ("Over", "Under"):
            kind = name.lower()
            if market == "totals":
                sides.append(Side(book, market, kind, int(price), f"{name} {point:g}", point=point))
            else:
                player = o.get("description")
                if player:
                    label = MARKET_LABELS.get(market, market.replace("_", " "))
                    sides.append(Side(book, market, kind, int(price),
                                      f"{player} {name} {point:g} {label}",
                                      point=point, player=player))
    return sides


def _settle_over_under(kind: str, point: float, value: int) -> str:
    if value == point:
        return "push"
    if kind == "over":
        return "win" if value > point else "lose"
    return "win" if value < point else "lose"


def _settle_spread(point: float, margin: int) -> str:
    """margin = this team's score minus the other team's."""
    adjusted = margin + point
    return "win" if adjusted > 0 else ("push" if adjusted == 0 else "lose")


def _join_values(values: list[str]) -> str:
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " or " + values[-1]


def pair_scenarios(a: Side, b: Side) -> tuple[tuple[tuple[str, str], ...], str | None] | None:
    """How bets a and b can settle together, and when both win (a middle).
    None unless every possible result wins at least one of the two bets.

    Lines don't have to match. Over 52.5 with Under 53.5 is kept: every total
    wins one bet, and a total of exactly 53 wins both. Over 53.5 with
    Under 52.5 is rejected: a total of 53 would lose both."""
    if a.market != b.market:
        return None
    results: set[tuple[str, str]] = set()
    both_win: list[int] = []
    if a.kind == "team" and b.kind == "team":
        if a.team == b.team:
            return None
        return (("lose", "win"), ("win", "lose")), None
    if {a.kind, b.kind} == {"yes", "no"}:
        return (("lose", "win"), ("win", "lose")), None
    if a.kind == "spread" and b.kind == "spread":
        if a.team == b.team:
            return None
        reach = int(abs(a.point) + abs(b.point)) + 3
        for margin in range(-reach, reach + 1):  # a's team score minus b's
            outcome = (_settle_spread(a.point, margin), _settle_spread(b.point, -margin))
            results.add(outcome)
            if outcome == ("win", "win"):
                both_win.append(margin)
        middle = None
        if both_win:
            words = []
            a_by = [str(m) for m in both_win if m > 0]
            b_by = [str(-m) for m in both_win if m < 0]
            if a_by:
                words.append(f"{a.team} win by {_join_values(a_by)}")
            if b_by:
                words.append(f"{b.team} win by {_join_values(b_by)}")
            if 0 in both_win:
                words.append("the game ties")
            middle = "Both bets win if " + " or ".join(words)
    elif {a.kind, b.kind} == {"over", "under"} and a.player == b.player:
        low = max(0, math.floor(min(a.point, b.point)) - 2)
        high = math.ceil(max(a.point, b.point)) + 2
        for value in range(low, high + 1):
            outcome = (_settle_over_under(a.kind, a.point, value),
                       _settle_over_under(b.kind, b.point, value))
            results.add(outcome)
            if outcome == ("win", "win"):
                both_win.append(value)
        middle = None
        if both_win:
            numbers = _join_values([str(v) for v in both_win])
            if a.player:
                label = MARKET_LABELS.get(a.market, a.market.replace("_", " "))
                middle = f"Both bets win if {a.player} has exactly {numbers} {label}"
            else:
                middle = f"Both bets win if the total is exactly {numbers}"
    else:
        return None
    if any("win" not in outcome for outcome in results):
        return None  # some result loses both bets (or only refunds them)
    return tuple(sorted(results)), middle


def build_matchups(game: dict[str, Any], league: str, allow_same_book: bool) -> list[Matchup]:
    try:
        commence = datetime.fromisoformat(game["commenceTime"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    label = f"{game.get('away')} @ {game.get('home')}"
    markets = sorted({m for book in game.get("books", {}).values() for m in book})
    matchups = []
    for market in markets:
        sides = [s for book in BOOKMAKERS for s in _sides(game, book, market)]
        for i in range(len(sides)):
            for j in range(i + 1, len(sides)):
                a, b = sides[i], sides[j]
                if a.book == b.book and not allow_same_book:
                    continue
                paired = pair_scenarios(a, b)
                if paired is None:
                    continue
                scenarios, middle = paired
                matchups.append(Matchup(league, str(game.get("id") or label), label,
                                        commence, market, a, b, scenarios, middle))
    return matchups
