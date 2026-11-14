import unittest

from clipzit.captions import retime
from clipzit.models import Word
from clipzit.pacing import compute_keep


def words_with_gaps():
    # "so um this is a test" with a long pause after "is" and a long lead-in silence
    spec = [("So", 1.0, 1.3), ("um", 1.4, 1.6), ("this", 1.7, 2.0), ("is.", 2.1, 2.4),
            ("A", 4.4, 4.5), ("test", 4.6, 5.0), ("really", 5.1, 5.5)]
    return [Word(a, b, t) for t, a, b in spec]


class PacingTests(unittest.TestCase):
    def test_off_keeps_everything(self):
        keep, stats = compute_keep(words_with_gaps(), 8.0, "off", False)
        self.assertEqual(keep, [(0.0, 8.0)])
        self.assertEqual(stats["cuts"], 0)

    def test_pauses_are_shortened_but_never_cut_words(self):
        w = words_with_gaps()
        keep, stats = compute_keep(w, 8.0, "medium")
        self.assertGreater(stats["removed_seconds"], 2.0)
        for word in w:
            mid = (word.start + word.end) / 2
            self.assertTrue(any(a <= mid <= b for a, b in keep), f"{word.text} was cut")
            self.assertTrue(any(a <= word.start + 1e-9 and word.end <= b + 1e-9 for a, b in keep),
                            f"{word.text} partially cut")

    def test_intensity_orders_removal(self):
        w = words_with_gaps()
        removed = [compute_keep(w, 8.0, i)[1]["removed_seconds"] for i in ("light", "medium", "strong")]
        self.assertLessEqual(removed[0], removed[1])
        self.assertLessEqual(removed[1], removed[2])

    def test_storytelling_pause_after_sentence_end_keeps_more_air(self):
        w = words_with_gaps()
        keep, _ = compute_keep(w, 8.0, "medium")
        # whole gap is 2.0 s; the kept part (outside the cut) must be > bare minimum 0.35 s
        total_kept_between = sum(min(b, 4.4) - max(a, 2.4) for a, b in keep if b > 2.4 and a < 4.4)
        self.assertGreater(total_kept_between, 0.35)

    def test_filler_removal(self):
        keep, stats = compute_keep(words_with_gaps(), 8.0, "off", True)
        self.assertEqual(stats["fillers_removed"], 1)
        self.assertFalse(any(a <= 1.5 <= b for a, b in keep))

    def test_retime_follows_cuts_and_drops_removed_words(self):
        w = words_with_gaps()
        keep, _ = compute_keep(w, 8.0, "medium", True)
        new = retime(w, keep)
        self.assertNotIn("um", [x.text for x in new])
        self.assertEqual([x.text for x in new][:2], ["So", "this"])
        starts = [x.start for x in new]
        self.assertEqual(starts, sorted(starts))
        total = sum(b - a for a, b in keep)
        self.assertLessEqual(new[-1].end, total + 1e-6)
        # first word moves earlier because the lead-in silence was removed
        self.assertLess(new[0].start, 0.3)

    def test_invalid_intensity(self):
        with self.assertRaises(ValueError):
            compute_keep(words_with_gaps(), 8.0, "extreme")


if __name__ == "__main__":
    unittest.main()
