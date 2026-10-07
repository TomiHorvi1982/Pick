// Runs the analyzer's engine over its own demo pair — a synthesised riff and
// a performance of it with known mistakes written in — and prints what it
// found as JSON for the Python test to check.
const E = require("../../static/analyzer-engine.js");
const d = E.demoPair();
const r = E.analyze(d.ref), p = E.analyze(d.ply);
const c = E.compare(r, p, E.align(r.ft, p.ft));
const near = (type, t) => c.issues.filter(x => x.type === type && Math.abs(x.t - t) < 0.15).map(x => x.text);
const W = d.written.ref;
console.log(JSON.stringify({
  refNotes: r.notes.length, plyNotes: p.notes.length, written: W.length, bpm: r.tempo.bpm,
  scores: c.scores, counts: c.counts,
  wrongAt5: near("PITCH", W[5].t), missedAt11: near("MISSED", W[11].t), lateAt18: near("TIMING", W[18].t),
  earlyAt26: near("TIMING", W[26].t), shortAt30: near("DURATION", W[30].t), sharpAt37: near("PITCH", W[37].t),
  extras: c.issues.filter(x => x.type === "EXTRA").length, rushing: c.issues.filter(x => x.type === "TEMPO").map(x => x.text),
  cleanIssues: c.issues.filter(x => x.t < W[5].t - 0.2).length,
}));
