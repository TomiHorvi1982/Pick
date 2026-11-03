"""EQ matching from the NAM-EQ-Match repo, adapted for Pick's tone_worker.

Provides two things that Pick's own `ear.ir(ear.colour(...))` does not:

  match_ir(candidate, target, sr)
      A minimum-phase FIR matched to the LTAS gap between the NAM core output
      (after print-ir.wav is applied) and the original recording. Capped at
      ±18 dB, tapered at the frequency extremes, 1/6-octave smoothed.
      Replaces (or sits next to) print-ir.wav in the output folder.

  gentle_ir(candidate, target, sr)
      The same, but capped at ±9 dB with broader 1/3-octave smoothing —
      designed to be used after the NAM plugin's own Bass/Mid/Treble EQ
      handles the coarse correction. This is the "hybrid" approach.

  tone_stack_knobs(candidate_ltas_db, target_ltas_db, grid, sr)
      Suggested Bass/Mid/Treble knob values (0–10) for the NAM plugin's EQ,
      fitting the same biquad filters the plugin itself uses (150 Hz shelf,
      425 Hz peak, 1.8 kHz shelf). Pure frequency-domain, no reamping needed.

All three are standalone numpy/scipy functions — no torch, no Gradio, no
dependency on the NAM-EQ-Match repo being installed. The relevant code was
inlined here so Pick's own venv (numpy + scipy + soundfile) is enough.

Usage in tone_worker.py — see the comment block at the bottom of this file.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.fft import dct
from scipy.signal import welch, fftconvolve

EPS = 1e-12

# ---------------------------------------------------------------------------
# Spectral helpers (inlined from tonematch/features.py + tonematch/match_eq.py)
# ---------------------------------------------------------------------------


def _ltas(x: np.ndarray, sr: int, n_fft: int = 8192) -> tuple[np.ndarray, np.ndarray]:
    """Long-term average power spectrum via Welch. Returns (freqs, power)."""
    nper = min(n_fft, len(x))
    f, p = welch(x, fs=sr, nperseg=nper, noverlap=nper // 2, window="hann")
    return f, p


def _log_freq_grid(sr: int, f_lo: float = 30.0, pts_per_octave: int = 24) -> np.ndarray:
    f_hi = 0.95 * sr / 2
    n_oct = np.log2(f_hi / f_lo)
    n = int(np.ceil(n_oct * pts_per_octave)) + 1
    return f_lo * 2.0 ** (np.arange(n) / pts_per_octave)


def _smooth_spectrum_db(
    f: np.ndarray, p: np.ndarray, grid: np.ndarray, octave_fraction: float = 6.0
) -> np.ndarray:
    """Interpolate power spectrum onto a log grid and smooth by 1/N octave."""
    p_db = 10.0 * np.log10(p + EPS)
    g_db = np.interp(grid, f, p_db)
    pts_per_octave = 1.0 / np.log2(grid[1] / grid[0])
    sigma = pts_per_octave / octave_fraction / 2.355
    radius = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / max(sigma, 1e-6)) ** 2)
    k /= k.sum()
    padded = np.pad(g_db, radius, mode="edge")
    return np.convolve(padded, k, mode="valid")


def _ltas_db(x: np.ndarray, sr: int, octave_fraction: float = 6.0) -> tuple[np.ndarray, np.ndarray]:
    """Return (grid_hz, ltas_db) for signal x. Level-normalised (median = 0)."""
    grid = _log_freq_grid(sr)
    fw, pw = _ltas(x, sr)
    db = _smooth_spectrum_db(fw, pw, grid, octave_fraction)
    db -= np.median(db)
    return grid, db


# ---------------------------------------------------------------------------
# FIR design (inlined from tonematch/match_eq.py)
# ---------------------------------------------------------------------------


def _minimum_phase_fir(mag: np.ndarray, n_taps: int) -> np.ndarray:
    n_fft = 2 * (len(mag) - 1)
    log_mag = np.log(np.maximum(mag, 1e-8))
    ceps = np.fft.irfft(log_mag)
    w = np.zeros(n_fft)
    w[0] = 1.0
    w[1: n_fft // 2] = 2.0
    w[n_fft // 2] = 1.0
    min_phase_spec = np.exp(np.fft.rfft(ceps * w))
    h = np.fft.irfft(min_phase_spec)[:n_taps]
    fade = int(0.25 * n_taps)
    h[-fade:] *= 0.5 * (1.0 + np.cos(np.linspace(0, np.pi, fade)))
    return h


def _fir_from_log_spectrum(
    grid: np.ndarray,
    gain_db: np.ndarray,
    sr: int,
    n_taps: int = 2048,
) -> np.ndarray:
    n_fft = 4 * n_taps
    f_lin = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    lf = np.log10(np.maximum(f_lin, grid[0] / 2))
    g_db = np.interp(lf, np.log10(grid), gain_db, left=gain_db[0], right=gain_db[-1])
    mag = 10.0 ** (g_db / 20.0)
    return _minimum_phase_fir(mag, n_taps).astype(np.float32)


def _design_ir(
    candidate: np.ndarray,
    target: np.ndarray,
    sr: int,
    n_taps: int = 2048,
    max_gain_db: float = 18.0,
    octave_fraction: float = 6.0,
    f_lo_taper: float = 50.0,
    f_hi_taper: float = 11000.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Design a min-phase FIR that makes `candidate` match `target` spectrally.

    Returns (ir, grid_hz, correction_db).
    """
    grid = _log_freq_grid(sr)
    fc, pc = _ltas(candidate, sr)
    ft, pt = _ltas(target, sr)
    c_db = _smooth_spectrum_db(fc, pc, grid, octave_fraction)
    t_db = _smooth_spectrum_db(ft, pt, grid, octave_fraction)
    corr_db = t_db - c_db
    # 0 dB reference in the mid band
    mid = (grid > 200) & (grid < 4000)
    corr_db -= np.median(corr_db[mid])

    # taper to 0 dB at extremes — don't boost rumble or air
    lo_fade = np.clip(np.log2(grid / (f_lo_taper / 2)) / np.log2(2.0), 0.0, 1.0)
    hi_fade = np.clip(np.log2((f_hi_taper * 2) / grid) / np.log2(2.0), 0.0, 1.0)
    corr_db = corr_db * lo_fade * hi_fade
    corr_db = np.clip(corr_db, -max_gain_db, max_gain_db)

    ir = _fir_from_log_spectrum(grid, corr_db, sr, n_taps)
    return ir, grid, corr_db


