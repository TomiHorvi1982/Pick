"""Cvičební deník — what was practised, how long and how far it got, written by Pick itself.

The page (static/journal.js) sends, every minute while a song plays in Steems, the time played
(and how much of it with the guitar plugged in), the sections and the tempo each was looped at;
the speed trainer and the timing measure add what they found. Kept per day and song in one small
file beside the app's settings, a year back. The summary of a week — how long, which days, what
got better, what has not been played for a while — is made here, and once a week the page shows
it by itself (remind).
"""
import json
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PATH = Path.home() / ".local" / "share" / "spotdl-helper" / "practice-journal.json"
router = APIRouter()
_LOCK = threading.Lock()
KEEP_DAYS = 400
FORGOTTEN_AFTER = 14          # days without a song that was practised: "dlouho jsi nehrál"
FORGOTTEN_MIN = 10 * 60       # …if it was practised at least this long before
REMIND_EVERY = 7 * 86400
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load() -> dict:
    try:
        got = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(got, dict) and isinstance(got.get("days"), dict):
            return got
    except (OSError, ValueError):
        pass
    return {"days": {}, "reminded": 0}


def _save(j: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(PATH)


def _num(x, lo=0.0, hi=1e9) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v)) if v == v else 0.0


class SectionIn(BaseModel):
    name: str = ""
    secs: float = 0
    speed: float = 0          # the tempo it was looped at (1 = the record's)


class EntryIn(BaseModel):
    day: str
    song: str
    title: str = ""
    secs: float = 0
    guitar: float = 0
    sections: List[SectionIn] = []
    event: Optional[Dict[str, Any]] = None      # {kind: "trainer" | "timing", …}


def _song_of(day: dict, song: str, title: str) -> dict:
    s = day.setdefault(song, {"secs": 0, "guitar": 0, "sections": {}, "events": []})
    if title:
        s["title"] = title[:160]
    return s


def _add(j: dict, e: EntryIn) -> None:
    d = j["days"].setdefault(e.day, {})
    s = _song_of(d, e.song, e.title)
    s["secs"] = round(s.get("secs", 0) + _num(e.secs, 0, 3600))
    s["guitar"] = round(s.get("guitar", 0) + _num(e.guitar, 0, 3600))
    for x in e.sections[:40]:
        name = " ".join((x.name or "").split())[:60]
        if not name:
            continue
        sec = s["sections"].setdefault(name, {"secs": 0, "best": 0})
        sec["secs"] = round(sec["secs"] + _num(x.secs, 0, 3600))
        sec["best"] = max(sec.get("best", 0), round(_num(x.speed, 0, 2), 2))
    if e.event and e.event.get("kind") in ("trainer", "timing"):
        ev = {k: v for k, v in e.event.items() if isinstance(v, (int, float, str, bool)) and len(str(v)) < 120}
        ev["at"] = int(time.time())
        s["events"] = (s.get("events") or [])[-49:] + [ev]
        if ev["kind"] == "trainer" and ev.get("section"):
            sec = s["sections"].setdefault(str(ev["section"])[:60], {"secs": 0, "best": 0})
            sec["trained"] = max(sec.get("trained", 0), int(_num(ev.get("reached"), 0, 200)))
    # a year back is enough
    cut = (date.fromisoformat(e.day) - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k in j["days"] if k < cut]:
        del j["days"][k]


@router.post("/api/journal")
def journal_add(e: EntryIn):
    if not DAY.match(e.day or "") or not (e.song or "").strip() or len(e.song) > 300 or "/" in e.song:
        return JSONResponse({"error": "Špatný záznam."}, status_code=400)
    try:
        date.fromisoformat(e.day)
    except ValueError:
        return JSONResponse({"error": "Špatné datum."}, status_code=400)
    with _LOCK:
        j = load()
        _add(j, e)
        _save(j)
    return {"ok": True}


def _week_of(today: date) -> date:
    return today - timedelta(days=today.weekday())        # Monday


