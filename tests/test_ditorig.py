"""Prints from DITORIG (Windows): its maps matched to Pick's songs by band and title, each
profile a song uses written as a print to check, the files found by their hash in the bank."""
import json
import sqlite3
import time

import server

SR = 48000
P1, P2 = "a" * 64, "b" * 64


def _source(base, title="Run to the Hills", artist="Iron Maiden", seconds=200, revision=3):
    root = base / "DITORIG" / "data" / "autotone-pilot"
    root.mkdir(parents=True)
    assets = base / "DITORIG" / "seed-tone-bank" / "assets"
    assets.mkdir(parents=True)
    for h in ("m1", "i1", "m2", "i2"):
        (assets / (h * 32 + (".nam" if h[0] == "m" else ".wav"))).write_bytes(b"RIFF" + h.encode() * 50)
    win = "F:\\DITORIG-ANALYZA\\DITORIG\\seed-tone-bank\\assets\\"
    profiles = {P1: {"id": P1, "name": "Marshall JCM800", "model_hash": "m1" * 32, "ir_hash": "i1" * 32, "input_gain_db": 3.0,
                     "output_gain": 0.6, "sample_rate": SR, "model_path": win + "m1" * 32 + ".nam", "ir_path": win + "i1" * 32 + ".wav"},
                P2: {"id": P2, "name": "Fender Clean", "model_hash": "m2" * 32, "ir_hash": "i2" * 32, "input_gain_db": 0.0,
                     "output_gain": 1.0, "sample_rate": SR, "model_path": win + "m2" * 32 + ".nam", "ir_path": win + "i2" * 32 + ".wav"}}
    seg = lambda a, b, p, cost: {"target": "g", "start_frame": a * SR, "end_frame": b * SR, "profile_version_id": p, "state": "x",
                                 "confidence": 0.5, "matching": {"profile_version_id": p, "cost": cost} if p else None}
    mp = {"song_id": "s1", "revision": revision, "timebase": {"unit": "frame", "sample_rate": SR}, "duration_frames": seconds * SR,
          "targets": [{"id": "g"}, {"id": "v"}],
          "segments": [seg(0, 5, None, None), seg(5, 150, P1, 0.3), seg(150, 190, P2, 0.2),
                       {**seg(0, 190, P2, 0.1), "target": "v"}]}               # the vocals' target: not a guitar
    manifest = {"id": "s1", "title": title, "artist": artist, "stems": [{"id": "g", "role": "guitar"}, {"id": "v", "role": "vocals"}]}
    con = sqlite3.connect(root / "pilot.sqlite")
    con.execute("create table songs(id text primary key, manifest text, state text, map text, error text)")
    con.execute("create table profiles(id text primary key, metadata text)")
    con.execute("insert into songs values(?,?,?,?,?)", ("s1", json.dumps(manifest), "Needs Review", json.dumps(mp), ""))
    for k, v in profiles.items():
        con.execute("insert into profiles values(?,?)", (k, json.dumps(v)))
    con.commit()
    con.close()
    return root


def _song(root, name, artist, title):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    (d / "meta.json").write_text(json.dumps({"artist": artist, "title": title}))
    return d


def _run(client, path):
    client.post("/api/stamps/ditorig", json={"path": str(path)})
    for _ in range(300):
        job = client.get("/api/stamps/ditorig").json()["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(job)


def test_maps_become_prints_of_the_matched_song(client, tmp_music_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    monkeypatch.setattr(server, "_write_config", lambda patch: patch)
    monkeypatch.setattr(server, "_audio_tags", lambda f: {"duration": 201.0})
    src = _source(tmp_path / "win")
    d = _song(tmp_music_root, "Run to the Hills - 2015 Remaster", "Iron Maiden", "Run to the Hills")
    job = _run(client, src.parent.parent.parent)             # the shared folder above it is enough
    assert job["status"] == "done", job
    r = job["result"]
    assert (r["added"], r["songs"], r["unmatched"], r["missing"], r["other_version"]) == (2, [d.name], [], [], [])
    prints = server._tone_prints_of(d)
    assert len(prints) == 2
    first = max(prints, key=lambda p: p["ditorig"]["cover"])
    assert first["ditorig"]["rank"] == 1 and first["core"]["id"].startswith("nam/DITORIG/") and first["match"] == 70
    assert first["drive_db"] == 3.0 and first["out_gain"] == 0.6 and first["review"] == "pending"
    assert [t["target"] for t in first["ditorig"]["timeline"]] == ["g", "g", "g"]     # only the guitar's stretches
    assert server._resolve_amp_path(first["core"]["id"]).is_file()
    # again: nothing new
    assert _run(client, src)["result"]["kept"] == 2 and len(server._tone_prints_of(d)) == 2


def test_an_unknown_song_and_another_version_are_told(client, tmp_music_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    monkeypatch.setattr(server, "_write_config", lambda patch: patch)
    monkeypatch.setattr(server, "_audio_tags", lambda f: {"duration": 260.0})
    src = _source(tmp_path / "win")
    r = _run(client, src)["result"]
    assert r["unmatched"] == ["Iron Maiden – Run to the Hills"]
    _song(tmp_music_root, "Run to the Hills", "Iron Maiden", "Run to the Hills")
    r = _run(client, src)["result"]
    assert r["added"] == 2 and len(r["other_version"]) == 1


def test_no_source_is_404(client, tmp_path):
    assert client.post("/api/stamps/ditorig", json={"path": str(tmp_path)}).status_code == 404


def test_the_archive_matches_exactly_by_the_card_folder_and_stem(client, tmp_music_root, tmp_path, monkeypatch):
    """KATALOG/knihovna.sqlite tells which folder of the card (and which stem) DITORIG analysed —
    the song is found by it even under a name nothing like the band and title."""
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    monkeypatch.setattr(server, "_write_config", lambda patch: patch)
    src = _source(tmp_path / "win", title="Run_to_the_Hills_abc123", artist="")
    kat = tmp_path / "win" / "KATALOG"
    kat.mkdir()
    con = sqlite3.connect(kat / "knihovna.sqlite")
    con.execute("create table tracks(id text primary key, song_id text, kind text, variant text, source text, organized text, bytes integer, identity text)")
    con.execute("create table tone_maps(id text primary key, song_id text, pilot_song_id text, revision integer, source_hash text, ready integer,"
                " review_state text, map text, manifest text, track_ids text, variant text, imported text)")
    con.execute("insert into tracks values('t1','x','guitar_rhythm','v',?, '', 1, '')",
                ("F:\\DITORIG-KNIHOVNA\\PICKSD\\spotdl\\Stems\\Maiden RTTH (live)\\guitar_rhythm.mp3",))
    con.execute("insert into tone_maps values('m','x','s1',3,'',1,'Needs Review','{}','{}',?,'v','')",
                (json.dumps([{"pilot_stem_id": "g", "track_id": "t1", "kind": "guitar_rhythm"}]),))
    con.commit()
    con.close()
    d = _song(tmp_music_root, "Maiden RTTH (live)", "", "whatever")
    (d / "guitar_rhythm.mp3").write_bytes(b"x")              # the same size as the archive says: the same file
    r = _run(client, src)["result"]
    assert (r["added"], r["songs"], r["exact"], r["other_version"]) == (2, [d.name], 1, [])
    prints = server._tone_prints_of(d)
    assert {p["stem"] for p in prints} == {"guitar_rhythm"}
    assert {p["category"] for p in prints} == {"other"}       # the test's stretches are of state "x"
    lib = client.get("/api/tone/prints").json()
    assert {x["source"] for x in lib} == {"ditorig"} and all(x["profile"] for x in lib)
