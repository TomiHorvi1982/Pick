// The deck — Steems at its top, and the whole screen above the singers:
//
//   ▶  [track icons on knobs]  [master]   key · tuning · tempo   ⌨
//   [intro][verse][chorus][verse]…        the song's parts, in order
//
// A track is its icon on a round knob: the mouse wheel (or a drag up and down)
// turns it, a double click mutes it (it goes grey) and back. A part is clicked
// to play from it; each can be left out, played more times in a row, or
// looped. Space plays and pauses, ← → jump to the part before / after — the
// keys are the listener's to change, and the same actions are there for the
// MIDI pedal (Settings → MIDI pedál, or "Naučit pedál" here).
//
// On the whole screen also: a finder (bands and songs — a song picked loads
// while the screen stays), and the chord helper: the bars running with their
// beats and chords (as on Moises), and the chords coming up drawn on the
// fretboard and on the piano.
const SongDeck = (() => {
  const CSS = `
  .dk{display:flex;flex-direction:column;gap:10px;padding:12px 14px;border-radius:18px;margin:0 0 12px;
    background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08);color:#f1f1f6}
  .dk.stage{margin:10px 14px 0;padding:10px 14px;gap:8px}
  .dk-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .dk-play{width:52px;height:52px;flex:0 0 auto;border-radius:50%;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;
    background:var(--accent,#8b5cf6);color:#0b0b12;box-shadow:0 0 0 5px color-mix(in srgb,var(--accent,#8b5cf6) 22%,transparent),0 8px 24px rgba(0,0,0,.35);transition:transform .12s}
  .dk-play:hover{transform:scale(1.06)}
  .dk.stage .dk-play{width:44px;height:44px}
  .dk-tracks{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
  .dk-mst{padding-left:12px;border-left:1px solid rgba(255,255,255,.1)}
  .dk-knob{display:flex;flex-direction:column;align-items:center;width:66px;user-select:none;-webkit-user-select:none;transition:filter .2s,opacity .2s}
  .dk-dial{position:relative;width:58px;height:58px;cursor:ns-resize;border-radius:50%;
    background:radial-gradient(circle at 50% 38%,rgba(255,255,255,.12),rgba(255,255,255,.02) 62%),#16161f;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 6px 16px rgba(0,0,0,.45)}
  .dk.stage .dk-knob{width:58px}.dk.stage .dk-dial{width:50px;height:50px}
  .dk-dial svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
  .dk-dial .trk{fill:none;stroke:rgba(255,255,255,.1);stroke-width:4.5;stroke-linecap:round}
  .dk-dial .val{fill:none;stroke:var(--c);stroke-width:4.5;stroke-linecap:round;filter:drop-shadow(0 0 4px color-mix(in srgb,var(--c) 55%,transparent))}
  .dk-dial .tick{stroke:rgba(255,255,255,.35);stroke-width:1.5}
  .dk-ic{position:absolute;inset:13px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:var(--c);
    background:color-mix(in srgb,var(--c) 16%,#101018);border:1px solid color-mix(in srgb,var(--c) 40%,transparent)}
  .dk.stage .dk-ic{inset:11px}
  .dk-nm{margin-top:5px;font-size:11px;font-weight:700;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e7e7ef}
  .dk-v{font-size:10px;color:#9a9ab0;font-variant-numeric:tabular-nums}
  .dk-knob.muted{filter:grayscale(1);opacity:.42}
  .dk-knob.muted .dk-v{color:#ff8a8a;opacity:1}
  .dk-facts{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto;font-size:12px;color:#a6a6bc}
  .dk-facts span{padding:6px 10px;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.07);white-space:nowrap}
  .dk-facts b{color:#fff;font-size:13px}
  .dk-keys,.dk-lpbtn{width:36px;height:36px;border-radius:10px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.05);color:#d8d8e4;cursor:pointer;
    display:flex;align-items:center;justify-content:center}
  .dk-keys:hover,.dk-lpbtn:hover{background:rgba(255,255,255,.12)}
  .dk-parts{display:flex;gap:5px;overflow-x:auto;padding-bottom:2px;scrollbar-width:thin}
  .dk-part{position:relative;min-width:78px;height:46px;border-radius:11px;cursor:pointer;overflow:hidden;flex-shrink:0;flex-basis:0;
    background:color-mix(in srgb,var(--c) 18%,#14141c);border:1px solid color-mix(in srgb,var(--c) 38%,transparent);transition:opacity .15s,transform .1s}
  .dk-part:hover{transform:translateY(-1px)}
  .dk-pfill{position:absolute;inset:0 auto 0 0;width:var(--p,0%);background:color-mix(in srgb,var(--c) 34%,transparent);pointer-events:none}
  .dk-part.now{border-color:var(--c);box-shadow:0 0 0 1px var(--c),0 0 18px color-mix(in srgb,var(--c) 35%,transparent)}
  .dk-part.past .dk-pfill{background:color-mix(in srgb,var(--c) 12%,transparent)}
  .dk-pname{position:relative;display:flex;align-items:center;gap:5px;padding:6px 9px 0;font-size:12px;white-space:nowrap}
  .dk-pname b{overflow:hidden;text-overflow:ellipsis}
  .dk-x{font-style:normal;font-size:10px;font-weight:800;color:#0b0b12;background:var(--c);border-radius:6px;padding:0 5px}
  .dk-lp{display:inline-flex;color:var(--c)}
  .dk-pctl{position:relative;display:flex;gap:3px;padding:3px 6px 0;opacity:0;transition:opacity .12s}
  .dk-part:hover .dk-pctl,.dk-part.loop .dk-pctl{opacity:1}
  .dk-pctl button{font:inherit;font-size:10px;line-height:1;padding:3px 6px;border-radius:6px;border:1px solid rgba(255,255,255,.14);
    background:rgba(0,0,0,.35);color:#e7e7ef;cursor:pointer;display:inline-flex;align-items:center}
  .dk-pctl button:hover,.dk-pctl button.on{background:var(--c);color:#0b0b12;border-color:var(--c)}
  .dk-part.off{opacity:.38;background:repeating-linear-gradient(135deg,rgba(255,255,255,.04) 0 6px,transparent 6px 12px),#121218;border-style:dashed;filter:grayscale(.9)}
  .dk-part.off:hover{opacity:.7}
  .dk-part.loop{box-shadow:0 0 0 2px var(--c) inset}
  .dk-none{font-size:12px;color:#8a8aa0;padding:8px 2px}
  .dk-pop{position:fixed;z-index:9600;background:#17171f;border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:12px;
    box-shadow:0 18px 50px rgba(0,0,0,.55);color:#ececf3;font-size:13px}
  .dk-pop-h{font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#a6a6bc;margin-bottom:8px}
  .dk-kr{display:grid;grid-template-columns:1fr auto auto;gap:6px;align-items:center;padding:4px 0}
  .dk-kr button{font:inherit;font-size:12px;padding:5px 9px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);color:#fff;cursor:pointer}
  .dk-kk{min-width:84px}.dk-kk.cap{background:var(--accent,#8b5cf6);color:#0b0b12}
  .dk-note{margin-top:8px;font-size:11px;color:#8a8aa0;line-height:1.4}
  .dk-fi{display:flex;gap:6px}.dk-fi input{flex:1;min-width:0;font:inherit;padding:8px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.12);background:#0e0e14;color:#fff}
  .dk-fl{margin-top:8px;max-height:52vh;overflow:auto;display:flex;flex-direction:column;gap:2px}
  .dk-fl button{display:flex;flex-direction:column;align-items:flex-start;text-align:left;font:inherit;padding:7px 10px;border-radius:8px;border:none;background:transparent;color:#fff;cursor:pointer}
  .dk-fl button:hover,.dk-fl button.cur{background:rgba(255,255,255,.08)}
  .dk-fl button span{font-size:11px;color:#9a9ab0}.dk-fl button.back{color:var(--accent,#8b5cf6);font-weight:700}
  .dk-chords{margin:8px 14px 0;display:flex;flex-direction:column;gap:8px}
  .dk-chords.off{display:none}
  .dk-bars{display:flex;gap:6px;padding-top:14px}
  .dk-bar{position:relative;flex:1;display:flex;gap:3px;min-width:0}
  .dk-bn{position:absolute;left:2px;top:-14px;font-size:9px;color:#6d6d82}
  .dk-sec{position:absolute;left:18px;top:-15px;font-size:9.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--c)}
  .dk-beat{flex:1;height:34px;border-radius:7px;background:rgba(255,255,255,.06);display:flex;align-items:center;padding-left:7px;min-width:0;transition:background .08s}
  .dk-bar .dk-beat:first-of-type{border-left:3px solid rgba(255,255,255,.28)}
  .dk-beat b{font-size:15px;white-space:nowrap}
  .dk-beat.past{background:rgba(255,255,255,.025);color:#8a8aa0}
  .dk-beat.now{background:var(--accent,#8b5cf6);color:#0b0b12;box-shadow:0 0 14px color-mix(in srgb,var(--accent,#8b5cf6) 50%,transparent)}
  .dk-cards{display:flex;gap:12px;justify-content:center;align-items:stretch;overflow-x:auto}
  .dk-card{flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 10px;border-radius:12px;background:#f2f2f4;color:#111;cursor:pointer;
    transition:transform .15s,opacity .15s;min-width:110px}
  .dk-card.past{opacity:.35}
  .dk-card.now{transform:scale(1.06);box-shadow:0 0 0 3px var(--accent,#8b5cf6),0 10px 30px rgba(0,0,0,.45)}
  .dk-cn{font-weight:800;font-size:19px;line-height:1}
  .dk-gtr{width:104px;height:130px}
  .dk-chords[data-view="both"] .dk-gtr{width:88px;height:110px}
  .dk-gtr .gf{stroke:#7a7a86;stroke-width:1}.dk-gtr .gs{stroke:#55555f;stroke-width:1.1}.dk-gtr .gn{fill:#222}
  .dk-gtr .gd{fill:#26262c}.dk-gtr .gt{fill:#fff;font:700 9px sans-serif;text-anchor:middle}
  .dk-gtr .gx{fill:#333;font:700 11px sans-serif;text-anchor:middle}.dk-gtr .go{fill:none;stroke:#333;stroke-width:1.4}
  .dk-gtr .gb{fill:#333;font:700 10px sans-serif;text-anchor:end}.dk-gtr .gl{fill:#555;font:700 8px sans-serif;text-anchor:middle}
  .dk-pno{width:150px;height:56px}
  .dk-pno .pw{fill:#fff;stroke:#9a9aa6;stroke-width:1}.dk-pno .pb{fill:#1c1c22}
  .dk-pno .pw.on{fill:#a9c7ff}.dk-pno .pw.root{fill:#7c5cff}.dk-pno .pb.on{fill:#4d78d6}.dk-pno .pb.root{fill:#6a46ff}
  .dk-pno .pt{fill:#1a1a22;font:700 7px sans-serif;text-anchor:middle}
  .dk.stage .dk-knob{width:54px}.dk.stage .dk-dial{width:44px;height:44px}.dk.stage .dk-ic{inset:10px}
  .dk.stage .dk-part{height:38px}
  .dk-pctl{position:absolute;right:4px;bottom:4px;padding:0}
  .dk.steems .dk-pctl{position:relative;right:auto;bottom:auto;padding:3px 6px 0}
  /* a duet on the whole screen: the singers get the room — the deck and the chords made small */
  #nm-stage:has(.nm-stage-body.duo) .dk.stage{padding:6px 12px;gap:6px}
  #nm-stage:has(.nm-stage-body.duo) .dk.stage .dk-nm,#nm-stage:has(.nm-stage-body.duo) .dk.stage .dk-v{display:none}
  #nm-stage:has(.nm-stage-body.duo) .dk.stage .dk-part{height:30px}
  #nm-stage:has(.nm-stage-body.duo) .dk-bars{padding-top:12px}
  #nm-stage:has(.nm-stage-body.duo) .dk-beat{height:24px}
  #nm-stage:has(.nm-stage-body.duo) .dk-beat b{font-size:13px}
  #nm-stage:has(.nm-stage-body.duo) .dk-card{padding:4px 8px;min-width:86px;gap:2px}
  #nm-stage:has(.nm-stage-body.duo) .dk-cn{font-size:15px}
  #nm-stage:has(.nm-stage-body.duo) .dk-chords .dk-gtr{width:64px;height:80px}
  #nm-stage:has(.nm-stage-body.duo) .dk-chords .dk-pno{width:108px;height:40px}
  #dk-find,#dk-chords-btn{display:inline-flex;align-items:center;gap:6px}
  #dk-chords-btn.on{background:var(--accent);color:#06150f;border-color:var(--accent);font-weight:700}
  `;
  { const st = document.createElement("style"); st.id = "dk-css"; st.textContent = CSS; document.head.appendChild(st); }
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const D = () => Mixer.deck;

  // ---- what is kept: the keys, the chord helper
  const KEY = "pick-deck";
  const ACTS = {play: "Přehrát / pauza", prev: "Předchozí část", next: "Další část", again: "Část znovu od začátku", loop: "Smyčka na části zap / vyp"};
  const cfg = {keys: {play: ["Space"], prev: ["ArrowLeft"], next: ["ArrowRight"], again: [], loop: []}, chords: true, chordView: "both"};
  try{
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    if(v.keys) Object.keys(cfg.keys).forEach(a => { if(Array.isArray(v.keys[a])) cfg.keys[a] = v.keys[a]; });
    if("chords" in v) cfg.chords = !!v.chords;
    if(["guitar", "piano", "both"].includes(v.chordView)) cfg.chordView = v.chordView;
  }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  const keyName = (c) => ({Space: "mezerník", ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Enter: "Enter", Tab: "Tab",
    PageUp: "PgUp", PageDown: "PgDn", Home: "Home", End: "End"})[c] || c.replace(/^Key/, "").replace(/^Digit/, "").replace(/^Numpad/, "num ");

  // ---- the parts of a song: left out, played n times (kept per song)
  let parts = {}, partsSong = null;
  const song = () => Mixer.currentSong() || "";
  function partsFor(name){
    if(partsSong !== name){ partsSong = name; try{ parts = JSON.parse(localStorage.getItem("pick-deck-parts:" + name) || "{}"); }catch(e){ parts = {}; } }
    return parts;
  }
  const pid = (sec) => Number(sec.start).toFixed(2);
  function partOf(sec){ const p = partsFor(song())[pid(sec)] || {}; return {off: !!p.off, times: Math.max(1, Math.min(8, p.times || 1))}; }
  function setPart(sec, patch){
    const all = partsFor(song()), p = {...(all[pid(sec)] || {}), ...patch};
    if(!p.off) delete p.off;
    if(!(p.times > 1)) delete p.times;
    if(Object.keys(p).length) all[pid(sec)] = p; else delete all[pid(sec)];
    try{ localStorage.setItem("pick-deck-parts:" + partsSong, JSON.stringify(all)); }catch(e){}
    paint(true);
  }
  const secs = () => (Mixer.sectionsNow ? Mixer.sectionsNow() : []);
  const idxAt = (list, t) => list.findIndex(x => t >= x.start && t < x.end);
  function nextOn(list, i, dir){
    let j = i + dir;
    while(j >= 0 && j < list.length && partOf(list[j]).off) j += dir;
    return j >= 0 && j < list.length ? j : -1;
  }

  // ---- the way through the song, asked by the mixer at every frame while it plays: a part played
  // again, a part left out. A part jumped to by hand plays even when it is left out.
  let at = -1, pass = 1, allow = -1, lastT = null;
  function flow(t){
    const list = secs();
    if(!list.length) return null;
    const prevT = lastT;
    lastT = t;
    const i = idxAt(list, t);
    // A frame can step over a part's end before it is seen coming (a busy moment): the part just
    // left by playing on decides all the same — again, or on past the parts left out.
    if(at >= 0 && i !== at && list[at] && prevT != null && t > prevT && t - prevT < 0.6 && t >= list[at].end - 0.001){
      const k = at, st = partOf(list[k]);
      if(pass < st.times){ pass++; return list[k].start; }
      const j = nextOn(list, k, 1);
      if(j !== k + 1 && j !== i){ at = j; pass = 1; return j < 0 ? Mixer.length() : list[j].start; }
    }
    if(i !== at){ at = i; pass = 1; if(i !== allow) allow = -1; }
    if(i < 0) return null;
    const sec = list[i], st = partOf(sec);
    if(st.off && allow !== i){ const j = nextOn(list, i, 1); return j < 0 ? Mixer.length() : list[j].start; }
    if(t >= sec.end - 0.04){
      if(pass < st.times){ pass++; return sec.start; }
      const j = nextOn(list, i, 1);
      if(j !== i + 1) return j < 0 ? Mixer.length() : list[j].start;
    }
    return null;
  }

  // ---- the actions (keys, pedal, the deck's own buttons)
  const loopIsPart = (list, lp) => lp && list.findIndex(x => Math.abs(x.start - lp.start) < 0.05 && Math.abs(x.end - lp.end) < 0.05);
  function loopOn(sec){
    Mixer.setLoop(sec.start, sec.end);
    const b = document.getElementById("mixer-loop"); if(b) b.classList.add("active");
  }
  function loopOff(){
    Mixer.setLoop(0, 0);
    const b = document.getElementById("mixer-loop"); if(b) b.classList.remove("active");
  }
  function go(j, opts = {}){
    const list = secs(), sec = list[j];
    if(!sec) return null;
    allow = j; at = j; pass = 1;
    const lp = Mixer.loopNow(), li = loopIsPart(list, lp);
    if(lp && li >= 0) loopOn(sec);                        // the loop goes along with the part
    else if(lp && (sec.start < lp.start || sec.start >= lp.end)) loopOff();
    Mixer.seek(sec.start);
    if(opts.play !== false && !Mixer.isPlaying()) Mixer.play();
    paint();
    return sec.name;
  }
  function noParts(){ showToast("Píseň zatím nemá rozdělené části — analýza ještě běží, nebo se nezdařila"); return null; }
  function jump(dir){
    const list = secs();
    if(!list.length) return noParts();
    const t = Mixer.currentTime();
    let i = idxAt(list, t);
    if(i < 0) i = t < list[0].start ? -1 : list.length;
    if(!dir) return go(Math.max(0, Math.min(list.length - 1, i)));
    const j = nextOn(list, i, dir);
    if(j < 0){ showToast(dir > 0 ? "Tohle je poslední část" : "Tohle je první část"); return null; }
    return go(j);
  }
  function toggleLoop(j){
    const list = secs();
    if(!list.length) return noParts();
    if(j == null){ j = idxAt(list, Mixer.currentTime()); if(j < 0) j = 0; }
    const lp = Mixer.loopNow();
    if(lp && loopIsPart(list, lp) === j){ loopOff(); paint(true); return "smyčka vypnutá"; }
    loopOn(list[j]);
    const t = Mixer.currentTime();
    if(t < list[j].start || t >= list[j].end) go(j); else paint(true);
    return "smyčka: " + list[j].name;
  }
  function run(a){
    if(!song()) return null;
    if(a === "play"){ D().toggle(); paint(); return Mixer.isPlaying() ? "hraje" : "stop"; }
    if(a === "next") return jump(1);
    if(a === "prev") return jump(-1);
    if(a === "again") return jump(0);
    if(a === "loop") return toggleLoop();
    return null;
  }

  // ---- keys: whenever Steems shows a song, or the whole screen is up
  let capture = null;
  const active = () => !!song() && !(typeof Looper !== "undefined" && Looper.isOpen()) && (D().stageOn() || (document.getElementById("mixer") || {}).offsetParent != null);
  document.addEventListener("keydown", (e) => {
    if(capture){
      e.preventDefault(); e.stopPropagation();
      const a = capture; capture = null;
      if(e.code === "Escape"){ paintKeys(); return; }
      Object.keys(cfg.keys).forEach(x => { cfg.keys[x] = cfg.keys[x].filter(c => c !== e.code); });
      if(e.code !== "Backspace" && e.code !== "Delete") cfg.keys[a] = [e.code];
      else cfg.keys[a] = [];
      keep(); paintKeys(); paint(true);
      return;
    }
    if(!active() || e.metaKey || e.ctrlKey || e.altKey) return;
    // typing, or a slider moved by its keys: theirs — only Space on a slider or a checkbox still plays
    const tg = e.target, field = tg && tg.closest && tg.closest("input, textarea, select, [contenteditable]");
    if(field && !(e.code === "Space" && field.matches("input[type=range], input[type=checkbox]"))) return;
    if(typeof Dialog !== "undefined" && Dialog.isOpen && Dialog.isOpen()) return;
    const ed = document.getElementById("midi-editor");
    if(ed && !ed.hidden && (ed.contains(document.activeElement) || ed.matches(":hover"))) return;
    const a = Object.keys(cfg.keys).find(x => cfg.keys[x].includes(e.code));
    if(!a) return;
    e.preventDefault(); e.stopPropagation();
    run(a);
  }, true);

  // ---- a knob: an arc of 270° around the icon
  const arc = (f) => {
    const r = 27, a0 = -135 * Math.PI / 180, a1 = (-135 + 270 * Math.max(0.0001, Math.min(1, f))) * Math.PI / 180;
    const p = (a) => [32 + r * Math.sin(a), 32 - r * Math.cos(a)].map(v => v.toFixed(2)).join(" ");
    return `M ${p(a0)} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${p(a1)}`;
  };
  const knobHtml = (k) => `<div class="dk-knob${k.master ? " master" : ""}" data-k="${k.id}" style="--c:${esc(k.color)}" title="${esc(k.title)}">
      <div class="dk-dial"><svg viewBox="0 0 64 64"><path class="trk" d="${arc(1)}"/>${k.max > 1 ? `<path class="tick" d="${tickAt(1 / k.max)}"/>` : ""}<path class="val" d=""/></svg>
        <span class="dk-ic">${icon(k.icon, k.master ? 20 : 18)}</span></div>
      <span class="dk-nm">${esc(k.label)}</span><span class="dk-v"></span></div>`;
  function tickAt(f){
    const a = (-135 + 270 * f) * Math.PI / 180, p = (r) => [32 + r * Math.sin(a), 32 - r * Math.cos(a)].map(v => v.toFixed(2)).join(" ");
    return `M ${p(23)} L ${p(31)}`;
  }
  // knobs: {id, get() → {v, muted}, set(v), mute(), max}
  function wireKnob(node, k){
    const dial = node.querySelector(".dk-dial");
    const show = () => {
      const s = k.get(), f = s.v / k.max;
      node.querySelector(".val").setAttribute("d", arc(f));
      node.classList.toggle("muted", !!s.muted);
      node.querySelector(".dk-v").textContent = s.muted ? "ztlumeno" : Math.round(s.v * 100) + " %";
    };
    node._show = show;
    dial.addEventListener("wheel", (e) => {
      e.preventDefault();
      const step = (e.shiftKey ? 0.01 : 0.04) * (Math.abs(e.deltaY) > 40 ? 1.5 : 1);
      k.set(Math.max(0, Math.min(k.max, k.get().v + (e.deltaY < 0 ? step : -step))));
      show();
    }, {passive: false});
    dial.addEventListener("pointerdown", (e) => {
      if(e.button !== 0) return;
      const y0 = e.clientY, v0 = k.get().v;
      let moved = false;
      dial.setPointerCapture(e.pointerId);
      const mv = (ev) => {
        if(Math.abs(ev.clientY - y0) > 2) moved = true;
        if(!moved) return;
        k.set(Math.max(0, Math.min(k.max, v0 + (y0 - ev.clientY) / 160 * k.max)));
        show();
      };
      const up = () => { dial.removeEventListener("pointermove", mv); dial.removeEventListener("pointerup", up); dial.removeEventListener("pointercancel", up); };
      dial.addEventListener("pointermove", mv); dial.addEventListener("pointerup", up); dial.addEventListener("pointercancel", up);
    });
    node.addEventListener("dblclick", (e) => { e.preventDefault(); k.mute(); show(); });
    show();
  }
  function master(){
    const sl = document.getElementById("pb-volume"), au = document.getElementById("pb-audio");
    return {id: "master", master: true, label: "Master", icon: "volume", color: "#f1f1f6", max: 1,
      title: "Celková hlasitost — kolečko myši nebo tažení nahoru a dolů; dvojklik ztlumí a zase pustí",
      get: () => ({v: sl ? parseFloat(sl.value) || 0 : 1, muted: !!(au && au.muted)}),
      set: (v) => { if(sl){ sl.value = v; sl.dispatchEvent(new Event("input")); } },
      mute: () => { const b = document.getElementById("pb-mute"); if(b) b.click(); }};
  }
  // a separated track's own name in Czech (a name given by hand stays)
  const CZ = {drums: "Bicí", bass: "Basa", guitar: "Kytara", vocals: "Zpěv", piano: "Klavír", other: "Ostatní"};
  const czLabel = (tr) => { if(tr.label !== tr.stem) return tr.label; const [b, ...r] = tr.stem.split("_"); return CZ[b] ? [CZ[b], ...r].join(" ") : tr.label; };
  function trackKnob(tr){
    tr = {...tr, label: czLabel(tr)};
    const find = () => D().tracks().find(x => x.stem === tr.stem) || tr;
    return {id: tr.stem, label: tr.label, icon: tr.icon || "sliders", color: tr.color, max: 1.5,
      title: `${tr.label} — kolečko myši nebo tažení nahoru a dolů; dvojklik ztlumí a zase pustí`,
      get: () => { const x = find(); return {v: x.vol, muted: x.muted}; },
      set: (v) => { const x = find(); D().setVol(x.i, v); },
      mute: () => { const x = find(); D().toggleMute(x.i); }};
  }

  // ---- the deck, in each place it is shown
  const hosts = [];
  function mountDeck(where, kind){
    const box = document.createElement("div");
    box.className = "dk " + kind;
    box.innerHTML = `<div class="dk-top">
        <button type="button" class="dk-play" title="Přehrát / pauza (${esc(keysOf("play"))})"></button>
        <div class="dk-tracks"></div>
        <div class="dk-mst"></div>
        <div class="dk-facts"></div>
        ${kind === "steems" ? `<button type="button" class="dk-lpbtn" title="Looper: nahraj si píseň po vrstvách, v jejím tempu a s jejími částmi">${icon("repeat", 16)}</button>` : ""}
        <button type="button" class="dk-keys" title="Klávesy a MIDI pedál pro přehrávání a skoky po částech">${icon("keyboard", 16)}</button>
      </div>
      <div class="dk-parts"></div>`;
    where(box);
    const h = {box, kind, sigT: "", sigP: ""};
    box.querySelector(".dk-play").onclick = () => run("play");
    box.querySelector(".dk-keys").onclick = (e) => openKeys(e.currentTarget);
    const lb = box.querySelector(".dk-lpbtn");
    if(lb) lb.onclick = () => { if(typeof Looper !== "undefined") Looper.open(song()); };
    const m = master();
    box.querySelector(".dk-mst").innerHTML = knobHtml(m);
    wireKnob(box.querySelector(".dk-mst .dk-knob"), m);
    hosts.push(h);
    return h;
  }
  const keysOf = (a) => cfg.keys[a].map(keyName).join(", ") || "bez klávesy";
  function paintTracks(h){
    const tr = D().tracks(), sig = song() + "|" + tr.map(t => [t.stem, t.label, t.color, t.icon].join(",")).join("|");
    const box = h.box.querySelector(".dk-tracks");
    if(sig !== h.sigT){
      h.sigT = sig;
      const ks = tr.map(trackKnob);
      box.innerHTML = ks.map(knobHtml).join("") || `<span class="dk-none">Žádné stopy</span>`;
      box.querySelectorAll(".dk-knob").forEach((n, i) => wireKnob(n, ks[i]));
    } else box.querySelectorAll(".dk-knob").forEach(n => n._show && n._show());
    const mk = h.box.querySelector(".dk-mst .dk-knob");
    if(mk && mk._show) mk._show();
  }
  function paintParts(h, force){
    const list = secs(), box = h.box.querySelector(".dk-parts"), lp = Mixer.loopNow(), li = loopIsPart(list, lp);
    const sig = song() + "|" + list.map(x => [pid(x), x.end, x.name, x.color, JSON.stringify(partOf(x))].join(",")).join("|") + "|" + li;
    if(sig !== h.sigP || force){
      h.sigP = sig;
      if(!list.length){
        const st = D().analysis();
        box.innerHTML = `<span class="dk-none">${st === "loading" ? "Části písně se hledají…" : st === "failed" ? "Části písně se nepodařilo najít (analýza se nezdařila)." : "Píseň zatím nemá rozdělené části."}</span>`;
      } else {
        const total = Math.max(1, list[list.length - 1].end - list[0].start);
        box.innerHTML = list.map((x, i) => { const p = partOf(x), loop = li === i;
          return `<div class="dk-part${p.off ? " off" : ""}${loop ? " loop" : ""}" data-i="${i}" style="--c:${esc(x.color || "#8391a5")};flex-grow:${((x.end - x.start) / total * 100).toFixed(2)}"
              title="${esc(x.name)} · ${fmt(x.start)}–${fmt(x.end)} — klik: hrát odsud">
            <div class="dk-pfill"></div>
            <div class="dk-pname"><b>${esc(x.name)}</b>${p.times > 1 ? `<i class="dk-x">×${p.times}</i>` : ""}${loop ? `<i class="dk-lp">${icon("repeat", 11)}</i>` : ""}</div>
            <div class="dk-pctl">
              <button type="button" data-a="off" title="${p.off ? "Zapnout — část se zase hraje" : "Vypnout — při přehrávání se část přeskočí"}">${p.off ? "zap" : "vyp"}</button>
              <button type="button" data-a="times" title="Kolikrát za sebou se část zahraje (klik přidá, až do 4×, pak zase 1×)">×${p.times}</button>
              <button type="button" data-a="loop" class="${loop ? "on" : ""}" title="Smyčka na části — hraje dokola">${icon("repeat", 12)}</button>
            </div></div>`; }).join("");
        box.querySelectorAll(".dk-part").forEach(n => {
          const i = +n.dataset.i;
          n.onclick = (e) => {
            const b = e.target.closest("button");
            if(!b){ go(i); return; }
            e.stopPropagation();
            const sec = secs()[i]; if(!sec) return;
            const p = partOf(sec);
            if(b.dataset.a === "off") setPart(sec, {off: !p.off});
            else if(b.dataset.a === "times") setPart(sec, {times: p.times >= 4 ? 1 : p.times + 1});
            else if(b.dataset.a === "loop") toggleLoop(i);
          };
        });
      }
    }
    const t = Mixer.currentTime(), i = idxAt(list, t);
    box.querySelectorAll(".dk-part").forEach(n => {
      const j = +n.dataset.i, on = j === i;
      n.classList.toggle("now", on);
      n.style.setProperty("--p", on ? ((t - list[j].start) / Math.max(0.01, list[j].end - list[j].start) * 100).toFixed(1) + "%" : j < i ? "100%" : "0%");
      n.classList.toggle("past", j < i);
    });
  }
  function paintFacts(h){
    const f = D().facts(), html = [
      `<span title="Tónina písně (odhad z nahrávky)">Tónina <b>${esc(f.key || "—")}</b></span>`,
      `<span title="${f.tuningFrom === "tab" ? "Ladění podle tabu" : f.tuningFrom ? "Ladění uložené u písně" : "Ladění zatím neznám — zjistí se z tabu"}">Ladění <b>${esc(f.tuning || "—")}</b></span>`,
      `<span title="Tempo písně (podle bicích)"><b>${f.bpm ? f.bpm : "—"}</b> BPM</span>`].join("");
    const box = h.box.querySelector(".dk-facts");
    if(box.dataset.h !== html){ box.dataset.h = html; box.innerHTML = html; }
  }
  function paint(force){
    if(typeof Mixer === "undefined" || !Mixer.deck) return;
    hosts.forEach(h => {
      if(!h.box.isConnected || h.box.offsetParent === null) return;
      const play = Mixer.isPlaying(), b = h.box.querySelector(".dk-play");
      if(b.dataset.p !== String(play)){ b.dataset.p = String(play); b.innerHTML = icon(play ? "pause" : "play", h.kind === "stage" ? 18 : 20); }
      paintTracks(h); paintParts(h, force); paintFacts(h);
    });
    if(chordBox && chordBox.isConnected && chordBox.offsetParent !== null) paintChords();
  }

  // ---- keys and pedal, in a little window by the deck
  let keysPop = null, keysAnchor = null;
  function openKeys(anchor){
    if(keysPop){ keysPop.remove(); keysPop = null; if(keysAnchor === anchor) return; }
    keysAnchor = anchor;
    keysPop = document.createElement("div");
    keysPop.className = "dk-pop";
    (anchor.closest("#nm-stage") || document.body).appendChild(keysPop);
    paintKeys();
    const r = anchor.getBoundingClientRect(), w = 380;
    Object.assign(keysPop.style, {left: Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + "px", top: (r.bottom + 6) + "px", width: w + "px"});
    setTimeout(() => document.addEventListener("pointerdown", away, true), 0);
  }
  function away(e){ if(keysPop && !keysPop.contains(e.target) && !(keysAnchor && keysAnchor.contains(e.target))){ keysPop.remove(); keysPop = null; capture = null; document.removeEventListener("pointerdown", away, true); } }
  function paintKeys(){
    if(!keysPop) return;
    const binds = (typeof Pedal !== "undefined" && Pedal.config) ? Pedal.config().binds || [] : [];
    keysPop.innerHTML = `<div class="dk-pop-h">Klávesy a pedál</div>
      ${Object.keys(ACTS).map(a => { const ped = binds.filter(b => b.action === "deck:" + a).map(b => b.label);
        return `<div class="dk-kr"><span>${esc(ACTS[a])}</span>
          <button type="button" class="dk-kk${capture === a ? " cap" : ""}" data-key="${a}" title="Klik a stiskni novou klávesu (Backspace = žádná, Esc = zpět)">${capture === a ? "stiskni klávesu…" : esc(keysOf(a))}</button>
          <button type="button" class="dk-kp" data-ped="${a}" title="Šlápni pak na spínač pedálu — přiřadí se mu tahle funkce">${ped.length ? "🦶 " + esc(ped.join(", ")) : "🦶 Naučit pedál"}</button></div>`; }).join("")}
      <div class="dk-note">Klávesy fungují ve Steems i na celé obrazovce, když nepíšeš do pole. Pedál se nastavuje i v Nastavení → MIDI pedál
        (tam i dvojklik a podržení spínače).</div>`;
    keysPop.querySelectorAll("[data-key]").forEach(b => b.onclick = () => { capture = b.dataset.key; paintKeys(); });
    keysPop.querySelectorAll("[data-ped]").forEach(b => b.onclick = async () => {
      if(typeof Pedal === "undefined" || !Pedal.learn){ showToast("MIDI pedál tu není k dispozici"); return; }
      if(!(await Pedal.connect())) return;
      Pedal.learn("deck:" + b.dataset.ped, "single");
      b.textContent = "🦶 Šlápni na spínač…";
      Pedal.onLearned(() => paintKeys());
    });
  }

  // ======================================================================
  // Chords: the shapes on the fretboard and the keys on the piano
  // ======================================================================
  const PC = {C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11};
  const NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const IV = {"": [0, 4, 7], m: [0, 3, 7], "7": [0, 4, 7, 10], maj7: [0, 4, 7, 11], m7: [0, 3, 7, 10], sus4: [0, 5, 7], sus2: [0, 2, 7],
    dim: [0, 3, 6], aug: [0, 4, 8], "5": [0, 7], "6": [0, 4, 7, 9], m6: [0, 3, 7, 9], add9: [0, 4, 7, 14], "9": [0, 4, 7, 10, 14],
    m9: [0, 3, 7, 10, 14], dim7: [0, 3, 6, 9], m7b5: [0, 3, 6, 10], "7sus4": [0, 5, 7, 10]};
  function parseChord(name){
    const m = /^([A-G])([#b♯♭]?)([^/]*)(?:\/([A-G][#b♯♭]?))?$/.exec(String(name || "").trim());
    if(!m) return null;
    const acc = m[2] === "#" || m[2] === "♯" ? 1 : m[2] === "b" || m[2] === "♭" ? -1 : 0;
    const pc = (PC[m[1]] + acc + 12) % 12;
    let q = m[3].replace(/^min(?!or)/, "m").replace(/^maj$/, "").replace(/^M7$/, "maj7").replace(/^:/, "");
    if(q === "min") q = "m";
    const iv = IV[q] || (q.startsWith("m") && !q.startsWith("maj") ? IV.m : IV[""]);
    const bass = m[4] ? (PC[m[4][0]] + (/[#♯]/.test(m[4]) ? 1 : /[b♭]/.test(m[4]) ? -1 : 0) + 12) % 12 : null;
    return {pc, q, iv, bass, name: String(name).trim()};
  }
  // The shape a quality is played with: the ones there are no drawn shapes for take the nearest.
  const SHAPE_Q = (q) => ({"6": "", add9: "", "9": "7", m6: "m", m9: "m7", dim7: "dim", m7b5: "dim", "7sus4": "sus4"})[q] ?? (IV[q] ? q : (q.startsWith("m") && !q.startsWith("maj") ? "m" : ""));
  // open shapes: frets low E → high e (-1 = not played) and fingers
  const OPEN = {
    "C": [[-1, 3, 2, 0, 1, 0], [0, 3, 2, 0, 1, 0]], "C7": [[-1, 3, 2, 3, 1, 0], [0, 3, 2, 4, 1, 0]], "Cmaj7": [[-1, 3, 2, 0, 0, 0], [0, 3, 2, 0, 0, 0]],
    "Csus2": [[-1, 3, 0, 0, 1, 3], [0, 2, 0, 0, 1, 3]], "Csus4": [[-1, 3, 3, 0, 1, 1], [0, 3, 4, 0, 1, 1]],
    "D": [[-1, -1, 0, 2, 3, 2], [0, 0, 0, 1, 3, 2]], "Dm": [[-1, -1, 0, 2, 3, 1], [0, 0, 0, 2, 3, 1]], "D7": [[-1, -1, 0, 2, 1, 2], [0, 0, 0, 2, 1, 3]],
    "Dm7": [[-1, -1, 0, 2, 1, 1], [0, 0, 0, 2, 1, 1]], "Dmaj7": [[-1, -1, 0, 2, 2, 2], [0, 0, 0, 1, 2, 3]], "Dsus4": [[-1, -1, 0, 2, 3, 3], [0, 0, 0, 1, 3, 4]],
    "Dsus2": [[-1, -1, 0, 2, 3, 0], [0, 0, 0, 1, 3, 0]], "D5": [[-1, -1, 0, 2, 3, -1], [0, 0, 0, 1, 3, 0]],
    "E": [[0, 2, 2, 1, 0, 0], [0, 2, 3, 1, 0, 0]], "Em": [[0, 2, 2, 0, 0, 0], [0, 2, 3, 0, 0, 0]], "E7": [[0, 2, 0, 1, 0, 0], [0, 2, 0, 1, 0, 0]],
    "Em7": [[0, 2, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0]], "Emaj7": [[0, 2, 1, 1, 0, 0], [0, 3, 1, 2, 0, 0]], "Esus4": [[0, 2, 2, 2, 0, 0], [0, 2, 3, 4, 0, 0]],
    "E5": [[0, 2, 2, -1, -1, -1], [0, 1, 2, 0, 0, 0]],
    "G": [[3, 2, 0, 0, 0, 3], [2, 1, 0, 0, 0, 3]], "G7": [[3, 2, 0, 0, 0, 1], [3, 2, 0, 0, 0, 1]], "Gmaj7": [[3, 2, 0, 0, 0, 2], [3, 2, 0, 0, 0, 1]],
    "G5": [[3, 5, 5, -1, -1, -1], [1, 3, 4, 0, 0, 0]],
    "A": [[-1, 0, 2, 2, 2, 0], [0, 0, 1, 2, 3, 0]], "Am": [[-1, 0, 2, 2, 1, 0], [0, 0, 2, 3, 1, 0]], "A7": [[-1, 0, 2, 0, 2, 0], [0, 0, 2, 0, 3, 0]],
    "Am7": [[-1, 0, 2, 0, 1, 0], [0, 0, 2, 0, 1, 0]], "Amaj7": [[-1, 0, 2, 1, 2, 0], [0, 0, 2, 1, 3, 0]], "Asus4": [[-1, 0, 2, 2, 3, 0], [0, 0, 1, 2, 3, 0]],
    "Asus2": [[-1, 0, 2, 2, 0, 0], [0, 0, 1, 2, 0, 0]], "A5": [[-1, 0, 2, 2, -1, -1], [0, 0, 1, 2, 0, 0]],
  };
  // movable shapes, by the fret r of the root: on the low E string (E shape) and on the A string (A shape)
  const E_SHAPE = {"": [[0, 2, 2, 1, 0, 0], [1, 3, 4, 2, 1, 1]], m: [[0, 2, 2, 0, 0, 0], [1, 3, 4, 1, 1, 1]], "7": [[0, 2, 0, 1, 0, 0], [1, 3, 1, 2, 1, 1]],
    m7: [[0, 2, 0, 0, 0, 0], [1, 3, 1, 1, 1, 1]], maj7: [[0, -9, 1, 1, 0, -9], [1, 0, 3, 4, 2, 0]], sus4: [[0, 2, 2, 2, 0, 0], [1, 2, 3, 4, 1, 1]],
    "5": [[0, 2, 2, -9, -9, -9], [1, 3, 4, 0, 0, 0]]};
  const A_SHAPE = {"": [[-9, 0, 2, 2, 2, 0], [0, 1, 2, 3, 4, 1]], m: [[-9, 0, 2, 2, 1, 0], [0, 1, 3, 4, 2, 1]], "7": [[-9, 0, 2, 0, 2, 0], [0, 1, 3, 1, 4, 1]],
    m7: [[-9, 0, 2, 0, 1, 0], [0, 1, 3, 1, 2, 1]], maj7: [[-9, 0, 2, 1, 2, 0], [0, 1, 3, 2, 4, 1]], sus4: [[-9, 0, 2, 2, 3, 0], [0, 1, 2, 3, 4, 1]],
    sus2: [[-9, 0, 2, 2, 0, 0], [0, 1, 3, 4, 1, 1]], dim: [[-9, 0, 1, 2, 1, -9], [0, 1, 2, 4, 3, 0]], aug: [[-9, 0, 3, 2, 2, -9], [0, 1, 4, 2, 3, 0]],
    "5": [[-9, 0, 2, 2, -9, -9], [0, 1, 3, 4, 0, 0]]};
  function shapeOf(ch){
    const q = SHAPE_Q(ch.q), nm = NN[ch.pc] + q;
    if(OPEN[nm]) return {frets: OPEN[nm][0], fingers: OPEN[nm][1]};
    const cands = [];
    const add = (tbl, root0) => { const s = tbl[q]; if(!s) return; let r = (ch.pc - root0 + 12) % 12; if(r === 0) r = 12;
      cands.push({frets: s[0].map(f => f <= -9 ? -1 : f + r), fingers: s[1], r}); };
    add(E_SHAPE, 4); add(A_SHAPE, 9);
    if(!cands.length){ const s = A_SHAPE[""]; let r = (ch.pc - 9 + 12) % 12 || 12; return {frets: s[0].map(f => f <= -9 ? -1 : f + r), fingers: s[1]}; }
    cands.sort((a, b) => a.r - b.r);
    return cands[0];
  }
  function guitarSvg(ch){
    const sh = shapeOf(ch), fr = sh.frets, used = fr.filter(f => f > 0);
    const hi = used.length ? Math.max(...used) : 0, lo = used.length ? Math.min(...used) : 0;
    const base = hi <= 4 ? 1 : lo;
    const W = 120, H = 150, x0 = 18, y0 = 30, sw = 17, fh = 20, N = 5;
    const sx = (i) => x0 + i * sw, fy = (f) => y0 + (f - base + 0.5) * fh;
    let g = "";
    for(let i = 0; i <= N; i++) g += `<line x1="${x0}" y1="${y0 + i * fh}" x2="${x0 + 5 * sw}" y2="${y0 + i * fh}" class="gf"/>`;
    for(let i = 0; i < 6; i++) g += `<line x1="${sx(i)}" y1="${y0}" x2="${sx(i)}" y2="${y0 + N * fh}" class="gs"/>`;
    if(base === 1) g += `<rect x="${x0 - 1}" y="${y0 - 4}" width="${5 * sw + 2}" height="5" class="gn"/>`;
    else g += `<text x="${x0 - 6}" y="${y0 + fh * 0.62}" class="gb">${base}</text>`;
    fr.forEach((f, i) => { if(f < 0) g += `<text x="${sx(i)}" y="${y0 - 9}" class="gx">×</text>`; else if(f === 0) g += `<circle cx="${sx(i)}" cy="${y0 - 12}" r="4" class="go"/>`; });
    // a barre: finger 1 on the same fret across strings
    const one = fr.map((f, i) => ({f, i, fi: sh.fingers[i]})).filter(x => x.fi === 1 && x.f > 0);
    const barre = one.length > 1 && one.every(x => x.f === one[0].f) ? one : null;
    if(barre){ const a = barre[0].i, b = barre[barre.length - 1].i;
      g += `<rect x="${sx(a) - 7}" y="${fy(barre[0].f) - 7}" width="${sx(b) - sx(a) + 14}" height="14" rx="7" class="gd"/><text x="${(sx(a) + sx(b)) / 2}" y="${fy(barre[0].f) + 4}" class="gt">1</text>`; }
    fr.forEach((f, i) => { if(f > 0 && !(barre && barre.some(x => x.i === i))) g += `<circle cx="${sx(i)}" cy="${fy(f)}" r="7" class="gd"/>${sh.fingers[i] ? `<text x="${sx(i)}" y="${fy(f) + 4}" class="gt">${sh.fingers[i]}</text>` : ""}`; });
    "EADGBE".split("").forEach((s, i) => { g += `<text x="${sx(i)}" y="${y0 + N * fh + 14}" class="gl">${s}</text>`; });
    return `<svg class="dk-gtr" viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }
  function pianoSvg(ch){
    // two octaves from C; the chord from its root upwards, the root marked
    const notes = new Set(), root = ch.pc;
    ch.iv.forEach(iv => { let n = root + iv; while(n > 23) n -= 12; notes.add(n); });
    if(ch.bass != null) notes.add(ch.bass);
    const WH = [0, 2, 4, 5, 7, 9, 11], BL = [1, 3, 6, 8, 10];
    const ww = 12, W = ww * 14, H = 62;
    let g = "";
    for(let o = 0; o < 2; o++) WH.forEach((pc, k) => { const n = o * 12 + pc, x = (o * 7 + k) * ww, on = notes.has(n);
      g += `<rect x="${x + 0.5}" y="0.5" width="${ww - 1}" height="${H - 1}" rx="2" class="pw${on ? (n % 12 === root ? " root" : " on") : ""}"/>`;
      if(on) g += `<text x="${x + ww / 2}" y="${H - 6}" class="pt">${NN[pc].replace("#", "♯")}</text>`; });
    for(let o = 0; o < 2; o++) BL.forEach(pc => { const n = o * 12 + pc, k = WH.indexOf(pc - 1), x = (o * 7 + k + 1) * ww - 4, on = notes.has(n);
      g += `<rect x="${x}" y="0" width="8" height="${H * 0.6}" rx="1.5" class="pb${on ? (n % 12 === root ? " root" : " on") : ""}"/>`; });
    return `<svg class="dk-pno" viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }

  // ---- the chord helper on the whole screen: bars with beats and chords, then the cards
  let chordBox = null, chordSig = "", barSig = "";
  function bars(){
    const gr = D().grid();
    let db = (gr.downbeats || []).slice();
    const beats = gr.beats || [], per = gr.perBar || 4;
    if(!db.length && beats.length) db = beats.filter((_, k) => k % per === 0);
    if(!db.length) return null;
    return {db, beats, per};
  }
  const chordAt = (list, t) => { let j = -1; for(let k = 0; k < list.length; k++){ if(list[k].start <= t + 0.03) j = k; else break; } return j >= 0 && t < list[j].end + 0.05 ? j : -1; };
  function paintChords(){
    const host = chordBox, t = Mixer.currentTime(), list = D().chords();
    host.classList.toggle("off", !cfg.chords);
    host.dataset.view = cfg.chordView;
    if(!cfg.chords) return;
    const strip = host.querySelector(".dk-bars"), cards = host.querySelector(".dk-cards");
    if(!list.length){
      const st = D().analysis(), msg = st === "loading" ? "Akordy se hledají…" : st === "failed" ? "Akordy se nepodařilo najít (analýza se nezdařila)." : "Píseň zatím nemá akordy.";
      if(cards.dataset.msg !== msg){ cards.dataset.msg = msg; cards.innerHTML = `<span class="dk-none">${msg}</span>`; strip.innerHTML = ""; chordSig = barSig = ""; }
      return;
    }
    cards.dataset.msg = "";
    // the bars around now
    const B = bars(), sl = secs();
    if(B){
      let b = 0; for(let k = 0; k < B.db.length; k++){ if(B.db[k] <= t + 0.02) b = k; else break; }
      const from = Math.max(0, b - 1), to = Math.min(B.db.length - 1, from + 7);
      const sig = song() + ":" + from + ":" + list.length;
      if(sig !== barSig){
        barSig = sig;
        let prevChord = null, html = "";
        for(let k = from; k <= to; k++){
          const a = B.db[k], e = B.db[k + 1] != null ? B.db[k + 1] : a + (B.db[k] - (B.db[k - 1] != null ? B.db[k - 1] : a - 2));
          let bt = B.beats.filter(x => x >= a - 0.02 && x < e - 0.02);
          if(!bt.length) bt = Array.from({length: B.per}, (_, q) => a + (e - a) * q / B.per);
          const sec = sl.find(x => x.start >= a - 0.3 && x.start < e - 0.3);
          html += `<div class="dk-bar" data-a="${a}" data-e="${e}">${sec ? `<span class="dk-sec" style="--c:${esc(sec.color || "#8391a5")}">${esc(sec.name)}</span>` : ""}<span class="dk-bn">${k + 1}</span>
            ${bt.map((x, q) => { const j = chordAt(list, x), c = j >= 0 ? list[j].chord : null, show = c && (c !== prevChord || (k === from && q === 0)); prevChord = c;
              return `<div class="dk-beat" data-t="${x.toFixed(3)}">${show ? `<b>${esc(c)}</b>` : ""}</div>`; }).join("")}</div>`;
        }
        strip.innerHTML = html;
      }
      strip.querySelectorAll(".dk-beat").forEach((n, k, all) => {
        const x = +n.dataset.t, nx = all[k + 1] ? +all[k + 1].dataset.t : x + 0.6;
        n.classList.toggle("now", t >= x - 0.02 && t < nx - 0.02);
        n.classList.toggle("past", t >= nx - 0.02);
      });
    } else if(strip.innerHTML) strip.innerHTML = "";
    // the cards: the last chord, the one now, three to come
    let ci = chordAt(list, t);
    if(ci < 0){ ci = list.findIndex(x => x.start > t) - 1; }
    const from = Math.max(0, ci - 1), shown = list.slice(from, from + 5);
    const sig = song() + ":" + from + ":" + ci + ":" + cfg.chordView;
    if(sig !== chordSig){
      chordSig = sig;
      cards.innerHTML = shown.map((x, k) => { const ch = parseChord(x.chord), i = from + k;
        const nm = x.chord + (x.bass && ch && x.bass !== NN[ch.pc] ? "/" + x.bass : "");
        return `<div class="dk-card${i < ci ? " past" : i === ci ? " now" : ""}" title="${esc(nm)} · ${fmt(x.start)}">
          <div class="dk-cn">${esc(nm)}</div>
          ${ch ? `${cfg.chordView !== "piano" ? guitarSvg(ch) : ""}${cfg.chordView !== "guitar" ? pianoSvg(ch) : ""}` : `<div class="dk-none">?</div>`}</div>`; }).join("");
      cards.querySelectorAll(".dk-card").forEach((n, k) => n.onclick = () => { Mixer.seek(shown[k].start); paint(); });
    }
  }

  // ---- the finder on the whole screen: a song or a band; picked, it loads and the screen stays
  let findPop = null, fq = {song: "", band: "", pick: ""};
  function openFind(anchor){
    if(findPop){ findPop.remove(); findPop = null; return; }
    findPop = document.createElement("div");
    findPop.className = "dk-pop dk-find";
    findPop.innerHTML = `<div class="dk-pop-h">Najít píseň ve Steems</div>
      <div class="dk-fi"><input type="search" data-k="song" placeholder="Hledat píseň…" autocomplete="off"><input type="search" data-k="band" placeholder="Hledat kapelu…" autocomplete="off"></div>
      <div class="dk-fl"></div>`;
    (anchor.closest("#nm-stage") || document.body).appendChild(findPop);
    const r = anchor.getBoundingClientRect(), w = 420;
    Object.assign(findPop.style, {left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px", top: (r.bottom + 6) + "px", width: w + "px"});
    findPop.querySelectorAll("input").forEach(i => {
      i.value = fq[i.dataset.k];
      i.oninput = () => { fq[i.dataset.k] = i.value; if(i.dataset.k === "band") fq.pick = ""; findList(); };
      i.onkeydown = (e) => { e.stopPropagation(); if(e.key === "Escape"){ findPop.remove(); findPop = null; } if(e.key === "Enter"){ const b = findPop.querySelector(".dk-fl button"); if(b) b.click(); } };
    });
    findList();
    findPop.querySelector('input[data-k="song"]').focus();
    const off = (e) => { if(findPop && !findPop.contains(e.target) && !anchor.contains(e.target)){ findPop.remove(); findPop = null; } if(!findPop) document.removeEventListener("pointerdown", off, true); };
    setTimeout(() => document.addEventListener("pointerdown", off, true), 0);
  }
  function findList(){
    if(!findPop) return;
    const has = SteemsFind.has, band = (s) => ((stemsDisplay(s).artist || "").split(/,|;| feat\.? | ft\.? /i)[0] || "").trim();
    const row = (s) => { const d = stemsDisplay(s); return `<button type="button" data-song="${esc(s.name)}" class="${s.name === song() ? "cur" : ""}"><b>${esc(d.title || s.name)}</b><span>${esc(d.artist || "")}</span></button>`; };
    let html;
    if(fq.pick){
      const mine = steemsSongs.filter(s => band(s) === fq.pick && has(fq.song, [stemsDisplay(s).title, s.name]));
      html = `<button type="button" class="back" data-back>‹ ${esc(fq.pick)} (${mine.length})</button>` + mine.map(row).join("");
    } else if(fq.band.trim()){
      const by = new Map();
      steemsSongs.forEach(s => { const b = band(s); if(b && has(fq.band, [b])) by.set(b, (by.get(b) || 0) + 1); });
      html = [...by].sort((a, b) => a[0].localeCompare(b[0], "cs")).slice(0, 40)
        .map(([b, n]) => `<button type="button" data-band="${esc(b)}"><b>${esc(b)}</b><span>${n} ${n === 1 ? "píseň" : n < 5 ? "písně" : "písní"}</span></button>`).join("")
        || `<div class="dk-none">Žádná kapela tak nezačíná.</div>`;
    } else {
      const ss = steemsSongs.filter(s => has(fq.song, [stemsDisplay(s).title, s.name]));
      html = ss.slice(0, 60).map(row).join("") + (ss.length > 60 ? `<div class="dk-none">… a dalších ${ss.length - 60} — piš dál</div>` : "")
        || `<div class="dk-none">Žádná píseň tak nezačíná.</div>`;
    }
    const box = findPop.querySelector(".dk-fl");
    box.innerHTML = html;
    box.querySelectorAll("[data-song]").forEach(b => b.onclick = () => { const n = b.dataset.song; findPop.remove(); findPop = null; if(n !== song()) loadMixerSong(n); });
    box.querySelectorAll("[data-band]").forEach(b => b.onclick = () => { fq.pick = b.dataset.band; findList(); });
    box.querySelectorAll("[data-back]").forEach(b => b.onclick = () => { fq.pick = ""; findList(); });
  }

  // ---- in place: Steems' top, and the whole screen (its bar gets the finder and the chord switch)
  function mount(){
    const nm = document.querySelector("#mixer .nm");
    if(nm) mountDeck((box) => nm.insertBefore(box, nm.firstChild), "steems");
    const stg = document.getElementById("nm-stage"), bar = stg && stg.querySelector(".nm-stage-bar");
    if(stg && bar){
      mountDeck((box) => bar.after(box), "stage");
      chordBox = document.createElement("div");
      chordBox.className = "dk-chords";
      chordBox.innerHTML = `<div class="dk-bars"></div><div class="dk-cards"></div>`;
      stg.querySelector(".dk.stage").after(chordBox);
      const sp = bar.querySelector(".sp");
      const find = document.createElement("button");
      find.type = "button"; find.id = "dk-find"; find.innerHTML = `${icon("search", 14)}<span>Písně</span>`;
      find.title = "Najít jinou píseň ve Steems podle názvu nebo kapely — načte se a zůstaneš tady";
      find.onclick = (e) => openFind(e.currentTarget);
      const cb = document.createElement("button");
      cb.type = "button"; cb.id = "dk-chords-btn"; cb.innerHTML = `${icon("guitar", 14)}<span>Akordy</span>`;
      cb.title = "Pomůcka: takty s dobami a akordy, a jak akordy držet na kytaře a na klavíru";
      cb.onclick = () => { cfg.chords = !cfg.chords; keep(); paintChordBtn(); paint(true); };
      const cv = document.createElement("select");
      cv.id = "dk-chord-view"; cv.title = "Jak akordy ukazovat";
      cv.innerHTML = `<option value="both">kytara i klavír</option><option value="guitar">kytara</option><option value="piano">klavír</option>`;
      cv.onchange = () => { cfg.chordView = cv.value; keep(); chordSig = ""; paint(true); };
      bar.insertBefore(find, sp); bar.insertBefore(cb, sp); bar.insertBefore(cv, sp);
      paintChordBtn();
    }
    D().onFlow(flow);
  }
  function paintChordBtn(){
    const b = document.getElementById("dk-chords-btn"), v = document.getElementById("dk-chord-view");
    if(b) b.classList.toggle("on", cfg.chords);
    if(v){ v.value = cfg.chordView; v.hidden = !cfg.chords; }
  }

  // ---- kept up to date while shown; a song picked on the whole screen is brought into it when in
  let lastSong = null, lastReady = false;
  setInterval(() => {
    if(typeof Mixer === "undefined" || !Mixer.deck) return;
    const nm = song(), ready = Mixer.ready();
    if(nm !== lastSong || ready !== lastReady){
      if(nm !== lastSong){ at = -1; allow = -1; pass = 1; chordSig = barSig = ""; }
      lastSong = nm; lastReady = ready;
      if(ready && D().stageOn()) D().restage();
    }
    paint();
  }, 120);

  mount();
  return {run, go, jump, toggleLoop, parseChord, shapeOf, cfg: () => cfg, partOf, setPart, flow, paint};
})();
