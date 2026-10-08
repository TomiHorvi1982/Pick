"""Voice commands for Pick — recognised on this computer, nothing leaves it.

Vosk's small English model, held to the handful of words Pick understands. A
small model made to choose among few words will turn any speech at all into
those words, so a command only counts when a whole utterance — silence before
and after — is exactly the wake word and one known phrase, heard with
confidence. Talking, singing and the song itself come out as word salad and
are dropped.
"""
import json
import os
import threading
from pathlib import Path

RATE = 16000
WAKE = "pick"
MIN_CONF = 0.8
# Little words the small model drops or adds at will; they carry no meaning here.
FILLERS = {"a", "the", "it", "to"}
MODEL_DIR = Path(os.environ.get(
    "PICK_VOICE_MODEL",
    Path.home() / ".local/share/spotdl-helper/voice/vosk-model-small-en-us-0.15"))

# What to say (after the wake word) -> what it does. `say` is what the help shows.
COMMANDS = [
    ("play", ["play", "start"], "play / start", "přehrát"),
    ("pause", ["stop", "pause"], "stop / pause", "zastavit"),
    ("rewind", ["from the start", "again"], "from the start / again", "na začátek (nebo začátek smyčky)"),
    ("loop", ["loop"], "loop", "smyčka zap / vyp"),
    ("slower", ["slower"], "slower", "tempo o 5 % dolů"),
    ("faster", ["faster"], "faster", "tempo o 5 % nahoru"),
    ("tempo:100", ["normal speed", "hundred percent"], "normal speed / hundred percent", "původní tempo"),
    ("tempo:50", ["fifty percent"], "fifty percent", "tempo 50 %"),
    ("tempo:60", ["sixty percent"], "sixty percent", "tempo 60 %"),
    ("tempo:70", ["seventy percent"], "seventy percent", "tempo 70 %"),
    ("tempo:80", ["eighty percent"], "eighty percent", "tempo 80 %"),
    ("tempo:90", ["ninety percent"], "ninety percent", "tempo 90 %"),
    ("metronome", ["metronome", "click"], "metronome / click", "metronom zap / vyp"),
    ("record", ["record"], "record", "nahrávání kytary zap / vyp"),
    ("next", ["next"], "next", "další píseň"),
    ("prev", ["previous"], "previous", "předchozí píseň"),
    ("louder", ["louder"], "louder", "hlasitost nahoru"),
    ("quieter", ["quieter"], "quieter", "hlasitost dolů"),
    ("tuner", ["tuner"], "tuner", "otevřít ladičku"),
    ("song", ["a song", "song"], "a song", "Pick vybere a pustí náhodnou píseň z knihovny"),
    ("playlist", ["a playlist", "playlist"], "a playlist", "otevře playlisty"),
]


def _bare(words):
    return " ".join(w for w in words if w not in FILLERS)


PHRASES = {_bare(p.split()): cid for cid, phrases, _, _ in COMMANDS for p in phrases}


def match(text, confs=None, custom=None):
    """The command an utterance is, or None: the wake word, then exactly one phrase.
    One of the user's own comes back as "custom:<id>"."""
    words = (text or "").split()
    if len(words) < 2 or words[0] != WAKE:
        return None
    if confs and len(confs) == len(words) and min(c for w, c in zip(words, confs) if w not in FILLERS) < MIN_CONF:
        return None
    said = _bare(words[1:])
    if said in PHRASES:
        return PHRASES[said]
    for c in custom or []:
        if _bare(c["phrase"].split()) == said:
            return "custom:" + c["id"]
    return None


# ---- the user's own commands: a phrase of their choosing that runs one or
# more of the actions above, in order. Kept beside the app's other settings.
CUSTOM_PATH = Path.home() / ".local/share/spotdl-helper/voice-commands.json"
ACTION_IDS = {cid for cid, _, _, _ in COMMANDS}
MAX_STEPS = 12


def valid_step(step):
    """A built-in action, any tempo ("tempo:65"), or a pause between steps ("wait:2")."""
    if step in ACTION_IDS:
        return True
    kind, _, n = str(step).partition(":")
    if not n.isdigit():
        return False
    return (kind == "tempo" and 25 <= int(n) <= 150) or (kind == "wait" and 1 <= int(n) <= 30)


