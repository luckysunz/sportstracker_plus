"""Picture gallery (0.6.0): up to 10 pictures, order, own display times, transitions, web pages."""

import io
import os
import shutil
import tempfile
import unittest

from PIL import Image

from sportstracker import picture as pic
from sportstracker.picture import PictureError, PictureRenderer, PictureStore

from test_web_050 import Web050Base


def png(color, size=(64, 64)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = PictureStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ids(self):
        return [item["id"] for item in self.store.items()]

    def test_limits(self):
        self.assertEqual(pic.MAX_IMAGES, 10)
        self.assertEqual(pic.MAX_UPLOAD_BYTES, 10 * 1024 * 1024)
        for index in range(10):
            self.store.add(png((index * 20, 0, 0)), "bild%d.png" % index)
        with self.assertRaises(PictureError) as ctx:
            self.store.add(png((1, 1, 1)))
        self.assertIn("voll", str(ctx.exception))
        self.assertEqual(self.store.count(), 10)

    def test_ten_mb_upload_is_allowed_more_is_not(self):
        # a valid PNG padded with a comment chunk up to just below 10 MB is accepted, one byte over is refused
        base = png((9, 9, 9), (16, 16))
        with self.assertRaises(PictureError):
            self.store.add(b"\x00" * (pic.MAX_UPLOAD_BYTES + 1))
        self.store.add(base + b"\x00" * (pic.MAX_UPLOAD_BYTES - len(base) - 10))     # trailing bytes are ignored
        self.assertEqual(self.store.count(), 1)

    def test_order_move_remove(self):
        a, b, c = (self.store.add(png(col))[0] for col in COLORS[:3])
        self.assertEqual(self.ids(), [a, b, c])
        self.assertTrue(self.store.move(c, -1))
        self.assertEqual(self.ids(), [a, c, b])
        self.assertTrue(self.store.move(a, 1))
        self.assertEqual(self.ids(), [c, a, b])
        self.assertFalse(self.store.move(c, -1))                       # already first
        self.assertFalse(self.store.move("deadbeef", 1))
        self.assertTrue(self.store.remove(a))
        self.assertEqual(self.ids(), [c, b])
        self.assertIsNone(self.store.path(a))
        self.assertFalse(self.store.remove(a))
        self.assertEqual(self.store.clear(), 2)
        self.assertEqual(self.store.count(), 0)

    def test_own_times(self):
        a, b = (self.store.add(png(col))[0] for col in COLORS[:2])
        self.store.set_seconds({a: 30, b: None})
        self.assertEqual([i["seconds"] for i in self.store.items()], [30, None])

    def test_missing_file_is_dropped_and_bad_ids_have_no_path(self):
        a, b = (self.store.add(png(col))[0] for col in COLORS[:2])
        os.unlink(self.store.path(a))
        self.assertEqual(self.ids(), [b])
        for bad in ("../x", "", "ABCDEF12", "abc", "deadbeef"):
            self.assertIsNone(self.store.path(bad))

    def test_migration_of_the_single_picture(self):
        legacy = os.path.join(self.tmp, "picture.png")
        with open(legacy, "wb") as handle:
            handle.write(png((5, 6, 7)))
        self.assertTrue(self.store.migrate())
        self.assertFalse(os.path.exists(legacy))
        self.assertEqual(self.store.count(), 1)
        self.assertFalse(self.store.migrate())


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = PictureStore(self.tmp)
        self.ids = [self.store.add(png(col))[0] for col in COLORS[:3]]
        self.r = PictureRenderer(self.tmp, 64, 64)
        self.s = {"fit": "stretch", "intensity": 100, "seconds": 10, "transition": "fade", "transition_ms": 1000,
                  "permanent": False}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def px(self, elapsed, **change):
        return self.r.frame(dict(self.s, **change), elapsed).getpixel((32, 32))

    def test_total_seconds_uses_own_times(self):
        self.assertEqual(self.r.total_seconds(self.s), 30)
        self.store.set_seconds({self.ids[1]: 20})
        self.assertEqual(PictureRenderer(self.tmp, 64, 64).total_seconds(self.s), 40)
        future = os.path.getmtime(self.store.manifest) + 5
        os.utime(self.store.manifest, (future, future))
        self.assertEqual(self.r.total_seconds(self.s), 40)              # a changed manifest is noticed

    def test_pictures_follow_each_other_and_the_round_repeats(self):
        self.assertEqual(self.px(0.0), (255, 0, 0))
        self.assertEqual(self.px(5.0), (255, 0, 0))
        self.assertEqual(self.px(15.0), (0, 255, 0))
        self.assertEqual(self.px(25.0), (0, 0, 255))
        self.assertEqual(self.px(35.0), (255, 0, 0))

    def test_fade_runs_at_the_start_of_the_next_picture(self):
        middle = self.px(10.5)                                          # half way from red to green
        self.assertTrue(120 <= middle[0] <= 135 and 120 <= middle[1] <= 135, middle)
        self.assertEqual(self.px(11.5), (0, 255, 0))

    def test_no_transition_in_the_first_slot_unless_permanent(self):
        self.assertEqual(self.px(0.2), (255, 0, 0))
        wrapped = self.px(0.5, permanent=True)                          # from the last picture back to the first
        self.assertTrue(wrapped[0] > 100 and wrapped[2] > 100, wrapped)

    def test_none_cuts_hard(self):
        self.assertEqual(self.px(10.3, transition="none"), (0, 255, 0))

    def test_slide_and_wipe_geometry(self):
        image = self.r.frame(dict(self.s, transition="slide"), 10.5)    # half way: left half red, right half green
        self.assertEqual((image.getpixel((5, 5)), image.getpixel((58, 5))), ((255, 0, 0), (0, 255, 0)))
        image = self.r.frame(dict(self.s, transition="wipe"), 10.5)     # top half green, bottom half red
        self.assertEqual((image.getpixel((5, 5)), image.getpixel((5, 58))), ((0, 255, 0), (255, 0, 0)))

    def test_random_changes_between_rounds(self):
        seen = set()
        for round_number in range(3):
            frame = self.r.frame(dict(self.s, transition="random"), 10.5)
            seen.add((frame.getpixel((5, 5)), frame.getpixel((5, 58))))
            self.r.frame(dict(self.s, transition="random"), 0.0)        # a new round starts
        self.assertGreater(len(seen), 1)

    def test_transition_is_shorter_than_half_the_picture_time(self):
        self.assertEqual(self.px(5.0 + 2.6, transition_ms=3000, seconds=5), (0, 255, 0))

    def test_redraw_hints(self):
        self.assertTrue(self.r.needs_fast_redraw(self.s, 10.2))
        self.assertFalse(self.r.needs_fast_redraw(self.s, 14.0))
        self.assertNotEqual(self.r.state_key(self.s, 14.0), self.r.state_key(self.s, 24.0))
        self.assertEqual(self.r.state_key(self.s, 14.0), self.r.state_key(self.s, 16.0))
        self.assertNotEqual(self.r.state_key(self.s, 10.1), self.r.state_key(self.s, 10.3))

    def test_single_picture_has_no_transition(self):
        for image_id in self.ids[1:]:
            self.store.remove(image_id)
        single = PictureRenderer(self.tmp, 64, 64)
        self.assertEqual(single.total_seconds(self.s), 10)
        self.assertFalse(single.needs_fast_redraw(self.s, 0.2))
        self.assertEqual(single.frame(dict(self.s, permanent=True), 0.2).getpixel((32, 32)), (255, 0, 0))

    def test_gallery_preview_is_a_time_lapse(self):
        data = self.r.preview_gif(self.s)
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.size, (256, 256))
        self.assertGreater(getattr(image, "n_frames", 1), 5)      # identical frames are merged by the GIF writer


