# Signalsmith Stretch (vendored)

[`signalsmith-stretch`][pkg] v1.3.2, MIT. One file: the WASM is base64-embedded
in the `.mjs`, so there is nothing else to host.

Used for pitch-shifting a stem **without** changing its length. The mixer's
sync model is "N buffers started at the same instant", so a stem is re-rendered
offline at the new pitch and played back at rate 1 — rather than putting a
stretch node in the live graph, whose own latency would offset that one stem
against the others.

To update: `npm install signalsmith-stretch@latest` in a scratch directory and
copy `SignalsmithStretch.mjs` over this one.

[pkg]: https://www.npmjs.com/package/signalsmith-stretch
