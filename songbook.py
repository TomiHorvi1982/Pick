"""Zpěvník pro Skladatele — what the songs in Steems are made of, as material for new ones.

For each separated song, written once beside its stems (Stems/<song>/songbook.json):

  sound   how it sounds, in the words ACE-Step understands: the band's genres, tempo, key,
          tuning, the guitars' tone, how the drums move — for the whole song (a caption)
          and for each of its sections (intro, verse, chorus…: who plays, how loud, how busy)
  shape   how its text is built, section by section: how many lines, how many syllables
          a line, the rhyme scheme, which part comes back word for word. Never the words
          themselves — the song's text stays where the karaoke bar keeps it; the Skladatel's
          lyricist writes new words of its own into a shape taken from here.

Beside them the player's own texts (songbook-mine.json), whole and tagged, by band and
section, to put into a new song as they are or in pieces.

A batch (a hundred songs across bands, say) runs in the background: the sections are
found first where a song has none yet (analyze.py, half a minute a song), then the
stems are listened through. It waits while a separation is running.
"""
import json
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import loopstudio

VERSION = 1
MINE = Path.home() / ".local" / "share" / "spotdl-helper" / "songbook-mine.json"
BANDS = Path.home() / ".local" / "share" / "spotdl-helper" / "songbook-bands.json"
QUEUE = Path.home() / ".local" / "share" / "spotdl-helper" / "songbook-queue.json"   # what is left of a batch: it goes on after a restart
router = APIRouter()

# set by the server
stems_root = lambda: Path(".")                       # noqa: E731
ffmpeg = lambda: "ffmpeg"                           # noqa: E731
analysis_for = lambda name: None                     # noqa: E731 — the song's sections and chords, found if missing
lyrics_for = lambda d: None                          # noqa: E731 — the text kept for the karaoke bar
busy = lambda: False                                 # noqa: E731 — a separation is running
genres_lookup = lambda band: []                      # noqa: E731

KIND_EN = {"intro": "Intro", "verse": "Verse", "prechorus": "Pre-Chorus", "chorus": "Chorus", "bridge": "Bridge",
           "inst": "Instrumental", "solo": "Guitar Solo", "break": "Breakdown", "outro": "Outro", "part": "Interlude", "song": "Song",
           "start": "Intro", "end": "Outro"}
STEM_EN = {"vocals": "vocals", "drums": "drums", "bass": "bass", "guitar": "guitars", "guitar_rhythm": "rhythm guitar",
           "guitar_solo": "lead guitar", "piano": "keys", "other": "atmospheric layers"}


# ------------------------------------------------------------------ the text's shape
_VOWELS = "aeiouyáéěíóúůýäöüàèìòùâêîôûåæøœ"


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def syllables(line: str) -> int:
    """Vowel groups, near enough for Czech and English (a silent final e is not one)."""
    words = re.findall(r"[^\W\d_]+", (line or "").lower())
    n = 0
    for w in words:
        k = len(re.findall(rf"[{_VOWELS}]+", w))
        if w.endswith("e") and k > 1 and not w.endswith(("le", "ee")) and not re.search(r"[ěé]$", w):
            k -= 1
        if k == 0 and re.search(r"[rl]", w):              # Czech "vlk", "krk": the r or l carries it
            k = 1
        n += max(k, 1 if w else 0)
    return n


def rhyme_key(line: str) -> str:
    w = re.findall(r"[^\W\d_]+", _fold(line))
    if not w:
        return ""
    last = w[-1]
    if len(last) > 3 and last.endswith("e") and last[-2] not in "aeiouy":
        last = last[:-1]                                   # stone, bone: the e is not heard
    m = re.search(r"[aeiouy][^aeiouy]*$", last)
    return (last[m.start():] if m else last[-2:])[-3:]


def scheme(lines: list) -> str:
    """AABB, ABAB… — X for a line that rhymes with nothing near it."""
    keys, letters, out = [rhyme_key(x) for x in lines], {}, []
    for i, k in enumerate(keys):
        partner = any(k and keys[j] == k for j in range(max(0, i - 3), min(len(keys), i + 4)) if j != i)
        if not partner:
            out.append("X")
            continue
        if k not in letters:
            letters[k] = chr(ord("A") + len(letters) % 26)
        out.append(letters[k])
    return "".join(out)


