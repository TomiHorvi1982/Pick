// Practice: Steems playlists, and a MIDI foot switch.
//
// Setlist — songs of Steems in an order of your own, played one after another in
// the mix: when a song ends, the next one is opened with the mix it was left with
// (faders, mutes, pitch… are kept with each song) and starts by itself. In the
// left panel under Steems; "pick practice playlist <name>" starts one by voice.
//
// Pedal — a MIDI foot switch (Ampero Switch over Bluetooth, or any other) mapped
// to what the hands cannot reach while playing: a press, a double press or a hold
// of each switch, taught by pressing it (MIDI learn). Settings → MIDI pedál.
const Setlist = (() => {
  let lists = [], loaded = false, open = new Set(), busy = false;
  const ACT_KEY = "pick-setlist-active";
  let active = (() => { try{ return JSON.parse(localStorage.getItem(ACT_KEY) || "null"); }catch(e){ return null; } })();
  const keepActive = () => { try{ localStorage.setItem(ACT_KEY, JSON.stringify(active)); }catch(e){} };
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const title = (name) => { const d = stemsDisplayByName(name); return d.artist ? `${d.artist} — ${d.title}` : d.title || name; };
  const repaint = () => { if(typeof renderLibraryRail === "function") renderLibraryRail(state.mode); };
  async function load(){
    try{ lists = await api("/api/setlists"); loaded = true; }catch(e){}
    if(active && !lists.some((p) => p.id === active.id)){ active = null; keepActive(); }
    return lists;
  }
  async function store(p){
    try{ const got = await api("/api/setlists/" + p.id, json("PUT", {name: p.name, songs: p.songs})); Object.assign(p, got); }
    catch(e){ showToast(e.message); }
    repaint();
  }

  // ---- the box under Steems in the left panel
  function railBox(collapsed){
    const box = document.createElement("div");
    box.className = "sl-box";
    box.dataset.sec = "steems";
    box.hidden = collapsed;
    if(!loaded){ load().then(repaint); box.innerHTML = `<div class="sl-empty">Načítám playlisty…</div>`; return box; }
    const row = (p) => {
      const on = active && active.id === p.id, isOpen = open.has(p.id);
      return `<div class="sl-pl ${on ? "on" : ""}" data-pl="${p.id}">
        <button type="button" class="sl-name" data-open="${p.id}" title="Ukázat a upravit písně">${isOpen ? "▾" : "▸"} ${escapeHtml(p.name)}</button>
        <span class="sl-n">${on ? `${active.idx + 1}/` : ""}${p.songs.length}</span>
        <button type="button" class="sl-go" data-play="${p.id}" title="${on ? "Zastavit playlist" : "Spustit playlist od začátku"}">${on ? "■" : "▶"}</button></div>`
        + (isOpen ? `<div class="sl-songs">${p.songs.map((s, i) => `<div class="sl-song ${on && active.idx === i ? "now" : ""}">
            <button type="button" class="sl-t" data-at="${p.id}:${i}" title="Spustit playlist od téhle písně">${i + 1}. ${escapeHtml(title(s))}</button>
            <button type="button" data-up="${p.id}:${i}" title="Výš" ${i ? "" : "disabled"}>↑</button>
            <button type="button" data-down="${p.id}:${i}" title="Níž" ${i < p.songs.length - 1 ? "" : "disabled"}>↓</button>
            <button type="button" data-rm="${p.id}:${i}" title="Odebrat z playlistu">✕</button></div>`).join("")
          || `<div class="sl-empty">Zatím prázdný.</div>`}
          <div class="sl-acts"><button type="button" data-add="${p.id}">＋ Přidat písně</button>
            ${currentSteemsName ? `<button type="button" data-addcur="${p.id}" title="${escapeHtml(title(currentSteemsName))}">＋ Otevřenou</button>` : ""}
            <button type="button" data-ren="${p.id}">Přejmenovat</button><button type="button" data-del="${p.id}" class="bad">Smazat</button></div></div>` : "");
    };
    box.innerHTML = `<div class="sl-head">Playlisty Steems</div>${lists.map(row).join("")}
      <button type="button" class="sl-new">＋ Nový playlist</button>`;
    const find = (id) => lists.find((p) => p.id === id);
    const at = (v) => { const [id, i] = v.split(":"); return [find(id), +i]; };
    box.querySelector(".sl-new").onclick = async () => {
      const name = await Dialog.prompt("Název nového playlistu:", "", {placeholder: "např. Zkouška se Standou"});
      if(!name || !name.trim()) return;
      try{ const p = await api("/api/setlists", json("POST", {name, songs: currentSteemsName ? [currentSteemsName] : []})); lists.push(p); open.add(p.id); repaint(); }
      catch(e){ showToast(e.message); }
    };
    box.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => { const id = b.dataset.open; open.has(id) ? open.delete(id) : open.add(id); repaint(); });
    box.querySelectorAll("[data-play]").forEach((b) => b.onclick = () => active && active.id === b.dataset.play ? stop() : play(b.dataset.play, 0));
    box.querySelectorAll("[data-at]").forEach((b) => b.onclick = () => { const [p, i] = at(b.dataset.at); play(p.id, i); });
    box.querySelectorAll("[data-up],[data-down]").forEach((b) => b.onclick = () => {
      const [p, i] = at(b.dataset.up || b.dataset.down), j = b.dataset.up ? i - 1 : i + 1;
      [p.songs[i], p.songs[j]] = [p.songs[j], p.songs[i]];
      if(active && active.id === p.id){ if(active.idx === i) active.idx = j; else if(active.idx === j) active.idx = i; keepActive(); }
      store(p);
    });
    box.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => {
      const [p, i] = at(b.dataset.rm); p.songs.splice(i, 1);
      if(active && active.id === p.id && active.idx > i){ active.idx--; keepActive(); }
      store(p);
    });
    box.querySelectorAll("[data-addcur]").forEach((b) => b.onclick = () => { const p = find(b.dataset.addcur); p.songs.push(currentSteemsName); store(p); showToast("Přidáno: " + title(currentSteemsName)); });
    box.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => addSongs(find(b.dataset.add)));
    box.querySelectorAll("[data-ren]").forEach((b) => b.onclick = async () => {
      const p = find(b.dataset.ren), name = await Dialog.prompt("Nový název playlistu:", p.name);
      if(name && name.trim()){ p.name = name.trim(); store(p); }
    });
    box.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      const p = find(b.dataset.del);
      if(!await Dialog.confirm(`Smazat playlist „${p.name}“? Písně ve Steems zůstanou.`, {ok: "Smazat", danger: true})) return;
      try{ await api("/api/setlists/" + p.id, {method: "DELETE"}); }catch(e){}
      lists = lists.filter((x) => x.id !== p.id); if(active && active.id === p.id) stop(); repaint();
    });
    return box;
  }
  // Songs added one after another: the window stays open for the next one until closed.
  async function addSongs(p){
    if(!steemsSongs.length){ try{ steemsSongs = await (await fetch("/api/stems")).json(); }catch(e){} }
    const all = steemsSongs.map((s) => ({name: s.name, d: stemsDisplay(s)}));
    const fold = (t) => (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    await Dialog.pick({title: `Přidat do „${p.name}“`, text: "Klik přidá píseň na konec — hledání zůstane, přidej si jich kolik chceš. Hotovo skončí.",
      placeholder: "Hledat kapelu nebo píseň ve Steems…", empty: "Nic takového ve Steems není.", cancel: "Hotovo",
      search: async (q) => all.filter((x) => !q || fold(`${x.d.artist} ${x.d.title} ${x.name}`).includes(fold(q))).slice(0, 80)
        .map((x) => ({label: x.d.title || x.name, sub: [x.d.artist, p.songs.includes(x.name) ? "✓ už je v playlistu" : ""].filter(Boolean).join(" · "), value: x.name})),
      onPick: async (name, it) => { p.songs.push(name); await store(p); return `Přidáno: ${it.label} · v playlistu ${p.songs.length} písní`; }});
  }

  // ---- playing: one song after another in the mix
  let waitT = 0;
  function play(id, idx = 0){
    const p = lists.find((x) => x.id === id);
    if(!p || !p.songs.length){ showToast("Playlist je prázdný — přidej do něj písně"); return false; }
    active = {id, idx: Math.max(0, Math.min(idx, p.songs.length - 1))}; keepActive();
    openSong(p, active.idx);
    return true;
  }
  function openSong(p, idx){
    const name = p.songs[idx];
    if(!steemsSongs.some((s) => s.name === name) && steemsSongs.length){
      showToast(`${title(name)} už ve Steems není — přeskakuji`);
      return step(1);
    }
    setMode("steems", {keepSection: true});
    if(currentSteemsName !== name) loadMixerSong(name);
    showToast(`▶ ${p.name} · ${idx + 1}/${p.songs.length}: ${title(name)}`);
    repaint();
    // started once the song is in the mix with its tracks
    clearTimeout(waitT);
    const t0 = Date.now();
    const go = () => {
      if(!active || active.id !== p.id || active.idx !== idx) return;
      // all its tracks in — before that the mix still holds the last song's, and play would start that one again
      if(Mixer.currentSong() === name && (Mixer.ready ? Mixer.ready() : Mixer.channelInfo().length && Mixer.length() > 0)){ setTimeout(() => { if(Mixer.currentSong() === name) Mixer.play(); }, 600); return; }
      if(Date.now() - t0 < 60000) waitT = setTimeout(go, 400);
    };
    waitT = setTimeout(go, 400);
  }
  function step(d){
    if(!active) return false;
    const p = lists.find((x) => x.id === active.id);
    if(!p) return false;
    const n = active.idx + d;
    if(n < 0 || n >= p.songs.length){
      showToast(d > 0 ? `Playlist „${p.name}“ dohrál` : "Tohle je první píseň playlistu");
      if(d > 0) stop();
      return false;
    }
    active.idx = n; keepActive();
    openSong(p, n);
    return true;
  }
  function stop(){ active = null; keepActive(); clearTimeout(waitT); repaint(); }
  // The mix reached the end of a song: the next one, if this song is the playlist's.
  function ended(){
    if(!active) return;
    const p = lists.find((x) => x.id === active.id);
    if(!p || Mixer.currentSong() !== p.songs[active.idx]) return;
    setTimeout(() => step(1), 1500);
  }
  async function playByName(said){
    await load();                                          // made in another window, or a moment ago

    if(!lists.length){ showToast("Ve Steems zatím nemáš žádný playlist"); return; }
    const fold = (t) => (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
    let p = lists.find((x) => fold(x.name) === fold(said)) || lists.find((x) => fold(x.name).includes(fold(said)) || fold(said).includes(fold(x.name)));
    if(!p){
      try{
        const r = await (await fetch("/api/voice/best", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({query: said, names: lists.map((x) => x.name)})})).json();
        if(r.index !== null && r.index !== undefined) p = lists[r.index];
      }catch(e){}
    }
    if(!p){ showToast(`Playlist „${said}“ ve Steems nemám`); return; }
    play(p.id, 0);
  }
  load();
  return {railBox, play, stop, next: () => step(1), prev: () => step(-1), ended, playByName, active: () => active, lists: () => lists};
})();

