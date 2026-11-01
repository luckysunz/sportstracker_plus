from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sportstracker.errors import HttpError, ProviderError
from sportstracker.models import GameStatus
from sportstracker.providers.openligadb import OpenLigaDbProvider

from helpers import FakeHttp, load_fixture

BERLIN = ZoneInfo("Europe/Berlin")
BASE = "https://api.openligadb.de"
EBB = "641"  # Eisbaeren Berlin
LAST_URL = BASE + "/getlastmatchbyleagueteam/4947/641"
NEXT_URL = BASE + "/getnextmatchbyleagueteam/4947/641"


def berlin(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def make_provider(routes):
    http = FakeHttp(routes)
    provider = OpenLigaDbProvider(http, "del", 2026, 4947, BERLIN)
    return provider, http


def synthetic_match(match_id, start_utc, results, finished, home=641, away=338):
    """Match in the OpenLigaDB shape; variants for live/overtime cases."""
    base = copy.deepcopy(load_fixture("openligadb", "match_next_scheduled.json"))
    teams = {t["teamId"]: t for t in load_fixture("openligadb", "teams.json")}
    base["matchID"] = match_id
    base["matchDateTimeUTC"] = start_utc
    base["matchDateTime"] = start_utc.replace("Z", "")
    base["team1"] = teams[home]
    base["team2"] = teams[away]
    base["matchIsFinished"] = finished
    base["matchResults"] = [
        {
            "resultID": 0,
            "resultName": name,
            "pointsTeam1": h,
            "pointsTeam2": a,
            "resultOrderID": order,
            "resultTypeID": type_id,
            "resultTypeKind": kind,
            "resultDescription": "",
        }
        for order, (name, h, a, type_id, kind) in enumerate(results, start=1)
    ]
    return base


class TeamsAndTableTests(unittest.TestCase):
    def test_list_teams(self):
        provider, _ = make_provider(
            {BASE + "/getavailableteams/del/2026": load_fixture("openligadb", "teams.json")}
        )
        teams = provider.list_teams()
        self.assertEqual(len(teams), 14)
        self.assertEqual(teams[0].team_id, "338")
        self.assertEqual(teams[0].name, "Adler Mannheim")
        self.assertEqual(teams[0].short_name, "MAN")
        self.assertTrue(teams[0].logo_url.endswith("team_2.svg"))

    def test_table(self):
        provider, _ = make_provider(
            {BASE + "/getbltable/del/2026": load_fixture("openligadb", "table.json")}
        )
        rows = provider.get_table()
        self.assertEqual([r.rank for r in rows], [1, 2, 3, 4, 5])
        first = rows[0]
        self.assertEqual(first.team.name, "Eisbären Berlin")
        self.assertEqual((first.played, first.won, first.draw, first.lost), (12, 9, 1, 2))
        self.assertEqual((first.goals_for, first.goals_against, first.goal_diff), (43, 18, 25))
        self.assertEqual(first.points, 28)

    def test_table_unexpected_format(self):
        provider, _ = make_provider({BASE + "/getbltable/del/2026": {"error": "x"}})
        with self.assertRaises(ProviderError):
            provider.get_table()

    def test_from_params_requires_keys(self):
        with self.assertRaises(ProviderError):
            OpenLigaDbProvider.from_params({"league_shortcut": "del"}, FakeHttp({}), BERLIN)


class GameOfDayTests(unittest.TestCase):
    def setUp(self):
        self.last = load_fixture("openligadb", "match_last_finished.json")  # 25.10. 14:00 Berlin
        self.next = load_fixture("openligadb", "match_next_scheduled.json")  # 30.10. 19:30 Berlin
        self.provider, self.http = make_provider({LAST_URL: self.last, NEXT_URL: self.next})

    def test_no_game_today(self):
        self.assertIsNone(self.provider.get_game_of_day(EBB, berlin(2026, 10, 27)))

    def test_upcoming_game_today(self):
        game = self.provider.get_game_of_day(EBB, berlin(2026, 10, 30, 10, 0))
        self.assertIsNotNone(game)
        self.assertEqual(game.game_id, "84406")
        self.assertEqual(game.status, GameStatus.SCHEDULED)
        self.assertEqual(game.home.name, "ERC Ingolstadt")
        self.assertEqual(game.away.name, "Eisbären Berlin")
        self.assertEqual(game.start.astimezone(BERLIN).hour, 19)
        self.assertEqual(game.start.astimezone(BERLIN).minute, 30)
        self.assertIsNone(game.home_score)
        self.assertEqual(game.periods, ())

    def test_finished_game_stays_until_midnight(self):
        # 25.10.2026 is the day the clocks go back; the game ends at 16:15 CET.
        game = self.provider.get_game_of_day(EBB, berlin(2026, 10, 25, 23, 59))
        self.assertIsNotNone(game)
        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertEqual((game.home_score, game.away_score), (1, 4))
        self.assertEqual(game.period_label, "3.Drittel")
        self.assertEqual([(p.label, p.home, p.away) for p in game.periods],
                         [("1.Drittel", 0, 1), ("2.Drittel", 1, 3), ("3.Drittel", 1, 4)])
        self.assertIsNone(game.decided_in)
        self.assertEqual(game.home.name, "Schwenninger Wild Wings")

    def test_table_again_after_midnight(self):
        self.assertIsNone(self.provider.get_game_of_day(EBB, berlin(2026, 10, 26, 0, 0)))
        # Same moment given in UTC: 26.10. 00:00 CET == 25.10. 23:00 UTC
        utc = datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc)
        self.assertIsNone(self.provider.get_game_of_day(EBB, utc))

    def test_uses_both_endpoints(self):
        self.provider.get_game_of_day(EBB, berlin(2026, 10, 27))
        self.assertEqual(self.http.calls, [LAST_URL, NEXT_URL])

    def test_missing_endpoints_mean_no_game(self):
        provider, _ = make_provider({})  # both endpoints answer 404
        self.assertIsNone(provider.get_game_of_day(EBB, berlin(2026, 10, 27)))

    def test_null_body_means_no_game(self):
        provider, _ = make_provider({LAST_URL: None, NEXT_URL: None})
        self.assertIsNone(provider.get_game_of_day(EBB, berlin(2026, 10, 27)))

    def test_network_error_raises_provider_error(self):
        provider, _ = make_provider({LAST_URL: HttpError("boom"), NEXT_URL: self.next})
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(EBB, berlin(2026, 10, 30))

    def test_broken_match_raises_provider_error(self):
        broken = dict(self.next)
        del broken["team1"]
        provider, _ = make_provider({LAST_URL: None, NEXT_URL: broken})
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(EBB, berlin(2026, 10, 30))


