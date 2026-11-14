"""FFmpeg / FFprobe wrappers. All failures raise MediaError with a readable message."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(Exception):
    pass


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool


ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}


def run(cmd: list[str], timeout: int, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError as exc:
        raise MediaError(f"{cmd[0]} is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise MediaError(f"{cmd[0]} failed (exit {proc.returncode}): {tail}")
    return proc


def probe(path: str | Path, timeout: int = 60) -> MediaInfo:
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               timeout)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned unreadable output") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise MediaError("file contains no audio or video streams")
    try:
        duration = float(data.get("format", {}).get("duration") or (video or audio).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise MediaError("could not determine media duration (file may be corrupt)")
    width = height = 0
    if video:
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        rot = 0
        try:
            rot = int(float(video.get("tags", {}).get("rotate", 0)))
        except (TypeError, ValueError):
            pass
        for sd in video.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        if abs(rot) % 180 == 90:
            width, height = height, width
    return MediaInfo(duration, width, height, video is not None, audio is not None)


def extract_audio(video: str | Path, out_wav: str | Path, timeout: int) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out_wav)], timeout)


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(video: str | Path, timeout: int, noise_db: int = -35, min_dur: float = 0.4,
                    total: float | None = None) -> list[tuple[float, float]]:
    proc = run(["ffmpeg", "-v", "info", "-nostats", "-i", str(video), "-vn", "-af",
                f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"], timeout)
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None and total:
        out.append((start, total))
    return out


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def crop_box(src_w: int, src_h: int, out_w: int, out_h: int, zoom: float, cx: float, cy: float):
    ar = out_w / out_h
    cw, ch = src_h * ar, float(src_h)
    if cw > src_w:
        cw, ch = float(src_w), src_w / ar
    zoom = max(1.0, zoom)
    cw, ch = _even(cw / zoom), _even(ch / zoom)
    cw, ch = min(cw, _even(src_w)), min(ch, _even(src_h))
    x = int((src_w - cw) * max(0.0, min(1.0, cx)))
    y = int((src_h - ch) * max(0.0, min(1.0, cy)))
    return cw, ch, x, y


@dataclass
class Enhance:
    brightness: float = 0.0   # -0.5 .. 0.5
    contrast: float = 1.0     # 0.5 .. 1.5
    saturation: float = 1.0   # 0 .. 2
    sharpen: float = 0.0      # 0 .. 1.5

    def active(self) -> bool:
        return (self.brightness, self.contrast, self.saturation, self.sharpen) != (0.0, 1.0, 1.0, 0.0)


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def render_clip(src: str | Path, out: str | Path, start: float, end: float, info: MediaInfo, *,
                aspect: str = "9:16", mode: str = "fill", crop_x: float = 0.5, crop_y: float = 0.5,
                zoom: float = 1.0, face_path: list[tuple[float, float]] | None = None,
                keep: list[tuple[float, float]] | None = None, enhance: Enhance | None = None,
                music: tuple[str | Path, float] | None = None, ass_path: str | Path | None = None,
                normalize: bool = True, fade: bool = True, crf: int = 20, fps: int | None = None,
                video_bitrate: str | None = None, timeout: int = 3600) -> MediaInfo:
    """Render one clip. `keep` = intervals (seconds relative to `start`) that remain after pause/filler removal."""
    if not info.has_video:
        raise MediaError("source has no video stream")
    dur = end - start
    if dur <= 0.2 or start < 0 or start >= info.duration:
        raise MediaError("invalid clip range")
    end = min(end, info.duration)
    dur = end - start
    if aspect == "original":
        out_w, out_h = _even(info.width), _even(info.height)
    elif aspect in ASPECTS:
        out_w, out_h = ASPECTS[aspect]
    else:
        raise MediaError(f"unsupported aspect ratio: {aspect}")
    if mode not in ("fill", "blur"):
        raise MediaError(f"unsupported framing mode: {mode}")
    if keep is not None:
        keep = [(max(0.0, a), min(dur, b)) for a, b in keep if min(dur, b) - max(0.0, a) > 0.05]
        if not keep:
            raise MediaError("nothing left after removing pauses")
        if len(keep) > 60:
            raise MediaError("too many cuts requested (max 60 segments)")
    final_dur = sum(b - a for a, b in keep) if keep else dur
    want_audio = info.has_audio or music is not None

    g: list[str] = []
    # 1) reframe
    if aspect == "original":
        g.append(f"[0:v]scale={out_w}:{out_h},setsar=1[vr]")
    elif mode == "fill":
        cw, ch, x, y = crop_box(info.width, info.height, out_w, out_h, zoom, crop_x, crop_y)
        xexpr = str(x)
        if face_path:
            from .vision import crop_x_expression
            xexpr = "'" + crop_x_expression(face_path, info.width, cw) + "'"
        g.append(f"[0:v]crop={cw}:{ch}:{xexpr}:{y},scale={out_w}:{out_h}:flags=lanczos,setsar=1[vr]")
    else:
        g.append(f"[0:v]split=2[a][b];[a]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                 f"crop={out_w}:{out_h},boxblur=24:6[bg];[b]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease"
                 f"[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vr]")
    cur_v = "vr"
    # 2) colour / sharpness
    if enhance and enhance.active():
        parts = [f"eq=brightness={_fmt(enhance.brightness)}:contrast={_fmt(enhance.contrast)}:"
                 f"saturation={_fmt(enhance.saturation)}"]
        if enhance.sharpen > 0:
            parts.append(f"unsharp=5:5:{_fmt(enhance.sharpen)}:5:5:0")
        g.append(f"[{cur_v}]{','.join(parts)}[ve]")
        cur_v = "ve"
    cur_a = "0:a" if info.has_audio else None
    # 3) pause / filler removal (concat of kept intervals)
    if keep:
        n = len(keep)
        if n == 1:
            a, b = keep[0]
            g.append(f"[{cur_v}]trim=start={_fmt(a)}:end={_fmt(b)},setpts=PTS-STARTPTS[vt]")
            if cur_a:
                g.append(f"[{cur_a}]atrim=start={_fmt(a)}:end={_fmt(b)},asetpts=PTS-STARTPTS[at]")
        else:
            g.append(f"[{cur_v}]split={n}" + "".join(f"[sv{k}]" for k in range(n)))
            if cur_a:
                g.append(f"[{cur_a}]asplit={n}" + "".join(f"[sa{k}]" for k in range(n)))
            labels = ""
            for k, (a, b) in enumerate(keep):
                g.append(f"[sv{k}]trim=start={_fmt(a)}:end={_fmt(b)},setpts=PTS-STARTPTS[tv{k}]")
                labels += f"[tv{k}]"
                if cur_a:
                    d = b - a
                    g.append(f"[sa{k}]atrim=start={_fmt(a)}:end={_fmt(b)},asetpts=PTS-STARTPTS,"
                             f"afade=t=in:d=0.01,afade=t=out:st={_fmt(max(d - 0.01, 0))}:d=0.01[ta{k}]")
                    labels += f"[ta{k}]"
            g.append(f"{labels}concat=n={n}:v=1:a={1 if cur_a else 0}[vt]" + ("[at]" if cur_a else ""))
        cur_v = "vt"
        if cur_a:
            cur_a = "at"
    # 4) burned-in subtitles
    if ass_path:
        g.append(f"[{cur_v}]subtitles=filename={Path(ass_path).name}[vs]")
        cur_v = "vs"
    if fade and final_dur > 1.0:
        g.append(f"[{cur_v}]fade=t=in:st=0:d=0.15,fade=t=out:st={_fmt(final_dur - 0.15)}:d=0.15[vf]")
        cur_v = "vf"
    # 5) audio: normalise voice, optional music bed, fades
    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(Path(src).resolve())]
    out_a = None
    if want_audio:
        chain = []
        if cur_a and normalize:
            chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if cur_a:
            g.append(f"[{cur_a}]{','.join(chain) if chain else 'anull'}[av]")
            voice = "av"
        else:
            voice = None
        if music is not None:
            mpath, mvol = music
            inputs += ["-stream_loop", "-1", "-i", str(Path(mpath).resolve())]
            g.append(f"[1:a]atrim=0:{_fmt(final_dur)},asetpts=PTS-STARTPTS,volume={_fmt(max(0.0, min(mvol, 1.0)))},"
                     f"aresample=48000,aformat=channel_layouts=stereo[mu]")
            if voice:
                g.append(f"[{voice}]aresample=48000,aformat=channel_layouts=stereo[vo]")
                g.append("[vo][mu]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[am]")
                voice = "am"
            else:
                voice = "mu"
        out_a = voice
        if fade and final_dur > 1.0:
            g.append(f"[{out_a}]afade=t=in:st=0:d=0.15,afade=t=out:st={_fmt(final_dur - 0.15)}:d=0.15[ao]")
            out_a = "ao"

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(g), "-map", f"[{cur_v}]"]
    if out_a:
        cmd += ["-map", f"[{out_a}]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p"]
    if video_bitrate:
        cmd += ["-b:v", video_bitrate, "-maxrate", video_bitrate, "-bufsize", video_bitrate]
    else:
        cmd += ["-crf", str(crf)]
    if fps:
        cmd += ["-r", str(fps)]
    cmd += ["-t", f"{final_dur:.3f}", "-movflags", "+faststart", str(Path(out).resolve())]
    cwd = Path(ass_path).parent if ass_path else None
    run(cmd, timeout, cwd=cwd)

    # Never report success until the output has been re-opened and checked.
    result = probe(out)
    if not result.has_video or (result.width, result.height) != (out_w, out_h):
        raise MediaError(f"output validation failed: got {result.width}x{result.height}, expected {out_w}x{out_h}")
    if want_audio and not result.has_audio:
        raise MediaError("output validation failed: audio track missing")
    if abs(result.duration - final_dur) > 0.6:
        raise MediaError(f"output validation failed: duration {result.duration:.2f}s, expected {final_dur:.2f}s")
    return result
