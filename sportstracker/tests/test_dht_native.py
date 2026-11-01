"""DHT22 decoding from edge timestamps and the libgpiod-2 read sequence (with a fake gpiod)."""

import unittest
from enum import Enum
from types import SimpleNamespace

from sportstracker import dht_native
from sportstracker.dht_native import DhtError, NativeDht22, decode_edges, high_pulses


def frame(humidity_raw, temperature_raw, start=1_000_000, checksum=None, drop_first=0):
    """Edges of a DHT22 answer: ~80 us low, ~80 us high, then 40 bits (low ~50 us, high 27 or 70 us)."""
    negative = temperature_raw < 0
    t = abs(temperature_raw)
    data = [humidity_raw >> 8, humidity_raw & 0xFF, ((t >> 8) & 0x7F) | (0x80 if negative else 0), t & 0xFF]
    data.append(sum(data) & 0xFF if checksum is None else checksum)
    edges, now = [], start
    edges.append((now, False))                        # sensor pulls the line low (response)
    now += 80_000
    edges.append((now, True))                         # ~80 us high before the data bits
    now += 80_000
    for byte in data:
        for i in range(7, -1, -1):
            edges.append((now, False))                # falling edge ends the previous high pulse
            now += 50_000
            edges.append((now, True))
            now += 70_000 if (byte >> i) & 1 else 27_000
    edges.append((now, False))                        # end of the last bit
    now += 50_000
    edges.append((now, True))                         # sensor releases the bus
    return edges[drop_first:]


class DecodeTests(unittest.TestCase):
    def test_decodes_temperature_and_humidity(self):
        self.assertEqual(decode_edges(frame(652, 351)), (35.1, 65.2))

    def test_negative_temperature(self):
        self.assertEqual(decode_edges(frame(400, -53)), (-5.3, 40.0))

    def test_bad_checksum(self):
        with self.assertRaises(DhtError) as ctx:
            decode_edges(frame(652, 351, checksum=0x01))
        self.assertIn("Prüfsumme", str(ctx.exception))

    def test_no_answer(self):
        with self.assertRaises(DhtError) as ctx:
            decode_edges([])
        self.assertIn("antwortet nicht", str(ctx.exception))

    def test_incomplete_frame(self):
        edges = frame(652, 351)[:-40]
        with self.assertRaises(DhtError) as ctx:
            decode_edges(edges)
        self.assertIn("unvollständig", str(ctx.exception))

    def test_missed_first_edges_are_ok(self):
        # edge detection armed late: the response edges (low / 80 us high) are missing, only the data bits count
        self.assertEqual(decode_edges(frame(652, 351, drop_first=2)), (35.1, 65.2))
        self.assertEqual(decode_edges(frame(652, 351, drop_first=3)), (35.1, 65.2))

    def test_high_pulses_pair_rising_with_next_falling(self):
        self.assertEqual(high_pulses([(0, False), (10, True), (40, False), (50, True)]), [30])


# -- the read sequence with a fake libgpiod 2 ---------------------------------------------------

class _Value(Enum):
    INACTIVE = 0
    ACTIVE = 1


class _Direction(Enum):
    INPUT = 1
    OUTPUT = 2


class _Bias(Enum):
    PULL_UP = 1


class _Edge(Enum):
    BOTH = 3


class _EventType(Enum):
    RISING_EDGE = 1
    FALLING_EDGE = 2


class FakeRequest:
    def __init__(self, owner, edges):
        self.owner = owner
        self.edges = list(edges)
        self.armed = False
        self.released = False

    def set_value(self, line, value):
        self.owner.log.append(("set", line, value))

    def reconfigure_lines(self, config):
        self.owner.log.append(("reconfigure", config))
        self.armed = True

    def wait_edge_events(self, timeout=None):
        return self.armed and bool(self.edges)

    def read_edge_events(self, max_events=None):
        taken, self.edges = self.edges, []
        return [SimpleNamespace(timestamp_ns=ts,
                                event_type=_EventType.RISING_EDGE if rising else _EventType.FALLING_EDGE)
                for ts, rising in taken]

    def release(self):
        self.released = True


class FakeChip:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def request_lines(self, config, consumer=None, event_buffer_size=None):
        if self.owner.busy:
            raise OSError(16, "Device or resource busy")
        self.owner.log.append(("request", config, consumer, event_buffer_size))
        self.owner.request = FakeRequest(self.owner, self.owner.edges)
        return self.owner.request

    def close(self):
        self.closed = True


class FakeGpiod:
    EdgeEvent = SimpleNamespace(Type=_EventType)
    line = SimpleNamespace(Bias=_Bias, Direction=_Direction, Edge=_Edge, Value=_Value)

    def __init__(self, edges, busy=False):
        self.edges, self.busy, self.log, self.request = edges, busy, [], None
        self.chip = FakeChip(self)

    def Chip(self, path):
        self.chip_path = path
        return self.chip

    @staticmethod
    def LineSettings(**kwargs):
        return SimpleNamespace(**kwargs)


class NativeReadTests(unittest.TestCase):
    def setUp(self):
        self._sleep = dht_native.time.sleep
        dht_native.time.sleep = lambda seconds: None

    def tearDown(self):
        dht_native.time.sleep = self._sleep

    def test_read_sequence(self):
        fake = FakeGpiod(frame(652, 351))
        reader = NativeDht22(25, gpiod_module=fake)
        self.assertEqual(reader.read(), (35.1, 65.2))
        kinds = [entry[0] for entry in fake.log]
        self.assertEqual(kinds, ["request", "set", "reconfigure"])
        request = fake.log[0]
        self.assertEqual(request[1][25].direction, _Direction.OUTPUT)
        self.assertEqual(request[1][25].output_value, _Value.ACTIVE)    # idle high first
        self.assertEqual(request[3], dht_native.EVENT_BUFFER)
        self.assertEqual(fake.log[1], ("set", 25, _Value.INACTIVE))     # start signal
        listen = fake.log[2][1][25]
        self.assertEqual((listen.direction, listen.bias, listen.edge_detection),
                         (_Direction.INPUT, _Bias.PULL_UP, _Edge.BOTH))
        self.assertTrue(fake.request.released)
        self.assertTrue(fake.chip.closed)
        self.assertEqual(len(reader.last_edges), len(frame(652, 351)))

    def test_no_answer_reports_wiring(self):
        fake = FakeGpiod([])
        reader = NativeDht22(25, gpiod_module=fake)
        with self.assertRaises(DhtError) as ctx:
            reader.read()
        self.assertIn("antwortet nicht", str(ctx.exception))
        self.assertTrue(fake.request.released)

    def test_busy_line_is_reported(self):
        fake = FakeGpiod([], busy=True)
        with self.assertRaises(DhtError) as ctx:
            NativeDht22(25, gpiod_module=fake).read()
        self.assertIn("belegt", str(ctx.exception))
        self.assertTrue(fake.chip.closed)

    def test_old_gpiod_is_rejected(self):
        import sys
        saved = sys.modules.get("gpiod")
        sys.modules["gpiod"] = SimpleNamespace(__version__="1.6.3")
        try:
            with self.assertRaises(DhtError) as ctx:
                NativeDht22(25).read()
        finally:
            if saved is None:
                del sys.modules["gpiod"]
            else:
                sys.modules["gpiod"] = saved
        self.assertIn("zu alt", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
