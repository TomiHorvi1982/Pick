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
import unicodedata
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


# Started from the Finder (the Pick app), the server gets the bare system PATH
# — without Homebrew, where ffmpeg lives. Everything the server starts (and
# what those start in turn: demucs, spotdl) must find it all the same.
for _p in ("/opt/homebrew/bin", "/usr/local/bin", str(HOME / ".local" / "bin")):
    if _p not in os.environ.get("PATH", "").split(os.pathsep) and os.path.isdir(_p):
        os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")


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
# beside the settings, not in the music folder: that one may live on a card that is not always in
LIBRARY_PATH = HOME / ".local" / "share" / "spotdl-helper" / "library.json"


# One save at a time, and each one whole: a reader that met the file half written took it for
# empty, and the save after it wrote back only its own key — the music root (the card) went so.
_CONFIG_LOCK = threading.RLock()


def _read_config() -> dict:
    """The settings; should the file be cut short (a crash, a full disk), the last good copy."""
    for f in (CONFIG_PATH, CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):            # missing, cut short, unreadable
            continue
        if isinstance(data, dict):
            return data
    return {}


def _write_config(patch: dict) -> dict:
    """Merges patch into config.json rather than replacing it — several
    unrelated settings (music root, video mode, amp category folders) all
    live in this one file, so a plain overwrite would silently erase
    whichever of them wasn't part of the current save. Written beside and
    moved in place (never seen half done), a copy kept as .bak.
    """
    with _CONFIG_LOCK:
        data = _read_config()
        data.update(patch)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False)
        for f in (CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak"), CONFIG_PATH):     # the copy first, then the file
            tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, f)
        return data


def _load_music_root() -> Path:
    """Reads the configured download folder from CONFIG_PATH, falling back
    to the original hardcoded default when there's no config yet (first
    run) or it's unreadable.
    """
    raw = _read_config().get("music_root")
    return Path(raw).expanduser() if raw else DEFAULT_MUSIC_ROOT


MUSIC_ROOT = _load_music_root()
import storage as _storage                  # the cards (Nastavení → Úložiště); hooks set further down

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
    if name in ("look.js", "studio.js", "practice.js", "chordcoach.js", "deck.js", "looper.js", "looper-sound.js", "tone.js", "viz.js", "vguitar.js", "kontakt.js", "lyricstyle.js"):
        # Úpravy vzhledu — the design mode, a script of its own.
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
    if filename.startswith(("nam/", "ir/")):
        # A collection copied in whole: nam/<model>, ir/<pack>/<folders>/<impulse>.
        base = AMPS_DIR / filename.split("/", 1)[0]
        try:
            path = (base / filename.split("/", 1)[1]).resolve()
        except (OSError, ValueError):
            return None
        return path if base.resolve() in path.parents else None
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
    return out + _amp_collections()


# Collections copied in whole sit beside the player's own amps: AMPS_DIR/nam (models) and
# AMPS_DIR/ir/<pack>/… (cabinets, any folders deep). Each item says its pack, so a picker can
# group a thousand impulses instead of listing them in one column.
AMP_COLLECTIONS = {"nam": ("model", ".nam"), "ir": ("ir", ".wav")}


def _amp_collections() -> list:
    out = []
    for top, (kind, ext) in AMP_COLLECTIONS.items():
        base = AMPS_DIR / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*" + ext), key=lambda q: str(q).lower()):
            rel = path.relative_to(base)
            if any(p.startswith(".") for p in rel.parts) or not path.is_file():
                continue
            pack = rel.parts[0] if top == "ir" and len(rel.parts) > 1 else ("NAM komunita" if top == "nam" else "")
            folder = "/".join(rel.parts[1:-1]) if top == "ir" else ""
            out.append({"name": path.stem, "file": f"{top}/{rel.as_posix()}", "kind": kind,
                        "bytes": path.stat().st_size, "pack": pack, "folder": folder})
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
    Persisted in config.json under 'video_mode'."""
    return {"mode": _read_config().get("video_mode", "full")}


class VideoModeRequest(BaseModel):
    mode: str  # "hidden" | "mini" | "full"


@app.post("/api/video/mode")
def set_video_mode(req: VideoModeRequest):
    """Persist video display mode preference."""
    if req.mode not in ("hidden", "mini", "full"):
        return JSONResponse({"error": "mode must be hidden, mini, or full"}, status_code=400)
    _write_config({"video_mode": req.mode})
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
async def search_tabs(q: str, size: int = 20, start: int = 0):
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
                params={"pattern": q.strip(), "size": max(1, min(size, 40)), **({"from": min(start, 2000)} if start > 0 else {})},
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
    if candidate != root and root not in candidate.parents and not _storage.under_card(candidate):
        return None                         # (a song on another card Pick uses comes through its link)
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


# Websites in Tools that will not be shown inside another page (they forbid framing, and are behind a
# bot check): each opens in a window of its own, as the loop sites do — the browser's own, so it is
# the player's visit, nothing fetched or copied by Pick. Only these addresses, by name.
WEB_TOOLS = {"ukutabs": "https://ukutabs.com/cs/99-newest-ukulele-tabs/"}


class WebToolRequest(BaseModel):
    id: str


@app.post("/api/tools/web/open")
def tools_web_open(req: WebToolRequest):
    url = WEB_TOOLS.get(req.id)
    if not url:
        return JSONResponse({"error": "Neznámý web."}, status_code=404)
    r = subprocess.run(["open", "-na", "Google Chrome", "--args", f"--app={url}", "--window-size=1280,900"], capture_output=True)
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
    # The Steems song the mixer plays (the mini player takes it over by it).
    stems: Optional[str] = None
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
_MINI_TAKE = {"seq": 0, "at": 0.0}       # the mini player opened (again): it takes over what the app plays


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
    if time.time() - _MINI_TAKE["at"] < 30:
        out["mini_take"] = _MINI_TAKE["seq"]
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


@app.get("/mapa")
def app_map():
    """The map of the app — every feature, where it is, how to use it — offline, the same page as
    the one published online (static/mapa.html is its source, updated with every new feature)."""
    path = APP_DIR / "static" / "mapa.html"
    if not path.is_file():
        return JSONResponse({"error": "Mapa chybí."}, status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


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


def _library_rel(d: Path) -> Optional[Path]:
    """d as a path within the music folder — also a song on another card Pick uses, reached
    through its link (Stems/<name>) or by where it really is (<card>/Stems/<name>)."""
    try:
        return d.resolve().relative_to(MUSIC_ROOT.resolve())
    except (ValueError, OSError):
        pass
    try:
        rel = d.relative_to(MUSIC_ROOT)
        return rel if _storage.under_card(d.resolve()) else None
    except (ValueError, OSError):
        pass
    for c in _storage.cards():
        try:
            return d.resolve().relative_to(c.resolve())
        except (ValueError, OSError):
            pass
    return None


def _stem_file(stems_dir: Path, stem: str) -> Optional[Path]:
    """The file for one stem, whatever format it was written in.

    Resolved through the same containment check as everything else: a plain
    is_file() here would happily follow a symlink out of the library, which is
    precisely what _resolve_under_music_root exists to stop.
    """
    rel_dir = _library_rel(stems_dir)
    if rel_dir is None:
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
    if dest_dir.is_symlink():               # separated again: the old set was on another card
        shutil.rmtree(dest_dir.resolve(), ignore_errors=True)
        dest_dir.unlink()
    elif dest_dir.exists():
        shutil.rmtree(dest_dir)
    real_dir = _storage.place_song(dest_dir)   # the card chosen for new songs (Nastavení → Úložiště)
    shutil.move(str(raw_track_dir), str(real_dir))
    _storage.link_song(dest_dir, real_dir)
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
    _names_soon(dest_dir)


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
_STEMS_PAUSED = {"on": False}           # held in Úlohy a procesy: nothing new starts
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
    if job.get("stopped"):                       # ended by hand in Úlohy a procesy
        job["lines"].append("Zastaveno v Úlohách a procesech.")
        job["status"] = "stopped"
    elif rc == 0:
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
        if job is None or _STEMS_PAUSED["on"]:
            _STEMS_WAKE.wait(5)
            _STEMS_WAKE.clear()
            continue
        # not while the memory is short (another model running): it waits — five minutes at most, then goes anyway
        ok, why = _sysload.room(2.5, "rozdělení písně")
        if not ok and time.time() - job.setdefault("room_wait", time.time()) < 300:
            job["note"] = "čeká na volnou paměť"
            _STEMS_WAKE.wait(5)
            _STEMS_WAKE.clear()
            continue
        job.pop("note", None)
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
    check: bool = False       # only say whether a new separation would start (asked "now or tonight?" first)


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
    if req.check:
        return {"job_id": None, "ready": False, "name": safe_name, "check": True}

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
    key = lambda t: _names.key(t)
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
            st.update(state="error", error="Rozdělení na stopy zastaveno." if job["status"] in ("stopped", "cancelled") else "Rozdělení na stopy selhalo.")
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
            _stems_tuning_keep(d, st["tuning"], "songsterr")
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
_SAVES_PAUSED = {"on": False}           # held in Úlohy a procesy: the song under way is finished, no new one starts
# What waits is written down (the song under way first), so a restart of Pick goes on with it.
SAVES_QUEUE_FILE = HOME / ".local" / "share" / "spotdl-helper" / "saves-queue.json"
_SAVES_NOW: dict = {"req": None}


def _saves_keep() -> None:
    with _SAVES_LOCK:
        reqs = ([_SAVES_NOW["req"]] if _SAVES_NOW["req"] is not None else []) + [r for _, r in _SAVE_QUEUE if r is not None]
    try:
        if reqs:
            SAVES_QUEUE_FILE.write_text(json.dumps([r.model_dump() for r in reqs], ensure_ascii=False), encoding="utf-8")
        else:
            SAVES_QUEUE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _saves_resume() -> int:
    """After a restart: what was waiting goes on, the song that was under way first."""
    try:
        rows = json.loads(SAVES_QUEUE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = 0
    with _SAVES_LOCK:
        busy = {st["key"] for st in _SAVES.values() if st["state"] not in ("ready", "error")}
        for d in rows if isinstance(rows, list) else []:
            try:
                req = SaveSongRequest(**d)
            except Exception:                                 # noqa: BLE001 — a broken row is let go
                continue
            key = (req.title.strip().lower(), req.artists.strip().lower())
            if key in busy:
                continue
            busy.add(key)
            sid = uuid.uuid4().hex[:10]
            _SAVES[sid] = {"id": sid, "key": key, "title": req.title, "artists": req.artists, "state": "waiting", "started": time.time() + n / 1000}
            _SAVE_QUEUE.append((sid, req))
            n += 1
        if n and _SAVE_WORKER["thread"] is None:
            _SAVE_WORKER["thread"] = threading.Thread(target=_save_drain, name="save-many", daemon=True)
            _SAVE_WORKER["thread"].start()
    return n


def _save_drain() -> None:
    while True:
        while _SAVES_PAUSED["on"]:
            time.sleep(1)
        with _SAVES_LOCK:
            if not _SAVE_QUEUE:
                _SAVE_WORKER["thread"] = None
                return
            sid, req = _SAVE_QUEUE.popleft()
            _SAVES_NOW["req"] = req
        _saves_keep()
        try:
            if sid in _SAVES:
                _SAVES[sid]["state"] = "looking"
                _save_song_run(sid, req)
        finally:
            _SAVES_NOW["req"] = None
            _saves_keep()


def _save_have(title: str, artists: str, rel: str = "", stems_index=None) -> dict:
    """What there already is of one song: its file, its stems."""
    artist = (artists or "").split(",")[0].strip()
    src = _resolve_under_music_root(rel) if rel else None
    rel = rel if src is not None and src.is_file() else _save_library_rel(artist, title)
    src = _resolve_under_music_root(rel) if rel else None
    ready = bool(src is not None and (_stems_state_for(src) or {}).get("state") == "ready")
    if not ready and stems_index is not None:
        key = lambda t: _names.key(t)
        t = _names.tidy_live(artist, title)               # a video's name, read the way it would be tidied
        ready = (key(artist), key(title)) in stems_index or ("", key(title)) in stems_index and not artist \
            or (key(t["artist"]), key(t["title"])) in stems_index
    return {"library": bool(rel), "stems": ready}


def _stems_index() -> set:
    key = lambda t: _names.key(t)
    out = set()
    root = MUSIC_ROOT / "Stems"
    for d in (root.glob("*") if root.is_dir() else []):
        if d.is_dir() and not d.name.startswith(".") and (_stems_ready(d.name) or _stems_meta_raw(d).get("imported")):
            m = _stems_meta(d)
            out.add((key((m.get("artist") or "").split(",")[0]), key(m.get("title") or d.name)))
    return out


def _have_all() -> dict:
    """Everything there is, looked through once: the library's songs by their
    title, the separated ones by artist and title, the tabs that are kept."""
    key = lambda t: _names.key(t)
    lib: dict = {}
    for e in _all_songs_flat().get("entries", []):
        lib.setdefault(key(e.get("title") or Path(e["name"]).stem), []).append((key(e.get("artist") or ""), e["rel"]))
    stems: dict = {}
    root = MUSIC_ROOT / "Stems"
    for d in (sorted(root.glob("*")) if root.is_dir() else []):
        if d.is_dir() and not d.name.startswith(".") and (_stems_ready(d.name) or _stems_meta_raw(d).get("imported")):
            m = _stems_meta(d)
            stems.setdefault((key((m.get("artist") or "").split(",")[0]), key(m.get("title") or d.name)), d.name)
    tabs = {(_name_key((t.get("artist") or "").split(",")[0]), _base_title(t.get("title") or "")) for t in _pick_tabs()}
    return {"lib": lib, "stems": stems, "tabs": tabs}


def _have_one(s: "SaveSongRequest", al: dict) -> dict:
    """What there already is of one song — its file, its stems, its tab, its
    text — found the way saving it for practice would find them."""
    key = lambda t: _names.key(t)
    artist = (s.artists or "").split(",")[0].strip()
    want_t, want_a = key(s.title), key(artist)
    src = _resolve_under_music_root(s.relPath) if s.relPath else None
    rel = s.relPath if src is not None and src.is_file() else None
    if not rel and want_t:
        loose = None
        for have_a, r in al["lib"].get(want_t, []):
            if not want_a or not have_a or want_a in have_a or have_a in want_a:
                rel = r
                break
            loose = loose or r
        if not rel and not want_a:
            rel = loose
    src = _resolve_under_music_root(rel) if rel else None
    state = (_stems_state_for(src) or {}) if src is not None else {}
    name = s.stems if s.stems and _stems_ready(s.stems) else state.get("name") if state.get("state") == "ready" else None
    if not name:
        name = al["stems"].get((want_a, want_t)) or (al["stems"].get(("", want_t)) if not want_a else None)
    if not name:                                          # a video's name, read the way it would be tidied
        t = _names.tidy_live(artist, s.title)
        name = al["stems"].get((key(t["artist"]), key(t["title"])))
    title, by, dur, tuning = s.title, artist, s.duration or None, None
    if name:
        d = _stems_dir_for(name)
        m = _stems_meta(d)
        title, by = m.get("title") or title, (m.get("artist") or by)
        dur = next((x for x in (_audio_tags(f).get("duration") for f in (_stem_file(d, st) for st in STEM_NAMES[:2]) if f) if x), None)
        tuning = _stems_tuning(d)
    tabs = bool(tuning) or (_name_key(artist), _base_title(s.title)) in al["tabs"] \
        or (_name_key(by.split(",")[0]), _base_title(title)) in al["tabs"]
    return {"library": bool(rel), "stems": bool(name), "name": name, "tabs": tabs,
            "lyrics": bool(_lyrics_kept(title, by, dur) or _lyrics_kept(s.title, artist, s.duration or None)),
            "queued": state.get("state") in ("queued", "running")}


class HaveRequest(BaseModel):
    songs: List[SaveSongRequest] = []


@app.post("/api/practice/have")
def practice_have(req: HaveRequest):
    """Of each song given: is it downloaded, separated, has it a tab and a text.
    Nothing is looked up on the internet — only what is already here is told."""
    al = _have_all()
    return {"have": [_have_one(s, al) for s in req.songs[:3000]]}


class SaveManyRequest(BaseModel):
    songs: List[SaveSongRequest] = []
    playlists: List[str] = []
    bitrate: str = "192k"
    format: str = "mp3"
    mp3_bitrate: int = 320
    cookies_browser: Optional[str] = None
    cookie_file: Optional[str] = None
    check: bool = False            # only say what is missing; nothing is started
    finish: bool = False           # what is separated but has no tab or no text yet is looked up once more


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
    index, seen, missing, ready, finish = _stems_index(), set(), [], 0, []
    al = _have_all() if req.finish else None
    for s in songs:
        key = (s.title.strip().lower(), s.artists.strip().lower())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        if _save_have(s.title, s.artists, s.relPath, index)["stems"] or (s.stems and _stems_ready(s.stems)):
            ready += 1
            if al is not None:
                h = _have_one(s, al)
                if h["name"] and not (h["tabs"] and h["lyrics"]):
                    finish.append(s.model_copy(update={"stems": h["name"]}))
        else:
            missing.append(s)
    out = {"songs": len(seen), "ready": ready, "missing": len(missing), "finish": len(finish), "playlists": lists, "queued": 0}
    missing += finish
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
    _saves_keep()
    return out


@app.delete("/api/practice/save-many")
def practice_save_many_stop():
    """Stops what is still waiting; the song being worked on is finished."""
    with _SAVES_LOCK:
        n = len(_SAVE_QUEUE)
        for sid, _ in _SAVE_QUEUE:
            _SAVES.pop(sid, None)
        _SAVE_QUEUE.clear()
    _saves_keep()
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
            "status": job["status"], "progress": job.get("progress"), "note": job.get("note"),
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
                    "source": "manual" if m.get("manual") else "official" if m.get("official") else "tags"}
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


def _stems_tuning_keep(d: Path, tuning, source: str = "") -> None:
    """`source`: where it was read ("ug" for the second tab site) — and with
    an empty tuning, that this place too was asked and knew nothing."""
    try:
        old = json.loads((d / "tuning.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    asked = sorted(set((old.get("asked") if isinstance(old, dict) else None) or []) | ({source} if source else set()))
    try:
        (d / "tuning.json").write_text(json.dumps({"tuning": [int(x) for x in tuning or []][:12], "asked": asked,
                                                   **({"from": source} if source and tuning else {})}), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def _stems_tuning_asked(d: Path) -> list:
    try:
        return list(json.loads((d / "tuning.json").read_text(encoding="utf-8")).get("asked") or [])
    except (OSError, ValueError, AttributeError):
        return []


# A second place that knows tunings: Ultimate Guitar. Its tab pages name the
# tuning ("D A D G B E") — also of many songs Songsterr has no tab for, Czech
# ones among them. Read politely: one song at a time, a pause between pages,
# and only when the first place knew nothing. A guitar tab is believed before
# a chord sheet (those often just say "Standard").
_UG_LOCK = threading.Lock()
_UG_LAST = [0.0]
_UG_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _ug_page(url: str, **params) -> dict:
    import html as _html
    with _UG_LOCK:
        wait = 1.2 - (time.time() - _UG_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _UG_LAST[0] = time.time()
        r = httpx.get(url, params=params or None, headers={"User-Agent": _UG_AGENT}, timeout=15, follow_redirects=True)
    r.raise_for_status()
    m = _re_mod.search(r'class="js-store"\s+data-content="([^"]+)"', r.text)
    data = json.loads(_html.unescape(m.group(1))) if m else {}
    return (((data.get("store") or {}).get("page") or {}).get("data")) or {}


def _tuning_from_names(value: str) -> list:
    """"D A D G B E" (low → high, no octaves) → MIDI notes high → low, each
    string taken at the pitch nearest to where a guitar has it."""
    tones = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}
    notes = []
    for w in (value or "").replace(",", " ").split():
        m = _re_mod.fullmatch(r"([A-Ha-h])([#b♯♭]?)", w)
        if not m:
            return []
        notes.append((tones[m.group(1).upper()] + {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2), 0)) % 12)
    base = {4: [28, 33, 38, 43], 5: [23, 28, 33, 38, 43], 6: [40, 45, 50, 55, 59, 64], 7: [35, 40, 45, 50, 55, 59, 64],
            8: [30, 35, 40, 45, 50, 55, 59, 64]}.get(len(notes))
    if not base:
        return []
    out = []
    for pc, std in zip(notes, base):
        near = std + ((pc - std) % 12)
        out.append(near - 12 if near - std > 6 else near)
    return out[::-1]


def _ug_tuning(artist: str, title: str) -> list:
    """The song's tuning as Ultimate Guitar's best tab of it says — [] when it has none."""
    want_a, want_t = _names.key(artist.split(",")[0]), _names.key(_names.no_brackets(title))
    if not (want_a and want_t):
        return []
    found = _ug_page("https://www.ultimate-guitar.com/search.php", search_type="title",
                     value=f"{artist.split(',')[0]} {_names.no_brackets(title)}").get("results") or []
    mine = [r for r in found if isinstance(r, dict) and r.get("tab_url") and r.get("type") in ("Tabs", "Chords", "Bass Tabs")
            and _names.key(r.get("artist_name")) == want_a and _names.key(_names.no_brackets(r.get("song_name") or "")) == want_t]
    mine.sort(key=lambda r: (r.get("type") != "Tabs", r.get("type") == "Bass Tabs", -(r.get("votes") or 0)))
    for r in mine[:2]:
        meta = (_ug_page(r["tab_url"]).get("tab_view") or {}).get("meta") or {}
        got = _tuning_from_names((meta.get("tuning") or {}).get("value") or "") if isinstance(meta, dict) else []
        if got and r.get("type") != "Bass Tabs":
            return got
    return []


def _tuning_lookup(d: Path, artist: str, title: str, rec="?") -> None:
    """A song's tuning from wherever it is known: its Songsterr tab first
    (`rec`, when the caller already looked), then Ultimate Guitar — each place
    asked once."""
    asked = _stems_tuning_asked(d)
    if _stems_tuning(d) is None:
        if rec == "?":
            rec = _match_song(artist, title)
        _stems_tuning_keep(d, _tab_tuning(rec), "songsterr")
    if not _stems_tuning(d) and "ug" not in asked:
        _stems_tuning_keep(d, _ug_tuning(artist, title), "ug")


def _tab_tuning(rec) -> list:
    tracks = (rec or {}).get("tracks") or []
    i = _guitar_index(rec) if rec else -1
    return list((tracks[i].get("tuning") or []) if 0 <= i < len(tracks) else [])


_TUNING_JOB = {"running": False, "done": 0, "total": 0}


def _tunings_fill() -> None:
    try:
        root = MUSIC_ROOT / "Stems"
        todo = [d for d in sorted(root.glob("*")) if d.is_dir() and not d.name.startswith(".")
                and (_stems_tuning(d) is None or (not _stems_tuning(d) and "ug" not in _stems_tuning_asked(d)))]
        _TUNING_JOB.update(done=0, total=len(todo))
        for d in todo:
            m = _stems_meta(d)
            try:
                if m.get("artist") and m.get("title"):
                    _tuning_lookup(d, m["artist"], m["title"])
                else:
                    _stems_tuning_keep(d, [], "songsterr")
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


# ---- where a separated song is from: the playlist it was downloaded in, and
# whether it is Czech/Slovak or from abroad. Nothing in a file says the second,
# so it is read off the names: letters only Czech and Slovak write (ě š č ř ž
# ů ť ď ň ľ ĺ ŕ), a playlist called Czech, or an artist already known to be
# one. A guess the player can put right — origin.json beside the stems wins.
_STEMS_PLAYLISTS = {"at": 0.0, "map": {}, "root": ""}
_CZ_LETTERS = set("ěščřžůťďňľĺŕ")
_CZ_WORDS = ("cesk", "czech", "slovak", "slovens", "cesko")


def _stems_playlists() -> dict:
    """safe name → the downloaded playlist holding a file of that name (refreshed each minute)."""
    now = time.time()
    if now - _STEMS_PLAYLISTS["at"] < 60 and _STEMS_PLAYLISTS["root"] == str(MUSIC_ROOT):
        return _STEMS_PLAYLISTS["map"]
    found: dict = {}
    try:
        for folder in sorted((MUSIC_ROOT / "Playlists").iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            for f in folder.iterdir():
                if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("."):
                    found.setdefault(_safe_name(f.stem), folder.name)
    except OSError:
        pass
    _STEMS_PLAYLISTS.update(at=now, map=found, root=str(MUSIC_ROOT))
    return found


def _looks_czech(*texts) -> bool:
    for t in texts:
        low = (t or "").lower()
        if _CZ_LETTERS & set(low) or any(w in _deaccent(low) for w in _CZ_WORDS):
            return True
    return False


_TEXT_CZECH: dict = {}


def _text_is_czech(title, artist, duration) -> bool:
    """Whether the song's kept text is Czech or Slovak — the surest sign there is."""
    if not title:
        return False
    f = _lyrics_file(title, artist or "", duration)
    try:
        at = f.stat().st_mtime
    except OSError:
        return False
    hit = _TEXT_CZECH.get(str(f))
    if hit is None or hit[0] != at:
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
            text = (got.get("plain") or "") + " ".join(l.get("text") or "" for l in got.get("lines") or [])
        except (OSError, ValueError, AttributeError, TypeError):
            text = ""
        hit = _TEXT_CZECH[str(f)] = (at, sum(1 for ch in text.lower() if ch in _CZ_LETTERS) >= 4)
    return hit[1]


