"""Kontakt: the libraries' plain sounds sorted and guarded; Kontakt's window and its renders (the host faked)."""
import json
import subprocess
from pathlib import Path

import kontakt


def _libs():
    root = kontakt.LIBS
    loops = root / "Impact_Soundworks_Riffage_Metal" / "Samples WAV" / "Axis_080BPM" / "Axis_080BPM_Loops_F#"
    loops.mkdir(parents=True)
    for n in ("AXIS_DRY_F#_080_44_01.wav", "AXIS_AMP_F#_080_44_01.wav"):
        (loops / n).write_bytes(b"RIFF0000WAVE")
    (root / "Impact_Soundworks_Riffage_Metal" / "Riffage - Metal.nicnt").write_bytes(b"x")
    one = root / "Axe Machina [Soundiron]" / "Samples" / "a" / "Plucks"
    one.mkdir(parents=True)
    (one / "pluck_E2.wav").write_bytes(b"RIFF0000WAVE")
    (root / "Heavyocity - Damage Guitars").mkdir()
    (root / "Heavyocity - Damage Guitars" / "Instruments.nkx").write_bytes(b"locked")
    return loops


def test_what_a_path_says():
    d = kontakt.describe("Impact_Soundworks_Riffage_Metal/Samples WAV/Axis_160BPM/Axis_160BPM_Loops_D#/AXIS_DRY_D#_160_44_12.wav")
    assert (d["bpm"], d["key"], d["kind"], d["loop"]) == (160, "D#", "dry", True)
    assert kontakt.describe("Axe Machina [Soundiron]/Samples/a/Plucks/pluck_E2.wav")["kind"] == ""
    b = kontakt.describe("Clean Guitars [Beastsamples]/Guitar Loops/#1 - Monday Freak - Cm - 80 bpm by Beastsamples.wav")
    assert (b["bpm"], b["key"], b["loop"]) == (80, "Cm", True)


def test_sounds_filtered_and_guarded(client):
    _libs()
    got = client.get("/api/kontakt/sounds").json()
    assert got["total"] == 3 and {l["name"]: l["sounds"] for l in got["libs"]} == {"Axe Machina": 1, "Damage Guitars": 0, "Riffage Metal": 2}
    assert [x["kind"] for x in client.get("/api/kontakt/sounds", params={"kind": "dry"}).json()["items"]] == ["dry"]
    assert client.get("/api/kontakt/sounds", params={"loops": "true", "bpm": 80}).json()["total"] == 2
    assert client.get("/api/kontakt/sounds", params={"q": "pluck"}).json()["total"] == 1
    path = "Impact_Soundworks_Riffage_Metal/Samples WAV/Axis_080BPM/Axis_080BPM_Loops_F#/AXIS_DRY_F#_080_44_01.wav"
    assert client.get("/api/kontakt/file", params={"path": path}).status_code == 200
    assert client.get("/api/kontakt/file", params={"path": "../../etc/passwd"}).status_code == 404
    assert client.get("/api/kontakt/file", params={"path": "Heavyocity - Damage Guitars/Instruments.nkx"}).status_code == 404


def test_a_sound_into_steems(client, monkeypatch):
    _libs()
    made = []
    monkeypatch.setattr(kontakt, "new_project", lambda src, label: made.append((src.name, label)) or {"name": label})
    path = "Impact_Soundworks_Riffage_Metal/Samples WAV/Axis_080BPM/Axis_080BPM_Loops_F#/AXIS_DRY_F#_080_44_01.wav"
    assert client.post("/api/kontakt/to-steems", json={"path": path}).json()["name"] == "Riffage Metal AXIS DRY F# 080 44 01 80 BPM F#"
    assert made[0][0] == "AXIS_DRY_F#_080_44_01.wav"


def test_kontakt_window_and_render(client, tmp_path, monkeypatch):
    assert client.post("/api/kontakt/presets", json={"name": "x"}).status_code == 503        # no Kontakt here
    plug = tmp_path / "Kontakt 8.vst3"; plug.mkdir()
    py = tmp_path / "python"; py.write_text("")
    monkeypatch.setattr(kontakt, "PLUGINS", [plug]); monkeypatch.setattr(kontakt, "HOST_PY", py)

    def host(args, timeout):
        if args[0] == "editor":
            Path(args[2]).write_bytes(b"state")
        else:
            Path(args[4]).write_bytes(b"RIFF")
            seen.append(json.loads(Path(args[3]).read_text())["notes"][0])
        return subprocess.CompletedProcess(args, 0, "", "")
    seen = []
    monkeypatch.setattr(kontakt, "_host", host)
    pid = client.post("/api/kontakt/presets", json={"name": "Riffage rytmická"}).json()["id"]
    import time
    for _ in range(50):
        if kontakt.JOB["status"] != "running":
            break
        time.sleep(0.02)
    st = client.get("/api/kontakt").json()
    assert st["plugin"] == "Kontakt 8.vst3" and [p["name"] for p in st["presets"]] == ["Riffage rytmická"]
    assert client.post("/api/kontakt/render", json={"preset": pid, "notes": []}).status_code == 400
    r = client.post("/api/kontakt/render", json={"preset": pid, "notes": [{"m": 40, "t": 0, "d": 0.5}, {"m": 999, "t": 0, "d": 1}]}).json()
    assert r["cached"] is False
    assert client.post("/api/kontakt/render", json={"preset": pid, "notes": [{"m": 40, "t": 0, "d": 0.5}]}).json() == {"file": r["file"], "cached": True}
    assert seen == [[40, 0.0, 0.5, 100]]
    # an octave up: the low keys of some instruments switch articulations instead of playing
    assert client.post(f"/api/kontakt/presets/{pid}/shift", json={"shift": 7}).status_code == 400
    assert client.post(f"/api/kontakt/presets/{pid}/shift", json={"shift": 12}).json()["shift"] == 12
    assert client.get("/api/kontakt").json()["presets"][0]["shift"] == 12
    up = client.post("/api/kontakt/render", json={"preset": pid, "notes": [{"m": 40, "t": 0, "d": 0.5}]}).json()
    assert up["cached"] is False and up["file"] != r["file"] and seen[-1] == [52, 0.0, 0.5, 100]
    assert client.get(f"/api/kontakt/render/{r['file']}").status_code == 200
    assert client.get("/api/kontakt/render/..%2Fx.wav").status_code == 404
    assert client.delete(f"/api/kontakt/presets/{pid}").json()["ok"] and client.get("/api/kontakt").json()["presets"] == []


def test_riffs_keep_a_kontakt_or_midi_sound(client):
    base = {"bpm": 120, "div": 4, "tuning": [64, 59, 55, 50, 45, 40], "cols": [{"s": {"5": 0}}]}
    r = client.post("/api/vguitar/riffs", json={"name": "k", "riff": {**base, "sound": {"kontakt": "0123456789"}}}).json()
    assert r["sound"] == {"kontakt": "0123456789"}
    r = client.post("/api/vguitar/riffs", json={"name": "m", "riff": {**base, "sound": {"midi": "IAC-1"}}}).json()
    assert r["sound"] == {"midi": "IAC-1"}
