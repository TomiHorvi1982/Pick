// ======================================================================
// Kontakt — Tools → Kontakt (server: kontakt.py, kontakt_host.py).
// Zvuky Kontaktu: an instrument chosen in Kontakt's own window, kept by Pick under a name — the
// virtual guitar plays its riffs through it. Smyčky a samply: what the libraries ship as plain
// audio (Riffage – Metal's loops, dry and through the amp; Axe Machina…), by tempo, key and kind;
// heard, a dry one through a song's tone print, or put into Steems as a project.
// ======================================================================
const KontaktPage = (() => {
  const enc = encodeURIComponent, esc = (s) => escapeHtml(String(s == null ? "" : s));
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const post = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  let root = null, shown = false, st = null, list = null, f = {q: "", lib: "", bpm: 0, key: "", kind: "", loops: true, page: 1}, prints = [], via = "";
  let watch = 0, ac = null, src = null, chain = null, chainKey = "", playing = "";
  try{ Object.assign(f, JSON.parse(localStorage.getItem("pick-kontakt-f") || "{}"), {page: 1}); via = localStorage.getItem("pick-kontakt-via") || ""; }catch(e){}
  const keepF = () => { try{ localStorage.setItem("pick-kontakt-f", JSON.stringify(f)); localStorage.setItem("pick-kontakt-via", via); }catch(e){} };

  function css(){
    if(document.getElementById("kt-css")) return;
    const s = document.createElement("style"); s.id = "kt-css";
    s.textContent = `
      .kt-page{padding:4px 2px 30px;display:flex;flex-direction:column;gap:14px}
      .kt-page h2{font-size:22px;font-weight:800;margin:0}.kt-page h3{font-size:15px;margin:6px 0 0}
      .kt-sub{color:var(--sub);font-size:13px;margin:0;line-height:1.5}
      .kt-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px}
      .kt-row label{display:flex;gap:6px;align-items:center;color:var(--sub)}
      .kt-row select,.kt-row input[type=text]{background:#0d0e17;border:1px solid #33364a;border-radius:8px;color:var(--text);font:inherit;padding:5px 8px}
      .kt-row .btn{border-radius:500px;padding:6px 14px;font-size:12.5px}
      .kt-card{border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:10px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
      .kt-card b{flex:1;min-width:140px}
      .kt-list{display:flex;flex-direction:column;gap:2px;max-height:56vh;overflow-y:auto;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:4px}
      .kt-it{display:grid;grid-template-columns:34px minmax(0,2.4fr) minmax(0,2fr) 70px 46px 92px 96px;gap:8px;align-items:center;font-size:12.5px;padding:4px 6px;border-radius:8px}
      .kt-it:hover{background:rgba(255,255,255,.04)}.kt-it.on{background:color-mix(in srgb,var(--accent) 18%,transparent)}
      .kt-it .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kt-it .s{color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .kt-it button{border:1px solid rgba(255,255,255,.18);background:none;color:var(--text);border-radius:500px;font:inherit;font-size:12px;padding:3px 8px;cursor:pointer}
      .kt-tag{font-size:11px;border-radius:500px;padding:1px 7px;background:rgba(255,255,255,.08);text-align:center}
      .kt-tag.dry{background:color-mix(in srgb,#4fd1c5 30%,transparent)}.kt-tag.amp{background:color-mix(in srgb,#f56565 30%,transparent)}
      .kt-job{font-size:13px;color:var(--sub)}`;
    document.head.appendChild(s);
  }
  async function show(container){
    root = container; shown = true; css();
    root.innerHTML = `<div class="kt-page"><p class="kt-sub">Načítám…</p></div>`;
    await Promise.all([load(), find(), (async () => { try{ prints = await Tone.prints(); }catch(e){ prints = []; } })()]);
    paint();
  }
  function hide(){ shown = false; stopSound(); clearTimeout(watch); }
  async function load(){ try{ st = await api("/api/kontakt"); }catch(e){ st = {plugin: null, presets: [], job: {}, libs: []}; } }
  async function find(){
    const ps = Object.entries({q: f.q, lib: f.lib, bpm: f.bpm, key: f.key, kind: f.kind, loops: f.loops ? "true" : "", page: f.page}).filter(([, v]) => v);
    try{ list = await api(`/api/kontakt/sounds?${new URLSearchParams(ps)}`); }catch(e){ list = {total: 0, items: [], libs: [], facets: {bpm: [], key: []}}; } }
  function follow(){
    clearTimeout(watch);
    if(!shown || !st || !st.job || st.job.status !== "running") return;
    watch = setTimeout(async () => { await load(); paint(); follow(); }, 2500);
  }
  function paint(){
    if(!root || !shown) return;
    const j = st.job || {}, libs = list.libs || st.libs || [];
    root.innerHTML = `<div class="kt-page">
      <h2>Kontakt</h2>
      <p class="kt-sub">Tvoje knihovny Native Instruments v Picku. Zvuk nástroje si vybereš v okně Kontaktu a virtuální kytara přes něj zahraje tvoje riffy. Volné smyčky a samply z knihoven si tady poslechneš a dáš do Steems.</p>
      <h3>Zvuky Kontaktu</h3>
      ${st.plugin ? `<p class="kt-sub">${esc(st.plugin)} · otevře se jeho vlastní okno: vyber knihovnu a nástroj (třeba Riffage – Metal nebo Electric Storm), nastav ho a okno zavři — Pick si ho uloží.</p>`
        : `<p class="kt-sub">Kontakt 7/8 (VST3) v počítači není.</p>`}
      ${j.status ? `<div class="kt-job">${j.status === "running" ? "⏳ " : j.status === "done" ? "✓ " : "⚠︎ "}${esc(j.note || "")}</div>` : ""}
      ${(st.presets || []).map((p) => `<div class="kt-card"><b>${esc(p.name)}</b>
        <button type="button" class="btn secondary" data-try="${esc(p.id)}">▶ Zkusit</button>
        <button type="button" class="btn secondary" data-edit="${esc(p.id)}" data-name="${esc(p.name)}">Upravit v Kontaktu</button>
        <button type="button" class="btn secondary" data-vg="${esc(p.id)}">Virtuální kytara</button>
        <button type="button" class="btn secondary" data-del="${esc(p.id)}">Smazat</button></div>`).join("")}
      ${st.plugin ? `<div class="kt-row"><button type="button" class="btn" id="kt-new" ${j.status === "running" ? "disabled" : ""}>＋ Nový zvuk — otevřít Kontakt</button></div>` : ""}
      <h3>Smyčky a samply z knihoven</h3>
      <p class="kt-sub">Jen to, co knihovny mají jako obyčejné zvukové soubory (${libs.filter((l) => l.sounds).map((l) => `${esc(l.name)}: ${l.sounds}`).join(", ") || "nic"}). „Čistá“ je kytara bez zesilovače (DI) — tu si pustíš i přes tónový otisk písně.</p>
      <div class="kt-row">
        <input type="text" id="kt-q" placeholder="Hledat (název, sada…)" value="${esc(f.q)}">
        <label>Knihovna <select id="kt-lib"><option value="">všechny</option>${libs.filter((l) => l.sounds).map((l) => `<option ${l.name === f.lib ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></label>
        <label>Tempo <select id="kt-bpm"><option value="">—</option>${(list.facets.bpm || []).map((b) => `<option value="${b}" ${b === +f.bpm ? "selected" : ""}>${b}</option>`).join("")}</select></label>
        <label>Tónina <select id="kt-key"><option value="">—</option>${(list.facets.key || []).map((k) => `<option ${k === f.key ? "selected" : ""}>${esc(k)}</option>`).join("")}</select></label>
        <label>Druh <select id="kt-kind"><option value="">vše</option><option value="dry" ${f.kind === "dry" ? "selected" : ""}>čistá (DI)</option><option value="amp" ${f.kind === "amp" ? "selected" : ""}>se zesilovačem</option></select></label>
        <label><input type="checkbox" id="kt-loops" ${f.loops ? "checked" : ""}> jen smyčky</label>
      </div>
      <div class="kt-row"><label>Přehrát přes <select id="kt-via"><option value="">— jak je nahraná —</option>${prints.map((p) => `<option value="${esc(p.song + "|" + p.id)}" ${via === p.song + "|" + p.id ? "selected" : ""}>otisk: ${esc((p.artist ? p.artist + " – " : "") + (p.title || p.song))} · ${esc(p.name)}</option>`).join("")}</select></label>
        <span class="kt-sub" id="kt-via-st"></span></div>
      <div class="kt-sub">${list.total} zvuků${list.total > 120 ? ` · stránka ${f.page} z ${Math.ceil(list.total / 120)}` : ""}</div>
      <div class="kt-list">${list.items.map((x) => `<div class="kt-it ${playing === x.path ? "on" : ""}">
          <button type="button" data-play="${esc(x.path)}" data-kind="${esc(x.kind)}" title="Poslechnout">${playing === x.path ? "■" : "▶"}</button>
          <span class="n" title="${esc(x.path)}">${esc(x.name.replace(/_/g, " "))}</span><span class="s" title="${esc(x.set)}">${esc(x.lib)} · ${esc(x.set)}</span>
          <span>${x.bpm ? x.bpm + " BPM" : ""}</span><span>${esc(x.key || "")}</span>
          <span class="kt-tag ${x.kind}">${x.kind === "dry" ? "čistá" : x.kind === "amp" ? "zesilovač" : x.loop ? "smyčka" : "sample"}</span>
          <button type="button" data-steems="${esc(x.path)}" title="Otevřít jako projekt ve Steems — můžeš na něj hrát, smyčkovat, nahrávat">Do Steems</button></div>`).join("") || `<p class="kt-sub" style="padding:8px">Nic tomu neodpovídá.</p>`}</div>
      ${list.total > 120 ? `<div class="kt-row"><button type="button" class="btn secondary" id="kt-prev" ${f.page > 1 ? "" : "disabled"}>← Předchozí</button><button type="button" class="btn secondary" id="kt-next" ${f.page * 120 < list.total ? "" : "disabled"}>Další →</button></div>` : ""}
    </div>`;
    wire(); follow();
  }
  function wire(){
    const q = (id) => root.querySelector("#" + id);
    const refind = async () => { f.page = 1; keepF(); await find(); paint(); };
    let t = 0;
    q("kt-q").oninput = (e) => { f.q = e.target.value; clearTimeout(t); t = setTimeout(async () => { f.page = 1; keepF(); await find(); paint(); const i = q("kt-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 400); };
    q("kt-lib").onchange = (e) => { f.lib = e.target.value; f.bpm = 0; f.key = ""; refind(); };
    q("kt-bpm").onchange = (e) => { f.bpm = +e.target.value || 0; refind(); };
    q("kt-key").onchange = (e) => { f.key = e.target.value; refind(); };
    q("kt-kind").onchange = (e) => { f.kind = e.target.value; refind(); };
    q("kt-loops").onchange = (e) => { f.loops = e.target.checked; refind(); };
    q("kt-via").onchange = (e) => { via = e.target.value; keepF(); stopSound(); };
    if(q("kt-prev")) q("kt-prev").onclick = async () => { f.page--; await find(); paint(); };
    if(q("kt-next")) q("kt-next").onclick = async () => { f.page++; await find(); paint(); };
    if(q("kt-new")) q("kt-new").onclick = () => edit("", "");
    root.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => edit(b.dataset.edit, b.dataset.name));
    root.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
      if(!await Dialog.confirm("Smazat tenhle zvuk Kontaktu? Knihovny ani Kontakt se nemění.", {ok: "Smazat", danger: true})) return;
      try{ await api("/api/kontakt/presets/" + enc(b.dataset.del), {method: "DELETE"}); }catch(e){ showToast(e.message); }
      await load(); paint(); });
    root.querySelectorAll("[data-vg]").forEach((b) => b.onclick = () => VGuitar.withKontakt(b.dataset.vg));
    root.querySelectorAll("[data-try]").forEach((b) => b.onclick = () => tryPreset(b.dataset.try, b));
    root.querySelectorAll("[data-play]").forEach((b) => b.onclick = () => playing === b.dataset.play ? stopSound() : hear(b.dataset.play));
    root.querySelectorAll("[data-steems]").forEach((b) => b.onclick = async () => {
      try{ const r = await api("/api/kontakt/to-steems", post({path: b.dataset.steems})); stopSound(); showToast("Otevírám ve Steems…");
        if(typeof openSteemsSong === "function"){ try{ steemsSongs = await (await fetch("/api/stems")).json(); }catch(e){} openSteemsSong(r.name); } }
      catch(e){ showToast(e.message); } });
  }
  async function edit(id, name){
    if(!id){ name = await Dialog.prompt("Jak se má zvuk jmenovat?", "", {placeholder: "např. Riffage – rytmická, Electric Storm – čistá"}); if(!name) return; }
    try{ await api("/api/kontakt/presets", post({id, name})); }catch(e){ showToast(e.message); return; }
    showToast("Otevírám Kontakt — okno se objeví za chvilku (načtení trvá asi půl minuty). Vyber nástroj a okno zavři.");
    await load(); paint();
  }
  // a short phrase through the sound: E5, a riff up the low string, an open chord
  async function tryPreset(id, btn){
    const n = [];
    [[40, 47, 52], [40, 47, 52]].forEach((ch, i) => ch.forEach((m) => n.push({m, t: i * 0.5, d: 0.4, v: 110})));
    [40, 40, 43, 40, 45, 40, 46, 45].forEach((m, i) => n.push({m, t: 1 + i * 0.25, d: 0.2, v: 100}));
    [40, 47, 52, 56, 59, 64].forEach((m, i) => n.push({m, t: 3.2 + i * 0.03, d: 1.8, v: 90}));
    btn.disabled = true; const was = btn.textContent; btn.textContent = "Kontakt hraje… (~30 s)";
    try{ const r = await api("/api/kontakt/render", post({preset: id, notes: n})); await playUrl(`/api/kontakt/render/${r.file}`, "", ""); }
    catch(e){ showToast(e.message); }
    btn.disabled = false; btn.textContent = was;
  }

  // ---- hearing a sound (a dry one through a print, if chosen)
  function stopSound(){ if(src){ try{ src.stop(); }catch(e){} src = null; } const was = playing; playing = ""; if(was && shown) paintPlay(); }
  function paintPlay(){ root && root.querySelectorAll("[data-play]").forEach((b) => { const on = b.dataset.play === playing; b.textContent = on ? "■" : "▶"; b.closest(".kt-it").classList.toggle("on", on); }); }
  async function playUrl(url, path, kind){
    stopSound();
    ac = Mixer.voiceOut().ac; if(ac.state === "suspended") await ac.resume();
    let buf;
    try{ buf = await ac.decodeAudioData(await (await fetch(url)).arrayBuffer()); }catch(e){ showToast("Zvuk se nepodařilo přehrát."); return; }
    let to = Mixer.voiceOut().out;
    if(via && kind === "dry"){
      const st2 = root && root.querySelector("#kt-via-st");
      if(chainKey !== via){
        if(chain){ chain.dispose(); chain = null; }
        if(st2) st2.textContent = "Načítám otisk…";
        try{ const i = via.lastIndexOf("|"); chain = await Tone.buildChain(via.slice(0, i), via.slice(i + 1)); chain.out.connect(Mixer.voiceOut().out);
          chain.input.gain.value = Math.pow(10, (chain.print.drive_db + (chain.print.gain_db || 0)) / 20); chainKey = via; }
        catch(e){ showToast(e.message); chain = null; chainKey = ""; }
        if(st2) st2.textContent = "";
      }
      if(chain) to = chain.input;
    }
    AudioBus.claim("kontakt"); window.dispatchEvent(new Event("pick-sound"));
    src = ac.createBufferSource(); src.buffer = buf; src.loop = !!path && /loop/i.test(path);
    src.connect(to); src.start();
    const me = src; src.onended = () => { if(src === me){ src = null; playing = ""; if(shown) paintPlay(); } };
    playing = path; paintPlay();
  }
  function hear(path){
    const it = list.items.find((x) => x.path === path) || {};
    playUrl(`/api/kontakt/file?path=${enc(path)}`, path, it.kind);
  }
  AudioBus.register("kontakt", stopSound, () => !!src);
  return {show, hide, presets: async () => { try{ return (await api("/api/kontakt")).presets; }catch(e){ return []; } }};
})();
