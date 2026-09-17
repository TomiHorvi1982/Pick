"""Song structure and chords for a separated track.

Runs under its own interpreter (~/.local/venvs/spotdl-analyze), because it
needs torch, librosa and the All-In-One checkpoints, and the server's venv has
none of them. Reads a folder of stems, prints one JSON object on stdout.

Two analyses, from two different angles:

**Sections** — All-In-One (Kim & Nam, ISMIR 2023), the `harmonix-all`
checkpoint, via the `all-in-one-infer` package. It is a transformer over four
stem spectrograms that predicts functional labels (intro / verse / chorus /
bridge / inst / solo / break / outro) along with boundaries. It wants exactly
bass, drums, other and vocals, so the six stems we have are mixed down to
those four first.

**Chords** — no pretrained chord model survives on a current Python: madmom's
chord package isn't in the `madmom-infer` fork, chord-extractor needs a Vamp
binary and autochord drags in TensorFlow. So this does the classical thing —
beat-synchronous chroma matched against chord templates and smoothed with
Viterbi — but with an advantage a plain mix doesn't have: the stems are
already separated, so the chroma is taken from the harmonic instruments alone,
with the drums (broadband noise across every pitch class) and the vocals
(portamento and vibrato that smear the chroma) left out. The bass stem votes
separately for the bass note, which is what makes an inversion visible.

Usage:  analyze.py <stems-dir> [--beats <beats.json>]
"""

import argparse
import contextlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# The hub writes symlinks by default; a plain copy is 10 MB and never depends
# on the filesystem underneath the cache.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

FFMPEG = "/opt/homebrew/bin/ffmpeg"
STEM_EXTS = (".mp3", ".wav", ".flac")

# What All-In-One expects, and which of our six stems feed each one.
SECTION_STEMS = {
    "bass": ("bass",),
    "drums": ("drums",),
    "vocals": ("vocals",),
    "other": ("other", "guitar", "piano"),
}
# Chords come from what actually plays them. Bass is excluded from the chroma
# (its octave dominates and pulls every chord towards its root) and judged
# separately.
HARMONIC_STEMS = ("other", "guitar", "piano")

SECTION_NAMES = {
    "intro": "Intro", "outro": "Outro", "break": "Break", "bridge": "Bridge",
    "inst": "Instrumental", "solo": "Solo", "verse": "Sloka",
    "chorus": "Refrén", "part": "Část",
}
SECTION_COLORS = {
    "intro": "#4a7fff", "verse": "#00c8a0", "chorus": "#9a4aff",
    "bridge": "#ff8a20", "break": "#2ab8e8", "inst": "#e8c840",
    "solo": "#ff4a90", "outro": "#00d4d4", "part": "#8391a5",
}
# start/end are the model's own sentinels for the edges of the track, not
# sections anyone would name.
SECTION_SENTINELS = {"start", "end"}
MIN_SECTION_SECONDS = 1.5

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _stem_path(stems_dir: Path, stem: str):
    for ext in STEM_EXTS:
        p = stems_dir / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


