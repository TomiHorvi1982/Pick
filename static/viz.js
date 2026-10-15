// ======================================================================
// Vizualizace — a window that plays behind the music, its size and place the player's.
// A theme is one of three kinds:
//   drawn  — pictures Pick draws itself, moving with what plays (bass, mids, highs);
//   clip   — the song's own clip, muted, kept with the song;
//   videos — the player's own videos, a folder each theme (Vizualizace/ on the card), one after another.
// The sound is only listened to: the mixer's output gets a tap, the player's <audio> is captured —
// nothing on the way to the speakers changes. It draws at most 30 times a second, and not at all closed.
// ======================================================================
const Viz = (() => {
  const KEY = "pick-viz";
  const DRAWN = [["spektrum", "Spektrum"], ["vesmir", "Vesmír"], ["neon", "Neon"], ["ohen", "Oheň"], ["vlny", "Vlny"], ["aurora", "Polární záře"]];
  let cfg = {theme: "spektrum", x: null, y: null, w: 560, h: 340};
  try{ Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); }catch(e){}
  const save = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  let win = null, cv = null, g = null, vids = [], raf = 0, last = 0, lib = {themes: [], jobs: []}, hideT = 0;

  // ---- what plays, and how it sounds right now
  let vctx = null, van = null, vsrc = null, vkey = "";
  function analyser(){
    try{
      if(typeof Mixer !== "undefined" && Mixer.isPlaying && Mixer.isPlaying() && Mixer.vizTap){
        const t = Mixer.vizTap();
        if(t){
          if(!t.node.__vizAn){ const a = t.ctx.createAnalyser(); a.fftSize = 1024; a.smoothingTimeConstant = 0.8; t.node.connect(a); t.node.__vizAn = a; }
          return t.node.__vizAn;
        }
      }
      const el = document.getElementById("pb-audio");
      if(el && !el.paused && el.currentSrc && el.captureStream){
        if(!vctx){
          vctx = new (window.AudioContext || window.webkitAudioContext)();
          van = vctx.createAnalyser(); van.fftSize = 1024; van.smoothingTimeConstant = 0.8;
          const mute = vctx.createGain(); mute.gain.value = 0; van.connect(mute); mute.connect(vctx.destination);
        }
        if(vctx.state === "suspended") vctx.resume();
        if(vkey !== el.currentSrc){
          const st = el.captureStream();
          if(st.getAudioTracks().length){
            if(vsrc) try{ vsrc.disconnect(); }catch(e){}
            vsrc = vctx.createMediaStreamSource(st); vsrc.connect(van); vkey = el.currentSrc;
          }
        }
        return van;
      }
    }catch(e){}
    return null;
  }
  const spec = new Uint8Array(512), wave = new Uint8Array(1024);
  const lv = {bass: 0, mid: 0, high: 0, level: 0, kick: 0, playing: false};
  let bassAvg = 0;
  function listen(){
    const an = analyser();
    lv.playing = !!an;
    if(!an){ spec.fill(0); wave.fill(128); lv.bass *= 0.92; lv.mid *= 0.92; lv.high *= 0.92; lv.level *= 0.92; lv.kick *= 0.85; return; }
    an.getByteFrequencyData(spec); an.getByteTimeDomainData(wave);
    const band = (a, b) => { let s = 0; for(let i = a; i < b; i++) s += spec[i]; return s / (b - a) / 255; };
    const bass = band(1, 8), mid = band(8, 80), high = band(80, 300);
    lv.bass += (bass - lv.bass) * 0.5; lv.mid += (mid - lv.mid) * 0.35; lv.high += (high - lv.high) * 0.35;
    lv.level = (lv.bass + lv.mid + lv.high) / 3;
    lv.kick = Math.max(lv.kick * 0.86, bass > bassAvg * 1.18 + 0.04 ? 1 : 0);
    bassAvg += (bass - bassAvg) * 0.05;
  }

  // ---- the song now, and where in it
  function now(){
    if(typeof Mixer !== "undefined" && Mixer.currentSong && Mixer.currentSong() && (Mixer.isPlaying() || !(Player.getCurrent && Player.getCurrent()))){
      const d = stemsDisplayByName(Mixer.currentSong());
      return {key: "m:" + Mixer.currentSong(), title: d.title, artist: d.artist, videoId: null, at: Mixer.position ? Mixer.position() : 0, playing: Mixer.isPlaying()};
    }
    const t = typeof Player !== "undefined" && Player.getCurrent ? Player.getCurrent() : null, el = document.getElementById("pb-audio");
    if(!t) return null;
    return {key: "p:" + (t.videoId || t.relPath || t.title), title: t.title, artist: t.artists || "", videoId: t.videoId || null,
      at: el ? el.currentTime : 0, playing: !!(el && !el.paused)};
  }

  // ---- the drawn themes
  const P = {stars: [], sparks: [], blobs: [], t: 0};
  function sizeCanvas(){
    const r = Math.min(1.5, window.devicePixelRatio || 1), w = Math.max(10, cv.clientWidth), h = Math.max(10, cv.clientHeight);
    if(cv.width !== Math.round(w * r) || cv.height !== Math.round(h * r)){ cv.width = Math.round(w * r); cv.height = Math.round(h * r); }
    g.setTransform(r, 0, 0, r, 0, 0);
    return [w, h];
  }
  const hue = () => (P.t * 12) % 360;
  const DRAW = {
    spektrum(w, h){
      g.fillStyle = "rgba(6,7,16,.35)"; g.fillRect(0, 0, w, h);
      const cx = w / 2, cy = h / 2, r0 = Math.min(w, h) * (0.16 + lv.bass * 0.08), n = 96;
      g.lineWidth = Math.max(2, Math.min(w, h) / 140); g.lineCap = "round";
      for(let i = 0; i < n; i++){
        const v = spec[Math.floor(2 + i * 2.2)] / 255, a = i / n * Math.PI * 2 - Math.PI / 2, len = r0 * 0.15 + v * Math.min(w, h) * 0.32;
        g.strokeStyle = `hsl(${(hue() + i * 3.75) % 360},90%,${45 + v * 25}%)`;
        g.beginPath(); g.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); g.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len)); g.stroke();
      }
      g.fillStyle = `hsla(${hue()},80%,60%,${0.12 + lv.kick * 0.25})`;
      g.beginPath(); g.arc(cx, cy, r0 * 0.92, 0, Math.PI * 2); g.fill();
    },
    vesmir(w, h){
      g.fillStyle = `rgba(2,3,10,${0.5 - lv.level * 0.3})`; g.fillRect(0, 0, w, h);
      while(P.stars.length < 380) P.stars.push({x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 2, z: Math.random()});
      const cx = w / 2, cy = h / 2, sp = 0.004 + lv.level * 0.03 + lv.kick * 0.02;
      for(const s of P.stars){
        const pz = s.z; s.z -= sp;
        if(s.z <= 0.02){ s.x = (Math.random() - 0.5) * 2; s.y = (Math.random() - 0.5) * 2; s.z = 1; continue; }
        const x = cx + s.x / s.z * cx, y = cy + s.y / s.z * cy, px = cx + s.x / pz * cx, py = cy + s.y / pz * cy;
        if(x < 0 || x > w || y < 0 || y > h){ s.z = 1; continue; }
        const b = 1 - s.z;
        g.strokeStyle = `rgba(${180 + lv.high * 75},${200 + lv.mid * 55},255,${b})`; g.lineWidth = b * 2.4;
        g.beginPath(); g.moveTo(px, py); g.lineTo(x, y); g.stroke();
      }
    },
    neon(w, h){
      const hz = h * 0.58, sky = g.createLinearGradient(0, 0, 0, hz);
      sky.addColorStop(0, "#0b0420"); sky.addColorStop(1, "#3a0b4a");
      g.fillStyle = sky; g.fillRect(0, 0, w, hz);
      const r = Math.min(w, h) * (0.2 + lv.bass * 0.05), sx = w / 2, sy = hz - r * 0.35;
      const sun = g.createLinearGradient(0, sy - r, 0, sy + r); sun.addColorStop(0, "#ffd34d"); sun.addColorStop(1, "#ff2d95");
      g.save(); g.beginPath(); g.arc(sx, sy, r, 0, Math.PI * 2); g.clip(); g.fillStyle = sun; g.fillRect(sx - r, sy - r, r * 2, r * 2);
      g.fillStyle = "#3a0b4a"; for(let i = 0; i < 6; i++){ const y = sy + i * r * 0.16, th = 1 + i * 1.6; g.fillRect(sx - r, y, r * 2, th); } g.restore();
      // the skyline: the spectrum as mountains
      g.fillStyle = "#14051f"; g.beginPath(); g.moveTo(0, hz);
      for(let i = 0; i <= 64; i++){ const v = spec[2 + i * 3] / 255; g.lineTo(i / 64 * w, hz - v * h * 0.22 - 4); }
      g.lineTo(w, hz); g.fill();
      g.fillStyle = "#0a0214"; g.fillRect(0, hz, w, h - hz);
      g.strokeStyle = `rgba(255,45,149,${0.55 + lv.kick * 0.45})`; g.lineWidth = 1.4;
      P.grid = ((P.grid || 0) + 0.006 + lv.level * 0.03) % 1;
      for(let i = 0; i < 14; i++){ const z = (i + P.grid) / 14, y = hz + (h - hz) * z * z; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
      for(let i = -12; i <= 12; i++){ g.beginPath(); g.moveTo(w / 2 + i * w * 0.02, hz); g.lineTo(w / 2 + i * w * 0.16, h); g.stroke(); }
    },
    ohen(w, h){
      g.globalCompositeOperation = "source-over"; g.fillStyle = "rgba(8,2,0,.28)"; g.fillRect(0, 0, w, h);
      const n = 3 + Math.round(lv.level * 22 + lv.kick * 18);
      for(let i = 0; i < n && P.sparks.length < 700; i++)
        P.sparks.push({x: w * (0.1 + Math.random() * 0.8), y: h + 4, vx: (Math.random() - 0.5) * 0.8, vy: -(1.2 + Math.random() * 2.4 + lv.bass * 3), life: 1, r: 3 + Math.random() * 7});
      g.globalCompositeOperation = "lighter";
      P.sparks = P.sparks.filter((s) => {
        s.x += s.vx + Math.sin(P.t * 2 + s.y * 0.02) * 0.6; s.y += s.vy; s.life -= 0.012 + Math.random() * 0.01;
        if(s.life <= 0) return false;
        const k = s.life;
        g.fillStyle = `rgba(255,${Math.round(80 + 150 * k)},${Math.round(30 * k)},${0.35 * k})`;
        g.beginPath(); g.arc(s.x, s.y, s.r * (0.4 + k), 0, Math.PI * 2); g.fill();
        return true;
      });
      g.globalCompositeOperation = "source-over";
    },
    vlny(w, h){
      g.fillStyle = "rgba(4,6,14,.22)"; g.fillRect(0, 0, w, h);
      for(let k = 0; k < 3; k++){
        g.strokeStyle = `hsla(${(hue() + k * 70) % 360},90%,60%,${0.85 - k * 0.22})`; g.lineWidth = 2.6 - k * 0.6;
        g.beginPath();
        for(let i = 0; i < 256; i++){
          const v = (wave[i * 4] - 128) / 128, x = i / 255 * w, y = h / 2 + v * h * (0.32 + k * 0.08) + Math.sin(P.t * (1 + k) + i * 0.05) * h * 0.03 * (k + 1);
          if(i) g.lineTo(x, y); else g.moveTo(x, y);
        }
        g.stroke();
      }
    },
    aurora(w, h){
      g.fillStyle = "rgba(3,6,14,.16)"; g.fillRect(0, 0, w, h);
      while(P.blobs.length < 6) P.blobs.push({a: Math.random() * 6.3, s: 0.15 + Math.random() * 0.25, hue: [140, 170, 200, 280, 120, 320][P.blobs.length], r: 0.3 + Math.random() * 0.3});
      g.globalCompositeOperation = "lighter";
      P.blobs.forEach((b, i) => {
        b.a += b.s * 0.01 * (1 + lv.mid * 3);
        const x = w * (0.5 + Math.cos(b.a + i) * 0.35), y = h * (0.45 + Math.sin(b.a * 1.3) * 0.28), r = Math.min(w, h) * b.r * (0.8 + lv.bass * 0.6);
        const gr = g.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, `hsla(${b.hue},85%,55%,${0.08 + lv.level * 0.18})`); gr.addColorStop(1, "hsla(0,0%,0%,0)");
        g.fillStyle = gr; g.fillRect(x - r, y - r, r * 2, r * 2);
      });
      g.globalCompositeOperation = "source-over";
    },
  };

  // ---- the videos: the player's own (one after another, two players crossfading), or the song's clip
  let order = [], at = 0, clip = {key: "", id: null, asked: ""};
  const kind = () => DRAWN.some(([k]) => k === cfg.theme) ? "drawn" : cfg.theme === "clip" ? "clip" : "videos";
  const folder = () => lib.themes.find((t) => "v:" + t.name === cfg.theme);
  function nextVideo(){
    const f = folder();
    if(!f || !f.videos.length){ vids.forEach((v) => { v.removeAttribute("src"); v.load(); v.style.opacity = 0; }); return false; }
    if(at >= order.length){ order = f.videos.map((_, i) => i).sort(() => Math.random() - 0.5); at = 0; }
    const v = vids[0].style.opacity === "1" ? vids[1] : vids[0], o = v === vids[0] ? vids[1] : vids[0];
    v.src = `/api/viz/video/${encodeURIComponent(f.name)}/${encodeURIComponent(f.videos[order[at++]])}`;
    v.loop = f.videos.length === 1;
    v.oncanplay = () => { v.oncanplay = null; v.play().catch(() => {}); v.style.opacity = 1; o.style.opacity = 0; setTimeout(() => { if(o.style.opacity === "0"){ o.pause(); } }, 900); };
    v.onended = () => nextVideo();
    return true;
  }
  async function clipTick(){
    const n = now(), v = vids[0];
    if(!n){ note("Nic nehraje — pusť písničku."); return; }
    if(clip.key !== n.key){
      clip = {key: n.key, id: n.videoId, asked: ""};
      v.removeAttribute("src"); v.style.opacity = 0;
      if(!clip.id){
        note("Hledám klip písně…");
        try{ const r = await (await fetch(`/api/viz/clip?${new URLSearchParams({title: n.title || "", artist: n.artist || ""})}`)).json(); if(clip.key === n.key) clip.id = r.videoId; }catch(e){}
        if(clip.key !== n.key) return;
        if(!clip.id){ note("Klip téhle písně se nenašel — hraje Polární záře."); return; }
      }
      v.src = `/api/stream-video/${encodeURIComponent(clip.id)}`; v.loop = false; note("Načítám klip…");
      v.oncanplay = () => { v.oncanplay = null; v.style.opacity = 1; note(""); };
    }
    if(!v.src || v.readyState < 2) return;
    if(n.playing){ if(v.paused) v.play().catch(() => {}); } else if(!v.paused) v.pause();
    if(isFinite(v.duration) && n.at < v.duration && Math.abs(v.currentTime - n.at) > 1.5) v.currentTime = n.at;
  }
  function note(t){ const n = win && win.querySelector(".viz-note"); if(n){ n.textContent = t; n.hidden = !t; } }

  // ---- the loop
  function frame(ts){
    raf = requestAnimationFrame(frame);
    if(ts - last < 33 || document.hidden) return;
    const dt = Math.min(0.1, (ts - last) / 1000); last = ts; P.t += dt;
    listen();
    const k = kind();
    const showCanvas = k === "drawn" || (k === "clip" && !(vids[0].style.opacity === "1")) || (k === "videos" && !(folder() || {videos: []}).videos.length);
    cv.style.opacity = showCanvas ? 1 : 0;
    if(showCanvas){
      const [w, h] = sizeCanvas();
      (DRAW[k === "drawn" ? cfg.theme : "aurora"])(w, h);
    }
    if(k === "videos"){
      const n = now();
      vids.forEach((v) => { if(v.style.opacity === "1"){ if(n && !n.playing && !v.paused) v.pause(); else if((!n || n.playing) && v.paused && v.src) v.play().catch(() => {}); } });
      // a little pulse with the beat over the picture
      win.querySelector(".viz-stage").style.setProperty("--pulse", (lv.kick * 0.12).toFixed(3));
    }
  }
  let clipT = 0;
  function startTheme(){
    vids.forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); v.style.opacity = 0; });
    clearInterval(clipT); clip = {key: "", id: null, asked: ""}; note("");
    P.sparks = []; P.blobs = [];
    const k = kind();
    if(k === "videos"){ order = []; at = 0; if(!nextVideo()) note(`Téma ${(folder() || {name: ""}).name} zatím nemá žádné video — přidáš ho přes ⚙︎.`); }
    if(k === "clip"){ clipTick(); clipT = setInterval(clipTick, 1000); }
    paintBar();
  }

  // ---- the window
  function paintBar(){
    const bar = win.querySelector(".viz-themes");
    bar.innerHTML = `<span class="viz-g">Kreslí Pick</span>${DRAWN.map(([k, l]) => `<button type="button" data-th="${k}" class="${cfg.theme === k ? "on" : ""}">${l}</button>`).join("")}
      <span class="viz-g">Video</span><button type="button" data-th="clip" class="${cfg.theme === "clip" ? "on" : ""}" title="Klip hrající písně, bez zvuku a v čase písně">Klip písně</button>
      ${lib.themes.map((t) => `<button type="button" data-th="v:${esc(t.name)}" class="${cfg.theme === "v:" + t.name ? "on" : ""}" title="${t.videos.length} videí ve složce Vizualizace/${esc(t.name)}">${esc(t.name)} <small>${t.videos.length}</small></button>`).join("")}`;
    bar.querySelectorAll("[data-th]").forEach((b) => b.onclick = () => { cfg.theme = b.dataset.th; save(); startTheme(); });
  }
  async function loadLib(){ try{ lib = await (await fetch("/api/viz")).json(); }catch(e){ lib = {themes: [], jobs: []}; } }
  function css(){
    if(document.getElementById("viz-css")) return;
    const s = document.createElement("style"); s.id = "viz-css";
    s.textContent = `
      #viz-win{position:fixed;z-index:900;min-width:240px;min-height:150px;background:#05060d;border:1px solid color-mix(in srgb,var(--accent) 35%,rgba(255,255,255,.12));
        border-radius:14px;overflow:hidden;resize:both;box-shadow:0 20px 60px rgba(0,0,0,.55);display:flex;flex-direction:column}
      #viz-win .viz-stage{position:relative;flex:1;overflow:hidden;--pulse:0}
      #viz-win canvas,#viz-win video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:opacity .8s}
      #viz-win video{transform:scale(calc(1.02 + var(--pulse)));transition:opacity .8s,transform .12s}
      #viz-win .viz-head{position:absolute;left:0;right:0;top:0;z-index:2;display:flex;gap:6px;align-items:flex-start;padding:8px;
        background:linear-gradient(rgba(0,0,0,.7),rgba(0,0,0,0));transition:opacity .3s;cursor:move}
      #viz-win.idle .viz-head{opacity:0}
      #viz-win .viz-themes{flex:1;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
      #viz-win .viz-themes button,#viz-win .viz-btn{border:1px solid rgba(255,255,255,.18);background:rgba(20,22,36,.75);color:var(--text);border-radius:500px;
        font:inherit;font-size:12px;padding:3px 10px;cursor:pointer}
      #viz-win .viz-themes button.on{background:var(--accent);border-color:var(--accent);color:#000}
      #viz-win .viz-themes small{opacity:.65}
      #viz-win .viz-g{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--sub);margin:0 2px 0 6px}
      #viz-win .viz-note{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);z-index:2;background:rgba(0,0,0,.6);color:#fff;font-size:12.5px;padding:5px 12px;border-radius:500px}
      #viz-win:fullscreen{border-radius:0;border:none}
      .viz-mgr .viz-theme{border-top:1px solid rgba(255,255,255,.08);padding:8px 0;display:flex;flex-direction:column;gap:4px}
      .viz-mgr .viz-theme-h{display:flex;gap:6px;align-items:center}.viz-mgr .viz-theme-h b{flex:1}
      .viz-mgr .viz-v{display:flex;gap:8px;font-size:12.5px;color:var(--sub);align-items:center}.viz-mgr .viz-v span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .viz-mgr .cp-x,.viz-mgr .viz-btn{background:none;border:1px solid rgba(255,255,255,.18);color:var(--text);border-radius:500px;font:inherit;font-size:12px;padding:2px 10px;cursor:pointer}
      .viz-mgr .viz-list{max-height:52vh;overflow-y:auto}`;
    document.head.appendChild(s);
  }
  function place(){
    const W = window.innerWidth, H = window.innerHeight;
    cfg.w = Math.min(Math.max(240, cfg.w || 560), W - 20); cfg.h = Math.min(Math.max(150, cfg.h || 340), H - 20);
    if(cfg.x == null || cfg.x > W - 60 || cfg.y > H - 60){ cfg.x = W - cfg.w - 24; cfg.y = 80; }
    Object.assign(win.style, {left: Math.max(0, cfg.x) + "px", top: Math.max(0, cfg.y) + "px", width: cfg.w + "px", height: cfg.h + "px"});
  }
  async function open(){
    if(win){ close(); return; }
    css();
    win = document.createElement("div"); win.id = "viz-win";
    win.innerHTML = `<div class="viz-stage"><canvas></canvas><video muted playsinline preload="auto" style="opacity:0"></video><video muted playsinline preload="auto" style="opacity:0"></video>
      <div class="viz-head"><div class="viz-themes"></div><button type="button" class="viz-btn" data-a="mgr" title="Moje videa: témata, přidat video odkazem, otevřít složku">⚙︎</button>
        <button type="button" class="viz-btn" data-a="full" title="Na celou obrazovku (dvojklik)">⛶</button><button type="button" class="viz-btn" data-a="close" title="Zavřít">✕</button></div>
      <div class="viz-note" hidden></div></div>`;
    document.body.appendChild(win);
    cv = win.querySelector("canvas"); g = cv.getContext("2d"); vids = [...win.querySelectorAll("video")];
    place();
    await loadLib();
    if(cfg.theme.startsWith("v:") && !folder()) cfg.theme = "spektrum";
    startTheme();
    // drag by the bar, size from the corner (remembered)
    const head = win.querySelector(".viz-head");
    head.onmousedown = (e) => {
      if(e.target.closest("button")) return;
      const sx = e.clientX - win.offsetLeft, sy = e.clientY - win.offsetTop;
      const mv = (ev) => { cfg.x = Math.max(0, Math.min(window.innerWidth - 60, ev.clientX - sx)); cfg.y = Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - sy));
        win.style.left = cfg.x + "px"; win.style.top = cfg.y + "px"; };
      const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); save(); };
      document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
    };
    new ResizeObserver(() => { if(!win || document.fullscreenElement === win) return; cfg.w = win.offsetWidth; cfg.h = win.offsetHeight; save(); }).observe(win);
    win.ondblclick = (e) => { if(!e.target.closest("button")) full(); };
    win.onmousemove = () => { win.classList.remove("idle"); clearTimeout(hideT); hideT = setTimeout(() => win && win.classList.add("idle"), 2500); };
    win.onmousemove();
    win.querySelectorAll("[data-a]").forEach((b) => b.onclick = () => ({mgr: manage, full, close})[b.dataset.a]());
    last = 0; raf = requestAnimationFrame(frame);
    document.getElementById("pb-viz-btn")?.classList.add("active");
  }
  function full(){ if(document.fullscreenElement) document.exitFullscreen(); else win.requestFullscreen().catch(() => {}); }
  function close(){
    if(!win) return;
    cancelAnimationFrame(raf); clearInterval(clipT);
    vids.forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); });
    if(document.fullscreenElement === win) document.exitFullscreen();
    win.remove(); win = null;
    if(vctx){ try{ vsrc && vsrc.disconnect(); vctx.close(); }catch(e){} vctx = null; vsrc = null; vkey = ""; }
    document.getElementById("pb-viz-btn")?.classList.remove("active");
  }

  // ---- Moje videa: the themes, a video put in by its link, the folder in Finder
  async function manage(){
    if(document.fullscreenElement) document.exitFullscreen();
    await loadLib();
    const paint = (card) => {
      card.innerHTML = `<div class="pk-dlg-title">Moje videa</div>
        <div class="pk-dlg-text">Každé téma je složka v <b>${esc(lib.folder)}</b>. Video do ní dáš ve Finderu, nebo ho sem stáhneš podle odkazu (jen obraz, hraje se bez zvuku).</div>
        ${lib.card ? "" : `<p class="cp-note bad">Karta s hudbou není připojená.</p>`}
        ${lib.jobs.length ? `<div class="pk-dlg-text">${lib.jobs.map((j) => `${j.status === "running" ? "⏳" : j.status === "done" ? "✓" : "⚠︎"} ${esc(j.theme)}: ${esc(j.file || j.url)} — ${esc(j.note)}`).join("<br>")}</div>` : ""}
        <div class="viz-list">${lib.themes.map((t) => `<div class="viz-theme"><div class="viz-theme-h"><b>${esc(t.name)}</b><small>${t.videos.length} videí</small>
            <button type="button" class="viz-btn" data-fetch="${esc(t.name)}">+ video odkazem…</button><button type="button" class="viz-btn" data-open="${esc(t.name)}">složka</button></div>
            ${t.videos.map((v) => `<div class="viz-v"><span title="${esc(v)}">${esc(v)}</span><button type="button" class="cp-x" data-del="${esc(t.name)}" data-v="${esc(v)}">smazat</button></div>`).join("")}</div>`).join("")}</div>
        <div class="pk-dlg-btns"><button type="button" class="btn secondary" data-new>Nové téma…</button><span class="pk-dlg-note"></span><button type="button" class="btn" data-done>Hotovo</button></div>`;
      card.querySelector("[data-done]").onclick = () => Dialog.close(true);
      card.querySelector("[data-new]").onclick = async () => {
        Dialog.close(true);
        const name = await Dialog.prompt("Název nového tématu:", "", {placeholder: "např. Oceán, Město v noci, Metal koncerty"});
        if(name && name.trim()){ try{ const r = await fetch("/api/viz/theme", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name})});
          const d = await r.json(); if(!r.ok) throw new Error(d.error); }catch(e){ showToast(e.message || "Téma se nepodařilo založit"); } }
        manage();
      };
      card.querySelectorAll("[data-open]").forEach((b) => b.onclick = () => fetch("/api/viz/open", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: b.dataset.open})}));
      card.querySelectorAll("[data-fetch]").forEach((b) => b.onclick = async () => {
        Dialog.close(true);
        const url = await Dialog.prompt(`Odkaz na video pro téma ${b.dataset.fetch} (třeba z YouTube):`, "", {placeholder: "https://…"});
        if(url && url.trim()){
          try{ const r = await fetch("/api/viz/fetch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({theme: b.dataset.fetch, url: url.trim()})});
            const d = await r.json(); if(!r.ok) throw new Error(d.error); showToast("Stahuji video — objeví se v tématu, až bude hotové."); watchJobs(); }
          catch(e){ showToast(e.message || "Stahování se nepodařilo spustit"); }
        }
        manage();
      });
      card.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
        Dialog.close(true);
        if(await Dialog.confirm(`Smazat video „${b.dataset.v}“ z tématu ${b.dataset.del}?`, {ok: "Smazat", danger: true}))
          await fetch(`/api/viz/video/${encodeURIComponent(b.dataset.del)}/${encodeURIComponent(b.dataset.v)}`, {method: "DELETE"});
        manage();
      });
    };
    await Dialog.open((card) => { card.classList.add("wide", "viz-mgr"); paint(card); }, true);
    await loadLib();
    if(win){ paintBar(); if(kind() === "videos" && !vids.some((v) => v.style.opacity === "1")) startTheme(); }
  }
  let jobsT = 0;
  function watchJobs(){
    clearTimeout(jobsT);
    jobsT = setTimeout(async () => {
      const before = lib.jobs.filter((j) => j.status === "running").map((j) => j.id);
      await loadLib();
      lib.jobs.filter((j) => before.includes(j.id) && j.status !== "running").forEach((j) =>
        showToast(j.status === "done" ? `Video je v tématu ${j.theme}.` : `Video se nestáhlo: ${j.note}`));
      if(win) paintBar();
      if(lib.jobs.some((j) => j.status === "running")) watchJobs();
    }, 4000);
  }

  return {open, close, isOpen: () => !!win};
})();
if(document.getElementById("pb-viz-btn")) document.getElementById("pb-viz-btn").onclick = () => Viz.open();
