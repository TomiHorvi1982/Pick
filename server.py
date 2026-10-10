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
import hashlib
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, unquote

import httpx
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

HOME = Path.home()
APP_DIR = Path(__file__).parent
SPOTDL_BIN = str(HOME / ".local" / "bin" / "spotdl")


def ffmpeg_bin() -> str:
    """Where ffmpeg is. PATH first, then the environment override a packaged
    build sets, then the Homebrew path this machine happens to use."""
    return (os.environ.get("PICK_FFMPEG")
            or shutil.which("ffmpeg")
            or "/opt/homebrew/bin/ffmpeg")
# What Chrome decodes by itself (checked against Chrome for Testing: MP3,
# AAC in any MP4 container, FLAC, WAV, Ogg Vorbis/Opus/FLAC, WebM).
BROWSER_AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".ogg", ".oga", ".opus", ".webm", ".weba"}
# Audio it can't (AIFF, WMA, AC3, Matroska, CAF, AU, MP2, TTA, WavPack, APE,
# DSD, AMR, …): shown and played all the same, converted once by ffmpeg into
# a cached copy — see _playable_copy.
TRANSCODE_AUDIO_EXTS = {".aif", ".aiff", ".aifc", ".wma", ".ac3", ".eac3", ".dts", ".mka", ".caf",
                        ".au", ".snd", ".mp2", ".mpa", ".tta", ".wv", ".ape", ".dsf", ".dff", ".amr",
                        ".awb", ".ra", ".mpc", ".spx", ".w64", ".rf64", ".voc", ".gsm"}
AUDIO_EXTS = BROWSER_AUDIO_EXTS | TRANSCODE_AUDIO_EXTS
# Standard MIDI files: listed in the library and dragged onto a MIDI track.
MIDI_EXTS = {".mid", ".midi"}
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


@app.get("/static/{name}")
def app_icon(name: str):
    """The app's own mark: the logo in the page and the tab icon."""
    from fastapi.responses import Response
    if name == "analyzer-engine.js":
        # The Guitar Performance Analyzer's engine: loaded as a Worker.
        return Response((APP_DIR / "static" / name).read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})
    if name not in ("pick-icon.png", "pick-favicon.png"):
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return Response((APP_DIR / "static" / name).read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/analyzer", response_class=HTMLResponse)
def analyzer_page():
    """Guitar Performance Analyzer — a page of its own, shown inside the app
    under Tools. All of its analysis runs in the browser."""
    return (APP_DIR / "static" / "analyzer.html").read_text(encoding="utf-8")


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


_re_alphatab_asset = _re_mod.compile(r"(font/|soundfont/)?[A-Za-z0-9_.-]+\.(js|mjs|woff2?|otf|eot|svg|txt|sf2|sf3)")


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


@app.get("/static/kytara-nastroj/{name}")
def kytara_nastroj_asset(name: str):
    """The "guitar as an instrument" library, installed as a local npm package
    (package.json → node_modules/kytara-nastroj). Only its built ES modules
    are served; the page imports them with import().
    """
    if not _re_nam_asset.fullmatch(name) or not name.endswith((".js", ".mjs")):
        return JSONResponse({"error": "Neplatný název."}, status_code=400)
    path = APP_DIR / "node_modules" / "kytara-nastroj" / "dist" / name
    if not path.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    return Response(
        path.read_bytes(),
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
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


# ---------------------------------------------------- audio Chrome can't play
TRANSCODE_CACHE_DIR = HOME / ".cache" / "spotdl-helper" / "transcode"
TRANSCODE_CACHE_MAX = 3 * 1024 ** 3
_LOSSLESS_EXTS = {".aif", ".aiff", ".aifc", ".caf", ".au", ".snd", ".tta", ".wv", ".ape",
                  ".dsf", ".dff", ".w64", ".rf64", ".voc"}
_transcode_locks: dict = {}
_codec_cache: dict = {}


def _audio_codec(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    key = (str(path), st.st_mtime, st.st_size)
    if key in _codec_cache:
        return _codec_cache[key]
    codec = ""
    try:
        probe = shutil.which("ffprobe") or str(Path(ffmpeg_bin()).with_name("ffprobe"))
        r = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, timeout=15)
        codec = r.stdout.strip().lower()
    except (OSError, subprocess.TimeoutExpired):
        pass
    _codec_cache[key] = codec
    return codec


def _needs_transcode(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in TRANSCODE_AUDIO_EXTS:
        return True
    # An .m4a can hold Apple Lossless, which Chrome doesn't decode.
    if ext in {".m4a", ".m4b"}:
        return _audio_codec(path) == "alac"
    return False


def _prune_transcode_cache():
    try:
        files = sorted((p for p in TRANSCODE_CACHE_DIR.iterdir() if p.is_file()),
                       key=lambda p: p.stat().st_atime)
        total = sum(p.stat().st_size for p in files)
        while files and total > TRANSCODE_CACHE_MAX:
            p = files.pop(0)
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _playable_copy(path: Path) -> Optional[Path]:
    """A copy of `path` Chrome can play, made once and cached (lossless
    sources → FLAC, lossy → AAC). The original is never touched."""
    import hashlib
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:20]
    lossless = path.suffix.lower() in _LOSSLESS_EXTS or _audio_codec(path) == "alac"
    out = TRANSCODE_CACHE_DIR / (key + (".flac" if lossless else ".m4a"))
    if out.is_file() and out.stat().st_size > 0:
        return out
    lock = _transcode_locks.setdefault(key, threading.Lock())
    with lock:
        if out.is_file() and out.stat().st_size > 0:
            return out
        TRANSCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part" + out.suffix)
        codec = ["-c:a", "flac"] if lossless else ["-c:a", "aac", "-b:a", "256k"]
        try:
            r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(path), "-vn",
                                "-map_metadata", "0", *codec, str(tmp)],
                               capture_output=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            tmp.unlink(missing_ok=True)
            return None
        if r.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            return None
        tmp.replace(out)
    _prune_transcode_cache()
    return out


def _resolve_amp_path(filename: str) -> Optional[Path]:
    """Resolves a client-supplied amp/IR file id to a real path on disk.
    The client only ever gets to name a category (checked against
    AMP_CATEGORIES) and a bare filename (basename only) — the actual
    directory always comes from the server side, so this can't be used to
    walk out of the amps folder or the one mapped external directory.
    """
    if filename.startswith("lib:"):
        # A .nam file anywhere in the music library (the "ALL" list).
        path = _resolve_under_music_root(filename[len("lib:"):])
        return path if path is not None and path.suffix.lower() == ".nam" else None
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


def _all_amps() -> list:
    """Every NAM profile the app knows about: the three categories (uploaded
    and mapped folders) plus any .nam file in the music library, each once."""
    out, seen = [], set()

    def add(item, path):
        try:
            key = str(path.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        out.append(item)

    for cat in AMP_CATEGORIES:
        cat_dir = AMPS_DIR / cat
        for path in sorted(cat_dir.glob("*.nam")) if cat_dir.is_dir() else []:
            if path.is_file():
                add({"name": path.stem, "file": f"{cat}/{path.name}", "kind": "model",
                     "bytes": path.stat().st_size, "category": cat}, path)
        ext_dir = _amp_category_dir(cat)
        for path in sorted(ext_dir.glob("*.nam")) if ext_dir is not None else []:
            if path.is_file():
                add({"name": path.stem, "file": f"ext:{cat}/{path.name}", "kind": "model",
                     "bytes": path.stat().st_size, "category": cat, "external": True}, path)
    root = MUSIC_ROOT.resolve()
    try:
        lib = sorted(root.rglob("*.nam"), key=lambda q: str(q).lower())
    except OSError:
        lib = []
    for path in lib:
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts):
            rel = path.relative_to(root).as_posix()
            add({"name": path.stem, "file": f"lib:{rel}", "kind": "model",
                 "bytes": path.stat().st_size, "category": "library"}, path)
    out.sort(key=lambda a: a["name"].lower())
    return out


@app.get("/api/amps")
def list_amps(category: str = ""):
    if category == "all":
        return _all_amps()
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
                "count": _int_or_none(r.get("itemCount")),
            }
        )
    return out


def _int_or_none(v):
    """"1,234" / "50 songs" / 50 → the number; anything else → None."""
    m = _re_mod.search(r"\d[\d,. \u00a0]*", str(v or ""))
    if not m:
        return None
    digits = _re_mod.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None


_PL_SUMMARY: dict = {}        # browseId -> {"ts", "data"}


@app.get("/api/playlist/{browse_id}/summary")
def playlist_summary(browse_id: str):
    """How many tracks a playlist has and how long it plays, for its card.

    The length is the detail page's own estimate (the average of the tracks
    it loads times the count), cached — a grid asks for dozens at once."""
    hit = _PL_SUMMARY.get(browse_id)
    if hit and time.time() - hit["ts"] < 6 * 3600:
        return hit["data"]
    d = playlist_detail(browse_id, track_limit=100)
    if not isinstance(d, dict):
        return d
    data = {"count": d.get("track_count") or 0, "seconds": d.get("estimated_total_duration_sec") or 0}
    _PL_SUMMARY[browse_id] = {"ts": time.time(), "data": data}
    if len(_PL_SUMMARY) > 2000:
        _PL_SUMMARY.clear()
    return data


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
        # Per-track year isn't a field ytmusicapi's get_album() returns —
        # every track on one release shares the album's own year, so that's
        # what the playlist table's Rok column sorts on for anything opened
        # this way. A generic (non-album) playlist mixing tracks from many
        # releases has no such single source and is left without one.
        if not t.get("year") and album.get("year"):
            t["year"] = album.get("year")
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
        # get_playlist() gives album as {"name": ...}; get_album()'s own
        # per-track entries (surfaced here via _album_as_playlist) give it
        # as a plain string instead — this used to only handle the dict
        # shape, so every track opened from an album showed no album at all.
        album = t.get("album")
        if isinstance(album, dict):
            album_name = album.get("name")
        elif isinstance(album, str):
            album_name = album
        else:
            album_name = None
        tracks.append(
            {
                "videoId": t["videoId"],
                "title": t.get("title"),
                "artists": artists,
                "album": album_name,
                "year": t.get("year"),
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
              "český {q} underground", "moravské {q} kapely",
              "české {q} skupiny", "{q} kapely z čech", "slovenské {q} skupiny",
              "česká {q} alba", "český {q} 90. léta", "český {q} 80. léta",
              "{q} česky", "slovak {q} band", "česká {q} klasika", "nové české {q} kapely"]

# ------------------------------------------------- Bandzone (Czech bands)
#
# YouTube Music knows the big Czech names; Bandzone.cz knows the scene — tens
# of thousands of Czech and Slovak bands, by genre, sorted by fans. Its genre
# filter takes one numeric id per request (?g=31), the order is a signal
# (orderType=fan_count&do=orderType) and pages go by &p=N. A merged genre
# (Rock & Metal) takes turns over its ids, most-followed first in each.
_BZ_GENRES = {
    "rock & metal": [40, 31, 53, 27, 50, 15, 7, 32, 33, 38, 54, 36, 88, 93],
    "rock": [40, 53, 84, 3, 54, 36], "metal": [31, 27, 50, 15, 7, 32, 33, 14, 88, 93, 95],
    "hard rock": [53], "punk": [38, 96], "punk rock": [38, 96], "pop": [34], "indie": [29], "indie rock": [29],
    "alternative": [3, 60], "alternative rock": [3], "hip hop": [28, 42], "rap": [42, 28], "folk": [21],
    "country": [9, 5], "blues": [6], "jazz": [30, 97], "electronic": [17, 64, 65, 66, 16],
    "techno": [64], "house": [65], "drum and bass": [66], "dubstep": [73], "ambient": [4], "trance": [67],
    "reggae": [43, 47], "ska": [47], "soul": [49], "classical": [12], "acoustic": [2], "emo": [19],
    "grunge": [23], "progressive rock": [36], "stoner rock": [54], "post-rock": [83], "hardcore punk": [26],
    "singer-songwriter": [2, 21], "world music": [52], "funk": [22], "shoegaze": [82],
}
# The ten Czech genres the app offers: each one searches YouTube Music under
# every one of its styles and draws on the matching Bandzone genres.
_CZ_GENRES = {
    "rock & metal": (["rock", "metal", "hard rock", "heavy metal", "punk rock", "thrash metal", "rock'n'roll",
                      "alternative rock", "death metal", "metalcore"],
                     [40, 31, 53, 27, 38, 50, 15, 3, 7, 32, 33, 54, 36, 23, 45, 57, 96, 14, 25, 39, 26]),
    "pop": (["pop", "popové", "dance pop"], [34]),
    "hip hop & rap": (["hip hop", "rap", "trap"], [28, 42]),
    "folk & country": (["folk", "country", "trampské", "bluegrass"], [21, 9, 5]),
    "elektronika": (["elektronická", "techno", "house", "drum and bass"], [17, 64, 65, 66, 73, 67]),
    "jazz & blues": (["jazz", "blues", "swing"], [30, 6, 97]),
    "písničkáři": (["písničkáři", "singer-songwriter", "šanson", "akustické"], [2, 94]),
    "alternativa & indie": (["indie", "alternativní", "indie rock", "post-rock"], [29, 60, 83, 82]),
    "reggae & ska": (["reggae", "ska", "dub"], [43, 47]),
    "soul & funk": (["soul", "funk", "r&b"], [49, 22]),
}
_BZ_CACHE: dict = {}          # (gid, page) -> {"ts", "bands"}
_BZ_TTL = 6 * 3600
_BZ_LOCK = threading.Lock()
_BZ_PAT = _re_mod.compile(
    r'<div class="profileLink band[^"]*"><a href="(/[^"]+)" title="[^"]*"><img src="([^"]+)"[^>]*/>'
    r'<div class="description cutter"><h4 class="title cutter">([^<]+)</h4><span class="genre cutter">([^<]*)</span>')


def _bz_page(gid: int, page: int) -> list:
    """One page (20 bands) of a Bandzone genre, most fans first."""
    key = (gid, page)
    hit = _BZ_CACHE.get(key)
    if hit and time.time() - hit["ts"] < _BZ_TTL:
        return hit["bands"]
    import http.cookiejar
    import html as _html
    import urllib.request as _ur
    bands = []
    try:
        with _BZ_LOCK:
            op = _ur.build_opener(_ur.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            op.addheaders = [("User-Agent", _CHART_UA)]
            url = f"https://bandzone.cz/kapely.html?g={int(gid)}&orderType=fan_count&do=orderType&p={max(1, int(page))}"
            body = op.open(url, timeout=12).read().decode("utf-8", "ignore")
        for href, img, name, genre in _BZ_PAT.findall(body):
            bands.append({"name": _html.unescape(name).strip(), "image": img,
                          "bandzone": "https://bandzone.cz" + href, "genre": _html.unescape(genre), "source": "bandzone"})
    except Exception:                                          # noqa: BLE001 — a shelf without Bandzone is fine
        bands = []
    _BZ_CACHE[key] = {"ts": time.time(), "bands": bands}
    if len(_BZ_CACHE) > 600:
        _BZ_CACHE.clear()
    return bands


def _name_key(name: str) -> str:
    """"Kabát" and "KABAT" are one band."""
    import unicodedata
    n = unicodedata.normalize("NFKD", (name or "").lower())
    return _re_mod.sub(r"\W+", "", "".join(c for c in n if not unicodedata.combining(c)))


def _bz_ids_for(q: str) -> list:
    k = q.strip().lower()
    if k in _CZ_GENRES:
        return _CZ_GENRES[k][1]
    return _BZ_GENRES.get(k) or _BZ_GENRES.get(k.replace("&", "and")) or []


_CZ_LINKS: dict = {}


@app.get("/api/artist/cz-links")
def artist_cz_links(name: str):
    """Where else this artist is: a Bandzone profile (Czech/Slovak bands) and
    Supermusic (chords and lyrics). Only what is actually found."""
    key = name.strip().lower()
    if not key:
        return {}
    if key in _CZ_LINKS:
        return _CZ_LINKS[key]
    import html as _html
    import urllib.parse as _up
    import urllib.request as _ur
    want = _name_key(key)
    out = {}

    def fetch(url):
        req = _ur.Request(url, headers={"User-Agent": _CHART_UA})
        return _ur.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
    try:
        body = fetch("https://bandzone.cz/kapely.html?q=" + _up.quote(name.strip()))
        for href, img, nm, genre in _BZ_PAT.findall(body):
            if _name_key(_html.unescape(nm)) == want:
                out["bandzone"] = "https://bandzone.cz" + href
                out["bandzone_genre"] = _html.unescape(genre)
                break
    except Exception:                                          # noqa: BLE001
        pass
    try:
        body = fetch("https://supermusic.cz/najdi.php?typhladania=skupina&hladane=" + _up.quote(name.strip()))
        for sid, nm in _re_mod.findall(r'skupina\.php\?idskupiny=(\d+)[^>]*>([^<]+)', body):
            n = _html.unescape(nm).strip()
            # "Renčín, Vladimír" → "Vladimír Renčín" as well
            alt = " ".join(reversed([x.strip() for x in n.split(",", 1)])) if "," in n else n
            if want in (_name_key(n), _name_key(alt)):
                out["supermusic"] = f"https://supermusic.cz/skupina.php?idskupiny={sid}"
                break
    except Exception:                                          # noqa: BLE001
        pass
    _CZ_LINKS[key] = out
    return out


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
    # A merged Czech genre asks under each of its styles in turn.
    terms = _CZ_GENRES[q.lower()][0] if region == "cz" and q.lower() in _CZ_GENRES else [q]
    variants = [v.replace("{q}", t) for v in variants for t in terms]
    need = offset + limit
    if cap:
        need = min(need, cap)
    # Czech genres also draw on Bandzone, a page of each genre id in turn;
    # the shelf takes turns between the two sources so both show up early.
    bz_ids = _bz_ids_for(q) if region == "cz" else []
    for k, v in (("bz_round", 0), ("bz_i", 0), ("names", set()), ("pool_bz", []), ("pool_yt", []), ("turn", 0)):
        st.setdefault(k, v)
    bz_done = lambda: not bz_ids or st["bz_round"] >= 15

    def fill_bz():
        # Two genres at a time, interleaved — rock, metal, rock, metal… —
        # so one genre's page does not fill the shelf on its own.
        while not st["pool_bz"] and not bz_done():
            got = []
            for _ in range(min(2, len(bz_ids))):
                gid = bz_ids[st["bz_i"] % len(bz_ids)]
                page = st["bz_round"] + 1
                st["bz_i"] += 1
                if st["bz_i"] % len(bz_ids) == 0:
                    st["bz_round"] += 1
                got.append(_bz_page(gid, page))
            for i in range(max(map(len, got))):
                st["pool_bz"].extend(g[i] for g in got if i < len(g))

    def fill_yt():
        while not st["pool_yt"] and st["variant"] < len(variants):
            phrase = variants[st["variant"]]
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
                st["pool_yt"].append({"name": a.get("artist"), "browseId": bid,
                                      "image": _best_thumb(a.get("thumbnails"))})

    # Keep asking until the page is full or both sources run out.
    while len(st["items"]) < need:
        fill_yt()
        fill_bz()
        if not st["pool_yt"] and not st["pool_bz"]:
            break
        first, second = ("pool_bz", "pool_yt") if st["turn"] % 2 else ("pool_yt", "pool_bz")
        pool = st[first] or st[second]
        st["turn"] += 1
        # A few at a time from each, so neither source crowds out the other.
        for _ in range(3):
            if not pool:
                break
            item = pool.pop(0)
            nk = _name_key(item["name"])
            if nk and nk not in st["names"]:
                st["names"].add(nk)
                st["items"].append(item)

    items = st["items"][:cap] if cap else st["items"]
    page = items[offset: offset + limit]
    more = offset + limit < len(items) or (
        (st["variant"] < len(variants) or not bz_done() or st["pool_yt"] or st["pool_bz"])
        and not (cap and len(items) >= cap))
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


@app.get("/api/tabs/match")
def tab_match(title: str, artist: str = ""):
    """The Songsterr tab of one song, when artist and title both agree —
    what a song opened in Steems loads by itself. {} when there is none."""
    if not artist and " - " in title:
        artist, title = [x.strip() for x in title.split(" - ", 1)]
    try:
        rec = _match_song(artist, title)
    except Exception:                                          # noqa: BLE001
        rec = None
    if not rec:
        return {}
    return {"songId": rec["songId"], "artist": rec.get("artist"), "title": rec.get("title"), "trackIndex": _guitar_index(rec)}


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



_VIDEO_POINTS_CACHE: dict = {}


@app.get("/api/tabs/video-points/{song_id}/{revision_id}")
async def tab_video_points(song_id: str, revision_id: str):
    """Songsterr's own bar timings: for each YouTube recording it has synced
    a tab to, the time (s) at which every bar starts in that recording. With
    these a tab follows the real performance — tempo changes, rubato and
    all — instead of the tab's nominal tempo. Same data the Songsterr web
    player syncs with.
    """
    key = f"{song_id}/{revision_id}"
    hit = _VIDEO_POINTS_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                f"https://www.songsterr.com/api/video-points/{song_id}/{revision_id}/list",
                headers={"User-Agent": APP_UA},
            )
            r.raise_for_status()
            raw = r.json()
    except Exception as e:
        return JSONResponse({"error": f"Video points fetch failed: {e}"}, status_code=500)
    out = []
    for v in raw if isinstance(raw, list) else []:
        pts = v.get("points")
        if not isinstance(pts, list) or len(pts) < 3 or v.get("status") != "done" or not v.get("videoId"):
            continue
        if v.get("problematic"):
            continue
        try:
            pts = [float(x) for x in pts]
        except (TypeError, ValueError):
            continue
        out.append({"videoId": v["videoId"], "feature": v.get("feature"), "points": pts})
    result = {"videos": out}
    _VIDEO_POINTS_CACHE[key] = (time.time() + 3600, result)
    return result


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
    """Cover art for a search term, from the first playlist/album/video that
    matches it. "videos" is last and only a fallback: for a real music term
    it is usually a worse cover than an album's own art, but for something
    that is not music at all — "Marshall JCM800", a NAM capture's own
    gear name — a demo/review video's thumbnail is often the only source
    that actually shows the thing, since no playlist or album is named
    after an amplifier.
    """
    for filt in ("playlists", "albums", "videos"):
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
APP_UA = "Pick/1.0 (local music app)"

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


# ---------------------------------------------- folders mapped into the library
#
# A folder anywhere on the Mac (or in iCloud / Google Drive / OneDrive) shown
# in the library without copying anything: it appears at the library's root
# as "@Name", and every library path under it is "@Name/sub/file". Each one
# is its own root — nothing resolved through it may leave it.
MOUNT_PREFIX = "@"


def _library_mounts() -> list:
    out = []
    for m in _read_config().get("library_mounts") or []:
        if isinstance(m, dict) and m.get("name") and m.get("path"):
            out.append({"name": str(m["name"]), "path": str(m["path"])})
    return out


def _mount_named(name: str):
    return next((m for m in _library_mounts() if m["name"] == name), None)


def _is_mount_root(rel: str) -> bool:
    return bool(rel) and rel.startswith(MOUNT_PREFIX) and "/" not in rel.strip("/")


def _rel_of(path: Path) -> Optional[str]:
    """The library path of a real path: under MUSIC_ROOT, or "@Name/…"."""
    p = path.resolve()
    root = MUSIC_ROOT.resolve()
    if p == root or root in p.parents:
        return p.relative_to(root).as_posix() if p != root else ""
    for m in _library_mounts():
        base = Path(m["path"]).resolve()
        if p == base or base in p.parents:
            sub = p.relative_to(base).as_posix() if p != base else ""
            return MOUNT_PREFIX + m["name"] + ("/" + sub if sub else "")
    return None


def _resolve_under_music_root(rel: str) -> Optional[Path]:
    """Resolves `rel` under MUSIC_ROOT, refusing anything that would escape
    it — a leading '/' (pathlib silently drops the left side when you `/`
    it with an absolute path, so this has to be checked explicitly), '..'
    segments, or a symlink pointing outside. Returns None on any violation.
    "@Name/…" resolves the same way under that mapped folder instead.
    """
    if not rel:
        return MUSIC_ROOT.resolve()
    if Path(rel).is_absolute():
        return None
    if rel.startswith(MOUNT_PREFIX):
        name, _, sub = rel[len(MOUNT_PREFIX):].partition("/")
        m = _mount_named(name)
        if m is None:
            return None
        base = Path(m["path"]).resolve()
        try:
            candidate = (base / sub).resolve() if sub else base
        except (ValueError, OSError):
            return None
        if candidate != base and base not in candidate.parents:
            return None
        return candidate
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


def _track_no(value) -> Optional[int]:
    try:
        return int(str(value).split("/")[0].strip())
    except (TypeError, ValueError):
        return None


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
                # "3" or "3/12" — only the position is wanted, for sorting
                # a list by album the way the album plays.
                "track": _track_no(first("tracknumber")),
                "duration": round(audio.info.length, 1) if getattr(audio, "info", None) else None,
            }
    except Exception:
        tags = {}

    if len(_TAG_CACHE) > 4000:
        _TAG_CACHE.clear()
    _TAG_CACHE[key] = {"sig": (st.st_mtime, st.st_size), "tags": tags}
    return tags


# Same (mtime, size)-keyed cache pattern as _audio_tags, for the gear info a
# .nam file already carries in its own "metadata" object — TONE3000 exports
# (and most other NAM trainers) write gear_make/gear_model/tone_type/
# modeled_by there, so the library can show what amp a capture actually is
# instead of just its (often cryptic) filename.
_NAM_META_CACHE: dict = {}


