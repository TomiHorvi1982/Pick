def test_stream_full_file_without_range_header(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get("/api/library/stream", params={"rel": "song.mp3"})

    assert res.status_code == 200
    assert res.content == b"0123456789"
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["content-length"] == "10"


def test_stream_partial_range_returns_206_with_requested_bytes(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=2-4"}
    )

    assert res.status_code == 206
    assert res.content == b"234"
    assert res.headers["content-range"] == "bytes 2-4/10"
    assert res.headers["content-length"] == "3"


def test_stream_open_ended_range_returns_rest_of_file(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=7-"}
    )

    assert res.status_code == 206
    assert res.content == b"789"
    assert res.headers["content-range"] == "bytes 7-9/10"


def test_stream_rejects_path_traversal(client, tmp_music_root):
    res = client.get("/api/library/stream", params={"rel": "../outside.mp3"})
    assert res.status_code == 404


def test_stream_missing_file_returns_404(client, tmp_music_root):
    res = client.get("/api/library/stream", params={"rel": "nope.mp3"})
    assert res.status_code == 404


def test_stream_malformed_range_header_returns_416(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=abc-def"}
    )

    assert res.status_code == 416
    assert res.json() == {"error": "Neplatný Range požadavek."}


def test_stream_inverted_range_returns_416(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=5-2"}
    )

    assert res.status_code == 416
    assert res.json() == {"error": "Neplatný Range požadavek."}
