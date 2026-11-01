"""Sensor service: reads the DHT22 (AM2302) and writes ``data/sensor.json``.

    python3 -m sportstracker.service_sensor --config config/config.json

The display shows the values in the room view, the web backend on its dashboard. The service

* reads every ``sensor.interval_seconds`` (the DHT22 allows one measurement per 2 s at most),
* tries up to three times per round, because the DHT22 often fails with a checksum or timing error,
* drops implausible values (outside -40..80 degC / 0..100 %) and single outliers (a jump is only accepted
  when the next reading confirms it),
* applies the offsets of the configuration (the sensor sits warm behind the panel),
* keeps the last good value when a round fails and records the error and the number of failures in a row,
* does nothing while ``sensor.enabled`` is false and picks up changes of the configuration without a restart.

The reader samples the GPIO level register (``dht_mmap``; alternatively kernel edge events through libgpiod 2.x,
``dht_native``). No Adafruit library is needed, and nothing is imported before the first measurement.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from .config import Config, load_config
from .dht_mmap import open_dht22
from .dht_native import DhtError
from .errors import ConfigError
from .runtime import PROJECT_ROOT, resolve
from .sensor import SensorReading, sensor_path, write_sensor

log = logging.getLogger("sportstracker.sensor")

TEMP_RANGE = (-40.0, 80.0)
HUMIDITY_RANGE = (0.0, 100.0)
ATTEMPTS = 3
RETRY_WAIT = 2.5            # seconds between two attempts of one round
SPIKE_TEMP = 5.0            # degC: a larger jump between two readings needs a confirmation
SPIKE_HUMIDITY = 20.0       # percent
CONFIRM_TEMP = 2.0
CONFIRM_HUMIDITY = 10.0
DISABLED_WAIT = 30.0


class SensorError(Exception):
    """A measurement failed (this happens regularly with the DHT22)."""


class Dht22Reader:
    """Reads a DHT22 on the given BCM GPIO: GPIO registers (``dht_mmap``, default) or libgpiod edge events.

    The backend can be forced with the environment variable ``SPORTSTRACKER_DHT_BACKEND`` (mmap or gpiod).
    """

    def __init__(self, gpio: int) -> None:
        self.gpio = gpio
        self._device: Any = None

    def read(self) -> Tuple[float, float]:
        try:
            if self._device is None:
                self._device = open_dht22(self.gpio, os.environ.get("SPORTSTRACKER_DHT_BACKEND", "auto"))
            temperature, humidity = self._device.read()
        except DhtError as exc:              # no answer, incomplete frame, checksum: just try again
            raise SensorError(str(exc) or "Lesefehler")
        except Exception as exc:  # noqa: BLE001 - e.g. permission problems
            raise SensorError("Sensorfehler: %s" % exc)
        return float(temperature), float(humidity)

    def close(self) -> None:
        if self._device is not None:
            self._device.close()


def plausible(temperature: float, humidity: float) -> bool:
    return TEMP_RANGE[0] <= temperature <= TEMP_RANGE[1] and HUMIDITY_RANGE[0] <= humidity <= HUMIDITY_RANGE[1]


class SensorService:
    def __init__(
        self,
        config_path: str,
        root: str = PROJECT_ROOT,
        now_func: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        reader_factory: Callable[[int], Any] = Dht22Reader,
        retry_wait: float = RETRY_WAIT,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config_path = config_path
        self.root = root
        self._now = now_func
        self._reader_factory = reader_factory
        self._retry_wait = retry_wait
        self._stop = threading.Event()
        self._sleep = sleep or (lambda seconds: self._stop.wait(seconds))
        self._reader: Any = None
        self._reader_gpio: Optional[int] = None
        self.reading = SensorReading()
        self._pending: Optional[Tuple[float, float]] = None   # an unconfirmed outlier

    # -- one round ----------------------------------------------------------------

    def _reader_for(self, gpio: int) -> Any:
        if self._reader is not None and self._reader_gpio != gpio:
            self._close_reader()
        if self._reader is None:
            self._reader = self._reader_factory(gpio)
            self._reader_gpio = gpio
        return self._reader

    def _close_reader(self) -> None:
        reader, self._reader, self._reader_gpio = self._reader, None, None
        if reader is not None and hasattr(reader, "close"):
            reader.close()

    def _measure(self, gpio: int) -> Tuple[float, float]:
        """One valid raw measurement, or SensorError after the last attempt."""
        last: Optional[Exception] = None
        for attempt in range(ATTEMPTS):
            if attempt:
                self._sleep(self._retry_wait)
                if self._stop.is_set():
                    break
            try:
                temperature, humidity = self._reader_for(gpio).read()
            except SensorError as exc:
                last = exc
                continue
            if not plausible(temperature, humidity):
                last = SensorError("unplausibler Wert: %.1f degC / %.1f %%" % (temperature, humidity))
                continue
            return temperature, humidity
        raise SensorError(str(last) if last else "abgebrochen")

    def _accept(self, temperature: float, humidity: float) -> bool:
        """Spike filter: a big jump is only taken over when the following reading confirms it."""
        old = self.reading
        if old.temperature_c is None or old.humidity_pct is None:
            return True
        jump = abs(temperature - old.temperature_c) > SPIKE_TEMP or abs(humidity - old.humidity_pct) > SPIKE_HUMIDITY
        if not jump:
            self._pending = None
            return True
        pending = self._pending
        self._pending = (temperature, humidity)
        if pending is not None and abs(temperature - pending[0]) <= CONFIRM_TEMP and abs(humidity - pending[1]) <= CONFIRM_HUMIDITY:
            self._pending = None
            return True
        log.info("outlier %.1f degC / %.1f %% held back until confirmed", temperature, humidity)
        return False

    def cycle(self) -> float:
        """Run one round; returns the seconds to wait before the next one."""
        cfg = load_config(self.config_path)
        settings = cfg.sensor
        if not settings["enabled"]:
            self._close_reader()
            return DISABLED_WAIT
        interval = float(settings["interval_seconds"])
        now = self._now()
        old = self.reading
        try:
            temperature, humidity = self._measure(int(settings["gpio"]))
            temperature += settings["temp_offset"]
            humidity = min(100.0, max(0.0, humidity + settings["humidity_offset"]))
            if self._accept(temperature, humidity):
                self.reading = SensorReading(now, round(temperature, 1), round(humidity, 1), None, 0)
            else:
                self.reading = SensorReading(old.updated_at, old.temperature_c, old.humidity_pct, old.error, old.failures)
        except SensorError as exc:
            log.warning("measurement failed: %s", exc)
            self.reading = SensorReading(old.updated_at, old.temperature_c, old.humidity_pct, str(exc)[:200], old.failures + 1)
        write_sensor(sensor_path(resolve(cfg.data_dir, self.root)), self.reading)
        return interval

    # -- loop ---------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.cycle()
            except ConfigError as exc:
                log.error("configuration problem: %s", exc)
                delay = DISABLED_WAIT
            except Exception:  # noqa: BLE001 - the service must keep running
                log.exception("unexpected error in sensor round")
                delay = 60.0
            self._stop.wait(delay)
        self._close_reader()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.service_sensor")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = SensorService(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())
    service.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
