"""Command line tool to check a provider against the live API.

Examples (run on the Pi or any PC with internet access):

    python3 -m sportstracker.cli check --config config/config.json
    python3 -m sportstracker.cli check --config config/config.json --now 2026-10-30T12:00:00+02:00
    python3 -m sportstracker.cli teams --config config/config.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from .config import Config, load_config
from .errors import ConfigError, HttpError, ProviderError
from .http import HttpClient
from .models import Game, GameStatus, TableRow
from .providers import create_provider


def _fmt_game(game: Game, tz: ZoneInfo) -> str:
    start = game.start.astimezone(tz).strftime("%d.%m.%Y %H:%M")
    score = "-:-" if game.home_score is None else "%d:%d" % (game.home_score, game.away_score)
    parts = ["%s vs %s" % (game.home.name, game.away.name), start, game.status.value, score]
    if game.period_label:
        parts.append("(%s)" % game.period_label)
    if game.decided_in:
        parts.append("[%s]" % game.decided_in)
    if game.clock:
        parts.append("clock %s" % game.clock)
    return "  ".join(parts)


def _fmt_table(rows: List[TableRow], favorite: str, limit: int) -> List[str]:
    lines = ["%2s  %-26s %3s %3s %3s %3s %5s %4s" % ("#", "Team", "Sp", "S", "U", "N", "Diff", "Pkt")]
    for row in rows[:limit]:
        mark = "*" if row.team.team_id == favorite else " "
        lines.append(
            "%2d%s %-26s %3d %3d %3d %3d %+5d %4d"
            % (row.rank, mark, row.team.name[:26], row.played, row.won, row.draw, row.lost,
               row.goal_diff, row.points)
        )
    return lines


def _build(config_path: str):
    cfg: Config = load_config(config_path)
    tz = ZoneInfo(cfg.timezone)
    provider = create_provider(cfg.provider, cfg.provider_params, HttpClient(), tz)
    return cfg, tz, provider


def cmd_check(args: argparse.Namespace) -> int:
    cfg, tz, provider = _build(args.config)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    team_id = args.team_id or cfg.favorite_team_id
    print("Provider: %s   Zeitzone: %s   Jetzt: %s" % (cfg.provider, cfg.timezone, now.astimezone(tz).strftime("%d.%m.%Y %H:%M")))

    teams = provider.list_teams()
    print("Teams: %d%s" % (len(teams), "" if team_id in {t.team_id for t in teams} else "   WARNUNG: Team-ID %s nicht in der Liste" % team_id))

    table = provider.get_table()
    print("Tabelle (%d Zeilen):" % len(table))
    for line in _fmt_table(table, team_id, args.rows):
        print("  " + line)

    game = provider.get_game_of_day(team_id, now)
    if game is None:
        print("Spiel des Tages fuer Team %s: keins -> Tabelle anzeigen" % team_id)
    else:
        print("Spiel des Tages fuer Team %s: %s" % (team_id, _fmt_game(game, tz)))
        if game.status in (GameStatus.FINISHED, GameStatus.UNKNOWN):
            print("  (Endstand bleibt bis Mitternacht stehen)")
    return 0


def cmd_teams(args: argparse.Namespace) -> int:
    _, _, provider = _build(args.config)
    for team in provider.list_teams():
        print("%-8s %-28s %-5s %s" % (team.team_id, team.name, team.short_name, team.logo_url))
    return 0


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(path: str) -> str:
    """Relative paths in the configuration are relative to the project folder."""
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def _fonts(path: Optional[str]):
    from .render import load_fonts

    return load_fonts(_resolve(path or "fonts"))


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import DemoLogos, demo_frames
    from .preview import contact_sheet
    from .render import Renderer
    from zoneinfo import ZoneInfo

    fonts = _fonts(args.fonts)
    renderer = Renderer(args.size, args.size, fonts, DemoLogos(fonts, missing=("2781",), dark=("345", "36911")),
                        ZoneInfo("Europe/Berlin"), table_logo_size=6)
    frames = demo_frames(renderer)
    contact_sheet(frames, factor=args.scale).save(args.out)
    print("Vorschau geschrieben: %s (%d Bilder)" % (args.out, len(frames)))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .demo import DemoLogos, demo_games, demo_table
    from .render import Renderer
    from zoneinfo import ZoneInfo

    t0 = time.monotonic()
    fonts = _fonts(args.fonts)
    print("Schriften laden: %.0f ms" % ((time.monotonic() - t0) * 1000))
    renderer = Renderer(args.size, args.size, fonts, DemoLogos(fonts), ZoneInfo("Europe/Berlin"))
    game = demo_games()[2][1]
    table = demo_table(9)
    for name, func in (("Spielbild", lambda: renderer.render_game(game)),
                       ("Tabelle", lambda: renderer.render_table(table, "641"))):
        func()  # warm-up (fills the logo memory cache)
        start = time.monotonic()
        for _ in range(args.runs):
            func()
        print("%-10s %.1f ms pro Bild (%d Laeufe)" % (name, (time.monotonic() - start) * 1000 / args.runs, args.runs))
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    from .logos import LogoCache
    from .preview import upscale
    from .render import Renderer

    cfg, tz, provider = _build(args.config)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    team_id = args.team_id or cfg.favorite_team_id
    width = int(cfg.matrix["cols"]) * int(cfg.matrix["chain_length"])
    height = int(cfg.matrix["rows"]) * int(cfg.matrix["parallel"])
    namespace = "%s_%s" % (cfg.provider, cfg.provider_params.get("league_shortcut") or cfg.provider_params.get("competition_id") or "")
    logos = LogoCache(_resolve(cfg.cache_dir), _resolve(cfg.logo_override_dir), HttpClient(), namespace,
                      backing=cfg.logo_backing, backing_overrides=cfg.logo_backing_teams)
    renderer = Renderer(width, height, _fonts(cfg.fonts_dir), logos, tz, labels=cfg.team_labels,
                        show_team_names=cfg.show_team_names, show_game_clock=cfg.show_game_clock,
                        table_logo_size=cfg.table_logo_size, table_label=cfg.table_label, cities=cfg.team_cities)
    game = None if args.table else provider.get_game_of_day(team_id, now)
    if game is not None:
        image = renderer.render_game(game)
        print("Spielbild: %s" % _fmt_game(game, tz))
    else:
        image = renderer.render_table(provider.get_table(), team_id)
        print("Tabellenbild")
    if args.out:
        upscale(image, args.scale).save(args.out)
        print("Vorschau geschrieben: %s" % args.out)
    if args.show:
        from .display import MatrixDisplay

        display = MatrixDisplay(cfg.matrix)
        display.show(image)
        print("Auf der Matrix fuer %d s (Strg+C beendet)" % args.seconds)
        try:
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstracker.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Teams, Tabelle und Spiel des Tages abrufen")
    check.add_argument("--config", required=True)
    check.add_argument("--now", help="ISO-Zeitpunkt zum Simulieren, z. B. 2026-10-30T12:00:00+01:00")
    check.add_argument("--team-id", help="statt favorite_team_id aus der Config")
    check.add_argument("--rows", type=int, default=8, help="Anzahl Tabellenzeilen")
    check.set_defaults(func=cmd_check)

    teams = sub.add_parser("teams", help="Teamliste mit IDs und Logo-URLs")
    teams.add_argument("--config", required=True)
    teams.set_defaults(func=cmd_teams)

    demo = sub.add_parser("demo", help="Demo-Bilder (ohne Netzwerk) als Vorschau-Datei rendern")
    demo.add_argument("--out", default="demo.png")
    demo.add_argument("--fonts", help="Schriftordner (Standard: fonts/ im Projekt)")
    demo.add_argument("--size", type=int, default=64)
    demo.add_argument("--scale", type=int, default=6)
    demo.set_defaults(func=cmd_demo)

    bench = sub.add_parser("bench", help="Renderzeit pro Bild messen (Zero: Python-Entscheidung)")
    bench.add_argument("--fonts")
    bench.add_argument("--size", type=int, default=64)
    bench.add_argument("--runs", type=int, default=50)
    bench.set_defaults(func=cmd_bench)

    preview = sub.add_parser("preview", help="Aktuelles Bild mit echten Daten rendern (Datei und/oder Matrix)")
    preview.add_argument("--config", required=True)
    preview.add_argument("--now")
    preview.add_argument("--team-id")
    preview.add_argument("--table", action="store_true", help="Tabellenbild erzwingen")
    preview.add_argument("--out", help="Vorschau als PNG speichern")
    preview.add_argument("--scale", type=int, default=8)
    preview.add_argument("--show", action="store_true", help="auf der Matrix anzeigen (Root noetig)")
    preview.add_argument("--seconds", type=int, default=30)
    preview.set_defaults(func=cmd_preview)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, ProviderError, HttpError) as exc:
        print("FEHLER: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
