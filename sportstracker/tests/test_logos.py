import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

from helpers import FakeHttp
from sportstracker.errors import HttpError
from sportstracker.logos import LogoCache, detect_kind
from sportstracker.models import Team


def png_bytes(size=(40, 20), color=(200, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def white_jpeg():
    image = Image.new("RGB", (60, 60), (255, 255, 255))
    image.paste((10, 10, 120), (15, 15, 45, 45))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def shape_png(color, inset=10, size=40):
    """Transparent square with a coloured disc in the middle."""
    from PIL import ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((inset, inset, size - inset, size - inset), fill=color + (255,))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeProcess:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, b"", returncode


class LogosTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.cache = os.path.join(self.tmp, "cache")
        self.team = Team("641", "Eisbären Berlin", "EBB", "http://x/logo.png")

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, http, **kw):
        return LogoCache(self.cache, os.path.join(self.tmp, "override"), http, "del", **kw)

    def test_detect_kind(self):
        self.assertEqual(detect_kind(png_bytes()), "png")
        self.assertEqual(detect_kind(white_jpeg()), "jpg")
        self.assertEqual(detect_kind(b'<?xml version="1.0"?><svg xmlns="x"/>'), "svg")
        self.assertIsNone(detect_kind(b"<html>not a logo</html>"))

    def test_download_scale_and_disk_cache(self):
        cache = self.make(FakeHttp({"http://x/logo.png": png_bytes((40, 20))}))
        image = cache.get(self.team, 24)
        self.assertEqual(image.size, (24, 24))
        self.assertEqual(image.mode, "RGBA")
        # aspect ratio kept: 40x20 -> 24x12, centred, transparent padding above/below
        self.assertEqual(image.getpixel((12, 0))[3], 0)
        self.assertEqual(image.getpixel((12, 12))[3], 255)
        # a fresh cache instance without any network must be served from disk
        offline = self.make(FakeHttp({}))
        again = offline.get(self.team, 24)
        self.assertEqual(again.size, (24, 24))
        self.assertEqual(offline.http.calls, [])

    def test_other_size_reuses_raw_download(self):
        http = FakeHttp({"http://x/logo.png": png_bytes()})
        cache = self.make(http)
        cache.get(self.team, 24)
        cache.get(self.team, 16)
        self.assertEqual(len(http.calls), 1)

    def test_white_jpeg_background_becomes_transparent(self):
        team = Team("9", "Test", "", "http://x/l.jpg")
        image = self.make(FakeHttp({"http://x/l.jpg": white_jpeg()})).get(team, 24)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        centre = image.getpixel((12, 12))
        self.assertEqual(centre[3], 255)
        self.assertLess(centre[0], 100)  # dark blue square kept

    def test_svg_uses_rsvg(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs["input"]))
            return FakeProcess(png_bytes((50, 50)))

        team = Team("3", "Svg", "", "http://x/l.svg")
        http = FakeHttp({"http://x/l.svg": b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'})
        image = self.make(http, runner=runner).get(team, 24)
        self.assertEqual(image.size, (24, 24))
        self.assertIn("-w", calls[0][0])
        self.assertIn("96", calls[0][0])  # rendered at 4x the target size

    def test_svg_without_rasteriser_gives_none(self):
        def runner(command, **kwargs):
            raise FileNotFoundError("rsvg-convert")

        team = Team("3", "Svg", "", "http://x/l.svg")
        http = FakeHttp({"http://x/l.svg": b"<svg></svg>"})
        with mock.patch.dict(sys.modules, {"cairosvg": None}):
            self.assertIsNone(self.make(http, runner=runner).get(team, 24))

    def test_override_wins_and_needs_no_network(self):
        os.makedirs(os.path.join(self.tmp, "override"))
        Image.new("RGBA", (30, 30), (0, 0, 255, 255)).save(os.path.join(self.tmp, "override", "641.png"))
        http = FakeHttp({})
        image = self.make(http).get(self.team, 24)
        self.assertEqual(image.getpixel((12, 12)), (0, 0, 255, 255))
        self.assertEqual(http.calls, [])

    def test_failed_download_is_retried_only_after_a_pause(self):
        now = [0.0]
        http = FakeHttp({"http://x/logo.png": HttpError("boom", status=500)})
        cache = self.make(http, clock=lambda: now[0], retry_after=900)
        self.assertIsNone(cache.get(self.team, 24))
        now[0] = 100
        self.assertIsNone(cache.get(self.team, 24))
        self.assertEqual(len(http.calls), 1)
        now[0] = 1000
        http.routes["http://x/logo.png"] = png_bytes()
        self.assertIsNotNone(cache.get(self.team, 24))
        self.assertEqual(len(http.calls), 2)

    def test_unknown_format_gives_none(self):
        http = FakeHttp({"http://x/logo.png": b"<html>error page</html>"})
        self.assertIsNone(self.make(http).get(self.team, 24))

    def test_changed_logo_url_is_downloaded_again(self):
        http = FakeHttp({"http://x/logo.png": png_bytes(color=(200, 0, 0)), "http://x/new.png": png_bytes(color=(0, 200, 0))})
        cache = self.make(http)
        cache.get(self.team, 24)
        moved = Team("641", "Eisbären Berlin", "EBB", "http://x/new.png")
        image = self.make(http).get(moved, 24)
        self.assertEqual(image.getpixel((12, 12))[:3], (0, 200, 0))

    def test_dark_logo_gets_light_backing(self):
        http = FakeHttp({"http://x/logo.png": shape_png((15, 15, 60))})
        image = self.make(http).get(self.team, 24)
        self.assertGreaterEqual(min(image.getpixel((2, 12))[:3]), 200)   # light disc around the logo
        self.assertEqual(image.getpixel((2, 12))[3], 255)
        self.assertLess(max(image.getpixel((12, 12))[:3]), 100)          # logo itself still dark

    def test_bright_logo_gets_no_backing(self):
        http = FakeHttp({"http://x/logo.png": shape_png((255, 120, 0))})
        image = self.make(http).get(self.team, 24)
        self.assertEqual(image.getpixel((2, 12))[3], 0)

    def test_backing_modes_and_team_overrides(self):
        dark = shape_png((15, 15, 60))
        bright = shape_png((255, 120, 0))
        off = self.make(FakeHttp({"http://x/logo.png": dark}), backing="off").get(self.team, 24)
        self.assertEqual(off.getpixel((2, 12))[3], 0)
        always = self.make(FakeHttp({"http://x/logo.png": bright}), backing="always").get(self.team, 24)
        self.assertEqual(always.getpixel((2, 12))[3], 255)
        per_team = self.make(FakeHttp({"http://x/logo.png": dark}), backing_overrides={"641": "off"}).get(self.team, 24)
        self.assertEqual(per_team.getpixel((2, 12))[3], 0)

    def test_no_url_and_no_cache_gives_none(self):
        self.assertIsNone(self.make(FakeHttp({})).get(Team("1", "X", "", ""), 24))


if __name__ == "__main__":
    unittest.main()
