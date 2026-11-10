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
            hi = json.loads(guest.receive_text())
            assert hi["type"] == "hello" and hi["role"] == "guest" and hi["peer"] is True and hi["chat"] == []
            assert json.loads(host.receive_text()) == {"type": "peer", "on": True}
            host.send_text(json.dumps({"type": "song", "name": "Band - Song", "stems": ["drums", "../x"]}))
            assert json.loads(guest.receive_text())["name"] == "Band - Song"
            assert jam.ROOM["song"] == "Band - Song" and jam.ROOM["stems"] == ["drums", "../x"]
            guest.send_text(json.dumps({"type": "sdp", "sdp": {"type": "answer"}}))
            assert json.loads(host.receive_text())["type"] == "sdp"
        bye = json.loads(host.receive_text())
        assert bye["type"] == "peer" and bye["on"] is False and bye["seen"] > 0
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


def test_the_chat_keeps_messages_and_tells_delivered_read_and_typing(client):
    g = TestClient(jam.gate)
    with client.websocket_connect("/api/jam/ws") as host:
        json.loads(host.receive_text())
        # the friend is not here yet: the message waits (sent ✓, not delivered)
        host.send_text(json.dumps({"type": "chat", "id": "m1", "text": "  Ahoj, dáme Master of Puppets?  "}))
        ok = json.loads(host.receive_text())
        assert ok["type"] == "chat-ok" and ok["id"] == "m1" and ok["dlv"] is False
        with g.websocket_connect("/ws?k=secret-key") as guest:
            hi = json.loads(guest.receive_text())
            assert [m["text"] for m in hi["chat"]] == ["Ahoj, dáme Master of Puppets?"] and hi["chat"][0]["from"] == "host"
            assert json.loads(host.receive_text()) == {"type": "peer", "on": True}
            assert json.loads(host.receive_text()) == {"type": "chat-dlv", "ids": ["m1"]}     # delivered now
            guest.send_text(json.dumps({"type": "typing", "on": True, "junk": "x" * 50}))
            assert json.loads(host.receive_text()) == {"type": "typing", "on": True}
            guest.send_text(json.dumps({"type": "read", "id": "m1"}))
            rd = json.loads(host.receive_text())
            assert rd["type"] == "read" and rd["id"] == "m1" and rd["t"] > 0
            guest.send_text(json.dumps({"type": "read", "id": "nope"}))              # not a message: dropped
            guest.send_text(json.dumps({"type": "chat", "id": "g1", "text": "Jo! <b>hned</b>"}))
            assert json.loads(guest.receive_text())["dlv"] is True
            m = json.loads(host.receive_text())
            assert m["type"] == "chat" and m["from"] == "guest" and m["text"] == "Jo! <b>hned</b>"
            guest.send_text(json.dumps({"type": "chat", "id": "g1", "text": "Jo! <b>hned</b>"}))   # sent again: kept once
            assert json.loads(guest.receive_text())["type"] == "chat-ok"
            guest.send_text(json.dumps({"type": "chat", "id": "g2", "text": "   "}))                # empty: nothing
            guest.send_text(json.dumps({"type": "away", "on": True}))
            assert json.loads(host.receive_text()) == {"type": "away", "on": True}
        json.loads(host.receive_text())                                                    # peer off
    assert [m["id"] for m in jam.ROOM["chat"]] == ["m1", "g1"]
    assert jam.ROOM["read"]["guest"]["id"] == "m1"
    # a reload: the history and what was read come back
    with client.websocket_connect("/api/jam/ws") as host:
        hi = json.loads(host.receive_text())
        assert [m["id"] for m in hi["chat"]] == ["m1", "g1"] and hi["read"]["guest"]["id"] == "m1" and hi["seen"] > 0
    client.post("/api/jam/stop")
    assert jam.ROOM["chat"] == []


def test_tailscale_is_asked_as_from_a_terminal(monkeypatch):
    """The app's binary answers as the command line only with TERM set — Pick runs without one."""
    seen = {}
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setattr(jam, "_ts_bin", lambda: "/x/Tailscale")
    monkeypatch.setattr(jam.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, env=kw.get("env")) or subprocess.CompletedProcess(cmd, 0, "{}", ""))
    jam._ts("status", "--json")
    assert seen["cmd"] == ["/x/Tailscale", "status", "--json"] and seen["env"]["TERM"]
