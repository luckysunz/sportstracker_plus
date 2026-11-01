"""Message mode ("Nachricht"): a free text with colour, font, intensity and one of six simple animations.

The message lives in the ``message`` section of the configuration (edited on the "Nachricht" page of the web
backend). The display service shows it in the rotation or, with ``permanent``, exclusively.

Rendering is built for a Pi Zero: the glyphs of a font are turned into small masks once, every frame only pastes
the visible masks into one grey-scale mask and colours it in one go.

Animations (``message["animation"]``):

* ``none``        text stands still, centred, wrapped to the width of the panel
* ``scroll``      one line runs from right to left
* ``blink``       text switches on and off
* ``pulse``       text fades in and out softly
* ``rainbow``     colours run through the text (the chosen colour is ignored)
* ``wave``        the letters bob up and down one after the other
* ``typewriter``  the letters appear one by one, the text stays for a moment, then it starts again
"""

from __future__ import annotations

import colorsys
import io
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .bdf import BdfFont, load_bdf

FPS = 15                         # frames per second while an animation runs
FRAME_INTERVAL = 1.0 / FPS
SCROLL_PIXELS_PER_SECOND = 22.0
TYPEWRITER_CHARS_PER_SECOND = 6.0
TYPEWRITER_HOLD_SECONDS = 2.5    # the finished text stays, then it is wiped
MAX_SCROLL_SECONDS = 180
SPEEDS = {"slow": 0.5, "normal": 1.0, "fast": 2.0}
DEFAULT_COLOR = (255, 176, 0)


@dataclass(frozen=True)
class FontChoice:
    key: str
    label: str
    filename: str
    advance: int          # character width, used to estimate how long a scroll takes


# Ordered by size: if the text does not fit, the next smaller font is used.
FONT_CHOICES: Tuple[FontChoice, ...] = (
    FontChoice("tom-thumb", "Winzig (Tom Thumb 3x5)", "tom-thumb.bdf", 4),
    FontChoice("4x6", "Klein 4x6", "4x6.bdf", 4),
    FontChoice("5x7", "5x7", "5x7.bdf", 5),
    FontChoice("5x8", "5x8", "5x8.bdf", 5),
    FontChoice("6x10", "6x10", "6x10.bdf", 6),
    FontChoice("6x13", "6x13", "6x13.bdf", 6),
    FontChoice("6x13B", "6x13 fett", "6x13B.bdf", 6),
    FontChoice("7x13", "7x13", "7x13.bdf", 7),
    FontChoice("7x13B", "7x13 fett", "7x13B.bdf", 7),
    FontChoice("8x13B", "8x13 fett", "8x13B.bdf", 8),
    FontChoice("9x15", "9x15", "9x15.bdf", 9),
    FontChoice("9x15B", "9x15 fett", "9x15B.bdf", 9),
    FontChoice("9x18B", "9x18 fett", "9x18B.bdf", 9),
    FontChoice("10x20", "10x20 (groß)", "10x20.bdf", 10),
)
FONT_KEYS = tuple(choice.key for choice in FONT_CHOICES)
_FONT_BY_KEY = {choice.key: choice for choice in FONT_CHOICES}

ANIMATIONS: Tuple[Tuple[str, str], ...] = (
    ("none", "Keine (stehender Text)"),
    ("scroll", "Lauftext"),
    ("blink", "Blinken"),
    ("pulse", "Pulsieren"),
    ("rainbow", "Regenbogen"),
    ("wave", "Welle"),
    ("typewriter", "Schreibmaschine"),
)
SPEED_LABELS: Tuple[Tuple[str, str], ...] = (("slow", "Langsam"), ("normal", "Normal"), ("fast", "Schnell"))


# -- small helpers --------------------------------------------------------------------------------

def parse_color(text: Any) -> Tuple[int, int, int]:
    """'#RRGGBB' to a tuple; anything else gives the default amber."""
    try:
        value = str(text).strip()
        if len(value) == 7 and value[0] == "#":
            return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    except ValueError:
        pass
    return DEFAULT_COLOR


