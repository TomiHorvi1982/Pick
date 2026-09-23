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
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, unquote

import httpx
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

HOME = Path.home()
APP_DIR = Path(__file__).parent
SPOTDL_BIN = str(HOME / ".local" / "bin" / "spotdl")


def ffmpeg_bin() -> str:
    """Where ffmpeg is. PATH first, then the environment override a packaged
    build sets, then the Homebrew path this machine happens to use."""
    return (os.environ.get("SPOTIFYLISTY_FFMPEG")
            or shutil.which("ffmpeg")
            or "/opt/homebrew/bin/ffmpeg")
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"}
CONFIG_PATH = HOME / ".local" / "share" / "spotdl-helper" / "config.json"
DEFAULT_MUSIC_ROOT = HOME / "Music" / "spotdl"
LIBRARY_PATH = HOME / "Music" / "spotdl" / "library.json"


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_config(patch: dict) -> dict:
    """Merges patch into config.json rather than replacing it — several
    unrelated settings (music root, video mode, amp category folders) all
    live in this one file, so a plain overwrite would silently erase
    whichever of them wasn't part of the current save.
    """
    data = _read_config()
    data.update(patch)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


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

# Only ever a plain file name from our own worklets folder.
import re as _re_mod
_re_worklet = _re_mod.compile(r"[a-z0-9_-]+\.js")
# take-1, take-2, … and nothing else that could walk out of the folder.
_re_take = _re_mod.compile(r"take-\d{1,4}")
_re_nam_asset = _re_mod.compile(r"[A-Za-z0-9_.-]+\.(js|mjs|wasm)")

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


@app.get("/static/stretch/{name}")
def stretch_asset(name: str):
    """The vendored Signalsmith Stretch module — one file, wasm included."""
    if not _re_nam_asset.fullmatch(name):
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "static" / "stretch" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    return Response(path.read_bytes(), media_type="text/javascript",
                    headers={"Cache-Control": "public, max-age=604800"})


_re_ladicka_asset = _re_mod.compile(r"[A-Za-z0-9_.-]+\.(js|txt|md)")


@app.get("/static/ladicka/{name}")
def ladicka_asset(name: str):
    """The vendored tuner web component (one file) and its licence."""
    if not _re_ladicka_asset.fullmatch(name):
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "static" / "ladicka" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    media = "text/javascript" if name.endswith(".js") else "text/plain; charset=utf-8"
    return Response(path.read_bytes(), media_type=media,
                    headers={"Cache-Control": "no-cache"})


_re_alphatab_asset = _re_mod.compile(r"(font/)?[A-Za-z0-9_.-]+\.(js|mjs|woff2?|otf|eot|svg|txt)")


