from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from sportstracker import cli

from helpers import FakeHttp, load_fixture

BASE = "https://api.openligadb.de"
CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "config.example.json")


def fake_http():
    return FakeHttp(
        {
            BASE + "/getavailableteams/del/2026": load_fixture("openligadb", "teams.json"),
            BASE + "/getbltable/del/2026": load_fixture("openligadb", "table.json"),
            BASE + "/getlastmatchbyleagueteam/4947/641": load_fixture("openligadb", "match_last_finished.json"),
            BASE + "/getnextmatchbyleagueteam/4947/641": load_fixture("openligadb", "match_next_scheduled.json"),
        }
    )


def run(argv):
    out = io.StringIO()
    with mock.patch.object(cli, "HttpClient", fake_http), contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_check_no_game(self):
        code, text = run(["check", "--config", CONFIG, "--now", "2026-10-27T12:00:00+01:00"])
        self.assertEqual(code, 0)
        self.assertIn("Teams: 14", text)
        self.assertIn("Eisbären Berlin", text)
        self.assertIn("keins -> Tabelle anzeigen", text)

    def test_check_game_today(self):
        code, text = run(["check", "--config", CONFIG, "--now", "2026-10-30T12:00:00+01:00"])
        self.assertEqual(code, 0)
        self.assertIn("ERC Ingolstadt vs Eisbären Berlin", text)
        self.assertIn("30.10.2026 19:30", text)
        self.assertIn("scheduled", text)

    def test_check_final_score(self):
        code, text = run(["check", "--config", CONFIG, "--now", "2026-10-25T22:00:00+01:00"])
        self.assertEqual(code, 0)
        self.assertIn("1:4", text)
        self.assertIn("Endstand bleibt bis Mitternacht", text)

    def test_teams(self):
        code, text = run(["teams", "--config", CONFIG])
        self.assertEqual(code, 0)
        self.assertEqual(len(text.strip().splitlines()), 14)

    def test_bad_config(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["teams", "--config", "/nonexistent.json"])
        self.assertEqual(code, 2)
        self.assertIn("FEHLER", err.getvalue())


if __name__ == "__main__":
    unittest.main()
