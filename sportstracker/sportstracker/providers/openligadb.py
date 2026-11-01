"""OpenLigaDB provider (https://api.openligadb.de, web UI openleaguedb.net).

Used for the DEL (ice hockey) test league: shortcut ``del``, season 2026,
leagueId 4947. Other leagues hosted on OpenLigaDB work with the same code.

Endpoints used:
  getavailableteams/{shortcut}/{season}
  getbltable/{shortcut}/{season}
  getlastmatchbyleagueteam/{leagueId}/{teamId}
  getnextmatchbyleagueteam/{leagueId}/{teamId}

Known limits of the source (see project doc "Quellen-Evaluierung"):
  * no running game clock; scores arrive as cumulative period entries
    (``1.Drittel``, ``2.Drittel``, ``3.Drittel``, ``Overtime``, ``Penalty``)
  * results are maintained by users, so live updates may lag
  * matchday numbers are not reliable -> selection is by team and date
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional

from ..errors import HttpError, ProviderError
from ..models import Game, GameStatus, PeriodScore, TableRow, Team
from ..selection import select_game_of_day
from ..timeutil import local_date, parse_utc
from .base import Provider

BASE_URL = "https://api.openligadb.de"

# A game that is not marked finished is considered live for this long after
# its start (ice hockey incl. overtime and shootout fits comfortably).
DEFAULT_LIVE_WINDOW = timedelta(hours=4)

# Cache lifetimes (seconds) so repeated calls inside one refresh cycle do not
# hit the server again.
TEAM_CACHE_TTL = 24 * 3600
TABLE_CACHE_TTL = 60
MATCH_CACHE_TTL = 20


class OpenLigaDbProvider(Provider):
    name = "openligadb"

    def __init__(
        self,
        http: Any,
        league_shortcut: str,
        season: int,
        league_id: int,
        tz: tzinfo,
        base_url: str = BASE_URL,
        live_window: timedelta = DEFAULT_LIVE_WINDOW,
    ) -> None:
        super().__init__(tz)
        self.http = http
        self.league_shortcut = league_shortcut
        self.season = int(season)
        self.league_id = int(league_id)
        self.base_url = base_url.rstrip("/")
        self.live_window = live_window

    @classmethod
    def from_params(cls, params: Dict[str, Any], http: Any, tz: tzinfo) -> "OpenLigaDbProvider":
        missing = [k for k in ("league_shortcut", "season", "league_id") if k not in params]
        if missing:
            raise ProviderError("openligadb: missing provider_params: %s" % ", ".join(missing))
        return cls(
            http=http,
            league_shortcut=str(params["league_shortcut"]),
            season=int(params["season"]),
            league_id=int(params["league_id"]),
            tz=tz,
            base_url=str(params.get("base_url", BASE_URL)),
        )

    # ------------------------------------------------------------------ teams

    def list_teams(self) -> List[Team]:
        url = "%s/getavailableteams/%s/%d" % (self.base_url, self.league_shortcut, self.season)
        data = self._get(url, ttl=TEAM_CACHE_TTL)
        if not isinstance(data, list):
            raise ProviderError("openligadb: unexpected team list format")
        return [self._parse_team(raw) for raw in data]

    # ------------------------------------------------------------------ table

    def get_table(self) -> List[TableRow]:
        url = "%s/getbltable/%s/%d" % (self.base_url, self.league_shortcut, self.season)
        data = self._get(url, ttl=TABLE_CACHE_TTL)
        if not isinstance(data, list):
            raise ProviderError("openligadb: unexpected table format")
        rows = []
        # The API returns the table already sorted; keep its order.
        for index, raw in enumerate(data, start=1):
            team = Team(
                team_id=str(raw.get("teamInfoId", "")),
                name=str(raw.get("teamName", "")),
                short_name=str(raw.get("shortName", "") or ""),
                logo_url=str(raw.get("teamIconUrl", "") or ""),
            )
            rows.append(
                TableRow(
                    rank=index,
                    team=team,
                    played=_int(raw.get("matches")),
                    won=_int(raw.get("won")),
                    draw=_int(raw.get("draw")),
                    lost=_int(raw.get("lost")),
                    goals_for=_int(raw.get("goals")),
                    goals_against=_int(raw.get("opponentGoals")),
                    goal_diff=_int(raw.get("goalDiff")),
                    points=_int(raw.get("points")),
                )
            )
        return rows

    # ----------------------------------------------------------- game of day

    def get_game_of_day(self, team_id: str, now: datetime) -> Optional[Game]:
        """Look at the team's last and next match and pick the one on today's date.

        The last-match endpoint covers "game already played/finished today",
        the next-match endpoint covers "game still to come today". Both are
        needed so the final score can stay up until local midnight.
        """
        now_utc = now.astimezone(timezone.utc)
        games = []
        seen = set()
        for endpoint in ("getlastmatchbyleagueteam", "getnextmatchbyleagueteam"):
            url = "%s/%s/%d/%s" % (self.base_url, endpoint, self.league_id, team_id)
            raw = self._get(url, ttl=MATCH_CACHE_TTL, allow_missing=True)
            if not isinstance(raw, dict) or "matchID" not in raw:
                continue
            game = self._parse_match(raw, now_utc)
            if game.game_id not in seen:
                seen.add(game.game_id)
                games.append(game)
        return select_game_of_day(games, local_date(now_utc, self.tz), self.tz)

    # ---------------------------------------------------------------- parsing

    def _parse_match(self, raw: Dict[str, Any], now_utc: datetime) -> Game:
        try:
            start = self._parse_start(raw)
            home = self._parse_team(raw["team1"])
            away = self._parse_team(raw["team2"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("openligadb: cannot parse match: %r" % (exc,))

        periods = self._parse_periods(raw.get("matchResults"))
        finished = bool(raw.get("matchIsFinished"))
        if finished:
            status = GameStatus.FINISHED
        elif now_utc < start:
            status = GameStatus.SCHEDULED
        elif now_utc - start <= self.live_window:
            status = GameStatus.LIVE
        else:
            status = GameStatus.UNKNOWN

        home_score = away_score = None
        label = None
        if periods:
            last = periods[-1]
            home_score, away_score, label = last.home, last.away, last.label

        return Game(
            game_id=str(raw["matchID"]),
            home=home,
            away=away,
            start=start,
            status=status,
            home_score=home_score,
            away_score=away_score,
            period_label=label,
            clock=None,  # OpenLigaDB provides no running clock
            periods=tuple(periods),
            decided_in=self._decided_in(raw.get("matchResults")),
        )

    def _parse_start(self, raw: Dict[str, Any]) -> datetime:
        if raw.get("matchDateTimeUTC"):
            return parse_utc(str(raw["matchDateTimeUTC"]))
        # Fallback: local time without offset -> interpret in the league timezone.
        local = datetime.fromisoformat(str(raw["matchDateTime"]).split(".")[0])
        return local.replace(tzinfo=self.tz).astimezone(timezone.utc)

    @staticmethod
    def _parse_team(raw: Dict[str, Any]) -> Team:
        return Team(
            team_id=str(raw.get("teamId", raw.get("teamInfoId", ""))),
            name=str(raw.get("teamName", "")),
            short_name=str(raw.get("shortName", "") or ""),
            logo_url=str(raw.get("teamIconUrl", "") or ""),
        )

    @staticmethod
    def _parse_periods(results: Any) -> List[PeriodScore]:
        if not isinstance(results, list):
            return []
        ordered = sorted(
            (r for r in results if isinstance(r, dict)),
            key=lambda r: (_int(r.get("resultOrderID")), _int(r.get("resultID"))),
        )
        return [
            PeriodScore(
                label=str(r.get("resultName", "")),
                home=_int(r.get("pointsTeam1")),
                away=_int(r.get("pointsTeam2")),
            )
            for r in ordered
        ]

    @staticmethod
    def _decided_in(results: Any) -> Optional[str]:
        if not isinstance(results, list):
            return None
        kinds = {str(r.get("resultTypeKind", "")) for r in results if isinstance(r, dict)}
        names = {str(r.get("resultName", "")).lower() for r in results if isinstance(r, dict)}
        if "AfterPenalties" in kinds or "penalty" in names:
            return "SO"
        if "AfterExtraTime" in kinds or "overtime" in names:
            return "OT"
        return None

    # ------------------------------------------------------------------- http

    def _get(self, url: str, ttl: float, allow_missing: bool = False) -> Any:
        try:
            return self.http.get_json(url, ttl=ttl)
        except HttpError as exc:
            if allow_missing and exc.status == 404:
                return None
            raise ProviderError("openligadb: %s" % exc)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
