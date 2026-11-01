from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sportstracker.errors import HttpError, ProviderError
from sportstracker.models import GameStatus
from sportstracker.providers import create_provider
from sportstracker.providers import genius_rbbl as g
from sportstracker.providers.genius_rbbl import GeniusRbblProvider, parse_widget_time

from genius_markup import match_html, schedule_payload, standings_payload
from helpers import FakeHttp

BERLIN = ZoneInfo("Europe/Berlin")
BASE = "https://hosted.dcd.shared.geniussports.com"
SCHED = BASE + "/embednf/WHBA/en/competition/49807/schedule"
STAND = BASE + "/embednf/WHBA/en/competition/49807/standings"
LIVE = BASE + "/ldata/basketball/competitions/comp49807.json"

MUENSTER, LAHN, RHINOS, ZWICKAU, KOELN, TRIER = "36911", "36370", "36369", "36365", "36368", "36366"


def berlin(month, day, hour=12, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=BERLIN)


def default_matches():
    return [
        # real: finished game on 17.10.
        match_html("2897422", "STATUS_COMPLETE", "Oct 17, 2026, 5:00 PM", MUENSTER, LAHN, 62, 65),
        # real: scheduled game on 31.10.
        match_html("2897434", "STATUS_SCHEDULED", "Oct 31, 2026, 5:00 PM", MUENSTER, RHINOS),
        # synthetic pairings with Zwickau
        match_html("2897440", "STATUS_SCHEDULED", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN),
        match_html("2897441", "STATUS_POSTPONED", "Nov 7, 2026, 5:00 PM", TRIER, ZWICKAU),
    ]


def make_provider(extra_routes=None, matches=None, live_ids="", **kwargs):
    routes = {
        SCHED: schedule_payload(matches if matches is not None else default_matches(), live_ids),
        STAND: standings_payload(),
    }
    routes.update(extra_routes or {})
    http = FakeHttp(routes)
    return GeniusRbblProvider(http, 49807, BERLIN, **kwargs), http


class TimeParsingTests(unittest.TestCase):
    def parse(self, text):
        return parse_widget_time(text, BERLIN)

    def test_summer_time(self):
        self.assertEqual(self.parse("Oct 17, 2026, 5:00 PM"), datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc))

    def test_winter_time(self):
        self.assertEqual(self.parse("Oct 31, 2026, 5:00 PM"), datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc))

    def test_half_hour(self):
        self.assertEqual(self.parse("Oct 23, 2026, 7:30 PM"), datetime(2026, 10, 23, 17, 30, tzinfo=timezone.utc))

    def test_noon_and_midnight(self):
        self.assertEqual(self.parse("Nov 7, 2026, 12:30 PM").astimezone(BERLIN).hour, 12)
        self.assertEqual(self.parse("Nov 7, 2026, 12:15 AM").astimezone(BERLIN).hour, 0)

    def test_24h_without_marker(self):
        self.assertEqual(self.parse("Nov 7, 2026, 17:00").astimezone(BERLIN).hour, 17)

    def test_invalid(self):
        for bad in ("", "tomorrow", "Foo 17, 2026, 5:00 PM"):
            with self.assertRaises(ValueError):
                self.parse(bad)


class HelperTests(unittest.TestCase):
    def test_clock_format(self):
        self.assertEqual(g._format_clock("07:32:00"), "07:32")
        self.assertEqual(g._format_clock("07:32:41"), "07:32:41")
        self.assertIsNone(g._format_clock("00:00:00"))
        self.assertIsNone(g._format_clock(None))
        self.assertEqual(g._format_clock("garbled"), "garbled")

    def test_period_label(self):
        self.assertEqual(g._period_label(3, "REGULAR"), "Q3")
        self.assertEqual(g._period_label(1, "EXTRATIME"), "OT")
        self.assertEqual(g._period_label(2, "EXTRATIME"), "OT2")
        self.assertEqual(g._period_label(1, "PENALTYSHOOTOUT"), "SO")
        self.assertIsNone(g._period_label(None, "REGULAR"))

    def test_live_scores_shapes(self):
        self.assertEqual(g._live_scores({"1": 10, "2": 8}), (10, 8))
        self.assertEqual(g._live_scores({1: 10, 2: 8}), (10, 8))
        self.assertEqual(g._live_scores([None, 10, 8]), (10, 8))
        self.assertEqual(g._live_scores([10, 8]), (10, 8))
        self.assertIsNone(g._live_scores("x"))
        self.assertIsNone(g._live_scores({"1": "a", "2": 1}))


