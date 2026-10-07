"use strict";
// Guitar Performance Analyzer — the analysis engine.
//
// Pure functions over mono PCM (Float32Array at SR below). It runs in a Worker
// in the page and under Node for the tests; nothing here touches the DOM or
// the audio hardware. Every result that is a measurement carries a confidence,
// and anything that could not be measured is null — never a made-up number.
//
//   features()  → per-frame RMS, spectral flux, chroma
//   onsets()    → where notes start (peaks of the flux)
//   notes()     → onset, length, pitch (YIN) and confidence of each note
//   tempo()     → BPM from the autocorrelation of the flux, and its drift
//   key()       → the key the chroma sums to
//   align()     → reference time ↔ player time (DTW over chroma)
//   compare()   → per-note verdicts, issues, scores, sections to practise
//
// A later ML model slots in by replacing notes() or align(): both return
// plain data in the shapes documented below.

const SR = 22050, WIN = 2048, FWIN = 1024, HOP = 256;
const FRAME_S = HOP / SR;
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

const DEFAULTS = {
  // pitch, in cents
  excellent: 15, good: 35, slight: 70,
  // timing, in ms
  timingOk: 40, timingMajor: 90, matchWindow: 260,
  // duration: off by more than both of these to count
  durRel: 0.35, durMs: 80,
  // rhythm: relative error of the gap between two notes
  rhythmMinor: 0.2, rhythmMajor: 0.4,
  // tempo: relative drift of the local speed
  tempoMinor: 0.05, tempoMajor: 0.1,
  // pitch range searched (Hz) — set from the tuning's lowest string
  fmin: 70, fmax: 1200,
  // a note's pitch is trusted from this YIN confidence up
  minConf: 0.6,
  // …or, where YIN is unsure, from this confidence of the harmonic reading
  minHarmonicConf: 0.45,
  // an attack weaker than this share of the strongest one within half a
  // second is not a new note (raise it and soft legato notes are skipped,
  // lower it and string noise becomes notes)
  onsetFloor: 0.28,
  // …and it has to rise this far above the average flux around it
  onsetRise: 1.22,
  // judge pitch after taking out a constant offset between guitar and record
  followTuning: true,
};

const noteName = (midi) => midi == null ? null : NOTE_NAMES[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1);
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const median = (a) => { if(!a.length) return 0; const s = Array.from(a).sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const mean = (a) => a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0;

// ------------------------------------------------------------------ FFT
// Two window lengths. Attacks are read through a short one (FWIN, 46 ms):
// at speed, sixteenth notes are 90 ms apart, and a window as long as that
// smears each attack into the next. Harmony (chroma) needs the long one
// (WIN, 93 ms) to tell low notes apart, and is taken every other frame.
function makeFFT(n){
  const rev = new Uint16Array(n), bits = Math.log2(n);
  for(let i = 0; i < n; i++){ let r = 0; for(let b = 0; b < bits; b++) if(i & (1 << b)) r |= 1 << (bits - 1 - b); rev[i] = r; }
  const cos = new Float32Array(n / 2), sin = new Float32Array(n / 2), hann = new Float32Array(n);
  for(let i = 0; i < n / 2; i++){ cos[i] = Math.cos(2 * Math.PI * i / n); sin[i] = Math.sin(2 * Math.PI * i / n); }
  for(let i = 0; i < n; i++) hann[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1));
  const re = new Float32Array(n), im = new Float32Array(n);
  // Magnitudes of the n samples of `pcm` from `o`, Hann-windowed, into `out`.
  return function mag(pcm, o, out){
    for(let i = 0; i < n; i++){ re[rev[i]] = pcm[o + i] * hann[i]; im[i] = 0; }
    for(let size = 2; size <= n; size <<= 1){
      const half = size >> 1, step = n / size;
      for(let i = 0; i < n; i += size){
        for(let j = 0, k = 0; j < half; j++, k += step){
          const tr = re[i + j + half] * cos[k] + im[i + j + half] * sin[k];
          const ti = im[i + j + half] * cos[k] - re[i + j + half] * sin[k];
          re[i + j + half] = re[i + j] - tr; im[i + j + half] = im[i + j] - ti;
          re[i + j] += tr; im[i + j] += ti;
        }
      }
    }
    for(let i = 0; i <= n / 2; i++) out[i] = Math.hypot(re[i], im[i]);
  };
}
const _fftLong = makeFFT(WIN), _fftShort = makeFFT(FWIN);

// Which pitch class each bin of the long FFT feeds (-1: outside the range that carries notes).
const _binPc = new Int8Array(WIN / 2 + 1).fill(-1);
for(let k = 1; k <= WIN / 2; k++){
  const f = k * SR / WIN;
  if(f >= 65 && f <= 2100) _binPc[k] = ((Math.round(69 + 12 * Math.log2(f / 440)) % 12) + 12) % 12;
}
const FLUX_TOP = Math.floor(5000 * FWIN / SR);

// ------------------------------------------------------------- features
// → {nF, rms, flux, chroma (nF×12), peak} — one value per frame of HOP
// samples; frame f is centred on sample f·HOP + WIN/2.
function features(pcm, onProgress){
  const nF = Math.max(0, Math.floor((pcm.length - WIN) / HOP) + 1);
  const rms = new Float32Array(nF), flux = new Float32Array(nF), chroma = new Float32Array(nF * 12);
  const mag = new Float32Array(FWIN / 2 + 1), prev = new Float32Array(FWIN / 2 + 1), long = new Float32Array(WIN / 2 + 1);
  const off = (WIN - FWIN) / 2;
  let peak = 0;
  for(let i = 0; i < pcm.length; i++){ const a = Math.abs(pcm[i]); if(a > peak) peak = a; }
  for(let f = 0; f < nF; f++){
    const o = f * HOP;
    let e = 0;
    for(let i = 0; i < FWIN; i++){ const x = pcm[o + off + i]; e += x * x; }
    rms[f] = Math.sqrt(e / FWIN);
    _fftShort(pcm, o + off, mag);
    let fl = 0;
    for(let k = 1; k <= FLUX_TOP; k++){
      const cur = Math.log1p(100 * mag[k]);
      const d = cur - prev[k];
      if(d > 0) fl += d;
      prev[k] = cur;
    }
    flux[f] = f ? fl : 0;
    if(f % 2 === 0){
      _fftLong(pcm, o, long);
      for(let k = 1; k <= WIN / 2; k++){ const pc = _binPc[k]; if(pc >= 0) chroma[f * 12 + pc] += long[k] * long[k]; }
    } else for(let p = 0; p < 12; p++) chroma[f * 12 + p] = chroma[(f - 1) * 12 + p];
    if(onProgress && f % 800 === 0) onProgress(f / nF);
  }
  return {nF, rms, flux, chroma, peak};
}

