"""Pause and filler-word removal planning. Never cuts through a word."""
from __future__ import annotations

from .analysis import FILLERS, norm
from .models import Word

INTENSITY = {
    # intensity: (cut gaps longer than, leave this much of the gap)
    "light": (1.2, 0.6),
    "medium": (0.7, 0.35),
    "strong": (0.45, 0.2),
}


def compute_keep(words: list[Word], clip_duration: float, intensity: str = "medium",
                 remove_fillers: bool = False) -> tuple[list[tuple[float, float]], dict]:
    """Return (kept intervals relative to clip start, stats). `words` must be clip-relative."""
    if intensity != "off" and intensity not in INTENSITY:
        raise ValueError("pacing must be one of: off, light, medium, strong")
    if (intensity == "off" and not remove_fillers) or not words:
        return [(0.0, clip_duration)], {"removed_seconds": 0.0, "cuts": 0, "fillers_removed": 0}
    cuts: list[tuple[float, float]] = []
    fillers = 0
    if intensity != "off":
        thr, leave = INTENSITY[intensity]
        if words[0].start > 0.4:
            cuts.append((0.0, words[0].start - 0.15))
        for a, b in zip(words, words[1:]):
            gap = b.start - a.end
            keep_gap = leave + (0.15 if a.text.rstrip("\"')").endswith((".", "?", "!", "…")) else 0.0)
            if gap > max(thr, keep_gap + 0.1):
                cuts.append((a.end + keep_gap / 2, b.start - keep_gap / 2))
        if clip_duration - words[-1].end > 0.5:
            cuts.append((words[-1].end + 0.3, clip_duration))
    if remove_fillers:
        for w in words:
            if norm(w.text) in FILLERS:
                cuts.append((max(0.0, w.start - 0.02), min(clip_duration, w.end + 0.02)))
                fillers += 1
    cuts = sorted((a, b) for a, b in cuts if b - a > 0.05)
    merged: list[list[float]] = []
    for a, b in cuts:
        if merged and a <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    keep: list[tuple[float, float]] = []
    pos = 0.0
    for a, b in merged:
        if a - pos > 0.08:
            keep.append((pos, a))
        pos = max(pos, b)
    if clip_duration - pos > 0.08:
        keep.append((pos, clip_duration))
    if not keep:
        return [(0.0, clip_duration)], {"removed_seconds": 0.0, "cuts": 0, "fillers_removed": 0}
    removed = clip_duration - sum(b - a for a, b in keep)
    return keep, {"removed_seconds": round(removed, 3), "cuts": len(merged), "fillers_removed": fillers}
