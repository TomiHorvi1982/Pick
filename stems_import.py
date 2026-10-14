"""Songs taken apart elsewhere (Moises and the like) and put into the Stems folder by hand.

Pick keeps one song per folder — Stems/<Band - Title>/drums.mp3, bass.mp3, … —
but what comes from outside is laid out in many ways:

    Sepultura/01 Arise/vocals.wav, lead_guitar.wav, …          a band, a folder per song
    Death-All Songs Steems from Moises/01 Bass - Killing Spree.mp3  one folder, the part in the name
    Trivium-Shogun album Steems from Moises/Shogun-B minor-140bpm-440hz/
        Shogun-Rhythm Guitars-B minor-140bpm-440hz.mp3            album / song / song-part-key-bpm

Each song found is given a folder of its own in Pick's layout, its files
*linked* in (a hard link: no second copy on the disk, the player's own folders
stay exactly as they are). What each file is comes from words in its name.
The full mix, a metronome or a backing track is left out. What has been taken
in once is noted (Stems/.imported.json), so a set the player deletes in Pick
does not come back.
"""
import json
import os
import re
from pathlib import Path

AUDIO = (".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif")
REGISTRY = ".imported.json"
# words of a file's name → the track it is; the first that fits wins (lead and rhythm before guitar)
PARTS = [
    ("guitar_solo", r"lead[ _-]*guitars?|solo[ _-]*guitars?|guitars?[ _-]*(lead|solo)"),
    ("guitar_rhythm", r"rhythm[ _-]*guitars?|guitars?[ _-]*rhythm"),
    ("vocals", r"vocals?|voice|vox"),
    ("drums", r"drums?|percussion"),
    ("bass", r"bass"),
    ("guitar", r"guitars?"),
    ("piano", r"piano|keys|keyboards?|synth"),
    ("other", r"others?"),
]
SKIP = r"metronome|click|backing|full[ _-]*song|original|mix|master|instrumental|karaoke"
_NUM = re.compile(r"^\s*\d{1,3}[\s._-]+")
_KEYBPM = re.compile(r"-[A-G][#b]?\s*(major|minor)-\d+bpm-\d+hz$", re.I)


def part_of(name: str):
    """The track a file is by its name ("01 Lead Guitars - X" → guitar_solo), "" for one to leave out, None if unknown."""
    low = _KEYBPM.sub("", name.lower())
    if re.search(rf"(^|[\s_-])({SKIP})($|[\s_-])", low):
        return ""
    for track, pat in PARTS:
        if re.search(rf"(^|[\s_\-(]){pat}($|[\s_\-)])", low):
            return track
    return None


def _strip_part(name: str, track: str) -> str:
    """What is left of a file's name without its number, its part, and the key and tempo after it."""
    low = _KEYBPM.sub("", name)
    pat = dict(PARTS)[track]
    pieces = [p.strip() for p in re.split(r"\s+-\s+|-(?=[A-Z])", low) if p.strip()]
    keep = [p for p in pieces if not re.fullmatch(rf"(\d+\s+)?({pat})", p.strip(), re.I)]
    title = " - ".join(keep) if keep else ""
    return _NUM.sub("", title).strip(" -_")


def band_and_album(container: str):
    """'Trivium-Shogun album Steems from Moises' → ('Trivium', 'Shogun'); 'Sepultura' → ('Sepultura', '')."""
    clean = re.sub(r"\s*(steems?|stems?)\b.*$", "", container, flags=re.I).strip(" -_")
    band, _, rest = clean.partition("-")
    album = re.sub(r"\balbum\b|\ball songs?\b", "", rest, flags=re.I).strip(" -_")
    return band.strip() or container, album


def songs_in(container: Path):
    """[(title, {track: file})] of a folder put in by hand — songs in folders of their own, or side by side."""
    out = []
    files = sorted(p for p in container.iterdir() if p.is_file() and p.suffix.lower() in AUDIO)
    groups = {}
    for f in files:
        t = part_of(f.stem)
        if not t:
            continue
        title = _strip_part(f.stem, t)
        if title:
            groups.setdefault(title.lower(), (title, {}))[1].setdefault(t, f)
    out += list(groups.values())
    for sub in sorted(p for p in container.iterdir() if p.is_dir() and not p.name.startswith(".")):
        parts = {}
        for f in sorted(p for p in sub.iterdir() if p.is_file() and p.suffix.lower() in AUDIO):
            t = part_of(f.stem)
            if t:
                parts.setdefault(t, f)
        if len(parts) >= 2:
            title = _NUM.sub("", _KEYBPM.sub("", sub.name)).strip(" -_") or sub.name
            title = re.sub(r"(?<=\w)_(?=\w)", "/", title)          # "Refuse_Resist": a slash a file name could not have
            out.append((title, parts))
    return [(t, p) for t, p in out if len(p) >= 2]


def is_set(d: Path, names) -> bool:
    """A folder already in Pick's layout: a stem straight in it."""
    return any((d / f"{n}{e}").is_file() for n in names for e in AUDIO)


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", name).strip().strip(".")[:150]


def _link(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        os.symlink(os.path.relpath(src, dest.parent), dest)


def run(stems_root: Path, names) -> dict:
    """Takes in what is new. → {"added": [names], "known": n}"""
    reg_path = stems_root / REGISTRY
    try:
        got = json.loads(reg_path.read_text(encoding="utf-8"))
        reg, seen = dict(got.get("songs") or {}), list(got.get("containers") or [])
    except (OSError, ValueError, AttributeError):
        reg, seen = {}, []
    added, known, containers = [], 0, []
    for c in sorted(p for p in stems_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if is_set(c, names) or (c / "meta.json").is_file():
            continue
        containers.append(c.name)
        try:
            found = songs_in(c)
        except OSError:
            continue
        band, album = band_and_album(c.name)
        for title, parts in found:
            src = str(next(iter(parts.values())).parent.relative_to(stems_root)) + "::" + title
            if src in reg:
                known += 1
                continue
            name = _safe(f"{band} - {title}")
            if (stems_root / name).exists():                # Pick has separated this song itself: this one goes beside it
                name = _safe(f"{band} - {title} (Moises)" if "moises" in c.name.lower() or {"guitar_solo", "guitar_rhythm"} & set(parts)
                             else f"{band} - {title} (vlastní)")
            dest, n = stems_root / name, 2
            while dest.exists():
                dest, n = stems_root / f"{name} {n}", n + 1
            dest.mkdir()
            for track, f in parts.items():
                _link(f, dest / f"{track}{f.suffix.lower()}")
            meta = {"artist": band, "title": title, "album": album or None, "manual": True,
                    "imported": str(next(iter(parts.values())).parent.relative_to(stems_root))}
            (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            reg[src] = name
            added.append(name)
    if added or sorted(containers) != sorted(seen) or not reg_path.exists():
        reg_path.write_text(json.dumps({"songs": reg, "containers": containers}, ensure_ascii=False, indent=0), encoding="utf-8")
    return {"added": added, "known": known}


def signature(stems_root: Path) -> str:
    """Changes when a folder is put into Stems, or something is put into one taken in before —
    cheap enough to ask on every listing (the song folders Pick writes to are not looked at)."""
    try:
        rows = [str(stems_root.stat().st_mtime_ns)]
        seen = json.loads((stems_root / REGISTRY).read_text(encoding="utf-8")).get("containers") or []
    except (OSError, ValueError, AttributeError):
        return "new"
    for name in seen:
        try:
            rows.append(f"{name}:{(stems_root / name).stat().st_mtime_ns}")
        except OSError:
            rows.append(f"{name}:-")
    return "|".join(rows)
