import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from flask import Flask  # noqa: F401 - skip the module when Flask is missing

from sportstracker.auth import AuthStore, LoginThrottle, password_problem
from sportstracker.models import GameStatus, Team
from sportstracker.settings import apply_form, config_to_values, text_to_mapping
from sportstracker.config import parse_config
from sportstracker.state import State, write_state
from sportstracker.system import SystemControl, valid_timezone
from sportstracker.webapp import create_app

from test_services import make_game, make_table, utc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEAMS = [Team("641", "Eisbären Berlin", "EBB"), Team("2", "ERC Ingolstadt", "ING")]
RBBL_TEAMS = [Team("36365", "RB Zwickau", "ZWI"), Team("36911", "Münsterland", "MUE")]


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, cmd, wait=True, timeout=20.0):
        self.calls.append((list(cmd), wait))
        if self.fail:
            return 1, "sudo: a password is required"
        if cmd[:2] == ["systemctl", "is-active"]:
            return 0, "active\n"
        if cmd[0] == "hostname":
            return 0, "192.168.22.201 \n"
        return 0, ""


class WebTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "config"))
        shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(self.tmp, "fonts"))
        self.config_path = os.path.join(self.tmp, "config", "config.json")
        shutil.copy(os.path.join(ROOT, "config", "config.example.json"), self.config_path)
        self.runner = FakeRunner()
        self.teams_calls = []
        self.now = utc(2026, 10, 30, 12)
        self.app = create_app(self.config_path, root=self.tmp, system=SystemControl(self.runner, self.tmp),
                              teams_loader=self.load_teams, now_func=lambda: self.now,
                              throttle=LoginThrottle(max_failures=3, lockout=60, clock=lambda: 0.0))
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load_teams(self, provider, params, tz):
        self.teams_calls.append(provider)
        if provider == "genius_rbbl":
            return list(RBBL_TEAMS)
        if provider == "openligadb":
            return list(TEAMS)
        raise RuntimeError("source down")

    def token(self, path="/login"):
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def login(self, user="admin", password="admin"):
        return self.client.post("/login", data={"username": user, "password": password, "csrf_token": self.token()})

    def post(self, path, page="/", **data):
        data["csrf_token"] = self.token(page)
        return self.client.post(path, data=data)

    def config(self):
        with open(self.config_path, encoding="utf-8") as handle:
            return json.load(handle)

    def settings_form(self, **overrides):
        form = {
            "league": "del", "favorite_team_id": "641", "show_team_names": "on", "show_game_clock": "on",
            "table_label": "city", "table_logo_size": "6", "team_cities": "", "team_labels": "",
            "poll_game_seconds": "60", "poll_idle_seconds": "900", "timezone": "Europe/Berlin",
            "matrix_rows": "64", "matrix_cols": "64", "matrix_chain_length": "1", "matrix_parallel": "1",
            "matrix_gpio_slowdown": "1", "matrix_brightness": "60", "matrix_pwm_bits": "11",
            "hardware_mapping": "adafruit-hat",
            "show_other_games": "on", "idle_mode": "alternate", "rotation_main_seconds": "10",
            "rotation_card_seconds": "10", "result_window_minutes": "30", "halftime_max_minutes": "15",
            "idle_table_seconds": "10", "idle_room_seconds": "10", "idle_kickoff_seconds": "10",
            "sensor_gpio": "25", "sensor_interval_seconds": "30", "sensor_temp_offset": "0",
            "sensor_humidity_offset": "0", "camera_enabled": "on", "camera_width": "640", "camera_height": "480",
        }
        form.update(overrides)
        return {k: v for k, v in form.items() if v is not None}


