"""Transcript-driven clip discovery.

Everything here is deterministic and derived from the transcript (and optional
silence intervals). No random scores, no invented quotes: hooks are verbatim
sentences from the clip.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .audio import EnergyProfile, delivery_score
from .models import Segment, Sentence, Word

TERMINALS = (".", "?", "!", "…")
NO_SPLIT = {"mr.", "mrs.", "ms.", "dr.", "vs.", "e.g.", "i.e.", "st."}

FILLERS = {"um", "uh", "uhm", "er", "erm", "hmm", "mm", "ah"}
WEAK_STARTS = {
    "and", "but", "so", "because", "which", "then", "also", "or", "yet",
    "he", "she", "it", "they", "them", "his", "her", "its", "their",
    "that", "those", "these", "there", "anyway", "however", "plus",
}
HOOK_WORDS = {
    "secret", "never", "mistake", "mistakes", "why", "nobody", "truth", "biggest",
    "worst", "best", "stop", "actually", "crazy", "insane", "shocking", "wrong",
    "problem", "lesson", "everyone", "nothing", "always", "hardest", "easiest",
    "most", "only", "real", "reason", "rule", "myth", "hidden", "unexpected",
    "surprising", "impossible", "dangerous", "free", "simple", "fastest",
    "lost", "failed", "regret", "quit", "broke", "ruined", "wasted", "nightmare",
}
NUMBER_WORDS = {"hundred", "thousand", "million", "billion", "percent", "dollars", "double", "doubled", "triple"}
EMOTION_WORDS = {
    "love", "hate", "scared", "afraid", "angry", "amazing", "incredible", "terrible",
    "awesome", "hilarious", "funny", "cried", "crying", "laugh", "laughed", "laughing",
    "shocked", "excited", "worried", "proud", "ashamed", "heartbroken", "furious",
    "unbelievable", "nightmare", "dream", "painful", "wow", "insane", "crazy",
}
LAUGH_RE = re.compile(r"\b(ha){2,}\b|\[laugh|\(laugh|\blol\b", re.I)
STOP = set(
    "a an the and or but if so to of in on at for with from by as is are was were be been being "
    "it its this that these those i you he she we they me him her us them my your his our their "
    "do does did done have has had having will would can could should may might must not no yes "
    "just very really like get got going go went know think say said says than then there here "
    "what which who whom when where how why about into out up down over under again more most "
    "some any each other such only own same too also all both few one two".split()
)

INTRO_PHRASES = (
    "welcome back", "hey guys", "hi everyone", "hello everyone", "what's up", "what is up", "in this video",
    "before we get started", "before we start", "today we", "today i",
)
OUTRO_PHRASES = (
    "thanks for watching", "thank you for watching", "see you in the next", "see you next time",
    "like and subscribe", "and subscribe", "don't forget to subscribe", "subscribe to the", "subscribe to my",
    "link in the description", "hit the bell", "smash the like", "see you later",
)
NEG_HOOK_NOTES = ("Starts mid-thought", "Starts with a filler", "Opens with generic intro", "Looks like a sign-off",
                  "Opening line is very short", "Opening line is long", "Empty opening")
CONCLUSION_MARKERS = (
    "that's why", "that is why", "the lesson", "in the end", "the point is", "the takeaway", "that's the secret",
    "that is the secret", "so remember", "bottom line", "that's it", "and that's", "which is why", "so stop",
    "so start", "it works",
)

WEIGHTS = {
    "hook": 0.19,
    "completeness": 0.14,
    "information": 0.12,
    "clarity": 0.10,
    "emotion": 0.08,
    "standalone": 0.08,
    "coherence": 0.10,
    "pacing": 0.06,
    "duration": 0.07,
    "delivery": 0.06,
}

# Attention patterns that reliably open a curiosity loop, a contrarian claim or a promised list.
HOOK_PATTERNS = (
    (re.compile(r"\b(here'?s|this is) (why|how|what|the)\b", re.I), 10, "Opens a curiosity loop"),
    (re.compile(r"\b(you (won'?t|will not|never) (believe|guess|see))\b", re.I), 12, "Opens a curiosity loop"),
    (re.compile(r"\b(nobody|no one) (tells|talks|knows|told|taught|warned)\b", re.I), 12, "Promises something unspoken"),
    (re.compile(r"\b(everyone|most people|they|people) (think|says?|believe|assume|tell)s?\b.*\b(but|actually|wrong|lie|myth)\b",
                re.I), 14, "Sets up a contrarian claim"),
    (re.compile(r"\b(\d+|two|three|four|five|six|seven|ten) (things|ways|reasons|mistakes|tips|steps|rules|lessons|secrets)\b",
                re.I), 10, "Promises a concrete list"),
    (re.compile(r"\b(stop|never|don'?t) (doing|using|saying|buying|eating|making|trying|wasting)\b", re.I), 8,
     "Gives a direct warning"),
    (re.compile(r"^(i|we) (was|got|lost|made|spent|quit|almost|nearly|failed|tried|went|woke)\b", re.I), 6,
     "Starts a personal story"),
    (re.compile(r"^(when|back when|last (year|month|week)|one (day|time|night)|the (first|last) time)\b.*\b(i|we)\b",
                re.I), 8, "Starts a personal story"),
)

TANGENT_MARKERS = ("anyway", "by the way", "oh and", "speaking of", "random thing", "off topic", "side note",
                   "so yeah", "also,")

ARC_SETUP = ("when i", "once ", "last year", "years ago", "i was", "i used to", "back then", "growing up", "one day",
             "the first time", "we were", "at first")
ARC_TURN = ("but ", "then ", "until", "suddenly", "however", "except", "turns out", "realized", "realised",
            "discovered", "that's when", "and then", "one thing")
ARC_PAYOFF = ("so ", "finally", "now ", "that's why", "which is why", "in the end", "it worked", "the result",
              "ended up", "learned", "lesson", "that's the")


@dataclass
class Ctx:
    """Whole-video context handed to the window scorer."""
    bsim: list[float]
    i: int
    j: int
    idf: dict[str, float] | None = None
    idf_norm: float = 1.0
    energy: EnergyProfile | None = None
    counts: list[Counter] | None = None

PRESETS = {
    "15-30": (15.0, 30.0),
    "30-45": (30.0, 45.0),
    "45-60": (45.0, 60.0),
    "60-90": (60.0, 90.0),
}


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(round(max(lo, min(hi, x))))


def norm(token: str) -> str:
    return re.sub(r"[^\w']", "", token.lower())


# --------------------------------------------------------------------------- sentences

def _ends_sentence(text: str) -> bool:
    t = text.rstrip("\"')]”’")
    if t.lower() in NO_SPLIT:
        return False
    return t.endswith(TERMINALS)


def _synthesize_words(seg: Segment) -> list[Word]:
    toks = seg.text.split()
    if not toks:
        return []
    step = (seg.end - seg.start) / len(toks)
    return [Word(seg.start + i * step, seg.start + (i + 1) * step, t) for i, t in enumerate(toks)]


def build_sentences(segments: list[Segment], max_gap: float = 1.0, max_words: int = 45) -> list[Sentence]:
    """Group word-level (or interpolated) timestamps into sentences."""
    words: list[Word] = []
    approx_flags: list[bool] = []
    for seg in segments:
        if seg.words:
            ws, approx = seg.words, False
        else:
            ws, approx = _synthesize_words(seg), True
        for w in ws:
            if w.text.strip():
                words.append(w)
                approx_flags.append(approx)
    sentences: list[Sentence] = []
    cur: list[Word] = []
    cur_approx = False
    for i, w in enumerate(words):
        cur.append(w)
        cur_approx = cur_approx or approx_flags[i]
        nxt_gap = (words[i + 1].start - w.end) if i + 1 < len(words) else 0.0
        if _ends_sentence(w.text) or nxt_gap > max_gap or len(cur) >= max_words or i + 1 == len(words):
            text = " ".join(x.text for x in cur).strip()
            sentences.append(Sentence(cur[0].start, cur[-1].end, text, cur, cur_approx))
            cur, cur_approx = [], False
    return sentences


# --------------------------------------------------------------------------- signals

def hook_signals(text: str) -> tuple[int, list[str]]:
    toks = [norm(t) for t in text.split() if norm(t)]
    n = len(toks)
    score, notes = 40.0, []
    if not toks:
        return 0, ["Empty opening"]
    if text.rstrip().endswith("?"):
        score += 18
        notes.append("Opens with a question")
    if re.search(r"\d", text) or any(t in NUMBER_WORDS for t in toks):
        score += 8
        notes.append("Opens with a concrete number")
    hits = [t for t in toks if t in HOOK_WORDS]
    if hits:
        score += min(24, 8 * len(hits))
        notes.append("Opening uses attention words (" + ", ".join(sorted(set(hits))[:3]) + ")")
    if "you" in toks or "your" in toks:
        score += 6
        notes.append("Speaks directly to the viewer")
    if n < 4:
        score -= 18
        notes.append("Opening line is very short")
    elif n > 28:
        score -= 12
        notes.append("Opening line is long")
    elif 5 <= n <= 18:
        score += 6
    if toks[0] in WEAK_STARTS:
        score -= 20
        notes.append("Starts mid-thought")
    if toks[0] in FILLERS:
        score -= 15
        notes.append("Starts with a filler word")
    bonus, seen = 0, set()
    for rx, pts, note in HOOK_PATTERNS:
        if rx.search(text):
            bonus += pts
            if note not in seen:
                seen.add(note)
                notes.append(note)
    score += min(30, bonus)
    low = text.lower()
    if any(p in low for p in INTRO_PHRASES):
        score -= 25
        notes.append("Opens with generic intro boilerplate")
    if any(p in low for p in OUTRO_PHRASES):
        score -= 30
        notes.append("Looks like a sign-off, not a hook")
    return clamp(score), notes


def _content_words(tokens: list[str]) -> list[str]:
    return [t for t in tokens if len(t) > 2 and t not in STOP and not t.isdigit()]


def stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def term_counts(sent: Sentence) -> Counter:
    toks = [norm(t) for t in sent.text.split() if norm(t)]
    return Counter(stem(t) for t in _content_words(toks))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def build_idf(counts: list[Counter]) -> tuple[dict[str, float], float]:
    """Inverse document frequency over sentences: rare terms mark what is distinctive about this video."""
    n = max(len(counts), 1)
    df: Counter = Counter()
    for c in counts:
        df.update(c.keys())
    idf = {t: math.log((n + 1) / (d + 1)) + 0.0 for t, d in df.items()}
    # a term seen in a single sentence is the most distinctive possible: that maximum maps to 1.0
    return idf, max(math.log((n + 1) / 2), 1.0)


def boundary_similarities(counts: list[Counter], block: int = 3) -> list[float]:
    """TextTiling-style similarity across every sentence boundary. Low value = topic shift (a good place to cut)."""
    n = len(counts)
    out = [0.0] * (n + 1)
    for k in range(1, n):
        left, right = Counter(), Counter()
        for c in counts[max(0, k - block):k]:
            left.update(c)
        for c in counts[k:k + block]:
            right.update(c)
        out[k] = cosine(left, right)
    return out


def window_signals(sents: list[Sentence], start: float, end: float, lo: float, hi: float,
                   silences: list[tuple[float, float]] | None = None,
                   ctx: Ctx | None = None) -> tuple[dict, list[str], list[str], dict]:
    dur = max(end - start, 0.001)
    text = " ".join(s.text for s in sents)
    toks = [norm(t) for t in text.split() if norm(t)]
    words = [w for s in sents for w in s.words]
    first = sents[0].text
    last = sents[-1].text

    reasons: list[str] = []
    tips: list[str] = []

    # hook
    hook, hnotes = hook_signals(first)
    reasons.extend([n for n in hnotes if not n.startswith(NEG_HOOK_NOTES)][:3])
    tips.extend([n + "." for n in hnotes if n.startswith(NEG_HOOK_NOTES)][:1])
    if hook < 50:
        tips.append("The opening line is weak; consider using a stronger sentence from the clip as an on-screen hook.")

    # pacing and speech ratio
    gaps = [max(0.0, words[i + 1].start - words[i].end) for i in range(len(words) - 1)]
    long_gaps = [g for g in gaps if g > 0.35]
    pause_time = sum(long_gaps)
    if silences:
        sil = sum(max(0.0, min(e, end) - max(s, start)) for s, e in silences)
        pause_time = max(pause_time, sil)
    speech_ratio = max(0.0, min(1.0, 1 - pause_time / dur))
    max_pause = max(gaps) if gaps else 0.0
    wps = len(words) / dur
    pacing = 100.0
    if speech_ratio < 0.85:
        pacing -= (0.85 - speech_ratio) * 200
    if max_pause > 1.5:
        pacing -= min(40, (max_pause - 1.5) * 20)
        tips.append("Long internal pause detected; trimming silence may improve pacing.")
    if wps < 1.6:
        pacing -= (1.6 - wps) * 25
    if wps > 4.2:
        pacing -= (wps - 4.2) * 20
    pacing = clamp(pacing)
    if pacing >= 80:
        reasons.append("Steady pacing with little dead air")

    # clarity
    fillers = [w for w in words if norm(w.text) in FILLERS]
    filler_ratio = len(fillers) / max(len(words), 1)
    clarity = 90.0 - filler_ratio * 400
    if 1.8 <= wps <= 3.6:
        clarity += 8
    elif wps < 1.0 or wps > 5.0:
        clarity -= 25
    avg_len = sum(len(t) for t in toks) / max(len(toks), 1)
    if avg_len < 3.2:
        clarity -= 10
    clarity = clamp(clarity)
    if filler_ratio > 0.04:
        tips.append("Several filler words (um/uh) found; consider removing them.")

    # emotion
    excl = text.count("!")
    emo_hits = sum(1 for t in toks if t in EMOTION_WORDS)
    laugh = len(LAUGH_RE.findall(text))
    qmarks = text.count("?")
    emotion = 30 + min(30, excl * 10) + min(25, emo_hits * 8) + min(25, laugh * 15) + min(10, qmarks * 4)
    emotion = clamp(emotion)
    if laugh:
        reasons.append("Contains laughter or humour markers")
    if emo_hits >= 2:
        reasons.append("Emotionally expressive language")

    # information value
    content = _content_words(toks)
    unique_ratio = len(set(content)) / max(len(content), 1)
    nums = len(re.findall(r"\d", text))
    caps = len(re.findall(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}", text))
    rarity = 0.5
    if ctx is not None and ctx.idf:
        stems = {stem(t) for t in content}
        if stems:
            rarity = min(1.0, (sum(ctx.idf.get(t, ctx.idf_norm) for t in stems) / len(stems)) / ctx.idf_norm)
    information = 20 + unique_ratio * 25 + rarity * 25 + min(15, nums * 3) + min(10, caps * 3) + min(15, len(content) / 4)
    information = clamp(information)
    if rarity >= 0.75:
        reasons.append("Covers something distinctive in this video")
    if nums >= 2:
        reasons.append("Includes specific figures")

    # completeness
    comp = 50.0
    f_tok = norm(first.split()[0]) if first.split() else ""
    if f_tok in WEAK_STARTS or f_tok in FILLERS:
        comp -= 25
    else:
        comp += 15
    stripped = last.rstrip("\"')]”’")
    if stripped.endswith((".", "!", "…")):
        comp += 30
    elif stripped.endswith("?"):
        comp += 18
    else:
        comp -= 20
        tips.append("The ending does not land on a finished sentence; adjust the end time.")
    if len(last.split()) >= 4:
        comp += 5
    if len(sents) >= 2:
        comp += 5
    low_all = text.lower()
    low_last = last.lower()
    if any(m in low_last for m in CONCLUSION_MARKERS) or any(m in low_all[-160:] for m in CONCLUSION_MARKERS):
        comp += 8
        reasons.append("Ends with a clear conclusion")
    if first.rstrip().endswith("?") and len(sents) >= 2 and not sents[1].text.rstrip().endswith("?"):
        comp += 8
        reasons.append("Asks a question and answers it")
    if len(sents) >= 3:
        third = max(1, len(sents) // 3)
        head = " ".join(x.text.lower() for x in sents[:third + 1]) + " "
        mid = " ".join(x.text.lower() for x in sents[1:-1]) + " "
        tail = " ".join(x.text.lower() for x in sents[-third - 1:]) + " "
        if (any(m in head for m in ARC_SETUP) and any(m in mid for m in ARC_TURN)
                and any(m in tail for m in ARC_PAYOFF)):
            comp += 10
            reasons.append("Has a setup, a turn and a payoff")
    drifted = False
    if ctx is not None and ctx.counts and len(sents) >= 3:
        rest = sum(ctx.counts[ctx.i:ctx.j], Counter())
        drifted = cosine(ctx.counts[ctx.j], rest) < 0.04
    if drifted or low_last.lstrip().startswith(TANGENT_MARKERS):
        comp -= 25
        tips.append("Ends on an unrelated tangent; end it one sentence earlier.")
    if any(p in low_last for p in OUTRO_PHRASES):
        comp -= 30
        tips.append("The clip ends on a sign-off (thanks for watching, subscribe...); end it earlier.")
    elif any(p in low_all for p in OUTRO_PHRASES):
        comp -= 12
        tips.append("Contains call-to-action boilerplate that weakens a standalone clip.")
    completeness = clamp(comp)
    if completeness >= 80:
        reasons.append("Starts and ends on complete sentences")

    # standalone context
    standalone = 85.0
    ft = [norm(t) for t in first.split()[:6]]
    dangling = sum(1 for t in ft if t in {"he", "she", "it", "they", "this", "that", "those", "them"})
    standalone -= dangling * 18
    if f_tok in WEAK_STARTS:
        standalone -= 12
    if len(set(_content_words(toks))) >= 8:
        standalone += 8
    standalone = clamp(standalone)
    if standalone < 55:
        tips.append("The opening refers to something not explained in the clip; it may need earlier context.")

    # duration suitability
    span = max(hi - lo, 1.0)
    sweet = lo + 0.6 * span
    duration = clamp(100 - 100 * abs(dur - sweet) / span, 40, 100)

    # topical coherence: stays on one topic, and begins/ends where the topic shifts
    if ctx is not None:
        bsim, ci, cj = ctx.bsim, ctx.i, ctx.j
        inner = [bsim[k] for k in range(ci + 1, cj + 1)]
        inner_mean = sum(inner) / len(inner) if inner else 0.25
        inner_s = min(1.0, inner_mean / 0.25)
        start_q = 1.0 - min(1.0, bsim[ci] / 0.25)
        end_q = 1.0 - min(1.0, bsim[cj + 1] / 0.25)
        coherence = clamp(100 * (0.5 * inner_s + 0.25 * start_q + 0.25 * end_q))
        if inner_s >= 0.8:
            reasons.append("Stays on a single topic throughout")
        if start_q >= 0.7 and end_q >= 0.7:
            reasons.append("Begins and ends at natural topic boundaries")
        if inner_s < 0.25:
            tips.append("The clip jumps between topics; a shorter range may feel tighter.")
    else:
        coherence = 60

    delivery, rel_db = delivery_score(ctx.energy if ctx else None, start, end)
    if rel_db is not None and rel_db >= 3:
        reasons.append("Energetic delivery (louder than the rest of the video)")
    elif rel_db is not None and rel_db <= -6:
        tips.append("Quiet delivery compared with the rest of the video; it may feel flat.")

    signals = {
        "hook": hook,
        "delivery": delivery,
        "completeness": completeness,
        "coherence": coherence,
        "information": information,
        "clarity": clarity,
        "emotion": emotion,
        "standalone": standalone,
        "pacing": pacing,
        "duration": duration,
    }
    extra = {"speech_ratio": round(speech_ratio, 3), "words_per_second": round(wps, 2),
             "max_pause": round(max_pause, 2), "filler_count": len(fillers)}
    return signals, reasons, tips, extra


def overall_score(signals: dict) -> int:
    return clamp(sum(signals[k] * w for k, w in WEIGHTS.items()))


# --------------------------------------------------------------------------- boundaries

def padded_bounds(sents: list[Sentence], i: int, j: int, video_duration: float | None,
                  max_len: float) -> tuple[float, float]:
    """Add breathing room without ever crossing into neighbouring speech."""
    s_start, s_end = sents[i].start, sents[j].end
    prev_end = sents[i - 1].end if i > 0 else 0.0
    next_start = sents[j + 1].start if j + 1 < len(sents) else (video_duration if video_duration else s_end + 0.5)
    pb = min(0.30, max(0.0, (s_start - prev_end) / 2))
    pa = min(0.40, max(0.0, (next_start - s_end) / 2))
    if i == 0:
        pb = min(0.30, s_start)
    avail = max_len - (s_end - s_start)
    if pb + pa > avail:
        f = max(avail, 0.0) / (pb + pa) if pb + pa else 0.0
        pb, pa = pb * f, pa * f
    start = max(0.0, s_start - pb)
    end = s_end + pa
    if video_duration:
        end = min(end, video_duration)
    return round(start, 3), round(end, 3)


# --------------------------------------------------------------------------- selection

def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(min(a[1] - a[0], b[1] - b[0]), 0.001)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def excerpt(sents: list[Sentence], limit: int = 220) -> str:
    """Extractive description: the most information-dense sentences, verbatim and in original order."""
    scored = sorted(range(len(sents)), key=lambda k: -len(_content_words([norm(t) for t in sents[k].text.split()])))
    chosen, total = [], 0
    for k in scored:
        if total + len(sents[k].text) > limit and chosen:
            continue
        chosen.append(k)
        total += len(sents[k].text) + 1
        if total >= limit * 0.6:
            break
    return " ".join(sents[k].text for k in sorted(chosen))


def extract_hooks(sents: list[Sentence], limit: int = 3) -> list[dict]:
    """Candidate on-screen hooks. Always verbatim sentences from the clip."""
    scored = []
    for idx, s in enumerate(sents):
        sc, notes = hook_signals(s.text)
        words = s.text.split()
        if len(words) < 4:
            continue
        scored.append((sc, idx, s, notes))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for sc, idx, s, notes in scored[:limit]:
        text = s.text
        if len(text) > 110:
            cut = text[:110].rsplit(" ", 1)[0].rstrip(",;: ")
            text = cut + "…"
        low = s.text.lower()
        if s.text.rstrip().endswith("?"):
            style = "question"
        elif re.search(r"\d", s.text):
            style = "educational"
        elif any(w in low.split() for w in ("never", "wrong", "worst", "stop", "mistake", "myth")):
            style = "bold statement"
        elif any(w in low.split() for w in ("secret", "hidden", "truth", "nobody")):
            style = "curiosity"
        else:
            style = "statement"
        out.append({"text": text, "style": style, "score": sc, "source_start": round(s.start, 3),
                    "verbatim": True, "is_opening": idx == 0})
    return out


def keywords(text: str, n: int = 5) -> list[str]:
    toks = [norm(t) for t in text.split()]
    cw = [t for t in _content_words(toks) if len(t) > 3]
    return [w for w, _ in Counter(cw).most_common(n)]


def find_fillers(sents: list[Sentence], start: float, end: float) -> list[dict]:
    out = []
    for s in sents:
        for w in s.words:
            if norm(w.text) in FILLERS and w.start >= start and w.end <= end:
                out.append({"start": round(w.start, 3), "end": round(w.end, 3), "text": w.text})
    return out


def _discover_clips(sentences: list[Sentence], video_duration: float | None = None,
                   min_seconds: float = 30.0, max_seconds: float = 60.0, max_clips: int = 8,
                   silences: list[tuple[float, float]] | None = None, min_score: int = 40,
                   max_overlap: float = 0.3, energy: EnergyProfile | None = None,
                    comp_floor: int = 45, speech_floor: float = 0.55, wps_floor: float = 0.8,
                    low_confidence: bool = False) -> list[dict]:
    if min_seconds <= 0 or max_seconds < min_seconds:
        raise ValueError("invalid duration range")
    n = len(sentences)
    counts = [term_counts(x) for x in sentences]
    bsim = boundary_similarities(counts)
    idf, idf_norm = build_idf(counts)
    cands: list[dict] = []
    for i in range(n):
        if norm(sentences[i].text.split()[0]) in FILLERS:
            continue
        for j in range(i, n):
            raw = sentences[j].end - sentences[i].start
            if raw > max_seconds:
                break
            if raw < min_seconds:
                continue
            start, end = padded_bounds(sentences, i, j, video_duration, max_seconds)
            window = sentences[i:j + 1]
            low_first = window[0].text.lower()
            if any(p in low_first for p in INTRO_PHRASES) or any(
                    p in x.text.lower() for x in window for p in OUTRO_PHRASES):
                continue  # channel intro / call-to-action boilerplate never makes a good standalone clip
            sig, reasons, tips, extra = window_signals(window, start, end, min_seconds, max_seconds, silences,
                                                       Ctx(bsim, i, j, idf, idf_norm, energy, counts))
            if (sig["completeness"] < comp_floor or extra["speech_ratio"] < speech_floor
                    or extra["words_per_second"] < wps_floor):
                continue
            total = overall_score(sig)
            if total < min_score:
                continue
            cands.append({"i": i, "j": j, "start": start, "end": end, "score": total,
                          "signals": sig, "reasons": reasons, "suggestions": tips, "extra": extra})
    cands.sort(key=lambda c: (-c["score"], c["start"]))
    picked: list[dict] = []
    for c in cands:
        if len(picked) >= max_clips:
            break
        if any(_overlap((c["start"], c["end"]), (p["start"], p["end"])) > max_overlap for p in picked):
            continue
        cset = set(sum((counts[k] for k in range(c["i"], c["j"] + 1)), Counter()))
        if any(_jaccard(cset, p["_cset"]) > 0.6 for p in picked):
            continue  # same material said again elsewhere
        c["_cset"] = cset
        picked.append(c)
    picked.sort(key=lambda c: -c["score"])
    clips = []
    for rank, c in enumerate(picked):
        window = sentences[c["i"]:c["j"] + 1]
        text = " ".join(s.text for s in window)
        hooks = extract_hooks(window)
        kws = keywords(text)
        title = hooks[0]["text"] if hooks else (" ".join(kws[:3]).title() or "Untitled clip")
        if len(title) > 70:
            title = title[:70].rsplit(" ", 1)[0] + "…"
        clips.append({
            "index": rank,
            "start": c["start"],
            "end": c["end"],
            "duration": round(c["end"] - c["start"], 3),
            "score": c["score"],
            "score_kind": "estimated content-quality indicator (not a prediction of views)",
            "signals": c["signals"],
            "reasons": c["reasons"] or ["Balanced scores across all content signals"],
            "suggestions": c["suggestions"],
            "metrics": c["extra"],
            "transcript": text,
            "hooks": hooks,
            "description": excerpt(window),
            "title": title,
            "hashtags": ["#" + k for k in kws],
            "fillers": find_fillers(window, c["start"], c["end"]),
            "approximate_timing": any(s.approximate_timing for s in window),
            "low_confidence": low_confidence,
        })
        if low_confidence:
            clips[-1]["suggestions"].insert(0, "Low confidence: no strong moments were found, so these are the best "
                                               "available. Try a different length or review them before posting.")
    return clips


def discover_clips(sentences: list[Sentence], video_duration: float | None = None,
                   min_seconds: float = 30.0, max_seconds: float = 60.0, max_clips: int = 8,
                   silences: list[tuple[float, float]] | None = None, min_score: int = 40,
                   max_overlap: float = 0.3, energy: EnergyProfile | None = None) -> list[dict]:
    """Strict pass first. If nothing qualifies, return the best available clips, clearly flagged low-confidence,
    instead of an empty result."""
    clips = _discover_clips(sentences, video_duration, min_seconds, max_seconds, max_clips, silences, min_score,
                            max_overlap, energy)
    if clips:
        return clips
    return _discover_clips(sentences, video_duration, min_seconds, max_seconds, min(max_clips, 3), silences, 0,
                           max_overlap, energy, comp_floor=25, speech_floor=0.4, wps_floor=0.5, low_confidence=True)


def search_transcript(sentences: list[Sentence], query: str, limit: int = 50) -> list[dict]:
    q = [norm(t) for t in query.split() if norm(t)]
    if not q:
        return []
    out = []
    for s in sentences:
        toks = {norm(t) for t in s.text.split()}
        if all(t in toks or any(t in x for x in toks) for t in q):
            out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text})
            if len(out) >= limit:
                break
    return out


def suggest_alternatives(sentences: list[Sentence], clip_start: float, clip_end: float,
                         video_duration: float | None, lo: float, hi: float,
                         silences: list[tuple[float, float]] | None = None, limit: int = 5,
                         energy: EnergyProfile | None = None) -> list[dict]:
    """Nearby sentence-aligned ranges around an existing clip, ranked by score."""
    inside = [k for k, s in enumerate(sentences) if s.end > clip_start + 0.05 and s.start < clip_end - 0.05]
    if not inside:
        return []
    counts = [term_counts(x) for x in sentences]
    bsim = boundary_similarities(counts)
    idf, idf_norm = build_idf(counts)
    i0, j0 = inside[0], inside[-1]
    n = len(sentences)
    base_sig, _, _, _ = window_signals(sentences[i0:j0 + 1], clip_start, clip_end, lo, hi, silences,
                                     Ctx(bsim, i0, j0, idf, idf_norm, energy, counts))
    base = overall_score(base_sig)
    out = []
    for i in range(max(0, i0 - 3), min(n, i0 + 4)):
        for j in range(max(i, j0 - 3), min(n, j0 + 4)):
            raw = sentences[j].end - sentences[i].start
            if raw < max(3.0, lo * 0.5) or raw > hi * 1.25:
                continue
            start, end = padded_bounds(sentences, i, j, video_duration, hi * 1.25)
            sig, reasons, _tips, extra = window_signals(sentences[i:j + 1], start, end, lo, hi, silences,
                                                   Ctx(bsim, i, j, idf, idf_norm, energy, counts))
            if extra["speech_ratio"] < 0.5:
                continue
            out.append({"start": start, "end": end, "duration": round(end - start, 3), "score": overall_score(sig),
                        "delta": overall_score(sig) - base, "signals": sig, "reasons": reasons[:3],
                        "is_current": (i, j) == (i0, j0)})
    out.sort(key=lambda a: (-a["score"], a["duration"]))
    return out[:limit]