def _mix_to_wav(sources, out_path: Path, rate=44100):
    """Sum stems into one float WAV. One input is a straight convert."""
    cmd = [FFMPEG, "-y", "-nostdin", "-loglevel", "error"]
    for src in sources:
        cmd += ["-i", str(src)]
    if len(sources) > 1:
        chain = "".join(f"[{i}:a]" for i in range(len(sources)))
        cmd += ["-filter_complex",
                f"{chain}amix=inputs={len(sources)}:normalize=0:duration=longest"]
    cmd += ["-ac", "2", "-ar", str(rate), "-c:a", "pcm_f32le", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


# ----------------------------------------------------------------- sections

def _sections(stems_dir: Path, work: Path, beats_path):
    """All-In-One's functional segments, normalised to our own shape."""
    have = {}
    for target, sources in SECTION_STEMS.items():
        paths = [p for p in (_stem_path(stems_dir, s) for s in sources) if p]
        if not paths:
            return [], "chybí stopa %s" % target
        have[target] = paths

    demix = work / "demix"
    demix.mkdir(parents=True, exist_ok=True)
    for target, paths in have.items():
        _mix_to_wav(paths, demix / f"{target}.wav")

    # Everything the libraries print goes to stderr: stdout carries the result.
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from allin1_infer.helpers import run_inference
        from allin1_infer.models import load_pretrained_model
        from allin1_infer.spectrogram import extract_spectrograms

        spec_paths = extract_spectrograms([demix], work / "spec", multiprocess=False)
        model = load_pretrained_model(model_name="harmonix-all", device="cpu")
        with torch.no_grad():
            result = run_inference(
                path=Path("track.wav"), spec_path=spec_paths[0], model=model,
                device="cpu", include_activations=False, include_embeddings=False,
            )

    raw = getattr(result, "segments", None) or []
    out = []
    for seg in raw:
        start, end = float(seg.start), float(seg.end)
        label = str(seg.label)
        if end - start < MIN_SECTION_SECONDS:
            continue
        # An interior sentinel means the model found a real span it could not
        # name; "part" says that honestly instead of inventing a chorus.
        sentinel = label in SECTION_SENTINELS
        kind = "part" if sentinel else label
        if kind not in SECTION_NAMES:
            kind = "part"
        out.append({"start": round(start, 3), "end": round(end, 3),
                    "kind": kind, "_sentinel": sentinel})

    out = _absorb_edge_slivers(out)

    out = _merge_and_number(out)
    if beats_path:
        out = _snap_to_beats(out, beats_path)
    return out, None


def _absorb_edge_slivers(sections, limit=9.0):
    """Fold the model's start/end markers into the music they touch.

    The first and last predictions are sentinels for the edges of the track,
    not sections; left alone they show up as a two-second "Část" before the
    intro, which is a row in the lane that means nothing. The limit is generous
    because a fade-out sentinel can run several seconds — the shortest real
    section anyone names is still longer than this.
    """
    while len(sections) > 1 and sections[0].get("_sentinel") \
            and sections[0]["end"] - sections[0]["start"] < limit:
        sections[1]["start"] = sections[0]["start"]
        sections.pop(0)
    while len(sections) > 1 and sections[-1].get("_sentinel") \
            and sections[-1]["end"] - sections[-1]["start"] < limit:
        sections[-2]["end"] = sections[-1]["end"]
        sections.pop()
    for s in sections:
        s.pop("_sentinel", None)
    return sections


def _merge_and_number(sections):
    """Fuse touching sections of one kind, then number the repeats.

    Two adjacent "verse" spans are one verse the model split; and once they're
    fused, "Sloka 1 / Sloka 2" is far easier to talk about than four rows all
    called Sloka.
    """
    merged = []
    for s in sections:
        if merged and merged[-1]["kind"] == s["kind"] and s["start"] - merged[-1]["end"] < 0.6:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))

    counts = {}
    totals = {}
    for s in merged:
        totals[s["kind"]] = totals.get(s["kind"], 0) + 1
    for s in merged:
        name = SECTION_NAMES[s["kind"]]
        if totals[s["kind"]] > 1:
            counts[s["kind"]] = counts.get(s["kind"], 0) + 1
            name = f"{name} {counts[s['kind']]}"
        s["name"] = name
        s["color"] = SECTION_COLORS[s["kind"]]
    return merged


