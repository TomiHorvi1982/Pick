"""Any audio file as a track of a song (or a mix of its own), and tracks
edited in the mixer saved back with the original kept for a restore."""
import io
import math
import struct
import wave

import pytest

import server


def _wav_bytes(seconds=0.5, freq=440, rate=22050):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


@pytest.fixture
def song(tmp_music_root):
    d = tmp_music_root / "Stems" / "Song"
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x" * 2048)
    return d


def _listed(client, name):
    return next(s for s in client.get("/api/stems").json() if s["name"] == name)


def test_a_file_from_the_computer_becomes_a_named_track(client, song):
    res = client.post("/api/stems/Song/tracks/upload",
                      files={"file": ("Moje sólo.wav", _wav_bytes(), "audio/wav")}).json()
    assert res["stem"] == "take-1" and res["label"] == "Moje sólo"
    assert (song / "take-1.mp3").stat().st_size > 1024
    s = _listed(client, "Song")
    assert "take-1" in s["stems"] and s["labels"] == {"take-1": "Moje sólo"}


def test_a_library_file_becomes_a_track_and_garbage_is_refused(client, song, tmp_music_root):
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "Songs" / "Riff.wav").write_bytes(_wav_bytes())
    assert client.post("/api/stems/Song/tracks/library", json={"rel": "Songs/Riff.wav"}).json()["label"] == "Riff"
    (tmp_music_root / "Songs" / "fake.mp3").write_bytes(b"not audio" * 300)
    bad = client.post("/api/stems/Song/tracks/library", json={"rel": "Songs/fake.mp3"})
    assert bad.status_code == 400 and "Převod" in bad.json()["error"]
    assert client.post("/api/stems/Song/tracks/library", json={"rel": "../etc/passwd"}).status_code == 404


def test_a_file_can_start_a_mix_of_its_own(client, tmp_music_root):
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "Songs" / "Jam.wav").write_bytes(_wav_bytes())
    first = client.post("/api/stems/new/library", json={"rel": "Songs/Jam.wav"}).json()
    again = client.post("/api/stems/new/library", json={"rel": "Songs/Jam.wav"}).json()
    assert (first["name"], again["name"]) == ("Jam", "Jam (2)")
    s = _listed(client, "Jam")
    assert s["stems"] == ["take-1"]
    assert s["missing"] == [] and s["complete"] is True      # not an "incomplete separation"
    assert s["meta"]["title"] == "Jam"


def test_an_edit_is_saved_and_the_original_can_be_restored(client, song):
    original = (song / "vocals.mp3").read_bytes()
    res = client.put("/api/stems/Song/vocals/audio", content=_wav_bytes(1.0, 220)).json()
    assert res["edited"] == ["vocals"]
    assert (song / ".orig" / "vocals.mp3").read_bytes() == original
    assert (song / "vocals.mp3").read_bytes() != original
    # A second edit keeps the very first original, not the previous edit.
    client.put("/api/stems/Song/vocals/audio", content=_wav_bytes(0.4, 330))
    assert (song / ".orig" / "vocals.mp3").read_bytes() == original
    assert _listed(client, "Song")["edited"] == ["vocals"]

    back = client.post("/api/stems/Song/vocals/restore").json()
    assert back["edited"] == []
    assert (song / "vocals.mp3").read_bytes() == original
    assert not (song / ".orig").exists()


def test_edits_refuse_unknown_stems_and_odd_names(client, song):
    assert client.put("/api/stems/Song/nope/audio", content=_wav_bytes()).status_code == 404
    assert client.put("/api/stems/Song/..%2Fx/audio", content=_wav_bytes()).status_code in (400, 404)
    assert client.post("/api/stems/Song/drums/restore").status_code == 404


def test_deleting_an_imported_track_drops_its_name(client, song):
    client.post("/api/stems/Song/tracks/upload", files={"file": ("A.wav", _wav_bytes(), "audio/wav")})
    client.delete("/api/stems/Song/record/take-1")
    assert _listed(client, "Song")["labels"] == {}
