"""Úložiště — the cards Pick's music lives on: how full each is and what fills it, a new card made
ready (formatted for the Mac, given a Pick folder), and which card the new songs go to.

The main card (the music root) is where everything is found. Another card ('a card in use') keeps
song folders of its own in <card>/spotdl/Stems; each is linked into the main card's Stems/ (a
symlink), so the whole app reads it as if it were there. A card that is out leaves its links
dangling: its songs are simply not listed until it is back. New separated songs go to the card
chosen here — the folder is made there, the link on the main card.

Formatting erases a whole card. Only a removable card (the SD slot, USB) can be chosen, never the
Mac's own disk nor a card Pick is using, and only with the word SMAZAT typed in.
"""
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

# set by the server
main = lambda: Path(".")                             # noqa: E731 — the music root (main card's Pick folder)
config = lambda: {}                                  # noqa: E731 — config["storage"]
set_config = lambda cfg: None                        # noqa: E731
DISKUTIL = "/usr/sbin/diskutil"
FREE_KEEP = 2 * 1024 ** 3                            # a card with less than this free takes no new song

AUDIO = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".aif", ".aiff", ".webm"}
VIDEO = {".mp4", ".m4v", ".mov", ".webm"}
NAMES = {"Stems": "Steems (rozdělené písně)", "Playlists": "Playlisty (stažené písně)", "Songs": "Skladatel (tvoje songy)",
         "Loops": "Smyčky", "Vizualizace": "Vizualizace (videa)"}


# ------------------------------------------------------------------ the cards in use
def cards() -> list:
    """The other cards' Pick folders, as they were added (mounted or not)."""
    return [Path(p) for p in (config().get("cards") or []) if p]


def save_to() -> Path:
    return Path(config().get("save_to") or main())


def _on(p: Path) -> bool:
    return p.is_dir()


def under_card(p: Path) -> bool:
    """A path on one of the other cards' Pick folders — the main card's links lead there."""
    for c in cards():
        try:
            r = c.resolve()
        except OSError:
            continue
        if p == r or r in p.parents:
            return True
    return False


def link_sync() -> dict:
    """Every song folder on a card that is in gets its link on the main card; a link whose song
    is gone from a card that is in goes. Nothing is done about a card that is out."""
    stems = main() / "Stems"
    made = gone = 0
    if not stems.is_dir():
        return {"made": 0, "gone": 0}
    for c in cards():
        cs = c / "Stems"
        if not cs.is_dir():
            continue
        for d in cs.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            link = stems / d.name
            if not link.exists() and not link.is_symlink():
                try:
                    link.symlink_to(d)
                    made += 1
                except OSError:
                    pass
    for link in stems.iterdir():
        if link.is_symlink():
            target = Path(os.readlink(link))
            card = next((c for c in cards() if c in target.parents), None)
            if card is not None and (card / "Stems").is_dir() and not target.exists():
                link.unlink()
                gone += 1
    return {"made": made, "gone": gone}


def place_song(dest: Path) -> Path:
    """Where a new song's folder really goes: the card chosen for new songs, when it is in and has
    room — then the main card gets a link to it. Otherwise the main card itself."""
    to = save_to()
    try:
        if to.resolve() == main().resolve() or not _on(to) or shutil.disk_usage(to).free < FREE_KEEP:
            return dest
    except OSError:
        return dest
    real = to / "Stems" / dest.name
    real.parent.mkdir(parents=True, exist_ok=True)
    return real


def link_song(dest: Path, real: Path) -> None:
    """The main card's link to a song that went onto another card."""
    if real == dest:
        return
    if dest.is_symlink():
        dest.unlink()
    dest.symlink_to(real)


# ------------------------------------------------------------------ the volumes, the disks
def _plist(*args) -> dict:
    try:
        out = subprocess.run([DISKUTIL, args[0], "-plist", *args[1:]], capture_output=True, timeout=20).stdout
        return plistlib.loads(out) if out else {}
    except (OSError, subprocess.TimeoutExpired, plistlib.InvalidFileException, ValueError):
        return {}


def _info(what: str) -> dict:
    return _plist("info", what)


def _physical(info: dict) -> str:
    """The card itself (disk4) under a volume (disk5s1, an APFS volume on a store disk4s2)."""
    stores = info.get("APFSPhysicalStores") or []
    dev = stores[0].get("APFSPhysicalStore") if stores else info.get("ParentWholeDisk") or info.get("DeviceIdentifier") or ""
    return re.sub(r"s\d+$", "", dev or "")


def _boot_disk() -> str:
    return _physical(_info("/"))


MAC_FS = {"apfs", "hfs"}
BUSES = ("Secure Digital", "USB", "Thunderbolt")


