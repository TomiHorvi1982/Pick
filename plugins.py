"""Pluginy — the player's own audio plugins (AU / VST3: Neural DSP and the rest) in Pick.

· Skenovat: every plugin in the system's plug-in folders is loaded once, in a process of its own
  (plugin_host.py probe), and what loads is listed (name, format, effect or instrument). A VST3 and
  an AU of the same plugin are one entry (the VST3 is used).
· A chain of plugins per place: the live guitar ("live"), or one track of a song in Steems
  ("stem:<song>|<track>"). Each plugin in it keeps its own settings in a state file, set in the
  plugin's own window (plugin_host.py editor).
· The live guitar: the chain runs in a host process of its own, from the interface's input (the
  guitar's channel) into BlackHole; Pick takes it from there through its tunnel — fader, EQ, the
  recording and AUTO MIX work on it as on any live guitar.
· A track: rendered through its chain into a new track of the song (the original stays).

Nothing of a plugin is copied or changed: it is loaded where it is installed, as any host does.
"""
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

APP_DIR = Path(__file__).parent
HOST = APP_DIR / "plugin_host.py"
HOST_PY = Path.home() / ".local" / "venvs" / "pick-kontakt" / "bin" / "python"
DATA = Path.home() / ".local" / "share" / "spotdl-helper" / "plugins"
FOLDERS = [Path("/Library/Audio/Plug-Ins/VST3"), Path("/Library/Audio/Plug-Ins/Components"),
           Path.home() / "Library/Audio/Plug-Ins/VST3", Path.home() / "Library/Audio/Plug-Ins/Components"]
SKIP = ("kontakt",)                         # Kontakt has a place of its own (kontakt.py)

# set by the server
stems_dir = lambda song: None               # noqa: E731  song name → its stems folder, or None
stem_file = lambda d, stem: None            # noqa: E731  (folder, track) → its audio file, or None
add_track = lambda d, src, label: None      # noqa: E731  a file becomes a new track of the song
tunnel_ok = lambda: True                    # noqa: E731

SCAN = {"running": False, "done": 0, "total": 0, "current": ""}
JOB = {"status": None, "what": "", "error": "", "result": None, "at": 0.0}
LIVE = {"proc": None, "input": "", "channel": "1", "output": "", "error": "", "edit": None, "stereo": True}
_lock = threading.Lock()


def _catalog_file() -> Path:
    return DATA / "catalog.json"


def _chains_file() -> Path:
    return DATA / "chains.json"


def _read(path: Path, empty):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _host(*args, timeout=120):
    """One run of the host; its last JSON line."""
    r = subprocess.run([str(HOST_PY), str(HOST), *args], capture_output=True, text=True, timeout=timeout)
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if not lines:
        return {"ok": False, "error": (r.stderr.strip().splitlines() or ["host neodpověděl"])[-1][:200]}
    return json.loads(lines[-1])


def candidates() -> list:
    """Plugin files on the disk: one per name, the VST3 first."""
    seen, out = {}, []
    for folder in FOLDERS:
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in (".vst3", ".component") or any(s in f.name.lower() for s in SKIP):
                continue
            key = f.stem.lower()
            if key in seen:
                continue
            seen[key] = True
            out.append(f)
    out.sort(key=lambda f: (f.suffix.lower() != ".vst3", f.stem.lower()))
    return out


def catalog() -> list:
    return _read(_catalog_file(), [])


def _scan():
    files = candidates()
    SCAN.update(running=True, done=0, total=len(files), current="")
    old = {c["path"]: c for c in catalog()}
    out = []
    for f in files:
        SCAN["current"] = f.stem
        st = f.stat()
        sig = f"{int(st.st_mtime)}"
        prev = old.get(str(f))
        # the same file as last time: what was found then holds — loaded or not (one that failed would take up to
        # 90 s again at every scan); a plugin updated or reinstalled has a new date and is tried again
        if prev and prev.get("sig") == sig:
            out.append(prev)
        else:
            try:
                got = _host("probe", str(f), timeout=90)
            except subprocess.TimeoutExpired:
                got = {"ok": False, "error": "nenačetl se do 90 s"}
            out.append({"id": uuid.uuid5(uuid.NAMESPACE_URL, str(f)).hex[:12], "path": str(f), "file": f.name,
                        "format": "VST3" if f.suffix.lower() == ".vst3" else "AU", "sig": sig,
                        "name": got.get("name") or f.stem, "ok": bool(got.get("ok")), "error": got.get("error", ""),
                        "instrument": bool(got.get("instrument")), "params": got.get("params", 0),
                        "vendor": "Neural DSP" if any(w in f.stem.lower() for w in
                                  ("archetype", "neural", "parallax", "fortin", "soldano", "mesa boogie", "quad cortex"))
                                  else ""})
        SCAN["done"] += 1
        _write(_catalog_file(), out)                        # what is found so far shows while the rest is scanned
    SCAN.update(running=False, current="")


