"""Katalog riffů — the Taby library's tabs taken apart into the parts of their songs (intro, verse,
chorus, solo, outro…) and the riff each part repeats, ready for the virtual guitar to play.

A Songsterr tab carries the song's sections as markers on its bars ("Intro", "Verse 1", "Guitar
Solo"); a part is the bars from one marker to the next. In a part the bars of all its guitars are
compared: the shortest run of bars that repeats through it is its riff (a part that never repeats
is kept whole, or its start when it is long). Guitars that play together stay together in one
entry — a rhythm and a lead, or two guitars each playing something else; two playing the same
notes (left and right) are one part, doubled. The same riff in more parts of a song (Verse 1,
Verse 2) is one entry with both names.

A riff here is the virtual guitar's own grid: a step an 8th, 16th, 32nd or triplet; a column the
frets on its strings, its palm mute, its legato notes (vguitar.py). Kept compact on disk as text —
a step a token: "." rests, notes "string:fret" joined by "+" (string 0 the highest), "~" after a
note plays it legato, "!" after a step palm mutes it.

The library is read one song at a time in the background, Songsterr asked one request at a time
with a pause between — it is a guest here. A song's catalog is a file (riffbook/songs/<id>.json);
stars and the player's own labels are kept apart (marks.json), so a song read again keeps them.
"""
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
ROOT = Path.home() / ".local" / "share" / "spotdl-helper" / "riffbook"
UA = "Pick/1.0 (https://github.com/TomiHorvi1982/Pick; personal music app)"
CDNS = ["dqsljvtekg760", "d34shlm8p2ums2", "d3cqchs6g3b5ew"]
PAUSE = float(os.environ.get("RIFFBOOK_PAUSE") or 1.2)   # seconds between two requests to Songsterr
MAX_GUITARS = 6
MAX_STEPS = 1024                  # the virtual guitar's longest riff
DIVS = (2, 4, 3, 6, 8)            # steps a beat the guitar knows: 8ths, 16ths, triplets, 16th triplets, 32nds
PAGE = 60

library = lambda: []              # noqa: E731 — the Taby library [{songId, artist, title}]; server.py sets it


def songs_dir() -> Path:
    return ROOT / "songs"


# ---------------------------------------------------------------- what a part is called
KINDS = [  # (kind, Czech name, pattern) — the first that matches; pre-chorus before chorus, solo before all
    ("solo", "Sólo", r"solo|lead break|shred"),
    ("prechorus", "Předrefrén", r"pre[- ]?(chorus|refr)|build[- ]?up|lift"),
    ("chorus", "Refrén", r"chorus|refr|hook"),
    ("verse", "Sloka", r"verse|sloka|strophe|couplet|pre[- ]?verse"),
    ("intro", "Intro", r"intro|opening|^start"),
    ("outro", "Outro", r"outro|ending|coda|\bend\b|fade|finale"),
    ("bridge", "Most", r"bridge|middle ?8|\bmost\b"),
    ("breakdown", "Breakdown", r"breakdown|break ?down|\bbreak\b"),
    ("interlude", "Mezihra", r"interlude|instrumental|mezihra|post[- ]?chorus|transition|link|theme"),
    ("riff", "Riff", r"riff|groove|main"),
]
KIND_NAMES = {k: n for k, n, _ in KINDS} | {"other": "Jiné"}


def kind_of(label: str) -> str:
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", (label or "").lower())       # "[B] Ballad: Verse" → "ballad: verse"
    t = t.split(":")[-1] if ":" in t else t                            # its last word says what it is
    for k, _, rx in KINDS:
        if re.search(rx, t):
            return k
    for k, _, rx in KINDS:                                             # "Opera" says nothing; "[A] Intro" did
        if re.search(rx, (label or "").lower()):
            return k
    return "other"


ROLES = [("lead", r"lead|solo|melod|harmon|fill|\blead\b"), ("rhythm", r"rhythm|rhy\b|chord|riff|power|crunch"),
         ("acoustic", r"acoustic|akust|nylon|12[- ]?str")]
ROLE_NAMES = {"lead": "Sólová", "rhythm": "Rytmická", "acoustic": "Akustická", "guitar": "Kytara"}