def _scaled(color: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    factor = max(0.0, min(1.0, factor))
    return tuple(int(round(channel * factor)) for channel in color)  # type: ignore[return-value]


def has_text(message: Dict[str, Any]) -> bool:
    return bool(str(message.get("text", "")).strip())


def is_animated(message: Dict[str, Any]) -> bool:
    return has_text(message) and message.get("animation", "none") != "none"


def speed_factor(message: Dict[str, Any]) -> float:
    return SPEEDS.get(str(message.get("speed", "normal")), 1.0)


def wrap_lines(font: BdfFont, text: str, max_width: int) -> List[str]:
    """Greedy word wrap; explicit line breaks are kept, words wider than the panel are broken."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else current + " " + word
            if font.text_width(candidate) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while font.text_width(word) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and font.text_width(word[:cut]) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def scroll_text(text: str) -> str:
    return " ".join(part.strip() for part in text.split("\n") if part.strip())


def scroll_seconds(message: Dict[str, Any], width: int) -> float:
    """How long one pass of the scrolling text takes (from entering on the right to leaving on the left)."""
    choice = _FONT_BY_KEY.get(str(message.get("font")), _FONT_BY_KEY["6x13B"])
    pixels = len(scroll_text(str(message.get("text", "")))) * choice.advance + width
    return pixels / (SCROLL_PIXELS_PER_SECOND * speed_factor(message))


def rotation_seconds(message: Dict[str, Any], width: int) -> int:
    """Seconds the message takes in the rotation: the configured time, at least one pass for a scroll text."""
    seconds = int(message.get("seconds", 10))
    if message.get("animation") == "scroll" and has_text(message):
        seconds = max(seconds, int(math.ceil(scroll_seconds(message, width))) + 1)
    return min(seconds, MAX_SCROLL_SECONDS)


def cycle_seconds(message: Dict[str, Any], width: int) -> float:
    """Length of one full animation cycle in seconds (for the preview GIF)."""
    speed = speed_factor(message)
    animation = message.get("animation", "none")
    if animation == "scroll":
        return scroll_seconds(message, width)
    if animation == "blink":
        return 1.0 / speed
    if animation == "pulse":
        return 2.0 / speed
    if animation == "rainbow":
        return 4.0 / speed
    if animation == "wave":
        return 1.25 / speed
    if animation == "typewriter":
        chars = len(str(message.get("text", "")))
        return (chars / TYPEWRITER_CHARS_PER_SECOND + TYPEWRITER_HOLD_SECONDS) / speed
    return 1.0


# -- layout and frames -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Cell:
    char: str
    x: int
    y: int


@dataclass(frozen=True)
class Layout:
    font_key: str
    cells: Tuple[Cell, ...]
    width: int            # width of the whole text (relevant for scrolling)
    font_height: int


class BannerRenderer:
    """Turns a message and a point in time into a picture of the panel size."""

    def __init__(self, fonts_dir: str, width: int, height: int) -> None:
        self.fonts_dir = fonts_dir
        self.width = width
        self.height = height
        self._fonts: Dict[str, BdfFont] = {}
        self._masks: Dict[Tuple[str, str], Image.Image] = {}
        self._layouts: Dict[Tuple[str, str, bool], Layout] = {}

    # -- fonts ---------------------------------------------------------------------------------

    def font(self, key: str) -> BdfFont:
        key = key if key in _FONT_BY_KEY else "5x7"
        font = self._fonts.get(key)
        if font is None:
            try:
                font = load_bdf(os.path.join(self.fonts_dir, _FONT_BY_KEY[key].filename))
            except Exception:  # noqa: BLE001 - a missing font file must not stop the display
                font = load_bdf(os.path.join(self.fonts_dir, "5x7.bdf"))
            self._fonts[key] = font
        return font

    def _mask(self, key: str, char: str) -> Image.Image:
        mask = self._masks.get((key, char))
        if mask is None:
            font = self.font(key)
            mask = Image.new("L", (max(1, font.text_width(char)), max(1, font.height)), 0)
            font.draw(mask, 0, 0, char, 255)  # type: ignore[arg-type]
            self._masks[(key, char)] = mask
        return mask

    # -- layout --------------------------------------------------------------------------------

    def layout(self, text: str, font_key: str, scroll: bool) -> Layout:
        cache_key = (text, font_key, scroll)
        cached = self._layouts.get(cache_key)
        if cached is not None:
            return cached
        layout = self._scroll_layout(text, font_key) if scroll else self._block_layout(text, font_key)
        if len(self._layouts) > 32:
            self._layouts.clear()
        self._layouts[cache_key] = layout
        return layout

    def _scroll_layout(self, text: str, font_key: str) -> Layout:
        font = self.font(font_key)
        line = scroll_text(text)
        y = max(0, (self.height - font.height) // 2)
        cells, x = [], 0
        for char in line:
            if char != " ":
                cells.append(Cell(char, x, y))
            x += font.text_width(char)
        return Layout(font_key, tuple(cells), x, font.height)

    def _block_layout(self, text: str, font_key: str) -> Layout:
        keys = list(FONT_KEYS)
        start = keys.index(font_key) if font_key in keys else keys.index("5x7")
        chosen, lines, font = FONT_KEYS[0], [], self.font(FONT_KEYS[0])
        for key in reversed(keys[:start + 1]):               # the wanted font first, then smaller ones
            font = self.font(key)
            lines = wrap_lines(font, text, self.width)
            chosen = key
            if len(lines) * font.height <= self.height:
                break
        max_lines = max(1, self.height // font.height)
        lines = lines[:max_lines]
        top = max(0, (self.height - len(lines) * font.height) // 2)
        cells: List[Cell] = []
        for index, line in enumerate(lines):
            x = max(0, (self.width - font.text_width(line)) // 2)
            y = top + index * font.height
            for char in line:
                if char != " ":
                    cells.append(Cell(char, x, y))
                x += font.text_width(char)
        return Layout(chosen, tuple(cells), self.width, font.height)

    # -- frames --------------------------------------------------------------------------------

    def frame(self, message: Dict[str, Any], t: float = 0.0) -> Image.Image:
        """The picture of the message ``t`` seconds into its animation."""
        image = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        if not has_text(message):
            return image
        animation = str(message.get("animation", "none"))
        intensity = max(5, min(100, int(message.get("intensity", 100)))) / 100.0
        base = _scaled(parse_color(message.get("color")), intensity)
        tt = t * speed_factor(message)
        layout = self.layout(str(message.get("text", "")).strip(), str(message.get("font", "6x13B")),
                             animation == "scroll")
        cells = layout.cells
        level = 1.0
        shift_x = 0
        offsets: Dict[int, int] = {}
        if animation == "scroll":
            position = (tt * SCROLL_PIXELS_PER_SECOND) % (self.width + layout.width)
            shift_x = int(math.floor(self.width - position))
        elif animation == "blink":
            if int(tt * 2) % 2 == 1:
                return image
        elif animation == "pulse":
            level = 0.1 + 0.9 * (0.5 - 0.5 * math.cos(2 * math.pi * tt / 2.0))
        elif animation == "wave":
            amplitude = max(1, int(round(layout.font_height / 6.0)))
            for index in range(len(cells)):
                offsets[index] = int(round(amplitude * math.sin(2 * math.pi * 0.8 * tt + index * 0.6)))
        elif animation == "typewriter":
            cycle = len(cells) / TYPEWRITER_CHARS_PER_SECOND + TYPEWRITER_HOLD_SECONDS
            phase = tt % cycle
            shown = min(len(cells), int(phase * TYPEWRITER_CHARS_PER_SECOND))
            cells = cells[:shown]
        mask = Image.new("L", (self.width, self.height), 0)
        for index, cell in enumerate(cells):
            x = cell.x + shift_x
            if x >= self.width or x + 12 < 0:
                continue
            glyph = self._mask(layout.font_key, cell.char)
            if x + glyph.width <= 0:
                continue
            mask.paste(255, (x, cell.y + offsets.get(index, 0)), glyph)
        if animation == "rainbow":
            strip = Image.new("RGB", (self.width, 1))
            pixels = strip.load()
            for x in range(self.width):
                hue = (x / float(self.width) + tt * 0.25) % 1.0
                red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, intensity)
                pixels[x, 0] = (int(red * 255), int(green * 255), int(blue * 255))
            image.paste(strip.resize((self.width, self.height), Image.NEAREST), (0, 0), mask)
        else:
            image.paste(_scaled(base, level), (0, 0), mask)
        return image


def preview_gif(renderer: BannerRenderer, message: Dict[str, Any], factor: int = 4, fps: int = 10,
                max_seconds: float = 12.0) -> bytes:
    """Animated GIF of one animation cycle (a single frame for a standing text), LED-style enlarged."""
    from .preview import upscale  # imported late: only the web backend needs it

    if is_animated(message):
        seconds = min(max_seconds, max(0.5, cycle_seconds(message, renderer.width)))
        count = max(2, int(seconds * fps))
    else:
        count = 1
    frames = [upscale(renderer.frame(message, index / float(fps)), factor) for index in range(count)]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)
    return out.getvalue()
