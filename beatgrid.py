#!/usr/bin/env python3
"""Beat and downbeat grid for one audio file, printed as JSON.

Runs under Demucs' own interpreter (the one with torch), not the server's —
see server._demucs_interpreter. The server invokes it as a subprocess and
caches the result next to the stems.

Why beat_this and not librosa: librosa's tempo prior is centred on 120 BPM, so
fast material resolves to half time — a 180 BPM song clicks at 90, and no
confidence metric catches it, because a half-time grid still lands a real drum
hit on every beat. beat_this is a transformer tracker (MIT-licensed weights)
that also returns *downbeats*, which is what lets the grid draw real bar lines
instead of counting a fixed four from the top of the track.

Usage:  beatgrid.py <audio-file>
Prints: {"beats": [...], "downbeats": [...], "bpm": float, "detector": str}
        or {"error": "..."} with a non-zero exit code.
"""

import json
import statistics
import sys

# A grid this short is noise, not a grid; clicking along to it is worse than
# offering no click at all.
MIN_BEATS = 8
MIN_BPM = 30.0
MAX_BPM = 300.0


def _bpm_from(beats):
    """Median-interval BPM. The median ignores the occasional doubled or
    dropped beat that would drag a mean off by several BPM.
    """
    if len(beats) < 2:
        return None
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    if not gaps:
        return None
    median = statistics.median(gaps)
    if median <= 0:
        return None
    bpm = 60.0 / median
    return bpm if MIN_BPM <= bpm <= MAX_BPM else None


def _load_mono(path, sr=22050):
    """Decode any audio file to a mono float32 array via ffmpeg.

    beat_this's own loader wants soundfile or madmom, neither of which is in
    this interpreter — and ffmpeg is already a hard dependency of the app, so
    it also gets us mp3/m4a/flac for free rather than PCM WAV only.
    """
    import shutil
    import subprocess

    import numpy as np

    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path,
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[:200]
                           or "ffmpeg produced no audio")
    return np.frombuffer(proc.stdout, dtype="<f4").copy(), sr


def _global_interval(beats):
    """Median beat interval over the whole grid."""
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    return statistics.median(gaps) if gaps else None


def _dominant_interval(beats):
    """The one beat period the track is actually at.

    beat_this switches into double time through busy sections, so the raw gap
    distribution is bimodal — on one metal track here, 312 gaps near 0.52 s
    against 192 near 0.27 s. The median lands on the dominant mode, and a
    second pass over only the gaps close to it removes the rest, which is what
    makes the number stable enough to extrapolate a whole intro from.
    """
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    if not gaps:
        return None
    median = statistics.median(gaps)
    near = [g for g in gaps if 0.75 * median <= g <= 1.33 * median]
    return statistics.median(near) if near else median


def _drop_subdivisions(beats, interval, floor=0.72):
    """Remove the extra beats a double-time stretch contributed.

    Left in, they make the lane grid twice as dense in exactly the loudest
    sections and put the click on eighth notes there. Measured against the
    beat that was *kept*, so a run of subdivisions collapses rather than
    alternating.
    """
    if not beats or not interval:
        return beats, 0
    kept = [beats[0]]
    dropped = 0
    for t in beats[1:]:
        if t - kept[-1] < interval * floor:
            dropped += 1
            continue
        kept.append(t)
    return kept, dropped


def _fill_interior_gaps(beats, interval, max_gap_beats=64):
    """Insert beats across stretches the tracker skipped.

    A drum-free bridge leaves a hole in the grid and the click simply stops for
    its duration, which reads as a bug rather than as musical silence. The
    track's own period is used rather than a local estimate, because a local
    estimate taken *at* a hole is measured from the very beats that are
    missing.
    """
    if len(beats) < 2 or not interval or interval <= 0:
        return beats, []

    out = [beats[0]]
    filled = []
    for prev, nxt in zip(beats, beats[1:]):
        gap = nxt - prev
        steps = int(round(gap / interval))
        # A gap of 1.5 beats is not two beats. Splitting it anyway was
        # manufacturing beats at 0.41 s inside a 0.54 s track; leaving a short
        # hole is better than inventing a beat that isn't there.
        if 2 <= steps <= max_gap_beats and abs(gap / interval - steps) < 0.18:
            step = gap / steps       # spread evenly so the far edge stays put
            for k in range(1, steps):
                t = round(prev + k * step, 4)
                out.append(t)
                filled.append(t)
        out.append(nxt)
    return out, filled


