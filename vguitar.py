"""Virtuální kytara — riffs the player writes or pastes as a tab, kept to be played again
(static/vguitar.js plays them, in the sound of a song's tone print or a clean guitar).

A riff: {id, name, bpm, div (steps a beat), tuning [high e … low E, MIDI], steps, cols:
[{"s": {string: fret | "x"}, "pm": bool, "lg": [strings played legato]}], sound {song, pid}}.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
FILE = Path.home() / ".local" / "share" / "spotdl-helper" / "vguitar.json"
MAX_STEPS = 1024


def riffs() -> list:
    try:
        got = json.loads(FILE.read_text(encoding="utf-8"))
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def clean(data: dict) -> Optional[dict]:
    """A riff as the page sent it, kept only in its known shape."""
    try:
        cols = []
        for c in (data.get("cols") or [])[:MAX_STEPS]:
            notes = {}
            for k, v in (c.get("s") or {}).items():
                i = int(k)
                if 0 <= i < 8 and (v == "x" or (isinstance(v, int) and 0 <= v <= 30)):
                    notes[str(i)] = v
            cols.append({"s": notes, "pm": bool(c.get("pm")), "lg": [int(x) for x in (c.get("lg") or []) if 0 <= int(x) < 8]})
        tuning = [int(x) for x in data.get("tuning") or []][:8]
        if not cols or not (4 <= len(tuning) <= 8) or not all(20 <= t <= 80 for t in tuning):
            return None
        snd = data.get("sound") or {}
        return {"bpm": max(30, min(300, int(data.get("bpm") or 120))), "div": int(data.get("div") or 4) if int(data.get("div") or 4) in (2, 3, 4, 6, 8) else 4,
                "tuning": tuning, "cols": cols,
                "sound": {"song": str(snd.get("song") or "")[:300], "pid": str(snd.get("pid") or "")[:60]} if snd.get("song") else None}
    except (TypeError, ValueError, AttributeError):
        return None


@router.get("/api/vguitar/riffs")
def riff_list():
    return {"riffs": sorted(riffs(), key=lambda r: -r.get("changed", 0))}


class RiffIn(BaseModel):
    id: str = ""
    name: str = ""
    riff: dict = {}


@router.post("/api/vguitar/riffs")
def riff_save(req: RiffIn):
    body = clean(req.riff)
    if not body:
        return JSONResponse({"error": "Riff je prázdný nebo se nedá přečíst."}, status_code=400)
    name = " ".join((req.name or "").split())[:80] or "Riff"
    rows = riffs()
    row = next((r for r in rows if req.id and r.get("id") == req.id), None)
    if row is None:
        row = {"id": uuid.uuid4().hex[:10], "created": time.time()}
        rows.append(row)
    row.update(body, name=name, changed=time.time())
    _save(rows)
    return row


@router.delete("/api/vguitar/riffs/{rid}")
def riff_delete(rid: str):
    rows = riffs()
    if not any(r.get("id") == rid for r in rows):
        return JSONResponse({"error": "Riff nenalezen."}, status_code=404)
    _save([r for r in rows if r.get("id") != rid])
    return {"ok": True}
