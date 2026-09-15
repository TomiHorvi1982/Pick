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


# ------------------------------------------------------- thumbnail proxy

def test_thumb_proxy_rejects_foreign_hosts(client):
    """It must never become an open proxy."""
    res = client.get("/api/thumb", params={"url": "https://example.com/x.jpg"})
    assert res.status_code == 403


def test_thumb_proxy_rejects_non_https(client):
    res = client.get("/api/thumb", params={"url": "http://ytimg.com/x.jpg"})
    assert res.status_code == 400


def test_thumb_proxy_rejects_lookalike_host(client):
    """"ytimg.com.evil.test" must not pass the suffix check."""
    res = client.get("/api/thumb", params={"url": "https://ytimg.com.evil.test/x.jpg"})
    assert res.status_code == 403


def test_thumb_proxy_accepts_allowed_host(client, monkeypatch):
    import server

    url = "https://i.ytimg.com/vi/abc/hq720.jpg"
    monkeypatch.setitem(server._THUMB_BYTES, url, (b"\xff\xd8jpeg", "image/jpeg"))

    res = client.get("/api/thumb", params={"url": url})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.content == b"\xff\xd8jpeg"


# --------------------------------------------------- podcast episodes

def test_episode_seconds_parses_the_shapes_get_podcast_returns():
    import server

    assert server._episode_seconds("23 min") == 1380
    assert server._episode_seconds("1 hr 4 min") == 3840
    assert server._episode_seconds("45 sec") == 45
    assert server._episode_seconds("12:34") == 754
    assert server._episode_seconds("1:02:03") == 3723
    assert server._episode_seconds("") == 0
    assert server._episode_seconds(None) == 0
    assert server._episode_seconds("nonsense") == 0


def test_podcast_episodes_uses_get_podcast_not_get_playlist(client, monkeypatch):
    """get_playlist raises on a podcast browse id — that's why episode lists
    came back empty. get_podcast is the right call."""
    import server

    class FakeYT:
        def get_podcast(self, show_id, limit=50):
            return {
                "title": "Show", "author": {"name": "Author"},
                "thumbnails": [{"url": "https://i.ytimg.com/x.jpg", "width": 100}],
                "episodes": [{"videoId": "v1", "title": "Ep 1", "duration": "23 min"}],
            }

        def get_playlist(self, *a, **kw):
            raise AssertionError("get_playlist must not be used for podcasts")

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/podcasts/SHOW").json()

    assert data["title"] == "Show"
    assert data["author"] == "Author"
    assert len(data["episodes"]) == 1
    ep = data["episodes"][0]
    assert ep["duration"] == 1380
    assert ep["durationFormatted"] == "23:00"
    # Episodes stream through the same proxy as everything else.
    assert ep["audioUrl"] == "/api/stream/v1"


def test_podcast_episodes_falls_back_to_playlist(client, monkeypatch):
    import server

    class FakeYT:
        def get_podcast(self, *a, **kw):
            raise RuntimeError("not a podcast")

        def get_playlist(self, show_id, limit=50):
            return {"title": "P", "tracks": [{"videoId": "v2", "title": "T", "duration_seconds": 60}]}

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/podcasts/SHOW").json()

    assert [e["id"] for e in data["episodes"]] == ["v2"]


def test_podcast_episodes_empty_when_both_calls_fail(client, monkeypatch):
    import server

    class FakeYT:
        def get_podcast(self, *a, **kw):
            raise RuntimeError("no")

        def get_playlist(self, *a, **kw):
            raise RuntimeError("no")

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    assert client.get("/api/podcasts/SHOW").json()["episodes"] == []


# ------------------------------------------------- library folder stats

def test_browse_reports_folder_counts_and_size(client, tmp_music_root):
    songs = tmp_music_root / "Songs" / "Artist"
    songs.mkdir(parents=True)
    (songs / "a.mp3").write_bytes(b"x" * 10)
    (songs / "b.mp3").write_bytes(b"x" * 20)
    (tmp_music_root / "Songs" / "notes.txt").write_bytes(b"ignored")

    data = client.get("/api/library/browse").json()
    entry = next(e for e in data["entries"] if e["name"] == "Songs")

    assert entry["track_count"] == 2
    assert entry["size_bytes"] == 30
    assert entry["folder_count"] == 1
    assert data["summary"]["total_tracks"] == 2


def test_browse_summary_counts_files_in_the_current_folder(client, tmp_music_root):
    (tmp_music_root / "x.mp3").write_bytes(b"x" * 5)

    summary = client.get("/api/library/browse").json()["summary"]

    assert summary["files"] == 1
    assert summary["total_size_bytes"] == 5


# --------------------------------------------------------- chip thumbs

