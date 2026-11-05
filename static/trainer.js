// Trenér rychlosti: a hard riff (the loop, or the section at the cursor) from a slow tempo up to the
// full one. In Steems, ⏩ in the lane "Cvičení" (or a pedal switch). Each pass of the loop is judged:
// with the guitar plugged in, the pass is recorded and its notes set against the record's guitar
// (static/timing.js does the measuring) — enough of them on time is a clean pass, and a clean pass
// raises the tempo by a step. Three passes in a row that are not clean take a step back. Without the
// guitar it only counts the passes and raises the tempo every few of them. Where it got is written
// into the practice journal (static/journal.js).
const Trainer = (() => {
  const KEY = "pick-trainer";
  const say = (m) => { try{ showToast(m); }catch(e){} };
  const esc = (x) => (typeof escapeHtml === "function" ? escapeHtml(String(x == null ? "" : x)) : String(x == null ? "" : x));
  const cfg = {from: 70, step: 5, to: 100, judge: "guitar", need: 70, every: 2, back: true};
  try{ Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  let run = null, win = null;

  const pct = (v) => Math.round(v * 100);
  const mmss = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const ac = () => Mixer.voiceOut().ac;
  const guitars = () => (Mixer.channelInfo ? Mixer.channelInfo() : []).map(c => c.stem).filter(s => /^guitar/.test(s));
  // what the stretch is called: the section it is, the one it lies in, or its times
  function nameOf(a, b){
    const secs = Mixer.sectionsNow ? Mixer.sectionsNow() : [];
    const same = secs.find(x => Math.abs(x.start - a) < 0.3 && Math.abs(x.end - b) < 0.3);
    if(same) return same.name;
    const mid = (a + b) / 2, inn = secs.find(x => x.start <= mid && x.end > mid);
    return (inn ? inn.name + " " : "") + `${mmss(a)}–${mmss(b)}`;
  }
  function stretch(){
    const loop = Mixer.loopNow();
    if(loop) return {a: loop.start, b: loop.end, set: false};
    const t = Mixer.currentTime(), sec = (Mixer.sectionsNow ? Mixer.sectionsNow() : []).find(x => x.start <= t && x.end > t);
    return sec ? {a: sec.start, b: sec.end, set: true} : null;
  }

  // ---------------------------------------------------------------- the run
  async function start(){
    if(run) return;
    const song = Mixer.currentSong && Mixer.currentSong();
    if(!song){ say("Nejdřív otevři píseň ve Steems"); return; }
    const st = stretch();
    if(!st){ say("Dej smyčku na těžké místo (nebo postav kurzor do sekce) a pusť trenéra znovu"); return; }
    if(st.b - st.a < 2){ say("Smyčka je moc krátká — aspoň 2 vteřiny"); return; }
    if(typeof Timing !== "undefined" && Timing.recording()){ say("Teď běží měření časování — nejdřív ho zastav"); return; }
    let judge = cfg.judge;
    if(judge === "guitar" && !(Mixer.guitar && Mixer.guitar.isLive())){ judge = "count"; say("Kytara není zapnutá — trenér jen počítá průchody. Pro posuzování zapni vstup kytary."); }
    if(judge === "guitar" && !guitars().length){ judge = "count"; say("Píseň nemá kytarovou stopu — trenér jen počítá průchody"); }
    const from = Math.max(50, Math.min(cfg.to, cfg.from)) / 100;
    run = {song, a: st.a, b: st.b, name: nameOf(st.a, st.b), judge, from, to: Math.min(150, cfg.to) / 100, speed: from,
           passes: [], fails: 0, deaf: 0, best: 0, rec: null, changeAt: 0, passAt: 0, wait: false};
    if(st.set) Mixer.setLoop(st.a, st.b);
    if(Mixer.isPlaying()) Mixer.pause();
    Mixer.setSpeed(from);
    paint();
    await Mixer.tempoSettled();
    if(!run) return;
    if(judge === "guitar"){
      try{ run.rec = await Timing.capture(); }catch(e){ say(e.message); run.judge = "count"; }
    }
    Mixer.seek(run.a);
    run.unwrap = Mixer.onWrap(wrapped);
    Mixer.play();
    run.passAt = ac().currentTime;
    run.watch = setInterval(watch, 400);
    say(`Trenér: ${run.name} od ${pct(from)} %`);
    paint(); rerenderLane();
  }
  function watch(){
    if(!run) return;
    if(Mixer.currentSong() !== run.song){ stop("jiná píseň"); return; }
    const l = Mixer.loopNow();
    if(!l){ stop("smyčka vypnutá"); return; }
    if(Math.abs(l.start - run.a) > 0.05 || Math.abs(l.end - run.b) > 0.05){ run.a = l.start; run.b = l.end; run.name = nameOf(l.start, l.end); paint(); }
    // the player took the tempo into their own hands
    if(!run.wait && Math.abs(Mixer.speed() - run.speed) > 0.004){ run.speed = Mixer.speed(); run.changeAt = ac().currentTime + 0.4; paint(); }
  }
  // a pass is over
  function wrapped(){
    if(!run) return;
    const now = ac().currentTime, len = (run.b - run.a) / (run.speed || 1);
    const whole = now - run.passAt > len * 0.7;         // a pass that was not cut short by a seek
    run.passAt = now;
    if(!whole) return;
    if(run.judge !== "guitar"){
      run.passes.push({speed: run.speed, ok: true});
      run.best = Math.max(run.best, run.speed);
      const n = run.passes.filter(p => Math.abs(p.speed - run.speed) < 0.004).length;
      if(n >= Math.max(1, cfg.every)) up();
      paint();
      return;
    }
    const batches = run.rec.batches.splice(0), speed = run.speed, after = run.changeAt;
    judge(batches, speed, after);
  }
  async function judge(batches, speed, after){
    const r = run; if(!r) return;
    let res = null;
    try{ res = await Timing.measure({...r.rec, batches, speed, after, quiet: true}); }catch(e){ res = null; }
    if(run !== r) return;
    if(!res){
      r.passes.push({speed, ok: null});
      if(++r.deaf === 3) say("Trenér tě neslyší hrát s písní — je zapnutá kytara a hraješ v rytmu?");
      paint(); return;
    }
    r.deaf = 0;
    const missed = (res.missed || []).length, cover = res.n / Math.max(1, res.n + missed);
    const ok = res.within >= cfg.need && cover >= 0.6;
    r.passes.push({speed, ok, within: res.within, cover: Math.round(cover * 100), median: res.median});
    if(ok){ r.fails = 0; r.best = Math.max(r.best, speed); up(); }
    else if(++r.fails >= 3 && cfg.back){ r.fails = 0; down(); }
    paint();
  }
  function setSpeed(v){
    run.speed = Math.round(v * 100) / 100; run.wait = true;
    Mixer.setSpeed(run.speed);
    run.changeAt = ac().currentTime + 0.5;
    Mixer.tempoSettled().then(() => { if(run){ run.wait = false; run.changeAt = ac().currentTime + 0.25; } });
  }
  function up(){
    if(run.speed >= run.to - 0.004){ finish(); return; }
    const next = Math.min(run.to, run.speed + cfg.step / 100);
    setSpeed(next);
    say(`⏩ čistě — tempo ${pct(next)} %`);
  }
  function down(){
    const next = Math.max(run.from, run.speed - cfg.step / 100);
    if(next >= run.speed - 0.004){ say("Zkus to ještě pár průchodů — nebo sniž počáteční tempo"); return; }
    setSpeed(next);
    say(`Tři průchody nesedly — zpátky na ${pct(next)} %`);
  }
  function finish(){
    const r = run;
    say(`🎉 ${r.name} čistě na ${pct(r.to)} %!`);
    r.done = true;
    stop();
  }
  async function stop(why){
    const r = run; if(!r) return;
    run = null;
    clearInterval(r.watch);
    if(r.unwrap) r.unwrap();
    if(r.rec) try{ await r.rec.close(); }catch(e){}
    if(why) say(`Trenér zastaven (${why})`);
    if(r.passes.length && typeof Journal !== "undefined")
      Journal.event(r.song, "trainer", {section: r.name, from: pct(r.from), to: pct(r.to), reached: pct(r.best || 0), passes: r.passes.length,
        clean: r.passes.filter(p => p.ok).length, done: !!r.done, judged: r.judge === "guitar"});
    last = r;
    paint(); rerenderLane();
  }
  let last = null;
  const toggle = () => (run ? (stop(), "trenér zastaven") : (start(), "trenér rychlosti"));
  function rerenderLane(){ try{ if(typeof renderTrackLanes === "function") renderTrackLanes(); }catch(e){} }

  // ---------------------------------------------------------------- the window
  function css(){
    if(document.getElementById("tr-css")) return;
    const s = document.createElement("style"); s.id = "tr-css";
    s.textContent = `
      #tr-win{position:fixed;right:18px;top:90px;width:340px;max-height:calc(100vh - 120px);overflow:auto;z-index:4000;
        background:linear-gradient(var(--card,#191a2a),var(--card,#191a2a)),#14151d;color:var(--text,#eef0ff);border:1px solid var(--border,#262840);border-radius:14px;
        box-shadow:0 24px 70px rgba(0,0,0,.6)}
      #tr-win header{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border,#262840);cursor:grab;user-select:none}
      #tr-win header b{font-size:15px}#tr-win header .tr-x{margin-left:auto;background:none;border:none;color:var(--sub,#8f96c0);font-size:20px;cursor:pointer}
      .tr-body{padding:12px 14px 14px;font-size:13px}
      .tr-where{color:var(--sub,#8f96c0);font-size:12px;margin-bottom:10px}.tr-where b{color:var(--text,#eef0ff)}
      .tr-grid{display:grid;grid-template-columns:auto 1fr;gap:8px 10px;align-items:center}
      .tr-grid input[type=number]{width:62px;font:inherit;background:var(--bg2,#12131f);color:inherit;border:1px solid var(--border,#262840);border-radius:7px;padding:4px 6px}
      .tr-grid label{color:var(--sub,#8f96c0);font-size:12px}
      .tr-judge{display:flex;flex-direction:column;gap:5px;grid-column:1/-1;font-size:12.5px}
      .tr-judge small{color:var(--sub,#8f96c0);font-size:11px;margin-left:22px;line-height:1.35}
      .tr-go{display:block;width:100%;margin-top:12px;padding:9px;border:none;border-radius:10px;font:inherit;font-weight:700;font-size:14px;cursor:pointer;
        background:var(--accent,#8b7bff);color:#0c0c16}
      .tr-go.stop{background:#ff5f6d;color:#16070a}
      .tr-big{font-size:38px;font-weight:800;text-align:center;letter-spacing:-1px;margin:4px 0 2px}
      .tr-big small{font-size:14px;font-weight:500;color:var(--sub,#8f96c0);letter-spacing:0}
      .tr-bar{height:8px;border-radius:5px;background:var(--bg2,#12131f);overflow:hidden;margin:6px 0 10px}
      .tr-bar i{display:block;height:100%;background:linear-gradient(90deg,#4ea8ff,#3ecf8e);border-radius:5px;transition:width .4s}
      .tr-passes{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
      .tr-p{font-size:11px;padding:2px 7px;border-radius:999px;background:#22242e;color:var(--sub,#999)}
      .tr-p.ok{background:rgba(62,207,142,.16);color:#3ecf8e}.tr-p.no{background:rgba(255,159,67,.14);color:#ff9f43}
      .tr-note{color:var(--sub,#8f96c0);font-size:11.5px;margin-top:8px;line-height:1.4}
      .tr-nudge{display:flex;gap:6px;justify-content:center}
      .tr-nudge button{font:inherit;font-size:12px;background:var(--bg2,#12131f);color:inherit;border:1px solid var(--border,#262840);border-radius:8px;padding:4px 10px;cursor:pointer}`;
    document.head.appendChild(s);
  }
  function drag(hd){
    hd.addEventListener("pointerdown", (e) => {
      if(e.target.closest("button,input,select")) return;
      const r = win.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      win.style.right = "auto"; win.style.left = r.left + "px"; win.style.top = r.top + "px";
      hd.setPointerCapture(e.pointerId);
      const mv = (ev) => { win.style.left = Math.max(0, Math.min(innerWidth - 120, ev.clientX - dx)) + "px"; win.style.top = Math.max(0, Math.min(innerHeight - 50, ev.clientY - dy)) + "px"; };
      const up = () => { hd.removeEventListener("pointermove", mv); hd.removeEventListener("pointerup", up); };
      hd.addEventListener("pointermove", mv); hd.addEventListener("pointerup", up);
    });
  }
  function open(){
    if(!(Mixer.currentSong && Mixer.currentSong())){ say("Nejdřív otevři píseň ve Steems"); return; }
    css();
    if(!win){
      win = document.createElement("div"); win.id = "tr-win";
      win.innerHTML = `<header><b>⏩ Trenér rychlosti</b><button type="button" class="tr-x" title="Zavřít (trenér běží dál)">×</button></header><div class="tr-body"></div>`;
      document.body.appendChild(win);
      drag(win.querySelector("header"));
      win.querySelector(".tr-x").onclick = close;
    }
    paint();
  }
  function close(){ if(win){ win.remove(); win = null; } }
  function chips(passes){
    return passes.slice(-24).map(p => p.ok === null ? `<span class="tr-p" title="Nic jsem neslyšel">${pct(p.speed)} % ·?</span>`
      : `<span class="tr-p ${p.ok ? "ok" : "no"}" title="${p.within != null ? `${p.within} % tónů do 25 ms, zahráno ${p.cover} % tónů` : "průchod"}">${pct(p.speed)} % ${p.ok ? "✓" : "✗"}${p.within != null ? ` ${p.within}` : ""}</span>`).join("");
  }
  function paint(){
    if(!win) return;
    const body = win.querySelector(".tr-body");
    if(run){
      const span = Math.max(0.01, run.to - run.from), done = Math.max(0, Math.min(1, (run.speed - run.from) / span));
      body.innerHTML = `<div class="tr-where">Cvičíš <b>${esc(run.name)}</b> · ${run.judge === "guitar" ? `čistý průchod = aspoň ${cfg.need} % tónů do 25 ms` : `+${cfg.step} % po ${cfg.every} průchodech`}</div>
        <div class="tr-big">${pct(run.speed)} %<small> → ${pct(run.to)} %</small></div>
        <div class="tr-bar"><i style="width:${Math.round(done * 100)}%"></i></div>
        <div class="tr-nudge"><button type="button" data-n="-1" title="O krok pomaleji">− ${cfg.step} %</button><button type="button" data-n="1" title="O krok rychleji">+ ${cfg.step} %</button></div>
        <div class="tr-passes">${chips(run.passes) || `<span class="tr-note">Hraj — po každém průchodu smyčkou tu přibude výsledek.</span>`}</div>
        <button type="button" class="tr-go stop">■ Zastavit trenéra</button>`;
      body.querySelector(".tr-go").onclick = () => stop();
      body.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { if(!run) return; const v = run.speed + (+b.dataset.n) * cfg.step / 100;
        setSpeed(Math.max(0.5, Math.min(run.to, v))); paint(); });
      return;
    }
    const st = stretch(), guitarOn = !!(Mixer.guitar && Mixer.guitar.isLive());
    const where = st ? `${st.set ? "Smyčku nemáš — vezmu sekci u kurzoru: " : "Smyčka: "}<b>${esc(nameOf(st.a, st.b))}</b>` : `<b>Dej smyčku na těžké místo</b> (nebo klikni do sekce) — trenér ji pak hraje dokola.`;
    body.innerHTML = `<div class="tr-where">${where}</div>
      ${last ? `<div class="tr-note" style="margin:0 0 10px">Naposledy: ${esc(last.name)} — ${last.done ? `čistě na ${pct(last.to)} % 🎉` : `nejvýš ${pct(last.best || last.from)} % (${last.passes.length} průchodů)`}</div>` : ""}
      <div class="tr-grid">
        <label>Začít na</label><span><input type="number" id="tr-from" min="50" max="150" step="5" value="${cfg.from}"> %</span>
        <label>Přidávat</label><span><input type="number" id="tr-step" min="1" max="20" step="1" value="${cfg.step}"> %</span>
        <label>Cíl</label><span><input type="number" id="tr-to" min="50" max="150" step="5" value="${cfg.to}"> %</span>
        <div class="tr-judge">
          <label><input type="radio" name="tr-j" value="guitar" ${cfg.judge === "guitar" ? "checked" : ""}> Posuzovat podle mé kytary</label>
          <small>Čistý průchod: aspoň <input type="number" id="tr-need" min="30" max="100" step="5" value="${cfg.need}" style="width:52px"> % tónů do 25 ms od originálu. ${guitarOn ? "Kytara je zapnutá ✓" : "Zapni vstup kytary na kanálu Live kytara."}</small>
          <label><input type="radio" name="tr-j" value="count" ${cfg.judge === "count" ? "checked" : ""}> Jen počítat průchody</label>
          <small>Tempo přidá každé <input type="number" id="tr-every" min="1" max="10" step="1" value="${cfg.every}" style="width:46px"> průchody.</small>
          <label><input type="checkbox" id="tr-back" ${cfg.back ? "checked" : ""}> Po třech nečistých průchodech o krok zpět</label>
        </div>
      </div>
      <button type="button" class="tr-go" ${st ? "" : "disabled"}>▶ Spustit trenéra</button>
      <div class="tr-note">Ovládat jde i pedálem (Nastavení → MIDI pedál → „Trenér rychlosti“).</div>`;
    const q = (id) => body.querySelector("#" + id);
    const num = (id, k, lo, hi) => { q(id).onchange = (e) => { const v = Math.round(+e.target.value); if(isFinite(v)) { cfg[k] = Math.max(lo, Math.min(hi, v)); keep(); } e.target.value = cfg[k]; }; };
    num("tr-from", "from", 50, 150); num("tr-step", "step", 1, 20); num("tr-to", "to", 50, 150); num("tr-need", "need", 30, 100); num("tr-every", "every", 1, 10);
    body.querySelectorAll("[name=tr-j]").forEach(r => r.onchange = () => { cfg.judge = r.value; keep(); });
    q("tr-back").onchange = (e) => { cfg.back = e.target.checked; keep(); };
    body.querySelector(".tr-go").onclick = () => start();
  }

  return {open, close, start, stop, toggle, running: () => !!run, state: () => run, config: () => cfg, _wrapped: () => wrapped()};
})();