def _nam_meta(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    cached = _NAM_META_CACHE.get(key)
    if cached and cached["sig"] == (st.st_mtime, st.st_size):
        return cached["meta"]

    meta = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        md = raw.get("metadata") or {}
        meta = {
            "gear_make": md.get("gear_make"),
            "gear_model": md.get("gear_model"),
            "tone_type": md.get("tone_type"),
            "modeled_by": md.get("modeled_by"),
        }
    except Exception:
        meta = {}

    if len(_NAM_META_CACHE) > 4000:
        _NAM_META_CACHE.clear()
    _NAM_META_CACHE[key] = {"sig": (st.st_mtime, st.st_size), "meta": meta}
    return meta


def _midi_count(folder: Path) -> int:
    try:
        return sum(1 for c in folder.iterdir()
                   if c.suffix.lower() in MIDI_EXTS and not c.name.startswith(".") and c.is_file())
    except OSError:
        return 0


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


def _all_songs_flat() -> dict:
    """Every song of Songs/ and Playlists/, in one list — "Songs" as the one
    place to look. A song in both (or in two playlists) shows once, the
    Songs/ copy first."""
    files, seen = [], set()
    for top in ("Songs", "Playlists"):
        base = MUSIC_ROOT / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if fn.startswith(".") or p.suffix.lower() not in AUDIO_EXTS:
                    continue
                tags = _audio_tags(p)
                guess = _guess_from_name(p.stem) if top == "Playlists" else {"artist": None, "title": p.stem}
                artist = tags.get("artist") or guess.get("artist") or (p.parent.name if top == "Songs" and p.parent != base else "")
                title = tags.get("title") or guess.get("title") or p.stem
                key = (_re_mod.sub(r"\W+", "", (artist or "").lower()), _re_mod.sub(r"\W+", "", title.lower()))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                files.append({"name": p.name, "type": "file", "rel": _rel_of(p), "size_bytes": size,
                              **tags, "stems": _stems_state_for(p),
                              # where it lives, for the list ("Songs" or the playlist)
                              "folder": top if p.parent == base or top == "Songs" else p.relative_to(base).parts[0]})
    return {"rel": "Songs", "breadcrumb": [{"name": (MUSIC_ROOT.resolve().name or "Knihovna"), "rel": ""},
                                            {"name": "Songs", "rel": "Songs"}],
            "entries": files, "flat": True,
            "summary": {"folders": 0, "files": len(files), "total_tracks": len(files),
                        "total_size_bytes": sum(f["size_bytes"] for f in files)}}


@app.get("/api/library/browse")
def library_browse(rel: str = "", flat: bool = False):
    if flat and rel.strip("/") == "Songs":
        return _all_songs_flat()
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_dir():
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)

    root = MUSIC_ROOT.resolve()
    here = _rel_of(target) or ""
    parts = [x for x in here.split("/") if x]
    breadcrumb = [{"name": root.name or str(root), "rel": ""}]
    acc = []
    for part in parts:
        acc.append(part)
        breadcrumb.append({"name": part.lstrip(MOUNT_PREFIX) if len(acc) == 1 and part.startswith(MOUNT_PREFIX) else part,
                           "rel": "/".join(acc)})

    dirs, files = [], []
    if not here:
        # Mapped folders first. Their contents aren't counted here: a cloud
        # folder can be large and slow to walk.
        for m in _library_mounts():
            ok = Path(m["path"]).is_dir()
            dirs.append({"name": m["name"], "type": "dir", "rel": MOUNT_PREFIX + m["name"],
                         "mount": True, "path": m["path"], "missing": not ok,
                         "track_count": 0, "folder_count": 0, "size_bytes": 0, "uncounted": True})
    try:
        listing = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        listing = []
    for p in listing:
        entry_rel = "/".join((*parts, p.name))
        if p.name.startswith("."):
            continue
        if p.is_dir():
            tracks, size, folders = _folder_stats(p)
            dirs.append({
                "name": p.name, "type": "dir", "rel": entry_rel,
                # Counts and size let the browser show the shape of a folder
                # without making you open it.
                "track_count": tracks, "folder_count": folders, "size_bytes": size,
                # MIDI files right inside it — the rail's tree shows those as
                # draggable leaves, so such a folder must be unfoldable.
                "midi_count": _midi_count(p),
            })
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append({
                "name": p.name, "type": "file", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
                **_audio_tags(p),
                **(_riff_tags(entry_rel) if entry_rel.startswith(RIFFS_REL + "/") else {}),
                "stems": _stems_state_for(p),
            })
        # A NAM capture isn't playable audio (it's model weights, not a
        # waveform) so it never matched AUDIO_EXTS and used to vanish from
        # this listing entirely — a folder full of presets looked empty.
        elif p.is_file() and p.suffix.lower() == ".nam":
            files.append({
                "name": p.name, "type": "nam", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
                **_nam_meta(p),
                "riff_id": _nam_riff_assignments().get(entry_rel),
            })
        elif p.is_file() and p.suffix.lower() in MIDI_EXTS:
            files.append({"name": p.name, "type": "midi", "rel": entry_rel,
                          "size_bytes": p.stat().st_size})
        # Anything else (tabs, PDFs, pictures, video…) is listed too, so a
        # folder shows what is really in it; it opens in its own Mac app.
        elif p.is_file() and not p.name.startswith("."):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append({"name": p.name, "type": "other", "rel": entry_rel,
                          "size_bytes": size, "ext": p.suffix.lower().lstrip(".")})

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
    if _needs_transcode(target):
        copy = _playable_copy(target)
        if copy is None:
            return JSONResponse({"error": "Soubor se nepodařilo převést."}, status_code=500)
        target = copy
    return _range_file_response(target, request.headers.get("range"))


@app.get("/api/library/midi")
def library_midi(rel: str):
    """A MIDI file's bytes — the browser parses it and puts the notes on a
    MIDI track. Only .mid/.midi."""
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_file() or target.suffix.lower() not in MIDI_EXTS:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    from fastapi.responses import Response
    return Response(target.read_bytes(), media_type="audio/midi")


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


class LibraryMountRequest(BaseModel):
    path: str = ""      # empty: ask with the system's folder picker
    name: str = ""


@app.get("/api/library/mounts")
def library_mounts():
    return {"mounts": [{**m, "rel": MOUNT_PREFIX + m["name"], "exists": Path(m["path"]).is_dir()}
                       for m in _library_mounts()]}