# ---------------------------------------------------------------------------
# Tone stack fitting (inlined from tonematch/tone_stack.py)
# ---------------------------------------------------------------------------

_BASS_FREQ, _BASS_Q   = 150.0, 0.707
_MID_FREQ             = 425.0
_TREB_FREQ, _TREB_Q   = 1800.0, 0.707


def _knob_gains_db(bass: float, mid: float, treble: float):
    return 4.0 * (bass - 5.0), 3.0 * (mid - 5.0), 2.0 * (treble - 5.0)


def _biquad_coeffs(kind: str, fs: float, f0: float, q: float, gain_db: float):
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / fs
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2.0 * q)
    if kind == "lowshelf":
        b0 = A * ((A+1) - (A-1)*cw + 2*np.sqrt(A)*alpha)
        b1 = 2*A * ((A-1) - (A+1)*cw)
        b2 = A * ((A+1) - (A-1)*cw - 2*np.sqrt(A)*alpha)
        a0 = (A+1) + (A-1)*cw + 2*np.sqrt(A)*alpha
        a1 = -2 * ((A-1) + (A+1)*cw)
        a2 = (A+1) + (A-1)*cw - 2*np.sqrt(A)*alpha
    elif kind == "highshelf":
        b0 = A * ((A+1) + (A-1)*cw + 2*np.sqrt(A)*alpha)
        b1 = -2*A * ((A-1) + (A+1)*cw)
        b2 = A * ((A+1) + (A-1)*cw - 2*np.sqrt(A)*alpha)
        a0 = (A+1) - (A-1)*cw + 2*np.sqrt(A)*alpha
        a1 = 2 * ((A-1) - (A+1)*cw)
        a2 = (A+1) - (A-1)*cw - 2*np.sqrt(A)*alpha
    elif kind == "peaking":
        b0 = 1 + alpha*A;  b1 = -2*cw;  b2 = 1 - alpha*A
        a0 = 1 + alpha/A;  a1 = -2*cw;  a2 = 1 - alpha/A
    else:
        raise ValueError(kind)
    b = np.array([b0, b1, b2]) / a0
    a = np.array([1.0, a1/a0, a2/a0])
    return b, a


def _band_response_db(band: str, knob: float, freqs: np.ndarray, fs: float) -> np.ndarray:
    bg, mg, tg = _knob_gains_db(knob, knob, knob)
    if band == "bass":
        kind, f0, q, g = "lowshelf",  _BASS_FREQ, _BASS_Q, bg
    elif band == "middle":
        kind, f0, q, g = "peaking",   _MID_FREQ,  1.5 if mg < 0 else 0.7, mg
    elif band == "treble":
        kind, f0, q, g = "highshelf", _TREB_FREQ, _TREB_Q, tg
    else:
        raise ValueError(band)
    if abs(g) < 1e-9:
        return np.zeros_like(freqs)
    b, a = _biquad_coeffs(kind, fs, f0, q, g)
    w = 2.0 * np.pi * freqs / fs
    z = np.exp(-1j * w)
    h = (b[0] + b[1]*z + b[2]*z**2) / (1.0 + a[1]*z + a[2]*z**2)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-9))


