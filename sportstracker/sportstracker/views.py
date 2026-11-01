"""Which picture to show, given the shared state and the current time.

Two layers:

* ``choose_view`` is the original rule of version 0.3.0 (only the favorite's game of the day, otherwise the
  table). It is kept as the fallback for providers that cannot list all games of a league.
* ``plan`` (0.5.0) decides what the matrix shows when several games of the league are on:

  1. the favorite's game while it is running always has priority (main game);
  2. otherwise the running game with the best-placed team is the main game;
  3. the *other* games appear as cards for the time of their half-time break and for
     ``result_window_minutes`` after the end; main game and cards alternate;
  4. with no main game and no card: the favorite's final score until midnight, otherwise a rotation of
     kick-off notice (favorite plays later today), table and room view.

  The rotation is derived from the wall clock, so nothing has to be remembered between redraws.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .banner import has_text, rotation_seconds
from .config import Config
from .models import Game, GameStatus, TableRow
from .render import Renderer
from .selection import select_game_of_day
from .timeutil import local_date
from .satellites import SatReading, air_quality
from .sensor import SensorReading
from .state import GameMarks, State

STALE_DOT = (140, 0, 0)
MAX_RESULT_AGE = timedelta(hours=5)    # a "game over" card needs a tip-off less than this long ago
UNRANKED = 999


@dataclass(frozen=True)
class View:
    kind: str                       # "game", "table", "room", "message" (system notice), "banner" (user message), "picture"
    game: Optional[Game] = None
    table: Optional[List[TableRow]] = None
    lines: Optional[List[str]] = None
    stale: bool = False             # data is old or the last update failed -> small red dot in the corner
    now: Optional[datetime] = None  # room view: the time to show
    reading: Optional[SensorReading] = None
    show_climate: bool = True
    message: Optional[Dict[str, Any]] = None   # banner: the message section of the configuration
    elapsed: float = 0.0                       # banner / picture: seconds into the animation
    picture: Optional[Dict[str, Any]] = None   # picture: the picture section of the configuration
    satellite: Optional[Dict[str, Any]] = None  # satellite: {"name", "reading" (fresh or None), "quality" (index, label)}


def _is_stale(state: State, now: datetime, stale_after: float) -> bool:
    return state.error is not None or state.updated_at is None or (now - state.updated_at).total_seconds() > stale_after


def choose_view(state: Optional[State], now: datetime, tz: tzinfo, stale_after: float) -> View:
    """Rule of version 0.3.0: the favorite's game of today, else the table."""
    if state is None:
        return View("message", lines=["STARTE"])
    stale = _is_stale(state, now, stale_after)
    today = now.astimezone(tz).date()
    game = state.game
    if game is not None and game.status != GameStatus.CANCELLED and game.start.astimezone(tz).date() == today:
        return View("game", game=game, stale=stale)
    if state.table:
        return View("table", table=state.table, stale=stale)
    return View("message", lines=["KEINE", "DATEN"], stale=stale)


def render_view(renderer: Renderer, view: View, favorite_id: str) -> Image.Image:
    if view.kind == "game" and view.game is not None:
        image = renderer.render_game(view.game)
    elif view.kind == "table" and view.table is not None:
        image = renderer.render_table(view.table, favorite_id)
    elif view.kind == "room" and view.now is not None:
        image = renderer.render_room(view.now, view.reading, view.show_climate)
    elif view.kind == "banner" and view.message is not None and getattr(renderer, "banner", None) is not None:
        image = renderer.banner.frame(view.message, view.elapsed)
    elif view.kind == "satellite" and view.satellite is not None:
        image = renderer.render_satellite(view.satellite["name"], view.satellite["reading"], view.satellite["quality"])
    elif view.kind == "picture" and view.picture is not None and getattr(renderer, "picture", None) is not None:
        image = renderer.picture.frame(view.picture, view.elapsed)
    else:
        image = renderer.render_message(view.lines or ["KEINE", "DATEN"])
    if view.stale:
        image.putpixel((image.width - 1, 0), STALE_DOT)
    return image


# ---------------------------------------------------------------------------
# plan (0.5.0)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    kind: str                       # "game", "table", "room", "message", "banner", "picture" or "satellite"
    game: Optional[Game] = None
    seconds: int = 10
    role: str = "idle"              # "main", "card" or "idle"
    lines: Optional[Tuple[str, ...]] = None
    satellite: Optional[str] = None  # satellite: its key


@dataclass(frozen=True)
class Plan:
    items: Tuple[Item, ...]
    stale: bool = False
    reason: str = ""

    def item_at(self, now: datetime) -> Item:
        return self.locate(now)[0]

    def locate(self, now: datetime) -> Tuple[Item, float]:
        """The item that is due and the seconds since it came up (animations start with their item)."""
        timestamp = now.timestamp()
        if len(self.items) == 1:
            return self.items[0], timestamp % 86400.0
        total = sum(item.seconds for item in self.items)
        whole = int(timestamp)
        slot = whole % total
        for item in self.items:
            if slot < item.seconds:
                return item, slot + (timestamp - whole)
            slot -= item.seconds
        return self.items[-1], 0.0  # unreachable, keeps type checkers calm


