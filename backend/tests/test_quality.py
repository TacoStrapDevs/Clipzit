"""Clip-quality behaviours: topic boundaries, boilerplate, duplicates, alternatives."""
import unittest

from clipzit.analysis import (WEIGHTS, boundary_similarities, build_sentences, discover_clips, excerpt, hook_signals,
                              suggest_alternatives, term_counts, window_signals)
from tests.helpers import speech

TOPIC_A = [
    "Guitar practice works best when you split each song into tiny pieces.",
    "Practicing the pieces slowly builds guitar muscle memory for every chord change.",
    "Slow guitar practice also fixes buzzing strings before they become habits.",
    "Chord changes become smooth when practice pieces stay short and repeated.",
]
TOPIC_B = [
    "Sourdough bread needs a healthy starter fed every single morning.",
    "A bubbly sourdough starter doubles in size within six hours of feeding.",
    "Flour and water ratios decide how sour the bread tastes after baking.",
    "Bake the sourdough loaf in a hot dutch oven for forty minutes.",
]


class QualityTests(unittest.TestCase):
    def sents(self, texts):
        return build_sentences(speech(texts, gap=0.4))

    def test_boundary_similarity_drops_at_topic_change(self):
        sents = self.sents(TOPIC_A + TOPIC_B)
        b = boundary_similarities([term_counts(s) for s in sents])
        within = [b[k] for k in (1, 2, 3, 5, 6, 7)]
        self.assertLess(b[4], min(within), (b[4], within))
        self.assertEqual((b[0], b[-1]), (0.0, 0.0))

    def test_clips_prefer_to_stay_on_one_topic(self):
        sents = self.sents(TOPIC_A + TOPIC_B)
        total = sents[-1].end + 1
        clips = discover_clips(sents, total, 8, 22, 4)
        self.assertTrue(clips)
        best = clips[0]
        a_words = {"guitar", "chord", "practice"}
        b_words = {"sourdough", "bread", "starter"}
        text = best["transcript"].lower()
        mixes = any(w in text for w in a_words) and any(w in text for w in b_words)
        self.assertFalse(mixes, "top clip should not straddle the topic change: " + best["transcript"])
        self.assertIn("coherence", best["signals"])

    def test_signoff_is_penalised(self):
        good = self.sents(["Learning scales slowly is the fastest way to improve your timing today.",
                           "Play each note evenly and listen closely to the spacing between them."])
        bad = self.sents(["Learning scales slowly is the fastest way to improve your timing today.",
                          "Thanks for watching and don't forget to subscribe to the channel."])
        a = window_signals(good, good[0].start, good[-1].end, 5, 30)
        b = window_signals(bad, bad[0].start, bad[-1].end, 5, 30)
        self.assertGreater(a[0]["completeness"], b[0]["completeness"] + 20)
        self.assertTrue(any("sign-off" in t for t in b[2]))

    def test_intro_boilerplate_is_a_weak_hook(self):
        weak, notes = hook_signals("Hey guys welcome back to the channel today.")
        strong, _ = hook_signals("Nobody tells you the biggest mistake beginners make.")
        self.assertLess(weak, 45)
        self.assertGreater(strong, weak + 25)
        self.assertTrue(any("boilerplate" in n for n in notes))

    def test_question_answered_gets_credit(self):
        s = self.sents(["Why does your tempo drift when you play fast passages?",
                        "Because you speed up on easy notes and rush through the hard ones."])
        sig, reasons, _t, _e = window_signals(s, s[0].start, s[-1].end, 5, 30)
        self.assertIn("Asks a question and answers it", reasons)

    def test_repeated_material_is_not_returned_twice(self):
        texts = TOPIC_A + ["And now a short unrelated pause for the audience to think."] + TOPIC_A
        sents = self.sents(texts)
        clips = discover_clips(sents, sents[-1].end + 1, 8, 22, 8, max_overlap=0.3)
        seen = [set(c["transcript"].lower().split()) for c in clips]
        for i, a in enumerate(seen):
            for b in seen[i + 1:]:
                self.assertLessEqual(len(a & b) / len(a | b), 0.6)

    def test_description_is_verbatim_excerpt(self):
        sents = self.sents(TOPIC_A)
        desc = excerpt(sents)
        for part in TOPIC_A:
            if part in desc:
                break
        else:
            self.fail("excerpt must contain whole sentences from the transcript")
        self.assertLessEqual(len(desc), 400)

    def test_alternatives_are_sentence_aligned_and_ranked(self):
        sents = self.sents(TOPIC_A + TOPIC_B)
        total = sents[-1].end + 1
        clip = discover_clips(sents, total, 8, 22, 1)[0]
        alts = suggest_alternatives(sents, clip["start"], clip["end"], total, 8, 22)
        self.assertTrue(alts)
        self.assertEqual([a["score"] for a in alts], sorted((a["score"] for a in alts), reverse=True))
        for a in alts:
            self.assertGreater(a["end"], a["start"])
            self.assertTrue(any(abs(a["start"] - s.start) < 0.5 for s in sents))
        self.assertEqual(suggest_alternatives(sents, 900, 950, total, 8, 22), [])

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
