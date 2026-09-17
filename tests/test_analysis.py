"""Song sections and chords.

The model itself isn't exercised here — it needs torch and a minute per track.
What is exercised is everything around it: the shaping of its output, the
chord maths, and the endpoint's caching and containment.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze                                    # noqa: E402


# ------------------------------------------------------------- section shape

def test_edge_sentinels_are_folded_into_the_music():
    """All-In-One brackets a track with start/end markers. Left alone the
    first shows up as a two-second "Část" before the intro."""
    got = analyze._absorb_edge_slivers([
        {"start": 0.2, "end": 2.4, "kind": "part", "_sentinel": True},
        {"start": 2.4, "end": 40.0, "kind": "intro", "_sentinel": False},
        {"start": 40.0, "end": 41.0, "kind": "part", "_sentinel": True},
    ])

    assert [s["kind"] for s in got] == ["intro"]
    assert got[0]["start"] == 0.2 and got[0]["end"] == 41.0


def test_a_real_interior_part_survives():
    got = analyze._absorb_edge_slivers([
        {"start": 0.0, "end": 30.0, "kind": "intro", "_sentinel": False},
        {"start": 30.0, "end": 60.0, "kind": "part", "_sentinel": True},
        {"start": 60.0, "end": 90.0, "kind": "verse", "_sentinel": False},
    ])
    assert [s["kind"] for s in got] == ["intro", "part", "verse"]


def test_repeats_are_numbered_and_touching_spans_fused():
    got = analyze._merge_and_number([
        {"start": 0, "end": 10, "kind": "verse"},
        {"start": 10, "end": 20, "kind": "verse"},     # the same verse, split
        {"start": 20, "end": 30, "kind": "chorus"},
        {"start": 30, "end": 40, "kind": "verse"},
        {"start": 40, "end": 50, "kind": "chorus"},
    ])

    assert [s["name"] for s in got] == ["Sloka 1", "Refrén 1", "Sloka 2", "Refrén 2"]
    assert got[0]["end"] == 20
    assert all(s["color"].startswith("#") for s in got)


def test_a_single_section_of_a_kind_is_not_numbered():
    got = analyze._merge_and_number([
        {"start": 0, "end": 10, "kind": "intro"},
        {"start": 10, "end": 20, "kind": "verse"},
    ])
    assert [s["name"] for s in got] == ["Intro", "Sloka"]


def test_boundaries_snap_to_a_bar_line_when_one_is_close(tmp_path):
    grid = tmp_path / "beats.json"
    grid.write_text(json.dumps({
        "bpm": 120.0, "beats": [i * 0.5 for i in range(200)],
        "downbeats": [i * 2.0 for i in range(50)],
    }))

    got = analyze._snap_to_beats([
        {"start": 0.0, "end": 19.9, "kind": "intro", "name": "Intro", "color": "#fff"},
        {"start": 19.9, "end": 40.2, "kind": "verse", "name": "Sloka", "color": "#fff"},
    ], grid)

    # 19.9 is within a beat of the bar line at 20.0, and the boundary the two
    # sections share stays shared.
    assert got[0]["end"] == 20.0
    assert got[1]["start"] == 20.0


def test_a_boundary_far_from_any_bar_line_is_left_alone(tmp_path):
    grid = tmp_path / "beats.json"
    grid.write_text(json.dumps({
        "bpm": 120.0, "beats": [i * 0.5 for i in range(200)],
        "downbeats": [0.0, 40.0],
    }))
    got = analyze._snap_to_beats(
        [{"start": 0.0, "end": 18.0, "kind": "intro", "name": "I", "color": "#fff"},
         {"start": 18.0, "end": 40.0, "kind": "verse", "name": "S", "color": "#fff"}],
        grid)
    assert got[0]["end"] == 18.0


# -------------------------------------------------------------- chord shapes

def test_templates_cover_every_root_and_quality():
    templates, names, roots, quals = analyze._chord_templates()
    assert templates.shape == (12 * len(analyze.CHORD_QUALITIES), 12)
    assert "C" in names and "Am" in names and "F#5" in names and "Bmaj7" in names
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("notes, expected", [
    ([0, 4, 7], "C"),          # C major
    ([9, 0, 4], "Am"),         # A minor
    ([2, 6, 9], "D"),
    ([7, 2], "G5"),            # power chord: no third to find
])
def test_a_clean_chroma_matches_its_own_chord(notes, expected):
    """The scoring has to get an unambiguous chord right before any of the
    smoothing on top of it is worth anything."""
    import numpy as np

    chroma = np.zeros(12, dtype=np.float32)
    for n in notes:
        chroma[n] = 1.0
    chroma /= np.linalg.norm(chroma)

    templates, names, _roots, _quals = analyze._chord_templates()
    assert names[int((chroma @ templates.T).argmax())] == expected


def test_viterbi_holds_a_chord_through_one_bad_frame():
    """Frame-by-frame argmax flickers between relatives; that is the whole
    reason for the smoothing."""
    import numpy as np

    n_states = 12 * len(analyze.CHORD_QUALITIES)
    scores = np.zeros((7, n_states), dtype=np.float32)
    scores[:, 0] = 0.8
    scores[3, 0] = 0.55          # one frame where another chord edges ahead
    scores[3, 10] = 0.62

    path = analyze._viterbi(scores)
    assert list(path) == [0] * 7


def test_viterbi_still_follows_a_real_change():
    import numpy as np

    n_states = 12 * len(analyze.CHORD_QUALITIES)
    scores = np.zeros((8, n_states), dtype=np.float32)
    scores[:4, 0] = 0.9
    scores[4:, 10] = 0.9

    path = analyze._viterbi(scores)
    assert list(path) == [0, 0, 0, 0, 10, 10, 10, 10]


def test_beat_spans_run_to_the_end_of_the_track():
    spans = analyze._beat_spans([0.0, 0.5, 1.0], 2.0)
    assert spans == [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]


def test_repeated_chords_become_one_span():
    got = analyze._merge_chords([
        {"start": 0.0, "end": 0.5, "chord": "C"},
        {"start": 0.5, "end": 1.0, "chord": "C"},
        {"start": 1.0, "end": 1.5, "chord": "G"},
        {"start": 1.5, "end": 2.0, "chord": "G"},
    ])
    assert [(c["start"], c["end"], c["chord"]) for c in got] == [
        (0.0, 1.0, "C"), (1.0, 2.0, "G")]
    assert got[0]["beats"] == 2


def test_a_flicker_shorter_than_a_beat_is_absorbed():
    got = analyze._merge_chords([
        {"start": 0.0, "end": 2.0, "chord": "C"},
        {"start": 2.0, "end": 2.2, "chord": "Am"},   # a passing estimate
        {"start": 2.2, "end": 4.0, "chord": "C"},
    ])
    assert [c["chord"] for c in got] == ["C", "C"]
    assert got[0]["end"] == 2.2


def test_a_walking_bass_does_not_split_the_chord():
    """Deciding the bass note per beat and splitting on it produced a new span
    every other beat — a bass line walks, and a walking note is not a chord
    change."""
    import numpy as np

    chords = [{"start": 0.0, "end": 2.0, "chord": "C", "root": "C"}]
    spans = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
    bass = np.zeros((4, 12), dtype=np.float32)
    for i, note in enumerate([4, 4, 4, 7]):        # E E E G under a C chord
        bass[i][note] = 1.0

    analyze._attach_bass(chords, bass, spans)
    assert len(chords) == 1
    assert chords[0]["bass"] == "E"                # the majority, not the last


def test_no_bass_is_reported_when_the_beats_disagree():
    import numpy as np

    chords = [{"start": 0.0, "end": 2.0, "chord": "C", "root": "C"}]
    spans = [(0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0)]
    bass = np.zeros((4, 12), dtype=np.float32)
    for i, note in enumerate([4, 7, 9, 2]):
        bass[i][note] = 1.0

    analyze._attach_bass(chords, bass, spans)
    assert "bass" not in chords[0]


def test_the_root_in_the_bass_is_not_worth_saying():
    import numpy as np

    chords = [{"start": 0.0, "end": 1.0, "chord": "C", "root": "C"}]
    spans = [(0.0, 0.5), (0.5, 1.0)]
    bass = np.zeros((2, 12), dtype=np.float32)
    bass[0][0] = bass[1][0] = 1.0

    analyze._attach_bass(chords, bass, spans)
    assert "bass" not in chords[0]


# ------------------------------------------------------------------ endpoint

def test_analysis_refuses_a_path_outside_the_music_root(client, tmp_music_root):
    res = client.get("/api/stems/..%2F..%2Fetc/analysis")
    assert res.status_code == 404


def test_analysis_serves_the_cache_without_running_anything(
        client, tmp_music_root, monkeypatch):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "analysis.json").write_text(
        json.dumps({"sections": [{"start": 0, "end": 9, "name": "Intro"}],
                    "chords": [], "warnings": []}), encoding="utf-8")

    def explode(*a, **kw):
        raise AssertionError("a cached analysis must not re-run the model")

    monkeypatch.setattr(subprocess, "run", explode)
    data = client.get("/api/stems/Song/analysis").json()
    assert data["sections"][0]["name"] == "Intro"


def test_analysis_writes_its_result_to_the_cache(
        client, tmp_music_root, monkeypatch):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    fake_python = tmp_music_root / "python"
    fake_python.write_text("")
    monkeypatch.setattr(server, "ANALYZE_PYTHON", fake_python)

    class Done:
        returncode = 0
        stdout = '{"sections":[],"chords":[{"start":0,"end":1,"chord":"C"}],"warnings":[]}'
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Done())
    monkeypatch.setattr(server, "_ANALYSIS_LOCKS", {})

    data = client.get("/api/stems/Song/analysis").json()
    assert data["chords"][0]["chord"] == "C"
    assert json.loads((stems / "analysis.json").read_text())["chords"]


def test_analysis_reports_a_missing_analyser_rather_than_crashing(
        client, tmp_music_root, monkeypatch):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    monkeypatch.setattr(server, "ANALYZE_PYTHON", Path("/nope/python"))

    res = client.get("/api/stems/Song/analysis")
    assert res.status_code == 503
