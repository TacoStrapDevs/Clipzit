"""Background analysis pipeline: transcribe -> segment -> discover clips."""
from __future__ import annotations

import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio, media
from .analysis import PRESETS, build_sentences, discover_clips
from .audio import EnergyProfile
from .config import Settings
from .models import Segment, segments_from_dicts
from .store import JobStore
from .transcribe import TranscriptionUnavailable, transcribe

log = logging.getLogger("clipzit.pipeline")

QUALITY_MODELS = {"performance": "tiny", "balanced": "base", "quality": "small"}


class Cancelled(Exception):
    pass


def job_dir(settings: Settings, job_id: str) -> Path:
    return settings.jobs_dir / job_id


def load_transcript(settings: Settings, job_id: str) -> list[Segment] | None:
    p = job_dir(settings, job_id) / "transcript.json"
    if not p.exists():
        return None
    return segments_from_dicts(json.loads(p.read_text(encoding="utf-8"))["segments"])


class Pipeline:
    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipzit-worker")
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:
            self._cancel.discard(job_id)
        self.store.update(job_id, status="queued", stage="Waiting in queue", progress=0.0, error=None)
        self._pool.submit(self._run_safe, job_id)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._cancel.add(job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    def _check(self, job_id: str) -> None:
        if self._cancelled(job_id):
            raise Cancelled()

    def _run_safe(self, job_id: str) -> None:
        try:
            self._run(job_id)
        except Cancelled:
            self.store.update(job_id, status="cancelled", stage="Cancelled", error=None)
        except TranscriptionUnavailable as exc:
            self.store.update(job_id, status="failed", stage="Transcription unavailable", error=str(exc))
        except media.MediaError as exc:
            self.store.update(job_id, status="failed", stage="Media error", error=str(exc))
        except Exception as exc:  # never leave a job stuck
            log.exception("job %s crashed", job_id)
            self.store.update(job_id, status="failed", stage="Unexpected error", error=f"{type(exc).__name__}: {exc}")

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        params = job["params"]
        video = Path(job["video_path"])
        if not video.exists():
            raise media.MediaError("uploaded video file is missing on the server")
        timeout = self.settings.ffmpeg_timeout
        d = job_dir(self.settings, job_id)

        info = media.probe(video)
        self.store.update(job_id, duration=info.duration)
        self._check(job_id)

        segments = load_transcript(self.settings, job_id)  # cached from a previous attempt or user-provided
        language = job.get("language")
        if segments is None:
            self.store.update(job_id, status="transcribing", stage="Extracting audio", progress=0.02)
            wav = d / "audio.wav"
            media.extract_audio(video, wav, timeout)
            self._check(job_id)
            self.store.update(job_id, stage="Transcribing speech", progress=0.05)

            last = [0.0]

            def on_progress(frac: float) -> None:
                if frac - last[0] >= 0.01 or frac >= 1.0:  # avoid a database commit per segment
                    last[0] = frac
                    self.store.update(job_id, progress=0.05 + 0.65 * frac)

            model = QUALITY_MODELS.get(params.get("quality") or "", self.settings.whisper_model)
            segments, language = transcribe(str(wav), model, self.settings.whisper_device,
                                            self.settings.whisper_compute_type, params.get("language"),
                                            info.duration, on_progress, lambda: self._cancelled(job_id))
            self._check(job_id)
            (d / "transcript.json").write_text(
                json.dumps({"language": language, "segments": [s.to_dict() for s in segments]}), encoding="utf-8")
            wav.unlink(missing_ok=True)
            self.store.update(job_id, language=language)
        if not segments or not any(s.text.strip() for s in segments):
            raise media.MediaError("no speech was detected in this video, so clips cannot be discovered")

        self.store.update(job_id, status="analyzing", stage="Analysing audio and sentences", progress=0.72)
        energy = EnergyProfile.load(d / "energy.json")
        note = ""
        if energy is None and info.has_audio:
            if audio.available():
                try:
                    energy = audio.energy_profile(video, timeout)
                    energy.save(d / "energy.json")
                except media.MediaError as exc:
                    note = f" (delivery signal unavailable: {exc})"
            else:
                note = " (install numpy to enable the delivery signal)"
        if energy is not None:
            silences = audio.silences_from_energy(energy, total=info.duration)  # no second audio decode
        else:
            silences = media.detect_silences(video, timeout, total=info.duration) if info.has_audio else []
        sentences = build_sentences(segments)
        self._check(job_id)

        self.store.update(job_id, status="discovering", stage="Scoring candidate clips", progress=0.85)
        lo, hi = float(params.get("min_seconds", 30)), float(params.get("max_seconds", 60))
        if params.get("preset") in PRESETS:
            lo, hi = PRESETS[params["preset"]]
        clips = discover_clips(sentences, info.duration, lo, hi, int(params.get("max_clips", 8)), silences, energy=energy)
        self._check(job_id)
        self.store.save_result(job_id, clips)
        self.store.update(job_id, status="completed", stage=(f"Found {len(clips)} clips{note}" if not (clips and clips[0].get("low_confidence"))
                                else f"Only low-confidence clips found ({len(clips)}){note}"), progress=1.0, error=None)

    def remove_files(self, job_id: str) -> None:
        shutil.rmtree(job_dir(self.settings, job_id), ignore_errors=True)