def _lang(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"[ěščřžůťďň]", low):
        return "cs"
    if re.search(r"[ñ¿¡]", low):
        return "es"
    if re.search(r"[äöüß]", low):
        return "de"
    if re.search(r"[ãõç]", low):
        return "pt"
    return "en"


def shape(lyrics: Optional[dict], sections: list) -> Optional[dict]:
    """How the text is built — never the text. Timed lines fall into the song's sections;
    an untimed text is split at its blank lines. → {lang, synced, sections: [...]}"""
    if not lyrics:
        return None
    rows = []
    if lyrics.get("synced") and lyrics.get("lines") and sections:
        lines = [(float(x.get("timeInMs") or 0) / 1000, (x.get("text") or "").strip()) for x in lyrics["lines"]]
        lines = [(t, s) for t, s in lines if s]
        for i, sec in enumerate(sections):
            mine = [s for t, s in lines if sec["start"] - 0.4 <= t < sec["end"] - 0.4]
            rows.append({"i": i, "lines": mine})
        synced = True
        all_text = " ".join(s for _, s in lines)
    else:
        text = lyrics.get("plain") or "\n".join((x.get("text") or "") for x in lyrics.get("lines") or [])
        blocks = [[ln.strip() for ln in b.splitlines() if ln.strip()] for b in re.split(r"\n\s*\n", text or "")]
        rows = [{"i": None, "lines": b} for b in blocks if b]
        synced = False
        all_text = text
    if not any(r["lines"] for r in rows):
        return None
    seen, out = {}, []
    for r in rows:
        ls = r["lines"]
        sig = "|".join(_fold(x) for x in ls)
        again = seen.get(sig) if ls else None
        if ls and again is None:
            seen[sig] = len(out)
        sy = [syllables(x) for x in ls]
        out.append({"section": r["i"], "lines": len(ls), "syllables": sy,
                    "avg": round(sum(sy) / len(sy), 1) if sy else 0, "rhyme": scheme(ls) if ls else "",
                    "repeat_of": again, "words": sum(len(x.split()) for x in ls)})
    if not synced:                                       # untimed: the stanza that comes back is the chorus
        for k, r in enumerate(out):
            r["kind"] = "chorus" if r["repeat_of"] is not None or any(x["repeat_of"] == k for x in out) else "verse"
    return {"lang": _lang(all_text), "synced": synced, "sections": out}


# ------------------------------------------------------------------ the sound
def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def _hits_per_sec(x: np.ndarray, rate: int) -> float:
    """Drum hits a second, from the rises of a 10 ms envelope."""
    hop = rate // 100
    if x.size < hop * 20:
        return 0.0
    env = np.sqrt(np.mean(x[: x.size // hop * hop].reshape(-1, hop) ** 2, axis=1))
    rise = np.diff(env)
    thr = max(float(np.percentile(rise, 97)) * 0.35, float(env.max()) * 0.04)
    peaks = np.where((rise[1:-1] > thr) & (rise[1:-1] >= rise[:-2]) & (rise[1:-1] >= rise[2:]))[0]
    if not peaks.size:
        return 0.0
    keep = [peaks[0]]
    for p in peaks[1:]:
        if p - keep[-1] >= 5:                             # 50 ms apart at least
            keep.append(p)
    return len(keep) / (x.size / rate)


def _brightness(x: np.ndarray, rate: int) -> float:
    """Share of the energy above 2 kHz — a bright, cutting tone against a dark, heavy one."""
    n = 4096
    if x.size < n * 4:
        return 0.0
    hi = lo = 0.0
    f = np.fft.rfftfreq(n, 1 / rate)
    for i in range(0, x.size - n, n * 8):
        m = np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n))) ** 2
        hi += float(m[f >= 2000].sum()); lo += float(m[(f >= 80) & (f < 2000)].sum())
    return hi / (hi + lo) if hi + lo else 0.0


