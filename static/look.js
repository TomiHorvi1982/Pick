// Úpravy vzhledu — the design mode. Anything on screen can be pointed at and
// restyled (colour, type, background, border, shadow, spacing, effects…), the
// whole app re-coloured and re-typed, and all of it is seen at once; nothing
// is kept until "Uložit". A look is data — a list of CSS selectors with what
// was changed on each — compiled into one <style> on every change, so it
// never touches the app's own styles and "Výchozí vzhled" is just an empty
// list. The panel lives in a shadow root: the look being edited cannot
// restyle its own editor.
//
// Free sources it draws on (server: look.py): Google Fonts and Fontshare for
// type; Openverse, Wikimedia Commons, Lorem Picsum, the Art Institute of
// Chicago and NASA for pictures; Iconify for icons. A picture of the
// player's own can be the background of anything — or lie over the app,
// half see-through, as the model to work to (Vzor), its colours picked off.
const PickLook = (() => {
  const CACHE = "pick-look", REFKEY = "pick-look-ref", SIDE = "pick-look-side";
  const blank = () => ({v: 1, vars: {}, app: {}, rules: [], fonts: {}});
  let look = blank(), saved = JSON.stringify(look), undo = [], redo = [];
  let host = null, root = null, on = false, picking = false, target = null, rule = null, tab = "el";
  let catalogue = null, kept = [], lastColor = null, swatches = [], srcTarget = "el", refState = null;

  // ------------------------------------------------------------- small tools
  const h = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for(const k in attrs || {}){
      const v = attrs[k];
      if(v == null || v === false) continue;
      if(k === "class") e.className = v; else if(k === "html") e.innerHTML = v; else if(k === "text") e.textContent = v;
      else if(k.startsWith("on")) e[k] = v; else if(k === "style") e.style.cssText = v; else if(k === "value") e.value = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for(const c of kids.flat(Infinity)) if(c != null && c !== false) e.append(c);
    return e;
  };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const api = async (url, opt) => {
    const r = await fetch(url, opt), d = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(d.error || "Nepodařilo se.");
    return d;
  };
  const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

  // colours: everything is kept as "#rrggbb" or "rgba(r,g,b,a)"
  function parseColor(v){
    if(!v) return null;
    const c = document.createElement("canvas").getContext("2d");
    c.fillStyle = "#000"; c.fillStyle = v; const a = c.fillStyle;
    c.fillStyle = "#fff"; c.fillStyle = v; if(c.fillStyle !== a) return null;
    if(a[0] === "#") return {hex: a, a: 1};
    const m = a.match(/rgba?\(([\d.]+), ?([\d.]+), ?([\d.]+)(?:, ?([\d.]+))?\)/);
    if(!m) return null;
    return {hex: "#" + [1, 2, 3].map((i) => (+m[i]).toString(16).padStart(2, "0")).join(""), a: m[4] == null ? 1 : +m[4]};
  }
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const colorOut = (hex, a) => a >= 1 ? hex : `rgba(${rgb(hex).join(",")},${Math.round(a * 100) / 100})`;
  const lum = (hex) => { const [r, g, b] = rgb(hex); return (0.299 * r + 0.587 * g + 0.114 * b) / 255; };
  const sat = (hex) => { const [r, g, b] = rgb(hex), mx = Math.max(r, g, b), mn = Math.min(r, g, b); return mx ? (mx - mn) / mx : 0; };
  const mix = (a, b, t) => "#" + rgb(a).map((x, i) => Math.round(x + (rgb(b)[i] - x) * t).toString(16).padStart(2, "0")).join("");

  // ------------------------------------------------- what the free sources give
  const THEMES = [
    ["Pick noční", {bg: "#0b0c14", bg2: "#12131f", card: "#191a2a", hi: "#23253a", text: "#eef0ff", sub: "#8f96c0", accent: "#8b7bff", accent2: "#3d8bfd", border: "#262840"}],
    ["Dracula", {bg: "#282a36", bg2: "#21222c", card: "#343746", hi: "#44475a", text: "#f8f8f2", sub: "#a7abc4", accent: "#bd93f9", accent2: "#ff79c6", border: "#44475a"}],
    ["Nord", {bg: "#2e3440", bg2: "#272c36", card: "#3b4252", hi: "#434c5e", text: "#eceff4", sub: "#a3aec2", accent: "#88c0d0", accent2: "#81a1c1", border: "#434c5e"}],
    ["Catppuccin Mocha", {bg: "#1e1e2e", bg2: "#181825", card: "#313244", hi: "#45475a", text: "#cdd6f4", sub: "#a6adc8", accent: "#cba6f7", accent2: "#89b4fa", border: "#45475a"}],
    ["Gruvbox", {bg: "#282828", bg2: "#1d2021", card: "#3c3836", hi: "#504945", text: "#ebdbb2", sub: "#a89984", accent: "#fabd2f", accent2: "#fe8019", border: "#504945"}],
    ["Tokyo Night", {bg: "#1a1b26", bg2: "#16161e", card: "#24283b", hi: "#2f3549", text: "#c0caf5", sub: "#7f88b3", accent: "#7aa2f7", accent2: "#bb9af7", border: "#2f3549"}],
    ["One Dark", {bg: "#282c34", bg2: "#21252b", card: "#2c313a", hi: "#3a3f4b", text: "#e6e9ef", sub: "#9aa2b1", accent: "#61afef", accent2: "#c678dd", border: "#3a3f4b"}],
    ["Monokai", {bg: "#272822", bg2: "#1e1f1c", card: "#34352f", hi: "#49483e", text: "#f8f8f2", sub: "#a59f85", accent: "#a6e22e", accent2: "#f92672", border: "#49483e"}],
    ["Solarized Dark", {bg: "#002b36", bg2: "#00222b", card: "#073642", hi: "#0d4654", text: "#eee8d5", sub: "#93a1a1", accent: "#2aa198", accent2: "#268bd2", border: "#0d4654"}],
    ["Rosé Pine", {bg: "#191724", bg2: "#14121d", card: "#1f1d2e", hi: "#26233a", text: "#e0def4", sub: "#908caa", accent: "#ebbcba", accent2: "#c4a7e7", border: "#26233a"}],
    ["Everforest", {bg: "#2d353b", bg2: "#272e33", card: "#343f44", hi: "#3d484d", text: "#d3c6aa", sub: "#9da9a0", accent: "#a7c080", accent2: "#7fbbb3", border: "#3d484d"}],
    ["Kanagawa", {bg: "#1f1f28", bg2: "#16161d", card: "#2a2a37", hi: "#363646", text: "#dcd7ba", sub: "#9c9a87", accent: "#7e9cd8", accent2: "#957fb8", border: "#363646"}],
    ["Synthwave", {bg: "#241b2f", bg2: "#1b1425", card: "#2f2440", hi: "#3c2e52", text: "#f6f0ff", sub: "#b6a4d0", accent: "#ff7edb", accent2: "#36f9f6", border: "#3c2e52"}],
    ["Krev a ocel", {bg: "#0c0a0a", bg2: "#141011", card: "#1d1617", hi: "#2a1e20", text: "#f3ecec", sub: "#a59090", accent: "#e01e2f", accent2: "#8a8f98", border: "#2e2022"}],
    ["Black metal", {bg: "#000000", bg2: "#0a0a0a", card: "#121212", hi: "#1c1c1c", text: "#f2f2f2", sub: "#8c8c8c", accent: "#e6e6e6", accent2: "#6e6e6e", border: "#242424"}],
    ["Stoner", {bg: "#17130d", bg2: "#1e1810", card: "#2a2115", hi: "#382c1b", text: "#f1e4c9", sub: "#b09a74", accent: "#e08a1e", accent2: "#7d9b3a", border: "#3a2d1c"}],
    ["Thrash", {bg: "#0d1109", bg2: "#12180c", card: "#1a2212", hi: "#243019", text: "#eaf5dc", sub: "#94a680", accent: "#9be21a", accent2: "#f2d21b", border: "#27341b"}],
    ["Glam", {bg: "#160a18", bg2: "#1d0e20", card: "#29132d", hi: "#381a3d", text: "#ffeefc", sub: "#c59ac4", accent: "#ff3fb4", accent2: "#ffd23f", border: "#3b1c40"}],
    ["Světlý papír", {bg: "#f3f0e8", bg2: "#ebe7dc", card: "#ffffff", hi: "#e2ddd0", text: "#1c1b18", sub: "#6b675d", accent: "#b3261e", accent2: "#1f5fbf", border: "#d6d0c2"}],
  ];
  const THEME_VARS = {bg: ["--bg"], bg2: ["--bg2"], card: ["--card"], hi: ["--card-hi", "--sp-hover"], text: ["--text"], sub: ["--sub"],
    accent: ["--accent", "--ok"], accent2: ["--accent2", "--accent-dim"], border: ["--border"]};
  const MAIN_VARS = [["--bg", "Pozadí"], ["--bg2", "Panely"], ["--card", "Karty"], ["--card-hi", "Karty – zvýraznění"], ["--text", "Text"],
    ["--sub", "Vedlejší text"], ["--accent", "Hlavní barva (tlačítka)"], ["--accent2", "Druhá barva"], ["--accent-dim", "Hlavní barva – tmavší"],
    ["--border", "Linky a okraje"], ["--danger", "Varování"], ["--ok", "V pořádku"], ["--gold", "Zlatá"], ["--sp-hover", "Najetí myší"]];
  const GRADS = [["Půlnoc", "#232526", "#414345"], ["Krev", "#200122", "#6f0000"], ["Láva", "#f12711", "#f5af19"], ["Oceán", "#0f2027", "#2c5364"],
    ["Fialový sen", "#41295a", "#2f0743"], ["Neon", "#12c2e9", "#f64f59"], ["Les", "#134e5e", "#71b280"], ["Západ", "#0b486b", "#f56217"],
    ["Uhel", "#000000", "#434343"], ["Královská", "#141e30", "#243b55"], ["Jed", "#0f0c29", "#24fe41"], ["Růže", "#3a1c71", "#d76d77"],
    ["Polární záře", "#00c3ff", "#6a3093"], ["Měď", "#b79891", "#3b2a20"], ["Ocel", "#485563", "#29323c"], ["Plamen", "#ff0844", "#ffb199"],
    ["Hlubina", "#000428", "#004e92"], ["Mech", "#283c14", "#0b1a06"], ["Písek", "#3e2c1c", "#c2a070"], ["Bouřka", "#1f1c2c", "#928dab"]];
  const PATTERNS = {
    dots: ["Tečky", (c, s) => `radial-gradient(${c} 1.5px,transparent 1.7px) 0 0/${s}px ${s}px`],
    grid: ["Mřížka", (c, s) => `linear-gradient(${c} 1px,transparent 1px) 0 0/${s}px ${s}px,linear-gradient(90deg,${c} 1px,transparent 1px) 0 0/${s}px ${s}px`],
    diag: ["Šrafy", (c, s) => `repeating-linear-gradient(45deg,${c} 0 1px,transparent 1px ${s / 2}px)`],
    hatch: ["Křížové šrafy", (c, s) => `repeating-linear-gradient(45deg,${c} 0 1px,transparent 1px ${s / 2}px),repeating-linear-gradient(-45deg,${c} 0 1px,transparent 1px ${s / 2}px)`],
    stripes: ["Pruhy", (c, s) => `repeating-linear-gradient(90deg,${c} 0 ${s / 2}px,transparent ${s / 2}px ${s}px)`],
    lines: ["Linky", (c, s) => `repeating-linear-gradient(0deg,${c} 0 1px,transparent 1px ${s / 2}px)`],
    checker: ["Šachovnice", (c, s) => `conic-gradient(${c} 25%,transparent 0 50%,${c} 0 75%,transparent 0) 0 0/${s}px ${s}px`],
    rings: ["Kruhy", (c, s) => `repeating-radial-gradient(circle at 50% 50%,${c} 0 1px,transparent 1px ${s / 2}px)`],
    carbon: ["Karbon", (c, s) => `linear-gradient(27deg,${c} ${s / 4}px,transparent ${s / 4}px) 0 ${s / 4}px/${s}px ${s}px,linear-gradient(207deg,${c} ${s / 4}px,transparent ${s / 4}px) ${s / 2}px 0/${s}px ${s}px`],
    zigzag: ["Cikcak", (c, s) => `linear-gradient(135deg,${c} 25%,transparent 25%) -${s / 2}px 0/${s}px ${s}px,linear-gradient(225deg,${c} 25%,transparent 25%) -${s / 2}px 0/${s}px ${s}px`],
    scales: ["Šupiny", (c, s) => `radial-gradient(circle at 50% 0,transparent ${s * 0.36}px,${c} ${s * 0.38}px ${s * 0.42}px,transparent ${s * 0.44}px) 0 0/${s}px ${s / 2}px`],
    noise: ["Zrno", (c) => `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .5 0'/></filter><rect width='180' height='180' filter='url(#n)' opacity='.5'/></svg>`)}")`],
    vignette: ["Vinětace", () => `radial-gradient(ellipse at center,transparent 45%,rgba(0,0,0,.72))`],
    spot: ["Reflektor", (c) => `radial-gradient(ellipse at 50% 0,${c},transparent 62%)`],
  };
  const SHADOWS = [["Jemný", {x: 0, y: 2, b: 8, s: 0, c: "rgba(0,0,0,0.35)"}], ["Hluboký", {x: 0, y: 14, b: 38, s: -6, c: "rgba(0,0,0,0.7)"}],
    ["Záře", {x: 0, y: 0, b: 18, s: 2, c: "ACCENT"}], ["Neon", {x: 0, y: 0, b: 6, s: 1, c: "ACCENT", glow: 1}],
    ["Vtlačený", {x: 0, y: 2, b: 6, s: 0, c: "rgba(0,0,0,0.6)", i: 1}], ["Ostrý", {x: 4, y: 4, b: 0, s: 0, c: "rgba(0,0,0,0.9)"}]];

  // ------------------------------------------------------- look → stylesheet
  function bgCss(g){
    if(!g || !g.mode) return null;
    const layers = [];
    if(+g.dim > 0) layers.push(`linear-gradient(rgba(0,0,0,${g.dim}),rgba(0,0,0,${g.dim}))`);
    if(g.pat && PATTERNS[g.pat]) layers.push(PATTERNS[g.pat][1](g.pcol || "rgba(255,255,255,0.08)", +g.psize || 24));
    let color = "";
    if(g.mode === "color") color = g.color || "";
    if(g.mode === "grad"){
      const stops = [g.a || "#232526", g.mid, g.b || "#414345"].filter(Boolean).join(",");
      layers.push(g.radial ? `radial-gradient(circle at ${g.pos || "center"},${stops})` : `linear-gradient(${g.angle == null ? 135 : g.angle}deg,${stops})`);
    }
    if(g.mode === "img" && g.img){
      const size = g.size === "px" ? `${+g.sizepx || 200}px auto` : (g.size || "cover");
      layers.push(`url("${g.img}") ${g.pos || "center"}/${size} ${g.repeat ? "repeat" : "no-repeat"}${g.fixed ? " fixed" : ""}`);
      color = g.color || "";
    }
    if(g.mode === "none") return "none";
    return (layers.join(",") + (color ? (layers.length ? " " : "") + color : "")) || null;
  }
  function decls(r){
    const out = [], x = r.x || {};
    for(const k in r.p || {}) if(r.p[k] !== "" && r.p[k] != null) out.push([k, r.p[k]]);
    const bg = bgCss(x.bg); if(bg) out.push(["background", bg]);
    if(x.sh){ const s = x.sh, base = `${s.i ? "inset " : ""}${s.x || 0}px ${s.y || 0}px ${s.b || 0}px ${s.s || 0}px ${s.c || "rgba(0,0,0,0.5)"}`;
      out.push(["box-shadow", s.glow ? `${base},0 0 ${(s.b || 0) * 3}px ${(s.s || 0) * 2}px ${s.c}` : base]); }
    if(x.ts) out.push(["text-shadow", `${x.ts.x || 0}px ${x.ts.y || 0}px ${x.ts.b || 0}px ${x.ts.c || "rgba(0,0,0,0.6)"}`]);
    const fn = (o, units) => Object.keys(units).filter((k) => o[k] != null && o[k] !== "").map((k) => `${units[k][0]}(${o[k]}${units[k][1]})`).join(" ");
    if(x.f){ const v = fn(x.f, FILTERS); if(v) out.push(["filter", v]); }
    if(x.bf){ const v = fn(x.bf, FILTERS); if(v){ out.push(["backdrop-filter", v]); out.push(["-webkit-backdrop-filter", v]); } }
    if(x.t){ const t = x.t, v = [(t.x || t.y) ? `translate(${t.x || 0}px,${t.y || 0}px)` : "", t.rot ? `rotate(${t.rot}deg)` : "",
        t.sc != null && t.sc !== "" && +t.sc !== 1 ? `scale(${t.sc})` : "", t.sk ? `skewX(${t.sk}deg)` : ""].filter(Boolean).join(" ");
      if(v) out.push(["transform", v]); }
    return out;
  }
  const FILTERS = {blur: ["blur", "px"], bright: ["brightness", "%"], contrast: ["contrast", "%"], sat: ["saturate", "%"],
    hue: ["hue-rotate", "deg"], gray: ["grayscale", "%"], invert: ["invert", "%"], sepia: ["sepia", "%"]};
  const fontStack = (f) => `"${f}",-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif`;
  function compile(l){
    const css = [], vars = {};
    for(const key in l.vars || {}){ const i = key.lastIndexOf("|"); (vars[key.slice(0, i)] = vars[key.slice(0, i)] || []).push(`${key.slice(i + 1)}:${l.vars[key]} !important`); }
    for(const sel in vars) css.push(`${sel}{${vars[sel].join(";")}}`);
    const a = l.app || {};
    if(a.font) css.push(`:root{--sp-font:${fontStack(a.font)} !important}body,button,input,select,textarea{font-family:${fontStack(a.font)} !important}`);
    if(a.zoom && +a.zoom !== 100) css.push(`html{zoom:${a.zoom / 100}}`);
    const abg = bgCss(a.bg);
    if(abg) css.push(`#look-bg{display:block !important;background:${abg};${+(a.bg.blur) ? `filter:blur(${a.bg.blur}px);inset:-${a.bg.blur * 2}px !important;` : ""}}`);
    for(const r of l.rules || []){
      const d = decls(r).map(([k, v]) => `${k}:${v} !important`).join(";");
      if(d) css.push(`${r.sel}{${d}}`);
      const ic = (r.x || {}).ic;
      if(ic && ic.img) css.push(`${r.sel}::before{content:"" !important;display:inline-block !important;width:${ic.size || 18}px;height:${ic.size || 18}px;` +
        `margin-right:${ic.gap == null ? 6 : ic.gap}px;vertical-align:${ic.va || "-3"}px;background:url("${ic.img}") center/contain no-repeat !important;flex:none}`);
      if(r.css && r.css.trim()) css.push(`${r.sel}{${r.css.replace(/[{}]/g, "")}}`);
    }
    return css.join("\n");
  }
  function fontLink(name, f){
    const id = "look-font-" + name.replace(/\W+/g, "-");
    if(document.getElementById(id)) return;
    const w = (f && f.w && f.w.length ? f.w : [400]).filter((x, i, a) => a.indexOf(x) === i).sort((x, y) => x - y);
    const href = f && f.s === "fontshare" && f.slug
      ? `https://api.fontshare.com/v2/css?f[]=${f.slug}@${w.join(",")}&display=swap`
      : `https://fonts.googleapis.com/css2?family=${encodeURIComponent(name).replace(/%20/g, "+")}${w.length > 1 || w[0] !== 400 ? ":wght@" + w.join(";") : ""}&display=swap`;
    document.head.append(h("link", {id, rel: "stylesheet", href}));
  }
  const fontInfo = (name) => (catalogue || []).find((f) => f.f === name) || (look.fonts || {})[name] || null;
  function useFont(name){
    const f = fontInfo(name);
    look.fonts = look.fonts || {};
    if(f) look.fonts[name] = {s: f.s, slug: f.slug, w: f.w};
    fontLink(name, f);
  }
  function paintLook(){
    let st = document.getElementById("look-style");
    if(!st){ st = h("style", {id: "look-style"}); document.head.append(st); }
    if(!document.getElementById("look-bg") && document.body)
      document.body.append(h("div", {id: "look-bg", "aria-hidden": "true", style: "display:none;position:fixed;inset:0;z-index:-1;pointer-events:none"}));
    for(const n in look.fonts || {}) fontLink(n, look.fonts[n]);
    st.textContent = compile(look);
  }
  const tidy = (l) => { const o = {...blank(), ...(l && typeof l === "object" ? l : {})};
    o.vars = o.vars || {}; o.app = o.app || {}; o.fonts = o.fonts || {}; o.rules = Array.isArray(o.rules) ? o.rules.filter((r) => r && r.sel) : []; return o; };
  const prune = () => { look.rules = look.rules.filter((r) => r === rule || decls(r).length || (r.css || "").trim() || ((r.x || {}).ic || {}).img); };
  const dirty = () => JSON.stringify(look) !== saved;

  function changed(keep){
    paintLook();
    if(!keep) redo = [];
    paintHead(); placeBox();
  }
  // every edit goes through here: one step back per gesture, not per pixel
  let stepAt = 0, stepKey = "";
  function edit(key, fn){
    const now = Date.now();
    if(key !== stepKey || now - stepAt > 900){ undo.push(JSON.stringify(look)); if(undo.length > 200) undo.shift(); }
    stepKey = key; stepAt = now;
    fn(); changed();
  }
  function restore(text){
    const selWas = rule && rule.sel;
    look = tidy(JSON.parse(text));
    rule = selWas ? look.rules.find((r) => r.sel === selWas) || (target ? {sel: selWas, p: {}, x: {}, fresh: true} : null) : null;
    stepKey = ""; changed(true); paintTab();
  }

  // ------------------------------------------------------------- selectors
  const STATE = /^(active|on|open|playing|sorted|selected|armed|hidden|collapsed|has-on|current|dragging|drag|loading|busy|focus|hover|show|shown|visible)$/;
  const okName = (s) => /^[A-Za-z_][\w-]*$/.test(s);
  const classesOf = (e) => [...e.classList].filter((c) => okName(c) && !STATE.test(c));
  const goodId = (e) => e.id && okName(e.id) && !/\d{4,}/.test(e.id);
  function uniqueSel(e){
    if(goodId(e)) return "#" + e.id;
    const parts = [];
    for(let n = e; n && n !== document.body && n !== document.documentElement && parts.length < 7; n = n.parentElement){
      if(goodId(n)){ parts.unshift("#" + n.id); break; }
      let part = n.tagName.toLowerCase() + classesOf(n).slice(0, 2).map((c) => "." + c).join("");
      const sibs = [...n.parentElement.children].filter((x) => x.tagName === n.tagName);
      if(sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(n) + 1})`;
      parts.unshift(part);
    }
    return parts.join(" > ");
  }
  function scopes(e){
    const tag = e.tagName.toLowerCase(), cls = classesOf(e), out = [{sel: uniqueSel(e), name: "Jen tento prvek"}];
    const count = (s) => { try{ return document.querySelectorAll(s).length; }catch(x){ return 0; } };
    const add = (sel, name) => { if(!out.some((o) => o.sel === sel)){ const n = count(sel); if(n) out.push({sel, name: `${name} (${n}×)`}); } };
    if(cls.length > 1) add("." + cls.join("."), `Všechny „${cls.join(" ")}“`);
    if(cls.length) add("." + cls[0], `Všechny „${cls[0]}“`);
    let anc = e.parentElement;
    while(anc && anc !== document.body && !goodId(anc) && !classesOf(anc).length) anc = anc.parentElement;
    if(anc && anc !== document.body){
      const a = goodId(anc) ? "#" + anc.id : "." + classesOf(anc)[0];
      add(`${a} ${tag}${cls.length ? "." + cls[0] : ""}`, `Stejné uvnitř „${a.slice(1)}“`);
    }
    add(tag, `Všechny <${tag}> v aplikaci`);
    return out;
  }
  const STATES = [["", "Běžný stav"], [":hover", "Při najetí myší"], [":active", "Při stisku"], [":focus", "Když se do něj píše"],
    [".active", "Když je zapnutý (active)"], [".on", "Když je zapnutý (on)"], [".playing", "Když hraje"]];
  function ruleFor(sel, make){
    let r = look.rules.find((x) => x.sel === sel);
    if(!r && make){ r = {sel, p: {}, x: {}}; look.rules.push(r); }
    return r || null;
  }

  // ------------------------------------------------------------ the picker
  let box = null, tag = null;
  const inPanel = (ev) => ev.composedPath().includes(host);
  function placeBox(){
    if(!box) return;
    if(!on || !target || !target.isConnected){ box.style.display = "none"; return; }
    const r = target.getBoundingClientRect();
    box.style.cssText = `display:block;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px`;
  }
  function hover(ev){
    if(!picking) return;
    const hl = root.getElementById("hl");
    if(inPanel(ev)){ hl.style.display = "none"; return; }
    const e = document.elementFromPoint(ev.clientX, ev.clientY);
    if(!e || e === document.documentElement || e.id === "look-bg"){ hl.style.display = "none"; return; }
    const r = e.getBoundingClientRect();
    hl.style.cssText = `display:block;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px`;
    hl.dataset.name = name(e);
  }
  const name = (e) => e.tagName.toLowerCase() + (goodId(e) ? "#" + e.id : classesOf(e).slice(0, 2).map((c) => "." + c).join(""));
  function block(ev){
    if(!picking || inPanel(ev)) return;
    ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation();
    if(ev.type !== "click") return;
    const e = document.elementFromPoint(ev.clientX, ev.clientY);
    if(e && e.id !== "look-bg") select(e);
  }
  function select(e, keepPicking){
    target = e; rule = null; stateSuf = "";
    const first = scopes(e)[0].sel;
    rule = ruleFor(first, false) || {sel: first, p: {}, x: {}, fresh: true};
    if(!keepPicking) setPicking(false);
    tab = "el"; paintTabs(); paintTab(); placeBox();
  }
  function setPicking(v){
    picking = v;
    if(root){ root.getElementById("hl").style.display = "none"; root.getElementById("pick").classList.toggle("on", v); }
    document.documentElement.style.cursor = v ? "crosshair" : "";
  }
  // the rule being edited joins the look the moment something is set on it
  function live(){
    if(rule && rule.fresh){ delete rule.fresh; const have = look.rules.find((x) => x.sel === rule.sel); if(have) rule = have; else look.rules.push(rule); }
    return rule;
  }

  // -------------------------------------------------------------- controls
  // Each control reads/writes one path of an object ("p.color", "x.sh.b") and
  // shows, greyed, what the element has now when nothing is set.
  const dig = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
  function put(o, path, v){
    const ks = path.split("."); let a = o;
    for(const k of ks.slice(0, -1)) a = a[k] = a[k] || {};
    const last = ks[ks.length - 1];
    if(v === undefined || v === "" || v === null) delete a[last]; else a[last] = v;
    // an emptied group (x.sh, x.bg…) goes too; the top ones (p, x) stay
    for(let i = ks.length - 1; i > 1; i--){
      const up = ks.slice(0, i - 1).reduce((x, k) => x[k], o), par = up[ks[i - 1]];
      if(par && typeof par === "object" && !Object.keys(par).length) delete up[ks[i - 1]]; else break;
    }
  }
  // `ctx` = {get(path), set(path, v), now(prop)}; the element tab and the app tab each bring their own
  const elCtx = {
    get: (p) => rule ? dig(rule, p) : undefined,
    set: (p, v, init) => edit("el:" + p, () => { const r = live(); if(init) init(r); put(r, p, v); prune(); }),
    now: (prop) => { try{ return target ? getComputedStyle(target).getPropertyValue(prop) : ""; }catch(e){ return ""; } },
  };
  const appCtx = {
    get: (p) => dig(look.app, p),
    set: (p, v, init) => edit("app:" + p, () => { if(init) init(look.app); put(look.app, p, v); }),
    now: () => "",
  };
  const row = (label, ...kids) => h("div", {class: "row"}, h("span", {class: "cap", text: label}), h("div", {class: "ctl"}, kids));
  const clearBtn = (fn) => h("button", {class: "x", title: "Vrátit původní", text: "×", onclick: fn});

  function colorCtl(ctx, path, label, fallbackProp, init){
    const cur = () => parseColor(ctx.get(path)) || parseColor(fallbackProp ? ctx.now(fallbackProp) : "") || {hex: "#888888", a: 1};
    const c = cur(), setp = ctx.get(path) != null;
    const sw = h("input", {type: "color", value: c.hex}), tx = h("input", {type: "text", class: "hex" + (setp ? "" : " ghost"), value: colorOut(c.hex, c.a), spellcheck: "false"});
    const al = h("input", {type: "range", min: 0, max: 100, value: Math.round(c.a * 100), class: "alpha", title: "Průhlednost"});
    const apply = (hex, a) => { tx.value = colorOut(hex, a); tx.classList.remove("ghost"); sw.value = hex; al.value = Math.round(a * 100); ctx.set(path, colorOut(hex, a), init); note(colorOut(hex, a)); };
    const me = {apply: (v) => { const p = parseColor(v); if(p) apply(p.hex, p.a); }};
    sw.oninput = () => apply(sw.value, al.value / 100);
    al.oninput = () => apply(sw.value, al.value / 100);
    tx.onchange = () => { const p = parseColor(tx.value.trim()); if(p) apply(p.hex, p.a); else tx.value = colorOut(sw.value, al.value / 100); };
    for(const e of [sw, tx, al]) e.addEventListener("focus", () => { lastColor = me; });
    sw.addEventListener("click", () => { lastColor = me; });
    const drop = window.EyeDropper ? h("button", {class: "mini", title: "Nabrat barvu odkudkoli z obrazovky", text: "⌖",
      onclick: async () => { lastColor = me; try{ const r = await new EyeDropper().open(); apply(r.sRGBHex, al.value / 100); }catch(e){} }}) : null;
    return row(label, sw, tx, al, drop, clearBtn(() => { ctx.set(path, undefined); paintTab(); }));
  }
  function numCtl(ctx, path, label, o){
    const unit = o.u == null ? "px" : o.u, raw = ctx.get(path);
    const fromNow = () => { const v = parseFloat(o.prop ? ctx.now(o.prop) : ""); return Number.isFinite(v) ? v : (o.def == null ? o.min : o.def); };
    const val = raw != null && raw !== "" ? parseFloat(raw) : fromNow();
    const rg = h("input", {type: "range", min: o.min, max: o.max, step: o.step || 1, value: val});
    const nb = h("input", {type: "number", class: "num" + (raw == null ? " ghost" : ""), min: o.min, max: o.max, step: o.step || 1, value: Math.round(val * 100) / 100});
    const apply = (v) => { v = clamp(+v, o.hard == null ? o.min : o.hard, o.hardMax == null ? o.max : o.hardMax); rg.value = v; nb.value = v; nb.classList.remove("ghost");
      ctx.set(path, o.raw ? v : v + unit, o.init); };
    rg.oninput = () => apply(rg.value); nb.onchange = () => apply(nb.value);
    return row(label, rg, nb, h("span", {class: "unit", text: unit}), clearBtn(() => { ctx.set(path, undefined); paintTab(); }));
  }
  function selCtl(ctx, path, label, opts, prop){
    const raw = ctx.get(path), now = prop ? (ctx.now(prop) || "").trim() : "";
    const s = h("select", {class: raw == null ? "ghost" : ""}, h("option", {value: "", text: raw == null && now ? `původní (${now})` : "původní"}),
      opts.map(([v, l]) => h("option", {value: v, text: l, selected: String(raw) === String(v)})));
    s.onchange = () => { ctx.set(path, s.value || undefined); paintTab(); };
    return row(label, s);
  }
  function fontCtl(ctx, path, label, prop){
    const raw = ctx.get(path), now = prop ? (ctx.now(prop) || "").split(",")[0].replace(/["']/g, "").trim() : "";
    const inp = h("input", {type: "text", class: "font", list: "fonts", placeholder: now || "název písma", value: raw ? String(raw).split(",")[0].replace(/["']/g, "") : "", spellcheck: "false"});
    inp.onchange = () => { const n = inp.value.trim();
      if(!n){ ctx.set(path, undefined); return; }
      edit("font", () => useFont(n)); ctx.set(path, path === "font" ? n : fontStack(n)); };
    return row(label, inp, h("button", {class: "mini", title: "Prohlédnout si písma", text: "…", onclick: () => { srcTarget = ctx === appCtx ? "app" : "el"; tab = "src"; srcTab = "fonts"; paintTabs(); paintTab(); }}),
      clearBtn(() => { ctx.set(path, undefined); paintTab(); }));
  }
  const sect = (title, open, ...kids) => { const d = h("details", {class: "sect"}, h("summary", {text: title}), h("div", {class: "body"}, kids));
    const k = "lk-open:" + title; let was = null; try{ was = sessionStorage.getItem(k); }catch(e){}
    d.open = was == null ? !!open : was === "1";
    d.ontoggle = () => { try{ sessionStorage.setItem(k, d.open ? "1" : "0"); }catch(e){} };
    return d; };

  function bgCtl(ctx, base, isApp){
    const g = ctx.get(base) || {}, P = (k) => base + "." + k;
    const mode = h("select", {}, [["", "původní"], ["color", "Barva"], ["grad", "Přechod"], ["img", "Obrázek"], ["none", "Žádné (průhledné)"]]
      .map(([v, l]) => h("option", {value: v, text: l, selected: (g.mode || "") === v})));
    mode.onchange = () => { if(!mode.value) ctx.set(base, undefined); else ctx.set(P("mode"), mode.value); paintTab(); };
    const out = [row("Druh", mode)];
    const need = (m) => (o) => { if((dig(o, base) || {}).mode !== m) put(o, base + ".mode", m); };
    if(g.mode === "color" || !g.mode) out.push(colorCtl(ctx, P("color"), "Barva", "background-color", need("color")));
    if(g.mode === "grad"){
      out.push(colorCtl(ctx, P("a"), "Od", null), colorCtl(ctx, P("mid"), "Přes (volitelné)", null), colorCtl(ctx, P("b"), "Do", null),
        numCtl(ctx, P("angle"), "Úhel", {min: 0, max: 360, u: "°", raw: true, def: 135}),
        selCtl(ctx, P("radial"), "Tvar", [["1", "Kruhový"]]),
        h("div", {class: "chips"}, GRADS.map(([n, a, b]) => h("button", {class: "grad", title: n, style: `background:linear-gradient(135deg,${a},${b})`,
          onclick: () => { ctx.set(base, {...(ctx.get(base) || {}), mode: "grad", a, b, mid: undefined}); paintTab(); }}))));
    }
    if(g.mode === "img"){
      out.push(h("div", {class: "imgpick"}, g.img ? h("img", {src: g.img, alt: ""}) : h("span", {class: "muted", text: "Zatím žádný obrázek"}),
          h("div", {class: "col"},
            h("button", {class: "btn", text: "Vybrat ze zdrojů…", onclick: () => { srcTarget = isApp ? "app" : "el"; tab = "src"; srcTab = "img"; paintTabs(); paintTab(); }}),
            h("button", {class: "btn", text: "Nahrát vlastní…", onclick: () => upload("own", (im) => { ctx.set(P("img"), im.url, need("img")); paintTab(); })}))),
        selCtl(ctx, P("size"), "Velikost", [["cover", "Vyplnit (ořízne)"], ["contain", "Celý obrázek"], ["100% 100%", "Roztáhnout"], ["auto", "Skutečná"], ["px", "Vlastní šířka"]]),
        g.size === "px" ? numCtl(ctx, P("sizepx"), "Šířka", {min: 16, max: 1600, raw: true, def: 200}) : null,
        selCtl(ctx, P("pos"), "Umístění", [["center", "Střed"], ["top", "Nahoře"], ["bottom", "Dole"], ["left", "Vlevo"], ["right", "Vpravo"], ["left top", "Vlevo nahoře"],
          ["right top", "Vpravo nahoře"], ["left bottom", "Vlevo dole"], ["right bottom", "Vpravo dole"]]),
        selCtl(ctx, P("repeat"), "Opakovat", [["1", "Ano (dlaždice)"]]),
        isApp ? null : selCtl(ctx, P("fixed"), "Při posunu", [["1", "Stojí na místě"]]),
        colorCtl(ctx, P("color"), "Barva pod ním", null));
    }
    if(g.mode && g.mode !== "none"){
      out.push(numCtl(ctx, P("dim"), "Ztmavení", {min: 0, max: 0.95, step: 0.05, u: "", raw: true, def: 0}),
        selCtl(ctx, P("pat"), "Vzorek přes to", Object.keys(PATTERNS).map((k) => [k, PATTERNS[k][0]])));
      if(g.pat) out.push(colorCtl(ctx, P("pcol"), "Barva vzorku", null), numCtl(ctx, P("psize"), "Hustota vzorku", {min: 4, max: 120, raw: true, def: 24}));
      if(isApp) out.push(numCtl(ctx, P("blur"), "Rozmazání", {min: 0, max: 60, raw: true, def: 0}));
    }
    return out;
  }

  // ----------------------------------------------------------- tab: Prvek
  const OPT = {
    weight: [["300", "Tenké"], ["400", "Běžné"], ["500", "Střední"], ["600", "Polotučné"], ["700", "Tučné"], ["800", "Extra tučné"], ["900", "Černé"]],
    style: [["normal", "Rovné"], ["italic", "Kurzíva"]],
    upper: [["none", "Jak je napsáno"], ["uppercase", "VELKÁ"], ["lowercase", "malá"], ["capitalize", "Počáteční Velká"]],
    align: [["left", "Vlevo"], ["center", "Na střed"], ["right", "Vpravo"], ["justify", "Do bloku"]],
    deco: [["none", "Žádné"], ["underline", "Podtržení"], ["line-through", "Přeškrtnutí"], ["overline", "Nadtržení"]],
    border: [["solid", "Plná"], ["dashed", "Čárkovaná"], ["dotted", "Tečkovaná"], ["double", "Dvojitá"], ["none", "Žádná"]],
    blend: [["normal", "Běžné"], ["multiply", "Násobit"], ["screen", "Závoj"], ["overlay", "Překrýt"], ["soft-light", "Měkké světlo"], ["color-dodge", "Zesvětlit"],
      ["difference", "Rozdíl"], ["luminosity", "Jas"]],
    show: [["none", "Skrýt úplně"], ["block", "Blok"], ["flex", "Řada (flex)"], ["inline-flex", "Řada v textu"], ["grid", "Mřížka"]],
    vis: [["hidden", "Neviditelný (místo zůstane)"], ["visible", "Viditelný"]],
    over: [["hidden", "Oříznout"], ["visible", "Nechat přetékat"], ["auto", "Posuvník"]],
    fit: [["cover", "Vyplnit"], ["contain", "Celý"], ["fill", "Roztáhnout"]],
    ease: [["ease", "Plynule"], ["linear", "Rovnoměrně"], ["ease-in-out", "Rozjezd a dojezd"], ["cubic-bezier(.2,1.4,.4,1)", "S odrazem"]],
  };
  let stateSuf = "";
  function elTab(){
    if(!target || !rule) return [h("div", {class: "empty"},
      h("p", {html: "Klikni na <b>Vybrat prvek</b> nahoře a pak na cokoli v aplikaci — tlačítko, nadpis, panel, řádek seznamu."}),
      h("p", {class: "muted", text: "Dokud vybíráš, aplikace na kliknutí nereaguje. Když potřebuješ přejít jinam (otevřít píseň, Steems, nastavení), výběr vypni, dojdi tam a zapni ho znovu."}),
      h("button", {class: "btn big", text: "Vybrat prvek", onclick: () => setPicking(true)}))];
    const sc = target.isConnected ? scopes(target) : [{sel: rule.sel, name: rule.sel}];
    const baseSel = STATES.reduce((s, [suf]) => suf && s.endsWith(suf) ? s.slice(0, -suf.length) : s, rule.sel);
    if(!sc.some((o) => o.sel === baseSel)) sc.push({sel: baseSel, name: baseSel});
    const scope = h("select", {}, sc.map((o) => h("option", {value: o.sel, text: o.name, selected: o.sel === baseSel})));
    const st = h("select", {}, STATES.map(([v, l]) => h("option", {value: v, text: l, selected: v === stateSuf})));
    const go = () => { stateSuf = st.value; const sel = scope.value + stateSuf; rule = ruleFor(sel, false) || {sel, p: {}, x: {}, fresh: true}; paintTab(); };
    scope.onchange = go; st.onchange = go;
    const crumbs = []; for(let n = target; n && n !== document.body && crumbs.length < 5; n = n.parentElement) crumbs.unshift(n);
    const c = elCtx, kids = [...(target.children || [])].slice(0, 12);
    const four = (prop, label, max) => [numCtl(c, "p." + prop, label, {min: 0, max, prop: prop + "-top"}),
      h("details", {class: "sub"}, h("summary", {text: "po stranách zvlášť"}),
        [["top", "Nahoře"], ["right", "Vpravo"], ["bottom", "Dole"], ["left", "Vlevo"]].map(([s, l]) => numCtl(c, `p.${prop}-${s}`, l, {min: prop === "margin" ? -80 : 0, max, prop: `${prop}-${s}`})))];
    const accent = () => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#8b7bff";
    return [
      h("div", {class: "who"},
        h("div", {class: "crumbs"}, crumbs.map((n) => h("button", {class: n === target ? "on" : "", text: name(n), title: "Vybrat tento prvek", onclick: () => select(n)})),
          kids.length ? h("select", {class: "kids", title: "Prvky uvnitř", onchange: (e) => { const k = kids[+e.target.value]; if(k) select(k); }},
            h("option", {value: "", text: "↓ uvnitř"}), kids.map((k, i) => h("option", {value: i, text: name(k)}))) : null),
        row("Platí pro", scope), row("Kdy", st),
        h("div", {class: "selrow"}, h("code", {text: rule.sel}),
          h("button", {class: "mini", title: "Zrušit všechny úpravy tohoto prvku", text: "Zrušit úpravy", onclick: () => { edit("drop", () => { look.rules = look.rules.filter((r) => r !== rule); rule = {sel: rule.sel, p: {}, x: {}, fresh: true}; }); paintTab(); }}))),
      sect("Text", true,
        colorCtl(c, "p.color", "Barva", "color"),
        fontCtl(c, "p.font-family", "Písmo", "font-family"),
        numCtl(c, "p.font-size", "Velikost", {min: 8, max: 96, prop: "font-size", hardMax: 400}),
        selCtl(c, "p.font-weight", "Tloušťka", OPT.weight, "font-weight"),
        selCtl(c, "p.font-style", "Řez", OPT.style),
        numCtl(c, "p.letter-spacing", "Rozpal písmen", {min: -3, max: 20, step: 0.1, prop: "letter-spacing", def: 0}),
        numCtl(c, "p.line-height", "Výška řádku", {min: 0.8, max: 3, step: 0.05, u: "", raw: true, def: 1.3}),
        selCtl(c, "p.text-transform", "Velikost písmen", OPT.upper),
        selCtl(c, "p.text-align", "Zarovnání", OPT.align),
        selCtl(c, "p.text-decoration", "Podtržení", OPT.deco),
        h("details", {class: "sub"}, h("summary", {text: "stín textu"}),
          colorCtl(c, "x.ts.c", "Barva", null), numCtl(c, "x.ts.b", "Rozmazání", {min: 0, max: 40, raw: true, def: 0}),
          numCtl(c, "x.ts.x", "Posun →", {min: -20, max: 20, raw: true, def: 0}), numCtl(c, "x.ts.y", "Posun ↓", {min: -20, max: 20, raw: true, def: 0}))),
      sect("Pozadí", true, bgCtl(c, "x.bg", false),
        h("details", {class: "sub"}, h("summary", {text: "sklo (rozmazat to, co je za prvkem)"}),
          numCtl(c, "x.bf.blur", "Rozmazání", {min: 0, max: 60, raw: true, def: 0}), numCtl(c, "x.bf.sat", "Sytost", {min: 0, max: 300, u: "%", raw: true, def: 100}),
          numCtl(c, "x.bf.bright", "Jas", {min: 20, max: 200, u: "%", raw: true, def: 100}))),
      sect("Okraj a rohy", false,
        numCtl(c, "p.border-radius", "Zaoblení rohů", {min: 0, max: 80, prop: "border-top-left-radius", hardMax: 999}),
        numCtl(c, "p.border-width", "Tloušťka okraje", {min: 0, max: 16, prop: "border-top-width", init: (r) => { if(!r.p["border-style"]) r.p["border-style"] = "solid"; }}),
        selCtl(c, "p.border-style", "Čára", OPT.border), colorCtl(c, "p.border-color", "Barva okraje", "border-top-color"),
        h("details", {class: "sub"}, h("summary", {text: "obrys (vně okraje)"}),
          numCtl(c, "p.outline-width", "Tloušťka", {min: 0, max: 12, def: 0, init: (r) => { if(!r.p["outline-style"]) r.p["outline-style"] = "solid"; }}),
          colorCtl(c, "p.outline-color", "Barva", null), numCtl(c, "p.outline-offset", "Odstup", {min: -10, max: 20, def: 0}))),
      sect("Stín a záře", false,
        h("div", {class: "chips"}, SHADOWS.map(([n, s]) => h("button", {class: "chip", text: n, onclick: () => { c.set("x.sh", {...s, c: s.c === "ACCENT" ? accent() : s.c}); paintTab(); }})),
          h("button", {class: "chip", text: "Bez stínu", onclick: () => { c.set("x.sh", undefined); c.set("p.box-shadow", "none"); paintTab(); }})),
        colorCtl(c, "x.sh.c", "Barva", null), numCtl(c, "x.sh.b", "Rozmazání", {min: 0, max: 80, raw: true, def: 0}), numCtl(c, "x.sh.s", "Rozpětí", {min: -20, max: 40, raw: true, def: 0}),
        numCtl(c, "x.sh.x", "Posun →", {min: -40, max: 40, raw: true, def: 0}), numCtl(c, "x.sh.y", "Posun ↓", {min: -40, max: 40, raw: true, def: 0}),
        selCtl(c, "x.sh.i", "Směr", [["1", "Dovnitř (vtlačený)"]]), selCtl(c, "x.sh.glow", "Zdvojit", [["1", "Ano (neon)"]])),
      sect("Rozměry a mezery", false, four("padding", "Vnitřní okraj", 80), four("margin", "Vnější okraj", 80),
        numCtl(c, "p.gap", "Mezera mezi prvky uvnitř", {min: 0, max: 60, prop: "column-gap", def: 0}),
        numCtl(c, "p.width", "Šířka", {min: 10, max: 1600, prop: "width", hardMax: 4000}), numCtl(c, "p.height", "Výška", {min: 10, max: 1200, prop: "height", hardMax: 4000}),
        numCtl(c, "p.min-height", "Nejmenší výška", {min: 0, max: 800, def: 0}), numCtl(c, "p.max-width", "Největší šířka", {min: 40, max: 2400, def: 800}),
        selCtl(c, "p.overflow", "Co přesahuje", OPT.over)),
      sect("Efekty", false,
        numCtl(c, "p.opacity", "Průhlednost", {min: 0, max: 1, step: 0.02, u: "", raw: true, prop: "opacity", def: 1}),
        numCtl(c, "x.f.blur", "Rozmazání", {min: 0, max: 30, step: 0.5, raw: true, def: 0}), numCtl(c, "x.f.bright", "Jas", {min: 0, max: 250, u: "%", raw: true, def: 100}),
        numCtl(c, "x.f.contrast", "Kontrast", {min: 0, max: 250, u: "%", raw: true, def: 100}), numCtl(c, "x.f.sat", "Sytost", {min: 0, max: 300, u: "%", raw: true, def: 100}),
        numCtl(c, "x.f.hue", "Posun barev", {min: 0, max: 360, u: "°", raw: true, def: 0}), numCtl(c, "x.f.gray", "Černobíle", {min: 0, max: 100, u: "%", raw: true, def: 0}),
        numCtl(c, "x.f.sepia", "Sépie", {min: 0, max: 100, u: "%", raw: true, def: 0}), numCtl(c, "x.f.invert", "Negativ", {min: 0, max: 100, u: "%", raw: true, def: 0}),
        selCtl(c, "p.mix-blend-mode", "Prolnutí s pozadím", OPT.blend)),
      sect("Poloha a tvar", false,
        numCtl(c, "x.t.rot", "Otočení", {min: -180, max: 180, u: "°", raw: true, def: 0}), numCtl(c, "x.t.sc", "Zvětšení", {min: 0.2, max: 3, step: 0.02, u: "×", raw: true, def: 1}),
        numCtl(c, "x.t.x", "Posun →", {min: -300, max: 300, raw: true, def: 0}), numCtl(c, "x.t.y", "Posun ↓", {min: -300, max: 300, raw: true, def: 0}),
        numCtl(c, "x.t.sk", "Zkosení", {min: -45, max: 45, u: "°", raw: true, def: 0}),
        numCtl(c, "p.z-index", "Vrstva (co je navrchu)", {min: -5, max: 100, u: "", raw: true, def: 0})),
      sect("Ikona před textem", false,
        h("div", {class: "imgpick"}, (dig(rule, "x.ic.img")) ? h("img", {src: rule.x.ic.img, alt: "", class: "ic"}) : h("span", {class: "muted", text: "Žádná"}),
          h("div", {class: "col"}, h("button", {class: "btn", text: "Vybrat ikonu…", onclick: () => { srcTarget = "el"; tab = "src"; srcTab = "icon"; paintTabs(); paintTab(); }}),
            h("button", {class: "btn", text: "Vlastní obrázek…", onclick: () => upload("own", (im) => { c.set("x.ic.img", im.url); paintTab(); })}),
            dig(rule, "x.ic.img") ? h("button", {class: "btn", text: "Odebrat", onclick: () => { c.set("x.ic", undefined); paintTab(); }}) : null)),
        numCtl(c, "x.ic.size", "Velikost", {min: 8, max: 96, raw: true, def: 18}), numCtl(c, "x.ic.gap", "Mezera za ní", {min: 0, max: 30, raw: true, def: 6}),
        numCtl(c, "x.ic.va", "Výška v řádku", {min: -20, max: 20, raw: true, def: -3})),
      sect("Chování", false,
        numCtl(c, "p.transition-duration", "Plynulost změn", {min: 0, max: 1500, step: 50, u: "ms", def: 0, init: (r) => { if(!r.p["transition-property"]) r.p["transition-property"] = "all"; }}),
        selCtl(c, "p.transition-timing-function", "Průběh", OPT.ease),
        selCtl(c, "p.display", "Zobrazení", OPT.show, "display"), selCtl(c, "p.visibility", "Viditelnost", OPT.vis),
        target && target.tagName === "IMG" ? selCtl(c, "p.object-fit", "Obrázek v rámu", OPT.fit) : null,
        h("p", {class: "muted", text: "Skrytý prvek už nejde vybrat kliknutím — vrátíš ho na kartě Změny."})),
      sect("Vlastní CSS", false,
        h("textarea", {class: "css", placeholder: "cokoli dalšího, např.\nclip-path: polygon(0 0, 100% 0, 92% 100%, 0 100%);\nfont-variant: small-caps;", spellcheck: "false", value: rule.css || "",
          oninput: (e) => edit("css", () => { live().css = e.target.value; prune(); })}),
        h("p", {class: "muted", text: "Jen vlastnosti (bez složených závorek). Platí pro výběr nahoře."})),
    ];
  }

  // -------------------------------------------------------- tab: Aplikace
  function findVars(){
    const out = new Map();
    const walk = (rules) => { for(const r of rules){ try{
      if(r.cssRules && r.cssRules.length && !r.style) walk(r.cssRules);
      if(!r.style || !r.selectorText) continue;
      for(const n of r.style){ if(!n.startsWith("--")) continue;
        const v = r.style.getPropertyValue(n).trim(), key = r.selectorText + "|" + n;
        if(/^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i.test(v) && parseColor(v) && !out.has(key)) out.set(key, {sel: r.selectorText, name: n, val: v});
      } }catch(e){} } };
    for(const s of document.styleSheets){ if(s.ownerNode && s.ownerNode.id === "look-style") continue; try{ walk(s.cssRules); }catch(e){} }
    return [...out.values()];
  }
  const varCtx = (sel) => ({
    get: (p) => look.vars[sel + "|" + p],
    set: (p, v) => edit("var:" + sel + p, () => { if(v == null || v === "") delete look.vars[sel + "|" + p]; else look.vars[sel + "|" + p] = v; }),
    now: (p) => { try{ return getComputedStyle(document.querySelector(sel.split(",")[0]) || document.documentElement).getPropertyValue(p); }catch(e){ return ""; } },
  });
  function applyTheme(t){
    edit("theme", () => { for(const k in THEME_VARS) for(const v of THEME_VARS[k]) look.vars[":root|" + v] = k === "hi" && v === "--sp-hover" ? colorOut(mix(t.hi, t.text, 0.12), 0.6) : t[k]; });
    paintTab();
  }
  function appTab(){
    const all = findVars(), root_ = varCtx(":root"), groups = {};
    for(const v of all) if(v.sel !== ":root") (groups[v.sel] = groups[v.sel] || []).push(v);
    const known = new Set(MAIN_VARS.map((m) => m[0]));
    const rest = all.filter((v) => v.sel === ":root" && !known.has(v.name));
    const used = Object.keys(look.vars).length;
    return [
      sect("Hotové palety", true,
        h("div", {class: "themes"}, THEMES.map(([n, t]) => h("button", {class: "theme", title: n, onclick: () => applyTheme(t)},
          h("span", {class: "dots"}, [t.bg, t.card, t.accent, t.accent2, t.text].map((x) => h("i", {style: "background:" + x}))), h("span", {text: n})))),
        h("div", {class: "chips"},
          h("button", {class: "chip", text: "Paleta z mého vzoru", title: "Barvy z obrázku na kartě Vzor", onclick: () => { if(swatches.length < 4){ tab = "ref"; paintTabs(); paintTab(); say("Nejdřív nahraj obrázek jako vzor."); } else applyTheme(themeFrom(swatches)); }}),
          used ? h("button", {class: "chip", text: "Vrátit původní barvy", onclick: () => { edit("vars0", () => { look.vars = {}; }); paintTab(); }}) : null),
        h("p", {class: "muted", text: "V ambientním vzhledu (Nastavení → Vzhled) si panely nechávají průsvitnost a barvu podle obalu; s Klasickým sedí paleta přesně."})),
      sect("Barvy aplikace", true, MAIN_VARS.map(([n, l]) => colorCtl(root_, n, l, n)),
        rest.length ? h("details", {class: "sub"}, h("summary", {text: `další barvy (${rest.length})`}), rest.map((v) => colorCtl(root_, v.name, v.name.slice(2), v.name))) : null),
      sect("Písmo a velikost", true, fontCtl(appCtx, "font", "Písmo celé aplikace", null),
        numCtl(appCtx, "zoom", "Velikost všeho", {min: 70, max: 140, step: 5, u: "%", raw: true, def: 100}),
        numCtl(root_, "--sp-radius", "Zaoblení panelů", {min: 0, max: 40, prop: "--sp-radius", def: 8}),
        numCtl(root_, "--sp-gap", "Mezery mezi panely", {min: 0, max: 30, prop: "--sp-gap", def: 8})),
      sect("Pozadí aplikace", true, bgCtl(appCtx, "bg", true),
        h("p", {class: "muted", text: "Obrázek je za vším. Aby prosvítal, zprůhledni barvu Panely a Karty výše (posuvník vedle barvy), nebo zapni ambientní vzhled."})),
      Object.keys(groups).length ? sect(`Barvy jednotlivých částí (${Object.keys(groups).length})`, false,
        Object.keys(groups).sort().map((sel) => h("details", {class: "sub"}, h("summary", {text: `${sel}  (${groups[sel].length})`}),
          groups[sel].map((v) => colorCtl(varCtx(sel), v.name, v.name.slice(2), v.name))))) : null,
    ];
  }
  function themeFrom(cols){
    const by = cols.slice().sort((a, b) => lum(a) - lum(b)), dark = by[0], light = by[by.length - 1];
    const vivid = cols.slice().sort((a, b) => sat(b) * (0.4 + lum(b)) - sat(a) * (0.4 + lum(a)));
    const bg = lum(dark) > 0.25 ? mix(dark, "#000000", 0.6) : dark, text = lum(light) < 0.75 ? mix(light, "#ffffff", 0.7) : light;
    return {bg, bg2: mix(bg, text, 0.05), card: mix(bg, text, 0.1), hi: mix(bg, text, 0.17), text, sub: mix(text, bg, 0.38),
      accent: vivid[0], accent2: vivid.find((c) => c !== vivid[0] && Math.abs(lum(c) - lum(vivid[0])) + Math.abs(sat(c) - sat(vivid[0])) > 0.12) || vivid[1] || vivid[0], border: mix(bg, text, 0.2)};
  }

  // ---------------------------------------------------------- tab: Zdroje
  let autoFind = false;
  let srcTab = "fonts", fontQ = "", fontCat = "", imgSrc = "openverse", imgQ = "", imgPage = 1, iconQ = "", iconColor = "#ffffff", found = {}, mine = null, sources = null;
  const TARGETS = [["el", "Vybraný prvek"], ["app", "Celá aplikace"], ["ref", "Vzor (přes aplikaci)"]];
  function useImage(im, asIcon){
    if(srcTarget === "ref"){ setRef({url: im.url, on: true}); tab = "ref"; paintTabs(); paintTab(); return; }
    if(srcTarget === "app"){ appCtx.set("bg", {...(look.app.bg || {}), mode: "img", img: im.url}); tab = "app"; paintTabs(); paintTab(); return; }
    if(!rule){ say("Nejdřív vyber prvek."); return; }
    if(asIcon) elCtx.set("x.ic", {...(dig(rule, "x.ic") || {}), img: im.url});
    else elCtx.set("x.bg", {...(dig(rule, "x.bg") || {}), mode: "img", img: im.url, ...(im.kind === "icon" ? {size: "contain"} : {})});
    tab = "el"; paintTabs(); paintTab();
  }
  async function upload(kind, then){
    const inp = h("input", {type: "file", accept: "image/*"});
    inp.onchange = async () => { const f = inp.files[0]; if(!f) return;
      const fd = new FormData(); fd.append("file", f);
      try{ const im = await api("/api/look/image?kind=" + kind, {method: "POST", body: fd}); mine = null; then(im); }catch(e){ say(e.message); } };
    inp.click();
  }
  async function take(src, it, extra){
    say("Stahuji obrázek…");
    try{ const im = await api("/api/look/take", json("POST", {src, id: it.id, title: it.title, credit: it.credit, license: it.license, page: it.page, ...extra})); mine = null; return im; }
    catch(e){ say(e.message); return null; }
  }
  function useFontOn(f){
    edit("font", () => { look.fonts[f.f] = {s: f.s, slug: f.slug, w: f.w}; fontLink(f.f, f); });
    if(srcTarget === "app") appCtx.set("font", f.f);
    else if(rule) elCtx.set("p.font-family", fontStack(f.f));
    else { say("Nejdřív vyber prvek, nebo dej cíl Celá aplikace."); return; }
    say(`Písmo ${f.f} použito.`);
  }
  function srcTabView(){
    const tg = h("div", {class: "seg"}, TARGETS.map(([k, l]) => h("button", {class: srcTarget === k ? "on" : "", text: l, onclick: () => { srcTarget = k; paintTab(); }})));
    const sub = h("div", {class: "seg small"}, [["fonts", "Písma"], ["img", "Obrázky"], ["icon", "Ikony"], ["grad", "Přechody a vzorky"], ["mine", "Moje"]]
      .map(([k, l]) => h("button", {class: srcTab === k ? "on" : "", text: l, onclick: () => { srcTab = k; paintTab(); }})));
    const body = h("div", {class: "srcbody"});
    const out = [h("div", {class: "cap2", text: "Kam to použít"}), tg, sub, body];
    if(srcTab === "fonts"){
      const list = h("div", {class: "fonts"});
      const cats = [["", "Vše"], ["sans", "Bezpatková"], ["serif", "Patková"], ["display", "Ozdobná"], ["handwriting", "Psaná"], ["mono", "Strojová"]];
      const draw = () => {
        list.textContent = "";
        if(!catalogue){ list.append(h("p", {class: "muted", text: "Načítám seznam písem…"})); return; }
        const q = fontQ.trim().toLowerCase();
        const rows = catalogue.filter((f) => (!q || f.f.toLowerCase().includes(q)) && (!fontCat || (fontCat === "serif" ? f.c === "serif" || f.c === "slab" : f.c.includes(fontCat))))
          .sort((a, b) => (a.p || 9e9) - (b.p || 9e9)).slice(0, 400);
        if(!rows.length) list.append(h("p", {class: "muted", text: "Nic takového."}));
        const io = new IntersectionObserver((es) => { for(const e of es) if(e.isIntersecting){ const f = e.target._f; fontLink(f.f, {...f, w: [f.w.includes(400) ? 400 : f.w[0]]}); e.target.querySelector(".fprev").style.fontFamily = `"${f.f}"`; io.unobserve(e.target); } }, {root: list, rootMargin: "200px"});
        for(const f of rows){
          const r = h("button", {class: "frow", title: `${f.f} — ${f.s === "fontshare" ? "Fontshare" : "Google Fonts"}${f.cz ? "" : " · bez háčků a čárek"}`, onclick: () => useFontOn(f)},
            h("span", {class: "fprev", text: fontSample}), h("span", {class: "fname", text: f.f + (f.cz ? "" : " ·bez ěšč")}));
          r._f = f; list.append(r); io.observe(r);
        }
      };
      body.append(h("div", {class: "bar"}, h("input", {type: "search", placeholder: `Hledat mezi ${catalogue ? catalogue.length : "…"} písmy`, value: fontQ, oninput: (e) => { fontQ = e.target.value; draw(); }}),
          h("select", {onchange: (e) => { fontCat = e.target.value; draw(); }}, cats.map(([v, l]) => h("option", {value: v, text: l, selected: v === fontCat})))),
        h("input", {type: "text", class: "sample", value: fontSample, title: "Ukázkový text", oninput: (e) => { fontSample = e.target.value || "Pick"; list.querySelectorAll(".fprev").forEach((x) => { x.textContent = fontSample; }); }}),
        list, h("p", {class: "muted", text: "Google Fonts a Fontshare — zdarma. Písmo se načítá z internetu při startu aplikace."}));
      draw();
      if(!catalogue) loadFonts().then(() => { if(tab === "src" && srcTab === "fonts") paintTab(); });
    }
    if(srcTab === "img" || srcTab === "icon"){
      const icon = srcTab === "icon", src = icon ? "iconify" : imgSrc, grid = h("div", {class: "grid" + (icon ? " icons" : "")});
      const key = () => `${src}|${icon ? iconQ : imgQ}|${imgPage}`;
      const draw = () => {
        grid.textContent = "";
        const got = found[key()];
        if(got === undefined){ grid.append(h("p", {class: "muted", text: icon ? "Napiš, co hledáš (anglicky: guitar, skull, fire, drum…)." : "Napiš, co hledáš (anglicky najde nejvíc: guitar, stage, smoke, concert…)."})); return; }
        if(got === null){ grid.append(h("p", {class: "muted", text: "Hledám…"})); return; }
        if(got.error){ grid.append(h("p", {class: "muted", text: got.error})); return; }
        if(!got.length) grid.append(h("p", {class: "muted", text: "Nic nenalezeno."}));
        for(const it of got) grid.append(h("button", {class: "tile", title: [it.title, it.credit, it.license].filter(Boolean).join(" · "),
          onclick: async () => { const im = await take(src, it, icon ? {color: iconColor} : {}); if(im) useImage(im, icon && srcTarget === "el" && iconAs === "before"); }},
          h("img", {src: icon ? it.thumb.replace(/color=[^&]*/, "color=" + encodeURIComponent(iconColor)) : it.thumb, alt: "", loading: "lazy"}), icon ? null : h("span", {text: it.credit || it.title || ""})));
      };
      const search = async () => { const k = key(); if(found[k]) { draw(); return; } found[k] = null; draw();
        try{ found[k] = (await api(`/api/look/find?src=${src}&q=${encodeURIComponent(icon ? iconQ : imgQ)}&page=${imgPage}`)).items; }catch(e){ found[k] = {error: e.message}; }
        if(key() === k) draw(); };
      const q = h("input", {type: "search", placeholder: icon ? "Hledat ikonu…" : "Hledat obrázek…", value: icon ? iconQ : imgQ,
        onkeydown: (e) => { if(e.key === "Enter"){ if(icon) iconQ = q.value.trim(); else imgQ = q.value.trim(); imgPage = 1; search(); } }});
      const go = h("button", {class: "btn", text: "Hledat", onclick: () => { if(icon) iconQ = q.value.trim(); else imgQ = q.value.trim(); imgPage = 1; search(); }});
      if(icon) body.append(h("div", {class: "bar"}, q, go),
        h("div", {class: "bar"}, h("span", {class: "muted", text: "Barva ikony"}), h("input", {type: "color", value: iconColor, onchange: (e) => { iconColor = e.target.value; draw(); }}),
          h("select", {onchange: (e) => { iconAs = e.target.value; }}, [["before", "Před text prvku"], ["bg", "Jako pozadí prvku"]].map(([v, l]) => h("option", {value: v, text: l, selected: v === iconAs})))));
      else body.append(h("div", {class: "bar"}, h("select", {onchange: (e) => { imgSrc = e.target.value; imgPage = 1; autoFind = imgSrc === "picsum" || !!imgQ; paintTab(); }},
          (sources || []).filter((s) => !s.icons).map((s) => h("option", {value: s.id, text: s.name, selected: s.id === imgSrc}))), q, go),
        h("p", {class: "muted", text: ((sources || []).find((s) => s.id === imgSrc) || {}).what || ""}));
      body.append(grid, h("div", {class: "bar"},
        h("button", {class: "btn", text: "← Předchozí", disabled: imgPage <= 1, onclick: () => { imgPage = Math.max(1, imgPage - 1); autoFind = true; paintTab(); }}),
        h("span", {class: "muted", text: "strana " + imgPage}),
        h("button", {class: "btn", text: "Další →", onclick: () => { imgPage++; autoFind = true; paintTab(); }})),
        h("p", {class: "muted", text: "Kliknutím se obrázek stáhne do Picku (funguje pak i bez internetu) a použije na zvolený cíl. Autor a licence jsou v popisku."}));
      draw();
      if(autoFind || (src === "picsum" && found[key()] === undefined)){ autoFind = false; search(); }
      if(!sources) api("/api/look/sources").then((s) => { sources = s; if(tab === "src") paintTab(); }).catch(() => {});
    }
    if(srcTab === "grad"){
      const put_ = (g) => { if(srcTarget === "app") appCtx.set("bg", {...(look.app.bg || {}), ...g}); else if(rule) elCtx.set("x.bg", {...(dig(rule, "x.bg") || {}), ...g}); else say("Nejdřív vyber prvek, nebo dej cíl Celá aplikace."); };
      body.append(h("div", {class: "cap2", text: "Přechody"}),
        h("div", {class: "grid grads"}, GRADS.map(([n, a, b]) => h("button", {class: "tile", title: n, style: `background:linear-gradient(135deg,${a},${b})`, onclick: () => put_({mode: "grad", a, b, mid: undefined})}, h("span", {text: n})))),
        h("div", {class: "cap2", text: "Vzorky (přidají se přes pozadí)"}),
        h("div", {class: "grid grads"}, Object.keys(PATTERNS).map((k) => h("button", {class: "tile", title: PATTERNS[k][0], style: `background:${PATTERNS[k][1]("rgba(255,255,255,0.28)", 16)},#15161f`,
          onclick: () => { const cur = srcTarget === "app" ? look.app.bg : dig(rule || {}, "x.bg"); put_({mode: (cur && cur.mode && cur.mode !== "none") ? cur.mode : "color", color: (cur && cur.color) || "#12131c", pat: k}); }}, h("span", {text: PATTERNS[k][0]})))));
    }
    if(srcTab === "mine"){
      const grid = h("div", {class: "grid"});
      body.append(h("div", {class: "bar"}, h("button", {class: "btn", text: "Nahrát obrázek z počítače…", onclick: () => upload("own", () => paintTab())})), grid,
        h("p", {class: "muted", text: "Tvoje obrázky a ty, které sis stáhl ze zdrojů. Klik = použít na zvolený cíl."}));
      const draw = () => { grid.textContent = ""; if(!mine){ grid.append(h("p", {class: "muted", text: "Načítám…"})); return; }
        if(!mine.length) grid.append(h("p", {class: "muted", text: "Zatím tu nic není."}));
        for(const im of mine) grid.append(h("div", {class: "tile" + (im.kind === "icon" ? " icn" : "")},
          h("img", {src: im.url, alt: "", loading: "lazy", onclick: () => useImage(im, im.kind === "icon" && srcTarget === "el")}),
          h("span", {text: im.title || im.credit || ""}),
          h("button", {class: "del", title: "Smazat obrázek z Picku", text: "×", onclick: async () => { if(!confirm("Smazat tenhle obrázek? Kde je použitý, zmizí.")) return; await api("/api/look/image/" + im.name, {method: "DELETE"}); mine = null; paintTab(); }}))); };
      draw();
      if(!mine) api("/api/look/images").then((l) => { mine = l; if(tab === "src" && srcTab === "mine") paintTab(); }).catch(() => {});
    }
    return out;
  }
  let fontSample = "Pick — Hraj líp 123", iconAs = "before";
  async function loadFonts(){
    try{ catalogue = await api("/api/look/fonts"); }catch(e){ catalogue = []; }
    const dl = root.getElementById("fonts"); dl.textContent = "";
    for(const f of catalogue.slice().sort((a, b) => a.f.localeCompare(b.f))) dl.append(h("option", {value: f.f}));
  }

  // ------------------------------------------------------------ tab: Vzor
  // A picture to work to: over the app, see-through, never part of the look.
  function loadRef(){ try{ refState = JSON.parse(localStorage.getItem(REFKEY) || "null"); }catch(e){ refState = null; } }
  function setRef(part){
    refState = part === null ? null : {op: 0.5, scale: 100, x: 0, y: 0, on: true, ...(refState || {}), ...part};
    try{ refState ? localStorage.setItem(REFKEY, JSON.stringify(refState)) : localStorage.removeItem(REFKEY); }catch(e){}
    paintRef();
  }
  function paintRef(){
    let im = document.getElementById("look-ref");
    const show = on && refState && refState.on && refState.url;
    if(!show){ if(im) im.remove(); return; }
    if(!im){ im = h("img", {id: "look-ref", alt: "", "aria-hidden": "true"}); document.body.append(im); }
    if(im.getAttribute("src") !== refState.url) im.src = refState.url;
    im.style.cssText = `position:fixed;left:${refState.x}px;top:${refState.y}px;width:${refState.scale}vw;height:auto;opacity:${refState.op};z-index:8000;pointer-events:none;` +
      (refState.blend ? `mix-blend-mode:${refState.blend};` : "");
  }
  function paletteOf(img, n){
    const c = document.createElement("canvas"); c.width = c.height = 72;
    const g = c.getContext("2d", {willReadFrequently: true}); g.drawImage(img, 0, 0, 72, 72);
    const d = g.getImageData(0, 0, 72, 72).data, bins = new Map();
    for(let i = 0; i < d.length; i += 4){ if(d[i + 3] < 128) continue;
      const k = (d[i] >> 4 << 8) | (d[i + 1] >> 4 << 4) | (d[i + 2] >> 4), b = bins.get(k) || [0, 0, 0, 0];
      b[0] += d[i]; b[1] += d[i + 1]; b[2] += d[i + 2]; b[3]++; bins.set(k, b); }
    const cols = [...bins.values()].sort((a, b) => b[3] - a[3]).map((b) => "#" + [0, 1, 2].map((i) => Math.round(b[i] / b[3]).toString(16).padStart(2, "0")).join(""));
    const out = [], far = (a, b) => { const x = rgb(a), y = rgb(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };
    for(const col of cols){ if(out.every((o) => far(o, col) > 42)) out.push(col); if(out.length >= n) break; }
    // …and the most vivid one, however small its share
    const vivid = cols.slice(0, 120).sort((a, b) => sat(b) * lum(b) - sat(a) * lum(a))[0];
    if(vivid && out.every((o) => far(o, vivid) > 30)) out[out.length - 1] = vivid;
    return out;
  }
  const note = (col) => { const p = parseColor(col); if(!p) return; recent = [p.hex, ...recent.filter((x) => x !== p.hex)].slice(0, 12); paintSwatches(); };
  let recent = [];
  function refTab(){
    const r = refState, out = [];
    out.push(h("div", {class: "bar"},
      h("button", {class: "btn", text: r && r.url ? "Jiný obrázek…" : "Nahrát obrázek jako vzor…", onclick: () => upload("ref", (im) => { setRef({url: im.url, on: true}); paintTab(); })}),
      h("button", {class: "btn", text: "Vybrat ze zdrojů…", onclick: () => { srcTarget = "ref"; tab = "src"; srcTab = "img"; paintTabs(); paintTab(); }})));
    if(!r || !r.url){ out.push(h("p", {class: "muted", text: "Vzor je obrázek, podle kterého ladíš: snímek jiné aplikace, obal desky, plakát, vlastní návrh. Položí se průsvitně přes Pick, takže vidíš obojí naráz, a jeho barvy si odsud nabereš. Součástí uloženého vzhledu není."})); return out; }
    const cv = h("canvas", {class: "refcv", title: "Klikni do obrázku a nabereš barvu"}), img = new Image();
    img.onload = () => { const w = 344, hh = Math.round(w * img.naturalHeight / img.naturalWidth); cv.width = w; cv.height = hh; cv.getContext("2d", {willReadFrequently: true}).drawImage(img, 0, 0, w, hh);
      swatches = paletteOf(img, 8); paintSwatches(); const p = root.getElementById("refpal"); if(p){ p.textContent = ""; p.append(...swatches.map(swBtn)); } };
    img.src = r.url;
    cv.onclick = (e) => { const b = cv.getBoundingClientRect(), d = cv.getContext("2d").getImageData(Math.round((e.clientX - b.left) * cv.width / b.width), Math.round((e.clientY - b.top) * cv.height / b.height), 1, 1).data;
      const hex = "#" + [0, 1, 2].map((i) => d[i].toString(16).padStart(2, "0")).join(""); note(hex); if(lastColor) lastColor.apply(hex); else say(`Barva ${hex} je dole v paletě — klikni na ni u barvy, kterou chceš změnit.`); };
    const ctx = {get: (p) => refState[p], set: (p, v) => setRef({[p]: v === undefined ? ({op: 0.5, scale: 100, x: 0, y: 0})[p] : v}), now: () => ""};
    out.push(cv,
      h("div", {class: "cap2", text: "Barvy vzoru — klikni a barva půjde tam, kde jsi naposledy měnil barvu"}), h("div", {class: "pal", id: "refpal"}),
      h("div", {class: "chips"},
        h("button", {class: "chip", text: "Obarvit aplikaci podle vzoru", onclick: () => { if(swatches.length >= 4) applyTheme(themeFrom(swatches)); }}),
        h("button", {class: "chip", text: "Dát jako pozadí aplikace", onclick: () => { appCtx.set("bg", {...(look.app.bg || {}), mode: "img", img: r.url, dim: (look.app.bg || {}).dim == null ? 0.45 : look.app.bg.dim}); say("Vzor je teď pozadím aplikace."); }}),
        rule ? h("button", {class: "chip", text: "Dát jako pozadí vybraného prvku", onclick: () => { elCtx.set("x.bg", {...(dig(rule, "x.bg") || {}), mode: "img", img: r.url}); }}) : null),
      sect("Vzor přes aplikaci", true,
        row("Ukázat", h("button", {class: "btn" + (r.on ? " on" : ""), text: r.on ? "Zapnuto" : "Vypnuto", onclick: () => { setRef({on: !r.on}); paintTab(); }})),
        numCtl(ctx, "op", "Průsvitnost", {min: 0.05, max: 1, step: 0.05, u: "", raw: true, def: 0.5}),
        numCtl(ctx, "scale", "Šířka", {min: 10, max: 200, u: "%", raw: true, def: 100}),
        numCtl(ctx, "x", "Posun →", {min: -1500, max: 2500, raw: true, def: 0}), numCtl(ctx, "y", "Posun ↓", {min: -1500, max: 1500, raw: true, def: 0}),
        selCtl(ctx, "blend", "Prolnutí", [["difference", "Rozdíl (ukáže, co nesedí)"], ["screen", "Závoj"], ["multiply", "Násobit"], ["overlay", "Překrýt"]]),
        h("button", {class: "btn", text: "Odebrat vzor", onclick: () => { setRef(null); swatches = []; paintSwatches(); paintTab(); }})));
    return out;
  }
  const swBtn = (col) => h("button", {class: "sw", style: "background:" + col, title: col, onclick: () => { if(lastColor){ lastColor.apply(col); } else { try{ navigator.clipboard.writeText(col); }catch(e){} say(`${col} zkopírováno. Klikni nejdřív do barvy, kterou chceš změnit.`); } }});
  function paintSwatches(){
    if(!root) return;
    const p = root.getElementById("pal"); p.textContent = "";
    const cols = [...swatches, ...recent.filter((x) => !swatches.includes(x))].slice(0, 20);
    p.hidden = !cols.length;
    if(cols.length) p.append(h("span", {class: "muted", text: "Paleta"}), ...cols.map(swBtn));
  }

  // ---------------------------------------------------------- tab: Změny
  function logTab(){
    const out = [], vars = Object.keys(look.vars), a = look.app || {};
    const line = (label, sub, go, drop) => h("div", {class: "chg"}, h("button", {class: "lnk", onclick: go, title: "Upravit"}, h("b", {text: label}), sub ? h("span", {text: sub}) : null),
      h("button", {class: "x", title: "Zrušit tuhle úpravu", text: "×", onclick: drop}));
    const toApp = () => { tab = "app"; paintTabs(); paintTab(); };
    if(vars.length) out.push(line(`Barvy aplikace (${vars.length})`, vars.slice(0, 6).map((k) => k.split("|")[1]).join(", ") + (vars.length > 6 ? "…" : ""), toApp, () => { edit("x", () => { look.vars = {}; }); paintTab(); }));
    if(a.font) out.push(line("Písmo aplikace", a.font, toApp, () => { edit("x", () => { delete look.app.font; }); paintTab(); }));
    if(a.zoom && +a.zoom !== 100) out.push(line("Velikost všeho", a.zoom + " %", toApp, () => { edit("x", () => { delete look.app.zoom; }); paintTab(); }));
    if(a.bg) out.push(line("Pozadí aplikace", ({color: "barva", grad: "přechod", img: "obrázek", none: "žádné"})[a.bg.mode] || "", toApp, () => { edit("x", () => { delete look.app.bg; }); paintTab(); }));
    for(const r of look.rules){
      const n = decls(r).length + ((r.css || "").trim() ? 1 : 0) + (((r.x || {}).ic || {}).img ? 1 : 0);
      let count = 0; try{ count = document.querySelectorAll(r.sel.replace(/:(hover|active|focus)$/, "")).length; }catch(e){}
      out.push(line(r.sel, `${n} ${n === 1 ? "úprava" : n < 5 ? "úpravy" : "úprav"}${count ? "" : " · teď není na obrazovce"}`,
        () => { let e = null; try{ e = document.querySelector(r.sel.replace(/:(hover|active|focus)$/, "")); }catch(x){}
          if(e){ target = e; rule = r; stateSuf = STATES.map((s) => s[0]).find((s) => s && r.sel.endsWith(s)) || ""; tab = "el"; paintTabs(); paintTab(); placeBox(); e.scrollIntoView({block: "nearest"}); }
          else say("Tenhle prvek teď na obrazovce není — přejdi tam, kde je."); },
        () => { edit("x", () => { look.rules = look.rules.filter((x) => x !== r); if(rule === r) rule = null; }); paintTab(); }));
    }
    if(!out.length) out.push(h("p", {class: "muted", text: "Zatím jsi nic nezměnil. Aplikace vypadá tak, jak byla."}));
    const nameIn = h("input", {type: "text", placeholder: "Název vzhledu, např. Noční metal"});
    return [
      sect("Co je změněno", true, out),
      sect("Uložené vzhledy", true,
        h("div", {class: "bar"}, nameIn, h("button", {class: "btn", text: "Uložit jako", onclick: async () => { const n = nameIn.value.trim(); if(!n){ say("Pojmenuj ho."); return; }
          try{ kept = (await api("/api/look/kept/" + encodeURIComponent(n), json("PUT", look))).kept; say(`Vzhled „${n}“ uložen.`); paintTab(); }catch(e){ say(e.message); } }})),
        kept.length ? kept.map((n) => h("div", {class: "chg"},
          h("button", {class: "lnk", title: "Načíst k náhledu (uloží se až tlačítkem Uložit)", onclick: async () => { try{ const g = await api("/api/look/kept/" + encodeURIComponent(n)); undo.push(JSON.stringify(look)); restore(JSON.stringify(g.look)); say(`Náhled vzhledu „${n}“. Líbí? Dej Uložit.`); }catch(e){ say(e.message); } }}, h("b", {text: n})),
          h("button", {class: "x", title: "Smazat uložený vzhled", text: "×", onclick: async () => { if(!confirm(`Smazat uložený vzhled „${n}“?`)) return; kept = (await api("/api/look/kept/" + encodeURIComponent(n), {method: "DELETE"})).kept; paintTab(); }})))
          : h("p", {class: "muted", text: "Vzhledů si můžeš uložit víc a přepínat mezi nimi."})),
      sect("Soubor a návrat", true,
        h("div", {class: "chips"},
          h("button", {class: "chip", text: "Stáhnout jako soubor", onclick: () => { const a_ = h("a", {href: URL.createObjectURL(new Blob([JSON.stringify(look, null, 1)], {type: "application/json"})), download: "pick-vzhled.json"}); a_.click(); }}),
          h("button", {class: "chip", text: "Načíst ze souboru", onclick: () => { const i = h("input", {type: "file", accept: ".json,application/json"}); i.onchange = async () => { try{ const t = JSON.parse(await i.files[0].text()); undo.push(JSON.stringify(look)); restore(JSON.stringify(tidy(t))); say("Vzhled ze souboru je v náhledu."); }catch(e){ say("Tohle není soubor se vzhledem."); } }; i.click(); }}),
          h("button", {class: "chip danger", text: "Výchozí vzhled (zrušit vše)", onclick: () => { if(!confirm("Zrušit všechny úpravy vzhledu? Je to jen náhled — natrvalo až po Uložit, a jde to vrátit šipkou zpět.")) return; edit("reset", () => { look = blank(); rule = null; }); paintTab(); }})),
        h("p", {class: "muted", text: "Obrázky použité ve vzhledu jsou uložené v Picku; soubor se vzhledem na ně jen odkazuje."})),
    ];
  }

  // ------------------------------------------------------------- the panel
  const CSS = `
    :host{all:initial}
    *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif}
    #panel{position:fixed;top:10px;bottom:10px;right:10px;width:384px;z-index:9000;display:flex;flex-direction:column;background:#14151d;color:#e9eaf2;
      border:1px solid #33364a;border-radius:14px;box-shadow:0 24px 70px rgba(0,0,0,.65);font-size:12.5px;overflow:hidden}
    #panel.left{right:auto;left:10px}
    #panel.small{bottom:auto}
    #panel.small #tabs,#panel.small #body,#panel.small #pal{display:none}
    header{display:flex;align-items:center;gap:6px;padding:10px 10px 8px;border-bottom:1px solid #262838;flex-wrap:wrap}
    header b{font-size:13.5px;margin-right:auto;letter-spacing:.01em}
    header .line{flex-basis:100%;display:flex;gap:6px}header .line button{flex:1;white-space:nowrap;padding:7px 6px}
    header .st{flex-basis:100%;font-size:11px;color:#8d92ad;min-height:13px}
    header .st.dirty{color:#ffc53d}
    button{font:inherit;color:inherit;cursor:pointer}
    .btn,.chip,.mini,header button{background:#22243a;border:1px solid #363955;border-radius:8px;padding:6px 10px;color:#e9eaf2}
    .btn:hover,.chip:hover,.mini:hover,header button:hover{border-color:#8b7bff}
    .btn:disabled,header button:disabled{opacity:.35;cursor:default}
    .btn.on,header button.on{background:#8b7bff;border-color:#8b7bff;color:#0c0c16;font-weight:600}
    .btn.big{padding:11px 18px;font-size:14px;margin-top:8px}
    header .save{background:#2fb872;border-color:#2fb872;color:#04170d;font-weight:700}
    header .icon{padding:6px 8px;min-width:30px}
    .mini{padding:3px 7px;font-size:11.5px}
    .chip{border-radius:500px;padding:5px 11px;font-size:12px}
    .chip.danger{color:#ff8f8f;border-color:#6b2f37}
    #tabs{display:flex;border-bottom:1px solid #262838}
    #tabs button{flex:1;background:none;border:none;border-bottom:2px solid transparent;padding:9px 2px;color:#8d92ad;font-size:12.5px}
    #tabs button.on{color:#fff;border-bottom-color:#8b7bff}
    #body{flex:1;overflow-y:auto;padding:10px 10px 30px;scrollbar-width:thin;scrollbar-color:#3a3d55 transparent}
    #pal{display:flex;align-items:center;gap:5px;padding:7px 10px;border-top:1px solid #262838;flex-wrap:wrap}
    #pal[hidden]{display:none}
    .sw{width:20px;height:20px;border-radius:5px;border:1px solid rgba(255,255,255,.25);padding:0}
    .pal{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 10px}.pal .sw{width:34px;height:34px;border-radius:8px}
    .sect{border:1px solid #262838;border-radius:10px;margin-bottom:8px;background:#181924}
    .sect>summary{padding:9px 11px;font-weight:600;font-size:12.5px;cursor:pointer;list-style:none;display:flex;align-items:center}
    .sect>summary::after{content:"＋";margin-left:auto;color:#6d7290;font-size:11px}.sect[open]>summary::after{content:"－"}
    .sect>.body{padding:2px 10px 10px}
    .sub{margin:6px 0 4px;border-left:2px solid #2c2f45;padding-left:8px}.sub>summary{cursor:pointer;color:#9aa0c0;font-size:11.5px;padding:3px 0}
    .row{display:flex;align-items:center;gap:8px;min-height:30px}
    .cap{flex:0 0 104px;color:#aab0cc;font-size:12px;line-height:1.2}
    .ctl{flex:1;display:flex;align-items:center;gap:5px;min-width:0}
    .cap2{color:#8d92ad;font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin:10px 0 5px}
    input[type=range]{flex:1;min-width:40px;accent-color:#8b7bff;height:18px}
    input[type=text],input[type=number],input[type=search],select,textarea{background:#0e0f16;border:1px solid #33364a;border-radius:7px;color:#e9eaf2;font-size:12px;padding:5px 7px;min-width:0;outline:none}
    input:focus,select:focus,textarea:focus{border-color:#8b7bff}
    select{flex:1;max-width:100%}
    .num{width:58px;flex:none}.hex{width:92px;flex:none;font-family:ui-monospace,Menlo,monospace;font-size:11px}.font{flex:1}
    .alpha{flex:1;min-width:30px}
    .ghost{color:#6d7290}
    .unit{color:#6d7290;font-size:11px;width:14px;flex:none}
    input[type=color]{width:26px;height:24px;padding:0;border:1px solid #33364a;border-radius:6px;background:none;flex:none}
    .x{background:none;border:none;color:#6d7290;font-size:15px;padding:0 3px;line-height:1}.x:hover{color:#ff8f8f}
    .muted{color:#7d829e;font-size:11.5px;line-height:1.45;margin:6px 0}
    .empty{text-align:center;padding:26px 14px;line-height:1.5}
    .who{background:#181924;border:1px solid #262838;border-radius:10px;padding:8px 10px;margin-bottom:8px}
    .crumbs{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:6px;align-items:center}
    .crumbs button{background:none;border:none;color:#8d92ad;padding:2px 4px;font-size:11px;border-radius:4px;font-family:ui-monospace,Menlo,monospace}
    .crumbs button::after{content:" ›";color:#4a4e68}.crumbs button.on{color:#fff;background:#2a2c44}.crumbs button.on::after{content:""}
    .kids{flex:0 1 90px;font-size:11px;padding:2px 4px}
    .selrow{display:flex;gap:6px;align-items:center;margin-top:5px}.selrow code{flex:1;min-width:0;font:11px ui-monospace,Menlo,monospace;color:#7d829e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .chips{display:flex;flex-wrap:wrap;gap:5px;margin:6px 0}
    .grad{width:30px;height:22px;border-radius:6px;border:1px solid rgba(255,255,255,.2);padding:0}
    .imgpick{display:flex;gap:8px;align-items:center;margin:6px 0}.imgpick img{width:96px;height:60px;object-fit:cover;border-radius:7px;border:1px solid #33364a;background:#0e0f16}
    .imgpick img.ic{width:44px;height:44px;object-fit:contain}.imgpick .col{display:flex;flex-direction:column;gap:4px;flex:1}
    textarea.css{width:100%;min-height:86px;font:11.5px/1.45 ui-monospace,Menlo,monospace;resize:vertical}
    .themes{display:grid;grid-template-columns:1fr 1fr;gap:5px}
    .theme{display:flex;flex-direction:column;gap:5px;align-items:flex-start;background:#0e0f16;border:1px solid #2c2f45;border-radius:8px;padding:7px 8px;font-size:11.5px}
    .theme:hover{border-color:#8b7bff}.dots{display:flex}.dots i{width:18px;height:18px;border-radius:50%;margin-right:-5px;border:1px solid rgba(255,255,255,.25)}
    .seg{display:flex;gap:3px;background:#0e0f16;border-radius:9px;padding:3px;margin-bottom:8px;flex-wrap:wrap}
    .seg button{flex:1;background:none;border:none;border-radius:7px;padding:6px 4px;color:#9aa0c0;font-size:12px;white-space:nowrap}
    .seg button.on{background:#2a2c44;color:#fff}.seg.small button{font-size:11.5px}
    .bar{display:flex;gap:6px;align-items:center;margin:6px 0}.bar input[type=search],.bar input[type=text]{flex:1}.bar select{flex:0 1 130px}
    .sample{width:100%;margin-bottom:6px}
    .fonts{max-height:calc(100vh - 420px);min-height:220px;overflow-y:auto;border:1px solid #262838;border-radius:9px;background:#0e0f16}
    .frow{display:flex;flex-direction:column;align-items:flex-start;gap:1px;width:100%;background:none;border:none;border-bottom:1px solid #1d1f2c;padding:8px 10px;text-align:left}
    .frow:hover{background:#1a1c2a}.fprev{font-size:21px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;color:#fff}.fname{font-size:10.5px;color:#7d829e}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:6px 0}
    .grid.icons{grid-template-columns:repeat(6,1fr)}.grid.grads{grid-template-columns:repeat(4,1fr)}
    .tile{position:relative;aspect-ratio:4/3;background:#0e0f16;border:1px solid #262838;border-radius:7px;padding:0;overflow:hidden;display:block}
    .tile:hover{border-color:#8b7bff}.tile img{width:100%;height:100%;object-fit:cover;display:block;cursor:pointer}
    .icons .tile{aspect-ratio:1}.icons .tile img,.tile.icn img{object-fit:contain;padding:7px}
    .tile span{position:absolute;left:0;right:0;bottom:0;font-size:9.5px;padding:8px 4px 2px;background:linear-gradient(transparent,rgba(0,0,0,.8));color:#ddd;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;pointer-events:none}
    .tile .del{position:absolute;right:2px;top:2px;background:rgba(0,0,0,.7);border:none;border-radius:50%;width:18px;height:18px;line-height:1;color:#fff;padding:0}
    .grid .muted{grid-column:1/-1}
    .refcv{width:100%;border-radius:9px;border:1px solid #33364a;cursor:crosshair;display:block}
    .chg{display:flex;align-items:center;gap:6px;border-bottom:1px solid #20222f;padding:5px 0}
    .lnk{flex:1;min-width:0;background:none;border:none;text-align:left;padding:2px 0;display:flex;flex-direction:column;gap:1px}
    .lnk b{font:11.5px ui-monospace,Menlo,monospace;color:#d8dbf0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}.lnk span{font-size:11px;color:#7d829e}
    .lnk:hover b{color:#fff}
    #hl,#box{position:fixed;display:none;pointer-events:none;z-index:8900;border-radius:3px}
    #hl{background:rgba(139,123,255,.18);outline:2px solid #8b7bff}
    #hl::after{content:attr(data-name);position:absolute;left:0;top:-19px;background:#8b7bff;color:#0c0c16;font:600 10.5px ui-monospace,Menlo,monospace;padding:2px 6px;border-radius:4px;white-space:nowrap}
    #box{outline:1.5px dashed #ffc53d;outline-offset:1px}
  `;
  const TABS = [["el", "Prvek"], ["app", "Aplikace"], ["src", "Zdroje"], ["ref", "Vzor"], ["log", "Změny"]];
  function build(){
    host = h("div", {id: "look-host"}); root = host.attachShadow({mode: "open"});
    root.append(h("style", {text: CSS}), h("div", {id: "hl"}), h("div", {id: "box"}), h("datalist", {id: "fonts"}),
      h("div", {id: "panel"},
        h("header", {},
          h("b", {text: "Úpravy vzhledu"}),
          h("button", {id: "un", class: "icon", text: "↩", title: "Zpět (⌘Z)", onclick: () => step(-1)}),
          h("button", {id: "re", class: "icon", text: "↪", title: "Znovu (⇧⌘Z)", onclick: () => step(1)}),
          h("button", {class: "icon", text: "⇆", title: "Přesunout panel na druhou stranu", onclick: () => { const p = root.getElementById("panel"); p.classList.toggle("left"); try{ localStorage.setItem(SIDE, p.classList.contains("left") ? "left" : ""); }catch(e){} }}),
          h("button", {class: "icon", text: "▁", title: "Sbalit / rozbalit panel", onclick: () => root.getElementById("panel").classList.toggle("small")}),
          h("button", {class: "icon", text: "×", title: "Zavřít úpravy vzhledu", onclick: () => close()}),
          h("div", {class: "line"},
            h("button", {id: "pick", text: "Vybrat prvek", title: "Zapni a klikni na cokoli v aplikaci (Esc = vypnout)", onclick: () => setPicking(!picking)}),
            h("button", {id: "save", class: "save", text: "Uložit", title: "Uložit vzhled natrvalo", onclick: save}),
            h("button", {id: "drop", text: "Zahodit", title: "Vrátit se k naposledy uloženému vzhledu", onclick: () => { if(!dirty()) return; undo.push(JSON.stringify(look)); restore(saved); say("Zpátky u uloženého vzhledu."); }}),
            h("button", {id: "cmp", text: "Původní ↔ nový", title: "Podrž a uvidíš aplikaci bez úprav",
              onpointerdown: () => { const s = document.getElementById("look-style"); if(s) s.disabled = true; const b = document.getElementById("look-bg"); if(b) b.style.visibility = "hidden"; },
              onpointerup: () => showAgain(), onpointerleave: () => showAgain()})),
          h("div", {class: "st", id: "st"})),
        h("div", {id: "tabs"}), h("div", {id: "body"}), h("div", {id: "pal", hidden: true})));
    box = root.getElementById("box");
    try{ if(localStorage.getItem(SIDE) === "left") root.getElementById("panel").classList.add("left"); }catch(e){}
    document.body.append(host);
    // what is typed into the panel is the panel's: the app's shortcuts (space = play…) must not hear it
    for(const t of ["keydown", "keyup", "keypress"]) host.addEventListener(t, (e) => e.stopPropagation());
    for(const t of ["click", "mousedown", "mouseup", "pointerdown", "pointerup", "dblclick", "contextmenu", "touchstart"]) window.addEventListener(t, block, true);
    window.addEventListener("mousemove", hover, true);
    window.addEventListener("scroll", placeBox, true); window.addEventListener("resize", placeBox);
    window.addEventListener("keydown", (e) => {
      if(!on) return;
      if(e.key === "Escape" && picking){ setPicking(false); e.stopPropagation(); return; }
      const typing = e.composedPath()[0] && /^(INPUT|TEXTAREA|SELECT)$/.test(e.composedPath()[0].tagName);
      if((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing){ e.preventDefault(); e.stopPropagation(); step(e.shiftKey ? 1 : -1); }
    }, true);
  }
  const showAgain = () => { const s = document.getElementById("look-style"); if(s) s.disabled = false; const b = document.getElementById("look-bg"); if(b) b.style.visibility = ""; };
  function step(dir){
    const from = dir < 0 ? undo : redo, to = dir < 0 ? redo : undo;
    if(!from.length) return;
    to.push(JSON.stringify(look)); restore(from.pop());
  }
  function paintHead(){
    if(!root) return;
    root.getElementById("un").disabled = !undo.length; root.getElementById("re").disabled = !redo.length;
    const d = dirty(), st = root.getElementById("st");
    root.getElementById("save").disabled = !d; root.getElementById("drop").disabled = !d;
    st.className = "st" + (d ? " dirty" : "");
    st.textContent = d ? "Náhled — změny ještě nejsou uložené." : "Vše uloženo.";
  }
  function paintTabs(){
    const t = root.getElementById("tabs"); t.textContent = "";
    for(const [k, l] of TABS) t.append(h("button", {class: tab === k ? "on" : "", text: l, onclick: () => { tab = k; paintTabs(); paintTab(); }}));
  }
  function paintTab(){
    if(!root || !on) return;
    const b = root.getElementById("body"), top = b.scrollTop;
    const view = tab === "el" ? elTab() : tab === "app" ? appTab() : tab === "src" ? srcTabView() : tab === "ref" ? refTab() : logTab();
    b.textContent = ""; b.append(...view.flat().filter(Boolean)); b.scrollTop = top;
    paintHead(); placeBox(); paintSwatches();
  }

  // -------------------------------------------------------- open, save, load
  async function save(){
    prune();
    try{
      await api("/api/look", json("PUT", look));
      saved = JSON.stringify(look);
      try{ localStorage.setItem(CACHE, saved); }catch(e){}
      say("Vzhled uložen."); paintHead();
    }catch(e){ say("Uložení se nepovedlo: " + e.message); }
  }
  function open(){
    if(!host) build();
    on = true; host.style.display = "";
    loadRef(); paintRef(); paintTabs(); paintTab();
    if(!catalogue) loadFonts();
    api("/api/look").then((d) => { kept = d.kept || []; if(tab === "log") paintTab(); }).catch(() => {});
    const so = document.getElementById("settings-overlay"); if(so) so.hidden = true;
  }
  function close(){
    if(dirty()){
      if(confirm("Máš neuložené změny vzhledu.\n\nOK = uložit a zavřít\nZrušit = zahodit je a zavřít")) save();
      else { look = tidy(JSON.parse(saved)); paintLook(); }
    }
    setPicking(false); on = false; target = null; rule = null; undo = []; redo = [];
    if(host) host.style.display = "none";
    paintRef();
  }
  function start(){
    try{ const c = localStorage.getItem(CACHE); if(c){ look = tidy(JSON.parse(c)); saved = JSON.stringify(look); paintLook(); } }catch(e){}
    api("/api/look").then((d) => {
      kept = d.kept || [];
      if(on && dirty()) return;                         // being edited: the server's copy must not undo it
      look = tidy(d.look); saved = JSON.stringify(look); paintLook();
      try{ localStorage.setItem(CACHE, saved); }catch(e){}
    }).catch(() => {});
    const b = document.getElementById("look-open"); if(b) b.onclick = open;
    window.addEventListener("beforeunload", (e) => { if(on && dirty()){ e.preventDefault(); e.returnValue = ""; } });
  }
  if(document.body) start(); else document.addEventListener("DOMContentLoaded", start);

  return {open, close, compile, isOpen: () => on, _state: () => ({look, rule, target, picking}), _select: select};
})();