class TableTests(unittest.TestCase):
    def test_table(self):
        provider, _ = make_provider()
        rows = provider.get_table()
        self.assertEqual(len(rows), 8)
        first = rows[0]
        self.assertEqual((first.rank, first.team.team_id, first.team.name), (1, "36362", "RSB Thuringia Bulls"))
        self.assertEqual((first.played, first.won, first.lost, first.points, first.goal_diff), (2, 2, 0, 4, 89))
        self.assertTrue(first.team.logo_url.endswith("5eb2722a1622f0782cb42c94091b252fT1.png"))
        zwickau = [r for r in rows if r.team.team_id == ZWICKAU][0]
        self.assertEqual((zwickau.rank, zwickau.lost, zwickau.goal_diff), (7, 1, -16))
        self.assertEqual(rows[5].played, 0)  # Koeln without games

    def test_list_teams(self):
        provider, _ = make_provider()
        teams = provider.list_teams()
        self.assertEqual(len(teams), 8)
        self.assertIn("RB Zwickau e.V.", [t.name for t in teams])

    def test_empty_standings_is_error(self):
        provider, _ = make_provider({STAND: {"css": [], "js": [], "html": "<div>nothing</div>"}})
        with self.assertRaises(ProviderError):
            provider.get_table()


class GameOfDayTests(unittest.TestCase):
    def test_scheduled_today_for_home_and_away_team(self):
        provider, _ = make_provider()
        for team_id in (ZWICKAU, KOELN):
            game = provider.get_game_of_day(team_id, berlin(10, 31, 9))
            self.assertIsNotNone(game, team_id)
            self.assertEqual(game.game_id, "2897440")
            self.assertEqual(game.status, GameStatus.SCHEDULED)
            self.assertEqual((game.home.name, game.away.name), ("RB Zwickau e.V.", "RBC Köln 99ers"))
            self.assertEqual(game.start, datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc))
            self.assertIsNone(game.home_score)
            self.assertTrue(game.home.logo_url.startswith("https://images.statsengine"))

    def test_no_game_today(self):
        provider, _ = make_provider()
        self.assertIsNone(provider.get_game_of_day(ZWICKAU, berlin(10, 27)))

    def test_finished_game_stays_until_midnight(self):
        provider, _ = make_provider()
        game = provider.get_game_of_day(MUENSTER, berlin(10, 17, 23, 59))
        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertEqual((game.home_score, game.away_score), (62, 65))
        self.assertEqual((game.home.name, game.away.name), ("Spinto Münsterland", "RSV Lahn-Dill"))
        self.assertIsNone(provider.get_game_of_day(MUENSTER, berlin(10, 18, 0, 0)))

    def test_postponed_game_is_not_game_of_day(self):
        provider, _ = make_provider()
        self.assertIsNone(provider.get_game_of_day(ZWICKAU, berlin(11, 7)))

    def test_schedule_is_cached_between_calls(self):
        provider, http = make_provider(schedule_ttl=900)
        provider.get_game_of_day(ZWICKAU, berlin(10, 31, 9, 0))
        provider.get_game_of_day(ZWICKAU, berlin(10, 31, 9, 5))
        self.assertEqual(http.calls.count(SCHED), 1)
        provider.get_game_of_day(ZWICKAU, berlin(10, 31, 9, 20))  # > 15 min later
        self.assertEqual(http.calls.count(SCHED), 2)

    def test_static_live_status(self):
        matches = [match_html("2897440", "STATUS_IN_PROGRESS", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN, 4, 2)]
        provider, _ = make_provider(matches=matches)
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 10))
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertEqual((game.home_score, game.away_score), (4, 2))

    def test_live_ids_mark_game_live(self):
        provider, _ = make_provider(live_ids="2897440,2897999")
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 10))
        self.assertEqual(game.status, GameStatus.LIVE)


