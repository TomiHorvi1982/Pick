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