class LoginTest(WebTestBase):
    def test_everything_requires_login(self):
        for path in ("/", "/settings", "/system", "/preview.png", "/password", "/logs/sportstracker-data"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/login", response.headers["Location"])

    def test_default_login_and_logout(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Standardkennwort", page)
        self.assertEqual(self.post("/logout").status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_wrong_password_and_throttle(self):
        for _ in range(3):
            self.assertEqual(self.login(password="nope").status_code, 401)
        self.assertEqual(self.login().status_code, 429)  # even the right password is refused while locked

    def test_post_without_csrf_token_is_refused(self):
        self.login()
        self.assertEqual(self.client.post("/system/reboot", data={"confirm": "yes"}).status_code, 400)
        self.assertEqual([c for c in self.runner.calls if "reboot" in c[0]], [])

    def test_auth_file_has_no_plain_password_and_is_private(self):
        path = os.path.join(self.tmp, "data", "auth.json")
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertTrue(stored["password_hash"].startswith("pbkdf2:sha256:"))
        self.assertNotIn("password", [k for k in stored if k != "password_hash" and k != "default_password"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class PasswordTest(WebTestBase):
    def test_change_password_and_login_again(self):
        self.login()
        response = self.post("/password", page="/password", username="frank", old_password="admin",
                             new_password="geheim-1234", new_password2="geheim-1234")
        self.assertEqual(response.status_code, 302)
        self.post("/logout")
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login("frank", "geheim-1234").status_code, 302)
        self.assertNotIn("Standardkennwort", self.client.get("/").get_data(as_text=True))
        # persisted: a new app instance reads the same file
        again = AuthStore(os.path.join(self.tmp, "data", "auth.json"))
        again.ensure()
        self.assertTrue(again.check("frank", "geheim-1234"))

    def test_rejects_wrong_old_mismatch_and_weak(self):
        self.login()
        base = dict(username="admin", old_password="admin", new_password="geheim-1234", new_password2="geheim-1234")
        for change in ({"old_password": "x"}, {"new_password2": "other"}, {"new_password": "kurz", "new_password2": "kurz"}):
            data = dict(base, **change)
            self.assertEqual(self.post("/password", page="/password", **data).status_code, 400, change)
        again = AuthStore(os.path.join(self.tmp, "data", "auth.json"))
        again.ensure()
        self.assertTrue(again.check("admin", "admin"))  # nothing changed

    def test_password_rules(self):
        self.assertIsNotNone(password_problem("short"))
        self.assertIsNotNone(password_problem("password"))
        self.assertIsNone(password_problem("lang-genug-1"))


class DashboardTest(WebTestBase):
    def test_dashboard_and_preview(self):
        self.login()
        write_state(os.path.join(self.tmp, "data", "state.json"),
                    State(self.now, "openligadb", "641", make_game(GameStatus.SCHEDULED), make_table()))
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("DEL", page)
        self.assertIn("preview.png", page)
        self.assertIn("sportstracker-display", self.client.get("/system").get_data(as_text=True))
        response = self.client.get("/preview.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertTrue(response.data.startswith(b"\x89PNG"))

    def test_preview_without_state_shows_message(self):
        self.login()
        self.assertEqual(self.client.get("/preview.png").status_code, 200)


class SettingsTest(WebTestBase):
    def test_page_lists_teams(self):
        self.login()
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Eisbären Berlin", page)
        self.assertIn('value="641" selected', page)

    def test_save_changes_only_known_keys(self):
        self.login()
        raw = self.config()
        raw["custom_key"] = {"keep": True}
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        response = self.post("/settings", page="/settings", **self.settings_form(
            favorite_team_id="2", show_team_names=None, table_logo_size="8", matrix_brightness="30",
            team_cities="2=Ingolstadt"))
        self.assertEqual(response.status_code, 302)
        saved = self.config()
        self.assertEqual(saved["favorite_team_id"], "2")
        self.assertFalse(saved["show_team_names"])
        self.assertEqual(saved["table_logo_size"], 8)
        self.assertEqual(saved["matrix"]["brightness"], 30)
        self.assertEqual(saved["team_cities"], {"2": "Ingolstadt"})
        self.assertEqual(saved["custom_key"], {"keep": True})
        self.assertEqual(saved["provider_params"]["league_id"], 4947)
        self.assertEqual(os.listdir(os.path.dirname(self.config_path)), ["config.json"])

    def test_league_change_needs_team_of_that_league(self):
        self.login()
        response = self.post("/settings", page="/settings", **self.settings_form(league="rbbl1", favorite_team_id="641"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.config()["provider"], "openligadb")
        response = self.post("/settings", page="/settings", **self.settings_form(league="rbbl1", favorite_team_id="36365"))
        self.assertEqual(response.status_code, 302)
        saved = self.config()
        self.assertEqual((saved["provider"], saved["provider_params"], saved["favorite_team_id"]),
                         ("genius_rbbl", {"competition_id": 49807}, "36365"))

    def test_league_selection_shows_that_leagues_teams(self):
        self.login()
        page = self.client.get("/settings?league=rbbl1").get_data(as_text=True)
        self.assertIn("RB Zwickau", page)
        self.assertNotIn("Eisbären Berlin", page)
        self.assertIn("noch nicht gespeichert", page)

    def test_invalid_values_are_refused_and_nothing_is_written(self):
        self.login()
        with open(self.config_path, encoding="utf-8") as handle:
            before = handle.read()
        for change in ({"matrix_brightness": "500"}, {"timezone": "Mars/Base"}, {"table_logo_size": "x"},
                       {"poll_game_seconds": "5"}, {"hardware_mapping": "evil"}, {"favorite_team_id": ""},
                       {"team_cities": "ohne gleichheitszeichen"}):
            response = self.post("/settings", page="/settings", **self.settings_form(**change))
            self.assertEqual(response.status_code, 400, change)
        with open(self.config_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_team_list_failure_falls_back_to_text_field(self):
        self.login()
        raw = self.config()
        raw["provider"] = "broken"
        raw["provider_params"] = {}
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Teamliste konnte nicht geladen werden", page)
        self.assertIn('<input type="text" id="favorite_team_id"', page)
        self.assertIn("Benutzerdefiniert", page)

    def test_timezone_change_also_sets_system_timezone(self):
        self.login()
        self.post("/settings", page="/settings", **self.settings_form(timezone="Europe/London"))
        self.assertIn((["sudo", "-n", "/usr/bin/timedatectl", "set-timezone", "Europe/London"], True), self.runner.calls)

    def test_teams_are_cached(self):
        self.login()
        self.client.get("/settings")
        self.client.get("/settings")
        self.assertEqual(self.teams_calls, ["openligadb"])


class SystemTest(WebTestBase):
    def test_system_page_shows_info(self):
        self.login()
        page = self.client.get("/system").get_data(as_text=True)
        self.assertIn("192.168.22.201", page)
        self.assertIn("Anzeigedienst", page)

    def test_reboot_and_shutdown_need_confirmation(self):
        self.login()
        self.post("/system/reboot", page="/system")
        self.post("/system/shutdown", page="/system")
        self.assertEqual([c for c in self.runner.calls if c[0][:2] == ["sudo", "-n"]], [])
        self.post("/system/reboot", page="/system", confirm="yes")
        self.post("/system/shutdown", page="/system", confirm="yes")
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "reboot"], False), self.runner.calls)
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "poweroff"], False), self.runner.calls)

    def test_restart_only_known_services(self):
        self.login()
        self.post("/system/restart/sportstracker-display", page="/system")
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "restart", "sportstracker-display"], True), self.runner.calls)
        self.post("/system/restart/sportstracker-web", page="/system")
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "restart", "sportstracker-web"], False), self.runner.calls)
        self.assertEqual(self.post("/system/restart/ssh", page="/system").status_code, 404)

    def test_failed_command_is_reported(self):
        self.login()
        self.runner.fail = True
        response = self.post("/system/restart/sportstracker-data", page="/system")
        page = self.client.get("/system").get_data(as_text=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("Fehlgeschlagen", page)

    def test_hostname_validation(self):
        self.login()
        self.post("/system/hostname", page="/system", hostname="sport tracker; rm -rf /")
        self.assertEqual([c for c in self.runner.calls if "hostnamectl" in " ".join(c[0])], [])
        self.post("/system/hostname", page="/system", hostname="sportstracker")
        self.assertIn((["sudo", "-n", "/usr/bin/hostnamectl", "set-hostname", "sportstracker"], True), self.runner.calls)

    def test_logs(self):
        self.login()
        self.assertEqual(self.client.get("/logs/sportstracker-data").status_code, 200)
        self.assertEqual(self.client.get("/logs/passwd").status_code, 404)


class UnitTest(unittest.TestCase):
    def test_timezone_validation(self):
        self.assertTrue(valid_timezone("Europe/Berlin"))
        for bad in ("", "Mars/Base", "../etc/passwd", "/etc/passwd"):
            self.assertFalse(valid_timezone(bad), bad)

    def test_mapping_text(self):
        errors = []
        self.assertEqual(text_to_mapping("1=A\n\n 2 = B \n", "x", errors), {"1": "A", "2": "B"})
        self.assertEqual(errors, [])
        text_to_mapping("kaputt", "x", errors)
        self.assertEqual(len(errors), 1)

    def test_apply_form_keeps_custom_provider(self):
        raw = {"provider": "other", "provider_params": {"a": 1}, "favorite_team_id": "7"}
        cfg = parse_config(raw)
        values = config_to_values(cfg)
        self.assertEqual(values["league"], "custom")
        form = {k: ("on" if v is True else str(v)) for k, v in values.items() if v is not False}
        new, errors = apply_form(raw, form)
        self.assertEqual(errors, [])
        self.assertEqual((new["provider"], new["provider_params"]), ("other", {"a": 1}))


if __name__ == "__main__":
    unittest.main()
