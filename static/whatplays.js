// Co tam hraje? — what the record's guitar plays in a few seconds, note by note. The stretch is the
// loop in Steems (or 5 s from the cursor); the server hears one guitar stem there with basic-pitch
// (/api/whatplays/{song}) and the page lays the notes out on the song's tuning: a tab where the hand
// moves as little as it can (chords on different strings, within four frets, open strings free),
// and a fretboard showing each note as it sounds. The stretch plays here at 50–100 % without the
// pitch changing — the guitar alone or the whole song — the note playing lit in the tab and on the neck.
const WhatPlays = (() => {
  const enc = encodeURIComponent;
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const nn = (m) => NAMES[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
  const STD = [64, 59, 55, 50, 45, 40];
  const MAXF = 22, SPAN = 4;
  let win = null, st = null;

  // ---------------------------------------------------------------- where on the neck
  // notes starting together are one chord; a hand position (the lowest fret of a four-fret box) is
  // chosen for each so the whole stretch moves as little as it can.
  function groups(notes){
    const out = [];
    for(const n of notes.slice().sort((a, b) => a.t - b.t || b.m - a.m)){
      const g = out[out.length - 1];
      if(g && n.t - g.t < 0.035){ if(!g.notes.some(x => x.m === n.m)) g.notes.push(n); }
      else out.push({t: n.t, notes: [n]});
    }
    return out;
  }
  function fit(g, base, tun){
    const ns = g.notes.slice().sort((a, b) => b.m - a.m).slice(0, tun.length);
    let best = null;
    const used = new Array(tun.length).fill(false), pick = [];
    const go = (k, cost) => {
      if(best && cost >= best.cost) return;
      if(k === ns.length){
        // a chord on strings next to each other, not with one skipped in the middle
        const ss = pick.map(p => p.s), gap = pick.length > 1 ? Math.max(...ss) - Math.min(...ss) + 1 - pick.length : 0;
        const c = cost + gap * 0.9;
        if(!best || c < best.cost) best = {cost: c, pos: pick.slice()};
        return;
      }
      const m = ns[k].m;
      for(let s = 0; s < tun.length; s++){
        if(used[s]) continue;
        const f = m - tun[s];
        if(f < 0 || f > MAXF || (f !== 0 && (f < base || f > base + SPAN))) continue;
        used[s] = true; pick.push({s, f, n: ns[k]});
        go(k + 1, cost + (f === 0 ? 0.2 : Math.abs(f - (base + 1.5)) * 0.3));
        used[s] = false; pick.pop();
      }
    };
    go(0, 0);
    return best;
  }
  // basic-pitch also hears a note's overtones as notes of their own: an octave (or an octave and a
  // fifth) above a note that starts with it, much quieter and gone much sooner. A played octave —
  // the top of a power chord — rings as long as its root and stays.
  function clean(notes){
    return notes.filter(n => !notes.some(r => r !== n && r.m < n.m && [12, 19, 24, 28, 31].includes(n.m - r.m) && Math.abs(r.t - n.t) < 0.06 &&
      n.v < r.v * 0.75 && n.d < r.d * 0.6));
  }
  function layout(notes, tun){
    const gs = groups(notes);
    if(!gs.length) return [];
    const B = MAXF - SPAN + 1;
    const cost = gs.map(g => { const row = []; for(let b = 0; b <= B; b++){ const f = fit(g, b, tun); row.push(f ? {c: f.cost + b * 0.12 + Math.max(0, b - 12) * 0.15, pos: f.pos} : null); } return row; });
    const dp = cost.map(() => new Array(B + 1).fill(Infinity)), back = cost.map(() => new Array(B + 1).fill(-1));
    for(let b = 0; b <= B; b++) if(cost[0][b]) dp[0][b] = cost[0][b].c;
    for(let i = 1; i < gs.length; i++){
      const gap = gs[i].t - gs[i - 1].t, move = gap > 1.2 ? 0.25 : 0.8;
      for(let b = 0; b <= B; b++){
        if(!cost[i][b]) continue;
        let m = Infinity, arg = -1;
        for(let p = 0; p <= B; p++){ const v = dp[i - 1][p] + Math.abs(b - p) * move; if(v < m){ m = v; arg = p; } }
        if(arg < 0){ m = 0; }                     // the one before fitted nowhere: start afresh
        dp[i][b] = m + cost[i][b].c; back[i][b] = arg;
      }
    }
    // a chord no box takes (too wide): each note where it is lowest
    const loose = (g) => g.notes.map(n => { let s = -1, f = 99; tun.forEach((o, k) => { const x = n.m - o; if(x >= 0 && x <= MAXF && x < f){ f = x; s = k; } }); return s < 0 ? null : {s, f, n}; }).filter(Boolean);
    const out = new Array(gs.length);
    const bestOf = (i) => { const row = dp[i]; let k = -1; row.forEach((v, j) => { if(isFinite(v) && (k < 0 || v < row[k])) k = j; }); return k; };
    let b = bestOf(gs.length - 1);
    for(let i = gs.length - 1; i >= 0; i--){
      const c = b >= 0 ? cost[i][b] : null;
      out[i] = {t: gs[i].t, d: Math.max(...gs[i].notes.map(n => n.d)), base: c ? b : null, pos: c ? c.pos : loose(gs[i])};
      if(i > 0) b = c && back[i][b] >= 0 ? back[i][b] : bestOf(i - 1);
    }
    return out;
  }

  // ---------------------------------------------------------------- the window
  function css(){
    if(document.getElementById("wp-css")) return;
    const s = document.createElement("style"); s.id = "wp-css";
    s.textContent = `
      #wp-win{position:fixed;left:50%;top:84px;transform:translateX(-50%);width:min(980px,calc(100vw - 24px));max-height:calc(100vh - 110px);z-index:4000;
        display:flex;flex-direction:column;background:linear-gradient(var(--card,#191a2a),var(--card,#191a2a)),#14151d;backdrop-filter:blur(14px);color:var(--text,#eef0ff);border:1px solid var(--border,#262840);border-radius:14px;
        box-shadow:0 24px 70px rgba(0,0,0,.6);overflow:hidden}
      #wp-win header{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border,#262840);cursor:grab;user-select:none}
      #wp-win header b{font-size:15px}#wp-win header small{color:var(--sub,#8f96c0);font-size:12px}
      #wp-win header .wp-x{margin-left:auto;background:none;border:none;color:var(--sub,#8f96c0);font-size:20px;cursor:pointer}
      .wp-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:9px 14px;border-bottom:1px solid var(--border,#262840)}
      .wp-bar select,.wp-bar button{font:inherit;font-size:12.5px;background:var(--bg2,#12131f);color:inherit;border:1px solid var(--border,#262840);border-radius:8px;padding:5px 9px;cursor:pointer}
      .wp-bar button.on,.wp-bar .wp-play{background:var(--accent,#8b7bff);border-color:var(--accent,#8b7bff);color:#0c0c16;font-weight:700}
      .wp-bar .wp-sp{display:flex;gap:2px}.wp-bar .wp-sp button{padding:5px 8px}
      .wp-body{overflow:auto;padding:12px 14px 16px}
      .wp-msg{color:var(--sub,#8f96c0);padding:24px;text-align:center}
      .wp-tab{position:relative;overflow-x:auto;overflow-y:hidden;border:1px solid var(--border,#262840);border-radius:10px;background:var(--bg2,#12131f);padding:10px 0 6px}
      .wp-tabin{position:relative}
      .wp-str{position:absolute;left:0;right:0;height:1px;background:rgba(255,255,255,.18)}
      .wp-sn{position:absolute;left:6px;font:600 11px ui-monospace,Menlo,monospace;color:var(--sub,#8f96c0);transform:translateY(-50%)}
      .wp-f{position:absolute;transform:translate(-50%,-50%);min-width:17px;height:17px;padding:0 3px;border-radius:5px;background:var(--bg2,#12131f);
        font:700 12px ui-monospace,Menlo,monospace;text-align:center;line-height:17px;cursor:pointer;color:var(--text,#eef0ff)}
      .wp-f:hover{outline:1px solid var(--accent,#8b7bff)}
      .wp-f.now{background:var(--accent,#8b7bff);color:#0c0c16}
      .wp-head{position:absolute;top:0;bottom:0;width:2px;background:#ff5f6d;pointer-events:none;display:none}
      .wp-neck{margin-top:12px;overflow-x:auto}
      .wp-neck svg{display:block}
      .wp-names{margin-top:10px;font:12px ui-monospace,Menlo,monospace;color:var(--sub,#8f96c0);line-height:1.6;word-break:break-word}
      .wp-names b{color:var(--text,#eef0ff)}
      .wp-hint{color:var(--sub,#8f96c0);font-size:11.5px;margin-top:8px}`;
    document.head.appendChild(s);
  }
  function close(){
    stopAudio();
    if(win){ win.remove(); win = null; }
    st = null;
  }
  function drag(hd){
    hd.addEventListener("pointerdown", (e) => {
      if(e.target.closest("button,select")) return;
      const r = win.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      win.style.transform = "none"; win.style.left = r.left + "px"; win.style.top = r.top + "px";
      hd.setPointerCapture(e.pointerId);
      const mv = (ev) => { win.style.left = Math.max(0, Math.min(innerWidth - 120, ev.clientX - dx)) + "px"; win.style.top = Math.max(0, Math.min(innerHeight - 50, ev.clientY - dy)) + "px"; };
      const up = () => { hd.removeEventListener("pointermove", mv); hd.removeEventListener("pointerup", up); };
      hd.addEventListener("pointermove", mv); hd.addEventListener("pointerup", up);
    });
  }
  const mmss = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  const guitarStems = () => (Mixer.channelInfo ? Mixer.channelInfo() : []).map(c => c.stem).filter(s => /^guitar/.test(s));
  const label = (s) => ({guitar: "Kytara", guitar_rhythm: "Doprovodná kytara", guitar_solo: "Sólová kytara"})[s] || s;
  function loudest(stems, a, b){
    let best = stems[0], top = -1;
    for(const s of stems){
      const c = Mixer.channelByStem(s); if(!c || !c.buffer) continue;
      const x = c.buffer.getChannelData(0), sr = c.buffer.sampleRate;
      let e = 0; for(let i = Math.floor(a * sr); i < Math.min(x.length, b * sr); i += 16) e += x[i] * x[i];
      if(e > top){ top = e; best = s; }
    }
    return best;
  }

  async function open(){
    if(typeof Mixer === "undefined" || !Mixer.currentSong || !Mixer.currentSong()){ say("Nejdřív otevři píseň ve Steems"); return; }
    const stems = guitarStems();
    if(!stems.length){ say("Píseň nemá kytarovou stopu"); return; }
    const song = Mixer.currentSong(), loop = Mixer.loopNow(), dur = Mixer.duration();
    let a, b;
    if(loop){ a = loop.start; b = loop.end; } else { a = Math.max(0, Mixer.currentTime()); b = Math.min(dur, a + 5); }
    if(b - a > 30){ b = a + 30; say("Smyčka je delší než 30 s — beru prvních 30 s"); }
    if(b - a < 0.5){ say("Úsek je moc krátký — dej smyčku nebo posuň kurzor"); return; }
    if(Mixer.isPlaying()) Mixer.pause();
    close(); css();
    st = {song, a, b, stem: loudest(stems, a, b), stems, speed: 0.75, whole: false, notes: null, lay: null, tun: STD, audio: null, url: null, raf: 0, loop: true};
    win = document.createElement("div"); win.id = "wp-win";
    win.innerHTML = `<header><b>Co tam hraje?</b><small class="wp-sub"></small><button type="button" class="wp-x" title="Zavřít">×</button></header>
      <div class="wp-bar">
        <select class="wp-stem" title="Která kytara">${stems.map(s => `<option value="${esc(s)}">${esc(label(s))}</option>`).join("")}</select>
        <button type="button" class="wp-play" title="Přehrát úsek (mezerník)">▶ Přehrát</button>
        <span class="wp-sp" title="Rychlost — výška tónů zůstane">${[0.5, 0.75, 1].map(v => `<button type="button" data-v="${v}">${v * 100} %</button>`).join("")}</span>
        <button type="button" class="wp-whole" title="Hrát jen kytaru, nebo celou píseň">Jen kytara</button>
        <button type="button" class="wp-loop on" title="Přehrávat dokola">⟳ Dokola</button>
        <button type="button" class="wp-copy" title="Zkopírovat tab jako text">Kopírovat tab</button>
      </div>
      <div class="wp-body"><p class="wp-msg">Poslouchám kytaru a hledám tóny…</p></div>`;
    document.body.appendChild(win);
    drag(win.querySelector("header"));
    win.querySelector(".wp-x").onclick = close;
    win.querySelector(".wp-stem").value = st.stem;
    win.querySelector(".wp-stem").onchange = (e) => { st.stem = e.target.value; stopAudio(); hear(); };
    win.querySelector(".wp-play").onclick = () => (st.audio && !st.audio.paused ? stopAudio() : play());
    win.querySelectorAll(".wp-sp button").forEach(x => x.onclick = () => { st.speed = +x.dataset.v; bar(); if(st.audio) st.audio.playbackRate = st.speed; });
    win.querySelector(".wp-whole").onclick = () => { st.whole = !st.whole; const was = st.audio && !st.audio.paused; stopAudio(); bar(); if(was) play(); };
    win.querySelector(".wp-loop").onclick = () => { st.loop = !st.loop; if(st.audio) st.audio.loop = st.loop; bar(); };
    win.querySelector(".wp-copy").onclick = copyTab;
    win.addEventListener("keydown", (e) => { if(e.key === " "){ e.preventDefault(); e.stopPropagation(); win.querySelector(".wp-play").click(); } });
    win.tabIndex = -1; win.focus();
    bar();
    await hear();
  }
  function bar(){
    if(!win) return;
    win.querySelector(".wp-sub").textContent = `${mmss(st.a)}–${mmss(st.b)} · ${label(st.stem)}${st.tunName ? " · " + st.tunName : ""}`;
    win.querySelectorAll(".wp-sp button").forEach(x => x.classList.toggle("on", +x.dataset.v === st.speed));
    const w = win.querySelector(".wp-whole"); w.textContent = st.whole ? "Celá píseň" : "Jen kytara"; w.classList.toggle("on", st.whole);
    win.querySelector(".wp-loop").classList.toggle("on", st.loop);
    win.querySelector(".wp-play").textContent = st.audio && !st.audio.paused ? "■ Stop" : "▶ Přehrát";
  }
  async function hear(){
    const body = win.querySelector(".wp-body"), my = st;
    body.innerHTML = `<p class="wp-msg"><span class="spinner"></span> Poslouchám kytaru a hledám tóny…</p>`;
    try{
      const r = await fetch(`/api/whatplays/${enc(my.song)}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({stem: my.stem, start: my.a, end: my.b})});
      const d = await r.json();
      if(!r.ok) throw new Error(d.error || "Nepodařilo se");
      if(st !== my) return;
      let tun = Array.isArray(d.tuning) && d.tuning.length >= 4 ? d.tuning.slice() : STD.slice();
      if(tun[0] < tun[tun.length - 1]) tun.reverse();                     // high string first
      my.tun = tun;
      const dt = typeof describeTuning === "function" ? describeTuning(tun) : null;
      my.tunName = dt && dt.name || "";
      my.notes = clean(d.notes).filter(n => n.m >= tun[tun.length - 1] && n.m <= tun[0] + MAXF);
      my.lay = layout(my.notes, tun);
      bar(); paint();
    }catch(e){ if(st === my) body.innerHTML = `<p class="wp-msg">${esc(e.message)}</p>`; }
  }

  // ---------------------------------------------------------------- tab and neck
  const PPS = 110, ROW = 20, LEFT = 34;
  function paint(){
    const body = win.querySelector(".wp-body"), L = st.lay, tun = st.tun;
    if(!L.length){ body.innerHTML = `<p class="wp-msg">V tomhle úseku jsem v kytaře žádné tóny neslyšel. Zkus jinou kytaru nahoře nebo jiný úsek.</p>`; return; }
    const w = LEFT + (st.b - st.a) * PPS + 30, h = tun.length * ROW + 10;
    let html = `<div class="wp-tab"><div class="wp-tabin" style="width:${w}px;height:${h}px">`;
    tun.forEach((m, s) => { const y = 10 + s * ROW; html += `<div class="wp-str" style="top:${y}px"></div><span class="wp-sn" style="top:${y}px">${NAMES[m % 12]}</span>`; });
    L.forEach((g, i) => g.pos.forEach(p => {
      html += `<span class="wp-f" data-g="${i}" style="left:${LEFT + (g.t - st.a) * PPS}px;top:${10 + p.s * ROW}px" title="${nn(p.n.m)} · ${p.s + 1}. struna, ${p.f}. pražec · ${mmss(g.t)}">${p.f}</span>`;
    }));
    html += `<div class="wp-head"></div></div></div><div class="wp-neck">${neck(-1)}</div>`;
    const names = L.map(g => g.pos.length > 1 ? `<b>[${g.pos.map(p => nn(p.n.m)).join(" ")}]</b>` : nn(g.pos[0] ? g.pos[0].n.m : 0)).join(" · ");
    html += `<div class="wp-names">${names}</div>
      <p class="wp-hint">Tóny rozpoznává basic-pitch z oddělené kytary — u zkreslené kytary a rychlých pasáží se může splést; ber to jako vodítko a ověř uchem (zpomaleně). Klik na číslo = přehraje od toho tónu.</p>`;
    body.innerHTML = html;
    body.querySelectorAll(".wp-f").forEach(f => f.onclick = () => { const g = st.lay[+f.dataset.g]; light(+f.dataset.g); play(g.t - st.a); });
  }
  function neck(gi){
    const tun = st.tun, N = 17, W = 900, H = tun.length * 18 + 26, x0 = 28, fw = (W - x0 - 10) / N;
    const X = (f) => f === 0 ? x0 - 12 : x0 + (f - 0.5) * fw;
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`;
    svg += `<rect x="${x0}" y="8" width="${W - x0 - 10}" height="${(tun.length - 1) * 18}" fill="rgba(255,255,255,.03)"/>`;
    for(let f = 0; f <= N; f++) svg += `<line x1="${x0 + f * fw}" y1="8" x2="${x0 + f * fw}" y2="${8 + (tun.length - 1) * 18}" stroke="${f === 0 ? "#bbb" : "rgba(255,255,255,.18)"}" stroke-width="${f === 0 ? 3 : 1}"/>`;
    [3, 5, 7, 9, 15, 17].forEach(f => { if(f <= N) svg += `<circle cx="${X(f)}" cy="${8 + (tun.length - 1) * 9}" r="4" fill="rgba(255,255,255,.12)"/>`; });
    if(N >= 12) [-1, 1].forEach(k => { svg += `<circle cx="${X(12)}" cy="${8 + (tun.length - 1) * 9 + k * 18}" r="4" fill="rgba(255,255,255,.12)"/>`; });
    tun.forEach((m, s) => svg += `<line x1="${x0}" y1="${8 + s * 18}" x2="${W - 10}" y2="${8 + s * 18}" stroke="rgba(255,255,255,${0.25 + s * 0.06})" stroke-width="${1 + s * 0.3}"/><text x="4" y="${12 + s * 18}" fill="#8f96c0" font-size="11" font-family="ui-monospace,Menlo,monospace">${NAMES[m % 12]}</text>`);
    for(let f = 1; f <= N; f += f < 3 ? 2 : 2) svg += `<text x="${X(f)}" y="${H - 4}" text-anchor="middle" fill="#6d7290" font-size="10" font-family="ui-monospace,Menlo,monospace">${f}</text>`;
    // every note of the stretch faint, the one sounding bright
    const seen = new Set();
    st.lay.forEach((g, i) => g.pos.forEach(p => {
      const k = p.s + ":" + p.f, now = i === gi;
      if(!now && seen.has(k)) return; seen.add(k);
      if(p.f > N) return;
      svg += `<circle cx="${X(p.f)}" cy="${8 + p.s * 18}" r="${now ? 8.5 : 6}" fill="${now ? "var(--accent,#8b7bff)" : "rgba(139,123,255,.28)"}"/>`;
      if(now) svg += `<text x="${X(p.f)}" y="${12 + p.s * 18}" text-anchor="middle" fill="#0c0c16" font-size="10" font-weight="700" font-family="ui-monospace,Menlo,monospace">${p.f}</text>`;
    }));
    return svg + `</svg>`;
  }
  let lit = -2;
  function light(gi){
    if(gi === lit || !win) return; lit = gi;
    win.querySelectorAll(".wp-f.now").forEach(x => x.classList.remove("now"));
    if(gi >= 0) win.querySelectorAll(`.wp-f[data-g="${gi}"]`).forEach(x => x.classList.add("now"));
    const n = win.querySelector(".wp-neck"); if(n) n.innerHTML = neck(gi);
  }

  // ---------------------------------------------------------------- sound: the stretch as a file, so the speed keeps the pitch
  function wav(x, sr){
    const b = new ArrayBuffer(44 + x.length * 2), v = new DataView(b);
    const w = (o, s) => { for(let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + x.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true);
    v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, "data"); v.setUint32(40, x.length * 2, true);
    for(let i = 0; i < x.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x[i])) * 32767, true);
    return new Blob([b], {type: "audio/wav"});
  }
  function cut(){
    const stems = st.whole ? (Mixer.channelInfo() || []).map(c => c.stem).filter(s => !/^take-/.test(s)) : [st.stem];
    const bufs = stems.map(s => Mixer.channelByStem(s)).filter(c => c && c.buffer).map(c => c.buffer);
    if(!bufs.length) return null;
    const sr = bufs[0].sampleRate, i0 = Math.floor(st.a * sr), n = Math.floor((st.b - st.a) * sr), x = new Float32Array(n);
    for(const buf of bufs) for(let ch = 0; ch < buf.numberOfChannels; ch++){ const d = buf.getChannelData(ch); for(let i = 0; i < n && i0 + i < d.length; i++) x[i] += d[i0 + i] / buf.numberOfChannels; }
    let peak = 0; for(let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(x[i]));
    if(peak > 0.98) for(let i = 0; i < n; i++) x[i] *= 0.98 / peak;
    return wav(x, sr);
  }
  function play(from){
    stopAudio();
    if(Mixer.isPlaying()) Mixer.pause();
    const blob = cut(); if(!blob){ say("Zvuk stopy není načtený"); return; }
    st.url = URL.createObjectURL(blob);
    const au = new Audio(st.url);
    au.preservesPitch = true; au.playbackRate = st.speed; au.loop = st.loop;
    if(from) au.currentTime = Math.max(0, from);
    st.audio = au;
    au.onended = () => { stopAudio(); };
    au.play().catch(() => {});
    const frame = () => {
      if(!st || st.audio !== au || au.paused) return;
      const t = st.a + au.currentTime;
      let gi = -1;
      for(let i = 0; i < st.lay.length; i++){ const g = st.lay[i]; if(g.t <= t && t < g.t + Math.max(0.12, g.d)) gi = i; if(g.t > t) break; }
      light(gi);
      const head = win && win.querySelector(".wp-head");
      if(head){ head.style.display = "block"; head.style.left = (LEFT + au.currentTime * PPS) + "px";
        const tab = win.querySelector(".wp-tab"), x = LEFT + au.currentTime * PPS;
        if(x < tab.scrollLeft + 40 || x > tab.scrollLeft + tab.clientWidth - 60) tab.scrollLeft = x - 80; }
      st.raf = requestAnimationFrame(frame);
    };
    st.raf = requestAnimationFrame(frame);
    bar();
  }
  function stopAudio(){
    if(!st) return;
    if(st.audio){ try{ st.audio.pause(); }catch(e){} st.audio = null; }
    if(st.url){ URL.revokeObjectURL(st.url); st.url = null; }
    cancelAnimationFrame(st.raf);
    const head = win && win.querySelector(".wp-head"); if(head) head.style.display = "none";
    light(-1); bar();
  }

  // ---------------------------------------------------------------- as text
  function asText(){
    const tun = st.tun, rows = tun.map(m => (NAMES[m % 12] + " ").slice(0, 2) + "|");
    for(const g of st.lay){
      const w = Math.max(1, ...g.pos.map(p => String(p.f).length));
      tun.forEach((m, s) => { const p = g.pos.find(x => x.s === s); rows[s] += (p ? String(p.f) : "").padEnd(w, "-") + "-"; });
    }
    return rows.map(r => r + "|").join("\n");
  }
  async function copyTab(){
    if(!st || !st.lay || !st.lay.length) return;
    try{ await navigator.clipboard.writeText(asText()); say("Tab zkopírovaný — vlož ho, kam chceš"); }catch(e){ say("Kopírování se nepovedlo"); }
  }

  return {open, close, _layout: layout, _text: () => st && st.lay ? asText() : ""};
})();
