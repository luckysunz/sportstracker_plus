"""RBBL provider: Genius Sports "hosted" widget (German wheelchair basketball).

The federation website (rollstuhlbasketball.de/ergebnisse/) renders schedule
and standings with a Genius Sports hosted widget. The widget loads JSON
fragments from public URLs; this provider reads the same fragments:

  {base}/embednf/{account}/{lang}/competition/{id}/schedule   -> JSON {css, js, html}
  {base}/embednf/{account}/{lang}/competition/{id}/standings  -> JSON {css, js, html}
  {base}/ldata/{sport}/competitions/comp{id}.json              -> live data per match

Only the ``html`` member is used; no JavaScript is executed. RBBL 1 26/27 is
competition 49807, RBBL 2 Div. A 26/27 is 49808.

Notes and risks (see project doc "Quellen-Evaluierung"):
  * No documented API; the markup can change. Parsing failures raise
    ProviderError so the caller keeps showing the last good state.
  * The schedule fragment is large (~440 KB). The parsed result is cached
    (``schedule_ttl``) so the Pi parses it rarely; during a game the small
    live JSON overlays status, score, period and clock.
  * Match times are shown in the widget in local German time (assumption,
    configurable via ``source_timezone``).
  * Live JSON field layout is derived from the widget's JavaScript
    (clock, status, period, periodType, scores[1]/scores[2]); it has not been
    seen with a real live game yet.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..errors import HttpError, ProviderError
from ..models import Game, GameStatus, TableRow, Team
from ..selection import select_game_of_day
from ..timeutil import local_date
from .base import Provider

BASE_URL = "https://hosted.dcd.shared.geniussports.com"
DEFAULT_ACCOUNT = "WHBA"
DEFAULT_SPORT = "basketball"

STANDINGS_TTL = 60
LIVE_TTL = 15
BUSY_SCHEDULE_TTL = 60.0                # schedule refresh while a game is due or running (results may be entered late)
BUSY_LEAD_TIME = timedelta(minutes=10)
LIVE_LEAD_TIME = timedelta(minutes=30)  # start asking the live feed shortly before tip-off
RECENT_WINDOW = timedelta(hours=6)      # games that started this long ago are still listed
STALE_AFTER = timedelta(hours=5)        # a game not marked finished this long after tip-off is "unknown"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TIME_RE = re.compile(
    r"^\s*([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4}),?\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$"
)
_TEAM_ID_RE = re.compile(r"/team/(\d+)")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")


def parse_widget_time(text: str, source_tz: tzinfo) -> datetime:
    """Parse e.g. ``Oct 17, 2026, 5:00 PM`` (English month names, local time)."""
    match = _TIME_RE.match(text or "")
    if not match:
        raise ValueError("unrecognised match time: %r" % (text,))
    month = _MONTHS.get(match.group(1)[:3].lower())
    if month is None:
        raise ValueError("unrecognised month: %r" % (match.group(1),))
    hour = int(match.group(4))
    minute = int(match.group(5))
    marker = match.group(6)
    if marker:
        hour = hour % 12 + (12 if marker.lower() == "pm" else 0)
    local = datetime(int(match.group(3)), month, int(match.group(2)), hour, minute, tzinfo=source_tz)
    return local.astimezone(timezone.utc)


def _cls(name: str) -> str:
    """XPath predicate: element has CSS class ``name``."""
    return "contains(concat(' ', normalize-space(@class), ' '), ' %s ')" % name


class GeniusRbblProvider(Provider):
    name = "genius_rbbl"

    def __init__(
        self,
        http: Any,
        competition_id: int,
        tz: tzinfo,
        base_url: str = BASE_URL,
        account: str = DEFAULT_ACCOUNT,
        language: str = "en",
        sport: str = DEFAULT_SPORT,
        source_tz: Optional[tzinfo] = None,
        schedule_ttl: float = 600.0,
        busy_schedule_ttl: float = BUSY_SCHEDULE_TTL,
    ) -> None:
        super().__init__(tz)
        self.http = http
        self.competition_id = int(competition_id)
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.language = language
        self.sport = sport
        self.source_tz = source_tz or ZoneInfo("Europe/Berlin")
        self.schedule_ttl = float(schedule_ttl)
        self.busy_schedule_ttl = float(busy_schedule_ttl)
        self._games: List[Game] = []
        self._games_fetched: Optional[datetime] = None

    @classmethod
    def from_params(cls, params: Dict[str, Any], http: Any, tz: tzinfo) -> "GeniusRbblProvider":
        if "competition_id" not in params:
            raise ProviderError("genius_rbbl: missing provider_params: competition_id")
        try:
            source_tz = ZoneInfo(str(params.get("source_timezone", "Europe/Berlin")))
        except Exception as exc:  # unknown timezone name
            raise ProviderError("genius_rbbl: bad source_timezone: %s" % exc)
        return cls(
            http=http,
            competition_id=int(params["competition_id"]),
            tz=tz,
            base_url=str(params.get("base_url", BASE_URL)),
            account=str(params.get("account", DEFAULT_ACCOUNT)),
            language=str(params.get("language", "en")),
            sport=str(params.get("sport", DEFAULT_SPORT)),
            source_tz=source_tz,
            schedule_ttl=float(params.get("schedule_ttl", 600)),
            busy_schedule_ttl=float(params.get("busy_schedule_ttl", BUSY_SCHEDULE_TTL)),
        )

    # ------------------------------------------------------------- URL helpers

    def _fragment_url(self, page: str) -> str:
        return "%s/embednf/%s/%s/competition/%d/%s" % (
            self.base_url, self.account, self.language, self.competition_id, page
        )

    def _live_url(self) -> str:
        return "%s/ldata/%s/competitions/comp%d.json" % (self.base_url, self.sport, self.competition_id)

    # ------------------------------------------------------------------ teams

    def list_teams(self) -> List[Team]:
        return [row.team for row in self.get_table()]

    # ------------------------------------------------------------------ table

    def get_table(self) -> List[TableRow]:
        root = self._fetch_fragment("standings", ttl=STANDINGS_TTL)
        rows = []
        for tr in root.xpath("//table[%s]//tr[td]" % _cls("standings")):
            cells = tr.xpath("./td")
            if len(cells) < 3:
                continue
            team = self._team_from(tr)
            if team is None:
                raise ProviderError("genius_rbbl: standings row without team id")
            rows.append(
                TableRow(
                    rank=_int(_text(cells[0])),
                    team=team,
                    played=_int(_cell_text(tr, "STANDINGS_played")),
                    won=_int(_cell_text(tr, "STANDINGS_won")),
                    lost=_int(_cell_text(tr, "STANDINGS_lost")),
                    goal_diff=_int(_cell_text(tr, "STANDINGS_pointsDiff")),
                    points=_int(_cell_text(tr, "STANDINGS_standingPoints")),
                )
            )
        if not rows:
            raise ProviderError("genius_rbbl: no standings rows found (markup changed?)")
        return rows

    # ----------------------------------------------------------- game of day

    def get_game_of_day(self, team_id: str, now: datetime) -> Optional[Game]:
        now_utc = now.astimezone(timezone.utc)
        games = [
            g for g in self._load_games(now_utc)
            if team_id in (g.home.team_id, g.away.team_id)
        ]
        game = select_game_of_day(games, local_date(now_utc, self.tz), self.tz)
        if game is None:
            return None
        if game.status in (GameStatus.SCHEDULED, GameStatus.LIVE) and now_utc >= game.start - LIVE_LEAD_TIME:
            game = self._overlay_live(game)
        return game

    def get_games_of_day(self, now: datetime) -> List[Game]:
        """All games of the competition that start today (local date) or started within the last hours."""
        now_utc = now.astimezone(timezone.utc)
        today = local_date(now_utc, self.tz)
        result = []
        for game in self._load_games(now_utc):
            if game.status == GameStatus.CANCELLED:
                continue
            started_recently = game.start <= now_utc and now_utc - game.start < RECENT_WINDOW
            if local_date(game.start, self.tz) != today and not started_recently:
                continue
            if game.status in (GameStatus.SCHEDULED, GameStatus.LIVE) and now_utc >= game.start - LIVE_LEAD_TIME:
                game = self._overlay_live(game)
            if game.status in (GameStatus.SCHEDULED, GameStatus.LIVE) and now_utc - game.start > STALE_AFTER:
                game = dataclasses.replace(game, status=GameStatus.UNKNOWN, clock=None, period_break=False, halftime=False)
            result.append(game)
        return sorted(result, key=lambda g: (g.start, g.game_id))

    def _schedule_ttl(self, now_utc: datetime) -> float:
        """Refresh the schedule more often while a game is due or running and has no final result yet.

        The RBBL results are often entered only after the game (the live feed stays silent), so the schedule
        itself is the only place where the final score shows up.
        """
        for game in self._games:
            if game.status in (GameStatus.SCHEDULED, GameStatus.LIVE, GameStatus.UNKNOWN) \
                    and game.start - BUSY_LEAD_TIME <= now_utc <= game.start + STALE_AFTER:
                return min(self.schedule_ttl, self.busy_schedule_ttl)
        return self.schedule_ttl

    def _load_games(self, now_utc: datetime) -> List[Game]:
        fresh = (
            self._games_fetched is not None
            and 0 <= (now_utc - self._games_fetched).total_seconds() < self._schedule_ttl(now_utc)
        )
        if fresh:
            return self._games
        root = self._fetch_fragment("schedule", ttl=0)
        live_ids = self._live_match_ids(root)
        games = []
        for element in root.xpath("//div[%s]" % _cls("match-wrap")):
            games.append(self._parse_match(element, live_ids))
        if not games:
            raise ProviderError("genius_rbbl: no matches found in schedule (markup changed?)")
        self._games = games
        self._games_fetched = now_utc
        return games

    # ---------------------------------------------------------------- parsing

    def _parse_match(self, element: Any, live_ids: set) -> Game:
        match_id_attr = element.get("id", "")
        id_match = re.search(r"(\d+)$", match_id_attr)
        if not id_match:
            raise ProviderError("genius_rbbl: match without id: %r" % match_id_attr)
        game_id = id_match.group(1)

        home_el = _first(element.xpath(".//div[%s]" % _cls("home-team")))
        away_el = _first(element.xpath(".//div[%s]" % _cls("away-team")))
        home = self._team_from(home_el) if home_el is not None else None
        away = self._team_from(away_el) if away_el is not None else None
        if home is None or away is None:
            raise ProviderError("genius_rbbl: match %s without both teams" % game_id)

        time_text = _text(_first(element.xpath(".//div[%s]/span" % _cls("match-time"))))
        try:
            start = parse_widget_time(time_text, self.source_tz)
        except ValueError as exc:
            raise ProviderError("genius_rbbl: match %s: %s" % (game_id, exc))

        tokens = [c[len("STATUS_"):] for c in element.get("class", "").split() if c.startswith("STATUS_")]
        token = tokens[0].upper() if tokens else ""
        cancelled_span = bool(
            element.xpath(".//div[%s]//span[%s or %s]" % (_cls("notlive"), _cls("postponed"), _cls("cancelled")))
        )
        status = _status_from(token, game_id in live_ids, cancelled_span)

        home_score = _score(_text(_first(element.xpath(".//div[%s]//div[%s]" % (_cls("homescore"), _cls("fake-cell"))))))
        away_score = _score(_text(_first(element.xpath(".//div[%s]//div[%s]" % (_cls("awayscore"), _cls("fake-cell"))))))
        if status == GameStatus.SCHEDULED:
            home_score = away_score = None
        elif status == GameStatus.LIVE and not home_score and not away_score:
            home_score = away_score = None      # "0:0" in the markup of a running game is only the placeholder

        return Game(
            game_id=game_id,
            home=home,
            away=away,
            start=start,
            status=status,
            home_score=home_score,
            away_score=away_score,
        )

    @staticmethod
    def _team_from(element: Any) -> Optional[Team]:
        href = _first(element.xpath(".//a/@href"))
        match = _TEAM_ID_RE.search(href or "")
        if match:
            team_id = match.group(1)
        else:
            row_class = element.get("class", "")
            row_match = re.search(r"standings_team_(\d+)", row_class)
            if not row_match:
                return None
            team_id = row_match.group(1)
        name = _text(_first(element.xpath(".//span[%s]" % _cls("team-name-full"))))
        if not name:
            name = _first(element.xpath(".//img/@alt")) or ""
        code = _text(_first(element.xpath(".//span[%s]" % _cls("team-name-code"))))
        logo = _first(element.xpath(".//img/@src")) or ""
        return Team(team_id=team_id, name=name, short_name=code, logo_url=logo)

    @staticmethod
    def _live_match_ids(root: Any) -> set:
        values = root.xpath("//input[@id='_livematches']/@value")
        if not values:
            return set()
        return {part.strip() for part in values[0].split(",") if part.strip()}

    # ------------------------------------------------------------ live overlay

    def _overlay_live(self, game: Game) -> Game:
        """Merge the small live feed into the cached schedule entry.

        Failures are not fatal: the static schedule data is still valid.
        """
        try:
            data = self.http.get_json(self._live_url(), ttl=LIVE_TTL)
        except HttpError:
            return game
        if not isinstance(data, dict):
            return game
        entry = data.get(game.game_id)
        if not isinstance(entry, dict):
            return game

        status_text = str(entry.get("status", "")).upper()
        scores = _live_scores(entry.get("scores"))
        changes: Dict[str, Any] = {}
        # An entry that is only READY (prepared, not started) carries 0:0; it stays like that in the feed when the
        # game was never scored live. Such a placeholder must not replace the schedule's result.
        if scores is not None and status_text in ("INPROGRESS", "PERIODBREAK", "COMPLETE", "FINISHED"):
            changes["home_score"], changes["away_score"] = scores

        if status_text in ("COMPLETE", "FINISHED"):
            changes["status"] = GameStatus.FINISHED
            changes["clock"] = None
            changes["period_break"] = False
            changes["halftime"] = False
        elif status_text in ("INPROGRESS", "PERIODBREAK"):
            changes["status"] = GameStatus.LIVE
            changes["period_break"] = status_text == "PERIODBREAK"
            changes["period_label"] = _period_label(entry.get("period"), entry.get("periodType"))
            changes["clock"] = None if status_text == "PERIODBREAK" else _format_clock(entry.get("clock"))
            changes["halftime"] = status_text == "PERIODBREAK" and changes["period_label"] == "Q2"
        return dataclasses.replace(game, **changes) if changes else game

    # ------------------------------------------------------------------- http

    def _fetch_fragment(self, page: str, ttl: float) -> Any:
        """Download a widget fragment and return the parsed ``html`` member."""
        url = self._fragment_url(page)
        try:
            payload = self.http.get_json(url, ttl=ttl)
        except HttpError as exc:
            raise ProviderError("genius_rbbl: %s" % exc)
        html_text = payload.get("html") if isinstance(payload, dict) else None
        if not isinstance(html_text, str) or not html_text.strip():
            raise ProviderError("genius_rbbl: %s has no html member" % page)
        try:
            from lxml import html as lxml_html  # python3-lxml
        except ImportError:
            raise ProviderError("genius_rbbl: lxml is required (apt install python3-lxml)")
        try:
            return lxml_html.fromstring(html_text)
        except Exception as exc:  # lxml.etree.ParserError and friends
            raise ProviderError("genius_rbbl: cannot parse %s html: %s" % (page, exc))


# ------------------------------------------------------------------- helpers


def _first(items: Any) -> Any:
    return items[0] if items else None


def _text(element: Any) -> str:
    if element is None:
        return ""
    if isinstance(element, str):
        return element.replace("\xa0", " ").strip()
    return element.text_content().replace("\xa0", " ").strip()


def _cell_text(row: Any, cls: str) -> str:
    return _text(_first(row.xpath("./td[%s]" % _cls(cls))))


def _int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _score(text: str) -> Optional[int]:
    text = text.strip()
    return int(text) if text.isdigit() else None


def _status_from(token: str, in_live_list: bool, cancelled_span: bool) -> GameStatus:
    if cancelled_span or "CANCEL" in token or "POSTPONE" in token:
        return GameStatus.CANCELLED
    if token in ("COMPLETE", "FINISHED"):
        return GameStatus.FINISHED
    if in_live_list or "LIVE" in token or "PROGRESS" in token:
        return GameStatus.LIVE
    if token == "SCHEDULED":
        return GameStatus.SCHEDULED
    return GameStatus.UNKNOWN


def _live_scores(scores: Any) -> Optional[Tuple[int, int]]:
    """Live feed scores: the widget reads scores[1] (home) and scores[2] (away)."""
    try:
        if isinstance(scores, dict):
            home = scores.get("1", scores.get(1))
            away = scores.get("2", scores.get(2))
        elif isinstance(scores, (list, tuple)):
            if len(scores) >= 3:
                home, away = scores[1], scores[2]
            elif len(scores) == 2:
                home, away = scores[0], scores[1]
            else:
                return None
        else:
            return None
        return int(home), int(away)
    except (TypeError, ValueError):
        return None


def _period_label(period: Any, period_type: Any) -> Optional[str]:
    try:
        number = int(period)
    except (TypeError, ValueError):
        return None
    kind = str(period_type or "REGULAR").upper()
    if kind == "EXTRATIME":
        return "OT" if number <= 1 else "OT%d" % number
    if kind == "PENALTYSHOOTOUT":
        return "SO"
    return "Q%d" % number


def _format_clock(value: Any) -> Optional[str]:
    """``07:32:00`` -> ``07:32`` (assumption: hh-less mm:ss:tenths-or-frames).

    Unknown formats are passed through unchanged.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "00:00:00":
        return None
    match = _CLOCK_RE.match(text)
    if match and match.group(3) == "00":
        return "%s:%s" % (match.group(1), match.group(2))
    return text
