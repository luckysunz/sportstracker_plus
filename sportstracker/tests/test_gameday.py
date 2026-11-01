"""Game day rotation (0.6.2): every finished game of today and the table take turns until midnight."""

import unittest
from datetime import timedelta

from sportstracker.config import ConfigError, parse_config
from sportstracker.models import GameStatus
from sportstracker.settings import apply_form, config_to_values
from sportstracker.state import GameMarks
from sportstracker.views import plan

from test_plan import BERLIN, NOW, STALE, cfg as plan_cfg, game, mk_state, utc
from test_web_050 import Web050Base


def final(gid, home, away, hs=70, as_=60, hours_ago=3, **kw):
    return game(gid, home, away, GameStatus.FINISHED, start=NOW - timedelta(hours=hours_ago), home_score=hs,
                away_score=as_, **kw)


def rows(p):
    return [(i.kind, i.game.game_id if i.game else None, i.role) for i in p.items]


class GamedayPlanTests(unittest.TestCase):
    def test_all_results_and_the_table_rotate_when_nothing_runs(self):
        games = [final("a", "Charlie", "Delta"), final("b", "Echo", "Foxtrot"), final("fav", "Alpha", "Bravo")]
        p = plan(mk_state(games), NOW, plan_cfg(), BERLIN, STALE)
        self.assertEqual(rows(p), [("game", "fav", "card"), ("game", "a", "card"), ("game", "b", "card"),
                                   ("table", None, "idle"), ("room", None, "idle")])
        self.assertIn("Spieltag", p.reason)

    def test_results_stay_long_after_the_old_30_minute_window(self):
        games = [final("a", "Charlie", "Delta")]
        marks = {"a": GameMarks(finished_seen_at=NOW - timedelta(hours=3))}
        p = plan(mk_state(games, marks), NOW, plan_cfg(), BERLIN, STALE)
        self.assertIn(("game", "a", "card"), rows(p))
        old = plan(mk_state(games, marks), NOW, plan_cfg(gameday_results=False), BERLIN, STALE)
        self.assertNotIn("card", [r[2] for r in rows(old)])

    def test_until_midnight_then_back_to_table_and_room(self):
        games = [final("a", "Charlie", "Delta")]
        later = utc(2026, 10, 31, 23, 0, 5)                       # 00:00:05 Berlin on 1 Nov
        p = plan(mk_state(games, now=later), later, plan_cfg(), BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)], ["table", "room"])

    def test_running_game_keeps_the_foreground_and_results_rotate_with_it(self):
        games = [game("live", "Echo", "Foxtrot", start=NOW - timedelta(minutes=30)), final("a", "Charlie", "Delta"),
                 final("fav", "Alpha", "Bravo")]
        p = plan(mk_state(games), NOW, plan_cfg(), BERLIN, STALE)
        self.assertEqual(rows(p), [("game", "live", "main"), ("game", "fav", "card"), ("game", "a", "card")])
        self.assertNotIn("table", [r[0] for r in rows(p)])            # the table only comes when nothing runs

    def test_kickoff_notice_for_a_later_favorite_game(self):
        games = [final("a", "Charlie", "Delta"),
                 game("fav", "Alpha", "Bravo", GameStatus.SCHEDULED, start=NOW + timedelta(hours=3))]
        p = plan(mk_state(games), NOW, plan_cfg(), BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)], ["game", "game", "table", "room"])
        self.assertEqual(rows(p)[1], ("game", "fav", "idle"))

    def test_idle_mode_table_only_has_no_room(self):
        p = plan(mk_state([final("a", "Charlie", "Delta")]), NOW, plan_cfg(idle_mode="table"), BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)], ["game", "table"])

    def test_games_of_other_days_and_unscored_games_are_no_cards(self):
        old = final("old", "Charlie", "Delta", hours_ago=30)
        unscored = game("u", "Echo", "Foxtrot", GameStatus.FINISHED, start=NOW - timedelta(hours=3))
        p = plan(mk_state([old, unscored]), NOW, plan_cfg(), BERLIN, STALE)
        self.assertNotIn("card", [r[2] for r in rows(p)])

    def test_only_the_favorite_when_other_games_are_off(self):
        games = [final("a", "Charlie", "Delta"), final("fav", "Alpha", "Bravo")]
        p = plan(mk_state(games), NOW, plan_cfg(show_other_games=False), BERLIN, STALE)
        self.assertEqual([r[1] for r in rows(p) if r[0] == "game"], ["fav"])
        self.assertIn("table", [r[0] for r in rows(p)])

    def test_without_a_table_only_results(self):
        p = plan(mk_state([final("a", "Charlie", "Delta")], tbl=[]), NOW, plan_cfg(idle_mode="table"), BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)], ["game"])

    def test_message_and_picture_join_the_rotation(self):
        cfg = plan_cfg(message={"enabled": True, "text": "Hi"})
        p = plan(mk_state([final("a", "Charlie", "Delta")]), NOW, cfg, BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)][-1], "banner")

    def test_provider_without_list_uses_the_favorite_game(self):
        from sportstracker.state import State
        fav = final("fav", "Alpha", "Bravo")
        state = State(updated_at=NOW, provider="p", favorite_team_id="1", game=fav, table=mk_state([]).table)
        p = plan(state, NOW, plan_cfg(), BERLIN, STALE)
        self.assertEqual([r[0] for r in rows(p)], ["game", "table", "room"])


class GamedayConfigTests(Web050Base):
    def test_config(self):
        self.assertTrue(parse_config({}).gameday_results)
        self.assertFalse(parse_config({"gameday_results": False}).gameday_results)
        with self.assertRaises(ConfigError):
            parse_config({"gameday_results": "ja"})
        self.assertTrue(config_to_values(parse_config({}))["gameday_results"])

    def test_checkbox(self):
        self.assertIn('name="gameday_results"', self.client.get("/settings").get_data(as_text=True))
        self.post("/settings", page="/settings", **self.settings_form(gameday_results="on"))
        self.assertTrue(self.config()["gameday_results"])
        self.post("/settings", page="/settings", **self.settings_form())
        self.assertFalse(self.config()["gameday_results"])


if __name__ == "__main__":
    unittest.main()
