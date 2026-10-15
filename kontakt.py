"""Kontakt — the player's Native Instruments libraries in Pick.

Two ways in, both within the player's own licences:
  sounds    the loops and samples a library ships as plain audio (Riffage – Metal's WAV loops,
            dry and through the amp; Axe Machina's samples…): found, sorted by tempo, key and kind,
            heard, put into Steems as a project. Encrypted samples are never touched — those
            sound only through Kontakt itself.
  Kontakt   the plugin, hosted by kontakt_host.py in a venv of its own (Spotify's pedalboard):
            its own window opens to choose an instrument, Pick keeps that as a sound, and notes
            (the virtual guitar's riff) are played through it into a file.
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

LIBS = Path.home() / "Documents" / "Native Instruments" / "Libraries"
DATA = Path.home() / ".local" / "share" / "spotdl-helper" / "kontakt"
HOST_PY = Path.home() / ".local" / "venvs" / "pick-kontakt" / "bin" / "python"
HOST = Path(__file__).with_name("kontakt_host.py")
# VST3: the AU never lets go of a host like this one (its first instance hangs on closing)
PLUGINS = [Path("/Library/Audio/Plug-Ins/VST3/Kontakt 8.vst3"), Path("/Library/Audio/Plug-Ins/VST3/Kontakt 7.vst3")]
AUDIO = {".wav", ".aif", ".aiff"}

# set by the server
new_project = lambda src, label: None                # noqa: E731 — a Steems project from a file


# ------------------------------------------------------------------ the plain sounds in the libraries
_INDEX = {"at": 0.0, "sig": None, "items": [], "libs": []}
_LOCK = threading.Lock()


def _lib_name(folder: str) -> str:
    """'Impact_Soundworks_Riffage_Metal' → 'Riffage Metal'; 'Axe Machina [Soundiron]' → 'Axe Machina'."""
    n = re.sub(r"\s*\[[^\]]*\]\s*", " ", folder).replace("_", " ")
    n = re.sub(r"^(Impact Soundworks|Heavyocity -)\s*", "", n).strip()
    return n or folder


def describe(rel: str) -> dict:
    """What a file's path says of it: tempo, key, dry (DI) or through the amp, the set it is in."""
    parts = rel.split("/")
    name = parts[-1]
    up = rel.upper()
    bpm = re.search(r"(?<!\d)(\d{2,3})\s?BPM", up) or re.search(r"_(\d{2,3})_\d{2}_\d+\.WAV$", up)
    key = re.search(r"LOOPS_([A-G]#?)(?:/|$)", up) or re.search(r"_([A-G]#?)_\d{2,3}_", up)
    kind = "dry" if "_DRY_" in up or "/DI/" in up or " DI " in up else "amp" if "_AMP_" in up else ""
    loop = "LOOP" in up
    return {"bpm": int(bpm.group(1)) if bpm and 40 <= int(bpm.group(1)) <= 260 else None, "key": key.group(1) if key else None,
            "kind": kind, "loop": loop, "set": " / ".join(p for p in parts[1:-1] if p.lower() not in ("samples", "samples wav"))[:120],
            "name": os.path.splitext(name)[0]}


def _scan() -> dict:
    items, libs = [], []
    if not LIBS.is_dir():
        return {"items": [], "libs": []}
    for lib in sorted(LIBS.iterdir(), key=lambda p: p.name.lower()):
        if not lib.is_dir() or lib.name.startswith("."):
            continue
        n = 0
        for root, dirs, files in os.walk(lib):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in AUDIO and not f.startswith("."):
                    rel = os.path.relpath(os.path.join(root, f), LIBS)
                    items.append({"path": rel, "lib": _lib_name(lib.name), **describe(rel)})
                    n += 1
        libs.append({"name": _lib_name(lib.name), "folder": lib.name, "sounds": n,
                     "kontakt": any(lib.glob("*.nicnt"))})
    return {"items": items, "libs": libs}


def index(fresh: bool = False) -> dict:
    with _LOCK:
        try:
            sig = LIBS.stat().st_mtime
        except OSError:
            sig = None
        if fresh or sig != _INDEX["sig"] or not _INDEX["at"]:
            got = _scan()
            _INDEX.update(got, sig=sig, at=time.time())
        return _INDEX


def _under_libs(rel: str):
    try:
        p = (LIBS / rel).resolve()
    except (OSError, ValueError):
        return None
    root = LIBS.resolve()
    return p if root in p.parents and p.is_file() and p.suffix.lower() in AUDIO else None