class WebGalleryTests(Web050Base):
    def data_dir(self):
        return os.path.join(self.tmp, "data")

    def store(self):
        return PictureStore(self.data_dir())

    def upload(self, files):
        token = self.token("/picture")
        data = {"csrf_token": token, "image": [(io.BytesIO(content), name) for name, content in files]}
        return self.client.post("/picture/upload", data=data, content_type="multipart/form-data")

    def test_several_files_in_one_upload(self):
        self.upload([("a.png", png(COLORS[0])), ("b.png", png(COLORS[1])), ("kaputt.png", b"nope")])
        self.assertEqual(self.store().count(), 2)
        page = self.client.get("/picture").get_data(as_text=True)
        self.assertIn("2 Bilder gespeichert", page)
        self.assertIn("kaputt.png", page)
        self.assertIn("Galerie (2 von 10)", page)
        self.assertIn("multiple", page)

    def test_upload_stops_at_ten(self):
        self.upload([("%d.png" % i, png((i * 10, 0, 0))) for i in range(12)])
        self.assertEqual(self.store().count(), 10)
        self.assertIn("voll", self.client.get("/picture").get_data(as_text=True))

    def test_move_delete_and_thumbnail(self):
        self.upload([("a.png", png(COLORS[0])), ("b.png", png(COLORS[1]))])
        first, second = [i["id"] for i in self.store().items()]
        self.post("/picture/item/%s/move" % second, page="/picture", direction="up")
        self.assertEqual([i["id"] for i in self.store().items()], [second, first])
        thumb = self.client.get("/picture/item/%s.png" % first)
        self.assertEqual((thumb.status_code, thumb.mimetype), (200, "image/png"))
        self.assertEqual(self.client.get("/picture/item/deadbeef.png").status_code, 404)
        self.assertEqual(self.client.get("/picture/item/..%2Fx.png").status_code, 404)
        self.post("/picture/item/%s/delete" % first, page="/picture")
        self.assertEqual([i["id"] for i in self.store().items()], [second])
        self.post("/picture/item/zzzz/delete", page="/picture")

    def test_save_times_and_transition(self):
        self.upload([("a.png", png(COLORS[0])), ("b.png", png(COLORS[1]))])
        first, second = [i["id"] for i in self.store().items()]
        self.post("/picture", page="/picture", fit="fill", intensity="100", seconds="8", enabled="on",
                  transition="slide", transition_ms="500", **{"seconds_" + first: "25", "seconds_" + second: ""})
        picture = self.config()["picture"]
        self.assertEqual((picture["transition"], picture["transition_ms"], picture["seconds"]), ("slide", 500, 8))
        self.assertEqual([i["seconds"] for i in self.store().items()], [25, None])
        page = self.client.get("/picture").get_data(as_text=True)
        self.assertIn('value="25"', page)
        self.assertIn('<option value="slide" selected>', page)

    def test_bad_times_and_effects_change_nothing(self):
        self.upload([("a.png", png(COLORS[0]))])
        first = self.store().items()[0]["id"]
        before = self.config()
        for extra in ({"seconds_" + first: "2"}, {"seconds_" + first: "abc"}, {"transition": "spin"},
                      {"transition_ms": "99999"}):
            data = dict({"fit": "fill", "intensity": "100", "seconds": "10", "transition": "fade",
                         "transition_ms": "800"}, **extra)
            self.assertEqual(self.post("/picture", page="/picture", **data).status_code, 400, extra)
        self.assertEqual(self.config(), before)
        self.assertIsNone(self.store().items()[0]["seconds"])

    def test_old_single_picture_is_migrated_when_the_page_opens(self):
        os.makedirs(self.data_dir(), exist_ok=True)
        with open(os.path.join(self.data_dir(), "picture.png"), "wb") as handle:
            handle.write(png(COLORS[2]))
        page = self.client.get("/picture").get_data(as_text=True)
        self.assertIn("Galerie (1 von 10)", page)
        self.assertFalse(os.path.exists(os.path.join(self.data_dir(), "picture.png")))

    def test_gallery_preview(self):
        self.upload([("a.png", png(COLORS[0])), ("b.png", png(COLORS[1]))])
        data = self.client.get("/picture/preview.gif?fit=fill&intensity=100&seconds=5&transition=wipe&transition_ms=500").get_data()
        self.assertGreater(getattr(Image.open(io.BytesIO(data)), "n_frames", 1), 2)


if __name__ == "__main__":
    unittest.main()