class LiveOverlayTests(unittest.TestCase):
    NOW = berlin(10, 31, 17, 20)

    def live_provider(self, entry):
        return make_provider({LIVE: {"2897440": entry}})

    def test_in_progress(self):
        provider, _ = self.live_provider(
            {"clock": "07:32:00", "status": "INPROGRESS", "period": 2, "periodType": "REGULAR", "scores": {"1": 18, "2": 15}}
        )
        game = provider.get_game_of_day(ZWICKAU, self.NOW)
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertEqual((game.home_score, game.away_score), (18, 15))
        self.assertEqual(game.period_label, "Q2")
        self.assertEqual(game.clock, "07:32")
        self.assertFalse(game.period_break)

    def test_period_break(self):
        provider, _ = self.live_provider(
            {"clock": "00:00:00", "status": "PERIODBREAK", "period": 2, "periodType": "REGULAR", "scores": [None, 30, 28]}
        )
        game = provider.get_game_of_day(ZWICKAU, self.NOW)
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertTrue(game.period_break)
        self.assertIsNone(game.clock)
        self.assertEqual(game.period_label, "Q2")
        self.assertEqual((game.home_score, game.away_score), (30, 28))

    def test_extra_time(self):
        provider, _ = self.live_provider(
            {"clock": "02:10:00", "status": "INPROGRESS", "period": 1, "periodType": "EXTRATIME", "scores": {"1": 60, "2": 60}}
        )
        self.assertEqual(provider.get_game_of_day(ZWICKAU, self.NOW).period_label, "OT")

    def test_complete_overrides_stale_schedule(self):
        provider, _ = self.live_provider(
            {"clock": "00:00:00", "status": "COMPLETE", "period": 4, "periodType": "REGULAR", "scores": {"1": 71, "2": 66}}
        )
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 19, 30))
        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertEqual((game.home_score, game.away_score), (71, 66))
        self.assertIsNone(game.clock)

    def test_not_requested_long_before_tip_off(self):
        provider, http = self.live_provider({"status": "INPROGRESS"})
        provider.get_game_of_day(ZWICKAU, berlin(10, 31, 9, 0))
        self.assertNotIn(LIVE, http.calls)

    def test_live_feed_failure_is_not_fatal(self):
        provider, _ = make_provider({LIVE: HttpError("boom")})
        game = provider.get_game_of_day(ZWICKAU, self.NOW)
        self.assertEqual(game.status, GameStatus.SCHEDULED)

    def test_unknown_match_in_live_feed_is_ignored(self):
        provider, _ = make_provider({LIVE: {"1": {"status": "INPROGRESS"}}})
        self.assertEqual(provider.get_game_of_day(ZWICKAU, self.NOW).status, GameStatus.SCHEDULED)

    def test_garbage_live_feed_is_ignored(self):
        provider, _ = make_provider({LIVE: ["not", "a", "dict"]})
        self.assertEqual(provider.get_game_of_day(ZWICKAU, self.NOW).status, GameStatus.SCHEDULED)


class LateResultTests(unittest.TestCase):
    """RBBL results are often entered after the game: no 0:0 placeholders, faster refresh until the final shows."""

    def ready_entry(self):
        return {"clock": "10:00:00", "status": "READY", "period": 1, "periodType": "REGULAR", "scores": {"1": 0, "2": 0}}

    def test_ready_entry_does_not_show_zero_zero(self):
        provider, _ = make_provider({LIVE: {"2897440": self.ready_entry()}})
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 40))
        self.assertEqual(game.status, GameStatus.SCHEDULED)
        self.assertEqual((game.home_score, game.away_score), (None, None))

    def test_ready_entry_does_not_replace_a_result(self):
        matches = [match_html("2897440", "STATUS_IN_PROGRESS", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN, 40, 31)]
        provider, _ = make_provider({LIVE: {"2897440": self.ready_entry()}}, matches=matches)
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 40))
        self.assertEqual((game.home_score, game.away_score), (40, 31))

    def test_live_placeholder_zero_zero_is_unknown(self):
        matches = [match_html("2897440", "STATUS_IN_PROGRESS", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN, 0, 0)]
        provider, _ = make_provider(matches=matches)
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 40))
        self.assertEqual(game.status, GameStatus.LIVE)
        self.assertEqual((game.home_score, game.away_score), (None, None))

    def test_real_zero_zero_from_the_live_feed_stays(self):
        entry = {"clock": "10:00:00", "status": "INPROGRESS", "period": 1, "periodType": "REGULAR", "scores": {"1": 0, "2": 0}}
        provider, _ = make_provider({LIVE: {"2897440": entry}})
        game = provider.get_game_of_day(ZWICKAU, berlin(10, 31, 17, 1))
        self.assertEqual((game.home_score, game.away_score), (0, 0))

    def test_schedule_is_refreshed_every_minute_until_the_final_shows(self):
        matches = [match_html("2897440", "STATUS_SCHEDULED", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN)]
        provider, http = make_provider(matches=matches, schedule_ttl=900)
        provider.get_games_of_day(berlin(10, 31, 18, 0))
        provider.get_games_of_day(berlin(10, 31, 18, 0))
        self.assertEqual(http.calls.count(SCHED), 1)
        provider.get_games_of_day(berlin(10, 31, 18, 2))                 # more than 60 s later
        self.assertEqual(http.calls.count(SCHED), 2)

    def test_final_result_arrives_with_the_next_refresh(self):
        provider, http = make_provider(matches=[match_html("2897440", "STATUS_SCHEDULED", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN)])
        game = provider.get_games_of_day(berlin(10, 31, 18, 30))[0]
        self.assertEqual((game.status, game.home_score), (GameStatus.SCHEDULED, None))
        http.routes[SCHED] = schedule_payload(
            [match_html("2897440", "STATUS_COMPLETE", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN, 52, 38)], "")
        game = provider.get_games_of_day(berlin(10, 31, 18, 32))[0]
        self.assertEqual((game.status, game.home_score, game.away_score), (GameStatus.FINISHED, 52, 38))

    def test_quiet_times_keep_the_long_interval(self):
        matches = [match_html("2897440", "STATUS_COMPLETE", "Oct 31, 2026, 5:00 PM", ZWICKAU, KOELN, 52, 38),
                   match_html("2897441", "STATUS_SCHEDULED", "Nov 7, 2026, 5:00 PM", ZWICKAU, TRIER)]
        provider, http = make_provider(matches=matches, schedule_ttl=900)
        provider.get_games_of_day(berlin(10, 31, 19, 0))
        provider.get_games_of_day(berlin(10, 31, 19, 5))
        self.assertEqual(http.calls.count(SCHED), 1)


