"""A song's text heard off its separated vocals — for the songs no lyrics site has.

heard()  — whisper (whisper-cli, on this computer) writes down what is sung,
           line by line, each line with the time it starts. The vocals are cut
           into the stretches where someone sings first: a whole song of
           silence and guitar bleed makes whisper invent things, a phrase at a
           time it does not. Every phrase is read in the song's one language —
           the one most phrases were heard in — since a short phrase on its own
           is easily taken for Urdu.
timed()  — a text that is known but has no times (LRCLib often has only the
           words) laid onto what was heard: the right words, at the times they
           are sung.

Nothing leaves the computer; the result is kept beside the stems (heard.json),
so a song is listened to once.
"""
import difflib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from collections import Counter
from pathlib import Path

import numpy as np

RATE = 16000
HOP = 320                  # 20 ms
VERSION = 1
FILE = "heard.json"
WHISPER_BIN = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = Path.home() / ".local/share/spotdl-helper/voice/ggml-large-v3-turbo-q5_0.bin"

# what whisper says to silence and applause: it learnt from subtitled videos
JUNK = re.compile(r"titulky|subtitles|thanks for watching|děkuj(i|eme) za pozornost|amara\.org|www\.|\.com|"
                  r"♪|music\b|hudba\b|potlesk|applause", re.I)


def ready():
    return Path(WHISPER_BIN).exists() and WHISPER_MODEL.exists()


def decode(path):
    out = subprocess.run([shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg", "-v", "quiet", "-nostdin", "-i", str(path),
                          "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True, check=True, timeout=300).stdout
    return np.frombuffer(out, dtype=np.float32)


def phrases(x, gap=0.6, longest=20.0, shortest=0.4):
    """[(start s, end s)] where someone sings: loud against the song's own loudest
    parts, short pauses bridged, never longer than whisper's half-minute."""
    n = len(x) // HOP
    if n == 0:
        return []
    rms = np.sqrt(np.mean(np.square(x[:n * HOP].reshape(n, HOP)), axis=1))
    db = 20 * np.log10(rms + 1e-9)
    on = db > max(float(np.percentile(db, 95)) - 30, -50)
    runs, i = [], 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    out = []
    for a, b in runs:
        if out and (a - out[-1][1]) * HOP / RATE < gap and (b - out[-1][0]) * HOP / RATE < longest:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a * HOP / RATE, b * HOP / RATE) for a, b in out if (b - a) * HOP / RATE >= shortest]


