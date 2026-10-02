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


def test_two_recordings_of_one_song_are_two_files(client, tmp_music_root, monkeypatch):
    """Picking another recording of the same song for a tab used to find the
    first one "already downloaded" — and link its old stems again."""
    monkeypatch.setattr(server, "JOBS", {})
    a = client.post("/api/tabfiles/fetch-audio", json={"video_id": "abcdef12345", "name": "Korn - Blind"}).json()
    b = client.post("/api/tabfiles/fetch-audio", json={"video_id": "zyxwvu98765", "name": "Korn - Blind"}).json()
    assert a["rel"] != b["rel"]
    assert a["rel"] == "Songs/Taby/Korn - Blind [abcdef12345].mp3"
    assert "--embed-metadata" in server.JOBS[a["job_id"]]["cmd"]
    # The same recording again is reused, not downloaded twice.
    (tmp_music_root / a["rel"]).write_bytes(b"x")
    again = client.post("/api/tabfiles/fetch-audio", json={"video_id": "abcdef12345", "name": "Korn - Blind"}).json()
    assert again["ready"] is True and again["rel"] == a["rel"]


def test_the_clip_layout_is_kept_in_the_mix(client, song):
    clips = [{"src": "vocals", "offset": 0.0, "dur": 2.5, "start": 0.0},
             {"src": "vocals", "offset": 2.5, "dur": 1.0, "start": 6.0}]
    client.put("/api/stems/Song/session", json={"channels": [{"stem": "vocals", "clips": clips}]})
    back = client.get("/api/stems/Song/session").json()["channels"][0]
    assert back["clips"] == clips
    client.put("/api/stems/Song/session", json={"channels": [{"stem": "vocals"}]})
    assert client.get("/api/stems/Song/session").json()["channels"][0]["clips"] is None


def test_a_clip_can_be_kept_in_the_cap_folder(client, tmp_music_root):
    first = client.post("/api/library/cap?name=Song – vocals 0:12.0", content=_wav_bytes()).json()
    second = client.post("/api/library/cap?name=Song – vocals 0:12.0", content=_wav_bytes()).json()
    assert first["rel"].startswith("CAP/") and first["rel"].endswith(".wav")
    assert first["rel"] != second["rel"]
    assert (tmp_music_root / first["rel"]).read_bytes()[:4] == b"RIFF"
    assert client.post("/api/library/cap?name=x", content=b"not a wav" * 200).status_code == 400


def test_a_clip_saved_as_a_nam_riff_stays_a_wav(client, tmp_music_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "NAM_RIFFS_DIR", tmp_path / "riffs")
    monkeypatch.setattr(server, "NAM_RIFFS_INDEX", tmp_path / "riffs.json", raising=False)
    res = client.post("/api/nam-preview/riffs?name=Dílek", content=_wav_bytes()).json()
    rid = res["riff"]["id"]
    assert (tmp_path / "riffs" / f"{rid}.wav").is_file()


def test_a_beat_grid_set_from_a_clip_replaces_the_detected_one(client, song):
    grid = {"beats": [0.5, 1.0, 1.5, 2.0, 2.5], "downbeats": [0.5, 2.5], "bpm": 120, "beats_per_bar": 4,
            "detected_from": 0.5, "detected_to": 2.5}
    saved = client.put("/api/stems/Song/beats", json=grid).json()
    assert saved["detector"] == "clip" and saved["bpm"] == 120
    back = client.get("/api/stems/Song/beats").json()
    assert back["beats"] == grid["beats"] and back["downbeats"] == grid["downbeats"]
    assert client.put("/api/stems/Song/beats", json={**grid, "bpm": 5}).status_code == 400


@pytest.mark.parametrize("fmt,bits,codec", [("wav", 24, "pcm_s24le"), ("flac", 16, "flac"), ("mp3", 16, "mp3")])
def test_a_mix_export_lands_in_the_export_folder_in_the_asked_format(client, tmp_music_root, fmt, bits, codec):
    import subprocess
    res = client.post(f"/api/library/export?name=Korn - Blind&fmt={fmt}&rate=48000&bits={bits}",
                      content=_wav_bytes(1.0)).json()
    assert res["rel"] == f"Export/Korn - Blind.{fmt}"
    probe = subprocess.run([server.ffmpeg_bin().replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries",
                            "stream=codec_name,sample_rate", "-of", "csv=p=0", str(tmp_music_root / res["rel"])],
                           capture_output=True, text=True).stdout.strip()
    assert probe.split(",")[0] == codec and "48000" in probe
    again = client.post(f"/api/library/export?name=Korn - Blind&fmt={fmt}", content=_wav_bytes(1.0)).json()
    assert again["rel"] == f"Export/Korn - Blind (2).{fmt}"


def test_an_export_refuses_garbage_and_unknown_formats(client, tmp_music_root):
    assert client.post("/api/library/export?fmt=wav", content=b"nope" * 400).status_code == 400
    assert client.post("/api/library/export?fmt=exe", content=_wav_bytes()).status_code == 400