class GamesOfDayTests(unittest.TestCase):
    """get_games_of_day (0.5.0): all games of the league that are relevant today."""

    def matches(self):
        return [
            match_html("1001", "STATUS_SCHEDULED", "Oct 31, 2026, 5:00 PM", MUENSTER, RHINOS),
            match_html("1002", "STATUS_SCHEDULED", "Oct 31, 2026, 7:30 PM", ZWICKAU, KOELN),
            match_html("1003", "STATUS_SCHEDULED", "Nov 1, 2026, 5:00 PM", TRIER, LAHN),
            match_html("1004", "STATUS_POSTPONED", "Oct 31, 2026, 5:00 PM", TRIER, ZWICKAU),
            match_html("1005", "STATUS_COMPLETE", "Oct 30, 2026, 11:00 PM", LAHN, KOELN, 70, 60),
        ]

    def test_lists_today_sorted_without_cancelled_or_other_days(self):
        provider, _ = make_provider(matches=self.matches())
        games = provider.get_games_of_day(berlin(10, 31, 12))
        self.assertEqual([g.game_id for g in games], ["1001", "1002"])

    def test_game_that_started_before_midnight_stays_listed(self):
        provider, _ = make_provider(matches=self.matches())
        games = provider.get_games_of_day(berlin(10, 31, 0, 30))   # 1005 started 23:00 the day before
        self.assertIn("1005", [g.game_id for g in games])

    def test_old_game_is_dropped(self):
        provider, _ = make_provider(matches=self.matches())
        games = provider.get_games_of_day(berlin(11, 1, 9, 0))
        self.assertNotIn("1005", [g.game_id for g in games])

    def test_halftime_flag_after_q2(self):
        entry = {"clock": "00:00:00", "status": "PERIODBREAK", "period": 2, "periodType": "REGULAR", "scores": {"1": 30, "2": 28}}
        provider, _ = make_provider({LIVE: {"1001": entry}}, matches=self.matches())
        games = {g.game_id: g for g in provider.get_games_of_day(berlin(10, 31, 17, 50))}
        self.assertTrue(games["1001"].halftime)
        self.assertEqual(games["1001"].status, GameStatus.LIVE)
        self.assertFalse(games["1002"].halftime)

    def test_break_after_q1_is_no_halftime(self):
        entry = {"clock": "00:00:00", "status": "PERIODBREAK", "period": 1, "periodType": "REGULAR", "scores": {"1": 10, "2": 12}}
        provider, _ = make_provider({LIVE: {"1001": entry}}, matches=self.matches())
        games = {g.game_id: g for g in provider.get_games_of_day(berlin(10, 31, 17, 25))}
        self.assertTrue(games["1001"].period_break)
        self.assertFalse(games["1001"].halftime)

    def test_complete_clears_halftime(self):
        entry = {"clock": "00:00:00", "status": "COMPLETE", "period": 4, "periodType": "REGULAR", "scores": {"1": 80, "2": 75}}
        provider, _ = make_provider({LIVE: {"1001": entry}}, matches=self.matches())
        games = {g.game_id: g for g in provider.get_games_of_day(berlin(10, 31, 19, 30))}
        self.assertEqual(games["1001"].status, GameStatus.FINISHED)
        self.assertFalse(games["1001"].halftime)

    def test_forgotten_live_game_becomes_unknown(self):
        entry = {"clock": "05:00:00", "status": "INPROGRESS", "period": 3, "periodType": "REGULAR", "scores": {"1": 30, "2": 28}}
        matches = [match_html("1001", "STATUS_IN_PROGRESS", "Oct 31, 2026, 5:00 PM", MUENSTER, RHINOS, 30, 28)]
        provider, _ = make_provider({LIVE: {"1001": entry}}, matches=matches)
        games = provider.get_games_of_day(berlin(10, 31, 23, 30))   # 6.5 h after tip-off
        self.assertEqual(games[0].status, GameStatus.UNKNOWN)
        self.assertFalse(games[0].halftime)

    def test_schedule_is_fetched_once_for_all_games(self):
        entry = {"clock": "05:00:00", "status": "INPROGRESS", "period": 1, "periodType": "REGULAR", "scores": {"1": 3, "2": 2}}
        provider, http = make_provider({LIVE: {"1001": entry, "1002": entry}}, matches=self.matches())
        provider.get_games_of_day(berlin(10, 31, 19, 40))
        self.assertGreaterEqual(http.calls.count(LIVE), 1)   # the HTTP client caches it for LIVE_TTL seconds
        self.assertEqual(http.calls.count(SCHED), 1)


