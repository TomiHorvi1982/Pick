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
    return {"themes": themes(), "folder": str(root()), "card": root().parent.is_dir(), "jobs": jobs, "pexels": bool(_pexels_key()), "pixabay": bool(_key(PIXABAY_KEY))}


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


# ------------------------------------------------------------------ free videos to choose from
# Wikimedia Commons (public domain and Creative Commons, no key needed) and Pexels (free to use;
# a free key of the player's own, kept here — never sent to the page). Found, looked at in the
# page straight from there, and saved into a theme with who made it and under what licence.
import html as _html
import httpx

PEXELS_KEY = Path.home() / ".local" / "share" / "spotdl-helper" / "pexels-key.txt"
PIXABAY_KEY = Path.home() / ".local" / "share" / "spotdl-helper" / "pixabay-key.txt"
UA = {"User-Agent": "Pick/1.0 (https://github.com/TomiHorvi1982/Pick; personal music app)"}   # Wikimedia asks for who asks
HOSTS = ("upload.wikimedia.org", "videos.pexels.com", "player.vimeo.com", "cdn.pixabay.com")


def _key(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _pexels_key() -> str:
    return _key(PEXELS_KEY)


def _pixabay(q: str, page: int) -> list:
    r = httpx.get("https://pixabay.com/api/videos/", timeout=20, params={"key": _key(PIXABAY_KEY), "q": q, "per_page": 30, "page": page, "safesearch": "true"})
    if r.status_code in (400, 401, 403) and "key" in r.text.lower():
        raise PermissionError("Klíč k Pixabay nefunguje — zkontroluj ho.")
    if r.status_code == 400:
        return []                                    # past the last page
    out = []
    for v in r.json().get("hits") or []:
        vs = v.get("videos") or {}
        best = next((vs[k] for k in ("medium", "large", "small") if (vs.get(k) or {}).get("url")), None)
        if not best or (best.get("width") or 0) < (best.get("height") or 0):
            continue
        out.append({"source": "pixabay", "title": (v.get("tags") or "").split(",")[0].strip().capitalize() or f"Pixabay {v.get('id')}",
                    "thumb": best.get("thumbnail") or "", "url": best["url"], "w": best.get("width"), "dur": v.get("duration"),
                    "author": v.get("user") or "", "license": "Pixabay (zdarma)", "page": v.get("pageURL")})
    return out


def _text(s) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", _html.unescape(str(s or ""))).split())[:120]


def _commons(q: str, page: int) -> list:
    r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=20, params={
        "action": "query", "generator": "search", "gsrsearch": f"filetype:video {q}", "gsrnamespace": 6,
        "gsrlimit": 30, "gsroffset": (page - 1) * 30, "prop": "videoinfo", "format": "json",
        "viprop": "url|size|mime|derivatives|extmetadata", "viurlwidth": 400})
    out = []
    for p in sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda x: x.get("index", 0)):
        v = (p.get("videoinfo") or [{}])[0]
        w, h = v.get("width") or 0, v.get("height") or 0
        if w < h or (v.get("duration") or 0) < 3:
            continue                                     # upright or a moment: no background
        webm = [d for d in v.get("derivatives") or [] if "webm" in (d.get("type") or "") and (d.get("width") or 0) <= 1920]
        best = max(webm, key=lambda d: (d.get("width") or 0) if (d.get("width") or 0) <= 1280 else 0, default=None)
        if not best:
            continue
        m = v.get("extmetadata") or {}
        out.append({"source": "commons", "title": re.sub(r"^File:|\.\w+$", "", p.get("title") or ""), "thumb": v.get("thumburl"),
                    "url": best["src"], "w": best.get("width"), "dur": round(v.get("duration") or 0),
                    "author": _text((m.get("Artist") or {}).get("value")), "license": _text((m.get("LicenseShortName") or {}).get("value")),
                    "page": v.get("descriptionurl")})
    return out


