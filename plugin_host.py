"""Audio plugins (AU / VST3: Neural DSP and the rest) hosted for Pick — pedalboard, in
~/.local/venvs/pick-kontakt — run by plugins.py, one process per job:

    plugin_host.py devices                              the sound devices, in and out, as JSON
    plugin_host.py probe <plugin>                       its name and kind, as JSON (the scan runs this per file)
    plugin_host.py editor <plugin> <state>              the plugin's own window; its settings are kept in the
                                                        state file when it closes
    plugin_host.py render <chain.json> <in> <out.wav>   a recording through the chain of plugins
    plugin_host.py live <chain.json> <input device> <channel 1|2> <output device> [<edit index>]
                                                        the guitar from the interface through the chain, live,
                                                        into the output (BlackHole: Pick's tunnel takes it from
                                                        there); with an index, that plugin's window opens too and
                                                        what is set in it is kept when it closes

chain.json: [{"path": …, "state": state file or null, "bypass": false}, …]
Plugins do not always shut down cleanly inside a host like this; the process ends itself with
os._exit once its work is written.
"""
import json
import os
import signal
import sys
import threading
import time


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load(path, state=None):
    from pedalboard import load_plugin
    p = load_plugin(path)
    if state and os.path.isfile(state):
        try:
            with open(state, "rb") as f:
                p.raw_state = f.read()
        except Exception:                                  # noqa: BLE001 — a state from another version: defaults
            pass
    return p


def keep(p, state):
    if not state:
        return
    tmp = state + ".tmp"
    with open(tmp, "wb") as f:
        f.write(p.raw_state)
    os.replace(tmp, state)


def board(chain):
    from pedalboard import Pedalboard
    plugins = []
    for c in chain:
        if c.get("bypass"):
            continue
        plugins.append(load(c["path"], c.get("state")))
    return Pedalboard(plugins)


def probe(path):
    t = time.time()
    p = load(path)
    out({"ok": True, "name": p.name, "instrument": bool(p.is_instrument), "params": len(p.parameters),
         "seconds": round(time.time() - t, 1)})


def editor(path, state):
    p = load(path, state)
    p.show_editor()                                         # until the player closes the window
    keep(p, state)
    out({"ok": True})


def render(chain_file, src, dst):
    from pedalboard.io import AudioFile
    chain = json.load(open(chain_file))
    fx = board(chain)
    with AudioFile(src) as f:
        sr, audio = f.samplerate, f.read(f.frames)
    tail = int(sr * 2)                                      # reverbs and delays ring on after the end
    import numpy as np
    audio = np.concatenate([audio, np.zeros((audio.shape[0], tail), dtype=audio.dtype)], axis=1)
    y = fx(audio, sr)
    if y.shape[0] == 1:
        y = np.repeat(y, 2, axis=0)
    peak = float(np.abs(y).max()) if y.size else 0.0
    if peak > 0.98:                                         # never written clipped
        y = y * (0.98 / peak)
    with AudioFile(dst, "w", sr, y.shape[0]) as f:
        f.write(y)
    out({"ok": True, "seconds": round(audio.shape[1] / sr, 1)})


def live(chain_file, input_name, channel, output_name, edit=None):
    import numpy as np
    from pedalboard.io import AudioStream
    chain = json.load(open(chain_file))
    active = [c for c in chain if not c.get("bypass")]
    plugins = [load(c["path"], c.get("state")) for c in active]
    from pedalboard import Pedalboard
    fx = Pedalboard(plugins)
    ch = 1 if str(channel) == "2" else 0
    block = 128
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    with AudioStream(input_name, output_name, num_input_channels=2, num_output_channels=2,
                     buffer_size=block, sample_rate=48000) as stream:
        stream.ignore_dropped_input = True
        sr = stream.sample_rate

        def run():
            while not stop.is_set():
                x = stream.read(block)                      # (channels, n): the guitar is one of the two inputs
                if x.shape[-1] == 0:
                    time.sleep(0.001)
                    continue
                mono = x[min(ch, x.shape[0] - 1):min(ch, x.shape[0] - 1) + 1]
                y = fx(mono, sr, reset=False)
                if y.shape[0] == 1:
                    y = np.repeat(y, 2, axis=0)
                stream.write(np.clip(y, -1.0, 1.0).astype(np.float32), sr)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        out({"ok": True, "live": True, "sample_rate": sr, "block": block})
        if edit is not None and 0 <= edit < len(active):
            plugins[edit].show_editor()                     # sounding live while it is turned
            keep(plugins[edit], active[edit].get("state"))
            out({"ok": True, "kept": edit})
        while not stop.is_set():
            time.sleep(0.2)
    out({"ok": True, "live": False})


def devices():
    from pedalboard.io import AudioStream
    out({"ok": True, "inputs": list(AudioStream.input_device_names), "outputs": list(AudioStream.output_device_names)})


def main(argv):
    cmd = argv[1]
    try:
        if cmd == "devices":
            devices()
        elif cmd == "probe":
            probe(argv[2])
        elif cmd == "editor":
            editor(argv[2], argv[3])
        elif cmd == "render":
            render(argv[2], argv[3], argv[4])
        elif cmd == "live":
            live(argv[2], argv[3], argv[4], argv[5], int(argv[6]) if len(argv) > 6 else None)
        else:
            out({"ok": False, "error": f"neznámý příkaz {cmd}"})
    except Exception as e:                                  # noqa: BLE001 — told to the server, not a traceback
        out({"ok": False, "error": f"{type(e).__name__}: {e}"})
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
