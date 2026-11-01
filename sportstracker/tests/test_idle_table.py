"""Option "Livingmodus ohne Tabelle" (0.5.6): the table is left out of the idle rotation."""

import unittest

from sportstracker.config import ConfigError, parse_config
from sportstracker.models import GameStatus
from sportstracker.settings import apply_form, config_to_values
from sportstracker.views import plan

from test_plan import BERLIN, NOW, STALE, cfg as plan_cfg, game, mk_state
from test_web_050 import Web050Base


def kinds(p):
    return [i.kind for i in p.items]


class PlanTests(unittest.TestCase):
    def test_default_keeps_the_table(self):
        self.assertEqual(kinds(plan(mk_state([]), NOW, plan_cfg(), BERLIN, STALE)), ["table", "room"])

    def test_hidden_table_leaves_the_room_view(self):
        self.assertEqual(kinds(plan(mk_state([]), NOW, plan_cfg(idle_hide_table=True), BERLIN, STALE)), ["room"])

    def test_table_only_mode_falls_back_to_the_room_view(self):
        cfg = plan_cfg(idle_hide_table=True, idle_mode="table")
        self.assertEqual(kinds(plan(mk_state([]), NOW, cfg, BERLIN, STALE)), ["room"])

    def test_room_mode_unchanged(self):
        cfg = plan_cfg(idle_hide_table=True, idle_mode="room")
        self.assertEqual(kinds(plan(mk_state([]), NOW, cfg, BERLIN, STALE)), ["room"])

    def test_kickoff_notice_and_message_stay(self):
        from datetime import timedelta
        later = game("a", "Alpha", "Bravo", GameStatus.SCHEDULED, start=NOW + timedelta(hours=2))
        cfg = plan_cfg(idle_hide_table=True, message={"enabled": True, "text": "Hi"})
        self.assertEqual(kinds(plan(mk_state([later]), NOW, cfg, BERLIN, STALE)), ["game", "room", "banner"])

    def test_game_days_are_not_affected(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        self.assertEqual(kinds(plan(live, NOW, plan_cfg(idle_hide_table=True), BERLIN, STALE)), ["game"])
        final = mk_state([game("a", "Alpha", "Bravo", GameStatus.FINISHED, home_score=80, away_score=70)])
        # on a game day the table rotates with the results, whatever "ohne Tabelle" says
        self.assertEqual(kinds(plan(final, NOW, plan_cfg(idle_hide_table=True), BERLIN, STALE)), ["game", "table", "room"])
        self.assertEqual(kinds(plan(final, NOW, plan_cfg(idle_hide_table=True, gameday_results=False), BERLIN, STALE)), ["game"])


class ConfigTests(unittest.TestCase):
    def test_parse(self):
        self.assertFalse(parse_config({}).idle_hide_table)
        self.assertTrue(parse_config({"idle_hide_table": True}).idle_hide_table)
        with self.assertRaises(ConfigError):
            parse_config({"idle_hide_table": "ja"})

    def test_values_roundtrip(self):
        self.assertTrue(config_to_values(parse_config({"idle_hide_table": True}))["idle_hide_table"])


class WebTests(Web050Base):
    def test_checkbox_is_saved(self):
        self.assertIn('name="idle_hide_table"', self.client.get("/settings").get_data(as_text=True))
        self.post("/settings", page="/settings", **self.settings_form(idle_hide_table="on"))
        self.assertTrue(self.config()["idle_hide_table"])
        self.post("/settings", page="/settings", **self.settings_form())
        self.assertFalse(self.config()["idle_hide_table"])


if __name__ == "__main__":
    unittest.main()
