"""A song's vocals sung in another voice — run in an environment of its own.

Seed-VC (github.com/Plachtaa/seed-vc, its 44 kHz singing model) keeps what
was sung — melody, timing, phrasing, the words — and changes whose voice it
is, taken from a short sample of that voice. It is heavy (torch, about 2 GB
of weights), so the server never imports it: this file is started as a
process in ~/.local/venvs/pick-svc, does one conversion and ends, and with it
the memory goes back.

    python svc_worker.py --source vocals.wav --ref voice.wav --out sung.wav

Progress goes to stdout as lines "PROGRESS <done> <of>"; the last line is
"DONE <seconds>" or "ERROR <what>".
"""
import argparse
import os
import sys
import time
from pathlib import Path

HOME = Path.home()
SEED_DIR = Path(os.environ.get("PICK_SVC_DIR") or HOME / ".local" / "share" / "spotdl-helper" / "svc" / "seed-vc")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=25)          # more = cleaner and slower
    ap.add_argument("--shift", type=int, default=0)           # semitones, e.g. -12 = an octave lower
    ap.add_argument("--auto", type=int, default=0)            # 1 = move the melody into the voice's own range
    ap.add_argument("--cfg", type=float, default=0.7)         # how strongly it leans to the sample's timbre
    a = ap.parse_args()

    if not SEED_DIR.is_dir():
        print("ERROR Seed-VC tu není.", flush=True)
        return 2
    os.chdir(SEED_DIR)                    # its weights and configs are found relative to itself
    sys.path.insert(0, str(SEED_DIR))
    import numpy as np
    import torch
    import inference as vc

    # Apple's GPU: no mixed precision in this torch (it is asked for with fp16
    # off as well), and no 64-bit floats, which the pitch reader hands over.
    import contextlib
    autocast = torch.autocast
    torch.autocast = lambda device_type="cpu", **k: (contextlib.nullcontext() if device_type == "mps"
                                                     else autocast(device_type=device_type, **k))
    load = vc.load_models

    def load_models(args):
        got = list(load(args))
        f0 = got[2]
        if f0 is not None:
            got[2] = lambda *x, **k: np.asarray(f0(*x, **k), dtype=np.float32)
        model = got[0]
        infer, state = model.cfm.inference, {"n": 0}

        def counted(cond, *x, **k):
            out = infer(cond, *x, **k)
            state["n"] += 1
            print(f"PROGRESS {state['n']}", flush=True)
            return out
        model.cfm.inference = counted
        print("LOADED", flush=True)
        return tuple(got)
    vc.load_models = load_models

    out = Path(a.out)
    work = out.parent / (out.stem + ".svc")
    args = argparse.Namespace(source=a.source, target=a.ref, output=str(work), diffusion_steps=max(4, min(a.steps, 100)),
                              length_adjust=1.0, inference_cfg_rate=a.cfg, f0_condition=True, auto_f0_adjust=bool(a.auto),
                              semi_tone_shift=a.shift, checkpoint=None, config=None, fp16=False)
    t = time.time()
    try:
        vc.main(args)
    except Exception as e:                                     # whatever it was, the server is told in one line
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        return 1
    made = sorted(work.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    if not made:
        print("ERROR Nic nevzniklo.", flush=True)
        return 1
    made[-1].replace(out)
    for f in work.glob("*"):
        f.unlink()
    work.rmdir()
    print(f"DONE {time.time() - t:.1f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
