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
    ("next", ["next", "next song"], "next / next song", "další píseň"),
    ("prev", ["previous", "previous song"], "previous / previous song", "předchozí píseň"),
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
    # Said again and again — "pick up", "pick up": three decibels at a time.
    ("up:1", ["up", "volume up"], "up", "hlasitost +3 dB"),
    ("up:2", ["up up"], "up up", "hlasitost +6 dB"),
    ("down:1", ["down", "volume down"], "down", "hlasitost −3 dB"),
    ("down:2", ["down down"], "down down", "hlasitost −6 dB"),
    # "play" and "stop" go by what is on and what is in front; these name it.
    ("mixplay", ["play mix"], "play mix", "spustit Steems (mix), ať je vpředu cokoli"),
    ("mixpause", ["stop mix"], "stop mix", "zastavit Steems (mix)"),
    ("pbplay", ["play player"], "play player", "spustit spodní přehrávač"),
    ("pbpause", ["stop player"], "stop player", "zastavit spodní přehrávač"),
    ("gtr:on", ["guitar on"], "guitar on", "originální kytara zpět do hraní (Steems, minipřehrávač)"),
    ("gtr:off", ["guitar off"], "guitar off", "originální kytara pryč, hraje se dál (Steems, minipřehrávač)"),
    ("tomini", ["play in mini", "open in mini"], "play in mini / open in mini", "hrající píseň do minipřehrávače bez kytary (rozdělí ji, když není ve Steems)"),
    ("tabs", ["tabs", "tab"], "tabs / tab", "tabulatura k hrající písni zap / vyp"),
    ("lyrics", ["lyrics", "text"], "lyrics / text", "text hrající písně zap / vyp"),
    ("steems", ["mixer", "stems"], "mixer / stems", "otevře Steems se seznamem rozdělených písní"),
    # For a list on screen — in the mini player, what a search found.
    ("page:+", ["next page", "page down"], "next page / page down", "o stránku dál"),
    ("page:-", ["previous page", "page up"], "previous page / page up", "o stránku zpět"),
    ("helppage:+", ["help down", "help page down"], "help down", "nabídka povelů o stránku dál"),
    ("helppage:-", ["help up", "help page up"], "help up", "nabídka povelů o stránku zpět"),
    # The list in front, put in order.
    ("sort:artist", ["sort by band", "sort by artist", "sort bands", "sort artists"], "sort by band / artist", "seřadí seznam podle kapely"),
    ("sort:album", ["sort by album", "sort albums"], "sort by album", "seřadí seznam podle alba"),
    ("sort:title", ["sort by title", "sort by name", "sort by song", "sort songs", "sort titles"], "sort by title / name", "seřadí seznam podle názvu písně"),
    ("playall", ["play all", "play playlist"], "play all", "přehrát celý nalezený seznam nebo otevřený playlist (minipřehrávač)"),
    ("back", ["back"], "back", "zpět z playlistu na výsledky (minipřehrávač)"),
    # The lists of the mini player's search; "pick play <name>" chooses from the one that is on.
    # "pick show me …" walks through what is already here.
    ("list:steems", ["show me stems", "show stems", "stems list"], "show me stems / stems list", "ukáže Steems — písně připravené na mix"),
    ("list:favs", ["show me favorites", "show favorites", "favorites"], "show me favorites", "ukáže oblíbené"),
    ("list:bands", ["show me bands", "show me artists", "show bands", "show artists", "bands", "artists"], "show me bands / artists", "ukáže kapely a umělce"),
    ("list:library", ["show me library", "show library", "library"], "show me library", "ukáže knihovnu stažených písní"),
    ("list:folders", ["show me folders", "show folders", "folders"], "show me folders", "ukáže složky a playlisty v knihovně (minipřehrávač)"),
    ("list:playlists", ["show me playlists", "show playlists", "playlists"], "show me playlists", "ukáže playlisty; „find playlist“ hledá další"),
    ("list:songs", ["show me internet", "internet"], "internet", "seznam: písně z internetu (minipřehrávač)"),
    # The app's other sections.
    ("show:podcasts", ["show me podcasts", "show podcasts"], "show me podcasts", "ukáže podcasty (aplikace)"),
    ("show:tools", ["show me tools", "show tools"], "show me tools", "ukáže nástroje (aplikace)"),
    ("show:tabs", ["show me tabs", "show tabs"], "show me tabs", "ukáže knihovnu tabů (aplikace)"),
    ("show:loops", ["show me loops", "show loops"], "show me loops", "ukáže loopy (aplikace)"),
    ("show:chords", ["show me chords", "show chords"], "show me chords", "ukáže stupnice a akordy (aplikace)"),
    ("show:analyzer", ["show me analyzer", "show analyzer"], "show me analyzer", "ukáže Performance Analyzer (aplikace)"),
    ("show:loopstudio", ["show me loop studio", "show loop studio"], "show me loop studio", "ukáže Loop Studio (aplikace)"),
    ("list:+", ["next list", "switch"], "next list / switch", "další seznam (minipřehrávač)"),
    ("list:-", ["previous list"], "previous list", "předchozí seznam (minipřehrávač)"),
    ("save", ["save song", "save this"], "save song", "hrající píseň do oblíbených: stáhne se, rozdělí na stopy, najde tab a text"),
    ("open", ["open pick", "open"], "open pick / open", "otevře okno Picku (i když je zavřené a poslouchá jen lišta)"),
    ("help", ["help", "help open", "open help"], "help / help open", "nabídka povelů — v aplikaci zůstane vpravo, dokud ji nezavřeš"),
    ("nothing", ["help close", "close help", "nothing"], "help close / nothing", "zavře nabídku povelů"),
    ("assist", ["listen", "assistant"], "listen", "asistent: pípne a poslouchá jednu českou větu (pusť…, ztlum…, zpomal…)"),
    ("full", ["full screen"], "full screen", "aplikace přes celou obrazovku / zpět"),
    ("minifull", ["mini full screen"], "mini full screen", "minipřehrávač přes celou obrazovku / zpět"),
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
         "practice": ("find:practice", "practice <název>", "píseň do Steems: rozdělí, ztlumí kytaru, zapne tvou"),
         "playlist": ("find:playlist", "find playlist <název>", "vyhledá playlisty toho jména"),
         "search": ("find:search", "search <název>", "najde píseň nebo kapelu: co už máš (Steems, oblíbené, knihovna), ukáže a otevře; jinak hledá na internetu")}
