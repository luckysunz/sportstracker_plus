"""Message mode: configuration, text layout, the six animations, plan integration, display service and web page."""

import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageChops

from sportstracker import banner
from sportstracker.banner import BannerRenderer
from sportstracker.config import DEFAULT_MESSAGE, MESSAGE_ANIMATIONS, MESSAGE_FONTS, MESSAGE_SPEEDS, parse_config
from sportstracker.errors import ConfigError
from sportstracker.settings import apply_message_form
from sportstracker.views import Item, Plan, plan, render_view, view_for_item

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FONTS = os.path.join(ROOT, "fonts")


def msg(**kwargs):
    message = dict(DEFAULT_MESSAGE)
    message.update(enabled=True, text="Hallo Welt", font="6x13B", color="#FFB000")
    message.update(kwargs)
    return message


def lit(image):
    return sum(1 for p in image.getdata() if p != (0, 0, 0))


def same(a, b):
    return ImageChops.difference(a, b).getbbox() is None


class ConfigTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        cfg = parse_config({})
        self.assertEqual(cfg.message, DEFAULT_MESSAGE)
        self.assertFalse(cfg.message["enabled"])

    def test_partial_message_is_completed(self):
        cfg = parse_config({"message": {"enabled": True, "text": "Hi"}})
        self.assertEqual(cfg.message["font"], "6x13B")
        self.assertEqual(cfg.message["seconds"], 10)

    def test_rejects_bad_values(self):
        for bad in ({"color": "red"}, {"color": "#12345"}, {"font": "comic"}, {"animation": "spin"},
                    {"speed": "warp"}, {"intensity": 0}, {"intensity": 101}, {"seconds": 2},
                    {"enabled": "yes"}, {"text": 5}, {"text": "x" * 201}, {"text": "a\x01b"}):
            with self.assertRaises(ConfigError, msg=str(bad)):
                parse_config({"message": bad})

    def test_lists_match_the_banner_module(self):
        self.assertEqual(MESSAGE_FONTS, banner.FONT_KEYS)
        self.assertEqual(MESSAGE_ANIMATIONS, tuple(key for key, _ in banner.ANIMATIONS))
        self.assertEqual(MESSAGE_SPEEDS, tuple(key for key, _ in banner.SPEED_LABELS))
        self.assertEqual(len(banner.ANIMATIONS), 7)          # standing text and six animations

    def test_every_font_file_exists_and_loads(self):
        renderer = BannerRenderer(FONTS, 64, 64)
        for choice in banner.FONT_CHOICES:
            self.assertTrue(os.path.exists(os.path.join(FONTS, choice.filename)), choice.filename)
            font = renderer.font(choice.key)
            self.assertGreater(font.height, 0)
            self.assertEqual(font.text_width("W"), choice.advance if choice.key != "tom-thumb" else font.text_width("W"))


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.r = BannerRenderer(FONTS, 64, 64)

    def test_wrap_keeps_words_and_breaks_long_ones(self):
        font = self.r.font("6x13B")
        lines = banner.wrap_lines(font, "Hallo Welt wie geht es", 64)
        self.assertTrue(all(font.text_width(line) <= 64 for line in lines))
        self.assertEqual(" ".join(lines), "Hallo Welt wie geht es")
        long_lines = banner.wrap_lines(font, "Donaudampfschifffahrt", 64)
        self.assertGreater(len(long_lines), 1)
        self.assertEqual("".join(long_lines), "Donaudampfschifffahrt")

    def test_explicit_line_breaks(self):
        font = self.r.font("5x7")
        self.assertEqual(banner.wrap_lines(font, "A\nB", 64), ["A", "B"])

    def test_text_is_centred(self):
        image = self.r.frame(msg(text="Hi"), 0)
        box = ImageChops.invert(image.convert("L")).getbbox()
        self.assertIsNotNone(image.getbbox())
        left, top, right, bottom = image.getbbox()
        self.assertLess(abs((left + right) / 2.0 - 32), 4)
        self.assertLess(abs((top + bottom) / 2.0 - 32), 8)
        del box

    def test_too_much_text_falls_back_to_a_smaller_font(self):
        text = "Dies ist ein ziemlich langer Text der nicht in der grossen Schrift auf die Matrix passt"
        layout = self.r.layout(text, "10x20", False)
        self.assertNotEqual(layout.font_key, "10x20")
        top = min(cell.y for cell in layout.cells)
        bottom = max(cell.y + layout.font_height for cell in layout.cells)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(bottom, 64)

    def test_missing_font_file_falls_back(self):
        renderer = BannerRenderer(FONTS, 64, 64)
        renderer._fonts.clear()
        self.assertGreater(renderer.font("nonexistent").height, 0)

    def test_umlauts_render(self):
        image = self.r.frame(msg(text="ÄÖÜ äöü ß"), 0)
        self.assertGreater(lit(image), 20)

    def test_empty_text_is_black(self):
        self.assertEqual(lit(self.r.frame(msg(text="  "), 0)), 0)


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.r = BannerRenderer(FONTS, 64, 64)

    def test_none_is_static(self):
        a, b = self.r.frame(msg(), 0.0), self.r.frame(msg(), 5.3)
        self.assertTrue(same(a, b))
        self.assertGreater(lit(a), 30)
        self.assertFalse(banner.is_animated(msg()))

    def test_colour_and_intensity(self):
        full = self.r.frame(msg(color="#FF0000"), 0)
        half = self.r.frame(msg(color="#FF0000", intensity=50), 0)
        colours = {p for p in full.getdata() if p != (0, 0, 0)}
        self.assertEqual(colours, {(255, 0, 0)})
        self.assertEqual({p for p in half.getdata() if p != (0, 0, 0)}, {(128, 0, 0)})
        self.assertEqual(lit(full), lit(half))

    def test_blink_switches_off(self):
        m = msg(animation="blink")
        self.assertGreater(lit(self.r.frame(m, 0.0)), 0)
        self.assertEqual(lit(self.r.frame(m, 0.6)), 0)
        self.assertGreater(lit(self.r.frame(m, 1.1)), 0)

    def test_blink_speed(self):
        slow, fast = msg(animation="blink", speed="slow"), msg(animation="blink", speed="fast")
        self.assertGreater(lit(self.r.frame(slow, 0.6)), 0)           # still on at 0.6 s when slow
        self.assertEqual(lit(self.r.frame(fast, 0.3)), 0)             # already off when fast

    def test_pulse_changes_brightness(self):
        m = msg(animation="pulse", color="#FFFFFF")
        def peak(t):
            return max(max(p) for p in self.r.frame(m, t).getdata())
        self.assertLess(peak(0.0), 60)
        self.assertGreater(peak(1.0), 240)

    def test_rainbow_has_many_colours_and_moves(self):
        m = msg(animation="rainbow", text="REGENBOGEN", font="6x13B")
        a, b = self.r.frame(m, 0.0), self.r.frame(m, 1.0)
        self.assertGreater(len({p for p in a.getdata() if p != (0, 0, 0)}), 8)
        self.assertFalse(same(a, b))

    def test_wave_moves_letters(self):
        m = msg(animation="wave", text="WELLE")
        self.assertFalse(same(self.r.frame(m, 0.0), self.r.frame(m, 0.3)))
        self.assertEqual(lit(self.r.frame(m, 0.0)), lit(self.r.frame(m, 0.3)))

    def test_typewriter_types_then_holds_then_restarts(self):
        m = msg(animation="typewriter", text="ABCDEF")
        counts = [lit(self.r.frame(m, t)) for t in (0.0, 0.5, 1.0, 1.9)]
        self.assertEqual(counts[0], 0)
        self.assertLess(counts[1], counts[2])
        self.assertLessEqual(counts[2], counts[3])
        full = lit(self.r.frame(m, 2.5))
        self.assertEqual(full, lit(self.r.frame(msg(text="ABCDEF"), 0)))
        cycle = 6 / banner.TYPEWRITER_CHARS_PER_SECOND + banner.TYPEWRITER_HOLD_SECONDS
        self.assertEqual(lit(self.r.frame(m, cycle + 0.05)), 0)

    def test_scroll_runs_through_the_panel(self):
        m = msg(animation="scroll", text="Hallo Welt, dies ist ein Lauftext")
        start = self.r.frame(m, 0.0)
        self.assertEqual(lit(start), 0)                                # begins outside on the right
        middle = self.r.frame(m, 4.0)
        self.assertGreater(lit(middle), 50)
        self.assertFalse(same(middle, self.r.frame(m, 4.5)))
        seconds = banner.scroll_seconds(m, 64)
        self.assertEqual(lit(self.r.frame(m, seconds - 0.001)), 0)

    def test_scroll_is_one_line_at_the_middle(self):
        image = self.r.frame(msg(animation="scroll", text="A\nB", font="6x13B"), 3.0)
        left, top, right, bottom = image.getbbox()
        self.assertGreaterEqual(top, 20)
        self.assertLessEqual(bottom, 44)

    def test_all_animations_produce_frames(self):
        for key in MESSAGE_ANIMATIONS:
            for font in ("tom-thumb", "10x20"):
                image = self.r.frame(msg(animation=key, font=font), 2.0)
                self.assertEqual(image.size, (64, 64), key)

    def test_rotation_seconds_and_cycle(self):
        self.assertEqual(banner.rotation_seconds(msg(seconds=12), 64), 12)
        long_scroll = msg(animation="scroll", text="x" * 100, seconds=10)
        self.assertGreater(banner.rotation_seconds(long_scroll, 64), 10)
        self.assertLessEqual(banner.rotation_seconds(msg(animation="scroll", text="x" * 200, speed="slow"), 64), banner.MAX_SCROLL_SECONDS)
        self.assertGreater(banner.cycle_seconds(msg(animation="typewriter"), 64), 2.5)

    def test_preview_gif(self):
        data = banner.preview_gif(self.r, msg(animation="blink"))
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.format, "GIF")
        self.assertGreater(getattr(image, "n_frames", 1), 1)
        still = Image.open(io.BytesIO(banner.preview_gif(self.r, msg())))
        self.assertEqual(getattr(still, "n_frames", 1), 1)


