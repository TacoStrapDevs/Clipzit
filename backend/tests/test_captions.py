import re
import unittest

from clipzit.analysis import build_sentences
from clipzit.captions import ACTIVE_COLOUR_ASS, chunk_words, clip_words, to_ass, to_srt
from tests.helpers import SAMPLE, speech


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.sents = build_sentences(speech(SAMPLE))

    def test_words_are_retimed_relative_to_clip_start(self):
        s = self.sents[3]
        words = clip_words(self.sents, s.start - 0.2, s.end + 0.2)
        self.assertAlmostEqual(words[0].start, 0.2, places=2)
        self.assertEqual(" ".join(w.text for w in words), s.text)

    def test_trimming_shifts_captions_consistently(self):
        a = clip_words(self.sents, 10.0, 20.0)
        b = clip_words(self.sents, 12.0, 20.0)
        by_text_a = {(w.text, round(w.start + 10.0, 2)) for w in a}
        for w in b:
            if w.start == 0.0:  # a word straddling the cut is clamped to the clip start
                continue
            self.assertIn((w.text, round(w.start + 12.0, 2)), by_text_a)

    def test_chunk_limits(self):
        words = clip_words(self.sents, 0, 60)
        for ch in chunk_words(words, max_chars=24, max_words=4):
            self.assertLessEqual(len(ch), 4)

    def test_srt_is_ordered_and_non_overlapping(self):
        chunks = chunk_words(clip_words(self.sents, 0, 60))
        srt = to_srt(chunks)
        times = re.findall(r"(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})", srt)
        self.assertEqual(len(times), len(chunks))
        prev_end = -1
        for t in times:
            a = int(t[0]) * 3600000 + int(t[1]) * 60000 + int(t[2]) * 1000 + int(t[3])
            b = int(t[4]) * 3600000 + int(t[5]) * 60000 + int(t[6]) * 1000 + int(t[7])
            self.assertLess(a, b)
            self.assertGreaterEqual(a, prev_end)
            prev_end = b

    def test_ass_highlights_each_active_word(self):
        chunks = chunk_words(clip_words(self.sents, 0, 12))
        ass = to_ass(chunks, 1080, 1920)
        self.assertIn("PlayResX: 1080", ass)
        events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(len(events), sum(len(c) for c in chunks))
        self.assertTrue(all(ACTIVE_COLOUR_ASS in e for e in events))

    def test_ass_text_is_escaped(self):
        from clipzit.models import Word
        ass = to_ass([[Word(0, 1, "{bad}")]], 1080, 1920, highlight=False)
        self.assertNotIn("{bad}", ass)


if __name__ == "__main__":
    unittest.main()
