const O = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const y = { min: 432, max: 445, default: 440 };
function B() {
  return typeof navigator < "u" && typeof navigator.mediaDevices?.getUserMedia == "function";
}
function V(a) {
  const t = typeof a == "object" && a !== null && "name" in a ? a.name : void 0;
  return t === "NotAllowedError" || t === "SecurityError" ? "permission-denied" : t === "NotFoundError" || t === "DevicesNotFoundError" ? "microphone-unavailable" : "start-failed";
}
function q(a, t = 440) {
  const e = Math.round(69 + 12 * Math.log2(a / t)), n = t * 2 ** ((e - 69) / 12), i = g(a, n), s = O[(e % 12 + 12) % 12], r = Math.floor(e / 12) - 1;
  return { frequency: a, note: s, octave: r, midi: e, targetFrequency: n, cents: i, clarity: 0, inputLevel: 0 };
}
function S(a) {
  return Number.isFinite(a) ? Math.min(y.max, Math.max(y.min, Math.round(a))) : y.default;
}
function g(a, t) {
  return 1200 * Math.log2(a / t);
}
class G {
  /** Referenční A. Mění se za běhu — ladění podle jiné reference je běžné. */
  referenceA = y.default;
  audioContext = null;
  analyser = null;
  animationFrameId = null;
  inputBuffer = null;
  lastAnalysisTime = 0;
  lastPitch = null;
  missedFrames = 0;
  mediaStream = null;
  recentFrequencies = [];
  setReferenceA(t) {
    this.referenceA = S(t);
  }
  async start(t) {
    this.stop();
    const e = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: !1, noiseSuppression: !1, autoGainControl: !1 }
    }), n = new AudioContext(), i = n.createMediaStreamSource(e);
    // Pick: each input of the interface is listened to on its own. Mixed
    // together, a hum on one input (a modeller's second USB channel) buried
    // the guitar on the other; apart, the one with the clear note is read.
    const split = n.createChannelSplitter(2), chain = (c) => {
      const s = n.createBiquadFilter(), r = n.createBiquadFilter(), l = n.createAnalyser();
      s.type = "highpass", s.frequency.value = 38, s.Q.value = 0.7, r.type = "lowpass", r.frequency.value = 1250, r.Q.value = 0.7, l.fftSize = 8192, l.smoothingTimeConstant = 0, split.connect(s, c), s.connect(r), r.connect(l);
      return l;
    };
    i.connect(split);
    const l = chain(0);
    this.analysers = [l, chain(1)], this.inputBuffers = this.analysers.map((a) => new Float32Array(a.fftSize));
    this.audioContext = n, this.analyser = l, this.inputBuffer = this.inputBuffers[0], this.mediaStream = e, this.lastAnalysisTime = 0, this.lastPitch = null, this.missedFrames = 0, this.recentFrequencies = [], this.runAnalysis(t);
  }
  stop() {
    this.animationFrameId !== null && (window.cancelAnimationFrame(this.animationFrameId), this.animationFrameId = null), this.mediaStream?.getTracks().forEach((t) => t.stop()), this.mediaStream = null, this.audioContext?.close().catch(() => {
    }), this.audioContext = null, this.analyser = null, this.analysers = [], this.inputBuffer = null, this.inputBuffers = [], this.lastAnalysisTime = 0, this.lastPitch = null, this.missedFrames = 0, this.recentFrequencies = [];
  }
  runAnalysis(t) {
    const e = (n) => {
      if (!(!this.analyser || !this.audioContext || !this.inputBuffer)) {
        if (n - this.lastAnalysisTime >= 90) {
          this.lastAnalysisTime = n;
          // the input with the clearest note (levels are taken before H reshapes the buffer)
          let i = null, lvl = 0;
          this.analysers.forEach((a, k) => {
            a.getFloatTimeDomainData(this.inputBuffers[k]);
            lvl = Math.max(lvl, F(this.inputBuffers[k]));
            const h = H(this.inputBuffers[k], this.audioContext.sampleRate);
            h && (!i || h.clarity > i.clarity) && (i = h);
          });
          if (i) {
            this.missedFrames = 0;
            const s = this.correctOctaveJump(i.frequency), r = this.smoothFrequency(s), l = q(r, this.referenceA);
            this.lastPitch = { ...l, clarity: i.clarity, inputLevel: i.inputLevel }, t({ pitch: this.lastPitch, inputLevel: i.inputLevel });
          } else if (this.missedFrames += 1, this.lastPitch && this.missedFrames <= 3) {
            const s = lvl;
            t({ pitch: { ...this.lastPitch, inputLevel: s }, inputLevel: s });
          } else
            this.lastPitch = null, this.recentFrequencies = [], t({ pitch: null, inputLevel: lvl });
        }
        this.animationFrameId = window.requestAnimationFrame(e);
      }
    };
    this.animationFrameId = window.requestAnimationFrame(e);
  }
  correctOctaveJump(t) {
    if (this.recentFrequencies.length === 0) return t;
    const e = N(this.recentFrequencies);
    if (Math.abs(g(t, e)) < 650) return t;
    const i = [t, t / 2, t * 2].filter(
      (s) => s >= 50 && s <= 900
    ).reduce((s, r) => Math.abs(g(r, e)) < Math.abs(g(s, e)) ? r : s, t);
    return Math.abs(g(i, e)) < 360 ? i : t;
  }
  smoothFrequency(t) {
    if (this.recentFrequencies.length > 0) {
      const e = N(this.recentFrequencies);
      if (Math.abs(g(t, e)) > 380)
        return this.recentFrequencies = [t], t;
    }
    return this.recentFrequencies = [...this.recentFrequencies, t].slice(-5), N(this.recentFrequencies);
  }
}
function H(a, t) {
  const e = F(a);
  // Pick: was 9e-3 (−41 dB) — a guitar's plain signal off a modeller's USB can sit far under that; the clarity test below keeps noise out.
  if (e < 5e-4) return null;
  const n = Math.floor(t / 900), i = Math.min(Math.floor(t / 50), Math.floor(a.length / 2)), s = a.length - i, r = new Float32Array(i + 1);
  Q(a);
  for (let o = 1; o <= i; o += 1) {
    let f = 0;
    for (let h = 0; h < s; h += 1) {
      const p = a[h] - a[h + o];
      f += p * p;
    }
    r[o] = f;
  }
  let l = 0;
  r[0] = 1;
  for (let o = 1; o <= i; o += 1)
    l += r[o], r[o] = l === 0 ? 1 : r[o] * o / l;
  let c = -1;
  for (let o = n; o <= i; o += 1)
    if (!(r[o] >= 0.14)) {
      for (; o + 1 <= i && r[o + 1] < r[o]; ) o += 1;
      c = o;
      break;
    }
  if (c < 0 && (c = Y(r, n, i)), c < 0) return null;
  // Pick: a dip still falling at the longest period looked at is a tone below the range (mains hum, rumble) — not a note.
  if (c >= i - 2) return null;
  c = X(c, r, i, t);
  const k = U(c, r), v = 1 - r[c];
  return v < 0.82 ? null : { frequency: t / k, clarity: v, inputLevel: e };
}
function F(a) {
  let t = 0;
  for (let e = 0; e < a.length; e += 1) t += a[e] * a[e];
  return Math.sqrt(t / a.length);
}
function U(a, t) {
  const e = t[a - 1] ?? 0, n = t[a] ?? 0, i = t[a + 1] ?? 0, s = 2 * (2 * n - e - i);
  return s === 0 ? a : a + (i - e) / s;
}
function Y(a, t, e) {
  let n = -1, i = Number.POSITIVE_INFINITY;
  for (let s = t; s <= e; s += 1)
    a[s] < i && (i = a[s], n = s);
  return 1 - i >= 0.82 ? n : -1;
}
function X(a, t, e, n) {
  if (n / a < 180) return a;
  const i = a * 2;
  return i > e ? a : t[i] + 0.018 < t[a] ? i : a;
}
function N(a) {
  const t = [...a].sort((n, i) => n - i), e = Math.floor(t.length / 2);
  return t.length % 2 === 0 ? (t[e - 1] + t[e]) / 2 : t[e];
}
function Q(a) {
  let t = 0;
  for (let n = 0; n < a.length; n += 1) t += a[n];
  const e = t / a.length;
  if (!(Math.abs(e) < 1e-4))
    for (let n = 0; n < a.length; n += 1) a[n] -= e;
}
class Z {
  engine = null;
  referenceA = y.default;
  setReferenceA(t) {
    this.referenceA = S(t), this.engine?.setReferenceA(this.referenceA);
  }
  get bezi() {
    return this.engine !== null;
  }
  async start(t) {
    if (!B()) return { ok: !1, chyba: "unsupported" };
    this.stop();
    const e = new G();
    e.setReferenceA(this.referenceA);
    try {
      await e.start((n) => {
        const i = n.pitch;
        if (!i) {
          t(null);
          return;
        }
        t({
          frequency: Math.round(i.frequency * 10) / 10,
          note: i.note,
          octave: i.octave,
          cents: Math.max(-50, Math.min(50, Math.round(i.cents))),
          clarity: i.clarity
        });
      });
    } catch (n) {
      return e.stop(), { ok: !1, chyba: V(n) };
    }
    return this.engine = e, { ok: !0 };
  }
  stop() {
    this.engine?.stop(), this.engine = null;
  }
}
const b = 50;
function E(a) {
  return Number.isFinite(a) ? Math.max(-b, Math.min(b, a)) / b * 90 : 0;
}
const w = 0.01;
function J({ soucasny: a, cil: t, dtMs: e, casovaKonstanta: n }) {
  if (e <= 0) return a;
  if (Math.abs(t - a) <= w) return t;
  const i = 1 - Math.exp(-e / n), s = a + (t - a) * i;
  return Math.abs(t - s) <= w ? t : s;
}
function K({ posledni: a, kdy: t, ted: e, drzetMs: n }) {
  return a && e - t <= n ? a : null;
}
function $(a, t, e, n) {
  const i = (n - 90) * Math.PI / 180;
  return { x: a + e * Math.cos(i), y: t + e * Math.sin(i) };
}
function W(a, t, e, n, i) {
  const s = $(a, t, e, n), r = $(a, t, e, i);
  return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${e} ${e} 0 0 1 ${r.x.toFixed(2)} ${r.y.toFixed(2)}`;
}
const T = [
  { id: "standard", nazev: "Standard E", struny: ["E2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "drop-d", nazev: "Drop D", struny: ["D2", "A2", "D3", "G3", "B3", "E4"] },
  { id: "eb", nazev: "Půltón dolů (Eb)", struny: ["Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"] },
  { id: "d", nazev: "Celý tón dolů (D)", struny: ["D2", "G2", "C3", "F3", "A3", "D4"] },
  { id: "drop-c", nazev: "Drop C", struny: ["C2", "G2", "C3", "F3", "A3", "D4"] },
  { id: "drop-b", nazev: "Drop B", struny: ["B1", "F#2", "B2", "E3", "G#3", "C#4"] },
  { id: "open-g", nazev: "Open G", struny: ["D2", "G2", "D3", "G3", "B3", "D4"] },
  { id: "open-d", nazev: "Open D", struny: ["D2", "A2", "D3", "F#3", "A3", "D4"] },
  { id: "dadgad", nazev: "DADGAD", struny: ["D2", "A2", "D3", "G3", "A3", "D4"] },
  { id: "baskytara", nazev: "Baskytara", struny: ["E1", "A1", "D2", "G2"] },
  { id: "ukulele", nazev: "Ukulele", struny: ["G4", "C4", "E4", "A4"] }
], m = "vlastni";
function z(a) {
  return T.find((t) => t.id === a) ?? T[0];
}
const M = {
  C: 0,
  "C#": 1,
  Db: 1,
  D: 2,
  "D#": 3,
  Eb: 3,
  E: 4,
  F: 5,
  "F#": 6,
  Gb: 6,
  G: 7,
  "G#": 8,
  Ab: 8,
  A: 9,
  "A#": 10,
  Bb: 10,
  B: 11,
  H: 11
}, tt = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
function L(a) {
  return /^[A-H](?:#|b)?-?\d$/.test(a.trim());
}
function P(a) {
  const t = /^([A-H](?:#|b)?)(-?\d)$/.exec(a.trim());
  if (!t || M[t[1]] === void 0) return Number.NaN;
  const e = M[t[1]] + (Number(t[2]) + 1) * 12;
  return Math.round(440 * 2 ** ((e - 69) / 12) * 100) / 100;
}
function et(a, t) {
  const e = /^([A-H](?:#|b)?)(-?\d)$/.exec(a.trim());
  if (!e || M[e[1]] === void 0) return a;
  const n = M[e[1]] + (Number(e[2]) + 1) * 12 + t, i = (n % 12 + 12) % 12, s = Math.floor(n / 12) - 1;
  return `${tt[i]}${s}`;
}
function j(a, t) {
  return Math.round(a * t * 100 / 440) / 100;
}
function nt(a, t, e) {
  let n = -1, i = 100;
  return t.forEach((s, r) => {
    const l = j(P(s), e), c = Math.abs(1200 * Math.log2(a / l));
    c < i && (i = c, n = r);
  }), n;
}
let x = null;
function at() {
  return (!x || x.state === "closed") && (x = new AudioContext()), x.state === "suspended" && x.resume(), x;
}
function it(a, t = 2.5, e = 0.5) {
  if (!Number.isFinite(a) || a <= 0) return;
  const n = at(), i = n.sampleRate, s = n.createBuffer(1, Math.ceil(i * t), i), r = s.getChannelData(0), l = Math.max(2, Math.round(i / a)), c = new Float32Array(l);
  for (let p = 0; p < l; p++) c[p] = Math.random() * 2 - 1;
  const k = 0.996 - Math.min(6e-3, a / 2e5);
  let v = 0;
  for (let p = 0; p < r.length; p++) {
    const D = (v + 1) % l, _ = c[v];
    c[v] = k * 0.5 * (_ + c[D]), r[p] = _, v = D;
  }
  const o = n.createBufferSource();
  o.buffer = s;
  const f = n.createGain(), h = n.currentTime;
  f.gain.setValueAtTime(e, h), f.gain.exponentialRampToValueAtTime(1e-4, h + t), o.connect(f).connect(n.destination), o.start(h), o.stop(h + t);
}
const u = 180, d = 176, A = 148, st = 150, rt = 1200, ot = 120, lt = 4, ct = [
  { od: -b, do: -20, barva: "var(--nl-chyba)" },
  { od: -20, do: -5, barva: "var(--nl-pozor)" },
  { od: -5, do: 5, barva: "var(--nl-signal)" },
  { od: 5, do: 20, barva: "var(--nl-pozor)" },
  { od: 20, do: b, barva: "var(--nl-chyba)" }
], R = {
  "permission-denied": "Mikrofon je zakázaný. Povol ho v nastavení prohlížeče.",
  "microphone-unavailable": "Žádný mikrofon nenalezen.",
  unsupported: "Tady mikrofon nejde. Stránka musí běžet na https nebo na localhostu.",
  "start-failed": "Mikrofon se nepodařilo spustit."
};
function ut() {
  const a = ct.map(
    (n) => `<path d="${W(u, d, A, E(n.od), E(n.do))}" fill="none" style="stroke:${n.barva}" stroke-width="8" stroke-opacity="0.55"/>`
  ).join("");
  let t = "", e = "";
  for (let n = 0; n <= b; n++) {
    const i = -b + n * 2, s = i % 10 === 0, r = E(i), l = $(u, d, A - 12, r), c = $(u, d, A - (s ? 30 : 22), r), k = i === 0 ? "var(--nl-signal)" : "var(--nl-text)", v = i === 0 ? 1 : s ? 0.55 : 0.22, o = i === 0 ? 3 : s ? 2 : 1;
    if (t += `<line x1="${l.x.toFixed(2)}" y1="${l.y.toFixed(2)}" x2="${c.x.toFixed(2)}" y2="${c.y.toFixed(2)}" style="stroke:${k}" stroke-opacity="${v}" stroke-width="${o}" stroke-linecap="round"/>`, s) {
      const f = $(u, d, A - 46, r), h = i === 0 ? "0" : i > 0 ? `+${i}` : `${i}`;
      e += `<text x="${f.x.toFixed(2)}" y="${f.y.toFixed(2)}" class="popisek${i === 0 ? " nula" : ""}" text-anchor="middle" dominant-baseline="middle">${h}</text>`;
    }
  }
  return `
    <svg class="budik" viewBox="0 0 360 200" role="img" aria-label="Odchylka v centech">
      ${a}${t}${e}
      <g class="rucicka" transform="rotate(0 ${u} ${d})">
        <polygon points="${u - 5},${d} ${u},${d - A + 18} ${u + 5},${d}"/>
      </g>
      <circle cx="${u}" cy="${d}" r="11" class="naboj"/>
      <circle cx="${u}" cy="${d}" r="4" class="naboj-tecka"/>
    </svg>`;
}
const dt = `
  :host {
    --nl-pozadi: transparent;
    --nl-plocha: #252329;
    --nl-vhloubeni: #0F0E11;
    --nl-text: #E9E2D0;
    --nl-tlum: #A39C8C;
    --nl-slaby: #948D7E;
    --nl-lampa: #FF9A2E;
    --nl-signal: #7FD46B;
    --nl-pozor: #F2B54A;
    --nl-chyba: #EF5B4F;
    --nl-kresba: rgba(233, 226, 208, 0.14);
    --nl-pismo: "Archivo", system-ui, -apple-system, "Segoe UI", sans-serif;
    --nl-pismo-cisla: "Saira Condensed", "Arial Narrow", system-ui, sans-serif;

    display: block;
    background: var(--nl-pozadi);
    color: var(--nl-text);
    font-family: var(--nl-pismo);
    font-size: 14px;
    line-height: 1.4;
  }
  :host([hidden]) { display: none; }
  * { box-sizing: border-box; }

  .ladicka { max-width: 560px; margin: 0 auto; display: grid; gap: 16px; }

  .horni { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px; }
  .reference { display: flex; align-items: center; gap: 8px; color: var(--nl-tlum); }
  .reference input { width: 110px; accent-color: var(--nl-lampa); }
  .reference output { min-width: 4.5em; font-family: var(--nl-pismo-cisla); font-size: 16px; color: var(--nl-text); font-variant-numeric: tabular-nums; }
  select {
    font: inherit; color: var(--nl-text); background: var(--nl-vhloubeni);
    border: 1px solid var(--nl-kresba); border-radius: 10px; padding: 6px 10px;
  }

  .stav { justify-self: end; color: var(--nl-slaby); font-size: 12px; }
  .stav[data-signal] { color: var(--nl-signal); }
  .chyba { color: var(--nl-chyba); font-size: 13px; text-align: center; }
  .chyba:empty { display: none; }

  .budik { width: 100%; max-width: 420px; display: block; margin: 0 auto; }
  .popisek { font-family: var(--nl-pismo-cisla); font-size: 12px; fill: var(--nl-text); fill-opacity: 0.45; }
  .popisek.nula { font-size: 14px; fill-opacity: 0.9; }
  .rucicka polygon { fill: var(--nl-text); fill-opacity: 0.25; }
  .naboj { fill: var(--nl-vhloubeni); stroke: var(--nl-text); stroke-opacity: 0.25; }
  .naboj-tecka { fill: var(--nl-text); fill-opacity: 0.3; }
  :host([data-ton]) .rucicka polygon, :host([data-ton]) .naboj-tecka { fill: var(--nl-lampa); fill-opacity: 1; }
  :host([data-naladeno]) .rucicka polygon, :host([data-naladeno]) .naboj-tecka { fill: var(--nl-signal); }

  /* Displej má pevnou výšku: když tón zmizí, stránka pod ním neposkočí. */
  .displej { height: 150px; display: grid; place-content: center; justify-items: center; gap: 8px; }
  .ton { display: flex; align-items: baseline; gap: 4px; font-family: var(--nl-pismo-cisla); font-variant-numeric: tabular-nums; line-height: 1; }
  .nota { font-size: 64px; font-weight: 700; color: color-mix(in srgb, var(--nl-text) 20%, transparent); }
  .oktava { font-size: 26px; font-weight: 600; color: color-mix(in srgb, var(--nl-text) 20%, transparent); }
  :host([data-ton]) .nota { color: var(--nl-text); }
  :host([data-ton]) .oktava { color: var(--nl-lampa); }
  :host([data-naladeno]) .nota { color: var(--nl-signal); }
  .odchylka {
    width: 14rem; text-align: center; padding: 3px 12px; border-radius: 8px; font-weight: 600; font-size: 13px;
    font-variant-numeric: tabular-nums; visibility: hidden;
    color: var(--nl-lampa); background: color-mix(in srgb, var(--nl-lampa) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--nl-lampa) 35%, transparent);
  }
  :host([data-ton]) .odchylka { visibility: visible; }
  :host([data-naladeno]) .odchylka {
    color: var(--nl-signal); background: color-mix(in srgb, var(--nl-signal) 12%, transparent);
    border-color: color-mix(in srgb, var(--nl-signal) 35%, transparent);
  }
  .frekvence { color: var(--nl-tlum); font-size: 12px; font-variant-numeric: tabular-nums; }

  .mikrofon {
    justify-self: center; font: inherit; font-weight: 700; cursor: pointer;
    padding: 10px 22px; border-radius: 12px; border: 0;
    background: var(--nl-lampa); color: #1B1A1D;
  }
  .mikrofon[aria-pressed="true"] { background: var(--nl-chyba); color: #fff; }

  .vlastni { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--nl-kresba); border-radius: 12px; }
  .vlastni[hidden] { display: none; }
  .vlastni-hlava { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .vlastni-pocet { display: flex; align-items: center; gap: 8px; color: var(--nl-tlum); font-size: 13px; }
  .vlastni-pocet input {
    width: 4em; font: inherit; color: var(--nl-text); background: var(--nl-vhloubeni);
    border: 1px solid var(--nl-kresba); border-radius: 8px; padding: 4px 6px; text-align: center;
  }
  .vlastni-struny { display: grid; grid-template-columns: repeat(auto-fit, minmax(70px, 1fr)); gap: 8px; }
  .vlastni-struna { display: grid; gap: 2px; justify-items: center; }
  .vlastni-struna .cislo { font-size: 11px; color: var(--nl-tlum); }
  .vlastni-struna input {
    width: 100%; font: inherit; font-family: var(--nl-pismo-cisla); font-size: 16px; font-weight: 700;
    color: var(--nl-text); background: var(--nl-vhloubeni); border: 1px solid var(--nl-kresba);
    border-radius: 8px; padding: 6px 4px; text-align: center; text-transform: none;
  }
  .vlastni-struna input:invalid, .vlastni-struna input[data-neplatny] { border-color: var(--nl-chyba); }
  .vlastni-akce { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
  .vlastni-chyba { color: var(--nl-chyba); font-size: 12px; flex: 1; min-width: 0; }
  .vlastni-pouzit {
    font: inherit; font-weight: 700; cursor: pointer; padding: 7px 16px; border-radius: 10px; border: 0;
    background: var(--nl-lampa); color: #1B1A1D;
  }

  .struny { display: grid; grid-template-columns: repeat(auto-fit, minmax(76px, 1fr)); gap: 8px; }
  .struna {
    font: inherit; color: var(--nl-text); cursor: pointer; text-align: center;
    background: var(--nl-plocha); border: 1px solid var(--nl-kresba); border-radius: 12px; padding: 10px 6px;
    display: grid; gap: 2px;
  }
  .struna:hover { border-color: color-mix(in srgb, var(--nl-text) 30%, transparent); }
  .struna[data-hraje] { border-color: var(--nl-signal); background: color-mix(in srgb, var(--nl-signal) 16%, var(--nl-plocha)); }
  .struna .cislo { font-size: 11px; color: var(--nl-tlum); }
  .struna .jmeno { font-family: var(--nl-pismo-cisla); font-size: 20px; font-weight: 700; }
  .struna .hz { font-size: 11px; color: var(--nl-tlum); font-variant-numeric: tabular-nums; }

  button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--nl-lampa); outline-offset: 2px; }
  @media (prefers-reduced-motion: reduce) { .struna { transition: none; } }