def stats(cols: list, tuning: list) -> tuple:
    """(share of the played steps that are chords, the notes' mean pitch)."""
    played = [c for c in cols if c["s"]]
    if not played:
        return 0.0, 50.0
    pitches = [tuning[int(s)] + f for c in played for s, f in c["s"].items() if f != "x" and int(s) < len(tuning)]
    return sum(1 for c in played if len(c["s"]) >= 2) / len(played), (sum(pitches) / len(pitches) if pitches else 50.0)


def role_of(names: list, instrument_id, cols: list, tuning: list, kind: str) -> str:
    """What the guitar does here. A track called Rhythm is rhythm, an acoustic one acoustic; else the
    notes say it — chords or a riff low on the strings are rhythm, a line high up is lead (bands call
    their two riffing guitars "Lead #1" and "Lead #2"); else what the track is called."""
    said = [r for n in names for r, rx in ROLES if re.search(rx, (n or "").lower())]
    if "rhythm" in said:
        return "rhythm"
    if "acoustic" in said or instrument_id in (24, 25):
        return "acoustic"
    if not any(c["s"] for c in cols):
        return said[0] if said else "guitar"
    chords, mean = stats(cols, tuning)
    if "lead" in said and mean >= 58:                    # a lead up the neck, in harmonies (two notes at once) too
        return "lead"
    if chords >= 0.45 or mean <= 55:
        return "rhythm"
    if mean >= 62 or (kind == "solo" and chords < 0.2):
        return "lead"
    return said[0] if said else "guitar"


def roles_together(parts: list, kind: str) -> None:
    """Guitars side by side: when none came out lead but one plays clearly above the others with few
    chords — or it is the solo — that one is the lead."""
    if len(parts) < 2 or any(p["role"] == "lead" for p in parts):
        return
    free = [p for p in parts if p["role"] != "acoustic" and not p["named_rhythm"]]
    if not free:
        return
    top = max(free, key=lambda p: p["mean"] - (0 if kind == "solo" else 20 * p["chords"]))
    rest = [p["mean"] for p in parts if p is not top]
    if top["mean"] >= max(rest) + 5 if kind == "solo" else top["chords"] < 0.25 and top["mean"] >= max(rest) + 7:
        top["role"] = "lead"


def short_name(t: dict, i: int) -> str:
    """"Brian May | Red Special | Rhythm Guitar (Left)" → "Rhythm Guitar (Left)"."""
    parts = [p.strip() for p in (t.get("name") or "").split("|") if p.strip()]
    return (parts[-1] if parts else "") or t.get("instrument") or f"Kytara {i + 1}"


TUNINGS = {(64, 59, 55, 50, 45, 40): "Standard E", (64, 59, 55, 50, 45, 38): "Drop D", (63, 58, 54, 49, 44, 39): "Eb Standard",
           (62, 57, 53, 48, 43, 38): "D Standard", (63, 58, 54, 49, 44, 37): "Drop C#", (62, 57, 53, 48, 43, 36): "Drop C",
           (60, 55, 51, 46, 41, 36): "C Standard", (61, 56, 52, 47, 42, 35): "Drop B", (64, 59, 55, 50, 45, 40, 35): "7 strun (B)"}
NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def tuning_name(t: list) -> str:
    return TUNINGS.get(tuple(t)) or " ".join(NOTE[m % 12] for m in reversed(t))


def is_guitar(t: dict) -> bool:
    iid = t.get("instrumentId")
    if iid == 1024 or re.search(r"drum|perc|bass", t.get("instrument") or "", re.I) or (isinstance(iid, int) and 32 <= iid <= 39):
        return False
    tun = t.get("tuning")
    ok = isinstance(tun, list) and 6 <= len(tun) <= 8 and all(isinstance(x, int) and 20 <= x <= 80 for x in tun)
    return ok and not t.get("isEmpty") and ((isinstance(iid, int) and 24 <= iid <= 31) or bool(re.search(r"guitar", t.get("instrument") or "", re.I)))


