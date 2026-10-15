// Audio through NAM models, offline, with the very WebAssembly engine the live
// guitar plays through (static/nam/nam-worklet.js) — so what the tone print is
// fitted on is what the guitar will sound like. Run by tone_worker.py:
//
//   node tone_nam.mjs <job.json>                       one batch, then exit
//   node tone_nam.mjs --serve <worklet> <wasm> <sr>    one request a line on stdin
//
// job: {sr, worklet, wasm, renders: [{model, inputs: [...], outputs: [...]}]}
// A request line is one render: {model, inputs, outputs}; the answer "OK n" or
// "ERR why", one line. Every input is raw little-endian float32 mono and comes
// out the same length. A batch prints "PROGRESS done of", then "DONE n" or "ERROR why".
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

const BLOCK = 2048;

async function engine(worklet, wasm){
  // The worklet file is the Emscripten glue plus an AudioWorkletProcessor; outside
  // a worklet scope the processor's two globals only have to exist.
  const src = fs.readFileSync(worklet, "utf8");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pick-nam-"));
  const file = path.join(dir, "nam-engine.mjs");
  fs.writeFileSync(file, "globalThis.AudioWorkletProcessor ??= class {};\nglobalThis.registerProcessor ??= () => {};\n"
    + src + "\nexport { createNamEngine };\n");
  try{
    const { createNamEngine } = await import(pathToFileURL(file).href);
    return await createNamEngine({ wasmBinary: fs.readFileSync(wasm), locateFile: (p) => p });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

class Player {
  constructor(mod, sr){
    this.mod = mod;
    this.id = mod._nam_createInstance(sr, BLOCK);
    this.ptr = mod._nam_getBuffer(this.id);
    this.quiet = new Float32Array(Math.round(sr * 0.5));    // a model's memory of what came before, emptied
    this.model = null;
  }
  load(model){
    if(this.model === model) return;
    const mod = this.mod, json = fs.readFileSync(model, "utf8");
    const n = mod.lengthBytesUTF8(json) + 1, p = mod._malloc(n);
    mod.stringToUTF8(json, p, n);
    const ok = mod._nam_loadModel(this.id, p, -1);
    mod._free(p);
    if(!ok){ this.model = null; throw new Error(`${path.basename(model)}: ${mod.UTF8ToString(mod._nam_getLastError()) || "model se nenačetl"}`); }
    this.model = model;
  }
  run(x){
    const mod = this.mod, out = new Float32Array(x.length);
    for(let off = 0; off < x.length; off += BLOCK){
      const k = Math.min(BLOCK, x.length - off), at = this.ptr >> 2;   // memory may grow: the view is taken anew
      mod.HEAPF32.set(x.subarray(off, off + k), at);
      mod._nam_process(this.id, k);
      out.set(mod.HEAPF32.subarray(at, at + k), off);
    }
    return out;
  }
  render(r){
    this.load(r.model);
    r.inputs.forEach((inp, k) => {
      const b = fs.readFileSync(inp);
      this.run(this.quiet);
      const y = this.run(new Float32Array(b.buffer, b.byteOffset, b.byteLength >> 2));
      fs.writeFileSync(r.outputs[k], Buffer.from(y.buffer, y.byteOffset, y.byteLength));
    });
    return r.inputs.length;
  }
}

async function batch(file){
  const job = JSON.parse(fs.readFileSync(file, "utf8"));
  const p = new Player(await engine(job.worklet, job.wasm), job.sr);
  let n = 0;
  for(const [i, r] of job.renders.entries()){
    n += p.render(r);
    console.log(`PROGRESS ${i + 1} ${job.renders.length}`);
  }
  console.log(`DONE ${n}`);
}

async function serve(worklet, wasm, sr){
  const p = new Player(await engine(worklet, wasm), Number(sr));
  console.log("READY");
  for await (const line of readline.createInterface({ input: process.stdin })){
    if(!line.trim()) continue;
    try{ console.log(`OK ${p.render(JSON.parse(line))}`); }
    catch(e){ console.log(`ERR ${(e && e.message) || e}`); }
  }
}

const main = process.argv[2] === "--serve" ? serve(...process.argv.slice(3, 6)) : batch(process.argv[2]);
main.catch((e) => { console.log(`ERROR ${(e && e.message) || e}`); process.exit(1); });
