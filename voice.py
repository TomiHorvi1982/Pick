"""Voice commands for Pick — recognised on this computer, nothing leaves it.

Vosk's small English model, held to the handful of words Pick understands. A
small model made to choose among few words will turn any speech at all into
those words, so a command only counts when a whole utterance — silence before
and after — is exactly the wake word and one known phrase, heard with
confidence. Talking, singing and the song itself come out as word salad and
are dropped.
"""
import difflib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import wave
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
    ("practice", ["practice"], "practice", "hrající píseň do Steems: rozdělí, ztlumí kytaru, zapne tvou"),
    ("myguitar", ["my guitar"], "my guitar", "tvoje kytara (živý vstup) zap / vyp"),
    ("preset:+", ["next preset"], "next preset", "další preset zesilovače"),
    ("preset:-", ["previous preset"], "previous preset", "předchozí preset zesilovače"),
    ("section:loop", ["loop section"], "loop section", "smyčka na sekci, ve které jsi"),
    ("section:+", ["next section"], "next section", "smyčka na další sekci"),
    ("section:-", ["previous section"], "previous section", "smyčka na předchozí sekci"),
    ("mini", ["mini player"], "mini player", "otevře minipřehrávač"),
]
# The same few words for every track of the mix…
STEMS = {"drums": "bicí", "bass": "basa", "guitar": "kytara", "vocals": "zpěv", "piano": "klavír", "other": "ostatní"}
for _s, _cz in STEMS.items():
    COMMANDS += [
        (f"mute:{_s}", [f"mute {_s}"], f"mute {_s}", f"{_cz}: ztlumit / pustit"),
        (f"solo:{_s}", [f"solo {_s}"], f"solo {_s}", f"{_cz}: sólo zap / vyp"),
        (f"vol:{_s}:+", [f"{_s} louder", f"{_s} up"], f"{_s} louder / up", f"{_cz}: hlasitěji"),
        (f"vol:{_s}:-", [f"{_s} quieter", f"{_s} down"], f"{_s} quieter / down", f"{_cz}: tišeji"),
        (f"pan:{_s}:-1", [f"{_s} left"], f"{_s} left", f"{_cz}: doleva"),
        (f"pan:{_s}:1", [f"{_s} right"], f"{_s} right", f"{_cz}: doprava"),
        (f"pan:{_s}:0", [f"{_s} center"], f"{_s} center", f"{_cz}: na střed"),
    ]
# …and for the parts of a song.
SECTIONS = {"intro": "intro", "verse": "sloka", "chorus": "refrén", "bridge": "mezihra", "solo": "sólo", "outro": "outro"}
for _s, _cz in SECTIONS.items():
    _say = {"outro": "ending"}.get(_s, _s)          # the model has no "outro"
    COMMANDS.append((f"section:{_s}", [f"loop {_say}"], f"loop {_say}", f"smyčka na: {_cz} (další v pořadí)"))
# Said with a name after them — "pick song wait and bleed". The name is not
# among the words this recogniser knows; whisper hears it (see transcribe).
SLOTS = {"song": ("find:song", "song <název>", "najde píseň v knihovně a pustí ji"),
         "band": ("find:band", "band <jméno>", "najde kapelu v knihovně a pustí ji"),
         "practice": ("find:practice", "practice <název>", "píseň do Steems: rozdělí, ztlumí kytaru, zapne tvou")}


def _bare(words):
    return " ".join(w for w in words if w not in FILLERS)


PHRASES = {_bare(p.split()): cid for cid, phrases, _, _ in COMMANDS for p in phrases}


def match(text, confs=None, custom=None, taught=None):
    """The command an utterance is, or None: the wake word, then exactly one phrase.
    One of the user's own comes back as "custom:<id>". `taught` is what the
    recogniser hears when this user says a command (see teach), whole utterances."""
    words = (text or "").split()
    sure = confs if confs and len(confs) == len(words) else None
    if taught and len(words) >= 2 and _bare(words) in taught:
        if sure and min(c for w, c in zip(words, sure) if w not in FILLERS) < MIN_CONF:
            return None
        return taught[_bare(words)]
    # A hard "p" into a microphone is now and then heard twice: one wake word, the surer one.
    while len(words) > 2 and words[0] == WAKE and words[1] == WAKE:
        words = words[1:]
        if sure:
            sure = [max(sure[0], sure[1])] + list(sure[2:])
    if len(words) < 2 or words[0] != WAKE:
        return None
    # A name follows: only the two words before it have to be clear.
    if len(words) > 2 and words[1] in SLOTS and len(words) <= 12:
        if sure and min(sure[:2]) < MIN_CONF:
            return None
        return SLOTS[words[1]][0]
    if sure and min(c for w, c in zip(words, sure) if w not in FILLERS) < MIN_CONF:
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
ACTION_IDS = {cid for cid, _, _, _ in COMMANDS}      # (after the generated ones above)
MAX_STEPS = 12


