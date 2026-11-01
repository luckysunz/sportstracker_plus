"""Synthetic demo data for layout checks, previews and timing (no network needed)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

from PIL import Image, ImageDraw

from .models import Game, GameStatus, PeriodScore, TableRow, Team
from .logos import is_dark_logo, with_backing
from .render import Fonts, Renderer

_NAMES = [
    ("338", "Adler Mannheim", "MAN"), ("348", "Augsburger Panther", "AEV"), ("641", "Eisbären Berlin", "EBB"),
    ("345", "ERC Ingolstadt", "ING"), ("5042", "Grizzlys Wolfsburg", "WOB"), ("347", "Iserlohn Roosters", "IEC"),
    ("344", "Kölner Haie", "KEC"), ("6451", "Krefeld Pinguine", "KEV"), ("5283", "Löwen Frankfurt", "FRA"),
    ("5450", "Nürnberg Ice Tigers", "NIT"), ("5041", "Pinguins Bremerhaven", "BHV"), ("2773", "Red Bull München", "RBM"),
    ("2781", "Schwenninger Wild Wings", "SWW"), ("351", "Straubing Tigers", "STR"),
]
TEAMS = {tid: Team(tid, name, short) for tid, name, short in _NAMES}
ZWICKAU = Team("36365", "RB Zwickau e.V.")
MUENSTER = Team("36911", "Spinto Münsterland")


class DemoLogos:
    """Colored discs with an initial; ``missing`` team ids return None (tests the fallback)."""

    def __init__(self, fonts: Fonts, missing: Tuple[str, ...] = (), dark: Tuple[str, ...] = ()) -> None:
        self.fonts = fonts
        self.missing = missing
        self.dark = dark

    def get(self, team: Team, size: int) -> Optional[Image.Image]:
        if team.team_id in self.missing:
            return None
        digest = hashlib.md5(team.team_id.encode()).digest()
        color = (25, 25, 70) if team.team_id in self.dark else tuple(60 + b % 180 for b in digest[:3])
        scale = 4
        big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        inset = size * scale // 5 if team.team_id in self.dark else 0  # dark logo: shape on transparent ground
        ImageDraw.Draw(big).ellipse((inset, inset, size * scale - 1 - inset, size * scale - 1 - inset), fill=color + (255,))
        img = big.resize((size, size), Image.LANCZOS)
        letter = team.name[0].upper()
        font = self.fonts.big
        if size >= font.height + 2:
            font.draw(img, (size - font.text_width(letter)) // 2, (size - font.height) // 2, letter, (255, 255, 255))
        return with_backing(img) if is_dark_logo(img) else img


def _start(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 30, hour, minute, tzinfo=timezone(timedelta(hours=1))).astimezone(timezone.utc)


def demo_games() -> List[Tuple[str, Game]]:  # index 2 is used by `cli bench`
    ebb, ing, sww = TEAMS["641"], TEAMS["345"], TEAMS["2781"]
    return [
        ("Basketball live mit Uhr", Game("1", ZWICKAU, MUENSTER, _start(17), GameStatus.LIVE, 34, 41,
                                         period_label="Q2", clock="07:32")),
        ("Basketball Pause", Game("2", ZWICKAU, MUENSTER, _start(17), GameStatus.LIVE, 48, 52,
                                  period_label="Q2", period_break=True)),
        ("Eishockey live (Drittelstaende)", Game("3", sww, ebb, _start(14), GameStatus.LIVE, 1, 3,
                                                 period_label="2.Drittel",
                                                 periods=(PeriodScore("1.Drittel", 0, 1), PeriodScore("2.Drittel", 1, 3)))),
        ("angesetzt", Game("4", ing, ebb, _start(19, 30), GameStatus.SCHEDULED)),
        ("beendet n.V.", Game("5", ebb, ing, _start(19, 30), GameStatus.FINISHED, 3, 2, decided_in="OT")),
        ("beendet", Game("6", sww, ebb, _start(14), GameStatus.FINISHED, 1, 4)),
    ]


def demo_table(favorite_rank: int = 1) -> List[TableRow]:
    teams = list(TEAMS.values())
    # Put the favorite (Eisbaeren) at the requested rank.
    fav = TEAMS["641"]
    others = [t for t in teams if t.team_id != fav.team_id]
    ordered = others[:favorite_rank - 1] + [fav] + others[favorite_rank - 1:]
    return [TableRow(rank=i + 1, team=t, played=12, won=9 - i // 2, lost=i // 2, points=max(0, 28 - 2 * i))
            for i, t in enumerate(ordered)]


def demo_frames(renderer: Renderer) -> List[Tuple[str, Image.Image]]:
    frames = [(title, renderer.render_game(game)) for title, game in demo_games()]
    frames.append(("Tabelle, Favorit Platz 1", renderer.render_table(demo_table(1), "641")))
    frames.append(("Tabelle, Favorit Platz 9", renderer.render_table(demo_table(9), "641")))
    first = demo_games()[0][1]
    saved = renderer.show_team_names
    renderer.show_team_names = False
    frames.append(("ohne Teamnamen", renderer.render_game(first)))
    renderer.show_team_names = saved
    saved_logo = renderer.table_logo_size
    for size in (6,):
        renderer.table_logo_size = size
        frames.append(("Tabelle mit Logo %d px" % size, renderer.render_table(demo_table(9), "641")))
    renderer.table_logo_size = saved_logo
    return frames