@app.post("/api/library/mounts")
def library_add_mount(req: LibraryMountRequest):
    """Maps a folder into the library — nothing is copied."""
    path = req.path
    if not path:
        try:
            r = subprocess.run(["osascript", "-e",
                                'POSIX path of (choose folder with prompt "Složka, kterou chceš vidět v knihovně (nic se nekopíruje)")'],
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {**library_mounts(), "added": None}
        if r.returncode != 0:
            return {**library_mounts(), "added": None}
        path = r.stdout.strip()
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        return JSONResponse({"error": "Složka neexistuje."}, status_code=400)
    p = p.resolve()
    root = MUSIC_ROOT.resolve()
    if p == root or root in p.parents:
        return JSONResponse({"error": "Tahle složka už je v knihovně."}, status_code=400)
    mounts = _library_mounts()
    if any(Path(m["path"]).resolve() == p for m in mounts):
        return JSONResponse({"error": "Tahle složka už je namapovaná."}, status_code=400)
    base = "".join(c for c in (req.name or p.name or "Složka") if c not in '/\\:@').strip()[:60] or "Složka"
    name, n = base, 2
    while any(m["name"] == name for m in mounts):
        name = f"{base} ({n})"
        n += 1
    mounts.append({"name": name, "path": str(p)})
    _write_config({"library_mounts": mounts})
    return {**library_mounts(), "added": {"name": name, "rel": MOUNT_PREFIX + name, "path": str(p)}}


@app.post("/api/library/mounts/remove")
def library_remove_mount(req: LibraryMountRequest):
    """Unmaps a folder. Its files stay where they are."""
    _write_config({"library_mounts": [m for m in _library_mounts() if m["name"] != req.name]})
    return library_mounts()


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
    return {"ok": True, "rel": _rel_of(target) or ""}


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
    return {"ok": True, "rel": _rel_of(target) or ""}


class LibraryMoveRequest(BaseModel):
    rel: str            # source, relative to MUSIC_ROOT
    dest_rel: str = ""  # destination folder, relative to MUSIC_ROOT


@app.post("/api/library/fs/move")
def library_fs_move(req: LibraryMoveRequest):
    if _is_mount_root(req.rel):
        return JSONResponse({"error": "Namapovanou složku nejde přesunout."}, status_code=400)
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
    old_rel = _rel_of(source) or ""
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        return JSONResponse({"error": f"Přesun selhal: {exc}"}, status_code=500)
    new_rel = _rel_of(target) or ""
    _rekey_nam_riff_assignments(old_rel, new_rel)
    return {"ok": True, "rel": new_rel}


class LibraryDeleteRequest(BaseModel):
    rel: str


@app.post("/api/library/fs/delete")
def library_fs_delete(req: LibraryDeleteRequest):
    """Moves to Trash via Finder rather than unlinking outright — a
    personal media library is exactly the kind of thing a misclick
    shouldn't be able to permanently destroy."""
    if _is_mount_root(req.rel):
        return JSONResponse({"error": "Namapovanou složku nejde smazat — jen odpojit (soubory zůstanou)."}, status_code=400)
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
    _rekey_nam_riff_assignments(req.rel, None)
    return {"ok": True}


def _rekey_nam_riff_assignments(old_rel: str, new_rel: Optional[str]):
    """Carries a NAM riff assignment forward through a move done via this
    app's own endpoints (new_rel given), or drops it on delete (new_rel
    None) — covers both a single .nam file and a folder of them, since
    old_rel might be either.
    """
    assignments = _nam_riff_assignments()
    changed = False
    for key in list(assignments.keys()):
        if key == old_rel:
            new_key = new_rel
        elif key.startswith(old_rel + "/"):
            new_key = (new_rel + key[len(old_rel):]) if new_rel else None
        else:
            continue
        value = assignments.pop(key)
        if new_key:
            assignments[new_key] = value
        changed = True
    if changed:
        _save_nam_riff_assignments()


# ------------------------------------------------------- NAM preset preview
#
# Same idea as TONE3000's web player (https://www.tone3000.com/blog/
# introducing-the-nam-web-player): a short dry guitar recording, played
# through whichever NAM capture is clicked, entirely client-side — the NAM
# WASM engine already used for the live guitar channel (vendored from
# tone-3000/neural-amp-modeler-wasm, the same package this feature is named
# after — see static/nam/README.md) does the actual amp-modeling work. The
# riff has to be a *real* dry (pre-amp) recording, so it's captured from the
# user's own guitar rather than faked; a stem or anything already-amped
# would just double up amp coloration.
#
# Started as a single riff with no way to replace or remove it; now a small
# library of them (riffs.json + riffs/<id>.webm), one marked active — the
# one preset-preview play buttons actually use — with the others kept
# around to switch between or delete.
NAM_PREVIEW_DIR = HOME / ".local" / "share" / "spotdl-helper"
NAM_RIFFS_DIR = NAM_PREVIEW_DIR / "riffs"
NAM_RIFFS_INDEX = NAM_PREVIEW_DIR / "riffs.json"
_LEGACY_NAM_RIFF = NAM_PREVIEW_DIR / "nam_preview_di.webm"
_re_riff_id = _re_mod.compile(r"[0-9a-f]{8,32}")


def _riff_file(riff_id: str) -> Optional[Path]:
    """The stored file of a riff. Recordings are .webm, but a DI riff copied
    in from disk keeps its own extension (wav, mp3, flac…)."""
    if not _re_riff_id.fullmatch(riff_id or ""):
        return None
    if NAM_RIFFS_DIR.is_dir():
        for p in sorted(NAM_RIFFS_DIR.glob(f"{riff_id}.*")):
            if p.is_file():
                return p
    return None


def _load_riffs_index() -> dict:
    try:
        data = json.loads(NAM_RIFFS_INDEX.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data.setdefault("riffs", [])
    data.setdefault("active_id", None)
    # One-time migration: a riff recorded before this became a list lived as
    # a single fixed-name file. Bring it in as riff 1 rather than orphaning
    # (or silently discarding) whatever was already recorded there.
    if not data["riffs"] and _LEGACY_NAM_RIFF.is_file():
        riff_id = uuid.uuid4().hex[:12]
        try:
            NAM_RIFFS_DIR.mkdir(parents=True, exist_ok=True)
            _LEGACY_NAM_RIFF.rename(NAM_RIFFS_DIR / f"{riff_id}.webm")
        except OSError:
            pass
        else:
            data["riffs"] = [{"id": riff_id, "name": "Riff 1", "created_at": int(time.time())}]
            data["active_id"] = riff_id
            _save_riffs_index(data)
    return data


def _save_riffs_index(data: dict):
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    NAM_RIFFS_INDEX.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# Which riff (by id, into the same global library above) a specific .nam
# file should preview with instead of the globally active one — keyed by
# library-relative path. A path key breaks if a file is moved from outside
# this app (Finder, another tool); library_fs_move carries the entry
# forward when the move happens through the app's own endpoint, which
# covers the common case without needing a content hash for every file.
NAM_RIFF_ASSIGN_PATH = NAM_PREVIEW_DIR / "nam_riff_assignments.json"
_nam_riff_assign_cache: Optional[dict] = None


def _nam_riff_assignments() -> dict:
    global _nam_riff_assign_cache
    if _nam_riff_assign_cache is None:
        try:
            _nam_riff_assign_cache = json.loads(NAM_RIFF_ASSIGN_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            _nam_riff_assign_cache = {}
    return _nam_riff_assign_cache


def _save_nam_riff_assignments():
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    NAM_RIFF_ASSIGN_PATH.write_text(
        json.dumps(_nam_riff_assignments(), ensure_ascii=False), encoding="utf-8")


class NamRiffAssignRequest(BaseModel):
    rel: str
    riff_id: Optional[str] = None  # None/omitted clears the assignment


@app.post("/api/nam-preview/assign")
def assign_nam_riff(req: NamRiffAssignRequest):
    target = _resolve_under_music_root(req.rel)
    if target is None or not target.is_file() or target.suffix.lower() != ".nam":
        return JSONResponse({"error": "Neplatný soubor."}, status_code=404)
    if req.riff_id:
        data = _load_riffs_index()
        if not any(r["id"] == req.riff_id for r in data["riffs"]):
            return JSONResponse({"error": "Neplatný riff."}, status_code=400)
    assignments = _nam_riff_assignments()
    if req.riff_id:
        assignments[req.rel] = req.riff_id
    else:
        assignments.pop(req.rel, None)
    _save_nam_riff_assignments()
    return {"ok": True}


@app.get("/api/nam-preview/riffs")
def list_nam_riffs():
    data = _load_riffs_index()
    out = []
    for r in data["riffs"]:
        path = _riff_file(r["id"])
        if path is None:
            continue
        out.append({**r, "bytes": path.stat().st_size})
    return {"riffs": out, "active_id": data.get("active_id")}


@app.post("/api/nam-preview/riffs")
async def add_nam_riff(request: Request, name: str = ""):
    raw = await request.body()
    if len(raw) < 1024:
        return JSONResponse({"error": "Nahrávka je prázdná."}, status_code=400)
    if len(raw) > 60 * 1024 * 1024:
        return JSONResponse({"error": "Nahrávka je příliš velká."}, status_code=413)
    data = _load_riffs_index()
    NAM_RIFFS_DIR.mkdir(parents=True, exist_ok=True)
    riff_id = uuid.uuid4().hex[:12]
    # Recorded in the browser it is WebM; a clip cut in the mixer comes as WAV.
    ext = ".wav" if raw[:4] == b"RIFF" else ".webm"
    try:
        (NAM_RIFFS_DIR / f"{riff_id}{ext}").write_bytes(raw)
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    entry = {"id": riff_id, "name": (name or "").strip() or f"Riff {len(data['riffs']) + 1}",
             "created_at": int(time.time())}
    data["riffs"].append(entry)
    if not data.get("active_id"):
        data["active_id"] = riff_id
    _save_riffs_index(data)
    return {"ok": True, "riff": {**entry, "bytes": len(raw)}, "active_id": data["active_id"]}


@app.get("/api/nam-preview/riffs/{riff_id}")
def get_nam_riff(riff_id: str):
    if not _re_riff_id.fullmatch(riff_id):
        return JSONResponse({"error": "Neplatné id."}, status_code=400)
    path = _riff_file(riff_id)
    if path is None:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    from fastapi.responses import Response
    return Response(path.read_bytes(),
                    media_type=mimetypes.guess_type(str(path))[0] or "audio/webm")


@app.delete("/api/nam-preview/riffs/{riff_id}")
def delete_nam_riff(riff_id: str):
    if not _re_riff_id.fullmatch(riff_id):
        return JSONResponse({"error": "Neplatné id."}, status_code=400)
    path = _riff_file(riff_id) or (NAM_RIFFS_DIR / f"{riff_id}.webm")
    # A real recording of the user's own playing, and a straight unlink()
    # here already once cost a real one during this feature's own testing —
    # Trash it instead, same as library_fs_delete, so that mistake (by
    # anyone, agent or human) is recoverable instead of just gone.
    if path.is_file():
        script = f'tell application "Finder" to delete POSIX file "{_applescript_quote(str(path))}"'
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        path.unlink(missing_ok=True)  # in case Finder couldn't (e.g. no Finder.app in this context)
    data = _load_riffs_index()
    data["riffs"] = [r for r in data["riffs"] if r["id"] != riff_id]
    if data.get("active_id") == riff_id:
        data["active_id"] = data["riffs"][0]["id"] if data["riffs"] else None
    _save_riffs_index(data)
    assignments = _nam_riff_assignments()
    stale = [k for k, v in assignments.items() if v == riff_id]
    if stale:
        for k in stale:
            del assignments[k]
        _save_nam_riff_assignments()
    return {"ok": True, "active_id": data.get("active_id")}


class NamRiffActiveRequest(BaseModel):
    riff_id: str


@app.post("/api/nam-preview/riffs/active")
def set_active_nam_riff(req: NamRiffActiveRequest):
    data = _load_riffs_index()
    if not any(r["id"] == req.riff_id for r in data["riffs"]):
        return JSONResponse({"error": "Neplatný riff."}, status_code=400)
    data["active_id"] = req.riff_id
    _save_riffs_index(data)
    return {"ok": True, "active_id": req.riff_id}


# ------------------------------------------------------- DI riffs from disk
#
# Folders of dry guitar recordings that live anywhere on the disk can be
# "mapped": they are browsed in place (never moved or changed) and any file in
# them can be copied into the sample riffs, to hear a NAM profile on it.
DI_EXTS = AUDIO_EXTS
# Chrome can't decode these, so they are converted to wav when copied in.
DI_TRANSCODE_EXTS = TRANSCODE_AUDIO_EXTS
DI_MAX_BYTES = 60 * 1024 * 1024


def _di_dirs() -> list:
    out = []
    for raw in _read_config().get("di_dirs") or []:
        if isinstance(raw, str) and raw:
            out.append(raw)
    return out


def _resolve_under_di_dirs(path: str) -> Optional[Path]:
    """`path` resolved, only if it is (inside) one of the mapped folders."""
    if not path or not Path(path).is_absolute():
        return None
    try:
        candidate = Path(path).resolve()
    except (ValueError, OSError):
        return None
    for d in _di_dirs():
        try:
            root = Path(d).resolve()
        except (ValueError, OSError):
            continue
        if candidate == root or root in candidate.parents:
            return candidate
    return None


def _di_dirs_payload() -> dict:
    return {"dirs": [{"path": d, "name": Path(d).name or d, "exists": Path(d).is_dir()}
                     for d in _di_dirs()]}


class DiDirRequest(BaseModel):
    path: Optional[str] = None  # omitted: ask with the native folder picker


@app.get("/api/di/dirs")
def di_dirs_list():
    return _di_dirs_payload()


@app.post("/api/di/dirs")
def di_dirs_add(req: DiDirRequest):
    path = req.path
    if not path:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "Složka s DI riffy")'],
                capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {**_di_dirs_payload(), "added": None}
        if result.returncode != 0:
            return {**_di_dirs_payload(), "added": None}  # dialog cancelled
        path = result.stdout.strip()
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        return JSONResponse({"error": "Složka neexistuje."}, status_code=400)
    p = p.resolve()
    dirs = _di_dirs()
    if str(p) not in dirs:
        dirs.append(str(p))
        _write_config({"di_dirs": dirs})
    return {**_di_dirs_payload(), "added": str(p)}


@app.post("/api/di/dirs/remove")
def di_dirs_remove(req: DiDirRequest):
    # Only the mapping goes; the folder and its files are not touched.
    dirs = [d for d in _di_dirs() if d != req.path]
    _write_config({"di_dirs": dirs})
    return _di_dirs_payload()


@app.get("/api/di/browse")
def di_browse(path: str):
    target = _resolve_under_di_dirs(path)
    if target is None or not target.is_dir():
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)
    roots = {str(Path(d).resolve()) for d in _di_dirs() if Path(d).exists()}
    entries = []
    try:
        children = sorted(target.iterdir(), key=lambda q: (not q.is_dir(), q.name.lower()))
    except OSError:
        children = []
    for c in children:
        if c.name.startswith("."):
            continue
        if c.is_dir():
            entries.append({"type": "dir", "name": c.name, "path": str(c)})
        elif c.is_file() and c.suffix.lower() in DI_EXTS:
            try:
                size = c.stat().st_size
            except OSError:
                continue
            entries.append({"type": "file", "name": c.name, "path": str(c), "size_bytes": size,
                            "playable": True})
    at_root = str(target) in roots
    return {"path": str(target), "name": target.name or str(target),
            "parent": None if at_root else str(target.parent), "entries": entries}


@app.get("/api/di/audio")
def di_audio(path: str, request: Request):
    target = _resolve_under_di_dirs(path)
    if target is None or not target.is_file() or target.suffix.lower() not in DI_EXTS:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    if _needs_transcode(target):
        target = _playable_copy(target)
        if target is None:
            return JSONResponse({"error": "Soubor se nepodařilo převést."}, status_code=500)
    return _range_file_response(target, request.headers.get("range"))


class RiffFromFileRequest(BaseModel):
    path: Optional[str] = None   # absolute, inside a mapped DI folder
    rel: Optional[str] = None    # or a library-relative audio file
    name: Optional[str] = None
    activate: bool = False


@app.post("/api/nam-preview/riffs/from-file")
def add_nam_riff_from_file(req: RiffFromFileRequest):
    """Copies an existing audio file into the sample riffs. The original is
    never moved or modified; copying the same file twice returns the riff
    that already holds it."""
    if req.path:
        src = _resolve_under_di_dirs(req.path)
    elif req.rel:
        src = _resolve_under_music_root(req.rel)
    else:
        src = None
    if src is None or not src.is_file() or src.suffix.lower() not in DI_EXTS:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    st = src.stat()
    if st.st_size > DI_MAX_BYTES:
        return JSONResponse({"error": "Soubor je příliš velký (max 60 MB) — vyber kratší riff."},
                            status_code=413)
    source_key = f"{src}|{int(st.st_mtime)}|{st.st_size}"
    data = _load_riffs_index()
    existing = next((r for r in data["riffs"] if r.get("source") == source_key
                     and _riff_file(r["id"]) is not None), None)
    if existing:
        if req.activate:
            data["active_id"] = existing["id"]
            _save_riffs_index(data)
        return {"ok": True, "riff": existing, "active_id": data.get("active_id"), "existing": True}

    NAM_RIFFS_DIR.mkdir(parents=True, exist_ok=True)
    riff_id = uuid.uuid4().hex[:12]
    ext = src.suffix.lower()
    try:
        if _needs_transcode(src):
            dest = NAM_RIFFS_DIR / f"{riff_id}.wav"
            r = subprocess.run([ffmpeg_bin(), "-y", "-i", str(src), "-vn", str(dest)],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not dest.is_file():
                dest.unlink(missing_ok=True)
                return JSONResponse({"error": "Soubor se nepodařilo převést na wav."}, status_code=500)
        else:
            dest = NAM_RIFFS_DIR / f"{riff_id}{ext}"
            shutil.copyfile(src, dest)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return JSONResponse({"error": f"Kopírování selhalo: {exc}"}, status_code=500)
    entry = {"id": riff_id, "name": (req.name or "").strip() or src.stem,
             "created_at": int(time.time()), "source": source_key}
    data["riffs"].append(entry)
    if req.activate or not data.get("active_id"):
        data["active_id"] = riff_id
    _save_riffs_index(data)
    return {"ok": True, "riff": {**entry, "bytes": dest.stat().st_size},
            "active_id": data["active_id"], "existing": False}


# ------------------------------------------------------------------- loops
#
# Sites with free loops (Looperman first) can't be shown inside the app —
# they send X-Frame-Options/frame-ancestors, and proxying them would break
# their login. So each one opens in an app window of its own (Chrome --app,
# the user's own profile, so the login sticks), and whatever it downloads
# into ~/Downloads is moved into the library under Loops/<site>/.
#
# A site's downloads are recognised by file name (Looperman's all start with
# "looperman-"). A site added without a pattern claims audio files that
# land in Downloads within two hours of its window being opened.
LOOP_SOURCES_BUILTIN = [
    {"id": "looperman", "name": "Looperman", "url": "https://www.looperman.com/loops",
     "match": r"^looperman-", "builtin": True},
]
DOWNLOADS_DIR = HOME / "Downloads"
_loop_events: list = []            # recent imports, for the page's toasts
_loop_opened: dict = {}            # source id -> time its window was opened
_loop_watch_lock = threading.Lock()
_re_loop_id = _re_mod.compile(r"[a-z0-9_-]{1,40}")


def _loop_sources() -> list:
    custom = _read_config().get("loop_sources") or []
    return LOOP_SOURCES_BUILTIN + [dict(c, builtin=False) for c in custom if isinstance(c, dict) and c.get("id")]


def _loop_source(source_id: str) -> Optional[dict]:
    return next((s for s in _loop_sources() if s["id"] == source_id), None)


def _loop_folder(src: dict) -> Path:
    safe = _re_mod.sub(r"[^\w .-]", "_", src["name"]).strip() or src["id"]
    return MUSIC_ROOT / "Loops" / safe


def _loop_watch_since() -> float:
    cfg = _read_config()
    since = cfg.get("loops_watch_since")
    if not isinstance(since, (int, float)):
        since = time.time()
        _write_config({"loops_watch_since": since})
    return float(since)


def _loop_owner(name: str, mtime: float) -> Optional[dict]:
    """Which loop site a downloaded file belongs to, if any."""
    sources = _loop_sources()
    for src in sources:
        pat = src.get("match")
        if pat:
            try:
                if _re_mod.search(pat, name, _re_mod.IGNORECASE):
                    return src
            except _re_mod.error:
                continue
    # No pattern matched: the site without a pattern whose window was opened
    # most recently, if that was within two hours of this download.
    best = None
    for src in sources:
        opened = _loop_opened.get(src["id"])
        if src.get("match") or not opened or mtime < opened or mtime - opened > 7200:
            continue
        if best is None or opened > _loop_opened[best["id"]]:
            best = src
    return best


def _move_into_loops(path: Path, src: dict) -> Optional[Path]:
    folder = _loop_folder(src)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    n = 2
    while dest.exists():
        dest = folder / f"{path.stem} ({n}){path.suffix}"
        n += 1
    try:
        shutil.move(str(path), str(dest))
    except OSError:
        return None
    try:
        rel = dest.relative_to(MUSIC_ROOT).as_posix()
    except ValueError:
        rel = dest.name
    _loop_events.append({"t": time.time(), "source": src["id"], "name": dest.name, "rel": rel})
    del _loop_events[:-50]
    return dest


def _loop_candidates(since: Optional[float]) -> list:
    """(path, source) for audio in Downloads that belongs to a loop site."""
    out = []
    try:
        entries = list(os.scandir(DOWNLOADS_DIR))
    except OSError:
        return out
    for e in entries:
        if not e.is_file() or e.name.startswith("."):
            continue
        p = Path(e.path)
        if p.suffix.lower() not in AUDIO_EXTS:
            continue          # .crdownload and friends: not finished yet
        try:
            st = e.stat()
        except OSError:
            continue
        if since is not None and st.st_mtime < since:
            continue
        src = _loop_owner(e.name, st.st_mtime)
        if src:
            out.append((p, src, st.st_size))
    return out


def _loops_watcher():
    sizes: dict = {}
    while True:
        time.sleep(2)
        try:
            since = _loop_watch_since()
            seen = {}
            for p, src, size in _loop_candidates(since):
                seen[str(p)] = size
                # Moved only once its size has held still for a poll — a
                # browser can create the final name before it has finished.
                if sizes.get(str(p)) == size and size > 0:
                    with _loop_watch_lock:
                        if p.exists():
                            _move_into_loops(p, src)
                    seen.pop(str(p), None)
            sizes = seen
        except Exception:
            pass


threading.Thread(target=_loops_watcher, daemon=True, name="loops-watcher").start()


@app.get("/api/loops/sources")
def loops_sources():
    _loop_watch_since()
    out = []
    for src in _loop_sources():
        folder = _loop_folder(src)
        try:
            count = sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS) if folder.is_dir() else 0
        except OSError:
            count = 0
        out.append({**src, "folder_rel": folder.relative_to(MUSIC_ROOT).as_posix(), "count": count})
    return {"sources": out, "downloads_dir": str(DOWNLOADS_DIR)}


class LoopSourceRequest(BaseModel):
    name: str
    url: str
    match: Optional[str] = None


@app.post("/api/loops/sources")
def loops_add_source(req: LoopSourceRequest):
    name = (req.name or "").strip()[:60]
    url = (req.url or "").strip()
    if not name or not _re_mod.match(r"^https?://[^\s/]+\.[^\s]+$", url):
        return JSONResponse({"error": "Zadej název a platnou adresu (https://…)."}, status_code=400)
    match = (req.match or "").strip() or None
    if match:
        try:
            _re_mod.compile(match)
        except _re_mod.error:
            return JSONResponse({"error": "Neplatný vzor názvu souboru."}, status_code=400)
    sid = _re_mod.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "web"
    existing = {s["id"] for s in _loop_sources()}
    base, n = sid, 2
    while sid in existing:
        sid = f"{base}-{n}"; n += 1
    custom = list(_read_config().get("loop_sources") or [])
    custom.append({"id": sid, "name": name, "url": url, "match": match})
    _write_config({"loop_sources": custom})
    return {"ok": True, "id": sid}


@app.delete("/api/loops/sources/{source_id}")
def loops_remove_source(source_id: str):
    # Only the entry goes; its loops stay in the library.
    custom = [c for c in (_read_config().get("loop_sources") or []) if c.get("id") != source_id]
    _write_config({"loop_sources": custom})
    return {"ok": True}


class LoopOpenRequest(BaseModel):
    id: str


@app.post("/api/loops/open")
def loops_open(req: LoopOpenRequest):
    src = _loop_source(req.id)
    if src is None:
        return JSONResponse({"error": "Neznámý zdroj."}, status_code=404)
    _loop_opened[src["id"]] = time.time()
    _loop_watch_since()
    url = src["url"]
    r = subprocess.run(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=1280,860"],
                       capture_output=True)
    if r.returncode != 0:
        subprocess.run(["open", url], check=False)
    return {"ok": True}


@app.get("/api/loops/pending")
def loops_pending():
    """Loop-site files in Downloads from before watching started — offered
    for import on request, never moved behind the user's back."""
    since = _loop_watch_since()
    out = []
    for p, src, size in _loop_candidates(None):
        try:
            if p.stat().st_mtime >= since:
                continue
        except OSError:
            continue
        out.append({"name": p.name, "source": src["id"], "size_bytes": size})
    return {"files": out}


@app.post("/api/loops/import-pending")
def loops_import_pending():
    since = _loop_watch_since()
    moved = 0
    with _loop_watch_lock:
        for p, src, _ in _loop_candidates(None):
            try:
                if p.stat().st_mtime >= since:
                    continue
            except OSError:
                continue
            if _move_into_loops(p, src):
                moved += 1
    return {"ok": True, "moved": moved}


@app.get("/api/loops/events")
def loops_events(since: float = 0):
    return {"events": [e for e in _loop_events if e["t"] > since], "now": time.time()}


# ------------------------------------------------- loop sites inside the app
#
# Looperman (like most sites) refuses to be framed by another origin, so the
# Loops page shows it through this proxy instead: /ext/<source>/<path> is
# fetched from the site, its own links rewritten back to /ext/<source>/…, the
# anti-framing headers dropped. It runs on a port of its own (8766), so the
# site's scripts live on a different origin than the app and can't reach
# its API or its page. The login lives here, server-side, in a cookie jar
# per site (the browser never holds the site's cookies). Anything the site
# sends as a file download is saved straight into Library/Loops/<site>.
import http.cookiejar as _cookiejar
from urllib.parse import urlsplit as _urlsplit, quote as _urlquote

LOOP_PROXY_PORT = 8766
WEB_SESSIONS_DIR = NAM_PREVIEW_DIR / "web-sessions"
_proxy_clients: dict = {}
_proxy_started = False
_proxy_lock = threading.Lock()
_AD_SCRIPT_RE = _re_mod.compile(
    r'<script[^>]*src="[^"]*(?:pub\.network|googlesyndication|btloader|googletagmanager|doubleclick|adservice)[^"]*"[^>]*>\s*</script>',
    _re_mod.IGNORECASE)
_DROP_RESP_HEADERS = {"x-frame-options", "content-security-policy", "content-security-policy-report-only",
                      "set-cookie", "content-length", "content-encoding", "transfer-encoding", "connection",
                      "strict-transport-security", "alt-svc", "location"}


def _source_origin(src: dict) -> str:
    u = _urlsplit(src["url"])
    return f"{u.scheme}://{u.netloc}"


def _proxy_client(src: dict) -> httpx.Client:
    c = _proxy_clients.get(src["id"])
    if c is None:
        WEB_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        jar = _cookiejar.LWPCookieJar(str(WEB_SESSIONS_DIR / f"{src['id']}.cookies"))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (OSError, _cookiejar.LoadError):
            pass
        c = httpx.Client(cookies=jar, follow_redirects=False, timeout=60.0)
        c._spotdl_jar = jar
        _proxy_clients[src["id"]] = c
    return c


def _save_jar(c: httpx.Client):
    jar = getattr(c, "_spotdl_jar", None)
    if jar is None:
        return
    try:
        jar.save(ignore_discard=True, ignore_expires=True)
        os.chmod(jar.filename, 0o600)
    except OSError:
        pass


def _rewrite_text(text: str, src: dict, html: bool) -> str:
    origin = _source_origin(src)
    host = _urlsplit(origin).netloc
    prefix = f"/ext/{src['id']}"
    bare = host[4:] if host.startswith("www.") else host
    for h in {host, bare, "www." + bare}:
        for scheme in ("https:", "http:", ""):
            text = text.replace(f"{scheme}//{h}", prefix)
            text = text.replace(f"{scheme}\\/\\/{h}".replace("\\/", "\\/"), prefix.replace("/", "\\/"))
    if html:
        text = _AD_SCRIPT_RE.sub("", text)
        # Root-relative links (href="/x") would otherwise leave the proxy.
        text = _re_mod.sub(r'(\s(?:href|src|action|data-[\w-]+)=["\'])/(?!/|ext/)', r"\1" + prefix + "/", text)
        shim = ("<script>(function(){var P=%s;function fx(u){if(typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/'&&u.indexOf(P+'/')!==0)return P+u;return u}"
                "var f=window.fetch;window.fetch=function(u,o){if(typeof u==='string')u=fx(u);return f.call(this,u,o)};"
                "var op=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=fx(u);return op.apply(this,arguments)};"
                "var wo=window.open;window.open=function(u){arguments[0]=fx(u);return wo.apply(this,arguments)};"
                "if(window.parent!==window){var rp=function(){try{parent.postMessage({spotdlLoops:location.pathname+location.search,title:document.title},'*')}catch(e){}};rp();"
                "addEventListener('DOMContentLoaded',rp);"
                "addEventListener('message',function(e){if(e.data==='spotdl-back')history.back();if(e.data==='spotdl-forward')history.forward();});}"
                "})();</script>") % json.dumps(prefix)
        text = _re_mod.sub(r"(<head[^>]*>)", lambda m: m.group(1) + shim, text, count=1, flags=_re_mod.IGNORECASE)
    return text


def _download_name(resp: httpx.Response, url: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = _re_mod.search(r"filename\*=UTF-8''([^;]+)", cd, _re_mod.IGNORECASE) or \
        _re_mod.search(r'filename="?([^";]+)"?', cd, _re_mod.IGNORECASE)
    name = m.group(1) if m else Path(_urlsplit(url).path).name
    from urllib.parse import unquote
    name = Path(unquote(name)).name.strip() or "loop"
    return _re_mod.sub(r'[\\/:*?"<>|]', "_", name)


def _save_proxy_download(src: dict, resp: httpx.Response, url: str) -> Optional[str]:
    name = _download_name(resp, url)
    folder = _loop_folder(src)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    n = 2
    while dest.exists():
        dest = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    tmp = dest.with_name(dest.name + ".part")
    try:
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
        tmp.replace(dest)
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    try:
        rel = dest.relative_to(MUSIC_ROOT).as_posix()
    except ValueError:
        rel = dest.name
    _loop_events.append({"t": time.time(), "source": src["id"], "name": dest.name, "rel": rel})
    del _loop_events[:-50]
    return rel


proxy_app = FastAPI(title="loop sites proxy")


@proxy_app.api_route("/ext/{source_id}/{path:path}", methods=["GET", "POST", "HEAD"])
async def loop_proxy(source_id: str, path: str, request: Request):
    import asyncio
    body = await request.body()
    return await asyncio.to_thread(_loop_proxy_sync, source_id, path, request, body)


def _loop_proxy_sync(source_id: str, path: str, request: Request, body: bytes):
    from fastapi.responses import Response
    src = _loop_source(source_id)
    if src is None:
        return JSONResponse({"error": "Neznámý zdroj."}, status_code=404)
    origin = _source_origin(src)
    prefix = f"/ext/{src['id']}"
    url = origin + "/" + path
    if request.url.query:
        url += "?" + request.url.query
    fwd = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in ("host", "cookie", "connection", "content-length", "accept-encoding") or lk.startswith("sec-fetch"):
            continue
        if lk in ("origin", "referer"):
            v = v.replace(f"{request.url.scheme}://{request.url.netloc}{prefix}", origin)
            v = v.replace(f"{request.url.scheme}://{request.url.netloc}", origin)
        fwd[k] = v
    # Sites protect their files against hotlinking by Referer; a request the
    # browser sent without one still comes "from the site" as far as it knows.
    if not any(k.lower() == "referer" for k in fwd):
        fwd["Referer"] = origin + "/"
    dest = (request.headers.get("sec-fetch-dest") or "").lower()
    client = _proxy_client(src)
    try:
        req = client.build_request(request.method, url, headers=fwd, content=body or None)
        resp = client.send(req, stream=True)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": f"{src['name']} neodpověděl: {exc}"}, status_code=502)
    try:
        _save_jar(client)
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        disp = resp.headers.get("content-disposition", "").lower()
        is_file_type = ctype.startswith("audio/") or ctype in (
            "application/zip", "application/x-zip-compressed", "application/octet-stream", "application/x-rar-compressed")
        # A real download (not the site's own preview player): sent as an
        # attachment, or a file opened as a page rather than by an <audio>.
        if resp.status_code == 200 and ("attachment" in disp or (is_file_type and dest in ("document", "iframe"))):
            rel = _save_proxy_download(src, resp, url)
            if rel is None:
                return JSONResponse({"error": "Uložení selhalo."}, status_code=500)
            return Response(status_code=204)

        headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESP_HEADERS}
        loc = resp.headers.get("location")
        if loc:
            if loc.startswith(origin):
                loc = prefix + loc[len(origin):]
            elif loc.startswith("/") and not loc.startswith("//"):
                loc = prefix + loc
            else:
                loc = _rewrite_text(loc, src, False)
            headers["location"] = loc
        textual = ctype.startswith("text/") or ctype in ("application/javascript", "application/json",
                                                           "application/x-javascript", "image/svg+xml")
        if textual:
            raw = resp.read()
            text = raw.decode(resp.encoding or "utf-8", errors="replace")
            text = _rewrite_text(text, src, ctype == "text/html")
            return Response(text.encode("utf-8"), status_code=resp.status_code, headers=headers,
                            media_type=resp.headers.get("content-type"))
        data = resp.read()
        return Response(data, status_code=resp.status_code, headers=headers,
                        media_type=resp.headers.get("content-type"))
    finally:
        resp.close()


@proxy_app.get("/")
def proxy_root():
    return JSONResponse({"ok": True})


def _ensure_loop_proxy():
    global _proxy_started
    with _proxy_lock:
        if _proxy_started:
            return
        _proxy_started = True
    import uvicorn

    def run():
        try:
            uvicorn.Server(uvicorn.Config(proxy_app, host="127.0.0.1", port=LOOP_PROXY_PORT,
                                          log_level="warning")).run()
        except BaseException:
            pass
    threading.Thread(target=run, daemon=True, name="loop-proxy").start()


@app.get("/api/loops/web")
def loops_web(id: str):
    """Where the Loops page frames a site from (its start page via the proxy)."""
    src = _loop_source(id)
    if src is None:
        return JSONResponse({"error": "Neznámý zdroj."}, status_code=404)
    _ensure_loop_proxy()
    u = _urlsplit(src["url"])
    start = (u.path or "/") + (("?" + u.query) if u.query else "")
    return {"port": LOOP_PROXY_PORT, "path": f"/ext/{src['id']}{start}"}


@app.post("/api/loops/logout")
def loops_logout(req: LoopOpenRequest):
    src = _loop_source(req.id)
    if src is None:
        return JSONResponse({"error": "Neznámý zdroj."}, status_code=404)
    c = _proxy_clients.pop(src["id"], None)
    if c is not None:
        c.close()
    (WEB_SESSIONS_DIR / f"{src['id']}.cookies").unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------- Guitar Pro & co.
#
# Folders of tab files anywhere on the disk (Guitar Pro 3–8, MusicXML,
# Capella) are "mapped" like DI folders: read in place, never moved. The
# Taby page plays them with alphaTab — its own synthesizer through a
# SoundFont, or in sync with the real recording of the song, separated into
# stems — and remembers per file which recording and which sync it uses.
TAB_FILE_EXTS = {".gp", ".gpx", ".gp3", ".gp4", ".gp5", ".gp7", ".gp8", ".musicxml", ".mxl", ".capx"}
TAB_LINKS_PATH = NAM_PREVIEW_DIR / "tab_links.json"
SOUNDFONT_DIR = NAM_PREVIEW_DIR / "soundfonts"
SOUNDFONT_SOURCES = {
    # Free for any use incl. redistribution (S. Christian Collins, license v2.0).
    "GeneralUser-GS.sf2": "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2",
}
_soundfont_jobs: dict = {}
_tab_index_cache: dict = {"key": None, "at": 0.0, "files": []}


def _tab_dirs() -> list:
    return [d for d in (_read_config().get("tab_dirs") or []) if isinstance(d, str) and d]


def _resolve_under_tab_dirs(path: str) -> Optional[Path]:
    if not path or not Path(path).is_absolute():
        return None
    try:
        candidate = Path(path).resolve()
    except (ValueError, OSError):
        return None
    for d in _tab_dirs():
        try:
            root = Path(d).resolve()
        except (ValueError, OSError):
            continue
        if candidate == root or root in candidate.parents:
            return candidate
    return None


def _tab_meta_from_name(p: Path, root: Path) -> dict:
    stem = p.stem.replace("_", " ").strip()
    artist, title = "", stem
    if " - " in stem:
        artist, title = [x.strip() for x in stem.split(" - ", 1)]
    elif p.parent != root:
        artist = p.parent.name
    return {"artist": artist, "title": title}


def _scan_tab_dirs(force: bool = False) -> list:
    dirs = _tab_dirs()
    key = tuple(dirs)
    if not force and _tab_index_cache["key"] == key and time.time() - _tab_index_cache["at"] < 30:
        return _tab_index_cache["files"]
    out = []
    for d in dirs:
        root = Path(d)
        if not root.is_dir():
            continue
        for cur, subdirs, files in os.walk(root):
            subdirs[:] = [s for s in subdirs if not s.startswith(".")]
            if len(Path(cur).relative_to(root).parts) > 8:
                subdirs[:] = []
            for f in files:
                p = Path(cur) / f
                if f.startswith(".") or p.suffix.lower() not in TAB_FILE_EXTS:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                folder = Path(cur).relative_to(root).as_posix()
                out.append({"path": str(p), "name": f, "ext": p.suffix.lower().lstrip("."),
                            "size_bytes": size, "root": str(root), "folder": "" if folder == "." else folder,
                            **_tab_meta_from_name(p, root)})
    out.sort(key=lambda x: (x["artist"].lower(), x["title"].lower()))
    _tab_index_cache.update({"key": key, "at": time.time(), "files": out})
    return out


def _tab_links() -> dict:
    try:
        return json.loads(TAB_LINKS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


@app.get("/api/tabfiles/dirs")
def tabfiles_dirs():
    return {"dirs": [{"path": d, "name": Path(d).name or d, "exists": Path(d).is_dir()} for d in _tab_dirs()]}


@app.post("/api/tabfiles/dirs")
def tabfiles_add_dir(req: DiDirRequest):
    path = req.path
    if not path:
        try:
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Složka s taby (Guitar Pro…)")'],
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {**tabfiles_dirs(), "added": None}
        if r.returncode != 0:
            return {**tabfiles_dirs(), "added": None}
        path = r.stdout.strip()
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        return JSONResponse({"error": "Složka neexistuje."}, status_code=400)
    p = p.resolve()
    dirs = _tab_dirs()
    if str(p) not in dirs:
        dirs.append(str(p))
        _write_config({"tab_dirs": dirs})
    return {**tabfiles_dirs(), "added": str(p)}


@app.post("/api/tabfiles/dirs/remove")
def tabfiles_remove_dir(req: DiDirRequest):
    _write_config({"tab_dirs": [d for d in _tab_dirs() if d != req.path]})
    return tabfiles_dirs()


# ------------------------------------------------- Pick Tabs (the catalog)
#
# Every Songsterr tab the app has opened — for a song in the mixer or for the
# player — is remembered here: which song, which part, with what it was used.
# Only the reference: the notation itself is fetched from Songsterr again when
# it is opened, it is not copied to disk.
PICK_TABS_PATH = NAM_PREVIEW_DIR / "pick_tabs.json"
PICK_TABS_FOLDER = "Pick Tabs"


def _pick_tabs() -> list:
    try:
        data = json.loads(PICK_TABS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


class PickTabRequest(BaseModel):
    songId: int
    revisionId: Optional[int] = None
    trackIndex: int = 0
    title: str = ""
    artist: str = ""
    track: str = ""
    usedWith: str = ""


@app.get("/api/picktabs")
def picktabs_list():
    return {"tabs": _pick_tabs()}


@app.post("/api/picktabs")
def picktabs_add(req: PickTabRequest):
    return _picktab_upsert(req)


_PICK_TABS_LOCK = threading.Lock()


def _picktab_upsert(req: "PickTabRequest") -> dict:
    with _PICK_TABS_LOCK:
        return _picktab_upsert_locked(req)


def _picktab_upsert_locked(req: "PickTabRequest") -> dict:
    tabs = _pick_tabs()
    old = next((t for t in tabs if t.get("songId") == req.songId), None)
    now = int(time.time())
    entry = {**(old or {"addedAt": now, "usedWith": []}),
             "songId": req.songId, "revisionId": req.revisionId, "trackIndex": req.trackIndex,
             "title": req.title or (old or {}).get("title", ""), "artist": req.artist or (old or {}).get("artist", ""),
             "track": req.track or (old or {}).get("track", ""), "lastUsed": now}
    used = [u for u in entry.get("usedWith") or [] if u != req.usedWith]
    if req.usedWith:
        used.insert(0, req.usedWith)
    entry["usedWith"] = used[:5]
    tabs = [entry] + [t for t in tabs if t.get("songId") != req.songId]
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    PICK_TABS_PATH.write_text(json.dumps(tabs, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "count": len(tabs)}


@app.delete("/api/picktabs/{song_id}")
def picktabs_remove(song_id: int):
    tabs = [t for t in _pick_tabs() if t.get("songId") != song_id]
    PICK_TABS_PATH.write_text(json.dumps(tabs, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


# ------------------------------------------- Pick Tabs: favourites synced
#
# A song, playlist or band added to the favourites has its tabs looked up on
# Songsterr in the background and put into the catalog. One worker thread,
# one request at a time with a pause between — Songsterr is a guest here.

_PT_SYNC = {"queue": [], "running": False, "done": 0, "total": 0, "added": 0,
            "current": "", "label": "", "finished_at": 0}
_PT_SYNC_LOCK = threading.Lock()


class PickSyncRequest(BaseModel):
    kind: str                     # "song" | "playlist" | "band"
    title: str = ""
    artist: str = ""
    playlistId: str = ""
    label: str = ""


def _base_title(t: str) -> str:
    """"Master of Puppets (Remastered)" → "master of puppets"."""
    t = _re_mod.sub(r"\s*[\(\[].*?[\)\]]", "", t or "")
    t = _re_mod.split(r"\s+[-–]\s+", t)[0]
    return _name_key(t)


def _songsterr_search(pattern: str, size: int = 20) -> list:
    r = httpx.get("https://www.songsterr.com/api/songs", params={"pattern": pattern, "size": size},
                  headers={"User-Agent": APP_UA}, timeout=15, follow_redirects=True)
    r.raise_for_status()
    data = r.json()
    recs = data if isinstance(data, list) else (data.get("records", []) if isinstance(data, dict) else [])
    return [x for x in recs if x.get("songId") is not None and not x.get("isJunk")]


def _guitar_index(rec: dict) -> int:
    for k in ("popularTrackGuitar", "defaultTrack", "popularTrack"):
        if isinstance(rec.get(k), int) and rec[k] >= 0:
            return rec[k]
    return 0


def _rec_to_tab(rec: dict, used: str) -> "PickTabRequest":
    tracks = rec.get("tracks") or []
    i = _guitar_index(rec)
    t = tracks[i] if 0 <= i < len(tracks) else {}
    return PickTabRequest(songId=int(rec["songId"]), trackIndex=i, title=rec.get("title") or "",
                          artist=rec.get("artist") or "", usedWith=used,
                          track=" — ".join(x for x in (t.get("instrument"), t.get("name")) if x))


def _match_song(artist: str, title: str):
    """The Songsterr tab of one song — only when artist and title both agree,
    a wrong tab in the catalog being worse than none."""
    want_a, want_t = _name_key(artist.split(",")[0]), _base_title(title)
    if not want_t:
        return None
    for rec in _songsterr_search(f"{artist.split(',')[0]} {title}".strip(), 10):
        rt = _base_title(rec.get("title") or "")
        # "Kabát" is Kabát, or one of "Kabát & Friends" — not "Kabát revival".
        parts = [_name_key(x) for x in _re_mod.split(r"\s*(?:,|&|/|\bfeat\.?|\bft\.?|\band\b|\bx\b)\s*", rec.get("artist") or "", flags=_re_mod.I)]
        if want_a and want_a not in parts and _name_key(rec.get("artist") or "") != want_a:
            continue
        if rt == want_t or (len(rt) > 3 and (rt in want_t or want_t in rt)):
            return rec
    return None


def _sync_jobs_for(req: PickSyncRequest) -> list:
    """The request as a list of (label, callable returning [records])."""
    used = req.label or "Oblíbené"
    if req.kind == "song":
        return [(f"{req.artist} – {req.title}", lambda: [r for r in [_match_song(req.artist, req.title)] if r], used)]
    if req.kind == "band":
        def band():
            want = _name_key(req.artist)
            seen, out = set(), []
            for rec in _songsterr_search(req.artist, 40):
                if _name_key(rec.get("artist") or "") != want:
                    continue
                bt = _base_title(rec.get("title") or "")
                if bt and bt not in seen:
                    seen.add(bt)
                    out.append(rec)
            return out[:25]
        return [(req.artist, band, used)]
    if req.kind == "playlist":
        try:
            d = playlist_detail(req.playlistId, track_limit=150)
        except Exception:
            return []
        if not isinstance(d, dict):
            return []
        used = req.label or f"Playlist {d.get('title') or ''}".strip()
        return [(f"{t.get('artists')} – {t.get('title')}",
                 (lambda a=t.get("artists") or "", ti=t.get("title") or "": [r for r in [_match_song(a, ti)] if r]),
                 used) for t in d.get("tracks") or []]
    return []


def _pt_sync_worker():
    while True:
        with _PT_SYNC_LOCK:
            if not _PT_SYNC["queue"]:
                _PT_SYNC.update(running=False, current="", finished_at=time.time())
                return
            label, fn, used = _PT_SYNC["queue"].pop(0)
            _PT_SYNC["current"] = label
        try:
            have = {t.get("songId") for t in _pick_tabs()}
            for rec in fn():
                if int(rec["songId"]) not in have:
                    _picktab_upsert(_rec_to_tab(rec, used))
                    with _PT_SYNC_LOCK:
                        _PT_SYNC["added"] += 1
        except Exception:                                      # noqa: BLE001 — one song failing is not the sync failing
            pass
        with _PT_SYNC_LOCK:
            _PT_SYNC["done"] += 1
        time.sleep(0.6)


@app.post("/api/picktabs/sync")
def picktabs_sync(req: PickSyncRequest):
    jobs = _sync_jobs_for(req)
    with _PT_SYNC_LOCK:
        if not _PT_SYNC["running"]:
            _PT_SYNC.update(done=0, total=0, added=0)
        _PT_SYNC["queue"].extend(jobs)
        _PT_SYNC["total"] += len(jobs)
        _PT_SYNC["label"] = req.label or req.artist or req.title
        start = not _PT_SYNC["running"] and bool(_PT_SYNC["queue"])
        if start:
            _PT_SYNC["running"] = True
    if start:
        threading.Thread(target=_pt_sync_worker, daemon=True).start()
    return picktabs_sync_status()


@app.get("/api/picktabs/sync")
def picktabs_sync_status():
    with _PT_SYNC_LOCK:
        return {k: _PT_SYNC[k] for k in ("running", "done", "total", "added", "current", "finished_at")}


def _pick_tab_rows() -> list:
    """The catalog as rows of the Taby list, in a folder of its own."""
    return [{"path": f"songsterr:{t['songId']}", "name": f"{t.get('artist', '')} - {t.get('title', '')}",
             "ext": "songsterr", "size_bytes": 0, "root": "", "folder": PICK_TABS_FOLDER,
             "artist": t.get("artist", ""), "title": t.get("title", ""),
             "songsterr": {"songId": t["songId"], "trackIndex": t.get("trackIndex", 0), "track": t.get("track", "")},
             "usedWith": t.get("usedWith") or []}
            for t in _pick_tabs() if t.get("songId") is not None]


@app.get("/api/tabfiles/list")
def tabfiles_list(refresh: bool = False):
    files = _pick_tab_rows() + _scan_tab_dirs(refresh)
    links = _tab_links()
    return {"files": [{**f, "linked": f["path"] in links} for f in files]}


@app.get("/api/tabfiles/file")
def tabfiles_file(path: str):
    target = _resolve_under_tab_dirs(path)
    if target is None or not target.is_file() or target.suffix.lower() not in TAB_FILE_EXTS:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    from fastapi.responses import Response
    return Response(target.read_bytes(), media_type="application/octet-stream")


class TabLinkRequest(BaseModel):
    path: str
    link: Optional[dict] = None     # None removes it


@app.get("/api/tabfiles/link")
def tabfiles_get_link(path: str):
    return {"link": _tab_links().get(path)}


@app.put("/api/tabfiles/link")
def tabfiles_put_link(req: TabLinkRequest):
    if not req.path.startswith("songsterr:") and _resolve_under_tab_dirs(req.path) is None:
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    links = _tab_links()
    if req.link:
        links[req.path] = {**req.link, "updated": int(time.time())}
    else:
        links.pop(req.path, None)
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    TAB_LINKS_PATH.write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@app.get("/api/tabfiles/yt-search")
def tabfiles_yt_search(q: str):
    out = []
    try:
        for kind in ("songs", "videos"):
            for r in yt().search(q, filter=kind, limit=6) or []:
                vid = r.get("videoId")
                if not vid or any(o["videoId"] == vid for o in out):
                    continue
                out.append({"videoId": vid, "title": r.get("title") or "",
                            "artists": ", ".join(a.get("name", "") for a in (r.get("artists") or [])),
                            "duration": r.get("duration") or "", "kind": kind,
                            "thumbnail": ((r.get("thumbnails") or [{}])[-1] or {}).get("url")})
            if len(out) >= 6:
                break
    except Exception as e:
        return JSONResponse({"error": f"Hledání selhalo: {e}"}, status_code=502)
    return {"results": out[:8]}


class TabAudioRequest(BaseModel):
    video_id: str
    name: str


@app.post("/api/tabfiles/fetch-audio")
def tabfiles_fetch_audio(req: TabAudioRequest):
    """Downloads a YouTube recording's audio into the library (Songs/Taby),
    as a job the page follows like any other download."""
    if not _re_mod.fullmatch(r"[\w-]{6,20}", req.video_id or ""):
        return JSONResponse({"error": "Neplatné video."}, status_code=400)
    # The video's id is part of the name: two recordings of one song are two
    # files (and two stem sets). Named by artist and title alone, picking a
    # different recording found the old file "already downloaded" and the
    # old stems came back. The tags (embedded below) carry the clean name.
    safe = _safe_name(f"{req.name} [{req.video_id}]")
    out_dir = MUSIC_ROOT / "Songs" / "Taby"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{safe}.mp3"
    rel = target.relative_to(MUSIC_ROOT).as_posix()
    if target.is_file() and target.stat().st_size > 0:
        return {"job_id": None, "rel": rel, "ready": True}
    py = str(Path(SPOTDL_BIN).resolve().parent / "python")
    cmd = [py, "-m", "yt_dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
           "--embed-metadata",
           "--no-playlist", "--newline", "--ffmpeg-location", ffmpeg_bin(),
           "-o", str(out_dir / f"{safe}.%(ext)s"),
           f"https://music.youtube.com/watch?v={req.video_id}"]
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"cmd": cmd, "status": "queued", "lines": [], "output_dir": str(out_dir)}
    return {"job_id": job_id, "rel": rel, "ready": False}


@app.get("/api/soundfonts")
def soundfonts_list():
    out = [{"id": "builtin:sonivox", "name": "Sonivox (základní, 1 MB)", "url": "/static/alphatab/soundfont/sonivox.sf3",
            "ready": True}]
    SOUNDFONT_DIR.mkdir(parents=True, exist_ok=True)
    local = {p.name: p for p in SOUNDFONT_DIR.iterdir() if p.suffix.lower() in (".sf2", ".sf3")}
    for name in SOUNDFONT_SOURCES:
        job = _soundfont_jobs.get(name)
        out.append({"id": name, "name": "GeneralUser GS (kvalitní, 32 MB)" if name.startswith("GeneralUser") else name,
                    "url": f"/api/soundfonts/file/{name}", "ready": name in local,
                    "downloadable": True, "progress": job.get("progress") if job else None,
                    "error": job.get("error") if job else None})
    for name in sorted(local):
        if name not in SOUNDFONT_SOURCES:
            out.append({"id": name, "name": name, "url": f"/api/soundfonts/file/{name}", "ready": True})
    return {"soundfonts": out, "folder": str(SOUNDFONT_DIR)}


@app.post("/api/soundfonts/download/{name}")
def soundfonts_download(name: str):
    url = SOUNDFONT_SOURCES.get(name)
    if not url:
        return JSONResponse({"error": "Neznámá zvuková banka."}, status_code=404)
    job = _soundfont_jobs.get(name)
    if job and job.get("running"):
        return {"ok": True}
    job = _soundfont_jobs[name] = {"running": True, "progress": 0.0, "error": None}

    def run():
        SOUNDFONT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SOUNDFONT_DIR / (name + ".part")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                got = 0
                with tmp.open("wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
                        got += len(chunk)
                        if total:
                            job["progress"] = round(got / total, 3)
            tmp.replace(SOUNDFONT_DIR / name)
            job["progress"] = 1.0
        except Exception as e:
            tmp.unlink(missing_ok=True)
            job["error"] = str(e)
        finally:
            job["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/soundfonts/file/{name}")
def soundfonts_file(name: str, request: Request):
    p = SOUNDFONT_DIR / Path(name).name
    if p.suffix.lower() not in (".sf2", ".sf3") or not p.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return _range_file_response(p, request.headers.get("range"))


@app.get("/api/preview")
def preview(q: str, kind: str = "song"):
    """Najde nejbližší YouTube video pro náhled (u výsledků hledání písní,
    kde ještě nemáme videoId — u playlistů ho už máme rovnou).

    kind=video hledá nejdřív videoklip (mini přehrávač ho ukazuje); "songs"
    na YouTube Music bývá jen zvuková stopa se statickým obrázkem.
    """
    order = ("videos", "songs") if kind == "video" else ("songs", "videos")
    res = yt().search(q, filter=order[0], limit=1)
    if not res:
        res = yt().search(q, filter=order[1], limit=1)
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
    # The mini player shows the video and finds the lyrics by these, and marks
    # which queue entry plays by the key.
    videoId: Optional[str] = None
    key: Optional[str] = None
    relPath: Optional[str] = None
    # Whether the app is listening for spoken commands (the mini player shows it).
    voice: Optional[bool] = None


class RemoteCommand(BaseModel):
    action: str
    value: Optional[float] = None
    text: Optional[str] = None      # "heard": what the menu bar's listening heard, for the window to act on


# A song handed to the mini player by the app ("send to mini"): the mini player
# picks it up from the state it polls anyway — also when it was only just opened.
_MINI_PLAY = {"seq": 0, "at": 0.0}


class MiniPlay(BaseModel):
    stems: str
    title: str = ""
    artists: str = ""


@app.post("/api/mini/play")
def mini_play(body: MiniPlay):
    d = _resolve_under_music_root(f"Stems/{body.stems}")
    if d is None or not d.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    _MINI_PLAY.update(seq=int(time.time() * 1000), at=time.time(), stems=body.stems, title=body.title or body.stems, artists=body.artists)
    return {"ok": True}


# The mini player says who it is when it asks (who=mini): that is how the
# server knows one is open.
_MINI_SEEN = {"at": 0.0}
_MINI_VOICE = {"seq": 0, "at": 0.0}      # a spoken command passed on to the mini player
_WIDGET_OPEN = {"seq": 0}                # "pick open": the launcher opens the app's window when this changes


@app.get("/api/remote/state")
def remote_state(who: str = ""):
    """What the playing window last said it was doing."""
    out = dict(_REMOTE_STATE)
    if who == "mini":
        _MINI_SEEN["at"] = time.time()
    if time.time() - _MINI_PLAY["at"] < 30:
        out["mini"] = dict(_MINI_PLAY)
    if time.time() - _MINI_VOICE["at"] < 30:
        out["mini_voice"] = dict(_MINI_VOICE)
    out["widget"] = bool(_WIDGET.get("running"))
    out["widget_open"] = _WIDGET_OPEN["seq"]
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


@app.post("/api/system/audio-midi-setup")
def open_audio_midi_setup():
    """Opens macOS's Audio MIDI Setup (BlackHole, aggregate devices, network MIDI)."""
    subprocess.run(["open", "-a", "Audio MIDI Setup"], check=False)
    return {"ok": True}


# ------------------------------------------------------------ saved tunings
#
# Named tunings of the user's own, shared by the tuner and the virtual
# keyboard's fretboard. Strings low → high in scientific pitch ("D2").
_re_tone = _re_mod.compile(r"[A-H](?:#|b)?-?\d")


class Tuning(BaseModel):
    id: Optional[str] = None
    name: str
    strings: List[str]


def _tunings() -> list:
    out = []
    for t in _read_config().get("tunings") or []:
        if isinstance(t, dict) and t.get("id") and t.get("name") and isinstance(t.get("strings"), list):
            out.append({"id": str(t["id"]), "name": str(t["name"]), "strings": [str(x) for x in t["strings"]]})
    return out


@app.get("/api/tunings")
def tunings_list():
    return {"tunings": _tunings()}


@app.put("/api/tunings")
def tunings_save(body: Tuning):
    """Saves (or, with an id, updates) one named tuning."""
    import secrets
    strings = [x.strip() for x in body.strings]
    name = body.name.strip()[:40]
    if not name:
        return JSONResponse({"error": "Zadej název ladění."}, status_code=400)
    if not 1 <= len(strings) <= 12 or not all(_re_tone.fullmatch(x) for x in strings):
        return JSONResponse({"error": "Struny piš jako E2, F#3, Bb1 (1–12 strun)."}, status_code=400)
    items = _tunings()
    tid = body.id if body.id and any(t["id"] == body.id for t in items) else "t-" + secrets.token_hex(4)
    entry = {"id": tid, "name": name, "strings": strings}
    items = [entry if t["id"] == tid else t for t in items]
    if not any(t["id"] == tid for t in items):
        items.append(entry)
    _write_config({"tunings": items[:50]})
    return {"tuning": entry, "tunings": items[:50]}


@app.delete("/api/tunings/{tid}")
def tunings_delete(tid: str):
    items = [t for t in _tunings() if t["id"] != tid]
    _write_config({"tunings": items})
    return {"tunings": items}


# ---------------------------------------------------------------- ToneRig
#
# ToneRig (the NAM pedalboard app on the iPhone) can't tell anyone which
# preset it is on — but it recalls presets 1–8 by MIDI Learn. So the presets
# are switched from here: the Mac app sends ToneRig the MIDI message for a
# preset (Web MIDI → a network MIDI session or the interface's MIDI out) and
# therefore knows which one is on. The buttons can also be on the phone: a
# tiny page on its own port, reachable on the home network only while it is
# switched on and only with the secret token in its address. Nothing else of
# the app is served there.
TONERIG_LAN_PORT = 8767
_tonerig = {"seq": 0, "events": [], "server": None, "thread": None}
_tonerig_lock = threading.Lock()


def _tonerig_cfg() -> dict:
    cfg = dict(_read_config().get("tonerig") or {})
    presets = list(cfg.get("presets") or [])[:8]
    presets += [""] * (8 - len(presets))
    out = {
        "mode": cfg.get("mode") if cfg.get("mode") in ("pc", "cc", "note") else "pc",
        "channel": max(1, min(16, int(cfg.get("channel") or 1))),
        "cc": max(0, min(127, int(cfg.get("cc") or 0))),
        "base": max(0, min(120, int(cfg.get("base") or 0))),
        "output": str(cfg.get("output") or ""),
        "presets": [str(x or "")[:40] for x in presets],
        "active": cfg.get("active") if isinstance(cfg.get("active"), int) and 0 <= cfg.get("active") < 8 else None,
        "external": bool(cfg.get("external")),
        "lan": bool(cfg.get("lan")),
        "token": str(cfg.get("token") or ""),
    }
    return out


def _tonerig_save(patch: dict) -> dict:
    cfg = _tonerig_cfg()
    cfg.update(patch)
    _write_config({"tonerig": cfg})
    return cfg


def _lan_ip() -> Optional[str]:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # no packet is sent; it picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _tonerig_public(cfg: dict) -> dict:
    ip = _lan_ip()
    running = _tonerig["server"] is not None
    return {**{k: v for k, v in cfg.items() if k != "token"},
            "lanRunning": running,
            "phoneUrl": f"http://{ip}:{TONERIG_LAN_PORT}/t/{cfg['token']}" if (running and ip and cfg["token"]) else None}


def _tonerig_select(i: int, source: str) -> dict:
    if not 0 <= i < 8:
        return {"error": "Preset 1–8."}
    with _tonerig_lock:
        _tonerig["seq"] += 1
        _tonerig["events"].append({"seq": _tonerig["seq"], "i": i, "source": source})
        _tonerig["events"] = _tonerig["events"][-20:]
    cfg = _tonerig_save({"active": i})
    return {"ok": True, "active": i, "name": cfg["presets"][i], "seq": _tonerig["seq"]}


class ToneRigSettings(BaseModel):
    mode: Optional[str] = None
    channel: Optional[int] = None
    cc: Optional[int] = None
    base: Optional[int] = None
    output: Optional[str] = None
    presets: Optional[List[str]] = None
    external: Optional[bool] = None


class ToneRigSelect(BaseModel):
    i: int


@app.get("/api/tonerig")
def tonerig_get():
    return _tonerig_public(_tonerig_cfg())


@app.put("/api/tonerig")
def tonerig_put(body: ToneRigSettings):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    _tonerig_save(patch)
    return _tonerig_public(_tonerig_cfg())


@app.post("/api/tonerig/select")
def tonerig_select(body: ToneRigSelect):
    res = _tonerig_select(body.i, "mac")
    return JSONResponse(res, status_code=400) if "error" in res else res


@app.get("/api/tonerig/pending")
def tonerig_pending(since: int = 0):
    """For the Mac app: presets picked (on the phone) that still need their
    MIDI message sent."""
    with _tonerig_lock:
        ev = [e for e in _tonerig["events"] if e["seq"] > since]
        seq = _tonerig["seq"]
    cfg = _tonerig_cfg()
    return {"seq": seq, "events": ev, "active": cfg["active"],
            "name": cfg["presets"][cfg["active"]] if cfg["active"] is not None else None}


# -- the phone's page, on its own port
tonerig_app = FastAPI()


def _tonerig_token_ok(token: str) -> bool:
    import hmac
    cfg = _tonerig_cfg()
    # Switched off means refused at once — the port itself may take a moment
    # to close while the phone still holds a connection open.
    return cfg["lan"] and bool(cfg["token"]) and hmac.compare_digest(token, cfg["token"])


_TONERIG_PAGE = r"""<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes"><title>ToneRig presety</title>
<style>
:root{color-scheme:dark}body{margin:0;background:#0d0d10;color:#eee;font-family:-apple-system,system-ui,sans-serif;padding:16px}
h1{font-size:17px;margin:4px 0 2px}p{margin:0 0 14px;color:#888;font-size:13px}
.g{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button{background:#1d1d24;color:#eee;border:1px solid #33333d;border-radius:14px;padding:18px 10px;font-size:16px;text-align:left;min-height:84px}
button b{display:block;font-size:26px;color:#666}button.on{background:#123a22;border-color:#1ed760}button.on b{color:#1ed760}
#st{margin-top:14px;font-size:12px;color:#777}
</style></head><body>
<h1>ToneRig presety</h1><p>Klepnutím přepneš preset v ToneRigu — přes Mac (MIDI).</p>
<div class="g" id="g"></div><div id="st"></div>
<script>
const base = location.pathname.replace(/\/$/, "");
let st = null;
function paint(){
  const g = document.getElementById("g"); g.innerHTML = "";
  st.presets.forEach((n, i) => { const b = document.createElement("button");
    b.className = st.active === i ? "on" : ""; b.innerHTML = "<b>" + (i + 1) + "</b>" + (n ? n.replace(/</g, "&lt;") : "Preset " + (i + 1));
    b.onclick = () => pick(i); g.appendChild(b); });
}
async function load(){ try{ st = await (await fetch(base + "/state")).json(); paint(); document.getElementById("st").textContent = ""; }
  catch(e){ document.getElementById("st").textContent = "Mac nedostupný — běží aplikace a ovladač je zapnutý?"; } }
async function pick(i){ st.active = i; paint(); try{ await fetch(base + "/select", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({i})}); }catch(e){} }
load(); setInterval(load, 2500);
</script></body></html>"""


@tonerig_app.get("/t/{token}")
def tonerig_phone_page(token: str):
    if not _tonerig_token_ok(token):
        return JSONResponse({"error": "Neplatný odkaz."}, status_code=403)
    return HTMLResponse(_TONERIG_PAGE)


@tonerig_app.get("/t/{token}/state")
def tonerig_phone_state(token: str):
    if not _tonerig_token_ok(token):
        return JSONResponse({"error": "Neplatný odkaz."}, status_code=403)
    cfg = _tonerig_cfg()
    return {"presets": cfg["presets"], "active": cfg["active"]}


@tonerig_app.post("/t/{token}/select")
def tonerig_phone_select(token: str, body: ToneRigSelect):
    if not _tonerig_token_ok(token):
        return JSONResponse({"error": "Neplatný odkaz."}, status_code=403)
    res = _tonerig_select(body.i, "phone")
    return JSONResponse(res, status_code=400) if "error" in res else res


def _tonerig_lan(on: bool) -> None:
    import uvicorn
    with _tonerig_lock:
        srv = _tonerig["server"]
        if not on:
            if srv is not None:
                srv.should_exit = True
                _tonerig["server"] = None
            return
        if srv is not None:
            return
        srv = uvicorn.Server(uvicorn.Config(tonerig_app, host="0.0.0.0", port=TONERIG_LAN_PORT, log_level="warning",
                                            timeout_graceful_shutdown=1))
        _tonerig["server"] = srv

    def run():
        try:
            srv.run()
        except BaseException:
            pass
        finally:
            with _tonerig_lock:
                if _tonerig["server"] is srv:
                    _tonerig["server"] = None
    threading.Thread(target=run, daemon=True, name="tonerig-lan").start()


class ToneRigLan(BaseModel):
    on: bool
    new_token: bool = False


@app.post("/api/tonerig/lan")
def tonerig_lan(body: ToneRigLan):
    """The phone page on/off. A new secret link on request (or the first time)."""
    import secrets
    cfg = _tonerig_cfg()
    patch = {"lan": body.on}
    if body.on and (body.new_token or not cfg["token"]):
        patch["token"] = secrets.token_urlsafe(9)
    _tonerig_save(patch)
    _tonerig_lan(body.on)
    time.sleep(0.4 if body.on else 0)
    return _tonerig_public(_tonerig_cfg())


@app.on_event("startup")
def _tonerig_resume():
    if _tonerig_cfg()["lan"]:
        _tonerig_lan(True)


@app.post("/api/remote/command")
def push_remote_command(body: RemoteCommand):
    """Queue something for the playing window to do."""
    global _REMOTE_SEQ
    if body.action not in REMOTE_ACTIONS:
        return JSONResponse({"error": "Neznámý příkaz."}, status_code=400)
    _REMOTE_SEQ += 1
    cmd = {"action": body.action, "value": body.value}
    if body.text is not None:
        cmd["text"] = body.text
    _REMOTE_COMMANDS.append((_REMOTE_SEQ, cmd))
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
    "playqueue",   # value = index into the saved queue (the mini player's Fronta)
    "voice",       # listening for spoken commands: 1 on, 0 off, nothing = the other one
    "heard",       # text = a recognised command (JSON) from the menu bar's listening
}


class MiniResize(BaseModel):
    width: int
    height: int


@app.post("/api/mini/resize")
def mini_resize(body: MiniResize, request: Request):
    """Resizes the mini player's own Chrome window (its size buttons).

    A page can't resize a window it didn't open, so the server asks Chrome
    through AppleScript, as Pick does for the main window. The right edge and
    the top stay put — the mini player sits at the right of the screen.
    """
    w = max(240, min(2400, int(body.width)))
    h = max(150, min(1600, int(body.height)))
    url = f"http://{request.url.netloc}/mini"
    script = f'''
    if application "Google Chrome" is running then
      tell application "Google Chrome"
        repeat with win in windows
          repeat with t in tabs of win
            if URL of t starts with "{url}" then
              set b to bounds of win
              set r to item 3 of b
              set l to r - {w}
              if l < 0 then
                set l to 0
                set r to {w}
              end if
              set bounds of win to {{l, item 2 of b, r, (item 2 of b) + {h}}}
              return "ok"
            end if
          end repeat
        end repeat
      end tell
    end if
    return "none"'''
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=6).stdout.strip()
    except Exception:                                          # noqa: BLE001
        out = ""
    return {"ok": out == "ok"}


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
        if job.get("kind") == "stems":
            # Run by the queue worker; this stream only follows it, so
            # closing it changes nothing about the separation itself.
            sent, seq, last = 0, -1, None
            while True:
                lines = list(job["lines"])
                if job.get("_seq", 0) != seq:
                    seq = job.get("_seq", 0)
                    # The progress bar rewrites its line in place; send the
                    # final state of the last line already sent first.
                    if sent and len(lines) >= sent and lines[sent - 1] != last:
                        yield f"data: {json.dumps({'line': lines[sent - 1], 'replace': True})}\n\n"
                        last = lines[sent - 1]
                    for text in lines[sent:]:
                        yield f"data: {json.dumps({'line': text})}\n\n"
                        last = text
                    sent = len(lines)
                if job["status"] == "queued":
                    yield f"data: {json.dumps({'queued': True, 'position': next((i + 1 for i, (jid, _) in enumerate(_stems_active_jobs()) if jid == job_id), None)})}\n\n"
                if job["status"] in ("done", "error"):
                    yield f"data: {json.dumps({'done': True, 'status': job['status'], 'returncode': job.get('returncode')})}\n\n"
                    return
                await asyncio.sleep(1.0 if job["status"] == "queued" else 0.4)
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


class LibraryPathRequest(BaseModel):
    rel: str


@app.post("/api/library/reveal")
def library_reveal(req: LibraryPathRequest):
    """Shows a library file or folder selected in Finder."""
    target = _resolve_under_music_root(req.rel)
    if target is None or not target.exists():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    subprocess.run(["open", "-R", str(target)], check=False)
    return {"ok": True}


@app.post("/api/library/open")
def library_open(req: LibraryPathRequest):
    """Opens a library file in its default Mac app (PDF, picture, tab…)."""
    target = _resolve_under_music_root(req.rel)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    subprocess.run(["open", str(target)], check=False)
    return {"ok": True}


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
    # Who and what this is, while the source file is still known — it may
    # be moved or renamed later.
    tags = _audio_tags(source) if source.is_file() else {}
    if tags.get("title") or tags.get("artist"):
        try:
            (dest_dir / "meta.json").write_text(json.dumps({
                "artist": tags.get("artist"), "title": tags.get("title"),
                "album": tags.get("album")}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------ separation queue
#
# Separations used to run inside the browser's event stream: closing it (by
# starting a second one, or closing the window) left Demucs stalled on a
# full pipe and the stems never got moved into place. Now they are a queue
# the server works through on its own, one at a time — two Demucs runs side
# by side on one Mac are slower in total and can run out of memory. The
# event stream only watches.
_STEMS_QUEUE: list = []                 # job ids, oldest first
_STEMS_LOCK = threading.Lock()
_STEMS_WAKE = threading.Event()
_STEMS_WORKER: dict = {}


def _stems_active_jobs() -> list:
    """Queued and running separations, in the order they will finish."""
    with _STEMS_LOCK:
        return [(jid, JOBS[jid]) for jid in _STEMS_QUEUE
                if jid in JOBS and JOBS[jid]["status"] in ("queued", "running")]


def _run_stems_job(job: dict) -> None:
    job["status"] = "running"
    job["started"] = time.time()
    try:
        proc = subprocess.Popen(job["cmd"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0)
    except OSError as e:
        job["lines"].append(f"Demucs se nepodařilo spustit: {e}")
        job["status"] = "error"
        return
    job["proc"] = proc
    # Demucs draws its progress bar with carriage returns, so a line only
    # ends at the very end; split on both to see the percentage move.
    buf = b""
    while True:
        chunk = proc.stdout.read(512)
        if not chunk:
            break
        buf += chunk
        parts = _re_mod.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            text = raw.decode(errors="replace").strip()
            if not text:
                continue
            m = _re_mod.search(r"(\d{1,3}(?:\.\d+)?)%", text)
            if m:
                job["progress"] = min(100.0, float(m.group(1)))
                # A bar redraws hundreds of times; keep only its latest state.
                if job["lines"] and job.get("_bar"):
                    job["lines"][-1] = text
                    job["_seq"] = job.get("_seq", 0) + 1
                    continue
                job["_bar"] = True
            else:
                job["_bar"] = False
            job["lines"].append(text)
            job["_seq"] = job.get("_seq", 0) + 1
    rc = proc.wait()
    job["returncode"] = rc
    if rc == 0:
        try:
            _finalize_stems_job(job)
            job["status"] = "done"
            job["progress"] = 100.0
        except Exception as e:
            job["lines"].append(f"Přesun stop selhal: {e}")
            job["status"] = "error"
    else:
        job["status"] = "error"
    job["finished"] = time.time()


def _stems_worker_loop() -> None:
    while True:
        job = None
        with _STEMS_LOCK:
            for jid in _STEMS_QUEUE:
                j = JOBS.get(jid)
                if j and j["status"] == "queued":
                    job = j
                    break
            # Finished entries stay visible for a while, then drop out.
            now = time.time()
            _STEMS_QUEUE[:] = [jid for jid in _STEMS_QUEUE if jid in JOBS and (
                JOBS[jid]["status"] in ("queued", "running")
                or now - JOBS[jid].get("finished", now) < 600)]
        if job is None:
            _STEMS_WAKE.wait(5)
            _STEMS_WAKE.clear()
            continue
        _run_stems_job(job)


_STEMS_AUTOSTART = True                 # tests fill the queue without running it


def _ensure_stems_worker() -> None:
    if not _STEMS_AUTOSTART:
        return
    t = _STEMS_WORKER.get("thread")
    if t is None or not t.is_alive():
        t = threading.Thread(target=_stems_worker_loop, name="stems-queue", daemon=True)
        _STEMS_WORKER["thread"] = t
        t.start()


def _stems_state_for(source: Path) -> Optional[dict]:
    """Where a library file stands: stems ready, waiting, or being made."""
    key = str(source)
    for pos, (jid, job) in enumerate(_stems_active_jobs()):
        if job.get("stems_source") == key:
            return {"state": job["status"], "position": pos + 1,
                    "progress": job.get("progress"), "job_id": jid}
    if _stems_ready(_safe_name(source.stem)):
        return {"state": "ready", "name": _safe_name(source.stem)}
    return None


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
    # Already waiting or running: hand back that job instead of a second one.
    for pos, (jid, job) in enumerate(_stems_active_jobs()):
        if job.get("stems_name") == safe_name:
            return {"job_id": jid, "ready": False, "name": safe_name,
                    "position": pos + 1, "already": True}

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
        "stems_rel": req.rel,
        "progress": None,
        "queued_at": time.time(),
    }
    with _STEMS_LOCK:
        _STEMS_QUEUE.append(job_id)
    _ensure_stems_worker()
    _STEMS_WAKE.set()
    position = next((i + 1 for i, (jid, _) in enumerate(_stems_active_jobs()) if jid == job_id), 1)
    return {"job_id": job_id, "ready": False, "name": safe_name, "position": position}


class StemsPlaylistRequest(BaseModel):
    playlist: str             # the name a playlist was downloaded under
    format: str = "mp3"
    mp3_bitrate: int = 320


@app.post("/api/stems/separate-playlist")
def separate_playlist(req: StemsPlaylistRequest):
    """Every song of a downloaded playlist into the separation queue — what
    "Separuj playlist" runs once the download has finished."""
    folder = MUSIC_ROOT / "Playlists" / _safe_name(req.playlist)
    if not folder.is_dir():
        return JSONResponse({"error": "Playlist není stažený."}, status_code=404)
    queued = ready = 0
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() not in AUDIO_EXTS:
            continue
        res = separate_stems(StemsSeparateRequest(rel=str(f.relative_to(MUSIC_ROOT)), format=req.format,
                                                   mp3_bitrate=req.mp3_bitrate))
        if isinstance(res, dict):
            if res.get("ready"):
                ready += 1
            elif res.get("job_id"):
                queued += 1
    return {"queued": queued, "ready": ready, "folder": str(folder.relative_to(MUSIC_ROOT))}


# ---- "pick save song": a song kept for practice. Whatever it takes to have
# it ready is done here, one step after another, with no window needed: its tab
# looked up (into Pick Tabs), its text looked up, the song downloaded when it
# is not in the library yet, and separated into stems.
_SAVES: dict = {}
_SAVES_LOCK = threading.Lock()
_SAVES_KEEP = 30


class SaveSongRequest(BaseModel):
    title: str
    artists: str = ""
    videoId: str = ""
    url: str = ""
    relPath: str = ""
    stems: str = ""
    duration: float = 0
    bitrate: str = "192k"
    format: str = "mp3"           # of the stems
    mp3_bitrate: int = 320
    cookies_browser: Optional[str] = None
    cookie_file: Optional[str] = None


def _save_library_rel(artist: str, title: str) -> Optional[str]:
    """The library's own copy of a song, by its name."""
    key = lambda t: _re_mod.sub(r"\W+", "", (t or "").lower())
    want_t, want_a = key(title), key(artist.split(",")[0])
    if not want_t:
        return None
    loose = None
    for e in _all_songs_flat().get("entries", []):
        if key(e.get("title") or Path(e["name"]).stem) != want_t:
            continue
        have_a = key(e.get("artist") or "")
        if not want_a or not have_a or want_a in have_a or have_a in want_a:
            return e["rel"]
        loose = loose or e["rel"]
    return None if want_a else loose


def _save_download(req: SaveSongRequest) -> bool:
    """The song from the internet into Songs/, the way the app's own download does it."""
    url = req.url or (f"https://music.youtube.com/watch?v={req.videoId}" if req.videoId
                      else f"{req.artists.split(',')[0]} - {req.title}".strip(" -"))
    job = start_download(DownloadRequest(urls=[url], output_name="Songs", kind="songs", bitrate=req.bitrate,
                                         cookies_browser=req.cookies_browser, cookie_file=req.cookie_file))
    cmd = JOBS.pop(job["job_id"])["cmd"]
    return subprocess.run(cmd, capture_output=True, timeout=900).returncode == 0


def _save_song_run(sid: str, req: SaveSongRequest) -> None:
    st = _SAVES[sid]
    artist = req.artists.split(",")[0].strip()
    try:
        try:
            rec = _match_song(artist, req.title)
            if rec and int(rec["songId"]) not in {t.get("songId") for t in _pick_tabs()}:
                _picktab_upsert(_rec_to_tab(rec, "Oblíbené songy"))
            st["tabs"] = bool(rec)
            st["tuning"] = _tab_tuning(rec)
        except Exception:                                  # noqa: BLE001 — no tab is not a failed save
            st["tabs"] = False
        try:
            got = _lookup_lyrics(req.title, artist, "", req.duration or None, req.videoId)
            st["lyrics"] = bool(got and (got.get("lines") or got.get("plain")))
        except Exception:                                  # noqa: BLE001
            st["lyrics"] = False
        if req.stems and _stems_ready(req.stems):
            st.update(state="ready", stems=req.stems)
            _save_finish(st, req)
            return
        src = _resolve_under_music_root(req.relPath) if req.relPath else None
        rel = req.relPath if src is not None and src.is_file() else _save_library_rel(artist, req.title)
        if not rel:
            st["state"] = "downloading"
            before = {e["rel"] for e in _all_songs_flat().get("entries", [])}
            if not _save_download(req):
                st.update(state="error", error="Stažení se nepovedlo.")
                return
            rel = _save_library_rel(artist, req.title)
            if not rel:                                    # saved under another spelling: the file that is new
                new = [e["rel"] for e in _all_songs_flat().get("entries", []) if e["rel"] not in before]
                rel = new[0] if len(new) == 1 else None
            if not rel:
                st.update(state="error", error="Píseň se stáhla, ale v knihovně jsem ji nenašel.")
                return
        st.update(state="separating", rel=rel)
        res = separate_stems(StemsSeparateRequest(rel=rel, format=req.format, mp3_bitrate=req.mp3_bitrate))
        if not isinstance(res, dict):
            st.update(state="error", error="Rozdělení na stopy se nespustilo.")
            return
        st["stems"] = res["name"]
        job = JOBS.get(res.get("job_id") or "")
        while job is not None and job["status"] in ("queued", "running"):
            st["progress"] = job.get("progress")
            st["position"] = next((i + 1 for i, (jid, _) in enumerate(_stems_active_jobs()) if jid == res["job_id"]), None)
            time.sleep(2)
        if job is not None and job["status"] != "done":
            st.update(state="error", error="Rozdělení na stopy selhalo.")
            return
        st.update(state="ready", progress=None, position=None)
        _save_finish(st, req)
    except Exception as e:                                 # noqa: BLE001
        st.update(state="error", error=str(e) or "Uložení selhalo.")
    finally:
        st["finished"] = time.time()


def _save_finish(st: dict, req: SaveSongRequest) -> None:
    """The stems are there: the tuning is noted beside them, and the text is
    looked up once more the way the mixer will ask for it — by the stems' own
    title, artist and length — so that it is kept for exactly that question."""
    try:
        d = _stems_dir_for(st["stems"])
        if st.get("tuning") and _stems_tuning(d) is None:
            _stems_tuning_keep(d, st["tuning"])
        m = _stems_meta(d)
        dur = next((x for x in (_audio_tags(f).get("duration") for f in (_stem_file(d, s) for s in STEM_NAMES[:2]) if f) if x), None)
        got = _lookup_lyrics(m.get("title") or req.title, m.get("artist") or req.artists.split(",")[0].strip(), m.get("album") or "", dur, req.videoId)
        st["lyrics"] = bool(st.get("lyrics") or (got and (got.get("lines") or got.get("plain"))))
    except Exception:                                      # noqa: BLE001 — the song is ready all the same
        pass


# Many at once — a whole playlist, every favourite: one after another, so the
# computer is not asked to download a hundred songs together.
import collections as _collections
_SAVE_QUEUE: "_collections.deque" = _collections.deque()
_SAVE_WORKER = {"thread": None}


def _save_drain() -> None:
    while True:
        with _SAVES_LOCK:
            if not _SAVE_QUEUE:
                _SAVE_WORKER["thread"] = None
                return
            sid, req = _SAVE_QUEUE.popleft()
        if sid in _SAVES:
            _SAVES[sid]["state"] = "looking"
            _save_song_run(sid, req)


def _save_have(title: str, artists: str, rel: str = "", stems_index=None) -> dict:
    """What there already is of one song: its file, its stems."""
    artist = (artists or "").split(",")[0].strip()
    src = _resolve_under_music_root(rel) if rel else None
    rel = rel if src is not None and src.is_file() else _save_library_rel(artist, title)
    src = _resolve_under_music_root(rel) if rel else None
    ready = bool(src is not None and (_stems_state_for(src) or {}).get("state") == "ready")
    if not ready and stems_index is not None:
        key = lambda t: _re_mod.sub(r"\W+", "", (t or "").lower())
        ready = (key(artist), key(title)) in stems_index or ("", key(title)) in stems_index and not artist
    return {"library": bool(rel), "stems": ready}


def _stems_index() -> set:
    key = lambda t: _re_mod.sub(r"\W+", "", (t or "").lower())
    out = set()
    root = MUSIC_ROOT / "Stems"
    for d in (root.glob("*") if root.is_dir() else []):
        if d.is_dir() and not d.name.startswith(".") and _stems_ready(d.name):
            m = _stems_meta(d)
            out.add((key((m.get("artist") or "").split(",")[0]), key(m.get("title") or d.name)))
    return out


class SaveManyRequest(BaseModel):
    songs: List[SaveSongRequest] = []
    playlists: List[str] = []
    bitrate: str = "192k"
    format: str = "mp3"
    mp3_bitrate: int = 320
    cookies_browser: Optional[str] = None
    cookie_file: Optional[str] = None
    check: bool = False            # only say what is missing; nothing is started


@app.post("/api/practice/save-many")
def practice_save_many(req: SaveManyRequest):
    """Every song given, and every song of the playlists given: what is not
    ready for practice yet is queued — downloaded, separated, its tab and text
    looked up — one after another. With `check`, only counted."""
    songs = list(req.songs[:2000])
    lists = []
    for bid in req.playlists[:50]:
        try:
            d = playlist_detail(bid, track_limit=300)
        except Exception:                                  # noqa: BLE001
            d = None
        tracks = d.get("tracks", []) if isinstance(d, dict) else []
        lists.append({"browseId": bid, "title": d.get("title") if isinstance(d, dict) else None, "count": len(tracks)})
        songs += [SaveSongRequest(title=t.get("title") or "", artists=t.get("artists") or "", videoId=t.get("videoId") or "",
                                  duration=t.get("duration_seconds") or 0) for t in tracks]
    index, seen, missing, ready = _stems_index(), set(), [], 0
    for s in songs:
        key = (s.title.strip().lower(), s.artists.strip().lower())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        if _save_have(s.title, s.artists, s.relPath, index)["stems"] or (s.stems and _stems_ready(s.stems)):
            ready += 1
        else:
            missing.append(s)
    out = {"songs": len(seen), "ready": ready, "missing": len(missing), "playlists": lists, "queued": 0}
    if req.check:
        return out
    with _SAVES_LOCK:
        busy = {st["key"] for st in _SAVES.values() if st["state"] not in ("ready", "error")}
        for s in missing:
            key = (s.title.strip().lower(), s.artists.strip().lower())
            if key in busy:
                continue
            s = s.model_copy(update={"bitrate": req.bitrate, "format": req.format, "mp3_bitrate": req.mp3_bitrate,
                                     "cookies_browser": req.cookies_browser, "cookie_file": req.cookie_file})
            sid = uuid.uuid4().hex[:10]
            _SAVES[sid] = {"id": sid, "key": key, "title": s.title, "artists": s.artists, "state": "waiting", "started": time.time()}
            _SAVE_QUEUE.append((sid, s))
            out["queued"] += 1
        if _SAVE_QUEUE and _SAVE_WORKER["thread"] is None:
            _SAVE_WORKER["thread"] = threading.Thread(target=_save_drain, name="save-many", daemon=True)
            _SAVE_WORKER["thread"].start()
    return out


@app.delete("/api/practice/save-many")
def practice_save_many_stop():
    """Stops what is still waiting; the song being worked on is finished."""
    with _SAVES_LOCK:
        n = len(_SAVE_QUEUE)
        for sid, _ in _SAVE_QUEUE:
            _SAVES.pop(sid, None)
        _SAVE_QUEUE.clear()
    return {"stopped": n}


def _save_public(st: dict) -> dict:
    return {k: st.get(k) for k in ("id", "title", "artists", "state", "stems", "tabs", "lyrics", "error", "progress", "position")}


@app.post("/api/practice/save")
def practice_save(req: SaveSongRequest):
    if not req.title.strip():
        return JSONResponse({"error": "Nic nehraje."}, status_code=400)
    key = (req.title.strip().lower(), req.artists.strip().lower())
    with _SAVES_LOCK:
        for st in _SAVES.values():
            if st["key"] == key and st["state"] not in ("ready", "error"):
                return {**_save_public(st), "already": True}
        sid = uuid.uuid4().hex[:10]
        _SAVES[sid] = {"id": sid, "key": key, "title": req.title, "artists": req.artists, "state": "looking",
                       "started": time.time()}
        for old in [k for k in sorted(_SAVES, key=lambda k: _SAVES[k]["started"]) if _SAVES[k].get("finished")][:-_SAVES_KEEP]:
            del _SAVES[old]
    threading.Thread(target=_save_song_run, args=(sid, req), name="save-song", daemon=True).start()
    return _save_public(_SAVES[sid])


@app.get("/api/practice/saves")
def practice_saves():
    with _SAVES_LOCK:
        rows = sorted(_SAVES.values(), key=lambda s: s["started"])
        # Of a long queue only what is under way and what was done last is told; the rest is counted.
        live = [st for st in rows if st["state"] != "waiting"][-40:]
        return {"saves": [_save_public(st) for st in live],
                "waiting": sum(1 for st in rows if st["state"] == "waiting"),
                "working": sum(1 for st in rows if st["state"] not in ("waiting", "ready", "error")),
                "done": sum(1 for st in rows if st["state"] == "ready"),
                "failed": sum(1 for st in rows if st["state"] == "error")}


@app.get("/api/stems/{name}/backing")
def stems_backing(name: str, without: str = "guitar", part: str = "all", request: Request = None):
    """The band without one of its parts: every stem but `without`, mixed —
    what a player practising that part plays along to. Mixed once by ffmpeg
    and kept in the transcode cache.

    `part` splits it in two: "tonal" is what can follow a change of tuning
    (bass, keys, the rest), "fixed" is what must not (the kit, the voices) —
    and "drums" and "vocals" are those two apart, each with its own fader."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    parts = sorted(f for f in stems_dir.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS
                   and not f.name.startswith(".") and f.stem != without)
    # The two halves of a split guitar go with the guitar they were split from;
    # and practising one half, the whole guitar (which still has that half in
    # it) is out, the other half stays.
    if without == "guitar":
        parts = [f for f in parts if not f.stem.startswith("guitar_")]
    elif without.startswith("guitar_"):
        parts = [f for f in parts if f.stem != "guitar"]
    still = _re_mod.compile(r"drum|kick|snare|hat|cymbal|tom|perc|vocal|voice|vox", _re_mod.I)
    voices = _re_mod.compile(r"vocal|voice|vox", _re_mod.I)
    if part == "tonal":
        parts = [f for f in parts if not still.search(f.stem)]
    elif part == "fixed":
        parts = [f for f in parts if still.search(f.stem)]
    elif part == "drums":
        parts = [f for f in parts if still.search(f.stem) and not voices.search(f.stem)]
    elif part == "vocals":
        parts = [f for f in parts if voices.search(f.stem)]
    if not parts:
        return JSONResponse({"error": "Žádné další stopy."}, status_code=404)
    key = hashlib.sha1(("|".join(f"{f}:{f.stat().st_mtime_ns}" for f in parts)).encode()).hexdigest()[:20]
    TRANSCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = TRANSCODE_CACHE_DIR / f"backing-{key}.m4a"
    if not out.is_file():
        cmd = [ffmpeg_bin(), "-v", "error", "-y"]
        for f in parts:
            cmd += ["-i", str(f)]
        mix = f"amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.97" if len(parts) > 1 else "alimiter=limit=0.97"
        cmd += ["-filter_complex", mix, "-c:a", "aac", "-b:a", "256k", str(out)]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0 or not out.is_file():
            return JSONResponse({"error": "Doprovod se nepodařilo smíchat."}, status_code=500)
    # With ranges, so a player can jump within it (the mini player plays this file as it is).
    resp = _range_file_response(out, request.headers.get("range") if request is not None else None)
    resp.headers["X-Stems"] = ",".join(f.stem for f in parts)
    resp.headers["content-type"] = "audio/mp4"
    return resp


# ---- recording from the audio interface, past the browser. Chrome hands a
# page two channels of an interface however many it has, so the processed
# signal of a modeller on inputs 3 and 4 never reaches it. ffmpeg reads them
# all. The page keeps recording the first input itself (its clock is the one
# the takes are timed by); this records the same input beside the wanted
# ones, and the page finds each take in it by that shared channel.
_CAPTURE: dict = {"proc": None, "path": None, "id": None}
_CAPTURE_LOCK = threading.Lock()
CAPTURE_DIR = Path(tempfile.gettempdir()) / "pick-capture"


def _capture_devices() -> List[str]:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True, timeout=15)
    names, audio = [], False
    for line in r.stderr.splitlines():
        if "audio devices" in line:
            audio = True
        elif audio:
            m = _re_mod.search(r"\]\s+\[\d+\]\s+(.+)$", line)
            if m:
                names.append(m.group(1).strip())
    return names


def _capture_device(label: str) -> Optional[str]:
    """The device the browser calls `label` ("Nano Cortex (1234:abcd)")."""
    hits = [n for n in _capture_devices() if n and n.lower() in (label or "").lower()]
    return max(hits, key=len) if hits else None


def _capture_cmd(device: str, channels: List[int], sync: int, out: Path, seconds: float) -> List[str]:
    keep = [sync] + channels
    pan = f"pan={len(keep)}c|" + "|".join(f"c{k}=c{c}" for k, c in enumerate(keep))
    return [ffmpeg_bin(), "-v", "error", "-y", "-f", "avfoundation", "-i", f":{device}", "-t", str(seconds),
            "-af", pan, "-c:a", "pcm_s24le", str(out)]


def _capture_end() -> Optional[Path]:
    """Stop the running capture; the file it wrote, when it wrote one."""
    with _CAPTURE_LOCK:
        proc, path = _CAPTURE["proc"], _CAPTURE["path"]
        _CAPTURE["proc"] = None
    if proc is not None and proc.poll() is None:
        try:
            proc.communicate(b"q", timeout=5)      # ffmpeg closes the file properly on "q"
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
    return path if path and path.is_file() else None


class CaptureRequest(BaseModel):
    device: str
    channels: List[int]
    sync: int = 0


@app.post("/api/capture/probe")
def capture_probe(req: CaptureRequest):
    """Can these inputs be recorded here? A third of a second, to find out."""
    device = _capture_device(req.device)
    if not device:
        return JSONResponse({"error": "Zařízení se nenašlo."}, status_code=404)
    if any(c < 0 or c > 63 for c in req.channels + [req.sync]) or not req.channels:
        return JSONResponse({"error": "Neplatné kanály."}, status_code=400)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = CAPTURE_DIR / f"probe-{uuid.uuid4().hex[:8]}.wav"
    try:
        # (Opening the device fails now and then for no reason of ours: again.)
        for _ in range(3):
            r = subprocess.run(_capture_cmd(device, req.channels, req.sync, out, 0.3), capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and out.is_file() and out.stat().st_size >= 1000:
                break
        if r.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
            lines = (r.stderr or "").strip().splitlines()
            return JSONResponse({"error": lines[-1][:200] if lines else "Ze zařízení nepřišel žádný zvuk — Pick nejspíš nemá povolený mikrofon.",
                                 "silent": not lines}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Zařízení neodpovídá — nejspíš chybí povolení k mikrofonu."}, status_code=500)
    finally:
        out.unlink(missing_ok=True)
    return {"device": device}


@app.post("/api/capture/start")
def capture_start(req: CaptureRequest):
    device = _capture_device(req.device)
    if not device:
        return JSONResponse({"error": "Zařízení se nenašlo."}, status_code=404)
    _capture_end()
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    for f in CAPTURE_DIR.glob("*.wav"):
        f.unlink(missing_ok=True)
    cid = uuid.uuid4().hex[:12]
    out = CAPTURE_DIR / f"{cid}.wav"
    for _ in range(3):
        proc = subprocess.Popen(_capture_cmd(device, req.channels, req.sync, out, 1800), stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # Running, once the file has sound in it.
        until = time.time() + 5
        while time.time() < until and proc.poll() is None and not (out.is_file() and out.stat().st_size > 4096):
            time.sleep(0.02)
        if proc.poll() is None:
            break
    if proc.poll() is not None or not out.is_file():
        err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()[:200] if proc.poll() is not None else ""
        if proc.poll() is None:
            proc.kill()
        return JSONResponse({"error": err or "Nahrávání se nespustilo."}, status_code=500)
    with _CAPTURE_LOCK:
        _CAPTURE.update(proc=proc, path=out, id=cid)
    return {"id": cid}


@app.post("/api/capture/stop")
def capture_stop():
    """End the capture. `started` is when its first sample was taken, by this
    machine's clock — close enough for the page to know where to look."""
    ended = time.time()
    path = _capture_end()
    if path is None:
        return JSONResponse({"error": "Nic se nenahrávalo."}, status_code=404)
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            dur = w.getnframes() / w.getframerate()
    except (wave.Error, OSError, EOFError):
        return JSONResponse({"error": "Nahrávka se nedá přečíst."}, status_code=500)
    return {"id": _CAPTURE["id"], "duration": dur, "started": ended - dur}


@app.get("/api/capture/{cid}/slice")
def capture_slice(cid: str, start: float = 0.0, dur: float = 30.0):
    """A stretch of the last capture, as a WAV: the shared input first, then
    the wanted ones."""
    path = _CAPTURE["path"]
    if not path or cid != _CAPTURE["id"] or not path.is_file() or _CAPTURE["proc"] is not None:
        return JSONResponse({"error": "Nahrávka už není."}, status_code=404)
    import io
    import wave
    with wave.open(str(path), "rb") as w:
        rate, total = w.getframerate(), w.getnframes()
        a = max(0, min(total, int(start * rate)))
        n = max(0, min(total - a, int(min(dur, 600) * rate)))
        w.setpos(a)
        frames = w.readframes(n)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as o:
            o.setnchannels(w.getnchannels()); o.setsampwidth(w.getsampwidth()); o.setframerate(rate)
            o.writeframes(frames)
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="audio/wav", headers={"X-Start": f"{a / rate:.6f}"})


# ---- riffs: stretches of songs cut out to practise and to loop. Each is an
# audio file (and, when its notes could be read, a MIDI file beside it) in
# the library under Loops/Riffs/<band>/<song>/, so the folders sort them by
# themselves; what a file name cannot hold — tempo, key, tuning, instrument
# — is kept in one index next to them.
RIFFS_REL = "Loops/Riffs"
_RIFFS_LOCK = threading.Lock()


def _riffs_dir() -> Path:
    return MUSIC_ROOT / "Loops" / "Riffs"


def _riffs_load() -> List[dict]:
    try:
        data = json.loads((_riffs_dir() / ".riffs.json").read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict) and r.get("id") and r.get("audio")] if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _riffs_save(riffs: List[dict]) -> None:
    _riffs_dir().mkdir(parents=True, exist_ok=True)
    tmp = _riffs_dir() / ".riffs.json.tmp"
    tmp.write_text(json.dumps(riffs, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_riffs_dir() / ".riffs.json")


def _riff_name(text, fallback: str) -> str:
    """A name fit for a file or a folder."""
    clean = _re_mod.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str(text or ""))
    clean = _re_mod.sub(r"\s+", " ", clean).strip(" .")[:80].strip(" .")
    return clean or fallback


def _riffs_alive() -> List[dict]:
    """The index without what has left the disk (deleted or moved in the
    library, in Finder): a riff is its audio file."""
    with _RIFFS_LOCK:
        riffs = _riffs_load()
        alive = []
        for r in riffs:
            if not (MUSIC_ROOT / r["audio"]).is_file():
                continue
            if r.get("midi") and not (MUSIC_ROOT / r["midi"]).is_file():
                r = {**r, "midi": None}
            alive.append(r)
        if alive != riffs:
            _riffs_save(alive)
    return alive


def _riff_tags(rel: str) -> dict:
    """What the library shows for a riff's audio file: the riff as the title,
    the band, the song — and the rest for its own column."""
    for r in _riffs_load():
        if r.get("audio") == rel:
            return {"title": r.get("riff"), "artist": r.get("artist"), "album": r.get("song"),
                    "riff": {k: r.get(k) for k in ("id", "instrument", "bpm", "key", "tuning", "dur", "midi")}}
    return {}


@app.get("/api/riffs")
def riffs_list():
    low = lambda v: str(v or "").lower()
    return sorted(_riffs_alive(), key=lambda r: (low(r.get("artist")), low(r.get("song")), low(r.get("riff")), low(r.get("instrument"))))


@app.post("/api/riffs")
async def riffs_add(meta: str, audio: UploadFile = File(...), midi: Optional[UploadFile] = File(None)):
    """Keep a riff: its audio, its MIDI when there is one, and what is known
    of it (`meta`, JSON: artist, song, riff, instrument, bpm, key, tuning,
    dur, notes, source)."""
    try:
        m = json.loads(meta)
        assert isinstance(m, dict)
    except (ValueError, AssertionError):
        return JSONResponse({"error": "Neplatný popis riffu."}, status_code=400)
    ext = Path(audio.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        ext = ".wav"
    sound = await audio.read()
    if len(sound) < 1000:
        return JSONResponse({"error": "Prázdný zvuk."}, status_code=400)
    notes = await midi.read() if midi is not None else b""
    if notes and notes[:4] != b"MThd":
        return JSONResponse({"error": "Tohle není MIDI soubor."}, status_code=400)
    artist = _riff_name(m.get("artist"), "Neznámý interpret")
    song = _riff_name(m.get("song"), "Bez názvu")
    riff = _riff_name(m.get("riff"), "Riff")
    instrument = _riff_name(m.get("instrument"), "Kytara")[:30]
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None
    folder = _riffs_dir() / artist / song
    folder.mkdir(parents=True, exist_ok=True)
    base, k = f"{riff} - {instrument}", 1
    while (folder / f"{base}{ext}").exists() or (folder / f"{base}.mid").exists():
        k += 1
        base = f"{riff} - {instrument} ({k})"
    (folder / f"{base}{ext}").write_bytes(sound)
    if notes:
        (folder / f"{base}.mid").write_bytes(notes)
    rel = f"{RIFFS_REL}/{artist}/{song}/{base}"
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    entry = {"id": uuid.uuid4().hex[:10], "artist": artist, "song": song, "riff": riff, "instrument": instrument,
             "bpm": num(m.get("bpm")), "key": str(m.get("key") or "")[:20], "tuning": str(m.get("tuning") or "")[:40],
             "dur": num(m.get("dur")), "bars": num(m.get("bars")), "notes": int(num(m.get("notes")) or 0),
             "audio": rel + ext, "midi": rel + ".mid" if notes else None, "added": time.time(),
             "source": {"song": str(src.get("song") or "")[:200], "stem": str(src.get("stem") or "")[:60],
                        "from": num(src.get("from")), "to": num(src.get("to"))}}
    with _RIFFS_LOCK:
        riffs = _riffs_load()
        riffs.append(entry)
        _riffs_save(riffs)
    return entry


@app.post("/api/riffs/midi")
async def riffs_midi_save(request: Request, name: str = "", rel: str = ""):
    """A MIDI file into the library — notes edited in Steems. Back over the
    riff's own file when `rel` names one, else as Loops/Riffs/MIDI/<name>.mid."""
    data = await request.body()
    if data[:4] != b"MThd" or len(data) > 4_000_000:
        return JSONResponse({"error": "Tohle není MIDI soubor."}, status_code=400)
    target = None
    if rel:
        hit = _resolve_under_music_root(rel)
        if (hit is not None and hit.is_file() and hit.suffix.lower() in MIDI_EXTS
                and _riffs_dir().resolve() in hit.resolve().parents):
            target = hit
    if target is None:
        folder = _riffs_dir() / "MIDI"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (_riff_name(Path(name).stem if name else "", "MIDI") + ".mid")
    replaced = target.exists()
    target.write_bytes(data)
    return {"rel": str(target.resolve().relative_to(MUSIC_ROOT.resolve())), "replaced": replaced}


# ---- a song in the Steems list, heard before it is opened: the file it was
# separated from when the library still has it, its stems mixed otherwise
# (a project of the player's own takes never had one).
_STEMS_ORIGINALS: dict = {"at": 0.0, "by_name": {}}
PREVIEW_REL = ".pick-preview"


def _stems_original(name: str) -> Optional[str]:
    """The library file a stems folder was made from, as a rel path."""
    if time.time() - _STEMS_ORIGINALS["at"] > 120:
        found: dict = {}
        for top in sorted(MUSIC_ROOT.iterdir()) if MUSIC_ROOT.is_dir() else []:
            if not top.is_dir() or top.name.startswith(".") or top.name in ("Stems", "Loops", "NAM", "CAP"):
                continue
            for f in top.rglob("*"):
                if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".") and f.is_file():
                    found.setdefault(_safe_name(f.stem), str(f.relative_to(MUSIC_ROOT)))
        _STEMS_ORIGINALS.update(at=time.time(), by_name=found)
    rel = _STEMS_ORIGINALS["by_name"].get(name)
    return rel if rel and (MUSIC_ROOT / rel).is_file() else None


@app.get("/api/stems/{name}/preview")
def stems_preview(name: str):
    """Something to play for a song in the list: {rel, original}."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    rel = _stems_original(name)
    if rel:
        return {"rel": rel, "original": True}
    mixed = stems_backing(name, without="", part="all")
    if not isinstance(mixed, FileResponse):
        return mixed
    out = MUSIC_ROOT / PREVIEW_REL / f"{name}.m4a"
    src = Path(mixed.path)
    if not out.is_file() or out.stat().st_mtime < src.stat().st_mtime:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
    return {"rel": f"{PREVIEW_REL}/{name}.m4a", "original": False}


# ---- a track's audio as notes: for a piano, a melody, a bass line pulled
# onto a MIDI track. basic-pitch does the listening, in a venv of its own.
MIDI_PYTHON = HOME / ".local" / "venvs" / "pick-midi" / "bin" / "python"


@app.post("/api/stems/{name}/{stem}/notes")
def stem_to_notes(name: str, stem: str):
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    src = _stem_file(stems_dir, stem)
    if src is None:
        return JSONResponse({"error": "Stopa nemá zvukový soubor."}, status_code=404)
    if not MIDI_PYTHON.is_file():
        return JSONResponse({"error": "Převod zvuku na MIDI není nainstalovaný (chybí ~/.local/venvs/pick-midi)."}, status_code=501)
    try:
        r = subprocess.run([str(MIDI_PYTHON), str(APP_DIR / "audio2midi.py"), str(src)], capture_output=True, text=True, timeout=600)
        notes = json.loads(r.stdout)["notes"] if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError, KeyError):
        notes = None
    if notes is None:
        return JSONResponse({"error": "Převod na MIDI se nepodařil."}, status_code=500)
    return {"notes": notes[:20000], "stem": stem}


@app.get("/api/stems/queue")
def stems_queue():
    """Every separation waiting, running, or finished in the last minutes."""
    with _STEMS_LOCK:
        ids = list(_STEMS_QUEUE)
    out, waiting = [], 0
    for jid in ids:
        job = JOBS.get(jid)
        if not job:
            continue
        if job["status"] in ("queued", "running"):
            waiting += 1
        out.append({
            "job_id": jid, "name": job["stems_name"], "rel": job.get("stems_rel"),
            "status": job["status"], "progress": job.get("progress"),
            "position": waiting if job["status"] in ("queued", "running") else None,
        })
    return {"jobs": out, "active": waiting}


# ------------------------------------------------------- what a stem set is
#
# A stem folder's name is whatever the source file was called ("06
# Propaganda", "Nothing Else Matters"), which is a poor thing to sort or
# group by. Its artist / title / album come, in order of trust, from:
#   1. meta.json in the folder with "manual": true — typed by the user;
#   2. meta.json written when the separation finished (the source's tags);
#   3. the tags of a library file with the same name, if there is one;
#   4. a guess from the folder name ("Artist - Title").
# The folder itself is never renamed: saved mixes, tab links and offsets
# all hang off its name.
_STEMS_SOURCE_INDEX: dict = {"at": 0.0, "map": {}}


def _stems_source_index() -> dict:
    """safe name → a library audio file with that name (refreshed each minute)."""
    now = time.time()
    if now - _STEMS_SOURCE_INDEX["at"] < 60 and _STEMS_SOURCE_INDEX.get("root") == str(MUSIC_ROOT):
        return _STEMS_SOURCE_INDEX["map"]
    found: dict = {}
    try:
        for dirpath, dirnames, filenames in os.walk(MUSIC_ROOT):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not (
                Path(dirpath) == MUSIC_ROOT and d == "Stems")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix.lower() in AUDIO_EXTS and not fn.startswith("."):
                    found.setdefault(_safe_name(p.stem), p)
    except OSError:
        pass
    _STEMS_SOURCE_INDEX.update(at=now, map=found, root=str(MUSIC_ROOT))
    return found


def _guess_from_name(name: str) -> dict:
    base = _re_mod.sub(r"^\s*\d{1,3}[\s._-]+", "", name).strip() or name
    if " - " in base:
        artist, title = base.split(" - ", 1)
        return {"artist": artist.strip(), "title": title.strip(), "album": None}
    return {"artist": None, "title": base, "album": None}


def _stems_meta(d: Path) -> dict:
    meta_file = d / "meta.json"
    if meta_file.is_file():
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
            return {"artist": m.get("artist") or None, "title": m.get("title") or None,
                    "album": m.get("album") or None,
                    "source": "manual" if m.get("manual") else "tags"}
        except (OSError, ValueError):
            pass
    src = _stems_source_index().get(d.name)
    if src is not None:
        tags = _audio_tags(src)
        if tags.get("title") or tags.get("artist"):
            return {"artist": tags.get("artist"), "title": tags.get("title") or d.name,
                    "album": tags.get("album"), "source": "tags"}
    return {**_guess_from_name(d.name), "source": "name"}


# ---- the guitar's tuning of a separated song: from its tab (Songsterr lists
# the strings high to low, as MIDI notes), kept in tuning.json beside the
# stems. An empty list is "looked for, none found" — not asked about again.
def _stems_tuning(d: Path):
    try:
        t = json.loads((d / "tuning.json").read_text(encoding="utf-8")).get("tuning")
        return [int(x) for x in t] if isinstance(t, list) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _stems_tuning_keep(d: Path, tuning) -> None:
    try:
        (d / "tuning.json").write_text(json.dumps({"tuning": [int(x) for x in tuning or []][:12]}), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def _tab_tuning(rec) -> list:
    tracks = (rec or {}).get("tracks") or []
    i = _guitar_index(rec) if rec else -1
    return list((tracks[i].get("tuning") or []) if 0 <= i < len(tracks) else [])


_TUNING_JOB = {"running": False, "done": 0, "total": 0}


def _tunings_fill() -> None:
    try:
        root = MUSIC_ROOT / "Stems"
        todo = [d for d in sorted(root.glob("*")) if d.is_dir() and not d.name.startswith(".") and _stems_tuning(d) is None]
        _TUNING_JOB.update(done=0, total=len(todo))
        for d in todo:
            m = _stems_meta(d)
            try:
                rec = _match_song(m.get("artist") or "", m.get("title") or "") if m.get("artist") and m.get("title") else None
                _stems_tuning_keep(d, _tab_tuning(rec))
            except Exception:                              # noqa: BLE001 — the internet; asked again next time
                pass
            _TUNING_JOB["done"] += 1
            time.sleep(0.25)
    finally:
        _TUNING_JOB["running"] = False


@app.get("/api/stems-tunings")
def stems_tunings_state():
    return dict(_TUNING_JOB)


@app.post("/api/stems-tunings/fill")
def stems_tunings_fill():
    """Looks up the tuning of every separated song that has none yet, in the background."""
    if not _TUNING_JOB["running"]:
        _TUNING_JOB.update(running=True, done=0, total=0)
        threading.Thread(target=_tunings_fill, name="stems-tunings", daemon=True).start()
    return dict(_TUNING_JOB)


class StemsTuning(BaseModel):
    tuning: List[int] = []


@app.put("/api/stems/{name}/tuning")
def put_stems_tuning(name: str, body: StemsTuning):
    """The tuning of the tab the song is played with — the page tells it when the tab opens."""
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    if body.tuning:
        _stems_tuning_keep(d, body.tuning)
    return {"name": name, "tuning": _stems_tuning(d)}


class StemsMeta(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None


@app.put("/api/stems/{name}/meta")
def put_stems_meta(name: str, body: StemsMeta):
    """Artist / title / album typed by the user. All three empty = back to
    the automatic ones."""
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    clean = {k: (v or "").strip()[:200] or None for k, v in body.model_dump().items()}
    meta_file = d / "meta.json"
    try:
        if not any(clean.values()):
            meta_file.unlink(missing_ok=True)
        else:
            meta_file.write_text(json.dumps({**clean, "manual": True}, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        return JSONResponse({"error": f"Uložení selhalo: {e}"}, status_code=500)
    return {"name": name, "meta": _stems_meta(d)}


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
        # A project opened from a single file has none of the separated
        # stems at all — that is what it is, not an incomplete separation.
        if not any(s in present for s in STEM_NAMES):
            missing = []
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
            "meta": _stems_meta(d),
            # How long the song is — any stem says it (tags are cached).
            "duration": next((dur for dur in (_audio_tags(f).get("duration")
                              for f in (_stem_file(d, st) for st in present[:2]) if f) if dur), None),
            "labels": _track_labels(d),
            "edited": _edited_stems(d),
            "tuning": _stems_tuning(d),
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

    # A project opened from a single file has no separated stems — its own
    # track(s) are what there is to listen to.
    for stem in ("drums", "other", "bass", "guitar", "piano", "vocals", *_takes_in(stems_dir)):
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


# ------------------------------------------- a clip cut in the mixer, kept
#
# Cut Audio Parts: pieces of tracks saved into the library, each a WAV in
# the CAP folder (lossless — they are usually short and often DI).
CAP_DIR_NAME = "CAP"


@app.post("/api/library/cap")
async def save_cap(request: Request, name: str = ""):
    raw = await request.body()
    if len(raw) < 1024 or raw[:4] != b"RIFF":
        return JSONResponse({"error": "Dílek je prázdný."}, status_code=400)
    if len(raw) > 1500 * 1024 * 1024:
        return JSONResponse({"error": "Dílek je příliš velký."}, status_code=413)
    d = MUSIC_ROOT / CAP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    base = _safe_name(name or "Dilek")
    target, n = d / f"{base}.wav", 2
    while target.exists():
        target = d / f"{base} ({n}).wav"
        n += 1
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return {"rel": target.relative_to(MUSIC_ROOT).as_posix(), "name": target.name}


# Mixer exports land in the library's Export folder, encoded to what the
# export dialog asked for. The page renders the mix and sends it as 32-bit
# float WAV, so a 24-bit or float export keeps its resolution.
EXPORT_DIR_NAME = "Export"
_EXPORT_CODECS = {
    "wav": {16: ["-c:a", "pcm_s16le"], 24: ["-c:a", "pcm_s24le"], 32: ["-c:a", "pcm_f32le"]},
    "flac": {16: ["-c:a", "flac", "-sample_fmt", "s16"], 24: ["-c:a", "flac", "-sample_fmt", "s32"],
             32: ["-c:a", "flac", "-sample_fmt", "s32"]},
    "mp3": {0: ["-c:a", "libmp3lame", "-b:a", "320k"]},
    "ogg": {0: ["-c:a", "libvorbis", "-q:a", "8"]},
}


@app.post("/api/library/export")
async def save_export(request: Request, name: str = "", fmt: str = "wav", rate: int = 44100, bits: int = 16):
    raw = await request.body()
    if len(raw) < 1024 or raw[:4] != b"RIFF":
        return JSONResponse({"error": "Export je prázdný."}, status_code=400)
    if fmt not in _EXPORT_CODECS:
        return JSONResponse({"error": "Neznámý formát."}, status_code=400)
    if rate not in (22050, 32000, 44100, 48000, 88200, 96000):
        rate = 44100
    codec = _EXPORT_CODECS[fmt].get(bits) or _EXPORT_CODECS[fmt].get(0) or _EXPORT_CODECS[fmt][16]
    d = MUSIC_ROOT / EXPORT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    base = _safe_name(name or "Mix")
    target, n = d / f"{base}.{fmt}", 2
    while target.exists():
        target = d / f"{base} ({n}).{fmt}"
        n += 1
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run([ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error", "-i", str(tmp_path),
                               "-ar", str(rate), *codec, str(target)],
                              capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return JSONResponse({"error": f"Export selhal: {exc}"}, status_code=500)
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0 or not target.is_file():
        target.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["neznámá chyba"]
        return JSONResponse({"error": f"Export selhal: {detail[0][:200]}"}, status_code=500)
    return {"rel": target.relative_to(MUSIC_ROOT).as_posix(), "name": target.name,
            "bytes": target.stat().st_size}


@app.post("/api/analyze/beats")
async def analyze_clip_beats(request: Request):
    """Beat grid of a piece of audio (a clip cut in the mixer), with the same
    tracker the stems get. Times are from the start of the clip."""
    raw = await request.body()
    if len(raw) < 1024 or raw[:4] != b"RIFF":
        return JSONResponse({"error": "Dílek je prázdný."}, status_code=400)
    interpreter = _demucs_interpreter()
    if interpreter is None:
        return JSONResponse({"error": "Detektor beatů není dostupný."}, status_code=503)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run([interpreter, str(APP_DIR / "beatgrid.py"), str(tmp_path)],
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return JSONResponse({"error": f"Detekce selhala: {exc}"}, status_code=500)
    finally:
        tmp_path.unlink(missing_ok=True)
    lines = (proc.stdout or "").strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else {"error": "bez výstupu"}
    except json.JSONDecodeError:
        data = {"error": "nečitelný výstup"}
    if "error" in data:
        return JSONResponse({"error": "Tempo se v dílku nepodařilo najít — zkus delší úsek s jasným rytmem."},
                            status_code=422)
    return data


class BeatGridBody(BaseModel):
    beats: List[float]
    downbeats: List[float] = []
    bpm: float
    beats_per_bar: int = 4
    detected_from: Optional[float] = None
    detected_to: Optional[float] = None


@app.put("/api/stems/{name}/beats")
def put_stems_beatgrid(name: str, body: BeatGridBody):
    """A grid set by hand (Detect Tempo on a clip) replaces the detected one."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    beats = sorted(float(b) for b in body.beats if b >= 0)[:20000]
    if len(beats) < 2 or not (20 <= body.bpm <= 300):
        return JSONResponse({"error": "Neplatná mřížka."}, status_code=400)
    data = {"beats": beats, "downbeats": sorted(float(b) for b in body.downbeats if b >= 0)[:5000],
            "bpm": round(body.bpm, 2), "beats_per_bar": max(1, min(12, body.beats_per_bar)),
            "detector": "clip", "tempo_curve": [],
            "detected_from": body.detected_from if body.detected_from is not None else beats[0],
            "detected_to": body.detected_to if body.detected_to is not None else beats[-1],
            "grid_version": BEATGRID_VERSION}
    try:
        (stems_dir / "beats.json").write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"error": f"Uložení selhalo: {exc}"}, status_code=500)
    return data


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


# ---- the singing trainer (sing.py): the tune to hit, the words as sung, and
# how each take went.
import sing as _sing


def _sing_dir(name: str):
    d = _resolve_under_music_root(f"Stems/{name}")
    return d if d is not None and d.is_dir() else None


@app.get("/api/stems/{name}/melody")
def stems_melody(name: str, refresh: bool = False):
    """The notes of the song's vocal line, read off the separated vocals."""
    d = _sing_dir(name)
    if d is None:
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    vocals = _stem_file(d, "vocals")
    if vocals is None:
        return JSONResponse({"error": "Tahle píseň nemá oddělený zpěv."}, status_code=422)
    try:
        return _sing.melody_cached(d, vocals, refresh)
    except Exception as e:
        return JSONResponse({"error": f"Melodii se nepodařilo spočítat ({e})."}, status_code=500)


@app.get("/api/stems/{name}/melody/kept")
def stems_melody_kept(name: str):
    """The same notes, but only if they were worked out before: nothing is
    computed here. The running lyrics ask this way on every song opened."""
    d = _sing_dir(name)
    vocals = _stem_file(d, "vocals") if d is not None else None
    got = _sing.melody_cached(d, vocals, only_cached=True) if vocals is not None else None
    return got or {"notes": []}


@app.post("/api/sing/line")
async def sing_line(request: Request, text: str, lang: str = "auto"):
    """One sung line (16 kHz mono s16le) against the words it should have been."""
    why = _voice.names_available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    pcm = await request.body()
    if len(pcm) < _voice.RATE or len(pcm) > _voice.RATE * 2 * 30 or len(pcm) % 2:
        return JSONResponse({"error": "Neplatný zvuk."}, status_code=400)
    if lang not in ("auto", "en", "cs"):
        lang = "auto"
    try:
        heard = await asyncio.get_running_loop().run_in_executor(None, lambda: _voice.transcribe(pcm, lang=lang, prompt=""))
    except Exception:
        return JSONResponse({"error": "Rozpoznání se nepovedlo."}, status_code=500)
    return _sing.compare(text, heard)


class SingTake(BaseModel):
    pitch: float            # share of the notes hit (0–100)
    near: float = 0.0       # …and of those sung close
    text: Optional[float] = None
    octave: int = 0
    sung: float = 0.0       # seconds of the tune that were sung at all
    lines: List[dict] = []


@app.get("/api/stems/{name}/sing")
def sing_history(name: str):
    d = _sing_dir(name)
    return _sing.history(d) if d is not None else []


@app.post("/api/stems/{name}/sing")
def sing_save(name: str, take: SingTake):
    d = _sing_dir(name)
    if d is None:
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    row = take.model_dump()
    row["lines"] = row["lines"][:400]
    row["at"] = time.time()
    return _sing.add_history(d, row)


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
            data = json.loads(cache.read_text(encoding="utf-8"))
            # Written before a project of the user's own files could be
            # analysed ("chybí stopa …"): worth another go, not a final answer.
            if not (not data.get("sections") and any("chybí stopa" in w for w in data.get("warnings") or [])):
                return data
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
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("sections") or not any("chybí stopa" in w for w in data.get("warnings") or []):
                    return data
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
    # Pieces of a track cut with the scissors: [{src, offset, dur, start}].
    clips: Optional[List[dict]] = None
    # Taken out of the mix with its × (the file stays; it can come back).
    hidden: bool = False
    eq: Optional[List[EqBand]] = None
    # Inserts: {mono, comp: {...}, dbl: {...}} — kept as a plain dict so a
    # new parameter doesn't need a model change (and isn't silently dropped).
    fx: Optional[dict] = None


class MetronomeSettings(BaseModel):
    volume: float = 0.6
    pan: float = 0.0
    muted: bool = False
    voice: str = "beep"
    rate: float = 1.0


class GuitarSettings(BaseModel):
    volume: float = 0.9
    pan: float = 0.0
    # The virtual guide's own level. None for mixes saved before it was
    # kept, so those leave whatever level is already set alone.
    guideVolume: Optional[float] = None
    muted: bool = False
    solo: bool = False
    threshold: float = -100.0
    inputGainDb: float = 0.0
    inputChannel: str = "auto"
    activeChannel: int = 0
    compOn: bool = False
    compThreshold: float = -24.0
    compRatio: float = 4.0
    compAttack: float = 0.02
    compRelease: float = 0.25
    compKnee: float = 6.0
    compMakeup: float = 0.0
    fx: Optional[dict] = None
    deviceId: Optional[str] = None
    ampName: Optional[str] = None
    ampFile: Optional[str] = None
    cabName: Optional[str] = None
    eq: Optional[List[EqBand]] = None
    instrOn: bool = False
    instrId: Optional[str] = None
    instrVolume: float = 0.8
    instrReplace: bool = False


class MixerSession(BaseModel):
    channels: List[MixerChannel] = []
    loop: Optional[dict] = None
    pixelsPerSecond: Optional[float] = None
    tempo: Optional[float] = None
    # The click is a channel like any other — its level, pan and sound are
    # part of the mix, and re-picking them on every open is a chore.
    metronome: Optional[MetronomeSettings] = None
    guitar: Optional[GuitarSettings] = None
    # MIDI tracks: [{stem, label, instrument, notes: [{m, t, d, v}]}] — the
    # notes themselves, played by a sampled instrument in the page.
    midi: Optional[List[dict]] = None


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
    # MIDI: keep only well-formed notes, and not an unbounded number of them.
    tracks = []
    for tr in (data.get("midi") or [])[:32]:
        if not isinstance(tr, dict) or not _re_mod.fullmatch(r"midi-\d{1,4}", str(tr.get("stem", ""))):
            continue
        notes = []
        for n in (tr.get("notes") or [])[:20000]:
            try:
                m, t, d = int(n["m"]), float(n["t"]), float(n["d"])
                v = float(n.get("v", 0.8))
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= m <= 127 and 0 <= t <= 7200 and 0.005 <= d <= 600:
                notes.append({"m": m, "t": round(t, 4), "d": round(d, 4), "v": max(0.05, min(1.0, v))})
        tracks.append({"stem": tr["stem"], "label": str(tr.get("label") or "MIDI")[:80],
                       "instrument": str(tr.get("instrument") or "")[:80], "notes": notes})
    data["midi"] = tracks or None
    gtr = data.get("guitar")
    if gtr:
        gtr["volume"] = max(0.0, min(2.0, float(gtr["volume"])))
        gtr["pan"] = max(-1.0, min(1.0, float(gtr["pan"])))
        gtr["threshold"] = max(-100.0, min(0.0, float(gtr["threshold"])))
        gtr["compThreshold"] = max(-60.0, min(0.0, float(gtr["compThreshold"])))
        gtr["compRatio"] = max(1.0, min(20.0, float(gtr["compRatio"])))
        gtr["compAttack"] = max(0.0, min(0.1, float(gtr["compAttack"])))
        gtr["compRelease"] = max(0.02, min(1.0, float(gtr["compRelease"])))
        if gtr.get("guideVolume") is not None:
            gtr["guideVolume"] = max(0.0, min(1.4, float(gtr["guideVolume"])))
        gtr["instrVolume"] = max(0.0, min(1.0, float(gtr["instrVolume"])))
        gtr["inputGainDb"] = max(-12.0, min(24.0, float(gtr["inputGainDb"])))
        if gtr.get("inputChannel") not in ("auto", "1", "2", "sum"):
            gtr["inputChannel"] = "auto"
        gtr["activeChannel"] = 1 if gtr.get("activeChannel") == 1 else 0
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


# ---------------------------------------------- tracks from any audio file
#
# Any audio file can join a song's mix as one more track, or start a mix of
# its own. It is stored like a recorded take (take-N.mp3, 320k) so it plays
# through the same path as every stem; its own name is kept in tracks.json.
TRACK_LABELS = "tracks.json"
ORIG_DIR = ".orig"


def _track_labels(d: Path) -> dict:
    try:
        data = json.loads((d / TRACK_LABELS).read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(k, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def _set_track_label(d: Path, stem: str, label: Optional[str]) -> None:
    labels = _track_labels(d)
    if label:
        labels[stem] = label[:120]
    else:
        labels.pop(stem, None)
    try:
        (d / TRACK_LABELS).write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _edited_stems(d: Path) -> list:
    o = d / ORIG_DIR
    if not o.is_dir():
        return []
    return sorted({p.stem for p in o.iterdir() if p.suffix.lower() in STEM_READ_EXTS})


def _encode_to_stem(src: Path, target: Path) -> Optional[str]:
    """Any audio ffmpeg can read → 320k MP3 at target. Returns an error or None."""
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error", "-i", str(src),
             "-vn", "-codec:a", "libmp3lame", "-b:a", "320k", str(target)],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Převod selhal: {exc}"
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1:] or ["neznámá chyba"]
        return f"Převod selhal: {detail[0][:200]}"
    return None


def _add_track_from(stems_dir: Path, src: Path, label: str):
    take = _next_take_name(stems_dir)
    err = _encode_to_stem(src, stems_dir / f"{take}.mp3")
    if err:
        return JSONResponse({"error": err}, status_code=400)
    # Sections/chords found from fewer tracks (or none) are redone next time.
    cache = stems_dir / "analysis.json"
    if cache.is_file() and _stem_file(stems_dir, "drums") is None:
        cache.unlink(missing_ok=True)
    _set_track_label(stems_dir, take, label)
    return {"stem": take, "label": label, "takes": _takes_in(stems_dir)}


async def _upload_to_temp(upload: UploadFile) -> Path:
    data = await upload.read()
    if len(data) < 1024:
        raise ValueError("Soubor je prázdný.")
    if len(data) > 800 * 1024 * 1024:
        raise ValueError("Soubor je příliš velký.")
    suffix = Path(upload.filename or "").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        return Path(tmp.name)


class LibraryFileRequest(BaseModel):
    rel: str
    name: str = ""          # project name (several files opened as one project)


@app.post("/api/stems/{name}/tracks/upload")
async def add_track_upload(name: str, file: UploadFile = File(...)):
    """A file from the computer becomes a new track of this song."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        return _add_track_from(stems_dir, tmp, Path(file.filename or "Stopa").stem)
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/stems/{name}/tracks/library")
def add_track_library(name: str, req: LibraryFileRequest):
    """A file from the library becomes a new track of this song."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    src = _resolve_under_music_root(req.rel)
    if src is None or not src.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _add_track_from(stems_dir, src, src.stem)


def _new_project_dir(base: str) -> Path:
    root = MUSIC_ROOT / "Stems"
    root.mkdir(parents=True, exist_ok=True)
    name = _safe_name(base)
    d, n = root / name, 2
    while d.exists():
        d = root / f"{name} ({n})"
        n += 1
    d.mkdir()
    return d


def _new_project_from(src: Path, label: str, project: str = ""):
    d = _new_project_dir(project or label)
    res = _add_track_from(d, src, label)
    if isinstance(res, JSONResponse):
        shutil.rmtree(d, ignore_errors=True)
        return res
    tags = _audio_tags(src) if src.is_file() else {}
    try:
        (d / "meta.json").write_text(json.dumps({
            "artist": None if project else tags.get("artist"), "title": project or tags.get("title") or label,
            "album": tags.get("album")}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return {"name": d.name, **res}


@app.post("/api/stems/new/library")
def new_project_library(req: LibraryFileRequest):
    """Opens a library file in the mixer as a project of its own."""
    src = _resolve_under_music_root(req.rel)
    if src is None or not src.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _new_project_from(src, src.stem, (req.name or "").strip()[:120])


@app.post("/api/stems/new/upload")
async def new_project_upload(file: UploadFile = File(...), name: str = ""):
    """A file from the computer as a project of its own; `name` names the
    project (a looper), otherwise the file does."""
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        return _new_project_from(tmp, Path(file.filename or "Projekt").stem, name)
    finally:
        tmp.unlink(missing_ok=True)


@app.put("/api/stems/{name}/{stem}/audio")
async def save_edited_stem(name: str, stem: str, request: Request):
    """A track edited in the mixer (cut, paste, fades…), sent back as WAV.
    The first edit keeps the original in .orig/ so it can be restored."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    if not _re_mod.fullmatch(r"[A-Za-z0-9_-]{1,40}", stem):
        return JSONResponse({"error": "Neplatná stopa."}, status_code=400)
    current = _stem_file(stems_dir, stem)
    if current is None:
        return JSONResponse({"error": "Stopa nenalezena."}, status_code=404)
    raw = await request.body()
    if len(raw) < 44:
        return JSONResponse({"error": "Prázdná stopa."}, status_code=400)
    if len(raw) > 1500 * 1024 * 1024:
        return JSONResponse({"error": "Stopa je příliš velká."}, status_code=413)
    orig = stems_dir / ORIG_DIR
    if stem not in _edited_stems(stems_dir):
        orig.mkdir(exist_ok=True)
        shutil.copy2(current, orig / current.name)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    target = stems_dir / f"{stem}.mp3"
    staged = stems_dir / f".{stem}.saving.mp3"
    try:
        err = _encode_to_stem(tmp_path, staged)
    finally:
        tmp_path.unlink(missing_ok=True)
    if err:
        return JSONResponse({"error": err}, status_code=500)
    os.replace(staged, target)
    # Older WAV/FLAC copies of the same stem would shadow nothing (MP3 wins)
    # but would confuse a restore; the original is safe in .orig/.
    for ext in STEM_READ_EXTS:
        other = stems_dir / f"{stem}{ext}"
        if other != target and other.exists():
            other.unlink()
    return {"stem": stem, "bytes": target.stat().st_size, "edited": _edited_stems(stems_dir)}


@app.post("/api/stems/{name}/{stem}/restore")
def restore_stem(name: str, stem: str):
    """Back to the track as it was before the first edit."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    orig = stems_dir / ORIG_DIR
    backup = next((orig / f"{stem}{ext}" for ext in STEM_READ_EXTS if (orig / f"{stem}{ext}").is_file()), None)
    if backup is None:
        return JSONResponse({"error": "Původní verze není uložená."}, status_code=404)
    for ext in STEM_READ_EXTS:
        (stems_dir / f"{stem}{ext}").unlink(missing_ok=True)
    shutil.move(str(backup), str(stems_dir / backup.name))
    try:
        orig.rmdir()
    except OSError:
        pass
    return {"stem": stem, "edited": _edited_stems(stems_dir)}


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
    _set_track_label(stems_dir, take, None)
    for ext in STEM_READ_EXTS:
        (stems_dir / ORIG_DIR / f"{take}{ext}").unlink(missing_ok=True)
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

    # Hidden name until both parts are done (see import_guitar_parts): a split
    # cut off halfway must not leave one part looking like a finished import.
    target = stems_dir / f".{dest_stem}.incoming.mp3"
    suffix = Path(upload.filename or "").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        # In a worker thread: the whole server used to stand still while
        # ffmpeg encoded a five-minute part.
        proc = await asyncio.to_thread(
            subprocess.run,
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


def _commit_incoming(stems_dir: Path):
    """Both parts converted: they take their real names together."""
    for part in GUITAR_PARTS:
        tmp = stems_dir / f".{part}.incoming.mp3"
        if tmp.is_file():
            os.replace(tmp, stems_dir / f"{part}.mp3")


def _drop_incoming(stems_dir: Path):
    for part in GUITAR_PARTS:
        (stems_dir / f".{part}.incoming.mp3").unlink(missing_ok=True)


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
        _drop_incoming(stems_dir)
        return JSONResponse({"error": " / ".join(errors)}, status_code=400)
    _commit_incoming(stems_dir)
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


# ------------------------------------------------- Moises inbox (solo/rhythm)
#
# Nothing local splits a guitar into lead and rhythm; Moises does. Uploading
# and downloading stays in Moises (no API in the consumer app, and driving its
# site would break its terms) — everything around it is automated here:
#   * the songs without a split are copied (their original audio) into
#     <inbox>/_k_nahrani, ready to be dragged into Moises in one go;
#   * whatever comes back is dropped into the inbox (single files, a folder
#     per song, or the ZIP as downloaded). Files are recognised by the words in
#     their names (lead/solo → solo, rhythm → rhythm), the song by the folder
#     / ZIP / file name, matched against the Steems songs;
#   * a confident match with both parts is imported (the existing
#     guitar_solo / guitar_rhythm stems) — by hand from the panel, or by
#     itself in the background — and the files move to <inbox>/_hotovo.
import unicodedata as _ud
import zipfile as _zipfile

_MOISES_EVENTS: list = []          # [{t, song, label}] — recent automatic imports
_MOISES_LOCK = threading.Lock()
_MOISES_SKIP = {"_hotovo", "_k_nahrani"}
_SOLO_WORDS = ("lead", "solo", "sólo", "solová", "solova", "sólová")
_RHYTHM_WORDS = ("rhythm", "rytm", "rytmus", "rytmická", "rytmicka", "rhytm")
_ROLE_NOISE = _re_mod.compile(
    r"\b(lead|solo|sólo|sólová|solová|solova|rhythm|rhytm|rytmus|rytmická|rytmicka|rytm|"
    r"electric|acoustic|guitars?|kytara|kytary|stem|stems|track|moises|hi-?fi)\b", _re_mod.I)


def _moises_inbox() -> Path:
    p = _read_config().get("moises_inbox")
    return Path(p).expanduser() if p else MUSIC_ROOT / "Moises"


def _moises_role(name: str):
    n = name.lower()
    if any(w in n for w in _RHYTHM_WORDS):
        return "rhythm"
    if any(w in n for w in _SOLO_WORDS):
        return "solo"
    return None


def _norm_words(s: str) -> list:
    s = _ud.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = _re_mod.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)              # [id], (Remastered)
    s = _re_mod.sub(r"\b(official|video|audio|remaster(ed)?|lyrics?|hd|hq|live|feat|ft)\b", " ", s)
    s = _re_mod.sub(r"^\s*\d{1,3}[\s._-]+", "", s)                # leading track number
    return [w for w in _re_mod.split(r"[^a-z0-9]+", s) if w]


def _song_label(path: Path, inbox: Path) -> str:
    """Which song a file belongs to: its folder under the inbox, or else its
    own name without the part words."""
    rel = path.relative_to(inbox)
    if len(rel.parts) > 1:
        return rel.parts[0]
    base = _ROLE_NOISE.sub(" ", path.stem)
    base = _re_mod.sub(r"[\s_\-–—.]+$|^[\s_\-–—.]+", "", _re_mod.sub(r"\s{2,}", " ", base))
    base = _re_mod.sub(r"\s*[-–—_]\s*$", "", base).strip()
    return base or path.stem


def _moises_match(label: str) -> list:
    """Steems songs ranked by how well their name fits the label."""
    want = set(_norm_words(label))
    if not want:
        return []
    root = MUSIC_ROOT / "Stems"
    out = []
    try:
        dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        dirs = []
    for d in dirs:
        meta = _stems_meta(d)
        best = 0.0
        for cand in (d.name, f"{meta.get('artist') or ''} {meta.get('title') or ''}", meta.get("title") or ""):
            have = set(_norm_words(cand))
            if not have:
                continue
            inter = len(want & have)
            # how much of the shorter name is in the other one
            score = inter / min(len(want), len(have)) * (0.6 + 0.4 * inter / max(len(want), len(have)))
            best = max(best, score)
        if best > 0.2:
            out.append({"song": d.name, "score": round(best, 3)})
    out.sort(key=lambda x: -x["score"])
    return out


def _moises_unzip(inbox: Path):
    for z in list(inbox.glob("*.zip")):
        dest = inbox / z.stem
        try:
            with _zipfile.ZipFile(z) as zf:
                for m in zf.infolist():
                    if m.is_dir() or m.filename.startswith("__MACOSX") or Path(m.filename).name.startswith("."):
                        continue
                    if Path(m.filename).suffix.lower() not in AUDIO_EXTS:
                        continue
                    dest.mkdir(parents=True, exist_ok=True)
                    target = _unique_dest(dest, Path(m.filename).name)
                    with zf.open(m) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
            done = inbox / "_hotovo"
            done.mkdir(exist_ok=True)
            shutil.move(str(z), str(_unique_dest(done, z.name)))
        except (OSError, _zipfile.BadZipFile):
            continue


def _moises_scan() -> list:
    inbox = _moises_inbox()
    if not inbox.is_dir():
        return []
    _moises_unzip(inbox)
    groups: dict = {}
    now = time.time()
    for dirpath, dirnames, filenames in os.walk(inbox):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _MOISES_SKIP]
        for fn in filenames:
            p = Path(dirpath) / fn
            if fn.startswith(".") or p.suffix.lower() not in AUDIO_EXTS:
                continue
            role = _moises_role(fn)
            if not role:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            label = _song_label(p, inbox)
            g = groups.setdefault(label, {"label": label, "solo": None, "rhythm": None, "settled": True})
            g[role] = str(p)
            if now - st.st_mtime < 8:          # still being written / downloaded
                g["settled"] = False
    out = []
    for g in groups.values():
        cands = _moises_match(g["label"])
        best = cands[0] if cands else None
        second = cands[1]["score"] if len(cands) > 1 else 0
        g["candidates"] = cands[:5]
        g["match"] = best["song"] if best else None
        g["confident"] = bool(best and best["score"] >= 0.75 and best["score"] - second >= 0.15)
        g["complete"] = bool(g["solo"] and g["rhythm"])
        out.append(g)
    out.sort(key=lambda g: g["label"].lower())
    return out


def _convert_part(stems_dir: Path, dest_stem: str, src: Path) -> dict:
    target = stems_dir / f".{dest_stem}.incoming.mp3"
    try:
        proc = subprocess.run([ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error", "-i", str(src),
                               "-codec:a", "libmp3lame", "-b:a", "320k", str(target)],
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{dest_stem}: převod selhal ({exc})"}
    if proc.returncode != 0 or not target.is_file() or target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        return {"error": f"{dest_stem}: převod selhal"}
    return {"stem": dest_stem}


def _moises_import(group: dict, song: str) -> dict:
    stems_dir = _resolve_under_music_root(f"Stems/{song}")
    if stems_dir is None or not stems_dir.is_dir():
        return {"error": "Píseň ve Steems nenalezena."}
    if not (group.get("solo") and group.get("rhythm")):
        return {"error": "Chybí jedna z částí (sólo nebo rytmus)."}
    with _MOISES_LOCK:
        res = [_convert_part(stems_dir, "guitar_solo", Path(group["solo"])),
               _convert_part(stems_dir, "guitar_rhythm", Path(group["rhythm"]))]
        errors = [r["error"] for r in res if "error" in r]
        if errors:
            _drop_incoming(stems_dir)
            return {"error": " / ".join(errors)}
        _commit_incoming(stems_dir)
        # Done with: out of the way, into _hotovo (the whole song folder if it had one).
        inbox = _moises_inbox()
        done = inbox / "_hotovo"
        done.mkdir(parents=True, exist_ok=True)
        for key in ("solo", "rhythm"):
            p = Path(group[key])
            try:
                rel = p.relative_to(inbox)
            except ValueError:
                continue
            if len(rel.parts) > 1:
                folder = inbox / rel.parts[0]
                if folder.is_dir():
                    shutil.move(str(folder), str(_unique_dest(done, folder.name)))
            elif p.is_file():
                shutil.move(str(p), str(_unique_dest(done, p.name)))
    return {"song": song, "guitar_parts": _guitar_parts_in(stems_dir)}


def _moises_auto_tick():
    if not _read_config().get("moises_auto"):
        return
    for g in _moises_scan():
        if g["complete"] and g["confident"] and g["settled"]:
            r = _moises_import(g, g["match"])
            if "error" not in r:
                _MOISES_EVENTS.append({"t": time.time(), "song": g["match"], "label": g["label"]})
                del _MOISES_EVENTS[:-30]


def _moises_watch():
    while True:
        time.sleep(15)
        try:
            _moises_auto_tick()
        except Exception:                                    # noqa: BLE001 — never kill the watcher
            pass


@app.on_event("startup")
def _moises_start():
    if os.environ.get("SPOTDL_NO_WATCHERS"):
        return
    threading.Thread(target=_moises_watch, daemon=True, name="moises-inbox").start()


@app.get("/api/moises")
def moises_status():
    inbox = _moises_inbox()
    root = MUSIC_ROOT / "Stems"
    todo = []
    try:
        for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if _guitar_parts_in(d) or _stem_file(d, "guitar") is None:
                continue
            meta = _stems_meta(d)
            todo.append({"song": d.name, "artist": meta.get("artist"), "title": meta.get("title"),
                         "has_source": True,
                         "from_stems": _stems_source_index().get(d.name) is None})
    except OSError:
        pass
    return {"folder": str(inbox), "exists": inbox.is_dir(), "auto": bool(_read_config().get("moises_auto")),
            "groups": _moises_scan(), "todo": todo, "events": _MOISES_EVENTS[-10:]}


class MoisesSettings(BaseModel):
    folder: Optional[str] = None
    auto: Optional[bool] = None
    pick: bool = False


@app.post("/api/moises/settings")
def moises_settings(body: MoisesSettings):
    patch = {}
    if body.pick:
        try:
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Složka pro soubory z Moises")'],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                patch["moises_inbox"] = r.stdout.strip().rstrip("/")
        except (OSError, subprocess.SubprocessError):
            pass
    elif body.folder:
        patch["moises_inbox"] = str(Path(body.folder).expanduser())
    if body.auto is not None:
        patch["moises_auto"] = bool(body.auto)
    if patch:
        _write_config(patch)
    inbox = _moises_inbox()
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return {"folder": str(inbox), "auto": bool(_read_config().get("moises_auto"))}


class MoisesImport(BaseModel):
    label: str
    song: str


@app.post("/api/moises/import")
def moises_import(body: MoisesImport):
    g = next((x for x in _moises_scan() if x["label"] == body.label), None)
    if g is None:
        return JSONResponse({"error": "Soubory už ve složce nejsou."}, status_code=404)
    r = _moises_import(g, body.song)
    if "error" in r:
        return JSONResponse(r, status_code=400)
    return r


class MoisesExport(BaseModel):
    songs: List[str]


@app.post("/api/moises/export")
def moises_export(body: MoisesExport):
    """The songs' original audio, copied into <inbox>/_k_nahrani — to drag into
    Moises all at once. The folder opens in Finder."""
    out_dir = _moises_inbox() / "_k_nahrani"
    out_dir.mkdir(parents=True, exist_ok=True)
    copied, mixed, missing = [], [], []
    index = _stems_source_index()
    for song in body.songs[:500]:
        src = index.get(song)
        if src is not None and src.is_file():
            dest = out_dir / f"{song}{src.suffix.lower()}"
            if not dest.exists():
                shutil.copy2(src, dest)
            copied.append(song)
        elif _mix_stems_to(song, out_dir / f"{song}.mp3"):
            mixed.append(song)
        else:
            missing.append(song)
    subprocess.run(["open", str(out_dir)], check=False)
    return {"folder": str(out_dir), "copied": copied + mixed, "mixed": mixed, "missing": missing}


def _mix_stems_to(song: str, dest: Path) -> bool:
    """No original file any more: the separated stems summed back into the
    whole song (what Moises needs to split the guitar from)."""
    if dest.exists():
        return True
    d = _resolve_under_music_root(f"Stems/{song}")
    if d is None or not d.is_dir():
        return False
    files = [f for f in (_stem_file(d, n) for n in STEM_NAMES) if f is not None]
    if not files:
        return False
    cmd = [ffmpeg_bin(), "-y", "-nostdin", "-loglevel", "error"]
    for f in files:
        cmd += ["-i", str(f)]
    cmd += ["-filter_complex", f"amix=inputs={len(files)}:duration=longest:normalize=0",
            "-codec:a", "libmp3lame", "-b:a", "320k", str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        return False
    return True


@app.post("/api/moises/open")
def moises_open():
    inbox = _moises_inbox()
    inbox.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(inbox)], check=False)
    return {"ok": True}


# ------------------------------------------------- a stem set's picture
def _embedded_cover(path: Path):
    """(bytes, mime) of the cover art inside an audio file, or None."""
    if path.suffix.lower() == ".mp3":
        try:
            from mutagen.id3 import ID3
            pics = ID3(str(path)).getall("APIC")
            if pics:
                front = next((p for p in pics if getattr(p, "type", None) == 3), pics[0])
                return front.data, front.mime or "image/jpeg"
        except Exception:
            pass
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(str(path))
    except Exception:
        return None
    if audio is None:
        return None
    try:
        tags = audio.tags
        if tags is not None and hasattr(tags, "getall"):           # MP3 (ID3)
            pics = tags.getall("APIC")
            if pics:
                front = next((p for p in pics if getattr(p, "type", None) == 3), pics[0])
                return front.data, front.mime or "image/jpeg"
        pics = getattr(audio, "pictures", None)                     # FLAC
        if pics:
            return pics[0].data, pics[0].mime or "image/jpeg"
        if tags is not None and "covr" in tags:                     # M4A
            data = bytes(tags["covr"][0])
            return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    except Exception:
        return None
    return None


def _stems_cover_file(d: Path):
    src = _stems_source_index().get(d.name)
    if src is not None and src.is_file():
        cov = _embedded_cover(src)
        if cov and len(cov[0]) > 512:
            return cov
    return None


@app.get("/api/stems/{name}/art")
def stems_art(name: str):
    """The picture for a separated song. The cover in its source file wins;
    otherwise the album's cover found by artist + album — so every song of an
    album shows the same one — then the song by artist + title, then the
    artist. Lookups are cached by artist|album, not by folder."""
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"url": None}, status_code=404)
    if _stems_cover_file(d):
        return {"url": f"/api/stems/{quote(name)}/art/image", "source": "file"}
    m = _stems_meta(d)
    artist, title, album = (m.get("artist") or "").strip(), (m.get("title") or "").strip(), (m.get("album") or "").strip()
    cache = _load_thumb_cache()
    key = "stemart:" + "|".join([artist.lower(), album.lower() or ("song:" + title.lower())])
    hit, cached = _thumb_get(cache, key)
    if hit:
        return {"url": cached, "source": "cache"}

    def same_artist(hit):
        names = " ".join(a.get("name", "") for a in (hit.get("artists") or [])).lower()
        return not artist or artist.lower() in names or names in artist.lower()

    attempts = []
    if artist and album:
        attempts.append((f"{artist} {album}", "albums"))
    if title:
        attempts.append((f"{artist} {title}".strip(), "songs"))
    if artist:
        attempts.append((artist, "artists"))
    url = None
    for query, filt in attempts:
        try:
            hits = yt().search(query, filter=filt, limit=5) or []
        except Exception:
            continue
        pick = next((h for h in hits if filt == "artists" or same_artist(h)), None)
        if pick:
            url = _best_thumb(pick.get("thumbnails"))
            if url:
                # Song results only carry a 120 px thumbnail; the same image
                # is served at any size by asking for it.
                url = _re_mod.sub(r"=w\d+-h\d+", "=w544-h544", url)
                break
    _thumb_put(cache, key, url)
    _save_thumb_cache()
    return {"url": url, "source": "search"}


@app.get("/api/stems/{name}/art/image")
def stems_art_image(name: str):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    cov = _stems_cover_file(d)
    if not cov:
        return JSONResponse({"error": "Bez obrázku."}, status_code=404)
    from fastapi.responses import Response
    return Response(cov[0], media_type=cov[1], headers={"Cache-Control": "public, max-age=86400"})


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


def _lrclib_diff(r: dict, duration) -> float:
    if not duration or not r.get("duration"):
        return 999.0
    return abs(float(r["duration"]) - float(duration))


def _lrclib_to_data(best: dict, duration) -> dict:
    data = {
        "synced": False,
        "lines": None,
        "plain": best.get("plainLyrics"),
        "source": "LRCLib",
        "duration_diff": None,
    }
    d = _lrclib_diff(best, duration)
    if d < 999:
        data["duration_diff"] = round(d, 1)
    if best.get("syncedLyrics"):
        parsed = _parse_lrc(best["syncedLyrics"])
        if parsed:
            data["synced"] = True
            data["lines"] = parsed
    return data


def _fetch_lrclib(track_name: str, artist_name: str, album_name: str = None, duration: float = None) -> Optional[dict]:
    """Fetch synced lyrics from LRCLib API.

    Returns dict with keys: synced (bool), lines (list or None), plain (str or None),
    source (str), duration_diff (seconds between the file and the picked version).

    One song usually has several versions on LRCLib (album, single, radio edit,
    live) whose timings differ, so which one gets picked decides whether the
    lines sit on the singing. A synced version whose length matches the file
    wins over anything else; a plain-only one never beats a synced one.
    """
    dur_key = int(round(duration)) if duration else 0
    cache_key = f"{track_name.lower()}|{artist_name.lower()}|{dur_key}"
    cached = _LYRICS_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached["data"]

    headers = {"User-Agent": "Pick/1.0"}
    try:
        data = None
        # LRCLib's own exact lookup (matches length within ±2 s) when the album
        # and length are both known — the closest thing to "this very version".
        if duration and album_name:
            g = httpx.get("https://lrclib.net/api/get", headers=headers, timeout=10.0, params={
                "track_name": track_name, "artist_name": artist_name,
                "album_name": album_name, "duration": int(round(duration)),
            })
            if g.status_code == 200:
                gj = g.json()
                if isinstance(gj, dict) and gj.get("syncedLyrics") and not gj.get("instrumental"):
                    data = _lrclib_to_data(gj, duration)

        if data is None:
            params = {"track_name": track_name, "artist_name": artist_name}
            if album_name:
                params["album_name"] = album_name
            req = httpx.get("https://lrclib.net/api/search", params=params, headers=headers, timeout=15.0)
            if req.status_code != 200:
                return None
            results = req.json()
            if not results or not isinstance(results, list):
                return None
            valid = [
                r for r in results
                if isinstance(r, dict) and (r.get("syncedLyrics") or r.get("plainLyrics"))
                and not r.get("instrumental")
            ]
            if not valid:
                return None

            synced = [r for r in valid if r.get("syncedLyrics")]
            pool = synced or valid
            want_album = (album_name or "").strip().lower()

            def rank(r):
                album_miss = 0 if want_album and (r.get("albumName") or "").strip().lower() == want_album else 1
                return (_lrclib_diff(r, duration), album_miss)

            data = _lrclib_to_data(min(pool, key=rank), duration)

        _LYRICS_CACHE[cache_key] = {"data": data, "expires": time.time() + _LYRICS_CACHE_TTL}
        return data
    except Exception:
        return None


# YouTube titles/artists carry noise LRCLib's own catalogue entries almost
# never do — "(feat. X)", "- Live", "(Official Video)" — so the exact string
# that plays fine as a video title is often the reason a search for it comes
# back empty. Only a conservative, clearly-noise set is stripped (not any
# parenthetical — "(Sittin' On) The Dock of the Bay" has to survive this).
_RE_LYRICS_FEAT = _re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+[^)\]]*[\)\]]"
    r"|\s+[-–]\s*(?:feat\.?|ft\.?|featuring)\s+.+$"
    r"|\s+(?:feat\.?|ft\.?|featuring)\s+.+$",
    _re.I,
)
_RE_LYRICS_TAGS = _re.compile(
    r"\s*[\(\[]\s*(?:official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|"
    r"remaster(?:ed)?(?:\s+\d{4})?|radio\s+edit|explicit|clean|visualizer)\s*[\)\]]"
    r"|\s+[-–]\s*(?:live|remaster(?:ed)?(?:\s+\d{4})?|radio\s+edit)\s*$",
    _re.I,
)


