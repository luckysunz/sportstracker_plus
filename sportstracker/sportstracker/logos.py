"""Team logo cache.

Logos come from third-party servers (SVG, PNG or JPEG, format detected from the
content, not the URL). Each logo is downloaded once, rasterised/scaled to the
requested square size and stored as PNG, so normal operation needs no network
and almost no CPU. A manual override ``<override_dir>/<team_id>.png`` always
wins over the downloaded logo.

SVG needs ``rsvg-convert`` (apt: librsvg2-bin) or the Python package cairosvg.
If a logo cannot be produced, ``get`` returns None and the renderer falls back
to a text label.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image, ImageDraw

from .errors import HttpError
from .models import Team

log = logging.getLogger(__name__)

_NEAR_WHITE = 235


def detect_kind(data: bytes) -> Optional[str]:
    """Return 'png', 'jpg', 'gif', 'webp', 'svg' or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:2048].lower()
    if b"<svg" in head:
        return "svg"
    return None


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value) or "_"


def remove_white_background(img: Image.Image, thresh: int = 40) -> Image.Image:
    """Make a white/near-white background transparent (flood fill from the corners).

    Only applied to fully opaque images whose four corners are near white, so
    white parts inside the logo stay. On a LED panel a white box around a logo
    would be far too bright.
    """
    img = img.convert("RGBA")
    if img.getchannel("A").getextrema()[0] < 255:
        return img
    w, h = img.size
    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    for xy in corners:
        if min(img.getpixel(xy)[:3]) < _NEAR_WHITE:
            return img
    for xy in corners:
        if img.getpixel(xy)[3] != 0:
            ImageDraw.floodfill(img, xy, (0, 0, 0, 0), thresh=thresh)
    return img


def fit_square(img: Image.Image, size: int) -> Image.Image:
    """Scale to fit into size x size keeping the aspect ratio; transparent padding, centred."""
    img = img.convert("RGBA")
    w, h = img.size
    scale = min(size / float(w), size / float(h))
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    resized = img.resize(new_size, Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - new_size[0]) // 2, (size - new_size[1]) // 2))
    return canvas


def is_dark_logo(img: Image.Image, bright_level: int = 110, min_bright_fraction: float = 0.25) -> bool:
    """True if (almost) all visible pixels are dark, i.e. the logo would vanish on a black LED panel.

    Brightness is the largest colour channel, so saturated blue or red counts as bright.
    """
    img = img.convert("RGBA")
    pixels = img.load()
    total = bright = 0
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if a < 128:
                continue
            total += 1
            if max(r, g, b) >= bright_level:
                bright += 1
    return total > 0 and bright < total * min_bright_fraction


def with_backing(img: Image.Image, color: Tuple[int, int, int] = (225, 225, 225)) -> Image.Image:
    """Put the logo on a light disc (readable on a black panel)."""
    img = img.convert("RGBA")
    size = img.width
    scale = 4
    disc = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    disc = disc.resize((size, size), Image.LANCZOS)
    base = Image.new("RGBA", (size, size), color + (0,))
    base.putalpha(disc)
    base.alpha_composite(img)
    return base


