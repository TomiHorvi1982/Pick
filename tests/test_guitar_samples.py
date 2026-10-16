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
    assert client.get("/api/guitar-samples").json()["sets"] == [{"id": "axe-machina-di", "name": "Axe Machina (DI)", "samples": 1, "bytes": 64}]
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
