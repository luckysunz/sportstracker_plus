"""Web backend, version 0.5.0: new settings, camera, plan and climate on the dashboard."""

import json
import os
import unittest
from datetime import timedelta

from flask import Flask  # noqa: F401

from sportstracker.camera import CameraControl, CameraError, MotionStatus
from sportstracker.models import GameStatus
from sportstracker.sensor import SensorReading, write_sensor
from sportstracker.settings import apply_form, config_to_values, format_decimal
from sportstracker.config import parse_config
from sportstracker.state import State, write_state
from sportstracker.system import SERVICES
from sportstracker.webapp import create_app

from test_web import WebTestBase
from test_services import make_game, make_table, utc


def make_jpeg(color=(200, 30, 30)):
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, format="JPEG")
    return out.getvalue()


class FakeCamera:
    def __init__(self):
        self.fail = None
        self.found = True
        self.live_active = False
        self.captures = []
        self.capture_options = []
        self.live_args = None
        self.live_options = None
        self.live_motion = None
        self.on_motion = None
        self.live_fail = None
        self.motion = MotionStatus(False, 0, "", None, 0.0, 0.0, "")

    def status(self):
        return self.found, "0 : imx219 [3280x2464]" if self.found else "Keine Kamera erkannt"

    def capture(self, width, height, use_cache=False, options=None):
        if self.fail:
            raise CameraError(self.fail)
        self.captures.append((width, height))
        self.capture_options.append(options)
        return make_jpeg()

    def motion_status(self):
        return self.motion

    def live_frames(self, width, height, fps, options=None, motion=None, on_motion=None):
        if self.live_fail:
            raise CameraError(self.live_fail)
        self.live_args = (width, height, fps)
        self.live_options, self.live_motion, self.on_motion = options, motion, on_motion
        return iter([b"\xff\xd8F1\xff\xd9", b"\xff\xd8F2\xff\xd9"])


class Web050Base(WebTestBase):
    def setUp(self):
        super().setUp()
        self.camera = FakeCamera()
        from sportstracker.system import SystemControl
        from sportstracker.auth import LoginThrottle
        self.app = create_app(self.config_path, root=self.tmp, system=SystemControl(self.runner, self.tmp),
                              teams_loader=self.load_teams, now_func=lambda: self.now, camera=self.camera,
                              throttle=LoginThrottle(max_failures=3, lockout=60, clock=lambda: 0.0))
        self.client = self.app.test_client()
        self.login()

    def set_config(self, **changes):
        raw = self.config()
        raw.update(changes)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)


class SettingsFormTest(Web050Base):
    def test_page_shows_new_sections(self):
        page = self.client.get("/settings").get_data(as_text=True)
        for text in ("Spieltag-Anzeige", "Spielfreie Zeit", "Temperatursensor", "name=\"sensor_gpio\"", "Kamera"):
            self.assertIn(text, page)

    def test_save_new_values(self):
        response = self.post("/settings", page="/settings", **self.settings_form(
            show_other_games=None, idle_mode="room", rotation_card_seconds="20", result_window_minutes="45",
            sensor_enabled="on", sensor_gpio="19", sensor_temp_offset="-2,5", sensor_humidity_offset="3",
            camera_enabled=None))
        self.assertEqual(response.status_code, 302)
        saved = self.config()
        self.assertFalse(saved["show_other_games"])
        self.assertEqual(saved["idle_mode"], "room")
        self.assertEqual((saved["rotation_card_seconds"], saved["result_window_minutes"]), (20, 45))
        self.assertEqual(saved["sensor"]["enabled"], True)
        self.assertEqual((saved["sensor"]["gpio"], saved["sensor"]["temp_offset"], saved["sensor"]["humidity_offset"]),
                         (19, -2.5, 3.0))
        self.assertFalse(saved["camera"]["enabled"])
        self.assertEqual((saved["camera"]["width"], saved["camera"]["live_width"]), (1280, 640))   # untouched
        values = config_to_values(parse_config(saved))
        self.assertEqual(values["sensor_temp_offset"], "-2,5")

    def test_invalid_values_are_refused(self):
        before = self.config()
        for change in ({"sensor_gpio": "4"}, {"sensor_gpio": "99"}, {"sensor_temp_offset": "heiß"},
                       {"sensor_temp_offset": "50"}, {"sensor_interval_seconds": "1"}, {"idle_mode": "disco"},
                       {"rotation_main_seconds": "1"}, {"result_window_minutes": "0"}):
            response = self.post("/settings", page="/settings", **self.settings_form(**change))
            self.assertEqual(response.status_code, 400, change)
        self.assertEqual(self.config(), before)

    def test_format_decimal(self):
        self.assertEqual([format_decimal(v) for v in (2.5, -1.0, 0.0, 0.05)], ["2,5", "-1", "0", "0,05"])

    def test_unknown_sensor_keys_survive(self):
        self.set_config(sensor={"enabled": True, "type": "dht22", "gpio": 25, "note": "keep"})
        self.post("/settings", page="/settings", **self.settings_form())
        self.assertEqual(self.config()["sensor"]["note"], "keep")