def _clean_lyrics_text(s: str) -> str:
    if not s:
        return s
    cleaned = _RE_LYRICS_FEAT.sub("", s)
    cleaned = _RE_LYRICS_TAGS.sub("", cleaned)
    return cleaned.strip()


def _lookup_lyrics(title: str, artist: str, album: str, duration, video_id: str = "") -> dict:
    """Tries the caller's title/artist as given, then a noise-stripped
    version (only if that's actually different — no point repeating an
    identical query), then YouTube Music's own (unsynced) lyrics as a last
    resort. Never trusts the cleaned version over a raw one that already
    worked — this only ever adds an attempt, it doesn't replace the first.
    """
    kept = _lyrics_kept(title, artist, duration)
    if kept:
        return kept
    result = _lookup_lyrics_online(title, artist, album, duration, video_id)
    if result.get("lines") or result.get("plain"):
        _lyrics_keep(title, artist, duration, result)
    return result


def _lyrics_file(title: str, artist: str, duration) -> Path:
    """Where a text that was found once is kept — beside the app's settings, so
    a song saved for practice has its text there without the internet."""
    key = f"{(title or '').strip().lower()}|{(artist or '').strip().lower()}|{int(round(duration)) if duration else 0}"
    return CONFIG_PATH.parent / "lyrics" / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json")


