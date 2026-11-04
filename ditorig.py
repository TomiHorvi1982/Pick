"""Prints made by DITORIG (the Windows app's autotone-pilot), read for Pick.

A DITORIG export is a folder `autotone-pilot` with
  pilot.sqlite        songs (their manifest: artist, title, stems) with their newest map,
                      profiles (a NAM model, a cabinet IR, gains)
  maps/<song>.json    the same maps, as files
and next to it `seed-tone-bank/assets`, where the profiles' .nam and .wav files are kept.
The paths written inside are the Windows machine's (F:\\…): files are found again by
their name in the folders brought along, not by those paths.

A map is a timeline of one song: stretches of each guitar target, each with the profile
that sounds like it there. Pick makes one print per profile a song uses (its guitar
plays with it), the one covering most of the song first, and keeps the timeline beside
it for switching on the way.

Nothing here touches Pick's library; server.py matches the songs and writes the prints.
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

ROOT_NAME = "autotone-pilot"


def is_source(p: Path) -> bool:
    return (p / "pilot.sqlite").is_file() or (p / "maps").is_dir()


def locate(p: Path) -> Optional[Path]:
    """The autotone-pilot folder at a folder given, or a few levels inside it (a shared drive, a project)."""
    tries = [p, p / ROOT_NAME, p / "data" / ROOT_NAME]
    try:
        for a in sorted(p.iterdir()):
            if a.is_dir() and not a.name.startswith("."):
                tries += [a / ROOT_NAME, a / "data" / ROOT_NAME]
                for b in sorted(a.iterdir()):
                    if b.is_dir() and not b.name.startswith("."):
                        tries += [b / ROOT_NAME, b / "data" / ROOT_NAME]
    except OSError:
        pass
    for c in tries:
        try:
            if c.is_dir() and is_source(c):
                return c
        except OSError:
            pass
    return None


def find_sources(extra: tuple = ()) -> list:
    """autotone-pilot folders: the ones named, then on the disks attached (a shared Windows drive too)."""
    out, seen = [], set()
    vols = Path("/Volumes")
    places = [Path(x) for x in extra if x] + (sorted(vols.iterdir()) if vols.is_dir() else [])
    for v in places:
        c = locate(v) if v.is_dir() else None
        if c is not None and str(c.resolve()) not in seen:
            seen.add(str(c.resolve()))
            out.append(c)
    return out

def _asset_dirs(root: Path) -> list:
    """Where the profiles' files may be: seed-tone-bank/assets beside the folder or above it."""
    dirs = []
    for base in (root, root.parent, root.parent.parent, root.parent.parent.parent):
        for sub in ("seed-tone-bank/assets", "seed-tone-bank", "assets"):
            d = base / sub
            if d.is_dir() and d not in dirs:
                dirs.append(d)
    return dirs


class Assets:
    """The .nam and .wav files brought along, by name and by name without its suffix."""

    def __init__(self, root: Path):
        self.by_name, self.by_stem = {}, {}
        for d in _asset_dirs(root):
            for f in d.rglob("*"):
                if f.suffix.lower() in (".nam", ".wav") and f.is_file():
                    self.by_name.setdefault(f.name.lower(), f)
                    self.by_stem.setdefault(f.stem.lower(), f)

    def find(self, written: str, hash_: str = "", suffix: str = "") -> Optional[Path]:
        """A file the profile names: where it says (on this machine), by its name, or by its hash."""
        if written:
            p = Path(written)
            if "\\" not in written and p.is_file():
                return p
            name = written.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if name in self.by_name:
                hit = self.by_name[name]
                # a job's model.nam / final-ir.wav are all called the same: only a hash tells them apart
                if name not in ("model.nam", "final-ir.wav", "ir.wav") or not hash_:
                    return hit
        if hash_:
            for key in (hash_.lower(), hash_[:16].lower(), hash_[:12].lower()):
                f = self.by_stem.get(key)
                if f is not None and (not suffix or f.suffix.lower() == suffix):
                    return f
            for stem, f in self.by_stem.items():
                if hash_.lower() in stem and (not suffix or f.suffix.lower() == suffix):
                    return f
        if written and "\\" not in written and Path(written).is_file():
            return Path(written)
        return None


def _catalog_db(root: Path) -> Optional[Path]:
    """KATALOG/knihovna.sqlite of the shared drive DITORIG lives on (its archive of the songs)."""
    for base in list(root.parents)[:4]:
        f = base / "KATALOG" / "knihovna.sqlite"
        if f.is_file():
            return f
    return None


def _stems_source(path: str) -> Optional[tuple]:
    """…\\spotdl\\Stems\\<song>\\<stem>.mp3 (the card copied to Windows) → (the song's folder under
    Stems — "Sepultura/01 Arise" when nested — and the stem)."""
    parts = [x for x in str(path or "").replace("\\", "/").split("/") if x]
    low = [x.lower() for x in parts]
    for i in range(len(parts) - 2, -1, -1):
        if low[i] == "stems" and i > 0 and low[i - 1] == "spotdl" and len(parts) - i >= 3:
            return "/".join(parts[i + 1:-1]), parts[-1].rsplit(".", 1)[0]
    return None

class _Copy:
    """A SQLite database read from a private copy (with its WAL): over SMB it cannot be opened in place."""

    def __init__(self, db: Path):
        import tempfile
        self.db, self.tmp = db, tempfile.TemporaryDirectory()

    def __enter__(self):
        import shutil
        for suf in ("", "-wal", "-shm"):
            f = Path(str(self.db) + suf)
            if f.is_file():
                shutil.copyfile(f, Path(self.tmp.name) / ("db.sqlite" + suf))
        self.con = sqlite3.connect(Path(self.tmp.name) / "db.sqlite")
        return self.con

    def __exit__(self, *a):
        self.con.close()
        self.tmp.cleanup()


