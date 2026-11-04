// Three lanes above the sections in Steems (Mixer → renderTrackLanes): which print the live guitar
// plays when (Otisky), and where a delay and a reverb come on (Delay, Reverb).
//
// Otisky: stretches glued edge to edge from the song's start to its end — dragging the end of one
// moves the start of the next. First drawn from DITORIG's map of the song (its profiles, a stretch
// it could not tell kept with the sound before), else one stretch of the song's newest print.
// A double click opens the catalog of sounds, the most alike first, then by kind and character.
// While the print is on (static/tone.js), the sound follows the lane (Tone.setLane).
//
// Delay, Reverb: stretches with gaps between them (no effect there). First put where the solo
// guitar plays (/api/tone/{song}/solo); each has its own settings — a preset or by hand. As the
// song reaches one, the live guitar's DLY / REV (static/space.js) comes on with those settings,
// and goes back to what it was after it.
//
// Kept beside the stems (/api/tone/{song}/lanes) once anything is changed by hand.
const ToneLanes = (() => {
  const enc = encodeURIComponent;
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const ic = (n, s = 12) => (typeof icon === "function" ? icon(n, s) : "");
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const MIN = 0.5;
  const CATS = [["clean", "Clean", "#4fd1c5"], ["crunch", "Crunch", "#f6ad55"], ["hi_gain", "Hi-Gain", "#f56565"], ["lead", "Sólo", "#b794f4"],
    ["acoustic", "Akustická", "#68d391"], ["ambient", "Ambient", "#63b3ed"], ["fuzz", "Fuzz", "#ed64a6"], ["other", "Jiné", "#a0aec0"]];
  const CAT = Object.fromEntries(CATS.map(([k, label, color], i) => [k, {label, color, order: i}]));
  const catOf = (k) => CAT[k] || CAT.other;
  const FX = {
    delay: {label: "Delay", color: "#5ec8ff", presets: () => SpaceFx.DELAY_PRESETS, dflt: "Sólo 1/4"},
    reverb: {label: "Reverb", color: "#b794f4", presets: () => SpaceFx.REVERB_PRESETS, dflt: "Plate na sóla"},
  };

  let song = null, data = null, loading = null, ctx = null, catalog = null, catalogAt = 0;
  let timer = 0, saveTimer = 0, approvedNow = false;
  const active = {delay: null, reverb: null}, base = {delay: null, reverb: null};

  // ---------------------------------------------------------------- the lanes' data
  const empty = () => ({tone: {segs: []}, delay: {on: true, blocks: []}, reverb: {on: true, blocks: []}, auto: true});
  async function load(name, duration){
    const mine = {};
    loading = mine;
    let d = null;
    try{ const r = await api(`/api/tone/${enc(name)}/lanes`); d = r.lanes; approvedNow = !!r.approved; }catch(e){}
    if(!d){
      d = empty();
      try{ d.tone.segs = await fromPrints(name, duration); }catch(e){}
      try{
        const solo = await api(`/api/tone/${enc(name)}/solo`);
        for(const k of ["delay", "reverb"]){
          const pr = FX[k].presets().find(x => x.name === FX[k].dflt) || FX[k].presets()[0];
          d[k].blocks = (solo.regions || []).map(r => ({start: r.start, end: Math.min(duration || r.end, r.end), name: pr.name, p: {...pr.p}}));
          d[k].from = solo.from;
        }
      }catch(e){}
    }
    if(loading !== mine) return;
    loading = null;
    for(const k of ["delay", "reverb"]) if(!d[k]) d[k] = {on: true, blocks: []};
    if(!d.tone) d.tone = {segs: []};
    data = d;
    pushLane();
    if(ctx && ctx.song === name) ctx.rerender();
  }
  // DITORIG's map, its stretches of one profile glued into one and a stretch it could not tell kept
  // with the sound before it — or one stretch of the song's newest print.
  // A print of Pick's own (made with the player's guitar) comes first; DITORIG's map when asked for.
  async function fromPrints(name, duration, ditorigMap){
    const st = await api(`/api/tone/${enc(name)}`);
    const prints = st.prints || [];
    if(!prints.length) return [];
    const own = prints.filter(x => x.source !== "ditorig");
    if(own.length && !ditorigMap){
      const p = own[0];
      return [{start: 0, end: duration || 600, song: name, pid: p.id, key: `pick:${name}/${p.id}`, name: p.name, category: p.category}];
    }
    const dit = prints.filter(x => x.source === "ditorig");
    if(dit.length){
      const stem = dit.some(x => (x.stem || "guitar") === "guitar") ? "guitar" : dit[0].stem;
      const mine = dit.filter(x => (x.stem || "guitar") === stem);
      const top = mine.slice().sort((a, b) => (b.cover || 0) - (a.cover || 0))[0];
      const full = (await api(`/api/tone/${enc(name)}?print=${enc(top.id)}`)).print;
      const byProfile = Object.fromEntries(mine.map(x => [x.profile, x]));
      const segs = [];
      for(const t of (full.ditorig && full.ditorig.timeline) || []){
        const x = t.profile && byProfile[t.profile];
        const last = segs[segs.length - 1];
        if(!x){ if(last) last.end = t.end; continue; }            // not known: the sound before goes on
        if(last && last.pid === x.id){ last.end = t.end; continue; }
        segs.push({start: t.start, end: t.end, song: name, pid: x.id, key: "dit:" + x.profile, name: (x.name || "").replace("DITORIG · ", ""), category: x.category});
      }
      if(segs.length){
        segs[0].start = 0;
        for(let i = 1; i < segs.length; i++) segs[i].start = segs[i - 1].end;
        segs[segs.length - 1].end = Math.max(segs[segs.length - 1].end, duration || 0);
        return segs;
      }
    }
    const p = prints[0];
    return [{start: 0, end: duration || 600, song: name, pid: p.id, key: `pick:${name}/${p.id}`, name: p.name, category: p.category}];
  }
  function save(){
    if(!data || !song) return;
    data.auto = false;
    clearTimeout(saveTimer);
    const name = song, body = JSON.stringify({...data, auto: false});
    saveTimer = setTimeout(() => fetch(`/api/tone/${enc(name)}/lanes`, {method: "PUT", headers: {"Content-Type": "application/json"}, body}).catch(() => {}), 400);
  }
  function pushLane(){
    if(typeof Tone === "undefined" || !Tone.setLane || !song) return;
    Tone.setLane(song, data && data.tone.segs.length ? data.tone.segs : null);
  }
  function changed(){ save(); pushLane(); if(ctx) ctx.rerender(); }

  // ---------------------------------------------------------------- drawing
  function render(rail, lanes, totalPx, c){
    ctx = c;
    if(c.song !== song){ song = c.song; data = null; restoreFx(); load(c.song, c.duration); }
    if(!timer) timer = setInterval(tick, 100);
    toneLane(rail, lanes, totalPx);
    fxLane(rail, lanes, totalPx, "delay");
    fxLane(rail, lanes, totalPx, "reverb");
  }
  function headBtn(rail, html, title, fn, on){
    const strip = rail.lastElementChild, b = document.createElement("button");
    b.type = "button"; b.className = "tl-hb" + (on ? " on" : ""); b.title = title; b.innerHTML = html;
    b.onclick = (e) => { e.stopPropagation(); fn(b); };
    strip.querySelector(".nm-strip-top").appendChild(b);
    return b;
  }
  const px = (t) => t * ctx.pps;

  function toneLane(rail, lanes, totalPx){
    const lane = ctx.thinLane(rail, lanes, totalPx, "Otisky", 30, "tl-lane tl-tone");
    const on = typeof Tone !== "undefined" && Tone.on && Tone.on();
    headBtn(rail, on ? "●" : "▶", on ? "Otisky hrají podle lišty (vypnout = otisk vypnout v kanálu Live kytara)" : "Zapnout otisky podle lišty — na kanálu Live kytara",
      async () => { if(!on) await startTone(); }, on);
    headBtn(rail, ic("plus", 11), "Rozdělit otisk na pozici přehrávání", () => split(ctx.time()));
    if(typeof StampSongs !== "undefined" && data && data.tone.segs.length){
      const ok = approvedNow;
      headBtn(rail, ok ? "✓" : "✓?", ok ? "Approved — zvuk i nastavení písně sedí (klik vrátí do To Review)" : "Approve — zvuk i nastavení písně sedí, je připravená na hraní",
        async () => { if(await StampSongs.approve(song, !ok)){ approvedNow = !ok; ctx.rerender(); } }, ok).classList.add("appr");
    }
    if(!data){ note(lane, "načítám…"); return; }
    const segs = data.tone.segs;
    if(!segs.length){ note(lane, "píseň zatím nemá otisk — dvojklik vybere z katalogu"); lane.ondblclick = () => pick(-1); return; }
    segs.forEach((s, i) => {
      const b = block(lane, s, catOf(s.category).color, s.name || "otisk", "tl-tb", i);
      b.title = `${s.name || "otisk"} — ${mmss(s.start)}–${mmss(s.end)}\nDvojklik = vyměnit z katalogu · táhni za okraj = posunout přechod (spojený otisk se posune s ním) · pravé tlačítko = rozdělit, spojit`;
      if(i < segs.length - 1) b.appendChild(grip("e"));
      b.addEventListener("pointerdown", (e) => { if(e.target.classList.contains("tl-grip")) dragEdge(e, b, i); });
      b.addEventListener("dblclick", (e) => { e.stopPropagation(); pick(i, b); });
      b.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); toneMenu(i, e.clientX, e.clientY); });
    });
  }
  function fxLane(rail, lanes, totalPx, k){
    const f = FX[k], L = data && data[k];
    const lane = ctx.thinLane(rail, lanes, totalPx, f.label, 26, "tl-lane tl-fx tl-" + k);
    headBtn(rail, L && L.on !== false ? "●" : "○", L && L.on !== false ? `${f.label} se zapíná podle lišty — klik vypne` : `${f.label} podle lišty je vypnutý — klik zapne`,
      () => { if(!data) return; data[k].on = data[k].on === false; if(data[k].on === false) restoreFx(k); changed(); }, L && L.on !== false);
    headBtn(rail, ic("plus", 11), `Přidat ${f.label.toLowerCase()} na pozici přehrávání`, () => addFx(k, ctx.time()));
    headBtn(rail, "S", `${f.label} na sóla — kde hraje sólová kytara (nahradí úseky v liště)`, () => soloFill(k));
    if(!data){ note(lane, "načítám…"); return; }
    if(!L.blocks.length) note(lane, "bez efektu — + přidá úsek, S dá efekt na sóla");
    lane.ondblclick = (e) => { if(e.target === lane){ const r = lane.getBoundingClientRect(); addFx(k, (e.clientX - r.left) / ctx.pps); } };
    L.blocks.forEach((x, i) => {
      const b = block(lane, x, f.color, x.name || "vlastní", "tl-fb", i);
      b.classList.toggle("off", L.on === false);
      b.title = `${f.label}: ${x.name || "vlastní nastavení"} — ${mmss(x.start)}–${mmss(x.end)}\nDvojklik = nastavení a presety · táhni = posun · okraj = délka · pravé tlačítko = smazat`;
      b.appendChild(grip("s")); b.appendChild(grip("e"));
      b.addEventListener("pointerdown", (e) => dragFx(e, b, k, i));
      b.addEventListener("dblclick", (e) => { e.stopPropagation(); editFx(k, i, b); });
      b.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); L.blocks.splice(i, 1); restoreFx(k); changed(); say(`${f.label}: úsek smazán`); });
    });
  }
  function note(lane, text){ const n = document.createElement("div"); n.className = "nm-lane-empty"; n.textContent = text; lane.appendChild(n); }
  function grip(edge){ const g = document.createElement("div"); g.className = "tl-grip " + edge; g.dataset.edge = edge; return g; }
  function block(lane, s, color, label, cls, i){
    const b = document.createElement("div"), w = Math.max(3, px(s.end - s.start));
    b.className = "tl-block " + cls;
    b.dataset.i = String(i);
    b.style.left = px(s.start) + "px"; b.style.width = (w - 2) + "px";
    b.style.setProperty("--c", color);
    b.innerHTML = w >= 40 ? `<span class="nm">${esc(label)}</span>` : "";
    lane.appendChild(b);
    return b;
  }
  function paintBlock(b, s){ b.style.left = px(s.start) + "px"; b.style.width = Math.max(3, px(s.end - s.start) - 2) + "px"; }

  // ---------------------------------------------------------------- moving
  function drag(e, b, onMove){
    if(e.button !== 0) return;
    const x0 = e.clientX;
    let moved = false;
    e.preventDefault(); e.stopPropagation();
    try{ b.setPointerCapture(e.pointerId); }catch(err){}
    b.classList.add("dragging");
    const mv = (ev) => { if(!moved && Math.abs(ev.clientX - x0) < 3) return; moved = true; onMove((ev.clientX - x0) / ctx.pps, ev.altKey); };
    const up = () => {
      b.classList.remove("dragging");
      window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up);
      if(moved) changed();
      else if(ctx) ctx.seek(Number(b.dataset.start || 0) || blockStart(b));
    };
    window.addEventListener("pointermove", mv); window.addEventListener("pointerup", up);
  }
  const blockStart = (b) => parseFloat(b.style.left) / ctx.pps;
  const snap = (t, alt) => (alt ? t : ctx.snap(t));
  // the end of one stretch is the start of the next: one edge, both move
  function dragEdge(e, b, i){
    const segs = data.tone.segs, a = segs[i], n = segs[i + 1], from = a.end;
    const nb = b.nextElementSibling;
    drag(e, b, (dt, alt) => {
      const t = snap(Math.max(a.start + MIN, Math.min(n.end - MIN, from + dt)), alt);
      a.end = n.start = t;
      paintBlock(b, a); if(nb) paintBlock(nb, n);
    });
  }
  function dragFx(e, b, k, i){
    const L = data[k].blocks, x = L[i], prev = L[i - 1], next = L[i + 1];
    const edge = e.target.classList.contains("tl-grip") ? e.target.dataset.edge : null;
    const from = {start: x.start, end: x.end}, lo = prev ? prev.end : 0, hi = next ? next.start : ctx.duration;
    drag(e, b, (dt, alt) => {
      if(edge === "s") x.start = snap(Math.max(lo, Math.min(x.end - MIN, from.start + dt)), alt);
      else if(edge === "e") x.end = snap(Math.min(hi, Math.max(x.start + MIN, from.end + dt)), alt);
      else{
        const span = from.end - from.start;
        x.start = snap(Math.max(lo, Math.min(hi - span, from.start + dt)), alt);
        x.end = x.start + span;
      }
      paintBlock(b, x);
    });
  }

  // ---------------------------------------------------------------- the Otisky lane's edits
  function split(t){
    if(!data) return;
    const segs = data.tone.segs, i = segs.findIndex(s => t > s.start + MIN && t < s.end - MIN);
    if(i < 0){ say("Tady se otisk rozdělit nedá — přesuň se dovnitř otisku"); return; }
    const t2 = ctx.snap(t);
    segs.splice(i + 1, 0, {...segs[i], start: t2});
    segs[i].end = t2;
    changed();
    say("Otisk rozdělen — dvojklikem na novou část vybereš jiný zvuk");
  }
  function toneMenu(i, x, y){
    const segs = data.tone.segs;
    menu(x, y, [
      ["Vyměnit otisk…", () => pick(i)],
      ["Rozdělit na pozici přehrávání", () => split(ctx.time())],
      i < segs.length - 1 ? ["Spojit s dalším", () => { segs[i].end = segs[i + 1].end; segs.splice(i + 1, 1); changed(); }] : null,
      segs.length > 1 ? ["Smazat (zabere ho sousední)", () => {
        if(i > 0) segs[i - 1].end = segs[i].end; else segs[1].start = 0;
        segs.splice(i, 1); changed(); }] : null,
      ["Podle mapy DITORIGu", async () => { if(!data) return; const s = await fromPrints(song, ctx.duration, true); if(s.length){ data.tone.segs = s; changed(); } else say("Píseň nemá otisk z DITORIGu"); }],
      ["Podle otisku Picku (celá píseň)", async () => { if(!data) return; data.tone.segs = await fromPrints(song, ctx.duration); changed(); }],
    ].filter(Boolean));
  }
  function menu(x, y, items){
    document.querySelectorAll(".tl-menu").forEach(m => m.remove());
    const m = document.createElement("div");
    m.className = "tl-menu";
    items.forEach(([label, fn]) => { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.onclick = () => { m.remove(); fn(); }; m.appendChild(b); });
    (document.fullscreenElement || document.body).appendChild(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.min(x, innerWidth - r.width - 8) + "px"; m.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
    setTimeout(() => document.addEventListener("pointerdown", function off(ev){ if(!m.contains(ev.target)){ m.remove(); document.removeEventListener("pointerdown", off, true); } }, true), 0);
  }
  async function startTone(){
    const segs = data && data.tone.segs;
    if(!segs || !segs.length){ say("Píseň zatím nemá otisk"); return; }
    const t = ctx.time(), s = segs.find(x => t >= x.start && t < x.end) || segs[0];
    try{ await Tone.apply(song, s.pid, false, s.song); pushLane(); }catch(e){ say(e.message); }
    if(ctx) ctx.rerender();
  }

  // ---------------------------------------------------------------- the catalog of sounds
  async function sounds(){
    if(!catalog || Date.now() - catalogAt > 30000){ catalog = (await api("/api/tone/catalog")).sounds || []; catalogAt = Date.now(); }
    return catalog;
  }
  function bright(r){ const d = r.descriptor; if(!d || d.length < 24) return null; const h = d.length / 2; let lo = 0, hi = 0;
    for(let i = 0; i < 24; i++) (i < 12 ? (lo += d[i]) : (hi += d[i])); return (hi - lo) / 12; }
  // how far one sound is from another: DITORIG's fingerprints when both have one, else the kind and the drive
  function distance(a, b){
    if(!a) return 0;
    if(a.descriptor && b.descriptor && a.descriptor.length === b.descriptor.length){
      let s = 0; for(let i = 0; i < a.descriptor.length; i++){ const v = a.descriptor[i] - b.descriptor[i]; s += v * v; }
      return Math.sqrt(s / a.descriptor.length);
    }
    return 12 + (a.category === b.category ? 0 : 10) + Math.abs((a.drive_db || 0) - (b.drive_db || 0)) / 2;
  }
  async function pick(i, anchor){
    let rows;
    try{ rows = await sounds(); }catch(e){ say(e.message); return; }
    const seg = i >= 0 ? data.tone.segs[i] : null;
    const ref = seg ? (rows.find(r => r.key === seg.key) || rows.find(r => r.song === seg.song && r.pid === seg.pid)) : null;
    document.querySelectorAll(".tl-pick").forEach(m => m.remove());
    const box = document.createElement("div");
    box.className = "tl-pick";
    let find = "", cat = "", sort = "like";
    const draw = () => {
      const f = find.trim().toLowerCase();
      let list = rows.filter(r => (!f || (r.name + " " + (r.core || "") + " " + r.label).toLowerCase().includes(f)) && (!cat || r.category === cat));
      const sorters = {
        like: (a, b) => distance(ref, a) - distance(ref, b),
        gain: (a, b) => catOf(a.category).order - catOf(b.category).order || (a.drive_db || 0) - (b.drive_db || 0),
        bright: (a, b) => (bright(b) ?? -99) - (bright(a) ?? -99),
        uses: (a, b) => b.uses - a.uses, name: (a, b) => a.name.localeCompare(b.name)};
      list = list.slice().sort(sorters[sort]);
      const row = (r) => `<button type="button" class="tl-pr ${ref && r.key === ref.key ? "cur" : ""}" data-key="${esc(r.key)}" style="--c:${catOf(r.category).color}">
          <i></i><span class="tl-prn">${esc(r.name)}</span><small>${esc(catOf(r.category).label)}${r.source === "ditorig" ? " · DITORIG" : ""} · ${r.uses} ${r.uses === 1 ? "píseň" : r.uses < 5 ? "písně" : "písní"}</small>
          ${ref && sort === "like" && r.key !== ref.key ? `<em title="Jak moc se liší od současného">${Math.max(0, Math.round(100 - distance(ref, r) * 4))} %</em>` : ""}</button>`;
      let html = "";
      if(sort === "like" && ref && !f && !cat){
        html += `<div class="tl-ph">Nejpodobnější „${esc(ref.name)}“</div>` + list.filter(r => r.key !== ref.key).slice(0, 10).map(row).join("");
        for(const [k, label] of CATS){
          const g = rows.filter(r => r.category === k).sort((a, b) => distance(ref, a) - distance(ref, b));
          if(g.length) html += `<div class="tl-ph">${esc(label)} <small>${g.length}</small></div>` + g.map(row).join("");
        }
      } else html += list.map(row).join("") || `<p class="tl-pe">Nic takového v katalogu není.</p>`;
      box.querySelector(".tl-pl").innerHTML = html;
      box.querySelectorAll(".tl-pr").forEach(b => b.onclick = () => choose(rows.find(r => r.key === b.dataset.key)));
    };
    box.innerHTML = `<div class="tl-pt"><b>Katalog otisků</b>${seg ? `<span>${mmss(seg.start)}–${mmss(seg.end)}</span>` : ""}<button type="button" class="tl-px">${ic("close", 11)}</button></div>
      <input type="search" class="tl-pf" placeholder="Hledat zvuk, zesilovač, píseň…">
      <div class="tl-pc"><button type="button" data-c="" class="on">Vše</button>${CATS.filter(([k]) => rows.some(r => r.category === k)).map(([k, l, c]) => `<button type="button" data-c="${k}" style="--c:${c}">${l}</button>`).join("")}</div>
      <div class="tl-ps">Řadit: ${[["like", "podobnost"], ["gain", "zkreslení"], ["bright", "jas"], ["uses", "nejpoužívanější"], ["name", "název"]].map(([k, l]) => `<button type="button" data-s="${k}" class="${k === sort ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="tl-pl"></div>`;
    (document.fullscreenElement || document.body).appendChild(box);
    const a = (anchor || document.querySelector(".tl-tone")).getBoundingClientRect();
    box.style.left = Math.max(8, Math.min(a.left, innerWidth - 400)) + "px";
    box.style.top = Math.max(8, Math.min(a.bottom + 6, innerHeight - 480)) + "px";
    box.querySelector(".tl-px").onclick = () => box.remove();
    box.querySelector(".tl-pf").oninput = (e) => { find = e.target.value; draw(); };
    box.querySelectorAll("[data-c]").forEach(b => b.onclick = () => { cat = b.dataset.c; box.querySelectorAll("[data-c]").forEach(x => x.classList.toggle("on", x === b)); draw(); });
    box.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { sort = b.dataset.s; box.querySelectorAll("[data-s]").forEach(x => x.classList.toggle("on", x === b)); draw(); });
    const esc_ = (ev) => { if(ev.key === "Escape"){ box.remove(); document.removeEventListener("keydown", esc_); } };
    document.addEventListener("keydown", esc_);
    draw();
    box.querySelector(".tl-pf").focus();
    function choose(r){
      if(!r) return;
      box.remove();
      const s = {song: r.song, pid: r.pid, key: r.key, name: r.name, category: r.category};
      if(i >= 0) Object.assign(data.tone.segs[i], s);
      else data.tone.segs = [{start: 0, end: ctx.duration, ...s}];
      changed();
      say(`Otisk vyměněn: ${r.name}`);
    }
  }

  // ---------------------------------------------------------------- the effect lanes' edits
  function addFx(k, t){
    if(!data) return;
    const L = data[k].blocks, pr = FX[k].presets().find(x => x.name === FX[k].dflt) || FX[k].presets()[0];
    const next = L.find(x => x.start > t), host = L.find(x => t >= x.start && t < x.end);
    if(host){ editFx(k, L.indexOf(host)); return; }
    const end = Math.min(next ? next.start : ctx.duration, t + 8);
    if(end - t < MIN){ say("Tady už na úsek není místo"); return; }
    L.push({start: ctx.snap(t), end, name: pr.name, p: {...pr.p}});
    L.sort((a, b) => a.start - b.start);
    changed();
  }
  async function soloFill(k){
    if(!data) return;
    let solo;
    try{ solo = await api(`/api/tone/${enc(song)}/solo`); }catch(e){ say(e.message); return; }
    if(!(solo.regions || []).length){ say("Sólo jsem v písni nenašel — přidej úsek ručně tlačítkem +"); return; }
    const pr = FX[k].presets().find(x => x.name === FX[k].dflt) || FX[k].presets()[0];
    data[k].blocks = solo.regions.map(r => ({start: r.start, end: r.end, name: pr.name, p: {...pr.p}}));
    data[k].on = true;
    restoreFx(k);
    changed();
    say(`${FX[k].label} na ${solo.regions.length} ${solo.regions.length < 5 ? "sóla" : "sól"} (${solo.from === "guitar_solo" ? "podle stopy sólové kytary" : "podle sekcí"})`);
  }
  // one stretch's settings: a preset, or the knobs that matter most
  function editFx(k, i, anchor){
    const x = data[k].blocks[i], f = FX[k];
    document.querySelectorAll(".tl-fxed").forEach(m => m.remove());
    const box = document.createElement("div");
    box.className = "tl-fxed";
    box.style.setProperty("--c", f.color);
    const knobs = k === "delay"
      ? [["mix", "Mix", 0, 0.8, 0.01, (v) => Math.round(v * 100) + " %"], ["fb", "Opakování", 0, 0.9, 0.01, (v) => Math.round(v * 100) + " %"],
         ["tone", "Barva", 0, 1, 0.01, (v) => v < 0.35 ? "tmavá" : v > 0.65 ? "jasná" : "střed"], ["ms", "Čas (bez synchronizace)", 40, 1500, 10, (v) => Math.round(v) + " ms"]]
      : [["mix", "Mix", 0, 0.8, 0.01, (v) => Math.round(v * 100) + " %"], ["decay", "Délka dozvuku", 0.3, 10, 0.1, (v) => v.toFixed(1) + " s"],
         ["pre", "Pre-delay", 0, 150, 1, (v) => Math.round(v) + " ms"], ["tone", "Barva", 0, 1, 0.01, (v) => v < 0.35 ? "tmavá" : v > 0.65 ? "jasná" : "střed"]];
    const dflt = k === "delay" ? {mix: 0.3, fb: 0.35, tone: 0.5, ms: 380} : {mix: 0.25, decay: 2.4, pre: 20, tone: 0.55};
    box.innerHTML = `<div class="tl-pt"><b>${f.label} ${mmss(x.start)}–${mmss(x.end)}</b><button type="button" class="tl-px">${ic("close", 11)}</button></div>
      <div class="tl-fxp">${f.presets().map(pr => `<button type="button" data-pr="${esc(pr.name)}" class="${pr.name === x.name ? "on" : ""}" title="${esc(pr.tip)}">${esc(pr.name)}</button>`).join("")}</div>
      ${k === "delay" ? `<label class="tl-fxk"><span>Do tempa písně</span><select data-div><option value="">vypnuto (čas v ms)</option>${["1/2", "1/4.", "1/4", "1/4T", "1/8.", "1/8", "1/8T", "1/16"].map(d => `<option ${x.p.sync !== false && x.p.div === d ? "selected" : ""}>${d}</option>`).join("")}</select></label>` : ""}
      ${knobs.map(([key, label, lo, hi, step, fmt]) => `<label class="tl-fxk"><span>${label} <b data-v="${key}">${fmt(x.p[key] ?? dflt[key])}</b></span>
        <input type="range" min="${lo}" max="${hi}" step="${step}" value="${x.p[key] ?? dflt[key]}" data-k="${key}"></label>`).join("")}
      <div class="tl-fxa"><button type="button" class="tl-fxdel">Smazat úsek</button><button type="button" class="tl-fxok">Hotovo</button></div>`;
    (document.fullscreenElement || document.body).appendChild(box);
    const a = (anchor || document.querySelector(".tl-" + k) || document.body).getBoundingClientRect();
    box.style.left = Math.max(8, Math.min(a.left, innerWidth - 330)) + "px";
    box.style.top = Math.max(8, Math.min(a.bottom + 6, innerHeight - 420)) + "px";
    const live = () => { if(active[k] === x) { active[k] = null; } };   // heard anew at the next tick
    box.querySelectorAll("[data-pr]").forEach(b => b.onclick = () => {
      const pr = f.presets().find(p => p.name === b.dataset.pr);
      x.name = pr.name; x.p = {...pr.p}; live(); changed(); editFx(k, i, anchor);
    });
    box.querySelectorAll("input[data-k]").forEach(inp => inp.oninput = () => {
      const kk = inp.dataset.k, fmt = knobs.find(z => z[0] === kk)[5];
      x.p[kk] = parseFloat(inp.value); x.name = "vlastní";
      if(kk === "ms") x.p.sync = false;
      box.querySelector(`[data-v="${kk}"]`).textContent = fmt(x.p[kk]);
      box.querySelectorAll("[data-pr]").forEach(b => b.classList.remove("on"));
      live(); save();
    });
    const dv = box.querySelector("[data-div]");
    if(dv) dv.onchange = () => { if(dv.value){ x.p.sync = true; x.p.div = dv.value; } else x.p.sync = false; x.name = "vlastní"; live(); save(); };
    box.querySelector(".tl-px").onclick = box.querySelector(".tl-fxok").onclick = () => { box.remove(); if(ctx) ctx.rerender(); };
    box.querySelector(".tl-fxdel").onclick = () => { data[k].blocks.splice(i, 1); restoreFx(k); box.remove(); changed(); };
  }

  // ---------------------------------------------------------------- as the song plays
  const gfx = () => { try{ return Mixer.guitar.fx(); }catch(e){ return null; } };
  function setFx(k, x){
    const fx = gfx();
    if(!fx || typeof SpaceFx === "undefined") return;
    fx.state.space = fx.state.space || {};
    if(x){
      if(!active[k]) base[k] = {...(fx.state.space[k] || {})};                       // what it was before the first stretch
      fx.state.space[k] = {...(base[k] || {}), ...x.p, on: true};
    } else fx.state.space[k] = {...(base[k] || {on: false})};
    active[k] = x;
    SpaceFx.apply(fx);
    try{ Mixer.guitarFaderRepaint(); }catch(e){}
  }
  function restoreFx(only){
    for(const k of only ? [only] : ["delay", "reverb"]) if(active[k]) setFx(k, null);
  }
  function tick(){
    if(!song || !data || typeof Mixer === "undefined") return;
    if(Mixer.currentSong() !== song){ restoreFx(); return; }
    const t = Mixer.position();
    for(const k of ["delay", "reverb"]){
      const x = data[k].on === false ? null : data[k].blocks.find(b => t >= b.start && t < b.end) || null;
      if(x !== active[k]) setFx(k, x);
    }
    // what plays now, lit
    document.querySelectorAll(".tl-block").forEach(b => {
      const s = parseFloat(b.style.left) / ctx.pps, e = s + (parseFloat(b.style.width) + 2) / ctx.pps;
      b.classList.toggle("now", t >= s && t < e);
    });
  }

  return {render, reload: () => { if(song && ctx) load(song, ctx.duration); }, data: () => data, pick};
})();