def _stems_origin_typed(d: Path) -> Optional[str]:
    try:
        o = json.loads((d / "origin.json").read_text(encoding="utf-8")).get("origin")
        return o if o in ("cz", "world") else None
    except (OSError, ValueError, AttributeError):
        return None


def _stems_origins(rows: list) -> None:
    """Fills "origin" ("cz" | "world") and "origin_typed" into the listed songs."""
    artist = lambda r: _deaccent(((r.get("meta") or {}).get("artist") or "").split(",")[0]).lower().strip()
    home = set()
    for r in rows:
        m = r.get("meta") or {}
        r["origin_typed"] = r.pop("_origin", None)
        r["origin"] = r["origin_typed"] or ("cz" if _looks_czech(m.get("artist"), m.get("title"), m.get("album"),
                                                                 r.get("playlist"), r["name"])
                                             or _text_is_czech(m.get("title"), m.get("artist"), r.get("duration"))
                                             else "world")
        if r["origin"] == "cz" and artist(r):
            home.add(artist(r))
    for r in rows:
        if not r["origin_typed"] and artist(r) in home:
            r["origin"] = "cz"
        r["origin_typed"] = bool(r["origin_typed"])


class StemsOrigin(BaseModel):
    origin: str = ""          # "cz", "world", or "" = back to the guess


@app.put("/api/stems/{name}/origin")
def put_stems_origin(name: str, body: StemsOrigin):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    f = d / "origin.json"
    if body.origin in ("cz", "world"):
        f.write_text(json.dumps({"origin": body.origin}), encoding="utf-8")
    elif f.is_file():
        f.unlink()
    return next((x for x in list_stems() if x["name"] == name), {"name": name})


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


# ---- names as they are officially written (names.py). A separated song is
# called what its file was — often a video: an uploader for the band, "(Official
# Video)" in the title — and with such a name neither its text nor its tab is
# found. Each song is put right once: the music catalogue is asked, its answer
# believed only when it names the artist and title we had; otherwise the name
# is at least tidied. What was there before stays in meta.json ("was"), and a
# name typed by hand is never touched.
import names as _names


# A second catalogue, asked where the first knows nothing or to check it:
# MusicBrainz — open, and it tells a studio album from a concert or a best-of.
# It wants to be asked slowly (one question a second) and to know who asks.
_MB_LOCK = threading.Lock()
_MB_LAST = [0.0]


def _mb_get(path: str, **params) -> dict:
    with _MB_LOCK:
        wait = 1.1 - (time.time() - _MB_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _MB_LAST[0] = time.time()
        r = httpx.get("https://musicbrainz.org/ws/2/" + path, params={**params, "fmt": "json"}, timeout=12,
                      headers={"User-Agent": "Pick/1.0 (music practice app; local use)"})
    r.raise_for_status()
    return r.json()


def _mb_song(artist: str, title: str, name: str) -> Optional[dict]:
    """The same question as _official_song, put to MusicBrainz."""
    t = _names.tidy(artist or "", title or name)
    if not (t["artist"] and t["title"]):
        return None
    esc = lambda x: _re_mod.sub(r'(["\\])', r"\\\1", x)
    got = _mb_get("recording", query=f'recording:"{esc(t["title"])}" AND artist:"{esc(t["artist"])}"', limit=8)
    rows = []
    for r in got.get("recordings") or []:
        rels = r.get("releases") or []
        plain = [x for x in rels if (x.get("release-group") or {}).get("primary-type") == "Album"
                 and not (x.get("release-group") or {}).get("secondary-types")]
        rows.append({"title": r.get("title"), "artists": [c.get("name") for c in r.get("artist-credit") or [] if isinstance(c, dict) and c.get("name")],
                     "album": ((plain or rels or [{}])[0]).get("title")})
    return _names.pick(rows, artist or "", title or "", name)


def _mb_albums(artist: str) -> Optional[dict]:
    """The artist's releases as MusicBrainz files them: title key → year and
    kind ("studio", "live", "compilation", "other"). None when it does not
    know the artist."""
    want = _names.key(artist)
    found = _mb_get("artist", query=f'artist:"{artist}"', limit=5).get("artists") or []
    hit = next((a for a in found if _names.key(a.get("name")) == want), None)
    if not hit:
        return None
    out, offset = {}, 0
    while offset < 300:
        page = _mb_get("release-group", artist=hit["id"], type="album|ep", limit=100, offset=offset)
        groups = page.get("release-groups") or []
        for g in groups:
            second = [x.lower() for x in g.get("secondary-types") or []]
            kind = "live" if "live" in second else "compilation" if "compilation" in second else \
                "other" if second or g.get("primary-type") != "Album" else "studio"
            k = _names.key(_names.no_brackets(g.get("title") or ""))
            if k and (k not in out or kind == "studio"):
                out[k] = {"year": (g.get("first-release-date") or "")[:4], "kind": kind}
        offset += 100
        if len(groups) < 100:
            break
    return out


def _official_song(artist: str, title: str, name: str) -> Optional[dict]:
    """The catalogue's own artist, title and album of one song — or None when
    nothing it answers is this song. Raises when it cannot be asked."""
    t = _names.tidy(artist or "", title or name)
    queries = []
    for q in (f"{t['artist'] or ''} {t['title'] or ''}".strip(), _names.clean(name),
              f"{artist or ''} {_names.clean(title or '')}".strip()):
        if q and q not in queries:
            queries.append(q)
    for q in queries:
        rows = [{"title": r.get("title"), "artists": [a.get("name") for a in r.get("artists") or [] if a.get("name")],
                 "album": (r.get("album") or {}).get("name") if isinstance(r.get("album"), dict) else None}
                for r in yt().search(q, filter="songs", limit=5)]
        got = _names.pick(rows, artist or "", title or "", name)
        if got:
            return got
    try:
        return _mb_song(artist, title, name)                # the first catalogue has nothing: the second is asked
    except Exception:                                      # noqa: BLE001
        return None


def _stems_meta_raw(d: Path) -> dict:
    try:
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _names_fix_one(d: Path) -> str:
    """One separated song's name put right. → "official", "tidied", or
    "skipped" (typed by hand, or seen to already)."""
    raw = _stems_meta_raw(d)
    if raw.get("manual") or raw.get("checked"):
        return "skipped"
    m = _stems_meta(d)
    got = _official_song(m.get("artist") or "", m.get("title") or "", d.name)
    if got:
        got["title"] = _names.with_live(got["title"], m.get("title"), d.name)
    new = got or {**_names.tidy_live(m.get("artist") or "", m.get("title") or d.name, d.name), "album": m.get("album")}
    if got and _names.is_live(new.get("title")):          # a concert: the catalogue's album is the studio one
        new["album"] = m.get("album") if _names.is_live(m.get("album")) else None
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps({"artist": new.get("artist"), "title": new.get("title"), "album": new.get("album") or (None if _names.is_live(new.get("title")) else m.get("album")),
                               "checked": True, "official": bool(got),
                               "was": {k: m.get(k) for k in ("artist", "title", "album")}}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "meta.json")
    return "official" if got else "tidied"


def _names_more(d: Path) -> None:
    """With its name right, what the song still lacks is looked for: its tab
    (and by it the tuning), its text."""
    m = _stems_meta(d)
    if not (m.get("artist") and m.get("title")):
        return
    if _stems_tuning(d) is None:
        rec = _match_song(m["artist"], m["title"])
        if rec and int(rec["songId"]) not in {t.get("songId") for t in _pick_tabs()}:
            _picktab_upsert(_rec_to_tab(rec, "Steems"))
        _tuning_lookup(d, m["artist"], m["title"], rec)
    dur = next((x for x in (_audio_tags(f).get("duration") for f in (_stem_file(d, st) for st in STEM_NAMES[:2]) if f) if x), None)
    if not _lyrics_kept(m["title"], m["artist"], dur):
        _lookup_lyrics(m["title"], m["artist"], m.get("album") or "", dur)


def _names_soon(d: Path) -> None:
    """A song just separated: its name, tab and text, behind the scenes."""
    if os.environ.get("SPOTDL_NO_WATCHERS"):
        return

    def run():
        try:
            _names_fix_one(d)
            _names_more(d)
        except Exception:                                  # noqa: BLE001 — the internet; the big run sees to it later
            pass
    threading.Thread(target=run, name="names-one", daemon=True).start()


_NAMES_JOB = {"running": False, "done": 0, "total": 0, "official": 0, "tidied": 0, "failed": 0, "more": False}


def _names_fix_all(more: bool) -> None:
    try:
        root = MUSIC_ROOT / "Stems"
        todo = [d for d in sorted(root.glob("*")) if d.is_dir() and not d.name.startswith(".")
                and not (_stems_meta_raw(d).get("manual") or _stems_meta_raw(d).get("checked"))]
        _NAMES_JOB.update(done=0, total=len(todo), official=0, tidied=0, failed=0, more=more)
        for d in todo:
            try:
                r = _names_fix_one(d)
                if r in ("official", "tidied"):
                    _NAMES_JOB[r] += 1
            except Exception:                              # noqa: BLE001 — not marked: asked again next time
                _NAMES_JOB["failed"] += 1
            if more:
                try:
                    _names_more(d)
                except Exception:                          # noqa: BLE001
                    pass
            _NAMES_JOB["done"] += 1
            time.sleep(0.15)
    finally:
        _NAMES_JOB["running"] = False


@app.get("/api/stems-names")
def stems_names_state():
    root = MUSIC_ROOT / "Stems"
    dirs = [d for d in (root.glob("*") if root.is_dir() else []) if d.is_dir() and not d.name.startswith(".")]
    left = sum(1 for d in dirs if not (_stems_meta_raw(d).get("manual") or _stems_meta_raw(d).get("checked")))
    return {**_NAMES_JOB, "songs": len(dirs), "left": left}


@app.post("/api/stems-names/fix")
def stems_names_fix(more: bool = True):
    """Every separated song whose name was not seen to yet is put right, in the
    background; with `more`, its tab and text are looked for as well."""
    if not _NAMES_JOB["running"]:
        _NAMES_JOB.update(running=True, done=0, total=0)
        threading.Thread(target=_names_fix_all, args=(more,), name="stems-names", daemon=True).start()
    return dict(_NAMES_JOB)


@app.delete("/api/stems-names")
def stems_names_undo():
    """The names as they were before they were put right (they are not asked about again)."""
    root, n = MUSIC_ROOT / "Stems", 0
    for d in (root.glob("*") if root.is_dir() else []):
        raw = _stems_meta_raw(d) if d.is_dir() else {}
        if raw.get("checked") and isinstance(raw.get("was"), dict) and not raw.get("manual"):
            (d / "meta.json").write_text(json.dumps({**raw["was"], "checked": True, "restored": True}, ensure_ascii=False), encoding="utf-8")
            n += 1
    return {"restored": n}


# ---- an artist's albums with their songs, and of each song what there
# already is — the list beside an open mix.
_DISCO_CACHE: dict = {}


def _discography(name: str) -> Optional[dict]:
    key = _name_key(name)
    hit = _DISCO_CACHE.get(key)
    if hit and time.time() - hit["at"] < 6 * 3600:
        return hit["data"]
    client = yt()
    hits = client.search(name, filter="artists", limit=3)
    best = next((h for h in hits if _name_key(h.get("artist") or "") == key), hits[0] if hits else None)
    if not best or not best.get("browseId"):
        return None
    a = client.get_artist(best["browseId"])
    sec = a.get("albums") or {}
    rows = list(sec.get("results") or [])
    if sec.get("params") and sec.get("browseId"):
        try:
            rows = client.get_artist_albums(sec["browseId"], sec["params"]) or rows
        except Exception:                                  # noqa: BLE001 — the first few are there all the same
            pass
    rows = [r for r in rows if r.get("browseId")][:90]

    def album(r):
        try:
            al = client.get_album(r["browseId"])
        except Exception:                                  # noqa: BLE001
            return None
        return {"title": al.get("title") or r.get("title"), "year": al.get("year") or r.get("year"),
                "browseId": r["browseId"], "thumbnail": _best_thumb(al.get("thumbnails") or r.get("thumbnails")),
                "tracks": [{"title": t.get("title") or "", "videoId": t.get("videoId") or "",
                            "duration_seconds": t.get("duration_seconds") or 0,
                            "artists": ", ".join(x["name"] for x in (t.get("artists") or []) if x.get("name")) or a.get("name") or name}
                           for t in al.get("tracks") or [] if t.get("title")]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        albums = [x for x in pool.map(album, rows) if x and len(x["tracks"]) >= 4]     # singles are not albums
    # The second catalogue says which of these are the studio albums, and when
    # they first came out (the first gives the year of whatever reissue it has).
    try:
        known = _mb_albums(a.get("name") or name)
    except Exception:                                      # noqa: BLE001 — without it: all in one list, as told
        known = None
    seen = {}
    for x in albums:
        k = _names.key(_names.no_brackets(x["title"] or "")) + ("|live" if _names.is_live(x["title"]) else "")
        info = (known or {}).get(k.split("|")[0])
        x["kind"] = "live" if _names.is_live(x["title"]) else info["kind"] if info else "studio" if known is None else "other"
        if info and info["year"]:
            x["year"] = info["year"]
        # one album in several editions: the plain one, or the one with the fewest additions
        old = seen.get(k)
        plain = lambda y: ((1, len(y["tracks"])) if _names.no_brackets(y["title"] or "") != (y["title"] or "") else (0, -len(y["tracks"])))
        if old is None or plain(x) < plain(old):
            seen[k] = x
    albums = sorted(seen.values(), key=lambda x: (x["kind"] != "studio", str(x.get("year") or "9999")))
    data = {"artist": a.get("name") or name, "thumbnail": _best_thumb(a.get("thumbnails")), "albums": albums,
            "checked": known is not None}
    _DISCO_CACHE[key] = {"at": time.time(), "data": data}
    if len(_DISCO_CACHE) > 60:
        _DISCO_CACHE.pop(next(iter(_DISCO_CACHE)))
    return data


@app.get("/api/artist/discography")
def artist_discography(name: str):
    """The artist's albums, oldest first, each with its songs — and of every
    song whether it is downloaded, separated, has its tab and its text."""
    if not name.strip():
        return JSONResponse({"error": "Chybí jméno interpreta."}, status_code=400)
    try:
        data = _discography(name.strip())
    except Exception:                                      # noqa: BLE001
        return JSONResponse({"error": "Alba se nepodařilo načíst."}, status_code=502)
    if not data:
        return JSONResponse({"error": "Interpret nenalezen."}, status_code=404)
    al = _have_all()
    return {**data, "albums": [{**x, "tracks": [{**t, "have": _have_one(SaveSongRequest(
        title=t["title"], artists=t["artists"], videoId=t["videoId"], duration=t["duration_seconds"]), al)}
        for t in x["tracks"]]} for x in data["albums"]]}


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


# Songs separated elsewhere and put into Stems by hand (stems_import.py): taken in
# when the list is asked for and the folder has changed since the last look.
import stems_import as _stems_import
_STEMS_IMPORT = {"sig": None}
_STEMS_IMPORT_LOCK = threading.Lock()


def _stems_take_in(stems_root: Path) -> list:
    with _STEMS_IMPORT_LOCK:
        sig = _stems_import.signature(stems_root)
        if sig == _STEMS_IMPORT["sig"]:
            return []
        try:
            got = _stems_import.run(stems_root, STEM_NAMES + GUITAR_PARTS)
        except OSError:
            return []
        _STEMS_IMPORT["sig"] = _stems_import.signature(stems_root)
    if not os.environ.get("SPOTDL_NO_WATCHERS"):
        threading.Thread(target=_stems_albums_fill, args=(stems_root,), daemon=True).start()
    return got["added"]


# What album each song taken in belongs to: its band's albums are asked once, and the
# song is found among them by its title — the first studio album holding it, else any.
_STEMS_ALBUMS_LOCK = threading.Lock()


def _stems_albums_fill(stems_root: Path) -> int:
    if not _STEMS_ALBUMS_LOCK.acquire(blocking=False):
        return 0
    try:
        bands: dict = {}
        for d in sorted(stems_root.glob("*")):
            raw = _stems_meta_raw(d) if d.is_dir() and not d.name.startswith(".") else {}
            if raw.get("imported") and not raw.get("album") and not raw.get("album_looked") and raw.get("artist"):
                bands.setdefault(raw["artist"], []).append((d, raw))
        done = 0
        for band, rows in bands.items():
            try:
                data = _discography(band)
            except Exception:                              # noqa: BLE001 — offline: asked again next time
                continue
            where: dict = {}
            for al in (data or {}).get("albums") or []:
                if _names.is_live(al.get("title") or "") or "on:stage" in (al.get("title") or "").lower():
                    continue                               # a concert: not the album a studio song is from
                for t in al.get("tracks") or []:
                    if _names.is_live(t.get("title") or ""):
                        continue
                    k = _names.key(_names.no_brackets(t.get("title") or ""))
                    if k and (k not in where or (where[k]["kind"] != "studio" and al.get("kind") == "studio")):
                        where[k] = {"title": al.get("title"), "kind": al.get("kind")}
            for d, raw in rows:
                hit = where.get(_names.key(_names.no_brackets(raw.get("title") or "")))
                if not hit:                                # not on the band's own albums: the song asked for by itself
                    try:
                        got = _official_song(band, raw.get("title") or "", d.name)
                    except Exception:                      # noqa: BLE001
                        got = None
                    if got and got.get("album") and not _names.is_live(got["album"]):
                        hit = {"title": got["album"]}
                album = _re_mod.sub(r"\s*[(\[][^)\]]*(remaster|deluxe|edition|reissue|expanded|anniversary)[^)\]]*[)\]]", "",
                                    hit["title"] or "", flags=_re_mod.I).strip() if hit else None
                raw.update(album=album or None, album_looked=True, album_auto=bool(album))
                try:
                    tmp = d / "meta.json.tmp"
                    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                    tmp.replace(d / "meta.json")
                    done += bool(hit)
                except OSError:
                    pass
        return done
    finally:
        _STEMS_ALBUMS_LOCK.release()


@app.get("/api/stems")
def list_stems():
    stems_root = MUSIC_ROOT / "Stems"
    if not stems_root.is_dir():
        return []
    _stems_take_in(stems_root)
    try:
        _storage.link_sync()                # a card put in since: its songs are listed too
    except OSError:
        pass
    out = []
    playlists = _stems_playlists()
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
        # A set taken in from another separator has the parts it was split into (often no piano,
        # the guitar as lead and rhythm): that is the whole of it.
        if missing and _stems_meta_raw(d).get("imported"):
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
            "playlist": playlists.get(d.name),
            "imported": bool(_stems_meta_raw(d).get("imported")),   # put into Stems by hand
            "_origin": _stems_origin_typed(d),
        })
    _stems_origins(out)
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
async def sing_line(request: Request, text: str, lang: str = "auto", song: str = "", key: str = "", strict: bool = False):
    """One sung line (16 kHz mono s16le) against the words it should have been.
    With `song` and `key` (the line's own, the page's choice) what was heard
    is kept with the song's stems, to be gone through later."""
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
    got = _sing.compare(text, heard, strict=strict)        # strict: said, to learn the pronunciation — sung is forgiven more
    d = _sing_dir(song) if song and key else None
    if d is not None:
        try:
            _sing.keep_sung(d, key[:300], {**got, "at": int(time.time())})
        except OSError:
            pass
    return got


@app.get("/api/stems/{name}/sing/words")
def sing_words(name: str):
    """What was heard of each lyric line the last time it was sung."""
    d = _sing_dir(name)
    return _sing.sung_words(d) if d is not None else {}


@app.delete("/api/stems/{name}/sing/words")
def sing_words_forget(name: str):
    d = _sing_dir(name)
    if d is not None:
        (d / "sing-words.json").unlink(missing_ok=True)
    return {}


# ---- the words the pronunciation practice caught: one list across all songs

def _hard_words_file() -> Path:
    return CONFIG_PATH.parent / "speak-words.json"


def _hard_words_rows():
    rows = list(_sing.hard_words(_hard_words_file()).values())
    rows.sort(key=lambda r: (r.get("done", False), -r.get("at", 0)))
    return rows


class HardWord(BaseModel):
    word: str
    lang: str = "en"
    ok: bool = False
    heard: str = ""
    song: str = ""
    learned: bool = False


@app.get("/api/speak/words")
def speak_words():
    """The mispronounced words: the ones still to be learnt first, the newest of them on top."""
    return _hard_words_rows()


@app.post("/api/speak/words")
def speak_word(w: HardWord):
    lang = w.lang if w.lang in ("en", "cs") else "en"
    try:
        _sing.hard_word(_hard_words_file(), w.word[:60], lang, w.ok, w.heard, w.song, w.learned)
    except OSError:
        return JSONResponse({"error": "Slovo se nepodařilo uložit."}, status_code=500)
    return _hard_words_rows()


@app.delete("/api/speak/words")
def speak_words_forget(word: str = "", lang: str = "en", done: bool = False):
    """One word off the list — or, with `done`, every one already learnt."""
    f = _hard_words_file()
    got = _sing.hard_words(f)
    left = {k: r for k, r in got.items() if not ((done and r.get("done")) or (word and k == f"{lang}|{_sing._plain(word)}"))}
    if len(left) != len(got):
        f.write_text(json.dumps(left, ensure_ascii=False), encoding="utf-8")
    return _hard_words_rows()


# ---- a voice to read the lyrics aloud: the user's own, cloned in the Voicebox
# app (its server answers on this computer while the app is open). A line
# takes it some twenty seconds, so every line is made once, in the background,
# and kept; the page plays what is ready and reads the rest with the system voice.
VOICEBOX_URL = "http://127.0.0.1:17493"
VOICEBOX_LANGS = {"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it", "he", "ar", "da", "el", "fi", "hi", "ms", "nl",
                  "no", "pl", "sv", "sw", "tr"}
_TTS_QUEUE: list = []          # (file, profile, lang, text, style), the next one first
_TTS_LOCK = threading.Lock()
_TTS = {"thread": None, "error": None, "opened": 0.0}


# Which of Voicebox's models reads. Qwen clones from the sample *and its
# transcript*, and with a transcript that does not fit the sample it speaks
# leftovers of it before every line; Chatterbox goes by the sound alone.
VOICEBOX_ENGINE = "chatterbox"


# English goes through the Turbo model when Voicebox has it: measured on this
# Mac it reads a line in about 8 s where the multilingual one takes 30–50, and
# as cleanly. (LuxTTS is faster still but swallows the first words; Qwen talks
# over the line.) Everything else stays with the multilingual model.
VOICEBOX_FAST = {"engine": "chatterbox_turbo", "model": "chatterbox-turbo"}
_TTS_FAST = {"at": 0.0, "have": False}


# ---- Pick's own voice. A voice is a folder — voices/<id>/ with ref.wav (a few
# seconds of the person speaking), ref.txt (what is said there) and name.txt —
# and the model that speaks with it is OmniVoice (Apache-2.0, 600+ languages,
# Czech among them), run by tts_worker.py in an environment of its own.
# Nothing else has to be open. Voicebox stays as the way for voices that live
# only there.
OWN_TTS_PYTHON = HOME / ".local" / "venvs" / "pick-tts" / "bin" / "python"
OWN_TTS_STEPS = 16                 # measured on an M1 Pro: about 8 s a line, and as clean as 32 steps
_OWN_TTS = {"proc": None}
_OWN_TTS_LOCK = threading.Lock()


