# Clipzit backend API

Base URL: `http://<host>:8000`. If `CLIPZIT_API_TOKEN` is set, every endpoint except `/api/health`
requires `Authorization: Bearer <token>`. Errors are JSON: `{"detail": "..."}`.

## Health
`GET /api/health` → ffmpeg present, `transcription_available` (faster-whisper), `face_tracking_available`
(OpenCV + Haar model), `quality_models`, `caption_presets`, `platforms`.

## Uploading
* **Resumable (used by the Android app):**
  1. `POST /api/uploads` JSON `{filename,size,name,preset,min_seconds,max_seconds,max_clips,language,quality}` → `{upload_id,offset:0}`
  2. `PUT /api/uploads/{id}?offset=N` with raw bytes (≤ 32 MB). `409` if `N` is not the server's current offset
     (nothing is duplicated). `GET /api/uploads/{id}` returns the current `offset` so a client can resume after a
     dropped connection or app restart.
  3. `POST /api/uploads/{id}/complete` → `202 {"id": job_id}`. Idempotent: repeating it returns the same job.
  4. `DELETE /api/uploads/{id}` abandons an upload. Unfinished uploads are purged after 3 days.
* **One-shot:** `POST /api/jobs` multipart: `file`, optional `transcript` (JSON), plus the same fields.

`preset`: `15-30`, `30-45`, `45-60`, `60-90`, `custom` (with `min_seconds`/`max_seconds`, 3–300).
`quality`: `performance` (Whisper tiny), `balanced` (base), `quality` (small).

## Jobs
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/jobs`, `/api/jobs/{id}` | Status: `queued, uploading, transcribing, analyzing, discovering, completed, failed, cancelled`, `stage`, `progress` 0..1, `error` |
| POST | `/api/jobs/{id}/cancel` | Cancel an active job |
| POST | `/api/jobs/{id}/retry` | Re-run a failed/cancelled job. The saved transcript is reused |
| POST | `/api/jobs/{id}/regenerate` | New suggestions with new `preset`/`min_seconds`/`max_seconds`/`max_clips`. Reuses the transcript, so it takes seconds |
| DELETE | `/api/jobs/{id}` | Delete job and all files |
| GET | `/api/jobs/{id}/clips` | Ranked clips (see below) |
| GET | `/api/jobs/{id}/clips/{n}/srt` | Subtitles for the clip, timed from the clip start |
| GET | `/api/jobs/{id}/clips/{n}/alternatives` | Nearby sentence-aligned start/end suggestions with scores |
| GET | `/api/jobs/{id}/search?q=` | Transcript keyword search with timestamps |

A clip: `index, start, end, duration, score, score_kind, signals{hook,completeness,coherence,information,clarity,emotion,delivery,
standalone,pacing,duration}, reasons[], suggestions[], metrics, transcript, hooks[{text,style,score,verbatim}], title,
description, hashtags[], fillers[], approximate_timing`. Scores are **estimated content-quality indicators**, not view
predictions. Hooks, titles and descriptions are verbatim excerpts of the transcript.

## Rendering
`POST /api/jobs/{id}/clips/{n}/render` with a JSON body of options (all optional) returns an MP4 (H.264/AAC).
The result is validated (dimensions, audio, duration) before it is returned; the header `X-Clipzit-Warnings` is a JSON
list of non-fatal notes (for example "Face framing unavailable").

| Option | Values |
|---|---|
| `start`, `end` | seconds in the source (defaults to the clip) |
| `platform` | `custom`, `tiktok`, `shorts`, `reels`, `facebook`, `vertical` (sets 9:16 and 30 fps; warns when too long) |
| `aspect` | `9:16`, `1:1`, `16:9`, `4:5`, `original` |
| `mode` | `fill` (crop) or `blur` (fit over a blurred copy of the picture) |
| `crop_x`, `crop_y`, `zoom` | manual framing (zoom 1–3) |
| `auto_frame` | follow the largest face (needs OpenCV on the server) |
| `captions`, `caption_preset` | `creator, bold, karaoke, highlight, clean, minimal, professional` |
| `caption_position`, `caption_size`, `caption_color`, `highlight_color`, `highlight` | style overrides (`#RRGGBB`) |
| `hook_text`, `hook_seconds` | text box over the opening |
| `watermark_text` | your own text mark |
| `pacing` | `off, light, medium, strong` (shorten long pauses; never cuts words) |
| `remove_fillers` | cut um/uh/er/hmm |
| `brightness, contrast, saturation, sharpen` | basic colour and sharpness |
| `normalize_audio`, `fade` | loudness normalisation, 0.15 s fades |
| `music_id`, `music_volume` | background track from `/api/music` |
| `crf`, `fps`, `bitrate` | quality controls (`bitrate` like `8M`) |

`POST /api/jobs/{id}/export.zip` body `{"indices":[0,2], "options":{...}, "ranges":{"0":[start,end]}}` renders the
selected clips (all if `indices` is omitted) and returns a ZIP with `clipNN.mp4`, `clipNN.srt`, `clipNN.json` and
`manifest.json`. The ZIP is returned only if every clip rendered and validated.

## Music
`POST /api/music` (multipart `file`: mp3, wav, m4a, aac, ogg, flac; ≤ 60 MB) → `{id}`. `GET /api/music`,
`DELETE /api/music/{id}`. Use your own or royalty-free tracks.

## Transcript JSON (optional upload, skips Whisper)
`{"segments":[{"start":0.0,"end":3.2,"text":"...","words":[{"start":0.0,"end":0.4,"text":"Hello"}]}]}`
