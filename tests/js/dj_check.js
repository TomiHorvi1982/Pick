// Pick DJ under Node: the deck arithmetic (static/dj-core.js) and the deck
// player itself (static/worklets/dj-deck.js, with a stand-in for the AudioWorklet
// globals). Prints JSON for tests/test_dj.py to check.
const path = require("path");
const fs = require("fs");
const C = require("../../static/dj-core.js");

// A 120 BPM grid from 0.5 s, then the same song at 100 BPM.
const grid = (bpm, first, n) => Array.from({length: n}, (_, i) => first + i * 60 / bpm);
const a = grid(120, 0.5, 200), b = grid(100, 0.3, 200);

const out = {};
out.beatAt = [C.beatAt(a, 0.5), C.beatAt(a, 0.75), C.beatAt(a, 0.25), C.beatAt(a, 200)];
out.timeOfBeat = [C.timeOfBeat(a, 0), C.timeOfBeat(a, 2.5), C.timeOfBeat(a, -1), C.timeOfBeat(a, 250)];
out.bpm = [C.localBpm(a, 10), C.localBpm(b, 10)];
// Drifting grid: tempo rises from 100 to 110 over the song; local BPM follows it.
const drift = [0];
for (let i = 1; i < 300; i++) drift.push(drift[i - 1] + 60 / (100 + 10 * i / 300));
out.driftBpm = [C.localBpm(drift, drift[10]), C.localBpm(drift, drift[290])];
out.nearest = [C.nearestBeat(a, 1.2), C.nearestBeat(a, 1.3)];
out.loop4 = C.loopAt(a, 3.1, 4);
out.loopHalf = C.loopAt(a, 3.3, 0.5);
out.loopNoGrid = C.loopAt(null, 3.0, 2, 0.5);

// SYNC: leader A at 120 BPM playing at rate 1.05, follower B (100 BPM) a third of a beat off.
const leader = {beats: a, pos: C.timeOfBeat(a, 40.25), rate: 1.05};
const follower = {beats: b, pos: C.timeOfBeat(b, 30.25 + 1 / 3), rate: 1};
const s = C.syncTo(leader, follower);
out.sync = s;
const after = C.beatAt(b, follower.pos + s.shift);
out.syncPhaseAfter = after - Math.floor(after);
// Phase wraps: 0.9 vs 0.1 is 0.2 apart, not 0.8.
out.syncWrap = C.syncTo({beats: a, pos: C.timeOfBeat(a, 10.9), rate: 1}, {beats: a, pos: C.timeOfBeat(a, 20.1), rate: 1}).phaseError;

// Half time: a 64 BPM leader against a 126 BPM follower syncs to 128, not to 64.
const slow = grid(64, 0.2, 200), fast = grid(126, 0.1, 300);
out.halfTime = C.syncTo({beats: slow, pos: C.timeOfBeat(slow, 20.25), rate: 1}, {beats: fast, pos: C.timeOfBeat(fast, 40.5), rate: 1});
out.xf = [C.crossfade(-1), C.crossfade(0), C.crossfade(0.5), C.crossfade(1)];
out.eq = [C.eqDb(0), C.eqDb(0.5), C.eqDb(1), C.eqDb(0.25)];
out.filter = [C.filterFor(0), C.filterFor(-1), C.filterFor(1), C.filterFor(-0.5)];
out.rate = [C.rateFor(1, 0.08), C.rateFor(-1, 0.5)];
out.keyLock = [C.keyLockSemitones(2), C.keyLockSemitones(1)];
out.time = [C.formatTime(65.25), C.formatTime(-3.5)];
out.playable = C.playable([
  {name: "ok", complete: true, stems: ["drums", "bass", "vocals", "other"]},
  {name: "takes", complete: true, stems: ["take-1", "take-2"]},
  {name: "partial", complete: false, stems: ["drums"]},
]).map(r => r.name);
out.order = C.orderStems(["other", "drums", "vocals", "take-1", "bass"]);

