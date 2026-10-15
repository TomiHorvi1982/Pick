"""Úlohy a procesy: what runs and waits, its load, pause / go on / stop. Only processes the
test starts itself (sleep) are signalled — never what else runs on this computer."""
import subprocess
import time

import pytest

import server
import songbook
import tasks


def _state(pid):
    return subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def sleeper():
    procs = []

    def make():
        p = subprocess.Popen(["/bin/sleep", "60"])
        procs.append(p)
        return p
    yield make
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
        p.wait()


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setitem(server._STEMS_PAUSED, "on", False)
    monkeypatch.setitem(server._SAVES_PAUSED, "on", False)
    monkeypatch.setattr(server, "JOBS", {})
    monkeypatch.setattr(server, "_SAVES", {})
    monkeypatch.setattr(server, "_SAVE_QUEUE", server._collections.deque())


def _task(client, tid, sort="cpu"):
    got = client.get(f"/api/tasks?sort={sort}").json()
    return next((t for t in got["tasks"] if t["id"] == tid), None), got


def test_cpu_time_is_read_in_every_shape():
    assert tasks._seconds("0:12.34") == pytest.approx(12.34)
    assert tasks._seconds("16:00.51") == pytest.approx(960.51)
    assert tasks._seconds("1:02:03.5") == pytest.approx(3723.5)
    assert tasks._seconds("2-01:00:00") == pytest.approx(2 * 86400 + 3600)


def test_the_list_has_the_server_and_its_totals(client):
    got = client.get("/api/tasks").json()
    assert got["cpus"] >= 1 and got["sort"] == "cpu"
    me = next(t for t in got["tasks"] if t["id"] == "server")
    assert me["procs"] >= 1 and me["mem"] > 0 and me["can"] == []
    assert client.get("/api/tasks?sort=mem").json()["sort"] == "mem"


def test_a_process_the_server_started_shows_and_stops(client, sleeper):
    p = sleeper()
    time.sleep(0.2)
    t, _ = _task(client, f"proc:{p.pid}")
    assert t and t["name"] == "sleep" and set(t["can"]) == {"pause", "stop"}
    t = next(x for x in client.post(f"/api/tasks/proc:{p.pid}/pause").json()["tasks"] if x["id"] == f"proc:{p.pid}")
    assert _state(p.pid).startswith("T") and t["state"] == "pozastaveno" and "resume" in t["can"]
    client.post(f"/api/tasks/proc:{p.pid}/resume")
    assert not _state(p.pid).startswith("T")
    client.post(f"/api/tasks/proc:{p.pid}/stop")
    assert p.wait(timeout=5) is not None
    assert client.post("/api/tasks/proc:1/stop").status_code == 404           # not one of the server's own


def test_a_running_separation_is_paused_and_stopped(client, sleeper):
    p = sleeper()
    server.JOBS["j1"] = {"status": "running", "proc": p, "stems_name": "Kapela - Píseň", "progress": 40.0, "lines": []}
    server._STEMS_QUEUE.append("j1")
    try:
        t, _ = _task(client, "stems:j1")
        assert t["detail"] == "Kapela - Píseň" and t["progress"] == 0.4 and t["procs"] == 1
        client.post("/api/tasks/stems:j1/pause")
        assert _state(p.pid).startswith("T")
        client.post("/api/tasks/stems:j1/stop")
        assert p.wait(timeout=5) is not None and server.JOBS["j1"]["stopped"]
    finally:
        server._STEMS_QUEUE.remove("j1")


def test_the_separation_queue_is_held_and_let_go(client):
    for i in range(3):
        server.JOBS[f"q{i}"] = {"status": "queued", "stems_name": f"Píseň {i}", "lines": []}
        server._STEMS_QUEUE.append(f"q{i}")
    try:
        t, _ = _task(client, "stems-queue")
        assert t["queue"] == ["Píseň 0", "Píseň 1", "Píseň 2"] and t["state"] == "čeká" and "pause" in t["can"]
        client.post("/api/tasks/stems-queue/pause")
        assert server._STEMS_PAUSED["on"]
        t, _ = _task(client, "stems-queue")
        assert t["state"] == "pozastaveno" and "resume" in t["can"]
        client.post("/api/tasks/stems-queue/resume")
        assert not server._STEMS_PAUSED["on"]
        client.post("/api/tasks/stems-queue/stop")
        assert {server.JOBS[f"q{i}"]["status"] for i in range(3)} == {"cancelled"}
        assert _task(client, "stems-queue")[0] is None
    finally:
        for i in range(3):
            server._STEMS_QUEUE.remove(f"q{i}")


