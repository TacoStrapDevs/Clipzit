"""SQLite persistence for jobs. Safe for use from API threads and the worker thread."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ACTIVE = ("queued", "uploading", "transcribing", "analyzing", "discovering")
FINAL = ("completed", "failed", "cancelled")

_COLUMNS = ("id", "name", "status", "stage", "progress", "error", "params", "video_path", "duration",
            "language", "created_at", "updated_at")


class JobStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")      # readers never block the worker's progress writes
            self._db.execute("PRAGMA synchronous=NORMAL")
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '', "
                "progress REAL NOT NULL DEFAULT 0, error TEXT, params TEXT NOT NULL DEFAULT '{}', "
                "video_path TEXT, duration REAL, language TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )
            self._db.execute("CREATE TABLE IF NOT EXISTS results (job_id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def create(self, name: str, params: dict, status: str = "uploading") -> str:
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO jobs (id,name,status,params,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (job_id, name, status, json.dumps(params), now, now))
        return job_id

    def update(self, job_id: str, **fields) -> None:
        bad = set(fields) - set(_COLUMNS)
        if bad:
            raise ValueError(f"unknown job fields: {bad}")
        if "params" in fields and not isinstance(fields["params"], str):
            fields["params"] = json.dumps(fields["params"])
        fields["updated_at"] = time.time()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def delete(self, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._db.execute("DELETE FROM results WHERE job_id=?", (job_id,))

    def save_result(self, job_id: str, clips: list[dict]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO results (job_id, body) VALUES (?,?)",
                             (job_id, json.dumps(clips)))

    def clear_result(self, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM results WHERE job_id=?", (job_id,))

    def get_result(self, job_id: str) -> list[dict] | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM results WHERE job_id=?", (job_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def recover_interrupted(self) -> int:
        """Jobs left running by a crash/restart become 'failed' (retryable) instead of hanging forever."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE jobs SET status='failed', error='Interrupted by a server restart. Use retry to resume.', "
                "updated_at=? WHERE status IN ('queued','uploading','transcribing','analyzing','discovering')",
                (time.time(),))
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["params"] = json.loads(d.get("params") or "{}")
        return d
