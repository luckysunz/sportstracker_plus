"""Choosing which game to show."""

from __future__ import annotations

from datetime import date, tzinfo
from typing import Iterable, Optional

from .models import Game, GameStatus


def select_game_of_day(games: Iterable[Game], today: date, tz: tzinfo) -> Optional[Game]:
    """Return the game that starts on ``today`` (local date), if any.

    This implements the display rule "show the game of the day; after the game
    the final score stays until local midnight": a game is selected by the
    local calendar date of its start, regardless of its status.

    Postponed/cancelled games are ignored. If (unusually) several games fall on the same day, a live game wins, then
    the earliest upcoming one, then the latest finished one.
    """
    todays = [
        g
        for g in games
        if g.status != GameStatus.CANCELLED and g.start.astimezone(tz).date() == today
    ]
    if not todays:
        return None
    live = [g for g in todays if g.status == GameStatus.LIVE]
    if live:
        return min(live, key=lambda g: g.start)
    upcoming = [g for g in todays if g.status == GameStatus.SCHEDULED]
    if upcoming:
        return min(upcoming, key=lambda g: g.start)
    return max(todays, key=lambda g: g.start)