`, C = 4, I = 9;
class ht extends HTMLElement {
  static get observedAttributes() {
    return ["ladeni", "custom-struny", "reference-a"];
  }
  detektor = new Z();
  surovy = null;
  kdy = 0;
  uhel = 0;
  snimek = 0;
  prepis = 0;
  zobrazeny = null;
  /** Po změně ladění nebo reference se musí přepočítat zvýrazněná struna. */
  zastaraly = !0;
  posloucha = !1;
  $;
  /** Rozepsané, ještě nepotvrzené struny vlastního ladění — dokud se
   * nestisne „Použít“, `custom-struny` se nemění. */
  vlastniNavrh = [];
  /** Čárkou oddělené struny, jak byly naposledy vykresleny do editoru —
   * jen když se tohle liší od aktuálních, editor se přemaluje (viz níže). */
  vlastniPosledniCommit = "";
  constructor() {
    super();
    const t = this.attachShadow({ mode: "open" });
    t.innerHTML = `
      <style>${dt}</style>
      <div class="ladicka">
        <div class="horni">
          <label class="reference">A
            <input type="range" min="${y.min}" max="${y.max}" step="1" aria-label="Referenční A">
            <output></output>
          </label>
          <select aria-label="Ladění">
            ${T.map((n) => `<option value="${n.id}">${n.nazev}</option>`).join("")}
            <option value="${m}">Vlastní…</option>
          </select>
        </div>
        <div class="stav" aria-live="polite">Mikrofon vypnutý</div>
        ${ut()}
        <div class="displej">
          <div class="ton"><span class="nota">--</span><span class="oktava"></span></div>
          <div class="odchylka">&nbsp;</div>
          <div class="frekvence">--- Hz</div>
        </div>
        <p class="chyba" role="alert"></p>
        <button class="mikrofon" type="button" aria-pressed="false">Spustit ladičku</button>
        <!-- Right above the strings it edits — the editor decides exactly
             what shows up as buttons below it. -->
        <div class="vlastni" hidden>
          <div class="vlastni-hlava">
            <strong>Vlastní ladění</strong>
            <label class="vlastni-pocet">Počet strun
              <input type="number" min="${C}" max="${I}" step="1" aria-label="Počet strun vlastního ladění">
            </label>
          </div>
          <div class="vlastni-struny"></div>
          <div class="vlastni-akce">
            <span class="vlastni-chyba" role="alert"></span>
            <button class="vlastni-pouzit" type="button">Použít</button>
          </div>
        </div>
        <div class="struny"></div>
      </div>`;
    const e = (n) => t.querySelector(n);
    this.$ = {
      ladeni: e("select"),
      reference: e(".reference input"),
      referenceHodnota: e(".reference output"),
      stav: e(".stav"),
      chyba: e(".chyba"),
      rucicka: e(".rucicka"),
      nota: e(".nota"),
      oktava: e(".oktava"),
      odchylka: e(".odchylka"),
      frekvence: e(".frekvence"),
      mikrofon: e(".mikrofon"),
      struny: e(".struny"),
      vlastni: e(".vlastni"),
      vlastniPocet: e(".vlastni-pocet input"),
      vlastniStruny: e(".vlastni-struny"),
      vlastniChyba: e(".vlastni-chyba"),
      vlastniPouzit: e(".vlastni-pouzit")
    }, this.$.ladeni.addEventListener("change", () => this.setAttribute("ladeni", this.$.ladeni.value)), this.$.reference.addEventListener("input", () => this.setAttribute("reference-a", this.$.reference.value)), this.$.mikrofon.addEventListener("click", () => this.posloucha ? this.stop() : void this.start()), this.$.struny.addEventListener("click", (n) => {
      const i = n.target.closest(".struna");
      i && it(Number(i.dataset.frekvence));
    }), this.$.vlastniPocet.addEventListener("input", () => this.zmenVlastniPocet()), this.$.vlastniStruny.addEventListener("input", (n) => {
      const i = n.target.closest(".vlastni-struna")?.dataset.index;
      i !== void 0 && (this.vlastniNavrh[Number(i)] = n.target.value, n.target.removeAttribute("data-neplatny"), this.$.vlastniChyba.textContent = "");
    }), this.$.vlastniStruny.addEventListener("keydown", (n) => {
      n.key === "Enter" && this.pouzijVlastni();
    }), this.$.vlastniPouzit.addEventListener("click", () => this.pouzijVlastni());
  }
  connectedCallback() {
    this.vykresliNastaveni();
  }
  disconnectedCallback() {
    this.stop();
  }
  attributeChangedCallback() {
    this.vykresliNastaveni();
  }
  get referenceA() {
    const t = this.getAttribute("reference-a");
    return S(t === null ? y.default : Number(t));
  }
  get ladeni() {
    const t = this.getAttribute("ladeni");
    return t === m ? m : z(t).id;
  }
  /** Struny vlastního ladění, od nejhlubší. Bez platného `custom-struny` je
   * to standardní ladění — něco použitelného, dokud si uživatel nezadá svoje. */
  get customStruny() {
    const t = this.getAttribute("custom-struny");
    if (t) {
      const e = t.split(",").map((n) => n.trim()).filter(Boolean);
      if (e.length >= C && e.every(L)) return e;
    }
    return z("standard").struny.slice();
  }
  aktualniLadeni() {
    return this.ladeni === m ? { id: m, nazev: "Vlastní ladění", struny: this.customStruny } : z(this.ladeni);
  }
  /** Spustí poslech mikrofonu. Musí přijít z kliknutí — jinak ho prohlížeč nepustí. */
  async start() {
    this.$.chyba.textContent = "", this.detektor.setReferenceA(this.referenceA);
    const t = await this.detektor.start((e) => {
      e && (this.surovy = e, this.kdy = performance.now());
    });
    if (t.ok === !1) {
      const e = R[t.chyba] ?? R["start-failed"];
      this.$.chyba.textContent = e, this.ohlas("ladicka-stav", { posloucha: !1, chyba: e });
      return;
    }
    this.posloucha = !0, this.$.mikrofon.textContent = "Vypnout ladičku", this.$.mikrofon.setAttribute("aria-pressed", "true"), this.$.stav.textContent = "Poslouchám…", this.ohlas("ladicka-stav", { posloucha: !0 }), this.smycka();
  }
  /** Vypne mikrofon a vrátí ručičku na nulu. */
  stop() {
    cancelAnimationFrame(this.snimek), this.detektor.stop(), this.surovy = null;
    const t = this.posloucha;
    this.posloucha = !1, this.uhel = 0, this.$.rucicka.setAttribute("transform", `rotate(0 ${u} ${d})`), this.$.mikrofon.textContent = "Spustit ladičku", this.$.mikrofon.setAttribute("aria-pressed", "false"), this.$.stav.textContent = "Mikrofon vypnutý", this.$.stav.removeAttribute("data-signal"), this.ukaz(null), t && this.ohlas("ladicka-stav", { posloucha: !1 });
  }
  /**
   * Ručička se hýbe každý snímek, zapisuje se rovnou do SVG.
   * Čísla se přepisují jen občas — naladěná struna se v centech chvěje
   * pořád a blikající číslice se nedají číst.
   */
  smycka() {
    let t = performance.now();
    const e = () => {
      const n = performance.now(), i = n - t;
      t = n;
      const s = K({ posledni: this.surovy, kdy: this.kdy, ted: n, drzetMs: rt }), r = J({
        soucasny: this.uhel,
        cil: s ? E(s.cents) : 0,
        dtMs: i,
        casovaKonstanta: st
      });
      r !== this.uhel && (this.uhel = r, this.$.rucicka.setAttribute("transform", `rotate(${r.toFixed(2)} ${u} ${d})`)), n - this.prepis >= ot && (this.prepis = n, this.ukaz(s)), this.snimek = requestAnimationFrame(e);
    };
    this.snimek = requestAnimationFrame(e);
  }
  ukaz(t) {
    const e = this.zobrazeny;
    if ((e === t || !!e && !!t && e.note === t.note && e.octave === t.octave && e.cents === t.cents && e.frequency === t.frequency) && !this.zastaraly) return;
    this.zobrazeny = t, this.zastaraly = !1;
    const i = !!t && Math.abs(t.cents) <= lt;
    this.toggleAttribute("data-ton", !!t), this.toggleAttribute("data-naladeno", i), this.$.nota.textContent = t ? t.note : "--", this.$.oktava.textContent = t ? String(t.octave) : "", this.$.odchylka.textContent = t ? i ? "Naladěno" : t.cents > 0 ? `+${t.cents} centů, povol` : `${t.cents} centů, přitáhni` : " ", this.$.frekvence.textContent = t ? `${t.frequency} Hz` : "--- Hz", this.posloucha && (this.$.stav.textContent = t ? "Přijímám signál" : "Poslouchám…", this.$.stav.toggleAttribute("data-signal", !!t));
    const s = this.aktualniLadeni().struny, r = t ? nt(t.frequency, s, this.referenceA) : -1;
    this.$.struny.querySelectorAll(".struna").forEach((l, c) => l.toggleAttribute("data-hraje", c === r)), this.ohlas("ladicka-ton", t);
  }
  vykresliNastaveni() {
    if (!this.$) return;
    const t = this.aktualniLadeni(), e = this.referenceA;
    if (this.$.ladeni.value = t.id, this.$.reference.value = String(e), this.$.referenceHodnota.textContent = `${e} Hz`, this.detektor.setReferenceA(e), this.$.vlastni.hidden = t.id !== m, t.id === m) {
      const i = t.struny.join(",");
      i !== this.vlastniPosledniCommit && (this.vlastniPosledniCommit = i, this.vlastniNavrh = t.struny.slice(), this.$.vlastniPocet.value = String(this.vlastniNavrh.length), this.$.vlastniChyba.textContent = "", this.vykresliVlastniRadky());
    }
    const n = t.struny.length;
    this.$.struny.innerHTML = t.struny.map((i, s) => {
      const r = j(P(i), e);
      return `<button class="struna" type="button" data-frekvence="${r}" aria-label="Zahrát ${i}, ${r} Hz">
        <span class="cislo">${n - s}. struna</span>
        <span class="jmeno">${i}</span>
        <span class="hz">${r} Hz</span>
      </button>`;
    }).join(""), this.zastaraly = !0, this.ukaz(this.zobrazeny);
  }
  /** Vykreslí jeden vstup na strunu z `vlastniNavrh` — volá se jen, když se
   * mění jejich počet nebo přichází nové ladění, ne při každém úhozu. */
  vykresliVlastniRadky() {
    const t = this.vlastniNavrh.length;
    this.$.vlastniStruny.innerHTML = this.vlastniNavrh.map((e, n) => `
      <label class="vlastni-struna" data-index="${n}">
        <span class="cislo">${t - n}. struna</span>
        <input type="text" value="${e}" aria-label="${t - n}. struna, vědecký zápis tónu, např. E2">
      </label>`).join("");
  }
  /**
   * Počet strun v editoru se změnil. Nová struna se přidává dole, kvartu
   * pod tu dosavadní nejhlubší — přesně jak přibývá 7. a 8. struna na
   * skutečné kytaře; ubírá se odspodu stejně.
   */
  zmenVlastniPocet() {
    let t = Math.round(Number(this.$.vlastniPocet.value));
    for (Number.isFinite(t) || (t = this.vlastniNavrh.length), t = Math.max(C, Math.min(I, t)), this.$.vlastniPocet.value = String(t); this.vlastniNavrh.length < t; ) {
      const e = this.vlastniNavrh[0] ?? "E2";
      this.vlastniNavrh.unshift(et(e, -5));
    }
    for (; this.vlastniNavrh.length > t; )
      this.vlastniNavrh.shift();
    this.$.vlastniChyba.textContent = "", this.vykresliVlastniRadky();
  }
  /** Potvrdí editor: platné zápisy se zapíšou do `custom-struny` (a přepnou
   * `ladeni` na "vlastni", pokud tam ještě není); neplatné zápisy se zvýrazní
   * a nic se neuloží. */
  pouzijVlastni() {
    const t = [...this.$.vlastniStruny.querySelectorAll(".vlastni-struna input")], e = t.map((s) => s.value.trim()), n = e.length, i = [];
    if (t.forEach((s, r) => {
      const l = L(e[r]);
      s.toggleAttribute("data-neplatny", !l), l || i.push(r);
    }), i.length) {
      this.$.vlastniChyba.textContent = `Neplatný zápis tónu u struny ${i.map((s) => n - s).join(", ")} — zkus třeba E2, F#3 nebo Bb1.`;
      return;
    }
    this.$.vlastniChyba.textContent = "", this.vlastniNavrh = e, this.setAttribute("custom-struny", e.join(",")), this.getAttribute("ladeni") === m ? this.vykresliNastaveni() : this.setAttribute("ladeni", m);
  }
  ohlas(t, e) {
    this.dispatchEvent(new CustomEvent(t, { detail: e, bubbles: !0, composed: !0 }));
  }
}
function pt(a = "neverlast-ladicka") {
  customElements.get(a) || customElements.define(a, class extends ht {
  });
}
pt();
export {
  Z as Detektor,
  T as LADENI,
  ht as NeverlastLadicka,
  y as REFERENCE_A_RANGE,
  b as ROZSAH_CENTU,
  J as dalsiUhel,
  K as drzenyTon,
  P as frekvenceTonu,
  j as naReferenci,
  z as najdiLadeni,
  nt as strunaKTonu,
  E as uhelZCentu,
  it as zahrajTon,
  pt as zaregistrujLadicku
};