def plugin_by_id(pid: str) -> Optional[dict]:
    return next((c for c in catalog() if c["id"] == pid), None)


# ---------------------------------------------------------------- chains
def chains() -> dict:
    return _read(_chains_file(), {})


def chain_of(key: str) -> list:
    return chains().get(key, [])


def _state_path(entry: dict) -> str:
    if not entry.get("state"):
        entry["state"] = str(DATA / "states" / f"{uuid.uuid4().hex}.state")
    Path(entry["state"]).parent.mkdir(parents=True, exist_ok=True)
    return entry["state"]


def save_chain(key: str, items: list) -> list:
    all_ = chains()
    old = {c.get("uid"): c for c in all_.get(key, [])}
    out = []
    for it in items[:8]:
        p = plugin_by_id(it.get("id", ""))
        if not p or not p.get("ok") or p.get("instrument"):
            continue
        prev = old.get(it.get("uid"))
        e = {"uid": it.get("uid") or uuid.uuid4().hex[:10], "id": p["id"], "name": p["name"], "path": p["path"],
             "bypass": bool(it.get("bypass")), "state": prev.get("state") if prev else None}
        _state_path(e)
        out.append(e)
    for uid, c in old.items():                              # settings of a plugin taken out go with it
        if uid not in {e["uid"] for e in out} and c.get("state"):
            Path(c["state"]).unlink(missing_ok=True)
    if out:
        all_[key] = out
    else:
        all_.pop(key, None)
    _write(_chains_file(), all_)
    return out


def _chain_file(items: list) -> str:
    f = DATA / "run" / f"{uuid.uuid4().hex}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([{"path": c["path"], "state": c.get("state"), "bypass": c.get("bypass", False)} for c in items]))
    return str(f)


# ---------------------------------------------------------------- the live guitar
def live_view() -> dict:
    p = LIVE["proc"]
    running = bool(p and p.poll() is None)
    return {"running": running, "input": LIVE["input"], "channel": LIVE["channel"], "output": LIVE["output"],
            "error": LIVE["error"] if not running else "", "editing": LIVE["edit"] if running else None, "stereo": LIVE["stereo"]}


def live_stop() -> None:
    p = LIVE["proc"]
    LIVE["proc"] = None
    if p and p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def live_start(inp: str, channel: str, outp: str, edit: Optional[int] = None, stereo: Optional[bool] = None) -> dict:
    items = chain_of("live")
    if not items:
        return {"error": "Živá kytara zatím nemá žádný plugin."}
    live_stop()
    stereo = LIVE.get("stereo", True) if stereo is None else bool(stereo)
    LIVE.update(input=inp, channel=channel, output=outp, error="", edit=edit, stereo=stereo)
    args = [str(HOST_PY), "-u", str(HOST), "live", _chain_file(items), inp, channel + ("s" if stereo else ""), outp] + ([str(edit)] if edit is not None else [])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    LIVE["proc"] = proc
    end = time.time() + 60                                  # loading a Neural DSP plugin takes a few seconds
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        if line.startswith("{"):
            got = json.loads(line)
            if got.get("live"):
                threading.Thread(target=_drain, args=(proc,), daemon=True).start()
                return live_view()
            if not got.get("ok"):
                LIVE["error"] = got.get("error", "")
                break
    live_stop()
    return {"error": LIVE["error"] or "Plugin se živě nespustil."}


def _drain(proc):
    for line in proc.stdout:
        if line.startswith("{") and '"kept"' in line:
            LIVE["edit"] = None


# ---------------------------------------------------------------- the API
class ChainBody(BaseModel):
    key: str
    items: List[dict]


class EditorBody(BaseModel):
    key: str
    index: int


class LiveBody(BaseModel):
    on: bool
    input: str = ""
    channel: str = "1"
    output: str = "BlackHole 2ch"
    stereo: bool = True          # the guitar on both sides into the plugins: their doubler, stereo cabinets


class RenderBody(BaseModel):
    song: str
    stem: str


def _key_ok(key: str) -> bool:
    return key == "live" or (key.startswith("stem:") and "|" in key and len(key) < 400)


@router.get("/api/plugins")
def plugins_list():
    return {"items": catalog(), "scan": dict(SCAN), "live": live_view(), "job": dict(JOB), "host": HOST_PY.is_file()}