# "pick play <name>": the song of that name from the list that is on; with
# "playlist" after the name, the playlist of that name. ("pick play" alone,
# "pick play all", "pick play mix" stay the commands they are.)
NAMED = [("find:play", "play <název>", "pustí píseň toho jména ze seznamu, který je vybraný"),
         ("find:open", "play <název> playlist", "otevře playlist toho jména z nalezených")]
_PLAYLIST_WORDS = {"playlist", "playlists"}
# "pick play twenty five": the song (or playlist) with that number in the list on screen.
NAMED += [("find:here", "open song <název>", "otevře píseň toho jména ze sekce, která je vidět — ve Steems rovnou do mixu"),
          ("num", "play <číslo> / open song <číslo>", "otevře píseň s tím číslem v seznamu, který je vidět — „pick play twenty five“"),
          ("plnum", "playlist <číslo>", "otevře playlist s tím číslem — „pick playlist twenty“ (minipřehrávač)")]
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
NUMBER_WORDS = set(_UNITS) | set(_TENS) | {"hundred", "number"}


def spoken_number(words):
    """The number 1–999 these words say — "twenty five", "one hundred and three",
    "25" — or None when they are anything else."""
    words = [w for w in words if w not in ("number", "and", "a")]
    if len(words) == 1 and words[0].isdigit():
        n = int(words[0])
        return n if 1 <= n <= 999 else None
    if not words or len(words) > 4:
        return None
    n, rest = 0, list(words)
    if "hundred" in rest:
        i = rest.index("hundred")
        if i > 1 or (i == 1 and not 1 <= _UNITS.get(rest[0], 0) <= 9):
            return None
        n, rest = 100 * (_UNITS[rest[0]] if i else 1), rest[i + 1:]
    if rest and rest[0] in _TENS:
        n, rest = n + _TENS[rest[0]], rest[1:]
        if rest and 1 <= _UNITS.get(rest[0], 0) <= 9:
            n, rest = n + _UNITS[rest[0]], rest[1:]
    elif rest and rest[0] in _UNITS:
        n, rest = n + _UNITS[rest[0]], rest[1:]
    return n if not rest and 1 <= n <= 999 else None


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
    # A number of the list on screen — "pick play twenty five", "pick play song
    # seven", "pick playlist twenty" — every word of it clear.
    if 2 < len(words) <= 9 and (words[1] in ("play", "open", "number") or words[1] in _PLAYLIST_WORDS):
        rest = words[2:] if words[1] in ("play", "open") else words[1:]
        kind = "plnum" if rest and rest[0] in _PLAYLIST_WORDS else "num"
        if rest and (rest[0] in _PLAYLIST_WORDS or (rest[0] == "song" and words[1] in ("play", "open"))):
            rest = rest[1:]
        n = spoken_number(rest)
        if n and not (sure and min(sure) < MIN_CONF):
            return f"{kind}:{n}"
    # "pick open song <name>": the song of that name in the section that is in front.
    if len(words) > 3 and words[1] == "open" and words[2] == "song" and len(words) <= 13:
        if sure and min(sure[:3]) < MIN_CONF:
            return None
        return "find:here"
    # A name follows: only the two words before it have to be clear.
    if len(words) > 2 and words[1] in SLOTS and len(words) <= 12:
        if sure and min(sure[:2]) < MIN_CONF:
            return None
        return SLOTS[words[1]][0]
    # "pick find playlist <name>" — and "find" before any of the others.
    if len(words) > 3 and words[1] == "find" and words[2] in SLOTS and len(words) <= 13:
        if sure and min(sure[:3]) < MIN_CONF:
            return None
        return SLOTS[words[2]][0]
    if len(words) > 2 and words[1] == "find" and len(words) <= 12:       # "pick find <name>" is "pick search <name>"
        if sure and min(sure[:2]) < MIN_CONF:
            return None
        return "find:search"
    # "pick play <name>" — unless what follows "play" is a command of its own.
    if (len(words) > 2 and words[1] == "play" and len(words) <= 12 and _bare(words[1:]) not in PHRASES
            and not any(_bare(c["phrase"].split()) == _bare(words[1:]) for c in custom or [])):
        if sure and min(sure[:2]) < MIN_CONF:
            return None
        return "find:open" if words[-1] in _PLAYLIST_WORDS and len(words) > 3 else "find:play"
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
# Listening from the menu bar, with no window open: on or off, and which input.
WIDGET_PATH = Path.home() / ".local/share/spotdl-helper/voice-widget.json"


