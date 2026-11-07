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
  .lp-body{flex:1;min-height:0;display:flex;position:relative}
  .lp-main{flex:1;min-width:0;min-height:0;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:16px}
  .lp-side{display:none;width:340px;flex:none;flex-direction:column;gap:8px;padding:12px;min-height:0;border-left:1px solid rgba(255,255,255,.08);background:rgba(12,12,19,.92)}
  .lp-side-on .lp-side{display:flex}
  @media (max-width:980px){.lp-side{position:absolute;right:0;top:0;bottom:0;z-index:5;box-shadow:-20px 0 40px rgba(0,0,0,.5)}}
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
  .lp-skel{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 12px;border-radius:12px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);font-size:12px}
  .lp-skel b{color:var(--accent,#8b5cf6);letter-spacing:.02em}.lp-skel span{color:#c9c9da;margin-right:4px}
  .lp-skel i{font-style:normal;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.06);color:#e6e6ef;font-variant-numeric:tabular-nums}
  .lp-skel i.warn{background:rgba(255,190,60,.14);color:#ffcf6e}
  .lp-slice{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .lp-sl-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .lp-sl-top .h,.lp-slice .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-sl-top select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-sl-info{margin-left:auto;color:#9a9ab0;font-size:12px;font-variant-numeric:tabular-nums}
  .lp-sl-wave{border-radius:12px;overflow:hidden;background:#07070c;border:1px solid rgba(255,255,255,.06)}
  .lp-sl-wave canvas{display:block;width:100%;height:92px;cursor:pointer}
  .lp-sl-sel{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .lp-sl-sel > span{color:#a6a6bc;font-size:12px}
  .lp-sl-stems{display:flex;gap:6px;flex-wrap:wrap}
  .lp-sl-stems .stm{display:inline-flex;align-items:center;gap:4px;padding:3px 4px 3px 8px;border-radius:999px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);font-size:12px}
  .lp-sl-stems .stm i{width:8px;height:8px;border-radius:50%;background:var(--c)}
  .lp-sl-stems .stm.mute{opacity:.45}.lp-sl-stems .stm.solo{border-color:var(--c);background:color-mix(in srgb,var(--c) 18%,transparent)}
  .lp-sl-stems button{width:22px;height:20px;border-radius:6px;border:1px solid rgba(255,255,255,.12);background:none;font-size:10px;font-weight:800;cursor:pointer;padding:0}
  .lp-sl-stems button.on{background:var(--c);color:#0b0b12;border-color:var(--c)}
  .lp-sl-to{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#a6a6bc}
  .lp-sl-to button{width:26px;height:26px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);cursor:pointer;font-weight:800}
  .lp-sl-to button:hover{background:var(--accent,#8b5cf6);color:#0b0b12}
  .lp-sl-drag{cursor:grab;padding:4px 10px;border-radius:999px;border:1px dashed rgba(255,255,255,.25);font-size:12px;color:#c9c9da}
  .lp-sl-wave canvas{touch-action:none}
  .lp-sl-zoom{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:999px;padding:3px 10px;font-weight:800;font-size:11px;cursor:pointer}
  .lp-sl-ghost{position:fixed;z-index:10000;pointer-events:none;padding:6px 12px;border-radius:999px;background:var(--accent,#8b5cf6);color:#0b0b12;font-weight:800;font-size:12px;box-shadow:0 8px 24px rgba(0,0,0,.5)}
  .lp-pads-bank{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .lp-pb-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .lp-pb-top .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-pb-top select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-pb-hint{margin-left:auto;color:#8a8aa0;font-size:11px}
  .lp-pb{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px}
  .lp-pad{position:relative;aspect-ratio:1.25;border-radius:14px;border:1px solid hsl(var(--h) 60% 50% / .45);cursor:pointer;text-align:left;padding:10px;
    background:linear-gradient(160deg,hsl(var(--h) 55% 30% / .55),hsl(var(--h) 45% 14% / .7));display:flex;flex-direction:column;justify-content:flex-end;gap:2px;
    transition:transform .06s,box-shadow .12s;user-select:none;-webkit-user-select:none;touch-action:none}
  .lp-pad b{font-size:13px;line-height:1.15;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lp-pad small{font-size:10.5px;color:hsl(var(--h) 40% 78%)}
  .lp-pad i{position:absolute;top:8px;right:10px;font-style:normal;font:700 10px/1 "Avenir Next Condensed",system-ui;letter-spacing:.08em;color:hsl(var(--h) 40% 78% / .8)}
  .lp-pad.piece{border-style:solid;border-width:2px}
  .lp-pad.hit,.lp-pad:active{transform:scale(.96);box-shadow:0 0 0 2px hsl(var(--h) 80% 65%),0 0 24px hsl(var(--h) 80% 55% / .6)}
  .lp-pad.on{box-shadow:0 0 0 2px hsl(var(--h) 85% 65%),0 0 30px hsl(var(--h) 85% 55% / .55);animation:lppulse 1s ease-in-out infinite}
  @keyframes lppulse{50%{box-shadow:0 0 0 2px hsl(var(--h) 85% 65%),0 0 12px hsl(var(--h) 85% 55% / .3)}}
  .lp-pad.drop{outline:2px dashed #fff;outline-offset:2px}
  .lp-pb{grid-template-columns:repeat(auto-fill,minmax(112px,1fr))}
  .lp-pad.empty{background:rgba(255,255,255,.02);border-style:dashed;border-color:rgba(255,255,255,.2)}
  .lp-pad.empty b{color:#9a9ab0;font-weight:600}
  .lp-pad.learn{outline:2px solid #ffd166;outline-offset:2px}.lp-pad.learn i{color:#ffd166}
  .lp-pad-add{--h:230;align-items:center;justify-content:center;font-size:24px;font-weight:300;border-style:dashed;background:rgba(255,255,255,.02);color:#9a9ab0}
  .lp-pad-add small{font-size:11px;color:#9a9ab0}
  .lp-sl-songs{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .lp-sl-song{display:inline-flex;align-items:center;gap:8px;padding:5px 8px 5px 12px;border-radius:12px;border:1px solid hsl(var(--h) 60% 55% / .35);background:hsl(var(--h) 40% 20% / .3);cursor:pointer;max-width:320px}
  .lp-sl-song b{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lp-sl-song small{font-size:10.5px;color:#a0a0b8;white-space:nowrap}
  .lp-sl-song em{font-style:normal;font-size:9.5px;font-weight:800;letter-spacing:.06em;color:hsl(var(--h) 60% 75%)}
  .lp-sl-song.on{background:hsl(var(--h) 55% 30% / .75);border-color:hsl(var(--h) 75% 65%)}
  .lp-sl-song button{background:none;border:0;color:#a0a0b8;cursor:pointer;font-size:11px;padding:2px 4px}.lp-sl-song button:hover{color:#fff}
  .lp-sl-pick{display:flex;flex-direction:column;gap:6px;padding:8px;border-radius:12px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.08)}
  .lp-sl-pick-top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .lp-sl-pick-top input[type=search]{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px;min-width:220px}
  .lp-chk{display:inline-flex;gap:5px;align-items:center;font-size:12px;color:#c8c8d8;cursor:pointer}
  .lp-sl-pick-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:4px;max-height:230px;overflow:auto}
  .lp-sl-pick-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1px 8px;text-align:left;padding:6px 9px;border-radius:8px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.03);cursor:pointer}
  .lp-sl-pick-row b{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lp-sl-pick-row small{font-size:10.5px;color:#9a9ab0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lp-sl-pick-row i{font-style:normal;font-size:10.5px;color:#c8c8d8;text-align:right;white-space:nowrap}.lp-sl-pick-row i:last-of-type{color:#9a9ab0}
  .lp-sl-pick-row b{grid-column:1;grid-row:1}.lp-sl-pick-row i:first-of-type{grid-column:2;grid-row:1}.lp-sl-pick-row small{grid-column:1;grid-row:2}.lp-sl-pick-row i:last-of-type{grid-column:2;grid-row:2}
  .lp-sl-pick-row:hover{border-color:var(--accent,#8b5cf6)}.lp-sl-pick-row:disabled{opacity:.5}
  .lp-sl-sel em{font-style:normal;color:#ffd166}
  .lp-comp{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .lp-cp-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .lp-cp-top .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-cp-top select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-cp-strip{position:relative;display:flex;gap:4px;overflow-x:auto;min-height:64px;padding:5px;border-radius:12px;background:rgba(0,0,0,.25);border:1px dashed rgba(255,255,255,.12)}
  .lp-cp-strip.drop{border-color:var(--accent,#8b5cf6);background:rgba(139,92,246,.1)}
  .lp-cp-b{position:relative;flex:none;display:flex;flex-direction:column;justify-content:center;gap:1px;padding:6px 22px 6px 10px;border-radius:9px;cursor:grab;user-select:none;-webkit-user-select:none;
    background:linear-gradient(160deg,hsl(var(--h) 55% 32% / .8),hsl(var(--h) 45% 16% / .9));border:1px solid hsl(var(--h) 60% 55% / .5)}
  .lp-cp-b b{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lp-cp-b small{font-size:10.5px;color:hsl(var(--h) 40% 82%);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lp-cp-b em{font-style:normal;font-size:10px;color:hsl(var(--h) 30% 76%);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lp-cp-b button{position:absolute;top:3px;right:3px;background:none;border:0;color:hsl(var(--h) 30% 85%);cursor:pointer;font-size:11px}
  .lp-cp-b.now{box-shadow:0 0 0 2px hsl(var(--h) 85% 65%)}.lp-cp-b.pv{outline:2px solid #fff;outline-offset:1px}
  .lp-cp-none{color:#8a8aa0;font-size:12px;align-self:center;padding:0 8px}
  .lp-cp-b.mine{border-width:2px;border-color:#ffd166}.lp-cp-b.swap{outline:2px dashed #ffd166;outline-offset:2px}
  .lp-cp-mine{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .lp-cp-mine .h2{font-size:10px;font-weight:800;letter-spacing:.1em;color:#ffd166;margin-right:4px}
  .lp-cp-chip{position:relative;display:inline-flex;flex-direction:column;padding:5px 22px 5px 10px;border-radius:9px;cursor:grab;user-select:none;
    background:linear-gradient(160deg,hsl(var(--h) 50% 28% / .8),hsl(var(--h) 40% 14% / .9));border:1px solid #ffd166aa}
  .lp-cp-chip b{font-size:11.5px;white-space:nowrap}.lp-cp-chip small{font-size:10px;color:hsl(var(--h) 30% 80%)}
  .lp-cp-chip button{position:absolute;top:3px;right:3px;background:none;border:0;color:#ccc;cursor:pointer;font-size:10px}
  .lp-cp-chip.pv{outline:2px solid #fff}
  .lp-riff{border-color:#ffd16699}.lp-riff.on{background:#e5484d;border-color:#e5484d;color:#fff}
  .lp-sl-sel em.rec{color:#ff6b6b;font-weight:700}
  .lp-rd{display:flex;flex-direction:column;gap:6px;margin:10px 0 4px;font-size:13px}
  .lp-rd label{display:flex;gap:7px;align-items:center;cursor:pointer}.lp-rd small{color:#9a9ab0}
  .lp-rd-h{font-size:11px;font-weight:800;letter-spacing:.08em;color:#9a9ab0;margin-top:8px;text-transform:uppercase}
  .lp-rd-row{display:flex;gap:10px;align-items:center;padding-left:24px;color:#c8c8d8}
  .lp-rd select{background:#0e0e14;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:4px 8px;color:inherit}
  .lp-dk{display:flex;flex-direction:column;gap:4px;padding:10px;border-radius:12px;border:1px solid #ffd16666;background:rgba(255,209,102,.04)}
  .lp-dk-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}.lp-dk-top b{font-size:13px;cursor:text}.lp-dk-top small{color:#9a9ab0;font-size:11px}
  .lp-dk-ch{display:grid;grid-template-columns:24px minmax(140px,1fr) 200px 190px 120px minmax(0,190px) 22px;align-items:center;gap:12px;padding:5px 8px;border-radius:9px;background:rgba(255,255,255,.03)}
  .lp-dk-tone{max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  @media (max-width:1100px){.lp-dk-ch{grid-template-columns:24px 1fr 1fr;}}
  .lp-dk-ch.off{opacity:.5}
  .lp-dk-ch label{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:#9a9ab0}.lp-dk-ch label.dim{opacity:.6}
  .lp-dk-ch input[type=range]{width:110px}.lp-dk-ch em{font-style:normal;font-size:11px;color:#c8c8d8;min-width:42px;font-variant-numeric:tabular-nums}
  .lp-dk-ch select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:7px;padding:3px 6px}
  .lp-dk-nm{display:flex;flex-direction:column;min-width:0}.lp-dk-nm b,.lp-dk-nm small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lp-dk-nm b{font-size:12px}.lp-dk-nm small{font-size:10.5px;color:#9a9ab0}
  .lp-dk-on{width:22px;height:22px;border-radius:50%;border:1px solid rgba(255,255,255,.2);background:none;color:#9a9ab0;cursor:pointer;font-size:11px;display:inline-flex;align-items:center;justify-content:center}
  .lp-dk-on.on{color:#ffd166;border-color:#ffd166}.lp-dk-on.fixed{color:#ffd166;border-color:transparent}
  .lp-dk-x{background:none;border:0;color:#9a9ab0;cursor:pointer}.lp-dk-x:hover{color:#fff}
  .lp-cp-head{position:absolute;top:2px;bottom:2px;width:2px;background:#fff;box-shadow:0 0 8px #fff;pointer-events:none;display:none}
  .lp-piano{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .lp-piano[hidden]{display:none}
  .lp-pn-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.lp-pn-top .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-pn-top select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-pn-rec.on{background:#e5484d;border-color:#e5484d;color:#fff}
  .lp-pn-key{font-size:11.5px;color:#c9b8ff}
  .lp-pn-kb{position:relative;height:150px;display:flex;user-select:none;-webkit-user-select:none;touch-action:none}
  .lp-pn-kb .w{flex:1;position:relative;border:1px solid #2a2a33;border-radius:0 0 8px 8px;background:linear-gradient(#f3f3f6,#dcdce4);cursor:pointer;display:flex;align-items:flex-end;justify-content:center;padding-bottom:6px;color:#666;font-size:10px}
  .lp-pn-kb .b{position:absolute;top:0;width:calc(100% / 15 * .6);height:60%;z-index:2;border-radius:0 0 6px 6px;border:1px solid #000;background:linear-gradient(#2c2c34,#111);cursor:pointer}
  .lp-pn-kb i{position:absolute;left:50%;transform:translateX(-50%);bottom:22px;width:6px;height:6px;border-radius:50%;background:var(--accent,#8b5cf6)}
  .lp-pn-kb .b i{bottom:8px}
  .lp-pn-kb .w.down{background:linear-gradient(#cbbcff,#a996ff)}.lp-pn-kb .b.down{background:linear-gradient(#6d5bd0,#4b3aa8)}
  .lp-t .row.fxr{display:flex;gap:6px;width:100%;justify-content:center;flex-wrap:wrap}
  .lp-t .row.fxr select,.lp-t .row.fxr button{max-width:100%;font-size:11.5px}
  .lp-t .row.fxr button{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:4px 8px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px}
  .lp-t .row.fxr button.on{border-color:var(--accent,#8b5cf6);color:#d7c9ff}

  .lp-sm-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.lp-sm-top .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-sm-fold{background:none;border:0;color:#9a9ab0;cursor:pointer;font-size:12px;padding:0 2px}
  .lp-sm-top input[type=search]{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 9px;min-width:180px}
  .lp-sm-dir{display:inline-flex;align-items:center;gap:4px;padding:3px 4px 3px 9px;border-radius:999px;background:rgba(255,255,255,.06);font-size:12px}
  .lp-sm-dir.gone{opacity:.5;text-decoration:line-through}.lp-sm-dir button{background:none;border:0;color:#9a9ab0;cursor:pointer}
  .lp-seg{display:inline-flex;border:1px solid rgba(255,255,255,.12);border-radius:999px;overflow:hidden}
  .lp-seg button{background:none;border:0;padding:4px 10px;font-size:11.5px;cursor:pointer;color:#c9c9da}.lp-seg button.on{background:var(--accent,#8b5cf6);color:#0b0b12;font-weight:700}
  .lp-sm-list{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:2px;margin:0 -4px;padding:0 4px}
  .lp-side > input[type=search]{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:7px 10px;width:100%;box-sizing:border-box}
  .lp-sm-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.lp-sm-tools input[type=range]{width:70px}
  .lp-sm-all{background:none;border:0;color:#9a9ab0;font-size:11px;cursor:pointer;margin-left:auto}.lp-sm-all:hover{color:#fff}
  .lp-sm-dirh{display:flex;align-items:center;gap:6px;padding:7px 6px 5px;margin-top:4px;cursor:pointer;font-size:12px;border-bottom:1px solid rgba(255,255,255,.08);user-select:none}
  .lp-sm-dirh span{width:10px;color:#9a9ab0;font-size:10px}.lp-sm-dirh b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lp-sm-dirh small,.lp-sm-fold-h small{color:#8a8aa0;font-size:10.5px;margin-left:auto}
  .lp-sm-dirh button{background:none;border:0;color:#8a8aa0;cursor:pointer;font-size:11px}.lp-sm-dirh button:hover{color:#fff}
  .lp-sm-dirh.gone b{text-decoration:line-through;opacity:.6}
  .lp-sm-fold-h{display:flex;align-items:center;gap:6px;padding:4px 6px 4px calc(10px + var(--d) * 12px);cursor:pointer;font-size:11.5px;color:#c8c8d8;user-select:none}
  .lp-sm-fold-h span{width:10px;color:#9a9ab0;font-size:9px}.lp-sm-fold-h:hover,.lp-sm-dirh:hover{color:#fff}
  .lp-sm-foot{color:#8a8aa0;font-size:10.5px}
  .lp-sm-row{position:relative;overflow:hidden;display:flex;align-items:center;gap:7px;padding:4px 6px 4px 18px;border-radius:8px;border:1px solid transparent;cursor:grab;min-width:0;flex:none}
  .lp-sm-row.sel{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.14)}
  .lp-sm-row.on{background:rgba(139,92,246,.18);border-color:var(--accent,#8b5cf6)}
  .lp-sm-row .bar{position:absolute;left:0;bottom:0;height:2px;width:0;background:var(--accent,#8b5cf6);pointer-events:none}
  .lp-sm-row:hover{background:rgba(255,255,255,.05)}
  .lp-sm-row b{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}.lp-sm-row small{font-size:10.5px;color:#8a8aa0;max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .lp-sm-row .pv{width:20px;height:20px;border-radius:50%;border:1px solid rgba(255,255,255,.15);background:none;cursor:pointer;font-size:10px;flex:none}
  .lp-sm-row .pv.on{background:var(--accent,#8b5cf6);color:#0b0b12}
  .lp-sm-row i{font-style:normal;font-size:10px;padding:1px 6px;border-radius:999px;background:rgba(255,255,255,.07);white-space:nowrap}
  .lp-sm-row i.key.ok{background:rgba(110,231,160,.18);color:#9ff0b0}.lp-sm-row i.key.sh{background:rgba(255,200,90,.16);color:#ffd27a}
  .lp-sm-none{grid-column:1/-1;color:#8a8aa0;font-size:12px;padding:8px}
  .lp-master{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 14px;border-radius:16px;
    background:linear-gradient(90deg,color-mix(in srgb,var(--accent,#8b5cf6) 22%,#0d0d14),#0d0d14 70%);border:1px solid color-mix(in srgb,var(--accent,#8b5cf6) 45%,transparent)}
  .lp-master .h{font-size:11px;font-weight:900;letter-spacing:.14em;color:#d7c9ff}
  .lp-ms-v{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:#e6e6ef;font-variant-numeric:tabular-nums}
  .lp-ms-v input{width:70px;background:#0e0e14;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:4px 6px;font-weight:800;font-size:15px}
  .lp-ms-v select{background:#0e0e14;border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:4px 6px;font-weight:700}
  .lp-ms-lock{font-size:11.5px;color:#a6a6bc}
  .lp-main .h{cursor:pointer;user-select:none}
  .lp-main .h:hover{color:#fff}
  .lp-main .h::before{content:"▾ ";font-size:9px;opacity:.7}
  .lp-master .h::before{content:""}
  .lp-folded > *:not(:first-child){display:none!important}
  .lp-folded .h::before{content:"▸ "}
  .lp-sc.lp-folded > *:not(.h),.lp-arr.lp-folded > *:not(.h),.lp-skel.lp-folded > *:not(.h){display:none!important}
  .lp-t.drop{outline:2px dashed var(--accent,#8b5cf6);outline-offset:3px}
  .lp-plan{display:flex;flex-direction:column;gap:8px;padding:10px 12px;border-radius:14px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
  .lp-plan-top{display:flex;align-items:center;gap:8px}.lp-plan .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-prompt{display:flex;flex-direction:column;gap:4px}
  .lp-prompt .now{font-size:20px;font-weight:700;line-height:1.3}
  .lp-prompt .next{font-size:13px;color:#a6a6bc}
  .lp-prompt i{font-style:normal;font-size:10px;font-weight:800;letter-spacing:.12em;color:var(--accent,#8b5cf6);margin-right:8px;vertical-align:middle}
  .lp-steps{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:2px;font-size:12px;color:#c9c9da;max-height:180px;overflow:auto}
  .lp-steps li b{margin-right:6px;color:#fff}.lp-steps li.rec span{color:#ffb4b4}.lp-steps li.live span{color:#b9e3ff}
  .lp-steps li.now{background:color-mix(in srgb,var(--accent,#8b5cf6) 20%,transparent);border-radius:6px}
  .lp-drums{display:inline-flex;align-items:center;gap:6px}
  .lp-drums select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:4px 6px}
  .lp-drums input[type=range]{width:80px}
  .lp-prep{border-color:color-mix(in srgb,var(--accent,#8b5cf6) 60%,transparent)}
  .lp-sc button em{font-style:normal;font-weight:600;font-size:10.5px;color:#9a9ab0}
  .lp-sc button .ch{display:block;margin-top:3px;font-size:11px;color:#d7c9ff;max-width:240px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
  .lp-arr .st{flex-wrap:wrap;max-width:260px}
  .lp-arr .st .ch{display:block;flex-basis:100%;font-size:10px;color:#b9abf0;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lp-sc{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .lp-sc .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-sc button{min-width:96px;padding:10px 14px;border-radius:13px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.05);cursor:pointer;text-align:left}
  .lp-sc button b{display:block}.lp-sc button span{font-size:11px;color:#9a9ab0}
  .lp-sc button.cur{background:color-mix(in srgb,var(--accent,#8b5cf6) 30%,#14141c);border-color:var(--accent,#8b5cf6)}
  .lp-sc button.next{animation:lpblink .5s steps(2) infinite;border-color:#ffa53d}
  .lp-arr{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  .lp-arr .h{font-size:11px;font-weight:800;letter-spacing:.1em;color:#9a9ab0;margin-right:4px}
  .lp-arr .st{display:flex;align-items:center;gap:4px;padding:6px 8px;border-radius:12px;border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.04)}
  .lp-arr .st.now{border-color:#3ddc97;box-shadow:0 0 0 1px #3ddc97}
  .lp-arr .st b{font-size:12px;cursor:pointer}.lp-arr .st i{font-style:normal;font-size:11px;color:#9a9ab0;min-width:44px;text-align:center}
  .lp-arr .st button{font-size:11px;padding:2px 6px;border-radius:6px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);cursor:pointer}
  .lp-arr .arrow{color:#55556a}
  .lp-help{font-size:12px;color:#8a8aa0;line-height:1.6}
  .lp-help kbd{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);border-radius:5px;padding:0 5px;font:inherit;font-size:11px;color:#ddd}
  .lp-ped{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .lp-t .row.mi select{max-width:100%;flex:1}
  .lp-t .row button.st{background:color-mix(in srgb,var(--accent,#8b5cf6) 25%,#14141c);border-color:var(--accent,#8b5cf6)}
  .lp-steps{border-radius:18px;padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08)}
  .lp-steps[hidden]{display:none}
  .lp-steps .hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .lp-steps .hd b{font-size:14px;margin-right:6px}
  .lp-steps select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-grid{display:grid;gap:3px;align-items:center}
  .lp-grid .rn{font-size:11px;color:#c8c8d8;padding-right:8px;white-space:nowrap;cursor:pointer}
  .lp-grid .rn:hover{color:#fff}
  .lp-grid .c{height:26px;border-radius:5px;background:rgba(255,255,255,.07);cursor:pointer;border:none;padding:0}
  .lp-grid .c.beat{background:rgba(255,255,255,.12)}
  .lp-grid .c.on{background:var(--accent,#8b5cf6);box-shadow:0 0 10px color-mix(in srgb,var(--accent,#8b5cf6) 50%,transparent)}
  .lp-grid .c.now{outline:2px solid #fff;outline-offset:-2px}
  .lp-grid .c.bar{margin-left:6px}
  .lp-notes{border-radius:18px;padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08)}
  .lp-notes[hidden]{display:none}
  .lp-notes .hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .lp-notes .hd b{font-size:14px;margin-right:6px}
  .lp-notes select{background:#0e0e14;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:5px 8px}
  .lp-roll{width:100%;display:block;border-radius:10px;background:#0d0d14;cursor:crosshair;touch-action:none}
  .lp-pads{display:grid;grid-template-columns:repeat(8,1fr);gap:8px;margin-top:12px}
  .lp-pads button{height:56px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#1b1b26;cursor:pointer;font-size:12px;font-weight:700}
  .lp-pads button:active,.lp-pads button.hit{background:var(--accent,#8b5cf6);color:#0b0b12}
  .lp-ped button{font-size:11px;padding:4px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);cursor:pointer}
  `;
  { const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st); }
  const esc = (s) => escapeHtml(String(s == null ? "" : s));

  // ---- state
  const KEY = "pick-looper";
  const cfg = {bpm: 100, perBar: 4, click: true, clickVol: 0.5, countIn: true, autoNext: true, monMic: false, vol: 0.9,
                drums: true, drumBars: 2, drumVol: 0.9, autoRec: false, kit: "rock"};
  try{ Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); }catch(e){}
  const keep = () => { try{ localStorage.setItem(KEY, JSON.stringify(cfg)); }catch(e){} };
  setTimeout(() => { try{ LoopSound.setStyle(cfg.kit); }catch(e){} }, 0);
  // src: gtr | mic (audio) · midi (notes on an instrument) · drums (notes on the kit, and the step grid)
  const DEF_SRC = ["drums", "midi", "gtr", "gtr", "mic"];
  const tracks = Array.from({length: N}, (_, i) => ({i, name: `Stopa ${i + 1}`, src: DEF_SRC[i] || "gtr", bars: 0, vol: 0.9,
    instr: i === 1 ? "syn:bass" : "syn:saw", q: 16, steps: null,
    layers: [], undone: [], len: 0, anchor: 0, gain: null, job: null}));
  const isNotes = (t) => t.src === "midi" || t.src === "drums";
  let scenes = [], cur = 0, pending = null;     // scenes: {name, on: Set of track numbers}
  let ac = null, out = null, bus = null, clickBus = null;
  let running = false, t0 = 0, sel = 0, song = null, open = false;
  const history = [];                           // tracks touched by a layer, for undo
  const redoStack = [];                         // tracks of the layers undone, last first
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
    tracks.forEach(t => { t.gain = ac.createGain(); t.gain.connect(bus); t.fxIn = ac.createGain(); t.fxIn.connect(t.gain); });
    monitor = ac.createGain(); monitor.gain.value = 0.9; monitor.connect(bus);
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
  // the guitar clean (DI, before Pick's amp): recorded as it is, and heard through the track's print when it plays
  srcs.gtrdi = {name: "Kytara čistě (DI)", batches: [], on: false, node: null, tap: null, unhook: null, async start(){
    if(this.on) return true;
    const G = Mixer.guitar;
    if(!G.isLive()) await Mixer.voice.myGuitar(true);
    if(!G.isLive()){ showToast("Kytara není zapnutá — připoj zvukovku a zapni vstup kytary ve Steems"); return false; }
    let node;
    try{ node = new AudioWorkletNode(ac, "take-recorder", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]}); }
    catch(e){ await ac.audioWorklet.addModule("/static/worklets/guitar.js"); node = new AudioWorkletNode(ac, "take-recorder", {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1]}); }
    const sink = ac.createGain(); sink.gain.value = 0; node.connect(sink); sink.connect(out);
    const hook = () => { try{ if(this.tap) this.tap.disconnect(node); }catch(e){} this.tap = G.cleanTap(); if(this.tap) this.tap.connect(node); };
    hook(); this.unhook = G.onRewire(hook);
    this.batches = [];
    node.port.onmessage = (e) => { if(e.data && e.data.data) this.batches.push(e.data); };
    node.port.postMessage("start");
    this.node = node; this.sink = sink; this.on = true;
    return true;
  }, async stop(){
    if(!this.on) return;
    this.on = false;
    try{ this.node.port.postMessage("stop"); if(this.tap) this.tap.disconnect(this.node); this.node.disconnect(); this.sink.disconnect(); }catch(e){}
    if(this.unhook) this.unhook();
    this.node = this.tap = this.sink = this.unhook = null; this.batches = [];
  }};
  srcs.midi = {name: "MIDI", batches: [], on: true, async start(){ return midiConnect(); }, async stop(){}};
  srcs.drums = {name: "Bicí", batches: [], on: true, async start(){ midiConnect(); return true; }, async stop(){}};
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
    s.connect(t.fxIn || t.gain);
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
    if(DK.src) dkStop();
    try{ Mixer.pause(); }catch(e){}
    if(running) return;
    if(ac.state === "suspended") ac.resume();
    const lead = countIn ? barLen() : 0;
    t0 = now() + 0.08 + lead;
    running = true;
    clickFrom = countIn ? t0 - lead : t0;
    tracks.forEach(t => { if(t.layers.length){ t.anchor = t0; t.layers.forEach(l => startLayer(t, l, t0)); } });
    if(arrOn && arr.length){ arrPos = {i: 0, start: 0}; cur = arr[0].k; }
    if(arrOn && arr.length) setTimeout(() => planStep(0), 0);
    applyMutes(now());
    drumsAt(cur, t0);
    if(cfg.guide) guideAt(t0, guidePos());
    paint();
  }
  function stopAll(){
    drumsStop();
    if(!running) return;
    running = false;
    tracks.forEach(t => { t.layers.forEach(stopLayer); if(t.job) cancelJob(t); });
    pending = null; arrPos = null; arrOn = false;
    Object.values(srcs).forEach(s => s.stop());
    if(RF.src){ try{ RF.src.stop(); }catch(e){} RF.src = null; }
    if(RF.on){ RF.on = false; RF.track = null; paintSlice(); }
    guideStop();
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
    t.job = {kind, from, to: null, src: t.src, notes: [], open: new Map()};
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
      if(tm < j.from){ if(RF.track === t){ riffStop(); return; } cancelJob(t); maybeStopSrc(t.src); paint(); return; }    // still waiting: called off
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
      if(j && isNotes(t) && j.to != null && !j.busy && tm >= j.to + 0.03){ finishNotes(t, j); return; }
      if(!j || isNotes(t) || j.to == null || tm < j.to + L + 0.06) return;
      const src = srcs[j.src], data = cut(src, j.from, j.to);
      if(j.kind === "first"){
        t.len = j.to - j.from; t.anchor = j.from;
        const buf = ac.createBuffer(2, data[0].length, ac.sampleRate);
        buf.copyToChannel(data[0], 0); buf.copyToChannel(data[1], 1);
        addLayer(t, {buf});
        if(RF.track === t) riffDone(t, buf);
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
  // the notes of a take, as places in the loop (a note played a hair before its bar counts as on it)
  async function finishNotes(t, j){
    j.busy = true;
    j.open.forEach((o, m) => j.notes.push({mt: o.mt, m, v: o.v, d: Math.max(0.05, j.to - o.mt)}));
    if(j.kind === "first"){ t.len = j.to - j.from; t.anchor = j.from; }
    const notes = j.notes.filter(x => x.mt >= j.from - 0.06 && x.mt < j.to - 0.02)
      .map(x => ({p: Math.max(0, (((x.mt - t.anchor) % t.len) + t.len) % t.len), m: x.m, v: x.v, d: x.d}));
    cancelJob(t);
    if(j.kind === "first" && cfg.autoNext){ const nx = tracks.findIndex(x => !x.layers.length && !x.job); if(nx >= 0) sel = nx; }
    if(!notes.length && j.kind === "first"){ t.len = 0; showToast(`${t.name}: nic se nezahrálo — smyčka se nevytvořila`); paint(); return; }
    if(!notes.length){ paint(); return; }
    const layer = {raw: notes};
    layer.buf = await render(t, quantized(t, notes));
    addLayer(t, layer);
    paint();
  }
  // onto the grid chosen for the track: 1/8 or 1/16 (0 = as played)
  function quantized(t, notes){
    if(!t.q) return notes;
    const step = beatLen() * 4 / t.q;
    return notes.map(x => ({...x, p: (Math.round(x.p / step) * step) % t.len}));
  }
  // notes → a buffer of the loop's length; what rings on past its end comes round to its start
  async function render(t, notes){
    const sr = ac.sampleRate, n = Math.max(1, Math.round(t.len * sr)), tail = Math.round(2.5 * sr);
    const oc = new OfflineAudioContext(2, n + tail, sr), dst = oc.destination;
    if(t.src === "drums") notes.forEach(x => LoopSound.drum(oc, dst, x.m, x.v, x.p));
    else if(t.instr.startsWith("syn:")) notes.forEach(x => LoopSound.synth(oc, dst, t.instr, x.m, x.v, x.p, x.d));
    else {
      try{
        const L = await Mixer.guitar.instrumentLib(), Z = libZv || (libZv = new L.Zvuky(ac)), bufs = await Z.nacti(L.najdiNastroj(t.instr).soundfont), keys = [...bufs.keys()];
        notes.forEach(x => {
          const k = bufs.has(x.m) ? x.m : keys.reduce((a, b) => Math.abs(b - x.m) < Math.abs(a - x.m) ? b : a);
          const src = oc.createBufferSource(), g = oc.createGain(), peak = 0.25 + 0.75 * (x.v ?? 0.8);
          src.buffer = bufs.get(k); src.playbackRate.value = Math.pow(2, (x.m - k) / 12);
          g.gain.setValueAtTime(0, x.p); g.gain.linearRampToValueAtTime(peak, x.p + 0.004); g.gain.setValueAtTime(peak, x.p + x.d); g.gain.linearRampToValueAtTime(0, x.p + x.d + 0.18);
          src.connect(g); g.connect(dst); src.start(x.p); src.stop(x.p + x.d + 0.2);
        });
      }catch(e){ showToast("Nástroj se nepodařilo načíst — hraje syntezátor"); notes.forEach(x => LoopSound.synth(oc, dst, "syn:saw", x.m, x.v, x.p, x.d)); }
    }
    const r2 = await oc.startRendering(), buf = ac.createBuffer(2, n, sr);
    for(let c = 0; c < 2; c++){
      const a = r2.getChannelData(c), b = buf.getChannelData(c);
      b.set(a.subarray(0, n));
      for(let k = n; k < a.length; k++) b[(k - n) % n] += a[k];
    }
    return buf;
  }
  let libZv = null;
  // a layer's sound made again (the grid changed, the instrument changed): the old one stops, the new one goes on in step
  async function rerender(t, layer, notes){
    const buf = await render(t, notes);
    stopLayer(layer); layer.buf = buf;
    if(running) startLayer(t, layer, now() + 0.005);
  }
  function rerenderAll(t){ t.layers.forEach(l => { if(l.raw) rerender(t, l, quantized(t, l.raw)); else if(l.steps) rerender(t, l, stepNotes(t)); }); }
  function addLayer(t, layer){
    t.layers.push(layer); tracks.forEach(x => { x.undone = []; }); redoStack.length = 0;
    history.push(t.i);
    startLayer(t, layer, now() + 0.005);
  }
  function undo(){
    const i = history.pop();
    if(i == null){ showToast("Není co vrátit"); return; }
    const t = tracks[i], l = t.layers.pop();
    if(l){ stopLayer(l); t.undone.push(l); redoStack.push(t.i); if(l.steps) t.steps = null; }
    if(stepT === t) paintSteps();
    if(!t.layers.length) t.len = 0;
    paint();
  }
  function redo(){
    let i = redoStack.pop();
    while(i != null && !tracks[i].undone.length) i = redoStack.pop();
    if(i == null){ showToast("Není co vrátit zpět"); return; }
    const t = tracks[i];
    const l = t.undone.pop();
    if(!t.len) t.len = l.buf.duration;
    if(l.steps) t.steps = l.steps;
    t.layers.push(l); history.push(t.i);
    startLayer(t, l, now() + 0.005);
    if(stepT === t) paintSteps();
    paint();
  }
  function clear(i){
    const t = tracks[i];
    t.layers.forEach(stopLayer); t.layers = []; t.undone = []; t.len = 0; t.steps = null; cancelJob(t);
    if(stepT === t) paintSteps();
    if(noteT === t) paintNotes();
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

  // ---- the arrangement: parts in order, each for some bars; played by itself, stepped by the pedal
  let arr = [], arrOn = false, arrPos = null, projName = null;
  // a part's natural length: its longest loop
  function sceneBars(k){
    const sc = scenes[k]; let b = 0;
    tracks.forEach(t => { if(t.len && (!sc || sc.on.has(t.i))) b = Math.max(b, Math.round(t.len / barLen())); });
    return b || 4;
  }
  const stepBars = (st) => Math.max(1, st.bars || sceneBars(st.k));
  function arrStep(d){
    const i = Math.max(0, Math.min(arr.length - 1, arrPos.i + d));
    const at = nextBar(now()), barIdx = Math.round((at - t0) / barLen());
    pending = {k: arr[i].k, at, step: {i, start: barIdx}};
    paint();
  }
  function playArr(){
    if(!arr.length){ showToast("Aranžmá je prázdné — přidej části (+ do aranžmá)"); return; }
    if(running) stopAll();
    arrOn = true; cur = arr[0].k;
    start(false);
  }
  function paintArr(){
    const box = root && root.querySelector("#lp-arr");
    if(!box) return;
    const total = arr.reduce((n, st) => n + stepBars(st), 0), sec = total * barLen();
    box.innerHTML = `<span class="h">ARANŽMÁ</span>
      <button type="button" class="lp-b ${arrOn && running ? "on" : ""}" data-a="play" title="Přehrát celé aranžmá: části se přepínají samy, pedál (další / předchozí část) skáče">${arrOn && running ? "■ Zastavit" : "▶ Přehrát aranžmá"}</button>`
      + arr.map((st, i) => `${i ? '<span class="arrow">›</span>' : ""}<span class="st ${arrOn && arrPos && arrPos.i === i && running ? "now" : ""}" data-i="${i}">
          <b data-a="go" title="Klik: tady pokračovat${st.full || st.chords ? "\nAkordy po taktech: " + esc(st.full || st.chords) : ""}">${esc(scenes[st.k] ? scenes[st.k].name : "?")}</b>${st.chords ? `<small class="ch">${esc(st.chords)}</small>` : ""}
          <button type="button" data-a="-" title="O takt kratší">−</button><i>${stepBars(st)} ${stepBars(st) === 1 ? "takt" : stepBars(st) < 5 ? "takty" : "taktů"}</i><button type="button" data-a="+" title="O takt delší">+</button>
          <button type="button" data-a="<" title="Dřív">‹</button><button type="button" data-a=">" title="Později">›</button><button type="button" data-a="x" title="Vyndat">✕</button></span>`).join("")
      + `<button type="button" class="lp-b" data-a="add" title="Přidá část, která právě hraje">+ do aranžmá</button>`
      + (arr.length ? `<span class="h" style="margin-left:6px">${total} taktů · ${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, "0")}</span>` : "");
    box.querySelector('[data-a="play"]').onclick = () => { if(arrOn && running){ stopAll(); arrOn = false; paint(); } else playArr(); };
    box.querySelector('[data-a="add"]').onclick = () => { arr.push({k: cur, bars: sceneBars(cur)}); paint(); };
    box.querySelectorAll(".st").forEach(n => {
      const i = +n.dataset.i;
      n.querySelectorAll("[data-a]").forEach(b => b.onclick = () => {
        const a = b.dataset.a, st = arr[i];
        if(a === "-") st.bars = Math.max(1, stepBars(st) - 1);
        else if(a === "+") st.bars = stepBars(st) + 1;
        else if(a === "<" && i > 0) [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
        else if(a === ">" && i < arr.length - 1) [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
        else if(a === "x") arr.splice(i, 1);
        else if(a === "go"){ if(arrOn && running && arrPos){ arrStep(i - arrPos.i); return; } goScene(st.k); }
        paint();
      });
    });
  }
  function arrFrom(list){
    // [{name, bars}] in the song's order → steps on the parts of those names
    arr = (list || []).map(x => ({k: scenes.findIndex(s => s.name === x.name), bars: Math.max(1, Math.round(x.bars || 0)) || 0})).filter(x => x.k >= 0);
  }

  // ---- the song's skeleton (Připravit z písně): its tempo, metre and key, its parts as scenes, the
  // arrangement in the song's order with each part's length in bars counted on the drums' downbeats, and
  // the chords of every bar — what to play, part by part, before anything is recorded.
  let skel = null;                     // {song, bpm, perBar, key, parts, bars}
  let plan = null;                     // the performance: [{k, act: "rec" | "play" | "live", track, bars, text}] by the arrangement's steps
  function barsOf(from, to, g){
    const db = (g && g.downbeats) || [];
    const inside = db.filter(t => t >= from - 0.05 && t < to - 0.05);
    if(inside.length >= 2) return inside;
    const n = Math.max(1, Math.round((to - from) / barLen())), out = [];
    for(let i = 0; i < n; i++) out.push(from + i * barLen());
    return out;
  }
  // the chord of each bar (the one sounding longest in it), repeats folded: "Em ×2 · C · G · D"
  function chordsIn(from, to, g, list){
    const starts = barsOf(from, to, g);
    const per = starts.map((b, i) => {
      const e = i + 1 < starts.length ? starts[i + 1] : Math.min(to, b + barLen());
      let best = "", most = 0;
      for(const c of list){
        const o = Math.min(e, c.end) - Math.max(b, c.start);
        if(o > most){ most = o; best = c.chord; }
      }
      return best;
    });
    const out = [];
    per.forEach(c => { const last = out[out.length - 1]; if(last && last.c === c) last.n++; else out.push({c, n: 1}); });
    const full = out.map(x => (x.c || "–") + (x.n > 1 ? " ×" + x.n : "")).join(" · ");
    // a part that goes round the same chords: the round once, and how many times ("E · A · D · A ×4")
    for(let L = 2; L <= 8 && per.length >= L * 2; L++){
      const hit = per.filter((c, i) => c === per[i % L]).length / per.length;
      if(hit >= 0.8 && new Set(per.slice(0, L)).size > 1)
        return {bars: starts.length, text: per.slice(0, L).join(" · ") + ` ×${Math.round(per.length / L)}`, full, cycle: L};
    }
    return {bars: starts.length, text: out.length > 8 ? out.slice(0, 8).map(x => (x.c || "–") + (x.n > 1 ? " ×" + x.n : "")).join(" · ") + " …" : full, full};
  }
  function skeletonFrom(name, quiet){
    let secs = [], g = null, chords = [];
    try{ secs = Mixer.sectionsNow() || []; g = Mixer.deck.grid(); chords = Mixer.deck.chords() || []; }catch(e){}
    // the chords as the Live Show's Základní shows them: the main one of each bar, not every passing colour
    try{ if(chords.length && typeof ChordCoach !== "undefined") chords = ChordCoach.timed(ChordCoach.simplify(chords, {level: "basic", beats: g.beats, downbeats: g.downbeats, perBar: g.perBar, sections: secs})); }catch(e){}
    if(g && g.bpm) cfg.bpm = Math.round(g.bpm * 10) / 10;
    if(g && g.perBar) cfg.perBar = g.perBar;
    const keyEl = document.getElementById("nm-key"), key = keyEl && !/^[…—-]?$/.test(keyEl.textContent.trim()) ? keyEl.textContent.trim() : "";
    makeScenes();
    drumCache = {};
    const steps = secs.map(x => ({name: x.name, start: x.start, end: x.end, ...chordsIn(x.start, x.end, g, chords)}));
    arr = steps.map(x => ({k: scenes.findIndex(sc => sc.name === x.name), bars: x.bars, chords: x.text, full: x.full})).filter(x => x.k >= 0);
    scenes.forEach(sc => { const first = steps.find(x => x.name === sc.name); sc.chords = first ? first.text : ""; sc.full = first ? first.full : ""; sc.bars = first ? first.bars : 0; sc.cycle = first ? first.cycle || 0 : 0; });
    plan = null;
    skel = {song: name, bpm: cfg.bpm, perBar: cfg.perBar, key, parts: scenes.length, bars: arr.reduce((n, st) => n + st.bars, 0), chords: chords.length > 0};
    if(!quiet) showToast(secs.length ? `Kostra z písně: ${skel.parts} částí, ${skel.bars} taktů, ${cfg.bpm} BPM` + (key ? `, tónina ${key}` : "")
      : "Píseň zatím nemá rozdělené části — kostra má jen tempo; části přidej ručně");
  }
  // ---- the performance (Scénář): who records what, where — from the skeleton. The song's first three
  // different parts (verse, chorus, a bridge…) each get a guitar track of their own, a loop as long as the
  // round of its chords (4 bars, say), recorded the first time the part comes; each part plays its own track.
  // A solo, an intro, an interlude plays the loop of the main part whose chords are most like its own, and
  // is played live over it. A part that comes again only plays. "Nahrávat samo": the arrangement arms each
  // recording on its bar by itself — the player only plays.
  const SPECIAL = /sol|solo|intro|outro|instrument|mezihr|bridge|most|break|konec|závěr|zaver/i;
  const CORE_TRACKS = [2, 3, 0];
  const chordSet = (sc) => new Set(String(sc.full || sc.chords || "").split(/\s*·\s*/).map(x => x.replace(/\s*×\d+.*$/, "").trim()).filter(x => x && x !== "–" && x !== "…"));
  function loopBarsOf(sc){
    const c = sc.cycle || 0, opts = [1, 2, 4, 8, 16];
    if(opts.includes(c)) return c;
    if(c > 0) return opts.find(o => o >= c) || 8;
    return Math.min(4, Math.max(1, sc.bars || 4));
  }
  // "Sloka 2" is "Sloka" again: parts told apart by their name without the number
  const base = (name) => String(name || "").replace(/\s*\d+\s*$/, "").trim().toLowerCase();
  // the chords of a part's first n bars ("E ×15 · A" → E E E E)
  function firstBars(sc, n){
    const out = [];
    for(const x of String(sc.full || sc.chords || "").split(/\s*·\s*/)){
      const m = x.match(/^(.*?)(?:\s*×(\d+))?$/); if(!m || !m[1] || m[1] === "…") continue;
      for(let i = 0; i < (+m[2] || 1) && out.length < n; i++) out.push(m[1]);
      if(out.length >= n) break;
    }
    return out.join(" · ");
  }
  function makePlan(){
    if(!arr.length) return null;
    const order = [];
    arr.forEach(st => { if(!order.includes(st.k)) order.push(st.k); });
    // the main parts by their kind (all the verses one, all the choruses one), the first three of them
    const coreBases = [];
    order.forEach(k => { const b = base(scenes[k].name); if(!SPECIAL.test(scenes[k].name) && !coreBases.includes(b) && coreBases.length < CORE_TRACKS.length) coreBases.push(b); });
    if(!coreBases.length && order.length) coreBases.push(base(scenes[order[0]].name));
    const core = coreBases.map(b => order.find(k => base(scenes[k].name) === b));
    const trackOf = {};
    order.forEach(k => { const n = coreBases.indexOf(base(scenes[k].name)); if(n >= 0) trackOf[k] = CORE_TRACKS[n]; });
    // the others: the core part with the most chords in common
    const like = (k) => {
      const mine = chordSet(scenes[k]); let best = core[0], most = -1;
      core.forEach(c => { const n = [...chordSet(scenes[c])].filter(x => mine.has(x)).length; if(n > most){ most = n; best = c; } });
      return best;
    };
    const seen = new Set();
    return arr.map((st, i) => {
      const k = st.k, sc = scenes[k], isCore = k in trackOf;
      const tr = isCore ? trackOf[k] : trackOf[like(k)], first = isCore && !seen.has(tr);
      if(isCore) seen.add(tr);
      const L = loopBarsOf(isCore ? scenes[core[coreBases.indexOf(base(sc.name))]] || sc : scenes[like(k)] || sc);
      const ch = firstBars(sc, L);
      if(isCore && first) return {k, act: "rec", track: tr, bars: L, text: `nahraj kytaru na stopu ${tr + 1} — ${L} ${L === 1 ? "takt" : L < 5 ? "takty" : "taktů"}${ch ? ": " + ch : ""}, pak s ní hraj dál`};
      if(isCore) return {k, act: "play", track: tr, text: `smyčka stopy ${tr + 1} hraje — hraj s ní, nebo zpívej`};
      // before that loop is recorded, there is only the song's drums under it
      const over = seen.has(tr) ? ` přes smyčku ${scenes[like(k)] ? scenes[like(k)].name.replace(/\s*\d+\s*$/, "") : ""} (stopa ${tr + 1})` : (cfg.drums ? " — smyčky ještě nejsou, hrají jen bicí z písně" : " — smyčky ještě nejsou");
      return {k, act: "live", track: seen.has(tr) ? tr : null, text: (/sol|solo/i.test(sc.name) ? "sólo živě" : "hraj živě") + over};
    });
  }
  function prepPlan(){
    if(!skel || !arr.length){ showToast("Nejdřív připrav kostru z písně"); return; }
    if(hasAudio()){ showToast("V looperu už jsou nahrané smyčky — scénář jde připravit jen do prázdného"); return; }
    plan = makePlan();
    if(!plan) return;
    // the tracks: a name, the guitar as the source, the loop's length; each part plays its own
    const used = new Map();
    plan.forEach(p => { if(p.act === "rec") used.set(p.track, p); });
    used.forEach((p, ti) => { const t = tracks[ti]; t.src = "gtr"; t.bars = p.bars; t.name = `${scenes[p.k].name.replace(/\s*\d+\s*$/, "")} – kytara`; });
    scenes.forEach((sc, k) => { const p = plan.find(x => x.k === k); sc.on = new Set(p && p.track != null ? [p.track] : []); });
    root.querySelectorAll(".lp-t").forEach(n => { const t = tracks[+n.dataset.i]; const inp = n.querySelector(".nm input"); if(inp) inp.value = t.name; });
    showToast(`Scénář: ${used.size} ${used.size === 1 ? "smyčka" : "smyčky"} k nahrání, ${plan.length} kroků`);
    paint();
  }
  // the arrangement reached a step: what it says, and its recording armed when "Nahrávat samo" is on
  function planStep(i){
    if(!plan || !plan[i] || !cfg.autoRec) return;
    const p = plan[i], t = tracks[p.track];
    if(p.act === "rec" && t && !t.layers.length && !t.job) arm(t, "first");
  }
  function paintPlan(){
    const box = root && root.querySelector("#lp-plan");
    if(!box) return;
    box.hidden = !skel;
    if(!skel) return;
    const at = arrOn && arrPos && running ? arrPos.i : -1, nx = at >= 0 ? at + 1 : 0;
    const say = (i) => plan && plan[i] ? `<b>${esc(scenes[plan[i].k] ? scenes[plan[i].k].name : "")}</b> — ${esc(plan[i].text)}` : "";
    box.innerHTML = `<div class="lp-plan-top"><span class="h">SCÉNÁŘ</span>
        <button type="button" class="lp-b" id="lp-plan-make" ${hasAudio() ? "disabled" : ""} title="Rozdělí hlavní části písně na stopy: co, kam a jak dlouhé nahrát, a kde hrát živě">${plan ? "Připravit znovu" : "Připravit scénář"}</button>
        <button type="button" class="lp-b ${cfg.autoRec ? "on" : ""}" id="lp-plan-auto" title="Při přehrávání aranžmá se nahrávání spustí samo na začátku části, kde se má nahrávat — ty jen hraješ">Nahrávat samo</button></div>`
      + (plan ? `<div class="lp-prompt">${at >= 0 ? `<div class="now"><i>TEĎ</i>${say(at)}</div>` : `<div class="now"><i>ZAČNI</i>Přehraj aranžmá (▶) — ${say(0)}</div>`}${plan[nx] && at >= 0 ? `<div class="next"><i>DALŠÍ</i>${say(nx)}</div>` : ""}</div>
        <ol class="lp-steps">${plan.map((p, i) => `<li class="${i === at ? "now" : ""} ${p.act}"><b>${esc(scenes[p.k] ? scenes[p.k].name : "")}</b><span>${esc(p.text)}</span></li>`).join("")}</ol>`
      : `<div class="lp-prompt"><div class="next">Scénář řekne, co v které části nahrát (a na kterou stopu) a kde hrát živě.</div></div>`);
    box.querySelector("#lp-plan-make").onclick = prepPlan;
    box.querySelector("#lp-plan-auto").onclick = () => { cfg.autoRec = !cfg.autoRec; keep(); paintPlan(); };
  }

  // ---- the song in pieces (Píseň po kouscích): the whole song open in Steems, its mix as a wave, cut on its
  // drum downbeats into as many pieces as asked (or its parts); scissors cut a piece in two on the nearest
  // bar, a double click on a cut takes it away. In each piece the song's own tracks (drums, bass, guitar,
  // vocals…) are muted or soloed for that piece alone; a piece dragged onto a looper track (or → 1–5) becomes
  // its loop: the chosen tracks mixed, fitted to the master tempo (the song's own, so normally as it is) and
  // as long as its bars — it plays on the bar like any recording.
  const blankSong = () => ({song: null, title: "", artist: "", own: false, stems: [], db: [], cuts: [], st: {}, sel: 0, mode: "parts", scissors: false, prev: null,
              zoom: 1, vs: 0, env: null, sections: [], key: "", bpm: 0, tuning: null, dur: 0});
  let SL = blankSong();               // the song shown in the wave: zoom 1–64, vs the view's start (s), env each stem's envelope
  const SLS = [];                     // the songs open in pieces: the one in Steems first, then those added (their audio stays on the server)
  const STEMC = {drums: "#f6ad55", bass: "#63b3ed", guitar: "#f2c25c", guitar_solo: "#f7d774", guitar_rhythm: "#d9a441", vocals: "#f687b3", piano: "#9f7aea", other: "#68d391"};
  const stemName = (st) => ({drums: "bicí", bass: "basa", guitar: "kytara", guitar_solo: "kytara sólo", guitar_rhythm: "kytara rytmus", vocals: "zpěv", piano: "klavír", other: "ostatní"}[st] || st);
  const songHue = (name) => { let h = 7; for(const c of String(name || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const shortTitle = (s) => String(s || "").replace(/^.*? - /, "").slice(0, 30);
  const secsOf = (e) => { if(e.own){ try{ return Mixer.sectionsNow() || []; }catch(x){ return []; } } return e.sections || []; };
  const songBar = (e) => e.db.length > 1 ? (e.db[e.db.length - 1] - e.db[0]) / (e.db.length - 1) : barLen();
  const TUNE_PC = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"], STD_TUNE = [64, 59, 55, 50, 45, 40];
  function tuningName(t){
    if(!Array.isArray(t) || t.length !== 6) return "";
    const d = t.map((n, i) => n - STD_TUNE[i]), low = TUNE_PC[((t[5] % 12) + 12) % 12];
    if(d.every(x => x === d[0])) return `${low} standard`;
    if(d.slice(0, 5).every(x => x === d[0]) && d[5] === d[0] - 2) return `Drop ${low}`;
    return t.slice().reverse().map(n => TUNE_PC[((n % 12) + 12) % 12]).join(" ");
  }
  const sameTuning = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]);
  const tempoRatio = (b) => { let r = b / cfg.bpm; while(r > 1.5) r /= 2; while(r < 0.75) r *= 2; return r; };   // half or double time counts as the same
  // the song open in Steems, with the tracks the mixer has decoded
  function ownSlice(){
    const s = Mixer.currentSong && Mixer.currentSong(), at = SLS.findIndex(x => x.own);
    if(!s){ if(at >= 0) SLS.splice(at, 1); return; }
    if(at >= 0 && SLS[at].song === s && SLS[at].stems.length) return;
    let au = [], g = null;
    try{ au = (Mixer.deck.audio() || []).filter(x => x.buffer && !/^take-|^midi/.test(x.stem)); g = Mixer.deck.grid(); }catch(e){}
    if(!au.length){ if(at >= 0 && SLS[at].song !== s) SLS.splice(at, 1); return; }
    const e = blankSong(), dur = Math.max(...au.map(x => x.buffer.duration));
    const kEl = document.getElementById("nm-key"), k = kEl ? kEl.textContent.trim() : "";
    Object.assign(e, {own: true, song: s, title: s, stems: au.map(x => ({stem: x.stem, buf: x.buffer})), dur,
      key: /^[A-G]/.test(k) ? k : (skel && skel.key) || "", bpm: (g && g.bpm) || cfg.bpm, perBar: (g && g.beats_per_bar) || cfg.perBar});
    e.db = (g && g.downbeats && g.downbeats.length > 1) ? g.downbeats.filter(t => t < dur) : Array.from({length: Math.floor(dur / barLen())}, (_, i) => i * barLen());
    const row = (LS.list || []).find(x => x.name === s); if(row) e.tuning = row.tuning;
    cutBy(e.mode, e);
    const was = SL;
    if(at >= 0) SLS[at] = e; else SLS.unshift(e);
    const dup = SLS.findIndex(x => !x.own && x.song === s);
    if(dup >= 0){ if(SL === SLS[dup]) SL = e; SLS.splice(dup, 1); }
    if(was && was.own) SL = e;
  }
  function sliceSong(){
    ownSlice();
    if(!SLS.includes(SL)){ stopSlicePrev(); SL = SLS[0] || blankSong(); }
    return !!SL.song;
  }
  function stopSlicePrev(){ if(SL.prev){ try{ SL.prev.stop(); }catch(e){} SL.prev = null; } }
  // another song: its downbeats, parts and wave from the server; its key heard from a minute of its harmony
  const LS = {list: null, open: false, q: "", sameTune: true, nearTempo: true, loading: new Map()};
  async function loadSongList(){
    if(LS.busy) return; LS.busy = true;
    try{ LS.list = (await (await fetch("/api/looper/songs")).json()).songs || []; }catch(e){ LS.list = []; }
    LS.busy = false;
    SLS.forEach(e => { if(!e.tuning){ const r = LS.list.find(x => x.name === e.song); if(r) e.tuning = r.tuning; } });
    paintSlice();
  }
  const slKeep = () => { cfg.slSongs = SLS.filter(e => !e.own).map(e => e.song); keep(); };
  async function addSong(name){
    ownSlice();
    const have = SLS.find(x => x.song === name); if(have) return have;
    if(LS.loading.has(name)) return LS.loading.get(name);
    const pr = (async () => {
      const r = await fetch(`/api/looper/song?name=${encodeURIComponent(name)}`), d = await r.json().catch(() => ({}));
      if(!r.ok) throw new Error(d.error || "píseň se nepodařilo otevřít");
      const e = blankSong(), bl = 60 / (d.bpm || 120) * (d.perBar || 4);
      Object.assign(e, {song: name, title: d.title || name, artist: d.artist || "", stems: d.stems.map(stem => ({stem, buf: null})), dur: d.dur,
        sections: d.sections || [], bpm: d.bpm || 0, tuning: d.tuning, perBar: d.perBar || 4});
      e.db = (d.downbeats || []).length > 1 ? d.downbeats.filter(t => t < d.dur) : Array.from({length: Math.floor(d.dur / bl)}, (_, i) => i * bl);
      const by = {}; let n = 0, top = 0;
      for(const [st, arr] of Object.entries(d.env || {})){ by[st] = {e: Float32Array.from(arr, v => v / 255), sr: d.envRate}; n = Math.max(n, arr.length); }
      for(let i = 0; i < n; i++){ let t = 0; for(const v of Object.values(by)) t += v.e[i] || 0; if(t > top) top = t; }
      e.env = {by, top: top || 1};
      cutBy(e.mode, e);
      if(!SLS.some(x => x.song === name)) SLS.push(e);
      slKeep();
      songKeyOf(e);
      return SLS.find(x => x.song === name);
    })();
    LS.loading.set(name, pr);
    try{ return await pr; } finally { LS.loading.delete(name); }
  }
  async function songKeyOf(e){
    if(e.key || !window.PickKey) return;
    const st = ["guitar", "bass", "piano", "other"].filter(s => e.stems.some(x => x.stem === s)); if(!st.length) return;
    try{ audio(); const a = Math.max(0, e.dur * 0.3), raw = await rawPiece(e, {a, z: Math.min(e.dur, a + 40), stems: st}); e.key = PickKey.detect(raw) || ""; }catch(x){}
    if(e.key){ paintSlice(); paintComp(); }
  }
  async function songFor(name){
    ownSlice();
    const e = SLS.find(x => x.song === name); if(e) return e;
    try{ return await addSong(name); }catch(x){ showToast(`„${shortTitle(name)}“: ${x.message}`); return null; }
  }
  function useSong(k){ if(!SLS[k] || SLS[k] === SL) return; stopSlicePrev(); SL = SLS[k]; paintSlice(); }
  function dropSong(k){ const e = SLS[k]; if(!e || e.own) return; if(e === SL){ stopSlicePrev(); SL = SLS[k - 1] || SLS[k + 1] || blankSong(); } SLS.splice(k, 1); slKeep(); paintSlice(); }
  // the cuts: at the song's parts, or into n even pieces — always on a downbeat
  function cutBy(mode, e = SL){
    e.mode = mode;
    const db = e.db, near = (t) => db.reduce((b, x) => Math.abs(x - t) < Math.abs(b - t) ? x : b, db[0]);
    let cuts = [];
    if(mode === "parts") cuts = secsOf(e).map(x => near(x.start));
    else { const n = +mode, per = Math.max(1, Math.ceil(db.length / n)); for(let i = 0; i < db.length; i += per) cuts.push(db[i]); }
    if(!cuts.length || cuts[0] == null) cuts = [db[0] || 0];
    e.cuts = [...new Set(cuts)].sort((a, b) => a - b);
    e.st = {}; e.sel = Math.max(0, Math.min(e.sel, e.cuts.length - 1));
  }
  const pieceOf = (i) => { const a = SL.cuts[i], z = i + 1 < SL.cuts.length ? SL.cuts[i + 1] : (SL.db[SL.db.length - 1] + songBar(SL)); return {a, z: Math.min(z, SL.dur), bars: Math.max(1, SL.db.filter(t => t >= a - 0.05 && t < z - 0.05).length)}; };
  const pieceName = (i) => { const p = pieceOf(i), sec = secsOf(SL).find(x => p.a >= x.start - 0.3 && p.a < x.end); return sec ? sec.name : ""; };
  // which of the song's tracks sound in a piece: "on", "mute" or "solo" for each
  const stemsIn = (i) => SL.st[i] || (SL.st[i] = Object.fromEntries(SL.stems.map(x => [x.stem, "on"])));
  const heard = (i) => { const m = stemsIn(i), solo = Object.values(m).includes("solo"); return SL.stems.filter(x => solo ? m[x.stem] === "solo" : m[x.stem] !== "mute"); };
  // a piece as it is now: which song, where in it, how many bars, which tracks sound in it
  const pieceSnap = (i) => { const p = pieceOf(i); return {song: SL.song, title: SL.title, a: p.a, z: p.z, bars: p.bars, stems: heard(i).map(x => x.stem), name: pieceName(i) || "Kousek " + (i + 1), n: i + 1}; };
  // its sound as the song has it: from the mixer's decoded tracks for the song in Steems, mixed by the server for others
  async function rawPiece(e, sn){
    if(e.own){
      const list = e.stems.filter(x => sn.stems.includes(x.stem)); if(!list.length) return null;
      const sr = list[0].buf.sampleRate, from = Math.round(sn.a * sr), n = Math.max(1, Math.round((sn.z - sn.a) * sr)), b = ac.createBuffer(2, n, sr);
      for(let c = 0; c < 2; c++){ const d = b.getChannelData(c);
        list.forEach(x => { const src = x.buf.getChannelData(Math.min(c, x.buf.numberOfChannels - 1)); for(let k = 0; k < n && from + k < src.length; k++) d[k] += src[from + k]; }); }
      return b;
    }
    if(!sn.stems.length) return null;
    const r = await fetch(`/api/looper/piece?name=${encodeURIComponent(e.song)}&a=${sn.a.toFixed(4)}&z=${sn.z.toFixed(4)}&stems=${sn.stems.join(",")}`);
    if(!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "kousek se nepodařilo vyříznout");
    return ac.decodeAudioData(await r.arrayBuffer());
  }
  function fadeEdges(b){ const f = Math.round(b.sampleRate * 0.004); for(let c = 0; c < b.numberOfChannels; c++){ const d = b.getChannelData(c); for(let k = 0; k < f && k < d.length; k++){ const g = k / f; d[k] *= g; d[d.length - 1 - k] *= g; } } return b; }
  function stretchLinear(raw, want){            // a tempo this near: read a little faster or slower (under 2 % of pitch is not heard)
    const sr = raw.sampleRate, n = Math.max(1, Math.round(want * sr)), rate = raw.length / n, b = ac.createBuffer(2, n, sr);
    for(let c = 0; c < 2; c++){ const s = raw.getChannelData(Math.min(c, raw.numberOfChannels - 1)), d = b.getChannelData(c);
      for(let k = 0; k < n; k++){ const f = k * rate, j = Math.floor(f), w = f - j; d[k] = j + 1 < s.length ? s[j] * (1 - w) + s[j + 1] * w : (s[j] || 0); } }
    return b;
  }
  // …fitted to the master: its bars at the master tempo (Signalsmith keeps the pitch) and in the master key
  // (keeping the length) — kept, so a piece heard again starts at once
  const PA = new Map();
  // how many master bars a piece takes: its own bars — or twice / half as many when its song is in half or double
  // the master's time (one of its bars is then nearer two of the master's, or half of one)
  const mBars = (sn) => { const r = (sn.z - sn.a) / sn.bars / barLen();
    return sn.bars * (r > 2.83 ? 4 : r > 1.414 ? 2 : r < 0.354 && sn.bars % 4 === 0 ? 0.25 : r < 0.707 && sn.bars % 2 === 0 ? 0.5 : 1); };
  const pieceFit = (sn) => { const e = SLS.find(x => x.song === sn.song); return {rate: (sn.z - sn.a) / (mBars(sn) * barLen()), semis: e ? keyShift(e.key) : 0}; };
  const fitText = (sn) => { const f = pieceFit(sn), r = f.rate; return [Math.abs(r - 1) > 0.02 ? `tempo ${r > 1 ? "+" : "−"}${Math.round(Math.abs(r - 1) * 100)} %` : "", f.semis ? `${f.semis > 0 ? "+" : ""}${f.semis} pt` : ""].filter(Boolean).join(" · "); };
  async function pieceAudio(sn){
    audio();
    if(sn.parts || sn.over) return dilekAudio(upgradeDilek(sn));
    const e = await songFor(sn.song); if(!e) return null;
    const semis = keyShift(e.key), k = [sn.song, sn.a.toFixed(3), sn.z.toFixed(3), sn.bars, sn.stems.join("+"), cfg.bpm, cfg.perBar, semis].join("|");
    if(!PA.has(k)){
      const pr = (async () => { const raw = await rawPiece(e, sn); if(!raw) return null;
        const want = mBars(sn) * barLen(), rate = raw.duration / want;
        return fadeEdges(Math.abs(rate - 1) < 0.02 && !semis ? stretchLinear(raw, want) : await fitAudio(raw, want, semis)); })();
      PA.set(k, pr); pr.catch(() => PA.delete(k));
      while(PA.size > 80) PA.delete(PA.keys().next().value);
    }
    return PA.get(k);
  }

  // ---- a track's sound after its loop: a tone print (a clean guitar played through it), and an effect —
  // for the voice (room, hall, delay, radio, doubling) or a space for anything. Built in the looper's own
  // audio context, put between the loop and the track's level; heard when the loop plays, nothing recorded
  // is changed — another print or effect can be tried after.
  const FX = {
    none: {name: "Bez efektu"},
    voice: {name: "Zpěv — čistý (komprese, jas)", voice: true},
    room: {name: "Místnost", voice: true, verb: [0.7, 0.18]},
    hall: {name: "Sál", voice: true, verb: [2.4, 0.28]},
    delay: {name: "Delay 1/4 + sál", voice: true, verb: [1.8, 0.18], delay: [1, 0.32, 0.25]},
    slap: {name: "Slapback (rock'n'roll)", voice: true, delay: [0, 0.12, 0.3]},
    double: {name: "Zdvojení (dva hlasy)", voice: true, double: true},
    radio: {name: "Rádio / megafon", radio: true},
    gspace: {name: "Kytara — prostor", verb: [1.6, 0.2]},
    gdelay: {name: "Kytara — delay 1/8.", delay: [0.75, 0.3, 0.28], verb: [1.2, 0.12]},
  };
  function roomIR(sec){
    const sr = ac.sampleRate, n = Math.round(sr * sec), b = ac.createBuffer(2, n, sr);
    for(let c = 0; c < 2; c++){ const d = b.getChannelData(c); let s2 = 77 + c * 999; for(let i = 0; i < n; i++){ s2 = (s2 * 16807) % 2147483647; d[i] = (s2 / 1073741823.5 - 1) * Math.pow(1 - i / n, 2.6); } }
    return b;
  }
  function buildFx(id){
    const F = FX[id]; if(!F || id === "none") return null;
    const g = (v = 1) => { const n = ac.createGain(); n.gain.value = v; return n; };
    const input = g(), outN = g(), nodes = [input, outN];
    let n = input;
    if(F.voice){
      const hp = ac.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 90;
      const cmp = ac.createDynamicsCompressor(); cmp.threshold.value = -22; cmp.ratio.value = 3.2; cmp.attack.value = 0.006; cmp.release.value = 0.15;
      const air = ac.createBiquadFilter(); air.type = "peaking"; air.frequency.value = 3200; air.gain.value = 3; air.Q.value = 0.8;
      const mk = g(1.4); n.connect(hp); hp.connect(cmp); cmp.connect(air); air.connect(mk); n = mk; nodes.push(hp, cmp, air, mk);
    }
    if(F.radio){
      const lo = ac.createBiquadFilter(); lo.type = "highpass"; lo.frequency.value = 450; const hi = ac.createBiquadFilter(); hi.type = "lowpass"; hi.frequency.value = 2800;
      const sh = ac.createWaveShaper(), cv = new Float32Array(256); for(let i = 0; i < 256; i++){ const x = i / 127.5 - 1; cv[i] = Math.tanh(3 * x); } sh.curve = cv;
      n.connect(lo); lo.connect(hi); hi.connect(sh); n = sh; nodes.push(lo, hi, sh);
    }
    n.connect(outN);
    if(F.double){ const d = ac.createDelay(0.1); d.delayTime.value = 0.024; const p = ac.createStereoPanner(); p.pan.value = 0.6; const v = g(0.7); n.connect(d); d.connect(v); v.connect(p); p.connect(outN); nodes.push(d, p, v); }
    if(F.delay){
      const [beats, fb, mix] = F.delay, d = ac.createDelay(3); d.delayTime.value = beats ? beats * beatLen() : 0.11;
      const f = g(fb), w = g(mix), lp = ac.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 3500;
      n.connect(d); d.connect(lp); lp.connect(f); f.connect(d); lp.connect(w); w.connect(outN); nodes.push(d, f, w, lp);
    }
    if(F.verb){
      const [sec, mix] = F.verb, cv2 = ac.createConvolver(); cv2.buffer = roomIR(sec); const w = g(mix);
      n.connect(cv2); cv2.connect(w); w.connect(outN); nodes.push(cv2, w);
    }
    return {input, out: outN, nodes};
  }
  // the track's chain: its print (if any), then its effect; rewired whenever one of them changes
  async function setTrackSound(t, patch){
    audio();
    Object.assign(t, patch);
    const old = t.chain || [];
    old.forEach(c => (c.nodes || []).forEach(x => { try{ x.disconnect(); }catch(e){} }));
    if(t.printChain && t.printChain.core && patch.print !== undefined){ try{ t.printChain.core.unloadModel(); }catch(e){} }
    try{ t.fxIn.disconnect(); }catch(e){}
    const chain = [];
    if(t.print && t.print.pid){
      if(patch.print !== undefined || !t.printChain){
        try{ const c = await Tone.buildChain(t.print.song, t.print.pid); t.printChain = c; }
        catch(e){ t.printChain = null; showToast("Otisk se nepodařilo načíst: " + e.message); }
      }
      if(t.printChain) chain.push({input: t.printChain.input, out: t.printChain.out, nodes: []});
    } else t.printChain = null;
    const fx = buildFx(t.fx || "none"); if(fx) chain.push(fx);
    let n = t.fxIn;
    chain.forEach(c => { n.connect(c.input); n = c.out; });
    n.connect(t.gain);
    t.chain = chain;
    paint();
  }
  // the prints there are: the song's own first, its confirmed one marked; any other by a search
  let printList = null;
  async function prints(){ if(!printList){ try{ printList = await (await fetch("/api/tone/prints")).json(); }catch(e){ printList = []; } } return printList; }
  async function pickPrint(t){
    const all = await prints(), songOf = skel && skel.song;
    const v = await Dialog.pick({title: `Otisk pro stopu ${t.i + 1}`, placeholder: "Hledat otisk — název, kapela, píseň, kategorie…", empty: "Nic takového.",
      search: async (q) => all.filter(p => !q || [p.name, p.artist, p.title, p.category, p.core].join(" ").toLowerCase().includes(q.toLowerCase()))
        .sort((a, b) => (b.song === songOf) - (a.song === songOf))
        .slice(0, 80).map(p => ({label: p.name || "otisk", sub: [p.artist || p.title, p.category, p.song === songOf ? "z téhle písně" : ""].filter(Boolean).join(" · "), value: p.song + "|" + p.id}))});
    if(!v) return;
    const [song2, pid] = v.split("|"), p = all.find(x => x.song === song2 && x.id === pid);
    await setTrackSound(t, {print: {song: song2, pid, name: p ? p.name : "otisk"}});
    showToast(`Stopa ${t.i + 1} hraje přes otisk „${p ? p.name : ""}“`);
  }

  // ---- the master (MASTER): the tempo, the metre and the key everything is fitted to — the song's own to begin
  // with. The key can be set otherwise: the samples in the tracks are put into it again, the piano dots it.
  const KEYS = () => [...PC, ...PC.map(n => n + "m")];
  const FOLDABLE = ["lp-slice", "lp-comp", "lp-pads", "lp-plan", "lp-sc", "lp-arr", "lp-skel"];
  function applyFolds(){ FOLDABLE.forEach(id => { const n = root && root.querySelector("#" + id); if(n) n.classList.toggle("lp-folded", !!(cfg.fold || {})[id]); }); }
  function paintMaster(){
    const box = root && root.querySelector("#lp-master"); if(!box) return;
    const songKey = skel && skel.key ? skel.key : "", lock = hasAudio();
    box.innerHTML = `<span class="h">MASTER</span>
      <span class="lp-ms-v" title="Tempo, ke kterému se všechno srovná — z písně; měnit jde, když jsou stopy prázdné">♩ <input type="number" id="lp-ms-bpm" min="40" max="240" step="0.1" value="${cfg.bpm}" ${lock ? "disabled" : ""}> BPM</span>
      <span class="lp-ms-v" title="Kolik dob má takt">Takt <select id="lp-ms-per" ${lock ? "disabled" : ""}>${[2, 3, 4, 5, 6, 7].map(n => `<option value="${n}" ${cfg.perBar === n ? "selected" : ""}>${n}/4</option>`).join("")}</select></span>
      <span class="lp-ms-v" title="Tónina, do které se ladí samply a kterou ukazuje klavír">Tónina <select id="lp-ms-key"><option value="">${songKey ? "z písně (" + esc(songKey) + ")" : "z písně (neznámá)"}</option>${KEYS().map(k => `<option value="${k}" ${cfg.masterKey === k ? "selected" : ""}>${k}</option>`).join("")}</select></span>
      <span class="lp-ms-v">smyčka 1 takt = ${barLen().toFixed(2).replace(".", ",")} s</span>
      ${lock ? `<span class="lp-ms-lock" title="Smyčky jsou nahrané v tomhle tempu a taktu">🔒 tempo a takt drží nahrané smyčky</span>` : ""}`;
    const q = (id) => box.querySelector("#" + id);
    q("lp-ms-bpm").onchange = (e) => { setBpm(parseFloat(e.target.value)); drumCache = {}; paintMaster(); paintComp(); paintSlice(); };
    q("lp-ms-per").onchange = (e) => { if(hasAudio()) return; cfg.perBar = +e.target.value; keep(); drumCache = {}; paint(); };
    q("lp-ms-key").onchange = async (e) => {
      cfg.masterKey = e.target.value; keep();
      // the samples in the tracks into the new key
      const again = tracks.filter(t => t.layers[0] && t.layers[0].sample && SM.list);
      for(const t of again){ const sm = t.layers[0].sample, x = SM.list.find(y => y.dir === sm.dir && y.rel === sm.rel); if(x) await sampleToTrack(x, t.i); }
      pads.forEach(pd => { if(pd.sample) pd.sample.fit = null; });
      showToast(`Tónina ${masterKey() || "z písně"}` + (again.length ? ` — ${again.length} ${again.length === 1 ? "sample přeladěn" : "samply přeladěny"}` : ""));
      paint(); paintPiano(); paintSamples(); paintComp();
    };
  }

  // ---- the piano (Klavír): two octaves on the screen, the computer's keys (A W S E D F T G Y H U J K, octave
  // with − and =) and a MIDI keyboard play the chosen MIDI track's instrument; ● Nahrát records what is played
  // into its loop (from the next bar, its notes put in time by the track's quantize). The song's key is dotted
  // on the keys. Open, its keys go before the pads'.
  const PIANO_KEYS = ["KeyA", "KeyW", "KeyS", "KeyE", "KeyD", "KeyF", "KeyT", "KeyG", "KeyY", "KeyH", "KeyU", "KeyJ", "KeyK"];
  const PC = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  let piano = {open: false, oct: 4, track: null, down: new Set()};
  const pianoTrack = () => { const t = piano.track != null ? tracks[piano.track] : null; return t && t.src === "midi" ? t : (tracks.find(x => x.src === "midi") || null); };
  // the song's key as pitch classes ("Em" → E minor's seven notes)
  function scaleOf(key){
    const m = String(key || "").match(/^([A-G])([#b]?)\s*(m|min|moll)?/i); if(!m) return null;
    let r = PC.indexOf(m[1].toUpperCase() + (m[2] === "#" ? "#" : "")); if(m[2] === "b") r = (PC.indexOf(m[1].toUpperCase()) + 11) % 12;
    const steps = m[3] ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
    return new Set(steps.map(x => (r + x) % 12));
  }
  function pianoOn(m, v){ const t = pianoTrack(); if(!t){ showToast("Žádná MIDI stopa — přepni stopu na „MIDI nástroj“"); return; } piano.down.add(m); noteOn(m, v, undefined, t); paintPianoKeys(); }
  function pianoOff(m){ piano.down.delete(m); noteOff(m); paintPianoKeys(); }
  function paintPianoKeys(){ root && root.querySelectorAll("#lp-piano [data-pm]").forEach(k => k.classList.toggle("down", piano.down.has(+k.dataset.pm))); }
  function paintPiano(){
    const box = root && root.querySelector("#lp-piano"); if(!box) return;
    box.hidden = !piano.open; if(!piano.open) return;
    const t = pianoTrack(), sc = scaleOf(skel && skel.key), base = 12 * (piano.oct + 1), rec = t && t.job;
    const keys = []; for(let i = 0; i < 25; i++) keys.push(base + i);
    const white = keys.filter(m => ![1, 3, 6, 8, 10].includes(m % 12));
    box.innerHTML = `<div class="lp-pn-top"><span class="h">KLAVÍR</span>
        <select id="lp-pn-track" title="Do které MIDI stopy hraje a nahrává">${tracks.map(x => `<option value="${x.i}" ${t === x ? "selected" : ""} ${x.src !== "midi" ? "disabled" : ""}>Stopa ${x.i + 1}${x.src === "midi" ? " · " + esc(instrName(x.instr)) : " (není MIDI)"}</option>`).join("")}</select>
        ${t ? `<select id="lp-pn-instr" title="Zvuk stopy">${instrOptions()}</select>` : ""}
        <span class="lp-f">Oktáva <button type="button" class="lp-b" id="lp-pn-dn">−</button><b>C${piano.oct}</b><button type="button" class="lp-b" id="lp-pn-up">=</button></span>
        <button type="button" class="lp-b lp-pn-rec ${rec ? "on" : ""}" id="lp-pn-rec" ${t ? "" : "disabled"} title="Nahrát do smyčky stopy, co zahraješ — od dalšího taktu (další klik nahrávání ukončí)">● ${rec ? "Nahrávám" : "Nahrát"}</button>
        ${sc ? `<span class="lp-pn-key">tónina ${esc(skel.key)} · tečka = tón z tóniny</span>` : ""}
        <span class="lp-pb-hint">klávesy A W S E D F T G Y H U J K · oktáva − =</span></div>
      <div class="lp-pn-kb">${white.map(m => `<button type="button" class="w" data-pm="${m}">${sc && sc.has(m % 12) ? "<i></i>" : ""}<span>${m % 12 === 0 ? "C" + (Math.floor(m / 12) - 1) : ""}</span></button>`).join("")}
        ${keys.filter(m => [1, 3, 6, 8, 10].includes(m % 12)).map(m => { const wi = white.filter(w => w < m).length; return `<button type="button" class="b" data-pm="${m}" style="left:calc(${wi} * (100% / ${white.length}) - (100% / ${white.length}) * .3)">${sc && sc.has(m % 12) ? "<i></i>" : ""}</button>`; }).join("")}</div>`;
    const q = (id) => box.querySelector("#" + id);
    q("lp-pn-track").onchange = (e) => { piano.track = +e.target.value; paintPiano(); };
    if(q("lp-pn-instr")){ q("lp-pn-instr").value = t.instr; q("lp-pn-instr").onchange = (e) => { t.instr = e.target.value; rerenderAll(t); paint(); }; }
    q("lp-pn-dn").onclick = () => { piano.oct = Math.max(1, piano.oct - 1); paintPiano(); };
    q("lp-pn-up").onclick = () => { piano.oct = Math.min(6, piano.oct + 1); paintPiano(); };
    q("lp-pn-rec").onclick = () => { if(t){ sel = t.i; press(t.i); setTimeout(paintPiano, 50); } };
    box.querySelectorAll("[data-pm]").forEach(k => {
      const m = +k.dataset.pm;
      k.onpointerdown = (e) => { e.preventDefault(); k.setPointerCapture(e.pointerId); const r = k.getBoundingClientRect(); pianoOn(m, 0.35 + 0.65 * Math.min(1, (e.clientY - r.top) / r.height)); };
      k.onpointerup = k.onpointercancel = () => pianoOff(m);
    });
  }

  // ---- the samples (Samply): the player's own sounds from mapped folders. A loop dragged onto a track is fitted
  // to the master: as many whole bars as it is long (by the tempo in its name, else by its length), stretched to
  // them without its pitch moving, and moved into the song's key (by its name's key, at most six half-tones) without
  // its length moving — Signalsmith Stretch, offline, as the mixer re-pitches stems. A short sound (a hit) goes at
  // the start of a one-bar loop. Onto a pad: a loop starts on the bar, a hit at once.
  const SM = {list: null, dirs: [], q: "", kind: "", open: true, prev: null, cache: new Map()};
  let stretchMod = null;
  async function fitAudio(buf, wantSec, semis){
    if(!stretchMod) stretchMod = (await import("/static/stretch/SignalsmithStretch.mjs")).default;
    const sr = buf.sampleRate, n = Math.max(1, Math.round(wantSec * sr)), off = new OfflineAudioContext(2, n, sr);
    const node = await stretchMod(off, {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2]});
    await node.addBuffers([buf.getChannelData(0).slice(), buf.getChannelData(Math.min(1, buf.numberOfChannels - 1)).slice()]);
    node.connect(off.destination);
    node.schedule({outputTime: 0, active: true, input: 0, rate: buf.duration / wantSec, semitones: semis || 0});
    return off.startRendering();
  }
  const keyRoot = (k) => { const m = String(k || "").match(/^([A-G])([#b]?)(m?)/); if(!m) return null; let r = PC.indexOf(m[1] + (m[2] === "#" ? "#" : "")); if(m[2] === "b") r = (PC.indexOf(m[1]) + 11) % 12; return {r, minor: !!m[3]}; };
  // half-tones from a sample's key to the song's (its relative when one is minor and the other not), −6…+6
  function keyShift(sampleKey){
    const a = keyRoot(sampleKey), b = keyRoot(masterKey()); if(!a || !b) return 0;
    let target = b.r; if(a.minor !== b.minor) target = (b.r + (a.minor ? 9 : 3)) % 12;
    let d = ((target - a.r) % 12 + 12) % 12; if(d > 6) d -= 12; return d;
  }
  const masterKey = () => cfg.masterKey || (skel && skel.key) || "";
  async function loadSample(x){
    const k = x.sound ? "s|" + x.sound : x.dir + "|" + x.rel;
    if(SM.cache.has(k)) return SM.cache.get(k);
    audio();
    const r = await fetch(x.sound ? `/api/looper/sounds/${x.sound}` : `/api/looper/samples/file?dir=${x.dir}&rel=${encodeURIComponent(x.rel)}`);
    if(!r.ok) throw new Error("soubor se nepodařilo načíst");
    const buf = await ac.decodeAudioData(await r.arrayBuffer());
    SM.cache.set(k, buf); return buf;
  }
  // how a sample sits in the master: a loop of so many bars, or a hit
  function sampleBars(x, buf){
    const d = buf.duration;
    if(x.bpm){ const b = Math.max(1, Math.round(d * x.bpm / 60 / cfg.perBar)); return b; }
    if(d < barLen() * 0.75) return 0;
    const b = d / barLen(), pick = [1, 2, 4, 8, 16].find(o => Math.abs(b - o) / o < 0.15);
    return pick || Math.max(1, Math.round(b));
  }
  async function sampleFitted(x){
    const buf = await loadSample(x), bars = sampleBars(x, buf), semis = keyShift(x.key);
    if(!bars){                                             // a hit: in tune, as long as it is
      const out2 = semis ? await fitAudio(buf, buf.duration, semis) : buf;
      return {buf: out2, bars: 0, semis, rate: 1};
    }
    const want = bars * barLen(), rate = buf.duration / want;
    const out2 = Math.abs(rate - 1) < 0.002 && !semis ? buf : await fitAudio(buf, want, semis);
    return {buf: out2, bars, semis, rate};
  }
  async function sampleToTrack(x, ti){
    audio();
    const t = tracks[ti]; if(!t) return;
    if(t.job){ showToast("Do stopy se právě nahrává"); return; }
    showToast(`Načítám „${x.name}“…`);
    let f; try{ f = await sampleFitted(x); }catch(e){ showToast("Sample se nepodařilo načíst: " + e.message); return; }
    let buf = f.buf, bars = f.bars;
    if(!bars){                                             // a hit at the start of a bar's loop
      bars = 1; const n = Math.round(barLen() * ac.sampleRate), b2 = ac.createBuffer(2, n, ac.sampleRate);
      for(let c = 0; c < 2; c++) b2.getChannelData(c).set(f.buf.getChannelData(Math.min(c, f.buf.numberOfChannels - 1)).subarray(0, n));
      buf = b2;
    }
    t.layers.forEach(stopLayer); t.layers = []; t.undone = [];
    t.len = bars * barLen(); t.bars = 0; t.anchor = running ? nextBar(now()) : t0;
    t.name = x.name.slice(0, 24);
    t.layers.push({buf, sample: {dir: x.dir, rel: x.rel}}); history.push(t.i);
    if(running) startLayer(t, t.layers[0], t.anchor);
    if(scenes[cur]) scenes[cur].on.add(t.i);
    const inp = root.querySelector(`.lp-t[data-i="${ti}"] .nm input`); if(inp) inp.value = t.name;
    showToast(`„${x.name}“ → stopa ${ti + 1}: ${bars} ${bars === 1 ? "takt" : bars < 5 ? "takty" : "taktů"}`
      + (f.bars && Math.abs(f.rate - 1) > 0.01 ? ` · tempo ${f.rate > 1 ? "−" : "+"}${Math.round(Math.abs(1 / f.rate - 1) * 100)} %` : "")
      + (f.semis ? ` · přeladěno ${f.semis > 0 ? "+" : ""}${f.semis} do ${masterKey()}` : ""));
    paint();
  }
  // ---- the samples' side panel: the mapped folders as a tree (folded as they were left), a search, loops /
  // hits; a click plays a sound at once (streamed, nothing decoded first), ↑ ↓ go through them playing each,
  // a double click puts it on a new pad, and any of them is dragged onto a track or a pad. The list is the
  // server's kept index — ↻ walks the folders again.
  async function loadSamples(refresh){
    SM.busy = true; if(refresh) paintSamples();
    try{ const d = await (await fetch("/api/looper/samples" + (refresh ? "?refresh=1" : ""))).json(); SM.list = d.samples || []; SM.dirs = d.dirs || []; }catch(e){ SM.list = SM.list || []; }
    SM.busy = false;
    if(SM.list.length && cfg.side == null){ cfg.side = true; keep(); applySide(); }
    paintSamples();
  }
  const sampleUrl = (x) => x.sound ? `/api/looper/sounds/${x.sound}` : `/api/looper/samples/file?dir=${x.dir}&rel=${encodeURIComponent(x.rel)}`;
  let smEl = null, smNode = null;
  function smPlay(i){
    audio();
    const x = SM.list[i]; if(!x) return;
    if(!smEl){ smEl = new Audio(); smEl.preload = "auto"; smNode = ac.createMediaElementSource(smEl); const g = ac.createGain(); smNode.connect(g); g.connect(bus); SM.gain = g;
      smEl.onended = () => { SM.prev = null; markPlaying(); }; smEl.onerror = () => { if(SM.prev != null){ showToast("Tenhle zvuk se nepodařilo přehrát"); SM.prev = null; markPlaying(); } }; }
    SM.gain.gain.value = cfg.smVol ?? 0.8;
    if(SM.prev === i && !smEl.paused){ smEl.pause(); SM.prev = null; markPlaying(); return; }
    if(ac.state === "suspended") ac.resume();
    smEl.src = sampleUrl(x); smEl.currentTime = 0; smEl.play().catch(() => {});
    SM.prev = i; markPlaying();
  }
  function smStop(){ if(smEl && !smEl.paused) smEl.pause(); SM.prev = null; markPlaying(); }
  function markPlaying(){
    const box = root && root.querySelector("#lp-samples"); if(!box) return;
    box.querySelectorAll(".lp-sm-row").forEach(r => { const i = +r.dataset.i; r.classList.toggle("on", SM.prev === i); r.classList.toggle("sel", SM.sel === i);
      const b = r.querySelector(".pv"); if(b) b.textContent = SM.prev === i ? "■" : "▶"; });
    cancelAnimationFrame(SM.raf);
    const tick = () => { const r = box.querySelector(".lp-sm-row.on .bar"); if(!r || !smEl || smEl.paused){ return; }
      r.style.width = (smEl.duration ? smEl.currentTime / smEl.duration * 100 : 0) + "%"; SM.raf = requestAnimationFrame(tick); };
    if(SM.prev != null) SM.raf = requestAnimationFrame(tick);
  }
  function smMove(d){
    const vis = SM.vis || []; if(!vis.length) return;
    let k = vis.indexOf(SM.sel); k = k < 0 ? (d > 0 ? 0 : vis.length - 1) : Math.max(0, Math.min(vis.length - 1, k + d));
    SM.sel = vis[k];
    if(cfg.smAuto !== false) smPlay(SM.sel); else markPlaying();
    const r = root.querySelector(`#lp-samples .lp-sm-row[data-i="${SM.sel}"]`); if(r) r.scrollIntoView({block: "nearest"});
  }
  function applySide(){ if(!root) return; root.classList.toggle("lp-side-on", !!cfg.side); const b = root.querySelector("#lp-side-btn"); if(b) b.classList.toggle("on", !!cfg.side); }
  function paintSamples(){
    const box = root && root.querySelector("#lp-samples"); if(!box) return;
    if(SM.list == null){ box.innerHTML = `<div class="lp-sm-top"><span class="h">SAMPLY</span><span class="lp-pb-hint">načítám…</span></div>`; if(!SM.busy) loadSamples(); return; }
    const q = SM.q.trim().toLowerCase(), mk = masterKey(), fold = cfg.smFold || (cfg.smFold = {});
    const list = SM.list.filter(x => (!q || (x.name + " " + x.folder).toLowerCase().includes(q)) && (!SM.kind || (SM.kind === "loop" ? x.bpm : !x.bpm)));
    const row = (x) => { const i = SM.list.indexOf(x), sh = keyShift(x.key);
      return `<div class="lp-sm-row ${SM.prev === i ? "on" : ""} ${SM.sel === i ? "sel" : ""}" draggable="true" data-i="${i}" title="${esc(x.folder ? x.folder + "/" : "")}${esc(x.name)} — klik: poslech · dvojklik: na nový pad · táhni na stopu nebo pad">
        <button type="button" class="pv" tabindex="-1">${SM.prev === i ? "■" : "▶"}</button><b>${esc(x.name)}</b>${q && x.folder ? `<small>${esc(x.folder)}</small>` : ""}
        ${x.bpm ? `<i class="bpm">${x.bpm}</i>` : ""}${x.key ? `<i class="key ${sh ? "sh" : "ok"}" title="${sh ? `přeladí se o ${sh > 0 ? "+" : ""}${sh} půltónu do ${esc(mk)}` : "sedí s tóninou"}">${esc(x.key)}</i>` : ""}<span class="bar"></span></div>`; };
    let body = "", vis = [];
    if(!SM.dirs.length) body = `<div class="lp-sm-none">Namapuj složku se svými samply (i na kartě) — Pick si ji projde jednou a pak se otevírá hned. Tempo a tóninu přečte z názvu (např. „Groove 120bpm Am.wav“).</div>`;
    else if(q || SM.kind){
      const shown = list.slice().sort((x, y) => x.folder.localeCompare(y.folder, "cs") || x.name.localeCompare(y.name, "cs", {numeric: true})).slice(0, 600); vis = shown.map(x => SM.list.indexOf(x));
      body = shown.length ? shown.map(row).join("") + (list.length > 600 ? `<div class="lp-sm-none">…a dalších ${list.length - 600} — upřesni hledání.</div>` : "") : `<div class="lp-sm-none">Nic takového.</div>`;
    } else {
      SM.dirs.forEach(d => {
        const mine = list.filter(x => x.dir === d.i), fk = d.path, open = !fold[fk];
        body += `<div class="lp-sm-dirh ${d.exists ? "" : "gone"}" data-fold="${esc(fk)}" title="${esc(d.path)}${d.exists ? "" : " — složka teď není dostupná (karta vysunutá?)"}"><span>${open ? "▾" : "▸"}</span><b>${esc(d.name)}</b><small>${mine.length}</small>
          <button type="button" data-rm="${esc(d.path)}" title="Odmapovat (nic se nesmaže)">✕</button></div>`;
        if(!open) return;
        const groups = new Map(); mine.forEach(x => { const g = x.folder || ""; if(!groups.has(g)) groups.set(g, []); groups.get(g).push(x); });
        [...groups.keys()].sort((a, b) => a.localeCompare(b, "cs")).forEach(g => {
          const gk = d.path + "|" + g;
          const isOpen = g === "" ? true : (gk in fold ? !fold[gk] : !cfg.smFoldAll);
          groups.get(g).sort((x, y) => x.name.localeCompare(y.name, "cs", {numeric: true}));
          if(g) body += `<div class="lp-sm-fold-h" data-fold="${esc(gk)}" style="--d:0"><span>${isOpen ? "▾" : "▸"}</span>${esc(g.split("/").join(" / "))}<small>${groups.get(g).length}</small></div>`;
          if(isOpen){ body += groups.get(g).map(row).join(""); vis.push(...groups.get(g).map(x => SM.list.indexOf(x))); }
        });
      });
    }
    SM.vis = vis;
    const dirsScanned = SM.dirs.filter(d => d.scanned).map(d => d.scanned), when = dirsScanned.length ? new Date(Math.min(...dirsScanned) * 1000).toLocaleDateString("cs-CZ") : "";
    const scroll = (box.querySelector(".lp-sm-list") || {}).scrollTop || 0;
    box.innerHTML = `<div class="lp-sm-top"><span class="h">SAMPLY</span><span class="lp-pb-hint">${SM.list.length}</span><span class="lp-sp"></span>
        <button type="button" class="lp-b" id="lp-sm-add" title="Namapovat složku se samply (otevře se výběr složky) — nic se nekopíruje">＋ Složka</button>
        <button type="button" class="lp-b ${SM.busy ? "on" : ""}" id="lp-sm-re" title="Projít složky znovu (nové a smazané soubory)${when ? " — naposledy " + when : ""}">${SM.busy ? "…" : "↻"}</button>
        <button type="button" class="lp-x" id="lp-sm-x" title="Skrýt panel">${icon("close", 12)}</button></div>
      <input type="search" id="lp-sm-q" placeholder="Hledat sample…" value="${esc(SM.q)}">
      <div class="lp-sm-tools"><span class="lp-seg"><button type="button" data-kind="" class="${!SM.kind ? "on" : ""}">vše</button><button type="button" data-kind="loop" class="${SM.kind === "loop" ? "on" : ""}">smyčky</button><button type="button" data-kind="hit" class="${SM.kind === "hit" ? "on" : ""}">údery</button></span>
        <label class="lp-chk" title="Klik nebo šipky ↑ ↓ rovnou přehrají"><input type="checkbox" id="lp-sm-auto" ${cfg.smAuto !== false ? "checked" : ""}> přehrávat</label>
        <input type="range" id="lp-sm-vol" min="0" max="1.2" step="0.01" value="${cfg.smVol ?? 0.8}" title="Hlasitost poslechu">
        ${!q && SM.dirs.length ? `<button type="button" class="lp-sm-all" id="lp-sm-all" title="Sbalit nebo rozbalit všechny podsložky">${cfg.smFoldAll ? "rozbalit" : "sbalit"} vše</button>` : ""}</div>
      <div class="lp-sm-list">${body}</div>
      <div class="lp-sm-foot">${mk ? `ladí se do ${esc(mk)} · ` : ""}${cfg.bpm} BPM · táhni na stopu nebo pad · ↑ ↓ procházet</div>`;
    const qq = (id) => box.querySelector("#" + id), L = box.querySelector(".lp-sm-list");
    L.scrollTop = scroll;
    qq("lp-sm-x").onclick = () => { cfg.side = false; keep(); smStop(); applySide(); };
    qq("lp-sm-add").onclick = async () => { const r = await fetch("/api/looper/samples/dirs", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"}); const d = await r.json().catch(() => ({})); if(d.error) showToast(d.error); if(d.added) showToast("Namapováno: " + d.added); loadSamples(); };
    qq("lp-sm-re").onclick = () => loadSamples(true);
    const sq = qq("lp-sm-q"); sq.oninput = () => { clearTimeout(SM.t); SM.t = setTimeout(() => { SM.q = sq.value; paintSamples(); const n2 = root.querySelector("#lp-sm-q"); n2.focus(); n2.setSelectionRange(n2.value.length, n2.value.length); }, 150); };
    sq.onkeydown = (e) => { if(e.key === "ArrowDown" || e.key === "ArrowUp"){ e.preventDefault(); sq.blur(); smMove(e.key === "ArrowDown" ? 1 : -1); } };
    box.querySelectorAll("[data-kind]").forEach(b => b.onclick = () => { SM.kind = b.dataset.kind; paintSamples(); });
    qq("lp-sm-auto").onchange = (e) => { cfg.smAuto = e.target.checked; keep(); };
    qq("lp-sm-vol").oninput = (e) => { cfg.smVol = +e.target.value; keep(); if(SM.gain) SM.gain.gain.value = cfg.smVol; };
    const all = qq("lp-sm-all"); if(all) all.onclick = () => { cfg.smFoldAll = !cfg.smFoldAll; Object.keys(cfg.smFold).forEach(k => { if(k.includes("|")) delete cfg.smFold[k]; }); keep(); paintSamples(); };
    box.querySelectorAll("[data-fold]").forEach(h => h.onclick = (e) => { if(e.target.closest("[data-rm]")) return; const k = h.dataset.fold, isG = k.includes("|");
      const openNow = isG ? (k in cfg.smFold ? !cfg.smFold[k] : !cfg.smFoldAll) : !cfg.smFold[k]; cfg.smFold[k] = openNow; keep(); paintSamples(); });
    box.querySelectorAll("[data-rm]").forEach(b => b.onclick = async () => { if(!(await Dialog.confirm(`Odmapovat složku „${b.dataset.rm}“? Soubory zůstanou, jen zmizí z panelu.`, {ok: "Odmapovat"}))) return;
      await fetch("/api/looper/samples/dirs/remove", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({path: b.dataset.rm})}); loadSamples(); });
    box.querySelectorAll(".lp-sm-row").forEach(r => { const i = +r.dataset.i;
      r.onclick = (e) => { SM.sel = i; if(cfg.smAuto !== false || e.target.closest(".pv")) smPlay(i); else markPlaying(); };
      r.ondblclick = () => { const k = addPad(); if(k >= 0){ setPadSample(k, SM.list[i]); } };
      r.ondragstart = (e) => { e.dataTransfer.setData("text/pick-sample", String(i)); e.dataTransfer.effectAllowed = "copy"; }; });
    markPlaying();
  }

  // ---- the pads: as many as wanted (eight to start: the kit's drums in a style). On one goes a sound of the
  // player's own (any file, dropped from the Finder or chosen — the server keeps a copy), a sample from the
  // mapped folders, or a piece of a song. Each pad has its key (Q W E R / A S D F, new ones the numeric keypad's,
  // any other can be taught) and a MIDI note. A drum or a short sound plays at once (again on each tap); a
  // loop or a piece starts on the next bar and a tap while it plays stops it. Right click: the pad's options.
  const PAD_KEYS = ["KeyQ", "KeyW", "KeyE", "KeyR", "KeyA", "KeyS", "KeyD", "KeyF"];
  const NUM_KEYS = ["Numpad7", "Numpad8", "Numpad9", "Numpad4", "Numpad5", "Numpad6", "Numpad1", "Numpad2", "Numpad3", "Numpad0",
    "NumpadDecimal", "NumpadEnter", "NumpadAdd", "NumpadSubtract", "NumpadMultiply", "NumpadDivide"];
  const RESERVED = ["Space", "Enter", "Tab", "Escape", "KeyZ", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Digit1", "Digit2", "Digit3", "Digit4", "Digit5",
    "Delete", "Backspace", "Minus", "Equal", "ShiftLeft", "ShiftRight", "CapsLock"];
  const KEY_SIGN = {BracketLeft: "[", BracketRight: "]", Quote: "'", Semicolon: ";", Comma: ",", Period: ".", Slash: "/", Backslash: "\\", Backquote: "`", IntlBackslash: "§"};
  const keyLabel = (c) => !c ? "" : c.startsWith("Key") ? c.slice(3) : c.startsWith("Digit") ? c.slice(5)
    : c.startsWith("Numpad") ? "num " + ({Decimal: ",", Enter: "↵", Add: "+", Subtract: "−", Multiply: "×", Divide: "÷"}[c.slice(6)] || c.slice(6)) : KEY_SIGN[c] || c;
  const padOf = (q, i) => { const k = i < 8 ? LoopSound.KIT[i] : null; q = q || {};
    return {note: q.note !== undefined ? q.note : (k ? k.note : null), drum: q.drum !== undefined ? q.drum : (k ? k.id : null), name: k ? k.name : "Pad " + (i + 1),
      label: q.label || "", key: q.key !== undefined ? q.key : (PAD_KEYS[i] || null), piece: q.piece || null,
      sample: q.sample && q.sample.x ? {x: q.sample.x, name: q.sample.x.name, bars: q.sample.bars} : null, src: null}; };
  let pads = [], padBus = null, padLearn = null;      // padLearn {i, kind: "key" | "midi"}: the next key or note goes to that pad
  function stopPad(pd){ if(pd && pd.src){ try{ pd.src.stop(); }catch(e){} pd.src = null; } }
  function padsFrom(list){ pads.forEach(stopPad); const n = Math.min(64, Math.max(8, Array.isArray(list) ? list.length : 0)); pads = Array.from({length: n}, (_, i) => padOf(list && list[i], i)); }
  const padsJSON = () => pads.map(pd => ({key: pd.key, note: pd.note, drum: pd.drum, label: pd.label || undefined, piece: pd.piece, sample: pd.sample ? {x: pd.sample.x, bars: pd.sample.bars} : null}));
  const padsKeep = () => { cfg.pads = padsJSON(); keep(); };
  padsFrom(cfg.pads);
  const freeKey = () => NUM_KEYS.find(c => !pads.some(p => p.key === c)) || null;
  const padTitle = (pd) => pd.label || (pd.sample ? pd.sample.name : pd.piece ? pd.piece.name : pd.drum ? pd.name : "Prázdný pad");
  const drumNote = (pd) => { const k = LoopSound.KIT.find(x => x.id === pd.drum); return k ? k.note : pd.note; };
  function padHit(i){
    audio();
    const pd = pads[i]; if(!pd) return;
    flashPadBank(i);
    if(pd.sample){
      if(pd.src){ try{ pd.src.stop(running ? nextBar(now()) : 0); }catch(e){} pd.src = null; paintPads(); return; }
      (async () => { let f; try{ f = pd.sample.fit || (pd.sample.fit = await sampleFitted(pd.sample.x)); }catch(e){ showToast("Zvuk se nepodařilo načíst: " + e.message); return; }
        const sr = ac.createBufferSource(); sr.buffer = f.buf; sr.connect(bus);
        sr.start(f.bars && running ? nextBar(now()) : now() + 0.003);
        if(f.bars){ pd.src = sr; sr.onended = () => { if(pd.src === sr){ pd.src = null; paintPads(); } }; paintPads(); } })();   // a hit: again on each tap
      return;
    }
    if(pd.piece){
      if(pd.src){ try{ pd.src.stop(running ? nextBar(now()) : 0); }catch(e){} pd.src = null; paintPads(); return; }
      if(pd.loading) return; pd.loading = true;
      pieceAudio(pd.piece).then(buf => { pd.loading = false; if(!buf){ showToast("V kousku nehraje žádná stopa"); return; }
        const sr = ac.createBufferSource(); sr.buffer = buf; sr.connect(bus); sr.start(running ? Math.max(nextBar(now()), now() + 0.01) : now() + 0.01); pd.src = sr;
        sr.onended = () => { if(pd.src === sr){ pd.src = null; paintPads(); } }; paintPads(); })
        .catch(e => { pd.loading = false; showToast("Kousek se nepodařilo připravit: " + e.message); });
      return;
    }
    if(pd.drum){
      const n = drumNote(pd);
      if(recTrack && recTrack.src === "drums" && recTrack.job){ noteOn(n, 0.95); setTimeout(() => noteOff(n), 120); return; }
      if(!padBus){ padBus = ac.createGain(); padBus.gain.value = 0.9; padBus.connect(bus); }
      LoopSound.drum(ac, padBus, n, 0.95, now() + 0.003);
      return;
    }
    showToast(`Pad ${i + 1} je prázdný — přetáhni na něj zvuk (i soubor z Finderu), sampl nebo kousek písně · pravé tlačítko = možnosti`);
  }
  function padPiece(i, snap){ const pd = pads[i]; if(!pd) return; stopPad(pd); pd.sample = null; pd.piece = snap; padsKeep(); paintPads();
    showToast(`Pad ${i + 1}: ${snap.name} (${snap.bars} t.) — spustí se na začátku taktu`); }
  function setPadSample(i, x, quiet){ const pd = pads[i]; if(!pd) return; stopPad(pd); pd.piece = null; pd.sample = {x, name: x.name, bars: !!x.bpm};
    if(!quiet){ padsKeep(); paintPads(); showToast(`Pad ${i + 1}: ${x.name}`); } }
  function addPad(){ if(pads.length >= 64){ showToast("Víc než 64 padů nejde"); return -1; } pads.push(padOf({key: freeKey(), drum: null, note: null}, pads.length)); padsKeep(); paintPads(); return pads.length - 1; }
  async function uploadSound(file){
    if(file.size > 300 * 1024 * 1024) throw new Error("soubor je větší než 300 MB");
    const fd = new FormData(); fd.append("audio", file, file.name);
    const r = await fetch("/api/looper/sounds", {method: "POST", body: fd}), d = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(d.error || "zvuk se nenahrál");
    return {sound: d.id, name: d.name};
  }
  // files onto a pad: the first on it, the rest on the next empty pads (new ones when there are none)
  async function filesToPads(files, i){
    audio();
    let last = i == null ? -1 : i - 1, done = 0;
    for(const f of files.slice(0, 64)){
      let k = i != null && !done ? i : pads.findIndex((p, j) => j > last && !p.sample && !p.piece && !p.drum);
      if(k < 0){ if(pads.length >= 64) break; pads.push(padOf({key: freeKey(), drum: null, note: null}, pads.length)); k = pads.length - 1; }
      last = k;
      showToast(`Nahrávám „${f.name}“…`);
      try{ setPadSample(k, await uploadSound(f), true); done++; }
      catch(e){ showToast(`„${f.name}“: ${e.message}`); if(i != null && !done) i = null; }
    }
    padsKeep(); paintPads();
    if(done) showToast(done === 1 ? `Pad ${last + 1}: ${pads[last].sample.name}` : `${done} zvuků na padech`);
  }
  function pickFiles(i){
    const inp = document.createElement("input"); inp.type = "file"; inp.multiple = true;
    inp.accept = "audio/*,video/*,.wav,.aif,.aiff,.aifc,.caf,.mp3,.m4a,.aac,.flac,.ogg,.opus,.wma,.mp4,.mov";
    inp.onchange = () => { if(inp.files.length) filesToPads([...inp.files], i); };
    inp.click();
  }
  async function padMenu(i){
    const pd = pads[i]; if(!pd) return;
    const items = [
      {label: "Zvuk ze souboru…", sub: "jakýkoli zvuk nebo video (WAV, AIFF, MP3, FLAC, M4A…) — víc souborů naplní další pady", value: "file"},
      {label: `Klávesa: ${keyLabel(pd.key) || "žádná"}`, sub: "vyber a stiskni klávesu — třeba na numerické klávesnici", value: "key"},
      {label: `MIDI: ${pd.note != null ? "nota " + pd.note : "žádná"}`, sub: "vyber a zahraj notu nebo pad na MIDI klávesách", value: "midi"},
      {label: "Přejmenovat", value: "name"}];
    if(pd.sample || pd.piece) items.push({label: pd.drum ? "Vrátit bicí" : "Vyprázdnit", value: "clear"});
    if(i >= 8) items.push({label: "Odebrat pad", value: "remove"});
    const v = await Dialog.pick({title: `Pad ${i + 1} · ${padTitle(pd)}`, items});
    if(v === "file") pickFiles(i);
    else if(v === "key"){ padLearn = {i, kind: "key"}; paintPads(); showToast(`Stiskni klávesu pro pad ${i + 1} (Esc zruší)`); }
    else if(v === "midi"){ await midiConnect(); padLearn = {i, kind: "midi"}; paintPads(); showToast(`Zahraj notu na MIDI klávesách pro pad ${i + 1}`); }
    else if(v === "name"){ const n = await Dialog.prompt("Název padu:", padTitle(pd)); if(n != null){ pd.label = n.trim().slice(0, 30); padsKeep(); paintPads(); } }
    else if(v === "clear"){ stopPad(pd); pd.sample = null; pd.piece = null; padsKeep(); paintPads(); }
    else if(v === "remove"){ stopPad(pd); pads.splice(i, 1); padsKeep(); paintPads(); }
  }
  function flashPadBank(i){ const b = root && root.querySelector(`.lp-pb [data-pad="${i}"]`); if(b){ b.classList.add("hit"); setTimeout(() => b.classList.remove("hit"), 120); } }
  const takesDrop = (e) => { const ty = [...e.dataTransfer.types]; return ty.includes("text/pick-piece") || ty.includes("text/pick-sample") || ty.includes("text/pick-mine") || ty.includes("Files"); };
  function dropOnPad(e, i){
    const v = e.dataTransfer.getData("text/pick-piece"), sm = e.dataTransfer.getData("text/pick-sample"), mi = e.dataTransfer.getData("text/pick-mine"), files = [...(e.dataTransfer.files || [])];
    if(files.length){ e.preventDefault(); filesToPads(files, i); return; }
    if(mi !== "" && mine[+mi]){ e.preventDefault(); if(i == null){ i = addPad(); if(i < 0) return; } padPiece(i, clone(mine[+mi])); return; }
    if(v === "" && !(sm !== "" && SM.list && SM.list[+sm])) return;
    e.preventDefault();
    if(i == null){ i = addPad(); if(i < 0) return; }
    if(v !== "") padPiece(i, pieceSnap(+v)); else setPadSample(i, SM.list[+sm]);
  }
  function paintPads(){
    const box = root && root.querySelector("#lp-pads"); if(!box) return;
    const st = LoopSound.style(), drumT = tracks.find(t => t.src === "drums");
    box.innerHTML = `<div class="lp-pb-top"><span class="h">PADY</span>
        <select id="lp-pb-style" title="Zvuk bicích podle stylu (pady, kroky i bicí stopa)">${Object.entries(LoopSound.STYLES).map(([k, v]) => `<option value="${k}" ${k === st ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>
        <select id="lp-pb-groove" title="Hotový rytmus do bicí stopy (stopa ${drumT ? drumT.i + 1 : 1}, 2 takty)"><option value="">Rytmus do bicí stopy…</option>${Object.entries(LoopSound.GROOVES).map(([k, g]) => `<option value="${k}" ${k === LoopSound.STYLE_GROOVE[st] ? "data-fit" : ""}>${esc(g.name)}${k === LoopSound.STYLE_GROOVE[st] ? " ★" : ""}</option>`).join("")}</select>
        <button type="button" class="lp-b ${piano.open ? "on" : ""}" id="lp-pb-piano" title="Virtuální klavír — hraje a nahrává do MIDI stopy">🎹 Klavír</button>
        <span class="lp-pb-hint">soubory přetáhni z Finderu, samply a kousky z vlny · pravé tlačítko: zvuk ze souboru, klávesa, MIDI nota</span></div>
      <div class="lp-pb">${pads.map((pd, i) => { const lr = padLearn && padLearn.i === i, empty = !pd.piece && !pd.sample && !pd.drum;
          const hue = pd.piece ? HUES[(pd.piece.n - 1) % HUES.length] : pd.sample ? (pd.sample.x.sound ? 168 : 195) : 230 + (i % 8) * 12;
          const sub = pd.piece ? pd.piece.bars + " t." + (pd.piece.song !== SL.song ? " · " + shortTitle(pd.piece.title || pd.piece.song) : pd.piece.stems.length < 3 ? " · " + pd.piece.stems.map(stemName).join("+") : "")
            : pd.sample ? (pd.sample.x.sound ? "vlastní zvuk" : pd.sample.x.bpm ? pd.sample.x.bpm + " BPM" : "sampl") : pd.drum ? LoopSound.STYLES[st].name : "přetáhni zvuk";
          const midi = pd.note != null && !(pd.drum && !pd.sample && !pd.piece) ? ` · ♪${pd.note}` : "";
          return `<button type="button" class="lp-pad ${pd.piece ? "piece" : ""} ${pd.src ? "on" : ""} ${empty ? "empty" : ""} ${lr ? "learn" : ""}" data-pad="${i}" style="--h:${hue}"
            title="${esc(padTitle(pd))}${pd.piece ? esc(" · " + (pd.piece.title || pd.piece.song) + " · " + pd.piece.stems.map(stemName).join("+")) : ""} — klávesa ${esc(keyLabel(pd.key) || "žádná")}, MIDI ${pd.note != null ? pd.note : "žádná"} · pravé tlačítko: možnosti">
            <b>${pd.sample ? "♫ " : ""}${esc(padTitle(pd))}</b><small>${esc(sub)}</small><i>${lr ? (padLearn.kind === "key" ? "stiskni klávesu" : "zahraj notu") : esc(keyLabel(pd.key)) + midi}</i></button>`; }).join("")}
        <button type="button" class="lp-pad lp-pad-add" id="lp-pad-add" title="Přidat pad — dostane volnou klávesu na numerické klávesnici · přetáhni sem i víc souborů najednou">＋<small>Pad</small></button></div>`;
    box.querySelector("#lp-pb-piano").onclick = () => { piano.open = !piano.open; paintPads(); paintPiano(); };
    box.querySelector("#lp-pb-style").onchange = async (e) => {
      LoopSound.setStyle(e.target.value); cfg.kit = e.target.value; keep();
      for(const t of tracks) if(t.src === "drums" && t.steps){ const l = t.layers.find(x => x.steps); if(l) await rerender(t, l, stepNotes(t)); }
      paintPads();
    };
    box.querySelector("#lp-pb-groove").onchange = (e) => { const id = e.target.value; e.target.value = ""; if(!id) return;
      const t = drumT || tracks[0]; if(t.layers.some(l => !l.steps)){ showToast(`Stopa ${t.i + 1} má nahraný zvuk — rytmus jde jen do bicí stopy s kroky`); return; }
      t.src = "drums"; setSteps(t, groove(id, 2)); showToast(`Rytmus ${LoopSound.GROOVES[id].name} → stopa ${t.i + 1}`); };
    box.querySelectorAll("[data-pad]").forEach(b => {
      const i = +b.dataset.pad;
      b.onpointerdown = (e) => { if(e.button === 0){ e.preventDefault(); if(padLearn && padLearn.i === i){ padLearn = null; paintPads(); return; } padHit(i); } };
      b.oncontextmenu = (e) => { e.preventDefault(); padMenu(i); };
      b.addEventListener("dragover", (e) => { if(takesDrop(e)){ e.preventDefault(); b.classList.add("drop"); } });
      b.addEventListener("dragleave", () => b.classList.remove("drop"));
      b.addEventListener("drop", (e) => { b.classList.remove("drop"); dropOnPad(e, i); });
    });
    const add = box.querySelector("#lp-pad-add");
    add.onclick = () => { const i = addPad(); if(i >= 0) showToast(`Pad ${i + 1}${pads[i].key ? " na klávese " + keyLabel(pads[i].key) : ""} — přetáhni na něj zvuk, nebo pravé tlačítko`); };
    add.addEventListener("dragover", (e) => { if(takesDrop(e)){ e.preventDefault(); add.classList.add("drop"); } });
    add.addEventListener("dragleave", () => add.classList.remove("drop"));
    add.addEventListener("drop", (e) => { add.classList.remove("drop"); dropOnPad(e, null); });
  }

  async function pieceToTrack(i, ti){ return snapToTrack(pieceSnap(i), ti); }
  async function snapToTrack(sn, ti){
    audio();
    const t = tracks[ti]; if(!t) return;
    if(t.job){ showToast("Do stopy se právě nahrává"); return; }
    let buf; try{ buf = await pieceAudio(sn); }catch(e){ showToast("Kousek se nepodařilo připravit: " + e.message); return; }
    if(!buf){ showToast("V kousku nehraje žádná stopa"); return; }
    t.layers.forEach(stopLayer); t.layers = []; t.undone = [];
    t.len = mBars(sn) * barLen(); t.bars = 0;                // the piece brings its own length
    t.anchor = running ? nextBar(now()) : t0;
    const e = SLS.find(x => x.song === sn.song), all = e && sn.stems.length === e.stems.length;
    t.name = (e && !e.own ? `${shortTitle(sn.title || sn.song)} · ${sn.name}` : `${sn.name} · ${all ? "celá píseň" : sn.stems.map(stemName).join("+")}`).slice(0, 24);
    t.layers.push({buf, piece: sn.n}); history.push(t.i);
    if(running) startLayer(t, t.layers[0], t.anchor);
    if(scenes[cur]) scenes[cur].on.add(t.i);
    const inp = root.querySelector(`.lp-t[data-i="${ti}"] .nm input`); if(inp) inp.value = t.name;
    const ft = fitText(sn);
    const mb = mBars(sn);
    showToast(`${sn.name} → stopa ${ti + 1}: ${mb} ${mb === 1 ? "takt" : mb < 5 ? "takty" : "taktů"}` + (ft ? ` (srovnáno: ${ft})` : ""));
    paint();
  }
  async function previewPiece(i){
    audio();
    if(SL.prev){ const was = SL.prev.piece; stopSlicePrev(); paintSlice(); if(was === i) return; }
    const me = SL; let buf;
    try{ buf = await pieceAudio(pieceSnap(i)); }catch(e){ showToast("Kousek se nepodařilo připravit: " + e.message); return; }
    if(!buf || SL !== me) return;
    stopSlicePrev();
    const s = ac.createBufferSource(); s.buffer = buf; s.loop = true; s.connect(bus); s.start(); s.piece = i; SL.prev = s;
    paintSlice();
  }
  // each stem's loudness, ~86 values a second (the wave is drawn from these at any zoom)
  const ENV_HOP = 512;
  function sliceEnv(){
    if(SL.env) return SL.env;
    const env = {}; let top = 0;
    SL.stems.forEach(x => { const d = x.buf.getChannelData(0), n = Math.ceil(d.length / ENV_HOP), e = new Float32Array(n);
      for(let i = 0; i < n; i++){ let m = 0; const a = i * ENV_HOP, z = Math.min(d.length, a + ENV_HOP); for(let k = a; k < z; k += 8){ const v = d[k] < 0 ? -d[k] : d[k]; if(v > m) m = v; } e[i] = m; }
      env[x.stem] = {e, sr: x.buf.sampleRate / ENV_HOP}; });
    const n = Math.max(...Object.values(env).map(v => v.e.length));
    for(let i = 0; i < n; i++){ let t = 0; for(const v of Object.values(env)) t += v.e[i] || 0; if(t > top) top = t; }
    SL.env = {by: env, top: top || 1};
    return SL.env;
  }
  const span = () => SL.dur / SL.zoom;
  function setView(zoom, start){
    SL.zoom = Math.max(1, Math.min(64, zoom));
    SL.vs = Math.max(0, Math.min(SL.dur - span(), start));
  }
  // the wave: each piece drawn from the tracks heard in it (a soloed drum: only the drums' wave)
  function drawSlice(){
    const cv = root && root.querySelector("#lp-sl-cv");
    if(!cv) return;
    const W = cv.clientWidth || 1200, H = 92, dpr = Math.min(2, devicePixelRatio || 1);
    if(cv.width !== Math.round(W * dpr)){ cv.width = Math.round(W * dpr); cv.height = H * dpr; }
    const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    const E = sliceEnv(), vs = SL.vs, sp = span(), X = (t) => (t - vs) / sp * W, T = (x) => vs + x / W * sp, i = SL.sel;
    SL.cuts.forEach((a, k) => {
      const q = pieceOf(k); if(X(q.z) < 0 || X(q.a) > W) return;
      const h = HUES[k % HUES.length], x0 = Math.max(0, X(q.a)), x1 = Math.min(W, X(q.z));
      g.fillStyle = `hsla(${h},70%,55%,${k === i ? 0.3 : 0.11})`; g.fillRect(x0, 0, x1 - x0, H);
      const list = heard(k).map(x => E.by[x.stem]).filter(Boolean);
      g.fillStyle = `hsl(${h},75%,${k === i ? 72 : 62}%)`;
      for(let x = Math.floor(x0); x < x1; x++){
        const ta = T(x), tb = T(x + 1); let m = 0;
        list.forEach(v => { const a = Math.floor(ta * v.sr), z = Math.max(a + 1, Math.ceil(tb * v.sr)); let mm = 0; for(let j = a; j < z && j < v.e.length; j++) if(v.e[j] > mm) mm = v.e[j]; m += mm; });
        const y = m / E.top * (H / 2 - 14); if(y > 0.3) g.fillRect(x, H / 2 + 6 - y, 1, y * 2);
      }
      g.fillStyle = "rgba(255,255,255,.88)"; g.font = "600 10px system-ui";
      if(x1 - x0 > 24) g.fillText(String(k + 1) + (heard(k).length < SL.stems.length ? " · " + heard(k).map(x => stemName(x.stem)).join("+") : ""), x0 + 4, 11);
    });
    // the bars (every bar when there is room, else every 4th), the cuts, the piece heard
    const barPx = sp && SL.db.length > 1 ? (SL.db[1] - SL.db[0]) / sp * W : 0, every = barPx > 14 ? 1 : 4;
    g.fillStyle = "rgba(255,255,255,.08)"; SL.db.forEach((t, k) => { if(k % every === 0){ const x = X(t); if(x >= 0 && x <= W) g.fillRect(Math.round(x), 14, 1, H - 14); } });
    SL.cuts.forEach((a, k) => { const x = X(a); if(x < -2 || x > W + 2) return; g.fillStyle = SL.drag && SL.drag.cut === k ? "#ffd166" : "#fff"; g.fillRect(Math.round(x) - (k ? 1 : 0), 0, k ? 3 : 2, H); });
    if(SL.prev && SL.prev.piece != null){ const q = pieceOf(SL.prev.piece); g.strokeStyle = "#fff"; g.lineWidth = 2; g.strokeRect(X(q.a) + 1, 1, X(q.z) - X(q.a) - 2, H - 2); }
    const zb = root.querySelector("#lp-sl-zoom"); if(zb){ zb.hidden = SL.zoom <= 1.01; zb.textContent = (SL.zoom < 10 ? SL.zoom.toFixed(1).replace(".", ",") : Math.round(SL.zoom)) + "×"; }
  }
  const HUES = [265, 200, 150, 35, 330, 95, 15, 180];
  function songPicker(){
    if(!LS.list){ loadSongList(); return `<div class="lp-sl-pick"><span class="lp-pb-hint">Načítám písně…</span></div>`; }
    const ref = (SLS.find(x => x.own && x.tuning) || SLS.find(x => x.tuning) || {}).tuning, q = LS.q.trim().toLowerCase();
    const rows = LS.list.filter(x => !SLS.some(e => e.song === x.name) && (!q || (x.artist + " " + x.title + " " + x.name).toLowerCase().includes(q))
        && (!LS.sameTune || !ref || sameTuning(x.tuning, ref)) && (!LS.nearTempo || (x.bpm && Math.abs(tempoRatio(x.bpm) - 1) <= 0.1)))
      .sort((a, b) => (LS.nearTempo ? Math.abs(tempoRatio(a.bpm || 1) - 1) - Math.abs(tempoRatio(b.bpm || 1) - 1) : 0) || a.title.localeCompare(b.title));
    return `<div class="lp-sl-pick"><div class="lp-sl-pick-top"><input type="search" id="lp-sl-q" placeholder="Hledat píseň nebo kapelu…" value="${esc(LS.q)}">
        <label class="lp-chk" title="${ref ? "Jen písně se stejným laděním kytary" : "Ladění otevřené písně neznám"}"><input type="checkbox" id="lp-sl-tune" ${LS.sameTune && ref ? "checked" : ""} ${ref ? "" : "disabled"}> stejné ladění${ref ? ` (${esc(tuningName(ref))})` : ""}</label>
        <label class="lp-chk" title="Tempo do 10 % od MASTERu — i poloviční nebo dvojnásobné"><input type="checkbox" id="lp-sl-tempo" ${LS.nearTempo ? "checked" : ""}> podobné tempo (${Math.round(cfg.bpm * 0.9)}–${Math.round(cfg.bpm * 1.1)} BPM)</label>
        <span class="lp-pb-hint">${rows.length} ${rows.length === 1 ? "píseň" : rows.length < 5 && rows.length ? "písně" : "písní"} · klik ji přidá</span></div>
      <div class="lp-sl-pick-list">${rows.slice(0, 150).map(x => { const r = x.bpm ? tempoRatio(x.bpm) : 0;
          return `<button type="button" class="lp-sl-pick-row" data-add="${LS.list.indexOf(x)}" title="${esc(x.name)}"><b>${esc(x.title)}</b><small>${esc(x.artist)}</small>
            <i>${x.bpm ? Math.round(x.bpm) + " BPM" + (Math.abs(r - 1) > 0.005 ? ` (${r > 1 ? "+" : "−"}${Math.round(Math.abs(r - 1) * 100)} %)` : "") : "tempo ?"}</i><i>${esc(tuningName(x.tuning) || "ladění ?")}</i></button>`; }).join("")
        || `<div class="lp-sm-none">Nic takového — zkus vypnout filtr.</div>`}</div></div>`;
  }
  function paintSlice(){
    const box = root && root.querySelector("#lp-slice");
    if(!box) return;
    const has = sliceSong();
    box.hidden = false;
    const songs = `<div class="lp-sl-songs">${SLS.map((e, k) => `<span class="lp-sl-song ${e === SL ? "on" : ""}" style="--h:${songHue(e.song)}" data-song="${k}" title="${esc(e.title)}${e.own ? " — otevřená ve Steems" : ""}">
          <b>${esc(shortTitle(e.title))}</b><small>${esc([e.bpm ? Math.round(e.bpm) + " BPM" : "", e.key, tuningName(e.tuning)].filter(Boolean).join(" · "))}</small>
          ${e.own ? `<em>Steems</em>` : `<button type="button" data-unsong="${k}" title="Zavřít píseň (kousky ve skladbě a na padech zůstanou)">✕</button>`}</span>`).join("")}
        <button type="button" class="lp-b ${LS.open ? "on" : ""}" id="lp-sl-addsong" title="Přidat další píseň — její kousky se srovnají do tempa a tóniny MASTERu">＋ Píseň</button></div>`;
    if(!has){
      box.innerHTML = `<div class="lp-sl-top"><span class="h">PÍSEŇ PO KOUSCÍCH</span><span class="lp-sl-info">Otevři píseň ve Steems, nebo přidej písně tady a skládej z jejich kousků.</span></div>${songs}${LS.open ? songPicker() : ""}`;
      wireSongs(box); return;
    }
    const i = Math.min(SL.sel, SL.cuts.length - 1), p = pieceOf(i), m = stemsIn(i), sn = pieceSnap(i), ft = fitText(sn);
    box.innerHTML = `<div class="lp-sl-top"><span class="h">PÍSEŇ PO KOUSCÍCH</span>
        <select id="lp-sl-n" title="Na kolik kousků rozsekat — vždy na začátku taktu (podle bicích)"><option value="parts" ${SL.mode === "parts" ? "selected" : ""}>podle částí písně</option>${[4, 8, 16, 32].map(n => `<option value="${n}" ${SL.mode == n ? "selected" : ""}>${n} kousků</option>`).join("")}</select>
        <button type="button" class="lp-b ${SL.scissors ? "on" : ""}" id="lp-sl-cut" title="Nůžky: klik do vlny rozstřihne kousek na nejbližším taktu · dvojklik na řez ho zruší">✂ Nůžky</button>
        <button type="button" class="lp-sl-zoom" id="lp-sl-zoom" hidden title="Vlna je přiblížená — klik ukáže celou píseň"></button>
        <span class="lp-sl-info">${SL.cuts.length} kousků · ${SL.db.length} taktů · sevřením prstů přiblížíš, dvěma prsty posuneš, řez táhni, kousek vytáhni na stopu, pad nebo do skladby</span></div>
      ${songs}${LS.open ? songPicker() : ""}
      <div class="lp-sl-wave"><canvas id="lp-sl-cv"></canvas></div>
      <div class="lp-sl-sel"><b>Kousek ${i + 1}</b><span>${esc(pieceName(i))} · ${p.bars} ${p.bars === 1 ? "takt" : p.bars < 5 ? "takty" : "taktů"} · ${Math.floor(p.a / 60)}:${String(Math.floor(p.a % 60)).padStart(2, "0")}${ft ? ` · <em title="Jak se kousek srovná do MASTERu (${cfg.bpm} BPM${masterKey() ? ", " + esc(masterKey()) : ""})">${esc(ft)}</em>` : ""}${RF.on && RF.song === SL.song && RF.piece === i ? ` · <em class="rec">● nahrává se do stopy ${RF.track ? RF.track.i + 1 : ""} — ${takt(mBars(RF.d))}</em>` : ""}${(() => { const n = mine.filter(x => x.song === SL.song && Math.abs(x.a - p.a) < 0.01).length; return n ? ` · <em>🎸 ${n}× tvůj part (Moje dílky)</em>` : ""; })()}</span>
        <div class="lp-sl-stems">${SL.stems.map(x => `<span class="stm ${m[x.stem]}" style="--c:${STEMC[x.stem] || "#aaa"}"><i></i>${esc(stemName(x.stem))}
          <button type="button" data-m="${x.stem}" class="${m[x.stem] === "mute" ? "on" : ""}" title="Ztlumit v tomhle kousku">M</button><button type="button" data-s="${x.stem}" class="${m[x.stem] === "solo" ? "on" : ""}" title="Sólo v tomhle kousku">S</button></span>`).join("")}</div>
        <button type="button" class="lp-b ${SL.prev && SL.prev.piece === i ? "on" : ""}" id="lp-sl-play" title="Poslechnout kousek dokola (s vybranými stopami, srovnaný do MASTERu)">${SL.prev && SL.prev.piece === i ? "■" : "▶"} Poslech</button>
        <button type="button" class="lp-b" id="lp-sl-comp" title="Přidat kousek na konec skladby z kousků">＋ Do skladby</button>
        <button type="button" class="lp-b lp-riff ${RF.on && RF.song === SL.song && RF.piece === i ? "on" : ""}" id="lp-sl-riff" title="Nahrát svůj part do tohohle kousku: looper převezme jeho tempo, takt a délku, kytara v něm ztichne (nebo co ztlumíš sám) a kousek hraje jako podklad. Nahrávka se zarovná na takty a vznikne tvůj dílek.">${RF.on && RF.song === SL.song && RF.piece === i ? "■ Zrušit nahrávání" : "🎸 Nahrát svůj part"}</button>
        <span class="lp-sl-to">Do stopy ${tracks.map(t => `<button type="button" data-to="${t.i}" title="Kousek jako smyčka stopy ${t.i + 1}">${t.i + 1}</button>`).join("")}</span>
        <span class="lp-sl-drag" draggable="true" title="Přetáhni na stopu, pad nebo do skladby">⠿ přetáhni</span></div>`;
    drawSlice();
    wireSliceCanvas(box.querySelector("#lp-sl-cv"));
    wireSongs(box);
    box.querySelector("#lp-sl-n").onchange = (e) => { cutBy(e.target.value); paintSlice(); };
    box.querySelector("#lp-sl-zoom").onclick = () => { setView(1, 0); drawSlice(); };
    box.querySelector("#lp-sl-cut").onclick = () => { SL.scissors = !SL.scissors; paintSlice(); };
    box.querySelector("#lp-sl-play").onclick = () => previewPiece(i);
    box.querySelector("#lp-sl-comp").onclick = () => compAdd(pieceSnap(i));
    box.querySelector("#lp-sl-riff").onclick = () => (RF.on ? riffStop() : riffStart({i}));
    box.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { const st = stemsIn(i); st[b.dataset.m] = st[b.dataset.m] === "mute" ? "on" : "mute"; if(SL.prev){ stopSlicePrev(); previewPiece(i); } else paintSlice(); });
    box.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { const st = stemsIn(i); st[b.dataset.s] = st[b.dataset.s] === "solo" ? "on" : "solo"; if(SL.prev){ stopSlicePrev(); previewPiece(i); } else paintSlice(); });
    box.querySelectorAll("[data-to]").forEach(b => b.onclick = () => pieceToTrack(i, +b.dataset.to));
    box.querySelector(".lp-sl-drag").ondragstart = (e) => { e.dataTransfer.setData("text/pick-piece", String(i)); e.dataTransfer.effectAllowed = "copy"; };
  }
  function wireSongs(box){
    box.querySelectorAll("[data-song]").forEach(b => b.onclick = (e) => { if(!e.target.closest("[data-unsong]")) useSong(+b.dataset.song); });
    box.querySelectorAll("[data-unsong]").forEach(b => b.onclick = () => dropSong(+b.dataset.unsong));
    box.querySelector("#lp-sl-addsong").onclick = () => { LS.open = !LS.open; if(LS.open) LS.list = null; paintSlice(); const q = root.querySelector("#lp-sl-q"); if(q) q.focus(); };
    const q = box.querySelector("#lp-sl-q");
    if(q) q.oninput = () => { clearTimeout(LS.t); LS.t = setTimeout(() => { LS.q = q.value; paintSlice(); const n = root.querySelector("#lp-sl-q"); if(n){ n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }, 200); };
    const tu = box.querySelector("#lp-sl-tune"); if(tu) tu.onchange = () => { LS.sameTune = tu.checked; paintSlice(); };
    const te = box.querySelector("#lp-sl-tempo"); if(te) te.onchange = () => { LS.nearTempo = te.checked; paintSlice(); };
    box.querySelectorAll("[data-add]").forEach(b => b.onclick = async () => {
      const x = LS.list[+b.dataset.add]; if(!x) return;
      b.disabled = true; showToast(`Otevírám „${x.title}“…`);
      try{ const e = await addSong(x.name); stopSlicePrev(); SL = e; paintSlice(); paintComp();
        showToast(`„${x.title}“: ${e.cuts.length} kousků · ${e.db.length} taktů${e.bpm ? " · " + Math.round(e.bpm) + " BPM" : ""} — kousky se srovnají na ${cfg.bpm} BPM`); }
      catch(err){ showToast(`„${x.title}“: ${err.message}`); b.disabled = false; }
    });
  }

  // ---- the song from pieces (Skladba z kousků): pieces of any of the open songs one after another, each on the
  // bar, fitted to the master tempo and key — played, put on a track as one long loop, or saved as a WAV
  let comp = Array.isArray(cfg.comp) ? cfg.comp.filter(b => b && b.song) : [];
  const CP = {src: null, at: 0, buf: null, sig: "", busy: false, raf: 0, pv: null, pvAt: -1};
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const compSig = () => JSON.stringify([comp.map(b => [b.song, b.a, b.z, b.bars, b.stems]), cfg.bpm, cfg.perBar, masterKey(), SLS.map(e => e.song + e.key)]);
  function compChanged(){ cfg.comp = comp; keep(); if(CP.src) compStop(); paintComp(); }
  function compAdd(sn, at){ comp.splice(at == null ? comp.length : at, 0, {...sn}); compChanged();
    showToast(`Do skladby: ${sn.name} (${shortTitle(sn.title || sn.song)}, ${sn.bars} t.)`); }
  const compNote = (t) => { const n = root && root.querySelector("#lp-cp-note"); if(n && t) n.textContent = t; };
  async function compRender(){
    const sig = compSig(); if(CP.buf && CP.sig === sig) return CP.buf;
    if(CP.busy) return null; CP.busy = true;
    try{
      const sr = ac.sampleRate, bl = barLen(), total = comp.reduce((n, b) => n + mBars(b), 0) * bl, bufs = [];
      for(const [k, b] of comp.entries()){ compNote(`Skládám ${k + 1}/${comp.length}…`); bufs.push(await pieceAudio(b)); }
      const oc = new OfflineAudioContext(2, Math.max(1, Math.ceil(total * sr)), sr);
      let at = 0;
      comp.forEach((b, k) => { if(bufs[k]){ const s = oc.createBufferSource(); s.buffer = bufs[k]; s.connect(oc.destination); s.start(at); } at += mBars(b) * bl; });
      CP.buf = await oc.startRendering(); CP.sig = sig;
      return CP.buf;
    } finally { CP.busy = false; paintComp(); }
  }
  async function compPlay(){
    audio();
    if(CP.src){ compStop(); return; }
    if(!comp.length){ showToast("Skladba je prázdná — přetáhni do ní kousky z vlny"); return; }
    let buf; try{ buf = await compRender(); }catch(e){ showToast("Skladbu se nepodařilo složit: " + e.message); return; }
    if(!buf) return;
    const s = ac.createBufferSource(); s.buffer = buf; s.connect(bus);
    const at = running ? nextBar(now()) : now() + 0.05; s.start(at);
    CP.src = s; CP.at = at;
    s.onended = () => { if(CP.src === s){ CP.src = null; paintComp(); } };
    paintComp(); compHead();
  }
  function compStop(){ if(CP.src){ const s = CP.src; CP.src = null; try{ s.stop(); }catch(e){} } paintComp(); }
  function compHead(){
    cancelAnimationFrame(CP.raf);
    const h = root && root.querySelector("#lp-cp-head"), strip = root && root.querySelector("#lp-cp-strip");
    if(!CP.src || !h || !strip){ if(h) h.style.display = "none"; return; }
    const t = (now() - CP.at) / barLen(), blocks = strip.querySelectorAll(".lp-cp-b");
    let acc = 0, x = -1;
    comp.forEach((b, k) => { const el = blocks[k], mb = mBars(b); if(!el) return; const on = t >= acc && t < acc + mb; if(on) x = el.offsetLeft + (t - acc) / mb * el.offsetWidth; el.classList.toggle("now", on); acc += mb; });
    h.style.display = x >= 0 ? "block" : "none"; h.style.left = x + "px";
    CP.raf = requestAnimationFrame(compHead);
  }
  async function compToTrack(ti){
    audio();
    const t = tracks[ti]; if(!t) return;
    if(t.job){ showToast("Do stopy se právě nahrává"); return; }
    let buf; try{ buf = await compRender(); }catch(e){ showToast("Skladbu se nepodařilo složit: " + e.message); return; }
    if(!buf) return;
    const bars = comp.reduce((n, b) => n + mBars(b), 0);
    t.layers.forEach(stopLayer); t.layers = []; t.undone = [];
    t.len = bars * barLen(); t.bars = 0; t.anchor = running ? nextBar(now()) : t0; t.name = "Skladba z kousků";
    t.layers.push({buf}); history.push(t.i);
    if(running) startLayer(t, t.layers[0], t.anchor);
    if(scenes[cur]) scenes[cur].on.add(t.i);
    const inp = root.querySelector(`.lp-t[data-i="${ti}"] .nm input`); if(inp) inp.value = t.name;
    showToast(`Skladba → stopa ${ti + 1}: ${bars} taktů (${mmss(t.len)})`);
    paint();
  }
  async function compWav(){
    audio();
    let buf; try{ buf = await compRender(); }catch(e){ showToast("Skladbu se nepodařilo složit: " + e.message); return; }
    if(!buf) return;
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([wav(buf)], {type: "audio/wav"}));
    a.download = `Skladba z kousků ${new Date().toLocaleDateString("cs-CZ")}.wav`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  async function compPreview(k){
    audio();
    if(CP.pv){ try{ CP.pv.stop(); }catch(e){} CP.pv = null; const was = CP.pvAt; CP.pvAt = -1; paintComp(); if(was === k) return; }
    let buf; try{ buf = await pieceAudio(comp[k]); }catch(e){ showToast(e.message); return; }
    if(!buf) return;
    const s = ac.createBufferSource(); s.buffer = buf; s.connect(bus); s.start(); CP.pv = s; CP.pvAt = k;
    s.onended = () => { if(CP.pv === s){ CP.pv = null; CP.pvAt = -1; paintComp(); } };
    paintComp();
  }
  function compIdxAt(x){
    const blocks = root.querySelectorAll("#lp-cp-strip .lp-cp-b");
    for(let k = 0; k < blocks.length; k++){ const r = blocks[k].getBoundingClientRect(); if(x < r.left + r.width / 2) return k; }
    return comp.length;
  }
  function paintComp(){
    const box = root && root.querySelector("#lp-comp"); if(!box) return;
    const bars = comp.reduce((n, b) => n + mBars(b), 0), mk = masterKey(), dis = comp.length ? "" : "disabled";
    const scroll = (box.querySelector("#lp-cp-strip") || {}).scrollLeft || 0;
    box.innerHTML = `<div class="lp-cp-top"><span class="h">SKLADBA Z KOUSKŮ</span>
        <button type="button" class="lp-b ${CP.src ? "on" : ""}" id="lp-cp-play" ${dis} title="Přehrát celou skladbu (s běžícím looperem od dalšího taktu)">${CP.busy ? "Skládám…" : CP.src ? "■ Zastavit" : "▶ Přehrát"}</button>
        <select id="lp-cp-to" ${dis} title="Celou skladbu jako jednu dlouhou smyčku do stopy"><option value="">Do stopy…</option>${tracks.map(t => `<option value="${t.i}">Stopa ${t.i + 1}</option>`).join("")}</select>
        <button type="button" class="lp-b" id="lp-cp-wav" ${dis} title="Stáhnout skladbu jako WAV">Uložit WAV</button>
        <button type="button" class="lp-b" id="lp-cp-whole" ${SL.song ? "" : "disabled"} title="Do skladby všechny kousky písně, která je vidět ve vlně, v jejím pořadí — pak v ní můžeš kousky vyměnit za své dílky">＋ Celá píseň</button>
        <button type="button" class="lp-b" id="lp-cp-clear" ${dis}>Vyprázdnit</button>
        <span class="lp-pb-hint" id="lp-cp-note">${comp.length ? `${comp.length} ${comp.length === 1 ? "kousek" : comp.length < 5 ? "kousky" : "kousků"} · ${bars} taktů · ${mmss(bars * barLen())} · vše v ${cfg.bpm} BPM${mk ? " a " + esc(mk) : ""}` : "kousky z různých písní navážou na taktu a srovnají se do tempa a tóniny MASTERu"}</span></div>
      <div class="lp-cp-strip" id="lp-cp-strip">${comp.length ? comp.map((b, k) => { const ft = fitText(b);
          return `<div class="lp-cp-b ${CP.pvAt === k ? "pv" : ""} ${b.parts ? "mine" : ""}" draggable="true" data-b="${k}" style="--h:${songHue(b.song)};width:${Math.max(96, Math.min(360, mBars(b) * 14))}px"
            title="${esc(b.name)} — ${esc(b.title || b.song)}&#10;${b.bars} taktů · ${esc(b.stems.map(stemName).join(", "))}${ft ? "&#10;srovnáno: " + esc(ft) : ""}&#10;klik: poslech · táhni: přesunout">
            <b>${b.parts ? "🎸 " : ""}${esc(b.name)}</b><small>${esc(shortTitle(b.title || b.song))}${b.n ? " · kousek " + b.n : ""}</small><em>${mBars(b)} t.${ft ? " · " + esc(ft) : ""}</em>
            <button type="button" data-rm="${k}" title="Odebrat ze skladby">✕</button></div>`; }).join("")
        : `<div class="lp-cp-none">Přetáhni sem kousky z vlny nahoře (nebo ＋ Do skladby, ＋ Celá píseň) — klidně z různých písní. Navážou na sebe na taktu.</div>`}<i class="lp-cp-head" id="lp-cp-head"></i></div>
      ${mine.length ? `<div class="lp-cp-mine"><span class="h2">MOJE DÍLKY</span>${mine.map((d, k) => `<span class="lp-cp-chip ${DK.d === d ? "pv" : ""}" draggable="true" data-mine="${k}" style="--h:${songHue(d.song)}"
          title="${esc(d.name)} — ${esc(d.title || d.song)} · ${d.bars} t. · ${(d.parts || []).length} part(y)&#10;klik: otevřít mix (hlasitost, pan, ladění, zvuk) · táhni do skladby (na kousek = nahradí ho) nebo na pad"><b>🎸 ${esc(d.name)}</b><small>${esc(shortTitle(d.title || d.song))} · ${mBars(d)} t.</small><button type="button" data-unmine="${k}" title="Odebrat dílek">✕</button></span>`).join("")}</div>` : ""}${dkHtml()}`;
    const q = (id) => box.querySelector("#" + id), strip = q("lp-cp-strip");
    strip.scrollLeft = scroll;
    q("lp-cp-play").onclick = compPlay;
    q("lp-cp-to").onchange = (e) => { const v = e.target.value; e.target.value = ""; if(v !== "") compToTrack(+v); };
    q("lp-cp-wav").onclick = compWav;
    q("lp-cp-whole").onclick = async () => {
      if(!sliceSong()) return;
      if(comp.length && !(await Dialog.confirm(`Nahradit skladbu všemi kousky písně „${shortTitle(SL.title)}“?`, {ok: "Nahradit"}))) return;
      comp = SL.cuts.map((_, i) => pieceSnap(i)); compChanged();
      showToast(`Skladba: ${comp.length} kousků písně — dílek s tvým partem na kousek přetáhni a nahradí ho`);
    };
    box.querySelectorAll("[data-mine]").forEach(el => { const k = +el.dataset.mine;
      el.onclick = (e) => { if(e.target.closest("[data-unmine]")) return; if(DK.d === mine[k]){ dkStop(); DK.d = null; paintComp(); return; } dkStop(); DK.d = mine[k]; paintComp(); dkPlay(); };
      el.ondragstart = (e) => { e.dataTransfer.setData("text/pick-mine", String(k)); e.dataTransfer.effectAllowed = "copy"; }; });
    box.querySelectorAll("[data-unmine]").forEach(b => b.onclick = async () => { if(await Dialog.confirm(`Odebrat dílek „${mine[+b.dataset.unmine].name}“?`, {ok: "Odebrat"})){ if(DK.d === mine[+b.dataset.unmine]){ dkStop(); DK.d = null; } mine.splice(+b.dataset.unmine, 1); mineKeep(); paintComp(); paintSlice(); } });
    wireDk(box);
    q("lp-cp-clear").onclick = async () => { if(await Dialog.confirm("Vyprázdnit skladbu z kousků?", {ok: "Vyprázdnit"})){ comp = []; compChanged(); } };
    strip.querySelectorAll("[data-b]").forEach(el => {
      const k = +el.dataset.b;
      el.onclick = (e) => { if(e.target.closest("[data-rm]")) return;
        const m = comp[k].mine && mine.find(x => x.mine === comp[k].mine);
        if(m){ dkStop(); DK.d = m; paintComp(); dkPlay(); return; }            // a piece of mine: its mix opens
        compPreview(k); };
      el.ondragstart = (e) => { e.dataTransfer.setData("text/pick-block", String(k)); e.dataTransfer.effectAllowed = "move"; };
    });
    strip.querySelectorAll("[data-rm]").forEach(b => b.onclick = () => { comp.splice(+b.dataset.rm, 1); compChanged(); });
    strip.addEventListener("dragover", (e) => { const ty = [...e.dataTransfer.types]; if(ty.includes("text/pick-piece") || ty.includes("text/pick-block") || ty.includes("text/pick-mine")){ e.preventDefault(); strip.classList.add("drop");
      strip.querySelectorAll(".lp-cp-b").forEach(el => el.classList.toggle("swap", ty.includes("text/pick-mine") && el.contains(e.target))); } });
    strip.addEventListener("dragleave", (e) => { if(!strip.contains(e.relatedTarget)){ strip.classList.remove("drop"); strip.querySelectorAll(".swap").forEach(el => el.classList.remove("swap")); } });
    strip.addEventListener("drop", (e) => {
      strip.classList.remove("drop");
      const v = e.dataTransfer.getData("text/pick-piece"), from = e.dataTransfer.getData("text/pick-block"), mi = e.dataTransfer.getData("text/pick-mine");
      let at = compIdxAt(e.clientX);
      const onB = e.target.closest && e.target.closest(".lp-cp-b");
      if(mi !== "" && mine[+mi]){ e.preventDefault(); const d = clone(mine[+mi]);
        if(onB){ const k = +onB.dataset.b; comp[k] = d; compChanged(); showToast(`Kousek ${k + 1} ve skladbě nahrazen: ${d.name}`); } else compAdd(d, at); return; }
      if(v !== ""){ e.preventDefault(); compAdd(pieceSnap(+v), at); }
      else if(from !== ""){ e.preventDefault(); const f = +from, [b] = comp.splice(f, 1); if(at > f) at--; comp.splice(at, 0, b); compChanged(); }
    });
    if(CP.src) compHead();
  }
  // ---- my parts in a piece (Moje dílky). A piece of mine is the song's piece with its own mix: the backing (the
  // tracks left on), the original part that was taken out (the guitar — off, or on beside mine), and my parts,
  // each with its level, pan and pitch. A part recorded through a print or a NAM profile is kept clean (DI) and
  // played through it offline, so the sound can be changed any time after. 🎸 Nahrát svůj part: the piece's tempo,
  // metre and length to the looper, its mix as the backing, a bar counted in, the take cut on the bars.
  const RF = {on: false, track: null, d: null, src: null, piece: -1, song: null};
  const GTR_STEMS = ["guitar", "guitar_rhythm", "guitar_solo"];
  const takt = (n) => `${n} ${n === 1 ? "takt" : n < 5 ? "takty" : "taktů"}`;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  function upgradeDilek(d){
    if(!d || d.parts) return d;
    const {over, ...rest} = d;
    return {...rest, base: {vol: 1, pan: 0}, orig: {stems: [], on: false, vol: 0.8, pan: -0.6, semis: 0},
      parts: over ? [{sound: over.sound, bpm: over.bpm, key: "", di: false, tone: null, vol: 1, pan: 0, semis: 0, on: true, name: "Part 1"}] : []};
  }
  let mine = (Array.isArray(cfg.mine) ? cfg.mine : []).filter(x => x && x.song && (x.parts || x.over)).map(upgradeDilek);
  comp = comp.map(b => (b.over || b.parts ? upgradeDilek(b) : b));
  const mineKeep = () => { cfg.mine = mine; keep(); };
  const DK = {d: null, src: null, at: 0, dur: 0, t: 0, busy: false};
  // a piece of mine changed: the copies in the song from pieces and on the pads follow
  function dkSync(d){
    mineKeep();
    let ch = false;
    comp.forEach((b, k) => { if(b.mine && b.mine === d.mine){ comp[k] = clone(d); ch = true; } });
    if(ch){ cfg.comp = comp; keep(); CP.buf = null; }
    pads.forEach(p => { if(p.piece && p.piece.mine === d.mine){ p.piece = clone(d); stopPad(p); } }); padsKeep();
  }
  // ---- the layers' sound, kept: a song's tracks fitted to the master (and moved), a part fitted (and through its tone)
  const LA = new Map();
  function kept(k, fn){ if(!LA.has(k)){ const p = fn(); LA.set(k, p); p.catch(() => LA.delete(k)); while(LA.size > 150) LA.delete(LA.keys().next().value); } return LA.get(k); }
  function stemsAudio(e, sn, stems, more){
    const semis = keyShift(e.key) + (more || 0);
    return kept(["st", sn.song, sn.a.toFixed(3), sn.z.toFixed(3), sn.bars, stems.join("+"), cfg.bpm, cfg.perBar, semis].join("|"), async () => {
      const raw = await rawPiece(e, {...sn, stems}); if(!raw) return null;
      const want = mBars(sn) * barLen(), rate = raw.duration / want;
      return fadeEdges(Math.abs(rate - 1) < 0.02 && !semis ? stretchLinear(raw, want) : await fitAudio(raw, want, semis));
    });
  }
  function partAudio(sn, pt){
    const want = mBars(sn) * barLen(), semis = (pt.key ? keyShift(pt.key) : 0) + (pt.semis || 0), tk = pt.di && pt.tone ? JSON.stringify(pt.tone) : "";
    return kept(["pt", pt.sound, tk, want.toFixed(4), semis].join("|"), async () => {
      let b = await loadSample({sound: pt.sound, name: pt.name || ""});
      if(tk) b = await toned(b, pt);
      if(Math.abs(b.duration / want - 1) > 0.002 || semis) b = await fitAudio(b, want, semis);
      return b;
    });
  }
  // a clean take through a print or a NAM profile (and the cabinet it was played with), offline; what rings on
  // past the end comes round to the start, as it would in the loop
  const namTexts = new Map();
  async function toned(buf, pt){
    const sr = buf.sampleRate, n = buf.length, tail = Math.round(sr * 1.5), oc = new OfflineAudioContext(2, n + tail, sr);
    const src = oc.createBufferSource(); src.buffer = buf;
    const T = pt.tone;
    if(T.kind === "print"){
      const c = await Tone.buildChain(T.song, T.pid, oc), p = c.print, g = oc.createGain();
      g.gain.value = Math.pow(10, ((p.drive_db || 0) + (p.gain_db || 0) - (pt.inDb || 0) + (Tone.trim ? Tone.trim() : 0)) / 20);
      src.connect(g); g.connect(c.input); c.out.connect(oc.destination);
    } else {
      let txt = namTexts.get(T.file);
      if(!txt){ const r = await fetch(`/api/amps/${encodeURIComponent(T.file)}`); if(!r.ok) throw new Error("NAM profil se nepodařilo načíst"); txt = await r.text(); namTexts.clear(); namTexts.set(T.file, txt); }
      const eng = await (await import("/static/nam/index.js")).NamEngine.attach(oc, {assetBaseUrl: "/static/nam/"}), node = await eng.createNode();
      await node.loadModel(txt);
      src.connect(node); let last = node;
      if(T.cab){ try{ const r = await fetch(`/api/amps/${encodeURIComponent(T.cab)}`); if(r.ok){ const cv = oc.createConvolver(); cv.normalize = true; cv.buffer = await oc.decodeAudioData(await r.arrayBuffer()); node.connect(cv); last = cv; } }catch(e){} }
      last.connect(oc.destination);
    }
    src.start(0);
    const r = await oc.startRendering(), out2 = ac.createBuffer(2, n, sr);
    for(let c = 0; c < 2; c++){ const a = r.getChannelData(c), b = out2.getChannelData(c); b.set(a.subarray(0, n)); for(let k = n; k < a.length; k++) b[(k - n) % n] += a[k]; }
    return out2;
  }
  async function dilekAudio(d, skip){
    const e = await songFor(d.song); if(!e) return null;
    const want = mBars(d) * barLen(), sr = ac.sampleRate, b = d.base || {}, o = d.orig || {};
    const jobs = [];
    if(d.stems.length) jobs.push(stemsAudio(e, d, d.stems, 0).then(buf => ({buf, vol: b.vol ?? 1, pan: b.pan || 0})));
    if(o.on && o.stems && o.stems.length) jobs.push(stemsAudio(e, d, o.stems, o.semis || 0).then(buf => ({buf, vol: o.vol ?? 1, pan: o.pan || 0})));
    (d.parts || []).forEach(pt => { if(pt.on !== false && pt !== skip) jobs.push(partAudio(d, pt).then(buf => ({buf, vol: pt.vol ?? 1, pan: pt.pan || 0}))); });
    const layers = await Promise.all(jobs);
    const oc = new OfflineAudioContext(2, Math.max(1, Math.round(want * sr)), sr);
    layers.forEach(l => { if(!l.buf) return; const s = oc.createBufferSource(), g = oc.createGain(), p = oc.createStereoPanner();
      s.buffer = l.buf; g.gain.value = l.vol; p.pan.value = Math.max(-1, Math.min(1, l.pan)); s.connect(g); g.connect(p); p.connect(oc.destination); s.start(0); });
    return oc.startRendering();
  }
  // ---- the sound to record with: as Pick plays now, or a print / a NAM profile (heard live, the take kept clean)
  let ampList = null;
  async function toneChoices(){
    const all = await prints();
    if(!ampList){ try{ ampList = (await (await fetch("/api/amps?category=all")).json()).filter(a => a.kind === "model"); }catch(e){ ampList = []; } }
    return [...all.map(p => ({v: "p|" + p.song + "|" + p.id, label: p.name || "otisk", sub: ["otisk", p.artist || p.title, p.category].filter(Boolean).join(" · "), tone: {kind: "print", song: p.song, pid: p.id, name: p.name || "otisk"}})),
      ...ampList.map(a => ({v: "n|" + a.file, label: a.name, sub: "NAM · " + a.file.replace(/^lib:|^ext:/, "").split("/").slice(-3, -1).join(" / "), tone: {kind: "nam", file: a.file, name: a.name}}))];
  }
  const toneName = (t) => !t ? "zvuk z Picku" : (t.kind === "print" ? "otisk " : "NAM ") + t.name;
  async function pickTone(cur){
    const all = await toneChoices();
    const v = await Dialog.pick({title: "Zvuk kytary", placeholder: "Hledat otisk nebo NAM profil…", empty: "Nic takového.",
      search: async (q) => all.filter(x => !q || (x.label + " " + x.sub).toLowerCase().includes(q.toLowerCase())).slice(0, 120).map(x => ({label: x.label + (cur && x.v === cur ? " ✓" : ""), sub: x.sub, value: x.v}))});
    return v ? all.find(x => x.v === v) : null;
  }
  const G2 = () => (Mixer.guitar && Mixer.guitar.state) || {};
  async function liveTone(t){            // the live guitar plays through the chosen sound while recording
    if(!t) return;
    try{
      if(t.kind === "print") await Tone.apply(Mixer.currentSong() || t.song, t.pid, true, t.song, true);
      else { try{ Tone.remove(true); }catch(e){} await Mixer.ampLoad(t.file, t.name); t.cab = G2().cabName ? G2().cabName + ".wav" : ""; }
    }catch(e){ showToast("Zvuk se nepodařilo zapnout: " + e.message); }
  }
  // before a take: the sound, the original beside it, where my part sits
  async function riffDialog(d){
    const o = d.orig || {}, names = (o.stems || []).map(stemName).join(" + ");
    let tone = cfg.rfTone || null, toneLbl = tone ? toneName(tone) : "";
    return Dialog.open((card) => {
      card.classList.add("wide");
      card.innerHTML = `<div class="pk-dlg-title">${d.parts && d.parts.length ? "Nahrát další part" : "Nahrát svůj part"}</div>
        <div class="pk-dlg-text"><b>${esc(d.name)}</b> · ${esc(shortTitle(d.title || d.song))} · ${takt(mBars(d))} · ${cfg.bpm} BPM, ${cfg.perBar}/4 — nahrávka začne po odpočtu jednoho taktu</div>
        <div class="lp-rd">
          <div class="lp-rd-h">Zvuk kytary</div>
          <label><input type="radio" name="lp-snd" value="live" ${tone ? "" : "checked"}> jak hraje teď v Picku <small>— nahraje se i se zvukem</small></label>
          <label><input type="radio" name="lp-snd" value="tone" ${tone ? "checked" : ""}> přes otisk nebo NAM profil <small>— uslyšíš ho, nahraje se čisté DI a zvuk můžeš pak kdykoli změnit</small></label>
          <div class="lp-rd-row"><button type="button" class="btn secondary" id="lp-rd-tone">${tone ? esc(toneLbl) : "Vybrat otisk nebo NAM profil…"}</button></div>
          ${names ? `<div class="lp-rd-h">Originál kousku (${esc(names)})</div>
          <label><input type="checkbox" id="lp-rd-orig" ${o.on ? "checked" : ""}> hrát i originál</label>
          <div class="lp-rd-row">originál <select id="lp-rd-opan"><option value="-0.7">vlevo</option><option value="0">uprostřed</option><option value="0.7">vpravo</option></select>
            hlasitost <input type="range" id="lp-rd-ovol" min="0" max="1.5" step="0.01" value="${o.vol ?? 0.8}"></div>` : ""}
          <div class="lp-rd-h">Můj part</div>
          <div class="lp-rd-row">bude <select id="lp-rd-mpan"><option value="0">uprostřed</option><option value="0.7">vpravo</option><option value="-0.7">vlevo</option></select></div>
        </div>
        <div class="pk-dlg-btns"><button type="button" class="btn secondary pk-dlg-no">Zrušit</button><button type="button" class="btn pk-dlg-ok">● Nahrát</button></div>`;
      const q = (s) => card.querySelector(s);
      if(q("#lp-rd-opan")){ q("#lp-rd-opan").value = String(o.pan != null ? (o.pan < -0.2 ? -0.7 : o.pan > 0.2 ? 0.7 : 0) : -0.7); }
      if(o.on) q("#lp-rd-mpan").value = "0.7";
      if(q("#lp-rd-orig")) q("#lp-rd-orig").onchange = (e) => { if(e.target.checked && q("#lp-rd-mpan").value === "0") q("#lp-rd-mpan").value = String(-+q("#lp-rd-opan").value || 0.7); };
      q("#lp-rd-tone").onclick = async () => {
        // the picker is a dialog of its own: this one is built again with what was chosen
        const prev = {orig: q("#lp-rd-orig") ? q("#lp-rd-orig").checked : false, opan: q("#lp-rd-opan") ? +q("#lp-rd-opan").value : -0.7, ovol: q("#lp-rd-ovol") ? +q("#lp-rd-ovol").value : 0.8, mpan: +q("#lp-rd-mpan").value};
        Dialog.close({again: true, prev});
      };
      q(".pk-dlg-no").onclick = () => Dialog.close(null);
      q(".pk-dlg-ok").onclick = () => {
        const useTone = q('input[name="lp-snd"]:checked').value === "tone";
        if(useTone && !tone){ showToast("Vyber otisk nebo NAM profil"); return; }
        Dialog.close({tone: useTone ? tone : null, orig: q("#lp-rd-orig") ? q("#lp-rd-orig").checked : false, opan: q("#lp-rd-opan") ? +q("#lp-rd-opan").value : -0.7,
          ovol: q("#lp-rd-ovol") ? +q("#lp-rd-ovol").value : 0.8, mpan: +q("#lp-rd-mpan").value});
      };
    }, null);
  }
  async function riffAsk(d){
    for(;;){
      const r = await riffDialog(d);
      if(!r || !r.again) return r;
      d.orig = {...(d.orig || {}), on: r.prev.orig, pan: r.prev.opan, vol: r.prev.ovol};
      const x = await pickTone(cfg.rfTone ? (cfg.rfTone.kind === "print" ? "p|" + cfg.rfTone.song + "|" + cfg.rfTone.pid : "n|" + cfg.rfTone.file) : "");
      if(x){ cfg.rfTone = x.tone; keep(); }
    }
  }
  async function riffStart(target){
    audio();
    if(RF.on){ riffStop(); return; }
    let d;
    if(target.d) d = target.d;
    else {
      const i = target.i, st = stemsIn(i);
      if(Object.values(st).every(v => v === "on")) SL.stems.forEach(x => { if(GTR_STEMS.includes(x.stem)) st[x.stem] = "mute"; });
      const sn = pieceSnap(i);
      d = {...sn, base: {vol: 1, pan: 0}, orig: {stems: SL.stems.map(x => x.stem).filter(s => !sn.stems.includes(s)), on: false, vol: 0.8, pan: -0.7, semis: 0}, parts: [], fresh: true};
    }
    if(!d.stems.length && !(d.orig && d.orig.on)){ showToast("V kousku teď nic nehraje — zapni aspoň jednu stopu jako podklad"); paintSlice(); return; }
    const t = tracks.find(x => !x.layers.length && !x.job && (x.src === "gtr" || x.src === "gtrdi")) || tracks.find(x => !x.layers.length && !x.job);
    if(!t){ showToast("Všechny stopy looperu jsou plné — smaž jednu, do ní se nahraje tvůj part"); paintSlice(); return; }
    const opt = await riffAsk(d); if(!opt) { paintSlice(); return; }
    d.orig = {...d.orig, on: opt.orig, pan: opt.opan, vol: opt.ovol};
    if(running) stopAll();
    dkStop();
    // the piece's own tempo and metre (while the tracks are empty): the song is then heard as it is, not stretched
    const e = SLS.find(x => x.song === d.song), per = (e && e.perBar) || cfg.perBar, bpm = Math.round(60 * per * d.bars / (d.z - d.a) * 10) / 10;
    if(!hasAudio() && bpm >= 40 && bpm <= 240){ cfg.perBar = per; cfg.bpm = bpm; keep(); }
    RF.prevSrc = t.src; t.src = opt.tone ? "gtrdi" : "gtr";
    RF.on = true; RF.track = t; RF.d = d; RF.opt = opt; RF.piece = target.d ? -1 : target.i; RF.song = d.song;
    paint(); paintMaster(); paintSlice(); paintComp();
    showToast("Připravuji podklad…");
    if(opt.tone) await liveTone(opt.tone);
    let buf; try{ buf = await dilekAudio(d); }catch(err){ buf = null; showToast("Podklad se nepodařilo připravit: " + err.message); }
    if(!buf || !RF.on){ riffStop(); return; }
    const bars = mBars(d);
    t.bars = bars;
    if(!(await srcs[t.src].start())){ riffStop(); return; }
    if(!RF.on) return;
    start(true);                                           // a bar counted in, always
    await arm(t, "first");
    if(!t.job){ riffStop(); return; }
    const s = ac.createBufferSource(); s.buffer = buf; s.loop = true; s.connect(bus); s.start(t.job.from); RF.src = s;
    showToast(`Hraj: ${takt(bars)} v ${cfg.bpm} BPM, ${cfg.perBar}/4 — po odpočtu${opt.tone ? " · " + toneName(opt.tone) + " (nahrává se DI)" : ""}`);
    paintSlice(); paintComp();
  }
  function riffStop(){
    const t = RF.track;
    if(RF.src){ try{ RF.src.stop(); }catch(e){} RF.src = null; }
    RF.on = false; RF.track = null;
    if(t && t.job){ cancelJob(t); maybeStopSrc(t.src); }
    if(t && RF.prevSrc){ t.src = RF.prevSrc; RF.prevSrc = null; }
    paint(); paintSlice(); paintComp();
  }
  async function riffDone(t, buf){
    const d = RF.d, opt = RF.opt || {}; RF.on = false; RF.track = null;
    let id = null;
    try{ id = await uploadSound(new File([wav(buf)], `Můj part – ${d.name}.wav`, {type: "audio/wav"})); }catch(e){ showToast("Part se nepodařilo uložit: " + e.message); }
    // the take lives in the piece of mine now: the looper's track is free again, the result plays
    const was = RF.prevSrc; RF.prevSrc = null;
    setTimeout(() => { if(RF.src){ try{ RF.src.stop(); }catch(e){} RF.src = null; } stopAll(); clear(t.i); if(was) t.src = was; paint(); }, 0);
    if(!id){ paintSlice(); return; }
    SM.cache.set("s|" + id.sound, buf);
    const pt = {sound: id.sound, bpm: cfg.bpm, key: masterKey(), di: !!opt.tone, tone: opt.tone ? {...opt.tone} : null, inDb: G2().inputGainDb || 0,
      vol: 1, pan: opt.mpan || 0, semis: 0, on: true, name: "Part " + ((d.parts || []).length + 1)};
    d.parts = [...(d.parts || []), pt];
    let msg;
    if(d.fresh){
      delete d.fresh;
      const n = mine.filter(x => x.song === d.song && Math.abs(x.a - d.a) < 0.01).length + 1;
      d.name = `${d.name} · můj part${n > 1 ? " " + n : ""}`; d.mine = Date.now();
      mine.unshift(d); mineKeep();
      const k = comp.findIndex(b => !b.parts && b.song === d.song && Math.abs(b.a - d.a) < 0.01 && Math.abs(b.z - d.z) < 0.01);
      if(k >= 0){ comp[k] = clone(d); compChanged(); }
      msg = k >= 0 ? `Hotovo — ve skladbě je kousek ${k + 1} teď s tvým partem` : "Hotovo — nový dílek je v Mých dílcích, přetáhni ho do skladby";
    } else { dkSync(d); msg = `Hotovo — ${pt.name} přidán do dílku`; }
    DK.d = d; showToast(msg + " · posloucháš výsledek");
    paintSlice(); paintComp();
    setTimeout(() => dkPlay(), 50);
  }
  // ---- the piece of mine open (its mix under Moje dílky): plays round while it is changed
  async function dkPlay(keepPos){
    audio();
    const d = DK.d; if(!d) return;
    const pos = keepPos && DK.src ? ((now() - DK.at) % DK.dur + DK.dur) % DK.dur : 0;
    const my = ++DK.t; DK.busy = true; paintDkHead();
    let buf; try{ buf = await dilekAudio(d); }catch(e){ DK.busy = false; paintDkHead(); showToast("Dílek se nepodařilo složit: " + e.message); return; }
    DK.busy = false;
    if(my !== DK.t || !buf || DK.d !== d){ paintDkHead(); return; }
    if(DK.src){ try{ DK.src.stop(); }catch(e){} }
    const s = ac.createBufferSource(); s.buffer = buf; s.loop = true; s.connect(bus);
    const p0 = Math.min(pos, buf.duration - 0.01); s.start(now() + 0.02, p0);
    DK.src = s; DK.dur = buf.duration; DK.at = now() + 0.02 - p0;
    paintDkHead();
  }
  function dkStop(){ DK.t++; if(DK.src){ try{ DK.src.stop(); }catch(e){} DK.src = null; } paintDkHead(); }
  let dkT = 0;
  function dkChanged(){ dkSync(DK.d); clearTimeout(dkT); dkT = setTimeout(() => { if(DK.src) dkPlay(true); }, 220); }
  function paintDkHead(){ const b = root && root.querySelector("#lp-dk-play"); if(b){ b.textContent = DK.busy ? "Skládám…" : DK.src ? "■ Zastavit" : "▶ Poslech dokola"; b.classList.toggle("on", !!DK.src); } }
  function dkHtml(){
    const d = DK.d; if(!d) return "";
    const panLbl = (p) => Math.abs(p) < 0.05 ? "střed" : (p < 0 ? "L " : "P ") + Math.round(Math.abs(p) * 100);
    const row = (key, title, sub, x, o) => `<div class="lp-dk-ch ${x.on === false ? "off" : ""}" data-ch="${key}">
        ${o.toggle ? `<button type="button" class="lp-dk-on ${x.on === false ? "" : "on"}" data-act="on" title="Zapnout / vypnout">${x.on === false ? "○" : "●"}</button>` : `<span class="lp-dk-on fixed">●</span>`}
        <span class="lp-dk-nm"><b>${esc(title)}</b><small>${esc(sub)}</small></span>
        <label title="Hlasitost">hlas. <input type="range" data-k="vol" min="0" max="1.5" step="0.01" value="${x.vol ?? 1}"><em>${Math.round((x.vol ?? 1) * 100)} %</em></label>
        <label title="Panorama — vlevo / vpravo (dvojklik: střed)">pan <input type="range" data-k="pan" min="-1" max="1" step="0.05" value="${x.pan || 0}"><em>${panLbl(x.pan || 0)}</em></label>
        ${o.pitch ? `<label title="Ladění v půltónech (tempo zůstane)">ladění <select data-k="semis">${Array.from({length: 25}, (_, k) => k - 12).map(v => `<option value="${v}" ${(x.semis || 0) === v ? "selected" : ""}>${v > 0 ? "+" : ""}${v}</option>`).join("")}</select></label>` : `<label class="dim" title="Podklad ladí s tóninou MASTERu">ladění: tónina</label>`}
        ${o.tone ? `<button type="button" class="lp-b lp-dk-tone" data-act="tone" title="Jiný zvuk pro tenhle part (nahraný čistě) — otisk nebo NAM profil">${esc(toneName(x.tone))} ▾</button>` : "<span></span>"}
        ${o.del ? `<button type="button" class="lp-dk-x" data-act="del" title="Smazat part">✕</button>` : "<span></span>"}</div>`;
    const o = d.orig || {};
    return `<div class="lp-dk" id="lp-dk"><div class="lp-dk-top"><b class="lp-dk-title" title="Dvojklik: přejmenovat">🎸 ${esc(d.name)}</b><small>${esc(shortTitle(d.title || d.song))}${d.n ? " · kousek " + d.n : ""} · ${takt(mBars(d))}</small>
        <button type="button" class="lp-b" id="lp-dk-play">▶ Poslech dokola</button>
        <button type="button" class="lp-b lp-riff ${RF.on && RF.d === d ? "on" : ""}" id="lp-dk-rec" title="Nahrát další part do tohohle dílku — podklad je jeho mix, jak je teď nastavený">${RF.on && RF.d === d ? "■ Zrušit nahrávání" : "🎙 Nahrát další part"}</button>
        <button type="button" class="lp-b" id="lp-dk-dup" title="Kopie dílku — na zkoušení jiné verze">Kopie</button>
        <span class="lp-sp"></span><button type="button" class="lp-x" id="lp-dk-x" title="Zavřít">${icon("close", 12)}</button></div>
      ${row("base", "Podklad", d.stems.map(stemName).join(" + ") || "nic", d.base || {}, {})}
      ${o.stems && o.stems.length ? row("orig", "Originál", o.stems.map(stemName).join(" + "), o, {toggle: true, pitch: true}) : ""}
      ${(d.parts || []).map((pt, k) => row("p" + k, pt.name || "Part " + (k + 1), pt.di ? "DI · " + toneName(pt.tone) : "nahráno se zvukem", pt, {toggle: true, pitch: true, tone: pt.di, del: true})).join("")}</div>`;
  }
  function wireDk(box){
    const d = DK.d, el = box.querySelector("#lp-dk"); if(!d || !el) return;
    const objOf = (k) => k === "base" ? (d.base || (d.base = {vol: 1, pan: 0})) : k === "orig" ? d.orig : d.parts[+k.slice(1)];
    el.querySelector("#lp-dk-play").onclick = () => (DK.src ? dkStop() : dkPlay());
    el.querySelector("#lp-dk-rec").onclick = () => (RF.on ? riffStop() : riffStart({d}));
    el.querySelector("#lp-dk-x").onclick = () => { dkStop(); DK.d = null; paintComp(); };
    el.querySelector("#lp-dk-dup").onclick = () => { const c = clone(d); c.mine = Date.now(); c.name = d.name + " (kopie)"; mine.unshift(c); mineKeep(); DK.d = c; paintComp(); showToast("Kopie dílku — můžeš ji měnit nezávisle"); };
    el.querySelector(".lp-dk-title").ondblclick = async () => { const n = await Dialog.prompt("Název dílku:", d.name); if(n && n.trim()){ d.name = n.trim().slice(0, 60); dkSync(d); paintComp(); } };
    paintDkHead();
    el.querySelectorAll(".lp-dk-ch").forEach(r => {
      const x = objOf(r.dataset.ch); if(!x) return;
      r.querySelectorAll("input[type=range]").forEach(inp => {
        inp.oninput = () => { x[inp.dataset.k] = +inp.value; const em = inp.nextElementSibling;
          if(em) em.textContent = inp.dataset.k === "vol" ? Math.round(x.vol * 100) + " %" : (Math.abs(x.pan) < 0.05 ? "střed" : (x.pan < 0 ? "L " : "P ") + Math.round(Math.abs(x.pan) * 100)); dkChanged(); };
        if(inp.dataset.k === "pan") inp.ondblclick = () => { inp.value = 0; inp.oninput(); };
      });
      const sm = r.querySelector("select[data-k=semis]"); if(sm) sm.onchange = () => { x.semis = +sm.value; dkChanged(); };
      const on = r.querySelector('[data-act="on"]'); if(on) on.onclick = () => { x.on = x.on === false; dkChanged(); paintComp(); };
      const tn = r.querySelector('[data-act="tone"]'); if(tn) tn.onclick = async () => {
        const cur = x.tone ? (x.tone.kind === "print" ? "p|" + x.tone.song + "|" + x.tone.pid : "n|" + x.tone.file) : "";
        const v = await pickTone(cur); if(!v) return;
        const keepCab = x.tone && x.tone.cab; x.tone = {...v.tone}; if(v.tone.kind === "nam") x.tone.cab = keepCab || (G2().cabName ? G2().cabName + ".wav" : "");
        showToast(`${x.name}: ${toneName(x.tone)} — přezvučuji…`); dkChanged(); paintComp(); if(!DK.src) dkPlay(); };
      const del = r.querySelector('[data-act="del"]'); if(del) del.onclick = async () => {
        if(!(await Dialog.confirm(`Smazat ${x.name}?`, {ok: "Smazat"}))) return;
        d.parts.splice(+r.dataset.ch.slice(1), 1); dkChanged(); paintComp(); };
    });
  }

  // The wave's hands: a pinch (ctrl + wheel) zooms round the pointer, two fingers sideways move along it; a cut
  // dragged moves (on the bars — the pieces either side follow), a piece dragged out of the wave goes onto
  // the looper's track it is let go over; a click picks a piece (with the scissors: cuts on the nearest bar).
  let sliceWired = null;
  function wireSliceCanvas(cv){
    if(!cv || cv.dataset.wired) return;
    cv.dataset.wired = "1";
    sliceWired = cv;
    const T = (e) => { const r = cv.getBoundingClientRect(); return SL.vs + (e.clientX - r.left) / r.width * span(); };
    const nearDb = (t) => SL.db.reduce((b, x) => Math.abs(x - t) < Math.abs(b - t) ? x : b, SL.db[0]);
    const cutAt = (e) => { const r = cv.getBoundingClientRect(), px = span() / r.width * 6, t = T(e); return SL.cuts.findIndex((x, k) => k > 0 && Math.abs(x - t) < px); };
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = cv.getBoundingClientRect(), f = (e.clientX - r.left) / r.width;
      if(e.ctrlKey){ const t = SL.vs + f * span(), z = SL.zoom * Math.exp(-e.deltaY * 0.008); setView(z, t - f * (SL.dur / Math.max(1, Math.min(64, z)))); }
      else { const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : (e.shiftKey ? e.deltaY : 0); if(!d) return; setView(SL.zoom, SL.vs + d / r.width * span()); }
      drawSlice();
    }, {passive: false});
    let gz = 1;
    cv.addEventListener("gesturestart", (e) => { e.preventDefault(); gz = SL.zoom; });
    cv.addEventListener("gesturechange", (e) => { e.preventDefault(); const r = cv.getBoundingClientRect(), f = (e.clientX - r.left) / r.width, t = SL.vs + f * span(); setView(gz * e.scale, t - f * (SL.dur / Math.max(1, Math.min(64, gz * e.scale)))); drawSlice(); });
    cv.addEventListener("pointerdown", (e) => {
      if(e.button !== 0) return;
      const k = cutAt(e), t0x = e.clientX, t0y = e.clientY;
      SL.drag = k > 0 ? {cut: k} : {piece: Math.max(0, SL.cuts.filter(x => x <= T(e) + 0.01).length - 1), ghost: null};
      cv.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const d = SL.drag; if(!d) return;
        if(d.cut != null){
          const lo = SL.cuts[d.cut - 1], hi = d.cut + 1 < SL.cuts.length ? SL.cuts[d.cut + 1] : SL.dur;
          const c = nearDb(T(ev)); if(c > lo + 0.01 && c < hi - 0.01){ SL.cuts[d.cut] = c; d.moved = true; drawSlice(); }
          return;
        }
        if(!d.ghost && Math.hypot(ev.clientX - t0x, ev.clientY - t0y) > 8 && Math.abs(ev.clientY - t0y) > 6){
          d.ghost = document.createElement("div"); d.ghost.className = "lp-sl-ghost";
          d.ghost.textContent = `Kousek ${d.piece + 1}${pieceName(d.piece) ? " · " + pieceName(d.piece) : ""} · ${pieceOf(d.piece).bars} t.`;
          document.body.appendChild(d.ghost);
        }
        if(d.ghost){
          d.ghost.style.left = ev.clientX + 12 + "px"; d.ghost.style.top = ev.clientY + 10 + "px";
          root.querySelectorAll(".lp-t, .lp-pad, .lp-cp-strip").forEach(n => { const r = n.getBoundingClientRect(); n.classList.toggle("drop", ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom); });
        }
      };
      const up = (ev) => {
        cv.removeEventListener("pointermove", move); cv.removeEventListener("pointerup", up); cv.removeEventListener("pointercancel", up);
        const d = SL.drag; SL.drag = null;
        if(!d) return;
        if(d.cut != null){ if(d.moved){ SL.st = {}; } paintSlice(); return; }
        if(d.ghost){
          d.ghost.remove();
          const n = [...root.querySelectorAll(".lp-t.drop")][0], pn = [...root.querySelectorAll(".lp-pad.drop")][0], cs = root.querySelector(".lp-cp-strip.drop");
          root.querySelectorAll(".lp-t.drop, .lp-pad.drop, .lp-cp-strip.drop").forEach(x => x.classList.remove("drop"));
          if(pn){ const pi = pn.dataset.pad != null ? +pn.dataset.pad : addPad(); if(pi >= 0) padPiece(pi, pieceSnap(d.piece)); }
          else if(cs) compAdd(pieceSnap(d.piece), compIdxAt(ev.clientX));
          else if(n) pieceToTrack(d.piece, +n.dataset.i);
          return;
        }
        // a click
        const t = T(ev);
        if(SL.scissors){ const c = nearDb(t); if(!SL.cuts.includes(c)){ SL.cuts.push(c); SL.cuts.sort((a, b) => a - b); SL.st = {}; } SL.sel = SL.cuts.filter(x => x <= c).length - 1; paintSlice(); return; }
        SL.sel = Math.max(0, SL.cuts.filter(x => x <= t + 0.01).length - 1); paintSlice();
      };
      cv.addEventListener("pointermove", move); cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    });
    cv.addEventListener("pointermove", (e) => { if(!SL.drag) cv.style.cursor = cutAt(e) > 0 ? "ew-resize" : SL.scissors ? "crosshair" : "grab"; });
    cv.addEventListener("dblclick", (e) => { const k = cutAt(e); if(k > 0){ SL.cuts.splice(k, 1); SL.st = {}; paintSlice(); } });
  }
  // the looper's tracks take a piece dropped on them
  function wirePieceDrop(){
    root.querySelectorAll(".lp-t").forEach(n => {
      n.addEventListener("dragover", (e) => { const ty = [...e.dataTransfer.types]; if(ty.includes("text/pick-piece") || ty.includes("text/pick-sample")){ e.preventDefault(); n.classList.add("drop"); } });
      n.addEventListener("dragleave", () => n.classList.remove("drop"));
      n.addEventListener("drop", (e) => { n.classList.remove("drop");
        const v = e.dataTransfer.getData("text/pick-piece"), sm = e.dataTransfer.getData("text/pick-sample");
        if(v !== ""){ e.preventDefault(); pieceToTrack(+v, +n.dataset.i); }
        else if(sm !== "" && SM.list && SM.list[+sm]){ e.preventDefault(); sampleToTrack(SM.list[+sm], +n.dataset.i); } });
    });
  }

  // ---- the rhythm from the song: each part's own drums, a loop of 1, 2 or 4 bars cut on the downbeats of
  // the song's drum stem (from the part's second bar — its first often carries the fill into it), the
  // edges faded a few ms so it does not click, played under the loops and switched with the part on the
  // bar. A part where the drums are silent plays none. Another tempo in the looper: the loop is sped up or
  // slowed down to fit (and says so when that is far enough to be heard in its pitch).
  let drumBus = null, drumNow = null, drumCache = {};
  function drumsBuf(k){
    const sc = scenes[k];
    if(!sc || !skel || !skel.song || !(Mixer.currentSong && Mixer.currentSong() === skel.song)) return null;
    const key = skel.song + "|" + sc.name + "|" + cfg.drumBars;
    if(key in drumCache) return drumCache[key];
    let src = null, secs = [], g = null;
    try{ src = (Mixer.deck.audio() || []).find(x => x.stem === "drums"); secs = Mixer.sectionsNow() || []; g = Mixer.deck.grid(); }catch(e){}
    const sec = secs.find(x => x.name === sc.name);
    if(!src || !src.buffer || !sec){ drumCache[key] = null; return null; }
    const starts = barsOf(sec.start, sec.end, g), n = cfg.drumBars, B = src.buffer, sr = B.sampleRate;
    // where in the part the drums really play: of its stretches of n bars (from the second bar on — the
    // first often carries the fill into it), the first that is at least 80 % as loud as the loudest —
    // not a quiet start (a clean interlude), not the one fill that is louder than the groove
    const rmsAt = (a, z) => { const d = B.getChannelData(0), f = Math.round(a * sr), t = Math.min(B.length, Math.round(z * sr)); let q = 0, m = 0; for(let i = f; i < t; i += 64){ q += d[i] * d[i]; m++; } return m ? Math.sqrt(q / m) : 0; };
    const ends = (i) => i + n < starts.length ? starts[i + n] : Math.min(sec.end, starts[i] + n * barLen());
    const cands = [];
    for(let i = starts.length > n + 1 ? 1 : 0; i + n <= starts.length || (i === 0 && starts.length); i += n) cands.push({i, r: rmsAt(starts[i], ends(i))});
    const top = Math.max(0, ...cands.map(c => c.r)), pick = cands.find(c => c.r >= top * 0.8) || cands[0];
    const i0 = pick ? pick.i : 0;
    const a = starts[i0], z = ends(i0);
    const from = Math.round(a * sr), len = Math.round((z - a) * sr);
    if(len < sr * 0.2 || from + len > B.length){ drumCache[key] = null; return null; }
    const loop = ac.createBuffer(B.numberOfChannels, len, sr), fade = Math.round(sr * 0.004);
    let sum = 0;
    for(let ch = 0; ch < B.numberOfChannels; ch++){
      const d = loop.getChannelData(ch);
      d.set(B.getChannelData(ch).subarray(from, from + len));
      for(let i = 0; i < fade; i++){ const f = i / fade; d[i] *= f; d[len - 1 - i] *= f; }
      for(let i = 0; i < len; i += 64) sum += d[i] * d[i];
    }
    const rms = Math.sqrt(sum / (len / 64 * B.numberOfChannels));
    drumCache[key] = rms < 0.004 ? null : {buf: loop, sec: z - a, bars: n};
    return drumCache[key];
  }
  function drumsAt(k, at){
    if(!ac || RF.on) return;                    // recording my part: the piece brings its own drums
    if(!drumBus){ drumBus = ac.createGain(); drumBus.connect(out); }
    drumBus.gain.value = cfg.drumVol;
    if(drumNow){ try{ drumNow.stop(at); }catch(e){} drumNow = null; }
    if(!cfg.drums) return;
    const d = drumsBuf(k);
    if(!d) return;
    const src = ac.createBufferSource();
    src.buffer = d.buf; src.loop = true;
    src.playbackRate.value = d.sec / (d.bars * barLen());          // to the looper's tempo
    src.connect(drumBus);
    src.start(Math.max(at, now()));
    drumNow = src;
  }
  function drumsStop(){ if(drumNow){ try{ drumNow.stop(); }catch(e){} drumNow = null; } }
  // how far the parts' drums are from the looper's tempo (1 = the same)
  function drumsRate(){
    const r = scenes.map((sc, k) => drumsBuf(k)).filter(Boolean).map(d => d.sec / (d.bars * barLen()));
    return r.length ? r.reduce((a, b) => a + b, 0) / r.length : 1;
  }

  function prepFromSong(){
    const s = Mixer.currentSong && Mixer.currentSong();
    if(!s){ showToast("Otevři píseň ve Steems — z ní se kostra připraví"); return; }
    if(hasAudio()){ showToast("V looperu už jsou nahrané smyčky — kostru jde připravit jen do prázdného (ulož projekt a vymaž stopy)"); return; }
    stopAll(); arrOn = false;
    song = s; projName = null;
    skeletonFrom(s);
    paint();
  }

  // ---- the original under the loops: the song open in Steems, from where the part playing begins in it
  const guideSong = () => song && Mixer.currentSong && Mixer.currentSong() === song;
  function guidePos(){
    let list = [];
    try{ list = Mixer.sectionsNow() || []; }catch(e){}
    const name = scenes[pending ? pending.k : cur] ? scenes[pending ? pending.k : cur].name : null;
    let nth = 0;
    if(arrOn && arrPos){ const i = pending && pending.step ? pending.step.i : arrPos.i; for(let k = 0; k < i; k++) if(arr[k].k === arr[i].k) nth++; }
    const hits = list.filter(x => x.name === name);
    return hits.length ? hits[nth % hits.length].start : 0;
  }
  let guideT = 0;
  function guideAt(at, pos){
    if(!cfg.guide || !guideSong() || RF.on) return;
    clearTimeout(guideT);
    guideT = setTimeout(() => { try{ Mixer.seek(pos); Mixer.play(); }catch(e){} }, Math.max(0, (at - now()) * 1000 - 5));
  }
  function guideStop(){ clearTimeout(guideT); try{ if(guideSong()) Mixer.pause(); }catch(e){} }

  // ---- a buffer as a 16-bit WAV
  function wav(buf){
    const n = buf.length, ch = Math.min(2, buf.numberOfChannels), sr = buf.sampleRate, out = new DataView(new ArrayBuffer(44 + n * ch * 2));
    const w = (o, s2) => { for(let k = 0; k < s2.length; k++) out.setUint8(o + k, s2.charCodeAt(k)); };
    w(0, "RIFF"); out.setUint32(4, 36 + n * ch * 2, true); w(8, "WAVE"); w(12, "fmt "); out.setUint32(16, 16, true);
    out.setUint16(20, 1, true); out.setUint16(22, ch, true); out.setUint32(24, sr, true); out.setUint32(28, sr * ch * 2, true);
    out.setUint16(32, ch * 2, true); out.setUint16(34, 16, true); w(36, "data"); out.setUint32(40, n * ch * 2, true);
    const data = Array.from({length: ch}, (_, c) => buf.getChannelData(c));
    let o = 44;
    for(let k = 0; k < n; k++) for(let c = 0; c < ch; c++){ const v = Math.max(-1, Math.min(1, data[c][k])); out.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true); o += 2; }
    return new Blob([out.buffer], {type: "audio/wav"});
  }

  // ---- saving and opening: Looper/<name>/project.json and a WAV per recorded layer
  const enc = encodeURIComponent;
  function toJSON(){
    return {version: 1, bpm: cfg.bpm, perBar: cfg.perBar, song, cur,
      scenes: scenes.map(sc => ({name: sc.name, on: [...sc.on], chords: sc.chords || "", full: sc.full || "", bars: sc.bars || 0})),
      arrangement: arr.map(st => ({k: st.k, bars: st.bars || 0, chords: st.chords || "", full: st.full || ""})), skeleton: skel, plan, kit: cfg.kit, pads: padsJSON(), songs: SLS.filter(e => !e.own).map(e => e.song), comp, mine,
      tracks: tracks.map(t => ({name: t.name, src: t.src, instr: t.instr, q: t.q, vol: t.vol, bars: t.bars, len: t.len, fx: t.fx || "none", print: t.print || null,
        layers: t.layers.map(l => l.steps ? {kind: "steps", steps: l.steps} : l.raw ? {kind: "notes", raw: l.raw} : {kind: "audio", file: l.file || null})}))};
  }
  async function save(asName){
    audio();
    let name = asName || projName;
    if(!name){
      name = await Dialog.prompt("Název projektu looperu:", song ? `Looper – ${song}` : "Můj looper");
      if(!name || !name.trim()) return false;
      name = name.trim();
    }
    try{
      for(const t of tracks) for(const l of t.layers){
        if(l.steps || l.raw || (l.file && l.fileProj === name)) continue;
        const f = `t${t.i + 1}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}.wav`;
        const res = await fetch(`/api/looper/${enc(name)}/files/${f}`, {method: "PUT", body: wav(l.buf)});
        if(!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "nahrávka se neuložila");
        l.file = f; l.fileProj = name;
      }
      const res = await fetch(`/api/looper/${enc(name)}`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(toJSON())});
      if(!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "projekt se neuložil");
      projName = (await res.json()).name || name;
      showToast(`Uloženo: ${projName}`);
      paint();
      return true;
    }catch(e){ showToast("Uložení se nepovedlo: " + e.message); return false; }
  }
  async function openProject(){
    let list = [];
    try{ list = (await (await fetch("/api/looper")).json()).projects || []; }catch(e){}
    if(!list.length){ showToast("Zatím žádný uložený projekt looperu"); return; }
    const name = await Dialog.pick({title: "Otevřít projekt looperu", placeholder: "Hledat projekt…", empty: "Nic takového.",
      search: async (q) => list.filter(p => !q || p.name.toLowerCase().includes(q.toLowerCase()))
        .map(p => ({label: p.name, sub: [p.song, p.bpm ? p.bpm + " BPM" : "", p.tracks + " stop", new Date(p.saved * 1000).toLocaleString("cs-CZ")].filter(Boolean).join(" · "), value: p.name}))});
    if(name) await load(name);
  }
  async function load(name){
    audio();
    if(hasAudio() && !(await Dialog.confirm("Otevřít jiný projekt? Co je teď v looperu a není uložené, se ztratí.", {ok: "Otevřít"}))) return false;
    let P;
    try{ const res = await fetch(`/api/looper/${enc(name)}`); if(!res.ok) throw new Error(); P = await res.json(); }
    catch(e){ showToast("Projekt se nepodařilo otevřít"); return false; }
    stopAll(); arrOn = false;
    tracks.forEach(t => clear(t.i));
    history.length = 0; redoStack.length = 0;
    cfg.bpm = P.bpm || cfg.bpm; cfg.perBar = P.perBar || cfg.perBar; song = P.song || null;
    scenes = (P.scenes || []).map(sc => ({name: sc.name, on: new Set(sc.on || []), chords: sc.chords || "", full: sc.full || "", bars: sc.bars || 0}));
    skel = P.skeleton || null;
    plan = Array.isArray(P.plan) ? P.plan : null;
    if(P.kit){ cfg.kit = P.kit; LoopSound.setStyle(P.kit); }
    if(Array.isArray(P.pads)){ padsFrom(P.pads); padsKeep(); }
    if(Array.isArray(P.comp)){ comp = P.comp.filter(b => b && b.song).map(b => (b.over || b.parts ? upgradeDilek(b) : b)); cfg.comp = comp; keep(); }
    if(Array.isArray(P.mine)){ P.mine.filter(d => d && (d.parts || d.over)).map(upgradeDilek).forEach(d => { if(!mine.some(x => x.mine === d.mine)) mine.push(d); }); mineKeep(); }
    (async () => { for(const n of P.songs || []){ try{ await addSong(n); }catch(e){} } paintSlice(); paintComp(); })();
    if(!scenes.length) makeScenes();
    cur = Math.min(P.cur || 0, scenes.length - 1);
    arr = (P.arrangement || []).filter(st => scenes[st.k]).map(st => ({k: st.k, bars: st.bars || 0, chords: st.chords || "", full: st.full || ""}));
    for(const [i, T] of (P.tracks || []).slice(0, N).entries()){
      const t = tracks[i];
      Object.assign(t, {name: T.name || t.name, src: T.src || t.src, instr: T.instr || t.instr, q: T.q ?? t.q, vol: T.vol ?? t.vol, bars: T.bars || 0, len: T.len || 0, anchor: 0});
      if(T.fx || T.print) await setTrackSound(t, {fx: T.fx || "none", print: T.print || null});
      for(const L of T.layers || []){
        try{
          let layer;
          if(L.kind === "audio" && L.file){
            const ab = await (await fetch(`/api/looper/${enc(name)}/files/${L.file}`)).arrayBuffer();
            layer = {buf: await ac.decodeAudioData(ab), file: L.file, fileProj: name};
          } else if(L.kind === "notes"){ layer = {raw: L.raw}; layer.buf = await render(t, quantized(t, L.raw)); }
          else if(L.kind === "steps"){ t.steps = L.steps; layer = {steps: L.steps}; layer.buf = await render(t, stepNotes(t)); }
          if(layer){ t.layers.push(layer); history.push(t.i); }
        }catch(e){ showToast(`${t.name}: vrstvu se nepodařilo načíst`); }
      }
    }
    projName = name;
    root.querySelectorAll(".lp-t").forEach(n => { const t = tracks[+n.dataset.i]; n.querySelector(".nm input").value = t.name; n.querySelector('[data-k="vol"]').value = t.vol; });
    applyMutes(now());
    paint(); paintSteps(); paintPads(); paintComp();
    showToast(`Otevřeno: ${name}`);
    return true;
  }

  // ---- export: the arrangement played through, offline — all of it, or a track at a time
  async function bounce(only){
    const steps = arr.length ? arr : [{k: cur, bars: sceneBars(cur) * 2}];
    const sr = ac.sampleRate, total = steps.reduce((n, st) => n + stepBars(st), 0) * barLen(), oc = new OfflineAudioContext(2, Math.ceil((total + 0.05) * sr), sr);
    let any = false;
    tracks.forEach(t => {
      if(!t.layers.length || (only != null && only !== t.i)) return;
      const g = oc.createGain(); g.connect(oc.destination);
      let at = 0;
      steps.forEach(st => { const on = !scenes[st.k] || scenes[st.k].on.has(t.i); g.gain.setValueAtTime(on ? t.vol : 0, at); at += stepBars(st) * barLen(); });
      t.layers.forEach(l => { const s2 = oc.createBufferSource(); s2.buffer = l.buf; s2.loop = true; s2.connect(g); s2.start(0); any = true; });
    });
    return any ? oc.startRendering() : null;
  }
  async function exportMenu(){
    audio();
    if(!hasAudio()){ showToast("Looper je prázdný — není co exportovat"); return; }
    const how = await Dialog.pick({title: "Export", text: arr.length ? `Aranžmá: ${arr.length} částí, ${arr.reduce((n, st) => n + stepBars(st), 0)} taktů.` : "Aranžmá je prázdné — vyveze se část, která hraje (dvakrát).",
      search: async () => [{label: "WAV — celá píseň", sub: "smíchané stopy, stáhne se", value: "wav"},
        {label: "Projekt ve Steems", sub: "každá stopa looperu jako stopa ve Steems — tam se dá dál mixovat a nahrávat", value: "steems"}]});
    if(how === "wav") return exportWav();
    if(how === "steems") return exportSteems();
  }
  async function exportWav(){
    showToast("Vykresluji…");
    const buf = await bounce(null);
    if(!buf) return;
    const a = document.createElement("a"), url = URL.createObjectURL(wav(buf));
    a.href = url; a.download = `${projName || (song ? "Looper – " + song : "Looper")}.wav`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    lastExport = {kind: "wav", seconds: buf.duration};
    showToast(`WAV hotový (${Math.round(buf.duration)} s)`);
  }
  let lastExport = null;
  async function exportSteems(){
    const name = (projName || (song ? "Looper – " + song : "Looper")).slice(0, 110);
    showToast("Vykresluji stopy pro Steems…");
    let project = null, n = 0;
    try{
      for(const t of tracks){
        if(!t.layers.length) continue;
        const buf = await bounce(t.i), fd = new FormData();
        fd.append("file", wav(buf), `${t.name}.wav`);
        const url = project ? `/api/stems/${enc(project)}/tracks/upload` : `/api/stems/new/upload?name=${enc(name)}`;
        const res = await fetch(url, {method: "POST", body: fd}), j = await res.json().catch(() => ({}));
        if(!res.ok) throw new Error(j.error || "stopa se neuložila");
        if(!project) project = j.name;
        n++;
      }
    }catch(e){ showToast("Export do Steems se nepovedl: " + e.message); return; }
    lastExport = {kind: "steems", project, tracks: n};
    try{ if(typeof loadSteemsList === "function") loadSteemsList(); }catch(e){}
    showToast(`Ve Steems je projekt „${project}“ (${n} stop)`);
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
    if(arrOn && arrPos && arr.length){
      const end = t0 + (arrPos.start + stepBars(arr[arrPos.i])) * barLen();
      if(!pending && tm >= end - 0.15){
        const i = arrPos.i + 1;
        if(i >= arr.length){ if(tm >= end - 0.01){ stopAll(); arrOn = false; showToast("Aranžmá dohrálo"); paint(); } }
        else { pending = {k: arr[i].k, at: end, step: {i, start: arrPos.start + stepBars(arr[arrPos.i])}}; paint(); }
      }
    }
    // the next part's drums laid on the bar line ahead, on the audio clock
    if(pending && !pending.drums && tm >= pending.at - 0.25){ pending.drums = true; drumsAt(pending.k, pending.at); }
    if(pending && tm >= pending.at - 0.02){
      cur = pending.k;
      if(pending.step){ arrPos = pending.step; planStep(arrPos.i); }
      if(cfg.guide) guideAt(pending.at, guidePos());
      pending = null; applyMutes(Math.max(tm, 0)); paint();
    }
    finish();
  }
  function beep(at, first){
    const o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = first ? 1760 : 1175;
    g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(first ? 0.9 : 0.55, at + 0.002); g.gain.exponentialRampToValueAtTime(0.0001, at + 0.06);
    o.connect(g); g.connect(clickBus); o.start(at); o.stop(at + 0.08);
  }
  setInterval(tick, 25);

  // ---- MIDI keys and pads: heard at once on the track they play, and written into its take
  let midiAcc = null, monitor = null;
  const held = new Map();                    // note → its release (live sound)
  async function midiConnect(){
    if(midiAcc) return true;
    if(!navigator.requestMIDIAccess){ showToast("Tenhle prohlížeč neumí MIDI — otevři Pick v Chrome"); return true; }
    try{ midiAcc = await navigator.requestMIDIAccess(); }catch(e){ showToast("Přístup k MIDI zamítnut — hrát jde aspoň na pady na obrazovce"); return true; }
    const hook = () => midiAcc.inputs.forEach(i => { if(!i._pickLooper){ i.addEventListener("midimessage", onMidi); i._pickLooper = true; } });
    midiAcc.addEventListener("statechange", hook); hook();
    return true;
  }
  function onMidi(e){
    if(!open) return;
    const [st, a, b] = e.data, kind = st & 0xf0;
    // a key's time: the browser stamps the message; heard against the music that was playing then
    const at = now() - Math.max(0, (performance.now() - e.timeStamp) / 1000);
    if(padLearn && padLearn.kind === "midi" && kind === 0x90 && b > 0){
      const i = padLearn.i; padLearn = null;
      pads.forEach((p, j) => { if(j !== i && p.note === a && (p.sample || p.piece || !p.drum)) p.note = null; });
      if(pads[i]){ pads[i].note = a; padsKeep(); paintPads(); showToast(`Pad ${i + 1} → MIDI nota ${a}`); }
      return;
    }
    const pi = pads.findIndex(pd => pd.note === a && (pd.piece || pd.sample));
    if(kind === 0x90 && b > 0 && pi >= 0){ padHit(pi); return; }
    if(kind === 0x90 && b > 0) noteOn(a, b / 127, at);
    else if(kind === 0x80 || (kind === 0x90 && b === 0)) noteOff(a, at);
  }
  // which track the keys play: the one recording notes, else the one selected (if it takes notes)
  const playTrack = () => (recTrack && isNotes(recTrack) && recTrack.job ? recTrack : isNotes(tracks[sel]) ? tracks[sel] : tracks.find(isNotes));
  const outLat = () => (ac.outputLatency || 0) + (ac.baseLatency || 0);
  function noteOn(m, v, at, force){
    audio();
    const t = force || playTrack();
    if(!t) return;
    if(ac.state === "suspended") ac.resume();
    const tm = now();
    if(t.src === "drums") LoopSound.drum(ac, monitor, m, v, tm);
    else if(t.instr.startsWith("syn:")){ const prev = held.get(m); if(prev) prev(tm); held.set(m, LoopSound.synth(ac, monitor, t.instr, m, v, tm)); }
    else liveLib(t, m, v);
    const j = t.job;
    if(j && running) j.open.set(m, {mt: (at ?? tm) - outLat(), v});
    flashPad(m);
  }
  function noteOff(m, at){
    const tm = now(), rel = held.get(m);
    if(rel){ rel(tm); held.delete(m); }
    if(libHeld.has(m) && libZv){ libZv.utni(libHeld.get(m)); libHeld.delete(m); }
    tracks.forEach(t => {
      const j = t.job, o = j && j.open.get(m);
      if(!o) return;
      j.open.delete(m);
      const mt = (at ?? tm) - outLat();
      j.notes.push({mt: o.mt, m, v: o.v, d: Math.max(0.03, mt - o.mt)});
    });
  }
  const libHeld = new Map();
  async function liveLib(t, m, v){
    try{
      const L = await Mixer.guitar.instrumentLib();
      if(!libZv) libZv = new L.Zvuky(ac);
      const sf = L.najdiNastroj(t.instr).soundfont;
      if(!libZv.jeNacteny(sf)) await libZv.nacti(sf);
      libHeld.set(m, libZv.hraj(sf, m, v, monitor));
    }catch(e){ const rel = LoopSound.synth(ac, monitor, "syn:saw", m, v, now()); held.set(m, rel); }
  }
  // a pad on the screen: a hit of the kit, short
  function pad(m){ noteOn(m, 0.9); setTimeout(() => noteOff(m), 120); }
  function flashPad(m){ const b = root && root.querySelector(`.lp-pads [data-m="${m}"]`); if(b){ b.classList.add("hit"); setTimeout(() => b.classList.remove("hit"), 110); } }

  // ---- the step grid of a drum track: 16 steps a bar, a row per drum, ready grooves
  let stepT = null;
  function stepNotes(t){
    const S = t.steps, step = beatLen() / 4, out2 = [];
    LoopSound.KIT.forEach((k, r) => S.on[r].forEach((on, i) => {
      if(!on) return;
      const sw = i % 2 ? (S.swing || 0) * step / 2 : 0;
      out2.push({p: i * step + sw, m: k.note, v: i % 4 === 0 ? 0.95 : 0.78, d: 0.1});
    }));
    return out2;
  }
  async function setSteps(t, S){
    audio();
    t.steps = S;
    if(!t.len){ t.len = S.bars * barLen(); t.anchor = running ? t0 : 0; }
    let layer = t.layers.find(l => l.steps);
    if(!layer){ layer = {steps: S}; layer.buf = await render(t, stepNotes(t)); t.layers.push(layer); tracks.forEach(x => { x.undone = []; }); redoStack.length = 0; history.push(t.i); if(running) startLayer(t, layer, now() + 0.005); }
    else { layer.steps = S; await rerender(t, layer, stepNotes(t)); }
    if(scenes[cur]) scenes[cur].on.add(t.i);
    paint();
  }
  function emptySteps(bars){ return {bars, swing: 0, on: LoopSound.KIT.map(() => Array(bars * 16).fill(false))}; }
  function groove(id, bars){
    const g = LoopSound.GROOVES[id], S = emptySteps(bars);
    S.swing = g.swing || 0;
    LoopSound.KIT.forEach((k, r) => (g[k.id] || []).forEach(i => { for(let b = 0; b < bars; b++) S.on[r][b * 16 + i] = true; }));
    return S;
  }
  function openSteps(t){
    stepT = stepT === t ? null : t;
    paintSteps(); paint();
  }
  function paintSteps(){
    const box = root && root.querySelector("#lp-steps");
    if(!box) return;
    const t = stepT;
    box.hidden = !t || t.src !== "drums";
    if(box.hidden) return;
    const bars = t.steps ? t.steps.bars : (t.len ? Math.max(1, Math.round(t.len / barLen())) : 1);
    const S = t.steps || emptySteps(Math.min(4, bars)), cols = S.bars * 16;
    box.innerHTML = `<div class="hd"><b>Bicí — ${esc(t.name)}</b>
        <select data-k="groove" title="Hotový rytmus (přepíše kroky)"><option value="">Hotový rytmus…</option>${Object.entries(LoopSound.GROOVES).map(([k, g]) => `<option value="${k}">${esc(g.name)}</option>`).join("")}</select>
        <select data-k="sbars" title="Délka vzoru" ${t.layers.some(l => !l.steps) ? "disabled" : ""}>${[1, 2, 4].map(b => `<option value="${b}" ${b === S.bars ? "selected" : ""}>${b} ${b === 1 ? "takt" : "takty"}</option>`).join("")}</select>
        <span class="lp-f">swing <input type="range" data-k="swing" min="0" max="0.6" step="0.05" value="${S.swing || 0}"></span>
        <button type="button" class="lp-b" data-k="sclear">Vymazat kroky</button>
        <span class="lp-sp"></span><span class="muted" style="font-size:11px;color:#8a8aa0">Klik na políčko = úder · klik na název = zahrát · pady dole hrají i nahrávají (jako MIDI pady klávesnice)</span></div>
      <div class="lp-grid" style="grid-template-columns:110px repeat(${cols},1fr)">
        ${LoopSound.KIT.map((k, r) => `<span class="rn" data-m="${k.note}">${esc(k.name)}</span>` + S.on[r].map((on, i) =>
          `<button type="button" class="c${on ? " on" : ""}${i % 4 === 0 ? " beat" : ""}${i % 16 === 0 && i ? " bar" : ""}" data-r="${r}" data-i="${i}"></button>`).join("")).join("")}</div>
      <div class="lp-pads">${LoopSound.KIT.map(k => `<button type="button" data-m="${k.note}">${esc(k.name)}</button>`).join("")}</div>`;
    const q = (k) => box.querySelector(`[data-k="${k}"]`);
    box.querySelectorAll(".lp-grid .c").forEach(c => c.onclick = () => {
      const S2 = t.steps ? {...t.steps, on: t.steps.on.map(x => x.slice())} : S;
      S2.on[+c.dataset.r][+c.dataset.i] = !S2.on[+c.dataset.r][+c.dataset.i];
      if(S2.on[+c.dataset.r][+c.dataset.i]) LoopSound.drum(ac, monitor, LoopSound.KIT[+c.dataset.r].note, 0.8, now());
      setSteps(t, S2).then(paintSteps);
    });
    box.querySelectorAll(".rn").forEach(n => n.onclick = () => pad(+n.dataset.m));
    box.querySelectorAll(".lp-pads button").forEach(b => b.onpointerdown = (e) => { e.preventDefault(); pad(+b.dataset.m); });
    q("groove").onchange = (e) => { if(e.target.value) setSteps(t, groove(e.target.value, S.bars)).then(paintSteps); };
    q("sbars").onchange = (e) => { const b = +e.target.value, S2 = emptySteps(b); S.on.forEach((row, r) => row.forEach((on, i) => { for(let k = i; k < b * 16; k += S.bars * 16) S2.on[r][k] = on; })); S2.swing = S.swing;
      t.len = b * barLen(); setSteps(t, S2).then(paintSteps); };
    q("swing").onchange = (e) => { if(!t.steps) return; setSteps(t, {...t.steps, swing: +e.target.value}); };
    q("sclear").onclick = () => { if(t.steps) setSteps(t, emptySteps(t.steps.bars)).then(paintSteps); };
  }

  // ---- the notes of a MIDI track, put right by hand (a piano roll): a click on the empty grid adds a
  // note, a note is dragged in time and pitch, its right edge drawn longer or shorter; a double click
  // (or Delete) takes it away. The layer's sound is made again at once.
  let noteT = null, noteLayer = -1, noteSel = null, noteDrag = null;
  const NN2 = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const noteName = (m) => NN2[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
  function openNotes(t){ noteT = noteT === t ? null : t; noteSel = null; noteLayer = -1; paintNotes(); paint(); }
  const noteLayersOf = (t) => t.layers.filter(l => l.raw);
  const gridStep = (t) => beatLen() * 4 / (t.q || 16);
  function editLayer(t){
    const ls = noteLayersOf(t);
    if(noteLayer >= 0 && ls[noteLayer]) return ls[noteLayer];
    if(ls.length) return ls[ls.length - 1];
    // an empty MIDI track: a loop of the bars set for it (1 when "auto"), its first layer written by hand
    if(!t.len){ t.len = Math.max(1, t.bars || 1) * barLen(); t.anchor = running ? t0 : 0; }
    const l = {raw: []};
    l.buf = ac.createBuffer(2, Math.max(1, Math.round(t.len * ac.sampleRate)), ac.sampleRate);
    t.layers.push(l); history.push(t.i); tracks.forEach(x => { x.undone = []; }); redoStack.length = 0;
    if(running) startLayer(t, l, now() + 0.005);
    if(scenes[cur]) scenes[cur].on.add(t.i);
    paint();
    return l;
  }
  const reTimers = new Map();
  function noteChanged(t, l){
    clearTimeout(reTimers.get(l));
    reTimers.set(l, setTimeout(() => { l.raw.sort((a, b) => a.p - b.p); rerender(t, l, quantized(t, l.raw)); }, 120));
  }
  function preview(t, m){ audio(); const rel = LoopSound.synth(ac, monitor, t.instr.startsWith("syn:") ? t.instr : "syn:saw", m, 0.7, now()); rel(now() + 0.18); }
  // where things are on the roll
  function rollGeo(t, cv){
    const all = noteLayersOf(t).flatMap(l => l.raw.map(x => x.m));
    let lo = all.length ? Math.min(...all) - 4 : 48, hi = all.length ? Math.max(...all) + 4 : 72;
    if(hi - lo < 24){ const c = Math.round((hi + lo) / 2); lo = c - 12; hi = c + 12; }
    lo = Math.max(21, lo); hi = Math.min(108, hi);
    const rows = hi - lo + 1, rowH = Math.max(9, Math.min(16, Math.floor(380 / rows))), left = 44;
    const W = cv.clientWidth || 1000, len = t.len || barLen();
    return {lo, hi, rows, rowH, left, W, H: rows * rowH, len,
      x: (p) => left + p / len * (W - left), y: (m) => (hi - m) * rowH,
      p: (x) => (x - left) / (W - left) * len, m: (y) => hi - Math.floor(y / rowH)};
  }
  function paintNotes(){
    const box = root && root.querySelector("#lp-notes");
    if(!box) return;
    const t = noteT;
    box.hidden = !t || t.src !== "midi";
    if(box.hidden) return;
    const ls = noteLayersOf(t);
    box.innerHTML = `<div class="hd"><b>Noty — ${esc(t.name)}</b>
        <select data-k="layer" title="Do které vrstvy se nové noty přidávají">${ls.length ? ls.map((l, k) => `<option value="${k}" ${k === (noteLayer >= 0 ? noteLayer : ls.length - 1) ? "selected" : ""}>vrstva ${k + 1} (${l.raw.length} not)</option>`).join("") : `<option>nová vrstva</option>`}</select>
        <button type="button" class="lp-b" data-k="nclear" title="Smazat všechny noty vybrané vrstvy">Vymazat noty vrstvy</button>
        <span class="lp-sp"></span><span style="font-size:11px;color:#8a8aa0">Klik = nová nota · tah = posun · tah za pravý okraj = délka · dvojklik nebo Delete = smazat · mřížka podle zarovnání stopy (${t.q ? "1/" + t.q : "1/16"})</span></div>
      <canvas class="lp-roll"></canvas>`;
    const sel = box.querySelector('[data-k="layer"]');
    sel.onchange = () => { noteLayer = +sel.value; noteSel = null; drawRoll(); };
    box.querySelector('[data-k="nclear"]').onclick = () => { const l = noteLayersOf(t)[noteLayer >= 0 ? noteLayer : ls.length - 1]; if(!l) return; l.raw.length = 0; noteSel = null; noteChanged(t, l); paintNotes(); };
    const cv = box.querySelector("canvas");
    cv.onpointerdown = (e) => rollDown(t, cv, e);
    cv.ondblclick = (e) => { const h = rollHit(t, cv, e); if(h){ h.l.raw.splice(h.l.raw.indexOf(h.n), 1); noteSel = null; noteChanged(t, h.l); noteHead(); drawRoll(); } };
    drawRoll();
  }
  function noteHead(){
    const sel = root && root.querySelector('#lp-notes [data-k="layer"]');
    if(!sel || !noteT) return;
    const ls = noteLayersOf(noteT);
    if(sel.options.length !== Math.max(1, ls.length)){ paintNotes(); return; }
    ls.forEach((l, k) => { sel.options[k].textContent = `vrstva ${k + 1} (${l.raw.length} not)`; });
  }
  function rollHit(t, cv, e){
    const g = rollGeo(t, cv), r2 = cv.getBoundingClientRect(), x = e.clientX - r2.left, y = e.clientY - r2.top;
    const ls = noteLayersOf(t);
    for(let k = ls.length - 1; k >= 0; k--) for(const n of ls[k].raw){
      const x0 = g.x(n.p), x1 = Math.max(x0 + 4, g.x(n.p + n.d)), y0 = g.y(n.m);
      if(x >= x0 && x <= x1 && y >= y0 && y < y0 + g.rowH) return {l: ls[k], n, edge: x1 - x < 7, g, x, y};
    }
    return null;
  }
  function rollDown(t, cv, e){
    e.preventDefault();
    audio();
    const r2 = cv.getBoundingClientRect(), g = rollGeo(t, cv), x = e.clientX - r2.left, y = e.clientY - r2.top;
    if(x < g.left) return;
    let h = rollHit(t, cv, e);
    const st = gridStep(t);
    if(!h){
      const l = editLayer(t);
      const n = {p: Math.min(g.len - st, Math.max(0, Math.floor(g.p(x) / st) * st)), m: g.m(y), v: 0.8, d: st * 2};
      l.raw.push(n); preview(t, n.m); noteChanged(t, l);
      h = {l, n, edge: false, g, x, y};
    }
    noteSel = h.n;
    noteDrag = {t, l: h.l, n: h.n, edge: h.edge, x0: x, y0: y, p0: h.n.p, m0: h.n.m, d0: h.n.d, g: h.g};
    try{ cv.setPointerCapture(e.pointerId); }catch(err){}
    cv.onpointermove = (ev) => {
      const D = noteDrag; if(!D) return;
      const dx = ev.clientX - r2.left - D.x0, dy = ev.clientY - r2.top - D.y0, dt = dx / (D.g.W - D.g.left) * D.g.len;
      if(D.edge) D.n.d = Math.max(st, Math.round((D.p0 + D.d0 + dt) / st) * st - D.p0);
      else {
        D.n.p = Math.max(0, Math.min(D.g.len - st, Math.round((D.p0 + dt) / st) * st));
        const m = Math.max(D.g.lo, Math.min(D.g.hi, D.m0 - Math.round(dy / D.g.rowH)));
        if(m !== D.n.m){ D.n.m = m; preview(t, m); }
      }
      drawRoll();
    };
    cv.onpointerup = () => { cv.onpointermove = cv.onpointerup = null; if(noteDrag){ noteChanged(t, noteDrag.l); noteDrag = null; } noteHead(); drawRoll(); };
    drawRoll();
  }
  function drawRoll(){
    const box = root && root.querySelector("#lp-notes"), cv = box && box.querySelector("canvas");
    if(!cv || !noteT) return;
    const t = noteT, g = noteDrag ? noteDrag.g : rollGeo(t, cv), dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth || g.W;
    if(cv.width !== Math.round(W * dpr) || cv.height !== Math.round(g.H * dpr)){ cv.width = Math.round(W * dpr); cv.height = Math.round(g.H * dpr); cv.style.height = g.H + "px"; }
    const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, g.H);
    for(let m = g.lo; m <= g.hi; m++){
      const y = g.y(m), black = [1, 3, 6, 8, 10].includes(((m % 12) + 12) % 12);
      c.fillStyle = black ? "#0a0a10" : "#13131c"; c.fillRect(g.left, y, W - g.left, g.rowH);
      c.fillStyle = black ? "#22222c" : "#d8d8e4"; c.fillRect(0, y, g.left - 2, g.rowH - 1);
      if(((m % 12) + 12) % 12 === 0 || g.rowH >= 13){ c.fillStyle = black ? "#9a9ab0" : "#2a2a36"; c.font = `${Math.min(10, g.rowH - 3)}px sans-serif`; c.fillText(noteName(m), 3, y + g.rowH - 3); }
    }
    const st = gridStep(t), b = barLen(), be = beatLen();
    for(let p = 0; p <= g.len + 1e-6; p += st){
      const x = g.x(p), onBar = Math.abs(p / b - Math.round(p / b)) < 1e-6, onBeat = Math.abs(p / be - Math.round(p / be)) < 1e-6;
      c.fillStyle = onBar ? "rgba(255,255,255,.35)" : onBeat ? "rgba(255,255,255,.14)" : "rgba(255,255,255,.05)";
      c.fillRect(Math.round(x), 0, 1, g.H);
    }
    const ls = noteLayersOf(t), cur2 = ls[noteLayer >= 0 ? noteLayer : ls.length - 1];
    ls.forEach(l => l.raw.forEach(n => {
      const x0 = g.x(n.p), x1 = Math.max(x0 + 4, g.x(n.p + n.d)), y = g.y(n.m) + 1;
      c.fillStyle = l === cur2 ? "#8b5cf6" : "rgba(139,92,246,.4)";
      c.fillRect(x0, y, x1 - x0 - 1, g.rowH - 2);
      if(n === noteSel){ c.strokeStyle = "#fff"; c.lineWidth = 1.5; c.strokeRect(x0 + 0.5, y + 0.5, x1 - x0 - 2, g.rowH - 3); }
    }));
    if(running && t.len){ const ph = (((now() - t.anchor) % t.len) + t.len) % t.len; c.fillStyle = "#3ddc97"; c.fillRect(g.x(ph), 0, 2, g.H); }
  }

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
        <button type="button" class="lp-b" id="lp-guide" title="Originál písně pod smyčkami (její stopy z Steems), na začátku části, kterou hraješ — jen s písní otevřenou ve Steems">Originál</button>
        <span class="lp-sp"></span>
        <button type="button" class="lp-b lp-prep" id="lp-prep" title="Z písně otevřené ve Steems: tempo, takt, tónina, části jako scény, aranžmá v pořadí písně s délkou v taktech a akordy každé části — do prázdného looperu">Připravit z písně</button>
        <button type="button" class="lp-b" id="lp-side-btn" title="Boční panel se samply z tvých složek">Samply</button>
        <button type="button" class="lp-b" id="lp-open" title="Otevřít uložený projekt looperu">Otevřít</button>
        <button type="button" class="lp-b" id="lp-save" title="Uložit projekt (stopy, vrstvy, části, aranžmá) — do složky Looper v hudbě">Uložit</button>
        <button type="button" class="lp-b" id="lp-export" title="Aranžmá jako WAV, nebo jako projekt ve Steems (stopa za stopu)">Export</button>
        <button type="button" class="lp-b" id="lp-undo" title="Vrátit poslední vrstvu (Z)">↶ Zpět</button>
        <button type="button" class="lp-b" id="lp-redo" title="Znovu (Shift+Z)">↷</button>
        <span class="lp-f" title="Hlasitost všech smyček">Smyčky <input type="range" id="lp-vol" min="0" max="1.4" step="0.01"></span>
        <button type="button" class="lp-x" id="lp-close" title="Zavřít (smyčky zůstanou, dokud nezavřeš Pick)">${icon("close", 14)}</button>
      </div>
      <div class="lp-body"><div class="lp-main">
        <div class="lp-master" id="lp-master"></div>
        <div class="lp-slice" id="lp-slice" hidden></div>
        <div class="lp-comp" id="lp-comp"></div>
        <div class="lp-tracks" id="lp-tracks"></div>
        <div class="lp-steps" id="lp-steps" hidden></div>
        <div class="lp-notes" id="lp-notes" hidden></div>
        <div class="lp-pads-bank" id="lp-pads"></div>
        <div class="lp-piano" id="lp-piano" hidden></div>
        <div class="lp-skel" id="lp-skel" hidden></div>
        <div class="lp-sc" id="lp-sc"></div>
        <div class="lp-arr" id="lp-arr"></div>
        <div class="lp-plan" id="lp-plan" hidden></div>
        <div class="lp-help">
          <b>Jak na to:</b> tlačítko stopy = <b>nahrát → hrát → přidat vrstvu → hrát</b>. Nahrávání začne na začátku taktu a skončí na konci taktu —
          samo po zvolené délce, nebo po druhém stisku (auto). Když nahráváš a stiskneš jinou stopu, předá se na stejném taktu. Části písně dole:
          každá si pamatuje, které stopy hrají (M na stopě); vybraná se přepne na začátku dalšího taktu.<br>
          Klávesy: <kbd>1</kbd>–<kbd>5</kbd> tlačítka stop · <kbd>mezerník</kbd> spustit / zastavit · <kbd>Tab</kbd> další stopa ·
          <kbd>Enter</kbd> tlačítko vybrané stopy · <kbd>Z</kbd> zpět · <kbd>Shift</kbd>+<kbd>Z</kbd> znovu · <kbd>←</kbd> <kbd>→</kbd> části
          · Zpoždění zvukovky <b id="lp-lat"></b> se z nahrávek odečítá (měří se v nahrávání ve Steems).
          <div class="lp-ped" id="lp-ped"></div>
        </div>
      </div><aside class="lp-side" id="lp-samples"></aside></div>`;
    document.body.appendChild(root);
    const $ = (id) => root.querySelector("#" + id);
    $("lp-go").onclick = () => (running ? stopAll() : start(false));
    $("lp-close").onclick = close;
    $("lp-undo").onclick = undo; $("lp-redo").onclick = redo;
    $("lp-save").onclick = () => save();
    $("lp-open").onclick = () => openProject();
    $("lp-side-btn").onclick = () => { cfg.side = !cfg.side; keep(); if(!cfg.side) smStop(); applySide(); paintSamples(); };
    applySide();
    $("lp-export").onclick = () => exportMenu();
    $("lp-guide").onclick = () => { cfg.guide = !cfg.guide; keep(); if(!cfg.guide) guideStop(); else if(running) guideAt(t0, guidePos()); paint(); };
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
        <div class="row"><select data-k="src" title="Odkud se nahrává"><option value="gtr">Kytara (zesilovač Picku)</option><option value="gtrdi">Kytara čistě (DI) + otisk</option><option value="mic">Mikrofon</option>
            <option value="midi">MIDI nástroj (klávesy)</option><option value="drums">Bicí (pady, kroky)</option></select>
          <select data-k="bars" title="Délka první smyčky: auto = do druhého stisku">${BARS.map(b => `<option value="${b}">${b ? b + (b === 1 ? " takt" : b < 5 ? " takty" : " taktů") : "auto"}</option>`).join("")}</select></div>
        <div class="row fxr"><button type="button" data-k="print" title="Otisk, přes který čistá kytara této stopy hraje (jde vyměnit i po nahrání)">Otisk…</button>
          <select data-k="fx" title="Efekt stopy — hraje se přes něj, nahrávka zůstává čistá">${Object.entries(FX).map(([k, f]) => `<option value="${k}">${esc(f.name)}</option>`).join("")}</select></div>
        <div class="row mi"><select data-k="instr" title="Zvuk MIDI stopy"></select>
          <select data-k="q" title="Zarovnání not do rytmu (kvantizace) — jde změnit i po nahrání"><option value="0">volně</option><option value="8">na 1/8</option><option value="16">na 1/16</option></select>
          <button type="button" data-k="steps" title="Krokový sekvencer a pady bicích">Kroky</button>
          <button type="button" data-k="notes" title="Noty smyčky: přidat, posunout, prodloužit, smazat">Noty</button></div>
        <div class="row"><button type="button" data-k="mute" title="Hraje v téhle části (M)">M</button><button type="button" data-k="undo" title="Vrátit poslední vrstvu téhle stopy">↶</button><button type="button" data-k="clear" title="Smazat stopu">Smazat</button></div>
        <div class="vol">hlasitost <input type="range" data-k="vol" min="0" max="1.4" step="0.01" value="${t.vol}"></div></div>`).join("");
    root.querySelectorAll(".lp-t").forEach(n => {
      const t = tracks[+n.dataset.i];
      n.querySelector(".lp-btn").onclick = () => press(t.i);
      n.onclick = (e) => { if(!e.target.closest("button, select, input")){ sel = t.i; paint(); } };
      n.querySelector(".nm input").onchange = (e) => { t.name = e.target.value.trim() || `Stopa ${t.i + 1}`; };
      n.querySelector('[data-k="src"]').onchange = (e) => {
        if(t.layers.length && isNotes(t) !== (e.target.value === "midi" || e.target.value === "drums")){ showToast("Stopa už má nahrávku — nejdřív ji smaž"); e.target.value = t.src; return; }
        t.src = e.target.value; if(isNotes(t)) midiConnect();
        if(t.src === "gtrdi" && !t.print && skel && skel.song) (async () => { const all = await prints(); let pr = null;
          try{ const d = await (await fetch("/api/stamps/songs")).json(); const row = (d.songs || []).find(r => r.name === skel.song); if(row && row.print) pr = all.find(x => x.song === row.print.song && x.id === row.print.pid); }catch(e){}
          pr = pr || all.filter(x => x.song === skel.song).sort((a, b) => (b.created || 0) - (a.created || 0))[0];
          if(pr){ await setTrackSound(t, {print: {song: pr.song, pid: pr.id, name: pr.name}}); showToast(`Stopa ${t.i + 1}: čistá kytara přes otisk „${pr.name}“`); } })();
        if(t.src === "mic" && !t.fx) setTrackSound(t, {fx: "room"}); if(t.src !== "drums" && stepT === t){ stepT = null; paintSteps(); }
        if(t.src !== "midi" && noteT === t){ noteT = null; paintNotes(); } paint(); };
      n.querySelector('[data-k="instr"]').onchange = (e) => { t.instr = e.target.value; rerenderAll(t); };
      n.querySelector('[data-k="fx"]').onchange = (e) => setTrackSound(t, {fx: e.target.value});
      n.querySelector('[data-k="print"]').onclick = () => pickPrint(t);
      n.querySelector('[data-k="q"]').onchange = (e) => { t.q = +e.target.value; rerenderAll(t); };
      n.querySelector('[data-k="steps"]').onclick = () => openSteps(t);
      n.querySelector('[data-k="notes"]').onclick = () => openNotes(t);
      n.querySelector('[data-k="bars"]').onchange = (e) => { t.bars = +e.target.value; };
      n.querySelector('[data-k="vol"]').oninput = (e) => { t.vol = +e.target.value; if(ac) applyMutes(now()); };
      n.querySelector('[data-k="mute"]').onclick = () => toggleTrack(t.i);
      n.querySelector('[data-k="undo"]').onclick = () => { const k = history.lastIndexOf(t.i); if(k < 0) return; history.splice(k, 1); history.push(t.i); undo(); };
      n.querySelector('[data-k="clear"]').onclick = async () => { if(!t.layers.length || await Dialog.confirm(`Smazat stopu „${t.name}“ (${t.layers.length} vrstev)?`, {ok: "Smazat", danger: true})) clear(t.i); };
    });
    paintPedal();
    wirePieceDrop();
    root.querySelector(".lp-main").addEventListener("click", (e) => {
      const h = e.target.closest(".h"); if(!h) return;
      const panel = FOLDABLE.map(id => root.querySelector("#" + id)).find(n => n && n.contains(h)); if(!panel) return;
      cfg.fold = cfg.fold || {}; cfg.fold[panel.id] = !cfg.fold[panel.id]; keep(); applyFolds();
    });
  }
  // the sounds a MIDI track can have: the looper's synths, and Pick's instruments when the library is there
  let libGroups = null;
  function instrOptions(){
    return `<optgroup label="Syntezátory">${LoopSound.SYNTHS.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</optgroup>`
      + (libGroups || []).map(g => `<optgroup label="${esc(g.kategorie)}">${g.nastroje.map(x => `<option value="${esc(x.id)}">${esc(x.nazev)}</option>`).join("")}</optgroup>`).join("");
  }
  function instrName(id){
    const sy = LoopSound.SYNTHS.find(x => x.id === id);
    if(sy) return sy.name;
    for(const g of libGroups || []) for(const x of g.nastroje) if(x.id === id) return x.nazev;
    return id;
  }
  function loadLib(){
    if(libGroups) return;
    try{ Mixer.guitar.instrumentLib().then(L => { libGroups = L.nastrojePodleKategorii(); paint(); }).catch(() => {}); }catch(e){}
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
    $("lp-song").textContent = [song, projName ? "projekt " + projName : ""].filter(Boolean).map(x => "· " + x).join(" ");
    $("lp-beats").innerHTML = Array.from({length: cfg.perBar}, (_, k) => `<i class="${k === 0 ? "one" : ""}"></i>`).join("");
    root.querySelectorAll(".lp-t").forEach(n => {
      const t = tracks[+n.dataset.i], s = stateOf(t);
      n.dataset.s = s; n.classList.toggle("sel", t.i === sel);
      n.querySelector(".lbl").textContent = STATE_TXT[s];
      n.querySelector('[data-k="src"]').value = t.src; n.querySelector('[data-k="bars"]').value = t.bars;
      { const pb = n.querySelector('[data-k="print"]'); pb.hidden = t.src !== "gtrdi"; pb.textContent = t.print ? "Otisk: " + t.print.name : "Otisk…"; pb.classList.toggle("on", !!t.print);
        const fs = n.querySelector('[data-k="fx"]'); if(document.activeElement !== fs) fs.value = t.fx || "none"; n.querySelector(".row.fxr").hidden = isNotes(t) && t.src === "drums"; }
      const mi = n.querySelector(".row.mi"), is = n.querySelector('[data-k="instr"]');
      mi.hidden = !isNotes(t);
      is.hidden = t.src !== "midi"; n.querySelector('[data-k="steps"]').hidden = t.src !== "drums";
      n.querySelector('[data-k="notes"]').hidden = t.src !== "midi";
      n.querySelector('[data-k="notes"]').classList.toggle("st", noteT === t);
      n.querySelector('[data-k="steps"]').classList.toggle("st", stepT === t);
      n.querySelector('[data-k="q"]').value = t.q;
      const opts = instrOptions();
      if(is.dataset.o !== opts){ is.dataset.o = opts; is.innerHTML = opts; }
      is.value = t.instr;
      n.querySelector('[data-k="mute"]').classList.toggle("on", scenes[cur] && !scenes[cur].on.has(t.i));
      const bars = t.len ? Math.round(t.len / barLen() * 100) / 100 : 0;
      const what = t.src === "midi" ? instrName(t.instr) : srcs[t.src].name;
      n.querySelector(".meta").textContent = t.layers.length ? `${bars} ${bars === 1 ? "takt" : bars < 5 ? "takty" : "taktů"} · ${t.layers.length} ${t.layers.length === 1 ? "vrstva" : t.layers.length < 5 ? "vrstvy" : "vrstev"}${t.steps ? " (kroky)" : ""} · ${what}` : what;
    });
    { const k = $("lp-skel"); k.hidden = !skel;
      if(skel){
        const here = Mixer.currentSong && Mixer.currentSong() === skel.song, rate = here && ac ? drumsRate() : 1;
        k.innerHTML = `<b class="h">Kostra z písně</b><span>${esc(skel.song || "")}</span><i>${skel.bpm} BPM</i><i>${skel.perBar}/4</i>${skel.key ? `<i>tónina ${esc(skel.key)}</i>` : ""}<i>${skel.parts} částí</i><i>${skel.bars} taktů</i>${skel.chords ? "" : '<i class="warn">akordy zatím nejsou</i>'}
          <span class="lp-sp"></span>
          <span class="lp-drums" title="${here ? "Bicí každé části ze skutečné bicí stopy písně, smyčka na dobách — hrají pod smyčkami a přepínají se s částí na taktu" : "Bicí z písně hrají, jen když je píseň otevřená ve Steems"}">
            <button type="button" class="lp-b ${cfg.drums && here ? "on" : ""}" id="lp-drums" ${here ? "" : "disabled"}>🥁 Bicí z písně</button>
            <select id="lp-drumbars" title="Délka smyčky bicích">${[1, 2, 4].map(n => `<option value="${n}" ${cfg.drumBars === n ? "selected" : ""}>${n} ${n === 1 ? "takt" : "takty"}</option>`).join("")}</select>
            <input type="range" id="lp-drumvol" min="0" max="1.5" step="0.01" value="${cfg.drumVol}" title="Hlasitost bicích">
            ${Math.abs(rate - 1) > 0.06 ? `<i class="warn" title="Tempo looperu je jiné než tempo písně — bicí se zrychlí nebo zpomalí a změní se jim i výška">bicí ${rate > 1 ? "zrychlené" : "zpomalené"} o ${Math.round(Math.abs(1 / rate - 1) * 100)} %</i>` : ""}
          </span>`;
        const db = k.querySelector("#lp-drums");
        if(db) db.onclick = () => { cfg.drums = !cfg.drums; keep(); if(running) drumsAt(cur, nextBar(now())); paint(); };
        k.querySelector("#lp-drumbars").onchange = (e) => { cfg.drumBars = +e.target.value; keep(); if(running) drumsAt(cur, nextBar(now())); };
        k.querySelector("#lp-drumvol").oninput = (e) => { cfg.drumVol = +e.target.value; keep(); if(drumBus) drumBus.gain.value = cfg.drumVol; };
      }
    }
    $("lp-sc").innerHTML = `<span class="h">ČÁSTI PÍSNĚ</span>` + scenes.map((s, k) => `<button type="button" data-k="${k}" class="${k === cur ? "cur" : ""}${pending && pending.k === k ? " next" : ""}"
        title="Klik: přepnout na začátku dalšího taktu · dvojklik: přejmenovat${s.full || s.chords ? "\nAkordy po taktech: " + esc(s.full || s.chords) : ""}"><b>${esc(s.name)}${s.bars ? ` <em>${s.bars} t.</em>` : ""}${skel && cfg.drums && ac && Mixer.currentSong && Mixer.currentSong() === skel.song ? (drumsBuf(k) ? ' <em title="Hraje s bicími z písně">🥁</em>' : ' <em title="V téhle části bicí nehrají">bez bicích</em>') : ""}</b><span>${s.on.size ? [...s.on].sort().map(x => x + 1).join(" ") : "ticho"}</span>${s.chords ? `<span class="ch">${esc(s.chords)}</span>` : ""}</button>`).join("")
      + `<button type="button" data-add title="Nová část — začne se stopami té, co hraje">＋ Část</button>`;
    $("lp-sc").querySelectorAll("[data-k]").forEach(b => {
      b.onclick = () => goScene(+b.dataset.k);
      b.ondblclick = async () => { const s = scenes[+b.dataset.k], v = await Dialog.prompt("Název části:", s.name); if(v && v.trim()){ s.name = v.trim(); paint(); } };
    });
    $("lp-sc").querySelector("[data-add]").onclick = addScene;
    $("lp-prep").onclick = prepFromSong; $("lp-prep").disabled = hasAudio();
    paintArr();
    paintPlan();
    paintSlice();
    paintMaster();
    applyFolds();
    if(!root.querySelector("#lp-pads .lp-pb")) paintPads();
    if(!root.querySelector("#lp-comp .lp-cp-top")) paintComp();
    if(!LS.restored){ LS.restored = true; const names = (cfg.slSongs || []).slice(0, 12);
      if(names.length) (async () => { for(const n of names){ try{ await addSong(n); }catch(e){} } paintSlice(); paintComp(); })(); }
    if(!root.querySelector("#lp-samples .lp-sm-top")) paintSamples();
    if(piano.open){ const r = root.querySelector("#lp-pn-rec"), t = pianoTrack(); if(r && t) { r.classList.toggle("on", !!t.job); r.textContent = t.job ? "● Nahrávám" : "● Nahrát"; } }
    $("lp-guide").classList.toggle("on", !!cfg.guide); $("lp-guide").hidden = !guideSong();
    $("lp-save").title = projName ? `Uložit do projektu „${projName}“` : "Uložit projekt (stopy, vrstvy, části, aranžmá) — do složky Looper v hudbě";
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
      if(noteT && running && !noteDrag) drawRoll();
      if(stepT && stepT.len && running){
        const st = beatLen() / 4, col = Math.floor(((((tm - stepT.anchor) % stepT.len) + stepT.len) % stepT.len) / st);
        if(col !== frame.col){ frame.col = col; root.querySelectorAll(".lp-grid .c.now").forEach(c => c.classList.remove("now")); root.querySelectorAll(`.lp-grid .c[data-i="${col}"]`).forEach(c => c.classList.add("now")); }
      }
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
    if((a === "scene:+" || a === "scene:-") && arrOn && arrPos && running){ arrStep(a === "scene:+" ? 1 : -1); return a === "scene:+" ? "další část aranžmá" : "předchozí část aranžmá"; }
    if(a === "scene:+"){ goScene(Math.min(scenes.length - 1, (pending ? pending.k : cur) + 1)); return "další část"; }
    if(a === "scene:-"){ goScene(Math.max(0, (pending ? pending.k : cur) - 1)); return "předchozí část"; }
    return null;
  }
  document.addEventListener("keyup", (e) => { if(open && piano.open && PIANO_KEYS.includes(e.code)) pianoOff(12 * (piano.oct + 1) + PIANO_KEYS.indexOf(e.code)); }, true);
  document.addEventListener("keydown", (e) => {
    if(!open || e.metaKey || e.ctrlKey || e.altKey) return;
    const tg = e.target;
    if(tg && tg.closest && tg.closest("input:not([type=range]), textarea, select, [contenteditable]")) return;
    if(typeof Dialog !== "undefined" && Dialog.isOpen && Dialog.isOpen()) return;
    if(padLearn && padLearn.kind === "key"){
      e.preventDefault(); e.stopPropagation();
      const i = padLearn.i;
      if(e.code === "Escape"){ padLearn = null; paintPads(); return; }
      if(RESERVED.includes(e.code)){ showToast(`${keyLabel(e.code)} už v looperu něco dělá — stiskni jinou klávesu (Esc zruší)`); return; }
      padLearn = null;
      pads.forEach((p, j) => { if(j !== i && p.key === e.code) p.key = null; });
      if(pads[i]){ pads[i].key = e.code; padsKeep(); paintPads(); showToast(`Pad ${i + 1} → klávesa ${keyLabel(e.code)}` + (piano.open && PIANO_KEYS.includes(e.code) ? " (s otevřeným klavírem hraje klavír)" : "")); }
      return;
    }
    let done = true;
    if(/^Digit[1-5]$/.test(e.code)) press(+e.code.slice(5) - 1);
    else if(e.code === "Space") run("play");
    else if(e.code === "Enter") press(sel);
    else if(e.code === "Tab") run("next");
    else if(e.code === "KeyZ") e.shiftKey ? redo() : undo();
    else if((e.code === "ArrowDown" || e.code === "ArrowUp") && cfg.side && SM.vis && SM.vis.length) smMove(e.code === "ArrowDown" ? 1 : -1);
    else if(e.code === "ArrowRight") run("scene:+");
    else if(e.code === "ArrowLeft") run("scene:-");
    else if((e.code === "Delete" || e.code === "Backspace") && noteT && noteSel){
      const l = noteLayersOf(noteT).find(x => x.raw.includes(noteSel));
      if(l){ l.raw.splice(l.raw.indexOf(noteSel), 1); noteSel = null; noteChanged(noteT, l); noteHead(); drawRoll(); }
    }
    else if(piano.open && PIANO_KEYS.includes(e.code)){ if(!e.repeat) pianoOn(12 * (piano.oct + 1) + PIANO_KEYS.indexOf(e.code), 0.85); }
    else if(piano.open && (e.code === "Minus" || e.code === "Equal")){ piano.oct = Math.max(1, Math.min(6, piano.oct + (e.code === "Equal" ? 1 : -1))); paintPiano(); }
    else if(pads.some(p => p.key === e.code)){ if(!e.repeat) padHit(pads.findIndex(p => p.key === e.code)); }
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
        if(o.parts || o.arrangement){ makeScenes(o.parts); if(o.arrangement) arrFrom(o.arrangement); else arr = []; skel = null; }
        else if(s && Mixer.currentSong() === s) skeletonFrom(s, true);
        else { makeScenes(); arr = []; skel = null; }
        projName = null;
      }
    }
    try{ Mixer.pause(); }catch(e){}
    root.hidden = false; open = true;
    loadLib(); if(tracks.some(isNotes)) midiConnect();
    paint(); paintPedal(); paintSteps();
  }
  function close(){ if(root) root.hidden = true; open = false; }
  return {open: openLooper, close, isOpen: () => open, run, press, undo, redo, start, stop: stopAll, goScene,
    state: () => ({running, bpm: cfg.bpm, sel, cur, scenes: scenes.map(s => ({name: s.name, on: [...s.on]})),
      tracks: tracks.map(t => ({name: t.name, src: t.src, instr: t.instr, q: t.q, layers: t.layers.length, len: t.len, state: ac ? stateOf(t) : "empty"})),
    arr: arr.map(st => ({name: scenes[st.k] ? scenes[st.k].name : null, bars: stepBars(st)})), arrOn, arrAt: arrPos ? arrPos.i : null, project: projName, lastExport}),
    save, load, playArr, bounce, exportWav, exportSteems,
    // for the tests: how loud a track's layers are
    _rms: (i) => { const t = tracks[i]; if(!t.layers.length) return 0; const d = t.layers[t.layers.length - 1].buf.getChannelData(0); let q = 0; for(let k = 0; k < d.length; k++) q += d[k] * d[k]; return Math.sqrt(q / d.length); },
    _cut: cut, _srcs: srcs,
    _midi: (data, ago = 0) => onMidi({data, timeStamp: performance.now() - ago * 1000}),
    _steps: (i, id, bars) => setSteps(tracks[i], groove(id, bars || 1)),
    _layers: (i) => tracks[i].layers.map(l => ({raw: l.raw ? l.raw.map(x => ({...x})) : null, steps: !!l.steps})),
    _rollAt: (p, m) => { const cv = root.querySelector("#lp-notes canvas"), g = rollGeo(noteT, cv), r3 = cv.getBoundingClientRect(); return {x: r3.left + g.x(p) + 3, y: r3.top + g.y(m) + g.rowH / 2, right: r3.left + g.x(p) - 3}; },
    _set: (i, k, v) => { tracks[i][k] = v; paint(); }};
})();
