"""Satellite sensors (0.6.0): payloads, storage, plan, matrix page, HTTP push, backend page, MQTT bridge."""

import json
import os
import tempfile
import unittest
from datetime import timedelta

from sportstracker.config import ConfigError, parse_config
from sportstracker.render import Renderer, load_fonts
from sportstracker.satellites import (PayloadError, SatReading, air_quality, parse_payload, read_satellites,
                                      store_reading)
from sportstracker.service_mqtt import MqttBridge, topic_map
from sportstracker.system import SERVICES
from sportstracker.views import plan, render_view, view_for_item

from test_plan import BERLIN, NOW, STALE, cfg as plan_cfg, mk_state
from test_services import ROOT, Env
from test_web_050 import Web050Base


def sat_cfg(**sat):
    base = {"enabled": True, "items": [{"key": "bad", "name": "Bad"}, {"key": "buero", "name": "Büro"}]}
    base.update(sat)
    return plan_cfg(satellites=base)


class PayloadTests(unittest.TestCase):
    def test_accepted_forms(self):
        reading = parse_payload({"temperature": 22.4, "humidity": "45.5", "gas": 812, "air_index": 78}, NOW)
        self.assertEqual((reading.temperature_c, reading.humidity_pct, reading.gas_raw, reading.air_index),
                         (22.4, 45.5, 812.0, 78.0))
        self.assertEqual(parse_payload({"temp": 1, "hum": 2, "mq135": 3}, NOW).gas_raw, 3.0)
        self.assertEqual(parse_payload({"gas": 100}, NOW).temperature_c, None)

    def test_refused(self):
        for bad in (None, [], "x", {}, {"foo": 1}, {"temperature": 99}, {"humidity": 101}, {"gas": -1},
                    {"temperature": "warm"}, {"temperature": True}, {"air_index": 120}, {"temperature": float("nan")}):
            with self.assertRaises(PayloadError, msg=bad):
                parse_payload(bad, NOW)

    def test_air_quality(self):
        item = {"gas_clean": 800, "gas_bad": 2500}
        cases = [(800, 100, "GUT"), (2500, 0, "SCHLECHT"), (1650, 50, "MITTEL"), (100, 100, "GUT"), (4000, 0, "SCHLECHT")]
        for gas, index, label in cases:
            self.assertEqual(air_quality(SatReading(gas_raw=gas), item), (index, label), gas)
        self.assertEqual(air_quality(SatReading(gas_raw=2500, air_index=90), item), (90, "GUT"))   # own index wins
        self.assertIsNone(air_quality(SatReading(temperature_c=20), item))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "data", "satellites.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_merge_and_cleanup(self):
        store_reading(self.path, "bad", parse_payload({"temperature": 21, "humidity": 50}, NOW))
        store_reading(self.path, "buero", parse_payload({"gas": 900}, NOW))
        store_reading(self.path, "bad", parse_payload({"gas": 1000}, NOW + timedelta(seconds=30)))
        items = read_satellites(self.path)
        self.assertEqual((items["bad"].temperature_c, items["bad"].gas_raw), (21.0, 1000.0))      # merged
        self.assertEqual(items["buero"].gas_raw, 900.0)
        store_reading(self.path, "bad", parse_payload({"temperature": 22}, NOW), keep={"bad"})
        self.assertEqual(sorted(read_satellites(self.path)), ["bad"])                           # removed satellite dropped
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.path)) if n.endswith(".tmp")], [])

    def test_missing_or_broken_file(self):
        self.assertEqual(read_satellites(self.path), {})
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as handle:
            handle.write("{kaputt")
        self.assertEqual(read_satellites(self.path), {})

    def test_freshness(self):
        reading = parse_payload({"temperature": 20}, NOW)
        self.assertTrue(reading.is_fresh(NOW + timedelta(minutes=9)))
        self.assertFalse(reading.is_fresh(NOW + timedelta(minutes=11)))
        self.assertFalse(SatReading().is_fresh(NOW))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        sat = parse_config({}).satellites
        self.assertEqual((sat["enabled"], sat["items"], sat["mqtt"]["port"], sat["seconds"]), (False, [], 1883, 8))

    def test_valid_and_limits(self):
        cfg = parse_config({"satellites": {"items": [{"key": "a1", "name": "Küche", "token": "12345678"}] * 1}})
        self.assertEqual(cfg.satellites["items"][0]["gas_bad"], 2500)
        four = [{"key": "k%d" % i, "name": "N%d" % i} for i in range(4)]
        for bad in ({"items": four}, {"items": [{"key": "A", "name": "x"}]}, {"items": [{"key": "a", "name": ""}]},
                    {"items": [{"key": "a", "name": "ABCDEFGHIJK"}]}, {"items": [{"key": "a", "name": "x", "token": "kurz"}]},
                    {"items": [{"key": "a", "name": "x"}, {"key": "a", "name": "y"}]},
                    {"items": [{"key": "a", "name": "x", "gas_clean": 3000, "gas_bad": 2000}]},
                    {"items": [{"key": "a", "name": "x", "topic": "a/#"}]}, {"items": "x"}, {"seconds": 2}, {"enabled": 1},
                    {"mqtt": {"port": 0}}, {"mqtt": {"base_topic": "/x"}}, {"mqtt": {"host": "a b"}}, {"mqtt": "x"}):
            with self.assertRaises(ConfigError, msg=bad):
                parse_config({"satellites": bad})


