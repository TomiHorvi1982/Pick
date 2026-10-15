"""Tónový otisk — the sound of the guitar on a record, put onto the player's own guitar.

Run by the server with the interpreter of its own small venv (~/.local/venvs/pick-tone:
numpy, scipy, soundfile). The NAM cores are played by tone_nam.mjs in Node, with the
WebAssembly engine the live guitar plays through — what is fitted here is what is heard.

    python tone_worker.py --job job.json    a print fitted: STAGE lines, then DONE or ERROR
    python tone_worker.py --selftest        a made-up guitar through a known chain, found again

The chain, the very one static/tone.js builds live:
    clean guitar → drive → pre-EQ (highpass, mid, tilt) → core (a NAM model, or none)
    → print-ir.wav (all that is linear after the core, the level too) → room → [doubler]

The colour is not searched for: for every core it is worked out exactly, as the record's
long-term spectrum over the core's, and becomes one minimum-phase impulse response. The
search is over what colour cannot do — how much the level of each band moves (distortion
squeezes it), how flat the spectrum is between the harmonics, the crest, the sustain after
a note starts, how the bands follow the record's. The room comes from how the record's notes
die away where the player stopped, the width from how alike its left and right are.
"""
import itertools
import json
import math
import queue
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import optimize, signal

N_FFT, HOP = 2048, 512
IR_LEN = 2048
PRE_MID_HZ, PRE_LOW_HZ, PRE_HIGH_HZ, HP_Q_DB = 750.0, 250.0, 2000.0, -3.0103   # static/tone.js has the same
ROOM_SEED, ROOM_POLE, ROOM_PREDELAY = 1, 0.55, 0.012
BOUNDS = {"drive_db": (-24.0, 24.0), "hp": (20.0, 400.0), "mid_db": (-9.0, 9.0), "tilt_db": (-9.0, 9.0)}
VERSION = 1


def say(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- the chain

def biquad(kind, f0, sr, q=0.7, gain_db=0.0):
    """One Web Audio BiquadFilterNode, by the spec's formulas (the Q of a highpass in dB,
    shelves with S = 1) — the filter here is the one the live chain runs."""
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    A = 10 ** (gain_db / 40)
    if kind == "highpass":
        al = sw / (2 * 10 ** (q / 20))
        b, a = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2], [1 + al, -2 * cw, 1 - al]
    elif kind == "lowpass":
        al = sw / (2 * 10 ** (q / 20))
        b, a = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2], [1 + al, -2 * cw, 1 - al]
    elif kind == "peaking":
        al = sw / (2 * q)
        b, a = [1 + al * A, -2 * cw, 1 - al * A], [1 + al / A, -2 * cw, 1 - al / A]
    else:
        k = 2 * math.sqrt(A) * sw / math.sqrt(2)
        if kind == "lowshelf":
            b = [A * ((A + 1) - (A - 1) * cw + k), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - k)]
            a = [(A + 1) + (A - 1) * cw + k, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - k]
        else:
            b = [A * ((A + 1) + (A - 1) * cw + k), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - k)]
            a = [(A + 1) - (A - 1) * cw + k, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - k]
    return np.array(b) / a[0], np.array(a) / a[0]


def pre(x, p, sr):
    """Drive and the EQ before the core."""
    y = np.asarray(x, np.float64) * 10 ** (p["drive_db"] / 20)
    if p.get("hp", 0) > 20:
        y = signal.lfilter(*biquad("highpass", p["hp"], sr, HP_Q_DB), y)
    if abs(p.get("mid_db", 0)) > 0.05:
        y = signal.lfilter(*biquad("peaking", PRE_MID_HZ, sr, 0.7, p["mid_db"]), y)
    if abs(p.get("tilt_db", 0)) > 0.05:
        y = signal.lfilter(*biquad("lowshelf", PRE_LOW_HZ, sr, 0, -p["tilt_db"] / 2), y)
        y = signal.lfilter(*biquad("highshelf", PRE_HIGH_HZ, sr, 0, p["tilt_db"] / 2), y)
    return y.astype(np.float32)


def mulberry32(seed, n):
    """The made-up room's noise: the same numbers static/tone.js draws (mulberry32)."""
    out, a, m = np.empty(n), seed & 0xFFFFFFFF, 0xFFFFFFFF
    for i in range(n):
        a = (a + 0x6D2B79F5) & m
        t = ((a ^ (a >> 15)) * (a | 1)) & m
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & m)) & m) ^ t
        out[i] = ((t ^ (t >> 14)) & m) / 4294967296
    return out


