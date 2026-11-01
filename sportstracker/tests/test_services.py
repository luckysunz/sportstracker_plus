import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sportstracker.config import parse_config
from sportstracker.errors import ProviderError
from sportstracker.models import Game, GameStatus, PeriodScore, TableRow, Team
from sportstracker.service_data import DataService, poll_interval
from sportstracker.service_display import DisplayService, RestartRequired
from sportstracker.state import State, read_state, state_from_dict, state_to_dict, write_state
from sportstracker.views import choose_view

BERLIN = ZoneInfo("Europe/Berlin")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME, AWAY = Team("1", "Eisbären Berlin", "EBB"), Team("2", "ERC Ingolstadt", "ING")


def utc(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def make_game(status=GameStatus.SCHEDULED, start=None, **kw):
    return Game("g1", HOME, AWAY, start or utc(2026, 10, 30, 18, 30), status, **kw)


def make_table():
    return [TableRow(rank=1, team=HOME, played=12, won=9, lost=3, points=28),
            TableRow(rank=2, team=AWAY, played=12, won=8, lost=4, points=25)]


class StateTest(unittest.TestCase):
    def test_round_trip(self):
        game = make_game(GameStatus.LIVE, home_score=1, away_score=3, period_label="2.Drittel", clock=None,
                         periods=(PeriodScore("1.Drittel", 0, 1), PeriodScore("2.Drittel", 1, 3)), decided_in="OT")
        state = State(utc(2026, 10, 30, 19), "p", "1", game, make_table(), "boom", utc(2026, 10, 30, 18), utc(2026, 10, 30, 20))
        again = state_from_dict(json.loads(json.dumps(state_to_dict(state))))
        self.assertEqual(again, state)

    def test_write_read_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data", "state.json")
            self.assertIsNone(read_state(path))
            state = State(utc(2026, 10, 30, 19), "p", "1", None, make_table())
            write_state(path, state)
            self.assertEqual(read_state(path), state)
            self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")], [])
            with open(path, "w") as handle:
                handle.write("{not json")
            self.assertIsNone(read_state(path))
            with open(path, "w") as handle:
                json.dump({"version": 99}, handle)
            self.assertIsNone(read_state(path))


class PollIntervalTest(unittest.TestCase):
    cfg = parse_config({})

    def test_rhythm(self):
        noon = utc(2026, 10, 30, 11)
        self.assertEqual(poll_interval(make_game(GameStatus.LIVE), noon, self.cfg, BERLIN), 60)
        self.assertEqual(poll_interval(make_game(GameStatus.SCHEDULED), noon, self.cfg, BERLIN), 60)
        self.assertEqual(poll_interval(make_game(GameStatus.FINISHED), noon, self.cfg, BERLIN), 900)
        self.assertEqual(poll_interval(None, noon, self.cfg, BERLIN), 900)

    def test_wakes_up_right_after_local_midnight(self):
        # 23:59:30 Berlin (CET) = 22:59:30 UTC -> 30 s to midnight + 5 s margin
        self.assertAlmostEqual(poll_interval(None, utc(2026, 10, 30, 22, 59, 30), self.cfg, BERLIN), 35, places=3)
        self.assertEqual(poll_interval(make_game(GameStatus.LIVE), utc(2026, 10, 30, 22, 59, 50), self.cfg, BERLIN), 15)

    def test_midnight_on_dst_change_day(self):
        # 25.10.2026 has 25 hours; 22:00 UTC (23:00 CET) -> one hour to midnight, capped by the idle poll
        self.assertEqual(poll_interval(None, utc(2026, 10, 25, 21, 30), self.cfg, BERLIN), 900)


class ChooseViewTest(unittest.TestCase):
    def test_rules(self):
        now = utc(2026, 10, 30, 21)  # 22:00 Berlin
        stale = 2000
        game_state = State(now, "p", "1", make_game(GameStatus.FINISHED, home_score=3, away_score=1), make_table())
        self.assertEqual(choose_view(game_state, now, BERLIN, stale).kind, "game")
        # after local midnight the same finished game gives way to the table
        after_midnight = utc(2026, 10, 30, 23, 0, 1)
        self.assertEqual(choose_view(game_state, after_midnight, BERLIN, stale).kind, "table")
        # game of another day -> table
        other = State(now, "p", "1", make_game(start=utc(2026, 10, 31, 18)), make_table())
        self.assertEqual(choose_view(other, now, BERLIN, stale).kind, "table")
        # cancelled game is never shown
        cancelled = State(now, "p", "1", make_game(GameStatus.CANCELLED), make_table())
        self.assertEqual(choose_view(cancelled, now, BERLIN, stale).kind, "table")
        self.assertEqual(choose_view(State(now, "p", "1"), now, BERLIN, stale).kind, "message")
        self.assertEqual(choose_view(None, now, BERLIN, stale).kind, "message")

    def test_stale_flag(self):
        now = utc(2026, 10, 30, 12)
        fresh = State(now, "p", "1", None, make_table())
        self.assertFalse(choose_view(fresh, now, BERLIN, 1800).stale)
        self.assertTrue(choose_view(fresh, now + timedelta(seconds=1801), BERLIN, 1800).stale)
        failed = State(now, "p", "1", None, make_table(), error="boom")
        self.assertTrue(choose_view(failed, now, BERLIN, 1800).stale)


class FakeProvider:
    def __init__(self):
        self.game = None
        self.table = make_table()
        self.fail_game = False
        self.fail_table = False
        self.calls = []

    def get_game_of_day(self, team_id, now):
        self.calls.append("game")
        if self.fail_game:
            raise ProviderError("game source down")
        return self.game

    def get_table(self):
        self.calls.append("table")
        if self.fail_table:
            raise ProviderError("table source down")
        return self.table


class Env:
    """Temporary project folder with a config file."""

    def __init__(self, **cfg):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "config"))
        self.config_path = os.path.join(self.root, "config", "config.json")
        self.write_config(favorite_team_id="1", fonts_dir=os.path.join(ROOT, "fonts"), **cfg)

    def write_config(self, **cfg):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle)
        future = os.path.getmtime(self.config_path) + 5  # make sure the mtime differs
        os.utime(self.config_path, (future, future))

    def close(self):
        self._tmp.cleanup()


class DataServiceTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.provider = FakeProvider()
        self.now = [utc(2026, 10, 30, 11)]
        self.service = DataService(self.env.config_path, root=self.env.root, http=None,
                                   now_func=lambda: self.now[0], provider_factory=lambda *a, **k: self.provider)

    def tearDown(self):
        self.env.close()

    def state(self):
        return read_state(os.path.join(self.env.root, "data", "state.json"))

    def test_game_day_polls_every_minute_and_keeps_table(self):
        self.provider.game = make_game(GameStatus.SCHEDULED)
        self.assertEqual(self.service.cycle(), 60)
        first = self.state()
        self.assertEqual(first.game.status, GameStatus.SCHEDULED)
        self.assertEqual([r.points for r in first.table], [28, 25])
        self.assertEqual(self.provider.calls.count("table"), 1)
        # second cycle during the game: table is not fetched again
        self.provider.game = make_game(GameStatus.LIVE, home_score=1, away_score=0)
        self.service.cycle()
        self.assertEqual(self.provider.calls.count("table"), 1)
        self.assertEqual(self.state().game.home_score, 1)

    def test_no_game_polls_every_15_minutes_and_fetches_table(self):
        self.assertEqual(self.service.cycle(), 900)
        state = self.state()
        self.assertIsNone(state.game)
        self.assertEqual(len(state.table), 2)
        self.assertIsNone(state.error)

    def test_error_keeps_last_good_data_and_marks_it(self):
        self.provider.game = make_game(GameStatus.LIVE, home_score=2, away_score=2)
        self.service.cycle()
        self.provider.fail_game = True
        self.assertEqual(self.service.cycle(), 60)
        state = self.state()
        self.assertEqual(state.game.home_score, 2)
        self.assertIn("game source down", state.error)
        self.assertEqual(state.updated_at, utc(2026, 10, 30, 11))
        self.assertIsNotNone(state.error_since)
        # recovery clears the error
        self.provider.fail_game = False
        self.now[0] = utc(2026, 10, 30, 11, 5)
        self.service.cycle()
        self.assertIsNone(self.state().error)

    def test_error_without_previous_data(self):
        self.provider.fail_game = True
        self.service.cycle()
        state = self.state()
        self.assertEqual(state.table, [])
        self.assertIsNone(state.updated_at)
        self.assertIsNotNone(state.error)

    def test_table_failure_during_game_keeps_old_table(self):
        self.service.cycle()  # no game: table fetched
        self.provider.game = make_game(GameStatus.FINISHED, home_score=3, away_score=1)
        self.provider.fail_table = True
        self.service.cycle()
        state = self.state()
        self.assertEqual(state.game.status, GameStatus.FINISHED)
        self.assertEqual(len(state.table), 2)
        self.assertIsNone(state.error)

    def test_changed_favorite_discards_carried_data(self):
        self.provider.game = make_game(GameStatus.LIVE, home_score=1, away_score=0)
        self.service.cycle()
        self.env.write_config(favorite_team_id="2", fonts_dir=os.path.join(ROOT, "fonts"))
        self.provider.game = None
        self.service.cycle()
        self.assertEqual(self.state().favorite_team_id, "2")
        self.assertIsNone(self.state().game)

    def test_poll_rhythm_comes_from_config(self):
        self.env.write_config(favorite_team_id="1", poll_game_seconds=30, poll_idle_seconds=120)
        self.provider.game = make_game(GameStatus.LIVE)
        self.assertEqual(self.service.cycle(), 30)
        self.provider.game = None
        self.assertEqual(self.service.cycle(), 120)

    def test_no_favorite_only_fetches_the_table(self):
        self.env.write_config(favorite_team_id="")
        self.service.cycle()
        self.assertNotIn("game", self.provider.calls)
        self.assertEqual(len(self.state().table), 2)


