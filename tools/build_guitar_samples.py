"""Makes Pick's sets of recorded guitar notes (guitar_samples.py) from Soundiron's Axe Machina.

    ~/.local/venvs/pick-tone/bin/python tools/build_guitar_samples.py [--only <set id>] [<Axe Machina folder>]

Every set is the same notes — each on each string, downstrokes, two strengths, two takes, plucked
and palm muted — trimmed to the pick and a few seconds, FLAC. Axe Machina's names are Kontakt's:
C3 is MIDI 60, so "D1" is 38.

  di       its DI samples ("a"): no amp, for a tone print or a NAM amp to play through
  amped    a finished sound, played as it is: Axe Machina's own amped samples ("b"), or its DI
           samples played here through a NAM amp from Pick's amps folder and a cabinet IR (the
           same WebAssembly engine as the live guitar, tone_worker.Nam). These carry power chords
           too, recorded whole: a distorted chord is not its notes distorted one by one.
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path.home() / ".local" / "share" / "spotdl-helper" / "guitar-samples"
AMPS = Path.home() / ".local" / "share" / "spotdl-helper" / "amps"
APP = Path(__file__).resolve().parent.parent
AXE = Path.home() / "Documents" / "Native Instruments" / "Libraries" / "Axe Machina [Soundiron]"
NOTE = re.compile(r"^n\d+_([A-G]#?)(-?\d)$")
PCS = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
# kind: folder, word in the file name, seconds kept, fade, strengths (Axe Machina's v1..v3)
KINDS = {"pluck": ("Plucks", "pluck", 3.5, 0.3, (2, 3)), "mute": ("Pluck Mutes", "mute", 1.0, 0.12, (2, 3)),
         "pc": ("Power Chords", "crd_sust", 3.5, 0.3, (3,)), "pcmute": ("Power Chord Mutes", "crd_mute", 1.0, 0.12, (2, 3))}
NOTES, ALL = ("pluck", "mute"), ("pluck", "mute", "pc", "pcmute")
ROUNDS = (1, 2)
LAYERS = {"soft": 2, "full": 3}
IR_CLEAN = "ir/Bogren – Jens Bogren Lead & Clean/Cleans/"
SETS = [
    {"id": "axe-machina-di", "name": "Axe Machina (DI)", "side": "a", "kind": "di", "kinds": NOTES,
     "about": "čistý signál snímače — hraje přes otisk nebo zesilovač"},
    {"id": "axe-machina-amped", "name": "Metal · Axe Machina", "side": "b", "kind": "amped", "tone": "hi_gain", "kinds": ALL,
     "about": "jak ji Soundiron nahrál přes svůj zesilovač a bednu"},
    {"id": "axe-clean", "name": "Clean · Fender Twin", "side": "a", "kind": "amped", "tone": "clean", "kinds": ALL,
     "nam": "nam/Tim R Fender TwinVerb Norm Bright.nam", "ir": IR_CLEAN + "BD_CL_BeefyBee.wav", "drive": 0.0},
    {"id": "axe-breakup", "name": "Blues · Vox AC15", "side": "a", "kind": "amped", "tone": "crunch", "kinds": ALL,
     "nam": "nam/Phillipe P VOXAC15-TopBoost.nam", "ir": IR_CLEAN + "BD_CL_HeyJob.wav", "drive": 0.0},
    {"id": "axe-crunch", "name": "Crunch · Marshall JCM2000", "side": "a", "kind": "amped", "tone": "crunch", "kinds": ALL,
     "nam": "nam/Tim R JCM2000 Crunch.nam", "ir": "ir/Joey Sturgis – Conquer All/Vol 1/EQ IRS/MARSHALL 30TH CAB/6100_1960_57_API.wav",
     "drive": 0.0},
    {"id": "axe-higain", "name": "Hi-Gain · 5150", "side": "a", "kind": "amped", "tone": "hi_gain", "kinds": ALL,
     "nam": "nam/Helga B 5150 BlockLetter - Boosted.nam", "ir": "ir/MDL Tone – Ultimate Heavy Cabs/MIX EQ IRS/MIX EQ - CAB 1 - TOP LEFT 57+421.wav",
     "drive": 0.0},
]


def midi_of(folder: str):
    """'n06_A#0' -> 34 (Kontakt names: C3 = 60)."""
    m = NOTE.match(folder)
    return 12 * (int(m.group(2)) + 2) + PCS[m.group(1)] if m else None


def pick_files(src: Path, side: str = "a", kinds=NOTES) -> list:
    """[(kind, string, midi, layer, round, wav)] — Axe Machina's notes on one side (a DI, b amped), downstrokes."""
    out = []
    for kind in kinds:
        folder, word, _, _, layers = KINDS[kind]
        for sdir in sorted((src / "Samples" / side / folder).glob("s[1-7]")):
            st = int(sdir.name[1:])
            for ndir in sorted(sdir.iterdir()):
                midi = midi_of(ndir.name)
                if midi is None:
                    continue
                for v in layers:
                    for r in ROUNDS:
                        hit = sorted((ndir / "dn").glob(f"*_{word}_s{st}_*_v{v}_dn_r{r}_{side}.wav"))
                        if hit:
                            out.append((kind, st, midi, v, r, hit[0]))
    return out