def _lyrics_kept(title: str, artist: str, duration) -> Optional[dict]:
    try:
        got = json.loads(_lyrics_file(title, artist, duration).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) and (got.get("lines") or got.get("plain")) else None
    except (OSError, ValueError):
        return None


def _lyrics_keep(title: str, artist: str, duration, result: dict) -> None:
    try:
        f = _lyrics_file(title, artist, duration)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except (OSError, TypeError, ValueError):
        pass


def _lookup_lyrics_online(title: str, artist: str, album: str, duration, video_id: str = "") -> dict:
    result = _fetch_lrclib(title, artist, album, duration)
    if not result or not (result.get("lines") or result.get("plain")):
        clean_title, clean_artist = _clean_lyrics_text(title), _clean_lyrics_text(artist)
        if clean_title != title or clean_artist != artist:
            result = _fetch_lrclib(clean_title, clean_artist, album, duration) or result
    if not result or not (result.get("lines") or result.get("plain")):
        result = _fetch_ytmusic_lyrics(title, artist, video_id) or result
    return result or {"synced": False, "lines": None, "plain": None, "source": None}


def _split_lyrics_query(q: str, artist: str) -> tuple:
    """(title, artist) from a raw q plus whatever artist the caller already
    knows.

    The main player always sends both — q as "{artist} - {title}" *and*
    artist separately (see LyricsView.showForTrack) — so q still has the
    artist baked into it as a prefix even though it's redundant. LRCLib's
    fuzzy search shrugs that off on its own most of the time, but stacked
    with any noise _clean_lyrics_text strips (a "(feat. X)" tag, say) it was
    sometimes enough of a mismatch to come back empty. Stripping a leading
    "{artist} - " from q whenever one is already known removes that
    redundancy before it can compound with anything else.
    """
    title = q
    if artist and q.lower().startswith(artist.lower() + " - "):
        title = q[len(artist) + 3:].strip()
    elif not artist and " - " in q:
        parts = q.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
    return title, artist