_FLAT = {"D#": "Eb", "G#": "Ab", "A#": "Bb"}


def _key_name(k):
    """'D# dur' → 'Eb major' — the way the Skladatel's key list writes it."""
    if not k:
        return None
    note, _, mode = k.partition(" ")
    return f"{_FLAT.get(note, note)} {'minor' if mode.startswith('moll') else 'major'}"


def tuning_words(tuning) -> list:
    if not tuning or not isinstance(tuning, list):
        return []
    low = min(tuning)
    s = sorted(tuning)
    drop = len(s) > 1 and s[1] - s[0] == 7
    names = {40: "E", 39: "Eb", 38: "D", 37: "C#", 36: "C", 35: "B", 34: "Bb", 33: "A"}
    if drop and low < 40:
        return [f"drop {names.get(low, '')} tuning".replace("  ", " "), "downtuned guitars"]
    if low < 40:
        return ["downtuned guitars", f"{names.get(low, 'low')} standard tuning"]
    return []


def _energy_word(level: float, lo: float, top: float) -> str:
    r = (level - lo) / (top - lo) if top > lo else 1.0      # against the song's own quietest and loudest part
    return "peak energy" if r > 0.8 else "full energy" if r > 0.5 else "medium energy" if r > 0.25 else "quiet, sparse"


def _drum_word(hps: float, bpm: float) -> str:
    if hps <= 0.3:
        return ""
    per_beat = hps / (bpm / 60) if bpm else 0
    if per_beat >= 3.2 and bpm >= 120:
        return "blast beats and double bass drumming"
    if per_beat >= 2.2:
        return "fast double kick drumming"
    if per_beat >= 1.4:
        return "driving drums"
    if per_beat >= 0.8:
        return "steady groove"
    return "half-time drums"


def sound(d: Path, analysis: dict, genres: list) -> dict:
    """The song as ACE-Step would be told it: a caption and a line per section."""
    grid = _json(d / "beats.json") or {}
    bpm = float(grid.get("bpm") or 0)
    tuning = (_json(d / "tuning.json") or {}).get("tuning")
    files = loopstudio.stem_files(d)
    rate = 8000
    audio = {k: loopstudio.decode(f, ffmpeg(), rate) for k, f in files.items()
             if k in STEM_EN}
    secs = [s for s in (analysis or {}).get("sections") or [] if s.get("end", 0) > s.get("start", 0)]
    length = max((a.size / rate for a in audio.values()), default=0)
    if not secs and length:
        secs = [{"start": 0.0, "end": length, "kind": "song", "name": "Celá píseň"}]
    key = None
    kf = next((files[k] for k in ("bass", "guitar", "guitar_rhythm") if k in files), None)
    if kf is not None:
        k = loopstudio.estimate_key(loopstudio.decode(kf, ffmpeg(), 11025), 11025, (analysis or {}).get("chords"))
        key = _key_name(k and k.get("key"))
    guitar = next((audio[g] for g in ("guitar", "guitar_rhythm", "guitar_solo") if g in audio and audio[g].size), None)
    bright = _brightness(guitar, rate) if guitar is not None else 0
    rows, totals = [], []
    tops = {k: max((_rms(a[int(s["start"] * rate):int(s["end"] * rate)]) for s in secs), default=0) for k, a in audio.items()}
    for s in secs:
        lv = {k: _rms(a[int(s["start"] * rate):int(s["end"] * rate)]) for k, a in audio.items()}
        # a separated voice carries the band's bleed: it sings when it is well up on its own loudest part
        plays = [k for k, v in lv.items() if tops[k] and v > max(tops[k] * (0.4 if k in ("vocals", "other") else 0.18), 0.004)]
        dr = audio.get("drums")
        hps = _hits_per_sec(dr[int(s["start"] * rate):int(s["end"] * rate)], rate) if dr is not None else 0
        totals.append(sum(lv.values()))
        rows.append({"name": s.get("name") or "", "kind": s.get("kind") or "part", "start": round(float(s["start"]), 2),
                     "end": round(float(s["end"]), 2), "plays": plays, "hits": round(hps, 2), "level": sum(lv.values())})
    top, lo = max(totals, default=0), min(totals, default=0)
    for r in rows:
        tag = KIND_EN.get(r["kind"], "Interlude")
        inst = [STEM_EN[k] for k in ("guitar", "guitar_rhythm", "guitar_solo", "bass", "drums", "piano", "other", "vocals") if k in r["plays"]]
        words = [", ".join(inst) if inst else "near silence"]
        if "vocals" not in r["plays"]:
            words.append("instrumental")
        if "drums" in r["plays"]:
            dw = _drum_word(r["hits"], bpm)
            if dw:
                words.append(dw)
        words.append(_energy_word(r.pop("level"), lo, top))
        r["tag"] = tag
        r["desc"] = ", ".join(w for w in words if w)
    # the whole song
    cap = list(dict.fromkeys(g.lower() for g in genres[:3]))
    cap += tuning_words(tuning)
    if guitar is not None:
        cap.append("dark, heavy guitar tone" if bright < 0.1 else "bright, cutting guitar tone" if bright > 0.26 else "crunchy mid-range guitars")
    hps_all = [r["hits"] for r in rows if "drums" in r["plays"]]
    if hps_all:
        dw = _drum_word(float(np.median(hps_all)), bpm)
        if dw:
            cap.append(dw)
    if any("vocals" in r["plays"] for r in rows):
        cap.append("powerful lead vocals")
    if any(r["kind"] == "solo" or ("guitar_solo" in r["plays"] and "vocals" not in r["plays"]) for r in rows):
        cap.append("guitar solo")
    if len(totals) > 2 and totals[0] < top * 0.5:
        cap.append("builds from a quiet intro")
    cap.append("live band recording")
    return {"caption": ", ".join(dict.fromkeys(cap)), "bpm": round(bpm) if bpm else None, "key": key,
            "timesig": str(grid.get("beats_per_bar") or "") or None, "tuning": tuning, "genres": genres[:5],
            "length": round(length, 1), "sections": rows}


