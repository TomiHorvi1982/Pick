"""
Lokální GUI server pro spotdl.

Backend pro procházení playlistů (podle žánru/éry) a skladeb, náhled písní
přes vložené YouTube video, odhad velikosti stažení a spouštění samotného
stahování přes spotdl s živým logem (Server-Sent Events).

Běží jen na 127.0.0.1 — není dostupný ze sítě.
"""

import asyncio
import json
import mimetypes
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

HOME = Path.home()
APP_DIR = Path(__file__).parent
SPOTDL_BIN = str(HOME / ".local" / "bin" / "spotdl")
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"}
CONFIG_PATH = HOME / ".local" / "share" / "spotdl-helper" / "config.json"
DEFAULT_MUSIC_ROOT = HOME / "Music" / "spotdl"


def _load_music_root() -> Path:
    """Reads the configured download folder from CONFIG_PATH, falling back
    to the original hardcoded default when there's no config yet (first
    run) or it's unreadable.
    """
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw = data.get("music_root")
        if raw:
            return Path(raw).expanduser()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return DEFAULT_MUSIC_ROOT


MUSIC_ROOT = _load_music_root()

app = FastAPI(title="spotdl GUI")

# ------------------------------------------------------------ lazy clients

_yt = None      # default / international locale
_yt_cz = None   # Czech locale — genuinely biases YT Music search results
_sp = None


def yt():
    global _yt
    if _yt is None:
        from ytmusicapi import YTMusic

        _yt = YTMusic()
    return _yt


def yt_cz():
    global _yt_cz
    if _yt_cz is None:
        from ytmusicapi import YTMusic

        _yt_cz = YTMusic(language="cs", location="CZ")
    return _yt_cz


def sp():
    global _sp
    if _sp is None:
        from spotdl.utils.spotify import SpotifyClient, SpotifyError

        try:
            _sp = SpotifyClient()
        except SpotifyError:
            SpotifyClient.init(client_id="placeholder", client_secret="placeholder")
            _sp = SpotifyClient()
    return _sp


def _best_thumb(thumbs) -> Optional[str]:
    if not thumbs:
        return None
    return max(thumbs, key=lambda t: t.get("width", 0)).get("url")


def _fmt_dur(total_seconds: int) -> str:
    total_seconds = int(total_seconds or 0)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"ok": True}


# ----------------------------------------------------------------- browse

@app.get("/api/categories")
def categories():
    """Žánry a nálady/období pro úvodní 'brouzdací' mřížku."""
    cats = yt().get_mood_categories()
    return {
        "genres": [c["title"] for c in cats.get("Genres", [])],
        "moods": [c["title"] for c in cats.get("Moods & moments", [])],
    }


@app.get("/api/playlists")
def search_playlists(q: str, limit: int = 40, region: str = "intl"):
    client = yt_cz() if region == "cz" else yt()
    results = client.search(q, filter="playlists", limit=limit)
    out = []
    for r in results:
        bid = r.get("browseId")
        if not bid:
            continue
        out.append(
            {
                "browseId": bid,
                "title": r.get("title"),
                "author": r.get("author"),
                "thumbnail": _best_thumb(r.get("thumbnails")),
            }
        )
    return out


@app.get("/api/playlist/{browse_id}")
def playlist_detail(browse_id: str, track_limit: int = 100):
    pid = browse_id[2:] if browse_id.startswith("VL") else browse_id
    pl = yt().get_playlist(pid, limit=track_limit)

    tracks = []
    total_shown_sec = 0
    for t in pl.get("tracks", []):
        if not t.get("videoId"):
            continue
        dur = t.get("duration_seconds") or 0
        total_shown_sec += dur
        artists = ", ".join(a["name"] for a in (t.get("artists") or []) if a.get("name"))
        album = t.get("album")
        tracks.append(
            {
                "videoId": t["videoId"],
                "title": t.get("title"),
                "artists": artists,
                "album": album.get("name") if isinstance(album, dict) else None,
                "duration": t.get("duration") or _fmt_dur(dur),
                "duration_seconds": dur,
                "thumbnail": _best_thumb(t.get("thumbnails")),
            }
        )

    total_count = pl.get("trackCount") or len(tracks)
    avg_sec = (total_shown_sec / len(tracks)) if tracks else 0
    est_total_sec = avg_sec * total_count

    author = pl.get("author")
    return {
        "browseId": browse_id,
        "title": pl.get("title"),
        "author": author.get("name") if isinstance(author, dict) else author,
        "description": pl.get("description"),
        "thumbnail": _best_thumb(pl.get("thumbnails")),
        "track_count": total_count,
        "tracks_shown": len(tracks),
        "total_duration_shown_sec": total_shown_sec,
        "estimated_total_duration_sec": round(est_total_sec),
        "tracks": tracks,
    }


