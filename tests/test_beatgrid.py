"""Beat grid post-processing.

The tracker's raw output is not directly usable: it switches into double time
through busy sections, skips drum-free ones, and starts at the first drum hit
rather than at the start of the music. These are the steps that turn it into a
grid that covers the whole track at one tempo.
"""

import importlib.util
import statistics
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def bg():
    spec = importlib.util.spec_from_file_location(
        "beatgrid", Path(__file__).resolve().parents[1] / "beatgrid.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grid(start, count, interval):
    return [round(start + i * interval, 4) for i in range(count)]


# ------------------------------------------------------- tempo estimate

def test_dominant_interval_ignores_double_time_stretches(bg):
    """Half the track detected at double time must not halve the tempo."""
    steady = grid(0.0, 40, 0.5)
    doubled = grid(20.0, 20, 0.25)
    beats = sorted(set(steady + doubled))

    assert bg._dominant_interval(beats) == pytest.approx(0.5, abs=0.02)


def test_dominant_interval_none_for_a_single_beat(bg):
    assert bg._dominant_interval([1.0]) is None


# --------------------------------------------------- subdivision removal

def test_drop_subdivisions_collapses_a_run(bg):
    """Measured against the beat that was kept, so a run collapses instead of
    alternating kept/dropped."""
    beats = grid(0.0, 4, 0.5) + [1.75, 2.0, 2.25, 2.5]

    kept, dropped = bg._drop_subdivisions(sorted(set(beats)), 0.5)

    assert dropped > 0
    gaps = [b - a for a, b in zip(kept, kept[1:])]
    assert min(gaps) >= 0.5 * 0.72


def test_drop_subdivisions_leaves_a_clean_grid_alone(bg):
    beats = grid(0.0, 20, 0.5)

    kept, dropped = bg._drop_subdivisions(beats, 0.5)

    assert dropped == 0
    assert kept == beats


# ------------------------------------------------------------ tempo fit

def test_score_interval_finds_the_true_period(bg):
    """The median of the detected gaps is not the tempo: beat_this quantises
    its output to its own frame grid, so the gaps are multiples of 20ms and
    their median is biased. On a real track the median said 115.38 BPM and
    matched 100 of 399 beats; the scan found 113.68 and matched all 399."""
    true_interval = 0.5278
    beats = [round(i * true_interval, 4) for i in range(80)]

    exact, _ = bg._score_interval(beats, true_interval)
    off, _ = bg._score_interval(beats, true_interval * 1.015)

    assert exact == len(beats)
    assert off < exact


def test_best_grid_recovers_a_period_the_median_would_miss(bg):
    """Quantise a clean grid to 20ms frames — what the tracker actually
    emits — and the scan must still recover the underlying period."""
    true_interval = 0.5278
    beats = [round(round(i * true_interval / 0.02) * 0.02, 4) for i in range(120)]

    interval, _phase, hits = bg._best_grid(beats, 0.52)

    assert interval == pytest.approx(true_interval, abs=0.004)
    assert hits > len(beats) * 0.9


def test_best_grid_ignores_a_misTracked_stretch(bg):
    """Twenty beats at 1.5x the real period — the failure that put every bar
    after them out of phase — must not move the fit."""
    interval = 0.54
    good = [round(i * interval, 4) for i in range(60)]
    bad = [round(good[-1] + i * interval * 1.5, 4) for i in range(1, 21)]
    tail = [round(bad[-1] + i * interval, 4) for i in range(1, 61)]

    fitted, _phase, _ = bg._best_grid(good + bad + tail, interval)

    assert fitted == pytest.approx(interval, abs=0.01)


def test_best_grid_handles_too_few_beats(bg):
    interval, phase, hits = bg._best_grid([1.0, 2.0], 0.5)
    assert interval == 0.5
    assert hits == 0


# ----------------------------------------------------- grid construction

def test_build_regular_grid_covers_the_whole_track(bg):
    """Detection starts at the first drum hit, so an intro has no beats of its
    own; the grid still has to reach the top and the end of the file."""
    segments = [(25.0, 200.0, 0.5, 25.0)]

    grid = bg._build_regular_grid(segments, duration=240.0)

    assert grid[0] <= 0.5
    assert grid[-1] > 239.0
    assert all(t > 0 for t in grid)
    gaps = [round(b - a, 4) for a, b in zip(grid, grid[1:])]
    assert set(gaps) == {0.5}          # perfectly regular


def test_build_regular_grid_follows_a_tempo_change(bg):
    segments = [(0.0, 60.0, 0.5, 0.0), (62.0, 120.0, 0.4, 62.0)]

    grid = bg._build_regular_grid(segments, duration=120.0)

    early = [b - a for a, b in zip(grid, grid[1:]) if b < 55]
    late = [b - a for a, b in zip(grid, grid[1:]) if a > 70]
    assert statistics.median(early) == pytest.approx(0.5, abs=0.01)
    assert statistics.median(late) == pytest.approx(0.4, abs=0.01)
    # No doubled beat at the seam.
    assert min(b - a for a, b in zip(grid, grid[1:])) > 0.19


def test_merge_segments_joins_identical_tempos(bg):
    """Splitting is driven by fit quality, so one badly-tracked bar ends a
    segment. Unmerged, a track here came out as eight segments spanning
    0.18 BPM — a tempo "curve" drawn out of nothing."""
    beats = [round(i * 0.5, 4) for i in range(200)]
    segments = [(0.0, 40.0, 0.5, 0.0), (41.0, 99.5, 0.5008, 41.0)]

    merged = bg._merge_segments(segments, beats)

    assert len(merged) == 1


def test_merge_segments_keeps_a_real_change(bg):
    beats = [round(i * 0.5, 4) for i in range(200)]
    segments = [(0.0, 40.0, 0.5, 0.0), (41.0, 99.5, 0.42, 41.0)]

    assert len(bg._merge_segments(segments, beats)) == 2


# -------------------------------------------------------------- bar lines

def test_rebuild_downbeats_is_evenly_spaced(bg):
    beats = grid(0.0, 32, 0.5)
    detected = [0.0, 2.0, 4.0, 6.0]

    bars = bg._rebuild_downbeats(beats, detected, 4)

    gaps = [round(b - a, 3) for a, b in zip(bars, bars[1:])]
    assert set(gaps) == {2.0}


def test_rebuild_downbeats_picks_the_phase_the_tracker_heard(bg):
    beats = grid(0.0, 32, 0.5)
    detected = [0.5, 2.5, 4.5]          # bar starts on the second beat

    bars = bg._rebuild_downbeats(beats, detected, 4)

    assert bars[0] == pytest.approx(0.5)


def test_rebuild_downbeats_without_any_detected(bg):
    beats = grid(0.0, 16, 0.5)

    bars = bg._rebuild_downbeats(beats, [], 4)

    assert bars[0] == 0.0
    assert len(bars) == 4


# --------------------------------------------------------------- tempo curve

def test_tempo_curve_follows_the_beats_as_heard(bg):
    """The grid that ships to the player is regular by construction, so a
    curve drawn from it is a flat line. This one comes from the raw
    detections, where a drummer speeding up is actually visible."""
    beats = [i * 0.5 for i in range(20)]                    # 120 BPM
    beats += [beats[-1] + 0.4 * (i + 1) for i in range(20)]  # then 150

    curve = bg._tempo_curve(beats, duration=beats[-1] + 1)

    assert curve, "a curve was expected"
    assert all(len(p) == 2 for p in curve)
    assert curve[0][1] == pytest.approx(120, abs=1)
    assert curve[-1][1] == pytest.approx(150, abs=1)
    assert all(0 <= t <= beats[-1] + 1 for t, _ in curve)


def test_one_missed_beat_does_not_dent_the_curve(bg):
    """A dropped detection doubles one gap. A running mean smears that spike
    across the window; the median ignores it, which is why it's a median."""
    beats = [i * 0.5 for i in range(30)]
    del beats[15]

    curve = bg._tempo_curve(beats, duration=20)
    bpms = [b for _t, b in curve]

    assert max(bpms) == pytest.approx(120, abs=2)
    assert min(bpms) == pytest.approx(120, abs=2)


def test_tempo_curve_is_thinned_for_a_long_track(bg):
    beats = [i * 0.25 for i in range(6000)]
    curve = bg._tempo_curve(beats, duration=1500, max_points=400)
    # Two points per bucket keep a spike alive through decimation.
    assert 400 <= len(curve) <= 800
    assert curve == sorted(curve)


def test_too_few_beats_produce_no_curve(bg):
    assert bg._tempo_curve([0.0, 0.5], duration=10) == []
