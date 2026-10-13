"""Zpěv mým hlasem: the vocals of a song sung in the player's voice — a job in
the background whose result is a track of the song; a stretch can be redone."""
import json
import shutil
import subprocess
import sys
import time

import pytest

import server

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

# Stands in for the model: what goes in comes out, twice as loud, told the way the real one tells it.
WORKER = '''
import argparse, shutil, wave, sys
ap = argparse.ArgumentParser()
for k in ("source", "ref", "out", "steps", "shift", "auto"): ap.add_argument("--" + k)
a = ap.parse_args()
print("LOADED", flush=True)
w = wave.open(a.source); n = w.getnframes(); raw = w.readframes(n); w.close()
import array
s = array.array("h", raw)
s = array.array("h", [max(-32767, min(32767, v * 2)) for v in s])
o = wave.open(a.out, "wb"); o.setnchannels(1); o.setsampwidth(2); o.setframerate(44100); o.writeframes(s.tobytes()); o.close()
print("PROGRESS 1", flush=True)
print("DONE 0.1", flush=True)
'''


def _tone(path, seconds, freq=220, volume=2.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-af", f"volume={volume}", str(path)], check=True)


@pytest.fixture
def song(tmp_music_root, tmp_path, monkeypatch):
    d = tmp_music_root / "Stems" / "Kapela - Píseň"
    d.mkdir(parents=True)
    _tone(d / "vocals.mp3", 6)
    (d / "drums.mp3").write_bytes(b"x")
    voice = server._own_voices_dir() / "v1"
    voice.mkdir(parents=True)
    _tone(voice / "ref.wav", 3)
    (voice / "name.txt").write_text("Tomáš", encoding="utf-8")
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    monkeypatch.setattr(server, "SVC_PYTHON", type(server.SVC_PYTHON)(sys.executable))
    monkeypatch.setattr(server, "SVC_WORKER", worker)
    monkeypatch.setattr(server, "SVC_DIR", tmp_path)
    monkeypatch.setitem(server._SVC, "job", None)
    return d


def _wait(client):
    for _ in range(200):
        job = client.get("/api/myvoice").json()["job"]
        if job and job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job never ended")


def test_the_vocals_become_a_track_in_my_voice(client, song):
    st = client.get("/api/myvoice?song=Kapela - Píseň").json()
    assert st["ready"] and st["has_vocals"] and st["kept"] == {}
    assert st["voices"] == [{"id": "v1", "name": "Tomáš", "sung": False}]
    r = client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1", "steps": 12})
    assert r.status_code == 200 and r.json()["status"] == "running"
    job = _wait(client)
    assert job["status"] == "done", job
    kept = json.loads((song / "myvoice.json").read_text())
    assert kept["stem"] == job["stem"] == "take-1" and kept["steps"] == 12
    assert (song / "take-1.mp3").is_file()
    listed = next(s for s in client.get("/api/stems").json() if s["name"] == "Kapela - Píseň")
    assert "take-1" in listed["stems"] and listed["labels"]["take-1"] == "Můj hlas (Tomáš)"
    # as long as the vocals, and as loud as them — not as loud as the model made it
    made, was = server._svc_pcm(song / "take-1.mp3"), server._svc_pcm(song / "vocals.mp3")
    assert abs(len(made) - len(was)) < 4410
    rms = lambda x: float((x ** 2).mean() ** 0.5)
    assert abs(rms(made) - rms(was)) < 0.02


def test_a_stretch_is_redone_into_the_same_track(client, song):
    client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1"})
    assert _wait(client)["status"] == "done"
    before = server._svc_pcm(song / "take-1.mp3")
    _tone(song / "vocals.mp3", 6, freq=880)                    # what is sung there is now something else
    r = client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1", "start": 2.0, "end": 4.0})
    assert r.status_code == 200
    job = _wait(client)
    assert job["status"] == "done" and job["stem"] == "take-1"
    assert sorted(p.name for p in song.glob("take-*")) == ["take-1.mp3"]
    after = server._svc_pcm(song / "take-1.mp3")
    n = min(len(before), len(after))
    diff = lambda a, b: float(abs(before[a * 44100:b * 44100][:n] - after[a * 44100:b * 44100][:n]).mean())
    assert diff(2, 4) > 0.05          # the stretch changed…
    assert diff(0, 1) < 0.02 and diff(5, 6) < 0.02            # …and the rest did not


def test_what_cannot_be_sung_is_said(client, song, monkeypatch):
    assert client.post("/api/stems/Nic/myvoice", json={"voice": "v1"}).status_code == 404
    assert client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "nikdo"}).status_code == 404
    assert client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "../v1"}).status_code == 404
    assert client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1", "start": 1, "end": 1.2}).status_code == 400
    (song / "vocals.mp3").unlink()
    assert client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1"}).status_code == 400
    monkeypatch.setattr(server, "SVC_DIR", song / "missing")
    assert client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1"}).status_code == 503
    assert client.get("/api/myvoice").json()["ready"] is False


