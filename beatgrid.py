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


def main(path):
    from beat_this.inference import Audio2Beats

    signal, sr = _load_mono(path)

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

    return {
        "beats": beats,
        "downbeats": downbeats,
        "bpm": round(bpm, 2),
        "beats_per_bar": beats_per_bar,
        "detector": "beat_this",
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
