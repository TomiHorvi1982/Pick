"""The singing trainer's two measurements, both made on this computer.

melody()  — the tune of a song, read off its separated vocals: the notes to
            hit, each with a start, an end and a pitch. Computed once per song
            and kept beside the stems.
compare() — the words of one line as they were sung (whisper's reading of the
            take) against the words of the lyric: right, wrong, left out.

A separated voice is not a clean one: bits of guitar bleed through, backing
vocals double the lead, a held scream has no pitch at all. So a note is only
kept where the pitch is steady and sure, and everything else is left blank
rather than guessed — a blank is not scored, a wrong note would be.
"""
import difflib
import json
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import numpy as np

RATE = 16000
HOP = 320                 # 20 ms between pitch readings
WIN = 800                 # 50 ms compared against itself…
FMIN, FMAX = 70.0, 1000.0  # …for any voice from a low bass to a high scream
YIN_SURE = 0.18           # the less periodic a frame, the higher this number; under it the pitch is sure
YIN_ROUGH = 0.34          # a rough voice (rock is sung that way) is less periodic: up to here its best guess is taken
MIN_NOTE = 0.10           # shorter than this is a blip, not a note
VERSION = 3               # bump to recompute what is cached


def decode(path):
    """Any audio file as mono float32 at RATE."""
    out = subprocess.run([shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg", "-v", "quiet", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32)


def pitch_track(x):
    """YIN on every frame: (midi pitch or nan, how loud) per HOP."""
    tmax, tmin = int(RATE / FMIN), int(RATE / FMAX)
    size = WIN + tmax
    n = max(0, (len(x) - size) // HOP + 1)
    midi = np.full(n, np.nan, dtype=np.float32)
    loud = np.zeros(n, dtype=np.float32)
    if not n:
        return midi, loud
    nfft = 1 << int(np.ceil(np.log2(size + WIN)))
    taus = np.arange(1, tmax + 1)
    for b in range(0, n, 2048):                        # in batches, to keep memory flat
        idx = np.arange(b, min(n, b + 2048))
        fr = x[idx[:, None] * HOP + np.arange(size)[None, :]].astype(np.float64)
        a = fr[:, :WIN]
        loud[idx] = np.sqrt((a * a).mean(axis=1))
        # d(tau) = sum (a[j] - x[j+tau])^2 = |a|^2 + |x[tau:tau+WIN]|^2 - 2 a·x[tau:]
        cross = np.fft.irfft(np.conj(np.fft.rfft(a, nfft)) * np.fft.rfft(fr, nfft), nfft)[:, :tmax + 1]
        sq = np.concatenate([np.zeros((len(idx), 1)), np.cumsum(fr * fr, axis=1)], axis=1)
        energy = sq[:, WIN:WIN + tmax + 1] - sq[:, :tmax + 1]
        d = np.maximum(energy[:, :1] + energy - 2 * cross, 0)[:, 1:]
        cm = d * taus / np.maximum(np.cumsum(d, axis=1), 1e-12)     # YIN's normalised difference
        for k, row in enumerate(cm):
            # The first dip under the line, followed down to its bottom — the
            # shortest period that repeats, not an octave below it.
            under = np.flatnonzero(row[tmin:] < YIN_SURE)
            if len(under):
                t = under[0] + tmin
                while t + 1 < tmax and row[t + 1] < row[t]:
                    t += 1
            else:
                # Nothing sure: a rough voice. Its deepest dip — not the first
                # one, which under a looser line would be an octave too high.
                t = int(np.argmin(row[tmin:])) + tmin
                if row[t] >= YIN_ROUGH:
                    continue
            if 0 < t < tmax - 1:                       # between two readings: a parabola through three
                y0, y1, y2 = row[t - 1], row[t], row[t + 1]
                den = y0 - 2 * y1 + y2
                shift = max(-1.0, min(1.0, 0.5 * (y0 - y2) / den)) if den > 0 else 0.0
            else:
                shift = 0.0
            period = t + 1 + shift
            midi[idx[k]] = 69 + 12 * np.log2(RATE / period / 440.0)
    return midi, loud


def notes_from(midi, loud):
    """Steady stretches of the pitch track as notes [start s, end s, midi]."""
    n = len(midi)
    if not n:
        return []
    gate = max(0.004, float(np.percentile(loud, 95)) * 0.06)       # quieter than this is bleed, not singing
    raw = np.where(loud >= gate, midi, np.nan)
    # A reading an octave off the ones around it is the tracker's slip, not the singer's.
    p = raw.copy()
    for i in range(n):
        if np.isnan(raw[i]):
            continue
        near = raw[max(0, i - 2):i + 3]
        near = near[~np.isnan(near)]
        if len(near) >= 3:
            m = float(np.median(near))
            if abs(abs(raw[i] - m) - 12) < 1.0:
                p[i] = m
    # A hole of a few unsure frames inside one held pitch is still that note.
    i = 0
    while i < n:
        if not np.isnan(p[i]):
            i += 1
            continue
        j = i
        while j < n and np.isnan(p[j]):
            j += 1
        if 0 < i and j < n and j - i <= 3 and abs(p[i - 1] - p[j]) < 1.0 and loud[i:j].min() >= gate * 0.5:
            p[i:j] = np.linspace(p[i - 1], p[j], j - i + 2)[1:-1]
        i = j
    out, start, vals = [], None, []

    def close(end):
        nonlocal start, vals
        if start is not None and (end - start) * HOP / RATE >= MIN_NOTE:
            out.append([round(start * HOP / RATE, 3), round(end * HOP / RATE, 3), round(float(np.median(vals)), 2)])
        start, vals = None, []

    for i in range(n):
        v = p[i]
        if np.isnan(v):
            close(i)
            continue
        if start is not None and abs(v - float(np.median(vals[-10:]))) > 1.0:     # the tune moved on (vibrato stays inside)
            close(i)
        if start is None:
            start = i
        vals.append(float(v))
    close(n)
    # A note an octave away from the tune around it is the tracker's slip (a
    # rough voice reads an octave off as easily as right): a voice does not
    # leap an octave and back within a breath. It is put where its neighbours are.
    if len(out) > 2:
        mids = np.array([o[2] for o in out])
        at = np.array([(o[0] + o[1]) / 2 for o in out])
        w = np.array([o[1] - o[0] for o in out])
        for k in range(len(mids)):
            near = (np.abs(at - at[k]) < 2.5) & (np.arange(len(out)) != k)
            if near.sum() < 2:
                continue
            order = np.argsort(mids[near])
            cum = np.cumsum(w[near][order])
            med = float(mids[near][order][np.searchsorted(cum, cum[-1] / 2)])     # the tune there, by time sung
            for shift in (12.0, -12.0):
                if abs(out[k][2] - med) > 7 and abs(out[k][2] + shift - med) < 5:
                    out[k][2] = round(out[k][2] + shift, 2)
                    break
            else:
                if abs(out[k][2] - med) > 9:              # nowhere near the tune: bleed from a guitar or the bass
                    out[k] = None
        out = [o for o in out if o is not None]
    # Two halves of one held note, split by a single unsure frame, are one note.
    merged = []
    for nt in out:
        if merged and nt[0] - merged[-1][1] <= 0.045 and abs(nt[2] - merged[-1][2]) < 0.5:
            a = merged[-1]
            w0, w1 = a[1] - a[0], nt[1] - nt[0]
            a[2] = round((a[2] * w0 + nt[2] * w1) / (w0 + w1), 2)
            a[1] = nt[1]
        else:
            merged.append(nt)
    return merged


def melody(vocals_path):
    x = decode(vocals_path)
    midi, loud = pitch_track(x)
    notes = notes_from(midi, loud)
    return {"version": VERSION, "duration": round(len(x) / RATE, 2), "notes": notes}


def melody_cached(stems_dir, vocals_path, refresh=False, only_cached=False):
    """The song's melody from melody.json beside its stems, computed when missing
    (only_cached: not computed — None when it is not there yet)."""
    cache = Path(stems_dir) / "melody.json"
    if not refresh:
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
            if got.get("version") == VERSION and got.get("source_mtime") == Path(vocals_path).stat().st_mtime:
                return got
        except (OSError, ValueError):
            pass
    if only_cached:
        return None
    got = melody(vocals_path)
    got["source_mtime"] = Path(vocals_path).stat().st_mtime
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(got, separators=(",", ":")), encoding="utf-8")
    tmp.replace(cache)
    return got


# ---- words

def _plain(word):
    t = unicodedata.normalize("NFKD", word or "").encode("ascii", "ignore").decode().lower()
    return "".join(ch for ch in t if ch.isalnum())


def words(text):
    """The words of a line; what is in brackets — (yeah), [chorus] — is not sung by you."""
    out, depth = [], 0
    for raw in (text or "").replace("’", "'").split():
        opens, closes = raw.count("(") + raw.count("["), raw.count(")") + raw.count("]")
        inside = depth > 0 or opens > 0
        depth = max(0, depth + opens - closes)
        if not inside and _plain(raw):
            out.append(raw.strip(".,!?;:\"“”„…-–—"))
    return out


def _same(a, b):
    """Close enough to be the same word sung: whisper writes "gonna" for "going to" and drops an -ing."""
    if a == b:
        return True
    if min(len(a), len(b)) <= 2:
        return False
    if a.startswith(b) or b.startswith(a):            # "planned" heard as "plan"
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


# Said aloud, these are one word: whisper writes whichever spelling the
# sentence suggests, and neither is a slip of the tongue.
_HOMOPHONES = [set(g.split()) for g in (
    "to too two 2", "for four 4", "one won 1", "ate eight 8", "there their theyre", "your youre yore", "its it's".replace("'", ""),
    "no know", "new knew", "right write rite", "by buy bye", "here hear", "be bee", "see sea", "would wood", "our hour",
    "son sun", "whole hole", "piece peace", "wait weight", "way weigh", "weak week", "break brake", "die dye", "i eye",
    "in inn", "not knot", "night knight", "made maid", "mail male", "meet meat", "pain pane", "plain plane", "road rode",
    "role roll", "sail sale", "seen scene", "so sew sow", "soul sole", "steal steel", "tail tale", "threw through thru",
    "waste waist", "wear where", "whose whos", "theres theirs", "heal heel", "hole whole", "aloud allowed", "blue blew",
    "flower flour", "great grate", "higher hire", "lead led", "passed past", "pray prey", "rain reign rein", "raise rays",
    "read red", "sight site", "some sum", "ok okay", "alright allright", "till til until", "cause cuz coz because",
    "yeah yea ya", "gonna gunna", "wanna wonna", "em them", "oh o", "color colour", "gray grey", "favor favour",
    "honor honour", "center centre", "realize realise", "tonight tonite", "though tho", "you u", "are r", "love luv")]


def _same_strict(a, b):
    """The same word, said right: its letters, or only another way of writing
    the same sound — never a word that merely looks alike ("fate" for "hate")."""
    if a == b:
        return True
    if any(a in g and b in g for g in _HOMOPHONES):
        return True
    # "runnin" as the lyric writes it, "running" as whisper does — and the plural's or possessive's s written apart
    drop = lambda w: w[:-1] if w.endswith("ing") else w
    return drop(a) == drop(b) and (a.endswith("in") or b.endswith("in"))


def compare(expected, heard, strict=False):
    """Each word of the lyric line: {"w": word, "s": "ok" | "bad" | "miss"}, and the share that was right.
    `strict` is for pronunciation: a word passes only when it was heard as
    itself (or as a word that sounds the same) — one that resembles it does not."""
    same = _same_strict if strict else _same
    want = words(expected)
    got = [_plain(w) for w in words(heard)]
    keys = [_plain(w) for w in want]
    state = ["miss"] * len(want)
    sm = difflib.SequenceMatcher(None, keys, got, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                state[i] = "ok"
        elif tag == "replace":
            # "icebox" heard as "ice box": the same letters, cut differently
            if (lambda x, y: x == y if strict else _same(x, y))("".join(keys[i1:i2]), "".join(got[j1:j2])):
                for i in range(i1, i2):
                    state[i] = "ok"
                continue
            for k, i in enumerate(range(i1, i2)):
                j = j1 + k
                state[i] = "ok" if j < j2 and same(keys[i], got[j]) else ("bad" if j < j2 else "miss")
    ok = state.count("ok")
    return {"words": [{"w": w, "s": s} for w, s in zip(want, state)],
            "ok": ok, "total": len(want), "heard": " ".join(words(heard))}


# ---- history: one line per finished take, beside the stems

def history(stems_dir):
    try:
        rows = json.loads((Path(stems_dir) / "sing-history.json").read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def add_history(stems_dir, row, keep=200):
    rows = (history(stems_dir) + [row])[-keep:]
    path = Path(stems_dir) / "sing-history.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return rows


# ---- what was heard of each lyric line the last time it was sung — kept beside
# the stems, so a take can be gone through afterwards, word by word
def sung_words(stems_dir):
    try:
        rows = json.loads((Path(stems_dir) / "sing-words.json").read_text(encoding="utf-8"))
        return rows if isinstance(rows, dict) else {}
    except (OSError, ValueError):
        return {}


def keep_sung(stems_dir, key, row, keep=600):
    rows = sung_words(stems_dir)
    rows.pop(key, None)
    rows[key] = row                                   # the newest last
    for old in list(rows)[:-keep]:
        del rows[old]
    path = Path(stems_dir) / "sing-words.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return rows


# ---- the words that would not come out right: one list for every song, so
# they can be practised together later

def hard_words(path):
    """{"en|word": {"word", "lang", "miss", "ok", "heard", "song", "at", "done"}}"""
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def hard_word(path, word, lang, ok, heard="", song="", learned=False, keep=2000):
    """One try at one word. A miss puts it on the list (again); getting it
    right in the practice of the list itself (`learned`) ticks it off."""
    word = (word or "").strip().strip(".,;:!?\"“”„()[]{}…–—-").lower()     # as it is written: "can't" stays "can't"
    if not _plain(word):
        return hard_words(path)
    got, key = hard_words(path), f"{lang}|{_plain(word)}"
    row = got.pop(key, None)
    if row is None:
        if ok:
            return got                                   # never was a problem
        row = {"word": word, "lang": lang, "miss": 0, "ok": 0, "done": False}
    if ok:
        row["ok"] += 1
        row["done"] = row["done"] or bool(learned)
    else:
        row["miss"] += 1
        row["done"] = False
        row["heard"], row["at"] = heard[:80], int(time.time())
        if song:
            row["song"] = song[:200]
    got[key] = row                                       # the newest last
    for k in list(got)[:max(0, len(got) - keep)]:
        del got[k]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return got
