"""Picture mode (0.6.0): a small gallery of up to ``MAX_IMAGES`` pictures with rotation and transitions.

The web backend stores every upload as ``data/pictures/<id>.png`` (an animated PNG when the upload had several
frames): decoded once, composited on black, EXIF-rotated and shrunk to at most ``SOURCE_MAX`` pixels, so the
display service never has to decode a large JPEG. ``data/pictures/index.json`` holds the order and an optional
display time for each picture. The display service fits the pictures to the matrix according to the settings
(``picture.fit``, ``picture.intensity``) and shows them one after the other; between two pictures a transition
(fade, slide, wipe or a random one of them) runs.

Until 0.5.7 there was a single ``data/picture.png``; ``PictureStore.migrate`` turns it into the first gallery picture.
"""

from __future__ import annotations

import bisect
import io
import json
import logging
import math
import os
import re
import tempfile
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

LEGACY_NAME = "picture.png"
GALLERY_DIR = "pictures"
MANIFEST = "index.json"
SOURCE_MAX = 128                    # longest side of a stored picture in pixels
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 10
MAX_PIXELS = 16_000_000             # larger uploads are refused (decoding needs memory) ...
MAX_PIXELS_JPEG = 64_000_000        # ... JPEG is decoded at reduced size, so much larger photos are fine
MAX_FRAMES = 300
MIN_FRAME_MS = 20
DEFAULT_FRAME_MS = 100
FORMATS = ["JPEG", "PNG", "GIF", "WEBP"]
SECONDS_MIN, SECONDS_MAX = 3, 120
FAST_FPS = 15                       # redraws per second while a transition or an animation runs

FIT_LABELS = {
    "fill": "Ausfüllen (Mitte zuschneiden)",
    "fit": "Einpassen (schwarze Ränder)",
    "stretch": "Strecken (verzerrt)",
}
TRANSITION_LABELS = {
    "none": "Keine (harter Wechsel)",
    "fade": "Überblenden",
    "slide": "Schieben",
    "wipe": "Wischen (von oben nach unten)",
    "random": "Zufällig",
}
TRANSITION_MS_OPTIONS = [300, 500, 800, 1000, 1500, 2000]
EFFECTS = ("fade", "slide", "wipe")

_ID = re.compile(r"^[0-9a-f]{8}$")
_LOCK = threading.Lock()


class PictureError(Exception):
    """An upload that cannot be used; the message is meant for the user (German)."""


def valid_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


def _flatten(image: Image.Image) -> Image.Image:
    """RGB copy of the frame; transparent parts become black (that is what an LED matrix shows for nothing)."""
    rgba = image.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (0, 0, 0))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    flat.thumbnail((SOURCE_MAX, SOURCE_MAX), Image.LANCZOS)
    return flat