class StatusTests(unittest.TestCase):
    START = "2026-11-01T18:30:00Z"  # 19:30 Berlin

    def provider_for(self, match):
        provider, _ = make_provider({LAST_URL: match, NEXT_URL: None})
        return provider

    def test_live_between_periods(self):
        match = synthetic_match(
            99001, self.START,
            [("1.Drittel", 1, 0, 1, "HalfTime"), ("2.Drittel", 1, 1, 1, "HalfTime")],
            finished=False,
        )
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 21, 15))
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertEqual((game.home_score, game.away_score), (1, 1))
        self.assertEqual(game.period_label, "2.Drittel")
        self.assertIsNone(game.clock)

    def test_live_without_results_has_no_score(self):
        match = synthetic_match(99002, self.START, [], finished=False)
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 19, 45))
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertIsNone(game.home_score)

    def test_scheduled_before_start(self):
        match = synthetic_match(99003, self.START, [], finished=False)
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 9, 0))
        self.assertEqual(game.status, GameStatus.SCHEDULED)

    def test_stale_unfinished_game_is_unknown(self):
        match = synthetic_match(
            99004, self.START, [("3.Drittel", 2, 1, 2, "After90Minutes")], finished=False
        )
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 23, 50))
        self.assertEqual(game.status, GameStatus.UNKNOWN)
        self.assertEqual((game.home_score, game.away_score), (2, 1))

    def test_overtime(self):
        match = synthetic_match(
            99005, self.START,
            [("1.Drittel", 0, 0, 1, "HalfTime"), ("2.Drittel", 1, 1, 1, "HalfTime"),
             ("3.Drittel", 2, 2, 2, "After90Minutes"), ("Overtime", 3, 2, 4, "AfterExtraTime")],
            finished=True,
        )
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 22, 30))
        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertEqual((game.home_score, game.away_score), (3, 2))
        self.assertEqual(game.period_label, "Overtime")
        self.assertEqual(game.decided_in, "OT")

    def test_shootout(self):
        match = synthetic_match(
            99006, self.START,
            [("1.Drittel", 0, 0, 1, "HalfTime"), ("2.Drittel", 1, 1, 1, "HalfTime"),
             ("3.Drittel", 2, 2, 2, "After90Minutes"), ("Overtime", 2, 2, 4, "AfterExtraTime"),
             ("Penalty", 3, 2, 5, "AfterPenalties")],
            finished=True,
        )
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 22, 45))
        self.assertEqual(game.decided_in, "SO")
        self.assertEqual((game.home_score, game.away_score), (3, 2))

    def test_results_are_ordered_by_result_order_id(self):
        match = synthetic_match(
            99007, self.START,
            [("1.Drittel", 1, 0, 1, "HalfTime"), ("2.Drittel", 2, 0, 1, "HalfTime")],
            finished=False,
        )
        match["matchResults"].reverse()  # API order should not matter
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 21, 0))
        self.assertEqual(game.period_label, "2.Drittel")
        self.assertEqual((game.home_score, game.away_score), (2, 0))

    def test_missing_utc_falls_back_to_local_time(self):
        match = synthetic_match(99008, self.START, [], finished=False)
        match["matchDateTimeUTC"] = None
        match["matchDateTime"] = "2026-11-01T19:30:00"
        game = self.provider_for(match).get_game_of_day(EBB, berlin(2026, 11, 1, 9, 0))
        self.assertEqual(game.start, datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