class PlanAndViewTests(unittest.TestCase):
    def test_pages_follow_the_room_view(self):
        state = mk_state([])
        kinds = [item.kind for item in plan(state, NOW, sat_cfg(), BERLIN, STALE).items]
        self.assertEqual(kinds, ["table", "room", "satellite", "satellite"])
        off = [item.kind for item in plan(state, NOW, sat_cfg(enabled=False), BERLIN, STALE).items]
        self.assertNotIn("satellite", off)
        disabled = sat_cfg(items=[{"key": "bad", "name": "Bad", "enabled": False}, {"key": "b", "name": "B"}])
        self.assertEqual([i.satellite for i in plan(state, NOW, disabled, BERLIN, STALE).items if i.kind == "satellite"], ["b"])

    def test_not_shown_while_a_game_runs(self):
        from test_plan import game
        state = mk_state([game("g", "Alpha", "Bravo")])
        self.assertNotIn("satellite", [i.kind for i in plan(state, NOW, sat_cfg(), BERLIN, STALE).items])

    def test_view_with_fresh_stale_and_unknown_data(self):
        cfg = sat_cfg()
        item = [i for i in plan(mk_state([]), NOW, cfg, BERLIN, STALE).items if i.kind == "satellite"][0]
        fresh = {"bad": parse_payload({"temperature": 21.5, "humidity": 40, "gas": 800}, NOW)}
        view = view_for_item(item, None, NOW, False, None, cfg, 0.0, fresh)
        self.assertEqual((view.kind, view.satellite["name"], view.satellite["quality"]), ("satellite", "Bad", (100, "GUT")))
        old = view_for_item(item, None, NOW + timedelta(hours=1), False, None, cfg, 0.0, fresh)
        self.assertIsNone(old.satellite["reading"])
        self.assertIsNone(view_for_item(item, None, NOW, False, None, cfg, 0.0, {}).satellite["reading"])
        gone = plan_cfg(satellites={"enabled": True, "items": []})
        self.assertEqual(view_for_item(item, None, NOW, False, None, gone, 0.0, fresh).kind, "message")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.r = Renderer(64, 64, load_fonts(os.path.join(ROOT, "fonts")))

    def test_page_with_values_and_air_bar(self):
        reading = parse_payload({"temperature": 21.5, "humidity": 40}, NOW)
        image = self.r.render_satellite("Wohnzimmer", reading, (85, "GUT"))
        self.assertEqual(image.size, (64, 64))
        colors = {image.getpixel((x, y)) for x in range(64) for y in range(64)}
        for expected in (self.r.theme.room_temperature, self.r.theme.room_humidity, self.r.theme.air_good):
            self.assertIn(expected, colors)
        self.r.satellite_design = "a"                                                            # list with segment bar
        listing = self.r.render_satellite("Wohnzimmer", reading, (85, "GUT"))
        bar = [listing.getpixel((x, 58)) for x in range(64)]
        self.assertEqual(bar.count(self.r.theme.air_good), 40)                                   # 4 of 5 segments, 10 px each
        self.r.satellite_design = "b"
        bad = self.r.render_satellite("Bad", reading, (10, "SCHLECHT"))
        self.assertIn(self.r.theme.air_bad, {bad.getpixel((x, y)) for x in range(64) for y in range(64)})

    def test_dashes_without_data_and_no_air_section(self):
        empty = self.r.render_satellite("Bad", None, None)
        self.assertIsNotNone(empty.getbbox())
        self.assertEqual(empty.crop((0, 40, 64, 64)).getbbox(), None)                            # nothing below humidity
        self.assertNotEqual(empty.tobytes(), self.r.render_satellite("Bad", parse_payload({"temperature": 20, "humidity": 30}, NOW), None).tobytes())

    def test_long_name_is_fitted(self):
        image = self.r.render_satellite("Schlafzimm", None, None)
        self.assertEqual(image.size, (64, 64))