def _own_voices_dir() -> Path:
    return CONFIG_PATH.parent / "voices"


def _own_voice(profile: str) -> Optional[dict]:
    d = _own_voices_dir() / profile
    if not profile or "/" in profile or profile.startswith(".") or not (d / "ref.wav").is_file() or not OWN_TTS_PYTHON.is_file():
        return None
    read = lambda n: (d / n).read_text(encoding="utf-8").strip() if (d / n).is_file() else ""
    return {"id": profile, "name": read("name.txt") or profile, "ref_audio": str(d / "ref.wav"), "ref_text": read("ref.txt")}


def _own_voices() -> list:
    root = _own_voices_dir()
    return [v for v in (_own_voice(d.name) for d in (sorted(root.iterdir()) if root.is_dir() else []) if d.is_dir()) if v]


def _voice_ref_make(src: Path, dest: Path, lang: str = "auto") -> str:
    """A voice's reference out of any recording of it: its first few seconds,
    cut at a pause (a clip that ends mid-word makes the voice stumble into
    every line), and what is said in them written down. → the text."""
    ff = ffmpeg_bin()
    probe = subprocess.run([ff, "-hide_banner", "-i", str(src), "-t", "14", "-af", "silencedetect=noise=-32dB:d=0.25", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60).stderr
    pauses = [float(x) for x in _re_mod.findall(r"silence_start: ([0-9.]+)", probe)]
    # the longest that still ends at a pause: more of the voice makes a better likeness
    end = next((t + 0.12 for t in reversed(pauses) if 5.0 <= t <= 10.5), None) or next((t + 0.12 for t in reversed(pauses) if 3.0 <= t < 5.0), 9.0)
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-v", "error", "-y", "-i", str(src), "-t", f"{end:.2f}", "-ac", "1", "-ar", "24000", str(dest / "ref.wav")],
                   check=True, timeout=60)
    pcm = subprocess.run([ff, "-v", "quiet", "-i", str(dest / "ref.wav"), "-ac", "1", "-ar", str(_voice.RATE), "-f", "s16le", "-"],
                         capture_output=True, timeout=60).stdout
    text = _voice.transcribe(pcm, lang=lang if lang in ("en", "cs") else "auto", prompt="")
    (dest / "ref.txt").write_text(text, encoding="utf-8")
    return text


# ---- how a line is said. The model clones a voice, not a mood: what it can
# be told is how fast to speak and to whisper, and the rest of a manner is
# made of the sound afterwards — lower, driven, compressed, warm, with a room
# around it. (A voice that is truly angry comes of a sample spoken in anger:
# "+ hlas" takes one.) Every line, plain ones too, leaves at the same
# loudness — a quiet sample used to make a quiet voice.
def _lower(by: float) -> str:
    return f"asetrate={24000 * by:.0f},aresample=24000,atempo={1 / by:.4f}"


TTS_STYLES = {
    "": {"name": "Přirozený"},
    "metal": {"name": "Metalový", "sys": (0.95, 0.55),
              "fx": _lower(0.9) + ",highpass=f=70,acompressor=threshold=-26dB:ratio=6:attack=3:release=90:makeup=5,"
                    "volume=10dB,asoftclip=type=tanh,equalizer=f=150:t=q:w=1:g=4,equalizer=f=2800:t=q:w=1.2:g=3,aecho=0.8:0.5:38:0.22"},
    "aggressive": {"name": "Agresivní", "speed": 1.15, "sys": (1.2, 0.75),
                   "fx": _lower(0.95) + ",highpass=f=120,acompressor=threshold=-30dB:ratio=10:attack=1:release=60:makeup=8,"
                         "volume=15dB,asoftclip=type=tanh,equalizer=f=3200:t=q:w=1.2:g=6,highshelf=f=7000:g=-3"},
    "angry": {"name": "Naštvaný", "speed": 1.08, "sys": (1.1, 0.7),
              "fx": _lower(0.96) + ",highpass=f=90,acompressor=threshold=-28dB:ratio=8:attack=2:release=70:makeup=6,"
                    "volume=7dB,asoftclip=type=tanh,equalizer=f=2500:t=q:w=1.2:g=4"},
    "romantic": {"name": "Romantický", "speed": 0.88, "sys": (0.85, 1.05),
                 "fx": "acompressor=threshold=-22dB:ratio=2.5:attack=15:release=200:makeup=2,equalizer=f=220:t=q:w=1:g=3,"
                       "highshelf=f=5000:g=-4,aecho=0.8:0.6:60|110:0.3|0.2"},
    "calm": {"name": "Klidný", "speed": 0.85, "sys": (0.8, 0.9),
             "fx": _lower(0.97) + ",acompressor=threshold=-22dB:ratio=2:attack=20:release=250:makeup=1,highshelf=f=6000:g=-3"},
    "whisper": {"name": "Šeptaný", "instruct": "whisper", "sys": (0.9, 1.0)},
}
TTS_LOUD = 0.14                    # the RMS every line leaves at (about −17 dBFS)
TTS_MADE = "2"                     # what the lines kept on disk were made by: raised when the making changes


def _tts_style(style: str) -> dict:
    return TTS_STYLES.get(style or "") or TTS_STYLES[""]


def _tts_finish(audio: bytes, style: str = "") -> bytes:
    """A line as it is kept: in the manner asked for, and as loud as every
    other. What is no sound at all is given back as it came."""
    try:
        import io
        import wave
        import numpy as np
        if audio[:4] != b"RIFF":
            return audio
        fx = _tts_style(style).get("fx")
        if fx:
            done = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "wav", "-i", "pipe:0", "-af", fx, "-ac", "1", "-ar", "24000",
                                   "-f", "wav", "pipe:1"], input=audio, capture_output=True, timeout=60).stdout
            # ffmpeg cannot go back to write the lengths into a piped header: read what follows it
            at = done.find(b"data")
            x, rate = (np.frombuffer(done[at + 8:len(done) - (len(done) - at - 8) % 2], dtype=np.int16), 24000) if at > 0 else (None, 0)
        else:
            x = None
        if x is None or not len(x):
            with wave.open(io.BytesIO(audio)) as w:
                rate, ch = w.getframerate(), w.getnchannels()
                x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if ch > 1:
                x = x.reshape(-1, ch)[:, 0]
        x = x.astype(np.float32) / 32768.0
        loud = x[np.abs(x) > 0.004]                         # the pauses do not count
        rms = float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0
        if rms > 1e-4:
            x = x * min(TTS_LOUD / rms, 40.0)
            over = np.abs(x) > 0.8                           # the few peaks above are bent, not cut
            x[over] = np.sign(x[over]) * (0.8 + 0.19 * np.tanh((np.abs(x[over]) - 0.8) / 0.19))
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes((x * 32767.0).astype(np.int16).tobytes())
        return out.getvalue()
    except Exception:                                      # noqa: BLE001 — better the line as it came than none
        return audio