def test_chip_thumbs_caches_and_does_not_refetch(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    calls = []

    def fake_resolve(term):
        calls.append(term)
        return f"https://i.ytimg.com/{term}.jpg"

    monkeypatch.setattr(server, "_resolve_chip_thumb", fake_resolve)

    first = client.get("/api/chip-thumbs", params={"terms": "Rock,Metal"}).json()
    second = client.get("/api/chip-thumbs", params={"terms": "Rock,Metal"}).json()

    assert first == second
    assert calls == ["Rock", "Metal"]   # resolved once each, then served from cache


def test_chip_thumbs_caches_misses_too(client, monkeypatch):
    """A term with no cover must not be retried on every page load."""
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    calls = []
    monkeypatch.setattr(server, "_resolve_chip_thumb",
                        lambda term: calls.append(term) or None)

    client.get("/api/chip-thumbs", params={"terms": "Nothing"})
    client.get("/api/chip-thumbs", params={"terms": "Nothing"})

    assert calls == ["Nothing"]
    # Entries carry a timestamp so stale signed URLs can age out.
    entry = server._thumb_cache["Nothing"]
    assert entry["url"] is None
    assert entry["at"] > 0


def test_chip_thumbs_caps_the_batch(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    monkeypatch.setattr(server, "_resolve_chip_thumb", lambda t: None)

    terms = ",".join(f"t{i}" for i in range(30))
    data = client.get("/api/chip-thumbs", params={"terms": terms}).json()

    assert len(data) == 12


# ------------------------------------------------------------ beat grid

def test_beats_endpoint_serves_the_cache_without_reinvoking(client, tmp_music_root, monkeypatch):
    """Inference costs seconds and the grid never changes for a given file."""
    import json as _json
    import server

    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "drums.wav").write_bytes(b"x")
    (stems / "beats.json").write_text(_json.dumps({"bpm": 120, "beats": [0.5, 1.0]}))

    def boom(*a, **kw):
        raise AssertionError("must not run the detector when a cache exists")

    monkeypatch.setattr(server.subprocess, "run", boom)

    data = client.get("/api/stems/Song/beats").json()

    assert data["bpm"] == 120


def test_beats_endpoint_falls_through_to_another_stem(client, tmp_music_root, monkeypatch):
    """Drumless material has to fall back rather than report no grid."""
    import subprocess as _sp
    import server

    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "drums.wav").write_bytes(b"x")
    (stems / "other.wav").write_bytes(b"x")

    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd[-1])
        if cmd[-1].endswith("drums.wav"):
            return _sp.CompletedProcess(cmd, 1, stdout='{"error": "only 0 beats detected"}', stderr="")
        return _sp.CompletedProcess(
            cmd, 0, stdout='{"bpm": 90.0, "beats": [1.0, 2.0], "downbeats": [1.0], "beats_per_bar": 4}',
            stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    data = client.get("/api/stems/Song/beats").json()

    assert data["source_stem"] == "other"
    assert data["bpm"] == 90.0
    assert [s.rsplit("/", 1)[-1] for s in seen] == ["drums.wav", "other.wav"]
    assert (stems / "beats.json").is_file()   # written through


def test_beats_endpoint_422_when_nothing_is_trackable(client, tmp_music_root, monkeypatch):
    import subprocess as _sp
    import server

    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "drums.wav").write_bytes(b"x")

    monkeypatch.setattr(server, "_demucs_interpreter", lambda: "/fake/python")
    monkeypatch.setattr(server.subprocess, "run",
                        lambda cmd, **kw: _sp.CompletedProcess(cmd, 1, stdout='{"error":"no"}', stderr=""))

    assert client.get("/api/stems/Song/beats").status_code == 422


def test_beats_endpoint_404_for_unknown_song(client, tmp_music_root):
    assert client.get("/api/stems/Nope/beats").status_code == 404


