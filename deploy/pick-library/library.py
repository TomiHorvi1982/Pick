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
LIB_DIRS = {"stretch": STATIC / "stretch", "nam": STATIC / "nam", "alphatab": STATIC / "alphatab"}
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


def users() -> dict:
    u = _read("users.json", {})
    return u if isinstance(u, dict) else {}


def user_set(name: str, pw: str) -> None:
    name = " ".join(name.split())[:30]
    if not name or len(pw) < 6:
        raise ValueError("Jméno a heslo aspoň o 6 znacích.")
    salt = secrets.token_bytes(16)
    u = users()
    u[name] = {"salt": salt.hex(), "hash": _hash(pw, salt), "at": time.time()}
    _write("users.json", u)


def _ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _who(request: Request) -> Optional[str]:
    v = SESS.get(request.cookies.get("pl") or "")
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
    rec = users().get(name)
    ok = False
    if rec:
        try:
            ok = secrets.compare_digest(_hash(str(body.get("password") or ""), bytes.fromhex(rec["salt"])), rec["hash"])
        except (ValueError, KeyError):
            ok = False
    else:
        _hash(str(body.get("password") or ""), b"0" * 16)
    if not ok:
        tries.append(now)
        return JSONResponse({"error": "Špatné jméno nebo heslo."}, status_code=401)
    tok = secrets.token_urlsafe(24)
    SESS[tok] = {"user": name, "exp": now + 30 * 24 * 3600}
    r = JSONResponse({"ok": True, "user": name})
    r.set_cookie("pl", tok, max_age=30 * 24 * 3600, httponly=True, samesite="strict",
                 secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    return r


@app.post("/logout")
def logout(request: Request):
    SESS.pop(request.cookies.get("pl") or "", None)
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
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def icon():
    f = STATIC / "pick-favicon.png"
    return Response(f.read_bytes(), media_type="image/png") if f.is_file() else Response(status_code=204)


@app.get("/static/{name}")
def guest_static(name: str):
    if name not in GUEST_FILES or not (STATIC / name).is_file():
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
    tuning = t.get("midi") if isinstance(t, dict) else t if isinstance(t, list) else None
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
    return {"user": _who(request), "login": True, "host": HOST, "on": False, "library": True, "jam": on, "jam_url": st.get("url") or JAM_URL}


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


CACHE = STATE / "cache"


@app.get("/g/preview")
async def g_preview(request: Request, name: str):
    """The song to listen to: its tracks mixed into one small file, made once."""
    who, bad = _need(request)
    if bad:
        return bad
    d = _song_dir(name)
    files = [f for f in (_stem_file(d, s) for s in STEM_NAMES) if f] if d else []
    if not files:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    sig = hashlib.sha1("|".join(f"{f}:{f.stat().st_mtime}" for f in files).encode()).hexdigest()[:16]
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{sig}.webm"
    if not out.is_file():
        cmd = ["ffmpeg", "-v", "error", "-y"] + sum([["-i", str(f)] for f in files], [])
        if len(files) > 1:
            cmd += ["-filter_complex", f"amix=inputs={len(files)}:normalize=0:duration=longest"]
        part = out.with_suffix(".part.webm")
        r = await asyncio.to_thread(subprocess.run, cmd + ["-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "96k", "-f", "webm", str(part)], capture_output=True, timeout=600)
        if r.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)
            return JSONResponse({"error": "Náhled se nepodařilo připravit."}, status_code=500)
        part.replace(out)
        for old in sorted(CACHE.glob("*.webm"), key=lambda p: p.stat().st_mtime)[:-60]:      # the newest 60
            old.unlink(missing_ok=True)
    return _range(out, request.headers.get("range"))


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


_AMPS = {"at": 0.0, "rows": []}


def amps() -> list:
    if time.time() - _AMPS["at"] < 600 and _AMPS["rows"]:
        return _AMPS["rows"]
    rows = []
    if AMPS.is_dir():
        for f in AMPS.rglob("*"):
            if f.suffix.lower() in (".nam", ".wav") and f.is_file():
                rows.append({"name": f.stem, "file": f.relative_to(AMPS).as_posix(), "kind": "model" if f.suffix.lower() == ".nam" else "ir"})
    _AMPS.update(at=time.time(), rows=sorted(rows, key=lambda x: x["name"].lower()))
    return _AMPS["rows"]


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
            parts = f.split("/")
            folder = "/".join(parts[1:-1]) if len(parts) > 2 else ""
            pack = parts[0] if len(parts) > 1 and parts[0].lower() not in ("nam", "ir") else "Kolekce"
            if it["kind"] == "model":
                try:
                    st = (AMPS / f).stat()
                    sig = f"{st.st_size}:{int(st.st_mtime)}"
                except OSError:
                    sig = ""
                got = old.get(f)
                meta = got["meta"] if got and got.get("sig") == sig else ac._meta(AMPS / f)
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
    f = (AMPS / file).resolve() if file else None
    if f is None or not f.is_relative_to(AMPS.resolve()) or not f.is_file():
        # a print names its core as Pick has it ("nam/DITORIG/…"); the copy may keep it elsewhere — by its name then
        base = file.replace("\\", "/").rsplit("/", 1)[-1]
        hit = next((x for x in amps() if x["file"].rsplit("/", 1)[-1] == base), None)
        f = (AMPS / hit["file"]).resolve() if hit else None
    if f is None or not f.is_relative_to(AMPS.resolve()) or not f.is_file() or f.suffix.lower() not in (".nam", ".wav"):
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="application/octet-stream" if f.suffix.lower() == ".nam" else "audio/wav")


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
    rows = _jload(HELPER / "steems-playlists.json", []) or []
    return {"playlists": [{"name": x.get("name") or "", "songs": [n for n in (x.get("songs") or []) if isinstance(n, str)]}
                          for x in rows if isinstance(x, dict) and x.get("songs") and x.get("name") != "Jam — na příště"]}


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


@app.post("/sync/status")
async def sync_status(request: Request):
    if not _token_ok(request):
        return JSONResponse({"error": "Nepovoleno."}, status_code=403)
    body = await request.json()
    url = str((body or {}).get("url") or "")
    _write("status.json", {"on": bool((body or {}).get("on")), "url": url if url.startswith("https://") else "", "at": time.time()})
    return {"ok": True}


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
