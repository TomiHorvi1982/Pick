"""Jam (jam.py): the friend reaches only the gateway — the guest page, its scripts, the room's messages and the
backing of the song being played — and nothing of it without the room's key."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import jam
import server


@pytest.fixture(autouse=True)
def _room(monkeypatch, tmp_path):
    monkeypatch.setattr(jam, "ROOM", {"key": "secret-key", "host": None, "guest": None, "song": None, "stems": [], "url": None, "started": 0})
    monkeypatch.setattr(jam, "MIX_DIR", tmp_path / "mix")


def test_the_gateway_wants_the_key_and_serves_only_the_jam():
    g = TestClient(jam.gate)
    assert g.get("/?k=wrong").status_code == 403
    assert "Pick Jam" in g.get("/?k=secret-key").text
    assert g.get("/static/jam-engine.js").status_code == 200
    assert g.get("/static/jam-host.js").status_code == 404          # the host's page is not the friend's
    assert g.get("/static/index.html").status_code == 404
    assert g.get("/api/stems").status_code == 404                   # nothing of Pick itself
    assert g.get("/mix?k=wrong&stems=drums").status_code == 403


def test_messages_go_from_the_host_to_the_friend_and_back(client):
    g = TestClient(jam.gate)
    with client.websocket_connect("/api/jam/ws") as host:
        assert json.loads(host.receive_text())["type"] == "hello"
        with g.websocket_connect("/ws?k=secret-key") as guest:
            assert json.loads(guest.receive_text()) == {"type": "hello", "role": "guest", "peer": True}
            assert json.loads(host.receive_text()) == {"type": "peer", "on": True}
            host.send_text(json.dumps({"type": "song", "name": "Band - Song", "stems": ["drums", "../x"]}))
            assert json.loads(guest.receive_text())["name"] == "Band - Song"
            assert jam.ROOM["song"] == "Band - Song" and jam.ROOM["stems"] == ["drums", "../x"]
            guest.send_text(json.dumps({"type": "sdp", "sdp": {"type": "answer"}}))
            assert json.loads(host.receive_text())["type"] == "sdp"
        assert json.loads(host.receive_text()) == {"type": "peer", "on": False}
    with pytest.raises(Exception):
        with g.websocket_connect("/ws?k=nope") as bad:
            bad.receive_text()


def test_the_backing_is_the_song_being_played_mixed(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Band - Song"
    d.mkdir(parents=True)
    for st, f in (("drums", 200), ("bass", 80)):
        subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d=1", str(d / f"{st}.mp3")], check=True)
    jam.ROOM.update(song="Band - Song", stems=["drums", "bass"])
    g = TestClient(jam.gate)
    r = g.get("/mix?k=secret-key&stems=drums,bass,vocals")              # vocals: not the song's — left out
    assert r.status_code == 200 and r.headers["content-type"] == "audio/webm" and r.content[:4] == b"\x1aE\xdf\xa3"
    jam.ROOM.update(song="../../etc", stems=["drums"])
    assert g.get("/mix?k=secret-key&stems=drums").status_code == 404
