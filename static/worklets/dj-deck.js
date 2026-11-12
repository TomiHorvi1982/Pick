// Pick DJ — one deck's player, running on the audio thread.
//
// Holds a song's stems (Int16, one array per stem and channel, at the
// context's sample rate) and mixes them with per-stem gains while reading at
// a variable rate (varispeed; key lock is a pitch shifter further down the
// chain, see static/dj.js). Everything that moves the playhead — seek, loop,
// nudge — happens here, sample-accurately. Reports its position about every
// 20 ms with the context time it refers to.
class PickDJDeck extends AudioWorkletProcessor {
  constructor() {
    super();
    this.stems = [];          // [[left Int16Array, right Int16Array], ...]
    this.length = 0;
    this.pos = 0;             // frames, fractional
    this.rate = 1;
    this.bend = 0;            // temporary rate offset (nudge / pitch bend)
    this.playing = false;
    this.gains = [];          // current per-stem gains
    this.targets = [];        // per-stem gains to glide to
    this.loop = null;         // {start, end} in frames
    this.fade = 1;            // declick after jumps: 0 → 1
    this.blocks = 0;
    this.port.onmessage = (e) => this.message(e.data);
  }

  message(m) {
    switch (m.type) {
      case "load":
        this.stems = m.stems;
        this.length = m.length;
        this.pos = 0;
        this.playing = false;
        this.loop = null;
        this.gains = m.stems.map(() => 1);
        this.targets = m.stems.map(() => 1);
        this.report(true);
        break;
      case "unload":
        this.stems = [];
        this.length = 0;
        this.playing = false;
        this.report(true);
        break;
      case "play":
        this.playing = !!m.on && this.length > 0;
        if (this.playing) this.fade = 0;
        this.report(true);
        break;
      case "seek":
        this.pos = Math.max(0, Math.min(this.length - 1, m.frame));
        this.fade = 0;
        this.report(true);
        break;
      case "shift":            // relative move (phase alignment); no declick, it is small
        this.pos = Math.max(0, Math.min(this.length - 1, this.pos + m.frames));
        this.report(true);
        break;
      case "rate":
        this.rate = m.value;
        break;
      case "bend":
        this.bend = m.value;
        break;
      case "gains":
        this.targets = m.values.slice(0, this.stems.length);
        break;
      case "loop":
        this.loop = m.loop && m.loop.end > m.loop.start ? m.loop : null;
        if (this.loop && (this.pos >= this.loop.end || this.pos < this.loop.start - 4 * sampleRate)) {
          this.pos = this.loop.start;
          this.fade = 0;
        }
        break;
    }
  }

  report(now) {
    if (!now && (++this.blocks & 7)) return;
    this.port.postMessage({pos: this.pos / sampleRate, playing: this.playing, time: currentTime});
  }

  process(inputs, outputs) {
    const out = outputs[0];
    const L = out[0], R = out[1] || out[0];
    const n = L.length;
    if (!this.playing || !this.length) {
      L.fill(0); R.fill(0);
      this.report(false);
      return true;
    }
    const stems = this.stems, count = stems.length;
    // Glide gains across the block (no zipper noise).
    const from = this.gains, to = this.targets;
    const step = this.rate + this.bend;
    const scale = 1 / 32768;
    const last = this.length - 2;
    for (let i = 0; i < n; i++) {
      let p = this.pos;
      if (this.loop && p >= this.loop.end) {
        p = this.loop.start + (p - this.loop.end);
      }
      if (p > last) {
        this.playing = false;
        for (; i < n; i++) { L[i] = 0; R[i] = 0; }
        break;
      }
      const k = Math.floor(p), f = p - k;
      const t = i / n;
      let l = 0, r = 0;
      for (let s = 0; s < count; s++) {
        const g = from[s] + (to[s] - from[s]) * t;
        if (g === 0) continue;
        const sl = stems[s][0], sr = stems[s][1];
        l += g * (sl[k] + (sl[k + 1] - sl[k]) * f);
        r += g * (sr[k] + (sr[k + 1] - sr[k]) * f);
      }
      let fade = 1;
      if (this.fade < 1) { this.fade = Math.min(1, this.fade + 1 / 256); fade = this.fade; }
      L[i] = l * scale * fade;
      R[i] = r * scale * fade;
      this.pos = p + step;
    }
    for (let s = 0; s < count; s++) from[s] = to[s];
    this.report(false);
    return true;
  }
}

registerProcessor("pick-dj-deck", PickDJDeck);