def load_custom(path=None):
    try:
        rows = json.loads(Path(path or CUSTOM_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id") and r.get("phrase")
            and all(valid_step(x) for x in r.get("steps", []))]


def _save_custom(rows, path=None):
    path = Path(path or CUSTOM_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def unknown_words(words):
    """Words the model has never heard of — it could not recognise a phrase with them."""
    model = _load()
    return [w for w in words if model.vosk_model_find_word(w) < 0]


def add_custom(name, phrase, steps, path=None, known=None):
    """Save a command; ValueError says, in Czech, what is wrong with it."""
    words = [w for w in "".join(ch if ch.isalpha() or ch == "'" else " " for ch in (phrase or "").lower()).split()]
    if words and words[0] == WAKE:
        words = words[1:]
    if not _bare(words):
        raise ValueError("Napiš, co se má říct (anglicky, po slově „pick“).")
    if len(words) > 6:
        raise ValueError("Povel může mít nejvýš šest slov.")
    missing = (known or unknown_words)(words)
    if missing:
        raise ValueError("Tahle slova model nezná, zkus jiná: " + ", ".join(missing))
    steps = [str(x) for x in (steps or [])]
    if not steps or len(steps) > MAX_STEPS or not all(valid_step(x) for x in steps):
        raise ValueError(f"Povel potřebuje 1 až {MAX_STEPS} platných kroků.")
    rows = load_custom(path)
    said = _bare(words)
    if said in PHRASES or any(_bare(r["phrase"].split()) == said for r in rows):
        raise ValueError("Takhle už se jmenuje jiný povel.")
    import uuid
    row = {"id": uuid.uuid4().hex[:8], "name": (name or "").strip()[:60] or " ".join(words),
           "phrase": " ".join(words), "steps": steps}
    _save_custom(rows + [row], path)
    return row


def delete_custom(cid, path=None):
    rows = load_custom(path)
    left = [r for r in rows if r["id"] != cid]
    if len(left) == len(rows):
        return False
    _save_custom(left, path)
    return True


def help_rows():
    return [{"id": cid, "say": f"{WAKE} {say}", "does": does} for cid, _, say, does in COMMANDS]


_model = None
_model_lock = threading.Lock()


def available():
    """Why voice can't run here, or None when it can."""
    try:
        import vosk  # noqa: F401
    except Exception:
        return "Chybí knihovna vosk."
    if not (MODEL_DIR / "am").exists():
        return f"Chybí model řeči ({MODEL_DIR})."
    return None


def _load():
    global _model
    with _model_lock:
        if _model is None:
            import vosk
            vosk.SetLogLevel(-1)
            _model = vosk.Model(str(MODEL_DIR))
        return _model


class Listener:
    """One stream of microphone audio in, commands out."""

    def __init__(self, custom=None):
        import vosk
        self.custom = custom if custom is not None else load_custom()
        vocab = sorted({w for _, phrases, _, _ in COMMANDS for p in phrases for w in p.split()} | {WAKE} | FILLERS
                       | {w for c in self.custom for w in c["phrase"].split()})
        self.rec = vosk.KaldiRecognizer(_load(), RATE, json.dumps(vocab + ["[unk]"], ensure_ascii=False))
        self.rec.SetWords(True)
        self.lock = threading.Lock()

    def feed(self, pcm):
        """16 kHz mono s16le. Returns {"heard", "command"} when an utterance just ended."""
        with self.lock:
            if not self.rec.AcceptWaveform(pcm):
                return {}
            res = json.loads(self.rec.Result())
        text = res.get("text", "").strip()
        if not text:
            return {}
        confs = [w.get("conf", 0) for w in res.get("result", [])]
        cmd = match(text, confs, self.custom)
        if cmd and cmd.startswith("custom:"):
            c = next(x for x in self.custom if x["id"] == cmd[7:])
            return {"heard": text, "command": cmd, "name": c["name"], "steps": c["steps"]}
        return {"heard": text, "command": cmd, "steps": [cmd] if cmd else None}
