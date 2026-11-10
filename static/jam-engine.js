// Pick Jam — the link between the two browsers (the host in Pick, the friend on the guest page).
//
// The room's socket only sets the link up; then all goes peer to peer (WebRTC), over Tailscale:
//   · four fixed lanes, made once by the host, filled and emptied without setting anything up again:
//       0 guitar as a call (Opus tuned for music) · 1 voice (talking) · 2 camera · 3 screen (Pick itself)
//   · data channel "au": the guitar the SonoBus way — 16-bit PCM in 2.7 ms packets, unreliable and unordered,
//     played through our own small jitter buffer (jam-worklet.js) — the least delay a browser can give
//   · data channel "ctl": the clocks (ping), the mode, the sound card's rate and latency, anything else.
// Mode "low" sends the guitar on "au", "rtc" on lane 0; both ends follow the one who changed it.
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
    setVolume(v){ this.vol.gain.value = v; }
    setPan(v){ this.pan.pan.value = Math.max(-1, Math.min(1, +v || 0)); }
    level(){ const a = new Float32Array(this.meter.fftSize); this.meter.getFloatTimeDomainData(a); let p = 0; for(const v of a) p = Math.max(p, Math.abs(v)); return p; }
    setBuffer(quanta, auto){ this.quanta = quanta; this.auto = auto; if(this.rx) this.rx.port.postMessage({quanta, auto}); }
    setMode(m, quiet){
      if(m !== "low" && m !== "rtc") return;
      this.mode = m; this.applyMode();
      if(!quiet) this.ctl({type: "mode", mode: m});
      this.on("mode", m);
    }
    applyMode(){
      const low = this.mode === "low";
      this.tx.port.postMessage({on: low});
      this.lowGain.gain.value = low ? 1 : 0; this.rtcGain.gain.value = low ? 0 : 1;
      this.putLane(0, low ? null : this.dest.stream.getAudioTracks()[0]);
      if(low && this.rx) this.rx.port.postMessage({reset: true});
    }
    onTx(d){
      if(d instanceof ArrayBuffer){
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
      ws.onclose = () => { this.on("ws", false); if(!this.closed) setTimeout(() => { if(!this.closed) this.connectWs(url); }, 2000); };
      ws.onopen = () => this.on("ws", true);
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
      this.cc.onopen = () => { this.ready = true; this.hello(); this.on("ready", true); };
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
      this.putLane(2, this.local.camera); return this.local.camera;
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
      if(m.type === "share"){ this.remoteShare = !!m.on; this.on("media", this.media); return; }
      this.on("ctl", m);
    }
    // the friend's performance.now() for one of ours
    remoteNow(t = performance.now()){ return t + this.clock.off; }
    async tick(){
      this.ctl({type: "ping", t: performance.now()});
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
      const buf = this.mode === "low" ? (this.rxStats.buffered || 0) + 2.7 : (this.rtcStats.jb || 0) + 20;
      return {total: Math.round((this.remote.inLat || 0.005) * 1000 + net + buf + outL), net: Math.round(net * 10) / 10, buf: Math.round(buf), out: Math.round(outL)};
    }
    // the way the link goes: straight over the tailnet / the home network, or round the internet
    route(){
      const a = this.rtcStats.addr || "";
      return /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(a) ? "Tailscale (přímo)" : /^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))/.test(a) ? "domácí síť" : a ? (this.rtcStats.ctype === "relay" ? "přes server" : "internet") : "";
    }
    close(){
      this.closed = true; clearInterval(this.timer); this.hangup();
      ["camera", "voice", "screen"].forEach(k => { if(this.local[k]) this.local[k].stop(); });
      try{ this.ws.close(); }catch(e){}
      try{ this.tx.disconnect(); this.rx.disconnect(); this.vol.disconnect(); this.pan.disconnect(); }catch(e){}
    }
  }
  window.JamLink = JamLink;
})();