def _snap_to_beats(sections, beats_path: Path):
    """Pull each boundary onto the nearest bar line it is already close to.

    The model predicts on its own frame grid, so a boundary lands a tenth of a
    second off the downbeat it obviously means. Snapping only when the bar line
    is within a beat keeps a genuinely off-grid boundary where it was found.
    """
    try:
        grid = json.loads(beats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return sections
    bars = grid.get("downbeats") or grid.get("beats") or []
    if len(bars) < 4:
        return sections
    beat = 60.0 / float(grid.get("bpm") or 120.0)
    tol = beat * 1.2

    def snap(t):
        best = min(bars, key=lambda b: abs(b - t))
        return round(best, 3) if abs(best - t) <= tol else round(t, 3)

    for i, s in enumerate(sections):
        s["start"] = snap(s["start"])
        s["end"] = snap(s["end"])
        if i and sections[i - 1]["end"] != s["start"]:
            # A shared boundary must stay shared, or the lane shows a sliver.
            sections[i - 1]["end"] = s["start"]
    return [s for s in sections if s["end"] - s["start"] >= MIN_SECTION_SECONDS]


# ------------------------------------------------------------------- chords

# (suffix, semitones, prior). The priors are not guesses: they were fitted
# against two tracks whose harmony is known — "Never Gonna Give You Up", whose
# every chord is diatonic to A-flat major, and a drop-C# metal track, which is
# power chords almost throughout. A noisy chroma always fits a four-note
# template better than a triad, so the extensions have to be nudged down or
# every bar comes back as a maj7.
CHORD_QUALITIES = [
    ("",      [0, 4, 7],     1.00),   # major
    ("m",     [0, 3, 7],     1.00),   # minor
    ("7",     [0, 4, 7, 10],  0.90),
    ("maj7",  [0, 4, 7, 11],  0.86),
    ("m7",    [0, 3, 7, 10],  0.90),
    ("sus4",  [0, 5, 7],      0.90),
    ("sus2",  [0, 2, 7],      0.88),
    ("dim",   [0, 3, 6],      0.90),
    ("aug",   [0, 4, 8],      0.84),
    ("5",     [0, 7],         0.90),  # power chord: metal lives here
]
# How hard a pitch class outside the chord counts against it. Without this a
# maj7 template scores a plain triad as well as the triad's own template does,
# because it contains it; with it, the extra energy a maj7 expects and doesn't
# find is what breaks the tie. 0.30 was the value that kept triads on the pop
# track (10% power chords) without flattening the metal one (38%).
NON_CHORD_PENALTY = 0.30
# Peak sharpening on the chroma. Higher picks the loudest pitch classes out of
# the harmonic mush; too high and the third disappears and everything becomes
# a power chord.
CHROMA_SHARPEN = 2.0
# Viterbi's reward for holding the previous chord, on the same scale as the
# cosine scores.
CHORD_SELF_BONUS = 0.15


def _chord_templates():
    """(matrix, names, roots, qualities) for every root and quality."""
    import numpy as np
    templates, names, roots, quals = [], [], [], []
    for root in range(12):
        for suffix, intervals, prior in CHORD_QUALITIES:
            vec = np.full(12, -NON_CHORD_PENALTY, dtype=np.float32)
            for i in intervals:
                vec[(root + i) % 12] = 1.0
            vec /= np.linalg.norm(vec)
            templates.append(vec * prior)
            names.append(PITCHES[root] + suffix)
            roots.append(root)
            quals.append(suffix or "maj")
    return np.stack(templates), names, roots, quals


def _viterbi(scores, self_bonus=CHORD_SELF_BONUS):
    """Most likely chord path, favouring staying put.

    Frame-by-frame argmax flickers between relatives — C and Am share two of
    three notes — which reads as noise rather than a progression. One
    transition penalty settles it, and it is the standard fix.
    """
    import numpy as np
    n_frames, n_states = scores.shape
    back = np.zeros((n_frames, n_states), dtype=np.int32)
    cost = scores[0].copy()
    for t in range(1, n_frames):
        stay = cost + self_bonus
        best_idx = int(cost.argmax())
        best = float(cost[best_idx])
        back[t] = np.where(stay >= best, np.arange(n_states), best_idx)
        cost = np.maximum(stay, best) + scores[t]
    path = np.zeros(n_frames, dtype=np.int32)
    path[-1] = int(cost.argmax())
    for t in range(n_frames - 1, 0, -1):
        path[t - 1] = back[t][path[t]]
    return path


def _chords(stems_dir: Path, work: Path, beats):
    """Beat-synchronous chord estimates over the harmonic stems."""
    import numpy as np
    import librosa

    paths = [p for p in (_stem_path(stems_dir, s) for s in HARMONIC_STEMS) if p]
    if not paths:
        return [], "chybí harmonické stopy"

    sr = 22050
    mix = _mix_to_wav(paths, work / "harmonic.wav", rate=sr)
    y, sr = librosa.load(str(mix), sr=sr, mono=True)
    if y.size < sr:
        return [], "stopa je příliš krátká"

    # CQT chroma over the range chords are actually voiced in. What's left of a
    # guitar stem's transients is still percussive enough to blur a boundary,
    # so the harmonic part is taken first.
    y_harm = librosa.effects.harmonic(y, margin=3.0)
    chroma = librosa.feature.chroma_cqt(
        y=y_harm, sr=sr, hop_length=512, fmin=librosa.note_to_hz("C2"),
        n_octaves=5, bins_per_octave=36,
    )
    # Distortion and the CQT's own leakage leave every pitch class lit; what
    # rises above the frame's median is the part that means something.
    chroma = np.maximum(0.0, chroma - np.median(chroma, axis=0, keepdims=True))
    chroma = chroma ** CHROMA_SHARPEN

    bass_chroma = None
    bass_path = _stem_path(stems_dir, "bass")
    if bass_path is not None:
        yb, _ = librosa.load(str(_mix_to_wav([bass_path], work / "bass.wav", rate=sr)),
                             sr=sr, mono=True)
        bass_chroma = librosa.feature.chroma_cqt(
            y=yb, sr=sr, hop_length=512, fmin=librosa.note_to_hz("C1"),
            n_octaves=3, bins_per_octave=36,
        )
        bass_chroma = np.maximum(
            0.0, bass_chroma - np.median(bass_chroma, axis=0, keepdims=True))

    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr, hop_length=512)
    duration = float(len(y)) / sr

    # Chords change on beats, so one estimate per beat is both the right
    # resolution and a large noise reduction over per-frame.
    spans = _beat_spans(beats, duration)
    if len(spans) < 2:
        return [], "chybí beat grid"

    def pool(source):
        out = np.zeros((len(spans), 12), dtype=np.float32)
        for i, (t0, t1) in enumerate(spans):
            lo = int(np.searchsorted(frame_times, t0))
            hi = max(lo + 1, int(np.searchsorted(frame_times, t1)))
            window = source[:, lo:min(hi, source.shape[1])]
            if window.size:
                out[i] = np.median(window, axis=1)
        return out

    beat_chroma = pool(chroma)
    beat_bass = pool(bass_chroma) if bass_chroma is not None else None

    # How much pitched content there is at all. A beat of silence or pure
    # drums must not be handed a chord.
    energy = beat_chroma.sum(axis=1)
    active = energy > max(1e-4, float(np.median(energy)) * 0.22)

    norm = beat_chroma / np.maximum(np.linalg.norm(beat_chroma, axis=1, keepdims=True), 1e-6)
    templates, names, roots, quals = _chord_templates()
    scores = norm @ templates.T
    path = _viterbi(scores.astype(np.float32))

    out = []
    for i, (t0, t1) in enumerate(spans):
        entry = {"start": round(t0, 3), "end": round(t1, 3)}
        if not active[i]:
            entry["chord"] = None
        else:
            idx = int(path[i])
            entry.update({
                "chord": names[idx],
                "root": PITCHES[roots[idx]],
                "quality": quals[idx],
                "confidence": round(float(scores[i][idx]), 3),
                "_beat": i,
            })
        out.append(entry)

    merged = _merge_chords(out)
    if beat_bass is not None:
        _attach_bass(merged, beat_bass, spans)
    for entry in merged:
        entry.pop("_beat", None)
        entry.pop("_beats_idx", None)
    return merged, None


