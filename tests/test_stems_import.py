"""Songs separated elsewhere and put into Stems by hand are taken in: a folder of their own each, files linked, not copied."""
import json
import os

import stems_import as si


def _f(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_three_ways_of_laying_songs_out(client, tmp_music_root):
    root = tmp_music_root / "Stems"
    for n in ("vocals", "drums", "bass", "lead_guitar", "rhythm_guitar", "other", "full_song", "backing"):
        _f(root / "Sepultura" / "01 Arise" / f"{n}.wav")
    for n in ("vocals", "drums"):
        _f(root / "Sepultura" / "07 Refuse_Resist" / f"{n}.wav")
    for n in ("01 Bass - Killing Spree", "01 Killing Spree - Lead Guitars", "01 Lead Guitars - Killing Spree",
              "01 Drums - Killing Spree", "01 Vocals - Killing Spree", "01 Killing Spree", "01 Killing Spree (1)"):
        _f(root / "Death-All Songs Steems from Moises" / f"{n}.mp3")
    for n in ("Bass", "Drums", "Rhythm Guitars", "Metronome", "Vocals"):
        _f(root / "Trivium-Shogun album Steems from Moises" / "Shogun-B minor-140bpm-440hz" / f"Shogun-{n}-B minor-140bpm-440hz.mp3")
    # a song Pick has separated itself already
    for n in ("vocals", "drums", "bass", "guitar", "piano", "other"):
        _f(root / "Sepultura - Arise" / f"{n}.mp3")

    songs = {s["name"]: s for s in client.get("/api/stems").json()}
    arise = songs["Sepultura - Arise (Moises)"]
    assert sorted(arise["stems"]) == ["bass", "drums", "guitar_rhythm", "guitar_solo", "other", "vocals"] and arise["complete"]
    assert arise["meta"]["artist"] == "Sepultura" and arise["meta"]["title"] == "Arise"
    assert songs["Sepultura - Refuse Resist"]["meta"]["title"] == "Refuse/Resist"
    assert sorted(songs["Death - Killing Spree"]["stems"]) == ["bass", "drums", "guitar_solo", "vocals"]
    sho = songs["Trivium - Shogun"]
    assert sorted(sho["stems"]) == ["bass", "drums", "guitar_rhythm", "vocals"] and sho["meta"]["album"] == "Shogun"
    assert "Sepultura" not in songs and "Death-All Songs Steems from Moises" not in songs     # the folders themselves are no songs
    # linked, not copied: the same file on the disk; the player's folders left as they were
    a, b = root / "Sepultura - Arise (Moises)" / "vocals.wav", root / "Sepultura" / "01 Arise" / "vocals.wav"
    assert os.stat(a).st_ino == os.stat(b).st_ino and b.is_file()
    # a set deleted in Pick does not come back; a song added later does
    import shutil
    shutil.rmtree(root / "Death - Killing Spree")
    for n in ("vocals", "drums"):
        _f(root / "Sepultura" / "02 Territory" / f"{n}.wav")
    songs = {s["name"] for s in client.get("/api/stems").json()}
    assert "Death - Killing Spree" not in songs and "Sepultura - Territory" in songs
    reg = json.loads((root / si.REGISTRY).read_text())
    assert "Sepultura" in reg["containers"] and len(reg["songs"]) == 5


def test_what_a_file_is_by_its_name():
    assert si.part_of("01 Lead Guitars - X") == "guitar_solo" and si.part_of("rhythm_guitar") == "guitar_rhythm"
    assert si.part_of("Shogun-Vocals-B minor-140bpm-440hz") == "vocals" and si.part_of("guitar") == "guitar"
    assert si.part_of("Shogun-Metronome-B minor-140bpm-440hz") == "" and si.part_of("full_song") == ""
    assert si.part_of("01 Killing Spree") is None
    assert si.band_and_album("Trivium-Shogun album Steems from Moises") == ("Trivium", "Shogun")
    assert si.band_and_album("Death-All Songs Steems from Moises") == ("Death", "")


def test_songs_taken_in_find_their_album(client, tmp_music_root, monkeypatch):
    import server
    root = tmp_music_root / "Stems"
    for t in ("01 Arise", "02 Roots Bloody Roots", "03 Nothing Like It"):
        for n in ("vocals", "drums"):
            _f(root / "Sepultura" / t / f"{n}.wav")
    asked = []

    def disco(name):
        asked.append(name)
        return {"albums": [{"title": "Arise", "kind": "studio", "tracks": [{"title": "Arise"}]},
                           {"title": "Roots", "kind": "studio", "tracks": [{"title": "Roots Bloody Roots"}]},
                           {"title": "Live", "kind": "live", "tracks": [{"title": "Arise (Live)"}]}]}
    monkeypatch.setattr(server, "_discography", disco)
    monkeypatch.setattr(server, "_official_song", lambda a, t, n: None)        # nor in the catalogue
    songs = {s["name"]: s for s in client.get("/api/stems").json()}
    assert songs["Sepultura - Arise"]["imported"] and songs["Sepultura - Arise"]["meta"]["album"] is None
    assert server._stems_albums_fill(root) == 2 and asked == ["Sepultura"]       # the band asked once
    songs = {s["name"]: s for s in client.get("/api/stems").json()}
    assert songs["Sepultura - Arise"]["meta"]["album"] == "Arise"
    assert songs["Sepultura - Roots Bloody Roots"]["meta"]["album"] == "Roots"
    assert songs["Sepultura - Nothing Like It"]["meta"]["album"] is None
    assert server._stems_albums_fill(root) == 0 and asked == ["Sepultura"]       # not found is not asked again


def test_songs_taken_in_count_as_there_beside_an_open_mix(client, tmp_music_root):
    import server
    for n in ("vocals", "drums", "bass"):
        _f(tmp_music_root / "Stems" / "Death" / "03 Zombie Ritual" / f"{n}.wav")
    client.get("/api/stems")
    al = server._have_all()
    got = server._have_one(server.SaveSongRequest(title="Zombie Ritual", artists="Death"), al)
    assert got["stems"] and got["name"] == "Death - Zombie Ritual"