def volumes() -> list:
    """The cards and drives that are in, besides the Mac's own disk."""
    boot, out = _boot_disk(), []
    for m in sorted(Path("/Volumes").iterdir()) if Path("/Volumes").is_dir() else []:
        if m.is_symlink() or not m.is_dir():
            continue
        info = _info(str(m))
        disk = _physical(info)
        if not info or disk == boot or info.get("MountPoint") != str(m):
            continue
        if not (info.get("RemovableMedia") or info.get("Ejectable")) or info.get("BusProtocol") not in BUSES:
            continue                                 # a disk image, a network share: not a card
        try:
            du = shutil.disk_usage(m)
        except OSError:
            continue
        fs = (info.get("FilesystemType") or "").lower()
        out.append({"mount": str(m), "name": info.get("VolumeName") or m.name, "disk": disk, "fs": info.get("FilesystemName") or fs,
                    "mac": fs in MAC_FS, "writable": bool(info.get("WritableVolume")), "bus": info.get("BusProtocol") or "",
                    "total": du.total, "used": du.used, "free": du.free})
    return out


def disks() -> list:
    """Removable cards that could be formatted: [{disk, size, media, bus, volumes}] — never the boot disk."""
    boot, vols, out = _boot_disk(), volumes(), []
    for d in _plist("list", "physical").get("WholeDisks") or []:
        if d == boot:
            continue
        info = _info(d)
        if not info.get("RemovableMedia") or not info.get("Ejectable") or info.get("BusProtocol") not in BUSES:
            continue
        out.append({"disk": d, "size": info.get("Size") or 0, "media": info.get("MediaName") or "", "bus": info.get("BusProtocol"),
                    "volumes": [v["name"] for v in vols if v["disk"] == d]})
    return out


def _holds_pick(disk: str) -> bool:
    for v in volumes():
        if v["disk"] != disk:
            continue
        m = Path(v["mount"])
        for p in [main(), *cards()]:
            if p == m or m in p.parents:
                return True
    return False


# ------------------------------------------------------------------ what fills a card
COUNT: dict = {"at": 0.0, "running": False, "cards": {}}
_COUNT_LOCK = threading.Lock()


def _walk(p: Path, seen: set = None) -> tuple:
    """(bytes, files, audio files, videos) under p — links not followed; the room a file really
    takes on the card, and a file kept twice (a hard link) counted once."""
    seen = set() if seen is None else seen
    size = files = audio = video = 0
    stack = [p]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir():
                        stack.append(e.path)
                    elif e.is_file():
                        st = e.stat()
                        if st.st_nlink > 1:
                            if st.st_ino in seen:
                                continue
                            seen.add(st.st_ino)
                        size += st.st_blocks * 512
                        files += 1
                        ext = os.path.splitext(e.name)[1].lower()
                        audio += ext in AUDIO
                        video += ext in VIDEO
                except OSError:
                    pass
    return size, files, audio, video


def count_pick(root: Path) -> list:
    """What Pick keeps on a card, by kind: [{key, name, bytes, items, unit}]."""
    rows, seen = [], set()
    if not root.is_dir():
        return rows
    for e in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if e.is_symlink():
            continue
        if e.is_dir():
            size, files, audio, video = _walk(e, seen)
            if e.name == "Stems":
                items = sum(1 for d in e.iterdir() if d.is_dir() and not d.is_symlink() and not d.name.startswith("."))
                unit = "písní"
            elif e.name == "Vizualizace":
                items, unit = video, "videí"
            elif e.name.startswith("."):
                continue
            else:
                items, unit = audio or files, "skladeb" if audio else "souborů"
            rows.append({"key": e.name, "name": NAMES.get(e.name, e.name), "bytes": size, "items": items, "unit": unit})
        elif e.is_file() and not e.name.startswith("."):
            rows.append({"key": e.name, "name": e.name, "bytes": e.stat().st_blocks * 512, "items": 1, "unit": "soubor"})
    rows.sort(key=lambda r: -r["bytes"])
    return rows


def recount() -> None:
    with _COUNT_LOCK:
        if COUNT["running"]:
            return
        COUNT["running"] = True

    def run():
        try:
            got = {}
            for r in [main(), *cards()]:
                if r.is_dir():
                    got[str(r)] = count_pick(r)
            COUNT.update(cards=got, at=time.time())
        finally:
            COUNT["running"] = False
    threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ formatting
JOB: dict = {"status": None, "disk": "", "name": "", "note": "", "at": 0.0}


def _format(disk: str, name: str) -> None:
    try:
        p = subprocess.run([DISKUTIL, "eraseDisk", "APFS", name, "GPT", disk], capture_output=True, text=True, timeout=900)
        ok = p.returncode == 0
        JOB.update(status="done" if ok else "error", at=time.time(),
                   note=f"Karta je připravená jako {name}." if ok else ((p.stderr or p.stdout or "").strip().splitlines() or ["Formátování se nepovedlo."])[-1][:240])
    except (OSError, subprocess.TimeoutExpired) as e:
        JOB.update(status="error", note=str(e)[:240], at=time.time())


