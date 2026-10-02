"""Named tunings shared by the tuner and the keyboard's fretboard."""


def test_a_tuning_is_saved_named_updated_and_deleted(client):
    t = client.put("/api/tunings", json={"name": "Drop A 7", "strings": ["A1", "E2", "A2", "D3", "G3", "B3", "E4"]}).json()["tuning"]
    assert t["id"].startswith("t-") and t["strings"][0] == "A1"
    up = client.put("/api/tunings", json={"id": t["id"], "name": "Drop A", "strings": ["A1", "E2", "A2", "D3", "G3", "B3", "E4"]}).json()
    assert [x["name"] for x in up["tunings"]] == ["Drop A"]
    assert client.get("/api/tunings").json()["tunings"][0]["id"] == t["id"]
    assert client.delete(f"/api/tunings/{t['id']}").json()["tunings"] == []


def test_nonsense_strings_or_no_name_are_refused(client):
    assert client.put("/api/tunings", json={"name": "X", "strings": ["E2", "Q9"]}).status_code == 400
    assert client.put("/api/tunings", json={"name": " ", "strings": ["E2"]}).status_code == 400
    assert client.put("/api/tunings", json={"name": "X", "strings": []}).status_code == 400
