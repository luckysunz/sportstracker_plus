"""Shared test helpers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from sportstracker.errors import HttpError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(*parts: str) -> Any:
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(*parts: str) -> str:
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


class FakeHttp:
    """Stands in for HttpClient: maps URL -> JSON/text, or raises HttpError."""

    def __init__(self, routes: Dict[str, Any]) -> None:
        self.routes = dict(routes)
        self.calls = []

    def _lookup(self, url: str) -> Any:
        self.calls.append(url)
        if url not in self.routes:
            raise HttpError("HTTP 404 for %s" % url, status=404)
        value = self.routes[url]
        if isinstance(value, Exception):
            raise value
        return value

    def get_json(self, url: str, ttl: float = 0.0) -> Any:
        return self._lookup(url)

    def get_text(self, url: str, ttl: float = 0.0, encoding: str = "utf-8") -> str:
        return self._lookup(url)

    def get_bytes(self, url: str, ttl: float = 0.0) -> bytes:
        return self._lookup(url)
