// Pick Jam — the friend's guitar sound, in his own browser: clean, a print (tónový otisk) from Pick's library, or
// a NAM amp with a cabinet. The very chain Pick's tone.js builds (input → drive → EQ → the NAM core → the print's
// IR → room), so a print sounds at his place as it does in Pick. What comes out is what he hears and what the
// host hears of him. And the take for his own print: his guitar, clean, over a stretch of the song.
(function(){
  const PRE = {mid: 750, low: 250, high: 2000, hpQ: -3.0103};          // tone.js / tone_worker.py have the same
  const enc = encodeURIComponent;
  function biquad(c, type, f, qv, gain){
    const b = c.createBiquadFilter();
    b.type = type; b.frequency.value = f; b.Q.value = qv;
    if(gain != null) b.gain.value = gain;
    return b;
  }
  function mulberry32(a){
    return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  function roomBuffer(c, decay){
    const sr = c.sampleRate, n = Math.floor(sr * Math.min(3.5, 1.5 * decay)), pre = Math.floor(sr * 0.012);
    const rnd = mulberry32(1), h = new Float32Array(pre + n);
    let y = 0, e = 0;
    for(let i = 0; i < n; i++){ const x = (rnd() * 2 - 1) * Math.exp(-6.907755 * i / sr / decay); y = 0.45 * x + 0.55 * y; h[pre + i] = y; e += y * y; }
    const k = 1 / Math.sqrt(e);
    for(let i = pre; i < h.length; i++) h[i] *= k;
    const buf = c.createBuffer(1, h.length, sr); buf.copyToChannel(h, 0);
    return buf;
  }

  class JamAmp {
    // api.raw(url) → fetch Response (the page adds the room's key when there is no login)
    constructor(ctx, api){
      this.ctx = ctx; this.api = api;
      this.input = ctx.createGain(); this.output = ctx.createGain();
      this.input.connect(this.output);
      this.cur = null; this.label = "Čistý zvuk"; this.inDb = 0; this.trimDb = 0; this.engine = null; this.models = new Map();
    }
    async eng(){
      if(!this.engine){ const m = await import("/static/nam/index.js"); this.engine = await m.NamEngine.attach(this.ctx, {assetBaseUrl: "/static/nam/"}); }
      return this.engine;
    }
    async modelText(file){
      if(this.models.has(file)) return this.models.get(file);
      const r = await this.api.raw(`/g/amp?file=${enc(file)}`);
      if(!r.ok) throw new Error("Zesilovač se nepodařilo načíst.");
      const t = await r.text(); this.models.clear(); this.models.set(file, t);
      return t;
    }
    async irBuffer(url){
      const r = await this.api.raw(url);
      if(!r.ok) throw new Error("Kabinet (IR) se nepodařilo načíst.");
      return this.ctx.decodeAudioData(await r.arrayBuffer());
    }
    _put(chain, extra, info){
      const old = this.cur;
      try{ this.input.disconnect(); }catch(e){}
      if(chain){
        this.input.connect(chain[0]);
        for(let i = 0; i + 1 < chain.length; i++) chain[i].connect(chain[i + 1]);
        chain[chain.length - 1].connect(this.output);
      } else this.input.connect(this.output);
      this.cur = chain ? {nodes: chain.concat(extra || []), ...info} : null;
      if(old){ old.nodes.forEach(n => { try{ n.disconnect(); }catch(e){} }); if(old.core){ try{ old.core.unloadModel(); }catch(e){} } }
      this.setDrive(this.trimDb);
    }
    clear(){ this._put(null); this.label = "Čistý zvuk"; this.kind = "clean"; }
    // a print of Pick's library: the song it is of and its id
    async setPrint(song, pid, name){
      const d = await (await this.api.raw(`/g/print?name=${enc(song)}&print=${enc(pid)}`)).json();
      const p = d.print; if(!p) throw new Error(d.error || "Otisk nenalezen.");
      const c = this.ctx;
      let core = null;
      if(p.core && p.core.id){ const txt = await this.modelText(p.core.id); core = await (await this.eng()).createNode(); await core.loadModel(txt); }
      const ir = await this.irBuffer(`/g/print-ir?name=${enc(song)}&print=${enc(p.id)}`);
      const drive = c.createGain(), colour = c.createConvolver(), out = c.createGain();
      colour.normalize = false; colour.buffer = ir;
      if(typeof p.out_gain === "number" && p.out_gain > 0) out.gain.value = p.out_gain;
      const chain = [drive];
      const pre = p.pre || {};
      if(pre.hp > 20) chain.push(biquad(c, "highpass", pre.hp, PRE.hpQ));
      if(Math.abs(pre.mid_db || 0) > 0.05) chain.push(biquad(c, "peaking", PRE.mid, 0.7, pre.mid_db));
      if(Math.abs(pre.tilt_db || 0) > 0.05) chain.push(biquad(c, "lowshelf", PRE.low, 0, -pre.tilt_db / 2), biquad(c, "highshelf", PRE.high, 0, pre.tilt_db / 2));
      if(core) chain.push(core);
      chain.push(colour, out);
      const extra = [];
      if(p.room && p.room.mix > 0){
        const wet = c.createConvolver(), mix = c.createGain();
        wet.normalize = false; wet.buffer = roomBuffer(c, p.room.decay); mix.gain.value = p.room.mix;
        colour.connect(wet); wet.connect(mix); mix.connect(out); extra.push(wet, mix);
      }
      this.kind = "print"; this.label = "Otisk · " + (name || p.name || "");
      this._put(chain, extra, {core, drive, print: p});
    }
    // a NAM amp (a model of Pick's catalog) and a cabinet (an IR of it), either may be left out
    async setAmp(model, ir, label){
      const c = this.ctx, chain = [c.createGain()];
      let core = null;
      if(model){ core = await (await this.eng()).createNode(); await core.loadModel(await this.modelText(model)); chain.push(core); }
      if(ir){ const cv = c.createConvolver(); cv.normalize = true; cv.buffer = await this.irBuffer(`/g/amp?file=${enc(ir)}`); chain.push(cv); }
      chain.push(c.createGain());
      this.kind = "amp"; this.label = label || "Zesilovač";
      this._put(chain, [], {core, drive: chain[0]});
    }
    setInputDb(db){ this.inDb = db; this.setDrive(this.trimDb); }
    // the drive into the print or the amp, on top of what the print was made with (tone.js: GAIN and Drive otisku)
    setDrive(db){
      this.trimDb = db;
      if(!this.cur || !this.cur.drive) return;
      const p = this.cur.print;
      const total = p ? (p.drive_db || 0) + (p.gain_db || 0) - this.inDb + db : db;
      this.cur.drive.gain.value = Math.pow(10, total / 20);
    }
  }

  // His take for a print of his own: what came in clean, block by block, each with the moment of the audio
  // clock it was played at — mapped back to the song by the page (songAt), then a WAV of [start, end).
  const REC = `class R extends AudioWorkletProcessor{constructor(){super();this.on=true;this.port.onmessage=()=>{this.on=false;}}
    process(i){const ch=i[0]&&i[0][0];if(ch&&this.on)this.port.postMessage({t:currentTime,d:ch.slice()});return this.on;}}registerProcessor("jam-rec",R);`;
  async function recorder(ctx){
    if(!recorder.ok){ await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([REC], {type: "text/javascript"}))); recorder.ok = true; }
    return new AudioWorkletNode(ctx, "jam-rec", {numberOfInputs: 1, numberOfOutputs: 0});
  }
  function wav(x, sr){
    const n = x.length, b = new ArrayBuffer(44 + n * 2), v = new DataView(b);
    const w = (o, s) => { for(let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, n * 2, true);
    for(let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x[i])) * 32767, true);
    return new Blob([b], {type: "audio/wav"});
  }
  window.JamAmp = JamAmp;
  window.JamRec = {recorder, wav};
})();