def _attach_bass(chords, beat_bass, spans):
    """The bass note of each chord, once the chord spans are settled.

    Deciding this per beat and then splitting on it produced a new span every
    other beat: a bass line walks, and a walking note is not a chord change.
    The note is only reported when most of the chord's beats agree on it and it
    isn't the root — an inversion is the case worth showing.
    """
    import numpy as np
    from collections import Counter

    starts = [s[0] for s in spans]
    for c in chords:
        if not c.get("chord"):
            continue
        lo = int(np.searchsorted(starts, c["start"] - 1e-6))
        hi = int(np.searchsorted(starts, c["end"] - 1e-6))
        votes = []
        for i in range(lo, max(lo + 1, min(hi, len(spans)))):
            row = beat_bass[i]
            top = int(np.argmax(row))
            # A flat frame has no bass note in it, only noise.
            if row[top] > 0.25 and row[top] > 1.6 * float(np.median(row)):
                votes.append(top)
        if not votes:
            continue
        note, count = Counter(votes).most_common(1)[0]
        if count / len(votes) < 0.6:
            continue
        if PITCHES[note] != c["root"]:
            c["bass"] = PITCHES[note]


def _beat_spans(beats, duration):
    """[(from, to)] for each beat, ending at the track's end."""
    spans = []
    for i, t in enumerate(beats):
        end = beats[i + 1] if i + 1 < len(beats) else duration
        if end > t:
            spans.append((float(t), float(min(end, duration))))
    return spans


