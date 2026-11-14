import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from clipzit import media, render_service, vision
from clipzit.analysis import build_sentences, discover_clips
from clipzit.config import Settings
from clipzit.pipeline import job_dir
from clipzit.render_service import RenderOptions, build_zip, render_cached, render_job_clip
from tests.helpers import SAMPLE, make_video, speech


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RenderServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.settings = Settings(data_dir=cls.tmp / "data")
        cls.job_id = "a" * 32
        d = job_dir(cls.settings, cls.job_id)
        d.mkdir(parents=True)
        cls.segs = speech(SAMPLE, gap=1.6)  # lots of dead air between sentences
        cls.sents = build_sentences(cls.segs)
        total = cls.sents[-1].end + 3
        make_video(d / "source.mp4", int(total) + 1)
        (d / "transcript.json").write_text(json.dumps({"segments": [s.to_dict() for s in cls.segs]}))
        cls.job = {"id": cls.job_id, "name": "Demo", "video_path": str(d / "source.mp4")}
        cls.clips = discover_clips(cls.sents, total, 10, 24, 3)
        assert len(cls.clips) >= 2

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, **kw):
        out = self.tmp / f"r_{len(list(self.tmp.glob('r_*')))}.mp4"
        res = render_job_clip(self.settings, self.job, self.clips[0], RenderOptions.from_dict(kw), out)
        return out, res

    def test_default_render_is_valid_vertical_with_audio(self):
        out, res = self.render()
        self.assertEqual((res["info"].width, res["info"].height), (1080, 1920))
        self.assertTrue(res["info"].has_audio)
        self.assertTrue(out.exists() and out.stat().st_size > 1000)
        self.assertTrue(res["words"])

    def test_pacing_shortens_clip_and_captions_follow_the_new_timeline(self):
        _o, plain = self.render(pacing="off")
        _o, tight = self.render(pacing="medium", remove_fillers=True)
        self.assertLess(tight["info"].duration, plain["info"].duration - 1.0)
        self.assertGreater(tight["stats"]["removed_seconds"], 1.0)
        self.assertLessEqual(tight["words"][-1].end, tight["info"].duration + 0.2)
        self.assertEqual(len(tight["words"]), len(plain["words"]))  # no filler words in this transcript

    def test_platform_preset_and_duration_warning(self):
        with mock.patch.dict(render_service.PLATFORMS, {"shorts": ("9:16", 30, 5)}):
            _o, res = self.render(platform="shorts")
        self.assertTrue(any("allows up to 5s" in w for w in res["warnings"]))
        self.assertEqual((res["info"].width, res["info"].height), (1080, 1920))

    @unittest.skipIf(vision.available(), "face model available")
    def test_auto_frame_without_opencv_warns_and_still_renders(self):
        _o, res = self.render(auto_frame=True)
        self.assertTrue(any("Face framing unavailable" in w for w in res["warnings"]))
        self.assertFalse(res["face_tracked"])

    def test_hook_watermark_and_look_options_render(self):
        _o, res = self.render(hook_text="Why do people quit?", watermark_text="@clipzit", caption_preset="karaoke",
                              brightness=0.1, contrast=1.1, saturation=1.2, sharpen=0.5, aspect="1:1")
        self.assertEqual((res["info"].width, res["info"].height), (1080, 1080))

    def test_invalid_options_are_rejected(self):
        for bad in ({"aspect": "3:7"}, {"zoom": 9}, {"caption_preset": "nope"}, {"pacing": "wild"},
                    {"bitrate": "lots"}, {"mystery": 1}, {"platform": "myspace"}, {"fps": 5}):
            with self.assertRaises(ValueError, msg=str(bad)):
                RenderOptions.from_dict(bad)

    def test_music_id_validation(self):
        with self.assertRaises(ValueError):
            render_service.music_path(self.settings, "../../etc/passwd")
        with self.assertRaises(ValueError):
            render_service.music_path(self.settings, "b" * 32)
        self.assertIsNone(render_service.music_path(self.settings, None))
        mdir = self.settings.data_dir / "music"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / ("c" * 32 + ".wav")).write_bytes(b"x")
        (mdir / ("c" * 32 + ".name")).write_text("track")  # sidecar must never be picked as audio
        self.assertEqual(render_service.music_path(self.settings, "c" * 32).suffix, ".wav")

    def test_cache_returns_same_file_without_rerendering(self):
        opts = RenderOptions.from_dict({"captions": False})
        a, meta_a = render_cached(self.settings, self.job, self.clips[1], opts)
        m1 = a.stat().st_mtime_ns
        b, meta_b = render_cached(self.settings, self.job, self.clips[1], opts)
        self.assertEqual(a, b)
        self.assertEqual(m1, b.stat().st_mtime_ns)
        self.assertEqual(meta_a, meta_b)

    def test_zip_export_contains_validated_clips_srt_and_metadata(self):
        z = self.tmp / "bundle.zip"
        calls = []
        manifest = build_zip(self.settings, self.job, self.clips[:2], RenderOptions.from_dict({"platform": "reels"}),
                             z, on_progress=calls.append)
        self.assertEqual(calls, [0.5, 1.0])
        with zipfile.ZipFile(z) as zf:
            self.assertIsNone(zf.testzip())
            names = set(zf.namelist())
            self.assertTrue({"clip01.mp4", "clip02.mp4", "clip01.srt", "clip01.json", "manifest.json"} <= names)
            extracted = self.tmp / "unz"
            zf.extractall(extracted)
            info = media.probe(extracted / "clip01.mp4")
            self.assertEqual((info.width, info.height), (1080, 1920))
            self.assertTrue(info.has_audio)
            meta = json.loads(zf.read("clip01.json"))
            self.assertIn("not a prediction", meta["score_kind"])
            self.assertEqual(len(json.loads(zf.read("manifest.json"))["clips"]), 2)
        self.assertEqual(len(manifest), 2)
        self.assertIn("-->", zipfile.ZipFile(z).read("clip01.srt").decode())

    def test_missing_source_is_a_clear_error(self):
        job = dict(self.job, video_path=str(self.tmp / "gone.mp4"))
        with self.assertRaises(media.MediaError):
            render_job_clip(self.settings, job, self.clips[0], RenderOptions(), self.tmp / "x.mp4")


if __name__ == "__main__":
    unittest.main()
