"""Minimal HTTP client based on the standard library.

Designed for a Raspberry Pi Zero: no third-party packages, gzip support,
bounded response size, retries with backoff and an optional per-URL TTL cache.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from .errors import HttpError

DEFAULT_USER_AGENT = "sportstracker/0.1 (+personal LED matrix scoreboard)"


class HttpClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        retries: int = 2,
        backoff: float = 2.0,
        max_bytes: int = 2_000_000,
        urlopen: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.max_bytes = max_bytes
        self._urlopen = urlopen or urllib.request.urlopen
        self._sleep = sleep
        self._monotonic = monotonic
        self._cache: Dict[str, Tuple[float, bytes]] = {}

    def get_bytes(self, url: str, ttl: float = 0.0) -> bytes:
        """GET ``url``. Results are cached for ``ttl`` seconds (0 = no cache)."""
        now = self._monotonic()
        cached = self._cache.get(url)
        if ttl > 0 and cached is not None and cached[0] > now:
            return cached[1]

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip",
                "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
            },
        )
        last_error: Optional[Exception] = None
        status: Optional[int] = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff * attempt)
            try:
                with self._urlopen(request, timeout=self.timeout) as response:
                    body = response.read(self.max_bytes + 1)
                    encoding = (response.headers.get("Content-Encoding") or "").lower()
                if len(body) > self.max_bytes:
                    raise HttpError("response larger than %d bytes: %s" % (self.max_bytes, url))
                if encoding == "gzip":
                    body = gzip.decompress(body)
                if ttl > 0:
                    self._cache[url] = (now + ttl, body)
                return body
            except urllib.error.HTTPError as exc:
                status = exc.code
                last_error = exc
                # 4xx (except 429) will not get better by retrying.
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise HttpError("HTTP %d for %s" % (exc.code, url), status=exc.code)
            except HttpError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
        raise HttpError("request failed after %d attempts: %s (%s)" % (self.retries + 1, url, last_error), status=status)

    def get_text(self, url: str, ttl: float = 0.0, encoding: str = "utf-8") -> str:
        return self.get_bytes(url, ttl=ttl).decode(encoding, errors="replace")

    def get_json(self, url: str, ttl: float = 0.0) -> Any:
        raw = self.get_bytes(url, ttl=ttl)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise HttpError("invalid JSON from %s: %s" % (url, exc))
