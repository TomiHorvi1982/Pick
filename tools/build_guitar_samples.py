"""Makes a set of recorded guitar notes for Pick (guitar_samples.py) from Soundiron's Axe Machina.

    ~/.local/venvs/pick-tone/bin/python tools/build_guitar_samples.py [<Axe Machina folder>]

Its DI samples ("a"), downstrokes: each note on each string, two strengths, two takes, plucked and
palm muted — trimmed to the pick and a few seconds, one gain for the whole set, FLAC.
Axe Machina's names are Kontakt's: C3 is MIDI 60, so "D1" is 38.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path.home() / ".local" / "share" / "spotdl-helper" / "guitar-samples"
AXE = Path.home() / "Documents" / "Native Instruments" / "Libraries" / "Axe Machina [Soundiron]"
NOTE = re.compile(r"^n\d+_([A-G]#?)(-?\d)$")
PCS = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
KINDS = {"pluck": ("Plucks", "pluck", 3.5, 0.3), "mute": ("Pluck Mutes", "mute", 1.0, 0.12)}  # folder, name, seconds, fade
LAYERS = (2, 3)        # Axe Machina's v1..v3: 2 soft, 3 full
ROUNDS = (1, 2)


def midi_of(folder: str):
    """'n06_A#0' -> 34 (Kontakt names: C3 = 60)."""
    m = NOTE.match(folder)
    return 12 * (int(m.group(2)) + 2) + PCS[m.group(1)] if m else None


def pick_files(src: Path) -> list:
    """[(kind, string, midi, layer, round, wav)] — Axe Machina's DI notes, downstrokes."""
    out = []
    for kind, (folder, word, _, _) in KINDS.items():
        for sdir in sorted((src / "Samples" / "a" / folder).glob("s[1-7]")):
            st = int(sdir.name[1:])
            for ndir in sorted(sdir.iterdir()):
                midi = midi_of(ndir.name)
                if midi is None:
                    continue
                for v in LAYERS:
                    for r in ROUNDS:
                        hit = sorted((ndir / "dn").glob(f"*_{word}_s{st}_*_v{v}_dn_r{r}_a.wav"))
                        if hit:
                            out.append((kind, st, midi, v, r, hit[0]))
    return out


def build(src: Path = AXE, set_id: str = "axe-machina-di"):
    import numpy as np
    import soundfile as sf
    files = pick_files(src)
    if not files:
        raise SystemExit(f"V {src} nejsou vzorky Axe Machiny (Samples/a/Plucks).")
    takes = []
    for kind, st, midi, v, r, wav in files:
        info = sf.info(str(wav))
        sr = info.samplerate
        x, _ = sf.read(str(wav), frames=int(sr * (KINDS[kind][2] + 0.5)), dtype="float32", always_2d=True)
        x = x.mean(1)
        peak = float(np.abs(x).max()) or 1.0
        on = max(0, int(np.argmax(np.abs(x) > 0.05 * peak)) - int(0.002 * sr))     # 2 ms before the pick
        x = x[on:on + int(sr * KINDS[kind][2])].copy()
        fade = min(len(x), int(sr * KINDS[kind][3]))
        x[len(x) - fade:] *= np.linspace(1, 0, fade, dtype=np.float32) ** 2
        takes.append((kind, st, midi, v, r, x, sr))
    full = [float(np.abs(t[5]).max()) for t in takes if t[0] == "pluck" and t[3] == max(LAYERS)]
    gain = 0.5 / float(np.median(full))                                              # one gain: loud stays loud
    out = ROOT / set_id
    out.mkdir(parents=True, exist_ok=True)
    samples, strings, total = [], {}, 0
    for kind, st, midi, v, r, x, sr in takes:
        name = f"{kind}-{midi:02d}-s{st}-v{v}-r{r}.flac"
        tmp = out / (name + ".tmp")
        sf.write(str(tmp), np.clip(x * gain, -0.99, 0.99), sr, format="FLAC", subtype="PCM_16")
        tmp.replace(out / name)
        total += (out / name).stat().st_size
        samples.append([kind, midi, st, v, r, name])
        if kind == "pluck":
            lo, hi = strings.get(st, (midi, midi))
            strings[st] = (min(lo, midi), max(hi, midi))
    m = {"name": "Axe Machina (DI)", "source": str(src), "made_from": "Samples/a (DI), downstrokes",
         "strings": [{"s": s, "open": lo, "top": hi} for s, (lo, hi) in sorted(strings.items())],
         "layers": {"soft": min(LAYERS), "full": max(LAYERS)}, "samples": samples, "bytes": total}
    tmp = out / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out / "manifest.json")
    print(json.dumps({"set": set_id, "samples": len(samples), "mb": round(total / 1e6, 1), "gain": round(gain, 2),
                      "strings": m["strings"]}))


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else AXE)
