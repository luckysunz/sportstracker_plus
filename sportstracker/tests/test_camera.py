import io
import os
import queue
import tempfile
import time
import unittest

from sportstracker import camera as camera_module
from sportstracker.camera import CameraControl, CameraError, split_frames

JPEG = b"\xff\xd8\xff\xe0fakejpeg"


class FakeRunner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, cmd, wait=True, timeout=20.0):
        self.calls.append(list(cmd))
        tool = cmd[0]
        handler = self.responses.get(tool)
        if handler is None:
            return 127, "not found"
        return handler(cmd)


def shoot(data=JPEG, code=0, out=""):
    def handler(cmd):
        if code == 0:
            with open(cmd[cmd.index("-o") + 1], "wb") as handle:
                handle.write(data)
        return code, out
    return handler


class CameraTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.now = [100.0]

    def tearDown(self):
        self._tmp.cleanup()

    def camera(self, responses):
        self.runner = FakeRunner(responses)
        return CameraControl(self._tmp.name, self.runner, clock=lambda: self.now[0])

    def test_snapshot_command_and_cleanup(self):
        cam = self.camera({"rpicam-still": shoot()})
        self.assertEqual(cam.snapshot(640, 480), JPEG)
        cmd = self.runner.calls[0]
        self.assertEqual(cmd[:2], ["rpicam-still", "-n"])
        self.assertIn("640", cmd)
        self.assertIn("480", cmd)
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_cached_for_a_few_seconds_only(self):
        cam = self.camera({"rpicam-still": shoot()})
        cam.snapshot(640, 480)
        self.now[0] += 3
        cam.snapshot(640, 480)
        self.assertEqual(len(self.runner.calls), 1)
        cam.snapshot(320, 240)                       # other size -> new shot
        self.assertEqual(len(self.runner.calls), 2)
        self.now[0] += 6
        cam.snapshot(320, 240)
        self.assertEqual(len(self.runner.calls), 3)

    def test_falls_back_to_libcamera(self):
        cam = self.camera({"libcamera-still": shoot()})
        self.assertEqual(cam.snapshot(640, 480), JPEG)
        self.assertEqual([c[0] for c in self.runner.calls], ["rpicam-still", "libcamera-still"])

    def test_missing_tools(self):
        cam = self.camera({})
        with self.assertRaises(CameraError) as ctx:
            cam.snapshot(640, 480)
        self.assertIn("fehlen", str(ctx.exception))

    def test_failure_and_invalid_image(self):
        cam = self.camera({"rpicam-still": shoot(code=1, out="ERROR: no cameras available\n")})
        with self.assertRaises(CameraError) as ctx:
            cam.snapshot(640, 480)
        self.assertIn("no cameras available", str(ctx.exception))
        cam = self.camera({"rpicam-still": shoot(data=b"not a jpeg")})
        with self.assertRaises(CameraError):
            cam.snapshot(640, 480)
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_status(self):
        listing = "Available cameras\n-----------------\n0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx219@10)\n"
        cam = self.camera({"rpicam-hello": lambda cmd: (0, listing)})
        ok, text = cam.status()
        self.assertTrue(ok)
        self.assertIn("imx219", text)
        cam = self.camera({"rpicam-hello": lambda cmd: (0, "No cameras available!\n")})
        self.assertFalse(cam.status()[0])
        cam = self.camera({})
        self.assertFalse(cam.status()[0])


def jpeg(n):
    return b"\xff\xd8" + (b"%d" % n) * 5 + b"\xff\xd9"


class SplitFramesTest(unittest.TestCase):
    def test_complete_and_partial_frames(self):
        buf = bytearray(jpeg(1) + jpeg(2) + jpeg(3)[:6])
        self.assertEqual(split_frames(buf), [jpeg(1), jpeg(2)])
        buf += jpeg(3)[6:]
        self.assertEqual(split_frames(buf), [jpeg(3)])
        self.assertEqual(bytes(buf), b"")

    def test_garbage_before_the_first_image_is_dropped(self):
        buf = bytearray(b"junk" + jpeg(1))
        self.assertEqual(split_frames(buf), [jpeg(1)])

    def test_end_marker_inside_an_image_is_skipped(self):
        frame = b"\xff\xd8" + b"AA" + b"\xff\xd9" + b"BB" + b"\xff\xd9"     # embedded thumbnail style
        buf = bytearray(frame + jpeg(2))
        self.assertEqual(split_frames(buf), [frame, jpeg(2)])

    def test_nothing_without_a_start_marker(self):
        buf = bytearray(b"no image here")
        self.assertEqual(split_frames(buf), [])
        self.assertEqual(bytes(buf), b"")

    def test_split_start_marker_waits_for_the_next_bytes(self):
        buf = bytearray(jpeg(1) + b"\xff")                    # unclear yet whether a new image starts
        self.assertEqual(split_frames(buf), [])
        buf += b"\xd8" + b"xx" + b"\xff\xd9"
        self.assertEqual(split_frames(buf), [jpeg(1), b"\xff\xd8xx\xff\xd9"])


class FakeStdout:
    def __init__(self):
        self.queue = queue.Queue()

    def read(self, size):
        return self.queue.get()


