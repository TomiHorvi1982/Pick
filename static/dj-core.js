// Pick DJ — the arithmetic behind the decks, kept free of the DOM and of Web
// Audio so node can test it (tests/js/dj_core_check.js).
//
// Positions are in seconds of the song (the stems' own time). The beat grid
// is the server's (/api/stems/{name}/beats, beat_this on the drums): a list of
// beat times that may drift in tempo, so beat positions are interpolated
// between neighbours and extrapolated past the ends at the nearest local BPM.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DJCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The six Demucs stems in the order the decks show them.
  const STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"];
  const STEM_TITLES = {vocals: "Vokály", drums: "Bicí", bass: "Basa", guitar: "Kytara", piano: "Klavír", other: "Ostatní"};

  // Presets: which stems stay up.
  const PRESETS = {
    acapella: ["vocals"],
    instrumental: ["drums", "bass", "guitar", "piano", "other"],
    beat: ["drums", "bass"],
    all: STEMS,
  };

  /** Songs the DJ can load now: separated, with at least one Demucs stem, no recorded takes only. */
  function playable(rows) {
    return (rows || []).filter(r => r && r.complete && Array.isArray(r.stems)
      && r.stems.some(s => STEMS.includes(s)));
  }

  const MAX_SECONDS = 20 * 60;   // a deck holds all six stems in memory

  /**
   * The DJ's library: every separated song (Steems) plus every song in Songs
   * that is not separated yet (those are separated while they play). One row
   * per song; a song in both lists is the separated one.
   */
  function libraryItems(stemRows, songEntries) {
    const items = [], seen = new Set();
    for (const r of playable(stemRows)) {
      const m = r.meta || {};
      items.push({key: "s:" + r.name, title: m.title || r.name, artist: m.artist || "", duration: r.duration || 0,
        stems: r.name, live: false, tooLong: (r.duration || 0) > MAX_SECONDS});
      seen.add(r.name);
    }
    for (const e of songEntries || []) {
      if (!e || e.type !== "file") continue;
      const st = e.stems || {};
      if (st.state === "ready" && seen.has(st.name)) continue;
      const ready = st.state === "ready" && st.name;
      items.push({key: ready ? "s:" + st.name : "f:" + e.rel, title: e.title || String(e.name || "").replace(/\.[^.]+$/, ""),
        artist: e.artist || "", duration: e.duration || 0, stems: ready ? st.name : null, rel: e.rel, live: !ready,
        tooLong: (e.duration || 0) > MAX_SECONDS});
      if (ready) seen.add(st.name);
    }
    return items;
  }

  /** The library on the PC (/g/songs rows: name, stems, title, artist, duration) as DJ items. */
  function fromLibrarySongs(rows) {
    const items = libraryItems((rows || []).map(r => ({name: r.name, complete: true, stems: r.stems || [], duration: r.duration || 0,
      meta: {title: r.title, artist: r.artist}})), []);
    const byName = new Map((rows || []).map(r => [r.name, r]));
    items.forEach(x => { const r = byName.get(x.stems) || {}; x.stemList = r.stems || null; x.tuning = r.tuning || null; x.album = r.album || ""; x.source = "stems"; });
    return items;
  }

  /** Which window of a song separated live to fetch next: the first missing one from the playhead on, then from the start. */
  function nextWindow(ready, pending, pos, windowSeconds) {
    const n = ready.length;
    const here = Math.max(0, Math.min(n - 1, Math.floor(pos / windowSeconds)));
    for (let i = 0; i < n; i++) {
      const k = (here + i) % n;
      if (!ready[k] && !pending.has(k)) return k;
    }
    return -1;
  }

  /** The stems of a song in deck order (only the Demucs ones). */
  function orderStems(stems) {
    return STEMS.filter(s => (stems || []).includes(s));
  }

  function upperBound(a, t) {           // first index with a[i] > t
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= t) lo = m + 1; else hi = m; }
    return lo;
  }

  /** Fractional beat index at time t (0 = first beat). Null without a grid. */
  function beatAt(beats, t) {
    const n = beats ? beats.length : 0;
    if (n < 2) return null;
    if (t <= beats[0]) return (t - beats[0]) / (beats[1] - beats[0]);
    if (t >= beats[n - 1]) return n - 1 + (t - beats[n - 1]) / (beats[n - 1] - beats[n - 2]);
    const i = upperBound(beats, t) - 1;
    return i + (t - beats[i]) / (beats[i + 1] - beats[i]);
  }

  /** Time of fractional beat index b (inverse of beatAt). */
  function timeOfBeat(beats, b) {
    const n = beats ? beats.length : 0;
    if (n < 2) return null;
    if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
    if (b >= n - 1) return beats[n - 1] + (b - (n - 1)) * (beats[n - 1] - beats[n - 2]);
    const i = Math.floor(b);
    return beats[i] + (b - i) * (beats[i + 1] - beats[i]);
  }

  /** Seconds per beat around t (median of the nearest intervals, robust to one odd beat). */
  function beatLength(beats, t) {
    const n = beats ? beats.length : 0;
    if (n < 2) return null;
    const i = Math.min(n - 2, Math.max(0, upperBound(beats, t) - 1));
    const d = [];
    for (let k = Math.max(0, i - 2); k <= Math.min(n - 2, i + 2); k++) d.push(beats[k + 1] - beats[k]);
    d.sort((a, b) => a - b);
    return d[d.length >> 1];
  }

  function localBpm(beats, t) {
    const l = beatLength(beats, t);
    return l ? 60 / l : null;
  }

  /** Nearest beat time to t (for quantised cues and loops). */
  function nearestBeat(beats, t) {
    const b = beatAt(beats, t);
    return b === null ? t : timeOfBeat(beats, Math.round(b));
  }

  /** A loop of `count` beats starting at the beat nearest t (or at t without a grid, using fallbackBeat seconds). */
  function loopAt(beats, t, count, fallbackBeat) {
    const b = beatAt(beats, t);
    if (b === null) {
      const len = (fallbackBeat || 0.5) * count;
      return {start: t, end: t + len};
    }
    const startBeat = count >= 1 ? Math.floor(b + 1e-6) : Math.floor(b / count + 1e-6) * count;
    return {start: timeOfBeat(beats, startBeat), end: timeOfBeat(beats, startBeat + count)};
  }

  /**
   * SYNC: the rate for the follower so its local tempo matches the leader's
   * heard tempo (folded by octaves: half/double time), and how far (seconds
   * of the follower's song) to move it so the beats line up. Phase is matched within the beat (±half a beat), so a
   * sync never jumps more than half a beat.
   */
  function syncTo(leader, follower) {
    const lb = localBpm(leader.beats, leader.pos), fb = localBpm(follower.beats, follower.pos);
    if (!lb || !fb) return null;
    // Half and double time are the same groove (a 65 BPM ballad grid against
    // a 130 BPM song): count the leader's beats at the follower's density.
    let ratio = lb * leader.rate / fb, k = 1;
    while (ratio < 0.7) { ratio *= 2; k *= 2; }
    while (ratio > 1.4) { ratio /= 2; k /= 2; }
    const rate = clamp(ratio, 0.5, 2);
    const la = beatAt(leader.beats, leader.pos) * k, fa = beatAt(follower.beats, follower.pos);
    const lf = la - Math.floor(la), ff = fa - Math.floor(fa);
    let d = lf - ff;
    if (d > 0.5) d -= 1;
    if (d < -0.5) d += 1;
    const shift = d * beatLength(follower.beats, follower.pos);
    return {rate, shift, phaseError: d, factor: k};
  }

  /** DJ crossfader: both full in the middle, cosine fade towards the far side. x in -1 (A) … 1 (B). */
  function crossfade(x) {
    x = clamp(x, -1, 1);
    return {a: x <= 0 ? 1 : Math.cos(x * Math.PI / 2), b: x >= 0 ? 1 : Math.cos(-x * Math.PI / 2)};
  }

  /** EQ knob 0…1 to gain in dB: 0.5 = flat, 0 = kill (-40 dB), 1 = +6 dB. */
  function eqDb(v) {
    v = clamp(v, 0, 1);
    if (v >= 0.5) return (v - 0.5) * 12;
    if (v < 0.02) return -40;
    return -26 * (0.5 - v) / 0.48;
  }

  /** Filter knob -1…1 to a filter: <0 low-pass sweeping down, >0 high-pass sweeping up, ~0 off. */
  function filterFor(v) {
    v = clamp(v, -1, 1);
    if (Math.abs(v) < 0.03) return {type: "off"};
    const a = (Math.abs(v) - 0.03) / 0.97;
    if (v < 0) return {type: "lowpass", hz: 20000 * Math.pow(60 / 20000, a)};
    return {type: "highpass", hz: 20 * Math.pow(8000 / 20, a)};
  }

  /** Tempo fader -1…1 and range (0.08, 0.16, 0.5) to a playback rate. */
  function rateFor(fader, range) {
    return 1 + clamp(fader, -1, 1) * range;
  }

  /** Semitones that cancel the pitch change of playing at `rate` (key lock). */
  function keyLockSemitones(rate) {
    return -12 * Math.log2(rate);
  }

  function formatTime(sec) {
    if (!isFinite(sec)) return "--:--";
    const neg = sec < 0;
    sec = Math.abs(sec);
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60), t = Math.floor((sec * 10) % 10);
    return (neg ? "-" : "") + m + ":" + String(s).padStart(2, "0") + "." + t;
  }

  /** Overview waveform: peak per bin of the summed stems, from interleaved-free Int16 channel data. */
  function peaks(channels, length, bins) {
    const out = new Float32Array(bins);
    const step = length / bins;
    for (let b = 0; b < bins; b++) {
      const from = Math.floor(b * step), to = Math.min(length, Math.floor((b + 1) * step));
      let m = 0;
      for (let i = from; i < to; i += 8) {
        let s = 0;
        for (const ch of channels) s += ch[i];
        const v = Math.abs(s) / 32768;
        if (v > m) m = v;
      }
      out[b] = m;
    }
    let max = 0;
    for (const v of out) if (v > max) max = v;
    if (max > 0) for (let b = 0; b < bins; b++) out[b] /= max;
    return out;
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  return {STEMS, STEM_TITLES, PRESETS, MAX_SECONDS, playable, libraryItems, fromLibrarySongs, nextWindow, orderStems, beatAt, timeOfBeat, beatLength, localBpm,
    nearestBeat, loopAt, syncTo, crossfade, eqDb, filterFor, rateFor, keyLockSemitones, formatTime, peaks, clamp};
});
