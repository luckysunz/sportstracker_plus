"""Camera 0.5.7: flip, brightness, sharpness for photo and live view; motion detection in the live view."""

import io
import itertools
import tempfile
import time
import unittest

from PIL import Image, ImageDraw

from sportstracker.camera import CameraControl, ImageOptions
from sportstracker.config import DEFAULT_CAMERA, parse_config
from sportstracker.errors import ConfigError
from sportstracker.motion import MotionDetector, MotionSettings

from test_camera import FakeProc, FakeRunner, shoot


def picture(box=None, size=(640, 480)):
    image = Image.new("RGB", size, (90, 90, 90))
    if box:
        ImageDraw.Draw(image).rectangle(box, fill=(235, 235, 235))
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


class ImageOptionsTest(unittest.TestCase):
    def test_defaults_pass_nothing(self):
        self.assertEqual(ImageOptions().args(), [])

    def test_all_options(self):
        args = ImageOptions(True, True, 0.2, 2.0).args()
        self.assertEqual(args, ["--hflip", "--vflip", "--brightness", "0.2", "--sharpness", "2"])
        self.assertEqual(ImageOptions(False, True, -0.3, 0.0).args(), ["--vflip", "--brightness", "-0.3", "--sharpness", "0"])


class StillOptionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runner = FakeRunner({"rpicam-still": shoot()})
        self.cam = CameraControl(self._tmp.name, self.runner)

    def tearDown(self):
        self._tmp.cleanup()

    def test_still_gets_the_options(self):
        self.cam.snapshot(640, 480, options=ImageOptions(True, True, 0.1, 1.5))
        cmd = self.runner.calls[0]
        for part in ("--hflip", "--vflip", "--brightness", "0.1", "--sharpness", "1.5"):
            self.assertIn(part, cmd)
        self.assertEqual(cmd[-2], "-o")                        # the output file stays last

    def test_cache_is_per_option_set(self):
        self.cam.snapshot(640, 480)
        self.cam.snapshot(640, 480)
        self.assertEqual(len(self.runner.calls), 1)
        self.cam.snapshot(640, 480, options=ImageOptions(hflip=True))
        self.assertEqual(len(self.runner.calls), 2)


class LiveOptionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.procs, self.commands, self.first = [], [], [picture()]
        self.cam = CameraControl(self._tmp.name, FakeRunner({"rpicam-still": shoot()}), spawn=self.spawn,
                                 idle_grace=0.05, clock=lambda: next(self.ticks) * 0.3)
        self.ticks = itertools.count()

    def tearDown(self):
        self.cam.stop_live()
        self._tmp.cleanup()

    def spawn(self, cmd, stderr):
        self.commands.append(list(cmd))
        proc = FakeProc()
        for frame in self.first:
            proc.stdout.queue.put(frame)
        self.procs.append(proc)
        return proc

    def wait_for(self, check, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end:
            if check():
                return True
            time.sleep(0.01)
        return check()

    def test_live_command_gets_the_options_before_the_output(self):
        frames = self.cam.live_frames(640, 480, 10, options=ImageOptions(True, True, 0.2, 2.0))
        next(frames)
        cmd = self.commands[0]
        for part in ("--hflip", "--vflip", "--brightness", "0.2", "--sharpness", "2"):
            self.assertIn(part, cmd)
        self.assertEqual(cmd[-3:], ["--flush", "-o", "-"])
        frames.close()

    def test_other_options_restart_the_process(self):
        first = self.cam.live_frames(640, 480, 10, options=ImageOptions())
        next(first)
        second = self.cam.live_frames(640, 480, 10, options=ImageOptions(hflip=True))
        next(second)
        self.assertEqual(len(self.procs), 2)

    def test_motion_calls_the_handler_with_the_changed_frame(self):
        saved = []

        def handler(frame):
            saved.append(frame)
            return "2026-10-31_18-00-00.jpg"

        still = picture()
        moved = picture((100, 150, 260, 380))
        self.first = [still] * 14
        frames = self.cam.live_frames(640, 480, 10, motion=MotionSettings(3, 60), on_motion=handler)
        next(frames)
        self.assertTrue(self.wait_for(lambda: self.procs[0].stdout.queue.empty()))
        self.assertEqual(saved, [])
        self.assertTrue(self.cam.motion_status().active)
        self.procs[0].stdout.queue.put(moved)
        self.assertTrue(self.wait_for(lambda: len(saved) == 1))
        self.assertEqual(saved[0], moved)
        self.assertTrue(self.wait_for(lambda: self.cam.motion_status().count == 1))
        status = self.cam.motion_status()
        self.assertEqual(status.last_name, "2026-10-31_18-00-00.jpg")
        self.assertGreater(status.changed, status.threshold)
        frames.close()

    def test_without_a_handler_nothing_is_watched(self):
        self.first = [picture()] * 14
        frames = self.cam.live_frames(640, 480, 10, motion=MotionSettings(3, 60))
        next(frames)
        self.assertFalse(self.cam.motion_status().active)
        frames.close()

    def test_a_failing_handler_does_not_stop_the_live_view(self):
        def handler(frame):
            raise OSError("Platte voll")

        self.first = [picture()] * 14
        frames = self.cam.live_frames(640, 480, 10, motion=MotionSettings(3, 60), on_motion=handler)
        next(frames)
        self.assertTrue(self.wait_for(lambda: self.procs[0].stdout.queue.empty()))
        self.procs[0].stdout.queue.put(picture((100, 150, 260, 380)))
        self.assertTrue(self.wait_for(lambda: "Platte voll" in self.cam.motion_status().error))
        self.assertEqual(self.cam.motion_status().count, 0)
        self.assertTrue(self.cam.live_active)
        frames.close()


class DetectorTest(unittest.TestCase):
    def detector(self, level=3, pause=60, warmup=2):
        self.now = [0.0]
        return MotionDetector(MotionSettings(level, pause), clock=lambda: self.now[0], warmup=warmup)

    def test_still_picture_is_no_motion(self):
        det = self.detector()
        self.assertEqual([det.feed(picture()) for _ in range(6)], [False] * 6)
        self.assertEqual(det.changed, 0.0)

    def test_warmup_frames_never_fire(self):
        det = self.detector(warmup=3)
        det.feed(picture())
        self.assertFalse(det.feed(picture((100, 150, 300, 400))))

    def test_movement_fires_once_per_pause(self):
        det = self.detector()
        for _ in range(4):
            det.feed(picture())
        self.assertTrue(det.feed(picture((100, 150, 260, 380))))
        self.now[0] = 10
        self.assertFalse(det.feed(picture((300, 150, 460, 380))))          # still in the pause
        self.now[0] = 100
        self.assertTrue(det.feed(picture((100, 150, 260, 380))))

    def test_small_change_depends_on_the_sensitivity(self):
        small = picture((300, 220, 340, 250))                              # about 0.4 % of the picture
        low, high = self.detector(level=1), self.detector(level=5)
        for det in (low, high):
            for _ in range(4):
                det.feed(picture())
        self.assertFalse(low.feed(small))
        self.assertTrue(high.feed(small))

    def test_damaged_frame_is_ignored(self):
        det = self.detector()
        self.assertFalse(det.feed(b"\xff\xd8not a jpeg\xff\xd9"))
        self.assertFalse(det.feed(picture()))

    def test_picture_size_change_starts_again(self):
        det = self.detector()
        for _ in range(4):
            det.feed(picture())
        self.assertFalse(det.feed(picture(size=(320, 240))))


class ConfigTest(unittest.TestCase):
    def camera(self, **changes):
        data = {"camera": dict(DEFAULT_CAMERA, **changes)}
        return parse_config(data).camera

    def test_defaults(self):
        camera = parse_config({}).camera
        self.assertTrue(camera["hflip"] and camera["vflip"])
        self.assertEqual((camera["brightness"], camera["sharpness"], camera["motion_enabled"]), (0, 1.0, False))

    def test_old_config_without_the_new_keys_still_loads(self):
        camera = parse_config({"camera": {"enabled": True, "width": 640, "height": 480}}).camera
        self.assertEqual((camera["width"], camera["motion_pause"], camera["motion_sensitivity"]), (640, 60, 3))

    def test_ranges(self):
        for key, bad in (("brightness", 51), ("brightness", -51), ("brightness", 1.5), ("sharpness", 5), ("sharpness", -1),
                         ("sharpness", "x"), ("hflip", "yes"), ("motion_enabled", 1), ("motion_sensitivity", 0),
                         ("motion_sensitivity", 6), ("motion_pause", 4), ("motion_pause", 3601)):
            with self.assertRaises(ConfigError, msg="%s=%r" % (key, bad)):
                self.camera(**{key: bad})

    def test_sharpness_may_be_a_whole_number(self):
        self.assertEqual(self.camera(sharpness=2)["sharpness"], 2.0)


if __name__ == "__main__":
    unittest.main()
