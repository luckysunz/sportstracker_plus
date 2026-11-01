"""Provider-independent data models.

All providers (OpenLigaDB, Genius/RBBL, TheSportsDB, ...) return these types,
so the display layer never needs to know where the data came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple


class GameStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    # Not finished according to the source, but far past the expected end.
    # Treated like FINISHED by the display (score stays visible).
    UNKNOWN = "unknown"
    # Postponed or cancelled; never selected as "game of the day".
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    short_name: str = ""
    logo_url: str = ""


@dataclass(frozen=True)
class PeriodScore:
    """Score entry reported by the source, e.g. ``1.Drittel`` 0:1 (cumulative)."""

    label: str
    home: int
    away: int


@dataclass(frozen=True)
class Game:
    game_id: str
    home: Team
    away: Team
    start: datetime  # timezone-aware, UTC
    status: GameStatus
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    # Label of the latest reported period entry (e.g. "2.Drittel"), if any.
    period_label: Optional[str] = None
    # Running game clock, only if the source provides one (e.g. "07:32").
    clock: Optional[str] = None
    # True while the game is paused between periods (clock not running).
    period_break: bool = False
    # All reported period entries in order (cumulative scores).
    periods: Tuple[PeriodScore, ...] = ()
    # "OT" (overtime) or "SO" (shootout / penalties) when decided after
    # regular time, otherwise None.
    decided_in: Optional[str] = None
    # True during the break in the middle of the game (basketball: after the 2nd quarter).
    # Set by providers that know about it; others leave it False.
    halftime: bool = False


@dataclass(frozen=True)
class TableRow:
    rank: int
    team: Team
    played: int = 0
    won: int = 0
    draw: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    points: int = 0