@app.get("/api/tracks")
def search_tracks(q: str, limit: int = 40):
    res = sp().search(q, type="track")
    items = res.get("tracks", {}).get("items", [])[:limit]
    out = []
    for t in items:
        images = (t.get("album") or {}).get("images") or []
        image = max(images, key=lambda i: i.get("width", 0)).get("url") if images else None
        out.append(
            {
                "id": t.get("track_id") or t.get("id"),
                "title": t.get("name"),
                "artists": ", ".join(a["name"] for a in t.get("artists", [])),
                "album": (t.get("album") or {}).get("name"),
                "duration_seconds": round((t.get("duration_ms") or 0) / 1000),
                "spotify_url": (t.get("external_urls") or {}).get("spotify"),
                "image": image,
            }
        )
    return out


_ARTIST_SEARCH_CACHE: dict = {}
_ARTIST_CACHE_TTL = 5 * 60


@app.get("/api/artists")
def search_artists(q: str, offset: int = 0, limit: int = 15):
    """Artist-name hints for the Songs tab — used to suggest bands/artists
    matching whatever's typed or picked via the decade/genre chips.

    Paginated so the GUI can infinite-scroll through it: most relevant (i.e.
    most famous, per YT Music's own ranking) artists first, then progressively
    less-known ones as the user keeps scrolling, instead of a fixed handful.

    ytmusicapi's search() has no resumable cursor — each call re-runs the
    whole (paginated) YT Music search from scratch up to `limit` — so results
    per query are cached briefly and only re-fetched with a wider `limit`
    when the caller actually scrolls past what's cached.

    Uses YouTube Music rather than Spotify: the (keyless) SpotifyClient this
    app runs on is an unofficial wrapper that only ever returns tracks —
    it silently ignores type="artist" — while ytmusicapi supports it directly.
    """
    key = q.strip().lower()
    now = time.time()
    need = offset + limit

    cached = _ARTIST_SEARCH_CACHE.get(key)
    if cached and now - cached["ts"] > _ARTIST_CACHE_TTL:
        cached = None
    # a previous fetch already came up short of what it asked for -> that's
    # every artist YT Music has for this query, no point asking again
    exhausted = cached is not None and len(cached["items"]) < cached["fetched_limit"]

    if cached is None or (len(cached["items"]) < need and not exhausted):
        fetch_limit = max(need, 15)
        items = yt().search(q, filter="artists", limit=fetch_limit)
        cached = {"ts": now, "items": items, "fetched_limit": fetch_limit}
        _ARTIST_SEARCH_CACHE[key] = cached

    page = cached["items"][offset : offset + limit]
    out = [
        {"name": a.get("artist"), "browseId": a.get("browseId"), "image": _best_thumb(a.get("thumbnails"))}
        for a in page
    ]
    return {"artists": out, "hasMore": offset + limit < len(cached["items"])}


_library_cache = {"ts": 0.0, "stems": []}


@app.get("/api/library")
def library():
    """Vrátí názvy už stažených souborů (bez přípony), aby GUI mohlo
    v seznamech naznačit 'tohle už máš' a nešlo se stahovat 2x."""
    now = time.time()
    if now - _library_cache["ts"] < 10 and _library_cache["stems"]:
        return {"stems": _library_cache["stems"]}

    stems = []
    if MUSIC_ROOT.exists():
        for p in MUSIC_ROOT.rglob("*"):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                stems.append(p.stem.lower())

    _library_cache["ts"] = now
    _library_cache["stems"] = stems
    return {"stems": stems}


# ----------------------------------------------------------------- browse (local files)


def _resolve_under_music_root(rel: str) -> Optional[Path]:
    """Resolves `rel` under MUSIC_ROOT, refusing anything that would escape
    it — a leading '/' (pathlib silently drops the left side when you `/`
    it with an absolute path, so this has to be checked explicitly), '..'
    segments, or a symlink pointing outside. Returns None on any violation.
    """
    if not rel:
        return MUSIC_ROOT.resolve()
    if Path(rel).is_absolute():
        return None
    try:
        candidate = (MUSIC_ROOT / rel).resolve()
    except (ValueError, OSError):
        # ValueError: embedded null character in path
        # OSError: other filesystem-related errors during resolve
        return None
    root = MUSIC_ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


@app.get("/api/library/browse")
def library_browse(rel: str = ""):
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_dir():
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)

    root = MUSIC_ROOT.resolve()
    parts = target.relative_to(root).parts
    breadcrumb = [{"name": root.name or str(root), "rel": ""}]
    acc = []
    for part in parts:
        acc.append(part)
        breadcrumb.append({"name": part, "rel": "/".join(acc)})

    dirs, files = [], []
    for p in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        entry_rel = "/".join((*parts, p.name))
        if p.is_dir():
            dirs.append({"name": p.name, "type": "dir", "rel": entry_rel})
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append({
                "name": p.name, "type": "file", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
            })

    return {"rel": "/".join(parts), "breadcrumb": breadcrumb, "entries": dirs + files}


