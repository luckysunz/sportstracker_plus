"""Display service: the only process that talks to the LED matrix (needs root).

    sudo python -m sportstracker.service_display --config config/config.json

Reads the state file written by the data service (and the sensor file of the
sensor service), builds the display list with ``views.plan`` and shows the item
that is due according to the wall clock. It only redraws when the picture
changes, so Python sleeps almost all the time and the C++ refresh thread does
the work. It also notices local midnight by itself (final score -> table).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .banner import FRAME_INTERVAL, is_animated
from .config import Config, load_config
from .errors import ConfigError
from .render import Renderer
from .runtime import PROJECT_ROOT, build_logo_cache, build_renderer, resolve, state_path
from .satellites import read_satellites, satellites_path
from .sensor import read_sensor, sensor_path
from .state import read_state
from .views import plan as build_plan, render_view, view_for_item

log = logging.getLogger("sportstracker.display")


class RestartRequired(Exception):
    """The matrix settings changed; they only apply at start-up, so exit and let systemd restart us."""


class DisplayService:
    def __init__(
        self,
        config_path: str,
        display: Any = None,
        display_factory: Optional[Callable[[dict], Any]] = None,
        root: str = PROJECT_ROOT,
        now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.config_path = config_path
        self.root = root
        self._now = now_func
        self._display = display
        self._display_factory = display_factory
        self._stop = threading.Event()
        self.cfg: Optional[Config] = None
        self.tz: Optional[ZoneInfo] = None
        self.renderer: Optional[Renderer] = None
        self._config_mtime = -1.0
        self._state_mtime = -1.0
        self._state: Any = None
        self._sensor_mtime = -1.0
        self._reading: Any = None
        self._sat_mtime = -1.0
        self._sats: Any = {}
        self.current_plan: Any = None
        self._last_context: Any = None
        self._last_frame = ""
        self.frames_shown = 0
        self.wait_seconds = 1.0            # pause before the next step: short while an animation runs
        self._animating = False

    # -- configuration ----------------------------------------------------------

    def _mtime(self, path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _reload_config(self) -> None:
        mtime = self._mtime(self.config_path)
        if mtime == self._config_mtime and self.cfg is not None:
            return
        new = load_config(self.config_path)
        if self.cfg is not None and new.matrix != self.cfg.matrix:
            raise RestartRequired()
        self.cfg = new
        self.tz = ZoneInfo(new.timezone)
        logos = build_logo_cache(new, http=None, read_only=True, root=self.root)
        self.renderer = build_renderer(new, logos, self.tz, root=self.root)
        self._config_mtime = mtime
        self._last_context = None  # force a redraw
        log.info("configuration loaded")

    def _ensure_display(self) -> None:
        if self._display is None:
            factory = self._display_factory
            if factory is None:
                from .display import MatrixDisplay as factory  # imported late: needs the rgbmatrix bindings
            assert self.cfg is not None
            self._display = factory(self.cfg.matrix)

    # -- one step ---------------------------------------------------------------

    def step(self) -> bool:
        """Re-read config/state if they changed and redraw if the picture changed. True if a frame was shown."""
        self._reload_config()
        assert self.cfg is not None and self.renderer is not None and self.tz is not None
        self._ensure_display()
        path = state_path(self.cfg, self.root)
        state_mtime = self._mtime(path)
        if state_mtime != self._state_mtime:
            self._state = read_state(path)
            self._state_mtime = state_mtime
        spath = sensor_path(resolve(self.cfg.data_dir, self.root))
        sensor_mtime = self._mtime(spath)
        if sensor_mtime != self._sensor_mtime:
            self._reading = read_sensor(spath)
            self._sensor_mtime = sensor_mtime
        sat_file = satellites_path(resolve(self.cfg.data_dir, self.root))
        sat_mtime = self._mtime(sat_file)
        if sat_mtime != self._sat_mtime:
            self._sats = read_satellites(sat_file)
            self._sat_mtime = sat_mtime
        now = self._now()
        stale_after = 2 * self.cfg.poll_idle_seconds + 60
        self.current_plan = build_plan(self._state, now, self.cfg, self.tz, stale_after,
                                       picture=self.renderer.picture.available(),
                                       picture_seconds=self.renderer.picture.total_seconds(self.cfg.picture))
        item, elapsed = self.current_plan.locate(now)
        view = view_for_item(item, self._state, now, self.current_plan.stale, self._reading, self.cfg, elapsed,
                             self._sats)
        self.wait_seconds = 1.0
        if item.kind == "banner" and is_animated(self.cfg.message):
            # an animation needs a new picture every frame: no change detection, short pause
            self.wait_seconds = FRAME_INTERVAL
            self._display.show(render_view(self.renderer, view, self.cfg.favorite_team_id))
            self.frames_shown += 1
            self._last_context = None
            self._last_frame = ""
            if not self._animating:
                self._animating = True
                log.info("showing animated message (%s)", self.cfg.message.get("animation"))
            return True
        self._animating = False
        identity = (item.kind, item.game.game_id if item.game is not None else None, item.role)
        minute = int(now.timestamp() // 60) if item.kind in ("room", "satellite") else None
        if item.kind == "picture":
            # file change and, for a GIF, the frame that is due; the pause follows the frame rate
            pictures = self.renderer.picture
            minute = pictures.state_key(self.cfg.picture, elapsed)
            if pictures.needs_fast_redraw(self.cfg.picture, elapsed):
                self.wait_seconds = FRAME_INTERVAL
        context = (self._config_mtime, self._state_mtime, self._sensor_mtime if item.kind == "room" else None,
                   self._sat_mtime if item.kind == "satellite" else None,
                   now.astimezone(self.tz).date(), (identity, item.satellite), view.stale, minute)
        if context == self._last_context:
            return False
        self._last_context = context
        image = render_view(self.renderer, view, self.cfg.favorite_team_id)
        digest = hashlib.sha1(image.tobytes()).hexdigest()
        if digest == self._last_frame:
            return False
        self._last_frame = digest
        self._display.show(image)
        self.frames_shown += 1
        log.info("showing %s view", view.kind)
        return True

    # -- loop -------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, interval: float = 1.0) -> int:
        while not self._stop.is_set():
            try:
                self.step()
            except RestartRequired:
                log.info("matrix settings changed, exiting for restart")
                return 0
            except ConfigError as exc:
                log.error("configuration problem: %s", exc)
            except Exception:  # noqa: BLE001 - keep showing the last picture
                log.exception("unexpected error")
            self._stop.wait(min(interval, self.wait_seconds))
        if self._display is not None and hasattr(self._display, "clear"):
            self._display.clear()
        return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_display")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = DisplayService(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())
    return service.run_forever()


if __name__ == "__main__":
    sys.exit(main())
