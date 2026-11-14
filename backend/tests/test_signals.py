"""Signals added for stronger clip selection: curiosity hooks, rarity, story arc, delivery energy."""
import shutil
import tempfile
import unittest
from pathlib import Path

from clipzit import audio
from clipzit.analysis import (Ctx, boundary_similarities, build_idf, build_sentences, discover_clips, hook_signals,
                              term_counts, window_signals)
from clipzit.audio import EnergyProfile, delivery_score
from tests.helpers import make_video, speech


class HookPatternTests(unittest.TestCase):
    def test_curiosity_contrarian_and_list_openers_beat_plain_statements(self):
        plain, _ = hook_signals("The weather was fine on that particular afternoon.")
        for text, note in [
            ("Here's why most gyms never make you stronger.", "curiosity loop"),
            ("Everyone thinks cardio burns fat but that is actually wrong.", "contrarian"),
            ("Three mistakes ruin every beginner budget.", "concrete list"),
            ("Nobody tells you how long recovery really takes.", "unspoken"),
            ("Stop wasting money on supplements.", "warning"),
        ]:
            score, notes = hook_signals(text)
            self.assertGreater(score, plain + 15, text)
            self.assertTrue(any(note in n.lower() for n in notes), (text, notes))

    def test_pattern_bonus_is_capped(self):
        score, _ = hook_signals("Here's why nobody tells you the three mistakes everyone thinks are wrong?")
        self.assertLessEqual(score, 100)


class RarityTests(unittest.TestCase):
    def test_distinctive_topic_scores_higher_information_than_repeated_chatter(self):
        chatter = ["Yeah so we talk about things and stuff all the time here.",
                   "Yeah so people talk about things and stuff every single day."] * 6
        rare = ["Hydrothermal vents sustain chemosynthetic bacteria far below sunlight."]
        sents = build_sentences(speech(chatter + rare, gap=0.4))
        counts = [term_counts(s) for s in sents]
        idf, norm = build_idf(counts)
        bsim = boundary_similarities(counts)
        n = len(sents)
        common = window_signals(sents[0:2], sents[0].start, sents[1].end, 3, 30, None, Ctx(bsim, 0, 1, idf, norm))
        distinct = window_signals(sents[n - 1:n], sents[-1].start, sents[-1].end, 3, 30, None,
                                  Ctx(bsim, n - 1, n - 1, idf, norm))
        self.assertGreater(distinct[0]["information"], common[0]["information"])
        self.assertIn("Covers something distinctive in this video", distinct[1])


class StoryArcTests(unittest.TestCase):
    def test_setup_turn_payoff_is_recognised(self):
        arc = ["When I was twenty I spent every weekend at the track losing races badly.",
               "My lap times never moved no matter how hard I pushed the car.",
               "But then a coach showed me that braking earlier makes corners faster.",
               "So I changed one habit and finally cut four seconds in a month."]
        flat = ["Cars have four wheels and an engine under the front hood area.",
                "Tracks are usually made of asphalt and marked with painted lines.",
                "Drivers wear helmets and fireproof suits during every official event.",
                "Fuel is stored in a tank near the rear axle of most vehicles."]
        a = build_sentences(speech(arc, gap=0.4))
        f = build_sentences(speech(flat, gap=0.4))
        sa = window_signals(a, a[0].start, a[-1].end, 5, 40)
        sf = window_signals(f, f[0].start, f[-1].end, 5, 40)
        self.assertIn("Has a setup, a turn and a payoff", sa[1])
        self.assertNotIn("Has a setup, a turn and a payoff", sf[1])


class DeliveryTests(unittest.TestCase):
    def test_delivery_score_rewards_loud_dynamic_windows(self):
        quiet = [-45.0 + (i % 2) for i in range(80)]
        loud = [-18.0 + (6 if i % 4 == 0 else 0) for i in range(80)]
        prof = EnergyProfile(0.25, quiet + loud)
        q, rq = delivery_score(prof, 0, 19)
        l, rl = delivery_score(prof, 20, 39)
        self.assertGreater(l, q + 25)
        self.assertLess(rq, 0)
        self.assertGreater(rl, 0)
        self.assertEqual(delivery_score(None, 0, 10), (60, None))
        self.assertEqual(delivery_score(prof, 5, 5.2), (60, None))

    def test_energetic_clip_outranks_flat_clip_and_explains_why(self):
        texts = ["Why do people quit learning guitar after three months of lessons?",
                 "The biggest mistake is practicing the wrong things for hours every single day.",
                 "Learn one song you love and break it into four small pieces of practice.",
                 "Sourdough bread needs a healthy starter fed every single morning before baking.",
                 "A bubbly starter doubles in size within six hours when the kitchen stays warm.",
                 "Bake the loaf in a hot dutch oven for forty minutes and let it cool."]
        sents = build_sentences(speech(texts, gap=0.4))
        total = sents[-1].end + 1
        quiet_part = int(total * 0.62 / 0.25)  # most of the video is quiet, so "loud" means louder than usual
        prof = EnergyProfile(0.25, [-40.0] * quiet_part + [-15.0] * (int(total / 0.25) - quiet_part + 8))
        with_e = discover_clips(sents, total, 8, 16, 6, energy=prof)
        top_late = [c for c in with_e if c["start"] > total / 2 - 1]
        top_early = [c for c in with_e if c["end"] < total / 2 + 1]
        self.assertTrue(top_late and top_early)
        self.assertGreater(max(c["signals"]["delivery"] for c in top_late),
                           max(c["signals"]["delivery"] for c in top_early) + 20)
        self.assertTrue(any("Energetic delivery" in r for c in top_late for r in c["reasons"]))


@unittest.skipUnless(shutil.which("ffmpeg") and audio.available(), "ffmpeg/numpy missing")
class EnergyProfileTests(unittest.TestCase):
    def test_profile_from_real_audio_tracks_volume(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            # quiet first 5 s, loud last 5 s
            import subprocess
            v = tmp / "v.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=10",
                            "-f", "lavfi", "-i", "sine=frequency=300:duration=10", "-af",
                            "volume='if(lt(t,5),0.02,0.6)':eval=frame", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(v)], check=True)
            prof = audio.energy_profile(v, 60)
            self.assertAlmostEqual(len(prof.db) * prof.hop, 10, delta=0.6)
            early = sum(prof.window(1, 4)) / len(prof.window(1, 4))
            late = sum(prof.window(6, 9)) / len(prof.window(6, 9))
            self.assertGreater(late, early + 15)
            p = tmp / "e.json"
            prof.save(p)
            again = EnergyProfile.load(p)
            self.assertEqual(len(again.db), len(prof.db))
            self.assertIsNone(EnergyProfile.load(tmp / "missing.json"))
            with self.assertRaises(Exception):
                audio.energy_profile(tmp / "nope.mp4", 10)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
