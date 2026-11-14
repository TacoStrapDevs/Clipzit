"""Optional face-aware framing.

Faces are detected on frames sampled once per second (OpenCV Haar cascade, optional dependency). The crop window
follows the largest face with a dead-band and hold time so it never jitters. This follows the dominant on-screen
face; it is not audio-based active-speaker detection.
"""
from __future__ import annotations

import subprocess
from typing import Callable

from .media import MediaError, MediaInfo

Detector = Callable[["object"], "list[tuple[float, float]]"]  # frame -> [(cx 0..1, area 0..1)]


def available() -> bool:
    """True only if OpenCV, numpy and the bundled Haar face model are all usable."""
    try:
        import os

        import cv2  # type: ignore
        import numpy  # noqa: F401
    except ImportError:
        return False
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return False
    return os.path.exists(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def haar_detector() -> Detector:
    import cv2  # type: ignore
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        raise MediaError("OpenCV face model could not be loaded")

    def detect(frame):
        h, w = frame.shape[:2]
        faces = cascade.detectMultiScale(frame, scaleFactor=1.15, minNeighbors=5, minSize=(max(24, w // 20),) * 2)
        return [((x + fw / 2) / w, (fw * fh) / (w * h)) for (x, y, fw, fh) in faces]

    return detect


def sample_positions(video: str, start: float, end: float, info: MediaInfo, detector: Detector | None = None,
                     fps: float = 1.0, timeout: int = 600) -> list[tuple[float, float | None]]:
    """[(t_relative_to_clip, face_center_x or None)] sampled at `fps`."""
    import numpy as np  # type: ignore
    det = detector or haar_detector()
    w = 480
    h = max(2, round(w * info.height / max(info.width, 1) / 2) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video),
           "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("face sampling timed out") from exc
    if proc.returncode != 0:
        raise MediaError("face sampling failed: " + proc.stderr.decode(errors="replace")[-300:])
    size = w * h
    n = len(proc.stdout) // size
    out: list[tuple[float, float | None]] = []
    for k in range(n):
        frame = np.frombuffer(proc.stdout, dtype=np.uint8, count=size, offset=k * size).reshape(h, w)
        faces = det(frame)
        best = max(faces, key=lambda f: f[1])[0] if faces else None
        out.append((k / fps, best))
    return out


def build_path(samples: list[tuple[float, float | None]], duration: float, deadband: float = 0.07,
               hold: int = 2, ease: float = 0.5, default: float = 0.5) -> list[tuple[float, float]]:
    """Stable keypoints [(t, x)] from noisy samples: median-smoothed, dead-band, hold, eased moves."""
    if not samples:
        return [(0.0, default), (max(duration, 0.1), default)]
    xs: list[float] = []
    last = None
    for _t, x in samples:
        if x is not None:
            last = x
        xs.append(last if last is not None else -1.0)
    first_known = next((v for v in xs if v >= 0), default)
    xs = [first_known if v < 0 else v for v in xs]
    sm = [sorted(xs[max(0, i - 1):i + 2])[len(xs[max(0, i - 1):i + 2]) // 2] for i in range(len(xs))]
    cur = sm[0]
    pts: list[tuple[float, float]] = [(0.0, cur)]
    streak, cand = 0, cur
    for i in range(1, len(sm)):
        t = samples[i][0]
        if abs(sm[i] - cur) > deadband:
            streak = streak + 1 if abs(sm[i] - cand) <= deadband else 1
            cand = sm[i]
            if streak >= hold:
                pts.append((t, cur))
                pts.append((t + ease, cand))
                cur, streak = cand, 0
        else:
            streak = 0
    end_t = max(duration, pts[-1][0] + 0.1)
    pts.append((end_t, cur))
    return pts


def piecewise_expr(points: list[tuple[float, float]]) -> str:
    """ffmpeg expression for a piecewise-linear function of t."""
    expr = f"{points[-1][1]:.2f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        seg = f"{v1:.2f}" if t1 <= t0 else f"({v0:.2f}+({v1 - v0:.2f})*(t-{t0:.3f})/{t1 - t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def crop_x_expression(path: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    max_x = max(0, src_w - crop_w)
    pts = [(t, min(max(x * src_w - crop_w / 2, 0.0), float(max_x))) for t, x in path]
    return piecewise_expr(pts)
