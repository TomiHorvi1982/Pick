"""Tone prints filed by what their core does, and named so a person can read it.

    ~/.local/venvs/pick-tone/bin/python tools/recategorize_prints.py [--apply]

DITORIG's import filed almost every print as "clean" (its own label for the song's stretches — a thrash
song's WildHeavy core among them) and named it after its files ("DITORIG · RVB100-WILDHEAVY-V1.nam ·
VH-MA-…wav"). Here every core is measured once (tools/measure_cores.py: how it compresses a soft and a hard
note) and kept in amps/.core-gain.json, which the import reads from then on. Each print whose kind was
filed automatically gets the measured one; a print still named after its files gets its amp's name.
What the player filed or named by hand is left as it is. Without --apply it only says what it would do.
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import measure_cores  # noqa: E402
from print_names import CACHE, kind_of, pretty  # noqa: E402

HELPER = Path.home() / ".local" / "share" / "spotdl-helper"
AMPS = HELPER / "amps"


def music_root() -> Path:
    try:
        r = json.loads((HELPER / "config.json").read_text(encoding="utf-8")).get("music_root")
        if r:
            return Path(r)
    except (OSError, ValueError):
        pass
    return Path.home() / "Music" / "spotdl"


def core_file(core_id: str, root: Path) -> Path:
    return root / core_id[4:] if core_id.startswith("lib:") else AMPS / core_id


def main(apply: bool):
    root = music_root()
    files = sorted(root.glob("Stems/*/tone/prints/*/print.json"))
    prints = []
    for f in files:
        try:
            prints.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            print(f"nečitelné: {f}", file=sys.stderr)
    cache = {}
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cores = sorted({(p.get("core") or {}).get("id") for _, p in prints} - {None} - set(cache))
    if cores:
        paths = {str(core_file(c, root)): c for c in cores if core_file(c, root).is_file()}
        print(f"měřím {len(paths)} jader…", file=sys.stderr)
        got = measure_cores.measure(list(paths))
        for path, m in got.items():
            cache[paths[path]] = m
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    changes, kinds_before, kinds_after = [], collections.Counter(), collections.Counter()
    for f, p in prints:
        cid = (p.get("core") or {}).get("id")
        kinds_before[p.get("category")] += 1
        new = dict(p)
        k = kind_of(cache.get(cid))
        if k and p.get("category_auto", True) and p.get("category") != k:
            new["category"], new["category_auto"] = k, True
        name = p.get("name") or ""
        if name.startswith("DITORIG · ") and (p.get("core") or {}).get("name"):
            new["name"] = pretty(p["core"]["name"])
        kinds_after[new.get("category")] += 1
        if new != p:
            changes.append((f, p, new))
    # the same amp twice in one song: told apart by where in the song it plays
    by_song = collections.defaultdict(list)
    for f, p, new in changes:
        by_song[(f.parent.parent, new.get("name"), new.get("stem"))].append(new)
    for (_, _, _), group in by_song.items():
        if len(group) > 1:
            for n in group:
                s = float(n.get("start") or 0)
                n["name"] = f"{n['name']} · {int(s // 60)}:{int(s % 60):02d}"
    print(json.dumps({"otisků": len(prints), "změní se": len(changes), "kategorie před": kinds_before, "kategorie po": kinds_after,
                      "příklady názvů": sorted({n.get("name") or "" for _, _, n in changes})[:25]}, ensure_ascii=False, indent=1, default=dict))
    if apply:
        for f, _, new in changes:
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        print(f"zapsáno {len(changes)}", file=sys.stderr)


if __name__ == "__main__":
    main("--apply" in sys.argv[1:])