const Pedal = (() => {
  const KEY = "pick-pedal";
  const ACTIONS = [
    ["playpause", "Přehrát / zastavit"], ["section:+", "Další sekce (smyčka)"], ["section:-", "Předchozí sekce (smyčka)"],
    ["gtrtoggle", "Originální kytary vyp / zap"], ["loop", "Smyčka zap / vyp"], ["rewind", "Na začátek (smyčky)"],
    ["setlist:+", "Další píseň playlistu"], ["setlist:-", "Předchozí píseň playlistu"], ["skip:-", "10 s zpět"], ["skip:+", "10 s vpřed"],
    ["slower", "Pomaleji (−5 %)"], ["faster", "Rychleji (+5 %)"], ["metronome", "Metronom"], ["record", "Nahrávání"],
    ["edge:start:-", "Začátek smyčky o 0,5 s dřív"], ["edge:end:+", "Konec smyčky o 0,5 s později"],
  ];
  const GESTURES = {single: "šlápnutí", double: "dvojklik", long: "podržení"};
  const LONG_MS = 600, DOUBLE_MS = 320;
  let cfg = {on: true, input: "", binds: []};
  let bt = null, btAsked = false;           // the Bluetooth MIDI devices macOS knows (/api/midi/system)
  try{ Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  let access = null, learning = null, last = "", root = null;
  const name = (a) => (ACTIONS.find((x) => x[0] === a) || [a, a])[1];

  // ---- what a MIDI message is: which switch, pressed or let go
  function parse(data){
    const [st, a, b] = data, kind = st & 0xf0, ch = (st & 0x0f) + 1;
    if(kind === 0xb0) return {key: `cc:${ch}:${a}`, label: `CC ${a} (kanál ${ch})`, down: b >= 64, up: b < 64};
    if(kind === 0x90) return {key: `note:${ch}:${a}`, label: `nota ${a} (kanál ${ch})`, down: b > 0, up: b === 0};
    if(kind === 0x80) return {key: `note:${ch}:${a}`, label: `nota ${a} (kanál ${ch})`, down: false, up: true};
    if(kind === 0xc0) return {key: `pc:${ch}:${a}`, label: `Program ${a + 1} (kanál ${ch})`, down: true, up: false, oneShot: true};
    return null;
  }
  // ---- presses into gestures, per switch
  const sw = {};
  function hit(m){
    const binds = cfg.binds.filter((x) => x.key === m.key);
    if(!binds.length) return;
    const has = (g) => binds.some((x) => x.gesture === g), oneShot = m.oneShot || binds.some((x) => x.mode === "toggle");
    const s = sw[m.key] || (sw[m.key] = {downAt: 0, taps: 0, longT: 0, tapT: 0, longDone: false});
    const fire = (g) => { const b = binds.find((x) => x.gesture === g); if(b) run(b.action, g); };
    const tap = () => {
      if(!has("double")){ fire("single"); return; }
      s.taps++;
      clearTimeout(s.tapT);
      if(s.taps >= 2){ s.taps = 0; fire("double"); return; }
      s.tapT = setTimeout(() => { s.taps = 0; fire("single"); }, DOUBLE_MS);
    };
    if(oneShot){ if(m.down || m.up) tap(); return; }        // a switch that sends one message a press: no holding
    if(m.down){
      s.downAt = performance.now(); s.longDone = false;
      clearTimeout(s.longT);
      if(has("long")) s.longT = setTimeout(() => { s.longDone = true; s.taps = 0; clearTimeout(s.tapT); fire("long"); }, LONG_MS);
      else if(!has("double")) fire("single");               // nothing else to wait for: at once, on the press
    } else if(m.up){
      clearTimeout(s.longT);
      if(s.longDone) return;
      if(has("long") || has("double")) tap();
    }
  }
  function run(action, g){
    let said = null;
    try{
      if(action === "playpause"){
        if(Mixer.currentSong()){ if(Mixer.isPlaying()){ Mixer.pause(); said = "stop"; } else { Mixer.play(); said = "hraje"; } }
        else said = Voice.run(el("pb-audio").paused ? "play" : "pause");
      }else if(action === "gtrtoggle"){
        const gs = Mixer.channelInfo().map((c) => c.stem).filter((s) => s.split("_")[0] === "guitar");
        if(!gs.length) said = "kytara tu není";
        else { const anyOn = gs.some((s) => !(Mixer.channelByStem(s) || {}).muted); Mixer.voice.guitars(!anyOn); said = anyOn ? "kytary ztlumené" : "kytary hrají"; }
      }else if(action === "setlist:+") said = Setlist.next() ? "další píseň" : null;
      else if(action === "setlist:-") said = Setlist.prev() ? "předchozí píseň" : null;
      else said = Voice.run(action);
    }catch(e){ said = null; }
    if(said) showToast(`🦶 ${said}`);
    if(root) { const n = root.querySelector("#pd-last"); if(n) n.textContent = `${last} → ${name(action)} (${GESTURES[g]})`; }
  }
  function onMsg(e){
    const m = parse(e.data);
    if(!m) return;
    last = m.label + (m.oneShot ? "" : m.down ? " ↓" : " ↑");
    if(root){ const n = root.querySelector("#pd-last"); if(n) n.textContent = last; }
    if(learning){ learnHeard(m); return; }
    if(cfg.on && (!cfg.input || e.target.name === cfg.input)) hit(m);
  }
  async function connect(quiet){
    if(access) return true;
    if(!navigator.requestMIDIAccess){ if(!quiet) showToast("Tenhle prohlížeč neumí MIDI — otevři Pick v Chrome"); return false; }
    try{ access = await navigator.requestMIDIAccess(); }catch(e){ if(!quiet) showToast("Přístup k MIDI zamítnut"); return false; }
    const hook = () => { access.inputs.forEach((i) => { if(!i._pickPedal){ i.addEventListener("midimessage", onMsg); i._pickPedal = true; } }); paint(); };
    access.onstatechange = hook; hook();
    return true;
  }
  // ---- MIDI learn: the next switch pressed is the one; whether it also says when it is let go decides if it can be held
  function learn(action, gesture){
    learning = {action, gesture, m: null, t: 0};
    paint();
  }
  function learnHeard(m){
    if(!learning.m){
      learning.m = m;
      if(m.oneShot){ finishLearn("toggle"); return; }
      // a release of the same switch within a moment: it says when it is let go (momentary), so it can be held
      learning.t = setTimeout(() => finishLearn("toggle"), 900);
      paint();
      return;
    }
    if(m.key === learning.m.key && m.up){ clearTimeout(learning.t); finishLearn("momentary"); }
  }
  function finishLearn(mode){
    const {action, gesture, m} = learning;
    learning = null;
    if(gesture === "long" && mode === "toggle"){ showToast("Tenhle spínač posílá jen jednu zprávu na šlápnutí — podržení nepozná. Přepni ho v pedálu na „momentary“, nebo vyber šlápnutí / dvojklik."); paint(); return; }
    cfg.binds = cfg.binds.filter((x) => !(x.key === m.key && x.gesture === gesture));
    cfg.binds.push({key: m.key, label: m.label, gesture, action, mode});
    keep(); paint();
    showToast(`Naučeno: ${m.label} — ${GESTURES[gesture]} → ${name(action)}`);
  }

  // ---- Settings → MIDI pedál
  function paint(){
    if(!root) return;
    if(!btAsked){
      btAsked = true;
      fetch("/api/midi/system").then((r) => (r.ok ? r.json() : null)).then((d) => { if(d){ bt = d.bluetooth || []; paint(); } }).catch(() => {});
    }
    const ins = access ? [...access.inputs.values()] : [];
    root.innerHTML = `
      <label class="pd-row"><input type="checkbox" id="pd-on" ${cfg.on ? "checked" : ""}> Pedál ovládá Pick</label>
      <div class="pd-row"><span>Vstup</span><select id="pd-in"><option value="">všechny MIDI vstupy</option>${ins.map((i) => `<option ${i.name === cfg.input ? "selected" : ""}>${escapeHtml(i.name)}</option>`).join("")}</select>
        <button type="button" class="btn secondary" id="pd-conn">${access ? "Obnovit" : "Připojit MIDI"}</button></div>
      <div class="set-note">${access ? (ins.length ? `Vidím: ${ins.map((i) => escapeHtml(i.name)).join(", ")}.` : "Žádné MIDI zařízení nevidím.") : "Pick zatím MIDI neposlouchá — klikni Připojit MIDI."}
        ${bt ? `<br>Bluetooth MIDI v macOS: ${bt.map((d) => `${escapeHtml(d.name)} ${d.online ? "✓ připojený" : "— nepřipojený"}`).join(" · ")}.` : ""}
        Bluetooth pedál zapni a klikni ${access ? "Obnovit" : "Připojit MIDI"} — Pick požádá macOS, ať ho připojí. Když nenaskočí, nesmí být spojený s jiným zařízením (telefon, tablet):
        tam ho odpoj, nebo v <b>Nastavení Audio MIDI → Okno → MIDI studio → ikona Bluetooth</b> klikni u něj na <b>Připojit</b>.
        <button type="button" class="pd-link" id="pd-setup">Otevřít Nastavení Audio MIDI</button></div>
      <div class="pd-last">Poslední zpráva: <b id="pd-last">${escapeHtml(last || "—")}</b></div>
      <div class="pd-binds">${cfg.binds.length ? cfg.binds.map((b, i) => `<div class="pd-b"><span>${escapeHtml(b.label)}</span><i>${GESTURES[b.gesture]}</i><b>${escapeHtml(name(b.action))}</b>
          <button type="button" data-x="${i}" title="Zrušit">✕</button></div>`).join("") : `<div class="set-note">Zatím nic přiřazeného. Vyber funkci a způsob, dej Naučit a šlápni na spínač.</div>`}</div>
      <div class="pd-add"><select id="pd-act">${ACTIONS.map(([k, l]) => `<option value="${k}">${escapeHtml(l)}</option>`).join("")}</select>
        <select id="pd-g">${Object.entries(GESTURES).map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>
        <button type="button" class="btn ${learning ? "secondary" : ""}" id="pd-learn">${learning ? (learning.m ? "Pusť spínač…" : "Šlápni teď…") : "Naučit"}</button></div>
      <div class="set-note">Podržení = drž déle než 0,6 s; dvojklik = dvě šlápnutí rychle po sobě. Spínač, který posílá jen jednu zprávu na šlápnutí (toggle), umí šlápnutí a dvojklik.</div>`;
    const q = (id) => root.querySelector("#" + id);
    q("pd-on").onchange = (e) => { cfg.on = e.target.checked; keep(); };
    q("pd-in").onchange = (e) => { cfg.input = e.target.value; keep(); };
    q("pd-conn").onclick = async (e) => {
      // a Bluetooth pedal switched on after a sleep: macOS is asked to connect it first (it does not by itself)
      const b = e.currentTarget;
      b.disabled = true; b.textContent = "Hledám pedál…";
      try{ const r = await fetch("/api/midi/bluetooth", {method: "POST"}); if(r.ok) bt = (await r.json()).bluetooth || bt; }catch(err){}
      if(!access && await connect()){ cfg.on = true; keep(); }
      paint();
    };
    q("pd-setup").onclick = () => fetch("/api/system/midi-setup", {method: "POST"}).catch(() => {});
    q("pd-learn").onclick = async () => {
      if(learning){ clearTimeout(learning.t); learning = null; paint(); return; }
      if(!(await connect())) return;
      const a = q("pd-act").value, g = q("pd-g").value;
      learn(a, g);
    };
    root.querySelectorAll("[data-x]").forEach((b) => b.onclick = () => { cfg.binds.splice(+b.dataset.x, 1); keep(); paint(); });
  }
  function mount(){
    const panel = document.querySelector("#settings-overlay .panel-head");
    if(!panel || document.getElementById("pd-box")) return;
    const head = document.createElement("div");
    head.className = "section-title"; head.style.marginTop = "20px";
    head.innerHTML = `<span data-icon="sliders"></span>MIDI pedál`;
    root = document.createElement("div"); root.id = "pd-box"; root.className = "pd-box";
    const before = [...panel.querySelectorAll(".section-title")].find((x) => /Kvalita stahovaných/.test(x.textContent));
    panel.insertBefore(head, before || null); panel.insertBefore(root, before || null);
    if(typeof paintStaticIcons === "function") try{ paintStaticIcons(head); }catch(e){}
    paint();
  }
  mount();
  // A pedal set up before is listened to from the start (Chrome remembers the MIDI permission).
  // listening from the start whenever the pedal is meant to work — the last message shows, learning works at once
  if(cfg.on) connect(true);
  return {connect, run, parse, actions: ACTIONS, config: () => cfg, feed: (data, from = "") => onMsg({data, target: {name: from}})};
})();