out.library = C.libraryItems(
  [{name: "Sep", complete: true, stems: ["drums", "vocals"], meta: {title: "Separated", artist: "X"}, duration: 200}],
  [{type: "file", rel: "Songs/Sep.mp3", name: "Sep.mp3", stems: {state: "ready", name: "Sep"}, duration: 200},
   {type: "file", rel: "Songs/New.mp3", name: "New.mp3", title: "New", artist: "Y", stems: null, duration: 180},
   {type: "file", rel: "Songs/Long.mp3", name: "Long.mp3", stems: null, duration: 3600},
   {type: "dir", rel: "Songs/Folder"}]);
const rd = Uint8Array.from([1, 1, 0, 1, 0, 0]);
out.next = [C.nextWindow(rd, new Set(), 13, 6.4), C.nextWindow(rd, new Set([4]), 26, 6.4), C.nextWindow(Uint8Array.from([1, 1]), new Set(), 0, 6.4)];

// ---- the worklet, with the AudioWorklet globals stood in.
let Processor = null;
const sandbox = {
  sampleRate: 1000, currentTime: 0,
  AudioWorkletProcessor: class { constructor() { this.port = {postMessage: (m) => sandbox.reports.push(m), onmessage: null}; } },
  registerProcessor: (name, cls) => { Processor = cls; },
  reports: [],
};
const src = fs.readFileSync(path.join(__dirname, "../../static/worklets/dj-deck.js"), "utf8");
new Function(...Object.keys(sandbox), src)(...Object.values(sandbox));
const p = new Processor();
const send = (m) => p.port.onmessage({data: m});
// Two stems: a ramp (value = frame index) and a constant 1000.
const N = 4000;
const ramp = Int16Array.from({length: N}, (_, i) => i), flat = new Int16Array(N).fill(1000);
send({type: "load", stems: [[ramp, ramp], [flat, flat]], length: N});
const run = (n) => {
  const L = new Float32Array(n), R = new Float32Array(n);
  p.process([], [[L, R]]);
  return Array.from(L, v => Math.round(v * 32768));
};
send({type: "play", on: true});
send({type: "seek", frame: 100});
p.fade = 1;                                  // skip the declick for exact values
out.w_plain = run(4);                        // 100+1000, 101+1000, …
send({type: "gains", values: [1, 0]});
run(128);                                    // gains glide across this block
out.w_gain = run(2);                         // ramp only
send({type: "rate", value: 2});
out.w_rate = run(3);                         // every second frame
send({type: "rate", value: 1});
send({type: "seek", frame: 500}); p.fade = 1;
send({type: "loop", loop: {start: 500, end: 504}});
out.w_loop = run(10);                        // 500..503 repeating
send({type: "loop", loop: null});
send({type: "seek", frame: N - 4}); p.fade = 1;
out.w_end = run(6);
out.w_endPlaying = p.playing;
out.w_reportsHavePos = sandbox.reports.every(r => typeof r.pos === "number");

// Live: the mix plays (at the stems' average gain) until a window's stems arrive.
const W = 100, M = 300;
const mixL = new Int16Array(M).fill(2000), mixR = new Int16Array(M).fill(2000);
const stemsLive = [0, 1].map(() => [new Int16Array(M), new Int16Array(M)]);
send({type: "load", stems: stemsLive, length: M, mix: [mixL, mixR], windowFrames: W, windows: 3});
send({type: "gains", values: [1, 0]});
send({type: "play", on: true});
run(128);                                    // gains glide while playing; settled at (1, 0): average 0.5
send({type: "seek", frame: 0}); p.fade = 1;
out.live_before = run(2);                    // window 0 missing: mix × 0.5
const win = new Int16Array(2 * 2 * W);       // stems × channels × frames: stem 0 = 700, stem 1 = 900
win.fill(700, 0, 2 * W); win.fill(900, 2 * W);
send({type: "fill", k: 0, start: 0, frames: W, data: win});
out.live_after = run(2);                     // stems: 1 × 700 + 0 × 900
send({type: "seek", frame: 150}); p.fade = 1;
out.live_next = run(2);                      // window 1 still missing: mix again

console.log(JSON.stringify(out));
