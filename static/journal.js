// Cvičební deník — Pick writes down by itself what was practised: while a song plays in Steems, how
// long (and how much of it with the guitar plugged in), which sections and the tempo each was looped
// at; the speed trainer (static/trainer.js) and the timing measure (static/timing.js) add what they
// found. Sent to the server every minute and when the page goes (journal.py). The window shows a
// week: the time per day, the songs and their sections, what got better and what has not been played
// for a while; once a week it comes up by itself with the week before. In the left panel under Steems.
const Journal = (() => {
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const TICK = 5;                       // s
  const day = () => new Date().toLocaleDateString("sv");     // YYYY-MM-DD, the local day
  let acc = {};                         // song → {secs, guitar, sections: {name: {secs, speed}}}
  let lastTick = 0, win = null, week = 0, data = null, rail = null, railData = null;

  // ---------------------------------------------------------------- writing it down
  function sectionAt(t){
    const secs = Mixer.sectionsNow ? Mixer.sectionsNow() : [];
    const loop = Mixer.loopNow && Mixer.loopNow();
    if(loop){
      const same = secs.find(x => Math.abs(x.start - loop.start) < 0.3 && Math.abs(x.end - loop.end) < 0.3);
      if(same) return same.name;
      const mid = (loop.start + loop.end) / 2, inn = secs.find(x => x.start <= mid && x.end > mid);
      const mm = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
      return (inn ? inn.name + " " : "") + `${mm(loop.start)}–${mm(loop.end)}`;
    }
    const sec = secs.find(x => x.start <= t && x.end > t);
    return sec ? sec.name : "";
  }
  function tick(){
    const now = performance.now(), dt = lastTick ? Math.min(TICK + 2, (now - lastTick) / 1000) : 0;
    lastTick = now;
    if(!dt || typeof Mixer === "undefined" || !Mixer.currentSong || !Mixer.isPlaying || !Mixer.isPlaying()) return;
    const song = Mixer.currentSong(); if(!song) return;
    const a = acc[song] || (acc[song] = {secs: 0, guitar: 0, sections: {}});
    a.secs += dt;
    if(Mixer.guitar && Mixer.guitar.isLive && Mixer.guitar.isLive()) a.guitar += dt;
    const name = sectionAt(Mixer.currentTime());
    if(name){
      const s = a.sections[name] || (a.sections[name] = {secs: 0, speed: 0});
      s.secs += dt;
      // the tempo counts where the section was looped — played through once at full speed is listening
      if(Mixer.loopNow && Mixer.loopNow()) s.speed = Math.max(s.speed, Mixer.speed());
    }
  }
  function title(song){
    try{ const d = stemsDisplayByName(song); return d.artist ? `${d.artist} — ${d.title}` : d.title || song; }catch(e){ return song; }
  }
  function payloads(){
    const out = [];
    for(const [song, a] of Object.entries(acc)){
      if(a.secs < 1) continue;
      out.push({day: day(), song, title: title(song), secs: Math.round(a.secs), guitar: Math.round(a.guitar),
        sections: Object.entries(a.sections).map(([name, s]) => ({name, secs: Math.round(s.secs), speed: Math.round(s.speed * 100) / 100}))});
    }
    acc = {};
    return out;
  }
  async function flush(){
    for(const p of payloads()){
      try{ await fetch("/api/journal", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(p)}); }catch(e){}
    }
    railData = null;
  }
  function flushNow(){                  // the page is going: what is left, without waiting
    for(const p of payloads()){
      try{ navigator.sendBeacon("/api/journal", new Blob([JSON.stringify(p)], {type: "application/json"})); }catch(e){}
    }
  }
  async function event(song, kind, data){
    if(!song) return;
    try{ await fetch("/api/journal", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({day: day(), song, title: title(song), secs: 0, guitar: 0, sections: [], event: {kind, ...data}})}); }catch(e){}
    railData = null;
    if(win) show(week);
  }

  // ---------------------------------------------------------------- reading it
  const hm = (s) => { if(s > 0 && s < 60) return "pod 1 min"; s = Math.round(s / 60); return s < 60 ? `${s} min` : `${Math.floor(s / 60)} h${s % 60 ? ` ${s % 60} min` : ""}`; };
  const DAYS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"];
  const dmy = (iso) => { const [y, m, d] = iso.split("-"); return `${+d}. ${+m}.`; };
  async function get(w){ const r = await fetch(`/api/journal?week=${w}`); if(!r.ok) throw new Error("Deník se nepodařilo načíst"); return r.json(); }

  function css(){
    if(document.getElementById("jr-css")) return;
    const s = document.createElement("style"); s.id = "jr-css";
    s.textContent = `
      #jr-win{position:fixed;left:50%;top:70px;transform:translateX(-50%);width:min(620px,calc(100vw - 24px));max-height:calc(100vh - 100px);z-index:4000;
        display:flex;flex-direction:column;background:linear-gradient(var(--card,#191a2a),var(--card,#191a2a)),#14151d;color:var(--text,#eef0ff);
        border:1px solid var(--border,#262840);border-radius:14px;box-shadow:0 24px 70px rgba(0,0,0,.6);overflow:hidden}
      #jr-win header{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border,#262840);user-select:none}
      #jr-win header b{font-size:15px}
      #jr-win header button{background:none;border:1px solid var(--border,#262840);color:inherit;border-radius:8px;padding:3px 9px;cursor:pointer;font:inherit;font-size:13px}
      #jr-win header button:disabled{opacity:.35;cursor:default}
      #jr-win header .jr-wk{font-size:12.5px;color:var(--sub,#8f96c0);min-width:130px;text-align:center}
      #jr-win header .jr-x{margin-left:auto;border:none;font-size:20px;color:var(--sub,#8f96c0)}
      .jr-body{overflow:auto;padding:12px 16px 16px;font-size:13px}
      .jr-top{display:flex;align-items:flex-end;gap:18px;margin-bottom:12px}
      .jr-total{font-size:30px;font-weight:800;letter-spacing:-.5px;line-height:1}.jr-total small{display:block;font-size:12px;font-weight:500;color:var(--sub,#8f96c0);margin-top:4px;letter-spacing:0}
      .jr-bars{display:flex;gap:6px;align-items:flex-end;height:64px;flex:1}
      .jr-bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;height:100%;justify-content:flex-end}
      .jr-bar i{display:block;width:100%;max-width:34px;border-radius:5px 5px 2px 2px;background:linear-gradient(#8b7bff,#4ea8ff);min-height:2px}
      .jr-bar i.none{background:var(--bg2,#12131f)}
      .jr-bar span{font-size:10.5px;color:var(--sub,#8f96c0)}
      .jr-h{font-size:12px;font-weight:700;letter-spacing:.01em;color:var(--sub,#8f96c0);margin:14px 0 6px}
  /* labels in sentence case: only the first letter big */
  .jr-h::first-letter{text-transform:uppercase}
      .jr-li{display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.05)}
      .jr-li b{font-weight:600}.jr-li small{color:var(--sub,#8f96c0)}
      .jr-up{color:#3ecf8e;font-weight:700;white-space:nowrap}
      .jr-song{padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06)}
      .jr-song>div:first-child{display:flex;gap:8px;align-items:baseline}
      .jr-song button.jr-open{background:none;border:none;color:inherit;font:inherit;font-weight:600;cursor:pointer;padding:0;text-align:left}
      .jr-song button.jr-open:hover{color:var(--accent,#8b7bff)}
      .jr-song .jr-t{margin-left:auto;color:var(--sub,#8f96c0);white-space:nowrap;font-size:12px}
      .jr-secs{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
      .jr-sec{font-size:11px;padding:2px 8px;border-radius:999px;background:#22242e;color:var(--sub,#aaa)}
      .jr-sec b{color:var(--text,#eef0ff);font-weight:600}
      .jr-ev{font-size:11.5px;color:var(--sub,#8f96c0);margin-top:4px}
      .jr-empty{color:var(--sub,#8f96c0);padding:18px 0;text-align:center;line-height:1.5}
      .jr-foot{display:flex;justify-content:space-between;margin-top:14px;font-size:11.5px;color:var(--sub,#8f96c0)}
      .jr-foot button{background:none;border:none;color:var(--sub,#8f96c0);cursor:pointer;font:inherit;text-decoration:underline}
      .jr-card{position:fixed;right:18px;bottom:96px;z-index:4100;width:min(360px,calc(100vw - 32px));background:linear-gradient(var(--card,#191a2a),var(--card,#191a2a)),#14151d;
        color:var(--text,#eef0ff);border:1px solid rgba(139,123,255,.5);border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.55);padding:12px 14px;font-size:13px}
      .jr-card b{font-size:14px}.jr-card p{margin:6px 0;color:var(--sub,#aab);line-height:1.45}.jr-card p em{color:var(--text,#eef0ff);font-style:normal;font-weight:600}
      .jr-card div{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
      .jr-card button{font:inherit;font-size:12.5px;border-radius:8px;padding:5px 12px;cursor:pointer;border:1px solid var(--border,#262840);background:none;color:inherit}
      .jr-card button.go{background:var(--accent,#8b7bff);border-color:var(--accent,#8b7bff);color:#0c0c16;font-weight:700}
      .jr-rail{display:block;width:100%;text-align:left;background:none;border:none;color:inherit;font:inherit;cursor:pointer;padding:6px 10px;border-radius:8px}
      .jr-rail:hover{background:var(--hi,rgba(255,255,255,.06))}
      .jr-rail span{color:var(--sub,#8f96c0);font-size:11.5px;margin-left:6px}`;
    document.head.appendChild(s);
  }

  async function open(w = 0){
    css();
    await flush();
    if(!win){
      win = document.createElement("div"); win.id = "jr-win";
      win.innerHTML = `<header><b>📓 Cvičební deník</b><button type="button" class="jr-prev" title="Týden předtím">‹</button><span class="jr-wk"></span>
        <button type="button" class="jr-next" title="Týden potom">›</button><button type="button" class="jr-x" title="Zavřít">×</button></header><div class="jr-body"><div class="jr-empty">Načítám…</div></div>`;
      document.body.appendChild(win);
      win.querySelector(".jr-x").onclick = close;
      win.querySelector(".jr-prev").onclick = () => show(week + 1);
      win.querySelector(".jr-next").onclick = () => show(Math.max(0, week - 1));
      win.addEventListener("keydown", (e) => { if(e.key === "Escape") close(); });
    }
    show(w);
  }
  function close(){ if(win){ win.remove(); win = null; } }
  async function show(w){
    week = w;
    try{ data = await get(w); }catch(e){ say(e.message); return; }
    if(!win) return;
    win.querySelector(".jr-wk").textContent = w === 0 ? "tento týden" : w === 1 ? "minulý týden" : `${dmy(data.from)} – ${dmy(data.to)}`;
    win.querySelector(".jr-next").disabled = w === 0;
    win.querySelector(".jr-prev").disabled = !data.older;
    const body = win.querySelector(".jr-body");
    if(!data.any){ body.innerHTML = `<div class="jr-empty">Deník je zatím prázdný.<br>Pusť si píseň ve Steems a hraj — Pick sám zapíše, co a jak dlouho cvičíš,<br>na jaké tempo ses v které sekci dostal a jak ti sedí časování.</div>`; return; }
    const top = Math.max(...data.days, 1);
    const played = data.days.filter(s => s >= 30).length;
    body.innerHTML = `
      <div class="jr-top"><div class="jr-total">${hm(data.total)}<small>${played ? `${played} ${played === 1 ? "den" : played < 5 ? "dny" : "dní"} s kytarou` : "nic zahráno"}</small></div>
        <div class="jr-bars">${data.days.map((s, i) => `<div class="jr-bar" title="${DAYS[i]} ${dmy(addDays(data.from, i))}: ${hm(s)}"><i class="${s ? "" : "none"}" style="height:${Math.max(3, Math.round(s / top * 48))}px"></i><span>${DAYS[i]}</span></div>`).join("")}</div></div>
      ${data.better.length ? `<div class="jr-h">Co se zlepšilo</div>${data.better.map(b => `<div class="jr-li"><span class="jr-up">↑</span><span><b>${esc(b.what)}</b> <small>${esc(b.title)}</small></span>
          <span style="margin-left:auto" class="jr-up">${b.kind === "trainer" ? `čistě na ${b.to} %` : b.kind === "timing" ? `${b.from} → ${b.to} % včas` : `${b.from} → ${b.to} % tempa`}</span></div>`).join("")}` : ""}
      ${data.songs.length ? `<div class="jr-h">Co jsi hrál</div>${data.songs.map(songRow).join("")}` : `<div class="jr-empty">Tenhle týden zatím nic.</div>`}
      ${data.forgotten.length ? `<div class="jr-h">Dlouho jsi nehrál</div>${data.forgotten.map(f => `<div class="jr-li"><button type="button" class="jr-open" data-song="${esc(f.song)}" style="background:none;border:none;color:inherit;font:inherit;cursor:pointer;padding:0;text-align:left"><b>${esc(f.title)}</b></button>
          <small style="margin-left:auto">naposledy před ${f.ago} dny · celkem ${hm(f.secs)}</small></div>`).join("")}` : ""}
      <div class="jr-foot"><span>Pick zapisuje sám, když ve Steems hraje píseň.</span><button type="button" class="jr-clear">Smazat deník</button></div>`;
    body.querySelectorAll(".jr-open").forEach(b => b.onclick = () => openSong(b.dataset.song));
    body.querySelector(".jr-clear").onclick = clear;
  }
  function addDays(iso, n){ const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toLocaleDateString("sv"); }
  function songRow(s){
    const secs = s.sections.filter(x => x.secs >= 20 || x.trained);
    const tr = s.trainer.filter(e => e.reached).slice(-1)[0], tm = s.timing.slice(-1)[0];
    return `<div class="jr-song"><div><button type="button" class="jr-open" data-song="${esc(s.song)}">${esc(s.title)}</button>
        <span class="jr-t">${hm(s.secs)}${s.guitar >= 60 ? ` · s kytarou ${hm(s.guitar)}` : ""}</span></div>
      ${secs.length ? `<div class="jr-secs">${secs.map(x => `<span class="jr-sec">${esc(x.name)} · ${hm(x.secs)}${x.trained ? ` · <b>trenér ${x.trained} %</b>` : x.best ? ` · <b>${Math.round(x.best * 100)} %</b>` : ""}</span>`).join("")}</div>` : ""}
      ${tr || tm ? `<div class="jr-ev">${tr ? `Trenér: ${esc(tr.section)} ${tr.done ? `čistě na ${tr.to} %` : `do ${tr.reached} %`}` : ""}${tr && tm ? " · " : ""}${tm ? `Časování: ${tm.within} % tónů včas` : ""}</div>` : ""}</div>`;
  }
  async function openSong(name){
    close();
    try{
      if(typeof setMode === "function") setMode("steems");
      if(typeof openSteemsSong === "function"){ await openSteemsSong(name); return; }
      if(typeof Voice !== "undefined" && Voice.openSteems){ await Voice.openSteems(name); return; }
    }catch(e){}
    say("Píseň najdeš ve Steems: " + title(name));
  }
  async function clear(){
    if(typeof Dialog !== "undefined" && !await Dialog.confirm("Smazat celý cvičební deník? Nejde to vrátit.", {ok: "Smazat", danger: true})) return;
    try{ await fetch("/api/journal", {method: "DELETE"}); }catch(e){}
    acc = {}; railData = null; show(0); repaintRail();
  }

  // ---------------------------------------------------------------- the reminder, once a week
  async function remind(){
    let d;
    try{ d = await get(1); }catch(e){ return; }
    if(!d.remind || !d.total) return;
    css();
    const card = document.createElement("div"); card.className = "jr-card";
    const better = d.better[0], forgot = d.forgotten[0];
    card.innerHTML = `<b>📓 Tvůj minulý týden</b>
      <p>Hrál jsi <em>${hm(d.total)}</em> během ${d.days.filter(s => s >= 60).length} dní${d.songs[0] ? `, nejvíc <em>${esc(d.songs[0].title)}</em>` : ""}.</p>
      ${better ? `<p>Zlepšilo se: <em>${esc(better.what)}</em> (${esc(better.title)})${better.kind === "tempo" ? ` z ${better.from} na ${better.to} % tempa` : better.kind === "timing" ? ` — ${better.to} % tónů včas` : ` — čistě na ${better.to} %`}.</p>` : ""}
      ${forgot ? `<p>Dlouho jsi nehrál <em>${esc(forgot.title)}</em> (${forgot.ago} dní).</p>` : ""}
      <div><button type="button" class="later">Později</button><button type="button" class="go">Otevřít deník</button></div>`;
    document.body.appendChild(card);
    const done = () => { card.remove(); fetch("/api/journal/reminded", {method: "POST"}).catch(() => {}); };
    card.querySelector(".later").onclick = done;
    card.querySelector(".go").onclick = () => { done(); open(1); };
  }

  // ---------------------------------------------------------------- the left panel, under Steems
  function railBox(collapsed){
    css();
    rail = document.createElement("div");
    rail.className = "sl-box jr-railbox";
    rail.dataset.sec = "steems";
    rail.hidden = collapsed;
    fillRail();
    if(!railData) get(0).then(d => { railData = d; fillRail(); }).catch(() => {});
    return rail;
  }
  function fillRail(){
    if(!rail) return;
    rail.innerHTML = `<button type="button" class="jr-rail" title="Co a jak dlouho jsi cvičil, na jaké tempo ses dostal, co se zlepšilo">📓 Cvičební deník${railData && railData.total ? `<span>${hm(railData.total)} tento týden</span>` : ""}</button>`;
    rail.querySelector("button").onclick = () => open(0);
  }
  function repaintRail(){ if(rail && rail.isConnected) fillRail(); }

  setInterval(tick, TICK * 1000);
  setInterval(() => { flush().then(repaintRail); }, 60000);
  addEventListener("pagehide", flushNow);
  setTimeout(remind, 20000);

  return {open, close, event, railBox, flush, _tick: tick, _acc: () => acc};
})();
