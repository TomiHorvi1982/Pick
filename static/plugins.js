// Pluginy — the player's AU / VST3 plugins (Neural DSP and the rest) on the live guitar and on the
// tracks of a song (server: plugins.py, plugin_host.py). One floating window per place:
// a chain of plugins, each set in its own window (⚙); the live guitar runs the chain live — from
// the interface's input into BlackHole, which Pick's tunnel plays as the live guitar; a track is
// rendered through its chain into a new track of the song.
const PluginRack = (() => {
  const POS = "pick-plugins-pos", DEV = "pick-plugins-dev";
  let win = null, o = null, cat = [], chain = [], scanT = 0, jobT = 0, devices = null;
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const dev = () => { try{ return JSON.parse(localStorage.getItem(DEV) || "{}"); }catch(e){ return {}; } };
  const keepDev = (d) => { try{ localStorage.setItem(DEV, JSON.stringify({...dev(), ...d})); }catch(e){} };

  function build(){
    win = document.createElement("div");
    win.className = "plg-win"; win.id = "plg-win";
    win.innerHTML = `<div class="plg-bar"><b class="plg-title"></b><span class="sp"></span>
      <button type="button" class="plg-x" title="Zavřít">${icon("close", 14)}</button></div><div class="plg-body"></div>`;
    document.body.appendChild(win);
    try{ const p = JSON.parse(localStorage.getItem(POS) || "null"); if(p){ win.style.left = p.x + "px"; win.style.top = p.y + "px"; } }catch(e){}
    win.querySelector(".plg-x").onclick = close;
    const bar = win.querySelector(".plg-bar");
    bar.onpointerdown = (e) => {
      if(e.button || e.target.closest("button")) return;
      const r = win.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      bar.setPointerCapture(e.pointerId);
      bar.onpointermove = (m) => { win.style.left = Math.max(0, Math.min(innerWidth - 120, m.clientX - dx)) + "px"; win.style.top = Math.max(0, Math.min(innerHeight - 40, m.clientY - dy)) + "px"; };
      bar.onpointerup = () => { bar.onpointermove = bar.onpointerup = null; try{ localStorage.setItem(POS, JSON.stringify({x: parseInt(win.style.left), y: parseInt(win.style.top)})); }catch(e){} };
    };
  }

  async function load(){
    const d = await api("/api/plugins");
    cat = d.items || [];
    chain = (await api("/api/plugins/chain?key=" + encodeURIComponent(o.key))).items || [];
    return d;
  }
  const effects = () => cat.filter(c => c.ok && !c.instrument);

  async function paint(state){
    const d = state || await api("/api/plugins").catch(() => ({scan: {}, live: {}, job: {}}));
    const body = win.querySelector(".plg-body"), scan = d.scan || {}, live = d.live || {}, job = d.job || {};
    const fx = effects();
    const groups = {};
    fx.forEach(p => (groups[p.vendor || "Ostatní"] = groups[p.vendor || "Ostatní"] || []).push(p));
    const opts = Object.keys(groups).sort((a, b) => (a === "Neural DSP" ? -1 : b === "Neural DSP" ? 1 : a.localeCompare(b, "cs")))
      .map(g => `<optgroup label="${escapeHtml(g)}">${groups[g].map(p => `<option value="${p.id}">${escapeHtml(p.name)} (${p.format})</option>`).join("")}</optgroup>`).join("");
    const rows = chain.map((c, i) => `<div class="plg-row${c.bypass ? " off" : ""}" data-i="${i}">
        <button type="button" class="plg-on" title="${c.bypass ? "Zapnout" : "Obejít (vypnout)"}">${c.bypass ? "○" : "●"}</button>
        <span class="plg-name">${escapeHtml(c.name)}</span>
        <button type="button" class="plg-edit" title="Otevřít okno pluginu — nastavení se uloží, až ho zavřeš">⚙ Okno</button>
        <button type="button" class="plg-up" title="Výš v řetězu" ${i ? "" : "disabled"}>↑</button>
        <button type="button" class="plg-del" title="Odebrat">✕</button></div>`).join("");
    const isLive = o.key === "live";
    body.innerHTML = `
      <div class="plg-sec">
        <div class="plg-h">Řetěz pluginů <small>${isLive ? "živá kytara" : escapeHtml(o.stem || "")}</small></div>
        ${rows || `<div class="plg-none">Zatím žádný plugin.</div>`}
        <div class="plg-add">${fx.length ? `<select class="plg-pick">${opts}</select><button type="button" class="btn plg-addb">Přidat</button>`
          : `<span class="plg-none">${scan.running ? "Skenuji…" : "Nejdřív naskenuj pluginy."}</span>`}</div>
      </div>
      ${isLive ? `<div class="plg-sec">
        <div class="plg-h">Hrát živě</div>
        <div class="plg-grid"><label>Vstup <select class="plg-in"></select></label>
          <label>Kanál <select class="plg-ch"><option value="1">vstup 1</option><option value="2">vstup 2</option></select></label>
          <label>Výstup <select class="plg-out"></select></label></div>
        <div class="plg-live"><button type="button" class="btn ${live.running ? "secondary" : ""} plg-liveb">${live.running ? "Vypnout živě" : "Zapnout živě"}</button>
          <span class="plg-st">${live.running ? `● hraje (${escapeHtml(live.input)} → ${escapeHtml(live.output)})` : live.error ? escapeHtml(live.error) : ""}</span></div>
        <div class="plg-hint">Kytara jde ze zvukovky do pluginů a do BlackHole; Pick ji odtud bere jako živou kytaru (tunel) — fader,
          EQ, nahrávání i AUTO MIX fungují dál. Okno pluginu (⚙) jde ladit za hraní.</div>
      </div>` : `<div class="plg-sec">
        <div class="plg-h">Prohnat stopu</div>
        <div class="plg-live"><button type="button" class="btn plg-render" ${job.status === "running" ? "disabled" : ""}>Prohnat stopu pluginy → nová stopa</button>
          <span class="plg-st">${job.status === "running" ? "Zpracovávám… " + escapeHtml(job.what || "") : job.status === "error" ? escapeHtml(job.error) : ""}</span></div>
        <div class="plg-hint">Nahrávka stopy projde řetězem a vznikne nová stopa písně; originál zůstane.</div>
      </div>`}
      <div class="plg-foot"><button type="button" class="plg-scan">${scan.running ? `Skenuji ${scan.done}/${scan.total} — ${escapeHtml(scan.current || "")}` : `Skenovat pluginy`}</button>
        <span>${fx.length} efektů${cat.length ? `, ${cat.filter(c => !c.ok).length} se nenačetlo` : ""}</span></div>`;
    wire(body, live);
    if(isLive) fillDevices(body);
  }

  async function saveChain(next){
    try{ chain = (await api("/api/plugins/chain", json("PUT", {key: o.key, items: next}))).items || []; }
    catch(e){ showToast(e.message); }
    paint();
    if(o && o.key === "live") window.dispatchEvent(new Event("pick-plugins-changed"));
  }
  function wire(body, live){
    body.querySelectorAll(".plg-row").forEach(r => {
      const i = +r.dataset.i;
      r.querySelector(".plg-on").onclick = () => saveChain(chain.map((c, k) => k === i ? {...c, bypass: !c.bypass} : c));
      r.querySelector(".plg-del").onclick = () => saveChain(chain.filter((c, k) => k !== i));
      r.querySelector(".plg-up").onclick = () => { const n = chain.slice(); [n[i - 1], n[i]] = [n[i], n[i - 1]]; saveChain(n); };
      r.querySelector(".plg-edit").onclick = async (e) => {
        e.currentTarget.disabled = true;
        showToast(`Otevírám ${chain[i].name}… (první načtení trvá pár vteřin)`);
        try{ await api("/api/plugins/editor", json("POST", {key: o.key, index: i})); }catch(err){ showToast(err.message); }
        e.currentTarget.disabled = false;
      };
    });
    const add = body.querySelector(".plg-addb");
    if(add) add.onclick = () => saveChain([...chain, {id: body.querySelector(".plg-pick").value}]);
    body.querySelector(".plg-scan").onclick = scan;
    const lb = body.querySelector(".plg-liveb");
    if(lb) lb.onclick = () => toggleLive(!live.running, body);
    const rb = body.querySelector(".plg-render");
    if(rb) rb.onclick = render;
  }
  async function fillDevices(body){
    if(!devices){ try{ devices = await api("/api/plugins/devices"); }catch(e){ devices = {inputs: [], outputs: []}; } }
    const d = dev(), ins = devices.inputs || [], outs = devices.outputs || [];
    const inSel = body.querySelector(".plg-in"), outSel = body.querySelector(".plg-out"), chSel = body.querySelector(".plg-ch");
    if(!inSel) return;
    const inPick = d.input && ins.includes(d.input) ? d.input : (ins.find(n => !/blackhole|teams|macbook/i.test(n)) || ins[0] || "");
    inSel.innerHTML = ins.map(n => `<option ${n === inPick ? "selected" : ""}>${escapeHtml(n)}</option>`).join("");
    const outPick = d.output && outs.includes(d.output) ? d.output : (outs.find(n => /blackhole/i.test(n)) || outs[0] || "");
    outSel.innerHTML = outs.map(n => `<option value="${escapeHtml(n)}" ${n === outPick ? "selected" : ""}>${escapeHtml(n)}${/blackhole/i.test(n) ? " (do Picku)" : " (rovnou, mimo Pick)"}</option>`).join("");
    chSel.value = d.channel || "1";
    inSel.onchange = () => keepDev({input: inSel.value}); outSel.onchange = () => keepDev({output: outSel.value}); chSel.onchange = () => keepDev({channel: chSel.value});
  }
  async function toggleLive(on, body){
    const btn = body.querySelector(".plg-liveb"); btn.disabled = true;
    if(on) showToast("Spouštím pluginy živě… (Neural DSP se načítá pár vteřin)");
    const inp = body.querySelector(".plg-in").value, ch = body.querySelector(".plg-ch").value, outp = body.querySelector(".plg-out").value;
    keepDev({input: inp, channel: ch, output: outp});
    try{
      await api("/api/plugins/live", json("POST", {on, input: inp, channel: ch, output: outp}));
      if(on && /blackhole/i.test(outp) && typeof Mixer !== "undefined" && Mixer.tunnel){
        try{ await Mixer.tunnel().on(); }catch(e){}         // Pick plays what comes out of the plugins
      }
      showToast(on ? "Pluginy hrají živě" : "Pluginy vypnuté");
    }catch(e){ showToast(e.message); }
    paint();
    window.dispatchEvent(new Event("pick-plugins-changed"));            // the names on the guitar's strip
  }
  async function scan(){
    try{ await api("/api/plugins/scan", {method: "POST"}); }catch(e){ showToast(e.message); return; }
    clearInterval(scanT);
    scanT = setInterval(async () => {
      if(!win || win.hidden){ clearInterval(scanT); return; }
      const d = await api("/api/plugins").catch(() => null);
      if(!d) return;
      cat = d.items || [];
      paint(d);
      if(!d.scan.running){ clearInterval(scanT); showToast(`Naskenováno: ${effects().length} efektů`); }
    }, 1500);
  }
  async function render(){
    try{ await api("/api/plugins/render", json("POST", {song: o.song, stem: o.stem})); }catch(e){ showToast(e.message); return; }
    paint();
    clearInterval(jobT);
    jobT = setInterval(async () => {
      const j = await api("/api/plugins/job").catch(() => null);
      if(!j || j.status === "running") return;
      clearInterval(jobT);
      if(j.status === "done"){
        showToast(`Nová stopa: ${(j.result || {}).label || "z pluginů"}`);
        if(typeof Mixer !== "undefined" && Mixer.reloadTracks) Mixer.reloadTracks();
      } else if(j.status === "error") showToast(j.error || "Nepovedlo se");
      if(win && !win.hidden) paint();
    }, 2000);
  }

  // {key: "live" | "stem:<song>|<stem>", title, song, stem}
  async function open(opts){
    o = opts;
    if(!win) build();
    win.hidden = false;
    win.querySelector(".plg-title").textContent = o.title || "Pluginy";
    let d = null;
    try{ d = await load(); }catch(e){ showToast(e.message); }
    paint(d);
    if(d && !cat.length && !d.scan.running) scan();          // the first time: scanned by itself
    if(d && d.scan.running) scan();
  }
  function close(){ if(win) win.hidden = true; clearInterval(scanT); }
  const forStem = (song, stem, label) => open({key: `stem:${song}|${stem}`, title: `Pluginy — ${label || stem}`, song, stem});
  return {open, close, live: () => open({key: "live", title: "Pluginy — živá kytara"}), forStem};
})();