def widget_config():
    try:
        got = json.loads(WIDGET_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    return {"on": bool(got.get("on")), "device": str(got.get("device") or ""), "channel": str(got.get("channel") or "")}


def widget_save(**change):
    cfg = {**widget_config(), **{k: v for k, v in change.items() if v is not None}}
    _save_custom(cfg, WIDGET_PATH)
    return cfg
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
    return ([{"id": cid, "say": f"{WAKE} {say}", "does": does} for cid, say, does in list(SLOTS.values()) + NAMED]
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


def transcribe(pcm, hint="", lang="en", prompt=None):
    """What was said in this stretch of 16 kHz mono s16le, as text. `prompt`
    replaces the command-shaped one (a sung line is no command)."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "u.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes(b"\0" * RATE + pcm + b"\0" * RATE)      # whisper wants a little room
        cmd = [WHISPER_BIN, "-m", str(WHISPER_MODEL), "-l", lang, "-np", "-nt", "-f", str(wav)]
        if prompt is None:
            # Whisper leans on what it has "just heard": sentences of the very shape
            # we expect, and a few of the library's bands for their spelling.
            prompt = ("Pick song Wait and Bleed. Pick band Sepultura. Pick practice Duality. Pick play Roots. "
                      "Pick find playlist Metal. " + hint)[:400]
        if prompt:
            cmd += ["--prompt", prompt]
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
              "practice": "practice", "practise": "practice", "practices": "practice",
              "playlist": "playlist", "playlists": "playlist", "search": "search", "searching": "search"}


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
    if words[0].lower() in ("find", "fine", "search") and len(words) > 2 and words[1].lower() in _SLOT_LIKE:
        words = words[1:]                                  # "find playlist metal" is "playlist metal"
    elif words[0].lower() == "find" and len(words) > 1:
        return ("find", "search", " ".join(words[1:]))     # "find metallica" is "search metallica"
    if words[0].lower() in ("open", "opens", "opened") and len(words) > 2 and words[1].lower() in ("song", "songs"):
        return ("find", "here", " ".join(words[2:]))
    heard_kind = _SLOT_LIKE.get(words[0].lower())
    if heard_kind and len(words) > 1:
        return ("find", heard_kind, " ".join(words[1:]))
    # "play <name>", "play <name> playlist" — the small recogniser said so, or whisper does.
    if words[0].lower() in ("play", "played", "plays") and len(words) > 1:
        words, kind = words[1:], kind if kind in ("play", "open") else "play"
    if kind in ("play", "open"):
        if words[-1].lower() in _PLAYLIST_WORDS and len(words) > 1:
            return ("find", "open", " ".join(words[:-1]))
        return ("find", "play", " ".join(words))
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
                       | set(SLOTS) | {"find"} | _PLAYLIST_WORDS | NUMBER_WORDS | {w for c in self.custom for w in c["phrase"].split()}
                       | {w for h in self.index for w in h.split()})
        self.audio = bytearray()        # the last seconds heard, for a name said after a command
        self.fed = 0
        self.rec = vosk.KaldiRecognizer(_load(), RATE, json.dumps(vocab + ["[unk]"], ensure_ascii=False))
        self.rec.SetWords(True)
        self.lock = threading.Lock()

    def feed(self, pcm, menu=False):
        """16 kHz mono s16le. Returns {"heard", "command"} when an utterance just ended.

        `menu`: the list of commands is on screen ("pick help"). While it is, a command needs no wake word, a word of the
        vocabulary narrows the list ("filter"), and anything else is answered
        with "again"."""
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
        said = text.split()
        bare = bool(menu and said and said[0] != WAKE)
        if cmd is None and bare:
            cmd = match(f"{WAKE} {text}", [1.0] + confs, self.custom, self.index)
            if cmd is None:
                if ("[unk]" not in said and len(said) <= 3 and min(confs or [0]) >= 0.6
                        and not all(w in NUMBER_WORDS for w in said)):          # a number alone narrows nothing
                    keep = [w for i, w in enumerate(said) if w not in FILLERS and (i == 0 or w != said[i - 1])]   # "guitar guitar" is one word, heard twice
                    return {"heard": text, "command": None, "filter": " ".join(keep)}
                return {"heard": text, "command": None, "again": True}
        if cmd and cmd.startswith("custom:"):
            c = next(x for x in self.custom if x["id"] == cmd[7:])
            return {"heard": text, "command": cmd, "name": c["name"], "steps": c["steps"]}
        words = text.split()
        wakes = [c for w, c in zip(words[:2], confs[:2]) if w == WAKE]
        # "pick" was clear and what followed was not a command as heard here —
        # unknown words, or known ones that add up to nothing (an accent).
        unclear = (cmd is None and words[0] == WAKE and 2 <= len(words) <= 12
                   and wakes and max(wakes) >= MIN_CONF)
        if (unclear or (cmd and cmd.startswith("find:"))) and res.get("result"):
            # Something was asked of Pick that these few words don't cover — a
            # name, most likely. The utterance itself goes on to whisper.
            start = int(max(0.0, res["result"][0].get("start", 0) - 0.3) * RATE) * 2 - heard_from
            return {"heard": text, "command": cmd or "ask", "pcm": snapshot[max(0, start):]}
        out = {"heard": text, "command": cmd, "steps": [cmd] if cmd else None}
        if cmd is None and words[0] == WAKE and len(words) >= 2:
            out["again"] = True                    # addressed to Pick, and not understood
        return out


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
