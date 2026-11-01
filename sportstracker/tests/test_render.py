import os
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sportstracker.demo import DemoLogos, demo_games, demo_table
from sportstracker.models import Game, GameStatus, PeriodScore, TableRow, Team
from sportstracker.render import (Renderer, draw_score, fit_text, team_city, Theme, info_line, load_fonts, period_chain, score_text, short_period,
                                  table_window, team_label)

BERLIN = ZoneInfo("Europe/Berlin")
FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def game(**kw):
    base = dict(game_id="1", home=Team("1", "A"), away=Team("2", "B"),
                start=datetime(2026, 10, 30, 18, 30, tzinfo=timezone.utc), status=GameStatus.LIVE)
    base.update(kw)
    return Game(**base)


class TextHelpersTest(unittest.TestCase):
    def test_team_label(self):
        self.assertEqual(team_label(Team("1", "Eisbären Berlin", "EBB")), "EBB")
        names = {
            "RSB Thuringia Bulls": "THU", "Hannover United": "HAN", "Rhine River Rhinos": "RHI",
            "RSV Lahn-Dill": "LAH", "Spinto Münsterland": "SPI", "RBC Köln 99ers": "KÖL",
            "RB Zwickau e.V.": "ZWI", "Doneck Dolphins Trier": "DON", "RBB München Iguanas": "MÜN",
        }
        for name, expected in names.items():
            self.assertEqual(team_label(Team("x", name)), expected, name)
        self.assertEqual(team_label(Team("36365", "RB Zwickau e.V."), {"36365": "RBZ"}), "RBZ")

    def test_team_city(self):
        expected = {
            "Eisbären Berlin": "Berlin", "Adler Mannheim": "Mannheim", "Augsburger Panther": "Augsburg",
            "ERC Ingolstadt": "Ingolstadt", "Grizzlys Wolfsburg": "Wolfsburg", "Iserlohn Roosters": "Iserlohn",
            "Kölner Haie": "Köln", "Krefeld Pinguine": "Krefeld", "Löwen Frankfurt": "Frankfurt",
            "Nürnberg Ice Tigers": "Nürnberg", "Pinguins Bremerhaven": "Bremerhaven", "Red Bull München": "München",
            "Schwenninger Wild Wings": "Schwenningen", "Straubing Tigers": "Straubing",
            "RSB Thuringia Bulls": "Thuringia", "Hannover United": "Hannover", "RSV Lahn-Dill": "Lahn-Dill",
            "RBC Köln 99ers": "Köln", "RB Zwickau e.V.": "Zwickau", "RBB München Iguanas": "München",
        }
        for name, city in expected.items():
            self.assertEqual(team_city(Team("x", name)), city, name)
        self.assertEqual(team_city(Team("36366", "Doneck Dolphins Trier"), {"36366": "Trier"}), "Trier")
        self.assertEqual(team_city(Team("x", "Bulls")), "Bulls")  # nothing left -> full name

    def test_short_period(self):
        self.assertEqual(short_period("2.Drittel"), "2.Dr")
        self.assertEqual(short_period("Overtime"), "OT")
        self.assertEqual(short_period("Penalty"), "PEN")
        self.assertEqual(short_period("Q3"), "Q3")
        self.assertEqual(short_period(None), "")

    def test_period_chain_drops_oldest_when_too_long(self):
        g = game(periods=(PeriodScore("1", 0, 1), PeriodScore("2", 1, 3), PeriodScore("3", 1, 4), PeriodScore("OT", 2, 4)))
        self.assertEqual(period_chain(g, 16), "0:1 1:3 1:4 2:4")
        self.assertEqual(period_chain(g, 11), "1:3 1:4 2:4")

    def test_score_text(self):
        self.assertEqual(score_text(game(status=GameStatus.SCHEDULED), BERLIN), "19:30")
        self.assertEqual(score_text(game(home_score=3, away_score=1), BERLIN), "3:1")
        self.assertEqual(score_text(game(home_score=None, away_score=None), BERLIN), "-:-")

    def test_info_line(self):
        self.assertEqual(info_line(game(period_label="Q2", clock="07:32"), True, 16), ("Q2 07:32", "live"))
        self.assertEqual(info_line(game(period_label="Q2", clock="07:32", period_break=True), True, 16), ("Q2 PAUSE", "live"))
        periods = (PeriodScore("1.Drittel", 0, 1), PeriodScore("2.Drittel", 1, 3))
        self.assertEqual(info_line(game(period_label="2.Drittel", periods=periods), True, 16), ("0:1 1:3", "live"))
        # clock hidden by the setting -> falls back to period scores / period
        self.assertEqual(info_line(game(period_label="Q2", clock="07:32"), False, 16), ("Q2", "live"))
        self.assertEqual(info_line(game(status=GameStatus.FINISHED), True, 16), ("ENDE", "final"))
        self.assertEqual(info_line(game(status=GameStatus.FINISHED, decided_in="OT"), True, 16), ("ENDE n.V.", "final"))
        self.assertEqual(info_line(game(status=GameStatus.UNKNOWN, decided_in="SO"), True, 16), ("ENDE n.P.", "final"))
        self.assertEqual(info_line(game(status=GameStatus.SCHEDULED), True, 16), ("HEUTE", "info"))

    def test_table_window(self):
        rows = [TableRow(rank=i + 1, team=Team(str(i), "T%d" % i)) for i in range(14)]
        ids = lambda fav: [r.rank for r in table_window(rows, str(fav), 9)]
        self.assertEqual(ids(0), list(range(1, 10)))
        self.assertEqual(ids(13), list(range(6, 15)))
        self.assertEqual(ids(7), list(range(4, 13)))
        self.assertEqual(len(table_window(rows[:5], "2", 9)), 5)
        self.assertEqual(ids(99), list(range(1, 10)))  # unknown favorite: top of the table