// ---------------------------------------------------------------- onsets
// Frames where a note starts: local peaks of the flux that stand clear of
// the flux around them, in sound that is actually audible.
function onsets(ft, cfg = DEFAULTS){
  const {nF, flux, rms} = ft;
  let fmax = 0, rmax = 0;
  for(let i = 0; i < nF; i++){ if(flux[i] > fmax) fmax = flux[i]; if(rms[i] > rmax) rmax = rms[i]; }
  if(!fmax || rmax < 1e-4) return [];
  const out = [];
  const R = 16, NEAR = Math.round(0.5 / FRAME_S), GAP = Math.round(0.055 / FRAME_S);
  // "Audible" is judged against the loudness around a moment (two seconds
  // either side), not against the loudest moment of the whole recording: a
  // quiet passage of a long song is still music, and the same eight seconds
  // must give the same notes whether analysed alone or inside the song.
  const BL = 32, nb = Math.ceil(nF / BL), blk = new Float32Array(nb), span = Math.ceil(2 / FRAME_S / BL);
  for(let i = 0; i < nF; i++){ const b = (i / BL) | 0; if(rms[i] > blk[b]) blk[b] = rms[i]; }
  const loudNear = (i) => { const b = (i / BL) | 0; let m = 0; for(let k = Math.max(0, b - span); k <= Math.min(nb - 1, b + span); k++) if(blk[k] > m) m = blk[k]; return m; };
  for(let i = 3; i < nF - 3; i++){
    const v = flux[i];
    if(v < flux[i - 1] || v < flux[i + 1] || v < flux[i - 2] || v <= flux[i + 2] || v < flux[i - 3] || v <= flux[i + 3]) continue;
    // The peak has to stand above the flux around it. Measured against the
    // local mean, not a share of the loudest attack in the song: a distorted
    // guitar never falls silent between notes, its attacks are shallow
    // ripples on a high floor, and a global threshold misses all of them.
    let sumF = 0, cntF = 0;
    for(let j = Math.max(0, i - R); j <= Math.min(nF - 1, i + R); j++){ sumF += flux[j]; cntF++; }
    const thr = sumF / cntF * cfg.onsetRise;
    if(v < thr) continue;
    // A real attack is not dwarfed by the attacks next to it; the click of a
    // note being let go, or a string's rattle, is.
    let near = 0;
    for(let j = Math.max(0, i - NEAR); j <= Math.min(nF - 1, i + NEAR); j++) if(flux[j] > near) near = flux[j];
    if(v < near * cfg.onsetFloor) continue;
    const loud = Math.max(rms[i], rms[Math.min(nF - 1, i + 2)], rms[Math.min(nF - 1, i + 4)]);
    if(loud < Math.max(loudNear(i) * 0.06, 0.0012)) continue;
    if(out.length && i - out[out.length - 1] < GAP){
      if(v > flux[out[out.length - 1]]) out[out.length - 1] = i;
      continue;
    }
    out.push(i);
  }
  return out;
}

// ------------------------------------------------------------------- YIN
// The fundamental of the YIN_W samples at `start`, with 1 − (the YIN
// difference at the chosen lag) as the confidence. null when out of signal.
// (A longer window reads a single low note better but runs across the fast
// ones; where YIN is unsure, harmonicPitch() below takes over.)
const YIN_W = 1024;
function yin(pcm, start, fmin, fmax){
  const tauMax = Math.min(Math.floor(SR / fmin), 512), tauMin = Math.max(2, Math.floor(SR / fmax));
  if(start < 0) start = 0;
  if(start + YIN_W + tauMax >= pcm.length) return null;
  const d = new Float32Array(tauMax + 1);
  for(let tau = 1; tau <= tauMax; tau++){
    let s = 0;
    for(let j = 0; j < YIN_W; j++){ const x = pcm[start + j] - pcm[start + j + tau]; s += x * x; }
    d[tau] = s;
  }
  let run = 0;
  d[0] = 1;
  for(let tau = 1; tau <= tauMax; tau++){ run += d[tau]; d[tau] = run ? d[tau] * tau / run : 1; }
  let best = -1;
  for(let tau = tauMin; tau <= tauMax; tau++){
    if(d[tau] < 0.15){ while(tau + 1 <= tauMax && d[tau + 1] < d[tau]) tau++; best = tau; break; }
  }
  if(best < 0){ let m = Infinity; for(let tau = tauMin; tau <= tauMax; tau++) if(d[tau] < m){ m = d[tau]; best = tau; } }
  if(best <= 0) return null;
  let t = best;
  if(best > 1 && best < tauMax){
    const a = d[best - 1], b = d[best], c = d[best + 1], den = a - 2 * b + c;
    if(den) t = best + 0.5 * (a - c) / den;
  }
  return {f: SR / t, conf: clamp01(1 - d[best])};
}

// ------------------------------------------------------ harmonic pitch
// The pitch a spectrum's partials add up to: for every candidate
// fundamental (in 10-cent steps) the magnitudes at its first ten harmonics,
// the lower ones counting more. This reads what YIN cannot — a distorted
// guitar, a power chord — because it does not need the wave to repeat
// cleanly, only its partials to line up. It can land an octave off (the
// comparison forgives that); its confidence is how far the best candidate
// stands above the field.
const _hpMag = new Float32Array(WIN / 2 + 1);
function harmonicPitch(pcm, start, fmin){
  if(start < 0) start = 0;
  if(start + WIN >= pcm.length) return null;
  _fftLong(pcm, start, _hpMag);
  const binHz = SR / WIN, top = WIN / 2 - 2;
  const at = (f) => { const x = f / binHz, i = Math.floor(x); if(i < 1 || i >= top) return 0; const fr = x - i; return _hpMag[i] * (1 - fr) + _hpMag[i + 1] * fr; };
  const steps = Math.floor(1200 * Math.log2(700 / fmin) / 10);
  const sal = new Float32Array(steps + 1);
  let best = 0, bi = -1, sum = 0;
  for(let c = 0; c <= steps; c++){
    const f0 = fmin * Math.pow(2, c / 120);
    let v = 0, w = 1;
    for(let h = 1; h <= 10; h++){ const f = f0 * h; if(f > 5000) break; v += w * at(f); w *= 0.84; }
    sal[c] = v; sum += v;
    if(v > best){ best = v; bi = c; }
  }
  if(bi < 0 || !best) return null;
  let off = 0;
  if(bi > 0 && bi < steps){ const a = sal[bi - 1], b = sal[bi], c = sal[bi + 1], den = a - 2 * b + c; if(den) off = 0.5 * (a - c) / den; }
  const mean = sum / (steps + 1);
  return {f: fmin * Math.pow(2, (bi + off) / 120), conf: clamp01((best - mean) / best * 1.15 - 0.25)};
}