def _write(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def _whisper(files, lang, prompt="", on_done=None):
    """{file: (language, [(from s, to s, text)])} — one whisper for all of them, the model loaded once."""
    cmd = [WHISPER_BIN, "-m", str(WHISPER_MODEL), "-l", lang, "-np", "-ojf", "-sns", "-t", "4"]
    if prompt:
        cmd += ["--prompt", prompt[:600]]
    for f in files:
        cmd += ["-f", str(f)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    done = 0
    for line in proc.stderr:
        if "output_json: saving" in line:
            done += 1
            if on_done:
                on_done(done)
    proc.wait()
    out = {}
    for f in files:
        j = Path(str(f) + ".json")
        try:
            d = json.loads(j.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        rows = [(s["offsets"]["from"] / 1000, s["offsets"]["to"] / 1000, (s.get("text") or "").strip())
                for s in d.get("transcription", [])]
        out[str(f)] = ((d.get("result") or {}).get("language") or lang, rows)
    return out


def _worth(text):
    letters = sum(c.isalpha() for c in text)
    return letters >= 2 and not JUNK.search(text)


def heard(vocals, lang="auto", prompt="", progress=None):
    """{"lang", "lines": [{"timeInMs", "text"}], "phrases": n} — what is sung, as whisper hears it."""
    x = decode(vocals)
    spans = phrases(x)
    if not spans:
        return {"lang": lang, "lines": [], "phrases": 0}
    tmp = Path(tempfile.mkdtemp(prefix="pick-hear-"))
    try:
        files, pad = [], 0.2
        for k, (a, b) in enumerate(spans):
            a0, b0 = max(0, int((a - pad) * RATE)), min(len(x), int((b + pad) * RATE))
            f = tmp / f"p{k:03d}.wav"
            # half a second of silence either side: whisper wants a little room
            _write(f, np.concatenate([np.zeros(RATE // 2, np.float32), x[a0:b0], np.zeros(RATE // 2, np.float32)]))
            files.append((f, a0 / RATE - 0.5))
        total = len(files)
        tell = (lambda n, of=total: progress(n, of)) if progress else None
        got = _whisper([f for f, _ in files], lang, prompt, tell)
        if lang == "auto":
            # the song's language: what most of its sung seconds were heard as
            weight = Counter()
            for (f, _), (a, b) in zip(files, spans):
                g = got.get(str(f))
                if g and any(_worth(t) for _, _, t in g[1]):
                    weight[g[0]] += b - a
            lang = weight.most_common(1)[0][0] if weight else "en"
            again = [f for f, _ in files if got.get(str(f)) and got[str(f)][0] != lang]
            if again:
                got.update(_whisper(again, lang, prompt))
        lines = []
        for f, off in files:
            for a, b, text in (got.get(str(f)) or ("", []))[1]:
                text = re.sub(r"\s+", " ", text).strip(" -")
                if not _worth(text):
                    continue
                if lines and _fold(lines[-1]["text"]) == _fold(text) and len(lines) >= 3 and _fold(lines[-3]["text"]) == _fold(text):
                    continue                          # whisper stuck on one line: a fourth time in a row is not sung
                for t, part in _split_long(off + a, off + b, text):
                    lines.append({"timeInMs": int(round(max(0.0, t) * 1000)), "text": part})
        lines.sort(key=lambda r: r["timeInMs"])
        return {"lang": lang, "lines": lines, "phrases": total}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _split_long(a, b, text, most=9):
    """A line of many words in two or three, at its commas if it has any, each with its share of the time."""
    words = text.split()
    if len(words) <= most:
        return [(a, text)]
    parts = [p.strip() for p in re.split(r"(?<=[,;])\s+", text) if p.strip()]
    if len(parts) < 2 or max(len(p.split()) for p in parts) > most * 1.5:
        k = -(-len(words) // most)
        size = -(-len(words) // k)
        parts = [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    out, used, chars = [], 0, max(1, sum(len(p) for p in parts))
    for p in parts:
        out.append((a + (b - a) * used / chars, p))
        used += len(p)
    return out


def _fold(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


def timed(plain, lines):
    """The known words (a text without times) at the times what was heard says they are sung:
    each line starts where its first word was heard; a line not heard at all is put
    between its neighbours."""
    rows = [r.strip() for r in (plain or "").splitlines()]
    rows = [r for r in rows if r and not re.fullmatch(r"[\[(].*[\])]", r)]       # "[Chorus]", "(2x)"
    if not rows or not lines:
        return []
    # the heard words, each with a time: a line's words spread over it until the next line
    hw, ht = [], []
    for i, ln in enumerate(lines):
        t0 = ln["timeInMs"]
        t1 = lines[i + 1]["timeInMs"] if i + 1 < len(lines) else t0 + 3000
        ws = [_fold(w) for w in ln["text"].split() if _fold(w)]
        for k, w in enumerate(ws):
            hw.append(w)
            ht.append(t0 + (min(t1 - t0, 4000)) * k / max(1, len(ws)))
    kw, owner = [], []
    for i, r in enumerate(rows):
        for w in r.split():
            if _fold(w):
                kw.append(_fold(w))
                owner.append(i)
    first = {}
    for j, o in enumerate(owner):
        first.setdefault(o, j)
    starts = [None] * len(rows)
    sm = difflib.SequenceMatcher(None, kw, hw, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            r = owner[blk.a + k]
            if starts[r] is None:
                # the line's first word heard: its time, less a moment for each word before it in the line
                starts[r] = ht[blk.b + k] - 450 * (blk.a + k - first[r])
    known = [i for i, s in enumerate(starts) if s is not None]
    if len(known) < max(2, len(rows) // 4):
        return []                                   # too little of it was heard to trust the times
    for i in range(len(rows)):
        if starts[i] is None:
            before = max((j for j in known if j < i), default=None)
            after = min((j for j in known if j > i), default=None)
            if before is not None and after is not None:
                starts[i] = starts[before] + (starts[after] - starts[before]) * (i - before) / (after - before)
            elif before is not None:
                starts[i] = starts[before] + 3000 * (i - before)
            else:
                starts[i] = max(0, starts[after] - 3000 * (after - i))
    out, last = [], -1
    for i, r in enumerate(rows):
        t = int(max(starts[i], last + 300))
        out.append({"timeInMs": t, "text": r})
        last = t
    return out


def kept(d):
    try:
        got = json.loads((Path(d) / FILE).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) and got.get("version") == VERSION and got.get("lines") else None
    except (OSError, ValueError):
        return None


def keep(d, got):
    f = Path(d) / FILE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": VERSION, **got}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
