import server


def test_separate_returns_ready_using_source_stem_as_safe_name(client, tmp_music_root):
    (tmp_music_root / "My Song.mp3").write_bytes(b"x")
    dest = tmp_music_root / "Stems" / "My Song"
    dest.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (dest / f"{stem}.wav").write_bytes(b"fake")

    res = client.post("/api/stems/separate", json={"rel": "My Song.mp3"})

    assert res.status_code == 200
    assert res.json() == {"job_id": None, "ready": True, "name": "My Song"}


def test_separate_creates_a_job_when_not_yet_separated(client, tmp_music_root):
    (tmp_music_root / "New Song.mp3").write_bytes(b"x")

    res = client.post("/api/stems/separate", json={"rel": "New Song.mp3"})

    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is False
    assert data["name"] == "New Song"
    assert data["job_id"] is not None
    job = server.JOBS[data["job_id"]]
    assert job["kind"] == "stems"
    assert job["status"] == "queued"
    assert job["stems_name"] == "New Song"
    assert job["cmd"][0] == server.DEMUCS_BIN
    assert "-n" in job["cmd"] and "htdemucs_6s" in job["cmd"]
    assert job["cmd"][-1] == job["stems_source"]


def test_separate_rejects_missing_source_file(client, tmp_music_root):
    res = client.post("/api/stems/separate", json={"rel": "nope.mp3"})
    assert res.status_code == 404


def test_separate_rejects_path_traversal(client, tmp_music_root):
    res = client.post("/api/stems/separate", json={"rel": "../outside.mp3"})
    assert res.status_code == 404


def test_finalize_stems_job_moves_demucs_output_into_place(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Cool Track.mp3"
    source.write_bytes(b"x")
    raw_track_dir = tmp_path / "Stems" / ".raw" / "htdemucs_6s" / "Cool Track"
    raw_track_dir.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (raw_track_dir / f"{stem}.wav").write_bytes(b"fake-audio")

    job = {"stems_source": str(source), "stems_name": "Cool Track"}
    server._finalize_stems_job(job)

    dest = tmp_path / "Stems" / "Cool Track"
    assert dest.is_dir()
    assert not raw_track_dir.exists()
    for stem in server.STEM_NAMES:
        assert (dest / f"{stem}.wav").read_bytes() == b"fake-audio"


def test_finalize_stems_job_overwrites_a_stale_partial_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Cool Track.mp3"
    source.write_bytes(b"x")
    stale_dest = tmp_path / "Stems" / "Cool Track"
    stale_dest.mkdir(parents=True)
    (stale_dest / "vocals.wav").write_bytes(b"stale-leftover")
    raw_track_dir = tmp_path / "Stems" / ".raw" / "htdemucs_6s" / "Cool Track"
    raw_track_dir.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (raw_track_dir / f"{stem}.wav").write_bytes(b"fresh-audio")

    server._finalize_stems_job({"stems_source": str(source), "stems_name": "Cool Track"})

    assert (tmp_path / "Stems" / "Cool Track" / "vocals.wav").read_bytes() == b"fresh-audio"


def test_finalize_stems_job_raises_if_demucs_output_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Nothing.mp3"
    source.write_bytes(b"x")

    import pytest
    with pytest.raises(FileNotFoundError):
        server._finalize_stems_job({"stems_source": str(source), "stems_name": "Nothing"})