@router.get("/api/kontakt/sounds")
def sounds(q: str = "", lib: str = "", bpm: int = 0, key: str = "", kind: str = "", loops: bool = False, page: int = 1):
    """The plain sounds, filtered: words in the path, a library, tempo, key, dry or amp, loops only."""
    ix = index()
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    out = [x for x in ix["items"] if (not lib or x["lib"] == lib) and (not bpm or x["bpm"] == bpm) and (not key or x["key"] == key)
           and (not kind or x["kind"] == kind) and (not loops or x["loop"]) and all(w in x["path"].lower() for w in words)]
    per = 120
    facets = {"bpm": sorted({x["bpm"] for x in ix["items"] if x["bpm"] and (not lib or x["lib"] == lib)}),
              "key": sorted({x["key"] for x in ix["items"] if x["key"] and (not lib or x["lib"] == lib)})}
    return {"total": len(out), "items": out[(page - 1) * per: page * per], "libs": ix["libs"], "facets": facets, "folder": str(LIBS)}


@router.get("/api/kontakt/file")
def sound_file(path: str):
    f = _under_libs(path)
    if not f:
        return JSONResponse({"error": "Zvuk nenalezen."}, status_code=404)
    return FileResponse(f, headers={"Cache-Control": "max-age=3600"})


class PathIn(BaseModel):
    path: str = ""


@router.post("/api/kontakt/to-steems")
def to_steems(req: PathIn):
    """A sound as a Steems project of its own — to play with, loop, and put the live guitar over."""
    f = _under_libs(req.path)
    if not f:
        return JSONResponse({"error": "Zvuk nenalezen."}, status_code=404)
    d = describe(req.path)
    label = " ".join(x for x in (_lib_name(req.path.split("/")[0]), d["name"].replace("_", " "),
                                 f"{d['bpm']} BPM" if d["bpm"] else "", d["key"] or "") if x)
    return new_project(f, label)


# ------------------------------------------------------------------ Kontakt itself (kontakt_host.py)
def plugin():
    return next((p for p in PLUGINS if p.exists()), None)


def presets() -> list:
    out = []
    for f in sorted(DATA.glob("*.json")) if DATA.is_dir() else []:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (DATA / f"{f.stem}.state").is_file():
            out.append({"id": f.stem, "name": m.get("name") or f.stem, "plugin": m.get("plugin"), "made": m.get("made"),
                        "shift": int(m.get("shift") or 0)})
    return sorted(out, key=lambda x: -(x.get("made") or 0))


JOB: dict = {"status": None, "what": "", "note": "", "id": "", "at": 0.0}


def _host(args: list, timeout: float):
    return subprocess.run([str(HOST_PY), str(HOST), *args], capture_output=True, text=True, timeout=timeout)


def _editor(pid: str, name: str, plug: Path) -> None:
    try:
        p = _host(["editor", str(plug), str(DATA / f"{pid}.state")], 4 * 3600)
        if p.returncode == 0 and (DATA / f"{pid}.state").is_file():
            meta = DATA / f"{pid}.json"
            old = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
            meta.write_text(json.dumps({**old, "name": name, "plugin": str(plug), "made": old.get("made") or time.time(), "changed": time.time()},
                                       ensure_ascii=False), encoding="utf-8")
            JOB.update(status="done", note=f"Zvuk „{name}“ je uložený.", at=time.time())
        else:
            JOB.update(status="error", note=((p.stderr or "").strip().splitlines() or ["Kontakt se zavřel bez uložení."])[-1][:240], at=time.time())
    except subprocess.TimeoutExpired:
        JOB.update(status="error", note="Okno Kontaktu bylo otevřené přes 4 hodiny — zavřel jsem ho.", at=time.time())


@router.get("/api/kontakt")
def kontakt_state():
    plug = plugin()
    return {"plugin": plug.name if plug else None, "host": HOST_PY.is_file(), "presets": presets(), "job": JOB, "libs": index()["libs"]}


class PresetIn(BaseModel):
    name: str = ""
    id: str = ""


@router.post("/api/kontakt/presets")
def preset_edit(req: PresetIn):
    """Kontakt's own window: choose (or change) the instrument; closing it keeps it as a sound of Pick's."""
    plug = plugin()
    if not plug or not HOST_PY.is_file():
        return JSONResponse({"error": "Kontakt nebo jeho hostitel v Picku nejsou nainstalované."}, status_code=503)
    if JOB["status"] == "running":
        return JSONResponse({"error": "Okno Kontaktu už je otevřené — nejdřív ho zavři."}, status_code=409)
    name = " ".join(req.name.split())[:80] or "Zvuk Kontaktu"
    pid = req.id if req.id and re.fullmatch(r"[0-9a-f]{10}", req.id) else uuid.uuid4().hex[:10]
    DATA.mkdir(parents=True, exist_ok=True)
    JOB.update(status="running", what="editor", id=pid, note="Okno Kontaktu je otevřené — vyber nástroj a okno zavři.", at=time.time())
    threading.Thread(target=_editor, args=(pid, name, plug), daemon=True).start()
    return {"id": pid}