def valid_step(step):
    """A built-in action, any tempo ("tempo:65"), a pause ("wait:2"), a button of the
    app pressed ("click:<how to find it>"), or a named song or band opened
    ("find:song:<name>")."""
    if step in ACTION_IDS:
        return True
    step = str(step)
    if step.startswith("click:"):
        return 6 < len(step) <= 300
    if step.startswith(("find:song:", "find:band:", "find:practice:")):
        return 1 <= len(step.split(":", 2)[2].strip()) <= 120
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


def delete_custom(cid, path=None, taught_path=None):
    rows = load_custom(path)
    left = [r for r in rows if r["id"] != cid]
    if len(left) == len(rows):
        return False
    _save_custom(left, path)
    forget("custom:" + cid, taught_path)
    return True


# ---- the user's own voice. A command said into the microphone a few times:
# what the recogniser hears then — right or wrong — is kept, and from then on
# counts as that command. An accent the model stumbles over stumbles the same
# way every time.
TAUGHT_PATH = Path.home() / ".local/share/spotdl-helper/voice-taught.json"
MAX_TAUGHT = 8


def load_taught(path=None):
    """{command id: {"heard": [utterances], "whisper": [folded readings]}}"""
    try:
        rows = json.loads(Path(path or TAUGHT_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, dict):
        return {}
    return {k: {"heard": [x for x in v.get("heard", []) if isinstance(x, str)],
                "whisper": [x for x in v.get("whisper", []) if isinstance(x, str)]}
            for k, v in rows.items() if isinstance(v, dict)}


def _save_taught(rows, path=None):
    _save_custom(rows, path or TAUGHT_PATH)


def taught_index(taught, custom=None):
    """Utterance -> command, for the commands that still exist."""
    ok = ACTION_IDS | {"custom:" + c["id"] for c in custom or []}
    return {_bare(h.split()): cid for cid, v in taught.items() if cid in ok for h in v["heard"]}


def teach(cid, heard, whisper="", custom=None, path=None):
    """Keep what was heard when the user said command `cid`. Returns what
    happened; ValueError says, in Czech, why this take is no use."""
    custom = load_custom() if custom is None else custom
    if cid not in ACTION_IDS and not any("custom:" + c["id"] == cid for c in custom):
        raise ValueError("Povel se nenašel.")
    words = (heard or "").lower().split()
    if not words:
        raise ValueError("Nic jsem neslyšel. Zkus to znovu, blíž k mikrofonu.")
    if len(_bare(words).split()) < 2:
        raise ValueError(f"Slyšel jsem jen „{heard}“. Řekni celý povel i se slovem „pick“.")
    if len(words) > 8:
        raise ValueError("To bylo moc dlouhé. Řekni jen povel.")
    taught = load_taught(path)
    others = {k: v for k, v in taught.items() if k != cid}
    was = match(" ".join(words), None, custom, taught_index(others, custom))
    if was and was != cid:
        raise ValueError(f"Slyšel jsem „{heard}“ — tak už se říká jiný povel.")
    mine = taught.setdefault(cid, {"heard": [], "whisper": []})
    out = {"heard": " ".join(words), "whisper": whisper, "added": False}
    if not was and " ".join(words) not in mine["heard"]:
        mine["heard"] = (mine["heard"] + [" ".join(words)])[-MAX_TAUGHT:]
        out["added"] = True
    w = _fold(whisper)                       # the whole of it, wake word and all
    if len(w) >= 4 and w not in mine["whisper"] and interpret(whisper, custom, None, others) is None:
        mine["whisper"] = (mine["whisper"] + [w])[-MAX_TAUGHT:]
        out["added"] = True
    if out["added"]:
        _save_taught(taught, path)
    out["takes"] = len(mine["heard"]) + len(mine["whisper"])
    return out


def forget(cid, path=None, takes_dir=None):
    taught = load_taught(path)
    if taught.pop(cid, None) is not None:
        _save_taught(taught, path)
    for f in _take_files(cid, takes_dir):
        f.unlink(missing_ok=True)
        f.with_suffix(".cmd").unlink(missing_ok=True)


def unteach(cid, heard, path=None):
    """Take back one utterance kept by teach()."""
    taught = load_taught(path)
    if cid in taught and heard in taught[cid]["heard"]:
        taught[cid]["heard"].remove(heard)
        _save_taught(taught, path)


# The takes themselves — the user saying each command, as understood at the
# time. Something learned later must not cost any of them: check_takes().
TAKES_DIR = Path.home() / ".local/share/spotdl-helper/voice/takes"


def _take_files(cid, takes_dir=None):
    stem = "".join(ch if ch.isalnum() else "_" for ch in cid)
    return sorted(Path(takes_dir or TAKES_DIR).glob(stem + "--*.pcm"))


def save_take(cid, pcm, takes_dir=None):
    d = Path(takes_dir or TAKES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    old = _take_files(cid, d)
    for f in old[:max(0, len(old) - MAX_TAUGHT + 1)]:
        f.unlink(missing_ok=True)
        f.with_suffix(".cmd").unlink(missing_ok=True)
    stem = "".join(ch if ch.isalnum() else "_" for ch in cid)
    n = max([int(f.stem.rsplit("--", 1)[1]) for f in old] or [0]) + 1
    (d / f"{stem}--{n:04d}.pcm").write_bytes(pcm)
    (d / f"{stem}--{n:04d}.cmd").write_text(cid, encoding="utf-8")


def broken_takes(custom=None, taught=None, takes_dir=None):
    """Commands whose kept takes are no longer understood."""
    bad = []
    for note in sorted(Path(takes_dir or TAKES_DIR).glob("*.cmd")):
        cid, pcm = note.read_text(encoding="utf-8"), note.with_suffix(".pcm")
        if pcm.exists() and cid not in bad and Listener(custom, taught).command(pcm.read_bytes()) != cid:
            bad.append(cid)
    return bad


def help_rows():
    return ([{"id": cid, "say": f"{WAKE} {say}", "does": does} for cid, say, does in SLOTS.values()]
            + [{"id": cid, "say": f"{WAKE} {say}", "does": does} for cid, _, say, does in COMMANDS])


# ---- names: whisper hears them, the library says which one was meant
WHISPER_BIN = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = Path(os.environ.get(
    "PICK_WHISPER_MODEL", Path.home() / ".local/share/spotdl-helper/voice/ggml-large-v3-turbo-q5_0.bin"))


def names_available():
    if not Path(WHISPER_BIN).exists():
        return "Chybí whisper-cli (brew install whisper-cpp)."
    if not WHISPER_MODEL.exists():
        return f"Chybí model pro jména ({WHISPER_MODEL})."
    return None


def transcribe(pcm, hint=""):
    """What was said in this stretch of 16 kHz mono s16le, as text."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "u.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes(b"\0" * RATE + pcm + b"\0" * RATE)      # whisper wants a little room
        cmd = [WHISPER_BIN, "-m", str(WHISPER_MODEL), "-l", "en", "-np", "-nt", "-f", str(wav),
               # Whisper leans on what it has "just heard": sentences of the very shape
               # we expect, and a few of the library's bands for their spelling.
               "--prompt", ("Pick song Wait and Bleed. Pick band Sepultura. Pick practice Duality. " + hint)[:400]]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return " ".join(out.stdout.split())


def _fold(text):
    """Letters only, the way they sound: no accents, no case, k for c, v for w."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = "".join(ch if ch.isalnum() else " " for ch in t)
    t = t.replace("ck", "k").replace("c", "k").replace("w", "v").replace("y", "i").replace("ph", "f")
    out = []
    for ch in " ".join(t.split()):
        if not out or out[-1] != ch or ch == " ":
            out.append(ch)
    return "".join(out)


_WAKE_LIKE = {"pick", "pik", "peak", "pic", "big", "picks"}
_SLOT_LIKE = {"song": "song", "songs": "song", "band": "band", "van": "band", "bend": "band", "bands": "band", "banned": "band",
              "practice": "practice", "practise": "practice", "practices": "practice"}


def _said(text):
    words = "".join(ch if ch.isalnum() or ch in "'’" else " " for ch in (text or "")).split()
    while words and words[0].lower() in _WAKE_LIKE:
        words = words[1:]
    return words


def spoken_name(text):
    """The name in "Pick song, wait and bleed." — what follows the two command words."""
    words = _said(text)
    if words and words[0].lower() in _SLOT_LIKE:
        words = words[1:]
    return " ".join(words)


def interpret(text, custom=None, kind=None, taught=None):
    """What whisper's reading of an unclear utterance asks for: ("command", id),
    ("find", kind, name), or None. The second opinion for words the small
    recogniser could not place — a name, or a command said with an accent."""
    words = _said(text)
    if not words:
        return None
    cmd = match(" ".join([WAKE] + [w.lower() for w in words]), None, custom)
    if cmd and not cmd.startswith("find:"):
        return ("command", cmd)
    heard_kind = _SLOT_LIKE.get(words[0].lower())
    if heard_kind and len(words) > 1:
        return ("find", heard_kind, " ".join(words[1:]))
    # The way this user's voice reads, as they taught it.
    said, ok = _fold(text), ACTION_IDS | {"custom:" + c["id"] for c in custom or []}
    for cid, v in (taught or {}).items():
        if cid in ok and any(difflib.SequenceMatcher(None, said, w).ratio() >= 0.86 for w in v["whisper"]):
            return ("command", cid)
    # The small recogniser was sure which of the three it was; the rest is the name.
    if kind and not heard_kind:
        return ("find", kind, " ".join(words))
    return None


def best_match(query, names):
    """Index of the name meant, and how sure (0–1); (None, 0) when nothing is close."""
    q = _fold(query)
    if len(q) < 2:
        return None, 0.0
    best, score = None, 0.0
    qt = set(q.split())
    for i, name in enumerate(names):
        n = _fold(name)
        if not n:
            continue
        r = difflib.SequenceMatcher(None, q, n).ratio()
        nt = set(n.split())
        both = len(qt & nt) / max(len(qt), len(nt))          # the same words, in any order
        sc = max(r, both)
        if sc > score:
            best, score = i, sc
    return (best, score) if score >= 0.62 else (None, score)


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

    def __init__(self, custom=None, taught=None):
        import vosk
        self.custom = custom if custom is not None else load_custom()
        self.taught = load_taught() if taught is None else taught
        self.index = taught_index(self.taught, self.custom)
        vocab = sorted({w for _, phrases, _, _ in COMMANDS for p in phrases for w in p.split()} | {WAKE} | FILLERS
                       | set(SLOTS) | {w for c in self.custom for w in c["phrase"].split()}
                       | {w for h in self.index for w in h.split()})
        self.audio = bytearray()        # the last seconds heard, for a name said after a command
        self.fed = 0
        self.rec = vosk.KaldiRecognizer(_load(), RATE, json.dumps(vocab + ["[unk]"], ensure_ascii=False))
        self.rec.SetWords(True)
        self.lock = threading.Lock()

    def feed(self, pcm):
        """16 kHz mono s16le. Returns {"heard", "command"} when an utterance just ended."""
        with self.lock:
            self.audio += pcm
            self.fed += len(pcm)
            del self.audio[:max(0, len(self.audio) - RATE * 2 * 14)]
            if not self.rec.AcceptWaveform(pcm):
                return {}
            res = json.loads(self.rec.Result())
            heard_from = self.fed - len(self.audio)
            snapshot = bytes(self.audio)
        text = res.get("text", "").strip()
        if not text:
            return {}
        confs = [w.get("conf", 0) for w in res.get("result", [])]
        cmd = match(text, confs, self.custom, self.index)
        if cmd and cmd.startswith("custom:"):
            c = next(x for x in self.custom if x["id"] == cmd[7:])
            return {"heard": text, "command": cmd, "name": c["name"], "steps": c["steps"]}
        words = text.split()
        wakes = [c for w, c in zip(words[:2], confs[:2]) if w == WAKE]
        # "pick" was clear and what followed was not a command as heard here —
        # unknown words, or known ones that add up to nothing (an accent).
        unclear = (cmd is None and words[0] == WAKE and 2 <= len(words) <= 12
                   and wakes and max(wakes) >= MIN_CONF)
        if unclear or (cmd and cmd.startswith("find:")):
            # Something was asked of Pick that these few words don't cover — a
            # name, most likely. The utterance itself goes on to whisper.
            start = int(max(0.0, res["result"][0].get("start", 0) - 0.3) * RATE) * 2 - heard_from
            return {"heard": text, "command": cmd or "ask", "pcm": snapshot[max(0, start):]}
        return {"heard": text, "command": cmd, "steps": [cmd] if cmd else None}


    def command(self, pcm):
        """The command in one finished recording (a take, when teaching), or None."""
        got, pcm = None, b"\0" * RATE + pcm + b"\0" * RATE * 3
        for i in range(0, len(pcm), 8000):
            got = self.feed(pcm[i:i + 8000]).get("command") or got
        return got


def hear(pcm):
    """One recording as the model hears it with every word it knows allowed —
    not held to the command words. This is what teach() keeps."""
    import vosk
    rec = vosk.KaldiRecognizer(_load(), RATE)
    rec.AcceptWaveform(b"\0" * RATE + pcm + b"\0" * RATE)
    return json.loads(rec.FinalResult()).get("text", "").strip()
