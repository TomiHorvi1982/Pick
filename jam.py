"""Jam — playing together over the internet with a friend (Tailscale between the two Macs).

The host is Pick; the friend opens a link in Chrome and needs nothing installed. Two servers:

* Pick's own (127.0.0.1:8765) — the host's page and its end of the room (/api/jam/…).
* The jam gateway (127.0.0.1:8766), the only thing the friend reaches: `tailscale serve` puts it on the
  tailnet as https://<this-mac>.<tailnet>.ts.net (a secure page, so the friend's camera and microphone
  work). It serves the guest page and its scripts, the room's messages, and the backing of the song being
  played — nothing else of Pick, and nothing at all without the room's key.

Between the two browsers everything goes peer to peer (WebRTC): the cameras, the host's screen (Pick
itself), the guitars — either as a call (Opus, tuned for music) or, for the least delay, the way SonoBus
does it: uncompressed sound in small packets over an unreliable data channel with a small jitter buffer of
our own (static/jam-engine.js, static/jam-worklet.js). The server only passes the setting-up messages on.
"""
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
GATE_PORT = int(os.environ.get("PICK_JAM_PORT") or 8766)        # tests run their own on another port
GUEST_FILES = {"jam-engine.js": "text/javascript", "jam-worklet.js": "text/javascript", "jam-guest.js": "text/javascript", "jam-chat.js": "text/javascript", "jam-mix.js": "text/javascript", "jam-amp.js": "text/javascript",
               "dj-core.js": "text/javascript", "dj.js": "text/javascript", "dj-library.js": "text/javascript"}
# Pick DJ in the friend's page too (the same as in the library on the PC): put in by guest_page
DJ_FILES = ("dj-core.js", "dj.js", "dj-library.js")
CHAT_KEEP = 300
CHAT_MAX = 2000
MIX_DIR = Path(tempfile.gettempdir()) / "pick-jam-mix"
CONF = Path.home() / ".local/share/spotdl-helper/jam.json"     # how the friend gets in: Tailscale, or my own domain
CF_DIR = Path.home() / ".cloudflared"
CF_TUNNEL = "pick-jam"

router = APIRouter()
# set by server.py: the stems folder of a song (or None), a stem's file, ffmpeg
stems_dir: Callable[[str], Optional[Path]] = lambda name: None
stem_file: Callable[[Path, str], Optional[Path]] = lambda d, s: None
ffmpeg: Callable[[], str] = lambda: "ffmpeg"
music_file: Callable[[str], Optional[Path]] = lambda rel: None      # a file under the music root, by its rel
range_file: Callable = lambda path, rng: FileResponse(path)          # Pick's own: a file served by ranges (a seek in <audio>)
main_app = None                     # Pick's own app: the friend's page reads from it, through _pick() and an allow-list
friend_print: Callable = lambda *a, **k: {"error": "Otisky nejsou k dispozici.", "status": 503}
song_covers: Callable[..., dict] = lambda **k: {}                       # the songs' covers for the library (server.song_covers)
cover_bytes: Callable = lambda d: None                                     # (bytes, mime) of a song's own cover
downloaded_playlists: Callable[[], list] = lambda: []                 # the downloaded playlists (Playlists/ folders) with songs that have stems


class Peer:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.loop = asyncio.get_running_loop()

    async def send(self, text: str) -> None:
        """Sent from whichever server's loop: the host's and the friend's sockets live on different ones."""
        try:
            if asyncio.get_running_loop() is self.loop:
                await self.ws.send_text(text)
            else:
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.ws.send_text(text), self.loop))
        except Exception:
            pass


ROOM: Dict = {"key": None, "host": None, "guest": None, "song": None, "stems": [], "url": None, "started": 0}


def _chat_reset() -> None:
    """The chat of this room: the messages, what each of the two has read, when each was last here, away or not."""
    ROOM.update(chat=[], read={}, seen={}, away={})
_GATE: Dict = {"thread": None, "server": None}


# ---- Tailscale: its command line (the app's own, or Homebrew's), this Mac's name on the tailnet, `serve`
def _ts_bin() -> Optional[str]:
    app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    if app.is_file():
        return str(app)
    return shutil.which("tailscale")


def _ts(*args, timeout: float = 15) -> subprocess.CompletedProcess:
    b = _ts_bin()
    if not b:
        raise FileNotFoundError("tailscale")
    # The app's binary acts as the command line only when run from a terminal (it looks at TERM); Pick runs in the
    # background without one, and the binary would try to open its window instead — and fail.
    env = {**os.environ, "TERM": os.environ.get("TERM") or "xterm-256color"}
    return subprocess.run([b, *args], capture_output=True, text=True, timeout=timeout, env=env)


def tailscale_state() -> dict:
    if not _ts_bin():
        return {"ok": False, "why": "notinstalled"}
    try:
        r = _ts("status", "--json", timeout=8)
        d = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return {"ok": False, "why": "notrunning"}
    if d.get("BackendState") != "Running":
        return {"ok": False, "why": "notrunning" if d.get("BackendState") in (None, "Stopped", "NoState") else "login",
                "state": d.get("BackendState"), "authUrl": d.get("AuthURL") or ""}
    me = d.get("Self") or {}
    dns = (me.get("DNSName") or "").rstrip(".")
    return {"ok": True, "dns": dns, "ips": me.get("TailscaleIPs") or [], "certDomains": d.get("CertDomains") or [],
            "peers": [{"name": (p.get("DNSName") or "").split(".")[0], "online": p.get("Online")} for p in (d.get("Peer") or {}).values()]}


def _serve_on() -> dict:
    """https://<this mac>.ts.net → the jam gateway (only on the tailnet)."""
    st = tailscale_state()
    if not st.get("ok"):
        return st
    try:
        r = _ts("serve", "--bg", "--https=443", f"http://127.0.0.1:{GATE_PORT}", timeout=20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "https", "msg": "Tailscale čeká na zapnutí HTTPS v administraci tailnetu."}
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        why = "https" if ("HTTPS" in out or "https" in out.lower() and "enable" in out.lower()) else "serve"
        link = next((w for w in out.split() if w.startswith("https://login.tailscale.com")), "")
        return {"ok": False, "why": why, "msg": out.strip()[:600], "authUrl": link}
    return {"ok": True, "dns": st["dns"]}


def _serve_off() -> None:
    try:
        _ts("serve", "--https=443", "off", timeout=10)
    except Exception:
        pass


# ---- or my own domain (jam.neverlast.cz): a Cloudflare Tunnel to the same gateway — the friend installs nothing,
# opens the link anywhere. The tunnel runs only while a jam is on.
_CF: Dict = {"proc": None, "login": None, "log": None}


