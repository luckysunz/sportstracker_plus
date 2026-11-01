"""WLAN setup (0.7.0): nmcli wrapper, service state machine, status/request files, setup page, matrix screen."""

import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from PIL import Image

from sportstracker.config import ConfigError, parse_config
from sportstracker.render import Renderer, load_fonts
from sportstracker.service_display import DisplayService
from sportstracker.service_wifi import RETRY_SECONDS, WifiService
from sportstracker.system import SERVICES
from sportstracker.wifi import (HOTSPOT_CONNECTION, Link, Network, Nmcli, check_credentials, hotspot_active, is_fresh,
                                qr_escape, read_status, request_path, split_terse, take_request, wifi_path, wifi_qr_text,
                                write_request, write_status)
from sportstracker.wifi_screen import _wrap, draw_qr, qr_modules, render_setup

from test_services import ROOT, Env, FakeDisplay
from test_web_050 import Web050Base

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


class ParseTests(unittest.TestCase):
    def test_terse_split_handles_escaped_colons(self):
        self.assertEqual(split_terse(r"Mein\:Netz:80:WPA2"), ["Mein:Netz", "80", "WPA2"])
        self.assertEqual(split_terse(r"a\\b:c"), ["a\\b", "c"])
        self.assertEqual(split_terse("x::"), ["x", "", ""])

    def test_credentials_check(self):
        self.assertIsNone(check_credentials("Heim", "geheim123"))
        self.assertIsNone(check_credentials("Offen", ""))
        for ssid, pw in (("", "geheim123"), ("x" * 33, "geheim123"), ("Heim", "kurz"), ("Heim", "x" * 64),
                         ("Hei\nm", "geheim123"), ("Heim", "ge\nheim123")):
            self.assertIsNotNone(check_credentials(ssid, pw), (ssid, pw))

    def test_qr_text_is_escaped(self):
        self.assertEqual(qr_escape('a;b,c:d\\e"f'), 'a\\;b\\,c\\:d\\\\e\\"f')
        self.assertEqual(wifi_qr_text("Sportstracker-Setup", "setup1234"), "WIFI:T:WPA;S:Sportstracker-Setup;P:setup1234;;")

    def test_config_section(self):
        wifi = parse_config({}).wifi
        self.assertEqual((wifi["hotspot_ssid"], wifi["pin"], wifi["grace_seconds"], wifi["enabled"]),
                         ("Sportstracker-Setup", "1234", 180, True))
        for bad in ({"pin": "12"}, {"pin": "abcd"}, {"pin": 1234}, {"hotspot_password": "kurz"}, {"hotspot_ssid": ""},
                    {"grace_seconds": 5}, {"enabled": "ja"}):
            with self.assertRaises(ConfigError):
                parse_config({"wifi": bad})
        self.assertEqual(parse_config({"wifi": {"pin": "987654"}}).wifi["pin"], "987654")

    def test_service_is_listed(self):
        self.assertIn("sportstracker-wifi", SERVICES)
        with open(os.path.join(ROOT, "deploy", "sportstracker-sudoers"), encoding="utf-8") as handle:
            self.assertIn("restart sportstracker-wifi", handle.read())


