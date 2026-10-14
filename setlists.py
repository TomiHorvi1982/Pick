"""Steems playlists — songs of Steems played one after another, each with its own mix.

A playlist is only a name and an order of Steems songs (their folder names);
what each song sounds like — faders, mutes, pitch, loop — is already kept with
the song (session.json), so playing the list is opening one song after another.
Kept beside the app's settings, one small file.
"""
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PATH = Path.home() / ".local" / "share" / "spotdl-helper" / "steems-playlists.json"
router = APIRouter()
_LOCK = threading.Lock()


def load() -> list:
    try:
        got = json.loads(PATH.read_text(encoding="utf-8"))
        return [p for p in got if isinstance(p, dict) and p.get("id")] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def _clean(name: str) -> str:
    return " ".join((name or "").split())[:80]


class SetlistIn(BaseModel):
    name: str = ""
    songs: List[str] = []


@router.get("/api/setlists")
def setlists():
    return load()


@router.post("/api/setlists")
def setlist_new(req: SetlistIn):
    name = _clean(req.name)
    if not name:
        return JSONResponse({"error": "Playlist potřebuje název."}, status_code=400)
    with _LOCK:
        rows = load()
        if any(p["name"].lower() == name.lower() for p in rows):
            return JSONResponse({"error": f"Playlist „{name}“ už je."}, status_code=409)
        p = {"id": uuid.uuid4().hex[:10], "name": name, "songs": [s for s in req.songs if s][:300], "created": time.time()}
        rows.append(p)
        _save(rows)
    return p


@router.put("/api/setlists/{sid}")
def setlist_set(sid: str, req: SetlistIn):
    """The whole playlist as it is now: its name, and its songs in their order."""
    with _LOCK:
        rows = load()
        p = next((x for x in rows if x["id"] == sid), None)
        if p is None:
            return JSONResponse({"error": "Takový playlist tu není."}, status_code=404)
        name = _clean(req.name) or p["name"]
        if any(x["id"] != sid and x["name"].lower() == name.lower() for x in rows):
            return JSONResponse({"error": f"Playlist „{name}“ už je."}, status_code=409)
        p.update(name=name, songs=[s for s in req.songs if s and re.match(r"^[^/\\]+$", s)][:300])
        _save(rows)
    return p


@router.delete("/api/setlists/{sid}")
def setlist_delete(sid: str):
    with _LOCK:
        rows = load()
        _save([x for x in rows if x["id"] != sid])
    return {"ok": True}
