"""Kontakt hosted for Pick (pedalboard, in ~/.local/venvs/pick-kontakt) — run by kontakt.py.

    kontakt_host.py editor <plugin> <state file>            Kontakt's own window; the instrument chosen
                                                           in it is kept in the state file when it closes
    kontakt_host.py render <plugin> <state file> <spec.json> <out.wav>
                                                           notes [[midi, start s, length s, velocity]] played
                                                           through that instrument into a WAV (48 kHz, stereo)

Kontakt never shuts down cleanly inside a host like this (it waits on its library scan), so the
process ends itself with os._exit once its work is written. Samples load in the background after
the state is set: the instrument is given a few seconds of silence first, and the render runs in
blocks, so a sample still streaming in has time.
"""
import json
import os
import sys
import time


def load(path, state=None):
    from pedalboard import load_plugin
    p = load_plugin(path)
    if state and os.path.isfile(state):
        with open(state, "rb") as f:
            p.raw_state = f.read()
    return p


def editor(path, state):
    p = load(path, state)
    p.show_editor()                                  # until the player closes the window
    tmp = state + ".tmp"
    with open(tmp, "wb") as f:
        f.write(p.raw_state)
    os.replace(tmp, state)


def render(path, state, spec, out):
    import mido
    import numpy as np
    from pedalboard.io import AudioFile
    sr = 48000
    with open(spec, encoding="utf-8") as f:
        job = json.load(f)
    p = load(path, state)
    # the samples: Kontakt loads them in the background after the state is set
    t0 = time.time()
    while time.time() - t0 < float(job.get("warm", 6)):
        p([], duration=0.5, sample_rate=sr, num_channels=2, reset=False)
        time.sleep(0.25)
    events = []
    for m, t, d, v in job["notes"]:
        events.append((float(t), mido.Message("note_on", note=int(m), velocity=int(v))))
        events.append((float(t) + float(d), mido.Message("note_off", note=int(m), velocity=0)))
    events.sort(key=lambda e: (e[0], e[1].type == "note_on"))
    msgs = [m.copy(time=t) for t, m in events]
    audio = p(msgs, duration=float(job["length"]), sample_rate=sr, num_channels=2, buffer_size=512, reset=False)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)
    with AudioFile(out, "w", sr, 2) as f:
        f.write(audio)
    print(json.dumps({"peak": round(peak, 4), "seconds": round(audio.shape[1] / sr, 2)}))


def main():
    try:
        cmd = sys.argv[1]
        if cmd == "editor":
            editor(sys.argv[2], sys.argv[3])
        elif cmd == "render":
            render(*sys.argv[2:6])
        else:
            print("neznámý příkaz", file=sys.stderr)
            os._exit(2)
    except Exception as e:                           # noqa: BLE001 — said, and out
        print(f"Kontakt: {e}", file=sys.stderr)
        sys.stderr.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
