import subprocess
import zipfile

import server


def _tone(path, seconds=1):
    subprocess.run([server.ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}", str(path)], check=True)


def _song(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    _tone(d / "guitar.mp3")
    return d


def test_loose_files_are_matched_and_imported(client, tmp_music_root):
    # Moises results dropped into the inbox as two files: recognised by the
    # part words, paired with the right Steems song, imported, moved away.
    song = _song(tmp_music_root, "Metallica - One")
    _song(tmp_music_root, "Sepultura - Roots Bloody Roots")
    inbox = tmp_music_root / "Moises"
    inbox.mkdir()
    _tone(inbox / "Metallica - One - Lead Guitar.wav")
    _tone(inbox / "Metallica - One - Rhythm Guitar.wav")
    import os, time
    old = time.time() - 60
    for f in inbox.iterdir():
        os.utime(f, (old, old))

    st = client.get("/api/moises").json()
    g = st["groups"][0]
    assert g["label"] == "Metallica - One"
    assert g["match"] == "Metallica - One" and g["confident"] and g["complete"] and g["settled"]
    assert [t["song"] for t in st["todo"]] == ["Metallica - One", "Sepultura - Roots Bloody Roots"]

    res = client.post("/api/moises/import", json={"label": g["label"], "song": g["match"]})
    assert res.status_code == 200, res.text
    assert (song / "guitar_solo.mp3").stat().st_size > 1024
    assert (song / "guitar_rhythm.mp3").stat().st_size > 1024
    assert not list(inbox.glob("*.wav")) and len(list((inbox / "_hotovo").iterdir())) == 2
    assert [t["song"] for t in client.get("/api/moises").json()["todo"]] == ["Sepultura - Roots Bloody Roots"]


def test_a_downloaded_zip_is_unpacked_and_auto_imported(tmp_music_root, monkeypatch):
    song = _song(tmp_music_root, "Pisces")
    inbox = tmp_music_root / "Moises"
    inbox.mkdir()
    wav = tmp_music_root / "t.wav"
    _tone(wav)
    with zipfile.ZipFile(inbox / "Jinjer - Pisces.zip", "w") as z:
        z.write(wav, "Jinjer - Pisces/lead_guitar.wav")
        z.write(wav, "Jinjer - Pisces/rhythm_guitar.wav")
        z.write(wav, "Jinjer - Pisces/vocals.wav")
    monkeypatch.setattr(server, "_read_config", lambda: {"moises_auto": True})
    monkeypatch.setattr(server.time, "time", lambda: 10 ** 10)    # everything looks settled
    server._moises_auto_tick()
    assert (song / "guitar_solo.mp3").is_file() and (song / "guitar_rhythm.mp3").is_file()
    assert server._MOISES_EVENTS[-1]["song"] == "Pisces"


def test_an_unsure_match_is_left_for_the_user(tmp_music_root, monkeypatch):
    _song(tmp_music_root, "Korn - Blind")
    _song(tmp_music_root, "Korn - Blind [x81SMw8qUh0]")
    inbox = tmp_music_root / "Moises"
    inbox.mkdir()
    _tone(inbox / "Korn - Blind (Lead).wav")
    _tone(inbox / "Korn - Blind (Rhythm).wav")
    monkeypatch.setattr(server, "_read_config", lambda: {"moises_auto": True})
    monkeypatch.setattr(server.time, "time", lambda: 10 ** 10)
    server._moises_auto_tick()
    g = server._moises_scan()[0]
    assert not g["confident"]                    # two songs fit equally well
    assert {c["song"] for c in g["candidates"][:2]} == {"Korn - Blind", "Korn - Blind [x81SMw8qUh0]"}
    assert list(inbox.glob("*.wav"))             # untouched, waiting for a choice


def test_a_song_without_its_original_is_mixed_from_its_stems(client, tmp_music_root, monkeypatch):
    song = _song(tmp_music_root, "Ztracený originál")
    _tone(song / "drums.mp3")
    monkeypatch.setattr(server.subprocess, "run", _real_run_except_open())
    r = client.post("/api/moises/export", json={"songs": ["Ztracený originál"]}).json()
    assert r["mixed"] == ["Ztracený originál"] and not r["missing"]
    assert (tmp_music_root / "Moises" / "_k_nahrani" / "Ztracený originál.mp3").stat().st_size > 1024


def _real_run_except_open():
    real = subprocess.run
    def run(cmd, *a, **kw):
        if cmd and cmd[0] == "open":          # don't open Finder in tests
            class R: returncode = 0; stdout = ""; stderr = ""
            return R()
        return real(cmd, *a, **kw)
    return run


def test_manual_import_puts_both_parts_in_place_or_neither(client, tmp_music_root):
    song = _song(tmp_music_root, "Song")
    wav = tmp_music_root / "p.wav"
    _tone(wav)
    ok = client.post("/api/stems/Song/guitar-parts/import",
                     files={"solo": ("lead.wav", wav.read_bytes()), "rhythm": ("rhythm.wav", wav.read_bytes())})
    assert ok.status_code == 200, ok.text
    assert (song / "guitar_solo.mp3").is_file() and (song / "guitar_rhythm.mp3").is_file()
    assert not list(song.glob(".*incoming*"))
    # a broken second part: neither is replaced, nothing half-done is left
    (song / "guitar_solo.mp3").unlink(); (song / "guitar_rhythm.mp3").unlink()
    bad = client.post("/api/stems/Song/guitar-parts/import",
                      files={"solo": ("lead.wav", wav.read_bytes()), "rhythm": ("rhythm.wav", b"x" * 4096)})
    assert bad.status_code == 400
    assert not (song / "guitar_solo.mp3").exists() and not list(song.glob(".*incoming*"))
