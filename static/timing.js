// Časování — where the player rushes and where they drag. In Steems, the lane "Cvičení": ● records
// the live guitar (clean, before the amp) while the song plays, on the song's own clock — the way
// in through the interface taken off, at any speed, through loops. Each note the player starts is
// set against the nearest note the record's guitar starts (the guitar stems, summed); the
// difference is the timing. The lane shows each note above the line (early) or below it (late),
// every section says whether it rushes, drags or sits, and ≡ lists the sections worst first —
// a click loops one. Notes are found by the Analyzer's engine (static/analyzer-engine.js) in a
// Worker; the runs are kept with the song (/api/timing/{song}), the newest first.
// ♪ in the same lane is "Co tam hraje?" (static/whatplays.js).
const Timing = (() => {
  const enc = encodeURIComponent;
  const say0 = (m) => { try{ showToast(m); }catch(e){} }, say = say0;
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const ic = (n, s = 12) => (typeof icon === "function" ? icon(n, s) : "");
  const OK = 25;                       // ms either way: on time
  const WIN = 0.15;                    // s: the farthest a note is still taken for the record's one
  const SR = 22050;
  let ctx = null, song = null, runs = null, rec = null, busy = "";

  // ---------------------------------------------------------------- data
  async function load(name){
    try{ const d = await (await fetch(`/api/timing/${enc(name)}`)).json(); if(song === name){ runs = d.runs || []; ctx && ctx.rerender(); } }
    catch(e){ runs = []; }
  }
  async function keep(name, run){
    try{
      const r = await fetch(`/api/timing/${enc(name)}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(run)});
      const d = await r.json(); if(!r.ok) throw new Error(d.error || "Uložení se nepovedlo");
      if(song === name) runs = d.runs;
    }catch(e){ say(e.message); if(song === name) runs = [run, ...(runs || [])]; }
  }
  async function clear(){
    if(!song || !runs || !runs.length) return;
    if(typeof Dialog !== "undefined" && !await Dialog.confirm("Smazat všechna měření časování u téhle písně?", {ok: "Smazat", danger: true})) return;
    try{ runs = (await (await fetch(`/api/timing/${enc(song)}`, {method: "DELETE"})).json()).runs || []; }catch(e){ runs = []; }
    ctx.rerender();
  }

  // ---------------------------------------------------------------- the lane
  function headBtn(rail, html, title, fn, cls){
    const strip = rail.lastElementChild, b = document.createElement("button");
    b.type = "button"; b.className = "tl-hb " + (cls || ""); b.title = title; b.innerHTML = html;
    b.onclick = (e) => { e.stopPropagation(); fn(b); };
    strip.querySelector(".nm-strip-top").appendChild(b);
    return b;
  }
  function note(lane, text){ const n = document.createElement("div"); n.className = "nm-lane-empty"; n.textContent = text; lane.appendChild(n); }
  function render(rail, lanes, totalPx, c){
    ctx = c;
    if(c.song !== song){ song = c.song; runs = null; load(c.song); }
    const lane = c.thinLane(rail, lanes, totalPx, "Cvičení", 34, "tmg-lane");
    headBtn(rail, rec ? "■" : "●", rec ? "Zastavit a vyhodnotit časování" : "Časování: nahraj se do písně (spusť ji a hraj) — ukáže, kde spěcháš a kde táhneš proti kytaře originálu",
      () => (rec ? stop() : start()), rec ? "on tmg-rec" : "tmg-rec");
    headBtn(rail, "⏩", "Trenér rychlosti — smyčka od pomalého tempa; po každém čistém průchodu o kus rychleji, až na plné tempo",
      () => { if(typeof Trainer !== "undefined") Trainer.open(); }, typeof Trainer !== "undefined" && Trainer.running() ? "on" : "");
    headBtn(rail, "♪?", "Co tam hraje? — tóny kytary ve smyčce (nebo 5 s od kurzoru) jako tab a na hmatníku, i zpomaleně",
      () => { if(typeof WhatPlays !== "undefined") WhatPlays.open(); });
    const run = runs && runs[0];
    if(run){
      headBtn(rail, "≡", "Časování po sekcích — nejhorší nahoře, klik zapne smyčku", (b) => summary(b));
      headBtn(rail, "×", "Smazat měření časování", () => clear());
      const sub = rail.lastElementChild.querySelector(".nm-strip-sub") || rail.lastElementChild.appendChild(Object.assign(document.createElement("div"), {className: "nm-strip-sub"}));
      sub.textContent = `${run.within} % do ${OK} ms · ${overall(run, true)}`;
    }
    if(busy){ note(lane, busy); return; }
    if(rec){ note(lane, "nahrávám — hraj s písní, ■ zastaví a vyhodnotí"); return; }
    if(!runs){ note(lane, "načítám…"); return; }
    if(!run){ note(lane, "● nahraj se do písně a uvidíš, kde spěcháš a kde táhneš · ♪? ukáže, co kytara hraje"); return; }
    paint(lane, run, c);
  }
  // the whole run: on average on time but all over the place is not "on time"
  const overall = (run, short) => Math.abs(run.median) <= OK && run.within < 50 && run.spread > OK ? `kolísáš ±${run.spread} ms` : verdict(run.median, short);
  const verdict = (ms, short) => ms == null ? "—" : Math.abs(ms) <= OK ? (short ? `sedí (${ms > 0 ? "+" : ""}${ms} ms)` : "sedí") : ms < 0 ? `spěcháš ${-ms} ms` : `táhneš ${ms} ms`;
  function paint(lane, run, c){
    const mid = document.createElement("div"); mid.className = "tmg-mid"; lane.appendChild(mid);
    const frag = document.createDocumentFragment();
    for(const [t, d] of run.hits){
      const h = document.createElement("i");
      const k = Math.abs(d) <= OK ? "ok" : d < 0 ? "early" : "late";
      h.className = "tmg-hit " + k;
      const len = Math.min(15, 3 + Math.abs(d) / 6);
      h.style.left = (t * c.pps) + "px"; h.style.height = len + "px";
      if(d < 0) h.style.bottom = "50%"; else h.style.top = "50%";
      h.title = `${mmss(t)} · ${d === 0 ? "přesně" : d < 0 ? `o ${-d} ms dřív` : `o ${d} ms později`}`;
      frag.appendChild(h);
    }
    lane.appendChild(frag);
    for(const s of run.sections || []){
      if(s.n < 4) continue;
      const l = document.createElement("span");
      const k = Math.abs(s.median) <= OK ? "ok" : s.median < 0 ? "early" : "late";
      l.className = "tmg-sec " + k;
      l.style.left = (s.start * c.pps + 3) + "px";
      l.textContent = `${s.name}: ${verdict(s.median)}`;
      l.title = `${s.name} — ${s.n} tónů, ${s.within} % do ${OK} ms. Klik = smyčka na sekci`;
      l.onclick = (e) => { e.stopPropagation(); loopOn(s); };
      lane.appendChild(l);
    }
  }
  const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  function loopOn(s){ Mixer.setLoop(s.start, s.end); Mixer.seek(s.start); ctx && ctx.rerender(); say(`Smyčka: ${s.name}`); }

  // ---------------------------------------------------------------- the summary
  let pop = null;
  function summary(btn){
    if(pop){ pop.remove(); pop = null; return; }
    const run = runs && runs[0]; if(!run) return;
    const prev = runs[1];
    const secs = (run.sections || []).filter(s => s.n >= 4).slice().sort((a, b) => Math.abs(b.median) - Math.abs(a.median));
    const r = btn.getBoundingClientRect();
    pop = document.createElement("div");
    pop.className = "tmg-pop";
    const bias = Math.abs(run.median) > 35 && run.spread < 30;
    pop.innerHTML = `<div class="tmg-ph"><b>Časování</b><button type="button" class="tmg-x" title="Zavřít">×</button></div>
      <p class="tmg-big">${overall(run)} <small>· ${run.within} % tónů do ${OK} ms · ${run.n} tónů${run.speed !== 1 ? ` · rychlost ${Math.round(run.speed * 100)} %` : ""}</small></p>
      ${prev ? `<p class="tmg-prev">Minule: ${prev.within} % do ${OK} ms (${overall(prev)}) — ${run.within > prev.within ? "lepší ↑" : run.within < prev.within ? "horší ↓" : "stejné"}</p>` : ""}
      ${bias ? `<p class="tmg-note">Celé je posunuté skoro stejně — může to být i latence zvukovky. Zkontroluj ji u Live kytary (Nahrávka → latence).</p>` : ""}
      <div class="tmg-list">${secs.map((s, i) => `<button type="button" class="tmg-row" data-i="${i}">
        <span class="tmg-dot ${Math.abs(s.median) <= OK ? "ok" : s.median < 0 ? "early" : "late"}"></span>
        <b>${esc(s.name)}</b><span>${mmss(s.start)}</span><em>${verdict(s.median)}</em><small>${s.within} %</small></button>`).join("") || `<p class="tmg-note">Málo tónů na sekce — nahraj delší kus.</p>`}</div>
      <p class="tmg-note">Nad čarou = dřív (spěcháš), pod čarou = později (táhneš). Klik na sekci ji zapne ve smyčce.</p>`;
    document.body.appendChild(pop);
    pop.style.left = Math.max(8, Math.min(innerWidth - 340, r.left)) + "px";
    const ph = pop.offsetHeight;
    pop.style.top = (r.bottom + 6 + ph < innerHeight - 8 ? r.bottom + 6 : Math.max(8, r.top - ph - 6)) + "px";
    pop.querySelector(".tmg-x").onclick = () => { pop.remove(); pop = null; };
    pop.querySelectorAll(".tmg-row").forEach(b => b.onclick = () => loopOn(secs[+b.dataset.i]));
  }

  // ---------------------------------------------------------------- recording
  const guitars = () => (Mixer.channelInfo ? Mixer.channelInfo() : []).map(c => c.stem).filter(s => /^guitar/.test(s));
  // ---- the live guitar, clean, recorded on the AudioContext's clock (blocks: {t, n, data}); the trainer
  // (static/trainer.js) takes the same, a pass at a time
  async function capture(){
    const G = Mixer.guitar;
    if(!G || !G.isLive()) throw new Error("Nejdřív zapni vstup kytary — tlačítko s kytarou na kanálu Live kytara");
    const c = Mixer.voiceOut().ac;
    let node;
    const opts = {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: "explicit"};
    try{ node = new AudioWorkletNode(c, "take-recorder", opts); }
    catch(e){ try{ await c.audioWorklet.addModule("/static/worklets/guitar.js"); node = new AudioWorkletNode(c, "take-recorder", opts); }
      catch(x){ throw new Error("Nahrávání se nepodařilo spustit"); } }
    const sink = c.createGain(); sink.gain.value = 0; node.connect(sink); sink.connect(c.destination);
    let tapped = null;
    const tap = () => { const t = G.cleanTap(); if(t) try{ t.connect(node); tapped = t; }catch(e){} };
    tap();
    const unhook = G.onRewire(tap);
    const r = {node, sink, batches: [], sr: c.sampleRate, lat: Mixer.takeLatency(), speed: Mixer.speed(), ac: c};
    let stopped; r.done = new Promise(res => { stopped = res; });
    node.port.onmessage = (e) => { const d = e.data; if(d && d.done) stopped(); else if(d && d.data) r.batches.push(d); };
    node.port.postMessage("start");
    r.close = async () => {
      node.port.postMessage("stop");
      await Promise.race([r.done, new Promise(res => setTimeout(res, 1500))]);
      unhook();
      try{ if(tapped) tapped.disconnect(node); }catch(e){}
      node.disconnect(); sink.disconnect();
    };
    return r;
  }
  async function start(){
    if(!guitars().length){ say("Píseň nemá kytarovou stopu — není s čím porovnat"); return; }
    if(typeof Trainer !== "undefined" && Trainer.running()){ say("Teď běží trenér rychlosti — ten časování měří sám"); return; }
    try{ rec = await capture(); }catch(e){ say(e.message); return; }
    rec.name = song;
    if(!Mixer.isPlaying()) Mixer.play();
    rec.watch = setInterval(() => { if(rec && !Mixer.isPlaying()) stop(); }, 250);
    ctx.rerender();
    say("Nahrávám časování — hraj s písní. ■ nebo pauza zastaví.");
  }
  async function stop(){
    const r = rec; if(!r) return;
    rec = null; clearInterval(r.watch);
    await r.close();
    busy = "vyhodnocuji časování…"; ctx && ctx.rerender();
    try{
      const run = await measure(r);
      if(!run){ busy = ""; ctx && ctx.rerender(); return; }
      await keep(r.name, run);
      say(`Časování: ${overall(run)} · ${run.within} % tónů do ${OK} ms`);
      if(typeof Journal !== "undefined") Journal.event(r.name, "timing", {within: run.within, median: run.median, n: run.n, speed: run.speed});
    }catch(e){ say("Vyhodnocení se nepovedlo: " + e.message); }
    busy = ""; ctx && ctx.rerender();
  }
  const toggle = () => (rec ? stop() : start());

  // the blocks on the song's clock: stretches where the song ran on without a jump (a loop starts a new one)
  function stretches(r){
    const out = []; let cur = null;
    for(const b of r.batches){
      const t0 = Mixer.songAt(b.t - r.lat);
      if(t0 == null) continue;
      const ch = b.data[0], n = b.n;
      if(cur && Math.abs(t0 - cur.next) < 0.03){ cur.parts.push(ch.subarray ? ch.subarray(0, n) : ch); cur.n += n; cur.next = t0 + n / r.sr * r.speed; }
      else { cur = {t0, parts: [ch.subarray ? ch.subarray(0, n) : ch], n, next: t0 + n / r.sr * r.speed}; out.push(cur); }
    }
    return out.filter(s => s.n / r.sr >= 2);
  }
  // → SR, mono
  function resample(chans, srIn, from, len){
    const n = Math.floor(len / srIn * SR), out = new Float32Array(n), k = srIn / SR;
    for(const x of chans){
      for(let i = 0; i < n; i++){
        const p = from + i * k, j = Math.floor(p), f = p - j;
        if(j + 1 >= x.length) break;
        out[i] += x[j] * (1 - f) + x[j + 1] * f;
      }
    }
    return out;
  }
  function flat(parts, n){ const x = new Float32Array(n); let o = 0; for(const p of parts){ x.set(p.subarray(0, Math.min(p.length, n - o)), o); o += p.length; if(o >= n) break; } return x; }

  let worker = null, wid = 0;
  const waiting = new Map();
  function engine(cmd, msg, transfer){
    if(!worker){
      worker = new Worker("/static/analyzer-engine.js");
      worker.onmessage = (e) => { const m = e.data, w = waiting.get(m.id); if(!w || m.progress != null) return; waiting.delete(m.id); m.error ? w.no(new Error(m.error)) : w.ok(m.result); };
    }
    const id = ++wid;
    return new Promise((ok, no) => { waiting.set(id, {ok, no}); worker.postMessage({id, cmd, ...msg}, transfer || []); });
  }
  async function onsetsOf(pcm){
    const key = "tm" + (++wid);
    const a = await engine("analyze", {pcm, key}, [pcm.buffer]);
    engine("forget", {key}).catch(() => {});
    return (a.notes || []).map(n => n.t);
  }

  // r.quiet: no toasts (a trainer's pass); r.after: blocks recorded before it (ctx time) are left out
  async function measure(r){
    const say = r.quiet ? () => {} : say0;
    if(r.after) r = {...r, batches: r.batches.filter(b => b.t >= r.after)};
    const segs = stretches(r);
    if(!segs.length){ say("Nahrávka je moc krátká — hraj aspoň pár vteřin"); return null; }
    let peak = 0;
    for(const s of segs) for(const p of s.parts) for(let i = 0; i < p.length; i += 4){ const a = Math.abs(p[i]); if(a > peak) peak = a; }
    if(peak < 0.003){ say("Z kytary nic nepřišlo — je vybraný správný vstup a hraješ?"); return null; }
    const bufs = guitars().map(s => Mixer.channelByStem(s)).filter(c => c && c.buffer).map(c => c.buffer);
    if(!bufs.length) return null;
    const hits = [], extra = [], missed = [];
    for(const s of segs){
      const songLen = s.n / r.sr * r.speed, a = s.t0, b = Math.min(a + songLen, bufs[0].duration);
      if(b - a < 1.5) continue;
      // the player: their time → the song's
      const mine = resample([flat(s.parts, s.n)], r.sr, 0, s.n);
      const pOn = (await onsetsOf(mine)).map(t => a + t * r.speed);
      // the record: its guitars in the same stretch
      const chans = [];
      for(const buf of bufs) for(let ch = 0; ch < buf.numberOfChannels; ch++) chans.push(buf.getChannelData(ch));
      const srB = bufs[0].sampleRate;
      const ref = resample(chans, srB, Math.floor(a * srB), Math.floor((b - a) * srB));
      const rOn = (await onsetsOf(ref)).map(t => a + t);
      // each of the player's notes to the nearest one of the record's, one to one, the closest pairs first
      const W = WIN * r.speed, pairs = [];
      let j0 = 0;
      for(let i = 0; i < pOn.length; i++){
        while(j0 < rOn.length && rOn[j0] < pOn[i] - W) j0++;
        for(let j = j0; j < rOn.length && rOn[j] <= pOn[i] + W; j++) pairs.push([Math.abs(pOn[i] - rOn[j]), i, j]);
      }
      pairs.sort((x, y) => x[0] - y[0]);
      const usedP = new Set(), usedR = new Set();
      for(const [, i, j] of pairs){
        if(usedP.has(i) || usedR.has(j)) continue;
        usedP.add(i); usedR.add(j);
        hits.push([Math.round(rOn[j] * 1000) / 1000, Math.round((pOn[i] - rOn[j]) / r.speed * 1000)]);
      }
      pOn.forEach((t, i) => { if(!usedP.has(i)) extra.push(Math.round(t * 100) / 100); });
      rOn.forEach((t, j) => { if(!usedR.has(j) && t > pOn[0] - 0.5 && t < pOn[pOn.length - 1] + 0.5) missed.push(Math.round(t * 100) / 100); });
    }
    if(hits.length < 4){ say("Nenašel jsem dost tónů, které by šly porovnat s originálem — hraj s písní, ne mimo ni"); return null; }
    hits.sort((x, y) => x[0] - y[0]);
    const ds = hits.map(h => h[1]);
    const med = (xs) => { const s = xs.slice().sort((p, q) => p - q); return s.length ? s[s.length >> 1] : null; };
    const within = (xs) => Math.round(xs.filter(d => Math.abs(d) <= OK).length / xs.length * 100);
    const median = med(ds), spread = med(ds.map(d => Math.abs(d - median)));
    const secs = (ctx && ctx.sections ? ctx.sections() : (Mixer.sectionsNow ? Mixer.sectionsNow() : [])).map(x => {
      const xs = hits.filter(h => h[0] >= x.start && h[0] < x.end).map(h => h[1]);
      return {name: x.name, start: x.start, end: x.end, n: xs.length, median: xs.length ? med(xs) : null, within: xs.length ? within(xs) : null};
    }).filter(x => x.n);
    return {speed: r.speed, n: hits.length, median, spread, within: within(ds), hits, extra: extra.slice(0, 2000), missed: missed.slice(0, 2000),
            from: hits[0][0], to: hits[hits.length - 1][0], sections: secs};
  }

  return {render, toggle, recording: () => !!rec, runs: () => runs, capture, measure, OK, _measure: measure};
})();
