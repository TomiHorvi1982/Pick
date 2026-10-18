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
  .cc{margin:8px 14px 0;display:flex;flex-direction:column;gap:10px;--cc-acc:var(--accent,#8b5cf6);flex:0 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin}
  .cc > *{flex-shrink:0}
  #nm-stage .nm-stage-body{min-height:96px}
  .cc.off{display:none}
  /* the chords running past: a still line, the song moving under it */
  .cc-lane{position:relative;height:calc(66px * var(--cc-z,1));border-radius:14px;overflow:hidden;cursor:pointer;user-select:none;-webkit-user-select:none;
    background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.07);
    -webkit-mask-image:linear-gradient(90deg,transparent,#000 5%,#000 92%,transparent);mask-image:linear-gradient(90deg,transparent,#000 5%,#000 92%,transparent)}
  .cc-track{position:absolute;inset:0 auto 0 0;width:1px;will-change:transform}
  .cc-bar{position:absolute;top:0;bottom:0;border-left:1px solid rgba(255,255,255,.16)}
  .cc-bar i{position:absolute;left:4px;top:3px;font-style:normal;font-size:9.5px;color:#6f6f86;font-variant-numeric:tabular-nums}
  .cc-bt{position:absolute;bottom:4px;width:1px;height:7px;background:rgba(255,255,255,.18)}
  .cc-sec{position:absolute;top:2px;font-size:9.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--c);white-space:nowrap;margin-left:22px}
  .cc-pill{position:absolute;top:calc(19px * var(--cc-z,1));height:calc(32px * var(--cc-z,1));border-radius:10px;display:flex;align-items:center;padding:0 10px;box-sizing:border-box;overflow:hidden;
    background:linear-gradient(180deg,hsl(var(--h) 55% 30% / .85),hsl(var(--h) 55% 22% / .85));border:1px solid hsl(var(--h) 70% 60% / .45);
    color:#fff;transition:opacity .25s,filter .25s,box-shadow .25s}
  .cc-pill span{font-weight:800;font-size:calc(16px * var(--cc-z,1));white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.4)}
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
  .cc-tools .cc-lvl button{padding:5px 9px}
  .cc-tools .cc-lvl button.on{background:var(--cc-acc);color:#0b0b12;border-color:var(--cc-acc);font-weight:800}
  .cc-sheet{width:min(760px,94vw);display:flex;flex-direction:column;gap:12px}
  .cc-sheet-t{width:100%;min-height:260px;box-sizing:border-box;background:#0e0e14;color:#f1f1f6;border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:12px;
    font:13px/1.45 ui-monospace,Menlo,monospace;resize:vertical}
  .cc-sheet-r{display:flex;flex-direction:column;gap:6px;font-size:13px;min-height:20px}
  .cc-zbox button.big{background:var(--cc-acc);color:#0b0b12;border-color:transparent;font-weight:800;padding:9px 16px}
  .cc-tools .cc-grp b{min-width:42px;text-align:center;font-variant-numeric:tabular-nums;color:#fff}
  /* Live Show's sounds */
  .cc-pop{position:fixed;z-index:9650;background:#17171f;border:1px solid rgba(255,255,255,.1);border-radius:16px;padding:14px;color:#ececf3;font-size:13px;
    box-shadow:0 22px 60px rgba(0,0,0,.6);display:flex;flex-direction:column;gap:10px}
  .cc-pop-h{font-weight:800;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#a6a6bc}
  .cc-snd{display:flex;flex-direction:column;gap:6px;padding:10px;border-radius:12px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06)}
  .cc-snd-t{font-weight:800}.cc-snd-t small{font-weight:600;color:#8a8aa0;margin-left:6px}
  .cc-snd-r{display:flex;align-items:center;gap:6px;color:#c9c9da;font-size:12.5px}
  .cc-snd-r input[type=range]{flex:1}
  .cc-snd-r b{min-width:54px;text-align:right;font-variant-numeric:tabular-nums}
  .cc-pop button{font:inherit;font-size:12.5px;padding:6px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.06);color:#f1f1f6;cursor:pointer;white-space:nowrap}
  .cc-pop button:hover{background:rgba(255,255,255,.12)}
  .cc-pop button.on{background:var(--accent,#8b5cf6);color:#0b0b12;border-color:transparent;font-weight:700}
  .cc-snd-n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;text-align:left}
  .cc-note{font-size:11.5px;color:#8a8aa0}
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
  .cc-pnh{display:flex;align-items:center;gap:8px;font-size:12px;color:#a6a6bc;flex-wrap:wrap;row-gap:6px}
  .cc-pnh button{white-space:nowrap}
  .cc-pn[data-p="gtr"]{flex-basis:100%}
  .cc-pnh b{font-size:12.5px;color:#fff;letter-spacing:.04em}
  .cc-pnh .sp{flex:1}
  .cc-pnh button{padding:4px 9px;font-size:11.5px}
  .cc-pnh button.on{background:var(--cc-acc);color:#0b0b12;border-color:var(--cc-acc)}
  .cc-pnb svg{display:block;width:100%;height:auto}
  .cc-pnb svg.cc-kb{max-height:200px}
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
  /* the keys drawn: ivory and ebony, pressed ones lit */
  .cc-kb{touch-action:none;user-select:none;-webkit-user-select:none;filter:drop-shadow(0 6px 14px rgba(0,0,0,.45))}
  .cc-kb [data-m]{cursor:pointer;transition:fill .08s}
  .cc-kb .kw{fill:url(#cc-kw);stroke:#3a3a48;stroke-width:.8}.cc-kb .kb{fill:url(#cc-kbk);stroke:#000;stroke-width:.7}
  .cc-kb .kw:hover{fill:#f4f6ff}.cc-kb .kb:hover{fill:#2c2c38}
  .cc-kb .kw.down{fill:color-mix(in srgb,var(--cc-acc) 70%,#fff)}.cc-kb .kb.down{fill:var(--cc-acc)}
  .cc-kb .kw.last{fill:color-mix(in srgb,var(--cc-acc) 32%,#fff)}.cc-kb .kb.last{fill:color-mix(in srgb,var(--cc-acc) 55%,#111)}
  .cc-kb .kw.fret{fill:#ffe08a}.cc-kb .kb.fret{fill:#d8a72c}
  .cc-kb .kw.heard{fill:#b9f2d0}.cc-kb .kb.heard{fill:#2f8f5a}
  .cc-kb .kw.ok{fill:#3ddc84}.cc-kb .kb.ok{fill:#24b46a}.cc-kb .kw.bad{fill:#ff6b7a}.cc-kb .kb.bad{fill:#e04456}
  .cc-kb .kw.out{fill:#b9bbc6}.cc-kb .kb.out{fill:#3a3a44;opacity:.55}.cc-kb .out.lock{cursor:not-allowed}
  .cc-kb .kw.root{stroke:var(--cc-acc);stroke-width:2.2}.cc-kb .kb.root{stroke:var(--cc-acc);stroke-width:1.6}
  .cc-kb .hint{fill:hsl(var(--h) 80% 55%);pointer-events:none}.cc-kb .ko{fill:#9a9ab0;font:700 8.5px system-ui,sans-serif;text-anchor:middle;pointer-events:none}
  .cc-kb .cap{pointer-events:none}.cc-kb .cap rect{fill:#fff;stroke:#8a8a9a;stroke-width:.8}.cc-kb .cap text{fill:#222;font:800 8.5px system-ui,sans-serif;text-anchor:middle}
  .cc-kb .cap.d rect{fill:#2e2e38;stroke:#000}.cc-kb .cap.d text{fill:#f2f2f6}
  /* the neck drawn */
  .cc-nk{user-select:none;-webkit-user-select:none}
  .cc-nk .wood{fill:url(#cc-wood)}.cc-nk .fr{stroke:#b9b9c6;stroke-width:2}.cc-nk .nut{fill:#efe9da}
  .cc-nk .st{stroke:#e2ddd2}.cc-nk .inl{fill:rgba(255,255,255,.18)}.cc-nk .fn{fill:#6f6f86;font:700 10px system-ui,sans-serif;text-anchor:middle}
  .cc-nk .sn{fill:#9a9ab0;font:700 10px system-ui,sans-serif;text-anchor:end}
  .cc-nk .scd{fill:color-mix(in srgb,var(--cc-acc) 55%,transparent)}.cc-nk .scd.r{fill:var(--cc-acc);stroke:#fff;stroke-width:1}
  .cc-nk .want{fill:none;stroke:hsl(var(--h) 85% 65%);stroke-width:2.2;stroke-dasharray:4 3}
  .cc-nk .want.root{stroke-width:3}
  .cc-nk .me{stroke:#0b0b12;stroke-width:1.5}.cc-nk .me.tap{fill:#ffd166}.cc-nk .me.keys{fill:color-mix(in srgb,var(--cc-acc) 75%,#fff)}
  .cc-nk .hd{stroke:#0b0b12;stroke-width:1.5}.cc-nk .hd.ok{fill:#3ddc84}.cc-nk .hd.near{fill:#f5b942}.cc-nk .hd.bad{fill:#ff5a6a}
  .cc-nk .ht{fill:#0b0b12;font:800 10px system-ui,sans-serif;text-anchor:middle;pointer-events:none}
  .cc-nk .cell{fill:transparent;cursor:pointer}.cc-nk .cell:hover{fill:rgba(255,255,255,.08)}.cc-nk .cell.dis{cursor:not-allowed}.cc-nk .cell.dis:hover{fill:rgba(255,80,90,.08)}
  /* the chord wheel */
  .cc-pn.wheel{flex:0 1 520px}
  .cc-whb{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  .cc-wh{width:210px;height:210px;flex:0 0 auto;user-select:none}
  .cc-wh .seg{cursor:pointer;fill:hsl(var(--h) 35% 22%);stroke:#0b0b12;stroke-width:1.5;transition:fill .15s}
  .cc-wh .seg.mn{fill:hsl(var(--h) 30% 16%)}
  .cc-wh .seg:hover{fill:hsl(var(--h) 45% 32%)}
  .cc-wh .seg.key{stroke:hsl(var(--h) 70% 65%);stroke-dasharray:3 2}
  .cc-wh .seg.song{fill:hsl(var(--h) 70% 45%)}
  .cc-wh .seg.sel{fill:hsl(var(--h) 80% 58%)}
  .cc-wh .wt{fill:#e6e6f0;font:800 11px system-ui,sans-serif;text-anchor:middle;dominant-baseline:middle;pointer-events:none}
  .cc-wh .wt.s{font-size:8.5px;fill:#b4b4c8}.cc-wh .wt.on{fill:#0b0b12}
  .cc-wh .wt.big{font-size:15px;fill:#fff}.cc-wh .wt.tiny{font-size:7.5px;fill:#8a8aa0;font-weight:700}
  .cc-wh .mid{fill:#121219;stroke:#2a2a36;cursor:pointer}
  .cc-whs{flex:1;min-width:200px;display:flex;flex-direction:column;gap:8px}
  .cc-whq{display:flex;flex-wrap:wrap;gap:4px}
  .cc-whq button.on{background:var(--cc-acc);color:#0b0b12;border-color:var(--cc-acc);font-weight:700}
  .cc-whn{font-size:12.5px;color:#c9c9da}.cc-whn b{font-size:20px;color:#fff;margin-right:6px}
  .cc-whp{display:flex;gap:6px;flex-wrap:wrap}
  .cc-grp{display:inline-flex;align-items:center;gap:4px;padding:2px 4px 2px 8px;border-radius:11px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .cc-grp select{padding:4px 6px!important}
  .cc-pnh .cc-grp button,.cc-pnh select{padding:4px 8px;font-size:11.5px;border-radius:8px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.06);color:#f1f1f6;font:inherit;font-size:11.5px}
  .cc-oct{font-size:11px;color:#c9c9da;min-width:22px;text-align:center;font-variant-numeric:tabular-nums}
  .cc-dev{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cc-free{padding:3px 9px;border-radius:8px;background:rgba(255,255,255,.07);color:#c9c9da;font-weight:700;font-size:11.5px;text-transform:uppercase;letter-spacing:.08em}
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
  const cfg = {tuning: "", keysSound: true, pc: true, pcMode: "chroma", hints: false, zoom: 1, level: "basic"};
  try{ const v = JSON.parse(localStorage.getItem(KEY) || "{}"); if(typeof v.tuning === "string") cfg.tuning = v.tuning;
    if(v.zoom >= 0.7 && v.zoom <= 2.5) cfg.zoom = v.zoom;
    if(["basic", "advanced", "master"].includes(v.level)) cfg.level = v.level;
    ["keysSound", "pc", "hints"].forEach(k => { if(k in v) cfg[k] = !!v[k]; }); if(v.pcMode === "scale") cfg.pcMode = "scale"; }catch(e){}
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
  // Sound: a guitar strummed, a piano — Pick's instruments (the keyboard tool's), else its plucked string and keys
  // ======================================================================
  const audio = () => { const o = Mixer.voiceOut(); if(o.ac.state === "suspended") o.ac.resume(); return o; };
  let lastHeard = null;     // what was played last (tests)
  // a shape strummed down (or picked string by string), low to high
  async function strum(v, tun, how){
    const notes = [];
    v.frets.forEach((f, s) => { if(f >= 0) notes.push({s, f, m: tun[s] + f}); });
    lastHeard = {kind: "guitar", notes: notes.map(x => x.m)};
    const gap = how === "arp" ? 240 : 32;
    notes.forEach((x, k) => setTimeout(() => voice(x.m, 0.75, true).then(stop => stop && setTimeout(stop, 2400 - k * gap)), k * gap));
  }
  async function piano(ch, how){
    const notes = pianoNotes(ch);
    lastHeard = {kind: "piano", notes};
    const gap = how === "arp" ? 220 : 0;
    notes.forEach((m, k) => setTimeout(() => voice(m, 0.7, false).then(stop => stop && setTimeout(stop, 2200)), k * gap));
  }

  // ======================================================================
  // How many chords to show: Základní (one per bar, the plain chord, the song's own progression),
  // Pokročilé (up to two per bar, sevenths and suspensions kept), Mistr (everything the analysis heard).
  //   1. the song is cut into units: bars (Základní) or half bars (Pokročilé), from its downbeats;
  //   2. each unit gets the chord that sounds longest in it (Základní names it plainly: Am7 → Am, Gsus4 → G);
  //   3. the key is found from what sounds the longest (the 24 keys' own chords scored by their time), and
  //      a chord outside the key that lasts a single unit is a passing one — it goes (Základní);
  //   4. a part that comes back (every chorus, every verse) is voted: at each bar of it, the chord most of its
  //      repeats agree on wins — one chorus, the same four chords each time;
  //   5. the same chord in a row is one.
  // ======================================================================
  const LEVELS = [["basic", "Základní"], ["advanced", "Pokročilé"], ["master", "Mistr"]];
  const lvlOk = (v) => LEVELS.some(x => x[0] === v);
  function plainName(name, level){
    const ch = parseChord(name);
    if(!ch) return name;
    const q = ch.q, r = SH[ch.pc];
    if(level === "basic"){
      if(/^dim/.test(q) || q === "m7b5") return r + "dim";
      if(/^m(?!aj)/.test(q)) return r + "m";
      return r;
    }
    const keep = {"": "", m: "m", "7": "7", maj7: "maj7", m7: "m7", sus4: "sus4", sus2: "sus2", dim: "dim", aug: "aug", "5": "5", "7sus4": "7sus4",
      "6": "", m6: "m", "9": "7", m9: "m7", add9: "", dim7: "dim", m7b5: "m7b5"};
    const nq = q in keep ? keep[q] : (/^m(?!aj)/.test(q) ? "m" : "");
    return r + nq + (ch.bass != null && ch.bass !== ch.pc ? "/" + SH[ch.bass] : "");
  }
  // the key: the 24 keys' six chords (I ii iii IV V vi), each scored by how long its chords sound
  function keyOf(list){
    const dur = new Map();
    list.forEach(c => { const n = plainName(c.chord, "basic"); dur.set(n, (dur.get(n) || 0) + (c.end - c.start)); });
    let best = null;
    for(let r = 0; r < 12; r++){
      const set = new Set([SH[r], SH[(r + 5) % 12], SH[(r + 7) % 12], SH[(r + 9) % 12] + "m", SH[(r + 2) % 12] + "m", SH[(r + 4) % 12] + "m"]);
      let sc = 0; dur.forEach((d, n) => { if(set.has(n)) sc += d; });
      if(!best || sc > best.sc) best = {sc, root: r, set};
    }
    return best;
  }
  function simplify(list, o){
    const level = o.level || "basic";
    list = (list || []).filter(c => c.chord).map(c => ({start: c.start, end: c.end, chord: c.bass && !/\//.test(c.chord) ? c.chord + "/" + c.bass : c.chord}));
    if(level === "master" || !list.length) return list;
    const end = Math.max(...list.map(c => c.end));
    // the units: bars, or half bars
    let db = (o.downbeats || []).slice();
    const beats = o.beats || [], per = o.perBar || 4;
    if(!db.length && beats.length) db = beats.filter((_, k) => k % per === 0);
    const units = [];
    if(db.length > 2){
      db.forEach((a, k) => {
        const e = db[k + 1] != null ? db[k + 1] : Math.min(end, a + (a - db[k - 1]));
        if(e <= a) return;
        if(level === "advanced"){ const m = beats.find(x => x > a + (e - a) * 0.4 && x < a + (e - a) * 0.6) || (a + e) / 2; units.push([a, m], [m, e]); }
        else units.push([a, e]);
      });
      if(db[0] > 0.5) units.unshift([0, db[0]]);
    } else { const step = level === "basic" ? 2 : 1; for(let t = 0; t < end; t += step) units.push([t, Math.min(end, t + step)]); }
    // 2. the longest chord in each unit
    let pick = units.map(([a, e]) => {
      const w = new Map();
      list.forEach(c => { const ov = Math.min(e, c.end) - Math.max(a, c.start); if(ov > 0){ const n = plainName(c.chord, level); w.set(n, (w.get(n) || 0) + ov); } });
      let top = null, tot = 0; w.forEach((d, n) => { tot += d; if(!top || d > top.d) top = {n, d}; });
      return top && top.d > (e - a) * 0.2 ? {n: top.n, share: top.d / Math.max(1e-6, tot)} : {n: null, share: 0};
    });
    // 3. a passing chord outside the key, one unit long, goes to its neighbour
    const key = keyOf(list), inKey = (n) => !n || key.set.has(plainName(n, "basic"));
    if(level === "basic") pick.forEach((u, i) => {
      if(!u.n || inKey(u.n)) return;
      const prev = pick[i - 1], next = pick[i + 1];
      if((!prev || prev.n !== u.n) && (!next || next.n !== u.n)) u.n = (prev && prev.n) || (next && next.n) || u.n;
    });
    // 4. the parts that come back, voted bar by bar
    const secs = (o.sections || []).filter(x => x.kind && x.kind !== "part");
    const byKind = new Map();
    secs.forEach(x => { if(!byKind.has(x.kind)) byKind.set(x.kind, []); byKind.get(x.kind).push(x); });
    byKind.forEach(arr => {
      if(arr.length < 2) return;
      const idx = arr.map(x => units.map((u, i) => (u[0] + u[1]) / 2 >= x.start && (u[0] + u[1]) / 2 < x.end ? i : -1).filter(i => i >= 0));
      const len = Math.max(...idx.map(a => a.length));
      for(let k = 0; k < len; k++){
        const votes = new Map(); let n = 0;
        idx.forEach(a => { const u = pick[a[k]]; if(u && u.n){ votes.set(u.n, (votes.get(u.n) || 0) + 1); n++; } });
        let top = null; votes.forEach((v, c) => { if(!top || v > top.v) top = {c, v}; });
        if(!top || n < 2) continue;
        idx.forEach(a => { const u = pick[a[k]]; if(!u || !u.n || u.n === top.c) return;
          if(level === "basic" ? top.v / n >= 0.5 : (top.v / n >= 0.6 && u.share < 0.6)) u.n = top.c; });
      }
    });
    // 5. one chord as long as it lasts
    const out = [];
    units.forEach(([a, e], i) => { const n = pick[i].n; if(!n) return; const l = out[out.length - 1];
      if(l && l.chord === n && Math.abs(l.end - a) < 0.05) l.end = e; else out.push({start: +a.toFixed(3), end: +e.toFixed(3), chord: n, bass: null}); });
    return out;
  }
  const levelNow = () => cfg.level;
  function setLevel(v){
    if(!lvlOk(v) || v === cfg.level) return;
    cfg.level = v; keep(); seqSig = ""; laneSig = carSig = "";
    window.dispatchEvent(new Event("pick-chord-level"));
    paintTools(); paint();
  }

  // ======================================================================
  // The song's chords from a chord sheet: chords copied from a page (Ultimate Guitar, Supermusic, Velký
  // zpěvník, a ChordPro file…) are laid onto the recording. The sheet gives the right chords, the
  // recording where they fall: for each half bar the sheet's chord whose notes best match what sounds
  // there, in the sheet's order (a step on, or the same chord on; a chord skipped costs). With the parts
  // named in the sheet ([Refrén], Verse 1…) each part of the song takes the sheet's part of its kind, its
  // chords round and round. The sheet may be in another key (a capo): all twelve are tried.
  // ======================================================================
  const KIND = [[/^(intro|úvod|uvod)/i, "intro"], [/^(verse|sloka|strofa|vers|\d+\.?\s*$)/i, "verse"], [/^(pre-?chorus|předrefrén|predrefren|pre)/i, "prechorus"],
    [/^(chorus|refrén|refren|ref|r\.?$)/i, "chorus"], [/^(bridge|most|middle)/i, "bridge"], [/^(solo)/i, "solo"], [/^(outro|závěr|zaver|coda|konec|dohra)/i, "outro"],
    [/^(inst|mezihra|interlude)/i, "inst"]];
  const CHORD_TOKEN = /^\(?([A-H])([#b♯♭]|is|es|s)?((?:maj|min|m|dim|aug|sus|add|M)?\d*(?:sus\d)?(?:add\d+)?(?:b5|#5|b9|#9)?)(?:\/([A-H][#b♯♭]?))?\)?$/;
  function parseSheet(text){
    const german = /(^|[\s\[(|])H(m|7|maj|sus|dim|\s|$|\]|\/)/m.test(text);       // Czech/German sheets: H = B, B = Bb
    const fix = (tok) => { const m = CHORD_TOKEN.exec(tok); if(!m) return null;
      let r = m[1], acc = {"♯": "#", "♭": "b", is: "#", es: "b", s: "b"}[m[2]] || m[2] || "", bass = m[4] || "";
      if(german){ if(r === "H") r = "B"; else if(r === "B" && !acc) { r = "B"; acc = "b"; } if(bass === "H") bass = "B"; else if(bass === "B") bass = "Bb"; }
      let q = m[3].replace(/^min/, "m").replace(/^M7/, "maj7");
      return r + acc + q + (bass ? "/" + bass : ""); };
    const sections = [];
    let cur = {kind: "", label: "", chords: []};
    const push = () => { if(cur.chords.length) sections.push(cur); };
    text.split(/\r?\n/).forEach(line => {
      const raw = line.trim();
      if(!raw) return;
      // a header: [Chorus], Refrén:, Sloka 2, R:, 1.
      const label = raw.replace(/^[\[{(]\s*|\s*[\]})]\s*$/g, "").replace(/:\s*$/, "");
      const kindOf = (s) => { for(const [re, k] of KIND) if(re.test(s.trim())) return k; return ""; };
      const bracket = (/^\[[^\]]+\]$/.test(raw) && !fix(label)) || /^[A-Za-zÁ-ž .\-]+\s*\d*\s*:$/.test(raw) || /^(R|Ref|S|Sl)\s*\d*[.:]$/i.test(raw);
      if(bracket || (kindOf(label) && label.split(/\s+/).length <= 3 && !label.split(/\s+/).some(t => fix(t)))){
        push(); cur = {kind: kindOf(label), label, chords: []}; return;
      }
      // chords in brackets inside lyrics (ChordPro): [Am]text
      const inline = [...raw.matchAll(/\[([^\]]+)\]/g)].map(m => fix(m[1].trim())).filter(Boolean);
      if(inline.length){ cur.chords.push(...inline); return; }
      // a chord line: most of its words are chords (the rest: |, x2, -, (…))
      const toks = raw.split(/[\s|]+/).filter(Boolean);
      let rep = 1;
      const words = toks.filter(t => { const r = /^[x×]?(\d)[x×]?$/i.exec(t.replace(/[()]/g, "")); if(r && /[x×]/i.test(t)){ rep = +r[1]; return false; } return !/^[-–—/.:%]+$/.test(t); });
      const chords = words.map(fix);
      const hit = chords.filter(Boolean).length;
      if(hit && hit / words.length >= 0.7){ const line = chords.filter(Boolean); for(let k = 0; k < Math.min(rep, 8); k++) cur.chords.push(...line); }
    });
    push();
    return {sections, flat: sections.flatMap(x => x.chords), german};
  }
  // what sounds in each unit of the song: the detected chords' notes, weighted by time
  function profiles(raw, units){
    return units.map(([a, e]) => {
      const p = new Float32Array(12);
      raw.forEach(c => { const ov = Math.min(e, c.end) - Math.max(a, c.start); if(ov <= 0) return; const ch = parseChord(c.chord); if(!ch) return;
        ch.pcs.forEach(pc => { p[pc] += ov * (pc === ch.pc ? 1.3 : 1); }); });
      return p;
    });
  }
  function costOf(name, p){
    const ch = parseChord(name);
    let tot = 0; for(let i = 0; i < 12; i++) tot += p[i] * p[i];
    if(!ch || tot <= 0) return 0.5;
    let dot = 0, tn = 0;
    ch.pcs.forEach(pc => { const w = pc === ch.pc ? 1.3 : 1; dot += w * p[pc]; tn += w * w; });
    return 1 - dot / Math.sqrt(tot * tn);
  }
  // the sheet's chords (in order) onto the units: Viterbi over the position in the sheet
  function alignSeq(seq, prof, cyclic){
    const L = seq.length, U = prof.length, CH = 0.12, SKIP = 0.9;
    if(!L || !U) return {cost: Infinity, path: []};
    const cost = seq.map(n => prof.map(p => costOf(n, p)));
    let prev = new Float64Array(L).fill(Infinity);
    for(let p = 0; p < L; p++) prev[p] = cost[p][0] + p * SKIP;
    const back = [];
    for(let u = 1; u < U; u++){
      const cur = new Float64Array(L).fill(Infinity), bk = new Int32Array(L);
      for(let p = 0; p < L; p++){
        let b = prev[p], bi = p;                                                  // the same chord on
        if(p >= 1 && prev[p - 1] + CH < b){ b = prev[p - 1] + CH; bi = p - 1; }   // the next one
        if(p >= 2 && prev[p - 2] + CH + SKIP < b){ b = prev[p - 2] + CH + SKIP; bi = p - 2; }
        if(cyclic && p === 0 && prev[L - 1] + CH < b){ b = prev[L - 1] + CH; bi = L - 1; }
        cur[p] = b + cost[p][u]; bk[p] = bi;
      }
      back.push(bk); prev = cur;
    }
    let last = 0;
    for(let p = 1; p < L; p++){ const pen = cyclic ? 0 : (L - 1 - p) * SKIP; if(prev[p] + pen < prev[last] + (cyclic ? 0 : (L - 1 - last) * SKIP)) last = p; }
    const path = new Array(U); path[U - 1] = last;
    for(let u = U - 1; u > 0; u--) path[u - 1] = back[u - 1][path[u]];
    return {cost: prev[last] / U, path};
  }
  function fromSheet(text){
    const sh = parseSheet(text);
    if(sh.flat.length < 2) return {error: "V textu nenacházím akordy — vlož řádky s akordy (třeba z Ultimate Guitar nebo Supermusic)."};
    const raw = D().chords(), G = grid();
    let secs = [];
    try{ secs = Mixer.sectionsNow ? Mixer.sectionsNow() : []; }catch(e){}
    const end = Math.max(Mixer.duration() || 0, ...raw.map(c => c.end));
    const units = [];
    let db = G.db.length > 2 ? G.db : [];
    if(db.length){ db.forEach((a, k) => { const e = db[k + 1] != null ? db[k + 1] : Math.min(end, a + (a - db[k - 1])); if(e <= a) return;
      const m = G.beats.find(x => x > a + (e - a) * 0.4 && x < a + (e - a) * 0.6) || (a + e) / 2; units.push([a, m], [m, e]); });
      if(db[0] > 0.5) units.unshift([0, db[0]]); }
    else for(let t = 0; t < end; t += 1) units.push([t, Math.min(end, t + 1)]);
    const prof = profiles(raw, units);
    // by parts when both the sheet and the song name them
    const named = sh.sections.filter(x => x.kind), songParts = secs.filter(x => x.kind && x.kind !== "part");
    const byParts = named.length >= 2 && songParts.length >= 2 && songParts.filter(x => named.some(y => y.kind === x.kind)).length >= songParts.length / 2;
    let best = null;
    for(let sft = 0; sft < 12; sft++){
      const tr = (n) => transpose(n, sft);
      const names = new Array(units.length).fill(null);
      let total = 0;
      if(byParts){
        const seen = {};
        songParts.forEach(x => {
          const k = seen[x.kind] = (seen[x.kind] || 0) + 1, cand = named.filter(y => y.kind === x.kind);
          const part = cand[Math.min(k, cand.length) - 1];
          const ui = units.map((u, i) => (u[0] + u[1]) / 2 >= x.start && (u[0] + u[1]) / 2 < x.end ? i : -1).filter(i => i >= 0);
          if(!part || !ui.length) return;
          const seq = part.chords.map(tr), r = alignSeq(seq, ui.map(i => prof[i]), true);
          ui.forEach((i, j) => { names[i] = seq[r.path[j]]; });
          total += r.cost * ui.length;
        });
        // what no named part covers: the whole sheet, in order
        const rest = units.map((_, i) => names[i] == null ? i : -1).filter(i => i >= 0);
        if(rest.length){ const seq = sh.flat.map(tr), r = alignSeq(seq, rest.map(i => prof[i]), false); rest.forEach((i, j) => { names[i] = seq[r.path[j]]; }); total += r.cost * rest.length; }
      } else {
        const seq = sh.flat.map(tr), r = alignSeq(seq, prof, false);
        r.path.forEach((p, i) => { names[i] = seq[p]; }); total = r.cost * units.length;
      }
      if(!best || total < best.total) best = {total, names, sft};
    }
    // silence in the recording stays silent
    const out = [];
    units.forEach(([a, e], i) => { let n = best.names[i]; if(!n) return; if(prof[i].every(v => v === 0)) n = null; if(!n) return;
      const l = out[out.length - 1]; if(l && l.chord === n && Math.abs(l.end - a) < 0.05) l.end = e; else out.push({start: +a.toFixed(3), end: +e.toFixed(3), chord: n}); });
    const list = out.map(c => { const m = /^([^/]+)(?:\/(.+))?$/.exec(c.chord); return {start: c.start, end: c.end, chord: m[1], bass: m[2] || null}; });
    return {list, shift: best.sft > 6 ? best.sft - 12 : best.sft, fit: Math.max(0, Math.round((1 - best.total / units.length) * 100)), parts: byParts, count: sh.flat.length, german: sh.german};
  }
  function openSheet(){
    closeZoom();
    const song = Mixer.currentSong();
    if(!song) return;
    const el = document.createElement("div");
    el.className = "cc-zoom fresh";
    el.innerHTML = `<div class="cc-zbox cc-sheet" style="--h:230">
      <button type="button" class="cc-zx" data-a="x" title="Zavřít (Esc)">×</button>
      <div class="cc-zt"><span class="cc-zn" style="font-size:30px">Akordy z listu</span></div>
      <div class="cc-note" style="font-size:13px;line-height:1.5">Najdi píseň na stránce s akordy (Ultimate Guitar, Supermusic, Velký zpěvník, Chordify…), označ celý text s akordy, zkopíruj (⌘C) a vlož sem (⌘V).
        Pick akordy z listu položí na nahrávku: kde který zní, pozná podle toho, co je slyšet. Když list části pojmenuje (Refrén, Sloka, [Chorus]…), každá část písně vezme svoje akordy.
        Jiná tónina (kapodastr) nevadí — zkusí všech 12. Česká H/B se pozná sama.</div>
      <textarea class="cc-sheet-t" placeholder="[Verse 1]&#10;Bm        G&#10;A city overflowing...&#10;&#10;[Chorus]&#10;D    A    Bm   G"></textarea>
      <div class="cc-sheet-r"></div>
      <div class="cc-zf"><button type="button" data-a="try" class="big">Položit na nahrávku</button><button type="button" data-a="save" disabled>Uložit jako akordy písně</button>
        <button type="button" data-a="reset" title="Smazat vlastní akordy a vrátit ty z analýzy">↺ Akordy z analýzy</button></div></div>`;
    (box && box.closest("#nm-stage")) ? box.closest("#nm-stage").appendChild(el) : document.body.appendChild(el);
    zoom = {el, items: [], i: 0, v: 0, song: false};
    window.addEventListener("keydown", zoomKey, true);
    const ta = el.querySelector("textarea"), res = el.querySelector(".cc-sheet-r"), save = el.querySelector('[data-a="save"]');
    ta.addEventListener("keydown", (e) => e.stopPropagation());
    let got = null;
    el.querySelector('[data-a="x"]').onclick = closeZoom;
    el.addEventListener("pointerdown", (e) => { if(e.target === el) closeZoom(); });
    el.querySelector('[data-a="try"]').onclick = () => {
      got = fromSheet(ta.value);
      if(got.error){ res.innerHTML = `<span class="cc-bad">${esc(got.error)}</span>`; save.disabled = true; return; }
      const names = [...new Set(got.list.map(c => c.chord + (c.bass ? "/" + c.bass : "")))];
      res.innerHTML = `<span class="cc-ok">✓ ${got.count} akordů z listu → ${got.list.length} úseků v písni</span>
        <span class="cc-heard">shoda s nahrávkou <b>${got.fit} %</b>${got.shift ? ` · list posunut o ${got.shift > 0 ? "+" : "−"}${Math.abs(got.shift)} (jiná tónina / kapodastr)` : ""}${got.parts ? " · po částech" : ""}${got.german ? " · české H/B" : ""}</span>
        <div class="cc-heard">akordy: <b>${esc(names.slice(0, 16).join(" "))}${names.length > 16 ? " …" : ""}</b></div>`;
      save.disabled = !got.list.length;
    };
    save.onclick = async () => {
      if(!got || !got.list.length) return;
      try{
        const r = await fetch(`/api/stems/${encodeURIComponent(song)}/chords`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify({chords: got.list})});
        if(!r.ok) throw new Error(r.status);
        D().useChords(got.list, song);
        try{ localStorage.setItem("pick-chords-src:" + song, "sheet"); }catch(e){}
        seqSig = laneSig = carSig = "";
        showToast("Akordy z listu uložené k písni — v Mistrovi přesně, v Základních jen hlavní");
        closeZoom(); paint();
      }catch(e){ showToast("Akordy se nepodařilo uložit"); }
    };
    el.querySelector('[data-a="reset"]').onclick = async () => {
      try{ await fetch(`/api/stems/${encodeURIComponent(song)}/chords`, {method: "DELETE"}); }catch(e){}
      try{ localStorage.removeItem("pick-chords-src:" + song); }catch(e){}
      await D().reloadChords(); seqSig = laneSig = carSig = "";
      showToast("Akordy z analýzy jsou zpátky"); closeZoom(); paint();
    };
    setTimeout(() => ta.focus(), 50);
  }

  // ======================================================================
  // The song's chords, where it sounds now
  // ======================================================================
  let seq = [], seqSig = "", semis = 0;
  function chords(){
    let raw = [], p = 0;
    try{ raw = D().chords(); p = D().pitch ? D().pitch() : 0; }catch(e){}
    const sig = (Mixer.currentSong() || "") + ":" + raw.length + ":" + (raw.length ? raw[0].start + ":" + raw[raw.length - 1].end : "") + ":" + p + ":" + cfg.level;
    if(sig === seqSig) return seq;
    seqSig = sig; semis = p;
    if(cfg.level !== "master"){
      const G = grid(); let secs = [];
      try{ secs = Mixer.sectionsNow ? Mixer.sectionsNow() : []; }catch(e){}
      raw = simplify(raw, {level: cfg.level, beats: G.beats, downbeats: G.db, perBar: G.per, sections: secs});
    }
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
    box.style.setProperty("--cc-z", cfg.zoom);
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
    const k = (box.closest("#nm-stage") && document.querySelector("#nm-stage .nm-stage-body.duo") ? 0.62 : window.innerHeight < 860 ? 0.8 : 1) * cfg.zoom;
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

  // ---- the tools under the cards: the guitar's tuning, the scale (shared with Pick's keyboard), the panels
  function paintTools(){
    if(!tools) return;
    const m = mine(), cur = cfg.tuning || (m ? "mine" : "standard"), E = eng();
    const opts = (m ? `<option value="mine">Moje kytara (z ladičky): ${esc(m.map(STRN).join(" "))}</option>` : "")
      + sixes().map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
    tools.innerHTML = `<label title="Pro jaké ladění kytary ukazovat hmaty a stupnici — tvoje z ladičky, nebo jiné">Ladění <select data-k="tun">${opts}</select></label>
      ${E ? `<span class="cc-grp" title="Stupnice — stejná jako na klaviatuře Picku. Zámek: tóny mimo stupnici se nezahrají (klavír, klávesy Macu i MIDI)">Stupnice
        <select data-k="root">${SH.map((n, i) => `<option value="${i}">${n}</option>`).join("")}</select>
        <select data-k="scale">${E.scales().map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
        <select data-k="lock"><option value="off">neukazovat</option><option value="highlight">ukázat</option><option value="lock">🔒 zámek</option></select></span>` : ""}
      <span class="cc-grp cc-lvl" title="Kolik akordů ukazovat: Základní — jen hlavní, jeden na takt; Pokročilé — až dva na takt, septimy; Mistr — všechno, co analýza slyšela">${LEVELS.map(([k, n]) => `<button type="button" data-lvl="${k}" class="${cfg.level === k ? "on" : ""}">${n}</button>`).join("")}</span>
      <button type="button" data-k="sheet" title="Vložit akordy z listu (Ultimate Guitar, Supermusic…) — Pick je položí na nahrávku">📋 Akordy z listu${(() => { try{ return localStorage.getItem("pick-chords-src:" + Mixer.currentSong()) === "sheet" ? " ✓" : ""; }catch(e){ return ""; } })()}</button>
      <span class="cc-grp" title="Velikost akordů — aby byly vidět i z dálky"><span>Velikost</span><button type="button" data-k="zdn">A−</button><b data-k="zv">${Math.round(cfg.zoom * 100)} %</b><button type="button" data-k="zup">A+</button></span>
      <span class="cc-shift" data-k="shift" hidden></span>
      <button type="button" data-k="keys" title="Klavír: hraj myší, na klávesnici Macu nebo na MIDI klávesách — tóny se ukážou i na kytaře">🎹 Klavír</button>
      <button type="button" data-k="gtr" title="Kytara: celý hmatník — klikni a zahraj, ukáže stupnici, hmat akordu, a umí poslouchat tvoji kytaru">🎸 Kytara</button>
      <button type="button" data-k="wheel" title="Kruh akordů — vyber akord, ukáže se na klavíru i na kytaře">◎ Kruh akordů</button>`;
    const sel = tools.querySelector('[data-k="tun"]');
    sel.value = cur; if(sel.value !== cur) sel.selectedIndex = 0;
    sel.onchange = () => {
      const before = play.src === "neck" ? shapeNotes() : [];
      cfg.tuning = sel.value; keep(); carSig = "";
      // a shape tapped on the neck stays the same notes on the new tuning, where the strings allow
      if(before.length && E){ const t = tuning(), v = E.voicing(before, t.slice().reverse()); play.shape = v ? v.frets.slice().reverse().map(f => f == null ? -1 : f) : null; }
      kb.sig = gl.sig = ""; paintPanels();
    };
    if(E){
      const sc = E.scale(), r = tools.querySelector('[data-k="root"]'), k = tools.querySelector('[data-k="scale"]'), l = tools.querySelector('[data-k="lock"]');
      r.value = String(sc.root); k.value = sc.scale; l.value = sc.lock;
      r.onchange = () => E.setScale({root: +r.value});
      k.onchange = () => E.setScale({scale: k.value, lock: k.value !== "none" && E.scale().lock === "off" ? "highlight" : undefined});
      l.onchange = () => E.setScale({lock: l.value});
    }
    tools.querySelectorAll("[data-lvl]").forEach(b => b.onclick = () => setLevel(b.dataset.lvl));
    tools.querySelector('[data-k="sheet"]').onclick = () => openSheet();
    const zoomBy = (d) => { cfg.zoom = Math.round(Math.max(0.7, Math.min(2.5, cfg.zoom + d)) * 100) / 100; keep(); applyZoom(); tools.querySelector('[data-k="zv"]').textContent = Math.round(cfg.zoom * 100) + " %"; };
    tools.querySelector('[data-k="zdn"]').onclick = () => zoomBy(-0.15);
    tools.querySelector('[data-k="zup"]').onclick = () => zoomBy(0.15);
    tools.querySelector('[data-k="keys"]').onclick = () => toggleKeys();
    tools.querySelector('[data-k="gtr"]').onclick = () => toggleGtr();
    tools.querySelector('[data-k="wheel"]').onclick = () => { wh.on = !wh.on; paintToolState(); paintPanels(); };
    paintToolState();
  }
  function paintToolState(){
    if(!tools) return;
    tools.querySelector('[data-k="keys"]').classList.toggle("on", kb.on);
    tools.querySelector('[data-k="gtr"]').classList.toggle("on", gl.on);
    tools.querySelector('[data-k="wheel"]').classList.toggle("on", wh.on);
    const sh = tools.querySelector('[data-k="shift"]');
    sh.hidden = !semis;
    sh.textContent = semis ? `Akordy posunuté o ${semis > 0 ? "+" : "−"}${Math.abs(semis)} — jak teď píseň zní` : "";
    sh.title = "Píseň hraje posunutá (výška písně, nebo přeladění na tvoji kytaru) — akordy na kartách i v pásu jsou přepočítané, aby seděly s tím, co slyšíš";
  }

  // ---- the chord wheel: the circle of fifths (majors outside, their minors inside); the song's chord lit,
  // the key's own chords marked; a chord picked shows on the keys and the neck and sounds
  const WQ = ["", "m", "7", "maj7", "m7", "sus2", "sus4", "add9", "6", "9", "dim", "aug", "5"];
  const FIFTHS = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5];
  const wh = {on: false, root: null, minor: false, q: "", sig: ""};
  function wheelChord(){ return wh.root == null ? null : SH[wh.root] + (wh.q || (wh.minor ? "m" : "")); }
  // the key's chords (major or minor scale set): I ii iii IV V vi
  function keyChords(){
    const E = eng(), sc = E ? E.scale() : null;
    if(!sc || !sc.iv || !["major", "minor"].includes(sc.scale)) return new Set();
    const r = sc.root, maj = sc.scale === "major" ? r : (r + 3) % 12;
    return new Set([maj + ":0", (maj + 7) % 12 + ":0", (maj + 5) % 12 + ":0", (maj + 9) % 12 + ":1", (maj + 2) % 12 + ":1", (maj + 4) % 12 + ":1"]);
  }
  function pickChord(name, how){
    const ch = parseChord(name), tun = tuning(), E = eng();
    if(!ch) return;
    const notes = pianoNotes(ch);
    [...play.held.keys()].forEach(noteOff);
    play.last = notes; play.src = "keys"; play.shape = null;
    if(how !== "quiet"){
      if(gl.on && !kb.on){ const v = voicings(ch, tun)[0]; if(v) strum(v, tun, "strum"); }
      else piano(ch, "chord");
    }
    lastHeard = {kind: "wheel", name, notes};
    paintPlayed();
  }
  function wheelSvg(cur){
    const R1 = 98, R2 = 68, R3 = 40, C = 102, kc = keyChords();
    const seg = (i, r0, r1) => { const a0 = (i - 0.5) / 12 * Math.PI * 2 - Math.PI / 2, a1 = (i + 0.5) / 12 * Math.PI * 2 - Math.PI / 2;
      const p = (r, a) => `${(C + r * Math.cos(a)).toFixed(1)} ${(C + r * Math.sin(a)).toFixed(1)}`;
      return `M${p(r1, a0)} A${r1} ${r1} 0 0 1 ${p(r1, a1)} L${p(r0, a1)} A${r0} ${r0} 0 0 0 ${p(r0, a0)} Z`; };
    const at = (i, r) => { const a = i / 12 * Math.PI * 2 - Math.PI / 2; return [C + r * Math.cos(a), C + r * Math.sin(a)]; };
    const isMinor = (c) => c && /^m(?!aj)/.test(c.q);
    let g = "";
    FIFTHS.forEach((pc, i) => {
      [[pc, false, R2, R1], [(pc + 9) % 12, true, R3, R2]].forEach(([r, mn, a, b]) => {
        const sel = wh.root === r && wh.minor === mn, song = cur && cur.pc === r && isMinor(cur) === mn, inKey = kc.has(r + ":" + (mn ? 1 : 0));
        const [x, yy] = at(i, (a + b) / 2);
        g += `<path d="${seg(i, a, b)}" class="seg${mn ? " mn" : ""}${sel ? " sel" : ""}${song ? " song" : ""}${inKey ? " key" : ""}" style="--h:${hue(r)}" data-root="${r}" data-minor="${mn ? 1 : 0}"/>
          <text x="${x.toFixed(1)}" y="${yy.toFixed(1)}" class="wt${mn ? " s" : ""}${sel || song ? " on" : ""}">${SH[r]}${mn ? "m" : ""}</text>`;
      });
    });
    const nm = wheelChord();
    g += `<circle cx="${C}" cy="${C}" r="${R3 - 3}" class="mid" data-a="big"/><text x="${C}" y="${C - 2}" class="wt big">${esc(nm || (cur ? cur.name : "—"))}</text>
      <text x="${C}" y="${C + 13}" class="wt tiny">${nm ? "zvětšit" : cur ? "teď hraje" : ""}</text>`;
    return `<svg class="cc-wh" viewBox="0 0 204 204">${g}</svg>`;
  }
  function paintWheel(){
    const pn = panels && panels.querySelector('[data-p="wheel"]');
    if(!pn) return;
    const tg = target(), cur = tg && tg.c.ch, E = eng(), sc = E ? E.scale() : null;
    const sig = [wh.root, wh.minor, wh.q, cur ? cur.name : "", sc ? sc.root + sc.scale : ""].join(";");
    if(sig === wh.sig) return;
    wh.sig = sig;
    const nm = wheelChord(), ch = nm && parseChord(nm);
    pn.innerHTML = `<div class="cc-pnh"><b>◎ KRUH AKORDŮ</b><span class="cc-dev">vnější kruh dur, vnitřní moll · ${cur ? "svítí akord, který teď hraje píseň" : ""}${keyChords().size ? " · tečkované: akordy tóniny" : ""}</span><span class="sp"></span><button type="button" data-a="x" title="Zavřít">×</button></div>
      <div class="cc-whb">${wheelSvg(cur)}
        <div class="cc-whs">
          <div class="cc-whq">${WQ.map(q => `<button type="button" data-q="${q}" class="${nm && (wh.q || (wh.minor ? "m" : "")) === q ? "on" : ""}">${q === "" ? "dur" : q === "m" ? "moll" : esc(q)}</button>`).join("")}</div>
          ${ch ? `<div class="cc-whn"><b>${esc(nm)}</b> ${esc(ch.pcs.map(p => SH[p]).join(" · "))}</div>
            <div class="cc-whp"><button type="button" data-a="pno">▶ klavír</button><button type="button" data-a="gtr">▶ kytara</button><button type="button" data-a="big">🔍 zvětšit</button></div>`
            : `<div class="cc-wait">Klikni na akord v kruhu — ukáže se na klavíru i na kytaře.</div>`}
        </div></div>`;
    pn.querySelectorAll("path[data-root]").forEach(p => p.onclick = () => { wh.root = +p.dataset.root; wh.minor = p.dataset.minor === "1"; wh.q = wh.minor ? "m" : ""; wh.sig = ""; paintWheel(); pickChord(wheelChord()); });
    pn.querySelectorAll("[data-q]").forEach(b => b.onclick = () => { if(wh.root == null) return; wh.q = b.dataset.q; wh.minor = /^m(?!aj)/.test(wh.q); wh.sig = ""; paintWheel(); pickChord(wheelChord()); });
    const on = (a, fn) => pn.querySelectorAll(`[data-a="${a}"]`).forEach(b => b.onclick = fn);
    on("pno", () => ch && piano(ch, "chord"));
    on("gtr", () => { if(!ch) return; const t = tuning(), v = voicings(ch, t)[0]; if(v) strum(v, t, "strum"); });
    on("big", () => { const n = wheelChord() || (cur && cur.name); if(n) openZoomName(n); });
    on("x", () => { wh.on = false; paintToolState(); paintPanels(); });
  }

  // the chords' size: the lane and the cards grow (seen from across the room)
  function applyZoom(){ if(!box) return; box.style.setProperty("--cc-z", cfg.zoom); laneSig = carSig = ""; paint(); }

  // ---- Live Show's sounds: the guitar played in (its input, the amp, the gain), the keys' and the neck's instrument, their volume
  let soundPop = null;
  function openSounds(anchor){
    if(soundPop){ soundPop.remove(); soundPop = null; return; }
    soundPop = document.createElement("div");
    soundPop.className = "cc-pop";
    (anchor.closest("#nm-stage") || document.body).appendChild(soundPop);
    const r = anchor.getBoundingClientRect(), w = 400;
    Object.assign(soundPop.style, {left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px", top: (r.bottom + 6) + "px", width: w + "px"});
    paintSounds();
    const E = eng(); if(E) E.instruments().then(() => paintSounds()).catch(() => paintSounds());
    const off = (e) => { if(soundPop && !soundPop.contains(e.target) && !anchor.contains(e.target) && !e.target.closest(".inst-pop")){ soundPop.remove(); soundPop = null; }
      if(!soundPop) document.removeEventListener("pointerdown", off, true); };
    setTimeout(() => document.addEventListener("pointerdown", off, true), 0);
  }
  function paintSounds(){
    if(!soundPop) return;
    const E = eng(), A = D().amp, live = A.live(), own = A.own(), nm = A.name() || "bez zesilovače";
    let groups = null; try{ groups = E && E.instrName(E.instrument()) ? true : null; }catch(e){}
    const inst = E ? (E.instrName(E.instrument()) || "—") : "—", gtrInst = E ? (E.instrName(E.guitarInstrument()) || "—") : "—";
    soundPop.innerHTML = `<div class="cc-pop-h">Zvuky</div>
      <div class="cc-snd"><div class="cc-snd-t">🎸 Tvoje kytara <small>${live ? "vstup zapnutý" : "vstup vypnutý"}</small></div>
        <div class="cc-snd-r"><button type="button" data-a="pwr" class="${live ? "on" : ""}">${live ? "⏻ zapnutá" : "⏻ zapnout"}</button>
          ${own ? `<button type="button" data-a="ap" title="Předchozí zesilovač">‹</button><button type="button" data-a="an" class="cc-snd-n" title="Vybrat NAM profil ze seznamu">${esc(nm)}</button><button type="button" data-a="ax" title="Další zesilovač">›</button>`
            : `<span class="cc-snd-n">${esc(nm)}</span>`}</div>
        <label class="cc-snd-r">Gain <input type="range" data-a="gain" min="-12" max="24" step="0.5" value="${A.gain()}"><b data-a="gv">${A.gain() > 0 ? "+" : ""}${A.gain()} dB</b></label></div>
      <div class="cc-snd"><div class="cc-snd-t">🎹 Klavír <small>myš, klávesy Macu, MIDI</small></div>
        <div class="cc-snd-r">${E ? `<button type="button" data-a="pp">‹</button><button type="button" data-a="pn" class="cc-snd-n" title="Vybrat nástroj">${esc(inst)}</button><button type="button" data-a="px">›</button>` : "—"}</div></div>
      <div class="cc-snd"><div class="cc-snd-t">🎸 Kytara na hmatníku <small>klik na pražec, akordy</small></div>
        <div class="cc-snd-r">${E ? `<button type="button" data-a="gp">‹</button><button type="button" data-a="gn" class="cc-snd-n" title="Vybrat kytaru">${esc(gtrInst)}</button><button type="button" data-a="gx">›</button>` : "—"}</div></div>
      ${E ? `<label class="cc-snd-r">Hlasitost klavíru a hmatníku <input type="range" data-a="vol" min="0" max="1.2" step="0.01" value="${E.volume()}"></label>` : ""}
      ${E && !groups ? `<div class="cc-note">Nástroje Picku se nenačetly — klavír a kytara hrají náhradním zvukem.</div>` : ""}`;
    const on = (a, ev, fn) => { const b = soundPop.querySelector(`[data-a="${a}"]`); if(b) b[ev] = fn; };
    on("pwr", "onclick", async () => { await A.toggle(); paintSounds(); });
    on("ap", "onclick", async () => { await A.step(-1); setTimeout(paintSounds, 150); });
    on("ax", "onclick", async () => { await A.step(1); setTimeout(paintSounds, 150); });
    on("an", "onclick", async (e) => { await A.pick(e.currentTarget); setTimeout(paintSounds, 300); });
    on("gain", "oninput", (e) => { A.setGain(+e.target.value); const v = soundPop.querySelector('[data-a="gv"]'); if(v) v.textContent = (A.gain() > 0 ? "+" : "") + A.gain() + " dB"; });
    if(E){
      const pick = (groupsP, cur, set) => async (e) => { const g = await groupsP(); if(g && g.length) InstrPick.open(e.currentTarget, g, cur(), (id) => { set(id); setTimeout(paintSounds, 50); }); };
      const step = (groupsP, cur, set, d) => async () => { const g = await groupsP(); const n = g && InstrPick.step(g, cur(), d); if(n){ set(n.id); paintSounds(); } };
      const all = () => E.instruments().catch(() => null), gtrs = () => E.instruments().then(() => E.guitarGroups()).catch(() => null);
      on("pn", "onclick", pick(all, E.instrument, E.setInstrument)); on("pp", "onclick", step(all, E.instrument, E.setInstrument, -1)); on("px", "onclick", step(all, E.instrument, E.setInstrument, 1));
      on("gn", "onclick", pick(gtrs, E.guitarInstrument, E.setGuitarInstrument)); on("gp", "onclick", step(gtrs, E.guitarInstrument, E.setGuitarInstrument, -1)); on("gx", "onclick", step(gtrs, E.guitarInstrument, E.setGuitarInstrument, 1));
      on("vol", "oninput", (e) => E.setVolume(+e.target.value));
    }
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
  function openZoomName(name){ const ch = parseChord(name); if(ch) openZoom(0, [{name, ch}]); }
  function openZoom(i, items){
    closeZoom();
    items = items || seq;
    const c = items[i];
    if(!c || !c.ch) return;
    zoom = {i, v: 0, items, song: items === seq, el: document.createElement("div")};
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
    const c = zoom.items[zoom.i], ch = c.ch, tun = tuning(), vs = voicings(ch, tun), v = vs[zoom.v % Math.max(1, vs.length)];
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
      ${zoom.song ? `<div class="cc-zf">
        <button type="button" data-a="prev" ${zoom.i > 0 ? "" : "disabled"}>‹ Předchozí akord</button>
        <button type="button" data-a="seek" title="Píseň skočí na tenhle akord">⏵ Přehrát píseň odsud</button>
        <button type="button" data-a="next" ${zoom.i < seq.length - 1 ? "" : "disabled"}>Další akord ›</button>
      </div>` : ""}</div>`;
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
    if(was >= 0 && Mixer.isPlaying()) ["keys", "gtr"].forEach(k => { const on = k === "keys" ? kb.on : gl.listen; if(!on) return;
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
    const want = (kb.on ? "k" : "") + (gl.on ? "g" : "") + (wh.on ? "w" : "");
    if(panels.dataset.w !== want){
      panels.dataset.w = want;
      kb.sig = gl.sig = wh.sig = "";
      panels.innerHTML = (wh.on ? `<div class="cc-pn wheel" data-p="wheel"></div>` : "") + (kb.on ? `<div class="cc-pn" data-p="keys"></div>` : "") + (gl.on ? `<div class="cc-pn" data-p="gtr"></div>` : "");
    }
    if(kb.on) paintKeys();
    if(gl.on) paintGtr();
    if(wh.on) paintWheel();
  }

  // ======================================================================
  // Played here: the piano (mouse, the Mac's keys, MIDI keys) and the neck (clicked) — Pick's own sounds
  // (the keyboard tool's instrument and guitar, Piano.engine), its scale and lock. What one plays shows on
  // the other: keys held appear on the neck where to hold them, a shape tapped on the neck on the keys.
  // ======================================================================
  const eng = () => (typeof Piano !== "undefined" && Piano.engine) ? Piano.engine : null;
  const NAME = (m) => SH[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
  const play = {held: new Map(), last: [], shape: null, src: ""};    // held: midi → {src, stop}; shape: frets low → high (-1 none)
  // a note sounded: Pick's instrument (the guitar's for the neck), else the plucked string / the electric piano
  async function voice(m, v, gtr){
    const E = eng();
    if(E){ try{ const h = await E.on(m, v, gtr); if(h) return () => E.off(h); }catch(e){} }
    const {ac, out} = audio();
    if(gtr && typeof GuitarSampler !== "undefined"){ GuitarSampler.playNote(ac, out, 0, 0, ac.currentTime, [m], {duration: 2.2}, 11); return null; }
    if(typeof LoopSound !== "undefined"){ const r = LoopSound.synth(ac, out, "syn:ep", m, v, ac.currentTime); return () => r(audio().ac.currentTime); }
    return null;
  }
  // a key down (src: keys | pc | midi): the lock moves a wrong note to the nearest right one
  async function noteOn(m, v = 0.8, src = "keys"){
    const E = eng();
    if(E && E.scale().lock === "lock") m = E.constrain(m);
    if(!play.held.size) play.last = [];
    if(play.held.has(m)) noteOff(m);
    const rec = {src, stop: null};
    play.held.set(m, rec);
    if(!play.last.includes(m)) play.last.push(m);
    play.src = "keys"; play.shape = null;
    if(m < kb.lo) kb.lo = Math.max(12, Math.floor(m / 12) * 12);
    if(m > kb.lo + kb.oct * 12) kb.lo = Math.min(108 - kb.oct * 12, Math.ceil((m + 1) / 12) * 12 - kb.oct * 12);
    lastHeard = {kind: "keys", notes: [...play.held.keys()]};
    judgeKeys(); paintPlayed();
    if(cfg.keysSound){ const stop = await voice(m, v, false); if(play.held.get(m) === rec) rec.stop = stop; else if(stop) stop(); }
    return m;
  }
  function noteOff(m){
    const r = play.held.get(m);
    if(!r) return;
    play.held.delete(m);
    if(r.stop) r.stop();
    judgeKeys(); paintPlayed();
  }
  // the neck clicked: the string holds that fret (clicked again, it is let go), the note sounds
  function tapNeck(s, f){
    const tun = tuning();
    if(play.src !== "neck" || !play.shape) play.shape = tun.map(() => -1);
    play.src = "neck";
    play.shape[s] = play.shape[s] === f ? -1 : f;
    play.last = [];
    voice(tun[s] + f, 0.75, true).then(stop => stop && setTimeout(stop, 1400));
    lastHeard = {kind: "neck", notes: shapeNotes()};
    paintPlayed();
  }
  const shapeNotes = () => { const tun = tuning(); return play.shape ? play.shape.map((f, s) => f >= 0 ? tun[s] + f : null).filter(m => m != null) : []; };
  function strumShape(){
    const tun = tuning(), ms = play.shape ? play.shape.map((f, s) => f >= 0 ? {s, f, m: tun[s] + f} : null).filter(Boolean) : [];
    ms.forEach((x, k) => setTimeout(() => voice(x.m, 0.75, true).then(stop => stop && setTimeout(stop, 1800)), k * 32));
  }
  // what is shown as played: the keys held (or the last ones, until the next), the shape tapped
  const keysNotes = () => play.held.size ? [...play.held.keys()] : play.src === "keys" ? play.last.slice() : [];
  function shownNotes(){ return play.src === "neck" ? shapeNotes() : keysNotes(); }
  // the keys' notes on the neck: the same pitches where the strings allow, else the same chord
  let mirrorKey = "", mirrorVal = null;
  function mirrorShape(){
    const ms = keysNotes(), E = eng(), tun = tuning();
    if(!ms.length || !E) return null;
    const k = ms.slice().sort((a, b) => a - b).join(",") + "|" + tun.join(",");
    if(k !== mirrorKey){ mirrorKey = k; const v = E.voicing(ms, tun.slice().reverse()); mirrorVal = v ? v.frets.slice().reverse().map(f => f == null ? -1 : f) : null; }
    return mirrorVal;
  }
  let paintQueued = 0;
  function paintPlayed(){ if(paintQueued) return; paintQueued = requestAnimationFrame(() => { paintQueued = 0; kb.sig = gl.sig = ""; paintKeys(); paintGtr(); }); }

  // ---- the Mac's keys: the keyboard tool's layout (Z S X D C… a piano, Q 2 W 3 E… the octave up), − + the octave
  const pcHeld = new Map();
  window.addEventListener("keydown", (e) => {
    if(!kb.on || !cfg.pc || !shown() || zoom || e.metaKey || e.ctrlKey || e.altKey) return;
    if(typeof Piano !== "undefined" && Piano.isOpen()) return;                     // its window plays them
    if(e.target && e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    const E = eng(); if(!E) return;
    if(e.code === "Minus" || e.code === "Equal"){ e.preventDefault(); e.stopPropagation(); E.setPcBase(E.pcBase() + (e.code === "Equal" ? 12 : -12)); kb.sig = ""; paintKeys(); return; }
    const m = E.pcNote(e.code, cfg.pcMode);
    if(m == null) return;
    e.preventDefault(); e.stopPropagation();
    if(e.repeat || pcHeld.has(e.code)) return;
    pcHeld.set(e.code, null);
    noteOn(m, 0.8, "pc").then(p => { if(pcHeld.has(e.code)) pcHeld.set(e.code, p); else noteOff(p); });
  }, true);
  window.addEventListener("keyup", (e) => {
    if(!pcHeld.has(e.code)) return;
    e.stopPropagation();
    const m = pcHeld.get(e.code); pcHeld.delete(e.code);
    if(m != null) noteOff(m);
  }, true);
  // ---- the mouse on the drawn keys: down plays, a drag glides
  let mouseKey = null;
  document.addEventListener("pointerdown", (e) => {
    const k = e.target.closest && e.target.closest(".cc-kb [data-m]");
    if(k){ e.preventDefault(); mouseKey = -1; noteOn(+k.dataset.m, 0.8, "keys").then(m => { if(mouseKey === -1) mouseKey = m; else noteOff(m); }); return; }
    const c = e.target.closest && e.target.closest(".cc-nk [data-s]");
    if(c && !c.classList.contains("dis")){ e.preventDefault(); tapNeck(+c.dataset.s, +c.dataset.f); }
  });
  document.addEventListener("pointerover", (e) => {
    if(mouseKey == null || mouseKey === -1) return;
    const k = e.target.closest && e.target.closest(".cc-kb [data-m]");
    if(!k || +k.dataset.m === mouseKey) return;
    noteOff(mouseKey); mouseKey = -1;
    noteOn(+k.dataset.m, 0.8, "keys").then(m => { if(mouseKey === -1) mouseKey = m; else noteOff(m); });
  });
  document.addEventListener("pointerup", () => { if(mouseKey == null) return; const m = mouseKey; mouseKey = null; if(m !== -1) noteOff(m); });

  // ---- the keys panel: MIDI keys connected as it opens
  const kb = {on: false, access: null, lo: 48, oct: 3, devs: [], sig: "", err: ""};
  async function toggleKeys(){
    if(kb.on){ kb.on = false; [...play.held.keys()].forEach(noteOff); paintToolState(); paintPanels(); return; }
    kb.on = true; paintToolState(); paintPanels();
    if(!navigator.requestMIDIAccess){ kb.err = "tenhle prohlížeč MIDI neumí"; paintPanels(); return; }
    try{
      if(!kb.access){
        kb.access = await navigator.requestMIDIAccess();
        const hook = () => { kb.devs = []; kb.access.inputs.forEach(inp => { kb.devs.push(inp.name || "MIDI"); if(!inp._cc){ inp._cc = true; inp.addEventListener("midimessage", (e) => midi(e.data)); } }); kb.sig = ""; paintPanels(); };
        kb.access.addEventListener("statechange", hook);
        hook();
      }
      kb.err = "";
    }catch(e){ kb.err = "MIDI se nepodařilo otevřít"; }
    kb.sig = ""; paintPanels();
  }
  // a note from the MIDI keys (switches the pedal is bound to are the pedal's)
  const midiMap = new Map();
  function midi(data){
    if(!kb.on || (typeof Looper !== "undefined" && Looper.isOpen())) return;
    const [st, a, b] = data, kind = st & 0xf0, ch = (st & 0x0f) + 1;
    if(kind !== 0x90 && kind !== 0x80) return;
    try{ if(typeof Pedal !== "undefined" && Pedal.config && (Pedal.config().binds || []).some(x => x.key === `note:${ch}:${a}`)) return; }catch(e){}
    if(kind === 0x90 && b > 0){ midiMap.set(a, null); noteOn(a, b / 127, "midi").then(m => { if(midiMap.has(a)) midiMap.set(a, m); else noteOff(m); }); }
    else { const m = midiMap.get(a); midiMap.delete(a); noteOff(m != null ? m : a); }
  }
  let keysV = null;
  function judgeKeys(){
    const tg = target();
    const pcs = new Set([...play.held.keys()].map(m => m % 12));
    keysV = tg && tg.c.ch ? verdict(tg.c.ch, pcs, false) : null;
    if(keysV && keysV.ok && tg.now && Mixer.isPlaying()) score.keys.hit = true;
  }
  const songOn = () => { try{ return Mixer.isPlaying() && !!target(); }catch(e){ return false; } };
  function keysSvg(ch){
    const lo = kb.lo, hi = kb.lo + kb.oct * 12, WH = [0, 2, 4, 5, 7, 9, 11], E = eng(), sc = E ? E.scale() : {lock: "off", iv: null, root: 0};
    const whites = []; for(let m = lo; m <= hi; m++) if(WH.includes(m % 12)) whites.push(m);
    const ww = 22, H = 110, W = whites.length * ww, need = ch && songOn() ? new Set(ch.pcs) : new Set();
    const held = new Set(play.held.keys()), last = new Set(play.held.size ? [] : keysNotes()), fret = new Set(play.src === "neck" ? shapeNotes() : []);
    const heard = new Set(gl.listen && gl.res ? gl.res.notes.map(n => n.m) : []);
    const scaleOn = sc.iv && sc.lock !== "off", inSc = (m) => !scaleOn || sc.iv.includes(((m - sc.root) % 12 + 12) % 12);
    const hints = cfg.hints && E ? E.pcHints(cfg.pcMode) : new Map();
    const cls = (m) => { let c = "";
      if(held.has(m)) c += need.size ? (need.has(m % 12) || (ch && ch.bass === m % 12) ? " ok" : " bad") : " down";
      else if(last.has(m)) c += " last"; else if(fret.has(m)) c += " fret"; else if(heard.has(m)) c += " heard";
      if(!inSc(m)) c += sc.lock === "lock" ? " out lock" : " out";
      if(scaleOn && ((m - sc.root) % 12 + 12) % 12 === 0) c += " root";
      return c; };
    const cap = (m, x, y, dark) => hints.has(m) ? `<g class="cap${dark ? " d" : ""}"><rect x="${x - 7.5}" y="${y}" width="15" height="14" rx="3"/><text x="${x}" y="${y + 10.5}">${esc(E.keyCap(hints.get(m)))}</text></g>` : "";
    let g = `<defs><linearGradient id="cc-kw" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset=".85" stop-color="#eceef4"/><stop offset="1" stop-color="#d4d6de"/></linearGradient>
      <linearGradient id="cc-kbk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a3a46"/><stop offset=".12" stop-color="#1c1c24"/><stop offset="1" stop-color="#0b0b10"/></linearGradient></defs>`;
    whites.forEach((m, k) => { const x = k * ww;
      g += `<rect x="${x + 0.6}" y="0" width="${ww - 1.2}" height="${H}" rx="4" class="kw${cls(m)}" data-m="${m}"/>`;
      if(need.has(m % 12) && !held.has(m)) g += `<circle cx="${x + ww / 2}" cy="${H - 26}" r="3.6" class="hint"/>`;
      g += cap(m, x + ww / 2, H - 19, false);
      if(m % 12 === 0) g += `<text x="${x + ww / 2}" y="${H + 12}" class="ko">C${Math.floor(m / 12) - 1}</text>`; });
    whites.forEach((m, k) => { const b = m + 1; if(b > hi || WH.includes(b % 12)) return; const x = (k + 1) * ww - 7;
      g += `<rect x="${x}" y="0" width="14" height="${H * 0.62}" rx="2.5" class="kb${cls(b)}" data-m="${b}"/>`;
      if(need.has(b % 12) && !held.has(b)) g += `<circle cx="${x + 7}" cy="${H * 0.62 - 22}" r="3" class="hint"/>`;
      g += cap(b, x + 7, H * 0.62 - 17, true); });
    return `<svg class="cc-kb" viewBox="0 -2 ${W} ${H + 18}" style="--h:${ch ? hue(ch.pc) : 230}">${g}</svg>`;
  }
  function paintKeys(){
    const pn = panels && panels.querySelector('[data-p="keys"]');
    if(!pn) return;
    const tg = target(), ch = tg && tg.c.ch, E = eng();
    // as many octaves as the panel is wide (the keys keep their shape)
    const oct = Math.max(2, Math.min(6, Math.floor((pn.clientWidth || 600) / 220)));
    if(oct !== kb.oct){ kb.oct = oct; if(kb.lo + oct * 12 > 108) kb.lo = 108 - oct * 12; }
    judgeKeys();
    const held = [...play.held.keys()].sort((a, b) => a - b), shown = (held.length ? held : keysNotes()).sort((a, b) => a - b);
    const named = shown.length >= 2 ? (E ? E.nameChord(shown) : "") : shown.length ? NAME(shown[0]) : "";
    const sc = E ? E.scale() : null;
    const sig = [tg ? tg.ci : -1, songOn(), held.join(","), shown.join(","), play.src, shapeNotes().join(","), kb.lo, kb.oct, kb.devs.join("|"), kb.err, cfg.keysSound, cfg.pc, cfg.pcMode, cfg.hints,
      E ? E.pcBase() : 0, sc ? sc.root + sc.scale + sc.lock : "", score.keys.got, score.keys.of, gl.listen && gl.res ? gl.res.notes.map(n => n.m).join(",") : ""].join(";");
    if(sig === kb.sig) return;
    kb.sig = sig;
    const status = songOn() && tg ? verdictHtml(tg, keysV || {ok: false, missing: [], extra: []}, named, !held.length)
      : shown.length ? `<span class="cc-free">${held.length ? "hraješ" : "zahráno"}</span><span class="cc-want">${esc(named || "—")}</span><span class="cc-heard">${esc(shown.map(NAME).join(" "))}</span>`
      : `<span class="cc-wait">Hraj myší, klávesami Macu${kb.devs.length ? " nebo na MIDI klávesách" : ""} — tóny se ukážou i na kytaře</span>`;
    pn.innerHTML = `<div class="cc-pnh"><b>🎹 KLAVÍR</b><span class="cc-dev">${kb.err ? esc(kb.err) : kb.devs.length ? "MIDI: " + esc(kb.devs.join(", ")) : "MIDI klávesy nepřipojené"}</span><span class="sp"></span>
        <span class="cc-grp" title="Hrát na klávesnici Macu (Z S X D C… jako klavír, Q 2 W 3 E… o oktávu výš; − + posune oktávu)">
          <button type="button" data-a="pc" class="${cfg.pc ? "on" : ""}">⌨ klávesnice</button>
          <select data-a="pcm" title="Jak klávesy Macu hrají"><option value="chroma">jako klavír</option><option value="scale">jen tóny stupnice</option></select>
          <button type="button" data-a="pcd" title="Klávesy Macu o oktávu níž (−)">−</button><span class="cc-oct">${E ? "C" + (Math.floor(E.pcBase() / 12) - 1) : ""}</span><button type="button" data-a="pcu" title="O oktávu výš (+)">+</button>
          <button type="button" data-a="hint" class="${cfg.hints ? "on" : ""}" title="Ukázat na klávesách, kterou klávesou Macu se hrají">nápověda</button></span>
        <span class="cc-grp" title="Rozsah klaviatury"><button type="button" data-a="lod">‹</button><button type="button" data-a="lou">›</button></span>
        <button type="button" data-a="snd" class="${cfg.keysSound ? "on" : ""}" title="Klávesy hrají zvukem nástroje z klaviatury Picku">🔊</button>
        <button type="button" data-a="x" title="Zavřít">×</button></div>
      <div class="cc-pnb">${keysSvg(ch)}</div>
      <div class="cc-verdict">${status}${songOn() ? scoreHtml("keys") : ""}</div>`;
    const on = (a, fn) => { const b = pn.querySelector(`[data-a="${a}"]`); if(b) b.onclick = fn; };
    on("pc", () => { cfg.pc = !cfg.pc; keep(); kb.sig = ""; paintKeys(); if(cfg.pc) showToast("Hraj na klávesnici Macu: Z S X D C V G B H N J M = C…H, Q 2 W 3 E R… o oktávu výš, − + posune oktávu"); });
    const pcm = pn.querySelector('[data-a="pcm"]'); pcm.value = cfg.pcMode; pcm.onchange = () => { cfg.pcMode = pcm.value; keep(); kb.sig = ""; paintKeys(); };
    on("pcd", () => { if(E){ E.setPcBase(E.pcBase() - 12); kb.sig = ""; paintKeys(); } });
    on("pcu", () => { if(E){ E.setPcBase(E.pcBase() + 12); kb.sig = ""; paintKeys(); } });
    on("hint", () => { cfg.hints = !cfg.hints; keep(); kb.sig = ""; paintKeys(); });
    on("lod", () => { if(kb.lo > 12){ kb.lo -= 12; kb.sig = ""; paintKeys(); } });
    on("lou", () => { if(kb.lo + kb.oct * 12 < 108){ kb.lo += 12; kb.sig = ""; paintKeys(); } });
    on("snd", () => { cfg.keysSound = !cfg.keysSound; keep(); kb.sig = ""; paintKeys(); });
    on("x", () => toggleKeys());
  }

  // ---- the guitar: the neck to click, and Pick's guitar input listened to (on request)
  const gl = {on: false, listen: false, an: null, buf: null, timer: 0, unhook: null, res: null, at: 0, err: "", sig: ""};
  function toggleGtr(){
    if(gl.on){ gl.on = false; stopGtr(); paintToolState(); paintPanels(); return; }
    gl.on = true; paintToolState(); paintPanels();
  }
  async function startGtr(){
    const G = Mixer.guitar;
    gl.listen = true; gl.err = "";
    try{ if(!G.isLive()) await Mixer.voice.myGuitar(true); }catch(e){}
    if(!G.isLive()){ gl.err = "kytara není zapnutá — připoj zvukovku a zapni vstup kytary ve Steems"; gl.listen = false; return false; }
    const {ac} = audio();
    if(!gl.an){ gl.an = ac.createAnalyser(); gl.an.fftSize = 16384; gl.an.smoothingTimeConstant = 0; gl.buf = new Float32Array(gl.an.fftSize); }
    const attach = () => { try{ const n = G.cleanTap(); if(n) n.connect(gl.an); }catch(e){} };
    attach();
    if(!gl.unhook && G.onRewire) gl.unhook = G.onRewire(attach);
    clearInterval(gl.timer);
    gl.timer = setInterval(listen, 90);
    return true;
  }
  function stopGtr(){
    gl.listen = false; clearInterval(gl.timer); gl.timer = 0;
    if(gl.unhook){ gl.unhook(); gl.unhook = null; }
    if(gl.an){ try{ Mixer.guitar.cleanTap().disconnect(gl.an); }catch(e){} }
    gl.res = null;
  }
  function listen(){
    if(!gl.listen || !gl.an) return;
    gl.an.getFloatTimeDomainData(gl.buf);
    const r = analyze(gl.buf, gl.an.context.sampleRate);
    const now = performance.now();
    if(r.notes.length){ gl.res = r; gl.at = now; }
    else if(gl.res && now - gl.at > 700) gl.res = null;
    if(gl.res){ const tg = target(); if(tg && tg.now && tg.c.ch && verdict(tg.c.ch, new Set(gl.res.pcs), true).ok) score.gtr.hit = true; }
    paintGtr();
    if(kb.on && gl.res) paintKeys();
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
  // the whole neck: the shape to play (dashed), the scale's notes (small), what is played here (the shape tapped,
  // or the keys' notes where to hold them) and what the guitar plays (heard)
  function neckSvg(ch, v, tun, heard, mine, mineKind){
    const W = 900, H = 156, nutX = 46, right = W - 14, top = 24, gap = 20, S = tun.length, F = 15, E = eng(), sc = E ? E.scale() : {lock: "off", iv: null, root: 0};
    const L = (right - nutX) / (1 - Math.pow(2, -F / 12)), fx = (f) => nutX + L * (1 - Math.pow(2, -f / 12));
    const y = (s) => top + (S - 1 - s) * gap, mid = (f) => f === 0 ? nutX - 16 : (fx(f - 1) + fx(f)) / 2;
    const scaleOn = sc.iv && sc.lock !== "off", deg = (m) => ((m - sc.root) % 12 + 12) % 12, inSc = (m) => !scaleOn || sc.iv.includes(deg(m));
    let g = `<defs><linearGradient id="cc-wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a2618"/><stop offset=".5" stop-color="#2b1b11"/><stop offset="1" stop-color="#22150d"/></linearGradient></defs>`;
    g += `<rect x="${nutX}" y="${top - 9}" width="${right - nutX}" height="${(S - 1) * gap + 18}" rx="5" class="wood"/>`;
    [3, 5, 7, 9, 15].forEach(f => { g += `<circle cx="${mid(f)}" cy="${top + (S - 1) * gap / 2}" r="5" class="inl"/>`; });
    g += `<circle cx="${mid(12)}" cy="${top + gap * 1.5}" r="5" class="inl"/><circle cx="${mid(12)}" cy="${top + gap * 3.5}" r="5" class="inl"/>`;
    for(let f = 1; f <= F; f++) g += `<line x1="${fx(f)}" y1="${top - 9}" x2="${fx(f)}" y2="${top + (S - 1) * gap + 9}" class="fr"/>`;
    g += `<rect x="${nutX - 5}" y="${top - 10}" width="6" height="${(S - 1) * gap + 20}" rx="2" class="nut"/>`;
    for(let s = 0; s < S; s++) g += `<line x1="${nutX - 4}" y1="${y(s)}" x2="${right}" y2="${y(s)}" class="st" stroke-width="${(2.6 - s * 0.36).toFixed(2)}"/><text x="${nutX - 26}" y="${y(s) + 3.5}" class="sn">${STRN(tun[s])}</text>`;
    [1, 3, 5, 7, 9, 12, 15].forEach(f => { g += `<text x="${mid(f)}" y="${H - 5}" class="fn">${f}</text>`; });
    // the scale on the neck: every note of it (the root bigger), wherever the strings put it
    if(scaleOn) for(let s = 0; s < S; s++) for(let f = 0; f <= F; f++){ const m = tun[s] + f; if(!inSc(m)) continue;
      g += `<circle cx="${mid(f)}" cy="${y(s)}" r="${deg(m) === 0 ? 5 : 3.4}" class="scd${deg(m) === 0 ? " r" : ""}"/>`; }
    if(v) v.frets.forEach((f, s) => { if(f < 0) return; const root = ch && (tun[s] + f) % 12 === ch.pc;
      g += `<circle cx="${mid(f)}" cy="${y(s)}" r="${root ? 9.5 : 8.5}" class="want${root ? " root" : ""}"/>`; });
    // played here
    if(mine) mine.forEach((f, s) => { if(f < 0) return; const m = tun[s] + f;
      g += `<circle cx="${mid(f)}" cy="${y(s)}" r="9" class="me ${mineKind}"/><text x="${mid(f)}" y="${y(s) + 3.5}" class="ht">${esc(SH[m % 12].replace("b", "♭"))}</text>`; });
    (heard || []).forEach(p => { g += `<circle cx="${mid(p.f)}" cy="${y(p.s)}" r="8.5" class="hd ${p.kind}"/><text x="${mid(p.f)}" y="${y(p.s) + 3.5}" class="ht">${esc(SH[p.m % 12].replace("b", "♭"))}</text>`; });
    // the cells to click (a note out of a locked scale cannot be)
    for(let s = 0; s < S; s++) for(let f = 0; f <= F; f++){
      const x0 = f === 0 ? nutX - 30 : fx(f - 1), x1 = f === 0 ? nutX - 2 : fx(f), m = tun[s] + f;
      g += `<rect x="${x0.toFixed(1)}" y="${y(s) - gap / 2}" width="${(x1 - x0).toFixed(1)}" height="${gap}" class="cell${sc.lock === "lock" && !inSc(m) ? " dis" : ""}" data-s="${s}" data-f="${f}"><title>${NAME(m)}</title></rect>`;
    }
    return `<svg class="cc-nk" viewBox="0 0 ${W} ${H}" style="--h:${ch ? hue(ch.pc) : 230}">${g}</svg>`;
  }
  function paintGtr(){
    const pn = panels && panels.querySelector('[data-p="gtr"]');
    if(!pn) return;
    const tg = target(), ch = tg && tg.c.ch, tun = tuning(), v = ch && (songOn() || !shownNotes().length) ? voicings(ch, tun)[0] : null, r = gl.res, E = eng();
    const heard = r ? place(r.notes, v, tun, ch) : [];
    const vd = r && ch ? verdict(ch, new Set(r.pcs), true) : {ok: false, missing: [], extra: []};
    const live = (() => { try{ return Mixer.guitar.isLive(); }catch(e){ return false; } })();
    const mine = play.src === "neck" ? play.shape : mirrorShape(), mineKind = play.src === "neck" ? "tap" : "keys";
    const notes = shownNotes().sort((a, b) => a - b), named = notes.length >= 2 ? (E ? E.nameChord(notes) : "") : notes.length ? NAME(notes[0]) : "";
    const sc = E ? E.scale() : null;
    const sig = [tg ? tg.ci : -1, songOn(), tun.join(","), heard.map(p => p.s + ":" + p.f + p.kind).join(","), r ? r.name : "", r && r.single ? r.single.cents : "", gl.err, gl.listen, live,
      score.gtr.got, score.gtr.of, (mine || []).join(","), mineKind, sc ? sc.root + sc.scale + sc.lock : ""].join(";");
    if(sig === gl.sig) return;
    gl.sig = sig;
    const label = r ? (r.single ? `${r.single.name} <small>${r.single.cents > 0 ? "+" : ""}${r.single.cents}¢</small>` : esc(r.name || "?")) : "";
    let status;
    if(gl.listen && tg && songOn()) status = verdictHtml(tg, vd, "", !r) + (r ? `<span class="cc-heard">slyším <b>${label}</b></span>` : "") + scoreHtml("gtr");
    else if(gl.listen && r) status = `<span class="cc-free">slyším</span><span class="cc-want">${label}</span>`;
    else if(notes.length) status = `<span class="cc-free">${play.src === "neck" ? "hmat" : "z klavíru"}</span><span class="cc-want">${esc(named || "—")}</span><span class="cc-heard">${esc(notes.map(NAME).join(" "))}</span>`;
    else status = `<span class="cc-wait">Klikni na hmatník — tón zazní a ukáže se i na klavíru. ${tg && v ? "Čárkovaně: hmat akordu " + esc(tg.c.name) + "." : ""}</span>`;
    pn.innerHTML = `<div class="cc-pnh"><b>🎸 KYTARA</b><span class="cc-dev">ladění ${esc(tun.map(STRN).join(" "))}${gl.err ? " · " + esc(gl.err) : gl.listen ? " · poslouchám vstup kytary" : ""}</span><span class="sp"></span>
        ${play.src === "neck" && shapeNotes().length ? `<button type="button" data-a="strum" title="Zahrát hmat">▶ zahrát</button><button type="button" data-a="clr" title="Smazat hmat">✕</button>` : ""}
        <button type="button" data-a="listen" class="${gl.listen ? "on" : ""}" title="Poslouchat tvoji kytaru (vstup kytary ve Steems) — ukáže, co hraješ, a jestli je to správně">🎤 ${gl.listen ? "poslouchám" : "poslouchat kytaru"}</button>
        <button type="button" data-a="x" title="Zavřít">×</button></div>
      <div class="cc-pnb">${neckSvg(ch, v, tun, heard, mine, mineKind)}</div>
      <div class="cc-verdict">${status}</div>`;
    const on = (a, fn) => { const b = pn.querySelector(`[data-a="${a}"]`); if(b) b.onclick = fn; };
    on("strum", strumShape);
    on("clr", () => { play.shape = null; play.src = ""; paintPlayed(); });
    on("listen", async () => { if(gl.listen) stopGtr(); else await startGtr(); gl.sig = ""; paintGtr(); });
    on("x", () => toggleGtr());
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

  // the scale changed (here or on Pick's keyboard): the selects and the panels follow
  try{ if(eng()) eng().onChange(() => { paintTools(); kb.sig = gl.sig = wh.sig = ""; paintPanels(); }); }catch(e){}
  // the tuner's tuning changed: the shapes follow it
  try{ MyTuning.onChange(() => { vcache.clear(); carSig = ""; paintTools(); }); }catch(e){}
  try{ Tunings.onChange(() => paintTools()); }catch(e){}

  return {mount, paint, openSounds, openSheet, simplify, level: levelNow, setLevel, levels: LEVELS, parseSheet, fromSheet, parseChord, shapeOf, transpose, voicings, closeZoom, isZoomed: () => !!zoom,
    // for the tests
    _midi: (bytes) => midi(bytes), _analyze: analyze, _played: () => ({held: [...play.held.keys()], last: play.last.slice(), src: play.src, shape: play.shape, mirror: mirrorShape(), shown: shownNotes()}),
    _tap: (s, f) => tapNeck(s, f), _pick: (n) => pickChord(n, "quiet"), _detect: detectChords, _found: (s) => found.get(s), _heard: () => lastHeard,
    _state: () => { const tg = target(); return {semis, target: tg && tg.c.name, now: tg && tg.now, held: [...play.held.keys()], keys: keysV, gtr: gl.res,
      tuning: tuning(), score: JSON.parse(JSON.stringify(score)), cards: [...(car ? car.querySelectorAll(".cc-card") : [])].filter(n => !n.classList.contains("gone")).map(n => ({name: n.querySelector(".cc-cn").textContent, now: n.classList.contains("now"), x: n.getBoundingClientRect().left + n.getBoundingClientRect().width / 2, w: n.getBoundingClientRect().width}))}; }};
})();
