"""Tests for the 0.5.0 display logic: marks, polling rhythm, plan, rotation and the display service."""

from __future__ import annotations

import dataclasses
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sportstracker.config import parse_config
from sportstracker.errors import ProviderError
from sportstracker.models import Game, GameStatus, TableRow, Team
from sportstracker.sensor import SensorReading, read_sensor, write_sensor
from sportstracker.service_data import DataService, poll_interval_for, update_marks
from sportstracker.service_display import DisplayService
from sportstracker.state import GameMarks, State, read_state, state_from_dict, state_to_dict, write_state
from sportstracker.views import Item, Plan, card_open, game_rank, plan, view_for_item

from test_services import Env, FakeDisplay, FakeProvider, make_table

BERLIN = ZoneInfo("Europe/Berlin")
STALE = 4000

T = {name: Team(str(i), name, name[:3].upper()) for i, name in enumerate(
    ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"], start=1)}


def utc(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


NOW = utc(2026, 10, 31, 18, 0)      # 19:00 Berlin (CET)


def table(order=("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot")):
    return [TableRow(rank=i, team=T[name], played=10, won=5, lost=5, points=20 - i) for i, name in enumerate(order, 1)]


def game(gid, home, away, status=GameStatus.LIVE, start=None, **kw):
    return Game(gid, T[home], T[away], start or NOW - timedelta(minutes=40), status, **kw)


def cfg(**kw):
    base = {"favorite_team_id": "1"}
    base.update(kw)
    return parse_config(base)


def mk_state(games, marks=None, tbl=None, now=NOW, **kw):
    return State(updated_at=now, provider="p", favorite_team_id="1", game=None,
                 table=tbl if tbl is not None else table(), games=games, marks=marks or {}, **kw)


def kinds(p):
    return [(i.kind, i.game.game_id if i.game else None, i.role) for i in p.items]


class StateRoundTripTest(unittest.TestCase):
    def test_games_marks_and_halftime_round_trip(self):
        g = game("a", "Alpha", "Bravo", home_score=10, away_score=12, halftime=True, period_label="Q2")
        marks = {"a": GameMarks(NOW - timedelta(minutes=30), NOW - timedelta(minutes=2), None)}
        state = mk_state([g, game("b", "Charlie", "Delta", GameStatus.SCHEDULED)], marks)
        again = state_from_dict(json.loads(json.dumps(state_to_dict(state))))
        self.assertEqual(again, state)
        self.assertTrue(again.games[0].halftime)

    def test_old_state_files_without_games_still_load(self):
        data = state_to_dict(mk_state([game("a", "Alpha", "Bravo")]))
        del data["games"]
        del data["marks"]
        again = state_from_dict(data)
        self.assertIsNone(again.games)
        self.assertEqual(again.marks, {})


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        c = parse_config({})
        self.assertTrue(c.show_other_games)
        self.assertEqual((c.rotation_main_seconds, c.rotation_card_seconds), (10, 10))
        self.assertEqual((c.result_window_minutes, c.halftime_max_minutes), (30, 15))
        self.assertEqual(c.idle_mode, "alternate")
        self.assertFalse(c.sensor["enabled"])
        self.assertEqual(c.sensor["gpio"], 25)
        self.assertTrue(c.camera["enabled"])

    def test_overrides_and_validation(self):
        c = parse_config({"idle_mode": "room", "rotation_card_seconds": 20,
                          "sensor": {"enabled": True, "gpio": 19, "temp_offset": -2.5}})
        self.assertEqual(c.idle_mode, "room")
        self.assertEqual(c.rotation_card_seconds, 20)
        self.assertEqual(c.sensor["gpio"], 19)
        self.assertEqual(c.sensor["temp_offset"], -2.5)
        from sportstracker.errors import ConfigError
        for bad in ({"idle_mode": "disco"}, {"rotation_main_seconds": 1}, {"result_window_minutes": 0},
                    {"sensor": {"gpio": 99}}, {"sensor": {"interval_seconds": 1}}):
            with self.assertRaises(ConfigError):
                parse_config(bad)


class MarksTest(unittest.TestCase):
    def test_marks_follow_the_game(self):
        live = game("a", "Alpha", "Bravo")
        marks = update_marks({}, [live], NOW)
        self.assertEqual(marks["a"], GameMarks(NOW, None, None))
        later = NOW + timedelta(minutes=20)
        ht = dataclasses.replace(live, halftime=True, period_break=True)
        marks = update_marks(marks, [ht], later)
        self.assertEqual(marks["a"], GameMarks(NOW, later, None))
        # the break is not reset while it lasts
        marks = update_marks(marks, [ht], later + timedelta(minutes=5))
        self.assertEqual(marks["a"].halftime_seen_at, later)
        # play resumes -> halftime mark cleared
        marks = update_marks(marks, [live], later + timedelta(minutes=10))
        self.assertIsNone(marks["a"].halftime_seen_at)
        end = later + timedelta(minutes=60)
        done = dataclasses.replace(live, status=GameStatus.FINISHED)
        marks = update_marks(marks, [done], end)
        self.assertEqual(marks["a"].finished_seen_at, end)
        marks = update_marks(marks, [done], end + timedelta(minutes=10))
        self.assertEqual(marks["a"].finished_seen_at, end)

    def test_games_that_are_no_longer_listed_are_forgotten(self):
        marks = update_marks({}, [game("a", "Alpha", "Bravo")], NOW)
        self.assertEqual(update_marks(marks, [], NOW), {})


class CardWindowTest(unittest.TestCase):
    def test_result_card_lasts_30_minutes(self):
        c = cfg()
        g = game("a", "Alpha", "Bravo", GameStatus.FINISHED)
        marks = GameMarks(finished_seen_at=NOW)
        self.assertTrue(card_open(g, marks, NOW + timedelta(minutes=29), c))
        self.assertFalse(card_open(g, marks, NOW + timedelta(minutes=31), c))
        self.assertFalse(card_open(g, None, NOW, c))

    def test_result_card_needs_a_recent_tipoff(self):
        # service restarted hours later: the first sighting of an old game must not trigger a card
        g = game("a", "Alpha", "Bravo", GameStatus.FINISHED, start=NOW - timedelta(hours=7))
        self.assertFalse(card_open(g, GameMarks(finished_seen_at=NOW), NOW, cfg()))

    def test_halftime_card_lasts_while_the_break_lasts(self):
        c = cfg()
        g = game("a", "Alpha", "Bravo", halftime=True)
        marks = GameMarks(live_seen_at=NOW, halftime_seen_at=NOW)
        self.assertTrue(card_open(g, marks, NOW + timedelta(minutes=14), c))
        self.assertFalse(card_open(g, marks, NOW + timedelta(minutes=16), c))
        self.assertFalse(card_open(dataclasses.replace(g, halftime=False), marks, NOW, c))


class RankTest(unittest.TestCase):
    def test_better_team_then_rank_sum_then_start(self):
        ranks = {t.team_id: r.rank for r in table() for t in [r.team]}
        a = game("a", "Alpha", "Foxtrot")     # best 1, sum 7
        b = game("b", "Bravo", "Charlie")     # best 2, sum 5
        self.assertLess(game_rank(a, ranks), game_rank(b, ranks))
        c = game("c", "Alpha", "Bravo")       # best 1, sum 3 -> beats a
        self.assertLess(game_rank(c, ranks), game_rank(a, ranks))
        d = game("d", "Alpha", "Bravo", start=NOW - timedelta(hours=1))
        self.assertLess(game_rank(d, ranks), game_rank(c, ranks))

    def test_unranked_teams_come_last(self):
        self.assertGreater(game_rank(game("x", "Alpha", "Bravo"), {})[0], 100)


class PlanTest(unittest.TestCase):
    def plan(self, state, now=NOW, **kw):
        return plan(state, now, cfg(**kw), BERLIN, STALE)

    def test_no_state(self):
        p = self.plan(None)
        self.assertEqual(p.items[0].kind, "message")

    def test_favorite_live_beats_better_ranked_game(self):
        fav = game("fav", "Echo", "Foxtrot")
        top = game("top", "Bravo", "Charlie")
        # favorite is "5" (Echo)
        p = self.plan(mk_state([top, fav]), favorite_team_id="5")
        self.assertEqual(kinds(p)[0], ("game", "fav", "main"))

    def test_best_placed_live_game_is_main_without_favorite_game(self):
        low = game("low", "Echo", "Foxtrot")
        top = game("top", "Bravo", "Charlie")
        p = self.plan(mk_state([low, top]), favorite_team_id="1")
        self.assertEqual(kinds(p)[0], ("game", "top", "main"))

    def test_no_table_falls_back_to_start_time(self):
        early = game("early", "Echo", "Foxtrot", start=NOW - timedelta(minutes=60))
        late = game("late", "Bravo", "Charlie", start=NOW - timedelta(minutes=10))
        p = self.plan(mk_state([late, early], tbl=[]))
        self.assertEqual(kinds(p)[0][1], "early")

    def test_halftime_card_rotates_with_main_game(self):
        main = game("main", "Alpha", "Bravo")
        ht = game("ht", "Charlie", "Delta", halftime=True, period_break=True)
        marks = {"ht": GameMarks(NOW - timedelta(minutes=40), NOW - timedelta(minutes=3), None)}
        p = self.plan(mk_state([main, ht], marks))
        self.assertEqual(kinds(p), [("game", "main", "main"), ("game", "ht", "card")])
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 3)).role, "main")
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 13)).role, "card")
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 23)).role, "main")

    def test_halftime_card_ends_with_the_break(self):
        main = game("main", "Alpha", "Bravo")
        running = game("other", "Charlie", "Delta", halftime=False)
        marks = {"other": GameMarks(NOW - timedelta(minutes=60), None, None)}
        p = self.plan(mk_state([main, running], marks))
        self.assertEqual(kinds(p), [("game", "main", "main")])

    def test_result_card_for_30_minutes_after_the_end(self):
        main = game("main", "Alpha", "Bravo")
        done = game("done", "Charlie", "Delta", GameStatus.FINISHED, home_score=70, away_score=60)
        marks = {"done": GameMarks(NOW - timedelta(minutes=70), None, NOW - timedelta(minutes=10))}
        self.assertEqual(len(self.plan(mk_state([main, done], marks), gameday_results=False).items), 2)
        marks = {"done": GameMarks(NOW - timedelta(minutes=70), None, NOW - timedelta(minutes=40))}
        self.assertEqual(len(self.plan(mk_state([main, done], marks), gameday_results=False).items), 1)

    def test_cards_sorted_by_rank(self):
        main = game("main", "Alpha", "Bravo")
        c1 = game("c1", "Echo", "Foxtrot", GameStatus.FINISHED)
        c2 = game("c2", "Charlie", "Delta", GameStatus.FINISHED)
        marks = {k: GameMarks(finished_seen_at=NOW - timedelta(minutes=5)) for k in ("c1", "c2")}
        p = self.plan(mk_state([main, c1, c2], marks))
        self.assertEqual([i[1] for i in kinds(p)], ["main", "c2", "c1"])

    def test_favorite_final_is_a_card_while_another_game_is_live(self):
        fav = game("fav", "Alpha", "Bravo", GameStatus.FINISHED, home_score=80, away_score=70)
        live = game("live", "Charlie", "Delta", start=NOW - timedelta(minutes=30))
        marks = {"fav": GameMarks(finished_seen_at=NOW - timedelta(minutes=5))}
        p = self.plan(mk_state([fav, live], marks), gameday_results=False)
        self.assertEqual(kinds(p), [("game", "live", "main"), ("game", "fav", "card")])
        # 31 minutes later only the live game remains
        later = NOW + timedelta(minutes=26)
        p = plan(mk_state([fav, live], marks, now=later), later, cfg(gameday_results=False), BERLIN, STALE)
        self.assertEqual(kinds(p), [("game", "live", "main")])

    def test_favorite_final_stays_until_midnight(self):
        fav = game("fav", "Alpha", "Bravo", GameStatus.FINISHED, start=NOW - timedelta(hours=2))
        state = mk_state([fav], {"fav": GameMarks(finished_seen_at=NOW - timedelta(hours=1))})
        p = self.plan(state)
        self.assertEqual(kinds(p), [("game", "fav", "idle")])
        after_midnight = utc(2026, 10, 31, 23, 0, 5)     # 00:00:05 Berlin on 1 Nov
        p = plan(dataclasses.replace(state, updated_at=after_midnight), after_midnight, cfg(), BERLIN, STALE)
        self.assertEqual([i.kind for i in p.items], ["table", "room"])

    def test_idle_rotation_with_kickoff_table_room(self):
        fav = game("fav", "Alpha", "Bravo", GameStatus.SCHEDULED, start=NOW + timedelta(hours=3))
        p = self.plan(mk_state([fav]))
        self.assertEqual([i.kind for i in p.items], ["game", "table", "room"])
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 5)).kind, "game")
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 15)).kind, "table")
        self.assertEqual(p.item_at(utc(2026, 10, 31, 18, 0, 25)).kind, "room")

    def test_idle_without_favorite_game(self):
        other = game("o", "Charlie", "Delta", GameStatus.SCHEDULED, start=NOW + timedelta(hours=3))
        p = self.plan(mk_state([other]))
        self.assertEqual([i.kind for i in p.items], ["table", "room"])

    def test_idle_modes(self):
        state = mk_state([])
        self.assertEqual([i.kind for i in self.plan(state, idle_mode="table").items], ["table"])
        self.assertEqual([i.kind for i in self.plan(state, idle_mode="room").items], ["room"])
        self.assertEqual([i.kind for i in self.plan(mk_state([], tbl=[]), idle_mode="table").items], ["message"])

    def test_show_other_games_off(self):
        main = game("main", "Charlie", "Delta")
        self.assertEqual([i.kind for i in self.plan(mk_state([main]), show_other_games=False).items], ["table", "room"])
        fav = game("fav", "Alpha", "Bravo")
        p = self.plan(mk_state([main, fav]), show_other_games=False)
        self.assertEqual(kinds(p), [("game", "fav", "main")])

    def test_legacy_state_without_games_list(self):
        fav = game("fav", "Alpha", "Bravo", GameStatus.FINISHED, start=NOW - timedelta(hours=2))
        state = mk_state(None)
        state = dataclasses.replace(state, game=fav)
        self.assertEqual(kinds(self.plan(state)), [("game", "fav", "idle")])

    def test_cancelled_games_are_ignored(self):
        p = self.plan(mk_state([game("c", "Alpha", "Bravo", GameStatus.CANCELLED)]))
        self.assertEqual([i.kind for i in p.items], ["table", "room"])

    def test_stale_flag(self):
        p = self.plan(mk_state([], now=NOW - timedelta(hours=3)))
        self.assertTrue(p.stale)
        self.assertTrue(self.plan(mk_state([], error="boom")).stale)
        self.assertFalse(self.plan(mk_state([])).stale)

    def test_item_at_single_and_ordering(self):
        single = Plan((Item("table"),))
        self.assertEqual(single.item_at(NOW).kind, "table")
        three = Plan((Item("game", seconds=5), Item("table", seconds=10), Item("room", seconds=15)))
        picks = [three.item_at(utc(2026, 10, 31, 18, 0, s)).kind for s in (0, 4, 5, 14, 15, 29, 30)]
        self.assertEqual(picks, ["game", "game", "table", "table", "room", "room", "game"])


