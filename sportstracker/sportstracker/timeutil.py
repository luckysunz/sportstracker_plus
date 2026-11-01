"""Small time helpers (no third-party dependencies)."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone, tzinfo

_FRACTION = re.compile(r"\.(\d+)")


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into an aware UTC datetime.

    Accepts a trailing ``Z``, ``+hh:mm`` offsets and fractional seconds of any
    length. Values without an offset are assumed to be UTC.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_date(moment: datetime, tz: tzinfo) -> date:
    """Calendar date of ``moment`` in the given timezone."""
    return moment.astimezone(tz).date()
