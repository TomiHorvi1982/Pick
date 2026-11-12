"""Pick DJ live separation (djlive.py): a song without stems opens as its mix,
its windows come from the worker on demand and in the background, and a song
separated to the end is handed to Steems through the normal finalisation.

The worker here is a stand-in speaking dj_separator.py's protocol (no torch):
each stem's samples are the mix's left channel times (stem index + 1) / 10, so
what comes back can be checked exactly. ffmpeg is real (it makes mix.wav and
encodes the MP3s)."""

import json
import shutil
import struct
import sys
import time
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import djlive

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").is_file() else None)
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

CORE = 20000
FAKE_WORKER = r'''
import json, sys, wave
CORE = %d
out = sys.stdout.buffer
out.write((json.dumps({"ready": True, "core": CORE, "stems": %r}) + "\n").encode()); out.flush()
for line in sys.stdin:
    req = json.loads(line)
    with wave.open(req["path"], "rb") as w:
        n = w.getnframes(); raw = w.readframes(n)
    import array
    a = array.array("h"); a.frombytes(raw)
    start = req["k"] * CORE; frames = min(CORE, n - start)
    left = a[0::2][start:start + frames]
    planes = array.array("h")
    for s in range(6):
        plane = array.array("h", (int(v * (s + 1) / 10) for v in left))
        planes.extend(plane); planes.extend(plane)
    data = planes.tobytes()
    out.write((json.dumps({"ok": True, "k": req["k"], "start": start, "frames": frames, "bytes": len(data)}) + "\n").encode())
    out.write(data); out.flush()
''' % (CORE, djlive.STEMS)


@pytest.fixture
def live(tmp_path, monkeypatch):
    music = tmp_path / "music"
    (music / "Songs").mkdir(parents=True)
    src = music / "Songs" / "New Song.wav"
    frames = 50000                                  # 3 windows: 20000, 20000, 10000
    with wave.open(str(src), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<hh", (i % 2000) - 1000, 0) for i in range(frames)))
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)
    finalized = []
    raw_root = music / "Stems" / ".raw" / "htdemucs_6s"
    monkeypatch.setattr(djlive, "LIVE_ROOT", tmp_path / "dj-live")
    monkeypatch.setattr(djlive, "resolve", lambda rel: (music / rel) if (music / rel).is_file() else None)
    monkeypatch.setattr(djlive, "interpreter", lambda: sys.executable)
    monkeypatch.setattr(djlive, "worker_script", script)
    monkeypatch.setattr(djlive, "ffmpeg", lambda: FFMPEG)
    monkeypatch.setattr(djlive, "stems_ready", lambda name: False)
    monkeypatch.setattr(djlive, "stems_busy", lambda name: False)
    monkeypatch.setattr(djlive, "raw_track_dir", lambda source: raw_root / Path(source).stem)
    monkeypatch.setattr(djlive, "finalize", finalized.append)
    monkeypatch.setattr(djlive, "WORKER", djlive.Worker())
    monkeypatch.setattr(djlive, "FILL", djlive.Filler())
    app = FastAPI()
    app.include_router(djlive.router)
    yield TestClient(app), finalized, raw_root
    djlive.WORKER.stop()


def wait_for(cond, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_a_song_opens_as_its_mix_and_its_windows_come_from_the_worker(live):
    client, finalized, raw_root = live
    r = client.post("/api/dj/live/open", json={"rel": "Songs/New Song.wav"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["frames"] == 50000 and info["core"] == CORE and info["windows"] == 3 and info["stems"] == djlive.STEMS

    mix = client.get(f"/api/dj/live/{info['id']}/mix")
    assert mix.status_code == 200 and mix.content[:4] == b"RIFF"

    w = client.get(f"/api/dj/live/{info['id']}/window/2")
    assert w.status_code == 200
    assert w.headers["X-DJ-Start"] == "40000" and w.headers["X-DJ-Frames"] == "10000"
    pcm = struct.unpack(f"<{len(w.content) // 2}h", w.content)
    assert len(pcm) == 6 * 2 * 10000
    first = (40000 % 2000) - 1000                      # the mix's left sample at frame 40000
    assert pcm[0] == int(first * 1 / 10)               # vocals = left × 0.1
    assert pcm[5 * 2 * 10000] == int(first * 6 / 10)   # other = left × 0.6

    assert client.get(f"/api/dj/live/{info['id']}/window/3").status_code == 416

    # The rest is separated in the background and the song lands in Steems as six MP3s.
    assert wait_for(lambda: client.get(f"/api/dj/live/{info['id']}/status").json()["saved"])
    assert finalized == [{"stems_source": str(raw_root.parent.parent.parent / "Songs" / "New Song.wav"), "stems_name": "New Song"}]
    mp3s = sorted(p.name for p in (raw_root / "New Song").iterdir())
    assert mp3s == sorted(f"{s}.mp3" for s in djlive.STEMS)


def test_a_song_already_in_steems_opens_with_its_stems(live, monkeypatch):
    client, _, _ = live
    monkeypatch.setattr(djlive, "stems_ready", lambda name: name == "New Song")
    r = client.post("/api/dj/live/open", json={"rel": "Songs/New Song.wav"})
    assert r.json() == {"separated": "New Song"}


def test_nothing_outside_the_library_and_no_unknown_songs(live):
    client, _, _ = live
    assert client.post("/api/dj/live/open", json={"rel": "../etc/passwd"}).status_code == 404
    assert client.get("/api/dj/live/nothere/mix").status_code == 404
    assert client.get("/api/dj/live/../x/mix").status_code == 404


def test_interleave():
    left = struct.pack("<3h", 1, 2, 3)
    right = struct.pack("<3h", -1, -2, -3)
    assert struct.unpack("<6h", djlive.interleave(left, right)) == (1, -1, 2, -2, 3, -3)
