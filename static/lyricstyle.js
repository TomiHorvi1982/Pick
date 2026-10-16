// ======================================================================
// Styl textu — how a song's text looks wherever Pick shows it: Steems (its lines, the karaoke list,
// the full screen), the player's text, the mini player. A preset, a size and two colours, kept in
// localStorage — one origin, so every window of Pick shares it — and passed on to the other windows
// as it changes (the storage event). Pages read it as CSS variables on the root:
//   --ly-size (a factor), --ly-cur (the line being sung), --ly-fill (how far it is sung; unset =
//   the page's own accent), --ly-dim (the other lines), --ly-weight, --ly-font, --ly-glow (0–2).
// Also here: KaraokeList, the mini player's way of showing text — every line in a scrolling list,
// the one being sung lit and filling from left to right — for any page that shows a timed text.
// Loaded in <head>, before the pages' own scripts; needs nothing of theirs.
// ======================================================================
const LyricStyle = (() => {
  const KEY = "pick-lyric-style", EVENT = "pick-lyric-style";
  const PRESETS = {
    karaoke: {name: "Karaoke", cur: "#ffffff", fill: "", dim: "rgba(255,255,255,.30)", weight: 800, font: "", glow: 1},
    klasik: {name: "Klasický", cur: "#ffd166", fill: "#ffd166", dim: "rgba(255,255,255,.45)", weight: 700, font: "", glow: 0},
    kontrast: {name: "Kontrast", cur: "#ffff3a", fill: "#ffffff", dim: "rgba(255,255,255,.60)", weight: 900, font: "", glow: 0},
    jemny: {name: "Jemný", cur: "#f4f1ea", fill: "#9ad0ff", dim: "rgba(255,255,255,.22)", weight: 500, font: "", glow: 0},
    neon: {name: "Neon", cur: "#7df9ff", fill: "#ff4fd8", dim: "rgba(125,249,255,.28)", weight: 800, font: "", glow: 2},
    kniha: {name: "Kniha", cur: "#fff4dc", fill: "#e0b86b", dim: "rgba(255,244,220,.32)", weight: 600, font: "Georgia, 'Times New Roman', serif", glow: 0},
  };
  const DEFAULT = {preset: "karaoke", size: 1, cur: "", fill: ""};
  const SIZES = [0.6, 2.5];

  function get(){
    let s = {};
    try{ s = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; }catch(e){}
    const out = {...DEFAULT, ...s};
    if(!PRESETS[out.preset]) out.preset = DEFAULT.preset;
    out.size = Math.min(SIZES[1], Math.max(SIZES[0], Number(out.size) || 1));
    return out;
  }
  // What the page draws with: the preset, with the player's own colours over it.
  function look(){
    const s = get(), p = PRESETS[s.preset];
    return {...p, preset: s.preset, size: s.size, cur: s.cur || p.cur, fill: s.fill || p.fill};
  }
  function apply(){
    const l = look(), r = document.documentElement.style;
    r.setProperty("--ly-size", String(l.size));
    r.setProperty("--ly-cur", l.cur);
    if(l.fill) r.setProperty("--ly-fill", l.fill); else r.removeProperty("--ly-fill");
    r.setProperty("--ly-dim", l.dim);
    r.setProperty("--ly-weight", String(l.weight));
    if(l.font) r.setProperty("--ly-font", l.font); else r.removeProperty("--ly-font");
    r.setProperty("--ly-glow", String(l.glow));
  }
  function set(patch){
    const s = {...get(), ...patch};
    try{ localStorage.setItem(KEY, JSON.stringify(s)); }catch(e){}
    apply();
    window.dispatchEvent(new Event(EVENT));
  }
  window.addEventListener("storage", (e) => { if(e.key === KEY){ apply(); window.dispatchEvent(new Event(EVENT)); } });
  apply();

  // ---- the panel: presets, size, colours
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const hex = (c) => /^#[0-9a-f]{6}$/i.test(c || "") ? c : "#ffffff";
  let css = false;
  function style(){
    if(css) return;
    css = true;
    const st = document.createElement("style");
    st.textContent = `
      .lys-pop{position:fixed;z-index:100000;width:268px;padding:12px;border-radius:12px;background:#1b1b22;color:#eef0f4;
        border:1px solid rgba(255,255,255,.12);box-shadow:0 12px 36px rgba(0,0,0,.55);font:13px/1.35 system-ui,-apple-system,sans-serif}
      .lys-pop h4{margin:0 0 8px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#9aa3b5}
      .lys-pre{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:12px}
      .lys-pre button{border:1px solid rgba(255,255,255,.12);background:#0e0e12;border-radius:8px;padding:7px 4px;cursor:pointer;font-size:12px}
      .lys-pre button.on{border-color:#ffd166;box-shadow:0 0 0 1px #ffd166 inset}
      .lys-row{display:flex;align-items:center;gap:8px;margin:8px 0}
      .lys-row label{flex:1;color:#c9cfdb}
      .lys-row input[type=range]{flex:1.3}
      .lys-row input[type=color]{width:34px;height:24px;border:none;background:none;padding:0;cursor:pointer}
      .lys-row .v{min-width:38px;text-align:right;font-variant-numeric:tabular-nums;color:#9aa3b5}
      .lys-demo{margin:10px 0 2px;padding:10px;border-radius:8px;background:#0b0b0e;text-align:center;line-height:1.25;overflow:hidden}
      .lys-demo .a{display:block;font-size:calc(13px * var(--ly-size,1));color:var(--ly-dim);font-family:var(--ly-font,inherit)}
      .lys-demo .b{display:block;font-size:calc(19px * var(--ly-size,1));font-weight:var(--ly-weight,800);font-family:var(--ly-font,inherit);
        background:linear-gradient(90deg, var(--ly-fill, #1ed760) 55%, var(--ly-cur,#fff) 55%);-webkit-background-clip:text;background-clip:text;color:transparent}
      .lys-foot{display:flex;justify-content:space-between;margin-top:10px}
      .lys-foot button{background:none;border:none;color:#9aa3b5;cursor:pointer;font-size:12px;padding:2px}
      .lys-foot button:hover{color:#fff}`;
    document.head.appendChild(st);
  }
  function open(anchor){
    style();
    const old = document.querySelector(".lys-pop");
    if(old){ old.remove(); if(old._anchor === anchor) return; }
    const box = document.createElement("div");
    box.className = "lys-pop";
    box._anchor = anchor;
    const paint = () => {
      const s = get(), l = look();
      box.innerHTML = `<h4>Styl textu</h4>
        <div class="lys-pre">${Object.entries(PRESETS).map(([k, p]) => `<button type="button" data-p="${k}" class="${k === s.preset ? "on" : ""}"
          style="color:${esc(p.cur)};font-weight:${p.weight};font-family:${esc(p.font || "inherit")}">${esc(p.name)}</button>`).join("")}</div>
        <div class="lys-row"><label>Velikost</label><input type="range" min="${SIZES[0]}" max="${SIZES[1]}" step="0.05" value="${s.size}" data-k="size"><span class="v">${Math.round(s.size * 100)} %</span></div>
        <div class="lys-row"><label>Barva zpívaného řádku</label><input type="color" value="${hex(l.cur)}" data-k="cur"></div>
        <div class="lys-row"><label>Barva postupu zpěvu</label><input type="color" value="${hex(l.fill || "#1ed760")}" data-k="fill"></div>
        <div class="lys-demo"><span class="a">Řádek, který byl</span><span class="b">Řádek, který se zpívá</span><span class="a">Řádek, který přijde</span></div>
        <div class="lys-foot"><button type="button" data-a="colours">Barvy podle předvolby</button><button type="button" data-a="reset">Výchozí</button></div>
        <div style="font-size:11px;color:#9aa3b5;margin-top:6px">Platí všude — ve Steems, v přehrávači i v minipřehrávači.</div>`;
      box.querySelectorAll("[data-p]").forEach((b) => { b.onclick = () => { set({preset: b.dataset.p, cur: "", fill: ""}); paint(); }; });
      const size = box.querySelector("[data-k=size]");
      size.oninput = () => { set({size: Number(size.value)}); box.querySelector(".lys-row .v").textContent = Math.round(Number(size.value) * 100) + " %"; };
      box.querySelectorAll("input[type=color]").forEach((c) => { c.oninput = () => set({[c.dataset.k]: c.value}); });
      box.querySelector("[data-a=colours]").onclick = () => { set({cur: "", fill: ""}); paint(); };
      box.querySelector("[data-a=reset]").onclick = () => { set({...DEFAULT}); paint(); };
    };
    paint();
    document.body.appendChild(box);
    const r = anchor.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + "px";
    box.style.top = (r.bottom + h + 8 < window.innerHeight ? r.bottom + 6 : Math.max(8, r.top - h - 6)) + "px";
    setTimeout(() => document.addEventListener("pointerdown", function off(e){
      if(!box.isConnected){ document.removeEventListener("pointerdown", off); return; }
      if(!box.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)){ box.remove(); document.removeEventListener("pointerdown", off); }
    }), 0);
  }
  // A button that opens the panel: <button class="ly-style-btn"> anywhere, wired once.
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest(".ly-style-btn");
    if(b){ e.preventDefault(); e.stopPropagation(); open(b); }
  }, true);
  const button = (extra = "") => `<button type="button" class="ly-style-btn ${extra}" title="Styl textu: předvolby, velikost, barvy">Aa</button>`;

  return {PRESETS, get, look, set, apply, open, button, EVENT};
})();