# -- plan, views, display service, web page ------------------------------------------------------------

from sportstracker.models import GameStatus  # noqa: E402
from sportstracker.service_display import DisplayService  # noqa: E402
from sportstracker.state import State, write_state  # noqa: E402

from test_plan import BERLIN, NOW, STALE, cfg as plan_cfg, game, mk_state, utc  # noqa: E402
from test_services import Env, FakeDisplay, make_table  # noqa: E402
from test_web import WebTestBase  # noqa: E402


def kinds_of(p):
    return [i.kind for i in p.items]


def config_with(**message):
    return plan_cfg(message=msg(**message))


class PlanTests(unittest.TestCase):
    def test_disabled_changes_nothing(self):
        state = mk_state([])
        before = plan(state, NOW, plan_cfg(), BERLIN, STALE)
        after = plan(state, NOW, plan_cfg(message=msg(enabled=False)), BERLIN, STALE)
        self.assertEqual(kinds_of(before), kinds_of(after))
        self.assertNotIn("banner", kinds_of(after))

    def test_permanent_shows_only_the_message_even_while_a_game_is_live(self):
        cfg = config_with(permanent=True)
        live = mk_state([game("a", "Alpha", "Bravo")])
        self.assertEqual(kinds_of(plan(live, NOW, cfg, BERLIN, STALE)), ["banner"])
        self.assertEqual(kinds_of(plan(None, NOW, cfg, BERLIN, STALE)), ["banner"])

    def test_rotates_in_the_idle_list(self):
        result = plan(mk_state([]), NOW, config_with(seconds=7), BERLIN, STALE)
        self.assertEqual(kinds_of(result), ["table", "room", "banner"])
        self.assertEqual(result.items[-1].seconds, 7)

    def test_not_shown_while_a_game_is_live_by_default(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        self.assertNotIn("banner", kinds_of(plan(live, NOW, config_with(), BERLIN, STALE)))

    def test_during_games_adds_it_after_the_games(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        result = plan(live, NOW, config_with(during_games=True), BERLIN, STALE)
        self.assertEqual(kinds_of(result), ["game", "banner"])

    def test_favorite_final_score_rotates_with_the_message(self):
        final = mk_state([game("a", "Alpha", "Bravo", GameStatus.FINISHED, home_score=80, away_score=70)])
        result = plan(final, NOW, config_with(), BERLIN, STALE)
        self.assertEqual(kinds_of(result), ["game", "table", "room", "banner"])      # game day: results and table
        result = plan(final, NOW, plan_cfg(message=msg(), gameday_results=False), BERLIN, STALE)
        self.assertEqual(kinds_of(result), ["game", "banner"])

    def test_empty_text_is_ignored(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        self.assertNotIn("banner", kinds_of(plan(live, NOW, config_with(text="   ", permanent=True), BERLIN, STALE)))

    def test_scroll_gets_time_for_a_full_pass(self):
        cfg = config_with(animation="scroll", text="x" * 80, seconds=5)
        item = plan(mk_state([]), NOW, cfg, BERLIN, STALE).items[-1]
        self.assertGreater(item.seconds, 20)

    def test_locate_gives_the_time_into_the_item(self):
        p = Plan((Item("table", seconds=10), Item("banner", seconds=10)))
        start = (int(NOW.timestamp()) // 20) * 20
        moment = datetime.fromtimestamp(start + 13.25, timezone.utc)
        item, elapsed = p.locate(moment)
        self.assertEqual(item.kind, "banner")
        self.assertAlmostEqual(elapsed, 3.25, places=2)
        self.assertEqual(p.item_at(moment).kind, "banner")
        single = Plan((Item("banner", seconds=10),))
        self.assertEqual(single.locate(moment)[0].kind, "banner")

    def test_view_and_render(self):
        from sportstracker.runtime import build_renderer
        cfg = config_with(animation="blink")
        view = view_for_item(Item("banner", seconds=10), None, NOW, False, None, cfg, 0.2)
        self.assertEqual(view.kind, "banner")
        renderer = build_renderer(cfg, None, BERLIN, root=ROOT)
        self.assertGreater(lit(render_view(renderer, view, "")), 0)
        off = view_for_item(Item("banner", seconds=10), None, NOW, False, None, cfg, 0.6)
        self.assertEqual(lit(render_view(renderer, off, "")), 0)


class DisplayServiceTests(unittest.TestCase):
    def make(self, **message):
        self.env = Env(message=msg(**message), sensor={"enabled": False})
        self.addCleanup(self.env.close)
        self.now = [utc(2026, 10, 30, 12, 0, 5)]
        self.display = FakeDisplay()
        self.service = DisplayService(self.env.config_path, display=self.display, root=self.env.root,
                                      now_func=lambda: self.now[0])
        path = os.path.join(self.env.root, "data", "state.json")
        write_state(path, State(updated_at=self.now[0], provider="p", favorite_team_id="1", table=make_table(),
                                games=[], marks={}))

    def test_permanent_animation_redraws_every_step_and_is_fast(self):
        self.make(permanent=True, animation="wave")
        for step in range(3):
            self.now[0] = utc(2026, 10, 30, 12, 0, 5) + timedelta(milliseconds=200 * step)
            self.assertTrue(self.service.step())
        self.assertEqual(len(self.display.shown), 3)
        self.assertAlmostEqual(self.service.wait_seconds, banner.FRAME_INTERVAL)
        self.assertNotEqual(self.display.shown[0].tobytes(), self.display.shown[2].tobytes())

    def test_static_message_is_drawn_once(self):
        self.make(permanent=True, animation="none")
        self.assertTrue(self.service.step())
        self.assertFalse(self.service.step())
        self.assertEqual(self.service.wait_seconds, 1.0)

    def test_rotation_switches_the_pace(self):
        self.make(animation="blink", seconds=10)       # idle list: table 10 s, room 10 s, message 10 s
        self.now[0] = utc(2026, 10, 30, 12, 0, 5)      # slot 5 -> table
        self.service.step()
        self.assertEqual(self.service.wait_seconds, 1.0)
        self.now[0] = utc(2026, 10, 30, 12, 0, 25)     # slot 25 -> message
        self.assertTrue(self.service.step())
        self.assertAlmostEqual(self.service.wait_seconds, banner.FRAME_INTERVAL)
        self.now[0] = utc(2026, 10, 30, 12, 0, 35)     # slot 35 -> table again
        self.service.step()
        self.assertEqual(self.service.wait_seconds, 1.0)


class WebTests(WebTestBase):
    def setUp(self):
        super().setUp()
        self.login()

    def form(self, **overrides):
        data = {"text": "Heimspiel heute!", "color": "#00ff00", "font": "7x13B", "intensity": "80",
                "animation": "wave", "speed": "fast", "seconds": "12", "enabled": "on", "action": "save"}
        data.update(overrides)
        return {k: v for k, v in data.items() if v is not None}

    def test_nav_link_and_page(self):
        page = self.client.get("/message").get_data(as_text=True)
        for text in ("Nachricht", "Farbe", "Schrift", "Intensität", "Animation", "Permanent", "Lauftext", "Regenbogen",
                     "Schreibmaschine", "10x20"):
            self.assertIn(text, page)
        self.assertIn('href="/message"', self.client.get("/").get_data(as_text=True))

    def test_requires_login(self):
        self.post("/logout")
        self.assertEqual(self.client.get("/message").status_code, 302)
        self.assertEqual(self.client.get("/message/preview.gif").status_code, 302)

    def test_save(self):
        response = self.post("/message", page="/message", **self.form(permanent="on"))
        self.assertEqual(response.status_code, 302)
        saved = self.config()["message"]
        self.assertEqual((saved["text"], saved["color"], saved["font"], saved["intensity"]),
                         ("Heimspiel heute!", "#00FF00", "7x13B", 80))
        self.assertEqual((saved["animation"], saved["speed"], saved["seconds"]), ("wave", "fast", 12))
        self.assertTrue(saved["enabled"] and saved["permanent"] and not saved["during_games"])
        page = self.client.get("/message").get_data(as_text=True)
        self.assertIn("Heimspiel heute!", page)

    def test_other_settings_are_kept(self):
        before = self.config()
        self.post("/message", page="/message", **self.form())
        after = self.config()
        for key in before:
            if key != "message":
                self.assertEqual(before[key], after[key], key)

    def test_invalid_input_is_rejected_and_not_saved(self):
        for change in ({"color": "gruen"}, {"font": "comic"}, {"animation": "spin"}, {"intensity": "500"},
                       {"seconds": "1"}, {"text": ""}, {"text": "x" * 201}):
            response = self.post("/message", page="/message", **self.form(**change))
            self.assertEqual(response.status_code, 400, change)
        self.assertEqual(self.config()["message"]["text"], "Hallo!")        # the file is untouched

    def test_empty_text_allowed_while_inactive(self):
        response = self.post("/message", page="/message", **self.form(text="", enabled=None))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.config()["message"]["enabled"])

    def test_preview_button_does_not_save(self):
        before = self.config()
        response = self.post("/message", page="/message", **self.form(action="preview", text="Nur Vorschau"))
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Nur Vorschau", page)
        self.assertIn("/message/preview.gif?", page)
        self.assertEqual(self.config(), before)

    def test_control_characters_are_removed(self):
        self.post("/message", page="/message", **self.form(text="A\x01B\r\nC"))
        self.assertEqual(self.config()["message"]["text"], "AB\nC")

    def test_preview_gif(self):
        saved = self.client.get("/message/preview.gif")
        self.assertEqual((saved.status_code, saved.mimetype), (200, "image/gif"))
        animated = self.client.get("/message/preview.gif?text=Hallo&color=%23FF0000&font=6x13B&intensity=100"
                                   "&animation=blink&speed=normal")
        self.assertEqual(animated.status_code, 200)
        self.assertGreater(Image.open(io.BytesIO(animated.data)).n_frames, 1)
        self.assertEqual(self.client.get("/message/preview.gif?text=Hi&color=rot&font=6x13B&intensity=100"
                                         "&animation=none&speed=normal").status_code, 400)

    def test_dashboard_names_the_message_in_the_list(self):
        self.post("/message", page="/message", **self.form(permanent="on", animation="none"))
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Nachricht", page)


class FormFunctionTests(unittest.TestCase):
    def test_apply_message_form_roundtrip(self):
        form = {"text": " Hi ", "color": "#abcdef", "font": "5x8", "intensity": "40", "animation": "scroll",
                "speed": "slow", "seconds": "30", "enabled": "on", "during_games": "on"}
        new, errors = apply_message_form({"favorite_team_id": "1"}, form)
        self.assertEqual(errors, [])
        self.assertEqual(new["favorite_team_id"], "1")
        self.assertEqual(new["message"]["text"], "Hi")
        self.assertEqual(new["message"]["color"], "#ABCDEF")
        self.assertTrue(new["message"]["during_games"])
        self.assertFalse(new["message"]["permanent"])


if __name__ == "__main__":
    unittest.main()
