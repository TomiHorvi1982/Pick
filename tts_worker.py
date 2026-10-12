"""Pick's own cloned voice: OmniVoice, kept warm in a process of its own.

Run by the interpreter of the pick-tts environment (torch lives there, not in
the server's). One request a line on stdin, one answer a line on stdout:

    {"text": "...", "lang": "en", "ref_audio": ".../ref.wav", "ref_text": "...", "out": ".../x.wav", "steps": 16,
     "instruct": "whisper", "speed": 1.1}            (the last two only when a manner of speaking asks for them)
    → {"ok": true, "seconds": 3.9}   or   {"ok": false, "error": "..."}

The model is loaded on the first line asked for; a voice's reference is
worked out once and kept. Left alone for a while, the process ends by itself
and gives its memory back — the server starts another when it is needed.
"""
import json
import os
import select
import sys
import warnings

warnings.filterwarnings("ignore")
IDLE = 600                      # seconds without work before the model is let go

real_out = sys.stdout
sys.stdout = sys.stderr         # the libraries chat on stdout; the answers must be the only thing there


def say(obj):
    real_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    real_out.flush()


def main():
    model, config, prompts = None, None, {}
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], IDLE)
        if not ready:
            return
        line = sys.stdin.readline()
        if not line:
            return
        try:
            req = json.loads(line)
            if model is None:
                import torch
                from omnivoice import OmniVoice
                from omnivoice.models.omnivoice import OmniVoiceGenerationConfig
                device = "mps" if torch.backends.mps.is_available() else "cpu"
                model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=device,
                                                  dtype=torch.float16 if device != "cpu" else torch.float32)
                config = OmniVoiceGenerationConfig
            key = (req["ref_audio"], os.path.getmtime(req["ref_audio"]))
            if key not in prompts:
                prompts[key] = model.create_voice_clone_prompt(ref_audio=req["ref_audio"], ref_text=req["ref_text"])
            import soundfile as sf
            audio = model.generate(text=req["text"], language=req.get("lang") or None, voice_clone_prompt=prompts[key],
                                   instruct=req.get("instruct") or None, speed=req.get("speed") or None,
                                   generation_config=config(num_step=int(req.get("steps") or 16)))[0]
            tmp = req["out"] + ".part"
            sf.write(tmp, audio, 24000, format="WAV", subtype="PCM_16")
            os.replace(tmp, req["out"])
            say({"ok": True, "seconds": round(len(audio) / 24000, 2)})
        except Exception as e:                              # noqa: BLE001 — told to the server, the next line still gets its turn
            say({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})


if __name__ == "__main__":
    main()
