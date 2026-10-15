"""Poznámky: bugs, ideas and notes about Pick — written, changed, ticked off, let go — and the
open ones copied as one message for Claude."""
import json

import notes


def test_a_note_is_kept_changed_ticked_and_let_go(client):
    n = client.post("/api/notes", json={"text": "  Mixer se seká při přepnutí písně  ", "kind": "chyba"}).json()["note"]
    assert n["text"] == "Mixer se seká při přepnutí písně" and n["kind"] == "chyba" and not n["done"]
    assert json.loads(notes.FILE.read_text())[0]["id"] == n["id"]          # where Claude can read it
    got = client.patch(f"/api/notes/{n['id']}", json={"text": "Mixer se seká", "kind": "napad"}).json()["note"]
    assert got["text"] == "Mixer se seká" and got["kind"] == "napad"
    assert client.patch(f"/api/notes/{n['id']}", json={"done": True}).json()["note"]["done"] is True
    assert client.delete(f"/api/notes/{n['id']}").json()["notes"] == []
    assert client.delete(f"/api/notes/{n['id']}").status_code == 404


def test_an_empty_note_or_an_unknown_kind_is_refused(client):
    assert client.post("/api/notes", json={"text": "   "}).status_code == 400
    n = client.post("/api/notes", json={"text": "něco", "kind": "divné"}).json()["note"]
    assert n["kind"] == "poznamka"
    assert client.patch(f"/api/notes/{n['id']}", json={"kind": "divné"}).status_code == 400
    assert client.patch(f"/api/notes/{n['id']}", json={"text": ""}).status_code == 400
    assert client.patch("/api/notes/nic", json={"done": True}).status_code == 404


def test_the_open_ones_go_first_newest_on_top(client):
    a = client.post("/api/notes", json={"text": "první"}).json()["note"]
    b = client.post("/api/notes", json={"text": "druhá"}).json()["note"]
    client.patch(f"/api/notes/{b['id']}", json={"done": True})
    c = client.post("/api/notes", json={"text": "třetí"}).json()["note"]
    assert [n["text"] for n in client.get("/api/notes").json()["notes"]] == ["třetí", "první", "druhá"]


def test_for_claude_they_come_as_one_message_by_kind(client):
    client.post("/api/notes", json={"text": "Přidat metronom do Zpěvníku", "kind": "napad"})
    client.post("/api/notes", json={"text": "Pedál se nepřipojí\npo probuzení Macu", "kind": "chyba"})
    done = client.post("/api/notes", json={"text": "Hotová věc", "kind": "chyba"}).json()["note"]
    client.patch(f"/api/notes/{done['id']}", json={"done": True})
    text = client.get("/api/notes/markdown").json()["text"]
    assert text.startswith("Poznámky z Picku")
    assert text.index("## Chyby") < text.index("## Nápady") and "## Poznámky" not in text
    assert "- Pedál se nepřipojí (" in text and "\n  po probuzení Macu" in text and "Hotová věc" not in text
    assert "- [x] Hotová věc" in client.get("/api/notes/markdown?all=true").json()["text"]


def test_nothing_open_nothing_to_send(client):
    assert client.get("/api/notes/markdown").json()["text"] == ""
