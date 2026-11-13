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
    land = c.get("/").text                                                  # the guide with the one download; the app is /app
    assert "Pick Jam" in land and "/pick-jam.exe" in land and "Pick Jam" in c.get("/app").text
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
    assert me["jam"] is True and me["jam_url"] == "/g/jam-go"
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


def test_playing_alone_the_tracks_and_a_login_kept_over_a_restart(lib):
    mod, c = lib
    n = "Band - Song [abcdefghijk]"
    assert c.get("/g/stem", params={"name": n, "stem": "drums"}).status_code == 403
    r = c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert "max-age=31536000" in r.headers["set-cookie"].lower()
    st = c.get("/g/stem", params={"name": n, "stem": "drums"})
    assert st.status_code == 200 and st.content[:4] == b"\x1aE\xdf\xa3"
    assert c.get("/g/stem", params={"name": n, "stem": "../../x"}).status_code == 404
    mod.SESS.clear()                                                           # the container started again
    assert c.get("/g/songs").status_code == 200
    assert "kytara66" not in (Path(mod.STATE) / "sessions.json").read_text()
    once = TestClient(mod.app)
    r = once.post("/login", json={"name": "Pavel", "password": "kytara66", "remember": False})
    assert "max-age" not in r.headers["set-cookie"].lower()


def test_the_desktop_app_launcher_and_manifest(lib):
    mod, c = lib
    cmd = c.get("/pick-jam.cmd", headers={"host": "jam.neverlast.cz"})
    assert cmd.status_code == 200 and 'filename="Pick Jam.cmd"' in cmd.headers["content-disposition"]
    t = cmd.content.decode()
    assert 'set "PJ_URL=https://jam.neverlast.cz/"' in t and "\r\n" in t and "{{" not in t and "--app=\" + `$url + \"app" in t and "engine/pick-jam-engine.exe" in t and "'serve'" in t
    m = c.get("/manifest.webmanifest").json()
    assert m["display"] == "standalone" and any(i["sizes"] == "512x512" for i in m["icons"])
    assert c.get("/static/jam-icon-512.png").status_code == 200 and c.get("/static/jam-icon.ico").status_code == 200


def test_the_nam_folder_on_the_card_copy_too(lib):
    mod, c = lib
    (mod.DATA / "NAM" / "tone3000-catalog").mkdir(parents=True)
    (mod.DATA / "NAM" / "tone3000-catalog" / "Mesa Dual Rec Modern.nam").write_text("{}")
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    items = c.get("/g/amp-catalog", params={"kind": "model", "q": "dual rec"}).json()["items"]
    assert [(x["file"], x["pack"], x["brand"]) for x in items] == [("lib:NAM/tone3000-catalog/Mesa Dual Rec Modern.nam", "tone3000-catalog", "Mesa Boogie")]
    assert c.get("/g/amp", params={"file": items[0]["file"]}).status_code == 200
    for bad in ("lib:NAM/../../spotdl/x.nam", "lib:NAM/../meta.json"):
        assert c.get("/g/amp", params={"file": bad}).status_code == 404
    assert (Path(mod.STATE) / "amps.json").is_file()


def test_the_name_in_any_letters(lib):
    mod, c = lib
    r = c.post("/login", json={"name": "  pAVEL ", "password": "kytara66"})
    assert r.status_code == 200 and r.json()["user"] == "Pavel"
    assert c.post("/login", json={"name": "pavel", "password": "Kytara66"}).status_code == 401     # the password stays exact


def test_the_friends_from_pick_log_in_too(lib):
    mod, c = lib
    import jam
    salt = bytes(16)
    pick = {"Max": {"salt": salt.hex(), "hash": jam._hash("struna77", salt)}}
    m = TestClient(mod.app)
    assert m.put("/sync/users", json={"users": pick}).status_code == 403
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert m.put("/sync/users", headers=h, json={"users": {**pick, "bad": {"salt": "x", "hash": "y"}}}).json()["users"] == ["Max"]
    assert c.post("/login", json={"name": "max", "password": "struna77"}).status_code == 200
    assert c.post("/login", json={"name": "Pavel", "password": "kytara66"}).status_code == 200      # its own account still
    m.put("/sync/users", headers=h, json={"users": {}})                       # taken away in Pick
    assert c.post("/login", json={"name": "Max", "password": "struna77"}).status_code == 401
    assert "struna77" not in (Path(mod.STATE) / "pick-users.json").read_text()