_ROOMS = {}


def room_ir(sr, decay):
    """A plain room: noise dying away by 60 dB in `decay` seconds, darker as it goes, energy 1."""
    key = (sr, round(decay, 3))
    if key not in _ROOMS:
        n = int(sr * min(3.5, 1.5 * decay))
        h = (mulberry32(ROOM_SEED, n) * 2 - 1) * np.exp(-6.907755 * np.arange(n) / sr / decay)
        h = signal.lfilter([1 - ROOM_POLE], [1, -ROOM_POLE], h)
        h = np.concatenate([np.zeros(int(sr * ROOM_PREDELAY)), h])
        _ROOMS[key] = (h / math.sqrt(float(np.sum(h * h)))).astype(np.float32)
    return _ROOMS[key]


def with_room(y, room, sr):
    if not room or room.get("mix", 0) <= 0:
        return y
    return (y + room["mix"] * signal.oaconvolve(y, room_ir(sr, room["decay"]))[:len(y)]).astype(np.float32)


def convolve(y, h):
    return signal.oaconvolve(y, h)[:len(y)].astype(np.float32)


# ---------------------------------------------------------------- the cores

class Nam:
    """Node processes playing NAM models (tone_nam.mjs --serve), each one render at a time."""

    def __init__(self, job, n):
        self.tmp = Path(tempfile.mkdtemp(prefix="pick-tone-"))
        self.free, self.procs, self.count = queue.Queue(), [], itertools.count()
        for _ in range(max(1, n)):
            p = subprocess.Popen([job["node"], job["nam_js"], "--serve", job["worklet"], job["wasm"], str(job["sr"])],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            line = (p.stdout.readline() or "").strip()
            if line != "READY":
                p.kill()
                self.close()
                raise RuntimeError("Engine NAM se nespustil" + (f": {line}" if line else "."))
            self.procs.append(p)
            self.free.put(p)

    def render(self, model, xs):
        """`xs` through one model (None: no core), in one go — the model is loaded once."""
        if model is None:
            return [np.asarray(x, np.float32).copy() for x in xs]
        k = next(self.count)
        ins = [self.tmp / f"{k}-{i}.in" for i in range(len(xs))]
        outs = [self.tmp / f"{k}-{i}.out" for i in range(len(xs))]
        for x, f in zip(xs, ins):
            np.asarray(x, np.float32).tofile(f)
        p = self.free.get()
        try:
            p.stdin.write(json.dumps({"model": model, "inputs": [str(f) for f in ins], "outputs": [str(f) for f in outs]}) + "\n")
            p.stdin.flush()
            ans = (p.stdout.readline() or "").strip()
        finally:
            self.free.put(p)
        if not ans.startswith("OK"):
            raise RuntimeError(ans[4:] or "Engine NAM nic nevrátil.")
        got = [np.fromfile(f, dtype=np.float32) for f in outs]
        for f in ins + outs:
            f.unlink(missing_ok=True)
        return got

    def close(self):
        for p in self.procs:
            try:
                p.stdin.close()
                p.wait(timeout=5)
            except Exception:
                p.kill()
        self.procs = []
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- listening

def _bands(freqs, centres, width_oct):
    """Weights of bins into bands: a bell on the log-frequency axis, never narrower than a bin."""
    lf = np.log2(np.maximum(freqs, 1.0))
    bin_oct = (freqs[1] - freqs[0]) / np.maximum(centres, 1.0) / math.log(2)
    sig = np.maximum(width_oct, 0.6 * bin_oct)[:, None]
    w = np.exp(-0.5 * ((lf[None, :] - np.log2(centres)[:, None]) / sig) ** 2)
    w[:, 0] = 0
    return (w / np.maximum(w.sum(1, keepdims=True), 1e-12)).astype(np.float32)


class Ear:
    """The frames, the bands and the measures every signal of one fit is heard by."""

    COARSE = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000], float)

    def __init__(self, sr):
        self.sr = sr
        self.win = np.hanning(N_FFT).astype(np.float32)
        self.freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
        self.fine_c = 50 * 2 ** (np.arange(int(6 * math.log2(16000 / 50)) + 1) / 6)
        self.fine_w = _bands(self.freqs, self.fine_c, 1 / 12)
        lo, hi = self.COARSE / math.sqrt(2), self.COARSE * math.sqrt(2)
        self.coarse_bins = [np.where((self.freqs >= a) & (self.freqs < b))[0] for a, b in zip(lo, hi)]
        self.coarse_w = np.zeros((len(self.COARSE), len(self.freqs)), np.float32)
        for i, idx in enumerate(self.coarse_bins):
            self.coarse_w[i, idx] = 1.0
        self.body = np.where((self.freqs >= 80) & (self.freqs <= 5000))[0]

    def power(self, x):
        x = np.asarray(x, np.float32)
        if len(x) < N_FFT:
            x = np.pad(x, (0, N_FFT - len(x)))
        n = 1 + (len(x) - N_FFT) // HOP
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
        f = np.fft.rfft(x[idx] * self.win, axis=1)
        return (f.real ** 2 + f.imag ** 2).astype(np.float32)

    def env(self, p):
        return 10 * np.log10(p[:, self.body].sum(1) + 1e-10)

    def context(self, r, d):
        """Where both play, where the player's notes start and where they stop."""
        pr, pd = self.power(r), self.power(d)
        n = min(len(pr), len(pd))
        er, ed = self.env(pr[:n]), self.env(pd[:n])
        active = (er > er.max() - 35) & (ed > ed.max() - 40)
        rise = np.diff(ed, prepend=ed[0])
        onsets = [t for t in range(2, n - 2) if rise[t] > 6 and ed[t] > ed.max() - 40 and rise[t] >= rise[t - 2:t + 3].max()]
        onsets = [t for i, t in enumerate(onsets) if not i or t - onsets[i - 1] >= 8]
        alone = [t for i, t in enumerate(onsets) if t + 30 < n and (i + 1 == len(onsets) or onsets[i + 1] - t >= 30)]
        stops = []
        for t in range(1, n - 30):
            if ed[t] > ed.max() - 30 and ed[t + 1:t + 6].min() < ed[t] - 18 and ed[t + 6:t + 30].max() < ed[t] - 18 \
                    and (not stops or t - stops[-1] > 30):
                stops.append(t)
        return {"n": n, "active": active, "onsets": alone, "stops": stops}

    def measure(self, y, ctx):
        p = self.power(y)[:ctx["n"]]
        act = ctx["active"]
        fine, coarse = p @ self.fine_w.T, p @ self.coarse_w.T
        bdb = 10 * np.log10(coarse + 1e-12)
        out = {"ltas": 10 * np.log10(fine[act].mean(0) + 1e-14),
               "spread": np.percentile(bdb[act], 90, axis=0) - np.percentile(bdb[act], 10, axis=0),
               "bands": bdb[act]}
        flat = []
        for idx in self.coarse_bins:
            q = p[act][:, idx] + 1e-14
            am = q.mean(1)
            fl = 10 * np.log10(np.exp(np.log(q).mean(1)) / am)
            flat.append(float(np.sum(fl * am) / np.sum(am)))
        out["flat"] = np.array(flat)
        ys = np.asarray(y, np.float32)
        cr = []
        for t in np.where(act)[0][::2]:
            seg = ys[t * HOP:t * HOP + N_FFT]
            rms = math.sqrt(float(np.mean(seg * seg)) + 1e-14)
            cr.append(20 * math.log10((float(np.abs(seg).max()) + 1e-9) / rms))
        out["crest"] = float(np.median(cr)) if cr else 0.0
        e = self.env(p)
        sus = [[e[t + k] - e[t:t + 5].max() for k in (4, 14, 28)] for t in ctx["onsets"] if t + 28 < len(e)]
        out["sustain"] = np.median(np.array(sus), 0) if len(sus) >= 3 else None
        tails = [[e[t + 10] - e[t], e[t + 25] - e[t]] for t in ctx["stops"] if t + 25 < len(e)]
        out["tail"] = np.median(np.array(tails), 0) if len(tails) >= 2 else None
        return out

    def colour(self, ltas_ref, ltas_core):
        """The EQ after the core, in dB a band: the record's spectrum over the core's — not chased
        above 12 kHz (the record or its separation has little there), smoothed, held in reason."""
        d = ltas_ref - ltas_core
        top = self.fine_c > 12000
        if top.any():
            d[top] = np.minimum(d[top], d[~top][-1])
        d = np.convolve(np.pad(d, 1, mode="edge"), np.ones(3) / 3, mode="valid")
        return np.clip(d, -45.0, 36.0)

    def ir(self, d_db):
        """A minimum-phase impulse response with that response (the folded cepstrum)."""
        nf = 16384
        f = np.fft.rfftfreq(nf, 1 / self.sr)
        m = np.interp(np.log2(np.maximum(f, 1.0)), np.log2(self.fine_c), d_db)
        m -= 24 * np.clip((40 - f) / 20, 0, 1) + 24 * np.clip((f - 16000) / 4000, 0, 1)
        c = np.fft.irfft(m * math.log(10) / 20, nf)
        fold = np.zeros(nf)
        fold[0], fold[1:nf // 2], fold[nf // 2] = 1, 2, 1
        h = np.fft.irfft(np.exp(np.fft.rfft(c * fold, nf)), nf)[:IR_LEN]
        h[-256:] *= np.hanning(512)[256:]
        return h.astype(np.float32)


def loss(fy, fr):
    """How far a candidate is from the record in what the colour cannot fix (0 = the same)."""
    parts = {"spread": float(np.mean(np.abs(fy["spread"] - fr["spread"])[1:7]) / 4.0),
             "flat": float(np.mean(np.abs(fy["flat"] - fr["flat"])[1:8]) / 2.5),
             "crest": abs(fy["crest"] - fr["crest"]) / 2.5}
    parts["sustain"] = float(np.mean(np.abs(fy["sustain"] - fr["sustain"])) / 4.0) \
        if fy["sustain"] is not None and fr["sustain"] is not None else 0.0
    cs = []
    for b in range(1, 7):
        a, c = fy["bands"][:, b], fr["bands"][:, b]
        if a.std() > 1e-6 and c.std() > 1e-6:
            cs.append(float(np.corrcoef(a, c)[0, 1]))
    parts["follow"] = max(0.0, 1 - float(np.mean(cs))) / 0.35 if cs else 0.0
    total = parts["spread"] + parts["flat"] + 0.7 * parts["crest"] + 0.7 * parts["sustain"] + 0.4 * parts["follow"]
    return total, parts


def match_of(total):
    return int(round(100 * math.exp(-total / 3.0)))


def describe(ear, f):
    """What the report shows of a sound, in plain numbers."""
    lt = f["ltas"]
    # a band's level is its power a bin; × its width (∝ its centre at 1/6 octave) it is all its power
    pw = 10 ** (lt / 10) * ear.fine_c
    body = (ear.fine_c >= 250) & (ear.fine_c <= 4000)
    bass = (ear.fine_c >= 80) & (ear.fine_c < 250)
    return {"drive": int(np.clip((float(np.mean(f["flat"][3:7])) + 22) / 14 * 100, 0, 100)),
            "sustain": None if f["sustain"] is None else round(float(f["sustain"][2]), 1),
            "bright": int(np.sum(ear.fine_c * pw) / np.sum(pw)),
            "bass": round(float(10 * np.log10(np.sum(pw[bass]) / np.sum(pw[body]))), 1),
            "room": None if f["tail"] is None else round(float(f["tail"][1]), 1)}


# ---------------------------------------------------------------- the fit

def _read(path, sr, stereo=False):
    x, r = sf.read(str(path), dtype="float32", always_2d=True)
    if r != sr:
        g = math.gcd(int(r), int(sr))
        x = signal.resample_poly(x, sr // g, r // g, axis=0).astype(np.float32)
    if stereo:
        return x if x.shape[1] >= 2 else np.repeat(x, 2, axis=1)
    return x.mean(1)


def _lag(ear, r, d):
    """How late the player's take is against the record (samples), from where notes start."""
    def odf(x):
        e = ear.env(ear.power(x))
        return np.maximum(0, np.diff(e, prepend=e[0]))
    a, b = odf(r), odf(d)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    best, lag = -1e9, 0
    for k in range(-14, 15):
        v = float(np.dot(a[max(0, k):n + min(0, k)], b[max(0, -k):n - max(0, k)]))
        if v > best:
            best, lag = v, k
    return -lag * HOP


def segments(job, sr):
    """The record's stretch and the takes on it, each take moved onto the record."""
    ref2 = _read(job["ref"], sr, stereo=True)
    ear = Ear(sr)
    rs, ds, rs2, place, lags = [], [], [], [], []
    for t in job["takes"]:
        x = _read(t["path"], sr)
        off = int(round(t.get("offset", 0.0) * sr))
        a, b = max(0, off), min(len(ref2), off + len(x))
        if b - a < sr * 2:
            continue
        r2, d = ref2[a:b], x[a - off:b - off]
        lag = _lag(ear, r2.mean(1), d)
        if lag > 0:
            d = np.concatenate([d[lag:], np.zeros(lag, np.float32)])
        elif lag < 0:
            d = np.concatenate([np.zeros(-lag, np.float32), d[:lag]])
        rs.append(r2.mean(1))
        rs2.append(r2)
        ds.append(d)
        place.append((a, b))
        lags.append(round(1000 * lag / sr))
    if not rs:
        raise RuntimeError("Nahrávka se s vybraným úsekem písně nepřekrývá — nahraj ji znovu.")
    gap = np.zeros(int(sr * 0.25), np.float32)
    cat = lambda parts: np.concatenate([p for q in parts for p in (q, gap)])[:-len(gap)]
    return {"r": cat(rs), "d": cat(ds), "r2": np.concatenate(rs2), "place": place, "lags": lags, "len": len(ref2),
            "seg_lens": [len(x) for x in rs]}


def _window(active, frames):
    """The stretch of `frames` frames with the most playing in it."""
    if len(active) <= frames:
        return 0, len(active)
    c = np.convolve(active.astype(float), np.ones(frames), mode="valid")
    a = int(np.argmax(c))
    return a, a + frames


class Fit:
    def __init__(self, job):
        self.job = job
        self.sr = int(job["sr"])
        self.ear = Ear(self.sr)

    def stage(self, key, done=0, of=0):
        say(f"STAGE {key} {done} {of}")

    def evaluate(self, core_out, fr, ctx, room=None):
        """A core's sound with its colour worked out: the loss, the IR, what it measures."""
        fc = self.ear.measure(with_room(core_out, room, self.sr), ctx)
        h = self.ear.ir(self.ear.colour(fr["ltas"], fc["ltas"]))
        y = with_room(convolve(core_out, h), room, self.sr)
        fy = self.ear.measure(y, ctx)
        total, parts = loss(fy, fr)
        return total, parts, h, y, fy

    def run(self):
        job, sr, ear = self.job, self.sr, self.ear
        self.stage("listen")
        seg = segments(job, sr)
        r, d = seg["r"], seg["d"]
        warnings = []
        if float(np.mean(np.abs(d) > 0.985)) > 0.0005:
            warnings.append("Nahrávka kytary je místy přebuzená — příště uber GAIN.")
        if float(np.abs(d).max()) < 0.02:
            warnings.append("Nahrávka kytary je hodně tichá — příště přidej GAIN.")
        ctx = ear.context(r, d)
        played = float(ctx["active"].sum()) * HOP / sr
        if played < 3:
            raise RuntimeError(f"V úseku jsem slyšel hrát vás oba jen {played:.0f} s — vyber úsek, kde kytara hraje, a zahraj ho celý.")
        fr = ear.measure(r, ctx)
        # the search hears a shorter stretch: the most played 8 s (4 s for the first sieve)
        a8, b8 = _window(ctx["active"], int(8 * sr / HOP))
        a4, b4 = _window(ctx["active"], int(4 * sr / HOP))
        cut = lambda x, a, b: x[a * HOP:b * HOP + N_FFT]
        r8, d8, r4, d4 = cut(r, a8, b8), cut(d, a8, b8), cut(r, a4, b4), cut(d, a4, b4)
        c8, c4 = ear.context(r8, d8), ear.context(r4, d4)
        f8, f4 = ear.measure(r8, c8), ear.measure(r4, c4)

        models = [None] + [m for m in job["models"]]
        prev = job.get("start_from") or {}
        procs = int(job.get("procs", 3))
        nam = Nam(job, procs)
        try:
            flat = {"hp": 0.0, "mid_db": 0.0, "tilt_db": 0.0}
            # 1 — every core, two drives, 4 s
            scores, lock = {}, threading.Lock()
            done = itertools.count(1)
            if prev.get("ranking"):
                keep = {x["id"] for x in prev["ranking"][:12]} | {(prev.get("core") or {}).get("id")}
                models = [None] + [m for m in job["models"] if m["id"] in keep]

            def sieve(m, drives, d_, f_, c_):
                outs = nam.render(m and m["path"], [pre(d_, {**flat, "drive_db": g}, sr) for g in drives])
                got = []
                for g, o in zip(drives, outs):
                    got.append((self.evaluate(o, f_, c_)[0], g))
                return got

            with ThreadPoolExecutor(procs) as pool:
                futs = {pool.submit(sieve, m, [0.0] if m is None else [-6.0, 6.0], d4, f4, c4): m for m in models}
                for fu in futs:
                    m = futs[fu]
                    best = min(fu.result())
                    with lock:
                        scores[m["id"] if m else None] = best
                    self.stage("sieve", next(done), len(models))
            ranked = sorted(models, key=lambda m: scores[m["id"] if m else None][0])
            # 2 — the best 20, three drives, 8 s
            top = ranked[:20]
            done = itertools.count(1)
            fine = {}
            with ThreadPoolExecutor(procs) as pool:
                futs = {pool.submit(sieve, m, [0.0] if m is None else [-12.0, -3.0, 6.0, 15.0], d8, f8, c8): m for m in top}
                for fu in futs:
                    m = futs[fu]
                    fine[m["id"] if m else None] = min(fu.result())
                    self.stage("sieve2", next(done), len(top))
            ranked = sorted(top, key=lambda m: fine[m["id"] if m else None][0])
            ranking = [{"id": m["id"] if m else None, "name": m["name"] if m else "bez jádra (čistý zvuk)",
                        "loss": round(fine[m["id"] if m else None][0], 3)} for m in ranked]
        finally:
            nam.close()

        # 3 — the best four cores, the drive and the EQ before them tuned
        best4 = ranked[:4]
        evals = itertools.count(1)
        budget = 36 * sum(1 for m in best4 if m is not None) + 1

        def tune(m):
            if m is None:
                return fine[None][0], {"drive_db": 0.0, **flat}
            own = Nam(job, 1)
            try:
                lo = np.array([BOUNDS[k][0] for k in ("drive_db", "hp", "mid_db", "tilt_db")])
                hi = np.array([BOUNDS[k][1] for k in ("drive_db", "hp", "mid_db", "tilt_db")])
                as_p = lambda v: dict(zip(("drive_db", "hp", "mid_db", "tilt_db"), np.clip(v, lo, hi).tolist()))
                memo = {}

                def f(v):
                    p = as_p(v)
                    key = tuple(round(x, 2) for x in p.values())
                    if key not in memo:
                        o = own.render(m["path"], [pre(d8, p, sr)])[0]
                        memo[key] = self.evaluate(o, f8, c8)[0]
                        self.stage("tune", next(evals), budget)
                    return memo[key]
                x0 = np.array([fine[m["id"]][1], 80.0, 0.0, 0.0])
                simplex = np.array([x0, x0 + [6, 0, 0, 0], x0 + [0, 90, 0, 0], x0 + [0, 0, 4, 0], x0 + [0, 0, 0, 4]])
                res = optimize.minimize(f, x0, method="Nelder-Mead",
                                        options={"maxfev": 36, "initial_simplex": simplex, "xatol": 0.5, "fatol": 0.005})
                return float(res.fun), as_p(res.x)
            finally:
                own.close()

        with ThreadPoolExecutor(min(4, procs + 1)) as pool:
            tuned = list(pool.map(tune, best4))
        k = int(np.argmin([t[0] for t in tuned]))
        core, params = best4[k], tuned[k][1]

        # 4 — the whole of it: colour, room, width
        self.stage("colour")
        nam = Nam(job, 1)
        try:
            core_out = nam.render(core and core["path"], [pre(d, params, sr)])[0]
        finally:
            nam.close()
        total, parts, h, y, fy = self.evaluate(core_out, fr, ctx)
        room = None
        if fr["tail"] is not None and fy["tail"] is not None and fr["tail"][1] - fy["tail"][1] > 3:
            self.stage("room")
            best = (abs(fr["tail"][1] - fy["tail"][1]) + abs(fr["tail"][0] - fy["tail"][0]), None)
            for decay in (0.5, 0.9, 1.5, 2.4):
                for mix in (0.1, 0.2, 0.35, 0.55):
                    rm = {"mix": mix, "decay": decay}
                    t_ = self.evaluate(core_out, fr, ctx, rm)
                    if t_[4]["tail"] is None:
                        continue
                    miss = abs(fr["tail"][1] - t_[4]["tail"][1]) + abs(fr["tail"][0] - t_[4]["tail"][0]) + 0.5 * t_[0]
                    if miss < best[0]:
                        best = (miss, rm)
            if best[1]:
                room = best[1]
                total, parts, h, y, fy = self.evaluate(core_out, fr, ctx, room)
        l2, r2_ = seg["r2"][:, 0], seg["r2"][:, 1]
        corr = float(np.dot(l2, r2_) / math.sqrt(float(np.dot(l2, l2) * np.dot(r2_, r2_)) + 1e-12))
        side = float(np.sum((l2 - r2_) ** 2) / (np.sum((l2 + r2_) ** 2) + 1e-12))
        width = {"on": bool(corr < 0.8 and side > 0.08), "corr": round(corr, 2)}

        self.stage("save")
        out = Path(job["out"])
        out.mkdir(parents=True, exist_ok=True)
        sf.write(str(out / "print-ir.wav"), h, sr, subtype="FLOAT")
        you = np.zeros(seg["len"], np.float32)
        at = 0
        for (a, b), n in zip(seg["place"], seg["seg_lens"]):
            you[a:b] = y[at:at + n][:b - a]
            at += n + int(sr * 0.25)
        sf.write(str(out / "you.wav"), you, sr, subtype="FLOAT")
        # what most likely went wrong, in plain words
        rec = describe(ear, fr)
        if parts["follow"] > 1.2:
            warnings.append("Tvoje nahrávka se s originálem v rytmu moc neshoduje — zahraj úsek přesně s písní, stejné tóny ve stejnou chvíli.")
        if rec["bright"] < 500:
            warnings.append("Kytara originálu tu zní hodně tmavě — v separované stopě je možná víc basy než kytary. Zkus úsek, kde je kytara výrazná.")
        if match_of(total) < 50 and not warnings:
            warnings.append("Shoda je nízká — zkus jiný úsek, nebo nahrávku znovu a pak Doladit.")
        ltas_y, ltas_r = fy["ltas"], fr["ltas"]
        show = (ear.fine_c >= 60) & (ear.fine_c <= 14000)
        result = {
            "version": VERSION, "sr": sr, "created": time.time(),
            "core": core and {"id": core["id"], "name": core["name"]},
            "drive_db": round(params["drive_db"], 2), "pre": {k: round(params[k], 2) for k in ("hp", "mid_db", "tilt_db")},
            "ir": "print-ir.wav", "room": room, "width": width,
            "match": match_of(total), "loss": round(total, 3), "parts": {k: round(v, 3) for k, v in parts.items()},
            "ranking": ranking[:12], "lags_ms": seg["lags"], "played_s": round(played, 1), "warnings": warnings,
            "report": {"record": rec, "you": describe(ear, fy),
                       "curve": {"hz": [int(x) for x in ear.fine_c[show]], "record": [round(float(v), 1) for v in ltas_r[show]],
                                 "you": [round(float(v), 1) for v in ltas_y[show]]}},
        }
        (out / "print.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result


# ---------------------------------------------------------------- self-test

def _pluck(sr, f0, dur, mute, rng):
    """A plucked string (Karplus–Strong), stopped by the hand at its end."""
    p = int(round(sr / f0))
    n = int(sr * (dur + 0.03))
    exc = signal.lfilter([0.5, 0.5], [1], rng.uniform(-1, 1, p))
    y = np.zeros(n + p + 1)
    y[p + 1:p + 1 + p] = exc
    rho = (0.93 if mute else 0.997) * 0.5
    for k in range(p + 1, len(y), p):
        z = min(k + p, len(y))
        y[k:z] += rho * (y[k - p:z - p] + y[k - p - 1:z - p - 1])
    y = y[p + 1:p + 1 + n]
    env = np.ones(n)
    stop = int(sr * dur)
    env[stop:] = np.linspace(1, 0, n - stop)
    if mute:
        y = signal.lfilter(*biquad("lowpass", 900, sr, -3.0103), y)
    return y * env


def riff(sr, seconds=12.0, seed=3):
    """Power chords on the low strings, chugs and open ones, and two rests to hear a room by."""
    rng = np.random.default_rng(seed)
    eighth = 60 / 132 / 2
    pattern = [(40, 1, 1)] * 4 + [(43, 0, 2), (45, 0, 2)] + [(40, 1, 1)] * 4 + [(50, 0, 1), (48, 0, 1), (47, 0, 2), (None, 0, 3)]
    out = np.zeros(int(sr * seconds))
    t = 0.2
    while t < seconds - 1.2:
        for root, mute, n8 in pattern:
            dur = n8 * eighth
            if t + dur > seconds - 0.5:
                break
            if root is not None:
                for m in (root, root + 7, root + 12):
                    y = _pluck(sr, 440 * 2 ** ((m - 69) / 12), dur, mute, rng) * (0.8 if mute else 1.0)
                    a = int(t * sr)
                    out[a:a + len(y)] += y[:len(out) - a]
            t += dur
    return (0.3 * out / np.abs(out).max()).astype(np.float32)


def selftest():
    app = Path(__file__).resolve().parent
    amps = Path.home() / ".local/share/spotdl-helper/amps"
    want = ["Jcm800", "deluxe", "5150 Stealth 100w Blue Mesa OS - jp_is_out_of_tune", "90sDualRec-FullRig-Red-Modn-Classic"]
    models = [{"id": f"{w}.nam", "name": w, "path": str(amps / f"{w}.nam")} for w in want]
    import shutil
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    if not all(Path(m["path"]).is_file() for m in models) or not Path(node).exists():
        say("SKIP chybí modely nebo Node")
        return 0
    sr = 48000
    tmp = Path(tempfile.mkdtemp(prefix="pick-tone-test-"))
    job = {"sr": sr, "node": node, "nam_js": str(app / "tone_nam.mjs"), "worklet": str(app / "static/nam/nam-worklet.js"),
           "wasm": str(app / "static/nam/nam-engine.wasm"), "procs": 3, "out": str(tmp / "out"), "models": models}
    try:
        d = riff(sr)
        truth = {"drive_db": 4.0, "hp": 110.0, "mid_db": 3.0, "tilt_db": -2.0}
        nam = Nam(job, 1)
        try:
            core = nam.render(models[0]["path"], [pre(d, truth, sr)])[0]
        finally:
            nam.close()
        y = core
        for b, a in (biquad("highpass", 80, sr, -3.0103), biquad("peaking", 120, sr, 1.0, 4.0),
                     biquad("lowpass", 5000, sr, -3.0103), biquad("lowpass", 5000, sr, -3.0103)):
            y = signal.lfilter(b, a, y)
        y = with_room(0.5 * y.astype(np.float32), {"mix": 0.25, "decay": 1.2}, sr)
        sf.write(str(tmp / "ref.wav"), np.stack([y, y], 1), sr, subtype="FLOAT")
        sf.write(str(tmp / "take.wav"), np.concatenate([np.zeros(int(sr * 0.012), np.float32), d[:-int(sr * 0.012)]]), sr, subtype="FLOAT")
        job.update(ref=str(tmp / "ref.wav"), takes=[{"path": str(tmp / "take.wav"), "offset": 0.0}])
        t0 = time.time()
        got = Fit(job).run()
        say(f"SELFTEST core={got['core'] and got['core']['name']} drive={got['drive_db']} pre={got['pre']} room={got['room']} "
            f"match={got['match']} loss={got['loss']} parts={got['parts']} lags={got['lags_ms']} in {time.time() - t0:.0f}s")
        say("SELFTEST ranking " + ", ".join(f"{x['name']}={x['loss']}" for x in got["ranking"]))
        ok = got["core"] and got["core"]["name"] == want[0] and abs(got["drive_db"] - truth["drive_db"]) <= 4.5 \
            and got["match"] >= 85 and got["room"] is not None and abs(got["lags_ms"][0] - 12) <= 12
        say("SELFTEST OK" if ok else "SELFTEST FAIL")
        return 0 if ok else 1
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    job = json.loads(Path(sys.argv[sys.argv.index("--job") + 1]).read_text(encoding="utf-8"))
    try:
        got = Fit(job).run()
    except Exception as e:                                   # the server shows the reason
        say(f"ERROR {str(e)[:300]}")
        return 1
    say(f"DONE {got['match']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