@app.get("/api/lyrics/search")
def search_lyrics(q: str = "", artist: str = "", album: str = "", duration: float = None,
                  video_id: str = ""):
    """Search for lyrics via LRCLib.

    If q is provided but artist is not, q is treated as "artist - title" or just title.
    """
    title, artist = _split_lyrics_query(q, artist)

    if not title:
        return {"synced": False, "lines": None, "plain": None, "source": None}

    return _lookup_lyrics(title, artist, album, duration, video_id)


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
    title, artist = _split_lyrics_query(q, artist)

    if not title:
        return {"synced": False, "lines": None, "plain": None, "source": None}

    return _lookup_lyrics(title, artist, album, duration, video_id)


# ======================================================================
# Loop Studio — a song taken apart into a Loop Plan (see loopstudio.py).
#
# A job runs in a thread: separate (through the Steems queue, if the song is
# not separated yet) → analyse (beat grid, sections, chords — Steems' own,
# cached next to the stems) → plan → cut the loops. The page polls the job.
# ======================================================================
import loopstudio as _ls
import voice as _voice

# ---- voice commands (voice.py): the page sends what the microphone hears,
# a quarter of a second at a time, and gets back a command when one was said.
_voice_listener = None
_voice_lock = threading.Lock()
# One microphone is listened to at a time. The mini player, when it listens,
# comes before the app's window: the app steps back until the mini player has
# been quiet for a few seconds (closed, or switched off).
_voice_owner = {"who": "app", "at": 0.0}


