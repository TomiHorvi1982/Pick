"""Words said, on this computer: whisper (whisper-cli) writes down a stretch of speech, and
best_match finds which of a list of names was meant — for the pronunciation trainer, a spoken
answer, a playlist or song asked for by name. Nothing leaves the computer.

What is left of Pick's voice commands (removed 3. 11.: Vosk listening for "pick …").
"""
import difflib
import os
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path

RATE = 16000

WHISPER_BIN = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = Path(os.environ.get(
    "PICK_WHISPER_MODEL", Path.home() / ".local/share/spotdl-helper/voice/ggml-large-v3-turbo-q5_0.bin"))


def names_available():
    if not Path(WHISPER_BIN).exists():
        return "Chybí whisper-cli (brew install whisper-cpp)."
    if not WHISPER_MODEL.exists():
        return f"Chybí model pro jména ({WHISPER_MODEL})."
    return None


def transcribe(pcm, hint="", lang="en", prompt=None):
    """What was said in this stretch of 16 kHz mono s16le, as text. `prompt`
    replaces the command-shaped one (a sung line is no command)."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "u.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes(b"\0" * RATE + pcm + b"\0" * RATE)      # whisper wants a little room
        cmd = [WHISPER_BIN, "-m", str(WHISPER_MODEL), "-l", lang, "-np", "-nt", "-f", str(wav)]
        if prompt is None:
            # Whisper leans on what it has "just heard": sentences of the very shape
            # we expect, and a few of the library's bands for their spelling.
            prompt = ("Pick song Wait and Bleed. Pick band Sepultura. Pick practice Duality. Pick play Roots. "
                      "Pick find playlist Metal. " + hint)[:400]
        if prompt:
            cmd += ["--prompt", prompt]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return " ".join(out.stdout.split())


def _fold(text):
    """Letters only, the way they sound: no accents, no case, k for c, v for w."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = "".join(ch if ch.isalnum() else " " for ch in t)
    t = t.replace("ck", "k").replace("c", "k").replace("w", "v").replace("y", "i").replace("ph", "f")
    out = []
    for ch in " ".join(t.split()):
        if not out or out[-1] != ch or ch == " ":
            out.append(ch)
    return "".join(out)


def best_match(query, names):
    """Index of the name meant, and how sure (0–1); (None, 0) when nothing is close."""
    q = _fold(query)
    if len(q) < 2:
        return None, 0.0
    best, score = None, 0.0
    qt = set(q.split())
    for i, name in enumerate(names):
        n = _fold(name)
        if not n:
            continue
        r = difflib.SequenceMatcher(None, q, n).ratio()
        nt = set(n.split())
        both = len(qt & nt) / max(len(qt), len(nt))          # the same words, in any order
        sc = max(r, both)
        if sc > score:
            best, score = i, sc
    return (best, score) if score >= 0.62 else (None, score)
