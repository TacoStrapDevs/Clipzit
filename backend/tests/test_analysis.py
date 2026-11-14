import unittest

from clipzit.analysis import (build_sentences, discover_clips, find_fillers, hook_signals,
                              overall_score, search_transcript, WEIGHTS)
from clipzit.models import Segment, Word, segments_from_dicts
from tests.helpers import SAMPLE, speech


class SentenceTests(unittest.TestCase):
    def test_sentences_split_on_punctuation_and_keep_word_times(self):
        segs = speech(SAMPLE)
        sents = build_sentences(segs)
        self.assertEqual(len(sents), len(SAMPLE))
        self.assertEqual(sents[0].text, SAMPLE[0])
        for s in sents:
            self.assertLess(s.start, s.end)
            self.assertFalse(s.approximate_timing)

    def test_segments_without_word_times_are_flagged_approximate(self):
        segs = [Segment(0, 6, "This is one sentence. And this is another one.")]
        sents = build_sentences(segs)
        self.assertEqual(len(sents), 2)
        self.assertTrue(all(s.approximate_timing for s in sents))

    def test_large_gap_splits_unpunctuated_speech(self):
        words = [Word(0, .4, "hello"), Word(.5, .9, "there"), Word(3.0, 3.4, "again")]
        sents = build_sentences([Segment(0, 3.4, "hello there again", words)])
        self.assertEqual(len(sents), 2)

    def test_malformed_transcript_rejected(self):
        with self.assertRaises(ValueError):
            segments_from_dicts([{"start": 0}])
        with self.assertRaises(ValueError):
            segments_from_dicts({"nope": 1})


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.segs = speech(SAMPLE)
        self.sents = build_sentences(self.segs)
        self.total = self.sents[-1].end + 2

    def clips(self, **kw):
        args = dict(video_duration=self.total, min_seconds=15, max_seconds=30, max_clips=5)
        args.update(kw)
        return discover_clips(self.sents, **args)

    def test_finds_clips_within_duration_range(self):
        clips = self.clips()
        self.assertGreater(len(clips), 0)
        for c in clips:
            self.assertGreaterEqual(c["duration"], 14.5)
            self.assertLessEqual(c["duration"], 30.0)
            self.assertGreaterEqual(c["start"], 0)
            self.assertLessEqual(c["end"], self.total)

    def test_boundaries_never_cut_words(self):
        for c in self.clips():
            inside = [s for s in self.sents if s.start >= c["start"] - 1e-6 and s.end <= c["end"] + 1e-6]
            self.assertGreaterEqual(len(inside), 1)
            # the clip must begin before its first sentence's first word and end after its last word...
            first, last = inside[0], inside[-1]
            self.assertLessEqual(c["start"], first.start)
            self.assertGreaterEqual(c["end"], last.end)
            # ...and must not reach into a neighbouring sentence's words.
            for s in self.sents:
                if s not in inside:
                    self.assertTrue(s.end <= c["start"] + 1e-6 or s.start >= c["end"] - 1e-6,
                                    f"clip {c['start']}-{c['end']} cuts into sentence {s.start}-{s.end}")

    def test_no_heavy_overlap_between_selected_clips(self):
        clips = self.clips(max_clips=8)
        for i, a in enumerate(clips):
            for b in clips[i + 1:]:
                inter = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
                self.assertLessEqual(inter / min(a["duration"], b["duration"]), 0.3 + 1e-9)

    def test_scores_are_bounded_and_explainable(self):
        for c in self.clips():
            self.assertTrue(0 <= c["score"] <= 100)
            self.assertEqual(set(c["signals"]), set(WEIGHTS))
            self.assertEqual(c["score"], overall_score(c["signals"]))
            self.assertTrue(c["reasons"])
            self.assertIn("not a prediction", c["score_kind"])

    def test_scores_are_deterministic(self):
        a = [(c["start"], c["end"], c["score"]) for c in self.clips()]
        b = [(c["start"], c["end"], c["score"]) for c in self.clips()]
        self.assertEqual(a, b)

    def test_hooks_are_verbatim_sentences(self):
        for c in self.clips():
            self.assertTrue(c["hooks"])
            for h in c["hooks"]:
                base = h["text"].rstrip("…").rstrip()
                self.assertIn(base, " ".join(SAMPLE), "hook must be quoted from the transcript")
                self.assertTrue(h["verbatim"])

    def test_question_opening_scores_higher_than_weak_opening(self):
        strong, _ = hook_signals("Why do most people quit learning guitar after three months?")
        weak, _ = hook_signals("And so then it was kind of there.")
        self.assertGreater(strong, weak + 25)

    def test_mid_thought_start_is_penalised_in_completeness(self):
        # same material, but the only candidate window starts with a dangling "And ..."
        texts = ["And then it kept going on for a while without any real context at all here today.",
                 "We tried three different approaches before finding the one that finally worked well.",
                 "The final version is faster and cheaper than anything we built before it."]
        sents = build_sentences(speech(texts))
        clips = discover_clips(sents, sents[-1].end + 1, 8, 40, 3)
        full = [c for c in clips if c["transcript"].startswith("And then")]
        for c in full:
            self.assertLess(c["signals"]["completeness"], 80)

    def test_silence_rich_window_is_rejected(self):
        sil = [(0.0, 18.0)]
        clips = self.clips(silences=sil)
        for c in clips:
            self.assertGreater(c["metrics"]["speech_ratio"], 0.55)

    def test_invalid_range_raises(self):
        with self.assertRaises(ValueError):
            discover_clips(self.sents, 100, 30, 10)

    def test_filler_detection(self):
        segs = speech(["Um so I think uh this is the most important point we can make today."])
        sents = build_sentences(segs)
        fl = find_fillers(sents, 0, 100)
        self.assertEqual([f["text"] for f in fl], ["Um", "uh"])

    def test_search_returns_timestamps(self):
        res = search_transcript(self.sents, "secret instrument")
        self.assertEqual(len(res), 1)
        self.assertIn("secret", res[0]["text"])
        self.assertGreater(res[0]["end"], res[0]["start"])
        self.assertEqual(search_transcript(self.sents, "   "), [])


if __name__ == "__main__":
    unittest.main()
