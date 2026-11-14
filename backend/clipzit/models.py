from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    words: list[Word]
    approximate_timing: bool = False


def segments_from_dicts(items: list[dict]) -> list[Segment]:
    """Parse transcript JSON. Raises ValueError on malformed input."""
    if not isinstance(items, list):
        raise ValueError("transcript must be a list of segments")
    out: list[Segment] = []
    for i, it in enumerate(items):
        try:
            words = [
                Word(float(w["start"]), float(w["end"]), str(w["text"]).strip())
                for w in it.get("words", [])
            ]
            seg = Segment(float(it["start"]), float(it["end"]), str(it["text"]).strip(), words)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed transcript segment #{i}: {exc}") from exc
        if seg.end < seg.start:
            raise ValueError(f"segment #{i} ends before it starts")
        out.append(seg)
    return out
