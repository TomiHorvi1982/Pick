import server


def test_settings_change_flows_through_to_browse_and_stream(client, tmp_path, monkeypatch):
    """Exercises the full cross-task path: POST /api/settings reassigns the
    real server.MUSIC_ROOT global (not a monkeypatched stand-in), and
    /api/library/browse + /api/library/stream must read that live value on
    the very next request — no stale/cached root.
    """
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    # Restore the original MUSIC_ROOT after the test, whatever the endpoint
    # below reassigns it to (see test_settings.py for the same landmine).
    monkeypatch.setattr(server, "MUSIC_ROOT", server.MUSIC_ROOT)

    new_root = tmp_path / "new-music-folder"
    res = client.post("/api/settings", json={"music_root": str(new_root)})
    assert res.status_code == 200
    assert server.MUSIC_ROOT == new_root

    # Seed a file directly into the new root, bypassing the API entirely.
    (new_root / "song.mp3").write_bytes(b"0123456789")

    browse_res = client.get("/api/library/browse")
    assert browse_res.status_code == 200
    names = [e["name"] for e in browse_res.json()["entries"]]
    assert "song.mp3" in names

    stream_res = client.get("/api/library/stream", params={"rel": "song.mp3"})
    assert stream_res.status_code == 200
    assert stream_res.content == b"0123456789"
