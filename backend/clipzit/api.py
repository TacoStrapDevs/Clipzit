"""FastAPI application factory. See docs/API.md for the endpoint reference."""
from __future__ import annotations

import hmac
import json
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import __version__, media, vision
from .analysis import PRESETS, build_sentences, search_transcript, suggest_alternatives
from .captions import PRESETS as CAPTION_PRESETS
from .audio import EnergyProfile
from .captions import chunk_words, clip_words, to_srt
from .config import Settings, load_settings
from .models import segments_from_dicts
from .pipeline import QUALITY_MODELS, Pipeline, job_dir, load_transcript
from .render_service import MUSIC_EXT, PLATFORMS, RenderOptions, build_zip, purge_renders, render_cached
from .store import ACTIVE, JobStore
from .uploads import UploadError, UploadManager

MAX_MUSIC_BYTES = 60 * 1024 * 1024
MAX_CHUNK_BYTES = 32 * 1024 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "music").mkdir(parents=True, exist_ok=True)
    store = JobStore(settings.db_path)
    store.recover_interrupted()
    pipeline = Pipeline(settings, store)
    uploads = UploadManager(settings.data_dir / "uploads", settings.max_upload_bytes, settings.allowed_extensions)
    uploads.purge_stale()
    purge_renders(settings)
    render_slots = threading.BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        pipeline.shutdown()
        store.close()

    app = FastAPI(title="Clipzit backend", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.pipeline = settings, store, pipeline

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

    def public(job: dict) -> dict:
        return {k: job[k] for k in ("id", "name", "status", "stage", "progress", "error", "duration",
                                    "language", "created_at", "updated_at")} | {"params": job["params"]}

    def parse_params(preset: str, min_seconds: float, max_seconds: float, max_clips: int, language: str,
                     quality: str) -> dict:
        if not (1 <= max_clips <= 30):
            raise HTTPException(400, "max_clips must be between 1 and 30")
        if preset != "custom" and preset not in PRESETS:
            raise HTTPException(400, "unknown preset")
        if preset == "custom" and not (3 <= min_seconds < max_seconds <= 300):
            raise HTTPException(400, "custom range must satisfy 3 <= min < max <= 300 seconds")
        if quality and quality not in QUALITY_MODELS:
            raise HTTPException(400, f"quality must be one of {sorted(QUALITY_MODELS)}")
        return {"preset": preset, "min_seconds": min_seconds, "max_seconds": max_seconds, "max_clips": max_clips,
                "language": language or None, "quality": quality or None}

    def get_clip(job_id: str, index: int) -> tuple[dict, dict]:
        job = get_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "job is not completed")
        for c in store.get_result(job_id) or []:
            if c["index"] == index:
                return job, c
        raise HTTPException(404, "clip not found")

    def discard_job(job_id: str) -> None:
        shutil.rmtree(job_dir(settings, job_id), ignore_errors=True)
        store.delete(job_id)

    # ---------------------------------------------------------------- health

    @app.get("/api/health")
    def health():
        import importlib.util
        return {
            "status": "ok",
            "version": __version__,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
            "transcription_available": importlib.util.find_spec("faster_whisper") is not None,
            "face_tracking_available": vision.available(),
            "whisper_model": settings.whisper_model,
            "quality_models": QUALITY_MODELS,
            "caption_presets": sorted(CAPTION_PRESETS),
            "platforms": sorted(PLATFORMS),
            "auth_required": bool(settings.api_token),
        }

    # ---------------------------------------------------------------- one-shot upload (small files, tests)

    @app.post("/api/jobs", status_code=202, dependencies=[Depends(auth)])
    def create_job(file: UploadFile = File(...), transcript: UploadFile | None = File(None),
                   name: str = Form(""), preset: str = Form("30-45"), min_seconds: float = Form(30.0),
                   max_seconds: float = Form(60.0), max_clips: int = Form(8), language: str = Form(""),
                   quality: str = Form("")):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in settings.allowed_extensions:
            raise HTTPException(400, f"unsupported file type '{ext}'. Allowed: {', '.join(settings.allowed_extensions)}")
        params = parse_params(preset, min_seconds, max_seconds, max_clips, language, quality)
        job_id = store.create(name.strip()[:120] or (file.filename or "Untitled"), params)
        d = job_dir(settings, job_id)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"source{ext}"
        try:
            written = 0
            with open(dest, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > settings.max_upload_bytes:
                        raise HTTPException(413, "file exceeds the configured upload limit")
                    out.write(chunk)
            if written == 0:
                raise HTTPException(400, "uploaded file is empty")
            info = media.probe(dest)
            if not info.has_audio:
                raise HTTPException(400, "video has no audio track, so there is no speech to analyse")
            if transcript is not None and transcript.filename:
                try:
                    body = json.loads(transcript.file.read().decode("utf-8"))
                    segs = segments_from_dicts(body["segments"] if isinstance(body, dict) else body)
                except (ValueError, KeyError, UnicodeDecodeError) as exc:
                    raise HTTPException(400, f"invalid transcript JSON: {exc}") from exc
                (d / "transcript.json").write_text(
                    json.dumps({"language": language or None, "segments": [s.to_dict() for s in segs]}),
                    encoding="utf-8")
        except media.MediaError as exc:
            discard_job(job_id)
            raise HTTPException(400, f"not a readable video: {exc}") from exc
        except BaseException:
            discard_job(job_id)
            raise
        store.update(job_id, video_path=str(dest), duration=info.duration)
        pipeline.submit(job_id)
        return {"id": job_id}

    # ---------------------------------------------------------------- resumable upload

    @app.post("/api/uploads", status_code=201, dependencies=[Depends(auth)])
    def start_upload(body: dict = Body(...)):
        try:
            params = parse_params(str(body.get("preset", "30-45")), float(body.get("min_seconds", 30)),
                                  float(body.get("max_seconds", 60)), int(body.get("max_clips", 8)),
                                  str(body.get("language") or ""), str(body.get("quality") or ""))
            return uploads.create(str(body.get("filename", "")), int(body.get("size", 0)),
                                  {"name": str(body.get("name", ""))[:120], "params": params})
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid upload request: {exc}") from exc
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/api/uploads/{upload_id}", dependencies=[Depends(auth)])
    def upload_status(upload_id: str):
        try:
            info = uploads.info(upload_id)
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {"upload_id": upload_id, "offset": info["offset"], "size": info["size"],
                "job_id": uploads.done(upload_id)}

    @app.put("/api/uploads/{upload_id}", dependencies=[Depends(auth)])
    async def upload_chunk(upload_id: str, request: Request, offset: int = Query(..., ge=0)):
        parts: list[bytes] = []
        total = 0
        async for part in request.stream():
            total += len(part)
            if total > MAX_CHUNK_BYTES:
                raise HTTPException(413, "chunk too large")
            parts.append(part)
        data = b"".join(parts)
        try:
            new_offset = await run_in_threadpool(uploads.append, upload_id, offset, data)
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {"offset": new_offset}

    @app.post("/api/uploads/{upload_id}/complete", status_code=202, dependencies=[Depends(auth)])
    def complete_upload(upload_id: str):
        try:
            existing = uploads.done(upload_id)
            if existing:
                return {"id": existing}
            meta = uploads.info(upload_id)
            job_id = store.create(meta["meta"]["name"] or meta["filename"], meta["meta"]["params"])
            d = job_dir(settings, job_id)
            dest = d / f"source{meta['ext']}"
            try:
                uploads.finish(upload_id, dest, job_id)
                info = media.probe(dest)
                if not info.has_audio:
                    raise HTTPException(400, "video has no audio track, so there is no speech to analyse")
            except media.MediaError as exc:
                discard_job(job_id)
                raise HTTPException(400, f"not a readable video: {exc}") from exc
            except BaseException:
                discard_job(job_id)
                raise
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        store.update(job_id, video_path=str(dest), duration=info.duration)
        pipeline.submit(job_id)
        return {"id": job_id}

    @app.delete("/api/uploads/{upload_id}", status_code=204, dependencies=[Depends(auth)])
    def cancel_upload(upload_id: str):
        try:
            uploads.discard(upload_id)
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    # ---------------------------------------------------------------- jobs

    @app.get("/api/jobs", dependencies=[Depends(auth)])
    def list_jobs():
        return [public(j) for j in store.list()]

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
    def job_status(job_id: str):
        return public(get_job(job_id))

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(auth)])
    def cancel(job_id: str):
        job = get_job(job_id)
        if job["status"] not in ACTIVE:
            raise HTTPException(409, f"job is already {job['status']}")
        pipeline.cancel(job_id)
        return {"status": "cancelling"}

    @app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(auth)])
    def retry(job_id: str):
        job = get_job(job_id)
        if job["status"] not in ("failed", "cancelled"):
            raise HTTPException(409, "only failed or cancelled jobs can be retried")
        store.clear_result(job_id)
        pipeline.submit(job_id)
        return {"status": "queued"}

    @app.post("/api/jobs/{job_id}/regenerate", dependencies=[Depends(auth)])
    def regenerate(job_id: str, body: dict = Body(default={})):
        """Re-run clip discovery with new settings. The saved transcript is reused (no re-transcription)."""
        job = get_job(job_id)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "job is still running")
        old = job["params"]
        try:
            params = parse_params(str(body.get("preset", old.get("preset", "30-45"))),
                                  float(body.get("min_seconds", old.get("min_seconds", 30))),
                                  float(body.get("max_seconds", old.get("max_seconds", 60))),
                                  int(body.get("max_clips", old.get("max_clips", 8))),
                                  str(body.get("language") or old.get("language") or ""),
                                  str(body.get("quality") or old.get("quality") or ""))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid settings: {exc}") from exc
        store.update(job_id, params=params)
        store.clear_result(job_id)
        pipeline.submit(job_id)
        return {"status": "queued"}

    @app.delete("/api/jobs/{job_id}", status_code=204, dependencies=[Depends(auth)])
    def delete_job(job_id: str):
        job = get_job(job_id)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "cancel the job before deleting it")
        discard_job(job_id)

    @app.get("/api/jobs/{job_id}/clips", dependencies=[Depends(auth)])
    def clips(job_id: str):
        job = get_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, f"job is {job['status']}; clips are only available when completed")
        return store.get_result(job_id) or []

    @app.get("/api/jobs/{job_id}/clips/{index}/srt", dependencies=[Depends(auth)], response_class=PlainTextResponse)
    def clip_srt(job_id: str, index: int):
        _job, clip = get_clip(job_id, index)
        segs = load_transcript(settings, job_id)
        if not segs:
            raise HTTPException(409, "transcript is not available")
        words = clip_words(build_sentences(segs), clip["start"], clip["end"])
        return PlainTextResponse(to_srt(chunk_words(words)), media_type="application/x-subrip")

    @app.get("/api/jobs/{job_id}/clips/{index}/alternatives", dependencies=[Depends(auth)])
    def alternatives(job_id: str, index: int):
        job, clip = get_clip(job_id, index)
        segs = load_transcript(settings, job_id)
        if not segs:
            raise HTTPException(409, "transcript is not available")
        p = job["params"]
        lo, hi = PRESETS.get(p.get("preset"), (float(p.get("min_seconds", 30)), float(p.get("max_seconds", 60))))
        energy = EnergyProfile.load(job_dir(settings, job_id) / "energy.json")
        return suggest_alternatives(build_sentences(segs), clip["start"], clip["end"], job["duration"], lo, hi,
                                    energy=energy)

    @app.get("/api/jobs/{job_id}/search", dependencies=[Depends(auth)])
    def search(job_id: str, q: str = Query(..., min_length=1, max_length=200)):
        get_job(job_id)
        segs = load_transcript(settings, job_id)
        if not segs:
            raise HTTPException(409, "transcript is not available")
        return search_transcript(build_sentences(segs), q)

    # ---------------------------------------------------------------- rendering

    def parse_options(raw: dict) -> RenderOptions:
        try:
            return RenderOptions.from_dict(raw)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/clips/{index}/render", dependencies=[Depends(auth)])
    def render(job_id: str, index: int, body: dict = Body(default={})):
        job, clip = get_clip(job_id, index)
        opts = parse_options(body)
        with render_slots:
            try:
                out, summary = render_cached(settings, job, clip, opts)
            except (media.MediaError, ValueError) as exc:
                raise HTTPException(500, f"render failed: {exc}") from exc
        safe = "".join(ch if ch.isalnum() else "_" for ch in job["name"])[:40] or "clip"
        return FileResponse(out, media_type="video/mp4", filename=f"{safe}_clip{index + 1}.mp4",
                            headers={"X-Clipzit-Warnings": json.dumps(summary["warnings"]).encode("ascii", "replace")
                                     .decode("ascii")})

    @app.post("/api/jobs/{job_id}/export.zip", dependencies=[Depends(auth)])
    def export_zip(job_id: str, body: dict = Body(default={})):
        job = get_job(job_id)
        if job["status"] != "completed":
            raise HTTPException(409, "job is not completed")
        all_clips = store.get_result(job_id) or []
        indices = body.get("indices")
        chosen = [c for c in all_clips if indices is None or c["index"] in indices]
        if not chosen:
            raise HTTPException(400, "no clips selected")
        opts = parse_options(body.get("options") or {})
        ranges = body.get("ranges") or {}
        if not isinstance(ranges, dict):
            raise HTTPException(400, "ranges must be an object of {clip_index: [start, end]}")
        adjusted = []
        for c in chosen:
            r = ranges.get(str(c["index"]))
            if r is not None:
                try:
                    start, end = float(r[0]), float(r[1])
                except (TypeError, ValueError, IndexError) as exc:
                    raise HTTPException(400, "each range must be [start, end] in seconds") from exc
                if not (0 <= start < end <= (job["duration"] or end) + 0.5):
                    raise HTTPException(400, f"range for clip {c['index']} is outside the video")
                c = dict(c, start=start, end=end)
            adjusted.append(c)
        chosen = adjusted
        zip_path = Path(tempfile.mkdtemp(dir=job_dir(settings, job_id))) / "clips.zip"
        with render_slots:
            try:
                build_zip(settings, job, chosen, opts, zip_path)
            except (media.MediaError, ValueError) as exc:
                shutil.rmtree(zip_path.parent, ignore_errors=True)
                raise HTTPException(500, f"export failed: {exc}") from exc
        return FileResponse(zip_path, media_type="application/zip", filename="Clipzit-clips.zip",
                            background=BackgroundTask(shutil.rmtree, zip_path.parent, True))  # delete after sending

    # ---------------------------------------------------------------- music library

    @app.post("/api/music", status_code=201, dependencies=[Depends(auth)])
    def upload_music(file: UploadFile = File(...)):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in MUSIC_EXT:
            raise HTTPException(400, f"unsupported audio type '{ext}'. Allowed: {', '.join(MUSIC_EXT)}")
        mid = uuid.uuid4().hex
        dest = settings.data_dir / "music" / f"{mid}{ext}"
        try:
            written = 0
            with open(dest, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_MUSIC_BYTES:
                        raise HTTPException(413, "audio file too large (max 60 MB)")
                    out.write(chunk)
            info = media.probe(dest)
            if not info.has_audio:
                raise HTTPException(400, "file has no audio")
        except media.MediaError as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"not a readable audio file: {exc}") from exc
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        (settings.data_dir / "music" / f"{mid}.name").write_text((file.filename or "track")[:120])
        return {"id": mid, "name": file.filename, "duration": info.duration}

    @app.get("/api/music", dependencies=[Depends(auth)])
    def list_music():
        out = []
        for n in sorted((settings.data_dir / "music").glob("*.name")):
            out.append({"id": n.stem, "name": n.read_text()})
        return out

    @app.delete("/api/music/{music_id}", status_code=204, dependencies=[Depends(auth)])
    def delete_music(music_id: str):
        if not music_id.isalnum() or len(music_id) != 32:
            raise HTTPException(404, "not found")
        for f in (settings.data_dir / "music").glob(f"{music_id}.*"):
            f.unlink(missing_ok=True)

    return app