# ------------------------------------------------------------------ the API
def _mb(v: dict) -> dict:
    pick = None
    m = Path(v["mount"])
    for p in [main(), *cards()]:
        if p == m or m in p.parents:
            pick = str(p)
    rows = COUNT["cards"].get(pick) if pick else None
    return {**v, "pick": pick, "main": bool(pick) and Path(pick) == main(), "save": bool(pick) and Path(pick) == save_to(),
            "parts": rows, "pick_bytes": sum(r["bytes"] for r in rows) if rows else None}


@router.get("/api/storage")
def storage():
    if not COUNT["running"] and time.time() - COUNT["at"] > 600:
        recount()
    try:
        link_sync()
    except OSError:
        pass
    vols = [_mb(v) for v in volumes()]
    missing = [str(c) for c in cards() if not c.is_dir()]
    return {"main": str(main()), "save_to": str(save_to()), "volumes": vols, "missing": missing,
            "counting": COUNT["running"], "counted": COUNT["at"], "disks": disks(), "job": JOB}


@router.post("/api/storage/recount")
def storage_recount():
    recount()
    return {"ok": True}


class MountIn(BaseModel):
    mount: str = ""


@router.post("/api/storage/use")
def storage_use(req: MountIn):
    """A card that is in becomes one Pick keeps songs on: its own Pick folder, its songs linked in."""
    v = next((x for x in volumes() if x["mount"] == req.mount), None)
    if not v:
        return JSONResponse({"error": "Taková karta není připojená."}, status_code=404)
    if not v["writable"]:
        return JSONResponse({"error": "Na kartu se nedá zapisovat — nejdřív ji naformátuj pro Mac."}, status_code=400)
    root = Path(v["mount"]) / "spotdl"
    if root == main() or main() in root.parents or root in main().parents:
        return JSONResponse({"error": "Tohle je hlavní karta."}, status_code=400)
    (root / "Stems").mkdir(parents=True, exist_ok=True)
    cfg = config()
    cfg["cards"] = list(dict.fromkeys([*(cfg.get("cards") or []), str(root)]))
    set_config(cfg)
    link_sync()
    recount()
    return {"ok": True, "root": str(root)}


class RootIn(BaseModel):
    root: str = ""


@router.post("/api/storage/forget")
def storage_forget(req: RootIn):
    """Pick stops using a card: its songs' links go, the songs stay on the card."""
    cfg = config()
    if req.root not in (cfg.get("cards") or []):
        return JSONResponse({"error": "Tuhle kartu Pick nepoužívá."}, status_code=404)
    root = Path(req.root)
    stems = main() / "Stems"
    if stems.is_dir():
        for link in stems.iterdir():
            if link.is_symlink() and root in Path(os.readlink(link)).parents:
                link.unlink()
    cfg["cards"] = [c for c in cfg["cards"] if c != req.root]
    if cfg.get("save_to") == req.root:
        cfg["save_to"] = ""
    set_config(cfg)
    return {"ok": True}


@router.post("/api/storage/save-to")
def storage_save_to(req: RootIn):
    cfg = config()
    if req.root and req.root != str(main()) and req.root not in (cfg.get("cards") or []):
        return JSONResponse({"error": "Na tuhle kartu Pick neukládá — nejdřív ji přidej."}, status_code=400)
    cfg["save_to"] = "" if req.root == str(main()) else req.root
    set_config(cfg)
    return {"save_to": str(save_to())}


class FormatIn(BaseModel):
    disk: str = ""
    name: str = ""
    confirm: str = ""


@router.post("/api/storage/format")
def storage_format(req: FormatIn):
    """The whole card erased and made a Mac card (APFS) named as given."""
    name = (req.name or "").strip()
    if req.confirm != "SMAZAT":
        return JSONResponse({"error": "Pro smazání karty napiš SMAZAT."}, status_code=400)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,23}", name):
        return JSONResponse({"error": "Název karty: písmena bez diakritiky, číslice, mezera, - nebo _ (nejvýš 24 znaků)."}, status_code=400)
    if JOB["status"] == "running":
        return JSONResponse({"error": "Jedna karta se už formátuje."}, status_code=409)
    d = next((x for x in disks() if x["disk"] == req.disk), None)
    if not d:
        return JSONResponse({"error": "Tahle karta se formátovat nedá (není vyměnitelná nebo není připojená)."}, status_code=400)
    if _holds_pick(d["disk"]):
        return JSONResponse({"error": "Na téhle kartě má Pick hudbu — tu formátovat nejde."}, status_code=400)
    JOB.update(status="running", disk=d["disk"], name=name, note="Formátuji…", at=time.time())
    threading.Thread(target=_format, args=(d["disk"], name), daemon=True).start()
    return {"ok": True}
