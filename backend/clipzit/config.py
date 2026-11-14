from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 4 * 1024**3
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    api_token: str | None = None
    ffmpeg_timeout: int = 3600
    allowed_extensions: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipzit.sqlite3"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("CLIPZIT_DATA_DIR", "./data")).resolve()
    return Settings(
        data_dir=data_dir,
        max_upload_bytes=int(os.environ.get("CLIPZIT_MAX_UPLOAD_MB", "4096")) * 1024 * 1024,
        whisper_model=os.environ.get("CLIPZIT_WHISPER_MODEL", "base"),
        whisper_device=os.environ.get("CLIPZIT_WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.environ.get("CLIPZIT_WHISPER_COMPUTE", "int8"),
        api_token=os.environ.get("CLIPZIT_API_TOKEN") or None,
        ffmpeg_timeout=int(os.environ.get("CLIPZIT_FFMPEG_TIMEOUT", "3600")),
    )
