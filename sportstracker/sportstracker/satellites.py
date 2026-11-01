"""Satellite sensors (0.6.0): small ESP32 boards with DHT22 and MQ-135 elsewhere in the house.

They deliver their values either by HTTP (``POST /api/satellite/<key>`` with a token, handled by the web backend)
or by MQTT (``sportstracker-mqtt`` service subscribes to ``<base_topic>/<key>/state``). Both ways end in
``data/satellites.json``, which the display service and the web backend read.

Payload (JSON, all values optional but at least one needed)::

    {"temperature": 22.4, "humidity": 45.1, "gas": 812, "air_index": 78}

``gas`` is the raw MQ-135 value (ADC counts, higher = worse air). ``air_index`` is an air quality from 0 (bad) to
100 (clean) if the satellite computes one itself; otherwise the index is derived from ``gas`` with the limits
``gas_clean`` / ``gas_bad`` of the satellite's configuration.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

from .timeutil import parse_utc

try:                                   # Linux (the Pi); without it updates are only safe within one process
    import fcntl
except ImportError:                    # pragma: no cover
    fcntl = None  # type: ignore

log = logging.getLogger(__name__)
SAT_VERSION = 1
MAX_AGE_SECONDS = 600                  # older values are not shown (dashes instead)

TEMPERATURE_KEYS = ("temperature", "temperature_c", "temp")
HUMIDITY_KEYS = ("humidity", "humidity_pct", "hum")
GAS_KEYS = ("gas", "gas_raw", "mq135")
INDEX_KEYS = ("air_index", "air_quality", "aqi")


class PayloadError(ValueError):
    """The satellite sent something unusable (message in German for the web response and the log)."""


@dataclass
class SatReading:
    updated_at: Optional[datetime] = None
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    gas_raw: Optional[float] = None
    air_index: Optional[float] = None

    def is_fresh(self, now: datetime, max_age: float = MAX_AGE_SECONDS) -> bool:
        if self.updated_at is None:
            return False
        return 0 <= (now - self.updated_at).total_seconds() <= max_age


def satellites_path(data_dir: str) -> str:
    return os.path.join(data_dir, "satellites.json")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _pick(data: Mapping[str, Any], keys: Tuple[str, ...], low: float, high: float, label: str) -> Optional[float]:
    for key in keys:
        if key in data and data[key] is not None:
            number = _number(data[key])
            if number is None or not low <= number <= high:
                raise PayloadError("%s außerhalb des gültigen Bereichs (%g bis %g)." % (label, low, high))
            return number
    return None


def parse_payload(data: Any, now: datetime) -> SatReading:
    """Validate a payload from a satellite. Raises PayloadError."""
    if not isinstance(data, dict):
        raise PayloadError("Erwartet wird ein JSON-Objekt.")
    reading = SatReading(
        updated_at=now,
        temperature_c=_pick(data, TEMPERATURE_KEYS, -40.0, 85.0, "Temperatur"),
        humidity_pct=_pick(data, HUMIDITY_KEYS, 0.0, 100.0, "Luftfeuchte"),
        gas_raw=_pick(data, GAS_KEYS, 0.0, 65535.0, "Gaswert"),
        air_index=_pick(data, INDEX_KEYS, 0.0, 100.0, "Luftqualität"),
    )
    if reading.temperature_c is None and reading.humidity_pct is None and reading.gas_raw is None \
            and reading.air_index is None:
        raise PayloadError("Keine bekannten Werte gefunden (temperature, humidity, gas, air_index).")
    return reading


def air_quality(reading: SatReading, item: Mapping[str, Any]) -> Optional[Tuple[int, str]]:
    """(index 0..100, label) or None if the satellite has no air sensor values."""
    index = reading.air_index
    if index is None and reading.gas_raw is not None:
        clean, bad = float(item.get("gas_clean", 800)), float(item.get("gas_bad", 2500))
        index = 100.0 * (bad - reading.gas_raw) / (bad - clean) if bad != clean else 0.0
    if index is None:
        return None
    index = int(round(max(0.0, min(100.0, index))))
    return index, ("GUT" if index >= 70 else "MITTEL" if index >= 40 else "SCHLECHT")


# -- file ---------------------------------------------------------------------------------------------------------

def _to_dict(reading: SatReading) -> Dict[str, Any]:
    return {
        "updated_at": reading.updated_at.astimezone(timezone.utc).isoformat() if reading.updated_at else None,
        "temperature_c": reading.temperature_c, "humidity_pct": reading.humidity_pct,
        "gas_raw": reading.gas_raw, "air_index": reading.air_index,
    }


def _from_dict(data: Dict[str, Any]) -> SatReading:
    def num(key: str) -> Optional[float]:
        return None if data.get(key) is None else float(data[key])

    return SatReading(
        updated_at=parse_utc(data["updated_at"]) if data.get("updated_at") else None,
        temperature_c=num("temperature_c"), humidity_pct=num("humidity_pct"), gas_raw=num("gas_raw"),
        air_index=num("air_index"),
    )


def read_satellites(path: str) -> Dict[str, SatReading]:
    """All stored readings by satellite key (empty if there is no usable file)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if raw.get("version") != SAT_VERSION:
            raise ValueError("version")
        return {str(key): _from_dict(value) for key, value in raw["items"].items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("satellite file %s unusable: %s", path, exc)
        return {}


@contextmanager
def _locked(path: str) -> Iterator[None]:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if fcntl is None:                  # pragma: no cover
        yield
        return
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def store_reading(path: str, key: str, reading: SatReading, keep: Optional[Any] = None) -> None:
    """Write one satellite's reading into the file (read-modify-write under a file lock; other keys stay).

    Values the satellite did not send are taken over from its previous reading so a satellite that sends the
    temperature and the gas value in separate messages still shows both.
    """
    with _locked(path):
        items = read_satellites(path)
        before = items.get(key)
        if before is not None:
            for name in ("temperature_c", "humidity_pct", "gas_raw", "air_index"):
                if getattr(reading, name) is None:
                    setattr(reading, name, getattr(before, name))
        items[key] = reading
        if keep is not None:
            items = {k: v for k, v in items.items() if k in keep}
        directory = os.path.dirname(os.path.abspath(path))
        handle, tmp = tempfile.mkstemp(prefix=".sat-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"version": SAT_VERSION, "items": {k: _to_dict(v) for k, v in items.items()}}, out)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
