// Pick DJ — two decks over the separated songs (Steems), on the whole screen.
//
//   [deck A: song · overview · transport · tempo · stems · cues · loops · FX]
//   [        mixer: EQ · filter · cue · faders · crossfader                 ]
//   [deck B: …]                       [library: songs with stems → A / B]
//
// Each deck is an AudioWorklet (static/worklets/dj-deck.js) that mixes the song's
// stems with their own gains and plays them at any rate, sample-accurately
// (seek, loops, nudge). Key lock inserts a Signalsmith Stretch pitch shifter
// (static/stretch, shared with the looper and Jam) that cancels the pitch change of the
// rate. SYNC matches tempo and beat phase on the server's beat grid
// (/api/stems/{name}/beats). The arithmetic lives in static/dj-core.js.
//
// Output goes where the rest of Pick plays (AudioOut). On an interface with
// four or more outputs the cue (headphones) bus plays on outputs 3–4.
const PickDJ = (() => {
  const C = window.DJCore;
  const SR = 44100;
  const BINS_PER_SECOND = 100;
  const enc = encodeURIComponent;

  let ctx = null, mixBus, masterGain, masterMeter, cueBus, reverb, recordDest = null, recorder = null, recChunks = [];
  let cueOutputs = false;
  let ready = null;
  let root = null, rafId = 0, lastSync = 0;
  let library = [], libraryLoaded = false, libMounts = [], genresPending = 0;
  const libPref = (k, v) => { try { if (v === undefined) return localStorage.getItem("pick-dj-lib-" + k); localStorage.setItem("pick-dj-lib-" + k, v); } catch (e) {} return null; };
  let libSrc = libPref("src") || "all", libGroup = libPref("group") || "", libOpen = new Set();
  let crossfader = 0, masterVolume = 0.9;

  // Where songs, stems and beat grids come from. Pick itself (the default) also
  // separates songs live; the library on the PC (static/dj-library.js) serves
  // only separated songs, as Opus, behind its login.
  let source = {
    // every song with what sorts it (band, tuning, year, genre, playlists) and where it comes from (/api/dj/library)
    async items() {
      const d = await (await fetch("/api/dj/library", {cache: "no-store"})).json();
      libMounts = d.mounts || [];
      genresPending = d.genres_pending || 0;
      return d.items || [];
    },
    mountable: true,
    stemUrl: (name, stem) => `/api/stems/${enc(name)}/${enc(stem)}`,
    beatsUrl: (name) => `/api/stems/${enc(name)}/beats`,
    live: true,
  };

  const decks = ["A", "B"].map((id, i) => ({
    id, index: i, song: null, token: 0, loading: null,
    pos: 0, posTime: 0, playing: false,
    rate: 1, fader: 0, range: 0.08, keyLock: true, keyShift: 0, bend: 0,
    sync: false, cue: 0, cuePreview: false, hot: [null, null, null, null], loop: null, loopBeats: null,
    gains: {}, mutes: {}, eq: {high: 0.5, mid: 0.5, low: 0.5}, filter: 0, volume: 0.85, cueOn: false,
    echo: 0, echoBeats: 0.75, verb: 0,
    peaks: null, overview: null, live: null, stretch: null, stretchOn: false, stretchLatency: 0,
    nodes: null, ui: null,
  }));

  // ------------------------------------------------------------------ audio

  function init() {
    if (ready) return ready;
    ready = (async () => {
      const hint = (typeof AudioPrefs !== "undefined" && AudioPrefs.hint) ? AudioPrefs.hint() : "interactive";
      ctx = new AudioContext({sampleRate: SR, latencyHint: hint});
      if (typeof AudioOut !== "undefined") AudioOut.adopt(ctx);
      await ctx.audioWorklet.addModule("/static/worklets/dj-deck.js");

      mixBus = ctx.createGain();
      masterGain = ctx.createGain();
      masterGain.gain.value = masterVolume;
      masterMeter = ctx.createAnalyser();
      masterMeter.fftSize = 1024;
      cueBus = ctx.createGain();
      reverb = ctx.createConvolver();
      reverb.buffer = impulse(2.8);
      reverb.connect(mixBus);
      mixBus.connect(masterGain);
      masterGain.connect(masterMeter);
      routeOutput();

      decks.forEach(buildDeck);
      setInterval(feedLive, 150);                  // live separation keeps coming with the screen closed too
      if (typeof AudioBus !== "undefined") {
        AudioBus.register("dj", () => decks.forEach(d => setPlaying(d, false)), () => decks.some(d => d.playing));
      }
    })();
    return ready;
  }

  /** Master on outputs 1–2; the cue bus on 3–4 when the device has them. */
  function routeOutput() {
    const dest = ctx.destination;
    masterGain.disconnect();
    masterGain.connect(masterMeter);
    cueOutputs = dest.maxChannelCount >= 4;
    if (!cueOutputs) {
      masterGain.connect(dest);
      return;
    }
    dest.channelCount = 4;
    dest.channelCountMode = "explicit";
    dest.channelInterpretation = "discrete";
    const merger = ctx.createChannelMerger(4);
    const ms = ctx.createChannelSplitter(2), cs = ctx.createChannelSplitter(2);
    masterGain.connect(ms);
    cueBus.connect(cs);
    ms.connect(merger, 0, 0); ms.connect(merger, 1, 1);
    cs.connect(merger, 0, 2); cs.connect(merger, 1, 3);
    merger.connect(dest);
  }

  function impulse(seconds) {
    const len = Math.floor(SR * seconds), buf = ctx.createBuffer(2, len, SR);
    for (let ch = 0; ch < 2; ch++) {
      const data = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
    }
    return buf;
  }

  function buildDeck(d) {
    const n = {};
    n.player = new AudioWorkletNode(ctx, "pick-dj-deck", {numberOfInputs: 0, outputChannelCount: [2]});
    n.player.port.onmessage = (e) => {
      d.pos = e.data.pos; d.posTime = e.data.time;
      if (d.playing && !e.data.playing && !d.cuePreview) { d.playing = false; render(); }
      d.playing = e.data.playing;
    };
    n.pre = ctx.createGain();
    n.high = biquad("highshelf", 3200);
    n.mid = biquad("peaking", 1000, 0.8);
    n.low = biquad("lowshelf", 220);
    n.filter = biquad("lowpass", 22000, 0.9);
    n.fader = ctx.createGain();
    n.xf = ctx.createGain();
    n.meter = ctx.createAnalyser();
    n.meter.fftSize = 512;
    n.cue = ctx.createGain();
    n.cue.gain.value = 0;
    n.echoSend = ctx.createGain();
    n.echoSend.gain.value = 0;
    n.echo = ctx.createDelay(4);
    n.echoFeedback = ctx.createGain();
    n.echoFeedback.gain.value = 0.42;
    n.verbSend = ctx.createGain();
    n.verbSend.gain.value = 0;

    n.player.connect(n.pre);
    n.pre.connect(n.low).connect(n.mid).connect(n.high).connect(n.filter).connect(n.fader);
    n.fader.connect(n.meter);
    n.fader.connect(n.xf).connect(mixBus);
    n.filter.connect(n.cue).connect(cueBus);          // pre-fader listen
    n.xf.connect(n.echoSend).connect(n.echo);
    n.echo.connect(n.echoFeedback).connect(n.echo);
    n.echo.connect(mixBus);
    n.xf.connect(n.verbSend).connect(reverb);
    d.nodes = n;
    applyMixer(d);
  }

  function biquad(type, hz, q) {
    const f = ctx.createBiquadFilter();
    f.type = type;
    f.frequency.value = hz;
    if (q) f.Q.value = q;
    return f;
  }

  const post = (d, msg, transfer) => d.nodes && d.nodes.player.port.postMessage(msg, transfer || []);

  /** Position as heard now: last report extrapolated, minus the key-lock shifter's delay. */
  function heardPos(d) {
    if (!d.song) return 0;
    let p = d.pos;
    if (d.playing && ctx) p += Math.max(0, ctx.currentTime - d.posTime) * (d.rate + d.bend);
    if (d.stretchOn) p -= d.stretchLatency * d.rate;
    return Math.max(0, Math.min(d.song.duration, p));
  }

  function setPlaying(d, on) {
    if (!d.song) return;
    if (on && ctx.state !== "running") ctx.resume();
    if (on && typeof AudioBus !== "undefined") AudioBus.claim("dj");
    d.playing = on;
    d.posTime = ctx.currentTime;
    post(d, {type: "play", on});
    render();
  }

  function seek(d, t) {
    if (!d.song || !Number.isFinite(t)) return;
    t = Math.max(0, Math.min(d.song.duration - 0.05, t));
    d.pos = t; d.posTime = ctx.currentTime;
    post(d, {type: "seek", frame: t * SR});
  }

  async function applyRate(d) {
    d.rate = d.sync ? d.rate : C.rateFor(d.fader, d.range);
    post(d, {type: "rate", value: d.rate});
    const semis = (d.keyLock ? C.keyLockSemitones(d.rate) : 0) + d.keyShift;
    const want = Math.abs(semis) > 0.001;
    if (want && !d.stretch) await makeStretch(d);
    if (want !== d.stretchOn && d.stretch) {
      const n = d.nodes;
      n.player.disconnect();
      if (want) { n.player.connect(d.stretch); }
      else n.player.connect(n.pre);
      d.stretchOn = want;
    }
    if (d.stretch) d.stretch.schedule({semitones: want ? semis : 0});
  }

  async function makeStretch(d) {
    try {
      const mod = await import("/static/stretch/SignalsmithStretch.mjs");
      d.stretch = await mod.default(ctx);
      d.stretchLatency = await d.stretch.latency();   // a Promise in this build (seconds)
      d.stretch.connect(d.nodes.pre);
      d.stretch.start();
    } catch (e) {
      d.stretch = null;
      d.keyLock = false;
      toast("Key lock se nepodařilo spustit");
    }
  }

  function applyMixer(d) {
    if (!d.nodes || !ctx) return;
    const n = d.nodes, now = ctx.currentTime;
    n.high.gain.setTargetAtTime(C.eqDb(d.eq.high), now, 0.01);
    n.mid.gain.setTargetAtTime(C.eqDb(d.eq.mid), now, 0.01);
    n.low.gain.setTargetAtTime(C.eqDb(d.eq.low), now, 0.01);
    const f = C.filterFor(d.filter);
    if (f.type === "off") { n.filter.type = "lowpass"; n.filter.frequency.setTargetAtTime(22000, now, 0.02); }
    else { n.filter.type = f.type; n.filter.frequency.setTargetAtTime(f.hz, now, 0.02); }
    n.fader.gain.setTargetAtTime(d.volume * d.volume, now, 0.01);
    const xf = C.crossfade(crossfader);
    n.xf.gain.setTargetAtTime(d.index === 0 ? xf.a : xf.b, now, 0.01);
    n.cue.gain.setTargetAtTime(d.cueOn && cueOutputs ? 1 : 0, now, 0.01);
    n.echoSend.gain.setTargetAtTime(d.echo * 0.8, now, 0.02);
    n.verbSend.gain.setTargetAtTime(d.verb * 0.7, now, 0.02);
    const beat = d.song ? (C.beatLength(d.song.beats, heardPos(d)) || 0.5) : 0.5;
    n.echo.delayTime.setTargetAtTime(Math.min(3.9, beat * d.echoBeats / d.rate), now, 0.05);
  }

  function applyGains(d) {
    if (!d.song) return;
    const solo = d.song.stems.filter(s => d.mutes[s] === "solo");
    const values = d.song.stems.map(s => {
      if (solo.length) return solo.includes(s) ? d.gains[s] : 0;
      return d.mutes[s] === true ? 0 : d.gains[s];
    });
    post(d, {type: "gains", values});
  }

  // ---------------------------------------------------------------- loading

  /**
   * Loads a library item (C.libraryItems): its Steems stems, or — for a song
   * not separated yet — its mix at once, with the stems arriving window by
   * window while it plays (djlive.py on the server).
   */
  async function load(d, item) {
    await init();
    if (item.tooLong) { toast(`Píseň je delší než ${C.MAX_SECONDS / 60} minut, DJ ji neudrží v paměti.`); return; }
    const token = ++d.token;
    d.loading = item.title;
    setPlaying(d, false);
    render();
    let stemsName = item.stems, live = null;
    try {
      if (!stemsName) {
        if (!source.live) throw new Error("píseň nemá stopy");
        const r = await fetch("/api/dj/live/open", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({rel: item.rel})});
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || r.status);
        if (j.separated) stemsName = j.separated;   // separated in the meantime
        else live = j;
      }
      if (token !== d.token) return;
      if (live) await loadLive(d, item, live, token);
      else await loadStems(d, item, stemsName, token);
    } catch (e) {
      if (token === d.token) { d.loading = null; render(); toast(`Píseň se nenačetla: ${e.message || e}`); }
    }
  }

  async function loadStems(d, item, name, token) {
    const listed = library.find(x => x.stems === name);
    const stems = C.orderStems(listed && listed.stemList ? listed.stemList : C.STEMS);
    const beatsP = fetch(source.beatsUrl(name)).then(r => r.ok ? r.json() : null).catch(() => null);
    const channels = [], present = [];
    let length = Infinity;
    for (const st of stems) {
      const r = await fetch(source.stemUrl(name, st));
      if (r.status === 404) continue;              // a song separated into fewer stems
      if (!r.ok) throw new Error(`stopa ${st} se nenačetla`);
      const audio = await ctx.decodeAudioData(await r.arrayBuffer());
      if (token !== d.token) return;
      const l = toInt16(audio.getChannelData(0));
      const rr = audio.numberOfChannels > 1 ? toInt16(audio.getChannelData(1)) : l.slice();
      channels.push([l, rr]);
      present.push(st);
      length = Math.min(length, audio.length);
    }
    if (!channels.length) throw new Error("píseň nemá stopy");
    const grid = await beatsP;
    if (token !== d.token) return;
    const flat = channels.flat();
    d.peaks = C.peaks(flat, length, Math.max(200, Math.ceil(length / SR * BINS_PER_SECOND)));
    d.live = null;
    start(d, item, name, present, length, grid);
    post(d, {type: "load", stems: channels, length}, flat.map(a => a.buffer));
    await afterLoad(d);
  }

  async function loadLive(d, item, info, token) {
    if (info.rate !== SR) throw new Error("nečekaná vzorkovací frekvence");
    const beatsP = fetch(`/api/dj/live/${info.id}/beats`).then(r => r.ok ? r.json() : null).catch(() => null);
    const r = await fetch(`/api/dj/live/${info.id}/mix`);
    if (!r.ok) throw new Error("mix se nenačetl");
    const mix = wavInt16(await r.arrayBuffer());
    if (token !== d.token) return;
    const length = mix[0].length;
    const stems = info.stems.map(() => [new Int16Array(length), new Int16Array(length)]);
    d.peaks = C.peaks(mix, length, Math.max(200, Math.ceil(length / SR * BINS_PER_SECOND)));
    d.live = {id: info.id, core: info.core, ready: new Uint8Array(info.windows), pending: new Set(), errors: 0};
    start(d, item, info.name, info.stems, length, null);
    post(d, {type: "load", stems, length, mix, windowFrames: info.core, windows: info.windows},
      [...stems.flat(), ...mix].map(a => a.buffer));
    await afterLoad(d);
    // The grid takes beat_this a few seconds on a new song: SYNC and loops snap to it once it is in.
    const grid = await beatsP;
    if (token !== d.token || !grid || !Array.isArray(grid.beats) || grid.beats.length < 2) return;
    d.song.beats = grid.beats;
    d.song.downbeats = Array.isArray(grid.downbeats) ? grid.downbeats : [];
    d.song.bpm = grid.bpm || null;
    render();
  }

  function start(d, item, name, stems, length, grid) {
    d.overview = null;
    d.song = {
      name, title: item.title, artist: item.artist || "",
      stems, duration: length / SR,
      beats: grid && Array.isArray(grid.beats) && grid.beats.length > 1 ? grid.beats : null,
      downbeats: grid && Array.isArray(grid.downbeats) ? grid.downbeats : [],
      bpm: grid && grid.bpm ? grid.bpm : null,
    };
    d.loading = null;
    d.pos = 0; d.posTime = ctx.currentTime;
    d.loop = null; d.loopBeats = null; d.sync = false; d.bend = 0;
    d.gains = Object.fromEntries(stems.map(s => [s, 1]));
    d.mutes = {};
    const saved = readCues(name);
    d.cue = saved.cue || (d.song.beats ? d.song.beats[0] : 0);
    d.hot = saved.hot || [null, null, null, null];
  }

  async function afterLoad(d) {
    post(d, {type: "seek", frame: d.cue * SR});
    d.pos = d.cue;
    applyGains(d);
    await applyRate(d);
    applyMixer(d);
    render();
  }

  /** Keeps the windows of live-separated songs coming: the nearest missing one from the playhead, one at a time per deck. */
  function feedLive() {
    for (const d of decks) {
      const L = d.live;
      if (!L || L.pending.size || L.errors > 5) continue;
      const k = C.nextWindow(L.ready, L.pending, heardPos(d), L.core / SR);
      if (k < 0) continue;
      const token = d.token;
      L.pending.add(k);
      fetch(`/api/dj/live/${L.id}/window/${k}`).then(async r => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
        const start = +r.headers.get("X-DJ-Start"), frames = +r.headers.get("X-DJ-Frames");
        const data = new Int16Array(await r.arrayBuffer());
        if (token !== d.token || d.live !== L) return;
        post(d, {type: "fill", k, start, frames, data}, [data.buffer]);
        L.ready[k] = 1;
        L.errors = 0;
      }).catch(e => {
        if (token !== d.token) return;
        if (++L.errors > 5) toast(`Stopy se nedělí: ${e.message || e}`);
      }).finally(() => { L.pending.delete(k); });
    }
  }

  /** A 16-bit stereo WAV (djlive's mix.wav) as two Int16 channels. */
  function wavInt16(buf) {
    const v = new DataView(buf);
    let pos = 12;
    while (pos + 8 <= v.byteLength) {
      const id = String.fromCharCode(v.getUint8(pos), v.getUint8(pos + 1), v.getUint8(pos + 2), v.getUint8(pos + 3));
      const size = v.getUint32(pos + 4, true);
      if (id === "data") {
        const n = Math.floor(Math.min(size, v.byteLength - pos - 8) / 4);
        const l = new Int16Array(n), r = new Int16Array(n);
        let o = pos + 8;
        for (let i = 0; i < n; i++, o += 4) { l[i] = v.getInt16(o, true); r[i] = v.getInt16(o + 2, true); }
        return [l, r];
      }
      pos += 8 + size + (size & 1);
    }
    throw new Error("mix není WAV");
  }

  function toInt16(f) {
    const out = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) {
      const v = f[i] < -1 ? -1 : f[i] > 1 ? 1 : f[i];
      out[i] = v * 32767;
    }
    return out;
  }

  const cueKey = (name) => "pick-dj-cues:" + name;
  function readCues(name) {
    try { return JSON.parse(localStorage.getItem(cueKey(name)) || "{}"); } catch (e) { return {}; }
  }
  function saveCues(d) {
    if (!d.song) return;
    try { localStorage.setItem(cueKey(d.song.name), JSON.stringify({cue: d.cue, hot: d.hot})); } catch (e) {}
  }

  async function loadLibrary() {
    if (libraryLoaded) return;
    try {
      library = await source.items();
      libraryLoaded = true;
    } catch (e) {
      toast("Knihovnu se nepodařilo načíst");
    }
    renderLibrary();
  }

  // --------------------------------------------------------------- commands

  function quantize(d, t) {
    return d.song && d.song.beats ? C.nearestBeat(d.song.beats, t) : t;
  }

  function cueDown(d) {
    if (!d.song) return;
    const p = heardPos(d);
    if (d.playing) {
      setPlaying(d, false);
      seek(d, d.cue);
      return;
    }
    if (Math.abs(p - d.cue) < 0.03) {
      d.cuePreview = true;
      setPlaying(d, true);
    } else {
      d.cue = quantize(d, p);
      seek(d, d.cue);
      saveCues(d);
    }
    render();
  }

  function cueUp(d) {
    if (!d.cuePreview) return;
    d.cuePreview = false;
    setPlaying(d, false);
    seek(d, d.cue);
  }

  function hotCue(d, i, clear) {
    if (!d.song) return;
    if (clear) { d.hot[i] = null; saveCues(d); render(); return; }
    if (d.hot[i] === null) { d.hot[i] = quantize(d, heardPos(d)); saveCues(d); render(); return; }
    d.cuePreview = false;
    seek(d, d.hot[i]);
    if (!d.playing) setPlaying(d, true);
  }

  function toggleLoop(d, beats) {
    if (!d.song) return;
    if (d.loop && d.loopBeats === beats) { exitLoop(d); return; }
    const fallback = d.song.bpm ? 60 / d.song.bpm : 0.5;
    const l = C.loopAt(d.song.beats, heardPos(d), beats, fallback);
    d.loop = l; d.loopBeats = beats;
    post(d, {type: "loop", loop: {start: l.start * SR, end: l.end * SR}});
    render();
  }

  function exitLoop(d) {
    d.loop = null; d.loopBeats = null;
    post(d, {type: "loop", loop: null});
    render();
  }

  function setBend(d, v) {
    if (!Number.isFinite(v)) v = 0;
    d.bend = v;
    post(d, {type: "bend", value: v});
  }

  function toggleSync(d) {
    const other = decks[1 - d.index];
    if (d.sync) { d.sync = false; setBend(d, 0); applyRate(d); render(); return; }
    if (!d.song || !other.song || !d.song.beats || !other.song.beats) { toast("SYNC potřebuje beat grid u obou písní"); return; }
    other.sync = false;
    d.sync = true;
    const s = C.syncTo(follower(other), follower(d));
    if (s) {
      d.rate = s.rate;
      seek(d, heardPos(d) + s.shift);
      applyRate(d);
    }
    render();
  }

  const follower = (d) => ({beats: d.song.beats, pos: heardPos(d), rate: d.rate + d.bend});

  /** Keeps a synced deck on the other's tempo and beat (gently, by bending the rate). */
  function keepSync(now) {
    if (now - lastSync < 0.1) return;
    lastSync = now;
    for (const d of decks) {
      if (!d.sync || !d.song) continue;
      const other = decks[1 - d.index];
      if (!other.song || !other.song.beats) continue;
      const s = C.syncTo({beats: other.song.beats, pos: heardPos(other), rate: other.rate}, follower(d));
      if (!s) continue;
      if (Math.abs(s.rate - d.rate) > 1e-4) { d.rate = s.rate; applyRate(d); }
      const bend = d.playing && other.playing ? C.clamp(s.shift / 0.6, -0.03, 0.03) : 0;
      if (Math.abs(bend - d.bend) > 1e-4) setBend(d, bend);
    }
  }

  function setPreset(d, name) {
    if (!d.song) return;
    const keep = C.PRESETS[name] || C.STEMS;
    d.song.stems.forEach(s => { d.gains[s] = keep.includes(s) ? 1 : 0; });
    d.mutes = {};
    applyGains(d);
    render();
  }

  function toggleRecording() {
    if (!ctx) return;
    if (recorder) { recorder.stop(); return; }
    if (!recordDest) { recordDest = ctx.createMediaStreamDestination(); masterGain.connect(recordDest); }
    const type = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
    recorder = new MediaRecorder(recordDest.stream, type ? {mimeType: type, audioBitsPerSecond: 256000} : {});
    recChunks = [];
    recorder.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
    recorder.onstop = () => {
      const blob = new Blob(recChunks, {type: recorder.mimeType || "audio/webm"});
      const a = document.createElement("a");
      const stamp = new Date().toISOString().slice(0, 16).replace("T", " ").replace(":", ".");
      a.href = URL.createObjectURL(blob);
      a.download = `Pick DJ mix ${stamp}.webm`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      recorder = null;
      render();
    };
    recorder.start(1000);
    recorder.startedAt = performance.now();
    render();
  }

  // --------------------------------------------------------------------- UI

  const CSS = `
  .pdj{position:fixed;inset:0;z-index:9000;display:flex;flex-direction:column;gap:10px;padding:10px 14px 12px;color:#eef0f4;overflow-y:auto;
    background:radial-gradient(1200px 600px at 20% -10%,rgba(139,108,255,.16),transparent 60%),radial-gradient(900px 500px at 90% 0,rgba(45,212,191,.12),transparent 60%),#0b0d16;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;--dja:#8b6cff;--djb:#2dd4bf;user-select:none;-webkit-user-select:none}
  .pdj[hidden]{display:none}
  .pdj *{box-sizing:border-box}
  .pdj button{font-family:inherit;color:inherit}
  .pdj-top{display:flex;align-items:center;gap:12px;min-height:38px}
  .pdj-logo{font-weight:900;font-size:20px;letter-spacing:.5px;background:linear-gradient(90deg,var(--dja),var(--djb));-webkit-background-clip:text;background-clip:text;color:transparent}
  .pdj-sub{font-size:12px;color:#9aa3b5}
  .pdj-sp{flex:1}
  .pdj-pill{display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);font-size:12px}
  .pdj-btn{border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);border-radius:10px;padding:6px 10px;font-size:12px;font-weight:700;cursor:pointer;transition:background .12s,transform .08s}
  .pdj-btn:hover{background:rgba(255,255,255,.12)}
  .pdj-btn:active{transform:scale(.97)}
  .pdj-btn.on{background:var(--c,#8b6cff);color:#0b0b12;border-color:transparent}
  .pdj-btn:disabled{opacity:.35;cursor:default}
  .pdj-rec.on{background:#f16565;color:#fff}
  .pdj-close{width:34px;height:34px;border-radius:50%;padding:0;font-size:16px}
  .pdj-waves{display:flex;flex-direction:column;gap:4px}
  .pdj-wave{height:64px;border-radius:12px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.06);width:100%;display:block;cursor:grab}
  .pdj-main{display:grid;grid-template-columns:minmax(0,1fr) 250px minmax(0,1fr);gap:10px;flex:0 0 auto}
  .pdj-deck,.pdj-mixer,.pdj-lib{border-radius:16px;background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.02));border:1px solid rgba(255,255,255,.08);padding:12px}
  .pdj-deck{display:flex;flex-direction:column;gap:10px;--c:var(--dja);min-width:0}
  .pdj-deck.b{--c:var(--djb)}
  .pdj-dh{display:flex;align-items:flex-start;gap:10px}
  .pdj-badge{width:30px;height:30px;border-radius:9px;background:var(--c);color:#0b0b12;font-weight:900;display:flex;align-items:center;justify-content:center;flex:0 0 auto}
  .pdj-title{font-weight:800;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pdj-artist{font-size:12px;color:#9aa3b5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pdj-meta{margin-left:auto;text-align:right;font-variant-numeric:tabular-nums}
  .pdj-bpm{font-weight:900;font-size:20px;color:var(--c)}
  .pdj-time{font-size:12px;color:#9aa3b5;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .pdj-ov{height:38px;width:100%;display:block;border-radius:8px;background:rgba(0,0,0,.25);cursor:pointer}
  .pdj-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .pdj-play{width:54px;height:54px;border-radius:50%;border:none;background:var(--c);color:#0b0b12;font-size:20px;cursor:pointer;box-shadow:0 0 0 5px color-mix(in srgb,var(--c) 22%,transparent)}
  .pdj-play.on{box-shadow:0 0 0 5px color-mix(in srgb,var(--c) 45%,transparent),0 0 24px color-mix(in srgb,var(--c) 50%,transparent)}
  .pdj-cue{width:54px;height:54px;border-radius:50%;border:2px solid var(--c);background:transparent;color:var(--c);font-weight:900;cursor:pointer}
  .pdj-lbl{font-size:10px;font-weight:800;letter-spacing:.8px;color:#6f778a;text-transform:uppercase}
  .pdj-tempo{display:flex;align-items:center;gap:8px;flex:1;min-width:200px}
  .pdj-tempo input{flex:1}
  .pdj input[type=range]{accent-color:var(--c,#8b6cff)}
  .pdj-stems{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}
  .pdj-stem{display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px 2px;border-radius:10px;background:rgba(255,255,255,.035)}
  .pdj-stem input{writing-mode:vertical-lr;direction:rtl;height:70px;width:22px}
  .pdj-stem .pdj-btn{padding:3px 6px;font-size:10px}
  .pdj-stem.off{opacity:.4}
  .pdj-stem.na{visibility:hidden}
  .pdj-hot{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;flex:1}
  .pdj-hot .pdj-btn{height:34px}
  .pdj-hot .set{background:color-mix(in srgb,var(--c) 30%,transparent);border-color:var(--c)}
  .pdj-loops{display:flex;gap:4px;flex-wrap:wrap}
  .pdj-loops .pdj-btn{min-width:34px;padding:5px 6px}
  .pdj-mixer{display:flex;flex-direction:column;gap:10px;align-items:stretch}
  .pdj-chs{display:grid;grid-template-columns:1fr 1fr;gap:10px;flex:1}
  .pdj-ch{display:flex;flex-direction:column;align-items:center;gap:8px;--c:var(--dja)}
  .pdj-ch.b{--c:var(--djb)}
  .pdj-knob{width:40px;height:40px;border-radius:50%;background:radial-gradient(circle at 50% 35%,#2b3040,#151823);border:1px solid rgba(255,255,255,.12);position:relative;cursor:ns-resize;touch-action:none}
  .pdj-knob i{position:absolute;left:50%;top:4px;width:3px;height:13px;margin-left:-1.5px;border-radius:2px;background:var(--c);transform-origin:50% 16px}
  .pdj-knob.kill{box-shadow:0 0 0 2px #f16565}
  .pdj-kw{display:flex;flex-direction:column;align-items:center;gap:2px}
  .pdj-fader{display:flex;align-items:flex-end;gap:6px;flex:1}
  .pdj-fader input{writing-mode:vertical-lr;direction:rtl;height:120px;width:26px}
  .pdj-meter{width:6px;height:120px;border-radius:3px;background:rgba(255,255,255,.06);position:relative;overflow:hidden}
  .pdj-meter i{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(0deg,#54d17a,#e8b93f 70%,#f16565)}
  #pdj-mmeter i{right:auto;top:0;height:100%;width:0;background:linear-gradient(90deg,#54d17a,#e8b93f 70%,#f16565)}
  .pdj-xf{display:flex;align-items:center;gap:8px}
  .pdj-xf input{flex:1}
  .pdj-lib{display:flex;flex-direction:column;gap:8px;min-height:240px;flex:1 0 240px}
  .pdj-lib input[type=search]{background:rgba(0,0,0,.3);border:1px solid rgba(255,255,255,.1);border-radius:999px;padding:6px 12px;color:#eef0f4;width:280px}
  .pdj-list{overflow:auto;min-height:0;flex:1}
  .pdj-item{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,220px) 54px 60px 90px;align-items:center;gap:10px;padding:6px 8px;border-radius:8px;font-size:13px;cursor:grab}
  .pdj-item:hover{background:rgba(255,255,255,.06)}
  .pdj-item .t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600}
  .pdj-item .a{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#9aa3b5}
  .pdj-item .d{color:#9aa3b5;text-align:right;font-variant-numeric:tabular-nums}
  .pdj-item .ld{display:flex;gap:4px;justify-content:flex-end}
  .pdj-item .k{font-size:10px;font-weight:800;text-align:center;border-radius:999px;padding:2px 0;background:rgba(255,255,255,.07);color:#9aa3b5}
  .pdj-item .k.live{background:rgba(232,185,63,.16);color:#e8b93f}
  .pdj-item .k.long{color:#f16565}
  .pdj-item .ld button:disabled{opacity:.3;cursor:default}
  .pdj-item .ld button{padding:3px 9px;border-radius:999px;border:none;font-weight:900;font-size:11px;cursor:pointer;color:#0b0b12}
  .pdj-sel{background:rgba(0,0,0,.3);border:1px solid rgba(255,255,255,.1);border-radius:999px;padding:5px 10px;color:#eef0f4;font-size:12px;max-width:220px}
  .pdj-grp{display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:7px 8px;margin-top:2px;border:none;border-radius:8px;background:rgba(255,255,255,.04);color:#eef0f4;font-weight:700;font-size:13px;cursor:pointer}
  .pdj-grp:hover{background:rgba(255,255,255,.09)}
  .pdj-grp .ar{width:12px;color:#9aa3b5}
  .pdj-grp .gn{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .pdj-grp .n{color:#9aa3b5;font-weight:600;font-variant-numeric:tabular-nums}
  .pdj-drop{outline:2px dashed var(--c);outline-offset:-4px}
  .pdj-loading{font-size:12px;color:var(--c)}
  .dj-quick{margin-left:auto;width:34px;height:34px;border-radius:50%;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);
    color:#eef0f4;font-weight:900;font-size:11px;letter-spacing:.3px;cursor:pointer;position:relative}
  .dj-quick:hover{background:rgba(255,255,255,.12)}
  .dj-quick.playing::after{content:"";position:absolute;top:2px;right:2px;width:8px;height:8px;border-radius:50%;background:#2dd4bf;box-shadow:0 0 8px #2dd4bf}
  .dj-quick + .jam-quick{margin-left:6px}
  body.lib-collapsed .dj-quick{display:none}
  `;

  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== false && v !== null && v !== undefined) e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : String(k));
    return e;
  }

  /** A rotary knob: vertical drag turns it, double click resets. */
  function knob(value, onChange, opts) {
    const o = Object.assign({min: 0, max: 1, def: 0.5, label: ""}, opts);
    const dot = h("i");
    const k = h("div", {class: "pdj-knob", title: o.title || o.label});
    k.append(dot);
    const set = (v) => {
      v = C.clamp(v, o.min, o.max);
      k.value = v;
      dot.style.transform = `rotate(${-135 + 270 * (v - o.min) / (o.max - o.min)}deg)`;
      k.classList.toggle("kill", !!o.kill && v <= o.min + 0.02);
    };
    set(value);
    k.addEventListener("pointerdown", (e) => {
      k.setPointerCapture(e.pointerId);
      const y0 = e.clientY, v0 = k.value;
      const move = (ev) => { set(v0 + (y0 - ev.clientY) / 160 * (o.max - o.min)); onChange(k.value); };
      const up = () => { k.removeEventListener("pointermove", move); k.removeEventListener("pointerup", up); };
      k.addEventListener("pointermove", move);
      k.addEventListener("pointerup", up);
    });
    k.addEventListener("dblclick", () => { set(o.def); onChange(k.value); });
    k.addEventListener("wheel", (e) => { e.preventDefault(); set(k.value - Math.sign(e.deltaY) * 0.02 * (o.max - o.min)); onChange(k.value); }, {passive: false});
    k.set = set;
    return h("div", {class: "pdj-kw"}, k, o.label ? h("span", {class: "pdj-lbl"}, o.label) : null);
  }

  function range(min, max, step, value, onInput, attrs) {
    const r = h("input", Object.assign({type: "range", min, max, step, value}, attrs || {}));
    r.addEventListener("input", () => onInput(parseFloat(r.value)));
    return r;
  }

  function build() {
    if (root) return;
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.append(style);
    root = h("div", {class: "pdj", hidden: true});
    document.body.append(root);

    const top = h("div", {class: "pdj-top"},
      h("span", {class: "pdj-logo"}, "Pick DJ"),
      h("span", {class: "pdj-sub", id: "pdj-status"}, "Dvě písně, šest stop, jeden mix"),
      h("span", {class: "pdj-sp"}),
      h("span", {class: "pdj-pill"}, h("span", {class: "pdj-lbl"}, "Master"),
        range(0, 1, 0.01, masterVolume, (v) => { masterVolume = v; if (masterGain) masterGain.gain.setTargetAtTime(v, ctx.currentTime, 0.01); }),
        h("span", {class: "pdj-meter", style: "height:14px;width:60px", id: "pdj-mmeter"}, h("i"))),
      h("button", {class: "pdj-btn pdj-rec", id: "pdj-rec", onclick: toggleRecording, title: "Nahrát mix (uloží se jako soubor)"}, "● REC"),
      h("button", {class: "pdj-btn pdj-close", onclick: close, title: "Zavřít (Esc)"}, "✕"));

    const waves = h("div", {class: "pdj-waves"}, decks.map(d => {
      const c = h("canvas", {class: "pdj-wave"});
      d.waveCanvas = c;
      scrub(c, d);
      return c;
    }));

    // Both decks first: the mixer's channel strips hang their controls on the decks' ui.
    const deckA = deckView(decks[0]), deckB = deckView(decks[1]);
    const main = h("div", {class: "pdj-main"}, deckA, mixerView(), deckB);
    const lib = libraryView();
    root.append(top, waves, main, lib);
    document.addEventListener("keydown", (e) => {
      if (root.hidden || e.target.matches("input[type=search],input[type=text],textarea")) return;
      if (e.key === "Escape") close();
    });
  }

  /** Dragging a waveform moves the song under the playhead. */
  function scrub(canvas, d) {
    canvas.addEventListener("pointerdown", (e) => {
      if (!d.song) return;
      canvas.setPointerCapture(e.pointerId);
      let x0 = e.clientX, t0 = heardPos(d);
      const wasPlaying = d.playing;
      if (wasPlaying) setPlaying(d, false);
      const move = (ev) => { const sec = (x0 - ev.clientX) / canvas.clientWidth * 8; seek(d, t0 + sec); };
      const up = () => {
        canvas.removeEventListener("pointermove", move); canvas.removeEventListener("pointerup", up);
        if (wasPlaying) setPlaying(d, true);
      };
      canvas.addEventListener("pointermove", move);
      canvas.addEventListener("pointerup", up);
    });
  }

  function deckView(d) {
    const ui = d.ui = {};
    ui.title = h("div", {class: "pdj-title"}, "Přetáhni sem píseň");
    ui.artist = h("div", {class: "pdj-artist"}, "nebo ji načti z knihovny tlačítkem " + d.id);
    ui.bpm = h("div", {class: "pdj-bpm"}, "—");
    ui.time = h("div", {class: "pdj-time"}, "--:--");
    ui.loading = h("div", {class: "pdj-loading"});
    ui.overview = h("canvas", {class: "pdj-ov"});
    ui.overview.addEventListener("pointerdown", (e) => {
      if (!d.song) return;
      const r = ui.overview.getBoundingClientRect();
      seek(d, (e.clientX - r.left) / r.width * d.song.duration);
    });

    ui.play = h("button", {class: "pdj-play", title: "Přehrát / pauza", onclick: () => { d.cuePreview = false; setPlaying(d, !d.playing); }}, "▶");
    ui.cue = h("button", {class: "pdj-cue", title: "CUE: při hraní skok na cue a stop; při pauze nastaví cue, podržením přehraje od něj"}, "CUE");
    ui.cue.addEventListener("pointerdown", () => cueDown(d));
    ui.cue.addEventListener("pointerup", () => cueUp(d));
    ui.cue.addEventListener("pointerleave", () => cueUp(d));
    ui.sync = h("button", {class: "pdj-btn", title: "Srovnat tempo a doby s druhým deckem", onclick: () => toggleSync(d)}, "SYNC");
    ui.keylock = h("button", {class: "pdj-btn", title: "Key lock: tempo beze změny tóniny", onclick: () => { d.keyLock = !d.keyLock; applyRate(d); render(); }}, "KEY");
    ui.keyDown = h("button", {class: "pdj-btn", title: "Tónina o půltón níž", onclick: () => { d.keyShift = Math.max(-6, d.keyShift - 1); applyRate(d); render(); }}, "♭");
    ui.key = h("span", {class: "pdj-time"}, "0");
    ui.keyUp = h("button", {class: "pdj-btn", title: "Tónina o půltón výš", onclick: () => { d.keyShift = Math.min(6, d.keyShift + 1); applyRate(d); render(); }}, "♯");
    const nudge = (sign) => {
      const b = h("button", {class: "pdj-btn", title: sign < 0 ? "Přibrzdit (podržet)" : "Popohnat (podržet)"}, sign < 0 ? "‹" : "›");
      b.addEventListener("pointerdown", () => setBend(d, sign * 0.04));
      const off = () => setBend(d, 0);
      b.addEventListener("pointerup", off); b.addEventListener("pointerleave", off);
      return b;
    };
    ui.tempo = range(-1, 1, 0.001, 0, (v) => { d.fader = v; d.sync = false; applyRate(d); render(); }, {title: "Tempo (dvojklik = 0)"});
    ui.tempo.addEventListener("dblclick", () => { d.fader = 0; ui.tempo.value = 0; d.sync = false; applyRate(d); render(); });
    ui.rangeBtn = h("button", {class: "pdj-btn", title: "Rozsah tempa", onclick: () => {
      d.range = d.range === 0.08 ? 0.16 : d.range === 0.16 ? 0.5 : 0.08; applyRate(d); render(); }}, "±8%");
    ui.rate = h("span", {class: "pdj-time", style: "min-width:54px;text-align:right"}, "+0.0%");

    ui.stems = {};
    const stemsBox = h("div", {class: "pdj-stems"}, C.STEMS.map(s => {
      const slider = range(0, 1, 0.01, 1, (v) => { d.gains[s] = v; applyGains(d); render(); }, {title: C.STEM_TITLES[s]});
      const mute = h("button", {class: "pdj-btn", title: "Klik = ztlumit, dvojklik = sólo",
        onclick: () => { d.mutes[s] = d.mutes[s] ? false : true; applyGains(d); render(); },
        ondblclick: () => { d.mutes[s] = d.mutes[s] === "solo" ? false : "solo"; applyGains(d); render(); }}, C.STEM_TITLES[s]);
      const box = h("div", {class: "pdj-stem"}, slider, mute);
      ui.stems[s] = {box, slider, mute};
      return box;
    }));
    const presets = h("div", {class: "pdj-row"}, h("span", {class: "pdj-lbl"}, "Stopy"),
      ...[["acapella", "Acapella"], ["instrumental", "Instrumentál"], ["beat", "Beat"], ["all", "Vše"]].map(([k, t]) =>
        h("button", {class: "pdj-btn", onclick: () => setPreset(d, k)}, t)));

    ui.hot = [0, 1, 2, 3].map(i => {
      const b = h("button", {class: "pdj-btn", title: "Hot cue: klik nastaví / skočí, pravé tlačítko smaže"}, String(i + 1));
      b.addEventListener("click", () => hotCue(d, i, false));
      b.addEventListener("contextmenu", (e) => { e.preventDefault(); hotCue(d, i, true); });
      return b;
    });
    ui.loops = {};
    const loops = h("div", {class: "pdj-loops"}, [0.5, 1, 2, 4, 8, 16].map(n => {
      const b = h("button", {class: "pdj-btn", title: `Smyčka ${n === 0.5 ? "½" : n} ${n > 1 ? "dob" : "doby"}`, onclick: () => toggleLoop(d, n)}, n === 0.5 ? "½" : String(n));
      ui.loops[n] = b;
      return b;
    }), h("button", {class: "pdj-btn", title: "Zrušit smyčku", onclick: () => exitLoop(d)}, "⟲ off"));

    ui.echoBeats = h("button", {class: "pdj-btn", title: "Délka echa v dobách", onclick: () => {
      const opts = [0.25, 0.5, 0.75, 1, 2];
      d.echoBeats = opts[(opts.indexOf(d.echoBeats) + 1) % opts.length]; applyMixer(d); render(); }}, "¾");
    const fx = h("div", {class: "pdj-row"},
      h("span", {class: "pdj-lbl"}, "FX"),
      knob(0, (v) => { d.echo = v; applyMixer(d); }, {def: 0, label: "Echo"}), ui.echoBeats,
      knob(0, (v) => { d.verb = v; applyMixer(d); }, {def: 0, label: "Reverb"}));

    const view = h("section", {class: "pdj-deck" + (d.index ? " b" : "")},
      h("div", {class: "pdj-dh"}, h("div", {class: "pdj-badge"}, d.id),
        h("div", {style: "min-width:0"}, ui.title, ui.artist, ui.loading),
        h("div", {class: "pdj-meta"}, ui.bpm, ui.time)),
      ui.overview,
      h("div", {class: "pdj-row"}, ui.cue, ui.play, ui.sync, ui.keylock, ui.keyDown, ui.key, ui.keyUp),
      h("div", {class: "pdj-tempo"}, h("span", {class: "pdj-lbl"}, "Tempo"), nudge(-1), ui.tempo, nudge(1), ui.rate, ui.rangeBtn),
      presets, stemsBox,
      h("div", {class: "pdj-row"}, h("span", {class: "pdj-lbl"}, "Cue"), h("div", {class: "pdj-hot"}, ui.hot)),
      h("div", {class: "pdj-row"}, h("span", {class: "pdj-lbl"}, "Smyčka"), loops),
      fx);
    dropTarget(view, d);
    return view;
  }

  function mixerView() {
    const ch = (d) => {
      const ui = d.ui;
      ui.meter = h("i");
      ui.cueBtn = h("button", {class: "pdj-btn", title: "Odposlech do sluchátek (výstupy 3–4)", onclick: () => { d.cueOn = !d.cueOn; applyMixer(d); render(); }}, "🎧");
      ui.volume = range(0, 1, 0.01, d.volume, (v) => { d.volume = v; applyMixer(d); }, {title: "Hlasitost kanálu"});
      return h("div", {class: "pdj-ch" + (d.index ? " b" : "")},
        h("div", {class: "pdj-badge"}, d.id),
        knob(0.5, (v) => { d.eq.high = v; applyMixer(d); }, {label: "High", kill: true}),
        knob(0.5, (v) => { d.eq.mid = v; applyMixer(d); }, {label: "Mid", kill: true}),
        knob(0.5, (v) => { d.eq.low = v; applyMixer(d); }, {label: "Low", kill: true}),
        knob(0, (v) => { d.filter = v; applyMixer(d); }, {min: -1, max: 1, def: 0, label: "Filtr", title: "Doleva low-pass, doprava high-pass"}),
        ui.cueBtn,
        h("div", {class: "pdj-fader"}, ui.volume, h("span", {class: "pdj-meter"}, ui.meter)));
    };
    const xf = range(-1, 1, 0.01, 0, (v) => { crossfader = v; decks.forEach(applyMixer); }, {title: "Crossfader (dvojklik = střed)"});
    xf.addEventListener("dblclick", () => { xf.value = 0; crossfader = 0; decks.forEach(applyMixer); });
    return h("section", {class: "pdj-mixer"},
      h("div", {class: "pdj-chs"}, ch(decks[0]), ch(decks[1])),
      h("div", {class: "pdj-xf"}, h("span", {class: "pdj-lbl"}, "A"), xf, h("span", {class: "pdj-lbl"}, "B")));
  }

  function dropTarget(view, d) {
    view.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-pick-stems")) { e.preventDefault(); view.classList.add("pdj-drop"); } });
    view.addEventListener("dragleave", () => view.classList.remove("pdj-drop"));
    view.addEventListener("drop", (e) => {
      view.classList.remove("pdj-drop");
      const key = e.dataTransfer.getData("text/x-pick-stems");
      const row = library.find(r => r.key === key);
      if (row) { e.preventDefault(); load(d, row); }
    });
  }

  let libSearch = null, libList = null, libSrcSel = null, libGroupSel = null;
  const NOTE = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
  /** A tuning (MIDI notes of the strings) as a guitarist says it: "E standard", "Drop D", "C# standard · 7 strun". */
  function tuneName(t) {
    if (!Array.isArray(t) || t.length < 4) return "";
    const a = [...t].sort((x, y) => x - y), iv = a.slice(1).map((v, i) => v - a[i]).join(","), nn = (m) => NOTE[((m % 12) + 12) % 12];
    if (iv === "5,5,5,4,5") return `${nn(a[0])} standard`;
    if (iv === "7,5,5,4,5") return `Drop ${nn(a[0])}`;
    if (iv === "5,5,5,5,4,5") return `${nn(a[0])} standard · 7 strun`;
    if (iv === "7,5,5,5,4,5") return `Drop ${nn(a[0])} · 7 strun`;
    return a.map(nn).join(" ");
  }
  const GROUPS = {
    "": ["Bez třídění", null],
    band: ["Kapela", (r) => [r.band || r.artist || "Neznámý interpret"]],
    tuning: ["Ladění", (r) => [tuneName(r.tuning) || "Ladění nezjištěné"]],
    year: ["Rok vydání", (r) => [r.year ? String(r.year) : "Rok neznámý"]],
    decade: ["Desetiletí", (r) => [r.year ? `${Math.floor(r.year / 10) * 10}s` : "Rok neznámý"]],
    genre: ["Žánr", (r) => [(r.genres && r.genres[0]) || "Žánr neznámý"]],
    playlist: ["Playlist", (r) => (r.playlists && r.playlists.length ? r.playlists : ["Mimo playlisty"])],
  };
  const UNKNOWN = /^(Neznámý|Ladění nezjištěné|Rok neznámý|Žánr neznámý|Mimo playlisty)/;
  function groupOrder(g, list) {                          // [name, rows] in the order a DJ looks for them
    if (libGroup === "year" || libGroup === "decade") return list.sort((a, b) => (UNKNOWN.test(a[0]) - UNKNOWN.test(b[0])) || b[0].localeCompare(a[0]));
    if (libGroup === "tuning" || libGroup === "genre" || libGroup === "playlist") return list.sort((a, b) => (UNKNOWN.test(a[0]) - UNKNOWN.test(b[0])) || b[1].length - a[1].length);
    return list.sort((a, b) => (UNKNOWN.test(a[0]) - UNKNOWN.test(b[0])) || a[0].localeCompare(b[0], "cs"));
  }
  function sourcesOf() {
    const n = (f) => library.filter(f).length;
    const out = [["all", `Vše (${library.length})`], ["stems", `Se stopami (${n(r => !r.live)})`]];
    if (library.some(r => r.source === "songs")) out.push(["songs", `Songs a playlisty (${n(r => r.source === "songs")})`]);
    for (const m of libMounts) out.push(["@" + m.name, `📁 ${m.name} (${m.exists ? m.count : "nedostupná"})`]);
    if (source.mountable) out.push(["+", "＋ Namapovat složku…"]);
    return out;
  }
  function fillSources() {
    if (!libSrcSel) return;
    const opts = sourcesOf();
    if (!opts.some(o => o[0] === libSrc)) libSrc = "all";
    libSrcSel.replaceChildren(...opts.map(([v, t]) => h("option", {value: v, selected: v === libSrc}, t)));
    libSrcSel.hidden = opts.length < 3 && !source.mountable;
  }
  async function mapFolder() {
    toast("Vyber složku s hudbou — nic se nekopíruje");
    try {
      const r = await fetch("/api/library/mounts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.error || "Složku se nepodařilo namapovat"); return; }
      if (!d.added) return;
      libSrc = "@" + d.added.name; libPref("src", libSrc);
      libraryLoaded = false; library = []; renderLibrary(); await loadLibrary();
      toast(`Složka ${d.added.name} je v DJ i v knihovně. Písně bez stop se rozdělí během hraní.`);
    } catch (e) { toast("Složku se nepodařilo namapovat"); }
  }
  function libraryView() {
    libSearch = h("input", {type: "search", placeholder: "Hledat interpreta, píseň, album, žánr"});
    libSearch.addEventListener("input", renderLibrary);
    libSrcSel = h("select", {class: "pdj-sel", title: "Odkud písně jsou"});
    libSrcSel.addEventListener("change", () => {
      if (libSrcSel.value === "+") { fillSources(); mapFolder(); return; }
      libSrc = libSrcSel.value; libPref("src", libSrc); renderLibrary();
    });
    libGroupSel = h("select", {class: "pdj-sel", title: "Třídit do skupin"},
      ...Object.entries(GROUPS).map(([k, [t]]) => h("option", {value: k, selected: k === libGroup}, k ? "Třídit: " + t : t)));
    libGroupSel.addEventListener("change", () => { libGroup = libGroupSel.value; libPref("group", libGroup); libOpen = new Set(); renderLibrary(); });
    libList = h("div", {class: "pdj-list"});
    return h("section", {class: "pdj-lib"},
      h("div", {class: "pdj-row"}, h("span", {class: "pdj-lbl"}, "Písně"), libSrcSel, libGroupSel, libSearch,
        h("span", {class: "pdj-sub", id: "pdj-libcount"})),
      libList);
  }

  function renderLibrary() {
    if (!libList) return;
    fillSources();
    const q = (libSearch.value || "").trim().toLowerCase();
    const inSrc = (r) => libSrc === "all" || (libSrc === "stems" ? !r.live : r.source === libSrc);
    const rows = library.filter(r => inSrc(r) && (!q || [r.title, r.artist, r.album, (r.genres || []).join(" "), (r.playlists || []).join(" "), r.year]
      .some(x => x && String(x).toLowerCase().includes(q))));
    const count = root.querySelector("#pdj-libcount");
    if (count) count.textContent = libraryLoaded ? `${rows.length} z ${library.length}` + (libGroup === "genre" && genresPending ? ` · žánry ${genresPending} kapel se dohledávají` : "") : "načítám…";
    const by = GROUPS[libGroup] && GROUPS[libGroup][1];
    if (!by) { libList.replaceChildren(...rows.slice(0, 300).map(itemOf)); return; }
    const m = new Map();
    for (const r of rows) for (const g of by(r)) { if (!m.has(g)) m.set(g, []); m.get(g).push(r); }
    const auto = q && rows.length <= 60;
    const kids = [];
    for (const [g, list] of groupOrder(libGroup, [...m])) {
      const open = auto || libOpen.has(g);
      kids.push(h("button", {class: "pdj-grp", onclick: () => { libOpen.has(g) ? libOpen.delete(g) : libOpen.add(g); renderLibrary(); }},
        h("span", {class: "ar"}, open ? "▾" : "▸"), h("span", {class: "gn"}, g), h("span", {class: "n"}, String(list.length))));
      if (open) kids.push(...list.slice(0, 300).map(itemOf));
    }
    libList.replaceChildren(...kids);
  }

  function itemOf(r) {
    {
      const kind = r.tooLong ? h("span", {class: "k long", title: `Delší než ${C.MAX_SECONDS / 60} minut`}, "dlouhá")
        : r.live ? h("span", {class: "k live", title: "Stopy se rozdělí během hraní (a pak uloží do Steems)"}, "živě")
        : h("span", {class: "k", title: "Stopy ze Steems"}, "stopy");
      const item = h("div", {class: "pdj-item", draggable: r.tooLong ? "false" : "true"},
        h("span", {class: "t"}, r.title),
        h("span", {class: "a"}, r.artist),
        kind,
        h("span", {class: "d"}, r.duration ? C.formatTime(r.duration).replace(/\.\d$/, "") : ""),
        h("span", {class: "ld"}, decks.map(d => h("button", {style: `background:var(--dj${d.id.toLowerCase()})`, title: `Načíst do decku ${d.id}`,
          disabled: r.tooLong, onclick: () => load(d, r)}, "→ " + d.id))));
      item.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/x-pick-stems", r.key));
      const tip = [r.album, r.year, tuneName(r.tuning), (r.genres || []).slice(0, 3).join(", "), (r.playlists || []).length ? "playlist: " + r.playlists.join(", ") : ""].filter(Boolean).join(" · ");
      if (tip) item.title = tip;
      return item;
    }
  }

  /** Updates the controls' state (not the per-frame parts, see frame()). */
  function render() {
    if (!root) return;
    for (const d of decks) {
      const ui = d.ui, s = d.song;
      ui.title.textContent = s ? s.title : "Přetáhni sem píseň";
      ui.artist.textContent = s ? s.artist : "nebo ji načti z knihovny tlačítkem → " + d.id;
      ui.loading.textContent = d.loading ? `Načítám ${d.loading}…` : liveText(d) || (s && !s.beats ? "bez beat gridu: SYNC a smyčky podle odhadu" : "");
      ui.play.textContent = d.playing ? "❚❚" : "▶";
      ui.play.classList.toggle("on", d.playing);
      ui.sync.classList.toggle("on", d.sync);
      ui.keylock.classList.toggle("on", d.keyLock);
      ui.key.textContent = (d.keyShift > 0 ? "+" : "") + d.keyShift;
      ui.rangeBtn.textContent = "±" + Math.round(d.range * 100) + "%";
      ui.tempo.value = d.sync ? C.clamp((d.rate - 1) / d.range, -1, 1) : d.fader;
      ui.hot.forEach((b, i) => b.classList.toggle("set", d.hot[i] !== null));
      for (const [n, b] of Object.entries(ui.loops)) b.classList.toggle("on", d.loopBeats === parseFloat(n));
      ui.echoBeats.textContent = {0.25: "¼", 0.5: "½", 0.75: "¾", 1: "1", 2: "2"}[d.echoBeats];
      for (const st of C.STEMS) {
        const x = ui.stems[st];
        const has = s && s.stems.includes(st);
        x.box.classList.toggle("na", !has);
        if (!has) continue;
        x.slider.value = d.gains[st];
        x.mute.classList.toggle("on", d.mutes[st] === "solo");
        x.box.classList.toggle("off", d.mutes[st] === true || d.gains[st] === 0);
      }
      if (ui.cueBtn) {
        ui.cueBtn.classList.toggle("on", d.cueOn);
        ui.cueBtn.disabled = !cueOutputs;
        ui.cueBtn.title = cueOutputs ? "Odposlech do sluchátek (výstupy 3–4)" : "Odposlech potřebuje zvukovou kartu se 4 a více výstupy";
      }
    }
    const rec = root.querySelector("#pdj-rec");
    rec.classList.toggle("on", !!recorder);
    const btn = document.getElementById("dj-quick");
    if (btn) btn.classList.toggle("playing", decks.some(d => d.playing));
  }

  /** "Stopy 12/40 · beat grid…" while a song is separated live; empty when there is nothing to say. */
  function liveText(d) {
    const L = d.live;
    if (!L || !d.song) return "";
    const done = L.ready.reduce((a, b) => a + b, 0), all = L.ready.length;
    const parts = [];
    if (done < all) parts.push(`stopy se dělí: ${done}/${all} ${L.errors > 5 ? "(chyba)" : ""}`.trim());
    if (!d.song.beats) parts.push("hledám beaty…");
    return parts.join(" · ");
  }

  function frame() {
    rafId = 0;
    if (root.hidden) return;
    const now = ctx ? ctx.currentTime : 0;
    keepSync(now);
    for (const d of decks) {
      const p = heardPos(d), s = d.song;
      if (s) {
        const bpm = s.beats ? C.localBpm(s.beats, p) : s.bpm;
        d.ui.bpm.textContent = bpm ? (bpm * (d.rate + d.bend)).toFixed(1) : "—";
        d.ui.time.textContent = `${C.formatTime(p)} / -${C.formatTime(s.duration - p).replace(/^-/, "")}`;
        const pct = (d.rate - 1) * 100;
        d.ui.rate.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%";
      }
      drawWave(d, p);
      drawOverview(d, p);
      if (d.live) d.ui.loading.textContent = liveText(d) || (d.song && !d.song.beats ? "bez beat gridu" : "");
      if (d.nodes) d.ui.meter.style.height = (level(d.nodes.meter) * 100).toFixed(1) + "%";
    }
    if (masterMeter) root.querySelector("#pdj-mmeter i").style.width = (level(masterMeter) * 100).toFixed(1) + "%";
    if (recorder) root.querySelector("#pdj-rec").textContent = "● " + C.formatTime((performance.now() - recorder.startedAt) / 1000).replace(/\.\d$/, "");
    else root.querySelector("#pdj-rec").textContent = "● REC";
    rafId = requestAnimationFrame(frame);
  }

  const meterBuf = new Float32Array(1024);
  function level(an) {
    const n = an.fftSize;
    an.getFloatTimeDomainData(meterBuf.subarray(0, n));
    let m = 0;
    for (let i = 0; i < n; i++) { const v = Math.abs(meterBuf[i]); if (v > m) m = v; }
    return Math.min(1, m);
  }

  function fit(c) {
    const dpr = window.devicePixelRatio || 1, w = Math.round(c.clientWidth * dpr), hh = Math.round(c.clientHeight * dpr);
    if (c.width !== w || c.height !== hh) { c.width = w; c.height = hh; }
    return dpr;
  }

  function color(d, alpha) {
    return d.index === 0 ? `rgba(139,108,255,${alpha})` : `rgba(45,212,191,${alpha})`;
  }

  /** The scrolling waveform: 8 s around the playhead, beat lines, cues and the loop. */
  function drawWave(d, p) {
    const c = d.waveCanvas, dpr = fit(c), g = c.getContext("2d"), W = c.width, H = c.height;
    g.clearRect(0, 0, W, H);
    g.fillStyle = "rgba(255,255,255,.35)";
    g.font = `${11 * dpr}px -apple-system, sans-serif`;
    g.fillText(d.id, 8 * dpr, 16 * dpr);
    if (!d.song || !d.peaks) return;
    const span = 8, t0 = p - span / 2, px = W / span;
    const x = (t) => (t - t0) * px;
    if (d.loop) {
      g.fillStyle = color(d, 0.13);
      g.fillRect(x(d.loop.start), 0, (d.loop.end - d.loop.start) * px, H);
    }
    g.fillStyle = color(d, 0.85);
    const bins = d.peaks.length, perPx = BINS_PER_SECOND / px;
    for (let xx = 0; xx < W; xx += 2) {
      const b = Math.floor((t0 + xx / px) * BINS_PER_SECOND);
      if (b < 0 || b >= bins) continue;
      let m = 0;
      for (let k = 0; k < Math.max(1, perPx * 2); k++) m = Math.max(m, d.peaks[Math.min(bins - 1, b + k)] || 0);
      const hh = m * (H * 0.9);
      g.fillRect(xx, (H - hh) / 2, 1.5, hh);
    }
    const beats = d.song.beats;
    if (beats) {
      const down = new Set(d.song.downbeats.map(v => v.toFixed(3)));
      const b0 = Math.ceil(C.beatAt(beats, t0)), b1 = Math.floor(C.beatAt(beats, t0 + span));
      for (let b = b0; b <= b1; b++) {
        const t = C.timeOfBeat(beats, b);
        const bar = down.has(t.toFixed(3));
        g.fillStyle = bar ? "rgba(255,255,255,.55)" : "rgba(255,255,255,.18)";
        g.fillRect(x(t), bar ? 0 : H * 0.78, Math.max(1, dpr), bar ? H : H * 0.22);
      }
    }
    g.fillStyle = "#e8b93f";
    g.fillRect(x(d.cue) - dpr, 0, 2 * dpr, H);
    d.hot.forEach((t, i) => {
      if (t === null) return;
      g.fillStyle = ["#f16565", "#54d17a", "#3d8bfd", "#e9b949"][i];
      g.fillRect(x(t) - dpr, 0, 2 * dpr, H * 0.3);
    });
    g.fillStyle = "#fff";
    g.fillRect(W / 2 - dpr, 0, 2 * dpr, H);
  }

  function drawOverview(d, p) {
    const c = d.ui.overview, dpr = fit(c), g = c.getContext("2d"), W = c.width, H = c.height;
    g.clearRect(0, 0, W, H);
    if (!d.song || !d.peaks) return;
    if (!d.overview || d.overview.length !== W) {
      const o = new Float32Array(W), per = d.peaks.length / W;
      for (let i = 0; i < W; i++) {
        let m = 0;
        for (let k = Math.floor(i * per); k < Math.floor((i + 1) * per); k++) m = Math.max(m, d.peaks[k]);
        o[i] = m;
      }
      d.overview = o;
    }
    const played = p / d.song.duration * W;
    for (let i = 0; i < W; i++) {
      g.fillStyle = i < played ? color(d, 0.35) : color(d, 0.85);
      const hh = d.overview[i] * H * 0.9;
      g.fillRect(i, (H - hh) / 2, 1, hh);
    }
    if (d.loop) { g.fillStyle = color(d, 0.25); g.fillRect(d.loop.start / d.song.duration * W, 0, (d.loop.end - d.loop.start) / d.song.duration * W, H); }
    g.fillStyle = "#e8b93f";
    g.fillRect(d.cue / d.song.duration * W - dpr, 0, 2 * dpr, H);
    if (d.live) {                                  // which parts already have stems
      const n = d.live.ready.length, wpx = W / n;
      for (let k = 0; k < n; k++) {
        g.fillStyle = d.live.ready[k] ? color(d, 0.95) : "rgba(255,255,255,.12)";
        g.fillRect(k * wpx, H - 3 * dpr, Math.max(1, wpx - dpr), 3 * dpr);
      }
    }
    g.fillStyle = "#fff";
    g.fillRect(played - dpr, 0, 2 * dpr, H);
  }

  let toastEl = null, toastTimer = 0;
  function toast(msg) {
    if (typeof showToast === "function") { showToast(msg); return; }
    // A page without Pick's toasts (the library on the PC): a small one of our own, over the DJ screen.
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9100;background:#1e222d;color:#eef0f4;" +
        "border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:10px 16px;font:600 13px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;" +
        "box-shadow:0 10px 30px rgba(0,0,0,.4);max-width:90vw";
      document.body.append(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 4000);
  }

  // ------------------------------------------------------------- open/close

  async function open() {
    build();
    root.hidden = false;
    render();
    renderLibrary();
    loadLibrary();
    try {
      await init();
      render();
    } catch (e) {
      toast("Zvuk pro DJ se nepodařilo spustit: " + (e.message || e));
    }
    if (!rafId) rafId = requestAnimationFrame(frame);
  }

  function close() {
    if (!root) return;
    root.hidden = true;
    render();
  }

  /** The DJ button, put before `anchor` (Pick: before the Jam button in the library's head). */
  function mountButton(anchor) {
    if (!anchor || document.getElementById("dj-quick")) return null;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dj-quick";
    b.id = "dj-quick";
    b.title = "Pick DJ: dva decky se stopami";
    b.textContent = "DJ";
    b.addEventListener("click", open);
    anchor.before(b);
    return b;
  }
  const autoMount = () => mountButton(document.getElementById("jam-quick"));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount);
  else autoMount();

  /** Another page using the DJ (the library on the PC) says where its songs come from. */
  function setSource(s) {
    source = Object.assign({}, source, s);
    library = []; libraryLoaded = false;
    if (root) renderLibrary();
  }

  return {open, close, setSource, mountButton, load: (deck, row) => load(decks[deck === "B" || deck === 1 ? 1 : 0], row),
    state: () => decks.map(d => ({song: d.song && d.song.name, playing: d.playing, pos: heardPos(d), rate: d.rate, sync: d.sync, loop: d.loop,
      beats: !!(d.song && d.song.beats), live: d.live ? {windows: d.live.ready.length, ready: d.live.ready.reduce((x, y) => x + y, 0)} : null}))};
})();
