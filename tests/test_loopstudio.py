"""Loop Studio: seconds into bars, the plan, the Loopy Pro card, and loops cut
to a whole number of bars — to the sample."""

import shutil
import subprocess
import wave

import pytest

import loopstudio as ls

GRID = {"bpm": 119.96, "beats_per_bar": 4, "duration": 40.0, "detector": "test",
        "downbeats": [round(0.5 + i * 2.0, 3) for i in range(17)], "tempo_segments": [{"from": 0, "to": 40, "bpm": 119.96}]}
ANALYSIS = {"sections": [{"start": 0.52, "end": 8.6, "kind": "intro", "name": "Intro"},
                         {"start": 8.6, "end": 16.4, "kind": "verse", "name": "Sloka 1"},
                         {"start": 16.4, "end": 32.5, "kind": "chorus", "name": "Refrén 1"}],
            "chords": [{"start": 0.5, "end": 8.5, "chord": "Em"}, {"start": 8.5, "end": 10.5, "chord": "Em"},
                       {"start": 10.5, "end": 12.5, "chord": "C"}, {"start": 12.5, "end": 14.5, "chord": "Em"},
                       {"start": 14.5, "end": 16.5, "chord": "C"}, {"start": 16.5, "end": 32.5, "chord": "G"}]}
ACTIVITY = {"drums": [False, True, True], "bass": [True, True, True], "guitar": [True, True, True], "vocals": [False, True, True]}


def plan():
    return ls.build_plan("Test", GRID, ANALYSIS, {"key": "E moll", "confidence": 0.8}, ACTIVITY, ls.DEFAULT_PROFILE)


def test_sections_land_on_bar_lines_with_a_chord_per_bar():
    s = plan()["facts"]["sections"]
    assert [(x["label"], x["startBar"], x["bars"]) for x in s] == [("Intro", 1, 4), ("Sloka 1", 5, 4), ("Refrén 1", 9, 8)]
    assert [c["chord"] for c in s[1]["chords"]] == ["Em", "C", "Em", "C"]
    assert s[1]["startSec"] == 8.5 and s[1]["endSec"] == 16.5          # the bar lines, not the analysis' own seconds


def test_the_tempo_of_the_plan_is_the_click_the_song_is_on():
    assert ls.plan_bpm(119.96) == 120.0 and ls.plan_bpm(89.97) == 90.0 and ls.plan_bpm(126.5) == 126.5
    assert plan()["facts"]["bpm"] == 120.0 and plan()["facts"]["bpmMeasured"] == 119.96


def test_a_loop_is_the_part_of_a_section_that_repeats_and_its_layers_come_in_recording_order():
    loops = {l["label"]: l for l in plan()["loops"]}
    assert loops["Sloka 1"]["bars"] == 2 and loops["Sloka 1"]["sectionBars"] == 4       # Em C, twice
    assert loops["Sloka 1"]["layers"] == ["drums", "bass", "guitar", "vocals"]          # groove first, voice last
    assert loops["Intro"]["layers"] == ["bass", "guitar"]                                # no drums in the intro


def test_loopy_card_follows_the_song_and_the_rig():
    card = plan()["loopy"]
    assert card["template"] == "Verse-Chorus Looper" and card["masterBars"] == 2 and card["phaseLocked"] is True
    assert [(f["button"], f["cc"]) for f in card["footswitch"]] == [(1, 20), (2, 21), (3, 22), (4, 23)]
    assert card["recordOrder"][0].startswith("bicí") and card["recordOrder"][-1] == "zpěv"
    # loops whose lengths share nothing run free; no verse/chorus → the four-track template
    odd = ls.loopy_card({"bpm": 100, "timeSignature": "4/4"}, [{"bars": 3, "kind": "riff", "layers": ["drums"]},
                                                              {"bars": 7, "kind": "solo", "layers": ["guitar"]}], {"looping": {}})
    assert odd["phaseLocked"] is False and odd["template"] == "Simple 4 Track Looper" and odd["footswitch"] == []


def test_nothing_is_made_up_when_the_analysis_has_no_chords_or_key():
    p = ls.build_plan("Test", GRID, {"sections": ANALYSIS["sections"], "chords": []}, None, ACTIVITY, ls.DEFAULT_PROFILE)
    assert p["facts"]["key"] is None and "akordy" in p["unavailable"] and "tónina" in p["unavailable"]
    assert all(l["bars"] == l["sectionBars"] for l in p["loops"])        # no chords, so no claim about what repeats


def test_corrections_move_the_seconds_with_the_bars():
    p = ls.apply_edits(plan(), {"facts": {"bpm": 118, "key": "G dur", "sections": [{"id": "s2", "startBar": 6, "bars": 2, "label": "Riff"}]},
                                "loops": [{"id": "l1", "enabled": False}, {"id": "l2", "bars": 4}]}, GRID, ls.DEFAULT_PROFILE)
    s = p["facts"]["sections"][1]
    assert (s["label"], s["startBar"], s["bars"], s["startSec"], s["endSec"]) == ("Riff", 6, 2, 10.5, 14.5)
    assert p["facts"]["bpm"] == 118 and p["facts"]["key"] == "G dur" and p["edited"] is True
    assert p["loops"][0]["enabled"] is False and p["loops"][1]["bars"] == 4 and p["loopy"]["masterBars"] == 2    # the chorus still loops in twos


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_loops_are_cut_to_the_bar_to_the_sample(tmp_path):
    ff = shutil.which("ffmpeg")
    for role, hz in (("drums", 110), ("bass", 55), ("guitar", 220), ("vocals", 440)):
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=36", "-ac", "2", str(tmp_path / f"{role}.wav")], check=True)
    p = plan()
    made = ls.cut_loops(p, GRID, tmp_path, ff)
    verse = next(l for l in p["loops"] if l["label"] == "Sloka 1")
    assert {f["stem"] for f in verse["files"]} == {"drums", "bass", "guitar", "vocals", "_mix"}
    for f in verse["files"]:
        with wave.open(str(tmp_path / f["path"])) as w:
            assert w.getframerate() == 44100 and w.getnframes() == 176400          # 2 bars of 4/4 at 120 BPM, exactly
    assert abs(verse["stretch"]) < 0.1 and len(made) >= 9
    z = ls.zip_loops(p, tmp_path)
    import zipfile
    names = zipfile.ZipFile(z).namelist()
    assert "Loop Plan.txt" in names and any(n.endswith("drums.wav") for n in names)
    # a disabled loop is not cut
    p["loops"][0]["enabled"] = False
    ls.cut_loops(p, GRID, tmp_path, ff)
    assert not any("Intro" in str(x) for x in (tmp_path / "Loops").rglob("*.wav"))


def test_the_key_listens_to_the_chords_and_admits_when_it_is_unsure():
    # A song in C minor that sits on G a lot: the chords say C minor.
    chords = [{"root": "C", "quality": "min", "start": 0, "end": 40}, {"root": "F", "quality": "min", "start": 40, "end": 60},
              {"root": "G", "quality": "maj", "start": 60, "end": 80}, {"root": "G#", "quality": "maj", "start": 80, "end": 95}]
    k = ls.estimate_key(None, chords=chords)
    assert k["key"] == "C moll" and k["fromChords"] == "C moll" and k["fromSound"] is None
    assert ls.estimate_key(None, chords=[]) is None                     # nothing to go on: no key, not a guess
    assert 0 <= k["confidence"] <= 1
