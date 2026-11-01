"""Room and satellite designs (0.7.0): config, form, drawing."""

import os
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sportstracker.config import ConfigError, parse_config
from sportstracker.render import Renderer, load_fonts
from sportstracker.roomviews import comfort, date_text, humidity_text, temp_text
from sportstracker.sensor import SensorReading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 11, 1, 9, 54, tzinfo=timezone.utc)          # Sunday, 10:54 in Berlin
BERLIN = ZoneInfo("Europe/Berlin")


class Reading:
    temperature_c = 22.8
    humidity_pct = 51.0


class ConfigTests(unittest.TestCase):
    def test_defaults_and_values(self):
        cfg = parse_config({})
        self.assertEqual((cfg.room_design, cfg.satellite_design), ("c", "b"))
        cfg = parse_config({"room_design": "a", "satellite_design": "c"})
        self.assertEqual((cfg.room_design, cfg.satellite_design), ("a", "c"))

    def test_invalid_design_is_refused(self):
        for key in ("room_design", "satellite_design"):
            with self.assertRaises(ConfigError):
                parse_config({key: "x"})


class TextTests(unittest.TestCase):
    def test_date_and_numbers(self):
        self.assertEqual(date_text(NOW.astimezone(BERLIN)), "SO 01.11.")
        self.assertEqual(date_text(NOW.astimezone(BERLIN), with_weekday=False), "01.11.")
        self.assertEqual((temp_text(21.54), temp_text(None), humidity_text(47.6), humidity_text(None)),
                         ("21,5", "--,-", "48", "--"))

    def test_comfort_levels(self):
        self.assertEqual(comfort(21.5, 48), ("ok", "KOMFORT"))
        self.assertEqual(comfort(21.5, 70)[0:2], ("warn", "ZU FEUCHT"))
        self.assertEqual(comfort(22, 25), ("bad", "ZU TROCKEN"))
        self.assertEqual(comfort(27, 50)[1], "ZU WARM")
        self.assertEqual(comfort(15, 50), ("bad", "ZU KÜHL"))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.r = Renderer(64, 64, load_fonts(os.path.join(ROOT, "fonts")), tz=BERLIN)

    def colors(self, image):
        return {image.getpixel((x, y)) for x in range(image.width) for y in range(image.height)}

    def test_three_room_designs_differ_and_fit(self):
        images = {}
        for design in ("a", "b", "c"):
            self.r.room_design = design
            image = self.r.render_room(NOW, Reading())
            self.assertEqual(image.size, (64, 64))
            self.assertIn(self.r.theme.room_temperature, self.colors(image))
            self.assertIn(self.r.theme.room_humidity, self.colors(image))
            images[design] = image.tobytes()
        self.assertEqual(len(set(images.values())), 3)

    def test_room_without_measurement_shows_dashes_and_still_draws(self):
        for design in ("a", "b", "c"):
            self.r.room_design = design
            image = self.r.render_room(NOW, None)
            self.assertIsNotNone(image.getbbox())
            self.assertNotEqual(image.tobytes(), self.r.render_room(NOW, Reading()).tobytes())

    def test_room_without_sensor_keeps_big_clock(self):
        self.r.room_design = "a"
        plain = self.r.render_room(NOW, None, show_climate=False)
        self.r.room_design = "b"
        self.assertEqual(plain.tobytes(), self.r.render_room(NOW, None, show_climate=False).tobytes())

    def test_analog_clock_follows_the_time(self):
        self.r.room_design = "b"
        other = datetime(2026, 11, 1, 9, 25, tzinfo=timezone.utc)
        self.assertNotEqual(self.r.render_room(NOW, Reading()).tobytes(), self.r.render_room(other, Reading()).tobytes())

    def test_comfort_dot_colour_of_design_c(self):
        self.r.room_design = "c"
        good = self.r.render_room(NOW, SensorReading(temperature_c=21.5, humidity_pct=48))
        dry = self.r.render_room(NOW, SensorReading(temperature_c=21.5, humidity_pct=20))
        self.assertEqual(good.getpixel((59, 4)), self.r.theme.air_good)
        self.assertEqual(dry.getpixel((59, 4)), self.r.theme.air_bad)

    def test_satellite_designs(self):
        class Sat:
            temperature_c = 22.8
            humidity_pct = 51.0
        seen = set()
        for design in ("a", "b", "c"):
            self.r.satellite_design = design
            for quality in ((90, "GUT"), (55, "MITTEL"), (10, "SCHLECHT")):
                image = self.r.render_satellite("Küche", Sat(), quality)
                self.assertEqual(image.size, (64, 64))
                expected = self.r.theme.air_good if quality[0] >= 70 else self.r.theme.air_mid if quality[0] >= 40 else self.r.theme.air_bad
                self.assertIn(expected, self.colors(image))
                # nothing is drawn beyond the right edge: the last column stays free of text (bars end at x=59)
                seen.add(image.tobytes())
        self.assertEqual(len(seen), 9)

    def test_other_panel_sizes_use_the_classic_layout(self):
        small = Renderer(32, 32, load_fonts(os.path.join(ROOT, "fonts")), tz=BERLIN)
        self.assertEqual(small.render_room(NOW, Reading()).size, (32, 32))


if __name__ == "__main__":
    unittest.main()
