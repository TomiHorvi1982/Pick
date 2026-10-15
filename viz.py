"""Vizualizace — what plays behind the music: the player's own videos, by theme.

Each theme is a folder in Vizualizace/ beside the music (on the card): any video put
there plays in it, muted, one after another. A video can also be fetched by its link
(yt-dlp, the picture only — the music is what plays). The pictures Pick draws itself,
and the song's own clip, live in the page (static/viz.js); the clip is found here.
"""
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

# set by the server
root = lambda: Path(".")                             # noqa: E731 — Vizualizace/ beside the music
ytdlp = lambda: "yt-dlp"                             # noqa: E731
search_videos = lambda q: []                         # noqa: E731 — YouTube's videos for a query: [{videoId, title}]
CLIPS = Path.home() / ".local" / "share" / "spotdl-helper" / "viz-clips.json"

VIDEO = {".mp4", ".m4v", ".mov", ".webm"}
FIRST = ["Příroda", "Vesmír", "Koncerty", "Abstraktní"]
JOBS: dict = {}                                      # id → {theme, url, status, note, file, started}
_LOCK = threading.Lock()


def _safe(name: str) -> str:
    """A theme's name as a folder: no slashes, no leading dot, not too long."""
    s = re.sub(r"[/\\:\x00]", " ", name or "").strip().lstrip(".")
    return " ".join(s.split())[:60]


def _theme_dir(theme: str):
    t = _safe(theme)
    if not t or t != (theme or "").strip():
        return None
    d = root() / t
    return d if d.is_dir() else None


def themes() -> list:
    base = root()
    if not base.parent.is_dir():                     # the card is not in
        return []
    if not base.exists():                            # the first time: a few themes to put videos into
        for t in FIRST:
            (base / t).mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        vids = sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in VIDEO and not f.name.startswith("."))
        out.append({"name": d.name, "videos": vids})
    return out


@router.get("/api/viz")
def viz_list():
    with _LOCK:
        jobs = [dict(j, id=k) for k, j in JOBS.items() if j["status"] == "running" or time.time() - j["started"] < 600]
    return {"themes": themes(), "folder": str(root()), "card": root().parent.is_dir(), "jobs": jobs}


@router.get("/api/viz/video/{theme}/{name}")
def viz_video(theme: str, name: str):
    d = _theme_dir(theme)
    f = d / name if d and "/" not in name and not name.startswith(".") else None
    if not f or not f.is_file() or f.suffix.lower() not in VIDEO:
        return JSONResponse({"error": "Video nenalezeno."}, status_code=404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


class ThemeIn(BaseModel):
    name: str = ""


@router.post("/api/viz/theme")
def viz_theme(req: ThemeIn):
    t = _safe(req.name)
    if not t:
        return JSONResponse({"error": "Téma potřebuje název."}, status_code=400)
    if not root().parent.is_dir():
        return JSONResponse({"error": "Karta s hudbou není připojená."}, status_code=503)
    (root() / t).mkdir(parents=True, exist_ok=True)
    return {"name": t}


@router.post("/api/viz/open")
def viz_open(req: ThemeIn):
    """The theme's folder in Finder — to put videos into by hand."""
    d = _theme_dir(req.name) if req.name else (root() if root().is_dir() else None)
    if not d:
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)
    subprocess.Popen(["open", str(d)])
    return {"ok": True}


@router.delete("/api/viz/video/{theme}/{name}")
def viz_delete(theme: str, name: str):
    d = _theme_dir(theme)
    f = d / name if d and "/" not in name and not name.startswith(".") else None
    if not f or not f.is_file() or f.suffix.lower() not in VIDEO:
        return JSONResponse({"error": "Video nenalezeno."}, status_code=404)
    f.unlink()
    return {"ok": True}


class FetchIn(BaseModel):
    theme: str = ""
    url: str = ""


def _fetch(jid: str, d: Path, url: str) -> None:
    job = JOBS[jid]
    before = {f.name for f in d.iterdir()}
    cmd = [ytdlp(), "--no-playlist", "--no-part", "--restrict-filenames", "-f",
           "bv*[ext=mp4][height<=1080]/bv*[height<=1080]/b[height<=1080]/b",
           "-o", str(d / "%(title).70s.%(ext)s"), url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        new = sorted(f.name for f in d.iterdir() if f.name not in before and f.suffix.lower() in VIDEO)
        if p.returncode == 0 and new:
            job.update(status="done", file=new[0], note="Staženo.")
        else:
            err = (p.stderr or "").strip().splitlines()
            job.update(status="error", note=(err[-1] if err else "Video se nepodařilo stáhnout.")[:200])
    except subprocess.TimeoutExpired:
        job.update(status="error", note="Stahování trvalo přes půl hodiny — vzdal jsem to.")
    except OSError as e:
        job.update(status="error", note=str(e)[:200])


@router.post("/api/viz/fetch")
def viz_fetch(req: FetchIn):
    d = _theme_dir(req.theme)
    url = (req.url or "").strip()
    if not d:
        return JSONResponse({"error": "Takové téma není."}, status_code=404)
    if not re.match(r"https?://", url):
        return JSONResponse({"error": "Vlož odkaz na video (začíná https://)."}, status_code=400)
    jid = uuid.uuid4().hex[:10]
    with _LOCK:
        JOBS[jid] = {"theme": d.name, "url": url, "status": "running", "note": "Stahuji…", "file": None, "started": time.time()}
    threading.Thread(target=_fetch, args=(jid, d, url), daemon=True).start()
    return {"id": jid}


def _clips() -> dict:
    try:
        got = json.loads(CLIPS.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


@router.get("/api/viz/clip")
def viz_clip(title: str = "", artist: str = ""):
    """The song's own clip on YouTube — its id, found once and kept."""
    title, artist = " ".join(title.split()), " ".join(artist.split(",")[0].split())
    if not title:
        return {"videoId": None}
    key = f"{artist.lower()}|{title.lower()}"
    kept = _clips()
    if key in kept:
        return {"videoId": kept[key]}
    try:
        found = search_videos(f"{artist} {title} official video".strip())
    except Exception:                                # noqa: BLE001 — offline: no clip this time, asked again later
        return {"videoId": None}
    vid = next((x.get("videoId") for x in found if x.get("videoId")), None)
    kept[key] = vid
    CLIPS.parent.mkdir(parents=True, exist_ok=True)
    CLIPS.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return {"videoId": vid}
