"""Jam (jam.py): the friend reaches only the gateway — the guest page, its scripts, the room's messages and the
backing of the song being played — and nothing of it without the room's key."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import jam
import server


@pytest.fixture(autouse=True)
def _room(monkeypatch, tmp_path):
    monkeypatch.setattr(jam, "ROOM", {"key": "secret-key", "host": None, "guest": None, "song": None, "stems": [], "url": None, "started": 0})
    monkeypatch.setattr(jam, "MIX_DIR", tmp_path / "mix")
    monkeypatch.setattr(jam, "CONF", tmp_path / "jam.json")
    monkeypatch.setattr(jam, "CF_DIR", tmp_path / "cf")
    monkeypatch.setattr(jam, "SESS", {})
    monkeypatch.setattr(jam, "_FAILS", {})
    monkeypatch.setattr(jam, "_SONGS", {"at": 0.0, "rows": []})
    monkeypatch.setattr(jam, "main_app", server.app)


def test_the_gateway_wants_the_key_and_serves_only_the_jam():
    g = TestClient(jam.gate)
    assert g.get("/?k=wrong").status_code == 403
    page = g.get("/?k=secret-key")
    assert "Pick Jam" in page.text and page.headers["cache-control"] == "no-store"
    assert "/static/jam-guest.js?v=" in page.text and "/static/jam-chat.js?v=" in page.text       # a new version, a new address
    assert g.get("/static/jam-guest.js?v=123").status_code == 200
    assert g.get("/static/jam-engine.js").status_code == 200
    assert g.get("/static/jam-host.js").status_code == 404          # the host's page is not the friend's
    assert g.get("/static/index.html").status_code == 404
    assert g.get("/api/stems").status_code == 404                   # nothing of Pick itself
    assert g.get("/mix?k=wrong&stems=drums").status_code == 403


def test_the_friend_downloads_the_engine_only_when_let_in(monkeypatch, tmp_path):
    monkeypatch.setattr(jam, "ENGINE_DIST", tmp_path)
    g = TestClient(jam.gate)
    assert g.get("/g/me?k=secret-key").json()["engine"] == []                  # nothing built yet
    (tmp_path / "pick-jam-engine.exe").write_bytes(b"MZ-test")
    (tmp_path / "notes.txt").write_text("x")
    assert g.get("/g/me?k=secret-key").json()["engine"] == ["pick-jam-engine.exe"]
    assert g.get("/engine/pick-jam-engine.exe?k=wrong").status_code == 403
    r = g.get("/engine/pick-jam-engine.exe?k=secret-key")
    assert r.status_code == 200 and r.content == b"MZ-test" and "attachment" in r.headers["content-disposition"]
    assert g.get("/engine/notes.txt?k=secret-key").status_code == 404            # only the engine's own files
    assert g.get("/engine/..%2Fjam.py?k=secret-key").status_code == 404


def test_messages_go_from_the_host_to_the_friend_and_back(client):
    g = TestClient(jam.gate)
    with client.websocket_connect("/api/jam/ws") as host:
        assert json.loads(host.receive_text())["type"] == "hello"
        with g.websocket_connect("/ws?k=secret-key") as guest:
            hi = json.loads(guest.receive_text())
            assert hi["type"] == "hello" and hi["role"] == "guest" and hi["peer"] is True and hi["chat"] == []
            assert json.loads(host.receive_text()) == {"type": "peer", "on": True, "names": {"host": "Tomi", "guest": "Kamarád"}}
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
            assert json.loads(host.receive_text()) == {"type": "peer", "on": True, "names": {"host": "Tomi", "guest": "Kamarád"}}
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


def test_my_own_domain_wants_setting_up_first(client, monkeypatch):
    monkeypatch.delenv("PICK_JAM_LOCAL", raising=False)
    monkeypatch.setattr(jam, "_gate_start", lambda: None)
    assert client.post("/api/jam/via", json={"via": "nowhere"}).status_code == 400
    assert client.post("/api/jam/via", json={"via": "domain"}).json()["via"] == "domain"
    d = client.get("/api/jam/status").json()
    assert d["via"] == "domain" and d["domain"]["ready"] is False
    r = client.post("/api/jam/start")
    assert r.status_code == 409 and r.json()["why"] == "domain"
    assert jam.ROOM["key"] == "secret-key" and jam.ROOM["url"] is None          # nothing started
    assert client.post("/api/jam/domain/setup", json={"host": "not a host"}).status_code == 400
    r = client.post("/api/jam/domain/setup", json={"host": "jam.neverlast.cz"})
    assert r.status_code == 400 and "přihlas" in r.json()["msg"]                  # not logged in to Cloudflare yet


def test_with_the_domain_ready_the_link_is_on_it(client, monkeypatch, tmp_path):
    monkeypatch.delenv("PICK_JAM_LOCAL", raising=False)
    monkeypatch.setattr(jam, "_gate_start", lambda: None)
    monkeypatch.setattr(jam, "_serve_off", lambda: None)
    monkeypatch.setattr(jam, "_cf_up", lambda: {"ok": True, "host": "jam.neverlast.cz"})
    jam.conf_save(via="domain")
    url = client.post("/api/jam/start").json()["url"]
    assert url == "https://jam.neverlast.cz/?k=secret-key"


def test_no_tunnel_yet_and_a_taken_name(monkeypatch, tmp_path):
    """cloudflared lists no tunnels as `null`; a name with an A record already (an old site) is said plainly."""
    (tmp_path / "cf").mkdir()
    (tmp_path / "cf" / "cert.pem").write_text("x")
    calls = []
    def fake(*args, timeout=30):
        calls.append(args)
        if args[:2] == ("tunnel", "list"):
            return subprocess.CompletedProcess(args, 0, "null", "")
        if args[:2] == ("tunnel", "create"):
            (tmp_path / "cf" / "abc.json").write_text("{}")
            return subprocess.CompletedProcess(args, 0, '{"id": "abc"}', "")
        return subprocess.CompletedProcess(args, 1, "", "Failed to add route: code: 1003, An A, AAAA, or CNAME record with that host already exists.")
    monkeypatch.setattr(jam, "_cf", fake)
    r = jam.cf_setup("neverlast.cz")
    assert r["ok"] is False and r["exists"] and "dash.cloudflare.com → neverlast.cz → DNS" in r["msg"]
    assert jam.conf()["tunnel_id"] == "abc"



def test_with_names_set_the_friend_logs_in_and_nothing_goes_without(client, tmp_music_root):
    g = TestClient(jam.gate)
    assert client.post("/api/jam/users", json={"name": "Pavel", "password": "123"}).status_code == 400      # too short
    assert client.post("/api/jam/users", json={"name": "Pavel", "password": "kytara66"}).json()["users"] == ["Pavel"]
    assert client.post("/api/jam/users", json={"host_name": "Tomi"}).json()["host_name"] == "Tomi"
    assert "kytara66" not in (jam.CONF.read_text())                                         # kept as a hash only
    assert g.get("/").status_code == 200                                                   # the page: the login form
    assert g.get("/g/me").json() == {"user": None, "login": True, "host": "Tomi", "on": True, "engine": [], "sonobus": None}
    assert g.get("/g/songs?k=secret-key").status_code == 403                               # the key alone is not enough now
    assert g.post("/login", json={"name": "Pavel", "password": "spatne"}).status_code == 401
    r = g.post("/login", json={"name": "Pavel", "password": "kytara66"})
    assert r.status_code == 200 and "pj" in r.cookies
    assert g.get("/g/me").json()["user"] == "Pavel"
    d = tmp_music_root / "Stems" / "Band - Song"
    d.mkdir(parents=True)
    subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", str(d / "drums.mp3")], check=True)
    songs = g.get("/g/songs").json()["songs"]
    assert [s["name"] for s in songs] == ["Band - Song"] and "rel" not in songs[0]
    st = g.get("/g/stem?name=Band%20-%20Song&stem=drums")
    assert st.status_code == 200 and st.content[:4] == b"\x1aE\xdf\xa3"
    assert g.get("/g/stem?name=..%2F..&stem=drums").status_code == 404
    assert g.get("/g/song?name=Band%20-%20Song").json()["name"] == "Band - Song"
    with g.websocket_connect("/ws") as ws:                                                 # the room: by the login
        hi = json.loads(ws.receive_text())
        assert hi["names"] == {"host": "Tomi", "guest": "Pavel"}
    client.delete("/api/jam/users?name=Pavel")                                            # taken away: out at once
    assert g.get("/g/songs").status_code == 403


def test_too_many_wrong_passwords_wait(client):
    client.post("/api/jam/users", json={"name": "Pavel", "password": "kytara66"})
    g = TestClient(jam.gate)
    for _ in range(8):
        assert g.post("/login", json={"name": "Pavel", "password": "x"}).status_code == 401
    assert g.post("/login", json={"name": "Pavel", "password": "kytara66"}).status_code == 429


def test_the_engines_and_nothing_beside(client):
    g = TestClient(jam.gate)
    assert g.get("/static/nam/index.js").status_code == 200
    assert g.get("/static/nam/../../server.py").status_code == 404
    assert g.get("/static/nam/%2E%2E/%2E%2E/server.py").status_code == 404
    assert g.get("/static/tone/x.js").status_code == 404


def test_a_take_of_the_friend_makes_his_print(client, monkeypatch, tmp_music_root):
    import io, wave
    seen = {}
    monkeypatch.setattr(jam, "friend_print", lambda name, start, end, gain, path, who: seen.update(name=name, start=start, end=end, who=who, size=path.stat().st_size) or {"job": {"status": "running"}})
    (tmp_music_root / "Stems" / "Band - Song").mkdir(parents=True)
    g = TestClient(jam.gate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(b"\0\0" * 48000 * 5)
    r = g.post("/g/take?k=secret-key&name=Band%20-%20Song&start=10&end=22", content=buf.getvalue())
    assert r.status_code == 200 and seen == {"name": "Band - Song", "start": 10.0, "end": 22.0, "who": "Kamarád", "size": len(buf.getvalue())}
    assert g.post("/g/take?k=secret-key&name=Band%20-%20Song&start=10&end=22", content=b"nope" * 400).status_code == 400
    assert g.post("/g/take?k=wrong&name=Band%20-%20Song&start=10&end=22", content=buf.getvalue()).status_code == 403


def test_the_browser_playlists_covers_and_preview_need_the_friend(client, tmp_music_root):
    g = TestClient(jam.gate)
    for u in ("/g/playlists", "/g/art?name=Band%20-%20Song", "/g/preview?name=Band%20-%20Song"):
        assert g.get(u).status_code == 403                                   # no key, no login: nothing
    assert g.get("/g/playlists?k=secret-key").json() == {"playlists": []} or "playlists" in g.get("/g/playlists?k=secret-key").json()
    assert g.get("/g/preview?k=secret-key&name=..%2F..").status_code == 404
    assert g.get("/g/art?k=secret-key&name=..%2Fx").status_code == 404


def test_tabs_songsterr_only_as_a_link_files_only_when_linked(client, monkeypatch, tmp_music_root):
    (tmp_music_root / "Stems" / "Band - Song").mkdir(parents=True)
    monkeypatch.setattr(jam, "_SONGS", {"at": time.time(), "rows": [{"name": "Band - Song", "artist": "Band", "title": "Song", "stems": ["drums"]}]})
    monkeypatch.setattr(jam, "_TABS", {"at": time.time(), "rows": [
        {"path": "songsterr:42", "ext": "songsterr", "name": "Band - Song", "artist": "Band", "title": "Song", "songsterr": {"songId": 42}},
        {"path": "/tabs/band-song.gp5", "ext": "gp5", "name": "Band - Song", "artist": "Band", "title": "Song"},
        {"path": "/tabs/other.gp5", "ext": "gp5", "name": "Other", "artist": "X", "title": "Other"}]})
    g = TestClient(jam.gate)
    assert g.get("/g/tabs?name=Band%20-%20Song").status_code == 403
    tabs = g.get("/g/tabs?k=secret-key&name=Band%20-%20Song").json()["tabs"]
    assert tabs == [{"kind": "songsterr", "songsterr": 42, "name": "Band - Song", "url": "https://www.songsterr.com/a/wa/song?id=42"},
                    {"kind": "file", "path": "/tabs/band-song.gp5", "name": "Band - Song"}]
    assert g.get("/g/tabfile?k=secret-key&name=Band%20-%20Song&path=%2Ftabs%2Fother.gp5").status_code == 404      # not this song's


def test_na_priste_is_a_steems_playlist_both_change(client, monkeypatch, tmp_path, tmp_music_root):
    import setlists
    monkeypatch.setattr(setlists, "PATH", tmp_path / "pl.json")
    for n in ("Band - Song", "Band - Other"):
        (tmp_music_root / "Stems" / n).mkdir(parents=True)
    client.post("/api/jam/users", json={"name": "Pavel", "password": "kytara66"})
    g = TestClient(jam.gate)
    g.post("/login", json={"name": "Pavel", "password": "kytara66"})
    jam.ROOM["key"] = None                                         # no jam on: still his, logged in
    assert g.get("/g/fav").json() == {"name": "Jam — na příště", "songs": []}
    r = g.post("/g/fav", json={"ops": [{"name": "Band - Song", "add": True}, {"name": "Band - Other", "add": True}, {"name": "../x", "add": True}]})
    assert r.json()["songs"] == ["Band - Song", "Band - Other"]
    pl = next(p for p in setlists.load() if p["name"] == "Jam — na příště")          # the host's Steems playlist
    assert pl["songs"] == ["Band - Song", "Band - Other"]
    setlists.setlist_set(pl["id"], setlists.SetlistIn(name=pl["name"], songs=["Band - Other"]))   # the host takes one away
    assert g.post("/g/fav", json={"ops": [{"name": "Band - Song", "add": False}]}).json()["songs"] == ["Band - Other"]
    assert g.get("/sw.js").headers["content-type"].startswith("text/javascript")
    assert TestClient(jam.gate).get("/g/fav").status_code == 403                    # not logged in: nothing


def test_na_priste_merges_with_the_library_on_the_pc(monkeypatch, tmp_path, tmp_music_root):
    import setlists
    monkeypatch.setattr(setlists, "PATH", tmp_path / "pl.json")
    for n in ("A", "B", "C"):
        (tmp_music_root / "Stems" / n).mkdir(parents=True)
    jam.conf_save(library_url="https://hraj.example", library_token="x" * 30, lib_base=["A", "B"])
    p = jam._fav()
    setlists.setlist_set(p["id"], setlists.SetlistIn(name=p["name"], songs=["A", "B"]))
    pc = {"songs": ["A", "B", "C"]}                       # the friend added C at the library…
    sent = []
    def call(method, path, body=None, timeout=15):
        if method == "GET":
            return dict(pc)
        sent.append((method, path, body))
        return {}
    monkeypatch.setattr(jam, "_lib_call", call)
    setlists.setlist_set(p["id"], setlists.SetlistIn(name=p["name"], songs=["A"]))   # …and the host took B away in Pick
    assert jam.lib_sync()["songs"] == ["A", "C"]
    assert jam._fav()["songs"] == ["A", "C"] and sent == [("PUT", "/sync/fav", {"songs": ["A", "C"]})]
    assert jam.conf()["lib_base"] == ["A", "C"]


def test_a_second_window_takes_over_and_the_first_is_told_not_to_come_back(client):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/api/jam/ws") as first:
        assert json.loads(first.receive_text())["type"] == "hello"
        with client.websocket_connect("/api/jam/ws") as second:
            assert json.loads(second.receive_text())["type"] == "hello"
            with pytest.raises(WebSocketDisconnect) as gone:
                first.receive_text()
            assert gone.value.code == 4409                                     # the page reads it: no reconnect, no ping-pong


def test_sonobus_group_stays_and_goes_only_to_the_friend_let_in(client):
    g = TestClient(jam.gate)
    sb = jam.sonobus()
    assert sb == jam.sonobus()                                          # the same group from jam to jam (SonoBus remembers it)
    assert sb["group"].startswith("pick-") and len(sb["password"]) == 12
    assert sb["app"] == f"sonobus://aoo.sonobus.net:10998/?g={sb['group']}&p={sb['password']}"
    assert g.get("/g/me").json()["sonobus"] is None                     # not without the key
    assert g.get("/g/me?k=secret-key").json()["sonobus"]["group"] == sb["group"]
    assert client.get("/api/jam/status").json()["sonobus"]["password"] == sb["password"]
    assert g.get("/static/jam-sonobus.js").status_code == 200 and client.get("/static/jam-sonobus.js").status_code == 200


def test_tabs_and_sonobus_go_to_the_library_on_the_pc(monkeypatch, tmp_music_root):
    (tmp_music_root / "Stems" / "Band - Song").mkdir(parents=True)
    jam.conf_save(library_url="https://hraj.example", library_token="x" * 30)
    monkeypatch.setattr(jam, "_SONGS", {"at": time.time(), "rows": [{"name": "Band - Song", "artist": "Band", "title": "Song", "stems": ["drums"]}]})
    rows = [{"path": "songsterr:42", "ext": "songsterr", "name": "Band - Song", "artist": "Band", "title": "Song", "songsterr": {"songId": 42}},
            {"path": "/tabs/band-song.gp5", "ext": "gp5", "name": "Band - Song", "artist": "Band", "title": "Song"}]

    class R:
        def __init__(self, code, body=None, content=b""):
            self.status_code, self._b, self.content = code, body, content
        def json(self):
            return self._b

    async def pick(path, params=None):
        return R(200, {"files": rows}) if path == "/api/tabfiles/list" else R(200, content=b"GP5-FILE-BYTES" * 10)
    monkeypatch.setattr(jam, "_pick", pick)
    calls, put = [], []
    monkeypatch.setattr(jam, "_lib_call", lambda m, p, body=None, timeout=15: calls.append((m, p, body)) or ({"files": []} if m == "GET" else {}))
    import urllib.request

    class Up:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b""
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: put.append(req.full_url) or Up())
    jam.lib_tabs()
    tabs = [c for c in calls if c[1] == "/sync/tabs" and c[0] == "PUT"][0][2]["tabs"]
    h = __import__("hashlib").sha1(b"GP5-FILE-BYTES" * 10).hexdigest()[:20]
    assert tabs == {"Band - Song": [{"kind": "songsterr", "songsterr": 42, "name": "Band - Song"}, {"kind": "file", "id": h, "name": "Band - Song"}]}
    assert put == [f"https://hraj.example/sync/tabfile/{h}"]
    calls.clear(); put.clear()
    jam.lib_tabs()                                                      # nothing changed: nothing sent
    assert not put and not any(c[0] == "PUT" for c in calls)
    jam.lib_status()
    st = [c for c in calls if c[1] == "/sync/status"][0][2]
    assert st["sonobus"]["group"] == jam.sonobus()["group"] and "app" not in st["sonobus"]