// ----------------------------------------------------------------- notes
// → [{t, dur, midi, cents, f, conf, level}] ; midi/cents/f are null when the
// pitch could not be read with confidence (chords, noise, a full mix).
function notes(pcm, ft, ons, cfg = DEFAULTS, onProgress){
  const out = [];
  for(let k = 0; k < ons.length; k++){
    const f0 = ons[k], limit = k + 1 < ons.length ? ons[k + 1] : ft.nF;
    let peak = 0;
    for(let f = f0; f < Math.min(limit, f0 + 16); f++) if(ft.rms[f] > peak) peak = ft.rms[f];
    let end = limit;
    for(let f = f0 + 4; f < limit; f++) if(ft.rms[f] < peak * 0.22){ end = f; break; }
    const fs = [], cs = [];
    let bestConf = 0;
    // The pitch is read from just after the attack, a few times along the note.
    for(let f = f0 + 1; f < Math.max(f0 + 2, Math.min(end, f0 + 26)); f += 3){
      const y = yin(pcm, f * HOP + WIN / 2, cfg.fmin, cfg.fmax);
      if(!y) continue;
      if(y.conf > bestConf) bestConf = y.conf;
      if(y.conf >= cfg.minConf){ fs.push(y.f); cs.push(y.conf); }
    }
    const note = {t: (f0 * HOP + WIN / 2) / SR, dur: Math.max(1, end - f0) * FRAME_S,
                  midi: null, cents: null, f: null, conf: bestConf, level: peak};
    if(fs.length){
      const f = median(fs), m = 69 + 12 * Math.log2(f / 440);
      note.f = f; note.midi = Math.round(m); note.cents = Math.round((m - note.midi) * 100); note.conf = median(cs);
    } else {
      // YIN was not sure: the partials may still say what the note is.
      const h = harmonicPitch(pcm, f0 * HOP + WIN / 2 - 200, cfg.fmin);
      if(h && h.conf >= cfg.minHarmonicConf){
        const m = 69 + 12 * Math.log2(h.f / 440);
        note.f = h.f; note.midi = Math.round(m); note.cents = Math.round((m - note.midi) * 100); note.conf = h.conf; note.harmonic = true;
      }
    }
    out.push(note);
    if(onProgress && k % 40 === 0) onProgress(k / ons.length);
  }
  return out;
}

// ----------------------------------------------------------------- tempo
function _acfAt(acf, lag){
  const i = Math.floor(lag), fr = lag - i;
  if(i + 1 >= acf.length) return 0;
  return acf[i] * (1 - fr) + acf[i + 1] * fr;
}
function _bpmOf(flux, from, to, lo = 55, hi = 215){
  const n = to - from;
  if(n < 64) return null;
  let m = 0;
  for(let i = from; i < to; i++) m += flux[i];
  m /= n;
  const maxLag = Math.min(n - 1, Math.ceil(4 * 60 / lo / FRAME_S) + 2);
  const acf = new Float32Array(maxLag + 1);
  for(let lag = 0; lag <= maxLag; lag++){
    let s = 0;
    for(let i = from; i + lag < to; i++) s += (flux[i] - m) * (flux[i + lag] - m);
    acf[lag] = s / (n - lag);
  }
  if(acf[0] <= 0) return null;
  let best = null, bestS = -Infinity;
  const scores = [];
  for(let bpm = lo; bpm <= hi; bpm += 0.25){
    const lag = 60 / bpm / FRAME_S;
    let s = 0;
    for(let h = 1; h <= 4; h++) s += _acfAt(acf, lag * h) / h;
    s *= Math.exp(-0.5 * Math.pow(Math.log2(bpm / 120) / 0.9, 2));     // most music sits near 120
    scores.push(s);
    if(s > bestS){ bestS = s; best = bpm; }
  }
  const med = median(scores);
  return {bpm: best, conf: clamp01((bestS - med) / (Math.abs(bestS) + 1e-9) * 0.9)};
}
// → {bpm, conf, curve: [{t, bpm, conf}]} or null when there is no pulse to find.
function tempo(ft){
  const g = _bpmOf(ft.flux, 0, ft.nF);
  if(!g) return null;
  const curve = [];
  const win = Math.round(10 / FRAME_S), hop = Math.round(2.5 / FRAME_S);
  for(let a = 0; a + win <= ft.nF; a += hop){
    const l = _bpmOf(ft.flux, a, a + win, g.bpm * 0.85, g.bpm * 1.15);
    if(l) curve.push({t: (a + win / 2) * FRAME_S, bpm: l.bpm, conf: l.conf});
  }
  return {bpm: g.bpm, conf: g.conf, curve};
}

