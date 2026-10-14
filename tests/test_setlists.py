"""Steems playlists: songs in an order of your own, kept on the server; started by voice by name."""
import pytest

import setlists
import voice


@pytest.fixture(autouse=True)
def _own_file(monkeypatch, tmp_path):
    monkeypatch.setattr(setlists, "PATH", tmp_path / "steems-playlists.json")


def test_a_playlist_is_made_changed_and_removed(client):
    p = client.post("/api/setlists", json={"name": "  Zkouška   se Standou ", "songs": ["Sepultura - Amen"]}).json()
    assert p["name"] == "Zkouška se Standou" and p["songs"] == ["Sepultura - Amen"]
    assert client.post("/api/setlists", json={"name": "zkouška se standou"}).status_code == 409
    assert client.post("/api/setlists", json={"name": " "}).status_code == 400
    got = client.put(f"/api/setlists/{p['id']}", json={"name": "", "songs": ["B", "Sepultura - Amen", "../etc", "A"]}).json()
    assert got["name"] == "Zkouška se Standou" and got["songs"] == ["B", "Sepultura - Amen", "A"]       # order kept, paths refused
    assert [x["name"] for x in client.get("/api/setlists").json()] == ["Zkouška se Standou"]
    assert client.put("/api/setlists/nothing", json={"songs": []}).status_code == 404
    client.delete(f"/api/setlists/{p['id']}")
    assert client.get("/api/setlists").json() == []


def test_practice_playlist_by_voice(client):
    assert voice.interpret("practice playlist zkouška", [], "practice") == ("find", "practice", "playlist zkouška")
    assert client.get("/api/voice/find?kind=practice&name=playlist Zkouška se Standou").json() == {"kind": "setlist", "query": "Zkouška se Standou"}
