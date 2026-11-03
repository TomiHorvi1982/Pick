// Delay & Reverb (DLY / REV on the live guitar's fader): a space after the channel's own inserts,
// made here in Web Audio — no files, no plugins. One floating window shows both: the echoes as
// they will come back, the reverb's tail as it dies away, types, presets and sets of both.
//
// The fx chain of a channel (Mixer, makeFx) calls SpaceFx.attach(fx) and wires fx.space
// (input → output) after the doubler; fx.state.space is saved with the session.
//
//   in ─┬─ dry ───────────────────┬─ mid ─┬───────────────────────────── out
//       └─ delay (L/R lines, feedback through tone, saturation, wow) ─┘  └─ low cut → pre-delay → IR → tone → wet ─┘
const SpaceFx = (() => {
  const H = () => (typeof Mixer !== "undefined" && Mixer.space) || null;
  const ctx = () => H().ctx();

  // ---------------------------------------------------------------- what there is
  const DELAY_TYPES = {
    digital:  {name: "Digitální", tip: "čisté, přesné opakování", sat: 0,   wow: 0,   dark: 0,    color: "#5ec8ff"},
    analog:   {name: "Analogový", tip: "teplý, s každým opakováním tmavší", sat: 0.35, wow: 0.35, dark: 0.35, color: "#ffb35e"},
    tape:     {name: "Páskový",   tip: "kolísání pásku a jemné přebuzení", sat: 0.6,  wow: 1,   dark: 0.25, color: "#ff7a7a"},
    pingpong: {name: "Ping-pong", tip: "ozvěna skáče zleva doprava", sat: 0.1, wow: 0.1, dark: 0.1, color: "#7affc4", stereo: true},
  };
  const REVERB_TYPES = {
    room:      {name: "Místnost", tip: "malý prostor, blízké odrazy"},
    hall:      {name: "Sál",      tip: "hladký, velký prostor"},
    plate:     {name: "Plate",    tip: "hustý a jasný, studiová klasika"},
    spring:    {name: "Pružina",  tip: "pružinový dozvuk z komba, surf"},
    cathedral: {name: "Katedrála", tip: "obrovský, pomalý nástup"},
    ambient:   {name: "Ambient",  tip: "dlouhá plocha, téměř pad"},
  };
  const DIVS = [["1/2", 2], ["1/4.", 1.5], ["1/4", 1], ["1/4T", 2 / 3], ["1/8.", 0.75], ["1/8", 0.5], ["1/8T", 1 / 3], ["1/16", 0.25]];
  const DELAY_DEFAULT = {on: false, type: "analog", sync: true, div: "1/8.", ms: 380, fb: 0.35, tone: 0.5, mod: 0.2, mix: 0.3};
  const REVERB_DEFAULT = {on: false, type: "hall", decay: 2.4, pre: 20, tone: 0.55, lowcut: 150, mix: 0.25};

  const DELAY_PRESETS = [
    {name: "Slapback", tip: "rock'n'roll a country — jedno krátké opakování", p: {type: "tape", sync: false, ms: 110, fb: 0.08, tone: 0.55, mod: 0.15, mix: 0.38}},
    {name: "Rytmus 1/8 s tečkou", tip: "pulzující rytmický delay", p: {type: "digital", sync: true, div: "1/8.", fb: 0.32, tone: 0.7, mod: 0, mix: 0.3}},
    {name: "Sólo 1/4", tip: "teplé opakování pod sólo", p: {type: "analog", sync: true, div: "1/4", fb: 0.38, tone: 0.4, mod: 0.25, mix: 0.28}},
    {name: "Široký ping-pong", tip: "stereo prostor", p: {type: "pingpong", sync: true, div: "1/8", fb: 0.42, tone: 0.6, mod: 0.1, mix: 0.3}},
    {name: "Páskový ambient", tip: "dlouhé, kolísavé, tmavnoucí", p: {type: "tape", sync: true, div: "1/4.", fb: 0.6, tone: 0.3, mod: 0.6, mix: 0.35}},
    {name: "Dub", tip: "opakování skoro donekonečna", p: {type: "tape", sync: true, div: "1/4", fb: 0.82, tone: 0.25, mod: 0.4, mix: 0.4}},
    {name: "Jemná ozvěna", tip: "skoro neslyšná, jen přidá prostor", p: {type: "digital", sync: false, ms: 260, fb: 0.15, tone: 0.5, mod: 0.05, mix: 0.15}},
  ];
  const REVERB_PRESETS = [
    {name: "Malá místnost", tip: "kytara jako ve zkušebně", p: {type: "room", decay: 0.6, pre: 4, tone: 0.5, lowcut: 200, mix: 0.2}},
    {name: "Studio", tip: "nenápadný, přirozený", p: {type: "room", decay: 1.1, pre: 10, tone: 0.6, lowcut: 180, mix: 0.18}},
    {name: "Velký sál", tip: "široký a hladký", p: {type: "hall", decay: 2.8, pre: 25, tone: 0.5, lowcut: 150, mix: 0.28}},
    {name: "Plate na sóla", tip: "hustý, jasný lesk", p: {type: "plate", decay: 1.9, pre: 15, tone: 0.75, lowcut: 250, mix: 0.25}},
    {name: "Pružinové kombo", tip: "klasický dozvuk z lampového komba", p: {type: "spring", decay: 2.0, pre: 0, tone: 0.55, lowcut: 300, mix: 0.3}},
    {name: "Katedrála", tip: "obrovský prostor", p: {type: "cathedral", decay: 5.5, pre: 45, tone: 0.4, lowcut: 150, mix: 0.32}},
    {name: "Ambientní plocha", tip: "kytara se rozplyne", p: {type: "ambient", decay: 8, pre: 60, tone: 0.55, lowcut: 200, mix: 0.45}},
  ];
  const SETS = [
    {name: "Clean ambient", tip: "páskový delay + dlouhý sál", d: {on: true, ...DELAY_PRESETS[4].p}, r: {on: true, ...REVERB_PRESETS[2].p, mix: 0.35}},
    {name: "Sólo", tip: "delay 1/4 + plate", d: {on: true, ...DELAY_PRESETS[2].p}, r: {on: true, ...REVERB_PRESETS[3].p}},
    {name: "Rocková rytmika", tip: "bez delaye, jen místnost", d: {on: false}, r: {on: true, ...REVERB_PRESETS[1].p}},
    {name: "Surf", tip: "slapback + pružina naplno", d: {on: true, ...DELAY_PRESETS[0].p}, r: {on: true, ...REVERB_PRESETS[4].p, mix: 0.45}},
    {name: "Shoegaze", tip: "ping-pong + katedrála", d: {on: true, ...DELAY_PRESETS[3].p, fb: 0.55, mix: 0.4}, r: {on: true, ...REVERB_PRESETS[5].p, mix: 0.45}},
    {name: "Suchá kytara", tip: "obojí vypnout", d: {on: false}, r: {on: false}},
  ];

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const toneHz = (t) => 900 * Math.pow(14000 / 900, clamp(t, 0, 1));      // 0 dark … 1 bright

  function stateOf(fx){
    const s = fx.state.space || (fx.state.space = {});
    s.delay = {...DELAY_DEFAULT, ...(s.delay || {})};
    s.reverb = {...REVERB_DEFAULT, ...(s.reverb || {})};
    return s;
  }
  function bpm(){ try{ return H().bpm() || 0; }catch(e){ return 0; } }
  function delayMs(d){
    if(!d.sync) return clamp(d.ms, 20, 2000);
    const b = bpm() || 120, f = (DIVS.find(x => x[0] === d.div) || DIVS[5])[1];
    return clamp(60000 / b * f, 20, 2000);
  }

  // ---------------------------------------------------------------- the reverb's impulse, made here
  function rand(seed){ let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2147483648 - 1; }; }
  function impulse(type, decay){
    const ac = ctx(), sr = ac.sampleRate;
    const len = Math.floor(sr * Math.min(12, decay * 1.25 + 0.15));
    const buf = ac.createBuffer(2, len, sr);
    const k = 6.9 / Math.max(0.1, decay);                                   // −60 dB at "decay"
    const cfg = {
      room:      {attack: 0.002, bright: 0.55, darkEnd: 0.18, early: [[0.004, 0.7], [0.009, 0.55], [0.013, 0.5], [0.019, 0.42], [0.024, 0.36], [0.031, 0.3], [0.038, 0.24]]},
      hall:      {attack: 0.03, bright: 0.45, darkEnd: 0.08, early: [[0.012, 0.35], [0.021, 0.3], [0.034, 0.26], [0.047, 0.2]]},
      plate:     {attack: 0.001, bright: 0.85, darkEnd: 0.35, early: []},
      spring:    {attack: 0.001, bright: 0.5, darkEnd: 0.2, early: [], spring: true},
      cathedral: {attack: 0.08, bright: 0.35, darkEnd: 0.05, early: [[0.03, 0.3], [0.055, 0.28], [0.08, 0.25], [0.11, 0.2], [0.14, 0.16]]},
      ambient:   {attack: 0.35, bright: 0.5, darkEnd: 0.1, early: []},
    }[type] || {};
    for(let ch = 0; ch < 2; ch++){
      const d = buf.getChannelData(ch), r = rand(7919 * (ch + 1) + type.length * 31);
      let lp = 0;
      for(let i = 0; i < len; i++){
        const t = i / sr, prog = Math.min(1, t / Math.max(0.2, decay));
        const a = cfg.bright + (cfg.darkEnd - cfg.bright) * prog;             // darker as it dies away
        lp += a * (r() - lp);
        const att = cfg.attack > 0.002 ? Math.min(1, t / cfg.attack) : 1;
        d[i] = lp * Math.exp(-k * t) * att * (type === "plate" ? 1.3 : 1);
      }
      (cfg.early || []).forEach(([tt, g]) => {
        const i = Math.floor((tt + (ch ? 0.0013 : 0)) * sr);
        if(i < len) d[i] += g * (ch ? -1 : 1);
      });
      if(cfg.spring){                                                       // a train of falling chirps, the spring's "drip"
        const period = 0.031 + ch * 0.0017;
        for(let n = 0; n * period < decay * 1.1; n++){
          const t0 = n * period, amp = 0.55 * Math.exp(-k * t0);
          for(let j = 0; j < sr * 0.024; j++){
            const i = Math.floor(t0 * sr) + j; if(i >= len) break;
            const tau = j / sr, f = 3200 - 2400 * (tau / 0.024);
            d[i] += amp * Math.sin(2 * Math.PI * f * tau) * Math.exp(-tau * 90);
          }
        }
      }
    }
    return buf;
  }

  // ---------------------------------------------------------------- the nodes
  function attach(fx){
    if(fx.space) return fx.space;
    const ac = ctx(), s = stateOf(fx);
    const g = (v = 1) => { const n = ac.createGain(); n.gain.value = v; return n; };
    const sp = {input: g(), output: g(), mid: g(), dry: g(1)};
    sp.input.connect(sp.dry); sp.dry.connect(sp.mid); sp.mid.connect(sp.output);
    // delay
    const D = sp.d = {send: g(0), mono: g(), lineL: ac.createDelay(2.5), lineR: ac.createDelay(2.5),
      injL: g(1), injR: g(1), wet: g(1), merge: ac.createChannelMerger(2), meter: ac.createAnalyser()};
    D.mono.channelCount = 1; D.mono.channelCountMode = "explicit";
    D.lpL = ac.createBiquadFilter(); D.lpR = ac.createBiquadFilter(); D.lpL.type = D.lpR.type = "lowpass";
    D.hpL = ac.createBiquadFilter(); D.hpR = ac.createBiquadFilter(); D.hpL.type = D.hpR.type = "highpass";
    D.hpL.frequency.value = D.hpR.frequency.value = 90;
    D.shL = ac.createWaveShaper(); D.shR = ac.createWaveShaper();
    D.selfL = g(0); D.selfR = g(0); D.crossLR = g(0); D.crossRL = g(0);
    D.lfo = ac.createOscillator(); D.lfo.frequency.value = 0.55; D.lfoGain = g(0);
    D.lfo.connect(D.lfoGain); D.lfoGain.connect(D.lineL.delayTime); D.lfoGain.connect(D.lineR.delayTime); D.lfo.start();
    sp.input.connect(D.send); D.send.connect(D.mono);
    D.mono.connect(D.injL); D.mono.connect(D.injR); D.injL.connect(D.lineL); D.injR.connect(D.lineR);
    D.lineL.connect(D.lpL); D.lpL.connect(D.hpL); D.hpL.connect(D.shL);
    D.lineR.connect(D.lpR); D.lpR.connect(D.hpR); D.hpR.connect(D.shR);
    D.shL.connect(D.selfL); D.selfL.connect(D.lineL); D.shL.connect(D.crossLR); D.crossLR.connect(D.lineR);
    D.shR.connect(D.selfR); D.selfR.connect(D.lineR); D.shR.connect(D.crossRL); D.crossRL.connect(D.lineL);
    D.lineL.connect(D.merge, 0, 0); D.lineR.connect(D.merge, 0, 1);
    D.merge.connect(D.wet); D.wet.connect(sp.mid); D.wet.connect(D.meter);
    D.meter.fftSize = 1024;
    // reverb
    const R = sp.r = {send: g(0), hp: ac.createBiquadFilter(), pre: ac.createDelay(0.5), conv: ac.createConvolver(),
      lp: ac.createBiquadFilter(), wet: g(0), meter: ac.createAnalyser(), key: ""};
    R.hp.type = "highpass"; R.lp.type = "lowpass"; R.meter.fftSize = 1024;
    sp.mid.connect(R.send); R.send.connect(R.hp); R.hp.connect(R.pre); R.pre.connect(R.conv);
    R.conv.connect(R.lp); R.lp.connect(R.wet); R.wet.connect(sp.output); R.wet.connect(R.meter);
    fx.space = sp;
    attached.add(fx);
    apply(fx);
    H().rewire(fx);
    return sp;
  }

  function satCurve(amount){
    if(amount <= 0.01) return null;
    const n = 1024, c = new Float32Array(n), k = 1 + amount * 4;
    for(let i = 0; i < n; i++){ const x = i / (n - 1) * 2 - 1; c[i] = Math.tanh(k * x) / Math.tanh(k); }
    return c;
  }

  let irTimer = 0;
  function apply(fx){
    const sp = fx.space; if(!sp) return;
    const s = stateOf(fx), ac = ctx(), now = ac.currentTime, T = (p, v) => p.setTargetAtTime(v, now, 0.03);
    // delay
    const d = s.delay, ty = DELAY_TYPES[d.type] || DELAY_TYPES.digital, D = sp.d;
    const t = delayMs(d) / 1000, fb = clamp(d.fb, 0, 0.92);
    T(D.send.gain, d.on ? 1 : 0);
    T(D.wet.gain, d.on ? d.mix * 1.4 : 0);
    T(D.lineL.delayTime, t); T(D.lineR.delayTime, ty.stereo ? t : t * 1.012);
    const cut = toneHz(d.tone * (1 - ty.dark * 0.6));
    T(D.lpL.frequency, cut); T(D.lpR.frequency, cut);
    D.shL.curve = D.shR.curve = satCurve(ty.sat);
    T(D.lfoGain.gain, d.mod * (0.0006 + ty.wow * 0.0032));
    D.lfo.frequency.setValueAtTime(d.type === "tape" ? 0.7 : 0.45, now);
    if(ty.stereo){ T(D.injL.gain, 1); T(D.injR.gain, 0); T(D.selfL.gain, 0); T(D.selfR.gain, 0); T(D.crossLR.gain, fb); T(D.crossRL.gain, fb); }
    else { T(D.injL.gain, 1); T(D.injR.gain, 1); T(D.selfL.gain, fb); T(D.selfR.gain, fb); T(D.crossLR.gain, 0); T(D.crossRL.gain, 0); }
    // reverb
    const r = s.reverb, R = sp.r;
    T(R.send.gain, r.on ? 1 : 0);
    T(R.wet.gain, r.on ? r.mix * 1.6 : 0);
    T(R.hp.frequency, clamp(r.lowcut, 20, 1200));
    T(R.pre.delayTime, clamp(r.pre, 0, 400) / 1000);
    T(R.lp.frequency, toneHz(r.tone));
    const key = r.type + ":" + r.decay.toFixed(2);
    if(r.on && key !== R.key){
      clearTimeout(irTimer);
      irTimer = setTimeout(() => { R.conv.buffer = impulse(r.type, r.decay); R.key = key; if(win && win.fx === fx) draw(); }, R.key ? 120 : 0);
    }
  }

  function change(fx, part, patch){
    const s = stateOf(fx);
    Object.assign(s[part], patch);
    if(!fx.space) attach(fx); else apply(fx);
    H().save(); H().repaint();
    if(win && win.fx === fx) render();
  }

  // ---------------------------------------------------------------- the window
  let win = null, raf = 0;
  const esc = (x) => String(x).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const ICON = {
    digital: '<path d="M2 12h3l2-6 3 12 3-12 3 12 2-6h4"/>',
    analog: '<path d="M2 12c3-8 5-8 7 0s4 8 6 0 4-6 7-2"/>',
    tape: '<circle cx="7" cy="12" r="4"/><circle cx="17" cy="12" r="4"/><path d="M7 16h10"/>',
    pingpong: '<path d="M3 7h6l6 10h6M3 17h6l6-10h6"/>',
    room: '<rect x="4" y="5" width="16" height="14" rx="2"/>',
    hall: '<path d="M3 20V9l9-5 9 5v11M8 20v-6h8v6"/>',
    plate: '<rect x="3" y="6" width="18" height="12" rx="1"/><path d="M6 9h12M6 12h12M6 15h12"/>',
    spring: '<path d="M3 12c1-4 2-4 3 0s2 4 3 0 2-4 3 0 2 4 3 0 2-4 3 0 2 4 3 0"/>',
    cathedral: '<path d="M12 2l3 6v14H9V8zM4 22V13l5-3M20 22V13l-5-3"/>',
    ambient: '<path d="M3 15c3-6 6-6 9 0s6 6 9 0M3 10c3-5 6-5 9 0s6 5 9 0"/>',
  };
  const icon = (k) => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON[k] || ""}</svg>`;

  function slider(part, key, label, min, max, step, val, fmt){
    return `<label class="spc-sl"><span>${label}<b>${esc(fmt(val))}</b></span>
      <input type="range" data-part="${part}" data-key="${key}" min="${min}" max="${max}" step="${step}" value="${val}"></label>`;
  }

  function cardHtml(fx, part){
    const s = stateOf(fx), isD = part === "delay", st = s[part];
    const types = isD ? DELAY_TYPES : REVERB_TYPES, presets = isD ? DELAY_PRESETS : REVERB_PRESETS;
    const pct = (v) => Math.round(v * 100) + " %";
    const ms = delayMs(st);
    const controls = isD ? `
      <div class="spc-time">
        <button type="button" class="spc-sync ${st.sync ? "on" : ""}" data-part="delay" data-sync title="Délka podle tempa písně (${bpm() ? Math.round(bpm()) + " BPM" : "tempo neznámé — 120 BPM"})">♩ podle tempa</button>
        ${st.sync ? `<div class="spc-divs">${DIVS.map(([n]) => `<button type="button" data-div="${n}" class="${st.div === n ? "on" : ""}">${n}</button>`).join("")}</div>`
                  : ""}
      </div>
      ${st.sync ? `<div class="spc-note">${Math.round(ms)} ms</div>` : slider("delay", "ms", "Čas", 20, 2000, 1, st.ms, v => Math.round(v) + " ms")}
      ${slider("delay", "fb", "Opakování", 0, 0.92, 0.01, st.fb, pct)}
      ${slider("delay", "tone", "Barva", 0, 1, 0.01, st.tone, v => v < 0.33 ? "tmavá" : v < 0.66 ? "střední" : "jasná")}
      ${slider("delay", "mod", "Modulace", 0, 1, 0.01, st.mod, pct)}
      ${slider("delay", "mix", "Mix", 0, 1, 0.01, st.mix, pct)}` : `
      ${slider("reverb", "decay", "Délka", 0.3, 12, 0.1, st.decay, v => (+v).toFixed(1).replace(".", ",") + " s")}
      ${slider("reverb", "pre", "Předzpoždění", 0, 200, 1, st.pre, v => Math.round(v) + " ms")}
      ${slider("reverb", "tone", "Barva", 0, 1, 0.01, st.tone, v => v < 0.33 ? "tmavá" : v < 0.66 ? "střední" : "jasná")}
      ${slider("reverb", "lowcut", "Ořez basů", 20, 800, 1, st.lowcut, v => Math.round(v) + " Hz")}
      ${slider("reverb", "mix", "Mix", 0, 1, 0.01, st.mix, pct)}`;
    return `<section class="spc-card ${st.on ? "" : "off"}" data-card="${part}">
      <div class="spc-head">
        <span class="spc-title">${isD ? "DELAY" : "REVERB"}</span>
        <span class="spc-sub">${esc(types[st.type].name)}</span>
        <button type="button" class="spc-sw ${st.on ? "on" : ""}" data-part="${part}" data-power title="Zapnout / vypnout"><i></i></button>
      </div>
      <canvas class="spc-cv" data-cv="${part}"></canvas>
      <div class="spc-types">${Object.entries(types).map(([k, t]) =>
        `<button type="button" data-part="${part}" data-type="${k}" class="${st.type === k ? "on" : ""}" title="${esc(t.tip)}">${icon(k)}<span>${esc(t.name)}</span></button>`).join("")}</div>
      <div class="spc-presets">${presets.map((p, i) =>
        `<button type="button" data-part="${part}" data-preset="${i}" title="${esc(p.tip)}">${esc(p.name)}</button>`).join("")}</div>
      <div class="spc-ctl">${controls}</div>
    </section>`;
  }

  function render(){
    if(!win) return;
    const fx = win.fx;
    win.body.innerHTML = `
      <div class="spc-sets"><span>Sady</span>${SETS.map((x, i) => `<button type="button" data-set="${i}" title="${esc(x.tip)}">${esc(x.name)}</button>`).join("")}</div>
      <div class="spc-cards">${cardHtml(fx, "delay")}${cardHtml(fx, "reverb")}</div>
      <div class="spc-foot">Delay jde před reverbem, jako na pedalboardu. Nastavení se ukládá s písní.</div>`;
    wire();
    draw();
  }

  function wire(){
    const fx = win.fx, b = win.body;
    b.querySelectorAll("[data-power]").forEach(x => x.onclick = () => { const p = x.dataset.part; change(fx, p, {on: !stateOf(fx)[p].on}); });
    b.querySelectorAll("[data-type]").forEach(x => x.onclick = () => change(fx, x.dataset.part, {type: x.dataset.type, on: true}));
    b.querySelectorAll("[data-preset]").forEach(x => x.onclick = () => {
      const p = x.dataset.part, list = p === "delay" ? DELAY_PRESETS : REVERB_PRESETS;
      change(fx, p, {...list[+x.dataset.preset].p, on: true});
    });
    b.querySelectorAll("[data-set]").forEach(x => x.onclick = () => {
      const set = SETS[+x.dataset.set], s = stateOf(fx);
      Object.assign(s.delay, set.d); Object.assign(s.reverb, set.r);
      change(fx, "delay", {});
    });
    b.querySelectorAll("[data-sync]").forEach(x => x.onclick = () => {
      const d = stateOf(fx).delay;
      change(fx, "delay", d.sync ? {sync: false, ms: Math.round(delayMs(d))} : {sync: true});
    });
    b.querySelectorAll("[data-div]").forEach(x => x.onclick = () => change(fx, "delay", {div: x.dataset.div}));
    b.querySelectorAll('input[type="range"]').forEach(inp => {
      inp.oninput = () => {
        const part = inp.dataset.part, key = inp.dataset.key, st = stateOf(fx)[part];
        st[key] = +inp.value;
        if(fx.space) apply(fx); else attach(fx);
        const lab = inp.parentElement.querySelector("b");
        const fmt = {ms: v => Math.round(v) + " ms", decay: v => (+v).toFixed(1).replace(".", ",") + " s", pre: v => Math.round(v) + " ms",
          lowcut: v => Math.round(v) + " Hz", tone: v => v < 0.33 ? "tmavá" : v < 0.66 ? "střední" : "jasná"}[key] || (v => Math.round(v * 100) + " %");
        lab.textContent = fmt(+inp.value);
        draw();
      };
      inp.onchange = () => { H().save(); };
    });
  }

  // ---- drawing: the echoes as they come back, the tail as it dies away; both glow with what sounds
  function fitCanvas(cv){
    const dpr = Math.min(window.devicePixelRatio || 1, 2), W = cv.clientWidth, Hh = cv.clientHeight;
    if(!W || !Hh) return null;
    if(cv.width !== Math.floor(W * dpr)){ cv.width = Math.floor(W * dpr); cv.height = Math.floor(Hh * dpr); }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, Hh);
    return {g, W, H: Hh};
  }
  function level(an){
    if(!an) return 0;
    const a = new Float32Array(an.fftSize); an.getFloatTimeDomainData(a);
    let s = 0; for(let i = 0; i < a.length; i++) s += a[i] * a[i];
    return Math.min(1, Math.sqrt(s / a.length) * 6);
  }
  function rgba(hex, a){ const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

  function drawDelay(cv, fx, t){
    const c = fitCanvas(cv); if(!c) return;
    const {g, W, H: Hh} = c, d = stateOf(fx).delay, ty = DELAY_TYPES[d.type], col = ty.color;
    const ms = delayMs(d), span = Math.max(1200, Math.min(4000, ms * 8)), x = (m) => 10 + (m / span) * (W - 20);
    const mid = ty.stereo ? Hh / 2 : Hh - 16, top = 12, full = ty.stereo ? Hh / 2 - top : Hh - 16 - top;
    g.strokeStyle = "rgba(255,255,255,.06)"; g.lineWidth = 1;
    for(let m = 0; m <= span; m += 250){ g.beginPath(); g.moveTo(x(m) + 0.5, top - 4); g.lineTo(x(m) + 0.5, Hh - 4); g.stroke(); }
    g.strokeStyle = "rgba(255,255,255,.18)"; g.beginPath(); g.moveTo(8, mid + 0.5); g.lineTo(W - 8, mid + 0.5); g.stroke();
    g.fillStyle = "rgba(255,255,255,.35)"; g.font = "9px sans-serif"; g.textBaseline = "bottom";
    g.fillText("0", 8, Hh - 2); g.textAlign = "right"; g.fillText((span / 1000).toFixed(1).replace(".", ",") + " s", W - 8, Hh - 2); g.textAlign = "left";
    if(ty.stereo){ g.fillText("L", 2, top + 8); g.fillText("R", 2, Hh - 4); }
    // the dry hit
    g.fillStyle = "rgba(255,255,255,.75)"; g.fillRect(x(0) - 1.5, mid - full, 3, full);
    const glow = d.on ? level(fx.space && fx.space.d.meter) : 0;
    const cut = d.tone * (1 - ty.dark * 0.6);
    let amp = d.mix * 1.2, k = 1;
    const travel = (t / 1000) % (span / 1000) * 1000;
    for(let m = ms; m < span && amp > 0.01; m += ms, k++){
      const h = Math.min(1, amp) * full, side = ty.stereo ? (k % 2 ? -1 : 1) : -1, xx = x(m);
      const dark = Math.pow(0.55 + cut * 0.45, k - 1);
      const gr = g.createLinearGradient(0, mid, 0, mid + side * h);
      gr.addColorStop(0, rgba(col, (d.on ? 0.25 : 0.12) * dark + 0.05)); gr.addColorStop(1, rgba(col, (d.on ? 0.95 : 0.35) * (0.4 + dark * 0.6)));
      g.fillStyle = gr;
      const wv = 7 + (ty.wow * d.mod) * 4;
      g.fillRect(xx - wv / 2, side < 0 ? mid - h : mid, wv, h);
      if(d.on && Math.abs(travel - m) < ms * 0.25){ g.fillStyle = rgba(col, 0.35 + glow * 0.6); g.beginPath(); g.arc(xx, side < 0 ? mid - h : mid + h, 4 + glow * 5, 0, Math.PI * 2); g.fill(); }
      amp *= clamp(d.fb, 0, 0.92) * (d.type === "analog" || d.type === "tape" ? 0.95 : 1);
    }
    if(glow > 0.02){ g.fillStyle = rgba(col, glow * 0.18); g.fillRect(0, 0, W, Hh); }
    g.fillStyle = col; g.font = "bold 11px sans-serif"; g.textBaseline = "top";
    g.fillText(`${Math.round(ms)} ms${d.sync ? " · " + d.div : ""}`, 10, 2);
  }

  const envCache = new WeakMap();
  function envelope(buf, n){
    let e = envCache.get(buf); if(e && e.length === n) return e;
    e = new Float32Array(n); const a = buf.getChannelData(0), b = buf.getChannelData(1), per = Math.max(1, Math.floor(a.length / n));
    let mx = 1e-9;
    for(let i = 0; i < n; i++){ let s = 0; for(let j = i * per; j < (i + 1) * per && j < a.length; j++) s += a[j] * a[j] + b[j] * b[j]; e[i] = Math.sqrt(s / per); mx = Math.max(mx, e[i]); }
    for(let i = 0; i < n; i++) e[i] /= mx;
    envCache.set(buf, e); return e;
  }
  function drawReverb(cv, fx, t){
    const c = fitCanvas(cv); if(!c) return;
    const {g, W, H: Hh} = c, r = stateOf(fx).reverb, col = "#b48cff", R = fx.space && fx.space.r;
    const span = Math.max(1.5, r.decay * 1.25 + r.pre / 1000), x = (s) => 10 + (s / span) * (W - 20), base = Hh - 14, top = 14;
    g.strokeStyle = "rgba(255,255,255,.06)";
    for(let s = 0; s <= span; s += span > 6 ? 1 : 0.5){ g.beginPath(); g.moveTo(x(s) + 0.5, top - 6); g.lineTo(x(s) + 0.5, base); g.stroke(); }
    // −60 dB line where the decay ends
    g.strokeStyle = "rgba(255,255,255,.22)"; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x(r.pre / 1000 + r.decay) + 0.5, top - 6); g.lineTo(x(r.pre / 1000 + r.decay) + 0.5, base); g.stroke(); g.setLineDash([]);
    // pre-delay gap
    if(r.pre > 0){ g.fillStyle = "rgba(255,255,255,.05)"; g.fillRect(x(0), top - 6, x(r.pre / 1000) - x(0), base - top + 6); }
    g.fillStyle = "rgba(255,255,255,.75)"; g.fillRect(x(0) - 1.5, top, 3, base - top);
    const buf = R && R.conv.buffer && R.key === r.type + ":" + r.decay.toFixed(2) ? R.conv.buffer : null;
    const n = Math.max(40, Math.floor((x(span) - x(r.pre / 1000)) / 2));
    const env = buf ? envelope(buf, n) : null, dur = buf ? buf.duration : r.decay * 1.25;
    const glow = r.on ? level(R && R.meter) : 0;
    const gr = g.createLinearGradient(0, top, 0, base);
    gr.addColorStop(0, rgba(col, r.on ? 0.85 : 0.3)); gr.addColorStop(1, rgba(col, 0.04));
    g.fillStyle = gr; g.beginPath(); g.moveTo(x(r.pre / 1000), base);
    for(let i = 0; i < n; i++){
      const s = r.pre / 1000 + (i / n) * dur;
      if(s > span) break;
      const v = env ? Math.pow(env[i], 0.5) : Math.exp(-3.45 * (i / n) * dur / r.decay);
      g.lineTo(x(s), base - v * (base - top) * (0.35 + r.mix * 0.65));
    }
    g.lineTo(x(Math.min(span, r.pre / 1000 + dur)), base); g.closePath(); g.fill();
    // the tone: a bright line on top for a bright reverb, none for a dark one
    g.strokeStyle = rgba("#ffffff", 0.1 + r.tone * 0.35); g.lineWidth = 1; g.stroke();
    if(glow > 0.02){ const rg = g.createRadialGradient(x(r.pre / 1000), base, 4, x(r.pre / 1000), base, W * 0.6);
      rg.addColorStop(0, rgba(col, glow * 0.45)); rg.addColorStop(1, rgba(col, 0)); g.fillStyle = rg; g.fillRect(0, 0, W, Hh); }
    g.fillStyle = "rgba(255,255,255,.35)"; g.font = "9px sans-serif"; g.textBaseline = "bottom";
    g.fillText("0", 8, Hh - 1); g.textAlign = "right"; g.fillText(span.toFixed(1).replace(".", ",") + " s", W - 8, Hh - 1); g.textAlign = "left";
    g.fillStyle = col; g.font = "bold 11px sans-serif"; g.textBaseline = "top";
    g.fillText(`${r.decay.toFixed(1).replace(".", ",")} s${r.pre ? " · předzpoždění " + Math.round(r.pre) + " ms" : ""}`, 10, 1);
  }

  function draw(){
    if(!win) return;
    const t = performance.now();
    const a = win.body.querySelector('[data-cv="delay"]'), b = win.body.querySelector('[data-cv="reverb"]');
    if(a) drawDelay(a, win.fx, t);
    if(b) drawReverb(b, win.fx, t);
  }
  function loop(){ if(!win) return; draw(); raf = requestAnimationFrame(loop); }

  function open(fx, opts = {}){
    if(!H()){ return; }
    if(!fx){ if(typeof showToast === "function") showToast("Živá kytara ještě není zapnutá"); return; }
    stateOf(fx);
    if(!win){
      const el = document.createElement("div");
      el.className = "spc-win";
      el.innerHTML = `<div class="spc-bar"><b>Delay & Reverb</b><span class="spc-who"></span><span class="sp"></span>
        <button type="button" class="spc-x" title="Zavřít">✕</button></div><div class="spc-body"></div>`;
      document.body.appendChild(el);
      win = {el, body: el.querySelector(".spc-body"), fx};
      el.querySelector(".spc-x").onclick = close;
      drag(el, el.querySelector(".spc-bar"));
    }
    win.fx = fx;
    win.el.querySelector(".spc-who").textContent = opts.title || "živá kytara";
    win.el.hidden = false;
    render();
    cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
  }
  function close(){ if(!win) return; cancelAnimationFrame(raf); win.el.remove(); win = null; }
  function drag(el, bar){
    bar.addEventListener("pointerdown", (e) => {
      if(e.target.closest("button")) return;
      const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      const mv = (ev) => { el.style.left = clamp(ev.clientX - dx, 0, innerWidth - 80) + "px"; el.style.top = clamp(ev.clientY - dy, 0, innerHeight - 40) + "px"; };
      const up = () => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); };
      addEventListener("pointermove", mv); addEventListener("pointerup", up);
    });
  }

  // The tempo moved (another song, the speed): a synced delay follows.
  const attached = new Set();
  function retime(fx){ if(fx && fx.space && stateOf(fx).delay.sync) apply(fx); }
  function retimeAll(){ attached.forEach(retime); if(win) render(); }
  function summary(fx){
    const s = fx && fx.state.space; if(!s) return {delay: false, reverb: false};
    return {delay: !!(s.delay && s.delay.on), reverb: !!(s.reverb && s.reverb.on),
            delayName: s.delay ? (DELAY_TYPES[s.delay.type] || {}).name : "", reverbName: s.reverb ? (REVERB_TYPES[s.reverb.type] || {}).name : ""};
  }
  function wants(saved){ return !!(saved && ((saved.delay && saved.delay.on) || (saved.reverb && saved.reverb.on))); }

  return {attach, apply, open, close, retime, retimeAll, summary, wants, impulse, delayMs,
          DELAY_TYPES, REVERB_TYPES, DELAY_PRESETS, REVERB_PRESETS, SETS};
})();
