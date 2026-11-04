// Pick Live: the song the player below plays, on the Live Show stage — one click.
// Separated already: it opens in the mix and the stage goes up. Not yet: the server
// gets it ready first (/api/live/prepare): downloaded, separated, its text, tab, tuning,
// sections and chords, and a print of its guitar begun once the player's own guitar is
// recorded in GuitarStamps. A card up top tells how far it is; when it is ready the
// stage opens by itself. The key and the tempo the mix reads off the stems as it loads.
const PickLive = (() => {
  const KEY = "pick-live-prep";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const ic = (n, s) => (typeof icon === "function" ? icon(n, s) : "");
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const post = (url, body) => fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
    .then(async r => { const d = await r.json().catch(() => ({})); if(!r.ok && !d.error) d.error = "Server odpověděl chybou."; return d; });
  const one = (t) => ({title: t.title || "", artists: t.artists || "", videoId: t.videoId || "", url: t.url || "",
    relPath: t.relPath || "", duration: t.duration_seconds || 0});
  let timer = 0, busy = false;

  // ---- the card up top ----
  function card(){
    let c = document.getElementById("pl-card");
    if(!c){
      c = document.createElement("div");
      c.id = "pl-card";
      c.className = "pl-card";
      c.innerHTML = `<div class="pl-c-head">${ic("flame", 15)}<b>Pick Live</b><span class="pl-c-song"></span>
          <button type="button" class="pl-c-x" title="Schovat (příprava běží dál)">${ic("close", 12)}</button></div>
        <div class="pl-c-steps"></div><div class="pl-c-bar"><i></i></div><div class="pl-c-msg"></div><div class="pl-c-acts"></div>`;
      document.body.appendChild(c);
      c.querySelector(".pl-c-x").onclick = () => { c.hidden = true; };
    }
    const host = document.fullscreenElement || document.body;      // over the stage, full screen too
    if(c.parentElement !== host) host.appendChild(c);
    c.hidden = false;
    return c;
  }
  const STEPS = [["looking", "Text a tab"], ["downloading", "Stažení"], ["separating", "Stopy"], ["analyzing", "Ladění, sekce, akordy, otisk"], ["ready", "Live"]];
  function paint(song, state, msg, pct, acts){
    const c = card();
    c.querySelector(".pl-c-song").textContent = song || "";
    const at = STEPS.findIndex(s => s[0] === state);
    c.querySelector(".pl-c-steps").innerHTML = state === "error" || state === "find" ? "" : STEPS.map(([k, label], i) =>
      `<span class="${i < at || state === "ready" ? "done" : i === at ? "now" : ""}">${i < at || state === "ready" ? ic("check", 10) : ""}${esc(label)}</span>`).join("");
    const bar = c.querySelector(".pl-c-bar");
    bar.hidden = state === "error" || state === "ready";
    bar.classList.toggle("indet", pct == null);
    bar.querySelector("i").style.width = pct == null ? "" : Math.max(2, Math.min(100, pct)) + "%";
    c.querySelector(".pl-c-msg").innerHTML = msg || "";
    c.classList.toggle("err", state === "error");
    const box = c.querySelector(".pl-c-acts");
    box.innerHTML = "";
    (acts || []).forEach(([label, fn, main]) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pl-c-btn" + (main ? " main" : ""); b.textContent = label;
      b.onclick = fn; box.appendChild(b);
    });
  }
  const hide = () => { const c = document.getElementById("pl-card"); if(c) c.hidden = true; };
  // GuitarStamps → Moje kytara, the stage closed first
  const stamps = () => {
    try{ if(Mixer.deck.stageOn()) Mixer.deck.live(false); }catch(e){}
    hide();
    if(typeof GuitarStamps !== "undefined") try{ localStorage.setItem("pick-stamps-tab", "guitar"); }catch(e){}
    if(typeof setMode === "function") setMode("stamps");
    if(typeof GuitarStamps !== "undefined") GuitarStamps.go("guitar");
  };

  // What the print came to, said under the stage — and what can be done about it.
  function printNote(name, print){
    const p = String(print || "");
    if(p === "started") return ["Otisk kytary se počítá na pozadí — hotový ho najdeš v GuitarStamps → Knihovna otisků.", []];
    if(p === "no-guitar") return ["Otisk kytary se udělá, až si v GuitarStamps → Moje kytara nahraješ svou kytaru (analýza tvé DI kytary).",
      [["Nahrát kytaru", stamps, true]]];
    if(p === "none") return ["Píseň ještě nemá otisk kytary.", [["Udělat otisk", () => fit(name), true]]];
    if(p.startsWith("busy: ")) return ["Otisk kytary se teď nespustil: " + esc(p.slice(6)), [["Zkusit znovu", () => fit(name)]]];
    return ["", []];
  }
  async function fit(name){
    const d = await post(`/api/tone/${encodeURIComponent(name)}/fit`, {stem: "guitar", auto: true}).catch(() => ({error: "Nepodařilo se"}));
    const song = (document.querySelector("#pl-card .pl-c-song") || {}).textContent || name;
    if(d.error) paint(song, "ready", "Otisk kytary: " + esc(d.error), null, [["Nahrát kytaru", stamps]]);
    else paint(song, "ready", d.skipped ? "Píseň už otisk kytary má." : "Otisk kytary se počítá na pozadí — najdeš ho v GuitarStamps.", null, []);
  }

  // ---- onto the stage ----
  async function openLive(name, print){
    const a = document.getElementById("pb-audio");
    if(a && !a.paused) a.pause();                       // the original stops; the stems take over
    document.querySelectorAll("video").forEach(v => { try{ if(!v.paused) v.pause(); }catch(e){} });
    if(typeof setMode === "function") setMode("steems");
    if(Mixer.currentSong() !== name) loadMixerSong(name);
    for(let i = 0; i < 300 && !(Mixer.currentSong() === name && Mixer.ready()); i++) await wait(100);
    if(!(Mixer.currentSong() === name && Mixer.ready())) throw new Error("Píseň se ve Steems nenačetla.");
    Mixer.deck.live(true);
    const [note, acts] = printNote(name, print);
    const shown = typeof stemsDisplayByName === "function" ? stemsDisplayByName(name) : {title: name};
    if(note) paint(shown.artist ? `${shown.artist} – ${shown.title}` : shown.title, "ready", note, null, acts);
    else hide();
  }

  // ---- following a song being got ready ----
  function say(st){
    const pos = st.position > 1 ? ` (ve frontě ${st.position}.)` : "";
    switch(st.state){
      case "waiting": return ["looking", "Čeká ve frontě přípravy…", null];
      case "looking": return ["looking", "Hledám tab a text písně…", null];
      case "downloading": return ["downloading", "Stahuji píseň…", null];
      case "separating": return ["separating", st.progress != null ? `Rozděluji na stopy… ${Math.round(st.progress)} %${pos}` : "Rozděluji na stopy — pár minut" + pos, st.progress];
      case "analyzing": return ["analyzing", "Zjišťuji ladění, sekce a akordy a začínám otisk kytary…", null];
      default: return [st.state, "", null];
    }
  }
  function follow(id, song){
    clearTimeout(timer);
    try{ localStorage.setItem(KEY, JSON.stringify({id, song})); }catch(e){}
    const tick = async () => {
      let st;
      try{ const r = await fetch(`/api/live/${encodeURIComponent(id)}`); st = await r.json(); if(!r.ok) throw new Error(st.error || ""); }
      catch(e){ forget(); paint(song, "error", esc(e.message || "Přípravu jsem ztratil (Pick se restartoval?) — klikni na Pick Live znovu."), null, []); return; }
      if(st.state === "error"){ forget(); paint(song, "error", esc(st.error || "Příprava se nepovedla."), null, [["Zkusit znovu", go, true]]); return; }
      if(st.state === "ready"){
        forget();
        if(typeof invalidateRailTop5 === "function") invalidateRailTop5("steems");
        try{ steemsSongs = await (await fetch("/api/stems")).json(); steemsListAt = Date.now(); }catch(e){}
        paint(song, "ready", "Hotovo — otevírám Live…", null, []);
        try{ await openLive(st.stems, st.print); }
        catch(e){ paint(song, "error", esc(e.message), null, [["Otevřít ve Steems", () => openSteemsSong(st.stems), true]]); }
        return;
      }
      const [k, msg, pct] = say(st);
      paint(song, k, msg + `<small>Běží to na serveru — klidně dělej něco jiného, Live se otevře sám.</small>`, pct, []);
      timer = setTimeout(tick, 2000);
    };
    tick();
  }
  const forget = () => { try{ localStorage.removeItem(KEY); }catch(e){} };

  // What plays: the mix when it is the one playing, else the player below, else the mix open.
  async function go(){
    if(busy) return;
    const mixSong = Mixer.currentSong && Mixer.currentSong();
    const mixPlaying = mixSong && Mixer.deck && Mixer.deck.isPlaying();
    const t = typeof Player !== "undefined" && Player.getCurrent ? Player.getCurrent() : null;
    if(mixPlaying || (!(t && t.title) && mixSong)){
      if(typeof setMode === "function") setMode("steems");
      Mixer.deck.live(true);
      return;
    }
    if(!t || !t.title){ if(typeof showToast === "function") showToast("Nic nehraje — pusť píseň v přehrávači dole a klikni na Pick Live"); return; }
    const song = [t.artists, t.title].filter(Boolean).join(" – ");
    busy = true;
    paint(song, "find", "Hledám ji ve Steems…", null, []);
    try{
      const opts = typeof PracticeReady !== "undefined" ? PracticeReady.opts() : {};
      const d = await post("/api/live/prepare", {...one(t), ...opts});
      if(d.error){ paint(song, "error", esc(d.error), null, []); return; }
      if(d.ready){ paint(song, "ready", "Je ve Steems — otevírám Live…", null, []); await openLive(d.name, d.print); return; }
      follow(d.id, song);
    }catch(e){ paint(song, "error", esc(e.message || "Nepodařilo se"), null, []); }
    finally{ busy = false; }
  }

  // A preparation under way when the page was reloaded: followed on.
  function resume(){
    let p = null;
    try{ p = JSON.parse(localStorage.getItem(KEY) || "null"); }catch(e){}
    if(p && p.id) follow(p.id, p.song || "");
  }
  // The stage goes full screen a moment after it opens: the card follows it in, and back out.
  document.addEventListener("fullscreenchange", () => {
    const c = document.getElementById("pl-card");
    const host = document.fullscreenElement || document.body;
    if(c && c.parentElement !== host) host.appendChild(c);
  });
  ["pick-live-top", "pick-live-side"].forEach(id => { const b = document.getElementById(id); if(b) b.onclick = go; });
  setTimeout(resume, 1500);
  return {go, openLive, follow};
})();
