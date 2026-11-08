"""The looper's projects (static/looper.js): saved and opened again, their recordings with them."""
import struct


def _wav(n=100):
    data = b"\x00\x00" * n
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16) \
        + b"data" + struct.pack("<I", len(data)) + data


def _project(files):
    return {"version": 1, "bpm": 120, "song": "Kapela - Píseň", "tracks": [
        {"name": "Kytara", "src": "gtr", "layers": [{"kind": "audio", "file": f} for f in files]},
        {"name": "Bicí", "src": "drums", "layers": [{"kind": "steps", "steps": {"bars": 1}}]}]}


def test_a_project_is_saved_with_its_recordings_and_opened_again(client, tmp_music_root):
    assert client.put("/api/looper/Můj loop/files/t0-l0.wav", content=_wav()).status_code == 200
    r = client.put("/api/looper/Můj loop", json=_project(["t0-l0.wav"]))
    assert r.status_code == 200 and r.json()["files"] == 1
    assert client.get("/api/looper/Můj loop").json()["bpm"] == 120
    w = client.get("/api/looper/Můj loop/files/t0-l0.wav")
    assert w.status_code == 200 and w.content[:4] == b"RIFF"
    lst = client.get("/api/looper").json()["projects"]
    assert lst[0]["name"] == "Můj loop" and lst[0]["tracks"] == 2 and lst[0]["song"] == "Kapela - Píseň"


def test_a_layer_left_out_lets_its_recording_go(client, tmp_music_root):
    client.put("/api/looper/P/files/a.wav", content=_wav())
    client.put("/api/looper/P/files/b.wav", content=_wav())
    client.put("/api/looper/P", json=_project(["a.wav", "b.wav"]))
    client.put("/api/looper/P", json=_project(["a.wav"]))
    assert client.get("/api/looper/P/files/b.wav").status_code == 404
    assert client.get("/api/looper/P/files/a.wav").status_code == 200


def test_a_project_naming_a_recording_it_does_not_have_is_refused(client, tmp_music_root):
    r = client.put("/api/looper/P", json=_project(["nic.wav"]))
    assert r.status_code == 400 and "nic.wav" in r.json()["error"]


def test_names_cannot_leave_the_looper_folder(client, tmp_music_root):
    assert client.put("/api/looper/x/files/..%2F..%2Fevil.wav", content=_wav()).status_code in (400, 404)
    assert client.put("/api/looper/x/files/a.mp3", content=_wav()).status_code == 400
    assert client.put("/api/looper/x/files/a.wav", content=b"not a wav at all, not at all.......................").status_code == 400
    assert client.put("/api/looper/..", json=_project([])).status_code in (400, 404, 405)
    assert client.put("/api/looper/%2E%2E", json=_project([])).status_code in (400, 404)
    assert not (tmp_music_root / "project.json").exists()
    assert not (tmp_music_root.parent / "evil.wav").exists()


def test_a_project_is_deleted(client, tmp_music_root):
    client.put("/api/looper/P", json=_project([]))
    assert client.delete("/api/looper/P").status_code == 200
    assert client.get("/api/looper/P").status_code == 404


# ---- the pads' own sounds, and pieces of any song
import json
import subprocess

import server


def _tone(path, secs=1.0, f=440):
    subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d={secs}", "-ac", "2", str(path)], check=True)