def _range_file_response(path: Path, range_header: Optional[str]):
    """Serves a local file with byte-range support so <audio>/<video> can
    seek. Starlette 0.27.0 (pinned in this venv) doesn't implement Range on
    its own FileResponse, so this fills that gap by hand — same idea as the
    existing Range-forwarding in _proxy_audio_response, just reading from
    disk instead of an upstream response.
    """
    size = path.stat().st_size
    start, end = 0, size - 1
    status_code = 200
    if range_header and range_header.startswith("bytes="):
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        try:
            if start_s:
                start = int(start_s)
            if end_s:
                end = int(end_s)
        except ValueError:
            return JSONResponse({"error": "Neplatný Range požadavek."}, status_code=416)
        end = min(end, size - 1)
        if start > end:
            return JSONResponse({"error": "Neplatný Range požadavek."}, status_code=416)
        status_code = 206

    length = end - start + 1
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    def body():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "accept-ranges": "bytes",
        "content-length": str(length),
        "cache-control": "no-store",
        "content-type": content_type,
    }
    if status_code == 206:
        headers["content-range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(body(), status_code=status_code, headers=headers)


@app.get("/api/library/stream")
def library_stream(rel: str, request: Request):
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _range_file_response(target, request.headers.get("range"))


@app.get("/api/preview")
def preview(q: str):
    """Najde nejbližší YouTube video pro náhled (u výsledků hledání písní,
    kde ještě nemáme videoId — u playlistů ho už máme rovnou)."""
    res = yt().search(q, filter="songs", limit=1)
    if not res:
        res = yt().search(q, filter="videos", limit=1)
    return {"videoId": res[0]["videoId"] if res else None}


# ------------------------------------------------------------ preview stream

# YouTube's iframe embed blocks a lot of official music videos ("video
# unavailable" — embedding disabled by request), which is why the old preview
# button showed nothing. Instead we resolve a direct media URL with yt-dlp
# (same tool spotdl already uses for downloads) and proxy the bytes straight
# to the browser's own <audio>/<video> element — no embedding involved.
_STREAM_CACHE: dict = {}
_STREAM_CACHE_TTL = 45 * 60  # googlevideo links are typically valid for hours; be conservative


def _resolve_audio(video_id: str):
    """Direct audio-only stream URL — proxied byte-for-byte, supports Range/seeking.

    Deliberately never uses the browser-cookies setting: that setting exists for
    the occasional download that hits YouTube's bot-check, and forwarding it to
    every single preview click made yt-dlp decrypt Chrome's cookies (via macOS
    Keychain) on every play — which is what caused the repeated Keychain popups.
    """
    cache_key = ("audio", video_id)
    cached = _STREAM_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp

    ydl_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    result = {
        "url": info["url"],
        "headers": info.get("http_headers") or {},
        "expires": time.time() + _STREAM_CACHE_TTL,
    }
    _STREAM_CACHE[cache_key] = result
    return result


def _resolve_video(video_id: str):
    """Progressive (single file, video+audio combined) stream — good enough
    for the in-app video toggle, not used for downloads. Combined
    progressive formats top out well below 1080p on YouTube these days;
    that's fine here.
    """
    cache_key = ("video", video_id)
    cached = _STREAM_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp

    ydl_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "best[ext=mp4]/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    result = {
        "url": info["url"],
        "headers": info.get("http_headers") or {},
        "expires": time.time() + _STREAM_CACHE_TTL,
    }
    _STREAM_CACHE[cache_key] = result
    return result


async def _proxy_media_response(request: Request, video_id: str, resolve, cache_key, default_content_type: str):
    try:
        info = await asyncio.to_thread(resolve, video_id)
    except Exception as e:
        return JSONResponse({"error": f"Náhled se nepodařilo najít ({e})"}, status_code=502)

    upstream_headers = dict(info["headers"])
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0)
    try:
        req = client.build_request("GET", info["url"], headers=upstream_headers)
        upstream = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        return JSONResponse({"error": f"Streamování selhalo ({e})"}, status_code=502)

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        _STREAM_CACHE.pop(cache_key, None)  # link may have expired — drop it so the next click re-resolves
        return JSONResponse({"error": "YouTube odmítlo přehrání (zkus to znovu)"}, status_code=upstream.status_code)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    out_headers = {"accept-ranges": "bytes", "cache-control": "no-store"}
    for h in ("content-length", "content-range", "content-type"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]
    out_headers.setdefault("content-type", default_content_type)

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


