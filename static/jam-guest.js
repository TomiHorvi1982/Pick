// Pick Jam — the friend's page. Their guitar from the input chosen, their camera, talking when they want;
// the host's screen (Pick), camera and guitar; and the song's backing played here at the very moment it
// plays at the host's (the clocks set by the link), so neither hears the backing late.
(function(){
  const K = new URLSearchParams(location.search).get("k") || "";
  const $ = (id) => document.getElementById(id);
  let ctx = null, link = null, inStream = null, inSrc = null, inSplit = null, inSel = null, inGain = null, monGain = null, meterA = null;
  const BK = {name: null, stems: [], on: {}, st: null, buf: null, src: null, gain: null, startCtx: 0, startPos: 0, rate: 1, loading: 0};
  const store = (k, v) => { try{ if(v === undefined) return JSON.parse(localStorage.getItem("pick-jam-" + k)); localStorage.setItem("pick-jam-" + k, JSON.stringify(v)); }catch(e){ return null; } };

  // the buffer choices: automatic, or so many packets of 2.7 ms
  $("buf").innerHTML = `<option value="auto">automaticky</option>` + [1, 2, 3, 4, 6, 8, 12].map(q => `<option value="${q}">${q} × 2,7 ms = ${(q * 2.67).toFixed(1).replace(".", ",")} ms</option>`).join("");

  async function devices(){
    const ds = await navigator.mediaDevices.enumerateDevices();
    const fill = (sel, kind, want) => { sel.innerHTML = ds.filter(d => d.kind === kind).map(d => `<option value="${d.deviceId}">${d.label || kind}</option>`).join(""); if(want && [...sel.options].some(o => o.value === want)) sel.value = want; };
    fill($("inDev"), "audioinput", store("in")); fill($("voiceDev"), "audioinput", store("voice")); fill($("camDev"), "videoinput", store("cam"));
    // a sound card, not the built-in microphone, for the guitar
    if(!store("in")){ const o = [...$("inDev").options].find(o => !/macbook|built-in|vestav|iphone|airpods|default/i.test(o.textContent)); if(o) $("inDev").value = o.value; }
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
  async function start(){
    $("joinErr").textContent = "";
    try{
      const probe = await navigator.mediaDevices.getUserMedia({audio: true, video: true}); probe.getTracks().forEach(t => t.stop());
    }catch(e){ $("joinErr").textContent = "Bez povolení kamery a mikrofonu to nepůjde — povol je v Chromu (ikona vlevo v adrese)."; return; }
    ctx = new AudioContext({latencyHint: 0});
    await devices();
    inGain = ctx.createGain(); inGain.gain.value = +$("inVol").value;
    monGain = ctx.createGain(); monGain.gain.value = 0; inGain.connect(monGain); monGain.connect(ctx.destination);
    meterA = ctx.createAnalyser(); meterA.fftSize = 512; inGain.connect(meterA);
    BK.gain = ctx.createGain(); BK.gain.gain.value = +$("bkVol").value; BK.gain.connect(ctx.destination);
    link = new JamLink({role: "guest", ctx, output: ctx.destination, onEvent: event});
    await link.start(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?k=${encodeURIComponent(K)}`);
    await useGuitar(); link.setInput(inGain);
    const b = store("buf"); if(b){ $("buf").value = b; applyBuf(); }
    try{ const t = await link.setCamera(true, $("camDev").value); showMe(t); }catch(e){ $("camBtn").classList.remove("on"); }
    $("join").hidden = true;
    setInterval(ui, 250); setInterval(syncBacking, 500);
  }
  function showMe(track){ const v = $("vMe"); if(track){ v.srcObject = new MediaStream([track]); v.hidden = false; } else { v.srcObject = null; v.hidden = true; } }
  function applyBuf(){ const v = $("buf").value; store("buf", v); link.setBuffer(v === "auto" ? 3 : +v, v === "auto"); }

  function event(kind, v){
    if(kind === "peer"){ $("state").textContent = v ? "kamarád je tu — spojuji…" : "čekám na kamaráda (Pick má jam zapnutý?)"; if(!v) stopBacking(); }
    if(kind === "conn"){ $("dot").classList.toggle("on", v === "connected"); if(v === "connected") $("state").textContent = "spojeno"; if(v === "failed") $("state").textContent = "spojení se nepovedlo — zkouším znovu"; }
    if(kind === "media") showMedia(v);
    if(kind === "mode") $("mode").textContent = v === "low" ? "Režim: nízké zpoždění (jako SonoBus) — kytary jdou nekomprimovaně" : "Režim: jako hovor (Opus) — stabilnější na horší síti";
    if(kind === "msg" && v.type === "song") onSong(v);
  }
  function showMedia(m){
    const big = $("vBig"), cam = $("vCam");
    const main = m.screen && m.screen.getVideoTracks()[0] && !m.screen.getVideoTracks()[0].muted ? m.screen : m.camera;
    if(main){ if(big.srcObject !== main) big.srcObject = main; big.hidden = false; $("none").hidden = true; } else { big.hidden = true; $("none").hidden = false; }
    if(m.camera && main !== m.camera){ if(cam.srcObject !== m.camera) cam.srcObject = m.camera; cam.hidden = false; } else cam.hidden = true;
    if(m.voice && $("voice").srcObject !== m.voice) $("voice").srcObject = m.voice;
  }

  // ---- the backing: the host's song, mixed by Pick (the tracks ticked here), played here in step with the host
  function onSong(m){
    BK.st = m;
    if(m.name !== BK.name){
      BK.name = m.name; BK.stems = m.stems || []; BK.buf = null; stopBacking();
      const kept = store("stems-" + m.name) || {}; BK.on = Object.fromEntries(BK.stems.map(s => [s, kept[s] !== false]));
      paintSong(); if(m.name) fetchMix();
    } else paintSong();
    syncBacking();
  }
  const SNAME = {drums: "bicí", bass: "basa", guitar: "kytara", guitar_solo: "kytara sólo", guitar_rhythm: "kytara rytmus", vocals: "zpěv", piano: "klavír", other: "ostatní"};
  function paintSong(){
    const m = BK.st || {};
    $("song").textContent = m.title || m.name || "—";
    $("stems").innerHTML = BK.stems.map(s => `<button data-s="${s}" class="${BK.on[s] ? "on" : ""}">${SNAME[s] || s}</button>`).join("");
    $("stems").querySelectorAll("[data-s]").forEach(b => b.onclick = () => { BK.on[b.dataset.s] = !BK.on[b.dataset.s]; store("stems-" + BK.name, BK.on); paintSong(); fetchMix(); });
  }
  async function fetchMix(){
    const want = BK.stems.filter(s => BK.on[s]), my = ++BK.loading, name = BK.name;
    if(!want.length){ BK.buf = null; stopBacking(); $("songSt").textContent = "podklad vypnutý"; return; }
    $("songSt").textContent = "připravuji podklad…";
    try{
      const r = await fetch(`/mix?k=${encodeURIComponent(K)}&stems=${want.join(",")}`); if(!r.ok) throw new Error();
      const buf = await ctx.decodeAudioData(await r.arrayBuffer());
      if(my !== BK.loading || name !== BK.name) return;
      BK.buf = buf; stopBacking(); syncBacking(true);
    }catch(e){ if(my === BK.loading) $("songSt").textContent = "podklad se nepodařilo načíst"; }
  }
  function stopBacking(){ if(BK.src){ try{ BK.src.stop(); }catch(e){} BK.src = null; } }
  // where the host's song is heard now (s), by the link's clock
  function hostPos(perf){ const m = BK.st; return m.pos + (link.remoteNow(perf) - m.at) / 1000 * (m.speed || 1); }
  function syncBacking(force){
    const m = BK.st;
    if(!m || !link || !link.ready || !link.clock.samples.length){ return; }
    if(!m.playing || !BK.buf){ stopBacking(); if(BK.buf) $("songSt").textContent = m.playing ? "" : "pozastaveno"; return; }
    const ts = ctx.getOutputTimestamp(); if(!ts.performanceTime) return;
    const want = hostPos(ts.performanceTime);
    if(BK.src && !force){
      const mine = BK.startPos + (ts.contextTime - BK.startCtx) * BK.rate, d = (mine - want) * 1000;
      $("songSt").textContent = `hraje u obou · rozdíl ${Math.abs(d) < 1 ? "<1" : Math.round(Math.abs(d))} ms`;
      if(Math.abs(d) < 15 && BK.rate === (m.speed || 1)) return;
    }
    stopBacking();
    const lead = 0.12, at = ts.contextTime + lead, pos = want + lead * (m.speed || 1);
    if(pos >= BK.buf.duration) return;
    const s = ctx.createBufferSource(); s.buffer = BK.buf; s.playbackRate.value = m.speed || 1; s.connect(BK.gain);
    s.start(at, Math.max(0, pos)); BK.src = s; BK.startCtx = at; BK.startPos = pos; BK.rate = m.speed || 1;
    if((m.speed || 1) !== 1) $("songSt").textContent = `tempo ${Math.round(m.speed * 100)} % (u tebe se mění i výška)`;
  }

  function ui(){
    if(!link) return;
    const d = link.delay(), r = link.route();
    $("lat").innerHTML = link.ready ? `${d.total} ms<small>síť ${d.net} · paměť ${d.buf} · výstup ${d.out}</small>` : "";
    $("lat").style.color = d.total <= 30 ? "var(--ok)" : d.total <= 50 ? "var(--warn)" : "var(--bad)";
    $("route").textContent = r ? `Spojení: ${r}${link.rxStats.under ? ` · výpadky ${link.rxStats.under}` : ""}${link.rxStats.lost ? ` · ztracené balíčky ${link.rxStats.lost}` : ""}` : "";
    if(meterA){ const a = new Float32Array(512); meterA.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); $("meter").style.width = Math.min(100, p * 100) + "%"; $("meter").style.background = p > 0.95 ? "var(--bad)" : "var(--ok)"; }
  }

  window.JamGuest = {link: () => link, backing: () => BK, ctx: () => ctx};     // for looking in (tests, the console)
  $("go").onclick = start;
  $("inDev").onchange = $("inCh").onchange = () => useGuitar().catch(() => {});
  $("inVol").oninput = (e) => { if(inGain) inGain.gain.value = +e.target.value; };
  $("frVol").oninput = (e) => link && link.setVolume(+e.target.value);
  $("bkVol").oninput = (e) => { if(BK.gain) BK.gain.gain.value = +e.target.value; };
  $("voVol").oninput = (e) => { $("voice").volume = +e.target.value; };
  $("mon").onchange = (e) => { if(monGain) monGain.gain.value = e.target.checked ? 1 : 0; };
  $("buf").onchange = applyBuf;
  $("camDev").onchange = async () => { store("cam", $("camDev").value); if($("camBtn").classList.contains("on")) showMe(await link.setCamera(true, $("camDev").value)); };
  $("camBtn").onclick = async () => { const on = !$("camBtn").classList.contains("on"); try{ showMe(await link.setCamera(on, $("camDev").value)); $("camBtn").classList.toggle("on", on); }catch(e){} };
  $("voiceBtn").onclick = async () => { const on = !$("voiceBtn").classList.contains("on"); try{ await link.setVoice(on, $("voiceDev").value); $("voiceBtn").classList.toggle("on", on); store("voice", $("voiceDev").value); }catch(e){} };
  $("voiceDev").onchange = async () => { store("voice", $("voiceDev").value); if($("voiceBtn").classList.contains("on")) await link.setVoice(true, $("voiceDev").value); };
})();