def test_a_failed_job_says_why_and_leaves_no_track(client, song, monkeypatch, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('print("LOADED", flush=True)\nprint("ERROR RuntimeError: došla paměť", flush=True)\nraise SystemExit(1)\n')
    monkeypatch.setattr(server, "SVC_WORKER", bad)
    client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1"})
    job = _wait(client)
    assert job["status"] == "error" and "došla paměť" in job["error"]
    assert not list(song.glob("take-*")) and not (song / "myvoice.json").exists()


def test_a_sample_of_me_singing_is_used_instead_of_speech(client, song, tmp_path):
    _tone(tmp_path / "zpev.wav", 8, freq=330)
    with open(tmp_path / "zpev.wav", "rb") as f:
        got = client.post("/api/myvoice/sample/v1", files={"file": ("zpev.wav", f, "audio/wav")}).json()
    assert got["voices"][0]["sung"] is True
    assert server._svc_voice_file("v1").name == "sing.wav"
    assert client.delete("/api/myvoice/sample/v1").json()["voices"][0]["sung"] is False
    assert server._svc_voice_file("v1").name == "ref.wav"
    assert client.post("/api/myvoice/sample/nikdo", files={"file": ("a.wav", b"x" * 2000, "audio/wav")}).status_code == 404


def test_a_voice_is_made_from_two_readings_and_a_sung_take(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OWN_TTS_PYTHON", type(server.OWN_TTS_PYTHON)(sys.executable))

    def ref(src, dest, lang="auto"):                       # stands in for cutting the clip and writing down its words
        shutil.copy(src, dest / "ref.wav")
        (dest / "ref.txt").write_text("Příliš žluťoučký kůň", encoding="utf-8")
        return "Příliš žluťoučký kůň"
    monkeypatch.setattr(server, "_voice_ref_make", ref)
    guide = client.get("/api/tts/voice-guide").json()
    assert guide["ready"] and "kůň" in guide["texts"]["cs"] and "fox" in guide["texts"]["en"]
    for n, f in (("cs", 200), ("en", 260), ("sing", 440)):
        _tone(tmp_path / f"{n}.wav", 14, freq=f)
    files = lambda *names: {n: (f"{n}.wav", (tmp_path / f"{n}.wav").read_bytes(), "audio/wav") for n in names}
    got = client.post("/api/tts/voice-guide?name=Tomáš", files=files("cs", "en", "sing")).json()
    assert got["name"] == "Tomáš" and got["sung"] and got["for_singing"] and got["ref_text"].startswith("Příliš")
    d = server._own_voices_dir() / got["id"]
    assert sorted(p.name for p in d.iterdir()) == ["guide.json", "name.txt", "read-cs.wav", "read-en.wav", "ref.txt", "ref.wav", "sing.wav"]
    # what the singing model is given: 25 seconds, the sung take first
    assert abs(len(server._svc_pcm(d / "sing.wav")) / 44100 - 25) < 0.5
    assert {"id": got["id"], "name": "Tomáš", "sung": True} in client.get("/api/myvoice").json()["voices"]
    # without a sung take the readings alone make it
    plain = client.post("/api/tts/voice-guide", files=files("cs", "en")).json()
    assert plain["sung"] is False and plain["for_singing"] and plain["name"] == "Můj hlas"
    # too little voice is said, and nothing is left behind
    _tone(tmp_path / "en.wav", 2)
    r = client.post("/api/tts/voice-guide", files=files("cs", "en"))
    assert r.status_code == 400 and "anglickém" in r.json()["error"]
    assert len(list(server._own_voices_dir().iterdir())) == 2


def test_my_take_of_this_song_becomes_its_model(client, song, tmp_path, monkeypatch):
    used = []
    real = server._svc_run
    monkeypatch.setattr(server, "_svc_run", lambda job, d, vocals, ref: (used.append(ref.name), real(job, d, vocals, ref)))
    assert client.get("/api/myvoice?song=Kapela - Píseň").json()["sample"] is None
    _tone(tmp_path / "ja.wav", 30, freq=300)
    with open(tmp_path / "ja.wav", "rb") as f:
        got = client.post("/api/stems/Kapela - Píseň/myvoice/sample", files={"file": ("ja.wav", f, "audio/wav")}).json()
    assert abs(got["seconds"] - 25) < 0.5                    # as much as the model takes
    st = client.get("/api/myvoice?song=Kapela - Píseň").json()
    assert st["sample"]["seconds"] == got["seconds"] and client.get(st["sample"]["url"]).status_code == 200
    client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1"})
    job = _wait(client)
    assert job["status"] == "done" and job["own"] is True and used == ["myvoice-sample.wav"]
    # asked not to, or with the take gone, the voice's own sample is the model again
    client.post("/api/stems/Kapela - Píseň/myvoice", json={"voice": "v1", "own": False})
    assert _wait(client)["own"] is False and used[-1] == "ref.wav"
    assert client.delete("/api/stems/Kapela - Píseň/myvoice/sample").json() == {"ok": True}
    assert client.get("/api/myvoice?song=Kapela - Píseň").json()["sample"] is None
    # the track of the song is untouched by the take: it is no stem
    assert "myvoice-sample" not in str(next(s for s in client.get("/api/stems").json() if s["name"] == "Kapela - Píseň")["stems"])
    _tone(tmp_path / "kratke.wav", 2)
    with open(tmp_path / "kratke.wav", "rb") as f:
        assert client.post("/api/stems/Kapela - Píseň/myvoice/sample", files={"file": ("k.wav", f, "audio/wav")}).status_code == 400
    assert client.post("/api/stems/Nic/myvoice/sample", files={"file": ("k.wav", b"x" * 2000, "audio/wav")}).status_code == 404
