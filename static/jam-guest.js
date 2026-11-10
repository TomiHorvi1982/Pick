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

  let me = {user: null, login: false, host: "Kamarád"}, ctx = null, link = null, chat = null, mix = null, amp = null;
  let inStream = null, inSrc = null, inSplit = null, inSel = null, inGain = null, monGain = null, monPan = null, myOut = null, meterA = null;
  let songs = [], S = null, loaded = null, loading = null, info = null, lyrics = [], view = "lyr", syncing = false;
  const MIX = {};                               // per song: {stem: {v, p, m, s}}

  // ---------------------------------------------------------------- in: who, then the sound card
  async function boot(){
    try{ me = await getJ("/g/me"); }catch(e){}
    $("outBtn").hidden = !me.login;
    if(me.login && !me.user){ $("login").hidden = false; $("lName").focus(); return; }
    if(!me.login && !K){ document.body.innerHTML = "<div style='padding:40px'>Tenhle odkaz je neúplný — popros kamaráda o nový.</div>"; return; }
    showJoin();
  }
  function showJoin(){
    $("login").hidden = true; $("join").hidden = false;
    $("joinHi").innerHTML = `${me.user && me.login ? `Ahoj <b>${esc(me.user)}</b>! ` : ""}${esc(me.host)} tě zve na společné hraní. Povol zvukovku (mikrofon) — Chrome se zeptá. Kamera není nutná.`;
    if(!me.on) $("joinErr").textContent = `${me.host} teď jam nemá zapnutý — jakmile ho zapne, připoj se.`;
  }
  $("loginF").onsubmit = async (e) => {
    e.preventDefault(); $("lErr").textContent = "";
    const r = await fetch("/login", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: $("lName").value, password: $("lPass").value})});
    const d = await r.json().catch(() => ({}));
    if(!r.ok){ $("lErr").textContent = d.error || "Nepodařilo se."; return; }
    me = await getJ("/g/me"); $("outBtn").hidden = false; showJoin();
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
    try{ me = await getJ("/g/me"); }catch(e){}
    if(!me.on){ $("joinErr").textContent = `${me.host} teď jam nemá zapnutý — jakmile ho zapne, zkus to znovu.`; return; }
    try{ const probe = await navigator.mediaDevices.getUserMedia({audio: true}); probe.getTracks().forEach(t => t.stop()); }
    catch(e){ $("joinErr").innerHTML = micHelp(e); return; }
    let camOk = true, camNote = "";
    try{ const v = await navigator.mediaDevices.getUserMedia({video: true}); v.getTracks().forEach(t => t.stop()); }
    catch(e){ camOk = false; camNote = camHelp(e); }
    ctx = new AudioContext({latencyHint: 0});
    await devices();
    // his guitar: in → gain → the sound (clean / print / amp) → out: heard here (monitor, pan) and sent to the host
    inGain = ctx.createGain(); amp = new JamAmp(ctx, api); inGain.connect(amp.input);
    myOut = ctx.createGain(); amp.output.connect(myOut);
    monGain = ctx.createGain(); monPan = ctx.createStereoPanner(); myOut.connect(monGain); monGain.connect(monPan); monPan.connect(ctx.destination);
    meterA = ctx.createAnalyser(); meterA.fftSize = 512; inGain.connect(meterA);
    mix = new JamMix(ctx, api, ctx.destination);
    link = new JamLink({role: "guest", ctx, output: ctx.destination, onEvent: event});
    chat = JamChat({root: $("chat"), me: "guest", name: me.host || "Kamarád", send: (o) => link && link.send(o),
      visible: () => $("join").hidden && document.visibilityState === "visible" && document.hasFocus(),
      onUnread: (n) => { document.title = n ? `(${n}) Pick Jam — nová zpráva` : "Pick Jam"; }});
    await link.start(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${withK("/ws")}`);
    try{ await useGuitar(); }
    catch(e){ $("inDev").value = ""; store("in", ""); await useGuitar(); }
    link.setInput(myOut);
    const b = store("buf"); if(b){ $("buf").value = b; applyBuf(); }
    if(camOk){ try{ showMe(await link.setCamera(true, $("camDev").value)); $("camBtn").classList.add("on"); }catch(e){} }
    $("join").hidden = true; chat.check();
    if(camNote) toast(camNote, 15000);
    applyMine(); renderRack(); restoreSound();
    loadSongs();
    setInterval(ui, 120); setInterval(sync, 400);
  }
  $("go").onclick = start;
  function showMe(track){ const v = $("vMe"); if(track){ v.srcObject = new MediaStream([track]); v.hidden = false; } else { v.srcObject = null; v.hidden = true; } }
  function applyBuf(){ const v = $("buf").value; store("buf", v); link.setBuffer(v === "auto" ? 3 : +v, v === "auto"); }

  function event(kind, v){
    if(kind === "peer"){ $("state").textContent = v ? `${me.host} je tu — spojuji…` : `čekám na ${me.host} (má jam zapnutý?)`; if(!v && mix) mix.stop(); }
    if(kind === "conn"){ $("dot").classList.toggle("on", v === "connected"); if(v === "connected") $("state").textContent = `spojeno s ${me.host}`; if(v === "failed") $("state").textContent = "spojení se nepovedlo — zkouším znovu"; }
    if(kind === "media") showMedia(v);
    if(kind === "mode") $("mode").textContent = v === "low" ? "Režim: nízké zpoždění (jako SonoBus) — kytary jdou nekomprimovaně" : "Režim: jako hovor (Opus) — stabilnější na horší síti";
    if(kind === "msg" && v && v.names && v.names.host && v.names.host !== me.host){ me.host = v.names.host; renderRack(); }
    if(kind === "msg" && v.type === "song") onSong(v);
    else if(kind === "msg" && chat) chat.onMsg(v);
  }
  function showMedia(m){
    const big = $("vBig"), cam = $("vCam");
    const scr = m.screen && link && link.remoteShare ? m.screen : null;    // what the host says, not whether frames come (a still screen sends none)
    if(scr){ if(big.srcObject !== scr){ big.srcObject = scr; big.play().catch(() => {}); } $("pz").hidden = false; $("ztools").hidden = false; $("pickNone").hidden = true; }
    else { $("pz").hidden = true; $("ztools").hidden = true; $("pickNone").hidden = false; }
    if(m.camera){ if(cam.srcObject !== m.camera) cam.srcObject = m.camera; cam.hidden = false; } else cam.hidden = true;
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
  $("zFull").onclick = () => { document.fullscreenElement ? document.exitFullscreen() : $("vPick").requestFullscreen().catch(() => {}); };
  $("pz").addEventListener("wheel", (e) => { if(!e.ctrlKey && !e.metaKey) return; e.preventDefault(); setZoom(zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY); }, {passive: false});
  $("pz").onpointerdown = (e) => { const pz = $("pz"); if(zoom <= 1) return; pz.setPointerCapture(e.pointerId); pz.classList.add("drag");
    const x0 = e.clientX, y0 = e.clientY, l0 = pz.scrollLeft, t0 = pz.scrollTop;
    pz.onpointermove = (ev) => { pz.scrollLeft = l0 - (ev.clientX - x0); pz.scrollTop = t0 - (ev.clientY - y0); };
    pz.onpointerup = () => { pz.onpointermove = null; pz.classList.remove("drag"); }; };
  $("pz").ondblclick = (e) => setZoom(zoom > 1 ? 1 : 2, e.clientX, e.clientY);
  document.addEventListener("keydown", (e) => { if(view === "pick" && (e.key === "f" || e.key === "F") && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) $("zFull").click(); });

  // the mix folded away, for more of the text or the shared screen
  const foldMix = (on) => { $("rack").classList.toggle("folded", on); $("mixFold").textContent = on ? "Mix ▸" : "Mix ▾"; store("mixFold", on); };
  foldMix(!!store("mixFold")); $("mixFold").onclick = () => foldMix(!$("rack").classList.contains("folded"));

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
    const h = Math.max(90, Math.min(window.innerHeight * 0.75, h0 - dy)); $("chat").style.setProperty("--jc-h", h + "px"); store("chatH", Math.round(h)); }});

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
    try{ songs = (await getJ("/g/songs")).songs || []; }catch(e){ $("slist").innerHTML = `<div class="bad">${esc(e.message)}</div>`; return; }
    songs.forEach(s => { s.tune = tuneName(s.tuning); s.key = fold(s.artist + " " + s.title + " " + s.name); });
    songs.sort((a, b) => fold(a.artist + a.title).localeCompare(fold(b.artist + b.title)));
    try{ SB.playlists = (await getJ("/g/playlists")).playlists || []; }catch(e){}
    document.querySelectorAll("#cats [data-c]").forEach(b => b.classList.toggle("on", b.dataset.c === SB.cat));
    paintSongs();
  }
  const art = (name) => withK(`/g/art?name=${encodeURIComponent(name)}`);
  const rowHtml = (s) => `<button class="row ${S && S.name === s.name ? "cur" : ""} ${PV.name === s.name ? "pv" : ""}" data-n="${esc(s.name)}" title="Klik = náhled jen pro tebe · dvojklik = hrát spolu">
      <img class="cv" loading="lazy" src="${art(s.name)}" onerror="this.removeAttribute('src')" alt=""><span class="tx"><b>${esc(s.title)}</b><span>${esc(s.artist || "")}${s.duration ? " · " + mmss(s.duration) : ""}</span></span>
      <span class="tn" title="Ladění">${esc(s.tune.replace(" standard", " std"))}</span></button>`;
  function groupsOf(list){
    if(SB.cat === "band"){ const m = new Map(); list.forEach(s => { const k = s.artist || "Neznámý interpret"; if(!m.has(k)) m.set(k, []); m.get(k).push(s); });
      return [...m].sort((a, b) => fold(a[0]).localeCompare(fold(b[0]))); }
    if(SB.cat === "tune"){ const m = new Map(); list.forEach(s => { if(!m.has(s.tune)) m.set(s.tune, []); m.get(s.tune).push(s); });
      return [...m].sort((a, b) => b[1].length - a[1].length); }
    if(SB.cat === "pl"){ const by = new Map(list.map(s => [s.name, s]));
      return SB.playlists.map(p => [p.name, p.songs.map(n => by.get(n)).filter(Boolean)]).filter(g => g[1].length); }
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
      const auto = words.length && got.length <= 40;
      box.innerHTML = groups.map(([g, list]) => { const open = auto || SB.open.has(SB.cat + ":" + g);
        return `<button class="grp" data-g="${esc(g)}"><span class="ar">${open ? "▾" : "▸"}</span>${SB.cat === "band" ? `<img class="cv" loading="lazy" src="${art(list[0].name)}" onerror="this.removeAttribute('src')" alt="">` : ""}<span>${esc(g)}</span><span class="n">${list.length}</span></button>`
          + (open ? list.slice(0, 200).map(rowHtml).join("") : ""); }).join("") || `<div class="muted">Nic nenalezeno.</div>`;
    }
    box.querySelectorAll("[data-g]").forEach(b => b.onclick = () => { const k = SB.cat + ":" + b.dataset.g; SB.open.has(k) ? SB.open.delete(k) : SB.open.add(k); paintSongs(); });
    box.querySelectorAll(".row[data-n]").forEach(b => { b.onclick = () => preview(b.dataset.n); b.ondblclick = () => playTogether(b.dataset.n); });
    const more = $("more"); if(more) more.onclick = () => { SB.shown += 100; paintSongs(); };
  }
  let findT = 0;
  $("find").oninput = () => { clearTimeout(findT); findT = setTimeout(() => { SB.shown = 60; paintSongs(); }, 150); };
  document.querySelectorAll("#cats [data-c]").forEach(b => b.onclick = () => { SB.cat = b.dataset.c; store("cat", SB.cat); SB.shown = 60;
    document.querySelectorAll("#cats [data-c]").forEach(x => x.classList.toggle("on", x === b)); paintSongs(); });

  // a song heard before it is picked: only here, the whole song in one small file, to move around in
  const PV = {name: null}, pa = $("pvAudio");
  function preview(name){
    const s = songs.find(x => x.name === name); if(!s) return;
    PV.name = name; $("pvBox").hidden = false;
    $("pvCv").src = art(name); $("pvT").textContent = s.title; $("pvA2").textContent = `${s.artist || ""} · ${s.tune}`;
    $("pvGo").textContent = `Hrát spolu s ${me.host}`;
    pa.src = withK(`/g/preview?name=${encodeURIComponent(name)}`); pa.volume = 0.9;
    pa.play().catch(() => {}); $("pvPlay").textContent = "…";
    paintSongs();
  }
  pa.onplaying = () => { $("pvPlay").textContent = "❚❚"; }; pa.onpause = () => { $("pvPlay").textContent = "▶"; };
  pa.ontimeupdate = () => { if(pa.duration){ $("pvSeek").value = Math.round(pa.currentTime / pa.duration * 1000); $("pvTime").textContent = `${mmss(pa.currentTime)} / ${mmss(pa.duration)}`; } };
  $("pvPlay").onclick = () => { pa.paused ? pa.play().catch(() => {}) : pa.pause(); };
  $("pvSeek").oninput = () => { if(pa.duration) pa.currentTime = $("pvSeek").value / 1000 * pa.duration; };
  function playTogether(name){ pa.pause(); ctl({do: "load", name}); $("songSt").textContent = `${me.host} načítá píseň…`; }
  $("pvGo").onclick = () => PV.name && playTogether(PV.name);
  const ctl = (o) => link && link.send({type: "ctl", ...o});

  // ---------------------------------------------------------------- the song as the host's Pick has it
  function hostPos(perf){ return S.pos + (link.remoteNow(perf) - S.at) / 1000 * (S.speed || 1); }
  function posNow(){ if(!S || !link) return 0; if(!S.playing || !link.clock.samples.length) return S.pos; return hostPos(performance.now()); }
  function onSong(m){
    const was = S; S = m;
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
    const want = stems.length ? stems : (info.channels || []).map(c => c.stem);
    try{
      const ok = await mix.load(name, want, (d, of) => { if(loading === name) $("songSt").textContent = `Stahuji stopy ${d}/${of}…`; });
      if(!ok || loading !== name) return;
    }catch(e){ if(loading === name){ $("songSt").textContent = e.message; loading = null; } return; }
    loaded = name; loading = null;
    applyMix(); renderRack();
    $("songSt").textContent = "Stopy připravené — hraje u obou zároveň.";
    sync();
  }
  // play here when and where the host plays
  async function sync(){
    if(!S || !link || !mix || loaded !== S.name || syncing) return;
    if(!link.ready || !link.clock.samples.length) return;
    if(!S.playing){ if(mix.running) mix.stop(); return; }
    const ts = ctx.getOutputTimestamp(); if(!ts.performanceTime) return;
    const want = hostPos(ts.performanceTime), speed = S.speed || 1, key = S.key || 0, sp = S.stemPitch || {};
    if(mix.running && mix.same(speed, key, sp)){
      const mine = mix.songAt(ts.contextTime); const d = mine == null ? 1e9 : (mine - want) * 1000;
      if(Math.abs(d) < (mix.stretchy ? 30 : 15)) return;
    }
    if(want >= mix.duration) return;
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
  const MY = Object.assign({hv: 1, hp: 0, hm: false, ing: 0, mv: 1, mp: 0, mm: false, drive: 0}, store("mine") || {});
  function applyMine(){
    if(!link || !amp) return;
    link.setVolume(MY.hm ? 0 : MY.hv); link.setPan(MY.hp);
    inGain.gain.value = Math.pow(10, MY.ing / 20); amp.setInputDb(MY.ing); amp.setDrive(MY.drive);
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
      ${Math.abs(a - s0) > 1 ? `<path d="${arc(Math.min(s0, a), Math.max(s0, a))}" stroke="var(--sc,#8b5cf6)" stroke-width="3.2" fill="none" stroke-linecap="round"/>` : ""}
      <circle cx="17" cy="17" r="9.5" fill="#23232e" stroke="#3a3a48"/><line x1="17" y1="17" x2="${17 + (hx - 17) * 0.62}" y2="${17 + (hy - 17) * 0.62}" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>`;
  }
  // a knob: key, label, min, max, value, text, reset value, centred (pan)
  const knob = (k, label, min, max, val, txt, def, center) => `<div class="kn" data-kn="${k}" data-min="${min}" data-max="${max}" data-def="${def}" data-c="${center ? 1 : 0}" title="${label} — táhni nahoru/dolů, dvojklik = výchozí">
      ${knobSvg((val - min) / (max - min), center)}<span class="kl">${label}</span><span class="kv">${txt}</span></div>`;
  const fader = (k, v, maxDb) => { const p = posOfDb(lin2db(v)) * 100;
    return `<div class="fb"><div class="sc">${SCALE.filter(d => d <= maxDb).map(d => `<i style="bottom:calc(${posOfDb(d) * 100}% * (164 - 8) / 164 + 4px)">${d > 0 ? "+" + d : d}</i>`).join("")}</div>
      <div class="mt"><b><i></i><em></em></b><b><i></i><em></em></b></div>
      <div class="ft" data-ft="${k}" data-max="${maxDb}" title="Hlasitost — táhni, kolečko = jemně, dvojklik = 0 dB"><div class="u" style="height:calc(${p}% * (164 - 8) / 164)"></div><div class="cap" style="bottom:calc(${p}% * (164 - 8) / 164 + 4px)"></div></div></div>`; };
  function renderRack(){
    const rack = $("rack"); if(!mix) return;
    const stemStrip = (c) => `<div class="strip" data-st="${esc(c.stem)}" style="--sc:${COL[c.stem] || "#8b5cf6"}"><div class="nm" title="${esc(c.stem)}">${esc(SNAME[c.stem] || c.stem)}</div><div class="sub">stopa</div>
        <div class="knobs">${knob("t", "GAIN", -12, 12, c.t || 0, fmtDb(c.t || 0) + " dB", 0)}${knob("p", "PAN", -1, 1, c.p, panTxt(c.p), 0, true)}</div>
        <div class="clip"></div>${fader("v", c.v, 6)}<div class="dbv">${fmtDb(lin2db(c.v))} dB</div>
        <div class="ms"><button class="m ${c.m ? "on" : ""}" data-k="m" title="Ztlumit (jen u tebe)">M</button><button class="s ${c.s ? "on" : ""}" data-k="s" title="Sólo (jen u tebe)">S</button></div></div>`;
    rack.innerHTML = mix.ch.map(stemStrip).join("") + `<div class="strip host" data-st="@host" style="--sc:#6ee7a0"><div class="nm">${esc(me.host)}</div><div class="sub">jeho kytara</div>
        <div class="knobs">${knob("hp", "PAN", -1, 1, MY.hp, panTxt(MY.hp), 0, true)}</div>
        <div class="clip"></div>${fader("hv", MY.hv, 6)}<div class="dbv">${fmtDb(lin2db(MY.hv))} dB</div>
        <div class="ms"><button class="m ${MY.hm ? "on" : ""}" data-k="hm" title="Ztlumit">M</button></div></div>
      <div class="strip me" data-st="@me" style="--sc:#a78bfa"><div class="nm">${esc(me.user || "Já")}</div><div class="sub">moje kytara</div>
        <button class="snd" id="sndBtn" title="Otisk, zesilovač, vlastní otisk">🎸 ${esc(amp ? amp.label : "Čistý zvuk")}</button>
        <div class="knobs">${knob("ing", "GAIN", -12, 24, MY.ing, fmtDb(MY.ing) + " dB", 0)}${knob("drive", "DRIVE", -24, 12, MY.drive, fmtDb(MY.drive), 0)}${knob("mp", "PAN", -1, 1, MY.mp, panTxt(MY.mp), 0, true)}</div>
        <div class="clip"></div>${fader("mv", MY.mv, 6)}<div class="dbv">${fmtDb(lin2db(MY.mv))} dB</div>
        <div class="ms"><button class="m ${MY.mm ? "on" : ""}" data-k="mm" title="Neslyšet se">M</button></div></div>`;
    rack.querySelectorAll(".strip").forEach(wireStrip);
    const sb = $("sndBtn"); if(sb) sb.onclick = openSound;
  }
  function putVal(id, k, val){
    if(id[0] === "@"){ MY[k] = val; applyMine(); return; }
    const all = mixOf(loaded), o = all[id] = all[id] || {}; o[k] = val; store("mix-" + loaded, all); mix.set(id, {[k]: val});
  }
  const getVal = (id, k) => id[0] === "@" ? MY[k] : (mix.ch.find(x => x.stem === id) || {})[k];
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
      kn.onwheel = (e) => { e.preventDefault(); set((+getVal(id, k) || 0) + (e.deltaY < 0 ? 1 : -1) * (max - min) / (center ? 50 : 48)); };
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
      ft.onwheel = (e) => { e.preventDefault(); const d = lin2db(+getVal(id, k)); setDb((isFinite(d) ? d : -60) + (e.deltaY < 0 ? 0.5 : -0.5)); };
    });
    const clip = st.querySelector(".clip"); if(clip) clip.onclick = () => { clip.classList.remove("on"); st._clip = false; };
  }
  // the meters, as smooth as the screen: falling slowly, a peak held a moment, clip until clicked
  function peakOf(an){ if(!an) return 0; const a = new Float32Array(an.fftSize); an.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); return p; }
  function meters(){
    if(mix && link) $("rack").querySelectorAll(".strip").forEach(st => {
      const id = st.dataset.st;
      const lr = id === "@host" ? [link.level(), link.level()] : id === "@me" ? [peakOf(meterA), peakOf(meterA)] : mix.levels(id);
      const bars = st.querySelectorAll(".mt b"); st._m = st._m || [{v: 0, pk: 0, t: 0}, {v: 0, pk: 0, t: 0}];
      lr.forEach((p, i) => { const m = st._m[i], pos = posOfDb(lin2db(p)), now = performance.now();
        m.v = Math.max(pos, m.v - 0.025); if(pos >= m.pk || now - m.t > 1200){ m.pk = pos; m.t = now; }
        if(bars[i]){ bars[i].firstElementChild.style.height = m.v * 100 + "%"; bars[i].lastElementChild.style.bottom = m.pk * 100 + "%"; }
        if(p >= 0.99 && !st._clip){ st._clip = true; const c = st.querySelector(".clip"); if(c) c.classList.add("on"); } });
    });
    requestAnimationFrame(meters);
  }
  requestAnimationFrame(meters);

  // ---------------------------------------------------------------- text, chords, sections
  let lyrIdx = -2;
  function paintLyrics(){
    $("vLyr").innerHTML = lyrics.length ? lyrics.map((l, i) => `<p data-i="${i}">${esc(l.text) || "&nbsp;"}</p>`).join("") : `<p class="muted">K téhle písni text nemám.</p>`;
    lyrIdx = -2;
  }
  function lyricsAt(pos){
    if(!lyrics.length) return;
    let i = -1; for(let k = 0; k < lyrics.length; k++){ if(lyrics[k].t <= pos + 0.15) i = k; else break; }
    if(i === lyrIdx) return; lyrIdx = i;
    $("vLyr").querySelectorAll("p").forEach((p, k) => { p.className = k === i ? "now" : k < i ? "past" : ""; });
    const cur = $("vLyr").querySelector("p.now"); if(cur) cur.scrollIntoView({block: "center", behavior: "smooth"});
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
    const secs = (info && info.sections) || [], dur = (S && S.dur) || (secs.length ? secs[secs.length - 1].end : 0);
    $("secs").innerHTML = dur ? secs.map((s, k) => `<i title="${esc(s.name)}" style="width:${(s.end - s.start) / dur * 100}%;background:${esc(s.color || "#666")};margin-left:${k === 0 ? s.start / dur * 100 : 0}%"></i>`).join("") : "";
    const sel = $("secSel");
    getJ(`/g/sections?name=${encodeURIComponent(S.name)}`).then(d => {
      const list = (d.sections || []).slice(0, 12);
      sel.innerHTML = list.length ? list.map(s => `<option value="${s.start}|${s.end}">${mmss(s.start)}–${mmss(s.end)}${s.score != null ? " · zřetelnost " + Math.round(s.score * 100) + " %" : ""}</option>`).join("")
        : `<option value="">V téhle písni jsem nenašel úsek se zřetelnou kytarou</option>`;
    }).catch(() => { sel.innerHTML = `<option value="">—</option>`; });
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
  document.querySelectorAll(".views [data-v]").forEach(b => b.onclick = () => {
    view = b.dataset.v; document.querySelectorAll(".views [data-v]").forEach(x => x.classList.toggle("on", x === b));
    $("vLyr").hidden = view !== "lyr"; $("vChords").hidden = view !== "chords"; $("vPick").hidden = view !== "pick"; lyrIdx = -2;
  });

  // ---------------------------------------------------------------- the sound of his guitar
  let prints = null;
  const ampSel = {model: "", ir: "", mname: "", iname: ""};
  function openSound(){ $("sndD").hidden = false; $("setD").hidden = true; paintPrints(); }
  document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => { $(b.dataset.close).hidden = true; });
  $("setBtn").onclick = () => { $("setD").hidden = !$("setD").hidden; $("sndD").hidden = true; };
  document.querySelectorAll(".tabs [data-st]").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tabs [data-st]").forEach(x => x.classList.toggle("on", x === b));
    $("stPrints").hidden = b.dataset.st !== "prints"; $("stAmps").hidden = b.dataset.st !== "amps"; $("stOwn").hidden = b.dataset.st !== "own";
    if(b.dataset.st === "amps"){ findAmps("model"); findAmps("ir"); }
  });
  async function paintPrints(){
    if(!prints){ $("plist").innerHTML = `<div class="muted">Načítám otisky…</div>`; try{ prints = (await getJ("/g/prints")).prints || []; }catch(e){ $("plist").innerHTML = `<div class="bad">${esc(e.message)}</div>`; return; } }
    const words = fold($("pFind").value).split(/\s+/).filter(Boolean);
    const mineP = prints.filter(p => p.friend && p.friend === me.user), rest = prints.filter(p => !(p.friend && p.friend === me.user));
    const ok = (p) => { const t = fold([p.name, p.artist, p.title, p.category, p.core].join(" ")); return words.every(w => t.includes(w)); };
    const row = (p) => `<button data-song="${esc(p.song)}" data-id="${esc(p.id)}" data-name="${esc(p.name)}"><b>${esc(p.name)}</b><span>${esc(p.artist || "")} · ${esc(p.title || p.song)}${p.match != null ? " · shoda " + p.match + " %" : ""}</span></button>`;
    const a = mineP.filter(ok), b = rest.filter(ok).slice(0, 200);
    $("plist").innerHTML = (a.length ? `<div class="muted">MOJE</div>` + a.map(row).join("") + `<div class="muted">KNIHOVNA</div>` : "") + (b.map(row).join("") || `<div class="muted">Nic nenalezeno.</div>`);
    $("plist").querySelectorAll("[data-id]").forEach(x => x.onclick = () => useSound({kind: "print", song: x.dataset.song, id: x.dataset.id, name: x.dataset.name}));
  }
  $("pFind").oninput = () => paintPrints();
  async function findAmps(kind){
    const q = kind === "ir" ? $("iFind").value : $("aFind").value, box = kind === "ir" ? $("ilist") : $("alist");
    try{
      const d = await getJ(`/g/amps?kind=${kind}&q=${encodeURIComponent(q)}`);
      box.innerHTML = d.amps.map(a => `<button data-f="${esc(a.file)}" data-n="${esc(a.name)}" class="${(kind === "ir" ? ampSel.ir : ampSel.model) === a.file ? "cur" : ""}"><b>${esc(a.name)}</b></button>`).join("") || `<div class="muted">Nic nenalezeno.</div>`;
      box.querySelectorAll("[data-f]").forEach(x => x.onclick = () => {
        if(kind === "ir"){ ampSel.ir = x.dataset.f; ampSel.iname = x.dataset.n; } else { ampSel.model = x.dataset.f; ampSel.mname = x.dataset.n; }
        findAmps(kind);
        $("ampPick").textContent = [ampSel.mname || "bez zesilovače", ampSel.iname || "bez kabinetu"].join(" + ");
        useSound({kind: "amp", model: ampSel.model, ir: ampSel.ir, name: [ampSel.mname, ampSel.iname].filter(Boolean).join(" + ")});
      });
    }catch(e){ box.innerHTML = `<div class="bad">${esc(e.message)}</div>`; }
  }
  let ampT = 0;
  $("aFind").oninput = () => { clearTimeout(ampT); ampT = setTimeout(() => findAmps("model"), 250); };
  $("iFind").oninput = () => { clearTimeout(ampT); ampT = setTimeout(() => findAmps("ir"), 250); };
  async function useSound(s, quiet){
    $("sndNow").textContent = "Načítám…";
    try{
      if(s.kind === "print") await amp.setPrint(s.song, s.id, s.name);
      else if(s.kind === "amp") await amp.setAmp(s.model, s.ir, s.name);
      else amp.clear();
      store("sound", s);
      if(!quiet) toast(amp.label);
    }catch(e){ toast(e.message); }
    $("sndNow").textContent = amp.label; renderRack();
  }
  function restoreSound(){ const s = store("sound"); if(s && s.kind !== "clean") useSound(s, true); }
  $("sndClean").onclick = () => useSound({kind: "clean"});
  $("drive").oninput = (e) => { MY.drive = +e.target.value; applyMine(); renderRack(); };

  // his own print: the stretch played along with the song, clean — sent to the host's Mac, a print made of it
  let rec = null;
  $("recBtn").onclick = async () => {
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
    if(!link) return;
    const d = link.delay(), r = link.route();
    $("lat").innerHTML = link.ready ? `${d.total} ms<small>síť ${d.net} · paměť ${d.buf} · výstup ${d.out}</small>` : "";
    $("lat").style.color = d.total <= 30 ? "var(--ok)" : d.total <= 50 ? "var(--warn)" : "var(--bad)";
    $("route").textContent = r ? `Spojení: ${r}${link.rxStats.under ? ` · výpadky ${link.rxStats.under}` : ""}${link.rxStats.lost ? ` · ztracené balíčky ${link.rxStats.lost}` : ""}` : "";
    const p = peakOf(meterA); $("meter").style.width = Math.min(100, p * 100) + "%"; $("meter").style.background = p > 0.95 ? "var(--bad)" : "var(--ok)";
    if(S && S.name){
      const pos = posNow(), dur = S.dur || mix.duration || 0;
      $("time").textContent = `${mmss(pos)} / ${mmss(dur)}`;
      $("head").style.left = dur ? Math.min(100, pos / dur * 100) + "%" : "0";
      const lp = $("loopR"), L = S.loop;
      if(L && dur){ lp.hidden = false; lp.style.left = L.start / dur * 100 + "%"; lp.style.width = (L.end - L.start) / dur * 100 + "%"; lp.title = `Smyčka ${mmss(L.start)}–${mmss(L.end)} (zapnutá v Picku)`; } else lp.hidden = true;
      if(view === "lyr") lyricsAt(pos); else if(view === "chords") chordsAt(pos);
    }
  }
  window.JamGuest = {link: () => link, mix: () => mix, amp: () => amp, ctx: () => ctx, song: () => S, chat: () => chat};
  boot();
})();