def _json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ one song, the batch
def meta_of(d: Path) -> dict:
    m = _json(d / "meta.json") or {}
    return {"artist": m.get("artist") or "", "title": m.get("title") or d.name, "album": m.get("album") or ""}


def band_genres(band: str) -> list:
    cache = _json(BANDS) or {}
    key = _fold(band).strip()
    if key in cache and time.time() - cache[key].get("at", 0) < 90 * 86400:
        return cache[key]["genres"]
    try:
        got = [g for g in genres_lookup(band) if g]
    except Exception:                                    # noqa: BLE001 — offline: no genres this time, asked again later
        return []
    cache[key] = {"genres": got, "at": time.time()}
    BANDS.parent.mkdir(parents=True, exist_ok=True)
    BANDS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return got


def analyse(name: str) -> dict:
    d = stems_root() / name
    m = meta_of(d)
    analysis = analysis_for(name) or {}
    snd = sound(d, analysis, band_genres(m["artist"]) if m["artist"] else [])
    secs = [{"start": r["start"], "end": r["end"]} for r in snd["sections"]]
    shp = shape(lyrics_for(d), secs)
    if shp and shp["synced"]:                            # the text's lines told by the section they are sung in
        for r in shp["sections"]:
            if r["section"] is not None and r["section"] < len(snd["sections"]):
                snd["sections"][r["section"]]["text"] = {k: r[k] for k in ("lines", "syllables", "avg", "rhyme", "repeat_of", "words")}
    out = {"version": VERSION, "at": time.time(), **m, "sound": snd,
           "text": {"lang": shp["lang"], "synced": shp["synced"],
                    "stanzas": [] if shp["synced"] else shp["sections"]} if shp else None}
    tmp = d / "songbook.json.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "songbook.json")
    return out


def done(d: Path) -> bool:
    got = _json(d / "songbook.json")
    return bool(got and got.get("version") == VERSION)


JOB = {"running": False, "total": 0, "done": 0, "now": "", "errors": [], "started": 0.0}
_JOB_LOCK = threading.Lock()


