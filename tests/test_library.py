"""The library on the host's PC (deploy/pick-library/library.py): read-only over a copy of the card, the friend's
page between jams; the Mac calls it with a token, never the other way."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server

TOKEN = "t" * 32


@pytest.fixture
def lib(tmp_path, monkeypatch):
    data, amps, helper, state = tmp_path / "spotdl", tmp_path / "amps", tmp_path / "helper", tmp_path / "state"
    song = data / "Stems" / "Band - Song [abcdefghijk]"
    (song / "tone" / "prints" / "p1").mkdir(parents=True)
    for st, f in (("drums", 200), ("bass", 80)):
        subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d=2", str(song / f"{st}.mp3")], check=True)
    (song / "meta.json").write_text(json.dumps({"artist": "Band", "title": "Song", "album": "Album"}))
    (song / "chords.json").write_text(json.dumps({"chords": [{"start": 0, "end": 1, "chord": "E5"}]}))
    (song / "tone" / "prints" / "p1" / "print.json").write_text(json.dumps({"name": "Lead", "core": {"id": "nam/DITORIG/Amp X.nam", "name": "Amp X"}, "pre": {}}))
    (song / "tone" / "prints" / "p1" / "print-ir.wav").write_bytes(b"RIFF0000WAVE")
    (amps / "nam").mkdir(parents=True)
    (amps / "nam" / "Amp X.nam").write_text("{}")                          # the copy keeps it flat, not in DITORIG/
    (amps / "ir").mkdir()
    (amps / "ir" / "Cab.wav").write_bytes(b"RIFF")
    (helper / "lyrics").mkdir(parents=True)
    (helper / "steems-playlists.json").write_text(json.dumps([{"id": "a", "name": "Metal", "songs": ["Band - Song [abcdefghijk]"]}]))
    for k, v in dict(PICK_DATA=data, PICK_AMPS=amps, PICK_HELPER=helper, PICK_STATE=state, PICK_SYNC_TOKEN=TOKEN, PICK_HOST_NAME="Tomi",
                     PICK_STATIC=Path(server.__file__).parent / "static").items():
        monkeypatch.setenv(k, str(v))
    spec = importlib.util.spec_from_file_location("library", Path(server.__file__).parent / "deploy" / "pick-library" / "library.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "library", mod)
    spec.loader.exec_module(mod)
    mod.user_set("Pavel", "kytara66")
    return mod, TestClient(mod.app)


def test_nothing_without_logging_in_then_the_songs(lib):
    mod, c = lib
    assert c.get("/g/songs").status_code == 403 and c.get("/g/preview?name=x").status_code == 403
    assert "Pick Jam" in c.get("/").text
    assert c.get("/g/me").json()["library"] is True
    assert c.post("/login", json={"name": "Pavel", "password": "spatne"}).status_code == 401
    assert c.post("/login", json={"name": "Pavel", "password": "kytara66"}).status_code == 200
    rows = c.get("/g/songs").json()["songs"]                                 # at once: lengths follow in the background
    assert [r["name"] for r in rows] == ["Band - Song [abcdefghijk]"] and rows[0]["stems"] == ["drums", "bass"]
    assert rows[0]["yt"] == "https://youtu.be/abcdefghijk"
    for _ in range(100):
        if not mod._SONGS["filling"]:
            break
        time.sleep(0.05)
    assert c.get("/g/songs").json()["songs"][0]["duration"] == pytest.approx(2, abs=0.2)
    assert c.get("/g/song?name=Band%20-%20Song%20%5Babcdefghijk%5D").json()["chords"][0]["chord"] == "E5"
    pv = c.get("/g/preview?name=Band%20-%20Song%20%5Babcdefghijk%5D", headers={"Range": "bytes=0-99"})
    assert pv.status_code == 206 and pv.content[:4] == b"\x1aE\xdf\xa3"
    assert c.get("/g/playlists").json()["playlists"][0]["name"] == "Metal"


def test_sounds_and_the_copy_laid_out_otherwise(lib):
    mod, c = lib
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert c.get("/g/prints").json()["prints"][0]["name"] == "Lead"
    p = c.get("/g/print?name=Band%20-%20Song%20%5Babcdefghijk%5D&print=p1").json()["print"]
    assert c.get("/g/amp", params={"file": p["core"]["id"]}).status_code == 200      # found by its name
    assert c.get("/g/amps?kind=ir").json()["amps"][0]["file"] == "ir/Cab.wav"
    for bad in ("../../etc/passwd", "/etc/passwd", "nam/../../state/users.json"):
        assert c.get("/g/amp", params={"file": bad}).status_code == 404
    assert c.get("/g/song?name=..%2F..").status_code == 404
    assert c.get("/g/print?name=Band%20-%20Song%20%5Babcdefghijk%5D&print=..%2F..").status_code == 404


def test_na_priste_and_the_mac_with_its_token(lib):
    mod, c = lib
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    n = "Band - Song [abcdefghijk]"
    assert c.post("/g/fav", json={"ops": [{"name": n, "add": True}, {"name": "../x", "add": True}]}).json()["songs"] == [n]
    m = TestClient(mod.app)                                                    # the Mac: no login, its token
    assert m.get("/sync/fav").status_code == 403
    assert m.get("/sync/fav", headers={"Authorization": "Bearer nope"}).status_code == 403
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert m.get("/sync/fav", headers=h).json()["songs"] == [n]
    assert m.put("/sync/fav", headers=h, json={"songs": []}).json()["songs"] == []
    assert m.post("/sync/status", headers=h, json={"on": True, "url": "https://neverlast.cz"}).status_code == 200
    me = c.get("/g/me").json()
    assert me["jam"] is True and me["jam_url"] == "https://neverlast.cz"
    users = json.loads((Path(mod.STATE) / "users.json").read_text())
    assert "kytara66" not in json.dumps(users)


def test_the_list_is_kept_and_follows_the_copy(lib):
    mod, c = lib
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert len(c.get("/g/songs").json()["songs"]) == 1
    assert (Path(mod.STATE) / "songs-cache.json").is_file()
    new = mod.DATA / "Stems" / "Other - Two [bcdefghijkl]"
    new.mkdir()
    (new / "drums.mp3").write_bytes((mod.DATA / "Stems" / "Band - Song [abcdefghijk]" / "drums.mp3").read_bytes())
    mod._SONGS.update(rows=None, dirs=None)                                   # a new start: last start's list at once
    assert len(c.get("/g/songs").json()["songs"]) == 1
    for _ in range(100):                                                       # and the walk behind it finds the new one
        if not mod._SONGS["busy"]:
            break
        time.sleep(0.05)
    assert [r["name"] for r in c.get("/g/songs").json()["songs"]] == ["Band - Song [abcdefghijk]", "Other - Two [bcdefghijkl]"]


def test_amps_and_cabinets_sorted_as_in_pick(lib):
    mod, c = lib
    (mod.AMPS / "nam" / "Peavey 5150 Lead.nam").write_text("{}")
    (mod.AMPS / "nam" / "Fender Twin Clean.nam").write_text("{}")
    (mod.AMPS / "ir" / "Mesa 4x12 V30 SM57.wav").write_bytes(b"RIFF")
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    d = c.get("/g/amp-catalog", params={"kind": "model", "sort": "gain"}).json()
    gains = {x["name"]: x["gain"] for x in d["items"]}
    assert gains["Peavey 5150 Lead"] == "hi_gain" and gains["Fender Twin Clean"] == "clean"
    assert dict(d["facets"]["gain"])["hi_gain"] == 1 and d["items"][0]["group"] == "Čistý"
    hot = c.get("/g/amp-catalog", params={"kind": "model", "gain": "hi_gain"}).json()["items"]
    assert [x["name"] for x in hot] == ["Peavey 5150 Lead"] and hot[0]["brand"] == "Peavey"
    ir = c.get("/g/amp-catalog", params={"kind": "ir", "sort": "speaker"}).json()["items"]
    v30 = next(x for x in ir if x["name"] == "Mesa 4x12 V30 SM57")
    assert v30["speaker"] == "Vintage 30" and v30["mic"] == "SM57" and v30["group"] == "Vintage 30"
    assert c.get("/g/amp", params={"file": v30["file"]}).status_code == 200