def _own_say(voice: dict, lang: str, text: str, style: str = "") -> bytes:
    """One line in one of Pick's own voices. The worker is started when there
    is none (the first line then waits for the model to load)."""
    how = _tts_style(style)
    out = Path(tempfile.gettempdir()) / f"pick-tts-{uuid.uuid4().hex}.wav"
    with _OWN_TTS_LOCK:
        proc = _OWN_TTS["proc"]
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen([str(OWN_TTS_PYTHON), str(Path(__file__).with_name("tts_worker.py"))], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
            _OWN_TTS["proc"] = proc
        try:
            proc.stdin.write(json.dumps({"text": text, "lang": lang, "ref_audio": voice["ref_audio"], "ref_text": voice["ref_text"],
                                         "out": str(out), "steps": OWN_TTS_STEPS, "instruct": how.get("instruct"),
                                         "speed": how.get("speed")}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            answer = proc.stdout.readline()
        except (OSError, ValueError):
            answer = ""
        if not answer:                                     # it went away mid-line: a fresh one next time
            _OWN_TTS["proc"] = None
            raise RuntimeError("Hlasový model se nespustil.")
    got = json.loads(answer)
    if not got.get("ok"):
        raise RuntimeError(got.get("error") or "Hlas se nepodařilo vytvořit.")
    try:
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def _tts_engine(lang: str, profile: str = "") -> str:
    if profile and _own_voice(profile):
        return f"omnivoice-{OWN_TTS_STEPS}"
    if not (lang or "").lower().startswith("en") or os.environ.get("SPOTDL_NO_WATCHERS"):
        return VOICEBOX_ENGINE
    if time.time() - _TTS_FAST["at"] > 300:
        try:
            models = httpx.get(VOICEBOX_URL + "/models/status", timeout=4).json().get("models") or []
            _TTS_FAST.update(at=time.time(), have=any(m.get("model_name") == VOICEBOX_FAST["model"] and m.get("downloaded") for m in models))
        except Exception:                                  # noqa: BLE001 — asked again with the next line
            pass
    return VOICEBOX_FAST["engine"] if _TTS_FAST["have"] else VOICEBOX_ENGINE


def _tts_file(profile: str, lang: str, text: str, style: str = "") -> Path:
    key = f"{_tts_engine(lang, profile)}|{TTS_MADE}|{style if style in TTS_STYLES else ''}|{profile}|{lang}|{' '.join(text.split())}"
    return CONFIG_PATH.parent / "tts" / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:24] + ".wav")


def _voicebox_profiles():
    """The voices there are: Pick's own first, then Voicebox's (those it has
    under the same id are Pick's now). None when there is neither."""
    own = [{"id": v["id"], "name": v["name"], "kind": "cloned", "engine": "pick"} for v in _own_voices()]
    theirs = _voicebox_only()
    if theirs is None:
        return own or None
    return own + [p for p in theirs if p["id"] not in {v["id"] for v in own}]


def _voicebox_only():
    """The voices of Voicebox, or None when its server does not answer."""
    try:
        r = httpx.get(VOICEBOX_URL + "/profiles", timeout=2.5)
        return [{"id": p["id"], "name": p.get("name") or "?", "kind": p.get("voice_type") or ""} for p in r.json()] if r.status_code == 200 else None
    except Exception:                                      # noqa: BLE001 — not running
        return None


def _voicebox_say(profile: str, lang: str, text: str, style: str = "") -> bytes:
    own = _own_voice(profile)
    if own:
        return _own_say(own, lang, text, style)
    r = httpx.post(VOICEBOX_URL + "/generate/stream", timeout=600,
                   json={"profile_id": profile, "text": text, "language": lang, "engine": _tts_engine(lang)})
    if r.status_code != 200 or r.content[:4] != b"RIFF":
        raise RuntimeError(f"Voicebox: {r.status_code}")
    return r.content


def _tts_sounds_right(audio: bytes, lang: str, text: str) -> bool:
    """Whether the voice said the line and nothing much else — whisper's
    reading of it against the text. Anything that cannot be checked passes."""
    try:
        import io
        import wave
        import numpy as np
        with wave.open(io.BytesIO(audio)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if ch > 1:
            x = x.reshape(-1, ch)[:, 0]
        pcm = x[(np.arange(int(len(x) * _voice.RATE / rate)) * rate / _voice.RATE).astype(int)].tobytes()
        if _voice.names_available() or len(pcm) < _voice.RATE // 2:
            return True
        heard = _voice.transcribe(pcm, lang=lang if lang in ("en", "cs") else "auto", prompt="")
        got = _sing.compare(text, heard)
        said, want = [w.lower() for w in _sing.words(heard)], [w.lower() for w in _sing.words(text)]
        # a word of its own before the line ("Yeah, …") is a slip as well
        led = len(said) == len(want) + 1 and said[1:] == want
        return got["total"] == 0 or (got["ok"] >= got["total"] * 0.6 and len(said) <= got["total"] * 2 + 2 and not led)
    except Exception:                                      # noqa: BLE001
        return True


def _voicebox_open() -> None:
    """Voicebox brought up in the background, once a minute at most. The app
    can be running with its voice server gone (it then answers nothing, and
    opening an app that is open does nothing): asked a second time in a row,
    it is closed first and started afresh."""
    if time.time() - _TTS["opened"] > 60:
        again = time.time() - _TTS["opened"] < 300
        _TTS["opened"] = time.time()
        try:
            if again and subprocess.run(["pgrep", "-x", "voicebox"], capture_output=True).returncode == 0:
                subprocess.run(["osascript", "-e", 'quit app "Voicebox"'], capture_output=True, timeout=15)
                time.sleep(2)
            subprocess.Popen(["open", "-g", "-a", "Voicebox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass


def _tts_drain() -> None:
    misses = 0
    while True:
        with _TTS_LOCK:
            if not _TTS_QUEUE:
                _TTS["thread"] = None
                return
            f, profile, lang, text, style = _TTS_QUEUE[0]
        try:
            if not f.is_file():
                audio = _voicebox_say(profile, lang, text, style)
                if not _tts_sounds_right(audio, lang, text):   # a slip of the model: once more, and that one is kept
                    audio = _voicebox_say(profile, lang, text, style)
                audio = _tts_finish(audio, style)
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_bytes(audio)
                tmp.replace(f)
            _TTS["error"], misses = None, 0
        except Exception as e:                             # noqa: BLE001
            misses += 1
            _TTS["error"] = "Voicebox neodpovídá — je otevřený?" if isinstance(e, httpx.HTTPError) else str(e)
            if misses < 4 and isinstance(e, httpx.ConnectError) and not _own_voice(profile):
                _voicebox_open()
                time.sleep(8)
                continue                                   # the same line again, once the app is up
            if misses >= 4:                                # it is not coming: nothing is left waiting for it
                with _TTS_LOCK:
                    _TTS_QUEUE.clear()
                    _TTS["thread"] = None
                return
        with _TTS_LOCK:
            if _TTS_QUEUE and _TTS_QUEUE[0][0] == f:
                _TTS_QUEUE.pop(0)


class TtsRequest(BaseModel):
    lines: List[str] = []
    lang: str = "en"
    profile: str = ""
    first: Optional[str] = None       # the line that is wanted now: made before the others
    style: str = ""                   # the manner it is said in (TTS_STYLES)


@app.get("/api/tts/voices")
def tts_voices():
    got = _voicebox_profiles()
    own = bool(_own_voices())
    # with voices of its own Pick needs nobody: "voicebox" then only says the voices are there
    return {"voicebox": got is not None, "profiles": got or [], "own": own,
            "styles": [{"id": k, "name": v["name"], "sys": v.get("sys")} for k, v in TTS_STYLES.items()],
            "installed": Path("/Applications/Voicebox.app").exists() and not own}


@app.post("/api/tts/voice")
async def tts_voice_add(file: UploadFile = File(...), name: str = "", lang: str = "auto"):
    """A new voice of Pick's own, out of any recording of someone speaking:
    its first seconds become the reference, and what is said there is written
    down (to be put right by hand when it was misheard)."""
    if not OWN_TTS_PYTHON.is_file():
        return JSONResponse({"error": "Hlasový model Picku není nainstalovaný."}, status_code=503)
    data = await file.read()
    if len(data) < 4000 or len(data) > 200 * 1024 * 1024:
        return JSONResponse({"error": "Nahrávka je prázdná nebo příliš velká."}, status_code=400)
    vid = uuid.uuid4().hex[:16]
    dest = _own_voices_dir() / vid
    src = Path(tempfile.gettempdir()) / f"pick-voice-{vid}{Path(file.filename or 'x.wav').suffix[:8] or '.wav'}"
    try:
        src.write_bytes(data)
        text = await asyncio.get_running_loop().run_in_executor(None, lambda: _voice_ref_make(src, dest, lang))
        if not (dest / "ref.wav").is_file() or (dest / "ref.wav").stat().st_size < 20000:
            raise RuntimeError("v nahrávce není dost zvuku")
        (dest / "name.txt").write_text((name or "").strip()[:60] or "Nový hlas", encoding="utf-8")
    except Exception as e:                                 # noqa: BLE001
        shutil.rmtree(dest, ignore_errors=True)
        return JSONResponse({"error": f"Z nahrávky se hlas nepodařilo připravit ({e})."}, status_code=400)
    finally:
        src.unlink(missing_ok=True)
    v = _own_voice(vid)
    dur = _audio_tags(dest / "ref.wav").get("duration")
    return {"id": vid, "name": v["name"], "ref_text": text, "seconds": dur}


# ---- a voice made with the guide: the player reads a Czech text and an English
# one, and — best of all for singing — sings a little. Reading aloud needs a
# short clip and its words (ref.wav, ref.txt, from the Czech reading); singing
# needs as much of the voice as the model takes, 25 seconds: what was sung
# first, then the two readings, pauses cut out, at one steady loudness
# (sing.wav). The readings are kept whole as well, so a better way of using
# them later needs no new recording.
GUIDE_TEXTS = {
    "cs": "Příliš žluťoučký kůň úpěl ďábelské ódy. Dnes večer si vezmu kytaru, zahraju pár tichých akordů "
          "a potom to pořádně rozjedu. Hudba mi dává sílu, radost i klid, a proto hraju každý den.",
    "en": "The quick brown fox jumps over the lazy dog. Tonight I will pick up my guitar, play a few quiet chords "
          "and then turn it up loud. Music gives me strength, joy and peace, and that is why I play every day.",
}
_GUIDE_CLEAN = "highpass=f=70,silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_duration=0.45:stop_threshold=-45dB,loudnorm=I=-19:TP=-2:LRA=11"


def _guide_clip(src: Path, dest: Path, rate: int = 44100, limit: float = 40.0) -> float:
    """A take tidied — rumble and pauses out, steady loudness. → its length in seconds."""
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(src), "-t", f"{limit}", "-ac", "1", "-af", _GUIDE_CLEAN,
                    "-ar", str(rate), str(dest)], capture_output=True, timeout=120)
    try:
        return max(0.0, (dest.stat().st_size - 44) / (rate * 2))
    except OSError:
        return 0.0


@app.get("/api/tts/voice-guide")
def tts_voice_guide_texts():
    return {"texts": GUIDE_TEXTS, "ready": OWN_TTS_PYTHON.is_file(), "sing": _svc_ready()}


@app.post("/api/tts/voice-guide")
async def tts_voice_guided(cs: UploadFile = File(...), en: UploadFile = File(...), sing: Optional[UploadFile] = File(None),
                           name: str = ""):
    if not OWN_TTS_PYTHON.is_file():
        return JSONResponse({"error": "Hlasový model Picku není nainstalovaný."}, status_code=503)
    vid = uuid.uuid4().hex[:16]
    dest = _own_voices_dir() / vid
    tmp = Path(tempfile.mkdtemp(prefix="pick-guide-"))
    try:
        dest.mkdir(parents=True)
        lens = {}
        for key, up in (("cs", cs), ("en", en), ("sing", sing)):
            if up is None:
                continue
            data = await up.read()
            if len(data) > 200 * 1024 * 1024:
                raise RuntimeError("nahrávka je příliš velká")
            raw = tmp / f"{key}{Path(up.filename or 'x.webm').suffix[:8] or '.webm'}"
            raw.write_bytes(data)
            lens[key] = _guide_clip(raw, dest / f"read-{key}.wav" if key != "sing" else tmp / "sung.wav")
        for key, label in (("cs", "českém"), ("en", "anglickém")):
            if lens.get(key, 0) < 5.0:
                raise RuntimeError(f"v {label} čtení je málo hlasu (aspoň 5 vteřin) — zkus to blíž k mikrofonu")
        if "sing" in lens and lens["sing"] < 3.0:
            lens.pop("sing")                                   # too little to be of use: the readings will do
        # for reading aloud
        text = await asyncio.get_running_loop().run_in_executor(None, lambda: _voice_ref_make(dest / "read-cs.wav", dest, "cs"))
        if not (dest / "ref.wav").is_file() or (dest / "ref.wav").stat().st_size < 20000:
            raise RuntimeError("z českého čtení se nepodařilo vzít ukázku")
        # for singing
        parts = ([tmp / "sung.wav"] if "sing" in lens else []) + [dest / "read-cs.wav", dest / "read-en.wav"]
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-t", "25", "-ac", "1", "-ar", str(SVC_SR), str(dest / "sing.wav")], capture_output=True, timeout=120)
        (dest / "name.txt").write_text((name or "").strip()[:60] or "Můj hlas", encoding="utf-8")
        (dest / "guide.json").write_text(json.dumps({"at": time.time(), "seconds": lens}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:                                 # noqa: BLE001
        shutil.rmtree(dest, ignore_errors=True)
        return JSONResponse({"error": f"Hlas se nepodařilo připravit: {e}."}, status_code=400)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"id": vid, "name": (dest / "name.txt").read_text(encoding="utf-8"), "ref_text": text, "seconds": lens,
            "sung": "sing" in lens, "for_singing": (dest / "sing.wav").is_file()}


class VoiceEdit(BaseModel):
    name: Optional[str] = None
    ref_text: Optional[str] = None


@app.put("/api/tts/voice/{vid}")
def tts_voice_edit(vid: str, body: VoiceEdit):
    """The voice's name, or what its reference really says."""
    v = _own_voice(vid)
    if not v:
        return JSONResponse({"error": "Hlas nenalezen."}, status_code=404)
    d = _own_voices_dir() / vid
    if body.name is not None and body.name.strip():
        (d / "name.txt").write_text(body.name.strip()[:60], encoding="utf-8")
    if body.ref_text is not None and body.ref_text.strip():
        (d / "ref.txt").write_text(" ".join(body.ref_text.split())[:600], encoding="utf-8")
        os.utime(d / "ref.wav")                            # the worker works the voice out again with the new words
    return _own_voice(vid)


@app.delete("/api/tts/voice/{vid}")
def tts_voice_delete(vid: str):
    if not _own_voice(vid):
        return JSONResponse({"error": "Hlas nenalezen."}, status_code=404)
    shutil.rmtree(_own_voices_dir() / vid, ignore_errors=True)
    return {"ok": True}


@app.post("/api/tts/open")
def tts_open():
    """Voicebox started in the background (its voices are there only while it runs)."""
    # asked for by hand while the app runs without answering: that is the case to start it afresh
    running = subprocess.run(["pgrep", "-x", "voicebox"], capture_output=True).returncode == 0
    _TTS["opened"] = time.time() - 120 if running and _voicebox_profiles() is None else 0.0
    _voicebox_open()
    return {"ok": True}


@app.post("/api/tts/prepare")
def tts_prepare(req: TtsRequest):
    """Which of these lines the chosen voice has already read (each with the
    address of its audio); the others are queued to be made."""
    if not req.profile:
        return JSONResponse({"error": "Chybí hlas."}, status_code=400)
    if req.lang not in VOICEBOX_LANGS and not _own_voice(req.profile):      # Pick's own voice speaks any language
        return {"unsupported": True, "ready": {}, "waiting": 0, "error": None}
    ready, todo = {}, []
    style = req.style if req.style in TTS_STYLES else ""
    for text in req.lines[:400]:
        text = " ".join((text or "").split())[:400]
        if not text:
            continue
        f = _tts_file(req.profile, req.lang, text, style)
        if f.is_file():
            ready[text] = f"/api/tts/audio/{f.stem}"
        else:
            todo.append((f, req.profile, req.lang, text, style))
    with _TTS_LOCK:
        have = {q[0] for q in _TTS_QUEUE}
        working = _TTS_QUEUE[:1] if _TTS["thread"] is not None else []
        rest = [q for q in _TTS_QUEUE[len(working):]]
        new = [q for q in todo if q[0] not in have]
        rest = rest + new
        want = " ".join((req.first or "").split())
        rest.sort(key=lambda q: q[3] != want)              # stable: the wanted line first, the others as they were
        _TTS_QUEUE[:] = working + rest
        waiting = len(_TTS_QUEUE)
        if _TTS_QUEUE and _TTS["thread"] is None:
            _TTS["error"] = None
            _TTS["thread"] = threading.Thread(target=_tts_drain, name="tts", daemon=True)
            _TTS["thread"].start()
    return {"ready": ready, "waiting": waiting, "mine": len(todo), "error": _TTS["error"]}


@app.get("/api/tts/audio/{stem}")
def tts_audio(stem: str):
    f = CONFIG_PATH.parent / "tts" / (_re_mod.sub(r"[^0-9a-f]", "", stem) + ".wav")
    if not f.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="audio/wav")


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
                if _chords_failed(data) and ANALYZE_PYTHON.is_file():
                    return _retry_chords(stems_dir, cache, data)
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


def _chords_failed(data) -> bool:
    """The chord step broke (no beat grid yet, an error) and left no chord at
    all: that is a hiccup to try again, not the song's answer. Tried a couple
    of times at most, so a song that truly has none is not re-run forever."""
    if any(c.get("chord") for c in data.get("chords") or [] if isinstance(c, dict)):
        return False
    broke = "chords" not in data or any(str(w).startswith("akordy") for w in data.get("warnings") or [])
    return broke and int(data.get("chords_retry") or 0) < 2


def _retry_chords(stems_dir: Path, cache: Path, data: dict):
    """Only the chords again (about ten seconds), into the cached analysis."""
    lock = _ANALYSIS_LOCKS.setdefault(str(stems_dir), threading.Lock())
    with lock:
        try:
            fresh = json.loads(cache.read_text(encoding="utf-8"))
            if not _chords_failed(fresh):
                return fresh
            data = fresh
        except (json.JSONDecodeError, OSError):
            pass
        _beat_grid_for(stems_dir)
        cmd = [str(ANALYZE_PYTHON), str(APP_DIR / "analyze.py"), str(stems_dir), "--skip-sections"]
        beats = stems_dir / "beats.json"
        if beats.is_file():
            cmd += ["--beats", str(beats)]
        data = dict(data)
        data["chords_retry"] = int(data.get("chords_retry") or 0) + 1
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            got = json.loads(((proc.stdout or "").strip().splitlines() or ["{}"])[-1])
            data["chords"] = got.get("chords") or []
            data["warnings"] = [w for w in data.get("warnings") or [] if not str(w).startswith("akordy")] \
                + [w for w in got.get("warnings") or [] if str(w).startswith("akordy")]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
            pass
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
def get_chords(name: str, analysis: bool = False):
    """The song's chords — the listener's own if they have edited any,
    otherwise whatever the analysis found (`analysis=1`: always those)."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)

    edited = stems_dir / "chords.json"
    if edited.is_file() and not analysis:
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


# ---- chord sheets from the web, for "Akordy z webu" (static/chordcoach.js lays them onto the recording):
# Ultimate Guitar's chord pages (its best rated versions) and Supermusic's (Czech and Slovak songs). Asked
# only when the listener clicks, one page at a time, and kept beside the stems.
_SM_LOCK = threading.Lock()
_SM_LAST = [0.0]


def _sm_get(url: str) -> str:
    with _SM_LOCK:
        wait = 1.0 - (time.time() - _SM_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _SM_LAST[0] = time.time()
        r = httpx.get(url, headers={"User-Agent": _UG_AGENT, "Accept-Language": "cs,sk;q=0.9,en;q=0.5"},
                      timeout=15, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _ug_sheet_text(content: str) -> str:
    """UG's wiki markup → plain text: [ch]Am[/ch] → Am (where it stood over the words), [tab] gone."""
    t = _re_mod.sub(r"\[/?tab\]", "", content or "")
    t = _re_mod.sub(r"\[ch\](.*?)\[/ch\]", r"\1", t)
    return t.replace("\r\n", "\n").strip()


def _ug_sheets(artist: str, title: str, limit: int = 2) -> list:
    want_a, want_t = _names.key(artist.split(",")[0]), _names.key(_names.no_brackets(title))
    if not want_t:
        return []
    found = _ug_page("https://www.ultimate-guitar.com/search.php", search_type="title",
                     value=f"{artist.split(',')[0]} {_names.no_brackets(title)}".strip()).get("results") or []
    mine = [r for r in found if isinstance(r, dict) and r.get("tab_url") and r.get("type") == "Chords"
            and (not want_a or _names.key(r.get("artist_name")) == want_a)
            and _names.key(_names.no_brackets(r.get("song_name") or "")) == want_t]
    mine.sort(key=lambda r: -((r.get("rating") or 0) * min(1.0, (r.get("votes") or 0) / 20) + (r.get("votes") or 0) / 1e4))
    out = []
    for r in mine[:limit]:
        try:
            tab = (_ug_page(r["tab_url"]).get("tab_view") or {}).get("wiki_tab") or {}
            text = _ug_sheet_text(tab.get("content") or "")
        except Exception:                                      # noqa: BLE001
            continue
        if text:
            out.append({"source": "Ultimate Guitar", "url": r["tab_url"], "artist": r.get("artist_name"),
                        "title": r.get("song_name"), "votes": r.get("votes") or 0,
                        "rating": round(float(r.get("rating") or 0), 2), "text": text[:60000]})
    return out


def _sm_sheet_text(page: str) -> str:
    """A Supermusic song page → its text with the chords: chords marked up (<sup>, links of class sup) become
    [Am] inline, the rest of the page around the song is left out."""
    import html as _html
    marks = list(_re_mod.finditer(r"<sup\b|class=\"?sup", page, _re_mod.I))
    if marks:
        a = page.rfind("<", 0, max(0, marks[0].start() - 3000))
        body = page[max(0, a):marks[-1].end() + 1500]
    else:                                   # chords written as text over the words: the page's text, the sheet finds them
        b = _re_mod.search(r"<body\b", page, _re_mod.I)
        body = page[b.start():] if b else page
    body = _re_mod.sub(r"<a\b[^>]*class=\"?sup\"?[^>]*>(.*?)</a>", r"[\1]", body, flags=_re_mod.I | _re_mod.S)
    body = _re_mod.sub(r"<sup\b[^>]*>(.*?)</sup>", r"[\1]", body, flags=_re_mod.I | _re_mod.S)
    body = _re_mod.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", body, flags=_re_mod.I)
    body = _re_mod.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=_re_mod.I | _re_mod.S)
    body = _html.unescape(_re_mod.sub(r"<[^>]+>", "", body))
    lines = [ln.rstrip() for ln in body.split("\n")]
    return "\n".join(lines).strip()


def _sm_sheets(artist: str, title: str, limit: int = 2) -> list:
    import html as _html
    want_a, want_t = _names.key(artist.split(",")[0]), _names.key(_names.no_brackets(title))
    if not want_t:
        return []
    body = _sm_get("https://supermusic.cz/najdi.php?typhladania=piesen&hladane=" + quote(_names.no_brackets(title)))
    hits = []
    for sid, nm, rest in _re_mod.findall(r'idpiesne=(\d+)[^>]*>(.*?)</a>(?=(.{0,400}))', body, flags=_re_mod.S):
        name = _html.unescape(_re_mod.sub(r"<[^>]+>", "", nm)).strip()
        rest = _re_mod.split(r"idpiesne=|<br|</tr|</li", rest, maxsplit=1, flags=_re_mod.I)[0]   # this row only
        near = _html.unescape(_re_mod.sub(r"<[^>]+>", " ", rest))
        # "Kabát - Pohoda (akordy)" or the song's name, the band beside it
        main = _re_mod.sub(r"\s*\((akordy|text|taby?|melodie|mp3)[^)]*\)\s*$", "", name, flags=_re_mod.I)
        if " - " in main:
            band, song = main.split(" - ", 1)
        else:
            band, song = near, main
        if _names.key(_names.no_brackets(song)) != want_t:
            continue
        if want_a and want_a not in _names.key(band) and want_a not in _names.key(near):
            continue
        hits.append((0 if "akordy" in name.lower() else 1, sid))
    out, seen = [], set()
    for _, sid in sorted(hits):
        if sid in seen or len(out) >= limit:
            continue
        seen.add(sid)
        url = f"https://supermusic.cz/skupina.php?idpiesne={sid}"
        try:
            text = _sm_sheet_text(_sm_get(url))
        except Exception:                                      # noqa: BLE001
            continue
        if text:
            out.append({"source": "Supermusic", "url": url, "artist": artist, "title": title, "votes": 0,
                        "rating": 0, "text": text[:60000]})
    return out


@app.get("/api/stems/{name}/chordsheets")
def chord_sheets(name: str, refresh: bool = False):
    """Chord sheets of this song found on the web (Ultimate Guitar, Supermusic) — the text only; laying them
    onto the recording happens in the page."""
    stems_dir = _resolve_under_music_root(f"Stems/{name}")
    if stems_dir is None or not stems_dir.is_dir():
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    cache = stems_dir / "chordsheets.json"
    if cache.is_file() and not refresh:
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
            if got.get("sheets"):
                return got
        except (OSError, ValueError):
            pass
    meta = _stems_meta(stems_dir)
    artist, title = (meta.get("artist") or "").strip(), (meta.get("title") or "").strip()
    if not title:
        return {"sheets": [], "artist": artist, "title": title, "errors": ["Píseň nemá název."]}
    sheets, errors = [], []
    for label, find in (("Supermusic", _sm_sheets), ("Ultimate Guitar", _ug_sheets)):
        try:
            sheets += find(artist, title)
        except Exception as exc:                               # noqa: BLE001
            errors.append(f"{label}: {exc.__class__.__name__}")
    out = {"sheets": sheets, "artist": artist, "title": title, "errors": errors}
    if sheets:
        try:
            cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return out


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


# ---- "Zpěv mým hlasem": the song's vocals sung in the player's own voice.
# The vocals stem keeps its melody, timing and phrasing; only whose voice it is
# changes — taken from the voice the player added for reading aloud (or, better,
# from a sample of them singing). The model (Seed-VC) lives in an environment of
# its own and runs as a process per job (svc_worker.py): about three times the
# length of what it converts on this kind of computer, so one job at a time,
# in the background, with its progress told. What comes out is a track of the
# song like any other; a stretch of it can be done again and is spliced in.
SVC_PYTHON = HOME / ".local" / "venvs" / "pick-svc" / "bin" / "python"
SVC_DIR = CONFIG_PATH.parent / "svc" / "seed-vc"
SVC_WORKER = APP_DIR / "svc_worker.py"
SVC_SR = 44100
SVC_FILE = "myvoice.json"
SVC_SONG_SAMPLE = "myvoice-sample.wav"     # the player singing a piece of this very song: the model's best model for it
_SVC: dict = {"job": None, "proc": None}
_SVC_LOCK = threading.Lock()


def _svc_ready() -> bool:
    return SVC_PYTHON.is_file() and SVC_DIR.is_dir()


def _svc_voices() -> list:
    root = _own_voices_dir()
    out = []
    for d in (sorted(root.iterdir()) if root.is_dir() else []):
        if d.is_dir() and not d.name.startswith(".") and (d / "ref.wav").is_file():
            name = (d / "name.txt").read_text(encoding="utf-8").strip() if (d / "name.txt").is_file() else d.name
            out.append({"id": d.name, "name": name, "sung": (d / "sing.wav").is_file()})
    return out


def _svc_voice_file(voice: str) -> Optional[Path]:
    if not voice or "/" in voice or voice.startswith("."):
        return None
    d = _own_voices_dir() / voice
    return next((f for f in (d / "sing.wav", d / "ref.wav") if f.is_file()), None)


def _svc_song_sample(d: Path) -> Optional[Path]:
    f = d / SVC_SONG_SAMPLE
    try:
        return f if f.is_file() and f.stat().st_size > SVC_SR * 2 * 3 else None
    except OSError:
        return None


def _svc_kept(d: Path) -> dict:
    try:
        got = json.loads((d / SVC_FILE).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) and _stem_file(d, got.get("stem") or "-") else {}
    except (OSError, ValueError):
        return {}


def _svc_pcm(src: Path, start: float = 0.0, length: Optional[float] = None):
    """Mono float samples of a file at the model's rate."""
    import numpy as np
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin"] + (["-ss", f"{start:.3f}"] if start > 0 else []) + ["-i", str(src)]
    cmd += (["-t", f"{length:.3f}"] if length else []) + ["-ac", "1", "-ar", str(SVC_SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _svc_wav(samples, path: Path) -> None:
    import wave
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SVC_SR)
        w.writeframes(pcm.tobytes())


def _svc_level(made, like):
    """As loud as what it replaces, and never over full scale."""
    import numpy as np
    rms = lambda x: float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    if rms(made) > 1e-5 and rms(like) > 1e-5:
        made = made * (rms(like) / rms(made))
    peak = float(np.max(np.abs(made))) if len(made) else 0.0
    return made * (0.97 / peak) if peak > 0.97 else made


def _svc_splice(whole, part, at: int, fade: int = 1764):
    """`part` put into `whole` from sample `at`, eased in and out over 40 ms."""
    import numpy as np
    part = part[:max(0, len(whole) - at)]
    n = len(part)
    if n < fade * 3:
        return whole
    env = np.ones(n, dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    if at > 0:
        env[:fade] = ramp
    if at + n < len(whole):
        env[-fade:] = ramp[::-1]
    out = whole.copy()
    out[at:at + n] = whole[at:at + n] * (1.0 - env) + part * env
    return out


def _svc_run(job: dict, d: Path, vocals: Path, ref: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pick-svc-"))
    try:
        whole = job["end"] is None
        pad = 0.0 if whole else min(0.6, job["start"])                 # a little before, so the voice is already going
        start = 0.0 if whole else job["start"] - pad
        length = None if whole else (job["end"] - job["start"]) + pad + 0.6
        src = _svc_pcm(vocals, start, length)
        if len(src) < SVC_SR:
            raise RuntimeError("V tomhle úseku není co zpívat.")
        _svc_wav(src, tmp / "src.wav")
        ref_len = min(25.0, len(_svc_pcm(ref, 0, 26)) / SVC_SR)
        job["of"] = max(1, int(-(-(len(src) / SVC_SR) // max(4.0, 30.0 - ref_len))))
        job["stage"] = "load"
        proc = subprocess.Popen([str(SVC_PYTHON), str(SVC_WORKER), "--source", str(tmp / "src.wav"), "--ref", str(ref),
                                 "--out", str(tmp / "out.wav"), "--steps", str(job["steps"]), "--shift", str(job["shift"]),
                                 "--auto", "1" if job["auto"] else "0"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        _SVC["proc"] = proc
        last = ""
        for line in proc.stdout:
            line = line.strip()
            if line == "LOADED":
                job["stage"] = "sing"; job["sing_at"] = time.time()
            elif line.startswith("PROGRESS"):
                job["done"] = min(job["of"], int(line.split()[1]))
            elif line.startswith(("ERROR", "DONE")):
                last = line
        proc.wait()
        if job.get("status") == "stopped":
            return
        if proc.returncode != 0 or not (tmp / "out.wav").is_file():
            raise RuntimeError(last[6:] if last.startswith("ERROR") else "Model skončil bez výsledku.")
        job["stage"] = "save"
        made = _svc_pcm(tmp / "out.wav")
        made = _svc_level(made[:len(src)], src)
        kept = _svc_kept(d)
        if whole or not kept:
            full = made if whole else _svc_splice(_svc_pcm(vocals) * 0.0, made, int(start * SVC_SR))
        else:
            full = _svc_splice(_svc_pcm(_stem_file(d, kept["stem"])), made, int(start * SVC_SR))
        _svc_wav(full, tmp / "full.wav")
        stem = kept.get("stem") or _next_take_name(d)
        for old in d.glob(f"{stem}.*"):                                # the same track, whatever it was encoded as
            if old.suffix.lower() in STEM_READ_EXTS and old.suffix.lower() != ".mp3":
                old.unlink()
        err = _encode_to_stem(tmp / "full.wav", d / f"{stem}.mp3")
        if err:
            raise RuntimeError(err)
        _set_track_label(d, stem, f"Můj hlas ({job['voice_name']})")
        (d / SVC_FILE).write_text(json.dumps({"stem": stem, "voice": job["voice"], "steps": job["steps"], "shift": job["shift"],
                                              "auto": job["auto"], "at": time.time()}, ensure_ascii=False), encoding="utf-8")
        job.update(status="done", stem=stem, took=time.time() - job["started"])
    except Exception as e:                                             # a job never takes the server with it
        if job.get("status") != "stopped":
            job.update(status="error", error=str(e)[:300])
    finally:
        _SVC["proc"] = None
        shutil.rmtree(tmp, ignore_errors=True)


class MyVoiceRequest(BaseModel):
    voice: str
    steps: int = 25
    shift: int = 0
    auto: bool = False
    start: Optional[float] = None      # with `end`: only this stretch, spliced into what is already there
    end: Optional[float] = None
    own: bool = True                   # use the player's take of this song as the model, when there is one


def _svc_job_view() -> Optional[dict]:
    job = _SVC["job"]
    if not job:
        return None
    out = {k: job.get(k) for k in ("song", "status", "stage", "done", "of", "error", "stem", "start", "end", "took", "own")}
    out["elapsed"] = time.time() - job["started"]
    out["estimate"] = job["estimate"]
    return out


@app.get("/api/myvoice")
def myvoice_status(song: str = ""):
    d = _resolve_under_music_root(f"Stems/{song}") if song else None
    return {"ready": _svc_ready(), "voices": _svc_voices(), "job": _svc_job_view(),
            "kept": _svc_kept(d) if d is not None and d.is_dir() else {},
            "sample": ({"url": f"/api/stems/{quote(song)}/myvoice/sample", "seconds": round((_svc_song_sample(d).stat().st_size - 44) / (SVC_SR * 2), 1)}
                       if d is not None and d.is_dir() and _svc_song_sample(d) else None),
            "has_vocals": bool(d is not None and d.is_dir() and _stem_file(d, "vocals"))}


@app.post("/api/stems/{name}/myvoice")
def myvoice_start(name: str, req: MyVoiceRequest):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    if not _svc_ready():
        return JSONResponse({"error": "Model pro zpěv mým hlasem není nainstalovaný."}, status_code=503)
    vocals = _stem_file(d, "vocals")
    if vocals is None:
        return JSONResponse({"error": "Tahle píseň nemá stopu zpěvu."}, status_code=400)
    ref = _svc_voice_file(req.voice)
    if ref is None:
        return JSONResponse({"error": "Takový hlas tu není — přidej si ho u Výslovnosti."}, status_code=404)
    # sung by the player to this very song: in its key and manner, a truer model than any other sample
    own = _svc_song_sample(d) if req.own else None
    ref = own or ref
    part = req.start is not None and req.end is not None
    if part and req.end - req.start < 1.0:
        return JSONResponse({"error": "Úsek je moc krátký (aspoň vteřina)."}, status_code=400)
    dur = _audio_tags(vocals).get("duration") or 0
    length = (req.end - req.start) if part else dur
    steps = max(8, min(int(req.steps), 60))
    ok, why = _sysload.room(2.5, "zpěv jiným hlasem")
    if not ok:
        return JSONResponse({"error": why}, status_code=503)
    with _SVC_LOCK:
        if _SVC["job"] and _SVC["job"].get("status") == "running":
            return JSONResponse({"error": "Jeden zpěv se už počítá — počkej, až doběhne."}, status_code=409)
        job = {"song": name, "status": "running", "stage": "prepare", "done": 0, "of": 1, "started": time.time(),
               "voice": req.voice, "voice_name": next((v["name"] for v in _svc_voices() if v["id"] == req.voice), req.voice),
               "own": bool(own),
               "steps": steps, "shift": max(-24, min(int(req.shift), 24)), "auto": bool(req.auto),
               "start": max(0.0, float(req.start)) if part else None, "end": float(req.end) if part else None,
               # measured on an M1 Pro: loading the model, then about 2.8 × the length at 25 steps
               "estimate": 45 + length * (0.6 + 2.2 * steps / 25)}
        _SVC["job"] = job
    threading.Thread(target=_svc_run, args=(job, d, vocals, ref), daemon=True).start()
    return _svc_job_view()


@app.post("/api/stems/{name}/myvoice/sample")
async def myvoice_song_sample(name: str, file: UploadFile = File(...)):
    """The player's own take of a piece of this song, sung along with it — kept beside the stems."""
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    made = d / "myvoice-sample.tmp.wav"
    try:
        secs = _guide_clip(tmp, made, SVC_SR, 25.0)       # rumble and pauses out, steady loudness, 25 s at the most
        if secs < 4.0:
            made.unlink(missing_ok=True)
            return JSONResponse({"error": "V nahrávce je málo zpěvu — aspoň čtyři vteřiny. Zpívej blíž k mikrofonu."}, status_code=400)
        made.replace(d / SVC_SONG_SAMPLE)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "seconds": round(secs, 1), "url": f"/api/stems/{quote(name)}/myvoice/sample"}


@app.get("/api/stems/{name}/myvoice/sample")
def myvoice_song_sample_get(name: str):
    d = _resolve_under_music_root(f"Stems/{name}")
    f = _svc_song_sample(d) if d is not None and d.is_dir() else None
    if f is None:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.delete("/api/stems/{name}/myvoice/sample")
def myvoice_song_sample_drop(name: str):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is not None and d.is_dir():
        (d / SVC_SONG_SAMPLE).unlink(missing_ok=True)
    return {"ok": True}


@app.delete("/api/myvoice/job")
def myvoice_stop():
    job, proc = _SVC["job"], _SVC["proc"]
    if job and job.get("status") == "running":
        job["status"] = "stopped"
        if proc is not None:
            proc.terminate()
    return {"ok": True}


@app.post("/api/myvoice/sample/{voice}")
async def myvoice_sample(voice: str, file: UploadFile = File(...)):
    """A recording of the player singing: a truer model for a sung voice than speech is."""
    d = _own_voices_dir() / voice
    if "/" in voice or voice.startswith(".") or not (d / "ref.wav").is_file():
        return JSONResponse({"error": "Takový hlas tu není."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(tmp), "-t", "25", "-ac", "1", "-ar", str(SVC_SR),
                        "-af", "silenceremove=start_periods=1:start_threshold=-45dB", str(d / "sing.tmp.wav")], capture_output=True, timeout=120)
        made = d / "sing.tmp.wav"
        if not made.is_file() or made.stat().st_size < SVC_SR * 2 * 3:
            made.unlink(missing_ok=True)
            return JSONResponse({"error": "Vzorek je moc krátký — aspoň pár vteřin zpěvu."}, status_code=400)
        made.replace(d / "sing.wav")
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "voices": _svc_voices()}


@app.delete("/api/myvoice/sample/{voice}")
def myvoice_sample_drop(voice: str):
    d = _own_voices_dir() / voice
    if "/" not in voice and not voice.startswith("."):
        (d / "sing.wav").unlink(missing_ok=True)
    return {"ok": True, "voices": _svc_voices()}


# ---- "Přezpívat řádek": one line of a song sung again with other words, in the
# singer's own voice and on the song's own tune. The words are spoken first —
# by OmniVoice, in the singer's voice taken off the vocals (or by the player,
# recorded) — then laid onto the line's notes and rhythm (resing_worker.py,
# the WORLD vocoder), then sung in the singer's voice by Seed-VC. Every line
# done goes into one track of its own, "Upravený zpěv", beside the original.
RESING_FILE = "resing.json"
RESING_WORKER = APP_DIR / "resing_worker.py"
RESING_TAKE = "resing-take.wav"            # the player saying the new words, the latest one recorded
_RESING: dict = {"job": None, "proc": None}


def _resing_kept(d: Path) -> dict:
    try:
        got = json.loads((d / RESING_FILE).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) and _stem_file(d, got.get("stem") or "-") else {"edits": (got or {}).get("edits") or []}
    except (OSError, ValueError, AttributeError):
        return {}


def _resing_refs(d: Path, vocals: Path, avoid: tuple, lang: str) -> tuple:
    """The singer's voice out of the vocals: ~16 s of their loudest singing for Seed-VC,
    and one phrase with what is sung in it for OmniVoice. Kept beside the stems."""
    import numpy as np
    sing, say, said = d / "resing-ref.wav", d / "resing-say.wav", d / "resing-say.txt"
    if sing.is_file() and say.is_file() and said.is_file():
        return sing, say, said.read_text(encoding="utf-8")
    x = _hear.decode(vocals)
    spans = [(a, b) for a, b in _hear.phrases(x, gap=0.4, longest=10.0) if b < avoid[0] - 1 or a > avoid[1] + 1]
    loud = lambda s: float(np.sqrt(np.mean(np.square(x[int(s[0] * 16000):int(s[1] * 16000)]))))
    best = sorted(spans, key=lambda s: -loud(s) * min(s[1] - s[0], 6.0))
    if not best:
        raise RuntimeError("Ve stopě zpěvu není dost zpěvu na ukázku hlasu.")
    picked, total = [], 0.0
    for s in best:
        if total >= 16:
            break
        picked.append(s)
        total += s[1] - s[0]
    picked.sort()
    parts = [_svc_pcm(vocals, a, b - a) for a, b in picked]
    _svc_wav(np.concatenate(parts), sing)
    one = next((s for s in best if 3.5 <= s[1] - s[0] <= 10.0), best[0])
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-ss", f"{one[0]:.2f}", "-t", f"{one[1] - one[0]:.2f}", "-i", str(vocals),
                    "-ac", "1", "-ar", "24000", str(say)], check=True, timeout=60)
    # what is sung in exactly this clip: a text that says more or less than the clip makes the
    # voice speak the rest of it too, so it is heard afresh, not taken from the song's lines
    pcm = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-i", str(say), "-ac", "1", "-ar", str(_voice.RATE), "-f", "s16le", "-"],
                         capture_output=True, timeout=60).stdout
    words = _voice.transcribe(pcm, lang=lang if lang in ("en", "cs") else "auto", prompt="").strip()
    if not words:
        raise RuntimeError("V ukázce hlasu zpěváka jsem neslyšel slova.")
    said.write_text(words, encoding="utf-8")
    return sing, say, words


def _resing_step(job: dict, cmd: list) -> str:
    """One of the two workers, its last line back; stopped with the job."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _RESING["proc"] = proc
    last = ""
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("PROGRESS"):
            job["done"] = line.split()[1]
        elif line.startswith(("ERROR", "DONE")):
            last = line
    proc.wait()
    _RESING["proc"] = None
    if job.get("status") == "stopped":
        raise InterruptedError()
    if proc.returncode != 0:
        raise RuntimeError(last[6:] if last.startswith("ERROR") else "Model skončil bez výsledku.")
    return last


def _resing_run(job: dict, d: Path, vocals: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pick-resing-"))
    try:
        start, end = job["start"], job["end"]
        a, b = max(0.0, start - 0.15), end + 0.35
        orig = tmp / "orig.wav"
        _svc_wav(_svc_pcm(vocals, a, b - a), orig)
        job["stage"] = "voice"
        sing, say, said_text = _resing_refs(d, vocals, (a, b), job["lang"])
        said = tmp / "said.wav"
        if job["take"]:
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(d / RESING_TAKE), "-ac", "1", "-ar", str(SVC_SR),
                            "-af", "highpass=f=70,silenceremove=start_periods=1:start_threshold=-40dB:stop_periods=1:stop_threshold=-40dB",
                            str(said)], check=True, timeout=60)
        else:
            job["stage"] = "say"
            said.write_bytes(_own_say({"ref_audio": str(say), "ref_text": said_text}, job["lang"], job["text"]))
            with _OWN_TTS_LOCK:                                   # its memory back before Seed-VC wants its own
                proc = _OWN_TTS["proc"]
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                _OWN_TTS["proc"] = None
        job["stage"] = "tune"
        tuned = tmp / "tuned.wav"
        _resing_step(job, [str(SVC_PYTHON), "-W", "ignore", str(RESING_WORKER), "--orig", str(orig), "--speech", str(said),
                           "--out", str(tuned), "--syll", str(_songbook.syllables(job["text"]) if job["text"] else 0)])
        job["stage"] = "sing"
        _resing_step(job, [str(SVC_PYTHON), str(SVC_WORKER), "--source", str(tuned), "--ref", str(sing), "--out", str(tmp / "sung.wav"),
                           "--steps", "30", "--cfg", "0.4"])
        job["stage"] = "save"
        src = _svc_pcm(orig)
        made = _svc_level(_svc_pcm(tmp / "sung.wav")[:len(src)], src)
        kept = _resing_kept(d)
        base = _stem_file(d, kept["stem"]) if kept.get("stem") else vocals
        full = _svc_splice(_svc_pcm(base), made, int(a * SVC_SR))
        _svc_wav(full, tmp / "full.wav")
        stem = kept.get("stem") or _next_take_name(d)
        for old in d.glob(f"{stem}.*"):
            if old.suffix.lower() in STEM_READ_EXTS and old.suffix.lower() != ".mp3":
                old.unlink()
        err = _encode_to_stem(tmp / "full.wav", d / f"{stem}.mp3")
        if err:
            raise RuntimeError(err)
        _set_track_label(d, stem, "Upravený zpěv")
        edits = [e for e in kept.get("edits", []) if e.get("end", 0) <= start or e.get("start", 0) >= end]
        edits.append({"start": start, "end": end, "old": job["old"], "text": job["text"], "take": job["take"], "at": time.time()})
        (d / RESING_FILE).write_text(json.dumps({"stem": stem, "edits": sorted(edits, key=lambda e: e["start"])}, ensure_ascii=False),
                                     encoding="utf-8")
        job.update(status="done", stem=stem, took=time.time() - job["started"])
    except InterruptedError:
        pass
    except Exception as e:                                             # a job never takes the server with it
        if job.get("status") != "stopped":
            job.update(status="error", error=str(e)[:300])
    finally:
        _RESING["proc"] = None
        shutil.rmtree(tmp, ignore_errors=True)


class ResingRequest(BaseModel):
    start: float
    end: float
    text: str = ""             # the new words; with `take`, what the recording says (heard when empty)
    old: str = ""              # the line as it was, to show beside the new one
    lang: str = "cs"
    take: bool = False         # the player's own recording of the new words (…/resing/take) instead of the singer's voice reading them


def _resing_view() -> Optional[dict]:
    job = _RESING["job"]
    if not job:
        return None
    return {k: job.get(k) for k in ("song", "status", "stage", "error", "stem", "start", "end", "text", "took")} | {
        "elapsed": time.time() - job["started"], "estimate": job["estimate"]}


@app.get("/api/resing")
def resing_status(song: str = ""):
    d = _resolve_under_music_root(f"Stems/{song}") if song else None
    return {"ready": _svc_ready() and OWN_TTS_PYTHON.is_file() and RESING_WORKER.is_file(), "job": _resing_view(),
            "kept": _resing_kept(d) if d is not None and d.is_dir() else {}}


@app.post("/api/stems/{name}/resing")
def resing_start(name: str, req: ResingRequest):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    vocals = _stem_file(d, "vocals")
    if vocals is None:
        return JSONResponse({"error": "Tahle píseň nemá stopu zpěvu."}, status_code=400)
    if not (_svc_ready() and OWN_TTS_PYTHON.is_file()):
        return JSONResponse({"error": "Chybí model pro zpěv (Seed-VC) nebo hlas (OmniVoice)."}, status_code=503)
    text = " ".join((req.text or "").split())[:200]
    if req.take and not (d / RESING_TAKE).is_file():
        return JSONResponse({"error": "Nejdřív nahraj, jak se nová slova říkají."}, status_code=400)
    if req.take and not text:
        pcm = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-i", str(d / RESING_TAKE), "-ac", "1", "-ar", str(_voice.RATE), "-f", "s16le", "-"],
                             capture_output=True, timeout=60).stdout
        text = _voice.transcribe(pcm, lang=req.lang if req.lang in ("en", "cs") else "auto", prompt="").strip()
    if not text and not req.take:
        return JSONResponse({"error": "Napiš nová slova."}, status_code=400)
    length = req.end - req.start
    if length < 0.5 or length > 15:
        return JSONResponse({"error": "Řádek musí mít od půl vteřiny do 15 vteřin."}, status_code=400)
    ok, why = _sysload.room(3.0, "přezpívání řádku")
    if not ok:
        return JSONResponse({"error": why}, status_code=503)
    with _SVC_LOCK:
        if (_SVC["job"] or {}).get("status") == "running" or (_RESING["job"] or {}).get("status") == "running":
            return JSONResponse({"error": "Jeden zpěv se už počítá — počkej, až doběhne."}, status_code=409)
        # measured on this Mac: the voice and the tune ~20 s, Seed-VC loading ~35 s, then ~3 × the line
        job = {"song": name, "status": "running", "stage": "prepare", "started": time.time(), "start": float(req.start),
               "end": float(req.end), "text": text, "old": (req.old or "")[:200], "lang": req.lang if req.lang in ("cs", "en", "sk", "de") else "cs",
               "take": bool(req.take), "estimate": 60 + length * 3.0}
        _RESING["job"] = job
    threading.Thread(target=_resing_run, args=(job, d, vocals), daemon=True).start()
    return _resing_view()


@app.post("/api/stems/{name}/resing/take")
async def resing_take(name: str, file: UploadFile = File(...)):
    """The player saying (or singing) the new words — kept until the next one."""
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(tmp), "-t", "16", "-ac", "1", "-ar", str(SVC_SR),
                            str(d / "resing-take.tmp.wav")], capture_output=True, timeout=60)
        made = d / "resing-take.tmp.wav"
        if r.returncode != 0 or not made.is_file() or made.stat().st_size < SVC_SR * 2 // 2:
            made.unlink(missing_ok=True)
            return JSONResponse({"error": "Nahrávka je moc krátká."}, status_code=400)
        made.replace(d / RESING_TAKE)
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True}


class ResingEditRequest(BaseModel):
    start: float               # which line sung again: its start, as kept
    text: str


def _resing_edit_at(kept: dict, start: float) -> Optional[dict]:
    return next((e for e in kept.get("edits", []) if abs(e.get("start", -1) - start) < 0.05), None)


@app.post("/api/stems/{name}/resing/edit")
def resing_edit_text(name: str, req: ResingEditRequest):
    """The new words of a line sung again, written down differently — the sound stays."""
    d = _resolve_under_music_root(f"Stems/{name}")
    kept = _resing_kept(d) if d is not None and d.is_dir() and not name.startswith(".") else {}
    e = _resing_edit_at(kept, req.start) if kept.get("stem") else None
    text = " ".join((req.text or "").split())[:200]
    if e is None or not text:
        return JSONResponse({"error": "Tenhle řádek přezpívaný není." if e is None else "Napiš text."}, status_code=400 if e else 404)
    e["text"] = text
    (d / RESING_FILE).write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "kept": kept}


@app.delete("/api/stems/{name}/resing/edit")
def resing_undo(name: str, start: float):
    """A line sung again back as it was: the original vocals put into the track there.
    The last one gone, the track goes too — it would only be the vocals again."""
    d = _resolve_under_music_root(f"Stems/{name}")
    kept = _resing_kept(d) if d is not None and d.is_dir() and not name.startswith(".") else {}
    e = _resing_edit_at(kept, start) if kept.get("stem") else None
    vocals = _stem_file(d, "vocals") if e is not None else None
    if e is None or vocals is None:
        return JSONResponse({"error": "Tenhle řádek přezpívaný není."}, status_code=404)
    if (_RESING["job"] or {}).get("status") == "running" and (_RESING["job"] or {}).get("song") == name:
        return JSONResponse({"error": "V téhle písni se zrovna přezpívává — počkej, až doběhne."}, status_code=409)
    stem, left = kept["stem"], [x for x in kept["edits"] if x is not e]
    if not left:
        for old in d.glob(f"{stem}.*"):
            if old.suffix.lower() in STEM_READ_EXTS:
                old.unlink()
        _set_track_label(d, stem, None)
        (d / RESING_FILE).unlink(missing_ok=True)
        return {"ok": True, "kept": {}, "gone": stem}
    a, b = max(0.0, e["start"] - 0.15), e["end"] + 0.35
    tmp = Path(tempfile.mkdtemp(prefix="pick-resing-"))
    try:
        _svc_wav(_svc_splice(_svc_pcm(_stem_file(d, stem)), _svc_pcm(vocals, a, b - a), int(a * SVC_SR)), tmp / "full.wav")
        err = _encode_to_stem(tmp / "full.wav", d / f"{stem}.mp3")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if err:
        return JSONResponse({"error": err}, status_code=500)
    kept["edits"] = left
    (d / RESING_FILE).write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "kept": kept}


@app.delete("/api/resing/job")
def resing_stop():
    job, proc = _RESING["job"], _RESING["proc"]
    if job and job.get("status") == "running":
        job["status"] = "stopped"
        if proc is not None:
            proc.terminate()
    return {"ok": True}


# ---- Tónový otisk: the sound of the guitar on a record, put onto the player's own guitar
# (docs/specs/2026-10-15-tonovy-otisk-design.md). The player plays a stretch of the song
# along with it; what came out of the guitar clean (tone/take-N.wav, on the song's clock)
# and the record's guitar on that stretch go to tone_worker.py (its own venv), which plays
# them through every NAM core of the library in Node (tone_nam.mjs — the very engine the
# live guitar runs) and keeps the chain that sounds the most like the record. A song can have
# many prints (its rhythm guitar, its solo, another stretch): tone/prints/<id>/ each — print.json
# (with its name), print-ir.wav, you.wav (the take through it, for the A/B), and what "Doladit"
# replaced (prev-*). All of them make the library of prints (Knihovna → Otisky zvuku).
TONE_PYTHON = HOME / ".local" / "venvs" / "pick-tone" / "bin" / "python"
TONE_WORKER = APP_DIR / "tone_worker.py"
TONE_NAM_JS = APP_DIR / "tone_nam.mjs"
TONE_DIR = "tone"
# how each NAM core sounds, heard once (tone_worker.py fingerprints) — near-twins are tried as one
TONE_CORES = ".tone-cores.json"
TONE_FILES = ("print-ir.wav", "you.wav", "abx-record.wav", "abx-you.wav", "prev-print-ir.wav", "you.mp3", "record.mp3")
TONE_STEM_LABELS = {"guitar": "Kytara", "guitar_solo": "Sólová kytara", "guitar_rhythm": "Rytmická kytara"}
TONE_PART = {"guitar_solo": "sólo", "guitar_rhythm": "rytmika"}
# The kinds of sound a print is filed under (static/tone.js shows them); the app picks one
# from the measured distortion of the record, the player can say otherwise.
TONE_CATEGORIES = ("clean", "crunch", "hi_gain", "lead", "acoustic", "ambient", "fuzz", "other")
_re_tone_take = _re_mod.compile(r"take-\d{1,4}\.wav")
_re_tone_print = _re_mod.compile(r"\d{8}-\d{6}(-\d{1,3})?")
_TONE: dict = {"job": None, "proc": None}


def _node_bin() -> Optional[str]:
    return shutil.which("node") or next((p for p in ("/opt/homebrew/bin/node", "/usr/local/bin/node") if Path(p).is_file()), None)


def _tone_ready() -> bool:
    return TONE_PYTHON.is_file() and TONE_WORKER.is_file() and TONE_NAM_JS.is_file() and _node_bin() is not None


def _tone_song(name: str) -> Optional[Path]:
    d = _resolve_under_music_root(f"Stems/{name}") if name and not name.startswith(".") else None
    return d if d is not None and d.is_dir() else None


def _tone_takes(d: Path) -> list:
    try:
        got = json.loads((d / TONE_DIR / "takes.json").read_text(encoding="utf-8"))
        return [t for t in got if isinstance(t, dict) and _re_tone_take.fullmatch(str(t.get("file", "")))
                and (d / TONE_DIR / t["file"]).is_file()]
    except (OSError, ValueError, TypeError):
        return []


def _tone_at(d: Path, pid: str) -> Path:
    return d / TONE_DIR / "prints" / pid


def _tone_new_id(d: Path, at: Optional[float] = None) -> str:
    base = time.strftime("%Y%m%d-%H%M%S", time.localtime(at or time.time()))
    pid, n = base, 1
    while _tone_at(d, pid).exists():
        n += 1
        pid = f"{base}-{n}"
    return pid


def _tone_auto_category(p: dict) -> str:
    drive = ((p.get("report") or {}).get("record") or {}).get("drive")
    if drive is None:
        return "other"
    return "clean" if drive < 30 else "crunch" if drive < 60 else "hi_gain"


def _tone_named(d: Path, prints: list) -> list:
    """What the player has not named or filed themselves: the band and the song (with the part
    and the stretch's start when the song has more prints of it), and the kind of sound."""
    m = _stems_meta(d)
    song = f"{m['artist']} – {m.get('title') or d.name}" if m.get("artist") else (m.get("title") or d.name)
    mm = lambda x: f"{int(float(x or 0) // 60)}:{int(float(x or 0) % 60):02d}"
    base = {p["id"]: song + (f" · {TONE_PART[p['stem']]}" if p.get("stem") in TONE_PART else "") for p in prints}
    count = {b: list(base.values()).count(b) for b in base.values()}
    for p in prints:
        if p.get("name_auto", True) or not p.get("name"):
            p["name"] = base[p["id"]] + (f" · {mm(p.get('start'))}" if count[base[p["id"]]] > 1 else "")
            p["name_auto"] = True
        if p.get("category") not in TONE_CATEGORIES:
            p["category"], p["category_auto"] = _tone_auto_category(p), True
    return prints


def _tone_migrate(d: Path) -> None:
    """A print from before a song could have more (tone/print.json) moves into prints/<its id>/."""
    t = d / TONE_DIR
    if not (t / "print.json").is_file():
        return
    try:
        p = json.loads((t / "print.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    pid = _tone_new_id(d, p.get("created"))
    dest = _tone_at(d, pid)
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("print-ir.wav", "you.wav", "prev-print.json", "prev-print-ir.wav", "abx-record.wav", "abx-you.wav"):
        if (t / f).is_file():
            (t / f).replace(dest / f)
    p.update(id=pid, name_auto=True)
    (dest / "print.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    (t / "print.json").unlink(missing_ok=True)


def _tone_print(d: Path, pid: str, prefix: str = "") -> Optional[dict]:
    if not pid or not _re_tone_print.fullmatch(pid):
        return None
    f = _tone_at(d, pid)
    try:
        got = json.loads((f / f"{prefix}print.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or not (f / f"{prefix}print-ir.wav").is_file():
        return None
    got["id"] = pid
    return got


def _tone_prints_of(d: Path) -> list:
    """The song's prints, the newest first."""
    _tone_migrate(d)
    base = d / TONE_DIR / "prints"
    found = [p for p in (_tone_print(d, f.name) for f in sorted(base.iterdir(), reverse=True) if f.is_dir()) if p] if base.is_dir() else []
    return _tone_named(d, found)


def _tone_summary(p: dict) -> dict:
    return {k: p.get(k) for k in ("id", "name", "name_auto", "category", "category_auto", "match", "stem", "start", "end", "created")} | {
        "core": (p.get("core") or {}).get("name")}


def _tone_stems(d: Path) -> list:
    """The stems a print can be taken from: the guitar, and its solo and rhythm when split."""
    return [s for s in ["guitar"] + GUITAR_PARTS if _stem_file(d, s) is not None]


def _tone_band(d: Path, stem: str) -> list:
    """The rest of the band around one guitar stem (a split guitar not counted twice)."""
    names = [s for s in STEM_NAMES if s != "guitar"] + ([g for g in GUITAR_PARTS if g != stem] if stem in GUITAR_PARTS else [])
    return [f for f in (_stem_file(d, s) for s in names) if f is not None]


def _tone_models() -> list:
    """Every NAM core the engine may try: the amps folder and all Pick knows of, each once."""
    out, seen = [], set()
    flat = sorted(AMPS_DIR.glob("*.nam")) if AMPS_DIR.is_dir() else []
    items = [{"name": p.stem, "file": p.name} for p in flat] + [a for a in _all_amps() + _amp_collections() if a.get("kind") == "model"]
    for a in items:
        path = _resolve_amp_path(a["file"])
        if path is None or not path.is_file():
            continue
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            out.append({"id": a["file"], "name": a["name"], "path": key})
    return out


def _tone_levels(path: Path, rate: int = 4000):
    """A stem's level every quarter of a second."""
    import numpy as np
    pcm = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, timeout=180).stdout
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    step = rate // 4
    n = len(x) // step
    return np.sqrt(np.mean(x[:n * step].reshape(n, step) ** 2, axis=1) + 1e-10) if n else np.zeros(0)


def _tone_sections(d: Path, stem: str, length: float = 12.0) -> list:
    """Up to three stretches where this guitar plays the most and the rest the least."""
    import numpy as np
    src = _stem_file(d, stem)
    if src is None:
        return []
    others = _tone_band(d, stem)
    g = _tone_levels(src)
    if not len(g) or g.max() < 1e-3:
        return []
    rest = np.zeros_like(g)
    for p in others:
        lv = _tone_levels(p)[:len(g)]
        rest[:len(lv)] += lv
    playing = g > g.max() * 10 ** (-25 / 20)
    score = np.where(playing, np.clip(20 * np.log10(g / (rest + 1e-4)), -20, 20), -20.0)
    w = int(length * 4)
    if len(score) <= w:
        return [{"start": 0.0, "end": round(len(score) / 4, 2), "score": round(float(score.mean()), 1)}]
    run = np.convolve(score, np.ones(w) / w, mode="valid")
    out = []
    for i in np.argsort(-run):
        if len(out) == 3 or run[i] < -10:
            break
        if all(abs(int(i) - int(o["start"] * 4)) >= w for o in out):
            out.append({"start": round(float(i) / 4, 2), "end": round(float(i + w) / 4, 2), "score": round(float(run[i]), 1)})
    return sorted(out, key=lambda o: o["start"])


def _tone_view() -> Optional[dict]:
    job = _TONE["job"]
    if not job:
        return None
    return {k: job.get(k) for k in ("song", "status", "stage", "done", "of", "error", "match", "took", "print")} | {
        "elapsed": time.time() - job["started"]}


def _tone_run(job: dict, d: Path, src: Path, takes: list, refine: bool, current: Optional[dict] = None) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pick-tone-"))
    try:
        sr = int(takes[-1].get("sr") or 48000) if takes else 48000
        ref = tmp / "ref.wav"
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-ss", f"{job['start']:.3f}", "-t", f"{job['end'] - job['start']:.3f}",
                        "-i", str(src), "-ac", "2", "-ar", str(sr), "-c:a", "pcm_f32le", str(ref)], check=True, timeout=120)
        old = _tone_print(d, job.get("print") or "") if refine else None
        spec = {"sr": sr, "ref": str(ref), "models": _tone_models(), "node": _node_bin(), "nam_js": str(TONE_NAM_JS),
                "worklet": str(APP_DIR / "static" / "nam" / "nam-worklet.js"), "wasm": str(APP_DIR / "static" / "nam" / "nam-engine.wasm"),
                "out": str(tmp / "out"), "procs": 3, "start_from": old if refine else None, "fp_cache": str(AMPS_DIR / TONE_CORES),
                "takes": [{"path": str(d / TONE_DIR / t["file"]), "offset": float(t["start"]) - job["start"]} for t in takes],
                "current": current}
        gid = _guitar_active()
        if gid:                                   # the calibrated guitar plays what the record plays
            job["stage"] = "notes"
            prof = _guitar_profile(gid)
            spec.update(guitar=prof, guitar_name=(_guitar_view(gid) or {}).get("name", ""), ref_notes=_notes_of(ref))
            if not takes and not spec["ref_notes"]:
                raise RuntimeError("V kytaře originálu jsem na tomhle úseku neslyšel noty — zahraj ho s písní, nebo vyber jiný úsek.")
        (tmp / "job.json").write_text(json.dumps(spec), encoding="utf-8")
        proc = subprocess.Popen([str(TONE_PYTHON), str(TONE_WORKER), "--job", str(tmp / "job.json")],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        _TONE["proc"] = proc
        last = ""
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("STAGE"):
                parts = (line.split() + ["0", "0"])[1:4]
                job.update(stage=parts[0], done=int(parts[1]), of=int(parts[2]))
            elif line.startswith(("DONE", "ERROR")):
                last = line
        proc.wait()
        _TONE["proc"] = None
        if job.get("status") == "stopped":
            return
        if proc.returncode != 0 or not last.startswith("DONE"):
            raise RuntimeError(last[6:] if last.startswith("ERROR") else "Otisk se nepovedl.")
        out = tmp / "out"
        pid = old["id"] if old else _tone_new_id(d)
        dest = _tone_at(d, pid)
        dest.mkdir(parents=True, exist_ok=True)
        got = json.loads((out / "print.json").read_text(encoding="utf-8"))
        got.update(id=pid, song=d.name, stem=job["stem"], start=job["start"], end=job["end"], takes=[t["file"] for t in takes],
                   gain_db=float(takes[-1].get("gain_db") or 0.0) if takes else 0.0)
        for k in ("name", "name_auto", "category", "category_auto"):     # what the player gave it stays
            if old and k in old:
                got[k] = old[k]
        if old:                                   # the one before stays, to go back to
            got["before"] = old.get("match")
            (dest / "prev-print.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            shutil.copyfile(dest / "print-ir.wav", dest / "prev-print-ir.wav")
        for f in ("print-ir.wav", "you.wav"):
            shutil.move(str(out / f), str(dest / f))
        for f in ("abx-record.wav", "abx-you.wav", "you.mp3", "record.mp3"):
            (dest / f).unlink(missing_ok=True)
        (dest / "print.json").write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
        job.update(status="done", match=got["match"], took=time.time() - job["started"], print=pid)
    except Exception as e:                                           # a job never takes the server with it
        if job.get("status") != "stopped":
            job.update(status="error", error=str(e)[:300])
    finally:
        _TONE["proc"] = None
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the player's guitar, calibrated (Tónový otisk → Moje kytara): the open strings, notes up the
# neck, chords, a riff muted and open — basic-pitch hears them (clean DI is what it hears best)
# and tone_worker.py --profile makes a library of the player's own notes. With it a print has the
# guitar play exactly what the record plays, on the record's clock and at its pitch — also with no
# take at all. tone-guitars/<id>/: meta.json, steps/<kind>.wav, profile.json.
TONE_GUITARS = HOME / ".local" / "share" / "spotdl-helper" / "tone-guitars"
TONE_GUITAR_STEPS = ("strings", "frets", "chords", "riff")
_re_guitar = _re_mod.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_TONE_GUITAR: dict = {"job": None}


def _guitar_at(gid: str) -> Optional[Path]:
    return TONE_GUITARS / gid if gid and _re_guitar.fullmatch(gid) else None


def _guitar_profile(gid: str) -> Optional[dict]:
    d = _guitar_at(gid)
    try:
        return json.loads((d / "profile.json").read_text(encoding="utf-8")) if d is not None else None
    except (OSError, ValueError):
        return None


def _guitar_view(gid: str) -> Optional[dict]:
    d = _guitar_at(gid)
    if d is None or not d.is_dir():
        return None
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    prof = _guitar_profile(gid)
    steps = {}
    for k in TONE_GUITAR_STEPS:
        f = d / "steps" / f"{k}.wav"
        if f.is_file():
            steps[k] = round((f.stat().st_size - 44) / (3 * int(meta.get("sr", {}).get(k, 48000) or 48000)), 1)
    return {"id": gid, "name": meta.get("name") or gid, "created": meta.get("created"), "steps": steps,
            "ready": prof is not None, "summary": (prof or {}).get("summary"), "analysed": (prof or {}).get("at")}


def _guitar_active() -> str:
    gid = _read_config().get("tone_guitar") or ""
    return gid if _guitar_profile(gid) is not None else ""


def _guitar_job_view() -> Optional[dict]:
    job = _TONE_GUITAR["job"]
    return None if not job else {k: job.get(k) for k in ("guitar", "status", "stage", "error")} | {"elapsed": time.time() - job["started"]}


def _notes_of(path: Path) -> list:
    """basic-pitch's notes of a recording (pick-midi venv), [] when it cannot say."""
    if not MIDI_PYTHON.is_file():
        return []
    try:
        r = subprocess.run([str(MIDI_PYTHON), str(APP_DIR / "audio2midi.py"), str(path)], capture_output=True, text=True, timeout=600)
        return json.loads(r.stdout)["notes"] if r.returncode == 0 else []
    except (subprocess.SubprocessError, ValueError, KeyError, OSError):
        return []


def _guitar_run(job: dict, d: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pick-guitar-"))
    try:
        steps = []
        for k in TONE_GUITAR_STEPS:
            f = d / "steps" / f"{k}.wav"
            if not f.is_file():
                continue
            job["stage"] = k
            steps.append({"kind": k, "path": str(f), "notes": _notes_of(f)})
        if not any(s["notes"] for s in steps):
            raise RuntimeError("V nahrávkách jsem neslyšel žádné tóny — je zapnutý vstup kytary a hraješ?")
        job["stage"] = "profile"
        spec = tmp / "job.json"
        spec.write_text(json.dumps({"sr": 48000, "steps": steps, "out": str(tmp / "profile.json")}), encoding="utf-8")
        r = subprocess.run([str(TONE_PYTHON), str(TONE_WORKER), "--profile", str(spec)], capture_output=True, text=True, timeout=600)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode != 0 or not (tmp / "profile.json").is_file():
            raise RuntimeError(last[6:] if last.startswith("ERROR") else "Kalibraci se nepodařilo vyhodnotit.")
        prof = json.loads((tmp / "profile.json").read_text(encoding="utf-8"))
        prof["at"] = time.time()
        (d / "profile.json").write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
        job.update(status="done", summary=prof.get("summary"))
    except Exception as e:                                            # a job never takes the server with it
        job.update(status="error", error=str(e)[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/tone/guitars")
def tone_guitars():
    TONE_GUITARS.mkdir(parents=True, exist_ok=True)
    rows = [v for v in (_guitar_view(p.name) for p in sorted(TONE_GUITARS.iterdir()) if p.is_dir()) if v]
    return {"guitars": rows, "active": _guitar_active(), "job": _guitar_job_view()}


class ToneGuitarRequest(BaseModel):
    name: str


@app.post("/api/tone/guitars")
def tone_guitar_new(req: ToneGuitarRequest):
    name = " ".join((req.name or "").split())[:60] or "Moje kytara"
    base = _re_mod.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")[:30] or "kytara"
    gid, n = base, 1
    while (TONE_GUITARS / gid).exists():
        n += 1
        gid = f"{base}-{n}"
    (TONE_GUITARS / gid / "steps").mkdir(parents=True, exist_ok=True)
    (TONE_GUITARS / gid / "meta.json").write_text(json.dumps({"name": name, "created": time.time(), "sr": {}}, ensure_ascii=False), encoding="utf-8")
    return _guitar_view(gid)


@app.post("/api/tone/guitars/{gid}/step/{kind}")
async def tone_guitar_step(gid: str, kind: str, file: UploadFile = File(...)):
    """One part of the calibration, played clean (the guitar's DI)."""
    import wave
    d = _guitar_at(gid)
    if d is None or not d.is_dir() or kind not in TONE_GUITAR_STEPS:
        return JSONResponse({"error": "Kytara nenalezena."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    made = d / "steps" / f"{kind}.wav"
    try:
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(tmp), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s24le", str(made)],
                           capture_output=True, timeout=120)
        with wave.open(str(made), "rb") as w:
            dur = w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error, subprocess.SubprocessError):
        made.unlink(missing_ok=True)
        return JSONResponse({"error": "Nahrávka se nedala přečíst."}, status_code=400)
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0 or dur < 2:
        made.unlink(missing_ok=True)
        return JSONResponse({"error": "Nahrávka je kratší než 2 s."}, status_code=400)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta.setdefault("sr", {})[kind] = 48000
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return _guitar_view(gid)


@app.post("/api/tone/guitars/{gid}/analyse")
def tone_guitar_analyse(gid: str):
    d = _guitar_at(gid)
    if d is None or not d.is_dir():
        return JSONResponse({"error": "Kytara nenalezena."}, status_code=404)
    if not (d / "steps" / "strings.wav").is_file() and not (d / "steps" / "chords.wav").is_file():
        return JSONResponse({"error": "Nahraj aspoň prázdné struny nebo akordy."}, status_code=400)
    if not (TONE_PYTHON.is_file() and MIDI_PYTHON.is_file()):
        return JSONResponse({"error": "Chybí engine otisku nebo převod na noty (pick-tone, pick-midi)."}, status_code=503)
    if (_TONE_GUITAR["job"] or {}).get("status") == "running":
        return JSONResponse({"error": "Jedna kalibrace se už vyhodnocuje."}, status_code=409)
    job = {"guitar": gid, "status": "running", "stage": "strings", "started": time.time()}
    _TONE_GUITAR["job"] = job
    threading.Thread(target=_guitar_run, args=(job, d), daemon=True).start()
    return _guitar_job_view()


@app.post("/api/tone/guitars/{gid}/use")
def tone_guitar_use(gid: str):
    gid = "" if gid == "-" else gid                     # "-": no calibration for the prints
    if gid and _guitar_profile(gid) is None:
        return JSONResponse({"error": "Tahle kytara ještě není vyhodnocená."}, status_code=400)
    _write_config({"tone_guitar": gid})
    return tone_guitars()


@app.delete("/api/tone/guitars/{gid}")
def tone_guitar_delete(gid: str):
    d = _guitar_at(gid)
    if d is None or not d.is_dir():
        return JSONResponse({"error": "Kytara nenalezena."}, status_code=404)
    job = _TONE_GUITAR["job"] or {}
    if job.get("status") == "running" and job.get("guitar") == gid:
        return JSONResponse({"error": "Kalibrace se právě vyhodnocuje — chvilku."}, status_code=409)
    shutil.rmtree(d, ignore_errors=True)
    if _read_config().get("tone_guitar") == gid:
        _write_config({"tone_guitar": ""})
    return tone_guitars()


@app.get("/api/tone/guitars/{gid}/step/{kind}.wav")
def tone_guitar_step_file(gid: str, kind: str):
    d = _guitar_at(gid)
    f = d / "steps" / f"{kind}.wav" if d is not None and kind in TONE_GUITAR_STEPS else None
    if f is None or not f.is_file():
        return JSONResponse({"error": "Nahrávka nenalezena."}, status_code=404)
    return FileResponse(f, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/tone/job")
def tone_job():
    return {"job": _tone_view()}


@app.delete("/api/tone/job")
def tone_stop():
    job, proc = _TONE["job"], _TONE["proc"]
    if job and job.get("status") == "running":
        job["status"] = "stopped"
        if proc is not None:
            proc.terminate()
    return {"ok": True}


@app.get("/api/tone/prints")
def tone_prints():
    """The library of prints: every one of every song, with its band and song, the match, the core
    and its sound in numbers and curves — the newest first."""
    root = _resolve_under_music_root("Stems")
    out = []
    for t in sorted(root.glob(f"*/{TONE_DIR}")) if root is not None and root.is_dir() else []:
        d = t.parent
        prints = _tone_prints_of(d)
        if not prints:
            continue
        m = _stems_meta(d)
        for p in prints:
            rep_ = p.get("report") or {}
            out.append(_tone_summary(p) | {"song": d.name, "artist": m.get("artist") or "", "title": m.get("title") or d.name,
                                           "album": m.get("album") or "", "drive_db": p.get("drive_db"), "room": p.get("room"),
                                           "width": (p.get("width") or {}).get("on"), "record": rep_.get("record"), "you": rep_.get("you"),
                                           "curve": rep_.get("curve")})
    return sorted(out, key=lambda x: -(x.get("created") or 0))


@app.get("/api/tone/{name}")
def tone_state(name: str, print: str = ""):
    d = _tone_song(name)
    if d is None:
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    job = _tone_view()
    prints = _tone_prints_of(d)
    sel = next((p for p in prints if p["id"] == print), prints[0] if prints else None)
    if sel is not None:
        sel = sel | {"prev": _tone_print(d, sel["id"], "prev-") is not None}
    return {"ready": _tone_ready(), "stems": _tone_stems(d), "takes": _tone_takes(d), "prints": [_tone_summary(p) for p in prints],
            "print": sel, "job": job if job and job["song"] == name else None, "busy": bool(job and job["status"] == "running")}


@app.get("/api/tone/{name}/sections")
def tone_sections(name: str, stem: str = "guitar"):
    d = _tone_song(name)
    if d is None or stem not in _tone_stems(d):
        return JSONResponse({"error": "Tahle píseň nemá stopu kytary."}, status_code=404)
    cache = d / TONE_DIR / f"sections-{stem}.json"
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
        if got.get("mtime") == _stem_file(d, stem).stat().st_mtime:
            return {"sections": got["sections"]}
    except (OSError, ValueError, AttributeError):
        pass
    found = _tone_sections(d, stem)
    try:
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"mtime": _stem_file(d, stem).stat().st_mtime, "sections": found}), encoding="utf-8")
    except OSError:
        pass
    return {"sections": found}


@app.post("/api/tone/{name}/take")
async def tone_take(name: str, start: float, gain_db: float = 0.0, file: UploadFile = File(...)):
    """What came out of the guitar clean while the song played, from `start` (song seconds)."""
    import wave
    d = _tone_song(name)
    if d is None:
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    try:
        tmp = await _upload_to_temp(file)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    folder = d / TONE_DIR
    folder.mkdir(exist_ok=True)
    takes = _tone_takes(d)
    n = max([int(t["file"][5:-4]) for t in takes] + [0]) + 1
    made = folder / f"take-{n}.wav"
    try:
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(tmp), "-ac", "1", "-c:a", "pcm_s24le", str(made)],
                           capture_output=True, timeout=120)
        with wave.open(str(made), "rb") as w:
            sr, dur = w.getframerate(), w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error, subprocess.SubprocessError):
        made.unlink(missing_ok=True)
        return JSONResponse({"error": "Nahrávka se nedala přečíst."}, status_code=400)
    finally:
        tmp.unlink(missing_ok=True)
    if r.returncode != 0 or dur < 2:
        made.unlink(missing_ok=True)
        return JSONResponse({"error": "Nahrávka je kratší než 2 s."}, status_code=400)
    takes.append({"file": made.name, "start": round(max(0.0, start), 4), "dur": round(dur, 3), "sr": sr, "gain_db": gain_db, "at": time.time()})
    for old in takes[:-8]:                                          # the newest eight are kept
        (folder / old["file"]).unlink(missing_ok=True)
    takes = takes[-8:]
    (folder / "takes.json").write_text(json.dumps(takes), encoding="utf-8")
    return {"take": takes[-1], "takes": takes}


@app.delete("/api/tone/{name}/take/{file}")
def tone_take_delete(name: str, file: str):
    d = _tone_song(name)
    if d is None or not _re_tone_take.fullmatch(file):
        return JSONResponse({"error": "Nahrávka nenalezena."}, status_code=404)
    takes = [t for t in _tone_takes(d) if t["file"] != file]
    (d / TONE_DIR / file).unlink(missing_ok=True)
    (d / TONE_DIR / "takes.json").write_text(json.dumps(takes), encoding="utf-8")
    return {"takes": takes}


class ToneFitRequest(BaseModel):
    start: float
    end: float
    stem: str = "guitar"
    refine: bool = False       # from the print there is ("Doladit"): only its best cores again
    print: str = ""            # …which one of the song's prints
    current_amp: str = ""      # the amp and cabinet the live guitar has now: how it sounds before the print
    current_cab: str = ""
    current_name: str = ""


@app.post("/api/tone/{name}/fit")
def tone_fit(name: str, req: ToneFitRequest):
    d = _tone_song(name)
    if d is None:
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    if req.stem not in _tone_stems(d):
        return JSONResponse({"error": "Tahle píseň nemá stopu kytary."}, status_code=400)
    if not _tone_ready():
        return JSONResponse({"error": "Chybí engine otisku (prostředí pick-tone nebo Node)."}, status_code=503)
    length = req.end - req.start
    if length < 4 or length > 40:
        return JSONResponse({"error": "Úsek musí mít od 4 do 40 vteřin."}, status_code=400)
    takes = [t for t in _tone_takes(d) if min(req.end, t["start"] + t["dur"]) - max(req.start, t["start"]) >= 2][-3:]
    if not takes and not _guitar_active():
        return JSONResponse({"error": "Nejdřív ten úsek zahraj s písní — nebo si v Moje kytara nahraj kalibraci, pak to jde i bez hraní."}, status_code=400)
    current = None
    if req.current_amp or req.current_cab:
        model, ir = _resolve_amp_path(req.current_amp) if req.current_amp else None, _resolve_amp_path(req.current_cab) if req.current_cab else None
        current = {"model": str(model) if model is not None and model.is_file() else None,
                   "ir": str(ir) if ir is not None and ir.is_file() else None, "name": req.current_name[:80]}
    if req.refine and _tone_print(d, req.print) is None:
        return JSONResponse({"error": "Není co doladit — nejdřív vytvoř otisk."}, status_code=400)
    ok, why = _sysload.room(1.5, "tónový otisk")
    if not ok:
        return JSONResponse({"error": why}, status_code=503)
    with _SVC_LOCK:
        if (_TONE["job"] or {}).get("status") == "running":
            return JSONResponse({"error": "Jeden otisk se už počítá — počkej, až doběhne."}, status_code=409)
        job = {"song": name, "status": "running", "stage": "listen", "done": 0, "of": 0, "started": time.time(),
               "start": float(req.start), "end": float(req.end), "stem": req.stem, "print": req.print if req.refine else ""}
        _TONE["job"] = job
    threading.Thread(target=_tone_run, args=(job, d, _stem_file(d, req.stem), takes, req.refine, current), daemon=True).start()
    return _tone_view()


@app.post("/api/tone/{name}/undo")
def tone_undo(name: str, print: str = ""):
    """The print before the last "Doladit" back (and the last one kept in its place)."""
    d = _tone_song(name)
    if d is None or _tone_print(d, print, "prev-") is None or _tone_print(d, print) is None:
        return JSONResponse({"error": "Není k čemu se vrátit."}, status_code=404)
    t = _tone_at(d, print)
    for a, b in (("print.json", "prev-print.json"), ("print-ir.wav", "prev-print-ir.wav")):
        (t / a).replace(t / "swap.tmp")
        (t / b).replace(t / a)
        (t / "swap.tmp").replace(t / b)
    for f in ("you.wav", "abx-record.wav", "abx-you.wav", "you.mp3", "record.mp3"):     # made with the other print
        (t / f).unlink(missing_ok=True)
    return {"print": _tone_print(d, print)}


class ToneEditRequest(BaseModel):
    name: Optional[str] = None         # "" gives the naming back to the app (band – song)
    category: Optional[str] = None     # one of TONE_CATEGORIES


@app.patch("/api/tone/{name}/prints/{pid}")
def tone_edit(name: str, pid: str, req: ToneEditRequest):
    """A print renamed, or filed under another kind of sound."""
    d = _tone_song(name)
    p = _tone_print(d, pid) if d is not None else None
    if p is None:
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    if req.category is not None and req.category not in TONE_CATEGORIES:
        return JSONResponse({"error": "Taková kategorie není."}, status_code=400)
    if req.name is not None:
        title = " ".join(req.name.split())[:80]
        if title:
            p["name"], p["name_auto"] = title, False
        else:
            p.pop("name", None)
            p["name_auto"] = True
    if req.category is not None:
        p["category"], p["category_auto"] = req.category, False
    p.pop("prev", None)
    (_tone_at(d, pid) / "print.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    return _tone_summary(next(x for x in _tone_prints_of(d) if x["id"] == pid))


@app.delete("/api/tone/{name}/prints/{pid}")
def tone_delete(name: str, pid: str):
    d = _tone_song(name)
    if d is None or _tone_print(d, pid) is None:
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    shutil.rmtree(_tone_at(d, pid), ignore_errors=True)
    return {"prints": [_tone_summary(p) for p in _tone_prints_of(d)]}


@app.get("/api/tone/{name}/file/{file}")
def tone_file(name: str, file: str, print: str = ""):
    """A take (the song's), or one of a print's files."""
    d = _tone_song(name)
    if d is None or not (file in TONE_FILES or _re_tone_take.fullmatch(file)):
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    if _re_tone_take.fullmatch(file):
        path = d / TONE_DIR / file
    elif _tone_print(d, print) is not None:
        path = _tone_at(d, print) / file
    else:
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    if not path.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return FileResponse(path, media_type="audio/mpeg" if file.endswith(".mp3") else "audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/tone/{name}/preview/{which}")
def tone_preview(name: str, which: str, print: str = ""):
    """A print's sound to hear in the library: the take through it ("you"), or the record's
    guitar on the same stretch ("record") — short MP3s, made once."""
    d = _tone_song(name)
    p = _tone_print(d, print) if d is not None else None
    if p is None or which not in ("you", "record"):
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    t = _tone_at(d, print)
    made = t / f"{which}.mp3"
    if not made.is_file():
        if which == "you":
            cmd = [ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(t / "you.wav")]
        else:
            src = _stem_file(d, p.get("stem") or "guitar")
            if src is None:
                return JSONResponse({"error": "Stopa kytary chybí."}, status_code=404)
            cmd = [ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-ss", f"{float(p['start']):.3f}", "-t",
                   f"{float(p['end']) - float(p['start']):.3f}", "-i", str(src)]
        r = subprocess.run(cmd + ["-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k", str(made)], capture_output=True, timeout=120)
        if r.returncode != 0 or not made.is_file():
            made.unlink(missing_ok=True)
            return JSONResponse({"error": "Ukázku se nepodařilo připravit."}, status_code=500)
    return FileResponse(made, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/tone/{name}/abx")
def tone_abx(name: str, print: str = ""):
    """The stretch of the print as two mixes for the blind test: the band with the record's
    guitar, and the band with the player's take through the print in its place."""
    import numpy as np
    d = _tone_song(name)
    p = _tone_print(d, print) if d is not None else None
    t = _tone_at(d, print) if p is not None else None
    if p is None or not (t / "you.wav").is_file():
        return JSONResponse({"error": "Nejdřív vytvoř otisk."}, status_code=404)
    if (t / "abx-record.wav").is_file() and (t / "abx-you.wav").is_file():
        return {"a": "abx-record.wav", "b": "abx-you.wav"}
    rate, start, length = 44100, float(p["start"]), float(p["end"]) - float(p["start"])

    def pcm(path, offset=start):
        raw = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-nostdin", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(path),
                              "-ac", "2", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, timeout=120).stdout
        x = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
        out = np.zeros((int(length * rate), 2), np.float32)
        out[:min(len(x), len(out))] = x[:len(out)]
        return out
    guitar = _stem_file(d, p.get("stem") or "guitar")
    if guitar is None:
        return JSONResponse({"error": "Stopa kytary chybí."}, status_code=404)
    band = np.zeros((int(length * rate), 2), np.float32)
    for f in _tone_band(d, p.get("stem") or "guitar"):
        band += pcm(f)
    rec, you = band + pcm(guitar), band + pcm(t / "you.wav", 0.0)
    peak = max(float(np.abs(rec).max()), float(np.abs(you).max()), 1e-6)
    import wave
    for arr, f in ((rec, "abx-record.wav"), (you, "abx-you.wav")):
        y = np.clip(arr * min(1.0, 0.95 / peak), -1, 1)
        with wave.open(str(t / f), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((y * 32767).astype("<i2").tobytes())
    return {"a": "abx-record.wav", "b": "abx-you.wav"}


# ---- Skladatel: a variant the player chose goes into the library as a song —
# and, when asked, is taken apart into stems and its vocals sung again in the
# player's own voice (the separation queue first, then the singing job).
class StudioUse(BaseModel):
    separate: bool = True
    voice: str = ""            # an own voice: the song's vocals are sung again in it


def _studio_sing_when_ready(stems_name: str, voice: str) -> None:
    end = time.time() + 3 * 3600
    while time.time() < end:
        if _stems_ready(stems_name):
            for _ in range(240):                           # one singing job at a time: wait for the other to end
                res = myvoice_start(stems_name, MyVoiceRequest(voice=voice))
                if not (isinstance(res, JSONResponse) and res.status_code == 409):
                    return
                time.sleep(30)
            return
        time.sleep(5)


@app.post("/api/studio/projects/{pid}/variants/{vid}/use")
def studio_use(pid: str, vid: str, req: StudioUse):
    p = _studio.load(pid)
    src = _studio.variant_file(p, vid) if p else None
    if src is None:
        return JSONResponse({"error": "Tahle varianta tu není."}, status_code=404)
    if req.voice and _svc_voice_file(req.voice) is None:
        return JSONResponse({"error": "Takový hlas tu není."}, status_code=404)
    v = _studio.variant(p, vid)
    folder = MUSIC_ROOT / "Songs"
    folder.mkdir(parents=True, exist_ok=True)
    rel = v.get("used")
    if not rel or not (MUSIC_ROOT / rel).is_file():
        base = _safe_name(f"{p['name']} - {v['name']}")
        dest, n = folder / f"{base}.mp3", 2
        while dest.exists():
            dest = folder / f"{base} ({n}).mp3"
            n += 1
        done = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-nostdin", "-i", str(src), "-c", "copy",
                               "-metadata", f"title={p['name']} ({v['name']})", "-metadata", "artist=Pick Skladatel",
                               "-metadata", f"album={p['name']}", str(dest)], capture_output=True, timeout=120)
        if done.returncode != 0 or not dest.is_file():
            shutil.copy(src, dest)
        rel = str(dest.relative_to(MUSIC_ROOT))
    out = {"rel": rel, "name": Path(rel).stem}
    made = _studio.stems_of(pid, vid)
    safe = _safe_name(Path(rel).stem)
    if (req.separate or req.voice) and len(made) == len(STEM_NAMES) and not _stems_ready(safe):
        # Skladatel has already taken it apart: the stems are put where Steems keeps them, no second separation
        raw = MUSIC_ROOT / "Stems" / ".raw" / "htdemucs_6s" / Path(rel).stem
        shutil.rmtree(raw, ignore_errors=True)
        raw.mkdir(parents=True)
        for s in made:
            shutil.copy(_studio.stems_dir(pid, vid) / f"{s}.mp3", raw / f"{s}.mp3")
        try:
            _finalize_stems_job({"stems_source": str(MUSIC_ROOT / rel), "stems_name": safe})
        except OSError as e:
            return JSONResponse({"error": f"Stopy se nepodařilo uložit: {e}"}, status_code=500)
    if req.separate or req.voice:
        job = separate_stems(StemsSeparateRequest(rel=rel))
        if isinstance(job, JSONResponse):
            return job
        out["stems"] = job
        if req.voice:
            threading.Thread(target=_studio_sing_when_ready, args=(job["name"], req.voice), daemon=True).start()
    with _studio._LOCK:
        fresh = _studio.load(pid)
        fv = _studio.variant(fresh, vid) if fresh else None
        if fv is not None:
            fv["used"] = rel
            if req.voice:
                fv["sung"] = next((x["name"] for x in _svc_voices() if x["id"] == req.voice), req.voice)
            _studio.save(fresh)
    return out


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
    if kept and kept.get("synced"):
        return kept
    # A text without times is looked for again: a timed one may be there now, or under a
    # length a second off (LRCLib holds several versions of a song) — it runs with the song.
    result = _lookup_lyrics_online(title, artist, album, duration, video_id)
    if not result.get("synced") and duration:
        for d in (duration + 1, duration - 1, duration + 2, duration - 2):
            near = _fetch_lrclib(title, artist, None, d)
            if near and near.get("synced") and near.get("lines"):
                result = near
                break
    if kept and not result.get("synced"):
        return kept
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
        # a video's name ("uploader | Band- Song (lyrics)"), read the way song names are tidied
        t = _names.tidy(artist or "", title or "")
        if t["title"] and (t["title"] != title or (t["artist"] or "") != (artist or "")):
            result = _fetch_lrclib(t["title"], t["artist"] or "", album, duration) or result
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


@app.get("/api/lyrics/choices")
def lyrics_choices(artist: str = "", q: str = ""):
    """Songs of a band that LRCLib has texts for — to pick from when the song's own name finds none.
    Timed ones first, each song once (the version nearest a typical length)."""
    artist, q = (artist or "").strip(), (q or "").strip()
    if not artist and not q:
        return {"items": []}
    # by the band's name as a band (a bare word search is often refused when LRCLib is busy),
    # and what was typed as a title of it; with nothing of the band found, as typed
    asks = []
    if artist:
        asks.append({"artist_name": artist, "track_name": q} if q else {"artist_name": artist, "q": artist})
    if q:
        asks.append({"q": f"{artist} {q}".strip()})
    rows, failed = [], 0
    for params in asks:
        for attempt in range(2):
            try:
                r = httpx.get("https://lrclib.net/api/search", params=params, headers={"User-Agent": "Pick/1.0"}, timeout=15.0)
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                got = r.json()
                rows += got if isinstance(got, list) else []
                break
            time.sleep(1.0)
        else:
            failed += 1
        if rows:
            break
    if not rows and failed == len(asks):
        return JSONResponse({"error": "LRCLib teď neodpovídá, zkus to za chvíli."}, status_code=502)
    fold = _name_key
    want = fold(artist)
    seen, items = {}, []
    for x in rows if isinstance(rows, list) else []:
        if not isinstance(x, dict) or x.get("instrumental") or not (x.get("syncedLyrics") or x.get("plainLyrics")):
            continue
        band = str(x.get("artistName") or "")
        if artist and want not in fold(band):
            continue
        title = str(x.get("trackName") or "").strip()
        key = title.lower()
        row = {"id": x.get("id"), "title": title, "artist": band, "album": x.get("albumName") or "",
               "duration": x.get("duration"), "synced": bool(x.get("syncedLyrics"))}
        if key not in seen or (row["synced"] and not seen[key]["synced"]):
            seen[key] = row
    items = sorted(seen.values(), key=lambda r: (not r["synced"], r["title"].lower()))
    return {"items": items[:200]}


class LyricsPick(BaseModel):
    id: int
    title: str = ""            # the song it is for: kept under its name, so the next time it is there at once
    artist: str = ""
    duration: Optional[float] = None


@app.post("/api/lyrics/pick")
def lyrics_pick(req: LyricsPick):
    try:
        r = httpx.get(f"https://lrclib.net/api/get/{int(req.id)}", headers={"User-Agent": "Pick/1.0"}, timeout=15.0)
        got = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        got = None
    if not isinstance(got, dict) or not (got.get("syncedLyrics") or got.get("plainLyrics")):
        return JSONResponse({"error": "Tenhle text se nepodařilo stáhnout."}, status_code=502)
    data = _lrclib_to_data(got, req.duration)
    if req.title:
        _lyrics_keep(req.title, req.artist, req.duration, data)
    return data


class LyricsKeepRequest(BaseModel):
    title: str
    artist: str = ""
    duration: Optional[float] = None
    lines: list


@app.post("/api/lyrics/keep")
def lyrics_keep(req: LyricsKeepRequest):
    """A text corrected by hand (a word whisper misheard): kept for the song, as it is now."""
    lines = [{"timeInMs": int(x.get("timeInMs") or 0), "text": str(x.get("text") or "")[:300]}
             for x in req.lines[:2000] if isinstance(x, dict)]
    if not req.title.strip() or not lines:
        return JSONResponse({"error": "Chybí text."}, status_code=400)
    data = {**(_lyrics_kept(req.title, req.artist, req.duration) or {}), "synced": True, "lines": lines, "edited": True}
    _lyrics_keep(req.title, req.artist, req.duration, data)
    return {"ok": True}


@app.get("/api/lyrics/{video_id}")
def get_lyrics_by_video(video_id: str, q: str = "", artist: str = "", album: str = "", duration: float = None):
    """Get lyrics for a video ID. Uses LRCLib with metadata from the caller."""
    title, artist = _split_lyrics_query(q, artist)

    if not title:
        return {"synced": False, "lines": None, "plain": None, "source": None}

    return _lookup_lyrics(title, artist, album, duration, video_id)


# ---- a text heard off the vocals (hear.py): for the songs no lyrics site has,
# Czech ones above all. Whisper listens to the separated singing on this
# computer, one song at a time, in the background; what it heard is kept beside
# the stems. A text known without times gets its times from it instead.
import hear as _hear

_HEAR: dict = {"job": None}
_HEAR_LOCK = threading.Lock()


class HearRequest(BaseModel):
    title: str = ""            # the song the text is kept under, as the mixer asks for it
    artist: str = ""
    duration: Optional[float] = None
    lang: str = "auto"
    again: bool = False        # listen again, not what was heard before


def _hear_lyrics(got: dict, req: HearRequest) -> dict:
    """What was heard as a text for the mixer — the words known already, when there are some, at the heard times."""
    known = _lyrics_kept(req.title, req.artist, req.duration) if req.title else None
    plain = (known or {}).get("plain") if known and not known.get("heard") else None
    lines = _hear.timed(plain, got["lines"]) if plain else []
    data = {"synced": True, "lines": lines or got["lines"], "plain": plain, "heard": True, "lang": got.get("lang"),
            "source": "text z LRCLib, časy ze zpěvu" if lines else "odposlechnuto ze zpěvu"}
    if req.title:
        _lyrics_keep(req.title, req.artist, req.duration, data)
    return data


def _hear_view() -> Optional[dict]:
    job = _HEAR["job"]
    if not job:
        return None
    return {k: job.get(k) for k in ("song", "status", "done", "of", "error", "took")} | {"elapsed": time.time() - job["started"]}


def _hear_run(job: dict, d: Path, vocals: Path, req: HearRequest) -> None:
    try:
        known = _lyrics_kept(req.title, req.artist, req.duration) if req.title else None
        prompt = ((known or {}).get("plain") or "") if known and not known.get("heard") else ""
        got = _hear.heard(vocals, req.lang if req.lang in ("auto", "cs", "en", "sk", "de") else "auto", prompt,
                          progress=lambda n, of: job.update(done=n, of=of), stop=lambda: job.get("status") == "stopped")
        if not got["lines"]:
            raise RuntimeError("Ve stopě zpěvu jsem neslyšel žádná slova.")
        _hear.keep(d, got)
        job.update(status="done", lyrics=_hear_lyrics(got, req), took=time.time() - job["started"])
    except InterruptedError:
        job["status"] = "stopped"
    except Exception as e:                                             # a job never takes the server with it
        if job.get("status") != "stopped":
            job.update(status="error", error=str(e)[:300])


@app.post("/api/stems/{name}/hear")
def stems_hear(name: str, req: HearRequest):
    d = _resolve_under_music_root(f"Stems/{name}")
    if d is None or not d.is_dir() or name.startswith("."):
        return JSONResponse({"error": "Stopy nenalezeny."}, status_code=404)
    vocals = _stem_file(d, "vocals")
    if vocals is None:
        return JSONResponse({"error": "Tahle píseň nemá stopu zpěvu."}, status_code=400)
    got = None if req.again else _hear.kept(d)
    if got:
        return {"status": "done", "song": name, "lyrics": _hear_lyrics(got, req)}
    if not _hear.ready():
        return JSONResponse({"error": "Chybí whisper (brew install whisper-cpp) nebo jeho model."}, status_code=503)
    ok, why = _sysload.room(1.0, "odposlech")
    if not ok:
        return JSONResponse({"error": why}, status_code=503)
    with _HEAR_LOCK:
        job = _HEAR["job"]
        if job and job.get("status") == "running":
            if job["song"] == name:
                return _hear_view()
            return JSONResponse({"error": f"Poslouchám teď jinou píseň ({job['song']}) — chvilku."}, status_code=409)
        job = {"song": name, "status": "running", "done": 0, "of": 0, "started": time.time()}
        _HEAR["job"] = job
    threading.Thread(target=_hear_run, args=(job, d, vocals, req), daemon=True).start()
    return _hear_view()


@app.delete("/api/hear/job")
def hear_stop():
    job = _HEAR["job"]
    if job and job.get("status") == "running":
        job["status"] = "stopped"
        rows = _tasks.table()
        _tasks.end(_tasks.matching(rows, _tasks.tree(rows, os.getpid()), "whisper-cli"))
    return {"ok": True}


@app.get("/api/hear")
def hear_status():
    job = _HEAR["job"]
    out = _hear_view()
    if out and job.get("status") == "done":
        out["lyrics"] = job.get("lyrics")
    return {"job": out, "ready": _hear.ready()}


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
    # "pick practice playlist <name>": a playlist of Steems, played song after song
    if kind == "practice" and _re_mod.match(r"(?i)^(the\s+)?play\s*lists?\s+\S", name or ""):
        return {"kind": "setlist", "query": _re_mod.sub(r"(?i)^(the\s+)?play\s*lists?\s+", "", name).strip()}
    if kind in ("playlist", "open", "stemsband"):        # a band in Steems: the page has the list and finds it there
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
    if kind not in ("song", "band", "practice", "stemsband"):
        return JSONResponse({"error": "Neznámý druh."}, status_code=400)
    return voice_find(kind, name)


@app.post("/api/system/midi-setup")
def midi_setup_open():
    """macOS's Audio MIDI Setup, where a Bluetooth MIDI pedal is connected (Window → MIDI Studio → Bluetooth)."""
    try:
        subprocess.Popen(["open", "-a", "Audio MIDI Setup"])
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}


@app.post("/api/mini/open")
def mini_open():
    """The mini player's window, as the menu bar opens it; brought forward when it is already there.
    Either way it takes over the song the app plays (state["mini_take"])."""
    _MINI_TAKE.update(seq=int(time.time() * 1000), at=time.time())
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
import look as _look
app.include_router(_look.router)
import studio as _studio
app.include_router(_studio.router)
import setlists as _setlists
app.include_router(_setlists.router)

# Zpěvník pro Skladatele (songbook.py): the sound and the text's shape of the songs in Steems.
import songbook as _songbook


def _songbook_analysis(name: str):
    got = stems_analysis(name)
    return got if isinstance(got, dict) else None


def _songbook_lyrics(d: Path):
    m = _stems_meta(d)
    if not m.get("title"):
        return None
    dur = next((x for x in (_audio_tags(f).get("duration") for f in (_stem_file(d, st) for st in STEM_NAMES[:2]) if f) if x), None)
    kept = _lyrics_kept(m["title"], m.get("artist") or "", dur)
    if kept and kept.get("synced"):
        return kept
    try:                                                   # the same search the karaoke bar makes
        got = _lookup_lyrics(m["title"], m.get("artist") or "", m.get("album") or "", dur)
    except Exception:                                      # noqa: BLE001
        got = None
    return got if got and (got.get("lines") or got.get("plain")) else kept


def _songbook_kept_text(d: Path):
    """The song's text as the karaoke bar keeps it, nothing searched for — asked the way the mixer
    asks: its title, its first artist, its length in whole seconds (a second either way: the player
    and the file's tags round it each their own way)."""
    m = _stems_meta(d)
    if not m.get("title"):
        return None
    dur = next((x for x in (_audio_tags(f).get("duration") for f in (_stem_file(d, st) for st in STEM_NAMES[:2]) if f) if x), None)
    secs = [int(round(dur)) + k for k in (0, -1, 1)] if dur else [0]
    for a in dict.fromkeys(((m.get("artist") or "").split(",")[0].strip(), m.get("artist") or "")):
        for n in secs:
            got = _lyrics_kept(m["title"], a, n)
            if got:
                return {"artist": m.get("artist") or "", "title": m["title"], "album": m.get("album") or "", "lyrics": got}
    return None


def _songbook_genres(band: str) -> list:
    """The band's genres as MusicBrainz's listeners tag them, the most agreed first."""
    want = _names.key(band)
    for a in _mb_get("artist", query=f'artist:"{band}"', limit=5).get("artists") or []:
        if _names.key(a.get("name")) == want:
            tags = sorted(a.get("tags") or [], key=lambda t: -int(t.get("count") or 0))
            return [t["name"] for t in tags if t.get("name") and int(t.get("count") or 0) > 0][:5]
    return []


def _songbook_busy() -> bool:
    with _STEMS_LOCK:
        ids = list(_STEMS_QUEUE)
    return any((JOBS.get(j) or {}).get("status") in ("queued", "running") for j in ids)


_songbook.stems_root = lambda: MUSIC_ROOT / "Stems"
_songbook.ffmpeg = ffmpeg_bin
_songbook.analysis_for = _songbook_analysis
_songbook.lyrics_for = _songbook_lyrics
_songbook.kept_text = _songbook_kept_text
_songbook.busy = _songbook_busy
_songbook.genres_lookup = _songbook_genres
app.include_router(_songbook.router)

# ---- Úložiště: the cards, what fills them, a new card made ready, where new songs go
_storage.main = lambda: MUSIC_ROOT
_storage.config = lambda: dict(_read_config().get("storage") or {})
_storage.set_config = lambda cfg: _write_config({"storage": cfg})
app.include_router(_storage.router)

# ---- Kontakt: the player's NI libraries — their plain loops and samples, and Kontakt itself (kontakt.py)
import kontakt as _kontakt
_kontakt.new_project = lambda src, label: _new_project_from(src, label, label)
app.include_router(_kontakt.router)

# ---- Virtuální kytara: riffs written or pasted as a tab (vguitar.py; the page plays them)
import vguitar as _vguitar
app.include_router(_vguitar.router)

# ---- Katalog riffů: the Taby library's tabs taken apart into parts and their riffs (riffbook.py)
import riffbook as _riffbook
_riffbook.library = _pick_tabs
app.include_router(_riffbook.router)
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _riffbook.resume_after_restart()        # the library's reading, cut off by the last stop, goes on

# ---- Nahraná kytara: recorded notes for the virtual guitar and the tab guide (guitar_samples.py)
import guitar_samples as _guitar_samples
app.include_router(_guitar_samples.router)

# ---- Odkaz ze Spotify: a playlist, album or song pasted as its link (spotify_link.py)
import spotify_link as _spotify_link
_spotify_link.client = sp
app.include_router(_spotify_link.router)

# ---- Vizualizace: the player's own videos by theme (Vizualizace/ beside the music), the song's clip
import viz as _viz
_viz.root = lambda: MUSIC_ROOT / "Vizualizace"
_viz.ytdlp = lambda: shutil.which("yt-dlp") or str(Path.home() / ".local" / "venvs" / "spotdl" / "bin" / "yt-dlp")
_viz.search_videos = lambda q: yt().search(q, filter="videos", limit=3)
_viz.serve = _range_file_response
app.include_router(_viz.router)
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _songbook.resume_after_restart()        # a batch cut off by the last stop goes on
    _saves_resume()                         # …and the songs that were waiting to be saved
_studio.demucs_device = _demucs_device     # Skladatel takes its variants apart the same way, on the GPU


# ---- how hard the computer works (sysload.py): shown in the settings, and asked
# before a heavy job starts, so two models at once never push it into swapping.
import sysload as _sysload


def _heavy_jobs() -> list:
    out = []
    with _STEMS_LOCK:
        ids = list(_STEMS_QUEUE)
    out += [f"rozdělování ({JOBS[j]['stems_name']})" for j in ids if (JOBS.get(j) or {}).get("status") == "running"]
    if (_SVC["job"] or {}).get("status") == "running":
        out.append("zpěv jiným hlasem")
    if (_HEAR["job"] or {}).get("status") == "running":
        out.append("odposlech textu")
    if (_RESING["job"] or {}).get("status") == "running":
        out.append("přezpívání řádku")
    if (_TONE["job"] or {}).get("status") == "running":
        out.append("tónový otisk")
    if _songbook.JOB.get("running"):
        out.append("rozbor Zpěvníku")
    try:
        if _studio.progress_now():
            out.append("Skladatel")
    except Exception:
        pass
    return out


_sysload.jobs = _heavy_jobs
_studio.room = lambda: _sysload.room(3.0, "Skladatel")[0]
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _sysload.start()


@app.get("/api/system")
def system_load():
    return _sysload.now()


# ---- Úlohy a procesy (Nastavení → Systém): what Pick is doing and has lined up, what each
# of it takes of the processor and the memory — the busiest first — and a hand on it: pause,
# go on, stop. tasks.py reads the processes; here they are put to Pick's own jobs, each
# process counted once (the first job that claims it), the rest of what the server started
# shown by its program.
import tasks as _tasks

NIGHT_DIR = HOME / ".local" / "share" / "spotdl-helper" / "night"
_TASK_PROGRAMS = [("demucs", "Rozdělení na stopy"), ("whisper-cli", "Odposlech textu"), ("tone_worker.py", "Tónový otisk"),
                  ("tone_nam.mjs", "Tónový otisk"), ("svc_worker.py", "Zpěv jiným hlasem"), ("tts_worker.py", "Hlas OmniVoice"),
                  ("resing_worker.py", "Přezpívání"), ("analyze.py", "Analýza písně"), ("audio2midi.py", "Převod na noty"),
                  ("assistant_worker.py", "Asistent"), ("lyrics_worker.py", "Hledání textu"), ("/bin/spotdl", "Stahování"),
                  ("yt-dlp", "Stahování"), ("yt_dlp", "Stahování"), ("ffmpeg", "Převod zvuku (ffmpeg)"), ("ffprobe", "Čtení zvuku (ffprobe)"),
                  ("caffeinate", "Počítač nespí (naplánované úlohy)")]
_SAVE_STATES = {"looking": "hledá tab a text", "downloading": "stahuje", "separating": "rozděluje na stopy"}


def _task_program(args: str) -> str:
    for word, name in _TASK_PROGRAMS:
        if word in args:
            return name
    return Path(args.split()[0]).name if args else "?"


def _night_pids(rows: dict, script: str) -> list:
    return [pid for pid, r in rows.items() if r["args"].endswith(script) or f"/night/{script}" in r["args"]]


def _tasks_now(sort: str = "cpu") -> dict:
    rows = _tasks.table()
    me = os.getpid()
    mine = _tasks.tree(rows, me)
    used: set = set()
    out = []

    def add(tid, name, detail="", state="běží", pids=(), can=(), progress=None, queue=None, note="", paused=None):
        procs = set()
        for p in pids:
            procs |= _tasks.tree(rows, p)
        procs -= used
        used.update(procs)
        held = _tasks.paused(rows, procs) if paused is None else paused
        if not held and procs & set(_guard.STATE["held"]):
            state = "ustupuje hudbě"
        out.append({"id": tid, "name": name, "detail": detail, "state": "pozastaveno" if held else state,
                    "cpu": round(sum(rows[p]["cpu"] for p in procs), 1), "mem": sum(rows[p]["rss"] for p in procs),
                    "procs": len(procs), "progress": progress, "queue": queue or [], "note": note,
                    "can": [c for c in can if not (c == "pause" and held) and not (c == "resume" and not held)]})

    # separations: the one running, and the queue
    with _STEMS_LOCK:
        ids = list(_STEMS_QUEUE)
    waiting = []
    for jid in ids:
        job = JOBS.get(jid) or {}
        if job.get("status") == "running":
            proc = job.get("proc")
            add(f"stems:{jid}", "Rozdělení na stopy", job.get("stems_name") or "", pids=[proc.pid] if proc else [],
                can=("pause", "resume", "stop"), progress=(job.get("progress") or 0) / 100,
                note="Pozastavené drží paměť; Ukončit ji uvolní a píseň zůstane nerozdělená.")
        elif job.get("status") == "queued":
            waiting.append(job.get("stems_name") or "")
    if waiting or _STEMS_PAUSED["on"]:
        add("stems-queue", "Fronta rozdělování", f"čeká {len(waiting)}" if waiting else "nic nečeká", state="čeká", queue=waiting,
            can=("pause", "resume", "stop") if waiting else ("resume",), paused=_STEMS_PAUSED["on"],
            note="Pozastavení: další píseň se nezačne rozdělovat. Ukončit: čekající se zruší.")
    # songs being saved (download → tab and text → separation), one after another
    with _SAVES_LOCK:
        saves = sorted(_SAVES.values(), key=lambda x: x["started"])
    working = [x for x in saves if x["state"] not in ("waiting", "ready", "error")]
    queued = [x for x in saves if x["state"] == "waiting"]
    if working or queued or _SAVES_PAUSED["on"]:
        cur = working[0] if working else None
        add("saves", "Ukládání písní", (f"{cur['artists']} – {cur['title']} ({_SAVE_STATES.get(cur['state'], cur['state'])})" if cur else "")
            + (f" · čeká {len(queued)}" if queued else ""), state="běží" if working else "čeká",
            pids=sorted(_tasks.matching(rows, mine, "/bin/spotdl", "yt-dlp", "yt_dlp")), can=("pause", "resume", "stop") if queued or _SAVES_PAUSED["on"] else (),
            queue=[f"{x['artists']} – {x['title']}" for x in queued[:40]] + ([f"… a dalších {len(queued) - 40}"] if len(queued) > 40 else []),
            paused=_SAVES_PAUSED["on"], note="Pozastavení: rozdělaná píseň se dodělá, další se nezačne. Ukončit: čekající se zruší.")
    # the songbook batch
    sb, left = _songbook.JOB, _songbook.left() or {}
    if sb.get("running") or left.get("names"):
        run = bool(sb.get("running"))
        add("songbook", "Rozbor Zpěvníku", f"{sb.get('done', 0)}/{sb.get('total', 0)} · {sb.get('now') or ''}" if run
            else f"zbývá {len(left.get('names') or [])} písní", state="běží",
            pids=sorted(_tasks.matching(rows, mine, "analyze.py")) if run else [], can=("pause", "stop") if run else ("resume", "stop"),
            progress=(sb.get("done", 0) / sb["total"]) if run and sb.get("total") else None, paused=not run,
            note="Pozastavení počká na dokončení písně; zbytek pokračuje i po restartu, až dáš Pokračovat.")
    # the one-at-a-time jobs
    j = _HEAR["job"] or {}
    if j.get("status") == "running":
        add("hear", "Odposlech textu", f"{j.get('song')} · {j.get('done', 0)}/{j.get('of', 0)}",
            pids=sorted(_tasks.matching(rows, mine, "whisper-cli")), can=("pause", "resume", "stop"),
            progress=(j.get("done", 0) / j["of"]) if j.get("of") else None)
    j = _TONE["job"] or {}
    if j.get("status") == "running":
        proc = _TONE["proc"]
        add("tone", "Tónový otisk", f"{j.get('song')} · {j.get('stage') or ''} {j.get('done', 0)}/{j.get('of', 0)}".strip(),
            pids=[proc.pid] if proc else [], can=("pause", "resume", "stop"), progress=(j.get("done", 0) / j["of"]) if j.get("of") else None)
    j = _RESING["job"] or {}
    if j.get("status") == "running":
        proc = _RESING["proc"]
        add("resing", "Přezpívání řádku", f"{j.get('song')} · {j.get('stage') or ''}", pids=[proc.pid] if proc else [], can=("stop",))
    j = _SVC["job"] or {}
    if j.get("status") == "running":
        proc = _SVC["proc"]
        add("myvoice", "Zpěv jiným hlasem", str(j.get("song") or ""), pids=[proc.pid] if proc else [], can=("stop",))
    # Skladatel and its model
    try:
        pr = _studio.progress_now()
    except Exception:                                        # noqa: BLE001
        pr = None
    try:
        epid = int(_studio.ENGINE_PID.read_text().strip())
    except (OSError, ValueError):
        epid = None
    if pr or (epid and epid in rows):
        now = (pr or {}).get("now") or {}
        add("studio", "Skladatel", f"{now.get('project_name', '')} · {now.get('name', '')}" if now
            else f"ve frontě {pr['count']}" if pr else "model připravený, nic neskládá",
            state="běží" if now else "čeká" if pr else "připraveno", pids=[epid] if epid and epid in rows else [],
            can=() if pr else ("stop",), progress=now.get("pct"),
            note="Skládání se ovládá ve Skladateli." if pr else "Model drží paměť; Ukončit ho uvolní (při dalším skládání se spustí sám).")
    proc = _OWN_TTS["proc"]
    if proc is not None and proc.poll() is None:
        add("tts", "Hlas OmniVoice", "model připravený v paměti", state="připraveno", pids=[proc.pid], can=("stop",),
            note="Ukončit uvolní paměť; při dalším použití se spustí sám.")
    # what is put off to the night (Naplánované úlohy)
    plan = [t for t in _planner.tasks()["tasks"] if t["status"] in ("waiting", "running")]
    if plan:
        cur = next((t for t in plan if t["status"] == "running"), None)
        rest = [t["label"] for t in plan if t["status"] == "waiting"]
        add("plan", "Naplánované úlohy", (f"teď: {cur['label']}" if cur else f"na noc od {(_read_config().get('plan') or {}).get('start', '23:00')}")
            + (f" · čeká {len(rest)}" if rest else ""), state="běží" if cur else "čeká", queue=rest,
            note="Spouštějí se jedna po druhé, když nic těžkého neběží; upravíš je v Naplánovaných úlohách níž.")
    # what runs beside Pick, started by hand
    night = _night_pids(rows, "night.py")
    if night:
        try:
            st = json.loads((NIGHT_DIR / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        add("night", "Noční plán", f"playlisty {st.get('A', '?')} · texty {st.get('B', '?')} · Zpěvník {st.get('C', '?')}", pids=night,
            can=("pause", "resume", "stop"), note="Řídí stahování, texty a Zpěvník. Písně, které už zařadil, se ukládají dál (Ukládání písní).")
    watch = _night_pids(rows, "restart-when-idle.py")
    if watch:
        add("watch", "Hlídač restartu Picku", "restartuje Pick, až nic nepoběží", pids=watch, can=("stop",))
    # the rest the server started, by program
    for pid in sorted(p for p in mine if rows[p]["ppid"] == me and p not in used):
        add(f"proc:{pid}", _task_program(rows[pid]["args"]), rows[pid]["args"][:140], pids=[pid], can=("pause", "resume", "stop"),
            note="Proces, který si Pick spustil; ukončení může přerušit, co právě dělá.")
    add("server", "Server Picku", "obsluha aplikace", pids=[me])
    parent = rows.get(me, {}).get("ppid")
    if parent and parent in rows and "Pick" in rows[parent]["args"]:
        add("app", "Aplikace Pick (okno)", "", pids=[parent])
    key = "mem" if sort == "mem" else "cpu"
    out.sort(key=lambda t: (t["procs"] == 0, -t[key], -t["mem"] if key == "cpu" else -t["cpu"]))
    s = _sysload.now()
    return {"tasks": out, "cpus": s.get("cpus") or os.cpu_count() or 1, "total_mem": s.get("total"),
            "cpu": round(sum(t["cpu"] for t in out), 1), "mem": sum(t["mem"] for t in out), "sort": key}


@app.get("/api/tasks")
def tasks_list(sort: str = "cpu"):
    return _tasks_now(sort)


@app.post("/api/tasks/{tid}/{action}")
def task_act(tid: str, action: str, sort: str = "cpu"):
    """Pause, go on with or stop one of them (as listed by /api/tasks)."""
    if action not in ("pause", "resume", "stop"):
        return JSONResponse({"error": "Neznámý pokyn."}, status_code=404)
    rows = _tasks.table()
    me = os.getpid()
    mine = _tasks.tree(rows, me)
    kind, _, key = tid.partition(":")
    gone = JSONResponse({"error": "Tahle úloha už neběží."}, status_code=404)

    def tree_of(pids):
        got = set()
        for p in pids:
            got |= _tasks.tree(rows, p)
        return got

    def by_signal(pids):
        if not pids:
            return gone
        if action == "pause":
            _tasks.pause(pids)
        elif action == "resume":
            _tasks.resume(pids)
        else:
            _tasks.end(pids)
        return None

    err = None
    if kind == "stems":
        job = JOBS.get(key) or {}
        proc = job.get("proc")
        if job.get("status") != "running" or proc is None:
            return gone
        if action == "stop":
            job["stopped"] = True
        err = by_signal(tree_of([proc.pid]))
    elif kind == "stems-queue":
        if action == "pause":
            _STEMS_PAUSED["on"] = True
        elif action == "resume":
            _STEMS_PAUSED["on"] = False
            _STEMS_WAKE.set()
        else:
            with _STEMS_LOCK:
                for jid in _STEMS_QUEUE:
                    j = JOBS.get(jid)
                    if j and j["status"] == "queued":
                        j.update(status="cancelled", finished=time.time())
            _STEMS_PAUSED["on"] = False
    elif kind == "saves":
        if action == "pause":
            _SAVES_PAUSED["on"] = True
        elif action == "resume":
            _SAVES_PAUSED["on"] = False
        else:
            practice_save_many_stop()
            _SAVES_PAUSED["on"] = False
    elif kind == "songbook":
        if action == "resume":
            got = _songbook.songbook_resume()
            if isinstance(got, JSONResponse):
                return got
        else:
            _songbook.songbook_stop(forget=action == "stop")
    elif kind == "hear":
        if (_HEAR["job"] or {}).get("status") != "running":
            return gone
        if action == "stop":
            hear_stop()
        else:
            err = by_signal(_tasks.matching(rows, mine, "whisper-cli"))
    elif kind == "tone":
        proc = _TONE["proc"]
        if (_TONE["job"] or {}).get("status") != "running":
            return gone
        if action == "stop":
            pids = tree_of([proc.pid]) if proc else set()
            tone_stop()
            _tasks.end(pids)
        else:
            err = by_signal(tree_of([proc.pid]) if proc else set())
    elif kind in ("resing", "myvoice") and action == "stop":
        resing_stop() if kind == "resing" else myvoice_stop()
    elif kind == "studio" and action == "stop":
        got = _studio.stop_engine()
        if isinstance(got, JSONResponse):
            return got
    elif kind == "tts" and action == "stop":
        with _OWN_TTS_LOCK:
            proc = _OWN_TTS["proc"]
            if proc is not None and proc.poll() is None:
                proc.terminate()
            _OWN_TTS["proc"] = None
    elif kind in ("night", "watch"):
        err = by_signal(tree_of(_night_pids(rows, "night.py" if kind == "night" else "restart-when-idle.py")))
    elif kind == "proc":
        try:
            pid = int(key)
        except ValueError:
            return gone
        if pid not in mine or pid == me:
            return gone
        err = by_signal(tree_of([pid]))
    else:
        return JSONResponse({"error": "Tohle se ovládat nedá."}, status_code=400)
    if err is not None:
        return err
    time.sleep(0.3)                                          # what was signalled has moved on
    return _tasks_now(sort)


# ---- Ochrana zvuku (audioguard.py; Nastavení → Zvuk a MIDI → Zvuk bez výpadků): while music
# or the guitar sounds in a Pick window, the heavy jobs step back — into macOS's background
# band, or paused outright — and are given back when the sound stops.
import audioguard as _guard


def _studio_engine_pid() -> list:
    try:
        return [int(_studio.ENGINE_PID.read_text().strip())]
    except (OSError, ValueError, AttributeError):
        return []


_guard.extra = _studio_engine_pid
_guard.STATE.update(on=bool((_read_config().get("audio_guard") or {}).get("on", True)),
                    mode=(_read_config().get("audio_guard") or {}).get("mode", "slow"))
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _guard.wake_orphans()
    _guard.start()


@app.on_event("shutdown")
def _guard_end():
    _guard.release()


# ---- Bluetooth MIDI (tools/midi_bt.swift): macOS keeps a pedal it once connected (FS-1-WL,
# Ampero Control…) but after a sleep or a restart often does not connect it again by itself —
# the MIDI pedal's "Připojit / Obnovit" asks it to. Built once with swiftc (Command Line Tools).
MIDI_BT = HOME / ".local" / "share" / "spotdl-helper" / "bin" / "midi-bt"
MIDI_BT_SRC = APP_DIR / "tools" / "midi_bt.swift"


def _midi_bt(cmd: str) -> dict:
    if not MIDI_BT.is_file() or MIDI_BT.stat().st_mtime < MIDI_BT_SRC.stat().st_mtime:
        MIDI_BT.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["/usr/bin/swiftc", "-O", "-o", str(MIDI_BT), str(MIDI_BT_SRC)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("Pomocníka pro Bluetooth MIDI se nepodařilo sestavit (chybí nástroje pro vývojáře macOS).")
    out = subprocess.run([str(MIDI_BT), cmd], capture_output=True, text=True, timeout=30).stdout
    return json.loads(out or "{}")


@app.get("/api/midi/system")
def midi_system():
    """What MIDI macOS has now: its inputs, and the Bluetooth MIDI devices it knows (on or off)."""
    try:
        return _midi_bt("list")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        return JSONResponse({"error": str(e) or "Bluetooth MIDI se nepodařilo zjistit."}, status_code=503)


@app.post("/api/midi/bluetooth")
def midi_bluetooth():
    """Every Bluetooth MIDI device macOS knows, asked to connect again (a switched-on pedal comes back)."""
    try:
        return _midi_bt("connect")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        return JSONResponse({"error": str(e) or "Bluetooth MIDI se nepodařilo připojit."}, status_code=503)


class AudioActiveRequest(BaseModel):
    client: str = ""           # which window (each sends its own)
    on: bool = True


@app.post("/api/audio/active")
def audio_active(req: AudioActiveRequest):
    _guard.ping(req.client[:40] or "page", req.on)
    return _guard.view(_task_program)


class AudioGuardRequest(BaseModel):
    on: Optional[bool] = None
    mode: Optional[str] = None         # "slow" | "pause"


@app.get("/api/audio/guard")
def audio_guard():
    return _guard.view(_task_program)


@app.post("/api/audio/guard")
def audio_guard_set(req: AudioGuardRequest):
    if req.mode is not None and req.mode not in ("slow", "pause"):
        return JSONResponse({"error": "Neznámý režim."}, status_code=400)
    _guard.configure(on=req.on, mode=req.mode)
    _write_config({"audio_guard": {"on": _guard.STATE["on"], "mode": _guard.STATE["mode"]}})
    return _guard.view(_task_program)


# ---- Naplánované úlohy (planner.py; Nastavení → Systém): a heavy job put off to the night is
# the very request the page would have sent; the planner sends it to this server in the night
# window, one after another, each when nothing heavy runs and no music plays.
import planner as _planner


def _plan_idle() -> bool:
    if _heavy_jobs() or _songbook_busy():
        return False
    with _SAVES_LOCK:
        return not _SAVE_QUEUE and _SAVES_NOW["req"] is None


def _plan_outcome(t: dict, answer: dict) -> tuple:
    """How a planned job ended, with a word for the list."""
    kind = t.get("kind")
    if kind in ("hear", "myvoice", "tone"):
        j = {"hear": _HEAR, "myvoice": _SVC, "tone": _TONE}[kind]["job"] or {}
        if j.get("status") == "error":
            return False, str(j.get("error") or "Skončilo chybou.")
        if j.get("status") == "stopped":
            return False, "Zastaveno."
        return True, ""
    if kind == "separate" and "queued" in answer:              # a whole playlist
        return True, f"Rozděleno {answer.get('queued', 0)} písní" + (f", {answer['ready']} už bylo hotových" if answer.get("ready") else "") + "."
    if kind == "separate":
        if answer.get("ready"):
            return True, "Stopy už byly hotové."
        j = JOBS.get(answer.get("job_id") or "") or {}
        if j.get("status") in ("error", "stopped"):
            return False, str(j.get("error") or "Rozdělení se nepovedlo.")
        return True, ""
    if kind == "saves":
        return True, f"Připraveno {answer.get('queued', 0)} písní" + (f", {answer['ready']} už bylo hotových" if answer.get("ready") else "") + "."
    if kind == "songbook":
        errs = len(_songbook.JOB.get("errors") or [])
        return True, (answer.get("note") or f"Rozebráno {answer.get('started', 0)} písní") + (f", {errs} se nepovedlo." if errs else "")
    return True, ""


_planner.idle = _plan_idle
_planner.quiet = lambda: not _guard.sounding()
_planner.settings = lambda: _read_config().get("plan") or {}
_planner.outcome = _plan_outcome
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _planner.start()


@app.on_event("shutdown")
def _planner_end():
    _planner.STATE["stop"] = True


class PlanAddRequest(BaseModel):
    kind: str = "other"
    label: str = ""
    path: str
    body: Optional[dict] = None


class PlanSettingsRequest(BaseModel):
    on: Optional[bool] = None
    ask: Optional[bool] = None         # asked "now or tonight?" before a heavy job
    start: Optional[str] = None        # "23:00"
    end: Optional[str] = None


class PlanNowRequest(BaseModel):
    id: str = ""                       # "" = everything that waits


class PlanMoveRequest(BaseModel):
    delta: int


@app.get("/api/plan")
def plan_view():
    return _planner.view()


@app.post("/api/plan")
def plan_add(req: PlanAddRequest):
    if not _planner.allowed(req.path):
        return JSONResponse({"error": "Tohle se naplánovat nedá."}, status_code=400)
    t = _planner.add(req.kind, req.label, "POST", req.path, req.body)
    return {"task": t, **_planner.view()}


@app.post("/api/plan/settings")
def plan_settings(req: PlanSettingsRequest):
    cfg = dict(_read_config().get("plan") or {})
    for k in ("start", "end"):
        v = getattr(req, k)
        if v is not None:
            if not _re_mod.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v.strip()):
                return JSONResponse({"error": "Čas zadej jako 23:00."}, status_code=400)
            h, m = v.strip().split(":")
            cfg[k] = f"{int(h):02d}:{m}"
    if req.on is not None:
        cfg["on"] = bool(req.on)
    if req.ask is not None:
        cfg["ask"] = bool(req.ask)
    _write_config({"plan": cfg})
    return _planner.view()


@app.post("/api/plan/now")
def plan_now(req: PlanNowRequest):
    if not _planner.now(req.id):
        return JSONResponse({"error": "Taková úloha nečeká."}, status_code=404)
    return _planner.view()


@app.delete("/api/plan/history")
def plan_history_clear():
    _planner.clear_history()
    return _planner.view()


@app.post("/api/plan/{tid}/move")
def plan_move(tid: str, req: PlanMoveRequest):
    if not _planner.move(tid, max(-50, min(50, req.delta))):
        return JSONResponse({"error": "Taková úloha tu není."}, status_code=404)
    return _planner.view()


@app.delete("/api/plan/{tid}")
def plan_remove(tid: str):
    if not _planner.remove(tid):
        return JSONResponse({"error": "Taková úloha nečeká (běžící zastavíš v Úlohách a procesech)."}, status_code=404)
    return _planner.view()


# ---- Poznámky (notes.py; Nastavení → Další): bugs, ideas and notes about Pick, kept in
# notes.json and copied for Claude in one go.
import notes as _notes


class NoteRequest(BaseModel):
    text: Optional[str] = None
    kind: Optional[str] = None         # chyba | napad | poznamka
    done: Optional[bool] = None


@app.get("/api/notes")
def notes_list():
    return {"notes": _notes.listing(), "kinds": _notes.KINDS}


@app.get("/api/notes/markdown")
def notes_markdown(all: bool = False):
    return {"text": _notes.markdown(all)}


@app.post("/api/notes")
def notes_add(req: NoteRequest):
    try:
        n = _notes.add(req.text or "", req.kind or "poznamka")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"note": n, "notes": _notes.listing()}


@app.patch("/api/notes/{nid}")
def notes_edit(nid: str, req: NoteRequest):
    try:
        n = _notes.edit(nid, text=req.text, kind=req.kind, done=req.done)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if n is None:
        return JSONResponse({"error": "Taková poznámka tu není."}, status_code=404)
    return {"note": n, "notes": _notes.listing()}


@app.delete("/api/notes/{nid}")
def notes_remove(nid: str):
    if not _notes.remove(nid):
        return JSONResponse({"error": "Taková poznámka tu není."}, status_code=404)
    return {"notes": _notes.listing()}


if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _studio.resume()                       # variants that were waiting when the server last stopped

# A model left running by a Pick that has since been restarted is nobody's: it is let go.
if not os.environ.get("SPOTDL_NO_WATCHERS"):
    _assistant.llm_stop()


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
    return {"unavailable": _assistant.available(), "model": _assistant.model_name(), "brain": _assistant.brain()}


@app.get("/assistant/book", response_class=HTMLResponse)
def assistant_book_page():
    return _assistant.book_page()


class AssistTeach(BaseModel):
    text: str
    calls: List[dict] = []


@app.post("/api/assistant/teach")
def assistant_teach(req: AssistTeach):
    """A sentence taught by hand on the page of what can be said."""
    got = _assistant.teach(req.text, req.calls)
    return JSONResponse(got, status_code=400) if got.get("error") else got


@app.delete("/api/assistant/learned")
def assistant_forget(text: str):
    return {"ok": _assistant.forget(text)}


@app.post("/api/assistant/wake")
def assistant_wake():
    """A sentence is about to be said: the model in this computer starts loading now."""
    if _assistant.brain() == "local":
        _assistant.llm_wake(wait=False)
    return {"ok": True}


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
    # The microphone made louder, when the sound card's own gain is not enough; a limiter keeps it from clipping.
    filters = [f"pan=mono|c0=c{int(cfg['channel']) - 1}"] if cfg["channel"] in ("1", "2") and device else []
    if cfg.get("gain"):
        filters += [f"volume={cfg['gain']:.1f}dB", "alimiter=limit=0.95"]
    if filters:
        cmd += ["-af", ",".join(filters)]
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
    gain: Optional[float] = None      # dB the microphone is made louder by (0–30)


@app.get("/api/voice/widget")
def voice_widget():
    return {**_voice.widget_config(), "running": bool(_WIDGET["running"]), "error": _WIDGET["error"], "heard": _WIDGET["heard"],
            "level": round(_WIDGET["level"], 4) if _WIDGET["running"] else 0.0,
            # Not one sample above zero in five seconds of listening: macOS is not letting the microphone through.
            "silent": bool(_WIDGET["running"] and not _WIDGET["sound_at"] and time.time() - _WIDGET["since"] > 5)}


@app.post("/api/voice/widget")
def voice_widget_set(req: VoiceWidget):
    before = _voice.widget_config()
    gain = None if req.gain is None else max(0.0, min(30.0, float(req.gain)))
    cfg = _voice.widget_save(on=req.on, device=req.device, channel=req.channel, gain=gain)
    moved = (cfg["device"], cfg["channel"], cfg["gain"]) != (before["device"], before["channel"], before["gain"])
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



# ---- the looper's projects (static/looper.js). A project is a folder under Looper/ in the music
# root: project.json (tempo, parts, arrangement, the tracks and their layers) and a WAV for every
# recorded layer. Notes and step grids are kept in the JSON and rendered again when it opens.
LOOPER_DIR = "Looper"
_LOOPER_FILE = _re_mod.compile(r"[A-Za-z0-9_-]{1,60}\.wav")


def _looper_dir(name: str, create: bool = False) -> Optional[Path]:
    """The project's folder, its name cleaned to what a folder may be called."""
    clean = _re_mod.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "", name or "").strip().strip(".")[:80].strip()
    if not clean:
        return None
    base = _resolve_under_music_root(LOOPER_DIR)
    if base is None:
        return None
    d = (base / clean)
    try:
        if d.resolve().parent != base.resolve():
            return None
    except OSError:
        return None
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


@app.get("/api/looper")
def looper_list():
    base = _resolve_under_music_root(LOOPER_DIR)
    out = []
    if base is not None and base.is_dir():
        for d in base.iterdir():
            f = d / "project.json"
            if not f.is_file():
                continue
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"name": d.name, "song": p.get("song"), "bpm": p.get("bpm"),
                        "tracks": sum(1 for t in p.get("tracks") or [] if t.get("layers")),
                        "saved": f.stat().st_mtime})
    out.sort(key=lambda x: -x["saved"])
    return {"projects": out}


@app.get("/api/looper/{name}")
def looper_get(name: str):
    d = _looper_dir(name)
    f = d / "project.json" if d else None
    if f is None or not f.is_file():
        return JSONResponse({"error": "Projekt nenalezen."}, status_code=404)
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return JSONResponse({"error": "Projekt se nepodařilo přečíst."}, status_code=500)


@app.put("/api/looper/{name}")
def looper_save(name: str, project: dict):
    """The project's description; WAVs no layer names any more are let go."""
    d = _looper_dir(name, create=True)
    if d is None:
        return JSONResponse({"error": "Neplatný název projektu."}, status_code=400)
    if not isinstance(project, dict) or not isinstance(project.get("tracks"), list):
        return JSONResponse({"error": "Projekt musí mít stopy."}, status_code=400)
    used = {l.get("file") for t in project["tracks"] if isinstance(t, dict)
            for l in (t.get("layers") or []) if isinstance(l, dict)}
    missing = [f for f in used if f and not (d / f).is_file()]
    if missing:
        return JSONResponse({"error": f"Chybí nahrávky: {', '.join(sorted(missing))}"}, status_code=400)
    tmp = d / ".project.json.saving"
    tmp.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "project.json")
    for f in d.glob("*.wav"):
        if f.name not in used:
            f.unlink(missing_ok=True)
    return {"name": d.name, "files": len(used - {None})}


@app.put("/api/looper/{name}/files/{fname}")
async def looper_file_put(name: str, fname: str, request: Request):
    if not _LOOPER_FILE.fullmatch(fname):
        return JSONResponse({"error": "Neplatný název souboru."}, status_code=400)
    d = _looper_dir(name, create=True)
    if d is None:
        return JSONResponse({"error": "Neplatný název projektu."}, status_code=400)
    raw = await request.body()
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return JSONResponse({"error": "Nahrávka není WAV."}, status_code=400)
    if len(raw) > 400 * 1024 * 1024:
        return JSONResponse({"error": "Nahrávka je příliš velká."}, status_code=413)
    tmp = d / f".{fname}.saving"
    tmp.write_bytes(raw)
    os.replace(tmp, d / fname)
    return {"file": fname, "bytes": len(raw)}


@app.get("/api/looper/{name}/files/{fname}")
def looper_file_get(name: str, fname: str):
    d = _looper_dir(name)
    if d is None or not _LOOPER_FILE.fullmatch(fname) or not (d / fname).is_file():
        return JSONResponse({"error": "Nahrávka nenalezena."}, status_code=404)
    return FileResponse(d / fname, media_type="audio/wav")


@app.delete("/api/looper/{name}")
def looper_delete(name: str):
    d = _looper_dir(name)
    if d is None or not d.is_dir():
        return JSONResponse({"error": "Projekt nenalezen."}, status_code=404)
    shutil.rmtree(d, ignore_errors=True)
    return {"deleted": d.name}
