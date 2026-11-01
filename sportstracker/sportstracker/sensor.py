"""Room climate data: the sensor service writes ``data/sensor.json``, the display and web services read it."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .timeutil import parse_utc

log = logging.getLogger(__name__)
SENSOR_VERSION = 1
MAX_AGE_SECONDS = 300   # older readings are not shown (the display shows dashes instead)


@dataclass
class SensorReading:
    updated_at: Optional[datetime] = None          # time of the last valid measurement (UTC)
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    error: Optional[str] = None                    # text of the last failed attempt, None if the last one worked
    failures: int = 0                              # failed attempts in a row

    def is_fresh(self, now: datetime, max_age: float = MAX_AGE_SECONDS) -> bool:
        if self.updated_at is None or self.temperature_c is None or self.humidity_pct is None:
            return False
        return 0 <= (now - self.updated_at).total_seconds() <= max_age


def sensor_path(data_dir: str) -> str:
    return os.path.join(data_dir, "sensor.json")


def reading_to_dict(reading: SensorReading) -> Dict[str, Any]:
    return {
        "version": SENSOR_VERSION,
        "updated_at": reading.updated_at.astimezone(timezone.utc).isoformat() if reading.updated_at else None,
        "temperature_c": reading.temperature_c,
        "humidity_pct": reading.humidity_pct,
        "error": reading.error,
        "failures": reading.failures,
    }


def reading_from_dict(data: Dict[str, Any]) -> SensorReading:
    if data.get("version") != SENSOR_VERSION:
        raise ValueError("unsupported sensor file version %r" % data.get("version"))
    temperature = data.get("temperature_c")
    humidity = data.get("humidity_pct")
    return SensorReading(
        updated_at=parse_utc(data["updated_at"]) if data.get("updated_at") else None,
        temperature_c=None if temperature is None else float(temperature),
        humidity_pct=None if humidity is None else float(humidity),
        error=data.get("error"),
        failures=int(data.get("failures", 0)),
    )


def write_sensor(path: str, reading: SensorReading) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".sensor-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(reading_to_dict(reading), out)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_sensor(path: str) -> Optional[SensorReading]:
    """The current reading, or None if there is none yet or the file is unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return reading_from_dict(json.load(handle))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("sensor file %s unusable: %s", path, exc)
        return None
