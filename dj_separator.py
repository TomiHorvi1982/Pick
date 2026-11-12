#!/usr/bin/env python3
"""Pick DJ — live stem separation, one window at a time.

Runs under Demucs' own interpreter (the one with torch; see
server._demucs_interpreter) as a long-lived process, so htdemucs_6s is loaded
once and each window costs only the model run (~0.5 s for 7.8 s of audio on
Apple Silicon's GPU). djlive.py starts it and talks to it over stdin/stdout.

The song is the 44.1 kHz 16-bit stereo WAV djlive made with ffmpeg — the same
file the page plays as the mix and the beat grid is detected on, so all three
line up to the sample.

Windows sit on a fixed grid: window k is the song's frames
[k*CORE, (k+1)*CORE). The model sees CONTEXT frames more on each side (zeros
past the ends) and only the middle is kept, where it had context both ways.

Protocol, one JSON line per request on stdin:
  {"op": "window", "path": "<mix.wav>", "k": 3}
answered with one JSON line
  {"ok": true, "k": 3, "start": <frame>, "frames": <n>, "bytes": <n*24>}
followed by exactly "bytes" bytes: int16 little-endian, planar in the order
STEMS × (left, right) × frames. Errors: {"ok": false, "error": "..."}.
On start it prints {"ready": true, "device": "...", "stems": [...], "core": N, "context": N}.
"""
import json
import sys
import wave
from collections import OrderedDict

import numpy as np
import torch
from demucs.pretrained import get_model

STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]   # the order djlive and the page use
RATE = 44100
CONTEXT = int(0.7 * RATE)


def load_model():
    bag = get_model("htdemucs_6s")
    model = bag.models[0].eval()
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    return model, device


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 2 or w.getsampwidth() != 2 or w.getframerate() != RATE:
            raise ValueError("expected 44.1 kHz 16-bit stereo")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return torch.from_numpy(data.reshape(-1, 2).T.astype(np.float32) / 32768.0)


def main():
    torch.set_grad_enabled(False)
    model, device = load_model()
    length = int(model.segment * model.samplerate)
    core = length - 2 * CONTEXT
    order = [model.sources.index(s) for s in STEMS]
    songs = OrderedDict()                      # path -> [2, N] float tensor, the two most recent
    out = sys.stdout.buffer

    def reply(obj, payload=b""):
        out.write((json.dumps(obj) + "\n").encode())
        if payload:
            out.write(payload)
        out.flush()

    reply({"ready": True, "device": device, "stems": STEMS, "core": core, "context": CONTEXT, "rate": RATE})
    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req.get("op") != "window":
                reply({"ok": False, "error": "unknown op"})
                continue
            path, k = req["path"], int(req["k"])
            if path not in songs:
                songs[path] = read_wav(path)
                while len(songs) > 2:
                    songs.popitem(last=False)
            songs.move_to_end(path)
            song = songs[path]
            n_song = song.shape[1]
            start = k * core
            if k < 0 or start >= n_song:
                reply({"ok": False, "error": "window out of range"})
                continue
            frames = min(core, n_song - start)
            seg = torch.zeros(2, length)
            s0 = start - CONTEXT
            a, b = max(0, s0), min(n_song, s0 + length)
            seg[:, a - s0: b - s0] = song[:, a:b]
            res = model(seg[None].to(device))[0].float().cpu()      # [sources, 2, length]
            res = res[order, :, CONTEXT: CONTEXT + frames]
            pcm = (res.clamp(-1, 1) * 32767).round().to(torch.int16).numpy().astype("<i2")
            payload = pcm.tobytes()
            reply({"ok": True, "k": k, "start": start, "frames": frames, "bytes": len(payload)}, payload)
        except Exception as exc:                                    # one bad request must not end the worker
            reply({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
