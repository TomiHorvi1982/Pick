"""Beat grid post-processing.

The tracker's raw output is not directly usable: it switches into double time
through busy sections, skips drum-free ones, and starts at the first drum hit
rather than at the start of the music. These are the steps that turn it into a
grid that covers the whole track at one tempo.
"""

import importlib.util
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


# --------------------------------------------------------- gap filling

def test_fill_interior_gaps_bridges_a_drumless_section(bg):
    beats = grid(0.0, 10, 0.5) + grid(8.5, 10, 0.5)   # 4s hole

    filled_beats, added = bg._fill_interior_gaps(beats, 0.5)

    assert len(added) == 7
    gaps = [round(b - a, 3) for a, b in zip(filled_beats, filled_beats[1:])]
    assert max(gaps) == pytest.approx(0.5, abs=0.01)


def test_fill_interior_gaps_refuses_a_non_integer_gap(bg):
    """A gap of 1.5 beats is not two beats — splitting it manufactures beats
    at the wrong tempo, which is worse than a short hole."""
    beats = [0.0, 0.5, 1.0, 1.75, 2.25]

    _, added = bg._fill_interior_gaps(beats, 0.5)

    assert added == []


def test_fill_interior_gaps_ignores_an_enormous_gap(bg):
    beats = [0.0, 0.5, 1.0, 400.0]

    _, added = bg._fill_interior_gaps(beats, 0.5, max_gap_beats=64)

    assert added == []


# --------------------------------------------------------- edge extension

def test_extend_to_edges_fills_the_intro_and_outro(bg):
    beats = grid(25.0, 20, 0.5)

    full, head, tail = bg._extend_to_edges(beats, duration=60.0, interval=0.5)

    assert head and tail
    assert full[0] <= 0.5                     # reaches the top of the track
    assert full[-1] > 59.0                    # and the end
    assert all(t > 0 for t in full)           # never negative
    assert full == sorted(full)


def test_extend_to_edges_is_a_noop_when_already_covered(bg):
    beats = grid(0.2, 20, 0.5)

    full, head, tail = bg._extend_to_edges(beats, duration=10.2, interval=0.5)

    assert head == []
    assert full[-1] <= 10.2


def test_dominant_interval_survives_a_closing_fill(bg):
    """The last few beats are often a fill. Extrapolating the outro from those
    produced a grid at twice the real tempo, so the period has to come from
    the track as a whole, not from either edge."""
    beats = grid(0.0, 30, 0.5) + [15.2, 15.4, 15.6, 15.8, 16.0]

    assert bg._dominant_interval(sorted(set(beats))) == pytest.approx(0.5, abs=0.05)


def test_extend_to_edges_uses_the_period_it_is_given(bg):
    """Edge extrapolation takes the track's period as an argument precisely so
    a local fill can't set the tempo for a whole outro."""
    beats = grid(10.0, 10, 0.5)

    full, head, tail = bg._extend_to_edges(beats, duration=30.0, interval=0.5)

    head_gaps = [round(b - a, 3) for a, b in zip(full, full[1:])]
    assert set(head_gaps) == {0.5}


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
