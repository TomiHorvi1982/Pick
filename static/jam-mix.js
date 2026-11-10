// Pick Jam — the friend's own mix of the song: each track of it (Opus from the jam's gateway), with his volume, pan,
// mute and solo — heard only at his place. Played at the moment the host's Pick plays it (the page says where);
// tempo and key are the host's (shared), done by time-stretching like Pick's mixer (a Signalsmith node a track).
(function(){
  const enc = encodeURIComponent;
  const LEAD = 0.12, STRETCH_LEAD = 0.25;

  class JamMix {
    constructor(ctx, api, out){
      this.ctx = ctx; this.api = api; this.bus = ctx.createGain(); this.bus.connect(out);
      this.ch = []; this.name = null; this.token = 0; this.log = []; this.running = false; this.stretchy = false; this.factory = null;
    }
    // every track of the song, decoded; onStep(done, of) while they come
    async load(name, stems, onStep){
      const my = ++this.token;
      this.stop(); this.ch.forEach(c => { try{ c.out.disconnect(); }catch(e){} }); this.ch = []; this.name = name;
      let done = 0;
      const one = async (stem) => {
        const r = await this.api.raw(`/g/stem?name=${enc(name)}&stem=${enc(stem)}`);
        if(!r.ok) throw new Error(`Stopu ${stem} se nepodařilo načíst.`);
        const buf = await this.ctx.decodeAudioData(await r.arrayBuffer());
        onStep && onStep(++done, stems.length);
        return {stem, buf};
      };
      const got = [];
      for(let i = 0; i < stems.length; i += 3){                       // three at a time: the line is shared
        got.push(...await Promise.all(stems.slice(i, i + 3).map(one)));
        if(my !== this.token) return false;
      }
      this.ch = got.map(({stem, buf}) => {
        // gain (trim) → fader → pan → out, and a meter for each side
        const gain = this.ctx.createGain(), pan = this.ctx.createStereoPanner(), split = this.ctx.createChannelSplitter(2);
        const anL = this.ctx.createAnalyser(), anR = this.ctx.createAnalyser();
        anL.fftSize = anR.fftSize = 512; gain.connect(pan); pan.connect(this.bus); pan.connect(split); split.connect(anL, 0); split.connect(anR, 1);
        return {stem, buf, gain, pan, anL, anR, out: pan, v: 1, t: 0, p: 0, m: false, s: false, src: null, stretch: null, semis: 0};
      });
      this.applyGains();
      return my === this.token;
    }
    get duration(){ return this.ch.reduce((m, c) => Math.max(m, c.buf.duration), 0); }
    set(stem, o){ const c = this.ch.find(x => x.stem === stem); if(!c) return; Object.assign(c, o); if("p" in o) c.pan.pan.value = c.p; this.applyGains(); }
    applyGains(){
      const solo = this.ch.some(c => c.s);
      this.ch.forEach(c => { const on = !c.m && (!solo || c.s); c.gain.gain.setTargetAtTime(on ? c.v * Math.pow(10, (c.t || 0) / 20) : 0, this.ctx.currentTime, 0.015); });
    }
    // the peak of each side, now
    levels(stem){
      const c = this.ch.find(x => x.stem === stem); if(!c) return [0, 0];
      const pk = (an) => { const a = new Float32Array(an.fftSize); an.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); return p; };
      return [pk(c.anL), pk(c.anR)];
    }
    // where the song is at a moment of this audio clock (the last start that came before it)
    songAt(t){
      for(let i = this.log.length - 1; i >= 0; i--){ const e = this.log[i]; if(e.when <= t) return e.offset + (t - e.when) * e.rate; }
      return null;
    }
    needStretch(speed, key, stemPitch){ return Math.abs(speed - 1) > 1e-3 || key || Object.values(stemPitch || {}).some(v => Math.abs(v) > 0.01); }
    async ensureStretch(){
      if(this.ch.every(c => c.stretch)) return;
      if(!this.factory) this.factory = (await import("/static/stretch/SignalsmithStretch.mjs")).default;
      for(const c of this.ch){
        if(c.stretch) continue;
        const node = await this.factory(this.ctx, {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2]});
        await node.addBuffers([c.buf.getChannelData(0).slice(), c.buf.getChannelData(Math.min(1, c.buf.numberOfChannels - 1)).slice()]);
        node.connect(c.gain); c.stretch = node;
      }
    }
    stop(){
      const now = this.ctx.currentTime;
      this.ch.forEach(c => {
        if(c.src){ try{ c.src.stop(); }catch(e){} c.src = null; }
        if(c.stretch){ try{ c.stretch.schedule({outputTime: now, active: false}); }catch(e){} }
      });
      if(this.running) this.log.push({when: now, offset: this.songAt(now) || 0, rate: 0});
      this.running = false;
    }
    // play from song second `pos` at the audio clock's `when`; tempo, key and each track's own shift as the host has them
    async start(pos, speed, key, stemPitch){
      this.stop();
      const stretch = this.needStretch(speed, key, stemPitch);
      if(stretch){
        await this.ensureStretch();
        const when = this.ctx.currentTime + STRETCH_LEAD, at = pos + STRETCH_LEAD * speed;
        this.ch.forEach(c => c.stretch.schedule({outputTime: when, active: true, input: at, rate: speed, semitones: key + ((stemPitch || {})[c.stem] || 0)}));
        this.log.push({when, offset: at, rate: speed});
      } else {
        const when = this.ctx.currentTime + LEAD, at = pos + LEAD;
        this.ch.forEach(c => {
          if(at >= c.buf.duration) return;
          const s = this.ctx.createBufferSource(); s.buffer = c.buf; s.connect(c.gain); s.start(when, Math.max(0, at)); c.src = s;
        });
        this.log.push({when, offset: at, rate: 1});
      }
      if(this.log.length > 200) this.log.splice(0, this.log.length - 200);
      this.running = true; this.stretchy = stretch; this.speed = speed; this.key = key; this.stemPitch = JSON.stringify(stemPitch || {});
    }
    // still the same way of playing? (a new tempo or key wants a fresh start)
    same(speed, key, stemPitch){ return this.running && this.speed === speed && this.key === key && this.stemPitch === JSON.stringify(stemPitch || {}); }
  }
  window.JamMix = JamMix;
})();
