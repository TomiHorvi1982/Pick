"""Pick DJ: the deck arithmetic (beat grid, SYNC, loops, mixer curves) and the
deck player worklet. Both are JavaScript, so this runs them under Node."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def out():
    if NODE is None:
        pytest.skip("node is not installed")
    script = Path(__file__).parent / "js" / "dj_check.js"
    return json.loads(subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True).stdout)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_beat_positions_interpolate_and_extrapolate(out):
    first, half, before, past = out["beatAt"]
    assert close(first, 0) and close(half, 0.5) and close(before, -0.5)
    assert close(past, 399)                       # 199 beats + 100 s at 2 beats/s past the last one
    t0, t25, tneg, tpast = out["timeOfBeat"]
    assert close(t0, 0.5) and close(t25, 1.75) and close(tneg, 0.0) and close(tpast, 125.5)


def test_local_bpm_follows_the_grid(out):
    assert close(out["bpm"][0], 120, 1e-6) and close(out["bpm"][1], 100, 1e-6)
    early, late = out["driftBpm"]
    assert 100 < early < 101.5 and 108.5 < late < 110.1


def test_cues_and_loops_land_on_beats(out):
    assert out["nearest"] == [1.0, 1.5]
    assert close(out["loop4"]["start"], 3.0) and close(out["loop4"]["end"], 5.0)   # 4 beats from the beat under the playhead
    assert close(out["loopHalf"]["start"], 3.25) and close(out["loopHalf"]["end"], 3.5)
    assert out["loopNoGrid"] == {"start": 3.0, "end": 4.0}


def test_sync_matches_tempo_and_phase(out):
    s = out["sync"]
    assert close(s["rate"], 120 * 1.05 / 100, 1e-6)
    assert close(s["phaseError"], -1 / 3, 1e-6)
    assert close(out["syncPhaseAfter"], 0.25, 1e-6)  # the follower now sits where the leader is in its beat
    assert close(out["syncWrap"], -0.2, 1e-6)    # 0.9 → 0.1 is a fifth of a beat, never 0.8


def test_sync_counts_half_time_as_the_same_groove(out):
    s = out["halfTime"]
    assert close(s["rate"], 128 / 126, 1e-6) and s["factor"] == 2
    assert close(s["phaseError"], 0.0, 1e-6)      # 20.25 beats of 64 = 40.5 beats of 128: already in phase


def test_mixer_curves(out):
    full_a, mid, quarter, full_b = out["xf"]
    assert full_a == {"a": 1, "b": 0} or close(full_a["b"], 0, 1e-9)
    assert mid == {"a": 1, "b": 1}
    assert close(quarter["a"], math.cos(math.pi / 4)) and quarter["b"] == 1
    assert close(full_b["a"], 0, 1e-9) and full_b["b"] == 1
    kill, flat, boost, cut = out["eq"]
    assert kill == -40 and flat == 0 and close(boost, 6) and -15 < cut < -12
    off, lowpass, highpass, half = out["filter"]
    assert off == {"type": "off"} and lowpass["type"] == "lowpass" and close(lowpass["hz"], 60, 1e-6)
    assert highpass["type"] == "highpass" and close(highpass["hz"], 8000, 1e-6)
    assert half["type"] == "lowpass" and 800 < half["hz"] < 1300
    assert close(out["rate"][0], 1.08) and close(out["rate"][1], 0.5)
    assert close(out["keyLock"][0], -12) and close(out["keyLock"][1], 0)
    assert out["time"] == ["1:05.2", "-0:03.5"]


def test_only_separated_songs_are_offered(out):
    assert out["playable"] == ["ok"]
    assert out["order"] == ["vocals", "drums", "bass", "other"]


def test_worklet_mixes_stems_and_moves_the_playhead(out):
    assert out["w_plain"] == [1100, 1101, 1102, 1103]
    assert out["w_gain"] == [232, 233]            # the constant stem is gone, the ramp continues
    assert out["w_rate"] == [234, 236, 238]
    assert out["w_loop"] == [500, 501, 502, 503, 500, 501, 502, 503, 500, 501]
    assert out["w_end"][:2] == [3996, 3997] and out["w_end"][-1] == 0
    assert out["w_endPlaying"] is False           # stops at the end of the song
    assert out["w_reportsHavePos"]


def test_library_offers_separated_songs_and_the_rest_live(out):
    items = {i["key"]: i for i in out["library"]}
    assert set(items) == {"s:Sep", "f:Songs/New.mp3", "f:Songs/Long.mp3"}   # the separated song once, folders not
    assert items["s:Sep"]["stems"] == "Sep" and items["s:Sep"]["live"] is False
    assert items["f:Songs/New.mp3"]["live"] is True and items["f:Songs/New.mp3"]["rel"] == "Songs/New.mp3"
    assert items["f:Songs/Long.mp3"]["tooLong"] is True


def test_the_next_window_is_the_nearest_missing_one_ahead(out):
    assert out["next"] == [2, 5, -1]          # from the playhead on, skipping one already asked for; all in: none


def test_worklet_plays_the_mix_until_the_stems_arrive(out):
    assert out["live_before"] == [1000, 1000]
    assert out["live_after"] == [700, 700]
    assert out["live_next"] == [1000, 1000]


def test_the_pc_library_songs_become_dj_items(out):
    (item,) = out["fromLibrary"]                  # a song with no Demucs stem (only a guitar split) is left out
    assert item["key"] == "s:Band - Song" and item["title"] == "Song" and item["artist"] == "Band"
    assert item["stems"] == "Band - Song" and item["live"] is False and item["stemList"] == ["drums", "guitar_solo", "vocals"]