def test_the_mac_launcher_in_a_zip_that_keeps_it_runnable(lib):
    import io, zipfile
    mod, c = lib
    r = c.get("/pick-jam-mac.zip", headers={"host": "jam.neverlast.cz"})
    assert r.status_code == 200 and 'Pick Jam pro Mac.zip' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    info = z.getinfo("Pick Jam.command")
    assert (info.external_attr >> 16) & 0o111                                  # runnable after unzipping
    t = z.read(info).decode()
    assert t.startswith("#!/bin/bash") and 'URL="https://jam.neverlast.cz/"' in t and "--app=" in t and "{{" not in t
    assert c.get("/static/jam-icon.icns").status_code == 200


def test_downloaded_playlists_from_the_copy_then_from_the_mac(lib):
    mod, c = lib
    dl = mod.DATA / "Playlists" / "Best Riffs"
    dl.mkdir(parents=True)
    (dl / "Band - Song [abcdefghijk].mp3").write_bytes(b"x")
    (dl / "Other - Missing.mp3").write_bytes(b"x")                      # no stems in the library: left out
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    got = {p["name"]: p for p in c.get("/g/playlists").json()["playlists"]}
    assert got["Metal"]["kind"] == "own" and got["Best Riffs"] == {"name": "Best Riffs", "songs": ["Band - Song [abcdefghijk]"], "kind": "dl", "all": 2}
    assert c.put("/sync/playlists", json={"playlists": []}).status_code == 403
    h = {"Authorization": f"Bearer {TOKEN}"}
    r = c.put("/sync/playlists", headers=h, json={"playlists": [{"name": "From Mac", "kind": "dl", "all": 9, "songs": ["Band - Song [abcdefghijk]", "../x", "Gone"]},
                                                              {"name": "Jam — na příště", "songs": ["Band - Song [abcdefghijk]"]}]})
    assert r.json()["ok"]
    assert c.get("/g/playlists").json()["playlists"] == [{"name": "From Mac", "songs": ["Band - Song [abcdefghijk]"], "kind": "dl", "all": 9}]


def test_tracks_and_preview_made_ahead(lib):
    mod, c = lib
    stale = mod.STEMS_CACHE / "0000000000000000.webm"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    mod._prepare_once()
    assert len(list(mod.STEMS_CACHE.glob("*.webm"))) == 2 and len(list(mod.CACHE.glob("*.webm"))) == 1 and not stale.exists()
    assert mod.PREP["done"] == mod.PREP["todo"] == 1 and mod.PREP["failed"] == 0
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    made = {p.stat().st_mtime for p in mod.STEMS_CACHE.glob("*.webm")}
    assert c.get("/g/stem?name=Band%20-%20Song%20%5Babcdefghijk%5D&stem=drums").content[:4] == b"\x1aE\xdf\xa3"
    assert {p.stat().st_mtime for p in mod.STEMS_CACHE.glob("*.webm")} == made            # served, not made again
    st = c.post("/sync/status", headers={"Authorization": f"Bearer {TOKEN}"}, json={"on": False}).json()
    assert st["prepared"]["done"] == 1 and st["free_gb"] > 0
    assert "prepared" not in c.get("/health").json()


def test_the_dj_is_on_the_page_and_reads_the_beat_grid_behind_the_login(lib):
    mod, c = lib
    song = mod.DATA / "Stems" / "Band - Song [abcdefghijk]"
    (song / "beats.json").write_text(json.dumps({"beats": [0.5, 1.0, 1.5], "downbeats": [0.5], "bpm": 120.0, "grid_version": 2, "source_stem": "drums"}))
    page = c.get("/app").text
    for name in ("dj-core.js", "dj.js", "dj-library.js"):
        assert f'<script src="/static/{name}?v=' in page
        assert c.get(f"/static/{name}").status_code == 200
    assert c.get("/static/worklets/dj-deck.js").status_code == 200
    assert c.get("/static/worklets/nothere.js").status_code == 404
    assert c.get("/g/beats?name=Band - Song [abcdefghijk]").status_code == 403
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert c.get("/g/beats?name=Band - Song [abcdefghijk]").json() == {
        "beats": [0.5, 1.0, 1.5], "downbeats": [0.5], "bpm": 120.0, "beats_per_bar": None, "duration": None}
    assert c.get("/g/beats?name=../Stems").status_code == 404
    (song / "beats.json").unlink()
    assert c.get("/g/beats?name=Band - Song [abcdefghijk]").status_code == 404


