def test_browse_root_lists_dirs_and_all_files(client, tmp_music_root):
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "track.mp3").write_bytes(b"x")
    (tmp_music_root / "notes.txt").write_bytes(b"x")  # not audio — listed, but as "other"
    (tmp_music_root / ".DS_Store").write_bytes(b"x")  # hidden — never listed

    res = client.get("/api/library/browse")

    assert res.status_code == 200
    data = res.json()
    assert data["rel"] == ""
    names = [e["name"] for e in data["entries"]]
    assert names == ["Songs", "notes.txt", "track.mp3"]  # dirs first, then files, both alphabetical
    by_name = {e["name"]: e for e in data["entries"]}
    assert by_name["Songs"]["type"] == "dir"
    assert by_name["track.mp3"]["type"] == "file"
    assert by_name["track.mp3"]["size_bytes"] == 1
    assert by_name["notes.txt"]["type"] == "other"
    assert by_name["notes.txt"]["ext"] == "txt"
    # Only audio counts as a track.
    assert data["summary"]["total_tracks"] == 1


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


def test_track_number_is_read_for_sorting_by_album():
    from server import _track_no
    assert _track_no("3") == 3
    assert _track_no("7/12") == 7
    assert _track_no(" 9 / 10") == 9
    assert _track_no(None) is None
    assert _track_no("") is None
    assert _track_no("A1") is None


def test_midi_files_are_listed_and_served(client, tmp_music_root):
    # MIDI files are dragged from the library onto a MIDI track, so they get
    # their own type, their folder is marked as holding some, and the bytes
    # are served (only for .mid/.midi).
    sub = tmp_music_root / "MIDI"
    sub.mkdir()
    (sub / "riff.mid").write_bytes(b"MThd-data")
    (sub / "notes.txt").write_bytes(b"x")

    root = {e["name"]: e for e in client.get("/api/library/browse").json()["entries"]}
    assert root["MIDI"]["midi_count"] == 1
    entries = {e["name"]: e for e in client.get("/api/library/browse", params={"rel": "MIDI"}).json()["entries"]}
    assert entries["riff.mid"]["type"] == "midi"

    res = client.get("/api/library/midi", params={"rel": "MIDI/riff.mid"})
    assert res.status_code == 200 and res.content == b"MThd-data"
    assert client.get("/api/library/midi", params={"rel": "MIDI/notes.txt"}).status_code == 404
    assert client.get("/api/library/midi", params={"rel": "../../etc/passwd"}).status_code == 404


def test_songs_flat_lists_playlist_songs_too_without_duplicates(client, tmp_music_root):
    (tmp_music_root / "Songs" / "Metallica").mkdir(parents=True)
    (tmp_music_root / "Songs" / "Metallica" / "One.mp3").write_bytes(b"x" * 2000)
    pl = tmp_music_root / "Playlists" / "Best Metal"
    pl.mkdir(parents=True)
    (pl / "Metallica - One.mp3").write_bytes(b"x" * 2000)          # the same song again
    (pl / "Slayer - Raining Blood.mp3").write_bytes(b"x" * 2000)
    d = client.get("/api/library/browse", params={"rel": "Songs", "flat": True}).json()
    assert d["flat"] is True
    rels = [e["rel"] for e in d["entries"]]
    assert "Songs/Metallica/One.mp3" in rels and "Playlists/Best Metal/Slayer - Raining Blood.mp3" in rels
    assert len(rels) == 2                               # "Metallica - One" in the playlist is the same song
    assert {e["folder"] for e in d["entries"]} <= {"Songs", "Best Metal"}
