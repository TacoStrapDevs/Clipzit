import subprocess
from pathlib import Path

from clipzit.models import Segment, Word


def make_video(path: Path, seconds: int = 20, w: int = 1280, h: int = 720, silence=(None, None), audio=True,
               pattern: str = "testsrc") -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{pattern}=size={w}x{h}:rate=25:duration={seconds}"]
    if audio:
        expr = "1"
        if silence[0] is not None:
            expr = f"if(between(t,{silence[0]},{silence[1]}),0,1)"
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-af", f"volume='{expr}':eval=frame", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def speech(text_sentences, start=0.5, wps=2.6, gap=0.5):
    """Build timestamped segments: one segment per sentence, evenly timed words."""
    t = start
    segs = []
    for sent in text_sentences:
        words = sent.split()
        ws = []
        for wd in words:
            ws.append(Word(round(t, 3), round(t + 1 / wps - 0.05, 3), wd))
            t += 1 / wps
        segs.append(Segment(ws[0].start, ws[-1].end, sent, ws))
        t += gap
    return segs


SAMPLE = [
    "Why do most people quit learning guitar after three months?",
    "The biggest mistake is practicing the wrong things for hours every single day.",
    "I spent two years playing scales and never learned a real song.",
    "Then my teacher told me something that changed everything about how I practice.",
    "Learn one song you love and break it into four small pieces.",
    "Practice each piece for ten minutes and you will see progress within a week.",
    "It sounds simple but nobody actually does it because it feels too easy.",
    "That is the secret, and it works for every instrument I have ever tried.",
    "So stop collecting exercises and start playing music you care about.",
    "Last year I taught my brother this method and he played his first full song in nine days.",
    "He cried when he finished because he never thought he could do it.",
    "Anyone can get there if they follow these steps and stay patient.",
    "Thanks for watching and see you in the next one.",
]
