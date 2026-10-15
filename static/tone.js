// Tónový otisk — the sound of the guitar on a record, put onto the player's own guitar
// (server: /api/tone/…, tone_worker.py; docs/specs/2026-10-15-tonovy-otisk-design.md).
//
// A window of its own, out of the live guitar's OTISK button: a stretch of the song's
// guitar (the loop, or one the server suggests), played along once — the guitar is
// recorded clean, on the song's clock — and the server finds the chain that sounds like
// the record. "Hrát s otiskem" puts that chain into the live guitar: drive → EQ → the
// NAM core → the print's IR → room (→ doubler), the very one the fit heard, number for
// number (tone_worker.py builds it alike). A print made with one song can be played with
// any other.
const Tone = (() => {
  const PRE = {mid: 750, low: 250, high: 2000, hpQ: -3.0103};       // tone_worker.py has the same
  const KEEP = "pick-tone-live", POS = "pick-tone-win";
  const STAGES = {listen: "poslouchám originál a tvou nahrávku", sieve: "zkouším jádra zkreslení", sieve2: "zkouším nejlepší jádra",
    tune: "dolaďuji drive a ekvalizér", colour: "počítám barvu", room: "hledám prostor", save: "ukládám"};
  const STEMS = {guitar: "Kytara", guitar_solo: "Sólová kytara", guitar_rhythm: "Rytmická kytara"};
  const enc = encodeURIComponent;
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const post = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const G = () => Mixer.guitar;
  const ac = () => Mixer.voiceOut().ac;
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const cz = (v) => String(v).replace("-", "−").replace(".", ",");
  const num = (v, d = 1) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d).replace(".", ",");
  const title = (name) => { const d = stemsDisplayByName(name); return d.artist ? `${d.artist} — ${d.title}` : d.title || name; };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));

  let win = null, song = null, state = null, range = null, stem = "guitar", sections = [], prints = [];
  let watch = 0, recording = null, playing = null, blindRun = null;
  let live = null;            // the print in the live guitar: {song, print, input, out, nodes, core, dbl}
  let restored = false;       // the print on before a reload put back (or the player chose since)

  // ---------------------------------------------------------------- the live chain
  function biquad(type, f, q, gain){
    const b = ac().createBiquadFilter();
    b.type = type; b.frequency.value = f; b.Q.value = q;
    if(gain != null) b.gain.value = gain;
    return b;
  }
  // The made-up room: the very numbers tone_worker.py draws (mulberry32, seed 1).
  function mulberry32(a){
    return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  function roomBuffer(decay){
    const c = ac(), sr = c.sampleRate, n = Math.floor(sr * Math.min(3.5, 1.5 * decay)), pre = Math.floor(sr * 0.012);
    const rnd = mulberry32(1), h = new Float32Array(pre + n);
    let y = 0, e = 0;
    for(let i = 0; i < n; i++){
      const x = (rnd() * 2 - 1) * Math.exp(-6.907755 * i / sr / decay);
      y = 0.45 * x + 0.55 * y;
      h[pre + i] = y; e += y * y;
    }
    const k = 1 / Math.sqrt(e);
    for(let i = pre; i < h.length; i++) h[i] *= k;
    const buf = c.createBuffer(1, h.length, sr);
    buf.copyToChannel(h, 0);
    return buf;
  }
  // GAIN turned since the take: the drive into the core stays what was fitted.
  function setDrive(){
    if(live) live.input.gain.value = Math.pow(10, (live.print.drive_db + (live.print.gain_db || 0) - (G().state.inputGainDb || 0)) / 20);
  }
  setInterval(() => { if(live) setDrive(); }, 400);

  async function apply(name, quiet){
    restored = true;                       // what the player does now counts, not what was on before a reload
    const p = (await api(`/api/tone/${enc(name)}`)).print;
    if(!p) throw new Error("Tahle píseň otisk nemá.");
    const c = ac();
    let core = null;
    if(p.core){
      const res = await fetch(`/api/amps/${enc(p.core.id)}`);
      if(!res.ok) throw new Error(`Jádro otisku (${p.core.name}) už v knihovně zesilovačů není.`);
      core = await (await Mixer.namEngine()).createNode();
      await core.loadModel(await res.text());
    }
    const ir = await c.decodeAudioData(await (await fetch(`/api/tone/${enc(name)}/file/print-ir.wav`)).arrayBuffer());
    const input = c.createGain(), out = c.createGain(), colour = c.createConvolver();
    colour.normalize = false; colour.buffer = ir;
    const chain = [input];
    if(p.pre.hp > 20) chain.push(biquad("highpass", p.pre.hp, PRE.hpQ));
    if(Math.abs(p.pre.mid_db) > 0.05) chain.push(biquad("peaking", PRE.mid, 0.7, p.pre.mid_db));
    if(Math.abs(p.pre.tilt_db) > 0.05) chain.push(biquad("lowshelf", PRE.low, 0, -p.pre.tilt_db / 2), biquad("highshelf", PRE.high, 0, p.pre.tilt_db / 2));
    if(core) chain.push(core);
    chain.push(colour, out);
    for(let i = 0; i + 1 < chain.length; i++) chain[i].connect(chain[i + 1]);
    const nodes = chain.slice();
    if(p.room && p.room.mix > 0){
      const wet = c.createConvolver(), mix = c.createGain();
      wet.normalize = false; wet.buffer = roomBuffer(p.room.decay); mix.gain.value = p.room.mix;
      colour.connect(wet); wet.connect(mix); mix.connect(out);
      nodes.push(wet, mix);
    }
    const dblBefore = live ? live.dbl : !!G().fx().state.dbl.on;
    remove(true);
    live = {song: name, print: p, input, out, nodes, core, dbl: dblBefore};
    setDrive();
    G().setPrint({input, output: out});
    if(p.width && p.width.on) Mixer.guitarDoubler(true);
    else if(!dblBefore) Mixer.guitarDoubler(false);
    try{ localStorage.setItem(KEEP, name); }catch(e){}
    Mixer.guitarFaderRepaint();
    if(!quiet) showToast(`Hraješ s otiskem „${title(name)}“ — shoda ${p.match} %`);
    paint();
  }
  function remove(quiet){
    if(!live) return;
    restored = true;
    const l = live;
    live = null;
    if(!quiet){ try{ localStorage.removeItem(KEEP); }catch(e){} }
    G().setPrint(null);
    l.nodes.forEach(n => { try{ n.disconnect(); }catch(e){} });
    if(l.core){ try{ l.core.unloadModel(); }catch(e){} }
    if(!quiet){
      if(!l.dbl) Mixer.guitarDoubler(false);
      Mixer.guitarFaderRepaint();
      showToast("Otisk vypnutý — hraješ zase přes svůj zesilovač");
      paint();
    }
  }
  // After a reload: the print that was on comes back once the guitar is plugged in.
  function restore(){
    if(restored || live || !G().isLive()) return;
    restored = true;
    let name = null;
    try{ name = localStorage.getItem(KEEP); }catch(e){}
    if(name) apply(name, true).catch(() => {});
  }
  (function hook(){
    if(typeof Mixer === "undefined" || !Mixer.guitar || !Mixer.guitar.onRewire){ setTimeout(hook, 1000); return; }
    Mixer.guitar.onRewire(restore);
  })();

  // ---------------------------------------------------------------- a take, on the song's clock
  function wav(x, sr){
    const b = new ArrayBuffer(44 + x.length * 4), v = new DataView(b);
    const w = (o, s) => { for(let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + x.length * 4, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 3, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true);
    v.setUint32(28, sr * 4, true); v.setUint16(32, 4, true); v.setUint16(34, 32, true);
    w(36, "data"); v.setUint32(40, x.length * 4, true);
    new Float32Array(b, 44).set(x);
    return new Blob([b], {type: "audio/wav"});
  }
  async function record(){
    if(recording){ recording.abort = true; return; }
    if(!song || !range) return;
    if(!G().isLive()){ showToast("Nejdřív zapni vstup kytary — tlačítko s kytarou na kanálu Live kytara"); return; }
    if(Math.abs(Mixer.speed() - 1) > 0.001){ showToast("Na nahrávku pro otisk přepni rychlost písně na 100 %"); return; }
    stopSound();
    const c = ac(), sr = c.sampleRate, name = song, r = {...range};
    const rec = new AudioWorkletNode(c, "take-recorder", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: "explicit"});
    const sink = c.createGain();
    sink.gain.value = 0; rec.connect(sink); sink.connect(c.destination);
    const tap = () => { const t = G().cleanTap(); if(t) try{ t.connect(rec); }catch(e){} };
    tap();
    const unhook = G().onRewire(tap);
    const batches = [];
    let stopped;
    const done = new Promise(res => { stopped = res; });
    rec.port.onmessage = (e) => { const d = e.data; if(d && d.done) stopped(); else if(d && d.data) batches.push(d); };
    const loop = Mixer.loopNow();
    if(loop) Mixer.setLoop(0, 0);
    recording = {abort: false};
    paint();
    Mixer.pause(); Mixer.seek(Math.max(0, r.start - 3));
    rec.port.postMessage("start");
    Mixer.play();
    await new Promise(res => { const t = setInterval(() => {
      const now = Mixer.currentTime();
      if(recording.abort || !Mixer.isPlaying() || now >= r.end + 0.25){ clearInterval(t); res(); }
      else if(recording.el) recording.el.textContent = now < r.start ? `Za ${Math.ceil(r.start - now)} s začni hrát…` : `Hraj — zbývá ${Math.ceil(r.end - now)} s`;
    }, 80); });
    rec.port.postMessage("stop");
    await Promise.race([done, wait(1500)]);
    Mixer.pause();
    unhook();
    try{ const t = G().cleanTap(); if(t) t.disconnect(rec); }catch(e){}
    rec.disconnect(); sink.disconnect();
    if(loop) Mixer.setLoop(loop.start, loop.end);
    const aborted = recording.abort;
    recording = null;
    if(aborted){ paint(); return; }
    // every block where the song was when it was played (minus the way in through the interface)
    const lat = Mixer.takeLatency(), len = Math.round((r.end - r.start) * sr), x = new Float32Array(len);
    let got = 0;
    for(const b of batches){
      const t0 = Mixer.songAt(b.t - lat);
      if(t0 == null) continue;
      const at = Math.round((t0 - r.start) * sr), ch = b.data[0];
      for(let i = 0; i < b.n; i++){ const k = at + i; if(k >= 0 && k < len){ x[k] = ch[i]; got++; } }
    }
    if(got < sr * 2){ showToast("Nahrávka úsek nezachytila — zkus to znovu"); paint(); return; }
    let peak = 0;
    for(let i = 0; i < len; i++){ const a = Math.abs(x[i]); if(a > peak) peak = a; }
    if(peak < 0.003){ showToast("Z kytary nic nepřišlo — je vybraný správný vstup a hraješ?"); paint(); return; }
    const fd = new FormData();
    fd.append("file", wav(x, sr), "take.wav");
    try{
      await api(`/api/tone/${enc(name)}/take?start=${r.start}&gain_db=${G().state.inputGainDb || 0}`, {method: "POST", body: fd});
      showToast("Nahráno — teď „Vytvořit otisk“");
    }catch(e){ showToast(e.message); }
    if(song === name) await refresh();
  }

  // ---------------------------------------------------------------- sound in the window
  async function buffer(url){
    return ac().decodeAudioData(await (await fetch(url, {cache: "no-store"})).arrayBuffer());
  }
  function stopSound(){
    if(playing){ try{ playing.stop(); }catch(e){} playing = null; }
  }
  function sound(buf, from = 0){
    stopSound();
    if(Mixer.isPlaying()) Mixer.pause();
    const {ac: c, out} = Mixer.voiceOut();
    const s = c.createBufferSource();
    s.buffer = buf; s.connect(out); s.start(0, from);
    s.onended = () => { if(playing === s) playing = null; };
    playing = s;
  }
  let abx = null;
  async function abxPair(){
    if(abx && abx.song === song && abx.created === (state.print || {}).created) return abx;
    const got = await api(`/api/tone/${enc(song)}/abx`, {method: "POST"});
    const [a, b] = await Promise.all([buffer(`/api/tone/${enc(song)}/file/${got.a}`), buffer(`/api/tone/${enc(song)}/file/${got.b}`)]);
    abx = {song, created: (state.print || {}).created, record: a, you: b};
    return abx;
  }
  async function hear(which, btn){
    const was = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try{ sound((await abxPair())[which]); }catch(e){ showToast(e.message); }
    btn.disabled = false; btn.textContent = was;
  }
  async function blind(){
    let pair;
    try{ pair = await abxPair(); }catch(e){ showToast(e.message); return; }
    blindRun = {round: 0, right: 0, of: 5, pick: null};
    next();
    function next(){
      const b = blindRun;
      if(!b) return;
      if(b.round === b.of){ paint(); return; }
      b.pick = Math.random() < 0.5 ? "record" : "you";
      b.round++;
      sound(pair[b.pick]);
      paint();
    }
    blindRun.answer = (which) => {
      const b = blindRun;
      if(!b || !b.pick) return;
      if(which === b.pick) b.right++;
      b.pick = null;
      stopSound();
      next();
    };
    blindRun.again = () => sound(pair[blindRun.pick]);
  }

  // ---------------------------------------------------------------- the server
  async function refresh(){
    if(!song) return;
    try{ state = await api(`/api/tone/${enc(song)}`); }catch(e){ state = {error: e.message}; }
    if(state.stems && state.stems.length && !state.stems.includes(stem)) stem = state.stems[0];
    if(state.job && state.job.status === "running") follow();
    paint();
  }
  async function loadSections(){
    sections = [];
    if(!song || !state || !(state.stems || []).includes(stem)) return;
    try{ sections = (await api(`/api/tone/${enc(song)}/sections?stem=${enc(stem)}`)).sections || []; }catch(e){}
    if(!range && sections.length) range = {start: sections[0].start, end: sections[0].end};
    paint();
  }
  async function fit(refine){
    if(!range) return;
    try{ await api(`/api/tone/${enc(song)}/fit`, post({start: range.start, end: range.end, stem, refine: !!refine})); }
    catch(e){ showToast(e.message); return; }
    await refresh();
    follow();
  }
  // While a print is made only the progress moves — a whole repaint every second would
  // swallow clicks and close what was open.
  function progress(j){
    const box = win && !win.hidden && win.querySelector(".tw-prog");
    if(!box) return false;
    box.querySelector(".tw-stage").textContent = `${STAGES[j.stage] || "počítám"}${j.of ? ` · ${j.done}/${j.of}` : ""} · ${mmss(j.elapsed || 0)}`;
    box.querySelector(".tw-pbar i").style.width = (j.of ? Math.round(100 * j.done / j.of) : 4) + "%";
    return true;
  }
  function follow(){
    clearTimeout(watch);
    watch = setTimeout(async () => {
      let j = null;
      try{ j = (await api("/api/tone/job")).job; }catch(e){ follow(); return; }
      if(!j || j.song !== song){ return; }
      if(j.status === "running"){ if(state) state.job = j; if(!progress(j)) paint(); follow(); return; }
      if(j.status === "done"){
        showToast(`Otisk je hotový — shoda ${j.match} %`);
        await refresh();
        if(live && live.song === song) apply(song, true).catch(e => showToast(e.message));
      } else if(j.status === "error"){ showToast("Otisk se nepovedl: " + (j.error || "")); await refresh(); }
    }, 1000);
  }

  // ---------------------------------------------------------------- the window
  function build(){
    win = document.createElement("div");
    win.id = "tone-win";
    win.className = "tone-win";
    win.innerHTML = `<div class="tw-bar"><button type="button" class="tw-close" title="Zavřít">${icon("close", 14)}</button>
      <span class="tw-title">Tónový otisk</span><span class="tw-song"></span></div><div class="tw-body"></div>`;
    document.body.appendChild(win);
    try{ const p = JSON.parse(localStorage.getItem(POS) || "null"); if(p){ win.style.left = p.x + "px"; win.style.top = p.y + "px"; } }catch(e){}
    win.querySelector(".tw-close").onclick = close;
    const bar = win.querySelector(".tw-bar");
    bar.onpointerdown = (e) => {
      if(e.button || e.target.closest("button")) return;
      const r = win.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      bar.setPointerCapture(e.pointerId);
      bar.onpointermove = (m) => {
        win.style.left = Math.max(0, Math.min(innerWidth - 120, m.clientX - dx)) + "px";
        win.style.top = Math.max(0, Math.min(innerHeight - 40, m.clientY - dy)) + "px";
      };
      bar.onpointerup = () => {
        bar.onpointermove = bar.onpointerup = null;
        try{ localStorage.setItem(POS, JSON.stringify({x: parseInt(win.style.left), y: parseInt(win.style.top)})); }catch(e){}
      };
    };
  }
  function close(){
    stopSound();
    blindRun = null;
    if(win) win.hidden = true;
  }
  async function open(){
    const name = Mixer.currentSong();
    if(!win) build();
    win.hidden = false;
    if(name !== song){ song = name; state = null; range = null; sections = []; blindRun = null; abx = null; }
    paint();
    api("/api/tone/prints").then(p => { prints = p; paint(); }).catch(() => {});
    if(!song) return;
    await refresh();
    const p = state && state.print;
    if(!range && p) range = {start: p.start, end: p.end};
    if(!range){ const l = Mixer.loopNow(); if(l && l.end - l.start >= 4) range = {start: l.start, end: Math.min(l.end, l.start + 40)}; }
    paint();
    loadSections();
  }

  function curve(cv, rep){
    const g = cv.getContext("2d"), w = cv.width, h = cv.height, c = rep.curve;
    g.clearRect(0, 0, w, h);
    if(!c || !c.hz || !c.hz.length) return;
    const all = c.record.concat(c.you), top = Math.max(...all), lo = top - 48;
    const x = (f) => (Math.log(f / c.hz[0]) / Math.log(c.hz[c.hz.length - 1] / c.hz[0])) * (w - 8) + 4;
    const y = (db) => 6 + (1 - (Math.max(lo, db) - lo) / (top - lo)) * (h - 18);
    g.strokeStyle = "rgba(255,255,255,.07)"; g.fillStyle = "rgba(255,255,255,.35)"; g.font = "9px -apple-system, sans-serif";
    [100, 1000, 10000].forEach(f => { if(f < c.hz[0] || f > c.hz[c.hz.length - 1]) return; g.beginPath(); g.moveTo(x(f), 0); g.lineTo(x(f), h); g.stroke(); g.fillText(f >= 1000 ? f / 1000 + " kHz" : f + " Hz", x(f) + 3, h - 3); });
    const line = (vals, col) => { g.strokeStyle = col; g.lineWidth = 2; g.beginPath(); vals.forEach((v, i) => { const X = x(c.hz[i]), Y = y(v); i ? g.lineTo(X, Y) : g.moveTo(X, Y); }); g.stroke(); };
    const css = getComputedStyle(cv);
    line(c.record, css.getPropertyValue("--accent").trim() || "#2dd4bf");
    line(c.you, css.getPropertyValue("--gold").trim() || "#e8b93f");
  }
  function bars(label, a, b, unit, lo, hi, hint){
    if(a == null || b == null) return "";
    const pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
    return `<div class="tw-meter" title="${hint}"><span class="tw-ml">${label}</span>
      <span class="tw-track"><i class="rec" style="left:${pos(a)}%"></i><i class="you" style="left:${pos(b)}%"></i></span>
      <span class="tw-mv">${cz(a)}${unit} · <b>${cz(b)}${unit}</b></span></div>`;
  }
  function paint(){
    if(!win || win.hidden) return;
    const body = win.querySelector(".tw-body");
    win.querySelector(".tw-song").textContent = song ? title(song) : "";
    if(!song){ body.innerHTML = `<p class="tw-note">Otevři píseň ve Steems — otisk se dělá z její kytary.</p>${printsHtml()}`; wirePrints(body); return; }
    if(!state){ body.innerHTML = `<p class="tw-note"><span class="spinner"></span> Načítám…</p>`; return; }
    if(state.error){ body.innerHTML = `<p class="tw-note">${escapeHtml(state.error)}</p>`; return; }
    if(!state.stems.length){ body.innerHTML = `<p class="tw-note">Tahle píseň nemá stopu kytary — otisk z ní udělat nejde.</p>${printsHtml()}`; wirePrints(body); return; }
    const p = state.print, job = state.job && state.job.status === "running" ? state.job : null;
    const takes = range ? state.takes.filter(t => Math.min(range.end, t.start + t.dur) - Math.max(range.start, t.start) >= 2) : [];
    const len = range ? range.end - range.start : 0;
    const rep = p && p.report;
    body.innerHTML = `
      ${state.ready ? "" : `<p class="tw-warn">Engine otisku tu není (prostředí pick-tone nebo Node) — nahrát jde, vytvořit otisk ne.</p>`}
      <section class="tw-sec"><div class="tw-h">1 · Úsek kytary
        ${state.stems.length > 1 ? `<select class="tw-stem">${state.stems.map(s => `<option value="${s}" ${s === stem ? "selected" : ""}>${STEMS[s] || s}</option>`).join("")}</select>` : ""}</div>
        <div class="tw-row"><b class="tw-range">${range ? `${mmss(range.start)}–${mmss(range.end)} (${Math.round(len)} s)` : "nevybráno"}</b>
          <button type="button" class="btn secondary tw-loop" title="Vzít úsek ze smyčky v mixu (4–40 s)">Smyčka z mixu</button>
          ${range ? `<button type="button" class="btn secondary tw-play" title="Přehrát úsek v mixu">▶ Úsek</button>` : ""}</div>
        ${sections.length ? `<div class="tw-row tw-chips"><span class="tw-sub">Kytara tu hraje nejvíc:</span>${sections.map((x, i) =>
          `<button type="button" class="tw-chip ${range && Math.abs(range.start - x.start) < 0.3 ? "on" : ""}" data-i="${i}">${mmss(x.start)}–${mmss(x.end)}</button>`).join("")}</div>` : ""}
        ${range && (len < 4 || len > 40) ? `<p class="tw-warn">Úsek musí mít 4 až 40 s.</p>` : ""}
      </section>
      <section class="tw-sec"><div class="tw-h">2 · Zahraj ho s písní</div>
        <p class="tw-sub">Píseň začne 3 s před úsekem. Hraj celý úsek tak, jak ho hraje originál — nahrává se čistý signál kytary, na konci úseku se nahrávání samo zastaví.</p>
        <div class="tw-row"><button type="button" class="btn ${recording ? "" : "secondary"} tw-rec" ${!range || len < 4 || len > 40 ? "disabled" : ""}>${recording ? "■ Zastavit" : "● Nahrát"}</button>
          <span class="tw-recnote">${recording ? "Za chvíli začni hrát…" : takes.length ? `${takes.length} ${takes.length === 1 ? "nahrávka" : "nahrávky"} na tomhle úseku` : ""}</span></div>
        ${takes.length ? `<div class="tw-takes">${takes.map(t => `<div class="tw-take"><span>${escapeHtml(t.file.replace(".wav", "").replace("take-", "Nahrávka "))} · ${mmss(t.start)}–${mmss(t.start + t.dur)}</span>
          <button type="button" class="tw-mini" data-play="${t.file}" title="Přehrát čistou nahrávku">▶</button>
          <button type="button" class="tw-mini" data-del="${t.file}" title="Smazat nahrávku">✕</button></div>`).join("")}</div>` : ""}
      </section>
      <section class="tw-sec"><div class="tw-h">3 · Otisk</div>
        ${job ? `<div class="tw-prog"><div class="tw-stage">${escapeHtml(STAGES[job.stage] || "počítám")}${job.of ? ` · ${job.done}/${job.of}` : ""} · ${mmss(job.elapsed || 0)}</div>
            <div class="tw-pbar"><i style="width:${job.of ? Math.round(100 * job.done / job.of) : 4}%"></i></div>
            <button type="button" class="btn secondary tw-stop">Zastavit</button></div>`
          : `<div class="tw-row"><button type="button" class="btn tw-fit" ${!state.ready || !takes.length || state.busy ? "disabled" : ""}>Vytvořit otisk</button>
            ${p ? `<button type="button" class="btn secondary tw-refine" ${!state.ready || !takes.length || state.busy ? "disabled" : ""} title="Znovu, i s novými nahrávkami — od nejlepších jader minula">Doladit</button>` : ""}
            <span class="tw-sub">${state.busy ? "Počítá se otisk jiné písně." : "Trvá asi 2 minuty."}</span></div>`}
      </section>
      ${p ? `<section class="tw-sec tw-result">
        <div class="tw-match"><b>${p.match} %</b><span>shoda s originálem${p.before != null ? ` <small>(předtím ${p.before} %)</small>` : ""}</span>
          <button type="button" class="btn ${live && live.song === song ? "" : "secondary"} tw-live">${live && live.song === song ? "✓ Hraješ s otiskem" : "Hrát s otiskem"}</button></div>
        ${(p.warnings || []).map(w => `<p class="tw-warn">${escapeHtml(w)}</p>`).join("")}
        <canvas class="tw-curve" width="440" height="120"></canvas>
        <div class="tw-legend"><i class="rec"></i>originál <i class="you"></i>ty přes otisk</div>
        ${rep ? bars("Zkreslení", rep.record.drive, rep.you.drive, "", 0, 100, "Jak moc je zvuk zkreslený (0 čistý – 100 metal)")
          + bars("Sustain", rep.record.sustain, rep.you.sustain, " dB", -30, 0, "Kolik zbude z tónu 0,3 s po úderu")
          + bars("Jas", rep.record.bright, rep.you.bright, " Hz", 300, 4000, "Těžiště spektra — vyšší = jasnější")
          + bars("Basy", rep.record.bass, rep.you.bass, " dB", -20, 10, "Basy proti středům")
          + bars("Prostor", rep.record.room, rep.you.room, " dB", -40, 0, "Jak rychle tón dozní, když přestaneš hrát (méně = víc prostoru)") : ""}
        <p class="tw-sub">Šířka: ${p.width && p.width.on ? "originál zní ze dvou stran (zdvojená kytara) — otisk zapne doubler" : "jedna kytara uprostřed"}</p>
        <div class="tw-row"><button type="button" class="btn secondary tw-a" title="Úsek s originální kytarou">▶ Originál</button>
          <button type="button" class="btn secondary tw-b" title="Úsek s tvou nahrávkou přes otisk">▶ Ty</button>
          <button type="button" class="btn secondary tw-hush">■</button>
          <button type="button" class="btn secondary tw-blind" title="Pick pustí úsek pětkrát — ty hádáš, jestli hraje originál, nebo ty">Slepý test</button>
          ${state.prev ? `<button type="button" class="btn secondary tw-undo" title="Vrátit otisk před posledním doladěním">Vrátit předchozí</button>` : ""}</div>
        ${blindHtml()}
        <details class="tw-more"><summary>Podrobnosti</summary>
          <div class="tw-sub">Jádro: ${escapeHtml(p.core ? p.core.name : "bez jádra (čistý zvuk)")} · drive ${num(p.drive_db)} dB ·
            před jádrem: ${p.pre.hp > 20 ? `ořez basů ${Math.round(p.pre.hp)} Hz, ` : ""}středy ${num(p.pre.mid_db)} dB, sklon ${num(p.pre.tilt_db)} dB
            ${p.room ? ` · dozvuk ${Math.round(p.room.mix * 100)} % / ${String(p.room.decay).replace(".", ",")} s` : ""}
            · posun nahrávky ${(p.lags_ms || []).join(", ")} ms · slyšeno ${String(p.played_s || "").replace(".", ",")} s hraní</div>
          ${(p.ranking || []).length ? `<div class="tw-sub">Další blízká jádra: ${p.ranking.slice(1, 6).map(r => escapeHtml(r.name)).join(", ")}</div>` : ""}
        </details>
      </section>` : ""}
      ${printsHtml()}`;
    wire(body);
    const cv = body.querySelector(".tw-curve");
    if(cv && rep) curve(cv, rep);
  }
  function blindHtml(){
    const b = blindRun;
    if(!b) return "";
    if(!b.pick && b.round === b.of){
      const verdict = b.right <= 3 ? "Spolehlivě to nepoznáš — zníš jako originál." : b.right === 4 ? "Většinou to poznáš — zkus Doladit s další nahrávkou." : "Poznáš to pokaždé — zkus Doladit, nebo jiný úsek.";
      return `<div class="tw-blind"><b>Trefil jsi ${b.right} z ${b.of}.</b> ${verdict} <button type="button" class="tw-mini tw-bdone">✕</button></div>`;
    }
    return `<div class="tw-blind">Kolo ${b.round}/${b.of} — kdo hraje?
      <button type="button" class="btn secondary" data-ans="record">Originál</button>
      <button type="button" class="btn secondary" data-ans="you">Já</button>
      <button type="button" class="tw-mini tw-bagain" title="Pustit znovu">↻</button></div>`;
  }
  function printsHtml(){
    const other = prints.filter(x => x.song !== song);
    if(!other.length && !(live && live.song !== song)) return "";
    return `<section class="tw-sec"><div class="tw-h">Otisky z jiných písní</div>
      <div class="tw-row"><select class="tw-prints">${other.map(x => `<option value="${escapeHtml(x.song)}" ${live && live.song === x.song ? "selected" : ""}>${escapeHtml(title(x.song))} · ${x.match} %</option>`).join("")}</select>
        <button type="button" class="btn secondary tw-use">Hrát s ním</button>
        ${live ? `<button type="button" class="btn secondary tw-off">Vypnout otisk</button>` : ""}</div></section>`;
  }
  function wirePrints(body){
    const use = body.querySelector(".tw-use"), off = body.querySelector(".tw-off");
    if(use) use.onclick = () => { const v = body.querySelector(".tw-prints").value; if(v) apply(v).catch(e => showToast(e.message)); };
    if(off) off.onclick = () => remove();
  }
  function wire(body){
    const q = (s) => body.querySelector(s);
    const sel = q(".tw-stem");
    if(sel) sel.onchange = () => { stem = sel.value; range = null; loadSections(); paint(); };
    q(".tw-loop").onclick = () => {
      const l = Mixer.loopNow();
      if(!l){ showToast("V mixu není zapnutá smyčka — nastav ji na úsek, kde hraje kytara"); return; }
      if(l.end - l.start < 4){ showToast("Smyčka je kratší než 4 s"); return; }
      range = {start: l.start, end: Math.min(l.end, l.start + 40)};
      paint();
    };
    const play = q(".tw-play");
    if(play) play.onclick = () => { stopSound(); Mixer.setLoop(range.start, range.end); Mixer.seek(range.start); Mixer.play(); };
    body.querySelectorAll(".tw-chip").forEach(b => b.onclick = () => {
      const x = sections[+b.dataset.i];
      range = {start: x.start, end: x.end};
      Mixer.setLoop(x.start, x.end); Mixer.seek(x.start);
      paint();
    });
    q(".tw-rec").onclick = () => record();
    recording && (recording.el = q(".tw-recnote"));
    body.querySelectorAll("[data-play]").forEach(b => b.onclick = async () => {
      try{ sound(await buffer(`/api/tone/${enc(song)}/file/${b.dataset.play}`)); }catch(e){ showToast("Nahrávku se nepodařilo přehrát"); }
    });
    body.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
      try{ await api(`/api/tone/${enc(song)}/take/${b.dataset.del}`, {method: "DELETE"}); }catch(e){ showToast(e.message); }
      refresh();
    });
    const f = q(".tw-fit"), rf = q(".tw-refine"), st = q(".tw-stop");
    if(f) f.onclick = () => fit(false);
    if(rf) rf.onclick = () => fit(true);
    if(st) st.onclick = async () => { try{ await api("/api/tone/job", {method: "DELETE"}); }catch(e){} refresh(); };
    const lv = q(".tw-live");
    if(lv) lv.onclick = () => {
      if(live && live.song === song){ remove(); return; }
      if(!G().isLive()) showToast("Otisk je zapnutý — uslyšíš ho, až zapneš vstup kytary");
      apply(song).catch(e => showToast(e.message));
    };
    const a = q(".tw-a"), b = q(".tw-b"), hush = q(".tw-hush"), bl = q(".tw-blind"), un = q(".tw-undo");
    if(a) a.onclick = () => hear("record", a);
    if(b) b.onclick = () => hear("you", b);
    if(hush) hush.onclick = () => { stopSound(); };
    if(bl) bl.onclick = () => blind();
    if(un) un.onclick = async () => {
      try{ await api(`/api/tone/${enc(song)}/undo`, {method: "POST"}); }catch(e){ showToast(e.message); return; }
      abx = null;
      await refresh();
      if(live && live.song === song) apply(song, true).catch(() => {});
      showToast("Vrácen předchozí otisk");
    };
    body.querySelectorAll("[data-ans]").forEach(x => x.onclick = () => blindRun && blindRun.answer(x.dataset.ans));
    const again = q(".tw-bagain"), bdone = q(".tw-bdone");
    if(again) again.onclick = () => blindRun && blindRun.again();
    if(bdone) bdone.onclick = () => { blindRun = null; paint(); };
    wirePrints(body);
  }

  return {open, close, on: () => !!live, label: () => (live ? title(live.song) : ""), apply, remove};
})();
