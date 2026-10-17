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
