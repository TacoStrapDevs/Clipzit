import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from clipzit.config import Settings
from clipzit.pipeline import Pipeline, job_dir
from clipzit.store import JobStore
from clipzit.transcribe import TranscriptionUnavailable
from tests.helpers import SAMPLE, make_video, speech


def wait_final(store, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = store.get(job_id)
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("job never reached a final state")


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = Settings(data_dir=self.tmp / "data")
        self.settings.jobs_dir.mkdir(parents=True)
        self.store = JobStore(self.settings.db_path)
        self.pipe = Pipeline(self.settings, self.store)

    def tearDown(self):
        self.pipe.shutdown()
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_job(self, video: Path, with_transcript=True, **params):
        p = {"preset": "custom", "min_seconds": 12, "max_seconds": 30, "max_clips": 4}
        p.update(params)
        jid = self.store.create("Test", p)
        d = job_dir(self.settings, jid)
        d.mkdir(parents=True)
        dest = d / "source.mp4"
        shutil.copy(video, dest)
        if with_transcript:
            segs = speech(SAMPLE)
            (d / "transcript.json").write_text(json.dumps({"segments": [s.to_dict() for s in segs]}))
        self.store.update(jid, video_path=str(dest))
        return jid

    def test_full_job_completes_and_persists_results(self):
        video = make_video(self.tmp / "v.mp4", 60)
        jid = self.new_job(video)
        self.pipe.submit(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "completed", job["error"])
        clips = self.store.get_result(jid)
        self.assertGreater(len(clips), 0)
        for c in clips:
            self.assertLessEqual(c["end"], 60.5)
        # Persistence across "restarts": a new store on the same database sees the same data.
        self.store.close()
        again = JobStore(self.settings.db_path)
        self.assertEqual(again.get(jid)["status"], "completed")
        self.assertEqual(len(again.get_result(jid)), len(clips))
        again.close()
        self.store = JobStore(self.settings.db_path)

    def test_corrupt_video_fails_with_clear_error(self):
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(b"garbage" * 1000)
        jid = self.new_job(bad)
        self.pipe.submit(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(job["error"])

    def test_missing_transcription_engine_is_reported_not_faked(self):
        try:
            import faster_whisper  # noqa: F401
            self.skipTest("faster-whisper installed")
        except ImportError:
            pass
        video = make_video(self.tmp / "v.mp4", 30)
        jid = self.new_job(video, with_transcript=False)
        self.pipe.submit(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "failed")
        self.assertIn("faster-whisper", job["error"])
        self.assertIsNone(self.store.get_result(jid))

    def test_failed_job_can_be_retried_without_retranscribing(self):
        video = make_video(self.tmp / "v.mp4", 60)
        jid = self.new_job(video, with_transcript=False)
        self.pipe.submit(jid)
        self.assertEqual(wait_final(self.store, jid)["status"], "failed")
        d = job_dir(self.settings, jid)
        (d / "transcript.json").write_text(
            json.dumps({"segments": [s.to_dict() for s in speech(SAMPLE)]}))
        self.pipe.submit(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "completed", job["error"])

    def test_interrupted_jobs_are_recovered_as_failed(self):
        jid = self.store.create("x", {}, status="transcribing")
        self.assertEqual(self.store.recover_interrupted(), 1)
        job = self.store.get(jid)
        self.assertEqual(job["status"], "failed")
        self.assertIn("restart", job["error"])

    def test_cancel_before_start(self):
        video = make_video(self.tmp / "v.mp4", 30)
        jid = self.new_job(video)
        self.pipe.cancel(jid)
        # submit() clears stale cancel flags, so cancel after queueing but before the worker runs:
        self.pipe._pool.submit(lambda: time.sleep(0.5))  # occupy the single worker
        self.pipe.submit(jid)
        self.pipe.cancel(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "cancelled")

    def test_no_speech_transcript_fails_cleanly(self):
        video = make_video(self.tmp / "v.mp4", 20)
        jid = self.new_job(video, with_transcript=False)
        d = job_dir(self.settings, jid)
        (d / "transcript.json").write_text(json.dumps({"segments": []}))
        self.pipe.submit(jid)
        job = wait_final(self.store, jid)
        self.assertEqual(job["status"], "failed")
        self.assertIn("no speech", job["error"])


if __name__ == "__main__":
    unittest.main()
