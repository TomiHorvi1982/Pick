"""Artist / title / album of a stem set: typed by hand, from the tags, or
guessed from the folder name — and never by renaming the folder."""
import json

import server


def _stems(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    return d


def _meta(client, name):
    return next(s["meta"] for s in client.get("/api/stems").json() if s["name"] == name)


def test_a_folder_name_is_guessed_into_artist_and_title(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_STEMS_SOURCE_INDEX", {"at": 0.0, "map": {}})
    _stems(tmp_music_root, "Metallica - Master Of Puppets")
    _stems(tmp_music_root, "06 Propaganda")
    assert _meta(client, "Metallica - Master Of Puppets") == {
        "artist": "Metallica", "title": "Master Of Puppets", "album": None, "source": "name"}
    m = _meta(client, "06 Propaganda")
    assert (m["artist"], m["title"], m["source"]) == (None, "Propaganda", "name")


def test_tags_of_the_library_file_win_over_the_guess(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_STEMS_SOURCE_INDEX", {"at": 0.0, "map": {}})
    _stems(tmp_music_root, "06 Propaganda")
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "Songs" / "06 Propaganda.mp3").write_bytes(b"x")
    monkeypatch.setattr(server, "_audio_tags", lambda p: {"artist": "Sepultura", "title": "Propaganda", "album": "Chaos A.D."})
    assert _meta(client, "06 Propaganda") == {
        "artist": "Sepultura", "title": "Propaganda", "album": "Chaos A.D.", "source": "tags"}


def test_typed_meta_wins_and_clearing_it_goes_back_to_automatic(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_STEMS_SOURCE_INDEX", {"at": 0.0, "map": {}})
    d = _stems(tmp_music_root, "Pisces")
    res = client.put("/api/stems/Pisces/meta", json={"artist": " Jinjer ", "title": "Pisces", "album": "King of Everything"})
    assert res.json()["meta"] == {"artist": "Jinjer", "title": "Pisces", "album": "King of Everything", "source": "manual"}
    assert _meta(client, "Pisces")["source"] == "manual"
    assert d.is_dir() and (d / "meta.json").is_file()        # the folder keeps its name
    assert json.loads((d / "meta.json").read_text())["manual"] is True
    # meta.json is not mistaken for a stem
    assert next(s for s in client.get("/api/stems").json() if s["name"] == "Pisces")["complete"] is True

    back = client.put("/api/stems/Pisces/meta", json={"artist": "", "title": "", "album": ""}).json()
    assert back["meta"]["source"] == "name"
    assert not (d / "meta.json").exists()


def test_meta_refuses_unknown_or_escaping_names(client, tmp_music_root):
    assert client.put("/api/stems/Nope/meta", json={"title": "x"}).status_code == 404
    assert client.put("/api/stems/..%2F..%2Fetc/meta", json={"title": "x"}).status_code == 404