async def _proxy_audio_response(request: Request, video_id: str):
    return await _proxy_media_response(request, video_id, _resolve_audio, ("audio", video_id), "audio/mp4")


async def _proxy_video_response(request: Request, video_id: str):
    return await _proxy_media_response(request, video_id, _resolve_video, ("video", video_id), "video/mp4")


@app.get("/api/stream/{video_id}")
async def stream(video_id: str, request: Request):
    return await _proxy_audio_response(request, video_id)


@app.get("/api/stream-video/{video_id}")
async def stream_video(video_id: str, request: Request):
    return await _proxy_video_response(request, video_id)


# --------------------------------------------------------------- download

JOBS: dict = {}


class DownloadRequest(BaseModel):
    urls: List[str]
    output_name: str
    kind: str = "generic"  # "playlist" | "songs" | "queue" | "generic"
    bitrate: str = "192k"
    format: str = "mp3"
    cookies_browser: Optional[str] = None  # "chrome" | "safari" | "brave" | ...
    cookie_file: Optional[str] = None


def _safe_name(name: str) -> str:
    name = "".join(c for c in (name or "") if c.isalnum() or c in " -_()[]").strip()
    return name[:80] or "playlist"


@app.post("/api/download")
def start_download(req: DownloadRequest):
    job_id = uuid.uuid4().hex[:12]
    safe = _safe_name(req.output_name)

    if req.kind == "playlist":
        out_dir = MUSIC_ROOT / "Playlists" / safe
        out_template = str(out_dir / "{artist} - {title}.{output-ext}")
    elif req.kind == "songs":
        out_dir = MUSIC_ROOT / "Songs"
        out_template = str(out_dir / "{artist}" / "{title}.{output-ext}")
    elif req.kind == "queue":
        out_dir = MUSIC_ROOT / "Playlist"
        out_template = str(out_dir / "{artist} - {title}.{output-ext}")
    else:
        out_dir = MUSIC_ROOT
        out_template = str(out_dir / "{artist}" / "{album}" / "{title}.{output-ext}")

    cmd = [
        SPOTDL_BIN,
        "download",
        *req.urls,
        "--output",
        out_template,
        "--format",
        req.format,
        "--bitrate",
        req.bitrate,
    ]

    if req.cookie_file:
        cmd += ["--cookie-file", req.cookie_file]
    elif req.cookies_browser:
        # Lets yt-dlp read the session cookies straight from an installed
        # browser, needed when YouTube shows "confirm you're not a bot".
        # The browser must be fully closed while this runs.
        cmd += ["--yt-dlp-args", f"--cookies-from-browser {req.cookies_browser}"]

    JOBS[job_id] = {
        "cmd": cmd,
        "status": "queued",
        "lines": [],
        "output_dir": str(out_dir),
    }
    return {"job_id": job_id, "output_dir": str(out_dir)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    async def gen():
        if job["status"] == "queued":
            job["status"] = "running"
            proc = await asyncio.create_subprocess_exec(
                *job["cmd"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            job["proc"] = proc
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                job["lines"].append(text)
                yield f"data: {json.dumps({'line': text})}\n\n"
            rc = await proc.wait()
            job["status"] = "done" if rc == 0 else "error"
            yield f"data: {json.dumps({'done': True, 'returncode': rc})}\n\n"
        else:
            for text in job["lines"]:
                yield f"data: {json.dumps({'line': text})}\n\n"
            yield f"data: {json.dumps({'done': True, 'status': job['status']})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class RevealRequest(BaseModel):
    path: str


@app.post("/api/reveal")
def reveal(req: RevealRequest):
    Path(req.path).mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", req.path], check=False)
    return {"ok": True}


# --------------------------------------------------------------- settings


class SettingsRequest(BaseModel):
    music_root: str


@app.get("/api/settings")
def get_settings():
    return {"music_root": str(MUSIC_ROOT)}


@app.post("/api/settings")
def update_settings(req: SettingsRequest):
    global MUSIC_ROOT
    path = Path(req.music_root).expanduser()
    if not path.is_absolute():
        return JSONResponse({"error": "Cesta musí být absolutní."}, status_code=400)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return JSONResponse({"error": f"Složku nejde vytvořit ({e})."}, status_code=400)
    if not os.access(path, os.W_OK):
        return JSONResponse({"error": "Do složky se nedá zapisovat."}, status_code=400)

    MUSIC_ROOT = path
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"music_root": str(path)}), encoding="utf-8")
    return {"music_root": str(MUSIC_ROOT)}


@app.post("/api/settings/pick-folder")
def pick_folder():
    """Native macOS folder picker via AppleScript — the browser sandbox
    can't hand back an absolute filesystem path itself.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", "POSIX path of (choose folder)"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"path": None}
    if result.returncode != 0:
        return {"path": None}  # user cancelled the dialog
    return {"path": result.stdout.strip()}
