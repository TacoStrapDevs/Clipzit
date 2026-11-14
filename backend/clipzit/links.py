"""Create an analysis job from a video link (YouTube and other sites supported by yt-dlp).

The backend downloads the video, then runs the normal pipeline. Routes are added by add_link_routes() so the
rest of the API is untouched. fastapi is imported lazily so the download logic can be tested without it.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import shutil
import socket
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import media
from .analysis import PRESETS
from .config import Settings
from .pipeline import QUALITY_MODELS, Pipeline, job_dir
from .store import JobStore

log = logging.getLogger("clipzit.links")

MAX_HEIGHT = 1080


class FetchError(Exception):
    """A problem the user can understand and fix (bad link, private video, too large...)."""


class Stopped(Exception):
    """The user cancelled the job while it was downloading."""


def check_url(url: str) -> str:
    """Accepts only public http(s) links. Blocks localhost / private network addresses (SSRF protection)."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise FetchError("Enter a full link that starts with http:// or https://")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise FetchError("Could not find that website. Check the link.") from None
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not ip.is_global:
            raise FetchError("Links to private or local network addresses are not allowed.")
    return url


def _clean(message: str) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", message).replace("ERROR: ", "").strip()
    return message[:300] or "The video could not be downloaded."


def download(url: str, dest_dir: Path, max_bytes: int, allowed: tuple[str, ...],
             on_progress: Callable[[float], None], cancelled: Callable[[], bool]) -> tuple[Path, str | None]:
    """Downloads the video with yt-dlp into dest_dir/source.<ext>. Returns (file, title)."""
    try:
        import yt_dlp
    except ImportError:
        raise FetchError("yt-dlp is not installed on the backend. Install it with: pip install -U \"yt-dlp[default]\"") from None

    def hook(d: dict) -> None:
        if cancelled():
            raise Stopped()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, (d.get("downloaded_bytes") or 0) / total))

    opts = {
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "format": f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": max_bytes,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 5,
        "restrictfilenames": True,
        "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Stopped:
        raise
    except yt_dlp.utils.DownloadError as exc:
        if cancelled():
            raise Stopped() from None
        raise FetchError(_clean(str(exc))) from None

    files = [p for p in dest_dir.glob("source.*") if p.suffix.lower() in allowed and p.is_file()]
    if not files:
        raise FetchError("No video was downloaded. Live streams and playlists are not supported.")
    path = max(files, key=lambda p: p.stat().st_size)
    if path.stat().st_size > max_bytes:
        raise FetchError("The video is larger than this server allows.")
    title = info.get("title") if isinstance(info, dict) else None
    return path, (str(title)[:120] if title else None)


class LinkFetcher:
    """Runs link downloads in background threads, then hands the job to the analysis pipeline."""

    def __init__(self, settings: Settings, store: JobStore, pipeline: Pipeline, downloader=download):
        self.settings, self.store, self.pipeline = settings, store, pipeline
        self._download = downloader
        self._slots = threading.BoundedSemaphore(1)  # one download at a time; others wait

    def start(self, job_id: str, url: str, keep_name: bool) -> threading.Thread:
        t = threading.Thread(target=self.run, args=(job_id, url, keep_name), daemon=True, name=f"clipzit-fetch-{job_id[:6]}")
        t.start()
        return t

    def run(self, job_id: str, url: str, keep_name: bool = False) -> None:
        d = job_dir(self.settings, job_id)
        cancelled = lambda: self.pipeline._cancelled(job_id)  # noqa: E731
        last = [0.0]

        def progress(frac: float) -> None:
            if frac - last[0] >= 0.01 or frac >= 1.0:
                last[0] = frac
                self.store.update(job_id, stage=f"Downloading video ({int(frac * 100)}%)", progress=min(0.02, frac * 0.02))

        def fail(stage: str, error: str) -> None:
            self.store.update(job_id, status="failed", stage=stage, error=error)
            shutil.rmtree(d, ignore_errors=True)

        try:
            d.mkdir(parents=True, exist_ok=True)
            self.store.update(job_id, stage="Waiting to download", progress=0.0)
            with self._slots:
                if cancelled():
                    raise Stopped()
                self.store.update(job_id, stage="Downloading video", progress=0.0)
                path, title = self._download(url, d, self.settings.max_upload_bytes,
                                             self.settings.allowed_extensions, progress, cancelled)
            if cancelled():
                raise Stopped()
            info = media.probe(path)
            if not info.has_audio:
                raise FetchError("This video has no audio track, so there is no speech to analyse.")
            fields = {"video_path": str(path), "duration": info.duration}
            if title and not keep_name:
                fields["name"] = title
            self.store.update(job_id, **fields)
        except Stopped:
            self.store.update(job_id, status="cancelled", stage="Cancelled", error=None)
            shutil.rmtree(d, ignore_errors=True)
        except FetchError as exc:
            fail("Download failed", str(exc))
        except media.MediaError as exc:
            fail("Download failed", f"The downloaded file is not a readable video: {exc}")
        except Exception as exc:  # never leave a job stuck
            log.exception("link job %s crashed", job_id)
            fail("Download failed", f"{type(exc).__name__}: {exc}")
        else:
            self.pipeline.submit(job_id)


def add_link_routes(app) -> None:
    from fastapi import Body, Depends, Header, HTTPException
    from fastapi.responses import FileResponse

    settings: Settings = app.state.settings
    store: JobStore = app.state.store
    pipeline: Pipeline = app.state.pipeline
    fetcher = LinkFetcher(settings, store, pipeline)

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, settings.api_token):
            raise HTTPException(401, "invalid or missing API token")

    def get_job(job_id: str) -> dict:
        if not job_id.isalnum() or len(job_id) != 32:
            raise HTTPException(404, "job not found")
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/jobs/from-url", status_code=202, dependencies=[Depends(auth)])
    def create_from_url(body: dict = Body(...)):
        try:
            url = check_url(str(body.get("url", "")))
        except FetchError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            preset = str(body.get("preset", "30-45"))
            min_s, max_s = float(body.get("min_seconds", 30)), float(body.get("max_seconds", 60))
            max_clips = int(body.get("max_clips", 8))
            quality = str(body.get("quality") or "")
            language = str(body.get("language") or "")
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid settings: {exc}") from exc
        if not (1 <= max_clips <= 30):
            raise HTTPException(400, "max_clips must be between 1 and 30")
        if preset != "custom" and preset not in PRESETS:
            raise HTTPException(400, "unknown preset")
        if preset == "custom" and not (3 <= min_s < max_s <= 300):
            raise HTTPException(400, "custom range must satisfy 3 <= min < max <= 300 seconds")
        if quality and quality not in QUALITY_MODELS:
            raise HTTPException(400, f"quality must be one of {sorted(QUALITY_MODELS)}")
        params = {"preset": preset, "min_seconds": min_s, "max_seconds": max_s, "max_clips": max_clips,
                  "language": language or None, "quality": quality or None}
        given = str(body.get("name") or "").strip()[:120]
        # "uploading" is an active status, so cancel works and a server restart marks the job failed (retryable).
        job_id = store.create(given or (urlparse(url).hostname or "Link"), params, status="uploading")
        store.update(job_id, stage="Waiting to download", progress=0.0)
        fetcher.start(job_id, url, keep_name=bool(given))
        return {"id": job_id}

    @app.get("/api/jobs/{job_id}/source", dependencies=[Depends(auth)])
    def source(job_id: str):
        job = get_job(job_id)
        raw = job.get("video_path")
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            raise HTTPException(404, "source video is not available")
        return FileResponse(path, filename=path.name)
