// Pick Jam — the sound between the two browsers the way SonoBus sends it: uncompressed, in small packets, with
// a small jitter buffer of our own (no call's buffering, no codec's delay).
//
// jam-tx: the guitar, mono, one packet a render quantum (128 samples, 2.7 ms at 48 kHz) as 16-bit PCM; the
//   page puts a sequence number in front and sends it on an unreliable, unordered data channel.
// jam-rx: the friend's packets into a ring; played back once `target` samples are waiting. A packet lost is
//   filled from the one before (fading); one late is dropped. The two sound cards' clocks are never quite the
//   same: the reading speed follows the buffer (±0.4 %), so it neither runs dry nor grows. Ran dry: it waits a
//   little more next time (auto); steady for 8 s: a little less.

class JamTx extends AudioWorkletProcessor {
  constructor(){
    super();
    this.on = true; this.peak = 0; this.n = 0;
    this.port.onmessage = (e) => { if(e.data && "on" in e.data) this.on = !!e.data.on; };
  }
  process(inputs){
    const inp = inputs[0];
    if(this.on){
      const n = 128, out = new Int16Array(n);
      if(inp && inp.length && inp[0].length){
        const a = inp[0], b = inp.length > 1 ? inp[1] : null;
        let pk = 0;
        for(let i = 0; i < n; i++){
          let v = b ? (a[i] + b[i]) * 0.5 : a[i];
          if(v > 1) v = 1; else if(v < -1) v = -1;
          out[i] = v * 32767;
          const av = v < 0 ? -v : v; if(av > pk) pk = av;
        }
        if(pk > this.peak) this.peak = pk;
      }
      this.port.postMessage(out.buffer, [out.buffer]);
    }
    if(++this.n % 40 === 0){ this.port.postMessage({peak: this.peak}); this.peak = 0; }
    return true;
  }
}

class JamRx extends AudioWorkletProcessor {
  constructor(o){
    super();
    const opt = (o && o.processorOptions) || {};
    this.cap = 1 << 16;
    this.buf = new Float32Array(this.cap);
    this.w = 0; this.r = 0;                         // samples written / read (the read position goes in fractions)
    this.ratio = (opt.remoteRate || sampleRate) / sampleRate;
    this.target = (opt.quanta || 3) * 128;          // remote samples waiting before play
    this.auto = opt.auto !== false;
    this.minT = 128; this.maxT = 48 * 128;
    this.started = false; this.expect = -1;
    this.last = new Float32Array(128); this.conceal = 0;
    this.under = 0; this.late = 0; this.lost = 0; this.n = 0; this.calm = 0;
    this.port.onmessage = (e) => {
      const d = e.data;
      if(d instanceof ArrayBuffer){ this.push(d); return; }
      if(!d) return;
      if(d.remoteRate) this.ratio = d.remoteRate / sampleRate;
      if(d.quanta != null){ this.target = Math.max(1, d.quanta) * 128; }
      if("auto" in d) this.auto = !!d.auto;
      if(d.reset){ this.w = 0; this.r = 0; this.started = false; this.expect = -1; this.under = this.late = this.lost = 0; }
    };
  }
  write(src, gain){
    const cap = this.cap;
    for(let i = 0; i < src.length; i++) this.buf[(this.w + i) % cap] = src[i] * gain;
    this.w += src.length;
  }
  push(ab){
    if(ab.byteLength < 8) return;
    const seq = new DataView(ab).getUint32(0, true), pcm = new Int16Array(ab, 8), n = pcm.length;
    if(this.expect >= 0 && seq < this.expect && this.expect - seq < 1e6){ this.late++; return; }
    if(this.expect >= 0 && seq > this.expect){
      const gap = Math.min(16, seq - this.expect);
      this.lost += seq - this.expect;
      for(let g = 0; g < gap; g++){ this.conceal = this.conceal ? this.conceal * 0.5 : 0.6; this.write(this.last, this.conceal); }   // lost: the one before, fading
    }
    const f = new Float32Array(n);
    for(let i = 0; i < n; i++) f[i] = pcm[i] / 32768;
    this.write(f, 1); this.conceal = 0;
    if(f.length === 128) this.last = f;
    this.expect = seq + 1;
    if(this.w - this.r > this.cap - 4096) this.r = this.w - this.target;           // far behind: catch up at once
  }
  process(inputs, outputs){
    const out = outputs[0], L = out[0], n = L.length;
    const avail = this.w - this.r, need = n * this.ratio + 2;
    if(!this.started){
      if(avail >= this.target + need){ this.started = true; }
      else { for(const ch of out) ch.fill(0); this.stats(avail); return true; }
    }
    if(avail < need){                                   // ran dry: silence, and a little more waiting next time
      for(const ch of out) ch.fill(0);
      this.started = false; this.under++; this.calm = 0;
      if(this.auto) this.target = Math.min(this.maxT, this.target + 64);
      this.stats(avail); return true;
    }
    // the clocks of two sound cards: read a hair faster when too much waits, slower when too little
    const err = avail - this.target;
    let rate = this.ratio * (1 + Math.max(-0.004, Math.min(0.004, err / 24000)));
    if(err > 6 * 128 + this.target){ this.r += err - this.target / 2; }              // a lot too much: skip
    const cap = this.cap, buf = this.buf;
    let r = this.r;
    for(let i = 0; i < n; i++){
      const k = Math.floor(r), fr = r - k, a = buf[k % cap], b = buf[(k + 1) % cap];
      L[i] = a + (b - a) * fr;
      r += rate;
    }
    this.r = r;
    for(let c = 1; c < out.length; c++) out[c].set(L);
    if(this.auto && ++this.calm > 3000 && this.target > this.minT){ this.target -= 32; this.calm = 0; }   // ~8 s steady: a little less
    this.stats(avail);
    return true;
  }
  stats(avail){
    if(++this.n % 94) return;                           // ~4 times a second
    const per = sampleRate * this.ratio / 1000;
    this.port.postMessage({buffered: avail / per, target: this.target / per, under: this.under, late: this.late, lost: this.lost, playing: this.started});
  }
}

registerProcessor("jam-tx", JamTx);
registerProcessor("jam-rx", JamRx);
