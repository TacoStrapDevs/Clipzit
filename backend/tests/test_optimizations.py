"""Behaviour added by the optimisation pass."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from clipzit import audio, media, transcribe
from clipzit.analysis import build_sentences, discover_clips
from clipzit.audio import EnergyProfile, silences_from_energy
from clipzit.config import Settings
from clipzit.render_service import purge_renders
from tests.helpers import make_video, speech


class SilencesFromEnergyTests(unittest.TestCase):
    def test_intervals_and_trailing_silence(self):
        db = [-20.0] * 8 + [-60.0] * 6 + [-20.0] * 4 + [-60.0] * 3     # hop 0.25 s
        p = EnergyProfile(0.25, db)
        got = silences_from_energy(p)
        self.assertEqual(got[0], (2.0, 3.5))
        self.assertEqual(got[1], (4.5, 5.25))      # 0.75 s trailing silence is kept
        short = EnergyProfile(0.25, [-20.0] * 4 + [-60.0] * 1 + [-20.0] * 4)
        self.assertEqual(silences_from_energy(short), [])  # 0.25 s is shorter than min_dur

    @unittest.skipUnless(shutil.which("ffmpeg") and audio.available(), "ffmpeg/numpy missing")
    def test_agrees_with_ffmpeg_silencedetect_on_real_audio(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            v = make_video(tmp / "v.mp4", 14, silence=(5, 8))
            ffm = media.detect_silences(v, 60, total=14)
            mine = silences_from_energy(audio.energy_profile(v, 60), total=14)
            self.assertTrue(any(abs(a - 5) < 0.7 and abs(b - 8) < 0.7 for a, b in ffm), ffm)
            self.assertTrue(any(abs(a - 5) < 0.7 and abs(b - 8) < 0.7 for a, b in mine), mine)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FallbackTests(unittest.TestCase):
    def test_weak_material_still_returns_flagged_clips_instead_of_nothing(self):
        texts = ["And so yeah we kind of talked about that thing for a while today.",
                 "Then it was sort of the same as before more or less honestly.",
                 "Because the other one was a bit like that other one anyway.",
                 "So we went on and on about it until it was time to leave.",
                 "And that was about it for the day I guess more or less."]
        sents = build_sentences(speech(texts, gap=0.4))
        total = sents[-1].end + 1
        clips = discover_clips(sents, total, 8, 20, 5, min_score=95)  # impossible bar for the strict pass
        self.assertTrue(clips, "must not return an empty result")
        self.assertLessEqual(len(clips), 3)
        for c in clips:
            self.assertTrue(c["low_confidence"])
            self.assertTrue(c["suggestions"][0].startswith("Low confidence"))

    def test_good_material_is_not_flagged(self):
        from tests.helpers import SAMPLE
        sents = build_sentences(speech(SAMPLE))
        clips = discover_clips(sents, sents[-1].end + 2, 15, 30, 3)
        self.assertTrue(clips)
        self.assertFalse(any(c["low_confidence"] for c in clips))


class ModelCacheTests(unittest.TestCase):
    def test_model_is_loaded_once_per_configuration(self):
        calls = []

        class Fake:
            def __init__(self, name, device, compute_type):
                calls.append((name, device, compute_type))

        transcribe._models.clear()
        a = transcribe._get_model(Fake, "base", "cpu", "int8")
        b = transcribe._get_model(Fake, "base", "cpu", "int8")
        c = transcribe._get_model(Fake, "small", "cpu", "int8")
        self.assertIs(a, b)
        self.assertIsNot(a, c)
        self.assertEqual(len(calls), 2)
        transcribe._models.clear()


class PurgeTests(unittest.TestCase):
    def test_old_and_oversized_renders_are_removed_recent_kept(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            s = Settings(data_dir=tmp)
            r = s.jobs_dir / ("a" * 32) / "renders"
            r.mkdir(parents=True)
            old, new = r / "old.mp4", r / "new.mp4"
            for f in (old, new):
                f.write_bytes(b"x" * 1000)
                f.with_suffix(".json").write_text("{}")
            os.utime(old, (time.time() - 10 * 86400,) * 2)
            self.assertEqual(purge_renders(s, max_age_days=7), 1)
            self.assertFalse(old.exists() or old.with_suffix(".json").exists())
            self.assertTrue(new.exists())
            # size cap: the oldest goes first
            older = r / "older.mp4"
            older.write_bytes(b"y" * 1000)
            os.utime(older, (time.time() - 60,) * 2)
            self.assertEqual(purge_renders(s, max_bytes_per_job=1500), 1)
            self.assertFalse(older.exists())
            self.assertTrue(new.exists())
            tmpdir = s.jobs_dir / ("a" * 32) / "tmpabc"
            tmpdir.mkdir()
            os.utime(tmpdir, (time.time() - 7200,) * 2)
            purge_renders(s)
            self.assertFalse(tmpdir.exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
