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
        return {"seed": "4242", "metas": {"bpm": 120, "keyscale": "A minor"}}

    def separate(pid, vid):                                # Demucs: six stems, each the same tone
        d = studio.stems_dir(pid, vid)
        d.mkdir(parents=True, exist_ok=True)
        for s in studio.STEMS:
            shutil.copy(tmp_path / "made.mp3", d / f"{s}.mp3")
    monkeypatch.setattr(studio, "ready", lambda: True)
    monkeypatch.setattr(studio, "model_ready", lambda k: k in studio.MODELS)
    monkeypatch.setattr(studio, "separate", separate)
    monkeypatch.setattr(studio, "engine_start", lambda wait=0, model="", lm=False: True)
    monkeypatch.setattr(studio, "engine_model", lambda name, lm=False: None)
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


def _done(client, pid, stems=False):
    for _ in range(300):
        p = client.get(f"/api/studio/projects/{pid}").json()
        if p["variants"] and all(v["status"] in ("done", "error") for v in p["variants"]) and \
                (not stems or all(v.get("stems") in (None, "done", "error") for v in p["variants"])):
            return p
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_an_idea_becomes_songs_in_the_styles_asked_for(client, engine, tmp_path):
    st = client.get("/api/studio").json()
    assert st["ready"] and st["projects"] == [] and any(s["id"] == "thrash" and "thrash metal" in s["caption"] for s in st["styles"])
    assert {c["id"] for c in st["words"]} >= {"genre", "guitar", "voice", "mood", "sound"} and st["tags"] and st["tips"]["lyrics"]
    assert [m["id"] for m in st["models"]] == ["turbo", "sft", "base"] and st["progress"] is None
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
    client.post(f"/api/studio/projects/{p['id']}/make", json={"desc": "dark  synth rock", "length": 60, "lang": "cs",
                                                               "lyrics": "Jedu tmou\na nikdo neví kam\n\nNoční jízda\nmě nese dál"})
    done = _done(client, p["id"])
    assert done["variants"][0]["name"] == "Můj popis" and done["lyrics"].startswith("Jedu tmou") and done["desc"] == "dark synth rock"
    a = engine[0]
    assert "task_type" not in a and a["vocal_language"] == "cs" and "lead vocals" in a["prompt"] and a["prompt"].startswith("dark synth rock")
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
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["grunge"], "opts": {"separate": False}})
    v = _done(client, p["id"])["variants"][0]
    assert v.get("stems") is None and v["stem_list"] == []
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

    def ace(data, src=None, progress=None):
        asked.append({**data, "src": src.name if src else None})
        if progress:
            progress(0.5, "x")
        return (tmp_path / "part.mp3").read_bytes(), {}
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


def test_a_finished_song_is_taken_apart_and_heard_instrument_by_instrument(client, engine, tmp_music_root):
    p = client.post("/api/studio/projects?name=Vrstvy").json()
    client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["heavy"], "length": 30})
    v = _done(client, p["id"], stems=True)["variants"][0]
    assert v["stems"] == "done" and v["stem_list"] == list(studio.STEMS) and v["seed"] == "4242" and v["metas"]["bpm"] == 120
    pk = client.get(f"/api/studio/peaks/{p['id']}/{v['id']}").json()
    assert len(pk["mix"]) == 1200 and set(pk["stems"]) == set(studio.STEMS) and 0.1 < max(pk["mix"]) <= 1.0 and pk["length"] > 0.9
    assert client.get(f"/api/studio/audio/{p['id']}/{v['id']}/drums.mp3").status_code == 200
    assert client.get(f"/api/studio/audio/{p['id']}/{v['id']}/cowbell.mp3").status_code == 404
    # chosen, its stems go straight into Steems — no second separation
    got = client.post(f"/api/studio/projects/{p['id']}/variants/{v['id']}/use", json={"separate": True}).json()
    assert got["stems"]["ready"] is True and got["stems"]["job_id"] is None
    assert sorted(f.stem for f in (tmp_music_root / "Stems" / "Vrstvy - Heavy metal").glob("*.mp3")) == sorted(studio.STEMS)
    # thrown away, the stems go with it
    client.delete(f"/api/studio/projects/{p['id']}/variants/{v['id']}")
    assert not studio.stems_dir(p["id"], v["id"]).exists()


def test_every_setting_reaches_the_model_as_it_should():
    p = {"id": "abcdef012345", "lyrics": "", "lang": "en", "idea": False, "idea_len": 0}
    o = studio.clean_options(studio.Options(model="sft", think=True, steps=500, guidance=99, bpm=300, key="A minor", timesig="3",
                                            seed=77, method="sde", adg=True, lm_temperature=0.5))
    assert o["steps"] == 200 and o["guidance"] == 15 and o["bpm"] == 240 and o["seed"] == 77
    v = {"caption": "doom metal", "desc": "female vocals", "length": 120, "opts": o}
    a = studio.payload(p, v)
    assert a["prompt"] == "doom metal, female vocals, instrumental, no vocals" and a["inference_steps"] == 200
    assert a["guidance_scale"] == 15 and a["use_adg"] is True and a["infer_method"] == "sde" and a["thinking"] is True
    assert a["lm_temperature"] == 0.5 and a["bpm"] == 240 and a["key_scale"] == "A minor" and a["time_signature"] == "3"
    assert a["seed"] == 77 and a["use_random_seed"] is False
    # the quick model has no guidance to set; the planner has nothing to plan when going on from an idea
    t = studio.payload({**p, "idea": True, "idea_len": 10.0}, {**v, "opts": studio.clean_options(studio.Options(think=True, key="nonsense"))})
    assert "guidance_scale" not in t and t["thinking"] is False and "key_scale" not in t and t["inference_steps"] == 8
    assert t["task_type"] == "repaint" and t["use_random_seed"] is True
    assert studio._form({"a": True, "b": 2.5}) == {"a": "true", "b": "2.5"}