@router.post("/api/plugins/scan")
def plugins_scan():
    if not HOST_PY.is_file():
        return JSONResponse({"error": "Chybí prostředí pro pluginy (~/.local/venvs/pick-kontakt)."}, status_code=503)
    with _lock:
        if not SCAN["running"]:
            SCAN["running"] = True
            threading.Thread(target=_scan, daemon=True).start()
    return dict(SCAN)


@router.get("/api/plugins/devices")
def plugins_devices():
    try:
        return _host("devices", timeout=30)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Zvuková zařízení se nepodařilo zjistit."}, status_code=500)


@router.get("/api/plugins/chain")
def plugins_chain(key: str):
    if not _key_ok(key):
        return JSONResponse({"error": "Neznámé místo."}, status_code=400)
    return {"key": key, "items": [{k: v for k, v in c.items() if k != "state"} for c in chain_of(key)]}


@router.put("/api/plugins/chain")
def plugins_chain_set(body: ChainBody):
    if not _key_ok(body.key):
        return JSONResponse({"error": "Neznámé místo."}, status_code=400)
    items = save_chain(body.key, body.items)
    if body.key == "live" and live_view()["running"]:
        live_start(LIVE["input"], LIVE["channel"], LIVE["output"])     # the new chain sounds at once
    return {"key": body.key, "items": [{k: v for k, v in c.items() if k != "state"} for c in items]}


@router.post("/api/plugins/editor")
def plugins_editor(body: EditorBody):
    items = chain_of(body.key)
    if not 0 <= body.index < len(items):
        return JSONResponse({"error": "Takový plugin v řetězu není."}, status_code=404)
    if body.key == "live" and live_view()["running"]:
        # the window of the plugin that is sounding: the live host opens it, the guitar goes on through it
        active = [c for c in items if not c.get("bypass")]
        if items[body.index] not in active:
            return JSONResponse({"error": "Plugin je vypnutý (obejitý) — zapni ho, ať ho slyšíš."}, status_code=400)
        got = live_start(LIVE["input"], LIVE["channel"], LIVE["output"], edit=active.index(items[body.index]))
        return got if got.get("running") or not got.get("error") else JSONResponse(got, status_code=500)
    e = items[body.index]
    subprocess.Popen([str(HOST_PY), str(HOST), "editor", e["path"], e["state"]],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "name": e["name"]}


@router.post("/api/plugins/live")
def plugins_live(body: LiveBody):
    if not body.on:
        live_stop()
        return live_view()
    if not body.input:
        return JSONResponse({"error": "Vyber vstup zvukovky, na kterém je kytara."}, status_code=400)
    got = live_start(body.input, "2" if body.channel == "2" else "1", body.output or "BlackHole 2ch", stereo=body.stereo)
    return got if got.get("running") or not got.get("error") else JSONResponse(got, status_code=500)


def _render(body: RenderBody, d: Path, src: Path, items: list):
    JOB.update(status="running", what=f"{body.stem} → {' + '.join(c['name'] for c in items)}", error="", result=None, at=time.time())
    out = DATA / "run" / f"{uuid.uuid4().hex}.wav"
    try:
        got = _host("render", _chain_file(items), str(src), str(out), timeout=1800)
        if not got.get("ok"):
            raise RuntimeError(got.get("error") or "Plugin stopu nezpracoval.")
        label = f"{body.stem} · {' + '.join(c['name'] for c in items if not c.get('bypass'))}"[:80]
        res = add_track(d, out, label)
        if isinstance(res, JSONResponse):
            raise RuntimeError("Novou stopu se nepodařilo uložit.")
        JOB.update(status="done", result=res)
    except Exception as e:                                  # noqa: BLE001
        JOB.update(status="error", error=str(e))
    finally:
        out.unlink(missing_ok=True)


@router.post("/api/plugins/render")
def plugins_render(body: RenderBody):
    d = stems_dir(body.song)
    if d is None:
        return JSONResponse({"error": "Píseň ve Steems není."}, status_code=404)
    src = stem_file(d, body.stem)
    if src is None:
        return JSONResponse({"error": "Taková stopa tu není."}, status_code=404)
    items = [c for c in chain_of(f"stem:{body.song}|{body.stem}") if not c.get("bypass")]
    if not items:
        return JSONResponse({"error": "Stopa nemá žádný zapnutý plugin."}, status_code=400)
    if JOB["status"] == "running":
        return JSONResponse({"error": "Jedna stopa se už pluginem prohání — počkej, až doběhne."}, status_code=409)
    threading.Thread(target=_render, args=(body, d, src, items), daemon=True).start()
    JOB.update(status="running")
    return dict(JOB)


@router.get("/api/plugins/job")
def plugins_job():
    return dict(JOB)