class PushTests(Web050Base):
    def setUp(self):
        super().setUp()
        self.set_config(satellites={"enabled": True, "items": [
            {"key": "bad", "name": "Bad", "token": "geheim-token-1"},
            {"key": "aus", "name": "Aus", "token": "geheim-token-2", "enabled": False},
            {"key": "ohne", "name": "Ohne"}]})
        self.anonymous = self.app.test_client()

    def push(self, key="bad", token="geheim-token-1", body=None, bearer=True, **kwargs):
        headers = {"Authorization": "Bearer " + token} if bearer else {"X-Token": token}
        return self.anonymous.post("/api/satellite/" + key, data=json.dumps(body or {"temperature": 22.1, "humidity": 41, "gas": 900}),
                                   content_type="application/json", headers=headers, **kwargs)

    def stored(self):
        return read_satellites(os.path.join(self.tmp, "data", "satellites.json"))

    def test_accepts_a_valid_push_without_login_or_csrf(self):
        response = self.push()
        self.assertEqual((response.status_code, response.get_json()), (200, {"ok": True}))
        reading = self.stored()["bad"]
        self.assertEqual((reading.temperature_c, reading.gas_raw), (22.1, 900.0))
        self.assertEqual(self.push(bearer=False, body={"temperature": 23}).status_code, 200)       # X-Token header
        self.assertEqual(self.stored()["bad"].gas_raw, 900.0)                                      # merged

    def test_refuses_wrong_token_unknown_disabled_and_tokenless(self):
        for kwargs in ({"token": "falsch"}, {"token": ""}, {"key": "unbekannt"}, {"key": "aus", "token": "geheim-token-2"},
                       {"key": "ohne", "token": ""}):
            self.assertEqual(self.push(**kwargs).status_code, 401, kwargs)
        self.assertEqual(self.stored(), {})
        self.set_config(satellites={"enabled": False, "items": [{"key": "bad", "name": "Bad", "token": "geheim-token-1"}]})
        self.assertEqual(self.push().status_code, 401)

    def test_bad_payloads(self):
        for body in ({"foo": 1}, {"temperature": 500}):
            self.assertEqual(self.push(body=body).status_code, 400, body)
        response = self.anonymous.post("/api/satellite/bad", data="kein json", headers={"Authorization": "Bearer geheim-token-1"})
        self.assertEqual(response.status_code, 400)
        big = self.anonymous.post("/api/satellite/bad", data="x" * 5000, headers={"Authorization": "Bearer geheim-token-1"})
        self.assertEqual(big.status_code, 413)

    def test_other_pages_still_need_a_login(self):
        self.assertEqual(self.anonymous.get("/satellites").status_code, 302)
        self.assertEqual(self.anonymous.post("/satellites").status_code, 400)


