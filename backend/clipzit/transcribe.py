"""Speech-to-text via faster-whisper (optional dependency)."""
from __future__ import annotations

import threading
from typing import Callable, Iterable

from .models import Segment, Word


class TranscriptionUnavailable(Exception):
    """Raised when the transcription engine or its model cannot be used."""


_models: dict = {}
_models_lock = threading.Lock()


def _get_model(cls, name: str, device: str, compute_type: str):
    """Loading a Whisper model takes seconds to minutes; keep one instance per configuration."""
    key = (name, device, compute_type)
    with _models_lock:
        if key not in _models:
            _models[key] = cls(name, device=device, compute_type=compute_type)
        return _models[key]


def transcribe(audio_path: str, model_name: str, device: str, compute_type: str, language: str | None,
               duration: float, on_progress: Callable[[float], None], should_cancel: Callable[[], bool]
               ) -> tuple[list[Segment], str | None]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "faster-whisper is not installed on this server (pip install -r requirements-whisper.txt), "
            "or upload a transcript JSON with the video."
        ) from exc
    try:
        model = _get_model(WhisperModel, model_name, device, compute_type)
        seg_iter, info = model.transcribe(audio_path, word_timestamps=True, vad_filter=True,
                                          language=language or None)
    except Exception as exc:  # model download / load failures
        raise TranscriptionUnavailable(f"could not load or run Whisper model '{model_name}': {exc}") from exc
    segments: list[Segment] = []
    for seg in _iter(seg_iter):
        if should_cancel():
            break
        words = [Word(float(w.start), float(w.end), w.word.strip()) for w in (seg.words or []) if w.word.strip()]
        segments.append(Segment(float(seg.start), float(seg.end), seg.text.strip(), words))
        if duration > 0:
            on_progress(min(1.0, float(seg.end) / duration))
    return segments, getattr(info, "language", None)


def _iter(x: Iterable):
    return iter(x)