class ShiftIn(BaseModel):
    shift: int = 0


@router.post("/api/kontakt/presets/{pid}/shift")
def preset_shift(pid: str, req: ShiftIn):
    """Octaves up or down: some instruments keep the low keys for switches, not notes (Electric Storm)."""
    meta = DATA / f"{pid}.json"
    if not re.fullmatch(r"[0-9a-f]{10}", pid) or not meta.is_file():
        return JSONResponse({"error": "Takový zvuk Kontaktu není."}, status_code=404)
    if req.shift not in (-24, -12, 0, 12, 24):
        return JSONResponse({"error": "Posun jen po celých oktávách (−2 až +2)."}, status_code=400)
    m = json.loads(meta.read_text(encoding="utf-8"))
    meta.write_text(json.dumps({**m, "shift": req.shift}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "shift": req.shift}


@router.delete("/api/kontakt/presets/{pid}")
def preset_delete(pid: str):
    if not re.fullmatch(r"[0-9a-f]{10}", pid):
        return JSONResponse({"error": "Neplatné id."}, status_code=400)
    for ext in (".json", ".state"):
        (DATA / f"{pid}{ext}").unlink(missing_ok=True)
    for f in DATA.glob(f"render-{pid}-*.wav"):
        f.unlink(missing_ok=True)
    return {"ok": True}


class RenderIn(BaseModel):
    preset: str = ""
    notes: list = []                                  # [{m, t, d, v}] — MIDI note, start s, length s, velocity 1–127
    length: float = 0


_RENDER_LOCK = threading.Lock()


@router.post("/api/kontakt/render")
def render(req: RenderIn):
    """Notes played through a kept Kontakt sound, into a WAV (the same notes again: the file kept)."""
    if not re.fullmatch(r"[0-9a-f]{10}", req.preset or "") or not (DATA / f"{req.preset}.state").is_file():
        return JSONResponse({"error": "Takový zvuk Kontaktu není."}, status_code=404)
    meta = json.loads((DATA / f"{req.preset}.json").read_text(encoding="utf-8"))
    shift = int(meta.get("shift") or 0)
    notes = []
    for n in req.notes[:4000]:
        try:
            m, t, d, v = int(n["m"]), float(n["t"]), float(n["d"]), int(n.get("v") or 100)
        except (KeyError, TypeError, ValueError):
            continue
        m += shift
        if 0 <= m <= 127 and 0 <= t < 600 and 0 < d < 30:
            notes.append([m, round(t, 4), round(d, 4), max(1, min(127, v))])
    if not notes:
        return JSONResponse({"error": "Žádné noty."}, status_code=400)
    length = max(req.length or 0, max(t + d for _, t, d, _ in notes)) + 1.5
    import hashlib
    key = hashlib.sha1(json.dumps([notes, round(length, 2), meta.get("changed")]).encode()).hexdigest()[:16]
    out = DATA / f"render-{req.preset}-{key}.wav"
    if out.is_file():
        return {"file": out.name, "cached": True}
    if not _RENDER_LOCK.acquire(timeout=300):
        return JSONResponse({"error": "Kontakt teď hraje něco jiného — zkus to za chvilku."}, status_code=409)
    try:
        spec = DATA / f"render-{key}.json"
        spec.write_text(json.dumps({"notes": notes, "length": length}), encoding="utf-8")
        p = _host(["render", meta.get("plugin") or str(plugin()), str(DATA / f"{req.preset}.state"), str(spec), str(out)], 600)
        spec.unlink(missing_ok=True)
        if p.returncode != 0 or not out.is_file():
            return JSONResponse({"error": ((p.stderr or "").strip().splitlines() or ["Kontakt nic nezahrál."])[-1][:240]}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Kontakt hrál přes 10 minut — vzdal jsem to."}, status_code=500)
    finally:
        _RENDER_LOCK.release()
    for old in sorted(DATA.glob(f"render-{req.preset}-*.wav"), key=lambda f: f.stat().st_mtime)[:-20]:
        old.unlink(missing_ok=True)                  # the last twenty riffs kept
    return {"file": out.name, "cached": False}


@router.get("/api/kontakt/render/{name}")
def render_file(name: str):
    f = DATA / name
    if not re.fullmatch(r"render-[0-9a-f]{10}-[0-9a-f]{16}\.wav", name) or not f.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="audio/wav")


def wav_seconds(f: Path) -> float:
    try:
        with wave.open(str(f)) as w:
            return w.getnframes() / float(w.getframerate())
    except (OSError, wave.Error, EOFError):
        return 0.0
