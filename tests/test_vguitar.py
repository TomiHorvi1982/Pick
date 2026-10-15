"""Virtuální kytara: riffs kept, only in their known shape."""
import vguitar

RIFF = {"bpm": 140, "div": 2, "tuning": [64, 59, 55, 50, 45, 38],
        "cols": [{"s": {"5": 0, "4": 2}, "pm": True, "lg": []}, {"s": {"5": "x"}}, {"s": {"5": 3}, "lg": [5]}],
        "sound": {"song": "Metallica - One", "pid": "20261015-1"}}


def test_save_list_rename_delete(client):
    r = client.post("/api/vguitar/riffs", json={"name": "  Úvod  ", "riff": RIFF}).json()
    assert r["name"] == "Úvod" and r["bpm"] == 140 and r["cols"][1]["s"] == {"5": "x"} and r["cols"][2]["lg"] == [5]
    assert r["sound"] == {"song": "Metallica - One", "pid": "20261015-1"}
    again = client.post("/api/vguitar/riffs", json={"id": r["id"], "name": "Sloka", "riff": {**RIFF, "bpm": 90}}).json()
    assert again["id"] == r["id"] and again["bpm"] == 90
    rows = client.get("/api/vguitar/riffs").json()["riffs"]
    assert [x["name"] for x in rows] == ["Sloka"]
    assert client.delete(f"/api/vguitar/riffs/{r['id']}").json()["ok"]
    assert client.get("/api/vguitar/riffs").json()["riffs"] == []
    assert client.delete("/api/vguitar/riffs/nope").status_code == 404


def test_odd_riffs_are_refused_or_cleaned(client):
    assert client.post("/api/vguitar/riffs", json={"name": "x", "riff": {"cols": [], "tuning": [64] * 6}}).status_code == 400
    assert client.post("/api/vguitar/riffs", json={"name": "x", "riff": {**RIFF, "tuning": [1, 2]}}).status_code == 400
    r = client.post("/api/vguitar/riffs", json={"name": "", "riff": {**RIFF, "div": 5, "bpm": 9999,
                    "cols": [{"s": {"0": 99, "1": "q", "2": 5, "9": 1}}]}}).json()
    assert r["name"] == "Riff" and r["div"] == 4 and r["bpm"] == 300 and r["cols"][0]["s"] == {"2": 5}
