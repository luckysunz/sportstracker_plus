"""Exception types shared by all modules."""

from __future__ import annotations

from typing import Optional


class HttpError(Exception):
    """Network or HTTP failure. ``status`` is set for HTTP error responses."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class ProviderError(Exception):
    """A data provider could not deliver usable data (network, format, ...).

    Callers should keep showing the last good state when this is raised.
    """


class ConfigError(Exception):
    """The configuration file is missing, unreadable or invalid."""
