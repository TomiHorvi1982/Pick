# NAM engine (vendored)

Prebuilt WebAssembly build of [Neural Amp Modeler Core][core], packaged by
TONE3000 as [`neural-amp-modeler-wasm`][pkg] v2.0.1 and copied here from the
npm package's `dist/engine/`.

    index.js          main-thread API (NamEngine, NamNode)
    nam-worklet.js    the AudioWorklet processor
    nam-engine.wasm   the engine, 415 kB

Vendored rather than built: the repo ships no artifacts and building it needs
an Emscripten toolchain (~1.5 GB) for a result that is byte-for-byte what npm
already publishes. Licence in LICENSE.

To update: `npm install neural-amp-modeler-wasm@latest` in a scratch directory
and copy `dist/engine/` over this folder.

[core]: https://github.com/sdatkinson/NeuralAmpModelerCore
[pkg]: https://github.com/tone-3000/neural-amp-modeler-wasm