def pick_songs(limit: int, names: list = None) -> list:
    """A hundred songs, say: round the bands one song each, so many bands are in it."""
    root = stems_root()
    if names:
        return [n for n in names if (root / n).is_dir()][:limit]
    bands: dict = {}
    for d in sorted(root.glob("*")):
        if not d.is_dir() or d.name.startswith(".") or done(d) or not (d / "meta.json").is_file():
            continue
        m = meta_of(d)
        if m["artist"] and loopstudio.stem_files(d):
            bands.setdefault(_fold(m["artist"]), []).append(d.name)
    out, lists = [], list(bands.values())
    while len(out) < limit and any(lists):
        for ls in lists:
            if ls and len(out) < limit:
                out.append(ls.pop(0))
    return out


def left() -> dict:
    """The batch as it was left: {"names": still to do, "total", "done", "paused"} — or {}."""
    got = _json(QUEUE)
    return got if isinstance(got, dict) and got.get("names") else {}


def _left_save(names: list, total: int, done_n: int, paused: bool) -> None:
    try:
        if not names:
            QUEUE.unlink(missing_ok=True)
            return
        QUEUE.parent.mkdir(parents=True, exist_ok=True)
        tmp = QUEUE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"names": names, "total": total, "done": done_n, "paused": paused}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(QUEUE)
    except OSError:
        pass


def run(names: list, total: int = 0, done_before: int = 0) -> None:
    """One song after another. What is left is written down after every song, so a batch
    paused, or cut off by a restart (or the power), goes on where it was."""
    total = total or len(names)
    JOB.update(running=True, paused=False, total=total, done=done_before, now="", errors=[], started=time.time())
    names = list(names)
    _left_save(names, total, done_before, False)
    try:
        while names:
            if not JOB["running"]:
                break
            while JOB["running"] and (busy() or not stems_root().is_dir()):
                JOB["now"] = "čekám, až doběhne rozdělování…" if stems_root().is_dir() else "čekám na disk s hudbou…"
                time.sleep(5)
            if not JOB["running"]:
                break
            n = names[0]
            JOB["now"] = n
            try:
                if not done(stems_root() / n):
                    analyse(n)
            except Exception as e:                       # noqa: BLE001 — one song failing is not the batch failing
                JOB["errors"].append(f"{n}: {e}"[:200])
            names.pop(0)
            JOB["done"] += 1
            _left_save(names, total, JOB["done"], False)
    finally:
        paused = bool(names)
        _left_save(names, total, JOB["done"], paused)
        JOB.update(running=False, now="", paused=paused)


def start(names: list, total: int = 0, done_before: int = 0) -> None:
    JOB["running"] = True
    threading.Thread(target=run, args=(names, total, done_before), daemon=True).start()


def resume_after_restart() -> None:
    """A batch that was running when Pick stopped goes on by itself; a paused one waits for Pokračovat."""
    got = left()
    if got and not got.get("paused") and not JOB["running"]:
        start(got["names"], got.get("total") or 0, got.get("done") or 0)


# ------------------------------------------------------------------ the player's own texts
TAG = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def split_tagged(text: str) -> list:
    """'[Verse 1]\\n…\\n\\n[Chorus]\\n…' → [{tag, text}]; lines before the first tag are a verse."""
    out, cur = [], None
    for line in (text or "").splitlines():
        m = TAG.match(line)
        if m:
            cur = {"tag": m.group(1).strip(), "text": ""}
            out.append(cur)
            continue
        if cur is None:
            if not line.strip():
                continue
            cur = {"tag": "Verse", "text": ""}
            out.append(cur)
        cur["text"] += line + "\n"
    for x in out:
        x["text"] = x["text"].strip("\n")
    return out


def mine() -> list:
    got = _json(MINE)
    return got if isinstance(got, list) else []


def _mine_save(rows: list) -> None:
    MINE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MINE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(MINE)


