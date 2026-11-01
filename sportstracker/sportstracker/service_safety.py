"""Safety service: powers the Raspberry Pi off when the CPU gets too hot.

    python3 -m sportstracker.service_safety --config config/config.json

The service checks the CPU temperature every few seconds. When it stays at or above ``safety.shutdown_temp_c``
(default 84 degC) for several readings in a row, it writes ``data/safety.json`` and runs ``systemctl poweroff``.
Shutting down cleanly before the 85 degC hardware limit protects the SD card and the board. A Pi Zero 2 W stays off
afterwards until the power supply is disconnected and connected again.

* runs as root (needed for ``poweroff``); it only reads the configuration and writes nothing but the record,
* a single spike does not trigger it: ``CONFIRM_READINGS`` readings in a row must be over the limit,
* a missing or broken configuration falls back to the defaults (enabled, 84 degC) - protection never switches off
  because of a typo; only an explicit ``"enabled": false`` does,
* an unreadable temperature is only logged.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .config import DEFAULT_SAFETY, load_config
from .errors import ConfigError
from .runtime import PROJECT_ROOT, resolve
from .safety import SafetyRecord, read_cpu_temp, safety_path, write_safety

log = logging.getLogger("sportstracker.safety")

CHECK_SECONDS = 5.0
CONFIRM_READINGS = 3          # consecutive readings at or above the limit (about 10 s)
RETRY_SECONDS = 10.0          # wait after a failed poweroff
WARN_MARGIN = 4               # log a warning from limit - WARN_MARGIN degC upwards (at most once per 10 minutes)
WARN_EVERY = 600.0


def poweroff() -> bool:
    """Power the system off; True if the command was accepted."""
    try:
        done = subprocess.run(["/usr/bin/systemctl", "poweroff"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("poweroff failed: %s", exc)
        return False
    if done.returncode != 0:
        log.error("poweroff failed (code %d): %s", done.returncode, done.stdout.strip())
        return False
    return True


class SafetyService:
    def __init__(
        self,
        config_path: str,
        root: str = PROJECT_ROOT,
        now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        temp_reader: Callable[[], Optional[float]] = read_cpu_temp,
        power_off: Callable[[], bool] = poweroff,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config_path = config_path
        self.root = root
        self._now = now_func
        self._read_temp = temp_reader
        self._power_off = power_off
        self._clock = clock
        self._stop = threading.Event()
        self.hot_readings = 0
        self.shutdown_started = False
        self._last_warning = -WARN_EVERY

    def settings(self):
        """(enabled, limit, data_dir); the defaults if the configuration cannot be read."""
        try:
            cfg = load_config(self.config_path)
            return bool(cfg.safety["enabled"]), int(cfg.safety["shutdown_temp_c"]), resolve(cfg.data_dir, self.root)
        except (ConfigError, OSError) as exc:
            log.error("configuration unusable, using the defaults: %s", exc)
            return DEFAULT_SAFETY["enabled"], int(DEFAULT_SAFETY["shutdown_temp_c"]), resolve("data", self.root)

    def cycle(self) -> float:
        """One check; returns the seconds to wait before the next one."""
        enabled, limit, data_dir = self.settings()
        if not enabled:
            self.hot_readings = 0
            return CHECK_SECONDS
        temp = self._read_temp()
        if temp is None:
            log.warning("CPU temperature not readable")
            return CHECK_SECONDS
        if temp < limit:
            self.hot_readings = 0
            if temp >= limit - WARN_MARGIN and self._clock() - self._last_warning >= WARN_EVERY:
                self._last_warning = self._clock()
                log.warning("CPU temperature %.1f degC, shutdown at %d degC", temp, limit)
            return CHECK_SECONDS
        self.hot_readings += 1
        log.warning("CPU temperature %.1f degC >= %d degC (%d of %d)", temp, limit, self.hot_readings, CONFIRM_READINGS)
        if self.hot_readings < CONFIRM_READINGS:
            return CHECK_SECONDS
        return self._shutdown(temp, limit, data_dir)

    def _shutdown(self, temp: float, limit: int, data_dir: str) -> float:
        log.critical("CPU temperature %.1f degC reached the limit of %d degC - powering off", temp, limit)
        try:
            write_safety(safety_path(data_dir), SafetyRecord(self._now(), round(temp, 1), limit))
        except OSError as exc:                      # the shutdown matters more than the record
            log.error("could not write the safety record: %s", exc)
        if self._power_off():
            self.shutdown_started = True
            return 60.0                              # the system is going down; do not run the command again
        return RETRY_SECONDS

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.cycle()
            except Exception:  # noqa: BLE001 - the protection must keep running
                log.exception("unexpected error in safety check")
                delay = CHECK_SECONDS
            self._stop.wait(delay)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_safety")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = SafetyService(args.config)
    enabled, limit, _ = service.settings()
    log.info("safety service started: %s, limit %d degC", "active" if enabled else "switched off", limit)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())
    service.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