def test_a_sound_of_any_kind_is_kept_for_a_pad(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOOPER_SOUNDS_DIR", tmp_path / "sounds")
    _tone(tmp_path / "Beep.aiff")                                  # a format the browser cannot read by itself
    r = client.post("/api/looper/sounds", files={"audio": ("Beep.aiff", (tmp_path / "Beep.aiff").read_bytes())})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "Beep" and len(d["id"]) == 16
    f = client.get(f"/api/looper/sounds/{d['id']}")
    assert f.status_code == 200 and f.content[:4] == b"fLaC"
    assert client.post("/api/looper/sounds", files={"audio": ("x.wav", b"not audio at all")}).status_code == 422
    assert client.get("/api/looper/sounds/..%2Fsecret").status_code == 404


def test_a_song_is_offered_with_its_tempo_and_tuning_and_cut_into_pieces(client, tmp_music_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOOPER_ENV_DIR", tmp_path / "env")
    d = tmp_music_root / "Stems" / "Band - Song"
    d.mkdir(parents=True)
    _tone(d / "drums.mp3", 8, 200)
    _tone(d / "bass.mp3", 8, 80)
    (d / "beats.json").write_text(json.dumps({"beats": [i * 0.5 for i in range(16)], "downbeats": [0, 2, 4, 6], "bpm": 120.0, "beats_per_bar": 4, "grid_version": server.BEATGRID_VERSION}))
    (d / "tuning.json").write_text(json.dumps({"tuning": [62, 57, 53, 48, 43, 38]}))
    songs = client.get("/api/looper/songs").json()["songs"]
    s = next(x for x in songs if x["name"] == "Band - Song")
    assert s["bpm"] == 120.0 and s["tuning"] == [62, 57, 53, 48, 43, 38]
    g = client.get("/api/looper/song", params={"name": "Band - Song"}).json()
    assert g["stems"] == ["drums", "bass"] and g["downbeats"] == [0, 2, 4, 6] and 7.5 < g["dur"] < 8.5
    assert len(g["env"]["drums"]) > 600 and max(g["env"]["drums"]) > 10
    r = client.get("/api/looper/piece", params={"name": "Band - Song", "a": 2, "z": 4, "stems": "drums,bass"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    n = (len(r.content) - 44) / 8                                   # 32-bit float, stereo
    assert abs(n / 44100 - 2) < 0.05
    assert client.get("/api/looper/piece", params={"name": "../x", "a": 0, "z": 1, "stems": "drums"}).status_code == 404


def test_the_sample_folders_are_walked_once_and_again_only_when_asked(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCODE_CACHE_DIR", tmp_path / "transcode")
    d = tmp_path / "Samply" / "Bicí"
    d.mkdir(parents=True)
    _tone(d / "Kick 120bpm.wav", 0.2)
    assert client.post("/api/looper/samples/dirs", json={"path": str(tmp_path / "Samply")}).json()["added"]
    got = client.get("/api/looper/samples?all=1").json()
    assert [x["name"] for x in got["samples"]] == ["Kick 120bpm"] and got["samples"][0]["bpm"] == 120 and got["dirs"][0]["count"] == 1
    _tone(d / "Snare.aiff", 0.2)
    assert client.get("/api/looper/samples").json()["total"] == 1                # the kept list: opens at once
    assert client.get("/api/looper/samples?refresh=1").json()["total"] == 2
    top = client.get("/api/looper/samples/tree", params={"dir": 0}).json()      # a folder at a time
    assert top["folders"] == [{"name": "Bicí", "path": "Bicí", "count": 2}] and top["files"] == []
    inside = client.get("/api/looper/samples/tree", params={"dir": 0, "path": "Bicí", "kind": "loop"}).json()
    assert [x["name"] for x in inside["files"]] == ["Kick 120bpm"]
    found = client.get("/api/looper/samples/search", params={"q": "bicí snare"}).json()
    assert found["total"] == 1 and found["files"][0]["rel"] == "Bicí/Snare.aiff"
    f = client.get("/api/looper/samples/file", params={"dir": 0, "rel": "Bicí/Snare.aiff"})
    assert f.status_code == 200 and f.content[:4] == b"fLaC"                      # AIFF converted for the browser


def test_a_drum_hit_has_no_tempo_from_the_numbers_in_its_name():
    assert server._sample_meta("SumoSub 52_Vel1-RR1.wav", "Kicks/1 SumoSub/Individuals")["bpm"] is None
    assert server._sample_meta("Snare 44.wav", "Drums")["bpm"] is None
    assert server._sample_meta("Kick 120bpm Dry.wav")["bpm"] == 120              # said so: it counts
    assert server._sample_meta("Groove 120bpm Am.wav", "Loops") == {"bpm": 120.0, "key": "Am"}
    assert server._sample_meta("Riff_100_E.wav", "Guitar")["bpm"] == 100
