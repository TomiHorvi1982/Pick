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
