// GuitarStamps (Tools → GuitarStamps): everything about the player's guitar and the tone prints on
// one page — the way through it, the guitar recorded clean (DI) and heard, the library of prints,
// trying them with the songs they came from, and the NAM cores and cabinets they are made of.
// The guitar and the library are the Tónový otisk panels (static/tone.js), mounted into the page.
const GuitarStamps = (() => {
  const TAB = "pick-stamps-tab";
  const TABS = [["guide", "Postup"], ["guitar", "Moje kytara"], ["library", "Knihovna otisků"], ["test", "Zkouška se songy"], ["amps", "NAM a IR"]];
  let box = null, tab = "guide", info = null, map = null, prints = null, songs = null, songQ = "", mapTimer = 0, noOriginal = true;
  try{ tab = localStorage.getItem(TAB) || "guide"; }catch(e){}
  try{ noOriginal = localStorage.getItem(TAB + "-mute") !== "0"; }catch(e){}

  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const ic = (n, s = 14) => (typeof icon === "function" ? icon(n, s) : "");
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const enc = encodeURIComponent;
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const num = (n) => Number(n || 0).toLocaleString("cs-CZ");
  const ago = (t) => { if(!t) return ""; const d = (Date.now() / 1000 - t) / 86400; return d < 1 ? "dnes" : d < 2 ? "včera" : `před ${Math.round(d)} dny`; };

  // ------------------------------------------------------------- the page
  function show(container){
    box = container;
    box.innerHTML = `<div class="gs-page">
      <header class="gs-hero"><div class="gs-hero-t"><span class="gs-logo">${ic("guitar", 22)}</span>
        <div><h1>GuitarStamps</h1><p>Tvoje kytara, její otisky a zesilovače, ze kterých vznikají — na jednom místě.</p></div></div>
        <div class="gs-stats"></div></header>
      <nav class="gs-tabs">${TABS.map(([k, l]) => `<button type="button" data-tab="${k}">${l}</button>`).join("")}</nav>
      <div class="gs-body"></div></div>`;
    box.querySelectorAll(".gs-tabs [data-tab]").forEach(b => b.onclick = () => go(b.dataset.tab));
    go(tab);
    load();
  }
  function hide(){
    clearTimeout(mapTimer);
    if(typeof Tone !== "undefined" && Tone.unmount) Tone.unmount();
    stopPreview();
  }
  async function load(){
    try{ info = await api("/api/stamps"); }catch(e){ info = {error: e.message}; }
    paintStats();
    if(tab === "guide" || tab === "amps") paintTab();
  }
  function go(t){
    tab = TABS.some(x => x[0] === t) ? t : "guide";
    try{ localStorage.setItem(TAB, tab); }catch(e){}
    if(!box) return;
    box.querySelectorAll(".gs-tabs [data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
    if(typeof Tone !== "undefined" && Tone.unmount && tab !== "guitar" && tab !== "library") Tone.unmount();
    stopPreview();
    paintTab();
  }
  function paintStats(){
    const s = box && box.querySelector(".gs-stats"); if(!s || !info || info.error) return;
    const g = info.guitar;
    const tile = (label, big, sub, to) => `<button type="button" class="gs-stat" data-to="${to}"><span>${label}</span><b>${big}</b><small>${sub}</small></button>`;
    s.innerHTML = tile("Kytara", g ? esc(g.name) : "—", g ? (g.ready ? "kalibrovaná ✓" : "nevyhodnocená") : "zatím žádná", "guitar")
      + tile("Otisky", num(info.prints), `${num(info.songs)} písní${info.review ? ` · ${info.review} ke kontrole` : ""}`, info.review ? "library" : "test")
      + tile("NAM jádra", num(info.cores), info.groups ? `${num(info.groups)} různých zvuků` : "zatím nezmapováno", "amps")
      + tile("Bedny (IR)", num((info.catalog || {}).irs), "v katalogu", "amps");
    s.querySelectorAll("[data-to]").forEach(b => b.onclick = () => go(b.dataset.to));
  }
  function body(){ return box.querySelector(".gs-body"); }
  function paintTab(){
    if(!box) return;
    const b = body();
    if(tab === "guitar" || tab === "library"){
      b.innerHTML = `<div class="gs-dock"></div>`;
      if(typeof Tone !== "undefined" && Tone.mount) Tone.mount(b.querySelector(".gs-dock"), tab);
      else b.innerHTML = `<p class="gs-note">Tónový otisk se nenačetl.</p>`;
      return;
    }
    if(tab === "test") return paintTest(b);
    if(tab === "amps") return paintAmps(b);
    paintGuide(b);
  }

  // ------------------------------------------------------------- Postup: the way through, with where one is
  function paintGuide(b){
    const g = info && info.guitar, live = typeof Mixer !== "undefined" && Mixer.guitar && Mixer.guitar.state && Mixer.guitar.state.live;
    const steps = [
      {done: !!live, t: "Zapoj kytaru", d: "Kytara do zvukovky (iRig), na kanálu Live kytara zapni vstup. Pro nahrávání má jít čistý signál (DI) — bez pedálů, s rozumným GAIN.", to: null,
       act: live ? "" : `<button type="button" class="btn secondary" data-do="input">Zapnout vstup kytary</button>`},
      {done: !!(g && g.ready), t: "Nahraj a vyhodnoť kytaru", d: "Čtyři krátké nahrávky čisté kytary: struny, tóny po hmatníku, akordy, riff dusaně i otevřeně, jemně i naplno. Pick z nich udělá knihovnu tvých tónů (analýza basic-pitch) a pozná ladění a sílu úhozu.", to: "guitar"},
      {done: !!(info && info.groups), t: "Zmapuj zesilovače", d: "Každý NAM model se jednou poslechne a podobně znějící (stejný aparát, jiný mikrofon) se seskupí. Otisk pak hledá rychleji a mezi opravdu různými zvuky.", to: "amps"},
      {done: !!(info && info.prints), t: "Udělej otisky", d: "U jedné písně: Steems → píseň → OTISK. Hromadně: v Moje kytara naplánuj otisky na noc — Pick vybere úsek, kde kytara hraje nejzřetelněji.", to: "guitar"},
      {done: !!(info && info.prints && !info.review), t: "Poslechni a zkoušej", d: "Otisky udělané v noci najdeš v Knihovně pod Ke kontrole. V Zkoušce se songy si zahraješ s písní přes otisk — s kytarou originálu nebo místo ní.", to: info && info.review ? "library" : "test"},
    ];
    b.innerHTML = `<div class="gs-guide">${steps.map((s, i) => `<div class="gs-step ${s.done ? "done" : ""}">
        <div class="gs-n">${s.done ? "✓" : i + 1}</div><div class="gs-st"><b>${s.t}</b><p>${s.d}</p>
        <div class="gs-acts">${s.act || ""}${s.to ? `<button type="button" class="btn ${s.done ? "secondary" : ""}" data-go="${s.to}">${s.done ? "Otevřít" : "Pustit se do toho"}</button>` : ""}</div></div></div>`).join("")}</div>
      ${info && info.error ? `<p class="gs-warn">${esc(info.error)}</p>` : ""}
      ${info && !info.ready ? `<p class="gs-warn">Chybí engine otisku (prostředí pick-tone nebo Node) — otisky ani mapování teď nepoběží.</p>` : ""}`;
    b.querySelectorAll("[data-go]").forEach(x => x.onclick = () => go(x.dataset.go));
    const inp = b.querySelector('[data-do="input"]');
    if(inp) inp.onclick = async () => { try{ await Mixer.guitar.start(); }catch(e){ say("Vstup kytary se nepodařilo zapnout: " + e.message); } paintTab(); };
  }

  // ------------------------------------------------------------- Zkouška se songy
  let audio = null;
  function stopPreview(){ if(audio){ audio.pause(); audio = null; } if(box) box.querySelectorAll(".gs-pv.on").forEach(x => x.classList.remove("on")); }
  function preview(song, pid, which, btn){
    const was = btn.classList.contains("on");
    stopPreview();
    if(was) return;
    audio = new Audio(`/api/tone/${enc(song)}/preview/${which}?print=${enc(pid)}`);
    btn.classList.add("on");
    audio.onended = () => btn.classList.remove("on");
    audio.play().catch(() => { btn.classList.remove("on"); say("Ukázka se nepřehrála"); });
  }
  async function playWith(song, pid){
    stopPreview();
    try{
      setMode("steems");
      if(Mixer.currentSong() !== song) loadMixerSong(song);
      for(let i = 0; i < 120 && !(Mixer.currentSong() === song && Mixer.ready()); i++) await wait(150);
      if(pid) await Tone.apply(song, pid);
      if(noOriginal && Mixer.voice && Mixer.voice.guitars) Mixer.voice.guitars(false);
      if(Mixer.guitar && Mixer.guitar.state && !Mixer.guitar.state.live) say("Otisk je zapnutý — uslyšíš ho, až zapneš vstup kytary");
    }catch(e){ say(e.message || "Nepodařilo se"); }
  }
  async function paintTest(b){
    b.innerHTML = `<p class="gs-note"><span class="spinner"></span> Načítám otisky…</p>`;
    try{ prints = await api("/api/tone/prints"); }catch(e){ prints = []; }
    if(!songs){
      try{ songs = (typeof steemsSongs !== "undefined" && steemsSongs.length ? steemsSongs : await api("/api/stems")).filter(x => (x.stems || []).includes("guitar")); }
      catch(e){ songs = []; }
    }
    if(tab !== "test") return;
    const withPrint = new Set(prints.map(p => p.song));
    const ring = (m) => `<span class="gs-ring" style="--m:${Math.max(0, Math.min(100, m || 0))}"><b>${m == null ? "–" : m + "%"}</b></span>`;
    b.innerHTML = `<div class="gs-testbar"><label class="gs-chk"><input type="checkbox" class="gs-mute" ${noOriginal ? "checked" : ""}> Hrát místo kytary originálu (její stopy se ztlumí)</label>
        <span class="gs-hint">${ic("headphones", 13)} ukázka = tvoje kytara přes otisk · ${ic("disc", 13)} = kytara originálu</span></div>
      <h3 class="gs-h">Otisky se svými písněmi <small>${prints.length}</small></h3>
      ${prints.length ? `<div class="gs-tiles">${prints.map(p => `<div class="gs-tile ${p.review === "pending" ? "rev" : ""}" data-song="${esc(p.song)}" data-pid="${esc(p.id)}">
          <div class="gs-art"></div>
          <div class="gs-tt"><b title="${esc(p.name)}">${esc(p.name)}</b><span>${esc(p.artist ? p.artist + " · " + p.title : p.title)}</span>
            <small>${esc(p.core || "bez jádra")}${p.review === "pending" ? ` · <i>ke kontrole</i>` : ""}</small></div>${ring(p.match)}
          <div class="gs-ta">
            <button type="button" class="gs-ib gs-pv" data-pv="you" title="Ukázka: tvoje kytara přes otisk">${ic("headphones", 15)}</button>
            <button type="button" class="gs-ib gs-pv" data-pv="record" title="Ukázka: kytara originálu">${ic("disc", 15)}</button>
            <button type="button" class="btn gs-play" title="Otevře píseň v mixu a zapne otisk na tvé kytaře">${ic("guitar", 13)} Hrát s písní</button></div></div>`).join("")}</div>`
        : `<p class="gs-note">Zatím žádný otisk. Začni v záložce Postup.</p>`}
      <h3 class="gs-h">Písně s kytarou bez otisku <small>${songs.filter(x => !withPrint.has(x.name)).length}</small></h3>
      <input type="search" class="gs-find" placeholder="Hledat píseň nebo kapelu…" value="${esc(songQ)}">
      <div class="gs-songs"></div>`;
    const paintSongs = () => {
      const q = (typeof norm === "function" ? norm(songQ) : songQ.toLowerCase());
      const left = songs.filter(x => !withPrint.has(x.name)).filter(x => {
        if(!q) return true;
        const m = x.meta || {}, t = `${m.artist || ""} ${m.title || ""} ${x.name}`;
        return (typeof norm === "function" ? norm(t) : t.toLowerCase()).includes(q);
      });
      const list = b.querySelector(".gs-songs");
      list.innerHTML = left.slice(0, 40).map(x => { const m = x.meta || {};
        return `<div class="gs-song" data-song="${esc(x.name)}"><div class="gs-art sm"></div><div class="gs-tt"><b>${esc(m.title || x.name)}</b><span>${esc(m.artist || "")}</span></div>
          <button type="button" class="btn secondary gs-open" title="Otevře píseň v mixu a okno otisku">Udělat otisk</button></div>`; }).join("")
        + (left.length > 40 ? `<p class="gs-note">… a dalších ${left.length - 40}. Zúži hledání, nebo je naplánuj hromadně v Moje kytara.</p>` : "");
      list.querySelectorAll(".gs-song").forEach(r => {
        if(typeof StemsArt !== "undefined") StemsArt.paint(r.querySelector(".gs-art"), r.dataset.song);
        r.querySelector(".gs-open").onclick = async () => {
          await playWith(r.dataset.song, null);
          Tone.open("song");
        };
      });
    };
    paintSongs();
    const f = b.querySelector(".gs-find");
    f.oninput = () => { songQ = f.value; paintSongs(); };
    b.querySelector(".gs-mute").onchange = (e) => { noOriginal = e.target.checked; try{ localStorage.setItem(TAB + "-mute", noOriginal ? "1" : "0"); }catch(x){} };
    b.querySelectorAll(".gs-tile").forEach(t => {
      if(typeof StemsArt !== "undefined") StemsArt.paint(t.querySelector(".gs-art"), t.dataset.song);
      t.querySelectorAll("[data-pv]").forEach(x => x.onclick = () => preview(t.dataset.song, t.dataset.pid, x.dataset.pv, x));
      t.querySelector(".gs-play").onclick = () => playWith(t.dataset.song, t.dataset.pid);
    });
  }

  // ------------------------------------------------------------- NAM a IR: the catalogue and the map of the cores
  async function paintAmps(b){
    if(!map){ try{ map = await api("/api/stamps/map"); }catch(e){ map = {map: null, job: null}; } }
    if(tab !== "amps") return;
    const cat = (info && info.catalog) || {}, m = map.map, job = map.job && map.job.status === "running" ? map.job : null;
    const top = Math.max(1, ...(cat.brands || []).map(x => x[1]));
    b.innerHTML = `<div class="gs-cols">
      <section class="gs-card"><h3>Katalog</h3>
        <div class="gs-nums"><div><b>${num(cat.models)}</b><span>NAM profilů</span></div><div><b>${num(cat.irs)}</b><span>beden (IR)</span></div></div>
        <div class="gs-bars">${(cat.brands || []).map(([k, v]) => `<div class="gs-bar"><span>${esc(k)}</span><i style="width:${Math.round(v / top * 100)}%"></i><b>${v}</b></div>`).join("")}</div>
        <div class="gs-chips">${(cat.types || []).map(([k, v]) => `<span>${esc(k)} <small>${v}</small></span>`).join("")}</div>
        <div class="gs-acts"><button type="button" class="btn" data-pick="model">Vybrat NAM pro živou kytaru</button><button type="button" class="btn secondary" data-pick="ir">Vybrat bednu (IR)</button></div>
        <p class="gs-note">Otisk si bednu (barvu) spočítá sám jako vlastní IR z nahrávky originálu. Tvoje IR soubory použiješ, když hraješ s vlastním NAM bez otisku — hlava bez bedny dostane IR automaticky.</p></section>
      <section class="gs-card"><h3>Mapa jader otisku</h3>
        <p class="gs-note">Každý NAM model se jednou poslechne na stejném riffu (zkreslení, jas, sustain…). Modely, které po srovnání barvy zní skoro stejně — jeden aparát nahraný deseti mikrofony — se zkoušejí jako jeden. Mapování jednou trvá déle (podle počtu modelů), pak už jen u nových.</p>
        ${job ? `<div class="gs-prog"><span class="spinner"></span> Poslouchám jádra ${job.done || 0} / ${job.of || "…"}<i style="width:${job.of ? Math.round(job.done / job.of * 100) : 3}%"></i>
            <button type="button" class="btn secondary" data-map="stop">Zastavit</button></div>`
          : `<div class="gs-acts"><button type="button" class="btn" data-map="start">${m ? "Přemapovat (jen nové modely)" : "Zmapovat jádra"}</button>
            ${m ? `<span class="gs-hint">naposledy ${ago(m.at)}</span>` : ""}</div>`}
        ${map.job && map.job.status === "error" ? `<p class="gs-warn">${esc(map.job.error || "Mapování se nepovedlo.")}</p>` : ""}
        ${m ? `<div class="gs-nums"><div><b>${num(m.known)}</b><span>modelů</span></div><div><b>${num((m.chosen || []).length)}</b><span>různých zvuků</span></div><div><b>${num((m.bad || []).length)}</b><span>nehraje</span></div></div>
          <input type="search" class="gs-find gs-cfind" placeholder="Hledat v jádrech…">
          <div class="gs-cores"></div>` : ""}</section></div>`;
    b.querySelectorAll("[data-pick]").forEach(x => x.onclick = () => { if(Mixer.ampPicker) Mixer.ampPicker(x.dataset.pick); });
    const st = b.querySelector('[data-map="start"]');
    if(st) st.onclick = async () => { try{ map = await api("/api/stamps/map", {method: "POST"}); }catch(e){ say(e.message); } paintAmps(b); follow(b); };
    const sp = b.querySelector('[data-map="stop"]');
    if(sp) sp.onclick = async () => { try{ map = await api("/api/stamps/map", {method: "DELETE"}); }catch(e){} paintAmps(b); };
    if(m){
      const list = b.querySelector(".gs-cores"), f = b.querySelector(".gs-cfind");
      const paintCores = () => {
        const q = f.value.trim().toLowerCase();
        const rows = (m.chosen || []).filter(c => !q || c.name.toLowerCase().includes(q) || (c.twins || []).some(t => t.toLowerCase().includes(q)))
          .sort((a, c) => (c.twins || []).length - (a.twins || []).length);
        list.innerHTML = rows.slice(0, 200).map(c => `<details class="gs-core"><summary><b>${esc(c.name)}</b>${(c.twins || []).length ? `<small>+ ${c.twins.length} podobných</small>` : ""}</summary>
          ${(c.twins || []).length ? `<ul>${c.twins.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}</details>`).join("")
          + (rows.length > 200 ? `<p class="gs-note">… a dalších ${rows.length - 200}</p>` : "");
      };
      f.oninput = paintCores;
      paintCores();
    }
    if(job) follow(b);
  }
  function follow(b){
    clearTimeout(mapTimer);
    mapTimer = setTimeout(async () => {
      if(tab !== "amps" || !box || !box.isConnected) return;
      try{ map = await api("/api/stamps/map"); }catch(e){ return; }
      if(map.job && map.job.status === "running") follow(b);
      else load();
      paintAmps(b);
    }, 2500);
  }

  return {show, hide, go};
})();
