"""Naplánované úlohy: a heavy job put off to the night waits in plan.json and is sent to the
server in the night window — one after another, only when nothing heavy runs and no music
plays — or at once when asked. What it was refused for a while comes back later."""
import json
import time

import pytest

import planner
import server


@pytest.fixture
def sent(monkeypatch):
    """What the planner sends, answered as the test says (by default: fine)."""
    calls, answers = [], []

    def send(method, path, body):
        calls.append((method, path, body))
        return answers.pop(0) if answers else (200, {})
    monkeypatch.setattr(planner, "send", send)
    monkeypatch.setattr(planner, "_wait_idle", lambda: True)
    monkeypatch.setattr(planner, "idle", lambda: True)
    monkeypatch.setattr(planner, "quiet", lambda: True)
    return calls, answers


def _window(client, on_now: bool):
    """A night window that holds this very minute — or one that does not."""
    lt = time.localtime()
    t = lt.tm_hour * 60 + lt.tm_min
    a, b = ((t - 5) % 1440, (t + 60) % 1440) if on_now else ((t + 60) % 1440, (t + 120) % 1440)
    return client.post("/api/plan/settings", json={"start": f"{a // 60}:{a % 60:02d}", "end": f"{b // 60}:{b % 60:02d}"}).json()


def _plan(client, path="/api/stems/separate", body=None, kind="separate", label="Píseň"):
    return client.post("/api/plan", json={"kind": kind, "label": label, "path": path, "body": body or {"rel": "a.mp3"}}).json()["task"]


def test_only_the_heavy_jobs_can_be_planned_and_they_wait(client):
    t = _plan(client, body={"rel": "x/a.mp3", "format": "wav"})
    assert t["status"] == "waiting" and t["method"] == "POST"
    assert client.post("/api/plan", json={"path": "/api/config", "body": {}}).status_code == 400
    assert client.post("/api/plan", json={"path": "/api/stems/a/b/hear"}).status_code == 400
    got = client.get("/api/plan").json()
    assert [x["label"] for x in got["tasks"]] == ["Píseň"] and got["tasks"][0]["body"] == {"rel": "x/a.mp3", "format": "wav"}
    assert got["settings"] == {"on": True, "ask": True, "start": "23:00", "end": "07:00"}


def test_it_waits_for_the_night_and_for_the_music_to_stop(client, sent, monkeypatch):
    calls, _ = sent
    _plan(client)
    _window(client, on_now=False)
    planner.step()
    assert calls == []                                          # not the night yet
    _window(client, on_now=True)
    monkeypatch.setattr(planner, "quiet", lambda: False)
    planner.step()
    assert calls == []                                          # the night, but music plays
    monkeypatch.setattr(planner, "quiet", lambda: True)
    monkeypatch.setattr(planner, "idle", lambda: False)
    planner.step()
    assert calls == []                                          # something heavy runs
    monkeypatch.setattr(planner, "idle", lambda: True)
    planner.step()
    assert calls == [("POST", "/api/stems/separate", {"rel": "a.mp3"})]
    got = client.get("/api/plan").json()
    assert got["tasks"] == [] and got["history"][0]["status"] == "done"


def test_switched_off_it_waits_even_at_night(client, sent):
    calls, _ = sent
    _plan(client)
    _window(client, on_now=True)
    client.post("/api/plan/settings", json={"on": False})
    planner.step()
    assert calls == []
    assert client.get("/api/plan").json()["settings"]["on"] is False


def test_now_runs_one_job_or_all_of_them_without_the_night(client, sent):
    calls, _ = sent
    _window(client, on_now=False)
    a, b, c = _plan(client, label="A"), _plan(client, label="B"), _plan(client, label="C")
    assert client.post("/api/plan/now", json={"id": c["id"]}).json()["tasks"][0]["label"] == "C"
    planner.step()
    assert [x["label"] for x in client.get("/api/plan").json()["tasks"]] == ["A", "B"]
    planner.step()
    assert len(calls) == 1                                      # the others wait for the night
    client.post("/api/plan/now", json={})
    planner.step()
    planner.step()
    planner.step()
    assert len(calls) == 3 and planner.STATE["run_now"] is False
    assert client.post("/api/plan/now", json={"id": "nic"}).status_code == 404


def test_the_order_can_be_changed_and_a_job_let_go(client):
    a, b, c = _plan(client, label="A"), _plan(client, label="B"), _plan(client, label="C")
    got = client.post(f"/api/plan/{c['id']}/move", json={"delta": -5}).json()
    assert [x["label"] for x in got["tasks"]] == ["C", "A", "B"]
    got = client.delete(f"/api/plan/{a['id']}").json()
    assert [x["label"] for x in got["tasks"]] == ["C", "B"]
    assert client.delete(f"/api/plan/{a['id']}").status_code == 404


