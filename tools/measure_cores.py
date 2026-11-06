"""How hard a NAM core distorts — told by what it does, not by a label (DITORIG's "clean" on a thrash song).

A pure tone goes in at three strengths, 20 dB apart. How much of each 20 dB step does not come out is the
compression: "low" between a soft touch and a picked note, "high" between that and playing hard. A clean amp
compresses neither, a crunch amp the hard one, a high-gain amp is saturated already at a soft touch. A cabinet
in the capture filters the overtones (the THD, kept beside it), not this.

    ~/.local/venvs/pick-tone/bin/python tools/measure_cores.py <core file> [<core file> …]

Prints {"<file>": thd_percent, …} as JSON. The same WebAssembly engine as the live guitar (tone_worker.Nam).
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parent.parent
SR = 48000
F0 = 196.0            # G3: a note in the middle of the neck
LEVEL = 0.25          # a picked note's body, as a DI plays into an amp


def tone(level=LEVEL):
    t = np.arange(int(SR * 1.2)) / SR
    x = np.sin(2 * np.pi * F0 * t) * level
    x[:int(SR * 0.02)] *= np.linspace(0, 1, int(SR * 0.02))
    return x.astype(np.float32)


def thd(y):
    y = np.asarray(y, dtype=np.float64)[int(SR * 0.4):int(SR * 1.15)]
    y = y * np.hanning(len(y))
    spec = np.abs(np.fft.rfft(y)) ** 2
    fr = np.fft.rfftfreq(len(y), 1 / SR)
    def band(f):
        k = (fr > f * 0.97) & (fr < f * 1.03)
        return float(spec[k].sum())
    fund = band(F0)
    harm = sum(band(F0 * k) for k in range(2, 16) if F0 * k < SR / 2 - 500)
    return 100 * np.sqrt(harm / max(fund, 1e-20))


def level(y):
    y = np.asarray(y, dtype=np.float64)[int(SR * 0.4):int(SR * 1.15)]
    return 20 * np.log10(np.sqrt(np.mean(y * y)) + 1e-12)


def measure(files, drive_db=None):
    sys.path.insert(0, str(APP))
    import tone_worker
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    job = {"sr": SR, "node": node, "nam_js": str(APP / "tone_nam.mjs"), "worklet": str(APP / "static/nam/nam-worklet.js"),
           "wasm": str(APP / "static/nam/nam-engine.wasm")}
    nam = tone_worker.Nam(job, 3)
    out = {}
    try:
        for f in files:
            try:
                g = 10 ** ((drive_db or {}).get(f, 0.0) / 20)
                # three strengths 20 dB apart: a soft touch, a picked note, hard
                quiet, mid, hi = nam.render(str(f), [tone(LEVEL / 100) * g, tone(LEVEL / 10) * g, tone() * g])
                out[str(f)] = {"low": round(20 - (level(mid) - level(quiet)), 1), "high": round(20 - (level(hi) - level(mid)), 1),
                               "thd": round(thd(hi), 1)}
            except Exception as e:                                  # noqa: BLE001 — one broken core does not stop the rest
                out[str(f)] = None
                print(f"  {Path(f).name}: {e}", file=sys.stderr)
    finally:
        nam.close()
    return out


if __name__ == "__main__":
    print(json.dumps(measure(sys.argv[1:]), ensure_ascii=False, indent=0))
