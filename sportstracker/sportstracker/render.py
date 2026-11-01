"""Frame renderer: turns a game or a league table into a Pillow image.

Pure drawing, no hardware access. The layout is designed for 64x64 and derived
from the image size, so other panel sizes work without code changes.

Game view (64x64):  logo vs logo, team labels, score, one small info line
(clock / period scores / "ENDE"). Table view: a window of rows around the
favorite team (rank, label, points), favorite highlighted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .bdf import BdfFont, load_bdf
from .models import Game, GameStatus, TableRow, Team
from .sensor import SensorReading

Color = Tuple[int, int, int]


@dataclass(frozen=True)
class Fonts:
    small: BdfFont   # 4x6: labels, info line, table
    medium: BdfFont  # 5x7: placeholders, messages
    big: BdfFont     # 6x10: score


def load_fonts(fonts_dir: str) -> Fonts:
    return Fonts(
        small=load_bdf(os.path.join(fonts_dir, "4x6.bdf")),
        medium=load_bdf(os.path.join(fonts_dir, "5x7.bdf")),
        big=load_bdf(os.path.join(fonts_dir, "6x10.bdf")),
    )


@dataclass(frozen=True)
class Theme:
    background: Color = (0, 0, 0)
    label: Color = (235, 235, 235)
    score: Color = (255, 220, 0)
    start_time: Color = (255, 255, 255)
    live: Color = (0, 220, 0)
    final: Color = (255, 140, 0)
    info: Color = (160, 160, 160)
    row: Color = (170, 170, 170)
    favorite_text: Color = (255, 220, 0)
    favorite_bar: Color = (60, 45, 0)
    room_time: Color = (255, 255, 255)
    room_temperature: Color = (255, 150, 0)
    room_humidity: Color = (0, 170, 255)
    sat_name: Color = (255, 255, 255)
    air_good: Color = (0, 200, 60)
    air_mid: Color = (255, 190, 0)
    air_bad: Color = (255, 40, 30)
    air_unknown: Color = (90, 90, 90)


# ---------------------------------------------------------------------------
# text helpers (pure functions, unit tested)
# ---------------------------------------------------------------------------

_GENERIC = {"rsb", "rsv", "rbc", "rbb", "rb", "sg", "tsv", "sv", "bg", "fc", "ev", "hc", "tv", "bsc", "erc", "e.v.", "ev."}


def team_label(team: Team, labels: Optional[Dict[str, str]] = None, maxlen: int = 3) -> str:
    """Short label for a team: manual override, API short name, else derived from the name."""
    if labels and team.team_id in labels:
        return labels[team.team_id]
    if team.short_name:
        return team.short_name[:maxlen + 2].upper()
    words = [w for w in re.split(r"\s+", team.name.strip()) if w and w.lower() not in _GENERIC]
    base = words[0] if words else team.name.strip()
    letters = "".join(ch for ch in base if ch.isalpha()) or base
    return letters[:maxlen].upper()


_NICKNAMES = {
    "adler", "panther", "eisbären", "grizzlys", "roosters", "haie", "pinguine", "löwen", "ice", "tigers", "pinguins",
    "red", "bull", "bulls", "wild", "wings", "united", "rhinos", "dolphins", "iguanas", "99ers", "eislöwen", "falken",
    "huskies", "füchse", "wölfe", "devils", "sharks", "lions", "eagles",
}
_DEMONYMS = {
    "augsburger": "Augsburg", "kölner": "Köln", "schwenninger": "Schwenningen", "nürnberger": "Nürnberg",
    "münchner": "München", "berliner": "Berlin", "frankfurter": "Frankfurt", "hamburger": "Hamburg",
    "düsseldorfer": "Düsseldorf", "krefelder": "Krefeld", "kasseler": "Kassel", "dresdner": "Dresden",
}


def team_city(team: Team, cities: Optional[Dict[str, str]] = None) -> str:
    """City/place name for a team: manual override, else the name without club forms and nicknames."""
    if cities and team.team_id in cities:
        return cities[team.team_id]
    words = [w for w in re.split(r"\s+", team.name.strip()) if w]
    kept = [w for w in words if w.lower() not in _GENERIC and w.lower() not in _NICKNAMES]
    if not kept:
        return team.name.strip()
    return " ".join(_DEMONYMS.get(w.lower(), w) for w in kept)


def fit_text(font: BdfFont, text: str, max_width: int) -> str:
    """Shorten ``text`` with a trailing '.' so that it fits into ``max_width`` pixels."""
    if font.text_width(text) <= max_width:
        return text
    while text and font.text_width(text + ".") > max_width:
        text = text[:-1]
    return (text.rstrip() + ".") if text else ""


def short_period(label: Optional[str]) -> str:
    if not label:
        return ""
    match = re.match(r"^\s*(\d)\.\s*(?:Drittel|Dr\.?)\s*$", label, re.IGNORECASE)
    if match:
        return "%s.Dr" % match.group(1)
    lowered = label.lower()
    if lowered in ("overtime", "verlängerung", "verlaengerung", "ot"):
        return "OT"
    if lowered.startswith("penalty") or lowered in ("so", "shootout"):
        return "PEN"
    return label[:6]


def period_chain(game: Game, max_chars: int) -> str:
    """Cumulative period scores, e.g. '0:1 1:3'; oldest entries dropped if too long."""
    parts = ["%d:%d" % (p.home, p.away) for p in game.periods]
    while parts and len(" ".join(parts)) > max_chars:
        parts.pop(0)
    return " ".join(parts)


def score_text(game: Game, tz: tzinfo) -> str:
    if game.status == GameStatus.SCHEDULED:
        return game.start.astimezone(tz).strftime("%H:%M")
    home = "-" if game.home_score is None else str(game.home_score)
    away = "-" if game.away_score is None else str(game.away_score)
    return "%s:%s" % (home, away)


def info_line(game: Game, show_clock: bool, max_chars: int) -> Tuple[str, str]:
    """Small line under the score: (text, kind) with kind 'live', 'final' or 'info'."""
    if game.status == GameStatus.SCHEDULED:
        return "HEUTE", "info"
    if game.status == GameStatus.LIVE:
        period = short_period(game.period_label)
        if game.period_break:
            return ("%s PAUSE" % period).strip(), "live"
        if game.clock and show_clock:
            return ("%s %s" % (period, game.clock)).strip(), "live"
        chain = period_chain(game, max_chars)
        if chain:
            return chain, "live"
        return period or "LIVE", "live"
    suffix = {"OT": " n.V.", "SO": " n.P."}.get(game.decided_in or "", "")
    return "ENDE" + suffix, "final"


def table_window(rows: List[TableRow], favorite_id: str, count: int) -> List[TableRow]:
    """``count`` rows, scrolled so that the favorite is visible (centred when possible)."""
    if len(rows) <= count:
        return list(rows)
    index = next((i for i, r in enumerate(rows) if r.team.team_id == favorite_id), 0)
    start = max(0, min(index - count // 2, len(rows) - count))
    return rows[start:start + count]


def logo_sizes(width: int, height: int, table_logo_size: int) -> Tuple[int, int]:
    """(game view logo size, table logo size) in pixels for a panel of the given size."""
    return min(width // 2 - 6, int(round(26 * height / 64.0))), table_logo_size


def draw_score(image: Image.Image, font: BdfFont, cx: int, y: int, text: str, color: Color) -> None:
    """Draw a score or time like ``3:1`` / ``19:30`` centred on ``cx``.

    The ':' is drawn as two 2x2 dots instead of the font glyph, because the
    big 6x10 font draws it as small crosses that look wrong on the panel.
    """
    if text.count(":") != 1:
        font.draw(image, cx - font.text_width(text) // 2, y, text, color)
        return
    left, right = text.split(":")
    gap, dot = 2, 2
    total = font.text_width(left) + gap + dot + gap + font.text_width(right)
    x = cx - total // 2
    font.draw(image, x, y, left, color)
    x += font.text_width(left) + gap
    for dy in (2, 5):  # digits span rows 1..7 of the line box; dots centred around row 4
        for yy in range(y + dy, y + dy + dot):
            for xx in range(x, x + dot):
                if 0 <= xx < image.width and 0 <= yy < image.height:
                    image.putpixel((xx, yy), color)
    font.draw(image, x + dot + gap, y, right, color)


# ---------------------------------------------------------------------------
# renderer
# ---------------------------------------------------------------------------

class Renderer:
    def __init__(
        self,
        width: int,
        height: int,
        fonts: Fonts,
        logos: Any = None,
        tz: Optional[tzinfo] = None,
        theme: Theme = Theme(),
        labels: Optional[Dict[str, str]] = None,
        show_team_names: bool = True,
        show_game_clock: bool = True,
        table_logo_size: int = 0,
        table_label: str = "city",
        cities: Optional[Dict[str, str]] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.fonts = fonts
        self.logos = logos
        self.tz = tz
        self.theme = theme
        self.labels = labels or {}
        self.show_team_names = show_team_names
        self.show_game_clock = show_game_clock
        self.table_logo_size = table_logo_size
        self.table_label = table_label
        self.cities = cities or {}
        self.banner: Optional[Any] = None   # message mode (banner.BannerRenderer), set by runtime.build_renderer
        self.picture: Optional[Any] = None  # picture mode (picture.PictureRenderer), set by runtime.build_renderer

    # -- helpers ----------------------------------------------------------------

    def _blank(self) -> Image.Image:
        return Image.new("RGB", (self.width, self.height), self.theme.background)

    def _v(self, value: int) -> int:
        """Scale a 64-pixel design coordinate to the actual height."""
        return int(round(value * self.height / 64.0))

    def _centered(self, image: Image.Image, font: BdfFont, cx: int, y: int, text: str, color: Color) -> None:
        font.draw(image, cx - font.text_width(text) // 2, y, text, color)

    def logo_sizes(self) -> Tuple[int, int]:
        """(game view logo size, table logo size) in pixels; used to pre-load the logo cache."""
        return logo_sizes(self.width, self.height, self.table_logo_size)

    # -- game view --------------------------------------------------------------

    def render_game(self, game: Game) -> Image.Image:
        t = self.theme
        image = self._blank()
        w = self.width
        logo_size = self.logo_sizes()[0]
        logo_y = self._v(3)
        centers = (w // 4, (3 * w) // 4)

        for team, cx in ((game.home, centers[0]), (game.away, centers[1])):
            self._draw_logo(image, team, cx, logo_y, logo_size)
            if self.show_team_names:
                label = team_label(team, self.labels)
                self._centered(image, self.fonts.small, cx, logo_y + logo_size + 2, label, t.label)

        if self.show_team_names:
            score_y, info_y = self._v(40), self._v(53)
        else:
            score_y, info_y = self._v(37), self._v(51)
        text = score_text(game, self.tz or game.start.tzinfo)
        color = t.start_time if game.status == GameStatus.SCHEDULED else t.score
        draw_score(image, self.fonts.big, w // 2, score_y, text, color)

        max_chars = max(1, w // self.fonts.small.default_advance)
        line, kind = info_line(game, self.show_game_clock, max_chars)
        line_color = {"live": t.live, "final": t.final}.get(kind, t.info)
        self._centered(image, self.fonts.small, w // 2, info_y, line, line_color)
        return image

    def _draw_logo(self, image: Image.Image, team: Team, cx: int, y: int, size: int) -> None:
        logo = self.logos.get(team, size) if self.logos is not None else None
        x = cx - size // 2
        if logo is not None:
            image.paste(logo, (x, y), logo)
            return
        # Fallback: framed label, so a missing logo is obvious but harmless.
        t = self.theme
        for i in range(size):
            for px, py in ((x + i, y), (x + i, y + size - 1), (x, y + i), (x + size - 1, y + i)):
                if 0 <= px < self.width and 0 <= py < self.height:
                    image.putpixel((px, py), t.info)
        label = team_label(team, self.labels)
        self._centered(image, self.fonts.medium, cx, y + (size - self.fonts.medium.height) // 2, label, t.label)

    # -- table view -------------------------------------------------------------

    def render_table(self, rows: List[TableRow], favorite_id: str) -> Image.Image:
        if not rows:
            return self.render_message(["KEINE", "TABELLE"])
        t = self.theme
        image = self._blank()
        font = self.fonts.small
        logo = self.table_logo_size
        row_h = max(font.height, logo) + 1
        count = max(1, (self.height - 1) // row_h)
        visible = table_window(rows, favorite_id, count)
        rank_right = 8
        logo_x = rank_right + 1
        label_x = (logo_x + logo + 1) if logo else rank_right + 5
        points_right = self.width - 1
        for i, row in enumerate(visible):
            bar_top = i * row_h
            content_top = bar_top + 1
            favorite = row.team.team_id == favorite_id
            if favorite:
                for yy in range(bar_top, min(self.height, bar_top + row_h)):
                    for xx in range(self.width):
                        image.putpixel((xx, yy), t.favorite_bar)
            color = t.favorite_text if favorite else t.row
            text_y = content_top + (row_h - 1 - font.height) // 2
            rank = str(row.rank)
            points = str(row.points)
            font.draw(image, rank_right - font.text_width(rank), text_y, rank, color)
            if logo and self.logos is not None:
                picture = self.logos.get(row.team, logo)
                if picture is not None:
                    image.paste(picture, (logo_x, content_top + (row_h - 1 - logo) // 2), picture)
            if self.table_label == "city":
                label = fit_text(font, team_city(row.team, self.cities), points_right - font.text_width(points) - 1 - label_x)
            else:
                label = team_label(row.team, self.labels, maxlen=3)
            font.draw(image, label_x, text_y, label, color)
            font.draw(image, points_right - font.text_width(points), text_y, points, color)
        return image

    # -- room view --------------------------------------------------------------

    def _draw_scaled(self, image: Image.Image, font: BdfFont, x: int, y: int, text: str, color: Color, scale: int) -> int:
        """Draw ``text`` enlarged by an integer factor (pixel doubling); returns the drawn width."""
        width = font.text_width(text)
        if scale <= 1:
            font.draw(image, x, y, text, color)
            return width
        small = Image.new("RGB", (width, font.height), (0, 0, 0))
        font.draw(small, 0, 0, text, (255, 255, 255))
        mask = small.convert("L").point(lambda v: 255 if v > 0 else 0).resize((width * scale, font.height * scale), Image.NEAREST)
        image.paste(Image.new("RGB", mask.size, color), (x, y), mask)
        return width * scale

    def _draw_clock(self, image: Image.Image, text: str, cx: int, y: int, scale: int) -> None:
        """``HH:MM`` with a colon drawn as two dots (the glyph of the 6x10 font looks like crosses)."""
        font = self.fonts.big
        hours, minutes = text.split(":")
        dot = 2 * scale
        gap = scale
        total = (font.text_width(hours) + font.text_width(minutes)) * scale + 2 * gap + dot
        x = cx - total // 2
        color = self.theme.room_time
        x += self._draw_scaled(image, font, x, y, hours, color, scale) + gap
        for dy in (3, 7):
            for yy in range(y + dy * scale, y + (dy + 1) * scale + dot - scale):
                for xx in range(x, x + dot):
                    if 0 <= xx < image.width and 0 <= yy < image.height:
                        image.putpixel((xx, yy), color)
        self._draw_scaled(image, font, x + dot + gap, y, minutes, color, scale)

    def render_room(self, now: datetime, reading: Optional[SensorReading] = None, show_climate: bool = True) -> Image.Image:
        """Large clock; below it temperature and humidity (dashes if there is no fresh measurement)."""
        t = self.theme
        image = self._blank()
        local = now.astimezone(self.tz) if self.tz is not None else now
        scale = 2 if self.width >= 56 and self.height >= 56 else 1
        clock_h = self.fonts.big.height * scale
        font = self.fonts.big
        cx = self.width // 2
        if not show_climate:
            self._draw_clock(image, local.strftime("%H:%M"), cx, (self.height - clock_h) // 2, scale)
            return image
        self._draw_clock(image, local.strftime("%H:%M"), cx, self._v(5), scale)
        if reading is not None and reading.temperature_c is not None and reading.humidity_pct is not None:
            temp = ("%.1f" % reading.temperature_c).replace(".", ",") + " \u00b0C"
            humidity = "%d %%" % int(round(reading.humidity_pct))
        else:
            temp, humidity = "--,- \u00b0C", "-- %"
        self._centered(image, font, cx, self._v(32), temp, t.room_temperature)
        self._centered(image, font, cx, self._v(46), humidity, t.room_humidity)
        return image

    # -- satellite page ---------------------------------------------------------

    def render_satellite(self, name: str, reading: Optional[Any] = None,
                         quality: Optional[Tuple[int, str]] = None) -> Image.Image:
        """One room of a satellite sensor: name, temperature, humidity and a coloured air quality bar.

        ``reading`` None (no fresh data) shows dashes. ``quality`` is (index 0..100, label) or None (no gas sensor).
        """
        t = self.theme
        image = self._blank()
        cx = self.width // 2
        title = name.upper()
        font = self.fonts.big if self.fonts.big.text_width(title) <= self.width - 2 else (
            self.fonts.medium if self.fonts.medium.text_width(title) <= self.width - 2 else self.fonts.small)
        self._centered(image, font, cx, self._v(2), fit_text(font, title, self.width - 2), t.sat_name)
        fresh = reading is not None
        if fresh and reading.temperature_c is not None:
            temp = ("%.1f" % reading.temperature_c).replace(".", ",") + " \u00b0C"
        else:
            temp = "--,- \u00b0C"
        if fresh and reading.humidity_pct is not None:
            humidity = "%d %%" % int(round(reading.humidity_pct))
        else:
            humidity = "-- %"
        self._centered(image, self.fonts.big, cx, self._v(17), temp, t.room_temperature)
        self._centered(image, self.fonts.big, cx, self._v(30), humidity, t.room_humidity)
        if quality is None:
            return image
        index, label = quality
        color = t.air_good if index >= 70 else t.air_mid if index >= 40 else t.air_bad
        self._centered(image, self.fonts.small, cx, self._v(43), "LUFT", t.info)
        self._centered(image, self.fonts.big, cx, self._v(49), label, color)
        bar_w = self.width - 8
        top = self._v(61)
        for x in range(bar_w):
            image.putpixel((4 + x, top), t.air_unknown)
        for x in range(int(round(bar_w * index / 100.0))):
            image.putpixel((4 + x, top), color)
        return image

    # -- message ----------------------------------------------------------------

    def render_message(self, lines: List[str]) -> Image.Image:
        image = self._blank()
        font = self.fonts.medium
        total = len(lines) * (font.height + 2)
        y = (self.height - total) // 2
        for line in lines:
            self._centered(image, font, self.width // 2, y, line, self.theme.info)
            y += font.height + 2
        return image