def catalog(root: Path) -> dict:
    """pilot song → {pilot stem → {song, stem, bytes}}: where each analysed stem came from on the card.
    The database is read from a copy (over SMB a live SQLite with its WAL is not to be opened)."""
    db = _catalog_db(root)
    if db is None:
        return {}
    out = {}
    with _Copy(db) as con:
        tracks = {tid: (src, b) for tid, src, b in con.execute("select id, source, bytes from tracks")}
        for pilot, ids in con.execute("select pilot_song_id, track_ids from tone_maps order by revision"):
            try:
                rows = json.loads(ids)
            except ValueError:
                continue
            m = out.setdefault(pilot, {})
            for r in rows if isinstance(rows, list) else []:
                src, size = tracks.get(r.get("track_id"), (None, None))
                got = _stems_source(src)
                if got:
                    m[r.get("pilot_stem_id")] = {"song": got[0], "stem": got[1], "bytes": size}
    return out


def tidy(artist: str, title: str) -> tuple:
    """A name written as a file's: "Death_-_Believe_752479" → ("Death", "Believe")."""
    import re
    if artist or "_" not in title:
        return artist, title
    t = re.sub(r"_[0-9a-f]{6}$", "", title)
    if "_-_" in t:
        artist, t = t.split("_-_", 1)
    return artist.replace("_", " ").strip(), t.replace("_", " ").strip()


def read(root: Path) -> dict:
    """{songs: [{id, artist, title, album, map, stems, source}], profiles: {id: metadata}}"""
    songs, profiles = {}, {}
    db = root / "pilot.sqlite"
    if db.is_file():
        with _Copy(db) as con:
            for pid, meta in con.execute("select id, metadata from profiles"):
                try:
                    profiles[pid] = json.loads(meta)
                except ValueError:
                    pass
            for sid, manifest, mp in con.execute("select id, manifest, map from songs"):
                try:
                    m = json.loads(manifest)
                except ValueError:
                    continue
                artist, title = tidy(m.get("artist") or "", m.get("title") or "")
                songs[sid] = {"id": sid, "artist": artist, "title": title, "album": m.get("album") or "",
                              "stems": m.get("stems") or [], "map": json.loads(mp) if mp else None, "source": {}}
    for d in _asset_dirs(root):                        # the bank's own list: profiles the database lacks
        f = (d.parent if d.name == "assets" else d) / "profiles.json"
        try:
            for pr in json.loads(f.read_text(encoding="utf-8")).get("profiles") or []:
                if isinstance(pr, dict) and pr.get("id"):
                    profiles.setdefault(pr["id"], pr)
        except (OSError, ValueError):
            pass
    for f in sorted((root / "maps").glob("*.json")) if (root / "maps").is_dir() else []:
        try:
            mp = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = mp.get("song_id") or f.stem
        s = songs.get(sid)
        if s is None:
            continue                                   # a map whose song is not told: nothing to match it by
        if not s["map"] or (mp.get("revision") or 0) > (s["map"].get("revision") or 0):
            s["map"] = mp
    try:
        cat = catalog(root)
    except (OSError, sqlite3.Error):
        cat = {}
    for sid, src in cat.items():
        if sid in songs:
            songs[sid]["source"] = src
    return {"songs": [s for s in songs.values() if s["map"]], "profiles": profiles}


def guitar_targets(song: dict) -> set:
    """The map's targets that are a guitar of the song (all of them when the manifest does not say)."""
    mp = song["map"]
    ids = {t.get("id") for t in mp.get("targets") or []}
    roles = {s.get("id"): (s.get("role") or "") for s in song.get("stems") or []}
    gtr = {i for i in ids if "guitar" in roles.get(i, "guitar")}
    return gtr or ids


def plan(song: dict, targets: Optional[set] = None) -> dict:
    """The timeline in seconds of the targets given (the song's guitars when none are),
    and their profiles by how much of the song each covers."""
    mp = song["map"]
    sr = float((mp.get("timebase") or {}).get("sample_rate") or 48000)
    gtr = targets if targets is not None else guitar_targets(song)
    timeline, cover = [], {}
    for seg in mp.get("segments") or []:
        if seg.get("target") not in gtr:
            continue
        a, b = seg.get("start_frame") or 0, seg.get("end_frame") or 0
        pid = seg.get("profile_version_id")
        timeline.append({"start": round(a / sr, 3), "end": round(b / sr, 3), "profile": pid, "state": seg.get("state"),
                         "confidence": seg.get("confidence"), "target": seg.get("target"),
                         "cost": (seg.get("matching") or {}).get("cost")})
        if pid:
            cover[pid] = cover.get(pid, 0.0) + max(0.0, (b - a) / sr)
    dur = (mp.get("duration_frames") or 0) / sr
    order = sorted(cover, key=lambda k: -cover[k])
    return {"duration": round(dur, 2), "timeline": timeline, "profiles": order,
            "cover": {k: round(cover[k] / dur, 3) if dur else 0 for k in order}, "revision": mp.get("revision") or 0}


def match_of(timeline: list, pid: str) -> Optional[int]:
    """How alike, in per cent, the profile was found where it plays: from DITORIG's matching cost."""
    costs = [t["cost"] for t in timeline if t["profile"] == pid and isinstance(t.get("cost"), (int, float))]
    if not costs:
        return None
    c = sum(costs) / len(costs)
    return max(0, min(100, round(100 * (1 - c))))
