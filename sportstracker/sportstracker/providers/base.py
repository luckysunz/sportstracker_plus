"""Provider interface.

One provider module per data source. Providers translate a source's API into
the shared models and implement the display rule "game of the day".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, tzinfo
from typing import List, Optional

from ..models import Game, TableRow, Team


class Provider(ABC):
    name = "base"

    def __init__(self, tz: tzinfo) -> None:
        self.tz = tz

    @abstractmethod
    def list_teams(self) -> List[Team]:
        """All teams of the configured league (for the favorite-team picker)."""

    @abstractmethod
    def get_table(self) -> List[TableRow]:
        """League table in display order (rank 1 first)."""

    @abstractmethod
    def get_game_of_day(self, team_id: str, now: datetime) -> Optional[Game]:
        """The game of ``team_id`` that starts on the local date of ``now``.

        Finished games are returned too; the display keeps the final score
        until local midnight. Returns None if the team has no game that day.
        Raises ProviderError if the source cannot be reached or understood.
        """

    def get_games_of_day(self, now: datetime) -> Optional[List[Game]]:
        """All games of the league that start on the local date of ``now`` or are still relevant.

        Relevant means: starting today, running (also after midnight), or started within the last six
        hours. Cancelled games are left out. Returns None if this source cannot list the games of the whole
        league; the caller then only knows the favorite's game from ``get_game_of_day``.
        """
        return None
