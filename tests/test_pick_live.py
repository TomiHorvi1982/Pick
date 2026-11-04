"""Pick Live: the song the player plays, on the Live Show stage — found among the
separated songs, or got ready first: separated, analysed, its guitar print begun."""
import time

import server


def _quiet(monkeypatch):
    monkeypatch.setattr(server, "_SAVES", {})
    monkeypatch.setattr(server, "_match_song", lambda artist, title: None)
    monkeypatch.setattr(server, "_lookup_lyrics", lambda *a, **k: {"lines": None, "plain": None})


def _stems(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    return d


def _wait(client, sid):
    for _ in range(300):
        st = client.get(f"/api/live/{sid}").json()
        if st["state"] in ("ready", "error"):
            return st
        time.sleep(0.02)
    raise AssertionError("never ready: " + str(st))


def test_a_separated_song_opens_at_once(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch)
    server._STEMS_ROWS.clear()
    _stems(tmp_music_root, "Roots Bloody Roots")
    r = client.post("/api/live/prepare", json={"title": "Roots Bloody Roots", "artists": "Sepultura"}).json()
    assert r["ready"] is True and r["name"] == "Roots Bloody Roots"
    assert r["print"] == "no-guitar"               # no guitar recorded in GuitarStamps yet: nothing started
    monkeypatch.setattr(server, "_guitar_active", lambda: "strat")
    assert client.post("/api/live/prepare", json={"title": "Roots Bloody Roots", "artists": "Sepultura"}).json()["print"] == "none"


def test_nothing_playing_is_told(client):
    r = client.post("/api/live/prepare", json={"title": " "})
    assert r.status_code == 400


def test_a_new_song_is_analysed_and_its_print_begun_before_it_is_ready(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch)
    analysed, fitted = [], []
    monkeypatch.setattr(server, "stems_analysis", lambda name, refresh=False: analysed.append(name) or {})
    monkeypatch.setattr(server, "_guitar_active", lambda: "strat")
    monkeypatch.setattr(server, "tone_fit", lambda name, req: fitted.append((name, req.auto)) or {"status": "running"})

    def download(req):
        d = tmp_music_root / "Songs" / "Korn"
        d.mkdir(parents=True)
        (d / "Blind.mp3").write_bytes(b"x")
        return True

    monkeypatch.setattr(server, "_save_download", download)
    r = client.post("/api/live/prepare", json={"title": "Blind", "artists": "Korn", "videoId": "abc"}).json()
    assert r["ready"] is False and r["id"]
    for _ in range(300):                           # the separation stands in for Demucs
        q = client.get("/api/stems/queue").json()
        if q["jobs"]:
            break
        time.sleep(0.02)
    job = server.JOBS[q["jobs"][0]["job_id"]]
    _stems(tmp_music_root, "Blind")
    job["status"] = "done"
    st = _wait(client, r["id"])
    assert (st["state"], st["stems"], st["print"]) == ("ready", "Blind", "started")
    assert analysed == ["Blind"] and fitted == [("Blind", True)]


def test_unknown_preparation_is_404(client):
    assert client.get("/api/live/nope").status_code == 404
