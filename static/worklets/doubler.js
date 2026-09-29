// The "second guitarist" of the doubler: a copy of the input read back out of
// a delay line whose length wanders like a human player's timing does.
// Modelled on NeuralDouble (MIT, github.com/ggibson1988-cyber/NeuralDouble):
//
//  - two independent, velocity-based random walks (timing drift and a faster
//    micro one whose speed is the clone's pitch wobble — a delay that changes
//    at rate r reads back at pitch 1 - r), each kicked at seed-unique random
//    intervals,
//  - a transient follower: every new note re-kicks the timing walk, so the
//    clone does not trail by the same amount on every attack,
//  - an optional gate on the clone (the negative half of Gate/Room).
//
// Output is the mono clone only; decorrelation (all-pass), the mono crossover,
// room and placement are native nodes around it (see makeDoubler).
class DoublerVoice extends AudioWorkletProcessor {
  static get parameterDescriptors(){
    return [
      {name: "smoothing", defaultValue: 0.5, minValue: 0, maxValue: 1, automationRate: "k-rate"},
      {name: "gate", defaultValue: 0, minValue: 0, maxValue: 1, automationRate: "k-rate"},
      {name: "acoustic", defaultValue: 0, minValue: 0, maxValue: 1, automationRate: "k-rate"},
    ];
  }

  constructor(options){
    super();
    const seed = options && options.processorOptions && options.processorOptions.seed;
    this.rng = (seed >>> 0) || 0x9e3779b9;
    this.size = 1 << 15;
    this.buf = new Float32Array(this.size);
    this.w = 0;
    this.fast = 0; this.slow = 0; this.level = 0;
    this.gateGain = 1;
    this.holdoff = 0;
    this.walkT = {pos: 0, vel: 0, timer: 0};   // timing drift (ms)
    this.walkP = {pos: 0, vel: 0, timer: 0};   // micro drift = pitch wobble (ms)
    this.delayMs = 22;
  }

  rand(){
    let x = this.rng;
    x ^= x << 13; x ^= x >>> 17; x ^= x << 5;
    this.rng = x >>> 0;
    return this.rng / 4294967296;
  }
  gauss(){ return (this.rand() + this.rand() + this.rand() + this.rand() - 2) * 1.732; }

  step(w, dt, maxPos, maxVel, kick, meanInterval, damping){
    w.timer -= dt;
    if(w.timer <= 0){
      w.vel += this.gauss() * kick;
      w.timer = meanInterval * (0.35 + this.rand() * 1.3);
    }
    w.vel *= damping;
    w.vel -= w.pos * 0.6 * dt;                 // a soft pull back to the middle
    if(w.vel > maxVel) w.vel = maxVel;
    if(w.vel < -maxVel) w.vel = -maxVel;
    w.pos += w.vel * dt;
    if(w.pos > maxPos){ w.pos = maxPos; w.vel = -Math.abs(w.vel) * 0.5; }
    if(w.pos < -maxPos){ w.pos = -maxPos; w.vel = Math.abs(w.vel) * 0.5; }
  }

  process(inputs, outputs, params){
    const out = outputs[0] && outputs[0][0];
    if(!out) return true;
    const input = inputs[0] || [];
    const n = out.length;
    const sr = sampleRate;
    const dt = n / sr;
    const smoothing = params.smoothing[0];
    const gateAmt = params.gate[0];
    const acoustic = params.acoustic[0] > 0.5;

    // Loose (0 %) … tight (100 %).
    const maxTimingMs = 20 - 15 * smoothing;           // ±20 … ±5 ms
    const maxCents = 12 - 8 * smoothing;               // 12 … 4 cents
    const baseMs = 16 + 8 * (1 - smoothing);           // 24 … 16 ms behind
    // |d(delay)/dt| that corresponds to maxCents, in ms per second.
    const maxRateMs = (1 - Math.pow(2, -maxCents / 1200)) * 1000;

    // Envelope follower on the (mono) input.
    const atkF = Math.exp(-1 / ((acoustic ? 0.0004 : 0.001) * sr));
    const relF = Math.exp(-1 / ((acoustic ? 0.03 : 0.05) * sr));
    const atkS = Math.exp(-1 / (0.03 * sr));
    const relS = Math.exp(-1 / (0.3 * sr));
    const ratio = acoustic ? 1.6 : 2.0;

    let transient = false;
    const chans = input.length;
    const mask = this.size - 1;
    for(let i = 0; i < n; i++){
      let x = 0;
      for(let c = 0; c < chans; c++) x += input[c][i];
      if(chans) x /= chans;
      this.buf[this.w] = x;
      this.w = (this.w + 1) & mask;
      const a = Math.abs(x);
      this.fast = a > this.fast ? atkF * this.fast + (1 - atkF) * a : relF * this.fast + (1 - relF) * a;
      this.slow = a > this.slow ? atkS * this.slow + (1 - atkS) * a : relS * this.slow + (1 - relS) * a;
    }
    this.level = this.slow;
    this.holdoff -= dt;
    if(this.holdoff <= 0 && this.fast > this.slow * ratio && this.fast > 0.004){
      transient = true;
      this.holdoff = 0.08;
    }

    if(transient) this.walkT.vel += this.gauss() * maxTimingMs * 6;
    this.step(this.walkT, dt, maxTimingMs, maxTimingMs * 4, maxTimingMs * 2.5, 0.35, 0.985);
    this.step(this.walkP, dt, 1.5, maxRateMs, maxRateMs * 0.8, 0.12, 0.96);

    const target = Math.max(4, baseMs + this.walkT.pos + this.walkP.pos);
    const d0 = this.delayMs, d1 = target;
    this.delayMs = target;

    // Gate on the clone only: below the threshold the second player stops,
    // which keeps a tight part tight instead of smearing the pauses.
    let gateTarget = 1;
    if(gateAmt > 0.001){
      const thrDb = -70 + 45 * gateAmt;
      const thr = Math.pow(10, thrDb / 20);
      gateTarget = this.slow > thr ? 1 : 0;
    }
    const gAtk = Math.exp(-1 / (0.002 * sr));
    const gRel = Math.exp(-1 / ((0.005 + 0.08 * (1 - gateAmt)) * sr));

    const start = (this.w - n) & mask;
    for(let i = 0; i < n; i++){
      const dMs = d0 + (d1 - d0) * (i / n);
      const dSamp = dMs * sr / 1000;
      let pos = start + i - dSamp;
      pos = ((pos % this.size) + this.size) % this.size;
      const i0 = Math.floor(pos);
      const frac = pos - i0;
      const s = this.buf[i0 & mask] * (1 - frac) + this.buf[(i0 + 1) & mask] * frac;
      const coef = gateTarget > this.gateGain ? gAtk : gRel;
      this.gateGain = coef * this.gateGain + (1 - coef) * gateTarget;
      out[i] = s * this.gateGain;
    }
    return true;
  }
}

