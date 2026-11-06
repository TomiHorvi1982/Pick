"""How a tone print is filed and named when nobody did it by hand (tools/recategorize_prints.py, the DITORIG import).

kind_of: clean / crunch / hi_gain from how its core compresses a soft touch and playing hard, measured once per core
(tools/measure_cores.py) and kept in amps/.core-gain.json. pretty: a readable name from the core's file names.
"""
import json
import re
from pathlib import Path

CACHE = Path.home() / ".local" / "share" / "spotdl-helper" / "amps" / ".core-gain.json"


def measured(core_id: str):
    """The core's measured compression ({low, high, thd}), or None when it has not been measured yet."""
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get(core_id)
    except (OSError, ValueError):
        return None


def kind_of(m: dict) -> str:
    """clean / crunch / hi_gain from the compression of a soft touch ("low") and of playing hard ("high")."""
    if m is None:
        return ""
    if m["low"] >= 10:
        return "hi_gain"
    if m["low"] >= 2.5 or m["high"] >= 12:
        return "crunch"
    return "clean"


# model names kept in capitals; any other all-capital word of four letters or more is written as a word
KEEP_CAPS = {"ENGL", "JCM", "JMP", "EVH", "SLO", "MXR", "VOX", "AC", "TS", "OD", "CH", "OS", "BE", "HBE", "CLN", "DLX", "VTM", "BMF"}


def pretty(core_name: str) -> str:
    """'DITORIG'-style file names made readable: the amp's part, without extensions, authors and versions."""
    parts = [p.strip() for p in core_name.split(" · ")]
    amp = next((p for p in parts if p.lower().endswith(".nam")), parts[-1] if len(parts) > 1 and "cabinet" in parts[-1] else parts[-1])
    if len(parts) > 1 and "cabinet included" in parts[-1]:
        amp = parts[0]
    amp = re.sub(r"\.nam$", "", amp, flags=re.I)
    amp = re.sub(r"^(?:S-)?\[(?:AMP|POW|PRE|FULL)\]\s*", "", amp)
    amp = re.sub(r"\s*\((?:author|v\d+)\)\s*", " ", amp, flags=re.I)
    amp = re.sub(r"\s+-\s+[a-z0-9]+(?:_[a-z0-9]+)+$", "", amp)               # " - jp_is_out_of_tune": who captured it
    amp = amp.replace("_", " ")
    amp = re.sub(r"(?<=\w)-(?=\w)", " ", amp)
    amp = re.sub(r"\s+-\s+", " · ", amp)
    words = [w for w in amp.split() if not re.fullmatch(r"v\d+", w, flags=re.I)]
    words = [w.capitalize() if w.isalpha() and w.isupper() and len(w) >= 4 and w not in KEEP_CAPS else w for w in words]
    return re.sub(r"\s+", " ", " ".join(words)).strip(" ·") or core_name


