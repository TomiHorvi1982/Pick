def test_browse_root_lists_dirs_and_audio_files(client, tmp_music_root):
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "track.mp3").write_bytes(b"x")
    (tmp_music_root / "notes.txt").write_bytes(b"x")  # not audio — must be excluded

    res = client.get("/api/library/browse")

    assert res.status_code == 200
    data = res.json()
    assert data["rel"] == ""
    names = [e["name"] for e in data["entries"]]
    assert names == ["Songs", "track.mp3"]  # dirs first, then files, both alphabetical
    assert data["entries"][0]["type"] == "dir"
    assert data["entries"][1]["type"] == "file"
    assert data["entries"][1]["size_bytes"] == 1


def test_browse_subfolder_returns_breadcrumb(client, tmp_music_root):
    sub = tmp_music_root / "Playlists" / "80s Rock"
    sub.mkdir(parents=True)
    (sub / "song.mp3").write_bytes(b"x")

    res = client.get("/api/library/browse", params={"rel": "Playlists/80s Rock"})

    data = res.json()
    assert data["rel"] == "Playlists/80s Rock"
    assert [b["name"] for b in data["breadcrumb"]] == [tmp_music_root.name, "Playlists", "80s Rock"]
    assert data["entries"][0]["rel"] == "Playlists/80s Rock/song.mp3"


def test_browse_rejects_path_traversal_with_dotdot(client, tmp_music_root):
    res = client.get("/api/library/browse", params={"rel": "../../../etc"})
    assert res.status_code == 404


def test_browse_rejects_absolute_path_injection(client, tmp_music_root):
    # Path("/root") / "/etc" collapses to just "/etc" in plain pathlib — the
    # guard has to catch this explicitly, not just check for "..".
    res = client.get("/api/library/browse", params={"rel": "/etc"})
    assert res.status_code == 404


def test_browse_missing_folder_returns_404(client, tmp_music_root):
    res = client.get("/api/library/browse", params={"rel": "does-not-exist"})
    assert res.status_code == 404


def test_browse_null_byte_in_rel(client, tmp_music_root):
    # Null byte in rel should return 404, not 500
    # Path.resolve() raises ValueError on embedded null character
    res = client.get("/api/library/browse", params={"rel": "foo\x00bar"})
    assert res.status_code == 404


def test_browse_rejects_symlink_escape(client, tmp_music_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_music_root / "escape").symlink_to(outside)
    res = client.get("/api/library/browse", params={"rel": "escape"})
    assert res.status_code == 404