def tone_stack_knobs(
    amped_ltas_db: np.ndarray,
    target_ltas_db: np.ndarray,
    grid: np.ndarray,
    sr: float,
    f_lo: float = 80.0,
    f_hi: float = 10000.0,
    reg: float = 0.01,
) -> dict:
    """Find NAM plugin Bass/Mid/Treble knob values (0–10) that best close
    the LTAS gap between `amped_ltas_db` and `target_ltas_db`.

    Both arrays must be on the same `grid` (Hz), as returned by _ltas_db().
    Returns {"bass": float, "middle": float, "treble": float,
             "bass_gain_db": float, "middle_gain_db": float, "treble_gain_db": float,
             "mse_before": float, "mse_after": float}.
    """
    sel = (grid >= f_lo) & (grid <= f_hi)
    g = grid[sel]
    a_db = amped_ltas_db[sel]
    t_db = target_ltas_db[sel]
    t_db = t_db - np.median(t_db)

    def search(bv, mv, tv):
        rb = np.stack([_band_response_db("bass",   v, g, sr) for v in bv])
        rm = np.stack([_band_response_db("middle", v, g, sr) for v in mv])
        rt = np.stack([_band_response_db("treble", v, g, sr) for v in tv])
        total = rb[:, None, None, :] + rm[None, :, None, :] + rt[None, None, :, :] + a_db
        total = total - np.median(total, axis=-1, keepdims=True)
        mse = np.mean((total - t_db) ** 2, axis=-1)
        pen = reg * (
            (bv[:, None, None] - 5.0) ** 2
            + (mv[None, :, None] - 5.0) ** 2
            + (tv[None, None, :] - 5.0) ** 2
        )
        cost = mse + pen
        i, j, k = np.unravel_index(np.argmin(cost), cost.shape)
        return float(bv[i]), float(mv[j]), float(tv[k]), float(mse[i, j, k])

    coarse = np.arange(0.0, 10.01, 0.5)
    b0, m0, t0, _ = search(coarse, coarse, coarse)

    def around(v):
        return np.clip(np.arange(v - 0.5, v + 0.51, 0.1), 0.0, 10.0)

    b, m, t, mse_after = search(around(b0), around(m0), around(t0))
    a_norm = a_db - np.median(a_db)
    mse_before = float(np.mean((a_norm - t_db) ** 2))
    gains = _knob_gains_db(b, m, t)
    return {
        "bass": round(b, 1), "middle": round(m, 1), "treble": round(t, 1),
        "bass_gain_db": round(gains[0], 2),
        "middle_gain_db": round(gains[1], 2),
        "treble_gain_db": round(gains[2], 2),
        "mse_before": round(mse_before, 3),
        "mse_after": round(mse_after, 3),
    }


# ---------------------------------------------------------------------------
# Public API — the three functions tone_worker.py calls
# ---------------------------------------------------------------------------


def match_ir(
    candidate: np.ndarray,
    target: np.ndarray,
    sr: int,
    n_taps: int = 2048,
) -> np.ndarray:
    """Minimum-phase FIR that corrects the spectral gap (candidate → target).

    ±18 dB cap, 1/6-octave smoothing, tapered at extremes.
    Suitable as a drop-in replacement for `print-ir.wav` when the NAM core
    itself is already close — this handles only the linear (EQ/cab) residual.

    candidate: the NAM core's output audio (after pick's `pre()` and NAM)
    target:    the isolated guitar recording (the record, mono)
    Returns:   float32 impulse response, length n_taps
    """
    ir, _, _ = _design_ir(candidate, target, sr, n_taps=n_taps, max_gain_db=18.0, octave_fraction=6.0)
    return ir


def gentle_ir(
    candidate: np.ndarray,
    target: np.ndarray,
    sr: int,
    n_taps: int = 2048,
) -> np.ndarray:
    """Like match_ir but gentler: ±9 dB cap, 1/3-octave smoothing.

    Designed to be used AFTER the NAM plugin's Bass/Mid/Treble EQ has already
    handled the coarse correction. The combination (plugin EQ + gentle_ir) is
    the 'hybrid' approach from NAM-EQ-Match and usually sounds most natural.

    candidate: the NAM core output *after* tone-stack EQ has been applied
    target:    the isolated guitar recording (mono)
    Returns:   float32 impulse response, length n_taps
    """
    ir, _, _ = _design_ir(candidate, target, sr, n_taps=n_taps, max_gain_db=9.0, octave_fraction=3.0)
    return ir