def conf() -> dict:
    try:
        d = json.loads(CONF.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def conf_save(**kw) -> dict:
    d = {**conf(), **kw}
    CONF.parent.mkdir(parents=True, exist_ok=True)
    CONF.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def _cf_bin() -> Optional[str]:
    for c in (shutil.which("cloudflared"), "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
        if c and Path(c).is_file():
            return c
    return None


def _cf(*args, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([_cf_bin() or "cloudflared", *args], capture_output=True, text=True, timeout=timeout)


def _host_ok(h: str) -> bool:
    import re
    return bool(re.fullmatch(r"(?=.{4,200}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", h or ""))


def cf_state() -> dict:
    c = conf()
    st = {"installed": bool(_cf_bin()), "logged": (CF_DIR / "cert.pem").is_file(), "host": c.get("host") or "",
          "tunnel": c.get("tunnel_id") or "", "routed": c.get("routed") == c.get("host") and bool(c.get("host")),
          "loggingIn": bool(_CF["login"] and _CF["login"].poll() is None), "running": bool(_CF["proc"] and _CF["proc"].poll() is None)}
    if st["loggingIn"] and _CF.get("loginLog"):
        try:
            st["loginUrl"] = next((w for w in _CF["loginLog"].read_text(errors="ignore").split() if w.startswith("https://dash.cloudflare.com/argotunnel")), "")
        except OSError:
            pass
    st["ready"] = st["installed"] and st["logged"] and bool(st["tunnel"]) and st["routed"] and (CF_DIR / f"{st['tunnel']}.json").is_file()
    return st


def cf_login() -> dict:
    """Opens the browser at Cloudflare: log in and pick the domain; the certificate lands in ~/.cloudflared."""
    if not _cf_bin():
        return {"ok": False, "msg": "Chybí program cloudflared (brew install cloudflared)."}
    if (CF_DIR / "cert.pem").is_file():
        return {"ok": True}
    if not (_CF["login"] and _CF["login"].poll() is None):
        log = Path(tempfile.gettempdir()) / "pick-jam-cflogin.log"
        _CF["loginLog"] = log
        _CF["login"] = subprocess.Popen([_cf_bin(), "tunnel", "login"], stdout=open(log, "w"), stderr=subprocess.STDOUT)
    return {"ok": True, "waiting": True}


def cf_setup(host: str) -> dict:
    """The tunnel (once) and jam.<domain> pointed at it in the domain's DNS."""
    host = (host or "").strip().lower().rstrip(".")
    if not _host_ok(host):
        return {"ok": False, "msg": "Zadej adresu jako jam.tvojedomena.cz."}
    if not (CF_DIR / "cert.pem").is_file():
        return {"ok": False, "msg": "Nejdřív se přihlas do Cloudflare (krok 1)."}
    c = conf()
    tid = c.get("tunnel_id")
    if not tid or not (CF_DIR / f"{tid}.json").is_file():
        r = _cf("tunnel", "list", "--output", "json", "--name", CF_TUNNEL)
        try:
            old = [t for t in (json.loads(r.stdout or "[]") or []) if isinstance(t, dict) and t.get("name") == CF_TUNNEL and (not t.get("deleted_at") or str(t["deleted_at"]).startswith("0001"))]
        except ValueError:
            old = []
        if old and not (CF_DIR / f"{old[0]['id']}.json").is_file():
            _cf("tunnel", "delete", "-f", CF_TUNNEL)                    # made elsewhere, its key is not here: anew
            old = []
        if old:
            tid = old[0]["id"]
        else:
            r = _cf("tunnel", "create", "--output", "json", CF_TUNNEL)
            try:
                tid = json.loads(r.stdout)["id"]
            except (ValueError, KeyError, TypeError):
                return {"ok": False, "msg": "Tunel se nepodařilo vytvořit: " + ((r.stderr or r.stdout or "").strip()[-400:])}
        conf_save(tunnel_id=tid)
    r = _cf("tunnel", "route", "dns", "--overwrite-dns", CF_TUNNEL, host)
    if r.returncode != 0:
        out = (r.stderr or r.stdout or "").strip()
        if "already exists" in out:
            return {"ok": False, "exists": True, "msg": f"Na adrese {host} už je v DNS jiný záznam (třeba starý web). Smaž ho v Cloudflare: "
                    f"dash.cloudflare.com → {'.'.join(host.split('.')[-2:])} → DNS → Records → u „{host}“ Delete. Pak dej Propojit znovu."}
        return {"ok": False, "msg": "Adresu se nepodařilo nasměrovat: " + out[-400:]}
    conf_save(host=host, routed=host)
    return {"ok": True, **cf_state()}


def _cf_up() -> dict:
    st = cf_state()
    if not st["ready"]:
        return {"ok": False, "why": "domain", "msg": "Vlastní doména ještě není nastavená (Nastavení → Jam)."}
    if st["running"]:
        return {"ok": True, "host": st["host"]}
    # a tunnel left from before Pick restarted: ours, so it goes
    subprocess.run(["pkill", "-f", f"tunnel --no-autoupdate run --url http://127.0.0.1:{GATE_PORT} {CF_TUNNEL}"], capture_output=True)
    log = Path(tempfile.gettempdir()) / "pick-jam-tunnel.log"
    f = open(log, "w")
    _CF["proc"] = subprocess.Popen([_cf_bin(), "tunnel", "--no-autoupdate", "run", "--url", f"http://127.0.0.1:{GATE_PORT}", CF_TUNNEL],
                                   stdout=f, stderr=subprocess.STDOUT)
    _CF["log"] = log
    for _ in range(100):                          # up when Cloudflare has the first connection
        time.sleep(0.15)
        text = log.read_text(errors="ignore")
        if "Registered tunnel connection" in text:
            return {"ok": True, "host": st["host"]}
        if _CF["proc"].poll() is not None:
            break
    if _CF["proc"].poll() is None:
        return {"ok": True, "host": st["host"]}  # slow, but running
    return {"ok": False, "why": "tunnel", "msg": "Tunel se nespustil: " + log.read_text(errors="ignore").strip()[-400:]}


def _cf_down() -> None:
    p = _CF["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
    _CF["proc"] = None


# ---- the gateway: what the friend can reach
gate = FastAPI()


def _key_ok(k: Optional[str]) -> bool:
    return bool(ROOM["key"]) and bool(k) and secrets.compare_digest(str(k), str(ROOM["key"]))


# ---- who comes in: with names and passwords set (Nastavení → Jam), the friend logs in and a cookie says who he
# is; without them, the room's key in the link is enough (as it was). Wrong passwords: a few, then a pause.
SESS: Dict[str, dict] = {}
SESS_HOURS = 12
_FAILS: Dict[str, list] = {}


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def users() -> Dict[str, dict]:
    u = conf().get("users")
    return u if isinstance(u, dict) else {}


def host_name() -> str:
    return (conf().get("host_name") or "Tomi").strip()[:30] or "Tomi"


def user_set(name: str, password: str) -> dict:
    name = " ".join((name or "").split())[:30]
    if not name or len(password or "") < 6:
        return {"ok": False, "msg": "Jméno a heslo aspoň o 6 znacích."}
    salt = secrets.token_bytes(16)
    u = users()
    u[name] = {"salt": salt.hex(), "hash": _hash(password, salt), "at": time.time()}
    conf_save(users=u)
    _lib_kick(accounts=True)
    for t in [t for t, v in SESS.items() if v["user"] == name]:      # a new password: out, in again
        SESS.pop(t, None)
    return {"ok": True, "users": sorted(u)}


def user_delete(name: str) -> dict:
    u = users()
    u.pop(name, None)
    conf_save(users=u)
    _lib_kick(accounts=True)
    for t in [t for t, v in SESS.items() if v["user"] == name]:
        SESS.pop(t, None)
    return {"ok": True, "users": sorted(u)}


def _ip(scope_headers, client) -> str:
    h = {k.decode().lower(): v.decode() for k, v in scope_headers} if isinstance(scope_headers, list) else {k.lower(): v for k, v in scope_headers.items()}
    return h.get("cf-connecting-ip") or (client.host if client else "?")


def login(name: str, password: str, ip: str) -> Optional[str]:
    now = time.time()
    tries = [t for t in _FAILS.get(ip, []) if now - t < 600]
    _FAILS[ip] = tries
    if len(tries) >= 8:
        raise PermissionError("Moc pokusů — zkus to za 10 minut.")
    rec = users().get(" ".join((name or "").split()))
    ok = False
    if rec:
        try:
            ok = secrets.compare_digest(_hash(password or "", bytes.fromhex(rec["salt"])), rec["hash"])
        except (ValueError, KeyError):
            ok = False
    else:
        _hash(password or "", b"0" * 16)                         # the same time for a name that is not there
    if not ok:
        tries.append(now)
        return None
    _FAILS.pop(ip, None)
    tok = secrets.token_urlsafe(24)
    SESS[tok] = {"user": " ".join(name.split()), "exp": now + SESS_HOURS * 3600}
    return tok


def _who(cookies, k: str = "") -> Optional[str]:
    """The friend's name when he may in: logged in (names set), or with the room's key (no names set)."""
    if users():
        t = cookies.get("pj") or ""
        v = SESS.get(t)
        if v and v["exp"] > time.time() and v["user"] in users():
            return v["user"]
        return None
    return "Kamarád" if _key_ok(k) else None


@gate.get("/", response_class=HTMLResponse)
def guest_page(k: str = ""):
    if not users() and not _key_ok(k):
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Pick Jam</title><body style='font:16px system-ui;background:#111;color:#ddd;padding:40px'>"
                            "<h2>Pick Jam</h2><p>Tenhle odkaz už neplatí — popros kamaráda o nový.</p>", status_code=403)
    # every script by its version: Cloudflare tells browsers to keep files for hours, so a new version gets a new address
    html = (STATIC / "jam.html").read_text(encoding="utf-8")
    for name in GUEST_FILES:
        f = STATIC / name
        if f.is_file():
            html = html.replace(f"/static/{name}\"", f"/static/{name}?v={int(f.stat().st_mtime)}\"")
    dj = [STATIC / n for n in DJ_FILES if (STATIC / n).is_file()]
    if len(dj) == len(DJ_FILES):
        html = html.replace("</body>", "".join(f'<script src="/static/{f.name}?v={int(f.stat().st_mtime)}"></script>' for f in dj) + "</body>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@gate.get("/static/worklets/dj-deck.js")
def guest_dj_worklet():
    return Response((STATIC / "worklets" / "dj-deck.js").read_text(encoding="utf-8"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@gate.get("/favicon.ico")
def guest_icon():
    f = STATIC / "pick-favicon.png"
    return Response(f.read_bytes(), media_type="image/png") if f.is_file() else Response(status_code=204)


@gate.get("/static/{name}")
def guest_static(name: str):
    if name not in GUEST_FILES:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return Response((STATIC / name).read_text(encoding="utf-8"), media_type=GUEST_FILES[name], headers={"Cache-Control": "no-cache"})


# the engines the friend's page runs too: the time-stretch (tempo, key) and NAM (his amp, his print)
LIB_DIRS = {"stretch": STATIC / "stretch", "nam": STATIC / "nam", "alphatab": STATIC / "alphatab"}
LIB_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm", ".json": "application/json",
             ".woff": "font/woff", ".woff2": "font/woff2", ".otf": "font/otf", ".eot": "application/vnd.ms-fontobject", ".svg": "image/svg+xml"}


@gate.get("/static/{lib}/{name:path}")
def guest_lib(lib: str, name: str):
    root = LIB_DIRS.get(lib)
    if root is None:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    f = (root / name).resolve()
    if not f.is_relative_to(root.resolve()) or not f.is_file() or f.suffix not in LIB_TYPES:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type=LIB_TYPES[f.suffix], headers={"Cache-Control": "no-cache"})


@gate.post("/login")
async def guest_login(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        tok = login(str(body.get("name") or ""), str(body.get("password") or ""), _ip(request.headers, request.client))
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=429)
    if not tok:
        return JSONResponse({"error": "Špatné jméno nebo heslo."}, status_code=401)
    r = JSONResponse({"ok": True, "user": SESS[tok]["user"]})
    r.set_cookie("pj", tok, max_age=SESS_HOURS * 3600, httponly=True, samesite="strict", secure=request.url.scheme == "https"
                 or request.headers.get("x-forwarded-proto") == "https")
    return r


# ---- one login for both: the friend logged in to the library on the PC (jam.neverlast.cz) comes into the jam with a
# one-time ticket it signed with the key both sides share (Nastavení → Jam → Knihovna na PC) — no second login
_SSO_SEEN: Dict[str, float] = {}


def _sso_user(t: str) -> Optional[str]:
    import base64
    import hmac
    key = conf().get("library_token") or ""
    if len(key) < 24 or "." not in (t or ""):
        return None
    body, sig = t.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(key.encode(), body.encode(), "sha256").hexdigest(), sig):
        return None
    try:
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    now = time.time()
    for n in [n for n, at in _SSO_SEEN.items() if now - at > 300]:
        _SSO_SEEN.pop(n, None)
    name, exp, nonce = str(p.get("u") or ""), float(p.get("exp") or 0), str(p.get("n") or "")
    if not (now < exp < now + 180) or not nonce or nonce in _SSO_SEEN or name not in users():
        return None
    _SSO_SEEN[nonce] = now
    return name


@gate.get("/sso", response_class=HTMLResponse)
def guest_sso(request: Request, t: str = ""):
    name = _sso_user(t)
    if not name:
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Pick Jam</title><script>location.replace('/')</script>", status_code=403)
    tok = secrets.token_urlsafe(24)
    SESS[tok] = {"user": name, "exp": time.time() + SESS_HOURS * 3600}
    # the page goes on to the jam by itself: a navigation from this site, so the (strict) cookie goes with it
    r = HTMLResponse("<!doctype html><meta charset=utf-8><title>Pick Jam</title><script>location.replace('/')</script>")
    r.set_cookie("pj", tok, max_age=SESS_HOURS * 3600, httponly=True, samesite="strict", secure=request.url.scheme == "https"
                 or request.headers.get("x-forwarded-proto") == "https")
    return r


@gate.post("/logout")
def guest_logout(request: Request):
    SESS.pop(request.cookies.get("pj") or "", None)
    r = JSONResponse({"ok": True})
    r.delete_cookie("pj")
    return r


@gate.get("/g/me")
def guest_me(request: Request, k: str = ""):
    who = _who(request.cookies, k)
    return {"user": who, "login": bool(users()), "host": host_name(), "on": bool(ROOM["key"]),
            "engine": engine_files() if who else []}


# Pick Jam Engine (engine/, built by engine/build-dist.sh): the friend downloads it from here once let in
ENGINE_DIST = APP_DIR / "engine" / "dist"
ENGINE_FILES = {"pick-jam-engine.exe": "application/vnd.microsoft.portable-executable",
                "pick-jam-engine-mac": "application/octet-stream"}


def engine_files() -> List[str]:
    return [n for n in ENGINE_FILES if (ENGINE_DIST / n).is_file()]


def start_engine() -> None:
    """Pick Jam Engine on this Mac, waiting for the page on 127.0.0.1:9877 — started with Pick so the native mode is just a choice.
    Left alone when something already listens there (one started by hand, or from Pick's last run)."""
    import platform
    import socket
    exe = ENGINE_DIST / ("pick-jam-engine.exe" if os.name == "nt" else "pick-jam-engine-mac")
    if platform.system() != "Darwin" and os.name != "nt" or not exe.is_file():
        return
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", 9877)) == 0:
            return
    try:
        subprocess.Popen([str(exe), "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


@gate.get("/engine/{name}")
def guest_engine(request: Request, name: str, k: str = ""):
    if not _who(request.cookies, k):
        return JSONResponse({"error": "Nejdřív se přihlas."}, status_code=403)
    if name not in ENGINE_FILES or not (ENGINE_DIST / name).is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(ENGINE_DIST / name, media_type=ENGINE_FILES[name], filename=name, headers={"Cache-Control": "no-cache"})


@gate.get("/mix")
def guest_mix(request: Request, k: str = "", stems: str = ""):
    """The backing of the song being played, as one file: the tracks asked for mixed (Opus in WebM)."""
    if not _who(request.cookies, k) or not ROOM["key"] or not ROOM["song"]:
        return JSONResponse({"error": "Žádná píseň."}, status_code=403)
    return mix_file(ROOM["song"], [s for s in stems.split(",") if s in ROOM["stems"]])


def mix_file(song: str, want: List[str]):
    d = stems_dir(song)
    if d is None:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    files = [f for f in (stem_file(d, s) for s in want) if f is not None]
    if not files:
        return JSONResponse({"error": "Žádná stopa."}, status_code=400)
    sig = hashlib.sha1("|".join(f"{f}:{f.stat().st_mtime}" for f in files).encode()).hexdigest()[:16]
    MIX_DIR.mkdir(parents=True, exist_ok=True)
    out = MIX_DIR / f"{sig}.webm"
    if not out.is_file():
        cmd = [ffmpeg(), "-v", "error", "-y"]
        for f in files:
            cmd += ["-i", str(f)]
        if len(files) > 1:
            cmd += ["-filter_complex", f"amix=inputs={len(files)}:normalize=0:duration=longest"]
        part = out.with_suffix(".part.webm")
        r = subprocess.run(cmd + ["-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "192k", "-f", "webm", str(part)], capture_output=True, timeout=300)
        if r.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)
            return JSONResponse({"error": "Podklad se nepodařilo smíchat."}, status_code=500)
        part.replace(out)
        for old in sorted(MIX_DIR.glob("*.webm"), key=lambda p: p.stat().st_mtime)[:-12]:
            old.unlink(missing_ok=True)
    return FileResponse(out, media_type="audio/webm")


async def _room(ws: WebSocket, role: str):
    await ws.accept()
    other = "guest" if role == "host" else "host"
    if ROOM.get(role):                       # one of each: a second tab takes over; the first one is told so and stays off
        try:
            await ROOM[role].ws.close(code=4409)
        except Exception:
            pass
    me = Peer(ws)
    ROOM[role] = me
    if "chat" not in ROOM:
        _chat_reset()
    # the other one's messages that waited for me: delivered now
    waited = [m["id"] for m in ROOM["chat"] if m["from"] == other and not m["dlv"]]
    for m in ROOM["chat"]:
        if m["from"] == other:
            m["dlv"] = True
    ROOM["away"][role] = False
    await me.send(json.dumps({"type": "hello", "role": role, "peer": bool(ROOM.get(other)), "chat": ROOM["chat"][-200:],
                              "names": {"host": host_name(), "guest": ROOM.get("guest_name") or "Kamarád"},
                              "read": ROOM["read"], "seen": ROOM["seen"].get(other), "away": bool(ROOM["away"].get(other))}))
    if ROOM.get(other):
        await ROOM[other].send(json.dumps({"type": "peer", "on": True, "names": {"host": host_name(), "guest": ROOM.get("guest_name") or "Kamarád"}}))
        if waited:
            await ROOM[other].send(json.dumps({"type": "chat-dlv", "ids": waited}))
    try:
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if role == "host" and kind == "song":
                ROOM["song"] = msg.get("name") or None
                ROOM["stems"] = [s for s in (msg.get("stems") or []) if isinstance(s, str)][:12]
            if kind == "chat":
                await _chat_in(me, role, other, msg)
                continue
            if kind == "read":
                mid = str(msg.get("id") or "")[:40]
                if any(m["id"] == mid for m in ROOM["chat"]):
                    ROOM["read"][role] = {"id": mid, "t": time.time()}
                    text = json.dumps({"type": "read", "id": mid, "t": ROOM["read"][role]["t"]})
                else:
                    continue
            if kind == "away":
                ROOM["away"][role] = bool(msg.get("on"))
                text = json.dumps({"type": "away", "on": ROOM["away"][role]})
            if kind == "typing":
                text = json.dumps({"type": "typing", "on": bool(msg.get("on"))})
            if ROOM.get(other):
                await ROOM[other].send(text)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if ROOM.get(role) is me:
            ROOM[role] = None
            if "seen" in ROOM:
                ROOM["seen"][role] = time.time()
                ROOM["away"][role] = False
            if ROOM.get(other):
                await ROOM[other].send(json.dumps({"type": "peer", "on": False, "seen": ROOM.get("seen", {}).get(role)}))


async def _chat_in(me: Peer, role: str, other: str, msg: dict) -> None:
    """A message: kept in the room (it is there after a reload), passed on, and its sender told it arrived."""
    mid, txt = str(msg.get("id") or "")[:40], msg.get("text")
    if not mid or not isinstance(txt, str) or not txt.strip():
        return
    if any(m["id"] == mid for m in ROOM["chat"]):           # sent again after a reconnect: already here
        old = next(m for m in ROOM["chat"] if m["id"] == mid)
        await me.send(json.dumps({"type": "chat-ok", "id": mid, "t": old["t"], "dlv": old["dlv"]}))
        return
    m = {"type": "chat", "id": mid, "from": role, "text": txt.strip()[:CHAT_MAX], "t": time.time(), "dlv": bool(ROOM.get(other))}
    ROOM["chat"].append(m)
    del ROOM["chat"][:-CHAT_KEEP]
    await me.send(json.dumps({"type": "chat-ok", "id": mid, "t": m["t"], "dlv": m["dlv"]}))
    if ROOM.get(other):
        await ROOM[other].send(json.dumps(m))


@gate.websocket("/ws")
async def guest_ws(ws: WebSocket, k: str = ""):
    who = _who(ws.cookies, k)
    if not who or not ROOM["key"]:
        await ws.close(code=4403)
        return
    ROOM["guest_name"] = who
    await _room(ws, "guest")


def _gate_start() -> None:
    if _GATE["thread"] and _GATE["thread"].is_alive():
        return
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(gate, host="127.0.0.1", port=GATE_PORT, log_level="warning", ws="websockets"))
    server.install_signal_handlers = lambda: None
    t = threading.Thread(target=server.run, daemon=True, name="jam-gateway")
    t.start()
    _GATE.update(thread=t, server=server)
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)


# ---- the host's end (Pick's own server)
@router.get("/api/jam/status")
def jam_status():
    via = conf().get("via") or "tailscale"
    return {"on": bool(ROOM["key"]), "url": ROOM["url"], "guest": bool(ROOM["guest"]), "via": via,
            "tailscale": tailscale_state() if via == "tailscale" else {"ok": True}, "domain": cf_state()}


@router.post("/api/jam/start")
def jam_start(via: str = ""):
    if via in ("tailscale", "domain"):
        conf_save(via=via)
    via = conf().get("via") or "tailscale"
    _gate_start()
    if os.environ.get("PICK_JAM_LOCAL"):          # tests: the gateway on this Mac only, no Tailscale
        ROOM["key"] = ROOM["key"] or secrets.token_urlsafe(9)
        ROOM["url"] = f"http://127.0.0.1:{GATE_PORT}/?k={ROOM['key']}"
        return {"ok": True, "url": ROOM["url"]}
    if via == "domain":
        st = _cf_up()
        if not st.get("ok"):
            return JSONResponse(st, status_code=409)
        _serve_off()
        base = st["host"]
    else:
        st = _serve_on()
        if not st.get("ok"):
            return JSONResponse(st, status_code=409)
        _cf_down()
        base = st["dns"]
    if not ROOM["key"]:
        ROOM["key"] = secrets.token_urlsafe(9)
        ROOM["started"] = time.time()
    ROOM["url"] = f"https://{base}/?k={ROOM['key']}"
    _lib_kick()
    return {"ok": True, "url": ROOM["url"]}


@router.post("/api/jam/stop")
def jam_stop():
    ROOM.update(key=None, url=None, song=None, stems=[], guest_name=None)
    _chat_reset()
    _serve_off()
    _cf_down()
    _lib_kick()
    for role in ("guest", "host"):
        p = ROOM.get(role)
        if p:
            try:
                asyncio.run_coroutine_threadsafe(p.ws.close(), p.loop)
            except Exception:
                pass
    return {"ok": True}


@router.websocket("/api/jam/ws")
async def host_ws(ws: WebSocket):
    if ws.client and ws.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        await ws.close(code=4403)
        return
    await _room(ws, "host")


@router.get("/api/jam/mix")
def host_mix(song: str, stems: str = ""):
    """The same backing the friend gets — for the host to try the mix (and for tests)."""
    return mix_file(song, [s for s in stems.split(",") if s])


@router.post("/api/jam/domain/login")
def jam_domain_login():
    return cf_login()


@router.post("/api/jam/domain/setup")
async def jam_domain_setup(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    r = await asyncio.to_thread(cf_setup, str((body or {}).get("host") or ""))
    return r if r.get("ok") else JSONResponse(r, status_code=400)


@router.post("/api/jam/via")
async def jam_via(request: Request):
    body = await request.json()
    via = body.get("via") if isinstance(body, dict) else None
    if via not in ("tailscale", "domain"):
        return JSONResponse({"error": "via"}, status_code=400)
    conf_save(via=via)
    return {"ok": True, "via": via}



# ---- the friend's Pick: Steems only, read-only. Every answer comes from Pick's own routes (_pick), and only the
# ones listed here; nothing that writes, deletes or reaches the files of this Mac other than the song's own.
async def _pick(path: str, params: Optional[dict] = None):
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_app), base_url="http://pick", timeout=120) as c:
        return await c.get(path, params=params)


def _guest_or_403(request: Request):
    """Logged in (names set): the songs, the playlists, Na příště. With the key only: in a jam."""
    who = _who(request.cookies, request.query_params.get("k") or "")
    if not who or (not users() and not ROOM["key"]):
        return None, JSONResponse({"error": "Nejsi přihlášený nebo jam neběží."}, status_code=403)
    return who, None


from urllib.parse import quote as _q

_SONGS: Dict = {"at": 0.0, "rows": []}


@gate.get("/g/songs")
async def g_songs(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if time.time() - _SONGS["at"] > 120:
        r = await _pick("/api/stems")
        rows = r.json() if r.status_code == 200 else []
        _SONGS.update(at=time.time(), rows=[{"name": x.get("name"), "stems": x.get("stems") or [], "duration": x.get("duration"),
                                             "artist": (x.get("meta") or {}).get("artist") or "", "title": (x.get("meta") or {}).get("title") or x.get("name"),
                                             "tuning": x.get("tuning"), "yt": _yt_of(x.get("name") or "")} for x in rows if isinstance(x, dict) and x.get("stems")])
    return {"songs": _SONGS["rows"]}


def _song_ok(name: str) -> bool:
    return bool(name) and "/" not in name and ".." not in name and stems_dir(name) is not None


@gate.get("/g/song")
async def g_song(request: Request, name: str):
    """One song for the friend's mixer: the host's mix as where to start, the sections, the chords."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    base = f"/api/stems/{_q(name, safe='')}"
    ses, ana, ch = await _pick(base + "/session"), await _pick(base + "/analysis"), await _pick(base + "/chords")
    sj = ses.json() if ses.status_code == 200 else {}
    sj = sj.get("session") if isinstance(sj, dict) and isinstance(sj.get("session"), dict) else sj
    chans = [{k: c.get(k) for k in ("stem", "muted", "solo", "faderValue", "panValue", "pitchRate", "hidden")}
             for c in ((sj or {}).get("channels") or []) if isinstance(c, dict)]
    a = ana.json() if ana.status_code == 200 else {}
    c = ch.json() if ch.status_code == 200 else {}
    return {"name": name, "channels": chans, "sections": a.get("sections") or [], "chords": c.get("chords") or [],
            "bpm": a.get("bpm") or a.get("tempo"), "key": a.get("key")}


@gate.get("/g/lyrics")
async def g_lyrics(request: Request, name: str):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    row = next((x for x in _SONGS["rows"] if x["name"] == name), None)
    if row is None:
        await g_songs(request)
        row = next((x for x in _SONGS["rows"] if x["name"] == name), None)
    if row is None:
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    params = {"q": row["title"] or name}
    if row["artist"]:
        params["artist"] = row["artist"].split(",")[0].strip()
    if row.get("duration"):
        params["duration"] = str(round(row["duration"]))
    r = await _pick("/api/lyrics/local", params)
    d = r.json() if r.status_code == 200 else {}
    return {"synced": bool(d.get("synced")), "lines": [{"t": (l.get("timeInMs") or 0) / 1000, "text": l.get("text") or ""} for l in (d.get("lines") or []) if isinstance(l, dict)]}


STEM_CACHE = MIX_DIR / "stems"


@gate.get("/g/stem")
def g_stem(request: Request, name: str, stem: str):
    """One track of the song, small enough for the internet: Opus in WebM, made once and kept a while."""
    if not _who(request.cookies, request.query_params.get("k") or "") or not ROOM["key"]:
        return JSONResponse({"error": "Nejsi přihlášený nebo jam neběží."}, status_code=403)
    d = stems_dir(name) if _song_ok(name) else None
    f = stem_file(d, stem) if d is not None and stem.replace("_", "").isalnum() else None
    if f is None:
        return JSONResponse({"error": "Stopa nenalezena."}, status_code=404)
    sig = hashlib.sha1(f"{f}:{f.stat().st_mtime}".encode()).hexdigest()[:16]
    STEM_CACHE.mkdir(parents=True, exist_ok=True)
    out = STEM_CACHE / f"{sig}.webm"
    if not out.is_file():
        part = out.with_suffix(".part.webm")
        r = subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(f), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libopus", "-b:a", "160k",
                            "-f", "webm", str(part)], capture_output=True, timeout=600)
        if r.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)
            return JSONResponse({"error": "Stopu se nepodařilo připravit."}, status_code=500)
        part.replace(out)
        for old in sorted(STEM_CACHE.glob("*.webm"), key=lambda p: p.stat().st_mtime)[:-120]:
            old.unlink(missing_ok=True)
    return FileResponse(out, media_type="audio/webm", headers={"Cache-Control": "private, max-age=86400"})


@gate.get("/g/beats")
def g_beats(request: Request, name: str):
    """The song's beat grid (beats.json beside the stems) for the friend's DJ — read, never computed here."""
    if not _who(request.cookies, request.query_params.get("k") or ""):
        return JSONResponse({"error": "Nejsi přihlášený."}, status_code=403)
    d = stems_dir(name) if _song_ok(name) else None
    try:
        grid = json.loads((d / "beats.json").read_text(encoding="utf-8")) if d is not None else None
    except (OSError, ValueError):
        grid = None
    if not isinstance(grid, dict) or not isinstance(grid.get("beats"), list):
        return JSONResponse({"error": "Beat grid není."}, status_code=404)
    return {k: grid.get(k) for k in ("beats", "downbeats", "bpm", "beats_per_bar", "duration")}


@gate.get("/g/prints")
async def g_prints(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    r = await _pick("/api/tone/prints")
    rows = r.json() if r.status_code == 200 else []
    keep = ("id", "name", "category", "match", "core", "song", "artist", "title", "created", "friend", "stem")
    return {"prints": [{k: x.get(k) for k in keep} for x in rows if isinstance(x, dict) and x.get("review") != "rejected"]}


@gate.get("/g/print")
async def g_print(request: Request, name: str, print: str = ""):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    r = await _pick(f"/api/tone/{_q(name, safe='')}", {"print": print})
    d = r.json() if r.status_code == 200 else {}
    p = d.get("print")
    if not p:
        return JSONResponse({"error": "Otisk nenalezen."}, status_code=404)
    return {"print": {k: p.get(k) for k in ("id", "name", "pre", "core", "room", "drive_db", "gain_db", "out_gain", "width")}}


@gate.get("/g/print-ir")
async def g_print_ir(request: Request, name: str, print: str = ""):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    r = await _pick(f"/api/tone/{_q(name, safe='')}/file/print-ir.wav", {"print": print})
    return Response(r.content, status_code=r.status_code, media_type="audio/wav")


_AMPS: Dict = {"at": 0.0, "rows": []}


@gate.get("/g/amps")
async def g_amps(request: Request, q: str = "", kind: str = "model"):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if time.time() - _AMPS["at"] > 600:
        r = await _pick("/api/amps")
        rows = r.json() if r.status_code == 200 else []
        _AMPS.update(at=time.time(), rows=[{"name": x.get("name"), "file": x.get("file"), "kind": x.get("kind")} for x in rows if isinstance(x, dict)])
    words = [w for w in q.lower().split() if w]
    got = [x for x in _AMPS["rows"] if x["kind"] == kind and all(w in (x["name"] or "").lower() for w in words)]
    return {"amps": got[:150], "total": len(got)}


CATALOG_ARGS = ("kind", "q", "sort", "gain", "type", "brand", "family", "pack", "cab", "speaker", "mic", "size", "offset", "limit")
CATALOG_ROW = ("file", "name", "group", "gain", "type", "brand", "family", "pack", "cab", "speaker", "mic", "size")


@gate.get("/g/amp-catalog")
async def g_amp_catalog(request: Request):
    """NAM profiles and cabinets sorted as in Pick's own picker: make and model, gain, type; pack, speaker, mic."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    params = {k: v[:80] for k, v in request.query_params.items() if k in CATALOG_ARGS}
    params["limit"] = str(min(300, int(params["limit"]) if params.get("limit", "").isdigit() else 150))
    r = await _pick("/api/amp-catalog", params)
    if r.status_code != 200:
        return JSONResponse({"error": "Katalog zesilovačů teď nejde načíst."}, status_code=502)
    d = r.json()
    return {"kind": d.get("kind"), "total": d.get("total"), "all": d.get("all"), "gains": d.get("gains"),
            "facets": d.get("facets") or {}, "items": [{k: x.get(k) for k in CATALOG_ROW} for x in d.get("items") or []]}


@gate.get("/g/amp")
async def g_amp(request: Request, file: str):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    # an amp, a cabinet or a print's core: Pick's own route reads only from its amp folders
    if not file or ".." in file or file.startswith("/"):
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    r = await _pick(f"/api/amps/{_q(file, safe='/:')}")
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type") or "application/octet-stream")


@gate.get("/g/sections")
async def g_sections(request: Request, name: str):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    for stem in ("guitar", "guitar_rhythm", "guitar_solo"):
        r = await _pick(f"/api/tone/{_q(name, safe='')}/sections", {"stem": stem})
        if r.status_code == 200:
            return {"stem": stem, "sections": r.json().get("sections") or []}
    return {"stem": None, "sections": []}


@gate.post("/g/take")
async def g_take(request: Request, name: str, start: float, end: float, gain_db: float = 0.0):
    """The friend's guitar, clean, over a stretch of the song: his own print is made of it on this Mac."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    body = await request.body()
    if len(body) < 1000 or len(body) > 40 * 1024 * 1024 or body[:4] != b"RIFF":
        return JSONResponse({"error": "Nahrávka nepřišla celá."}, status_code=400)
    tmp = Path(tempfile.mkstemp(prefix="pick-jam-take-", suffix=".wav")[1])
    try:
        tmp.write_bytes(body)
        got = await asyncio.to_thread(friend_print, name, start, end, gain_db, tmp, who)
    finally:
        tmp.unlink(missing_ok=True)
    if got.get("error"):
        return JSONResponse({"error": got["error"]}, status_code=got.get("status") or 400)
    return got


@gate.get("/g/job")
async def g_job(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    r = await _pick("/api/tone/job")
    j = (r.json() if r.status_code == 200 else {}).get("job")
    return {"job": j}


@gate.get("/g/playlists")
async def g_playlists(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    r = await _pick("/api/setlists")
    d = r.json() if r.status_code == 200 else []
    rows = d if isinstance(d, list) else (d.get("setlists") or d.get("playlists") or [])
    return {"playlists": all_playlists(rows)}


def all_playlists(rows=None) -> list:
    """The host's own Steems playlists first, then the downloaded ones — each with the songs that have stems."""
    if rows is None:
        try:
            import setlists
            rows = setlists.load()
        except Exception:
            rows = []
    own = [{"name": x.get("name") or "", "songs": [n for n in (x.get("songs") or []) if isinstance(n, str)], "kind": "own"}
           for x in rows if isinstance(x, dict) and x.get("songs")]
    try:
        dl = downloaded_playlists()
    except Exception:
        dl = []
    return own + dl


@gate.get("/g/art")
async def g_art(request: Request, name: str):
    """The song's picture: its own cover, or the album's found online (then the browser goes there itself)."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return Response(status_code=404)
    r = await _pick(f"/api/stems/{_q(name, safe='')}/art")
    url = (r.json() if r.status_code == 200 else {}).get("url")
    if not url:
        return Response(status_code=404)
    if url.startswith("/api/stems/"):
        img = await _pick(f"/api/stems/{_q(name, safe='')}/art/image")
        return Response(img.content, status_code=img.status_code, media_type=img.headers.get("content-type") or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})
    if url.startswith("https://"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=86400"})
    return Response(status_code=404)


@gate.get("/g/preview")
async def g_preview(request: Request, name: str):
    """The whole song in one small file, to listen to before it is picked (seekable)."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    r = await _pick(f"/api/stems/{_q(name, safe='')}/preview")
    if r.status_code == 200 and (r.headers.get("content-type") or "").startswith("audio/"):
        # Pick hands the file itself (a small m4a): kept as it is, served by ranges
        ext = ".m4a" if "mp4" in r.headers.get("content-type", "") or r.content[4:8] == b"ftyp" else ".audio"
        out = STEM_CACHE / f"pv-{hashlib.sha1(name.encode() + str(len(r.content)).encode()).hexdigest()[:16]}{ext}"
        if not out.is_file():
            STEM_CACHE.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.content)
        return range_file(out, request.headers.get("range"))
    try:
        rel = (r.json() if r.status_code == 200 else {}).get("rel")
    except ValueError:
        rel = None
    f = music_file(rel) if rel else None
    if f is None or not f.is_file():
        return JSONResponse({"error": "Náhled není k dispozici."}, status_code=404)
    sig = hashlib.sha1(f"{f}:{f.stat().st_mtime}".encode()).hexdigest()[:16]
    out = STEM_CACHE / f"pv-{sig}.webm"
    if not out.is_file():
        STEM_CACHE.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part.webm")
        got = await asyncio.to_thread(subprocess.run, [ffmpeg(), "-v", "error", "-y", "-i", str(f), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libopus",
                                                         "-b:a", "96k", "-f", "webm", str(part)], capture_output=True, timeout=600)
        if got.returncode != 0 or not part.is_file():
            part.unlink(missing_ok=True)
            return JSONResponse({"error": "Náhled se nepodařilo připravit."}, status_code=500)
        part.replace(out)
    return range_file(out, request.headers.get("range"))


# ---- the song's tabs: the host's own Guitar Pro files linked to it (sent, drawn on the friend's page), and the
# Songsterr ones only as a link — the friend opens them at Songsterr himself; their data is not passed on
TAB_EXTS = {".gp", ".gp3", ".gp4", ".gp5", ".gpx", ".gp7", ".xml", ".musicxml", ".mxl"}
_TABS: Dict = {"at": 0.0, "rows": []}


def _fold(t: str) -> str:
    import unicodedata
    return " ".join("".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if c.isalnum() or c == " ").split())


async def _tabs_of(name: str) -> list:
    if time.time() - _TABS["at"] > 300:
        r = await _pick("/api/tabfiles/list")
        _TABS.update(at=time.time(), rows=(r.json() if r.status_code == 200 else {}).get("files") or [])
    row = next((x for x in _SONGS["rows"] if x["name"] == name), None) or {}
    art, tit = _fold(row.get("artist", "").split(",")[0]), _fold(row.get("title", ""))
    out = []
    for f in _TABS["rows"]:
        if not isinstance(f, dict):
            continue
        hit = name in (f.get("usedWith") or []) or (tit and _fold(f.get("title")) == tit and (not art or _fold(f.get("artist")) == art))
        if not hit:
            continue
        if f.get("ext") == "songsterr" and isinstance(f.get("songsterr"), dict):
            sid = f["songsterr"].get("songId")
            if isinstance(sid, int) and not any(o.get("songsterr") == sid for o in out):
                out.append({"kind": "songsterr", "songsterr": sid, "name": f.get("name") or "", "url": f"https://www.songsterr.com/a/wa/song?id={sid}"})
        elif Path(f.get("path") or "").suffix.lower() in TAB_EXTS:
            out.append({"kind": "file", "path": f["path"], "name": f.get("name") or Path(f["path"]).stem})
    return out


@gate.get("/g/tabs")
async def g_tabs(request: Request, name: str):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name):
        return JSONResponse({"error": "Píseň nenalezena."}, status_code=404)
    if not _SONGS["rows"]:
        await g_songs(request)
    return {"tabs": await _tabs_of(name)}


@gate.get("/g/tabfile")
async def g_tabfile(request: Request, name: str, path: str):
    """A Guitar Pro / MusicXML file of the host's — only one the song is linked to."""
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    if not _song_ok(name) or not any(t["kind"] == "file" and t["path"] == path for t in await _tabs_of(name)):
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    r = await _pick("/api/tabfiles/file", {"path": path})
    return Response(r.content, status_code=r.status_code, media_type="application/octet-stream")


# ---- the host's settings (Nastavení → Jam): names and passwords, the host's own name
@router.get("/api/jam/users")
def jam_users():
    return {"users": sorted(users()), "host_name": host_name()}


@router.post("/api/jam/users")
async def jam_user_set(request: Request):
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    if body.get("host_name") is not None:
        conf_save(host_name=" ".join(str(body["host_name"]).split())[:30])
        if not body.get("name"):
            return {"ok": True, "host_name": host_name(), "users": sorted(users())}
    r = user_set(str(body.get("name") or ""), str(body.get("password") or ""))
    return r if r.get("ok") else JSONResponse(r, status_code=400)


@router.delete("/api/jam/users")
def jam_user_delete(name: str):
    return user_delete(name)



def _yt_of(name: str) -> str:
    import re
    m = re.search(r"\[([A-Za-z0-9_-]{11})\]\s*$", name)
    return f"https://youtu.be/{m.group(1)}" if m else ""


# "Na příště": one playlist of the host's Steems (he sees and edits it in Pick), the friend adds and takes away —
# by changes, so what he did offline goes in when he is back without undoing what the host did meanwhile
FAV_NAME = "Jam — na příště"


def _fav() -> dict:
    import setlists
    c = conf()
    rows = setlists.load()
    p = next((x for x in rows if x["id"] == c.get("fav_id")), None) or next((x for x in rows if x["name"] == FAV_NAME), None)
    if p is None:
        got = setlists.setlist_new(setlists.SetlistIn(name=FAV_NAME, songs=[]))
        p = got if isinstance(got, dict) else next((x for x in setlists.load() if x["name"] == FAV_NAME), {"id": "", "name": FAV_NAME, "songs": []})
    if c.get("fav_id") != p.get("id"):
        conf_save(fav_id=p.get("id"))
    return p


@gate.get("/g/fav")
async def g_fav(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    p = await asyncio.to_thread(_fav)
    return {"name": p["name"], "songs": p.get("songs") or []}


@gate.post("/g/fav")
async def g_fav_change(request: Request):
    who, bad = _guest_or_403(request)
    if bad:
        return bad
    import setlists
    body = await request.json()
    ops = [o for o in ((body or {}).get("ops") or []) if isinstance(o, dict) and o.get("name") and _song_ok(str(o["name"]))][:200]

    def apply():
        p = _fav()
        songs = list(p.get("songs") or [])
        for o in ops:
            n = str(o["name"])
            if o.get("add") and n not in songs:
                songs.append(n)
            elif not o.get("add") and n in songs:
                songs.remove(n)
        setlists.setlist_set(p["id"], setlists.SetlistIn(name=p["name"], songs=songs))
        return songs
    songs = await asyncio.to_thread(apply)
    return {"name": FAV_NAME, "songs": songs}


# the page kept in the friend's browser: when the jam is off (or this Mac is), it still opens with the songs as
# they were last seen, YouTube for listening, and Na příště kept until the next time
SW = (STATIC / "jam-sw.js")


@gate.get("/sw.js")
def guest_sw():
    return Response(SW.read_text(encoding="utf-8"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# ---- the library on the host's PC (deploy/pick-library): the friend's page between jams. This Mac only calls it
# (nothing there can call here): "Na příště" merged both ways, and whether a jam is on, once a minute.
def _lib():
    c = conf()
    url, tok = (c.get("library_url") or "").rstrip("/"), c.get("library_token") or ""
    return (url, tok) if url.startswith("https://") and len(tok) >= 24 else (None, None)


def _lib_call(method: str, path: str, body=None, timeout: float = 15):
    import urllib.request
    url, tok = _lib()
    if not url:
        raise RuntimeError("Knihovna na PC není nastavená.")
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "User-Agent": "Pick"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def lib_sync() -> dict:
    """Na příště: what either side took away since the last time stays away; what either added is there."""
    import setlists
    pc = _lib_call("GET", "/sync/fav").get("songs") or []
    p = _fav()
    mac = list(p.get("songs") or [])
    base = set(conf().get("lib_base") or [])
    gone = (base - set(pc)) | (base - set(mac))
    out = [n for n in mac + [x for x in pc if x not in mac] if n not in gone]
    if out != mac:
        setlists.setlist_set(p["id"], setlists.SetlistIn(name=p["name"], songs=out))
    if out != pc:
        _lib_call("PUT", "/sync/fav", {"songs": out})
    conf_save(lib_base=out)
    return {"songs": out}


def lib_status() -> dict:
    return _lib_call("POST", "/sync/status", {"on": bool(ROOM["key"]), "url": (ROOM["url"] or "").split("?")[0] if ROOM["key"] else ""})


def lib_users() -> None:
    """The friends' accounts made here go to the library too (salt and hash only): one name and password for both."""
    _lib_call("PUT", "/sync/users", {"users": {n: {"salt": r.get("salt"), "hash": r.get("hash")} for n, r in users().items() if isinstance(r, dict)}})


def lib_playlists() -> None:
    """Every playlist (own and downloaded) to the library, so the friend sees them between jams too."""
    pls = [p for p in all_playlists() if p["name"] != FAV_NAME]
    _lib_call("PUT", "/sync/playlists", {"playlists": pls}, timeout=30)


def lib_engine() -> None:
    """The engine's builds (engine/dist, not in the repository) to the library, when they differ, for friends to download."""
    import urllib.request
    have = _lib_call("GET", "/sync/engine")
    url, tok = _lib()
    for n in engine_files():
        data = (ENGINE_DIST / n).read_bytes()
        if have.get(n) != hashlib.sha256(data).hexdigest():
            req = urllib.request.Request(f"{url}/sync/engine/{n}", method="PUT", data=data,
                                         headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/octet-stream", "User-Agent": "Pick"})
            with urllib.request.urlopen(req, timeout=120) as r:
                r.read()


def lib_covers() -> None:
    """The songs' covers to the library on the PC (it cannot look them up itself: no way out of its network):
    the address of each, and the picture itself for those whose cover is in the song's own file."""
    import urllib.request
    covers = song_covers()
    url, tok = _lib()
    have = set(_lib_call("GET", "/sync/art").get("files") or [])
    sent = {}
    for name, c in covers.items():
        if "u" in c:
            sent[name] = {"u": c["u"]}
            continue
        got = cover_bytes(c["d"])
        if not got:
            continue
        data, mime = got
        h = hashlib.sha1(data).hexdigest()[:20]
        if h not in have and len(data) < 3_000_000:
            req = urllib.request.Request(f"{url}/sync/artimg/{h}", method="PUT", data=data,
                                         headers={"Authorization": f"Bearer {tok}", "Content-Type": mime or "image/jpeg", "User-Agent": "Pick"})
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            have.add(h)
        sent[name] = {"f": h}
    _lib_call("PUT", "/sync/art", {"art": sent}, timeout=60)


def lib_pcsongs() -> None:
    """The songs imported from the PC's Music folder (tools/import_folder.py, its newest plan): their names and lengths
    to the library, which plays them from that folder on the PC — only when the list changed."""
    import glob
    plans = sorted(glob.glob(str(Path.home() / ".local/share/spotdl-helper/import/plan-*.json")), key=os.path.getmtime)
    if not plans:
        return
    plan = json.loads(Path(plans[-1]).read_text(encoding="utf-8"))
    src = str(plan.get("source") or "").rstrip("/") + "/"
    songs = [{"rel": x["path"][len(src):], "artist": x.get("artist") or "", "title": x.get("title") or "", "album": x.get("album") or "",
              "duration": x.get("duration")} for x in plan.get("new") or [] if isinstance(x, dict) and str(x.get("path", "")).startswith(src)]
    sig = hashlib.sha1(json.dumps(songs, sort_keys=True).encode()).hexdigest()
    if conf().get("lib_pc_sig") == sig:
        return
    _lib_call("PUT", "/sync/pcsongs", {"songs": songs}, timeout=120)
    conf_save(lib_pc_sig=sig)


def _lib_loop() -> None:
    n = 0
    while True:
        try:
            if _lib()[0]:
                lib_status()
                lib_users()
                lib_sync()
                if n % 10 == 0:                     # every ten minutes is enough for these
                    lib_playlists()
                    for step in (lib_engine, lib_covers, lib_pcsongs):
                        try:
                            step()
                        except Exception:
                            pass
        except Exception:
            pass
        n += 1
        time.sleep(60)


def start_library_sync() -> None:
    threading.Thread(target=_lib_loop, daemon=True, name="jam-library").start()


def _lib_kick(accounts: bool = False) -> None:
    def go():
        if _lib()[0]:
            lib_users() if accounts else lib_status()
    threading.Thread(target=go, daemon=True).start()


@router.get("/api/jam/library")
def jam_library():
    c = conf()
    return {"url": c.get("library_url") or "", "token": bool(c.get("library_token"))}


@router.post("/api/jam/library")
async def jam_library_set(request: Request):
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    url = str(body.get("url") or "").strip().rstrip("/")
    if url and not url.startswith("https://"):
        return JSONResponse({"error": "Adresa musí začínat https://"}, status_code=400)
    tok = str(body.get("token") or "").strip()
    if tok and len(tok) < 24:
        return JSONResponse({"error": "Klíč musí mít aspoň 24 znaků."}, status_code=400)
    conf_save(library_url=url, **({"library_token": tok} if tok else {}))
    return {"ok": True, **jam_library()}


@router.post("/api/jam/library/test")
async def jam_library_test():
    try:
        st = await asyncio.to_thread(lib_status) or {}
        got = await asyncio.to_thread(lib_sync)
        for step in (lib_playlists, lib_pcsongs, lib_engine, lib_covers):
            try:
                await asyncio.to_thread(step)
            except Exception:
                pass                                # an older library (not redeployed yet) has no place for these
        return {"ok": True, "fav": len(got["songs"]), "prepared": st.get("prepared"), "free_gb": st.get("free_gb"), "used_gb": st.get("used_gb")}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Knihovna na PC neodpovídá: {str(e)[:200]}"}, status_code=502)
