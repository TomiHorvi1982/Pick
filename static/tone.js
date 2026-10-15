// Tónový otisk — the sound of the guitar on a record, put onto the player's own guitar
// (server: /api/tone/…, tone_worker.py; docs/specs/2026-10-15-tonovy-otisk-design.md).
//
// A window of its own with two tabs. "Tato píseň", out of the live guitar's OTISK button: a
// stretch of the song's guitar (the loop, or one the server suggests), played along once —
// the guitar is recorded clean, on the song's clock — and the server finds the chain that
// sounds like the record. A song keeps as many prints as were made of it (its rhythm guitar,
// its solo…), each with a name. "Knihovna otisků", also from Knihovna → Otisky zvuku in the
// left panel: every print of every song by band, with its sound to hear and its curve, to be
// put on the live guitar with any song. "Hrát s otiskem" builds in the live guitar: drive →
// EQ → the NAM core → the print's IR → room (→ doubler), the very chain the fit heard, number
// for number (tone_worker.py builds it alike).
const Tone = (() => {
  const PRE = {mid: 750, low: 250, high: 2000, hpQ: -3.0103};       // tone_worker.py has the same
  const KEEP = "pick-tone-live", POS = "pick-tone-win", TAB = "pick-tone-tab", LSORT = "pick-tone-lib-sort";
  const STAGES = {listen: "poslouchám originál a tvou nahrávku", sieve: "zkouším jádra zkreslení", sieve2: "zkouším nejlepší jádra",
    tune: "dolaďuji drive a ekvalizér", colour: "počítám barvu", room: "hledám prostor", save: "ukládám"};
  const STEMS = {guitar: "Kytara", guitar_solo: "Sólová kytara", guitar_rhythm: "Rytmická kytara"};
  // the kinds of sound a print is filed under (server.py TONE_CATEGORIES has the same keys)
  const CATS = [["clean", "Clean", "#4fd1c5"], ["crunch", "Crunch", "#f6ad55"], ["hi_gain", "Hi-Gain", "#f56565"], ["lead", "Sólo", "#b794f4"],
    ["acoustic", "Akustická", "#68d391"], ["ambient", "Ambient", "#63b3ed"], ["fuzz", "Fuzz", "#ed64a6"], ["other", "Jiné", "#a0aec0"]];
  const CAT = Object.fromEntries(CATS.map(([k, label, color]) => [k, {label, color}]));
  const catOf = (k) => CAT[k] || CAT.other;
  const enc = encodeURIComponent;
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const post = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const G = () => Mixer.guitar;
  const ac = () => Mixer.voiceOut().ac;
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const cz = (v) => String(v).replace("-", "−").replace(".", ",");
  const num = (v, d = 1) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d).replace(".", ",");
  const title = (name) => { const d = stemsDisplayByName(name); return d.artist ? `${d.artist} — ${d.title}` : d.title || name; };
  const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const day = (t) => t ? new Date(t * 1000).toLocaleDateString("cs-CZ", {day: "numeric", month: "numeric", year: "numeric"}) : "";
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const q = (pid) => `print=${enc(pid || "")}`;

  let win = null, tab = "song", song = null, state = null, pid = null, range = null, stem = "guitar", sections = [];
  let library = null, libFind = "", libSort = "band", libCat = "", editing = null, catMenu = null;
  let watch = 0, recording = null, playing = null, blindRun = null, armed = null;
  let live = null;            // the print in the live guitar: {song, pid, print, input, out, nodes, core, dbl}
  let restored = false;       // the print on before a reload put back (or the player chose since)
  try{ tab = localStorage.getItem(TAB) || "song"; libSort = localStorage.getItem(LSORT) || "band"; libCat = localStorage.getItem(LSORT + "-cat") || ""; }catch(e){}

  // ---------------------------------------------------------------- the live chain
  function biquad(type, f, qv, gain){
    const b = ac().createBiquadFilter();
    b.type = type; b.frequency.value = f; b.Q.value = qv;
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

  async function apply(name, which, quiet){
    restored = true;                       // what the player does now counts, not what was on before a reload
    const p = (await api(`/api/tone/${enc(name)}?${q(which)}`)).print;
    if(!p) throw new Error("Tahle píseň otisk nemá.");
    const c = ac();
    let core = null;
    if(p.core){
      const res = await fetch(`/api/amps/${enc(p.core.id)}`);
      if(!res.ok) throw new Error(`Jádro otisku (${p.core.name}) už v knihovně zesilovačů není.`);
      core = await (await Mixer.namEngine()).createNode();
      await core.loadModel(await res.text());
    }
    const ir = await c.decodeAudioData(await (await fetch(`/api/tone/${enc(name)}/file/print-ir.wav?${q(p.id)}`)).arrayBuffer());
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
    live = {song: name, pid: p.id, print: p, input, out, nodes, core, dbl: dblBefore};
    setDrive();
    G().setPrint({input, output: out});
    if(p.width && p.width.on) Mixer.guitarDoubler(true);
    else if(!dblBefore) Mixer.guitarDoubler(false);
    try{ localStorage.setItem(KEEP, JSON.stringify({song: name, pid: p.id})); }catch(e){}
    Mixer.guitarFaderRepaint();
    if(!quiet) showToast(`Hraješ s otiskem „${p.name}“ (${title(name)}) — shoda ${p.match} %`);
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
    let k = null;
    try{ k = localStorage.getItem(KEEP); }catch(e){}
    if(!k) return;
    let was = {song: k, pid: ""};
    try{ const j = JSON.parse(k); if(j && j.song) was = j; }catch(e){}
    apply(was.song, was.pid, true).catch(() => {});
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
    const r = await fetch(url, {cache: "no-store"});
    if(!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Zvuk se nepodařilo načíst.");
    return ac().decodeAudioData(await r.arrayBuffer());
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
  async function preview(name, which, btn){
    btn.disabled = true; btn.classList.add("busy");
    try{ sound(await buffer(`/api/tone/${enc(name)}/preview/${which}?${q(btn.dataset.pid)}`)); }catch(e){ showToast(e.message); }
    btn.disabled = false; btn.classList.remove("busy");
  }
  let abx = null;
  async function abxPair(){
    const p = state.print;
    if(abx && abx.song === song && abx.pid === p.id && abx.created === p.created) return abx;
    const got = await api(`/api/tone/${enc(song)}/abx?${q(p.id)}`, {method: "POST"});
    const [a, b] = await Promise.all([buffer(`/api/tone/${enc(song)}/file/${got.a}?${q(p.id)}`), buffer(`/api/tone/${enc(song)}/file/${got.b}?${q(p.id)}`)]);
    abx = {song, pid: p.id, created: p.created, record: a, you: b};
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
    try{ state = await api(`/api/tone/${enc(song)}?${q(pid)}`); }catch(e){ state = {error: e.message}; }
    if(state.print) pid = state.print.id;
    if(state.stems && state.stems.length && !state.stems.includes(stem)) stem = state.stems[0];
    if(state.job && state.job.status === "running") follow();
    paint();
  }
  async function loadLibrary(){
    try{ library = await api("/api/tone/prints"); }catch(e){ library = []; showToast(e.message); }
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
    try{ await api(`/api/tone/${enc(song)}/fit`, post({start: range.start, end: range.end, stem, refine: !!refine, print: refine ? pid : ""})); }
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
        if(j.print) pid = j.print;
        library = null;
        await refresh();
        if(live && live.song === song && live.pid === pid) apply(song, pid, true).catch(e => showToast(e.message));
      } else if(j.status === "error"){ showToast("Otisk se nepovedl: " + (j.error || "")); await refresh(); }
    }, 1000);
  }
  // A print renamed ("" gives the naming back to the app) or filed under another kind of sound.
  async function edit(name, which, patch){
    let got;
    try{ got = await api(`/api/tone/${enc(name)}/prints/${enc(which)}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify(patch)}); }
    catch(e){ showToast(e.message); return; }
    if(live && live.song === name && live.pid === which){ live.print.name = got.name; live.print.category = got.category; }
    const x = library && library.find(z => z.song === name && z.id === which);
    if(x) Object.assign(x, {name: got.name, name_auto: got.name_auto, category: got.category, category_auto: got.category_auto});
    if(name === song) await refresh();
    paint();
    Mixer.guitarFaderRepaint();
  }
  // the name in place, as a field: Enter keeps it, Esc does not, ↺ gives it back to the app
  function rename(name, which, box){
    editing = name + "/" + which;
    const now = box.textContent;
    box.innerHTML = `<input type="text" class="tw-namein" maxlength="80"><button type="button" class="tw-ib tw-nok" title="Uložit">${icon("check", 12)}</button>
      <button type="button" class="tw-ib tw-nauto" title="Jméno podle kapely a písně">↺</button>`;
    const inp = box.querySelector("input");
    inp.value = now; inp.focus(); inp.select();
    const done = (patch) => { editing = null; if(patch) edit(name, which, patch); else paint(); };
    inp.onkeydown = (e) => { if(e.key === "Enter"){ e.preventDefault(); done({name: inp.value}); } else if(e.key === "Escape"){ e.preventDefault(); done(null); } };
    box.querySelector(".tw-nok").onmousedown = (e) => { e.preventDefault(); done({name: inp.value}); };
    box.querySelector(".tw-nauto").onmousedown = (e) => { e.preventDefault(); done({name: ""}); };
    inp.onblur = () => setTimeout(() => { if(editing === name + "/" + which) done(inp.value.trim() && inp.value.trim() !== now ? {name: inp.value} : null); }, 120);
  }
  function categories(name, which, now, anchor){
    if(catMenu){ catMenu.remove(); catMenu = null; }
    const m = document.createElement("div");
    m.className = "tw-catmenu";
    m.innerHTML = CATS.map(([k, label, color]) => `<button type="button" data-cat="${k}" class="${k === now ? "on" : ""}" style="--cat:${color}"><i></i>${label}</button>`).join("");
    document.body.appendChild(m);
    const r = anchor.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(r.left, innerWidth - m.offsetWidth - 8)) + "px";
    m.style.top = (r.bottom + m.offsetHeight + 8 > innerHeight ? Math.max(8, r.top - m.offsetHeight - 4) : r.bottom + 4) + "px";
    catMenu = m;
    m.querySelectorAll("[data-cat]").forEach(b => b.onclick = () => { m.remove(); catMenu = null; if(b.dataset.cat !== now) edit(name, which, {category: b.dataset.cat}); });
    setTimeout(() => document.addEventListener("pointerdown", function off(e){
      if(m.contains(e.target)) return;
      document.removeEventListener("pointerdown", off, true);
      if(catMenu === m){ m.remove(); catMenu = null; }
    }, true), 0);
  }
  const catChip = (c, own) => `<button type="button" class="tw-cat" style="--cat:${catOf(c).color}" title="${own ? "Kategorie — klikni a změň" : "Kategorii odhadl Pick ze zkreslení — klikni a změň"}"><i></i>${catOf(c).label}<span class="tw-caret">▾</span></button>`;
  const ring = (pct) => {
    const col = pct >= 80 ? "var(--ok)" : pct >= 60 ? "var(--accent)" : pct >= 40 ? "var(--gold)" : "var(--danger)";
    return `<svg class="tw-ring" viewBox="0 0 36 36" aria-label="shoda ${pct} %"><circle cx="18" cy="18" r="15.5" fill="none" stroke="currentColor" stroke-opacity=".12" stroke-width="3.2"/>
      <circle cx="18" cy="18" r="15.5" fill="none" stroke="${col}" stroke-width="3.2" stroke-linecap="round" stroke-dasharray="${(Math.max(0, Math.min(100, pct)) / 100 * 97.4).toFixed(1)} 97.4" transform="rotate(-90 18 18)"/>
      <text x="18" y="20.6" text-anchor="middle" font-size="9.5" font-weight="700" fill="currentColor">${pct}%</text></svg>`;
  };
  async function forget(name, which, btn){
    const key = name + "/" + which;
    if(armed !== key){
      armed = key;
      btn.textContent = "Smazat?"; btn.classList.add("armed");
      setTimeout(() => { if(armed === key){ armed = null; paint(); } }, 3000);
      return;
    }
    armed = null;
    try{ await api(`/api/tone/${enc(name)}/prints/${enc(which)}`, {method: "DELETE"}); }catch(e){ showToast(e.message); return; }
    if(live && live.song === name && live.pid === which) remove();
    if(name === song && pid === which) pid = null;
    library = null;
    if(name === song) await refresh();
    if(tab === "library") loadLibrary();
    showToast("Otisk smazán");
  }

  // ---------------------------------------------------------------- the window
  function build(){
    win = document.createElement("div");
    win.id = "tone-win";
    win.className = "tone-win";
    win.innerHTML = `<div class="tw-bar"><button type="button" class="tw-close" title="Zavřít">${icon("close", 14)}</button>
      <span class="tw-title">Tónový otisk</span>
      <span class="tw-tabs"><button type="button" data-tab="song">Tato píseň</button><button type="button" data-tab="library">Knihovna otisků</button></span></div>
      <div class="tw-live" hidden></div><div class="tw-body"></div>`;
    document.body.appendChild(win);
    try{ const p = JSON.parse(localStorage.getItem(POS) || "null"); if(p){ win.style.left = p.x + "px"; win.style.top = p.y + "px"; } }catch(e){}
    win.querySelector(".tw-close").onclick = close;
    win.querySelectorAll(".tw-tabs [data-tab]").forEach(b => b.onclick = () => show(b.dataset.tab));
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
  function show(which){
    tab = which;
    try{ localStorage.setItem(TAB, tab); }catch(e){}
    blindRun = null;
    if(tab === "library" && !library) loadLibrary();
    paint();
  }
  function close(){
    stopSound();
    blindRun = null;
    if(win) win.hidden = true;
  }
  async function open(which){
    const name = Mixer.currentSong();
    if(!win) build();
    win.hidden = false;
    tab = which || (name ? "song" : "library");
    if(name !== song){ song = name; state = null; pid = null; range = null; sections = []; blindRun = null; abx = null; }
    library = null;
    paint();
    if(tab === "library") loadLibrary();
    if(!song) return;
    await refresh();
    const p = state && state.print;
    if(!range && p) range = {start: p.start, end: p.end};
    if(!range){ const l = Mixer.loopNow(); if(l && l.end - l.start >= 4) range = {start: l.start, end: Math.min(l.end, l.start + 40)}; }
    paint();
    loadSections();
  }

  function curve(cv, c, thin){
    const g = cv.getContext("2d"), w = cv.width, h = cv.height;
    g.clearRect(0, 0, w, h);
    if(!c || !c.hz || !c.hz.length) return;
    const all = c.record.concat(c.you), top = Math.max(...all), lo = top - 48;
    const x = (f) => (Math.log(f / c.hz[0]) / Math.log(c.hz[c.hz.length - 1] / c.hz[0])) * (w - 8) + 4;
    const y = (db) => (thin ? 3 : 6) + (1 - (Math.max(lo, db) - lo) / (top - lo)) * (h - (thin ? 6 : 18));
    if(!thin){
      g.strokeStyle = "rgba(255,255,255,.07)"; g.fillStyle = "rgba(255,255,255,.35)"; g.font = "9px -apple-system, sans-serif";
      [100, 1000, 10000].forEach(f => { if(f < c.hz[0] || f > c.hz[c.hz.length - 1]) return; g.beginPath(); g.moveTo(x(f), 0); g.lineTo(x(f), h); g.stroke(); g.fillText(f >= 1000 ? f / 1000 + " kHz" : f + " Hz", x(f) + 3, h - 3); });
    }
    const css = getComputedStyle(cv), acc = css.getPropertyValue("--accent").trim() || "#2dd4bf", gold = css.getPropertyValue("--gold").trim() || "#e8b93f";
    const path = (vals) => { g.beginPath(); vals.forEach((v, i) => { const X = x(c.hz[i]), Y = y(v); i ? g.lineTo(X, Y) : g.moveTo(X, Y); }); };
    if(thin){                                            // the print's sound, filled, under the record's line
      path(c.you); g.lineTo(x(c.hz[c.hz.length - 1]), h); g.lineTo(x(c.hz[0]), h); g.closePath();
      let fill = gold;
      try{ const gr = g.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, gold + "66"); gr.addColorStop(1, gold + "00"); fill = gr; }
      catch(e){ g.globalAlpha = 0.22; }                // a colour that is not #rrggbb: plain and see-through
      g.fillStyle = fill; g.fill(); g.globalAlpha = 1;
    }
    const line = (vals, col) => { g.strokeStyle = col; g.lineWidth = thin ? 1.6 : 2; path(vals); g.stroke(); };
    line(c.record, acc);
    line(c.you, gold);
  }
  function bars(label, a, b, unit, lo, hi, hint){
    if(a == null || b == null) return "";
    const pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
    return `<div class="tw-meter" title="${hint}"><span class="tw-ml">${label}</span>
      <span class="tw-track"><i class="rec" style="left:${pos(a)}%"></i><i class="you" style="left:${pos(b)}%"></i></span>
      <span class="tw-mv">${cz(a)}${unit} · <b>${cz(b)}${unit}</b></span></div>`;
  }
  function liveLine(){
    const box = win.querySelector(".tw-live");
    box.hidden = !live;
    if(!live) return;
    box.innerHTML = `Teď hraješ s otiskem <b>${escapeHtml(live.print.name)}</b> · ${escapeHtml(title(live.song))}
      <button type="button" class="tw-mini tw-off">Vypnout</button>`;
    box.querySelector(".tw-off").onclick = () => remove();
  }
  function paint(){
    if(!win || win.hidden) return;
    win.querySelectorAll(".tw-tabs [data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
    liveLine();
    const body = win.querySelector(".tw-body");
    if(tab === "library") paintLibrary(body);
    else paintSong(body);
  }

  // ---- Tato píseň
  function paintSong(body){
    if(!song){ body.innerHTML = `<p class="tw-note">Otevři píseň ve Steems — otisk se dělá z její kytary. Hotové otisky najdeš v Knihovně otisků.</p>`; return; }
    if(!state){ body.innerHTML = `<p class="tw-note"><span class="spinner"></span> Načítám…</p>`; return; }
    if(state.error){ body.innerHTML = `<p class="tw-note">${escapeHtml(state.error)}</p>`; return; }
    body.innerHTML = `<p class="tw-songname">${escapeHtml(title(song))}</p>` + (state.stems.length ? songHtml()
      : `<p class="tw-note">Tahle píseň nemá stopu kytary — otisk z ní udělat nejde. Hotové otisky jiných písní jsou v Knihovně otisků.</p>`);
    if(state.stems.length) wireSong(body);
  }
  function songHtml(){
    const p = state.print, job = state.job && state.job.status === "running" ? state.job : null;
    const takes = range ? state.takes.filter(t => Math.min(range.end, t.start + t.dur) - Math.max(range.start, t.start) >= 2) : [];
    const len = range ? range.end - range.start : 0;
    const rep = p && p.report, isLive = live && p && live.song === song && live.pid === p.id;
    const prints = state.prints || [];
    return `
      ${state.ready ? "" : `<p class="tw-warn">Engine otisku tu není (prostředí pick-tone nebo Node) — nahrát jde, vytvořit otisk ne.</p>`}
      ${prints.length ? `<section class="tw-sec"><div class="tw-h">Otisky této písně</div><div class="tw-row tw-chips">${prints.map(x =>
        `<button type="button" class="tw-chip ${p && x.id === p.id ? "on" : ""}" data-pick="${escapeHtml(x.id)}" title="${escapeHtml(x.core || "")} · ${day(x.created)}">${escapeHtml(x.name)} · ${x.match} %${live && live.song === song && live.pid === x.id ? " ♪" : ""}</button>`).join("")}</div></section>` : ""}
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
        <p class="tw-sub">Píseň začne 3 s před úsekem. Hraj celý úsek tak, jak ho hraje originál — stejné tóny ve stejnou chvíli. Nahrává se čistý signál kytary, na konci úseku se nahrávání samo zastaví.</p>
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
          : `<div class="tw-row"><button type="button" class="btn tw-fit" ${!state.ready || !takes.length || state.busy ? "disabled" : ""}>${p ? "Vytvořit nový otisk" : "Vytvořit otisk"}</button>
            ${p ? `<button type="button" class="btn secondary tw-refine" ${!state.ready || !takes.length || state.busy ? "disabled" : ""} title="Vybraný otisk znovu, i s novými nahrávkami — od nejlepších jader minula">Doladit „${escapeHtml(p.name)}“</button>` : ""}
            <span class="tw-sub">${state.busy ? "Počítá se otisk jiné písně." : "Trvá asi 2 minuty."}</span></div>`}
      </section>
      ${p ? `<section class="tw-sec tw-result">
        <div class="tw-pname"><div class="tw-cname tw-big" title="Klikni a přejmenuj">${escapeHtml(p.name)}</div>${catChip(p.category, !p.category_auto)}
          <button type="button" class="tw-ib" data-forget="${escapeHtml(p.id)}" title="Smazat otisk">${icon("trash", 13)}</button></div>
        <div class="tw-sub tw-pmeta">${escapeHtml(STEMS[p.stem] || "")} · ${mmss(p.start)}–${mmss(p.end)} · ${day(p.created)}</div>
        <div class="tw-match"><b>${p.match} %</b><span>shoda s originálem${p.before != null ? ` <small>(předtím ${p.before} %)</small>` : ""}</span>
          <button type="button" class="btn ${isLive ? "" : "secondary"} tw-live">${isLive ? "✓ Hraješ s otiskem" : "Hrát s otiskem"}</button></div>
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
          ${p.prev ? `<button type="button" class="btn secondary tw-undo" title="Vrátit tenhle otisk před posledním doladěním">Vrátit předchozí</button>` : ""}</div>
        ${blindHtml()}
        <details class="tw-more"><summary>Podrobnosti</summary>
          <div class="tw-sub">Jádro: ${escapeHtml(p.core ? p.core.name : "bez jádra (čistý zvuk)")} · drive ${num(p.drive_db)} dB ·
            před jádrem: ${p.pre.hp > 20 ? `ořez basů ${Math.round(p.pre.hp)} Hz, ` : ""}středy ${num(p.pre.mid_db)} dB, sklon ${num(p.pre.tilt_db)} dB
            ${p.room ? ` · dozvuk ${Math.round(p.room.mix * 100)} % / ${String(p.room.decay).replace(".", ",")} s` : ""}
            · posun nahrávky ${(p.lags_ms || []).join(", ")} ms · slyšeno ${String(p.played_s || "").replace(".", ",")} s hraní</div>
          ${(p.ranking || []).length ? `<div class="tw-sub">Další blízká jádra: ${p.ranking.slice(1, 6).map(r => escapeHtml(r.name)).join(", ")}</div>` : ""}
        </details>
      </section>` : ""}`;
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
  function wireSong(body){
    const qs = (s) => body.querySelector(s);
    body.querySelectorAll("[data-pick]").forEach(b => b.onclick = () => {
      pid = b.dataset.pick; blindRun = null; abx = null;
      const x = (state.prints || []).find(p => p.id === pid);
      if(x){ range = {start: x.start, end: x.end}; stem = x.stem || stem; }
      refresh();
    });
    const sel = qs(".tw-stem");
    if(sel) sel.onchange = () => { stem = sel.value; range = null; loadSections(); paint(); };
    qs(".tw-loop").onclick = () => {
      const l = Mixer.loopNow();
      if(!l){ showToast("V mixu není zapnutá smyčka — nastav ji na úsek, kde hraje kytara"); return; }
      if(l.end - l.start < 4){ showToast("Smyčka je kratší než 4 s"); return; }
      range = {start: l.start, end: Math.min(l.end, l.start + 40)};
      paint();
    };
    const play = qs(".tw-play");
    if(play) play.onclick = () => { stopSound(); Mixer.setLoop(range.start, range.end); Mixer.seek(range.start); Mixer.play(); };
    body.querySelectorAll(".tw-chip[data-i]").forEach(b => b.onclick = () => {
      const x = sections[+b.dataset.i];
      range = {start: x.start, end: x.end};
      Mixer.setLoop(x.start, x.end); Mixer.seek(x.start);
      paint();
    });
    qs(".tw-rec").onclick = () => record();
    if(recording) recording.el = qs(".tw-recnote");
    body.querySelectorAll("[data-play]").forEach(b => b.onclick = async () => {
      try{ sound(await buffer(`/api/tone/${enc(song)}/file/${b.dataset.play}`)); }catch(e){ showToast("Nahrávku se nepodařilo přehrát"); }
    });
    body.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
      try{ await api(`/api/tone/${enc(song)}/take/${b.dataset.del}`, {method: "DELETE"}); }catch(e){ showToast(e.message); }
      refresh();
    });
    const f = qs(".tw-fit"), rf = qs(".tw-refine"), st = qs(".tw-stop");
    if(f) f.onclick = () => fit(false);
    if(rf) rf.onclick = () => fit(true);
    if(st) st.onclick = async () => { try{ await api("/api/tone/job", {method: "DELETE"}); }catch(e){} refresh(); };
    const lv = qs(".tw-live");
    if(lv) lv.onclick = () => {
      const p = state.print;
      if(live && live.song === song && live.pid === p.id){ remove(); return; }
      if(!G().isLive()) showToast("Otisk je zapnutý — uslyšíš ho, až zapneš vstup kytary");
      apply(song, p.id).catch(e => showToast(e.message));
    };
    const nm = body.querySelector(".tw-pname .tw-cname"), cat = body.querySelector(".tw-pname .tw-cat"), fg = body.querySelector("[data-forget]");
    if(nm) nm.onclick = () => { if(!nm.querySelector("input")) rename(song, state.print.id, nm); };
    if(cat) cat.onclick = () => categories(song, state.print.id, state.print.category, cat);
    if(fg) fg.onclick = () => forget(song, state.print.id, fg);
    const a = qs(".tw-a"), b = qs(".tw-b"), hush = qs(".tw-hush"), bl = qs(".tw-blind"), un = qs(".tw-undo");
    if(a) a.onclick = () => hear("record", a);
    if(b) b.onclick = () => hear("you", b);
    if(hush) hush.onclick = () => { stopSound(); };
    if(bl) bl.onclick = () => blind();
    if(un) un.onclick = async () => {
      try{ await api(`/api/tone/${enc(song)}/undo?${q(pid)}`, {method: "POST"}); }catch(e){ showToast(e.message); return; }
      abx = null; library = null;
      await refresh();
      if(live && live.song === song && live.pid === pid) apply(song, pid, true).catch(() => {});
      showToast("Vrácen předchozí otisk");
    };
    body.querySelectorAll("[data-ans]").forEach(x => x.onclick = () => blindRun && blindRun.answer(x.dataset.ans));
    const again = qs(".tw-bagain"), bdone = qs(".tw-bdone");
    if(again) again.onclick = () => blindRun && blindRun.again();
    if(bdone) bdone.onclick = () => { blindRun = null; paint(); };
    const cv = body.querySelector(".tw-curve");
    if(cv && state.print && state.print.report) curve(cv, state.print.report.curve);
  }

  // ---- Knihovna otisků
  function paintLibrary(body){
    if(!library){ body.innerHTML = `<p class="tw-note"><span class="spinner"></span> Načítám knihovnu otisků…</p>`; return; }
    if(editing) return;                                   // a name being typed is not redrawn under the fingers
    const keep = document.activeElement && document.activeElement.classList.contains("tw-find");
    const f = fold(libFind);
    const found = library.filter(x => !f || [x.artist, x.title, x.name, x.core, x.album, catOf(x.category).label].some(v => fold(v).includes(f)));
    const rows = found.filter(x => !libCat || x.category === libCat);
    const sorters = {band: (a, b) => fold(a.artist || "~").localeCompare(fold(b.artist || "~")) || fold(a.title).localeCompare(fold(b.title)) || (b.created || 0) - (a.created || 0),
      new: (a, b) => (b.created || 0) - (a.created || 0), match: (a, b) => (b.match || 0) - (a.match || 0)};
    rows.sort(sorters[libSort] || sorters.band);
    const n = (k) => found.filter(x => x.category === k).length;
    const plural = (v, one, few, many) => `${v} ${v === 1 ? one : v > 1 && v < 5 ? few : many}`;
    const bands = new Set(library.map(x => x.artist || "—")).size, songs = new Set(library.map(x => x.song)).size;
    let html = `<div class="tw-libtop"><div class="tw-libtitle">${icon("guitar", 16)}<b>Knihovna otisků</b>
        <span>${plural(library.length, "otisk", "otisky", "otisků")} · ${plural(songs, "píseň", "písně", "písní")} · ${plural(bands, "kapela", "kapely", "kapel")}</span></div>
      <div class="tw-libhead"><label class="tw-findbox">${icon("search", 13)}<input type="search" class="tw-find" placeholder="Hledat kapelu, píseň, otisk, jádro…" value="${escapeHtml(libFind)}"></label>
        <span class="tk-sort tw-lsort">${[["band", "Kapela"], ["new", "Nejnovější"], ["match", "Shoda"]].map(([k, l]) =>
          `<button type="button" data-lsort="${k}" class="${libSort === k ? "on" : ""}">${l}</button>`).join("")}</span></div>
      <div class="tw-cats"><button type="button" data-lcat="" class="${libCat ? "" : "on"}">Vše <small>${found.length}</small></button>${CATS.map(([k, label, color]) =>
        `<button type="button" data-lcat="${k}" class="${libCat === k ? "on" : ""} ${n(k) ? "" : "none"}" style="--cat:${color}"><i></i>${label} <small>${n(k)}</small></button>`).join("")}</div></div>`;
    if(!library.length) html += `<div class="tw-empty">${icon("guitar", 28)}<b>Zatím tu žádný otisk není</b>
        <span>Otevři píseň ve Steems, na kanálu Live kytara klikni na OTISK a záložka Tato píseň tě provede.</span></div>`;
    else if(!rows.length) html += `<div class="tw-empty"><b>Nic takového tu není</b><span>Zkus jiné hledání nebo kategorii.</span></div>`;
    html += `<div class="tw-grid">`;
    let band = null;
    for(const x of rows){
      const b = x.artist || "Neznámá kapela";
      if(libSort === "band" && b !== band){
        band = b;
        html += `<div class="tw-band"><span>${escapeHtml(b)}</span><small>${rows.filter(z => (z.artist || "Neznámá kapela") === b).length}</small></div>`;
      }
      const isLive = live && live.song === x.song && live.pid === x.id, key = x.song + "/" + x.id;
      const y = x.you || {};
      const meter = (label, v, lo, hi, unit) => v == null ? "" : `<span class="tw-cm" title="${label}: ${cz(v)}${unit}"><em>${label}</em><b style="width:${Math.max(4, Math.min(100, (v - lo) / (hi - lo) * 100))}%"></b></span>`;
      html += `<div class="tw-card ${isLive ? "live" : ""}" style="--cat:${catOf(x.category).color}" data-song="${escapeHtml(x.song)}" data-pid="${escapeHtml(x.id)}">
        <div class="tw-chead"><div class="tw-art">${escapeHtml((x.artist || x.title || "?").trim().charAt(0).toUpperCase())}</div>
          <div class="tw-ctitle"><div class="tw-cname" title="Klikni a přejmenuj">${escapeHtml(x.name)}</div>
            <div class="tw-csub">${escapeHtml(x.artist ? `${x.artist} · ${x.title}` : x.title)}</div></div>${ring(x.match)}</div>
        <div class="tw-ctags">${catChip(x.category, !x.category_auto)}<span class="tw-tag">${escapeHtml(STEMS[x.stem] || "Kytara")} ${mmss(x.start || 0)}–${mmss(x.end || 0)}</span>
          <span class="tw-tag" title="Jádro zkreslení">${escapeHtml(x.core || "bez jádra")}</span>${x.room ? `<span class="tw-tag">dozvuk</span>` : ""}${x.width ? `<span class="tw-tag">zdvojená</span>` : ""}</div>
        <canvas class="tw-mini-curve" width="300" height="54"></canvas>
        <div class="tw-cmeters">${meter("Zkreslení", y.drive, 0, 100, "")}${meter("Jas", y.bright, 300, 4000, " Hz")}${meter("Sustain", y.sustain, -30, 0, " dB")}</div>
        <div class="tw-cacts">
          <button type="button" class="tw-ib" data-act="you" title="Poslechnout — tvoje nahrávka přes otisk">${icon("headphones", 14)}</button>
          <button type="button" class="tw-ib" data-act="record" title="Poslechnout kytaru originálu">${icon("disc", 14)}</button>
          <button type="button" class="tw-ib" data-act="hush" title="Zastavit poslech">${icon("pause", 14)}</button>
          <span class="tw-sp"></span>
          <button type="button" class="tw-ib" data-act="open" title="Otevřít píseň a její otisky">${icon("external", 14)}</button>
          <button type="button" class="tw-ib ${armed === key ? "armed" : ""}" data-act="del" title="${armed === key ? "Klikni znovu — otisk se smaže" : "Smazat otisk"}">${armed === key ? "Smazat?" : icon("trash", 14)}</button>
          <button type="button" class="tw-play ${isLive ? "on" : ""}" data-act="use">${icon("guitar", 13)}<span>${isLive ? "Hraješ" : "Hrát"}</span></button>
        </div></div>`;
    }
    body.innerHTML = html + `</div>`;
    const find = body.querySelector(".tw-find");
    find.oninput = () => { libFind = find.value; paintLibrary(body); };
    if(keep){ find.focus(); find.setSelectionRange(find.value.length, find.value.length); }
    body.querySelectorAll("[data-lsort]").forEach(b => b.onclick = () => {
      libSort = b.dataset.lsort;
      try{ localStorage.setItem(LSORT, libSort); }catch(e){}
      paintLibrary(body);
    });
    body.querySelectorAll("[data-lcat]").forEach(b => b.onclick = () => {
      libCat = b.dataset.lcat;
      try{ localStorage.setItem(LSORT + "-cat", libCat); }catch(e){}
      paintLibrary(body);
    });
    body.querySelectorAll(".tw-card").forEach(card => {
      const x = library.find(z => z.song === card.dataset.song && z.id === card.dataset.pid);
      if(!x) return;
      curve(card.querySelector(".tw-mini-curve"), x.curve, true);
      if(typeof StemsArt !== "undefined") StemsArt.paint(card.querySelector(".tw-art"), x.song);
      const nm = card.querySelector(".tw-cname");
      nm.onclick = () => { if(!nm.querySelector("input")) rename(x.song, x.id, nm); };
      card.querySelector(".tw-cat").onclick = (e) => categories(x.song, x.id, x.category, e.currentTarget);
      card.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
        const act = b.dataset.act;
        if(act === "you" || act === "record"){ b.dataset.pid = x.id; preview(x.song, act, b); }
        else if(act === "hush") stopSound();
        else if(act === "del") forget(x.song, x.id, b);
        else if(act === "use"){
          if(live && live.song === x.song && live.pid === x.id){ remove(); return; }
          if(!G().isLive()) showToast("Otisk je zapnutý — uslyšíš ho, až zapneš vstup kytary");
          apply(x.song, x.id).catch(e => showToast(e.message));
        } else if(act === "open"){
          stopSound();
          if(Mixer.currentSong() !== x.song){
            if(typeof loadMixerSong === "function") loadMixerSong(x.song);
            for(let i = 0; i < 40 && Mixer.currentSong() !== x.song; i++) await wait(150);
          }
          song = x.song; state = null; pid = x.id; range = {start: x.start, end: x.end}; stem = x.stem || "guitar"; sections = []; abx = null;
          show("song");
          await refresh();
          loadSections();
        }
      });
    });
  }

  // ---- Knihovna → Otisky zvuku in the left panel
  let railCount = null;
  function railBox(collapsed){
    const row = document.createElement("div");
    row.className = "lib-row tw-rail";
    row.dataset.sec = "library";
    row.hidden = collapsed;
    row.title = "Knihovna otisků — zvuky kytar z písní, k zapnutí na tvé kytaře";
    row.innerHTML = `<span class="tw-rail-ic">${icon("guitar", 14)}</span><span class="tw-rail-name">Otisky zvuku</span><span class="tw-rail-n">${railCount ?? ""}</span>`;
    row.onclick = () => open("library");
    if(railCount === null){
      railCount = "";
      api("/api/tone/prints").then(p => { railCount = p.length || ""; const n = document.querySelector(".tw-rail .tw-rail-n"); if(n) n.textContent = railCount; }).catch(() => {});
    }
    return row;
  }

  return {open, close, railBox, on: () => !!live, label: () => (live ? `${live.print.name} · ${title(live.song)}` : ""), apply, remove};
})();