def read_takes(files):
    import numpy as np
    import soundfile as sf
    takes = []
    for kind, st, midi, v, r, wav in files:
        sr = sf.info(str(wav)).samplerate
        keep, fade = KINDS[kind][2], KINDS[kind][3]
        x, _ = sf.read(str(wav), frames=int(sr * (keep + 0.5)), dtype="float32", always_2d=True)
        x = x.mean(1)
        peak = float(np.abs(x).max()) or 1.0
        on = max(0, int(np.argmax(np.abs(x) > 0.05 * peak)) - int(0.002 * sr))     # 2 ms before the pick
        x = x[on:on + int(sr * keep)].copy()
        takes.append([kind, st, midi, v, r, x, sr])
    return takes


def fade_out(x, sr, kind):
    import numpy as np
    n = min(len(x), int(sr * KINDS[kind][3]))
    x[len(x) - n:] *= np.linspace(1, 0, n, dtype=np.float32) ** 2
    return x


def through_amp(takes, spec):
    """Each DI note through a NAM amp and a cabinet: the live guitar's engine, three at a time."""
    import shutil
    import numpy as np
    import soundfile as sf
    from scipy import signal
    sys.path.insert(0, str(APP))
    import tone_worker
    sr = takes[0][6]
    model, ir = AMPS / spec["nam"], AMPS / spec["ir"]
    if not model.is_file() or not ir.is_file():
        raise SystemExit(f"Chybí {model if not model.is_file() else ir}.")
    node = shutil.which("node") or "/opt/homebrew/bin/node"
    job = {"sr": sr, "node": node, "nam_js": str(APP / "tone_nam.mjs"), "worklet": str(APP / "static/nam/nam-worklet.js"),
           "wasm": str(APP / "static/nam/nam-engine.wasm")}
    h, hsr = sf.read(str(ir), dtype="float32", always_2d=True)
    h = h.mean(1)
    if hsr != sr:
        h = signal.resample_poly(h, sr, hsr).astype(np.float32)
    h = h[:int(sr * 0.5)] / (np.sqrt(np.sum(h * h)) + 1e-9)
    drive = 10 ** (spec.get("drive", 0.0) / 20)
    nam = tone_worker.Nam(job, 3)
    try:
        chunks = [takes[i:i + 24] for i in range(0, len(takes), 24)]

        def play(chunk):
            return nam.render(str(model), [t[5] * drive for t in chunk])
        with ThreadPoolExecutor(3) as pool:
            done = 0
            for chunk, outs in zip(chunks, pool.map(play, chunks)):
                for t, y in zip(chunk, outs):
                    t[5] = signal.fftconvolve(y, h)[:len(y)].astype(np.float32)
                done += len(chunk)
                print(f"  {spec['id']}: {done}/{len(takes)}", file=sys.stderr, flush=True)
    finally:
        nam.close()
    return takes


