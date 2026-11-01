from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sportstracker.config import DEFAULT_MATRIX, load_config, parse_config
from sportstracker.errors import ConfigError, HttpError
from sportstracker.http import HttpClient
from sportstracker.models import Game, GameStatus, Team
from sportstracker.selection import select_game_of_day
from sportstracker.timeutil import parse_utc

BERLIN = ZoneInfo("Europe/Berlin")
A, B = Team("1", "A"), Team("2", "B")


def game(game_id, start_utc, status):
    return Game(game_id, A, B, parse_utc(start_utc), status)


class TimeTests(unittest.TestCase):
    def test_parse_z_suffix(self):
        self.assertEqual(parse_utc("2026-10-25T13:00:00Z"), datetime(2026, 10, 25, 13, tzinfo=timezone.utc))

    def test_parse_short_fraction_and_offset(self):
        value = parse_utc("2026-10-25T16:15:26.76+02:00")
        self.assertEqual((value.hour, value.minute, value.second), (14, 15, 26))
        self.assertEqual(value.microsecond, 760000)

    def test_parse_naive_is_utc(self):
        self.assertEqual(parse_utc("2026-10-25T13:00:00").hour, 13)


class SelectionTests(unittest.TestCase):
    TODAY = date(2026, 11, 1)

    def test_none_when_other_day(self):
        self.assertIsNone(select_game_of_day([game("1", "2026-11-02T18:30:00Z", GameStatus.SCHEDULED)], self.TODAY, BERLIN))

    def test_late_game_counts_for_local_date(self):
        # 23:30Z on 31.10. is 00:30 on 01.11. in Berlin (CET)
        g = game("1", "2026-10-31T23:30:00Z", GameStatus.SCHEDULED)
        self.assertIs(select_game_of_day([g], self.TODAY, BERLIN), g)

    def test_live_beats_others(self):
        scheduled = game("1", "2026-11-01T20:00:00Z", GameStatus.SCHEDULED)
        live = game("2", "2026-11-01T14:00:00Z", GameStatus.LIVE)
        finished = game("3", "2026-11-01T09:00:00Z", GameStatus.FINISHED)
        self.assertIs(select_game_of_day([finished, scheduled, live], self.TODAY, BERLIN), live)

    def test_upcoming_beats_finished(self):
        scheduled = game("1", "2026-11-01T20:00:00Z", GameStatus.SCHEDULED)
        finished = game("3", "2026-11-01T09:00:00Z", GameStatus.FINISHED)
        self.assertIs(select_game_of_day([finished, scheduled], self.TODAY, BERLIN), scheduled)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_merge(self):
        cfg = parse_config({"matrix": {"rows": 32}, "favorite_team_id": 641, "future_key": 1})
        self.assertEqual(cfg.matrix["rows"], 32)
        self.assertEqual(cfg.matrix["cols"], DEFAULT_MATRIX["cols"])
        self.assertEqual(cfg.favorite_team_id, "641")
        self.assertEqual(cfg.extra, {"future_key": 1})

    def test_invalid_values(self):
        with self.assertRaises(ConfigError):
            parse_config({"show_team_names": "yes"})
        with self.assertRaises(ConfigError):
            parse_config({"timezone": ""})
        with self.assertRaises(ConfigError):
            parse_config([])

    def test_load_example_file(self):
        path = os.path.join(os.path.dirname(__file__), "..", "config", "config.example.json")
        cfg = load_config(path)
        self.assertEqual(cfg.provider, "openligadb")
        self.assertEqual(cfg.provider_params["league_id"], 4947)

    def test_load_errors(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/config.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
        try:
            with self.assertRaises(ConfigError):
                load_config(handle.name)
        finally:
            os.unlink(handle.name)


class FakeResponse:
    def __init__(self, body, encoding=None):
        self._body = body
        self.headers = {"Content-Encoding": encoding} if encoding else {}

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpClientTests(unittest.TestCase):
    def client(self, outcomes, **kwargs):
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        sleeps = []
        clock = kwargs.pop("clock", None) or [0.0]
        client = HttpClient(urlopen=urlopen, sleep=sleeps.append, monotonic=lambda: clock[0], **kwargs)
        return client, calls, sleeps, clock

    def test_get_json(self):
        client, _, _, _ = self.client([FakeResponse(b'{"a": 1}')])
        self.assertEqual(client.get_json("http://x/a"), {"a": 1})

    def test_gzip(self):
        client, _, _, _ = self.client([FakeResponse(gzip.compress(b'{"a": 2}'), "gzip")])
        self.assertEqual(client.get_json("http://x/a"), {"a": 2})

    def test_retry_then_success(self):
        err = urllib.error.URLError("temporary")
        client, calls, sleeps, _ = self.client([err, err, FakeResponse(b"[]")])
        self.assertEqual(client.get_json("http://x/a"), [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_no_retry_on_404(self):
        err = urllib.error.HTTPError("http://x/a", 404, "nf", {}, io.BytesIO(b""))
        client, calls, _, _ = self.client([err])
        with self.assertRaises(HttpError) as ctx:
            client.get_json("http://x/a")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_retries(self):
        err = urllib.error.HTTPError("http://x/a", 503, "bad", {}, io.BytesIO(b""))
        client, calls, _, _ = self.client([err], retries=1)
        with self.assertRaises(HttpError) as ctx:
            client.get_json("http://x/a")
        self.assertEqual(len(calls), 2)
        self.assertEqual(ctx.exception.status, 503)

    def test_size_limit(self):
        client, _, _, _ = self.client([FakeResponse(b"x" * 50)], max_bytes=10)
        with self.assertRaises(HttpError):
            client.get_bytes("http://x/a")

    def test_invalid_json(self):
        client, _, _, _ = self.client([FakeResponse(b"<html>")])
        with self.assertRaises(HttpError):
            client.get_json("http://x/a")

    def test_ttl_cache(self):
        clock = [0.0]
        client, calls, _, _ = self.client([FakeResponse(b"[1]"), FakeResponse(b"[2]")], clock=clock)
        self.assertEqual(client.get_json("http://x/a", ttl=30), [1])
        clock[0] = 10.0
        self.assertEqual(client.get_json("http://x/a", ttl=30), [1])
        self.assertEqual(len(calls), 1)
        clock[0] = 31.0
        self.assertEqual(client.get_json("http://x/a", ttl=30), [2])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


class ConfigRenderingKeysTest(unittest.TestCase):
    def test_defaults_and_values(self):
        from sportstracker.config import parse_config
        cfg = parse_config({})
        self.assertEqual((cfg.logo_backing, cfg.table_logo_size, cfg.logo_backing_teams), ("auto", 6, {}))
        cfg = parse_config({"logo_backing": "off", "logo_backing_teams": {"36911": "always"}, "table_logo_size": 8,
                            "team_labels": {"36365": "RBZ"}})
        self.assertEqual((cfg.logo_backing, cfg.table_logo_size), ("off", 8))
        self.assertEqual(cfg.logo_backing_teams, {"36911": "always"})

    def test_invalid_values(self):
        from sportstracker.config import parse_config
        from sportstracker.errors import ConfigError
        self.assertEqual(parse_config({}).table_label, "city")
        self.assertEqual(parse_config({"team_cities": {"1": "Bremerhv"}}).team_cities, {"1": "Bremerhv"})
        for bad in ({"table_label": "x"}, {"team_cities": []}, {"logo_backing": "maybe"}, {"table_logo_size": 99}, {"table_logo_size": "8"},
                    {"logo_backing_teams": {"1": "x"}}):
            with self.assertRaises(ConfigError):
                parse_config(bad)
