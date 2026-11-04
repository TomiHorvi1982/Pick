// Songs with Guitar Stamps — first on the Steems page and first under Steems in the left panel.
// Folded: a row of songs with a print picked at random. Unfolded: all of them, in two groups —
// Approved (checked: the sound and the song's setup right, ready to play) and To Review (still to
// be listened to and set up). A song is approved from its tile, or in the mix (the Otisky lane).
// The list: /api/stamps/songs; approving: /api/stamps/songs/{song}/approve.
const StampSongs = (() => {
  const enc = encodeURIComponent;
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const ic = (n, s = 14) => (typeof icon === "function" ? icon(n, s) : "");
  const OPEN = "pick-stampsongs-open", GROUPS = "pick-stampsongs-groups", RAIL = "pick-stampsongs-rail";
  const PAGE = 60, RANDOM = 14;
  let rows = null, at = 0, loading = null, shuffle = [], open = false, groups = {approved: true, review: true}, shown = {approved: PAGE, review: PAGE};
  let railOpen = true;
  try{ open = localStorage.getItem(OPEN) === "1"; groups = {...groups, ...JSON.parse(localStorage.getItem(GROUPS) || "{}")}; railOpen = localStorage.getItem(RAIL) !== "0"; }catch(e){}
  const keep = () => { try{ localStorage.setItem(OPEN, open ? "1" : "0"); localStorage.setItem(GROUPS, JSON.stringify(groups)); localStorage.setItem(RAIL, railOpen ? "1" : "0"); }catch(e){} };

  async function load(force){
    if(rows && !force && Date.now() - at < 60000) return rows;
    if(loading) return loading;
    loading = fetch("/api/stamps/songs" + (force ? "?fresh=true" : "")).then(r => r.json()).then(d => {
      rows = d.songs || []; at = Date.now(); loading = null;
      shuffle = rows.slice().sort(() => Math.random() - 0.5).slice(0, RANDOM).map(x => x.name);
      return rows;
    }).catch(() => { loading = null; return rows || []; });
    return loading;
  }
  const songOf = (name) => (typeof steemsSongs !== "undefined" && steemsSongs.find(x => x.name === name)) || {name};
  const shown_ = (name) => { const d = typeof stemsDisplay === "function" ? stemsDisplay(songOf(name)) : {title: name}; return d; };
  const counts = () => ({all: rows.length, approved: rows.filter(x => x.approved).length, review: rows.filter(x => !x.approved).length});

  // ---------------------------------------------------------------- a tile
  let io = null;
  function art(a, name){
    if(typeof StemsArt === "undefined") return;
    if(!io) io = new IntersectionObserver(es => es.forEach(e => { if(e.isIntersecting){ io.unobserve(e.target); StemsArt.paint(e.target, e.target._name); } }), {rootMargin: "200px"});
    a._name = name; io.observe(a);
  }
  function tile(r){
    const d = shown_(r.name);
    const c = document.createElement("div");
    c.className = "card steems-tile gss-tile " + (r.approved ? "ok" : "rev");
    c.title = `${d.artist ? d.artist + " — " : ""}${d.title}\n${r.prints} ${r.prints === 1 ? "otisk" : r.prints < 5 ? "otisky" : "otisků"}${r.pending ? ` · ${r.pending} ke kontrole` : ""}`;
    c.innerHTML = `<div class="art">${ic("stems", 30)}</div>
      <span class="gss-badge">${r.approved ? "✓ Approved" : "To Review"}</span>
      <button type="button" class="gss-appr" title="${r.approved ? "Vrátit mezi To Review" : "Schválit: zvuk i nastavení písně sedí, je připravená na hraní"}">${r.approved ? "↺" : "✓"}</button>
      <div class="meta"><div class="title">${esc(d.title)}</div><div class="sub">${esc(d.artist || "")} · ${r.prints} ${ic("guitar", 10)}</div></div>`;
    art(c.querySelector(".art"), r.name);
    c.onclick = () => { if(typeof loadMixerSong === "function") loadMixerSong(r.name); };
    c.querySelector(".gss-appr").onclick = (e) => { e.stopPropagation(); approve(r.name, !r.approved); };
    return c;
  }

  // ---------------------------------------------------------------- on the Steems page
  let box = null;
  function section(){
    box = document.createElement("div");
    box.className = "gss-songs";
    box.id = "gss-songs";
    box.innerHTML = `<div class="shelf-head"><h2 class="h">${ic("guitar", 18)} Songs with Guitar Stamps</h2></div><p class="gss-note"><span class="spinner"></span></p>`;
    load().then(paint);
    return box;
  }
  function paint(){
    if(!box || !rows) return;
    const c = counts();
    if(!c.all){ box.innerHTML = ""; box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="shelf-head gss-sh"><h2 class="h">${ic("guitar", 18)} Songs with Guitar Stamps</h2>
        <span class="gss-cnt">${c.all} · <b class="ok">${c.approved} Approved</b> · <b class="rev">${c.review} To Review</b></span>
        ${open ? "" : `<button type="button" class="chip gss-shuf" title="Jiné náhodné písně">${ic("shuffle", 12)}</button>`}
        <a class="all gss-tog" href="#">${open ? "Show less" : `Show all (${c.all})`}</a></div>
      <div class="gss-body"></div>`;
    const body = box.querySelector(".gss-body");
    box.querySelector(".gss-tog").onclick = (e) => { e.preventDefault(); open = !open; keep(); paint(); };
    const sh = box.querySelector(".gss-shuf");
    if(sh) sh.onclick = () => { shuffle = rows.slice().sort(() => Math.random() - 0.5).slice(0, RANDOM).map(x => x.name); paint(); };
    if(!open){
      const row = document.createElement("div");
      row.className = "shelf-row";
      shuffle.map(n => rows.find(x => x.name === n)).filter(Boolean).forEach(r => row.appendChild(tile(r)));
      body.appendChild(row);
      return;
    }
    for(const [k, label, tip] of [["approved", "Approved", "zkontrolované — zvuk i nastavení sedí, připravené na hraní"],
                                  ["review", "To Review", "potřebují poslechnout a nastavit"]]){
      const list = rows.filter(x => (k === "approved") === x.approved).sort((a, b) =>
        k === "approved" ? (b.at || 0) - (a.at || 0) : (typeof steemsCollator !== "undefined" ? steemsCollator.compare(shown_(a.name).artist || "~", shown_(b.name).artist || "~") : 0));
      const g = document.createElement("div");
      g.className = "gss-group " + k;
      g.dataset.g = k;
      g.innerHTML = `<button type="button" class="gss-gh">${groups[k] ? "▾" : "▸"} <b>${label}</b> <small>${list.length}</small> <span>${tip}</span></button>`;
      g.querySelector(".gss-gh").onclick = () => { groups[k] = !groups[k]; keep(); paint(); };
      if(groups[k]){
        const grid = document.createElement("div");
        grid.className = "gss-grid";
        list.slice(0, shown[k]).forEach(r => grid.appendChild(tile(r)));
        g.appendChild(grid);
        if(!list.length) g.insertAdjacentHTML("beforeend", `<p class="gss-note">${k === "approved" ? "Zatím žádná — u písně, kde ti zvuk i nastavení sedí, klikni na ✓." : "Všechno zkontrolováno."}</p>`);
        if(list.length > shown[k]){
          const more = document.createElement("button");
          more.type = "button"; more.className = "btn secondary gss-more";
          more.textContent = `Show more (${list.length - shown[k]})`;
          more.onclick = () => { shown[k] += PAGE * 2; paint(); };
          g.appendChild(more);
        }
      }
      body.appendChild(g);
    }
  }

  // ---------------------------------------------------------------- approving
  async function approve(name, on){
    try{
      const r = await fetch(`/api/stamps/songs/${enc(name)}/approve`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({approved: on})});
      if(!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Nepodařilo se");
    }catch(e){ if(typeof showToast === "function") showToast(e.message); return false; }
    const row = rows && rows.find(x => x.name === name);
    if(row){ row.approved = on; row.at = Date.now() / 1000; if(on) row.pending = 0; }
    if(typeof showToast === "function") showToast(on ? `Approved: ${shown_(name).title} — připravená na hraní` : `${shown_(name).title} je zpátky v To Review`);
    paint(); repaintRail();
    return true;
  }
  const isApproved = (name) => !!(rows && (rows.find(x => x.name === name) || {}).approved);

  // ---------------------------------------------------------------- in the left panel, above the playlists
  let rail = null;
  function railBox(collapsed){
    rail = document.createElement("div");
    rail.className = "sl-box gss-rail";
    rail.dataset.sec = "steems";
    rail.hidden = collapsed;
    if(!rows){ load().then(repaintRail); rail.innerHTML = `<div class="sl-head">Songs with Guitar Stamps</div><div class="sl-empty">Načítám…</div>`; return rail; }
    fillRail();
    return rail;
  }
  function fillRail(){
    if(!rail || !rows) return;
    const c = counts();
    if(!c.all){ rail.innerHTML = ""; return; }
    rail.innerHTML = `<button type="button" class="sl-head gss-rh">${railOpen ? "▾" : "▸"} Songs with Guitar Stamps <span>${c.all}</span></button>
      ${railOpen ? `<button type="button" class="gss-rr ok" data-go="approved">✓ Approved <span>${c.approved}</span></button>
        <button type="button" class="gss-rr rev" data-go="review">● To Review <span>${c.review}</span></button>` : ""}`;
    rail.querySelector(".gss-rh").onclick = () => { railOpen = !railOpen; keep(); fillRail(); };
    rail.querySelectorAll("[data-go]").forEach(b => b.onclick = () => go(b.dataset.go));
  }
  function repaintRail(){ if(rail && rail.isConnected) fillRail(); }
  // the Steems page, this section unfolded at a group
  async function go(k){
    open = true; groups[k] = true; keep();
    if(typeof setMode === "function") setMode("steems");
    if(typeof loadSteemsList === "function") await loadSteemsList({cached: true});
    await load();
    paint();
    const g = document.querySelector(`#gss-songs .gss-group[data-g="${k}"]`);
    if(g) g.scrollIntoView({block: "start", behavior: "smooth"});
  }

  return {section, railBox, approve, isApproved, reload: () => load(true).then(() => { paint(); repaintRail(); }), go};
})();