class PageTests(Web050Base):
    def form(self, **extra):
        data = {"enabled": "on", "seconds": "8", "mqtt_host": "", "mqtt_port": "1883", "mqtt_base_topic": "sportstracker",
                "s1_name": "Bad", "s1_enabled": "on", "s2_name": "", "s3_name": ""}
        data.update(extra)
        return data

    def test_page_and_navigation(self):
        page = self.client.get("/satellites").get_data(as_text=True)
        self.assertIn("Satellit 3", page)
        self.assertIn("/api/satellite/", page)
        self.assertIn('href="/satellites"', self.client.get("/").get_data(as_text=True))

    def test_save_generates_key_and_token(self):
        response = self.post("/satellites", page="/satellites", **self.form())
        self.assertEqual(response.status_code, 302)
        sat = self.config()["satellites"]
        self.assertEqual(len(sat["items"]), 1)
        item = sat["items"][0]
        self.assertEqual((item["key"], item["name"], item["enabled"]), ("bad", "Bad", True))
        self.assertGreaterEqual(len(item["token"]), 8)
        token = item["token"]
        self.post("/satellites", page="/satellites", **self.form(s1_token=token, s1_name="Bad oben"))
        self.assertEqual(self.config()["satellites"]["items"][0]["token"], token)
        self.assertIn(token, self.client.get("/satellites").get_data(as_text=True))

    def test_gas_limits_and_topic_are_kept_per_satellite(self):
        self.post("/satellites", page="/satellites", **self.form(s1_gas_clean="700", s1_gas_bad="2000", s1_topic="haus/bad/state"))
        item = self.config()["satellites"]["items"][0]
        self.assertEqual((item["gas_clean"], item["gas_bad"], item["topic"]), (700, 2000, "haus/bad/state"))

    def test_mqtt_password_is_kept_unless_replaced_or_cleared(self):
        self.post("/satellites", page="/satellites", **self.form(mqtt_host="192.168.22.30", mqtt_username="ha", mqtt_password="geheim"))
        mqtt = self.config()["satellites"]["mqtt"]
        self.assertEqual((mqtt["host"], mqtt["username"], mqtt["password"]), ("192.168.22.30", "ha", "geheim"))
        self.post("/satellites", page="/satellites", **self.form(mqtt_host="192.168.22.30", mqtt_username="ha"))
        self.assertEqual(self.config()["satellites"]["mqtt"]["password"], "geheim")
        page = self.client.get("/satellites").get_data(as_text=True)
        self.assertNotIn("geheim", page)
        self.post("/satellites", page="/satellites", **self.form(mqtt_clear_password="on"))
        self.assertEqual(self.config()["satellites"]["mqtt"]["password"], "")

    def test_invalid_input_changes_nothing(self):
        before = self.config()
        for extra in ({"seconds": "1"}, {"mqtt_port": "0"}, {"s1_name": "ZUUUULANGERNAME"}, {"s1_key": "Ungültig!"},
                      {"s1_gas_clean": "3000", "s1_gas_bad": "2000"}, {"mqtt_base_topic": "a/#"}):
            self.assertEqual(self.post("/satellites", page="/satellites", **self.form(**extra)).status_code, 400, extra)
        self.assertEqual(self.config(), before)

    def test_removed_row_removes_the_satellite_and_values_are_listed(self):
        self.post("/satellites", page="/satellites", **self.form(s2_name="Büro"))
        self.assertEqual([i["key"] for i in self.config()["satellites"]["items"]], ["bad", "b-ro"])
        store_reading(os.path.join(self.tmp, "data", "satellites.json"), "bad",
                      parse_payload({"temperature": 21.5, "humidity": 40, "gas": 800}, self.now))
        page = self.client.get("/satellites").get_data(as_text=True)
        self.assertIn("21,5 °C", page)
        self.assertIn("Luft gut", page)
        self.post("/satellites", page="/satellites", **self.form(s2_name=""))
        self.assertEqual([i["key"] for i in self.config()["satellites"]["items"]], ["bad"])

    def test_preview_shows_a_satellite_page(self):
        self.post("/satellites", page="/satellites", **self.form())
        self.set_config(satellites=self.config()["satellites"], idle_hide_table=True)
        self.assertEqual(self.client.get("/preview.png").status_code, 200)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.on_connect = None
        self.on_message = None

    def username_pw_set(self, user, password):
        self.calls.append(("auth", user, password))

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect_async(self, host, port, keepalive=60):
        self.calls.append(("connect", host, port))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def subscribe(self, topic):
        self.calls.append(("subscribe", topic))


class Message:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload


