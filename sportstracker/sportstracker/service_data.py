"""Data service: polls the league provider and writes the shared state file.

    python3 -m sportstracker.service_data --config config/config.json

Poll rhythm (configurable): every ``poll_game_seconds`` (60) while any game of the league is running,
while a result card is still shown, or while the favorite's game of the day is scheduled or running;
otherwise every ``poll_idle_seconds`` (900), with a wake-up five minutes before the next kick-off of the
league. A wake-up is always scheduled right after local midnight, so the table appears on time after the
final score.

The sources give no end times. For every game of the day the service therefore records when it first saw it
live, at half-time and finished (``State.marks``); the display uses these marks for its result windows.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .config import Config, load_config
from .errors import ConfigError, HttpError, ProviderError
from .http import HttpClient
from .models import Game, GameStatus
from .providers import create_provider
from .runtime import PROJECT_ROOT, build_logo_cache, logo_sizes_for, matrix_size, state_path
from .selection import select_game_of_day
from .state import GameMarks, State, read_state, write_state
from .views import card_open

log = logging.getLogger("sportstracker.data")

MIDNIGHT_MARGIN = 5.0
KICKOFF_LEAD = 300.0   # start polling every minute this many seconds before a kick-off


def poll_interval(game: Optional[Game], now: datetime, cfg: Config, tz: ZoneInfo) -> float:
    """Seconds until the next provider poll."""
    active = game is not None and game.status in (GameStatus.SCHEDULED, GameStatus.LIVE)
    base = float(cfg.poll_game_seconds if active else cfg.poll_idle_seconds)
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    until_midnight = (midnight - local).total_seconds() + MIDNIGHT_MARGIN
    return max(5.0, min(base, until_midnight))


def poll_interval_for(
    games: Optional[list], fav_game: Optional[Game], marks: dict, now: datetime, cfg: Config, tz: ZoneInfo
) -> float:
    """Seconds until the next poll, taking all games of the league into account."""
    active = fav_game is not None and fav_game.status in (GameStatus.SCHEDULED, GameStatus.LIVE)
    wake_in = None
    for game in games or []:
        if game.status == GameStatus.LIVE or card_open(game, marks.get(game.game_id), now, cfg):
            active = True
        elif game.status == GameStatus.SCHEDULED:
            until = (game.start - now).total_seconds() - KICKOFF_LEAD
            if until <= 0:
                active = True
            elif wake_in is None or until < wake_in:
                wake_in = until
    base = float(cfg.poll_game_seconds if active else cfg.poll_idle_seconds)
    if wake_in is not None:
        base = min(base, wake_in)
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    until_midnight = (midnight - local).total_seconds() + MIDNIGHT_MARGIN
    return max(5.0, min(base, until_midnight))


def update_marks(previous: dict, games: list, now: datetime) -> dict:
    """Remember when each game was first seen live / at half-time / finished (only games still listed)."""
    marks = {}
    for game in games:
        old = previous.get(game.game_id, GameMarks())
        live, halftime, finished = old.live_seen_at, old.halftime_seen_at, old.finished_seen_at
        if game.status == GameStatus.LIVE:
            live = live or now
            halftime = (halftime or now) if game.halftime else None
            finished = None
        elif game.status in (GameStatus.FINISHED, GameStatus.UNKNOWN):
            finished = finished or now
            halftime = None
        marks[game.game_id] = GameMarks(live, halftime, finished)
    return marks


class DataService:
    def __init__(
        self,
        config_path: str,
        root: str = PROJECT_ROOT,
        http: Any = None,
        now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        provider_factory: Callable[..., Any] = create_provider,
    ) -> None:
        self.config_path = config_path
        self.root = root
        self.http = http or HttpClient()
        self._now = now_func
        self._provider_factory = provider_factory
        self._stop = threading.Event()
        self._provider: Any = None
        self._provider_key = ""
        self._logos: Any = None
        self._logos_key = ""
        self._state: Optional[State] = None
        self._table_fetched: Optional[datetime] = None
        self._table_key = ""
        self._config_mtime = 0.0
        self.cfg: Optional[Config] = None

    # -- one cycle --------------------------------------------------------------

    def _load(self) -> Config:
        cfg = load_config(self.config_path)
        try:
            self._config_mtime = os.path.getmtime(self.config_path)
        except OSError:
            pass
        self.cfg = cfg
        return cfg

    def _provider_for(self, cfg: Config, tz: ZoneInfo) -> Any:
        key = json.dumps([cfg.provider, cfg.provider_params, cfg.timezone], sort_keys=True)
        if self._provider is None or key != self._provider_key:
            self._provider = self._provider_factory(cfg.provider, cfg.provider_params, self.http, tz)
            self._provider_key = key
        return self._provider

    def _warm_logos(self, cfg: Config, games: list, table: list) -> None:
        """Download/scale the logos now, so the display service never needs the network."""
        key = json.dumps([cfg.provider, cfg.provider_params, cfg.logo_backing, cfg.logo_backing_teams, cfg.cache_dir])
        if self._logos is None or key != self._logos_key:
            self._logos = build_logo_cache(cfg, self.http, root=self.root)
            self._logos_key = key
        game_size, table_size = logo_sizes_for(cfg)
        for game in games:
            for team in (game.home, game.away):
                self._logos.get(team, game_size)
        if table_size > 0:
            for row in table:
                self._logos.get(row.team, table_size)

    def cycle(self) -> float:
        """Run one poll; returns the seconds to wait before the next one."""
        cfg = self._load()
        tz = ZoneInfo(cfg.timezone)
        now = self._now()
        provider = self._provider_for(cfg, tz)
        fav = cfg.favorite_team_id
        key = "%s|%s" % (self._provider_key, fav)
        if self._state is None:
            self._state = read_state(state_path(cfg, self.root))
        prev = self._state if (self._state is not None and self._state.provider == key) else None

        try:
            lister = getattr(provider, "get_games_of_day", None)
            games = lister(now) if lister is not None else None
            if games is not None:
                fav_games = [g for g in games if fav in (g.home.team_id, g.away.team_id)] if fav else []
                game = select_game_of_day(fav_games, now.astimezone(tz).date(), tz)
            else:
                game = provider.get_game_of_day(fav, now) if fav else None
            table = self._table(provider, key, prev, now, cfg, game)
            listed = games if games is not None else ([game] if game is not None else [])
            marks = update_marks(prev.marks if prev is not None else {}, listed, now)
            self._warm_logos(cfg, listed, table)
            interval = poll_interval_for(listed, game, marks, now, cfg, tz)
            state = State(updated_at=now, provider=key, favorite_team_id=fav, game=game, table=table,
                          next_poll_at=now + timedelta(seconds=interval), games=games, marks=marks)
        except (ProviderError, HttpError) as exc:
            log.warning("poll failed: %s", exc)
            interval = min(60.0, float(cfg.poll_game_seconds))
            if prev is not None:
                state = State(prev.updated_at, key, fav, prev.game, prev.table, str(exc)[:300],
                              prev.error_since or now, now + timedelta(seconds=interval), prev.games, prev.marks)
            else:
                state = State(None, key, fav, None, [], str(exc)[:300], now, now + timedelta(seconds=interval))
        self._state = state
        write_state(state_path(cfg, self.root), state)
        log.info("state written: game=%s table=%d rows, next poll in %.0f s",
                 state.game.status.value if state.game else "none", len(state.table), interval)
        return interval

    def _table(self, provider: Any, key: str, prev: Optional[State], now: datetime, cfg: Config,
               game: Optional[Game]) -> list:
        """The league table; during busy periods it is fetched at most every ``poll_idle_seconds``."""
        fresh = (
            prev is not None and prev.table and self._table_key == key and self._table_fetched is not None
            and 0 <= (now - self._table_fetched).total_seconds() < cfg.poll_idle_seconds
        )
        if fresh:
            return prev.table
        try:
            table = provider.get_table()
        except (ProviderError, HttpError):
            if prev is None or not prev.table:
                raise
            return prev.table  # keep showing the old table; the games are what matters
        self._table_fetched, self._table_key = now, key
        return table

    # -- loop -------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def _wait(self, seconds: float) -> None:
        """Sleep, but wake up early on stop or when the configuration file changed."""
        end = self._now() + timedelta(seconds=seconds)
        while not self._stop.is_set():
            remaining = (end - self._now()).total_seconds()
            if remaining <= 0:
                return
            self._stop.wait(min(5.0, remaining))
            try:
                if os.path.getmtime(self.config_path) != self._config_mtime:
                    log.info("configuration changed, polling now")
                    return
            except OSError:
                pass

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.cycle()
            except ConfigError as exc:
                log.error("configuration problem: %s", exc)
                delay = 30.0
            except Exception:  # noqa: BLE001 - the service must keep running
                log.exception("unexpected error in poll cycle")
                delay = 60.0
            self._wait(delay)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_data")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = DataService(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())
    service.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