registerProcessor("doubler-voice", DoublerVoice);

// ---------------------------------------------------------------------------
// "MicroShift"-style doubler: the micro pitch-shift + short delay trick of the
// Eventide H3000 and AMS DMX 15-80 (what Soundtoys MicroShift models). Each
// side gets its own copy, pitched a few cents up (left) or down (right) and
// delayed by a few ms; the three styles differ in how much and whether it
// moves. Values follow the published descriptions of those presets — an
// approximation, not a copy of anyone's code.
//
// Pitch shift: the classic two-tap delay line ("rotating tape heads") — a
// read pointer drifting through a 40 ms window at (1 − ratio) samples per
// sample, a second one half a window behind, cross-faded so the wrap is
// never heard.
const MS_STYLES = {
  1: {L: {c: 9, d: 10}, R: {c: -9, d: 14}, lfoC: 0, lfoD: 0, rate: [0, 0], drive: 1.25},
  2: {L: {c: 7, d: 7}, R: {c: -7, d: 12}, lfoC: 4, lfoD: 1.5, rate: [0.23, 0.19], drive: 1},
  3: {L: {c: 5, d: 22}, R: {c: -5, d: 28}, lfoC: 1.5, lfoD: 2, rate: [0.4, 0.33], drive: 1},
};

class MicroPitchVoice extends AudioWorkletProcessor {
  static get parameterDescriptors(){
    return [
      {name: "style", defaultValue: 1, minValue: 1, maxValue: 3, automationRate: "k-rate"},
      {name: "detune", defaultValue: 1, minValue: 0, maxValue: 2, automationRate: "k-rate"},
      {name: "delay", defaultValue: 1, minValue: 0, maxValue: 2, automationRate: "k-rate"},
    ];
  }
  constructor(){
    super();
    this.size = 1 << 14;
    this.buf = new Float32Array(this.size);
    this.w = 0;
    this.win = Math.round(0.04 * sampleRate);
    this.ph = [0, 0];            // pitch-shift phase per side, 0..win
    this.lfo = [0, Math.PI / 2];
  }
  read(pos){
    const mask = this.size - 1;
    pos = ((pos % this.size) + this.size) % this.size;
    const i = Math.floor(pos), f = pos - i;
    return this.buf[i & mask] * (1 - f) + this.buf[(i + 1) & mask] * f;
  }
  process(inputs, outputs, params){
    const out = outputs[0];
    if(!out || out.length < 2) return true;
    const input = inputs[0] || [];
    const n = out[0].length, sr = sampleRate, mask = this.size - 1;
    const st = MS_STYLES[Math.round(params.style[0])] || MS_STYLES[1];
    const detune = params.detune[0], delayK = params.delay[0];
    const chans = input.length;
    for(let i = 0; i < n; i++){
      let x = 0;
      for(let c = 0; c < chans; c++) x += input[c][i];
      if(chans) x /= chans;
      this.buf[this.w] = x;
      this.w = (this.w + 1) & mask;
    }
    const start = (this.w - n) & mask;
    const win = this.win;
    [["L", 0], ["R", 1]].forEach(([side, k]) => {
      const cfg = st[side];
      this.lfo[k] += 2 * Math.PI * st.rate[k] * n / sr;
      const m = Math.sin(this.lfo[k]) * (k ? -1 : 1);
      const cents = (cfg.c + st.lfoC * m * Math.sign(cfg.c)) * detune;
      const ratio = Math.pow(2, cents / 1200);
      const baseD = Math.max(1, (cfg.d + st.lfoD * m) * delayK) * sr / 1000;
      const o = out[k];
      for(let i = 0; i < n; i++){
        this.ph[k] += 1 - ratio;
        if(this.ph[k] >= win) this.ph[k] -= win;
        if(this.ph[k] < 0) this.ph[k] += win;
        const p1 = this.ph[k], p2 = (p1 + win / 2) % win;
        const g1 = Math.sin(Math.PI * p1 / win), g2 = Math.sin(Math.PI * p2 / win);
        const at = start + i - baseD;
        let s = this.read(at - p1) * g1 * g1 + this.read(at - p2) * g2 * g2;
        if(st.drive > 1) s = Math.tanh(s * st.drive) / Math.tanh(st.drive);
        o[i] = s;
      }
    });
    return true;
  }
}

registerProcessor("micropitch-voice", MicroPitchVoice);
