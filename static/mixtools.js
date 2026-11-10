// Mix tools for the Steems mixer (Pick's index.html calls them; Mixer.tools gives the tracks):
//  · the multiband compressor of a track or the live guitar — three bands at two crossovers, each with its own
//    threshold, ratio, attack, release and makeup; presets for palm muting;
//  · the mix's spectrum — every track (or the ones ticked) in one graph, by colour, with the places where they
//    fight over the same frequencies marked, the EQ of a track drawn and moved right there, and the EQs of the
//    whole song kept as a preset and put on another song.
const MixTools = (() => {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const T = () => Mixer.tools;
  const store = (k, v) => { try{ if(v === undefined) return JSON.parse(localStorage.getItem("pick-mix-" + k)); localStorage.setItem("pick-mix-" + k, JSON.stringify(v)); }catch(e){ return null; } };
  const CSS = `
  .mxw{position:fixed;z-index:9200;background:rgba(17,17,25,.98);border:1px solid rgba(255,255,255,.12);border-radius:16px;box-shadow:0 24px 70px rgba(0,0,0,.65);color:#ececf3;font:13px/1.4 system-ui,-apple-system,sans-serif;display:flex;flex-direction:column;overflow:hidden;resize:both;min-width:520px;min-height:300px}
  .mxw .mh{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.08);cursor:move;user-select:none}
  .mxw .mh b{font-size:14px;letter-spacing:.02em}.mxw .mh .sp{flex:1}
  .mxw button,.mxw select{background:#232331;color:#ececf3;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 10px;font:inherit;cursor:pointer}
  .mxw button.on{background:var(--accent,#8b5cf6);border-color:var(--accent,#8b5cf6);color:#0b0b12;font-weight:800}
  .mxw .mb{flex:1;min-height:0;overflow:auto;padding:12px 14px;display:flex;flex-direction:column;gap:12px}
  .mxw canvas{width:100%;display:block;border-radius:10px;background:#0a0a10;border:1px solid rgba(255,255,255,.07)}
  .mbb{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
  .mbb .band{border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:10px;display:flex;flex-direction:column;gap:6px;background:#15151f}
  .mbb .band.off{opacity:.55}
  .mbb .bh{display:flex;align-items:center;gap:6px}.mbb .bh b{flex:1}.mbb .bh .hz{font-size:11px;color:#9a9ab0}
  .mbb label{display:grid;grid-template-columns:66px 1fr 54px;gap:6px;align-items:center;font-size:11.5px;color:#9a9ab0}
  .mbb label input{width:100%}.mbb label span:last-child{text-align:right;color:#ececf3;font-variant-numeric:tabular-nums}
  .gr{height:8px;border-radius:4px;background:#22222e;overflow:hidden;direction:rtl}.gr i{display:block;height:100%;width:0;background:#ff8a65}
  .mxnote{font-size:12px;color:#9a9ab0}
  .chips{display:flex;gap:6px;flex-wrap:wrap}
  .chip{display:flex;align-items:center;gap:6px;border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:3px 10px 3px 6px;cursor:pointer;font-size:12px;user-select:none}
  .chip i{width:12px;height:12px;border-radius:50%;display:inline-block}.chip.off{opacity:.4}.chip.focus{border-color:#fff;box-shadow:0 0 0 1px #fff inset}
  .conf{display:flex;flex-direction:column;gap:4px;max-height:150px;overflow:auto}
  .conf div{display:flex;align-items:center;gap:8px;font-size:12px;background:#1a1420;border:1px solid rgba(255,107,107,.25);border-radius:8px;padding:5px 8px}
  .conf div b{color:#ff8a8a;min-width:64px}.conf div span{flex:1}
  `;
  { const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st); }

  function win(id, title, w, h){
    let el = document.getElementById(id);
    if(el){ el.hidden = false; return el; }
    el = document.createElement("div"); el.className = "mxw"; el.id = id;
    const pos = store("pos-" + id) || {x: Math.max(20, (innerWidth - w) / 2), y: 70, w, h};
    Object.assign(el.style, {left: pos.x + "px", top: pos.y + "px", width: (pos.w || w) + "px", height: (pos.h || h) + "px"});
    el.innerHTML = `<div class="mh"><b>${title}</b><span class="sp"></span><span class="mtools"></span><button data-x title="Zavřít">✕</button></div><div class="mb"></div>`;
    document.body.appendChild(el);
    el.querySelector("[data-x]").onclick = () => { el.hidden = true; if(el._close) el._close(); };
    const h0 = el.querySelector(".mh");
    h0.onpointerdown = (e) => { if(e.target.closest("button,select,input")) return; const r = el.getBoundingClientRect(), x0 = e.clientX, y0 = e.clientY; h0.setPointerCapture(e.pointerId);
      h0.onpointermove = (ev) => { el.style.left = Math.max(0, r.left + ev.clientX - x0) + "px"; el.style.top = Math.max(0, r.top + ev.clientY - y0) + "px"; };
      h0.onpointerup = () => { h0.onpointermove = null; keepPos(); }; };
    const keepPos = () => { const r = el.getBoundingClientRect(); store("pos-" + id, {x: r.left, y: r.top, w: r.width, h: r.height}); };
    new ResizeObserver(() => { keepPos(); if(el._resize) el._resize(); }).observe(el);
    return el;
  }
  const xOfF = (f, W) => Math.log(f / 20) / Math.log(1000) * W, fOfX = (x, W) => 20 * Math.pow(1000, x / W);
  const fmtHz = (f) => f >= 1000 ? (f / 1000).toFixed(f >= 10000 ? 0 : 1).replace(".", ",") + " kHz" : Math.round(f) + " Hz";

  // ================================================================== the multiband compressor
  const MB_PRESETS = [
    ["Palm muting — sevřít dunění", {x1: 200, x2: 2000, bands: [{on: true, threshold: -32, ratio: 5, attack: 0.003, release: 0.09, makeup: 3}, {on: true, threshold: -24, ratio: 2, attack: 0.01, release: 0.15, makeup: 1}, {on: false}]}],
    ["Palm muting — jemně", {x1: 220, x2: 2500, bands: [{on: true, threshold: -26, ratio: 3, attack: 0.005, release: 0.12, makeup: 2}, {on: false}, {on: false}]}],
    ["Zkrotit výšky (fizz)", {x1: 250, x2: 3500, bands: [{on: false}, {on: false}, {on: true, threshold: -30, ratio: 4, attack: 0.002, release: 0.08, makeup: 0}]}],
    ["Sjednotit celou kytaru", {x1: 250, x2: 2500, bands: [{on: true, threshold: -24, ratio: 2.5, attack: 0.008, release: 0.15, makeup: 2}, {on: true, threshold: -24, ratio: 2.5, attack: 0.008, release: 0.15, makeup: 2}, {on: true, threshold: -26, ratio: 2.5, attack: 0.005, release: 0.12, makeup: 1}]}],
    ["Basa — pevný spodek", {x1: 120, x2: 1200, bands: [{on: true, threshold: -28, ratio: 4, attack: 0.01, release: 0.2, makeup: 3}, {on: true, threshold: -22, ratio: 2, attack: 0.008, release: 0.15, makeup: 1}, {on: false}]}],
  ];
  const BAND_NAMES = ["Basy", "Středy", "Výšky"], BAND_COL = ["#ff6b6b", "#ffd166", "#38bdf8"];
  let mbSel = null, mbRaf = 0;
  const CZ = {drums: "Bicí", bass: "Basa", guitar: "Kytara", guitar_solo: "Kytara sólo", guitar_rhythm: "Kytara rytmus", vocals: "Zpěv", piano: "Klávesy", other: "Ostatní"};
  const nameOf = (t) => (t.label && t.label !== t.stem ? t.label : CZ[t.stem] || t.label || t.stem);
  function mbName(sel){ if(sel === "guitar") return "živá kytara"; const t = T().tracks()[sel]; return t ? nameOf(t) : "stopa"; }
  function mb(sel, anchor){
    mbSel = sel;
    const w = win("mx-mb", "Vícepásmový kompresor", 720, 470);
    w.querySelector(".mh b").textContent = `Vícepásmový kompresor — ${mbName(sel)}`;
    const fx = T().fx(sel); if(!fx){ return; }
    const s = () => fx.state.mb;
    const tools = w.querySelector(".mtools");
    tools.innerHTML = `<select data-pre><option value="">Presety…</option>${MB_PRESETS.map(([n], i) => `<option value="${i}">${esc(n)}</option>`).join("")}</select>
      <button data-on class="${s().on ? "on" : ""}">${s().on ? "Zapnutý" : "Vypnutý"}</button>`;
    tools.querySelector("[data-on]").onclick = () => { T().mbSet(sel, {on: !s().on}); mb(sel); };
    tools.querySelector("[data-pre]").onchange = (e) => { if(e.target.value === "") return; const p = MB_PRESETS[+e.target.value][1];
      T().mbSet(sel, {on: true, x1: p.x1, x2: p.x2, bands: p.bands.map(b => b.on === false ? {on: false} : b)}); mb(sel); };
    const body = w.querySelector(".mb");
    const range = (i) => i === 0 ? `do ${fmtHz(s().x1)}` : i === 1 ? `${fmtHz(s().x1)} – ${fmtHz(s().x2)}` : `od ${fmtHz(s().x2)}`;
    const ctl = (i, k, label, min, max, step, val, fmt) => `<label>${label}<input type="range" min="${min}" max="${max}" step="${step}" value="${val}" data-b="${i}" data-k="${k}"><span>${fmt(val)}</span></label>`;
    const ms = (v) => (v * 1000 < 10 ? (v * 1000).toFixed(1) : Math.round(v * 1000)) + " ms";
    body.innerHTML = `<canvas data-c height="150"></canvas>
      <div class="mxnote">Táhni svislé čáry v grafu — tím posuneš hranice pásem. Palm muting: zapni <b>Basy</b>, threshold kolem −30 dB, ratio 4–6 a rychlý attack; dunění se sevře a zbytek kytary zůstane.</div>
      <div class="mbb">${s().bands.map((b, i) => `<div class="band ${b.on ? "" : "off"}" data-band="${i}"><div class="bh"><i style="width:10px;height:10px;border-radius:50%;background:${BAND_COL[i]}"></i><b>${BAND_NAMES[i]}</b><span class="hz">${range(i)}</span>
          <button data-tog="${i}" class="${b.on ? "on" : ""}" style="padding:2px 8px">${b.on ? "ON" : "OFF"}</button></div>
          ${ctl(i, "threshold", "Threshold", -60, 0, 0.5, b.threshold, (v) => (+v).toFixed(1) + " dB")}
          ${ctl(i, "ratio", "Ratio", 1, 20, 0.1, b.ratio, (v) => (+v).toFixed(1) + " : 1")}
          ${ctl(i, "attack", "Attack", 0.0005, 0.1, 0.0005, b.attack, ms)}
          ${ctl(i, "release", "Release", 0.02, 1, 0.01, b.release, ms)}
          ${ctl(i, "makeup", "Makeup", -6, 18, 0.5, b.makeup, (v) => ((+v) > 0 ? "+" : "") + (+v).toFixed(1) + " dB")}
          <div class="mxnote">Ztlumení <span data-grv="${i}">0,0 dB</span></div><div class="gr"><i data-gr="${i}"></i></div></div>`).join("")}</div>`;
    const fmts = {threshold: (v) => (+v).toFixed(1) + " dB", ratio: (v) => (+v).toFixed(1) + " : 1", attack: ms, release: ms, makeup: (v) => ((+v) > 0 ? "+" : "") + (+v).toFixed(1) + " dB"};
    body.querySelectorAll("input[data-b]").forEach(inp => inp.oninput = () => {
      const i = +inp.dataset.b, k = inp.dataset.k, bands = s().bands.map(b => ({...b})); bands[i][k] = +inp.value;
      if(!s().on) T().mbSet(sel, {on: true}); T().mbSet(sel, {bands}); inp.nextElementSibling.textContent = fmts[k](inp.value);
      tools.querySelector("[data-on]").className = "on"; tools.querySelector("[data-on]").textContent = "Zapnutý";
    });
    body.querySelectorAll("[data-tog]").forEach(b => b.onclick = () => { const i = +b.dataset.tog, bands = s().bands.map(x => ({...x})); bands[i].on = !bands[i].on;
      T().mbSet(sel, {on: true, bands}); mb(sel); });
    // the graph: the bands, their crossovers to drag
    const cv = body.querySelector("[data-c]");
    const draw = () => {
      const W = cv.width = cv.clientWidth * devicePixelRatio, H = cv.height = 150 * devicePixelRatio, g = cv.getContext("2d");
      g.clearRect(0, 0, W, H);
      const xs = [0, xOfF(s().x1, W), xOfF(s().x2, W), W];
      const red = T().mbReduction(sel);
      for(let i = 0; i < 3; i++){ g.fillStyle = BAND_COL[i] + (s().bands[i].on && s().on ? "33" : "11"); g.fillRect(xs[i], 0, xs[i + 1] - xs[i], H);
        const r = Math.min(24, -(red[i] || 0)); g.fillStyle = BAND_COL[i] + "aa"; g.fillRect(xs[i], 0, xs[i + 1] - xs[i], r / 24 * H * 0.6); }
      g.strokeStyle = "rgba(255,255,255,.08)"; g.lineWidth = devicePixelRatio;
      [50, 100, 200, 500, 1000, 2000, 5000, 10000].forEach(f => { const x = xOfF(f, W); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); });
      g.fillStyle = "#8a8aa0"; g.font = `${11 * devicePixelRatio}px system-ui`;
      [[100, "100"], [1000, "1k"], [10000, "10k"]].forEach(([f, t]) => g.fillText(t, xOfF(f, W) + 4, H - 6 * devicePixelRatio));
      [1, 2].forEach(k => { const x = xs[k]; g.strokeStyle = "#fff"; g.lineWidth = 2 * devicePixelRatio; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke();
        g.fillStyle = "#fff"; g.fillText(fmtHz(k === 1 ? s().x1 : s().x2), x + 5, 16 * devicePixelRatio); });
    };
    cv.onpointerdown = (e) => {
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left, d1 = Math.abs(x - xOfF(s().x1, r.width)), d2 = Math.abs(x - xOfF(s().x2, r.width));
      if(Math.min(d1, d2) > 14) return; const k = d1 < d2 ? "x1" : "x2"; cv.setPointerCapture(e.pointerId);
      cv.onpointermove = (ev) => { let f = Math.round(fOfX(ev.clientX - r.left, r.width));
        f = k === "x1" ? Math.max(40, Math.min(s().x2 * 0.8, f)) : Math.max(s().x1 * 1.25, Math.min(12000, f));
        T().mbSet(sel, {[k]: f}); body.querySelectorAll(".band .hz").forEach((h, i) => { h.textContent = range(i); }); };
      cv.onpointerup = () => { cv.onpointermove = null; };
    };
    cancelAnimationFrame(mbRaf);
    const loop = () => { if(w.hidden || mbSel !== sel) return; draw();
      T().mbReduction(sel).forEach((r, i) => { const v = Math.min(24, -(r || 0)), gi = body.querySelector(`[data-gr="${i}"]`), gv = body.querySelector(`[data-grv="${i}"]`);
        if(gi) gi.style.width = v / 24 * 100 + "%"; if(gv) gv.textContent = "−" + v.toFixed(1).replace(".", ",") + " dB"; });
      mbRaf = requestAnimationFrame(loop); };
    loop();
    if(anchor && !store("pos-mx-mb")){ const r = anchor.getBoundingClientRect(); w.style.left = Math.max(10, Math.min(innerWidth - 740, r.left - 300)) + "px"; w.style.top = Math.max(10, r.top - 500) + "px"; }
  }

  // ================================================================== the spectrum of the mix
  // the instruments by how much each has the right to a frequency (the one lower here gives way)
  const RANK = (stem) => { const s = stem.split("_")[0]; return ({vocals: 0, bass: 1, drums: 2, guitar: 3, piano: 4, other: 5})[s] ?? 6; };
  const BANDS = []; for(let f = 25; f <= 16000; f *= Math.pow(2, 1 / 3)) BANDS.push(f);       // third octaves
  let spOn = new Set(store("sp-on") || []), focus = null, ema = {}, spRaf = 0, conflicts = [], drag = null;
  function spectrum(){
    const w = win("mx-spec", "Spektrum mixu", Math.min(1100, innerWidth - 40), Math.min(680, innerHeight - 100));
    const tools = w.querySelector(".mtools");
    const presets = store("eq-presets") || [];
    tools.innerHTML = `<select data-load><option value="">Načíst preset EQ…</option>${presets.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("")}</select>
      <button data-save title="Ekvalizéry všech stop téhle písně jako preset — dá se pak nahrát na jinou píseň">Uložit preset</button>
      <button data-flat title="Všem stopám vynulovat ekvalizér">Vše na nulu</button>`;
    const body = w.querySelector(".mb");
    body.innerHTML = `<div class="chips" data-chips></div>
      <canvas data-c style="flex:1;min-height:260px"></canvas>
      <div class="mxnote">Barevné plochy = co která stopa právě hraje (po jejím ekvalizéru). <b style="color:#ff8a8a">Červené pruhy</b> = frekvence, kde se dvě a víc stop přetlačují — tam mix houstne.
        Klikni na stopu nahoře a v grafu uvidíš a potáhneš její ekvalizér (body = pásma, kolečko = šířka Q). Pusť píseň, ať je co měřit.</div>
      <div class="conf" data-conf></div>`;
    const tracks = () => T().tracks();
    const chips = () => { const tr = tracks(); if(!spOn.size) tr.forEach(t => spOn.add(t.stem));
      body.querySelector("[data-chips]").innerHTML = tr.map(t => `<span class="chip ${spOn.has(t.stem) ? "" : "off"} ${focus === t.idx ? "focus" : ""}" data-i="${t.idx}" title="Klik = ukázat a upravit její EQ · dvojklik = zahrnout / vynechat"><i style="background:${t.color}"></i>${esc(nameOf(t))}</span>`).join("");
      body.querySelectorAll(".chip").forEach(ch => { const t = tr[+ch.dataset.i];
        ch.onclick = () => { focus = focus === t.idx ? null : t.idx; if(!spOn.has(t.stem)) spOn.add(t.stem); store("sp-on", [...spOn]); chips(); };
        ch.ondblclick = () => { spOn.has(t.stem) ? spOn.delete(t.stem) : spOn.add(t.stem); if(focus === t.idx && !spOn.has(t.stem)) focus = null; store("sp-on", [...spOn]); chips(); }; }); };
    chips();
    tools.querySelector("[data-save]").onclick = async () => {
      const name = (typeof Dialog !== "undefined" && Dialog.prompt) ? await Dialog.prompt("Název presetu EQ", "Můj mix") : prompt("Název presetu EQ", "Můj mix");
      if(!name) return; const bands = {}; tracks().forEach(t => { bands[t.stem] = t.bands; });
      const list = (store("eq-presets") || []).filter(p => p.name !== name); list.push({name, bands, at: Date.now()}); store("eq-presets", list);
      if(typeof showToast === "function") showToast(`Preset „${name}“ uložený`); spectrum();
    };
    tools.querySelector("[data-load]").onchange = (e) => { const p = presets[+e.target.value]; if(!p) return;
      let n = 0; tracks().forEach(t => { const src = p.bands[t.stem] || p.bands[Object.keys(p.bands).find(k => k.split("_")[0] === t.stem.split("_")[0])];
        if(src) { src.forEach((b, i) => T().setBand(t.idx, i, {freq: b.freq, gain: b.gain, q: b.q})); n++; } });
      if(typeof showToast === "function") showToast(`Preset „${p.name}“ nahraný na ${n} stop`); e.target.value = ""; };
    tools.querySelector("[data-flat]").onclick = () => { tracks().forEach(t => t.bands.forEach((b, i) => T().setBand(t.idx, i, {gain: 0}))); };
    const cv = body.querySelector("[data-c]");
    const yOf = (db, H) => (1 - (db + 90) / 80) * H, eqY = (db, H) => H / 2 - db / 18 * (H / 2) * 0.9;
    // the graph, every frame
    const frame = () => {
      if(w.hidden) return;
      const W = cv.width = Math.max(200, cv.clientWidth) * devicePixelRatio, H = cv.height = Math.max(200, cv.clientHeight) * devicePixelRatio, g = cv.getContext("2d");
      g.clearRect(0, 0, W, H);
      g.strokeStyle = "rgba(255,255,255,.06)"; g.lineWidth = devicePixelRatio;
      [30, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000].forEach(f => { const x = xOfF(f, W); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); });
      const tr = tracks().filter(t => spOn.has(t.stem) && t.spectrum && !t.muted), sr = T().ctx().sampleRate;
      // each track by third octaves, smoothed over time
      const lv = tr.map(t => { const a = t.spectrum, d = new Float32Array(a.frequencyBinCount); a.getFloatFrequencyData(d); const binHz = sr / a.fftSize;
        const bands = BANDS.map(f => { const lo = Math.floor(f / Math.pow(2, 1 / 6) / binHz), hi = Math.max(lo + 1, Math.ceil(f * Math.pow(2, 1 / 6) / binHz)); let p = 0, n = 0;
          for(let k = lo; k < hi && k < d.length; k++){ p += Math.pow(10, d[k] / 10); n++; } return n ? 10 * Math.log10(p / n + 1e-12) : -120; });
        const e = ema[t.stem] = ema[t.stem] && ema[t.stem].length === bands.length ? ema[t.stem].map((v, i) => v * 0.8 + bands[i] * 0.2) : bands;
        return {t, e}; });
      // where they fight: two or more within 6 dB of each other, near the loudest, and loud enough to matter
      conflicts = [];
      BANDS.forEach((f, i) => { const xs = lv.map(o => ({t: o.t, v: o.e[i]})).filter(o => o.v > -62).sort((a, b) => b.v - a.v);
        if(xs.length < 2) return; const top = xs[0].v, near = xs.filter(o => o.v > top - 6);
        if(near.length >= 2 && f > 40){ conflicts.push({f, i, who: near, level: top}); const x0 = xOfF(f / Math.pow(2, 1 / 6), W), x1 = xOfF(f * Math.pow(2, 1 / 6), W);
          g.fillStyle = `rgba(255,80,80,${Math.min(0.32, 0.1 + 0.07 * near.length)})`; g.fillRect(x0, 0, x1 - x0, H); } });
      // the tracks' areas
      lv.forEach(({t, e}) => { g.beginPath(); g.moveTo(0, H);
        BANDS.forEach((f, i) => g.lineTo(xOfF(f, W), yOf(e[i], H))); g.lineTo(W, H); g.closePath();
        g.fillStyle = t.color + (focus === t.idx ? "55" : "26"); g.fill(); g.strokeStyle = t.color; g.lineWidth = (focus === t.idx ? 2.5 : 1.3) * devicePixelRatio; g.stroke(); });
      // the focused track's EQ: its curve and its five bands to drag
      const ft = tracks().find(t => t.idx === focus);
      if(ft && ft.filters.length){
        const N = 240, fr = new Float32Array(N), mag = new Float32Array(N), ph = new Float32Array(N), tot = new Float32Array(N);
        for(let i = 0; i < N; i++) fr[i] = 20 * Math.pow(1000, i / (N - 1));
        ft.filters.forEach(fl => { fl.getFrequencyResponse(fr, mag, ph); for(let i = 0; i < N; i++) tot[i] += 20 * Math.log10(mag[i] || 1e-6); });
        g.beginPath(); for(let i = 0; i < N; i++){ const x = i / (N - 1) * W, y = eqY(tot[i], H); i ? g.lineTo(x, y) : g.moveTo(x, y); }
        g.strokeStyle = "#fff"; g.lineWidth = 2.5 * devicePixelRatio; g.setLineDash([6 * devicePixelRatio, 4 * devicePixelRatio]); g.stroke(); g.setLineDash([]);
        g.strokeStyle = "rgba(255,255,255,.25)"; g.beginPath(); g.moveTo(0, eqY(0, H)); g.lineTo(W, eqY(0, H)); g.stroke();
        ft.bands.forEach((b, i) => { const x = xOfF(b.freq, W), y = eqY(b.gain, H); g.fillStyle = ft.color; g.beginPath(); g.arc(x, y, 8 * devicePixelRatio, 0, 7); g.fill();
          g.strokeStyle = "#fff"; g.lineWidth = 2 * devicePixelRatio; g.stroke(); g.fillStyle = "#fff"; g.font = `bold ${10 * devicePixelRatio}px system-ui`; g.fillText(String(i + 1), x - 3 * devicePixelRatio, y + 4 * devicePixelRatio); });
      }
      g.fillStyle = "#8a8aa0"; g.font = `${11 * devicePixelRatio}px system-ui`;
      [[50, "50"], [100, "100"], [200, "200"], [500, "500"], [1000, "1k"], [2000, "2k"], [5000, "5k"], [10000, "10k"]].forEach(([f, t]) => g.fillText(t, xOfF(f, W) + 3, H - 5 * devicePixelRatio));
      if(!lv.length || lv.every(o => o.e.every(v => v < -80))){ g.fillStyle = "#9a9ab0"; g.font = `${14 * devicePixelRatio}px system-ui`; g.fillText("Pusť píseň — spektrum se ukáže, když hraje.", 20 * devicePixelRatio, 30 * devicePixelRatio); }
      paintConflicts();
      spRaf = requestAnimationFrame(frame);
    };
    let confT = 0;
    const paintConflicts = () => {
      if(performance.now() - confT < 600) return; confT = performance.now();
      // neighbouring bands with the same pair as one place
      const byPair = new Map();
      conflicts.forEach(c => { const key = c.who.slice(0, 2).map(o => o.t.stem).sort().join("|"); const o = byPair.get(key);
        if(!o || c.level > o.level) byPair.set(key, {...c, from: o ? Math.min(o.from, c.f) : c.f, to: o ? Math.max(o.to, c.f) : c.f}); else { o.from = Math.min(o.from, c.f); o.to = Math.max(o.to, c.f); } });
      const list = [...byPair.values()].sort((a, b) => b.level - a.level).slice(0, 6);
      const box = body.querySelector("[data-conf]");
      box.innerHTML = list.length ? list.map((c, k) => { const give = [...c.who].sort((a, b) => RANK(b.t.stem) - RANK(a.t.stem))[0].t;
          return `<div><b>${fmtHz(c.f)}</b><span>${c.who.map(o => esc(nameOf(o.t))).join(" × ")} se přetlačují${c.to > c.from * 1.3 ? ` (${fmtHz(c.from)}–${fmtHz(c.to)})` : ""}</span>
            <button data-fix="${k}" title="Na stopě ${esc(nameOf(give))} stáhne ${fmtHz(c.f)} o 3 dB">Udělat místo: ${esc(nameOf(give))} −3 dB</button></div>`; }).join("")
        : `<span class="mxnote">Žádné velké přetlačování — nebo píseň zrovna nehraje.</span>`;
      box.querySelectorAll("[data-fix]").forEach(b => b.onclick = () => { const c = list[+b.dataset.fix], give = [...c.who].sort((a, x) => RANK(x.t.stem) - RANK(a.t.stem))[0].t;
        const t = tracks().find(x => x.idx === give.idx); if(!t) return;
        let bi = 1, bd = 1e9; [1, 2, 3].forEach(i => { const d = Math.abs(Math.log(t.bands[i].freq / c.f)); if(d < bd){ bd = d; bi = i; } });
        T().setBand(t.idx, bi, {freq: Math.round(c.f), gain: Math.max(-15, Math.min(t.bands[bi].gain, 0) - 3), q: 1.4}); focus = t.idx; chips();
        if(typeof showToast === "function") showToast(`${nameOf(t)}: ${fmtHz(c.f)} −3 dB (pásmo ${bi + 1})`); });
    };
    // dragging a band of the focused track's EQ
    const hit = (e) => { const ft = tracks().find(t => t.idx === focus); if(!ft) return null; const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      let best = null, bd = 16; ft.bands.forEach((b, i) => { const d = Math.hypot(xOfF(b.freq, r.width) - x, eqY(b.gain, r.height) - y); if(d < bd){ bd = d; best = i; } }); return best == null ? null : {ft, i: best, r}; };
    cv.onpointerdown = (e) => { const h = hit(e); if(!h) return; cv.setPointerCapture(e.pointerId); drag = h;
      cv.onpointermove = (ev) => { const x = ev.clientX - h.r.left, y = ev.clientY - h.r.top;
        T().setBand(h.ft.idx, h.i, {freq: Math.round(fOfX(Math.max(0, Math.min(h.r.width, x)), h.r.width)), gain: Math.round((h.r.height / 2 - y) / (h.r.height / 2 * 0.9) * 18 * 2) / 2}); };
      cv.onpointerup = () => { cv.onpointermove = null; drag = null; }; };
    cv.onwheel = (e) => { const h = hit(e); if(!h) return; e.preventDefault(); const b = h.ft.bands[h.i]; T().setBand(h.ft.idx, h.i, {q: Math.round(b.q * (e.deltaY < 0 ? 1.15 : 1 / 1.15) * 100) / 100}); };
    cv.ondblclick = (e) => { const h = hit(e); if(h) T().setBand(h.ft.idx, h.i, {gain: 0}); else if(focus != null) T().openEq(focus); };
    w._close = () => cancelAnimationFrame(spRaf);
    cancelAnimationFrame(spRaf); frame();
  }
  document.addEventListener("click", (e) => { if(e.target.closest("#mix-spectrum-open")) spectrum(); });
  return {mb, spectrum};
})();
