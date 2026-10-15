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
  const isTab = (l) => /\|/.test(l) && (l.match(/-/g) || []).length >= 4 && /^\s*([A-Ga-g][#b]?)?\s*[|:-]/.test(l);
  function parseTab(text){
    const lines = String(text || "").replace(/\t/g, "    ").split(/\r?\n/);
    const blocks = [];
    for(let i = 0; i < lines.length; i++){
      if(!isTab(lines[i])) continue;
      const b = {lines: [], above: i > 0 && !isTab(lines[i - 1]) ? lines[i - 1] : ""};
      while(i < lines.length && isTab(lines[i])) b.lines.push(lines[i++].replace(/\|\s+\S.*$/, "|"));   // "|  x3" after the bar: a note, not notes
      b.below = i < lines.length ? lines[i] : ""; i--;
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
      // palm mute over the strings: "PM----" (or "P.M. - - -"), or under them
      const pm = /P\.?\s?M/i.test(b.above) ? b.above : b.below || "", m = pm.match(/P\.?\s?M\.?/i);
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

  // ---------------------------------------------------------------- example riffs, a style each
  // A step a token: "." rests, notes "string:fret" (1 = high e … 6 = low E) joined by "+",
  // "!" after a step palm mutes it, "~" after a note plays it legato. Written for Pick, not taken from songs.
  const PC = (a, b) => `6:${a}+5:${b}+4:${b}`;            // a power chord on the low strings (b = a + 2 in standard)
  const EXAMPLES = [
    {name: "Metal – cval", style: "metal", bpm: 150, div: 4, tuning: [64, 59, 55, 50, 45, 38], steps: (() => {
      const g = "6:0! . 6:0! 6:0!";
      return [g, g, g, g, g, "6:5+5:5+4:5 . . .", g, "6:3+5:3+4:3 . 6:1+5:1+4:1 .",
        g, g, g, g, "6:7+5:7+4:7 . . 6:0!", "6:0! . 6:5+5:5+4:5 .", "6:3+5:3+4:3 . . .", ". . . ."].join(" "); })()},
    {name: "Blues – shuffle v E", style: "blues", bpm: 92, div: 3, tuning: [64, 59, 55, 50, 45, 40], steps: (() => {
      const e = "6:0+5:2 . 6:0+5:2 6:0+5:4 . 6:0+5:4", a = "5:0+4:2 . 5:0+4:2 5:0+4:4 . 5:0+4:4";
      return [e, e, e, e, a, a, e, e, "6:0+5:2 . 6:0+5:2 6:0+5:4 . 6:0+5:2", "5:2 . 5:0 6:3 . 6:0", "6:0+5:2+4:2 . . . . .", ". . . . . ."].join(" "); })()},
    {name: "Funk – škrábání 16tin", style: "funk", bpm: 98, div: 4, tuning: [64, 59, 55, 50, 45, 40], steps: (() => {
      const C = "5:7+4:6+3:7+2:7", X = "5:x+4:x+3:x+2:x";
      return [C, ".", X, C, X, C, X, ".", C, ".", X, X, C, X, C, X,
        C, ".", X, C, X, C, X, ".", "6:0", ".", "6:0", "6:3~", "6:5", ".", X, X].join(" "); })()},
    {name: "Punk – power akordy", style: "punk", bpm: 176, div: 2, tuning: [64, 59, 55, 50, 45, 40], steps: (() => {
      const G = "6:3+5:5", C = "5:3+4:5", D = "5:5+4:7";
      return [...Array(8).fill(G + "!"), ...Array(4).fill(C + "!"), D, D, D, D, ...Array(8).fill(G + "!"), C + "!", C + "!", C + "!", C + "!", D, ".", D, "."].join(" "); })()},
    {name: "Rock – pentatonika v A", style: "rock", bpm: 112, div: 4, tuning: [64, 59, 55, 50, 45, 40], steps: [
      "6:5 . 6:5 6:8", ". 6:5 5:5 .", "5:7 . 5:5 5:7~", "4:5 . 4:7 .",
      "5:7 5:5~ 6:8 .", "6:5 . . .", PC(5, 7) + " . . .", ". . . .",
      "6:5 . 6:5 6:8", ". 6:5 5:5 .", "5:7 . 5:5 5:7~", "4:5 . 4:7 4:5~",
      "3:7 . 3:5 .", "4:7 . 4:5 5:7", PC(5, 7) + " . . .", PC(3, 5) + " . " + PC(5, 7) + " ."].join(" ")},
    {name: "Flamenco – andaluská kadence", style: "flamenco", bpm: 104, div: 2, tuning: [64, 59, 55, 50, 45, 40], steps: [
      "5:0 4:2 3:2 2:1 1:0 2:1 3:2 4:2", "6:3 5:2 4:0 3:0 2:0 3:0 4:0 5:2",
      "6:1 5:3 4:3 3:2 2:1 1:1 2:1 3:2", "6:0 5:2 4:2 3:1 2:0 1:0 2:0 3:1",
      "5:0 4:2 3:2 2:1 1:0 2:1 3:2 4:2", "6:3 5:2 4:0 3:0 2:0 3:0 4:0 5:2",
      "6:1 5:3 4:3 3:2 2:1 1:1 2:1 3:2", "6:0+5:2+4:2+3:1+2:0+1:0 . . . . . . ."].join(" ")},
  ];
  function exampleRiff(ex){
    const cols = ex.steps.trim().split(/\s+/).map((tok) => {
      const c = {s: {}, pm: tok.endsWith("!"), lg: []}, body = tok.replace("!", "");
      if(body !== ".") body.split("+").forEach((nt) => {
        const [str, f] = nt.split(":"), si = +str - 1, v = f.replace("~", "");   // string 1 = the grid's top row
        c.s[si] = v === "x" ? "x" : +v;
        if(f.endsWith("~")) c.lg.push(si);
      });
      return c;
    });
    return {id: "", name: ex.name, bpm: ex.bpm, div: ex.div, tuning: ex.tuning.slice(), cols, sound: riff.sound};
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
      .vg-riffs .del{opacity:.6;padding:4px 7px}
      .vg-riffs .ex{border-style:dashed}
      .vg-sec{border-top:1px solid rgba(255,255,255,.1);padding-top:12px;display:flex;flex-direction:column;gap:10px}
      .vg-sec h3{margin:0;font-size:16px}
      .vg-tabsw{display:flex;gap:6px}.vg-tabsw button{border:1px solid rgba(255,255,255,.15);background:transparent;color:var(--sub);border-radius:500px;font:inherit;font-size:12.5px;padding:5px 12px;cursor:pointer}
      .vg-tabsw button.on{color:var(--text);border-color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent)}
      .vg-res{display:flex;flex-direction:column;max-height:260px;overflow-y:auto;border:1px solid rgba(255,255,255,.08);border-radius:10px}
      .vg-res button{display:grid;grid-template-columns:1fr auto auto;gap:10px;text-align:left;background:transparent;border:0;border-bottom:1px solid rgba(255,255,255,.05);color:var(--text);font:inherit;font-size:13px;padding:7px 10px;cursor:pointer}
      .vg-res button:hover,.vg-res button.on{background:rgba(255,255,255,.06)}
      .vg-res .m{color:var(--sub);font-size:12px}
      .vg-res .row{display:flex}.vg-res .row button:first-child{flex:1}.vg-res .row .del{flex:0 0 auto;display:block;opacity:.6}
      .vg-tabtext{margin:0;max-height:520px;overflow:auto;background:#0b0c14;border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:12px;font:12.5px/1.35 ui-monospace,Menlo,monospace;white-space:pre;user-select:text;cursor:text}
      .vg-tabtext::selection,.vg-tabtext *::selection{background:color-mix(in srgb,var(--accent) 45%,transparent)}`;
    document.head.appendChild(s);
  }
  async function show(container){
    root = container; shown = true; css();
    try{ prints = await Tone.prints(); }catch(e){ prints = []; }
    try{ saved = (await api("/api/vguitar/riffs")).riffs; }catch(e){ saved = []; }
    try{ kpresets = typeof KontaktPage !== "undefined" ? await KontaktPage.presets() : []; }catch(e){ kpresets = []; }
    await midiAccess(true);
    await loadMine();
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
      <div class="vg-riffs"><span class="vg-sub" style="margin:0;align-self:center">Ukázky:</span>${EXAMPLES.map((e, i) => `<button type="button" class="ex${riff.name === e.name && !riff.id ? " on" : ""}" data-ex="${i}" title="${e.bpm} BPM · ${esc(tuningName(e.tuning))}">${esc(e.name)}</button>`).join("")}</div>
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
      <div class="vg-sec" id="vg-tabs"></div>
    </div>`;
    paintGrid(); wire(); paintSound(); paintTabs();
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
      if(text) await takeTab(text);
    };
    root.querySelectorAll("[data-ex]").forEach((b) => b.onclick = async () => {
      if(dirty && riff.cols.some((c) => Object.keys(c.s).length) && !await Dialog.confirm("Otevřít ukázku? Neuložené změny se ztratí.", {ok: "Otevřít"})) return;
      stop(); riff = exampleRiff(EXAMPLES[+b.dataset.ex]); cur = {c: 0, s: riff.tuning.length - 1}; dirty = false; keep(); paint();
      showToast(`Ukázka „${riff.name}“ — ▶ Přehrát. Když ji upravíš, ulož si ji jako vlastní riff.`);
    });
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
  // A text tab into the editor: added at the end, or in place of what is there.
  async function takeTab(text, name){
    const got = parseTab(text);
    if(!got){ showToast(name ? "V tomhle úseku nejsou řádky strun (e|---0---|). Tab typu Akordy riff nemá — zkus verzi Tabs." : "V textu jsem nenašel tab — řádky strun musí obsahovat | a pomlčky."); return false; }
    stop();
    const keepRiff = riff.cols.some((c) => Object.keys(c.s).length) && await Dialog.confirm("Přidat tab na konec riffu, nebo nahradit ten, co je v editoru?", {ok: "Přidat na konec", cancel: "Nahradit"});
    if(got.tuning) riff.tuning = got.tuning;
    else if(got.strings !== riff.tuning.length) riff.tuning = (TUNINGS.find(([, t]) => t.length === got.strings) || TUNINGS[0])[1].slice();
    if(!keepRiff){ riff.cols = got.cols; riff.div = 2; riff.id = ""; if(name) riff.name = name; }
    else riff.cols.push(...got.cols);
    cur = {c: 0, s: riff.tuning.length - 1}; changed(); paint();
    showToast(`Převedeno: ${got.cols.length} kroků${got.tuning ? `, ladění ${tuningName(got.tuning)}` : ""}. Krok je teď 1/8 — když to hraje moc rychle nebo pomalu, změň Krok nebo Tempo.`);
    root.querySelector("#vg-wrap").scrollIntoView({behavior: "smooth", block: "center"});
    return true;
  }

  // ---------------------------------------------------------------- tabs of songs, under the guitar
  // Searched through Freetar (the Ultimate Guitar tabs), or the ones kept in Moje taby. A riff is
  // taken by selecting it in the tab: across the strings, from where it starts to where it ends.
  const TKEY = "pick-vguitar-tabs";
  const KINDS = [["Tabs", "Kytarové taby"], ["Bass", "Basa"], ["Chords", "Akordy"], ["", "Vše"]];
  let tb = {mode: "find", q: "", kind: "Tabs", page: 1, res: null, busy: false, mq: "", mine: [], open: null, opening: false};
  try{ Object.assign(tb, JSON.parse(localStorage.getItem(TKEY) || "{}")); tb.busy = tb.opening = false; }catch(e){}
  const keepTabs = () => { try{ localStorage.setItem(TKEY, JSON.stringify({mode: tb.mode, q: tb.q, kind: tb.kind, page: tb.page, res: tb.res, mq: tb.mq, open: tb.open})); }catch(e){} };
  async function loadMine(){ try{ tb.mine = (await api("/api/vguitar/tabs/mine?q=" + enc(tb.mq))).tabs; }catch(e){ tb.mine = []; } }
  function paintTabs(){
    const box = root && root.querySelector("#vg-tabs");
    if(!box) return;
    const rows = tb.res ? tb.res.results.filter((r) => !tb.kind || r.type === tb.kind) : [];
    const o = tb.open;
    box.innerHTML = `<h3>Taby z písní</h3>
      <div class="vg-tabsw"><button type="button" data-tm="find" class="${tb.mode === "find" ? "on" : ""}">Hledat (Freetar · Ultimate Guitar)</button>
        <button type="button" data-tm="mine" class="${tb.mode === "mine" ? "on" : ""}">Moje taby${tb.mine.length ? ` (${tb.mine.length})` : ""}</button></div>
      ${tb.mode === "find" ? `<div class="vg-bar">
          <input type="text" id="vg-tq" placeholder="Kapela nebo píseň…" value="${esc(tb.q)}">
          <label>Druh <select id="vg-tk">${KINDS.map(([k, l]) => `<option value="${k}" ${k === tb.kind ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <button type="button" class="btn" id="vg-tgo" ${tb.busy ? "disabled" : ""}>${tb.busy ? "Hledám…" : "Hledat"}</button>
          ${tb.busy ? `<span class="vg-sub" style="margin:0">Freetar bývá pomalý — klidně i minutu.</span>` : ""}</div>
        ${tb.res ? `<div class="vg-res">${rows.map((r) => `<button type="button" data-turl="${esc(r.url)}" class="${o && o.url === r.url ? "on" : ""}"><span>${esc(r.artist)} – ${esc(r.song)}${r.version > 1 ? ` <span class="m">ver ${r.version}</span>` : ""}</span>
            <span class="m">★ ${r.rating} (${r.votes})</span><span class="m">${esc(r.type)}</span></button>`).join("") || `<p class="vg-sub" style="padding:8px 10px;margin:0">Nic${tb.kind ? " tohoto druhu — zkus Druh: Vše" : ""}.</p>`}</div>
          ${tb.res.pages > 1 ? `<div class="vg-bar"><button type="button" class="btn secondary" id="vg-tprev" ${tb.page > 1 && !tb.busy ? "" : "disabled"}>← Předchozí</button>
            <span class="vg-sub" style="margin:0">stránka ${tb.page} z ${tb.res.pages}</span>
            <button type="button" class="btn secondary" id="vg-tnext" ${tb.page < tb.res.pages && !tb.busy ? "" : "disabled"}>Další →</button></div>` : ""}` : ""}`
      : `<div class="vg-bar"><input type="text" id="vg-tmq" placeholder="Hledat v mých tabech…" value="${esc(tb.mq)}"></div>
        <div class="vg-res">${tb.mine.map((t) => `<div class="row"><button type="button" data-tid="${esc(t.id)}" class="${o && o.id === t.id ? "on" : ""}"><span>${esc(t.artist ? t.artist + " – " : "")}${esc(t.song)}</span><span></span><span></span></button>
          <button type="button" class="del" data-tdel="${esc(t.id)}" title="Odebrat z Mých tabů">✕</button></div>`).join("")
          || `<p class="vg-sub" style="padding:8px 10px;margin:0">${tb.mq ? "Nic tomu neodpovídá." : "Zatím žádné — otevři tab v Hledat a dej Uložit do Mých tabů."}</p>`}</div>`}
      ${tb.opening ? `<p class="vg-sub" style="margin:0">Otevírám tab…</p>` : o ? `<div class="vg-bar"><b>${esc(o.artist ? o.artist + " – " : "")}${esc(o.song)}</b>
          ${(o.versions || []).length ? `<label>Verze <select id="vg-tver"><option value="">tahle</option>${o.versions.map((v) => `<option value="${esc(v.url)}">${esc(v.label)}</option>`).join("")}</select></label>` : ""}</div>
        <div class="vg-bar"><button type="button" class="btn" id="vg-tsel">Vybraný úsek do kytary</button>
          <button type="button" class="btn secondary" id="vg-tall">Celý tab do kytary</button>
          ${o.id ? `<span class="vg-sub" style="margin:0">✓ v Mých tabech</span>` : `<button type="button" class="btn secondary" id="vg-tkeep">Uložit do Mých tabů</button>`}
          <button type="button" class="btn secondary" id="vg-tclose">Zavřít</button></div>
        <p class="vg-sub" style="margin:0">Riff vybereš myší: táhni přes struny od začátku riffu po jeho konec (stačí i na jedné struně — vezme se celý blok strun v tom rozsahu).</p>
        <pre class="vg-tabtext" id="vg-tabtext">${esc(o.text)}</pre>` : ""}`;
    wireTabs();
  }
  async function findTabs(page){
    if(tb.q.trim().length < 2){ showToast("Napiš aspoň dvě písmena."); return; }
    tb.busy = true; tb.page = page || 1; paintTabs();
    try{ tb.res = await api(`/api/vguitar/tabs/search?q=${enc(tb.q)}&page=${tb.page}`); }catch(e){ showToast(e.message); }
    tb.busy = false; keepTabs(); paintTabs();
  }
  async function openTab(url){
    tb.opening = true; paintTabs();
    try{ const t = await api("/api/vguitar/tabs/open?url=" + enc(url)); const kept = tb.mine.find((m) => m.url === t.url); tb.open = {...t, id: kept ? kept.id : ""}; }
    catch(e){ showToast(e.message); }
    tb.opening = false; keepTabs(); paintTabs();
  }
  // the selection in the tab, as a tab again: the rows of its strings cut where it starts and ends
  function selectedTab(pre){
    const sel = getSelection();
    if(!sel.rangeCount || sel.isCollapsed || !pre.contains(sel.anchorNode) || !pre.contains(sel.focusNode)) return null;
    const rg = sel.getRangeAt(0), off = (node, o) => { const r = document.createRange(); r.setStart(pre, 0); r.setEnd(node, o); return r.toString().length; };
    const a = off(rg.startContainer, rg.startOffset), b = off(rg.endContainer, rg.endOffset);
    const lines = pre.textContent.split("\n"), starts = [];
    let acc = 0; lines.forEach((l) => { starts.push(acc); acc += l.length + 1; });
    const lineOf = (x) => { let i = 0; while(i + 1 < starts.length && starts[i + 1] <= x) i++; return i; };
    const la = lineOf(a), lb = lineOf(Math.max(a, b - 1)), ca = a - starts[la], cb = b - starts[lb];
    let top = la, bot = lb;
    while(top > 0 && isTab(lines[top]) && isTab(lines[top - 1])) top--;
    while(bot + 1 < lines.length && isTab(lines[bot]) && isTab(lines[bot + 1])) bot++;
    const block = lines.slice(top, bot + 1);
    if(!block.every(isTab)) return block.join("\n");            // across more blocks: their whole rows
    const x0 = Math.min(ca, cb), x1 = Math.max(ca, cb);
    const head = (l) => (l.match(/^\s*[A-Ga-g]?[#b]?\s*\|/) || [""])[0];
    const ispm = (l) => /P\.?\s?M/i.test(l || "");
    const above = top > 0 && ispm(lines[top - 1]) ? lines[top - 1] : "", below = bot + 1 < lines.length && ispm(lines[bot + 1]) ? lines[bot + 1] : "";
    if(x1 - x0 < 2) return [above, ...block, below].filter(Boolean).join("\n");
    const cut = block.map((l) => { const h = head(l); return h + l.slice(Math.max(x0, h.length), x1); });
    const pmCut = (line) => {                                     // palm mute over or under the strings, cut the same way
      const w = head(block[0]).length, seg = line.slice(Math.max(x0, w), x1);
      if(!/[^\s|]/.test(seg)) return "";
      return " ".repeat(w) + (ispm(seg) ? seg : /^[-=._~ ]/.test(seg) && /[-=._~]/.test(seg) ? "PM" + seg.slice(2) : seg);
    };
    const pa = above && pmCut(above), pb = below && pmCut(below);
    return [pa, ...cut, pb].filter(Boolean).join("\n");
  }
  function wireTabs(){
    const box = root.querySelector("#vg-tabs"), q = (id) => box.querySelector("#" + id);
    box.querySelectorAll("[data-tm]").forEach((b) => b.onclick = async () => { tb.mode = b.dataset.tm; if(tb.mode === "mine") await loadMine(); keepTabs(); paintTabs(); });
    if(q("vg-tq")){ q("vg-tq").oninput = (e) => { tb.q = e.target.value; }; q("vg-tq").onkeydown = (e) => { if(e.key === "Enter") findTabs(1); }; }
    if(q("vg-tk")) q("vg-tk").onchange = (e) => { tb.kind = e.target.value; keepTabs(); paintTabs(); };
    if(q("vg-tgo")) q("vg-tgo").onclick = () => findTabs(1);
    if(q("vg-tprev")) q("vg-tprev").onclick = () => findTabs(tb.page - 1);
    if(q("vg-tnext")) q("vg-tnext").onclick = () => findTabs(tb.page + 1);
    let t = 0;
    if(q("vg-tmq")) q("vg-tmq").oninput = (e) => { tb.mq = e.target.value; clearTimeout(t); t = setTimeout(async () => { await loadMine(); paintTabs(); const i = q("vg-tmq") || box.querySelector("#vg-tmq"); if(i){ i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 250); };
    box.querySelectorAll("[data-turl]").forEach((b) => b.onclick = () => openTab(b.dataset.turl));
    box.querySelectorAll("[data-tid]").forEach((b) => b.onclick = () => { const m = tb.mine.find((x) => x.id === b.dataset.tid); if(m){ tb.open = {...m, versions: []}; keepTabs(); paintTabs(); } });
    box.querySelectorAll("[data-tdel]").forEach((b) => b.onclick = async () => {
      const m = tb.mine.find((x) => x.id === b.dataset.tdel);
      if(!m || !await Dialog.confirm(`Odebrat „${m.song}“ z Mých tabů?`, {ok: "Odebrat", danger: true})) return;
      try{ await api("/api/vguitar/tabs/mine/" + enc(m.id), {method: "DELETE"}); }catch(e){ showToast(e.message); }
      if(tb.open && tb.open.id === m.id) tb.open.id = "";
      await loadMine(); keepTabs(); paintTabs();
    });
    const o = tb.open, name = o ? `${o.artist ? o.artist + " – " : ""}${o.song}` : "";
    if(q("vg-tver")) q("vg-tver").onchange = (e) => { if(e.target.value) openTab(e.target.value); };
    if(q("vg-tclose")) q("vg-tclose").onclick = () => { tb.open = null; keepTabs(); paintTabs(); };
    if(q("vg-tall")) q("vg-tall").onclick = () => takeTab(o.text, name);
    if(q("vg-tsel")) q("vg-tsel").onclick = () => {
      const part = selectedTab(q("vg-tabtext"));
      if(!part){ showToast("Nejdřív v tabu myší označ riff — přes struny od začátku po konec."); return; }
      takeTab(part, name);
    };
    if(q("vg-tkeep")) q("vg-tkeep").onclick = async () => {
      try{ const r = await api("/api/vguitar/tabs/mine", post({artist: o.artist, song: o.song, text: o.text, url: o.url || ""})); o.id = r.id; await loadMine(); keepTabs(); paintTabs(); showToast("Uloženo do Mých tabů."); }
      catch(e){ showToast(e.message); }
    };
  }

  // From the tone print library: this print's sound, right away.
  function withPrint(song, pid){ riff.sound = {song, pid}; keep(); setMode("vguitar"); }
  function withKontakt(id){ riff.sound = {kontakt: id}; keep(); setMode("vguitar"); }

  return {show, hide, withPrint, withKontakt, parseTab, EXAMPLES, exampleRiff, isPlaying: () => !!play};
})();
