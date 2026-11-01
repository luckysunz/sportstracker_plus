"""Room and satellite pages in three selectable designs (0.7.0), drawn for a 64x64 matrix.

``draw_room`` and ``draw_satellite`` are pure drawing functions (no hardware, no clock access) that the renderer calls
when the panel is 64x64; other sizes keep the simple classic layout of ``render.py``.

Room designs (``room_design`` in the configuration):
  a  classic+   date, large digital clock, temperature and humidity with small symbols
  b  analog     hands clock, temperature and humidity side by side, date below
  c  climate    clock and date small on top, temperature large, humidity bar, comfort hint (default)

Satellite designs (``satellite_design``): a list with a segment bar, b semicircle gauge (default), c air index large.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional, Tuple

from PIL import Image, ImageDraw

Color = Tuple[int, int, int]

WEEKDAYS = ("MO", "DI", "MI", "DO", "FR", "SA", "SO")
DEGREE = "°"

_PERCENT = ("##...#.", "##..#..", "...#...", "..#....", ".#..##.", "#...##.", ".......")
_DROP = ("...#...", "...#...", "..###..", "..###..", ".#####.", "#######", "#######", ".#####.", "..###..")


def date_text(local: datetime, with_weekday: bool = True) -> str:
    if with_weekday:
        return "%s %s" % (WEEKDAYS[local.weekday()], local.strftime("%d.%m."))
    return local.strftime("%d.%m.")


def comfort(temperature: float, humidity: float) -> Tuple[str, str]:
    """(level, hint): level is ``ok``, ``warn`` or ``bad``; the hint says what is off (empty if everything is fine)."""
    if humidity > 60:
        hint = "ZU FEUCHT"
    elif humidity < 40:
        hint = "ZU TROCKEN"
    elif temperature > 24:
        hint = "ZU WARM"
    elif temperature < 20:
        hint = "ZU KÜHL"
    else:
        hint = ""
    if not hint:
        return "ok", "KOMFORT"
    if 18 <= temperature <= 26 and 30 <= humidity <= 70:
        return "warn", hint
    return "bad", hint


def temp_text(value: Optional[float]) -> str:
    return "--,-" if value is None else ("%.1f" % value).replace(".", ",")


def humidity_text(value: Optional[float]) -> str:
    return "--" if value is None else "%d" % int(round(value))


# -- drawing helpers ---------------------------------------------------------------------------------------------

def _put(image: Image.Image, x: int, y: int, color: Color) -> None:
    if 0 <= x < image.width and 0 <= y < image.height:
        image.putpixel((x, y), color)


def _bitmap(image: Image.Image, x: int, y: int, rows: Tuple[str, ...], color: Color) -> None:
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch == "#":
                _put(image, x + dx, y + dy, color)


def draw_percent(image: Image.Image, x: int, y: int, color: Color) -> int:
    """A clean 7x7 percent sign (the glyph of the small fonts looks like an X); returns the width."""
    _bitmap(image, x, y, _PERCENT, color)
    return 7


def draw_drop(image: Image.Image, x: int, y: int, color: Color) -> None:
    _bitmap(image, x, y, _DROP, color)


def draw_thermometer(image: Image.Image, x: int, y: int, color: Color) -> None:
    for dy in range(6):
        _put(image, x + 2, y + dy, color)
    for dx, dy in ((1, 6), (2, 6), (3, 6), (0, 7), (1, 7), (2, 7), (3, 7), (4, 7), (1, 8), (2, 8), (3, 8)):
        _put(image, x + dx, y + dy, color)


def scaled(image: Image.Image, font: Any, x: int, y: int, text: str, color: Color, scale: int) -> int:
    """Text enlarged by an integer factor; returns the drawn width."""
    width = font.text_width(text)
    if scale <= 1:
        font.draw(image, x, y, text, color)
        return width
    small = Image.new("RGB", (width, font.height), (0, 0, 0))
    font.draw(small, 0, 0, text, (255, 255, 255))
    mask = small.convert("L").point(lambda v: 255 if v > 0 else 0).resize((width * scale, font.height * scale),
                                                                          Image.NEAREST)
    image.paste(Image.new("RGB", mask.size, color), (x, y), mask)
    return width * scale


def centered(image: Image.Image, font: Any, cx: int, y: int, text: str, color: Color) -> None:
    font.draw(image, cx - font.text_width(text) // 2, y, text, color)


def fit(font: Any, text: str, width: int) -> str:
    while text and font.text_width(text) > width:
        text = text[:-1]
    return text


def _digital_clock(image: Image.Image, font: Any, cx: int, y: int, hhmm: str, color: Color, scale: int) -> None:
    hours, minutes = hhmm.split(":")
    dot = 2 * scale
    gap = scale
    total = (font.text_width(hours) + font.text_width(minutes)) * scale + 2 * gap + dot
    x = cx - total // 2
    x += scaled(image, font, x, y, hours, color, scale) + gap
    for dy in (3, 7):
        for yy in range(y + dy * scale, y + dy * scale + dot):
            for xx in range(x, x + dot):
                _put(image, xx, yy, color)
    scaled(image, font, x + dot + gap, y, minutes, color, scale)


def _units(image: Image.Image, fonts: Any, theme: Any, x: int, y: int, temperature: Optional[float],
           humidity: Optional[float]) -> None:
    """Temperature and humidity side by side, centred on ``x`` (6x10 font)."""
    font = fonts.big
    temp = temp_text(temperature) + DEGREE
    hum = humidity_text(humidity)
    w1 = font.text_width(temp)
    w2 = font.text_width(hum) + 8
    gap = 5
    x0 = x - (w1 + gap + w2) // 2
    end = font.draw(image, x0, y, temp, theme.room_temperature)
    end = font.draw(image, end + gap, y, hum, theme.room_humidity)
    draw_percent(image, end + 1, y + 1, theme.room_humidity)


# -- room --------------------------------------------------------------------------------------------------------

def draw_room(image: Image.Image, fonts: Any, theme: Any, local: datetime, temperature: Optional[float],
              humidity: Optional[float], design: str) -> None:
    if design == "a":
        _room_a(image, fonts, theme, local, temperature, humidity)
    elif design == "b":
        _room_b(image, fonts, theme, local, temperature, humidity)
    else:
        _room_c(image, fonts, theme, local, temperature, humidity)


def _room_a(image: Image.Image, fonts: Any, theme: Any, local: datetime, temperature: Optional[float],
            humidity: Optional[float]) -> None:
    centered(image, fonts.small, 32, 2, date_text(local), theme.date)
    _digital_clock(image, fonts.big, 32, 11, local.strftime("%H:%M"), theme.room_time, 2)
    for x in range(6, 58):
        _put(image, x, 36, theme.dim)
    draw_thermometer(image, 5, 41, theme.room_temperature)
    end = fonts.big.draw(image, 16, 41, temp_text(temperature), theme.room_temperature)
    fonts.big.draw(image, end + 1, 41, DEGREE + "C", theme.room_temperature)
    draw_drop(image, 3, 53, theme.room_humidity)
    end = fonts.big.draw(image, 16, 53, humidity_text(humidity), theme.room_humidity)
    draw_percent(image, end + 2, 54, theme.room_humidity)


def _room_b(image: Image.Image, fonts: Any, theme: Any, local: datetime, temperature: Optional[float],
            humidity: Optional[float]) -> None:
    draw = ImageDraw.Draw(image)
    cx, cy, radius = 32, 22, 20
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=theme.dim)
    for i in range(12):
        angle = math.radians(i * 30)
        major = i % 3 == 0
        inner = radius - (4 if major else 2)
        draw.line((cx + math.sin(angle) * inner, cy - math.cos(angle) * inner,
                   cx + math.sin(angle) * (radius - 1), cy - math.cos(angle) * (radius - 1)),
                  fill=theme.room_time if major else theme.date)
    minute_angle = math.radians(local.minute * 6)
    hour_angle = math.radians((local.hour % 12) * 30 + local.minute * 0.5)
    draw.line((cx, cy, cx + math.sin(hour_angle) * 11, cy - math.cos(hour_angle) * 11), fill=theme.room_time, width=2)
    draw.line((cx, cy, cx + math.sin(minute_angle) * 17, cy - math.cos(minute_angle) * 17), fill=theme.room_time)
    draw.rectangle((cx - 1, cy - 1, cx + 1, cy + 1), fill=theme.score)
    _units(image, fonts, theme, 32, 46, temperature, humidity)
    centered(image, fonts.small, 32, 58, date_text(local), theme.date)


def _room_c(image: Image.Image, fonts: Any, theme: Any, local: datetime, temperature: Optional[float],
            humidity: Optional[float]) -> None:
    hhmm = local.strftime("%H:%M")
    fonts.medium.draw(image, 2, 2, hhmm, theme.room_time)
    fonts.small.draw(image, 2 + fonts.medium.text_width(hhmm) + 4, 3, date_text(local, with_weekday=False), theme.date)
    fresh = temperature is not None and humidity is not None
    level, hint = comfort(temperature, humidity) if fresh else ("none", "")
    dot = {"ok": theme.air_good, "warn": theme.air_mid, "bad": theme.air_bad}.get(level, theme.dim)
    for dx in range(4):
        for dy in range(4):
            _put(image, 58 + dx, 3 + dy, dot)
    number = temp_text(temperature)
    total = fonts.big.text_width(number) * 2 + 14
    x = 32 - total // 2
    end = x + scaled(image, fonts.big, x, 15, number, theme.room_temperature, 2)
    fonts.medium.draw(image, end + 2, 15, DEGREE, theme.room_temperature)
    fonts.medium.draw(image, end + 2, 23, "C", theme.room_temperature)
    hum = humidity_text(humidity)
    end = fonts.big.draw(image, 3, 41, hum, theme.room_humidity)
    draw_percent(image, end + 2, 42, theme.room_humidity)
    left, right, top = 30, 60, 44
    for xx in range(left, right + 1):
        for yy in range(top, top + 3):
            _put(image, xx, yy, theme.dim)
    if humidity is not None:
        for xx in range(left, left + int((right - left) * max(0.0, min(100.0, humidity)) / 100.0) + 1):
            for yy in range(top, top + 3):
                _put(image, xx, yy, theme.room_humidity)
    for percent in (40, 60):                         # comfort range 40..60 %
        xx = left + int((right - left) * percent / 100.0)
        _put(image, xx, top - 2, theme.date)
        _put(image, xx, top + 4, theme.date)
    if hint:
        color = theme.date if level == "ok" else (theme.air_mid if level == "warn" else theme.air_bad)
        centered(image, fonts.small, 32, 55, hint, color)


# -- satellite ---------------------------------------------------------------------------------------------------

def _air_color(theme: Any, index: int) -> Color:
    return theme.air_good if index >= 70 else theme.air_mid if index >= 40 else theme.air_bad


def draw_satellite(image: Image.Image, fonts: Any, theme: Any, name: str, temperature: Optional[float],
                   humidity: Optional[float], quality: Tuple[int, str], design: str) -> None:
    index, label = quality
    color = _air_color(theme, index)
    if design == "a":
        _sat_a(image, fonts, theme, name, temperature, humidity, index, label, color)
    elif design == "c":
        _sat_c(image, fonts, theme, name, temperature, humidity, index, label, color)
    else:
        _sat_b(image, fonts, theme, name, temperature, humidity, index, label, color)


def _sat_title(image: Image.Image, fonts: Any, theme: Any, name: str, y: int, preferred: Any) -> None:
    title = name.upper()
    font = preferred if preferred.text_width(title) <= 62 else fonts.small
    centered(image, font, 32, y, fit(font, title, 62), theme.sat_name)


def _sat_a(image: Image.Image, fonts: Any, theme: Any, name: str, temperature: Optional[float],
           humidity: Optional[float], index: int, label: str, color: Color) -> None:
    _sat_title(image, fonts, theme, name, 2, fonts.big)
    for x in range(10, 54):
        _put(image, x, 14, theme.dim)
    draw_thermometer(image, 5, 19, theme.room_temperature)
    fonts.big.draw(image, 16, 19, temp_text(temperature) + DEGREE + "C", theme.room_temperature)
    draw_drop(image, 3, 31, theme.room_humidity)
    end = fonts.big.draw(image, 16, 31, humidity_text(humidity), theme.room_humidity)
    draw_percent(image, end + 2, 32, theme.room_humidity)
    fonts.small.draw(image, 3, 45, "LUFT", theme.date)
    big_fits = 24 + fonts.big.text_width(label) <= 62
    if big_fits:
        fonts.big.draw(image, 24, 43, label, color)
    else:
        fonts.medium.draw(image, min(24, 62 - fonts.medium.text_width(label)), 44, label, color)
    segments, width, gap = 5, 10, 2
    x0 = (image.width - (segments * width + (segments - 1) * gap)) // 2
    lit = max(1, int(round(index / 100.0 * segments)))
    for s in range(segments):
        fill = color if s < lit else theme.dim
        for xx in range(x0 + s * (width + gap), x0 + s * (width + gap) + width):
            for yy in range(56, 61):
                _put(image, xx, yy, fill)


def _sat_b(image: Image.Image, fonts: Any, theme: Any, name: str, temperature: Optional[float],
           humidity: Optional[float], index: int, label: str, color: Color) -> None:
    _sat_title(image, fonts, theme, name, 1, fonts.medium)
    cx, cy, radius = 32, 34, 22
    for k in range(0, 181, 2):
        angle = math.radians(180 + k)
        for r in range(radius - 4, radius + 1):
            _put(image, int(round(cx + math.cos(angle) * r)), int(round(cy + math.sin(angle) * r)), theme.dim)
    for k in range(0, int(180 * index / 100) + 1):
        angle = math.radians(180 + k)
        for r in range(radius - 4, radius + 1):
            _put(image, int(round(cx + math.cos(angle) * r)), int(round(cy + math.sin(angle) * r)), color)
    centered(image, fonts.big, 32, 22, "%d" % index, color)
    text = "LUFT " + label
    if fonts.small.text_width(text) > 62:
        text = label
    centered(image, fonts.small, 32, 34, text, color)
    _units(image, fonts, theme, 32, 48, temperature, humidity)


def _sat_c(image: Image.Image, fonts: Any, theme: Any, name: str, temperature: Optional[float],
           humidity: Optional[float], index: int, label: str, color: Color) -> None:
    title = fit(fonts.medium, name.upper(), 52)
    fonts.medium.draw(image, 2, 2, title, theme.sat_name)
    for dx in range(4):
        for dy in range(4):
            _put(image, 58 + dx, 3 + dy, color)
    number = "%d" % index
    width = fonts.big.text_width(number) * 3
    scaled(image, fonts.big, 32 - width // 2, 12, number, color, 3)
    centered(image, fonts.small, 32, 44, "LUFT  " + label, color)
    for x in range(4, 60):
        _put(image, x, 53, theme.dim)
    for x in range(4, 4 + int(56 * index / 100)):
        _put(image, x, 53, color)
    fonts.small.draw(image, 2, 57, temp_text(temperature) + DEGREE, theme.room_temperature)
    hum = humidity_text(humidity) + "%"
    fonts.small.draw(image, 62 - fonts.small.text_width(hum), 57, hum, theme.room_humidity)