class FakeDisplay:
    def __init__(self, cfg=None):
        self.shown = []
        self.cleared = False

    def show(self, image):
        self.shown.append(image.copy())

    def clear(self):
        self.cleared = True


class DisplayServiceTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.now = [utc(2026, 10, 30, 12)]
        self.display = FakeDisplay()
        self.service = DisplayService(self.env.config_path, display=self.display, root=self.env.root,
                                      now_func=lambda: self.now[0])
        self.state_path = os.path.join(self.env.root, "data", "state.json")

    def tearDown(self):
        self.env.close()

    def put_state(self, **kw):
        base = dict(updated_at=self.now[0], provider="p", favorite_team_id="1", table=make_table())
        base.update(kw)
        write_state(self.state_path, State(**base))
        future = os.path.getmtime(self.state_path) + len(self.display.shown) + 1
        os.utime(self.state_path, (future, future))

    def test_starts_with_message_then_shows_state_only_on_change(self):
        self.assertTrue(self.service.step())           # no state file yet -> "STARTE"
        self.assertFalse(self.service.step())          # nothing changed -> no redraw
        self.put_state(game=make_game(GameStatus.SCHEDULED))
        self.assertTrue(self.service.step())
        self.assertFalse(self.service.step())
        self.assertEqual(len(self.display.shown), 2)

    def test_midnight_switches_from_final_score_to_table(self):
        self.now[0] = utc(2026, 10, 30, 21)  # 22:00 Berlin
        self.put_state(game=make_game(GameStatus.FINISHED, home_score=3, away_score=1), updated_at=self.now[0])
        self.assertTrue(self.service.step())
        final_frame = self.display.shown[-1].tobytes()
        self.now[0] = utc(2026, 10, 30, 22, 59)      # still 23:59 Berlin
        # the data service keeps refreshing the state, so it is not stale shortly before midnight
        self.put_state(game=make_game(GameStatus.FINISHED, home_score=3, away_score=1), updated_at=self.now[0])
        self.assertFalse(self.service.step())
        self.now[0] = utc(2026, 10, 30, 23, 0, 5)    # after local midnight
        self.assertTrue(self.service.step())
        self.assertNotEqual(self.display.shown[-1].tobytes(), final_frame)

    def test_stale_dot_appears_on_error(self):
        self.put_state()
        self.service.step()
        clean = self.display.shown[-1]
        self.put_state(error="boom")
        self.assertTrue(self.service.step())
        marked = self.display.shown[-1]
        self.assertEqual(marked.getpixel((63, 0)), (140, 0, 0))
        self.assertNotEqual(clean.getpixel((63, 0)), (140, 0, 0))

    def test_config_change_applies_and_matrix_change_requests_restart(self):
        self.put_state()
        self.service.step()
        self.env.write_config(favorite_team_id="1", fonts_dir=os.path.join(ROOT, "fonts"), show_team_names=False)
        self.assertFalse(self.service.cfg.show_team_names is False)
        self.service.step()
        self.assertFalse(self.service.cfg.show_team_names)
        self.env.write_config(favorite_team_id="1", fonts_dir=os.path.join(ROOT, "fonts"), matrix={"brightness": 30})
        with self.assertRaises(RestartRequired):
            self.service.step()


if __name__ == "__main__":
    unittest.main()
