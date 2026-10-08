"""Loop Studio: seconds into bars, the plan, the Loopy Pro card, and loops cut
to a whole number of bars — to the sample."""

import shutil
import subprocess
import wave

import numpy as np
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


# ---- the arrangement and what to play
def test_chord_becomes_notes_for_the_keys():
    c = ls.chord_notes("Cm")
    assert c["bassName"] == "C2" and c["names"] == ["C3", "D#3", "G3"]
    assert ls.chord_notes("G#maj7")["names"] == ["G#2", "C3", "D#3", "G3"]      # high roots drop an octave
    assert ls.chord_notes("Bb")["bass"] == ls.chord_notes("A#")["bass"]
    assert ls.chord_notes("E5")["notes"] == [52, 59]
    assert ls.chord_notes("Cweird")["known"] is False and ls.chord_notes("-") is None and ls.chord_notes("N") is None


def _kit(rate, bars, bpm=120.0):
    """Four on the floor with a backbeat and eighth-note hats, as sound."""
    rng = np.random.default_rng(1)
    bar = 4 * 60 / bpm
    x = np.zeros(int(bars * bar * rate) + rate, np.float32)

    def put(t, sig):
        i = int(t * rate)
        x[i:i + sig.size] += sig[: x.size - i]
    tk = np.arange(int(0.12 * rate)) / rate
    kick = (np.sin(2 * np.pi * (55 + 60 * np.exp(-tk * 40)) * tk) * np.exp(-tk * 28)).astype(np.float32)
    ts = np.arange(int(0.09 * rate)) / rate
    snare = ((rng.standard_normal(ts.size) * 0.5 + np.sin(2 * np.pi * 400 * ts) * 0.6) * np.exp(-ts * 35)).astype(np.float32)
    th = np.arange(int(0.03 * rate)) / rate
    hat = np.diff(rng.standard_normal(th.size + 2), 2).astype(np.float32) * np.exp(-th * 160).astype(np.float32) * 0.12
    for b in range(bars):
        for beat in range(4):
            put(b * bar + beat * bar / 4, kick)
            if beat in (1, 3):
                put(b * bar + beat * bar / 4, snare)
            put(b * bar + beat * bar / 4, hat)
            put(b * bar + (beat + 0.5) * bar / 4, hat)
    return x, [b * bar for b in range(bars + 1)]


def test_groove_is_read_off_the_drums():
    x, lines = _kit(22050, 8)
    g = ls.drum_groove(x, 22050, lines, 4)
    assert g["steps"] == 16 and g["bars"] == 8 and g["share"] > 0.9
    assert g["kick"] == [1, 0, 0, 0] * 4
    assert g["snare"] == [0, 0, 0, 0, 1, 0, 0, 0] * 2
    assert [g["hat"][k] for k in (2, 6, 10, 14)] == [1, 1, 1, 1]            # the off-beats, where nothing covers them
    assert ls.drum_groove(np.zeros(22050 * 4, np.float32), 22050, [0, 2, 4], 4) is None


def _plan():
    sections = [{"id": "s1", "label": "Sloka", "kind": "verse", "startBar": 1, "bars": 2, "startSec": 0.0, "endSec": 4.0,
                 "chords": [{"bar": 1, "chord": "Am"}, {"bar": 2, "chord": "G"}]},
                {"id": "s2", "label": "Refrén", "kind": "chorus", "startBar": 3, "bars": 1, "startSec": 4.0, "endSec": 6.0,
                 "chords": [{"bar": 3, "chord": "C"}]}]
    return {"facts": {"sections": sections, "bpm": 120.0, "beatsPerBar": 4, "timeSignature": "4/4", "key": None}, "loops": [], "fixedTempo": True}


def test_arrangement_orders_and_repeats_the_sections():
    plan = _plan()
    assert [s["id"] for s in ls.sequence(plan)] == ["s1", "s2"]                 # the song's own order to begin with
    plan = ls.apply_edits(plan, {"arrangement": [{"sectionId": "s2", "repeats": 2}, {"sectionId": "s1"}, {"sectionId": "nope"}]},
                          {"downbeats": [0, 2, 4, 6]}, ls.DEFAULT_PROFILE)
    assert [s["id"] for s in ls.sequence(plan)] == ["s2", "s2", "s1"] and plan["edited"]
    parts = {"sections": [{"sectionId": "s1", "drums": {"steps": 16, "kick": [1] + [0] * 15, "snare": [0] * 16, "hat": [0] * 16}},
                          {"sectionId": "s2", "drums": None}]}
    n = ls.arrangement_notes(plan, parts)
    assert n["seconds"] == 8.0
    # C twice (a bar each), then Am and G: the bass plays the roots in that order
    assert [(b["m"], b["t"]) for b in n["bass"]] == [(36, 0.0), (36, 2.0), (45, 4.0), (43, 6.0)]
    assert [d["t"] for d in n["drums"]] == [4.0, 6.0] and all(d["m"] == 36 for d in n["drums"])
    assert len(n["chords"]) == 12


def test_midi_file_is_well_formed():
    data = ls.midi_bytes([{"m": 60, "t": 0.0, "d": 0.5, "v": 0.8}, {"m": 64, "t": 0.5, "d": 0.5, "v": 0.5}], 120.0, "Zkouška", 0)
    assert data[:4] == b"MThd" and data[14:18] == b"MTrk"
    assert int.from_bytes(data[18:22], "big") == len(data) - 22
    assert data.endswith(b"\xff\x2f\x00") and b"\x90\x3c" in data and b"\x80\x40" in data
    assert b"\x99\x24" in ls.midi_bytes([{"m": 36, "t": 0, "d": 0.1}], 100.0, "kit", 9)      # the drum channel