class MqttTests(unittest.TestCase):
    SAT = {"enabled": True, "mqtt": {"enabled": True, "host": "192.168.22.30", "username": "ha", "password": "pw"},
           "items": [{"key": "bad", "name": "Bad"}, {"key": "buero", "name": "Büro", "topic": "haus/buero/state"},
                     {"key": "aus", "name": "Aus", "enabled": False}]}

    def setUp(self):
        self.env = Env(satellites=self.SAT)
        self.clients = []
        self.bridge = MqttBridge(self.env.config_path, root=self.env.root, client_factory=self.factory, now_func=lambda: NOW)

    def tearDown(self):
        self.env.close()

    def factory(self):
        client = FakeClient()
        self.clients.append(client)
        return client

    def path(self):
        return os.path.join(self.env.root, "data", "satellites.json")

    def test_topics(self):
        self.assertEqual(topic_map(parse_config(dict(favorite_team_id="1", satellites=self.SAT))),
                         {"sportstracker/bad/state": "bad", "haus/buero/state": "buero"})

    def test_connects_subscribes_and_stores(self):
        self.bridge.step()
        client = self.clients[0]
        self.assertIn(("connect", "192.168.22.30", 1883), client.calls)
        self.assertIn(("auth", "ha", "pw"), client.calls)
        client.on_connect(client, None, {}, 0)
        self.assertEqual(sorted(c[1] for c in client.calls if c[0] == "subscribe"), ["haus/buero/state", "sportstracker/bad/state"])
        client.on_message(client, None, Message("sportstracker/bad/state", b'{"temperature": 20.5, "humidity": 44, "gas": 1000}'))
        client.on_message(client, None, Message("haus/buero/state", b'{"temperature": 19}'))
        items = read_satellites(self.path())
        self.assertEqual((items["bad"].temperature_c, items["bad"].gas_raw, items["buero"].temperature_c), (20.5, 1000.0, 19.0))
        self.assertEqual(self.bridge.received, 2)

    def test_bad_and_foreign_messages_are_ignored(self):
        self.bridge.step()
        client = self.clients[0]
        for topic, payload in (("sportstracker/bad/state", b"nicht json"), ("sportstracker/bad/state", b'{"temperature": 900}'),
                               ("sportstracker/bad/state", b"\xff\xfe"), ("fremd/topic", b'{"temperature": 20}'),
                               ("sportstracker/aus/state", b'{"temperature": 20}')):
            client.on_message(client, None, Message(topic, payload))
        self.assertEqual(read_satellites(self.path()), {})
        self.assertTrue(self.bridge.last_error)

    def test_unchanged_config_keeps_the_connection_changes_reconnect(self):
        self.bridge.step()
        self.bridge.step()
        self.assertEqual(len(self.clients), 1)
        sat = json.loads(json.dumps(self.SAT))
        sat["mqtt"]["host"] = "192.168.22.31"
        self.env.write_config(favorite_team_id="1", satellites=sat)
        self.bridge.step()
        self.assertEqual(len(self.clients), 2)
        self.assertIn(("disconnect",), self.clients[0].calls)
        self.assertIn(("connect", "192.168.22.31", 1883), self.clients[1].calls)

    def test_switched_off_or_incomplete_does_not_connect(self):
        for change in ({"enabled": False}, {"host": ""}):
            sat = json.loads(json.dumps(self.SAT))
            sat["mqtt"].update(change)
            self.env.write_config(favorite_team_id="1", satellites=sat)
            self.bridge.step()
        self.assertEqual(self.clients, [])
        sat = json.loads(json.dumps(self.SAT))
        sat["items"] = []
        self.env.write_config(favorite_team_id="1", satellites=sat)
        self.bridge.step()
        self.assertEqual(self.clients, [])

    def test_removed_satellite_is_dropped_from_the_file(self):
        self.bridge.step()
        client = self.clients[0]
        client.on_message(client, None, Message("sportstracker/bad/state", b'{"temperature": 20}'))
        store_reading(self.path(), "alt", parse_payload({"temperature": 1}, NOW))
        client.on_message(client, None, Message("sportstracker/bad/state", b'{"temperature": 21}'))
        self.assertEqual(sorted(read_satellites(self.path())), ["bad"])


class ServiceListTests(unittest.TestCase):
    def test_mqtt_service_is_listed(self):
        self.assertIn("sportstracker-mqtt", SERVICES)
        for name in ("deploy/sportstracker-mqtt.service",):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)))
        with open(os.path.join(ROOT, "deploy", "sportstracker-sudoers"), encoding="utf-8") as handle:
            self.assertIn("restart sportstracker-mqtt", handle.read())
        with open(os.path.join(ROOT, "deploy", "install.sh"), encoding="utf-8") as handle:
            self.assertIn("sportstracker-mqtt", handle.read())


if __name__ == "__main__":
    unittest.main()
