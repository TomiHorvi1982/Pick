// The looper's own sounds (static/looper.js): a drum kit and a few synths made in Web Audio — no
// samples, so they are there at once and anywhere, and they play the same in an OfflineAudioContext
// (a loop of notes is rendered into a buffer that loops like a recording).
//
// Drums take General MIDI's drum notes (the pads of a MIDI keyboard send them): 36 kick, 38 snare,
// 39 clap, 42 closed hi-hat, 46 open hi-hat, 45/47/48 toms, 49 crash, 51 ride, 37 rim. Any other note
// lands on the kit by where it falls in the octave.
const LoopSound = (() => {
  // the kit's rows, in the order the step grid shows them
  const KIT = [
    {id: "kick", name: "Kopák", note: 36}, {id: "snare", name: "Virbl", note: 38}, {id: "clap", name: "Tlesk", note: 39},
    {id: "hat", name: "Hi-hat", note: 42}, {id: "open", name: "Otevřený hat", note: 46}, {id: "tom", name: "Tom", note: 45},
    {id: "crash", name: "Činel", note: 49}, {id: "ride", name: "Ride", note: 51}];
  const GM = {35: "kick", 36: "kick", 37: "rim", 38: "snare", 40: "snare", 39: "clap", 42: "hat", 44: "hat", 46: "open",
    41: "tomL", 43: "tomL", 45: "tom", 47: "tom", 48: "tomH", 50: "tomH", 49: "crash", 57: "crash", 51: "ride", 59: "ride", 53: "ride"};
  const BY_PC = ["kick", "rim", "snare", "clap", "snare", "tomL", "hat", "tomL", "hat", "tom", "open", "tomH"];
  const drumOf = (m) => GM[m] || BY_PC[((m % 12) + 12) % 12];

  const noises = new WeakMap();
  function noise(c){
    let b = noises.get(c);
    if(!b){
      b = c.createBuffer(1, Math.round(c.sampleRate * 1.5), c.sampleRate);
      const d = b.getChannelData(0); let s = 22222;
      for(let i = 0; i < d.length; i++){ s = (s * 16807) % 2147483647; d[i] = s / 1073741823.5 - 1; }
      noises.set(c, b);
    }
    return b;
  }
  const env = (c, dest, t, peak, attack, decay) => {
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
    g.connect(dest); return g;
  };
  function noiseHit(c, dest, t, v, type, freq, q, decay, peak = 1){
    const s = c.createBufferSource(); s.buffer = noise(c);
    const f = c.createBiquadFilter(); f.type = type; f.frequency.value = freq; if(q) f.Q.value = q;
    s.connect(f); f.connect(env(c, dest, t, v * peak, 0.001, decay));
    s.start(t, Math.random() * 0.5); s.stop(t + decay + 0.05);
  }
  function tone(c, dest, t, v, f0, f1, sweep, decay, type = "sine", peak = 1){
    const o = c.createOscillator(); o.type = type;
    o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f1, t + sweep);
    o.connect(env(c, dest, t, v * peak, 0.002, decay)); o.start(t); o.stop(t + decay + 0.05);
  }
  // one hit of the kit at time t (v 0..1)
  function drum(c, dest, m, v, t){
    const k = drumOf(m);
    v = Math.max(0.05, Math.min(1, v));
    if(k === "kick"){ tone(c, dest, t, v, 160, 42, 0.12, 0.42, "sine", 1.3); noiseHit(c, dest, t, v, "lowpass", 1200, 0, 0.02, 0.25); }
    else if(k === "snare"){ tone(c, dest, t, v, 220, 160, 0.05, 0.12, "triangle", 0.5); noiseHit(c, dest, t, v, "highpass", 1400, 0, 0.2, 0.7); }
    else if(k === "clap"){ [0, 0.011, 0.022].forEach(d => noiseHit(c, dest, t + d, v, "bandpass", 1500, 1.2, 0.03, 0.8)); noiseHit(c, dest, t + 0.03, v, "bandpass", 1300, 1, 0.16, 0.6); }
    else if(k === "hat") noiseHit(c, dest, t, v, "highpass", 8000, 0, 0.05, 0.45);
    else if(k === "open") noiseHit(c, dest, t, v, "highpass", 7500, 0, 0.32, 0.4);
    else if(k === "crash") noiseHit(c, dest, t, v, "highpass", 4800, 0, 1.3, 0.5);
    else if(k === "ride"){ noiseHit(c, dest, t, v, "bandpass", 9000, 0.8, 0.55, 0.35); tone(c, dest, t, v, 3200, 3100, 0.3, 0.4, "triangle", 0.06); }
    else if(k === "rim"){ tone(c, dest, t, v, 1700, 1500, 0.01, 0.04, "square", 0.25); noiseHit(c, dest, t, v, "highpass", 3000, 0, 0.02, 0.3); }
    else { const f = k === "tomL" ? 95 : k === "tomH" ? 170 : 125; tone(c, dest, t, v, f * 1.6, f, 0.08, 0.4, "sine", 1); }
  }

  // ---- synths: a note m at t for d seconds, velocity v
  const SYNTHS = [{id: "syn:saw", name: "Syntezátor (lead)"}, {id: "syn:bass", name: "Syntezátorová basa"},
    {id: "syn:ep", name: "Elektrické piano"}, {id: "syn:pad", name: "Pad (plocha)"}];
  const hz = (m) => 440 * Math.pow(2, (m - 69) / 12);
  // returns a function that lets the note go (for live play); scheduled notes pass their length
  function synth(c, dest, kind, m, v, t, d){
    const f = hz(m), g = c.createGain(), out = c.createGain();
    out.gain.value = 0.22 + 0.5 * Math.max(0, Math.min(1, v));
    g.connect(out); out.connect(dest);
    const oscs = [];
    const osc = (type, fr, det = 0, gain = 1, to = g) => { const o = c.createOscillator(), og = c.createGain(); o.type = type; o.frequency.value = fr; o.detune.value = det; og.gain.value = gain; o.connect(og); og.connect(to); oscs.push(o); return o; };
    let A = 0.005, D = 0.25, S = 0.6, R = 0.18;
    if(kind === "syn:bass"){
      const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.setValueAtTime(1800, t); lp.frequency.exponentialRampToValueAtTime(380, t + 0.25); lp.Q.value = 6; lp.connect(g);
      osc("sawtooth", f, 0, 0.6, lp); osc("square", f / 2, 0, 0.4, lp); S = 0.7; R = 0.08;
    } else if(kind === "syn:ep"){
      const car = osc("sine", f, 0, 0.9), mod = c.createOscillator(), mg = c.createGain();
      mod.frequency.value = f * 2; mg.gain.setValueAtTime(f * 2.2, t); mg.gain.exponentialRampToValueAtTime(f * 0.2, t + 0.6);
      mod.connect(mg); mg.connect(car.frequency); oscs.push(mod);
      osc("sine", f * 2, 0, 0.12); D = 1.2; S = 0.25; R = 0.35;
    } else if(kind === "syn:pad"){
      const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 2400; lp.connect(g);
      [-9, 0, 9].forEach(dt => osc("sawtooth", f, dt, 0.28, lp)); osc("sine", f / 2, 0, 0.25, lp); A = 0.35; D = 0.4; S = 0.8; R = 0.8;
    } else {
      const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.setValueAtTime(6000, t); lp.frequency.exponentialRampToValueAtTime(1600, t + 0.4); lp.Q.value = 3; lp.connect(g);
      osc("sawtooth", f, -6, 0.4, lp); osc("sawtooth", f, 6, 0.4, lp); osc("square", f * 2, 0, 0.08, lp);
    }
    const p = g.gain;
    p.setValueAtTime(0, t); p.linearRampToValueAtTime(1, t + A); p.setTargetAtTime(S, t + A, D / 3);
    oscs.forEach(o => o.start(t));
    const release = (at) => { p.cancelScheduledValues(at); p.setValueAtTime(p.value, at); p.setTargetAtTime(0, at, R / 3); oscs.forEach(o => o.stop(at + R * 2 + 0.05)); };
    if(d != null) release(t + d);
    return release;
  }

  // ---- ready-made grooves for the step grid: per row the 16ths that hit, in one bar
  const GROOVES = {
    rock: {name: "Rock", kick: [0, 8, 10], snare: [4, 12], hat: [0, 2, 4, 6, 8, 10, 12, 14]},
    pop: {name: "Pop", kick: [0, 6, 8], snare: [4, 12], hat: [0, 2, 4, 6, 8, 10, 12, 14], clap: [12]},
    disco: {name: "Disco", kick: [0, 4, 8, 12], snare: [4, 12], open: [2, 6, 10, 14], hat: [0, 4, 8, 12]},
    funk: {name: "Funk", kick: [0, 3, 10], snare: [4, 7, 12], hat: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
    half: {name: "Half-time", kick: [0, 10], snare: [8], hat: [0, 2, 4, 6, 8, 10, 12, 14]},
    shuffle: {name: "Shuffle", kick: [0, 8], snare: [4, 12], hat: [0, 3, 4, 7, 8, 11, 12, 15], swing: 0.5},
    hiphop: {name: "Hip-hop", kick: [0, 7, 10], snare: [4, 12], hat: [0, 2, 4, 6, 8, 10, 12, 14], swing: 0.3},
    ballad: {name: "Balada", kick: [0, 10], snare: [8], ride: [0, 4, 8, 12], tom: [14]},
    reggae: {name: "Reggae (one drop)", kick: [8], snare: [8], hat: [2, 6, 10, 14], ride: [0, 4, 8, 12]},
  };
  return {KIT, drumOf, drum, SYNTHS, synth, GROOVES};
})();
