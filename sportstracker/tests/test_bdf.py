import os
import unittest

from PIL import Image

from sportstracker.bdf import load_bdf, parse_bdf

FONT = """STARTFONT 2.1
FONT test
FONTBOUNDINGBOX 4 6 0 -1
STARTPROPERTIES 2
FONT_ASCENT 5
FONT_DESCENT 1
ENDPROPERTIES
CHARS 3
STARTCHAR A
ENCODING 65
DWIDTH 4 0
BBX 3 3 0 0
BITMAP
E0
A0
E0
ENDCHAR
STARTCHAR question
ENCODING 63
DWIDTH 4 0
BBX 3 1 0 0
BITMAP
40
ENDCHAR
STARTCHAR far
ENCODING 8364
DWIDTH 4 0
BBX 1 1 0 0
BITMAP
80
ENDCHAR
ENDFONT
"""

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


class BdfTest(unittest.TestCase):
    def test_metrics_and_glyph_filter(self):
        font = parse_bdf(FONT)
        self.assertEqual((font.ascent, font.descent, font.height), (5, 1, 6))
        self.assertIn(65, font.glyphs)
        self.assertNotIn(8364, font.glyphs)  # outside the kept Latin range

    def test_text_width_and_fallback(self):
        font = parse_bdf(FONT)
        self.assertEqual(font.text_width("AA"), 8)
        self.assertEqual(font.text_width("B"), 4)  # unknown char uses '?'

    def test_draw_places_pixels_relative_to_top(self):
        font = parse_bdf(FONT)
        image = Image.new("RGB", (8, 8))
        end = font.draw(image, 1, 0, "A", (255, 0, 0))
        self.assertEqual(end, 5)
        # glyph is 3 rows high, baseline at y = 0 + ascent 5 -> rows 2..4, columns 1..3
        self.assertEqual(image.getpixel((1, 2)), (255, 0, 0))
        self.assertEqual(image.getpixel((3, 2)), (255, 0, 0))
        self.assertEqual(image.getpixel((2, 3)), (0, 0, 0))
        self.assertEqual(image.getpixel((1, 3)), (255, 0, 0))

    def test_draw_is_clipped(self):
        font = parse_bdf(FONT)
        image = Image.new("RGB", (2, 3))
        font.draw(image, -1, -1, "AA", (255, 255, 255))  # must not raise

    @unittest.skipUnless(os.path.isdir(FONTS_DIR), "fonts folder missing")
    def test_real_fonts_load(self):
        for name, height in (("4x6", 6), ("5x7", 7), ("6x10", 10)):
            font = load_bdf(os.path.join(FONTS_DIR, name + ".bdf"))
            self.assertEqual(font.height, height)
            self.assertGreater(font.text_width("äöüß"), 0)


if __name__ == "__main__":
    unittest.main()
