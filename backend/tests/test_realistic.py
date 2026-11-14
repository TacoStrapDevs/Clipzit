"""Regression test on a realistic creator-style transcript: intro chatter, a story, a tangent, a list, a sign-off."""
import unittest

from clipzit.analysis import build_sentences, discover_clips
from tests.helpers import speech

TRANSCRIPT = """Hey guys welcome back to the channel today we are going to talk about money.
Before we get started please like and subscribe to the channel.
So yeah um I wanted to say that it is kind of a thing you know.
When I was twenty three I lost eleven thousand dollars on a single trade.
I thought I was smarter than the market and I never checked my risk.
But then my mentor told me one rule that changed how I invest forever.
Never risk more than one percent of your account on any single idea.
So I followed that rule for two years and finally my account doubled.
That is the lesson nobody tells beginners about money.
Anyway the weather has been nice lately and we went to the lake.
It was really great and the kids liked the water and stuff.
Here's why most people never build wealth even with a good salary.
They spend every raise on a bigger lifestyle and never invest the difference.
Three habits separate the wealthy from everyone else in the long run.
First they automate savings before they ever see the money in the account.
Second they keep their fixed costs low even when income rises quickly.
Third they invest in boring index funds for decades without checking daily.
That is it and it works for almost every income level I have ever seen.
Okay so that is the video thanks for watching and see you in the next one.""".splitlines()


class RealisticTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.sents = build_sentences(speech(TRANSCRIPT, gap=0.5))
        self.clips = discover_clips(self.sents, self.sents[-1].end + 2, 20, 45, 4)

    def test_boilerplate_chatter_and_signoff_never_appear_in_clips(self):
        self.assertTrue(self.clips)
        for c in self.clips:
            t = c["transcript"].lower()
            for bad in ("welcome back", "subscribe", "thanks for watching", "weather has been nice"):
                self.assertNotIn(bad, t)

    def test_the_two_real_pieces_of_content_are_found(self):
        texts = " ".join(c["transcript"] for c in self.clips)
        self.assertIn("Three habits", texts)
        self.assertTrue(any("lost eleven thousand" in c["transcript"] for c in self.clips),
                        "the story should start at its setup, not mid-way")

    def test_story_clip_gets_arc_credit_and_no_negative_note_in_reasons(self):
        story = next(c for c in self.clips if "lost eleven thousand" in c["transcript"])
        self.assertIn("Has a setup, a turn and a payoff", story["reasons"])
        for c in self.clips:
            for r in c["reasons"]:
                self.assertFalse(r.startswith(("Starts mid-thought", "Opens with generic intro")), r)

    def test_clips_start_and_end_on_sentence_boundaries(self):
        for c in self.clips:
            self.assertTrue(c["transcript"][0].isupper())
            self.assertTrue(c["transcript"].rstrip().endswith((".", "?", "!")))


if __name__ == "__main__":
    unittest.main()
