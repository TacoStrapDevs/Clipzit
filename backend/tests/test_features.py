"""Render features: pacing cuts, enhancement, music bed, caption presets, hook overlay."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from clipzit import media
from clipzit.analysis import build_sentences
from clipzit.captions import PRESETS, chunk_words, clip_words, style_from, to_ass
from clipzit.models import Word
from tests.helpers import SAMPLE, make_video, speech


def rgb_frame(path, t, w=108, h=192):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.uint8).reshape(h, w, 3).astype(int)


class CaptionStyleTests(unittest.TestCase):
    def setUp(self):
        self.chunks = chunk_words(clip_words(build_sentences(speech(SAMPLE)), 0, 8))

    def test_all_seven_presets_exist_and_produce_distinct_styles(self):
        self.assertEqual(set(PRESETS), {"bold", "minimal", "creator", "highlight", "karaoke", "clean", "professional"})
        styles = set()
        for name in PRESETS:
            ass = to_ass(self.chunks, 1080, 1920, style=style_from(name), clip_duration=8)
            styles.add(next(l for l in ass.splitlines() if l.startswith("Style: Default")))
        self.assertEqual(len(styles), 7)

    def test_karaoke_uses_sweep_tags_one_event_per_chunk(self):
        ass = to_ass(self.chunks, 1080, 1920, style=style_from("karaoke"))
        events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(len(events), len(self.chunks))
        self.assertTrue(all("\\kf" in e for e in events))

    def test_bold_is_uppercase_and_minimal_has_no_highlight(self):
        bold = to_ass(self.chunks, 1080, 1920, style=style_from("bold"))
        self.assertIn("WHY", bold)
        minimal = to_ass(self.chunks, 1080, 1920, style=style_from("minimal"))
        self.assertNotIn("\\c&H", minimal)

    def test_overrides_and_validation(self):
        st = style_from("creator", position="top", size_scale=1.5, primary="#00FF00")
        ass = to_ass(self.chunks, 1080, 1920, style=st)
        self.assertIn("&H0000FF00", ass)
        self.assertIn(",8,", next(l for l in ass.splitlines() if l.startswith("Style: Default")))
        with self.assertRaises(ValueError):
            style_from("nope")
        with self.assertRaises(ValueError):
            style_from("creator", primary="red")

    def test_hook_and_watermark_events(self):
        ass = to_ass(self.chunks, 1080, 1920, hook_text="Why do people quit?", hook_seconds=3, watermark="@clipzit",
                     clip_duration=8)
        self.assertIn("Hook,,0,0,0,,{\\fad(150,250)}Why do people quit?", ass)
        self.assertIn("Mark,,0,0,0,,@clipzit", ass)
        none = to_ass(self.chunks, 1080, 1920, clip_duration=8)
        self.assertNotIn(",Hook,", none)

    def test_text_cannot_inject_ass_override_tags(self):
        ass = to_ass([[Word(0, 1, "{\\fs200}evil")]], 1080, 1920, hook_text="{\\an5}x")
        self.assertNotIn("{\\fs200}", ass)
        self.assertNotIn("{\\an5}", ass)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RenderFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.video = make_video(cls.tmp / "v.mp4", 20)
        cls.info = media.probe(cls.video)
        cls.black = cls.tmp / "black.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=25:d=8",
                        "-f", "lavfi", "-i", "sine=frequency=300:duration=8", "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", "-c:a", "aac", "-shortest", str(cls.black)], check=True)
        cls.binfo = media.probe(cls.black)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_keep_intervals_shorten_output_and_keep_audio(self):
        out = self.tmp / "tight.mp4"
        res = media.render_clip(self.video, out, 0, 10, self.info, aspect="9:16",
                                keep=[(0.0, 3.0), (5.0, 7.0), (8.0, 10.0)])
        self.assertAlmostEqual(res.duration, 7.0, delta=0.5)
        self.assertTrue(res.has_audio)

    def test_single_keep_interval(self):
        res = media.render_clip(self.video, self.tmp / "one.mp4", 0, 10, self.info, aspect="1:1", keep=[(2.0, 6.0)])
        self.assertAlmostEqual(res.duration, 4.0, delta=0.5)

    def test_empty_keep_is_rejected(self):
        with self.assertRaises(media.MediaError):
            media.render_clip(self.video, self.tmp / "e.mp4", 0, 10, self.info, keep=[(3.0, 3.01)])

    def test_enhancement_changes_picture(self):
        a, b = self.tmp / "plain.mp4", self.tmp / "bright.mp4"
        media.render_clip(self.video, a, 0, 4, self.info, aspect="16:9", fade=False)
        media.render_clip(self.video, b, 0, 4, self.info, aspect="16:9", fade=False,
                          enhance=media.Enhance(brightness=0.25, saturation=1.6, sharpen=0.8))
        self.assertGreater(rgb_frame(b, 1).mean(), rgb_frame(a, 1).mean() + 8)

    def test_music_bed_is_mixed_and_louder_than_voice_only(self):
        music = self.tmp / "m.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
                        str(music)], check=True)  # shorter than the clip: must loop
        silent = make_video(self.tmp / "quiet.mp4", 6, silence=(0, 6))
        sinfo = media.probe(silent)
        out = self.tmp / "mixed.mp4"
        res = media.render_clip(silent, out, 0, 5, sinfo, aspect="9:16", music=(music, 0.8), normalize=False)
        self.assertTrue(res.has_audio)
        vol = subprocess.run(["ffmpeg", "-i", str(out), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        mean = float(vol.split("mean_volume:")[1].split("dB")[0])
        self.assertGreater(mean, -40, vol[-300:])  # the voice track is silent, so this is the music

    def test_music_works_for_video_without_audio(self):
        music = self.tmp / "m2.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=3",
                        str(music)], check=True)
        mute = make_video(self.tmp / "mute.mp4", 5, audio=False)
        res = media.render_clip(mute, self.tmp / "mm.mp4", 0, 4, media.probe(mute), aspect="1:1", music=(music, 0.5))
        self.assertTrue(res.has_audio)

    def test_hook_overlay_is_yellow_box_at_top_and_disappears(self):
        d = self.tmp / "hook"
        d.mkdir(exist_ok=True)
        ass = d / "subs.ass"
        ass.write_text(to_ass([], 1080, 1920, hook_text="Why do people quit guitar", hook_seconds=2,
                              clip_duration=6), encoding="utf-8")
        out = self.tmp / "hooked.mp4"
        media.render_clip(self.black, out, 0, 6, self.binfo, aspect="9:16", ass_path=ass, fade=False)

        def yellow(t):
            f = rgb_frame(out, t)
            return int(((f[:, :, 0] > 200) & (f[:, :, 1] > 150) & (f[:, :, 2] < 90)).sum())
        self.assertGreater(yellow(1.0), 80)
        self.assertEqual(yellow(4.5), 0)
        top = rgb_frame(out, 1.0)[:96]
        self.assertGreater(((top[:, :, 0] > 200) & (top[:, :, 2] < 90)).sum(), 80)  # in the upper half

    def test_clean_preset_draws_dark_box_and_creator_highlights_in_yellow(self):
        words = [Word(0.2, 0.6, "hello"), Word(0.7, 1.1, "brave"), Word(1.2, 1.6, "world")]
        bright = self.tmp / "bright.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x808080:s=1280x720:r=25:d=3",
                        "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a",
                        "aac", "-shortest", str(bright)], check=True)
        binfo = media.probe(bright)
        d = self.tmp / "cap"
        d.mkdir(exist_ok=True)
        for preset in ("clean", "creator"):
            ass = d / "subs.ass"
            ass.write_text(to_ass([words], 1080, 1920, style=style_from(preset)), encoding="utf-8")
            out = self.tmp / f"{preset}.mp4"
            media.render_clip(bright, out, 0, 3, binfo, aspect="9:16", ass_path=ass, fade=False)
            f = rgb_frame(out, 0.8, 270, 480)
            if preset == "clean":
                self.assertGreater(int((f.sum(axis=2) < 150).sum()), 100, "clean preset should draw a dark box")
            else:
                self.assertGreater(int(((f[:, :, 0] > 200) & (f[:, :, 1] > 150) & (f[:, :, 2] < 90)).sum()), 5,
                                   "active word should be yellow")


if __name__ == "__main__":
    unittest.main()