def test_refused_for_want_of_room_it_comes_back_later_then_gives_up(client, sent, monkeypatch):
    calls, answers = sent
    _window(client, on_now=True)
    t = _plan(client)
    answers += [(503, {"error": "Volná paměť je teď 1.0 GB"})] * 3
    planner.step()
    w = client.get("/api/plan").json()["tasks"][0]
    assert w["status"] == "waiting" and w["tries"] == 1 and "paměť" in w["note"] and w["not_before"] > time.time()
    planner.step()
    assert len(calls) == 1                                      # not again straight away
    for _ in range(2):
        monkeypatch.setattr(planner, "AGAIN_AFTER", 0.0)
        with planner._LOCK:
            d = planner._load()
            d["tasks"][0]["not_before"] = 0
            planner._save(d)
        planner.step()
    got = client.get("/api/plan").json()
    assert got["tasks"] == [] and got["history"][0]["status"] == "error" and "3. pokus" in got["history"][0]["note"]


def test_a_job_cut_off_by_a_restart_runs_again(client, sent):
    calls, _ = sent
    _window(client, on_now=True)
    t = _plan(client)
    d = json.loads(planner.FILE.read_text())
    d["tasks"][0]["status"] = "running"
    planner.FILE.write_text(json.dumps(d))
    planner.step()
    assert len(calls) == 1 and client.get("/api/plan").json()["history"][0]["id"] == t["id"]


def test_the_night_window_reaches_over_midnight(monkeypatch):
    monkeypatch.setattr(planner, "settings", lambda: {"start": "23:00", "end": "07:00"})
    at = lambda h, m: time.mktime((2026, 10, 15, h, m, 0, 0, 0, -1))
    assert planner.in_window(at(23, 30)) and planner.in_window(at(3, 0)) and planner.in_window(at(6, 59))
    assert not planner.in_window(at(7, 0)) and not planner.in_window(at(12, 0)) and not planner.in_window(at(22, 59))
    monkeypatch.setattr(planner, "settings", lambda: {"start": "01:00", "end": "05:00"})
    assert planner.in_window(at(2, 0)) and not planner.in_window(at(23, 30))


def test_the_settings_want_a_time(client):
    assert client.post("/api/plan/settings", json={"start": "25:00"}).status_code == 400
    got = client.post("/api/plan/settings", json={"start": "22:30", "end": "6:00"}).json()
    assert got["settings"]["start"] == "22:30" and got["settings"]["end"] == "06:00"
    assert server._read_config()["plan"] == {"start": "22:30", "end": "06:00"}


def test_through_the_server_a_missing_song_is_an_error_and_a_real_one_is_queued(client, tmp_music_root, monkeypatch):
    def send(method, path, body):
        r = client.request(method, path, json=body)
        return r.status_code, r.json()
    monkeypatch.setattr(planner, "send", send)
    monkeypatch.setattr(planner, "_wait_idle", lambda: True)
    monkeypatch.setattr(planner, "quiet", lambda: True)
    (tmp_music_root / "a.mp3").write_bytes(b"\0" * 64)
    _plan(client, body={"rel": "nic.mp3"}, label="Chybí")
    _plan(client, body={"rel": "a.mp3"}, label="Je")
    client.post("/api/plan/now", json={})
    planner.step()
    h = client.get("/api/plan").json()["history"]
    assert h[0]["label"] == "Chybí" and h[0]["status"] == "error" and "nenalezen" in h[0]["note"]
    assert server._plan_idle()
    planner.step()
    assert not server._plan_idle()                              # the separation now waits in its queue
    h = client.get("/api/plan").json()["history"]
    assert h[0]["label"] == "Je" and h[0]["status"] == "done"


def test_the_tasks_list_shows_what_is_planned(client):
    _plan(client, label="Kryptonite – rozdělit")
    row = next(t for t in client.get("/api/tasks").json()["tasks"] if t["id"] == "plan")
    assert row["queue"] == ["Kryptonite – rozdělit"] and row["state"] == "čeká" and "23:00" in row["detail"]


def test_asking_first_can_be_switched_off(client):
    assert client.post("/api/plan/settings", json={"ask": False}).json()["settings"]["ask"] is False
    assert server._read_config()["plan"] == {"ask": False}


def test_the_mac_stays_awake_while_jobs_are_due(client, sent, tmp_path, monkeypatch):
    awake = tmp_path / "awake.sh"
    awake.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    awake.chmod(0o755)
    monkeypatch.setattr(planner, "CAFFEINATE", str(awake))
    _window(client, on_now=False)
    _plan(client)
    planner.step()
    assert planner._AWAKE["proc"] is None                      # not the night: it may sleep
    _window(client, on_now=True)
    monkeypatch.setattr(planner, "idle", lambda: False)         # due, though waiting for a heavy job
    planner.step()
    p = planner._AWAKE["proc"]
    assert p is not None and p.poll() is None
    monkeypatch.setattr(planner, "idle", lambda: True)
    planner.step()                                              # done…
    planner.step()                                              # …and nothing more is due
    assert planner._AWAKE["proc"] is None and p.wait(timeout=5) is not None


def test_the_history_can_be_cleared(client, sent):
    _window(client, on_now=True)
    _plan(client)
    planner.step()
    assert client.get("/api/plan").json()["history"]
    assert client.delete("/api/plan/history").json()["history"] == []
