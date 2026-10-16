"""Nahraná kytara — the virtual guitar and the tab guide played on recorded notes, not a synthesised string.

A set is a folder under guitar-samples/<id>/: a manifest.json and one FLAC a note. Every note was
recorded on its own string (a guitar plays the same pitch on more strings, each sounding a little
different), picked down, at two strengths (soft for legato, full for a picked note), twice each
(played in turn, so a riff does not machine-gun), plucked open and palm muted. The guitar is DI —
no amp — because what Pick plays goes on into a NAM amp or a tone print.

The first set is made from Soundiron's Axe Machina (the player's own library, its plain WAVs, in
Kontakt's Libraries folder) by tools/build_guitar_samples.py, once.
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
ROOT = Path.home() / ".local" / "share" / "spotdl-helper" / "guitar-samples"
SET_ID = re.compile(r"^[a-z0-9-]{1,40}$")
FILE = re.compile(r"^[a-z]+-\d{2,3}-s\d-v\d-r\d\.flac$")


def sets() -> list:
    out = []
    if ROOT.is_dir():
        for d in sorted(ROOT.iterdir()):
            f = d / "manifest.json"
            if f.is_file():
                try:
                    m = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({"id": d.name, "name": m.get("name", d.name), "samples": len(m.get("samples", [])),
                            "bytes": m.get("bytes", 0)})
    return out


@router.get("/api/guitar-samples")
def list_sets():
    return {"sets": sets(), "folder": str(ROOT)}


@router.get("/api/guitar-samples/{set_id}/manifest.json")
def manifest(set_id: str):
    f = ROOT / set_id / "manifest.json"
    if not SET_ID.match(set_id) or not f.is_file():
        raise HTTPException(404, "Ta sada vzorků tu není.")
    return FileResponse(f, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/api/guitar-samples/{set_id}/{name}")
def sample(set_id: str, name: str):
    f = ROOT / set_id / name
    if not SET_ID.match(set_id) or not FILE.match(name) or not f.is_file():
        raise HTTPException(404, "Ten vzorek tu není.")
    return FileResponse(f, media_type="audio/flac", headers={"Cache-Control": "max-age=31536000, immutable"})