def game_rank(game: Game, ranks: Dict[str, int]) -> Tuple[int, int, datetime, str]:
    """Sort key: better placed team first, then smaller rank sum, then earlier tip-off."""
    home = ranks.get(game.home.team_id, UNRANKED)
    away = ranks.get(game.away.team_id, UNRANKED)
    return (min(home, away), home + away, game.start, game.game_id)


def _within(moment: Optional[datetime], now: datetime, minutes: int) -> bool:
    return moment is not None and timedelta(0) <= now - moment < timedelta(minutes=minutes)


def result_window_open(game: Game, marks: Optional[GameMarks], now: datetime, cfg: Config) -> bool:
    """A finished game is shown as a card for ``result_window_minutes`` after the end was first seen."""
    if game.status not in (GameStatus.FINISHED, GameStatus.UNKNOWN) or marks is None:
        return False
    return _within(marks.finished_seen_at, now, cfg.result_window_minutes) and now - game.start < MAX_RESULT_AGE


def halftime_window_open(game: Game, marks: Optional[GameMarks], now: datetime, cfg: Config) -> bool:
    """The half-time break is shown while it lasts (but at most ``halftime_max_minutes``)."""
    if game.status != GameStatus.LIVE or not game.halftime or marks is None:
        return False
    return _within(marks.halftime_seen_at, now, cfg.halftime_max_minutes)


def card_open(game: Game, marks: Optional[GameMarks], now: datetime, cfg: Config) -> bool:
    return result_window_open(game, marks, now, cfg) or halftime_window_open(game, marks, now, cfg)


def card_wanted(game: Game, marks: Optional[GameMarks], now: datetime, cfg: Config, tz: tzinfo) -> bool:
    """A card for this game: half-time / result window, or (``gameday_results``) any finished game of today."""
    if card_open(game, marks, now, cfg):
        return True
    return bool(
        cfg.gameday_results and game.status == GameStatus.FINISHED and game.home_score is not None
        and local_date(game.start, tz) == local_date(now, tz)
    )


def banner_wanted(cfg: Config) -> bool:
    return bool(cfg.message.get("enabled") and has_text(cfg.message))


def banner_item(cfg: Config) -> Item:
    width = int(cfg.matrix["cols"]) * int(cfg.matrix["chain_length"])
    return Item("banner", seconds=rotation_seconds(cfg.message, width), role="idle")


def picture_item(cfg: Config, seconds: Optional[int] = None) -> Item:
    return Item("picture", seconds=int(seconds or cfg.picture["seconds"]), role="idle")