def _voice_taken(who):
    """The other window that holds the listening right now, or None."""
    if who == "app" and _voice_listener is not None and _voice_owner["who"] == "mini" and time.time() - _voice_owner["at"] < 6:
        return "mini"
    return None


@app.get("/api/voice/commands")
def voice_commands():
    return {"wake": _voice.WAKE, "commands": _voice.help_rows(), "unavailable": _voice.available(),
            "custom": _voice.load_custom(),
            "taught": {k: len(v["heard"]) + len(v["whisper"]) for k, v in _voice.load_taught().items()}}


class VoiceCustom(BaseModel):
    name: str = ""
    phrase: str
    steps: List[str]


@app.post("/api/voice/custom")
def voice_custom_add(req: VoiceCustom):
    """One of the user's own commands: a phrase, and the actions it runs in order."""
    why = _voice.available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    try:
        return _voice.add_custom(req.name, req.phrase, req.steps)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/voice/custom/{cid}")
def voice_custom_delete(cid: str):
    if not _voice.delete_custom(cid):
        return JSONResponse({"error": "Povel se nenašel."}, status_code=404)
    return {"ok": True}


@app.post("/api/voice/start")
def voice_start(who: str = "app"):
    """A fresh listener — what was half-heard before is forgotten."""
    global _voice_listener
    why = _voice.available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    who = "mini" if who == "mini" else "app"
    with _voice_lock:
        if who == "app" and _voice_taken("app"):
            return JSONResponse({"error": "Poslouchá minipřehrávač.", "taken": "mini"}, status_code=409)
        _voice_listener = _voice.Listener()
        _voice_owner.update(who=who, at=time.time())
    return {"ok": True, "rate": _voice.RATE}


@app.post("/api/voice/feed")
async def voice_feed(request: Request, who: str = "app", menu: bool = False):
    pcm = await request.body()
    lis = _voice_listener
    if lis is None:
        return JSONResponse({"error": "Hlasové ovládání neběží."}, status_code=409)
    who = "mini" if who == "mini" else "app"
    if _voice_owner["who"] != who:
        # The mini player started listening after the app did (or the other way
        # round, once the mini player has gone quiet): this window is not the one.
        return JSONResponse({"error": "Poslouchá jiné okno.", "taken": _voice_owner["who"]}, status_code=409)
    _voice_owner["at"] = time.time()
    if len(pcm) > _voice.RATE * 2 * 5 or len(pcm) % 2:
        return JSONResponse({"error": "Neplatný zvuk."}, status_code=400)
    return await asyncio.get_running_loop().run_in_executor(None, _voice_heard, lis, pcm, menu)


def _voice_library():
    """Songs and bands a spoken name can mean."""
    songs = [e for e in _all_songs_flat().get("entries", []) if e.get("type") == "file"]
    bands = {}
    for e in songs:
        if e.get("artist"):
            bands.setdefault(e["artist"], []).append(e)
    return songs, bands


def voice_find(kind, name):
    """The song or band in the library that `name` means, or why not."""
    if kind in ("playlist", "open"):
        return {"kind": kind, "query": name}
    songs, bands = _voice_library()
    if kind == "band":
        keys = sorted(bands)
        i, score = _voice.best_match(name, keys)
        if i is None:
            return {"kind": kind, "query": name, "error": f"Kapelu „{name}“ jsem v knihovně nenašel."}
        tracks = sorted(bands[keys[i]], key=lambda e: (e.get("album") or "", e.get("track") or 0, e.get("title") or ""))
        return {"kind": kind, "query": name, "artist": keys[i], "score": round(score, 2),
                "tracks": [{"rel": e["rel"], "title": e.get("title") or e["name"], "artist": e.get("artist") or "",
                            "album": e.get("album")} for e in tracks]}
    # A title alone, or said with its band.
    i, score = _voice.best_match(name, [e.get("title") or e["name"] for e in songs])
    j, score2 = _voice.best_match(name, [f'{e.get("artist") or ""} {e.get("title") or e["name"]}' for e in songs])
    if j is not None and score2 > score:
        i, score = j, score2
    if i is None:
        if kind in ("search", "here", "play"):     # not in the library: the page looks further itself
            return {"kind": kind, "query": name, "missing": True}
        return {"kind": kind, "query": name, "error": f"Píseň „{name}“ jsem v knihovně nenašel."}
    e = songs[i]
    return {"kind": kind, "query": name, "score": round(score, 2),
            "track": {"rel": e["rel"], "title": e.get("title") or e["name"], "artist": e.get("artist") or "",
                      "album": e.get("album"), "stems": e.get("stems")}}


def _voice_heard(lis, pcm, menu=False):
    r = lis.feed(pcm, menu)
    audio = r.pop("pcm", None)
    if audio is None:
        return r
    # Whisper reads the utterance again: a name after "song" / "band" /
    # "practice", or a command the small recogniser did not catch.
    # "again": Pick was spoken to and no command came of it — the page says so aloud.
    quiet = {"heard": r["heard"], "command": None, "again": r["command"] == "ask"}
    why = _voice.names_available()
    if why:
        return {**quiet, "find": {"error": why}} if r["command"] != "ask" else quiet
    songs, bands = _voice_library()
    try:
        text = _voice.transcribe(audio, ", ".join(sorted(bands, key=lambda b: -len(bands[b]))[:12]))
    except Exception:
        return quiet
    what = _voice.interpret(text, lis.custom, r["command"][5:] if r["command"].startswith("find:") else None, lis.taught)
    # What whisper wrote, without the wake word — for a window that shows a
    # list and takes a title said aloud as the choice.
    quiet["said"] = " ".join(_voice._said(text))
    if not what:
        return quiet
    if what[0] == "command":
        cmd = what[1]
        if cmd.startswith("custom:"):
            c = next(x for x in lis.custom if x["id"] == cmd[7:])
            return {"heard": f"{_voice.WAKE} {c['phrase']}", "command": cmd, "name": c["name"], "steps": c["steps"]}
        return {"heard": " ".join(text.lower().strip(" .!?").replace(",", "").split()), "command": cmd, "steps": [cmd]}
    _, kind, name = what
    return {"heard": f"{_voice.WAKE} {kind} {name}", "command": f"find:{kind}", "name": name, "find": voice_find(kind, name)}


@app.get("/api/voice/find")
def voice_find_api(kind: str, name: str):
    """The same lookup for a name that was typed (a step of a command of your own)."""
    if kind not in ("song", "band", "practice"):
        return JSONResponse({"error": "Neznámý druh."}, status_code=400)
    return voice_find(kind, name)


