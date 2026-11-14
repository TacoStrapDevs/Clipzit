"""Speech-energy profile used for the 'delivery' signal (louder, more dynamic delivery usually reads as more engaging).

Optional: needs numpy. Without it the delivery signal is simply neutral and the job says so.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .media import MediaError


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class EnergyProfile:
    hop: float = 0.25
    db: list[float] = field(default_factory=list)

    @property
    def median(self) -> float:
        s = sorted(self.db)
        return s[len(s) // 2] if s else -60.0

    def window(self, start: float, end: float) -> list[float]:
        a = max(0, int(start / self.hop))
        b = min(len(self.db), int(end / self.hop) + 1)
        return self.db[a:b]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"hop": self.hop, "db": [round(x, 1) for x in self.db]}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "EnergyProfile | None":
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls(float(d["hop"]), [float(x) for x in d["db"]])
        except (ValueError, KeyError, TypeError):
            return None


def energy_profile(video: str | Path, timeout: int = 3600, hop: float = 0.25, rate: int = 8000) -> EnergyProfile:
    """Streams the audio through FFmpeg and returns dBFS per `hop` seconds (memory use stays small)."""
    import numpy as np  # type: ignore
    per = int(hop * rate)
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise MediaError("ffmpeg is not installed or not on PATH") from exc
    out: list[float] = []
    leftover = b""
    block = per * 2 * 400
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(block)
            if not chunk:
                break
            buf = leftover + chunk
            usable = (len(buf) // (per * 2)) * per * 2
            leftover = buf[usable:]
            if usable:
                x = np.frombuffer(buf[:usable], dtype=np.int16).astype(np.float32) / 32768.0
                rms = np.sqrt((x.reshape(-1, per) ** 2).mean(axis=1))
                out.extend((20 * np.log10(np.maximum(rms, 1e-5))).tolist())
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise MediaError("audio analysis timed out") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
    err = proc.stderr.read().decode(errors="replace")[-300:] if proc.stderr else ""
    for pipe in (proc.stdout, proc.stderr):
        if pipe:
            pipe.close()
    if proc.returncode != 0:
        raise MediaError(f"audio analysis failed: {err}")
    if not out:
        raise MediaError("audio analysis produced no data")
    return EnergyProfile(hop, [float(v) for v in out])


def delivery_score(profile: EnergyProfile | None, start: float, end: float) -> tuple[int, float | None]:
    """0-100 delivery score (neutral 60 when unknown) and the loudness relative to the whole video in dB."""
    if profile is None or not profile.db:
        return 60, None
    seg = profile.window(start, end)
    if len(seg) < 4:
        return 60, None
    mean = sum(seg) / len(seg)
    rel = mean - profile.median
    s = sorted(seg)
    rng = s[int(0.9 * (len(s) - 1))] - s[int(0.1 * (len(s) - 1))]
    score = 55 + rel * 4 + min(rng, 24) * 1.2
    return int(round(max(0.0, min(100.0, score)))), rel




def silences_from_energy(profile: EnergyProfile, threshold_db: float = -38.0, min_dur: float = 0.4,
                         total: float | None = None) -> list[tuple[float, float]]:
    """Silent intervals derived from the energy profile, so the audio only has to be decoded once."""
    out: list[tuple[float, float]] = []
    start: int | None = None
    for i, v in enumerate(profile.db):
        if v < threshold_db:
            if start is None:
                start = i
        elif start is not None:
            if (i - start) * profile.hop >= min_dur:
                out.append((start * profile.hop, i * profile.hop))
            start = None
    if start is not None:
        end = total if total else len(profile.db) * profile.hop
        if end - start * profile.hop >= min_dur:
            out.append((start * profile.hop, end))
    return out
