// ======================================================================
// Virtuální kytara — a riff written into a tab (or pasted as a text tab), played in the sound
// of a song's guitar: the strings are plucked (GuitarSampler, as the tab guide does) and go
// through the song's tone print (Tone.buildChain: drive, EQ, the NAM core, the print's colour,
// its room) — or a clean guitar. Tools → Virtuální kytara; riffs are kept (vguitar.py).
//
// The tab: a grid, strings top (high e) to bottom, a column a step (an 8th, a 16th, a triplet…).
// A cell holds a fret, or x (a dead note); a column can be palm muted (PM), a note legato
// (hammer-on / pull-off, picked softer). A note rings until the string plays again (at most two beats).
// ======================================================================
const VGuitar = (() => {
  const TUNINGS = [["Standard E", [64, 59, 55, 50, 45, 40]], ["Drop D", [64, 59, 55, 50, 45, 38]], ["Eb Standard", [63, 58, 54, 49, 44, 39]],
    ["D Standard", [62, 57, 53, 48, 43, 38]], ["Drop C#", [63, 58, 54, 49, 44, 37]], ["Drop C", [62, 57, 53, 48, 43, 36]],
    ["C Standard", [60, 55, 51, 46, 41, 36]], ["Drop B", [61, 56, 52, 47, 42, 35]], ["7 strun (B)", [64, 59, 55, 50, 45, 40, 35]]];
  const DIVS = [[2, "1/8"], [4, "1/16"], [3, "1/8 triola"], [6, "1/16 triola"]];
  const NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const KEY = "pick-vguitar";
  const enc = encodeURIComponent, esc = (s) => escapeHtml(String(s == null ? "" : s));
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const post = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const blank = (n) => Array.from({length: n}, () => ({s: {}, pm: false, lg: []}));
  const fresh = () => ({id: "", name: "", bpm: 120, div: 4, tuning: TUNINGS[0][1].slice(), cols: blank(32), sound: null});

  let riff = fresh(), cur = {c: 0, s: 5}, root = null, shown = false, prints = [], saved = [], dirty = false, kpresets = [], midiOuts = [], midi = null;
  let opts = {trim: 0, vol: 0.8, click: false, loop: true};
  try{ const k = JSON.parse(localStorage.getItem(KEY) || "{}"); if(k.riff && k.riff.cols) riff = k.riff; Object.assign(opts, k.opts || {}); }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify({riff, opts})); }catch(e){} };
  const strName = (m) => NOTE[m % 12];
  const tuningName = (t) => (TUNINGS.find(([, x]) => x.join() === t.join()) || [t.slice().reverse().map(strName).join(" ")])[0];

  // ---------------------------------------------------------------- a text tab, read
  // "e|---0---3---|" lines, six (or four to eight) together; a run of digits is one fret; x a dead
  // note; h / p before a note makes it legato; a "PM----" line over the strings mutes those columns.
  // The tightest distance between two notes is one step.
  function parseTab(text){
    const lines = String(text || "").replace(/\t/g, "    ").split(/\r?\n/);
    const isTab = (l) => /\|/.test(l) && (l.match(/-/g) || []).length >= 4 && /^\s*([A-Ga-g][#b]?)?\s*[|:-]/.test(l);
    const blocks = [];
    for(let i = 0; i < lines.length; i++){
      if(!isTab(lines[i])) continue;
      const b = {lines: [], above: i > 0 && !isTab(lines[i - 1]) ? lines[i - 1] : ""};
      while(i < lines.length && isTab(lines[i])) b.lines.push(lines[i++]);
      if(b.lines.length >= 4 && b.lines.length <= 8) blocks.push(b);
    }
    if(!blocks.length) return null;
    const n = blocks[0].lines.length, names = blocks[0].lines.map((l) => (l.match(/^\s*([A-Ga-g][#b]?)/) || [])[1] || "");
    const cols = [];
    for(const b of blocks.filter((x) => x.lines.length === n)){
      const start = b.lines.map((l) => l.indexOf("|") >= 0 && l.indexOf("|") < 6 ? l.indexOf("|") + 1 : (l.match(/^\s*[A-Ga-g]?[#b]?\s*/) || [""])[0].length);
      const body = b.lines.map((l, k) => l.slice(start[k]));
      const events = [];                                   // {pos, str, fret, lg}
      body.forEach((l, str) => {
        for(let p = 0; p < l.length; p++){
          const ch = l[p];
          if(/[0-9]/.test(ch)){
            let q = p + 1;
            if(q < l.length && /[0-9]/.test(l[q]) && +(ch + l[q]) <= 24) q++;
            events.push({pos: p, str, fret: +l.slice(p, q), lg: /[hp]/i.test(l[p - 1] || "")});
            p = q - 1;
          } else if(ch === "x" || ch === "X") events.push({pos: p, str, fret: "x", lg: false});
        }
      });
      if(!events.length) continue;
      // the bars are not time: positions counted without them (they line up across the strings)
      const bars = new Set(); body.forEach((l) => { for(let p = 0; p < l.length; p++) if(l[p] === "|") bars.add(p); });
      const eff = (pos) => pos - [...bars].filter((b2) => b2 < pos).length;
      const at = [...new Set(events.map((e) => eff(e.pos)))].sort((a, b2) => a - b2);
      const gaps = at.slice(1).map((x, i) => x - at[i]).filter((g) => g > 0);
      const unit = Math.max(1, gaps.length ? Math.min(...gaps) : 2);
      const len = Math.max(...body.map((l) => eff(l.length)));
      const steps = Math.max(1, Math.round((len - at[0]) / unit));
      const mine = blank(steps);
      events.forEach((e) => { const k = Math.min(steps - 1, Math.round((eff(e.pos) - at[0]) / unit)); mine[k].s[e.str] = e.fret; if(e.lg) mine[k].lg.push(e.str); });
      // palm mute over the strings: "PM----" (or "P.M. - - -")
      const pm = b.above || "", m = pm.match(/P\.?\s?M\.?/i);
      if(m){                                             // its columns counted as the strings' are (from their first |)
        let endp = m.index + m[0].length;
        while(endp < pm.length && /[-.=_~|]/.test(pm[endp])) endp++;
        for(let p = m.index - start[0]; p < endp - start[0]; p++){ const k = Math.round((eff(Math.max(0, p)) - at[0]) / unit); if(k >= 0 && k < steps) mine[k].pm = true; }
      }
      cols.push(...mine);
    }
    if(!cols.length) return null;
    // the tuning by the strings' names, when they say one we know
    let tuning = null;
    if(names.every(Boolean)){
      const flat = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"};
      const want = names.map((x) => { const u = x.toUpperCase(); return flat[u] || u; });
      tuning = (TUNINGS.find(([, t]) => t.length === n && t.every((mm, i) => strName(mm) === want[i])) || [])[1] || null;
    }
    return {cols, strings: n, tuning};
  }
  function toText(){
    const t = riff.tuning, lines = t.map((m) => strName(m).padEnd(2, " ") + "|");
    riff.cols.forEach((c, k) => {
      const w = Math.max(1, ...t.map((_, s) => String(c.s[s] == null ? "" : c.s[s]).length));
      t.forEach((_, s) => { const v = c.s[s] == null ? "" : String(c.s[s]); lines[s] += (v || "").padEnd(w, "-") + "-"; });
      if((k + 1) % (riff.div * 4) === 0) t.forEach((_, s) => { lines[s] += "|"; });
    });
    const pm = riff.cols.some((c) => c.pm) ? "PM " + riff.cols.map((c, k) => { const w = Math.max(1, ...t.map((_, s) => String(c.s[s] == null ? "" : c.s[s]).length)) + 1; return (c.pm ? "=" : " ").repeat(w) + ((k + 1) % (riff.div * 4) === 0 ? " " : ""); }).join("") + "\n" : "";
    return pm + lines.map((l) => l.endsWith("|") ? l : l + "|").join("\n");
  }

  // ---------------------------------------------------------------- the sound
  let ac = null, out = null, trim = null, chain = null, chainKey = "", clean = null, loading = false;
  function graph(){
    const v = Mixer.voiceOut();
    if(ac !== v.ac){
      ac = v.ac;
      trim = ac.createGain(); out = ac.createGain();
      out.connect(v.out);
      // no print: a clean guitar, the pick's fizz taken off and a little body
      const hp = ac.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 80;
      const lp = ac.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 5200;
      const body = ac.createBiquadFilter(); body.type = "peaking"; body.frequency.value = 220; body.gain.value = 3; body.Q.value = 0.8;
      hp.connect(lp); lp.connect(body);
      clean = {input: hp, out: body};
      chain = null; chainKey = "";
    }
    trim.gain.value = Math.pow(10, opts.trim / 20);
    out.gain.value = opts.vol;
    return ac;
  }
  const soundKind = () => !riff.sound ? "clean" : riff.sound.kontakt ? "kontakt" : riff.sound.midi ? "midi" : "print";
  async function useSound(){
    graph();
    if(soundKind() === "kontakt" || soundKind() === "midi") return;
    const want = riff.sound ? `${riff.sound.song}|${riff.sound.pid}` : "";
    if(want === chainKey && (chain || !want)) return;
    trim.disconnect();
    if(chain){ try{ chain.out.disconnect(); }catch(e){} chain.dispose(); chain = null; }
    clean.out.disconnect();
    chainKey = want;
    if(!want){ trim.connect(clean.input); clean.out.connect(out); return; }
    loading = true; paintSound();
    try{
      chain = await Tone.buildChain(riff.sound.song, riff.sound.pid);
      // the plucked string is a quiet pickup: up to where a guitar meets the print's drive
      chain.input.gain.value = Math.pow(10, (chain.print.drive_db + (chain.print.gain_db || 0) + 6) / 20);
      trim.connect(chain.input); chain.out.connect(out);
    }catch(e){
      showToast(e.message || "Zvuk otisku se nepodařilo načíst — hraje čistá kytara.");
      chainKey = ""; riff.sound = null; trim.connect(clean.input); clean.out.connect(out);
    }
    loading = false; paintSound();
  }

  // ---------------------------------------------------------------- playing
  let play = null;                                       // {t0, step, next, timer, from, to}
  const stepDur = () => 60 / riff.bpm / riff.div;
  function noteLen(k, s){
    let j = k + 1;
    while(j < riff.cols.length && riff.cols[j].s[s] == null && j - k < riff.div * 2) j++;
    return (j - k) * stepDur();
  }
  function schedule(){
    if(!play) return;
    const until = ac.currentTime + 0.15;
    while(play.next < until){
      const k = play.step, c = riff.cols[k];
      if(opts.click && k % riff.div === 0){
        const o = ac.createOscillator(), g = ac.createGain();
        o.frequency.value = k % (riff.div * 4) === 0 ? 1600 : 1100; g.gain.setValueAtTime(0.25, play.next); g.gain.exponentialRampToValueAtTime(0.001, play.next + 0.05);
        o.connect(g); g.connect(out); o.start(play.next); o.stop(play.next + 0.06);
      }
      const mo = soundKind() === "midi" ? midiOut() : null;
      Object.entries(c.s).forEach(([s, f]) => {
        const str = +s, dead = f === "x";
        if(mo){ sendNote(mo, riff.tuning[str] + (dead ? 5 : f), dead ? 40 : c.pm ? 85 : c.lg.includes(str) ? 70 : 105, play.next, dead ? 0.04 : c.pm ? Math.min(0.12, stepDur()) : noteLen(k, str)); return; }
        GuitarSampler.playNote(ac, trim, str, dead ? 0 : f, play.next, riff.tuning,
          {duration: dead ? 0.04 : c.pm ? 0.11 : noteLen(k, str), staccato: dead || c.pm, hammer: c.lg.includes(str)}, 9);
      });
      play.marks.push([play.next, k]);
      play.next += stepDur();
      play.step++;
      if(play.step >= play.to){
        if(!opts.loop){ play.ending = play.next; break; }
        play.step = play.from;
      }
    }
    if(play.ending && ac.currentTime > play.ending + 0.3){ stop(); return; }
    play.timer = setTimeout(schedule, 25);
  }
  // ---- through Kontakt: the riff played into a file once (kontakt.py), then that file, looped
  function riffNotes(){
    const n = [], sd = stepDur();
    riff.cols.forEach((c, k) => Object.entries(c.s).forEach(([s, f]) => {
      const str = +s, open = riff.tuning[str];
      if(open == null) return;
      if(f === "x"){ n.push({m: open + 5, t: k * sd, d: 0.04, v: 40}); return; }
      n.push({m: open + f, t: k * sd, d: c.pm ? Math.min(0.12, sd) : noteLen(k, str), v: c.pm ? 85 : c.lg.includes(str) ? 70 : 105});
    }));
    return n;
  }
  let rendering = false;
  async function startKontakt(from){
    const n = riffNotes();
    if(!n.length){ showToast("Riff je prázdný."); return; }
    rendering = true; paintBar();
    const st = root && root.querySelector("#vg-sound-st"); if(st) st.textContent = "Kontakt hraje riff… (poprvé asi půl minuty, pak hned)";
    let buf = null;
    try{
      const r = await api("/api/kontakt/render", post({preset: riff.sound.kontakt, notes: n, length: riff.cols.length * stepDur()}));
      buf = await ac.decodeAudioData(await (await fetch(`/api/kontakt/render/${r.file}`)).arrayBuffer());
    }catch(e){ showToast(e.message || "Kontakt riff nezahrál."); }
    rendering = false; if(st) st.textContent = ""; paintBar();
    if(!buf) return;
    const len = riff.cols.length * stepDur(), off = Math.max(0, Math.min(len - 0.01, (from || 0) * stepDur()));
    const src = ac.createBufferSource(); src.buffer = buf; src.connect(out);
    if(opts.loop){ src.loop = true; src.loopStart = 0; src.loopEnd = Math.min(len, buf.duration); }
    const t0 = ac.currentTime + 0.05;
    src.start(t0, off);
    AudioBus.claim("vguitar"); window.dispatchEvent(new Event("pick-sound"));
    play = {src, t0: t0 - off, len, marks: [], timer: 0, raf: 0, kontakt: true};
    src.onended = () => { if(play && play.src === src) stop(); };
    const head = () => { if(!play) return; const at = (ac.currentTime - play.t0) % (opts.loop ? play.len : Infinity);
      mark(at >= 0 && at < play.len ? Math.floor(at / stepDur()) : -1); play.raf = requestAnimationFrame(head); };
    head(); paintBar();
  }
  // ---- live MIDI out (to Kontakt or Gig Performer through the IAC bus): the notes as they come
  async function midiAccess(quiet){
    if(midi) return midi;
    if(!navigator.requestMIDIAccess) return null;
    try{ midi = await navigator.requestMIDIAccess(); midi.onstatechange = () => { midiOuts = [...midi.outputs.values()]; }; midiOuts = [...midi.outputs.values()]; }
    catch(e){ if(!quiet) showToast("Přístup k MIDI zamítnut"); }
    return midi;
  }
  const midiOut = () => midiOuts.find((o) => o.id === (riff.sound || {}).midi) || null;
  const perf = (t) => performance.now() + (t - ac.currentTime) * 1000;
  function sendNote(o, m, v, t, d){
    if(m < 0 || m > 127) return;
    try{ o.send([0x90, m, v], perf(t)); o.send([0x80, m, 0], perf(t + d)); }catch(e){}
  }

  async function start(from){
    if(play) stop();
    graph();
    if(ac.state === "suspended") await ac.resume();
    if(soundKind() === "kontakt"){ await startKontakt(from); return; }
    if(soundKind() === "midi" && !midiOut()){ await midiAccess(); if(!midiOut()){ showToast("Ten MIDI výstup teď není připojený."); return; } }
    await useSound();
    AudioBus.claim("vguitar");
    window.dispatchEvent(new Event("pick-sound"));
    const f = Math.max(0, Math.min(riff.cols.length - 1, from || 0));
    play = {step: f, from: opts.loop ? 0 : f, to: riff.cols.length, next: ac.currentTime + 0.08, marks: [], timer: 0, raf: 0, ending: 0};
    schedule();
    const head = () => { if(!play) return; while(play.marks.length > 1 && play.marks[1][0] <= ac.currentTime) play.marks.shift();
      const m = play.marks[0]; if(m && m[0] <= ac.currentTime) mark(m[1]); play.raf = requestAnimationFrame(head); };
    head();
    paintBar();
  }
  function stop(){
    if(!play) return;
    clearTimeout(play.timer); cancelAnimationFrame(play.raf);
    if(play.src){ try{ play.src.stop(); }catch(e){} }
    play = null;
    if(ac) GuitarSampler.stopAll(ac);
    const mo = midiOut(); if(mo){ try{ mo.send([0xB0, 123, 0]); mo.send([0xB0, 120, 0]); }catch(e){} }
    mark(-1); paintBar();
    window.dispatchEvent(new Event("pick-sound"));
  }
  AudioBus.register("vguitar", stop, () => !!play);

  // ---------------------------------------------------------------- the page
  function css(){
    if(document.getElementById("vg-css")) return;
    const s = document.createElement("style"); s.id = "vg-css";
    s.textContent = `
      .vg-page{padding:4px 2px 30px;display:flex;flex-direction:column;gap:12px}
      .vg-title{font-size:22px;font-weight:800;margin:0}
      .vg-sub{color:var(--sub);font-size:13px;margin:-6px 0 0}
      .vg-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13px}
      .vg-bar label{display:flex;gap:6px;align-items:center;color:var(--sub)}
      .vg-bar select,.vg-bar input[type=number],.vg-bar input[type=text]{background:#0d0e17;border:1px solid #33364a;border-radius:8px;color:var(--text);font:inherit;padding:5px 8px}
      .vg-bar input[type=number]{width:64px}.vg-bar input[type=text]{min-width:180px}
      .vg-bar .btn{border-radius:500px;padding:6px 14px;font-size:12.5px}
      .vg-bar input[type=range]{width:90px;accent-color:var(--accent)}
      .vg-play{min-width:110px}
      .vg-wrap{overflow-x:auto;border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:10px 10px 6px;background:rgba(0,0,0,.25);outline:none}
      .vg-wrap:focus{border-color:color-mix(in srgb,var(--accent) 60%,transparent)}
      .vg-grid{display:grid;gap:0;width:max-content;font-family:ui-monospace,Menlo,monospace;font-size:13px}
      .vg-cell{width:26px;height:24px;display:flex;align-items:center;justify-content:center;position:relative;cursor:pointer;user-select:none;color:var(--text)}
      .vg-cell::before{content:"";position:absolute;left:0;right:0;top:50%;border-top:1px solid rgba(255,255,255,.22);z-index:0}
      .vg-cell span{position:relative;z-index:1;background:#14161f;padding:0 2px;border-radius:3px;min-width:12px;text-align:center}
      .vg-cell.lg span{color:#7fd1ff}.vg-cell.x span{color:#ff9a6b}
      .vg-cell.beat{border-left:1px solid rgba(255,255,255,.08)}.vg-cell.bar{border-left:1px solid rgba(255,255,255,.45)}
      .vg-cell.cur{outline:2px solid var(--accent);outline-offset:-2px;border-radius:5px}
      .vg-cell.now{background:color-mix(in srgb,var(--accent) 28%,transparent)}
      .vg-name{width:30px;height:24px;display:flex;align-items:center;color:var(--sub);font-size:12px}
      .vg-pm{height:18px;font-size:10px;color:var(--sub);cursor:pointer}.vg-pm.on{color:#ffcf6b;font-weight:700}
      .vg-num{height:16px;font-size:10px;color:var(--sub)}
      .vg-keys{color:var(--sub);font-size:12px;line-height:1.6}
      .vg-keys b{color:var(--text);font-weight:600}
      .vg-riffs{display:flex;flex-wrap:wrap;gap:6px}
      .vg-riffs button{border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.04);color:var(--text);border-radius:500px;font:inherit;font-size:12px;padding:4px 10px;cursor:pointer}
      .vg-riffs button.on{border-color:var(--accent)}
      .vg-riffs .del{opacity:.6;padding:4px 7px}`;
    document.head.appendChild(s);
  }
  async function show(container){
    root = container; shown = true; css();
    try{ prints = await Tone.prints(); }catch(e){ prints = []; }
    try{ saved = (await api("/api/vguitar/riffs")).riffs; }catch(e){ saved = []; }
    try{ kpresets = typeof KontaktPage !== "undefined" ? await KontaktPage.presets() : []; }catch(e){ kpresets = []; }
    await midiAccess(true);
    paint();
  }
  function hide(){ shown = false; stop(); }
  function soundOptions(){
    const sel = !riff.sound ? "" : riff.sound.kontakt ? "k:" + riff.sound.kontakt : riff.sound.midi ? "m:" + riff.sound.midi : `${riff.sound.song}|${riff.sound.pid}`;
    const bands = {};
    prints.forEach((p) => (bands[p.artist || "Bez kapely"] = bands[p.artist || "Bez kapely"] || []).push(p));
    return `<option value="">Čistá kytara (bez otisku)</option>` + Object.entries(bands).map(([b, ps]) => `<optgroup label="${esc(b)}">${ps.map((p) =>
      `<option value="${esc(p.song + "|" + p.id)}" ${sel === p.song + "|" + p.id ? "selected" : ""}>${esc(p.title || p.song)} · ${esc(p.name)}${p.match ? ` (${p.match} %)` : ""}</option>`).join("")}</optgroup>`).join("")
      + (kpresets.length ? `<optgroup label="Kontakt">${kpresets.map((k) => `<option value="k:${esc(k.id)}" ${sel === "k:" + k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("")}</optgroup>` : "")
      + (midiOuts.length ? `<optgroup label="Živě přes MIDI (Kontakt, Gig Performer…)">${midiOuts.map((o) => `<option value="m:${esc(o.id)}" ${sel === "m:" + o.id ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</optgroup>` : "");
  }
  function paint(){
    if(!root || !shown) return;
    const tName = tuningName(riff.tuning);
    root.innerHTML = `<div class="vg-page">
      <h2 class="vg-title">Virtuální kytara</h2>
      <p class="vg-sub">Napiš nebo vlož tab a kytara ho zahraje — zvukem kytary z písně (tónový otisk), nebo čistě.</p>
      <div class="vg-bar">
        <input type="text" id="vg-name" maxlength="80" placeholder="Název riffu" value="${esc(riff.name)}">
        <button type="button" class="btn" id="vg-save">${riff.id ? "Uložit" : "Uložit riff"}</button>
        <button type="button" class="btn secondary" id="vg-new">Nový</button>
        <button type="button" class="btn secondary" id="vg-paste">Vložit tab…</button>
        <button type="button" class="btn secondary" id="vg-copy">Zkopírovat jako tab</button>
      </div>
      ${saved.length ? `<div class="vg-riffs">${saved.map((r) => `<span><button type="button" data-load="${esc(r.id)}" class="${r.id === riff.id ? "on" : ""}">${esc(r.name)}</button><button type="button" class="del" data-del="${esc(r.id)}" title="Smazat riff">✕</button></span>`).join("")}</div>` : ""}
      <div class="vg-bar">
        <button type="button" class="btn vg-play" id="vg-play">${play ? "■ Stop" : "▶ Přehrát"}</button>
        <label>Zvuk <select id="vg-sound">${soundOptions()}</select></label><span id="vg-sound-st" class="vg-sub" style="margin:0"></span>
      </div>
      <div class="vg-bar">
        <label>Tempo <input type="number" id="vg-bpm" min="30" max="300" value="${riff.bpm}"></label>
        <label>Krok <select id="vg-div">${DIVS.map(([d, l]) => `<option value="${d}" ${riff.div === d ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label>Ladění <select id="vg-tuning">${TUNINGS.map(([n, t]) => `<option value="${t.join(",")}" ${t.join() === riff.tuning.join() ? "selected" : ""}>${n}</option>`).join("")}
          ${TUNINGS.some(([, t]) => t.join() === riff.tuning.join()) ? "" : `<option selected value="${riff.tuning.join(",")}">${esc(tName)}</option>`}</select></label>
        <label title="Jak silně struny vstupují do zesilovače otisku">Úhoz <input type="range" id="vg-trim" min="-12" max="12" step="1" value="${opts.trim}"></label>
        <label>Hlasitost <input type="range" id="vg-vol" min="0" max="1.5" step="0.05" value="${opts.vol}"></label>
        <label><input type="checkbox" id="vg-click" ${opts.click ? "checked" : ""}> metronom</label>
        <label><input type="checkbox" id="vg-loop" ${opts.loop ? "checked" : ""}> dokola</label>
        <button type="button" class="btn secondary" id="vg-more" title="Přidat takt na konec">+ takt</button>
        <button type="button" class="btn secondary" id="vg-less" title="Ubrat poslední takt">− takt</button>
      </div>
      <div class="vg-wrap" id="vg-wrap" tabindex="0"><div class="vg-grid" id="vg-grid"></div></div>
      <div class="vg-keys"><b>Klikni do tabu</b> a piš: <b>číslo</b> = pražec (dvouciferné rychle za sebou), <b>x</b> = mrtvá nota, <b>Backspace</b> smaže,
        <b>šipky</b> posouvají, <b>p</b> = tlumení dlaní (PM) celého kroku, <b>h</b> = legato (hammer-on / pull-off), <b>mezerník</b> = přehrát / zastavit od kurzoru.
        Vložený textový tab (třeba zkopírovaný ze stránky s taby) se převede sám — nejkratší vzdálenost mezi dvěma tóny je jeden krok.</div>
    </div>`;
    paintGrid(); wire(); paintSound();
  }
  function paintGrid(){
    const g = root && root.querySelector("#vg-grid");
    if(!g) return;
    const n = riff.cols.length, t = riff.tuning, perBar = riff.div * 4;
    g.style.gridTemplateColumns = `30px repeat(${n}, 26px)`;
    let h = `<div class="vg-num"></div>` + riff.cols.map((_, k) => `<div class="vg-num">${k % perBar === 0 ? k / perBar + 1 : ""}</div>`).join("");
    h += `<div class="vg-pm" title="Tlumení dlaní">PM</div>` + riff.cols.map((c, k) => `<div class="vg-pm ${c.pm ? "on" : ""}" data-pm="${k}">${c.pm ? "▬" : "·"}</div>`).join("");
    t.forEach((m, s) => {
      h += `<div class="vg-name">${s === 0 ? strName(m).toLowerCase() : strName(m)}</div>`;
      h += riff.cols.map((c, k) => { const v = c.s[s];
        return `<div class="vg-cell${k % perBar === 0 ? " bar" : k % riff.div === 0 ? " beat" : ""}${v === "x" ? " x" : ""}${c.lg.includes(s) ? " lg" : ""}${cur.c === k && cur.s === s ? " cur" : ""}" data-c="${k}" data-s="${s}">${v == null ? "" : `<span>${v}</span>`}</div>`; }).join("");
    });
    g.innerHTML = h;
  }
  let nowCol = -1;
  function mark(k){
    if(!root) return;
    root.querySelectorAll(".vg-cell.now").forEach((x) => x.classList.remove("now"));
    nowCol = k;
    if(k >= 0) root.querySelectorAll(`.vg-cell[data-c="${k}"]`).forEach((x) => x.classList.add("now"));
  }
  function paintBar(){ const b = root && root.querySelector("#vg-play"); if(b){ b.textContent = rendering ? "Kontakt hraje…" : play ? "■ Stop" : "▶ Přehrát"; b.disabled = rendering; } }
  function paintSound(){
    const st = root && root.querySelector("#vg-sound-st");
    if(!st) return;
    const p = riff.sound && prints.find((x) => x.song === riff.sound.song && x.id === riff.sound.pid);
    st.textContent = loading ? "Načítám zvuk otisku…" : soundKind() === "kontakt" ? "Kontakt zahraje celý riff do souboru — poprvé to trvá asi půl minuty, pak hraje hned."
      : soundKind() === "midi" ? "Noty jdou živě do MIDI výstupu — zvuk hraje Kontakt (nebo Gig Performer), ne Pick." : p ? `jádro: ${p.core || "bez jádra"}` : prints.length ? "" : "Otisky zatím nemáš — udělej si je ve Steems (kanál Live kytara → OTISK).";
  }
  function changed(){ dirty = true; keep(); }
  function setCell(v){
    const c = riff.cols[cur.c];
    if(v == null) delete c.s[cur.s]; else c.s[cur.s] = v;
    if(v == null || v === "x") c.lg = c.lg.filter((x) => x !== cur.s);
    changed(); paintGrid();
    if(v != null && v !== "x" && !play) preview(cur.c, cur.s);
  }
  async function preview(k, s){
    graph(); if(ac.state === "suspended") await ac.resume();
    if(soundKind() === "kontakt") return;                 // a note at a time would mean half a minute each
    if(soundKind() === "midi"){ const mo = midiOut(), f0 = riff.cols[k].s[s]; if(mo && f0 != null) sendNote(mo, riff.tuning[s] + (f0 === "x" ? 5 : f0), 100, ac.currentTime + 0.01, 0.5); return; }
    await useSound();
    const c = riff.cols[k], f = c.s[s];
    if(f == null) return;
    GuitarSampler.playNote(ac, trim, s, f === "x" ? 0 : f, ac.currentTime + 0.01, riff.tuning, {duration: f === "x" ? 0.04 : 0.6, staccato: f === "x" || c.pm}, 10);
  }
  let typed = "", typedAt = 0;
  function onKey(e){
    if(e.metaKey || e.ctrlKey || e.altKey) return;
    const n = riff.cols.length, strings = riff.tuning.length;
    const move = (dc, ds) => { cur.c = Math.max(0, Math.min(n - 1, cur.c + dc)); cur.s = Math.max(0, Math.min(strings - 1, cur.s + ds)); typed = ""; paintGrid(); scrollTo(); };
    if(e.key === "ArrowRight" || e.key === "Tab"){ e.preventDefault(); move(e.shiftKey && e.key === "Tab" ? -1 : 1, 0); return; }
    if(e.key === "ArrowLeft"){ e.preventDefault(); move(-1, 0); return; }
    if(e.key === "ArrowUp"){ e.preventDefault(); move(0, -1); return; }
    if(e.key === "ArrowDown"){ e.preventDefault(); move(0, 1); return; }
    if(e.key === " "){ e.preventDefault(); if(play) stop(); else start(cur.c); return; }
    if(e.key === "Backspace" || e.key === "Delete"){ e.preventDefault(); typed = ""; setCell(null); return; }
    if(/^[0-9]$/.test(e.key)){
      e.preventDefault();
      const now = Date.now();
      const two = typed && now - typedAt < 700 && +(typed + e.key) <= 24;
      typed = two ? typed + e.key : e.key; typedAt = now;
      setCell(+typed);
      if(two) typed = "";
      return;
    }
    if(e.key === "x" || e.key === "X"){ e.preventDefault(); typed = ""; setCell("x"); return; }
    if(e.key === "p" || e.key === "P"){ e.preventDefault(); riff.cols[cur.c].pm = !riff.cols[cur.c].pm; changed(); paintGrid(); return; }
    if(e.key === "h" || e.key === "H"){ e.preventDefault(); const c = riff.cols[cur.c];
      if(c.s[cur.s] != null && c.s[cur.s] !== "x"){ c.lg = c.lg.includes(cur.s) ? c.lg.filter((x) => x !== cur.s) : [...c.lg, cur.s]; changed(); paintGrid(); } }
  }
  function scrollTo(){
    const w = root.querySelector("#vg-wrap"), x = 30 + cur.c * 26;
    if(x < w.scrollLeft + 40) w.scrollLeft = x - 40; else if(x > w.scrollLeft + w.clientWidth - 60) w.scrollLeft = x - w.clientWidth + 60;
  }
  function wire(){
    const q = (id) => root.querySelector("#" + id);
    const wrap = q("vg-wrap");
    wrap.onkeydown = onKey;
    wrap.onclick = (e) => {
      const c = e.target.closest(".vg-cell"), pm = e.target.closest("[data-pm]");
      if(c){ cur = {c: +c.dataset.c, s: +c.dataset.s}; typed = ""; paintGrid(); wrap.focus(); if(riff.cols[cur.c].s[cur.s] != null) preview(cur.c, cur.s); }
      else if(pm){ const k = +pm.dataset.pm; riff.cols[k].pm = !riff.cols[k].pm; changed(); paintGrid(); }
    };
    q("vg-name").oninput = (e) => { riff.name = e.target.value; changed(); };
    q("vg-play").onclick = () => { if(play) stop(); else start(0); };
    q("vg-sound").onchange = async (e) => {
      const v = e.target.value;
      stop();
      if(v.startsWith("k:")){ riff.sound = {kontakt: v.slice(2)}; changed(); paintSound(); return; }
      if(v.startsWith("m:")){ riff.sound = {midi: v.slice(2)}; changed(); paintSound(); return; }
      const [song, pid] = v ? [v.slice(0, v.lastIndexOf("|")), v.slice(v.lastIndexOf("|") + 1)] : [];
      riff.sound = song ? {song, pid} : null; changed();
      await useSound();
      const s = riff.cols.findIndex((c) => Object.keys(c.s).length);
      if(s >= 0 && !play) preview(s, +Object.keys(riff.cols[s].s)[0]);
    };
    q("vg-bpm").onchange = (e) => { riff.bpm = Math.max(30, Math.min(300, +e.target.value || 120)); e.target.value = riff.bpm; changed(); };
    q("vg-div").onchange = (e) => { riff.div = +e.target.value; changed(); paintGrid(); };
    q("vg-tuning").onchange = (e) => {
      const t = e.target.value.split(",").map(Number);
      if(t.length !== riff.tuning.length) riff.cols.forEach((c) => Object.keys(c.s).forEach((s) => { if(+s >= t.length) delete c.s[s]; }));
      riff.tuning = t; cur.s = Math.min(cur.s, t.length - 1); changed(); paintGrid();
    };
    q("vg-trim").oninput = (e) => { opts.trim = +e.target.value; keep(); if(trim) trim.gain.value = Math.pow(10, opts.trim / 20); };
    q("vg-vol").oninput = (e) => { opts.vol = +e.target.value; keep(); if(out) out.gain.value = opts.vol; };
    q("vg-click").onchange = (e) => { opts.click = e.target.checked; keep(); };
    q("vg-loop").onchange = (e) => { opts.loop = e.target.checked; keep(); };
    q("vg-more").onclick = () => { riff.cols.push(...blank(riff.div * 4)); changed(); paintGrid(); };
    q("vg-less").onclick = () => { const k = riff.div * 4; if(riff.cols.length > k){ riff.cols.splice(-k); cur.c = Math.min(cur.c, riff.cols.length - 1); changed(); paintGrid(); } };
    q("vg-new").onclick = async () => {
      if(dirty && riff.cols.some((c) => Object.keys(c.s).length) && !await Dialog.confirm("Začít nový riff? Neuložené změny se ztratí.", {ok: "Nový"})) return;
      stop(); const snd = riff.sound; riff = fresh(); riff.sound = snd; cur = {c: 0, s: 5}; dirty = false; keep(); paint();
    };
    q("vg-paste").onclick = async () => {
      const text = await Dialog.prompt("Vlož textový tab — řádky strun jako e|---0---3---| (klidně víc řádků pod sebou, i s PM nad nimi):", "",
        {title: "Vložit tab", multiline: true, ok: "Převést"});
      if(!text) return;
      const got = parseTab(text);
      if(!got){ showToast("V textu jsem nenašel tab — řádky strun musí obsahovat | a pomlčky."); return; }
      stop();
      const keepRiff = riff.cols.some((c) => Object.keys(c.s).length) && await Dialog.confirm("Přidat vložený tab na konec, nebo nahradit ten, co je v editoru?", {ok: "Přidat na konec", cancel: "Nahradit"});
      if(got.tuning) riff.tuning = got.tuning;
      else if(got.strings !== riff.tuning.length) riff.tuning = (TUNINGS.find(([, t]) => t.length === got.strings) || TUNINGS[0])[1].slice();
      if(!keepRiff){ riff.cols = got.cols; riff.div = 2; riff.id = ""; }
      else riff.cols.push(...got.cols);
      cur = {c: 0, s: riff.tuning.length - 1}; changed(); paint();
      showToast(`Převedeno: ${got.cols.length} kroků${got.tuning ? `, ladění ${tuningName(got.tuning)}` : ""}. Krok je teď 1/8 — když to hraje moc rychle nebo pomalu, změň Krok nebo Tempo.`);
    };
    q("vg-copy").onclick = async () => { try{ await navigator.clipboard.writeText(toText()); showToast("Tab je ve schránce."); }catch(e){ await Dialog.prompt("Tab:", toText(), {multiline: true, ok: "Zavřít"}); } };
    q("vg-save").onclick = async () => {
      if(!riff.cols.some((c) => Object.keys(c.s).length)){ showToast("Riff je prázdný."); return; }
      let name = riff.name.trim();
      if(!name){ name = await Dialog.prompt("Jak se riff jmenuje?", "", {placeholder: "např. Úvod – Enter Sandman"}); if(!name) return; riff.name = name.trim(); }
      try{ const r = await api("/api/vguitar/riffs", post({id: riff.id, name: riff.name, riff})); riff.id = r.id; dirty = false; keep();
        saved = (await api("/api/vguitar/riffs")).riffs; paint(); showToast(`Riff „${r.name}“ je uložený.`); }
      catch(e){ showToast(e.message); }
    };
    root.querySelectorAll("[data-load]").forEach((b) => b.onclick = async () => {
      const r = saved.find((x) => x.id === b.dataset.load);
      if(!r) return;
      if(dirty && riff.cols.some((c) => Object.keys(c.s).length) && riff.id !== r.id && !await Dialog.confirm("Otevřít jiný riff? Neuložené změny se ztratí.", {ok: "Otevřít"})) return;
      stop(); riff = JSON.parse(JSON.stringify({id: r.id, name: r.name, bpm: r.bpm, div: r.div, tuning: r.tuning, cols: r.cols, sound: r.sound}));
      cur = {c: 0, s: riff.tuning.length - 1}; dirty = false; keep(); paint();
    });
    root.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      const r = saved.find((x) => x.id === b.dataset.del);
      if(!r || !await Dialog.confirm(`Smazat riff „${r.name}“?`, {ok: "Smazat", danger: true})) return;
      try{ await api("/api/vguitar/riffs/" + enc(r.id), {method: "DELETE"}); if(riff.id === r.id) riff.id = ""; saved = (await api("/api/vguitar/riffs")).riffs; keep(); paint(); }
      catch(e){ showToast(e.message); }
    });
  }
  // From the tone print library: this print's sound, right away.
  function withPrint(song, pid){ riff.sound = {song, pid}; keep(); setMode("vguitar"); }
  function withKontakt(id){ riff.sound = {kontakt: id}; keep(); setMode("vguitar"); }

  return {show, hide, withPrint, withKontakt, parseTab, isPlaying: () => !!play};
})();
