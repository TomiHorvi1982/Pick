"""Úložiště (more cards, where new songs go, formatting guarded) and Vizualizace (videos by theme, the clip)."""
from pathlib import Path

import server
import storage
import viz


def _card(tmp_path, name="Card2"):
    root = tmp_path / name / "spotdl"
    (root / "Stems").mkdir(parents=True)
    return root


def test_songs_on_another_card_are_listed_through_links(client, tmp_music_root, tmp_path):
    (tmp_music_root / "Stems" / "Here - Song").mkdir(parents=True)
    (tmp_music_root / "Stems" / "Here - Song" / "drums.mp3").write_bytes(b"x")
    card = _card(tmp_path)
    (card / "Stems" / "There - Song").mkdir()
    (card / "Stems" / "There - Song" / "drums.mp3").write_bytes(b"x")
    server._write_config({"storage": {"cards": [str(card)]}})
    names = [s["name"] for s in client.get("/api/stems").json()]
    assert names == ["Here - Song", "There - Song"]
    link = tmp_music_root / "Stems" / "There - Song"
    assert link.is_symlink() and server._resolve_under_music_root("Stems/There - Song/drums.mp3") is not None
    # the song gone from the card that is in: its link goes too
    (card / "Stems" / "There - Song" / "drums.mp3").unlink(); (card / "Stems" / "There - Song").rmdir()
    assert storage.link_sync()["gone"] == 1 and not link.is_symlink()


def test_a_card_that_is_out_hides_its_songs(client, tmp_music_root, tmp_path):
    card = _card(tmp_path)
    (card / "Stems" / "There - Song").mkdir()
    (card / "Stems" / "There - Song" / "drums.mp3").write_bytes(b"x")
    server._write_config({"storage": {"cards": [str(card)]}})
    (tmp_music_root / "Stems").mkdir()
    storage.link_sync()
    card.rename(tmp_path / "away")                       # pulled out
    assert client.get("/api/stems").json() == []
    assert (tmp_music_root / "Stems" / "There - Song").is_symlink()   # kept for when it is back


def test_new_songs_go_to_the_chosen_card(tmp_music_root, tmp_path, monkeypatch):
    card = _card(tmp_path)
    server._write_config({"storage": {"cards": [str(card)], "save_to": str(card)}})
    dest = tmp_music_root / "Stems" / "New - Song"; dest.parent.mkdir()
    real = storage.place_song(dest)
    assert real == card / "Stems" / "New - Song"
    real.mkdir(); storage.link_song(dest, real)
    assert dest.is_symlink() and dest.resolve() == real.resolve()
    monkeypatch.setattr(storage, "FREE_KEEP", 10 ** 18)  # a full card takes no more
    assert storage.place_song(tmp_music_root / "Stems" / "Other") == tmp_music_root / "Stems" / "Other"


def test_save_to_only_a_card_in_use(client, tmp_music_root, tmp_path):
    r = client.post("/api/storage/save-to", json={"root": str(tmp_path / "nope")})
    assert r.status_code == 400
    card = _card(tmp_path)
    server._write_config({"storage": {"cards": [str(card)]}})
    assert client.post("/api/storage/save-to", json={"root": str(card)}).json()["save_to"] == str(card)
    assert client.post("/api/storage/forget", json={"root": str(card)}).json()["ok"]
    assert server._read_config()["storage"] == {"cards": [], "save_to": ""}


def test_formatting_is_guarded(client, monkeypatch):
    started = []
    monkeypatch.setattr(storage, "_format", lambda d, n: started.append((d, n)))
    monkeypatch.setattr(storage, "disks", lambda: [{"disk": "disk9", "size": 1, "media": "SD", "bus": "USB", "volumes": ["Old"]}])
    monkeypatch.setattr(storage, "_holds_pick", lambda d: False)
    assert client.post("/api/storage/format", json={"disk": "disk9", "name": "PickSD2", "confirm": "ano"}).status_code == 400
    assert client.post("/api/storage/format", json={"disk": "disk9", "name": "Píšť/ka", "confirm": "SMAZAT"}).status_code == 400
    assert client.post("/api/storage/format", json={"disk": "disk0", "name": "PickSD2", "confirm": "SMAZAT"}).status_code == 400
    monkeypatch.setattr(storage, "_holds_pick", lambda d: True)
    assert client.post("/api/storage/format", json={"disk": "disk9", "name": "PickSD2", "confirm": "SMAZAT"}).status_code == 400
    monkeypatch.setattr(storage, "_holds_pick", lambda d: False)
    assert client.post("/api/storage/format", json={"disk": "disk9", "name": "PickSD2", "confirm": "SMAZAT"}).json()["ok"]
    import time; time.sleep(0.1)
    assert started == [("disk9", "PickSD2")]


