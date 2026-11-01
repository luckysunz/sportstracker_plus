"""Shared state between the data service (writer) and the display/web services (readers).

One JSON file, replaced atomically, so readers never see a half-written file
and no IPC is needed.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import Game, GameStatus, PeriodScore, TableRow, Team
from .timeutil import parse_utc

log = logging.getLogger(__name__)
STATE_VERSION = 1


@dataclass
class GameMarks:
    """When the data service first saw a game in a given phase (the sources give no end times)."""

    live_seen_at: Optional[datetime] = None
    halftime_seen_at: Optional[datetime] = None
    finished_seen_at: Optional[datetime] = None


@dataclass
class State:
    updated_at: Optional[datetime] = None          # last successful provider cycle (UTC)
    provider: str = ""
    favorite_team_id: str = ""
    game: Optional[Game] = None                    # game of the day of the favorite, if any
    table: List[TableRow] = field(default_factory=list)
    error: Optional[str] = None                    # text of the last failed cycle, None if it succeeded
    error_since: Optional[datetime] = None
    next_poll_at: Optional[datetime] = None
    # All league games of today (and of the last hours); None = the provider cannot list them,
    # then only ``game`` (the favorite's game) is known.
    games: Optional[List[Game]] = None
    marks: Dict[str, GameMarks] = field(default_factory=dict)


# -- serialisation -----------------------------------------------------------

def _iso(moment: Optional[datetime]) -> Optional[str]:
    return moment.astimezone(timezone.utc).isoformat() if moment else None


def _team_to(team: Team) -> Dict[str, Any]:
    return {"id": team.team_id, "name": team.name, "short": team.short_name, "logo": team.logo_url}


def _team_from(data: Dict[str, Any]) -> Team:
    return Team(str(data["id"]), data["name"], data.get("short", ""), data.get("logo", ""))


def game_to_dict(game: Game) -> Dict[str, Any]:
    return {
        "id": game.game_id, "home": _team_to(game.home), "away": _team_to(game.away), "start": _iso(game.start),
        "status": game.status.value, "home_score": game.home_score, "away_score": game.away_score,
        "period_label": game.period_label, "clock": game.clock, "period_break": game.period_break,
        "periods": [[p.label, p.home, p.away] for p in game.periods], "decided_in": game.decided_in,
        "halftime": game.halftime,
    }


def game_from_dict(data: Dict[str, Any]) -> Game:
    return Game(
        game_id=str(data["id"]), home=_team_from(data["home"]), away=_team_from(data["away"]),
        start=parse_utc(data["start"]), status=GameStatus(data["status"]),
        home_score=data.get("home_score"), away_score=data.get("away_score"),
        period_label=data.get("period_label"), clock=data.get("clock"),
        period_break=bool(data.get("period_break", False)),
        periods=tuple(PeriodScore(str(p[0]), int(p[1]), int(p[2])) for p in data.get("periods", [])),
        decided_in=data.get("decided_in"), halftime=bool(data.get("halftime", False)),
    )


def marks_to_dict(marks: GameMarks) -> Dict[str, Any]:
    return {"live": _iso(marks.live_seen_at), "halftime": _iso(marks.halftime_seen_at),
            "finished": _iso(marks.finished_seen_at)}


def marks_from_dict(data: Dict[str, Any]) -> GameMarks:
    def moment(key: str) -> Optional[datetime]:
        return parse_utc(data[key]) if data.get(key) else None

    return GameMarks(moment("live"), moment("halftime"), moment("finished"))


def state_to_dict(state: State) -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "updated_at": _iso(state.updated_at),
        "provider": state.provider,
        "favorite_team_id": state.favorite_team_id,
        "game": game_to_dict(state.game) if state.game else None,
        "table": [
            {"rank": r.rank, "team": _team_to(r.team), "played": r.played, "won": r.won, "draw": r.draw,
             "lost": r.lost, "goals_for": r.goals_for, "goals_against": r.goals_against,
             "goal_diff": r.goal_diff, "points": r.points}
            for r in state.table
        ],
        "error": state.error,
        "error_since": _iso(state.error_since),
        "next_poll_at": _iso(state.next_poll_at),
        "games": None if state.games is None else [game_to_dict(g) for g in state.games],
        "marks": {game_id: marks_to_dict(m) for game_id, m in state.marks.items()},
    }


def state_from_dict(data: Dict[str, Any]) -> State:
    if data.get("version") != STATE_VERSION:
        raise ValueError("unsupported state version %r" % data.get("version"))
    return State(
        updated_at=parse_utc(data["updated_at"]) if data.get("updated_at") else None,
        provider=data.get("provider", ""),
        favorite_team_id=str(data.get("favorite_team_id", "")),
        game=game_from_dict(data["game"]) if data.get("game") else None,
        table=[
            TableRow(rank=int(r["rank"]), team=_team_from(r["team"]), played=r.get("played", 0), won=r.get("won", 0),
                     draw=r.get("draw", 0), lost=r.get("lost", 0), goals_for=r.get("goals_for", 0),
                     goals_against=r.get("goals_against", 0), goal_diff=r.get("goal_diff", 0), points=r.get("points", 0))
            for r in data.get("table", [])
        ],
        error=data.get("error"),
        error_since=parse_utc(data["error_since"]) if data.get("error_since") else None,
        next_poll_at=parse_utc(data["next_poll_at"]) if data.get("next_poll_at") else None,
        games=None if data.get("games") is None else [game_from_dict(g) for g in data["games"]],
        marks={str(k): marks_from_dict(v) for k, v in (data.get("marks") or {}).items()},
    )


# -- file access -------------------------------------------------------------

def write_state(path: str, state: State) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(state_to_dict(state), out, ensure_ascii=False)
        os.chmod(tmp, 0o644)  # the display service runs as another user
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(path: str) -> Optional[State]:
    """The current state, or None if the file is missing or unreadable (a warning is logged)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return state_from_dict(json.load(handle))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("state file %s unusable: %s", path, exc)
        return None