def test_a_sound_reference_is_kept_with_the_project(client, engine, tmp_path):
    p = client.post("/api/studio/projects?name=Předloha").json()
    _tone(tmp_path / "ref.mp3", 8)
    with open(tmp_path / "ref.mp3", "rb") as f:
        got = client.post(f"/api/studio/projects/{p['id']}/reference", files={"file": ("Metallica - One.mp3", f, "audio/mpeg")}).json()
    assert got["ref"] is True and got["ref_name"] == "Metallica - One"
    v = {"caption": "x", "length": 30, "opts": studio.clean_options(studio.Options(ref_strength=0.3))}
    a = studio.payload(studio.load(p["id"]), v)
    assert a["reference_audio_path"].endswith("ref.wav") and a["audio_cover_strength"] == 0.3
    assert "reference_audio_path" not in studio.payload(studio.load(p["id"]), {**v, "opts": {**v["opts"], "use_ref": False}})
    assert client.delete(f"/api/studio/projects/{p['id']}/reference").json()["ref"] is False


def test_a_model_not_downloaded_is_refused(client, engine, monkeypatch):
    monkeypatch.setattr(studio, "model_ready", lambda k: k == "turbo")
    p = client.post("/api/studio/projects").json()
    r = client.post(f"/api/studio/projects/{p['id']}/make", json={"styles": ["punk"], "opts": {"model": "sft"}})
    assert r.status_code == 503 and "SFT" in r.json()["error"]


def test_how_far_it_is_and_how_long_is_left(monkeypatch):
    p = {"id": "abcdef012345", "name": "Song", "idea_len": 0, "variants": [
        {"id": "aaaaaaaaaa", "name": "Punk", "length": 60, "status": "running", "started": time.time() - 30, "opts": {}},
        {"id": "bbbbbbbbbb", "name": "Funk", "length": 60, "status": "queued", "opts": {}}]}
    monkeypatch.setattr(studio, "projects", lambda: [p])
    monkeypatch.setitem(studio._STATE, "live", {"aaaaaaaaaa": {"pct": 0.5, "stage": "song", "t0": time.time() - 30, "p0": 0.0}})
    g = studio.progress_now()
    assert g["now"]["name"] == "Punk" and g["now"]["pct"] == 0.5 and 25 <= g["now"]["left"] <= 35 and g["count"] == 2
    assert g["left"] > g["now"]["left"] + studio.estimate(60)          # the queued one, and taking both apart
    monkeypatch.setitem(studio._STATE, "live", {})
    assert studio.progress_now()["now"]["left"] >= 10                   # before the model says, from the estimate
    # the model's own count starts late (it wakes up first): the time left follows its pace from then on
    studio._live("aaaaaaaaaa", 0.1, "song")
    studio._STATE["live"]["aaaaaaaaaa"]["t0"] -= 20
    studio._live("aaaaaaaaaa", 0.5, "song")
    assert 15 <= studio.progress_now()["now"]["left"] <= 25


def test_the_lyricist_is_asked_in_the_shape_the_model_sings(client, monkeypatch):
    import lyricist
    text = lyricist.prompt({"lang": "cs", "length": 120, "about": "noční jízda", "words": "asfalt", "mode": "complete",
                            "text": "Jedu tmou", "desc": "punk rock", "idea": True}, studio.LANGS)
    assert "česky" in text and "120 seconds" in text and "noční jízda" in text and "asfalt" in text and "Jedu tmou" in text
    assert "punk rock" in text and "instrumental idea" in text and "[Intro]" in lyricist.SYSTEM
    got = lyricist.parse('TITLE: „Noční jízda“\nSOUND: punk rock, raw\nLYRICS:\n[Verse 1]\nJedu tmou\n\n[Chorus]\nDál')
    assert got == {"title": "Noční jízda", "sound": "punk rock, raw", "lyrics": "[Verse 1]\nJedu tmou\n\n[Chorus]\nDál"}
    assert lyricist.tidy("[Bridge]\n[Bridge - whispered]\nSvětla mizí [cs]\n(Fast, melodic, technically demanding solo)\n(ooh)") == \
        "[Bridge - whispered]\nSvětla mizí\n(ooh)"
    assert client.post("/api/studio/lyrics", json={"about": " "}).status_code == 400
    monkeypatch.setattr(lyricist, "available", lambda: [])
    r = client.post("/api/studio/lyrics", json={"about": "bouřka"})
    assert r.status_code == 503 and "API klíč" in r.json()["error"]
    monkeypatch.setattr(lyricist, "write", lambda req, langs: {"title": "T", "sound": "s", "lyrics": "[Verse]\n" + req["about"], "by": "Claude"})
    assert client.post("/api/studio/lyrics", json={"about": "bouřka"}).json()["lyrics"] == "[Verse]\nbouřka"
