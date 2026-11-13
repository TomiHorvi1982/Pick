// Pick Jam — the host's end, in Pick: start the room (Tailscale puts it on the tailnet), send the friend the link,
// then the two of you: cameras, Pick's screen shared, talking, and the guitars — the SonoBus way (least delay)
// or as a call. The guitar sent is Pick's own sound (the print, the NAM, the effects); the friend's comes into
// Pick's mix. The song playing in Steems goes over as a message: the friend's page plays the same backing at
// the same moment (static/jam-guest.js).
const Jam = (() => {
  let box = null, link = null, st = {on: false, url: null}, songT = 0, lastSong = "", uiT = 0, unhook = null, chat = null, chatEl = null, unreadN = 0;
  let names = {host: "", guest: "Kamarád"}, jf = null, lastSent = null, friendNow = null, stageWas = false;
  const cfg = (() => { try{ return JSON.parse(localStorage.getItem("pick-jam") || "{}"); }catch(e){ return {}; } })();
  const keep = () => { try{ localStorage.setItem("pick-jam", JSON.stringify(cfg)); }catch(e){} };
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  // the friend's guide (a page on a link): Tailscale, the invite, the sound card, joining in Chrome
  const GUIDE = "https://claude.ai/artifact/5rqyJzeqvuF2nLnYkry23H";
  const guideText = () => `Ahoj! Pojď si zahrát přes Pick Jam. Tady je návod, co nainstalovat a jak se připojit:\n${GUIDE}\n\nAž budeš mít Tailscale, pošlu ti pozvánku a pak odkaz na jam.`;
  function copyGuide(){ navigator.clipboard.writeText(guideText()).then(() => showToast("Návod zkopírovaný — vlož ho kamarádovi do zprávy"), () => showToast(GUIDE)); }
  const CSS = `
  .jam{position:fixed;right:18px;bottom:18px;z-index:9300;width:400px;max-height:calc(100vh - 36px);display:flex;flex-direction:column;border-radius:16px;
    background:rgba(18,18,27,.97);border:1px solid rgba(255,255,255,.1);box-shadow:0 20px 60px rgba(0,0,0,.6);color:#ececf3;font-size:13px;overflow:hidden}
  .jam.big{width:min(1100px,calc(100vw - 36px))}
  .jam-h{display:flex;align-items:center;gap:8px;padding:10px 12px;cursor:move;user-select:none;border-bottom:1px solid rgba(255,255,255,.07)}
  .jam-h b{font-size:14px;letter-spacing:.02em}.jam-h .sp{flex:1}
  .jam-dot{width:9px;height:9px;border-radius:50%;background:#666}.jam-dot.on{background:#6ee7a0;box-shadow:0 0 8px #6ee7a0}
  .jam-h button{background:none;border:0;color:#9a9ab0;cursor:pointer;padding:4px 6px;border-radius:6px}.jam-h button:hover{color:#fff;background:rgba(255,255,255,.06)}
  .jam-b{overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
  .jam-v{position:relative;background:#000;border-radius:12px;overflow:hidden;aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;color:#777}
  .jam-v video{width:100%;height:100%;object-fit:cover}
  .jam-v video.me{position:absolute;right:8px;bottom:8px;width:28%;height:auto;aspect-ratio:16/9;border-radius:8px;border:1px solid rgba(255,255,255,.2);transform:scaleX(-1)}
  .jam-row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .jam-b button.jb{background:#232331;color:#ececf3;border:1px solid rgba(255,255,255,.1);border-radius:9px;padding:6px 10px;cursor:pointer;font:inherit}
  .jam-b button.jb.on{background:var(--accent,#8b5cf6);border-color:var(--accent,#8b5cf6);color:#0b0b12;font-weight:700}
  .jam-b button.go{background:var(--accent,#8b5cf6);color:#0b0b12;font-weight:800;border:0;padding:10px 14px;border-radius:10px;cursor:pointer}
  .jam-link{display:flex;gap:6px}.jam-link input{flex:1;min-width:0;background:#0e0e14;color:#cfe;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:6px 8px;font:12px ui-monospace,monospace}
  .jam-l{display:flex;align-items:center;gap:8px}.jam-l span{min-width:88px;color:#9a9ab0}.jam-l input[type=range],.jam-l select{flex:1;min-width:0}
  .jam-nat .jam-l{margin-top:6px}.jam-nat .jam-l>div{flex:1;display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-width:0}.jam-nat select{flex:1;min-width:0}
  .jam-l select{background:#0e0e14;color:#ececf3;border:1px solid rgba(255,255,255,.12);border-radius:7px;padding:4px 6px}
  .jam-lat{font-variant-numeric:tabular-nums;font-weight:800;font-size:15px;white-space:nowrap;min-width:200px;text-align:right;overflow:hidden}.jam-lat small{font-weight:500;color:#9a9ab0;font-size:11px;margin-left:6px}
  #jam-route,#jam-gtr{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:18px}
  .jam .jam-row{flex-wrap:nowrap}
  .jam-m{height:5px;border-radius:3px;background:#222;overflow:hidden;flex:1}.jam-m i{display:block;height:100%;width:0;background:#6ee7a0}
  .jam-note{color:#9a9ab0;font-size:12px;line-height:1.45}.jam-note b{color:#ececf3}.jam-warn{color:#ffd166}.jam-err{color:#ff8a8a}
  .jam-guide summary{cursor:pointer;font-weight:700;margin:6px 0}.jam-guide ol{margin:4px 0 8px;padding-left:20px;display:flex;flex-direction:column;gap:6px;font-size:13px;line-height:1.45}
  .jam-guide a,#settings-overlay a[href*="5rqyJzeqvuF2nLnYkry23H"]{color:var(--accent,#a78bfa)}
  .jam-guide .jg-h{font-weight:800;font-size:12px;letter-spacing:.06em;text-transform:uppercase;opacity:.75;margin-top:8px}
  .jam-dom{display:flex;flex-direction:column;gap:10px;margin-top:14px;padding:12px;border:1px solid rgba(255,255,255,.1);border-radius:12px}
  .jam-dom .jg-h{font-weight:800;font-size:12px;letter-spacing:.06em;text-transform:uppercase;opacity:.8}
  .jd-row{display:grid;grid-template-columns:24px 1fr auto;gap:10px;align-items:start;font-size:13px}
  .jd-n{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:12px;background:rgba(139,92,246,.25)}
  .jd-in{display:block;width:100%;margin-top:4px;background:#0e0e14;color:#ececf3;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:6px 8px;font:13px ui-monospace,monospace}
  .jd-st{font-size:12px;margin-top:3px;opacity:.85}.jd-st.ok{color:#6ee7a0}.jd-st.bad{color:#ff8a8a}
  .jam-via{display:flex;gap:4px;flex-wrap:wrap}.jam-via button{font-size:12px!important;padding:5px 9px!important}
  #jam-fader .jf-led{display:flex;flex-direction:column-reverse;gap:2px;width:5px}
  #jam-fader .jf-led i{display:block;height:6%;flex:1;border-radius:1px;background:rgba(255,255,255,.08)}
  #jam-fader .jf-led i.on{background:#6ee7a0}#jam-fader .jf-led i.on.hot{background:#ffd166}#jam-fader .jf-led i.on.clip{background:#ff6b6b}
  #jam-fader .fl-fader-track{cursor:ns-resize;touch-action:none}#jam-fader .fl-pan-knob{cursor:ns-resize;touch-action:none}
  #jam-fader .jf-off{font-size:10px;opacity:.7;text-align:center}
  #jam-fader .jf-snd{width:100%;margin-top:4px}#jam-fader .jf-now{font-size:9px;opacity:.75;text-align:center;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .jam-fs{position:fixed;z-index:9000;width:330px;max-height:78vh;display:flex;flex-direction:column;gap:7px;padding:12px;border-radius:12px;background:#17171f;border:1px solid rgba(255,255,255,.12);box-shadow:0 16px 40px rgba(0,0,0,.6);color:#eee;font-size:12px}
  .jam-fs .fs-hd{display:flex;align-items:center;justify-content:space-between}.jam-fs .fs-x{background:none;border:0;color:#aaa;cursor:pointer;font-size:14px}
  .jam-fs .fs-now{color:#6ee7a0;font-size:11.5px}
  .jam-fs .fs-tabs{display:flex;gap:3px}.jam-fs .fs-tabs button{flex:1;padding:5px 4px;border-radius:7px;border:1px solid rgba(255,255,255,.12);background:none;color:#ccc;cursor:pointer;font-size:11px}
  .jam-fs .fs-tabs button.on{background:var(--accent,#8b5cf6);color:#0b0b12;font-weight:700}
  .jam-fs .fs-q{padding:6px 8px;border-radius:7px;border:1px solid rgba(255,255,255,.12);background:#0e0e14;color:#eee}
  .jam-fs .fs-list{overflow:auto;max-height:30vh;display:flex;flex-direction:column;gap:1px}
  .jam-fs .fs-list button{text-align:left;background:none;border:0;color:#eee;padding:5px 7px;border-radius:6px;cursor:pointer;display:flex;flex-direction:column}
  .jam-fs .fs-list button:hover{background:rgba(255,255,255,.06)}.jam-fs .fs-list button.on{background:rgba(139,92,246,.25)}
  .jam-fs .fs-list span{font-size:10.5px;opacity:.65}.jam-fs .fs-g{font-size:10px;letter-spacing:.08em;text-transform:uppercase;opacity:.55;padding:6px 7px 1px}
  .jam-fs .fs-sls{display:flex;flex-direction:column;gap:4px;border-top:1px solid rgba(255,255,255,.08);padding-top:6px}
  .jam-fs .fs-sl{display:grid;grid-template-columns:72px 1fr 54px;align-items:center;gap:6px}.jam-fs .fs-sl em{font-style:normal;font-variant-numeric:tabular-nums;text-align:right;opacity:.85}
  .jam-fs .fs-sl input{accent-color:var(--accent,#8b5cf6)}.jam-fs .hint{opacity:.6;font-size:10.5px}
  .jam-steps{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:4px}
  .jam.big .jc{--jc-h:320px}
  .jam-badge{position:absolute;top:-5px;right:-5px;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:#ff4d6d;color:#fff;font:700 10.5px/17px system-ui;text-align:center;pointer-events:none;box-shadow:0 0 0 2px #111}
  `;
  { const s = document.createElement("style"); s.textContent = CSS; document.head.appendChild(s); }

  function build(){
    box = document.createElement("div"); box.className = "jam"; box.hidden = true;
    box.innerHTML = `<div class="jam-h"><span class="jam-dot" id="jam-dot"></span><b>Jam</b><span class="jam-note" id="jam-sub"></span><span class="sp"></span>
      <button type="button" id="jam-big" title="Větší / menší">⤢</button><button type="button" id="jam-x" title="Skrýt (jam běží dál)">✕</button></div><div class="jam-b" id="jam-b"></div>`;
    document.body.appendChild(box);
    box.querySelector("#jam-x").onclick = () => { box.hidden = true; };
    box.addEventListener("pointerdown", () => chat && setTimeout(chat.check, 0));
    box.querySelector("#jam-big").onclick = () => { box.classList.toggle("big"); };
    // the header drags the panel
    const h = box.querySelector(".jam-h");
    h.onpointerdown = (e) => { if(e.target.closest("button")) return; const r = box.getBoundingClientRect(), x0 = e.clientX, y0 = e.clientY; h.setPointerCapture(e.pointerId);
      const mv = (ev) => { box.style.right = "auto"; box.style.bottom = "auto"; box.style.left = Math.max(0, r.left + ev.clientX - x0) + "px"; box.style.top = Math.max(0, r.top + ev.clientY - y0) + "px"; };
      const up = () => { h.removeEventListener("pointermove", mv); h.removeEventListener("pointerup", up); };
      h.addEventListener("pointermove", mv); h.addEventListener("pointerup", up); };
  }
  async function open(){
    if(!box) build();
    box.hidden = false;
    try{ st = await (await fetch("/api/jam/status")).json(); }catch(e){}
    paint();
    if(chat) chat.check();
  }
  const B = () => box.querySelector("#jam-b");

  function paint(){
    if(!box) return;
    const b = B(), ts = st.tailscale || {};
    box.querySelector("#jam-dot").classList.toggle("on", !!(link && link.pc && link.pc.connectionState === "connected"));
    if(!st.on){
      box.querySelector("#jam-sub").textContent = "hraj s kamarádem přes internet";
      const dom = st.domain || {}, via = st.via || "tailscale";
      b.innerHTML = `<div class="jam-note">Kamarád otevře odkaz v Chromu. Uvidí tvůj Pick (píseň, text, taby), uslyšíte se navzájem
          a podklad písně hraje u obou ve stejný okamžik.</div>
        <div class="jam-l"><span>Kamarád přijde</span><div class="jam-via">
          <button type="button" class="jb ${via === "domain" ? "on" : ""}" data-via="domain">${dom.host ? esc(dom.host) : "přes mou doménu"}</button>
          <button type="button" class="jb ${via === "tailscale" ? "on" : ""}" data-via="tailscale">přes Tailscale</button></div></div>
        ${via === "domain" ? (dom.ready ? `<div class="jam-note">Odkaz bude na <b>${esc(dom.host)}</b> — kamarád nic neinstaluje.</div>`
          : `<div class="jam-note jam-warn">Doména ještě není nastavená — <a href="#" id="jam-dom-set">Nastavení → Jam</a> (3 kroky, jednou).</div>`) : tsHelp(ts)}
        <button type="button" class="go" id="jam-start">Zahájit jam</button><div class="jam-err" id="jam-err"></div>
        <div class="jam-row"><button type="button" class="jb" id="jam-guide">Návod pro kamaráda</button><a class="jam-note" href="${GUIDE}" target="_blank" rel="noopener">otevřít</a><span class="jam-note">· celý postup je v Nastavení → Jam</span></div>`;
      b.querySelector("#jam-guide").onclick = copyGuide;
      b.querySelectorAll("[data-via]").forEach(x => x.onclick = async () => { await fetch("/api/jam/via", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({via: x.dataset.via})});
        st = await (await fetch("/api/jam/status")).json(); paint(); });
      const ds = b.querySelector("#jam-dom-set"); if(ds) ds.onclick = (e) => { e.preventDefault(); openSettingsJam(); };
      b.querySelector("#jam-start").onclick = startRoom;
      return;
    }
    box.querySelector("#jam-sub").textContent = link && link.peerOn ? "kamarád je tu" : "čekám na kamaráda";
    b.innerHTML = `
      <div class="jam-link"><input readonly id="jam-url" value="${esc(st.url)}"><button type="button" class="jb" id="jam-copy">Kopírovat odkaz</button></div>
      <div class="jam-row"><button type="button" class="jb" id="jam-guide" title="Zkopíruje zprávu s odkazem na návod: Tailscale, pozvánka, zvukovka, připojení">Návod pro kamaráda</button><a class="jam-note" href="${GUIDE}" target="_blank" rel="noopener">otevřít</a></div>
      <div class="jam-note">Pošli odkaz kamarádovi (zprávou). Otevře ho v Chromu (Mac i Windows)${st.via === "domain" ? " — nic neinstaluje" : " — musí být ve tvém Tailscale (sdílené zařízení)"}. Napsat si můžete v chatu níž.</div>
      <div class="jam-v" id="jam-vbox"><span id="jam-vnone">kamarádova kamera</span><video id="jam-vfr" autoplay playsinline hidden></video><video class="me" id="jam-vme" autoplay playsinline muted hidden></video></div>
      <div class="jam-row">
        <button type="button" class="jb ${link && link.local.camera ? "on" : ""}" id="jam-cam">Kamera</button>
        <button type="button" class="jb ${link && link.local.voice ? "on" : ""}" id="jam-voice">Mluvit</button>
        <button type="button" class="jb ${link && link.local.screen ? "on" : ""}" id="jam-screen" title="Kamarád uvidí Pick — píseň, text, taby, akordy">Sdílet Pick</button>
        <span class="sp" style="flex:1"></span><span id="jam-pill-slot"></span><span class="jam-lat" id="jam-lat"></span></div>
      <div id="jam-chat-slot"></div>
      <div class="jam-l"><span>Zvuk kytar</span><select id="jam-mode">
          <option value="low" ${(!link || link.mode === "low") ? "selected" : ""}>nízké zpoždění (jako SonoBus)</option>
          <option value="rtc" ${link && link.mode === "rtc" ? "selected" : ""}>jako hovor (stabilnější)</option>
          <option value="native" ${link && link.mode === "native" ? "selected" : ""}>nativní engine (nejnižší zpoždění)</option></select></div>
      <div id="jam-nat-slot"></div>
      <div class="jam-l"><span>Vyrovnávání</span><select id="jam-buf"><option value="auto">automaticky</option>${[1, 2, 3, 4, 6, 8, 12].map(q => `<option value="${q}" ${String(cfg.buf) === String(q) ? "selected" : ""}>${q} × 2,7 ms = ${(q * 2.67).toFixed(1).replace(".", ",")} ms</option>`).join("")}</select></div>
      <div class="jam-l"><span>Kamarád</span><input type="range" id="jam-vol" min="0" max="2" step="0.01" value="${cfg.vol ?? 1}"></div>
      <div class="jam-l"><span>Hlas kamaráda</span><input type="range" id="jam-vvol" min="0" max="1" step="0.01" value="${cfg.vvol ?? 1}"></div>
      <div class="jam-l"><span>Moje kytara</span><div class="jam-m"><i id="jam-me"></i></div></div>
      <div class="jam-note" id="jam-gtr"></div>
      <div class="jam-note" id="jam-route"></div>
      <div class="jam-row"><button type="button" class="jb" id="jam-stop">Ukončit jam</button></div>
      <audio id="jam-voice-el" autoplay></audio>`;
    if(cfg.buf && b.querySelector("#jam-buf")) b.querySelector("#jam-buf").value = String(cfg.buf);
    const q = (id) => b.querySelector("#" + id);
    q("jam-guide").onclick = copyGuide;
    q("jam-copy").onclick = () => { navigator.clipboard.writeText(st.url).then(() => showToast("Odkaz zkopírovaný — pošli ho kamarádovi")); };
    q("jam-cam").onclick = async (e) => { try{ await link.setCamera(!link.local.camera); }catch(x){ showToast("Kameru se nepodařilo zapnout"); } paint(); };
    q("jam-voice").onclick = async () => { try{ await link.setVoice(!link.local.voice); }catch(x){ showToast("Mikrofon se nepodařilo zapnout"); } paint(); };
    q("jam-screen").onclick = async () => { try{ await link.setScreen(!link.local.screen); }catch(x){} paint(); };
    q("jam-mode").onchange = (e) => { link.setMode(e.target.value); link.wantNative = e.target.value === "native"; cfg.mode2 = e.target.value; keep(); natSlot(); };
    natSlot();
    // the engine's light: green with the delay when the native sound plays; a click shows tips
    if(window.JamNativeUI && JamNativeUI.pill){ if(!window.__jamPill) window.__jamPill = JamNativeUI.pill(() => link, {settings: () => { const m = q("jam-mode"); if(m) m.scrollIntoView({block: "center"}); }, offHelp: "Pick ho spouští sám — když neběží, restartuj Pick."}); q("jam-pill-slot").append(window.__jamPill); }
    q("jam-buf").onchange = (e) => { const v = e.target.value; cfg.buf = v; keep(); link.setBuffer(v === "auto" ? 3 : +v, v === "auto"); };
    q("jam-vol").oninput = (e) => { cfg.vol = +e.target.value; keep(); applyFriend(); if(jf) jfPaint(); };
    q("jam-vvol").oninput = (e) => { cfg.vvol = +e.target.value; keep(); q("jam-voice-el").volume = cfg.vvol; };
    q("jam-stop").onclick = stopRoom;
    q("jam-voice-el").volume = cfg.vvol ?? 1;
    if(chatEl) q("jam-chat-slot").replaceWith(chatEl);
    showVideos();
  }
  function tsHelp(ts){
    if(ts.ok) return `<div class="jam-note">Tailscale běží — <b>${esc(ts.dns || "")}</b>.</div>`;
    const steps = {
      notinstalled: `Nainstaluj aplikaci <b>Tailscale</b> (tailscale.com) a přihlas se.`,
      notrunning: `Spusť aplikaci <b>Tailscale</b> (ikona nahoře v liště) a přihlas se.`,
      login: `Přihlas se do <b>Tailscale</b> (ikona nahoře v liště).`};
    return `<ol class="jam-steps jam-note"><li class="jam-warn">${steps[ts.why] || steps.notrunning}</li>
      <li>V administraci Tailscale zapni <b>MagicDNS</b> a <b>HTTPS</b> (DNS → HTTPS Certificates) — jednou.</li>
      <li>Kamarádovi tenhle Mac nasdílej: v administraci Tailscale u zařízení <b>Share…</b> a jeho e-mail. On si nainstaluje Tailscale a přihlásí se.</li></ol>`;
  }
  async function startRoom(){
    const err = B().querySelector("#jam-err"); err.textContent = "Zapínám…";
    const r = await fetch("/api/jam/start", {method: "POST"}), d = await r.json().catch(() => ({}));
    if(!r.ok){
      st.tailscale = d.why === "https" || d.why === "serve" ? (st.tailscale || {}) : d;
      err.innerHTML = d.why === "https" ? `V Tailscale ještě není zapnuté <b>HTTPS</b>. ${d.authUrl ? `<a href="${esc(d.authUrl)}" target="_blank">Zapnout zde</a>` : "V administraci: DNS → HTTPS Certificates → Enable."} Pak zkus znovu.`
        : d.why === "serve" ? "Tailscale odmítl zpřístupnit jam: " + esc(d.msg || "")
        : d.why === "domain" || d.why === "tunnel" ? esc(d.msg || "Doménu se nepodařilo zapnout.")
        : "Tailscale neběží — spusť ho a přihlas se, pak zkus znovu.";
      if(d.why === "domain" || d.why === "tunnel") return;
      if(d.why !== "https" && d.why !== "serve"){ paint(); B().querySelector("#jam-err").innerHTML = err.innerHTML; }
      return;
    }
    st.on = true; st.url = d.url;
    await connect();
    paint();
  }
  async function connect(){
    if(link) return;
    const vo = Mixer.voiceOut();
    link = new JamLink({role: "host", ctx: vo.ac, output: vo.out, mode: cfg.mode2 || "native", native: cfg.nat, quanta: cfg.buf && cfg.buf !== "auto" ? +cfg.buf : 3, auto: !cfg.buf || cfg.buf === "auto", onEvent: event});
    chatEl = document.createElement("div");
    chat = JamChat({root: chatEl, me: "host", name: "Kamarád", send: (o) => link && link.send(o), visible: chatSeen, onUnread: badge,
      onNew: (m) => { if(!chatSeen()) showToast("💬 Kamarád: " + (m.text.length > 90 ? m.text.slice(0, 90) + "…" : m.text)); }});
    await link.start(`ws://${location.host}/api/jam/ws`);
    applyFriend();
    hookGuitar();
    try{ await link.setCamera(true); }catch(e){}
    clearInterval(songT); songT = setInterval(() => sendSong(false), 150);
    clearInterval(uiT); uiT = setInterval(ui, 250);
  }
  // Pick's guitar as it sounds (print / NAM / effects) — again after every rewire of the guitar's chain
  function hookGuitar(){
    const g = Mixer.guitar;
    const put = () => { const n = g.analyserNode && g.analyserNode(); link.setInput(n || null); };
    put(); if(unhook) unhook(); unhook = g.onRewire ? g.onRewire(() => setTimeout(put, 0)) : null;
  }
  async function stopRoom(){
    if(!(await Dialog.confirm("Ukončit jam? Odkaz přestane platit.", {ok: "Ukončit"}))) return;
    await fetch("/api/jam/stop", {method: "POST"}).catch(() => {});
    if(link){ link.close(); link = null; } if(unhook){ unhook(); unhook = null; }
    if(chat){ chat.destroy(); chat = null; chatEl = null; } badge(0);
    if(jf){ jf.remove(); jf = null; }
    clearInterval(songT); clearInterval(uiT); st.on = false; st.url = null; lastSong = "";
    paint();
  }
  // the chat counts as read only with the panel open and Pick in front
  function chatSeen(){ return !!(box && !box.hidden && chatEl && chatEl.isConnected && document.visibilityState === "visible" && document.hasFocus()); }
  function badge(n){
    unreadN = n;
    const b = document.querySelector(".jam-btn"); if(!b) return;
    let s = b.querySelector(".jam-badge");
    if(!n){ if(s) s.remove(); return; }
    if(!s){ s = document.createElement("span"); s.className = "jam-badge"; b.appendChild(s); }
    s.textContent = n > 9 ? "9+" : String(n);
  }
  // the native engine's settings under "Zvuk kytar", only while that mode is chosen
  function natSlot(){
    const slot = box && box.querySelector("#jam-nat-slot"); if(!slot || !link) return;
    if(link.mode !== "native"){ slot.innerHTML = ""; return; }
    if(slot.firstChild) return;
    slot.append(JamNativeUI.block(link, (l, h) => `<div class="jam-l"><span>${l}</span><div>${h}</div></div>`, (c) => { cfg.nat = c; keep(); }));
  }
  function event(kind, v){
    if(kind === "mode"){ const m = box && box.querySelector("#jam-mode"); if(m) m.value = v; natSlot(); return; }
    if(kind === "native-fallback"){ showToast(JamNativeUI.why(v), 9000); return; }
    if(kind === "native-state"){ const n = box && box.querySelector(".jam-nat"); if(n && n.repaint) n.repaint(); return; }
    if(kind === "taken"){ showToast("Jam je otevřený v jiném okně Picku — tady je odpojený. Zavři to druhé okno a jam tady spusť znovu.", 15000); return; }
    if(kind === "msg" && v && v.names){ names = v.names; if(jf) jfPaint(); }
    if(kind === "msg" && v && v.type === "ctl"){ onCtl(v); return; }
    if(kind === "msg" && v && v.type === "mine"){ friendNow = v; fsPaintNow(); if(jf) jfPaint(); return; }
    if(kind === "msg" && chat) chat.onMsg(v);
    if(kind === "peer" && jf) jfPaint();
    if(kind === "peer" || kind === "conn" || kind === "ready"){
      // the panel is not built again (its videos would blink): only what says where the link is
      if(box && !box.hidden){ if(st.on && box.querySelector("#jam-url")){ box.querySelector("#jam-sub").textContent = link && link.peerOn ? `${names.guest || "kamarád"} je tu` : "čekám na kamaráda"; showVideos(); } else paint(); }
      if(kind === "ready" && v) sendSong(true);
      if(kind === "peer" && v) showToast("Kamarád se připojil k jamu"); }
    if(kind === "media") showVideos();
    if(kind === "screen") paint();
  }
  function showVideos(){
    if(!box || !link) return;
    const fr = box.querySelector("#jam-vfr"), me = box.querySelector("#jam-vme"), none = box.querySelector("#jam-vnone"), vo = box.querySelector("#jam-voice-el");
    if(!fr) return;
    const cam = link.remoteCam === false ? null : link.media.camera;       // his camera off: no frozen last frame
    if(cam){ if(fr.srcObject !== cam) fr.srcObject = cam; fr.hidden = false; none.hidden = true; } else { fr.hidden = true; fr.srcObject = null; none.hidden = false; }
    if(link.local.camera){ const s = me.srcObject && me.srcObject.getVideoTracks()[0] === link.local.camera ? me.srcObject : new MediaStream([link.local.camera]); if(me.srcObject !== s) me.srcObject = s; me.hidden = false; } else me.hidden = true;
    if(link.media.voice && vo && vo.srcObject !== link.media.voice) vo.srcObject = link.media.voice;
  }
  // ---- the friend steers the song too (his Pick Jam): picks one from Steems, plays, pauses, moves, tempo, key —
  // all done here, in Pick's own mixer, so the two of you stay on one song and one clock
  async function onCtl(m){
    // the host (the admin) picks the songs and runs the playback in a jam; the friend's page asks nothing of it any more
    // (an older page might): nothing here moves because of it
    if(!cfg.guestCtl) return;
    const who = names.guest || "Kamarád";
    try{
      if(m.do === "load" && typeof m.name === "string" && m.name){
        if(Mixer.currentSong() !== m.name){
          if(typeof setMode === "function" && typeof state !== "undefined" && state.mode !== "steems") setMode("steems");
          loadMixerSong(m.name);
          showToast(`${who} vybral píseň: ${m.name.replace(/^.*? - /, "")}`);
        }
      }
      else if(m.do === "play"){ if(typeof m.pos === "number" && isFinite(m.pos)) Mixer.seek(Math.max(0, m.pos)); Mixer.play(); }
      else if(m.do === "pause") Mixer.pause();
      else if(m.do === "seek" && isFinite(m.pos)) Mixer.seek(Math.max(0, +m.pos));
      else if(m.do === "tempo" && isFinite(m.speed)) Mixer.setSpeed(+m.speed);
      else if(m.do === "key" && isFinite(m.semis)) Mixer.setKey(Math.round(+m.semis));
      // a section looped from the friend's page (⟲), or the loop off
      else if(m.do === "loop" && isFinite(m.start) && isFinite(m.end)){
        if(+m.end > +m.start){ Mixer.setLoop(+m.start, +m.end); Mixer.seek(+m.start); Mixer.play(); showToast(`${who}: smyčka ${Math.floor(m.start / 60)}:${String(Math.floor(m.start % 60)).padStart(2, "0")}`); }
        else Mixer.setLoop(0, 0);
      }
    }catch(e){}
    setTimeout(() => sendSong(true), 200); setTimeout(() => sendSong(true), 900);
  }

  // ---- the friend's guitar as a strip in the mix, beside the live guitar: volume, pan, mute — for this Mac only
  function applyFriend(){ if(!link) return; link.setVolume(cfg.fmute ? 0 : (cfg.vol ?? 1)); link.setPan(cfg.fpan || 0); }
  function jfHtml(){
    const v = cfg.vol ?? 1, p = cfg.fpan || 0;
    const db = v <= 0.01 ? "-∞" : (20 * Math.log10(v)).toFixed(1);
    const pl = p > 0.05 ? "R" + Math.round(p * 100) : p < -0.05 ? "L" + Math.round(-p * 100) : "C";
    const led = () => `<div class="jf-led">${"<i></i>".repeat(12)}</div>`;
    return `<div class="fl-ch-header"><div class="fl-ch-name">${esc(names.guest || "Kamarád")}</div><div class="fl-ch-tag">jam · přes internet</div></div>
      <div class="fl-led-wrap">${led()}<div class="fl-fader-area"><div class="fl-fader-track" data-jf="vol" title="Hlasitost kamaráda u tebe — táhni, dvojklik = 0 dB">
        <div class="fl-fader-handle" style="bottom:${Math.round(Math.min(1.4, v) / 1.4 * 90) + 5}%"></div></div></div>${led()}</div>
      <div class="fl-db-label">${db} dB</div>
      <div class="fl-pan-wrap"><div class="fl-pan-knob ${p < -0.05 ? "pan-l" : p > 0.05 ? "pan-r" : ""}" data-jf="pan" title="Panorama — táhni svisle, dvojklik = střed"></div><div class="fl-pan-label">${pl}</div></div>
      <div class="fl-ms-btns"><button class="fl-ms-btn mute ${cfg.fmute ? "active" : ""}" data-jf="mute" title="Ztlumit kamaráda">M</button></div>
      <button class="fl-fx-btn jf-snd" data-jf="snd" title="Nastavit kamarádovi zvuk: otisk nebo NAM, gain, drive, gate a hlasitosti — on si to pak může změnit">ZVUK</button>
      <div class="jf-now" title="Co teď kamarád hraje">${esc(friendNow && friendNow.sound || "")}</div>
      <div class="jf-off">${link && link.peerOn ? "" : "kamarád není připojený"}</div>`;
  }
  function jfPaint(){
    if(!jf) return;
    jf.className = "fl-channel color-guitar fl-live" + (cfg.fmute ? " muted" : "");
    jf.innerHTML = jfHtml();
    const tr = jf.querySelector('[data-jf="vol"]'), kn = jf.querySelector('[data-jf="pan"]');
    const setV = (y) => { const r = tr.getBoundingClientRect(), pct = (r.bottom - y) / r.height * 100; cfg.vol = Math.max(0, Math.min(1.4, (pct - 5) / 90 * 1.4)); keep(); applyFriend(); jfPaint(); const s = box && box.querySelector("#jam-vol"); if(s) s.value = cfg.vol; };
    tr.onpointerdown = (e) => { tr.setPointerCapture(e.pointerId); setV(e.clientY); tr.onpointermove = (ev) => setV(ev.clientY); tr.onpointerup = () => { tr.onpointermove = null; }; };
    tr.ondblclick = () => { cfg.vol = 1; keep(); applyFriend(); jfPaint(); };
    kn.onpointerdown = (e) => { kn.setPointerCapture(e.pointerId); const y0 = e.clientY, p0 = cfg.fpan || 0;
      kn.onpointermove = (ev) => { cfg.fpan = Math.max(-1, Math.min(1, p0 + (y0 - ev.clientY) / 100)); applyFriend(); const lb = jf.querySelector(".fl-pan-label"); if(lb) lb.textContent = cfg.fpan > 0.05 ? "R" + Math.round(cfg.fpan * 100) : cfg.fpan < -0.05 ? "L" + Math.round(-cfg.fpan * 100) : "C"; };
      kn.onpointerup = () => { kn.onpointermove = null; keep(); jfPaint(); }; };
    kn.ondblclick = () => { cfg.fpan = 0; keep(); applyFriend(); jfPaint(); };
    jf.querySelector('[data-jf="mute"]').onclick = () => { cfg.fmute = !cfg.fmute; keep(); applyFriend(); jfPaint(); };
    jf.querySelector('[data-jf="snd"]').onclick = (e) => friendSound(e.currentTarget);
  }
  // ---- his sound set from here: a print or a NAM (+ cabinet), his gain, drive, gate, his guitar and the backing —
  // sent to his page and played there at once; he can change any of it afterwards
  let fs = null, fsTab = "prints", fsQ = "", fsAmp = {model: "", ir: "", mname: "", iname: ""}, fsT = 0, fsPrints = null;
  const fsSend = (o) => { if(link) link.send({type: "setup", ...o}); };
  function fsPaintNow(){
    if(!fs) return;
    const n = friendNow || {}, my = n.my || {};
    fs.querySelector(".fs-now").textContent = n.sound ? `Teď hraje: ${n.sound}` : (link && link.peerOn ? "Kamarád zatím nic neposlal" : "Kamarád není připojený");
    [["ing", -12, 24], ["drive", -24, 12], ["gate", -100, -20], ["mvdb", -30, 6], ["bv", -24, 6]].forEach(([k]) => {
      const inp = fs.querySelector(`[data-fk="${k}"]`); if(!inp || document.activeElement === inp) return;
      const v = k === "mvdb" ? (my.mv > 0 ? 20 * Math.log10(my.mv) : -30) : my[k];
      if(v != null && isFinite(v)){ inp.value = v; fsLabel(inp); }
    });
  }
  function fsLabel(inp){ const k = inp.dataset.fk, v = +inp.value;
    inp.nextElementSibling.textContent = k === "gate" ? (v <= -99 ? "vyp." : Math.round(v) + " dB") : (v > 0 ? "+" : "") + v.toFixed(k === "gate" ? 0 : 1) + " dB"; }
  async function fsList(){
    if(!fs) return;
    const box = fs.querySelector(".fs-list"), q = fsQ.toLowerCase();
    fs.querySelectorAll(".fs-tabs [data-t]").forEach(b => b.classList.toggle("on", b.dataset.t === fsTab));
    try{
      if(fsTab === "prints"){
        if(!fsPrints) fsPrints = ((await (await fetch("/api/tone/prints")).json()) || []).filter(x => x && x.review !== "rejected");
        const got = fsPrints.filter(p => !q || [p.name, p.artist, p.title, p.category].join(" ").toLowerCase().includes(q)).slice(0, 120);
        box.innerHTML = got.map(p => `<button data-song="${esc(p.song)}" data-id="${esc(p.id)}" data-name="${esc(p.name || p.title || "")}"><b>${esc(p.name || p.title || p.id)}</b><span>${esc([p.artist, p.title, p.category].filter(Boolean).join(" · "))}</span></button>`).join("") || `<div class="hint">Nic nenalezeno.</div>`;
        box.querySelectorAll("[data-id]").forEach(b => b.onclick = () => { fsSend({sound: {kind: "print", song: b.dataset.song, id: b.dataset.id, name: b.dataset.name}}); showToast(`Kamarádovi posílám otisk ${b.dataset.name}`); });
      } else {
        const kind = fsTab === "ir" ? "ir" : "model";
        const d = await (await fetch(`/api/amp-catalog?kind=${kind}&limit=150&q=${encodeURIComponent(fsQ)}`)).json();
        let last = null;
        box.innerHTML = (d.items || []).map(a => { const hd = a.group !== last ? `<div class="fs-g">${esc(a.group)}</div>` : ""; last = a.group;
          return hd + `<button data-f="${esc(a.file)}" data-n="${esc(a.name)}" class="${(kind === "ir" ? fsAmp.ir : fsAmp.model) === a.file ? "on" : ""}"><b>${esc(a.name)}</b></button>`; }).join("") || `<div class="hint">Nic nenalezeno.</div>`;
        box.querySelectorAll("[data-f]").forEach(b => b.onclick = () => {
          if(kind === "ir"){ fsAmp.ir = b.dataset.f; fsAmp.iname = b.dataset.n; } else { fsAmp.model = b.dataset.f; fsAmp.mname = b.dataset.n; }
          const name = [fsAmp.mname, fsAmp.iname].filter(Boolean).join(" + ");
          fsSend({sound: {kind: "amp", model: fsAmp.model, ir: fsAmp.ir, name}}); showToast(`Kamarádovi posílám ${name}`); fsList(); });
      }
    }catch(e){ box.innerHTML = `<div class="hint">Seznam se nepodařilo načíst.</div>`; }
  }
  function friendSound(anchor){
    if(fs){ fs.remove(); fs = null; return; }
    fs = document.createElement("div"); fs.className = "jam-fs";
    const sl = (k, label, min, max, step) => `<label class="fs-sl"><span>${label}</span><input type="range" data-fk="${k}" min="${min}" max="${max}" step="${step}" value="0"><em></em></label>`;
    fs.innerHTML = `<div class="fs-hd"><b>Zvuk pro ${esc(names.guest || "kamaráda")}</b><button class="fs-x" title="Zavřít">✕</button></div>
      <div class="fs-now"></div>
      <div class="fs-tabs"><button data-t="prints">Otisky</button><button data-t="model">NAM</button><button data-t="ir">Bedny (IR)</button><button data-t="clean">Čistý</button></div>
      <input class="fs-q" placeholder="Hledat…"><div class="fs-list"></div>
      <div class="fs-sls">${sl("ing", "Gain", -12, 24, 0.5)}${sl("drive", "Drive", -24, 12, 0.5)}${sl("gate", "Gate", -100, -20, 1)}${sl("mvdb", "Jeho kytara", -30, 6, 0.5)}${sl("bv", "Podklad", -24, 6, 0.5)}</div>
      <div class="hint">Pošle se mu hned a hraje to u něj. Pak si to může sám změnit.</div>`;
    document.body.appendChild(fs);
    const r = anchor.getBoundingClientRect();
    if(r.height){ fs.style.left = Math.max(8, Math.min(window.innerWidth - 340, r.left - 140)) + "px"; fs.style.bottom = Math.min(window.innerHeight - 200, Math.max(8, window.innerHeight - r.top + 8)) + "px"; }
    else { fs.style.left = Math.max(8, (window.innerWidth - 330) / 2) + "px"; fs.style.top = "80px"; }
    fs.querySelector(".fs-x").onclick = () => { fs.remove(); fs = null; };
    fs.querySelectorAll(".fs-tabs [data-t]").forEach(b => b.onclick = () => {
      if(b.dataset.t === "clean"){ fsSend({sound: {kind: "clean"}}); showToast("Kamarádovi posílám čistý zvuk"); return; }
      fsTab = b.dataset.t; fsList(); });
    fs.querySelector(".fs-q").oninput = (e) => { fsQ = e.target.value; clearTimeout(fsT); fsT = setTimeout(fsList, 250); };
    fs.querySelectorAll("[data-fk]").forEach(inp => { fsLabel(inp); inp.oninput = () => { fsLabel(inp);
      const k = inp.dataset.fk, v = +inp.value, mine = k === "mvdb" ? {mv: v <= -29.9 ? 0 : Math.pow(10, v / 20)} : {[k]: v};
      clearTimeout(inp._t); inp._t = setTimeout(() => fsSend({mine}), 80); }; });
    fsPaintNow(); fsList();
  }
  function jfMount(){
    const rack = document.getElementById("fl-channels");
    if(!link || !rack || !rack.querySelector(".fl-channel:not(#jam-fader)")){ if(jf && !link){ jf.remove(); jf = null; } return; }
    if(!jf){ jf = document.createElement("div"); jf.id = "jam-fader"; jfPaint(); }
    if(jf.parentNode !== rack || jf.nextElementSibling) rack.appendChild(jf);
  }
  function jfMeter(){
    if(!jf || !link || !jf.isConnected) return;
    const p = link.level(), n = Math.round(Math.min(1, Math.sqrt(p)) * 12);
    jf.querySelectorAll(".jf-led").forEach(m => [...m.children].forEach((i, k) => { i.className = k < n ? "on" + (k >= 10 ? " clip" : k >= 8 ? " hot" : "") : ""; }));
  }

  // the song in Steems: which, where (as heard now), playing or not — the friend's page plays the same then
  function sendSong(force){
    if(!link) return;
    let name = null, playing = false, pos = 0, at = performance.now(), stems = [], speed = 1;
    try{
      name = Mixer.currentSong && Mixer.currentSong();
      if(name){
        const ac = Mixer.voiceOut().ac, ts = ac.getOutputTimestamp();
        playing = !!Mixer.isPlaying(); speed = Mixer.speed ? Mixer.speed() : 1;
        pos = playing && ts.performanceTime ? Mixer.songAt(ts.contextTime) : (Mixer.position ? Mixer.position() : 0);
        at = playing && ts.performanceTime ? ts.performanceTime : performance.now();
        stems = [...new Set((Mixer.deck.audio() || []).map(x => x.stem).filter(s => !/^take-|^midi/.test(s)))];
      }
    }catch(e){}
    let key = 0, stemPitch = {}, dur = 0, ready = false, loop = null;
    // Steems on the whole screen (text, tab or both): his page shows the same — his own text, and the tab as the shared screen
    const sg = document.getElementById("nm-stage"), sb = document.getElementById("nm-stage-body");
    const stage = sg && !sg.hidden ? {on: true, show: (sb && sb.dataset.show) || "both", dir: sb && sb.classList.contains("col") ? "col" : "row"} : {on: false};
    if(stage.on && !stageWas && link.peerOn && !(link.local && link.local.screen) && stage.show !== "lyr")
      showToast(`${names.guest || "Kamarád"} teď vidí text. Taby uvidí, když v jamu zapneš Sdílet Pick.`);
    stageWas = stage.on;
    try{ key = Mixer.key ? Mixer.key() : 0; stemPitch = Mixer.stemPitch ? Mixer.stemPitch() : {}; dur = Mixer.duration ? Mixer.duration() : 0; ready = Mixer.ready ? Mixer.ready() : true; loop = Mixer.loopNow ? Mixer.loopNow() : null; }catch(e){}
    // sent at once when something changed — play, pause, a jump (a seek, the loop going round), tempo, key, the song —
    // otherwise once a second: the friend's page plays from it
    const sig = JSON.stringify([name, playing, speed, key, stemPitch, ready, loop, stems.length, stage]);
    const was = lastSent, now = performance.now();
    const jumped = was && playing && was.playing ? Math.abs(pos - (was.pos + (at - was.at) / 1000 * was.speed)) > 0.04 : was && Math.abs(pos - was.pos) > 0.04;
    if(!force && was && sig === lastSong && !jumped && now - was.sentAt < 1000) return;
    link.send({type: "song", name, title: name ? name.replace(/^.*? - /, "") : "", stems, playing, pos, at, speed, key, stemPitch, dur, ready, loop, stage});
    lastSong = sig; lastSent = {pos, at, speed, playing, sentAt: now};
  }
  function ui(){
    if(link){ jfMount(); jfMeter(); }
    if(!box || box.hidden || !link) return;
    const lat = box.querySelector("#jam-lat"); if(!lat) return;
    const d = link.delay();
    lat.innerHTML = link.ready ? `${d.total} ms<small>síť ${d.net} · paměť ${d.buf} · výstup ${d.out}</small>` : "";
    lat.style.color = d.total <= 30 ? "#6ee7a0" : d.total <= 50 ? "#ffd166" : "#ff8a8a";
    const natDev = link.mode === "native" && link.natCfg.source === "device";
    const me = box.querySelector("#jam-me"); if(me) me.style.width = Math.min(100, (natDev ? (link.native.stats && link.native.stats.in_peak) || 0 : link.txPeak || 0) * 100) + "%";
    const g = box.querySelector("#jam-gtr");
    if(g) g.innerHTML = natDev ? "Kamarád slyší kytaru přímo ze zvukovky (vstup enginu) — bez otisku a efektů Picku." : link.input ? "Kamarád slyší tvou kytaru tak, jak zní v Picku (otisk, NAM, efekty)." : `<span class="jam-warn">Kytara v Picku není zapnutá — zapni živou kytaru ve Steems (vstup zvukovky), pak ji kamarád uslyší.</span>`;
    const r = box.querySelector("#jam-route"), rt = link.route(), rs = link.rxStats;
    if(r) r.textContent = rt ? `Spojení: ${rt}${rs.under ? ` · výpadky ${rs.under}` : ""}${rs.lost ? ` · ztracené balíčky ${rs.lost}` : ""}` : "";
    box.querySelector("#jam-dot").classList.toggle("on", !!(link.pc && link.pc.connectionState === "connected"));
  }
  // the Steems row: a button beside the looper's
  function mount(){
    const acts = document.getElementById("nm-song-acts") || document.querySelector("#steems-hero .hero-actions");
    if(acts && !acts.querySelector(".jam-btn")){
      const b = document.createElement("button"); b.type = "button"; b.className = "nm-btn round ic dk-hero-btn jam-btn";
      b.title = "Jam — hraj s kamarádem přes internet (Tailscale): uslyšíte se, uvidíte se, uvidí tvůj Pick";
      b.innerHTML = icon("headphones", 15); b.dataset.lbl = "JAM"; b.onclick = open;
      const lp = acts.querySelector(".dk-hero-btn"); if(lp) lp.after(b); else acts.prepend(b);
      badge(unreadN);
    }
  }
  setInterval(mount, 2000); setTimeout(mount, 500);
  function openSettingsJam(){
    const s = document.getElementById("settings-btn"); if(s) s.click();
    setTimeout(() => { const t = [...document.querySelectorAll("#settings-overlay button")].find(b => b.textContent.trim() === "Jam"); if(t) t.click(); domPaint(); }, 300);
  }
  // Nastavení → Jam → vlastní doména: log in to Cloudflare, point jam.<domain> at the tunnel, choose how the friend comes
  let domT = 0;
  async function domPaint(){
    const box = document.getElementById("jam-dom"); if(!box) return;
    let d = {};
    try{ d = await (await fetch("/api/jam/status")).json(); }catch(e){ return; }
    const dm = d.domain || {}, $ = (id) => document.getElementById(id);
    const li = $("jd-login-st");
    li.className = "jd-st " + (dm.logged ? "ok" : "");
    li.textContent = !dm.installed ? "Chybí program cloudflared." : dm.logged ? "Přihlášeno ✓" : dm.loggingIn ? "Čekám, až v prohlížeči vybereš doménu a dáš Authorize…" : "";
    if(dm.loggingIn && dm.loginUrl) li.innerHTML = `Čekám, až v prohlížeči vybereš doménu a dáš Authorize… <a href="${esc(dm.loginUrl)}" target="_blank" rel="noopener">neotevřelo se? klikni sem</a>`;
    $("jd-login").disabled = !!dm.logged; $("jd-login").textContent = dm.logged ? "Hotovo" : "Přihlásit";
    const h = $("jd-host"); if(!h.value) h.value = dm.host || "jam.neverlast.cz";
    const se = $("jd-setup-st");
    if(!se.dataset.busy){ se.className = "jd-st " + (dm.ready ? "ok" : ""); se.textContent = dm.ready ? `Propojeno ✓ — https://${dm.host}` : ""; }
    $("jd-setup").disabled = !dm.logged;
    $("jd-via-domain").checked = d.via === "domain"; $("jd-via-ts").checked = d.via !== "domain";
    clearTimeout(domT); if(dm.loggingIn && !document.getElementById("settings-overlay").hidden) domT = setTimeout(domPaint, 2000);
  }
  // Nastavení → Jam → přístup: names and passwords for the friends, the host's own name
  async function accPaint(){
    const $ = (id) => document.getElementById(id); if(!$("jam-acc")) return;
    let d = {users: [], host_name: ""};
    try{ d = await (await fetch("/api/jam/users")).json(); }catch(e){ return; }
    if(document.activeElement !== $("ja-host")) $("ja-host").value = d.host_name || "";
    $("ja-list").innerHTML = d.users.length ? "Mohou se přihlásit: " + d.users.map(u => `<b>${esc(u)}</b> <a href="#" data-del="${esc(u)}" title="Odebrat">✕</a>`).join(" · ")
      : "Zatím nikdo — kamarád se připojí odkazem s klíčem (bez hesla).";
    $("ja-list").querySelectorAll("[data-del]").forEach(a => a.onclick = async (e) => { e.preventDefault();
      if(!(await Dialog.confirm(`Odebrat ${a.dataset.del}? Už se nepřihlásí.`, {ok: "Odebrat"}))) return;
      await fetch("/api/jam/users?name=" + encodeURIComponent(a.dataset.del), {method: "DELETE"}); accPaint(); });
  }
  function accWire(){
    const $ = (id) => document.getElementById(id); if(!$("jam-acc")) return;
    const post = (b) => fetch("/api/jam/users", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b)});
    $("ja-host-save").onclick = async () => { await post({host_name: $("ja-host").value}); showToast("Jméno uložené"); accPaint(); };
    $("ja-add").onclick = async () => {
      const r = await post({name: $("ja-name").value, password: $("ja-pass").value}), d = await r.json().catch(() => ({}));
      const stl = $("ja-st");
      if(r.ok){ stl.className = "jd-st ok"; stl.textContent = `Hotovo — ${$("ja-name").value.trim()} se může přihlásit.`; $("ja-pass").value = ""; $("ja-name").value = ""; accPaint(); }
      else { stl.className = "jd-st bad"; stl.textContent = d.msg || "Nepovedlo se."; }
    };
    const sb = $("settings-btn"); if(sb) sb.addEventListener("click", () => setTimeout(accPaint, 200));
  }
  // Nastavení → Jam → knihovna na PC
  async function libPaint(){ const $ = (id) => document.getElementById(id); if(!$("jam-lib")) return;
    try{ const d = await (await fetch("/api/jam/library")).json(); if(document.activeElement !== $("jl-url")) $("jl-url").value = d.url || "";
      if(!$("jl-st").dataset.busy){ $("jl-st").className = "jd-st"; $("jl-st").textContent = d.token ? "Klíč je uložený." : "Klíč zatím není."; } }catch(e){} }
  function libWire(){
    const $ = (id) => document.getElementById(id); if(!$("jam-lib")) return;
    $("jl-gen").onclick = () => { const a = new Uint8Array(24); crypto.getRandomValues(a); const t = [...a].map(b => b.toString(16).padStart(2, "0")).join("");
      $("jl-tok").type = "text"; $("jl-tok").value = t; navigator.clipboard.writeText(t).catch(() => {});
      $("jl-st").className = "jd-st ok"; $("jl-st").textContent = "Nový klíč je zkopírovaný — vlož ho na PC do .env knihovny (PICK_SYNC_TOKEN) a tady dej Uložit."; };
    $("jl-save").onclick = async () => { const r = await fetch("/api/jam/library", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({url: $("jl-url").value, token: $("jl-tok").value})});
      const d = await r.json().catch(() => ({})); $("jl-st").className = "jd-st " + (r.ok ? "ok" : "bad"); $("jl-st").textContent = r.ok ? "Uloženo." : (d.error || "Nepodařilo se."); if(r.ok){ $("jl-tok").value = ""; $("jl-tok").type = "password"; } };
    $("jl-test").onclick = async () => { $("jl-st").dataset.busy = "1"; $("jl-st").className = "jd-st"; $("jl-st").textContent = "Zkouším…";
      const r = await fetch("/api/jam/library/test", {method: "POST"}), d = await r.json().catch(() => ({})); delete $("jl-st").dataset.busy;
      $("jl-st").className = "jd-st " + (r.ok ? "ok" : "bad"); $("jl-st").textContent = r.ok ? `Spojeno ✓ — Na příště má ${d.fav} písní.` + (d.prepared ? ` Předem připraveno ${d.prepared.done} z ${d.prepared.todo} písní${d.prepared.on ? " (pracuje se)" : ""}${d.used_gb != null ? `, zabírají ${d.used_gb} GB` : ""}.${d.prepared.note ? " " + d.prepared.note : ""}` : "") : (d.error || "Nepodařilo se."); };
    const sb = $("settings-btn"); if(sb) sb.addEventListener("click", () => setTimeout(libPaint, 200));
    // the friend's desktop app: the library gives the launcher out, with its own address in it
    $("jl-cmd").onclick = (e) => { e.preventDefault(); const u = ($("jl-url").value || "").trim().replace(/\/+$/, "");
      if(!/^https:\/\//.test(u)){ showToast("Nejdřív vyplň adresu knihovny (https://…)"); return; } window.open(u + "/pick-jam.cmd", "_blank"); };
    $("jl-mac").onclick = (e) => { e.preventDefault(); const u = ($("jl-url").value || "").trim().replace(/\/+$/, "");
      if(!/^https:\/\//.test(u)){ showToast("Nejdřív vyplň adresu knihovny (https://…)"); return; } window.open(u + "/pick-jam-mac.zip", "_blank"); };
  }
  function domWire(){
    const $ = (id) => document.getElementById(id); if(!$("jam-dom")) return;
    $("jd-login").onclick = async () => { const r = await (await fetch("/api/jam/domain/login", {method: "POST"})).json(); if(!r.ok) showToast(r.msg || "Nepovedlo se"); domPaint(); };
    $("jd-setup").onclick = async () => {
      const se = $("jd-setup-st"); se.dataset.busy = "1"; se.className = "jd-st"; se.textContent = "Vytvářím tunel a nastavuji DNS…";
      const r = await fetch("/api/jam/domain/setup", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({host: $("jd-host").value})});
      const d = await r.json().catch(() => ({})); delete se.dataset.busy;
      if(r.ok){ await fetch("/api/jam/via", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({via: "domain"})}); showToast("Doména propojená — kamarád se připojí bez instalace"); }
      else { se.className = "jd-st bad"; se.textContent = d.msg || "Nepovedlo se"; }
      domPaint(); if(st && !st.on) try{ st = await (await fetch("/api/jam/status")).json(); paint(); }catch(e){}
    };
    document.querySelectorAll("input[name=jd-via]").forEach(x => x.onchange = async () => { await fetch("/api/jam/via", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({via: x.value})}); domPaint(); });
    const sb = $("settings-btn"); if(sb) sb.addEventListener("click", () => setTimeout(domPaint, 200));
  }
  // Nastavení → Jam
  setTimeout(() => { domWire(); accWire(); libWire(); const c = document.getElementById("jam-guide-copy"), o = document.getElementById("jam-open-set");
    if(c) c.onclick = copyGuide;
    if(o) o.onclick = () => { const x = document.getElementById("settings-close"); if(x) x.click(); open(); }; }, 0);
  // a room left on (Pick reloaded): back in it
  fetch("/api/jam/status").then(r => r.json()).then(async (d) => { st = d; if(d.on){ build(); await connect(); } }).catch(() => {});
  // the button beside Pick's brand: opens this panel; its dot says off / on / the friend is in
  function quick(){
    const b = document.getElementById("jam-quick"); if(!b) return;
    b.onclick = () => open();
    const tick = async () => {
      if(!(quick.n++ % 5)){ try{ const d = await (await fetch("/api/jam/status")).json(); if(d && typeof d === "object") st = {...st, ...d}; }catch(e){} }
      const peer = !!(link && link.peerOn);
      b.classList.toggle("on", !!st.on); b.classList.toggle("peer", peer);
      b.title = peer ? `Jam běží — ${names.guest || "kamarád"} je připojený` : st.on ? "Jam běží — čekám na kamaráda" : "Jam s kamarádem — nastavit a spustit";
    };
    tick(); setInterval(tick, 2000);
  }
  quick.n = 0; quick();
  return {open, isOn: () => !!st.on, link: () => link,
    // the friend's volume at the host (his strip in the mix, and a knob on the Live Show's deck)
    friend: () => link && link.peerOn ? {name: names.guest || "Kamarád",
      get: () => ({v: cfg.vol ?? 1, muted: !!cfg.fmute}),
      set: (v) => { cfg.vol = Math.max(0, Math.min(1.4, v)); keep(); applyFriend(); if(jf) jfPaint(); },
      mute: () => { cfg.fmute = !cfg.fmute; keep(); applyFriend(); if(jf) jfPaint(); }} : null};
})();
