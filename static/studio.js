// Skladatel — a song out of an idea. A piece of guitar (a file, or played in
// now), a few styles, and whole songs come back to choose from: the idea as it
// was played, and a band that takes it on from where it ends. With a text the
// song is sung; the one that is chosen goes into the library, is taken apart
// into stems and — when asked — sung again in the player's own voice.
// The making is the server's (studio.py, the model runs beside the app) and
// takes minutes, so this window only asks and watches: it can be closed and
// opened again, the work goes on.
const Composer = (() => {
  let box = null, st = null, pid = null, timer = 0, rec = null, stream = null, voices = [], recT0 = 0;
  const form = {styles: ["hardrock"], custom: "", per: 1, length: 90, lyrics: "", lang: "cs", name: ""};
  try{ Object.assign(form, JSON.parse(localStorage.getItem("pick-composer") || "{}")); }catch(e){}
  const keep = () => { try{ localStorage.setItem("pick-composer", JSON.stringify({styles: form.styles, per: form.per, length: form.length, lang: form.lang})); }catch(e){} };
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const json = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const mins = (s) => s < 90 ? `${Math.max(10, Math.round(s / 10) * 10)} s` : `${Math.round(s / 60)} min`;
  const project = () => st && st.projects.find((p) => p.id === pid) || null;
  const busy = () => st && st.waiting > 0;

  // chosen variants on their way into Steems: until their stems are there, the list is asked again now and then
  let stemsAt = 0;
  const stemOf = (v) => v.used ? v.used.replace(/^Songs\//, "").replace(/\.mp3$/, "") : "";
  const pending = () => st ? st.projects.flatMap((p) => p.variants).filter((v) => v.used && !steemsSongs.some((x) => x.name === stemOf(v))) : [];
  async function load(){
    try{ st = await api("/api/studio"); }catch(e){ st = null; }
    if(st && pid && !project()) pid = null;
    if(pending().length && Date.now() - stemsAt > 15000){
      stemsAt = Date.now();
      try{ steemsSongs = await (await fetch("/api/stems")).json(); }catch(e){}
    }
    return st;
  }
  function watch(){
    clearTimeout(timer);
    if(!box && !busy() && !pending().length) return;
    timer = setTimeout(async () => {
      const was = st ? st.projects.flatMap((p) => p.variants).filter((v) => v.status === "done").length : 0;
      await load();
      const now = st ? st.projects.flatMap((p) => p.variants).filter((v) => v.status === "done").length : 0;
      if(now > was && !box) showToast("Skladatel: další varianta je hotová.");
      if(box) paintResults();
      if(busy() || box || pending().length) watch();
    }, busy() ? 2500 : 6000);
  }

  // ---- the idea: a file, or played in now
  async function newProject(file){
    const fd = new FormData();
    if(file) fd.append("file", file, file.name || "nápad.webm");
    const name = (box.querySelector("#cp-name").value || "").trim();
    try{
      const p = await api("/api/studio/projects?name=" + encodeURIComponent(name), {method: "POST", body: fd});
      pid = p.id; await load(); paint();
      return p;
    }catch(e){ showToast(e.message); return null; }
  }
  function stopMic(){ if(stream){ stream.getTracks().forEach((t) => t.stop()); stream = null; } rec = null; }
  async function record(){
    if(rec){ rec.stop(); return; }
    try{ stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}}); }
    catch(e){ showToast("Vstup se nepodařilo otevřít: " + e.message); return; }
    const parts = [];
    rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => { if(e.data.size) parts.push(e.data); };
    rec.onstop = () => { stopMic(); const blob = new Blob(parts, {type: (parts[0] && parts[0].type) || "audio/webm"});
      newProject(new File([blob], "Nápad.webm", {type: blob.type})); };
    rec.start(); recT0 = performance.now();
    const tick = () => { if(!rec || !box) return; const s = (performance.now() - recT0) / 1000, b = box.querySelector("#cp-rec");
      if(b) b.textContent = `■ Hotovo (${s.toFixed(0)} s)`; if(s >= 60){ rec.stop(); return; } setTimeout(tick, 300); };
    tick();
  }

  // ---- the window
  function paint(){
    if(!box) return;
    const p = project();
    let left = "";
    if(!st) left = `<p class="cp-note">Načítám…</p>`;
    else if(!st.ready) left = `<p class="cp-note bad">Model pro skládání v tomhle počítači není nainstalovaný.</p>`;
    else {
      const est = (15 + form.length * 1.6) * Math.max(1, (form.styles.length + (form.custom.trim() ? 1 : 0)) * form.per) + (st.engine ? 0 : 130);
      left = `
        <div class="cp-cap">Projekt</div>
        <select id="cp-project"><option value="">＋ Nový song</option>${st.projects.map((x) => `<option value="${x.id}" ${x.id === pid ? "selected" : ""}>${esc(x.name)}${x.idea ? "" : " (bez nápadu)"} · ${x.variants.length}×</option>`).join("")}</select>
        ${p ? `<div class="cp-idea">${p.idea ? `<span>Tvůj nápad · ${p.idea_len.toFixed(0)} s</span><audio controls src="${p.idea_url}"></audio>` : `<span>Bez nahraného nápadu — song vznikne jen podle stylu a textu.</span>`}
            <button type="button" class="cp-x" id="cp-drop" title="Smazat celý projekt i s variantami">smazat projekt</button></div>`
          : `<div class="cp-cap">1 · Nápad</div>
            <input type="text" id="cp-name" maxlength="80" placeholder="Název songu (nepovinné)" value="${esc(form.name)}">
            <div class="cp-row"><button type="button" class="btn secondary" id="cp-file">Nahrát soubor s kytarou…</button>
              <button type="button" class="btn secondary" id="cp-rec">● Nahrát teď (do 60 s)</button></div>
            <div class="cp-row"><button type="button" class="btn secondary" id="cp-none">Bez nápadu — jen styl a text</button></div>
            <p class="cp-note">Stačí 10–40 vteřin: riff, akordy, kus sloky. Nápad zůstane na začátku tak, jak jsi ho zahrál, a kapela na něj naváže ve stejném tempu a tónině.</p>`}
        <div class="cp-cap">${p ? "Styly" : "2 · Styly"} <small>— z každého vznikne vlastní verze</small></div>
        <div class="cp-chips">${st.styles.map((s) => `<button type="button" data-style="${s.id}" class="${form.styles.includes(s.id) ? "on" : ""}">${esc(s.name)}</button>`).join("")}</div>
        <input type="text" id="cp-custom" maxlength="300" placeholder="Vlastní styl anglicky, např. dark doom metal, slow, female vocals" value="${esc(form.custom)}">
        <div class="cp-row two"><label>Variant od každého<select id="cp-per">${[1, 2, 3].map((n) => `<option ${n === form.per ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          <label>Délka songu<select id="cp-length">${st.lengths.map((n) => `<option value="${n}" ${n === form.length ? "selected" : ""}>${n < 60 ? n + " s" : (n / 60).toString().replace(".", ",") + " min"}</option>`).join("")}</select></label></div>
        <div class="cp-cap">${p ? "Text" : "3 · Text"} <small>— nepovinné; bez textu vznikne instrumentálka</small></div>
        <textarea id="cp-lyrics" placeholder="Napiš text písně. Prázdný řádek odděluje sloku a refrén.\nMůžeš i označit části: [Verse] [Chorus] [Bridge] [Solo]">${esc(form.lyrics || (p && p.lyrics) || "")}</textarea>
        <div class="cp-row two"><label>Jazyk zpěvu<select id="cp-lang">${Object.entries(st.langs).map(([k, l]) => `<option value="${k}" ${k === form.lang ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label><span></span></div>
        <p class="cp-note">Potrvá to asi ${mins(est)}${st.engine ? "" : " (z toho dvě minuty probouzení modelu)"}. Okno můžeš zavřít, skládá se dál. Zpívá hlas, který si model vymyslí — <b>tvým hlasem</b> ho přezpíváš u hotové varianty.</p>
        <div class="cp-row"><button type="button" class="btn" id="cp-go" ${p ? "" : "disabled"} title="${p ? "" : "Nejdřív nahraj nápad, nebo zvol Bez nápadu"}">Složit</button></div>`;
    }
    box.innerHTML = `<div class="cp-box">
      <div class="cp-head"><b>Skladatel — song z nápadu</b><span class="cp-eng" id="cp-eng"></span><button type="button" class="cp-close" id="cp-close" title="Zavřít (skládání běží dál)">×</button></div>
      <div class="cp-cols"><div class="cp-left">${left}</div><div class="cp-right" id="cp-right"></div></div></div>`;
    const q = (id) => box.querySelector("#" + id), on = (id, ev, fn) => { const n = q(id); if(n) n[ev] = fn; };
    on("cp-close", "onclick", close);
    on("cp-project", "onchange", (e) => { pid = e.target.value || null; form.lyrics = ""; paint(); });
    on("cp-name", "oninput", (e) => { form.name = e.target.value; });
    on("cp-file", "onclick", () => { const i = document.createElement("input"); i.type = "file"; i.accept = "audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.aif,.aiff";
      i.onchange = () => { if(i.files[0]) newProject(i.files[0]); }; i.click(); });
    on("cp-rec", "onclick", record);
    on("cp-none", "onclick", () => newProject(null));
    on("cp-drop", "onclick", async () => { if(!confirm("Smazat projekt i se všemi variantami? Co už je v knihovně, zůstane.")) return;
      try{ await api("/api/studio/projects/" + pid, {method: "DELETE"}); pid = null; await load(); paint(); }catch(e){ showToast(e.message); } });
    box.querySelectorAll("[data-style]").forEach((b) => b.onclick = () => { const k = b.dataset.style;
      form.styles = form.styles.includes(k) ? form.styles.filter((x) => x !== k) : [...form.styles, k]; keep(); grab(); paint(); });
    on("cp-custom", "oninput", (e) => { form.custom = e.target.value; });
    on("cp-per", "onchange", (e) => { form.per = +e.target.value; keep(); grab(); paint(); });
    on("cp-length", "onchange", (e) => { form.length = +e.target.value; keep(); grab(); paint(); });
    on("cp-lang", "onchange", (e) => { form.lang = e.target.value; keep(); });
    on("cp-lyrics", "oninput", (e) => { form.lyrics = e.target.value; });
    on("cp-go", "onclick", make);
    paintResults();
  }
  const grab = () => { const t = box && box.querySelector("#cp-lyrics"); if(t) form.lyrics = t.value; };
  async function make(){
    grab();
    try{
      await api(`/api/studio/projects/${pid}/make`, json({styles: form.styles, custom: form.custom, per: form.per, length: form.length, lyrics: form.lyrics, lang: form.lang}));
      form.custom = ""; await load(); paint(); watch();
      showToast("Skládám. Hotové varianty se objeví vpravo.");
    }catch(e){ showToast(e.message); }
  }

  function paintResults(){
    if(!box) return;
    const right = box.querySelector("#cp-right"), eng = box.querySelector("#cp-eng"), p = project();
    if(eng) eng.textContent = !st ? "" : busy() ? `skládá se · ve frontě ${st.waiting}` : st.engine ? "model je připravený" : "";
    if(!right) return;
    const list = (p ? [p] : (st ? st.projects : [])).filter((x) => x.variants.length);
    if(!list.length){ right.innerHTML = `<div class="cp-empty"><b>Tady budou hotové verze.</b><p>Nahraj nápad, vyber pár stylů a dej Složit. Každou verzi si poslechneš a tu nejlepší pošleš do Steems — tam ji máš po stopách, s taby, akordy i zpěvem svým hlasem.</p></div>`; return; }
    // players that are playing are left alone: a repaint must not cut the music
    const playing = [...right.querySelectorAll("audio")].find((a) => !a.paused);
    if(playing && right.dataset.sig === sig(list)) { tickBars(right, list); return; }
    right.dataset.sig = sig(list);
    right.innerHTML = list.map((x) => `${p ? "" : `<div class="cp-cap">${esc(x.name)}</div>`}` + x.variants.slice().reverse().map((v) => card(x, v)).join("")).join("");
    right.querySelectorAll("[data-use]").forEach((b) => b.onclick = () => use(b.dataset.p, b.dataset.use, b.dataset.voice === "1"));
    right.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => { close(); setMode("steems"); loadMixerSong(b.dataset.open); });
    right.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { try{ await api(`/api/studio/projects/${b.dataset.p}/variants/${b.dataset.del}`, {method: "DELETE"}); await load(); paintResults(); }catch(e){ showToast(e.message); } });
    right.querySelectorAll("audio").forEach((a) => a.addEventListener("play", () => right.querySelectorAll("audio").forEach((o) => { if(o !== a) o.pause(); })));
  }
  const sig = (list) => list.map((x) => x.id + ":" + x.variants.map((v) => v.id + v.status + (v.used || "") + (v.sung || "")).join(",")).join("|");
  function tickBars(right, list){
    for(const x of list) for(const v of x.variants) if(v.status === "running"){
      const bar = right.querySelector(`[data-bar="${v.id}"]`); if(bar) bar.style.width = Math.min(97, Math.round(v.elapsed / v.estimate * 100)) + "%"; }
  }
  function card(x, v){
    const stemName = stemOf(v), hasStems = v.used && steemsSongs.some((s) => s.name === stemName);
    let body = "";
    if(v.status === "queued") body = `<div class="cp-st">čeká ve frontě</div>`;
    else if(v.status === "running") body = `<div class="cp-st">skládá se… zbývá asi ${mins(Math.max(10, v.estimate - v.elapsed))}</div><div class="cp-bar"><i data-bar="${v.id}" style="width:${Math.min(97, Math.round(v.elapsed / v.estimate * 100))}%"></i></div>`;
    else if(v.status === "error") body = `<div class="cp-st bad">nepovedlo se: ${esc(v.error || "")}</div>`;
    else body = `<audio controls preload="none" src="${v.url}"></audio>
      <div class="cp-acts">
        ${v.used ? `<span class="cp-used">${hasStems ? "je ve Steems" : "v knihovně — rozděluje se na stopy"}${v.sung ? ` · zpívá ${esc(v.sung)}` : ""}</span>
            ${hasStems ? `<button type="button" class="btn secondary" data-open="${esc(stemName)}">Otevřít ve Steems</button>` : ""}`
          : `<button type="button" class="btn" data-use="${v.id}" data-p="${x.id}" title="Uloží song do knihovny a rozdělí ho na stopy">Tuhle chci — do Steems</button>`}
        ${x.lyrics && !v.sung ? `<button type="button" class="btn secondary" data-use="${v.id}" data-p="${x.id}" data-voice="1" title="Uloží song, rozdělí ho na stopy a zpěv přezpívá tvým hlasem">…a zazpívat mým hlasem</button>` : ""}
      </div>`;
    return `<div class="cp-card ${v.status}"><div class="cp-card-head"><b>${esc(v.name)}</b><span>${v.length < 60 ? v.length + " s" : (v.length / 60).toString().replace(".", ",") + " min"}${v.took ? ` · složeno za ${mins(v.took)}` : ""}</span>
      ${v.status === "running" ? "" : `<button type="button" class="cp-x" data-del="${v.id}" data-p="${x.id}" title="Zahodit tuhle variantu">×</button>`}</div>${body}</div>`;
  }
  async function use(p, vid, sing){
    let voice = "";
    if(sing){
      try{ voices = (await api("/api/myvoice")).voices; }catch(e){ voices = []; }
      if(!voices.length){ if(confirm("Ještě nemáš vlastní hlas. Vytvořit ho teď? (přečteš dva krátké texty a kousek zazpíváš)")){ close(); VoiceGuide.open(() => open()); } return; }
      voice = voices.length === 1 ? voices[0].id : pickVoice();
      if(!voice) return;
    }
    try{
      await api(`/api/studio/projects/${p}/variants/${vid}/use`, json({separate: true, voice}));
      showToast(sing ? "Ukládám, rozděluji na stopy a pak zazpívám tvým hlasem — dám vědět ve Steems." : "Song je v knihovně a rozděluje se na stopy.");
      await load(); paintResults();
    }catch(e){ showToast(e.message); }
  }
  function pickVoice(){
    const n = prompt("Kterým hlasem?\n" + voices.map((v, i) => `${i + 1} = ${v.name}${v.sung ? " (se vzorkem zpěvu)" : ""}`).join("\n"), "1");
    const v = voices[parseInt(n, 10) - 1];
    return v ? v.id : "";
  }

  async function open(){
    close();
    box = document.createElement("div");
    box.className = "cp-overlay";
    document.body.appendChild(box);
    paint(); await load();
    if(st && !pid){ const live = st.projects.find((p) => p.variants.some((v) => v.status === "queued" || v.status === "running")); if(live) pid = live.id; }
    paint(); watch();
  }
  function close(){ stopMic(); if(box){ box.remove(); box = null; } }
  // work that was going on when the page was loaded is watched again
  load().then(() => { if(busy()) watch(); });
  return {open, close, state: () => st};
})();
