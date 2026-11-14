"""API integration tests. Require fastapi + httpx (installed in CI)."""
import importlib.util
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import SAMPLE, make_video, speech

HAVE = importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx") and shutil.which("ffmpeg")


@unittest.skipUnless(HAVE, "fastapi/httpx/ffmpeg not installed")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from clipzit.api import create_app
        from clipzit.config import Settings
        cls.tmp = Path(tempfile.mkdtemp())
        cls.settings = Settings(data_dir=cls.tmp / "data", api_token="secret", max_upload_bytes=50 * 1024 * 1024)
        cls.app = create_app(cls.settings)
        cls.client = TestClient(cls.app)
        cls.h = {"Authorization": "Bearer secret"}
        cls.video = make_video(cls.tmp / "v.mp4", 60)
        cls.transcript = json.dumps({"segments": [s.to_dict() for s in speech(SAMPLE)]})

    @classmethod
    def tearDownClass(cls):
        cls.app.state.pipeline.shutdown()
        cls.app.state.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def upload(self, **form):
        data = {"name": "Demo", "preset": "custom", "min_seconds": "12", "max_seconds": "30", "max_clips": "4"}
        data.update(form)
        with open(self.video, "rb") as f:
            return self.client.post("/api/jobs", headers=self.h, data=data, files={
                "file": ("demo.mp4", f, "video/mp4"),
                "transcript": ("t.json", self.transcript, "application/json")})

    def wait(self, jid):
        for _ in range(300):
            r = self.client.get(f"/api/jobs/{jid}", headers=self.h).json()
            if r["status"] in ("completed", "failed", "cancelled"):
                return r
            time.sleep(0.2)
        self.fail("timeout")

    def test_health_is_public_and_auth_is_enforced(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/jobs", headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_rejects_bad_uploads(self):
        r = self.client.post("/api/jobs", headers=self.h, files={"file": ("a.txt", b"hi", "text/plain")})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/jobs", headers=self.h, files={"file": ("a.mp4", b"not video", "video/mp4")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/api/jobs/" + "0" * 32, headers=self.h).status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/../../etc", headers=self.h).status_code, 404)

    def test_end_to_end_job_clips_srt_render_search(self):
        r = self.upload()
        self.assertEqual(r.status_code, 202, r.text)
        jid = r.json()["id"]
        self.assertEqual(self.wait(jid)["status"], "completed")
        clips = self.client.get(f"/api/jobs/{jid}/clips", headers=self.h).json()
        self.assertGreater(len(clips), 0)
        srt = self.client.get(f"/api/jobs/{jid}/clips/0/srt", headers=self.h)
        self.assertEqual(srt.status_code, 200)
        self.assertIn("-->", srt.text)
        found = self.client.get(f"/api/jobs/{jid}/search", headers=self.h, params={"q": "secret"}).json()
        self.assertEqual(len(found), 1)
        rr = self.client.post(f"/api/jobs/{jid}/clips/0/render", headers=self.h,
                              json={"aspect": "9:16", "captions": True})
        self.assertEqual(rr.status_code, 200, rr.text)
        out = self.tmp / "rendered.mp4"
        out.write_bytes(rr.content)
        from clipzit import media
        info = media.probe(out)
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertTrue(info.has_audio)
        bad = self.client.post(f"/api/jobs/{jid}/clips/0/render", headers=self.h, json={"aspect": "7:3"})
        self.assertEqual(bad.status_code, 400)

    def test_resumable_upload_end_to_end(self):
        data = self.video.read_bytes()
        r = self.client.post("/api/uploads", headers=self.h, json={
            "filename": "demo.mp4", "size": len(data), "name": "Chunked", "preset": "custom",
            "min_seconds": 12, "max_seconds": 30, "max_clips": 3})
        self.assertEqual(r.status_code, 201, r.text)
        uid = r.json()["upload_id"]
        half = len(data) // 2
        put = self.client.put(f"/api/uploads/{uid}", headers=self.h, params={"offset": 0}, content=data[:half])
        self.assertEqual(put.json()["offset"], half)
        # a wrong offset is refused, then the client asks where to resume
        bad = self.client.put(f"/api/uploads/{uid}", headers=self.h, params={"offset": 5}, content=b"x")
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self.client.get(f"/api/uploads/{uid}", headers=self.h).json()["offset"], half)
        early = self.client.post(f"/api/uploads/{uid}/complete", headers=self.h)
        self.assertEqual(early.status_code, 409)
        self.client.put(f"/api/uploads/{uid}", headers=self.h, params={"offset": half}, content=data[half:])
        done = self.client.post(f"/api/uploads/{uid}/complete", headers=self.h)
        self.assertEqual(done.status_code, 202, done.text)
        again = self.client.post(f"/api/uploads/{uid}/complete", headers=self.h)
        self.assertEqual(again.json()["id"], done.json()["id"])  # idempotent

    def test_regenerate_alternatives_zip_and_music(self):
        jid = self.upload().json()["id"]
        self.assertEqual(self.wait(jid)["status"], "completed")
        alts = self.client.get(f"/api/jobs/{jid}/clips/0/alternatives", headers=self.h)
        self.assertEqual(alts.status_code, 200)
        self.assertIsInstance(alts.json(), list)
        z = self.client.post(f"/api/jobs/{jid}/export.zip", headers=self.h,
                             json={"indices": [0], "options": {"platform": "reels"}})
        self.assertEqual(z.status_code, 200, z.text)
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        self.assertIn("clip01.mp4", names)
        self.assertIn("manifest.json", names)
        self.assertEqual(self.client.post(f"/api/jobs/{jid}/export.zip", headers=self.h,
                                          json={"indices": [99]}).status_code, 400)
        bad = self.client.post(f"/api/jobs/{jid}/clips/0/render", headers=self.h, json={"pacing": "wild"})
        self.assertEqual(bad.status_code, 400)
        regen = self.client.post(f"/api/jobs/{jid}/regenerate", headers=self.h,
                                 json={"preset": "custom", "min_seconds": 10, "max_seconds": 20})
        self.assertEqual(regen.status_code, 200)
        self.assertEqual(self.wait(jid)["status"], "completed")
        tone = self.tmp / "tone.wav"
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=2", str(tone)], check=True)
        with open(tone, "rb") as f:
            up = self.client.post("/api/music", headers=self.h, files={"file": ("tone.wav", f, "audio/wav")})
        self.assertEqual(up.status_code, 201, up.text)
        self.assertEqual(len(self.client.get("/api/music", headers=self.h).json()), 1)
        self.assertEqual(self.client.post("/api/music", headers=self.h,
                                          files={"file": ("x.mp3", b"junk", "audio/mpeg")}).status_code, 400)
        r = self.client.post(f"/api/jobs/{jid}/clips/0/render", headers=self.h,
                             json={"music_id": up.json()["id"], "pacing": "light", "caption_preset": "bold"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_retry_conflict_and_delete(self):
        jid = self.upload().json()["id"]
        self.wait(jid)
        self.assertEqual(self.client.post(f"/api/jobs/{jid}/retry", headers=self.h).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/jobs/{jid}", headers=self.h).status_code, 204)
        self.assertEqual(self.client.get(f"/api/jobs/{jid}", headers=self.h).status_code, 404)


if __name__ == "__main__":
    unittest.main()
