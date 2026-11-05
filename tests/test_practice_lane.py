"""The Steems lane Cvičení: "Co tam hraje?" (a stretch of a guitar stem heard note by note) and
Časování (the player's runs against the record's guitar, kept with the song)."""
import json
import math
import struct
import wave

import server


def _song(root, name="Riff"):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    with wave.open(str(d / "guitar.wav"), "wb") as w:            # 3 s of an A, a real file for ffmpeg to cut
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 110 * i / 22050))) for i in range(22050 * 3)))
    (d / "tuning.json").write_text(json.dumps({"tuning": [64, 59, 55, 50, 45, 38]}))
    return d


def test_what_plays_hears_only_the_stretch_and_tells_the_tuning(client, tmp_music_root, monkeypatch, tmp_path):
    _song(tmp_music_root)
    fake = tmp_path / "python"; fake.write_text("")
    monkeypatch.setattr(server, "MIDI_PYTHON", fake)
    heard = {}

    def notes_of(path):
        with wave.open(str(path)) as w:
            heard["s"] = w.getnframes() / w.getframerate()
        return [{"m": 45, "t": 0.1, "d": 0.5, "v": 0.8}, {"m": 52, "t": 5.0, "d": 0.5, "v": 0.8}]
    monkeypatch.setattr(server, "_notes_of", notes_of)
    r = client.post("/api/whatplays/Riff", json={"stem": "guitar", "start": 1.0, "end": 2.5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert abs(heard["s"] - 1.5) < 0.05                       # only the stretch was cut
    assert d["notes"] == [{"m": 45, "t": 1.1, "d": 0.5, "v": 0.8}]   # on the song's clock; past the end dropped
    assert d["tuning"] == [64, 59, 55, 50, 45, 38]


def test_what_plays_refuses_what_it_cannot_do(client, tmp_music_root, monkeypatch, tmp_path):
    _song(tmp_music_root)
    fake = tmp_path / "python"; fake.write_text("")
    monkeypatch.setattr(server, "MIDI_PYTHON", fake)
    assert client.post("/api/whatplays/Riff", json={"stem": "guitar", "start": 0, "end": 40}).status_code == 400
    assert client.post("/api/whatplays/Riff", json={"stem": "../x", "start": 0, "end": 2}).status_code == 400
    assert client.post("/api/whatplays/Riff", json={"stem": "bass", "start": 0, "end": 2}).status_code == 404
    assert client.post("/api/whatplays/Nope", json={"stem": "guitar", "start": 0, "end": 2}).status_code == 404


def test_timing_runs_are_kept_newest_first_and_cleared(client, tmp_music_root):
    d = _song(tmp_music_root)
    assert client.get("/api/timing/Riff").json() == {"runs": []}
    for i in range(14):
        r = client.post("/api/timing/Riff", json={"n": i, "median": -10, "within": 80, "hits": [[1.0, -10]], "sections": []})
        assert r.status_code == 200
    runs = client.get("/api/timing/Riff").json()["runs"]
    assert len(runs) == 12 and runs[0]["n"] == 13 and "at" in runs[0]
    assert (d / "practice" / "timing.json").is_file()
    assert client.post("/api/timing/Riff", json={"no": "hits"}).status_code == 400
    assert client.delete("/api/timing/Riff").json() == {"runs": []}
    assert client.get("/api/timing/Nope").status_code == 404