class ErrorTests(unittest.TestCase):
    def test_network_error(self):
        provider, _ = make_provider({SCHED: HttpError("boom")})
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(ZWICKAU, berlin(10, 31))

    def test_no_html_member(self):
        provider, _ = make_provider({SCHED: {"css": [], "js": []}})
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(ZWICKAU, berlin(10, 31))

    def test_no_matches_means_markup_changed(self):
        provider, _ = make_provider({SCHED: {"html": "<div>new design</div>"}})
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(ZWICKAU, berlin(10, 31))

    def test_bad_time_text(self):
        matches = [match_html("1", "STATUS_SCHEDULED", "soon", ZWICKAU, KOELN)]
        provider, _ = make_provider(matches=matches)
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(ZWICKAU, berlin(10, 31))

    def test_failed_refresh_keeps_provider_usable(self):
        provider, http = make_provider(schedule_ttl=60)
        self.assertIsNotNone(provider.get_game_of_day(ZWICKAU, berlin(10, 31, 9, 0)))
        http.routes[SCHED] = HttpError("down")
        with self.assertRaises(ProviderError):
            provider.get_game_of_day(ZWICKAU, berlin(10, 31, 12, 0))
        http.routes[SCHED] = schedule_payload(default_matches())
        self.assertIsNotNone(provider.get_game_of_day(ZWICKAU, berlin(10, 31, 12, 5)))


class FactoryTests(unittest.TestCase):
    def test_from_params(self):
        provider = create_provider("genius_rbbl", {"competition_id": 49807}, FakeHttp({}), BERLIN)
        self.assertIsInstance(provider, GeniusRbblProvider)
        self.assertEqual(provider.competition_id, 49807)

    def test_missing_competition(self):
        with self.assertRaises(ProviderError):
            create_provider("genius_rbbl", {}, FakeHttp({}), BERLIN)

    def test_bad_source_timezone(self):
        with self.assertRaises(ProviderError):
            create_provider("genius_rbbl", {"competition_id": 1, "source_timezone": "Mars/Base"}, FakeHttp({}), BERLIN)

    def test_unknown_provider(self):
        with self.assertRaises(ProviderError):
            create_provider("nope", {}, FakeHttp({}), BERLIN)

    def test_openligadb_via_factory(self):
        provider = create_provider(
            "openligadb", {"league_shortcut": "del", "season": 2026, "league_id": 4947}, FakeHttp({}), BERLIN
        )
        self.assertEqual(provider.name, "openligadb")


if __name__ == "__main__":
    unittest.main()
