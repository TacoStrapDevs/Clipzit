"""Shared clip rendering used by the API and by multi-clip ZIP export."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import media, vision
from .analysis import build_sentences
from .captions import chunk_words, clip_words, retime, style_from, to_ass, to_srt
from .config import Settings
from .pacing import compute_keep
from .pipeline import job_dir, load_transcript

MUSIC_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

PLATFORMS = {
    # name: (aspect, fps, max_seconds)
    "tiktok": ("9:16", 30, 600),
    "shorts": ("9:16", 30, 180),
    "reels": ("9:16", 30, 90),
    "facebook": ("9:16", 30, 90),
    "vertical": ("9:16", 30, None),
}


@dataclass
class RenderOptions:
    start: float | None = None
    end: float | None = None
    platform: str = "custom"
    aspect: str = "9:16"
    mode: str = "fill"
    crop_x: float = 0.5
    crop_y: float = 0.5
    zoom: float = 1.0
    auto_frame: bool = False
    captions: bool = True
    caption_preset: str = "creator"
    caption_position: str | None = None
    caption_size: float | None = None
    caption_color: str | None = None
    highlight_color: str | None = None
    highlight: bool = True
    hook_text: str | None = None
    hook_seconds: float = 3.0
    watermark_text: str | None = None
    pacing: str = "off"
    remove_fillers: bool = False
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpen: float = 0.0
    normalize_audio: bool = True
    fade: bool = True
    music_id: str | None = None
    music_volume: float = 0.15
    crf: int = 20
    fps: int | None = None
    bitrate: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "RenderOptions":
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown render options: {sorted(unknown)}")
        o = cls(**d)
        if o.platform != "custom" and o.platform not in PLATFORMS:
            raise ValueError(f"platform must be custom or one of {sorted(PLATFORMS)}")
        if o.aspect != "original" and o.aspect not in media.ASPECTS:
            raise ValueError("aspect must be one of 9:16, 1:1, 16:9, 4:5, original")
        if o.mode not in ("fill", "blur"):
            raise ValueError("mode must be 'fill' or 'blur'")
        for name, lo, hi in (("crop_x", 0, 1), ("crop_y", 0, 1), ("zoom", 1, 3), ("brightness", -0.5, 0.5),
                             ("contrast", 0.5, 1.5), ("saturation", 0, 2), ("sharpen", 0, 1.5),
                             ("music_volume", 0, 1), ("hook_seconds", 0.5, 10), ("crf", 14, 30)):
            if not (lo <= getattr(o, name) <= hi):
                raise ValueError(f"{name} must be between {lo} and {hi}")
        if o.fps is not None and not (15 <= o.fps <= 60):
            raise ValueError("fps must be between 15 and 60")
        if o.bitrate is not None and not (o.bitrate[:-1].isdigit() and o.bitrate[-1] in "kKmM"):
            raise ValueError("bitrate must look like 6M or 8000k")
        if o.pacing not in ("off", "light", "medium", "strong"):
            raise ValueError("pacing must be off, light, medium or strong")
        style_from(o.caption_preset)  # validates preset name
        return o


def music_path(settings: Settings, music_id: str | None) -> Path | None:
    if not music_id:
        return None
    if not music_id.isalnum() or len(music_id) != 32:
        raise ValueError("invalid music id")
    matches = [p for ext in MUSIC_EXT if (p := settings.data_dir / "music" / f"{music_id}{ext}").exists()]
    if not matches:
        raise ValueError("music track not found")
    return matches[0]


def render_job_clip(settings: Settings, job: dict, clip: dict, opts: RenderOptions, out: Path) -> dict:
    """Render one clip to `out`. Returns {info, warnings, words, keep, stats}."""
    src = Path(job["video_path"])
    if not src.exists():
        raise media.MediaError("source video is no longer on the server")
    info = media.probe(src)
    warnings: list[str] = []
    aspect, fps = opts.aspect, opts.fps
    max_seconds = None
    if opts.platform != "custom":
        aspect, pfps, max_seconds = PLATFORMS[opts.platform]
        fps = fps or pfps
    start = clip["start"] if opts.start is None else opts.start
    end = min(clip["end"] if opts.end is None else opts.end, info.duration)
    if end - start < 0.5 or start >= info.duration:
        raise media.MediaError("invalid clip range")
    dur = end - start

    segs = load_transcript(settings, job["id"])
    words = clip_words(build_sentences(segs or []), start, end)
    keep, stats = None, {"removed_seconds": 0.0, "cuts": 0, "fillers_removed": 0}
    if opts.pacing != "off" or opts.remove_fillers:
        if not words:
            warnings.append("Pacing/filler removal skipped: no word timestamps for this range.")
        else:
            k, stats = compute_keep(words, dur, opts.pacing, opts.remove_fillers)
            keep = k if stats["cuts"] else None
    final_words = retime(words, keep) if keep else words
    final_dur = sum(b - a for a, b in keep) if keep else dur
    if max_seconds and final_dur > max_seconds:
        warnings.append(f"Clip is {final_dur:.0f}s; {opts.platform} allows up to {max_seconds}s.")

    face_path = None
    if opts.auto_frame and opts.mode == "fill" and aspect != "original":
        if not vision.available():
            warnings.append("Face framing unavailable on this server (install requirements-vision.txt); "
                            "used the manual crop position.")
        else:
            try:
                samples = vision.sample_positions(str(src), start, end, info)
                if any(x is not None for _t, x in samples):
                    face_path = vision.build_path(samples, dur)
                else:
                    warnings.append("No face detected; used the manual crop position.")
            except media.MediaError as exc:
                warnings.append(f"Face framing failed ({exc}); used the manual crop position.")

    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        ass = None
        if opts.captions or opts.hook_text or opts.watermark_text:
            ow, oh = (info.width, info.height) if aspect == "original" else media.ASPECTS[aspect]
            style = style_from(opts.caption_preset, position=opts.caption_position, size_scale=opts.caption_size,
                               primary=opts.caption_color,
                               highlight=opts.highlight_color if opts.highlight else None)
            chunks = chunk_words(final_words, style.max_chars, style.max_words) if (opts.captions and final_words) else []
            if not opts.highlight:
                style = style_from(opts.caption_preset, position=opts.caption_position, size_scale=opts.caption_size,
                                   primary=opts.caption_color)
            ass = Path(tmp) / "subs.ass"
            text = to_ass(chunks, ow, oh, highlight=opts.highlight, style=style,
                          hook_text=opts.hook_text, hook_seconds=opts.hook_seconds,
                          watermark=opts.watermark_text, clip_duration=final_dur)
            if opts.captions and not final_words:
                warnings.append("No transcript words in this range; rendered without captions.")
            ass.write_text(text, encoding="utf-8")
        mpath = music_path(settings, opts.music_id)
        partial = Path(tmp) / "out.mp4"
        result = media.render_clip(
            src, partial, start, end, info, aspect=aspect, mode=opts.mode, crop_x=opts.crop_x, crop_y=opts.crop_y,
            zoom=opts.zoom, face_path=face_path, keep=keep,
            enhance=media.Enhance(opts.brightness, opts.contrast, opts.saturation, opts.sharpen),
            music=(mpath, opts.music_volume) if mpath else None, ass_path=ass, normalize=opts.normalize_audio,
            fade=opts.fade, crf=opts.crf, fps=fps, video_bitrate=opts.bitrate, timeout=settings.ffmpeg_timeout)
        out.parent.mkdir(parents=True, exist_ok=True)
        partial.replace(out)
    return {"info": result, "warnings": warnings, "words": final_words, "keep": keep, "stats": stats,
            "options": asdict(opts), "range": (start, end), "face_tracked": face_path is not None}


def cache_key(job: dict, clip_index: int, opts: RenderOptions) -> str:
    try:
        mtime = Path(job["video_path"]).stat().st_mtime
    except OSError:
        mtime = 0
    raw = json.dumps([clip_index, mtime, asdict(opts)], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:14]


def render_cached(settings: Settings, job: dict, clip: dict, opts: RenderOptions) -> tuple[Path, dict]:
    out_dir = job_dir(settings, job["id"]) / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"clip{clip['index']}_{cache_key(job, clip['index'], opts)}.mp4"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        return out, json.loads(meta.read_text())
    res = render_job_clip(settings, job, clip, opts, out)
    summary = {"warnings": res["warnings"], "stats": res["stats"], "face_tracked": res["face_tracked"],
               "width": res["info"].width, "height": res["info"].height, "duration": round(res["info"].duration, 3),
               "srt": to_srt(chunk_words(res["words"])) if res["words"] else ""}
    meta.write_text(json.dumps(summary), encoding="utf-8")
    return out, summary


def build_zip(settings: Settings, job: dict, clips: list[dict], opts: RenderOptions, zip_path: Path,
              on_progress=None) -> list[dict]:
    """Render every clip and bundle MP4 + SRT + metadata. Fails loudly if any clip fails."""
    manifest = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for n, clip in enumerate(clips):
            out, summary = render_cached(settings, job, clip, opts)
            if not out.exists() or out.stat().st_size == 0:
                raise media.MediaError(f"clip {clip['index'] + 1} rendered no file")
            base = f"clip{n + 1:02d}"
            zf.write(out, f"{base}.mp4")
            if summary.get("srt"):
                zf.writestr(f"{base}.srt", summary["srt"])
            entry = {"file": f"{base}.mp4", "index": clip["index"], "title": clip["title"],
                     "hashtags": clip["hashtags"], "description": clip.get("description", ""),
                     "score": clip["score"], "score_kind": clip["score_kind"],
                     "source_range": [clip["start"], clip["end"]], "hooks": clip["hooks"],
                     "duration": summary["duration"], "warnings": summary["warnings"]}
            zf.writestr(f"{base}.json", json.dumps(entry, indent=2))
            manifest.append(entry)
            if on_progress:
                on_progress((n + 1) / len(clips))
        zf.writestr("manifest.json", json.dumps({"job": job["name"], "clips": manifest}, indent=2))
    return manifest


def purge_renders(settings: Settings, max_age_days: float = 7.0, max_bytes_per_job: int = 3 * 1024**3) -> int:
    """Delete old cached renders (and abandoned export temp folders) so disk use cannot grow without bound."""
    removed = 0
    now = time.time()
    for job in settings.jobs_dir.glob("*"):
        if not job.is_dir():
            continue
        for tmp in job.glob("tmp*"):  # leftovers from interrupted ZIP exports / renders
            if tmp.is_dir() and now - tmp.stat().st_mtime > 3600:
                shutil.rmtree(tmp, ignore_errors=True)
                removed += 1
        rdir = job / "renders"
        if not rdir.is_dir():
            continue
        files = sorted(rdir.glob("*.mp4"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            too_old = now - f.stat().st_mtime > max_age_days * 86400
            if too_old or total > max_bytes_per_job:
                total -= f.stat().st_size
                f.unlink(missing_ok=True)
                f.with_suffix(".json").unlink(missing_ok=True)
                removed += 1
    return removed
