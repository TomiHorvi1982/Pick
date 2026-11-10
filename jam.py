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
GATE_PORT = 8766
GUEST_FILES = {"jam-engine.js": "text/javascript", "jam-worklet.js": "text/javascript", "jam-guest.js": "text/javascript", "jam-chat.js": "text/javascript"}
CHAT_KEEP = 300
CHAT_MAX = 2000
MIX_DIR = Path(tempfile.gettempdir()) / "pick-jam-mix"

router = APIRouter()
# set by server.py: the stems folder of a song (or None), a stem's file, ffmpeg
stems_dir: Callable[[str], Optional[Path]] = lambda name: None
stem_file: Callable[[Path, str], Optional[Path]] = lambda d, s: None
ffmpeg: Callable[[], str] = lambda: "ffmpeg"


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
    return subprocess.run([b, *args], capture_output=True, text=True, timeout=timeout)


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


# ---- the gateway: what the friend can reach
gate = FastAPI()


def _key_ok(k: Optional[str]) -> bool:
    return bool(ROOM["key"]) and bool(k) and secrets.compare_digest(str(k), str(ROOM["key"]))


@gate.get("/", response_class=HTMLResponse)
def guest_page(k: str = ""):
    if not _key_ok(k):
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Pick Jam</title><body style='font:16px system-ui;background:#111;color:#ddd;padding:40px'>"
                            "<h2>Pick Jam</h2><p>Tenhle odkaz už neplatí — popros kamaráda o nový.</p>", status_code=403)
    return (STATIC / "jam.html").read_text(encoding="utf-8")


@gate.get("/favicon.ico")
def guest_icon():
    f = STATIC / "pick-favicon.png"
    return Response(f.read_bytes(), media_type="image/png") if f.is_file() else Response(status_code=204)


@gate.get("/static/{name}")
def guest_static(name: str):
    if name not in GUEST_FILES:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return Response((STATIC / name).read_text(encoding="utf-8"), media_type=GUEST_FILES[name], headers={"Cache-Control": "no-cache"})


@gate.get("/mix")
def guest_mix(k: str = "", stems: str = ""):
    """The backing of the song being played, as one file: the tracks asked for mixed (Opus in WebM)."""
    if not _key_ok(k) or not ROOM["song"]:
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
    if ROOM.get(role):                       # one of each: a second tab takes over
        try:
            await ROOM[role].ws.close()
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
                              "read": ROOM["read"], "seen": ROOM["seen"].get(other), "away": bool(ROOM["away"].get(other))}))
    if ROOM.get(other):
        await ROOM[other].send(json.dumps({"type": "peer", "on": True}))
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
    if not _key_ok(k):
        await ws.close(code=4403)
        return
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
    return {"on": bool(ROOM["key"]), "url": ROOM["url"], "guest": bool(ROOM["guest"]), "tailscale": tailscale_state()}


@router.post("/api/jam/start")
def jam_start():
    _gate_start()
    if os.environ.get("PICK_JAM_LOCAL"):          # tests: the gateway on this Mac only, no Tailscale
        ROOM["key"] = ROOM["key"] or secrets.token_urlsafe(9)
        ROOM["url"] = f"http://127.0.0.1:{GATE_PORT}/?k={ROOM['key']}"
        return {"ok": True, "url": ROOM["url"]}
    st = _serve_on()
    if not st.get("ok"):
        return JSONResponse(st, status_code=409)
    if not ROOM["key"]:
        ROOM["key"] = secrets.token_urlsafe(9)
        ROOM["started"] = time.time()
    ROOM["url"] = f"https://{st['dns']}/?k={ROOM['key']}"
    return {"ok": True, "url": ROOM["url"]}


@router.post("/api/jam/stop")
def jam_stop():
    ROOM.update(key=None, url=None, song=None, stems=[])
    _chat_reset()
    _serve_off()
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