def test_beatgrid_bpm_uses_the_median_interval():
    """A mean would be dragged off by a single dropped beat; the median isn't."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "beatgrid", Path(__file__).resolve().parents[1] / "beatgrid.py")
    beatgrid = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(beatgrid)

    steady = [i * 0.5 for i in range(20)]            # 120 BPM
    assert round(beatgrid._bpm_from(steady)) == 120

    dropped = steady[:10] + [t + 0.5 for t in steady[10:]]   # one beat missed
    assert round(beatgrid._bpm_from(dropped)) == 120

    assert beatgrid._bpm_from([0.0]) is None
    assert beatgrid._bpm_from([0.0, 10.0]) is None   # 6 BPM — out of range


# ------------------------------------------------------ Czech podcasts

def test_podcast_search_uses_czech_queries_by_default(client, monkeypatch):
    """A bare "podcast" search on YouTube Music returns essentially nothing
    Czech, because it ranks by global popularity."""
    import server

    seen = []

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            seen.append(q)
            return [{"browseId": "id-" + q[:4], "title": q, "thumbnails": []}]

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    client.get("/api/podcasts", params={"region": "cz", "limit": 40})

    assert any("česk" in q or "Český" in q for q in seen)


def test_podcast_search_honours_an_explicit_query(client, monkeypatch):
    import server

    seen = []

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            seen.append(q)
            return []

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    client.get("/api/podcasts", params={"q": "true crime"})

    assert seen == ["true crime"]


def test_podcast_search_deduplicates_across_queries(client, monkeypatch):
    """The Czech queries overlap heavily; the same show must appear once."""
    import server

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            return [{"browseId": "same", "title": "Show", "thumbnails": []}]

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    rows = client.get("/api/podcasts", params={"region": "cz"}).json()

    assert len(rows) == 1


# -------------------------------------------------- lyrics fallback

def test_lyrics_fall_back_to_youtube_music(client, monkeypatch):
    """LRCLib knows almost no Czech repertoire, so an empty result there has
    to try YouTube Music rather than report "not found"."""
    import server

    monkeypatch.setattr(server, "_fetch_lrclib", lambda *a, **kw: None)

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            return [{"videoId": "abcdefghijk"}]

        def get_watch_playlist(self, videoId=None):
            return {"lyrics": "LYRICS_ID"}

        def get_lyrics(self, lid):
            return {"lyrics": "Vezmu tě má milá", "source": "Zdroj"}

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/lyrics/x", params={"q": "Kabát - Pohoda", "artist": "Kabát"}).json()

    assert data["plain"].startswith("Vezmu")
    assert data["synced"] is False


def test_lyrics_prefer_lrclib_when_it_has_synced_words(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_fetch_lrclib",
                        lambda *a, **kw: {"synced": True, "lines": [{"timeInMs": 0, "text": "hi"}],
                                          "plain": None, "source": "LRCLib"})

    def boom():
        raise AssertionError("must not reach YouTube Music when LRCLib answered")

    monkeypatch.setattr(server, "yt", boom)

    data = client.get("/api/lyrics/x", params={"q": "a - b", "artist": "a"}).json()

    assert data["source"] == "LRCLib"


def test_lyrics_return_empty_when_neither_source_has_it(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_fetch_lrclib", lambda *a, **kw: None)

    class FakeYT:
        def search(self, *a, **kw):
            return []

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/lyrics/x", params={"q": "nic - nic", "artist": "nic"}).json()

    assert data["plain"] is None
    assert data["lines"] is None


def test_thumb_cache_entries_expire(monkeypatch):
    """Some YouTube thumbnail URLs are signed and stop resolving after a few
    weeks; a permanent cache eventually serves nothing but 404s."""
    import server

    cache = {}
    server._thumb_put(cache, "Rock", "https://i.ytimg.com/x.jpg")

    hit, url = server._thumb_get(cache, "Rock")
    assert hit and url.endswith("x.jpg")

    cache["Rock"]["at"] -= server.THUMB_CACHE_TTL + 1
    hit, _ = server._thumb_get(cache, "Rock")
    assert hit is False        # aged out, so it gets re-resolved


def test_thumb_cache_reads_pre_ttl_entries(monkeypatch):
    """Caches written before entries were stamped are bare strings."""
    import server

    hit, url = server._thumb_get({"Old": "https://i.ytimg.com/old.jpg"}, "Old")

    assert hit is True
    assert url.endswith("old.jpg")


def test_art_lookup_prefers_the_artist_before_the_dash(client, monkeypatch):
    """Stem folders are named "Artist - Title", so the part before the dash is
    a real artist far more often than not."""
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    seen = []

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            seen.append((q, filter))
            return [{"thumbnails": [{"url": "https://i.ytimg.com/a.jpg", "width": 100}]}]

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/art", params={"q": "Sylosis - Erased"}).json()

    assert data["url"].endswith("a.jpg")
    assert seen[0] == ("Sylosis", "artists")


def test_art_lookup_falls_back_for_a_bare_name(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    seen = []

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            seen.append(filter)
            return [] if filter == "artists" else [
                {"thumbnails": [{"url": "https://i.ytimg.com/s.jpg", "width": 60}]}]

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    data = client.get("/api/art", params={"q": "Cerberus"}).json()

    assert data["url"].endswith("s.jpg")
    assert "songs" in seen


def test_art_lookup_caches_a_miss(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_thumb_cache", {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    calls = []

    class FakeYT:
        def search(self, q, filter=None, limit=0):
            calls.append(q)
            return []

    monkeypatch.setattr(server, "yt", lambda: FakeYT())

    client.get("/api/art", params={"q": "Nic"})
    before = len(calls)
    client.get("/api/art", params={"q": "Nic"})

    assert len(calls) == before


def test_art_lookup_rejects_an_empty_query(client):
    assert client.get("/api/art", params={"q": "  "}).json() == {"url": None}
