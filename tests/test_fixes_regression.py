"""Regression tests for the round of fixes covering download layout,
Demucs device selection and merged-video temp cleanup.
"""

import subprocess
import time

import server


# ------------------------------------------------- queue download layout

def test_queue_download_lands_in_playlists_folder(client, tmp_music_root):
    """A "queue" download used to be written to MUSIC_ROOT/"Playlist"
    (singular) — a stray fourth folder alongside Playlists/Songs/Stems that
    the library browser never showed.
    """
    res = client.post("/api/download", json={
        "urls": ["https://open.spotify.com/track/x"],
        "output_name": "My Queue",
        "kind": "queue",
    })

    assert res.status_code == 200
    out_dir = res.json()["output_dir"]
    assert out_dir == str(tmp_music_root / "Playlists" / "My Queue")
    assert not (tmp_music_root / "Playlist").exists()


def test_queue_download_sanitises_output_name(client, tmp_music_root):
    res = client.post("/api/download", json={
        "urls": ["https://open.spotify.com/track/x"],
        "output_name": "../../etc/evil",
        "kind": "queue",
    })

    out_dir = res.json()["output_dir"]
    assert out_dir.startswith(str(tmp_music_root / "Playlists"))
    assert ".." not in out_dir


# ------------------------------------------------------- Demucs device

def test_demucs_interpreter_read_from_shebang(tmp_path, monkeypatch):
    fake = tmp_path / "demucs"
    fake.write_text("#!/opt/some/tool/bin/python\nprint('hi')\n")
    monkeypatch.setattr(server, "DEMUCS_BIN", str(fake))

    assert server._demucs_interpreter() == "/opt/some/tool/bin/python"


def test_demucs_interpreter_none_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DEMUCS_BIN", str(tmp_path / "nope"))

    assert server._demucs_interpreter() is None


def test_demucs_device_probes_demucs_own_interpreter(monkeypatch):
    """torch lives in Demucs' isolated tool venv, never in this server's venv,
    so the probe must run as a subprocess. Importing torch in-process always
    raised ImportError, which silently sent every separation to the CPU.
    """
    monkeypatch.setattr(server, "_DEMUCS_DEVICE_CACHE", {})
    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="mps\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    assert server._demucs_device() == "mps"
    assert calls and calls[0][0] == "/fake/python"
    assert "torch" in calls[0][2]


def test_demucs_device_none_when_no_mps(monkeypatch):
    monkeypatch.setattr(server, "_DEMUCS_DEVICE_CACHE", {})
    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")
    monkeypatch.setattr(
        server.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="\n", stderr=""),
    )

    assert server._demucs_device() is None


def test_demucs_device_survives_probe_failure(monkeypatch):
    monkeypatch.setattr(server, "_DEMUCS_DEVICE_CACHE", {})
    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")

    def boom(cmd, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(server.subprocess, "run", boom)

    assert server._demucs_device() is None


def test_demucs_device_result_is_cached(monkeypatch):
    monkeypatch.setattr(server, "_DEMUCS_DEVICE_CACHE", {})
    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="mps\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    server._demucs_device()
    server._demucs_device()

    assert len(calls) == 1


def test_separate_passes_device_flag_to_demucs(client, tmp_music_root, monkeypatch):
    src = tmp_music_root / "Songs" / "track.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"fake audio")
    monkeypatch.setattr(server, "_DEMUCS_DEVICE_CACHE", {"device": "mps"})

    res = client.post("/api/stems/separate", json={"rel": "Songs/track.mp3"})

    assert res.status_code == 200
    job = server.JOBS[res.json()["job_id"]]
    assert "-d" in job["cmd"]
    assert job["cmd"][job["cmd"].index("-d") + 1] == "mps"


# ------------------------------------------- merged-video temp cleanup

def test_purge_removes_expired_merged_video_tmpdir(tmp_path, monkeypatch):
    """Merged DASH videos are written to a mkdtemp() that nothing removed,
    leaking tens of MB per playback for the rest of the uptime.
    """
    tmpdir = tmp_path / "leaked"
    tmpdir.mkdir()
    (tmpdir / "video.mp4").write_bytes(b"x" * 1024)
    monkeypatch.setattr(server, "_STREAM_CACHE", {
        ("video", "expired"): {
            "url": f"file://{tmpdir}/video.mp4",
            "expires": time.time() - 1,
            "is_file": True,
            "tmpdir": str(tmpdir),
        },
    })

    server._purge_expired_stream_files()

    assert not tmpdir.exists()
    assert ("video", "expired") not in server._STREAM_CACHE


def test_purge_keeps_live_entries(tmp_path, monkeypatch):
    tmpdir = tmp_path / "live"
    tmpdir.mkdir()
    monkeypatch.setattr(server, "_STREAM_CACHE", {
        ("video", "live"): {
            "url": f"file://{tmpdir}/video.mp4",
            "expires": time.time() + 600,
            "is_file": True,
            "tmpdir": str(tmpdir),
        },
    })

    server._purge_expired_stream_files()

    assert tmpdir.exists()
    assert ("video", "live") in server._STREAM_CACHE


def test_purge_ignores_plain_url_entries(monkeypatch):
    """Direct googlevideo URLs have no temp dir — purging must not choke."""
    monkeypatch.setattr(server, "_STREAM_CACHE", {
        ("audio", "x"): {"url": "https://example/x", "expires": time.time() - 1},
    })

    server._purge_expired_stream_files()

    assert ("audio", "x") in server._STREAM_CACHE  # only is_file entries are swept


# --------------------------------------------------- video format probe

def test_video_format_combos_use_selectors_not_hardcoded_ids():
    """Hardcoded IDs like "399+251" only exist on some videos, so most
    DASH-only videos burned six failed probes before reaching the fallback.
    """
    import inspect

    # Comments are stripped first: the rewritten function names the old IDs
    # on purpose, to explain why they're gone.
    source = inspect.getsource(server._resolve_video)
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())

    for dead_id in ("399+251", "137+251", "398+251", "136+251", "397+251", "135+251"):
        assert dead_id not in code
    assert "bestvideo" in code


def test_quiet_logger_swallows_yt_dlp_errors():
    logger = server._QuietLogger()
    # Probing selectors is normal control flow; yt-dlp's per-miss ERROR lines
    # used to fill the server log with non-errors.
    logger.error("Requested format is not available")
    logger.warning("w")
    logger.info("i")
    logger.debug("d")