class ViewForItemTest(unittest.TestCase):
    def test_room_uses_only_fresh_readings(self):
        c = cfg(sensor={"enabled": True})
        fresh = SensorReading(NOW - timedelta(seconds=30), 21.5, 48.0)
        old = SensorReading(NOW - timedelta(hours=1), 21.5, 48.0)
        self.assertIs(view_for_item(Item("room"), None, NOW, False, fresh, c).reading, fresh)
        self.assertIsNone(view_for_item(Item("room"), None, NOW, False, old, c).reading)
        self.assertTrue(view_for_item(Item("room"), None, NOW, False, None, c).show_climate)
        self.assertFalse(view_for_item(Item("room"), None, NOW, False, None, cfg()).show_climate)

    def test_table_without_rows_becomes_message(self):
        state = mk_state([], tbl=[])
        self.assertEqual(view_for_item(Item("table"), state, NOW, False, None, cfg()).kind, "message")


class PollIntervalForTest(unittest.TestCase):
    c = cfg()

    def test_busy_while_any_game_is_live(self):
        live = game("a", "Charlie", "Delta")
        self.assertEqual(poll_interval_for([live], None, {}, NOW, self.c, BERLIN), 60)

    def test_card_window_keeps_polling(self):
        done = game("a", "Charlie", "Delta", GameStatus.FINISHED)
        marks = {"a": GameMarks(finished_seen_at=NOW - timedelta(minutes=5))}
        self.assertEqual(poll_interval_for([done], None, marks, NOW, self.c, BERLIN), 60)
        self.assertEqual(poll_interval_for([done], None, {}, NOW, self.c, BERLIN), 900)

    def test_idle_wakes_five_minutes_before_kickoff(self):
        soon = game("a", "Charlie", "Delta", GameStatus.SCHEDULED, start=NOW + timedelta(minutes=12))
        self.assertAlmostEqual(poll_interval_for([soon], None, {}, NOW, self.c, BERLIN), 7 * 60, places=3)
        near = game("a", "Charlie", "Delta", GameStatus.SCHEDULED, start=NOW + timedelta(minutes=4))
        self.assertEqual(poll_interval_for([near], None, {}, NOW, self.c, BERLIN), 60)

    def test_overdue_scheduled_game_polls_fast(self):
        late = game("a", "Charlie", "Delta", GameStatus.SCHEDULED, start=NOW - timedelta(minutes=2))
        self.assertEqual(poll_interval_for([late], None, {}, NOW, self.c, BERLIN), 60)

    def test_favorite_game_scheduled_is_busy(self):
        fav = game("a", "Alpha", "Bravo", GameStatus.SCHEDULED, start=NOW + timedelta(hours=5))
        self.assertEqual(poll_interval_for([fav], fav, {}, NOW, self.c, BERLIN), 60)

    def test_nothing_to_do(self):
        self.assertEqual(poll_interval_for([], None, {}, NOW, self.c, BERLIN), 900)
        self.assertEqual(poll_interval_for(None, None, {}, NOW, self.c, BERLIN), 900)


class ListingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.games = []
        self.fail_games = False

    def get_games_of_day(self, now):
        self.calls.append("games")
        if self.fail_games:
            raise ProviderError("schedule down")
        return list(self.games)


class DataServiceListingTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.now = [utc(2026, 10, 30, 17, 0)]
        self.provider = ListingProvider()
        self.service = DataService(self.env.config_path, root=self.env.root, http=object(),
                                   now_func=lambda: self.now[0], provider_factory=lambda *a, **k: self.provider)
        self.path = os.path.join(self.env.root, "data", "state.json")

    def tearDown(self):
        self.env.close()

    def state(self):
        return read_state(self.path)

    def start(self, **kw):
        return utc(2026, 10, 30, 17, 0) - timedelta(minutes=20)

    def test_lists_all_games_and_records_marks(self):
        live = Game("g1", T["Alpha"], T["Bravo"], self.start(), GameStatus.LIVE, home_score=3, away_score=2)
        other = Game("g2", T["Charlie"], T["Delta"], self.start(), GameStatus.LIVE, halftime=True)
        self.provider.games = [live, other]
        self.service.cycle()
        st = self.state()
        self.assertEqual([g.game_id for g in st.games], ["g1", "g2"])
        self.assertEqual(st.marks["g2"].halftime_seen_at, self.now[0])
        self.assertNotIn("game", self.provider.calls)       # the listing replaces the single lookup

    def test_marks_survive_between_cycles(self):
        self.provider.games = [Game("g2", T["Charlie"], T["Delta"], self.start(), GameStatus.LIVE, halftime=True)]
        self.service.cycle()
        first = self.now[0]
        self.now[0] += timedelta(minutes=3)
        self.service.cycle()
        self.assertEqual(self.state().marks["g2"].halftime_seen_at, first)

    def test_error_keeps_games_and_marks(self):
        self.provider.games = [Game("g2", T["Charlie"], T["Delta"], self.start(), GameStatus.LIVE, halftime=True)]
        self.service.cycle()
        self.provider.fail_games = True
        self.now[0] += timedelta(minutes=1)
        self.assertEqual(self.service.cycle(), 60)
        st = self.state()
        self.assertIn("schedule down", st.error)
        self.assertEqual([g.game_id for g in st.games], ["g2"])
        self.assertIn("g2", st.marks)

    def test_providers_without_listing_still_work(self):
        plain = FakeProvider()
        service = DataService(self.env.config_path, root=self.env.root, http=object(),
                              now_func=lambda: self.now[0], provider_factory=lambda *a, **k: plain)
        service.cycle()
        self.assertIsNone(self.state().games)


class DisplayRotationTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(sensor={"enabled": True})
        self.now = [utc(2026, 10, 30, 12, 0, 5)]
        self.display = FakeDisplay()
        self.service = DisplayService(self.env.config_path, display=self.display, root=self.env.root,
                                      now_func=lambda: self.now[0])
        self.state_path = os.path.join(self.env.root, "data", "state.json")
        self.sensor_path = os.path.join(self.env.root, "data", "sensor.json")

    def tearDown(self):
        self.env.close()

    def put(self, games, marks=None):
        st = State(updated_at=self.now[0], provider="p", favorite_team_id="1", table=make_table(),
                   games=games, marks=marks or {})
        write_state(self.state_path, st)
        future = os.path.getmtime(self.state_path) + 5 + len(self.display.shown)
        os.utime(self.state_path, (future, future))

    def test_idle_rotation_between_table_and_room(self):
        self.put([])
        self.now[0] = utc(2026, 10, 30, 12, 0, 5)        # slot 5 -> table (10 s)
        self.assertTrue(self.service.step())
        table_frame = self.display.shown[-1].tobytes()
        self.assertFalse(self.service.step())
        self.now[0] = utc(2026, 10, 30, 12, 0, 15)       # slot 15 -> room
        self.assertTrue(self.service.step())
        self.assertNotEqual(self.display.shown[-1].tobytes(), table_frame)
        self.now[0] = utc(2026, 10, 30, 12, 0, 25)       # back to the table
        self.assertTrue(self.service.step())
        self.assertEqual(self.display.shown[-1].tobytes(), table_frame)
        self.assertEqual([i.kind for i in self.service.current_plan.items], ["table", "room"])

    def test_room_view_redraws_every_minute_and_reads_the_sensor(self):
        self.put([])
        self.now[0] = utc(2026, 10, 30, 12, 0, 15)       # room slot
        self.service.step()
        before = len(self.display.shown)
        self.now[0] = utc(2026, 10, 30, 12, 0, 18)
        self.assertFalse(self.service.step())            # same minute, same picture
        write_sensor(self.sensor_path, SensorReading(self.now[0], 22.5, 51.0))
        os.utime(self.sensor_path, (os.path.getmtime(self.sensor_path) + 3,) * 2)
        self.assertTrue(self.service.step())             # new reading -> redraw
        self.assertEqual(len(self.display.shown), before + 1)
        self.now[0] = utc(2026, 10, 30, 12, 1, 15)       # next minute (still a room slot)
        self.assertTrue(self.service.step())

    def test_sensor_file_round_trip(self):
        write_sensor(self.sensor_path, SensorReading(self.now[0], 20.0, 40.0))
        again = read_sensor(self.sensor_path)
        self.assertEqual((again.temperature_c, again.humidity_pct), (20.0, 40.0))
        self.assertTrue(again.is_fresh(self.now[0] + timedelta(seconds=100)))
        self.assertFalse(again.is_fresh(self.now[0] + timedelta(seconds=400)))

    def test_card_and_main_game_alternate(self):
        main = Game("m", T["Alpha"], T["Bravo"], self.now[0] - timedelta(minutes=30), GameStatus.LIVE,
                    home_score=10, away_score=8)
        done = Game("d", T["Charlie"], T["Delta"], self.now[0] - timedelta(hours=2), GameStatus.FINISHED,
                    home_score=70, away_score=60)
        marks = {"d": GameMarks(finished_seen_at=self.now[0] - timedelta(minutes=5))}
        self.put([main, done], marks)
        self.service.step()
        first = self.display.shown[-1].tobytes()
        self.now[0] = utc(2026, 10, 30, 12, 0, 15)
        self.assertTrue(self.service.step())
        self.assertNotEqual(self.display.shown[-1].tobytes(), first)


if __name__ == "__main__":
    unittest.main()