class CameraTest(Web050Base):
    def gallery_dir(self):
        return os.path.join(self.tmp, "data", "gallery")

    def photos(self):
        try:
            return sorted(n for n in os.listdir(self.gallery_dir()) if n.endswith(".jpg"))
        except OSError:
            return []

    def take_photo(self, **extra):
        data = {"source": "still"}
        data.update(extra)
        return self.post("/camera/photo", page="/camera", **data)

    def test_requires_login(self):
        self.post("/logout")
        for path in ("/camera", "/camera/live", "/camera/live.mjpg", "/camera/live/motion", "/camera/gallery", "/camera/thumb/x.jpg",
                     "/camera/photo/x.jpg", "/camera/view/x.jpg"):
            self.assertEqual(self.client.get(path).status_code, 302, path)

    def test_settings_page_has_only_the_switch(self):
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('name="camera_enabled"', page)
        self.assertNotIn('name="camera_width"', page)

    def test_photo_page_offers_resolutions(self):
        page = self.client.get("/camera").get_data(as_text=True)
        self.assertIn("1280 × 960", page)
        self.assertIn("3280 × 2464", page)
        self.assertIn('value="1280x960" selected', page)
        self.assertIn("imx219", page)

    def test_photo_is_saved_with_a_time_stamp(self):
        response = self.take_photo(photo_size="1920x1080")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.photos(), ["2026-10-30_13-00-00.jpg"])         # 12:00 UTC = 13:00 Berlin (winter time)
        self.assertEqual(self.camera.captures, [(1920, 1080)])
        self.assertEqual((self.config()["camera"]["width"], self.config()["camera"]["height"]), (1920, 1080))
        page = self.client.get("/camera").get_data(as_text=True)
        self.assertIn("Letztes Foto", page)
        self.assertIn("2026-10-30_13-00-00.jpg", page)

    def test_invalid_resolution_takes_no_photo(self):
        self.take_photo(photo_size="9999x9999")
        self.assertEqual(self.photos(), [])
        self.assertEqual(self.camera.captures, [])

    def test_camera_error_is_reported_and_nothing_is_saved(self):
        self.camera.fail = "Aufnahme fehlgeschlagen: boom"
        response = self.take_photo()
        self.assertEqual(response.status_code, 302)
        self.assertIn("boom", self.client.get("/camera").get_data(as_text=True))
        self.assertEqual(self.photos(), [])

    def test_no_camera_found(self):
        self.camera.found = False
        page = self.client.get("/camera").get_data(as_text=True)
        self.assertIn("Keine Kamera erkannt", page)
        self.assertNotIn("Foto aufnehmen und speichern", page)

    def test_gallery_view_download_delete(self):
        self.take_photo()
        name = self.photos()[0]
        page = self.client.get("/camera/gallery").get_data(as_text=True)
        self.assertIn("/camera/thumb/" + name, page)
        self.assertIn("1 Fotos", page)
        thumb = self.client.get("/camera/thumb/" + name)
        self.assertEqual((thumb.status_code, thumb.mimetype), (200, "image/jpeg"))
        photo = self.client.get("/camera/photo/" + name)
        self.assertEqual(photo.mimetype, "image/jpeg")
        self.assertNotIn("attachment", photo.headers.get("Content-Disposition", ""))
        download = self.client.get("/camera/photo/%s?dl=1" % name)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        view = self.client.get("/camera/view/" + name).get_data(as_text=True)
        self.assertIn("Foto vom 30.10.2026 13:00:00", view)
        refused = self.post("/camera/photo/%s/delete" % name, page="/camera/view/" + name)
        self.assertEqual(refused.status_code, 302)
        self.assertEqual(self.photos(), [name])                    # not confirmed
        self.post("/camera/photo/%s/delete" % name, page="/camera/view/" + name, confirm="yes")
        self.assertEqual(self.photos(), [])
        self.assertEqual(self.client.get("/camera/photo/" + name).status_code, 404)

    def test_view_links_to_neighbours(self):
        for second in (0, 5):
            self.now = utc(2026, 10, 30, 12, 0, second)
            self.take_photo()
        older, newer = self.photos()
        page = self.client.get("/camera/view/" + newer).get_data(as_text=True)
        self.assertIn("/camera/view/" + older, page)
        self.assertIn("älteres", page)
        self.assertNotIn("neueres", page)

    def test_no_path_tricks(self):
        for path in ("/camera/photo/..%2Fconfig%2Fconfig.json", "/camera/photo/config.json",
                     "/camera/thumb/..%2F..%2Fx.jpg", "/camera/view/..%2Fx.jpg", "/camera/photo/2026-01-01_00-00-00.jpg"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.post("/camera/photo/..%2Fx.jpg/delete", page="/camera", confirm="yes").status_code, 404)

    def test_gallery_limit_and_clear(self):
        for second in (0, 5):
            self.now = utc(2026, 10, 30, 12, 0, second)
            self.take_photo()
        self.post("/camera/gallery/limit", page="/camera/gallery", gallery_limit_mb="50")
        self.assertEqual(self.config()["camera"]["gallery_limit_mb"], 50)
        for bad in ("5", "x", "99999"):
            self.post("/camera/gallery/limit", page="/camera/gallery", gallery_limit_mb=bad)
            self.assertEqual(self.config()["camera"]["gallery_limit_mb"], 50)
        self.post("/camera/gallery/clear", page="/camera/gallery")
        self.assertEqual(len(self.photos()), 2)                    # no confirmation
        self.post("/camera/gallery/clear", page="/camera/gallery", confirm="yes")
        self.assertEqual(self.photos(), [])

    def test_gallery_pages(self):
        from sportstracker.gallery import Gallery
        gallery = Gallery(self.gallery_dir())
        data = make_jpeg()
        from datetime import datetime
        for number in range(30):
            gallery.save(data, datetime(2026, 10, 30, 12, number, 0), 200)
        first = self.client.get("/camera/gallery").get_data(as_text=True)
        self.assertEqual(first.count("/camera/thumb/"), 24)
        self.assertIn("Seite 1 von 2", first)
        second = self.client.get("/camera/gallery?page=2").get_data(as_text=True)
        self.assertEqual(second.count("/camera/thumb/"), 6)
        self.assertIn("Seite 2 von 2", self.client.get("/camera/gallery?page=99").get_data(as_text=True))
        self.assertEqual(self.client.get("/camera/gallery?page=x").status_code, 200)

    def test_live_page_start_and_stop(self):
        page = self.client.get("/camera/live").get_data(as_text=True)
        self.assertIn("Live-Ansicht starten", page)
        self.assertNotIn("live.mjpg", page)
        running = self.client.get("/camera/live?run=1").get_data(as_text=True)
        self.assertIn("/camera/live.mjpg", running)
        self.assertIn("Live-Ansicht beenden", running)

    def test_live_settings(self):
        self.post("/camera/live/settings", page="/camera/live", live_size="1280x720", live_fps="15")
        camera = self.config()["camera"]
        self.assertEqual((camera["live_width"], camera["live_height"], camera["live_fps"]), (1280, 720, 15))
        self.post("/camera/live/settings", page="/camera/live", live_size="1920x1080", live_fps="15")
        self.post("/camera/live/settings", page="/camera/live", live_size="640x480", live_fps="99")
        self.assertEqual(self.config()["camera"]["live_width"], 1280)
        page = self.client.get("/camera/live").get_data(as_text=True)
        self.assertIn('value="1280x720" selected', page)
        self.assertIn('<option value="15" selected>', page)

    def test_live_stream_is_multipart_with_the_configured_size(self):
        self.post("/camera/live/settings", page="/camera/live", live_size="800x600", live_fps="5")
        response = self.client.get("/camera/live.mjpg")
        self.assertIn("multipart/x-mixed-replace", response.headers["Content-Type"])
        body = response.get_data()
        self.assertEqual(body.count(b"--frame"), 2)
        self.assertIn(b"F1", body)
        self.assertIn(b"F2", body)
        self.assertEqual(self.camera.live_args, (800, 600, 5))

    def test_live_stream_error(self):
        self.camera.live_fail = "Die Kamera wird gerade benutzt."
        response = self.client.get("/camera/live.mjpg")
        self.assertEqual(response.status_code, 503)
        self.assertIn("benutzt", response.get_data(as_text=True))

    def test_photo_from_live(self):
        self.take_photo(source="live")
        self.assertEqual(self.photos(), [])                        # live view is not running
        self.camera.live_active = True
        self.take_photo(source="live")
        self.assertEqual(len(self.photos()), 1)

    def test_photo_and_live_use_the_image_settings(self):
        self.take_photo()
        options = self.camera.capture_options[-1]
        self.assertTrue(options.hflip and options.vflip)                 # default: turned by 180 degrees
        self.assertEqual((options.brightness, options.sharpness), (0.0, 1.0))
        self.post("/camera/image/settings", page="/camera", back="photo", hflip="on", brightness="20", sharpness="2")
        self.take_photo()
        options = self.camera.capture_options[-1]
        self.assertEqual((options.hflip, options.vflip, options.brightness, options.sharpness), (True, False, 0.2, 2.0))
        self.client.get("/camera/live.mjpg")
        self.assertEqual(self.camera.live_options, options)

    def test_image_settings_are_saved_and_shown(self):
        response = self.post("/camera/image/settings", page="/camera", back="photo", vflip="on", brightness="-30",
                             sharpness="0")
        self.assertEqual(response.status_code, 302)
        camera = self.config()["camera"]
        self.assertEqual((camera["hflip"], camera["vflip"], camera["brightness"], camera["sharpness"]),
                         (False, True, -30, 0.0))
        page = self.client.get("/camera").get_data(as_text=True)
        self.assertIn('name="vflip" checked', page)
        self.assertNotIn('name="hflip" checked', page)
        self.assertIn('<option value="-30" selected>', page)
        self.assertIn('<option value="0" selected>', page)
        live = self.client.get("/camera/live").get_data(as_text=True)
        self.assertIn('name="vflip" checked', live)
        self.assertIn('name="back" value="live"', live)

    def test_invalid_image_settings_change_nothing(self):
        before = self.config()
        self.post("/camera/image/settings", page="/camera", back="photo", hflip="on", brightness="99", sharpness="1")
        self.post("/camera/image/settings", page="/camera", back="photo", hflip="on", brightness="0", sharpness="abc")
        self.assertEqual(self.config(), before)

    def test_image_settings_return_to_the_running_live_view(self):
        response = self.post("/camera/image/settings", page="/camera/live", back="live", run="1", brightness="10",
                             sharpness="1")
        self.assertTrue(response.headers["Location"].endswith("/camera/live?run=1"))
        response = self.post("/camera/image/settings", page="/camera", back="https://example.org", brightness="10",
                             sharpness="1")
        self.assertTrue(response.headers["Location"].endswith("/camera"))

    def test_motion_is_off_by_default(self):
        self.client.get("/camera/live.mjpg")
        self.assertIsNone(self.camera.live_motion)

    def test_motion_settings_are_passed_to_the_live_view(self):
        self.post("/camera/live/motion/settings", page="/camera/live", motion_enabled="on", motion_sensitivity="4",
                  motion_pause="120")
        camera = self.config()["camera"]
        self.assertEqual((camera["motion_enabled"], camera["motion_sensitivity"], camera["motion_pause"]),
                         (True, 4, 120))
        self.client.get("/camera/live.mjpg")
        self.assertEqual(tuple(self.camera.live_motion), (4, 120))
        self.assertTrue(callable(self.camera.on_motion))
        page = self.client.get("/camera/live?run=1").get_data(as_text=True)
        self.assertIn('name="motion_enabled" checked', page)
        self.assertIn('<option value="4" selected>', page)
        self.assertIn('<option value="120" selected>', page)
        self.assertIn("/camera/live/motion", page)

    def test_invalid_motion_settings_change_nothing(self):
        before = self.config()
        self.post("/camera/live/motion/settings", page="/camera/live", motion_enabled="on", motion_sensitivity="9",
                  motion_pause="60")
        self.post("/camera/live/motion/settings", page="/camera/live", motion_enabled="on", motion_sensitivity="3",
                  motion_pause="7")
        self.assertEqual(self.config(), before)

    def test_motion_photo_goes_to_the_gallery(self):
        self.post("/camera/live/motion/settings", page="/camera/live", motion_enabled="on", motion_sensitivity="3",
                  motion_pause="30")
        self.client.get("/camera/live.mjpg")
        name = self.camera.on_motion(make_jpeg())
        self.assertEqual(self.photos(), [name])
        self.assertRegex(name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.jpg$")

    def test_motion_status_page(self):
        page = self.client.get("/camera/live/motion").get_data(as_text=True)
        self.assertIn("ausgeschaltet", page)
        self.set_config(camera={"motion_enabled": True})
        page = self.client.get("/camera/live/motion").get_data(as_text=True)
        self.assertIn("nicht aktiv", page)
        self.camera.motion = MotionStatus(True, 2, "2026-10-31_17-40-00.jpg", 12.4, 1.25, 2.0, "")
        page = self.client.get("/camera/live/motion").get_data(as_text=True)
        self.assertIn("1,2 % (Schwelle 2,0 %)", page)
        self.assertIn("vor 12 s", page)
        self.assertIn("/camera/view/2026-10-31_17-40-00.jpg", page)
        self.assertIn('http-equiv="refresh"', page)

    def test_disabled_camera(self):
        self.set_config(camera={"enabled": False})
        for path in ("/camera", "/camera/live", "/camera/gallery"):
            self.assertIn("ausgeschaltet", self.client.get(path).get_data(as_text=True))
        for path in ("/camera/live.mjpg", "/camera/live/motion", "/camera/thumb/2026-10-30_13-00-00.jpg",
                     "/camera/photo/2026-10-30_13-00-00.jpg", "/camera/view/2026-10-30_13-00-00.jpg"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.take_photo().status_code, 404)
        self.assertNotIn('href="/camera"', self.client.get("/").get_data(as_text=True))

    def test_navigation_link_when_enabled(self):
        self.assertIn('href="/camera"', self.client.get("/").get_data(as_text=True))


class DashboardTest(Web050Base):
    def put_state(self, games, marks=None):
        from sportstracker.runtime import state_path
        from sportstracker.config import load_config
        cfg = load_config(self.config_path)
        write_state(state_path(cfg, self.tmp), State(updated_at=self.now, provider="p", favorite_team_id="641",
                                                    table=make_table(), games=games, marks=marks or {}))

    def test_plan_is_listed(self):
        self.put_state([])
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Anzeigeliste", page)
        self.assertIn("Tabelle", page)
        self.assertIn("Raumansicht", page)

    def test_game_is_listed(self):
        self.put_state([make_game(GameStatus.LIVE, start=self.now - timedelta(minutes=30), home_score=3, away_score=2)])
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("3:2", page)

    def test_climate_card(self):
        self.set_config(sensor={"enabled": True, "gpio": 25})
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Raumklima", page)
        self.assertIn("keine aktuellen Werte", page)
        from sportstracker.runtime import resolve
        write_sensor(resolve("data", self.tmp) + "/sensor.json", SensorReading(self.now, 21.54, 48.2))
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("21,5 °C", page)
        self.assertIn("48 %", page)

    def test_no_climate_card_without_sensor(self):
        self.assertNotIn("Raumklima", self.client.get("/").get_data(as_text=True))

    def test_preview_renders_with_games_and_room(self):
        self.put_state([make_game(GameStatus.LIVE, start=self.now - timedelta(minutes=30))])
        self.assertEqual(self.client.get("/preview.png").status_code, 200)
        self.put_state([])
        for second in (5, 15):                       # table slot, room slot
            self.now = utc(2026, 10, 30, 12, 0, second)
            self.assertEqual(self.client.get("/preview.png").status_code, 200)

    def test_sensor_service_is_listed(self):
        self.assertIn("sportstracker-sensor", SERVICES)
        page = self.client.get("/system").get_data(as_text=True)
        self.assertIn("Sensordienst", page)
        self.post("/system/restart/sportstracker-sensor", page="/system")
        self.assertIn((["sudo", "-n", "/usr/bin/systemctl", "restart", "sportstracker-sensor"], True), self.runner.calls)


if __name__ == "__main__":
    unittest.main()
