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

  function build(){
    win = document.createElement("div");
    win.className = "ampb-win";
    win.id = "ampb-win";
    win.innerHTML = `<div class="ampb-bar"><span class="ampb-title"></span><span class="ampb-tabs"></span><span class="sp"></span>
        <button type="button" class="ampb-close" title="Zavřít">${icon("close", 14)}</button></div>
      <div class="ampb-tools"><input type="search" class="ampb-q" placeholder="Hledat — značka, model, zvuk, reproduktor, mikrofon…" autocomplete="off">
        <select class="ampb-sort" title="Řazení"></select></div>
      <div class="ampb-filters"></div>
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
      ${(opts.extra || []).map((x, i) => `<button type="button" class="ampb-none" data-x="${i}">${escapeHtml(x.label)}</button>`).join("")}
      <button type="button" class="ampb-none">${icon("close", 11)} ${tab === "ir" ? "Bez bedny" : "Bez zesilovače"}</button>`;
    win.querySelectorAll(".ampb-foot [data-x]").forEach(b => b.onclick = () => { close(); opts.extra[+b.dataset.x].onclick(); });
    win.querySelector(".ampb-none:not([data-x])").onclick = () => choose(null, null);
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
    try{ await fn(x ? {file: x.file, name: x.name, item: x} : null); }catch(e){ showToast(e.message || "Nepodařilo se načíst"); }
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
    setTimeout(() => win.querySelector(".ampb-q").focus(), 0);
  }
  function close(){ if(win) win.hidden = true; }
  return {open, close, isOpen: () => !!win && !win.hidden};
})();