def test_what_fills_a_card(tmp_path):
    root = tmp_path / "spotdl"
    (root / "Stems" / "A - B").mkdir(parents=True)
    (root / "Stems" / "A - B" / "drums.mp3").write_bytes(b"x" * 100000)
    (root / "Playlists" / "P").mkdir(parents=True)
    (root / "Playlists" / "P" / "s.mp3").write_bytes(b"x" * 50000)
    rows = {r["key"]: r for r in storage.count_pick(root)}
    assert rows["Stems"]["items"] == 1 and rows["Stems"]["bytes"] >= 100000 and rows["Stems"]["unit"] == "písní"
    assert rows["Playlists"]["items"] == 1 and rows["Playlists"]["bytes"] >= 50000


def test_viz_themes_videos_and_clip(client, tmp_music_root, monkeypatch):
    got = client.get("/api/viz").json()
    assert [t["name"] for t in got["themes"]] == sorted(viz.FIRST, key=str.lower)
    (tmp_music_root / "Vizualizace" / "Vesmír" / "a.mp4").write_bytes(b"\x00" * 10)
    got = client.get("/api/viz").json()
    assert next(t for t in got["themes"] if t["name"] == "Vesmír")["videos"] == ["a.mp4"]
    assert client.get("/api/viz/video/Vesmír/a.mp4").status_code == 200
    assert client.get("/api/viz/video/Vesmír/..%2Fx.mp4").status_code == 404
    assert client.post("/api/viz/theme", json={"name": "Oceán"}).json()["name"] == "Oceán"
    assert client.post("/api/viz/fetch", json={"theme": "Oceán", "url": "ne-odkaz"}).status_code == 400
    asked = []
    monkeypatch.setattr(viz, "search_videos", lambda q: asked.append(q) or [{"videoId": "abc123"}])
    assert client.get("/api/viz/clip", params={"title": "One", "artist": "Metallica"}).json()["videoId"] == "abc123"
    assert client.get("/api/viz/clip", params={"title": "One", "artist": "Metallica"}).json()["videoId"] == "abc123"
    assert asked == ["Metallica One official video"]                 # found once, kept
    assert client.delete("/api/viz/video/Vesmír/a.mp4").json()["ok"]


def test_free_videos_search_key_and_save_guard(client, tmp_music_root, monkeypatch):
    assert client.get("/api/viz/search", params={"q": ""}).json() == {"items": []}
    assert client.get("/api/viz/search", params={"q": "ocean", "source": "pexels"}).status_code == 400   # no key yet
    assert client.post("/api/viz/pexels-key", json={"key": "nope!"}).status_code == 400
    assert client.post("/api/viz/pexels-key", json={"key": "A" * 56}).json() == {"pexels": True}
    assert client.get("/api/viz").json()["pexels"] is True
    monkeypatch.setattr(viz, "_pexels", lambda q, page: [{"source": "pexels", "title": q, "url": "https://videos.pexels.com/x.mp4"}])
    assert client.get("/api/viz/search", params={"q": "fire", "source": "pexels"}).json()["items"][0]["title"] == "fire"
    client.get("/api/viz")                                   # the first themes
    r = client.post("/api/viz/save", json={"theme": "Vesmír", "url": "https://evil.example/x.mp4", "title": "x"})
    assert r.status_code == 400                              # only from the sources it searches
    saved = []
    monkeypatch.setattr(viz, "_save", lambda jid, d, req: saved.append((d.name, req.url)))
    assert client.post("/api/viz/save", json={"theme": "Vesmír", "url": "https://upload.wikimedia.org/a.webm", "title": "a"}).json()["id"]
    import time; time.sleep(0.1)
    assert saved == [("Vesmír", "https://upload.wikimedia.org/a.webm")]
    assert client.post("/api/viz/pexels-key", json={"key": ""}).json() == {"pexels": False}


def test_pixabay_key_and_search(client, monkeypatch):
    assert client.get("/api/viz/search", params={"q": "fire", "source": "pixabay"}).status_code == 400
    assert client.post("/api/viz/pixabay-key", json={"key": "AAAA"}).status_code == 400
    assert client.post("/api/viz/pixabay-key", json={"key": "12345678-" + "a1" * 13}).json() == {"pixabay": True}
    assert client.get("/api/viz").json()["pixabay"] is True

    class R:
        status_code, text = 200, ""
        def json(self):
            return {"hits": [{"id": 1, "tags": "fire, flame", "duration": 12, "user": "u", "pageURL": "p",
                              "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4", "width": 1280, "height": 720, "thumbnail": "t"}}},
                             {"id": 2, "tags": "upright", "videos": {"medium": {"url": "https://cdn.pixabay.com/u.mp4", "width": 720, "height": 1280}}}]}
    monkeypatch.setattr(viz.httpx, "get", lambda *a, **k: R())
    items = client.get("/api/viz/search", params={"q": "fire", "source": "pixabay"}).json()["items"]
    assert [(x["title"], x["url"]) for x in items] == [("Fire", "https://cdn.pixabay.com/v.mp4")]
