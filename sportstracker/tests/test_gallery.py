import io
import os
import tempfile
import unittest
from datetime import datetime

from sportstracker.gallery import MB, Gallery, GalleryError, valid_name

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def jpeg_bytes(size=(64, 48), color=(200, 30, 30), pad=0):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue() + b"\0" * pad


class GalleryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.gallery = Gallery(os.path.join(self._tmp.name, "gallery"))

    def tearDown(self):
        self._tmp.cleanup()

    def save(self, minute, pad=0, limit=200):
        return self.gallery.save(jpeg_bytes(pad=pad), datetime(2026, 10, 30, 13, minute, 0), limit)

    def test_save_and_list_newest_first(self):
        self.save(1)
        name = self.save(2)
        self.assertEqual(name, "2026-10-30_13-02-00.jpg")
        self.assertEqual([p.name for p in self.gallery.list()], [name, "2026-10-30_13-01-00.jpg"])
        self.assertEqual(self.gallery.list()[0].taken, datetime(2026, 10, 30, 13, 2, 0))
        self.assertEqual(os.listdir(self.gallery.directory).count(".thumbs"), 0)

    def test_same_second_gets_a_suffix(self):
        first = self.save(5)
        second = self.save(5)
        self.assertEqual((first, second), ("2026-10-30_13-05-00.jpg", "2026-10-30_13-05-00-2.jpg"))
        self.assertEqual(len(self.gallery.list()), 2)

    def test_not_a_jpeg_is_refused(self):
        with self.assertRaises(GalleryError):
            self.gallery.save(b"nope", datetime(2026, 10, 30, 13, 0, 0), 200)
        self.assertEqual(self.gallery.list(), [])

    def test_limit_deletes_the_oldest_but_never_the_new_photo(self):
        size = len(jpeg_bytes(pad=MB // 2))
        for minute in range(1, 5):
            self.save(minute, pad=MB // 2, limit=1)           # 1 MB fits one photo of ~0.5 MB twice
        names = [p.name for p in self.gallery.list()]
        self.assertEqual(names[0], "2026-10-30_13-04-00.jpg")
        self.assertLessEqual(self.gallery.usage(), 1 * MB)
        self.assertGreater(size, MB // 2)
        self.assertNotIn("2026-10-30_13-01-00.jpg", names)

    def test_a_single_photo_bigger_than_the_limit_is_kept(self):
        name = self.gallery.save(jpeg_bytes(pad=2 * MB), datetime(2026, 10, 30, 13, 0, 0), 1)
        self.assertEqual([p.name for p in self.gallery.list()], [name])

    def test_enforce_limit_after_lowering_it(self):
        for minute in range(1, 4):
            self.save(minute, pad=MB - 2000)                  # a little under 1 MB each
        deleted = self.gallery.enforce_limit(2)
        self.assertEqual(deleted, ["2026-10-30_13-01-00.jpg"])

    def test_names_are_validated(self):
        self.assertTrue(valid_name("2026-10-30_13-02-00.jpg"))
        self.assertTrue(valid_name("2026-10-30_13-02-00-12.jpg"))
        for bad in ("", "../x.jpg", "a/b.jpg", "2026-10-30_13-02-00.png", "x2026-10-30_13-02-00.jpg", "config.json"):
            self.assertFalse(valid_name(bad), bad)
            self.assertIsNone(self.gallery.path(bad))
        self.assertFalse(self.gallery.delete("../../etc/passwd"))

    def test_other_files_are_ignored(self):
        self.save(1)
        with open(os.path.join(self.gallery.directory, "notes.txt"), "w") as handle:
            handle.write("x")
        self.assertEqual(len(self.gallery.list()), 1)

    @unittest.skipIf(Image is None, "Pillow missing")
    def test_thumbnail_is_small_cached_and_removed_with_the_photo(self):
        name = self.gallery.save(jpeg_bytes(size=(640, 480)), datetime(2026, 10, 30, 13, 0, 0), 200)
        data = self.gallery.thumbnail(name)
        with Image.open(io.BytesIO(data)) as thumb:
            self.assertLessEqual(max(thumb.size), 160)
        self.assertTrue(os.path.exists(os.path.join(self.gallery.thumbs, name)))
        self.assertEqual(self.gallery.thumbnail(name), data)
        self.assertTrue(self.gallery.delete(name))
        self.assertFalse(os.path.exists(os.path.join(self.gallery.thumbs, name)))
        self.assertIsNone(self.gallery.thumbnail(name))

    @unittest.skipIf(Image is None, "Pillow missing")
    def test_damaged_photo_has_no_thumbnail(self):
        name = self.gallery.save(b"\xff\xd8broken", datetime(2026, 10, 30, 13, 0, 0), 200)
        with self.assertRaises(GalleryError):
            self.gallery.thumbnail(name)

    def test_clear(self):
        self.save(1)
        self.save(2)
        self.assertEqual(self.gallery.clear(), 2)
        self.assertEqual(self.gallery.list(), [])


if __name__ == "__main__":
    unittest.main()
