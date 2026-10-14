// Skladatel — a song out of an idea. A page of its own under Tools: a piece of
// guitar (a file, or played in now), the sound described by styles or in the
// player's own words, a text (written here, or by the lyricist), the model and
// all its settings — and whole songs come back to choose from. Each finished
// one is taken apart into its instruments, so it can be heard here with any of
// them switched off; the one that is chosen goes into the library and Steems.
// The making is the server's (studio.py, the model runs beside the app) and
// takes minutes: the page can be left, the work goes on, and the bar at the
// top beside the arrows says how far it is.
const Composer = (() => {
  let root = null, st = null, pid = null, timer = 0, rec = null, stream = null, voices = [], recT0 = 0, shown = false, writing = false;
  let wordTab = "genre", tagTab = "parts", tipsOpen = "";
  const DEF_OPTS = {model: "turbo", think: false, cot_caption: true, lm_temperature: 0.85, lm_cfg: 2.0, steps: 0, guidance: 7.0, shift: 3.0,
    method: "ode", adg: false, cfg_start: 0.0, cfg_end: 1.0, bpm: 0, key: "", timesig: "", seed: -1, ref_strength: 0.5, use_ref: true, separate: true};
  const form = {styles: ["hardrock"], desc: "", per: 1, length: 90, lyrics: "", lang: "cs", name: "", mode: "after", opts: {...DEF_OPTS},
    lw: {mode: "write", about: "", words: "", mood: "", rhyme: true}, plan: []};
  try{ const s = JSON.parse(localStorage.getItem("pick-composer") || "{}");
    Object.assign(form, s, {opts: {...DEF_OPTS, ...(s.opts || {})}, lw: {...form.lw, ...(s.lw || {})}}); }catch(e){}
  const keep = () => { try{ localStorage.setItem("pick-composer", JSON.stringify({styles: form.styles, desc: form.desc, per: form.per, length: form.length,
    lang: form.lang, mode: form.mode, opts: form.opts, lw: form.lw, lyrics: form.lyrics, plan: form.plan})); }catch(e){} };
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  const api = async (url, opt) => { const r = await fetch(url, opt), d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.error || "Nepodařilo se."); return d; };
  const json = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const mins = (s) => { s = Math.max(0, Math.round(s)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min` : `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`; };
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const lenName = (n) => n < 60 ? n + " s" : (n / 60).toString().replace(".", ",") + " min";
  const project = () => st && st.projects.find((p) => p.id === pid) || null;
  const busy = () => !!(st && st.progress);
  const STAGE = {wake: "probouzím model", queued: "připravuji model", drums: "přidávám bicí pod tvoji kytaru", bass: "přidávám basu", song: "skládám", stems: "rozděluji na nástroje"};
  const stageName = (s) => STAGE[s] || "skládám";
  const STEM_CZ = {vocals: "Zpěv", drums: "Bicí", bass: "Basa", guitar: "Kytara", piano: "Klávesy", other: "Ostatní"};
  const STEM_COL = {vocals: "#d8d4e8", drums: "#ff7a6b", bass: "#4d8cff", guitar: "#e9b949", piano: "#5fd39a", other: "#9aa0b4"};

  // ---- what the server is doing: polled while something is being made, shown in the bar at the top
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
    pill();
    return st;
  }
  function pill(){
    const b = document.getElementById("studio-pill");
    if(!b) return;
    const g = st && st.progress;
    b.hidden = !g;
    if(!g) return;
    const n = g.now, pct = n ? Math.round(n.pct * 100) : 0;
    b.innerHTML = `<i class="st-pill-fill" style="width:${pct}%"></i><span class="st-pill-ic">♪</span>
      <span class="st-pill-tx">${n ? `${esc(stageName(n.stage))} · ${esc(n.name)}` : "Skladatel čeká"}</span>
      <b>${n ? pct + " %" : ""}</b><span class="st-pill-left">${mins(g.left)}${g.count > 1 ? ` · ${g.count}×` : ""}</span>`;
    b.title = `Skladatel${n ? ` — ${n.project_name}: ${n.name}, ${stageName(n.stage)} ${pct} %, běží ${mins(n.elapsed)}` : ""}\nZbývá asi ${mins(g.left)} celkem (${g.count} ve frontě). Klik otevře Skladatele.`;
  }
  function watch(){
    clearTimeout(timer);
    if(!shown && !busy() && !pending().length) return;
    timer = setTimeout(async () => {
      const done = (s) => s ? s.projects.flatMap((p) => p.variants).filter((v) => v.status === "done").length : 0, was = done(st);
      await load();
      if(done(st) > was && !shown) showToast("Skladatel: další varianta je hotová.");
      if(shown) paintResults();
      watch();
    }, busy() ? 2500 : 8000);
  }

  // ---- the idea: a file, or played in now
  async function newProject(file){
    const fd = new FormData();
    if(file) fd.append("file", file, file.name || "nápad.webm");
    try{
      const p = await api("/api/studio/projects?name=" + encodeURIComponent((form.name || "").trim()), {method: "POST", body: fd});
      pid = p.id; form.name = ""; await load(); paint();
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
    const tick = () => { if(!rec || !root) return; const s = (performance.now() - recT0) / 1000, b = root.querySelector("#cp-rec");
      if(b) b.textContent = `■ Hotovo (${s.toFixed(0)} s)`; if(s >= 60){ rec.stop(); return; } setTimeout(tick, 300); };
    tick();
  }
  const pickFile = (accept, then) => { const i = document.createElement("input"); i.type = "file"; i.accept = accept; i.onchange = () => { if(i.files[0]) then(i.files[0]); }; i.click(); };

  // ---- the description: styles, own words, and words the model knows
  const descHas = (w) => form.desc.split(",").map((x) => x.trim().toLowerCase()).includes(w.toLowerCase());
  function toggleWord(w){
    const parts = form.desc.split(",").map((x) => x.trim()).filter(Boolean);
    const i = parts.findIndex((x) => x.toLowerCase() === w.toLowerCase());
    if(i >= 0) parts.splice(i, 1); else parts.push(w);
    form.desc = parts.join(", "); keep();
  }
  function insertTag(tag){
    const t = root.querySelector("#cp-lyrics"); if(!t) return;
    const a = t.selectionStart, b = t.selectionEnd, before = t.value.slice(0, a), nl = before && !before.endsWith("\n") ? "\n" : "";
    const add = tag.startsWith("[") && /^(\[(Intro|Verse|Pre-Chorus|Chorus|Bridge|Guitar Solo|Instrumental|Breakdown|Build|Final Chorus|Outro|Fade Out))/.test(tag) && before.trim() ? "\n" : "";
    t.value = before + add + nl + tag + "\n" + t.value.slice(b);
    const pos = (before + add + nl + tag + "\n").length;
    t.focus(); t.setSelectionRange(pos, pos);
    form.lyrics = t.value; keep();
  }

  // ---- how long it will take, before the model says
  function estimate(){
    const p = project(), m = st.models.find((x) => x.id === form.opts.model) || st.models[0];
    const rtf = {turbo: 1.6, sft: 4.5, base: 6}[m.id] || 1.6, count = Math.max(1, (form.styles.length || (form.desc.trim() ? 1 : 0)) * form.per);
    const under = !!(p && p.idea && st.under && form.mode === "under");
    const one = under ? 30 + p.idea_len * 6.8 + (form.length >= p.idea_len + 15 ? 145 + form.length * rtf : 0)
      : 15 + (form.opts.think && !(p && p.idea) ? 45 : 0) + form.length * rtf;
    return one * count + (form.opts.separate ? (20 + form.length * 0.45) * count : 0) + (st.engine ? 0 : 130);
  }

  // ---- the page
  function paint(){
    if(!root) return;
    if(!st){ root.innerHTML = `<div class="cp-page"><p class="cp-note">Načítám…</p></div>`; return; }
    if(!st.ready){ root.innerHTML = `<div class="cp-page"><h2 class="cp-title">Skladatel</h2><p class="cp-note bad">Model pro skládání v tomhle počítači není nainstalovaný.</p></div>`; return; }
    const p = project(), o = form.opts, m = st.models.find((x) => x.id === o.model) || st.models[0];
    const under = !!(p && p.idea && st.under && form.mode === "under");
    const sec = (id, title, sub, body, open = true) => `<details class="cp-sec" data-sec="${id}" ${secOpen(id, open) ? "open" : ""}><summary><b>${title}</b>${sub ? `<small>${sub}</small>` : ""}</summary><div class="cp-sec-b">${body}</div></details>`;
    const words = st.words.find((c) => c.id === wordTab) || st.words[0], tags = st.tags.find((c) => c.id === tagTab) || st.tags[0];
    const idea = p
      ? `<div class="cp-idea">${p.idea ? `<span>Tvůj nápad · ${p.idea_len.toFixed(0)} s</span><audio controls src="${p.idea_url}"></audio>` : `<span>Bez nahraného nápadu — song vznikne jen podle popisu a textu.</span>`}</div>
         ${p.idea ? `<label class="cp-lbl">Kdy nastoupí kapela<select id="cp-mode"><option value="after" ${under ? "" : "selected"}>Po mém nápadu — naváže, kde skončím (rychlejší)</option>
            <option value="under" ${under ? "selected" : ""} ${st.under ? "" : "disabled"}>Hraje pod mou kytarou od začátku${st.under ? " (pomalejší)" : " — model není stažený"}</option></select></label>
            ${under ? `<p class="cp-note">K tvé kytaře se přidají bicí a pak basa, každý nástroj zvlášť. Když je song delší než nápad + 15 s, kapela pak dohraje zbytek.</p>` : ""}` : ""}`
      : `<input type="text" id="cp-name" maxlength="80" placeholder="Název songu (nepovinné)" value="${esc(form.name)}">
         <div class="cp-row"><button type="button" class="btn secondary" id="cp-file">Nahrát soubor s kytarou…</button>
           <button type="button" class="btn secondary" id="cp-rec">● Nahrát teď (do 60 s)</button></div>
         <div class="cp-row"><button type="button" class="btn secondary" id="cp-none">Bez nápadu — jen popis a text</button></div>
         <p class="cp-note">Stačí 10–40 vteřin: riff, akordy, kus sloky. Nápad zůstane na začátku tak, jak jsi ho zahrál, a kapela na něj naváže ve stejném tempu a tónině.</p>`;
    const sound = `
      <div class="cp-sub">Hotové styly <small>— každý vybraný = vlastní verze; najetím uvidíš, co model dostane</small></div>
      <div class="cp-chips">${st.styles.map((s) => `<button type="button" data-style="${s.id}" class="${form.styles.includes(s.id) ? "on" : ""}" title="${esc(s.caption)}">${esc(s.name)}</button>`).join("")}</div>
      <div class="cp-sub">Popis zvuku vlastními slovy <small>— anglicky; ${form.styles.length ? "přidá se ke každému stylu" : "bez stylu je to jediná verze"}</small></div>
      <textarea id="cp-desc" class="cp-desc" placeholder="např. down-tuned heavy riffs, groovy drums, gritty raspy male vocals, dark, modern production, catchy chorus">${esc(form.desc)}</textarea>
      <div class="cp-sub">Slova, kterým model rozumí <small>— klik přidá / ubere</small></div>
      <div class="cp-tabs">${st.words.map((c) => `<button type="button" data-wtab="${c.id}" class="${c.id === words.id ? "on" : ""}">${esc(c.name)}</button>`).join("")}</div>
      <div class="cp-chips small">${words.words.map(([en, cs]) => `<button type="button" data-word="${esc(en)}" class="${descHas(en) ? "on" : ""}" title="${esc(cs)}">${esc(en)}</button>`).join("")}</div>
      ${tips("caption", "Jak psát popis, aby výsledek seděl")}
      <div class="cp-sub">Zvuková předloha <small>— song, jehož zvukem se má řídit (barva, mix, ne melodie)</small></div>
      ${p ? (p.ref ? `<div class="cp-ref"><span>🎧 ${esc(p.ref_name || "předloha")}</span><button type="button" class="cp-x" id="cp-ref-drop">odebrat</button></div>
          <label class="cp-lbl">Jak moc se jí držet: <b id="cp-refs-v">${Math.round(o.ref_strength * 100)} %</b><input type="range" id="cp-refs" min="0" max="1" step="0.05" value="${o.ref_strength}"></label>`
        : `<div class="cp-row"><button type="button" class="btn secondary" id="cp-ref">Vybrat nahrávku jako předlohu…</button></div>`)
        : `<p class="cp-note">Předlohu přidáš, až bude projekt.</p>`}`;
    const text = `
      <textarea id="cp-lyrics" class="cp-lyrics" placeholder="Text písně. Části označ [Verse] [Chorus] [Bridge] [Guitar Solo]… Prázdný text = instrumentálka.">${esc(form.lyrics || (p && p.lyrics) || "")}</textarea>
      <div class="cp-tabs">${st.tags.map((c) => `<button type="button" data-ttab="${c.id}" class="${c.id === tags.id ? "on" : ""}">${esc(c.name)}</button>`).join("")}</div>
      <div class="cp-chips small">${tags.tags.map(([t, cs]) => `<button type="button" data-tag="${esc(t)}" title="${esc(cs)}">${esc(t)}</button>`).join("")}</div>
      <label class="cp-lbl">Jazyk zpěvu<select id="cp-lang">${Object.entries(st.langs).map(([k, l]) => `<option value="${k}" ${k === form.lang ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      ${tips("lyrics", "Jak psát text, aby ho model dobře zazpíval")}
      <div class="cp-writer">
        <div class="cp-sub">Textař <small>— ${st.lyricist.length ? (st.lyricist[0] === "claude" ? "píše Claude" : "píše model v počítači") : "nemá kdo psát (chybí API klíč i model)"}</small></div>
        <div class="cp-seg">${[["write", "Napsat celý"], ["complete", "Dopsat můj"], ["polish", "Vylepšit můj"]].map(([k, l]) => `<button type="button" data-lw="${k}" class="${form.lw.mode === k ? "on" : ""}">${l}</button>`).join("")}</div>
        <input type="text" id="cp-lw-about" maxlength="400" placeholder="O čem má píseň být — věta nebo pár slov" value="${esc(form.lw.about)}">
        <input type="text" id="cp-lw-words" maxlength="300" placeholder="Slova nebo verše, které tam musí být (nepovinné)" value="${esc(form.lw.words)}">
        <input type="text" id="cp-lw-mood" maxlength="200" placeholder="Styl textu: drsný, ironický, smutný, vzdorovitý, poetický… (nepovinné)" value="${esc(form.lw.mood)}">
        <label class="cp-check"><input type="checkbox" id="cp-lw-rhyme" ${form.lw.rhyme ? "checked" : ""}> rýmovat</label>
        <div class="cp-row"><button type="button" class="btn" id="cp-lw-go" ${st.lyricist.length && !writing ? "" : "disabled"}>${writing ? "Píšu text…" : "Napsat text"}</button></div>
        <div id="cp-lw-out"></div>
      </div>`;
    const lens = [...new Set([...st.lengths, form.length])].sort((a, b) => a - b);
    const length = `<div class="cp-row two"><label class="cp-lbl">Délka songu<select id="cp-length">${lens.map((n) => `<option value="${n}" ${n === form.length ? "selected" : ""}>${lenName(n)}</option>`).join("")}</select></label>
        <label class="cp-lbl">Verzí od každého stylu<select id="cp-per">${[1, 2, 3, 4].map((n) => `<option ${n === form.per ? "selected" : ""}>${n}</option>`).join("")}</select></label></div>
      <label class="cp-check"><input type="checkbox" id="cp-sep" ${o.separate ? "checked" : ""}> hotový song hned rozdělit na nástroje (zapínáš je pak v přehrávači)</label>`;
    const num = (id, label, val, min, max, step, hint) => `<label class="cp-lbl">${label} <b id="${id}-v">${val}</b>${hint ? `<small>${hint}</small>` : ""}<input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"></label>`;
    const ideaNow = !!(p && p.idea);
    const settings = `
      <div class="cp-models">${st.models.map((x) => `<label class="cp-model ${x.id === m.id ? "on" : ""} ${x.ready ? "" : "off"}"><input type="radio" name="cp-model" value="${x.id}" ${x.id === m.id ? "checked" : ""} ${x.ready ? "" : "disabled"}>
        <b>${esc(x.name)}</b><span>${esc(x.note)}${x.ready ? "" : " — zatím není stažený."}</span></label>`).join("")}</div>
      <label class="cp-check"><input type="checkbox" id="cp-think" ${o.think ? "checked" : ""} ${st.lm ? "" : "disabled"}> <b>Přemýšlení</b> — plánovač (1,7 mld. parametrů) si song nejdřív rozvrhne: tempo, tóninu, stavbu. Často lepší celek, o minutu déle${ideaNow ? ". <i>U songu z nápadu se nepoužije — model navazuje na tvoji nahrávku.</i>" : "."}</label>
      ${o.think ? `<div class="cp-indent"><label class="cp-check"><input type="checkbox" id="cp-cot" ${o.cot_caption ? "checked" : ""}> plánovač smí popis rozvést vlastními slovy</label>
        ${num("cp-lmt", "Fantazie plánovače", o.lm_temperature, 0.3, 1.5, 0.05, "nižší = drží se zadání, vyšší = překvapí")}
        ${num("cp-lmc", "Jak přísně plánovač drží popis", o.lm_cfg, 1, 4, 0.1, "")}</div>` : ""}
      ${num("cp-steps", "Kroků", o.steps || m.steps, 1, m.max_steps, 1, `výchozí ${m.steps}; víc = jemnější a pomalejší`)}
      ${m.cfg ? `${num("cp-guid", "Síla popisu", o.guidance, 1, 15, 0.5, "jak přesně se drží popisu; moc = přepálené")}
        ${num("cp-shift", "Shift", o.shift, 1, 5, 0.5, "vyšší = nejdřív stavba celku, nižší = víc detailu")}
        <label class="cp-check"><input type="checkbox" id="cp-adg" ${o.adg ? "checked" : ""}> přizpůsobivé vedení (ADG)</label>
        <div class="cp-row two">${num("cp-cfg0", "Vedení od", o.cfg_start, 0, 1, 0.05, "")}${num("cp-cfg1", "do", o.cfg_end, 0, 1, 0.05, "")}</div>` : `<p class="cp-note">Rychlý model sílu popisu ani shift nepoužívá — má je zapečené.</p>`}
      <label class="cp-lbl">Postup<select id="cp-method"><option value="ode" ${o.method === "ode" ? "selected" : ""}>Přesný (ODE)</option><option value="sde" ${o.method === "sde" ? "selected" : ""}>S trochou náhody (SDE)</option></select></label>
      <div class="cp-row three">
        <label class="cp-lbl">Tempo (BPM)<input type="number" id="cp-bpm" min="0" max="240" placeholder="samo" value="${o.bpm || ""}"></label>
        <label class="cp-lbl">Tónina<select id="cp-key"><option value="">sama</option>${st.keys.map((k) => `<option ${k === o.key ? "selected" : ""}>${k}</option>`).join("")}</select></label>
        <label class="cp-lbl">Takt<select id="cp-ts"><option value="">sám</option>${Object.entries(st.timesigs).map(([k, l]) => `<option value="${k}" ${k === o.timesig ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
      ${ideaNow ? `<p class="cp-note">S nápadem se tempo a tónina berou z tvé nahrávky; tahle pole jsou jen nápověda.</p>` : ""}
      <label class="cp-lbl">Seed (náhoda)<span class="cp-row"><input type="number" id="cp-seed" min="0" placeholder="pokaždé jiný" value="${o.seed >= 0 ? o.seed : ""}"><button type="button" class="btn secondary" id="cp-seed-x">náhodný</button></span>
        <small>stejný seed + stejné zadání = skoro stejný song; seed povedené verze najdeš u ní</small></label>
      <div class="cp-row"><button type="button" class="btn secondary" id="cp-defaults">Vrátit výchozí nastavení</button></div>`;
    root.innerHTML = `<div class="cp-page">
      <div class="cp-top"><h2 class="cp-title">Skladatel</h2><span class="cp-eng" id="cp-eng"></span>
        <select id="cp-project"><option value="">＋ Nový song</option>${st.projects.map((x) => `<option value="${x.id}" ${x.id === pid ? "selected" : ""}>${esc(x.name)}${x.idea ? "" : " (bez nápadu)"} · ${x.variants.length}×</option>`).join("")}</select>
        ${p ? `<button type="button" class="cp-x" id="cp-drop" title="Smazat celý projekt i s variantami">smazat projekt</button>` : ""}</div>
      <div class="cp-cols"><div class="cp-left">
        ${sec("idea", p ? esc(p.name) : "1 · Nápad", p ? "projekt" : "kytara, nebo nic", idea)}
        ${sec("sound", "2 · Zvuk", "styly, vlastní popis, předloha", sound)}
        ${sec("sb", "Zpěvník", "zvuk a stavba tvých písní ze Steems, tvoje texty — předlohy pro nový song", `<div id="cp-sb"></div>`, false)}
        ${sec("text", "3 · Text", "nepovinné; bez textu instrumentálka", text)}
        ${sec("len", "4 · Délka a verze", "", length)}
        ${sec("set", "5 · Model a nastavení", `${esc(m.name)}${o.think && !ideaNow ? " + přemýšlení" : ""}`, settings, false)}
        <div class="cp-go"><span>Potrvá asi <b>${mins(estimate())}</b>${st.engine ? "" : " (vč. probuzení modelu)"}. Stránku můžeš opustit — průběh je nahoře v liště.</span>
          <button type="button" class="btn" id="cp-go" ${p ? "" : "disabled"} title="${p ? "" : "Nejdřív nahraj nápad, nebo zvol Bez nápadu"}">Složit</button></div>
      </div><div class="cp-right" id="cp-right"></div></div></div>`;
    wire();
    paintResults();
    paintSb();
  }
  const secState = (() => { try{ return JSON.parse(localStorage.getItem("pick-composer-sec") || "{}"); }catch(e){ return {}; } })();
  const secOpen = (id, def) => id in secState ? secState[id] : def;
  function tips(kind, title){
    const list = st.tips[kind] || [];
    return `<button type="button" class="cp-tipbtn" data-tips="${kind}">${tipsOpen === kind ? "▾" : "▸"} ${title}</button>${tipsOpen === kind ? `<ul class="cp-tips">${list.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}`;
  }
  function wire(){
    const q = (id) => root.querySelector("#" + id), on = (id, ev, fn) => { const n = q(id); if(n) n[ev] = fn; };
    const opt = (k, v) => { form.opts[k] = v; keep(); };
    const repaint = () => { grab(); paint(); };
    root.querySelectorAll(".cp-sec").forEach((d) => d.addEventListener("toggle", () => { secState[d.dataset.sec] = d.open;
      try{ localStorage.setItem("pick-composer-sec", JSON.stringify(secState)); }catch(e){} }));
    on("cp-project", "onchange", (e) => { pid = e.target.value || null; const p = project(); form.lyrics = p ? p.lyrics || "" : ""; if(p && p.desc) form.desc = p.desc; paint(); });
    on("cp-name", "oninput", (e) => { form.name = e.target.value; });
    on("cp-file", "onclick", () => pickFile("audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.aif,.aiff", newProject));
    on("cp-rec", "onclick", record);
    on("cp-none", "onclick", () => newProject(null));
    on("cp-drop", "onclick", async () => { if(!await Dialog.confirm("Smazat projekt i se všemi variantami? Co už je v knihovně, zůstane.", {ok: "Smazat", danger: true})) return;
      try{ await api("/api/studio/projects/" + pid, {method: "DELETE"}); pid = null; await load(); paint(); }catch(e){ showToast(e.message); } });
    on("cp-mode", "onchange", (e) => { form.mode = e.target.value; keep(); repaint(); });
    root.querySelectorAll("[data-style]").forEach((b) => b.onclick = () => { const k = b.dataset.style;
      form.styles = form.styles.includes(k) ? form.styles.filter((x) => x !== k) : [...form.styles, k]; keep(); repaint(); });
    on("cp-desc", "oninput", (e) => { form.desc = e.target.value; keep(); root.querySelectorAll("[data-word]").forEach((b) => b.classList.toggle("on", descHas(b.dataset.word))); });
    root.querySelectorAll("[data-wtab]").forEach((b) => b.onclick = () => { wordTab = b.dataset.wtab; repaint(); });
    root.querySelectorAll("[data-word]").forEach((b) => b.onclick = () => { toggleWord(b.dataset.word); const t = q("cp-desc"); if(t) t.value = form.desc; b.classList.toggle("on", descHas(b.dataset.word)); });
    root.querySelectorAll("[data-tips]").forEach((b) => b.onclick = () => { tipsOpen = tipsOpen === b.dataset.tips ? "" : b.dataset.tips; repaint(); });
    on("cp-ref", "onclick", () => pickFile("audio/*,.wav,.mp3,.m4a,.flac,.ogg", async (f) => {
      const fd = new FormData(); fd.append("file", f, f.name);
      try{ showToast("Nahrávám předlohu…"); await api(`/api/studio/projects/${pid}/reference`, {method: "POST", body: fd}); await load(); paint(); }catch(e){ showToast(e.message); } }));
    on("cp-ref-drop", "onclick", async () => { try{ await api(`/api/studio/projects/${pid}/reference`, {method: "DELETE"}); await load(); paint(); }catch(e){ showToast(e.message); } });
    on("cp-refs", "oninput", (e) => { opt("ref_strength", +e.target.value); q("cp-refs-v").textContent = Math.round(e.target.value * 100) + " %"; });
    on("cp-lyrics", "oninput", (e) => { form.lyrics = e.target.value; keep(); });
    root.querySelectorAll("[data-ttab]").forEach((b) => b.onclick = () => { tagTab = b.dataset.ttab; repaint(); });
    root.querySelectorAll("[data-tag]").forEach((b) => b.onclick = () => insertTag(b.dataset.tag));
    on("cp-lang", "onchange", (e) => { form.lang = e.target.value; keep(); });
    root.querySelectorAll("[data-lw]").forEach((b) => b.onclick = () => { form.lw.mode = b.dataset.lw; keep(); repaint(); });
    on("cp-lw-about", "oninput", (e) => { form.lw.about = e.target.value; keep(); });
    on("cp-lw-words", "oninput", (e) => { form.lw.words = e.target.value; keep(); });
    on("cp-lw-mood", "oninput", (e) => { form.lw.mood = e.target.value; keep(); });
    on("cp-lw-rhyme", "onchange", (e) => { form.lw.rhyme = e.target.checked; keep(); });
    on("cp-lw-go", "onclick", writeLyrics);
    on("cp-length", "onchange", (e) => { form.length = +e.target.value; keep(); repaint(); });
    on("cp-per", "onchange", (e) => { form.per = +e.target.value; keep(); repaint(); });
    on("cp-sep", "onchange", (e) => { opt("separate", e.target.checked); repaint(); });
    root.querySelectorAll('input[name="cp-model"]').forEach((r) => r.onchange = () => { opt("model", r.value); opt("steps", 0); repaint(); });
    on("cp-think", "onchange", (e) => { opt("think", e.target.checked); repaint(); });
    on("cp-cot", "onchange", (e) => opt("cot_caption", e.target.checked));
    const range = (id, k, fmt = (x) => x) => on(id, "oninput", (e) => { opt(k, +e.target.value); const v = q(id + "-v"); if(v) v.textContent = fmt(+e.target.value); });
    range("cp-lmt", "lm_temperature"); range("cp-lmc", "lm_cfg"); range("cp-steps", "steps"); range("cp-guid", "guidance");
    range("cp-shift", "shift"); range("cp-cfg0", "cfg_start"); range("cp-cfg1", "cfg_end");
    on("cp-steps", "onchange", repaint);
    on("cp-adg", "onchange", (e) => opt("adg", e.target.checked));
    on("cp-method", "onchange", (e) => opt("method", e.target.value));
    on("cp-bpm", "onchange", (e) => opt("bpm", Math.max(0, Math.min(240, parseInt(e.target.value, 10) || 0))));
    on("cp-key", "onchange", (e) => opt("key", e.target.value));
    on("cp-ts", "onchange", (e) => opt("timesig", e.target.value));
    on("cp-seed", "onchange", (e) => opt("seed", e.target.value === "" ? -1 : Math.max(0, parseInt(e.target.value, 10) || 0)));
    on("cp-seed-x", "onclick", () => { opt("seed", -1); repaint(); });
    on("cp-defaults", "onclick", () => { form.opts = {...DEF_OPTS, separate: form.opts.separate}; keep(); repaint(); });
    on("cp-go", "onclick", make);
  }
  const grab = () => { if(!root) return; const t = root.querySelector("#cp-lyrics"); if(t) form.lyrics = t.value; const d = root.querySelector("#cp-desc"); if(d) form.desc = d.value; };

  async function writeLyrics(){
    grab();
    const p = project(), mode = form.lw.mode;
    if(mode !== "write" && !form.lyrics.trim()){ showToast("Nejdřív napiš do pole Text pár veršů — nebo zvol Napsat celý."); return; }
    if(mode === "write" && form.lyrics.trim() && !await Dialog.confirm("Napsat nový text místo toho, co je v poli Text?", {ok: "Napsat nový"})) return;
    writing = true; paint();
    try{
      const desc = [form.styles.map((k) => (st.styles.find((s) => s.id === k) || {}).caption).filter(Boolean)[0], form.desc].filter(Boolean).join(", ");
      const out = await api("/api/studio/lyrics", json({mode, about: form.lw.about, words: form.lw.words, mood: form.lw.mood, rhyme: form.lw.rhyme,
        text: form.lyrics, lang: form.lang, desc, length: form.length, idea: !!(p && p.idea)}));
      form.lyrics = out.lyrics; keep();
      writing = false; paint();
      const box = root.querySelector("#cp-lw-out");
      if(box) box.innerHTML = `<div class="cp-lw-res">${out.title ? `<b>„${esc(out.title)}“</b>` : ""} <small>napsal ${esc(out.by)}</small>${out.note ? `<p class="cp-note bad">${esc(out.note)}</p>` : ""}
        ${out.sound ? `<p>Navržený zvuk: <i>${esc(out.sound)}</i></p><button type="button" class="btn secondary" id="cp-lw-sound">Použít jako popis zvuku</button>` : ""}</div>`;
      const b = root.querySelector("#cp-lw-sound");
      if(b) b.onclick = () => { form.desc = out.sound; keep(); paint(); showToast("Popis zvuku je doplněný."); };
    }catch(e){ writing = false; paint(); showToast(e.message); }
  }

  // ---- Zpěvník (songbook.py): how the songs in Steems sound, section by section, and how their texts are
  // built — lines, syllables, rhymes, never the words. With the player's own texts (kept whole), pieces of
  // them are put together into the plan of a new song: from the plan come the sound description, the
  // parts' tags and the shape the lyricist writes its own words into.
  let sb = null, sbBand = "", sbSong = null, sbTab = "songs", sbT = 0;
  async function sbLoad(){ try{ sb = await api("/api/songbook"); }catch(e){ sb = null; } }
  function sbWatch(){
    clearTimeout(sbT);
    if(!shown || !sb || !sb.job.running) return;
    sbT = setTimeout(async () => { await sbLoad(); if(sbSong && !sbSong.sound) await openSong(sbSong.name, true); paintSb(); sbWatch(); }, 4000);
  }
  const ENERGY = /peak energy|full energy|medium energy|quiet, sparse/, DRUMS = /blast beats and double bass drumming|fast double kick drumming|half-time drums/;
  const hintOf = (r) => ((r.desc.match(DRUMS) || r.desc.match(ENERGY) || [""])[0]).replace(" and double bass drumming", "");
  const shapeTx = (t) => !t || !t.lines ? "bez textu" : `${t.lines} ř. · ~${Math.round(t.avg)} slabik · rým ${t.rhyme || "—"}`;
  async function openSong(name, quiet){
    try{ const got = await api("/api/songbook/song/" + encodeURIComponent(name)); sbSong = {name, ...got}; }
    catch(e){ sbSong = {name}; if(!quiet) showToast(e.message); }
  }
  async function analyse(names, limit){
    try{ const r = await api("/api/songbook/analyse", json({names: names || [], limit: limit || 100}));
      showToast(r.started ? `Rozebírám ${r.started} ${r.started === 1 ? "píseň" : r.started < 5 ? "písně" : "písní"} — běží na pozadí.` : r.note || "Hotovo."); }
    catch(e){ showToast(e.message); }
    await sbLoad(); paintSb(); sbWatch();
  }
  function addRow(row){ form.plan.push(row); keep(); paintSb(); }
  function rowFromSection(song, r){
    return {tag: r.tag, hint: hintOf(r), desc: r.desc, shape: r.text && r.text.lines ? {lines: r.text.lines, avg: r.text.avg, rhyme: r.text.rhyme} : null,
      own: "", from: `${song.artist} – ${song.title}`, cap: song.sound.caption, bpm: song.sound.bpm, key: song.sound.key, ts: song.sound.timesig};
  }
  // the plan's parts as tags: verses numbered, a chorus is the chorus each time
  function planTags(){
    const n = {};
    return form.plan.map((r) => { const base = r.tag.replace(/\s+\d+$/, ""); n[base] = (n[base] || 0) + 1;
      return /^(Verse|Pre-Chorus|Bridge|Guitar Solo|Instrumental|Interlude)$/.test(base) && form.plan.filter((x) => x.tag.replace(/\s+\d+$/, "") === base).length > 1 ? `${base} ${n[base]}` : base; });
  }
  function planStructure(){
    const tags = planTags(), seen = {};
    return form.plan.map((r, i) => {
      const t = `[${tags[i]}${r.hint ? " - " + r.hint : ""}]`, base = tags[i].replace(/\s+\d+$/, "");
      if(r.own) return `${t} the player's own lines (given below)`;
      if(!r.shape) return `${t} instrumental, no lines`;
      const again = base === "Chorus" && seen.Chorus; seen[base] = true;
      return `${t} ${again ? "the chorus again, same words (the last one may change one line)" : `${r.shape.lines} lines, about ${Math.round(r.shape.avg)} syllables a line, rhyme scheme ${r.shape.rhyme || "free"}`}`;
    }).join("\n");
  }
  function planCaption(){
    const caps = [...new Set(form.plan.map((r) => r.cap).filter(Boolean))];
    const words = [...new Set(caps.flatMap((c) => c.split(",").map((x) => x.trim()).filter(Boolean)))];
    return words.slice(0, 16).join(", ");
  }
  function soundFrom(src){
    grab();
    form.desc = src.cap;
    if(src.bpm) form.opts.bpm = src.bpm;
    if(src.key && st.keys.includes(src.key)) form.opts.key = src.key;
    if(src.ts && st.timesigs[src.ts]) form.opts.timesig = src.ts;
    keep(); paint(); showToast("Popis zvuku, tempo a tónina jsou doplněné.");
  }
  async function writePlan(){
    if(!form.plan.length){ showToast("Stavba je prázdná — přidej do ní části písní."); return; }
    grab();
    if(form.lyrics.trim() && !await Dialog.confirm("Napsat nový text podle stavby místo toho, co je v poli Text?", {ok: "Napsat"})) return;
    const tags = planTags(), own = form.plan.map((r, i) => r.own ? `[${tags[i]}]\n${r.own}` : "").filter(Boolean).join("\n\n");
    writing = true; paint();
    try{
      const p = project(), desc = [planCaption(), form.desc].filter(Boolean)[0] || "";
      const out = await api("/api/studio/lyrics", json({mode: own ? "complete" : "write", about: form.lw.about, words: form.lw.words, mood: form.lw.mood,
        rhyme: form.lw.rhyme, text: own, lang: form.lang, desc, length: form.length, idea: !!(p && p.idea), structure: planStructure()}));
      form.lyrics = out.lyrics; keep(); writing = false; paint();
      showToast(`Text podle stavby napsal ${out.by}${out.note ? " — " + out.note : ""}.`);
    }catch(e){ writing = false; paint(); showToast(e.message); }
  }
  async function tagsOnly(){
    if(!form.plan.length) return;
    grab();
    if(form.lyrics.trim() && !await Dialog.confirm("Nahradit pole Text značkami stavby (a tvými texty v ní)?", {ok: "Nahradit"})) return;
    const tags = planTags();
    form.lyrics = form.plan.map((r, i) => `[${tags[i]}${r.hint ? " - " + r.hint : ""}]` + (r.own ? "\n" + r.own : r.shape ? "\n" : "")).join("\n\n") + "\n";
    keep(); paint();
  }
  async function saveMine(){
    grab();
    if(!form.lyrics.trim()){ showToast("Pole Text je prázdné."); return; }
    const title = await Dialog.prompt("Název textu:", form.name || (project() || {}).name || "", {placeholder: "např. Noční jízda"});
    if(title == null) return;
    const band = await Dialog.prompt("Kapela nebo projekt (podle toho se texty třídí):", "", {placeholder: "např. Naše kapela"});
    if(band == null) return;
    try{ await api("/api/songbook/mine", json({title, band, lang: form.lang, text: form.lyrics})); await sbLoad(); sbTab = "mine"; paintSb(); showToast("Text je ve Zpěvníku."); }
    catch(e){ showToast(e.message); }
  }
  async function paintSb(){
    const box = root && root.querySelector("#cp-sb");
    if(!box) return;
    const det = box.closest("details");
    if(det && !det.open){ det.addEventListener("toggle", () => { if(det.open) paintSb(); }, {once: true}); return; }
    if(!sb){ box.innerHTML = `<p class="cp-note">Načítám Zpěvník…</p>`; await sbLoad(); if(!sb){ box.innerHTML = `<p class="cp-note bad">Zpěvník se nepodařilo načíst.</p>`; return; } sbWatch(); }
    const all = sb.bands.reduce((n, b) => n + b.songs.length, 0), done = sb.bands.reduce((n, b) => n + b.analysed, 0), j = sb.job;
    const band = sb.bands.find((b) => b.band === sbBand) || null;
    const head = `<div class="sb-top"><span>Rozebráno <b>${done}</b> z ${all} písní ve Steems</span>
      ${j.running ? `<span class="sb-run"><i style="width:${j.total ? Math.round(j.done / j.total * 100) : 0}%"></i>${j.done}/${j.total} · ${esc(j.now || "")}</span><button type="button" class="cp-x" id="sb-stop" title="Dodělá rozebíranou píseň a počká — i přes vypnutí Picku. Pokračovat naváže, kde skončil.">⏸ pozastavit</button>`
        : j.paused ? `<span class="sb-run"><i style="width:${j.total ? Math.round(j.done / j.total * 100) : 0}%"></i>pozastaveno · ${j.done}/${j.total}, zbývá ${j.left}</span>
          <button type="button" class="btn secondary" id="sb-resume">▶ Pokračovat</button><button type="button" class="cp-x" id="sb-forget" title="Zbytek pozastaveného rozboru zahodit (rozebrané písně zůstanou)">zrušit zbytek</button>`
        : j.todo ? `<button type="button" class="btn secondary" id="sb-go" title="Vezme po jedné písni z každé kapely, dokud jich nebude sto: najde sekce, poslechne stopy a změří stavbu textu">Rozebrat dalších ${Math.min(100, j.todo)} (různé kapely)</button>
          ${j.todo > 100 ? `<button type="button" class="cp-x" id="sb-all" title="Všechny ještě nerozebrané písně, po kapelách na přeskáčku — asi půl minuty na píseň">celou knihovnu (${j.todo}, ~${Math.max(1, Math.round(j.todo * 35 / 3600))} h)</button>` : ""}`
        : `<span class="cp-note">Všechno je rozebrané.</span>`}</div>
      ${j.errors && j.errors.length && !j.running ? `<p class="cp-note bad">Nepovedlo se: ${j.errors.map(esc).join(" · ")}</p>` : ""}
      <div class="cp-seg"><button type="button" data-sbtab="songs" class="${sbTab === "songs" ? "on" : ""}">Písně ze Steems</button><button type="button" data-sbtab="mine" class="${sbTab === "mine" ? "on" : ""}">Moje texty (${sb.mine.length})</button></div>`;
    let body = "";
    if(sbTab === "songs"){
      body = `<select id="sb-band"><option value="">Vyber kapelu…</option>${sb.bands.map((b) => `<option value="${esc(b.band)}" ${b.band === sbBand ? "selected" : ""}>${esc(b.band)} — ${b.analysed}/${b.songs.length}</option>`).join("")}</select>`;
      if(band){
        body += `${band.genres.length ? `<p class="cp-note">${esc(band.genres.join(", "))}</p>` : ""}
          <div class="sb-songs">${band.songs.map((x) => `<button type="button" data-sbsong="${esc(x.name)}" class="${sbSong && sbSong.name === x.name ? "on" : ""} ${x.analysed ? "" : "no"}">
            <b>${esc(x.title)}</b><small>${x.analysed ? `${x.bpm ? x.bpm + " BPM" : ""}${x.key ? " · " + esc(x.key) : ""} · ${x.sections} částí${x.text ? "" : " · bez textu"}` : "nerozebraná"}</small></button>`).join("")}</div>
          ${band.songs.some((x) => !x.analysed) && !j.running ? `<button type="button" class="cp-x" id="sb-band-go">rozebrat celou kapelu (${band.songs.filter((x) => !x.analysed).length})</button>` : ""}`;
      }
      const g = sbSong;
      if(g && g.sound){
        const s = g.sound;
        body += `<div class="sb-song"><div class="sb-cap"><b>${esc(g.artist)} – ${esc(g.title)}</b><small>${[s.bpm && s.bpm + " BPM", s.key, s.timesig && s.timesig + "/4", g.text ? `text: ${esc(g.text.lang)}` : "text nenalezen"].filter(Boolean).join(" · ")}</small>
            <p>${esc(s.caption)}</p>
            <div class="cp-row"><button type="button" class="btn secondary" id="sb-sound">Popis celé písně → Zvuk</button><button type="button" class="btn secondary" id="sb-all">Celou stavbu → Stavba</button></div></div>
          ${s.sections.map((r, i) => `<div class="sb-sec"><span class="sb-tag">[${esc(r.tag)}]</span><span class="sb-t">${clock(r.start)}–${clock(r.end)}</span>
            <span class="sb-d">${esc(r.desc)}<small>${esc(shapeTx(r.text))}</small></span><button type="button" class="sb-add" data-sbsec="${i}" title="Přidat tuhle část do stavby nové písně">＋</button></div>`).join("")}</div>`;
      } else if(g){
        body += `<p class="cp-note">Píseň ještě není rozebraná. <button type="button" class="cp-x" id="sb-one" ${j.running ? "disabled" : ""}>rozebrat ji</button></p>`;
      }
    } else {
      const groups = {};
      sb.mine.forEach((m) => (groups[m.band || "Bez kapely"] = groups[m.band || "Bez kapely"] || []).push(m));
      body = `<div class="cp-row"><button type="button" class="btn secondary" id="sb-save">Uložit text z pole Text jako můj</button></div>
        ${sb.mine.length ? Object.entries(groups).map(([b, ms]) => `<div class="sb-band">${esc(b)}</div>` + ms.map((m) => `<div class="sb-mine"><div class="sb-mine-h"><b>${esc(m.title)}</b><small>${esc(m.lang || "")} · ${m.sections.length} částí</small>
            <button type="button" class="cp-x" data-sbmineall="${m.id}">celý → Stavba</button><button type="button" class="cp-x" data-sbminedel="${m.id}">smazat</button></div>
            ${m.sections.map((x, i) => `<div class="sb-sec"><span class="sb-tag">[${esc(x.tag)}]</span><span class="sb-d">${esc(x.text.split("\n")[0] || "")}${x.text.includes("\n") ? "…" : ""}<small>${x.text ? x.text.split("\n").length + " ř." : "bez textu"}</small></span>
              <button type="button" class="sb-add" data-sbmine="${m.id}:${i}" title="Přidat do stavby">＋</button></div>`).join("")}</div>`).join("")).join("")
          : `<p class="cp-note">Zatím tu nic není. Napiš nebo nech napsat text v části Text a ulož ho sem — pak ho můžeš skládat po částech do dalších písní.</p>`}`;
    }
    const tags = planTags();
    const plan = `<div class="sb-plan"><div class="cp-sub">Stavba nové písně <small>— části v pořadí, v jakém zazní; mohou být z různých písní</small></div>
      ${form.plan.length ? form.plan.map((r, i) => `<div class="sb-prow"><span class="sb-tag">[${esc(tags[i])}${r.hint ? " - " + esc(r.hint) : ""}]</span>
          <span class="sb-d">${r.own ? "tvůj text · " + esc(r.own.split("\n")[0]) : esc(shapeTx(r.shape))}<small>${esc(r.from || "")}</small></span>
          <button type="button" data-pup="${i}" ${i ? "" : "disabled"}>↑</button><button type="button" data-pdown="${i}" ${i < form.plan.length - 1 ? "" : "disabled"}>↓</button><button type="button" data-prm="${i}">✕</button></div>`).join("")
        : `<p class="cp-note">Prázdná. Přidej ＋ části z písní nebo ze svých textů.</p>`}
      ${form.plan.length ? `<div class="cp-row"><button type="button" class="btn secondary" id="sb-psound" title="Spojí popisy písní, ze kterých stavba je">Popis zvuku ze stavby</button>
        <button type="button" class="btn" id="sb-pwrite" ${st.lyricist.length && !writing ? "" : "disabled"} title="Textař napíše nový vlastní text přesně do téhle stavby: počty řádků, slabik a rýmy; tvoje texty v ní nechá">Napsat text do stavby</button>
        <button type="button" class="btn secondary" id="sb-ptags" title="Do pole Text dá značky částí (s tvými texty) — zbytek dopíšeš sám">Jen značky do Textu</button>
        <button type="button" class="cp-x" id="sb-pclear">vyprázdnit</button></div>` : ""}</div>`;
    box.innerHTML = head + body + plan;
    const q = (id) => box.querySelector("#" + id), on = (id, fn) => { const n = q(id); if(n) n.onclick = fn; };
    on("sb-go", () => analyse([], 100));
    on("sb-stop", async () => { try{ await api("/api/songbook/analyse", {method: "DELETE"}); }catch(e){} showToast("Pozastavuji — rozebíraná píseň se ještě dodělá."); await sbLoad(); paintSb(); });
    on("sb-all", () => analyse([], 5000));
    on("sb-resume", async () => { try{ const r = await api("/api/songbook/analyse/resume", {method: "POST"}); showToast(`Pokračuji — zbývá ${r.started}.`); }catch(e){ showToast(e.message); }
      await sbLoad(); paintSb(); sbWatch(); });
    on("sb-forget", async () => { try{ await api("/api/songbook/analyse?forget=true", {method: "DELETE"}); }catch(e){} await sbLoad(); paintSb(); });
    on("sb-band-go", () => analyse(band.songs.filter((x) => !x.analysed).map((x) => x.name), 300));
    on("sb-one", () => analyse([sbSong.name], 1));
    box.querySelectorAll("[data-sbtab]").forEach((b) => b.onclick = () => { sbTab = b.dataset.sbtab; paintSb(); });
    const bs = q("sb-band"); if(bs) bs.onchange = () => { sbBand = bs.value; sbSong = null; paintSb(); };
    box.querySelectorAll("[data-sbsong]").forEach((b) => b.onclick = async () => { await openSong(b.dataset.sbsong, true); paintSb(); });
    if(sbSong && sbSong.sound){
      const src = {cap: sbSong.sound.caption, bpm: sbSong.sound.bpm, key: sbSong.sound.key, ts: sbSong.sound.timesig};
      on("sb-sound", () => soundFrom(src));
      on("sb-all", () => { sbSong.sound.sections.forEach((r) => form.plan.push(rowFromSection(sbSong, r))); keep(); paintSb(); showToast("Celá stavba písně je ve Stavbě."); });
      box.querySelectorAll("[data-sbsec]").forEach((b) => b.onclick = () => addRow(rowFromSection(sbSong, sbSong.sound.sections[+b.dataset.sbsec])));
    }
    on("sb-save", saveMine);
    const mineRow = (m, x) => ({tag: x.tag.replace(/\s+-\s+.*$/, ""), hint: "", desc: "", shape: null, own: x.text, from: `${m.band ? m.band + " – " : ""}${m.title}`, cap: ""});
    box.querySelectorAll("[data-sbmine]").forEach((b) => b.onclick = () => { const [id, i] = b.dataset.sbmine.split(":"), m = sb.mine.find((x) => x.id === id); if(m) addRow(mineRow(m, m.sections[+i])); });
    box.querySelectorAll("[data-sbmineall]").forEach((b) => b.onclick = () => { const m = sb.mine.find((x) => x.id === b.dataset.sbmineall); if(!m) return; m.sections.forEach((x) => form.plan.push(mineRow(m, x))); keep(); paintSb(); });
    box.querySelectorAll("[data-sbminedel]").forEach((b) => b.onclick = async () => { if(!await Dialog.confirm("Smazat tenhle text ze Zpěvníku?", {ok: "Smazat", danger: true})) return;
      try{ await api("/api/songbook/mine/" + b.dataset.sbminedel, {method: "DELETE"}); await sbLoad(); paintSb(); }catch(e){ showToast(e.message); } });
    box.querySelectorAll("[data-pup],[data-pdown]").forEach((b) => b.onclick = () => { const i = +(b.dataset.pup || b.dataset.pdown), k = b.dataset.pup ? i - 1 : i + 1;
      [form.plan[i], form.plan[k]] = [form.plan[k], form.plan[i]]; keep(); paintSb(); });
    box.querySelectorAll("[data-prm]").forEach((b) => b.onclick = () => { form.plan.splice(+b.dataset.prm, 1); keep(); paintSb(); });
    on("sb-psound", () => { const f = form.plan.find((r) => r.cap) || {}; soundFrom({cap: planCaption(), bpm: f.bpm, key: f.key, ts: f.ts}); });
    on("sb-pwrite", writePlan);
    on("sb-ptags", tagsOnly);
    on("sb-pclear", async () => { if(await Dialog.confirm("Vyprázdnit stavbu?", {ok: "Vyprázdnit"})){ form.plan = []; keep(); paintSb(); } });
  }

  async function make(){
    grab();
    if(!form.styles.length && !form.desc.trim()){ showToast("Vyber aspoň jeden styl, nebo popiš zvuk vlastními slovy."); return; }
    try{
      await api(`/api/studio/projects/${pid}/make`, json({styles: form.styles, desc: form.desc, per: form.per, length: form.length, lyrics: form.lyrics,
        lang: form.lang, mode: form.mode, opts: form.opts}));
      await load(); paint(); watch();
      showToast("Skládám. Průběh vidíš nahoře v liště, hotové verze se objeví vpravo.");
    }catch(e){ showToast(e.message); }
  }

  // ---- the results, each with its own player
  function paintResults(){
    if(!root) return;
    const right = root.querySelector("#cp-right"), eng = root.querySelector("#cp-eng"), p = project();
    if(eng) eng.textContent = !st ? "" : busy() ? `pracuje se · ve frontě ${st.progress.count}` : st.engine ? "model je připravený" : "";
    if(!right) return;
    const list = (p ? [p] : (st ? st.projects : [])).filter((x) => x.variants.length);
    if(!list.length){ right.innerHTML = `<div class="cp-empty"><b>Tady budou hotové verze.</b><p>Nahraj nápad, popiš zvuk, případně napiš text, a dej Složit. Každou verzi si tu přehraješ, nástroje můžeš vypínat, a tu nejlepší pošleš do Steems.</p></div>`; return; }
    const s = sig(list);
    if(right.dataset.sig === s){ tickRunning(right, list); return; }
    right.dataset.sig = s;
    Player.detachAll(right);
    right.innerHTML = list.map((x) => `${p ? "" : `<div class="cp-sub big">${esc(x.name)}</div>`}` + x.variants.slice().reverse().map((v) => card(x, v)).join("")).join("");
    right.querySelectorAll("[data-use]").forEach((b) => b.onclick = () => use(b.dataset.p, b.dataset.use, b.dataset.voice === "1"));
    right.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => { Player.stop(); setMode("steems"); loadMixerSong(b.dataset.open); });
    right.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { if(!await Dialog.confirm("Zahodit tuhle verzi?", {ok: "Zahodit", danger: true})) return;
      try{ await api(`/api/studio/projects/${b.dataset.p}/variants/${b.dataset.del}`, {method: "DELETE"}); await load(); paintResults(); }catch(e){ showToast(e.message); } });
    right.querySelectorAll("[data-sep]").forEach((b) => b.onclick = async () => { try{ await api(`/api/studio/projects/${b.dataset.p}/variants/${b.dataset.sep}/separate`, {method: "POST"}); await load(); paintResults(); watch(); }catch(e){ showToast(e.message); } });
    right.querySelectorAll("[data-seed]").forEach((b) => b.onclick = () => { form.opts.seed = +b.dataset.seed; keep(); secState.set = true; paint(); showToast(`Seed ${b.dataset.seed} je v nastavení — další skládání půjde stejnou cestou.`); });
    right.querySelectorAll(".vp").forEach((n) => Player.attach(n));
  }
  const sig = (list) => list.map((x) => x.id + ":" + x.variants.map((v) => v.id + v.status + (v.stems || "") + (v.used || "") + (v.sung || "") + (v.stem_list || []).length).join(",")).join("|") + (steemsSongs.length);
  function tickRunning(right, list){
    for(const x of list) for(const v of x.variants) if(v.pct != null){
      const bar = right.querySelector(`[data-bar="${v.id}"]`), tx = right.querySelector(`[data-tx="${v.id}"]`);
      if(bar) bar.style.width = Math.round(v.pct * 100) + "%";
      if(tx) tx.textContent = `${stageName(v.stage)} · ${Math.round(v.pct * 100)} % · zbývá asi ${mins(v.left)}`;
    }
  }
  function card(x, v){
    const stemName = stemOf(v), hasStems = v.used && steemsSongs.some((s) => s.name === stemName);
    const meta = [v.mode === "under" ? "kapela pod kytarou" : "", v.model_name + (v.think ? " + přemýšlení" : ""), lenName(v.length),
      known(v.metas, "bpm") ? v.metas.bpm + " BPM" : "", known(v.metas, "keyscale") ? v.metas.keyscale : "", v.took ? `složeno za ${mins(v.took)}` : ""].filter(Boolean).join(" · ");
    const running = (label) => `<div class="cp-st" data-tx="${v.id}">${label} · ${Math.round((v.pct || 0) * 100)} % · zbývá asi ${mins(v.left || 0)}</div><div class="cp-bar"><i data-bar="${v.id}" style="width:${Math.round((v.pct || 0) * 100)}%"></i></div>`;
    let body = "";
    if(v.status === "queued") body = `<div class="cp-st">čeká ve frontě</div>`;
    else if(v.status === "running") body = running(stageName(v.stage));
    else if(v.status === "error") body = `<div class="cp-st bad">nepovedlo se: ${esc(v.error || "")}</div>`;
    else {
      const stems = v.stem_list || [];
      let st2 = "";
      if(v.stems === "running") st2 = running("rozděluji na nástroje");
      else if(v.stems === "queued") st2 = `<div class="cp-st">na nástroje se rozdělí, až se dosloží ostatní</div>`;
      else if(v.stems === "error") st2 = `<div class="cp-st bad">rozdělení se nepovedlo: ${esc(v.stems_error || "")} <button type="button" class="cp-x" data-sep="${v.id}" data-p="${x.id}">zkusit znovu</button></div>`;
      else if(!stems.length) st2 = `<button type="button" class="cp-x" data-sep="${v.id}" data-p="${x.id}">rozdělit na nástroje</button>`;
      body = `<div class="vp" data-p="${x.id}" data-v="${v.id}" data-url="${v.url}" data-stems="${stems.join(",")}">
          <canvas class="vp-wave"></canvas>
          <div class="vp-bar"><button type="button" class="vp-play" title="Přehrát / zastavit (mezerník)">▶</button><span class="vp-time">0:00 / ${clock(v.length)}</span>
            <span class="vp-vol" title="Hlasitost">🔊<input type="range" class="vp-volr" min="0" max="1.5" step="0.01"></span></div>
          ${stems.length ? `<div class="vp-stems">${stems.map((s) => `<button type="button" class="on" data-stem="${s}" title="Klik zapne / vypne · dvojklik = jen tenhle"><i style="background:${STEM_COL[s]}"></i>${STEM_CZ[s]}</button>`).join("")}<button type="button" class="vp-all" title="Zapnout všechny">vše</button></div>` : ""}
        </div>${st2}
        <div class="cp-acts">
          ${v.used ? `<span class="cp-used">${hasStems ? "je ve Steems" : "v knihovně — ukládá se do Steems"}${v.sung ? ` · zpívá ${esc(v.sung)}` : ""}</span>
              ${hasStems ? `<button type="button" class="btn secondary" data-open="${esc(stemName)}">Otevřít ve Steems</button>` : ""}`
            : `<button type="button" class="btn" data-use="${v.id}" data-p="${x.id}" title="Uloží song do knihovny a do Steems po stopách">Tuhle chci — do Steems</button>`}
          ${x.lyrics && !v.sung ? `<button type="button" class="btn secondary" data-use="${v.id}" data-p="${x.id}" data-voice="1" title="Uloží song do Steems a zpěv přezpívá tvým hlasem">…a zazpívat mým hlasem</button>` : ""}
          ${v.seed ? `<button type="button" class="cp-x" data-seed="${esc(v.seed)}" title="Dát tenhle seed do nastavení">seed ${esc(v.seed)}</button>` : ""}
        </div>`;
    }
    const what = [v.caption, v.desc].filter(Boolean).join(", ");
    return `<div class="cp-card ${v.status}"><div class="cp-card-head"><b title="${esc(what)}">${esc(v.name)}</b><span>${esc(meta)}</span>
      ${v.status === "running" || v.stems === "running" ? "" : `<button type="button" class="cp-x" data-del="${v.id}" data-p="${x.id}" title="Zahodit tuhle verzi">×</button>`}</div>${body}</div>`;
  }
  const known = (m, k) => m && m[k] != null && !["", "N/A", "None"].includes(String(m[k]));
  async function use(p, vid, sing){
    let voice = "";
    if(sing){
      try{ voices = (await api("/api/myvoice")).voices; }catch(e){ voices = []; }
      if(!voices.length){ if(await Dialog.confirm("Ještě nemáš vlastní hlas. Vytvořit ho teď? (přečteš dva krátké texty a kousek zazpíváš)", {ok: "Vytvořit"})) VoiceGuide.open(() => setMode("composer")); return; }
      voice = voices.length === 1 ? voices[0].id : await pickVoice();
      if(!voice) return;
    }
    try{
      await api(`/api/studio/projects/${p}/variants/${vid}/use`, json({separate: true, voice}));
      showToast(sing ? "Ukládám do Steems a pak zazpívám tvým hlasem — dám vědět ve Steems." : "Song je v knihovně a ukládá se do Steems.");
      await load(); paintResults(); watch();
    }catch(e){ showToast(e.message); }
  }
  async function pickVoice(){
    return (await Dialog.pick({title: "Kterým hlasem zpívat?", items: voices.map((v) => ({label: v.name, sub: v.sung ? "se vzorkem zpěvu" : "", value: v.id}))})) || "";
  }

  // ---- the player: a waveform to click into, volume, and every instrument on its own switch.
  // One at a time; what it plays is decoded when it is first played and let go when another one starts.
  const Player = (() => {
    let ctx = null, cur = null;
    const vol = () => { const v = parseFloat(localStorage.getItem("pick-composer-vol")); return isFinite(v) ? v : 1; };
    const peaksCache = new Map();
    function attach(n){
      const P = {n, pid: n.dataset.p, vid: n.dataset.v, url: n.dataset.url, stems: n.dataset.stems ? n.dataset.stems.split(",") : [],
        on: {}, peaks: null, buffers: null, sources: [], gains: {}, master: null, t0: 0, off: 0, playing: false, len: 0};
      P.stems.forEach((s) => P.on[s] = true);
      if(cur && cur.vid === P.vid){ P.off = cur.playing ? now(cur) : cur.off; P.on = cur.on; P.buffers = cur.buffers; P.len = cur.len;
        if(cur.playing){ stopSources(cur); cur = P; start(P, P.off); } else cur = P; }
      n._vp = P;
      const r = n.querySelector(".vp-volr"); r.value = vol();
      r.oninput = () => { try{ localStorage.setItem("pick-composer-vol", r.value); }catch(e){} if(cur && cur.master) cur.master.gain.value = +r.value; };
      n.querySelector(".vp-play").onclick = () => toggle(P);
      const cv = n.querySelector(".vp-wave");
      cv.onclick = (e) => { const b = cv.getBoundingClientRect(), x = (e.clientX - b.left) / b.width; seek(P, x * (P.len || 0)); };
      n.querySelectorAll("[data-stem]").forEach((b) => {
        b.onclick = () => { P.on[b.dataset.stem] = !P.on[b.dataset.stem]; applyStems(P); };
        b.ondblclick = () => { P.stems.forEach((s) => P.on[s] = s === b.dataset.stem); applyStems(P); };
      });
      const all = n.querySelector(".vp-all"); if(all) all.onclick = () => { P.stems.forEach((s) => P.on[s] = true); applyStems(P); };
      paintStemBtns(P);
      loadPeaks(P);
    }
    function detachAll(){ /* the playing set lives on in `cur` and is picked up again by attach() */ }
    async function loadPeaks(P){
      const key = P.vid + ":" + P.stems.length;
      try{
        if(!peaksCache.has(key)) peaksCache.set(key, await (await fetch(`/api/studio/peaks/${P.pid}/${P.vid}`)).json());
        P.peaks = peaksCache.get(key); P.len = P.len || P.peaks.length || 0;
      }catch(e){ P.peaks = null; }
      draw(P);
    }
    const now = (P) => P.playing ? Math.min(P.len, P.off + (ctx.currentTime - P.t0)) : P.off;
    function draw(P){
      const cv = P.n.querySelector(".vp-wave"); if(!cv || !cv.isConnected) return;
      const w = cv.clientWidth || 600, h = cv.clientHeight || 72, dpr = window.devicePixelRatio || 1;
      if(cv.width !== Math.round(w * dpr)){ cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
      const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      const pk = P.peaks, at = P.len ? now(P) / P.len : 0, mid = h / 2;
      if(!pk || !pk.mix || !pk.mix.length){ g.fillStyle = "rgba(255,255,255,.08)"; g.fillRect(0, mid - 1, w, 2); return; }
      const cols = Math.max(60, Math.floor(w / 2)), n = pk.mix.length, stems = P.stems.filter((s) => pk.stems && pk.stems[s]);
      let top = 0;
      const sumAt = (i, all) => stems.reduce((a, s) => a + ((all || P.on[s]) ? pk.stems[s][i] : 0), 0);
      for(let c = 0; c < cols; c++){ const i = Math.floor(c / cols * n); top = Math.max(top, stems.length ? sumAt(i, true) : pk.mix[i]); }
      top = top || 1;
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#9d85ff";
      for(let c = 0; c < cols; c++){
        const i = Math.floor(c / cols * n), x = c / cols * w, bw = Math.max(1, w / cols - 0.6);
        g.globalAlpha = c / cols < at ? 1 : 0.5;
        if(stems.length){
          // the instruments stacked in their own colours, centred; a switched-off one leaves the outline
          let y = mid - sumAt(i, false) / top * (h - 4) / 2;
          for(const s of stems){ if(!P.on[s]) continue; const hh = pk.stems[s][i] / top * (h - 4); g.fillStyle = STEM_COL[s]; g.fillRect(x, y, bw, hh); y += hh; }
        } else {
          const hh = pk.mix[i] / top * (h - 4); g.fillStyle = accent; g.fillRect(x, mid - hh / 2, bw, hh);
        }
      }
      g.globalAlpha = 1; g.fillStyle = "#fff"; g.fillRect(Math.round(at * w), 0, 1.5, h);
      const t = P.n.querySelector(".vp-time"); if(t) t.textContent = `${clock(now(P))} / ${clock(P.len)}`;
    }
    function paintStemBtns(P){ P.n.querySelectorAll("[data-stem]").forEach((b) => b.classList.toggle("on", !!P.on[b.dataset.stem])); }
    function applyStems(P){ paintStemBtns(P); for(const s of P.stems) if(P.gains[s]) P.gains[s].gain.setTargetAtTime(P.on[s] ? 1 : 0, ctx.currentTime, 0.015); draw(P); }
    async function decode(url){ const b = await (await fetch(url)).arrayBuffer(); return await ctx.decodeAudioData(b); }
    async function ensure(P){
      if(P.buffers) return true;
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      const btn = P.n.querySelector(".vp-play"); btn.textContent = "…"; btn.disabled = true;
      try{
        if(cur && cur !== P && cur.buffers && cur.vid !== P.vid){ stopSources(cur); cur.buffers = null; cur.playing = false; paintBtn(cur); }
        if(P.stems.length){
          const got = await Promise.all(P.stems.map((s) => decode(`/api/studio/audio/${P.pid}/${P.vid}/${s}.mp3`)));
          P.buffers = Object.fromEntries(P.stems.map((s, i) => [s, got[i]]));
        } else P.buffers = {mix: await decode(P.url)};
        P.len = Math.max(...Object.values(P.buffers).map((b) => b.duration));
        return true;
      }catch(e){ showToast("Přehrát se nepodařilo: " + e.message); return false; }
      finally{ btn.disabled = false; paintBtn(P); }
    }
    function stopSources(P){ P.sources.forEach((s) => { try{ s.onended = null; s.stop(); }catch(e){} }); P.sources = []; }
    function start(P, at){
      stopSources(P);
      P.master = ctx.createGain(); P.master.gain.value = vol(); P.master.connect(ctx.destination);
      P.gains = {};
      for(const [k, buf] of Object.entries(P.buffers)){
        const src = ctx.createBufferSource(), g = ctx.createGain();
        src.buffer = buf; g.gain.value = k === "mix" || P.on[k] ? 1 : 0;
        src.connect(g).connect(P.master); P.gains[k] = g; P.sources.push(src);
      }
      P.t0 = ctx.currentTime + 0.03; P.off = Math.max(0, Math.min(at, P.len - 0.05));
      P.sources.forEach((s) => s.start(P.t0, P.off));
      P.sources[0].onended = () => { if(P.playing && now(P) >= P.len - 0.1){ P.playing = false; P.off = 0; paintBtn(P); draw(P); } };
      P.playing = true; paintBtn(P); loop();
    }
    function paintBtn(P){ const b = P.n && P.n.querySelector(".vp-play"); if(b){ b.textContent = P.playing ? "⏸" : "▶"; b.classList.toggle("on", P.playing); } }
    async function toggle(P){
      if(P.playing){ P.off = now(P); P.playing = false; stopSources(P); paintBtn(P); draw(P); return; }
      if(cur && cur !== P && cur.playing){ cur.off = now(cur); cur.playing = false; stopSources(cur); paintBtn(cur); draw(cur); }
      if(!(await ensure(P))) return;
      cur = P;
      if(ctx.state === "suspended") await ctx.resume();
      if(window.Player && typeof window.Player.pause === "function"){ try{ window.Player.pause(); }catch(e){} }
      start(P, P.off >= P.len - 0.1 ? 0 : P.off);
    }
    function seek(P, t){ if(P.playing && P.buffers) start(P, t); else { P.off = Math.max(0, Math.min(t, P.len || 0)); draw(P); } }
    let raf = 0;
    function loop(){ cancelAnimationFrame(raf); if(!cur || !cur.playing) return; draw(cur); raf = requestAnimationFrame(loop); }
    function stop(){ if(cur && cur.playing){ cur.off = now(cur); cur.playing = false; stopSources(cur); paintBtn(cur); } }
    document.addEventListener("keydown", (e) => {
      if(!shown || e.code !== "Space" || /INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || "") || !cur || !cur.n.isConnected) return;
      e.preventDefault(); e.stopPropagation(); toggle(cur);
    }, true);
    window.addEventListener("resize", () => { if(root) root.querySelectorAll(".vp").forEach((n) => n._vp && draw(n._vp)); });
    return {attach, detachAll, stop};
  })();

  // ---- shown as a section of the app (Tools → Skladatel)
  async function show(container){
    root = container; shown = true;
    if(!st) paint();
    await load();
    if(st && !pid){ const live = st.projects.find((p) => p.variants.some((v) => v.status === "queued" || v.status === "running" || v.stems === "running")) || st.projects[0];
      if(live) pid = live.id; const p = project(); if(p && !form.lyrics) form.lyrics = p.lyrics || ""; }
    paint(); watch();
  }
  function hide(){ shown = false; stopMic(); Player.stop(); }
  // the old way in (a window) leads to the page now
  const open = () => setMode("composer");
  const close = hide;
  // work that was going on when the page was loaded is watched again, and shown at the top
  load().then(() => { if(busy() || pending().length) watch(); });
  document.addEventListener("click", (e) => { if(e.target.closest && e.target.closest("#studio-pill")) setMode("composer"); });
  // the app came up straight into this section (a link, the last place) before this script was there
  setTimeout(() => { if(typeof state !== "undefined" && state.mode === "composer" && !shown) show(document.getElementById("composer-section")); }, 0);
  return {show, hide, open, close, state: () => st};
})();
