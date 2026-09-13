import server


def test_list_stems_empty_when_no_stems_dir(client, tmp_music_root):
    res = client.get("/api/stems")
    assert res.status_code == 200
    assert res.json() == []


def test_list_stems_reports_present_stems_per_song(client, tmp_music_root):
    d1 = tmp_music_root / "Stems" / "Song A"
    d1.mkdir(parents=True)
    for s in server.STEM_NAMES:
        (d1 / f"{s}.wav").write_bytes(b"x")
    d2 = tmp_music_root / "Stems" / "Song B (partial)"
    d2.mkdir(parents=True)
    (d2 / "vocals.wav").write_bytes(b"x")
    (d2 / "drums.wav").write_bytes(b"x")

    res = client.get("/api/stems")

    assert res.status_code == 200
    data = res.json()
    assert {"name": "Song A", "stems": server.STEM_NAMES} in data
    b_entry = next(d for d in data if d["name"] == "Song B (partial)")
    assert set(b_entry["stems"]) == {"vocals", "drums"}


def test_list_stems_skips_the_raw_staging_dir(client, tmp_music_root):
    raw = tmp_music_root / "Stems" / ".raw" / "htdemucs_6s" / "Leftover"
    raw.mkdir(parents=True)
    (raw / "vocals.wav").write_bytes(b"x")

    res = client.get("/api/stems")

    assert res.json() == []


def test_stream_stem_returns_file_content(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"0123456789")

    res = client.get("/api/stems/Song A/vocals.wav")

    assert res.status_code == 200
    assert res.content == b"0123456789"
    assert res.headers["accept-ranges"] == "bytes"


def test_stream_stem_supports_range(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"0123456789")

    res = client.get("/api/stems/Song A/vocals.wav", headers={"Range": "bytes=2-4"})

    assert res.status_code == 206
    assert res.content == b"234"


def test_stream_stem_rejects_unknown_stem_name(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"x")

    res = client.get("/api/stems/Song A/not-a-real-stem.wav")

    assert res.status_code == 404


def test_stream_stem_rejects_path_traversal_in_name(client, tmp_music_root):
    res = client.get("/api/stems/../../etc/vocals.wav")
    assert res.status_code == 404


def test_stream_stem_missing_file_returns_404(client, tmp_music_root):
    res = client.get("/api/stems/Does Not Exist/vocals.wav")
    assert res.status_code == 404
