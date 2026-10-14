"""New words on an old tune: a spoken line laid onto the melody and rhythm of a sung one.

Run in the pick-svc environment (pyworld, librosa live there), one line per run:

    python resing_worker.py --orig line.wav --speech said.wav --out tuned.wav

The WORLD vocoder takes both apart into pitch, spectral envelope and breath.
The sung line gives where its notes start and what pitch they hold; the spoken
one gives its syllables. Each spoken syllable is moved to start on a note —
the first on the first, the last ending where the singing ends, the rest
spread over the notes between — and the voice is put back together on the
sung pitch. What comes out still sounds spoken-through-a-vocoder; Seed-VC
(svc_worker.py) then sings it in the original voice.

The last line on stdout is "DONE <syllables> <notes>" or "ERROR <what>".
"""
import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import soundfile as sf

FS = 44100
FP = 5.0                    # ms a frame


def load(path):
    import librosa
    x, _ = librosa.load(path, sr=FS, mono=True)
    return x.astype(np.float64)


def frames_db(x, n):
    hop = int(FS * FP / 1000)
    out = np.full(n, -120.0)
    for i in range(n):
        seg = x[i * hop:(i + 1) * hop]
        if len(seg):
            out[i] = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)
    return out


def smooth(v, k=5):
    if len(v) < k:
        return v
    return np.convolve(v, np.ones(k) / k, mode="same")


def units(f0, db, gap_db=6.0):
    """Starts (frame) of the sung or spoken units: a voiced run begins one, so does a
    dip in loudness inside it, and — for singing — a step to another note."""
    thr = max(np.percentile(db, 95) - 28, -55)
    on = (db > thr)
    voiced = (f0 > 0) & on
    starts, i, n = [], 0, len(f0)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if j - i >= 8:                      # 40 ms at least
                starts.append(i)
                sdb = smooth(db[i:j], 9)
                semi = 12 * np.log2(np.maximum(f0[i:j], 1) / 440.0)
                last = i
                for k in range(12, j - i - 12):
                    t = i + k
                    if t - last < 24:           # 120 ms between two at the least
                        continue
                    dip = sdb[k] < min(sdb[max(0, k - 12):k].max(), sdb[k:k + 12].max()) - gap_db and sdb[k] == sdb[k - 3:k + 4].min()
                    step = abs(np.median(semi[k:k + 10]) - np.median(semi[k - 10:k])) > 0.8
                    if dip or step:
                        starts.append(t)
                        last = t
            i = j
        else:
            i += 1
    # the span by loudness, not by voice: a word's last consonant ("-dek") has no pitch but is heard
    span = np.flatnonzero(voiced)
    if not len(span):
        return starts, (0, 0)
    loud = np.flatnonzero(on)
    a0 = next((int(t) for t in loud if t >= span[0] - 30), int(span[0]))
    a1 = next((int(t) for t in loud[::-1] if t <= span[-1] + 40), int(span[-1])) + 1
    return starts, (min(a0, int(span[0])), max(a1, int(span[-1]) + 1))


def main():
    import pyworld as pw
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--speech", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--syll", type=int, default=0)       # the new words' syllables, when known
    a = ap.parse_args()

    xo, xs = load(a.orig), load(a.speech)
    f0o, to = pw.harvest(xo, FS, f0_floor=65, f0_ceil=1100, frame_period=FP)
    f0s, ts = pw.harvest(xs, FS, f0_floor=60, f0_ceil=600, frame_period=FP)
    sp = pw.cheaptrick(xs, f0s, ts, FS)
    apr = pw.d4c(xs, f0s, ts, FS)
    dbo, dbs = frames_db(xo, len(f0o)), frames_db(xs, len(f0s))
    no, (o0, o1) = units(f0o, dbo)
    ns, (s0, s1) = units(f0s, dbs, gap_db=4.0)
    if o1 - o0 < 20 or s1 - s0 < 20:
        print("ERROR V řádku nebo v řeči není dost hlasu.", flush=True)
        return 2
    s0 = max(0, s0 - 6)
    s1 = min(len(f0s), s1 + 6)
    no = [t for t in no if o0 <= t < o1] or [o0]
    ns = [t for t in ns if s0 <= t < s1] or [s0]
    # more pieces of speech than the words have syllables: the closest ones are one syllable
    while a.syll and len(ns) > a.syll:
        gaps = [ns[i + 1] - ns[i] for i in range(len(ns) - 1)]
        del ns[1 + int(np.argmin(gaps))]
    # anchors: speech frame -> sung frame; each spoken syllable on a note, in order
    pairs = [(s0, o0)]
    K, M = len(ns), len(no)
    for k in range(1, K):
        m = int(round(k * M / K)) if M >= K else None
        target = no[m] if m is not None and m < M else o0 + (o1 - o0) * k / K
        pairs.append((ns[k], target))
    pairs.append((s1, o1))
    pairs.sort()
    sx = np.array([p[0] for p in pairs], dtype=float)
    ox = np.maximum.accumulate(np.array([p[1] for p in pairs], dtype=float))
    # every output frame of the sung span takes the speech frame the map gives it
    n_out = len(f0o)
    t_out = np.arange(n_out, dtype=float)
    src = np.interp(t_out, ox, sx, left=-1, right=-1)
    idx = np.clip(np.round(src).astype(int), 0, len(f0s) - 1)
    inside = (src >= 0)
    # the tune: the sung pitch where it is sure, bridged where it is not
    vo = np.flatnonzero(f0o > 0)
    tune = np.interp(t_out, vo, f0o[vo]) if len(vo) else np.full(n_out, 150.0)
    f0 = np.where(inside & (f0s[idx] > 0), tune, 0.0)
    sp_o = np.where(inside[:, None], sp[idx], sp.min())
    ap_o = np.where(inside[:, None], apr[idx], 1.0)
    y = pw.synthesize(np.ascontiguousarray(f0), np.ascontiguousarray(sp_o), np.ascontiguousarray(ap_o), FS, FP)
    y = y[:len(xo)]
    if len(y) < len(xo):
        y = np.concatenate([y, np.zeros(len(xo) - len(y))])
    hop = int(FS * FP / 1000)
    sung = xo[o0 * hop:o1 * hop]
    made = y[o0 * hop:o1 * hop]
    r = lambda v: float(np.sqrt(np.mean(v ** 2))) if len(v) else 0.0
    if r(made) > 1e-6:
        y = y * (r(sung) / r(made))
    peak = float(np.max(np.abs(y))) if len(y) else 0
    if peak > 0.97:
        y = y * 0.97 / peak
    sf.write(a.out, y.astype(np.float32), FS)
    print(f"DONE {K} {M}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                       # noqa: BLE001
        print(f"ERROR {str(e)[:200]}", flush=True)
        sys.exit(1)
