"""DHT22 (AM2302) reader that samples the GPIO level register directly (``/dev/gpiomem``).

The libgpiod edge events (``dht_native``) arrive only after the kernel has armed its interrupt handler. That takes
a few hundred microseconds after the pin was released - the sensor is already sending by then and the first bits
are lost (measured on a Pi Zero 2 W: 35 to 39 of 40 bits). This reader avoids the problem:

* the start signal and the release of the pin are written straight into the GPIO registers,
* right after the release the level register is polled in a tight loop (well below 2 us per round trip); every
  change of the level is timestamped with ``time.perf_counter_ns``,
* the pulse lengths (about 27 us = 0, 70 us = 1) are decoded by ``dht_native.decode_edges`` including the checksum.

Works on the Raspberry Pi 1 to 4 and Zero (not Pi 5, where the GPIO sits behind the RP1 chip). The process needs
read/write access to ``/dev/gpiomem`` (group ``gpio``), no root. A read that gets preempted is simply wrong and
fails the checksum; the sensor service tries again.
"""

from __future__ import annotations

import gc
import mmap
import os
import time
from typing import Any, Callable, List, Optional, Tuple

from .dht_native import DhtError, Edge, NativeDht22, decode_edges

# Register offsets in 32-bit words (BCM283x peripheral map, relative to the GPIO base).
GPFSEL0 = 0
GPSET0 = 7
GPCLR0 = 10
GPLEV0 = 13
GPPUD = 37
GPPUDCLK0 = 38
GPPUPPDN0 = 57          # Raspberry Pi 4 only

IDLE_HIGH_SECONDS = 0.05
START_LOW_SECONDS = 0.002
FIRST_EDGE_TIMEOUT_NS = 5_000_000     # the sensor answers within ~40 us; nothing for 5 ms = no sensor
FRAME_GAP_NS = 1_000_000              # no change for 1 ms after the answer started: the frame is over
MAX_EDGES = 100

Clock = Callable[[], int]


class _Registers:
    """The GPIO register block of ``/dev/gpiomem`` as 32-bit words."""

    def __init__(self, device: str) -> None:
        try:
            fd = os.open(device, os.O_RDWR | os.O_SYNC)
        except OSError as exc:
            raise DhtError("%s nicht lesbar (Benutzer in der Gruppe gpio? Raspberry Pi 5?): %s" % (device, exc))
        try:
            self._map = mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        finally:
            os.close(fd)
        self._words = memoryview(self._map).cast("I")

    def __getitem__(self, index: int) -> int:
        return self._words[index]

    def __setitem__(self, index: int, value: int) -> None:
        self._words[index] = value

    def close(self) -> None:
        self._words.release()
        self._map.close()


def pi_model() -> str:
    try:
        with open("/proc/device-tree/model", "rb") as handle:
            return handle.read().decode("ascii", "replace").strip("\x00 \n")
    except OSError:
        return ""


class MmapDht22:
    """Reads a DHT22 on one BCM GPIO through the GPIO registers. Nothing stays open between two reads."""

    def __init__(
        self,
        gpio: int,
        device: str = "/dev/gpiomem",
        open_registers: Optional[Callable[[], Any]] = None,
        clock: Clock = time.perf_counter_ns,
        sleep: Callable[[float], None] = time.sleep,
        model: Optional[str] = None,
    ) -> None:
        if not 0 <= gpio <= 31:
            raise DhtError("GPIO%d wird von diesem Leser nicht unterstützt (nur GPIO0 bis GPIO31)" % gpio)
        self.gpio = gpio
        self._open = open_registers or (lambda: _Registers(device))
        self._clock = clock
        self._sleep = sleep
        self._model = model
        self.last_edges: List[Edge] = []

    # -- pin setup ------------------------------------------------------------------

    def _set_function(self, regs: Any, output: bool) -> None:
        index, shift = GPFSEL0 + self.gpio // 10, 3 * (self.gpio % 10)
        value = regs[index] & ~(7 << shift)
        regs[index] = value | ((1 if output else 0) << shift)

    def _pull_up(self, regs: Any) -> None:
        """Best effort: switch the internal pull-up on (the usual breakout board has its own resistor)."""
        model = self._model if self._model is not None else pi_model()
        mask = 1 << self.gpio
        try:
            if "Raspberry Pi 4" in model or "Raspberry Pi 400" in model or "Compute Module 4" in model:
                index, shift = GPPUPPDN0 + self.gpio // 16, 2 * (self.gpio % 16)
                regs[index] = (regs[index] & ~(3 << shift)) | (1 << shift)
            else:                                    # Pi 1, 2, 3, Zero, Zero 2 W
                regs[GPPUD] = 2
                self._sleep(0.0001)
                regs[GPPUDCLK0] = mask
                self._sleep(0.0001)
                regs[GPPUD] = 0
                regs[GPPUDCLK0] = 0
        except Exception:  # noqa: BLE001 - not critical
            pass

    # -- one measurement ------------------------------------------------------------

    def _sample(self, regs: Any) -> List[Edge]:
        """Poll the level register until the sensor's answer is over; every level change is one edge."""
        clock, shift = self._clock, self.gpio
        edges: List[Edge] = []
        last = 1
        t_last = clock()
        while True:
            level = (regs[GPLEV0] >> shift) & 1
            now = clock()
            if level != last:
                edges.append((now, level == 1))
                last = level
                t_last = now
                if len(edges) >= MAX_EDGES:
                    break
            elif now - t_last > (FRAME_GAP_NS if edges else FIRST_EDGE_TIMEOUT_NS):
                break
        return edges

    def capture(self) -> List[Edge]:
        model = self._model if self._model is not None else pi_model()
        if "Raspberry Pi 5" in model:
            raise DhtError("Raspberry Pi 5: /dev/gpiomem ist nicht der GPIO-Chip - Backend gpiod verwenden")
        regs = self._open()
        mask = 1 << self.gpio
        gc_was_enabled = gc.isenabled()
        try:
            self._pull_up(regs)
            self._set_function(regs, output=True)
            regs[GPSET0] = mask
            self._sleep(IDLE_HIGH_SECONDS)
            regs[GPCLR0] = mask                      # start signal
            self._sleep(START_LOW_SECONDS)
            gc.disable()
            regs[GPSET0] = mask                      # pull up briefly ...
            self._set_function(regs, output=False)   # ... and release the bus to the sensor
            edges = self._sample(regs)
            self.last_edges = edges
            return edges
        finally:
            if gc_was_enabled and not gc.isenabled():
                gc.enable()
            try:
                self._set_function(regs, output=False)
            except Exception:  # noqa: BLE001
                pass
            if hasattr(regs, "close"):
                regs.close()

    def read(self) -> Tuple[float, float]:
        """One measurement as (temperature, humidity); raises DhtError."""
        return decode_edges(self.capture())

    def close(self) -> None:
        """Nothing stays open between two reads."""


def open_dht22(gpio: int, backend: str = "auto") -> Any:
    """The reader for the backend: ``mmap`` (registers), ``gpiod`` (kernel edge events) or ``auto``."""
    backend = (backend or "auto").lower()
    if backend == "auto":
        usable = os.path.exists("/dev/gpiomem") and "Raspberry Pi 5" not in pi_model()
        backend = "mmap" if usable else "gpiod"
    if backend == "mmap":
        return MmapDht22(gpio)
    if backend == "gpiod":
        return NativeDht22(gpio)
    raise DhtError("unbekanntes Sensor-Backend %r (auto, mmap oder gpiod)" % backend)