def summary(j: dict, today: date, week: int = 0) -> dict:
    """The week `week` weeks back (0: this one, Monday to Sunday): its days, songs and what got better
    against everything before it; songs not played for a while; whether a reminder is due."""
    days = j.get("days") or {}
    mon = _week_of(today) - timedelta(weeks=week)
    span = [(mon + timedelta(days=i)).isoformat() for i in range(7)]
    first, last = span[0], span[-1]
    per_day = [sum(s.get("secs", 0) for s in (days.get(d) or {}).values()) for d in span]
    songs: Dict[str, dict] = {}
    for d in span:
        for name, s in (days.get(d) or {}).items():
            w = songs.setdefault(name, {"song": name, "title": s.get("title") or name, "secs": 0, "guitar": 0, "days": 0, "sections": {}, "events": []})
            w["secs"] += s.get("secs", 0)
            w["guitar"] += s.get("guitar", 0)
            w["days"] += 1
            if s.get("title"):
                w["title"] = s["title"]
            for k, v in (s.get("sections") or {}).items():
                x = w["sections"].setdefault(k, {"name": k, "secs": 0, "best": 0, "trained": 0})
                x["secs"] += v.get("secs", 0)
                x["best"] = max(x["best"], v.get("best", 0))
                x["trained"] = max(x["trained"], v.get("trained", 0))
            w["events"] += s.get("events") or []
    # before the week: the best each section got to, the timing's last
    before: Dict[str, dict] = {}
    for d in sorted(k for k in days if k < first):
        for name, s in days[d].items():
            b = before.setdefault(name, {"best": {}, "timing": None})
            for k, v in (s.get("sections") or {}).items():
                b["best"][k] = max(b["best"].get(k, 0), v.get("best", 0), (v.get("trained", 0) or 0) / 100)
            for ev in s.get("events") or []:
                if ev.get("kind") == "timing":
                    b["timing"] = ev
    better = []
    for name, w in songs.items():
        b = before.get(name) or {"best": {}, "timing": None}
        for k, x in w["sections"].items():
            now = max(x["best"], x["trained"] / 100)
            was = b["best"].get(k, 0)
            if was and now - was >= 0.049:
                better.append({"song": name, "title": w["title"], "what": k, "from": round(was * 100), "to": round(now * 100), "kind": "tempo"})
        tim = [ev for ev in w["events"] if ev.get("kind") == "timing" and isinstance(ev.get("within"), (int, float))]
        if tim:
            was = b["timing"] or (tim[0] if len(tim) > 1 else None)
            if was is not None and tim[-1]["within"] - was.get("within", 0) >= 5:
                better.append({"song": name, "title": w["title"], "what": "časování", "from": was.get("within"), "to": tim[-1]["within"], "kind": "timing"})
        for ev in w["events"]:
            if ev.get("kind") == "trainer" and ev.get("done"):
                better.append({"song": name, "title": w["title"], "what": ev.get("section") or "", "to": ev.get("to"), "kind": "trainer"})
    # songs practised for a while that have not been played for FORGOTTEN_AFTER days
    total: Dict[str, dict] = {}
    for d, ss in days.items():
        if d > last:
            continue
        for name, s in ss.items():
            t = total.setdefault(name, {"song": name, "title": s.get("title") or name, "secs": 0, "last": d})
            t["secs"] += s.get("secs", 0)
            t["last"] = max(t["last"], d)
            if s.get("title"):
                t["title"] = s["title"]
    ref = min(today, date.fromisoformat(last))
    forgotten = []
    for t in total.values():
        ago = (ref - date.fromisoformat(t["last"])).days
        if t["secs"] >= FORGOTTEN_MIN and ago >= FORGOTTEN_AFTER:
            forgotten.append({**t, "ago": ago})
    forgotten.sort(key=lambda t: -t["secs"])
    out_songs = []
    for w in sorted(songs.values(), key=lambda w: -w["secs"]):
        secs = sorted(w["sections"].values(), key=lambda x: -x["secs"])[:8]
        out_songs.append({"song": w["song"], "title": w["title"], "secs": w["secs"], "guitar": w["guitar"], "days": w["days"], "sections": secs,
                          "trainer": [ev for ev in w["events"] if ev.get("kind") == "trainer"][-5:],
                          "timing": [ev for ev in w["events"] if ev.get("kind") == "timing"][-5:]})
    had = any(d < _week_of(today).isoformat() for d in days)
    return {"week": week, "from": first, "to": last, "days": per_day, "total": sum(per_day), "songs": out_songs,
            "better": better[:20], "forgotten": forgotten[:8], "any": bool(days), "older": any(d < first for d in days),
            "remind": bool(days) and had and time.time() - (j.get("reminded") or 0) >= REMIND_EVERY}


@router.get("/api/journal")
def journal_get(week: int = 0):
    week = max(0, min(60, week))
    return summary(load(), date.today(), week)


@router.post("/api/journal/reminded")
def journal_reminded():
    with _LOCK:
        j = load()
        j["reminded"] = int(time.time())
        _save(j)
    return {"ok": True}


@router.delete("/api/journal")
def journal_clear():
    with _LOCK:
        _save({"days": {}, "reminded": 0})
    return {"ok": True}