class LogoCache:
    def __init__(
        self,
        cache_dir: str,
        override_dir: Optional[str] = None,
        http: Any = None,
        namespace: str = "default",
        rsvg_command: str = "rsvg-convert",
        runner: Callable[..., Any] = subprocess.run,
        retry_after: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        backing: str = "auto",
        backing_overrides: Optional[Dict[str, str]] = None,
        read_only: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.override_dir = override_dir
        self.http = http
        self.namespace = _safe(namespace)
        self.rsvg_command = rsvg_command
        self._runner = runner
        self.retry_after = retry_after
        self._clock = clock
        # "auto": light disc behind dark logos, "off": never, "always": always.
        self.backing = backing
        self.backing_overrides = backing_overrides or {}
        # read_only: never write cache files (display service runs as root; avoids root-owned cache files)
        self.read_only = read_only
        self._memory: Dict[Tuple[str, int], Optional[Image.Image]] = {}
        self._failed: Dict[str, float] = {}

    # -- public -----------------------------------------------------------------

    def get(self, team: Team, size: int) -> Optional[Image.Image]:
        key = _safe(team.team_id)
        mem_key = (key + "|" + team.logo_url, size)
        if mem_key in self._memory:
            return self._memory[mem_key]
        image = self._load(team, key, size)
        if image is not None:
            self._memory[mem_key] = image
        return image

    # -- internals --------------------------------------------------------------

    def _load(self, team: Team, key: str, size: int) -> Optional[Image.Image]:
        override = self._override_path(key)
        if override:
            try:
                with Image.open(override) as handle:
                    return fit_square(handle, size)
            except (OSError, ValueError) as exc:
                log.warning("override logo %s unusable: %s", override, exc)

        mode = self.backing_overrides.get(team.team_id, self.backing)
        tag = "%s_%s_%s" % (self.namespace, key, hashlib.sha1(team.logo_url.encode("utf-8")).hexdigest()[:8])
        render_path = os.path.join(self.cache_dir, "render", "%s_%d_%s.png" % (tag, size, mode))
        if os.path.exists(render_path):
            try:
                with Image.open(render_path) as handle:
                    return handle.convert("RGBA")
            except (OSError, ValueError):
                pass  # corrupt cache file: rebuild below

        raw = self._raw(team, tag)
        if raw is None:
            return None
        data, kind = raw
        try:
            image = self._rasterise(data, kind, size)
        except (OSError, ValueError) as exc:
            log.warning("logo of %s not usable: %s", team.name, exc)
            self._failed[tag] = self._clock()
            return None
        if image is None:
            return None
        if mode == "always" or (mode == "auto" and is_dark_logo(image)):
            image = with_backing(image)
        if not self.read_only:
            try:
                os.makedirs(os.path.dirname(render_path), exist_ok=True)
                image.save(render_path, "PNG")
            except OSError as exc:
                log.warning("cannot write logo cache %s: %s", render_path, exc)
        return image

    def _override_path(self, key: str) -> Optional[str]:
        if not self.override_dir:
            return None
        path = os.path.join(self.override_dir, key + ".png")
        return path if os.path.exists(path) else None

    def _raw(self, team: Team, tag: str) -> Optional[Tuple[bytes, str]]:
        raw_dir = os.path.join(self.cache_dir, "raw")
        if os.path.isdir(raw_dir):
            for name in os.listdir(raw_dir):
                if name.startswith(tag + "."):
                    try:
                        with open(os.path.join(raw_dir, name), "rb") as handle:
                            data = handle.read()
                    except OSError:
                        continue
                    kind = detect_kind(data)
                    if kind:
                        return data, kind
        if self.http is None or not team.logo_url:
            return None
        failed_at = self._failed.get(tag)
        if failed_at is not None and self._clock() - failed_at < self.retry_after:
            return None
        try:
            data = self.http.get_bytes(team.logo_url)
        except HttpError as exc:
            log.warning("logo download failed for %s: %s", team.name, exc)
            self._failed[tag] = self._clock()
            return None
        kind = detect_kind(data)
        if kind is None:
            log.warning("logo of %s has an unknown format", team.name)
            self._failed[tag] = self._clock()
            return None
        try:
            os.makedirs(raw_dir, exist_ok=True)
            with open(os.path.join(raw_dir, "%s.%s" % (tag, kind)), "wb") as handle:
                handle.write(data)
        except OSError as exc:
            log.warning("cannot write logo cache: %s", exc)
        return data, kind

    def _rasterise(self, data: bytes, kind: str, size: int) -> Optional[Image.Image]:
        work = min(size * 4, 256)
        if kind == "svg":
            png = self._svg_to_png(data, work)
            if png is None:
                return None
            source = Image.open(io.BytesIO(png))
        else:
            source = Image.open(io.BytesIO(data))
        source = source.convert("RGBA")
        if max(source.size) > work:
            source.thumbnail((work, work), Image.LANCZOS)
        source = remove_white_background(source)
        return fit_square(source, size)

    def _svg_to_png(self, data: bytes, px: int) -> Optional[bytes]:
        command = [self.rsvg_command, "-a", "-w", str(px), "-h", str(px), "-f", "png"]
        try:
            result = self._runner(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if result.returncode == 0 and result.stdout:
                return result.stdout
            log.warning("rsvg-convert failed: %s", (result.stderr or b"")[:200])
        except FileNotFoundError:
            pass
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("rsvg-convert error: %s", exc)
        try:
            import cairosvg  # type: ignore

            return cairosvg.svg2png(bytestring=data, output_width=px, output_height=px)
        except Exception:  # noqa: BLE001 - optional dependency, any failure means "not available"
            log.warning("no SVG rasteriser available (install librsvg2-bin)")
            return None
