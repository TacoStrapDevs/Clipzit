import shutil
import tempfile
import unittest
from pathlib import Path

from clipzit.uploads import UploadError, UploadManager


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = UploadManager(self.tmp / "u", 1000, (".mp4", ".mkv"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_chunked_upload_reassembles_bytes(self):
        data = bytes(range(256)) * 3
        u = self.m.create("a.mp4", len(data), {"x": 1})
        off = 0
        for i in range(0, len(data), 100):
            off = self.m.append(u["upload_id"], off, data[i:i + 100])
        self.assertEqual(off, len(data))
        dest = self.tmp / "out" / "source.mp4"
        meta = self.m.finish(u["upload_id"], dest, "job1")
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(meta["meta"], {"x": 1})
        self.assertEqual(self.m.done(u["upload_id"]), "job1")
        info = self.m.info(u["upload_id"])  # still answerable after completion (client may re-check)
        self.assertEqual(info["offset"], len(data))

    def test_resume_after_interruption_uses_server_offset(self):
        data = b"z" * 500
        u = self.m.create("a.mp4", 500, {})
        self.m.append(u["upload_id"], 0, data[:200])
        # client "restarts" and asks where to continue
        self.assertEqual(self.m.info(u["upload_id"])["offset"], 200)
        self.m.append(u["upload_id"], 200, data[200:])
        self.assertEqual(self.m.info(u["upload_id"])["offset"], 500)

    def test_wrong_offset_is_rejected_with_current_offset(self):
        u = self.m.create("a.mp4", 500, {})
        self.m.append(u["upload_id"], 0, b"a" * 100)
        with self.assertRaises(UploadError) as cm:
            self.m.append(u["upload_id"], 50, b"b" * 10)
        self.assertEqual((cm.exception.status, cm.exception.offset), (409, 100))
        # a replayed chunk (retry after a lost response) is also refused, never duplicated
        with self.assertRaises(UploadError):
            self.m.append(u["upload_id"], 0, b"a" * 100)
        self.assertEqual(self.m.info(u["upload_id"])["offset"], 100)

    def test_validation(self):
        with self.assertRaises(UploadError):
            self.m.create("a.exe", 10, {})
        with self.assertRaises(UploadError) as cm:
            self.m.create("a.mp4", 5000, {})
        self.assertEqual(cm.exception.status, 413)
        with self.assertRaises(UploadError):
            self.m.create("a.mp4", 0, {})
        u = self.m.create("a.mp4", 10, {})
        with self.assertRaises(UploadError):
            self.m.append(u["upload_id"], 0, b"x" * 11)
        with self.assertRaises(UploadError):
            self.m.finish(u["upload_id"], self.tmp / "d.mp4", "j")  # incomplete
        for bad in ("../x", "nope", "0" * 32):
            with self.assertRaises(UploadError):
                self.m.info(bad)

    def test_discard_and_purge(self):
        u = self.m.create("a.mp4", 10, {})
        self.m.discard(u["upload_id"])
        with self.assertRaises(UploadError):
            self.m.info(u["upload_id"])
        u2 = self.m.create("a.mp4", 10, {})
        self.assertEqual(self.m.purge_stale(max_age_s=-1), 1)
        with self.assertRaises(UploadError):
            self.m.info(u2["upload_id"])


if __name__ == "__main__":
    unittest.main()