def _extend_to_edges(beats, duration, interval):
    """Extrapolate the grid back to 0 and forward to the end of the track.

    Detection starts at the first drum hit, so an intro — often the first
    20-30 seconds — has no grid at all, and bar 1 lands wherever the drums
    came in rather than at the start of the music.
    """
    head, tail = [], []
    if not interval or interval <= 0:
        return beats, head, tail

    t = beats[0] - interval
    while t > 0.0005:                 # a beat at t<=0 has nowhere to be drawn
        head.append(round(t, 4))
        t -= interval
    head.reverse()

    if duration:
        t = beats[-1] + interval
        while t < duration:
            tail.append(round(t, 4))
            t += interval

    return head + beats + tail, head, tail


def _rebuild_downbeats(all_beats, detected, beats_per_bar):
    """Regular bar lines, phase-fitted to the downbeats that were detected.

    Keeping the raw downbeats sounds more faithful but isn't usable: on real
    material they arrive irregularly enough that consecutive "bars" differ by
    a whole beat, and a ruler numbered from them is wrong more often than it
    is right. Instead every beats_per_bar-th beat becomes a bar line, at
    whichever of the possible offsets agrees with the detected downbeats most
    often — so the bars are even *and* start where the tracker heard them.
    """
    if not all_beats:
        return []
    if not detected or beats_per_bar < 2:
        return all_beats[::max(1, beats_per_bar)]

    detected_set = set(detected)
    best_phase, best_score = 0, -1
    for phase in range(beats_per_bar):
        score = sum(1 for i in range(phase, len(all_beats), beats_per_bar)
                    if all_beats[i] in detected_set)
        if score > best_score:
            best_phase, best_score = phase, score
    return [all_beats[i] for i in range(best_phase, len(all_beats), beats_per_bar)]


def main(path):
    from beat_this.inference import Audio2Beats

    signal, sr = _load_mono(path)
    duration = len(signal) / float(sr) if sr else 0.0

    # dbn=False keeps this dependency-free: the DBN post-processor needs a git
    # install of madmom with an undeclared Cython build step.
    model = Audio2Beats(device="cpu", dbn=False)
    beats, downbeats = model(signal, sr)

    beats = [round(float(t), 4) for t in beats]
    downbeats = [round(float(t), 4) for t in downbeats]

    if len(beats) < MIN_BEATS:
        return {"error": f"only {len(beats)} beats detected"}

    bpm = _bpm_from(beats)
    if bpm is None:
        return {"error": "tempo outside a plausible range"}

    # Beats per bar, from the ratio of the median bar to the median beat.
    # Counting beats *between* downbeats sounds equivalent but isn't: a single
    # missed or doubled downbeat shifts whole counts, and the mode of those
    # counts then lands on 2 for plain 4/4 material. A ratio of medians is
    # unmoved by the same errors.
    beats_per_bar = 4
    if len(downbeats) >= 3:
        bar_gaps = [b - a for a, b in zip(downbeats, downbeats[1:]) if b > a]
        beat_gap = 60.0 / bpm
        if bar_gaps and beat_gap > 0:
            ratio = statistics.median(bar_gaps) / beat_gap
            # Only trust the meters that actually occur; anything else is a
            # tracking artefact and 4/4 is the safer default.
            candidates = [3, 4, 5, 6, 7]
            nearest = min(candidates, key=lambda n: abs(n - ratio))
            if abs(nearest - ratio) < 0.35:
                beats_per_bar = nearest

    # Everything above this point is what the tracker actually heard; keep its
    # bounds so the UI can draw the extrapolated bars more faintly.
    detected_from, detected_to = beats[0], beats[-1]

    # One stable period first: collapse double-time stretches onto it, then
    # fill and extend using that single number, so the finished grid is
    # regular instead of inheriting the tracker's mode switches.
    interval = _dominant_interval(beats) or (60.0 / bpm)
    beats, dropped = _drop_subdivisions(beats, interval)
    downbeats = [t for t in downbeats if t in set(beats)]
    beats, filled = _fill_interior_gaps(beats, interval)
    beats, head, tail = _extend_to_edges(beats, duration, interval)
    downbeats = _rebuild_downbeats(beats, downbeats, beats_per_bar)
    bpm = 60.0 / interval

    return {
        "beats": beats,
        "downbeats": downbeats,
        "bpm": round(bpm, 2),
        "beats_per_bar": beats_per_bar,
        "detector": "beat_this",
        "duration": round(duration, 3),
        # The span the tracker was confident about; outside it the grid is
        # extrapolated from the tempo at the nearest edge.
        "detected_from": detected_from,
        "detected_to": detected_to,
        "extrapolated": {
            "head": len(head), "tail": len(tail),
            "filled": len(filled), "dropped": dropped,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: beatgrid.py <audio-file>"}))
        sys.exit(2)
    try:
        result = main(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 — the server only needs the message
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
    print(json.dumps(result))
    sys.exit(1 if "error" in result else 0)
