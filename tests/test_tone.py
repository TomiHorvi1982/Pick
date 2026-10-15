"""Tónový otisk: the takes, the fit with its worker, the stretches suggested, the blind test's mixes,
and the engine itself (tone_worker.py --selftest) when its venv, Node and the amps are here.
Only made-up sound — sine tones and noise, nobody's song."""
import json
import math
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

import server
import sysload


def _wav(path, x, rate=48000, channels=1):
    x = np.clip(np.asarray(x, np.float32), -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def _tone(seconds, hz=110.0, rate=48000, level=0.3):
    t = np.arange(int(seconds * rate)) / rate
    return level * np.sin(2 * math.pi * hz * t)


def _song(root, name="Kapela - Píseň", seconds=30):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    _wav(d / "guitar.wav", _tone(seconds), 44100)
    _wav(d / "drums.wav", _tone(seconds, 60, level=0.05), 44100)
    return d


def _wait(client, url="/api/tone/job"):
    for _ in range(200):
        got = client.get(url).json()
        if (got.get("job") or {}).get("status") != "running":
            return got
        time.sleep(0.05)
    return got


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """A worker of our own in place of the real one: it reports its stages and writes a print."""
    script = tmp_path / "fake_tone_worker.py"
    script.write_text("""
import json, sys, wave
from pathlib import Path
job = json.loads(Path(sys.argv[2]).read_text())
out = Path(job["out"]); out.mkdir(parents=True, exist_ok=True)
print("STAGE listen 0 0", flush=True)
print("STAGE sieve 3 5", flush=True)
def silent(p, n):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(job["sr"]); w.writeframes(b"\\0\\0" * n)
silent(out / "print-ir.wav", 2048)
silent(out / "you.wav", job["sr"] * 10)
again = bool(job.get("start_from"))
(out / "print.json").write_text(json.dumps({"version": 1, "core": {"id": "Amp.nam", "name": "Amp"}, "drive_db": 3.0,
    "pre": {"hp": 90, "mid_db": 1, "tilt_db": 0}, "ir": "print-ir.wav", "room": None, "width": {"on": False, "corr": 1.0},
    "match": 91 if again else 84, "takes_seen": len(job["takes"]), "offsets": [t["offset"] for t in job["takes"]]}))
print("DONE 84", flush=True)
""", encoding="utf-8")
    monkeypatch.setattr(server, "TONE_PYTHON", Path(sys.executable))
    monkeypatch.setattr(server, "TONE_WORKER", script)
    monkeypatch.setattr(server, "_tone_ready", lambda: True)
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path / "amps")
    monkeypatch.setattr(sysload, "room", lambda need, what="": (True, ""))
    return script


def _take(client, name, start, seconds=12.0, tmp=None):
    f = tmp / "take.wav"
    _wav(f, _tone(seconds, 82.4))
    with open(f, "rb") as fh:
        return client.post(f"/api/tone/{name}/take?start={start}&gain_db=2.5", files={"file": ("take.wav", fh, "audio/wav")})


def test_a_take_is_kept_on_the_songs_clock(client, tmp_music_root, tmp_path):
    d = _song(tmp_music_root)
    r = _take(client, "Kapela - Píseň", 5.0, tmp=tmp_path)
    assert r.status_code == 200, r.text
    t = r.json()["take"]
    assert t["file"] == "take-1.wav" and t["start"] == 5.0 and t["sr"] == 48000 and abs(t["dur"] - 12) < 0.05 and t["gain_db"] == 2.5
    state = client.get("/api/tone/Kapela - Píseň").json()
    assert state["stems"] == ["guitar"] and [x["file"] for x in state["takes"]] == ["take-1.wav"] and state["print"] is None
    assert client.get("/api/tone/Kapela - Píseň/file/take-1.wav").status_code == 200
    assert client.get("/api/tone/Kapela - Píseň/file/..%2Fguitar.wav").status_code == 404
    assert client.delete("/api/tone/Kapela - Píseň/take/take-1.wav").json()["takes"] == []
    assert not (d / "tone" / "take-1.wav").exists()
    assert client.get("/api/tone/Nic - Tu").status_code == 404


def test_a_print_is_fitted_only_when_it_can_be(client, tmp_music_root, tmp_path, fake_engine, monkeypatch):
    _song(tmp_music_root)
    url = "/api/tone/Kapela - Píseň/fit"
    assert client.post(url, json={"start": 5, "end": 17}).status_code == 400                    # nothing played yet
    _take(client, "Kapela - Píseň", 30.0, tmp=tmp_path)
    assert client.post(url, json={"start": 5, "end": 17}).status_code == 400                    # played elsewhere
    assert client.post(url, json={"start": 5, "end": 7}).status_code == 400                     # too short
    assert client.post(url, json={"start": 5, "end": 17, "stem": "vocals"}).status_code == 400  # not a guitar
    assert client.post(url, json={"start": 30, "end": 40, "refine": True}).status_code == 400   # nothing to refine
    monkeypatch.setattr(sysload, "room", lambda need, what="": (False, "Volná paměť je teď 0.8 GB"))
    r = client.post(url, json={"start": 30, "end": 40})
    assert r.status_code == 503 and "paměť" in r.json()["error"]
    monkeypatch.setattr(server, "_tone_ready", lambda: False)
    assert client.post(url, json={"start": 30, "end": 40}).status_code == 503