def convert_upload(data: bytes) -> Tuple[bytes, str]:
    """Check and convert an upload. Returns (PNG bytes, note for the user - may be empty)."""
    if not data:
        raise PictureError("Es wurde keine Datei übertragen.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PictureError("Die Datei ist größer als %d MB." % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        width, height = image.size
        limit = MAX_PIXELS_JPEG if image.format == "JPEG" else MAX_PIXELS
        if width * height > limit:
            raise PictureError("Das Bild ist zu groß (%d x %d Pixel). Bitte vorher verkleinern." % (width, height))
        count = getattr(image, "n_frames", 1)
        frames: List[Image.Image] = []
        durations: List[int] = []
        if count <= 1:
            if image.format == "JPEG":
                image.draft("RGB", (SOURCE_MAX * 2, SOURCE_MAX * 2))
            image.load()
            frames.append(_flatten(ImageOps.exif_transpose(image)))
        else:
            for index in range(min(count, MAX_FRAMES)):
                image.seek(index)
                image.load()
                frames.append(_flatten(image))
                duration = image.info.get("duration") or DEFAULT_FRAME_MS
                durations.append(max(MIN_FRAME_MS, int(duration)))
    except PictureError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises many kinds of errors for broken files
        log.info("upload not readable: %s", exc)
        raise PictureError("Die Datei ist kein lesbares Bild (erlaubt: JPEG, PNG, GIF, WebP).")
    out = io.BytesIO()
    if len(frames) == 1:
        frames[0].save(out, format="PNG", optimize=True)
    else:
        frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], duration=durations, loop=0)
    note = "Die Animation wurde auf die ersten %d Bilder gekürzt." % MAX_FRAMES if count > MAX_FRAMES else ""
    return out.getvalue(), note


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=".pic-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PictureStore:
    """The stored pictures of the gallery (files plus manifest). The web backend writes, the display reads."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.directory = os.path.join(data_dir, GALLERY_DIR)
        self.manifest = os.path.join(self.directory, MANIFEST)

    def path(self, image_id: str) -> Optional[str]:
        if not valid_id(image_id):
            return None
        path = os.path.join(self.directory, image_id + ".png")
        return path if os.path.isfile(path) else None

    def items(self) -> List[Dict[str, Any]]:
        """``[{"id", "name", "seconds"}]`` in display order; ``seconds`` is None for the default time."""
        try:
            with open(self.manifest, "r", encoding="utf-8") as handle:
                raw = json.load(handle)["items"]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("picture manifest unusable: %s", exc)
            return []
        items: List[Dict[str, Any]] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict) or not valid_id(str(entry.get("id", ""))) or self.path(entry["id"]) is None:
                continue
            seconds = entry.get("seconds")
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not SECONDS_MIN <= seconds <= SECONDS_MAX:
                seconds = None
            items.append({"id": entry["id"], "name": str(entry.get("name", ""))[:60], "seconds": seconds})
        return items[:MAX_IMAGES]

    def count(self) -> int:
        return len(self.items())

    def mtime(self) -> float:
        try:
            return os.path.getmtime(self.manifest)
        except OSError:
            return 0.0

    def _write(self, items: List[Dict[str, Any]]) -> None:
        _atomic_write(self.manifest, json.dumps({"version": 1, "items": items}).encode("utf-8"))

    def add(self, data: bytes, name: str = "") -> Tuple[str, str]:
        """Store an upload as the last picture. Returns (id, note)."""
        with _LOCK:
            items = self.items()
            if len(items) >= MAX_IMAGES:
                raise PictureError("Die Galerie ist voll (höchstens %d Bilder). Bitte zuerst ein Bild entfernen." % MAX_IMAGES)
            png, note = convert_upload(data)
            image_id = uuid.uuid4().hex[:8]
            _atomic_write(os.path.join(self.directory, image_id + ".png"), png)
            items.append({"id": image_id, "name": os.path.basename(name or "")[:60], "seconds": None})
            self._write(items)
        return image_id, note

    def remove(self, image_id: str) -> bool:
        with _LOCK:
            items = self.items()
            kept = [item for item in items if item["id"] != image_id]
            if len(kept) == len(items):
                return False
            self._write(kept)
            try:
                os.unlink(os.path.join(self.directory, image_id + ".png"))
            except OSError:
                pass
        return True

    def clear(self) -> int:
        with _LOCK:
            items = self.items()
            self._write([])
            for item in items:
                try:
                    os.unlink(os.path.join(self.directory, item["id"] + ".png"))
                except OSError:
                    pass
        return len(items)

    def move(self, image_id: str, delta: int) -> bool:
        with _LOCK:
            items = self.items()
            ids = [item["id"] for item in items]
            if image_id not in ids:
                return False
            index = ids.index(image_id)
            target = index + (-1 if delta < 0 else 1)
            if not 0 <= target < len(items):
                return False
            items[index], items[target] = items[target], items[index]
            self._write(items)
        return True

    def set_seconds(self, seconds: Dict[str, Optional[int]]) -> None:
        with _LOCK:
            items = self.items()
            for item in items:
                if item["id"] in seconds:
                    item["seconds"] = seconds[item["id"]]
            self._write(items)

    def migrate(self) -> bool:
        """Turn the single picture of version 0.5.x (``data/picture.png``) into the first gallery picture."""
        legacy = os.path.join(self.data_dir, LEGACY_NAME)
        if not os.path.isfile(legacy):
            return False
        with _LOCK:
            if self.items():
                return False
            image_id = uuid.uuid4().hex[:8]
            os.makedirs(self.directory, exist_ok=True)
            os.replace(legacy, os.path.join(self.directory, image_id + ".png"))
            self._write([{"id": image_id, "name": "Bild", "seconds": None}])
        return True


class _Loaded:
    def __init__(self, seconds: Optional[int]) -> None:
        self.seconds = seconds
        self.frames: List[Image.Image] = []
        self.ends: List[int] = []           # cumulative end time of each frame in ms


def _transition(effect: str, old: Image.Image, new: Image.Image, progress: float) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    width, height = new.size
    if effect == "slide":
        shift = int(round(width * progress))
        out = Image.new("RGB", new.size, (0, 0, 0))
        out.paste(old, (-shift, 0))
        out.paste(new, (width - shift, 0))
        return out
    if effect == "wipe":
        rows = int(round(height * progress))
        out = old.copy()
        if rows:
            out.paste(new.crop((0, 0, width, rows)), (0, 0))
        return out
    return Image.blend(old, new, progress)


class PictureRenderer:
    """Fits the gallery pictures to the matrix and renders the picture due at a given time.

    Reloads when the manifest changes; fitted frames are cached. ``elapsed`` is the time since the picture item of
    the display list started; the pictures run one after the other and the cycle repeats.
    """

    def __init__(self, data_dir: str, width: int, height: int) -> None:
        self.data_dir = data_dir
        self.store = PictureStore(data_dir)
        self.width = width
        self.height = height
        self._source_key: Optional[float] = None
        self._images: List[_Loaded] = []
        self._prepared_key: Any = None
        self._prepared: List[List[Image.Image]] = []
        self._cycle = 0
        self._last_elapsed = 0.0

    # -- the stored files -----------------------------------------------------------

    def mtime(self) -> float:
        return self.store.mtime()

    def available(self) -> bool:
        return self._load()

    def count(self) -> int:
        return len(self._images) if self._load() else 0

    def _load(self) -> bool:
        mtime = self.mtime()
        if mtime == self._source_key:
            return bool(self._images)
        self._source_key = mtime
        self._images, self._prepared_key, self._prepared = [], None, []
        if not mtime:
            return False
        for item in self.store.items():
            loaded = _Loaded(item["seconds"])
            try:
                with Image.open(self.store.path(item["id"]) or "", formats=["PNG"]) as image:
                    total = 0
                    for index in range(min(getattr(image, "n_frames", 1), MAX_FRAMES)):
                        image.seek(index)
                        loaded.frames.append(image.convert("RGB"))
                        total += max(MIN_FRAME_MS, int(image.info.get("duration") or DEFAULT_FRAME_MS))
                        loaded.ends.append(total)
            except Exception as exc:  # noqa: BLE001
                log.warning("picture %s unusable: %s", item["id"], exc)
                continue
            if loaded.frames:
                self._images.append(loaded)
        return bool(self._images)

    def animated(self) -> bool:
        return self._load() and any(len(image.frames) > 1 for image in self._images)

    @staticmethod
    def _frame_index(image: _Loaded, seconds: float) -> int:
        if len(image.frames) == 1:
            return 0
        position = int(seconds * 1000) % image.ends[-1]
        return min(bisect.bisect_right(image.ends, position), len(image.frames) - 1)

    def loop_seconds(self) -> float:
        """Length of the animation of a single picture (0 for stills and galleries)."""
        if not self._load() or len(self._images) != 1 or len(self._images[0].frames) < 2:
            return 0.0
        return self._images[0].ends[-1] / 1000.0

    # -- timeline -------------------------------------------------------------------

    def _layout(self, settings: Dict[str, Any], cap: Optional[float] = None) -> Tuple[List[float], float]:
        default = float(settings.get("seconds", 10))
        durations = [float(image.seconds or default) for image in self._images]
        if cap:
            durations = [min(value, cap) for value in durations]
        transition = 0.0
        if str(settings.get("transition", "fade")) != "none" and len(durations) > 1:
            transition = int(settings.get("transition_ms", 800)) / 1000.0
            if cap:
                transition = min(transition, cap / 3.0)
            transition = min(transition, min(durations) / 2.0)
        return durations, transition

    @staticmethod
    def _locate(durations: List[float], elapsed: float) -> Tuple[int, float]:
        total = sum(durations)
        position = (elapsed % total) if total > 0 else 0.0
        for index, length in enumerate(durations):
            if position < length:
                return index, position
            position -= length
        return len(durations) - 1, durations[-1]

    def total_seconds(self, settings: Dict[str, Any]) -> int:
        """Length of one full round through the gallery (the plan shows the picture item that long)."""
        if not self._load():
            return int(settings.get("seconds", 10))
        durations, _ = self._layout(settings)
        return max(SECONDS_MIN, int(math.ceil(sum(durations))))

    def _in_transition(self, settings: Dict[str, Any], index: int, offset: float, transition: float) -> bool:
        return transition > 0 and offset < transition and (index > 0 or bool(settings.get("permanent")))

    def state_key(self, settings: Dict[str, Any], elapsed: float) -> Any:
        """Changes whenever the picture at ``elapsed`` differs from the one a moment before."""
        if not self._load():
            return (self._source_key, None)
        durations, transition = self._layout(settings)
        index, offset = self._locate(durations, elapsed)
        if self._in_transition(settings, index, offset, transition):
            return (self._source_key, index, "t", int(offset * FAST_FPS))
        return (self._source_key, index, "f", self._frame_index(self._images[index], offset))

    def needs_fast_redraw(self, settings: Dict[str, Any], elapsed: float) -> bool:
        if not self._load():
            return False
        durations, transition = self._layout(settings)
        index, offset = self._locate(durations, elapsed)
        return self._in_transition(settings, index, offset, transition) or len(self._images[index].frames) > 1

    # -- fitting --------------------------------------------------------------------

    def _fit(self, image: Image.Image, fit: str) -> Image.Image:
        size = (self.width, self.height)
        if fit == "stretch":
            return image.resize(size, Image.LANCZOS)
        if fit == "fit":
            inner = ImageOps.contain(image, size, Image.LANCZOS)
            out = Image.new("RGB", size, (0, 0, 0))
            out.paste(inner, ((self.width - inner.width) // 2, (self.height - inner.height) // 2))
            return out
        return ImageOps.fit(image, size, Image.LANCZOS, centering=(0.5, 0.5))

    def _prepare(self, settings: Dict[str, Any]) -> List[List[Image.Image]]:
        key = (self._source_key, settings.get("fit"), settings.get("intensity"))
        if key != self._prepared_key:
            percent = max(5, min(100, int(settings.get("intensity", 100))))
            table = [value * percent // 100 for value in range(256)] * 3
            prepared = []
            for image in self._images:
                frames = [self._fit(frame, str(settings.get("fit", "fill"))) for frame in image.frames]
                if percent < 100:
                    frames = [frame.point(table) for frame in frames]
                prepared.append(frames)
            self._prepared, self._prepared_key = prepared, key
        return self._prepared

    def _effect(self, settings: Dict[str, Any], index: int) -> str:
        name = str(settings.get("transition", "fade"))
        if name == "random":
            return EFFECTS[(index + self._cycle) % len(EFFECTS)]
        return name if name in EFFECTS else "fade"

    def frame(self, settings: Dict[str, Any], elapsed: float = 0.0, cap: Optional[float] = None) -> Image.Image:
        """The picture for ``elapsed`` seconds into the display time (black if there is no usable picture)."""
        if not self._load():
            return Image.new("RGB", (self.width, self.height), (0, 0, 0))
        if elapsed + 0.5 < self._last_elapsed:
            self._cycle += 1                      # a new round: "random" picks other transitions
        self._last_elapsed = elapsed
        prepared = self._prepare(settings)
        durations, transition = self._layout(settings, cap)
        index, offset = self._locate(durations, elapsed)
        current = prepared[index][self._frame_index(self._images[index], offset)]
        if self._in_transition(settings, index, offset, transition):
            before = (index - 1) % len(durations)
            old = prepared[before][self._frame_index(self._images[before], durations[before])]
            return _transition(self._effect(settings, index), old, current, offset / transition)
        return current.copy()

    def preview_gif(self, settings: Dict[str, Any], factor: int = 4, fps: int = 8, cap: float = 1.2,
                    max_seconds: float = 6.0) -> bytes:
        """Enlarged LED-style GIF: a single picture (the first ``max_seconds`` of an animation) or a time-lapse
        of the whole gallery with its transitions (each picture shortened to ``cap`` seconds)."""
        from .preview import upscale  # imported late: only the web backend needs it

        if not self._load():
            raise PictureError("Kein Bild vorhanden.")
        if len(self._images) == 1:
            image = self._images[0]
            if len(image.frames) > 1:
                fps = 10
                count = max(2, int(min(max_seconds, image.ends[-1] / 1000.0) * fps))
            else:
                count = 1
            frames = [upscale(self.frame(settings, index / float(fps)), factor) for index in range(count)]
        else:
            durations, _ = self._layout(settings, cap)
            count = max(2, int(sum(durations) * fps))
            frames = [upscale(self.frame(settings, index / float(fps), cap=cap), factor) for index in range(count)]
        out = io.BytesIO()
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)
        return out.getvalue()