@app.post("/api/mini/open")
def mini_open():
    """The mini player's window, as the menu bar opens it; brought forward when it is already there."""
    url = "http://127.0.0.1:8765/mini"
    script = f'''tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t starts with "{url}" then
        if minimized of w then set minimized of w to false
        set index of w to 1
        activate
        return "found"
      end if
    end repeat
  end repeat
  return "none"
end tell'''
    try:
        if subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5).stdout.strip() == "found":
            return {"ok": True, "opened": False}
    except Exception:
        pass
    subprocess.Popen(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=340,210"])
    return {"ok": True, "opened": True}


# ---- the assistant (assistant.py): a Czech sentence in, the app's own actions out
import assistant as _assistant


class AssistSay(BaseModel):
    session: str
    text: str


class AssistResults(BaseModel):
    session: str
    results: List[dict]


def _assist_run(fn, *args):
    why = _assistant.available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    return fn(*args)


@app.get("/api/assistant")
def assistant_status():
    return {"unavailable": _assistant.available(), "model": _assistant.MODEL}


@app.post("/api/assistant/say")
async def assistant_say(req: AssistSay):
    if not req.text.strip():
        return JSONResponse({"error": "Prázdná věta."}, status_code=400)
    return await asyncio.get_running_loop().run_in_executor(None, _assist_run, _assistant.say, req.session, req.text)


@app.post("/api/assistant/hear")
async def assistant_hear(request: Request, session: str):
    """A spoken sentence (16 kHz mono s16le, Czech): whisper writes it down, then as /say."""
    why = _assistant.available() or _voice.names_available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    pcm = await request.body()
    if len(pcm) < _voice.RATE or len(pcm) > _voice.RATE * 2 * 25 or len(pcm) % 2:
        return JSONResponse({"error": "Nic jsem neslyšel."}, status_code=400)

    def work():
        text = _voice.transcribe(pcm, lang="cs", prompt="Povely pro hudební aplikaci: píseň, kapela, kytara, bicí, basa, zpěv, stopa, tempo, smyčka, refrén, sólo, Steems.")
        text = text.strip()
        if len(text) < 2:
            return {"heard": "", "error": "Nerozuměl jsem, zkus to znovu.", "done": True}
        return {"heard": text, **_assistant.say(session, text)}
    return await asyncio.get_running_loop().run_in_executor(None, work)


@app.post("/api/assistant/results")
async def assistant_results(req: AssistResults):
    return await asyncio.get_running_loop().run_in_executor(None, _assist_run, _assistant.results, req.session, req.results)


@app.post("/api/window/full")
def window_full(which: str = "app"):
    """A Pick window over the whole screen and back — for a spoken command, which
    a page may not do by itself (the browser wants a click for real full
    screen). This is the window's own "zoom": everything but the menu bar."""
    base = "http://127.0.0.1:8765", "http://localhost:8765"
    mini = "true" if which == "mini" else "false"
    script = f'''tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      set u to URL of t
      if (u starts with "{base[0]}" or u starts with "{base[1]}") and ((u contains "/mini") is {mini}) then
        if minimized of w then set minimized of w to false
        set zoomed of w to not (zoomed of w)
        set index of w to 1
        return "ok"
      end if
    end repeat
  end repeat
  return "none"
end tell'''
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=6).stdout.strip()
    except Exception:
        out = ""
    if out != "ok":
        return JSONResponse({"error": "Okno se nenašlo."}, status_code=404)
    return {"ok": True}


def _voice_teach(cmd, pcm):
    # Already understood as it is? Then the take is only kept, to be checked
    # against whatever is learned later.
    if _voice.Listener().command(pcm) == cmd:
        _voice.save_take(cmd, pcm)
        return {"heard": "", "understood": True, "added": False, "works": True}
    text = ""
    if not _voice.names_available():
        try:
            text = _voice.transcribe(pcm)
        except Exception:
            text = ""
    out = _voice.teach(cmd, _voice.hear(pcm), text)
    # The same take once more, now that its words are known — and every take
    # kept so far: new words must not push an old command out.
    out["works"] = _voice.Listener().command(pcm) == cmd
    broke = _voice.broken_takes() if out["works"] else []
    if broke or not out["works"]:
        _voice.unteach(cmd, out["heard"])
        if broke:
            names = {c["id"]: c["say"] for c in _voice.help_rows()}
            names.update({"custom:" + c["id"]: f'{_voice.WAKE} {c["phrase"]}' for c in _voice.load_custom()})
            raise ValueError("Tohle znělo moc podobně jako „" + names.get(broke[0], broke[0]) + "“ — neuložil jsem to. Zkus to říct znovu.")
        out["added"] = False
    else:
        _voice.save_take(cmd, pcm)
    return out


@app.post("/api/voice/teach")
async def voice_teach(request: Request, cmd: str):
    """One take of the user saying a command; what was heard is kept for it."""
    global _voice_listener
    why = _voice.available()
    if why:
        return JSONResponse({"error": why}, status_code=503)
    pcm = await request.body()
    if not pcm or len(pcm) > _voice.RATE * 2 * 8 or len(pcm) % 2:
        return JSONResponse({"error": "Neplatný zvuk."}, status_code=400)
    try:
        out = await asyncio.get_running_loop().run_in_executor(None, _voice_teach, cmd, pcm)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with _voice_lock:
        if _voice_listener is not None:
            _voice_listener = _voice.Listener()
    return out


@app.delete("/api/voice/teach")
def voice_forget(cmd: str):
    global _voice_listener
    _voice.forget(cmd)
    with _voice_lock:
        if _voice_listener is not None:
            _voice_listener = _voice.Listener()
    return {"ok": True}


# ---- listening from the menu bar, with no window open. The server reads the
# microphone itself (ffmpeg, the same way it records the interface) and feeds
# the same recogniser. A window that listens comes first — the mini player,
# then the app; this only hears what nobody else is hearing. What it hears and
# cannot do alone (anything but opening a window) is passed to a window, the
# mini player being opened for it when none is there.
_WIDGET = {"proc": None, "running": False, "error": None, "heard": "", "stop": None, "level": 0.0, "sound_at": 0.0, "since": 0.0}
_WIDGET_LOCK = threading.Lock()


def _window_listening():
    return _voice_listener is not None and time.time() - _voice_owner["at"] < 6


def _widget_again():
    """Not understood, and no window to say so: the computer's own voice does."""
    try:
        subprocess.Popen(["say", "-v", "Samantha", "Again"])
    except OSError:
        pass


def _widget_do(r):
    global _REMOTE_SEQ
    cmd = r.get("command")
    _WIDGET["heard"] = r.get("heard", "")
    if not cmd:
        if r.get("again"):
            _widget_again()
        return
    if cmd == "open":
        _WIDGET_OPEN["seq"] = int(time.time() * 1000)
        return
    if cmd == "mini":
        mini_open()
        return
    r = {k: v for k, v in r.items() if k != "pcm"}
    mini_there = time.time() - _MINI_SEEN["at"] < 4
    app_there = (time.time() - _REMOTE_STATE.get("at", 0)) < 12
    if app_there and not mini_there:
        _REMOTE_SEQ += 1
        _REMOTE_COMMANDS.append((_REMOTE_SEQ, {"action": "heard", "value": None, "text": json.dumps(r, ensure_ascii=False)}))
        del _REMOTE_COMMANDS[:-_REMOTE_KEEP]
        return
    if not mini_there:
        mini_open()
    _MINI_VOICE.update(seq=int(time.time() * 1000), at=time.time(), r=r)


def _widget_loop(proc, stop):
    lis, skipped = None, True
    try:
        while not stop.is_set():
            pcm = proc.stdout.read(8000)                 # a quarter of a second
            if not pcm:
                break
            # How loud the microphone is (0–1). A microphone macOS has not allowed
            # gives perfect silence: 0, always — that is how it shows.
            _WIDGET["level"] = max(abs(v) for v in memoryview(pcm[:len(pcm) - len(pcm) % 2]).cast("h")[::16]) / 32768 if len(pcm) > 32 else 0.0
            if _WIDGET["level"] > 0:
                _WIDGET["sound_at"] = time.time()
            if _window_listening():
                skipped = True                           # a window is listening: not ours to hear
                continue
            if skipped or lis is None:
                lis, skipped = _voice.Listener(), False  # a fresh start; half a sentence from before is nothing
            try:
                r = _voice_heard(lis, pcm[:len(pcm) - len(pcm) % 2])
            except Exception:
                continue
            if r and r.get("heard"):
                _widget_do(r)
    finally:
        if proc.poll() is None:
            proc.kill()
        err = ""
        try:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
            err = err[0] if err else ""
        except Exception:
            pass
        with _WIDGET_LOCK:
            if _WIDGET["proc"] is proc:
                _WIDGET.update(proc=None, running=False, error=None if stop.is_set() else (err or "Mikrofon se zavřel."))


def _widget_start():
    why = _voice.available()
    if why:
        _WIDGET["error"] = why
        return False
    cfg = _voice.widget_config()
    device = _capture_device(cfg["device"]) if cfg["device"] else None
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", f":{device or 'default'}"]
    # A two-input interface: the microphone's input alone, not mixed with the guitar's.
    if cfg["channel"] in ("1", "2") and device:
        cmd += ["-af", f"pan=mono|c0=c{int(cfg['channel']) - 1}"]
    cmd += ["-ar", str(_voice.RATE), "-ac", "1", "-f", "s16le", "-"]
    with _WIDGET_LOCK:
        if _WIDGET["proc"] is not None:
            return True
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except OSError as e:
            _WIDGET["error"] = f"Mikrofon se nepodařilo otevřít ({e})."
            return False
        stop = threading.Event()
        _WIDGET.update(proc=proc, running=True, error=None, stop=stop, since=time.time(), sound_at=0.0, level=0.0)
    threading.Thread(target=_widget_loop, args=(proc, stop), daemon=True).start()
    return True


def _widget_stop():
    with _WIDGET_LOCK:
        proc, stop = _WIDGET["proc"], _WIDGET["stop"]
        _WIDGET.update(proc=None, running=False, error=None)
    if stop is not None:
        stop.set()
    if proc is not None and proc.poll() is None:
        proc.kill()


class VoiceWidget(BaseModel):
    on: Optional[bool] = None
    device: Optional[str] = None      # as the browser names it; "" = the system's default input
    channel: Optional[str] = None     # "1" / "2" of a two-input interface, "" = as it comes


@app.get("/api/voice/widget")
def voice_widget():
    return {**_voice.widget_config(), "running": bool(_WIDGET["running"]), "error": _WIDGET["error"], "heard": _WIDGET["heard"],
            "level": round(_WIDGET["level"], 4) if _WIDGET["running"] else 0.0,
            # Not one sample above zero in five seconds of listening: macOS is not letting the microphone through.
            "silent": bool(_WIDGET["running"] and not _WIDGET["sound_at"] and time.time() - _WIDGET["since"] > 5)}


@app.post("/api/voice/widget")
def voice_widget_set(req: VoiceWidget):
    before = _voice.widget_config()
    cfg = _voice.widget_save(on=req.on, device=req.device, channel=req.channel)
    moved = (cfg["device"], cfg["channel"]) != (before["device"], before["channel"])
    if not cfg["on"] or moved:
        _widget_stop()
    if cfg["on"]:
        _widget_start()
        time.sleep(0.6)                # long enough for a microphone that will not open to say so
    return voice_widget()


@app.on_event("startup")
def _widget_resume():
    if _voice.widget_config()["on"]:
        threading.Thread(target=_widget_start, daemon=True).start()


@app.on_event("shutdown")
def _widget_end():
    _widget_stop()


@app.get("/api/voice/remote")
def voice_remote(on: Optional[int] = None):
    """Listening on or off from outside the page — a Siri shortcut opens this
    address. The microphone belongs to the open Pick window, so it is asked to."""
    global _REMOTE_SEQ
    if (time.time() - _REMOTE_STATE.get("at", 0)) > 12:
        return PlainTextResponse("Pick is not open.", status_code=409)
    _REMOTE_SEQ += 1
    _REMOTE_COMMANDS.append((_REMOTE_SEQ, {"action": "voice", "value": None if on is None else float(bool(on))}))
    del _REMOTE_COMMANDS[:-_REMOTE_KEEP]
    return PlainTextResponse("Pick is listening." if on else "Pick stopped listening." if on is not None else "Pick listening switched.")


@app.post("/api/voice/stop")
def voice_stop(who: str = "app"):
    global _voice_listener
    who = "mini" if who == "mini" else "app"
    with _voice_lock:
        if _voice_owner["who"] == who or not _voice_taken(who):      # not somebody else's listening
            _voice_listener = None
            _voice_owner.update(who="app", at=0.0)
    return {"ok": True}


class VoiceBest(BaseModel):
    query: str
    names: List[str]


# ---- "Again!": what is shown and shouted when Pick was spoken to and did not
# understand — the user's own words, and their own voice (a short recording).
AGAIN_TEXT = "Say it again!!!"


def _again_paths():
    d = _voice.CUSTOM_PATH.parent
    return d / "voice-again.json", d / "voice-again.wav"


def _again_state():
    cfg, wav = _again_paths()
    try:
        text = str(json.loads(cfg.read_text(encoding="utf-8")).get("text") or "").strip()
    except (OSError, ValueError, AttributeError):
        text = ""
    return {"text": text or AGAIN_TEXT, "audio": wav.is_file(), "v": int(wav.stat().st_mtime) if wav.is_file() else 0}


class AgainText(BaseModel):
    text: str = ""


@app.get("/api/voice/again")
def voice_again():
    return _again_state()


@app.put("/api/voice/again")
def voice_again_text(req: AgainText):
    cfg, _ = _again_paths()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"text": req.text.strip()[:60]}, ensure_ascii=False), encoding="utf-8")
    return _again_state()


@app.post("/api/voice/again/audio")
async def voice_again_record(request: Request):
    """The user's own shout, as the microphone took it (16 kHz mono s16le): the
    silence before and after is cut, and it is made as loud as it goes."""
    import wave
    import numpy as np
    pcm = await request.body()
    if not pcm or len(pcm) % 2 or len(pcm) > _voice.RATE * 2 * 8:
        return JSONResponse({"error": "Neplatný zvuk."}, status_code=400)
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak < 600:
        return JSONResponse({"error": "Nic jsem neslyšel — zkus to znovu a hlasitěji."}, status_code=400)
    loud = np.flatnonzero(np.abs(x) > peak * 0.08)
    pad = int(_voice.RATE * 0.08)
    x = x[max(0, loud[0] - pad):loud[-1] + pad]
    x = (x * (30000.0 / peak)).astype(np.int16)
    _, wav = _again_paths()
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(_voice.RATE)
        w.writeframes(x.tobytes())
    tmp.replace(wav)
    return {**_again_state(), "seconds": round(len(x) / _voice.RATE, 2)}


@app.get("/api/voice/again/audio")
def voice_again_audio():
    _, wav = _again_paths()
    if not wav.is_file():
        return JSONResponse({"error": "Nic nahraného."}, status_code=404)
    return FileResponse(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.delete("/api/voice/again/audio")
def voice_again_forget():
    _, wav = _again_paths()
    wav.unlink(missing_ok=True)
    return _again_state()


@app.post("/api/voice/best")
def voice_best(req: VoiceBest):
    """Which of the names on screen was said: its index, or null when none is close."""
    i, score = _voice.best_match(req.query, req.names[:2000])
    return {"index": i, "score": round(score, 2)}


HARDWARE_PROFILE_PATH = NAM_PREVIEW_DIR / "hardware-profile.json"
_LS_JOBS: dict = {}            # job id → {state, progress, song, error, note}
_LS_STATES = ("queued", "separating", "analyzing", "planning", "cutting", "done", "error")


def _hardware_profile() -> dict:
    try:
        data = json.loads(HARDWARE_PROFILE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return json.loads(json.dumps(_ls.DEFAULT_PROFILE))


def _ls_stems_dir(song: str):
    d = _resolve_under_music_root(f"Stems/{song}")
    return d if d is not None and d.is_dir() else None


def _ls_run(job_id: str, rel: str, song: str, refresh: bool):
    job = _LS_JOBS[job_id]

    def step(state, progress=None, note=None):
        job["state"] = state
        if progress is not None:
            job["progress"] = round(progress, 3)
        if note is not None:
            job["note"] = note

    try:
        if rel:
            step("separating", 0.02, "Rozděluji píseň na stopy")
            res = separate_stems(StemsSeparateRequest(rel=rel))
            if not isinstance(res, dict):
                raise RuntimeError("Zdrojový soubor nenalezen.")
            song = res["name"]
            job["song"] = song
            sid = res.get("job_id")
            while sid and JOBS.get(sid, {}).get("status") not in ("done", "error"):
                sj = JOBS.get(sid) or {}
                pos = next((i + 1 for i, (jid, _) in enumerate(_stems_active_jobs()) if jid == sid), None)
                step("separating", 0.02 + 0.5 * float(sj.get("progress") or 0) / 100.0,
                     "Čeká ve frontě separací" if sj.get("status") == "queued" and pos and pos > 1 else "Rozděluji píseň na stopy")
                time.sleep(1.0)
            if sid and JOBS.get(sid, {}).get("status") == "error":
                raise RuntimeError("Separace se nepodařila — podrobnosti ve výpisu úloh.")
        stems_dir = _ls_stems_dir(song)
        if stems_dir is None:
            raise RuntimeError("Stopy písně nenalezeny.")

        step("analyzing", 0.55, "Hledám doby a takty")
        grid = _beat_grid_for(stems_dir)
        if isinstance(grid, str):
            raise RuntimeError("Takty se nepodařilo určit: " + grid)
        step("analyzing", 0.62, "Hledám sekce a akordy (kolem minuty)")
        analysis = stems_analysis(song)
        if not isinstance(analysis, dict):
            analysis = {"sections": [], "chords": []}
        # An analysis cached before the beat grid existed has no chords: once more.
        if not analysis.get("chords") and any("beat grid" in w for w in analysis.get("warnings") or []):
            again = stems_analysis(song, refresh=True)
            if isinstance(again, dict):
                analysis = again

        step("planning", 0.86, "Počítám tóninu a sestavuji plán")
        ff = ffmpeg_bin()
        files = _ls.stem_files(stems_dir)
        harmonic = [files[r] for r in ("guitar", "piano", "bass", "other") if r in files] or list(files.values())
        mix = None
        for f in harmonic:
            x = _ls.decode(f, ff)
            mix = x if mix is None else (mix[:min(len(mix), len(x))] + x[:min(len(mix), len(x))])
        key = _ls.estimate_key(mix, chords=analysis.get("chords") or [])
        sections = _ls.build_sections(grid, analysis)
        activity = _ls.stem_activity(stems_dir, sections, ff)
        meta = next((x.get("meta") for x in list_stems() if x.get("name") == song), None) if "list_stems" in globals() else None
        old = _ls.load_plan(stems_dir)
        plan = _ls.build_plan(song, grid, analysis, key, activity, _hardware_profile(), meta)
        if old and old.get("edited") and not refresh:
            plan = old                                    # the player's corrections stand
            plan["loopy"] = _ls.loopy_card(plan["facts"], plan["loops"], _hardware_profile())

        step("cutting", 0.9, "Řežu smyčky na takty")
        _ls.cut_loops(plan, grid, stems_dir, ff, lambda x: step("cutting", 0.9 + 0.09 * x))
        _ls.save_plan(stems_dir, plan)
        step("done", 1.0, "")
    except Exception as e:                                     # noqa: BLE001 — the job reports it
        job.update(state="error", error=str(e) or e.__class__.__name__)


class LoopJobRequest(BaseModel):
    rel: str = ""              # a file in the library (separated first if needed)…
    song: str = ""             # …or a song already in Steems
    refresh: bool = False      # measure again, dropping manual corrections


@app.post("/api/loopstudio/jobs")
def loopstudio_start(req: LoopJobRequest):
    if not req.rel and not req.song:
        return JSONResponse({"error": "Chybí píseň."}, status_code=400)
    if req.song and _ls_stems_dir(req.song) is None:
        return JSONResponse({"error": "Píseň ve Steems nenalezena."}, status_code=404)
    if req.rel:
        src = _resolve_under_music_root(req.rel)
        if src is None or not src.is_file():
            return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    job_id = uuid.uuid4().hex[:12]
    _LS_JOBS[job_id] = {"state": "queued", "progress": 0.0, "song": req.song, "error": None, "note": "", "started": time.time()}
    threading.Thread(target=_ls_run, args=(job_id, req.rel, req.song, req.refresh), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/loopstudio/upload")
async def loopstudio_upload(file: UploadFile = File(...)):
    """A song from the computer: kept in the library (Songs/Loop Studio), so
    it is there for the player and for Steems like any other."""
    name = Path(file.filename or "song").name
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        return JSONResponse({"error": "Tohle není zvukový soubor."}, status_code=400)
    folder = MUSIC_ROOT / "Songs" / "Loop Studio"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    return {"rel": str(dest.relative_to(MUSIC_ROOT))}


@app.get("/api/loopstudio/jobs/{job_id}")
def loopstudio_job(job_id: str):
    job = _LS_JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": "Úloha nenalezena."}, status_code=404)
    return {k: job.get(k) for k in ("state", "progress", "song", "error", "note")}


@app.get("/api/loopstudio/plans")
def loopstudio_plans():
    """Songs that already have a Loop Plan, newest first."""
    out = []
    root = MUSIC_ROOT / "Stems"
    if root.is_dir():
        for d in root.iterdir():
            f = d / "loopplan.json"
            if not f.is_file():
                continue
            plan = _ls.load_plan(d)
            if plan:
                out.append({"song": d.name, "bpm": plan["facts"]["bpm"], "key": plan["facts"]["key"],
                            "loops": sum(1 for l in plan["loops"] if l.get("enabled", True)), "modified": f.stat().st_mtime})
    out.sort(key=lambda x: -x["modified"])
    return {"plans": out}


@app.get("/api/loopstudio/plan/{song}")
def loopstudio_plan(song: str):
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None:
        return JSONResponse({"error": "Plán pro tuhle píseň ještě není."}, status_code=404)
    return plan


@app.put("/api/loopstudio/plan/{song}")
def loopstudio_plan_edit(song: str, edits: dict):
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None:
        return JSONResponse({"error": "Plán pro tuhle píseň ještě není."}, status_code=404)
    grid = _beat_grid_for(d)
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=422)
    plan = _ls.apply_edits(plan, edits or {}, grid, _hardware_profile())
    _ls.save_plan(d, plan)
    return plan


@app.post("/api/loopstudio/plan/{song}/loops")
def loopstudio_cut(song: str):
    """Cut the loops again — after a correction to the plan."""
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None:
        return JSONResponse({"error": "Plán pro tuhle píseň ještě není."}, status_code=404)
    grid = _beat_grid_for(d)
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=422)
    _ls.cut_loops(plan, grid, d, ffmpeg_bin())
    _ls.save_plan(d, plan)
    return plan


def _ls_parts(d: Path, plan: dict, grid: dict, fresh: bool = False) -> dict:
    """What to play in the arrangement — worked out once and kept with the
    plan until the sections, the order or the loops change."""
    parts = plan.get("parts")
    if fresh or not parts or parts.get("signature") != _ls.parts_signature(plan):
        parts = _ls.build_parts(plan, grid, d, ffmpeg_bin(), _hardware_profile())
        plan["parts"] = parts
        _ls.save_plan(d, plan)
    return parts


@app.get("/api/loopstudio/plan/{song}/parts")
def loopstudio_parts(song: str, fresh: int = 0):
    """The arrangement taken apart: chords as notes, the kit's groove as a
    pad pattern, an instrument for each layer."""
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None:
        return JSONResponse({"error": "Plán pro tuhle píseň ještě není."}, status_code=404)
    grid = _beat_grid_for(d)
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=422)
    parts = _ls_parts(d, plan, grid, bool(fresh))
    notes = _ls.arrangement_notes(plan, parts)
    return {**parts, "arrangement": _ls.arrangement_of(plan), "seconds": notes["seconds"],
            "counts": {k: len(notes[k]) for k in ("chords", "bass", "drums")}}


@app.get("/api/loopstudio/plan/{song}/midi/{which}.mid")
def loopstudio_midi(song: str, which: str):
    """The arrangement's chords, bass roots or drum pattern as a MIDI file."""
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None or which not in ("chords", "bass", "drums"):
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    grid = _beat_grid_for(d)
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=422)
    notes = _ls.arrangement_notes(plan, _ls_parts(d, plan, grid))[which]
    if not notes:
        return JSONResponse({"error": "Pro tohle v aranžmá nejsou žádné tóny."}, status_code=404)
    label = {"chords": "akordy", "bass": "basa", "drums": "bici"}[which]
    data = _ls.midi_bytes(notes, float(plan["facts"]["bpm"]), f"{song} {label}", 9 if which == "drums" else 0)
    from fastapi.responses import Response
    return Response(data, media_type="audio/midi",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(_safe_name(song) + ' - ' + label + '.mid')}"})


@app.post("/api/loopstudio/plan/{song}/project")
def loopstudio_project(song: str):
    """The arrangement as a project in Steems to record into: every stem
    with the sections in the new order, on the plan's tempo, and MIDI tracks
    with the chords, the bass roots and the drum pattern."""
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None:
        return JSONResponse({"error": "Plán pro tuhle píseň ještě není."}, status_code=404)
    grid = _beat_grid_for(d)
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=422)
    if not _ls.sequence(plan):
        return JSONResponse({"error": "Aranžmá je prázdné."}, status_code=400)
    parts = _ls_parts(d, plan, grid)
    notes = _ls.arrangement_notes(plan, parts)
    title = plan["song"].get("title") or song
    proj = _new_project_dir(f"Looper - {title}")
    tmp = Path(tempfile.mkdtemp(prefix="pick-arr-"))
    try:
        made = _ls.render_arrangement(plan, d, ffmpeg_bin(), tmp)
        kept = []
        for role, wav in made.items():
            if _encode_to_stem(wav, proj / f"{role}.mp3") is None:
                kept.append(role)
        if not kept:
            shutil.rmtree(proj, ignore_errors=True)
            return JSONResponse({"error": "Stopy aranžmá se nepodařilo vytvořit."}, status_code=500)
        tracks = []
        for key, label, inst in (("chords", "Akordy (klávesy)", "grand_piano_yamaha"), ("bass", "Basa – základní tóny", "fender_precision_pick"),
                                 ("drums", "Bicí – kdy uhodit (pady)", "japanese_taiko_drums")):
            if notes[key]:
                tracks.append({"stem": f"midi-{len(tracks) + 1}", "label": label, "instrument": inst, "notes": notes[key][:20000]})
        (proj / "session.json").write_text(json.dumps({"channels": [], "loop": None, "pixelsPerSecond": None, "tempo": None,
                                                       "midi": tracks or None}, ensure_ascii=False), encoding="utf-8")
        (proj / "meta.json").write_text(json.dumps({"artist": plan["song"].get("artist"), "title": f"Looper - {title}", "album": None},
                                                   ensure_ascii=False), encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"name": proj.name, "stems": kept, "midi": [t["label"] for t in tracks], "seconds": notes["seconds"]}


@app.get("/api/loopstudio/file/{song}/{path:path}")
def loopstudio_file(song: str, path: str):
    d = _ls_stems_dir(song)
    f = (d / path).resolve() if d else None
    if f is None or not f.is_file() or (d / "Loops").resolve() not in f.parents or f.suffix.lower() != ".wav":
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="audio/wav")


@app.get("/api/loopstudio/loops/{song}.zip")
def loopstudio_zip(song: str):
    d = _ls_stems_dir(song)
    plan = _ls.load_plan(d) if d else None
    if plan is None or not any(l.get("files") for l in plan["loops"]):
        return JSONResponse({"error": "Smyčky ještě nejsou nařezané."}, status_code=404)
    z = _ls.zip_loops(plan, d)
    return FileResponse(z, media_type="application/zip", filename=z.name)


@app.get("/api/loopstudio/hardware")
def loopstudio_hardware():
    return _hardware_profile()


@app.put("/api/loopstudio/hardware")
def loopstudio_hardware_save(profile: dict):
    if not isinstance(profile, dict) or not isinstance(profile.get("looping"), dict):
        return JSONResponse({"error": "Profil musí obsahovat část „looping“."}, status_code=400)
    NAM_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    HARDWARE_PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    return profile


@app.get("/loopstudio", response_class=HTMLResponse)
def loopstudio_page():
    return (APP_DIR / "static" / "loopstudio.html").read_text(encoding="utf-8")

