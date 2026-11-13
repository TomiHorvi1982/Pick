"""Pick knihovna — the friend's Pick Jam page between jams, on the host's PC (Docker), not on his Mac.

It reads a copy of the Pick card (the Stems: tracks, prints, sections, chords) and of Pick's own data (NAM
profiles and cabinets, kept lyrics, covers, Steems playlists) — read-only, mounted so. It writes only to its own
small folder (/state): the friends' names and passwords (hashed), "Na příště", a cache of previews.

The friend logs in, looks through the host's songs, listens to them (a preview: the tracks mixed), reads the
text and the chords, tries the prints and amps on his guitar, and keeps "Na příště". The jam itself — the
guitars, the cameras, playing together — is on the host's Mac at the moment the host starts it; this page says
when it is on and links there. The Mac talks to this page (never the other way): it fetches and sends
"Na příště" and says when a jam starts (/sync/…, with a token).

Run: uvicorn library:app --host 0.0.0.0 --port 8080
Friends:  python library.py adduser NAME      (asks for the password)
          python library.py deluser NAME
          python library.py users
"""
import asyncio
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

HERE = Path(__file__).resolve().parent
STATIC = Path(os.environ.get("PICK_STATIC") or HERE / "static")
DATA = Path(os.environ.get("PICK_DATA") or "/data/spotdl")              # the card's copy: it has Stems/
AMPS = Path(os.environ.get("PICK_AMPS") or "/data/amps")                # NAM profiles and cabinets
HELPER = Path(os.environ.get("PICK_HELPER") or "/data/helper")          # Pick's own data: lyrics/, chip-thumbs.json, steems-playlists.json
STATE = Path(os.environ.get("PICK_STATE") or "/state")                  # the only place written to
HOST = (os.environ.get("PICK_HOST_NAME") or "Tomi").strip()[:30]
JAM_URL = os.environ.get("PICK_JAM_URL") or "https://neverlast.cz"
TOKEN = os.environ.get("PICK_SYNC_TOKEN") or ""
STEM_NAMES = ["drums", "bass", "guitar", "guitar_rhythm", "guitar_solo", "vocals", "piano", "other"]
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".aiff", ".aif"}
GUEST_FILES = {"jam-engine.js", "jam-worklet.js", "jam-guest.js", "jam-chat.js", "jam-mix.js", "jam-amp.js"}
# Pick DJ: two decks over the separated songs (static/dj*.js), put into the page by page()
DJ_FILES = ("dj-core.js", "dj.js", "dj-library.js")
LIB_DIRS = {"stretch": STATIC / "stretch", "nam": STATIC / "nam", "alphatab": STATIC / "alphatab", "worklets": STATIC / "worklets"}
LIB_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm", ".json": "application/json",
             ".woff": "font/woff", ".woff2": "font/woff2", ".otf": "font/otf", ".svg": "image/svg+xml"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- state (the only writes)
def _read(name: str, default):
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, data) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE / name)


# ---------------------------------------------------------------- who: names and passwords (as on the Mac)
SESS: Dict[str, dict] = {}
FAILS: Dict[str, list] = {}


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def own_users() -> dict:
    u = _read("users.json", {})
    return u if isinstance(u, dict) else {}


def pick_users() -> dict:
    """The friends' accounts made in Pick on the Mac (Nastavení → Jam), sent here by the Mac: the same name and password both places."""
    u = _read("pick-users.json", {})
    return u if isinstance(u, dict) else {}


def users() -> dict:
    return {**pick_users(), **own_users()}


def user_set(name: str, pw: str) -> None:
    name = " ".join(name.split())[:30]
    if not name or len(pw) < 6:
        raise ValueError("Jméno a heslo aspoň o 6 znacích.")
    salt = secrets.token_bytes(16)
    u = own_users()
    u[name] = {"salt": salt.hex(), "hash": _hash(pw, salt), "at": time.time()}
    _write("users.json", u)


