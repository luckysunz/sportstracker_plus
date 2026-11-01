"""Minimal BDF bitmap font reader and drawer.

The hzeller matrix library ships its fonts as BDF files. Reading them here
(instead of using the library's own canvas drawing) keeps the renderer
independent of the matrix hardware: frames are plain Pillow images that can be
previewed on any PC and tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .errors import ConfigError

# Only the Latin range is kept (umlauts, sharp s, accents): faster to load on a Pi Zero.
MAX_CODEPOINT = 0x17F


@dataclass(frozen=True)
class Glyph:
    advance: int
    width: int
    height: int
    xoff: int
    yoff: int
    bits: int  # bits per stored row (whole bytes)
    rows: Tuple[int, ...]  # most significant bit = leftmost pixel


class BdfFont:
    def __init__(self, glyphs: Dict[int, Glyph], ascent: int, descent: int, default_advance: int) -> None:
        self.glyphs = glyphs
        self.ascent = ascent
        self.descent = descent
        self.default_advance = default_advance

    @property
    def height(self) -> int:
        return self.ascent + self.descent

    def _glyph(self, char: str) -> Optional[Glyph]:
        glyph = self.glyphs.get(ord(char))
        if glyph is None:
            glyph = self.glyphs.get(ord("?"))
        return glyph

    def text_width(self, text: str) -> int:
        total = 0
        for char in text:
            glyph = self._glyph(char)
            total += glyph.advance if glyph else self.default_advance
        return total

    def draw(self, image, x: int, y: int, text: str, color: Tuple[int, int, int]) -> int:
        """Draw ``text`` with its top-left corner at (x, y); returns the x after the last glyph."""
        pixels = image.load()
        width, height = image.size
        baseline = y + self.ascent
        pen = x
        for char in text:
            glyph = self._glyph(char)
            if glyph is None:
                pen += self.default_advance
                continue
            top = baseline - glyph.yoff - glyph.height
            for row_index, row in enumerate(glyph.rows):
                py = top + row_index
                if py < 0 or py >= height:
                    continue
                for col in range(glyph.width):
                    if (row >> (glyph.bits - 1 - col)) & 1:
                        px = pen + glyph.xoff + col
                        if 0 <= px < width:
                            pixels[px, py] = color
            pen += glyph.advance
        return pen


def parse_bdf(text: str) -> BdfFont:
    glyphs: Dict[int, Glyph] = {}
    bbox = (0, 0, 0, 0)
    font_ascent: Optional[int] = None
    font_descent: Optional[int] = None
    encoding = -1
    advance = 0
    gbbx = (0, 0, 0, 0)
    in_bitmap = False
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if in_bitmap:
            if line == "ENDCHAR":
                in_bitmap = False
                if 0 <= encoding <= MAX_CODEPOINT:
                    bits = (len(rows[0]) * 4) if rows else 0
                    glyphs[encoding] = Glyph(
                        advance, gbbx[0], gbbx[1], gbbx[2], gbbx[3], bits, tuple(int(r, 16) for r in rows)
                    )
            elif line:
                rows.append(line)
            continue
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        if key == "FONTBOUNDINGBOX":
            bbox = tuple(int(v) for v in parts[1:5])  # type: ignore[assignment]
        elif key == "FONT_ASCENT":
            font_ascent = int(parts[1])
        elif key == "FONT_DESCENT":
            font_descent = int(parts[1])
        elif key == "ENCODING":
            encoding = int(parts[1])
        elif key == "DWIDTH":
            advance = int(parts[1])
        elif key == "BBX":
            gbbx = tuple(int(v) for v in parts[1:5])  # type: ignore[assignment]
        elif key == "BITMAP":
            in_bitmap = True
            rows = []
    if not glyphs:
        raise ConfigError("BDF font contains no usable glyphs")
    ascent = font_ascent if font_ascent is not None else bbox[1] + bbox[3]
    descent = font_descent if font_descent is not None else -bbox[3]
    return BdfFont(glyphs, ascent, descent, bbox[0] or 4)


def load_bdf(path: str) -> BdfFont:
    try:
        with open(path, "r", encoding="latin-1") as handle:
            return parse_bdf(handle.read())
    except OSError as exc:
        raise ConfigError("cannot read font %s: %s" % (path, exc))