def test_a_print_is_made_kept_refined_and_taken_back(client, tmp_music_root, tmp_path, fake_engine):
    d = _song(tmp_music_root)
    _take(client, "Kapela - Píseň", 4.0, tmp=tmp_path)
    r = client.post("/api/tone/Kapela - Píseň/fit", json={"start": 5, "end": 15})
    assert r.status_code == 200, r.text
    got = _wait(client)
    assert got["job"]["status"] == "done" and got["job"]["match"] == 84, got
    p = client.get("/api/tone/Kapela - Píseň").json()["print"]
    assert p["core"]["name"] == "Amp" and p["start"] == 5 and p["end"] == 15 and p["stem"] == "guitar"
    assert p["takes"] == ["take-1.wav"] and p["gain_db"] == 2.5 and p["offsets"] == [-1.0] and p["song"] == "Kapela - Píseň"
    assert client.get("/api/tone/Kapela - Píseň/file/print-ir.wav").status_code == 200
    assert [x["song"] for x in client.get("/api/tone/prints").json()] == ["Kapela - Píseň"]
    assert client.post("/api/tone/Kapela - Píseň/undo").status_code == 404                     # nothing before it
    # refined: the one before is kept, and can come back
    assert client.post("/api/tone/Kapela - Píseň/fit", json={"start": 5, "end": 15, "refine": True}).status_code == 200
    assert _wait(client)["job"]["status"] == "done"
    state = client.get("/api/tone/Kapela - Píseň").json()
    assert state["print"]["match"] == 91 and state["print"]["before"] == 84 and state["prev"]
    assert client.post("/api/tone/Kapela - Píseň/undo").json()["print"]["match"] == 84
    assert json.loads((d / "tone" / "prev-print.json").read_text())["match"] == 91


def test_a_failing_engine_says_why(client, tmp_music_root, tmp_path, fake_engine):
    _song(tmp_music_root)
    _take(client, "Kapela - Píseň", 0.0, tmp=tmp_path)
    fake_engine.write_text('print("ERROR V úseku jsem slyšel hrát vás oba jen 1 s", flush=True)\nraise SystemExit(1)\n', encoding="utf-8")
    assert client.post("/api/tone/Kapela - Píseň/fit", json={"start": 0, "end": 10}).status_code == 200
    got = _wait(client)["job"]
    assert got["status"] == "error" and "jen 1 s" in got["error"]


def test_the_stretches_suggested_are_where_the_guitar_leads(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Kapela - Riff"
    d.mkdir(parents=True)
    rate, n = 44100, 60
    g, rest = np.zeros(rate * n), np.zeros(rate * n)
    t = np.arange(rate * n) / rate
    g[rate * 20:rate * 34] = 0.4 * np.sin(2 * math.pi * 110 * t[rate * 20:rate * 34])     # the guitar alone 20–34 s
    g[rate * 40:] = 0.2 * np.sin(2 * math.pi * 110 * t[rate * 40:])
    rest[:rate * 18] = 0.4 * np.sin(2 * math.pi * 55 * t[:rate * 18])
    rest[rate * 40:] = 0.5 * np.sin(2 * math.pi * 55 * t[rate * 40:])
    _wav(d / "guitar.wav", g, rate)
    _wav(d / "bass.wav", rest, rate)
    got = client.get("/api/tone/Kapela - Riff/sections").json()["sections"]
    assert got and 19 <= got[0]["start"] <= 23 and got[0]["end"] <= 35, got
    assert client.get("/api/tone/Kapela - Riff/sections?stem=vocals").status_code == 404


def test_the_blind_test_hears_the_band_with_either_guitar(client, tmp_music_root, tmp_path, fake_engine):
    d = _song(tmp_music_root)
    _take(client, "Kapela - Píseň", 0.0, tmp=tmp_path)
    assert client.post("/api/tone/Kapela - Píseň/abx").status_code == 404
    client.post("/api/tone/Kapela - Píseň/fit", json={"start": 2, "end": 10})
    assert _wait(client)["job"]["status"] == "done"
    got = client.post("/api/tone/Kapela - Píseň/abx").json()
    assert got == {"a": "abx-record.wav", "b": "abx-you.wav"}
    with wave.open(str(d / "tone" / "abx-record.wav")) as a, wave.open(str(d / "tone" / "abx-you.wav")) as b:
        assert a.getnframes() == b.getnframes() == 8 * 44100 and a.getnchannels() == 2
        ra = np.frombuffer(a.readframes(a.getnframes()), "<i2").astype(float)
        rb = np.frombuffer(b.readframes(b.getnframes()), "<i2").astype(float)
    assert np.abs(ra).max() > np.abs(rb).max()          # the made-up print is silent: only the drums left


@pytest.mark.skipif(not (server.TONE_PYTHON.is_file() and server._node_bin()), reason="engine otisku tu není")
def test_the_engine_finds_a_known_chain_again():
    """A made-up riff through a known core, drive, EQ, cabinet and room: the fit has to find them."""
    r = subprocess.run([str(server.TONE_PYTHON), str(server.TONE_WORKER), "--selftest"], capture_output=True, text=True, timeout=900)
    out = r.stdout
    if "SKIP" in out:
        pytest.skip(out.strip())
    assert r.returncode == 0 and "SELFTEST OK" in out, out[-2000:]