def _merge_chords(chords, min_seconds=0.45):
    """One row per chord, not per beat.

    A chord held for a bar is one chord. Anything shorter than a beat or two
    after merging was a passing estimate, not a chord change, so it is folded
    into whichever neighbour it agrees with.
    """
    merged = []
    for c in chords:
        if merged and merged[-1].get("chord") == c.get("chord"):
            merged[-1]["end"] = c["end"]
            merged[-1]["beats"] = merged[-1].get("beats", 1) + 1
        else:
            entry = dict(c)
            entry["beats"] = 1
            merged.append(entry)

    cleaned = []
    for c in merged:
        if c["end"] - c["start"] < min_seconds and cleaned and c.get("chord") is not None:
            cleaned[-1]["end"] = c["end"]
            continue
        cleaned.append(c)
    return cleaned


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stems_dir", type=Path)
    ap.add_argument("--beats", type=Path)
    ap.add_argument("--skip-sections", action="store_true")
    ap.add_argument("--skip-chords", action="store_true")
    args = ap.parse_args()

    beats = []
    if args.beats and args.beats.is_file():
        try:
            beats = json.loads(args.beats.read_text(encoding="utf-8")).get("beats") or []
        except (OSError, json.JSONDecodeError):
            beats = []

    out = {"sections": [], "chords": [], "warnings": []}
    with tempfile.TemporaryDirectory(prefix="analyze-") as tmp:
        work = Path(tmp)
        if not args.skip_sections:
            try:
                sections, why = _sections(args.stems_dir, work, args.beats)
                out["sections"] = sections
                if why:
                    out["warnings"].append(f"sekce: {why}")
            except Exception as exc:                     # noqa: BLE001
                out["warnings"].append(f"sekce selhaly: {type(exc).__name__}: {exc}")
        if not args.skip_chords:
            try:
                chords, why = _chords(args.stems_dir, work, beats)
                out["chords"] = chords
                if why:
                    out["warnings"].append(f"akordy: {why}")
            except Exception as exc:                     # noqa: BLE001
                out["warnings"].append(f"akordy selhaly: {type(exc).__name__}: {exc}")

    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
