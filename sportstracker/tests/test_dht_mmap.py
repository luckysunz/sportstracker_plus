"""DHT22 read through the GPIO registers: sequence of register writes and decoding of a simulated frame."""

import unittest

from sportstracker import dht_mmap
from sportstracker.dht_mmap import GPCLR0, GPFSEL0, GPLEV0, GPSET0, MmapDht22, open_dht22
from sportstracker.dht_native import DhtError, NativeDht22
from test_dht_native import frame


class FakeClock:
    """Every call advances the time by one microsecond (a poll round trip)."""

    def __init__(self):
        self.now = 0

    def __call__(self):
        self.now += 1000
        return self.now


class FakeRegisters:
    """Level register of a line that follows the given edges (relative to the moment the pin is released)."""

    def __init__(self, clock, gpio, edges):
        self.clock, self.gpio, self.edges = clock, gpio, edges
        self.words = {}
        self.writes = []
        self.release_at = None
        self.closed = False

    def __getitem__(self, index):
        if index == GPLEV0:
            return self._level() << self.gpio
        return self.words.get(index, 0)

    def __setitem__(self, index, value):
        self.writes.append((index, value))
        self.words[index] = value
        function_index, shift = GPFSEL0 + self.gpio // 10, 3 * (self.gpio % 10)
        if index == function_index and (value >> shift) & 7 == 0 and self.release_at is None:
            self.release_at = self.clock.now             # the pin became an input: the sensor starts to answer

    def _level(self):
        if self.release_at is None:
            return 1
        level, elapsed = 1, self.clock.now - self.release_at
        for timestamp, rising in self.edges:
            if timestamp <= elapsed:
                level = 1 if rising else 0
        return level

    def close(self):
        self.closed = True


def make(edges, gpio=25, model="Raspberry Pi Zero 2 W Rev 1.0"):
    clock = FakeClock()
    registers = FakeRegisters(clock, gpio, edges)
    reader = MmapDht22(gpio, open_registers=lambda: registers, clock=clock, sleep=lambda s: None, model=model)
    return reader, registers


class MmapReadTests(unittest.TestCase):
    def test_reads_a_frame(self):
        reader, regs = make(frame(652, 351, start=30_000))
        temperature, humidity = reader.read()
        self.assertEqual((temperature, humidity), (35.1, 65.2))
        self.assertTrue(regs.closed)

    def test_register_sequence(self):
        reader, regs = make(frame(652, 351, start=30_000))
        reader.read()
        mask = 1 << 25
        index, shift = GPFSEL0 + 2, 15                     # GPIO25 sits in GPFSEL2, bits 15-17
        writes = [w for w in regs.writes if w[0] in (index, GPSET0, GPCLR0)]
        self.assertEqual(writes[0], (index, 1 << shift))   # output
        self.assertEqual(writes[1], (GPSET0, mask))        # idle high
        self.assertEqual(writes[2], (GPCLR0, mask))        # start signal
        self.assertEqual(writes[3], (GPSET0, mask))        # brief pull-up
        self.assertEqual(writes[4], (index, 0))            # released: input
        self.assertEqual(writes[-1], (index, 0))           # left as input at the end

    def test_other_function_bits_are_kept(self):
        reader, regs = make(frame(652, 351, start=30_000))
        regs.words[GPFSEL0 + 2] = 0b001_000_000_000_000_000_000_000_001    # GPIO20 and GPIO22 as outputs (matrix)
        before = regs.words[GPFSEL0 + 2]
        reader.read()
        self.assertEqual(regs.words[GPFSEL0 + 2], before)  # restored: only the three bits of GPIO25 were touched

    def test_no_answer(self):
        reader, _ = make([])
        with self.assertRaises(DhtError) as ctx:
            reader.read()
        self.assertIn("antwortet nicht", str(ctx.exception))

    def test_pi5_is_refused(self):
        reader, _ = make(frame(652, 351), model="Raspberry Pi 5 Model B Rev 1.0")
        with self.assertRaises(DhtError) as ctx:
            reader.read()
        self.assertIn("gpiod", str(ctx.exception))

    def test_internal_pull_up_pi_zero_and_pi4(self):
        reader, regs = make(frame(652, 351, start=30_000))
        reader.read()
        self.assertIn((dht_mmap.GPPUD, 2), regs.writes)
        self.assertIn((dht_mmap.GPPUDCLK0, 1 << 25), regs.writes)
        reader, regs = make(frame(652, 351, start=30_000), model="Raspberry Pi 4 Model B Rev 1.4")
        reader.read()
        self.assertIn((dht_mmap.GPPUPPDN0 + 1, 1 << (2 * 9)), regs.writes)      # GPIO25: register 1, bits 18-19
        self.assertNotIn(dht_mmap.GPPUD, [w[0] for w in regs.writes])

    def test_gpio_range(self):
        with self.assertRaises(DhtError):
            MmapDht22(40)


class FactoryTests(unittest.TestCase):
    def test_explicit_backends(self):
        self.assertIsInstance(open_dht22(25, "mmap"), MmapDht22)
        self.assertIsInstance(open_dht22(25, "gpiod"), NativeDht22)
        with self.assertRaises(DhtError):
            open_dht22(25, "nope")


if __name__ == "__main__":
    unittest.main()
