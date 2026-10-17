// Looper — a favourite song rebuilt layer by layer, live (docs/specs/2026-10-17-looper-design.md).
//
// Five tracks, each one button as on a Boss RC-505: empty → record → play → overdub → play. A
// recording starts on the next bar and ends on a bar — by itself after the bars set for the track,
// or at the bar after the second press. Pressing another track while one records hands over on the
// same bar. The song's parts (intro, verse, chorus…) are scenes: each remembers which tracks play,
// and one chosen switches at the next bar. All of it by the keys or a MIDI pedal.
//
// Sound: the mixer's audio context and output. Sources: the live guitar of Steems (through Pick's
// amp; its take recorder hands over the samples stamped with the audio clock) or the singing
// microphone. The interface's latency (the takes' latency) is taken off every recording.
const Looper = (() => {
  const N = 5;
  const BARS = [0, 1, 2, 4, 8, 16];          // 0 = "auto": until pressed again
  const CSS = `
  .lp{position:fixed;inset:0;z-index:9200;display:flex;flex-direction:column;color:#f1f1f6;font-size:13px;
    background:radial-gradient(1000px 600px at 10% -10%,color-mix(in srgb,var(--accent,#8b5cf6) 22%,transparent),transparent 60%),
      radial-gradient(900px 560px at 110% 110%,rgba(255,80,80,.12),transparent 60%),#08080d}
  .lp[hidden]{display:none}
  .lp-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 18px;background:rgba(14,14,22,.66);border-bottom:1px solid rgba(255,255,255,.07)}
  .lp-bar h2{margin:0 8px 0 0;font-size:18px;letter-spacing:.02em}
  .lp-bar .song{color:#a6a6bc;font-size:12px;max-width:26vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lp button,.lp select,.lp input{font:inherit;color:#f1f1f6}
  .lp-b{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:11px;padding:7px 12px;cursor:pointer}
  .lp-b:hover{background:rgba(255,255,255,.12)}
  .lp-b.on{background:var(--accent,#8b5cf6);color:#0b0b12;border-color:var(--accent,#8b5cf6);font-weight:700}
  .lp-b:disabled{opacity:.4;cursor:default}
  .lp-f{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:11px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07)}
  .lp-f input[type=number]{width:62px;background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 6px}
  .lp-f input[type=range]{width:80px}
  .lp-sp{flex:1}
  .lp-go{width:46px;height:46px;border-radius:50%;border:none;background:var(--accent,#8b5cf6);color:#0b0b12;cursor:pointer;display:flex;align-items:center;justify-content:center}
  .lp-x{width:36px;height:36px;border-radius:50%;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);cursor:pointer;display:flex;align-items:center;justify-content:center}
  .lp-pos{font-variant-numeric:tabular-nums;font-size:22px;font-weight:800;min-width:92px;text-align:center}
  .lp-pos small{display:block;font-size:10px;font-weight:600;color:#9a9ab0;letter-spacing:.08em}
  .lp-beats{display:flex;gap:4px}.lp-beats i{width:12px;height:12px;border-radius:50%;background:rgba(255,255,255,.12)}
  .lp-beats i.on{background:var(--accent,#8b5cf6)}.lp-beats i.on.one{background:#ff5c5c}
  .lp-main{flex:1;min-height:0;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:16px}
  .lp-tracks{display:grid;grid-template-columns:repeat(${N},minmax(150px,1fr));gap:12px}
  .lp-t{position:relative;border-radius:18px;padding:12px;display:flex;flex-direction:column;align-items:center;gap:8px;
    background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08)}
  .lp-t.sel{border-color:var(--accent,#8b5cf6);box-shadow:0 0 0 1px var(--accent,#8b5cf6),0 0 26px color-mix(in srgb,var(--accent,#8b5cf6) 25%,transparent)}
  .lp-t .nm{width:100%;display:flex;align-items:center;gap:6px}
  .lp-t .nm b{font-size:12px;color:#9a9ab0}
  .lp-t .nm input{flex:1;min-width:0;background:transparent;border:1px solid transparent;border-radius:6px;padding:3px 5px;font-weight:700}
  .lp-t .nm input:focus{border-color:rgba(255,255,255,.2);background:#0e0e14}
  .lp-btn{position:relative;width:118px;height:118px;border-radius:50%;border:none;cursor:pointer;background:#15151d;
    box-shadow:inset 0 2px 0 rgba(255,255,255,.06),0 10px 28px rgba(0,0,0,.5);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
  .lp-btn svg{position:absolute;inset:-6px;width:130px;height:130px;transform:rotate(-90deg);pointer-events:none}
  .lp-btn .rg{fill:none;stroke:rgba(255,255,255,.08);stroke-width:6}
  .lp-btn .pr{fill:none;stroke:var(--st,#3a3a48);stroke-width:6;stroke-linecap:round}
  .lp-btn .core{width:64px;height:64px;border-radius:50%;background:var(--st,#2a2a36);box-shadow:0 0 22px color-mix(in srgb,var(--st,#2a2a36) 60%,transparent);
    display:flex;align-items:center;justify-content:center;color:#0b0b12;font-weight:900;font-size:22px;transition:background .15s}
  .lp-btn .lbl{position:absolute;bottom:-22px;font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--st,#8a8aa0);white-space:nowrap}
  .lp-t[data-s="empty"] .lp-btn{--st:#3a3a48}
  .lp-t[data-s="armed"] .lp-btn,.lp-t[data-s="armod"] .lp-btn{--st:#ff5c5c;animation:lpblink .5s steps(2) infinite}
  .lp-t[data-s="armod"] .lp-btn{--st:#ffa53d}
  .lp-t[data-s="rec"] .lp-btn{--st:#ff4d4d}
  .lp-t[data-s="dub"] .lp-btn{--st:#ffa53d}
  .lp-t[data-s="play"] .lp-btn{--st:#3ddc97}
  .lp-t[data-s="mute"] .lp-btn{--st:#6a6a7a}
  @keyframes lpblink{50%{filter:brightness(.45)}}
  .lp-t .meta{margin-top:18px;font-size:11px;color:#9a9ab0;text-align:center;min-height:15px}
  .lp-t .row{display:flex;gap:5px;flex-wrap:wrap;justify-content:center;width:100%}
  .lp-t .row select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:4px 6px;font-size:12px}
  .lp-t .row button{font-size:12px;padding:4px 9px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);cursor:pointer}
  .lp-t .row button.on{background:#6a6a7a;border-color:#6a6a7a}
  .lp-t .vol{width:100%;display:flex;align-items:center;gap:6px;font-size:11px;color:#9a9ab0}.lp-t .vol input{flex:1}
  .lp-sc{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .lp-sc .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-sc button{min-width:96px;padding:10px 14px;border-radius:13px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.05);cursor:pointer;text-align:left}
  .lp-sc button b{display:block}.lp-sc button span{font-size:11px;color:#9a9ab0}
  .lp-sc button.cur{background:color-mix(in srgb,var(--accent,#8b5cf6) 30%,#14141c);border-color:var(--accent,#8b5cf6)}
  .lp-sc button.next{animation:lpblink .5s steps(2) infinite;border-color:#ffa53d}
  .lp-help{font-size:12px;color:#8a8aa0;line-height:1.6}
  .lp-help kbd{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);border-radius:5px;padding:0 5px;font:inherit;font-size:11px;color:#ddd}
  .lp-ped{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .lp-ped button{font-size:11px;padding:4px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);cursor:pointer}
  `;
  { const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st); }
  const esc = (s) => escapeHtml(String(s == null ? "" : s));

  // ---- state
  const KEY = "pick-looper";
  const cfg = {bpm: 100, perBar: 4, click: true, clickVol: 0.5, countIn: true, autoNext: true, monMic: false, vol: 0.9};
  try{ Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  const tracks = Array.from({length: N}, (_, i) => ({i, name: `Stopa ${i + 1}`, src: i === N - 1 ? "mic" : "gtr", bars: 0, vol: 0.9,
    layers: [], undone: [], len: 0, anchor: 0, gain: null, job: null}));
  let scenes = [], cur = 0, pending = null;     // scenes: {name, on: Set of track numbers}
  let ac = null, out = null, bus = null, clickBus = null;
  let running = false, t0 = 0, sel = 0, song = null, open = false;
  const history = [];                           // tracks touched by a layer, for undo
  const barLen = () => 60 / cfg.bpm * cfg.perBar;
  const beatLen = () => 60 / cfg.bpm;
  const lat = () => { try{ return Math.max(0, Mixer.takeLatency() || 0); }catch(e){ return 0; } };
  const now = () => ac.currentTime;
  const hasAudio = () => tracks.some(t => t.layers.length);

  function audio(){
    if(ac) return;
    const o = Mixer.voiceOut();
    ac = o.ac; out = o.out;
    bus = ac.createGain(); bus.gain.value = cfg.vol; bus.connect(out);
    clickBus = ac.createGain(); clickBus.gain.value = cfg.clickVol; clickBus.connect(out);
    tracks.forEach(t => { t.gain = ac.createGain(); t.gain.connect(bus); });
  }
  // the next bar line from t (a hair late counts as on it)
  const nextBar = (t) => { const b = barLen(), k = Math.ceil((t - t0 - 0.03) / b); return t0 + Math.max(0, k) * b; };

  // ---- sources: what a recording takes from — the guitar through Pick's amp, or the microphone
  const srcs = {
    gtr: {name: "Kytara", batches: [], on: false, async start(){
      const G = Mixer.guitar;
      if(!G.isLive()){ await Mixer.voice.myGuitar(true); }
      if(!G.isLive()){ showToast("Kytara není zapnutá — připoj zvukovku a zapni vstup kytary ve Steems"); return false; }
      if(G.isRecording() && !this.on){ showToast("Kytara se právě nahrává jinde (nahrávání ve Steems)"); return false; }
      if(this.on) return true;
      this.batches = [];
      this.on = G.startRecording((b) => this.batches.push(b));
      return this.on;
    }, async stop(){ if(!this.on) return; this.on = false; await Mixer.guitar.stopRecording(); this.batches = []; }},
    mic: {name: "Mikrofon", batches: [], on: false, node: null, stream: null, mon: null, async start(){
      if(this.on) return true;
      try{
        this.stream = await VoiceMic.open();
        const s = ac.createMediaStreamSource(this.stream);
        const n = this.stream.getAudioTracks()[0].getSettings().channelCount || 1;
        const split = ac.createChannelSplitter(Math.max(1, n)), pick = ac.createGain();
        s.connect(split); split.connect(pick, VoiceMic.index(n));
        let node;
        try{ node = new AudioWorkletNode(ac, "take-recorder", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]}); }
        catch(e){ await ac.audioWorklet.addModule("/static/worklets/guitar.js"); node = new AudioWorkletNode(ac, "take-recorder", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]}); }
        this.mon = ac.createGain(); this.mon.gain.value = cfg.monMic ? 1 : 0;
        pick.connect(node); node.connect(this.mon); this.mon.connect(out);
        this.batches = [];
        node.port.onmessage = (e) => { if(e.data && e.data.data) this.batches.push(e.data); };
        node.port.postMessage("start");
        this.node = node; this.on = true;
        return true;
      }catch(e){ showToast("Mikrofon se nepodařilo otevřít — vyber ho v Nastavení (Mikrofon pro zpěv)"); return false; }
    }, async stop(){
      if(!this.on) return;
      this.on = false;
      try{ this.node.port.postMessage("stop"); this.node.disconnect(); this.mon.disconnect(); }catch(e){}
      try{ this.stream.getTracks().forEach(t => t.stop()); }catch(e){}
      this.node = this.stream = this.mon = null; this.batches = [];
    }},
  };
  // The samples between two moments of music, the latency taken off: what was played then.
  function cut(src, from, to){
    const sr = ac.sampleRate, L = lat(), n = Math.max(1, Math.round((to - from) * sr));
    const out2 = [new Float32Array(n), new Float32Array(n)];
    src.batches.forEach(b => {
      const m0 = b.t - L;                         // the music this batch's first sample answered
      const i0 = Math.round((m0 - from) * sr);
      if(i0 >= n || i0 + b.n <= 0) return;
      for(let c = 0; c < 2; c++){
        const d = b.data[Math.min(c, b.data.length - 1)];
        const a = Math.max(0, -i0), e = Math.min(b.n, n - i0);
        if(e > a) out2[c].set(d.subarray(a, e), i0 + a);
      }
    });
    return out2;
  }

  // ---- playing: every layer a looping source, in step with the track's own start
  function startLayer(t, layer, when){
    if(!running) return;
    const s = ac.createBufferSource();
    s.buffer = layer.buf; s.loop = true;
    s.connect(t.gain);
    const off = (((when - t.anchor) % t.len) + t.len) % t.len;
    s.start(when, off);
    layer.src = s;
  }
  function stopLayer(layer){ if(layer.src){ try{ layer.src.stop(); }catch(e){} layer.src.disconnect(); layer.src = null; } }
  function applyMutes(at){
    const on = scenes[cur] ? scenes[cur].on : new Set(tracks.map(t => t.i));
    tracks.forEach(t => { const g = on.has(t.i) ? t.vol : 0; t.gain.gain.cancelScheduledValues(at); t.gain.gain.setTargetAtTime(g, at, 0.008); });
  }

  // ---- the transport
  function start(countIn){
    audio();
    try{ Mixer.pause(); }catch(e){}
    if(running) return;
    if(ac.state === "suspended") ac.resume();
    const lead = countIn ? barLen() : 0;
    t0 = now() + 0.08 + lead;
    running = true;
    clickFrom = countIn ? t0 - lead : t0;
    tracks.forEach(t => { if(t.layers.length){ t.anchor = t0; t.layers.forEach(l => startLayer(t, l, t0)); } });
    applyMutes(now());
    paint();
  }
  function stopAll(){
    if(!running) return;
    running = false;
    tracks.forEach(t => { t.layers.forEach(stopLayer); if(t.job) cancelJob(t); });
    pending = null;
    Object.values(srcs).forEach(s => s.stop());
    paint();
  }

  // ---- recording: one at a time; a job is {kind: "first" | "dub", from, to (null = until pressed)}
  let recTrack = null;
  async function arm(t, kind){
    audio();
    const src = srcs[t.src];
    if(!(await src.start())) return;
    if(!running) start(cfg.countIn);
    let from = nextBar(now());
    // a handover: the one recording ends on the bar this one starts on
    if(recTrack && recTrack !== t && recTrack.job){
      const j = recTrack.job;
      if(j.to == null || j.to > from) j.to = Math.max(j.from + barLen(), from);
      from = Math.max(from, j.to);
    }
    t.job = {kind, from, to: null, src: t.src};
    if(kind === "first" && t.bars) t.job.to = from + t.bars * barLen();
    if(kind === "dub") t.job.to = from + t.len;                  // one pass, unless pressed sooner
    recTrack = t;
    // a recording joins the part playing
    if(scenes[cur]) scenes[cur].on.add(t.i);
    paint();
  }
  function cancelJob(t){ t.job = null; if(recTrack === t) recTrack = null; }
  function press(i){
    const t = tracks[i];
    if(!t) return;
    sel = i;
    const j = t.job;
    if(j){
      const tm = now();
      if(tm < j.from){ cancelJob(t); maybeStopSrc(t.src); paint(); return; }    // still waiting: called off
      const end = Math.max(j.from + barLen(), nextBar(tm));
      j.to = j.to == null ? end : Math.min(j.to, end);
      paint(); return;
    }
    if(!t.layers.length) arm(t, "first"); else arm(t, "dub");
  }
  function maybeStopSrc(name){ if(!tracks.some(t => t.job && t.job.src === name)) srcs[name].stop(); }
  // finished recordings become layers as soon as their last samples are in
  function finish(){
    const tm = now(), L = lat();
    tracks.forEach(t => {
      const j = t.job;
      if(!j || j.to == null || tm < j.to + L + 0.06) return;
      const src = srcs[j.src], data = cut(src, j.from, j.to);
      if(j.kind === "first"){
        t.len = j.to - j.from; t.anchor = j.from;
        const buf = ac.createBuffer(2, data[0].length, ac.sampleRate);
        buf.copyToChannel(data[0], 0); buf.copyToChannel(data[1], 1);
        addLayer(t, {buf});
      } else {
        // an overdub laid onto the loop where it was played, round past the end
        const n = Math.round(t.len * ac.sampleRate), buf = ac.createBuffer(2, n, ac.sampleRate);
        const at = Math.round(((((j.from - t.anchor) % t.len) + t.len) % t.len) * ac.sampleRate);
        for(let c = 0; c < 2; c++){ const d = buf.getChannelData(c), s = data[c]; for(let k = 0; k < s.length; k++){ d[(at + k) % n] += s[k]; } }
        addLayer(t, {buf});
      }
      cancelJob(t);
      if(j.kind === "first" && cfg.autoNext){ const nx = tracks.findIndex(x => !x.layers.length && !x.job); if(nx >= 0) sel = nx; }
      maybeStopSrc(j.src);
      paint();
    });
  }
  function addLayer(t, layer){
    t.layers.push(layer); t.undone = [];
    history.push(t.i);
    startLayer(t, layer, now() + 0.005);
  }
  function undo(){
    const i = history.pop();
    if(i == null){ showToast("Není co vrátit"); return; }
    const t = tracks[i], l = t.layers.pop();
    if(l){ stopLayer(l); t.undone.push(l); }
    if(!t.layers.length) t.len = 0;
    paint();
  }
  function redo(){
    const t = tracks.find(x => x.undone.length);
    if(!t){ showToast("Není co vrátit zpět"); return; }
    const l = t.undone.pop();
    if(!t.len) t.len = l.buf.duration;
    t.layers.push(l); history.push(t.i);
    startLayer(t, l, now() + 0.005);
    paint();
  }
  function clear(i){
    const t = tracks[i];
    t.layers.forEach(stopLayer); t.layers = []; t.undone = []; t.len = 0; cancelJob(t);
    for(let k = history.length - 1; k >= 0; k--) if(history[k] === i) history.splice(k, 1);
    paint();
  }
  function toggleTrack(i){
    const sc = scenes[cur]; if(!sc) return;
    sc.on.has(i) ? sc.on.delete(i) : sc.on.add(i);
    applyMutes(now());
    paint();
  }

  // ---- parts (scenes): which tracks play; a part chosen switches at the next bar
  function makeScenes(given){
    let names = (given || []).filter((n, k, a) => n && a.indexOf(n) === k);
    if(!names.length) try{ (Mixer.sectionsNow() || []).forEach(s => { if(!names.includes(s.name)) names.push(s.name); }); }catch(e){}
    if(!names.length) names = ["A", "B", "C"];
    const all = new Set(tracks.map(t => t.i));
    scenes = names.slice(0, 8).map(n => ({name: n, on: new Set(all)}));
    cur = 0; pending = null;
  }
  function goScene(k){
    if(k < 0 || k >= scenes.length) return;
    if(!running){ cur = k; applyMutes(now()); paint(); return; }
    pending = {k, at: nextBar(now())};
    paint();
  }
  function addScene(){
    const s = scenes[cur];
    scenes.push({name: String.fromCharCode(65 + scenes.length), on: new Set(s ? s.on : tracks.map(t => t.i))});
    paint();
  }

  // ---- the click and the clock: a little scheduler a tenth of a second ahead
  let clickFrom = 0, clickNext = 0;
  function tick(){
    if(!ac || !running){ return; }
    const tm = now(), b = beatLen();
    if(clickNext < clickFrom) clickNext = clickFrom;
    while(clickNext < tm + 0.12){
      const k = Math.round((clickNext - t0) / b), first = ((k % cfg.perBar) + cfg.perBar) % cfg.perBar === 0;
      if((cfg.click || clickNext < t0) && clickNext >= tm - 0.01) beep(clickNext, first);
      clickNext += b;
    }
    if(pending && tm >= pending.at - 0.02){ cur = pending.k; pending = null; applyMutes(Math.max(tm, 0)); paint(); }
    finish();
  }
  function beep(at, first){
    const o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = first ? 1760 : 1175;
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(first ? 0.9 : 0.55, at + 0.002); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.06);
    o.connect(g); g.connect(clickBus); o.start(at); o.stop(at + 0.08);
  }
  setInterval(tick, 25);

  // ---- the page
  let root = null;
  const ring = 2 * Math.PI * 59;
  function build(){
    root = document.createElement("div");
    root.className = "lp"; root.id = "looper"; root.hidden = true;
    root.innerHTML = `<div class="lp-bar">
        <h2>Looper</h2><span class="song" id="lp-song"></span>
        <button type="button" class="lp-go" id="lp-go" title="Spustit / zastavit všechno (mezerník)"></button>
        <div class="lp-pos" id="lp-pos">1.1<small>TAKT.DOBA</small></div><div class="lp-beats" id="lp-beats"></div>
        <span class="lp-f" title="Tempo — z písně, nebo vlastní. Měnit jde, jen když jsou stopy prázdné.">Tempo <input type="number" id="lp-bpm" min="40" max="240" step="0.1"> BPM
          <button type="button" class="lp-b" id="lp-tap" title="Ťukni v rytmu (aspoň 4×)">Tap</button></span>
        <span class="lp-f"><button type="button" class="lp-b" id="lp-click" title="Klik (metronom)">Klik</button><input type="range" id="lp-clickv" min="0" max="1" step="0.01" title="Hlasitost kliku"></span>
        <button type="button" class="lp-b" id="lp-count" title="Před prvním nahráváním odpočet jednoho taktu">Odpočet</button>
        <button type="button" class="lp-b" id="lp-mon" title="Slyšet mikrofon ve sluchátkách (jen se sluchátky — z reproduktorů by pískal)">Slyšet mikrofon</button>
        <span class="lp-sp"></span>
        <button type="button" class="lp-b" id="lp-undo" title="Vrátit poslední vrstvu (Z)">↶ Zpět</button>
        <button type="button" class="lp-b" id="lp-redo" title="Znovu (Shift+Z)">↷</button>
        <span class="lp-f" title="Hlasitost všech smyček">Smyčky <input type="range" id="lp-vol" min="0" max="1.4" step="0.01"></span>
        <button type="button" class="lp-x" id="lp-close" title="Zavřít (smyčky zůstanou, dokud nezavřeš Pick)">${icon("close", 14)}</button>
      </div>
      <div class="lp-main">
        <div class="lp-tracks" id="lp-tracks"></div>
        <div class="lp-sc" id="lp-sc"></div>
        <div class="lp-help">
          <b>Jak na to:</b> tlačítko stopy = <b>nahrát → hrát → přidat vrstvu → hrát</b>. Nahrávání začne na začátku taktu a skončí na konci taktu —
          samo po zvolené délce, nebo po druhém stisku (auto). Když nahráváš a stiskneš jinou stopu, předá se na stejném taktu. Části písně dole:
          každá si pamatuje, které stopy hrají (M na stopě); vybraná se přepne na začátku dalšího taktu.<br>
          Klávesy: <kbd>1</kbd>–<kbd>5</kbd> tlačítka stop · <kbd>mezerník</kbd> spustit / zastavit · <kbd>Tab</kbd> další stopa ·
          <kbd>Enter</kbd> tlačítko vybrané stopy · <kbd>Z</kbd> zpět · <kbd>Shift</kbd>+<kbd>Z</kbd> znovu · <kbd>←</kbd> <kbd>→</kbd> části
          · Zpoždění zvukovky <b id="lp-lat"></b> se z nahrávek odečítá (měří se v nahrávání ve Steems).
          <div class="lp-ped" id="lp-ped"></div>
        </div>
      </div>`;
    document.body.appendChild(root);
    const $ = (id) => root.querySelector("#" + id);
    $("lp-go").onclick = () => (running ? stopAll() : start(false));
    $("lp-close").onclick = close;
    $("lp-undo").onclick = undo; $("lp-redo").onclick = redo;
    $("lp-bpm").onchange = (e) => setBpm(parseFloat(e.target.value));
    $("lp-click").onclick = () => { cfg.click = !cfg.click; keep(); paint(); };
    $("lp-clickv").oninput = (e) => { cfg.clickVol = +e.target.value; keep(); if(clickBus) clickBus.gain.value = cfg.clickVol; };
    $("lp-count").onclick = () => { cfg.countIn = !cfg.countIn; keep(); paint(); };
    $("lp-mon").onclick = () => { cfg.monMic = !cfg.monMic; keep(); if(srcs.mic.mon) srcs.mic.mon.gain.value = cfg.monMic ? 1 : 0; paint(); };
    $("lp-vol").oninput = (e) => { cfg.vol = +e.target.value; keep(); if(bus) bus.gain.value = cfg.vol; };
    let taps = [];
    $("lp-tap").onclick = () => {
      const t = performance.now(); taps = taps.filter(x => t - x < 3000); taps.push(t);
      if(taps.length >= 4){ const d = (taps[taps.length - 1] - taps[0]) / (taps.length - 1); setBpm(Math.round(60000 / d * 10) / 10); }
    };
    $("lp-tracks").innerHTML = tracks.map(t => `<div class="lp-t" data-i="${t.i}">
        <div class="nm"><b>${t.i + 1}</b><input type="text" maxlength="24" value="${esc(t.name)}" title="Název stopy"></div>
        <button type="button" class="lp-btn" title="Nahrát / hrát / přidat vrstvu (klávesa ${t.i + 1})">
          <svg viewBox="0 0 130 130"><circle class="rg" cx="65" cy="65" r="59"/><circle class="pr" cx="65" cy="65" r="59" stroke-dasharray="0 ${ring}"/></svg>
          <span class="core">${t.i + 1}</span><span class="lbl"></span></button>
        <div class="meta"></div>
        <div class="row"><select data-k="src" title="Odkud se nahrává"><option value="gtr">Kytara (zesilovač Picku)</option><option value="mic">Mikrofon</option></select>
          <select data-k="bars" title="Délka první smyčky: auto = do druhého stisku">${BARS.map(b => `<option value="${b}">${b ? b + (b === 1 ? " takt" : b < 5 ? " takty" : " taktů") : "auto"}</option>`).join("")}</select></div>
        <div class="row"><button type="button" data-k="mute" title="Hraje v téhle části (M)">M</button><button type="button" data-k="undo" title="Vrátit poslední vrstvu téhle stopy">↶</button><button type="button" data-k="clear" title="Smazat stopu">Smazat</button></div>
        <div class="vol">hlasitost <input type="range" data-k="vol" min="0" max="1.4" step="0.01" value="${t.vol}"></div></div>`).join("");
    root.querySelectorAll(".lp-t").forEach(n => {
      const t = tracks[+n.dataset.i];
      n.querySelector(".lp-btn").onclick = () => press(t.i);
      n.onclick = (e) => { if(!e.target.closest("button, select, input")){ sel = t.i; paint(); } };
      n.querySelector(".nm input").onchange = (e) => { t.name = e.target.value.trim() || `Stopa ${t.i + 1}`; };
      n.querySelector('[data-k="src"]').onchange = (e) => { t.src = e.target.value; };
      n.querySelector('[data-k="bars"]').onchange = (e) => { t.bars = +e.target.value; };
      n.querySelector('[data-k="vol"]').oninput = (e) => { t.vol = +e.target.value; if(ac) applyMutes(now()); };
      n.querySelector('[data-k="mute"]').onclick = () => toggleTrack(t.i);
      n.querySelector('[data-k="undo"]').onclick = () => { const k = history.lastIndexOf(t.i); if(k < 0) return; history.splice(k, 1); history.push(t.i); undo(); };
      n.querySelector('[data-k="clear"]').onclick = async () => { if(!t.layers.length || await Dialog.confirm(`Smazat stopu „${t.name}“ (${t.layers.length} vrstev)?`, {ok: "Smazat", danger: true})) clear(t.i); };
    });
    paintPedal();
  }
  function setBpm(v){
    if(!(v >= 40 && v <= 240)) return;
    if(hasAudio()){ showToast("Tempo jde změnit, jen když jsou stopy prázdné — smyčky jsou nahrané v tomhle tempu"); paint(); return; }
    cfg.bpm = Math.round(v * 10) / 10; keep(); paint();
  }
  const STATE_TXT = {empty: "PRÁZDNÁ", armed: "ČEKÁ NA TAKT", armod: "VRSTVA ČEKÁ", rec: "NAHRÁVÁ", dub: "PŘIDÁVÁ VRSTVU", play: "HRAJE", mute: "TICHO"};
  function stateOf(t){
    const j = t.job, tm = ac ? now() : 0;
    if(j) return tm < j.from ? (j.kind === "first" ? "armed" : "armod") : (j.kind === "first" ? "rec" : "dub");
    if(!t.layers.length) return "empty";
    const on = scenes[cur] ? scenes[cur].on.has(t.i) : true;
    return on ? "play" : "mute";
  }
  function paint(){
    if(!root || root.hidden) return;
    const $ = (id) => root.querySelector("#" + id);
    $("lp-go").innerHTML = icon(running ? "stop" : "play", 18);
    $("lp-bpm").value = cfg.bpm; $("lp-bpm").disabled = hasAudio();
    $("lp-click").classList.toggle("on", cfg.click); $("lp-clickv").value = cfg.clickVol;
    $("lp-count").classList.toggle("on", cfg.countIn); $("lp-mon").classList.toggle("on", cfg.monMic);
    $("lp-vol").value = cfg.vol;
    $("lp-lat").textContent = Math.round(lat() * 1000) + " ms";
    $("lp-song").textContent = song ? "· " + song : "";
    $("lp-beats").innerHTML = Array.from({length: cfg.perBar}, (_, k) => `<i class="${k === 0 ? "one" : ""}"></i>`).join("");
    root.querySelectorAll(".lp-t").forEach(n => {
      const t = tracks[+n.dataset.i], s = stateOf(t);
      n.dataset.s = s; n.classList.toggle("sel", t.i === sel);
      n.querySelector(".lbl").textContent = STATE_TXT[s];
      n.querySelector('[data-k="src"]').value = t.src; n.querySelector('[data-k="bars"]').value = t.bars;
      n.querySelector('[data-k="mute"]').classList.toggle("on", scenes[cur] && !scenes[cur].on.has(t.i));
      const bars = t.len ? Math.round(t.len / barLen() * 100) / 100 : 0;
      n.querySelector(".meta").textContent = t.layers.length ? `${bars} ${bars === 1 ? "takt" : bars < 5 ? "takty" : "taktů"} · ${t.layers.length} ${t.layers.length === 1 ? "vrstva" : t.layers.length < 5 ? "vrstvy" : "vrstev"} · ${srcs[t.src].name}` : srcs[t.src].name;
    });
    $("lp-sc").innerHTML = `<span class="h">ČÁSTI PÍSNĚ</span>` + scenes.map((s, k) => `<button type="button" data-k="${k}" class="${k === cur ? "cur" : ""}${pending && pending.k === k ? " next" : ""}"
        title="Klik: přepnout na začátku dalšího taktu · dvojklik: přejmenovat"><b>${esc(s.name)}</b><span>${s.on.size ? [...s.on].sort().map(x => x + 1).join(" ") : "ticho"}</span></button>`).join("")
      + `<button type="button" data-add title="Nová část — začne se stopami té, co hraje">＋ Část</button>`;
    $("lp-sc").querySelectorAll("[data-k]").forEach(b => {
      b.onclick = () => goScene(+b.dataset.k);
      b.ondblclick = async () => { const s = scenes[+b.dataset.k], v = await Dialog.prompt("Název části:", s.name); if(v && v.trim()){ s.name = v.trim(); paint(); } };
    });
    $("lp-sc").querySelector("[data-add]").onclick = addScene;
  }
  // the moving parts, every frame: the rings, the bar and beat
  function frame(){
    if(root && !root.hidden){
      const tm = ac ? now() : 0;
      root.querySelectorAll(".lp-t").forEach(n => {
        const t = tracks[+n.dataset.i], j = t.job;
        let f = 0;
        if(running && j && tm >= j.from) f = j.to ? (tm - j.from) / (j.to - j.from) : ((tm - j.from) % barLen()) / barLen();
        else if(running && t.len) f = (((tm - t.anchor) % t.len) + t.len) % t.len / t.len;
        n.querySelector(".pr").setAttribute("stroke-dasharray", `${(Math.max(0, Math.min(1, f)) * ring).toFixed(1)} ${ring}`);
        const s = stateOf(t);
        if(n.dataset.s !== s){ paint(); }
      });
      const pos = root.querySelector("#lp-pos"), beats = root.querySelectorAll("#lp-beats i");
      if(running){
        const k = Math.floor((tm - t0) / beatLen()), bar = Math.floor(k / cfg.perBar), beat = ((k % cfg.perBar) + cfg.perBar) % cfg.perBar;
        pos.innerHTML = (tm < t0 ? "odpočet " + (cfg.perBar - (((-k - 1) % cfg.perBar) + cfg.perBar) % cfg.perBar) : `${bar + 1}.${beat + 1}`) + "<small>TAKT.DOBA</small>";
        beats.forEach((b, x) => b.classList.toggle("on", x === beat));
      } else { pos.innerHTML = "—<small>STOJÍ</small>"; beats.forEach(b => b.classList.remove("on")); }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ---- keys and pedal
  function run(a){
    if(!open) openLooper();
    const m = /^track:(\d)$/.exec(a);
    if(m){ press(+m[1] - 1); return `stopa ${m[1]}`; }
    if(a === "press"){ press(sel); return `stopa ${sel + 1}`; }
    if(a === "next"){ sel = (sel + 1) % N; paint(); return `vybraná stopa ${sel + 1}`; }
    if(a === "undo"){ undo(); return "zpět"; }
    if(a === "play"){ running ? stopAll() : start(false); return running ? "hraje" : "stop"; }
    if(a === "scene:+"){ goScene(Math.min(scenes.length - 1, (pending ? pending.k : cur) + 1)); return "další část"; }
    if(a === "scene:-"){ goScene(Math.max(0, (pending ? pending.k : cur) - 1)); return "předchozí část"; }
    return null;
  }
  document.addEventListener("keydown", (e) => {
    if(!open || e.metaKey || e.ctrlKey || e.altKey) return;
    const tg = e.target;
    if(tg && tg.closest && tg.closest("input:not([type=range]), textarea, select, [contenteditable]")) return;
    if(typeof Dialog !== "undefined" && Dialog.isOpen && Dialog.isOpen()) return;
    let done = true;
    if(/^Digit[1-5]$/.test(e.code)) press(+e.code.slice(5) - 1);
    else if(e.code === "Space") run("play");
    else if(e.code === "Enter") press(sel);
    else if(e.code === "Tab") run("next");
    else if(e.code === "KeyZ") e.shiftKey ? redo() : undo();
    else if(e.code === "ArrowRight") run("scene:+");
    else if(e.code === "ArrowLeft") run("scene:-");
    else if(e.code === "Escape") close();
    else done = false;
    if(done){ e.preventDefault(); e.stopPropagation(); }
  }, true);
  const PED = [["looper:press", "Tlačítko vybrané stopy"], ["looper:next", "Další stopa"], ["looper:undo", "Zpět"], ["looper:play", "Spustit / zastavit"],
    ["looper:scene:+", "Další část"], ["looper:scene:-", "Předchozí část"]];
  function paintPedal(){
    const box = root && root.querySelector("#lp-ped");
    if(!box) return;
    const binds = (typeof Pedal !== "undefined" && Pedal.config) ? Pedal.config().binds || [] : [];
    box.innerHTML = `<span style="align-self:center">🦶 Pedál:</span>` + PED.map(([a, l]) => { const b = binds.filter(x => x.action === a).map(x => x.label);
      return `<button type="button" data-a="${a}" title="Klik a šlápni na spínač — přiřadí se mu tahle akce">${esc(l)}${b.length ? ": " + esc(b.join(", ")) : ""}</button>`; }).join("");
    box.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => {
      if(typeof Pedal === "undefined" || !Pedal.learn){ showToast("MIDI pedál tu není k dispozici"); return; }
      if(!(await Pedal.connect())) return;
      Pedal.learn(b.dataset.a, "single");
      b.textContent = "Šlápni na spínač…";
      Pedal.onLearned(() => paintPedal());
    });
  }

  // ---- open and close: with the song open in Steems, its tempo and parts
  // opts: a song's name, or Loop Studio's plan {song, bpm, perBar, parts: [names in order]}
  function openLooper(opts){
    if(!root) build();
    audio();
    const o = typeof opts === "string" ? {song: opts} : (opts || {});
    const s = o.song || (Mixer.currentSong && Mixer.currentSong()) || null;
    if(s !== song || !scenes.length || o.bpm){
      song = s;
      if(!hasAudio()){
        const g = !o.bpm && s && Mixer.currentSong() === s ? Mixer.deck.grid() : null;
        const bpm = o.bpm || (g && g.bpm), per = o.perBar || (g && g.perBar);
        if(bpm) cfg.bpm = Math.round(bpm * 10) / 10;
        if(per) cfg.perBar = per;
        makeScenes(o.parts);
      }
    }
    try{ Mixer.pause(); }catch(e){}
    root.hidden = false; open = true;
    paint(); paintPedal();
  }
  function close(){ if(root) root.hidden = true; open = false; }
  return {open: openLooper, close, isOpen: () => open, run, press, undo, redo, start, stop: stopAll, goScene,
    state: () => ({running, bpm: cfg.bpm, sel, cur, scenes: scenes.map(s => ({name: s.name, on: [...s.on]})),
      tracks: tracks.map(t => ({name: t.name, src: t.src, layers: t.layers.length, len: t.len, state: ac ? stateOf(t) : "empty"}))}),
    // for the tests: how loud a track's layers are
    _rms: (i) => { const t = tracks[i]; if(!t.layers.length) return 0; const d = t.layers[t.layers.length - 1].buf.getChannelData(0); let q = 0; for(let k = 0; k < d.length; k++) q += d[k] * d[k]; return Math.sqrt(q / d.length); },
    _cut: cut, _srcs: srcs};
})();