def eq_enrich(
    core_out: np.ndarray,
    target: np.ndarray,
    sr: int,
    out_dir,            # pathlib.Path or str
    n_taps: int = 2048,
) -> dict:
    """Run all three analyses and write the extra files next to print-ir.wav.

    Writes:
      match-ir.wav      — full match IR (replaces print-ir.wav when loaded alone)
      gentle-ir.wav     — gentle residual IR (use after plugin EQ)
      eq-match.json     — tone-stack knobs + LTAS curves for the UI

    Returns a dict suitable for merging into tone_worker's result JSON:
      {"eq_match": {"bass", "middle", "treble", ..., "match_ir", "gentle_ir"}}

    This is the only function tone_worker.py needs to call.
    """
    import json
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1) LTAS of both signals on the same grid
    grid, c_db = _ltas_db(core_out, sr, octave_fraction=6.0)
    _,    t_db = _ltas_db(target,   sr, octave_fraction=6.0)

    # 2) Match IR (full, ±18 dB)
    mir = match_ir(core_out, target, sr, n_taps)

    # 3) Tone-stack knobs
    knobs = tone_stack_knobs(c_db, t_db, grid, float(sr))

    # 4) Apply tone-stack EQ in-place (offline, for the gentle IR input)
    from scipy.signal import lfilter
    eq_out = core_out.copy().astype(np.float64)
    for band, knob in (("bass", knobs["bass"]), ("middle", knobs["middle"]), ("treble", knobs["treble"])):
        bg, mg, tg = _knob_gains_db(knob, knob, knob)
        if band == "bass":
            kind, f0, q, g = "lowshelf",  _BASS_FREQ, _BASS_Q, bg
        elif band == "middle":
            kind, f0, q, g = "peaking",   _MID_FREQ,  1.5 if mg < 0 else 0.7, mg
        else:
            kind, f0, q, g = "highshelf", _TREB_FREQ, _TREB_Q, tg
        if abs(g) < 1e-9:
            continue
        b_c, a_c = _biquad_coeffs(kind, float(sr), f0, q, g)
        eq_out = lfilter(b_c, a_c, eq_out)
    eq_out = eq_out.astype(np.float32)

    # 5) Gentle IR (±9 dB, after EQ)
    gir = gentle_ir(eq_out, target, sr, n_taps)

    # 6) Write WAVs
    import soundfile as sf
    sf.write(str(out / "match-ir.wav"),  mir, sr, subtype="FLOAT")
    sf.write(str(out / "gentle-ir.wav"), gir, sr, subtype="FLOAT")

    # 7) Write JSON sidecar (for the UI / report)
    show = (grid >= 60) & (grid <= 14000)
    eq_data = {
        **knobs,
        "match_ir":  "match-ir.wav",
        "gentle_ir": "gentle-ir.wav",
        "curve": {
            "hz":       [int(x) for x in grid[show]],
            "core":     [round(float(v), 1) for v in c_db[show]],
            "target":   [round(float(v), 1) for v in t_db[show]],
            "correction": [round(float(v), 1) for v in np.clip(t_db - c_db, -18, 18)[show]],
        },
    }
    (out / "eq-match.json").write_text(
        json.dumps(eq_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"eq_match": eq_data}


# ---------------------------------------------------------------------------
# HOW TO USE IN tone_worker.py
# ---------------------------------------------------------------------------
#
# 1. Import at the top of tone_worker.py (after existing imports):
#
#       try:
#           from eq_match import eq_enrich
#           _EQ_MATCH_AVAILABLE = True
#       except ImportError:
#           _EQ_MATCH_AVAILABLE = False
#
#
# 2. In Fit.run(), right after the existing `sf.write(... "print-ir.wav" ...)`
#    line (around line 837), add:
#
#       if _EQ_MATCH_AVAILABLE:
#           try:
#               eq_info = eq_enrich(core_out, seg["r"].astype(np.float32), sr, out)
#               result_extra = eq_info          # merged into result dict below
#           except Exception as _e:
#               result_extra = {}
#               warnings.append(f"EQ match skipped: {_e}")
#       else:
#           result_extra = {}
#
#
# 3. In the result dict (around line 869), add **result_extra:
#
#       result = {
#           "version": VERSION, ...
#           **result_extra,           # <-- add this line
#       }
#
#
# That is all. The existing print-ir.wav is untouched.  The new files appear
# alongside it: match-ir.wav, gentle-ir.wav, eq-match.json.
# The result JSON then has an "eq_match" key with knobs and curves for the UI.
#
# If eq_match.py is not on PYTHONPATH, the import silently fails and Pick
# works exactly as before — the feature degrades gracefully to nothing.
