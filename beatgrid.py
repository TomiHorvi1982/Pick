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
import math
import os
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

    ffmpeg = (os.environ.get("PICK_FFMPEG")
              or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg")
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


def _score_interval(beats, interval, tol_frac=0.12):
    """How many beats land on a grid of this period, and at what phase.

    The phase is the circular mean of the beat positions folded into one
    period — the offset that best centres them — so no search over phase is
    needed. A beat counts as a hit when it is within tol_frac of a period of
    the nearest grid line.
    """
    if interval <= 0:
        return 0, 0.0
    two_pi = 2.0 * math.pi
    sx = sy = 0.0
    for t in beats:
        ang = two_pi * (t / interval)
        sx += math.cos(ang)
        sy += math.sin(ang)
    phase = (math.atan2(sy, sx) / two_pi) * interval

    tol = interval * tol_frac
    half = interval * 0.5
    hits = 0
    for t in beats:
        off = ((t - phase) % interval + interval * 1.5) % interval - half
        if abs(off) <= tol:
            hits += 1
    return hits, phase


def _best_grid(beats, seed_interval, span=0.22, steps=2400):
    """Tempo and phase of the grid that best explains these beats.

    Fitting by regression on beat index looks equivalent and is not: across a
    gap the assigned indices come out wrong and the fitted slope follows them.
    Worse, the median of the detected gaps isn't the tempo either — beat_this
    quantises its output to its own frame grid, so the gaps are multiples of
    20 ms and their median is biased. On one track the median said 115.38 BPM
    and matched 100 of 399 beats, while the scan found 113.68 and matched all
    399. So: scan the period and score by how many beats actually land on it.
    """
    if len(beats) < 4 or seed_interval <= 0:
        return seed_interval, beats[0] if beats else 0.0, 0

    lo = seed_interval * (1 - span)
    hi = seed_interval * (1 + span)
    best = (0, seed_interval, beats[0])
    for i in range(steps + 1):
        iv = lo + (hi - lo) * i / steps
        hits, phase = _score_interval(beats, iv)
        if hits > best[0]:
            best = (hits, iv, phase)
    return best[1], best[2], best[0]


def _inlier_mask(beats, interval, phase, tol_frac=0.12):
    tol = interval * tol_frac
    half = interval * 0.5
    out = []
    for t in beats:
        off = ((t - phase) % interval + interval * 1.5) % interval - half
        out.append(abs(off) <= tol)
    return out


def _tempo_segments(beats, interval, min_beats=40, max_segments=8):
    """Split the track where one regular grid stops explaining the beats.

    A tempo change shows up as a stretch the fitted grid cannot cover. So does
    a tracking artefact — the detector following a 1.5x pulse for twenty bars —
    so a candidate whose period is a simple ratio of the track's own is skipped
    and the original grid carries through it. Real tempo changes are rarely
    exactly 1.5x.
    """
    segments = []
    remaining = list(beats)
    while remaining and len(segments) < max_segments:
        a, b, _hits = _best_grid(remaining, interval)
        mask = _inlier_mask(remaining, a, b)

        window = 16
        cut = len(remaining)
        for i in range(len(remaining) - window):
            if sum(mask[i:i + window]) / window < 0.45:
                cut = i
                break

        if cut >= len(remaining) - min_beats:
            segments.append((remaining[0], remaining[-1], a, b))
            break

        head = remaining[:cut]
        if len(head) >= min_beats:
            a_h, b_h, _ = _best_grid(head, interval)
            segments.append((head[0], head[-1], a_h, b_h))

        # Always consume something, or the loop can sit on cut == 0 forever.
        remaining = remaining[max(cut, 8):]

        guard = 0
        while len(remaining) > min_beats and guard < 40:
            guard += 1
            a_t, _b_t, _ = _best_grid(remaining[:min_beats], interval)
            ratio = a_t / interval
            if not any(abs(ratio - r) < 0.06 for r in (0.5, 0.667, 0.75, 1.333, 1.5, 2.0)):
                break
            remaining = remaining[8:]

    if not segments:
        a, b, _ = _best_grid(beats, interval)
        segments = [(beats[0], beats[-1], a, b)]
    return _merge_segments(segments, beats)


def _merge_segments(segments, beats, tol=0.015):
    """Join neighbours that are at the same tempo.

    The split is driven by how well one grid explains the beats, and a single
    badly-tracked bar is enough to end a segment. Left alone, one track here
    came out as eight segments spanning 0.18 BPM — which would draw a tempo
    "curve" out of nothing. Neighbours within 1.5% are the same tempo, and the
    merged span is refitted over all of its beats.
    """
    if len(segments) < 2:
        return segments

    merged = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if abs(seg[2] - prev[2]) / prev[2] < tol:
            span = [t for t in beats if prev[0] <= t <= seg[1]]
            if len(span) >= 4:
                a, b, _ = _best_grid(span, prev[2], span=0.04, steps=600)
            else:
                a, b = prev[2], prev[3]
            merged[-1] = (prev[0], seg[1], a, b)
        else:
            merged.append(seg)
    return merged


def _build_regular_grid(segments, duration):
    """Emit a regular grid covering the whole track from the fitted segments.

    Segment boundaries sit midway between the last beat of one and the first of
    the next, and each segment is generated from its own period and phase, so a
    real tempo change is followed without a gap or a doubled beat at the seam.
    """
    bounds = []
    for i, (t_from, t_to, a, b) in enumerate(segments):
        lo = 0.0 if i == 0 else (segments[i - 1][1] + t_from) / 2.0
        hi = duration if i == len(segments) - 1 else (t_to + segments[i + 1][0]) / 2.0
        bounds.append((lo, hi, a, b))

    grid = []
    for lo, hi, a, b in bounds:
        if a <= 0:
            continue
        k = math.ceil((lo - b) / a)
        t = b + k * a
        while t < hi:
            if t > 0.0005:
                grid.append(round(t, 4))
            t += a
    grid = sorted(set(grid))

    smallest = min((s[2] for s in bounds), default=0.5)
    cleaned = []
    for t in grid:
        if cleaned and t - cleaned[-1] < smallest * 0.5:
            continue   # never two beats within half a period at a seam
        cleaned.append(t)
    return cleaned


def _tempo_curve(beats, duration, smooth=5, max_points=900):
    """Instantaneous tempo along the track, from the beats as they were heard.

    The grid that ships to the player is regular by construction — one fitted
    period per segment — so a curve drawn from it is a flat line and says
    nothing. This is drawn from the raw detections instead, where a drummer
    pushing into a chorus or dragging through a breakdown is actually visible.

    A running median, not a mean: a single missed beat doubles one gap, and a
    mean smears that spike across the whole window while a median ignores it.
    """
    if len(beats) < 4:
        return []

    raw = []
    for i in range(len(beats) - 1):
        gap = beats[i + 1] - beats[i]
        if gap <= 0.05:
            continue
        raw.append((beats[i], 60.0 / gap))
    if len(raw) < 4:
        return []

    half = max(1, smooth // 2)
    points = []
    for i, (t, _bpm) in enumerate(raw):
        lo, hi = max(0, i - half), min(len(raw), i + half + 1)
        window = sorted(v for _t, v in raw[lo:hi])
        points.append((t, statistics.median(window)))

    # A five-minute track at 200 BPM is a thousand beats; the lane is a few
    # hundred pixels wide. Thin by keeping the extremes of each bucket, so a
    # brief spike survives decimation instead of being averaged away.
    if len(points) > max_points:
        step = len(points) / max_points
        thinned = []
        for k in range(max_points):
            lo = int(k * step)
            hi = max(lo + 1, int((k + 1) * step))
            bucket = points[lo:hi]
            lowest = min(bucket, key=lambda p: p[1])
            highest = max(bucket, key=lambda p: p[1])
            for p in sorted({lowest, highest}, key=lambda p: p[0]):
                thinned.append(p)
        points = thinned

    return [[round(t, 3), round(v, 2)] for t, v in points if 0 <= t <= duration]


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

    # The detected times are evidence for a grid, not the grid itself. Keeping
    # them verbatim inherits every tracking error: on one track here the
    # detector followed a dotted pulse for twenty bars, and because those beats
    # sit *between* the real ones, every bar after them was out of phase.
    interval = _dominant_interval(beats) or (60.0 / bpm)
    detected_beats, dropped = _drop_subdivisions(beats, interval)

    segments = _tempo_segments(detected_beats, interval)
    grid = _build_regular_grid(segments, duration)
    if len(grid) < MIN_BEATS:
        return {"error": "grid fit produced too few beats"}

    downbeats = _rebuild_downbeats(grid, downbeats, beats_per_bar)
    beats = grid
    bpm = 60.0 / statistics.median([s[2] for s in segments])

    tempo_segments = [
        {"from": round(max(0.0, lo), 3), "to": round(hi, 3), "bpm": round(60.0 / a, 2)}
        for lo, hi, a, _b in (
            (0.0 if i == 0 else (segments[i - 1][1] + seg[0]) / 2.0,
             duration if i == len(segments) - 1 else (seg[1] + segments[i + 1][0]) / 2.0,
             seg[2], seg[3])
            for i, seg in enumerate(segments)
        )
    ]

    return {
        "beats": beats,
        "tempo_curve": _tempo_curve(detected_beats, duration),
        "downbeats": downbeats,
        "bpm": round(bpm, 2),
        "beats_per_bar": beats_per_bar,
        "detector": "beat_this",
        "duration": round(duration, 3),
        # The span the tracker was confident about; outside it the grid is
        # extrapolated from the tempo at the nearest edge.
        "detected_from": detected_from,
        "detected_to": detected_to,
        "tempo_segments": tempo_segments,
        "extrapolated": {"dropped": dropped, "segments": len(segments)},
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