def plan(state: Optional[State], now: datetime, cfg: Config, tz: tzinfo, stale_after: float,
         picture: bool = False, picture_seconds: Optional[int] = None) -> Plan:
    """``picture``: a stored picture exists (the plan does not touch the file system itself);
    ``picture_seconds``: length of one round through the gallery."""
    wanted = banner_wanted(cfg)
    pic = bool(picture and cfg.picture.get("enabled"))
    if wanted and cfg.message.get("permanent"):
        return Plan((banner_item(cfg),), False, "Nachricht dauerhaft")
    if pic and cfg.picture.get("permanent"):
        return Plan((picture_item(cfg, picture_seconds),), False, "Bild dauerhaft")
    extras: List[Item] = ([banner_item(cfg)] if wanted else []) + ([picture_item(cfg, picture_seconds)] if pic else [])
    extras_text = " und ".join(text for text, on in (("die Nachricht", wanted), ("das Bild", pic)) if on)
    if state is None:
        return Plan((Item("message", lines=("STARTE",), role="idle"),), False, "Warte auf Daten")
    stale = _is_stale(state, now, stale_after)
    listed = state.games is not None
    games = list(state.games) if listed else ([state.game] if state.game else [])
    games = [g for g in games if g.status != GameStatus.CANCELLED]
    favorite = cfg.favorite_team_id
    ranks = {row.team.team_id: row.rank for row in state.table}

    def is_favorite(game: Game) -> bool:
        return favorite in (game.home.team_id, game.away.team_id)

    others = cfg.show_other_games and listed

    # 1. main game
    main: Optional[Game] = None
    reason = ""
    favorite_live = [g for g in games if is_favorite(g) and g.status == GameStatus.LIVE]
    if favorite_live:
        main = min(favorite_live, key=lambda g: g.start)
        reason = "Das Lieblingsteam spielt"
    elif others:
        live = [g for g in games if g.status == GameStatus.LIVE]
        if live:
            main = min(live, key=lambda g: game_rank(g, ranks))
            reason = "Laufendes Spiel mit der besten Platzierung"

    # 2. cards: all listed games, or only the favorite's when other games are switched off (and the results of the
    #    day are kept); the favorite's own result comes first
    today = now.astimezone(tz).date()
    favorite_game = select_game_of_day([g for g in games if is_favorite(g)], today, tz)
    pool = games if others else ([g for g in games if is_favorite(g)] if cfg.gameday_results else [])
    cards: List[Game] = sorted(
        (g for g in pool if g is not main and card_wanted(g, state.marks.get(g.game_id), now, cfg, tz)),
        key=lambda g: ((0 if is_favorite(g) else 1) if cfg.gameday_results else 0,) + game_rank(g, ranks),
    )

    if main is not None or cards:
        items = [Item("game", main, cfg.rotation_main_seconds, "main")] if main is not None else []
        items += [Item("game", card, cfg.rotation_card_seconds, "card") for card in cards]
        if main is None:
            reason = "Ergebnis eines anderen Spiels"
        elif cards:
            reason += ", dazu Ergebniskarten"
        if main is None and cfg.gameday_results:
            # nothing is running: the results of the day and the table take turns until midnight
            if favorite_game is not None and favorite_game.status == GameStatus.SCHEDULED:
                items.append(Item("game", favorite_game, cfg.idle_kickoff_seconds, "idle"))
            if state.table:
                items.append(Item("table", seconds=cfg.idle_table_seconds, role="idle"))
            if cfg.idle_mode in ("alternate", "room"):
                items.append(Item("room", seconds=cfg.idle_room_seconds, role="idle"))
            items.extend(extras)
            return Plan(tuple(items), stale, "Spieltag: Ergebnisse und Tabelle im Wechsel")
        if wanted and cfg.message.get("during_games"):
            items.append(banner_item(cfg))
            reason += ", dazu die Nachricht"
        return Plan(tuple(items), stale, reason)

    # 3. nothing running, no card
    if favorite_game is not None and favorite_game.status in (GameStatus.FINISHED, GameStatus.UNKNOWN):
        if extras:
            return Plan((Item("game", favorite_game, 60, "idle"), *extras), stale,
                        "Endstand des Lieblingsteams bis Mitternacht, dazu " + extras_text)
        return Plan((Item("game", favorite_game, 60, "idle"),), stale, "Endstand des Lieblingsteams bis Mitternacht")

    idle: List[Item] = []
    if favorite_game is not None and favorite_game.status == GameStatus.SCHEDULED:
        idle.append(Item("game", favorite_game, cfg.idle_kickoff_seconds, "idle"))
    # "livingmodus ohne Tabelle": the table is dropped from the idle list, the room view takes its place
    if cfg.idle_mode in ("alternate", "table") and state.table and not cfg.idle_hide_table:
        idle.append(Item("table", seconds=cfg.idle_table_seconds, role="idle"))
    if cfg.idle_mode in ("alternate", "room") or cfg.idle_hide_table:
        idle.append(Item("room", seconds=cfg.idle_room_seconds, role="idle"))
    if cfg.satellites.get("enabled"):
        idle.extend(Item("satellite", seconds=int(cfg.satellites["seconds"]), role="idle", satellite=sat["key"])
                    for sat in cfg.satellites["items"] if sat["enabled"])
    idle.extend(extras)
    if not idle:
        return Plan((Item("message", lines=("KEINE", "DATEN"), role="idle"),), stale, "Keine Daten")
    reason = "Kein Spiel läuft"
    if favorite_game is not None:
        reason += ", das Lieblingsteam spielt später"
    return Plan(tuple(idle), stale, reason)


def view_for_item(item: Item, state: Optional[State], now: datetime, stale: bool,
                  reading: Optional[SensorReading], cfg: Config, elapsed: float = 0.0,
                  satellites: Optional[Dict[str, SatReading]] = None) -> View:
    if item.kind == "satellite":
        entry = next((s for s in cfg.satellites["items"] if s["key"] == item.satellite), None)
        if entry is None:
            return View("message", lines=["KEINE", "DATEN"], stale=stale)
        value = (satellites or {}).get(entry["key"])
        fresh = value if value is not None and value.is_fresh(now) else None
        quality = air_quality(fresh, entry) if fresh is not None else None
        return View("satellite", stale=stale, satellite={"name": entry["name"], "reading": fresh, "quality": quality})
    if item.kind == "banner":
        return View("banner", message=dict(cfg.message), elapsed=elapsed)
    if item.kind == "picture":
        return View("picture", picture=dict(cfg.picture), elapsed=elapsed)
    if item.kind == "game" and item.game is not None:
        return View("game", game=item.game, stale=stale)
    if item.kind == "table" and state is not None and state.table:
        return View("table", table=state.table, stale=stale)
    if item.kind == "room":
        fresh = reading if reading is not None and reading.is_fresh(now) else None
        return View("room", stale=stale, now=now, reading=fresh, show_climate=bool(cfg.sensor.get("enabled")))
    return View("message", lines=list(item.lines or ("KEINE", "DATEN")), stale=stale)
