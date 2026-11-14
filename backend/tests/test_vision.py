import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from clipzit import media
from clipzit.vision import build_path, crop_x_expression, piecewise_expr, sample_positions
from tests.helpers import make_video

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


class PathTests(unittest.TestCase):
    def test_stable_subject_gives_flat_path(self):
        p = build_path([(i, 0.5) for i in range(10)], 10)
        self.assertEqual({x for _t, x in p}, {0.5})

    def test_jitter_inside_deadband_is_ignored(self):
        p = build_path([(i, 0.5 + (0.03 if i % 2 else -0.03)) for i in range(12)], 12)
        self.assertEqual(len({round(x, 2) for _t, x in p}), 1)

    def test_single_glitch_does_not_move_the_crop(self):
        s = [(i, 0.3) for i in range(10)]
        s[5] = (5, 0.9)
        p = build_path(s, 10)
        self.assertEqual({round(x, 2) for _t, x in p}, {0.3})

    def test_real_move_is_followed_with_easing(self):
        p = build_path([(i, 0.3 if i < 5 else 0.7) for i in range(12)], 12)
        xs = [round(x, 2) for _t, x in p]
        self.assertEqual(xs[0], 0.3)
        self.assertEqual(xs[-1], 0.7)
        ts = [t for t, _x in p]
        self.assertEqual(ts, sorted(ts))

    def test_missing_detections_fall_back_to_centre(self):
        self.assertEqual({x for _t, x in build_path([(i, None) for i in range(4)], 4)}, {0.5})
        self.assertEqual({x for _t, x in build_path([], 4)}, {0.5})

    def test_expression_is_clamped_inside_the_frame(self):
        expr = crop_x_expression([(0.0, 0.0), (5.0, 1.0)], 1280, 404)
        self.assertIn("if(lt(t,", expr)
        self.assertNotIn("-", expr.replace("(t-", "").replace("+(-", "").replace("-0", "-0"))  # no negative positions
        self.assertEqual(piecewise_expr([(0.0, 3.0)]), "3.00")


@unittest.skipUnless(shutil.which("ffmpeg") and HAVE_NUMPY, "ffmpeg/numpy missing")
class SamplingAndRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.video = make_video(cls.tmp / "v.mp4", 10)
        cls.static = make_video(cls.tmp / "static.mp4", 10, pattern="smptebars")
        cls.info = media.probe(cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_frames_are_sampled_once_per_second_and_detector_is_used(self):
        calls = []

        def fake(frame):
            calls.append(frame.shape)
            return [(0.25, 0.1), (0.75, 0.02)]  # two faces: the larger one wins

        out = sample_positions(str(self.video), 2.0, 6.0, self.info, detector=fake)
        self.assertEqual(len(out), 4)
        self.assertTrue(all(abs(x - 0.25) < 1e-9 for _t, x in out))
        self.assertEqual(calls[0], (270, 480))

    def gray(self, path, t):
        import subprocess
        import numpy as np
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=108:192,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True)
        return np.frombuffer(r.stdout, dtype=np.uint8).astype(float)

    def test_dynamic_crop_follows_path_in_a_real_render(self):
        path = [(0.0, 0.2), (2.0, 0.2), (2.5, 0.8), (5.0, 0.8)]
        moving = self.tmp / "follow.mp4"
        res = media.render_clip(self.static, moving, 0, 5, self.info, aspect="9:16", face_path=path, fade=False)
        self.assertEqual((res.width, res.height), (1080, 1920))
        fixed = self.tmp / "fixed.mp4"
        media.render_clip(self.static, fixed, 0, 5, self.info, aspect="9:16", fade=False)
        # The source is a static test card: with a fixed crop two frames are (nearly) identical,
        # with a moving crop window they are clearly different.
        still = abs(self.gray(fixed, 1.0) - self.gray(fixed, 4.0)).mean()
        moved = abs(self.gray(moving, 1.0) - self.gray(moving, 4.0)).mean()
        self.assertLess(still, 3.0)
        self.assertGreater(moved, 10.0)


if __name__ == "__main__":
    unittest.main()
