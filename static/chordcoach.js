// The chord helper on the whole screen (Steems → celá obrazovka → Akordy):
//
//   ──────────────┃───────────────────────────    the song's chords running past a still line
//   [Am]  [F]   [ C ]   [G]  [Am]                 the one sounding big in the middle, the ones around it small
//   Ladění kytary ▾   🎹 MIDI klávesy   🎸 Hlídat kytaru
//   [ the keys: what is held, and is it the chord ]  [ the neck: what the guitar plays, and is it the chord ]
//
// A chord clicked opens big: its shape on the neck (other shapes too), the keys, and both heard — a guitar
// strummed, a piano. The chords sound where the song sounds: the key knob and a tuning followed move them,
// and the shapes are for the guitar's own tuning (the one in the tuner, or one picked here).
//
// The keys: a MIDI keyboard (Web MIDI), its notes on keys drawn here, and whether they make the chord.
// The guitar: Pick's guitar input listened to (its clean signal, before the amp) — the notes read from the
// spectrum, the chord they make named, and both put on a neck drawn here, next to the shape to play.
const ChordCoach = (() => {
  const CSS = `
  .cc{margin:8px 14px 0;display:flex;flex-direction:column;gap:10px;--cc-acc:var(--accent,#8b5cf6)}
  .cc.off{display:none}
  /* the chords running past: a still line, the song moving under it */
  .cc-lane{position:relative;height:66px;border-radius:14px;overflow:hidden;cursor:pointer;user-select:none;-webkit-user-select:none;
    background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.07);
    -webkit-mask-image:linear-gradient(90deg,transparent,#000 5%,#000 92%,transparent);mask-image:linear-gradient(90deg,transparent,#000 5%,#000 92%,transparent)}
  .cc-track{position:absolute;inset:0 auto 0 0;width:1px;will-change:transform}
  .cc-bar{position:absolute;top:0;bottom:0;border-left:1px solid rgba(255,255,255,.16)}
  .cc-bar i{position:absolute;left:4px;top:3px;font-style:normal;font-size:9.5px;color:#6f6f86;font-variant-numeric:tabular-nums}
  .cc-bt{position:absolute;bottom:4px;width:1px;height:7px;background:rgba(255,255,255,.18)}
  .cc-sec{position:absolute;top:2px;font-size:9.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--c);white-space:nowrap;margin-left:22px}
  .cc-pill{position:absolute;top:19px;height:32px;border-radius:10px;display:flex;align-items:center;padding:0 10px;box-sizing:border-box;overflow:hidden;
    background:linear-gradient(180deg,hsl(var(--h) 55% 30% / .85),hsl(var(--h) 55% 22% / .85));border:1px solid hsl(var(--h) 70% 60% / .45);
    color:#fff;transition:opacity .25s,filter .25s,box-shadow .25s}
  .cc-pill span{font-weight:800;font-size:16px;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.4)}
  .cc-pill.past{opacity:.35;filter:saturate(.4)}
  .cc-pill.now{background:linear-gradient(180deg,hsl(var(--h) 75% 58%),hsl(var(--h) 70% 44%));border-color:hsl(var(--h) 90% 75%);
    box-shadow:0 0 22px hsl(var(--h) 85% 55% / .55)}
  .cc-head{position:absolute;top:0;bottom:0;width:3px;margin-left:-1.5px;border-radius:2px;background:#fff;pointer-events:none;
    box-shadow:0 0 12px var(--cc-acc),0 0 3px #fff}
  .cc-head::before{content:"";position:absolute;left:50%;top:-1px;transform:translateX(-50%);border:6px solid transparent;border-top-color:#fff}
  .cc-head.beat{animation:cc-beat .28s ease-out}
  .cc-head.down{animation:cc-down .38s ease-out}
  @keyframes cc-beat{from{box-shadow:0 0 22px var(--cc-acc),0 0 6px #fff}to{box-shadow:0 0 12px var(--cc-acc),0 0 3px #fff}}
  @keyframes cc-down{from{box-shadow:0 0 34px var(--cc-acc),0 0 12px #fff;width:5px}to{box-shadow:0 0 12px var(--cc-acc),0 0 3px #fff;width:3px}}
  .cc-msg{font-size:12px;color:#8a8aa0;padding:8px 2px;text-align:center}
  .cc-msg button{margin-left:8px;font:inherit;font-size:12px;padding:5px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);color:#fff;cursor:pointer}
  /* the carousel: the chord sounding in the middle, big; the ones before and after, small */
  .cc-car{position:relative;height:calc(var(--ch,236px) + 14px);overflow:hidden}
  .cc-card{position:absolute;left:50%;top:calc(50% + 7px);display:flex;flex-direction:column;align-items:center;gap:6px;padding:10px 14px 12px;border-radius:18px;
    cursor:zoom-in;box-sizing:border-box;width:var(--cw,190px);
    background:linear-gradient(165deg,#20202e,#14141c);border:1px solid rgba(255,255,255,.08);box-shadow:0 10px 28px rgba(0,0,0,.45);
    transition:transform .5s cubic-bezier(.2,.8,.2,1),opacity .5s,box-shadow .3s,border-color .3s}
  .cc-card::before{content:"";position:absolute;inset:0 0 auto;height:4px;border-radius:18px 18px 0 0;background:hsl(var(--h) 75% 58%)}
  .cc-card.now{border-color:hsl(var(--h) 80% 65% / .9);box-shadow:0 0 0 1px hsl(var(--h) 80% 65% / .5),0 14px 44px hsl(var(--h) 80% 40% / .45)}
  .cc-card.gone{opacity:0!important;pointer-events:none}
  .cc-cn{font-weight:900;font-size:26px;line-height:1;color:#fff;letter-spacing:-.01em}
  .cc-tag{position:absolute;top:-10px;left:50%;transform:translateX(-50%);white-space:nowrap;font-size:10px;font-weight:800;letter-spacing:.12em;color:#0b0b12;
    background:hsl(var(--h) 80% 65%);padding:3px 8px;border-radius:7px;box-shadow:0 4px 12px rgba(0,0,0,.4)}
  .cc-tag.next{background:#2a2a3a;color:#e6e6f2;border:1px solid hsl(var(--h) 70% 60% / .6)}
  .cc-prog{position:absolute;left:14px;right:14px;bottom:5px;height:3px;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}
  .cc-prog i{display:block;height:100%;width:0;background:hsl(var(--h) 80% 65%)}
  .cc-q{display:flex;flex-direction:column;align-items:center;gap:6px}
  .cc[data-view="both"] .cc-q{flex-direction:column}
  /* the shapes: a neck in a card, the keys under it */
  .cc-gtr{width:104px;height:132px;overflow:visible}
  .cc-gtr .gfb{fill:#2a1c14}.cc-gtr .gf{stroke:#8c8c98;stroke-width:1.2}.cc-gtr .gs{stroke:#d9d6cf;opacity:.85}
  .cc-gtr .gn{fill:#f1ecdf}.cc-gtr .gb{fill:#cfcfe0;font:700 10px system-ui,sans-serif;text-anchor:end}
  .cc-gtr .gx{fill:#8d8da6;font:700 11px system-ui,sans-serif;text-anchor:middle}.cc-gtr .go{fill:none;stroke:#d7d7e6;stroke-width:1.5}
  .cc-gtr .gd{fill:#f4f4fa}.cc-gtr .gd.root{fill:hsl(var(--h) 80% 62%)}
  .cc-gtr .gt{fill:#15151d;font:800 9px system-ui,sans-serif;text-anchor:middle}
  .cc-gtr .gl{fill:#8a8aa0;font:700 8px system-ui,sans-serif;text-anchor:middle}
  .cc-gtr .gbar{fill:#f4f4fa}
  .cc-pno{width:156px;height:54px}
  .cc-pno .pw{fill:#ecedf3;stroke:#55556a;stroke-width:.8}.cc-pno .pb{fill:#15151c;stroke:#000;stroke-width:.6}
  .cc-pno .pw.on{fill:hsl(var(--h) 75% 78%)}.cc-pno .pw.root{fill:hsl(var(--h) 80% 60%)}
  .cc-pno .pb.on{fill:hsl(var(--h) 60% 50%)}.cc-pno .pb.root{fill:hsl(var(--h) 80% 55%)}
  .cc-pno .pt{fill:#15151d;font:800 7px system-ui,sans-serif;text-anchor:middle}
  .cc[data-view="guitar"] .cc-gtr{width:120px;height:152px}
  .cc[data-view="piano"] .cc-pno{width:200px;height:70px}
  /* the tools: the guitar's tuning, how far the song is moved, the keys and the guitar checked */
  .cc-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:center;font-size:12.5px;color:#b4b4c8}
  .cc-tools label{display:inline-flex;align-items:center;gap:6px}
  .cc-tools select,.cc-tools button,.cc-pn button,.cc-zbox button{font:inherit;font-size:12.5px;padding:6px 11px;border-radius:10px;border:1px solid rgba(255,255,255,.1);
    background:rgba(255,255,255,.06);color:#f1f1f6;cursor:pointer}
  .cc-tools button:hover,.cc-pn button:hover,.cc-zbox button:hover{background:rgba(255,255,255,.12)}
  .cc-tools button.on{background:var(--cc-acc);color:#0b0b12;border-color:var(--cc-acc);font-weight:700}
  .cc-shift{padding:5px 10px;border-radius:9px;background:rgba(255,200,80,.12);border:1px solid rgba(255,200,80,.3);color:#ffd27a;font-weight:700}
  .cc-panels{display:flex;gap:10px;flex-wrap:wrap}
  .cc-panels:empty{display:none}
  .cc-pn{flex:1 1 380px;min-width:0;border-radius:16px;padding:10px 12px;display:flex;flex-direction:column;gap:8px;
    background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08)}
  .cc-pnh{display:flex;align-items:center;gap:8px;font-size:12px;color:#a6a6bc}
  .cc-pnh b{font-size:12.5px;color:#fff;letter-spacing:.04em}
  .cc-pnh .sp{flex:1}
  .cc-pnh button{padding:4px 9px;font-size:11.5px}
  .cc-pnh button.on{background:var(--cc-acc);color:#0b0b12;border-color:var(--cc-acc)}
  .cc-pnb svg{display:block;width:100%;height:auto}
  .cc-verdict{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;min-height:30px}
  .cc-want{font-weight:900;font-size:20px;color:#fff}
  .cc-ok,.cc-bad,.cc-wait{padding:4px 10px;border-radius:9px;font-weight:800}
  .cc-ok{background:#1f8f55;color:#eafff2;box-shadow:0 0 16px rgba(61,220,132,.35)}
  .cc-bad{background:rgba(255,90,106,.16);color:#ff9aa5;border:1px solid rgba(255,90,106,.35)}
  .cc-wait{background:rgba(255,255,255,.06);color:#a6a6bc}
  .cc-chip{padding:3px 8px;border-radius:8px;font-weight:700;font-size:11.5px}
  .cc-chip.miss{background:rgba(255,200,80,.14);color:#ffd27a}.cc-chip.xtra{background:rgba(255,90,106,.14);color:#ff9aa5}
  .cc-heard{color:#d8d8e6}.cc-heard b{color:#fff;font-size:14px}
  .cc-score{margin-left:auto;color:#a6a6bc;font-variant-numeric:tabular-nums}.cc-score b{color:#fff}
  /* the keys drawn */
  .cc-kb .kw{fill:#ecedf3;stroke:#4a4a5c;stroke-width:.8}.cc-kb .kb{fill:#15151c;stroke:#000;stroke-width:.6}
  .cc-kb .kw.ok{fill:#3ddc84}.cc-kb .kb.ok{fill:#24b46a}.cc-kb .kw.bad{fill:#ff6b7a}.cc-kb .kb.bad{fill:#e04456}
  .cc-kb .hint{fill:hsl(var(--h) 80% 60%);opacity:.9}.cc-kb .ko{fill:#9a9ab0;font:700 8px system-ui,sans-serif;text-anchor:middle}
  /* the neck drawn */
  .cc-nk .wood{fill:url(#cc-wood)}.cc-nk .fr{stroke:#b9b9c6;stroke-width:2}.cc-nk .nut{fill:#efe9da}
  .cc-nk .st{stroke:#e2ddd2}.cc-nk .inl{fill:rgba(255,255,255,.18)}.cc-nk .fn{fill:#6f6f86;font:700 10px system-ui,sans-serif;text-anchor:middle}
  .cc-nk .sn{fill:#9a9ab0;font:700 10px system-ui,sans-serif;text-anchor:end}
  .cc-nk .want{fill:none;stroke:hsl(var(--h) 85% 65%);stroke-width:2.2;stroke-dasharray:4 3}
  .cc-nk .want.root{stroke-width:3}
  .cc-nk .hd{stroke:#0b0b12;stroke-width:1.5}.cc-nk .hd.ok{fill:#3ddc84}.cc-nk .hd.near{fill:#f5b942}.cc-nk .hd.bad{fill:#ff5a6a}
  .cc-nk .ht{fill:#0b0b12;font:800 10px system-ui,sans-serif;text-anchor:middle}
  .cc-off{font-size:12.5px;color:#a6a6bc;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  /* a chord big */
  .cc-zoom{position:fixed;inset:0;z-index:9700;display:flex;align-items:center;justify-content:center;background:rgba(4,4,10,.66);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
  .cc-zbox{position:relative;max-width:min(860px,94vw);max-height:92vh;overflow:auto;border-radius:24px;padding:22px 26px;color:#f1f1f6;
    background:radial-gradient(600px 300px at 20% -20%,hsl(var(--h) 70% 40% / .35),transparent 70%),linear-gradient(170deg,#1f1f2d,#111118);
    border:1px solid hsl(var(--h) 70% 60% / .4);box-shadow:0 30px 80px rgba(0,0,0,.6)}
  .cc-zoom.fresh .cc-zbox{animation:cc-pop .22s ease-out}
  @keyframes cc-pop{from{transform:scale(.92);opacity:0}to{transform:none;opacity:1}}
  .cc-zx{position:absolute;top:12px;right:12px;width:36px;height:36px;border-radius:50%!important;padding:0!important;font-size:18px!important}
  .cc-zt{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:12px}
  .cc-zn{font-size:56px;font-weight:900;line-height:1;color:#fff}
  .cc-znotes{font-size:15px;color:#c9c9da;letter-spacing:.06em}
  .cc-zrow{display:flex;gap:26px;flex-wrap:wrap;align-items:flex-start;justify-content:center}
  .cc-zcol{display:flex;flex-direction:column;align-items:center;gap:10px}
  .cc-zcol .cc-gtr{width:220px;height:280px;cursor:pointer}
  .cc-zcol .cc-pno{width:380px;height:132px;cursor:pointer}
  .cc-zcol h4{margin:0;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#9a9ab0}
  .cc-zv{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#b4b4c8}
  .cc-zplay{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
  .cc-zplay button.big{background:hsl(var(--h) 75% 55%);color:#0b0b12;border-color:transparent;font-weight:800;padding:9px 16px}
  .cc-zf{display:flex;gap:8px;justify-content:center;margin-top:16px;flex-wrap:wrap}
  /* a duet: the singers get the room */
  #nm-stage:has(.nm-stage-body.duo) .cc-lane{height:52px}
  #nm-stage:has(.nm-stage-body.duo) .cc-pill{top:16px;height:26px}
  `;
  { const st = document.createElement("style"); st.id = "cc-css"; st.textContent = CSS; document.head.appendChild(st); }
  const esc = (s) => escapeHtml(String(s == null ? "" : s));
  const D = () => Mixer.deck;

  // ======================================================================
  // Chords: names, moving them, the shapes on the neck and the keys
  // ======================================================================
  const PC = {C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11};
  const NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const SH = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];      // the names a moved chord gets
  const IV = {"": [0, 4, 7], m: [0, 3, 7], "7": [0, 4, 7, 10], maj7: [0, 4, 7, 11], m7: [0, 3, 7, 10], sus4: [0, 5, 7], sus2: [0, 2, 7],
    dim: [0, 3, 6], aug: [0, 4, 8], "5": [0, 7], "6": [0, 4, 7, 9], m6: [0, 3, 7, 9], add9: [0, 4, 7, 14], "9": [0, 4, 7, 10, 14],
    m9: [0, 3, 7, 10, 14], dim7: [0, 3, 6, 9], m7b5: [0, 3, 6, 10], "7sus4": [0, 5, 7, 10]};
  const accOf = (a) => a === "#" || a === "♯" ? 1 : a === "b" || a === "♭" ? -1 : 0;
  const RE = /^([A-G])([#b♯♭]?)([^/]*)(?:\/([A-G])([#b♯♭]?))?$/;
  function parseChord(name){
    const m = RE.exec(String(name || "").trim());
    if(!m) return null;
    const pc = (PC[m[1]] + accOf(m[2]) + 12) % 12;
    let q = m[3].replace(/^min(?!or)/, "m").replace(/^maj$/, "").replace(/^M7$/, "maj7").replace(/^:/, "");
    if(q === "min") q = "m";
    const iv = IV[q] || (q.startsWith("m") && !q.startsWith("maj") ? IV.m : IV[""]);
    const bass = m[4] ? (PC[m[4]] + accOf(m[5]) + 12) % 12 : null;
    return {pc, q, iv, bass, name: String(name).trim(), pcs: [...new Set(iv.map(i => (pc + i) % 12))]};
  }
  // a chord's name moved by s semitones (its quality and its bass go with it)
  function transpose(name, s){
    s = ((s % 12) + 12) % 12;
    const m = RE.exec(String(name || "").trim());
    if(!s || !m) return name;
    const r = SH[(PC[m[1]] + accOf(m[2]) + s + 12) % 12];
    return r + m[3] + (m[4] ? "/" + SH[(PC[m[4]] + accOf(m[5]) + s + 12) % 12] : "");
  }
  const hue = (pc) => (pc * 30 + 200) % 360;

  // ---- the shapes, for a guitar in standard tuning (low E → high e)
  const STD = [40, 45, 50, 55, 59, 64];
  const SHAPE_Q = (q) => ({"6": "", add9: "", "9": "7", m6: "m", m9: "m7", dim7: "dim", m7b5: "dim", "7sus4": "sus4"})[q] ?? (IV[q] ? q : (q.startsWith("m") && !q.startsWith("maj") ? "m" : ""));
  // open shapes: frets low E → high e (-1 = not played) and fingers
  const OPEN = {
    "C": [[-1, 3, 2, 0, 1, 0], [0, 3, 2, 0, 1, 0]], "C7": [[-1, 3, 2, 3, 1, 0], [0, 3, 2, 4, 1, 0]], "Cmaj7": [[-1, 3, 2, 0, 0, 0], [0, 3, 2, 0, 0, 0]],
    "Csus2": [[-1, 3, 0, 0, 1, 3], [0, 2, 0, 0, 1, 3]], "Csus4": [[-1, 3, 3, 0, 1, 1], [0, 3, 4, 0, 1, 1]],
    "D": [[-1, -1, 0, 2, 3, 2], [0, 0, 0, 1, 3, 2]], "Dm": [[-1, -1, 0, 2, 3, 1], [0, 0, 0, 2, 3, 1]], "D7": [[-1, -1, 0, 2, 1, 2], [0, 0, 0, 2, 1, 3]],
    "Dm7": [[-1, -1, 0, 2, 1, 1], [0, 0, 0, 2, 1, 1]], "Dmaj7": [[-1, -1, 0, 2, 2, 2], [0, 0, 0, 1, 2, 3]], "Dsus4": [[-1, -1, 0, 2, 3, 3], [0, 0, 0, 1, 3, 4]],
    "Dsus2": [[-1, -1, 0, 2, 3, 0], [0, 0, 0, 1, 3, 0]], "D5": [[-1, -1, 0, 2, 3, -1], [0, 0, 0, 1, 3, 0]],
    "E": [[0, 2, 2, 1, 0, 0], [0, 2, 3, 1, 0, 0]], "Em": [[0, 2, 2, 0, 0, 0], [0, 2, 3, 0, 0, 0]], "E7": [[0, 2, 0, 1, 0, 0], [0, 2, 0, 1, 0, 0]],
    "Em7": [[0, 2, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0]], "Emaj7": [[0, 2, 1, 1, 0, 0], [0, 3, 1, 2, 0, 0]], "Esus4": [[0, 2, 2, 2, 0, 0], [0, 2, 3, 4, 0, 0]],
    "E5": [[0, 2, 2, -1, -1, -1], [0, 1, 2, 0, 0, 0]],
    "G": [[3, 2, 0, 0, 0, 3], [2, 1, 0, 0, 0, 3]], "G7": [[3, 2, 0, 0, 0, 1], [3, 2, 0, 0, 0, 1]], "Gmaj7": [[3, 2, 0, 0, 0, 2], [3, 2, 0, 0, 0, 1]],
    "G5": [[3, 5, 5, -1, -1, -1], [1, 3, 4, 0, 0, 0]],
    "A": [[-1, 0, 2, 2, 2, 0], [0, 0, 1, 2, 3, 0]], "Am": [[-1, 0, 2, 2, 1, 0], [0, 0, 2, 3, 1, 0]], "A7": [[-1, 0, 2, 0, 2, 0], [0, 0, 2, 0, 3, 0]],
    "Am7": [[-1, 0, 2, 0, 1, 0], [0, 0, 2, 0, 1, 0]], "Amaj7": [[-1, 0, 2, 1, 2, 0], [0, 0, 2, 1, 3, 0]], "Asus4": [[-1, 0, 2, 2, 3, 0], [0, 0, 1, 2, 3, 0]],
    "Asus2": [[-1, 0, 2, 2, 0, 0], [0, 0, 1, 2, 0, 0]], "A5": [[-1, 0, 2, 2, -1, -1], [0, 0, 1, 2, 0, 0]],
  };
  // movable shapes, by the fret r of the root: on the low E string (E shape) and on the A string (A shape)
  const E_SHAPE = {"": [[0, 2, 2, 1, 0, 0], [1, 3, 4, 2, 1, 1]], m: [[0, 2, 2, 0, 0, 0], [1, 3, 4, 1, 1, 1]], "7": [[0, 2, 0, 1, 0, 0], [1, 3, 1, 2, 1, 1]],
    m7: [[0, 2, 0, 0, 0, 0], [1, 3, 1, 1, 1, 1]], maj7: [[0, -9, 1, 1, 0, -9], [1, 0, 3, 4, 2, 0]], sus4: [[0, 2, 2, 2, 0, 0], [1, 2, 3, 4, 1, 1]],
    "5": [[0, 2, 2, -9, -9, -9], [1, 3, 4, 0, 0, 0]]};
  const A_SHAPE = {"": [[-9, 0, 2, 2, 2, 0], [0, 1, 2, 3, 4, 1]], m: [[-9, 0, 2, 2, 1, 0], [0, 1, 3, 4, 2, 1]], "7": [[-9, 0, 2, 0, 2, 0], [0, 1, 3, 1, 4, 1]],
    m7: [[-9, 0, 2, 0, 1, 0], [0, 1, 3, 1, 2, 1]], maj7: [[-9, 0, 2, 1, 2, 0], [0, 1, 3, 2, 4, 1]], sus4: [[-9, 0, 2, 2, 3, 0], [0, 1, 2, 3, 4, 1]],
    sus2: [[-9, 0, 2, 2, 0, 0], [0, 1, 3, 4, 1, 1]], dim: [[-9, 0, 1, 2, 1, -9], [0, 1, 2, 4, 3, 0]], aug: [[-9, 0, 3, 2, 2, -9], [0, 1, 4, 2, 3, 0]],
    "5": [[-9, 0, 2, 2, -9, -9], [0, 1, 3, 4, 0, 0]]};
  function shapeOf(ch){
    const q = SHAPE_Q(ch.q), nm = NN[ch.pc] + q;
    if(OPEN[nm]) return {frets: OPEN[nm][0], fingers: OPEN[nm][1]};
    const cands = [];
    const add = (tbl, root0) => { const s = tbl[q]; if(!s) return; let r = (ch.pc - root0 + 12) % 12; if(r === 0) r = 12;
      cands.push({frets: s[0].map(f => f <= -9 ? -1 : f + r), fingers: s[1], r}); };
    add(E_SHAPE, 4); add(A_SHAPE, 9);
    if(!cands.length){ const s = A_SHAPE[""]; let r = (ch.pc - 9 + 12) % 12 || 12; return {frets: s[0].map(f => f <= -9 ? -1 : f + r), fingers: s[1]}; }
    cands.sort((a, b) => a.r - b.r);
    return cands[0];
  }

  // ---- shapes for any six strings: the drawn ones when the guitar is standard moved as a whole (Eb, D…),
  // and what the neck offers within four frets — the lowest note the bass (or the root), every note of
  // the chord there (the fifth may go when there are four), no string skipped, four fingers (one a barre).
  function search(ch, tun){
    const pcs = new Set(ch.pcs), fifth = (ch.pc + 7) % 12, bass = ch.bass != null ? ch.bass : ch.pc;
    const found = new Map();
    for(let base = 0; base <= 12; base++){
      const opts = tun.map(o => { const a = [-1];
        (base === 0 ? [0, 1, 2, 3] : [0, base, base + 1, base + 2, base + 3]).forEach(f => { if(pcs.has((o + f) % 12) && !a.includes(f)) a.push(f); });
        return a; });
      const cur = new Array(tun.length);
      const rec = (s) => { if(s === tun.length){ judge(cur.slice()); return; } for(const f of opts[s]){ cur[s] = f; rec(s + 1); } };
      rec(0);
    }
    function judge(fr){
      const played = []; fr.forEach((f, i) => { if(f >= 0) played.push(i); });
      if(played.length < Math.min(4, pcs.size + 1)) return;
      const lo = played[0], hi = played[played.length - 1];
      for(let i = lo; i <= hi; i++) if(fr[i] < 0) return;
      if(hi < tun.length - 2) return;
      if((tun[lo] + fr[lo]) % 12 !== bass) return;
      const have = new Set(played.map(i => (tun[i] + fr[i]) % 12));
      for(const p of pcs) if(!have.has(p) && !(p === fifth && pcs.size >= 4)) return;
      const fretted = fr.filter(f => f > 0);
      const minF = fretted.length ? Math.min(...fretted) : 0, maxF = fretted.length ? Math.max(...fretted) : 0;
      if(maxF - minF > 3) return;
      let barre = false;
      if(fretted.length > 4){
        const at = []; fr.forEach((f, i) => { if(f === minF) at.push(i); });
        if(at.length < 2) return;
        for(let i = at[0]; i <= at[at.length - 1]; i++) if(fr[i] === 0) return;     // a barre lies over the strings between
        barre = true;
        if(fretted.length - at.length + 1 > 4) return;
      }
      const opens = fr.filter(f => f === 0).length;
      const score = played.length * 3 - (maxF - minF) - minF * 0.45 - (barre ? 1.2 : 0) + opens * 0.6;
      const key = fr.join(",");
      if(!found.has(key) || found.get(key).score < score) found.set(key, {frets: fr, score, minF, barre});
    }
    return [...found.values()].sort((a, b) => b.score - a.score);
  }
  function fingersFor(v){
    const fr = v.frets, fi = fr.map(() => 0), fretted = [];
    fr.forEach((f, i) => { if(f > 0) fretted.push(i); });
    if(!fretted.length) return fi;
    const minF = Math.min(...fretted.map(i => fr[i]));
    let n = 1, rest = fretted;
    if(v.barre || fretted.length > 4){ fretted.forEach(i => { if(fr[i] === minF) fi[i] = 1; }); rest = fretted.filter(i => fr[i] !== minF); n = 2; }
    rest.sort((a, b) => fr[a] - fr[b] || a - b).forEach(i => { fi[i] = Math.min(4, n++); });
    return fi;
  }
  const vcache = new Map();
  // the shapes for a chord on this guitar, the best first: [{frets, fingers}]
  function voicings(ch, tun){
    tun = tun || STD;
    const key = ch.name + "|" + tun.join(",");
    if(vcache.has(key)) return vcache.get(key);
    const out = [], seen = new Set();
    const push = (v) => { const k = v.frets.join(","); if(seen.has(k)) return; seen.add(k); out.push({frets: v.frets, fingers: v.fingers || fingersFor(v)}); };
    if(tun.length === 6){
      const d = tun.map((m, i) => m - STD[i]);
      if(d.every(x => x === d[0])){
        const moved = parseChord(transpose(ch.name, -d[0]));
        if(moved) push(shapeOf(moved));
      }
    }
    const pos = new Set();
    search(ch, tun).forEach(v => { if(out.length >= 6) return; const p = Math.floor(v.minF / 3); if(pos.has(p) && out.length) return; pos.add(p); push(v); });
    if(!out.length && tun.length === 6) push(shapeOf(ch));
    vcache.set(key, out);
    return out;
  }

  // ---- drawing: a shape on a piece of the neck (a card), the keys of two octaves
  const STRN = (m) => SH[((m % 12) + 12) % 12];
  function guitarSvg(ch, v, tun){
    const fr = v.frets, used = fr.filter(f => f > 0);
    const hi = used.length ? Math.max(...used) : 0, lo = used.length ? Math.min(...used) : 0;
    const base = hi <= 5 ? 1 : lo;
    const W = 120, H = 156, x0 = 20, y0 = 30, sw = 16, fh = 21, N = 5, S = tun.length;
    const sx = (i) => x0 + i * sw, fy = (f) => y0 + (f - base + 0.5) * fh;
    let g = `<rect x="${x0 - 6}" y="${y0}" width="${(S - 1) * sw + 12}" height="${N * fh}" rx="3" class="gfb"/>`;
    for(let i = 0; i <= N; i++) g += `<line x1="${x0 - 6}" y1="${y0 + i * fh}" x2="${x0 + (S - 1) * sw + 6}" y2="${y0 + i * fh}" class="gf"/>`;
    for(let i = 0; i < S; i++) g += `<line x1="${sx(i)}" y1="${y0}" x2="${sx(i)}" y2="${y0 + N * fh}" class="gs" stroke-width="${(1.9 - i * 0.22).toFixed(2)}"/>`;
    if(base === 1) g += `<rect x="${x0 - 7}" y="${y0 - 5}" width="${(S - 1) * sw + 14}" height="6" rx="2" class="gn"/>`;
    else g += `<text x="${x0 - 9}" y="${y0 + fh * 0.66}" class="gb">${base}</text>`;
    const isRoot = (i) => fr[i] >= 0 && (tun[i] + fr[i]) % 12 === ch.pc;
    fr.forEach((f, i) => { if(f < 0) g += `<text x="${sx(i)}" y="${y0 - 10}" class="gx">×</text>`;
      else if(f === 0) g += `<circle cx="${sx(i)}" cy="${y0 - 14}" r="4.4" class="go"${isRoot(i) ? ` style="stroke:hsl(var(--h) 80% 62%)"` : ""}/>`; });
    const one = fr.map((f, i) => ({f, i, fi: v.fingers[i]})).filter(x => x.fi === 1 && x.f > 0);
    const barre = one.length > 1 && one.every(x => x.f === one[0].f) ? one : null;
    if(barre){ const a = barre[0].i, b = barre[barre.length - 1].i;
      g += `<rect x="${sx(a) - 7.5}" y="${fy(barre[0].f) - 7.5}" width="${sx(b) - sx(a) + 15}" height="15" rx="7.5" class="gbar"/><text x="${(sx(a) + sx(b)) / 2}" y="${fy(barre[0].f) + 3.5}" class="gt">1</text>`; }
    fr.forEach((f, i) => { if(f > 0 && !(barre && barre.some(x => x.i === i)))
      g += `<circle cx="${sx(i)}" cy="${fy(f)}" r="7.5" class="gd${isRoot(i) ? " root" : ""}"/>${v.fingers[i] ? `<text x="${sx(i)}" y="${fy(f) + 3.5}" class="gt">${v.fingers[i]}</text>` : ""}`; });
    tun.forEach((m, i) => { g += `<text x="${sx(i)}" y="${y0 + N * fh + 15}" class="gl">${STRN(m)}</text>`; });
    return `<svg class="cc-gtr" viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }
  // the chord on the keys: from its root upwards in the first octave, its bass under it when it has one
  function pianoNotes(ch){
    const root = 60 + ch.pc - (ch.pc > 6 ? 12 : 0), b = ch.bass != null ? ch.bass : ch.pc;
    return [root - 12 - ((ch.pc - b + 12) % 12), ...ch.iv.map(i => root + i)];
  }
  function pianoSvg(ch){
    const notes = new Set(), root = ch.pc;
    ch.iv.forEach(iv => { let n = root + iv; while(n > 23) n -= 12; notes.add(n); });
    if(ch.bass != null) notes.add(ch.bass);
    const WH = [0, 2, 4, 5, 7, 9, 11], BL = [1, 3, 6, 8, 10];
    const ww = 12, W = ww * 14, H = 58;
    let g = "";
    for(let o = 0; o < 2; o++) WH.forEach((pc, k) => { const n = o * 12 + pc, x = (o * 7 + k) * ww, on = notes.has(n);
      g += `<rect x="${x + 0.5}" y="0.5" width="${ww - 1}" height="${H - 1}" rx="2" class="pw${on ? (n % 12 === root ? " root" : " on") : ""}"/>`;
      if(on) g += `<text x="${x + ww / 2}" y="${H - 6}" class="pt">${SH[pc].replace("b", "♭").replace("#", "♯")}</text>`; });
    for(let o = 0; o < 2; o++) BL.forEach(pc => { const n = o * 12 + pc, k = WH.indexOf(pc - 1), x = (o * 7 + k + 1) * ww - 4, on = notes.has(n);
      g += `<rect x="${x}" y="0" width="8" height="${H * 0.6}" rx="1.5" class="pb${on ? (n % 12 === root ? " root" : " on") : ""}"/>`; });
    return `<svg class="cc-pno" viewBox="0 0 ${W} ${H}">${g}</svg>`;
  }

  // ======================================================================
  // What is kept: the guitar's tuning for the shapes, the keys heard
  // ======================================================================
  const KEY = "pick-chordcoach";
  const cfg = {tuning: "", keysSound: true};
  try{ const v = JSON.parse(localStorage.getItem(KEY) || "{}"); if(typeof v.tuning === "string") cfg.tuning = v.tuning; if("keysSound" in v) cfg.keysSound = !!v.keysSound; }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  const mine = () => { try{ const v = MyTuning.get(); return v && v.midi.length === 6 ? v.midi.slice() : null; }catch(e){ return null; } };
  const sixes = () => { try{ return Tunings.all().filter(t => t.strings.length === 6 && t.strings.every(Tunings.valid)); }catch(e){ return []; } };
  // the guitar's strings, low → high
  function tuning(){
    if(!cfg.tuning || cfg.tuning === "mine"){ const m = mine(); if(m) return m; }
    const t = sixes().find(x => x.id === cfg.tuning) || sixes().find(x => x.id === "standard");
    return t ? t.strings.map(Tunings.midi).sort((a, b) => a - b) : STD.slice();
  }
  const tuningKey = () => tuning().join(",");

  // ======================================================================
  // Sound: a guitar strummed, a piano — Pick's instruments when it has them, else its plucked string and keys
  // ======================================================================
  const audio = () => { const o = Mixer.voiceOut(); if(o.ac.state === "suspended") o.ac.resume(); return o; };
  const banks = {};
  let zv = null;
  function bank(kind){
    if(banks[kind] !== undefined) return banks[kind];
    return banks[kind] = (async () => {
      try{
        const L = await Mixer.guitar.instrumentLib(), {ac} = audio();
        zv = zv || new L.Zvuky(ac);
        const all = L.nastrojePodleKategorii().flatMap(g => g.nastroje.map(x => ({id: x.id, nazev: x.nazev, kat: g.kategorie})));
        const want = kind === "piano" ? [/acoustic_grand_piano/, /grand|piano/i, /klav/i] : [/acoustic_guitar_steel/, /acoustic_guitar/, /akust/i, /guitar|kytar/i];
        let hit = null;
        for(const r of want){ hit = all.find(x => r.test(x.id) || r.test(x.nazev || "")); if(hit) break; }
        if(!hit) return null;
        return await zv.nacti(L.najdiNastroj(hit.id).soundfont);
      }catch(e){ return null; }
    })();
  }
  function playBuf(ac, out, bufs, m, at, dur, vel){
    const keys = [...bufs.keys()], k = bufs.has(m) ? m : keys.reduce((a, b) => Math.abs(b - m) < Math.abs(a - m) ? b : a);
    const src = ac.createBufferSource(), g = ac.createGain(), peak = 0.25 + 0.6 * vel;
    src.buffer = bufs.get(k); src.playbackRate.value = Math.pow(2, (m - k) / 12);
    g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(peak, at + 0.004);
    if(dur != null){ g.gain.setValueAtTime(peak, at + dur); g.gain.linearRampToValueAtTime(0, at + dur + 0.35); src.stop(at + dur + 0.4); }
    src.connect(g); g.connect(out); src.start(at);
    return (t) => { try{ g.gain.cancelScheduledValues(t); g.gain.setValueAtTime(g.gain.value, t); g.gain.linearRampToValueAtTime(0, t + 0.3); src.stop(t + 0.35); }catch(e){} };
  }
  let lastHeard = null;     // what was played last (tests)
  // a shape strummed down (or picked string by string), low to high
  async function strum(v, tun, how){
    const {ac, out} = audio(), notes = [];
    v.frets.forEach((f, s) => { if(f >= 0) notes.push({s, f, m: tun[s] + f}); });
    lastHeard = {kind: "guitar", notes: notes.map(x => x.m)};
    const bufs = await bank("guitar"), t0 = ac.currentTime + 0.04, gap = how === "arp" ? 0.24 : 0.032;
    notes.forEach((x, k) => {
      const at = t0 + k * gap;
      if(bufs) playBuf(ac, out, bufs, x.m, at, 2.6 - k * gap, 0.75);
      else if(typeof GuitarSampler !== "undefined") GuitarSampler.playNote(ac, out, tun.length - 1 - x.s, x.f, at, tun.slice().reverse(), {duration: 2.6 - k * gap}, 9);
    });
  }
  async function piano(ch, how){
    const {ac, out} = audio(), notes = pianoNotes(ch);
    lastHeard = {kind: "piano", notes};
    const bufs = await bank("piano"), t0 = ac.currentTime + 0.04, gap = how === "arp" ? 0.22 : 0;
    notes.forEach((m, k) => {
      const at = t0 + k * gap;
      if(bufs) playBuf(ac, out, bufs, m, at, 2.2, 0.7);
      else if(typeof LoopSound !== "undefined") LoopSound.synth(ac, out, "syn:ep", m, 0.7, at, 2.2);
    });
  }

  // ======================================================================
  // The song's chords, where it sounds now
  // ======================================================================
  let seq = [], seqSig = "", semis = 0;
  function chords(){
    let raw = [], p = 0;
    try{ raw = D().chords(); p = D().pitch ? D().pitch() : 0; }catch(e){}
    const sig = (Mixer.currentSong() || "") + ":" + raw.length + ":" + (raw.length ? raw[0].start + ":" + raw[raw.length - 1].end : "") + ":" + p;
    if(sig === seqSig) return seq;
    seqSig = sig; semis = p;
    const out = [];
    raw.forEach(x => {
      const ch0 = parseChord(x.chord);
      let nm = x.chord;
      if(x.bass && ch0 && !/\//.test(nm)){ const b = parseChord(x.bass); if(b && b.pc !== ch0.pc) nm += "/" + x.bass; }
      nm = transpose(nm, p);
      const last = out[out.length - 1];
      if(last && last.name === nm && x.start - last.end < 0.15){ last.end = x.end; return; }
      out.push({start: x.start, end: x.end, name: nm, ch: parseChord(nm)});
    });
    seq = out;
    return seq;
  }
  // the chord sounding at t (-1 between chords)
  function idxAt(list, t){
    let lo = 0, hi = list.length - 1, j = -1;
    while(lo <= hi){ const mid = (lo + hi) >> 1; if(list[mid].start <= t + 0.03){ j = mid; lo = mid + 1; } else hi = mid - 1; }
    return j >= 0 && t < list[j].end + 0.05 ? j : -1;
  }
  const nextAt = (list, t) => { const k = list.findIndex(x => x.start > t + 0.03); return k; };
  function grid(){
    let gr = {};
    try{ gr = D().grid(); }catch(e){}
    let db = (gr.downbeats || []).slice();
    const beats = gr.beats || [], per = gr.perBar || 4;
    if(!db.length && beats.length) db = beats.filter((_, k) => k % per === 0);
    return {db, beats, per};
  }

  // ======================================================================
  // On the screen
  // ======================================================================
  let box = null, lane = null, track = null, head = null, car = null, tools = null, panels = null, deckCfg = () => ({chords: true, chordView: "both"});
  function mount(after, getCfg){
    deckCfg = getCfg || deckCfg;
    box = document.createElement("div");
    box.className = "cc";
    box.innerHTML = `<div class="cc-lane" title="Klikni kamkoli — píseň skočí tam"><div class="cc-track"></div><div class="cc-head"></div></div>
      <div class="cc-car"></div><div class="cc-tools"></div><div class="cc-panels"></div>`;
    after.after(box);
    lane = box.querySelector(".cc-lane"); track = box.querySelector(".cc-track"); head = box.querySelector(".cc-head");
    car = box.querySelector(".cc-car"); tools = box.querySelector(".cc-tools"); panels = box.querySelector(".cc-panels");
    lane.addEventListener("click", (e) => {
      if(!pps) return;
      const r = lane.getBoundingClientRect(), t = (e.clientX - r.left - laneX) / pps;
      if(t >= 0){ Mixer.seek(Math.min(t, Mixer.duration() || t)); if(typeof SongDeck !== "undefined") SongDeck.paint(); }
    });
    new ResizeObserver(() => { laneSig = ""; carSig = ""; }).observe(lane);
    paintTools();
    requestAnimationFrame(frame);
    return box;
  }
  const shown = () => box && box.isConnected && box.offsetParent !== null;

  // ---- the lane: every chord of the song laid out in time; moved under a line that stays
  let laneSig = "", pps = 0, laneX = 0, laneCi = -2, laneBeat = -1;
  function buildLane(){
    const list = chords(), W = lane.clientWidth || 900, G = grid();
    const sig = seqSig + ":" + W + ":" + G.db.length + ":" + G.beats.length;
    if(sig === laneSig) return;
    laneSig = sig; laneCi = -2;
    laneX = Math.round(W * 0.3);
    let barLen = 2;
    if(G.db.length > 2){ const d = G.db.slice(1).map((x, k) => x - G.db[k]).sort((a, b) => a - b); barLen = d[d.length >> 1] || 2; }
    pps = Math.max(30, Math.min(420, W / ((G.db.length > 2 ? 4.2 : 6) * barLen)));
    let html = "";
    G.db.forEach((a, k) => { const e = G.db[k + 1] != null ? G.db[k + 1] : a + barLen;
      html += `<div class="cc-bar" style="left:${(a * pps).toFixed(1)}px;width:${((e - a) * pps).toFixed(1)}px"><i>${k + 1}</i></div>`; });
    G.beats.forEach(x => { html += `<b class="cc-bt" style="left:${(x * pps).toFixed(1)}px"></b>`; });
    let secs = [];
    try{ secs = Mixer.sectionsNow ? Mixer.sectionsNow() : []; }catch(e){}
    secs.forEach(s => { html += `<span class="cc-sec" style="left:${(s.start * pps).toFixed(1)}px;--c:${esc(s.color || "#8391a5")}">${esc(s.name)}</span>`; });
    list.forEach((c, i) => { html += `<div class="cc-pill" data-i="${i}" style="left:${(c.start * pps + 1).toFixed(1)}px;width:${Math.max(16, (c.end - c.start) * pps - 3).toFixed(1)}px;--h:${c.ch ? hue(c.ch.pc) : 230}"><span>${esc(c.name)}</span></div>`; });
    track.innerHTML = html;
    head.style.left = laneX + "px";
  }
  function frame(){
    requestAnimationFrame(frame);
    if(!shown() || !deckCfg().chords || !seq.length || !pps) return;
    const t = Mixer.currentTime();
    track.style.transform = `translate3d(${(laneX - t * pps).toFixed(1)}px,0,0)`;
    const ci = idxAt(seq, t);
    if(ci !== laneCi){
      laneCi = ci;
      const from = ci >= 0 ? ci : nextAt(seq, t);
      track.querySelectorAll(".cc-pill").forEach(n => { const i = +n.dataset.i; n.classList.toggle("now", i === ci); n.classList.toggle("past", from >= 0 ? i < from : true); });
    }
    // the line pulses on the beat, harder on the bar's first
    const G = grid();
    if(G.beats.length){
      let lo = 0, hi = G.beats.length - 1, b = -1;
      while(lo <= hi){ const mid = (lo + hi) >> 1; if(G.beats[mid] <= t){ b = mid; lo = mid + 1; } else hi = mid - 1; }
      if(b !== laneBeat){
        const was = laneBeat; laneBeat = b;
        if(b >= 0 && was >= 0 && b === was + 1 && Mixer.isPlaying()){
          const down = G.db.some(x => Math.abs(x - G.beats[b]) < 0.03);
          head.classList.remove("beat", "down"); void head.offsetWidth; head.classList.add(down ? "down" : "beat");
        }
      }
    }
    // how far into the chord now
    const cur = car.querySelector(".cc-card.now .cc-prog i");
    if(cur && ci >= 0){ const c = seq[ci]; cur.style.width = Math.max(0, Math.min(100, (t - c.start) / Math.max(0.05, c.end - c.start) * 100)).toFixed(1) + "%"; }
  }

  // ---- the cards: the one sounding in the middle, two before and two after
  let carSig = "", cards = new Map();
  const VIEW = {both: {cw: 176, ch: 252}, guitar: {cw: 150, ch: 214}, piano: {cw: 214, ch: 146}};
  function cardHtml(c, i, view, tun){
    const ch = c.ch, h = ch ? hue(ch.pc) : 230;
    let body = `<div class="cc-msg">?</div>`;
    if(ch){
      const v = voicings(ch, tun)[0];
      body = `<div class="cc-q">${view !== "piano" && v ? guitarSvg(ch, v, tun) : ""}${view !== "guitar" ? pianoSvg(ch) : ""}</div>`;
    }
    return {h, html: `<div class="cc-cn">${esc(c.name)}</div>${body}<span class="cc-tag"></span><div class="cc-prog"><i></i></div>`};
  }
  function paintCards(t){
    const list = seq, view = deckCfg().chordView || "both", V = VIEW[view] || VIEW.both, tun = tuning();
    const ci = idxAt(list, t), base = ci >= 0 ? ci : nextAt(list, t);
    // smaller in a duet (the singers need the room) and on a low screen
    const k = box.closest("#nm-stage") && document.querySelector("#nm-stage .nm-stage-body.duo") ? 0.62 : window.innerHeight < 860 ? 0.8 : 1;
    const build = seqSig + ":" + view + ":" + tun.join(",") + ":" + car.clientWidth + ":" + k;
    if(build !== carSig){ carSig = build; car.innerHTML = ""; cards = new Map(); car.style.setProperty("--ch", Math.round(V.ch * k) + "px"); car.style.setProperty("--cw", V.cw + "px"); }
    if(base < 0){ cards.forEach(n => n.remove()); cards.clear(); return; }
    const cw = V.cw * k, W = car.clientWidth || 900, s1 = 0.7, s2 = 0.52, gap = Math.max(18, Math.min(46, (W - cw * (1 + 2 * s1 + 2 * s2)) / 6));
    const x1 = cw / 2 + gap + cw * s1 / 2, x2 = x1 + cw * s1 / 2 + gap + cw * s2 / 2;
    const place = (d) => ({x: Math.sign(d) * (Math.abs(d) === 1 ? x1 : Math.abs(d) === 2 ? x2 : x2 + cw), s: k * (d === 0 ? 1 : Math.abs(d) === 1 ? s1 : s2),
      o: d === 0 ? 1 : Math.abs(d) === 1 ? 0.78 : 0.42});
    const want = new Map();
    for(let d = -2; d <= 2; d++){ const i = base + d; if(i >= 0 && i < list.length) want.set(i, d); }
    cards.forEach((n, i) => { if(!want.has(i)){ const d = i < base ? -3 : 3, p = place(d); n.classList.add("gone");
      n.style.transform = `translate(-50%,-50%) translateX(${p.x}px) scale(${p.s})`; setTimeout(() => n.remove(), 520); cards.delete(i); } });
    want.forEach((d, i) => {
      let n = cards.get(i);
      if(!n){
        const c = cardHtml(list[i], i, view, tun);
        n = document.createElement("div"); n.className = "cc-card"; n.dataset.i = i; n.style.setProperty("--h", c.h); n.innerHTML = c.html;
        const from = place(d + (d >= 0 ? 1 : -1));
        n.style.transition = "none"; n.style.transform = `translate(-50%,-50%) translateX(${from.x}px) scale(${from.s})`; n.style.opacity = "0";
        n.onclick = () => openZoom(+n.dataset.i);
        car.appendChild(n); cards.set(i, n);
        void n.offsetWidth; n.style.transition = "";
      }
      const p = place(d);
      n.style.transform = `translate(-50%,-50%) translateX(${p.x}px) scale(${p.s})`; n.style.opacity = String(p.o); n.style.zIndex = String(10 - Math.abs(d));
      n.classList.toggle("now", d === 0 && ci >= 0);
      const tag = n.querySelector(".cc-tag");
      if(d === 0 && ci >= 0){ tag.className = "cc-tag"; tag.textContent = "TEĎ"; }
      else if((d === 1 && ci >= 0) || (d === 0 && ci < 0)){ tag.className = "cc-tag next"; tag.textContent = "DALŠÍ" + beatsTo(list[i].start, t); }
      else { tag.className = "cc-tag"; tag.textContent = ""; tag.style.display = "none"; }
      if(tag.textContent) tag.style.display = "";
    });
  }
  function beatsTo(at, t){
    const G = grid();
    if(!G.beats.length) return "";
    const n = G.beats.filter(x => x > t + 0.02 && x < at - 0.02).length + 1;
    return n <= 8 ? ` · za ${n}` : "";
  }

  // ---- the tools under the cards
  function paintTools(){
    if(!tools) return;
    const m = mine(), cur = cfg.tuning || (m ? "mine" : "standard");
    const opts = (m ? `<option value="mine">Moje kytara (z ladičky): ${esc(m.map(STRN).join(" "))}</option>` : "")
      + sixes().map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
    tools.innerHTML = `<label title="Pro jaké ladění kytary ukazovat hmaty — tvoje z ladičky, nebo jiné">Ladění kytary <select data-k="tun">${opts}</select></label>
      <span class="cc-shift" data-k="shift" hidden></span>
      <button type="button" data-k="keys" title="Připoj MIDI klávesy — ukáže, co držíš, a jestli je to správný akord">🎹 MIDI klávesy</button>
      <button type="button" data-k="gtr" title="Poslouchá tvoji kytaru (vstup kytary ve Steems) — ukáže na hmatníku, co hraješ, a jestli je to správný akord">🎸 Hlídat kytaru</button>`;
    const sel = tools.querySelector('[data-k="tun"]');
    sel.value = cur; if(sel.value !== cur) sel.selectedIndex = 0;
    sel.onchange = () => { cfg.tuning = sel.value; keep(); carSig = ""; paintPanels(); };
    tools.querySelector('[data-k="keys"]').onclick = () => toggleKeys();
    tools.querySelector('[data-k="gtr"]').onclick = () => toggleGtr();
    paintToolState();
  }
  function paintToolState(){
    if(!tools) return;
    tools.querySelector('[data-k="keys"]').classList.toggle("on", kb.on);
    tools.querySelector('[data-k="gtr"]').classList.toggle("on", gl.on);
    const sh = tools.querySelector('[data-k="shift"]');
    sh.hidden = !semis;
    sh.textContent = semis ? `Akordy posunuté o ${semis > 0 ? "+" : "−"}${Math.abs(semis)} — jak teď píseň zní` : "";
    sh.title = "Píseň hraje posunutá (výška písně, nebo přeladění na tvoji kytaru) — akordy na kartách i v pásu jsou přepočítané, aby seděly s tím, co slyšíš";
  }

  function paint(){
    if(!box) return;
    const c = deckCfg();
    box.classList.toggle("off", !c.chords);
    box.dataset.view = c.chordView || "both";
    if(!c.chords || !shown()) return;
    const list = chords();
    if(!list.length){
      let st = "";
      try{ st = D().analysis(); }catch(e){}
      const song = Mixer.currentSong(), f = found.get(song);
      const msg = st === "loading" ? "Akordy se hledají…" : f === "busy" ? autoMsg : f === "none" ? autoMsg || "Akordy se najít nepodařilo." : st === "failed" ? "Analýza se nezdařila — akordy hledám z nahrávky…" : "Akordy se hledají…";
      if(car.dataset.msg !== msg){ car.dataset.msg = msg; track.innerHTML = ""; laneSig = carSig = ""; cards = new Map();
        car.innerHTML = `<div class="cc-msg">${esc(msg)}${f === "none" ? ` <button type="button" class="cc-again">Zkusit znovu analýzou</button>` : ""}</div>`;
        const again = car.querySelector(".cc-again");
        if(again) again.onclick = async () => { found.delete(song); car.dataset.msg = ""; showToast("Hledám akordy znovu — chvíli to potrvá"); await D().reanalyze(); }; }
      return;
    }
    if(car.dataset.msg){ car.dataset.msg = ""; car.innerHTML = ""; cards = new Map(); carSig = ""; }
    buildLane();
    const t = Mixer.currentTime();
    paintCards(t);
    paintToolState();
    tally(t);
    paintPanels();
  }

  // ======================================================================
  // A chord big: its shapes, its keys, and both heard
  // ======================================================================
  let zoom = null;
  function openZoom(i){
    closeZoom();
    const c = seq[i];
    if(!c || !c.ch) return;
    zoom = {i, v: 0, el: document.createElement("div")};
    zoom.el.className = "cc-zoom fresh";
    setTimeout(() => zoom && zoom.el.classList.remove("fresh"), 300);
    (box.closest("#nm-stage") || document.body).appendChild(zoom.el);
    zoom.el.addEventListener("pointerdown", (e) => { if(e.target === zoom.el) closeZoom(); });
    paintZoom();
    window.addEventListener("keydown", zoomKey, true);
  }
  function zoomKey(e){
    if(!zoom) return;
    if(e.key === "Escape"){ e.preventDefault(); e.stopPropagation(); closeZoom(); }
  }
  function closeZoom(){ if(!zoom) return; zoom.el.remove(); zoom = null; window.removeEventListener("keydown", zoomKey, true); }
  function paintZoom(){
    if(!zoom) return;
    const c = seq[zoom.i], ch = c.ch, tun = tuning(), vs = voicings(ch, tun), v = vs[zoom.v % Math.max(1, vs.length)];
    const names = ch.pcs.map(p => SH[p]).join(" · ") + (ch.bass != null && !ch.pcs.includes(ch.bass) ? ` (basa ${SH[ch.bass]})` : "");
    zoom.el.innerHTML = `<div class="cc-zbox" style="--h:${hue(ch.pc)}">
      <button type="button" class="cc-zx" data-a="x" title="Zavřít (Esc)">×</button>
      <div class="cc-zt"><span class="cc-zn">${esc(c.name)}</span><span class="cc-znotes">${esc(names)}</span></div>
      <div class="cc-zrow">
        <div class="cc-zcol"><h4>Kytara · ${esc(tun.map(STRN).join(" "))}</h4>
          ${v ? guitarSvg(ch, v, tun) : `<div class="cc-msg">Na tohle ladění hmat nenacházím.</div>`}
          ${vs.length > 1 ? `<div class="cc-zv"><button type="button" data-a="vp" title="Jiný hmat">‹</button><span>hmat ${zoom.v % vs.length + 1} z ${vs.length}</span><button type="button" data-a="vn" title="Jiný hmat">›</button></div>` : ""}
          <div class="cc-zplay"><button type="button" class="big" data-a="gtr">▶ Kytara</button><button type="button" data-a="garp" title="Struna po struně">Rozložit</button></div>
        </div>
        <div class="cc-zcol"><h4>Klavír</h4>${pianoSvg(ch)}
          <div class="cc-zplay"><button type="button" class="big" data-a="pno">▶ Klavír</button><button type="button" data-a="parp" title="Tón po tónu">Rozložit</button></div>
        </div>
      </div>
      <div class="cc-zf">
        <button type="button" data-a="prev" ${zoom.i > 0 ? "" : "disabled"}>‹ Předchozí akord</button>
        <button type="button" data-a="seek" title="Píseň skočí na tenhle akord">⏵ Přehrát píseň odsud</button>
        <button type="button" data-a="next" ${zoom.i < seq.length - 1 ? "" : "disabled"}>Další akord ›</button>
      </div></div>`;
    const on = (a, fn) => { const b = zoom.el.querySelector(`[data-a="${a}"]`); if(b) b.onclick = fn; };
    on("x", closeZoom);
    on("vp", () => { zoom.v = (zoom.v - 1 + vs.length) % vs.length; paintZoom(); });
    on("vn", () => { zoom.v = (zoom.v + 1) % vs.length; paintZoom(); });
    on("gtr", () => v && strum(v, tun, "strum"));
    on("garp", () => v && strum(v, tun, "arp"));
    on("pno", () => piano(ch, "chord"));
    on("parp", () => piano(ch, "arp"));
    on("prev", () => { zoom.i--; zoom.v = 0; paintZoom(); });
    on("next", () => { zoom.i++; zoom.v = 0; paintZoom(); });
    on("seek", () => { Mixer.seek(seq[zoom.i].start); closeZoom(); if(!Mixer.isPlaying()) Mixer.play(); });
    const g = zoom.el.querySelector(".cc-gtr"); if(g && v) g.onclick = () => strum(v, tun, "strum");
    const p = zoom.el.querySelector(".cc-pno"); if(p) p.onclick = () => piano(ch, "chord");
  }

  // ======================================================================
  // Played along: is it the chord?
  // ======================================================================
  // pcs heard / held → what is missing, what is too much (the fifth may be left out where asked)
  function verdict(ch, pcs, lenient){
    const need = new Set(ch.pcs), fifth = (ch.pc + 7) % 12;
    const missing = [...need].filter(p => !pcs.has(p) && !(lenient && p === fifth && need.size >= 3));
    const extra = [...pcs].filter(p => !need.has(p) && p !== ch.bass);
    return {ok: pcs.size > 0 && !missing.length && !extra.length, missing, extra};
  }
  // which chord the notes make — the best of the common ones
  const TPL = [["", [0, 4, 7]], ["m", [0, 3, 7]], ["7", [0, 4, 7, 10]], ["m7", [0, 3, 7, 10]], ["maj7", [0, 4, 7, 11]], ["sus4", [0, 5, 7]],
    ["sus2", [0, 2, 7]], ["5", [0, 7]], ["dim", [0, 3, 6]], ["aug", [0, 4, 8]], ["6", [0, 4, 7, 9]], ["m6", [0, 3, 7, 9]]];
  function nameOf(w /* weights by pc */, bassPc){
    const tot = w.reduce((a, b) => a + b, 0);
    if(tot <= 0) return "";
    const on = w.map(x => x / tot);
    let best = null;
    for(let r = 0; r < 12; r++) TPL.forEach(([q, iv]) => {
      const inT = new Set(iv.map(i => (r + i) % 12));
      let s = 0;
      for(let p = 0; p < 12; p++) s += inT.has(p) ? on[p] : -0.9 * on[p];
      const hit = iv.filter(i => on[(r + i) % 12] > 0.04).length;
      s -= (iv.length - hit) * 0.22;
      if(r === bassPc) s += 0.06;
      s -= iv.length * 0.012;
      if(!best || s > best.s) best = {s, name: SH[r] + q};
    });
    return best && best.s > 0.35 ? best.name : "";
  }
  // the chords got right while playing along: each chord counted once it is over
  const score = {keys: {got: 0, of: 0, hit: false}, gtr: {got: 0, of: 0, hit: false}};
  let tallyCi = -2;
  function tally(t){
    const ci = idxAt(seq, t);
    if(ci === tallyCi) return;
    const was = tallyCi; tallyCi = ci;
    if(was >= 0 && Mixer.isPlaying()) ["keys", "gtr"].forEach(k => { const on = k === "keys" ? kb.on : gl.on; if(!on) return;
      score[k].of++; if(score[k].hit) score[k].got++; });
    score.keys.hit = score.gtr.hit = false;
  }
  const scoreHtml = (k) => score[k].of ? `<span class="cc-score">trefeno <b>${score[k].got}</b> z ${score[k].of} (${Math.round(score[k].got / score[k].of * 100)} %)</span>` : `<span class="cc-score">skóre se počítá, když píseň hraje</span>`;
  function target(){
    const t = Mixer.currentTime(), ci = idxAt(seq, t);
    if(ci >= 0) return {c: seq[ci], ci, now: true};
    const n = nextAt(seq, t);
    return n >= 0 ? {c: seq[n], ci: n, now: false} : null;
  }
  function verdictHtml(tg, v, heardName, empty){
    if(!tg) return `<span class="cc-wait">Píseň tu nemá akord</span>`;
    let h = `<span class="cc-want" style="color:hsl(${hue(tg.c.ch ? tg.c.ch.pc : 0)} 85% 72%)">${esc(tg.c.name)}</span>`;
    if(empty) return h + `<span class="cc-wait">${tg.now ? "hraj" : "připrav se"}</span>`;
    h += v.ok ? `<span class="cc-ok">✓ správně</span>` : `<span class="cc-bad">✗ ještě ne</span>`;
    if(heardName) h += `<span class="cc-heard">${v.ok ? "" : "hraješ "}<b>${esc(heardName)}</b></span>`;
    if(!v.ok){
      if(v.missing.length) h += `<span class="cc-chip miss">chybí ${esc(v.missing.map(p => SH[p]).join(", "))}</span>`;
      if(v.extra.length) h += `<span class="cc-chip xtra">navíc ${esc(v.extra.map(p => SH[p]).join(", "))}</span>`;
    }
    return h;
  }
  function paintPanels(){
    if(!panels) return;
    const want = (kb.on ? "k" : "") + (gl.on ? "g" : "");
    if(panels.dataset.w !== want){
      panels.dataset.w = want;
      kb.sig = gl.sig = "";
      panels.innerHTML = (kb.on ? `<div class="cc-pn" data-p="keys"></div>` : "") + (gl.on ? `<div class="cc-pn" data-p="gtr"></div>` : "");
    }
    if(kb.on) paintKeys();
    if(gl.on) paintGtr();
  }

  // ---- the keys: a MIDI keyboard
  const kb = {on: false, access: null, held: new Map(), voices: new Map(), lo: 36, hi: 84, devs: [], sig: ""};
  async function toggleKeys(){
    if(kb.on){ kb.on = false; kb.held.clear(); kb.voices.forEach(r => r(audio().ac.currentTime)); kb.voices.clear(); paintToolState(); paintPanels(); return; }
    kb.on = true; paintToolState(); paintPanels();
    if(!navigator.requestMIDIAccess){ kb.err = "Tenhle prohlížeč MIDI neumí"; paintPanels(); return; }
    try{
      if(!kb.access){
        kb.access = await navigator.requestMIDIAccess();
        const hook = () => { kb.devs = []; kb.access.inputs.forEach(inp => { kb.devs.push(inp.name || "MIDI"); if(!inp._cc){ inp._cc = true; inp.addEventListener("midimessage", (e) => midi(e.data)); } }); paintPanels(); };
        kb.access.addEventListener("statechange", hook);
        hook();
      }
      kb.err = "";
    }catch(e){ kb.err = "MIDI se nepodařilo otevřít: " + (e.message || e); }
    paintPanels();
  }
  // a note from the keyboard (switches the pedal is bound to are the pedal's)
  function midi(data){
    if(!kb.on || (typeof Looper !== "undefined" && Looper.isOpen())) return;
    const [st, a, b] = data, kind = st & 0xf0, ch = (st & 0x0f) + 1;
    if(kind !== 0x90 && kind !== 0x80) return;
    try{ if(typeof Pedal !== "undefined" && Pedal.config && (Pedal.config().binds || []).some(x => x.key === `note:${ch}:${a}`)) return; }catch(e){}
    const on = kind === 0x90 && b > 0, {ac, out} = audio();
    if(on){
      kb.held.set(a, b / 127);
      if(a < kb.lo) kb.lo = Math.floor(a / 12) * 12;
      if(a > kb.hi) kb.hi = Math.ceil((a + 1) / 12) * 12;
      if(cfg.keysSound){
        const old = kb.voices.get(a); if(old) old(ac.currentTime);
        bank("piano").then(bufs => {
          if(!kb.held.has(a)) return;
          const r = bufs ? playBuf(ac, out, bufs, a, ac.currentTime, null, b / 127) : (typeof LoopSound !== "undefined" ? LoopSound.synth(ac, out, "syn:ep", a, b / 127, ac.currentTime) : null);
          if(r) kb.voices.set(a, r);
        });
      }
    } else {
      kb.held.delete(a);
      const r = kb.voices.get(a); if(r){ r(ac.currentTime); kb.voices.delete(a); }
    }
    judgeKeys();
    paintKeys();
  }
  let keysV = null;
  function judgeKeys(){
    const tg = target();
    const pcs = new Set([...kb.held.keys()].map(m => m % 12));
    keysV = tg && tg.c.ch ? verdict(tg.c.ch, pcs, false) : null;
    if(keysV && keysV.ok && tg.now) score.keys.hit = true;
  }
  function keysSvg(ch){
    const lo = kb.lo, hi = kb.hi, WH = [0, 2, 4, 5, 7, 9, 11];
    const whites = []; for(let m = lo; m <= hi; m++) if(WH.includes(m % 12)) whites.push(m);
    const ww = 18, H = 92, W = whites.length * ww, need = ch ? new Set(ch.pcs) : new Set();
    const state = (m) => !kb.held.has(m) ? "" : need.has(m % 12) || (ch && ch.bass === m % 12) ? " ok" : " bad";
    let g = "";
    whites.forEach((m, k) => { const x = k * ww;
      g += `<rect x="${x + 0.5}" y="0.5" width="${ww - 1}" height="${H - 1}" rx="2.5" class="kw${state(m)}"/>`;
      if(need.has(m % 12) && !kb.held.has(m)) g += `<circle cx="${x + ww / 2}" cy="${H - 10}" r="3.2" class="hint"/>`;
      if(m % 12 === 0) g += `<text x="${x + ww / 2}" y="${H + 10}" class="ko">C${Math.floor(m / 12) - 1}</text>`; });
    whites.forEach((m, k) => { const b = m + 1; if(b > hi || WH.includes(b % 12)) return; const x = (k + 1) * ww - 5.5;
      g += `<rect x="${x}" y="0" width="11" height="${H * 0.6}" rx="1.8" class="kb${state(b)}"/>`;
      if(need.has(b % 12) && !kb.held.has(b)) g += `<circle cx="${x + 5.5}" cy="${H * 0.6 - 8}" r="2.6" class="hint"/>`; });
    return `<svg class="cc-kb" viewBox="0 0 ${W} ${H + 14}" style="--h:${ch ? hue(ch.pc) : 230}">${g}</svg>`;
  }
  function paintKeys(){
    const pn = panels && panels.querySelector('[data-p="keys"]');
    if(!pn) return;
    const tg = target(), ch = tg && tg.c.ch;
    judgeKeys();
    const pcs = [...kb.held.keys()].sort((a, b) => a - b);
    const w = new Array(12).fill(0); pcs.forEach(m => { w[m % 12] += 1; });
    const named = pcs.length >= 2 ? nameOf(w, pcs.length ? pcs[0] % 12 : -1) : pcs.length ? SH[pcs[0] % 12] : "";
    const sig = [tg ? tg.ci : -1, pcs.join(","), kb.lo, kb.hi, kb.devs.join("|"), kb.err, cfg.keysSound, score.keys.got, score.keys.of].join(";");
    if(sig === kb.sig) return;
    kb.sig = sig;
    pn.innerHTML = `<div class="cc-pnh"><b>🎹 MIDI KLÁVESY</b><span>${kb.err ? esc(kb.err) : kb.devs.length ? esc(kb.devs.join(", ")) : "žádné klávesy nejsou připojené — připoj je a zahraj"}</span><span class="sp"></span>
        <button type="button" data-a="snd" class="${cfg.keysSound ? "on" : ""}" title="Klávesy hrají zvukem klavíru">🔊 zvuk</button>
        <button type="button" data-a="x" title="Zavřít">×</button></div>
      <div class="cc-pnb">${keysSvg(ch)}</div>
      <div class="cc-verdict">${verdictHtml(tg, keysV || {ok: false, missing: [], extra: []}, named, !pcs.length)}${scoreHtml("keys")}</div>`;
    pn.querySelector('[data-a="snd"]').onclick = () => { cfg.keysSound = !cfg.keysSound; keep(); kb.sig = ""; paintKeys(); };
    pn.querySelector('[data-a="x"]').onclick = () => toggleKeys();
  }

  // ---- the guitar: Pick's guitar input listened to
  const gl = {on: false, an: null, buf: null, timer: 0, unhook: null, res: null, at: 0, err: "", sig: "", chroma: new Float32Array(12)};
  async function toggleGtr(){
    if(gl.on){ stopGtr(); paintToolState(); paintPanels(); return; }
    gl.on = true; gl.err = ""; paintToolState(); paintPanels();
    await startGtr();
    paintPanels();
  }
  async function startGtr(){
    const G = Mixer.guitar;
    try{ if(!G.isLive()) await Mixer.voice.myGuitar(true); }catch(e){}
    if(!G.isLive()){ gl.err = "Kytara není zapnutá — připoj zvukovku a zapni vstup kytary ve Steems"; return false; }
    const {ac} = audio();
    if(!gl.an){ gl.an = ac.createAnalyser(); gl.an.fftSize = 16384; gl.an.smoothingTimeConstant = 0; gl.buf = new Float32Array(gl.an.fftSize); }
    const attach = () => { try{ const n = G.cleanTap(); if(n) n.connect(gl.an); }catch(e){} };
    attach();
    if(!gl.unhook && G.onRewire) gl.unhook = G.onRewire(attach);
    clearInterval(gl.timer);
    gl.timer = setInterval(listen, 90);
    gl.err = "";
    return true;
  }
  function stopGtr(){
    gl.on = false; clearInterval(gl.timer); gl.timer = 0;
    if(gl.unhook){ gl.unhook(); gl.unhook = null; }
    if(gl.an){ try{ Mixer.guitar.cleanTap().disconnect(gl.an); }catch(e){} }
    gl.res = null;
  }
  function listen(){
    if(!gl.on || !gl.an) return;
    gl.an.getFloatTimeDomainData(gl.buf);
    const r = analyze(gl.buf, gl.an.context.sampleRate);
    const now = performance.now();
    if(r.notes.length){ gl.res = r; gl.at = now; }
    else if(gl.res && now - gl.at > 700) gl.res = null;
    if(gl.res){ const tg = target(); if(tg && tg.now && tg.c.ch && verdict(tg.c.ch, new Set(gl.res.pcs), true).ok) score.gtr.hit = true; }
    paintGtr();
  }

  // ---- reading notes from the sound: the spectrum's peaks, the harmonics given back to their note
  let fftN = 0, cosT = null, sinT = null, rev = null;
  function fft(re, im){
    const n = re.length;
    if(n !== fftN){
      fftN = n; cosT = new Float64Array(n / 2); sinT = new Float64Array(n / 2); rev = new Uint32Array(n);
      for(let i = 0; i < n / 2; i++){ cosT[i] = Math.cos(2 * Math.PI * i / n); sinT[i] = -Math.sin(2 * Math.PI * i / n); }
      const bits = Math.log2(n);
      for(let i = 0; i < n; i++){ let r = 0, x = i; for(let b = 0; b < bits; b++){ r = (r << 1) | (x & 1); x >>= 1; } rev[i] = r; }
    }
    for(let i = 0; i < n; i++){ const j = rev[i]; if(j > i){ let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; } }
    for(let size = 2; size <= n; size <<= 1){
      const half = size >> 1, step = n / size;
      for(let i = 0; i < n; i += size) for(let j = 0, k = 0; j < half; j++, k += step){
        const a = i + j, b = a + half, tr = re[b] * cosT[k] - im[b] * sinT[k], ti = re[b] * sinT[k] + im[b] * cosT[k];
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
      }
    }
  }
  function analyze(x, sr){
    const N = x.length;
    let rms = 0; for(let i = 0; i < N; i++) rms += x[i] * x[i];
    rms = Math.sqrt(rms / N);
    const res = {rms, notes: [], pcs: [], name: "", single: null};
    if(rms < 0.002) return res;
    const re = new Float64Array(N), im = new Float64Array(N);
    for(let i = 0; i < N; i++) re[i] = x[i] * (0.5 - 0.5 * Math.cos(2 * Math.PI * i / (N - 1)));
    fft(re, im);
    const half = N >> 1, mag = new Float64Array(half);
    for(let k = 0; k < half; k++) mag[k] = Math.hypot(re[k], im[k]);
    const kLo = Math.max(2, Math.floor(65 * N / sr)), kHi = Math.min(half - 2, Math.ceil(1500 * N / sr));
    let max = 0; for(let k = kLo; k <= kHi; k++) if(mag[k] > max) max = mag[k];
    if(max <= 0) return res;
    let peaks = [];
    for(let k = kLo + 1; k < kHi; k++){
      if(mag[k] < max * 0.05 || mag[k] < mag[k - 1] || mag[k] <= mag[k + 1]) continue;
      const a = Math.log(mag[k - 1] + 1e-12), b = Math.log(mag[k] + 1e-12), c = Math.log(mag[k + 1] + 1e-12);
      const den = a - 2 * b + c, p = den ? 0.5 * (a - c) / den : 0;
      const f = (k + p) * sr / N, amp = Math.exp(b - 0.25 * (a - c) * p), mf = 69 + 12 * Math.log2(f / 440), m = Math.round(mf);
      peaks.push({f, amp, m, cents: Math.round((mf - m) * 100)});
    }
    peaks.sort((a, b) => b.amp - a.amp); peaks = peaks.slice(0, 28).sort((a, b) => a.f - b.f);
    // a peak a whole multiple of a lower note's is that note's harmonic: it goes to the note
    const notes = [];
    peaks.forEach(p => {
      const of = notes.find(q => { const r = p.f / q.f, k = Math.round(r); return k >= 2 && k <= 10 && Math.abs(r - k) / k < 0.012; });
      if(of){ of.str += p.amp * 0.6; return; }
      if(Math.abs(p.cents) > 42) return;
      const same = notes.find(q => q.m === p.m);
      if(same){ same.str += p.amp; return; }
      notes.push({m: p.m, f: p.f, cents: p.cents, amp: p.amp, str: p.amp});
    });
    const top = Math.max(0, ...notes.map(n => n.str));
    const kept = notes.filter(n => n.str >= top * 0.16 && n.m >= 36 && n.m <= 96);
    res.notes = kept.map(n => ({m: n.m, cents: n.cents, str: n.str / top}));
    const w = new Array(12).fill(0); kept.forEach(n => { w[n.m % 12] += n.str; });
    const wm = Math.max(...w);
    res.pcs = w.map((v, p) => v >= wm * 0.2 ? p : -1).filter(p => p >= 0);
    const tot = kept.reduce((a, n) => a + n.str, 0);
    if(kept.length === 1 || (kept.length && kept[0] && Math.max(...kept.map(n => n.str)) / tot > 0.82 && res.pcs.length === 1)){
      const n = kept.reduce((a, b) => b.str > a.str ? b : a);
      res.single = {m: n.m, cents: n.cents, name: SH[n.m % 12] + (Math.floor(n.m / 12) - 1)};
      res.name = res.single.name;
    } else {
      const low = kept.length ? kept[0].m % 12 : -1;
      res.name = nameOf(w, low);
    }
    return res;
  }

  // ---- where on the neck: on the shape when it is one of its notes, else the nearest place to it
  function place(notes, v, tun, ch){
    const out = [], used = new Set();
    const fr = v ? v.frets : tun.map(() => -1), live = fr.filter(f => f > 0);
    const lo = live.length ? Math.min(...live) : 0, hi = live.length ? Math.max(...live) : 4, mid = (lo + hi) / 2;
    notes.slice().sort((a, b) => b.str - a.str).forEach(n => {
      let pos = null;
      fr.forEach((f, s) => { if(!pos && f >= 0 && !used.has(s) && tun[s] + f === n.m) pos = {s, f, kind: "ok"}; });
      if(!pos){
        let best = null;
        tun.forEach((o, s) => { const f = n.m - o; if(f < 0 || f > 15 || used.has(s)) return;
          const d = Math.abs(f - mid) + (f >= lo - 1 && f <= hi + 2 ? 0 : 6);
          if(!best || d < best.d) best = {s, f, d}; });
        if(best) pos = {s: best.s, f: best.f, kind: ch && ch.pcs.includes(n.m % 12) ? "near" : "bad"};
      }
      if(pos){ used.add(pos.s); out.push({...pos, m: n.m}); }
    });
    return out;
  }
  function neckSvg(ch, v, tun, heard){
    const W = 900, H = 150, nutX = 46, right = W - 14, top = 22, gap = 20, S = tun.length, F = 15;
    const L = (right - nutX) / (1 - Math.pow(2, -F / 12)), fx = (f) => nutX + L * (1 - Math.pow(2, -f / 12));
    const y = (s) => top + (S - 1 - s) * gap, mid = (f) => f === 0 ? nutX - 16 : (fx(f - 1) + fx(f)) / 2;
    let g = `<defs><linearGradient id="cc-wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a2618"/><stop offset=".5" stop-color="#2b1b11"/><stop offset="1" stop-color="#22150d"/></linearGradient></defs>`;
    g += `<rect x="${nutX}" y="${top - 8}" width="${right - nutX}" height="${(S - 1) * gap + 16}" rx="4" class="wood"/>`;
    [3, 5, 7, 9, 15].forEach(f => { g += `<circle cx="${mid(f)}" cy="${top + (S - 1) * gap / 2}" r="5" class="inl"/>`; });
    g += `<circle cx="${mid(12)}" cy="${top + gap * 1.5}" r="5" class="inl"/><circle cx="${mid(12)}" cy="${top + gap * 3.5}" r="5" class="inl"/>`;
    for(let f = 1; f <= F; f++) g += `<line x1="${fx(f)}" y1="${top - 8}" x2="${fx(f)}" y2="${top + (S - 1) * gap + 8}" class="fr"/>`;
    g += `<rect x="${nutX - 5}" y="${top - 9}" width="6" height="${(S - 1) * gap + 18}" rx="2" class="nut"/>`;
    for(let s = 0; s < S; s++) g += `<line x1="${nutX - 4}" y1="${y(s)}" x2="${right}" y2="${y(s)}" class="st" stroke-width="${(2.6 - s * 0.36).toFixed(2)}"/><text x="${nutX - 26}" y="${y(s) + 3.5}" class="sn">${STRN(tun[s])}</text>`;
    [1, 3, 5, 7, 9, 12, 15].forEach(f => { g += `<text x="${mid(f)}" y="${H - 6}" class="fn">${f}</text>`; });
    if(v) v.frets.forEach((f, s) => { if(f < 0) return; const root = ch && (tun[s] + f) % 12 === ch.pc;
      g += `<circle cx="${mid(f)}" cy="${y(s)}" r="${root ? 9 : 8}" class="want${root ? " root" : ""}"/>`; });
    (heard || []).forEach(p => { g += `<circle cx="${mid(p.f)}" cy="${y(p.s)}" r="8.5" class="hd ${p.kind}"/><text x="${mid(p.f)}" y="${y(p.s) + 3.5}" class="ht">${esc(SH[p.m % 12].replace("b", "♭"))}</text>`; });
    return `<svg class="cc-nk" viewBox="0 0 ${W} ${H}" style="--h:${ch ? hue(ch.pc) : 230}">${g}</svg>`;
  }
  function paintGtr(){
    const pn = panels && panels.querySelector('[data-p="gtr"]');
    if(!pn) return;
    const tg = target(), ch = tg && tg.c.ch, tun = tuning(), v = ch ? voicings(ch, tun)[0] : null, r = gl.res;
    const heard = r ? place(r.notes, v, tun, ch) : [];
    const vd = r && ch ? verdict(ch, new Set(r.pcs), true) : {ok: false, missing: [], extra: []};
    const live = (() => { try{ return Mixer.guitar.isLive(); }catch(e){ return false; } })();
    const sig = [tg ? tg.ci : -1, tun.join(","), heard.map(p => p.s + ":" + p.f + p.kind).join(","), r ? r.name : "", r && r.single ? r.single.cents : "", gl.err, live, score.gtr.got, score.gtr.of].join(";");
    if(sig === gl.sig) return;
    gl.sig = sig;
    const label = r ? (r.single ? `${r.single.name} <small>${r.single.cents > 0 ? "+" : ""}${r.single.cents}¢</small>` : esc(r.name || "?")) : "";
    pn.innerHTML = `<div class="cc-pnh"><b>🎸 KYTARA</b><span>${gl.err ? esc(gl.err) : live ? "poslouchám vstup kytary · ladění " + esc(tun.map(STRN).join(" ")) : "kytara není zapnutá"}</span><span class="sp"></span>
        ${!live ? `<button type="button" data-a="on">Zapnout kytaru</button>` : ""}<button type="button" data-a="x" title="Přestat poslouchat">×</button></div>
      <div class="cc-pnb">${neckSvg(ch, v, tun, heard)}</div>
      <div class="cc-verdict">${tg ? verdictHtml(tg, vd, "", !r) + (r ? `<span class="cc-heard">slyším <b>${label}</b></span>` : "") : `<span class="cc-wait">Píseň tu nemá akord</span>`}${scoreHtml("gtr")}</div>`;
    const on = pn.querySelector('[data-a="on"]'); if(on) on.onclick = async () => { await startGtr(); gl.sig = ""; paintGtr(); };
    pn.querySelector('[data-a="x"]').onclick = () => toggleGtr();
  }

  // ======================================================================
  // Chords found here, from the song's own tracks, when the analysis has none (it broke, or the analyser
  // is not installed): the same recipe as analyze.py — chroma of the harmonic tracks, one estimate per
  // beat, matched against chord templates and smoothed (Viterbi) — so they sit on the song's beats.
  // ======================================================================
  const QUAL = [["", [0, 4, 7], 1], ["m", [0, 3, 7], 1], ["7", [0, 4, 7, 10], 0.9], ["maj7", [0, 4, 7, 11], 0.8], ["m7", [0, 3, 7, 10], 0.9],
    ["sus4", [0, 5, 7], 0.9], ["sus2", [0, 2, 7], 0.88], ["dim", [0, 3, 6], 0.9], ["aug", [0, 4, 8], 0.84], ["5", [0, 7], 0.9]];
  const NCP = 0.3, SELF = 0.15;
  const PN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  // one channel at a lower rate (a box filter is enough below 2 kHz), the tracks summed
  function monoOf(bufs, step){
    const len = Math.max(...bufs.map(b => b.length)), n = Math.floor(len / step), out = new Float32Array(n);
    bufs.forEach(b => { for(let c = 0; c < b.numberOfChannels; c++){ const d = b.getChannelData(c), k = 1 / (step * b.numberOfChannels);
      for(let i = 0, j = 0; i < n; i++){ let a = 0; for(let q = 0; q < step; q++, j++) a += d[j] || 0; out[i] += a * k; } } });
    return out;
  }
  // per frame: the spectrum's peaks, each whole multiple of a lower one given to it (a note's
  // overtones are not notes), the notes' strength summed by pitch class
  async function chromaOf(x, sr, fLo, fHi, N, hop){
    const frames = Math.max(0, Math.floor((x.length - N) / hop) + 1), out = new Array(frames);
    const win = new Float64Array(N); for(let i = 0; i < N; i++) win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (N - 1));
    const re = new Float64Array(N), im = new Float64Array(N), mag = new Float64Array(N / 2);
    const kLo = Math.max(2, Math.floor(fLo * N / sr)), kHi = Math.min(N / 2 - 2, Math.ceil(fHi * N / sr));
    for(let f = 0; f < frames; f++){
      const o = f * hop;
      for(let i = 0; i < N; i++){ re[i] = x[o + i] * win[i]; im[i] = 0; }
      fft(re, im);
      let max = 0;
      for(let k = kLo - 1; k <= kHi + 1; k++){ mag[k] = Math.hypot(re[k], im[k]); if(k >= kLo && k <= kHi && mag[k] > max) max = mag[k]; }
      const c = new Float32Array(12);
      if(max > 1e-7){
        const peaks = [];
        for(let k = kLo; k <= kHi; k++){
          if(mag[k] < max * 0.03 || mag[k] < mag[k - 1] || mag[k] <= mag[k + 1]) continue;
          const A = Math.log(mag[k - 1] + 1e-12), B = Math.log(mag[k] + 1e-12), C = Math.log(mag[k + 1] + 1e-12), den = A - 2 * B + C;
          const d = den ? 0.5 * (A - C) / den : 0;
          peaks.push({f: (k + d) * sr / N, a: Math.exp(B - 0.25 * (A - C) * d)});
        }
        const notes = [];
        peaks.forEach(pk => {
          const of = notes.find(q => { const r = pk.f / q.f, h = Math.round(r); return h >= 2 && h <= 8 && Math.abs(r - h) / h < 0.012; });
          if(of){ of.s += pk.a * 0.5; return; }
          const m = 69 + 12 * Math.log2(pk.f / 440), r = Math.round(m);
          if(Math.abs(m - r) > 0.4) return;
          notes.push({f: pk.f, pc: ((r % 12) + 12) % 12, s: pk.a});
        });
        notes.forEach(n => { c[n.pc] += Math.sqrt(n.s); });
      }
      out[f] = c;
      if(f % 300 === 299) await new Promise(r => setTimeout(r, 0));      // the page stays alive meanwhile
    }
    return out;
  }
  async function detectChords(tracks, beats, duration){
    const pick = (re) => tracks.filter(t => t.buffer && re.test(t.stem));
    let harm = pick(/^(guitar|piano|other)/);
    if(!harm.length) harm = tracks.filter(t => t.buffer && !/^(drums|vocals|bass|click)/.test(t.stem));
    if(!harm.length) return [];
    const sr0 = harm[0].buffer.sampleRate, step = Math.max(1, Math.round(sr0 / 11025)), sr = sr0 / step, N = 4096, hop = 1024;
    const ch = await chromaOf(monoOf(harm.map(t => t.buffer), step), sr, 65, 2100, N, hop);
    if(!ch.length) return [];
    // what rises above each frame's median is what means something; then sharpened
    ch.forEach(c => { const m = [...c].sort((a, b) => a - b)[6]; for(let p = 0; p < 12; p++){ const v = Math.max(0, c[p] - m); c[p] = v * v; } });
    const ft = (i) => (i * hop + N / 2) / sr;
    // one estimate per beat (or per half second without a grid)
    let b = (beats || []).filter(t => t < duration);
    if(b.length < 8){ b = []; for(let t = 0; t < duration; t += 0.5) b.push(t); }
    const spans = b.map((t, i) => [t, i + 1 < b.length ? b[i + 1] : duration]).filter(([a, e]) => e > a);
    let fi = 0;
    const beatC = spans.map(([a, e]) => {
      while(fi < ch.length && ft(fi) < a) fi++;
      const acc = new Float32Array(12); let n = 0;
      for(let j = fi; j < ch.length && ft(j) < e; j++, n++) for(let p = 0; p < 12; p++) acc[p] += ch[j][p];
      if(n) for(let p = 0; p < 12; p++) acc[p] /= n;
      return acc;
    });
    const energy = beatC.map(c => c.reduce((a, v) => a + v, 0)), med = energy.slice().sort((a, b) => a - b)[energy.length >> 1] || 0;
    const T = [];
    for(let r = 0; r < 12; r++) QUAL.forEach(([suf, iv, prior]) => {
      const v = new Float32Array(12).fill(-NCP); iv.forEach(i => { v[(r + i) % 12] = 1; });
      const nrm = Math.hypot(...v); for(let p = 0; p < 12; p++) v[p] = v[p] / nrm * prior;
      T.push({v, name: PN[r] + suf});
    });
    const scores = beatC.map(c => { const nrm = Math.max(1e-9, Math.hypot(...c)); return T.map(t => { let s = 0; for(let p = 0; p < 12; p++) s += t.v[p] * c[p] / nrm; return s; }); });
    // the path that changes chord least for the most fit
    const K = T.length, back = [], path = new Array(scores.length);
    let cost = scores[0].slice();
    for(let i = 1; i < scores.length; i++){
      let bi = 0; for(let k = 1; k < K; k++) if(cost[k] > cost[bi]) bi = k;
      const best = cost[bi], bk = new Int16Array(K), next = new Array(K);
      for(let k = 0; k < K; k++){ const stay = cost[k] + SELF; if(stay >= best){ bk[k] = k; next[k] = stay + scores[i][k]; } else { bk[k] = bi; next[k] = best + scores[i][k]; } }
      back.push(bk); cost = next;
    }
    let last = 0; for(let k = 1; k < K; k++) if(cost[k] > cost[last]) last = k;
    path[scores.length - 1] = last;
    for(let i = scores.length - 1; i > 0; i--) path[i - 1] = back[i - 1][path[i]];
    const out = [];
    spans.forEach(([a, e], i) => {
      const name = energy[i] > Math.max(1e-6, med * 0.22) ? T[path[i]].name : null, l = out[out.length - 1];
      if(l && l.chord === name){ l.end = e; l.beats.push(i); } else out.push({start: +a.toFixed(3), end: +e.toFixed(3), chord: name, bass: null, beats: [i]});
    });
    // a seventh only where it is heard as much as the chord's own notes (a guitar's overtones put a
    // major seventh into every open C): else the triad
    out.forEach(c => {
      const m = c.chord && /^([A-G]#?)(maj7|m7|7)$/.exec(c.chord);
      if(!m) return;
      const r = PN.indexOf(m[1]), avg = new Float32Array(12);
      c.beats.forEach(i => { for(let p = 0; p < 12; p++) avg[p] += beatC[i][p]; });
      const third = m[2] === "m7" ? 3 : 4, sev = m[2] === "maj7" ? 11 : 10;
      const tri = Math.min(avg[r], avg[(r + third) % 12], avg[(r + 7) % 12]);
      if(avg[(r + sev) % 12] < tri) c.chord = PN[r] + (m[2] === "m7" ? "m" : "");
    });
    out.forEach(c => { delete c.beats; });
    const clean = [];
    out.forEach(c => { c.end = +c.end.toFixed(3);
      if(c.end - c.start < 0.45 && clean.length && c.chord){ clean[clean.length - 1].end = c.end; return; }
      const l = clean[clean.length - 1]; if(l && l.chord === c.chord){ l.end = c.end; return; } clean.push(c); });
    return clean.filter(c => c.chord);
  }
  // a song open with no chords, once the analysis has had its say: they are found here
  const found = new Map();      // song → "busy" | "done" | "none"
  let autoMsg = "";
  async function autoChords(){
    if(typeof Mixer === "undefined" || !Mixer.deck || !Mixer.ready()) return;
    const song = Mixer.currentSong(), st = D().analysis();
    if(!song || st === "loading" || st === "idle" || found.has(song) || D().chords().length) return;
    found.set(song, "busy"); autoMsg = "Analýza akordy nenašla — hledám je přímo z nahrávky…";
    try{
      const G = D().grid(), tracks = D().audio(), dur = Math.max(0, ...tracks.map(t => t.buffer ? t.buffer.duration : 0));
      const list = await detectChords(tracks, G.beats, dur);
      if(Mixer.currentSong() !== song) { found.delete(song); return; }
      found.set(song, list.length ? "done" : "none");
      if(list.length){ D().useChords(list, song); seqSig = ""; laneSig = carSig = ""; }
      autoMsg = list.length ? "" : "Akordy se v nahrávce najít nepodařilo.";
    }catch(e){ console.error("chords:", e); found.set(song, "none"); autoMsg = "Akordy se v nahrávce najít nepodařilo."; }
    if(typeof SongDeck !== "undefined") SongDeck.paint();
  }
  setInterval(() => { try{ autoChords(); }catch(e){} }, 1500);

  // the tuner's tuning changed: the shapes follow it
  try{ MyTuning.onChange(() => { vcache.clear(); carSig = ""; paintTools(); }); }catch(e){}
  try{ Tunings.onChange(() => paintTools()); }catch(e){}

  return {mount, paint, parseChord, shapeOf, transpose, voicings, closeZoom, isZoomed: () => !!zoom,
    // for the tests
    _midi: (bytes) => midi(bytes), _analyze: analyze, _detect: detectChords, _found: (s) => found.get(s), _heard: () => lastHeard,
    _state: () => { const tg = target(); return {semis, target: tg && tg.c.name, now: tg && tg.now, held: [...kb.held.keys()], keys: keysV, gtr: gl.res,
      tuning: tuning(), score: JSON.parse(JSON.stringify(score)), cards: [...(car ? car.querySelectorAll(".cc-card") : [])].filter(n => !n.classList.contains("gone")).map(n => ({name: n.querySelector(".cc-cn").textContent, now: n.classList.contains("now"), x: n.getBoundingClientRect().left + n.getBoundingClientRect().width / 2, w: n.getBoundingClientRect().width}))}; }};
})();