class FakeProc:
    def __init__(self):
        self.stdout = FakeStdout()
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.stdout.queue.put(b"")

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True
        self.stdout.queue.put(b"")


class LiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.procs = []
        self.commands = []
        self.first = [jpeg(1)]
        self.stderr_text = b""
        self.missing = False
        self.runner = FakeRunner({"rpicam-still": shoot()})
        self.cam = CameraControl(self._tmp.name, self.runner, spawn=self.spawn, idle_grace=0.05)

    def tearDown(self):
        self.cam.stop_live()
        self._tmp.cleanup()

    def spawn(self, cmd, stderr):
        if self.missing:
            raise FileNotFoundError(cmd[0])
        self.commands.append(list(cmd))
        proc = FakeProc()
        if self.stderr_text:
            stderr.write(self.stderr_text)
        for frame in self.first:
            proc.stdout.queue.put(frame)
        self.procs.append(proc)
        return proc

    def wait_for(self, condition, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end:
            if condition():
                return True
            time.sleep(0.01)
        return condition()

    def test_frames_command_and_sharing(self):
        frames = self.cam.live_frames(640, 480, 10)
        self.assertEqual(next(frames), jpeg(1))
        cmd = self.commands[0]
        for part in ("rpicam-vid", "mjpeg", "640", "480", "10", "-"):
            self.assertIn(part, cmd)
        self.assertTrue(self.cam.live_active)
        second = self.cam.live_frames(640, 480, 10)           # a second viewer shares the process
        self.assertEqual(len(self.procs), 1)
        self.procs[0].stdout.queue.put(jpeg(2))
        self.assertEqual(next(frames), jpeg(2))
        self.assertEqual(next(second), jpeg(2))               # the newest frame, no backlog

    def test_process_stops_after_the_last_viewer_left(self):
        frames = self.cam.live_frames(640, 480, 10)
        next(frames)
        frames.close()
        self.assertTrue(self.wait_for(lambda: self.procs[0].terminated))
        self.assertFalse(self.cam.live_active)

    def test_viewer_returning_within_the_grace_time_keeps_the_process(self):
        cam = CameraControl(self._tmp.name, self.runner, spawn=self.spawn, idle_grace=0.5)
        frames = cam.live_frames(640, 480, 10)
        next(frames)
        frames.close()
        again = cam.live_frames(640, 480, 10)
        time.sleep(0.7)
        self.assertEqual(len(self.procs), 1)
        self.assertFalse(self.procs[0].terminated)
        again.close()
        cam.stop_live()

    def test_other_size_restarts_the_process(self):
        frames = self.cam.live_frames(640, 480, 10)
        next(frames)
        self.first = [jpeg(7)]
        other = self.cam.live_frames(320, 240, 5)
        self.assertEqual(next(other), jpeg(7))
        self.assertEqual(len(self.procs), 2)
        self.assertTrue(self.wait_for(lambda: self.procs[0].terminated))

    def test_capture_uses_the_live_frame_while_the_live_view_runs(self):
        frames = self.cam.live_frames(640, 480, 10)
        next(frames)
        self.assertEqual(self.cam.capture(1280, 960), jpeg(1))
        self.assertEqual(self.runner.calls, [])               # no still was taken

    def test_capture_without_live_takes_a_new_still_each_time(self):
        self.assertEqual(self.cam.capture(640, 480), JPEG)
        self.assertEqual(self.cam.capture(640, 480), JPEG)
        self.assertEqual(len(self.runner.calls), 2)

    def test_camera_is_free_again_after_the_live_view(self):
        frames = self.cam.live_frames(640, 480, 10)
        next(frames)
        self.cam.stop_live()
        self.assertEqual(self.cam.snapshot(640, 480), JPEG)

    def test_missing_tools(self):
        self.missing = True
        with self.assertRaises(CameraError) as ctx:
            self.cam.live_frames(640, 480, 10)
        self.assertIn("fehlen", str(ctx.exception))
        self.missing = False
        self.first = [jpeg(1)]
        self.assertEqual(next(self.cam.live_frames(640, 480, 10)), jpeg(1))      # the lock was released

    def test_no_picture_reports_the_error_of_the_process(self):
        self.first = [b""]
        self.stderr_text = b"ERROR: no cameras available\n"
        with self.assertRaises(CameraError) as ctx:
            self.cam.live_frames(640, 480, 10)
        self.assertIn("no cameras available", str(ctx.exception))

    def test_timeout_without_picture(self):
        old = camera_module.FIRST_FRAME_TIMEOUT
        camera_module.FIRST_FRAME_TIMEOUT = 0.2
        try:
            self.first = []
            with self.assertRaises(CameraError):
                self.cam.live_frames(640, 480, 10)
            self.assertTrue(self.wait_for(lambda: self.procs[0].terminated))
        finally:
            camera_module.FIRST_FRAME_TIMEOUT = old

    def test_stream_ends_when_the_process_ends(self):
        frames = self.cam.live_frames(640, 480, 10)
        next(frames)
        self.procs[0].stdout.queue.put(b"")
        self.assertEqual(list(frames), [])


if __name__ == "__main__":
    unittest.main()