class FakeRunner:
    """Stands in for subprocess: answers nmcli calls from a table, records every call."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        for prefix, answer in self.answers.items():
            if " ".join(argv).startswith(prefix):
                return answer
        return 0, ""


class NmcliTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.nm = Nmcli(self.runner)

    def test_link(self):
        self.runner.answers["nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device"] = (
            0, "eth0:ethernet:unavailable:\nwlan0:wifi:connected:Heim\\:WLAN\nlo:loopback:unmanaged:\n")
        link = self.nm.link()
        self.assertEqual((link.device, link.connected, link.connection), ("wlan0", True, "Heim:WLAN"))
        self.runner.answers["nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device"] = (1, "")
        self.assertIsNone(self.nm.link())

    def test_ssid_of_the_active_network(self):
        self.runner.answers["nmcli -t -f ACTIVE,SSID device wifi list"] = (0, "no:Nachbar\nyes:Heim\\:WLAN\n")
        self.assertEqual(self.nm.ssid("wlan0"), "Heim:WLAN")
        self.runner.answers["nmcli -t -f ACTIVE,SSID device wifi list"] = (0, "no:Nachbar\n")
        self.assertEqual(self.nm.ssid("wlan0"), "")

    def test_scan_keeps_the_strongest_entry_per_name_and_sorts(self):
        self.runner.answers["nmcli -t -f SSID,SIGNAL,SECURITY device wifi list"] = (
            0, "Heim:55:WPA2\nHeim:80:WPA2\n:30:WPA2\nNachbar:60:WPA1 WPA2\nOffen:40:\n--:20:WPA2\n")
        found = self.nm.scan("wlan0")
        self.assertEqual([(n.ssid, n.signal, n.secured) for n in found],
                         [("Heim", 80, True), ("Nachbar", 60, True), ("Offen", 40, False)])
        self.assertIn("--rescan", self.runner.calls[0])

    def test_connect_updates_an_existing_profile(self):
        self.runner.answers["nmcli -t -f NAME,TYPE connection show"] = (0, "Heim Profil:802-11-wireless\nKabel:802-3-ethernet\n")
        self.runner.answers["nmcli -t -g 802-11-wireless.ssid connection show id Heim Profil"] = (0, "Heim")
        ok, message = self.nm.connect("wlan0", "Heim", "neues-passwort")
        self.assertTrue(ok, message)
        joined = [" ".join(call) for call in self.runner.calls]
        self.assertTrue(any("connection modify id Heim Profil wifi-sec.key-mgmt wpa-psk wifi-sec.psk neues-passwort" in c for c in joined))
        self.assertTrue(any(c.startswith("nmcli connection up id Heim Profil") for c in joined))
        self.assertFalse(any("device wifi connect" in c for c in joined))

    def test_profile_of_a_network_name_with_a_colon_is_found(self):
        self.runner.answers["nmcli -t -f NAME,TYPE connection show"] = (0, "preconfigured:802-11-wireless\n")
        self.runner.answers["nmcli -t -g 802-11-wireless.ssid connection show id preconfigured"] = (0, "HECHT\\:IOT")
        ok, message = self.nm.connect("wlan0", "HECHT:IOT", "neues-passwort")
        self.assertTrue(ok, message)
        joined = [" ".join(call) for call in self.runner.calls]
        self.assertTrue(any(c.startswith("nmcli connection up id preconfigured") for c in joined))
        self.assertFalse(any("device wifi connect" in c for c in joined))

    def test_connect_creates_a_profile_for_a_new_network(self):
        ok, _ = self.nm.connect("wlan0", "Neu", "geheim123", hidden=True)
        self.assertTrue(ok)
        joined = [" ".join(call) for call in self.runner.calls]
        self.assertTrue(any(c.startswith("nmcli device wifi connect Neu ifname wlan0 password geheim123 hidden yes") for c in joined))

    def test_connect_reports_a_wrong_password_in_german(self):
        self.runner.answers["nmcli device wifi connect"] = (4, "Error: Connection activation failed: Secrets were required, but not provided.")
        ok, message = self.nm.connect("wlan0", "Neu", "falsch1234")
        self.assertFalse(ok)
        self.assertIn("Kennwort", message)

    def test_connect_refuses_bad_input_without_calling_nmcli(self):
        ok, message = self.nm.connect("wlan0", "Neu", "kurz")
        self.assertFalse(ok)
        self.assertEqual(self.runner.calls, [])

    def test_hotspot_is_created_without_autoconnect(self):
        ok, _ = self.nm.start_hotspot("wlan0", "Sportstracker-Setup", "setup1234")
        self.assertTrue(ok)
        joined = [" ".join(call) for call in self.runner.calls]
        self.assertTrue(any("device wifi hotspot ifname wlan0 con-name %s ssid Sportstracker-Setup password setup1234" % HOTSPOT_CONNECTION in c for c in joined))
        self.assertTrue(any("connection.autoconnect no" in c for c in joined))


class FakeNm:
    """Scripted NetworkManager for the service tests."""

    def __init__(self):
        self.link_value = Link("wlan0", "connected", "Heim")
        self.networks = [Network("Heim", 80, True), Network("Nachbar", 50, True)]
        self.connect_ok = True
        self.events = []
        self.ssid_value = ""
        self.after_stop = None          # Link to report after the hotspot was stopped (known WLAN is back)
        self.runner = lambda argv, timeout: (0, "")

    def link(self):
        return self.link_value

    def ip_address(self, device):
        return "192.168.22.22"

    def ssid(self, device):
        return self.ssid_value or self.link_value.connection

    def scan(self, device):
        self.events.append("scan")
        return list(self.networks)

    def start_hotspot(self, device, ssid, password):
        self.events.append(("hotspot", ssid, password))
        self.link_value = Link(device, "connected", HOTSPOT_CONNECTION)
        return True, ""

    def stop_hotspot(self):
        self.events.append("stop")
        self.link_value = self.after_stop or Link("wlan0", "disconnected", "")

    def connect(self, device, ssid, password, hidden=False):
        self.events.append(("connect", ssid, password, hidden))
        if self.connect_ok:
            self.link_value = Link(device, "connected", ssid)
            return True, ""
        return False, "Das Kennwort wurde nicht akzeptiert."

    def reconnect(self, name, device):
        self.events.append(("reconnect", name))
        self.link_value = Link(device, "connected", name)
        return True


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.nm = FakeNm()
        self.now = [T0]
        self.clients = [0]
        self.service = WifiService(self.env.config_path, nm=self.nm, root=self.env.root, now_func=lambda: self.now[0],
                                   sleeper=lambda seconds: None, clients_func=lambda device: self.clients[0])
        self.status_file = wifi_path(os.path.join(self.env.root, "data"))
        self.request_file = request_path(os.path.join(self.env.root, "data"))

    def tearDown(self):
        self.env.close()

    def advance(self, seconds):
        self.now[0] = self.now[0] + timedelta(seconds=seconds)

    def status(self):
        return read_status(self.status_file)

    def go_offline(self):
        self.nm.link_value = Link("wlan0", "disconnected", "")

    def test_connected_writes_status_and_scans_once(self):
        self.service.step()
        status = self.status()
        self.assertEqual((status["mode"], status["connection"], status["ip"]), ("client", "Heim", "192.168.22.22"))
        self.assertEqual([n["ssid"] for n in status["networks"]], ["Heim", "Nachbar"])
        self.service.step()
        self.assertEqual(self.nm.events.count("scan"), 1)

    def test_status_shows_the_network_name_not_the_profile_name(self):
        self.nm.link_value = Link("wlan0", "connected", "preconfigured")
        self.nm.ssid_value = "Heim"
        self.service.step()
        self.assertEqual(self.status()["connection"], "Heim")

    def test_hotspot_only_after_the_grace_period(self):
        self.go_offline()
        self.service.step()
        self.assertEqual(self.status()["mode"], "offline")
        self.advance(120)
        self.service.step()
        self.assertEqual(self.status()["mode"], "offline")
        self.advance(70)                                              # 190 s > 180 s
        self.service.step()
        status = self.status()
        self.assertEqual(status["mode"], "hotspot")
        self.assertEqual((status["hotspot"]["ssid"], status["hotspot"]["password"]), ("Sportstracker-Setup", "setup1234"))
        self.assertIn("10.42.0.1", status["hotspot"]["url"])
        self.assertEqual([n["ssid"] for n in status["networks"]], ["Heim", "Nachbar"])   # scanned before the access point
        self.assertTrue(hotspot_active(status, self.now[0]))

    def test_short_dropout_does_not_open_the_hotspot(self):
        self.go_offline()
        self.service.step()
        self.advance(60)
        self.nm.link_value = Link("wlan0", "connected", "Heim")
        self.service.step()
        self.advance(300)
        self.service.step()
        self.assertEqual(self.status()["mode"], "client")
        self.assertFalse(any(isinstance(e, tuple) and e[0] == "hotspot" for e in self.nm.events))

    def test_disabled_never_opens_the_hotspot(self):
        self.env.write_config(wifi={"enabled": False})
        self.go_offline()
        self.service.step()
        self.advance(1000)
        self.service.step()
        self.assertEqual(self.status()["mode"], "offline")

    def open_hotspot(self):
        self.go_offline()
        self.service.step()
        self.advance(200)
        self.service.step()
        self.assertEqual(self.status()["mode"], "hotspot")

    def test_request_in_hotspot_mode_connects_and_closes_the_hotspot(self):
        self.open_hotspot()
        write_request(self.request_file, "Heim", "neues-passwort", False, self.now[0])
        self.advance(5)
        self.service.step()
        status = self.status()
        self.assertEqual((status["mode"], status["connection"]), ("client", "Heim"))
        self.assertTrue(status["last_result"]["ok"])
        self.assertIn(("connect", "Heim", "neues-passwort", False), self.nm.events)
        self.assertFalse(os.path.exists(self.request_file))                # the password is not kept

    def test_failed_request_brings_the_hotspot_back_with_the_reason(self):
        self.open_hotspot()
        self.nm.connect_ok = False
        write_request(self.request_file, "Heim", "falsches-passwort", False, self.now[0])
        self.service.step()
        status = self.status()
        self.assertEqual(status["mode"], "hotspot")
        self.assertFalse(status["last_result"]["ok"])
        self.assertIn("Kennwort", status["last_result"]["message"])

    def test_failed_request_while_connected_returns_to_the_old_network(self):
        self.service.step()
        self.nm.connect_ok = False
        write_request(self.request_file, "Nachbar", "falsches-passwort", False, self.now[0])
        self.service.step()
        self.assertIn(("reconnect", "Heim"), self.nm.events)
        self.assertEqual(self.status()["mode"], "client")

    def test_unusable_request_file_is_dropped(self):
        os.makedirs(os.path.dirname(self.request_file), exist_ok=True)
        with open(self.request_file, "w") as handle:
            handle.write("{kaputt")
        self.service.step()
        self.assertFalse(os.path.exists(self.request_file))
        self.assertEqual(self.status()["mode"], "client")

    def test_idle_hotspot_looks_for_the_old_wlan_but_not_with_a_client_connected(self):
        self.open_hotspot()
        self.clients[0] = 1
        self.advance(RETRY_SECONDS + 5)
        self.service.step()
        self.assertEqual(self.status()["mode"], "hotspot")                # someone is entering a password
        self.clients[0] = 0
        self.nm.after_stop = Link("wlan0", "connected", "Heim")
        self.service.step()
        status = self.status()
        self.assertEqual((status["mode"], status["connection"]), ("client", "Heim"))
        self.assertTrue(status["last_result"]["ok"])

    def test_idle_hotspot_comes_back_if_the_wlan_is_still_missing(self):
        self.open_hotspot()
        self.advance(RETRY_SECONDS + 5)
        self.service.step()
        self.assertEqual(self.status()["mode"], "hotspot")
        self.assertEqual(sum(1 for e in self.nm.events if isinstance(e, tuple) and e[0] == "hotspot"), 2)

    def test_without_networkmanager_the_status_says_so(self):
        self.nm.link = lambda: None
        self.service.step()
        self.assertEqual(self.status()["mode"], "unavailable")

    def test_status_goes_stale(self):
        self.service.step()
        status = self.status()
        self.assertTrue(is_fresh(status, self.now[0] + timedelta(seconds=30)))
        self.assertFalse(is_fresh(status, self.now[0] + timedelta(seconds=300)))
        self.assertFalse(is_fresh(None, self.now[0]))


class FilesTests(unittest.TestCase):
    def test_request_roundtrip_and_permissions(self):
        env = Env()
        try:
            path = request_path(os.path.join(env.root, "data"))
            write_request(path, "Heim", "geheim123", True, T0)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            request = take_request(path)
            self.assertEqual((request["ssid"], request["password"], request["hidden"]), ("Heim", "geheim123", True))
            self.assertIsNone(take_request(path))
        finally:
            env.close()


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.r = Renderer(64, 64, load_fonts(os.path.join(ROOT, "fonts")))
        self.hotspot = {"ssid": "Sportstracker-Setup", "password": "setup1234"}

    def page(self, number):
        image = Image.new("RGB", (64, 64))
        render_setup(image, self.r.fonts, self.r.theme, self.hotspot, number)
        return image

    def test_qr_pages_fit_and_text_page_is_dark(self):
        if qr_modules("x") is None:
            self.skipTest("qrcode not installed")
        texts = (wifi_qr_text("Sportstracker-Setup", "setup1234"), "http://10.42.0.1:8080/wifi/setup")
        for number in (0, 1):
            image = self.page(number)
            size = len(qr_modules(texts[number]))
            origin = (64 - 2 * size) // 2                                   # two pixels per module
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))       # quiet zone is lit
            self.assertEqual(image.getpixel((origin, origin)), (0, 0, 0))   # first finder pattern module is dark
        self.assertEqual(len({self.page(n).tobytes() for n in (0, 1, 2)}), 3)
        self.assertEqual(self.page(2).getpixel((0, 0)), (0, 0, 0))

    def test_qr_that_does_not_fit_is_refused(self):
        self.assertFalse(draw_qr(Image.new("RGB", (16, 16)), [[True] * 29 for _ in range(29)]))

    def test_wrap(self):
        font = self.r.fonts.small
        self.assertEqual(_wrap(font, "Sportstracker-Setup", 63), ["Sportstracker-", "Setup"])
        self.assertEqual(_wrap(font, "kurz", 63), ["kurz"])
        self.assertTrue(all(font.text_width(line) <= 63 for line in _wrap(font, "x" * 40, 63)))


class DisplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.now = [T0]
        self.display = FakeDisplay()
        self.service = DisplayService(self.env.config_path, display=self.display, root=self.env.root,
                                      now_func=lambda: self.now[0])
        self.path = wifi_path(os.path.join(self.env.root, "data"))

    def tearDown(self):
        self.env.close()

    def put(self, mode, ssid="Sportstracker-Setup"):
        status = {"mode": mode}
        if mode == "hotspot":
            status["hotspot"] = {"ssid": ssid, "password": "setup1234", "url": "http://10.42.0.1:8080/wifi/setup"}
        write_status(self.path, status, self.now[0])
        future = os.path.getmtime(self.path) + len(self.display.shown) + 1
        os.utime(self.path, (future, future))

    def test_setup_screen_replaces_the_rotation_and_normal_view_returns(self):
        self.assertTrue(self.service.step())
        normal = self.display.shown[-1].tobytes()
        self.put("hotspot")
        self.assertTrue(self.service.step())
        setup = self.display.shown[-1].tobytes()
        self.assertNotEqual(setup, normal)
        self.assertFalse(self.service.step())                                # unchanged page: no redraw
        self.now[0] = self.now[0] + timedelta(seconds=7)                     # next page
        self.put("hotspot")
        self.assertTrue(self.service.step())
        self.put("client")
        self.assertTrue(self.service.step())
        self.assertEqual(self.display.shown[-1].tobytes(), normal)           # back to the picture shown before

    def test_stale_status_does_not_block_the_normal_display(self):
        self.put("hotspot")
        self.now[0] = self.now[0] + timedelta(seconds=600)
        self.service.step()
        self.assertEqual(self.display.shown[-1].getpixel((0, 0)), (0, 0, 0))
        self.assertFalse(hotspot_active(read_status(self.path), self.now[0]))


class WebTests(Web050Base):
    def put_status(self, mode="hotspot", age=0):
        status = {"mode": mode, "networks": [{"ssid": "Heim", "signal": 80, "secured": True}],
                  "last_result": {"ok": False, "message": "„Heim“: Das Kennwort wurde nicht akzeptiert.", "at": ""}}
        if mode == "hotspot":
            status["hotspot"] = {"ssid": "Sportstracker-Setup", "password": "setup1234", "url": "x"}
        write_status(wifi_path(os.path.join(self.tmp, "data")), status, self.now - timedelta(seconds=age))

    def request_file(self):
        return request_path(os.path.join(self.tmp, "data"))

    def anonymous_client(self):
        return self.app.test_client()

    def anonymous_post(self, client, path, page, **data):
        import re
        html = client.get(page).get_data(as_text=True)
        data["csrf_token"] = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        return client.post(path, data=data)

    def test_wifi_page_needs_login_and_shows_state(self):
        self.assertEqual(self.anonymous_client().get("/wifi").status_code, 302)
        page = self.client.get("/wifi").get_data(as_text=True)
        self.assertIn("Noch kein Status", page)
        self.put_status("client")
        page = self.client.get("/wifi").get_data(as_text=True)
        self.assertIn("Heim (80 %", page)
        self.assertIn("nicht akzeptiert", page)
        self.assertIn('href="/wifi"', page)

    def test_connect_writes_a_private_request(self):
        self.put_status("client")
        response = self.post("/wifi/connect", page="/wifi", network="Heim", ssid="", password="neues-passwort")
        self.assertEqual(response.status_code, 302)
        with open(self.request_file(), encoding="utf-8") as handle:
            request = json.load(handle)
        self.assertEqual((request["ssid"], request["password"]), ("Heim", "neues-passwort"))
        self.assertEqual(os.stat(self.request_file()).st_mode & 0o777, 0o600)

    def test_connect_validates_and_needs_a_running_service(self):
        self.put_status("client")
        response = self.post("/wifi/connect", page="/wifi", network="Heim", ssid="", password="kurz")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(os.path.exists(self.request_file()))
        self.put_status("client", age=600)
        response = self.post("/wifi/connect", page="/wifi", network="Heim", ssid="", password="neues-passwort")
        self.assertEqual(response.status_code, 503)

    def test_manual_name_wins_over_the_list(self):
        self.put_status("client")
        self.post("/wifi/connect", page="/wifi", network="Heim", ssid="Versteckt", password="neues-passwort", hidden="on")
        with open(self.request_file(), encoding="utf-8") as handle:
            request = json.load(handle)
        self.assertEqual((request["ssid"], request["hidden"]), ("Versteckt", True))

    def test_hotspot_settings(self):
        response = self.post("/wifi/settings", page="/wifi", enabled="on", hotspot_ssid="Setup-X",
                             hotspot_password="passwort99", pin="4711", grace_seconds="240")
        self.assertEqual(response.status_code, 302)
        wifi = self.config()["wifi"]
        self.assertEqual((wifi["hotspot_ssid"], wifi["pin"], wifi["grace_seconds"], wifi["enabled"]), ("Setup-X", "4711", 240, True))
        self.post("/wifi/settings", page="/wifi", enabled="on", hotspot_ssid="Setup-X", hotspot_password="passwort99",
                  pin="", grace_seconds="240")
        self.assertEqual(self.config()["wifi"]["pin"], "4711")                    # empty field keeps the PIN
        bad = self.post("/wifi/settings", page="/wifi", enabled="on", hotspot_ssid="Setup-X", hotspot_password="kurz",
                        pin="", grace_seconds="240")
        self.assertEqual(bad.status_code, 400)
        bad = self.post("/wifi/settings", page="/wifi", enabled="on", hotspot_ssid="Setup-X", hotspot_password="passwort99",
                        pin="12", grace_seconds="240")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.config()["wifi"]["pin"], "4711")

    def test_setup_page_only_exists_while_the_hotspot_is_up(self):
        phone = self.anonymous_client()
        self.assertEqual(phone.get("/wifi/setup").status_code, 302)               # no status file
        self.put_status("client")
        self.assertEqual(phone.get("/wifi/setup").status_code, 302)
        self.put_status("hotspot", age=300)
        self.assertEqual(phone.get("/wifi/setup").status_code, 302)               # stale
        self.put_status("hotspot")
        page = phone.get("/wifi/setup").get_data(as_text=True)
        self.assertIn("PIN eingeben", page)
        self.assertNotIn("Verbinden", page)
        self.assertNotIn('href="/settings"', page)                                # no navigation without login

    def test_pin_unlocks_and_wrong_pin_does_not(self):
        self.put_status("hotspot")
        phone = self.anonymous_client()
        wrong = self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="pin", pin="0000")
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn("Gefundene Netzwerke", wrong.get_data(as_text=True))
        denied = self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="connect", network="Heim", ssid="",
                                     password="neues-passwort")
        self.assertEqual(denied.status_code, 400)
        self.assertFalse(os.path.exists(self.request_file()))
        ok = self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="pin", pin="1234")
        self.assertEqual(ok.status_code, 302)
        page = phone.get("/wifi/setup").get_data(as_text=True)
        self.assertIn("Heim (80 %", page)
        self.assertIn("Letzter Versuch", page)
        done = self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="connect", network="Heim", ssid="",
                                   password="neues-passwort")
        self.assertEqual(done.status_code, 200)
        self.assertIn("Verbindung wird hergestellt", done.get_data(as_text=True))
        with open(self.request_file(), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["password"], "neues-passwort")

    def test_wrong_pins_are_throttled(self):
        self.put_status("hotspot")
        phone = self.anonymous_client()
        codes = [self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="pin", pin="9%d99" % i).status_code
                 for i in range(4)]
        self.assertEqual(codes, [401, 401, 401, 429])                             # test throttle: 3 failures, then locked
        right = self.anonymous_post(phone, "/wifi/setup", "/wifi/setup", action="pin", pin="1234")
        self.assertEqual(right.status_code, 429)

    def test_setup_post_needs_the_csrf_token(self):
        self.put_status("hotspot")
        phone = self.anonymous_client()
        phone.get("/wifi/setup")
        self.assertEqual(phone.post("/wifi/setup", data={"action": "pin", "pin": "1234"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