# ---------------------------------------------------------------- a tab's bars on the guitar's grid
def beat_len(b: dict) -> float:
    """A beat's length in quarter notes."""
    d = b.get("duration") if isinstance(b.get("duration"), list) and len(b["duration"]) == 2 else [1, 4]
    try:
        q = 4 * float(d[0] or 1) / float(d[1] or 4)
    except (TypeError, ValueError, ZeroDivisionError):
        q = 1.0
    dots = int(b.get("dots") or 0)
    q *= 1.5 if dots == 1 else 1.75 if dots >= 2 else 1
    tup = int(b.get("tuplet") or 0)
    q *= {3: 2 / 3, 6: 2 / 3, 5: 4 / 5, 7: 4 / 7, 9: 8 / 9}.get(tup, 1)
    return q


def bars_of(measures: list) -> list:
    """[{q: the bar's length in quarters, ev: [(at, beat)]}] — the first voice; a grace note takes no time."""
    out, sig = [], [4, 4]
    for m in measures:
        sig = m.get("signature") or sig
        try:
            q = 4 * float(sig[0]) / float(sig[1])
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            q = 4.0
        beats = ((m.get("voices") or [{}])[0] or {}).get("beats") or []
        at, ev = 0.0, []
        for b in beats:
            if b.get("graceNote"):
                continue
            ev.append((at, b))
            at += beat_len(b)
        out.append({"q": q, "ev": ev, "marker": ((m.get("marker") or {}).get("text") or "").strip()})
    return out


def fit_div(starts: list) -> int:
    for d in DIVS:
        if sum(1 for x in starts if abs(x * d - round(x * d)) < 0.02) >= len(starts) * 0.98:
            return d
    return 4


def bar_cols(bar: dict, div: int, strings: int, hp: list) -> list:
    """One bar as the guitar's columns. hp: per string, the last note began a hammer-on or pull-off
    (Songsterr marks the note it starts from; the grid, the one it lands on)."""
    steps = max(1, round(bar["q"] * div))
    cols = [{"s": {}, "pm": False, "lg": []} for _ in range(steps)]
    for at, b in bar["ev"]:
        k = round(at * div)
        if k >= steps or b.get("rest"):
            continue
        end = min(steps, max(k + 1, round((at + beat_len(b)) * div)))
        mute = bool(b.get("palmMute"))
        for n in b.get("notes") or []:
            s, f = n.get("string"), n.get("fret")
            if n.get("rest") or n.get("tie") or not isinstance(s, int) or not 0 <= s < strings or not isinstance(f, (int, float)):
                continue
            if n.get("dead"):
                cols[k]["s"][s] = "x"
                hp[s] = False
            else:
                cols[k]["s"][s] = max(0, min(30, int(f)))
                if hp[s]:
                    cols[k]["lg"].append(s)
                hp[s] = bool(n.get("hp") or n.get("hammer") or n.get("pull"))
            mute = mute or bool(n.get("palmMute"))
        if mute:
            for j in range(k, end):
                cols[j]["pm"] = True
    return cols


def encode(cols: list) -> str:
    def tok(c):
        notes = "+".join(f"{s}:{f}{'~' if s in c['lg'] else ''}" for s, f in sorted(c["s"].items()))
        return (notes or ".") + ("!" if c["pm"] else "")
    return " ".join(tok(c) for c in cols)


def decode(text: str) -> list:
    cols = []
    for t in (text or "").split():
        c = {"s": {}, "pm": t.endswith("!"), "lg": []}
        body = t.rstrip("!")
        if body != ".":
            for nt in body.split("+"):
                s, f = nt.split(":")
                c["s"][str(int(s))] = "x" if f.rstrip("~") == "x" else int(f.rstrip("~"))
                if f.endswith("~"):
                    c["lg"].append(int(s))
        cols.append(c)
    return cols


