"""Motion detection for the live view (Pillow only, no numpy).

The live view delivers JPEG frames. Each analysed frame is decoded at a fraction of its size (``Image.draft``
lets the JPEG decoder skip most of the work, which matters on a Pi Zero 2 W), converted to grey and blurred
slightly. It is compared with a slowly adapting reference image: pixels whose brightness differs by more than
a threshold count as changed, and if enough of the picture has changed, that is motion. A pause between two
detections keeps the gallery from filling up while somebody keeps moving.
"""

from __future__ import annotations

import io
import time
from typing import Callable, Dict, NamedTuple, Optional, Tuple

from PIL import Image, ImageChops, ImageFilter

# level -> (brightness difference 0..255 that counts as a change, share of the picture in percent that must change)
SENSITIVITY_LEVELS: Dict[int, Tuple[int, float]] = {
    1: (40, 8.0),
    2: (32, 4.0),
    3: (25, 2.0),
    4: (18, 1.0),
    5: (12, 0.5),
}
SENSITIVITY_LABELS: Dict[int, str] = {
    1: "1 – sehr unempfindlich (nur große, deutliche Bewegung)",
    2: "2 – unempfindlich",
    3: "3 – mittel",
    4: "4 – empfindlich",
    5: "5 – sehr empfindlich (auch kleine Bewegung)",
}
PAUSE_OPTIONS = [10, 30, 60, 120, 300, 600]
ANALYSIS_SIZE = (80, 60)        # the JPEG decoder picks the largest reduction that is not smaller than this
ANALYSIS_INTERVAL = 0.25        # seconds: at most four frames per second are analysed
WARMUP_FRAMES = 8               # automatic exposure settles after the start; these frames only build the reference
REFERENCE_WEIGHT = 0.25         # share of the new frame in the reference after each analysis


class MotionSettings(NamedTuple):
    sensitivity: int = 3
    pause: int = 60


def pause_label(seconds: int) -> str:
    if seconds < 60:
        return "%d Sekunden" % seconds
    minutes = seconds // 60
    return "1 Minute" if minutes == 1 else "%d Minuten" % minutes


def _grey(jpeg: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(jpeg))
    image.draft("L", ANALYSIS_SIZE)
    image = image.convert("L")
    return image.filter(ImageFilter.GaussianBlur(1))


class MotionDetector:
    def __init__(self, settings: MotionSettings, clock: Callable[[], float] = time.monotonic,
                 warmup: int = WARMUP_FRAMES) -> None:
        level = settings.sensitivity if settings.sensitivity in SENSITIVITY_LEVELS else 3
        self.threshold, self.min_area = SENSITIVITY_LEVELS[level]
        self.pause = settings.pause
        self._clock = clock
        self._warmup = warmup
        self._reference: Optional[Image.Image] = None
        self._last_fire: Optional[float] = None
        self.changed = 0.0          # share of changed pixels (percent) of the last analysed frame

    def feed(self, jpeg: bytes) -> bool:
        """Analyse one frame. True if motion was found and the pause since the last detection is over."""
        try:
            current = _grey(jpeg)
        except (OSError, ValueError, SyntaxError):
            return False            # a damaged frame: ignore it
        reference = self._reference
        if reference is None or reference.size != current.size:
            self._reference = current
            return False
        diff = ImageChops.difference(reference, current)
        mask = diff.point(lambda value, limit=self.threshold: 255 if value > limit else 0)
        width, height = current.size
        self.changed = mask.histogram()[255] * 100.0 / float(width * height)
        self._reference = Image.blend(reference, current, REFERENCE_WEIGHT)
        if self._warmup > 0:
            self._warmup -= 1
            return False
        if self.changed < self.min_area:
            return False
        now = self._clock()
        if self._last_fire is not None and now - self._last_fire < self.pause:
            return False
        self._last_fire = now
        return True