// ------------------------------------------------------------------- key
const _KS_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const _KS_MIN = [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
function key(ft){
  const sum = new Float64Array(12);
  for(let f = 0; f < ft.nF; f++) for(let p = 0; p < 12; p++) sum[p] += ft.chroma[f * 12 + p];
  const tot = sum.reduce((a, b) => a + b, 0);
  if(!tot) return null;
  const corr = (prof, rot) => {
    let mx = 0, my = 0;
    for(let i = 0; i < 12; i++){ mx += sum[(i + rot) % 12]; my += prof[i]; }
    mx /= 12; my /= 12;
    let a = 0, b = 0, c = 0;
    for(let i = 0; i < 12; i++){ const x = sum[(i + rot) % 12] - mx, y = prof[i] - my; a += x * y; b += x * x; c += y * y; }
    return a / Math.sqrt(b * c || 1);
  };
  const all = [];
  for(let r = 0; r < 12; r++){ all.push({name: NOTE_NAMES[r] + " major", s: corr(_KS_MAJ, r)}); all.push({name: NOTE_NAMES[r] + " minor", s: corr(_KS_MIN, r)}); }
  all.sort((a, b) => b.s - a.s);
  return {name: all[0].name, conf: clamp01((all[0].s - all[1].s) * 4 + (all[0].s > 0.6 ? 0.3 : 0))};
}

// ------------------------------------------------------------- alignment
// What the two recordings are matched on, per `group` frames: the harmony
// (chroma, normalised) and the rhythm (how strong an attack falls there,
// against the attacks around it). Harmony alone cannot place a riff that
// chugs on one note — every moment of it sounds the same; its rhythm can.
function _alignSeq(ft, group){
  const n = Math.floor(ft.nF / group);
  const seq = new Float32Array(n * 12), quiet = new Uint8Array(n), hit = new Float32Array(n);
  let rmax = 0;
  for(let i = 0; i < ft.nF; i++) if(ft.rms[i] > rmax) rmax = ft.rms[i];
  for(let i = 0; i < n; i++){
    let e = 0, r = 0, fl = 0;
    for(let g = 0; g < group; g++){
      const f = i * group + g;
      r += ft.rms[f];
      if(ft.flux[f] > fl) fl = ft.flux[f];
      for(let p = 0; p < 12; p++) seq[i * 12 + p] += ft.chroma[f * 12 + p];
    }
    hit[i] = fl;
    for(let p = 0; p < 12; p++) e += seq[i * 12 + p] * seq[i * 12 + p];
    e = Math.sqrt(e);
    if(r / group < rmax * 0.03 || !e){ quiet[i] = 1; for(let p = 0; p < 12; p++) seq[i * 12 + p] = 0; }
    else for(let p = 0; p < 12; p++) seq[i * 12 + p] /= e;
  }
  // attack strength relative to the strongest within a second either side
  const span = Math.max(1, Math.round(1 / (group * FRAME_S))), rel = new Float32Array(n);
  for(let i = 0; i < n; i++){
    let m = 0;
    for(let j = Math.max(0, i - span); j <= Math.min(n - 1, i + span); j++) if(hit[j] > m) m = hit[j];
    rel[i] = m ? hit[i] / m : 0;
  }
  return {seq, quiet, hit: rel, n};
}

// Dynamic time warping of the performance against the reference. The
// performance is matched whole (its silent ends trimmed); the reference may
// be entered and left anywhere — a player who plays one riff of a song is
// matched to that riff, not stretched over the whole song.
// → {ref: [s], ply: [s], cost, from, to} — matching times, increasing in
// both, and the stretch of the reference that was played — or null.
function align(ftRef, ftPly, onProgress, opts){
  // opts.band (seconds): the performance was recorded while the reference
  // played, so the two run at the same clock — the path may only stray this
  // far from "same time". Without it the reference may be entered anywhere.
  const band = opts && opts.band;
  // opts.from / opts.to (seconds): only this stretch of the reference was
  // attempted — a section practised in a loop. The reference is cut to it
  // (with a little room either side) and the times put back afterwards.
  let shift = 0;
  if(opts && opts.from != null && opts.to != null){
    const a = Math.max(0, Math.floor((opts.from - 1) / FRAME_S)), b = Math.min(ftRef.nF, Math.ceil((opts.to + 1) / FRAME_S));
    if(b - a > 16){
      shift = a * FRAME_S;
      ftRef = {nF: b - a, rms: ftRef.rms.subarray(a, b), flux: ftRef.flux.subarray(a, b), chroma: ftRef.chroma.subarray(a * 12, b * 12)};
    }
  }
  // Where the take's first sample sits in the cut. The take is late by the
  // rig's latency (opts.lag, seconds): what is at its start was played that
  // much earlier in the reference.
  const zero = opts && opts.from != null ? opts.from - (opts.lag || 0) : 0;
  const lead = zero - shift;
  let group = 4;
  while(!band && (ftRef.nF / group) * (ftPly.nF / group) > 24e6) group++;
  const A = _alignSeq(ftRef, group), B = _alignSeq(ftPly, group);
  let b0 = 0, b1 = B.n - 1;
  while(b0 < B.n && B.quiet[b0]) b0++;
  while(b1 > b0 && B.quiet[b1]) b1--;
  const N = A.n, M = b1 - b0 + 1;
  if(N < 8 || M < 8) return null;
  const W = M + 1, STEP = 0.08;
  const bandF = band ? Math.max(4, Math.round(band / (group * FRAME_S))) : 0;
  const D = new Float32Array((N + 1) * W).fill(Infinity);
  const cost = (i, j) => {                                   // reference frame i−1 against performance frame j−1
    const jj = b0 + j - 1;
    if(A.quiet[i - 1] || B.quiet[jj]) return A.quiet[i - 1] && B.quiet[jj] ? 0.2 : 0.9;
    const ai = (i - 1) * 12, bj = jj * 12;
    let dot = 0;
    for(let p = 0; p < 12; p++) dot += A.seq[ai + p] * B.seq[bj + p];
    return (1 - dot) * 0.6 + Math.abs(A.hit[i - 1] - B.hit[jj]) * 0.4;
  };
  // The performance starts at its start, somewhere in the reference.
  const leadF = Math.round(lead / (group * FRAME_S));
  for(let i = 0; i <= N; i++) if(!band || Math.abs(i - leadF - b0) <= bandF) D[i * W] = 0;
  // Steps of (1,1), (2,1) and (1,2) only: the player may run at half to
  // twice the reference's speed, never stand still while it moves or the
  // other way round — a path free to do that collapses a whole riff onto
  // one moment of the song.
  for(let i = 1; i <= N; i++){
    let jLo = 1, jHi = M;
    if(band){ jLo = Math.max(1, i - 1 - leadF - b0 - bandF + 1); jHi = Math.min(M, i - 1 - leadF - b0 + bandF + 1); }
    for(let j = jLo; j <= jHi; j++){
      let m = D[(i - 1) * W + j - 1];
      if(i >= 2){ const v = D[(i - 2) * W + j - 1] + STEP; if(v < m) m = v; }
      if(j >= 2){ const v = D[(i - 1) * W + j - 2] + STEP; if(v < m) m = v; }
      if(m !== Infinity) D[i * W + j] = cost(i, j) + m;
    }
    if(onProgress && i % 200 === 0) onProgress(i / N);
  }
  // …and ends where that is cheapest.
  let iEnd = 0, best = Infinity;
  for(let i = 1; i <= N; i++) if(D[i * W + M] < best){ best = D[i * W + M]; iEnd = i; }
  if(!iEnd) return null;
  const sum = new Float64Array(N), cnt = new Uint32Array(N);
  let i = iEnd, j = M;
  while(i > 0 && j > 0){
    sum[i - 1] += j - 1; cnt[i - 1]++;
    const d = D[(i - 1) * W + j - 1];
    const u = i >= 2 ? Math.fround(D[(i - 2) * W + j - 1] + STEP) : Infinity;
    const l = j >= 2 ? Math.fround(D[(i - 1) * W + j - 2] + STEP) : Infinity;
    if(d <= u && d <= l){ i--; j--; } else if(u <= l){ i -= 2; j--; } else { i--; j -= 2; }
  }
  const step = group * FRAME_S, ref = [], ply = [];
  // Only frames with sound in them say where the music is: silence is
  // stretched freely by the path and would bend the line the timing is
  // measured against.
  for(let k = 0; k < N; k++){
    if(!cnt[k] || A.quiet[k]) continue;
    const jm = b0 + sum[k] / cnt[k];
    if(B.quiet[Math.min(B.n - 1, Math.round(jm))]) continue;
    ref.push((k + 0.5) * step + shift); ply.push((jm + 0.5) * step);
  }
  if(ref.length < 4) return null;
  return {ref, ply, cost: best / M, step, from: ref[0], to: ref[ref.length - 1],
          zero: opts && opts.from != null ? zero : null};       // reference time of the take's first sample, when known
}

// How late the take is against the reference as a whole: the shift at which
// the take's attacks best line up with the reference's (their onset
// envelopes, cross-correlated). `from` is the reference time the recording
// was started at. → {lag (s, positive = the take is late), confidence}. This is
// the rig's latency plus the player's own average earliness or lateness —
// the two cannot be told apart from one recording.
function estimateLag(ftRef, ftPly, from, maxLag = 0.35){
  const f0 = Math.round((from || 0) / FRAME_S), hi = Math.round(maxLag / FRAME_S), lo = -Math.round(0.08 / FRAME_S);
  const n = ftPly.nF;
  if(n < 40) return null;
  let mp = 0, mr = 0, cnt = 0;
  for(let i = 0; i < n; i++){ const j = f0 + i; if(j < 0 || j >= ftRef.nF) continue; mp += ftPly.flux[i]; mr += ftRef.flux[j]; cnt++; }
  if(cnt < 40) return null;
  mp /= cnt; mr /= cnt;
  const sc = [];
  let best = -Infinity, bi = 0;
  for(let L = lo; L <= hi; L++){
    let v = 0;
    for(let i = 0; i < n; i++){ const j = f0 + i - L; if(j < 0 || j >= ftRef.nF) continue; v += (ftPly.flux[i] - mp) * (ftRef.flux[j] - mr); }
    sc.push(v);
    if(v > best){ best = v; bi = L - lo; }
  }
  if(best <= 0) return null;
  let off = 0;
  if(bi > 0 && bi < sc.length - 1){ const a = sc[bi - 1], b = sc[bi], c = sc[bi + 1], den = a - 2 * b + c; if(den) off = 0.5 * (a - c) / den; }
  const sorted = sc.slice().sort((a, b) => a - b), med = sorted[sorted.length >> 1];
  return {lag: (bi + lo + off) * FRAME_S, confidence: clamp01((best - med) / (Math.abs(best) + 1e-9))};
}

// The time map read two ways. raw(): exactly where the path puts a reference
// time. smooth(): where the *phrase* is — a straight-line fit over ±`span`
// seconds — and how fast the player is going there (`rate`, 1 = as written).
// A note's timing error is its distance from the smooth line: the line
// follows the player's tempo, the note-to-note wobble is what is left.
function timeMap(al, span = 1.6){
  const {ref, ply} = al;
  const lower = (t) => { let lo = 0, hi = ref.length - 1; while(lo < hi){ const m = (lo + hi + 1) >> 1; if(ref[m] <= t) lo = m; else hi = m - 1; } return lo; };
  const raw = (t) => {
    if(t <= ref[0]) return ply[0] + (t - ref[0]);
    const n = ref.length - 1;
    if(t >= ref[n]) return ply[n] + (t - ref[n]);
    const i = lower(t), fr = (t - ref[i]) / (ref[i + 1] - ref[i] || 1);
    return ply[i] + (ply[i + 1] - ply[i]) * fr;
  };
  const smooth = (t) => {
    let a = lower(t - span), b = lower(t + span);
    if(b - a < 3){ a = Math.max(0, a - 2); b = Math.min(ref.length - 1, b + 2); }
    let sx = 0, sy = 0, sxx = 0, sxy = 0, n = 0;
    for(let k = a; k <= b; k++){ sx += ref[k]; sy += ply[k]; sxx += ref[k] * ref[k]; sxy += ref[k] * ply[k]; n++; }
    const den = n * sxx - sx * sx;
    if(n < 2 || Math.abs(den) < 1e-9) return {t: raw(t), rate: 1};
    const rate = (n * sxy - sx * sy) / den;
    return {t: (sy - rate * sx) / n + rate * t, rate: Math.max(0.25, Math.min(4, rate))};
  };
  // The other way: a player time back to the reference.
  const inverse = (tp) => {
    let lo = 0, hi = ply.length - 1;
    if(tp <= ply[0]) return ref[0] + (tp - ply[0]);
    if(tp >= ply[hi]) return ref[hi] + (tp - ply[hi]);
    while(lo < hi - 1){ const m = (lo + hi) >> 1; if(ply[m] <= tp) lo = m; else hi = m; }
    const fr = (tp - ply[lo]) / (ply[hi] - ply[lo] || 1);
    return ref[lo] + (ref[hi] - ref[lo]) * fr;
  };
  return {raw, smooth, inverse};
}

// ------------------------------------------------------------ comparison
function _pitchVerdict(absCents, cfg){
  if(absCents <= cfg.excellent) return {v: "EXCELLENT", score: 1, sev: 0};
  if(absCents <= cfg.good) return {v: "GOOD", score: 0.85, sev: 0};
  if(absCents <= cfg.slight) return {v: "SLIGHTLY OFF", score: 0.5, sev: 1};
  return {v: "WRONG NOTE", score: 0, sev: 2};
}

// ref/ply: {notes, tempo, dur}; al: the alignment. Everything in the result
// is derived from those — see the fields' own comments.
function compare(ref, ply, al, cfgIn){
  const cfg = {...DEFAULTS, ...(cfgIn || {})};
  const map = timeMap(al);
  // Only the stretch of the reference that was played is judged: what comes
  // before the player came in and after they stopped was not attempted, and
  // is not "missed".
  const covFrom = Math.max(0, al.from - 0.25), covTo = Math.min(ref.dur, al.to + 0.25);
  const rn = ref.notes.filter(n => n.t >= covFrom && n.t <= covTo), pn = ply.notes;
  const used = new Uint8Array(pn.length);
  const rows = [];                 // one per reference note, then the extras
  // Which played note answers which written one: the two lists aligned as
  // sequences (a note may be matched, missed, or extra), not each note to
  // its nearest neighbour — nearest-neighbour hands a missed note's slot to
  // the note next to it and calls that one wrong.
  const exp = rn.map(r => map.smooth(r.t));
  const win = cfg.matchWindow / 1000, SKIP = 0.75;
  const N = rn.length, M = pn.length, W = M + 1;
  const pair = new Int32Array(N).fill(-1);
  if(N && M && N * M <= 30e6){
    const D = new Float32Array((N + 1) * W);
    for(let j = 1; j <= M; j++) D[j] = j * SKIP;
    for(let i = 1; i <= N; i++){
      D[i * W] = i * SKIP;
      for(let j = 1; j <= M; j++){
        const dt = Math.abs(pn[j - 1].t - exp[i - 1].t);
        let m = Infinity;
        if(dt <= win){
          const a = rn[i - 1].midi, b = pn[j - 1].midi;
          const same = a != null && b != null && (b - a) % 12 === 0;
          m = D[(i - 1) * W + j - 1] + dt / win + (same ? 0 : (a == null || b == null ? 0.15 : 0.4));
        }
        D[i * W + j] = Math.min(m, D[(i - 1) * W + j] + SKIP, D[i * W + j - 1] + SKIP);
      }
    }
    // Back along the path. The table is 32-bit: the moves are compared as
    // 32-bit numbers too, or the walk takes a branch that was never taken.
    let i = N, j = M;
    while(i > 0 && j > 0){
      const v = D[i * W + j];
      const up = Math.fround(D[(i - 1) * W + j] + SKIP), left = Math.fround(D[i * W + j - 1] + SKIP);
      const dt = Math.abs(pn[j - 1].t - exp[i - 1].t);
      if(dt <= win && v < up && v < left){ pair[i - 1] = j - 1; i--; j--; }
      else if(up <= left) i--;
      else j--;
    }
  }
  for(let i = 0; i < rn.length; i++){
    const r = rn[i], ex = exp[i], best = pair[i];
    const row = {i, kind: "note", t: r.t, expected: {...r, name: noteName(r.midi)}, played: null,
                 expectedAt: ex.t, rate: ex.rate, pitchCents: null, pitch: null, timingMs: null, timing: null,
                 durMs: null, duration: null, confidence: r.conf, lowConfidence: false, octave: false};
    if(best < 0){ row.kind = "missed"; rows.push(row); continue; }
    used[best] = 1;
    const q = pn[best];
    row.played = {...q, name: noteName(q.midi)};
    row.confidence = Math.min(r.conf, q.conf);
    row.timingMs = Math.round((q.t - ex.t) * 1000);
    row.timing = Math.abs(row.timingMs) <= cfg.timingOk ? "ON TIME" : row.timingMs > 0 ? "LATE" : "EARLY";
    if(r.midi != null && q.midi != null) row.rawCents = (q.midi - r.midi) * 100 + (q.cents - r.cents);
    else row.lowConfidence = true;
    const want = r.dur * ex.rate * 1000, got = q.dur * 1000;
    row.durMs = Math.round(got - want);
    row.duration = Math.abs(got - want) > Math.max(cfg.durMs, want * cfg.durRel) ? (got < want ? "SHORTER" : "LONGER") : "OK";
    rows.push(row);
  }
  // ---- the whole guitar against the whole recording: a constant offset
  // between them is a matter of tuning (the record a half step up, the
  // guitar a little flat), not of every note being wrong. It is measured —
  // the median of the note-by-note differences, octaves folded away — and,
  // when the notes agree on it, taken out before each note is judged.
  const fold = (c) => c - Math.round(c / 1200) * 1200;
  const diffs = rows.filter(r => r.rawCents != null).map(r => fold(r.rawCents));
  const precise = rows.filter(r => r.rawCents != null && !r.expected.harmonic && !r.played.harmonic).map(r => fold(r.rawCents));
  const pool = precise.length >= 8 ? precise : diffs;
  let tuning = {offsetCents: null, applied: false, pairs: pool.length, agree: null};
  if(pool.length >= 8){
    const med = median(pool), agree = pool.filter(c => Math.abs(c - med) <= 60).length / pool.length;
    tuning = {offsetCents: Math.round(med), applied: cfg.followTuning && Math.abs(med) >= 20 && agree >= 0.55, pairs: pool.length, agree};
  }
  const shift = tuning.applied ? tuning.offsetCents : 0;
  rows.forEach(row => {
    if(row.rawCents == null) return;
    let cents = row.rawCents - shift;
    // The classic miss of a pitch reader is the octave; a clean octave apart
    // is the same note read an octave off, and is reported as such.
    const oct = Math.round(cents / 1200);
    if(oct !== 0 && Math.abs(cents - oct * 1200) <= cfg.slight){ row.octave = true; cents -= oct * 1200; }
    // A pitch read from the partials is good to the note, not to the cent:
    // such a pair is judged as the same note or a different one, no finer.
    row.coarse = !!(row.expected.harmonic || row.played.harmonic);
    if(row.coarse){
      const same = Math.abs(cents) < 80;
      row.pitchCents = same ? null : Math.round(cents / 100) * 100;
      row.pitch = same ? {v: "CORRECT NOTE", score: 1, sev: 0} : {v: "WRONG NOTE", score: 0, sev: 2};
    } else {
      row.pitchCents = Math.round(cents);
      row.pitch = _pitchVerdict(Math.abs(cents), cfg);
    }
  });

  const extras = [];
  for(let k = 0; k < pn.length; k++) if(!used[k]){
    extras.push({kind: "extra", t: map.inverse(pn[k].t), expected: null, played: {...pn[k], name: noteName(pn[k].midi)},
                 confidence: pn[k].conf, playedAt: pn[k].t});
  }

  // ---- rhythm: the gap to the previous matched note, against the gap written
  const matched = rows.filter(r => r.kind === "note");
  for(let k = 1; k < matched.length; k++){
    const a = matched[k - 1], b = matched[k];
    if(b.i !== a.i + 1) continue;                 // a missed note between them: not comparable
    const want = (b.expected.t - a.expected.t) * b.rate, got = b.played.t - a.played.t;
    if(want > 0.03) b.rhythm = (got - want) / want;
  }

  // ---- issues: what is worth a marker on the timeline
  const issues = [];
  const push = (type, row, sev, text, extra) => issues.push({id: issues.length, type, t: row.t, sev, text,
    confidence: row.confidence, row: rows.indexOf(row), ...(extra || {})});
  rows.forEach(r => {
    if(r.kind === "missed"){ push("MISSED", r, 2, `Missed ${r.expected.name || "note"}`); return; }
    if(r.pitch && r.pitch.sev){
      // With a tuning offset taken out, "instead of" is told in the
      // reference's terms: the note the player's would be at its pitch.
      const playedAs = shift && r.played.midi != null ? noteName(Math.round(r.played.midi - shift / 100)) : r.played.name;
      push("PITCH", r, r.pitch.sev, r.pitch.sev === 2
        ? `Wrong note — ${playedAs} instead of ${r.expected.name}` : `${r.expected.name} ${r.pitchCents > 0 ? "+" : ""}${r.pitchCents} cents`);
    }
    if(r.timing !== "ON TIME") push("TIMING", r, Math.abs(r.timingMs) > cfg.timingMajor ? 2 : 1,
      `${r.timingMs > 0 ? "+" : ""}${r.timingMs} ms ${r.timing.toLowerCase()}`);
    if(r.rhythm != null && Math.abs(r.rhythm) > cfg.rhythmMinor) push("RHYTHM", r, Math.abs(r.rhythm) > cfg.rhythmMajor ? 2 : 1,
      `Gap ${r.rhythm > 0 ? "longer" : "shorter"} by ${Math.round(Math.abs(r.rhythm) * 100)} %`);
    if(r.duration !== "OK") push("DURATION", r, Math.abs(r.durMs) > 200 ? 2 : 1, `${r.durMs > 0 ? "+" : ""}${r.durMs} ms ${r.duration.toLowerCase()}`);
  });
  extras.forEach(x => issues.push({id: issues.length, type: "EXTRA", t: x.t, sev: 1, text: `Extra ${x.played.name || "note"}`,
    confidence: x.confidence, extra: extras.indexOf(x)}));
  // tempo: stretches where the player runs away from the reference
  const tempoCurve = [];
  for(let t = covFrom + 1; t < covTo - 0.75; t += 1){
    const s = map.smooth(t);
    tempoCurve.push({t, rate: 1 / s.rate, bpm: ref.tempo ? ref.tempo.bpm / s.rate : null});
  }
  let run = null;
  tempoCurve.forEach(pt => {
    const dev = pt.rate - 1, big = Math.abs(dev) > cfg.tempoMinor;
    if(big && !run) run = {from: pt.t, dev, n: 1};
    else if(big && run){ run.n++; if(Math.abs(dev) > Math.abs(run.dev)) run.dev = dev; }
    if((!big || pt === tempoCurve[tempoCurve.length - 1]) && run){
      if(run.n >= 2) issues.push({id: issues.length, type: "TEMPO", t: run.from, sev: Math.abs(run.dev) > cfg.tempoMajor ? 2 : 1,
        text: `${run.dev > 0 ? "Rushing" : "Dragging"} — ${run.dev > 0 ? "+" : ""}${Math.round(run.dev * 100)} % tempo`, confidence: 1 - clamp01(al.cost)});
      run = null;
    }
  });
  issues.sort((a, b) => a.t - b.t).forEach((x, k) => x.id = k);

  // ---- scores: each from what was measured; null when there is too little
  const enough = (a) => a.length >= 4;
  const pitched = rows.filter(r => r.kind !== "extra" && r.expected.midi != null && (r.kind === "missed" || r.pitch));
  const sPitch = enough(pitched) ? mean(pitched.map(r => r.kind === "missed" ? 0 : r.pitch.score)) : null;
  const sTiming = enough(matched) ? mean(matched.map(r => clamp01(1 - Math.max(0, Math.abs(r.timingMs) - cfg.timingOk / 2) / 200))) : null;
  const rh = matched.filter(r => r.rhythm != null);
  const sRhythm = enough(rh) ? mean(rh.map(r => clamp01(1 - Math.abs(r.rhythm) / 0.6))) : null;
  const sDur = enough(matched) ? mean(matched.map(r => clamp01(1 - Math.abs(r.durMs) / Math.max(150, r.expected.dur * r.rate * 1000)))) : null;
  const sTempo = tempoCurve.length >= 2 ? mean(tempoCurve.map(p => clamp01(1 - Math.abs(p.rate - 1) / 0.2))) : null;
  // Notes missed or added cost the whole performance, not one category.
  const coverage = rn.length ? matched.length / (rn.length + extras.length * 0.5) : null;
  const parts = [[sPitch, 0.3], [sTiming, 0.25], [sRhythm, 0.2], [sTempo, 0.1], [sDur, 0.15]].filter(p => p[0] != null);
  let overall = parts.length ? parts.reduce((s, p) => s + p[0] * p[1], 0) / parts.reduce((s, p) => s + p[1], 0) : null;
  if(overall != null && coverage != null) overall *= clamp01(0.4 + 0.6 * coverage);
  const pct = (x) => x == null ? null : Math.round(x * 100);
  const scores = {overall: pct(overall), pitch: pct(sPitch), timing: pct(sTiming), rhythm: pct(sRhythm), tempo: pct(sTempo),
                  duration: pct(sDur), articulation: null};

  // ---- heat map: per stretch of time, the worst thing in each category
  const B = 48, span = (covTo - covFrom) / B, bucket = (t) => Math.max(0, Math.min(B - 1, Math.floor((t - covFrom) / span)));
  const heat = {};
  ["PITCH", "TIMING", "RHYTHM", "TEMPO", "DURATION"].forEach(c => heat[c] = new Array(B).fill(null));
  rows.forEach(r => { if(r.kind === "extra") return; const b = bucket(r.t);
    ["PITCH", "TIMING", "RHYTHM", "DURATION"].forEach(c => { if(heat[c][b] == null) heat[c][b] = 0; }); });
  tempoCurve.forEach(pt => { const b = bucket(pt.t); if(heat.TEMPO[b] == null) heat.TEMPO[b] = 0; });
  issues.forEach(x => { const c = x.type === "MISSED" || x.type === "EXTRA" ? "PITCH" : x.type; const b = bucket(x.t);
    if(heat[c]) heat[c][b] = Math.max(heat[c][b] || 0, x.sev); });
  heat.ARTICULATION = new Array(B).fill(null);          // not analysed

  // ---- sections to practise: ten-second stretches, worst first
  const sections = [];
  const SEC = 10;
  for(let a = Math.floor(covFrom / SEC) * SEC; a < covTo; a += SEC){
    const inside = issues.filter(x => x.t >= a && x.t < a + SEC);
    const rs = matched.filter(r => r.t >= a && r.t < a + SEC);
    if(!inside.length) continue;
    const wrong = inside.filter(x => x.type === "PITCH" && x.sev === 2).length;
    const missedN = inside.filter(x => x.type === "MISSED").length;
    const tim = rs.length ? Math.round(mean(rs.map(r => clamp01(1 - Math.max(0, Math.abs(r.timingMs) - cfg.timingOk / 2) / 200))) * 100) : null;
    sections.push({from: Math.max(a, covFrom), to: Math.min(covTo, a + SEC), penalty: inside.reduce((s, x) => s + x.sev, 0),
                   wrong, missed: missedN, timing: tim, issues: inside.length,
                   worst: ["PITCH", "MISSED", "TIMING", "RHYTHM", "DURATION", "TEMPO", "EXTRA"]
                     .map(t => [t, inside.filter(x => x.type === t).reduce((s, x) => s + x.sev, 0)]).sort((x, y) => y[1] - x[1])[0][0]});
  }
  sections.sort((a, b) => b.penalty - a.penalty);

  const playerBpm = ref.tempo && tempoCurve.length ? ref.tempo.bpm * median(tempoCurve.map(p => p.rate)) : null;
  // Each note's timing above is against the phrase around it. With the
  // take's start known (recorded on the reference's clock, latency taken
  // out) the whole take can also be placed against the reference: how far
  // ahead or behind the player sits on average.
  let bias = null;
  if(al.zero != null && matched.length >= 4){
    const d = matched.map(r => (r.played.t - (r.expected.t - al.zero)) * 1000);
    bias = {ms: Math.round(median(d)), notes: d.length};
  }
  return {rows, extras, issues, scores, heat, heatSpan: span,
          // the stretch of the reference that was played, and how much of the whole that is
          coverage: {from: covFrom, to: covTo, share: ref.dur ? (covTo - covFrom) / ref.dur : null},
          timingBias: bias,
          // the constant pitch offset between the guitar and the recording
          tuning, sections: sections.slice(0, 6), tempoCurve,
          counts: {reference: rn.length, played: pn.length, matched: matched.length, missed: rows.length - matched.length, extra: extras.length,
                   wrong: issues.filter(x => x.type === "PITCH" && x.sev === 2).length,
                   lowConfidence: rows.filter(r => r.lowConfidence).length},
          tempo: {reference: ref.tempo ? ref.tempo.bpm : null, player: playerBpm,
                  playerMeasured: ply.tempo ? ply.tempo.bpm : null,
                  deviation: playerBpm != null ? playerBpm - ref.tempo.bpm : null},
          alignment: {cost: al.cost, confidence: clamp01(1 - al.cost * 1.9)},
          cfg};
}

// One recording, analysed: everything the comparison needs, plus a small
// waveform (min/max per column) to draw.
function analyze(pcm, cfgIn, onProgress){
  const cfg = {...DEFAULTS, ...(cfgIn || {})};
  const P = (a, b) => onProgress ? (x) => onProgress(a + (b - a) * x) : null;
  const ft = features(pcm, P(0, 0.45));
  const ons = onsets(ft, cfg);
  const ns = notes(pcm, ft, ons, cfg, P(0.45, 0.9));
  const tp = tempo(ft);
  if(onProgress) onProgress(0.97);
  const cols = 2000, per = Math.max(1, Math.floor(pcm.length / cols));
  const wave = new Float32Array(cols * 2);
  for(let c = 0; c < cols; c++){
    let lo = 0, hi = 0;
    for(let i = c * per; i < Math.min(pcm.length, (c + 1) * per); i++){ const x = pcm[i]; if(x < lo) lo = x; if(x > hi) hi = x; }
    wave[c * 2] = lo; wave[c * 2 + 1] = hi;
  }
  const pitched = ns.filter(n => n.midi != null);
  return {ft, notes: ns, tempo: tp, key: key(ft), dur: pcm.length / SR, wave, peak: ft.peak,
          // how much of what was found could be read as single notes — low for
          // chords and for a full mix, where the note-level verdicts are weak
          pitchedShare: ns.length ? pitched.length / ns.length : null,
          meanConfidence: pitched.length ? mean(pitched.map(n => n.conf)) : null};
}

// ------------------------------------------------------------ demo audio
// A plucked-string riff synthesised from a note list — the demo's reference
// and (with mistakes written in) its performance. Real audio, so the demo's
// analysis is the engine's own work, not prepared numbers.
function synth(list, dur){
  const pcm = new Float32Array(Math.ceil(dur * SR));
  list.forEach(n => {
    const f = 440 * Math.pow(2, (n.midi - 69 + (n.cents || 0) / 100) / 12), s0 = Math.floor(n.t * SR), len = Math.floor(n.dur * SR);
    for(let i = 0; i < len + 2000 && s0 + i < pcm.length; i++){
      const t = i / SR, rel = i > len ? Math.exp(-(i - len) / 300) : 1;
      const env = Math.min(1, i / 40) * Math.exp(-t * 2.2) * rel;
      pcm[s0 + i] += env * 0.32 * (Math.sin(2 * Math.PI * f * t) + 0.5 * Math.sin(4 * Math.PI * f * t) + 0.25 * Math.sin(6 * Math.PI * f * t));
    }
  });
  return pcm;
}
function demoPair(){
  const beat = 60 / 112, riff = [40, 43, 45, 43, 47, 45, 43, 40, 45, 47, 48, 47, 45, 43, 45, 40];
  const ref = [];
  for(let bar = 0; bar < 4; bar++) riff.forEach((m, k) => ref.push({midi: m + (bar === 2 ? 5 : 0), t: 0.5 + (bar * 16 + k) * beat / 2, dur: beat / 2 * 0.85}));
  // The performance: starts late, speeds up in the last bar, and makes the
  // mistakes a player makes.
  const ply = [];
  ref.forEach((n, k) => {
    let t = n.t + 0.35;
    if(k >= 48) t -= (k - 48) * beat / 2 * 0.07;            // rushing the last bar
    const o = {midi: n.midi, t, dur: n.dur};
    if(k === 5) o.midi -= 1;                                 // wrong note
    if(k === 11) return;                                     // missed
    if(k === 18) o.t += 0.13;                                // late
    if(k === 19) o.t += 0.1;
    if(k === 26) o.t -= 0.09;                                // early
    if(k === 30) o.dur *= 0.35;                              // cut short
    if(k === 37) o.cents = 45;                               // out of tune
    ply.push(o);
    if(k === 41) ply.push({midi: n.midi + 7, t: t + beat / 4, dur: 0.1});   // an extra note
  });
  const dur = ref[ref.length - 1].t + 1.5;
  return {ref: synth(ref, dur), ply: synth(ply, dur + 0.5), written: {ref, ply}};
}

const API = {SR, WIN, HOP, DEFAULTS, NOTE_NAMES, noteName, features, onsets, yin, harmonicPitch, estimateLag, notes, tempo, key, align, timeMap, compare, analyze, synth, demoPair};

if(typeof module !== "undefined" && module.exports) module.exports = API;
else if(typeof self !== "undefined" && typeof window === "undefined"){
  // Worker: analysed recordings are kept here by id, so the features are not
  // copied back and forth; the page gets everything but `ft`.
  const store = new Map();
  const slim = (a) => { const {ft, ...rest} = a; return rest; };
  self.onmessage = (e) => {
    const m = e.data, prog = (x) => self.postMessage({id: m.id, progress: x});
    try{
      if(m.cmd === "analyze"){
        const a = analyze(m.pcm, m.cfg, prog);
        store.set(m.key, a);
        self.postMessage({id: m.id, result: slim(a)});
      } else if(m.cmd === "compare"){
        const r = store.get(m.ref), p = store.get(m.ply);
        if(!r || !p) throw new Error("missing analysis");
        const al = align(r.ft, p.ft, (x) => prog(x * 0.9), m.band || m.range ? {band: m.band, ...(m.range || {})} : null);
        if(!al) throw new Error("too short to align");
        self.postMessage({id: m.id, result: {...compare(r, p, al, m.cfg), map: {ref: al.ref, ply: al.ply}}});
      } else if(m.cmd === "lag"){
        const r = store.get(m.ref);
        if(!r) throw new Error("missing analysis");
        self.postMessage({id: m.id, result: estimateLag(r.ft, features(m.pcm), m.from)});
      } else if(m.cmd === "demo"){
        const d = demoPair();
        self.postMessage({id: m.id, result: {ref: d.ref, ply: d.ply}}, [d.ref.buffer, d.ply.buffer]);
      } else if(m.cmd === "forget"){ store.delete(m.key); self.postMessage({id: m.id, result: true}); }
    }catch(err){ self.postMessage({id: m.id, error: String(err && err.message || err)}); }
  };
} else if(typeof window !== "undefined") window.AnalyzerEngine = API;