@unittest.skipUnless(os.path.isdir(FONTS_DIR), "fonts folder missing")
class RendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fonts = load_fonts(FONTS_DIR)

    def renderer(self, w=64, h=64, **kw):
        return Renderer(w, h, self.fonts, DemoLogos(self.fonts), BERLIN, **kw)

    def lit(self, image):
        pixels = image.load()
        w, h = image.size
        return sum(1 for y in range(h) for x in range(w) if pixels[x, y] != (0, 0, 0))

    def test_game_frames_have_matrix_size_and_content(self):
        r = self.renderer()
        for _, g in demo_games():
            image = r.render_game(g)
            self.assertEqual(image.size, (64, 64))
            self.assertGreater(self.lit(image), 200)

    def test_missing_logo_uses_fallback(self):
        r = Renderer(64, 64, self.fonts, DemoLogos(self.fonts, missing=("1", "2")), BERLIN)
        self.assertGreater(self.lit(r.render_game(game(home_score=1, away_score=0))), 100)

    def test_no_logo_provider_is_allowed(self):
        r = Renderer(64, 64, self.fonts, None, BERLIN)
        self.assertEqual(r.render_game(game(home_score=1, away_score=0)).size, (64, 64))

    def test_team_names_switch_changes_frame(self):
        g = game(home_score=1, away_score=0)
        with_names = self.renderer(show_team_names=True).render_game(g)
        without = self.renderer(show_team_names=False).render_game(g)
        self.assertNotEqual(with_names.tobytes(), without.tobytes())

    def test_clock_switch_changes_frame(self):
        g = game(home_score=1, away_score=0, period_label="Q2", clock="07:32")
        on = self.renderer(show_game_clock=True).render_game(g)
        off = self.renderer(show_game_clock=False).render_game(g)
        self.assertNotEqual(on.tobytes(), off.tobytes())

    def test_table_highlights_favorite(self):
        theme = Theme()
        image = self.renderer().render_table(demo_table(1), "641")
        # favorite is rank 1 -> first row: highlight bar drawn at the left edge of that row
        self.assertEqual(image.getpixel((0, 3)), theme.favorite_bar)
        self.assertEqual(image.getpixel((0, 3 + 7)), theme.background)

    def test_table_with_small_logos(self):
        r = self.renderer(table_logo_size=8)  # row height 9 -> 7 rows
        image = r.render_table(demo_table(1), "641")
        self.assertEqual(image.size, (64, 64))
        theme = Theme()
        # favorite row 0: logo disc in the logo column is neither bar colour nor background
        self.assertNotIn(image.getpixel((16, 5)), (theme.favorite_bar, theme.background))
        # 7th row (index 6) is drawn, an 8th would not fit
        self.assertNotEqual(image.getpixel((16, 6 * 9 + 5)), theme.background)
        self.assertEqual(image.getpixel((16, 63)), theme.background)

    def test_score_colon_is_two_square_dots(self):
        from PIL import Image

        image = Image.new("RGB", (40, 14))
        draw_score(image, self.fonts.big, 20, 2, "1:4", (255, 255, 0))
        lit = (255, 255, 0)
        for x in (19, 20):
            for y in (4, 5, 7, 8):
                self.assertEqual(image.getpixel((x, y)), lit, (x, y))
            for y in (2, 3, 6, 9, 10, 11):
                self.assertEqual(image.getpixel((x, y)), (0, 0, 0), (x, y))
        for x in (17, 18, 21, 22):  # clear gap to the digits
            for y in range(14):
                self.assertEqual(image.getpixel((x, y)), (0, 0, 0), (x, y))

    def test_table_logos_are_the_default_for_the_example_config(self):
        import json

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("config.example.json", "config.rbbl.example.json"):
            with open(os.path.join(root, "config", name), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["table_logo_size"], 6)

    def test_fit_text_shortens_with_dot(self):
        font = self.fonts.small  # 4 px per character
        self.assertEqual(fit_text(font, "Mannheim", 32), "Mannheim")
        self.assertEqual(fit_text(font, "Bremerhaven", 32), "Bremerh.")
        self.assertEqual(fit_text(font, "Schwenningen", 20), "Schw.")
        self.assertEqual(fit_text(font, "X", 2), "")

    def test_table_label_city_or_short(self):
        city = self.renderer(table_logo_size=6, table_label="city").render_table(demo_table(1), "641")
        short = self.renderer(table_logo_size=6, table_label="short").render_table(demo_table(1), "641")
        self.assertNotEqual(city.tobytes(), short.tobytes())
        self.assertEqual(city.size, (64, 64))

    def test_empty_table_gives_message(self):
        image = self.renderer().render_table([], "1")
        self.assertGreater(self.lit(image), 20)

    def test_other_panel_sizes_do_not_crash(self):
        g = demo_games()[0][1]
        for w, h in ((128, 64), (32, 32), (64, 32), (128, 128)):
            r = self.renderer(w, h)
            self.assertEqual(r.render_game(g).size, (w, h))
            self.assertEqual(r.render_table(demo_table(9), "641").size, (w, h))


if __name__ == "__main__":
    unittest.main()
