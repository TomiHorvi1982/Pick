"""Pick Tabs: the catalog of Songsterr tabs the app has opened, listed in the
Taby section as a folder of its own. References only — no notation on disk."""

import server


def test_a_tab_is_remembered_once_with_what_it_was_used_for(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PICK_TABS_PATH", tmp_path / "pick_tabs.json")
    monkeypatch.setattr(server, "_scan_tab_dirs", lambda refresh=False: [])
    body = {"songId": 35, "revisionId": 7, "trackIndex": 1, "title": "Master of Puppets",
            "artist": "Metallica", "track": "Lead Guitar", "usedWith": "Metallica - Master of Puppets"}
    client.post("/api/picktabs", json=body)
    client.post("/api/picktabs", json={**body, "usedWith": "Přehrávač"})

    tabs = client.get("/api/picktabs").json()["tabs"]
    assert len(tabs) == 1
    assert tabs[0]["usedWith"] == ["Přehrávač", "Metallica - Master of Puppets"]

    rows = client.get("/api/tabfiles/list").json()["files"]
    assert rows[0]["folder"] == "Pick Tabs" and rows[0]["path"] == "songsterr:35"
    assert rows[0]["songsterr"] == {"songId": 35, "trackIndex": 1, "track": "Lead Guitar"}
    assert not any(p.suffix in (".gp", ".gp5", ".gpx") for p in tmp_path.iterdir())

    client.delete("/api/picktabs/35")
    assert client.get("/api/picktabs").json()["tabs"] == []


def _wait_sync(client):
    import time
    for _ in range(100):
        st = client.get("/api/picktabs/sync").json()
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("sync never finished")


def test_favourites_find_their_tabs_in_the_background(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PICK_TABS_PATH", tmp_path / "pick_tabs.json")
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    recs = {
        "Kabát Pohoda (Remastered)": [
            {"songId": 1, "artist": "Kabát revival", "title": "Pohoda", "tracks": []},
            {"songId": 58916, "artist": "Kabát", "title": "Pohoda", "popularTrackGuitar": 3,
             "tracks": [{}, {}, {}, {"instrument": "Distortion Guitar", "name": "Gitara 3"}]}],
        "Arakain": [{"songId": 10, "artist": "Arakain", "title": "Zimní Královna"},
                    {"songId": 11, "artist": "Arakain", "title": "Zimní královna (live)"},
                    {"songId": 12, "artist": "Arakain Revival", "title": "Princess"},
                    {"songId": 13, "artist": "Arakain", "title": "Adrian"}],
    }
    monkeypatch.setattr(server, "_songsterr_search", lambda q, size=20: recs.get(q, []))

    client.post("/api/picktabs/sync", json={"kind": "song", "artist": "Kabát", "title": "Pohoda (Remastered)"})
    st = _wait_sync(client)
    assert st["added"] == 1
    tab = client.get("/api/picktabs").json()["tabs"][0]
    assert tab["songId"] == 58916 and tab["trackIndex"] == 3 and tab["usedWith"] == ["Oblíbené"]

    client.post("/api/picktabs/sync", json={"kind": "band", "artist": "Arakain"})
    _wait_sync(client)
    ids = {t["songId"] for t in client.get("/api/picktabs").json()["tabs"]}
    assert ids == {58916, 10, 13}        # same song once, other bands left out
