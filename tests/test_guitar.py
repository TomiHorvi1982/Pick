"""The live guitar channel: recorded takes and the amp library.

The audio itself lives in the browser — a Web Audio graph is not something a
backend test can hold — so what is tested here is everything the browser talks
to: where a take ends up, what it is allowed to be called, and the containment
around a folder full of files the user drops in.
"""

import json

import pytest

import server


# ------------------------------------------------------------------- takes

def test_a_take_becomes_a_track_of_the_song(client, tmp_music_root, monkeypatch):
    """The whole point of recording a guitar part here is that it is still a
    track after the guitar is unplugged."""
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "vocals.mp3").write_bytes(b"x" * 2048)

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kw):
        # ffmpeg's job here is only the encode; write what it would have.
        out = cmd[cmd.index(str(stems / "take-1.mp3"))]
        (stems / "take-1.mp3").write_bytes(b"m" * 4096)
        return Done()

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    res = client.post("/api/stems/Song/record", content=b"RIFF" + b"0" * 4096)

    assert res.status_code == 200
    assert res.json()["stem"] == "take-1"
    assert (stems / "take-1.mp3").is_file()
    # And the song now has one more track than it was separated into.
    listed = [s for s in client.get("/api/stems").json() if s["name"] == "Song"]
    assert "take-1" in listed[0]["stems"]


def test_takes_are_numbered_from_the_gap(tmp_music_root):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    for n in (1, 2, 4):
        (stems / f"take-{n}.mp3").write_bytes(b"x")

    assert server._takes_in(stems) == ["take-1", "take-2", "take-4"]
    assert server._next_take_name(stems) == "take-3"


def test_an_empty_recording_is_refused(client, tmp_music_root):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    assert client.post("/api/stems/Song/record", content=b"tiny").status_code == 400


def test_a_take_streams_like_any_other_track(client, tmp_music_root):
    """It used to 404: the stream route checked the name against the six
    separated stems, so a recording could be saved and then not played."""
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "take-1.mp3").write_bytes(b"m" * 4096)

    assert client.get("/api/stems/Song/take-1").status_code == 200


def test_only_takes_can_be_deleted(client, tmp_music_root):
    """A stray request must not be able to delete a separated stem."""
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "vocals.mp3").write_bytes(b"x" * 2048)
    (stems / "take-1.mp3").write_bytes(b"x" * 2048)

    assert client.request("DELETE", "/api/stems/Song/record/vocals").status_code == 400
    assert (stems / "vocals.mp3").is_file()

    assert client.request("DELETE", "/api/stems/Song/record/take-1").status_code == 200
    assert not (stems / "take-1.mp3").exists()


@pytest.mark.parametrize("name", ["take-x", "take-99999", "../vocals", "takes-1"])
def test_a_name_that_is_not_a_take_does_not_stream(client, tmp_music_root, name):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    assert client.get(f"/api/stems/Song/{name}").status_code in (400, 404)


# -------------------------------------------------------------- amp library

def test_the_amp_library_lists_models_and_impulses(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    server.AMPS_DIR.mkdir()
    (server.AMPS_DIR / "jcm.nam").write_text("{}")
    (server.AMPS_DIR / "ampeg.wav").write_bytes(b"RIFF")
    (server.AMPS_DIR / "notes.txt").write_text("ignore me")

    got = client.get("/api/amps").json()
    assert {(a["name"], a["kind"]) for a in got} == {("jcm", "model"), ("ampeg", "ir")}


def test_only_models_and_impulses_are_accepted(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    res = client.post("/api/amps", params={"filename": "evil.sh"}, content=b"#!/bin/sh")
    assert res.status_code == 400


def test_an_uploaded_model_comes_back(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    payload = json.dumps({"architecture": "WaveNet"}).encode()

    stored = client.post("/api/amps", params={"filename": "My Amp.nam"},
                         content=payload).json()
    assert stored["kind"] == "model"

    back = client.get(f"/api/amps/{stored['file']}")
    assert back.status_code == 200
    assert json.loads(back.content)["architecture"] == "WaveNet"


def test_an_amp_name_cannot_walk_out_of_the_folder(client, tmp_path, monkeypatch):
    """The name comes from a file picker, which is to say from anywhere."""
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    client.post("/api/amps", params={"filename": "../../escaped.nam"},
                content=b"{}")

    assert not (tmp_path / "escaped.nam").exists()
    assert not (tmp_path.parent / "escaped.nam").exists()


def test_the_nam_engine_is_served_from_our_own_origin(client):
    """A worklet module cannot be loaded cross-origin, and the engine fetches
    its wasm relative to the glue — so both have to come from here."""
    for name, ctype in (("index.js", "text/javascript"),
                        ("nam-worklet.js", "text/javascript"),
                        ("nam-engine.wasm", "application/wasm")):
        res = client.get(f"/static/nam/{name}")
        assert res.status_code == 200, name
        assert ctype in res.headers["content-type"]


def test_the_nam_route_serves_nothing_else(client):
    assert client.get("/static/nam/server.py").status_code == 400
    assert client.get("/static/nam/nope.js").status_code == 404