@app.get("/static/alphatab/{name:path}")
def alphatab_asset(name: str):
    """The vendored alphaTab engine and its Bravura font files.

    Self-hosted for the same reason NAM's worklet is: nothing here calls out
    to a CDN, so this has to come from our own origin too. `{name:path}`
    lets this also serve the nested font/ files, not just the top level.
    """
    if not _re_alphatab_asset.fullmatch(name) or ".." in name:
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "static" / "alphatab" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    types = {
        ".js": "text/javascript", ".mjs": "text/javascript",
        ".woff2": "font/woff2", ".woff": "font/woff",
        ".otf": "font/otf", ".eot": "application/vnd.ms-fontobject",
        ".svg": "image/svg+xml", ".txt": "text/plain",
    }
    return Response(
        path.read_bytes(),
        media_type=types.get(path.suffix, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/static/nam/{name}")
def nam_asset(name: str):
    """The vendored NAM engine: its worklet, its glue and its wasm.

    The worklet and the wasm both have to come from our own origin — a
    worklet module cannot be loaded cross-origin, and the engine fetches the
    binary itself from a path relative to the glue.
    """
    if not _re_nam_asset.fullmatch(name):
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "static" / "nam" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    types = {".js": "text/javascript", ".wasm": "application/wasm"}
    return Response(
        path.read_bytes(),
        media_type=types.get(path.suffix, "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


# ------------------------------------------------------------- amp library
#
# Models and cabinet impulse responses the listener has collected, kept once
# for the whole app rather than per song: an amp is a thing you own, not a
# property of one track.

AMPS_DIR = HOME / ".local" / "share" / "spotdl-helper" / "amps"
AMP_EXTS = {".nam": "model", ".wav": "ir"}
MAX_AMP_BYTES = 80 * 1024 * 1024

# Live-guitar preset browsing groups NAM models into three tone categories.
# Each has its own subfolder under AMPS_DIR for uploads, and can additionally
# be pointed at an external folder on disk (config-only — never copied) for
# packs downloaded elsewhere.
AMP_CATEGORIES = ["clean", "crunch", "hi_gain"]


def _amp_category_dir(cat: str) -> Optional[Path]:
    """The external folder mapped to `cat`, if one is set and still exists.
    Never trusts a path from the client directly — only this config-stored,
    server-validated one, keyed by a category checked against the fixed list."""
    dirs = _read_config().get("amp_category_dirs") or {}
    raw = dirs.get(cat)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _resolve_amp_path(filename: str) -> Optional[Path]:
    """Resolves a client-supplied amp/IR file id to a real path on disk.
    The client only ever gets to name a category (checked against
    AMP_CATEGORIES) and a bare filename (basename only) — the actual
    directory always comes from the server side, so this can't be used to
    walk out of the amps folder or the one mapped external directory.
    """
    if filename.startswith("ext:"):
        rest = filename[len("ext:"):]
        if "/" not in rest:
            return None
        cat, name = rest.split("/", 1)
        if cat not in AMP_CATEGORIES:
            return None
        base = _amp_category_dir(cat)
        if base is None:
            return None
        path = base / Path(name).name
        return path if path.resolve().parent == base.resolve() else None
    if "/" in filename:
        cat, name = filename.split("/", 1)
        if cat not in AMP_CATEGORIES:
            return None
        base = AMPS_DIR / cat
        path = base / Path(name).name
        return path if path.resolve().parent == base.resolve() else None
    path = AMPS_DIR / Path(filename).name
    return path if path.resolve().parent == AMPS_DIR.resolve() else None


@app.get("/api/amps")
def list_amps(category: str = ""):
    if category:
        if category not in AMP_CATEGORIES:
            return JSONResponse({"error": "Neplatná kategorie."}, status_code=400)
        out = []
        cat_dir = AMPS_DIR / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(cat_dir.glob("*.nam")):
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            out.append({"name": path.stem, "file": f"{category}/{path.name}",
                        "kind": "model", "bytes": size, "category": category})
        ext_dir = _amp_category_dir(category)
        if ext_dir is not None:
            for path in sorted(ext_dir.glob("*.nam")):
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                out.append({"name": path.stem, "file": f"ext:{category}/{path.name}",
                            "kind": "model", "bytes": size, "category": category,
                            "external": True})
        return out

    AMPS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(AMPS_DIR.glob("*")):
        kind = AMP_EXTS.get(path.suffix.lower())
        if not kind or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        out.append({"name": path.stem, "file": path.name, "kind": kind,
                    "bytes": size})
    return out


@app.post("/api/amps")
async def add_amp(request: Request, filename: str = "", category: str = ""):
    """Store one .nam model or .wav impulse response. `category` (clean /
    crunch / hi_gain) files it under AMPS_DIR/<category>/ instead of the
    flat top-level folder — only .nam models are categorized, cabinets stay
    uncategorized."""
    suffix = Path(filename).suffix.lower()
    if suffix not in AMP_EXTS:
        return JSONResponse(
            {"error": "Podporované jsou jen .nam modely a .wav impulsy."},
            status_code=400)
    if category and (suffix != ".nam" or category not in AMP_CATEGORIES):
        return JSONResponse({"error": "Neplatná kategorie."}, status_code=400)
    raw = await request.body()
    if not raw:
        return JSONResponse({"error": "Prázdný soubor."}, status_code=400)
    if len(raw) > MAX_AMP_BYTES:
        return JSONResponse({"error": "Soubor je příliš velký."}, status_code=413)

    target_dir = (AMPS_DIR / category) if category else AMPS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(Path(filename).stem) + suffix
    target = target_dir / safe
    # Never let a name walk out of the folder, whatever the browser sent.
    if target.resolve().parent != target_dir.resolve():
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    file_id = f"{category}/{target.name}" if category else target.name
    return {"name": target.stem, "file": file_id,
            "kind": AMP_EXTS[suffix], "bytes": len(raw)}


@app.get("/api/amps/{filename:path}")
def get_amp(filename: str):
    if Path(filename).suffix.lower() not in AMP_EXTS:
        return JSONResponse({"error": "Neplatný soubor."}, status_code=400)
    path = _resolve_amp_path(filename)
    if path is None or not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    types = {".nam": "application/json", ".wav": "audio/wav"}
    return Response(path.read_bytes(),
                    media_type=types[path.suffix.lower()])


@app.delete("/api/amps/{filename:path}")
def delete_amp(filename: str):
    if Path(filename).suffix.lower() not in AMP_EXTS:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    path = _resolve_amp_path(filename)
    if path is None or not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    try:
        path.unlink()
    except OSError as exc:
        return JSONResponse({"error": f"Smazání selhalo: {exc}"}, status_code=500)
    return {"ok": True}


class AmpCategoryDirRequest(BaseModel):
    category: str
    path: str = ""


@app.get("/api/settings/amp-category-dirs")
def get_amp_category_dirs():
    dirs = _read_config().get("amp_category_dirs") or {}
    return {cat: dirs.get(cat) for cat in AMP_CATEGORIES}


@app.post("/api/settings/amp-category-dir")
def set_amp_category_dir(req: AmpCategoryDirRequest):
    if req.category not in AMP_CATEGORIES:
        return JSONResponse({"error": "Neplatná kategorie."}, status_code=400)
    dirs = dict(_read_config().get("amp_category_dirs") or {})
    raw = req.path.strip()
    if not raw:
        dirs.pop(req.category, None)
        _write_config({"amp_category_dirs": dirs})
        return {"category": req.category, "path": None}
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return JSONResponse({"error": "Cesta musí být absolutní."}, status_code=400)
    if not path.is_dir():
        return JSONResponse({"error": "Složka neexistuje."}, status_code=400)
    dirs[req.category] = str(path)
    _write_config({"amp_category_dirs": dirs})
    return {"category": req.category, "path": str(path)}


@app.get("/static/worklets/{name}")
def audio_worklet(name: str):
    """AudioWorklet modules.

    Served from a route rather than a mounted directory because the rest of
    the app is a single HTML file and there is nothing else static to serve —
    and because a worklet has to come from our own origin to load at all.
    """
    if not _re_worklet.fullmatch(name):
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "static" / "worklets" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    return Response(path.read_text(encoding="utf-8"),
                    media_type="text/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    return {"ok": True}


# ----------------------------------------------------------------- browse

# YouTube Music's own split between a genre and a mood is not quite the one a
# listener would make: "Autumn" and "Family" sit under Genres, where they read
# as mistakes next to Jazz and Metal. "Decades" is neither — it is a browse
# axis, and the page already has a Dekády shelf of its own.
MOODS_NOT_GENRES = {"Autumn", "Family"}
NOT_A_CATEGORY = {"Decades"}


@app.get("/api/categories")
def categories():
    """Žánry a nálady/období pro úvodní 'brouzdací' mřížku."""
    cats = yt().get_mood_categories()
    genres, moods = [], []
    for c in cats.get("Genres", []):
        title = c.get("title")
        if not title or title in NOT_A_CATEGORY:
            continue
        (moods if title in MOODS_NOT_GENRES else genres).append(title)
    for c in cats.get("Moods & moments", []):
        title = c.get("title")
        if title and title not in NOT_A_CATEGORY:
            moods.append(title)
    return {"genres": genres, "moods": sorted(moods)}


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


def _album_as_playlist(client, browse_id: str):
    """get_album's result reshaped to look like a playlist.

    The detail page renders one shape; albums arrive in another, with the
    artist at the top level rather than per track.
    """
    try:
        album = client.get_album(browse_id)
    except Exception:
        return None

    artists = album.get("artists") or []
    album_artist = ", ".join(a["name"] for a in artists if a.get("name"))
    tracks = []
    for t in album.get("tracks") or []:
        t = dict(t)
        # Album tracks often omit both, inheriting them from the release.
        if not t.get("artists") and artists:
            t["artists"] = artists
        if not t.get("album"):
            t["album"] = {"name": album.get("title")}
        if not t.get("thumbnails"):
            t["thumbnails"] = album.get("thumbnails")
        tracks.append(t)

    return {
        "title": album.get("title"),
        "author": album_artist,
        "description": album.get("description"),
        "thumbnails": album.get("thumbnails"),
        "trackCount": album.get("trackCount") or len(tracks),
        "tracks": tracks,
    }


@app.get("/api/playlist/{browse_id}")
def playlist_detail(browse_id: str, track_limit: int = 100):
    pid = browse_id[2:] if browse_id.startswith("VL") else browse_id
    client = yt()

    # An album is not a playlist: get_playlist raises on the response shape
    # YouTube returns for one, which is what made an album card from the
    # artist panel 500. "MPREb_" ids are albums; anything else that fails as
    # a playlist gets the same second chance.
    if pid.startswith("MPREb"):
        pl = _album_as_playlist(client, pid)
    else:
        try:
            pl = client.get_playlist(pid, limit=track_limit)
        except Exception:
            pl = _album_as_playlist(client, pid)
    if pl is None:
        return JSONResponse({"error": "Playlist nenalezen."}, status_code=404)

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


# ------------------------------------------- bands & artists, by criterion
#
# One YouTube Music artist search only ever returns a couple of dozen names,
# and a narrow query ("české death metal kapely") far fewer. To keep a shelf
# scrollable, a criterion is asked in several phrasings, one after another as
# the reader scrolls; each phrasing's hits are appended, duplicates dropped.
# YT Music ranks by its own popularity, so within a phrasing the order is
# already most-famous-first.
_BROWSE_ARTISTS_CACHE: dict = {}
_BROWSE_TTL = 3600
# YT Music files playlists-as-channels ("90s Hits", "DJ Party Mix") under artists.
_NOT_AN_ARTIST = _re_mod.compile(
    r"\b(hits|mix|playlist|party|radio|music for|compilation|top \d+|best of|"
    r"greatest|karaoke|instrumental versions?)\b", _re_mod.I)
_BROWSE_INTL = ["{q}", "{q} bands", "best {q} artists", "{q} singers",
                "{q} legends", "new {q} artists", "underground {q}"]
_BROWSE_CZ = ["české {q} kapely", "česká {q} hudba interpreti", "czech {q} band",
              "čeští {q} zpěváci", "{q} czech artists", "české {q} legendy",
              "slovenské {q} kapely", "nejlepší čeští {q} interpreti",
              "česká {q} scéna", "mladé české {q} kapely",
              "český {q} underground", "moravské {q} kapely"]


@app.get("/api/artists/browse")
def browse_artists(q: str, region: str = "intl", offset: int = 0, limit: int = 15,
                   cap: int = 0):
    """Artists matching a decade / genre / mood, for the horizontal shelves.

    `cap` limits the whole list (the "Top 20" shelf ends at twenty).
    """
    q = q.strip()
    if not q:
        return JSONResponse({"error": "Chybí kritérium."}, status_code=400)
    region = "cz" if region == "cz" else "intl"
    key = f"{region}|{q.lower()}"
    now = time.time()
    st = _BROWSE_ARTISTS_CACHE.get(key)
    if not st or now - st["ts"] > _BROWSE_TTL:
        st = {"ts": now, "items": [], "seen": set(), "variant": 0}
        if len(_BROWSE_ARTISTS_CACHE) > 300:
            _BROWSE_ARTISTS_CACHE.clear()
        _BROWSE_ARTISTS_CACHE[key] = st

    variants = _BROWSE_CZ if region == "cz" else _BROWSE_INTL
    need = offset + limit
    if cap:
        need = min(need, cap)
    # Keep asking new phrasings until the page is full or they run out.
    while len(st["items"]) < need and st["variant"] < len(variants):
        phrase = variants[st["variant"]].format(q=q)
        st["variant"] += 1
        try:
            hits = yt().search(phrase, filter="artists", limit=30)
        except Exception:
            continue
        for a in hits:
            bid = a.get("browseId")
            if not bid or bid in st["seen"] or _NOT_AN_ARTIST.search(a.get("artist") or ""):
                continue
            st["seen"].add(bid)
            st["items"].append({"name": a.get("artist"), "browseId": bid,
                                "image": _best_thumb(a.get("thumbnails"))})

    items = st["items"][:cap] if cap else st["items"]
    page = items[offset: offset + limit]
    more = offset + limit < len(items) or (
        st["variant"] < len(variants) and not (cap and len(items) >= cap))
    return {"artists": page, "hasMore": more}


# ------------------------------------------------------- music charts → artists
#
# Charts are lists of songs or albums; the Top shelves want the artists behind
# them, in chart order. Each source is scraped or fetched keylessly, reduced to
# artist names, and the names of one page at a time are looked up on YouTube
# Music (for a picture and a channel id) so a shelf costs ~15 searches, not 100.
#
# Not here, because they have no keyless public feed: Spotify charts, Shazam,
# IFPI Global charts, Beatport (403 to scripts), and the magazine lists.
# A full browser UA gets a 403 from officialcharts.com; the short one is let in.
_CHART_UA = "Mozilla/5.0 Chrome/124"

_BB = "https://www.billboard.com/charts/"
_UK = "https://www.officialcharts.com/charts/"
_APPLE = "https://rss.applemarketingtools.com/api/v2/{cc}/music/most-played/100/{kind}.json"

# (id, label, group, source, arg)
_CHARTS_INTL = [
    ("bb-hot100", "Billboard Hot 100 (USA, singly)", "Billboard", "billboard", "hot-100"),
    ("bb-200", "Billboard 200 (USA, alba)", "Billboard", "billboard", "billboard-200"),
    ("bb-rock", "Billboard Rock & Alternative", "Rock / Metal", "billboard", "hot-rock-songs"),
    ("bb-hardrock", "Billboard Hard Rock", "Rock / Metal", "billboard", "hot-hard-rock-songs"),
    ("bb-hardrock-alb", "Billboard Hard Rock Albums", "Rock / Metal", "billboard", "hard-rock-albums"),
    ("bb-rock-alb", "Billboard Rock Albums", "Rock / Metal", "billboard", "rock-albums"),
    ("uk-rockmetal-alb", "Official UK Rock & Metal Albums", "Rock / Metal", "uk", "rock-and-metal-albums-chart"),
    ("uk-rockmetal", "Official UK Rock & Metal Singles", "Rock / Metal", "uk", "rock-and-metal-singles-chart"),
    ("bb-dance", "Billboard Dance / Electronic", "Elektronika", "billboard", "hot-dance-electronic-songs"),
    ("uk-dance", "Official UK Dance", "Elektronika", "uk", "dance-singles-chart"),
    ("bb-rap", "Billboard Hot Rap Songs", "Hip-hop / Rap", "billboard", "hot-rap-songs"),
    ("bb-rap-alb", "Billboard Rap Albums", "Hip-hop / Rap", "billboard", "rap-albums"),
    ("uk-hiphop", "Official UK Hip Hop / R&B", "Hip-hop / Rap", "uk", "hip-hop-and-r-and-b-singles-chart"),
    ("bb-country", "Billboard Hot Country Songs", "Country", "billboard", "hot-country-songs"),
    ("bb-country-alb", "Billboard Country Albums", "Country", "billboard", "country-albums"),
    ("uk-singles", "UK Official Singles Chart", "Británie", "uk", "singles-chart"),
    ("uk-albums", "UK Official Albums Chart", "Británie", "uk", "albums-chart"),
    ("yt-ZZ", "YouTube Music — svět", "YouTube Music", "ytartists", "ZZ"),
]
for _cc, _nm in [("US", "USA"), ("GB", "Velká Británie"), ("DE", "Německo"), ("FR", "Francie"),
                 ("ES", "Španělsko"), ("IT", "Itálie"), ("PL", "Polsko"), ("SE", "Švédsko"),
                 ("CA", "Kanada"), ("AU", "Austrálie"), ("BR", "Brazílie"), ("MX", "Mexiko"),
                 ("IN", "Indie"), ("JP", "Japonsko"), ("KR", "Jižní Korea")]:
    _CHARTS_INTL.append((f"yt-{_cc}", f"YouTube Music — {_nm}", "YouTube Music", "ytartists", _cc))
for _cc, _nm in [("us", "USA"), ("gb", "Velká Británie"), ("de", "Německo"), ("fr", "Francie"),
                 ("jp", "Japonsko"), ("kr", "Jižní Korea"), ("br", "Brazílie"), ("mx", "Mexiko"),
                 ("in", "Indie"), ("au", "Austrálie")]:
    _CHARTS_INTL.append((f"am-{_cc}", f"Apple Music Top 100 — {_nm}", "Apple Music", "apple", (_cc, "songs")))
_CHARTS_INTL.append(("am-us-alb", "Apple Music Top alba — USA", "Apple Music", "apple", ("us", "albums")))

_CHARTS_CZ = [
    ("cz-sing100", "CZ Singles Digital TOP 100", "ČNS IFPI", "ifpi", 30),
    ("cz-sing50cz", "CZ Singles Digital TOP 50 CZ", "ČNS IFPI", "ifpi", 32),
    ("cz-alb100", "CZ Albums TOP 100", "ČNS IFPI", "ifpi", 14),
    ("cz-radio100", "CZ Radio TOP 100", "ČNS IFPI", "ifpi", 2),
    ("cz-radio50cz", "CZ Radio TOP 50 CZ", "ČNS IFPI", "ifpi", 7),
    ("cz-radio-rock", "CZ Radio TOP 20 Modern Rock", "ČNS IFPI", "ifpi", 21),
    ("cz-classical", "CZ Albums Classical TOP 10", "ČNS IFPI", "ifpi", 25),
    ("yt-CZ", "YouTube Music — Česko", "YouTube Music", "ytartists", "CZ"),
    ("am-cz", "Apple Music Top 100 — Česko", "Apple Music", "apple", ("cz", "songs")),
    ("am-cz-alb", "Apple Music Top alba — Česko", "Apple Music", "apple", ("cz", "albums")),
]
_CHART_INDEX = {c[0]: c for c in _CHARTS_INTL + _CHARTS_CZ}

_CHART_CACHE: dict = {}          # id -> {"ts", "artists": [{"name", "browseId"?, "image"?}]}
_CHART_TTL = 3600
_ARTIST_LOOKUP: dict = {}        # lower name -> {"browseId", "image"} | None
_CREDIT_SPLIT = _re_mod.compile(
    r"\s+(?:feat\.?|featuring|ft\.?|with|x)\s+|\s*,\s*|\s*/\s*|\s+&\s+|\s+and\s+", _re_mod.I)
# Acts whose name really contains one of those separators.
_WHOLE_NAMES = ("earth, wind & fire", "simon & garfunkel", "florence & the machine",
                "hall & oates", "mumford & sons", "brooks & dunn", "ac/dc", "tyler, the creator",
                "crosby, stills & nash", "sly & the family stone", "bob marley & the wailers",
                "prince & the revolution", "kool & the gang", "gladys knight & the pips",
                "belle and sebastian", "up, bustle and out", "florida georgia line")


def _credited_artists(raw: str) -> list:
    raw = _re_mod.sub(r"\s+", " ", (raw or "").strip())
    if not raw:
        return []
    keep = {}
    for i, w in enumerate(_WHOLE_NAMES):
        m = _re_mod.search(_re_mod.escape(w), raw, _re_mod.I)
        if m:
            token = f"\x00{i}\x00"
            keep[token] = m.group(0)
            raw = raw[:m.start()] + token + raw[m.end():]
    out = []
    for p in _CREDIT_SPLIT.split(raw):
        p = (p or "").strip()
        for t, whole in keep.items():
            p = p.replace(t, whole)
        if p:
            out.append(p.title() if p.isupper() and len(p) > 4 else p)
    return out


def _chart_get(url: str) -> str:
    r = httpx.get(url, headers={"User-Agent": _CHART_UA, "Accept-Language": "en"},
                  timeout=20.0, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _chart_billboard(slug: str) -> list:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_chart_get(_BB + slug + "/"), "html.parser")
    out = []
    for h3 in soup.select("h3#title-of-a-story"):
        li = h3.find_parent("li")
        if not li or "o-chart-results-list__item" not in (li.get("class") or []):
            continue
        sp = h3.find_next_sibling("span")
        if sp:
            out.extend(_credited_artists(sp.get_text(" ", strip=True)))
    return [{"name": n} for n in out]


def _chart_uk(slug: str) -> list:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_chart_get(_UK + slug + "/"), "html.parser")
    out = []
    for item in soup.select(".chart-item"):
        a = item.select_one(".description a.chart-artist")
        if a:
            out.extend(_credited_artists(a.get_text(strip=True)))
    return [{"name": n} for n in out]


def _chart_ifpi(chart_id: int) -> list:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_chart_get(f"https://ifpicr.cz/hitparada/{chart_id}"), "html.parser")
    out = []
    for row in soup.select("#chartContainer .chart-full-row"):
        a = row.select_one(".chart-full-row__interpreter a")
        if a:
            out.extend(_credited_artists(a.get_text(strip=True)))
    return [{"name": n} for n in out]


def _chart_apple(arg) -> list:
    cc, kind = arg
    data = httpx.get(_APPLE.format(cc=cc, kind=kind), headers={"User-Agent": _CHART_UA},
                     timeout=20.0, follow_redirects=True).json()
    out = []
    for r in (data.get("feed") or {}).get("results") or []:
        out.extend(_credited_artists(r.get("artistName") or ""))
    return [{"name": n} for n in out]


def _chart_ytartists(cc: str) -> list:
    data = yt().get_charts(cc)
    return [{"name": a.get("title"), "browseId": a.get("browseId"),
             "image": _best_thumb(a.get("thumbnails"))}
            for a in (data.get("artists") or []) if a.get("title")]


_CHART_SOURCES = {"billboard": _chart_billboard, "uk": _chart_uk, "ifpi": _chart_ifpi,
                  "apple": _chart_apple, "ytartists": _chart_ytartists}


def _chart_artists(chart_id: str) -> list:
    now = time.time()
    hit = _CHART_CACHE.get(chart_id)
    if hit and now - hit["ts"] < _CHART_TTL:
        return hit["artists"]
    _, _, _, source, arg = _CHART_INDEX[chart_id]
    raw = _CHART_SOURCES[source](arg)
    seen, artists = set(), []
    for a in raw:
        k = (a["name"] or "").strip().lower()
        if k and k not in seen and k not in ("různí", "various", "various artists"):
            seen.add(k)
            artists.append(a)
    if artists:                      # a failed scrape must not be cached as empty
        _CHART_CACHE[chart_id] = {"ts": now, "artists": artists}
    return artists


def _resolve_artist(a: dict) -> dict:
    """Fill in browseId + picture from YouTube Music where a chart gave only a name."""
    if a.get("browseId"):
        return a
    k = a["name"].lower()
    if k not in _ARTIST_LOOKUP:
        try:
            hits = yt().search(a["name"], filter="artists", limit=1)
        except Exception:
            hits = []
        _ARTIST_LOOKUP[k] = ({"browseId": hits[0].get("browseId"),
                              "image": _best_thumb(hits[0].get("thumbnails"))} if hits else None)
    found = _ARTIST_LOOKUP[k]
    return {"name": a["name"], **(found or {})}


@app.get("/api/charts/catalog")
def charts_catalog():
    def rows(lst):
        return [{"id": c[0], "label": c[1], "group": c[2]} for c in lst]
    return {"intl": rows(_CHARTS_INTL), "cz": rows(_CHARTS_CZ)}


@app.get("/api/charts/artists")
def chart_artists(id: str, offset: int = 0, limit: int = 15):
    if id not in _CHART_INDEX:
        return JSONResponse({"error": "Neznámý žebříček."}, status_code=404)
    try:
        artists = _chart_artists(id)
    except Exception:
        return {"artists": [], "hasMore": False, "failed": True}
    page = artists[offset: offset + limit]
    with ThreadPoolExecutor(max_workers=5) as pool:
        resolved = list(pool.map(_resolve_artist, page))
    return {"artists": resolved, "hasMore": offset + limit < len(artists)}


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


# ----------------------------------------------------------------- video mode

@app.get("/api/video/mode")
def get_video_mode():
    """Get the current video display mode (hidden/mini/full).
    Persisted in ~/Music/spotdl/config.json under 'video_mode'."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {"mode": data.get("video_mode", "full")}


class VideoModeRequest(BaseModel):
    mode: str  # "hidden" | "mini" | "full"


@app.post("/api/video/mode")
def set_video_mode(req: VideoModeRequest):
    """Persist video display mode preference."""
    if req.mode not in ("hidden", "mini", "full"):
        return JSONResponse({"error": "mode must be hidden, mini, or full"}, status_code=400)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["video_mode"] = req.mode
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"mode": req.mode}


# ----------------------------------------------------------------- library management (Kaset-style)

def _load_library_db() -> dict:
    """Load the library database from ~/Music/spotdl/library.json.
    
    Schema: { "playlists": [...], "albums": [...], "artists": [...] }
    Each item: { "id", "title", "author", "thumbnail", "addedAt" }
    """
    if not LIBRARY_PATH.exists():
        return {"playlists": [], "albums": [], "artists": []}
    try:
        data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
        for k in ("playlists", "albums", "artists"):
            if k not in data:
                data[k] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"playlists": [], "albums": [], "artists": []}


def _save_library_db(data: dict):
    """Persist the library database."""
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class LibraryItem(BaseModel):
    kind: str  # "playlist" | "album" | "artist"
    id: str
    title: str
    author: Optional[str] = None
    thumbnail: Optional[str] = None


@app.get("/api/library/list")
def library_list():
    """List all saved library items (playlists, albums, artists)."""
    db = _load_library_db()
    items = []
    for kind in ("playlists", "albums", "artists"):
        for item in db.get(kind, []):
            items.append({"kind": kind[:-1], **item})
    return {"items": items}


@app.post("/api/library/add")
def library_add(item: LibraryItem):
    """Add an item to the library. Returns {ok: true, added: bool}."""
    if item.kind not in ("playlist", "album", "artist"):
        return JSONResponse({"error": "kind must be playlist, album, or artist"}, status_code=400)
    db = _load_library_db()
    kind_key = item.kind + "s"
    existing = db.get(kind_key, [])
    if any(e.get("id") == item.id for e in existing):
        return {"ok": True, "added": False}
    entry = {
        "id": item.id,
        "title": item.title,
        "author": item.author,
        "thumbnail": item.thumbnail,
        "addedAt": int(time.time()),
    }
    existing.append(entry)
    db[kind_key] = existing
    _save_library_db(db)
    return {"ok": True, "added": True}


class LibraryRemoveRequest(BaseModel):
    kind: str
    id: str


@app.post("/api/library/remove")
def library_remove(req: LibraryRemoveRequest):
    """Remove an item from the library. Returns {ok: true, removed: bool}."""
    if req.kind not in ("playlist", "album", "artist"):
        return JSONResponse({"error": "kind must be playlist, album, or artist"}, status_code=400)
    db = _load_library_db()
    kind_key = req.kind + "s"
    existing = db.get(kind_key, [])
    before = len(existing)
    db[kind_key] = [e for e in existing if e.get("id") != req.id]
    removed = len(db[kind_key]) < before
    if removed:
        _save_library_db(db)
    return {"ok": True, "removed": removed}


@app.get("/api/library/check")
def library_check(kind: str, id: str):
    """Check if an item is in the library. Returns {inLibrary: bool}."""
    if kind not in ("playlist", "album", "artist"):
        return JSONResponse({"error": "kind must be playlist, album, or artist"}, status_code=400)
    db = _load_library_db()
    kind_key = kind + "s"
    return {"inLibrary": any(e.get("id") == id for e in db.get(kind_key, []))}


# ----------------------------------------------------------------- search top results (Kaset-style)

@app.get("/api/search/top")
def search_top(q: str, limit: int = 8):
    """Return the top/featured result plus suggestions for a query.
    
    The top result is what YT Music's own ranking considers most relevant
    (typically the most popular/most-viewed match). Suggestions are the
    remaining search results that might also interest the user.
    
    Inspired by Kaset's search_top_result_watch_endpoints fixture — the
    same idea that a search response often has one clear "best" match.
    """
    if not q.strip():
        return {"top": None, "suggestions": []}
    
    client = yt()
    song_results = client.search(q, filter="songs", limit=1)
    top_song = None
    if song_results:
        r = song_results[0]
        top_song = {
            "kind": "song",
            "videoId": r.get("videoId"),
            "title": r.get("title"),
            "artists": ", ".join(a["name"] for a in (r.get("artists") or []) if a.get("name")),
            "album": (r.get("album") or {}).get("name") if isinstance(r.get("album"), dict) else None,
            "duration": r.get("duration"),
            "duration_seconds": r.get("duration_seconds"),
            "thumbnail": _best_thumb(r.get("thumbnails")),
        }
    
    playlist_results = client.search(q, filter="playlists", limit=3)
    playlists = []
    for r in playlist_results:
        bid = r.get("browseId")
        if not bid:
            continue
        playlists.append({
            "kind": "playlist",
            "browseId": bid,
            "title": r.get("title"),
            "author": r.get("author"),
            "thumbnail": _best_thumb(r.get("thumbnails")),
        })
    
    video_results = client.search(q, filter="videos", limit=3)
    videos = []
    for r in video_results:
        vid = r.get("videoId")
        if not vid:
            continue
        videos.append({
            "kind": "video",
            "videoId": vid,
            "title": r.get("title"),
            "artists": r.get("author") or "",
            "thumbnail": _best_thumb(r.get("thumbnails")),
        })
    
    all_suggestions = []
    for r in client.search(q, filter="songs", limit=limit + 1)[1:]:
        if not r.get("videoId"):
            continue
        artists = ", ".join(a["name"] for a in (r.get("artists") or []) if a.get("name"))
        all_suggestions.append({
            "kind": "song",
            "videoId": r["videoId"],
            "title": r.get("title"),
            "artists": artists,
            "duration": r.get("duration"),
            "duration_seconds": r.get("duration_seconds"),
            "thumbnail": _best_thumb(r.get("thumbnails")),
        })
    
    for p in playlists:
        all_suggestions.append(p)
    
    return {
        "top": top_song,
        "suggestions": all_suggestions[:limit],
        "related": {"playlists": playlists, "videos": videos},
    }


# ----------------------------------------------------------------- songsterr tabs
#
# Songsterr serves tabs as a set of JSON files on a CDN. We proxy them to avoid
# CORS issues and provide a simple API for the mixpult to fetch them.
#
# search -> meta -> track JSON
#
# The search endpoint is keyless and stable. Metadata provides the CDN path
# (the 'image' hash) and revision ID needed to fetch the actual notes.
#
# Ref: https://github.com/6801318d8d/songsterr-dl
#
#- search: /a/ra/songs.json?pattern=...
#- meta: /a/ra/song/{songId}.json
#- track: {cdn}/{songId}/{revision}/{image}/{trackIdx}.json

@app.get("/api/tabs/search")
async def search_tabs(q: str, size: int = 20):
    """Search Songsterr for songs. Returns a list of matches with their songId.

    The query goes in `pattern`: the older `/api/search?q=` ignores it and
    answers every request with the same ten popular songs.
    """
    if not q.strip():
        return {"tabs": []}
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                "https://www.songsterr.com/api/songs",
                params={"pattern": q.strip(), "size": max(1, min(size, 40))},
                headers={"User-Agent": APP_UA},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return JSONResponse({"error": f"Search failed: {e}"}, status_code=500)

    records = data if isinstance(data, list) else (data.get("records", []) if isinstance(data, dict) else [])
    seen, tabs = set(), []
    for rec in records:
        sid = rec.get("songId")
        if sid is None or sid in seen or rec.get("isJunk"):
            continue
        seen.add(sid)
        tabs.append(rec)
    return {"tabs": tabs}


@app.get("/api/tabs/meta/{song_id}")
async def tab_meta(song_id: str):
    """Get metadata for a song, including the CDN path hash ('image') and revision."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                f"https://www.songsterr.com/api/meta/{song_id}",
                headers={"User-Agent": APP_UA}
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return JSONResponse({"error": f"Metadata fetch failed: {e}"}, status_code=500)



def normalize_songsterr_track(data: dict) -> dict:
    """Convert Songsterr measure/voice/beat data to a flat note timeline."""

    measures = data.get("measures") or []
    automations = data.get("automations") or {}
    tempo_events = automations.get("tempo") or []

    # Songsterr duration is expressed as a fraction of a whole note.
    # We convert it to seconds using the active BPM.
    def beat_seconds(bpm: float) -> float:
        return 60.0 / bpm

    def duration_seconds(duration, bpm: float) -> float:
        if not isinstance(duration, (list, tuple)) or len(duration) != 2:
            return beat_seconds(bpm)
        numerator, denominator = duration
        try:
            return (float(numerator) / float(denominator)) * (60.0 / bpm) * 4.0
        except (TypeError, ValueError, ZeroDivisionError):
            return beat_seconds(bpm)

    tempo_events = sorted(
        [
            event for event in tempo_events
            if isinstance(event, dict) and "measure" in event and "bpm" in event
        ],
        key=lambda event: (int(event.get("measure", 0)), float(event.get("position", 0)))
    )

    def bpm_for_measure(measure_index: int) -> float:
        bpm = 120.0
        for event in tempo_events:
            if int(event.get("measure", 0)) <= measure_index:
                try:
                    bpm = float(event["bpm"])
                except (TypeError, ValueError):
                    pass
            else:
                break
        return bpm

    notes = []
    measure_start = 0.0
    measure_times = []

    for measure_index, measure in enumerate(measures):
        measure_times.append(measure_start)

        signature = measure.get("signature") or [4, 4]
        try:
            sig_num = float(signature[0])
            sig_den = float(signature[1])
        except (TypeError, ValueError, IndexError):
            sig_num, sig_den = 4.0, 4.0

        bpm = bpm_for_measure(measure_index)
        beat_time = measure_start

        voices = measure.get("voices") or []
        voice = voices[0] if voices else {}
        beats = voice.get("beats") or []

        for beat in beats:
            duration = beat.get("duration", [1, 4])
            dur_seconds = duration_seconds(duration, bpm)

            for note in beat.get("notes") or []:
                if note.get("rest"):
                    continue

                if "fret" not in note or "string" not in note:
                    continue

                try:
                    string_index = int(note["string"])
                    fret = int(note["fret"])
                except (TypeError, ValueError):
                    continue

                notes.append({
                    "time": round(beat_time, 6),
                    "duration": round(dur_seconds, 6),
                    "string": string_index,
                    "fret": fret,
                    "staccato": bool(note.get("staccato", False)),
                    "hammer": bool(note.get("hammer", False)),
                    "pull": bool(note.get("pull", False)),
                    "slide": bool(note.get("slide", False)),
                    "bend": bool(note.get("bend", False)),
                })

            beat_time += dur_seconds

        # Use the actual beat accumulation when possible.
        measure_length = beat_time - measure_start

        # Fallback for an empty/incomplete measure.
        if measure_length <= 0:
            measure_length = (sig_num * 4.0 / sig_den) * (60.0 / bpm)

        measure_start += measure_length

    return {
        "name": data.get("name", ""),
        "songId": data.get("songId"),
        "revisionId": data.get("revisionId"),
        "partId": data.get("partId"),
        "instrument": data.get("instrument"),
        "tuning": data.get("tuning") or [],
        "strings": data.get("strings", 6),
        "tempo": tempo_events,
        "duration": round(measure_start, 6),
        "notes": notes,
        # The bar/beat structure itself, untouched — "notes" above flattens
        # it to absolute seconds for the timeline and the virtual guitar,
        # which throws away exactly what a tab renderer needs (note values,
        # measures). Kept alongside rather than instead of, so nothing that
        # already reads "notes" has to change.
        "measures": measures,
    }

@app.get("/api/tabs/proxy")
async def tab_proxy(url: str):
    """Proxy a Songsterr CDN JSON file to avoid CORS.

    The 'url' should be the full CDN path to the .json file.
    """
    if not url.startswith("https://"):
        return JSONResponse({"error": "Invalid URL."}, status_code=400)

    # Basic safety: ensure it's actually from a known Songsterr CDN
    # (Usually cloudfront or similar).
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": APP_UA})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return JSONResponse({"error": f"Proxy failed: {e}"}, status_code=500)

@app.get("/api/tabs/track/{song_id}/{revision_id}/{part_id}")
async def tab_track(song_id: int, revision_id: int, part_id: int):
    """Fetch and normalize one Songsterr track."""

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            meta_response = await client.get(
                f"https://www.songsterr.com/api/meta/{song_id}",
                headers={"User-Agent": APP_UA},
            )
            meta_response.raise_for_status()
            meta = meta_response.json()

            image = meta.get("image")
            if not image:
                return JSONResponse(
                    {"error": "Songsterr metadata does not contain an image hash."},
                    status_code=502,
                )

            tracks = meta.get("tracks", [])

            # Songsterr's CDN path uses the track's array index,
            # while the metadata may expose a different partId.
            track = None

            if 0 <= part_id < len(tracks):
                track = tracks[part_id]

            if track is None:
                track = next(
                    (
                        item for item in tracks
                        if str(item.get("partId", "")) == str(part_id)
                    ),
                    None,
                )

            if track is None:
                return JSONResponse(
                    {"error": f"Track {part_id} was not found."},
                    status_code=404,
                )

            cdns = [
                "dqsljvtekg760",
                "d34shlm8p2ums2",
                "d3cqchs6g3b5ew",
            ]

            track_url = (
                f"https://{cdns[0]}.cloudfront.net/"
                f"{song_id}/{revision_id}/{image}/{part_id}.json"
            )

            track_response = await client.get(
                track_url,
                headers={"User-Agent": APP_UA},
            )
            track_response.raise_for_status()

            import gzip

            raw = track_response.content
            if raw[:2] == b"\\x1f\\x8b":
                raw = gzip.decompress(raw)

            track_data = json.loads(raw.decode("utf-8"))

            normalized = normalize_songsterr_track(track_data)
            normalized["trackUrl"] = track_url
            normalized["sourceTrack"] = track

            return normalized

    except Exception as e:
        return JSONResponse(
            {"error": f"Track fetch failed: {e}"},
            status_code=502,
        )

# ----------------------------------------------------------------- browse (Kaset-style enhanced)

# ------------------------------------------------------- chip thumbnails

# Every browse chip ("Thrash Metal", "80. léta", "Top 50") gets a cover so the
# search page reads as pictures rather than a wall of pills. Resolving one
# costs a YouTube Music search, so results are cached on disk and survive
# restarts — a term is looked up once, ever.
THUMB_CACHE_PATH = HOME / ".local" / "share" / "spotdl-helper" / "chip-thumbs.json"
_thumb_cache: Optional[dict] = None

# Some of YouTube's thumbnail URLs are signed and stop resolving after a few
# weeks, so a permanent cache eventually serves nothing but 404s. Entries are
# stamped and re-resolved once they age out.
THUMB_CACHE_TTL = 21 * 24 * 3600


def _thumb_get(cache: dict, key: str):
    """(hit, url). Old entries were bare strings; treat those as fresh once
    and let them be re-stamped on the next write."""
    entry = cache.get(key)
    if entry is None:
        return False, None
    if isinstance(entry, dict):
        if time.time() - entry.get("at", 0) > THUMB_CACHE_TTL:
            return False, None
        return True, entry.get("url")
    return True, entry


def _thumb_put(cache: dict, key: str, url):
    cache[key] = {"url": url, "at": time.time()}


def _load_thumb_cache() -> dict:
    global _thumb_cache
    if _thumb_cache is None:
        try:
            _thumb_cache = json.loads(THUMB_CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            _thumb_cache = {}
    return _thumb_cache


def _save_thumb_cache() -> None:
    try:
        THUMB_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        THUMB_CACHE_PATH.write_text(json.dumps(_load_thumb_cache()), encoding="utf-8")
    except OSError:
        pass


def _resolve_chip_thumb(term: str) -> Optional[str]:
    """Cover art for a search term, from the first playlist that matches it."""
    for filt in ("playlists", "albums"):
        try:
            res = yt().search(term, filter=filt, limit=1)
        except Exception:
            continue
        if res:
            thumb = _best_thumb(res[0].get("thumbnails"))
            if thumb:
                return thumb
    return None


# Chrome refuses some googleusercontent/ggpht images with ERR_BLOCKED_BY_ORB
# when they're loaded cross-origin as CSS backgrounds, even though the bytes
# are a perfectly good JPEG. Serving them from our own origin sidesteps that
# entirely, and lets us cache them in memory for the session.
# Several of these hosts turn away clients that don't say who they are.
APP_UA = "Spotifylisty/1.0 (local music app)"

_THUMB_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com",
                "ytimg.googleusercontent.com",
                # artist photos: Wikimedia for the press shots, Bandsintown
                # for the tour images that come with an event.
                "wikimedia.org", "bandsintown.com")
_THUMB_BYTES: dict = {}
_THUMB_BYTES_MAX = 400


@app.get("/api/thumb")
async def proxy_thumb(url: str = ""):
    if not url.startswith("https://"):
        return JSONResponse({"error": "Neplatná adresa."}, status_code=400)
    host = url.split("/", 3)[2].split(":")[0]
    # Never let this become an open proxy.
    if not any(host == h or host.endswith("." + h) for h in _THUMB_HOSTS):
        return JSONResponse({"error": "Nepovolený zdroj."}, status_code=403)

    cached = _THUMB_BYTES.get(url)
    if cached is None:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                # Wikimedia refuses requests that don't identify themselves,
                # and the refusal arrives as a 404, not a 403.
                r = await client.get(url, headers={"User-Agent": APP_UA})
            if r.status_code != 200:
                return JSONResponse({"error": "Náhled nenalezen."}, status_code=404)
            ctype = r.headers.get("content-type", "image/jpeg")
            if not ctype.split(";")[0].strip().startswith("image/"):
                return JSONResponse({"error": "Není obrázek."}, status_code=415)
            cached = (r.content, ctype)
        except httpx.HTTPError:
            return JSONResponse({"error": "Náhled se nepodařilo načíst."}, status_code=502)
        if len(_THUMB_BYTES) >= _THUMB_BYTES_MAX:
            _THUMB_BYTES.clear()   # simple bound; these are re-fetchable
        _THUMB_BYTES[url] = cached

    body, content_type = cached
    from fastapi.responses import Response
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


# An artist page costs a search plus a browse, and the answer doesn't change
# between songs, so it's cached for the session.
_ARTIST_CACHE: dict = {}


def _artist_release_rows(section) -> list:
    """Normalise one of get_artist's release sections into flat rows."""
    if not isinstance(section, dict):
        return []
    rows = []
    for r in section.get("results") or []:
        rows.append({
            "title": r.get("title"),
            "year": r.get("year"),
            "browseId": r.get("browseId"),
            "playlistId": r.get("audioPlaylistId") or r.get("playlistId"),
            "thumbnail": _best_thumb(r.get("thumbnails")),
            "type": r.get("type"),
        })
    return rows


@app.get("/api/artist")
def artist_info(name: str = "", channel_id: str = ""):
    """Everything the now-playing rail shows about an artist.

    Takes a name (what a playing track actually carries) or a channel id.
    Returns the profile plus their albums and singles.
    """
    key = channel_id or name.strip().lower()
    if not key:
        return JSONResponse({"error": "Chybí jméno interpreta."}, status_code=400)
    if key in _ARTIST_CACHE:
        return _ARTIST_CACHE[key]

    client = yt()
    cid = channel_id
    if not cid:
        try:
            hits = client.search(name, filter="artists", limit=1)
        except Exception:
            hits = []
        if not hits:
            return JSONResponse({"error": "Interpret nenalezen."}, status_code=404)
        cid = hits[0].get("browseId")
    if not cid:
        return JSONResponse({"error": "Interpret nenalezen."}, status_code=404)

    try:
        a = client.get_artist(cid)
    except Exception:
        return JSONResponse({"error": "Profil se nepodařilo načíst."}, status_code=502)

    out = {
        "name": a.get("name"),
        "channelId": cid,
        "thumbnail": _best_thumb(a.get("thumbnails")),
        "description": a.get("description"),
        "subscribers": a.get("subscribers"),
        "monthlyListeners": a.get("monthlyListeners"),
        "views": a.get("views"),
        "albums": _artist_release_rows(a.get("albums")),
        "singles": _artist_release_rows(a.get("singles")),
        "related": [
            {"name": r.get("title"), "browseId": r.get("browseId"),
             "thumbnail": _best_thumb(r.get("thumbnails"))}
            for r in ((a.get("related") or {}).get("results") or [])
        ],
        # Music videos. These are real videoIds, so the rail can hand one
        # straight to the video player rather than searching for it again.
        "videos": [
            {"title": v.get("title"), "videoId": v.get("videoId"),
             "views": v.get("views"), "playlistId": v.get("playlistId"),
             "thumbnail": _best_thumb(v.get("thumbnails"))}
            for v in ((a.get("videos") or {}).get("results") or [])
            if v.get("videoId")
        ][:12],
    }
    if len(_ARTIST_CACHE) > 200:
        _ARTIST_CACHE.clear()
    _ARTIST_CACHE[key] = out
    return out


# ---------------------------------------------- everything else about an artist
#
# YouTube Music knows the discography and a promo blurb; it knows nothing about
# where a band came from, who is in it, what they look like or where they play
# next. Those three answers come from three open sources, fetched in parallel
# and cached on disk for a day:
#
#   Wikipedia     the lead section, plus the photos on the page
#   MusicBrainz   formed / origin / genres / line-up
#   Bandsintown   upcoming tour dates
#
# All three are best-effort: any of them can be down or simply have nothing on
# the artist, and the panel just shows less.

ABOUT_CACHE_PATH = HOME / ".local" / "share" / "spotdl-helper" / "artist-about.json"
ABOUT_TTL = 24 * 3600
ABOUT_MISS_TTL = 20 * 60
_about_cache: Optional[dict] = None

WIKI_UA = APP_UA
MB_UA = APP_UA
# Bandsintown's JS widget authenticates as "js_" plus the host it is embedded
# in; this app is served from 127.0.0.1, so that is the honest id for it.
BIT_APP_ID = "js_127.0.0.1"
# Within reach without a flight.
HOME_COUNTRIES = {"Czech Republic", "Czechia", "Slovakia"}

# A bare name is ambiguous — "Kabát" is a coat, "Chinaski" is a character. The
# lead section of the right page nearly always says what kind of act it is, so
# a page that says none of this is the wrong page. Matching is on the stem and
# without diacritics, because Czech inflects: "zpěvák", "zpěvačky", "zpěvem"
# are all one word to this check.
_MUSIC_WORDS = (
    "kapela", "skupina", "zpev", "hudb", "hudebn", "pisnic", "pisnick",
    "rapper", "raper", "muzikant", "kytarist", "bubenik", "klavirist",
    "houslist", "skladatel", "interpret", "album", "band", "singer",
    "musician", "songwriter", "composer", "rapper", "duo", "trio",
    "orchestr", "sbor", "dj",
)


_NOT_GENRES = {
    "czech", "american", "british", "english", "german", "swedish", "finnish",
    "norwegian", "danish", "french", "italian", "spanish", "polish", "slovak",
    "dutch", "canadian", "australian", "japanese", "russian", "irish",
    "usa", "uk", "seen live", "male vocalists", "female vocalists",
}


def _commons_key(url: str) -> str:
    """The Commons file a Wikimedia URL points at.

    A thumbnail lives under .../thumb/<dir>/<File.jpg>/640px-<File.jpg>, so the
    file name is the second-to-last segment there and the last one otherwise.
    """
    parts = (url or "").split("?")[0].rstrip("/").split("/")
    if not parts:
        return url or ""
    name = parts[-2] if "/thumb/" in (url or "") and len(parts) >= 2 else parts[-1]
    return unquote(name).lower()


def _deaccent(text: str) -> str:
    """Lowercased and stripped of diacritics, for matching Czech text."""
    import unicodedata
    return "".join(
        ch for ch in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(ch) != "Mn"
    )


def _load_about_cache() -> dict:
    global _about_cache
    if _about_cache is None:
        try:
            _about_cache = json.loads(ABOUT_CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            _about_cache = {}
    return _about_cache


def _save_about_cache() -> None:
    try:
        ABOUT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ABOUT_CACHE_PATH.write_text(
            json.dumps(_load_about_cache(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


async def _wiki_api(client: httpx.AsyncClient, lang: str, params: dict):
    """One MediaWiki API call, with a single retry when we're being throttled.

    Wikipedia answers 429 to bursts, and a burst is exactly what a cold cache
    looks like. One short back-off turns that into an answer instead of an
    artist with no biography.
    """
    url = f"https://{lang}.wikipedia.org/w/api.php"
    body = {**params, "format": "json", "formatversion": "2"}
    for attempt in (0, 1):
        r = await client.get(url, params=body, headers={"User-Agent": WIKI_UA})
        if r.status_code == 429 and attempt == 0:
            await asyncio.sleep(1.2)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


async def _wiki_search_titles(client, lang: str, name: str) -> list:
    """Disambiguated titles for a name that isn't a page by itself —
    "Kabát (hudební skupina)" rather than the garment."""
    hint = "hudební skupina" if lang == "cs" else "band"
    try:
        d = await _wiki_api(client, lang, {
            "action": "query", "list": "search",
            "srsearch": f"{name} {hint}", "srlimit": 3,
        })
    except (httpx.HTTPError, ValueError):
        return []
    seen, out = {name.lower()}, []
    for h in ((d.get("query") or {}).get("search") or []):
        t = h.get("title")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:3]


async def _wiki_photos(client, lang: str, title: str) -> list:
    """The photographs on a page — not its icons, logos or maintenance glyphs."""
    try:
        d = await _wiki_api(client, lang, {
            "action": "query", "generator": "images", "titles": title,
            "gimlimit": 24, "prop": "imageinfo", "iiprop": "url|mime",
            "iiurlwidth": 640, "redirects": 1,
        })
    except (httpx.HTTPError, ValueError):
        return []

    junk = ("logo", "icon", "commons-", "edit-", "question", "ambox",
            "symbol", "flag of", "wiki", "sound", "speaker", "disambig")
    out = []
    for p in (d.get("query") or {}).get("pages") or []:
        info = (p.get("imageinfo") or [{}])[0]
        mime = str(info.get("mime") or "")
        if not mime.startswith("image/") or mime == "image/svg+xml":
            continue
        name = str(p.get("title") or "")
        if any(w in name.lower() for w in junk):
            continue
        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        label = name.split(":", 1)[-1].rsplit(".", 1)[0].replace("_", " ")
        out.append({"url": url, "title": label})
    return out[:8]


async def _wiki_try_title(client, lang: str, name: str, title: str) -> Optional[dict]:
    """The page under `title`, if it really is this artist's page."""
    try:
        d = await _wiki_api(client, lang, {
            "action": "query", "prop": "extracts|pageimages",
            "titles": title, "explaintext": 1, "exintro": 1,
            "redirects": 1, "piprop": "original",
        })
    except (httpx.HTTPError, ValueError):
        return None

    pages = (d.get("query") or {}).get("pages") or []
    if not pages or pages[0].get("missing"):
        return None
    page = pages[0]
    text = (page.get("extract") or "").strip()
    if len(text) < 80:
        return None

    low = _deaccent(text)
    real_title = page.get("title") or title
    # Right name, and an act of some kind — otherwise it's a homonym.
    flat_name = _deaccent(name)
    if flat_name not in low[:600] and flat_name not in _deaccent(real_title):
        return None
    if not any(w in low for w in _MUSIC_WORDS):
        return None

    return {
        "text": text,
        "title": real_title,
        "lang": lang,
        "url": f"https://{lang}.wikipedia.org/wiki/{quote(real_title.replace(' ', '_'))}",
        "image": (page.get("original") or {}).get("source"),
        "photos": await _wiki_photos(client, lang, real_title),
    }


async def _wiki_about(client, name: str) -> Optional[dict]:
    """Lead section and photos, from the Czech Wikipedia where it has the
    artist and the English one otherwise.

    The plain name is tried first and is usually the page; search is only
    consulted when it isn't, which keeps the common case to one request.
    """
    for lang in ("cs", "en"):
        hit = await _wiki_try_title(client, lang, name, name)
        if hit:
            return hit
        for title in await _wiki_search_titles(client, lang, name):
            hit = await _wiki_try_title(client, lang, name, title)
            if hit:
                return hit
    return None


async def _mb_get(client, url: str, params: dict) -> Optional[dict]:
    """One MusicBrainz call. They ask for a request a second and answer 503
    when the server is busy, so a throttled call is retried once."""
    for attempt in (0, 1):
        try:
            r = await client.get(url, params=params, headers={"User-Agent": MB_UA})
        except httpx.HTTPError:
            return None
        if r.status_code in (429, 503) and attempt == 0:
            await asyncio.sleep(1.1)
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


async def _mb_about(client, name: str) -> dict:
    """Formed / origin / genres / line-up from MusicBrainz."""
    found = await _mb_get(client, "https://musicbrainz.org/ws/2/artist/",
                          {"query": f'artist:"{name}"', "fmt": "json", "limit": 3})
    hits = (found or {}).get("artists") or []

    match = None
    for h in hits:
        if (h.get("name") or "").strip().lower() == name.strip().lower():
            match = h
            break
    if match is None and hits and hits[0].get("score", 0) >= 90:
        match = hits[0]
    if match is None:
        return {}

    span = match.get("life-span") or {}
    out = {
        "mbid": match.get("id"),
        "kind": match.get("type"),
        "formed": (span.get("begin") or "")[:4] or None,
        "ended": (span.get("end") or "")[:4] or None,
        "origin": ", ".join(x for x in [
            (match.get("begin-area") or {}).get("name"),
            (match.get("area") or {}).get("name"),
        ] if x) or None,
        # MusicBrainz tags mix genres with nationalities — "czech" and
        # "american" say nothing about how a band sounds.
        "genres": [t["name"] for t in (match.get("tags") or [])
                   if t.get("name") and (t.get("count") or 0) > 0
                   and t["name"].lower() not in _NOT_GENRES][:5],
        "members": [],
    }

    # The line-up is a second lookup; without it a band is just a name.
    # MusicBrainz asks for no more than a request a second, and this follows
    # the search immediately.
    await asyncio.sleep(1.05)
    detail = await _mb_get(client, f"https://musicbrainz.org/ws/2/artist/{out['mbid']}",
                           {"inc": "artist-rels", "fmt": "json"})
    rels = (detail or {}).get("relations") or []

    # One person can hold several relations (guitar and vocals are separate
    # rows), so collect by name and merge the roles rather than listing them
    # twice.
    by_name: dict = {}
    for rel in rels:
        if rel.get("type") != "member of band" or rel.get("direction") != "backward":
            continue
        who = (rel.get("artist") or {}).get("name")
        if not who:
            continue
        m = by_name.setdefault(who, {"name": who, "roles": [], "current": False})
        for role in (rel.get("attributes") or []):
            if role and role not in m["roles"]:
                m["roles"].append(role)
        m["current"] = m["current"] or not rel.get("ended")
    for m in by_name.values():
        m["roles"] = m["roles"][:3]
        out["members"].append(m)
    # Current members first, then the alumni, each keeping MusicBrainz's order.
    out["members"] = ([m for m in out["members"] if m["current"]] +
                      [m for m in out["members"] if not m["current"]])[:14]
    return out


async def _bit_events(client, name: str) -> list:
    """Upcoming tour dates from Bandsintown."""
    try:
        r = await client.get(
            f"https://rest.bandsintown.com/v3/artists/{quote(name, safe='')}/events/",
            params={"app_id": BIT_APP_ID, "date": "upcoming"},
        )
        r.raise_for_status()
        raw = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    out = []
    for e in raw:
        venue = e.get("venue") or {}
        tickets = next((o.get("url") for o in (e.get("offers") or [])
                        if o.get("type") == "Tickets" and o.get("url")), None)
        out.append({
            "datetime": e.get("datetime"),
            "venue": venue.get("name"),
            "city": venue.get("city"),
            "region": venue.get("region"),
            "country": venue.get("country"),
            "lineup": [a for a in (e.get("lineup") or []) if a][:4],
            "url": tickets or e.get("url"),
            "soldOut": bool(e.get("sold_out")),
            "title": e.get("title") or "",
            "home": venue.get("country") in HOME_COUNTRIES,
        })

    # A world tour is listed by date, which for a Czech listener buries the one
    # date they can actually get to under a month of Australia. Home dates come
    # first, each group still in date order — and because the trim happens
    # after this, a home date is never the one that falls off the end.
    out.sort(key=lambda e: (not e["home"], e["datetime"] or ""))
    return out[:24]


@app.get("/api/artist/about")
async def artist_about(name: str = ""):
    """Biography, line-up, photos and tour dates for an artist.

    Split out of /api/artist deliberately: that one answers from a single
    YouTube Music call and the rail needs it immediately, while this one talks
    to three outside services and can take a second or two. The panel paints
    first and fills in when this lands.
    """
    term = name.strip()
    if not term:
        return JSONResponse({"error": "Chybí jméno interpreta."}, status_code=400)

    cache = _load_about_cache()
    key = term.lower()
    entry = cache.get(key)
    if isinstance(entry, dict) and time.time() - entry.get("at", 0) < entry.get("ttl", ABOUT_TTL):
        return entry.get("data") or {}

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        wiki, mb, events = await asyncio.gather(
            _wiki_about(client, term),
            _mb_about(client, term),
            _bit_events(client, term),
            return_exceptions=True,
        )
    wiki = wiki if isinstance(wiki, dict) else None
    mb = mb if isinstance(mb, dict) else {}
    events = events if isinstance(events, list) else []

    # The lead image is usually also one of the page images, once as the full
    # original and once as a 640px thumbnail — the same photograph twice. Key
    # them by the Commons file they point at rather than by URL.
    photos = list((wiki or {}).get("photos") or [])
    lead = (wiki or {}).get("image")
    if lead and not any(_commons_key(p["url"]) == _commons_key(lead) for p in photos):
        photos.insert(0, {"url": lead, "title": (wiki or {}).get("title") or term})

    data = {
        "name": term,
        "bio": ({"text": wiki["text"], "url": wiki["url"], "lang": wiki["lang"]}
                if wiki else None),
        "facts": {
            "kind": mb.get("kind"),
            "formed": mb.get("formed"),
            "ended": mb.get("ended"),
            "origin": mb.get("origin"),
            "genres": mb.get("genres") or [],
        },
        "members": mb.get("members") or [],
        "photos": photos,
        "events": events,
    }

    if len(cache) > 300:
        cache.clear()
    # A thin answer usually means an outside service was throttling us, not
    # that there is nothing to say — don't let one bad minute stick for a day.
    thin = not data["bio"] and not data["photos"] and not data["events"] \
        and not data["facts"]["formed"]
    cache[key] = {"at": time.time(), "data": data,
                  "ttl": ABOUT_MISS_TTL if thin else ABOUT_TTL}
    _save_about_cache()
    return data


@app.get("/api/art")
def art_lookup(q: str = "", kind: str = "auto"):
    """Artwork for a name — an artist photo, or a cover for a song or album.

    Shares the chip-thumbnail disk cache (under its own key prefix) so a name
    is resolved once, ever. Separated songs are folders like
    "Sylosis - Erased" or plain "Cerberus", so both shapes have to work.
    """
    term = q.strip()
    if not term:
        return {"url": None}

    cache = _load_thumb_cache()
    key = f"art:{kind}:{term}"
    hit, cached_url = _thumb_get(cache, key)
    if hit:
        return {"url": cached_url}

    # "Artist - Title" is spotdl's own naming, so the part before the dash is
    # a real artist far more often than not; fall back to the whole string.
    artist = term.split(" - ", 1)[0].strip() if " - " in term else term
    attempts = []
    if kind in ("auto", "artist"):
        attempts.append((artist, "artists"))
    attempts += [(term, "songs"), (term, "albums"), (artist, "artists")]

    url = None
    for query, filt in attempts:
        if not query:
            continue
        try:
            hits = yt().search(query, filter=filt, limit=1)
        except Exception:
            continue
        if hits:
            url = _best_thumb(hits[0].get("thumbnails"))
            if url:
                break

    _thumb_put(cache, key, url)
    _save_thumb_cache()
    return {"url": url}


@app.get("/api/chip-thumbs")
def chip_thumbs(terms: str = ""):
    """Thumbnails for up to 12 comma-separated terms.

    Cached terms come back immediately; uncached ones are resolved now and
    written through. A term that resolves to nothing is cached as null so it
    isn't retried on every page load.
    """
    wanted = [t.strip() for t in terms.split(",") if t.strip()][:12]
    cache = _load_thumb_cache()
    out = {}
    dirty = False
    for term in wanted:
        hit, cached_url = _thumb_get(cache, term)
        if hit:
            out[term] = cached_url
            continue
        thumb = _resolve_chip_thumb(term)
        _thumb_put(cache, term, thumb)
        out[term] = thumb
        dirty = True
    if dirty:
        _save_thumb_cache()
    return out


@app.get("/api/video/blackout")
def video_blackout():
    """Returns a CSS snippet that can be injected to black out the video
    area during mode transitions. Inspired by Kaset's approach of hiding
    the player WebView during mode switches to prevent visual artifacts."""
    return {"css": "body.video-mode-transition #video-section, body.video-mode-transition .video-player-wrap { opacity: 0 !important; transition: opacity 0.15s ease-out !important; }"}


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


# Reading tags costs a file open each, and a playlist folder holds dozens.
# Keyed on (mtime, size) so an edited file is re-read but an unchanged one
# never is.
_TAG_CACHE: dict = {}


def _audio_tags(path: Path) -> dict:
    """title / artist / album / duration from the file's own tags.

    spotdl writes all of these, so the library can show what a track actually
    is rather than guessing from the filename.
    """
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    cached = _TAG_CACHE.get(key)
    if cached and cached["sig"] == (st.st_mtime, st.st_size):
        return cached["tags"]

    tags = {}
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path), easy=True)
        if audio is not None:
            def first(name):
                value = audio.get(name)
                return value[0] if value else None

            tags = {
                "title": first("title"),
                "artist": first("artist"),
                "album": first("album"),
                "duration": round(audio.info.length, 1) if getattr(audio, "info", None) else None,
            }
    except Exception:
        tags = {}

    if len(_TAG_CACHE) > 4000:
        _TAG_CACHE.clear()
    _TAG_CACHE[key] = {"sig": (st.st_mtime, st.st_size), "tags": tags}
    return tags


def _folder_stats(folder: Path, _depth: int = 0):
    """(audio files, total bytes, subfolders) beneath `folder`.

    Walks the whole subtree so a top-level folder reports what's actually in
    it, with a depth cap so a stray deep or symlinked tree can't stall the
    listing. Only the immediate subfolder count is reported.
    """
    tracks = 0
    size = 0
    folders = 0
    if _depth > 6:
        return tracks, size, folders
    try:
        children = list(folder.iterdir())
    except OSError:
        return tracks, size, folders
    for child in children:
        try:
            if child.is_dir():
                if _depth == 0:
                    folders += 1
                t, sz, _ = _folder_stats(child, _depth + 1)
                tracks += t
                size += sz
            elif child.is_file() and child.suffix.lower() in AUDIO_EXTS:
                tracks += 1
                size += child.stat().st_size
        except OSError:
            continue
    return tracks, size, folders


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
            tracks, size, folders = _folder_stats(p)
            dirs.append({
                "name": p.name, "type": "dir", "rel": entry_rel,
                # Counts and size let the browser show the shape of a folder
                # without making you open it.
                "track_count": tracks, "folder_count": folders, "size_bytes": size,
            })
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append({
                "name": p.name, "type": "file", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
                **_audio_tags(p),
            })
        # A NAM capture isn't playable audio (it's model weights, not a
        # waveform) so it never matched AUDIO_EXTS and used to vanish from
        # this listing entirely — a folder full of presets looked empty.
        elif p.is_file() and p.suffix.lower() == ".nam":
            files.append({
                "name": p.name, "type": "nam", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
            })

    total_size = sum(f["size_bytes"] for f in files) + sum(d["size_bytes"] for d in dirs)
    # NAM presets aren't songs — _folder_stats (which feeds a subfolder's own
    # track_count) never counted them either, so keep this count consistent
    # with that rather than inflating "N skladeb" with preset files.
    total_tracks = sum(1 for f in files if f["type"] == "file") + sum(d["track_count"] for d in dirs)
    return {
        "rel": "/".join(parts), "breadcrumb": breadcrumb, "entries": dirs + files,
        "summary": {
            "folders": len(dirs), "files": len(files),
            "total_tracks": total_tracks, "total_size_bytes": total_size,
        },
    }


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
        spec = range_header.removeprefix("bytes=")
        try:
            if spec.startswith("-"):
                # Suffix range: "bytes=-500" means "the last 500 bytes".
                suffix_length = int(spec[1:])
                start = max(0, size - suffix_length)
                end = size - 1
            else:
                start_s, _, end_s = spec.partition("-")
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
    if target.suffix.lower() not in AUDIO_EXTS:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _range_file_response(target, request.headers.get("range"))


@app.get("/api/library/raw")
def library_raw(rel: str):
    """Raw bytes of a NAM capture under the library — .nam is model weights
    (JSON), not a waveform, so it never belongs behind /api/library/stream's
    audio-range machinery. Scoped to .nam only; anything else stays served
    through /api/library/stream instead.
    """
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    if target.suffix.lower() != ".nam":
        return JSONResponse({"error": "Neplatný soubor."}, status_code=400)
    from fastapi.responses import Response
    return Response(target.read_bytes(), media_type="application/json")


def _applescript_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unique_dest(parent: Path, name: str) -> Path:
    """Appends " 2", " 3", … before the extension until the name is free,
    rather than overwriting something already at the destination."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        candidate = parent / f"{stem} {n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


class LibraryMkdirRequest(BaseModel):
    rel: str = ""   # parent folder, relative to MUSIC_ROOT
    name: str


@app.post("/api/library/fs/mkdir")
def library_fs_mkdir(req: LibraryMkdirRequest):
    parent = _resolve_under_music_root(req.rel)
    if parent is None or not parent.is_dir():
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)
    name = "".join(c for c in (req.name or "").strip() if c not in '/\\:*?"<>|')[:120]
    if not name:
        return JSONResponse({"error": "Zadej název."}, status_code=400)
    target = parent / name
    if target.exists():
        return JSONResponse({"error": "Taková složka už existuje."}, status_code=409)
    try:
        target.mkdir()
    except OSError as exc:
        return JSONResponse({"error": f"Nepodařilo se vytvořit: {exc}"}, status_code=500)
    return {"ok": True, "rel": str(target.relative_to(MUSIC_ROOT.resolve()))}


class LibraryImportRequest(BaseModel):
    rel: str = ""       # destination folder, relative to MUSIC_ROOT
    source_path: str    # absolute path on disk, from the native folder/file picker


@app.post("/api/library/fs/import")
def library_fs_import(req: LibraryImportRequest):
    """Copies a folder or file chosen on disk into the library.

    A copy, not a move or a symlink: _resolve_under_music_root resolves
    symlinks and rejects anything whose real path escapes MUSIC_ROOT (by
    design — that's what stops a browsed path from walking out of the
    library), so a symlink into an external folder would just get refused
    the moment anyone browsed into it. Copying also never risks a source
    the user still has open somewhere else, the way a move would.
    """
    dest_dir = _resolve_under_music_root(req.rel)
    if dest_dir is None or not dest_dir.is_dir():
        return JSONResponse({"error": "Cílová složka nenalezena."}, status_code=404)
    source = Path(req.source_path).expanduser()
    if not source.is_absolute() or not source.exists():
        return JSONResponse({"error": "Zdroj neexistuje."}, status_code=400)
    try:
        source.resolve().relative_to(MUSIC_ROOT.resolve())
        return JSONResponse({"error": "Zdroj už je v knihovně."}, status_code=400)
    except ValueError:
        pass
    target = _unique_dest(dest_dir, source.name)
    try:
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    except OSError as exc:
        return JSONResponse({"error": f"Kopírování selhalo: {exc}"}, status_code=500)
    return {"ok": True, "rel": str(target.relative_to(MUSIC_ROOT.resolve()))}


class LibraryMoveRequest(BaseModel):
    rel: str            # source, relative to MUSIC_ROOT
    dest_rel: str = ""  # destination folder, relative to MUSIC_ROOT


@app.post("/api/library/fs/move")
def library_fs_move(req: LibraryMoveRequest):
    source = _resolve_under_music_root(req.rel)
    dest_dir = _resolve_under_music_root(req.dest_rel)
    if source is None or not source.exists():
        return JSONResponse({"error": "Zdroj nenalezen."}, status_code=404)
    if dest_dir is None or not dest_dir.is_dir():
        return JSONResponse({"error": "Cílová složka nenalezena."}, status_code=404)
    if source == MUSIC_ROOT.resolve():
        return JSONResponse({"error": "Kořenovou složku nelze přesunout."}, status_code=400)
    if source.is_dir() and (dest_dir == source or source in dest_dir.parents):
        return JSONResponse({"error": "Nelze přesunout složku samu do sebe."}, status_code=400)
    target = _unique_dest(dest_dir, source.name)
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        return JSONResponse({"error": f"Přesun selhal: {exc}"}, status_code=500)
    return {"ok": True, "rel": str(target.relative_to(MUSIC_ROOT.resolve()))}


class LibraryDeleteRequest(BaseModel):
    rel: str


@app.post("/api/library/fs/delete")
def library_fs_delete(req: LibraryDeleteRequest):
    """Moves to Trash via Finder rather than unlinking outright — a
    personal media library is exactly the kind of thing a misclick
    shouldn't be able to permanently destroy."""
    target = _resolve_under_music_root(req.rel)
    if target is None or not target.exists():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    if target == MUSIC_ROOT.resolve():
        return JSONResponse({"error": "Kořenovou složku nelze smazat."}, status_code=400)
    script = f'tell application "Finder" to delete POSIX file "{_applescript_quote(str(target))}"'
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Časový limit vypršel."}, status_code=500)
    if result.returncode != 0:
        return JSONResponse({"error": result.stderr.strip() or "Smazání selhalo."}, status_code=500)
    return {"ok": True}


# ------------------------------------------------------- NAM preset preview
#
# Same idea as TONE3000's web player (https://www.tone3000.com/blog/
# introducing-the-nam-web-player): a short dry guitar recording, played
# through whichever NAM capture is clicked, entirely client-side — the NAM
# WASM engine already used for the live guitar channel does the actual
# amp-modeling work. The riff has to be a *real* dry (pre-amp) recording,
# so it's captured once from the user's own guitar rather than faked; a
# stem or anything already-amped would just double up amp coloration.
NAM_PREVIEW_DIR = HOME / ".local" / "share" / "spotdl-helper"
NAM_PREVIEW_RIFF = NAM_PREVIEW_DIR / "nam_preview_di.webm"


@app.get("/api/nam-preview/riff")
def get_nam_preview_riff():
    if not NAM_PREVIEW_RIFF.is_file():
        return JSONResponse({"error": "Zatím nenahráno."}, status_code=404)
    from fastapi.responses import Response
    return Response(NAM_PREVIEW_RIFF.read_bytes(), media_type="audio/webm")


@app.get("/api/nam-preview/riff/status")
def nam_preview_riff_status():
    exists = NAM_PREVIEW_RIFF.is_file()
    return {"exists": exists, "bytes": NAM_PREVIEW_RIFF.stat().st_size if exists else 0}


@app.post("/api/nam-preview/riff")
async def save_nam_preview_riff(request: Request):
    raw = await request.body()
    if len(raw) < 1024:
        return JSONResponse({"error": "Nahrávka je prázdná."}, status_code=400)
    if len(raw) > 60 * 1024 * 1024:
        return JSONResponse({"error": "Nahrávka je příliš velká."}, status_code=413)
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        NAM_PREVIEW_RIFF.write_bytes(raw)
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return {"ok": True, "bytes": len(raw)}


@app.delete("/api/nam-preview/riff")
def delete_nam_preview_riff():
    NAM_PREVIEW_RIFF.unlink(missing_ok=True)
    return {"ok": True}


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


class _QuietLogger:
    """Swallows yt-dlp's own ERROR lines.

    Probing several format selectors is normal control flow here — a selector
    that doesn't match just means "try the next one" — but yt-dlp prints
    "Requested format is not available" to stderr for each miss, which filled
    the server log with dozens of scary non-errors.
    """

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _purge_expired_stream_files() -> None:
    """Deletes temp dirs of merged videos whose cache entry has expired.

    Merged DASH videos are written to a tempfile.mkdtemp() that nothing used
    to remove, so every such playback leaked tens of MB into /var/folders for
    the rest of the uptime.
    """
    now = time.time()
    for key, entry in list(_STREAM_CACHE.items()):
        if entry.get("expires", 0) > now or not entry.get("is_file"):
            continue
        tmpdir = entry.get("tmpdir")
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        _STREAM_CACHE.pop(key, None)


def _resolve_video(video_id: str):
    """Progressive (single file, video+audio combined) stream — good enough
    for the in-app video toggle, not used for downloads.

    For progressive videos, returns direct stream URL.
    For DASH-only videos (no progressive format), downloads and merges
    to a temp file using yt-dlp + ffmpeg, then serves that.
    """
    cache_key = ("video", video_id)
    cached = _STREAM_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp
    import tempfile

    _purge_expired_stream_files()

    base_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "logger": _QuietLogger(),
    }

    # Try progressive formats first (single file, direct stream - no download needed)
    for fmt in ["best[ext=mp4]/best", "best"]:
        try:
            with yt_dlp.YoutubeDL({**base_opts, "format": fmt}) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if info and info.get("url"):
                result = {
                    "url": info["url"],
                    "headers": info.get("http_headers") or {},
                    "expires": time.time() + _STREAM_CACHE_TTL,
                }
                _STREAM_CACHE[cache_key] = result
                return result
        except Exception:
            continue

    # No progressive format available - download and merge to temp file.
    # These are *selectors*, not the literal format IDs this used to hardcode
    # ("399+251", "137+251", ...): those IDs only exist on some videos, so most
    # DASH-only videos burned six failed probes before reaching the fallback.
    format_combos = [
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]",
        "bestvideo[height<=1080]+bestaudio",
        "bestvideo+bestaudio",
        "best",
    ]

    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="spotdl-helper-video-")
        tmp_path = os.path.join(tmpdir, "video.mp4")

        ffmpeg_path = ffmpeg_bin()

        for fmt in format_combos:
            ydl_opts = {
                **base_opts,
                "format": fmt,
                "outtmpl": tmp_path,
                "merge_output_format": "mp4",
                "ffmpeg_location": ffmpeg_path,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    result = {
                        "url": f"file://{tmp_path}",
                        "headers": {},
                        "expires": time.time() + _STREAM_CACHE_TTL,
                        "is_file": True,
                        "tmpdir": tmpdir,
                    }
                    _STREAM_CACHE[cache_key] = result
                    return result
            except Exception:
                continue
    except Exception:
        pass

    # Nothing worked — don't leak the scratch dir we created for the attempt.
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return None


async def _proxy_media_response(request: Request, video_id: str, resolve, cache_key, default_content_type: str):
    try:
        info = await asyncio.to_thread(resolve, video_id)
    except Exception as e:
        return JSONResponse({"error": f"Náhled se nepodařilo najít ({e})"}, status_code=502)

    if info is None:
        return JSONResponse({"error": "Video nemá dostupný progresivní stream — je možné ho přehrát jen jako zvuk"}, status_code=404)

    # Handle local file (DASH-only videos downloaded to temp)
    if info.get("is_file"):
        file_path = info["url"].replace("file://", "")
        path = Path(file_path)
        if not path.exists():
            return JSONResponse({"error": "Video soubor nenalezen"}, status_code=404)
        return _range_file_response(path, request.headers.get("range"))

    upstream_headers = dict(info["headers"])
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
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


@app.get("/api/stream-video/{video_id}/prepare")
async def prepare_video(video_id: str):
    """Resolve a video's stream URL without streaming anything.

    Resolution is a yt-dlp extraction and takes about eight seconds, and until
    now every one of those was paid after the video button was pressed, with
    the window already open and empty. The player calls this as soon as it
    knows a track's video id, so by the time anyone presses the button the
    answer is usually already in the cache and playback starts at once.
    """
    try:
        info = await asyncio.to_thread(_resolve_video, video_id)
    except Exception as exc:                                   # noqa: BLE001
        return JSONResponse({"ready": False, "error": str(exc)[:200]},
                            status_code=200)
    return {"ready": bool(info)}


# ------------------------------------------------------------ remote control
#
# The menu bar icon and the mini player live outside the browser window that
# actually owns playback — a second window can't share a Web Audio graph or an
# <audio> element. So they don't try: the window that plays pushes what it is
# doing here, and anything that wants to control it leaves a command for that
# window to pick up. One tiny hop through the server, and the browser stays the
# only thing that makes sound.

_REMOTE_STATE: dict = {
    "title": None, "artists": None, "thumbnail": None,
    "playing": False, "volume": 1.0, "position": 0.0, "duration": 0.0,
    "source": None,          # "player" | "mixer"
    "at": 0.0,
}
_REMOTE_COMMANDS: list = []   # [(seq, {"action":..., "value":...})]
_REMOTE_SEQ = 0
_REMOTE_KEEP = 40


class RemoteState(BaseModel):
    """What the playing window says it is doing.

    The numbers are optional because they aren't always numbers: an audio
    element's duration is NaN until metadata arrives, JSON turns NaN into
    null, and rejecting that meant the state never got through at all.
    """
    title: Optional[str] = None
    artists: Optional[str] = None
    thumbnail: Optional[str] = None
    playing: bool = False
    volume: Optional[float] = 1.0
    position: Optional[float] = 0.0
    duration: Optional[float] = 0.0
    source: Optional[str] = None


class RemoteCommand(BaseModel):
    action: str
    value: Optional[float] = None


@app.get("/api/remote/state")
def remote_state():
    """What the playing window last said it was doing."""
    out = dict(_REMOTE_STATE)
    # A window that has gone away shouldn't leave the menu bar claiming a
    # song is playing forever.
    out["stale"] = (time.time() - out.get("at", 0)) > 12
    return out


@app.post("/api/remote/state")
def push_remote_state(body: RemoteState):
    data = body.model_dump()
    for key in ("volume", "position", "duration"):
        value = data.get(key)
        data[key] = 0.0 if value is None or value != value else float(value)
    _REMOTE_STATE.update(data)
    _REMOTE_STATE["at"] = time.time()
    return {"ok": True}


@app.post("/api/remote/command")
def push_remote_command(body: RemoteCommand):
    """Queue something for the playing window to do."""
    global _REMOTE_SEQ
    if body.action not in REMOTE_ACTIONS:
        return JSONResponse({"error": "Neznámý příkaz."}, status_code=400)
    _REMOTE_SEQ += 1
    _REMOTE_COMMANDS.append((_REMOTE_SEQ, {"action": body.action, "value": body.value}))
    del _REMOTE_COMMANDS[:-_REMOTE_KEEP]
    return {"seq": _REMOTE_SEQ}


@app.get("/api/remote/commands")
def remote_commands(since: int = 0):
    """Everything queued after `since`, for the window that plays."""
    pending = [cmd for seq, cmd in _REMOTE_COMMANDS if seq > since]
    return {"seq": _REMOTE_SEQ, "commands": pending}


REMOTE_ACTIONS = {
    "playpause", "play", "pause", "next", "prev",
    "volume", "seek", "stop", "mute",
}


@app.get("/mini")
def mini_player():
    """The mini player window — its own page, not the app in a small window."""
    path = APP_DIR / "static" / "mini.html"
    if not path.is_file():
        return JSONResponse({"error": "Mini přehrávač chybí."}, status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


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
        out_dir = MUSIC_ROOT / "Playlists" / safe
        out_template = str(out_dir / "{artist} - {title}.{output-ext}")
    else:
        out_dir = MUSIC_ROOT
        out_template = str(out_dir / "{artist}" / "{album}" / "{title}.{output-ext}")

    # Find ffmpeg
    ffmpeg_path = ffmpeg_bin()
    
    cmd = [
        SPOTDL_BIN,
        "--ffmpeg", ffmpeg_path,
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
            if job["status"] == "done" and job.get("kind") == "stems":
                try:
                    _finalize_stems_job(job)
                except Exception as e:
                    job["status"] = "error"
                    job["lines"].append(f"Přesun stop selhal: {e}")
                    yield f"data: {json.dumps({'line': f'Přesun stop selhal: {e}'})}\n\n"
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
    _write_config({"music_root": str(path)})
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


# ------------------------------------------------------------------- stems

DEMUCS_BIN = str(HOME / ".local" / "bin" / "demucs")
STEM_NAMES = ["drums", "bass", "guitar", "vocals", "piano", "other"]
# Anything recorded into a song lands beside the separated stems and becomes a
# track like any other — the point of recording a guitar part is that it is
# still there after the guitar is unplugged.
TAKE_PREFIX = "take-"


def _takes_in(stems_dir: Path) -> list:
    """Recorded takes in a stem folder, in the order they were made."""
    found = []
    for path in stems_dir.glob(f"{TAKE_PREFIX}*"):
        if path.suffix.lower() not in STEM_READ_EXTS:
            continue
        name = path.stem
        try:
            index = int(name[len(TAKE_PREFIX):])
        except ValueError:
            continue
        found.append((index, name))
    return [name for _i, name in sorted(found)]


def _next_take_name(stems_dir: Path) -> str:
    used = set()
    for name in _takes_in(stems_dir):
        try:
            used.add(int(name[len(TAKE_PREFIX):]))
        except ValueError:
            pass
    n = 1
    while n in used:
        n += 1
    return f"{TAKE_PREFIX}{n}"


# The two extra guitar parts an outside separator (Moises) can be asked to
# split a song's guitar into — imported by hand rather than detected, since
# nothing local does this split. Fixed names, not numbered like takes: there
# is exactly one solo and one rhythm part, not an open-ended series of them.
GUITAR_PARTS = ["guitar_solo", "guitar_rhythm"]


def _guitar_parts_in(stems_dir: Path) -> list:
    return [name for name in GUITAR_PARTS if _stem_file(stems_dir, name) is not None]

# Stems are written as 320k MP3: six lossless stems of a five-minute track run
# to well over 300 MB, and a library of them fills a disk fast. Reading still
# accepts WAV so sets separated before this keep working.
STEM_EXT = ".mp3"
STEM_READ_EXTS = (".mp3", ".wav", ".flac")


def _stem_file(stems_dir: Path, stem: str) -> Optional[Path]:
    """The file for one stem, whatever format it was written in.

    Resolved through the same containment check as everything else: a plain
    is_file() here would happily follow a symlink out of the library, which is
    precisely what _resolve_under_music_root exists to stop.
    """
    try:
        rel_dir = stems_dir.resolve().relative_to(MUSIC_ROOT.resolve())
    except (ValueError, OSError):
        return None
    for ext in STEM_READ_EXTS:
        candidate = _resolve_under_music_root(f"{rel_dir}/{stem}{ext}")
        if candidate is not None and candidate.is_file():
            return candidate
    return None


_DEMUCS_DEVICE_CACHE: dict = {}


def _demucs_interpreter() -> Optional[str]:
    """The Python that actually runs Demucs, read from its shebang.

    Demucs is installed as its own isolated tool (uv/pipx), so torch lives in
    *that* interpreter — never in this server's venv.
    """
    try:
        first = Path(DEMUCS_BIN).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("#!"):
        return None
    return first[2:].strip() or None


def _demucs_device() -> Optional[str]:
    """Prefer Apple Silicon (MPS) acceleration when available — Demucs'
    own default is 'cuda if available else cpu', which would silently run
    on CPU (much slower, not broken) on a Mac with no explicit -d flag.
    Returns None (let Demucs pick its own default) if torch/MPS aren't
    available, so this never hard-fails on non-Apple-Silicon hardware.

    The probe deliberately runs in Demucs' *own* interpreter rather than
    importing torch here: this server's venv has no torch at all, so the
    old in-process `import torch` always raised and every separation
    silently fell back to CPU. Cached — spawning a Python just to ask
    about MPS costs ~1s and the answer can't change while we run.
    """
    if "device" in _DEMUCS_DEVICE_CACHE:
        return _DEMUCS_DEVICE_CACHE["device"]

    device = None
    interpreter = _demucs_interpreter()
    if interpreter:
        try:
            probe = subprocess.run(
                [interpreter, "-c",
                 "import torch;print('mps' if torch.backends.mps.is_available() else '')"],
                capture_output=True, text=True, timeout=60,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "mps":
                device = "mps"
        except (OSError, subprocess.SubprocessError):
            pass

    _DEMUCS_DEVICE_CACHE["device"] = device
    return device


def _stems_dir_for(safe_name: str) -> Path:
    return MUSIC_ROOT / "Stems" / safe_name


def _stems_ready(safe_name: str) -> bool:
    d = _stems_dir_for(safe_name)
    return d.is_dir() and all(_stem_file(d, s) is not None for s in STEM_NAMES)


def _finalize_stems_job(job: dict) -> None:
    """Moves Demucs' own output layout (<raw>/htdemucs_6s/<track>/<stem>.wav)
    into our MUSIC_ROOT/Stems/<safe-name>/<stem>.wav — called once a "stems"
    job's subprocess exits successfully. Demucs always nests its output one
    level deeper under a model-name folder ("-o OUT ... creates a subfolder
    with the model name") regardless of --filename, which is why this reaches
    into "htdemucs_6s" explicitly rather than just <raw>/<track>.
    """
    source = Path(job["stems_source"])
    safe_name = job["stems_name"]
    raw_track_dir = MUSIC_ROOT / "Stems" / ".raw" / "htdemucs_6s" / source.stem
    dest_dir = _stems_dir_for(safe_name)
    if not raw_track_dir.is_dir():
        raise FileNotFoundError(f"Demucs nevytvořil očekávaný výstup ({raw_track_dir})")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.move(str(raw_track_dir), str(dest_dir))


class StemsSeparateRequest(BaseModel):
    rel: str
    format: str = "mp3"       # "mp3" or "wav"
    mp3_bitrate: int = 320    # 128 / 192 / 256 / 320, MP3 only


@app.post("/api/stems/separate")
def separate_stems(req: StemsSeparateRequest):
    source = _resolve_under_music_root(req.rel)
    if source is None or not source.is_file():
        return JSONResponse({"error": "Zdrojový soubor nenalezen."}, status_code=404)

    safe_name = _safe_name(source.stem)
    if _stems_ready(safe_name):
        return {"job_id": None, "ready": True, "name": safe_name}

    job_id = uuid.uuid4().hex[:12]
    raw_dir = MUSIC_ROOT / "Stems" / ".raw"
    cmd = [
        DEMUCS_BIN, "-n", "htdemucs_6s", "-o", str(raw_dir),
        "--filename", "{track}/{stem}.{ext}",
    ]
    # MP3 by default: a six-stem set of one five-minute track is over 300 MB
    # lossless, and these get remixed, not mastered. WAV is there for anyone
    # who does want to master them.
    if req.format == "wav":
        pass                                   # Demucs' own default output
    else:
        kbps = req.mp3_bitrate if req.mp3_bitrate in (128, 192, 256, 320) else 320
        cmd += ["--mp3", "--mp3-bitrate", str(kbps), "--mp3-preset", "2"]
    device = _demucs_device()
    if device:
        cmd += ["-d", device]
    cmd.append(str(source))

    JOBS[job_id] = {
        "cmd": cmd,
        "status": "queued",
        "lines": [],
        "kind": "stems",
        "stems_source": str(source),
        "stems_name": safe_name,
    }
    return {"job_id": job_id, "ready": False, "name": safe_name}


@app.get("/api/stems")
def list_stems():
    stems_root = MUSIC_ROOT / "Stems"
    if not stems_root.is_dir():
        return []
    out = []
    for d in sorted(stems_root.glob("*"), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue  # ".raw" is Demucs' scratch area, not a stem set
        present, missing, total_bytes = [], [], 0
        # An imported guitar split sits right after drums — appending it at
        # the end (past piano/other) put it low enough in the mixer's rail
        # that it rendered clipped, out of view without scrolling.
        stem_order = (["drums"] + _guitar_parts_in(d)
                      + [s for s in STEM_NAMES if s != "drums"] + _takes_in(d))
        for stem in stem_order:
            f = _stem_file(d, stem)
            if f is not None:
                present.append(stem)
                try:
                    total_bytes += f.stat().st_size
                except OSError:
                    pass
            elif stem in STEM_NAMES:
                missing.append(stem)
        if not present:
            continue
        try:
            mtime = d.stat().st_mtime
        except OSError:
            mtime = 0
        out.append({
            "name": d.name,
            "stems": present,
            # A set missing a stem still loads, but the mixer is incomplete —
            # the library should say so rather than look identical to a full one.
            "missing": missing,
            "complete": not missing,
            "size_bytes": total_bytes,
            "modified": mtime,
            "rel": f"Stems/{d.name}",
        })
    return out


@app.post("/api/stems/convert")
def convert_stems_to_mp3(dry_run: bool = False):
    """Re-encode already-separated WAV stems to 320k MP3.

    New separations are written as MP3, but sets made before that are still
    lossless and are what actually fills the disk. Each WAV is encoded, the
    result verified, and only then is the original removed — a failed encode
    leaves the WAV exactly where it was.
    """
    stems_root = MUSIC_ROOT / "Stems"
    if not stems_root.is_dir():
        return {"converted": 0, "freed_bytes": 0, "songs": []}

    ffmpeg = ffmpeg_bin()
    converted = 0
    failed = []
    freed = 0
    songs = []

    for d in sorted(stems_root.glob("*")):
        if not d.is_dir() or d.name.startswith("."):
            continue
        wavs = [d / f"{stem}.wav" for stem in STEM_NAMES if (d / f"{stem}.wav").is_file()]
        if not wavs:
            continue
        song_freed = 0

        for wav in wavs:
            mp3 = wav.with_suffix(".mp3")
            try:
                before = wav.stat().st_size
            except OSError:
                continue
            if dry_run:
                song_freed += before
                converted += 1
                continue
            try:
                proc = subprocess.run(
                    [ffmpeg, "-v", "error", "-y", "-i", str(wav),
                     "-codec:a", "libmp3lame", "-b:a", "320k", str(mp3)],
                    capture_output=True, timeout=600,
                )
            except (OSError, subprocess.SubprocessError):
                failed.append(wav.name)
                continue

            # Only drop the original once there's a plausible encode in place.
            if proc.returncode != 0 or not mp3.is_file() or mp3.stat().st_size < 10_000:
                failed.append(str(wav.relative_to(stems_root)))
                mp3.unlink(missing_ok=True)
                continue

            song_freed += before - mp3.stat().st_size
            wav.unlink(missing_ok=True)
            converted += 1

        if song_freed:
            songs.append({"name": d.name, "freed_bytes": song_freed})
            freed += song_freed

    return {
        "converted": converted, "freed_bytes": freed,
        "songs": songs, "failed": failed, "dry_run": dry_run,
    }


# Bumped whenever beatgrid.py starts returning something the player needs;
# a cache written before that is a miss rather than a stale answer.
BEATGRID_VERSION = 2


def _beat_grid_for(stems_dir: Path, refresh: bool = False):
    """The cached grid, computing it first if there isn't one.

    Returns the grid, or a string saying why there is none — the chord
    analysis needs the same answer as the endpoint and must not have to go
    through HTTP to get it.
    """
    cache = stems_dir / "beats.json"
    if cache.is_file() and not refresh:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # A grid written before the tempo curve existed is missing a field
            # the player now draws; re-running costs a few seconds once.
            if cached.get("grid_version") == BEATGRID_VERSION:
                return cached
        except (json.JSONDecodeError, OSError):
            pass  # unreadable cache is just a cache miss

    interpreter = _demucs_interpreter()
    if interpreter is None:
        return "Detektor beatů není dostupný."

    for stem in ("drums", "other", "bass", "guitar", "piano", "vocals"):
        source = _stem_file(stems_dir, stem)
        if source is None:
            continue
        try:
            proc = subprocess.run(
                [interpreter, str(APP_DIR / "beatgrid.py"), str(source)],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"Detekce selhala: {exc}"

        line = (proc.stdout or "").strip().splitlines()
        if not line:
            continue
        try:
            data = json.loads(line[-1])
        except json.JSONDecodeError:
            continue
        if "error" in data:
            continue  # this stem had nothing trackable; try the next one

        data["source_stem"] = stem
        data["grid_version"] = BEATGRID_VERSION
        try:
            cache.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        return data

    return "Beaty se nepodařilo detekovat."


@app.get("/api/stems/{name}/beats")
def stems_beatgrid(name: str, refresh: bool = False):
    """Beat and downbeat grid for a separated song.

    Tracking runs on the isolated drums stem — by far the cleanest onset
    signal available, and the reason this waits for separation instead of
    analysing the mix. Falls back through the other stems for drumless
    material. The result is cached next to the stems, because inference
    costs a few seconds and the grid never changes for a given file.
    """
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    grid = _beat_grid_for(stems_dir, refresh)
    if isinstance(grid, str):
        code = 503 if "není dostupný" in grid else 422
        return JSONResponse({"error": grid}, status_code=code)
    return grid


# The analysis interpreter carries torch, librosa and the All-In-One
# checkpoints; the server's venv carries none of them.
ANALYZE_PYTHON = HOME / ".local" / "venvs" / "spotdl-analyze" / "bin" / "python"
# Sections take about half a minute and chords about ten seconds on a
# five-minute track, so two requests for the same song must not both run it.
_ANALYSIS_LOCKS: dict = {}


@app.get("/api/stems/{name}/analysis")
def stems_analysis(name: str, refresh: bool = False):
    """Song sections and chords for a separated song.

    Both come from the stems rather than the mix, which is the whole reason
    this waits for separation: All-In-One is trained on four stem
    spectrograms, and the chord chroma is far cleaner once the drums and
    vocals are out of it. Cached next to the stems — it costs the better part
    of a minute and never changes for a given file.
    """
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    cache = stems_dir / "analysis.json"
    if cache.is_file() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if not ANALYZE_PYTHON.is_file():
        return JSONResponse(
            {"error": "Analyzátor není nainstalován."}, status_code=503)

    lock = _ANALYSIS_LOCKS.setdefault(str(stems_dir), threading.Lock())
    with lock:
        # Another request may have finished it while this one waited.
        if cache.is_file() and not refresh:
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        # Chords are estimated once per beat, so the grid has to exist first.
        # On a freshly separated song the player asks for both at once and
        # this would otherwise race the beat endpoint and lose.
        _beat_grid_for(stems_dir)

        cmd = [str(ANALYZE_PYTHON), str(APP_DIR / "analyze.py"), str(stems_dir)]
        beats = stems_dir / "beats.json"
        if beats.is_file():
            cmd += ["--beats", str(beats)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            return JSONResponse({"error": f"Analýza selhala: {exc}"}, status_code=500)

        lines = (proc.stdout or "").strip().splitlines()
        if not lines:
            detail = (proc.stderr or "").strip().splitlines()[-1:] or ["bez výstupu"]
            return JSONResponse({"error": f"Analýza selhala: {detail[0][:200]}"},
                                status_code=500)
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError:
            return JSONResponse({"error": "Analýza vrátila neplatný výstup."},
                                status_code=500)

        try:
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return data


# What a section can be called. The detector only ever produces the first
# eight; the rest are here because a listener naming their own parts wants
# them, and the list is editable per song anyway.
DEFAULT_SECTION_PRESETS = [
    {"kind": "intro", "name": "Intro", "color": "#4a7fff"},
    {"kind": "verse", "name": "Sloka", "color": "#00c8a0"},
    {"kind": "prechorus", "name": "Předrefrén", "color": "#2ab8e8"},
    {"kind": "chorus", "name": "Refrén", "color": "#9a4aff"},
    {"kind": "bridge", "name": "Bridge", "color": "#ff8a20"},
    {"kind": "inst", "name": "Instrumentál", "color": "#e8c840"},
    {"kind": "solo", "name": "Sólo", "color": "#ff4a90"},
    {"kind": "break", "name": "Break", "color": "#2ab8e8"},
    {"kind": "outro", "name": "Outro", "color": "#00d4d4"},
    {"kind": "part", "name": "Část", "color": "#8391a5"},
]


def _clean_sections(raw) -> list:
    """Only well-formed, ordered, non-overlapping spans get stored."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0) or end - start < 0.2:
            continue
        name = str(item.get("name") or "Část")[:40]
        kind = str(item.get("kind") or "part")[:24]
        color = str(item.get("color") or "#8391a5")[:9]
        out.append({"start": round(start, 3), "end": round(end, 3),
                    "name": name, "kind": kind, "color": color})
    out.sort(key=lambda x: x["start"])
    return out[:200]


def _clean_presets(raw) -> list:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append({
            "kind": str(item.get("kind") or "part")[:24],
            "name": str(item["name"])[:40],
            "color": str(item.get("color") or "#8391a5")[:9],
        })
    return out[:40] or list(DEFAULT_SECTION_PRESETS)


@app.get("/api/stems/{name}/sections")
def get_sections(name: str):
    """The song's sections — the listener's own if they have edited any,
    otherwise whatever the analysis found."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    edited = stems_dir / "sections.json"
    if edited.is_file():
        try:
            data = json.loads(edited.read_text(encoding="utf-8"))
            return {"sections": _clean_sections(data.get("sections")),
                    "presets": _clean_presets(data.get("presets")),
                    "edited": True}
        except (json.JSONDecodeError, OSError):
            pass

    analysis = stems_dir / "analysis.json"
    sections = []
    if analysis.is_file():
        try:
            sections = _clean_sections(
                json.loads(analysis.read_text(encoding="utf-8")).get("sections"))
        except (json.JSONDecodeError, OSError):
            sections = []
    return {"sections": sections,
            "presets": list(DEFAULT_SECTION_PRESETS), "edited": False}


class SectionsIn(BaseModel):
    sections: List[dict] = []
    presets: Optional[List[dict]] = None


@app.put("/api/stems/{name}/sections")
def put_sections(name: str, body: SectionsIn):
    """Store the listener's own sections.

    Written beside the stems rather than into analysis.json, so re-running the
    detector never overwrites hand-made edits.
    """
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    data = {"sections": _clean_sections(body.sections),
            "presets": _clean_presets(body.presets)}
    try:
        (stems_dir / "sections.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return {**data, "edited": True}


@app.delete("/api/stems/{name}/sections")
def reset_sections(name: str):
    """Throw the edits away and fall back to what the detector found."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        (stems_dir / "sections.json").unlink(missing_ok=True)
    except OSError:
        pass
    return get_sections(name)


def _clean_chords(raw) -> list:
    """Only well-formed, ordered, non-overlapping spans get stored. Chord
    and bass are free-text (the wheel builds them client-side, e.g. "F#m",
    "Cadd9") rather than an enum, so the only real validation is shape."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (end > start >= 0):
            continue
        chord = item.get("chord")
        chord = str(chord)[:16] if chord else None
        bass = item.get("bass")
        bass = str(bass)[:8] if bass else None
        out.append({"start": round(start, 3), "end": round(end, 3),
                    "chord": chord, "bass": bass})
    out.sort(key=lambda x: x["start"])
    return out[:2000]


@app.get("/api/stems/{name}/chords")
def get_chords(name: str):
    """The song's chords — the listener's own if they have edited any,
    otherwise whatever the analysis found."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    edited = stems_dir / "chords.json"
    if edited.is_file():
        try:
            data = json.loads(edited.read_text(encoding="utf-8"))
            return {"chords": _clean_chords(data.get("chords")), "edited": True}
        except (json.JSONDecodeError, OSError):
            pass

    analysis = stems_dir / "analysis.json"
    chords = []
    if analysis.is_file():
        try:
            chords = _clean_chords(
                json.loads(analysis.read_text(encoding="utf-8")).get("chords"))
        except (json.JSONDecodeError, OSError):
            chords = []
    return {"chords": chords, "edited": False}


class ChordsIn(BaseModel):
    chords: List[dict] = []


@app.put("/api/stems/{name}/chords")
def put_chords(name: str, body: ChordsIn):
    """Store the listener's own chords, beside the stems rather than into
    analysis.json, so re-running the detector never overwrites hand-made
    edits."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    data = {"chords": _clean_chords(body.chords)}
    try:
        (stems_dir / "chords.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return {**data, "edited": True}


@app.delete("/api/stems/{name}/chords")
def reset_chords(name: str):
    """Throw the edits away and fall back to what the detector found."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        (stems_dir / "chords.json").unlink(missing_ok=True)
    except OSError:
        pass
    return get_chords(name)


class EqBand(BaseModel):
    freq: float
    gain: float
    q: float


class MixerChannel(BaseModel):
    stem: str
    muted: bool = False
    solo: bool = False
    faderValue: float = 1.0
    pitchRate: float = 1.0
    panValue: float = 0.0
    clipOffset: float = 0.0
    eq: Optional[List[EqBand]] = None


class MetronomeSettings(BaseModel):
    volume: float = 0.6
    pan: float = 0.0
    muted: bool = False
    voice: str = "beep"
    rate: float = 1.0


class GuitarSettings(BaseModel):
    volume: float = 0.9
    pan: float = 0.0
    muted: bool = False
    solo: bool = False
    threshold: float = -100.0
    compOn: bool = False
    compThreshold: float = -24.0
    compRatio: float = 4.0
    compAttack: float = 0.02
    compRelease: float = 0.25
    deviceId: Optional[str] = None
    ampName: Optional[str] = None
    ampFile: Optional[str] = None
    cabName: Optional[str] = None
    eq: Optional[List[EqBand]] = None


class MixerSession(BaseModel):
    channels: List[MixerChannel] = []
    loop: Optional[dict] = None
    pixelsPerSecond: Optional[float] = None
    tempo: Optional[float] = None
    # The click is a channel like any other — its level, pan and sound are
    # part of the mix, and re-picking them on every open is a chore.
    metronome: Optional[MetronomeSettings] = None
    guitar: Optional[GuitarSettings] = None


@app.get("/api/stems/{name}/session")
def get_session(name: str):
    """The mixer as it was left for this song.

    Beside the stems rather than in the browser's localStorage, which was
    where this used to live: localStorage is per browser profile, and the
    menu bar opens the app in a profile of its own, so a mix set up in one
    window was simply not there in the other.
    """
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    path = stems_dir / "session.json"
    if not path.is_file():
        return {"channels": [], "loop": None, "pixelsPerSecond": None,
                "tempo": None, "saved": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"channels": [], "loop": None, "pixelsPerSecond": None,
                "tempo": None, "saved": False}
    data["saved"] = True
    return data


@app.put("/api/stems/{name}/session")
def put_session(name: str, body: MixerSession):
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    data = body.model_dump()
    # A fader that came back as 14 instead of 1.4 would blow the ears off
    # whoever opened the song next.
    for ch in data["channels"]:
        ch["faderValue"] = max(0.0, min(2.0, float(ch["faderValue"])))
        ch["pitchRate"] = max(0.25, min(4.0, float(ch["pitchRate"])))
        ch["panValue"] = max(-1.0, min(1.0, float(ch["panValue"])))
        ch["clipOffset"] = max(-600.0, min(600.0, float(ch["clipOffset"])))
    metro = data.get("metronome")
    if metro:
        metro["volume"] = max(0.0, min(2.0, float(metro["volume"])))
        metro["pan"] = max(-1.0, min(1.0, float(metro["pan"])))
        metro["rate"] = max(0.25, min(4.0, float(metro["rate"])))
    gtr = data.get("guitar")
    if gtr:
        gtr["volume"] = max(0.0, min(2.0, float(gtr["volume"])))
        gtr["pan"] = max(-1.0, min(1.0, float(gtr["pan"])))
        gtr["threshold"] = max(-100.0, min(0.0, float(gtr["threshold"])))
        gtr["compThreshold"] = max(-60.0, min(0.0, float(gtr["compThreshold"])))
        gtr["compRatio"] = max(1.0, min(20.0, float(gtr["compRatio"])))
        gtr["compAttack"] = max(0.0, min(0.1, float(gtr["compAttack"])))
        gtr["compRelease"] = max(0.02, min(1.0, float(gtr["compRelease"])))
    try:
        (stems_dir / "session.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return {**data, "saved": True}


@app.delete("/api/stems/{name}/session")
def reset_session(name: str):
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        (stems_dir / "session.json").unlink(missing_ok=True)
    except OSError:
        pass
    return {"channels": [], "loop": None, "pixelsPerSecond": None,
            "tempo": None, "saved": False}


@app.post("/api/stems/{name}/record")
async def save_take(name: str, request: Request):
    """Store a recorded take as a track of this song.

    The body is a WAV the browser encoded from what it captured; it is
    re-encoded to 320k MP3 like every other stem, so a take costs about eight
    megabytes rather than forty and sits in the folder as an ordinary track.
    The whole point of recording a guitar part here is that it is still a
    track after the guitar is unplugged.
    """
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    raw = await request.body()
    if len(raw) < 1024:
        return JSONResponse({"error": "Nahrávka je prázdná."}, status_code=400)
    if len(raw) > 600 * 1024 * 1024:
        return JSONResponse({"error": "Nahrávka je příliš velká."}, status_code=413)

    take = _next_take_name(stems_dir)
    target = stems_dir / f"{take}.mp3"
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error",
             "-i", str(tmp_path), "-codec:a", "libmp3lame", "-b:a", "320k",
             str(target)],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        tmp_path.unlink(missing_ok=True)
        return JSONResponse({"error": f"Převod selhal: {exc}"}, status_code=500)
    finally:
        tmp_path.unlink(missing_ok=True)

    if proc.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["neznámá chyba"]
        return JSONResponse({"error": f"Převod selhal: {detail[0][:200]}"},
                            status_code=500)

    return {"stem": take, "bytes": target.stat().st_size,
            "takes": _takes_in(stems_dir)}


@app.delete("/api/stems/{name}/record/{take}")
def delete_take(name: str, take: str):
    """Remove one recorded take. Only takes — the separated stems are not
    something an errant request should be able to delete."""
    if not take.startswith(TAKE_PREFIX):
        return JSONResponse({"error": "Smazat jde jen nahrávku."}, status_code=400)
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    path = _stem_file(stems_dir, take)
    if path is None:
        return JSONResponse({"error": "Nahrávka nenalezena."}, status_code=404)
    try:
        path.unlink()
    except OSError as exc:
        return JSONResponse({"error": f"Smazání selhalo: {exc}"}, status_code=500)
    return {"takes": _takes_in(stems_dir)}


async def _save_guitar_part(stems_dir: Path, dest_stem: str, upload: UploadFile) -> dict:
    """Converts one uploaded guitar-part file to 320k MP3 beside the other
    stems, the same encode _record_take already uses — so it plays back
    through the exact same path as every other stem, not a special case."""
    data = await upload.read()
    if len(data) < 1024:
        return {"error": f"{dest_stem}: soubor je prázdný."}
    if len(data) > 600 * 1024 * 1024:
        return {"error": f"{dest_stem}: soubor je příliš velký."}

    target = stems_dir / f"{dest_stem}.mp3"
    suffix = Path(upload.filename or "").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error",
             "-i", str(tmp_path), "-codec:a", "libmp3lame", "-b:a", "320k",
             str(target)],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{dest_stem}: převod selhal ({exc})."}
    finally:
        tmp_path.unlink(missing_ok=True)

    if proc.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["neznámá chyba"]
        return {"error": f"{dest_stem}: převod selhal ({detail[0][:200]})"}
    return {"stem": dest_stem, "bytes": target.stat().st_size}


@app.post("/api/stems/{name}/guitar-parts/import")
async def import_guitar_parts(name: str,
                               solo: UploadFile = File(...),
                               rhythm: UploadFile = File(...)):
    """Brings a solo/rhythm guitar split made elsewhere (nothing local does
    this) into the song as two extra stems, guitar_solo and guitar_rhythm —
    the original combined "guitar" stem is left in place untouched, muted
    by the frontend rather than deleted, so the split is easy to back out
    of if it isn't an improvement."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    solo_res = await _save_guitar_part(stems_dir, "guitar_solo", solo)
    rhythm_res = await _save_guitar_part(stems_dir, "guitar_rhythm", rhythm)
    errors = [r["error"] for r in (solo_res, rhythm_res) if "error" in r]
    if errors:
        return JSONResponse({"error": " / ".join(errors)}, status_code=400)
    return {"saved": [solo_res["stem"], rhythm_res["stem"]],
            "guitar_parts": _guitar_parts_in(stems_dir)}


@app.delete("/api/stems/{name}/guitar-parts")
def delete_guitar_parts(name: str):
    """Removes both imported parts, falling back to the original combined
    guitar stem (which was never touched)."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    for part in GUITAR_PARTS:
        f = _stem_file(stems_dir, part)
        if f is not None:
            try:
                f.unlink()
            except OSError:
                pass
    return {"guitar_parts": _guitar_parts_in(stems_dir)}


@app.get("/api/stems/{name}/{stem}")
def stream_stem(name: str, stem: str, request: Request):
    """Serves one stem, whatever format it was written in.

    The extension is optional and ignored — older builds requested
    "<stem>.wav" and those URLs still resolve.
    """
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    # Separated stems, plus whatever was recorded into this song, plus an
    # imported guitar split — all three have to stream like any other stem.
    if (stem not in STEM_NAMES and stem not in GUITAR_PARTS
            and not _re_take.fullmatch(stem)):
        return JSONResponse({"error": "Neplatná stopa."}, status_code=404)

    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)

    target = _stem_file(stems_dir, stem)
    if target is None:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    # _range_file_response derives the content type from the extension.
    return _range_file_response(target, request.headers.get("range"))


# ----------------------------------------------------------------- lyrics

import re as _re  # for LRC parsing (lyrics feature)


def _parse_lrc_cents_to_ms(cc: str) -> int:
    """Convert LRC centiseconds string to milliseconds.
    ".1" -> 100, ".12" -> 120, ".123" -> 123
    """
    s = cc
    while len(s) < 3:
        s += "0"
    if len(s) > 3:
        s = s[:3]
    return int(s) if s.isdigit() else 0


def _parse_lrc(raw: str) -> Optional[list]:
    """Parse LRC format text into structured synced lyrics lines.

    Returns a list of dicts with keys: timeInMs, duration, text, id
    or None if parsing yields no valid lines.
    """
    lines = raw.split("\n")
    time_pattern = _re.compile(r"\[(\d{2,}):(\d{2})\.(\d{2,3})\]")
    metadata_pattern = _re.compile(r"\[([a-z]+):([^\]]+)\]")

    unmerged = []
    offset_ms = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check for metadata tags like [offset:100]
        meta_match = metadata_pattern.search(line)
        if meta_match:
            key = meta_match.group(1).lower()
            value = meta_match.group(2).strip()
            if key == "offset":
                try:
                    offset_ms = int(value)
                except ValueError:
                    pass
            # Skip pure metadata lines (no timestamp)
            if not time_pattern.search(line):
                continue

        time_matches = time_pattern.findall(line)
        if not time_matches:
            continue

        # Extract text (remove all timestamp tags)
        text = time_pattern.sub("", line).strip()

        for mm_str, ss_str, ms_str in time_matches:
            mm = int(mm_str)
            ss = int(ss_str)
            ms = _parse_lrc_cents_to_ms(ms_str)
            time_ms = (mm * 60 * 1000) + (ss * 1000) + ms - offset_ms
            unmerged.append({
                "timeInMs": max(0, time_ms),
                "text": text,
            })

    if not unmerged:
        return None

    # Sort by time
    unmerged.sort(key=lambda x: x["timeInMs"])

    result = []
    # Insert empty line at 0ms if first line is > 300ms
    if unmerged[0]["timeInMs"] > 300:
        result.append({
            "timeInMs": 0,
            "duration": unmerged[0]["timeInMs"],
            "text": "",
            "id": "line-0",
        })

    for i, entry in enumerate(unmerged):
        if i < len(unmerged) - 1:
            duration = unmerged[i + 1]["timeInMs"] - entry["timeInMs"]
        else:
            duration = 5000  # default 5s for last line
        result.append({
            "timeInMs": entry["timeInMs"],
            "duration": duration,
            "text": entry["text"],
            "id": f"line-{len(result)}",
        })

    return result


_LYRICS_CACHE: dict = {}
_LYRICS_CACHE_TTL = 30 * 60  # 30 minutes


def _fetch_ytmusic_lyrics(title: str, artist: str, video_id: str = "") -> Optional[dict]:
    """Lyrics from YouTube Music, as a fallback when LRCLib has nothing.

    LRCLib's catalogue is thin outside English-language pop — it knows almost
    no Czech repertoire — while YouTube Music carries lyrics for most of what
    it streams. These are plain text, not timed, so the panel scrolls them
    without highlighting; that's still better than an empty panel.
    """
    try:
        client = yt()
        vid = video_id
        if not vid or len(vid) != 11:
            query = f"{artist} {title}".strip()
            if not query:
                return None
            hits = client.search(query, filter="songs", limit=1)
            if not hits:
                return None
            vid = hits[0].get("videoId")
        if not vid:
            return None

        watch = client.get_watch_playlist(videoId=vid)
        lyrics_id = watch.get("lyrics")
        if not lyrics_id:
            return None

        payload = client.get_lyrics(lyrics_id)
    except Exception:
        return None

    text = (payload or {}).get("lyrics")
    if not text or not text.strip():
        return None
    return {
        "synced": False,
        "lines": None,
        "plain": text,
        "source": (payload or {}).get("source") or "YouTube Music",
    }


def _fetch_lrclib(track_name: str, artist_name: str, album_name: str = None, duration: float = None) -> Optional[dict]:
    """Fetch synced lyrics from LRCLib API.

    Returns dict with keys: synced (bool), lines (list or None), plain (str or None), source (str)
    """
    cache_key = f"{track_name.lower()}|{artist_name.lower()}"
    cached = _LYRICS_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached["data"]

    try:
        params = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
        if album_name:
            params["album_name"] = album_name

        req = httpx.get("https://lrclib.net/api/search", params=params, headers={"User-Agent": "Spotifylisty/1.0"}, timeout=15.0)
        if req.status_code != 200:
            return None

        results = req.json()
        if not results or not isinstance(results, list):
            return None

        # Filter valid results
        valid = [
            r for r in results
            if (r.get("syncedLyrics") or r.get("plainLyrics"))
            and not r.get("instrumental")
        ]
        if not valid:
            return None

        # Find best match by duration
        best = valid[0]
        if duration and any(r.get("duration") for r in valid):
            target_dur = duration
            best = min(valid, key=lambda r: abs((r.get("duration") or 0) - target_dur))

        data = {
            "synced": False,
            "lines": None,
            "plain": best.get("plainLyrics"),
            "source": "LRCLib",
        }

        if best.get("syncedLyrics"):
            parsed = _parse_lrc(best["syncedLyrics"])
            if parsed:
                data["synced"] = True
                data["lines"] = parsed

        _LYRICS_CACHE[cache_key] = {"data": data, "expires": time.time() + _LYRICS_CACHE_TTL}
        return data
    except Exception:
        return None


@app.get("/api/lyrics/search")
def search_lyrics(q: str = "", artist: str = "", album: str = "", duration: float = None,
                  video_id: str = ""):
    """Search for lyrics via LRCLib.

    If q is provided but artist is not, q is treated as "artist - title" or just title.
    """
    title = q
    if not artist and " - " in q:
        parts = q.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()

    if not title:
        return {"synced": False, "lines": None, "plain": None, "source": None}

    result = _fetch_lrclib(title, artist, album, duration)
    if not result or not (result.get("lines") or result.get("plain")):
        result = _fetch_ytmusic_lyrics(title, artist, video_id) or result
    return result or {"synced": False, "lines": None, "plain": None, "source": None}


# ----------------------------------------------------------------- podcasts

# A single "podcast" query returns almost nothing Czech, because YouTube Music
# ranks by global popularity. Searching a handful of Czech-language terms and
# merging the results is what actually surfaces Czech shows.
CZ_PODCAST_QUERIES = [
    "český podcast",
    "podcast česky",
    "rozhovor podcast cz",
    "Český rozhlas podcast",
    "Seznam Zprávy podcast",
    "Aktuálně podcast",
    "kriminální podcast česky",
    "historie podcast česky",
    "věda podcast česky",
    "komedie podcast česky",
    "vesmír podcast česky",
    "technologie podcast česky",
    "sociologie podcast česky",
    "psychologie podcast česky",
    "hudba podcast česky",
    "filozofie podcast česky",
    "byznys podcast česky",
    "zdraví podcast česky",
    "sport podcast česky",
    "cestování podcast česky",
]

# The sidebar's Podcasty branch (Věda, Vesmír, Humor, …) — one query per
# topic, run on demand only when that branch is opened rather than folded
# into the default CZ pool above, so picking a topic actually narrows things
# instead of the same 40-show mix appearing under every heading.
PODCAST_TOPICS = [
    ("veda", "Věda", "věda podcast česky"),
    ("vesmir", "Vesmír", "vesmír astronomie podcast česky"),
    ("humor", "Humor", "humor komedie podcast česky"),
    ("technika", "Technika", "technologie technika podcast česky"),
    ("sociologie", "Sociologie", "sociologie společnost podcast česky"),
    ("psychologie", "Psychologie", "psychologie podcast česky"),
    ("historie", "Historie", "historie podcast česky"),
    ("hudba", "Hudba", "hudba podcast česky"),
    ("filozofie", "Filozofie", "filozofie podcast česky"),
    ("zdravi", "Zdraví", "zdraví podcast česky"),
    ("byznys", "Byznys a finance", "byznys finance podcast česky"),
    ("sport", "Sport", "sport podcast česky"),
    ("cestovani", "Cestování", "cestování podcast česky"),
    ("literatura", "Literatura", "knihy literatura podcast česky"),
    ("politika", "Politika", "politika podcast česky"),
]

INTL_PODCAST_QUERIES = ["podcast"]


def _podcast_rows(client, query, limit):
    try:
        results = client.search(query, filter="podcasts", limit=limit)
    except Exception:
        return []
    rows = []
    for r in results:
        bid = r.get("browseId")
        if not bid:
            continue
        rows.append({
            "id": bid,
            "title": r.get("title"),
            "author": r.get("author"),
            "publisher": r.get("author"),
            "thumbnail": _best_thumb(r.get("thumbnails")),
            "type": "podcast",
        })
    return rows


@app.get("/api/podcasts/topics")
def podcast_topics():
    """Topic list for the sidebar's Podcasty branch — key/label only; the
    actual query string stays server-side."""
    return [{"key": key, "label": label} for key, label, _q in PODCAST_TOPICS]


@app.get("/api/podcasts")
def search_podcasts(q: str = "", region: str = "cz", limit: int = 40, topic: str = ""):
    """Search for podcasts via YouTube Music (which indexes podcast shows).

    With no query, `region` picks a curated set of searches; Czech is the
    default because a bare "podcast" search returns essentially no Czech
    shows. `topic` (a PODCAST_TOPICS key) runs that one topic's query alone,
    for the sidebar's Podcasty branch. Episode fetching is deferred to
    /api/podcasts/{show_id}.
    """
    client = yt()

    if topic.strip():
        match = next((t for t in PODCAST_TOPICS if t[0] == topic.strip()), None)
        if not match:
            return JSONResponse({"error": "Neplatné téma."}, status_code=400)
        return _podcast_rows(client, match[2], limit)

    if q.strip():
        return _podcast_rows(client, q.strip(), limit)

    queries = CZ_PODCAST_QUERIES if region == "cz" else INTL_PODCAST_QUERIES
    per_query = max(4, limit // max(1, len(queries)) + 2)

    out, seen = [], set()
    for query in queries:
        for row in _podcast_rows(client, query, per_query):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            out.append(row)
            if len(out) >= limit:
                return out
    return out


# "1 hr 4 min", "23 min", "45 sec" — the shapes get_podcast returns.
_re_dur = __import__("re").compile(r"(\d+)\s*(hr|hour|h|min|m|sec|s)\b", __import__("re").I)


def _episode_seconds(text) -> int:
    """Seconds from get_podcast's human duration ("23 min", "1 hr 4 min",
    "12:34"). Returns 0 when it can't be read, which the caller renders as
    the original text.
    """
    if not text:
        return 0
    text = str(text).strip()
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            return 0
        seconds = 0
        for n in nums:
            seconds = seconds * 60 + n
        return seconds
    total = 0
    for value, unit in _re_dur.findall(text):
        n = int(value)
        if unit.startswith("h"):
            total += n * 3600
        elif unit.startswith("m"):
            total += n * 60
        else:
            total += n
    return total


@app.get("/api/podcasts/{show_id}")
def podcast_episodes(show_id: str, limit: int = 50):
    """Fetch episodes for a podcast show.

    YouTube Music's get_playlist returns podcast episodes as tracks when
    the show_id is a podcast browse ID.
    """
    # A podcast browse id is not a playlist id: get_playlist raises a
    # KeyError on the response shape YouTube returns for shows. get_podcast
    # is the right call; get_playlist stays as a fallback for anything that
    # is actually a playlist of episodes.
    show = None
    raw_episodes = []
    try:
        show = yt().get_podcast(show_id, limit=limit)
        raw_episodes = show.get("episodes") or []
    except Exception:
        try:
            show = yt().get_playlist(show_id, limit=limit)
            raw_episodes = show.get("tracks") or []
        except Exception:
            return {"id": show_id, "title": "", "episodes": []}

    episodes = []
    for t in raw_episodes:
        video_id = t.get("videoId")
        if not video_id:
            continue
        seconds = t.get("duration_seconds") or _episode_seconds(t.get("duration"))
        episodes.append({
            "id": video_id,
            "title": t.get("title"),
            "description": t.get("description"),
            "date": (t.get("date") or {}).get("text") if isinstance(t.get("date"), dict) else t.get("date"),
            "duration": seconds,
            "durationFormatted": _fmt_dur(seconds) if seconds else (t.get("duration") or ""),
            "thumbnail": _best_thumb(t.get("thumbnails")),
            # Episodes stream through the same audio proxy as everything else.
            "audioUrl": f"/api/stream/{video_id}",
            "videoId": video_id,
        })

    author = show.get("author")
    if isinstance(author, dict):
        author = author.get("name")

    return {
        "id": show_id,
        "title": show.get("title"),
        "author": author,
        "description": show.get("description"),
        "thumbnail": _best_thumb(show.get("thumbnails")),
        "episodes": episodes,
    }


@app.get("/api/lyrics/{video_id}")
def get_lyrics_by_video(video_id: str, q: str = "", artist: str = "", album: str = "", duration: float = None):
    """Get lyrics for a video ID. Uses LRCLib with metadata from the caller."""
    title = q
    if not artist and " - " in q:
        parts = q.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()

    if not title:
        return {"synced": False, "lines": None, "plain": None, "source": None}

    result = _fetch_lrclib(title, artist, album, duration)
    if not result or not (result.get("lines") or result.get("plain")):
        result = _fetch_ytmusic_lyrics(title, artist, video_id) or result
    return result or {"synced": False, "lines": None, "plain": None, "source": None}
