"""ToneRig presets switched from here: settings, the queue the Mac app sends
MIDI from, and the phone page that only answers with its secret token."""
import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(server, "_tonerig", {"seq": 0, "events": [], "server": None, "thread": None})
    monkeypatch.setattr(server, "_tonerig_lan", lambda on: None)     # no real port in tests


def test_settings_are_kept_and_cleaned(client, cfg):
    got = client.put("/api/tonerig", json={"mode": "cc", "channel": 40, "cc": 300, "presets": ["Clean", "Crunch"],
                                           "external": True}).json()
    assert (got["mode"], got["channel"], got["cc"]) == ("cc", 16, 127)
    assert got["presets"][:3] == ["Clean", "Crunch", ""] and len(got["presets"]) == 8
    assert got["external"] is True and "token" not in got


def test_a_pick_is_queued_for_the_mac_and_remembered(client, cfg):
    client.put("/api/tonerig", json={"presets": ["Clean", "Crunch", "Lead"]})
    assert client.post("/api/tonerig/select", json={"i": 2}).json()["name"] == "Lead"
    p = client.get("/api/tonerig/pending", params={"since": 0}).json()
    assert [e["i"] for e in p["events"]] == [2] and p["active"] == 2 and p["name"] == "Lead"
    assert client.get("/api/tonerig/pending", params={"since": p["seq"]}).json()["events"] == []
    assert client.post("/api/tonerig/select", json={"i": 9}).status_code == 400


def test_the_phone_page_needs_its_token(client, cfg):
    on = client.post("/api/tonerig/lan", json={"on": True}).json()
    token = server._tonerig_cfg()["token"]
    assert token and on["lan"] is True
    phone = TestClient(server.tonerig_app)
    assert phone.get("/t/wrong").status_code == 403
    assert phone.post("/t/wrong/select", json={"i": 1}).status_code == 403
    assert "ToneRig" in phone.get(f"/t/{token}").text
    assert phone.post(f"/t/{token}/select", json={"i": 1}).json()["active"] == 1
    assert client.get("/api/tonerig/pending").json()["events"][-1] == {"seq": 1, "i": 1, "source": "phone"}
    # Only the ToneRig page lives on that port — none of the app.
    assert phone.get("/api/library/browse").status_code == 404
    assert phone.get("/").status_code == 404
    # Switched off: the same link is refused straight away.
    client.post("/api/tonerig/lan", json={"on": False})
    assert phone.get(f"/t/{token}").status_code == 403
