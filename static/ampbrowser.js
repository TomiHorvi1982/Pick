// Zesilovače a bedny — one floating window to choose a NAM profile or a cabinet
// impulse from, wherever one is chosen (the live guitar in Steems, a track of the
// mix, the player's guitar). The server's catalogue (amp_catalog.py) has sorted
// them: make and model, gain, kind of capture; a cabinet's pack, speaker,
// microphone and size. Found by any word, filtered, ordered by "my key", a page
// at a time; each heading shows a picture of the amp or cabinet (ChipThumbs).
// The window stays open while one profile after another is tried.
const AmpBrowser = (() => {
  const POS = "pick-ampb-pos", SORT = "pick-ampb-sort", PAGE = 150;
  const enc = encodeURIComponent;
  let win = null, opts = null, tab = "model", q = "", items = [], total = 0, all = 0, busy = false, token = 0, timer = 0;
  let sort = {model: "key", ir: "key"}, filt = {model: {}, ir: {}}, facets = {}, gains = {}, sel = null;
  try{ sort = {...sort, ...JSON.parse(localStorage.getItem(SORT) || "{}")}; }catch(e){}

  const SORTS = {
    model: [["key", "Můj klíč: značka → model → zisk"], ["gain", "Zisk: od čistého po hi-gain"], ["type", "Typ: zesilovač, aparát, krabička"], ["pack", "Odkud je (balíček)"], ["name", "Název A–Z"]],
    ir: [["key", "Můj klíč: balíček → bedna → reproduktor → mikrofon"], ["brand", "Značka a velikost bedny"], ["speaker", "Reproduktor"], ["mic", "Mikrofon"], ["name", "Název A–Z"]],
  };
  const FILTERS = {
    model: [["brand", "Značka"], ["gain", "Zisk"], ["type", "Typ"], ["pack", "Odkud"]],
    ir: [["pack", "Balíček"], ["brand", "Značka"], ["size", "Bedna"], ["speaker", "Reproduktor"], ["mic", "Mikrofon"]],
  };
  const GAIN_CLS = {clean: "g-clean", crunch: "g-crunch", hi_gain: "g-hot"};

  // ---- a head needs a cabinet. A profile of the amp alone (no cabinet, no microphone) sounds fizzy
  // through speakers; one of the whole rig already has its cabinet, and a second IR would muffle it.
  // So: a head gets the cabinet last paired with it, or the last one used with any head, or a
  // 4x12 with V30s; a full rig turns the IR off. Every cabinet chosen while a head plays is paired
  // with that head. "Bednu k hlavě samo" in the window's footer turns it off.
  const PAIR = "pick-ampb-pair", LAST_IR = "pick-ampb-last-ir", AUTO = "pick-ampb-autocab";
  const HEAD = new Set(["Zesilovač", "Předzesilovač", "Koncový zesilovač"]);
  const store = (k, d) => { try{ return JSON.parse(localStorage.getItem(k) || "null") ?? d; }catch(e){ return d; } };
  const keep = (k, v) => { try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} };
  let autoCab = store(AUTO, true), model = null;         // model: the item last loaded here, with its type
  async function defaultIr(){
    for(const q of ["4x12 v30 sm57", "v30", "4x12"]){
      try{ const d = await (await fetch(`/api/amp-catalog?kind=ir&limit=1&q=${enc(q)}`)).json(); if(d.items && d.items[0]) return {file: d.items[0].file, name: d.items[0].name}; }catch(e){}
    }
    return null;
  }
  async function cabFor(x){
    if(!opts.onIr || !autoCab || !x) return;
    const ir = (opts.current || {}).ir;
    if(HEAD.has(x.type) && !ir){
      const want = (store(PAIR, {})[x.file]) || store(LAST_IR, null) || await defaultIr();
      if(!want) return;
      await opts.onIr({file: want.file, name: want.name});
      opts.current = {...opts.current, ir: want.file};
      showToast(`„${x.name}“ je jen zesilovač — přidal jsem bednu ${want.name} (jinou vybereš v kartě Bedny)`);
    } else if(x.type === "Celý aparát" && ir){
      await opts.onIr(null);
      opts.current = {...opts.current, ir: ""};
      showToast(`„${x.name}“ už bednu má — IR jsem vypnul, aby nezněla dvakrát`);
    }
  }
  function remember(ir){
    if(!ir || !model || !HEAD.has(model.type)) return;
    const pair = store(PAIR, {});
    pair[model.file] = {file: ir.file, name: ir.name};
    keep(PAIR, pair); keep(LAST_IR, {file: ir.file, name: ir.name});
  }
  function hint(){
    const box = win.querySelector(".ampb-hint");
    const head = model && HEAD.has(model.type) && opts.onIr && !(opts.current || {}).ir;
    const rig = model && model.type === "Celý aparát" && opts.onIr && (opts.current || {}).ir;
    box.hidden = !(head || rig);
    if(head) box.innerHTML = `${icon("volume", 12)} „${escapeHtml(model.name)}“ je jen zesilovač bez bedny — bez IR zní ostře. <button type="button" data-go="ir">Vybrat bednu</button>`;
    else if(rig) box.innerHTML = `${icon("volume", 12)} „${escapeHtml(model.name)}“ je celý aparát i s bednou — IR navíc zvuk utlumí. <button type="button" data-off="1">Vypnout IR</button>`;
    const go = box.querySelector("[data-go]"); if(go) go.onclick = () => { tab = "ir"; paintHead(); load(true); };
    const off = box.querySelector("[data-off]"); if(off) off.onclick = async () => { await opts.onIr(null); opts.current = {...opts.current, ir: ""}; hint(); if(tab === "ir") paintList(false); };
  }

  function build(){
    win = document.createElement("div");
    win.className = "ampb-win";
    win.id = "ampb-win";
    win.innerHTML = `<div class="ampb-bar"><span class="ampb-title"></span><span class="ampb-tabs"></span><span class="sp"></span>
        <button type="button" class="ampb-close" title="Zavřít">${icon("close", 14)}</button></div>
      <div class="ampb-tools"><input type="search" class="ampb-q" placeholder="Hledat — značka, model, zvuk, reproduktor, mikrofon…" autocomplete="off">
        <select class="ampb-sort" title="Řazení"></select></div>
      <div class="ampb-filters"></div><div class="ampb-hint" hidden></div>
      <div class="ampb-main"><div class="ampb-list"></div>
        <aside class="ampb-side"><div class="ampb-pic"><span>${icon("sliders", 34)}</span></div><div class="ampb-info"></div></aside></div>
      <div class="ampb-foot"></div>`;
    document.body.appendChild(win);
    try{ const p = JSON.parse(localStorage.getItem(POS) || "null"); if(p){ Object.assign(win.style, {left: p.x + "px", top: p.y + "px", width: p.w + "px", height: p.h + "px"}); } }catch(e){}
    win.querySelector(".ampb-close").onclick = close;
    const qi = win.querySelector(".ampb-q");
    qi.oninput = () => { q = qi.value; clearTimeout(timer); timer = setTimeout(() => load(true), 180); };
    qi.onkeydown = (e) => { if(e.key === "Escape"){ if(qi.value){ qi.value = ""; qi.oninput(); } else close(); } };
    win.querySelector(".ampb-sort").onchange = (e) => { sort[tab] = e.target.value; try{ localStorage.setItem(SORT, JSON.stringify(sort)); }catch(x){} load(true); };
    const list = win.querySelector(".ampb-list");
    list.onscroll = () => { if(list.scrollTop + list.clientHeight > list.scrollHeight - 400) more(); };
    const bar = win.querySelector(".ampb-bar");
    bar.onpointerdown = (e) => {
      if(e.button || e.target.closest("button")) return;
      const r = win.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      bar.setPointerCapture(e.pointerId);
      bar.onpointermove = (m) => {
        win.style.left = Math.max(0, Math.min(innerWidth - 120, m.clientX - dx)) + "px";
        win.style.top = Math.max(0, Math.min(innerHeight - 40, m.clientY - dy)) + "px";
      };
      bar.onpointerup = () => { bar.onpointermove = bar.onpointerup = null; keepPos(); };
    };
    new ResizeObserver(() => keepPos()).observe(win);
  }
  function keepPos(){
    if(!win || win.hidden) return;
    const r = win.getBoundingClientRect();
    try{ localStorage.setItem(POS, JSON.stringify({x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)})); }catch(e){}
  }

  function paintHead(){
    win.querySelector(".ampb-title").textContent = opts.title || "Zesilovače a bedny";
    const kinds = opts.kinds || ["model", "ir"];
    win.querySelector(".ampb-tabs").innerHTML = kinds.length > 1 ? kinds.map(k =>
      `<button type="button" data-k="${k}" class="${k === tab ? "on" : ""}">${k === "ir" ? "Bedny (IR)" : "Zesilovače (NAM)"}</button>`).join("") : "";
    win.querySelectorAll(".ampb-tabs [data-k]").forEach(b => b.onclick = () => { tab = b.dataset.k; sel = null; paintHead(); load(true); });
    const s = win.querySelector(".ampb-sort");
    s.innerHTML = SORTS[tab].map(([v, l]) => `<option value="${v}" ${sort[tab] === v ? "selected" : ""}>${l}</option>`).join("");
  }
  function paintFilters(){
    const box = win.querySelector(".ampb-filters"), f = filt[tab];
    box.innerHTML = FILTERS[tab].map(([k, l]) => {
      const opts_ = facets[k] || [];
      if(!opts_.length && !f[k]) return "";
      const name = (v) => k === "gain" ? (gains[v] || v) : v;
      return `<select data-f="${k}" class="${f[k] ? "on" : ""}" title="${l}"><option value="">${l}: vše</option>` +
        opts_.map(([v, n]) => `<option value="${escapeHtml(v)}" ${f[k] === v ? "selected" : ""}>${escapeHtml(name(v))} (${n})</option>`).join("") +
        (f[k] && !opts_.some(o => o[0] === f[k]) ? `<option value="${escapeHtml(f[k])}" selected>${escapeHtml(name(f[k]))}</option>` : "") + `</select>`;
    }).join("") + (Object.values(f).some(Boolean) ? `<button type="button" class="ampb-clear">Zrušit filtry</button>` : "");
    box.querySelectorAll("select[data-f]").forEach(s => s.onchange = () => { f[s.dataset.f] = s.value; load(true); });
    const c = box.querySelector(".ampb-clear"); if(c) c.onclick = () => { filt[tab] = {}; load(true); };
  }

  async function load(reset){
    if(reset){ items = []; total = 0; }
    const mine = ++token;
    busy = true;
    const f = filt[tab];
    const qs = `kind=${tab}&q=${enc(q)}&sort=${sort[tab]}&offset=${items.length}&limit=${PAGE}` +
      Object.keys(f).filter(k => f[k]).map(k => `&${k}=${enc(f[k])}`).join("");
    if(reset) win.querySelector(".ampb-list").innerHTML = `<div class="ampb-empty"><span class="spinner"></span> Třídím sbírku…</div>`;
    let d = null;
    try{ d = await (await fetch("/api/amp-catalog?" + qs)).json(); }catch(e){}
    if(mine !== token) return;
    busy = false;
    if(!d || !Array.isArray(d.items)){ win.querySelector(".ampb-list").innerHTML = `<div class="ampb-empty">Katalog se nepodařilo načíst.</div>`; return; }
    items = items.concat(d.items); total = d.total; all = d.all; facets = d.facets || {}; gains = d.gains || {};
    if(reset){ paintFilters(); win.querySelector(".ampb-list").scrollTop = 0; }
    paintList(reset);
  }
  function more(){ if(!busy && items.length < total) load(false); }

  const cur = () => (opts.current || {})[tab] || "";
  function row(x){
    const on = x.file === cur();
    const tags = tab === "model"
      ? [x.gain ? `<i class="${GAIN_CLS[x.gain] || ""}">${escapeHtml(gains[x.gain] || x.gain)}</i>` : "", x.type !== "Zesilovač" ? `<i>${escapeHtml(x.type)}</i>` : ""]
      : [x.size ? `<i>${escapeHtml(x.size)}</i>` : "", x.speaker ? `<i>${escapeHtml(x.speaker)}</i>` : "", x.mic ? `<i class="g-mic">${escapeHtml(x.mic)}</i>` : ""];
    return `<button type="button" class="ampb-row${on ? " on" : ""}" data-file="${escapeHtml(x.file)}">
      <span class="ab-n">${escapeHtml(x.name)}</span><span class="ab-t">${tags.join("")}</span></button>`;
  }
  function paintList(reset){
    const list = win.querySelector(".ampb-list");
    if(!items.length){
      list.innerHTML = `<div class="ampb-empty">${q || Object.values(filt[tab]).some(Boolean) ? "Nic neodpovídá — zkus jiné slovo nebo zruš filtr." : tab === "ir" ? "Zatím žádné bedny." : "Zatím žádné NAM profily."}</div>`;
    } else {
      // only what came now is added: the rows already there keep their place and pictures
      const from = reset ? 0 : list.querySelectorAll(".ampb-row").length;
      if(reset) list.innerHTML = "";
      let last = from ? items[from - 1].group : null, grp = from ? list.lastElementChild : null;
      const frag = document.createDocumentFragment();
      for(let i = from; i < items.length; i++){
        const x = items[i];
        if(x.group !== last){
          last = x.group;
          grp = document.createElement("section");
          grp.className = "ampb-grp";
          grp.innerHTML = `<h4><span class="ampb-th"></span><span class="t">${escapeHtml(x.group)}</span></h4>`;
          if(sort[tab] !== "name") ChipThumbs.observe(grp.querySelector("h4"), x.picture, ".ampb-th");
          frag.appendChild(grp);
        }
        grp.insertAdjacentHTML("beforeend", row(x));
      }
      list.appendChild(frag);
      list.querySelectorAll(".ampb-row:not([data-w])").forEach(b => {
        b.dataset.w = "1";
        const x = items.find(i => i.file === b.dataset.file);
        b.onmouseenter = () => preview(x);
        b.onclick = () => choose(x, b);
      });
    }
    const kind = tab === "ir" ? "beden" : "profilů";
    win.querySelector(".ampb-foot").innerHTML = `<span>${total} z ${all} ${kind}${tab === "ir" ? " (každá bedna jednou, 48 kHz)" : ""}</span><span class="sp"></span>
      ${opts.onIr ? `<label class="ampb-auto" title="K profilu, který je jen zesilovač, přidat bednu samo (tu, co jsi k němu vybral naposledy); u celého aparátu IR vypnout"><input type="checkbox" ${autoCab ? "checked" : ""}> Bednu k hlavě samo</label>` : ""}
      ${(opts.extra || []).map((x, i) => `<button type="button" class="ampb-none" data-x="${i}">${escapeHtml(x.label)}</button>`).join("")}
      <button type="button" class="ampb-none">${icon("close", 11)} ${tab === "ir" ? "Bez bedny" : "Bez zesilovače"}</button>`;
    win.querySelectorAll(".ampb-foot [data-x]").forEach(b => b.onclick = () => { close(); opts.extra[+b.dataset.x].onclick(); });
    win.querySelector(".ampb-none:not([data-x])").onclick = () => choose(null, null);
    const ac = win.querySelector(".ampb-auto input"); if(ac) ac.onchange = () => { autoCab = ac.checked; keep(AUTO, autoCab); };
    if(reset) preview(items.find(i => i.file === cur()) || items[0] || null);
  }
  function preview(x){
    sel = x;
    const pic = win.querySelector(".ampb-pic"), info = win.querySelector(".ampb-info");
    pic.style.backgroundImage = ""; pic.classList.remove("has-thumb");
    pic.innerHTML = `<span>${icon(tab === "ir" ? "volume" : "sliders", 34)}</span>`;
    if(!x){ info.innerHTML = ""; return; }
    const holder = document.createElement("div");
    holder.className = "ampb-pic-in";
    pic.appendChild(holder);
    ChipThumbs.observe(pic, x.picture, ".ampb-pic-in");
    const line = (k, v) => v ? `<div><span>${k}</span>${escapeHtml(v)}</div>` : "";
    info.innerHTML = `<b>${escapeHtml(x.name)}</b>` + (tab === "model"
      ? line("Značka", x.brand) + line("Model", x.family !== x.brand ? x.family : "") + line("Zisk", x.gain ? gains[x.gain] : "") + line("Typ", x.type) + line("Odkud", x.pack) + line("Nahrál", x.by)
      : line("Balíček", x.pack) + line("Bedna", x.cab !== x.pack ? x.cab : "") + line("Značka", x.brand !== "Ostatní" ? x.brand : "") + line("Velikost", x.size) + line("Reproduktor", x.speaker) + line("Mikrofon", x.mic))
      + `<p>Klik na řádek ho načte — okno zůstane otevřené, můžeš zkoušet další.</p>`;
  }
  async function choose(x, btn){
    const fn = tab === "ir" ? opts.onIr : opts.onModel;
    if(!fn) return;
    win.querySelectorAll(".ampb-row.on").forEach(b => b.classList.remove("on"));
    if(btn) btn.classList.add("on");
    opts.current = {...(opts.current || {}), [tab]: x ? x.file : ""};
    try{ await fn(x ? {file: x.file, name: x.name, item: x} : null); }catch(e){ showToast(e.message || "Nepodařilo se načíst"); return; }
    if(tab === "model"){ model = x; try{ await cabFor(x); }catch(e){} }
    else remember(x);
    hint();
  }

  // {title, kinds: ["model", "ir"], tab, current: {model, ir}, onModel(item|null), onIr(item|null)}
  function open(o){
    opts = o || {};
    if(!win) build();
    const kinds = opts.kinds || ["model", "ir"];
    tab = kinds.includes(opts.tab) ? opts.tab : kinds[0];
    win.hidden = false;
    paintHead();
    load(true);
    model = null; hint();
    const cm = (opts.current || {}).model;
    if(cm) fetch(`/api/amp-catalog?kind=model&limit=500&q=${enc(cm.split("/").pop().replace(/\.nam$/i, ""))}`).then(r => r.json())
      .then(d => { model = (d.items || []).find(i => i.file === cm) || null; hint(); }).catch(() => {});
    setTimeout(() => win.querySelector(".ampb-q").focus(), 0);
  }
  function close(){ if(win) win.hidden = true; }
  return {open, close, isOpen: () => !!win && !win.hidden};
})();
