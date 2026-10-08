"""Audio → notes, with Spotify's basic-pitch (polyphonic: chords come out as
chords). Run by the server with the interpreter of its own small venv
(~/.local/venvs/pick-midi: onnxruntime, librosa, basic-pitch) — nothing of
it is imported by the server itself.

    python audio2midi.py <audio file>      →  JSON on stdout: {"notes": [{m, t, d, v}]}
"""
import json
import logging
import sys


def main() -> int:
    logging.disable(logging.WARNING)
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):           # it prints what it is doing
        _, _, events = predict(sys.argv[1], ICASSP_2022_MODEL_PATH, onset_threshold=0.5, frame_threshold=0.3,
                               minimum_note_length=80, multiple_pitch_bends=False, melodia_trick=True)
    notes = [{"m": int(p), "t": round(float(a), 4), "d": round(max(0.04, float(b) - float(a)), 4), "v": round(min(1.0, max(0.15, float(amp) * 1.4)), 3)}
             for a, b, p, amp, _ in events if 21 <= int(p) <= 108]
    notes.sort(key=lambda n: (n["t"], n["m"]))
    json.dump({"notes": notes}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
