"""The separation queue: several songs can be sent at once, the server works
through them one by one, and the library says where each song stands."""
import sys

import server


def _song(root, name="song.mp3"):
    (root / name).write_bytes(b"x")
    return name


def test_the_same_song_twice_is_one_job(client, tmp_music_root):
    rel = _song(tmp_music_root)
    first = client.post("/api/stems/separate", json={"rel": rel}).json()
    again = client.post("/api/stems/separate", json={"rel": rel}).json()
    assert again["job_id"] == first["job_id"]
    assert again["already"] is True


def test_several_songs_queue_up_in_order(client, tmp_music_root):
    a = client.post("/api/stems/separate", json={"rel": _song(tmp_music_root, "a.mp3")}).json()
    b = client.post("/api/stems/separate", json={"rel": _song(tmp_music_root, "b.mp3")}).json()
    assert (a["position"], b["position"]) == (1, 2)

    q = client.get("/api/stems/queue").json()
    assert q["active"] == 2
    assert [j["name"] for j in q["jobs"]] == ["a", "b"]
    assert [j["status"] for j in q["jobs"]] == ["queued", "queued"]


def test_the_library_shows_which_songs_have_stems(client, tmp_music_root):
    _song(tmp_music_root, "done.mp3")
    d = tmp_music_root / "Stems" / "done"
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    _song(tmp_music_root, "waiting.mp3")
    _song(tmp_music_root, "plain.mp3")
    client.post("/api/stems/separate", json={"rel": "waiting.mp3"})

    entries = {e["name"]: e for e in client.get("/api/library/browse").json()["entries"]}
    assert entries["done.mp3"]["stems"]["state"] == "ready"
    assert entries["waiting.mp3"]["stems"]["state"] == "queued"
    assert entries["waiting.mp3"]["stems"]["position"] == 1
    assert entries["plain.mp3"]["stems"] is None


def test_a_job_runs_to_the_end_and_its_stems_land_in_place(client, tmp_music_root, tmp_path):
    """Runs one job through the worker's own code with a stand-in for
    Demucs that prints a progress bar and writes the six stems."""
    rel = _song(tmp_music_root)
    job = server.JOBS[client.post("/api/stems/separate", json={"rel": rel}).json()["job_id"]]
    raw = tmp_music_root / "Stems" / ".raw" / "htdemucs_6s" / "song"
    fake = tmp_path / "fake_demucs.py"
    fake.write_text(
        "import sys, pathlib\n"
        "for p in (10, 55, 100):\n"
        "    sys.stdout.write(f'\\r{p}%|####|'); sys.stdout.flush()\n"
        "sys.stdout.write('\\nhotovo\\n')\n"
        f"d = pathlib.Path({str(raw)!r}); d.mkdir(parents=True, exist_ok=True)\n"
        f"for s in {server.STEM_NAMES!r}: (d / (s + '.mp3')).write_bytes(b'x')\n")
    job["cmd"] = [sys.executable, str(fake)]

    server._run_stems_job(job)

    assert job["status"] == "done"
    assert job["progress"] == 100.0
    assert job["lines"][-1] == "hotovo"
    # The bar's redraws collapse into one line instead of one per update.
    assert sum("%" in line for line in job["lines"]) == 1
    assert server._stems_ready("song")