// The text as a scrolling list, the line being sung lit and filling as it goes (the mini player's look).
// lines: [{t (seconds, already shifted), text}]; draw(t) on every tick; a click on a line seeks there.
function KaraokeList(box, {onSeek} = {}){
  let lines = [], els = [], cur = -2, userAt = 0;
  box.classList.add("ly-kar");
  box.addEventListener("wheel", () => { userAt = Date.now(); }, {passive: true});
  box.addEventListener("touchmove", () => { userAt = Date.now(); }, {passive: true});
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  function set(ls, plain){
    lines = ls || [];
    cur = -2;
    if(!lines.length){
      box.innerHTML = plain ? `<div class="ly-plain">${esc(plain)}</div>` : "";
      els = [];
      return;
    }
    box.innerHTML = lines.map((l, i) => `<p class="ln" data-i="${i}"><span class="fill">${esc(l.text || "♪")}</span></p>`).join("");
    els = [...box.querySelectorAll(".ln")];
    els.forEach((p) => { p.onclick = () => { userAt = 0; if(onSeek) onSeek(lines[+p.dataset.i].t); }; });
  }
  function at(t){
    let lo = 0, hi = lines.length;
    while(lo < hi){ const mid = (lo + hi) >>> 1; if(lines[mid].t <= t) lo = mid + 1; else hi = mid; }
    return lo - 1;
  }
  function draw(t){
    if(!els.length) return;
    const i = at(t + 0.15);
    if(i !== cur){
      els.forEach((p, k) => { p.classList.toggle("cur", k === i); p.classList.toggle("past", k < i); });
      cur = i;
      if(i >= 0 && Date.now() - userAt > 4000 && box.clientHeight){
        box.scrollTo({top: els[i].offsetTop - box.clientHeight * 0.38, behavior: "smooth"});
      }
    }
    if(i >= 0){
      const start = lines[i].t, end = lines[i + 1] ? lines[i + 1].t : start + 4;
      els[i].style.setProperty("--p", (Math.max(0, Math.min(1, (t - start) / Math.max(0.05, end - start))) * 100).toFixed(1) + "%");
    }
  }
  // After the box changed size or came into view: the line being sung back where it is read.
  function recenter(){ const i = cur; cur = -2; userAt = 0; if(i >= 0 && els[i]) box.scrollTop = els[i].offsetTop - box.clientHeight * 0.38; cur = i; }
  return {set, draw, recenter, has: () => els.length > 0};
}