def finished_gain(xs, full_plucks, sr):
    """A finished sound as loud as the others by its body (the first half second of a full pluck),
    as far as nine notes in ten keep their peak under 0.9 — a clean amp's attack is far above its body."""
    import numpy as np
    rms = float(np.median([np.sqrt(np.mean(x[:int(sr * 0.5)] ** 2)) for x in full_plucks]))
    p90 = float(np.percentile([np.abs(x).max() for x in xs], 90))
    return min(0.12 / rms, 0.9 / p90)


def soft_limit(x, knee=0.7):
    """Above the knee the peak bends towards 1 and never reaches it (the few sharpest attacks, like a compressor)."""
    import numpy as np
    a = np.abs(x)
    over = a > knee
    y = x.copy()
    y[over] = np.sign(x[over]) * (knee + (1 - knee) * np.tanh((a[over] - knee) / (1 - knee)))
    return y


def build(src: Path, spec: dict):
    import numpy as np
    import soundfile as sf
    files = pick_files(src, spec["side"], spec["kinds"])
    if not files:
        raise SystemExit(f"V {src} nejsou vzorky Axe Machiny (Samples/{spec['side']}/Plucks).")
    takes = read_takes(files)
    full = [float(np.abs(t[5]).max()) for t in takes if t[0] == "pluck" and t[3] == LAYERS["full"]]
    di_gain = 0.5 / float(np.median(full))                                         # one gain: loud stays loud
    for t in takes:
        t[5] = t[5] * di_gain
    if spec.get("nam"):
        takes = through_amp(takes, spec)
    for t in takes:
        t[5] = fade_out(t[5], t[6], t[0])
    gain = 1.0 if spec["kind"] == "di" else finished_gain([t[5] for t in takes], [t[5] for t in takes if t[0] == "pluck" and t[3] == LAYERS["full"]], takes[0][6])
    out = ROOT / spec["id"]
    out.mkdir(parents=True, exist_ok=True)
    samples, strings, total = [], {}, 0
    for kind, st, midi, v, r, x, sr in takes:
        name = f"{kind}-{midi:02d}-s{st}-v{v}-r{r}.flac"
        tmp = out / (name + ".tmp")
        sf.write(str(tmp), soft_limit(x * gain) if spec["kind"] != "di" else np.clip(x, -0.99, 0.99), sr, format="FLAC", subtype="PCM_16")
        tmp.replace(out / name)
        total += (out / name).stat().st_size
        samples.append([kind, midi, st, v, r, name])
        if kind == "pluck":
            lo, hi = strings.get(st, (midi, midi))
            strings[st] = (min(lo, midi), max(hi, midi))
    about = spec.get("about") or f"čistá Axe Machina přes {Path(spec['nam']).stem} (NAM) a bednu {Path(spec['ir']).stem}"
    m = {"name": spec["name"], "kind": spec["kind"], "tone": spec.get("tone", ""), "about": about, "source": str(src),
         "made_from": f"Samples/{spec['side']}" + (f" → {spec['nam']} → {spec['ir']}" if spec.get("nam") else "") + ", downstrokes",
         "strings": [{"s": s, "open": lo, "top": hi} for s, (lo, hi) in sorted(strings.items())],
         "layers": LAYERS, "samples": samples, "bytes": total}
    tmp = out / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out / "manifest.json")
    print(json.dumps({"set": spec["id"], "samples": len(samples), "mb": round(total / 1e6, 1), "gain": round(gain, 2)}), flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    rest = [a for i, a in enumerate(args) if a != "--only" and (i == 0 or args[i - 1] != "--only")]
    src = Path(rest[0]) if rest else AXE
    for spec in SETS:
        if not only or spec["id"] == only:
            build(src, spec)
