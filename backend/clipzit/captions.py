"""Caption generation from real word timestamps. Times are relative to clip start.

Supports seven style presets, word highlighting, karaoke sweep, an opening-hook overlay and a text watermark.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .models import Sentence, Word

ACTIVE_COLOUR_ASS = "&H0000D4FF&"  # Clipzit yellow #FFD400 in ASS BGR order


@dataclass(frozen=True)
class CaptionStyle:
    name: str = "creator"
    font: str = "Arial"
    size_scale: float = 1.0
    primary: str = "#FFFFFF"
    highlight: str | None = "#FFD400"
    outline_colour: str = "#000000"
    outline_scale: float = 1.0
    shadow: int = 1
    box: bool = False
    box_alpha: int = 0x60          # 0 opaque .. 255 transparent
    uppercase: bool = False
    position: str = "bottom"       # bottom | middle | top
    max_words: int = 4
    max_chars: int = 24
    karaoke: bool = False
    pop: bool = False
    bold: bool = True


PRESETS: dict[str, CaptionStyle] = {
    "bold": CaptionStyle("bold", size_scale=1.2, outline_scale=1.4, uppercase=True, max_words=3, max_chars=20, pop=True),
    "minimal": CaptionStyle("minimal", size_scale=0.8, highlight=None, outline_scale=0.7, shadow=0, bold=False,
                            max_words=6, max_chars=34),
    "creator": CaptionStyle("creator", pop=True),
    "highlight": CaptionStyle("highlight", highlight="#39FF88", size_scale=1.05, outline_scale=1.2),
    "karaoke": CaptionStyle("karaoke", highlight="#FFD400", karaoke=True, max_words=5, max_chars=28),
    "clean": CaptionStyle("clean", highlight=None, box=True, outline_scale=0.0, shadow=0, size_scale=0.9,
                          bold=False, max_words=5, max_chars=30),
    "professional": CaptionStyle("professional", size_scale=0.85, highlight=None, outline_scale=0.8, shadow=2,
                                 bold=False, max_words=7, max_chars=38),
}


def style_from(name: str | None, **overrides) -> CaptionStyle:
    base = PRESETS.get((name or "creator").lower())
    if base is None:
        raise ValueError(f"unknown caption preset '{name}'. Choose from: {', '.join(PRESETS)}")
    clean = {k: v for k, v in overrides.items() if v is not None}
    style = replace(base, **clean) if clean else base
    for colour in (style.primary, style.highlight, style.outline_colour):
        if colour:
            ass_colour(colour)  # raises ValueError for anything but #RRGGBB
    if style.position not in ("bottom", "middle", "top"):
        raise ValueError("caption position must be bottom, middle or top")
    if not (0.4 <= style.size_scale <= 2.5):
        raise ValueError("caption size must be between 0.4 and 2.5")
    return style


def ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"invalid colour '{hex_rgb}', expected #RRGGBB")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper().replace("&H", "&H")


def clip_words(sentences: list[Sentence], start: float, end: float) -> list[Word]:
    """Words whose midpoint lies in [start, end], re-timed so the clip begins at 0."""
    out: list[Word] = []
    for s in sentences:
        if s.end < start or s.start > end:
            continue
        for w in s.words:
            mid = (w.start + w.end) / 2
            if start <= mid <= end:
                out.append(Word(max(0.0, w.start - start), min(end - start, w.end - start), w.text))
    return out


def chunk_words(words: list[Word], max_chars: int = 24, max_words: int = 4, max_gap: float = 0.8) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            length = sum(len(x.text) + 1 for x in cur) + len(w.text)
            gap = w.start - cur[-1].end
            ended = cur[-1].text.rstrip("\"')").endswith((".", "?", "!", "…"))
            if len(cur) >= max_words or length > max_chars or gap > max_gap or ended:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _srt_time(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(chunks: list[list[Word]]) -> str:
    lines = []
    for i, ch in enumerate(chunks):
        start = ch[0].start
        end = ch[-1].end
        if i + 1 < len(chunks):
            end = min(end, chunks[i + 1][0].start)
        if end <= start:
            end = start + 0.05
        lines.append(f"{i + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n{' '.join(w.text for w in ch)}\n")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def retime(words: list[Word], keep: list[tuple[float, float]]) -> list[Word]:
    """Map caption words onto a tightened timeline. `keep` are source intervals (relative to clip start) that remain.

    Words whose midpoint falls in a removed interval are dropped, so captions never describe cut speech.
    """
    out: list[Word] = []
    offsets, acc = [], 0.0
    for a, b in keep:
        offsets.append(acc)
        acc += b - a
    for w in words:
        for (a, b), off in zip(keep, offsets):
            mid = (w.start + w.end) / 2
            if a <= mid <= b:
                out.append(Word(max(0.0, off + max(w.start, a) - a), off + min(w.end, b) - a, w.text))
                break
    return out


def to_ass(chunks: list[list[Word]], width: int, height: int, highlight: bool = True, font: str = "Arial",
           style: CaptionStyle | None = None, hook_text: str | None = None, hook_seconds: float = 3.0,
           watermark: str | None = None, clip_duration: float | None = None) -> str:
    st = style or PRESETS["creator"]
    if style is None and not highlight:
        st = replace(st, highlight=None)
    if font != "Arial":
        st = replace(st, font=font)
    base = min(width, height)
    size = max(12, round(base * 0.067 * st.size_scale))
    outline = round(max(0, size / 16 * st.outline_scale), 1)
    margin_v = {"bottom": round(height * (0.18 if height > width else 0.08)),
                "middle": round(height * 0.42), "top": round(height * 0.12)}.get(st.position, 0)
    align = {"bottom": 2, "middle": 5, "top": 8}[st.position]
    primary = ass_colour(st.primary)
    secondary = ass_colour(st.highlight) if (st.highlight and st.karaoke) else primary
    if st.karaoke and st.highlight:  # karaoke sweeps SecondaryColour -> PrimaryColour
        primary, secondary = ass_colour(st.highlight), ass_colour(st.primary)
    if st.box:
        border_style, outline_col, back_col, outline = 3, ass_colour("#000000", st.box_alpha), ass_colour("#000000", st.box_alpha), max(size // 6, 6)
    else:
        border_style, outline_col, back_col = 1, ass_colour(st.outline_colour), ass_colour("#000000", 0x64)
    bold = -1 if st.bold else 0
    hook_size = round(base * 0.062)
    wm_size = max(12, round(base * 0.032))
    side = round(width * 0.06)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
        "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
        "MarginV,Encoding\n"
        f"Style: Default,{st.font},{size},{primary},{secondary},{outline_col},{back_col},{bold},0,0,0,100,100,0,0,"
        f"{border_style},{outline},{st.shadow},{align},{side},{side},{margin_v},1\n"
        f"Style: Hook,{st.font},{hook_size},&H00000000,&H00000000,{ass_colour('#FFD400')},&H00000000,-1,0,0,0,100,100,0,0,"
        f"3,{max(hook_size // 5, 6)},0,8,{side},{side},{round(height * 0.10)},1\n"
        f"Style: Mark,{st.font},{wm_size},&H80FFFFFF,&H80FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,3,"
        f"{side // 2},{side // 2},{round(height * 0.04)},1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    events: list[str] = []

    def txt(w: Word) -> str:
        t = _esc(w.text)
        return t.upper() if st.uppercase else t

    pop = r"{\fscx85\fscy85\t(0,120,\fscx100\fscy100)}" if st.pop else ""
    for ci, ch in enumerate(chunks):
        chunk_end = ch[-1].end
        if ci + 1 < len(chunks):
            chunk_end = min(chunk_end, chunks[ci + 1][0].start)
        chunk_end = max(chunk_end, ch[0].start + 0.05)
        if st.karaoke and st.highlight:
            parts = []
            for k, w in enumerate(ch):
                nxt = ch[k + 1].start if k + 1 < len(ch) else chunk_end
                cs = max(1, round((max(nxt, w.end if k + 1 == len(ch) else nxt) - w.start) * 100))
                parts.append("{\\kf" + str(cs) + "}" + txt(w))
            events.append(f"Dialogue: 0,{_ass_time(ch[0].start)},{_ass_time(chunk_end)},Default,,0,0,0,,{pop}{' '.join(parts)}")
            continue
        if not (highlight and st.highlight):
            events.append(f"Dialogue: 0,{_ass_time(ch[0].start)},{_ass_time(chunk_end)},Default,,0,0,0,,"
                          f"{pop}{' '.join(txt(w) for w in ch)}")
            continue
        hl = ass_colour(st.highlight)
        for k, w in enumerate(ch):
            ev_start = w.start if k else ch[0].start
            ev_end = ch[k + 1].start if k + 1 < len(ch) else chunk_end
            if ev_end <= ev_start:
                ev_end = ev_start + 0.05
            parts = []
            for m, x in enumerate(ch):
                t = txt(x)
                parts.append("{\\c" + hl + "&}" + t + "{\\c" + primary + "&}" if m == k else t)
            events.append(f"Dialogue: 0,{_ass_time(ev_start)},{_ass_time(ev_end)},Default,,0,0,0,,"
                          f"{pop if k == 0 else ''}{' '.join(parts)}")
    if hook_text and hook_text.strip():
        end = min(hook_seconds, clip_duration) if clip_duration else hook_seconds
        fade = r"{\fad(150,250)}"
        events.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Hook,,0,0,0,,{fade}{_esc(hook_text.strip())}")
    if watermark and watermark.strip() and clip_duration:
        events.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(clip_duration)},Mark,,0,0,0,,{_esc(watermark.strip())}")
    return head + "\n".join(events) + "\n"
