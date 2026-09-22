const R = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const v = { min: 432, max: 445, default: 440 };
function I() {
  return typeof navigator < "u" && typeof navigator.mediaDevices?.getUserMedia == "function";
}
function O(n) {
  const t = typeof n == "object" && n !== null && "name" in n ? n.name : void 0;
  return t === "NotAllowedError" || t === "SecurityError" ? "permission-denied" : t === "NotFoundError" || t === "DevicesNotFoundError" ? "microphone-unavailable" : "start-failed";
}
function j(n, t = 440) {
  const e = Math.round(69 + 12 * Math.log2(n / t)), a = t * 2 ** ((e - 69) / 12), i = x(n, a), s = R[(e % 12 + 12) % 12], r = Math.floor(e / 12) - 1;
  return { frequency: n, note: s, octave: r, midi: e, targetFrequency: a, cents: i, clarity: 0, inputLevel: 0 };
}
function z(n) {
  return Number.isFinite(n) ? Math.min(v.max, Math.max(v.min, Math.round(n))) : v.default;
}
function x(n, t) {
  return 1200 * Math.log2(n / t);
}
class P {
  /** Referenční A. Mění se za běhu — ladění podle jiné reference je běžné. */
  referenceA = v.default;
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
    this.referenceA = z(t);
  }
  async start(t) {
    this.stop();
    const e = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: !1, noiseSuppression: !1, autoGainControl: !1 }
    }), a = new AudioContext(), i = a.createMediaStreamSource(e), s = a.createBiquadFilter(), r = a.createBiquadFilter(), l = a.createAnalyser();
    s.type = "highpass", s.frequency.value = 38, s.Q.value = 0.7, r.type = "lowpass", r.frequency.value = 1250, r.Q.value = 0.7, l.fftSize = 8192, l.smoothingTimeConstant = 0, i.connect(s), s.connect(r), r.connect(l), this.audioContext = a, this.analyser = l, this.inputBuffer = new Float32Array(l.fftSize), this.mediaStream = e, this.lastAnalysisTime = 0, this.lastPitch = null, this.missedFrames = 0, this.recentFrequencies = [], this.runAnalysis(t);
  }
  stop() {
    this.animationFrameId !== null && (window.cancelAnimationFrame(this.animationFrameId), this.animationFrameId = null), this.mediaStream?.getTracks().forEach((t) => t.stop()), this.mediaStream = null, this.audioContext?.close().catch(() => {
    }), this.audioContext = null, this.analyser = null, this.inputBuffer = null, this.lastAnalysisTime = 0, this.lastPitch = null, this.missedFrames = 0, this.recentFrequencies = [];
  }
  runAnalysis(t) {
    const e = (a) => {
      if (!(!this.analyser || !this.audioContext || !this.inputBuffer)) {
        if (a - this.lastAnalysisTime >= 90) {
          this.lastAnalysisTime = a, this.analyser.getFloatTimeDomainData(this.inputBuffer);
          const i = B(this.inputBuffer, this.audioContext.sampleRate);
          if (i) {
            this.missedFrames = 0;
            const s = this.correctOctaveJump(i.frequency), r = this.smoothFrequency(s), l = j(r, this.referenceA);
            this.lastPitch = { ...l, clarity: i.clarity, inputLevel: i.inputLevel }, t({ pitch: this.lastPitch, inputLevel: i.inputLevel });
          } else if (this.missedFrames += 1, this.lastPitch && this.missedFrames <= 3) {
            const s = F(this.inputBuffer);
            t({ pitch: { ...this.lastPitch, inputLevel: s }, inputLevel: s });
          } else
            this.lastPitch = null, this.recentFrequencies = [], t({ pitch: null, inputLevel: F(this.inputBuffer) });
        }
        this.animationFrameId = window.requestAnimationFrame(e);
      }
    };
    this.animationFrameId = window.requestAnimationFrame(e);
  }
  correctOctaveJump(t) {
    if (this.recentFrequencies.length === 0) return t;
    const e = $(this.recentFrequencies);
    if (Math.abs(x(t, e)) < 650) return t;
    const i = [t, t / 2, t * 2].filter(
      (s) => s >= 50 && s <= 900
    ).reduce((s, r) => Math.abs(x(r, e)) < Math.abs(x(s, e)) ? r : s, t);
    return Math.abs(x(i, e)) < 360 ? i : t;
  }
  smoothFrequency(t) {
    if (this.recentFrequencies.length > 0) {
      const e = $(this.recentFrequencies);
      if (Math.abs(x(t, e)) > 380)
        return this.recentFrequencies = [t], t;
    }
    return this.recentFrequencies = [...this.recentFrequencies, t].slice(-5), $(this.recentFrequencies);
  }
}
function B(n, t) {
  const e = F(n);
  if (e < 9e-3) return null;
  const a = Math.floor(t / 900), i = Math.min(Math.floor(t / 50), Math.floor(n.length / 2)), s = n.length - i, r = new Float32Array(i + 1);
  U(n);
  for (let o = 1; o <= i; o += 1) {
    let m = 0;
    for (let h = 0; h < s; h += 1) {
      const p = n[h] - n[h + o];
      m += p * p;
    }
    r[o] = m;
  }
  let l = 0;
  r[0] = 1;
  for (let o = 1; o <= i; o += 1)
    l += r[o], r[o] = l === 0 ? 1 : r[o] * o / l;
  let c = -1;
  for (let o = a; o <= i; o += 1)
    if (!(r[o] >= 0.14)) {
      for (; o + 1 <= i && r[o + 1] < r[o]; ) o += 1;
      c = o;
      break;
    }
  if (c < 0 && (c = G(r, a, i)), c < 0) return null;
  c = H(c, r, i, t);
  const g = q(c, r), f = 1 - r[c];
  return f < 0.82 ? null : { frequency: t / g, clarity: f, inputLevel: e };
}
function F(n) {
  let t = 0;
  for (let e = 0; e < n.length; e += 1) t += n[e] * n[e];
  return Math.sqrt(t / n.length);
}
function q(n, t) {
  const e = t[n - 1] ?? 0, a = t[n] ?? 0, i = t[n + 1] ?? 0, s = 2 * (2 * a - e - i);
  return s === 0 ? n : n + (i - e) / s;
}
function G(n, t, e) {
  let a = -1, i = Number.POSITIVE_INFINITY;
  for (let s = t; s <= e; s += 1)
    n[s] < i && (i = n[s], a = s);
  return 1 - i >= 0.82 ? a : -1;
}
function H(n, t, e, a) {
  if (a / n < 180) return n;
  const i = n * 2;
  return i > e ? n : t[i] + 0.018 < t[n] ? i : n;
}
function $(n) {
  const t = [...n].sort((a, i) => a - i), e = Math.floor(t.length / 2);
  return t.length % 2 === 0 ? (t[e - 1] + t[e]) / 2 : t[e];
}
function U(n) {
  let t = 0;
  for (let a = 0; a < n.length; a += 1) t += n[a];
  const e = t / n.length;
  if (!(Math.abs(e) < 1e-4))
    for (let a = 0; a < n.length; a += 1) n[a] -= e;
}
class Y {
  engine = null;
  referenceA = v.default;
  setReferenceA(t) {
    this.referenceA = z(t), this.engine?.setReferenceA(this.referenceA);
  }
  get bezi() {
    return this.engine !== null;
  }
  async start(t) {
    if (!I()) return { ok: !1, chyba: "unsupported" };
    this.stop();
    const e = new P();
    e.setReferenceA(this.referenceA);
    try {
      await e.start((a) => {
        const i = a.pitch;
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
    } catch (a) {
      return e.stop(), { ok: !1, chyba: O(a) };
    }
    return this.engine = e, { ok: !0 };
  }
  stop() {
    this.engine?.stop(), this.engine = null;
  }
}
const y = 50;
function E(n) {
  return Number.isFinite(n) ? Math.max(-y, Math.min(y, n)) / y * 90 : 0;
}
const S = 0.01;
function V({ soucasny: n, cil: t, dtMs: e, casovaKonstanta: a }) {
  if (e <= 0) return n;
  if (Math.abs(t - n) <= S) return t;
  const i = 1 - Math.exp(-e / a), s = n + (t - n) * i;
  return Math.abs(t - s) <= S ? t : s;
}
function Q({ posledni: n, kdy: t, ted: e, drzetMs: a }) {
  return n && e - t <= a ? n : null;
}
function A(n, t, e, a) {
  const i = (a - 90) * Math.PI / 180;
  return { x: n + e * Math.cos(i), y: t + e * Math.sin(i) };
}
function X(n, t, e, a, i) {
  const s = A(n, t, e, a), r = A(n, t, e, i);
  return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${e} ${e} 0 0 1 ${r.x.toFixed(2)} ${r.y.toFixed(2)}`;
}
const C = [
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
];
function M(n) {
  return C.find((t) => t.id === n) ?? C[0];
}
const D = {
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
};
function L(n) {
  const t = /^([A-H](?:#|b)?)(-?\d)$/.exec(n.trim());
  if (!t || D[t[1]] === void 0) return Number.NaN;
  const e = D[t[1]] + (Number(t[2]) + 1) * 12;
  return Math.round(440 * 2 ** ((e - 69) / 12) * 100) / 100;
}
function w(n, t) {
  return Math.round(n * t * 100 / 440) / 100;
}
function J(n, t, e) {
  let a = -1, i = 100;
  return t.forEach((s, r) => {
    const l = w(L(s), e), c = Math.abs(1200 * Math.log2(n / l));
    c < i && (i = c, a = r);
  }), a;
}
let b = null;
function Z() {
  return (!b || b.state === "closed") && (b = new AudioContext()), b.state === "suspended" && b.resume(), b;
}
function K(n, t = 2.5, e = 0.5) {
  if (!Number.isFinite(n) || n <= 0) return;
  const a = Z(), i = a.sampleRate, s = a.createBuffer(1, Math.ceil(i * t), i), r = s.getChannelData(0), l = Math.max(2, Math.round(i / n)), c = new Float32Array(l);
  for (let p = 0; p < l; p++) c[p] = Math.random() * 2 - 1;
  const g = 0.996 - Math.min(6e-3, n / 2e5);
  let f = 0;
  for (let p = 0; p < r.length; p++) {
    const N = (f + 1) % l, T = c[f];
    c[f] = g * 0.5 * (T + c[N]), r[p] = T, f = N;
  }
  const o = a.createBufferSource();
  o.buffer = s;
  const m = a.createGain(), h = a.currentTime;
  m.gain.setValueAtTime(e, h), m.gain.exponentialRampToValueAtTime(1e-4, h + t), o.connect(m).connect(a.destination), o.start(h), o.stop(h + t);
}
const u = 180, d = 176, k = 148, W = 150, tt = 1200, et = 120, nt = 4, at = [
  { od: -y, do: -20, barva: "var(--nl-chyba)" },
  { od: -20, do: -5, barva: "var(--nl-pozor)" },
  { od: -5, do: 5, barva: "var(--nl-signal)" },
  { od: 5, do: 20, barva: "var(--nl-pozor)" },
  { od: 20, do: y, barva: "var(--nl-chyba)" }
], _ = {
  "permission-denied": "Mikrofon je zakázaný. Povol ho v nastavení prohlížeče.",
  "microphone-unavailable": "Žádný mikrofon nenalezen.",
  unsupported: "Tady mikrofon nejde. Stránka musí běžet na https nebo na localhostu.",
  "start-failed": "Mikrofon se nepodařilo spustit."
};
function it() {
  const n = at.map(
    (a) => `<path d="${X(u, d, k, E(a.od), E(a.do))}" fill="none" style="stroke:${a.barva}" stroke-width="8" stroke-opacity="0.55"/>`
  ).join("");
  let t = "", e = "";
  for (let a = 0; a <= y; a++) {
    const i = -y + a * 2, s = i % 10 === 0, r = E(i), l = A(u, d, k - 12, r), c = A(u, d, k - (s ? 30 : 22), r), g = i === 0 ? "var(--nl-signal)" : "var(--nl-text)", f = i === 0 ? 1 : s ? 0.55 : 0.22, o = i === 0 ? 3 : s ? 2 : 1;
    if (t += `<line x1="${l.x.toFixed(2)}" y1="${l.y.toFixed(2)}" x2="${c.x.toFixed(2)}" y2="${c.y.toFixed(2)}" style="stroke:${g}" stroke-opacity="${f}" stroke-width="${o}" stroke-linecap="round"/>`, s) {
      const m = A(u, d, k - 46, r), h = i === 0 ? "0" : i > 0 ? `+${i}` : `${i}`;
      e += `<text x="${m.x.toFixed(2)}" y="${m.y.toFixed(2)}" class="popisek${i === 0 ? " nula" : ""}" text-anchor="middle" dominant-baseline="middle">${h}</text>`;
    }
  }
  return `
    <svg class="budik" viewBox="0 0 360 200" role="img" aria-label="Odchylka v centech">
      ${n}${t}${e}
      <g class="rucicka" transform="rotate(0 ${u} ${d})">
        <polygon points="${u - 5},${d} ${u},${d - k + 18} ${u + 5},${d}"/>
      </g>
      <circle cx="${u}" cy="${d}" r="11" class="naboj"/>
      <circle cx="${u}" cy="${d}" r="4" class="naboj-tecka"/>
    </svg>`;
}
const st = `
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
`;
class rt extends HTMLElement {
  static get observedAttributes() {
    return ["ladeni", "reference-a"];
  }
  detektor = new Y();
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
  constructor() {
    super();
    const t = this.attachShadow({ mode: "open" });
    t.innerHTML = `
      <style>${st}</style>
      <div class="ladicka">
        <div class="horni">
          <label class="reference">A
            <input type="range" min="${v.min}" max="${v.max}" step="1" aria-label="Referenční A">
            <output></output>
          </label>
          <select aria-label="Ladění">
            ${C.map((a) => `<option value="${a.id}">${a.nazev}</option>`).join("")}
          </select>
        </div>
        <div class="stav" aria-live="polite">Mikrofon vypnutý</div>
        ${it()}
        <div class="displej">
          <div class="ton"><span class="nota">--</span><span class="oktava"></span></div>
          <div class="odchylka">&nbsp;</div>
          <div class="frekvence">--- Hz</div>
        </div>
        <p class="chyba" role="alert"></p>
        <button class="mikrofon" type="button" aria-pressed="false">Spustit ladičku</button>
        <div class="struny"></div>
      </div>`;
    const e = (a) => t.querySelector(a);
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
      struny: e(".struny")
    }, this.$.ladeni.addEventListener("change", () => this.setAttribute("ladeni", this.$.ladeni.value)), this.$.reference.addEventListener("input", () => this.setAttribute("reference-a", this.$.reference.value)), this.$.mikrofon.addEventListener("click", () => this.posloucha ? this.stop() : void this.start()), this.$.struny.addEventListener("click", (a) => {
      const i = a.target.closest(".struna");
      i && K(Number(i.dataset.frekvence));
    });
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
    return z(t === null ? v.default : Number(t));
  }
  get ladeni() {
    return M(this.getAttribute("ladeni")).id;
  }
  /** Spustí poslech mikrofonu. Musí přijít z kliknutí — jinak ho prohlížeč nepustí. */
  async start() {
    this.$.chyba.textContent = "", this.detektor.setReferenceA(this.referenceA);
    const t = await this.detektor.start((e) => {
      e && (this.surovy = e, this.kdy = performance.now());
    });
    if (t.ok === !1) {
      const e = _[t.chyba] ?? _["start-failed"];
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
      const a = performance.now(), i = a - t;
      t = a;
      const s = Q({ posledni: this.surovy, kdy: this.kdy, ted: a, drzetMs: tt }), r = V({
        soucasny: this.uhel,
        cil: s ? E(s.cents) : 0,
        dtMs: i,
        casovaKonstanta: W
      });
      r !== this.uhel && (this.uhel = r, this.$.rucicka.setAttribute("transform", `rotate(${r.toFixed(2)} ${u} ${d})`)), a - this.prepis >= et && (this.prepis = a, this.ukaz(s)), this.snimek = requestAnimationFrame(e);
    };
    this.snimek = requestAnimationFrame(e);
  }
  ukaz(t) {
    const e = this.zobrazeny;
    if ((e === t || !!e && !!t && e.note === t.note && e.octave === t.octave && e.cents === t.cents && e.frequency === t.frequency) && !this.zastaraly) return;
    this.zobrazeny = t, this.zastaraly = !1;
    const i = !!t && Math.abs(t.cents) <= nt;
    this.toggleAttribute("data-ton", !!t), this.toggleAttribute("data-naladeno", i), this.$.nota.textContent = t ? t.note : "--", this.$.oktava.textContent = t ? String(t.octave) : "", this.$.odchylka.textContent = t ? i ? "Naladěno" : t.cents > 0 ? `+${t.cents} centů, povol` : `${t.cents} centů, přitáhni` : " ", this.$.frekvence.textContent = t ? `${t.frequency} Hz` : "--- Hz", this.posloucha && (this.$.stav.textContent = t ? "Přijímám signál" : "Poslouchám…", this.$.stav.toggleAttribute("data-signal", !!t));
    const s = M(this.ladeni).struny, r = t ? J(t.frequency, s, this.referenceA) : -1;
    this.$.struny.querySelectorAll(".struna").forEach((l, c) => l.toggleAttribute("data-hraje", c === r)), this.ohlas("ladicka-ton", t);
  }
  vykresliNastaveni() {
    if (!this.$) return;
    const t = M(this.getAttribute("ladeni")), e = this.referenceA;
    this.$.ladeni.value = t.id, this.$.reference.value = String(e), this.$.referenceHodnota.textContent = `${e} Hz`, this.detektor.setReferenceA(e);
    const a = t.struny.length;
    this.$.struny.innerHTML = t.struny.map((i, s) => {
      const r = w(L(i), e);
      return `<button class="struna" type="button" data-frekvence="${r}" aria-label="Zahrát ${i}, ${r} Hz">
        <span class="cislo">${a - s}. struna</span>
        <span class="jmeno">${i}</span>
        <span class="hz">${r} Hz</span>
      </button>`;
    }).join(""), this.zastaraly = !0, this.ukaz(this.zobrazeny);
  }
  ohlas(t, e) {
    this.dispatchEvent(new CustomEvent(t, { detail: e, bubbles: !0, composed: !0 }));
  }
}
function ot(n = "neverlast-ladicka") {
  customElements.get(n) || customElements.define(n, class extends rt {
  });
}
ot();
export {
  Y as Detektor,
  C as LADENI,
  rt as NeverlastLadicka,
  v as REFERENCE_A_RANGE,
  y as ROZSAH_CENTU,
  V as dalsiUhel,
  Q as drzenyTon,
  L as frekvenceTonu,
  w as naReferenci,
  M as najdiLadeni,
  J as strunaKTonu,
  E as uhelZCentu,
  K as zahrajTon,
  ot as zaregistrujLadicku
};