def period(keys: list, most: int) -> Optional[int]:
    """The shortest run of bars the part repeats (three quarters of it at least), or None."""
    n = len(keys)
    for p in range(1, min(most, n // 2) + 1):
        same = sum(1 for i in range(p, n) if keys[i] == keys[i - p])
        if same >= 0.75 * (n - p):
            return p
    return None


# ---------------------------------------------------------------- a song taken apart
def sections(bars: list) -> list:
    """[(label, start, end)] from the markers; a tab without them in runs of eight bars."""
    marks = [(i, b["marker"]) for i, b in enumerate(bars) if b["marker"]]
    n = len(bars)
    if not marks:
        return [(f"Úsek {k // 8 + 1}", k, min(n, k + 8)) for k in range(0, n, 8)]
    out = [("Začátek", 0, marks[0][0])] if marks[0][0] > 0 else []
    for j, (i, label) in enumerate(marks):
        out.append((label, i, marks[j + 1][0] if j + 1 < len(marks) else n))
    return [s for s in out if s[2] > s[1]]


def tempo_at(tempo: list, bar: int) -> int:
    bpm = 120.0
    for e in sorted((e for e in tempo or [] if isinstance(e, dict) and "bpm" in e), key=lambda e: (int(e.get("measure", 0)), float(e.get("position", 0)))):
        if int(e.get("measure", 0)) <= bar:
            bpm = float(e["bpm"])
    return max(30, min(300, round(bpm)))


def analyze(meta: dict, parts: list) -> list:
    """parts: [{i, track (the meta's), measures, tempo}] — the guitars. The song's entries."""
    if not parts:
        return []
    guit = [{**p, "bars": bars_of(p["measures"]), "strings": len(p["track"]["tuning"])} for p in parts]
    ref = max(guit, key=lambda g: sum(1 for b in g["bars"] if b["marker"]))       # the one with the most markers
    n = min(len(g["bars"]) for g in guit)
    entries, seen = [], {}
    for label, s, e in sections(ref["bars"][:n]):
        kind = kind_of(label) if not label.startswith(("Úsek", "Začátek")) else ("intro" if label == "Začátek" else "other")
        starts = [at for g in guit for b in g["bars"][s:e] for at, bt in b["ev"] if not bt.get("rest")]
        if not starts:
            continue
        div = fit_div(starts)
        grid = {}
        for g in guit:
            hp = [False] * g["strings"]
            grid[g["i"]] = [bar_cols(b, div, g["strings"], hp) for b in g["bars"][s:e]]
        on = [g for g in guit if any(c["s"] for bar in grid[g["i"]] for c in bar)]
        if not on:
            continue
        rows = list(range(e - s))                                 # bars where some guitar plays: the part's own
        while rows and not any(c["s"] for g in on for c in grid[g["i"]][rows[0]]):
            rows.pop(0)
        while rows and not any(c["s"] for g in on for c in grid[g["i"]][rows[-1]]):
            rows.pop()
        keys = ["|".join(encode(grid[g["i"]][r]) for g in on) for r in rows]
        p = period(keys, 8)
        steps_bar = max(len(grid[on[0]["i"]][r]) for r in rows)
        cap = max(1, min(16 if kind == "solo" else 8, MAX_STEPS // max(1, steps_bar)))
        take = min(p or len(rows), cap)
        use = rows[:take]
        got = []
        for g in on:
            cols = [c for r in use for c in grid[g["i"]][r]]
            if any(c["s"] for c in cols):
                got.append({"g": g, "riff": encode(cols), "cols": cols})
        if not got:
            continue
        merged = []                                                # the same notes on two guitars: one part, doubled
        for x in got:
            twin = next((m for m in merged if m["riff"] == x["riff"]), None)
            if not twin:
                twin = {**x, "names": [], "full": []}
                merged.append(twin)
            twin["names"].append(short_name(x["g"]["track"], x["g"]["i"]))
            twin["full"].append(x["g"]["track"].get("name") or "")
        key = "#".join(m["riff"] for m in merged)
        if key in seen:                                           # Verse 2 plays Verse 1's riff: one entry, both names
            ent = seen[key]
            if label not in ent["labels"]:
                ent["labels"].append(label)
            continue
        out = []
        for m in merged:
            g = m["g"]
            role = role_of(m["full"], g["track"].get("instrumentId"), m["cols"], g["track"]["tuning"], kind)
            chords, mean = stats(m["cols"], g["track"]["tuning"])
            out.append({"track": g["i"], "name": " + ".join(m["names"]), "double": len(m["names"]), "role": role,
                        "tuning": list(g["track"]["tuning"]), "cols": m["riff"], "chords": chords, "mean": mean,
                        "named_rhythm": any(re.search(ROLES[1][1], n.lower()) for n in m["full"])})
        roles_together(out, kind)
        for x in out:
            for k in ("chords", "mean", "named_rhythm"):
                x.pop(k)
        out.sort(key=lambda x: {"rhythm": 0, "guitar": 1, "acoustic": 2, "lead": 3}[x["role"]])
        ent = {"id": f"{meta.get('songId')}-{len(entries) + 1}", "labels": [label], "kind": kind, "start": s + 1 + use[0],
               "bars": take, "of": len(rows), "repeats": round(len(rows) / p) if p else 1, "whole": not p and take >= len(rows),
               "bpm": tempo_at(ref.get("tempo") or [], s + use[0]), "div": div, "parts": out}
        seen[key] = ent
        entries.append(ent)
    return entries


# ---------------------------------------------------------------- Songsterr, asked politely
def _get(url: str):
    for attempt in range(2):
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
        if r.status_code == 429 and not attempt:
            time.sleep(60)
            continue
        r.raise_for_status()
        return r.json()


def fetch_meta(song_id: int) -> dict:
    return _get(f"https://www.songsterr.com/api/meta/{song_id}")


def fetch_track(song_id: int, revision: int, image: str, i: int) -> dict:
    last = None
    for cdn in CDNS:
        try:
            return _get(f"https://{cdn}.cloudfront.net/{song_id}/{revision}/{image}/{i}.json")
        except (httpx.HTTPError, ValueError) as e:
            last = e
    raise last or RuntimeError("track")


def read_song(song_id: int, pause: Optional[float] = None) -> dict:
    pause = PAUSE if pause is None else pause
    meta = fetch_meta(song_id)
    tracks = meta.get("tracks") or []
    want = [i for i, t in enumerate(tracks) if is_guitar(t)][:MAX_GUITARS]
    parts = []
    for i in want:
        time.sleep(pause)
        data = fetch_track(song_id, meta["revisionId"], meta["image"], i)
        parts.append({"i": i, "track": tracks[i], "measures": data.get("measures") or [], "tempo": (data.get("automations") or {}).get("tempo") or []})
    return {"songId": song_id, "revisionId": meta.get("revisionId"), "artist": meta.get("artist") or "", "title": meta.get("title") or "",
            "guitars": [{"i": i, "name": short_name(tracks[i], i), "tuning": tracks[i]["tuning"]} for i in want],
            "read": int(time.time()), "entries": analyze(meta, parts)}


# ---------------------------------------------------------------- the catalog on disk
_LOCK = threading.Lock()
_SONGS: Optional[dict] = None                  # songId → the song's file without its notes (read from disk when a riff opens)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _light(d: dict) -> dict:
    return {**d, "entries": [{**e, "parts": [{k: v for k, v in p.items() if k != "cols"} for p in e["parts"]]} for e in d.get("entries") or []]}


def songs() -> dict:
    global _SONGS
    with _LOCK:
        if _SONGS is None:
            _SONGS = {}
            for f in songs_dir().glob("*.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    _SONGS[int(d["songId"])] = _light(d)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        return _SONGS


def song_file(song_id: int) -> Optional[dict]:
    try:
        return json.loads((songs_dir() / f"{int(song_id)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def keep_song(d: dict) -> None:
    _write(songs_dir() / f"{d['songId']}.json", d)
    songs()[int(d["songId"])] = _light(d)


def marks() -> dict:
    try:
        got = json.loads((ROOT / "marks.json").read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def rows() -> list:
    mk = marks()
    out = []
    for d in songs().values():
        for e in d.get("entries") or []:
            m = mk.get(e["id"]) or {}
            out.append({"id": e["id"], "songId": d["songId"], "artist": d.get("artist", ""), "title": d.get("title", ""),
                        "labels": e["labels"], "kind": e["kind"], "start": e["start"], "bars": e["bars"], "of": e["of"],
                        "repeats": e["repeats"], "whole": e.get("whole", False), "bpm": e["bpm"],
                        "tuning": tuning_name(e["parts"][0]["tuning"]),
                        "parts": [{"name": p["name"], "role": p["role"], "double": p["double"]} for p in e["parts"]],
                        "star": bool(m.get("star")), "tags": m.get("tags") or []})
    out.sort(key=lambda r: (r["artist"].lower(), r["title"].lower(), r["start"]))
    return out


# ---------------------------------------------------------------- reading the library, in the background
_SCAN = {"queue": [], "running": False, "stop": False, "done": 0, "total": 0, "current": "", "errors": 0, "error": "", "finished": 0}
_SCAN_LOCK = threading.Lock()


def _state_keep() -> None:
    try:
        _write(ROOT / "scan.json", {"queue": _SCAN["queue"], "running": _SCAN["running"], "done": _SCAN["done"], "total": _SCAN["total"]})
    except OSError:
        pass


def _worker(pause: float) -> None:
    while True:
        with _SCAN_LOCK:
            if _SCAN["stop"] or not _SCAN["queue"]:
                _SCAN.update(running=False, current="", finished=int(time.time()), stop=False)
                if not _SCAN["queue"]:
                    _SCAN.update(done=0, total=0)
                _state_keep()
                return
            sid, label = _SCAN["queue"][0]
            _SCAN["current"] = label
        try:
            d = read_song(sid, pause)
            keep_song(d)
        except Exception as e:                                         # noqa: BLE001 — one song failing is not the reading failing
            with _SCAN_LOCK:
                _SCAN["errors"] += 1
                _SCAN["error"] = f"{label}: {e}"[:200]
        with _SCAN_LOCK:
            if _SCAN["queue"] and _SCAN["queue"][0][0] == sid:
                _SCAN["queue"].pop(0)
            _SCAN["done"] += 1
            _state_keep()
        time.sleep(pause)


def start(items: list, pause: Optional[float] = None) -> dict:
    pause = PAUSE if pause is None else pause
    with _SCAN_LOCK:
        have = {sid for sid, _ in _SCAN["queue"]}
        new = [(int(s), lbl) for s, lbl in items if int(s) not in have]
        _SCAN["queue"].extend(new)
        _SCAN["total"] += len(new)
        _SCAN["stop"] = False
        go = not _SCAN["running"] and bool(_SCAN["queue"])
        if go:
            _SCAN.update(running=True, errors=0, error="")
        _state_keep()
    if go:
        threading.Thread(target=_worker, args=(pause,), daemon=True).start()
    return status()


def status() -> dict:
    with _SCAN_LOCK:
        left = len(_SCAN["queue"])
        out = {k: _SCAN[k] for k in ("running", "done", "total", "current", "errors", "error", "finished")}
    lib = library()
    have = songs()
    out.update(left=left, songs=len(have), library=len(lib), entries=sum(len(d.get("entries") or []) for d in have.values()),
               unread=sum(1 for t in lib if t.get("songId") is not None and int(t["songId"]) not in have))
    return out


def resume_after_restart() -> None:
    try:
        st = json.loads((ROOT / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if st.get("running") and st.get("queue"):
        with _SCAN_LOCK:
            _SCAN.update(done=int(st.get("done") or 0), total=int(st.get("total") or 0))
        start([tuple(x) for x in st["queue"]])


# ---------------------------------------------------------------- the page's questions
GROUPS = [("kind", "Část písně"), ("guitars", "Kytary"), ("role", "Kytara hraje"), ("tuning", "Ladění"), ("tempo", "Tempo"), ("tag", "Moje štítky")]
TEMPO = [("slow", "pomalé (do 90)"), ("mid", "střední (90–130)"), ("fast", "rychlé (130–170)"), ("vfast", "velmi rychlé (170+)")]


def _facets(r: dict) -> dict:
    n = len(r["parts"])
    t = r["bpm"]
    return {"kind": [r["kind"]], "guitars": ["1" if n == 1 else "2" if n == 2 else "3"],
            "role": sorted({p["role"] for p in r["parts"]}), "tuning": [r["tuning"]],
            "tempo": ["slow" if t < 90 else "mid" if t < 130 else "fast" if t < 170 else "vfast"], "tag": r["tags"]}


def _value_name(g: str, v: str) -> str:
    if g == "kind":
        return KIND_NAMES.get(v, v)
    if g == "guitars":
        return {"1": "jedna", "2": "dvě spolu", "3": "tři a víc"}.get(v, v)
    if g == "role":
        return ROLE_NAMES.get(v, v)
    if g == "tempo":
        return dict(TEMPO).get(v, v)
    return v


@router.get("/api/riffbook")
def riffbook_list(request: Request, q: str = "", song: int = 0, star: bool = False, page: int = 1):
    chosen = {g: [v for v in request.query_params.getlist(g) if v][:30] for g, _ in GROUPS}
    words = [w for w in q.lower().split() if w]
    base = [r for r in rows() if (not song or r["songId"] == song) and (not star or r["star"])
            and all(w in f"{r['artist']} {r['title']} {' '.join(r['labels'])}".lower() for w in words)]
    fac = {r["id"]: _facets(r) for r in base}
    ok = lambda r, skip="": all(not vs or any(v in fac[r["id"]][g] for v in vs) for g, vs in chosen.items() if g != skip)  # noqa: E731
    hits = [r for r in base if ok(r)]
    groups = []
    for g, name in GROUPS:
        counts = {}
        for r in base:
            if ok(r, g):
                for v in fac[r["id"]][g]:
                    counts[v] = counts.get(v, 0) + 1
        order = [k for k, _, _ in KINDS] + ["other"] if g == "kind" else ["1", "2", "3"] if g == "guitars" \
            else ["rhythm", "lead", "acoustic", "guitar"] if g == "role" else [k for k, _ in TEMPO] if g == "tempo" \
            else sorted(counts, key=lambda v: -counts[v])
        vals = [{"v": v, "name": _value_name(g, v), "count": counts.get(v, 0)} for v in order if counts.get(v) or v in chosen[g]]
        groups.append({"param": g, "name": name, "values": vals})
    pages = max(1, math.ceil(len(hits) / PAGE))
    page = max(1, min(page, pages))
    return {"rows": hits[(page - 1) * PAGE: page * PAGE], "total": len(hits), "page": page, "pages": pages, "groups": groups,
            "kinds": KIND_NAMES, "roles": ROLE_NAMES, "status": status()}


@router.get("/api/riffbook/riff/{rid}")
def riffbook_riff(rid: str):
    m = re.fullmatch(r"(\d+)-\d+", rid)
    d = song_file(int(m.group(1))) if m else None
    e = next((x for x in (d or {}).get("entries") or [] if x["id"] == rid), None)
    if not e:
        return JSONResponse({"error": "Riff v katalogu není."}, status_code=404)
    return {**e, "artist": d.get("artist", ""), "title": d.get("title", ""), "songId": d["songId"],
            "parts": [{**p, "cols": decode(p["cols"])} for p in e["parts"]]}


class MarkIn(BaseModel):
    id: str
    star: Optional[bool] = None
    tags: Optional[list] = None


@router.post("/api/riffbook/mark")
def riffbook_mark(req: MarkIn):
    if not re.fullmatch(r"\d+-\d+", req.id):
        return JSONResponse({"error": "Neznámý riff."}, status_code=400)
    with _LOCK:
        mk = marks()
        m = mk.get(req.id) or {}
        if req.star is not None:
            m["star"] = req.star
        if req.tags is not None:
            tags = []
            for t in req.tags:
                t = " ".join(str(t).split())[:30]
                if t and t.lower() not in [x.lower() for x in tags]:
                    tags.append(t)
            m["tags"] = tags[:8]
        if m.get("star") or m.get("tags"):
            mk[req.id] = m
        else:
            mk.pop(req.id, None)
        _write(ROOT / "marks.json", mk)
    return {"ok": True, **m}


class ScanIn(BaseModel):
    songIds: list = []
    force: bool = False


@router.post("/api/riffbook/scan")
def riffbook_scan(req: ScanIn):
    lib = {int(t["songId"]): t for t in library() if t.get("songId") is not None}
    have = songs()
    if req.songIds:
        ids = [int(s) for s in req.songIds if str(s).isdigit()][:50]
    else:
        ids = [s for s in lib if req.force or s not in have]
    items = [(s, f"{(lib.get(s) or have.get(s) or {}).get('artist', '')} – {(lib.get(s) or have.get(s) or {}).get('title', '')}".strip(" –") or str(s))
             for s in ids if req.force or req.songIds or s not in have]
    return start(items)


@router.post("/api/riffbook/scan/stop")
def riffbook_stop():
    with _SCAN_LOCK:
        if _SCAN["running"]:
            _SCAN["stop"] = True
        _SCAN["queue"] = _SCAN["queue"][:1] if _SCAN["running"] else []
        _state_keep()
    return status()


@router.get("/api/riffbook/scan")
def riffbook_status():
    return status()

