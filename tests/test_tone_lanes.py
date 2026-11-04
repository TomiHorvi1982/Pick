"""The lanes above the sections: kept beside the stems, the catalog of sounds once each, the solos found."""
import json

import server


def _song(root, name, prints=()):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for s in ("drums", "bass", "vocals", "other", "guitar"):
        (d / f"{s}.mp3").write_bytes(b"x" * 2000)
    for pid, extra in prints:
        p = d / server.TONE_DIR / "prints" / pid
        p.mkdir(parents=True)
        (p / "print.json").write_text(json.dumps({"name": "x", "core": {"id": "a.nam", "name": "Amp"}, "category": "crunch", **extra}))
        (p / "print-ir.wav").write_bytes(b"RIFF")
    return d


def test_lanes_are_kept_and_given_back(client, tmp_music_root):
    _song(tmp_music_root, "S")
    assert client.get("/api/tone/S/lanes").json() == {"lanes": None}
    lanes = {"tone": {"segs": [{"start": 0, "end": 10, "song": "S", "pid": "20261104-120000"}]},
             "delay": {"on": True, "blocks": [{"start": 2, "end": 5, "name": "Sólo 1/4", "p": {"mix": 0.3}}]}, "reverb": {"on": False, "blocks": []}}
    assert client.put("/api/tone/S/lanes", json=lanes).json() == {"lanes": lanes}
    assert client.get("/api/tone/S/lanes").json() == {"lanes": lanes}
    assert client.put("/api/tone/S/lanes", json={"tone": []}).status_code == 400
    assert client.put("/api/tone/S/lanes", content="null").json() == {"lanes": None}
    assert client.get("/api/tone/S/lanes").json() == {"lanes": None}


def test_the_catalog_has_each_ditorig_sound_once(client, tmp_music_root):
    server._TONE_CATALOG["at"] = 0
    dit = lambda prof: {"ditorig": {"song": "x", "profile": prof, "descriptor": [1.0, 2.0]}, "name": "DITORIG · Marshall", "name_auto": False}
    _song(tmp_music_root, "A", [("20261104-120000", dit("p1")), ("20261104-120001", {})])
    _song(tmp_music_root, "B", [("20261104-120000", dit("p1"))])
    rows = client.get("/api/tone/catalog?fresh=true").json()["sounds"]
    by = {r["key"]: r for r in rows}
    assert by["dit:p1"]["uses"] == 2 and by["dit:p1"]["name"] == "Marshall" and by["dit:p1"]["descriptor"] == [1.0, 2.0]
    assert "pick:A/20261104-120001" in by and len(rows) == 2


def test_solos_from_the_sections_when_there_is_no_solo_stem(client, tmp_music_root):
    d = _song(tmp_music_root, "S")
    (d / "sections.json").write_text(json.dumps({"sections": [{"start": 0, "end": 20, "name": "Sloka", "kind": "verse"},
                                                              {"start": 20, "end": 35, "name": "Sólo", "kind": "solo"}]}))
    got = client.get("/api/tone/S/solo").json()
    assert got == {"regions": [{"start": 20.0, "end": 35.0}], "from": "sections"}
