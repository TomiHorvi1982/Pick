import json

import server


def test_get_settings_returns_current_music_root(client, tmp_music_root):
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json() == {"music_root": str(tmp_music_root)}


def test_update_settings_persists_and_updates_music_root(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    # Endpoint reassigns the module-level MUSIC_ROOT global; setting it to
    # its own current value here just registers it with monkeypatch so the
    # original is restored after the test, regardless of what the endpoint
    # sets it to — otherwise MUSIC_ROOT is left pointing at a tmp_path
    # directory that gets deleted, a landmine for later tests that read it.
    monkeypatch.setattr(server, "MUSIC_ROOT", server.MUSIC_ROOT)
    new_root = tmp_path / "new-music-folder"

    res = client.post("/api/settings", json={"music_root": str(new_root)})

    assert res.status_code == 200
    assert res.json() == {"music_root": str(new_root)}
    assert server.MUSIC_ROOT == new_root
    assert new_root.is_dir()  # created if missing
    assert json.loads((tmp_path / "config.json").read_text()) == {"music_root": str(new_root)}


def test_update_settings_rejects_relative_path(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")

    res = client.post("/api/settings", json={"music_root": "relative/path"})

    assert res.status_code == 400
    assert "error" in res.json()


def test_pick_folder_returns_chosen_path(client, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return type("R", (), {"returncode": 0, "stdout": "/Users/max/Music\n"})()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    res = client.post("/api/settings/pick-folder")

    assert res.json() == {"path": "/Users/max/Music"}


def test_pick_folder_returns_none_on_cancel(client, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return type("R", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    res = client.post("/api/settings/pick-folder")

    assert res.json() == {"path": None}


def test_settings_saved_at_once_keep_each_other(tmp_path, monkeypatch):
    """Night jobs, the loops watcher and the page save settings at the same moment: none may see
    the file half written and save over it what it alone knows — the music root (the card!) went so."""
    import threading
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server._write_config({"music_root": "/Volumes/Card/spotdl", "storage": {"cards": []}})
    lost = []

    def writer(k):
        for i in range(150):
            server._write_config({f"k{k}": i})

    def reader():
        for _ in range(600):
            if server._read_config().get("music_root") != "/Volumes/Card/spotdl":
                lost.append(1)
    ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)] + [threading.Thread(target=reader) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = server._read_config()
    assert not lost and got["music_root"] == "/Volumes/Card/spotdl" and all(got[f"k{k}"] == 149 for k in range(4))


def test_an_unreadable_settings_file_is_not_saved_over(tmp_path, monkeypatch):
    """Should the file ever be cut short (a crash, a full disk), the last good one is read instead of nothing."""
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server._write_config({"music_root": "/Volumes/Card/spotdl"})
    server._write_config({"video_mode": "mini"})
    (tmp_path / "config.json").write_text('{"music_ro')
    assert server._read_config()["music_root"] == "/Volumes/Card/spotdl"
    server._write_config({"loops_watch_since": 1.0})
    assert server._read_config() == {"music_root": "/Volumes/Card/spotdl", "video_mode": "mini", "loops_watch_since": 1.0}


def test_the_video_mode_is_saved_beside_the_rest(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server._write_config({"music_root": "/Volumes/Card/spotdl"})
    assert client.post("/api/video/mode", json={"mode": "hidden"}).json() == {"mode": "hidden"}
    assert server._read_config() == {"music_root": "/Volumes/Card/spotdl", "video_mode": "hidden"}
