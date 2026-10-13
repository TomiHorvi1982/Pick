"""Úpravy vzhledu: the look the player made is kept, named looks beside it,
its pictures as files — and a separated song says where it is from."""
import server
import look


def _stems(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    return d


def _song(client, name):
    return next(s for s in client.get("/api/stems").json() if s["name"] == name)


def test_a_look_is_kept_and_named_looks_beside_it(client):
    assert client.get("/api/look").json() == {"look": {}, "kept": []}
    mine = {"v": 1, "rules": [{"sel": "#sb-logo", "props": {"color": "#ff0000"}}]}
    assert client.put("/api/look", json=mine).json() == {"ok": True}
    assert client.get("/api/look").json()["look"] == mine
    assert client.put("/api/look/kept/Noční metal", json=mine).json()["kept"] == ["Noční metal"]
    assert client.get("/api/look/kept/Noční metal").json()["look"] == mine
    assert client.delete("/api/look/kept/Noční metal").json()["kept"] == []
    assert client.get("/api/look/kept/Noční metal").status_code == 404


def test_a_look_that_is_no_look_is_refused(client):
    assert client.put("/api/look", content=b"[1,2]").status_code == 400
    assert client.put("/api/look", content=b"x" * (look.LOOK_MAX + 1)).status_code == 413
    assert look._kept_file("../../x").parent == look.LOOK_DIR / "kept" and look._kept_file(" .. ") is None


def test_an_own_picture_is_kept_served_listed_and_removed(client):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    got = client.post("/api/look/image?kind=ref", files={"file": ("vzor.png", png, "image/png")}).json()
    assert got["name"].endswith(".png") and got["kind"] == "ref" and got["title"] == "vzor.png"
    assert client.get(got["url"]).content == png
    assert [i["name"] for i in client.get("/api/look/images").json()] == [got["name"]]
    client.delete(f"/api/look/image/{got['name']}")
    assert client.get(got["url"]).status_code == 404
    assert client.get("/api/look/images").json() == []


def test_what_is_no_picture_is_not_kept_and_names_cannot_wander(client):
    assert client.post("/api/look/image", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400
    assert client.get("/api/look/image/..%2Flook.json").status_code == 404
    assert client.get("/api/look/image/look.json").status_code == 404


def test_a_found_picture_is_taken_only_from_a_known_source(client, monkeypatch):
    assert client.post("/api/look/take", json={"src": "elsewhere", "id": "1"}).status_code == 404
    assert client.get("/api/look/find?src=elsewhere&q=x").status_code == 404
    monkeypatch.setattr(look, "_FIND", {**look._FIND, "nasa": lambda q, page: [{"id": "a1", "thumb": "t", "title": q}]})
    assert client.get("/api/look/find?src=nasa&q=mlhovina").json() == {"items": [{"id": "a1", "thumb": "t", "title": "mlhovina"}]}
    assert client.get("/api/look/find?src=nasa&q=").json() == {"items": []}
    # an id that is no id never reaches the internet
    assert look._full_url("iconify", "../../etc") is None
    assert look._full_url("picsum", "1/../../x") is None
    assert look._full_url("iconify", "lucide:guitar", "#ff0000") == "https://api.iconify.design/lucide/guitar.svg?color=%23ff0000"


def test_fonts_are_offered_even_with_no_internet(client, monkeypatch):
    monkeypatch.setattr(look, "_fonts_fetch", lambda: [])
    got = client.get("/api/look/fonts").json()
    assert got[0]["f"] == "Inter" and len(got) == len(look.FONTS_BASIC)
    monkeypatch.setattr(look, "_fonts_fetch", lambda: [{"f": "Metal Mania", "c": "display", "w": [400], "p": 1, "s": "google"}])
    assert client.get("/api/look/fonts").json()[0]["f"] == "Metal Mania"
    monkeypatch.setattr(look, "_fonts_fetch", lambda: 1 / 0)        # kept: not asked again
    assert client.get("/api/look/fonts").json()[0]["f"] == "Metal Mania"


def test_the_design_mode_script_is_served(client):
    r = client.get("/static/look.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_a_song_says_its_playlist_and_whether_it_is_czech(client, tmp_music_root):
    for n in ("Kabat - Colorado", "Kabat - Žízeň", "Sepultura - Roots", "Lucie - Amerika", "Olympic - Okno"):
        _stems(tmp_music_root, n)
    pl = tmp_music_root / "Playlists" / "České hity  Czech songs"
    pl.mkdir(parents=True)
    (pl / "Lucie - Amerika.mp3").write_bytes(b"x")
    (tmp_music_root / "Playlists" / "Metal").mkdir()
    (tmp_music_root / "Playlists" / "Metal" / "Sepultura - Roots.mp3").write_bytes(b"x")
    assert (_song(client, "Lucie - Amerika")["playlist"], _song(client, "Lucie - Amerika")["origin"]) == ("České hity  Czech songs", "cz")
    assert (_song(client, "Sepultura - Roots")["playlist"], _song(client, "Sepultura - Roots")["origin"]) == ("Metal", "world")
    assert _song(client, "Kabat - Žízeň")["origin"] == "cz"             # by its letters
    assert _song(client, "Kabat - Colorado")["origin"] == "cz"              # …and so the artist's other songs
    assert _song(client, "Olympic - Okno")["playlist"] is None
    assert _song(client, "Olympic - Okno")["origin"] == "world"             # nothing says so…
    server._lyrics_keep("Okno", "Olympic", None, {"plain": "Okno mé lásky, už se rozednívá, řekni mi, proč se tak díváš"})
    assert _song(client, "Olympic - Okno")["origin"] == "cz"                # …until its text does
    server._lyrics_file("Okno", "Olympic", None).unlink()
    assert client.put("/api/stems/Olympic - Okno/origin", json={"origin": "cz"}).json()["origin"] == "cz"
    assert _song(client, "Olympic - Okno")["origin_typed"] is True
    assert client.put("/api/stems/Olympic - Okno/origin", json={"origin": ""}).json()["origin"] == "world"
    assert client.put("/api/stems/Nic/origin", json={"origin": "cz"}).status_code == 404