def _pexels(q: str, page: int) -> list:
    r = httpx.get("https://api.pexels.com/videos/search", timeout=20, headers={"Authorization": _pexels_key()},
                  params={"query": q, "per_page": 30, "page": page, "orientation": "landscape"})
    if r.status_code in (401, 403):
        raise PermissionError("Klíč k Pexels nefunguje — zkontroluj ho.")
    out = []
    for v in r.json().get("videos") or []:
        files = [f for f in v.get("video_files") or [] if f.get("file_type") == "video/mp4" and (f.get("width") or 0) <= 1920]
        best = max(files, key=lambda f: (f.get("width") or 0) if (f.get("width") or 0) <= 1280 else 0, default=None)
        if not best:
            continue
        out.append({"source": "pexels", "title": re.sub(r"[-\d]+$", "", (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").strip() or f"Pexels {v.get('id')}",
                    "thumb": v.get("image"), "url": best.get("link"), "w": best.get("width"), "dur": v.get("duration"),
                    "author": (v.get("user") or {}).get("name") or "", "license": "Pexels (zdarma)", "page": v.get("url")})
    return out


@router.get("/api/viz/search")
def viz_search(q: str = "", source: str = "commons", page: int = 1):
    q = " ".join(q.split())[:100]
    if not q:
        return {"items": []}
    try:
        if source == "pixabay":
            if not _key(PIXABAY_KEY):
                return JSONResponse({"error": "Pro Pixabay vlož nejdřív svůj klíč (zdarma na pixabay.com/api/docs)."}, status_code=400)
            return {"items": _pixabay(q, max(1, page))}
        if source == "pexels":
            if not _pexels_key():
                return JSONResponse({"error": "Pro Pexels vlož nejdřív svůj klíč (zdarma na pexels.com/api)."}, status_code=400)
            return {"items": _pexels(q, max(1, page))}
        return {"items": _commons(q, max(1, page))}
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except (httpx.HTTPError, ValueError):
        return JSONResponse({"error": "Hledání teď nejde — jsi připojený k internetu?"}, status_code=502)


class KeyIn(BaseModel):
    key: str = ""


@router.post("/api/viz/pixabay-key")
def viz_pixabay_key(req: KeyIn):
    key = (req.key or "").strip()
    if key and not re.fullmatch(r"[0-9]{3,12}-[0-9a-f]{20,40}", key):
        return JSONResponse({"error": "Tohle nevypadá jako klíč k Pixabay (čísla, pomlčka, písmena)."}, status_code=400)
    PIXABAY_KEY.parent.mkdir(parents=True, exist_ok=True)
    if key:
        PIXABAY_KEY.write_text(key, encoding="utf-8")
        PIXABAY_KEY.chmod(0o600)
    else:
        PIXABAY_KEY.unlink(missing_ok=True)
    return {"pixabay": bool(key)}


@router.post("/api/viz/pexels-key")
def viz_pexels_key(req: KeyIn):
    key = (req.key or "").strip()
    if key and not re.fullmatch(r"[A-Za-z0-9]{20,100}", key):
        return JSONResponse({"error": "Tohle nevypadá jako klíč k Pexels."}, status_code=400)
    PEXELS_KEY.parent.mkdir(parents=True, exist_ok=True)
    if key:
        PEXELS_KEY.write_text(key, encoding="utf-8")
        PEXELS_KEY.chmod(0o600)
    else:
        PEXELS_KEY.unlink(missing_ok=True)
    return {"pexels": bool(key)}


class SaveIn(BaseModel):
    theme: str = ""
    url: str = ""
    title: str = ""
    author: str = ""
    license: str = ""
    page: str = ""


def _save(jid: str, d: Path, req: SaveIn) -> None:
    job = JOBS[jid]
    ext = ".webm" if ".webm" in req.url.lower() else ".mp4"
    base = re.sub(r"[^\w .,()-]+", "", req.title or "video", flags=re.U).strip()[:70] or "video"
    f, n = d / f"{base}{ext}", 2
    while f.exists():
        f, n = d / f"{base} ({n}){ext}", n + 1
    tmp = f.with_name("." + f.name + ".part")
    try:
        for attempt in range(4):                     # "too many requests": wait as asked, then again
            with httpx.stream("GET", req.url, headers=UA, timeout=60, follow_redirects=True) as r:
                if r.status_code == 429 and attempt < 3:
                    job["note"] = "Zdroj chce chvilku počkat…"
                    time.sleep(min(30, int(r.headers.get("retry-after") or 0) or 5 * (attempt + 1)))
                    continue
                r.raise_for_status()
                with tmp.open("wb") as out:
                    for chunk in r.iter_bytes(1 << 16):
                        out.write(chunk)
                break
        tmp.replace(f)
        credits = d / ".credits.json"
        try:
            got = json.loads(credits.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        got[f.name] = {"author": req.author, "license": req.license, "page": req.page}
        credits.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
        job.update(status="done", file=f.name, note="Uloženo.")
    except (httpx.HTTPError, OSError) as e:
        tmp.unlink(missing_ok=True)
        job.update(status="error", note=f"Video se nepodařilo stáhnout ({str(e)[:120]}).")


@router.post("/api/viz/save")
def viz_save(req: SaveIn):
    """A found video into a theme (with who made it and its licence, in the theme's .credits.json)."""
    d = _theme_dir(req.theme)
    if not d:
        return JSONResponse({"error": "Takové téma není."}, status_code=404)
    host = re.match(r"https://([^/]+)/", req.url or "")
    if not host or host.group(1) not in HOSTS:
        return JSONResponse({"error": "Odtud Pick videa neukládá."}, status_code=400)
    jid = uuid.uuid4().hex[:10]
    with _LOCK:
        JOBS[jid] = {"theme": d.name, "url": req.url, "status": "running", "note": "Stahuji…", "file": None, "started": time.time()}
    threading.Thread(target=_save, args=(jid, d, req), daemon=True).start()
    return {"id": jid}
