// Pick Jam — the friend's page: a small Pick of his own, Steems only. He logs in (name and password from the host),
// picks songs from the host's Steems, plays, pauses, moves, sets tempo and key — for both of them, done in the
// host's Pick. His mix of the song (each track's volume, pan, mute, solo), the host's guitar and his own guitar's
// sound (a print or a NAM amp of Pick's library, or a print of his own guitar made on the host's Mac) are his
// alone. The song plays here at the moment it plays at the host's (the link's clocks). Chat, cameras, talking.
(function(){
  const K = new URLSearchParams(location.search).get("k") || "";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const mmss = (s) => { s = Math.max(0, s || 0); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };
  const store = (k, v) => { try{ if(v === undefined) return JSON.parse(localStorage.getItem("pick-jam-" + k)); localStorage.setItem("pick-jam-" + k, JSON.stringify(v)); }catch(e){ return null; } };
  const withK = (u) => K ? u + (u.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(K) : u;
  const api = {raw: (u, o) => fetch(withK(u), Object.assign({credentials: "same-origin"}, o || {}))};
  const getJ = async (u) => { const r = await api.raw(u); const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const SNAME = {drums: "Bicí", bass: "Basa", guitar: "Kytara", guitar_solo: "Kytara sólo", guitar_rhythm: "Kytara rytmus", vocals: "Zpěv", piano: "Klávesy", other: "Ostatní"};
  function toast(t, ms){ const n = document.createElement("div"); n.className = "toast"; n.textContent = t; n.onclick = () => n.remove(); document.body.appendChild(n); setTimeout(() => n.remove(), ms || 5000); }

  let gateN = null, gateOpen = 1, solo = false, mixBus = null, master = null;     // solo: the library on the PC, no host — he plays along on his own
  let me = {user: null, login: false, host: "Kamarád"}, ctx = null, link = null, chat = null, mix = null, amp = null;
  let eqNodes = [], tunA = null;
  let inStream = null, inSrc = null, inSplit = null, inSel = null, inGain = null, monGain = null, monPan = null, myOut = null, meterA = null;
  let songs = [], S = null, loaded = null, loading = null, info = null, lyrics = [], view = "lyr", syncing = false;
  const MIX = {};                               // per song: {stem: {v, p, m, s}}

  // ---------------------------------------------------------------- in: who, then the sound card
  let offline = false;
  async function boot(){
    if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    let ok = false;
    try{ const r = await api.raw("/g/me"); if(r.ok){ me = await r.json(); ok = true; } }catch(e){}
    if(!ok){ me = {user: store("user"), login: true, host: store("host") || "Kamarád", on: false}; return browse(); }
    store("host", me.host); if(me.user) store("user", me.user);
    if(me.user && (store("favq") || []).length) favSync();
    $("outBtn").hidden = !me.login;
    if(me.library && me.user) return showSolo();
    if(me.user && !me.on) return browse();
    if(me.login && !me.user){ $("login").hidden = false; $("lName").focus(); return; }
    if(!me.login && !K){ document.body.innerHTML = "<div style='padding:40px'>Tenhle odkaz je neúplný — popros kamaráda o nový.</div>"; return; }
    showJoin();
  }
  // The library (the host's PC): his own Pick to play along with the songs alone — the mix, prints, NAM — and word when the host's jam is on
  function showSolo(){
    solo = true; $("login").hidden = true; $("join").hidden = false;
    $("joinHi").innerHTML = `Ahoj <b>${esc(me.user || "")}</b>! Hraj s písněmi, zkoušej otisky a NAM zesilovače. Až ${esc(me.host)} zapne jam, dám ti vědět.`;
    $("go").textContent = "Zapnout zvuk a hrát";
    $("joinErr").innerHTML = `<a href="#" id="justBrowse">jen procházet písně</a>`;
    $("justBrowse").onclick = (e) => { e.preventDefault(); browse(true); };
    if(me.jam) $("joinErr").innerHTML = `<b>${esc(me.host)} právě hraje!</b> <a href="${esc(me.jam_url)}">Připojit se k jamu</a> · ` + $("joinErr").innerHTML;
    if($("justBrowse")) $("justBrowse").onclick = (e) => { e.preventDefault(); browse(true); };
    const eng = engineDownloads()[0];                 // ready before the jam: the native sound needs it running on this computer
    if(eng) $("joinErr").insertAdjacentHTML("beforeend", `<div class="muted" style="margin-top:8px">Pro nejmenší zpoždění v jamu: <a href="${esc(eng.url)}" download>⤓ ${esc(eng.label.replace("Stáhnout ", ""))}</a> — stáhni, dvakrát klikni a nech okno otevřené, když hrajete.</div>`);
  }
  // the host's jam came on: a note in the page, the system notification (also with the window in the background), a button
  let jamWatch = 0;
  function watchJam(){
    if(jamWatch) return;
    const show = () => { $("jamGo").hidden = !me.jam; if(me.jam) $("jamGo").href = me.jam_url; };
    show();
    jamWatch = setInterval(async () => {
      try{ const d = await (await fetch("/g/me", {cache: "no-store"})).json();
        if(d.jam && !me.jam){
          toast(`${me.host} zapnul jam — klikni „Připojit se k jamu“ nahoře`, 20000);
          if("Notification" in window && Notification.permission === "granted"){
            const n = new Notification(`${me.host} je online a hraje`, {body: "Zapnul jam — klikni a připoj se.", icon: "/static/jam-icon-192.png", tag: "pick-jam", requireInteraction: true});
            n.onclick = () => { window.focus(); location.href = d.jam_url; n.close(); }; }
        }
        me.jam = d.jam; me.jam_url = d.jam_url; show();
        if(!solo && $("songSt") && document.body.classList.contains("browse")) $("songSt").innerHTML = jamLineOf();
      }catch(e){}
    }, 15000);
  }
  const jamLineOf = () => me.jam ? `<b>${esc(me.host)} právě hraje!</b> <a href="${esc(me.jam_url)}">Připojit se k jamu</a>`
    : `${esc(me.host)} teď nehraje. Procházej písně, poslechni si je a dej jim ★ <strong>Na příště</strong> — uvidí je i ${esc(me.host)}.`;
  function showJoin(){
    $("login").hidden = true; $("join").hidden = false;
    $("joinHi").innerHTML = `${me.user && me.login ? `Ahoj <b>${esc(me.user)}</b>! ` : ""}${esc(me.host)} tě zve na společné hraní. Povol zvukovku (mikrofon) — Chrome se zeptá. Kamera není nutná.`;
    if(!me.on) $("joinErr").textContent = `${me.host} teď jam nemá zapnutý — jakmile ho zapne, připoj se.`;
  }
  $("loginF").onsubmit = async (e) => {
    e.preventDefault(); $("lErr").textContent = "";
    const r = await fetch("/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: $("lName").value, password: $("lPass").value, remember: $("lKeep").checked})});
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ $("lErr").textContent = d.error || "Nepodařilo se."; return; }
    me = await getJ("/g/me"); $("outBtn").hidden = false; store("host", me.host); store("user", me.user);
    if(me.library) return showSolo();
    if(!me.on) return browse();
    showJoin();
  };
  $("outBtn").onclick = async () => { await fetch("/logout", {method: "POST"}); location.reload(); };

  $("buf").innerHTML = `<option value="auto">automaticky</option>` + [1, 2, 3, 4, 6, 8, 12].map(q => `<option value="${q}">${q} × 2,7 ms = ${(q * 2.67).toFixed(1).replace(".", ",")} ms</option>`).join("");
  async function devices(){
    const ds = await navigator.mediaDevices.enumerateDevices();
    const fill = (sel, kind, want) => { sel.innerHTML = ds.filter(d => d.kind === kind).map(d => `<option value="${d.deviceId}">${esc(d.label || kind)}</option>`).join(""); if(want && [...sel.options].some(o => o.value === want)) sel.value = want; };
    fill($("inDev"), "audioinput", store("in")); fill($("voiceDev"), "audioinput", store("voice")); fill($("camDev"), "videoinput", store("cam"));
    if(!store("in")){ const o = [...$("inDev").options].find(o => !/macbook|built-in|vestav|iphone|airpods|default|výchozí/i.test(o.textContent)); if(o) $("inDev").value = o.value; }
    if(store("ch") != null) $("inCh").value = store("ch");
  }
  async function useGuitar(){
    if(inStream) inStream.getTracks().forEach(t => t.stop());
    [inSrc, inSplit, inSel].forEach(n => { if(n) try{ n.disconnect(); }catch(e){} });
    const id = $("inDev").value; store("in", id); store("ch", $("inCh").value);
    inStream = await navigator.mediaDevices.getUserMedia({audio: {deviceId: id ? {exact: id} : undefined, echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: {ideal: 2}, latency: {ideal: 0}}});
    inSrc = ctx.createMediaStreamSource(inStream); inSel = ctx.createGain();
    const ch = $("inCh").value;
    if(ch === "sum"){ inSrc.connect(inSel); inSel.gain.value = 0.7; }
    else { inSplit = ctx.createChannelSplitter(2); inSrc.connect(inSplit); inSplit.connect(inSel, Math.min(+ch, inSrc.channelCount - 1)); }
    inSel.connect(inGain);
  }
  function micHelp(e){
    const n = e && e.name;
    if(n === "NotAllowedError" || n === "SecurityError") return "Mikrofon je zablokovaný. 1) V Chromu klikni na ikonu vlevo v adrese → Mikrofon: <b>Povolit</b> → obnov stránku. "
      + "2) Na <b>Windows</b> ho může blokovat i systém: Nastavení → Ochrana osobních údajů a zabezpečení → <b>Mikrofon</b> → zapni „Přístup k mikrofonu“ i „Povolit desktopovým aplikacím přístup k mikrofonu“. "
      + "Na Macu: Nastavení systému → Soukromí a zabezpečení → Mikrofon → Google Chrome.";
    if(n === "NotFoundError" || n === "OverconstrainedError") return "Chrome nevidí žádný mikrofon ani zvukovku. Připoj zvukovku (nebo mikrofon) a obnov stránku; na Windows zkontroluj, že ji vidí Nastavení → Systém → Zvuk → Vstup.";
    if(n === "NotReadableError" || n === "AbortError") return "Mikrofon nebo zvukovka je obsazená jiným programem nebo ji Windows nepustí. Zavři Teams, Zoom, Discord, DAW či ovládací panel zvukovky (u ASIO ovladačů) a obnov stránku.";
    return "Mikrofon se nepodařilo otevřít (" + (n || "chyba") + "). Obnov stránku a zkus to znovu.";
  }
  function camHelp(e){
    const n = e && e.name;
    const why = n === "NotFoundError" ? "kamera nenalezena" : n === "NotReadableError" ? "kamera je obsazená jiným programem" : n === "NotAllowedError" ? "kamera je zablokovaná (Chrome nebo Windows: Ochrana osobních údajů → Kamera)" : (n || "chyba");
    return "Jedeme bez kamery — " + why + ". Hrát a mluvit jde i tak; kameru zapneš tlačítkem, až bude volná.";
  }
  async function start(){
    $("joinErr").textContent = "";
    if(!window.isSecureContext || !navigator.mediaDevices){
      $("joinErr").innerHTML = "Stránka není otevřená zabezpečeně, takže prohlížeč k mikrofonu nepustí. Otevři odkaz <b>přesně tak, jak přišel</b> (začíná <b>https://</b>), ne přes IP adresu."; return;
    }
    if(!solo){
      try{ me = await getJ("/g/me"); }catch(e){}
      if(!me.on){ $("joinErr").textContent = `${me.host} teď jam nemá zapnutý — jakmile ho zapne, zkus to znovu.`; return; }
    }
    if(solo && "Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
    $("joinErr").innerHTML = `<span class="muted">Čekám, až v Chromu povolíš mikrofon (okénko vlevo nahoře u adresy)…</span>`;
    try{ const probe = await navigator.mediaDevices.getUserMedia({audio: true}); probe.getTracks().forEach(t => t.stop()); $("joinErr").textContent = ""; }
    catch(e){ $("joinErr").innerHTML = micHelp(e); return; }
    let camOk = !solo, camNote = "";
    if(!solo){ try{ const v = await navigator.mediaDevices.getUserMedia({video: true}); v.getTracks().forEach(t => t.stop()); }
      catch(e){ camOk = false; camNote = camHelp(e); } }
    ctx = new AudioContext({latencyHint: 0});
    await devices();
    // his guitar: in → gain → the sound (clean / print / amp) → out: heard here (monitor, pan) and sent to the host
    inGain = ctx.createGain(); amp = new JamAmp(ctx, api);
    gateN = await gateNode(ctx); gateN.port.onmessage = (e) => { gateOpen = e.data; }; inGain.connect(gateN); gateN.connect(amp.input);
    myOut = ctx.createGain();
    eqNodes = EQ_DEF.map(b => { const f = ctx.createBiquadFilter(); f.type = b.type; return f; });
    amp.output.connect(eqNodes[0]); for(let i = 0; i + 1 < eqNodes.length; i++) eqNodes[i].connect(eqNodes[i + 1]); eqNodes[eqNodes.length - 1].connect(myOut);
    applyEq();
    tunA = ctx.createAnalyser(); tunA.fftSize = 4096; inGain.connect(tunA);
    monGain = ctx.createGain(); monPan = ctx.createStereoPanner(); myOut.connect(monGain); monGain.connect(monPan); monPan.connect(ctx.destination);
    meterA = ctx.createAnalyser(); meterA.fftSize = 512; inGain.connect(meterA);
    master = ctx.createGain(); master.gain.value = +(store("vol") ?? 1); master.connect(ctx.destination);   // 🔊 up top: the music and the friend
    mixBus = ctx.createGain(); mixBus.connect(master);                     // the backing as a whole (PODKLAD)
    mix = new JamMix(ctx, api, mixBus);
    if(!solo){
    link = new JamLink({role: "guest", ctx, output: master, native: store("nat") || undefined, onEvent: event});

    chat = JamChat({root: $("chat"), me: "guest", name: me.host || "Kamarád", send: (o) => link && link.send(o),
      visible: () => $("join").hidden && document.visibilityState === "visible" && document.hasFocus(),
      onUnread: (n) => { document.title = n ? `(${n}) Pick Jam — nová zpráva` : "Pick Jam"; }});
    await link.start(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${withK("/ws")}`);
    }
    try{ await useGuitar(); }
    catch(e){ $("inDev").value = ""; store("in", ""); await useGuitar(); }
    if(link) link.setInput(myOut);
    const b = store("buf"); if(b && link){ $("buf").value = b; applyBuf(); }
    if(camOk && link){ try{ showMe(await link.setCamera(true, $("camDev").value)); $("camBtn").classList.add("on"); }catch(e){} }
    $("join").hidden = true; if(chat) chat.check();
    if(solo){ document.body.classList.add("solo"); $("state").textContent = "hraješ sám s písněmi"; $("dot").classList.add("on");
      ["camBtn", "voiceBtn"].forEach(id => { $(id).disabled = true; $(id).title = "Kamera a mluvení jsou jen v jamu"; });
      $("songSt").innerHTML = "Klikni na píseň = náhled · <b>Hrát</b> nebo dvojklik = stopy do mixu, pak ▶."; watchJam(); }
    if(camNote) toast(camNote, 15000);
    applyMine(); renderRack(); restoreSound();
    loadSongs();
    setInterval(ui, 120); setInterval(sync, 400);
  }
  $("go").onclick = start;
  // ⟳: the link again from scratch and the data anew (a screen that does not come, a song that stuck loading)
  $("reBtn").onclick = async () => {
    const b = $("reBtn"); if(b.disabled) return; b.disabled = true; setTimeout(() => { b.disabled = false; }, 4000);
    if(link){ $("state").textContent = `obnovuji spojení s ${me.host}…`; if(loading && loading !== loaded) loading = null; link.reconnect(); }
    try{ const m = await getJ("/g/me"); if(m && m.host) me = {...me, ...m}; }catch(e){}
    SB.shown = 60; await loadSongs();
    toast(link ? "Spojení se obnovuje — písně načteny znovu." : "Písně načteny znovu.", 3000);
  };
  // the song list folds away: more room for the screen and the rack
  function sideFold(off){ document.querySelector("main").classList.toggle("noside", off); $("sideBtn").classList.toggle("on", !off); store("sideOff", off ? 1 : 0); }
  $("sideBtn").onclick = () => sideFold(!document.querySelector("main").classList.contains("noside"));
  sideFold(!!store("sideOff"));
  // No jam (it is off, or the host's Mac is): the songs as last seen, YouTube to listen, Na příště kept for later
  // lib: the library on the host's PC (online — previews, Na příště live; the jam itself is on the host's Mac)
  function browse(lib){
    offline = !lib;
    $("login").hidden = true; $("join").hidden = true;
    $("dot").classList.remove("on");
    const jamLine = () => me.jam ? `<b>${esc(me.host)} právě hraje!</b> <a href="${esc(me.jam_url)}">Připojit se k jamu</a>`
      : `${esc(me.host)} teď nehraje. Procházej písně, poslechni si je a dej jim ★ <strong>Na příště</strong> — uvidí je i ${esc(me.host)}.`;
    if(lib){ $("state").textContent = "knihovna písní"; $("songSt").innerHTML = jamLine(); document.body.classList.add("browse"); watchJam(); }
    else { $("state").textContent = `${me.host} teď není online — procházíš písně z minula`;
      $("songSt").innerHTML = `Až ${esc(me.host)} zapne jam, <a href="#" id="retry">zkus to znovu</a>. Písně si zatím pusť na YouTube a dej si je ★ <strong>Na příště</strong> — uvidí je i ${esc(me.host)}.`; }
    const rt = $("retry"); if(rt) rt.onclick = (e) => { e.preventDefault(); location.reload(); };
    ["camBtn", "voiceBtn", "play", "toStart", "tDn", "tUp", "kDn", "kUp"].forEach(id => { $(id).disabled = true; });
    $("rackW").hidden = true;
    loadSongs();
    if(!lib) setInterval(async () => { try{ const r = await fetch("/g/me", {cache: "no-store"}); if(r.ok && (await r.json()).on) $("songSt").innerHTML = `<b>${esc(me.host)} zapnul jam!</b> <a href="#" onclick="location.reload();return false">Připojit se</a>`; }catch(e){} }, 20000);
  }
  function showMe(track){ const v = $("vMe"); if(track){ v.srcObject = new MediaStream([track]); v.hidden = false; } else { v.srcObject = null; v.hidden = true; } }
  function applyBuf(){ const v = $("buf").value; store("buf", v); link.setBuffer(v === "auto" ? 3 : +v, v === "auto"); }

  function event(kind, v){
    if(kind === "taken"){ $("state").textContent = "Jam je otevřený v jiném okně nebo na jiném počítači — tady je odpojený. Obnov stránku, jestli chceš hrát tady."; $("dot").classList.remove("on"); if(mix) mix.stop(); toast("Jam se otevřel jinde — tady je odpojený.", 10000); return; }
    if(kind === "peer"){ $("state").textContent = v ? `${me.host} je tu — spojuji…` : `čekám na ${me.host} (má jam zapnutý?)`; if(!v && mix) mix.stop(); }
    if(kind === "conn"){ $("dot").classList.toggle("on", v === "connected"); if(v === "connected") $("state").textContent = `spojeno s ${me.host}`; if(v === "failed") $("state").textContent = "spojení se nepovedlo — zkouším znovu"; }
    if(kind === "media") showMedia(v);
    if(kind === "mode"){ $("mode").textContent = v === "low" ? "Režim: nízké zpoždění (jako SonoBus) — kytary jdou nekomprimovaně" : v === "native" ? "Režim: nativní engine — kytary jdou přímo mezi zvukovkami (Pick Jam Engine)" : "Režim: jako hovor (Opus) — stabilnější na horší síti"; natSlot(); }
    if(kind === "native-fallback") toast(JamNativeUI.why(v), 9000);
    if(kind === "native-state"){ const n = $("natSlot").querySelector(".jam-nat"); if(n && n.repaint) n.repaint(); }
    if(kind === "msg" && v && v.names && v.names.host && v.names.host !== me.host){ me.host = v.names.host; renderRack(); }
    if(kind === "msg" && v && v.type === "setup"){ onSetup(v); return; }
    if(kind === "ready" && v) sendMine();
    if(kind === "msg" && v.type === "song") onSong(v);
    else if(kind === "msg" && chat) chat.onMsg(v);
  }
  // the engine to download when it isn't running here (the gateway serves what the host has built)
  function engineDownloads(){
    const have = (me && me.engine) || [], win = /Windows/i.test(navigator.userAgent);
    return have.filter(n => win ? n.endsWith(".exe") : !n.endsWith(".exe"))
      .map(n => ({label: n.endsWith(".exe") ? "Stáhnout Pick Jam Engine pro Windows" : "Stáhnout Pick Jam Engine pro Mac", url: withK("/engine/" + n)}));
  }
  // the native engine's settings in SPOJENÍ, only while the host has that mode on
  function natSlot(){
    const slot = $("natSlot"); if(!slot || !link) return;
    if(link.mode !== "native"){ slot.innerHTML = ""; return; }
    if(slot.firstChild) return;
    slot.append(JamNativeUI.block(link, (l, h) => `<div class="nrow"><span>${l}</span><div>${h}</div></div>`, (c) => store("nat", c), {pickLabel: "zvuk ze stránky (zesilovač, EQ)", download: engineDownloads()}));
  }
  function showMedia(m){
    const big = $("vBig"), cam = $("vCam");
    const scr = m.screen && link && link.remoteShare ? m.screen : null;    // what the host says, not whether frames come (a still screen sends none)
    if(scr){ if(big.srcObject !== scr){ big.srcObject = scr; big.play().catch(() => {}); } $("pz").hidden = false; $("ztools").hidden = false; $("pickNone").hidden = true; }
    else { $("pz").hidden = true; $("ztools").hidden = true; $("pickNone").hidden = false; }
    if(m.camera && link && link.remoteCam !== false){ if(cam.srcObject !== m.camera) cam.srcObject = m.camera; cam.hidden = false; } else { cam.hidden = true; cam.srcObject = null; }
    if(m.voice && $("voice").srcObject !== m.voice) $("voice").srcObject = m.voice;
  }
  let zoom = 1;
  function setZoom(z, ax, ay){
    const pz = $("pz"), v = $("vBig"), r = pz.getBoundingClientRect();
    const fx = ax == null ? 0.5 : (ax - r.left) / r.width, fy = ay == null ? 0.5 : (ay - r.top) / r.height;
    const cx = (pz.scrollLeft + fx * r.width) / (pz.scrollWidth || 1), cy = (pz.scrollTop + fy * r.height) / (pz.scrollHeight || 1);
    zoom = Math.max(1, Math.min(4, Math.round(z * 10) / 10));
    pz.classList.toggle("zoomed", zoom > 1); v.style.width = zoom * 100 + "%";
    $("zVal").textContent = Math.round(zoom * 100) + " %";
    requestAnimationFrame(() => { pz.scrollLeft = cx * pz.scrollWidth - fx * r.width; pz.scrollTop = cy * pz.scrollHeight - fy * r.height; });
  }
  $("zIn").onclick = () => setZoom(zoom + 0.25); $("zOut").onclick = () => setZoom(zoom - 0.25); $("zFit").onclick = () => setZoom(1);
  // full screen: only the shared window, as big as the screen (the tools and the rest of the page stay out)
  $("zFull").onclick = () => { document.fullscreenElement ? document.exitFullscreen() : (setZoom(1), $("pz").requestFullscreen().catch(() => {})); };
  $("pz").addEventListener("wheel", (e) => { if(!e.ctrlKey && !e.metaKey) return; e.preventDefault(); setZoom(zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY); }, {passive: false});
  $("pz").onpointerdown = (e) => { const pz = $("pz"); if(zoom <= 1) return; pz.setPointerCapture(e.pointerId); pz.classList.add("drag");
    const x0 = e.clientX, y0 = e.clientY, l0 = pz.scrollLeft, t0 = pz.scrollTop;
    pz.onpointermove = (ev) => { pz.scrollLeft = l0 - (ev.clientX - x0); pz.scrollTop = t0 - (ev.clientY - y0); };
    pz.onpointerup = () => { pz.onpointermove = null; pz.classList.remove("drag"); }; };
  $("pz").ondblclick = (e) => { if(document.fullscreenElement) return document.exitFullscreen(); setZoom(zoom > 1 ? 1 : 2, e.clientX, e.clientY); };
  document.addEventListener("keydown", (e) => { if(view === "pick" && (e.key === "f" || e.key === "F") && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) $("zFull").click(); });

  // the mix folded away, for more of the text or the shared screen
  const foldMix = (on) => { $("rackW").classList.toggle("folded", on); $("mixFold").querySelector(".mf-ar").textContent = on ? "▴" : "▾";
    $("mixFold").title = on ? "Ukázat mix" : "Skrýt mix"; store("mixFold", on); };
  foldMix(!!store("mixFold")); $("mixFold").onclick = () => foldMix(!$("rackW").classList.contains("folded"));

  // the side panel's width, the chat's height: by hand, remembered
  function dragSize(handle, onMove){
    handle.onpointerdown = (e) => { handle.setPointerCapture(e.pointerId); const x0 = e.clientX, y0 = e.clientY, st = onMove.start();
      handle.onpointermove = (ev) => onMove.move(st, ev.clientX - x0, ev.clientY - y0);
      handle.onpointerup = () => { handle.onpointermove = null; onMove.end && onMove.end(); }; };
  }
  const side = () => parseInt(getComputedStyle(document.querySelector("main")).gridTemplateColumns) || 320;
  if(store("sideW")) document.documentElement.style.setProperty("--side-w", store("sideW") + "px");
  dragSize($("rsSide"), {start: side, move: (w0, dx) => { const w = Math.max(240, Math.min(620, w0 + dx)); document.documentElement.style.setProperty("--side-w", w + "px"); store("sideW", w); }});
  if(store("chatH")) $("chat").style.setProperty("--jc-h", store("chatH") + "px");
  dragSize($("rsChat"), {start: () => (parseInt(getComputedStyle($("chat")).getPropertyValue("--jc-h")) || 220), move: (h0, dx, dy) => {
    const h = Math.max(90, Math.min(window.innerHeight * 0.75, h0 - dy)); $("chat").style.setProperty("--jc-h", h + "px"); store("chatH", Math.round(h)); camSize(); }});
  // the camera sits in the chat and grows with it: wider with the panel, taller with the chat
  function camSize(){ const h = parseInt(getComputedStyle($("chat")).getPropertyValue("--jc-h")) || 200; $("chatCam").style.setProperty("--cam-h", Math.round(Math.max(90, h * 0.75)) + "px"); }
  camSize();

  // ---------------------------------------------------------------- the host's Steems
  // ---------------------------------------------------------------- the host's Steems: all, by band, by tuning, by playlist
  const NF = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  const nn = (m) => NF[((m % 12) + 12) % 12];
  function tuneName(t){
    if(!Array.isArray(t) || t.length < 4) return "neznámé ladění";
    const a = [...t].sort((x, y) => x - y), iv = a.slice(1).map((v, i) => v - a[i]).join(",");
    if(iv === "5,5,5,4,5") return a[0] === 40 ? "E standard" : `${nn(a[0])} standard`;
    if(iv === "7,5,5,4,5") return `Drop ${nn(a[0])}`;
    if(iv === "5,5,5,5,4,5") return `${nn(a[0])} standard · 7 strun`;
    if(iv === "7,5,5,5,4,5") return `Drop ${nn(a[0])} · 7 strun`;
    return a.map(nn).join(" ");
  }
  const SB = {cat: store("cat") || "all", open: new Set(), shown: 60, playlists: []};
  async function loadSongs(){
    try{ songs = (await getJ("/g/songs")).songs || []; store("songs", songs); }
    catch(e){ songs = store("songs") || []; if(!songs.length){
      if(me.library){ $("slist").innerHTML = `<div class="bad">Knihovna ještě načítá písně — za chvilku to zkusím znovu…</div>`; setTimeout(loadSongs, 8000); return; }
      $("slist").innerHTML = `<div class="bad">Seznam písní zatím nemám — otevři stránku, až bude ${esc(me.host)} online.</div>`; return; } }
    songs.forEach(s => { s.tune = tuneName(s.tuning); s.key = fold(s.artist + " " + s.title + " " + s.name); });
    await favSync();
    songs.sort((a, b) => fold(a.artist + a.title).localeCompare(fold(b.artist + b.title)));
    try{ SB.playlists = (await getJ("/g/playlists")).playlists || []; store("pls", SB.playlists); }catch(e){ SB.playlists = store("pls") || []; }
    document.querySelectorAll("#cats [data-c]").forEach(b => b.classList.toggle("on", b.dataset.c === SB.cat));
    paintSongs();
  }
  const art = (name) => withK(`/g/art?name=${encodeURIComponent(name)}`);
  const ytOf = (s) => s.yt || `https://www.youtube.com/results?search_query=${encodeURIComponent(((s.artist || "").split(",")[0] + " " + s.title).trim())}`;
  const rowHtml = (s) => `<button class="row ${S && S.name === s.name ? "cur" : ""} ${PV.name === s.name ? "pv" : ""}" data-n="${esc(s.name)}" title="Klik = náhled jen pro tebe · dvojklik = hrát spolu">
      <img class="cv" loading="lazy" src="${art(s.name)}" onerror="this.removeAttribute('src')" alt=""><span class="tx"><b>${esc(s.title)}</b><span>${esc(s.artist || "")}${s.duration ? " · " + mmss(s.duration) : ""}</span></span>
      <span class="rx"><span class="tn" title="Ladění">${esc((s.tune || "").replace(" standard", " std"))}</span>
      <span class="ic"><a class="yt" href="${esc(ytOf(s))}" target="_blank" rel="noopener" title="Pustit na YouTube">▶</a><b class="fav ${FAV.set.has(s.name) ? "on" : ""}" data-fav="${esc(s.name)}" title="Na příště — uvidí to i ${esc(me.host)}">★</b></span></span></button>`;
  function groupsOf(list){
    if(SB.cat === "band"){ const m = new Map(); list.forEach(s => { const k = s.artist || "Neznámý interpret"; if(!m.has(k)) m.set(k, []); m.get(k).push(s); });
      return [...m].sort((a, b) => fold(a[0]).localeCompare(fold(b[0]))); }
    if(SB.cat === "tune"){ const m = new Map(); list.forEach(s => { if(!m.has(s.tune)) m.set(s.tune, []); m.get(s.tune).push(s); });
      return [...m].sort((a, b) => b[1].length - a[1].length); }
    if(SB.cat === "fav"){ const by = new Map(list.map(s => [s.name, s])); return [["Na příště", [...FAV.set].map(n => by.get(n)).filter(Boolean)]]; }
    if(SB.cat === "pl"){ const by = new Map(list.map(s => [s.name, s]));
      const pls = [...SB.playlists].sort((a, b) => (a.kind === "dl") - (b.kind === "dl"));      // own first, then the downloaded ones
      return pls.map(p => [(p.kind === "dl" ? "⤓ " : "♫ ") + p.name, p.songs.map(n => by.get(n)).filter(Boolean)]).filter(g => g[1].length); }
    return null;
  }
  function paintSongs(){
    const words = fold($("find").value).split(/\s+/).filter(Boolean);
    const got = words.length ? songs.filter(s => words.every(w => s.key.includes(w))) : songs;
    $("sCount").textContent = `· ${got.length}`;
    const groups = groupsOf(got), box = $("slist");
    if(!groups){
      box.innerHTML = got.slice(0, SB.shown).map(rowHtml).join("") + (got.length > SB.shown ? `<button class="more" id="more">Další písně (${got.length - SB.shown})</button>` : got.length ? "" : `<div class="muted">Nic nenalezeno.</div>`);
    } else {
      const auto = (words.length && got.length <= 40) || SB.cat === "fav";
      box.innerHTML = groups.map(([g, list]) => { const open = auto || SB.open.has(SB.cat + ":" + g);
        return `<button class="grp" data-g="${esc(g)}"><span class="ar">${open ? "▾" : "▸"}</span>${SB.cat === "band" ? `<img class="cv" loading="lazy" src="${art(list[0].name)}" onerror="this.removeAttribute('src')" alt="">` : ""}<span>${esc(g)}</span><span class="n">${list.length}</span></button>`
          + (open ? list.slice(0, 200).map(rowHtml).join("") : ""); }).join("") || `<div class="muted">Nic nenalezeno.</div>`;
    }
    box.querySelectorAll("[data-g]").forEach(b => b.onclick = () => { const k = SB.cat + ":" + b.dataset.g; SB.open.has(k) ? SB.open.delete(k) : SB.open.add(k); paintSongs(); });
    box.querySelectorAll(".row[data-n]").forEach(b => { b.onclick = (e) => { if(e.target.closest(".yt")) return; if(e.target.closest("[data-fav]")){ e.preventDefault(); favToggle(b.dataset.n); return; }
      offline ? window.open(ytOf(songs.find(x => x.name === b.dataset.n) || {}), "_blank", "noopener") : preview(b.dataset.n); }; b.ondblclick = () => { if(!offline) playTogether(b.dataset.n); }; });
    if(SB.cat === "fav" && !FAV.set.size) box.innerHTML = `<div class="muted" style="padding:10px">Zatím prázdné. Dej písním ★ — co si chcete příště zahrát. Uvidí to i ${esc(me.host)} v Picku (playlist „Jam — na příště“).</div>`;
    const more = $("more"); if(more) more.onclick = () => { SB.shown += 100; paintSongs(); };
  }
  let findT = 0;
  $("find").oninput = () => { clearTimeout(findT); findT = setTimeout(() => { SB.shown = 60; paintSongs(); }, 150); };
  document.querySelectorAll("#cats [data-c]").forEach(b => b.onclick = () => { SB.cat = b.dataset.c; store("cat", SB.cat); SB.shown = 60;
    document.querySelectorAll("#cats [data-c]").forEach(x => x.classList.toggle("on", x === b)); paintSongs(); });

  // ---- Na příště, shared with the host (his Steems playlist "Jam — na příště"); changes queued while offline
  const FAV = {set: new Set(store("fav") || [])};
  async function favSync(){
    if(offline) return;
    const q = store("favq") || [];
    try{
      const r = q.length ? await api.raw("/g/fav", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ops: q})}) : await api.raw("/g/fav");
      if(!r.ok) throw 0;
      const d = await r.json(); FAV.set = new Set(d.songs || []); store("fav", [...FAV.set]); store("favq", []);
    }catch(e){}
  }
  function favToggle(name){
    const add = !FAV.set.has(name); add ? FAV.set.add(name) : FAV.set.delete(name); store("fav", [...FAV.set]);
    const q = store("favq") || []; q.push({name, add}); store("favq", q);
    if(!offline) favSync().then(paintSongs);
    toast(add ? "★ Přidáno na příště" + (offline ? " — pošle se, až bude " + me.host + " online" : "") : "Odebráno z Na příště");
    paintSongs();
  }
  setInterval(() => { if(!offline) favSync().then(() => { if(SB.cat === "fav") paintSongs(); }); }, 30000);

  // a song heard before it is picked: only here, the whole song in one small file, to move around in
  const PV = {name: null}, pa = $("pvAudio");
  function preview(name){
    const s = songs.find(x => x.name === name); if(!s) return;
    PV.name = name; $("pvBox").hidden = false;
    $("pvCv").src = art(name); $("pvT").textContent = s.title; $("pvA2").textContent = `${s.artist || ""} · ${s.tune}`;
    $("pvGo").textContent = solo ? "Hrát s písní" : `Hrát spolu s ${me.host}`;
    pa.src = withK(`/g/preview?name=${encodeURIComponent(name)}`); pa.volume = 0.9;
    pa.play().catch(() => {}); $("pvPlay").textContent = "…";
    paintSongs();
  }
  pa.onplaying = () => { $("pvPlay").textContent = "❚❚"; }; pa.onpause = () => { $("pvPlay").textContent = "▶"; };
  pa.ontimeupdate = () => { if(pa.duration){ $("pvSeek").value = Math.round(pa.currentTime / pa.duration * 1000); $("pvTime").textContent = `${mmss(pa.currentTime)} / ${mmss(pa.duration)}`; } };
  $("pvPlay").onclick = () => { pa.paused ? pa.play().catch(() => {}) : pa.pause(); };
  $("pvSeek").oninput = () => { if(pa.duration) pa.currentTime = $("pvSeek").value / 1000 * pa.duration; };
  function playTogether(name){ pa.pause(); ctl({do: "load", name}); if(!solo) $("songSt").textContent = `${me.host} načítá píseň…`; }
  $("pvGo").onclick = () => PV.name && playTogether(PV.name);
  // in a jam the host (the admin) picks the songs and runs the playback; alone the friend plays for himself
  const ctl = (o) => { if(solo) return soloCtl(o); if(link && link.ready){ toast(`Během jamu vybírá písně a ovládá přehrávání ${me.host}.`, 3500); } };
  // alone: this page is the host — the song's place, tempo and key kept here, the mix follows them the same way
  function soloCtl(o){
    const now = performance.now();
    if(o.do === "load"){ const row = songs.find(x => x.name === o.name) || {};
      onSong({name: o.name, title: row.title, stems: row.stems || [], playing: false, pos: 0, at: now, speed: S ? S.speed || 1 : 1, key: 0, dur: row.duration || 0}); return; }
    if(!S || !S.name) return;
    const n = {...S, pos: Math.min(posNow(), S.dur || 1e9), at: now};
    if(o.do === "play"){ if(loaded !== S.name) return; if(n.dur && n.pos >= n.dur - 0.2) n.pos = 0; n.playing = true; }
    if(o.do === "pause") n.playing = false;
    if(o.do === "end"){ n.playing = false; n.pos = 0; }
    if(o.do === "seek") n.pos = Math.max(0, Math.min(o.pos, (n.dur || o.pos + 1) - 0.1));
    if(o.do === "tempo") n.speed = o.speed;
    if(o.do === "key") n.key = o.semis;
    if(o.do === "loop"){ n.loop = o.end > o.start ? {start: o.start, end: o.end} : null; if(n.loop){ n.pos = o.start; n.playing = loaded === S.name; } }
    onSong(n);
  }

  // ---------------------------------------------------------------- the song as the host's Pick has it
  function hostPos(perf){ return S.pos + ((solo ? perf : link.remoteNow(perf)) - S.at) / 1000 * (S.speed || 1); }
  function posNow(){ if(!S || (!link && !solo)) return 0; if(!S.playing || (!solo && !link.clock.samples.length)) return S.pos; return hostPos(performance.now()); }
  let stageOff = false, stageNow = null;
  function stageFollow(st){
    const on = !!(st && st.on);
    if(!on) stageOff = false;
    const show = on && !stageOff;
    $("stageIn").hidden = !(on && stageOff);
    if(show === !!stageNow && (!show || (stageNow.show === st.show && stageNow.dir === st.dir))) return;
    stageNow = show ? {show: st.show, dir: st.dir} : null;
    const box = $("stageV");
    if(show){
      box.dataset.show = st.show || "both"; box.classList.toggle("col", st.dir === "col");
      $("stLyr").appendChild($("vLyr")); $("stTab").appendChild($("vPick"));
      $("vLyr").hidden = false; $("vPick").hidden = false; box.hidden = false; document.body.classList.add("staged"); lyrIdx = -2;
    } else {
      const v = document.querySelector(".view"); v.insertBefore($("vPick"), v.firstChild); v.insertBefore($("vLyr"), v.firstChild);
      box.hidden = true; document.body.classList.remove("staged"); setView(view);
    }
  }
  $("stageOut").onclick = () => { stageOff = true; stageFollow(S && S.stage); };
  $("stageIn").onclick = () => { stageOff = false; stageFollow(S && S.stage); };
  function onSong(m){
    const was = S; S = m; stageFollow(m.stage);
    $("tempo").textContent = `tempo ${Math.round((m.speed || 1) * 100)} %`;
    $("key").textContent = `tónina ${m.key > 0 ? "+" : ""}${m.key || 0}`;
    $("play").textContent = m.playing ? "❚❚" : "▶";
    if(!m.name){ $("song").textContent = "Vyber píseň"; return; }
    if(m.name !== loaded && m.name !== loading && m.ready !== false) loadSong(m.name, m.stems || []);
    if(!was || was.name !== m.name){ const row = songs.find(s => s.name === m.name); $("song").textContent = row ? `${row.title} — ${row.artist}` : (m.title || m.name); paintSongs(); }
    if(m.ready === false && m.name !== loaded) $("songSt").textContent = `${me.host} načítá píseň…`;
    sync();
  }
  async function loadSong(name, stems){
    loading = name; mix.stop(); loaded = null;
    $("songSt").textContent = "Připravuji stopy…";
    $("rack").querySelectorAll(".strip:not([data-st^='@'])").forEach(x => x.remove());
    const [i, l] = await Promise.all([getJ(`/g/song?name=${encodeURIComponent(name)}`).catch(() => null), getJ(`/g/lyrics?name=${encodeURIComponent(name)}`).catch(() => null)]);
    if(loading !== name) return;
    info = i || {channels: [], sections: [], chords: []}; lyrics = (l && l.lines) || [];
    paintLyrics(); paintSecs();
    // the tracks: what the host sends, else his mix's channels, else what the list says the song has
    // (a whole song from the PC has one track and no mix saved — without this it loaded nothing)
    const row = songs.find(x => x.name === name), chans = (info.channels || []).map(c => c.stem).filter(Boolean);
    const want = stems.length ? stems : chans.length ? chans : (row && row.stems) || [];
    try{
      const ok = await mix.load(name, want, (d, of) => { if(loading === name) $("songSt").textContent = `Stahuji stopy ${d}/${of}…`; });
      if(!ok || loading !== name) return;
    }catch(e){ if(loading === name){ $("songSt").textContent = e.message; loading = null; } return; }
    loaded = name; loading = null;
    applyMix(); renderRack(); paintSecs();
    $("songSt").textContent = solo ? "Stopy připravené — dej ▶ a hraj." : "Stopy připravené — hraje u obou zároveň.";
    if(solo && S && S.name === name && mix.duration) S = {...S, dur: mix.duration};
    sync();
  }
  // play here when and where the host plays
  async function sync(){
    if(!S || (!link && !solo) || !mix || loaded !== S.name || syncing) return;
    if(!solo && (!link.ready || !link.clock.samples.length)) return;
    if(!S.playing){ if(mix.running) mix.stop(); return; }
    const ts = ctx.getOutputTimestamp(); if(!ts.performanceTime) return;
    const want = hostPos(ts.performanceTime), speed = S.speed || 1, key = S.key || 0, sp = S.stemPitch || {};
    if(mix.running && mix.same(speed, key, sp)){
      const mine = mix.songAt(ts.contextTime); const d = mine == null ? 1e9 : (mine - want) * 1000;
      if(Math.abs(d) < (mix.stretchy ? 30 : 15)) return;
    }
    if(want >= mix.duration){ if(solo && S.playing) soloCtl({do: "end"}); return; }
    syncing = true;
    // `want` is what is heard now; the mix starts from the audio clock's now, which runs ahead of what is heard
    const ahead = Math.max(0, ctx.currentTime - ts.contextTime) * speed;
    try{ await mix.start(Math.max(0, want + ahead), speed, key, sp); }finally{ syncing = false; }
  }

  // ---------------------------------------------------------------- his mix: each track, the host's guitar, his own
  function mixOf(name){ if(!MIX[name]) MIX[name] = store("mix-" + name) || {}; return MIX[name]; }
  function applyMix(){
    if(!info || !loaded) return;
    const mine = mixOf(loaded), host = Object.fromEntries((info.channels || []).map(c => [c.stem, c]));
    mix.ch.forEach(c => {
      const h = host[c.stem] || {}, o = mine[c.stem] || {};
      mix.set(c.stem, {v: o.v ?? (h.faderValue ?? 1), t: o.t ?? 0, p: o.p ?? (h.panValue || 0), m: o.m ?? !!h.muted, s: o.s ?? !!h.solo});
    });
  }
  // Noise gate on his guitar, before the amp (the hiss would be amplified with the rest): opens on the pick attack, closes
  // a bit lower than it opens (no chatter on a dying note), holds a moment, no lookahead (latency matters more here)
  const GATE = `class G extends AudioWorkletProcessor{
    static get parameterDescriptors(){return [{name:"threshold",defaultValue:-100,minValue:-100,maxValue:0,automationRate:"k-rate"}];}
    constructor(){super();this.env=0;this.g=1;this.hold=0;this.n=0;}
    process(ins,outs,p){const i=ins[0],o=outs[0];if(!i||!i.length){o.forEach(c=>c.fill(0));return true;}
      const th=p.threshold[0],off=th<=-99.5,open=Math.pow(10,th/20),close=open*0.6,att=Math.exp(-1/(0.002*sampleRate)),rel=Math.exp(-1/(0.12*sampleRate)),hf=0.05*sampleRate;
      for(let k=0;k<i[0].length;k++){let s=0;for(let c=0;c<i.length;c++)s+=i[c][k];const l=Math.abs(s/i.length);
        this.env=l>this.env?l:l+(this.env-l)*rel;let t;
        if(off)t=1;else if(this.env>open){t=1;this.hold=hf;}else if(this.env>close||this.hold>0){t=1;if(this.hold>0)this.hold--;}else t=0;
        this.g=t+(this.g-t)*(t>this.g?att:rel);for(let c=0;c<o.length;c++)o[c][k]=i[Math.min(c,i.length-1)][k]*this.g;}
      if((this.n+=i[0].length)>=sampleRate/20){this.n=0;this.port.postMessage(this.g);}return true;}}
    registerProcessor("jam-gate",G);`;
  async function gateNode(c){
    if(!gateNode.ok){ await c.audioWorklet.addModule(URL.createObjectURL(new Blob([GATE], {type: "text/javascript"}))); gateNode.ok = true; }
    return new AudioWorkletNode(c, "jam-gate");
  }
  const gateTxt = (d) => d <= -99 ? "vyp." : Math.round(d) + " dB";
  const MY = Object.assign({hv: 1, hp: 0, hm: false, ing: 0, mv: 1, mp: 0, mm: false, drive: 0, eqOn: false, eq: null, gate: -100, bv: 0}, store("mine") || {});
  let mineT = 0;
  function sendMine(){
    if(!link) return;
    clearTimeout(mineT);
    mineT = setTimeout(() => link && link.send({type: "mine", sound: amp ? amp.label : "Čistý zvuk",
      my: {ing: MY.ing, drive: MY.drive, gate: MY.gate, mv: MY.mm ? 0 : MY.mv, bv: MY.bv || 0}}), 250);
  }
  // the host set his sound or levels: played at once; he can change all of it afterwards
  async function onSetup(m){
    if(m.sound && typeof m.sound === "object" && ["print", "amp", "clean"].includes(m.sound.kind)){
      await useSound(m.sound, true); toast(`${me.host} ti nastavil zvuk: ${amp ? amp.label : "Čistý zvuk"}`, 5000); }
    if(m.mine && typeof m.mine === "object"){
      const lim = {ing: [-12, 24], drive: [-24, 12], gate: [-100, 0], mv: [0, 2], bv: [-24, 6]};
      Object.entries(m.mine).forEach(([k, v]) => { if(lim[k] && isFinite(+v)) MY[k] = Math.max(lim[k][0], Math.min(lim[k][1], +v)); });
      applyMine(); renderRack(); }
  }
  function applyMine(){
    if(!amp) return;
    if(link){ link.setVolume(MY.hm ? 0 : MY.hv); link.setPan(MY.hp); }
    inGain.gain.value = Math.pow(10, MY.ing / 20); amp.setInputDb(MY.ing); amp.setDrive(MY.drive);
    if(gateN) gateN.parameters.get("threshold").setTargetAtTime(MY.gate, ctx.currentTime, 0.02);
    if(mixBus) mixBus.gain.setTargetAtTime(Math.pow(10, (MY.bv || 0) / 20), ctx.currentTime, 0.02);
    sendMine();
    monGain.gain.value = MY.mm || !$("mon").checked ? 0 : MY.mv; monPan.pan.value = MY.mp;
    $("drive").value = MY.drive; $("driveV").textContent = MY.drive + " dB";
    store("mine", MY);
  }
  // ---- a console's channels: gain and pan knobs, a fader on a dB scale, a meter for each side, clip
  const TAPER = [[6, 1], [0, 0.8], [-5, 0.69], [-10, 0.58], [-15, 0.49], [-20, 0.41], [-30, 0.29], [-40, 0.19], [-60, 0.07]];
  function posOfDb(db){ if(!isFinite(db) || db <= -60) return db <= -90 || !isFinite(db) ? 0 : 0.07 * (db + 90) / 30;
    for(let i = 0; i + 1 < TAPER.length; i++){ const [a, pa_] = TAPER[i], [b, pb] = TAPER[i + 1]; if(db <= a && db >= b) return pb + (db - b) / (a - b) * (pa_ - pb); } return 1; }
  function dbOfPos(p){ if(p <= 0.005) return -Infinity; if(p < 0.07) return -90 + p / 0.07 * 30;
    for(let i = 0; i + 1 < TAPER.length; i++){ const [a, pa_] = TAPER[i], [b, pb] = TAPER[i + 1]; if(p <= pa_ && p >= pb) return b + (p - pb) / (pa_ - pb) * (a - b); } return 6; }
  const lin2db = (v) => v <= 0.00001 ? -Infinity : 20 * Math.log10(v), db2lin = (d) => isFinite(d) ? Math.pow(10, d / 20) : 0;
  const fmtDb = (d) => !isFinite(d) || d < -89 ? "−∞" : (d > 0.05 ? "+" : d < -0.05 ? "−" : "") + Math.abs(d).toFixed(1);
  const panTxt = (p) => p > 0.02 ? "R" + Math.round(p * 100) : p < -0.02 ? "L" + Math.round(-p * 100) : "C";
  const COL = {drums: "#ff6b6b", bass: "#4f8bff", guitar: "#f6c344", guitar_solo: "#ffb347", guitar_rhythm: "#e8d16a", vocals: "#e9e9ff", piano: "#4ad3a5", other: "#b28bff"};
  const SCALE = [6, 0, -5, -10, -15, -20, -30, -40, -60];
  function knobSvg(f, center){
    const a0 = -135, a = a0 + f * 270, r = 13, cx = 17, cy = 17, pt = (deg) => [cx + r * Math.sin(deg * Math.PI / 180), cy - r * Math.cos(deg * Math.PI / 180)];
    const arc = (from, to) => { const [x1, y1] = pt(from), [x2, y2] = pt(to); return `M${x1} ${y1} A${r} ${r} 0 ${Math.abs(to - from) > 180 ? 1 : 0} ${to > from ? 1 : 0} ${x2} ${y2}`; };
    const s0 = center ? 0 : a0, [hx, hy] = pt(a);
    return `<svg viewBox="0 0 34 34"><path d="${arc(-135, 135)}" stroke="#2a2a36" stroke-width="3.2" fill="none" stroke-linecap="round"/>
      ${Math.abs(a - s0) > 1 ? `<path d="${arc(Math.min(s0, a), Math.max(s0, a))}" stroke="var(--sc,#70dcd3)" stroke-width="3.2" fill="none" stroke-linecap="round"/>` : ""}
      <circle cx="17" cy="17" r="9.5" fill="#23232e" stroke="#3a3a48"/><line x1="17" y1="17" x2="${17 + (hx - 17) * 0.62}" y2="${17 + (hy - 17) * 0.62}" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>`;
  }
  // a knob: key, label, min, max, value, text, reset value, centred (pan)
  const knob = (k, label, min, max, val, txt, def, center) => `<div class="kn" data-kn="${k}" data-min="${min}" data-max="${max}" data-def="${def}" data-c="${center ? 1 : 0}" title="${label} — táhni nahoru/dolů, dvojklik = výchozí">
      ${knobSvg((val - min) / (max - min), center)}<span class="kl">${label}</span><span class="kv">${txt}</span></div>`;
  const fader = (k, v, maxDb, gate) => { const p = posOfDb(lin2db(v)) * 100, gp = gate == null ? 0 : posOfDb(gate) * 100;
    return `<div class="fb ${gate != null ? "g" : ""}"><div class="sc">${SCALE.filter(d => d <= maxDb).map(d => `<i style="bottom:calc(${posOfDb(d) * 100}% * (164 - 8) / 164 + 4px)">${d > 0 ? "+" + d : d}</i>`).join("")}</div>
      <div class="mt"><b><i></i><em></em></b><b><i></i><em></em></b>${gate != null ? `<s class="gl" style="bottom:${gp}%" ${gate <= -99 ? "hidden" : ""}></s>` : ""}</div>
      <div class="ft" data-ft="${k}" data-max="${maxDb}" title="Hlasitost — táhni, kolečko = jemně, dvojklik = 0 dB"><div class="u" style="height:calc(${p}% * (164 - 8) / 164)"></div><div class="cap" style="bottom:calc(${p}% * (164 - 8) / 164 + 4px)"></div></div>${gate != null ? `<div class="gt" title="Noise gate — práh: táhni nad úroveň šumu na měřáku (červená čára). Úplně dole = vypnuto, dvojklik = vypnout"><div class="gc" style="bottom:calc(${gp}% * (164 - 8) / 164 + 4px)"></div></div>` : ""}</div>`; };
  function renderRack(){
    const rack = $("rack"); if(!mix) return;
    const stemStrip = (c) => `<div class="strip ${MINI.has(c.stem) ? "mini" : ""}" data-st="${esc(c.stem)}" style="--sc:${COL[c.stem] || "#70dcd3"}"><div class="hd"><div class="nm" title="${esc(c.stem)}">${esc(SNAME[c.stem] || c.stem)}</div><button class="fold" title="Sbalit / rozbalit">${MINI.has(c.stem) ? "+" : "–"}</button></div><div class="sub">stopa</div>
        <div class="knobs">${knob("t", "GAIN", -12, 12, c.t || 0, fmtDb(c.t || 0) + " dB", 0)}${knob("p", "PAN", -1, 1, c.p, panTxt(c.p), 0, true)}</div>
        <div class="clip"></div>${fader("v", c.v, 6)}<div class="dbv">${fmtDb(lin2db(c.v))} dB</div>
        <div class="ms"><button class="m ${c.m ? "on" : ""}" data-k="m" title="Ztlumit (jen u tebe)">M</button><button class="s ${c.s ? "on" : ""}" data-k="s" title="Sólo (jen u tebe)">S</button></div></div>`;
    rack.innerHTML = mix.ch.map(stemStrip).join("") + (solo ? "" : `<div class="strip host ${MINI.has("@host") ? "mini" : ""}" data-st="@host" style="--sc:#6ee7a0"><div class="hd"><div class="nm">${esc(me.host)}</div><button class="fold" title="Sbalit / rozbalit">${MINI.has("@host") ? "+" : "–"}</button></div><div class="sub">jeho kytara</div>
        <div class="knobs">${knob("hp", "PAN", -1, 1, MY.hp, panTxt(MY.hp), 0, true)}</div>
        <div class="clip"></div>${fader("hv", MY.hv, 6)}<div class="dbv">${fmtDb(lin2db(MY.hv))} dB</div>
        <div class="ms"><button class="m ${MY.hm ? "on" : ""}" data-k="hm" title="Ztlumit">M</button></div></div>`) + `
      <div class="strip me ${MINI.has("@me") ? "mini" : ""}" data-st="@me" style="--sc:#a78bfa"><div class="hd"><div class="nm">${esc(me.user || "Já")}</div><button class="fold" title="Sbalit / rozbalit">${MINI.has("@me") ? "+" : "–"}</button></div><div class="sub">moje kytara</div>
        <div class="step"><button id="sPrev" title="Předchozí zvuk z naposledy použitých">◀</button><button class="snd" id="sndBtn" title="Otisk, zesilovač, vlastní otisk">🎸 ${esc(amp ? amp.label : "Čistý zvuk")}</button><button id="sNext" title="Další zvuk z naposledy použitých">▶</button></div>
        <div class="tools"><button id="tPrints" title="Katalog otisků">Otisky</button><button id="tNam" title="NAM zesilovače a kabinety">NAM</button></div>
        <div class="tools"><button id="tEq" class="${MY.eqOn ? "on" : ""}" title="Ekvalizér tvé kytary">EQ</button><button id="tTun" title="Ladička">Ladička</button></div>
        <div class="knobs">${knob("ing", "GAIN", -12, 24, MY.ing, fmtDb(MY.ing) + " dB", 0)}${knob("drive", "DRIVE", -24, 12, MY.drive, fmtDb(MY.drive), 0)}${knob("mp", "PAN", -1, 1, MY.mp, panTxt(MY.mp), 0, true)}</div>
        <div class="knobs">${knob("bv", "PODKLAD", -24, 6, MY.bv || 0, fmtDb(MY.bv || 0) + " dB", 0)}</div>
        <div class="clip"></div>${fader("mv", MY.mv, 6, MY.gate)}<div class="dbv">${fmtDb(lin2db(MY.mv))} dB</div>
        <div class="gv" title="Noise gate tvé kytary: zelená = propouští, červená = zavřeno (ticho mezi tóny)"><span class="gled"></span>GATE <b>${gateTxt(MY.gate)}</b></div>
        <div class="ms"><button class="m ${MY.mm ? "on" : ""}" data-k="mm" title="Neslyšet se">M</button></div></div>`;
    rack.querySelectorAll(".strip").forEach(wireStrip);
    rack.querySelectorAll(".strip .fold").forEach(b => b.onclick = () => { const id = b.closest(".strip").dataset.st; MINI.has(id) ? MINI.delete(id) : MINI.add(id); store("mini", [...MINI]); renderRack(); });
    const sb = $("sndBtn"); if(sb) sb.onclick = () => openSound();
    if($("tPrints")){ $("tPrints").onclick = () => openSound("prints"); $("tNam").onclick = () => openSound("amps");
      $("tEq").onclick = (e) => openEq(e.currentTarget); $("tTun").onclick = (e) => openTuner(e.currentTarget);
      $("sPrev").onclick = () => stepSound(-1); $("sNext").onclick = () => stepSound(1); }
  }
  function putVal(id, k, val){
    if(id[0] === "@"){ MY[k] = val; applyMine(); return; }
    const all = mixOf(loaded), o = all[id] = all[id] || {}; o[k] = val; store("mix-" + loaded, all); mix.set(id, {[k]: val});
  }
  const getVal = (id, k) => id[0] === "@" ? MY[k] : (mix.ch.find(x => x.stem === id) || {})[k];
  // a trackpad sends many small wheel events, a mouse few big ones: everything turns by the distance moved
  const wheelPx = (e) => e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
  function wireStrip(st){
    const id = st.dataset.st;
    st.querySelectorAll("button[data-k]").forEach(b => b.onclick = () => { putVal(id, b.dataset.k, !getVal(id, b.dataset.k)); renderRack(); });
    st.querySelectorAll(".kn").forEach(kn => {
      const k = kn.dataset.kn, min = +kn.dataset.min, max = +kn.dataset.max, def = +kn.dataset.def, center = kn.dataset.c === "1";
      const paint = (v) => { kn.querySelector("svg").outerHTML = knobSvg((v - min) / (max - min), center);
        kn.querySelector(".kv").textContent = center ? panTxt(v) : fmtDb(v) + (k === "drive" ? "" : " dB"); };
      const set = (v) => { v = Math.max(min, Math.min(max, v)); v = center ? Math.round(v * 50) / 50 : Math.round(v * 2) / 2; putVal(id, k, v); paint(v); };
      kn.onpointerdown = (e) => { kn.setPointerCapture(e.pointerId); const y0 = e.clientY, v0 = +getVal(id, k) || 0;
        kn.onpointermove = (ev) => set(v0 + (y0 - ev.clientY) / (ev.shiftKey ? 600 : 150) * (max - min)); kn.onpointerup = () => { kn.onpointermove = null; }; };
      kn.ondblclick = () => set(def);
      // the turn is kept unrounded while the fingers move, so small trackpad steps add up instead of rounding away
      kn.onwheel = (e) => { e.preventDefault(); if(kn._wv == null) kn._wv = +getVal(id, k) || 0;
        kn._wv = Math.max(min, Math.min(max, kn._wv - wheelPx(e) / (e.shiftKey ? 5000 : 1300) * (max - min))); set(kn._wv);
        clearTimeout(kn._wt); kn._wt = setTimeout(() => { kn._wv = null; }, 400); };
    });
    st.querySelectorAll(".ft").forEach(ft => {
      const k = ft.dataset.ft, maxDb = +ft.dataset.max;
      const show = (v) => { const p = posOfDb(lin2db(v)) * 100; ft.querySelector(".u").style.height = `calc(${p}% * (164 - 8) / 164)`; ft.querySelector(".cap").style.bottom = `calc(${p}% * (164 - 8) / 164 + 4px)`;
        st.querySelector(".dbv").textContent = fmtDb(lin2db(v)) + " dB"; };
      const setDb = (d) => { d = Math.min(maxDb, d); const v = d < -89 ? 0 : db2lin(d); putVal(id, k, v); show(v); };
      const fromY = (y) => { const r = ft.getBoundingClientRect(); return dbOfPos(Math.max(0, Math.min(1, (r.bottom - 4 - y) / (r.height - 8)))); };
      ft.onpointerdown = (e) => { ft.setPointerCapture(e.pointerId);
        if(!e.target.classList.contains("cap")) setDb(fromY(e.clientY));
        const y0 = e.clientY, p0 = posOfDb(lin2db(+getVal(id, k)));
        ft.onpointermove = (ev) => { const r = ft.getBoundingClientRect(); setDb(dbOfPos(Math.max(0, Math.min(1, p0 + (y0 - ev.clientY) / (r.height - 8) * (ev.shiftKey ? 0.25 : 1))))); };
        ft.onpointerup = () => { ft.onpointermove = null; }; };
      ft.ondblclick = () => setDb(0);
      ft.onwheel = (e) => { e.preventDefault(); const d = lin2db(+getVal(id, k)); setDb((isFinite(d) ? d : -60) - wheelPx(e) / (e.shiftKey ? 400 : 100)); };
    });
    const gt = st.querySelector(".gt");
    if(gt){
      const showG = (d) => { const p = posOfDb(d) * 100; gt.querySelector(".gc").style.bottom = `calc(${p}% * (164 - 8) / 164 + 4px)`;
        const gl = st.querySelector(".gl"); gl.style.bottom = p + "%"; gl.hidden = d <= -99; st.querySelector(".gv b").textContent = gateTxt(d); };
      const setG = (d) => { d = !isFinite(d) || d < -84 ? -100 : Math.round(Math.min(-6, d)); putVal(id, "gate", d); showG(d); };
      const fromY = (y) => { const r = gt.getBoundingClientRect(); return dbOfPos(Math.max(0, Math.min(1, (r.bottom - 4 - y) / (r.height - 8)))); };
      gt.onpointerdown = (e) => { gt.setPointerCapture(e.pointerId); setG(fromY(e.clientY)); gt.onpointermove = (ev) => setG(fromY(ev.clientY)); gt.onpointerup = () => { gt.onpointermove = null; }; };
      gt.ondblclick = () => setG(-100);
      gt.onwheel = (e) => { e.preventDefault(); if(gt._wv == null) gt._wv = MY.gate <= -99 ? -80 : MY.gate;
        gt._wv = Math.max(-100, Math.min(-6, gt._wv - wheelPx(e) / 60)); setG(gt._wv);
        clearTimeout(gt._wt); gt._wt = setTimeout(() => { gt._wv = null; }, 400); };
    }
    const clip = st.querySelector(".clip"); if(clip) clip.onclick = () => { clip.classList.remove("on"); st._clip = false; };
  }
  // the meters, as smooth as the screen: falling slowly, a peak held a moment, clip until clicked
  function peakOf(an){ if(!an) return 0; const a = new Float32Array(an.fftSize); an.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); return p; }
  function meters(){
    if(mix && (link || solo)) $("rack").querySelectorAll(".strip").forEach(st => {
      const id = st.dataset.st;
      const lr = id === "@host" ? (link ? [link.level(), link.level()] : [0, 0]) : id === "@me" ? [peakOf(meterA), peakOf(meterA)] : mix.levels(id);
      const bars = st.querySelectorAll(".mt b"); st._m = st._m || [{v: 0, pk: 0, t: 0}, {v: 0, pk: 0, t: 0}];
      lr.forEach((p, i) => { const m = st._m[i], pos = posOfDb(lin2db(p)), now = performance.now();
        m.v = Math.max(pos, m.v - 0.025); if(pos >= m.pk || now - m.t > 1200){ m.pk = pos; m.t = now; }
        if(bars[i]){ bars[i].firstElementChild.style.height = m.v * 100 + "%"; bars[i].lastElementChild.style.bottom = m.pk * 100 + "%"; }
        if(id === "@me" && i === 0){ const led = st.querySelector(".gled"); if(led) led.className = "gled " + (MY.gate <= -99 ? "" : gateOpen > 0.5 ? "open" : "shut"); }
        if(p >= 0.99 && !st._clip){ st._clip = true; const c = st.querySelector(".clip"); if(c) c.classList.add("on"); } });
    });
    requestAnimationFrame(meters);
  }
  requestAnimationFrame(meters);

  // ---------------------------------------------------------------- text, chords, sections
  let lyrIdx = -2;
  function paintLyrics(){
    $("vLyr").innerHTML = lyrics.length ? `<div class="lyr-row">${lyrics.map((l, i) => `<p data-i="${i}">${esc(l.text) || "&nbsp;"}</p>`).join("")}</div>` : `<p class="muted" style="margin:auto">K téhle písni text nemám.</p>`;
    lyrIdx = -2;
  }
  function lyricsAt(pos){
    if(!lyrics.length) return;
    let i = -1; for(let k = 0; k < lyrics.length; k++){ if(lyrics[k].t <= pos + 0.15) i = k; else break; }
    if(i === lyrIdx) return; lyrIdx = i;
    $("vLyr").querySelectorAll("p").forEach((p, k) => { p.className = k === i ? "now" : k < i ? "past" : ""; });
    const cur = $("vLyr").querySelector("p.now"), row = $("vLyr").querySelector(".lyr-row");
    if(document.body.classList.contains("staged")){ if(cur) cur.scrollIntoView({block: "center", behavior: "smooth"}); return; }
    // one line, running with the song: the line sung now in the middle, the next ones coming from the right
    if(row){ const p = cur || row.querySelector("p"); if(p) row.style.transform = `translateX(${Math.round($("vLyr").clientWidth / 2 - (p.offsetLeft + p.offsetWidth / 2))}px)`; }
  }
  function chordsAt(pos){
    const cs = (info && info.chords || []).filter(c => c.chord && c.chord !== "N");
    const i = cs.findIndex(c => c.end > pos);
    const now = i >= 0 && cs[i].start <= pos + 0.05 ? cs[i] : null;
    $("chNow").textContent = now ? now.chord : "—";
    const from = now ? i + 1 : Math.max(0, i);
    $("chNext").innerHTML = (i < 0 ? [] : cs.slice(from, from + 6)).map(c => `<span>${esc(c.chord)}<small>za ${Math.max(0, c.start - pos).toFixed(1).replace(".", ",")} s</small></span>`).join("");
  }
  function paintSecs(){
    const secs = (info && info.sections) || [], dur = (S && S.dur) || (mix && mix.duration) || (secs.length ? secs[secs.length - 1].end : 0);
    $("secs").innerHTML = dur ? secs.map((s, k) => `<i data-k="${k}" title="${esc(s.name)} · ${mmss(s.start)}–${mmss(s.end)}" style="left:${s.start / dur * 100}%;width:${(s.end - s.start) / dur * 100}%;--c:${esc(s.color || "#666")}"><b>${esc(s.name || "")}</b></i>`).join("") : "";
    secNow = -1; paintWave();
    $("secRow").innerHTML = dur ? secs.map((s, k) => `<span data-k="${k}" title="${esc(s.name)} · ${mmss(s.start)}–${mmss(s.end)} — klik: hrát od začátku sekce" style="left:${s.start / dur * 100}%;width:${(s.end - s.start) / dur * 100}%;--c:${esc(s.color || "#666")}"><em>${esc(s.name || "")}</em><u data-loop title="Smyčka: tahle sekce dokola">⟲</u></span>`).join("") : "";
    $("secRow").querySelectorAll("span").forEach(el => el.onclick = (e) => {
      const sc = secs[+el.dataset.k]; if(!sc || !S) return;
      if(e.target.closest("[data-loop]")){ const on = S.loop && Math.abs(S.loop.start - sc.start) < 0.05 && Math.abs(S.loop.end - sc.end) < 0.05;
        ctl(on ? {do: "loop", start: 0, end: 0} : {do: "loop", start: sc.start, end: sc.end}); return; }
      ctl({do: "seek", pos: sc.start}); if(!S.playing) setTimeout(() => ctl({do: "play"}), 80);
    });
    const sel = $("secSel");
    getJ(`/g/sections?name=${encodeURIComponent(S.name)}`).then(d => {
      const list = (d.sections || []).slice(0, 12);
      sel.innerHTML = list.length ? list.map(s => `<option value="${s.start}|${s.end}">${mmss(s.start)}–${mmss(s.end)}${s.score != null ? " · zřetelnost " + Math.round(s.score * 100) + " %" : ""}</option>`).join("")
        : `<option value="">V téhle písni jsem nenašel úsek se zřetelnou kytarou</option>`;
    }).catch(() => { sel.innerHTML = `<option value="">—</option>`; });
  }
  // the song's own outline (all its tracks together), drawn once a song is loaded
  let wavePeaks = null, wavePeaksOf = null, secNow = -1;
  function paintWave(){
    const cv = $("wave"); if(!cv) return;
    const w = cv.clientWidth, h = cv.clientHeight; if(!w || !h) return;
    const dpr = window.devicePixelRatio || 1; cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d"); g.clearRect(0, 0, cv.width, cv.height);
    if(!mix || !mix.ch.length || !loaded) return;
    if(wavePeaksOf !== loaded){
      const N = 1400, bufs = mix.ch.map(c => c.buf), len = Math.max(...bufs.map(b => b.length)), step = Math.max(1, Math.floor(len / N / 24));
      const pk = new Float32Array(N);
      bufs.forEach(b => { const d = b.getChannelData(0); for(let i = 0; i < d.length; i += step){ const k = Math.floor(i / len * N), v = Math.abs(d[i]); if(v > pk[k]) pk[k] = v; } });
      const mx = Math.max(0.05, ...pk); wavePeaks = pk.map(v => v / mx); wavePeaksOf = loaded;
    }
    const n = wavePeaks.length, mid = cv.height / 2;
    g.fillStyle = "rgba(255,255,255,.55)";
    for(let x = 0; x < cv.width; x++){ const v = wavePeaks[Math.min(n - 1, Math.floor(x / cv.width * n))], a = Math.max(1, v * mid * 0.95); g.fillRect(x, mid - a, 1, a * 2); }
  }
  window.addEventListener("resize", () => paintWave());
  function paintSecNow(pos){
    const secs = (info && info.sections) || [];
    const k = secs.findIndex(s => pos >= s.start && pos < s.end);
    if(k === secNow) return; secNow = k;
    $("secs").querySelectorAll("i").forEach(x => x.classList.toggle("now", +x.dataset.k === k));
    $("secRow").querySelectorAll("span").forEach(x => x.classList.toggle("now", +x.dataset.k === k));
    $("secName").textContent = k >= 0 ? secs[k].name || "" : "";
  }
  $("seek").onclick = (e) => {
    if(!S || !S.dur) return;
    const r = $("seek").getBoundingClientRect(), pos = (e.clientX - r.left) / r.width * S.dur;
    ctl({do: "seek", pos: Math.max(0, pos)});
  };
  $("play").onclick = () => { if(!S || !S.name) return; ctl({do: S.playing ? "pause" : "play"}); };
  $("toStart").onclick = () => ctl({do: "seek", pos: 0});
  $("tDn").onclick = () => S && ctl({do: "tempo", speed: Math.max(0.5, Math.round(((S.speed || 1) - 0.05) * 100) / 100)});
  $("tUp").onclick = () => S && ctl({do: "tempo", speed: Math.min(1.5, Math.round(((S.speed || 1) + 0.05) * 100) / 100)});
  $("kDn").onclick = () => S && ctl({do: "key", semis: Math.max(-12, (S.key || 0) - 1)});
  $("kUp").onclick = () => S && ctl({do: "key", semis: Math.min(12, (S.key || 0) + 1)});
  document.addEventListener("keydown", (e) => {
    if(e.code !== "Space" || /INPUT|TEXTAREA|SELECT|BUTTON/.test(e.target.tagName) || !$("join").hidden) return;
    e.preventDefault(); $("play").click();
  });
  function setView(v){
    view = v; document.querySelectorAll("#hviews [data-v]").forEach(x => x.classList.toggle("on", x.dataset.v === v));
    $("vLyr").hidden = view !== "lyr"; $("vChords").hidden = view !== "chords"; $("vPick").hidden = view !== "pick"; $("vTabs").hidden = view !== "tabs"; lyrIdx = -2;
    if(view === "tabs") showTabs();
  }
  document.querySelectorAll("#hviews [data-v]").forEach(b => b.onclick = () => setView(b.dataset.v));

  // ---------------------------------------------------------------- the sound of his guitar
  let prints = null;
  const ampSel = {model: "", ir: "", mname: "", iname: ""};
  function openSound(tab){ $("sndD").hidden = false; $("setD").hidden = true; paintPrints(); if(tab){ const b = document.querySelector(`.tabs [data-st="${tab}"]`); if(b) b.click(); } }
  document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => { $(b.dataset.close).hidden = true; });
  $("setBtn").onclick = () => { $("setD").hidden = !$("setD").hidden; $("sndD").hidden = true; };
  document.querySelectorAll(".tabs [data-st]").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tabs [data-st]").forEach(x => x.classList.toggle("on", x === b));
    $("stPrints").hidden = b.dataset.st !== "prints"; $("stAmps").hidden = b.dataset.st !== "amps"; $("stOwn").hidden = b.dataset.st !== "own";
    if(b.dataset.st === "amps"){ paintAmpHead(); findAmps(); }
  });
  // the sounds sorted as in Pick: prints by what they sound like, amps by make/gain/type, cabinets by pack/speaker/mic
  const PCAT = {clean: ["Clean", "#4fd1c5"], crunch: ["Crunch", "#f6ad55"], hi_gain: ["Hi-Gain", "#f56565"], lead: ["Sólo", "#b794f4"],
    acoustic: ["Akustická", "#68d391"], ambient: ["Ambient", "#63b3ed"], fuzz: ["Fuzz", "#ed64a6"], other: ["Jiné", "#a0aec0"]};
  let pCat = store("pCat") || "all";
  async function paintPrints(){
    if(!prints){ $("plist").innerHTML = `<div class="muted">Načítám otisky…</div>`; try{ prints = (await getJ("/g/prints")).prints || []; }catch(e){ $("plist").innerHTML = `<div class="bad">${esc(e.message)}</div>`; return; } }
    const words = fold($("pFind").value).split(/\s+/).filter(Boolean);
    const mineP = prints.filter(p => p.friend && p.friend === me.user), rest = prints.filter(p => !(p.friend && p.friend === me.user));
    const ok = (p) => { const t = fold([p.name, p.artist, p.title, p.category, p.core].join(" ")); return words.every(w => t.includes(w)); };
    const row = (p) => `<button data-song="${esc(p.song)}" data-id="${esc(p.id)}" data-name="${esc(p.name)}"><b>${esc(p.name)}</b><span>${esc(p.artist || "")} · ${esc(p.title || p.song)}${p.match != null ? " · shoda " + p.match + " %" : ""}</span></button>`;
    const catOf = (p) => PCAT[p.category] ? p.category : "other";
    const cnt = {}; rest.filter(ok).forEach(p => { const c = catOf(p); cnt[c] = (cnt[c] || 0) + 1; });
    if(pCat !== "all" && !cnt[pCat]) pCat = "all";
    $("pChips").innerHTML = `<button data-c="all" class="${pCat === "all" ? "on" : ""}">Vše<i>${rest.filter(ok).length}</i></button>` +
      Object.keys(PCAT).filter(c => cnt[c]).map(c => `<button data-c="${c}" class="${pCat === c ? "on" : ""}" style="${pCat === c ? `background:${PCAT[c][1]};border-color:${PCAT[c][1]}` : `border-color:${PCAT[c][1]}55`}">${PCAT[c][0]}<i>${cnt[c]}</i></button>`).join("");
    $("pChips").querySelectorAll("[data-c]").forEach(x => x.onclick = () => { pCat = x.dataset.c; store("pCat", pCat); paintPrints(); });
    const a = mineP.filter(ok), b = rest.filter(ok).filter(p => pCat === "all" || catOf(p) === pCat);
    const order = Object.keys(PCAT), grouped = [...b].sort((x, y) => order.indexOf(catOf(x)) - order.indexOf(catOf(y))).slice(0, 300);
    let last = null;
    const body = grouped.map(p => { const c = catOf(p), hd = c !== last ? `<div class="ghd"><i style="background:${PCAT[c][1]}"></i>${PCAT[c][0]}</div>` : ""; last = c; return hd + row(p); }).join("");
    $("plist").innerHTML = (a.length ? `<div class="ghd">Moje otisky</div>` + a.map(row).join("") : "") + (body || `<div class="muted">Nic nenalezeno.</div>`);
    $("plist").querySelectorAll("[data-id]").forEach(x => x.onclick = () => useSound({kind: "print", song: x.dataset.song, id: x.dataset.id, name: x.dataset.name}));
  }
  $("pFind").oninput = () => paintPrints();
  const ASORT = {model: [["key", "Značka a model"], ["gain", "Zisk"], ["type", "Typ"], ["name", "Název A–Z"]],
    ir: [["key", "Balíček a bedna"], ["speaker", "Reproduktor"], ["mic", "Mikrofon"], ["brand", "Značka a velikost"], ["name", "Název A–Z"]]};
  const GAINC = {clean: "#4fd1c5", crunch: "#f6ad55", hi_gain: "#f56565"};
  const AC = Object.assign({kind: "model", sort: {model: "key", ir: "key"}, f: {model: {}, ir: {}}}, store("ampCat") || {});
  function paintAmpHead(){
    $("aKind").querySelectorAll("[data-k]").forEach(b => b.classList.toggle("on", b.dataset.k === AC.kind));
    $("aSort").innerHTML = ASORT[AC.kind].map(([k, l]) => `<option value="${k}" ${AC.sort[AC.kind] === k ? "selected" : ""}>${l}</option>`).join("");
  }
  $("aKind").querySelectorAll("[data-k]").forEach(b => b.onclick = () => { AC.kind = b.dataset.k; store("ampCat", AC); paintAmpHead(); findAmps(); });
  $("aSort").onchange = () => { AC.sort[AC.kind] = $("aSort").value; store("ampCat", AC); findAmps(); };
  async function findAmps(){
    const kind = AC.kind, box = $("alist"), f = AC.f[kind], q = $("aFind").value;
    if(!$("aSort").options.length) paintAmpHead();
    const qs = new URLSearchParams({kind, q, sort: AC.sort[kind], limit: "300", ...f});
    let d;
    try{ d = await getJ("/g/amp-catalog?" + qs); }
    catch(e){                                                        // an older library: the plain list
      try{ const o = await getJ(`/g/amps?kind=${kind}&q=${encodeURIComponent(q)}`); d = {items: o.amps.map(a => ({...a, group: ""})), facets: {}, gains: {}}; }
      catch(e2){ box.innerHTML = `<div class="bad">${esc(e2.message)}</div>`; return; } }
    // chips: gain and type for amps, speaker and mic for cabinets — tap one to keep only those, again to let go
    const chipKeys = kind === "ir" ? [["speaker", 8], ["mic", 8]] : [["gain", 4], ["type", 6]];
    const label = (k, v) => k === "gain" ? (d.gains[v] || v) : v;
    $("aChips").innerHTML = chipKeys.map(([k, n], ix) => (ix ? `<span class="sep"></span>` : "") + ((d.facets[k] || []).slice(0, n).map(([v, c]) => {
      const on = f[k] === v, col = k === "gain" ? GAINC[v] : "";
      return `<button data-fk="${k}" data-fv="${esc(v)}" class="${on ? "on" : ""}" style="${on ? `background:${col || "var(--acc)"};border-color:${col || "var(--acc)"}` : col ? `border-color:${col}66` : ""}">${esc(label(k, v))}<i>${c}</i></button>`; }).join(""))).join("");
    $("aChips").querySelectorAll("[data-fk]").forEach(x => x.onclick = () => { const k = x.dataset.fk; f[k] = f[k] === x.dataset.fv ? "" : x.dataset.fv; if(!f[k]) delete f[k]; store("ampCat", AC); findAmps(); });
    const cur = kind === "ir" ? ampSel.ir : ampSel.model;
    // his own files first (loaded from his computer, kept in this browser)
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const own = (await OwnSounds.list()).filter(k => (kind === "ir" ? /\.wav$/i : /\.nam$/i).test(k) && words.every(w => k.toLowerCase().includes(w)));
    d.items = own.map(k => ({file: k, name: k.slice(4).replace(/\.(nam|wav)$/i, ""), group: "Moje z počítače", own: true})).concat(d.items);
    const sub = (a) => a.own ? "z tvého počítače" : kind === "ir" ? [a.speaker, a.mic, a.size].filter(Boolean).join(" · ") : [d.gains[a.gain] && a.gain ? d.gains[a.gain] : "", a.type].filter(Boolean).join(" · ");
    let last = null;
    box.innerHTML = d.items.map(a => { const hd = a.group && a.group !== last ? `<div class="ghd">${a.gain && AC.sort[kind] === "gain" ? `<i style="background:${GAINC[a.gain] || "#a0aec0"}"></i>` : ""}${esc(a.group)}</div>` : ""; last = a.group;
      return hd + `<button data-f="${esc(a.file)}" data-n="${esc(a.name)}" class="${cur === a.file ? "cur" : ""}"><b>${esc(a.name)}${a.own ? `<i class="own-x" data-del="${esc(a.file)}" title="Smazat z prohlížeče">✕</i>` : ""}</b>${sub(a) ? `<span>${esc(sub(a))}</span>` : ""}</button>`; }).join("")
      + (d.total > d.items.length ? `<div class="muted">… a dalších ${d.total - d.items.length} — zúži hledání nebo kategorii</div>` : "") || `<div class="muted">Nic nenalezeno.</div>`;
    box.querySelectorAll("[data-del]").forEach(x => x.onclick = async (e) => { e.stopPropagation(); await OwnSounds.del(x.dataset.del); toast("Smazáno z prohlížeče."); findAmps(); });
    box.querySelectorAll("[data-f]").forEach(x => x.onclick = () => {
      if(kind === "ir"){ ampSel.ir = x.dataset.f; ampSel.iname = x.dataset.n; } else { ampSel.model = x.dataset.f; ampSel.mname = x.dataset.n; }
      box.querySelectorAll(".cur").forEach(c => c.classList.remove("cur")); x.classList.add("cur");
      $("ampPick").textContent = [ampSel.mname || "bez zesilovače", ampSel.iname || "bez kabinetu"].join(" + ");
      useSound({kind: "amp", model: ampSel.model, ir: ampSel.ir, name: [ampSel.mname, ampSel.iname].filter(Boolean).join(" + ")});
    });
  }
  // a .nam or .wav from his computer (tone3000.com and the like): kept in the browser, chosen at once
  $("ownUp").onchange = async (e) => {
    const files = [...e.target.files]; e.target.value = "";
    let lastNam = null, lastIr = null;
    for(const f of files){
      if(f.size > 25e6){ toast(`${f.name} je moc velký.`); continue; }
      const nam = /\.nam$/i.test(f.name), wav = /\.wav$/i.test(f.name); if(!nam && !wav) continue;
      if(nam){ try{ JSON.parse(await f.slice(0, 2e6).text().then(x => x.length < 2e6 ? x : "{}")); }catch(err){ toast(`${f.name} není NAM profil.`); continue; } }
      const id = "own:" + f.name; await OwnSounds.put(id, f);
      if(nam) lastNam = {id, name: f.name.replace(/\.nam$/i, "")}; else lastIr = {id, name: f.name.replace(/\.wav$/i, "")};
    }
    if(lastNam){ ampSel.model = lastNam.id; ampSel.mname = lastNam.name; }
    if(lastIr){ ampSel.ir = lastIr.id; ampSel.iname = lastIr.name; }
    if(lastNam || lastIr){
      AC.kind = lastNam ? "model" : "ir"; paintAmpHead(); findAmps();
      $("ampPick").textContent = [ampSel.mname || "bez zesilovače", ampSel.iname || "bez kabinetu"].join(" + ");
      useSound({kind: "amp", model: ampSel.model, ir: ampSel.ir, name: [ampSel.mname, ampSel.iname].filter(Boolean).join(" + ")});
    }
  };
  let ampT = 0;
  $("aFind").oninput = () => { clearTimeout(ampT); ampT = setTimeout(findAmps, 250); };
  async function useSound(s, quiet){
    $("sndNow").textContent = "Načítám…";
    try{
      if(s.kind === "print") await amp.setPrint(s.song, s.id, s.name);
      else if(s.kind === "amp") await amp.setAmp(s.model, s.ir, s.name);
      else amp.clear();
      store("sound", s);
      const k = JSON.stringify(s), h = (store("hist") || []).filter(x => JSON.stringify(x) !== k); h.unshift(s); store("hist", h.slice(0, 12)); HI = 0;
      if(!quiet) toast(amp.label);
    }catch(e){ toast(e.message); }
    $("sndNow").textContent = amp.label; renderRack(); sendMine();
  }
  function restoreSound(){ const s = store("sound"); if(s && s.kind !== "clean") useSound(s, true); }
  $("sndClean").onclick = () => useSound({kind: "clean"});
  $("drive").oninput = (e) => { MY.drive = +e.target.value; applyMine(); renderRack(); };

  // his own print: the stretch played along with the song, clean — sent to the host's Mac, a print made of it
  let rec = null;
  $("recBtn").onclick = async () => {
    if(solo){ toast(`Vlastní otisk jde jen během jamu — počítá ho Pick na Macu u ${me.host}.`, 8000); return; }
    if(rec){ rec.abort = true; return; }
    const v = $("secSel").value; if(!v || !S || loaded !== S.name){ toast("Nejdřív vyber píseň a úsek."); return; }
    const [a, b] = v.split("|").map(Number);
    const start = a, end = Math.min(b, a + 40), sr = ctx.sampleRate;
    rec = {abort: false}; $("recBtn").textContent = "Zastavit";
    const node = await JamRec.recorder(ctx), blocks = [];
    node.port.onmessage = (e) => blocks.push(e.data);
    inSel.connect(node);
    ctl({do: "play", pos: Math.max(0, start - 4)});
    const inLat = ((inStream.getAudioTracks()[0].getSettings() || {}).latency || 0.01), outLat = (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
    const t0 = performance.now();
    await new Promise(res => { const t = setInterval(() => {
      const p = posNow();
      $("recSt").textContent = p < start ? `Za ${Math.ceil(start - p)} s začni hrát…` : `Hraj — zbývá ${Math.ceil(end - p)} s`;
      if(rec.abort || (S && !S.playing && performance.now() - t0 > 3000) || p >= end + 0.3){ clearInterval(t); res(); }
    }, 100); });
    node.port.postMessage("stop"); try{ inSel.disconnect(node); }catch(e){}
    ctl({do: "pause"});
    const aborted = rec.abort; rec = null; $("recBtn").textContent = "Nahrát úsek a vytvořit otisk";
    if(aborted){ $("recSt").textContent = "Zrušeno."; return; }
    const len = Math.round((end - start) * sr), x = new Float32Array(len); let got = 0, peak = 0;
    for(const bk of blocks){
      const s0 = mix.songAt(bk.t - inLat - outLat); if(s0 == null) continue;
      const at = Math.round((s0 - start) * sr);
      for(let i = 0; i < bk.d.length; i++){ const k = at + i; if(k >= 0 && k < len){ x[k] = bk.d[i]; got++; peak = Math.max(peak, Math.abs(bk.d[i])); } }
    }
    if(got < sr * 4){ $("recSt").textContent = "Nahrávka úsek nezachytila — hrála píseň? Zkus to znovu."; return; }
    if(peak < 0.003){ $("recSt").textContent = "Z kytary nic nepřišlo — je vybraná správná zvukovka a kanál?"; return; }
    $("recSt").textContent = "Posílám nahrávku na Mac…";
    const r = await api.raw(`/g/take?name=${encodeURIComponent(S.name)}&start=${start}&end=${end}&gain_db=${MY.ing}`, {method: "POST", body: JamRec.wav(x, sr)});
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ $("recSt").textContent = d.error || "Nepodařilo se."; return; }
    followJob(S.name);
  };
  const STAGES = {listen: "poslouchám originál a tvou nahrávku", sieve: "zkouším jádra zkreslení", sieve2: "zkouším nejlepší jádra", tune: "dolaďuji drive a ekvalizér",
    colour: "počítám barvu", room: "hledám prostor", save: "ukládám", notes: "poslouchám noty", compare: "porovnávám"};
  function followJob(song){
    const t = setInterval(async () => {
      let j = null; try{ j = (await getJ("/g/job")).job; }catch(e){}
      if(!j) return;
      if(j.status === "running"){ $("recSt").textContent = `Mac počítá tvůj otisk — ${STAGES[j.stage] || j.stage}${j.of ? ` (${j.done}/${j.of})` : ""}…`; return; }
      clearInterval(t);
      if(j.status === "done"){ $("recSt").textContent = `Hotovo — shoda ${j.match} %. Otisk je zapnutý.`; prints = null;
        await useSound({kind: "print", song, id: j.print, name: "můj otisk"}); paintPrints(); }
      else $("recSt").textContent = j.error || "Otisk se nepovedl.";
    }, 2000);
  }

  // ---------------------------------------------------------------- the strips folded, the sounds stepped through
  const MINI = new Set(store("mini") || []);
  let HI = 0;
  function stepSound(d){
    const h = store("hist") || []; if(h.length < 2){ toast("Zatím žádné další použité zvuky — vyber v Otiscích nebo NAM."); return; }
    HI = (HI + d + h.length) % h.length; const s = h[HI];
    const keep = store("hist"); useSound(s).then(() => { store("hist", keep); });          // stepping keeps the order
  }

  // ---------------------------------------------------------------- the EQ of his guitar: Pick's five bands
  const EQ_DEF = [{type: "lowshelf", freq: 80, q: 0.7, color: "#ff5c8a"}, {type: "peaking", freq: 250, q: 1, color: "#ffb020"}, {type: "peaking", freq: 1000, q: 1, color: "#3ddc97"},
    {type: "peaking", freq: 4000, q: 1, color: "#38bdf8"}, {type: "highshelf", freq: 10000, q: 0.7, color: "#a78bfa"}];
  const EQ_PRESETS = [
    ["Plochý (vše na nule)", [[80, 0, .7], [250, 0, 1], [1000, 0, 1], [4000, 0, 1], [10000, 0, .7]]],
    ["Kytara — metal rytmika", [[90, -4, .7], [250, -3, 1.3], [700, -2, 1], [3000, 3, 1], [9000, -3, .7]]],
    ["Kytara — sólo / lead", [[100, -3, .7], [400, -2, 1], [1500, 3, .9], [4000, 2, 1], [9000, -1, .7]]],
    ["Kytara — čistá", [[90, -2, .7], [250, -1.5, 1], [1000, 0, 1], [3500, 2, 1], [10000, 3, .7]]],
    ["Akustická kytara", [[100, -4, .7], [200, -3, 1.4], [1000, -1, 1], [5000, 2, 1], [12000, 3, .7]]],
    ["Baskytara", [[60, 3, .7], [250, -3, 1.2], [800, 2, 1.2], [2500, 2, 1], [8000, -4, .7]]],
    ["Telefon / lo-fi", [[300, -12, .7], [800, 2, 1], [1600, 5, 1.2], [3000, 2, 1], [4500, -12, .7]]]];
  if(!Array.isArray(MY.eq) || MY.eq.length !== 5) MY.eq = EQ_DEF.map(b => [b.freq, 0, b.q]);
  function applyEq(){ eqNodes.forEach((f, i) => { const [fr, g, q] = MY.eq[i]; f.frequency.value = fr; f.Q.value = q; f.gain.value = MY.eqOn ? g : 0; }); }
  let pop = null;
  function closePop(){ if(pop){ pop.remove(); pop = null; } }
  function place(el, anchor){ document.body.appendChild(el); const r = anchor.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px"; el.style.top = Math.max(8, r.top - h - 10) + "px"; }
  document.addEventListener("pointerdown", (e) => { if(pop && !pop.contains(e.target) && !e.target.closest("#tEq,#tTun")) closePop(); });
  function openEq(anchor){
    closePop(); pop = document.createElement("div"); pop.className = "pop";
    const paint = () => {
      pop.innerHTML = `<div class="ph"><b>Ekvalizér — moje kytara</b><label class="muted"><input type="checkbox" id="eqOn" ${MY.eqOn ? "checked" : ""}> zapnutý</label>
          <select id="eqPre"><option value="">Presety…</option>${EQ_PRESETS.map(([n], i) => `<option value="${i}">${esc(n)}</option>`).join("")}</select><button id="eqX">✕</button></div>
        <canvas class="eqc" id="eqC" width="840" height="300"></canvas>
        <div class="eqb">${MY.eq.map(([f, g, q], i) => `<div class="b" style="--c:${EQ_DEF[i].color}"><b style="color:${EQ_DEF[i].color}">${g > 0 ? "+" : ""}${g.toFixed(1)} dB</b>
          <input type="range" min="-15" max="15" step="0.5" value="${g}" data-i="${i}" data-k="1" style="accent-color:${EQ_DEF[i].color}">
          <input type="number" min="20" max="20000" step="10" value="${Math.round(f)}" data-i="${i}" data-k="0" title="Frekvence (Hz)">
          <input type="number" min="0.2" max="8" step="0.1" value="${q}" data-i="${i}" data-k="2" title="Šířka (Q)"></div>`).join("")}</div>
        <div class="muted">Táhni posuvníky, dvojklik = 0 dB. Hz a Q se dají přepsat. Ekvalizér je za otiskem nebo zesilovačem a slyší ho i ${esc(me.host)}.</div>`;
      pop.querySelector("#eqX").onclick = closePop;
      pop.querySelector("#eqOn").onchange = (e) => { MY.eqOn = e.target.checked; applyEq(); applyMine(); curve(); const t = $("tEq"); if(t) t.classList.toggle("on", MY.eqOn); };
      pop.querySelector("#eqPre").onchange = (e) => { if(e.target.value === "") return; MY.eq = EQ_PRESETS[+e.target.value][1].map(b => b.slice()); MY.eqOn = true; applyEq(); applyMine(); paint(); const t = $("tEq"); if(t) t.classList.add("on"); };
      pop.querySelectorAll("[data-i]").forEach(el => {
        const i = +el.dataset.i, k = +el.dataset.k;
        el.oninput = () => { MY.eq[i][k] = +el.value; if(k === 1 && !MY.eqOn){ MY.eqOn = true; pop.querySelector("#eqOn").checked = true; const t = $("tEq"); if(t) t.classList.add("on"); }
          applyEq(); applyMine(); curve(); if(k === 1) el.parentNode.querySelector("b").textContent = (MY.eq[i][1] > 0 ? "+" : "") + MY.eq[i][1].toFixed(1) + " dB"; };
        if(k === 1) el.ondblclick = () => { el.value = 0; el.oninput(); };
      });
      curve();
    };
    const curve = () => {
      const c = pop.querySelector("#eqC"); if(!c) return; const g = c.getContext("2d"), W = c.width, H = c.height, N = 200;
      g.clearRect(0, 0, W, H); g.strokeStyle = "rgba(255,255,255,.07)"; g.lineWidth = 1;
      const xOf = (f) => Math.log(f / 20) / Math.log(1000) * W, yOf = (db) => H / 2 - db / 18 * (H / 2);
      [50, 100, 200, 500, 1000, 2000, 5000, 10000].forEach(f => { g.beginPath(); g.moveTo(xOf(f), 0); g.lineTo(xOf(f), H); g.stroke(); });
      [-12, -6, 0, 6, 12].forEach(d => { g.beginPath(); g.moveTo(0, yOf(d)); g.lineTo(W, yOf(d)); g.stroke(); });
      g.fillStyle = "#7d7d92"; g.font = "18px system-ui"; [[100, "100"], [1000, "1k"], [10000, "10k"]].forEach(([f, t]) => g.fillText(t, xOf(f) + 4, H - 8));
      const fr = new Float32Array(N); for(let i = 0; i < N; i++) fr[i] = 20 * Math.pow(1000, i / (N - 1));
      const tot = new Float32Array(N).fill(0), mag = new Float32Array(N), ph = new Float32Array(N);
      eqNodes.forEach(f => { f.getFrequencyResponse(fr, mag, ph); for(let i = 0; i < N; i++) tot[i] += 20 * Math.log10(mag[i] || 1e-6); });
      g.beginPath(); for(let i = 0; i < N; i++){ const x = i / (N - 1) * W, y = yOf(tot[i]); i ? g.lineTo(x, y) : g.moveTo(x, y); }
      g.strokeStyle = MY.eqOn ? "#a78bfa" : "#555"; g.lineWidth = 4; g.stroke();
      MY.eq.forEach(([f, gg], i) => { g.fillStyle = EQ_DEF[i].color; g.beginPath(); g.arc(xOf(f), yOf(MY.eqOn ? gg : 0), 9, 0, 7); g.fill(); });
    };
    paint(); place(pop, anchor);
  }

  // ---------------------------------------------------------------- the tuner (Pick's YIN), on his guitar before the sound
  const NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  function yin(buf, sr, fmin, fmax){
    const W = 2048, tauMax = Math.min(Math.floor(sr / fmin), buf.length - W - 1), tauMin = Math.floor(sr / fmax);
    const d = new Float32Array(tauMax + 1); let run = 0, best = -1;
    for(let tau = 1; tau <= tauMax; tau++){ let v = 0; for(let j = 0; j < W; j += 2){ const x = buf[j] - buf[j + tau]; v += x * x; } d[tau] = v; }
    for(let tau = 1; tau <= tauMax; tau++){ run += d[tau]; d[tau] = run ? d[tau] * tau / run : 1; }
    for(let tau = tauMin; tau <= tauMax; tau++) if(d[tau] < 0.12){ while(tau + 1 <= tauMax && d[tau + 1] < d[tau]) tau++; best = tau; break; }
    if(best < 1) return null;
    let t = best; if(best > 1 && best < tauMax){ const a = d[best - 1], b = d[best], c = d[best + 1], den = a - 2 * b + c; if(den) t = best + 0.5 * (a - c) / den; }
    return {f: sr / t, conf: 1 - d[best]};
  }
  function openTuner(anchor){
    closePop(); pop = document.createElement("div"); pop.className = "pop tun";
    const row = songs.find(x => S && x.name === S.name), tune = (row && Array.isArray(row.tuning) ? [...row.tuning].sort((a, b) => a - b) : [40, 45, 50, 55, 59, 64]).map(m => m + ((S && S.key) || 0));
    pop.innerHTML = `<div class="ph" style="width:100%"><b>Ladička</b><label class="muted"><input type="checkbox" id="tuMute" checked> neslyšet se při ladění</label><button id="tuX">✕</button></div>
      <div class="note" id="tuN">—</div><div class="hz" id="tuHz">zahraj prázdnou strunu</div><div class="gauge"><i id="tuG"></i></div>
      <div class="muted">Ladění písně${S && S.key ? ` (posunuté o ${S.key > 0 ? "+" : ""}${S.key})` : ""}:</div><div class="tstr" id="tuS">${tune.map(m => `<span data-m="${m}">${NOTES[((m % 12) + 12) % 12]}</span>`).join("")}</div>`;
    place(pop, anchor);
    const was = MY.mm; const mute = (on) => { MY.mm = on ? true : was; applyMine(); }; mute(true);
    pop.querySelector("#tuMute").onchange = (e) => mute(e.target.checked);
    const buf = new Float32Array(tunA.fftSize); let tm = 0, alive = true;
    const end = () => { alive = false; mute(false); MY.mm = was; applyMine(); renderRack(); };
    pop.querySelector("#tuX").onclick = () => { end(); closePop(); };
    const obs = new MutationObserver(() => { if(!pop || !document.body.contains(pop)){ if(alive) end(); obs.disconnect(); } }); obs.observe(document.body, {childList: true});
    const tick = () => {
      if(!alive) return; tunA.getFloatTimeDomainData(buf);
      let e = 0; for(let i = 0; i < buf.length; i += 4) e += buf[i] * buf[i];
      const r = e / (buf.length / 4) > 1e-5 ? yin(buf, ctx.sampleRate, 30, 1200) : null;
      if(r && r.conf > 0.85){
        const midi = 69 + 12 * Math.log2(r.f / 440), m = Math.round(midi), cents = Math.round((midi - m) * 100);
        pop.querySelector("#tuN").innerHTML = `${NOTES[((m % 12) + 12) % 12]}<small>${Math.floor(m / 12) - 1}</small>`;
        pop.querySelector("#tuN").style.color = Math.abs(cents) <= 4 ? "var(--ok)" : Math.abs(cents) <= 15 ? "var(--warn)" : "var(--bad)";
        pop.querySelector("#tuHz").textContent = `${r.f.toFixed(1).replace(".", ",")} Hz · ${cents > 0 ? "+" : ""}${cents} centů`;
        pop.querySelector("#tuG").style.left = (50 + Math.max(-50, Math.min(50, cents))) + "%";
        const near = tune.reduce((a, b) => Math.abs(b - midi) < Math.abs(a - midi) ? b : a, tune[0]);
        pop.querySelectorAll("#tuS span").forEach(sp => sp.classList.toggle("on", +sp.dataset.m === near && Math.abs(near - midi) < 0.6));
        tm = performance.now();
      } else if(performance.now() - tm > 1500){ pop.querySelector("#tuN").textContent = "—"; pop.querySelector("#tuN").style.color = ""; }
      setTimeout(tick, 60);
    };
    tick();
  }

  // ---------------------------------------------------------------- the song's tabs
  const TABS = {name: null, list: [], api: null};
  async function showTabs(){
    if(!S || !S.name){ $("tabBody").innerHTML = `<div class="muted" style="padding:20px">Vyber píseň.</div>`; return; }
    if(TABS.name !== S.name){
      TABS.name = S.name; $("tabBar").innerHTML = ""; $("tabBody").innerHTML = `<div class="muted" style="padding:20px">Hledám taby…</div>`;
      try{ TABS.list = (await getJ(`/g/tabs?name=${encodeURIComponent(S.name)}`)).tabs || []; }catch(e){ TABS.list = []; }
      if(TABS.name !== S.name) return;
      paintTabBar(TABS.list.find(t => t.kind === "file") || TABS.list[0]);
    }
  }
  function paintTabBar(cur){
    $("tabBar").innerHTML = TABS.list.map((t, i) => `<button data-t="${i}" class="${t === cur ? "on" : ""}">${t.kind === "file" ? "📄 " : "🌐 "}${esc(t.name || "Taby")}</button>`).join("")
      + `<span class="muted" style="margin-left:auto">Tabulatura v Picku u ${esc(me.host)} živě: záložka „Pick (taby)“</span>`;
    $("tabBar").querySelectorAll("[data-t]").forEach(b => b.onclick = () => paintTabBar(TABS.list[+b.dataset.t]));
    if(!cur){ $("tabBody").innerHTML = `<div class="tcard"><b>K téhle písni žádné taby nemám.</b><span class="muted">Až ${esc(me.host)} v Picku otevře tab a klikne „Sdílet Pick“, uvidíš ho v záložce „Pick (taby)“.</span></div>`; return; }
    if(cur.kind === "songsterr" && !cur.meta){
      $("tabBody").innerHTML = `<div class="tcard"><b>${esc(cur.name)}</b><span>Taby k téhle písni jsou na Songsterru — kytary, basa, bicí i ostatní nástroje, celá stránka. Otevřou se ti v nové kartě.</span>
        <a class="big" href="${esc(cur.url)}" target="_blank" rel="noopener">Otevřít taby na Songsterru ↗</a>
        <span class="muted">Taby přímo v Picku u ${esc(me.host)} (jak jdou s písní) uvidíš v záložce „Pick (taby)“, když je sdílí.</span></div>`;
      return;
    }
    cur.kind === "songsterr" ? drawSongsterr(cur) : drawTabFile(cur);
  }
  const addScript = (src) => new Promise((res, rej) => { const sc = document.createElement("script"); sc.src = src; sc.onload = res; sc.onerror = () => rej(new Error("Nepodařilo se načíst " + src)); document.head.appendChild(sc); });
  // one tab drawn by alphaTab: a Guitar Pro file of the host's (bytes), or a Songsterr tab (a score made in this browser)
  async function tabView(t, getScore){
    $("tabBody").innerHTML = `<div class="muted" style="padding:20px">${t.kind === "songsterr" ? "Stahuju tab ze Songsterru…" : "Kreslím tab…"}</div>`;
    try{
      if(!window.alphaTab) await addScript("/static/alphatab/alphaTab.min.js");
      const got = await getScore();
      if(TABS.name !== (S && S.name)) return;
      $("tabBody").innerHTML = `<div style="display:flex;gap:6px;padding:8px 12px;align-items:center;background:#ece9e1;flex-wrap:wrap"><span style="font-size:12px">Nástroj:</span><select id="tabTrack"></select>
        <button id="tabZo">−</button><button id="tabZi">+</button>${t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener" style="margin-left:auto;font-size:12px">Songsterr ↗</a>` : ""}</div>
        ${got.note ? `<div class="muted" style="padding:6px 12px">${esc(got.note)}</div>` : ""}<div id="tabAt" style="padding:10px"></div>`;
      const at = new alphaTab.AlphaTabApi($("tabAt"), {core: {fontDirectory: "/static/alphatab/font/", useWorkers: false}, display: {layoutMode: alphaTab.LayoutMode.Page, scale: store("tabScale") || 1}, player: {enablePlayer: false}});
      TABS.api = at;
      at.scoreLoaded.on((score) => { $("tabTrack").innerHTML = score.tracks.map((tr, i) => `<option value="${i}">${esc(tr.name || "Stopa " + (i + 1))}</option>`).join("") + `<option value="all">Všechny nástroje</option>`;
        $("tabTrack").onchange = (e) => at.renderTracks(e.target.value === "all" ? score.tracks : [score.tracks[+e.target.value]]); });
      const zoomBy = (d) => { const sc = Math.max(0.5, Math.min(2, (at.settings.display.scale || 1) + d)); at.settings.display.scale = sc; store("tabScale", sc); at.updateSettings(); at.render(); };
      $("tabZo").onclick = () => zoomBy(-0.1); $("tabZi").onclick = () => zoomBy(0.1);
      if(got.bytes) at.load(got.bytes);
      else { at.renderScore(got.score, [got.track || 0]); $("tabTrack").value = String(got.track || 0); }
    }catch(e){
      $("tabBody").innerHTML = `<div class="tcard"><b>Tab se nepodařilo vykreslit.</b><span class="muted">${esc(e.message || "")}</span>
        ${t.url ? `<a class="big" href="${esc(t.url)}" target="_blank" rel="noopener">Otevřít taby na Songsterru ↗</a>` : ""}</div>`;
    }
  }
  function drawTabFile(t){
    return tabView(t, async () => ({bytes: new Uint8Array(await (await api.raw(`/g/tabfile?name=${encodeURIComponent(S.name)}&path=${encodeURIComponent(t.path)}`)).arrayBuffer())}));
  }
  // Songsterr: the host's Pick passes only where the tab is; this browser fetches its parts from Songsterr's CDN
  function drawSongsterr(t){
    return tabView(t, async () => {
      if(!window.SongsterrTabs) await addScript("/static/songsterr-alphatab.js");
      const got = await window.SongsterrTabs.load(t.meta);
      const gtr = got.score.tracks.findIndex(tr => tr.playbackInfo.program >= 24 && tr.playbackInfo.program <= 31);   // a guitar first, not the vocals
      return {score: got.score, track: Math.max(0, gtr), note: got.missing.length ? `Některé nástroje (${got.missing.length}) se ze Songsterru nepodařilo stáhnout.` : ""};
    });
  }

  // ---------------------------------------------------------------- the rest of the controls, the loop
  $("inDev").onchange = $("inCh").onchange = () => useGuitar().catch(() => {});
  $("mon").onchange = () => applyMine();
  $("buf").onchange = applyBuf;
  $("voVol").oninput = (e) => { $("voice").volume = +e.target.value; };
  $("camDev").onchange = async () => { store("cam", $("camDev").value); if($("camBtn").classList.contains("on")) showMe(await link.setCamera(true, $("camDev").value)); };
  $("camBtn").onclick = async () => { if(!link) return; const on = !$("camBtn").classList.contains("on"); try{ showMe(await link.setCamera(on, $("camDev").value)); $("camBtn").classList.toggle("on", on); }catch(e){ toast(camHelp(e)); } };
  $("voiceBtn").onclick = async () => { if(!link) return; const on = !$("voiceBtn").classList.contains("on"); try{ await link.setVoice(on, $("voiceDev").value); $("voiceBtn").classList.toggle("on", on); store("voice", $("voiceDev").value); }catch(e){ toast(micHelp(e)); } };
  $("voiceDev").onchange = async () => { store("voice", $("voiceDev").value); if($("voiceBtn").classList.contains("on")) await link.setVoice(true, $("voiceDev").value); };

  function ui(){
    if(!link && !solo) return;
    if(link){
    const d = link.delay(), r = link.route();
    $("lat").innerHTML = link.ready ? `${d.total} ms<small>síť ${d.net} · paměť ${d.buf} · výstup ${d.out}</small>` : "";
    $("lat").style.color = d.total <= 30 ? "var(--ok)" : d.total <= 50 ? "var(--warn)" : "var(--bad)";
    $("route").textContent = r ? `Spojení: ${r}${link.rxStats.under ? ` · výpadky ${link.rxStats.under}` : ""}${link.rxStats.lost ? ` · ztracené balíčky ${link.rxStats.lost}` : ""}` : "";
    }
    const p = peakOf(meterA); $("meter").style.width = Math.min(100, p * 100) + "%"; $("meter").style.background = p > 0.95 ? "var(--bad)" : "var(--ok)";
    if(S && S.name){
      const pos = posNow(), dur = S.dur || mix.duration || 0;
      $("time").textContent = `${mmss(pos)} / ${mmss(dur)}`;
      if(stageNow){ $("stTime").textContent = `${mmss(pos)} / ${mmss(dur)}`; $("stSong").textContent = $("song").textContent; $("stSec").textContent = $("secName").textContent; }
      $("head").style.left = dur ? Math.min(100, pos / dur * 100) + "%" : "0";
      const lp = $("loopR"), L = S.loop;
      // alone the loop is kept here: at its end, back to its start
      if(solo && L && S.playing && pos >= L.end - 0.03) ctl({do: "seek", pos: L.start});
      $("secRow").querySelectorAll("span").forEach(x => { const sc = ((info && info.sections) || [])[+x.dataset.k];
        x.classList.toggle("loop", !!(L && sc && Math.abs(L.start - sc.start) < 0.05 && Math.abs(L.end - sc.end) < 0.05)); });
      if(L && dur){ lp.hidden = false; lp.style.left = L.start / dur * 100 + "%"; lp.style.width = (L.end - L.start) / dur * 100 + "%"; lp.title = `Smyčka ${mmss(L.start)}–${mmss(L.end)}`; } else lp.hidden = true;
      if(view === "lyr" || stageNow) lyricsAt(pos); else if(view === "chords") chordsAt(pos);
      paintSecNow(pos);
    }
  }
  let instEv = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); instEv = e; $("instBtn").hidden = false; });
  window.addEventListener("appinstalled", () => { $("instBtn").hidden = true; toast("Pick Jam je na ploše a v nabídce Start.", 6000); });
  $("instBtn").onclick = async () => { if(!instEv) return; instEv.prompt(); try{ await instEv.userChoice; }catch(e){} instEv = null; $("instBtn").hidden = true; };
  // ---------------------------------------------------------------- the player in the top bar
  const hpSong = () => !!(S && S.name && !document.body.classList.contains("browse"));
  const hpName = () => hpSong() ? S.name : PV.name;
  function hpNeighbour(d){
    const rows = [...document.querySelectorAll("#slist .row[data-n]")].map(b => b.dataset.n), list = rows.length ? rows : songs.map(x => x.name);
    const i = list.indexOf(hpName()); return list.length ? list[(i < 0 ? 0 : i + d + list.length) % list.length] : null;
  }
  const hpGo = (name) => { if(!name) return; hpSong() ? playTogether(name) : preview(name); };
  $("hpPlay").onclick = () => { if(hpSong()) return $("play").click(); if(PV.name) pa.paused ? pa.play().catch(() => {}) : pa.pause(); else hpGo(hpNeighbour(0)); };
  $("hpStop").onclick = () => { if(hpSong()){ ctl({do: "pause"}); setTimeout(() => ctl({do: "seek", pos: 0}), 80); } else { pa.pause(); try{ pa.currentTime = 0; }catch(e){} } };
  $("hpNext").onclick = () => hpGo(hpNeighbour(1));
  $("hpPrev").onclick = () => { const pos = hpSong() ? posNow() : pa.currentTime || 0; if(pos > 3){ hpSong() ? ctl({do: "seek", pos: 0}) : (pa.currentTime = 0); } else hpGo(hpNeighbour(-1)); };
  $("hpFav").onclick = () => { const n = hpName(); if(n) favToggle(n); };
  // the song playing here into the DJ (deck A), and the DJ's list opened on the songs in the same tuning
  $("hpDj").onclick = () => { const n = hpName(); if(!n) return toast("Nejdřív si pusť píseň.");
    if(!solo && link && link.ready) return toast("DJ je jen mimo jam.");
    if(window.PickDJ && PickDJ.sendSong){ if(hpSong()) ctl({do: "pause"}); else pa.pause(); PickDJ.sendSong(n); } };
  $("hpVol").value = String(+(store("vol") ?? 1));
  $("hpVol").oninput = () => { const v = +$("hpVol").value; store("vol", v); if(master) master.gain.setTargetAtTime(v, ctx.currentTime, 0.02); pa.volume = Math.min(1, v); };
  pa.volume = Math.min(1, +$("hpVol").value);
  $("hpSeek").oninput = () => { const f = $("hpSeek").value / 1000;
    if(hpSong()){ const d = S.dur || (mix && mix.duration) || 0; if(d) ctl({do: "seek", pos: f * d}); } else if(pa.duration) pa.currentTime = f * pa.duration; };
  setInterval(() => {
    const song = hpSong(), name = hpName(), row = name ? songs.find(x => x.name === name) : null;
    const pos = song ? posNow() : pa.currentTime || 0, dur = song ? (S.dur || (mix && mix.duration) || 0) : (pa.duration || (row && row.duration) || 0);
    const playing = song ? !!S.playing : !!(PV.name && !pa.paused);
    $("hpTitle").textContent = name ? `${row ? row.title : name}${row && row.artist ? " · " + row.artist : ""}${song ? "" : " (náhled)"}` : "Vyber píseň";
    $("hpTime").textContent = `${mmss(pos)} / ${mmss(dur)}`; $("hpPlay").textContent = playing ? "❚❚" : "▶";
    if(document.activeElement !== $("hpSeek")) $("hpSeek").value = dur ? Math.round(Math.min(1, pos / dur) * 1000) : 0;
    $("hpFav").classList.toggle("on", !!(name && FAV.set.has(name)));
    const jam = !solo && !!(link && link.ready);
    document.body.classList.toggle("locked", jam && song);
    const dj = $("dj-quick"); if(dj) dj.hidden = jam; $("hpDj").hidden = jam;
  }, 250);
  // ---------------------------------------------------------------- the engine: its light up top, from the start
  // (before the jam too), and the one download that puts Pick Jam into the computer while it is not there
  (function enginePill(){
    if(!window.JamNativeUI || !JamNativeUI.pill || !$("engPill")) return;
    // the full light (start, tips) in Nastavení; up top only a small one: online, connecting, off
    $("engPill").append(JamNativeUI.pill(() => link, {offHelp: "Pick Jam z ikony na ploše zapne engine sám.",
      onState: (k) => { const l = $("engLed"); if(!l) return; l.className = "eled " + (k === "on" || k === "ready" ? "on" : k === "off" ? "" : "wait");
        l.querySelector("span").textContent = k === "on" ? "Engine online" : k === "ready" ? "Engine online" : k === "off" ? "Engine vypnutý" : "Engine připojuji…"; }}));
    $("engLed").onclick = () => { const b = $("setBtn"); if(b) b.click(); };
  })();
  window.JamGuest = {link: () => link, mix: () => mix, amp: () => amp, ctx: () => ctx, song: () => S, chat: () => chat, input: () => inGain, solo: () => solo, ctl: (o) => ctl(o)};
  boot();
})();
