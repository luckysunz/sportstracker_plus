"""Camera for the web backend (IMX219 on the CSI port, libcamera tools).

Two ways to use it:

* ``capture``: a still (``rpicam-still``, on older systems ``libcamera-still``) for the photo gallery.
* ``live_frames``: a live view. ``rpicam-vid --codec mjpeg`` writes JPEG frames to its standard output, they are
  cut apart here and handed to the browser as a ``multipart/x-mixed-replace`` stream. One camera process serves
  all viewers; it is started with the first viewer and stopped a few seconds after the last one has left (and
  after ``LIVE_MAX_SECONDS`` at the latest, so a forgotten browser tab does not keep the Pi busy).

The camera can only be opened by one process. A lock makes sure that stills and the live process never
overlap; while the live view runs, ``capture`` simply takes the current live frame.

Image settings (flip, brightness, sharpness) are passed to both tools as ``ImageOptions``. The live view can also
watch for motion (see ``motion.py``): when it finds some, a callback receives the current frame.

All external commands go through injectable functions (``runner`` for short commands, ``spawn`` for the long
running live process), so the tests never touch a camera.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterator, List, NamedTuple, Optional, Tuple

from .motion import ANALYSIS_INTERVAL, MotionDetector, MotionSettings
from .system import default_runner

log = logging.getLogger(__name__)

STILL_COMMANDS = ("rpicam-still", "libcamera-still")
LIST_COMMANDS = ("rpicam-hello", "libcamera-hello")
VIDEO_COMMANDS = ("rpicam-vid", "libcamera-vid")
CACHE_SECONDS = 5.0
SHOT_TIMEOUT = 25.0
LOCK_WAIT = 30.0
JPEG_MAGIC = b"\xff\xd8"
EOI = b"\xff\xd9"
FIRST_FRAME_TIMEOUT = 10.0
FRAME_TIMEOUT = 10.0
IDLE_GRACE = 5.0
LIVE_MAX_SECONDS = 1800.0
MISSING_TOOLS = "Kamera-Werkzeuge fehlen (sudo apt install rpicam-apps bzw. libcamera-apps)."

Spawn = Callable[[List[str], Any], Any]
MotionHandler = Callable[[bytes], str]


class CameraError(Exception):
    pass


class ImageOptions(NamedTuple):
    """Picture settings for ``rpicam-still`` and ``rpicam-vid``. Defaults mean: do not pass the option."""
    hflip: bool = False
    vflip: bool = False
    brightness: float = 0.0     # -1.0 .. 1.0, 0 = unchanged
    sharpness: float = 1.0      # 0 .. 16, 1 = the camera tuning's default

    def args(self) -> List[str]:
        out: List[str] = []
        if self.hflip:
            out.append("--hflip")
        if self.vflip:
            out.append("--vflip")
        if self.brightness:
            out += ["--brightness", "%g" % self.brightness]
        if self.sharpness != 1.0:
            out += ["--sharpness", "%g" % self.sharpness]
        return out


class MotionStatus(NamedTuple):
    active: bool                # motion detection runs right now
    count: int                  # detections (photos) since the live view was started
    last_name: str              # gallery name of the last motion photo ("" = none yet)
    last_age: Optional[float]   # seconds since the last detection
    changed: float              # share of the picture that changed in the last analysed frame (percent)
    threshold: float            # share (percent) from which a change counts as motion
    error: str                  # last problem with saving a motion photo


def default_spawn(cmd: List[str], stderr: Any) -> Any:
    """Start a long running process whose standard output is read by us (unbuffered)."""
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr, bufsize=0)


def split_frames(buf: bytearray) -> List[bytes]:
    """Remove all complete JPEG images from ``buf`` and return them (a partial image stays in ``buf``).

    An image runs from the start marker FFD8 to an end marker FFD9 that is followed by the next image (or by the
    end of the data); an FFD9 inside an image (embedded thumbnail) is skipped that way.
    """
    frames: List[bytes] = []
    while True:
        start = buf.find(JPEG_MAGIC)
        if start < 0:
            if buf and buf[-1] == 0xFF:
                del buf[:-1]            # might be the first half of a start marker
            else:
                buf.clear()
            return frames
        if start:
            del buf[:start]
        pos, end = 2, -1
        while True:
            found = buf.find(EOI, pos)
            if found < 0:
                break
            after = found + 2
            if after == len(buf) or buf[after:after + 2] == JPEG_MAGIC:
                end = after
                break
            if len(buf) - after < 2:
                break                   # cannot tell yet what follows
            pos = found + 2
        if end < 0:
            return frames
        frames.append(bytes(buf[:end]))
        del buf[:end]


class CameraControl:
    def __init__(
        self,
        tmp_dir: str,
        runner: Callable[..., Tuple[int, str]] = default_runner,
        clock: Callable[[], float] = time.monotonic,
        spawn: Spawn = default_spawn,
        idle_grace: float = IDLE_GRACE,
        live_max_seconds: float = LIVE_MAX_SECONDS,
    ) -> None:
        self.tmp_dir = tmp_dir
        self.runner = runner
        self._clock = clock
        self._spawn = spawn
        self._idle_grace = idle_grace
        self._live_max = live_max_seconds
        self._lock = threading.Lock()            # the camera itself: one user at a time
        self._cached: Optional[Tuple[float, Tuple[int, int, ImageOptions], bytes]] = None
        # live view state, guarded by _cond
        self._cond = threading.Condition()
        self._gen = 0
        self._proc: Any = None
        self._holding = False                    # the live process holds the camera lock
        self._frame: Optional[bytes] = None
        self._seq = 0
        self._ended = False
        self._viewers = 0
        self._key: Optional[Tuple[int, int, int, ImageOptions, Optional[MotionSettings]]] = None
        self._error = ""
        self._detector: Optional[MotionDetector] = None
        self._on_motion: Optional[MotionHandler] = None
        self._motion_count = 0
        self._motion_name = ""
        self._motion_time: Optional[float] = None
        self._motion_error = ""
        self._timer: Optional[threading.Timer] = None
        self._stderr: Any = None

    # -- status -------------------------------------------------------------------

    def status(self) -> Tuple[bool, str]:
        """(camera found, text). Uses ``rpicam-hello --list-cameras``."""
        for tool in LIST_COMMANDS:
            code, out = self.runner([tool, "--list-cameras"], timeout=15)
            if code == 127:
                continue
            text = out.strip()
            if code != 0:
                return False, text.splitlines()[-1] if text else "%s endete mit Code %d" % (tool, code)
            if "Available cameras" in text and "No cameras available" not in text:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                found = [line for line in lines if line[:1].isdigit() and ":" in line]
                return True, found[0] if found else "Kamera gefunden"
            return False, "Keine Kamera erkannt (Flachbandkabel, Kontakte und Kamera-Anschluss prüfen)."
        return False, "Kamera-Werkzeuge fehlen (sudo apt install rpicam-apps bzw. libcamera-apps)."

    # -- stills -------------------------------------------------------------------

    @property
    def live_active(self) -> bool:
        with self._cond:
            return self._proc is not None and not self._ended and self._frame is not None

    def capture(self, width: int, height: int, use_cache: bool = False,
                options: Optional[ImageOptions] = None) -> bytes:
        """A photo for the gallery: the current live frame while the live view runs, otherwise a new still."""
        with self._cond:
            if self._proc is not None and not self._ended and self._frame is not None:
                return self._frame
        return self.snapshot(width, height, use_cache=use_cache, options=options)

    def snapshot(self, width: int, height: int, use_cache: bool = True,
                 options: Optional[ImageOptions] = None) -> bytes:
        options = options or ImageOptions()
        size = (int(width), int(height), options)
        if not self._lock.acquire(timeout=LOCK_WAIT):
            raise CameraError("Die Kamera wird gerade benutzt (Live-Ansicht?). Bitte gleich noch einmal versuchen.")
        try:
            cached = self._cached
            if use_cache and cached is not None and cached[1] == size and 0 <= self._clock() - cached[0] < CACHE_SECONDS:
                return cached[2]
            data = self._shoot(size[:2], options)
            self._cached = (self._clock(), size, data)
            return data
        finally:
            self._lock.release()

    def _shoot(self, size: Tuple[int, int], options: ImageOptions) -> bytes:
        os.makedirs(self.tmp_dir, exist_ok=True)
        handle, path = tempfile.mkstemp(prefix="snapshot-", suffix=".jpg", dir=self.tmp_dir)
        os.close(handle)
        try:
            last = ""
            for tool in STILL_COMMANDS:
                cmd: List[str] = [tool, "-n", "-t", "1500", "--width", str(size[0]), "--height", str(size[1])]
                cmd += options.args()
                cmd += ["-o", path]
                code, out = self.runner(cmd, timeout=SHOT_TIMEOUT)
                if code == 127:
                    last = MISSING_TOOLS
                    continue
                if code != 0:
                    detail = out.strip().splitlines()[-1] if out.strip() else "Code %d" % code
                    raise CameraError("Aufnahme fehlgeschlagen: %s" % detail)
                with open(path, "rb") as image:
                    data = image.read()
                if not data.startswith(JPEG_MAGIC):
                    raise CameraError("Die Kamera hat kein gültiges Bild geliefert.")
                return data
            raise CameraError(last or "Aufnahme fehlgeschlagen.")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- live view ----------------------------------------------------------------

    def live_frames(self, width: int, height: int, fps: int, options: Optional[ImageOptions] = None,
                    motion: Optional[MotionSettings] = None,
                    on_motion: Optional[MotionHandler] = None) -> Iterator[bytes]:
        """Attach to the live view (starting the camera process if needed) and return an iterator of JPEG frames.

        With ``motion`` the frames are watched for movement; ``on_motion(frame)`` is called (in its own thread)
        for each detection and returns the name under which the photo was stored.
        Raises CameraError if the camera cannot be started. Closing the iterator releases the viewer.
        """
        key = (int(width), int(height), int(fps), options or ImageOptions(), motion if on_motion else None)
        with self._cond:
            if self._proc is not None and (self._key != key or self._ended):
                self._stop_locked()
            if self._proc is None:
                self._start_locked(key, on_motion)
            generation = self._gen
            self._viewers += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            ready = self._cond.wait_for(
                lambda: self._gen != generation or self._frame is not None or self._ended, timeout=FIRST_FRAME_TIMEOUT)
            if self._gen != generation or self._frame is None:
                error = self._error or ("Kein Bild von der Kamera." if ready else "Die Kamera liefert kein Bild (Zeitüberschreitung).")
                self._viewers = max(0, self._viewers - 1)
                if self._gen == generation:
                    self._stop_locked()
                raise CameraError(error)
        return self._iterate(generation)

    def stop_live(self) -> None:
        with self._cond:
            self._stop_locked()

    def _iterate(self, generation: int) -> Iterator[bytes]:
        last = 0
        try:
            while True:
                with self._cond:
                    self._cond.wait_for(
                        lambda: self._gen != generation or self._seq > last or self._ended, timeout=FRAME_TIMEOUT)
                    if self._gen != generation:
                        return
                    if self._seq > last:
                        frame, last = self._frame, self._seq
                    else:
                        return              # stream over (ended or no frames for too long)
                if frame is not None:
                    yield frame
        finally:
            self._detach(generation)

    def _detach(self, generation: int) -> None:
        with self._cond:
            if self._gen != generation:
                return
            self._viewers = max(0, self._viewers - 1)
            if self._viewers == 0 and self._proc is not None:
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = threading.Timer(self._idle_grace, self._idle_stop, args=(generation,))
                self._timer.daemon = True
                self._timer.start()

    def _idle_stop(self, generation: int) -> None:
        with self._cond:
            if self._gen == generation and self._viewers == 0:
                self._stop_locked()

    def _start_locked(self, key: Tuple[int, int, int, ImageOptions, Optional[MotionSettings]],
                      on_motion: Optional[MotionHandler] = None) -> None:
        if not self._lock.acquire(timeout=15.0):
            raise CameraError("Die Kamera wird gerade benutzt. Bitte gleich noch einmal versuchen.")
        self._holding = True
        stderr = tempfile.TemporaryFile()
        proc = None
        for tool in VIDEO_COMMANDS:
            cmd = [tool, "-n", "-t", "0", "--codec", "mjpeg", "--width", str(key[0]), "--height", str(key[1]),
                   "--framerate", str(key[2])] + key[3].args() + ["--flush", "-o", "-"]
            try:
                proc = self._spawn(cmd, stderr)
                break
            except FileNotFoundError:
                continue
            except OSError as exc:
                self._release_locked()
                stderr.close()
                raise CameraError("Live-Ansicht konnte nicht gestartet werden: %s" % exc)
        if proc is None:
            self._release_locked()
            stderr.close()
            raise CameraError(MISSING_TOOLS)
        self._gen += 1
        self._proc, self._key, self._stderr = proc, key, stderr
        self._frame, self._seq, self._ended, self._viewers, self._error = None, 0, False, 0, ""
        self._detector = MotionDetector(key[4]) if key[4] is not None and on_motion is not None else None
        self._on_motion = on_motion
        self._motion_count, self._motion_name, self._motion_time, self._motion_error = 0, "", None, ""
        thread = threading.Thread(target=self._reader, args=(proc, self._gen, self._detector), daemon=True,
                                  name="camera-live")
        thread.start()

    def _release_locked(self) -> None:
        if self._holding:
            self._holding = False
            self._lock.release()

    def _stop_locked(self) -> None:
        self._gen += 1
        proc, self._proc = self._proc, None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._frame, self._seq, self._ended, self._viewers, self._key = None, 0, False, 0, None
        self._detector, self._on_motion = None, None
        if self._stderr is not None:
            try:
                self._stderr.close()
            except OSError:
                pass
            self._stderr = None
        if proc is not None:
            threading.Thread(target=_terminate, args=(proc,), daemon=True).start()
        self._release_locked()
        self._cond.notify_all()

    def _reader(self, proc: Any, generation: int, detector: Optional[MotionDetector] = None) -> None:
        buf = bytearray()
        started = self._clock()
        analysed = float("-inf")
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                frames = split_frames(buf)
                if frames:
                    with self._cond:
                        if self._gen != generation:
                            return
                        self._frame = frames[-1]
                        self._seq += 1
                        self._cond.notify_all()
                    if detector is not None and self._clock() - analysed >= ANALYSIS_INTERVAL:
                        analysed = self._clock()
                        if detector.feed(frames[-1]):
                            threading.Thread(target=self._motion_fire, args=(frames[-1], generation), daemon=True,
                                             name="camera-motion").start()
                if self._clock() - started > self._live_max:
                    break
        except (OSError, ValueError):
            pass
        finally:
            with self._cond:
                if self._gen == generation:
                    self._ended = True
                    if self._frame is None and not self._error:
                        self._error = self._stderr_tail()
                    self._release_locked()           # the process ends now, the camera is free again
                    threading.Thread(target=_terminate, args=(proc,), daemon=True).start()
                    self._cond.notify_all()

    def _motion_fire(self, frame: bytes, generation: int) -> None:
        with self._cond:
            handler = self._on_motion if self._gen == generation else None
        if handler is None:
            return
        try:
            name = handler(frame)
        except Exception as exc:  # noqa: BLE001 - e.g. gallery write problem; the live view must go on
            log.warning("motion photo failed: %s", exc)
            with self._cond:
                if self._gen == generation:
                    self._motion_error = str(exc)
            return
        with self._cond:
            if self._gen == generation:
                self._motion_count += 1
                self._motion_name = name
                self._motion_time = self._clock()
                self._motion_error = ""

    def motion_status(self) -> MotionStatus:
        with self._cond:
            detector = self._detector
            running = self._proc is not None and not self._ended and detector is not None
            age = None if self._motion_time is None else max(0.0, self._clock() - self._motion_time)
            return MotionStatus(
                active=running, count=self._motion_count, last_name=self._motion_name, last_age=age,
                changed=detector.changed if detector is not None else 0.0,
                threshold=detector.min_area if detector is not None else 0.0, error=self._motion_error)

    def _stderr_tail(self) -> str:
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError, AttributeError):
            return ""
        return ("Kamera: " + text.splitlines()[-1]) if text else ""


def _terminate(proc: Any) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 - did not stop in time
            proc.kill()
    except Exception:  # noqa: BLE001 - already gone
        pass
