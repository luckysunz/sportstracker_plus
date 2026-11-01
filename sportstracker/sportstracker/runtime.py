"""Shared set-up helpers for the CLI and the three services."""

from __future__ import annotations

import os
from typing import Any, Optional, Tuple

from .banner import BannerRenderer
from .config import Config
from .logos import LogoCache
from .picture import PictureRenderer
from .render import Renderer, load_fonts, logo_sizes

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(path: str, root: str = PROJECT_ROOT) -> str:
    """Relative paths in the configuration are relative to the project folder."""
    return path if os.path.isabs(path) else os.path.join(root, path)


def matrix_size(cfg: Config) -> Tuple[int, int]:
    return int(cfg.matrix["cols"]) * int(cfg.matrix["chain_length"]), int(cfg.matrix["rows"]) * int(cfg.matrix["parallel"])


def logo_sizes_for(cfg: Config) -> Tuple[int, int]:
    width, height = matrix_size(cfg)
    return logo_sizes(width, height, cfg.table_logo_size)


def logo_namespace(cfg: Config) -> str:
    return "%s_%s" % (cfg.provider, cfg.provider_params.get("league_shortcut") or cfg.provider_params.get("competition_id") or "")


def state_path(cfg: Config, root: str = PROJECT_ROOT) -> str:
    return os.path.join(resolve(cfg.data_dir, root), "state.json")


def build_logo_cache(cfg: Config, http: Any = None, read_only: bool = False, root: str = PROJECT_ROOT) -> LogoCache:
    return LogoCache(
        resolve(cfg.cache_dir, root), resolve(cfg.logo_override_dir, root), http, logo_namespace(cfg),
        backing=cfg.logo_backing, backing_overrides=cfg.logo_backing_teams, read_only=read_only,
    )


def build_renderer(cfg: Config, logos: Any, tz: Any, root: str = PROJECT_ROOT, fonts: Optional[Any] = None) -> Renderer:
    width, height = matrix_size(cfg)
    renderer = Renderer(
        width, height, fonts or load_fonts(resolve(cfg.fonts_dir, root)), logos, tz, labels=cfg.team_labels,
        show_team_names=cfg.show_team_names, show_game_clock=cfg.show_game_clock,
        table_logo_size=cfg.table_logo_size, table_label=cfg.table_label, cities=cfg.team_cities,
    )
    renderer.banner = BannerRenderer(resolve(cfg.fonts_dir, root), width, height)
    renderer.picture = PictureRenderer(resolve(cfg.data_dir, root), width, height)
    return renderer