def _ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _tk(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _sess_save() -> None:
    now = time.time()
    _write("sessions.json", {k: v for k, v in SESS.items() if v["exp"] > now})


def _who(request: Request) -> Optional[str]:
    if not SESS:
        SESS.update(_read("sessions.json", {}) or {})
    v = SESS.get(_tk(request.cookies.get("pl") or ""))
    return v["user"] if v and v["exp"] > time.time() and v["user"] in users() else None


def _need(request: Request):
    who = _who(request)
    return (who, None) if who else (None, JSONResponse({"error": "Nejsi přihlášený."}, status_code=403))


@app.post("/login")
async def login(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    ip, now = _ip(request), time.time()
    tries = [t for t in FAILS.get(ip, []) if now - t < 600]
    FAILS[ip] = tries
    if len(tries) >= 8:
        return JSONResponse({"error": "Moc pokusů — zkus to za 10 minut."}, status_code=429)
    name = " ".join(str(body.get("name") or "").split())
    u = users()
    if name not in u:                                       # "maxdrako" is MaxDrako: the letters' size does not matter
        name = next((k for k in u if k.casefold() == name.casefold()), name)
    # the account made here or the one from Pick under the same name: either password lets him in
    recs = [r for r in (own_users().get(name), pick_users().get(name)) if isinstance(r, dict)]
    ok = False
    for rec in recs:
        try:
            ok = ok or secrets.compare_digest(_hash(str(body.get("password") or ""), bytes.fromhex(rec["salt"])), rec["hash"])
        except (ValueError, KeyError, TypeError):
            pass
    if not recs:
        _hash(str(body.get("password") or ""), b"0" * 16)
    if not ok:
        tries.append(now)
        return JSONResponse({"error": "Špatné jméno nebo heslo."}, status_code=401)
    tok = secrets.token_urlsafe(24)
    keep = body.get("remember") is not False                # "Zapamatovat si mě": a year; otherwise until the browser closes
    if not SESS:
        SESS.update(_read("sessions.json", {}) or {})
    SESS[_tk(tok)] = {"user": name, "exp": now + (365 if keep else 1) * 24 * 3600}
    _sess_save()
    r = JSONResponse({"ok": True, "user": name})
    r.set_cookie("pl", tok, max_age=365 * 24 * 3600 if keep else None, httponly=True, samesite="strict",
                 secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    return r


@app.post("/logout")
def logout(request: Request):
    SESS.pop(_tk(request.cookies.get("pl") or ""), None)
    _sess_save()
    r = JSONResponse({"ok": True})
    r.delete_cookie("pl")
    return r


# ---------------------------------------------------------------- the page and its scripts
@app.get("/", response_class=HTMLResponse)
def page():
    html = (STATIC / "jam.html").read_text(encoding="utf-8")
    for name in GUEST_FILES:
        f = STATIC / name
        if f.is_file():
            html = html.replace(f"/static/{name}\"", f"/static/{name}?v={int(f.stat().st_mtime)}\"")
    dj = [STATIC / n for n in DJ_FILES if (STATIC / n).is_file()]
    if len(dj) == len(DJ_FILES):                               # all three, or none: half a DJ would only throw
        tags = "".join(f'<script src="/static/{f.name}?v={int(f.stat().st_mtime)}"></script>' for f in dj)
        html = html.replace("</body>", tags + "</body>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def icon():
    f = STATIC / "pick-favicon.png"
    return Response(f.read_bytes(), media_type="image/png") if f.is_file() else Response(status_code=204)


ICONS = {"jam-icon-192.png": "image/png", "jam-icon-512.png": "image/png", "jam-icon.ico": "image/x-icon", "jam-icon.icns": "image/icns"}


@app.get("/manifest.webmanifest")
def manifest():
    """Installable: Chrome's "Nainstalovat" puts Pick Jam on the desktop, in its own window."""
    return JSONResponse({"name": "Pick Jam", "short_name": "Pick Jam", "start_url": "/", "scope": "/", "display": "standalone",
                         "background_color": "#0d0d14", "theme_color": "#0d0d14", "description": f"Písně, zvuky a jam s {HOST}",
                         "icons": [{"src": "/static/jam-icon-192.png", "sizes": "192x192", "type": "image/png"},
                                   {"src": "/static/jam-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]},
                        media_type="application/manifest+json")


@app.get("/pick-jam.cmd")
def launcher(request: Request):
    """For Windows: put a "Pick Jam" icon on the desktop that opens this page in Chrome as an app, and open it now.
    Nothing secret in it — only this address; the name and password are asked in the window."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "jam.neverlast.cz"
    url = f"https://{host}/" if not host.startswith(("127.", "localhost")) else f"http://{host}/"
    t = (STATIC / "jam-launcher.cmd").read_text(encoding="utf-8").replace("{{URL}}", url).replace("{{HOST}}", HOST)
    return Response(t.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="Pick Jam.cmd"', "Cache-Control": "no-store"})


@app.get("/pick-jam-mac.zip")
def launcher_mac(request: Request):
    """For a Mac: "Pick Jam.command" in a zip (a zip keeps it runnable; a plain download loses that) — it makes a
    "Pick Jam" app with the icon on the Desktop that opens this page in Chrome as an app. Only this address in it."""
    import io
    import zipfile
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "jam.neverlast.cz"
    url = f"https://{host}/" if not host.startswith(("127.", "localhost")) else f"http://{host}/"
    t = (STATIC / "jam-launcher.command").read_text(encoding="utf-8").replace("{{URL}}", url).replace("{{HOST}}", HOST)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("Pick Jam.command", date_time=time.localtime()[:6])
        info.external_attr = (0o100755 & 0xFFFF) << 16
        info.create_system = 3                                   # unix: the mode above counts
        z.writestr(info, t.replace("\r\n", "\n").encode("utf-8"))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="Pick Jam pro Mac.zip"', "Cache-Control": "no-store"})


@app.get("/static/{name}")
def guest_static(name: str):
    if name in ICONS and (STATIC / name).is_file():
        return FileResponse(STATIC / name, media_type=ICONS[name], headers={"Cache-Control": "public, max-age=86400"})
    if (name not in GUEST_FILES and name not in DJ_FILES) or not (STATIC / name).is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return Response((STATIC / name).read_text(encoding="utf-8"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/static/{lib}/{name:path}")
def guest_lib(lib: str, name: str):
    root = LIB_DIRS.get(lib)
    f = (root / name).resolve() if root else None
    if f is None or not f.is_relative_to(root.resolve()) or not f.is_file() or f.suffix not in LIB_TYPES:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type=LIB_TYPES[f.suffix])


SW = (STATIC / "jam-sw.js")


@app.get("/sw.js")
def sw():
    return Response(SW.read_text(encoding="utf-8") if SW.is_file() else "", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------- the songs (read from the copy)
def _stems_root() -> Path:
    return DATA / "Stems"


def _song_dir(name: str) -> Optional[Path]:
    if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        return None
    d = _stems_root() / name
    return d if d.is_dir() else None


def _stem_file(d: Path, stem: str) -> Optional[Path]:
    for f in sorted(d.glob(stem + ".*")):
        if f.suffix.lower() in AUDIO_EXTS:
            return f
    return None


def _jload(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _duration(f: Path) -> Optional[float]:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)], capture_output=True, text=True, timeout=20)
        return round(float(r.stdout.strip()), 1)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


# The copy sits on a Windows disk seen through Docker Desktop, where every file call costs ~1 ms: a song is read
# with one listing of its folder, and only again when that folder changed. The list is kept in /state between starts.
_SONGS = {"at": 0.0, "rows": None, "dirs": None, "busy": False, "filling": False}
_SONGS_LOCK = threading.Lock()
_DUR: dict = {}


def _song_row(d: Path) -> Optional[dict]:
    try:
        names = sorted(e.name for e in os.scandir(d))
    except OSError:
        return None
    stems, first = [], None
    for st in STEM_NAMES:
        f = next((n for n in names if n.startswith(st + ".") and Path(n).suffix.lower() in AUDIO_EXTS), None)
        if f:
            stems.append(st)
            first = first or f
    if not stems:
        return None
    m = _jload(d / "meta.json", {}) if "meta.json" in names else {}
    m = m if isinstance(m, dict) else {}
    t = _jload(d / "tuning.json", None) if "tuning.json" in names else None
    tuning = (t.get("tuning") or t.get("midi")) if isinstance(t, dict) else t if isinstance(t, list) else None
    try:
        key = f"{d.name}:{(d / first).stat().st_mtime}"
    except OSError:
        return None
    yt = re.search(r"\[([A-Za-z0-9_-]{11})\]\s*$", d.name)
    return {"name": d.name, "stems": stems, "artist": m.get("artist") or "", "title": m.get("title") or d.name, "album": m.get("album") or "",
            "tuning": tuning if isinstance(tuning, list) else None, "yt": f"https://youtu.be/{yt.group(1)}" if yt else "", "_k": key, "_f": first}


def _scan() -> None:
    """Walk the copy: a folder not changed since the last walk keeps its row."""
    old = _SONGS["dirs"] if _SONGS["dirs"] is not None else (_read("songs-cache.json", {}) or {})
    dirs, root = {}, _stems_root()
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name) if root.is_dir() else []
    except OSError:
        entries = []
    for e in entries:
        if e.name.startswith(".") or not e.is_dir():
            continue
        try:
            mt = e.stat().st_mtime
        except OSError:
            continue
        prev = old.get(e.name)
        if prev and prev.get("m") == mt:
            dirs[e.name] = prev
            continue
        row = _song_row(Path(e.path))
        dirs[e.name] = {"m": mt, "row": row}
    _write("songs-cache.json", dirs)
    rows = [v["row"] for v in dirs.values() if v.get("row")]
    _SONGS.update(dirs=dirs, rows=rows, at=time.time())
    todo = [(r["_k"], root / r["name"] / r["_f"]) for r in rows if r["_k"] not in _DUR]
    if todo and not _SONGS["filling"]:
        _SONGS["filling"] = True
        threading.Thread(target=_fill_durations, args=(todo,), daemon=True).start()


def _fill_durations(todo: list) -> None:
    """Lengths of songs not measured yet, in the background: the list goes out at once."""
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as ex:
            for n, (key, got) in enumerate(zip([k for k, _ in todo], ex.map(lambda kf: _duration(kf[1]), todo)), 1):
                _DUR[key] = got
                if n % 100 == 0:
                    _write("durations.json", _DUR)
        _write("durations.json", _DUR)
    finally:
        _SONGS["filling"] = False


def _rescan_bg() -> None:
    try:
        with _SONGS_LOCK:
            _scan()
    finally:
        _SONGS["busy"] = False


def songs() -> list:
    if not _DUR:
        _DUR.update(_read("durations.json", {}) or {})
    if _SONGS["rows"] is None:
        cached = _read("songs-cache.json", {}) or {}
        if cached:                                                              # last start's list at once, checked behind
            _SONGS.update(dirs=cached, rows=[v["row"] for v in cached.values() if v.get("row")], at=0.0)
        else:
            with _SONGS_LOCK:
                if _SONGS["rows"] is None:
                    _scan()
    if time.time() - _SONGS["at"] > 300 and not _SONGS["busy"]:
        _SONGS["busy"] = True
        threading.Thread(target=_rescan_bg, daemon=True).start()
    return [{**{k: v for k, v in r.items() if not k.startswith("_")}, "duration": _DUR.get(r["_k"])} for r in _SONGS["rows"]]


@app.get("/g/me")
def me(request: Request):
    st = _read("status.json", {})
    on = bool(st.get("on")) and time.time() - (st.get("at") or 0) < 180          # the Mac says so, at least every minute
    who = _who(request)
    return {"user": who, "login": True, "host": HOST, "on": False, "library": True, "jam": on, "jam_url": st.get("url") or JAM_URL,
            "engine": _engines() if who else []}


# ---------------------------------------------------------------- Pick Jam Engine: the Mac sends its builds, a friend downloads one
ENGINE_DIR = STATE / "engine"
ENGINE_FILES = {"pick-jam-engine.exe": "application/vnd.microsoft.portable-executable", "pick-jam-engine-mac": "application/octet-stream"}


def _engines() -> list:
    return [n for n in ENGINE_FILES if (ENGINE_DIR / n).is_file()]


@app.get("/engine/{name}")
def g_engine(request: Request, name: str):
    # open to all: it is only the program (no names, keys or songs), and the desktop launcher fetches it before anyone logs in
    if name not in ENGINE_FILES or not (ENGINE_DIR / name).is_file():
        return JSONResponse({"error": "Engine tu není."}, status_code=404)
    return FileResponse(ENGINE_DIR / name, media_type=ENGINE_FILES[name], filename=name, headers={"Cache-Control": "no-cache"})


@app.get("/g/songs")
async def g_songs(request: Request):
    who, bad = _need(request)
    if bad:
        return bad
    return {"songs": await asyncio.to_thread(songs)}


@app.get("/g/song")
def g_song(request: Request, name: str):
    who, bad = _need(request)
    if bad:
        return bad
    d = _song_dir(name)
    if d is None:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    ses = _jload(d / "session.json", {}) or {}
    ses = ses.get("session") if isinstance(ses.get("session"), dict) else ses
    ana = _jload(d / "analysis.json", {}) or {}
    ch = _jload(d / "chords.json", {}) or {}
    secs = ana.get("sections") or (_jload(d / "sections.json", {}) or {}).get("sections") or []
    return {"name": name, "channels": [{k: c.get(k) for k in ("stem", "muted", "solo", "faderValue", "panValue", "pitchRate")} for c in (ses.get("channels") or []) if isinstance(c, dict)],
            "sections": secs, "chords": ch.get("chords") if isinstance(ch, dict) else ch if isinstance(ch, list) else []}


def _lyrics_key(title: str, artist: str, duration) -> str:
    key = f"{(title or '').strip().lower()}|{(artist or '').strip().lower()}|{int(round(duration)) if duration else 0}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json"


@app.get("/g/lyrics")
def g_lyrics(request: Request, name: str):
    """The text Pick kept of the song (it is not looked up here: what Pick found, the friend sees)."""
    who, bad = _need(request)
    if bad:
        return bad
    row = next((x for x in songs() if x["name"] == name), None)
    if row is None:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    artist = (row["artist"] or "").split(",")[0].strip()
    d = row.get("duration") or 0
    for dur in ([d, d - 1, d + 1, 0] if d else [0]):
        got = _jload(HELPER / "lyrics" / _lyrics_key(row["title"], artist, dur))
        if isinstance(got, dict) and (got.get("lines") or got.get("plain")):
            return {"synced": bool(got.get("synced")), "lines": [{"t": (l.get("timeInMs") or 0) / 1000, "text": l.get("text") or ""} for l in (got.get("lines") or []) if isinstance(l, dict)]}
    return {"synced": False, "lines": []}


@app.get("/g/art")
def g_art(request: Request, name: str):
    """The cover Pick found for the song's album (kept in its chip-thumbs.json): the browser goes there."""
    who, bad = _need(request)
    if bad:
        return bad
    row = next((x for x in songs() if x["name"] == name), None)
    if row is None:
        return Response(status_code=404)
    cache = _jload(HELPER / "chip-thumbs.json", {}) or {}
    a, al, t = row["artist"].lower(), row["album"].lower(), row["title"].lower()
    for k in (f"stemart:{a}|{al}" if al else "", f"stemart:{a}|song:{t}"):
        v = cache.get(k) if k else None
        url = v.get("url") if isinstance(v, dict) else v if isinstance(v, str) else None
        if url and url.startswith("https://"):
            return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=86400"})
    return Response(status_code=404)


def _range(path: Path, rng: Optional[str]) -> Response:
    size = path.stat().st_size
    m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
    if not m:
        return FileResponse(path, headers={"Accept-Ranges": "bytes"})
    a = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2) or 0))
    b = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
    b = min(b, size - 1)
    with open(path, "rb") as f:
        f.seek(a)
        data = f.read(b - a + 1)
    return Response(data, status_code=206, media_type="audio/webm",
                    headers={"Content-Range": f"bytes {a}-{b}/{size}", "Accept-Ranges": "bytes", "Content-Length": str(len(data))})


CACHE = STATE / "cache"          # the songs to listen to (all tracks mixed)
STEMS_CACHE = STATE / "stems"    # each track on its own, for playing along
KEEP_FREE = 15 * 1024 ** 3       # making ahead stops while the disk has less free than this
LOW_FREE = 5 * 1024 ** 3         # below this even a file made on asking makes room by dropping the oldest
MAX_CACHE = int(float(os.environ.get("PICK_CACHE_MAX_GB") or 65) * 1024 ** 3)   # the most the made files may take (Docker's disk may not show the real free room)
PREP = {"on": False, "done": 0, "todo": 0, "made": 0, "failed": 0, "at": 0.0, "note": ""}


def _free(p: Path) -> int:
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 1 << 62


def _cache_size() -> int:
    n = 0
    for folder in (STEMS_CACHE, CACHE):
        try:
            n += sum(e.stat().st_size for e in os.scandir(folder) if e.is_file())
        except OSError:
            pass
    return n


def _room(folder: Path) -> None:
    if _free(folder) < LOW_FREE:
        for old in sorted(folder.glob("*.webm"), key=lambda q: q.stat().st_mtime)[:20]:
            old.unlink(missing_ok=True)


def _preview_files(d: Optional[Path]) -> list:
    return [f for f in (_stem_file(d, s) for s in STEM_NAMES) if f] if d else []


def _preview_out(files: list) -> Path:
    return CACHE / (hashlib.sha1("|".join(f"{f}:{f.stat().st_mtime}" for f in files).encode()).hexdigest()[:16] + ".webm")


def _stem_out(f: Path) -> Path:
    return STEMS_CACHE / (hashlib.sha1(f"{f}:{f.stat().st_mtime}".encode()).hexdigest()[:16] + ".webm")


def _ffmpeg(args: list, out: Path, low: bool = False) -> bool:
    """One file made, whole or not at all (a part file renamed when done)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(f".{secrets.token_hex(3)}.part")
    cmd = (["nice", "-n", "15"] if low and shutil.which("nice") else []) + ["ffmpeg", "-v", "error", "-y"] + args \
        + ["-ac", "2", "-ar", "48000", "-c:a", "libopus"] + (["-threads", "1"] if low else []) + ["-f", "webm", str(part)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        part.unlink(missing_ok=True)
        return False
    if r.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        return False
    part.replace(out)
    return True


def _make_preview(files: list, low: bool = False) -> Optional[Path]:
    out = _preview_out(files)
    if out.is_file():
        return out
    _room(CACHE) if CACHE.is_dir() else None
    args = sum([["-i", str(f)] for f in files], [])
    if len(files) > 1:
        args += ["-filter_complex", f"amix=inputs={len(files)}:normalize=0:duration=longest"]
    return out if _ffmpeg(args + ["-b:a", "96k"], out, low) else None


def _make_stem(f: Path, low: bool = False) -> Optional[Path]:
    out = _stem_out(f)
    if out.is_file():
        return out
    _room(STEMS_CACHE) if STEMS_CACHE.is_dir() else None
    return out if _ffmpeg(["-i", str(f), "-vn", "-b:a", "128k"], out, low) else None


@app.get("/g/preview")
async def g_preview(request: Request, name: str):
    """The song to listen to: its tracks mixed into one small file, made once (most made ahead, see _prepare)."""
    who, bad = _need(request)
    if bad:
        return bad
    files = _preview_files(_song_dir(name))
    if not files:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    out = await asyncio.to_thread(_make_preview, files)
    if out is None:
        return JSONResponse({"error": "Náhled se nepodařilo připravit."}, status_code=500)
    return _range(out, request.headers.get("range"))


@app.get("/g/stem")
async def g_stem(request: Request, name: str, stem: str):
    """One track, for playing along alone: Opus in WebM, made once and kept."""
    who, bad = _need(request)
    if bad:
        return bad
    d = _song_dir(name)
    f = _stem_file(d, stem) if d is not None and stem in STEM_NAMES else None
    if f is None:
        return JSONResponse({"error": "Stopa nenalezena."}, status_code=404)
    out = await asyncio.to_thread(_make_stem, f)
    if out is None:
        return JSONResponse({"error": "Stopu se nepodařilo připravit."}, status_code=500)
    return FileResponse(out, media_type="audio/webm", headers={"Cache-Control": "private, max-age=86400"})


def _prepare_once() -> None:
    """Every song's tracks and its preview made ahead, so the friend never waits for them: Na příště first,
    one file at a time at low priority. Files of songs gone or changed on the copy are dropped after a full pass."""
    rows = songs()
    first = set(fav()["songs"])
    rows = sorted(rows, key=lambda r: (r["name"] not in first, r["name"].lower()))
    PREP.update(todo=len(rows), done=0, note="")
    keep, size, n = set(), _cache_size(), 0
    for r in rows:
        n += 1
        if n % 20 == 0:
            size = _cache_size()
        if _free(STATE) < KEEP_FREE or size > MAX_CACHE:
            PREP["note"] = "Na disku je málo místa — zbytek se připraví, až si o něj kamarád řekne."
            return
        d = _song_dir(r["name"])
        if d is None:
            continue
        for st in STEM_NAMES:
            f = _stem_file(d, st)
            if f is None:
                continue
            out = _stem_out(f)
            keep.add(out.name)
            if not out.is_file():
                PREP["made" if _make_stem(f, low=True) else "failed"] += 1
        files = _preview_files(d)
        if files:
            out = _preview_out(files)
            keep.add(out.name)
            if not out.is_file():
                PREP["made" if _make_preview(files, low=True) else "failed"] += 1
        PREP["done"] += 1
    for folder in (STEMS_CACHE, CACHE):                                          # whole pass: what no song needs any more
        for old in folder.glob("*.webm") if folder.is_dir() else []:
            if old.name not in keep:
                old.unlink(missing_ok=True)
        for old in folder.glob("*.part") if folder.is_dir() else []:
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink(missing_ok=True)


def _prepare_loop() -> None:
    time.sleep(20)                                                               # the list first
    while True:
        PREP["on"] = True
        try:
            _prepare_once()
        except Exception as e:                                                   # the next round tries again
            PREP["note"] = f"Chyba: {str(e)[:120]}"
        PREP.update(on=False, at=time.time())
        time.sleep(30 * 60)                                                      # new songs on the copy: half an hour later


@app.on_event("startup")
def _start_prepare() -> None:
    if os.environ.get("PICK_PREPARE", "1") != "0":
        threading.Thread(target=_prepare_loop, daemon=True, name="prepare").start()


# ---------------------------------------------------------------- the sounds: prints, NAM, cabinets
def _prints_of(d: Path) -> list:
    out = []
    for f in sorted((d / "tone" / "prints").glob("*/print.json")):
        p = _jload(f, None)
        if isinstance(p, dict):
            p.setdefault("id", f.parent.name)
            out.append(p)
    return out


@app.get("/g/prints")
def g_prints(request: Request):
    who, bad = _need(request)
    if bad:
        return bad
    rows = []
    for s in songs():
        d = _song_dir(s["name"])
        for p in _prints_of(d) if d else []:
            if p.get("review") == "rejected":
                continue
            core = p.get("core") or {}
            rows.append({"id": p["id"], "name": p.get("name") or f"{s['artist']} – {s['title']}", "category": p.get("category"), "match": p.get("match"),
                         "core": core.get("name") if isinstance(core, dict) else core, "song": s["name"], "artist": s["artist"], "title": s["title"],
                         "created": p.get("created"), "friend": p.get("friend")})
    return {"prints": sorted(rows, key=lambda x: -(x.get("created") or 0))}


def _print(name: str, pid: str) -> Optional[dict]:
    d = _song_dir(name)
    if d is None or not re.fullmatch(r"[\w.-]{1,60}", pid or ""):
        return None
    p = _jload(d / "tone" / "prints" / pid / "print.json", None)
    if isinstance(p, dict):
        p.setdefault("id", pid)
        return p
    return None


@app.get("/g/print")
def g_print(request: Request, name: str, print: str = ""):
    who, bad = _need(request)
    if bad:
        return bad
    d = _song_dir(name)
    p = _print(name, print) if print else (_prints_of(d)[0] if d and _prints_of(d) else None)
    if p is None:
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    return {"print": {k: p.get(k) for k in ("id", "name", "pre", "core", "room", "drive_db", "gain_db", "out_gain", "width")}}


@app.get("/g/print-ir")
def g_print_ir(request: Request, name: str, print: str = ""):
    who, bad = _need(request)
    if bad:
        return bad
    p = _print(name, print)
    f = _song_dir(name) / "tone" / "prints" / p["id"] / "print-ir.wav" if p else None
    return FileResponse(f, media_type="audio/wav") if f and f.is_file() else Response(status_code=404)


# The amps and cabinets: Pick's own collection (PICK_AMPS) and the NAM folder on the card's copy (spotdl/NAM, "lib:…" as
# Pick names them). Walked without a stat per file (slow through Docker Desktop's mount), kept in /state between starts,
# looked at again behind the answer every 10 minutes.
_AMPS: Dict = {"at": 0.0, "rows": None, "busy": False}
_AMPS_LOCK = threading.Lock()
AMP_EXTS = (".nam", ".wav")


def _walk(root: Path, prefix: str, only_nam: bool) -> list:
    rows = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for n in files:
            ext = os.path.splitext(n)[1].lower()
            if ext in AMP_EXTS and not n.startswith(".") and (ext == ".nam" or not only_nam):
                rel = Path(d, n).relative_to(root).as_posix()
                rows.append({"name": n[:-len(ext)], "file": prefix + rel, "kind": "model" if ext == ".nam" else "ir"})
    return rows


def _amps_scan() -> None:
    rows = (_walk(AMPS, "", False) if AMPS.is_dir() else []) + (_walk(DATA / "NAM", "lib:NAM/", True) if (DATA / "NAM").is_dir() else [])
    rows.sort(key=lambda x: x["name"].lower())
    _write("amps.json", rows)
    _AMPS.update(at=time.time(), rows=rows)
    _CAT["at"] = 0.0                                                               # the catalogue follows


def _amps_bg() -> None:
    try:
        with _AMPS_LOCK:
            _amps_scan()
    finally:
        _AMPS["busy"] = False


def amps() -> list:
    if _AMPS["rows"] is None:
        kept = _read("amps.json", None)
        if isinstance(kept, list) and kept:
            _AMPS.update(rows=kept, at=0.0)
        else:
            with _AMPS_LOCK:
                if _AMPS["rows"] is None:
                    _amps_scan()
    if time.time() - _AMPS["at"] > 600 and not _AMPS["busy"]:
        _AMPS["busy"] = True
        threading.Thread(target=_amps_bg, daemon=True).start()
    return _AMPS["rows"]


def _amp_path(file: str) -> Optional[Path]:
    """An amp's file id → the file, only inside the two amp folders."""
    if not file or ".." in file.replace("\\", "/").split("/"):
        return None
    root, rel = (DATA / "NAM", file[len("lib:NAM/"):]) if file.startswith("lib:NAM/") else (AMPS, file)
    f = (root / rel).resolve()
    return f if f.is_relative_to(root.resolve()) and f.suffix.lower() in AMP_EXTS and f.is_file() else None


@app.get("/g/amps")
def g_amps(request: Request, q: str = "", kind: str = "model"):
    who, bad = _need(request)
    if bad:
        return bad
    words = [w for w in q.lower().split() if w]
    got = [x for x in amps() if x["kind"] == kind and all(w in x["name"].lower() for w in words)]
    return {"amps": got[:150], "total": len(got)}


# ---- the same sorting as Pick's own picker (amp_catalog.py, copied in beside this file): make, model, gain, type
def _catalog_mod():
    for d in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else None):
        if d is not None and (d / "amp_catalog.py").is_file() and str(d) not in sys.path:
            sys.path.append(str(d))
    import amp_catalog                       # only its pure functions: Pick's own server may share this module in tests
    return amp_catalog


_CAT: Dict = {"at": 0.0, "models": [], "irs": []}
_CAT_LOCK = threading.Lock()


def _catalogue():
    ac = _catalog_mod()
    with _CAT_LOCK:
        if time.time() - _CAT["at"] < 600 and (_CAT["models"] or _CAT["irs"]):
            return ac, _CAT["models"], _CAT["irs"]
        old = _read("amp-meta.json", {}) or {}
        metas, models, irs = {}, [], []
        for it in amps():
            f, name = it["file"], it["name"]
            lib = f.startswith("lib:NAM/")
            parts = (f[len("lib:NAM/"):] if lib else f).split("/")
            folder = "/".join(parts[1:-1]) if len(parts) > 2 else ""
            pack = parts[0] if len(parts) > 1 and parts[0].lower() not in ("nam", "ir") else ("Knihovna" if lib else "Kolekce")
            if it["kind"] == "model":
                got = old.get(f)                              # a .nam does not change under its name: no stat for each
                if got:
                    meta = got["meta"]
                else:
                    fp = _amp_path(f)
                    meta = ac._meta(fp) if fp else {}
                sig = ""
                metas[f] = {"sig": sig, "meta": {k: meta.get(k) for k in ("gear_make", "gear_model", "gear_type", "tone_type", "modeled_by", "name")}}
                models.append({"file": f, "name": name, "pack": pack, **ac.classify_model(name, folder, metas[f]["meta"])})
            else:
                irs.append({"file": f, "name": name, "pack": pack, "folder": folder, **ac.classify_ir(name, pack if pack != "Kolekce" else "", folder)})
        _write("amp-meta.json", metas)
        _CAT.update(at=time.time(), models=models, irs=irs)
        return ac, models, irs


@app.get("/g/amp-catalog")
def g_amp_catalog(request: Request, kind: str = "model", q: str = "", sort: str = "key", offset: int = 0, limit: int = 150,
                  brand: str = "", family: str = "", gain: str = "", type: str = "", pack: str = "", cab: str = "", speaker: str = "", mic: str = "", size: str = ""):
    who, bad = _need(request)
    if bad:
        return bad
    ac, models, irs = _catalogue()
    kind = "ir" if kind == "ir" else "model"
    items = irs if kind == "ir" else models
    words = ac._norm(q).split()
    if words:
        items = [x for x in items if all(w in ac._norm(" ".join(str(x.get(k) or "") for k in ("name", "brand", "family", "pack", "cab", "speaker", "mic", "size", "type"))) for w in words)]
    want = {"brand": brand, "family": family, "gain": gain, "type": type, "pack": pack, "cab": cab, "speaker": speaker, "mic": mic, "size": size}
    keys = ac.FILTERS[kind]
    facets = {}
    for k in keys:
        cnt: Dict[str, int] = {}
        for x in items:
            if all(not want[o] or x.get(o) == want[o] for o in keys if o != k) and x.get(k):
                cnt[x[k]] = cnt.get(x[k], 0) + 1
        facets[k] = sorted(cnt.items(), key=lambda kv: (-kv[1], ac._norm(kv[0])))[:80]
    items = sorted([x for x in items if all(not want[k] or x.get(k) == want[k] for k in keys)], key=ac.SORTS[kind].get(sort, ac.SORTS[kind]["key"]))
    g = ac.GROUP[kind].get(sort, ac.GROUP[kind]["key"])
    offset, limit = max(0, offset), max(1, min(300, limit))
    row = ("file", "name", "gain", "type", "brand", "family", "pack", "cab", "speaker", "mic", "size")
    return {"kind": kind, "total": len(items), "all": len(irs if kind == "ir" else models), "gains": ac.GAIN_LABEL, "facets": facets,
            "items": [{**{k: x.get(k) for k in row}, "group": g(x)} for x in items[offset:offset + limit]]}


@app.get("/g/amp")
def g_amp(request: Request, file: str):
    who, bad = _need(request)
    if bad:
        return bad
    f = _amp_path(file)
    if f is None and file:
        # a print names its core as Pick has it ("nam/DITORIG/…"); the copy may keep it elsewhere — by its name then
        base = file.replace("\\", "/").rsplit("/", 1)[-1]
        hit = next((x for x in amps() if x["file"].rsplit("/", 1)[-1] == base), None)
        f = _amp_path(hit["file"]) if hit else None
    if f is None:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="application/octet-stream" if f.suffix.lower() == ".nam" else "audio/wav")


@app.get("/g/beats")
def g_beats(request: Request, name: str):
    """The song's beat grid as Pick found it on the Mac (beats.json beside the stems) — read, never computed here."""
    who, bad = _need(request)
    if bad:
        return bad
    d = _song_dir(name)
    grid = _jload(d / "beats.json", None) if d is not None else None
    if not isinstance(grid, dict) or not isinstance(grid.get("beats"), list):
        return JSONResponse({"error": "Beat grid není."}, status_code=404)
    return {k: grid.get(k) for k in ("beats", "downbeats", "bpm", "beats_per_bar", "duration")}


@app.get("/g/sections")
def g_sections(request: Request, name: str):
    who, bad = _need(request)
    if bad:
        return bad
    return {"stem": None, "sections": []}            # a print of his own is made on the Mac, in a jam


@app.get("/g/tabs")
def g_tabs(request: Request, name: str):
    who, bad = _need(request)
    if bad:
        return bad
    return {"tabs": []}


# ---------------------------------------------------------------- playlists and "Na příště"
def fav() -> dict:
    f = _read("fav.json", {"songs": [], "at": 0})
    return f if isinstance(f, dict) and isinstance(f.get("songs"), list) else {"songs": [], "at": 0}


@app.get("/g/playlists")
def g_playlists(request: Request):
    who, bad = _need(request)
    if bad:
        return bad
    have = {s["name"] for s in songs()}
    got = _read("playlists.json", {}) or {}
    rows = got.get("playlists") if isinstance(got, dict) and isinstance(got.get("playlists"), list) else None
    if rows is None:                                                            # the Mac has not sent them yet: read the copies
        rows = [dict(x, kind="own") for x in (_jload(HELPER / "steems-playlists.json", []) or []) if isinstance(x, dict)] + _dl_playlists()
    out = []
    for x in rows:
        if not isinstance(x, dict) or x.get("name") == "Jam — na příště":
            continue
        lst = [n for n in (x.get("songs") or []) if isinstance(n, str) and n in have]
        if lst:
            out.append({"name": str(x.get("name") or "")[:120], "songs": lst, "kind": "dl" if x.get("kind") == "dl" else "own",
                        "all": x.get("all") if isinstance(x.get("all"), int) else len(lst)})
    return {"playlists": out}


def _safe_name(name: str) -> str:
    name = "".join(c for c in (name or "") if c.isalnum() or c in " -_()[]").strip()
    return name[:80] or "playlist"


def _dl_playlists() -> list:
    """The downloaded playlists on the card's copy (folders in Playlists/), when it has them."""
    out = []
    try:
        folders = sorted((DATA / "Playlists").iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for folder in folders:
        if folder.is_dir() and not folder.name.startswith("."):
            try:
                names = sorted({_safe_name(f.stem) for f in folder.iterdir() if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith(".")}, key=str.lower)
            except OSError:
                continue
            out.append({"name": folder.name, "songs": names, "all": len(names), "kind": "dl"})
    return out


@app.get("/g/fav")
def g_fav(request: Request):
    who, bad = _need(request)
    if bad:
        return bad
    return {"name": "Jam — na příště", "songs": fav()["songs"]}


@app.post("/g/fav")
async def g_fav_change(request: Request):
    who, bad = _need(request)
    if bad:
        return bad
    body = await request.json()
    have = {s["name"] for s in songs()}
    f = fav()
    lst = list(f["songs"])
    for o in [o for o in ((body or {}).get("ops") or []) if isinstance(o, dict)][:200]:
        n = str(o.get("name") or "")
        if n not in have:
            continue
        if o.get("add") and n not in lst:
            lst.append(n)
        elif not o.get("add") and n in lst:
            lst.remove(n)
    _write("fav.json", {"songs": lst[:300], "at": time.time()})
    return {"name": "Jam — na příště", "songs": lst}


# ---------------------------------------------------------------- the Mac (it calls here; nothing here calls it)
def _token_ok(request: Request) -> bool:
    got = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    return bool(TOKEN) and len(TOKEN) >= 24 and secrets.compare_digest(got, TOKEN)


@app.get("/sync/fav")
def sync_fav(request: Request):
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    return fav()


@app.put("/sync/fav")
async def sync_fav_put(request: Request):
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    body = await request.json()
    lst = [n for n in ((body or {}).get("songs") or []) if isinstance(n, str) and _song_dir(n) is not None][:300]
    _write("fav.json", {"songs": lst, "at": time.time()})
    return {"ok": True, "songs": lst}


@app.put("/sync/playlists")
async def sync_playlists(request: Request):
    """The host's playlists — his own in Steems and the downloaded ones — each with its songs' names."""
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    body = await request.json()
    rows = []
    for x in [x for x in ((body or {}).get("playlists") or []) if isinstance(x, dict)][:500]:
        lst = [n for n in (x.get("songs") or []) if isinstance(n, str) and n and "/" not in n and ".." not in n][:2000]
        if lst:
            rows.append({"name": " ".join(str(x.get("name") or "").split())[:120], "songs": lst,
                         "kind": "dl" if x.get("kind") == "dl" else "own", "all": int(x.get("all") or len(lst)) if str(x.get("all") or "").isdigit() else len(lst)})
    _write("playlists.json", {"playlists": rows, "at": time.time()})
    return {"ok": True, "playlists": len(rows)}


@app.get("/sync/engine")
def sync_engine(request: Request):
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    return {n: hashlib.sha256((ENGINE_DIR / n).read_bytes()).hexdigest() for n in _engines()}


@app.put("/sync/engine/{name}")
async def sync_engine_put(request: Request, name: str):
    """A build of Pick Jam Engine from the Mac (they are not in the repository), kept for friends to download."""
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    if name not in ENGINE_FILES:
        return JSONResponse({"error": "Neznámý soubor."}, status_code=400)
    data = await request.body()
    if not 100_000 < len(data) < 30_000_000:
        return JSONResponse({"error": "Divná velikost."}, status_code=400)
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    part = ENGINE_DIR / (name + ".part")
    part.write_bytes(data)
    part.replace(ENGINE_DIR / name)
    return {"ok": True, "sha256": hashlib.sha256(data).hexdigest()}


@app.put("/sync/users")
async def sync_users(request: Request):
    """Pick's accounts (names, salts, hashes — never a password), all of them each time: one taken away in Pick goes here too."""
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    body = await request.json()
    got = {}
    for n, r in ((body or {}).get("users") or {}).items():
        if isinstance(n, str) and isinstance(r, dict) and re.fullmatch(r"[0-9a-f]{32}", str(r.get("salt"))) and re.fullmatch(r"[0-9a-f]{64}", str(r.get("hash"))):
            got[" ".join(n.split())[:30]] = {"salt": r["salt"], "hash": r["hash"]}
    _write("pick-users.json", dict(list(got.items())[:50]))
    return {"ok": True, "users": sorted(got)}


@app.post("/sync/status")
async def sync_status(request: Request):
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    body = await request.json()
    url = str((body or {}).get("url") or "")
    _write("status.json", {"on": bool((body or {}).get("on")), "url": url if url.startswith("https://") else "", "at": time.time()})
    return {"ok": True, "prepared": {k: PREP[k] for k in ("on", "done", "todo", "made", "failed", "note")}, "free_gb": round(_free(STATE) / 1024 ** 3, 1), "used_gb": round(_cache_size() / 1024 ** 3, 1)}


@app.get("/health")
def health():
    return {"ok": True, "songs_dir": _stems_root().is_dir(), "amps_dir": AMPS.is_dir(), "helper_dir": HELPER.is_dir(), "users": len(users())}


# ---------------------------------------------------------------- friends, from the command line in the container
if __name__ == "__main__":
    cmd = sys.argv[1:2] and sys.argv[1]
    if cmd == "adduser" and len(sys.argv) > 2:
        pw = getpass.getpass("Heslo (aspoň 6 znaků): ")
        if pw != getpass.getpass("Znovu: "):
            sys.exit("Hesla se neshodují.")
        user_set(" ".join(sys.argv[2:]), pw)
        print("Hotovo.")
    elif cmd == "deluser" and len(sys.argv) > 2:
        u = users()
        u.pop(" ".join(sys.argv[2:]), None)
        _write("users.json", u)
        print("Odebráno.")
    elif cmd == "users":
        print("\n".join(sorted(users())) or "(nikdo)")
    else:
        print(__doc__)
