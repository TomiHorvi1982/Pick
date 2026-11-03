"""The key of a separated song and its stem files — what the songbook needs to know about a
song in Steems (songbook.py). Left over from Loop Studio (removed 3. 11.), whose plans and loops
are gone; the key was always the one thing others asked for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def decode(path: Path, ffmpeg: str, rate: int = 11025) -> np.ndarray:
    """A file as mono float samples (through ffmpeg, so any format Steems holds)."""
    out = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True, timeout=180)
    return np.frombuffer(out.stdout, dtype=np.float32) if out.returncode == 0 else np.zeros(0, np.float32)


def stem_files(stems_dir: Path) -> dict:
    """role → file. Demucs' six by name; anything else (a project of the
    player's own takes) keeps its own name."""
    found = {}
    for f in sorted(stems_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("."):
            found.setdefault(f.stem, f)
    return found


_KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


_QUALITY_TONES = {"maj": (0, 4, 7), "min": (0, 3, 7), "5": (0, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
                  "dim": (0, 3, 6), "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7)}


def _key_scores(hist: np.ndarray) -> np.ndarray:
    """How well a pitch-class histogram fits each of the 24 keys (12 major, then 12 minor)."""
    out = np.zeros(24)
    if hist.sum() <= 0 or np.allclose(hist, hist[0]):
        return out
    for r in range(12):
        rolled = np.roll(hist, -r)
        out[r] = np.corrcoef(rolled, _KS_MAJ)[0, 1]
        out[12 + r] = np.corrcoef(rolled, _KS_MIN)[0, 1]
    return out


def estimate_key(samples: np.ndarray, rate: int = 11025, chords: list = None):
    """The key, from two witnesses: the sound itself (Krumhansl profiles over
    the summed chroma) and the chords already found (their tones, by how long
    each lasts). The sound alone often lands a fifth off — a song in C minor
    spends a long time on G. Where the two agree the answer is trusted; where
    they do not, the key the two fit best together is given, both single
    answers are reported next to it, and the confidence is capped low. The
    confidence is otherwise how far the winner stands above the runner-up.
    None when there is nothing to go on.
    """
    n = 4096
    audio = np.zeros(24)
    if samples is not None and samples.size >= n * 4:
        hop = n // 2
        frames = 1 + (samples.size - n) // hop
        win = np.hanning(n)
        freqs = np.fft.rfftfreq(n, 1 / rate)
        ok = (freqs >= 65) & (freqs <= 2000)
        pcs = (np.round(69 + 12 * np.log2(np.maximum(freqs, 1e-9) / 440)).astype(int) % 12)[ok]
        chroma = np.zeros(12)
        for i in range(0, frames, 2):                          # every other frame is plenty for a key
            mag = np.abs(np.fft.rfft(samples[i * hop:i * hop + n] * win))[ok]
            np.add.at(chroma, pcs, mag ** 2)
        audio = _key_scores(chroma)
    hist = np.zeros(12)
    for c in chords or []:
        root = c.get("root")
        if root not in NOTE_NAMES:
            continue
        dur = max(0.0, float(c.get("end", 0)) - float(c.get("start", 0)))
        for k, t in enumerate(_QUALITY_TONES.get(c.get("quality") or "maj", (0, 4, 7))):
            hist[(NOTE_NAMES.index(root) + t) % 12] += dur * (1.0 if k == 0 else 0.6)
    harm = _key_scores(hist)
    if not audio.any() and not harm.any():
        return None
    name = lambda i: NOTE_NAMES[i % 12] + (" dur" if i < 12 else " moll")
    both = audio.any() and harm.any()
    total = audio + harm if both else (harm if harm.any() else audio)
    order = np.argsort(-total)
    best, second = int(order[0]), int(order[1])
    margin = float(total[best] - total[second]) / (2.0 if both else 1.0)
    agree = (not both) or int(np.argmax(audio)) == int(np.argmax(harm))
    conf = max(0.0, min(1.0, margin * 5))
    if not agree:
        conf = min(conf, 0.35)                                 # two answers: say so
    return {"key": name(best), "confidence": round(conf, 2), "agree": bool(agree),
            "fromSound": name(int(np.argmax(audio))) if audio.any() else None,
            "fromChords": name(int(np.argmax(harm))) if harm.any() else None}
