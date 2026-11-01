"""Picture mode (0.5.5): upload conversion, fitting, plan, display service and web page."""

import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta

from PIL import Image

from sportstracker import picture as pic
from sportstracker.config import ConfigError, DEFAULT_PICTURE, PICTURE_FITS, parse_config
from sportstracker.models import GameStatus
from sportstracker.picture import PictureError, PictureRenderer, PictureStore


def store_upload(data, directory):
    """Single-picture helper of the old tests: the new picture replaces the gallery content."""
    store = PictureStore(directory)
    old = [item["id"] for item in store.items()]
    note = store.add(data)[1]
    for image_id in old:
        store.remove(image_id)
    return note


def first_path(directory):
    return PictureStore(directory).path(PictureStore(directory).items()[0]["id"])


def bump(directory, seconds=5):
    """Make the renderer notice a change even if the file system clock did not tick."""
    manifest = PictureStore(directory).manifest
    future = os.path.getmtime(manifest) + seconds
    os.utime(manifest, (future, future))


def picture_available(directory):
    return PictureStore(directory).count() > 0


def remove_picture(directory):
    return PictureStore(directory).clear() > 0

from sportstracker.service_display import DisplayService
from sportstracker.settings import apply_picture_form
from sportstracker.state import State, write_state
from sportstracker.views import Item, Plan, plan, render_view, view_for_item

from test_plan import BERLIN, NOW, STALE, cfg as plan_cfg, game, mk_state, utc
from test_services import Env, FakeDisplay, make_table
from test_web_050 import Web050Base

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def encode(image, fmt, **kwargs):
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def gif(colors, durations):
    frames = [Image.new("RGB", (160, 80), c) for c in colors]
    return encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)


def pcfg(**picture):
    base = dict(DEFAULT_PICTURE, enabled=True)
    base.update(picture)
    return plan_cfg(picture=base)


def kinds_of(p):
    return [i.kind for i in p.items]


class ConfigTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(parse_config({}).picture, DEFAULT_PICTURE)
        self.assertEqual(parse_config({"picture": {"fit": "fit"}}).picture["fit"], "fit")
        for bad in ({"fit": "zoom"}, {"intensity": 4}, {"intensity": 101}, {"seconds": 2}, {"seconds": 121},
                    {"enabled": 1}, {"permanent": "x"}, {"seconds": True}, "x"):
            with self.assertRaises(ConfigError, msg=bad):
                parse_config({"picture": bad})
        self.assertNotIn("picture", parse_config({"picture": {}}).extra)
        self.assertEqual(PICTURE_FITS, ("fill", "fit", "stretch"))


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stored(self):
        with Image.open(first_path(self.tmp)) as image:
            image.load()
            image.n_frames = getattr(image, "n_frames", 1)
            return image

    def test_jpeg_is_shrunk_and_stored_as_png(self):
        store_upload(encode(Image.new("RGB", (1600, 800), (200, 10, 10)), "JPEG"), self.tmp)
        image = self.stored()
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.size, (128, 64))
        self.assertTrue(picture_available(self.tmp))
        self.assertEqual(os.stat(first_path(self.tmp)).st_mode & 0o777, 0o644)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])

    def test_small_picture_is_not_enlarged(self):
        store_upload(encode(Image.new("RGB", (40, 30), (1, 2, 3)), "PNG"), self.tmp)
        self.assertEqual(self.stored().size, (40, 30))

    def test_exif_orientation_is_applied(self):
        source = Image.new("RGB", (100, 50), (0, 0, 255))
        exif = Image.Exif()
        exif[0x0112] = 6                       # rotate 90 degrees clockwise when showing
        store_upload(encode(source, "JPEG", exif=exif), self.tmp)
        self.assertEqual(self.stored().size, (50, 100))

    def test_transparency_becomes_black(self):
        rgba = Image.new("RGBA", (20, 20), (255, 255, 255, 0))
        store_upload(encode(rgba, "PNG"), self.tmp)
        self.assertEqual(self.stored().convert("RGB").getpixel((5, 5)), (0, 0, 0))

    def test_animated_gif_keeps_frames_and_timing(self):
        store_upload(gif([(255, 0, 0), (0, 255, 0), (0, 0, 255)], [100, 200, 300]), self.tmp)
        renderer = PictureRenderer(self.tmp, 64, 64)
        self.assertTrue(renderer.animated())
        self.assertAlmostEqual(renderer.loop_seconds(), 0.6, places=2)
        image = renderer._images[0]
        self.assertEqual([renderer._frame_index(image, t) for t in (0.0, 0.15, 0.45, 0.61)], [0, 1, 2, 0])

    def test_too_many_frames_are_cut(self):
        frames = [Image.new("RGB", (8, 8), (i % 256, 0, 0)) for i in range(pic.MAX_FRAMES + 5)]
        data = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
        note = store_upload(data, self.tmp)
        self.assertIn("gekürzt", note)
        self.assertLessEqual(self.stored().n_frames, pic.MAX_FRAMES)

    def test_refused_uploads_keep_the_old_picture(self):
        store_upload(encode(Image.new("RGB", (20, 20), (9, 9, 9)), "PNG"), self.tmp)
        with open(first_path(self.tmp), "rb") as handle:
            before = handle.read()
        for data in (b"", b"not an image at all", b"GIF89a" + b"\x00" * 10,
                     encode(Image.new("RGB", (10, 10)), "BMP"),
                     b"\x00" * (pic.MAX_UPLOAD_BYTES + 1)):
            with self.assertRaises(PictureError, msg=data[:12]):
                store_upload(data, self.tmp)
        with open(first_path(self.tmp), "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_huge_png_is_refused_before_decoding(self):
        big = io.BytesIO()
        Image.new("1", (5000, 5000), 0).save(big, format="PNG")
        with self.assertRaises(PictureError) as ctx:
            store_upload(big.getvalue(), self.tmp)
        self.assertIn("zu groß", str(ctx.exception))

    def test_remove(self):
        self.assertFalse(remove_picture(self.tmp))
        store_upload(encode(Image.new("RGB", (20, 20)), "PNG"), self.tmp)
        self.assertTrue(remove_picture(self.tmp))
        self.assertFalse(picture_available(self.tmp))


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # left half red, right half blue, 128 x 64
        source = Image.new("RGB", (128, 64), (255, 0, 0))
        source.paste((0, 0, 255), (64, 0, 128, 64))
        store_upload(encode(source, "PNG"), self.tmp)
        self.r = PictureRenderer(self.tmp, 64, 64)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fill_crops_the_middle(self):
        image = self.r.frame({"fit": "fill", "intensity": 100})
        self.assertEqual(image.size, (64, 64))
        self.assertEqual((image.getpixel((5, 32)), image.getpixel((58, 32))), ((255, 0, 0), (0, 0, 255)))
        self.assertEqual(image.getpixel((5, 2)), (255, 0, 0))             # no black bars

    def test_fit_adds_black_bars(self):
        image = self.r.frame({"fit": "fit", "intensity": 100})
        self.assertEqual(image.getpixel((10, 2)), (0, 0, 0))
        self.assertEqual(image.getpixel((10, 61)), (0, 0, 0))
        self.assertEqual(image.getpixel((10, 32)), (255, 0, 0))

    def test_stretch_keeps_both_halves(self):
        image = self.r.frame({"fit": "stretch", "intensity": 100})
        self.assertEqual((image.getpixel((5, 5)), image.getpixel((58, 60))), ((255, 0, 0), (0, 0, 255)))

    def test_intensity(self):
        image = self.r.frame({"fit": "stretch", "intensity": 50})
        self.assertEqual(image.getpixel((5, 5)), (127, 0, 0))

    def test_settings_changes_and_new_upload_are_picked_up(self):
        self.assertEqual(self.r.frame({"fit": "stretch", "intensity": 100}).getpixel((5, 5)), (255, 0, 0))
        self.assertEqual(self.r.frame({"fit": "stretch", "intensity": 20}).getpixel((5, 5)), (51, 0, 0))
        store_upload(encode(Image.new("RGB", (64, 64), (0, 200, 0)), "PNG"), self.tmp)
        bump(self.tmp)
        self.assertEqual(self.r.frame({"fit": "stretch", "intensity": 100}).getpixel((5, 5)), (0, 200, 0))

    def test_missing_or_broken_file_gives_a_black_frame(self):
        broken = first_path(self.tmp)
        with open(broken, "wb") as handle:
            handle.write(b"junk")
        bump(self.tmp)
        self.assertEqual(self.r.frame({"fit": "fill", "intensity": 100}).getbbox(), None)
        self.assertFalse(self.r.available())
        self.assertFalse(self.r.animated())
        remove_picture(self.tmp)
        bump(self.tmp, 10)
        self.assertEqual(self.r.frame({"fit": "fill", "intensity": 100}).getbbox(), None)

    def test_preview_gif(self):
        data = self.r.preview_gif({"fit": "fill", "intensity": 100})
        image = Image.open(io.BytesIO(data))
        self.assertEqual((image.format, image.size, getattr(image, "n_frames", 1)), ("GIF", (256, 256), 1))
        store_upload(gif([(255, 0, 0), (0, 255, 0)], [300, 300]), self.tmp)
        bump(self.tmp)
        moving = Image.open(io.BytesIO(self.r.preview_gif({"fit": "fill", "intensity": 100})))
        self.assertGreater(getattr(moving, "n_frames", 1), 1)
        remove_picture(self.tmp)
        bump(self.tmp, 10)
        with self.assertRaises(PictureError):
            self.r.preview_gif({"fit": "fill", "intensity": 100})


class PlanTests(unittest.TestCase):
    def test_needs_the_switch_and_a_stored_picture(self):
        state = mk_state([])
        self.assertNotIn("picture", kinds_of(plan(state, NOW, pcfg(), BERLIN, STALE)))          # no file
        self.assertNotIn("picture", kinds_of(plan(state, NOW, pcfg(enabled=False), BERLIN, STALE, picture=True)))
        self.assertIn("picture", kinds_of(plan(state, NOW, pcfg(), BERLIN, STALE, picture=True)))

    def test_rotates_after_table_room_and_message(self):
        cfg = plan_cfg(picture=dict(DEFAULT_PICTURE, enabled=True, seconds=7),
                       message={"enabled": True, "text": "Hallo"})
        result = plan(mk_state([]), NOW, cfg, BERLIN, STALE, picture=True)
        self.assertEqual(kinds_of(result), ["table", "room", "banner", "picture"])
        self.assertEqual(result.items[-1].seconds, 7)

    def test_not_shown_while_a_game_is_live(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        self.assertEqual(kinds_of(plan(live, NOW, pcfg(), BERLIN, STALE, picture=True)), ["game"])

    def test_permanent_wins_over_games(self):
        live = mk_state([game("a", "Alpha", "Bravo")])
        cfg = pcfg(permanent=True)
        self.assertEqual(kinds_of(plan(live, NOW, cfg, BERLIN, STALE, picture=True)), ["picture"])
        self.assertEqual(kinds_of(plan(None, NOW, cfg, BERLIN, STALE, picture=True)), ["picture"])

    def test_permanent_message_wins_over_permanent_picture(self):
        cfg = plan_cfg(picture=dict(DEFAULT_PICTURE, enabled=True, permanent=True),
                       message={"enabled": True, "text": "Hallo", "permanent": True})
        self.assertEqual(kinds_of(plan(mk_state([]), NOW, cfg, BERLIN, STALE, picture=True)), ["banner"])

    def test_favorite_final_score_rotates_with_the_picture(self):
        final = mk_state([game("a", "Alpha", "Bravo", GameStatus.FINISHED, home_score=80, away_score=70)])
        result = plan(final, NOW, pcfg(), BERLIN, STALE, picture=True)
        self.assertEqual(kinds_of(result), ["game", "table", "room", "picture"])
        result = plan(final, NOW, plan_cfg(picture=dict(DEFAULT_PICTURE, enabled=True), gameday_results=False),
                      BERLIN, STALE, picture=True)
        self.assertEqual(kinds_of(result), ["game", "picture"])
        self.assertIn("das Bild", result.reason)

    def test_view_and_render(self):
        from sportstracker.runtime import build_renderer
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        store_upload(encode(Image.new("RGB", (64, 64), (0, 120, 0)), "PNG"), os.path.join(tmp, "data"))
        cfg = plan_cfg(picture=dict(DEFAULT_PICTURE, enabled=True), fonts_dir=os.path.join(ROOT, "fonts"))
        view = view_for_item(Item("picture", seconds=10), None, NOW, False, None, cfg, 0.0)
        self.assertEqual(view.kind, "picture")
        renderer = build_renderer(cfg, None, BERLIN, root=tmp)
        self.assertEqual(render_view(renderer, view, "").getpixel((10, 10)), (0, 120, 0))


class DisplayServiceTests(unittest.TestCase):
    def make(self, animated, **picture):
        settings = dict(DEFAULT_PICTURE, enabled=True, **picture)
        self.env = Env(picture=settings, sensor={"enabled": False})
        self.addCleanup(self.env.close)
        data = os.path.join(self.env.root, "data")
        if animated:
            store_upload(gif([(255, 0, 0), (0, 255, 0)], [200, 200]), data)
        else:
            store_upload(encode(Image.new("RGB", (64, 64), (0, 0, 255)), "PNG"), data)
        self.now = [utc(2026, 10, 30, 12, 0, 0)]       # a multiple of the 0.4 s loop: starts at frame 0
        self.display = FakeDisplay()
        self.service = DisplayService(self.env.config_path, display=self.display, root=self.env.root,
                                      now_func=lambda: self.now[0])
        write_state(os.path.join(data, "state.json"),
                    State(updated_at=self.now[0], provider="p", favorite_team_id="1", table=make_table(),
                          games=[], marks={}))

    def test_static_permanent_picture_is_drawn_once(self):
        self.make(False, permanent=True)
        self.assertTrue(self.service.step())
        self.assertFalse(self.service.step())
        self.assertEqual(self.display.shown[0].getpixel((3, 3)), (0, 0, 255))
        self.assertEqual(self.service.wait_seconds, 1.0)

    def test_animated_picture_follows_the_frames_and_pace(self):
        self.make(True, permanent=True)
        self.assertTrue(self.service.step())
        self.assertAlmostEqual(self.service.wait_seconds, 1 / 15, places=3)
        self.assertFalse(self.service.step())                         # same frame: nothing to draw
        self.now[0] += timedelta(milliseconds=250)
        self.assertTrue(self.service.step())
        first, second = self.display.shown[0], self.display.shown[1]
        self.assertEqual((first.getpixel((3, 3)), second.getpixel((3, 3))), ((255, 0, 0), (0, 255, 0)))

    def test_new_upload_is_shown_without_restart(self):
        self.make(False, permanent=True)
        self.service.step()
        path = os.path.join(self.env.root, "data")
        store_upload(encode(Image.new("RGB", (64, 64), (255, 255, 0)), "PNG"), path)
        bump(path)
        self.assertTrue(self.service.step())
        self.assertEqual(self.display.shown[-1].getpixel((3, 3)), (255, 255, 0))

    def test_rotation_pace_returns_to_normal(self):
        self.make(True, seconds=10)                    # idle list: table 10 s, room 10 s, picture 10 s
        self.now[0] = utc(2026, 10, 30, 12, 0, 25)     # slot 25 -> picture
        self.assertTrue(self.service.step())
        self.assertAlmostEqual(self.service.wait_seconds, 1 / 15, places=3)
        self.now[0] = utc(2026, 10, 30, 12, 0, 35)     # slot 35 -> table
        self.service.step()
        self.assertEqual(self.service.wait_seconds, 1.0)


class FormTests(unittest.TestCase):
    def test_form_values(self):
        form = {"fit": "fit", "intensity": "70", "seconds": "15", "enabled": "on"}
        new, errors = apply_picture_form({"picture": {"permanent": True}}, form, True)
        self.assertEqual(errors, [])
        self.assertEqual(new["picture"], dict(DEFAULT_PICTURE, enabled=True, fit="fit", intensity=70, permanent=False, seconds=15))

    def test_errors(self):
        base = {"fit": "fill", "intensity": "100", "seconds": "10"}
        for change in ({"fit": "zoom"}, {"intensity": "3"}, {"seconds": "x"}, {"seconds": "500"}):
            new, errors = apply_picture_form({}, dict(base, **change), True)
            self.assertTrue(errors, change)
        new, errors = apply_picture_form({}, dict(base, enabled="on"), False)
        self.assertIn("hochladen", errors[0])
        new, errors = apply_picture_form({}, base, False)               # switched off without a picture is fine
        self.assertEqual(errors, [])


class WebTests(Web050Base):
    def upload(self, data, name="bild.png", token_page="/picture"):
        token = self.token(token_page)
        return self.client.post("/picture/upload", data={"csrf_token": token, "image": (io.BytesIO(data), name)},
                                content_type="multipart/form-data")

    def data_dir(self):
        return os.path.join(self.tmp, "data")

    def test_page_and_navigation(self):
        page = self.client.get("/picture").get_data(as_text=True)
        self.assertIn("Noch kein Bild hochgeladen", page)
        self.assertIn('name="image"', page)
        self.assertIn(">Bild</a>", self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get("/picture/preview.gif").status_code, 404)

    def test_requires_login(self):
        anonymous = self.app.test_client()
        for path in ("/picture", "/picture/preview.gif"):
            self.assertEqual(anonymous.get(path).status_code, 302, path)
        self.assertEqual(anonymous.post("/picture/upload").status_code, 400)       # no CSRF token

    def test_upload_save_preview_delete(self):
        response = self.upload(encode(Image.new("RGB", (300, 200), (10, 200, 10)), "PNG"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(picture_available(self.data_dir()))
        page = self.client.get("/picture").get_data(as_text=True)
        self.assertIn("Bild gespeichert", page)
        self.assertIn("/picture/preview.gif", page)
        gif_data = self.client.get("/picture/preview.gif?fit=fit&intensity=60").get_data()
        self.assertEqual(Image.open(io.BytesIO(gif_data)).format, "GIF")
        self.assertEqual(self.client.get("/picture/preview.gif?fit=zoom&intensity=60").status_code, 400)
        self.post("/picture", page="/picture", fit="stretch", intensity="80", seconds="12", enabled="on", permanent="on")
        self.assertEqual(self.config()["picture"], dict(DEFAULT_PICTURE, enabled=True, fit="stretch", intensity=80,
                                                         permanent=True, seconds=12))
        shown = self.post("/picture", page="/picture", action="preview", fit="fit", intensity="40", seconds="5")
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(self.config()["picture"]["fit"], "stretch")                  # preview does not save
        self.post("/picture/delete", page="/picture")                                  # no confirmation box
        self.assertTrue(picture_available(self.data_dir()))
        self.post("/picture/delete", page="/picture", confirm="yes")
        self.assertFalse(picture_available(self.data_dir()))

    def test_activating_without_a_picture_is_refused(self):
        before = self.config()
        response = self.post("/picture", page="/picture", fit="fill", intensity="100", seconds="10", enabled="on")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.config(), before)

    def test_bad_uploads(self):
        for data, name in ((b"", "leer.png"), (b"hallo", "text.png"), (b"x" * (pic.MAX_UPLOAD_BYTES - 100), "gross.png")):
            response = self.upload(data, name)
            self.assertEqual(response.status_code, 302, name)
        self.assertFalse(picture_available(self.data_dir()))
        token = self.token("/picture")
        response = self.client.post("/picture/upload", data={"csrf_token": token}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)                                    # no file chosen
        self.assertIn("Bilddatei", self.client.get("/picture").get_data(as_text=True))

    def test_oversized_request_is_refused(self):
        response = self.upload(b"x" * (50 * 1024 * 1024 + 400_000))
        self.assertEqual(response.status_code, 413)

    def test_other_forms_stay_small(self):
        token = self.token("/message")
        response = self.client.post("/message", data={"csrf_token": token, "text": "x" * 100_000})
        self.assertEqual(response.status_code, 413)

    def test_dashboard_lists_the_picture_in_the_plan(self):
        self.upload(encode(Image.new("RGB", (64, 64), (0, 0, 200)), "PNG"))
        self.post("/picture", page="/picture", fit="fill", intensity="100", seconds="10", enabled="on")
        self.assertIn("Bild", self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get("/preview.png").status_code, 200)


if __name__ == "__main__":
    unittest.main()
