import os
import unittest
from datetime import datetime, timedelta, timezone

from sportstracker.sensor import read_sensor
from sportstracker.service_sensor import Dht22Reader, SensorError, SensorService, plausible

from test_services import Env

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class ScriptedReader:
    """Plays back a list of (temperature, humidity) tuples or exceptions."""

    def __init__(self, script):
        self.script = script   # shared with the test, which refills it
        self.reads = 0
        self.closed = False

    def read(self):
        self.reads += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class SensorServiceTest(unittest.TestCase):
    def setUp(self):
        self.env = Env(sensor={"enabled": True, "gpio": 25, "interval_seconds": 30})
        self.now = [T0]
        self.readers = []
        self.script = []
        self.sleeps = []
        self.path = os.path.join(self.env.root, "data", "sensor.json")

        def factory(gpio):
            reader = ScriptedReader(self.script)
            self.readers.append((gpio, reader))
            return reader

        self.service = SensorService(self.env.config_path, root=self.env.root, now_func=lambda: self.now[0],
                                     reader_factory=factory, sleep=self.sleeps.append)

    def tearDown(self):
        self.env.close()

    def read(self, *values):
        self.script[:] = list(values)
        return self.service.cycle()

    def test_writes_reading_and_returns_interval(self):
        self.assertEqual(self.read((21.54, 48.2)), 30)
        r = read_sensor(self.path)
        self.assertEqual((r.temperature_c, r.humidity_pct, r.updated_at, r.failures), (21.5, 48.2, T0, 0))
        self.assertIsNone(r.error)
        self.assertEqual(self.readers[0][0], 25)

    def test_offsets_are_applied(self):
        self.env.write_config(favorite_team_id="1", sensor={"enabled": True, "temp_offset": -2.5, "humidity_offset": 3})
        self.read((24.0, 40.0))
        r = read_sensor(self.path)
        self.assertEqual((r.temperature_c, r.humidity_pct), (21.5, 43.0))

    def test_retries_within_a_round(self):
        self.read(SensorError("checksum"), SensorError("timing"), (20.0, 50.0))
        self.assertEqual(read_sensor(self.path).temperature_c, 20.0)
        self.assertEqual(self.sleeps, [2.5, 2.5])

    def test_failed_round_keeps_last_good_value(self):
        self.read((20.0, 50.0))
        self.now[0] = T0 + timedelta(seconds=30)
        self.read(SensorError("a"), SensorError("b"), SensorError("c"))
        r = read_sensor(self.path)
        self.assertEqual((r.temperature_c, r.updated_at, r.failures, r.error), (20.0, T0, 1, "c"))
        self.read(SensorError("a"), SensorError("b"), SensorError("d"))
        self.assertEqual(read_sensor(self.path).failures, 2)
        self.now[0] = T0 + timedelta(seconds=90)
        self.read((20.5, 49.0))
        r = read_sensor(self.path)
        self.assertEqual((r.failures, r.error, r.updated_at), (0, None, self.now[0]))

    def test_implausible_values_are_rejected(self):
        self.assertFalse(plausible(120.0, 50.0))
        self.assertFalse(plausible(20.0, 101.0))
        self.read((120.0, 50.0), (20.0, 150.0), (-60.0, 10.0))
        r = read_sensor(self.path)
        self.assertIsNone(r.temperature_c)
        self.assertEqual(r.failures, 1)
        self.assertIn("unplausibler", r.error)

    def test_single_spike_is_held_back_but_a_confirmed_jump_is_taken(self):
        self.read((21.0, 50.0))
        self.read((35.0, 50.0))                     # spike: not accepted
        self.assertEqual(read_sensor(self.path).temperature_c, 21.0)
        self.read((21.2, 50.0))                     # back to normal -> the spike is forgotten
        self.assertEqual(read_sensor(self.path).temperature_c, 21.2)
        self.read((30.0, 50.0))                     # a real jump...
        self.assertEqual(read_sensor(self.path).temperature_c, 21.2)
        self.read((30.4, 50.0))                     # ...is confirmed by the next reading
        self.assertEqual(read_sensor(self.path).temperature_c, 30.4)

    def test_disabled_does_nothing_and_closes_reader(self):
        self.read((20.0, 50.0))
        reader = self.readers[0][1]
        self.env.write_config(favorite_team_id="1", sensor={"enabled": False})
        self.assertEqual(self.service.cycle(), 30)
        self.assertTrue(reader.closed)

    def test_gpio_change_opens_a_new_reader(self):
        self.read((20.0, 50.0))
        self.env.write_config(favorite_team_id="1", sensor={"enabled": True, "gpio": 19})
        self.read((20.0, 50.0))
        self.assertEqual([g for g, _ in self.readers], [25, 19])
        self.assertTrue(self.readers[0][1].closed)

    def test_missing_library_is_a_reported_error(self):
        service = SensorService(self.env.config_path, root=self.env.root, now_func=lambda: self.now[0],
                                reader_factory=Dht22Reader, sleep=self.sleeps.append)
        service.cycle()
        r = read_sensor(self.path)
        self.assertEqual(r.failures, 1)
        self.assertTrue(r.error)


if __name__ == "__main__":
    unittest.main()
