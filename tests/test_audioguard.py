"""Ochrana zvuku: while a window sounds, the heavy jobs step back (background band, or paused)
and come back when it stops. Only `sleep` processes the test starts itself stand in for them."""
import subprocess
import time

import pytest

import audioguard
import server


def _ps(pid, field):
    return subprocess.run(["/bin/ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def _back(pid):
    """In macOS's background band: its priority down to 4 (from 31)."""
    return int(_ps(pid, "pri") or 99) <= 4


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(audioguard, "SLOW", ("/bin/sleep 61",))
    monkeypatch.setattr(audioguard, "PAUSABLE", ("/bin/sleep 61",))
    monkeypatch.setattr(audioguard, "extra", lambda: [])
    monkeypatch.setattr(audioguard, "STATE", {"on": True, "mode": "slow", "clients": {}, "held": {}, "what": {}})
    procs = []

    def heavy():
        p = subprocess.Popen(["/bin/sleep", "61"])
        procs.append(p)
        time.sleep(0.2)
        return p
    yield heavy
    audioguard.release()
    for p in procs:
        p.kill()
        p.wait()


def test_a_heavy_job_steps_back_while_the_music_plays(guard):
    p = guard()
    assert not _back(p.pid)
    audioguard.ping("okno", True)
    audioguard.step()
    assert audioguard.active() and _back(p.pid) and audioguard.STATE["held"] == {p.pid: "slow"}
    audioguard.ping("okno", False)                          # given back at once
    assert not audioguard.active() and not _back(p.pid) and audioguard.STATE["held"] == {}


def test_in_the_pause_mode_it_waits_and_goes_on(guard):
    p = guard()
    audioguard.configure(mode="pause")
    audioguard.ping("okno", True)
    audioguard.step()
    assert _ps(p.pid, "stat").startswith("T")
    audioguard.configure(on=False)                          # the protection off: back at once, though it plays
    assert not _ps(p.pid, "stat").startswith("T")


def test_a_window_gone_quiet_counts_as_silence(guard, monkeypatch):
    p = guard()
    monkeypatch.setattr(audioguard, "QUIET_AFTER", 0.3)
    audioguard.ping("okno", True)
    audioguard.ping("druhé okno", True)
    audioguard.ping("okno", False)
    audioguard.step()
    assert audioguard.active() and _back(p.pid)               # the other window still sounds
    time.sleep(0.4)
    audioguard.step()
    assert not audioguard.active() and not _back(p.pid)


def test_the_settings_are_kept_and_the_page_is_heard(client, guard):
    p = guard()
    got = client.post("/api/audio/active", json={"client": "okno", "on": True}).json()
    audioguard.step()
    got = client.get("/api/audio/guard").json()
    assert got["active"] and got["held"][0]["pid"] == p.pid and got["held"][0]["how"] == "slow"
    assert client.post("/api/audio/guard", json={"mode": "loud"}).status_code == 400
    got = client.post("/api/audio/guard", json={"mode": "pause"}).json()
    assert got["mode"] == "pause" and _ps(p.pid, "stat").startswith("T")
    assert server._read_config()["audio_guard"] == {"on": True, "mode": "pause"}
    client.post("/api/audio/active", json={"client": "okno", "on": False})
    assert not _ps(p.pid, "stat").startswith("T")
