"""Skladatel: an idea, a few styles, and whole songs to choose from — made one
after another by a model beside the app; the chosen one goes into the library."""
import shutil
import subprocess
import time

import pytest

import server
import studio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(path, seconds, freq=220):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-af", "volume=2", str(path)], check=True)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """Stands in for the model: every variant is a second of tone, and what was asked is kept."""
    asked = []
    _tone(tmp_path / "made.mp3", 1)

    def make(p, v):
        asked.append(studio.payload(p, v))
        shutil.copy(tmp_path / "made.mp3", studio.PROJECTS / p["id"] / f"{v['id']}.mp3")
    monkeypatch.setattr(studio, "ready", lambda: True)
    monkeypatch.setattr(studio, "engine_start", lambda wait=0, model="": True)
    monkeypatch.setattr(studio, "engine_model", lambda name: None)
    monkeypatch.setattr(studio, "engine_up", lambda: True)
    monkeypatch.setattr(studio, "engine_stop", lambda: None)
    monkeypatch.setattr(studio, "_make", make)
    monkeypatch.setattr(studio, "IDLE", 0)
    yield asked
    for _ in range(100):                                   # the worker ends by itself once nothing is left
        w = studio._STATE["worker"]
        if w is None or not w.is_alive():
            break
        time.sleep(0.05)


def _done(client, pid):
    for _ in range(200):
        p = client.get(f"/api/studio/projects/{pid}").json()
        if p["variants"] and all(v["status"] in ("done", "error") for v in p["variants"]):
            return p
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_an_idea_becomes_songs_in_the_styles_asked_for(client, engine, tmp_path):
    st = client.get("/api/studio").json()
    assert st["ready"] and st["projects"] == [] and {"id": "thrash", "name": "Thrash metal"} in st["styles"]
    _tone(tmp_path / "riff.wav", 12)
    with open(tmp_path / "riff.wav", "rb") as f:
        p = client.post("/api/studio/projects", files={"file": ("Můj riff.wav", f, "audio/wav")}).json()
    assert p["name"] == "Můj riff" and p["idea"] and abs(p["idea_len"] - 12) < 0.3
    assert client.get(p["idea_url"]).status_code == 200
    r = client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["thrash", "ballad", "nonsense"], "per": 2, "length": 90})
    assert [v["name"] for v in r.json()["variants"]] == ["Thrash metal 1", "Thrash metal 2", "Rocková balada 1", "Rocková balada 2"]
    done = _done(client, p["id"])
    assert all(v["status"] == "done" and client.get(v["url"]).status_code == 200 for v in done["variants"])
    # the idea is kept as played and the song goes on from where it ends, without a singer
    a = engine[0]
    assert a["task_type"] == "repaint" and abs(a["repainting_start"] - 12) < 0.3 and a["repainting_end"] == 90 and a["audio_duration"] == 90
    assert a["lyrics"] == "[Instrumental]" and "thrash metal" in a["prompt"] and "no vocals" in a["prompt"]
    assert "rock ballad" in engine[2]["prompt"]


def test_a_text_is_sung_and_a_song_needs_no_idea(client, engine):
    p = client.post("/api/studio/projects?name=Noční jízda").json()
    assert p["idea"] is False and p["name"] == "Noční jízda"
    client.post(f"/api/studio/projects/{p['id']}/make", json={"custom": "dark  synth rock", "length": 60, "lang": "cs",
                                                               "lyrics": "Jedu tmou\na nikdo neví kam\n\nNoční jízda\nmě nese dál"})
    done = _done(client, p["id"])
    assert done["variants"][0]["name"] == "dark synth rock" and done["lyrics"].startswith("Jedu tmou")
    a = engine[0]
    assert "task_type" not in a and a["vocal_language"] == "cs" and "lead vocals" in a["prompt"]
    assert a["lyrics"] == "[Verse]\nJedu tmou\na nikdo neví kam\n\n[Chorus]\nNoční jízda\nmě nese dál"
    # parts the player marked themselves are left as they are; with an idea, it is the intro
    assert studio.lyrics_for("[Bridge]\nTicho", True) == "[Intro]\n\n[Bridge]\nTicho"


def test_what_cannot_be_made_is_said(client, engine, monkeypatch, tmp_path):
    p = client.post("/api/studio/projects").json()
    assert client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": []}).status_code == 400
    assert client.post("/api/studio/projects/ffffffffffff/make", json={"styles": ["punk"]}).status_code == 404
    assert client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": [s["id"] for s in studio.STYLES], "per": 4}).status_code == 400
    assert client.get("/api/studio/audio/../../x/idea.wav").status_code == 404
    assert client.get(f"/api/studio/audio/{p['id']}/project.json").status_code == 404
    _tone(tmp_path / "short.wav", 1)
    with open(tmp_path / "short.wav", "rb") as f:
        assert client.post("/api/studio/projects", files={"file": ("short.wav", f, "audio/wav")}).status_code == 400
    monkeypatch.setattr(studio, "ready", lambda: False)
    assert client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["punk"]}).status_code == 503


def test_a_failed_variant_says_why_and_the_rest_go_on(client, engine, monkeypatch):
    make, n = studio._make, {"n": 0}

    def flaky(p, v):
        n["n"] += 1
        if n["n"] == 1:
            raise RuntimeError("došla paměť")
        make(p, v)
    monkeypatch.setattr(studio, "_make", flaky)
    p = client.post("/api/studio/projects").json()
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["punk", "funk"]})
    done = _done(client, p["id"])
    assert [(v["status"], v.get("error")) for v in done["variants"]] == [("error", "došla paměť"), ("done", None)]
    assert client.delete(f"/api/studio/projects/{p['id']}/variants/{done['variants'][0]['id']}").json()["variants"][0]["status"] == "done"
    assert client.delete(f"/api/studio/projects/{p['id']}").json() == {"ok": True}
    assert client.get(f"/api/studio/projects/{p['id']}").status_code == 404


def test_the_chosen_variant_goes_into_the_library_and_to_be_separated(client, engine, tmp_music_root):
    p = client.post("/api/studio/projects?name=Bouřka").json()
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["grunge"]})
    v = _done(client, p["id"])["variants"][0]
    got = client.post(f"/api/studio/projects/{p['id']}/variants/{v['id']}/use", json={"separate": True}).json()
    assert got["rel"] == "Songs/Bouřka - Grunge.mp3" and (tmp_music_root / got["rel"]).is_file()
    assert got["stems"]["name"] == "Bouřka - Grunge" and got["stems"]["job_id"]
    assert client.get(f"/api/studio/projects/{p['id']}").json()["variants"][0]["used"] == got["rel"]
    # asked again, it is the same song — not a second copy
    again = client.post(f"/api/studio/projects/{p['id']}/variants/{v['id']}/use", json={"separate": False}).json()
    assert again["rel"] == got["rel"] and len(list((tmp_music_root / "Songs").iterdir())) == 1
    assert client.post(f"/api/studio/projects/{p['id']}/variants/{v['id']}/use", json={"voice": "nikdo"}).status_code == 404
    assert client.post(f"/api/studio/projects/{p['id']}/variants/xxxxxxxxxx/use", json={}).status_code == 404


def test_the_band_can_play_under_the_idea_from_its_first_note(client, engine, monkeypatch, tmp_path):
    real_make = studio.__dict__["_make_under"]
    asked, models = [], []
    _tone(tmp_path / "part.mp3", 12, freq=110)

    def ace(data, src=None):
        asked.append({**data, "src": src.name if src else None})
        return (tmp_path / "part.mp3").read_bytes()
    monkeypatch.setattr(studio, "_ace", ace)
    monkeypatch.setattr(studio, "engine_model", models.append)
    monkeypatch.setattr(studio, "under_ready", lambda: True)
    monkeypatch.setattr(studio, "_make", lambda p, v: real_make(p, v) if v.get("mode") == "under" else None)
    _tone(tmp_path / "riff.wav", 12)
    with open(tmp_path / "riff.wav", "rb") as f:
        p = client.post("/api/studio/projects", files={"file": ("riff.wav", f, "audio/wav")}).json()
    assert client.get("/api/studio").json()["under"] is True
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["punk"], "length": 60, "mode": "under"})
    v = _done(client, p["id"])["variants"][0]
    assert v["status"] == "done" and v["mode"] == "under", v
    # drums to the guitar, bass to guitar and drums — the big model — then the quick one carries the song on
    assert [a["src"] for a in asked] == ["idea.wav", "with-drums.wav", "band.wav"]
    assert "DRUMS" in asked[0]["instruction"] and "BASS" in asked[1]["instruction"] and asked[0]["task_type"] == "lego"
    assert asked[2]["task_type"] == "repaint" and abs(asked[2]["repainting_start"] - 12) < 0.3
    assert models == [studio.BASE_MODEL, studio.TURBO_MODEL]
    assert not list((studio.PROJECTS / p["id"]).glob("*.work"))
    # a song no longer than the idea is the accompaniment alone, mixed here — the quick model is not asked
    asked.clear(); models.clear()
    monkeypatch.setattr(studio, "LENGTHS", (20, 60))
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["funk"], "length": 20, "mode": "under"})
    done = _done(client, p["id"])["variants"][-1]
    assert done["status"] == "done" and done["length"] == 12 and len(asked) == 2 and models == [studio.BASE_MODEL]


def test_parts_are_mixed_at_even_loudness_without_clipping():
    import numpy as np
    loud = np.ones((48000, 2), dtype=np.float32) * 0.9
    quiet = np.ones((40000, 2), dtype=np.float32) * 0.01
    out = studio.mix_band([(loud, 1.0), (quiet, 1.0), (loud, 0.5)])
    assert out.shape == (40000, 2) and float(np.abs(out).max()) <= 0.921


def test_the_part_under_the_idea_is_brought_up_to_the_rest():
    import numpy as np
    song = np.concatenate([np.full((96000, 2), 0.05, dtype=np.float32), np.full((96000, 2), 0.2, dtype=np.float32)])
    out = studio.even_out(song, 96000)
    assert abs(float(out[:90000].mean()) - 0.15) < 0.01   # three times at the most and abs(float(out[-1000:].mean()) - 0.2) < 0.001
    assert studio.even_out(song[::-1].copy(), 96000) is not None and float(np.abs(out).max()) <= 0.97
