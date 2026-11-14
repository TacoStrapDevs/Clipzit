"""Resumable chunked uploads (survive dropped connections and app restarts)."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

_ID = re.compile(r"^[0-9a-f]{32}$")


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400, offset: int | None = None):
        super().__init__(message)
        self.status = status
        self.offset = offset


class UploadManager:
    def __init__(self, root: Path, max_bytes: int, allowed_ext: tuple[str, ...]):
        self.root = root
        self.max_bytes = max_bytes
        self.allowed_ext = allowed_ext
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, upload_id: str) -> Path:
        if not _ID.match(upload_id):
            raise UploadError("upload not found", 404)
        d = self.root / upload_id
        if not d.is_dir():
            raise UploadError("upload not found", 404)
        return d

    def create(self, filename: str, size: int, meta: dict) -> dict:
        ext = Path(filename or "").suffix.lower()
        if ext not in self.allowed_ext:
            raise UploadError(f"unsupported file type '{ext}'. Allowed: {', '.join(self.allowed_ext)}")
        if size <= 0:
            raise UploadError("file size must be positive")
        if size > self.max_bytes:
            raise UploadError("file exceeds the configured upload limit", 413)
        uid = uuid.uuid4().hex
        d = self.root / uid
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({"filename": filename, "ext": ext, "size": size, "meta": meta,
                                                 "created": time.time()}))
        (d / "data.part").write_bytes(b"")
        return {"upload_id": uid, "offset": 0, "size": size}

    def info(self, upload_id: str) -> dict:
        d = self._dir(upload_id)
        m = json.loads((d / "meta.json").read_text())
        part = d / "data.part"
        m["offset"] = part.stat().st_size if part.exists() else m["size"]  # moved away once completed
        return m

    def append(self, upload_id: str, offset: int, chunk: bytes) -> int:
        d = self._dir(upload_id)
        m = json.loads((d / "meta.json").read_text())
        part = d / "data.part"
        current = part.stat().st_size
        if offset != current:
            raise UploadError(f"offset mismatch: server has {current} bytes", 409, current)
        if current + len(chunk) > m["size"]:
            raise UploadError("chunk exceeds the declared file size", 400, current)
        with open(part, "ab") as f:
            f.write(chunk)
        return current + len(chunk)

    def done(self, upload_id: str) -> str | None:
        """Job id if this upload was already completed (makes /complete idempotent)."""
        d = self._dir(upload_id)
        f = d / "done.json"
        return json.loads(f.read_text())["job_id"] if f.exists() else None

    def finish(self, upload_id: str, dest: Path, job_id: str) -> dict:
        d = self._dir(upload_id)
        m = json.loads((d / "meta.json").read_text())
        part = d / "data.part"
        if part.stat().st_size != m["size"]:
            raise UploadError(f"upload incomplete: {part.stat().st_size} of {m['size']} bytes", 409,
                              part.stat().st_size)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(part), str(dest))
        (d / "done.json").write_text(json.dumps({"job_id": job_id}))
        return m

    def discard(self, upload_id: str) -> None:
        shutil.rmtree(self._dir(upload_id), ignore_errors=True)

    def purge_stale(self, max_age_s: float = 3 * 24 * 3600) -> int:
        n = 0
        for d in self.root.iterdir():
            try:
                if d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
            except OSError:
                pass
        return n
