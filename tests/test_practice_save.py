"""“pick save song”: the song that plays is made ready for practice — its tab
and text looked up, downloaded when it is not in the library, and separated."""
import time

import server


def _wait(client, sid, states=("ready", "error")):
    for _ in range(200):
        st = next(s for s in client.get("/api/practice/saves").json()["saves"] if s["id"] == sid)
        if st["state"] in states:
            return st
        time.sleep(0.02)
    raise AssertionError("the save never got there: " + str(st))


def _quiet(monkeypatch, tab=None, lyrics=None):
    monkeypatch.setattr(server, "_SAVES", {})
    monkeypatch.setattr(server, "_match_song", lambda artist, title: tab)
    monkeypatch.setattr(server, "_lookup_lyrics", lambda *a, **k: lyrics or {"lines": None, "plain": None})


def _stems(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")


def test_a_song_already_in_steems_is_ready_at_once(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch, lyrics={"lines": [{"timeInMs": 0, "text": "la"}]})
    _stems(tmp_music_root, "Roots")
    sid = client.post("/api/practice/save", json={"title": "Roots", "artists": "Sepultura", "stems": "Roots"}).json()["id"]
    st = _wait(client, sid)
    assert (st["state"], st["stems"], st["lyrics"], st["tabs"]) == ("ready", "Roots", True, False)


def test_a_song_of_the_library_goes_to_the_separation_queue(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(server, "_save_download", lambda req: (_ for _ in ()).throw(AssertionError("no download needed")))
    (tmp_music_root / "Songs" / "Slipknot").mkdir(parents=True)
    (tmp_music_root / "Songs" / "Slipknot" / "Duality.mp3").write_bytes(b"x")
    sid = client.post("/api/practice/save", json={"title": "Duality", "artists": "Slipknot"}).json()["id"]
    _wait(client, sid, ("separating",))
    for _ in range(100):
        if client.get("/api/stems/queue").json()["active"]:
            break
        time.sleep(0.02)
    q = client.get("/api/stems/queue").json()
    assert [j["name"] for j in q["jobs"]] == ["Duality"]
    # the same song asked for again while it is under way is the same save
    again = client.post("/api/practice/save", json={"title": "Duality", "artists": "Slipknot"}).json()
    assert again["id"] == sid and again["already"] is True
    # the separation finishing is the save finishing
    job = server.JOBS[q["jobs"][0]["job_id"]]
    _stems(tmp_music_root, "Duality")
    job["status"] = "done"
    assert _wait(client, sid)["state"] == "ready"


def test_a_song_from_the_internet_is_downloaded_first(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch)
    asked = []

    def download(req):
        asked.append((req.videoId, req.title))
        d = tmp_music_root / "Songs" / "Korn"
        d.mkdir(parents=True)
        (d / "Blind.mp3").write_bytes(b"x")
        return True

    monkeypatch.setattr(server, "_save_download", download)
    sid = client.post("/api/practice/save", json={"title": "Blind", "artists": "Korn", "videoId": "abc"}).json()["id"]
    st = _wait(client, sid, ("separating", "error"))
    assert st["state"] == "separating" and asked == [("abc", "Blind")]
    for _ in range(100):
        q = client.get("/api/stems/queue").json()
        if q["active"]:
            break
        time.sleep(0.02)
    assert [j["name"] for j in q["jobs"]] == ["Blind"]
    server.JOBS[q["jobs"][0]["job_id"]]["status"] = "error"
    assert _wait(client, sid)["state"] == "error"


def test_a_download_that_fails_says_so(client, tmp_music_root, monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(server, "_save_download", lambda req: False)
    sid = client.post("/api/practice/save", json={"title": "Nowhere", "artists": "Nobody"}).json()["id"]
    st = _wait(client, sid)
    assert st["state"] == "error" and "Stažení" in st["error"]
    assert client.post("/api/practice/save", json={"title": " "}).status_code == 400
