// Pick Jam — the link between the two browsers (the host in Pick, the friend on the guest page).
//
// The room's socket only sets the link up; then all goes peer to peer (WebRTC), over Tailscale:
//   · four fixed lanes, made once by the host, filled and emptied without setting anything up again:
//       0 guitar as a call (Opus tuned for music) · 1 voice (talking) · 2 camera · 3 screen (Pick itself)
//   · data channel "au": the guitar the SonoBus way — 16-bit PCM in 2.7 ms packets, unreliable and unordered,
//     played through our own small jitter buffer (jam-worklet.js) — the least delay a browser can give
//   · data channel "ctl": the clocks (ping), the mode, the sound card's rate and latency, anything else.
// Mode "low" sends the guitar on "au", "rtc" on lane 0, "native" through Pick Jam Engine on each machine (UDP
// straight between the two engines, the addresses swapped on "ctl"; back to "low" when it cannot work);
// both ends follow the one who changed it.
(function(){
  const LANES = ["guitar", "voice", "camera", "screen"];
  const ICE = [{urls: "stun:stun.l.google.com:19302"}];
  // the worklet by the same version as this script (?v=… from the guest page), so no browser keeps an old one
  const VER = (() => { try{ return new URL(document.currentScript.src).search; }catch(e){ return ""; } })();

  // Opus for music: stereo, a high rate, no silence suppression, the shortest packets it takes
  function musicSdp(sdp){
    const m = sdp.match(/a=rtpmap:(\d+) opus\/48000\/2/i); if(!m) return sdp;
    const pt = m[1], want = "minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxaveragebitrate=256000;usedtx=0;cbr=0";
    return sdp.replace(new RegExp(`a=fmtp:${pt} [^\\r\\n]*`), `a=fmtp:${pt} ${want}`);
  }

  // ---- Pick Jam Engine: the native sound path (engine/ in the repo), reached on this machine's control socket.
  // It takes the guitar straight from the sound card (or Pick's own sound from the page), sends it over UDP on the
  // tailnet and plays the other one's guitar straight into the sound card — no browser audio on that path.
  const NATIVE_URL = "ws://127.0.0.1:9877";
  class NativeEngine {
    constructor(on, url){ this.on = on; this.url = url || NATIVE_URL; this.ws = null; this.ok = false; this.hello = null; this.started = null; this.stats = null; this.waiters = []; this.host = ""; }
    // true when an engine answers on this machine (quick: a refused port fails at once)
    probe(ms = 1500){
      if(this.ok && this.ws && this.ws.readyState === 1) return Promise.resolve(true);
      return new Promise((res) => {
        let done = false; const fin = (v) => { if(!done){ done = true; clearTimeout(t); res(v); } };
        const t = setTimeout(() => { try{ ws.close(); }catch(e){} fin(false); }, ms);
        let ws; try{ ws = new WebSocket(this.url); }catch(e){ fin(false); return; }
        ws.binaryType = "arraybuffer";
        ws.onopen = () => ws.send(JSON.stringify({type: "hello", host: this.host}));
        ws.onerror = () => fin(false);
        ws.onclose = () => { if(this.ws === ws){ this.ws = null; this.ok = false; this.started = null; this.on("native-state"); } fin(false); };
        ws.onmessage = (e) => {
          const m = JSON.parse(e.data);
          if(m.type === "hello" && !done){ this.ws = ws; this.ok = true; this.hello = m; this.started = m.running || null; this.on("native-state"); fin(true); }
          this.onMsg(m);
        };
      });
    }
    onMsg(m){
      if(m.type === "stats"){ this.stats = m; return; }
      if(m.type === "started"){ this.started = m; this.stats = null; }
      if(m.type === "stopped"){ this.started = null; this.stats = null; }
      if(m.type === "devices" && this.hello){ this.hello.devices = m.devices; this.on("native-state"); }
      if(m.type === "error" && m.code === "taken"){ this.ok = false; }
      const w = this.waiters.filter(x => x.type === m.type || (m.type === "error" && x.err)); this.waiters = this.waiters.filter(x => !w.includes(x));
      w.forEach(x => (m.type === "error" ? x.rej(new Error(m.msg)) : x.res(m)));
      this.on("native-msg", m);
    }
    cmd(o, want, ms = 8000){
      if(!this.ws || this.ws.readyState !== 1) return Promise.reject(new Error("engine neběží"));
      this.ws.send(JSON.stringify(o));
      if(!want) return Promise.resolve();
      return new Promise((res, rej) => { const w = {type: want, err: true, res, rej}; this.waiters.push(w);
        setTimeout(() => { if(this.waiters.includes(w)){ this.waiters = this.waiters.filter(x => x !== w); rej(new Error("engine neodpověděl")); } }, ms); });
    }
    audio(buf){ if(this.ws && this.ws.readyState === 1 && this.ws.bufferedAmount < 65536) this.ws.send(buf); }
    close(){ const ws = this.ws; this.ws = null; this.ok = false; this.started = null; if(ws){ try{ ws.close(); }catch(e){} } }
  }
  window.JamNative = NativeEngine;

  // ---- the engine's settings in a page (the host's jam panel, the friend's settings): one block, the page's own row look
  const WHY = {
    "engine": "Pick Jam Engine na tomhle počítači neběží — spusť ho a zkus to znovu.",
    "tailscale": "Tailscale na tomhle počítači není připojený — nativní zvuk jde jen přes Tailscale.",
    "peer-engine": "Kamarád nemá spuštěný Pick Jam Engine.",
    "peer-tailscale": "Kamarád nemá připojený Tailscale.",
    "peer-start": "Kamarádovi se engine nepodařilo spustit (zvukovka?).",
    "peer-silent": "Kamarádova stránka nativní zvuk nezná — ať ji obnoví.",
    "udp": "Enginy se přímo nespojily (UDP neprošlo) — zvuk jde zase přes prohlížeč.",
    "engine-lost": "Pick Jam Engine se odpojil.",
  };
  const why = (code) => WHY[code] || (code && code.startsWith("start: ") ? "Engine nespustil zvuk: " + code.slice(7) : "Nativní zvuk nejde: " + code);
  const escH = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  // row(label, html) gives the page's own row markup; onChange(cfg) gets the settings to keep
  function nativeBlock(link, row, onChange, opts = {}){
    const el = document.createElement("div"); el.className = "jam-nat";
    const paint = () => {
      const n = link.native, c = link.natCfg, d = n.hello && n.hello.devices, s = n.stats;
      const khz = (r) => r && r !== 48000 ? ` · ${String(r / 1000).replace(".", ",")} kHz` : "";
      const opt = (list, cur, def) => `<option value="">výchozí${def ? ` (${escH(def)})` : ""}</option>` + (list || []).map(x => `<option value="${escH(x.name)}" ${x.name === cur ? "selected" : ""} ${x.ok ? "" : "disabled"}>${escH(x.name)}${x.ok ? khz(x.rate) : " — nepodporovaný formát"}</option>`).join("");
      const hosts = (d && d.hosts) || [];
      const ins = d ? d.inputs : [], dev = ins.find(x => x.name === (c.input || d.default_input)), chs = dev ? dev.channels : 2;
      let state;
      const dl = (opts.download || []).map(x => `<a class="jb" href="${escH(x.url)}" download>${escH(x.label)}</a>`).join(" ");
      if(!n.ok) state = `<span class="jam-warn">Engine nenalezen</span> <button type="button" class="jb" data-k="retry">Zkusit znovu</button>`
        + (dl ? `<div class="jam-nat-dl">${dl}<br><small>Stáhni, spusť a nech jeho okno otevřené. Windows se poprvé zeptá: „Další informace“ → „Přesto spustit“, a firewall: „Povolit“. Pak „Zkusit znovu“.</small></div>` : "");
      else if(link.mode !== "native") state = `připojen (v${escH(n.hello.version)}${n.hello.ip ? ", adresa " + escH(n.hello.ip) : ", <span class='jam-warn'>bez Tailscale</span>"})`;
      else if(!n.started) state = "spouštím…";
      else if(!link.natPeer) state = "čekám na kamarádův engine…";
      else if(s && s.rx_age_ms != null && s.rx_age_ms < 1000) state = `hraje · buffer ${s.buffer_ms.toFixed(1)} ms · ztráty ${s.lost} · výpadky ${s.underruns}`;
      else state = "spojuji…";
      el.innerHTML = [
        row("Engine", `<span class="jam-nat-st">${state}</span>`),
        hosts.length > 1 || c.host ? row("Ovladač zvuku", `<select data-k="host"><option value="">výchozí (${escH(hosts[0])})</option>${[...new Set(hosts.slice(1).concat(c.host ? [c.host] : []))].map(h => `<option value="${escH(h)}" ${c.host === h ? "selected" : ""}>${escH(h)}${h === "ASIO" ? " (nejnižší zpoždění)" : ""}</option>`).join("")}</select>`) : "",
        d && d.error ? row("", `<span class="jam-warn">${escH(d.error)}</span>`) : "",
        row("Kytara do jamu", `<select data-k="source"><option value="device" ${c.source === "device" ? "selected" : ""}>přímo ze zvukovky (nejrychlejší)</option><option value="pick" ${c.source === "pick" ? "selected" : ""}>${escH(opts.pickLabel || "zvuk z Picku (otisk, efekty)")}</option></select>`),
        c.source === "device" ? row("Vstup", `<select data-k="input">${opt(ins, c.input, d && d.default_input)}</select> <select data-k="in_ch" title="Kanál kytary na zvukovce">${Array.from({length: chs}, (_, i) => `<option value="${i + 1}" ${+c.in_ch === i + 1 ? "selected" : ""}>kanál ${i + 1}</option>`).join("")}</select>`) : "",
        row("Výstup", `<select data-k="output">${opt(d && d.outputs, c.output, d && d.default_output)}</select>`),
        row("Buffer zvukovky", `<select data-k="buffer">${[32, 64, 128, 256].map(b => `<option value="${b}" ${+c.buffer === b ? "selected" : ""}>${b} vzorků = ${(b / 48).toFixed(1).replace(".", ",")} ms</option>`).join("")}</select>`
          + (d && d.host === "WASAPI" ? ` <small title="Ve sdíleném režimu Windows určuje buffer systém (kolem 10 ms). Nejnižší zpoždění dává ASIO.">WASAPI: řídí Windows (~10 ms)</small>` : "")),
      ].join("");
      el.querySelectorAll("select").forEach(sel => sel.onchange = () => {
        const k = sel.dataset.k, v = k === "in_ch" || k === "buffer" ? +sel.value : sel.value;
        const ch = {[k]: v}; if(k === "input") ch.in_ch = 1;
        if(k === "host") Object.assign(ch, {input: "", output: "", in_ch: 1});
        link.setNativeConfig(ch); onChange(Object.assign({}, link.natCfg)); paint();
        if(k === "host") link.native.cmd({type: "devices", host: v}, "devices").then(paint, () => {});
      });
      const r = el.querySelector("[data-k=retry]"); if(r) r.onclick = async () => { await link.native.probe(); paint(); };
    };
    // only the status line moves while playing; selects are rebuilt on engine events, not under the mouse
    const tick = () => { if(!el.isConnected) return clearInterval(t); if(!el.contains(document.activeElement) || document.activeElement.tagName !== "SELECT") paint(); };
    const t = setInterval(tick, 1000);
    link.native.probe().then(paint); paint();
    el.repaint = paint;
    return el;
  }
  window.JamNativeUI = {why, block: nativeBlock};

  class JamLink {
    constructor(o){
      this.role = o.role; this.ctx = o.ctx; this.output = o.output; this.on = o.onEvent || (() => {});
      this.base = o.base || "";                         // where /static/jam-worklet.js is
      this.mode = o.mode || "low"; this.quanta = o.quanta || 3; this.auto = o.auto !== false;
      this.input = null; this.seq = 0; this.ready = false; this.peerOn = false;
      this.local = {camera: null, voice: null, screen: null};
      this.clock = {off: 0, rtt: null, samples: []};
      this.remote = {rate: null, lat: 0, inLat: 0, name: ""};
      this.rxStats = {}; this.txPeak = 0; this.rtcStats = {};
      this.media = {};                                  // remote: lane → MediaStream
      // the native engine: its settings (from the page), my side's state, the other side's address
      this.native = new NativeEngine((k, m) => this.on(k, m), o.nativeUrl);
      this.natCfg = Object.assign({source: "device", host: "", input: "", output: "", in_ch: 1, buffer: 64}, o.native || {});
      this.native.host = this.natCfg.host || "";
      this.natPeer = null; this.natSince = 0; this.natWhy = "";
      this.lastVol = 1; this.lastPan = 0;
    }
    async start(wsUrl){
      const c = this.ctx;
      await c.audioWorklet.addModule(this.base + "/static/jam-worklet.js" + VER);
      this.tx = new AudioWorkletNode(c, "jam-tx", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]});
      const sink = c.createGain(); sink.gain.value = 0; this.tx.connect(sink); sink.connect(c.destination);
      this.tx.port.onmessage = (e) => this.onTx(e.data);
      this.dest = c.createMediaStreamDestination(); this.dest.channelCount = 2;
      this.lowGain = c.createGain(); this.rtcGain = c.createGain(); this.vol = c.createGain();
      // the other one's guitar: volume, pan, mute (the host's fader "Kamarád" in the mix, the friend's own) and a meter
      this.pan = c.createStereoPanner(); this.meter = c.createAnalyser(); this.meter.fftSize = 1024;
      this.lowGain.connect(this.vol); this.rtcGain.connect(this.vol); this.vol.connect(this.pan); this.pan.connect(this.output); this.vol.connect(this.meter);
      this.makeRx();
      this.applyMode();
      if(this.mode === "native") this.natStart();
      this.connectWs(wsUrl);
      this.timer = setInterval(() => this.tick(), 1000);
    }
    makeRx(){
      if(this.rx){ try{ this.rx.disconnect(); }catch(e){} }
      this.rx = new AudioWorkletNode(this.ctx, "jam-rx", {numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [2],
        processorOptions: {quanta: this.quanta, auto: this.auto, remoteRate: this.remote.rate || this.ctx.sampleRate}});
      this.rx.port.onmessage = (e) => { this.rxStats = e.data; };
      this.rx.connect(this.lowGain);
    }
    // what is sent: the guitar (an AudioNode) — Pick's own sound for the host, the input chosen for the friend
    setInput(node){
      if(this.input){ try{ this.input.disconnect(this.tx); }catch(e){} try{ this.input.disconnect(this.dest); }catch(e){} }
      this.input = node || null;
      if(node){ node.connect(this.tx); node.connect(this.dest); }
    }
    setVolume(v){ this.vol.gain.value = v; this.lastVol = v; this.natMix(); }
    setPan(v){ this.pan.pan.value = Math.max(-1, Math.min(1, +v || 0)); this.lastPan = this.pan.pan.value; this.natMix(); }
    level(){
      if(this.mode === "native") return this.native.stats ? this.native.stats.peer_peak || 0 : 0;
      const a = new Float32Array(this.meter.fftSize); this.meter.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); return p;
    }
    setBuffer(quanta, auto){ this.quanta = quanta; this.auto = auto; if(this.rx) this.rx.port.postMessage({quanta, auto}); }
    setMode(m, quiet){
      if(m !== "low" && m !== "rtc" && m !== "native") return;
      const was = this.mode;
      this.mode = m; this.applyMode();
      if(!quiet) this.ctl({type: "mode", mode: m});
      if(m === "native") this.natStart();
      else if(was === "native") this.natStop();
      this.on("mode", m);
    }
    applyMode(){
      const low = this.mode === "low", nat = this.mode === "native", fromPick = nat && this.natCfg.source === "pick";
      // native: the browser sends the guitar only to the engine (Pick's sound), and plays nothing of the friend's guitar
      this.tx.port.postMessage({on: low || fromPick, f32: fromPick});
      this.lowGain.gain.value = low ? 1 : 0; this.rtcGain.gain.value = this.mode === "rtc" ? 1 : 0;
      this.putLane(0, this.mode === "rtc" ? this.dest.stream.getAudioTracks()[0] : null);
      if(low && this.rx) this.rx.port.postMessage({reset: true});
    }

    // ---- the native engine
    setNativeConfig(c){
      const before = JSON.stringify(this.natCfg);
      Object.assign(this.natCfg, c || {}); this.native.host = this.natCfg.host || "";
      if(this.mode === "native" && JSON.stringify(this.natCfg) !== before){ this.applyMode(); this.natStart(); }
    }
    natMix(){ if(this.mode === "native" && this.native.started) this.native.cmd({type: "mix", gain: this.lastVol, pan: this.lastPan}).catch(() => {}); }
    // back to the browser's own path, saying why (both sides follow)
    natFallback(why, quiet){
      this.natWhy = why; this.on("native-fallback", why);
      if(this.mode === "native") this.setMode("low", quiet);
    }
    async natStart(){
      this.natPeer = null; this.natSince = Date.now(); this.natWhy = "";
      const say = (o) => this.ctl(Object.assign({type: "native"}, o));
      if(!(await this.native.probe())){ say({ok: false, why: "engine"}); this.natFallback("engine"); return; }
      const c = this.natCfg;
      try{
        const s = await this.native.cmd({type: "start", source: c.source === "pick" ? "browser" : "device", rate: this.ctx.sampleRate,
          host: c.host || "", input: c.input, output: c.output, in_ch: +c.in_ch || 1, buffer: +c.buffer || 64}, "started", 20000);
        if(!s.ip){ say({ok: false, why: "tailscale"}); this.native.cmd({type: "stop"}).catch(() => {}); this.natFallback("tailscale"); return; }
        this.natMix();
        say({ok: true, ip: s.ip, port: s.port});
        this.natConnect();
      }catch(e){ say({ok: false, why: "start", msg: String(e.message || e)}); this.natFallback("start: " + (e.message || e)); }
    }
    natStop(){ this.natPeer = null; if(this.native.started) this.native.cmd({type: "stop"}).catch(() => {}); }
    // both sides ready: point my engine at the other one's
    natConnect(){
      const p = this.natPeer, me = this.native.started; if(!p || !me) return;
      this.native.cmd({type: "peer", addr: `${p.ip}:${p.port}`}).catch(() => {});
      this.natSince = Date.now(); this.on("native-state");
    }
    onTx(d){
      if(d instanceof ArrayBuffer){
        if(this.mode === "native"){ if(this.natCfg.source === "pick") this.native.audio(d); return; }
        const au = this.au;
        if(this.mode !== "low" || !au || au.readyState !== "open" || au.bufferedAmount > 32768) return;
        const pk = new Uint8Array(8 + d.byteLength); new DataView(pk.buffer).setUint32(0, this.seq++, true); pk.set(new Uint8Array(d), 8);
        try{ au.send(pk.buffer); }catch(e){}
        return;
      }
      if(d && d.peak != null) this.txPeak = d.peak;
    }

    // ---- the room's socket: setting the link up
    connectWs(url){
      this.wsUrl = url;
      const ws = this.ws = new WebSocket(url);
      ws.onmessage = (e) => this.onWs(JSON.parse(e.data));
      ws.onclose = (e) => {
        this.on("ws", false);
        // another window of the same side took the jam over: coming back would throw it out again, and so on every 2 s
        if(e && e.code === 4409){ this.taken = true; this.on("taken", true); return; }
        if(!this.closed) setTimeout(() => { if(!this.closed && !this.taken) this.connectWs(url); }, 2000); };
      ws.onopen = () => this.on("ws", true);
    }
    // the friend's ⟳: a fresh link from scratch (the room, then the call the host makes on seeing him come back)
    reconnect(){
      if(this.closed || !this.wsUrl) return;
      this.taken = false; this.hangup();
      const old = this.ws; if(old){ old.onclose = null; try{ old.close(); }catch(e){} }
      this.on("ws", false); this.connectWs(this.wsUrl);
    }
    send(o){ if(this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(o)); }
    async onWs(m){
      if(m.type === "hello"){ this.peerOn = !!m.peer; this.on("peer", this.peerOn); if(this.peerOn && this.role === "host") this.call(); this.on("msg", m); return; }
      if(m.type === "peer"){ this.peerOn = !!m.on; this.on("peer", this.peerOn);
        if(m.on && this.role === "host") this.call(); if(!m.on) this.hangup(); this.on("msg", m); return; }
      if(m.type === "sdp"){
        if(m.sdp.type === "offer"){ this.newPc(); await this.pc.setRemoteDescription(m.sdp);
          this.pc.getTransceivers().forEach(t => { t.direction = "sendrecv"; });
          this.refill();
          const a = await this.pc.createAnswer(); a.sdp = musicSdp(a.sdp); await this.pc.setLocalDescription(a); this.send({type: "sdp", sdp: this.pc.localDescription}); }
        else if(this.pc) await this.pc.setRemoteDescription(m.sdp);
        (this.pendingIce || []).forEach(c => this.pc.addIceCandidate(c).catch(() => {})); this.pendingIce = [];
        return;
      }
      if(m.type === "ice"){ if(this.pc && this.pc.remoteDescription) this.pc.addIceCandidate(m.cand).catch(() => {}); else (this.pendingIce = this.pendingIce || []).push(m.cand); return; }
      this.on("msg", m);
    }
    newPc(){
      this.hangup();
      const pc = this.pc = new RTCPeerConnection({iceServers: ICE, bundlePolicy: "max-bundle"});
      pc.onicecandidate = (e) => { if(e.candidate) this.send({type: "ice", cand: e.candidate}); };
      pc.onconnectionstatechange = () => { this.on("conn", pc.connectionState); };
      pc.ontrack = (e) => this.onTrack(e);
      this.au = pc.createDataChannel("au", {negotiated: true, id: 0, ordered: false, maxRetransmits: 0});
      this.au.binaryType = "arraybuffer";
      this.au.onmessage = (e) => { if(this.mode === "low" && this.rx) this.rx.port.postMessage(e.data, [e.data]); };
      this.cc = pc.createDataChannel("ctl", {negotiated: true, id: 1});
      this.cc.onopen = () => { this.ready = true; this.readyAt = Date.now(); this.hello(); this.on("ready", true); };
      this.cc.onclose = () => { this.ready = false; this.on("ready", false); };
      this.cc.onmessage = (e) => this.onCtl(JSON.parse(e.data));
    }
    async call(){
      this.newPc();
      LANES.forEach(k => this.pc.addTransceiver(k === "guitar" || k === "voice" ? "audio" : "video", {direction: "sendrecv"}));
      this.refill();
      const o = await this.pc.createOffer(); o.sdp = musicSdp(o.sdp); await this.pc.setLocalDescription(o);
      this.send({type: "sdp", sdp: this.pc.localDescription});
    }
    hangup(){ if(this.pc){ try{ this.pc.close(); }catch(e){} } this.pc = null; this.au = null; this.cc = null; this.ready = false; this.media = {}; this.on("media", {}); }
    lane(i){ return this.pc ? this.pc.getTransceivers()[i] : null; }
    putLane(i, track){
      const t = this.lane(i); if(!t) return;
      t.sender.replaceTrack(track || null).catch(() => {});
      if(track && track.kind === "video"){
        const p = t.sender.getParameters(); p.encodings = p.encodings && p.encodings.length ? p.encodings : [{}];
        p.encodings[0].maxBitrate = i === 3 ? 3000000 : 1500000; if(i === 3) p.degradationPreference = "maintain-resolution";
        t.sender.setParameters(p).catch(() => {});
      }
    }
    refill(){
      this.putLane(0, this.mode === "rtc" ? this.dest.stream.getAudioTracks()[0] : null);
      this.putLane(1, this.local.voice); this.putLane(2, this.local.camera); this.putLane(3, this.local.screen);
    }
    onTrack(e){
      const i = this.pc.getTransceivers().indexOf(e.transceiver), k = LANES[i]; if(!k) return;
      const s = new MediaStream([e.track]);
      if(k === "guitar"){
        try{ e.receiver.jitterBufferTarget = 0; }catch(x){} try{ e.receiver.playoutDelayHint = 0; }catch(x){}
        // Chrome sends a remote stream to Web Audio only while a media element holds it
        const hold = new Audio(); hold.muted = true; hold.srcObject = s; hold.play().catch(() => {});
        if(this.rtcSrc){ try{ this.rtcSrc.disconnect(); }catch(x){} }
        this.rtcSrc = this.ctx.createMediaStreamSource(s); this.rtcSrc.connect(this.rtcGain); this.rtcHold = hold;
      }
      this.media[k] = s;
      e.track.onunmute = () => this.on("media", this.media);
      e.track.onmute = () => this.on("media", this.media);
      this.on("media", this.media);
    }
    // ---- our own camera, voice, screen
    async setCamera(on, deviceId){
      if(this.local.camera){ this.local.camera.stop(); this.local.camera = null; }
      if(on){ const s = await navigator.mediaDevices.getUserMedia({video: {deviceId: deviceId ? {exact: deviceId} : undefined, width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 30}}});
        this.local.camera = s.getVideoTracks()[0]; }
      // said aloud, like the screen: a camera turned off sends no more frames, and the other side would keep its last one
      this.putLane(2, this.local.camera); this.ctl({type: "cam", on: !!this.local.camera}); return this.local.camera;
    }
    async setVoice(on, deviceId){
      if(this.local.voice){ this.local.voice.stop(); this.local.voice = null; }
      if(on){ const s = await navigator.mediaDevices.getUserMedia({audio: {deviceId: deviceId ? {exact: deviceId} : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        this.local.voice = s.getAudioTracks()[0]; }
      this.putLane(1, this.local.voice); return this.local.voice;
    }
    async setScreen(on){
      if(this.local.screen){ this.local.screen.stop(); this.local.screen = null; }
      if(on){ const s = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: {ideal: 15}, width: {ideal: 1920}}, audio: false, preferCurrentTab: true, selfBrowserSurface: "include"});
        const t = this.local.screen = s.getVideoTracks()[0]; t.contentHint = "detail";
        t.onended = () => { if(this.local.screen === t){ this.local.screen = null; this.putLane(3, null); this.ctl({type: "share", on: false}); this.on("screen", false); } }; }
      this.putLane(3, this.local.screen); this.ctl({type: "share", on: !!this.local.screen}); return this.local.screen;
    }

    // ---- the control channel: clocks, mode, the sound cards
    ctl(o){ if(this.cc && this.cc.readyState === "open") this.cc.send(JSON.stringify(o)); }
    hello(){
      const c = this.ctx;
      if(this.local && this.local.screen) setTimeout(() => this.ctl({type: "share", on: true}), 300);
      if(this.local) setTimeout(() => this.ctl({type: "cam", on: !!this.local.camera}), 300);
      const ns = this.native.started;
      if(this.mode === "native" && ns && ns.ip) setTimeout(() => this.ctl({type: "native", ok: true, ip: ns.ip, port: ns.port}), 300);
      this.ctl({type: "hello", rate: c.sampleRate, lat: (c.outputLatency || 0) + (c.baseLatency || 0), inLat: c.baseLatency || 0, mode: this.role === "host" ? this.mode : undefined});
    }
    onCtl(m){
      if(m.type === "ping"){ this.ctl({type: "pong", t: m.t, r: performance.now()}); return; }
      if(m.type === "pong"){
        const now = performance.now(), rtt = now - m.t, off = m.r - (m.t + now) / 2;
        const S = this.clock.samples; S.push({rtt, off}); if(S.length > 12) S.shift();
        const best = S.reduce((a, b) => (b.rtt < a.rtt ? b : a));
        this.clock.rtt = S.reduce((a, b) => a + b.rtt, 0) / S.length; this.clock.off = best.off; this.clock.minRtt = best.rtt;
        return;
      }
      if(m.type === "hello"){
        this.remote.rate = m.rate; this.remote.lat = m.lat || 0; this.remote.inLat = m.inLat || 0;
        if(this.rx) this.rx.port.postMessage({remoteRate: m.rate});
        if(m.mode && this.role === "guest") this.setMode(m.mode, true);
        return;
      }
      if(m.type === "mode"){ this.setMode(m.mode, true); return; }
      if(m.type === "native"){
        if(!m.ok){ this.natFallback("peer-" + (m.why || "?"), true); return; }
        this.natPeer = {ip: m.ip, port: m.port, inMs: m.inMs || 0}; this.natConnect(); return;
      }
      if(m.type === "share"){ this.remoteShare = !!m.on; this.on("media", this.media); return; }
      if(m.type === "cam"){ this.remoteCam = !!m.on; this.on("media", this.media); return; }
      this.on("ctl", m);
    }
    // the friend's performance.now() for one of ours
    remoteNow(t = performance.now()){ return t + this.clock.off; }
    async tick(){
      this.ctl({type: "ping", t: performance.now()});
      // native: nothing coming from the other engine for a while (UDP blocked, engine quit) → back to the browser
      if(this.mode === "native" && Date.now() - this.natSince > 6000){
        if(!this.native.ok) this.natFallback("engine-lost");
        else if(this.natPeer && this.native.started){ const a = this.native.stats && this.native.stats.rx_age_ms; if(a == null || a > 3000) this.natFallback("udp"); }
        else if(!this.natPeer && this.ready && Date.now() - this.readyAt > 6000) this.natFallback("peer-silent");
      }
      if(!this.pc) return;
      try{
        const st = await this.pc.getStats(); const s = {};
        st.forEach(r => {
          if(r.type === "candidate-pair" && r.nominated && r.state === "succeeded"){ s.rtt = r.currentRoundTripTime; s.local = r.localCandidateId; s.remote = r.remoteCandidateId; }
          if(r.type === "inbound-rtp" && r.kind === "audio" && r.jitterBufferEmittedCount){ s.jb = Math.max(s.jb || 0, r.jitterBufferDelay / r.jitterBufferEmittedCount * 1000); }
        });
        if(s.remote){ const rc = st.get(s.remote); if(rc){ s.addr = rc.address || rc.ip; s.ctype = rc.candidateType; } }
        this.rtcStats = s;
      }catch(e){}
    }
    // the friend's guitar into my ears, in ms: their input, the packet, the network, my buffer, my output
    delay(){
      const c = this.ctx, net = (this.clock.minRtt || this.clock.rtt || 0) / 2, outL = ((c.outputLatency || 0) + (c.baseLatency || 0)) * 1000;
      if(this.mode === "native"){
        const s = this.native.stats || {}, nIn = this.natCfg.source === "pick" ? (this.remote.inLat || 0.005) * 1000 : 3;
        const nb = (s.buffer_ms || 0) + (s.frame_ms || 2.5) * 2, no = s.out_lat_ms || 1.5;
        return {total: Math.round(nIn + net + nb + no), net: Math.round(net * 10) / 10, buf: Math.round(nb), out: Math.round(no * 10) / 10, native: true};
      }
      const buf = this.mode === "low" ? (this.rxStats.buffered || 0) + 2.7 : (this.rtcStats.jb || 0) + 20;
      return {total: Math.round((this.remote.inLat || 0.005) * 1000 + net + buf + outL), net: Math.round(net * 10) / 10, buf: Math.round(buf), out: Math.round(outL)};
    }
    // the way the link goes: straight over the tailnet / the home network, or round the internet
    route(){
      if(this.mode === "native" && this.natPeer){ const ip = this.natPeer.ip || ""; return `nativní engine · ${/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(ip) ? "Tailscale" : ip}`; }
      const a = this.rtcStats.addr || "";
      return /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(a) ? "Tailscale (přímo)" : /^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))/.test(a) ? "domácí síť" : a ? (this.rtcStats.ctype === "relay" ? "přes server" : "internet") : "";
    }
    close(){
      this.closed = true; clearInterval(this.timer); this.hangup(); this.natStop(); this.native.close();
      ["camera", "voice", "screen"].forEach(k => { if(this.local[k]) this.local[k].stop(); });
      try{ this.ws.close(); }catch(e){}
      try{ this.tx.disconnect(); this.rx.disconnect(); this.vol.disconnect(); this.pan.disconnect(); }catch(e){}
    }
  }
  window.JamLink = JamLink;
})();