def test_engine_sent_by_the_mac_downloaded_by_a_friend(lib):
    mod, c = lib
    h = {"Authorization": f"Bearer {TOKEN}"}
    blob = b"MZ" + b"x" * 200_000
    assert c.put("/sync/engine/pick-jam-engine.exe", content=blob).status_code == 403
    assert c.put("/sync/engine/evil.sh", headers=h, content=blob).status_code == 400
    assert c.put("/sync/engine/pick-jam-engine.exe", headers=h, content=blob).json()["ok"]
    assert list(c.get("/sync/engine", headers=h).json()) == ["pick-jam-engine.exe"]
    # the program itself is open (the desktop launcher fetches it before anyone logs in); the page lists it once logged in
    assert c.get("/engine/pick-jam-engine.exe").content == blob and c.get("/g/me").json()["engine"] == []
    assert c.get("/engine/evil.sh").status_code == 404
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert c.get("/g/me").json()["engine"] == ["pick-jam-engine.exe"]


def test_covers_sent_by_the_mac(lib):
    mod, c = lib
    h = {"Authorization": f"Bearer {TOKEN}"}
    name = "Band - Song [abcdefghijk]"
    img = b"\xff\xd8\xff" + b"j" * 500
    assert c.put("/sync/artimg/" + "a" * 20, content=img, headers={**h, "Content-Type": "image/jpeg"}).json()["ok"]
    assert c.get("/sync/art", headers=h).json()["files"] == ["a" * 20]
    assert c.put("/sync/art", headers=h, json={"art": {name: {"f": "a" * 20}, "x": {"u": "http://plain"}}}).json()["songs"] == 1
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    r = c.get("/g/art", params={"name": name})
    assert r.status_code == 200 and r.content == img and r.headers["content-type"].startswith("image/jpeg")
    c.put("/sync/art", headers=h, json={"art": {name: {"u": "https://img.example/cover.jpg"}}})
    r = c.get("/g/art", params={"name": name}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://img.example/cover.jpg"
    assert c.put("/sync/art", json={"art": {}}).status_code == 403


def test_whole_songs_from_the_pcs_music_folder(lib, tmp_path, monkeypatch):
    mod, c = lib
    music = tmp_path / "pcmusic"
    (music / "Band").mkdir(parents=True)
    subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=2", str(music / "Band" / "New.mp3")], check=True)
    monkeypatch.setattr(mod, "PCMUSIC", music)
    h = {"Authorization": f"Bearer {TOKEN}"}
    r = c.put("/sync/pcsongs", headers=h, json={"songs": [{"rel": "Band/New.mp3", "artist": "Band", "title": "New", "duration": 2.0},
                                                          {"rel": "../etc/passwd.mp3"}]}).json()
    assert r["songs"] == 1 and r["folder"]
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    row = [s for s in c.get("/g/songs").json()["songs"] if s["title"] == "New"][0]
    assert row["stems"] == ["other"] and row["whole"] and row["name"].startswith("pc~")
    assert c.get("/g/song", params={"name": row["name"]}).json()["whole"]
    assert c.get("/g/stem", params={"name": row["name"], "stem": "other"}).content[:4] == b"\x1aE\xdf\xa3"
    assert c.get("/g/stem", params={"name": row["name"], "stem": "drums"}).status_code == 404


def test_into_the_jam_without_a_second_login(lib, monkeypatch):
    import jam
    mod, c = lib
    h = {"Authorization": f"Bearer {TOKEN}"}
    c.post("/sync/status", headers=h, json={"on": True, "url": "https://neverlast.cz/?k=secret"})
    c.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert c.get("/g/me").json()["jam_url"] == "/g/jam-go"
    r = c.get("/g/jam-go", follow_redirects=False)
    loc = r.headers["location"]
    assert r.status_code == 302 and loc.startswith("https://neverlast.cz/sso?t=") and "secret" not in loc
    t = loc.split("t=", 1)[1]
    monkeypatch.setattr(jam, "conf", lambda: {"library_token": TOKEN})
    monkeypatch.setattr(jam, "users", lambda: {"Pavel": {}})
    assert jam._sso_user(t) == "Pavel"
    assert jam._sso_user(t) is None                                       # a ticket goes in once
    assert jam._sso_user(t[:-1] + ("0" if t[-1] != "0" else "1")) is None  # signed by someone else: no