# ------------------------------------------------------------------ the API
@router.get("/api/songbook")
def songbook_list():
    """Bands → their songs, analysed or not; the player's texts; the batch."""
    root = stems_root()
    bands: dict = {}
    for d in sorted(root.glob("*")) if root.is_dir() else []:
        if not d.is_dir() or d.name.startswith(".") or not (d / "meta.json").is_file():
            continue
        m = meta_of(d)
        if not m["artist"]:
            continue
        got = _json(d / "songbook.json") if (d / "songbook.json").is_file() else None
        row = {"name": d.name, "title": m["title"], "album": m["album"], "analysed": bool(got and got.get("version") == VERSION)}
        if row["analysed"]:
            s = got["sound"]
            row.update(bpm=s.get("bpm"), key=s.get("key"), sections=len(s.get("sections") or []),
                       text=bool(got.get("text")), lang=(got.get("text") or {}).get("lang"))
        b = bands.setdefault(_fold(m["artist"]), {"band": m["artist"], "songs": []})
        b["songs"].append(row)
    out = sorted(bands.values(), key=lambda b: _fold(b["band"]))
    for b in out:
        b["analysed"] = sum(1 for s in b["songs"] if s["analysed"])
        b["genres"] = (_json(BANDS) or {}).get(_fold(b["band"]), {}).get("genres") or []
    rest = left()
    job = {k: JOB.get(k) for k in ("running", "total", "done", "now")} | {"errors": JOB["errors"][-5:]}
    job |= {"paused": bool(rest) and not JOB["running"], "left": len(rest.get("names") or []),
            "todo": sum(1 for b in out for x in b["songs"] if not x["analysed"])}
    return {"bands": out, "mine": mine(), "job": job}


@router.get("/api/songbook/song/{name}")
def songbook_song(name: str):
    d = stems_root() / name
    if "/" in name or not d.is_dir():
        return JSONResponse({"error": "Taková píseň ve Steems není."}, status_code=404)
    got = _json(d / "songbook.json")
    if not got:
        return JSONResponse({"error": "Píseň ještě není rozebraná."}, status_code=404)
    return got


class AnalyseIn(BaseModel):
    names: List[str] = []
    limit: int = 100


@router.post("/api/songbook/analyse")
def songbook_analyse(req: AnalyseIn):
    """A new batch — a hundred more, the whole library (limit up to 5000), or the songs named."""
    with _JOB_LOCK:
        if JOB["running"]:
            return JSONResponse({"error": "Rozbor už běží."}, status_code=409)
        names = pick_songs(max(1, min(5000, req.limit)), [n for n in req.names if "/" not in n])
        if not names:
            return {"started": 0, "note": "Všechno je už rozebrané."}
        start(names)
    return {"started": len(names)}


@router.post("/api/songbook/analyse/resume")
def songbook_resume():
    """The paused batch, from where it stopped."""
    with _JOB_LOCK:
        if JOB["running"]:
            return JSONResponse({"error": "Rozbor už běží."}, status_code=409)
        got = left()
        if not got:
            return JSONResponse({"error": "Není co dokončit."}, status_code=404)
        start(got["names"], got.get("total") or 0, got.get("done") or 0)
    return {"started": len(got["names"])}


@router.delete("/api/songbook/analyse")
def songbook_stop(forget: bool = False):
    """Paused: the song being analysed is finished, the rest waits (also over a restart).
    With forget, what is left is let go."""
    JOB["running"] = False
    if forget:
        QUEUE.unlink(missing_ok=True)
    return {"ok": True}


class MineIn(BaseModel):
    title: str = ""
    band: str = ""
    lang: str = ""
    text: str = ""


@router.post("/api/songbook/mine")
def mine_add(req: MineIn):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "Text je prázdný."}, status_code=400)
    rows = mine()
    row = {"id": uuid.uuid4().hex[:10], "title": " ".join(req.title.split())[:80] or "Bez názvu",
           "band": " ".join(req.band.split())[:80], "lang": req.lang[:5] or _lang(text), "text": text[:20000],
           "sections": split_tagged(text), "created": time.time()}
    rows.append(row)
    _mine_save(rows)
    return row


@router.delete("/api/songbook/mine/{mid}")
def mine_delete(mid: str):
    _mine_save([x for x in mine() if x.get("id") != mid])
    return {"ok": True}
