"""Nahraná kytara: a set of recorded notes served to the page, and the notes' names read as Kontakt reads them."""
import importlib.util
import json
from pathlib import Path

import pytest

import guitar_samples as gs

_spec = importlib.util.spec_from_file_location("build_guitar_samples", Path(__file__).resolve().parent.parent / "tools" / "build_guitar_samples.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def test_notes_named_as_kontakt_names_them():
    # C3 is 60: Axe Machina's low strings of D standard, its 7th string an F
    assert build.midi_of("n01_D1") == 38 and build.midi_of("n01_G1") == 43 and build.midi_of("n01_D3") == 62
    assert build.midi_of("n06_A#0") == 34 and build.midi_of("n01_F0") == 29 and build.midi_of("rel") is None


def test_a_finished_sound_brings_its_power_chords(tmp_path):
    """Axe Machina's amped side ("b") and its power chords, found as the DI notes are: string, note, strength, take."""
    def wav(side, folder, word, st, note, v, r):
        d = tmp_path / "Samples" / side / folder / f"s{st}" / note / "dn"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"SICPAM_EGtr_{word}_s{st}_{note}_v{v}_dn_r{r}_{side}.wav").write_bytes(b"RIFF")
    for v in (1, 2, 3):
        wav("b", "Plucks", "sng_pluck", 2, "n01_D1", v, 1)
        wav("a", "Plucks", "sng_pluck", 2, "n01_D1", v, 1)
    wav("b", "Power Chords", "crd_sust", 2, "n01_D1", 3, 1)
    wav("b", "Power Chords", "crd_sust", 2, "n01_D1", 3, 2)
    (tmp_path / "Samples" / "b" / "Power Chords" / "s2" / "rel").mkdir()
    got = build.pick_files(tmp_path, "b", build.ALL)
    assert sorted((k, st, m, v, r) for k, st, m, v, r, _ in got) == [
        ("pc", 2, 38, 3, 1), ("pc", 2, 38, 3, 2), ("pluck", 2, 38, 2, 1), ("pluck", 2, 38, 3, 1)]
    assert all(f.name.endswith("_b.wav") for *_, f in got)
    assert [k for k, *_ in build.pick_files(tmp_path, "a")] == ["pluck", "pluck"]        # the DI side, notes only
    assert {s["id"]: s["kind"] for s in build.SETS}["axe-machina-di"] == "di"
    assert all(s.get("nam", "nam/").startswith("nam/") and s.get("ir", "ir/").startswith("ir/") for s in build.SETS)


@pytest.fixture
def one_set(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "ROOT", tmp_path)
    d = tmp_path / "axe-machina-di"
    d.mkdir()
    (d / "pluck-38-s2-v3-r1.flac").write_bytes(b"fLaC" + b"\0" * 60)
    (d / "manifest.json").write_text(json.dumps({"name": "Axe Machina (DI)", "strings": [{"s": 2, "open": 38, "top": 52}],
                                                 "layers": {"soft": 2, "full": 3}, "bytes": 64,
                                                 "samples": [["pluck", 38, 2, 3, 1, "pluck-38-s2-v3-r1.flac"]]}))
    return d


def test_a_set_served_to_the_page(client, one_set):
    assert client.get("/api/guitar-samples").json()["sets"] == [{"id": "axe-machina-di", "name": "Axe Machina (DI)", "samples": 1, "bytes": 64,
                                                               "kind": "di", "tone": "", "about": ""}]
    assert client.get("/api/guitar-samples/axe-machina-di/manifest.json").json()["strings"][0]["open"] == 38
    r = client.get("/api/guitar-samples/axe-machina-di/pluck-38-s2-v3-r1.flac")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/flac" and "immutable" in r.headers["cache-control"]
    # only a set's own notes
    assert client.get("/api/guitar-samples/axe-machina-di/manifest.json.tmp").status_code == 404
    assert client.get("/api/guitar-samples/axe-machina-di/..%2Fx.flac").status_code == 404
    assert client.get("/api/guitar-samples/nope/pluck-38-s2-v3-r1.flac").status_code == 404


def test_no_set_no_recorded_notes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "ROOT", tmp_path / "none")
    assert client.get("/api/guitar-samples").json()["sets"] == []


def test_the_di_set_comes_first(client, tmp_path, monkeypatch):
    """What a tone print plays through when nothing else is asked for; the finished sounds after it."""
    monkeypatch.setattr(gs, "ROOT", tmp_path)
    for sid, name, kind in (("axe-clean", "Clean · Fender Twin", "amped"), ("axe-machina-di", "Axe Machina (DI)", "di"),
                            ("axe-machina-amped", "Metal · Axe Machina", "amped")):
        (tmp_path / sid).mkdir()
        (tmp_path / sid / "manifest.json").write_text(json.dumps({"name": name, "kind": kind, "about": "x", "samples": []}))
    got = client.get("/api/guitar-samples").json()["sets"]
    assert [x["id"] for x in got] == ["axe-machina-di", "axe-clean", "axe-machina-amped"] and got[1]["kind"] == "amped"
