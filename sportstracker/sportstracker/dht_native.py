"""DHT22 (AM2302) reader on top of libgpiod 2.x edge events.

The Adafruit library bit-bangs the protocol in Python and measures the pulses by polling the pin. That only works
while every poll is fast enough; with the libgpiod-2 backend of newer Blinka versions it is not ("A full buffer
was not returned"). This reader lets the *kernel* timestamp every edge of the sensor's answer instead, so the
speed of Python does not matter:

1. the pin is driven low for 2 ms (start signal) and then switched to input with pull-up and edge detection,
2. the sensor answers with 40 bits; every bit is a high pulse of about 27 us (0) or 70 us (1),
3. the pulse lengths are taken from the edge timestamps and decoded; the checksum is verified.

Only the standard library and ``gpiod`` (version 2) are needed - ``gpiod`` is imported when the reader is used.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, List, Optional, Sequence, Tuple

BIT_THRESHOLD_NS = 49_000     # a high pulse longer than this is a 1 (0: 26-28 us, 1: 70 us)
DATA_BITS = 40
START_LOW_SECONDS = 0.002     # the DHT22 wants the start signal low for at least 1 ms
IDLE_HIGH_SECONDS = 0.05
EVENT_GAP = timedelta(milliseconds=10)   # no edge for this long: the frame is over
MAX_WAIT_SECONDS = 0.2
EVENT_BUFFER = 256

# An edge is (timestamp in nanoseconds, True for rising / False for falling).
Edge = Tuple[int, bool]


class DhtError(RuntimeError):
    """A measurement failed (no answer, incomplete frame, wrong checksum)."""


def high_pulses(edges: Sequence[Edge]) -> List[int]:
    """Lengths (ns) of all high pulses: each rising edge paired with the next falling edge."""
    pulses: List[int] = []
    rising_at: Optional[int] = None
    for timestamp, rising in edges:
        if rising:
            rising_at = timestamp
        elif rising_at is not None:
            pulses.append(timestamp - rising_at)
            rising_at = None
    return pulses


def decode_edges(edges: Sequence[Edge]) -> Tuple[float, float]:
    """Temperature (degC) and relative humidity (%) from the edges of one frame."""
    if len(edges) < 10:
        raise DhtError("Sensor antwortet nicht (%d Flanken) - Verdrahtung, Pin und Spannung prüfen" % len(edges))
    pulses = high_pulses(edges)
    # The answer starts with one long high pulse (~80 us) before the 40 data bits. It may be missing when the
    # first edges came before the edge detection was armed, so only the last 40 pulses are taken.
    if len(pulses) < DATA_BITS:
        raise DhtError("unvollständiges Telegramm (%d von %d Bits, %d Flanken)" % (len(pulses), DATA_BITS, len(edges)))
    bits = [1 if width > BIT_THRESHOLD_NS else 0 for width in pulses[-DATA_BITS:]]
    data = []
    for byte in range(5):
        value = 0
        for bit in bits[byte * 8:byte * 8 + 8]:
            value = (value << 1) | bit
        data.append(value)
    if (data[0] + data[1] + data[2] + data[3]) & 0xFF != data[4]:
        raise DhtError("Prüfsumme falsch")
    humidity = ((data[0] << 8) | data[1]) / 10.0
    temperature = (((data[2] & 0x7F) << 8) | data[3]) / 10.0
    if data[2] & 0x80:
        temperature = -temperature
    return temperature, humidity


class NativeDht22:
    """Reads a DHT22 on one BCM GPIO. Every read opens and releases the line, so tools can share the pin."""

    def __init__(self, gpio: int, chip_path: str = "/dev/gpiochip0", gpiod_module: Any = None) -> None:
        self.gpio = gpio
        self.chip_path = chip_path
        self._gpiod = gpiod_module
        self.last_edges: List[Edge] = []

    def _module(self) -> Any:
        if self._gpiod is None:
            try:
                import gpiod  # type: ignore
            except ImportError as exc:
                raise DhtError("Python-Modul gpiod fehlt (~/venv/bin/pip install 'gpiod>=2.2.2,<3'): %s" % exc)
            if not hasattr(gpiod, "LineSettings"):
                raise DhtError("gpiod %s ist zu alt, benötigt wird Version 2 (pip install 'gpiod>=2.2.2,<3')"
                               % getattr(gpiod, "__version__", "1.x"))
            self._gpiod = gpiod
        return self._gpiod

    def capture(self) -> List[Edge]:
        """Send the start signal and collect the edges of the answer."""
        gpiod = self._module()
        line = gpiod.line
        Bias, Direction, EdgeMode, Value = line.Bias, line.Direction, line.Edge, line.Value

        try:
            chip = gpiod.Chip(self.chip_path)
        except OSError as exc:
            raise DhtError("GPIO-Chip %s nicht lesbar: %s" % (self.chip_path, exc))
        request = None
        try:
            try:
                request = chip.request_lines(
                    config={self.gpio: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)},
                    consumer="sportstracker-dht",
                    event_buffer_size=EVENT_BUFFER,
                )
            except OSError as exc:
                raise DhtError("GPIO%d ist nicht verfügbar (vom Sensordienst oder einem anderen Programm belegt?): %s"
                               % (self.gpio, exc))
            time.sleep(IDLE_HIGH_SECONDS)
            request.set_value(self.gpio, Value.INACTIVE)
            time.sleep(START_LOW_SECONDS)
            request.reconfigure_lines({
                self.gpio: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=EdgeMode.BOTH)
            })
            edges: List[Edge] = []
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            while time.monotonic() < deadline and len(edges) < EVENT_BUFFER:
                if not request.wait_edge_events(EVENT_GAP):
                    break
                for event in request.read_edge_events(EVENT_BUFFER):
                    edges.append((event.timestamp_ns, event.event_type == gpiod.EdgeEvent.Type.RISING_EDGE))
            self.last_edges = edges
            return edges
        finally:
            if request is not None:
                try:
                    request.release()
                except Exception:  # noqa: BLE001
                    pass
            try:
                chip.close()
            except Exception:  # noqa: BLE001
                pass

    def read(self) -> Tuple[float, float]:
        """One measurement as (temperature, humidity); raises DhtError."""
        return decode_edges(self.capture())

    def close(self) -> None:
        """Nothing stays open between two reads."""
