"""Photo gallery of the camera: JPEG files named after the time of the shot, limited to a size in MB.

``2026-10-28_15-42-07.jpg`` (local time; ``-2`` etc. is appended if two photos fall into the same second).
When the gallery is bigger than the limit, the oldest photos are deleted. Thumbnails live in ``.thumbs`` and
are removed together with their photo.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(?:-(\d{1,3}))?\.jpg$")
THUMB_SIZE = 160
MB = 1024 * 1024


class GalleryError(Exception):
    pass


@dataclass(frozen=True)
class Photo:
    name: str
    size: int          # bytes
    taken: datetime    # local time as given in the file name (naive)


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def _taken(name: str) -> Optional[datetime]:
    match = NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime(*[int(match.group(i)) for i in range(1, 7)])
    except ValueError:
        return None


class Gallery:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.thumbs = os.path.join(directory, ".thumbs")

    # -- reading ------------------------------------------------------------------

    def list(self) -> List[Photo]:
        """All photos, newest first."""
        photos = []
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        for name in names:
            taken = _taken(name)
            if taken is None:
                continue
            try:
                size = os.path.getsize(os.path.join(self.directory, name))
            except OSError:
                continue
            photos.append(Photo(name, size, taken))
        photos.sort(key=lambda p: (p.taken, p.name), reverse=True)
        return photos

    def usage(self) -> int:
        return sum(p.size for p in self.list())

    def path(self, name: str) -> Optional[str]:
        """Full path of an existing photo; None for unknown or invalid names (no path tricks)."""
        if not valid_name(name):
            return None
        path = os.path.join(self.directory, name)
        return path if os.path.isfile(path) else None

    # -- writing ------------------------------------------------------------------

    def save(self, data: bytes, when: datetime, limit_mb: int) -> str:
        if not data.startswith(b"\xff\xd8"):
            raise GalleryError("Kein gültiges JPEG-Bild.")
        os.makedirs(self.directory, exist_ok=True)
        base = when.strftime("%Y-%m-%d_%H-%M-%S")
        name, counter = base + ".jpg", 1
        while os.path.exists(os.path.join(self.directory, name)):
            counter += 1
            name = "%s-%d.jpg" % (base, counter)
        handle, tmp = tempfile.mkstemp(prefix=".photo-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(self.directory, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.enforce_limit(limit_mb, keep=name)
        return name

    def enforce_limit(self, limit_mb: int, keep: Optional[str] = None) -> List[str]:
        """Delete the oldest photos until the gallery fits into ``limit_mb``. Returns the deleted names."""
        limit = int(limit_mb) * MB
        photos = self.list()
        total = sum(p.size for p in photos)
        deleted = []
        for photo in reversed(photos):                       # oldest first
            if total <= limit:
                break
            if photo.name == keep:
                continue
            if self.delete(photo.name):
                total -= photo.size
                deleted.append(photo.name)
        return deleted

    def delete(self, name: str) -> bool:
        path = self.path(name)
        if path is None:
            return False
        try:
            os.unlink(path)
        except OSError:
            return False
        try:
            os.unlink(os.path.join(self.thumbs, name))
        except OSError:
            pass
        return True

    def clear(self) -> int:
        return sum(1 for photo in self.list() if self.delete(photo.name))

    # -- thumbnails ---------------------------------------------------------------

    def thumbnail(self, name: str) -> Optional[bytes]:
        path = self.path(name)
        if path is None:
            return None
        cached = os.path.join(self.thumbs, name)
        try:
            if os.path.getmtime(cached) >= os.path.getmtime(path):
                with open(cached, "rb") as handle:
                    return handle.read()
        except OSError:
            pass
        from PIL import Image  # imported late: not needed by the other services

        try:
            with Image.open(path) as image:
                image.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))   # fast JPEG down-scaling while decoding
                image = image.convert("RGB")
                image.thumbnail((THUMB_SIZE, THUMB_SIZE))
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=70)
        except Exception as exc:  # noqa: BLE001 - damaged file
            raise GalleryError("Vorschaubild nicht lesbar: %s" % exc)
        data = out.getvalue()
        try:
            os.makedirs(self.thumbs, exist_ok=True)
            with open(cached, "wb") as handle:
                handle.write(data)
        except OSError:
            pass
        return data
