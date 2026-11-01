"""Temperature protection: reading the CPU temperature and the record of the last safety shutdown.

The safety service (``service_safety``) compares the CPU temperature with ``safety.shutdown_temp_c`` and powers the
Raspberry Pi off before the hardware limit (85 degC) is reached. Right before it does so it writes
``data/safety.json``; the web backend shows that record so the reason for a sudden shutdown is visible afterwards.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .timeutil import parse_utc

log = logging.getLogger(__name__)
SAFETY_VERSION = 1
THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
RECENT_HOURS = 72          # the dashboard warns about a safety shutdown for this long


def read_cpu_temp(path: str = THERMAL_PATH) -> Optional[float]:
    """CPU temperature in degC, or None if it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read().strip()
        return int(text) / 1000.0
    except (OSError, ValueError):
        return None


@dataclass
class SafetyRecord:
    shutdown_at: Optional[datetime] = None       # UTC time of the last safety shutdown
    temperature_c: Optional[float] = None
    limit_c: Optional[int] = None

    def is_recent(self, now: datetime, hours: float = RECENT_HOURS) -> bool:
        if self.shutdown_at is None:
            return False
        return 0 <= (now - self.shutdown_at).total_seconds() <= hours * 3600


def safety_path(data_dir: str) -> str:
    return os.path.join(data_dir, "safety.json")


def write_safety(path: str, record: SafetyRecord) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    data = {
        "version": SAFETY_VERSION,
        "shutdown_at": record.shutdown_at.astimezone(timezone.utc).isoformat() if record.shutdown_at else None,
        "temperature_c": record.temperature_c,
        "limit_c": record.limit_c,
    }
    handle, tmp = tempfile.mkstemp(prefix=".safety-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())            # the Pi is about to lose power: make sure the record is on the card
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_safety(path: str) -> Optional[SafetyRecord]:
    """The record of the last safety shutdown, or None if there is none or the file is unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("version") != SAFETY_VERSION:
            raise ValueError("unsupported version %r" % data.get("version"))
        temp = data.get("temperature_c")
        limit = data.get("limit_c")
        return SafetyRecord(
            shutdown_at=parse_utc(data["shutdown_at"]) if data.get("shutdown_at") else None,
            temperature_c=None if temp is None else float(temp),
            limit_c=None if limit is None else int(limit),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("safety file %s unusable: %s", path, exc)
        return None