def test_saving_songs_is_held_and_the_waiting_let_go(client):
    for i in range(2):
        sid = f"s{i}"
        server._SAVES[sid] = {"id": sid, "key": (f"t{i}", "a"), "title": f"Píseň {i}", "artists": "Kapela", "state": "waiting", "started": time.time() + i}
        server._SAVE_QUEUE.append((sid, None))
    t, _ = _task(client, "saves")
    assert t["queue"] == ["Kapela – Píseň 0", "Kapela – Píseň 1"] and "pause" in t["can"]
    client.post("/api/tasks/saves/pause")
    assert server._SAVES_PAUSED["on"] and _task(client, "saves")[0]["state"] == "pozastaveno"
    client.post("/api/tasks/saves/stop")
    assert not server._SAVE_QUEUE and not server._SAVES and not server._SAVES_PAUSED["on"]


def test_the_songbook_batch_pauses_and_goes_on(client, monkeypatch):
    monkeypatch.setattr(songbook, "JOB", {"running": True, "total": 10, "done": 4, "now": "Kapela - Píseň", "errors": []})
    monkeypatch.setattr(songbook, "left", lambda: {"names": ["a", "b"], "total": 10, "done": 4, "paused": False})
    started = []
    monkeypatch.setattr(songbook, "start", lambda names, total=0, done_before=0: started.append(names))
    t, _ = _task(client, "songbook")
    assert t["progress"] == 0.4 and "pause" in t["can"]
    client.post("/api/tasks/songbook/pause")
    assert songbook.JOB["running"] is False
    t, _ = _task(client, "songbook")
    assert t["state"] == "pozastaveno" and "resume" in t["can"]
    client.post("/api/tasks/songbook/resume")
    assert started == [["a", "b"]]


def test_hearing_can_be_stopped(client, monkeypatch):
    job = {"song": "Kapela - Píseň", "status": "running", "done": 3, "of": 9, "started": time.time()}
    monkeypatch.setitem(server._HEAR, "job", job)
    t, _ = _task(client, "hear")
    assert t["detail"] == "Kapela - Píseň · 3/9" and "stop" in t["can"]
    client.post("/api/tasks/hear/stop")
    assert job["status"] == "stopped"
    assert client.post("/api/tasks/hear/stop").status_code == 404


def test_nonsense_is_refused(client):
    assert client.post("/api/tasks/server/explode").status_code == 404
    assert client.post("/api/tasks/server/stop").status_code == 400
    assert client.post("/api/tasks/stems:nic/pause").status_code == 404


def test_the_songs_waiting_to_be_saved_outlive_a_restart(monkeypatch):
    monkeypatch.setitem(server._SAVE_WORKER, "thread", None)
    monkeypatch.setattr(server, "_save_drain", lambda: None)
    reqs = [server.SaveSongRequest(title=f"Píseň {i}", artists="Kapela", videoId=f"v{i}") for i in range(3)]
    monkeypatch.setitem(server._SAVES_NOW, "req", reqs[0])                     # the one under way
    for i in (1, 2):
        server._SAVE_QUEUE.append((f"s{i}", reqs[i]))
    server._saves_keep()
    assert [r["title"] for r in server.json.loads(server.SAVES_QUEUE_FILE.read_text())] == ["Píseň 0", "Píseň 1", "Píseň 2"]
    server._SAVE_QUEUE.clear()                                                 # …the server stops, and starts again
    server._SAVES.clear()
    monkeypatch.setitem(server._SAVES_NOW, "req", None)
    assert server._saves_resume() == 3
    assert [r.title for _, r in server._SAVE_QUEUE] == ["Píseň 0", "Píseň 1", "Píseň 2"] and reqs[2].videoId == "v2"
    assert {s["state"] for s in server._SAVES.values()} == {"waiting"}
    assert server._saves_resume() == 0                                         # the same songs are not queued twice
    server._SAVE_QUEUE.clear()
    server._saves_keep()
    assert not server.SAVES_QUEUE_FILE.exists()
