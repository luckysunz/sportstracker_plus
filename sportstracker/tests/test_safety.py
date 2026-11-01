"""Safety shutdown (0.5.4): temperature reading, record file, service logic and web backend."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from sportstracker import service_safety
from sportstracker.config import ConfigError, DEFAULT_SAFETY, parse_config
from sportstracker.safety import SafetyRecord, read_cpu_temp, read_safety, safety_path, write_safety
from sportstracker.service_safety import CONFIRM_READINGS, SafetyService
from sportstracker.settings import apply_form, config_to_values
from sportstracker.system import SERVICES

from test_web_050 import Web050Base

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = parse_config({})
        self.assertEqual(cfg.safety, {"enabled": True, "shutdown_temp_c": 84})
        self.assertEqual(DEFAULT_SAFETY["shutdown_temp_c"], 84)

    def test_partial_and_valid(self):
        cfg = parse_config({"safety": {"shutdown_temp_c": 75}})
        self.assertEqual(cfg.safety, {"enabled": True, "shutdown_temp_c": 75})
        self.assertFalse(parse_config({"safety": {"enabled": False}}).safety["enabled"])

    def test_invalid(self):
        for bad in ({"shutdown_temp_c": 86}, {"shutdown_temp_c": 59}, {"shutdown_temp_c": "84"},
                    {"shutdown_temp_c": True}, {"shutdown_temp_c": 84.5}, {"enabled": "yes"}, "x"):
            with self.assertRaises(ConfigError, msg=bad):
                parse_config({"safety": bad})

    def test_safety_is_a_known_key(self):
        self.assertNotIn("safety", parse_config({"safety": {}}).extra)


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_cpu_temp(self):
        path = os.path.join(self.tmp, "temp")
        with open(path, "w") as handle:
            handle.write("50506\n")
        self.assertEqual(read_cpu_temp(path), 50.506)
        self.assertIsNone(read_cpu_temp(os.path.join(self.tmp, "missing")))
        with open(path, "w") as handle:
            handle.write("heiss")
        self.assertIsNone(read_cpu_temp(path))

    def test_record_roundtrip(self):
        path = safety_path(os.path.join(self.tmp, "data"))
        self.assertIsNone(read_safety(path))
        write_safety(path, SafetyRecord(NOW, 84.6, 84))
        record = read_safety(path)
        self.assertEqual((record.shutdown_at, record.temperature_c, record.limit_c), (NOW, 84.6, 84))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")], [])

    def test_broken_file_is_ignored(self):
        path = os.path.join(self.tmp, "safety.json")
        for text in ("{", json.dumps({"version": 9}), json.dumps({"version": 1, "shutdown_at": "x"})):
            with open(path, "w") as handle:
                handle.write(text)
            self.assertIsNone(read_safety(path))

    def test_is_recent(self):
        record = SafetyRecord(NOW, 84.0, 84)
        self.assertTrue(record.is_recent(NOW + timedelta(hours=71)))
        self.assertFalse(record.is_recent(NOW + timedelta(hours=73)))
        self.assertFalse(record.is_recent(NOW - timedelta(hours=1)))
        self.assertFalse(SafetyRecord().is_recent(NOW))


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "config"))
        self.config_path = os.path.join(self.tmp, "config", "config.json")
        self.write_config({})
        self.temps = []
        self.off_calls = 0
        self.off_ok = True
        self.ticks = 0.0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, data):
        with open(self.config_path, "w") as handle:
            json.dump(data, handle)

    def power_off(self):
        self.off_calls += 1
        return self.off_ok

    def clock(self):
        self.ticks += 1000.0
        return self.ticks

    def service(self):
        return SafetyService(self.config_path, root=self.tmp, now_func=lambda: NOW,
                             temp_reader=lambda: self.temps.pop(0) if self.temps else None,
                             power_off=self.power_off, clock=self.clock)

    def run_cycles(self, service, temps):
        self.temps = list(temps)
        for _ in temps:
            service.cycle()

    def record(self):
        return read_safety(safety_path(os.path.join(self.tmp, "data")))

    def test_cool_cpu_does_nothing(self):
        service = self.service()
        self.run_cycles(service, [45.0, 70.0, 83.9] * 3)
        self.assertEqual(self.off_calls, 0)
        self.assertIsNone(self.record())

    def test_shutdown_after_confirmed_readings(self):
        service = self.service()
        self.run_cycles(service, [84.0] * (CONFIRM_READINGS - 1))
        self.assertEqual(self.off_calls, 0)
        self.run_cycles(service, [84.5])
        self.assertEqual(self.off_calls, 1)
        record = self.record()
        self.assertEqual((record.shutdown_at, record.temperature_c, record.limit_c), (NOW, 84.5, 84))
        self.assertTrue(service.shutdown_started)

    def test_single_spike_is_ignored(self):
        service = self.service()
        self.run_cycles(service, [85.0, 85.0, 70.0, 85.0, 85.0, 70.0])
        self.assertEqual(self.off_calls, 0)

    def test_limit_from_configuration(self):
        self.write_config({"safety": {"shutdown_temp_c": 70}})
        service = self.service()
        self.run_cycles(service, [70.0] * CONFIRM_READINGS)
        self.assertEqual(self.off_calls, 1)
        self.assertEqual(self.record().limit_c, 70)

    def test_disabled_never_shuts_down(self):
        self.write_config({"safety": {"enabled": False}})
        service = self.service()
        self.run_cycles(service, [90.0] * 10)
        self.assertEqual(self.off_calls, 0)

    def test_enabling_later_starts_counting_fresh(self):
        self.write_config({"safety": {"enabled": False}})
        service = self.service()
        self.run_cycles(service, [90.0] * 5)
        self.write_config({"safety": {"enabled": True}})
        self.run_cycles(service, [90.0] * (CONFIRM_READINGS - 1))
        self.assertEqual(self.off_calls, 0)

    def test_unreadable_temperature_is_no_shutdown(self):
        service = self.service()
        self.run_cycles(service, [90.0, 90.0])
        self.temps = [None]
        service.cycle()
        self.assertEqual(self.off_calls, 0)

    def test_broken_configuration_keeps_default_protection(self):
        with open(self.config_path, "w") as handle:
            handle.write("{not json")
        service = self.service()
        self.run_cycles(service, [84.0] * CONFIRM_READINGS)
        self.assertEqual(self.off_calls, 1)
        self.write_config({"safety": {"shutdown_temp_c": 99}})
        service = self.service()
        self.assertEqual(service.settings()[:2], (True, 84))

    def test_failed_poweroff_is_retried(self):
        self.off_ok = False
        service = self.service()
        self.temps = [86.0] * (CONFIRM_READINGS + 1)
        for _ in range(CONFIRM_READINGS):
            delay = service.cycle()
        self.assertEqual(delay, service_safety.RETRY_SECONDS)
        self.assertFalse(service.shutdown_started)
        service.cycle()
        self.assertEqual(self.off_calls, 2)

    def test_record_failure_does_not_block_shutdown(self):
        os.makedirs(os.path.join(self.tmp, "data"))
        os.chmod(os.path.join(self.tmp, "data"), 0o500)
        try:
            service = self.service()
            self.run_cycles(service, [90.0] * CONFIRM_READINGS)
        finally:
            os.chmod(os.path.join(self.tmp, "data"), 0o700)
        self.assertEqual(self.off_calls, 1)

    def test_unit_file_and_install_script(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "deploy", "sportstracker-safety.service"), encoding="utf-8") as handle:
            unit = handle.read()
        self.assertIn("User=root", unit)
        self.assertIn("sportstracker.service_safety", unit)
        self.assertIn("Restart=always", unit)
        with open(os.path.join(root, "deploy", "install.sh"), encoding="utf-8") as handle:
            self.assertIn("sportstracker-safety", handle.read())
        with open(os.path.join(root, "deploy", "sportstracker-sudoers"), encoding="utf-8") as handle:
            self.assertIn("restart sportstracker-safety", handle.read())
        self.assertIn("sportstracker-safety", SERVICES)


class WebTest(Web050Base):
    def test_settings_page_has_section(self):
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Temperaturschutz", page)
        self.assertIn('name="safety_shutdown_temp_c"', page)
        self.assertIn('value="84"', page)

    def test_save_limit_and_disable(self):
        self.post("/settings", page="/settings", **self.settings_form(safety_shutdown_temp_c="80"))
        self.assertEqual(self.config()["safety"], {"enabled": False, "shutdown_temp_c": 80})   # box not ticked
        self.post("/settings", page="/settings", **self.settings_form(safety_shutdown_temp_c="82", safety_enabled="on"))
        self.assertEqual(self.config()["safety"], {"enabled": True, "shutdown_temp_c": 82})

    def test_form_without_safety_fields_leaves_section_alone(self):
        self.set_config(safety={"enabled": False, "shutdown_temp_c": 77})
        self.post("/settings", page="/settings", **self.settings_form())
        self.assertEqual(self.config()["safety"], {"enabled": False, "shutdown_temp_c": 77})

    def test_invalid_limit_is_refused(self):
        before = self.config()
        for value in ("86", "59", "abc", ""):
            response = self.post("/settings", page="/settings", **self.settings_form(safety_shutdown_temp_c=value, safety_enabled="on"))
            self.assertEqual(response.status_code, 400, value)
        self.assertEqual(self.config(), before)

    def test_values_roundtrip(self):
        values = config_to_values(parse_config({"safety": {"enabled": False, "shutdown_temp_c": 79}}))
        self.assertEqual((values["safety_enabled"], values["safety_shutdown_temp_c"]), (False, "79"))
        new, errors = apply_form({}, {"favorite_team_id": "1", "idle_mode": "alternate", "safety_shutdown_temp_c": "81",
                                      "safety_enabled": "on"})
        self.assertEqual(new["safety"], {"enabled": True, "shutdown_temp_c": 81})

    def test_system_page(self):
        page = self.client.get("/system").get_data(as_text=True)
        self.assertIn("Temperaturschutz", page)
        self.assertIn("aktiv – Abschaltung ab 84", page)
        self.assertIn("Sicherheitsdienst", page)
        self.assertNotIn("Letzte Abschaltung", page)
        write_safety(safety_path(os.path.join(self.tmp, "data")), SafetyRecord(self.now - timedelta(hours=2), 84.7, 84))
        page = self.client.get("/system").get_data(as_text=True)
        self.assertIn("Letzte Abschaltung", page)
        self.assertIn("84,7", page)
        self.set_config(safety={"enabled": False, "shutdown_temp_c": 84})
        self.assertIn("ausgeschaltet", self.client.get("/system").get_data(as_text=True))

    def test_restart_safety_service(self):
        self.post("/system/restart/sportstracker-safety", page="/system")
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "restart", "sportstracker-safety"], True), self.runner.calls)

    def test_dashboard_warns_after_recent_shutdown(self):
        self.assertNotIn("Sicherheitsdienst hat", self.client.get("/").get_data(as_text=True))
        write_safety(safety_path(os.path.join(self.tmp, "data")), SafetyRecord(self.now - timedelta(hours=5), 84.2, 84))
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Sicherheitsdienst hat den Raspberry Pi", page)
        self.assertIn("84,2", page)
        write_safety(safety_path(os.path.join(self.tmp, "data")), SafetyRecord(self.now - timedelta(days=5), 84.2, 84))
        self.assertNotIn("Sicherheitsdienst hat", self.client.get("/").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
