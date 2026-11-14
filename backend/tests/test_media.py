import shutil
import tempfile
import unittest
from pathlib import Path

from clipzit import media
from clipzit.analysis import build_sentences
from clipzit.captions import chunk_words, clip_words, to_ass
from tests.helpers import SAMPLE, make_video, speech


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.video = make_video(cls.tmp / "src.mp4", 20, silence=(8, 11))
        cls.info = media.probe(cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_probe(self):
        self.assertAlmostEqual(self.info.duration, 20, delta=0.5)
        self.assertEqual((self.info.width, self.info.height), (1280, 720))
        self.assertTrue(self.info.has_audio and self.info.has_video)

    def test_probe_rejects_garbage(self):
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(b"this is not a video" * 100)
        with self.assertRaises(media.MediaError):
            media.probe(bad)

    def test_silence_detection_finds_gap(self):
        sil = media.detect_silences(self.video, 60, total=self.info.duration)
        self.assertTrue(any(abs(s - 8) < 0.6 and abs(e - 11) < 0.6 for s, e in sil), sil)

    def test_render_all_aspects_have_exact_dimensions_audio_and_duration(self):
        for aspect, mode in [("9:16", "fill"), ("9:16", "blur"), ("1:1", "fill"), ("16:9", "fill"),
                             ("4:5", "fill"), ("original", "fill")]:
            out = self.tmp / f"out_{aspect.replace(':', 'x')}_{mode}.mp4"
            res = media.render_clip(self.video, out, 2.0, 7.0, self.info, aspect=aspect, mode=mode)
            exp = self.info and (media.ASPECTS.get(aspect) or (1280, 720))
            self.assertEqual((res.width, res.height), exp, f"{aspect}/{mode}")
            self.assertTrue(res.has_audio)
            self.assertAlmostEqual(res.duration, 5.0, delta=0.6)
            self.assertGreater(out.stat().st_size, 1000)

    def test_render_with_burned_captions_and_zoom(self):
        sents = build_sentences(speech(SAMPLE))
        words = clip_words(sents, 0.0, 6.0)
        d = self.tmp / "caps"
        d.mkdir()
        ass = d / "subs.ass"
        ass.write_text(to_ass(chunk_words(words), 1080, 1920), encoding="utf-8")
        out = self.tmp / "captioned.mp4"
        res = media.render_clip(self.video, out, 0.0, 6.0, self.info, aspect="9:16", ass_path=ass,
                                zoom=1.3, crop_x=0.2)
        self.assertEqual((res.width, res.height), (1080, 1920))
        # captions must actually change the picture: compare a frame with and without them
        plain = self.tmp / "plain.mp4"
        media.render_clip(self.video, plain, 0.0, 6.0, self.info, aspect="9:16", zoom=1.3, crop_x=0.2)
        import subprocess
        def frame_hash(p):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(p), "-frames:v", "1",
                                "-f", "md5", "-"], capture_output=True, text=True, check=True)
            return r.stdout.strip()
        self.assertNotEqual(frame_hash(out), frame_hash(plain))

    def test_invalid_ranges_and_aspect_rejected(self):
        out = self.tmp / "x.mp4"
        with self.assertRaises(media.MediaError):
            media.render_clip(self.video, out, 5, 5.1, self.info)
        with self.assertRaises(media.MediaError):
            media.render_clip(self.video, out, 0, 5, self.info, aspect="3:7")

    def test_crop_box_stays_inside_source_and_even(self):
        for zoom in (1.0, 1.5, 2.0):
            for cx in (0.0, 0.5, 1.0):
                cw, ch, x, y = media.crop_box(1280, 720, 1080, 1920, zoom, cx, 0.5)
                self.assertEqual((cw % 2, ch % 2), (0, 0))
                self.assertLessEqual(x + cw, 1280)
                self.assertLessEqual(y + ch, 720)
                self.assertAlmostEqual(cw / ch, 9 / 16, delta=0.02)

    def test_video_without_audio_renders(self):
        silent = make_video(self.tmp / "noaudio.mp4", 6, audio=False)
        info = media.probe(silent)
        self.assertFalse(info.has_audio)
        res = media.render_clip(silent, self.tmp / "na_out.mp4", 0, 4, info, aspect="1:1")
        self.assertFalse(res.has_audio)


if __name__ == "__main__":
    unittest.main()
