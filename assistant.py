"""Pick's assistant: a sentence said in Czech becomes calls of the app's own actions.

A model reads what was said, together with SYSTEM — the one description of
Pick it ever sees — and TOOLS, and answers with the tools to call. The tools
themselves run in the page (static/index.html, Voice.assist): that is where
the player and the mixer live. So one spoken request is a few round trips:
say() → the page runs the calls → results() → … until there is nothing more
to call and what was done is said.

The model is one that runs in this computer when there is one (a small Gemma
under llama-server: free, no internet, started when first asked and let go
when left alone — see "the model in this computer" below), and otherwise
Claude over the Anthropic API.

The API key stays on this computer, in KEY_PATH or ANTHROPIC_API_KEY, and is
handed only to the worker process that talks to the API. The Anthropic library
wants newer versions of packages the server itself pins, so it lives in an
environment of its own (WORKER_PYTHON) and each call runs assistant_worker.py
there.

Keep SYSTEM and TOOLS true to the app: when Pick learns something new that the
assistant should be able to do, it goes in here, or the assistant will not
know it exists.
"""
import difflib
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
import urllib.request
import uuid
from pathlib import Path

HOME = Path.home()
KEY_PATH = HOME / ".local/share/spotdl-helper/anthropic-key.txt"
WORKER_PYTHON = HOME / ".local/venvs/pick-assistant/bin/python"
WORKER = Path(__file__).with_name("assistant_worker.py")
MODEL = "claude-haiku-4-5"          # quick and cheap: what a spoken command needs
MAX_ROUNDS = 8                      # tool round trips in one request, then it has to stop
IDLE = 600                          # a conversation is forgotten after ten quiet minutes

STEMS = ["drums", "bass", "guitar", "vocals", "piano", "other"]
SECTIONS = ["intro", "verse", "chorus", "bridge", "solo", "outro"]

SYSTEM = """Jsi hlasový asistent v aplikaci Pick — hudební aplikaci pro kytaristu, která běží v jeho počítači. Uživatel hraje na kytaru, má plné ruce a mluví na tebe česky přes mikrofon; jeho věta k tobě přichází jako přepis řeči, takže v ní mohou být zkomolená slova a názvy písní napsané foneticky. Tvoje práce je udělat v aplikaci to, co řekl, pomocí nástrojů.

Jak Pick vypadá:
- Knihovna: písně stažené v počítači. Hrají ve spodním přehrávači. Ten umí jen přehrávat, zastavit, přeskakovat a měnit hlasitost.
- Steems: mixážní pult pro píseň rozdělenou na stopy (bicí = drums, basa = bass, kytara = guitar, zpěv = vocals, klavír = piano, ostatní = other). Jen tady jde ztlumit nebo zesílit jednotlivou stopu, změnit tempo beze změny výšky a pustit část písně ve smyčce. Sekce písně (intro, sloka = verse, refrén = chorus, mezihra = bridge, sólo = solo, konec = outro) má jen píseň, kterou aplikace rozebrala.
- Cvičení: píseň otevřená ve Steems, její kytara ztlumená a zapnutá uživatelova živá kytara.
- Rozdělení písně na stopy trvá několik minut. Píseň, která rozdělená není, jde otevřít ve Steems jen nástrojem practice, který ji nejdřív rozdělí — řekni uživateli, že to potrvá.

Jak postupovat:
- Když věta obsahuje víc pokynů, udělej je popořadě. Cokoli se týká stop, tempa nebo smyčky, potřebuje píseň otevřenou ve Steems: nejdřív ji tam otevři (open_song s where="steems"), teprve potom měň stopy, tempo a smyčku.
- Když nevíš, co je právě otevřené a co hraje, zavolej get_state. Nehádej.
- Nástroje vracejí krátkou českou větu o tom, co se stalo. Když nástroj vrátí, že něco nejde, neopakuj ho stejně znovu; zkus jinou cestu, nebo to uživateli řekni.
- Názvy písní a kapel předávej tak, jak je uživatel řekl; knihovna si je dohledá podle zvuku. Když open_song nic nenajde, řekni to a nezkoušej vymyšlené názvy.
- Ptej se jen tehdy, když bez odpovědi nejde pokračovat (například není jasné, kterou píseň myslí). Otázka musí být jedna, krátká, a končit otazníkem — aplikace podle otazníku pozná, že má poslouchat odpověď.
- Nic nemaž a nic nepřepisuj; takové nástroje nemáš. Když o to uživatel požádá, řekni, že to hlasem zatím nejde.

Jak odpovídat:
- Na konci jednou větou řekni, co jsi udělal, nebo co se nepovedlo a proč. Česky, bez formátování, bez výčtů — odpověď se čte nahlas.
- Neopakuj uživatelovu větu a nenabízej další pomoc."""

TOOLS = [
    {"name": "get_state",
     "description": "Co je teď v aplikaci otevřené a co hraje: píseň ve spodním přehrávači, píseň ve Steems, její stopy, tempo, hlasitost. Zavolej, když to pro pokyn potřebuješ vědět.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "open_song",
     "description": "Najde píseň v knihovně podle názvu (může být i s kapelou) a otevře ji. where=\"player\" ji pustí ve spodním přehrávači. where=\"steems\" ji otevře v mixážním pultu, kde jde měnit stopy, tempo a smyčky; funguje jen pro píseň už rozdělenou na stopy, jinak vrátí, že rozdělená není.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Název písně, jak ho uživatel řekl."},
         "where": {"type": "string", "enum": ["player", "steems"]}},
         "required": ["name", "where"], "additionalProperties": False}},
    {"name": "play_band",
     "description": "Pustí ve spodním přehrávači všechny písně kapely nebo interpreta, které jsou v knihovně.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"], "additionalProperties": False}},
    {"name": "playback",
     "description": "Ovládání přehrávání. play spustí to, co je vpředu (otevřenou píseň ve Steems, jinak spodní přehrávač); stop zastaví vše, co hraje; restart skočí na začátek; next a previous přepínají písně ve spodním přehrávači.",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["play", "stop", "restart", "next", "previous"]}},
         "required": ["action"], "additionalProperties": False}},
    {"name": "set_tempo",
     "description": "Tempo písně otevřené ve Steems v procentech původního (100 = původní, 80 = pomaleji). Výška tónů se nemění. Mimo Steems nefunguje.",
     "input_schema": {"type": "object", "properties": {"percent": {"type": "integer", "minimum": 25, "maximum": 150}},
                      "required": ["percent"], "additionalProperties": False}},
    {"name": "loop_section",
     "description": "Smyčka ve Steems. Název sekce (intro, verse, chorus, bridge, solo, outro) dá smyčku na nejbližší další sekci toho druhu; current na sekci, kde přehrávání právě je; next a previous na sousední sekci; off smyčku zruší. Funguje jen u písně, která má sekce.",
     "input_schema": {"type": "object", "properties": {
         "section": {"type": "string", "enum": SECTIONS + ["current", "next", "previous", "off"]}},
         "required": ["section"], "additionalProperties": False}},
    {"name": "stem",
     "description": "Jedna stopa písně otevřené ve Steems: mute ji ztlumí, unmute pustí, solo nechá hrát jen ji, unsolo sólo zruší, louder a quieter změní její hlasitost o krok.",
     "input_schema": {"type": "object", "properties": {
         "stem": {"type": "string", "enum": STEMS},
         "action": {"type": "string", "enum": ["mute", "unmute", "solo", "unsolo", "louder", "quieter"]}},
         "required": ["stem", "action"], "additionalProperties": False}},
    {"name": "volume",
     "description": "Celková hlasitost aplikace o daný počet kroků po 3 dB: kladné číslo přidá, záporné ubere.",
     "input_schema": {"type": "object", "properties": {"steps": {"type": "integer", "minimum": -6, "maximum": 6}},
                      "required": ["steps"], "additionalProperties": False}},
    {"name": "practice",
     "description": "Režim cvičení: píseň do Steems, její kytara ztlumená, uživatelova živá kytara zapnutá. S názvem najde píseň v knihovně a když není rozdělená na stopy, nejdřív ji rozdělí (několik minut). Bez názvu použije píseň, která je právě otevřená nebo hraje.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Název písně; vynech pro právě hrající."}},
                      "additionalProperties": False}},
]
# What else Pick does at one word (voice.py's commands, run in the page by
# their id): anything here the assistant can do as well.
COMMANDS = {
    "tuner": "otevře ladičku",
    "metronome": "metronom zap / vyp",
    "record": "nahrávání uživatelovy kytary zap / vyp",
    "loop": "smyčka zap / vyp (bez určení sekce)",
    "tabs": "tabulatura k hrající písni zap / vyp",
    "lyrics": "text hrající písně zap / vyp",
    "see:lyrics": "text písně přes celé okno",
    "see:mix": "zpět na mix a stopy",
    "words": "trénink výslovnosti: přečte řádek textu a poslechne si uživatele",
    "mywords": "trénink výslovnosti jen ze slov, která se dřív nepovedla",
    "melody": "trénink zpěvu po řádcích",
    "myguitar": "uživatelova živá kytara zap / vyp",
    "preset:+": "další preset zesilovače",
    "preset:-": "předchozí preset zesilovače",
    "gtrs:all": "ukáže v tabu všechny kytary",
    "gtrs:more": "přidá do tabu další kytaru",
    "skip:+": "o deset vteřin dál",
    "skip:-": "o deset vteřin zpět",
    "song": "vybere a pustí náhodnou píseň z knihovny",
    "save": "hrající píseň do oblíbených (stáhne ji, rozdělí na stopy, najde tab a text)",
    "tomini": "hrající píseň do minipřehrávače",
    "mini": "otevře minipřehrávač",
    "full": "aplikace přes celou obrazovku / zpět",
    "panel:off": "schová levý panel",
    "panel:on": "vrátí levý panel",
    "steems": "otevře Steems se seznamem rozdělených písní",
    "playlist": "otevře playlisty",
    "list:favs": "ukáže oblíbené",
    "list:bands": "ukáže kapely a umělce",
    "list:library": "ukáže knihovnu stažených písní",
    "show:tabs": "ukáže knihovnu tabů",
    "show:chords": "ukáže stupnice a akordy",
    "show:loops": "ukáže loopy",
    "show:tools": "ukáže nástroje",
    "show:podcasts": "ukáže podcasty",
    "show:analyzer": "ukáže Performance Analyzer",
    "show:loopstudio": "ukáže Loop Studio",
    "sort:artist": "seřadí seznam podle kapely",
    "sort:album": "seřadí seznam podle alba",
    "sort:title": "seřadí seznam podle názvu",
    "sort:tuning": "seřadí seznam podle ladění kytary",
    "slower": "tempo o 5 % dolů",
    "faster": "tempo o 5 % nahoru",
    "help": "ukáže nabídku hlasových povelů",
}
TOOLS.append(
    {"name": "command",
     "description": "Ostatní, co Pick umí jedním povelem — vše, na co není jiný nástroj. Co který povel dělá: "
                    + "; ".join(f"{k} = {v}" for k, v in COMMANDS.items()) + ".",
     "input_schema": {"type": "object", "properties": {"id": {"type": "string", "enum": list(COMMANDS)}},
                      "required": ["id"], "additionalProperties": False}})
TOOL_NAMES = {t["name"] for t in TOOLS}


def key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k:
        try:
            k = KEY_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            k = ""
    return k


def brain():
    """Who answers: "local" (the model in this computer), "claude", or None."""
    if llm_model() and Path(LLM_BIN).exists():
        return "local"
    return "claude" if WORKER_PYTHON.exists() and key() else None


def model_name():
    return Path(llm_model()).stem + " (v tomhle počítači)" if brain() == "local" else MODEL


def available():
    """Why the assistant can't run here, or None when it can."""
    if brain():
        return None
    if not WORKER_PYTHON.exists():
        return "Chybí prostředí asistenta (~/.local/venvs/pick-assistant)."
    if not key():
        return f"Chybí API klíč. Ulož ho do souboru {KEY_PATH} (jen klíč, nic dalšího)."
    return None


def ask(messages, call=None):
    """One request to the model: {"content": [blocks], "stop_reason", "usage"} or {"error"}."""
    if call:
        return call(messages)
    if brain() == "local":
        return ask_local(messages)
    try:
        out = subprocess.run([str(WORKER_PYTHON), str(WORKER)], input=json.dumps({"messages": messages, "model": MODEL}),
                             capture_output=True, text=True, timeout=90, env={**os.environ, "ANTHROPIC_API_KEY": key()})
        return json.loads(out.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "Asistent neodpověděl včas."}
    except (ValueError, OSError):
        return {"error": "Asistent se nespustil."}


_sessions = {}
_lock = threading.Lock()


def _session(sid):
    now = time.time()
    with _lock:
        for k in [k for k, s in _sessions.items() if now - s["at"] > IDLE]:
            del _sessions[k]
        s = _sessions.setdefault(sid, {"messages": [], "rounds": 0, "pending": []})
        s["at"] = now
        return s


def _step(s, call=None):
    """Ask the model and turn its answer into what the page needs: text to say,
    tools to run, and whether that is the end of it."""
    r = ask(s["messages"], call)
    if r.get("error"):
        # The request that failed must not stay half-said in the conversation.
        while s["messages"] and not (s["messages"][-1]["role"] == "user" and isinstance(s["messages"][-1]["content"], str)):
            s["messages"].pop()
        if s["messages"]:
            s["messages"].pop()
        s["pending"] = []
        return {"error": r["error"], "done": True}
    content = [b for b in r.get("content", []) if b.get("type") in ("text", "tool_use")]
    s["messages"].append({"role": "assistant", "content": content or [{"type": "text", "text": "…"}]})
    say = " ".join(b["text"].strip() for b in content if b["type"] == "text" and b["text"].strip())
    calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}} for b in content if b["type"] == "tool_use"]
    if calls and s["rounds"] >= MAX_ROUNDS:
        # Still calling after this many rounds: answer its calls with a refusal so the
        # conversation stays well-formed, and stop.
        s["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c["id"], "content": "Zastaveno: příliš mnoho kroků.", "is_error": True} for c in calls]})
        return {"say": say or "Zamotal jsem se, zkus to říct jinak.", "calls": [], "done": True}
    s["pending"] = [c["id"] for c in calls]
    return {"say": say, "calls": calls, "done": not calls, "usage": r.get("usage")}


def say(sid, text, call=None):
    """The user said `text`. Returns {"say", "calls", "done"} — run the calls, then results()."""
    s = _session(sid)
    if s["pending"]:                     # the page never answered the last calls: that request is void
        s["messages"], s["pending"] = [], []
    if len(s["messages"]) > 40:          # a long chat: only the recent part is needed for "that one"
        cut = next((i for i in range(len(s["messages"]) - 20, len(s["messages"]))
                    if s["messages"][i]["role"] == "user" and isinstance(s["messages"][i]["content"], str)), None)
        s["messages"] = s["messages"][cut:] if cut is not None else []
    s["messages"].append({"role": "user", "content": text.strip()[:2000]})
    s["rounds"] = 0
    return _step(s, call)


def results(sid, done, call=None):
    """What the tools returned: [{"id", "content", "is_error"}], one for every call."""
    s = _session(sid)
    if not s["pending"]:
        return {"error": "Asistent na nic nečeká.", "done": True}
    by = {d.get("id"): d for d in done}
    s["messages"].append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": i, "content": str((by.get(i) or {}).get("content") or "Nástroj neodpověděl.")[:1500],
         **({"is_error": True} if i not in by or by[i].get("is_error") else {})} for i in s["pending"]]})
    s["pending"] = []
    s["rounds"] += 1
    return _step(s, call)


# ---- the model in this computer. A small one cannot hold a conversation of
# tool calls the way Claude does, so it is asked for one thing only: given the
# state of the app and the sentence, the list of calls — written into a form
# it cannot step out of (llama-server holds its answer to a JSON schema). The
# rest is done around it, in the same shape the page already knows: first
# get_state is called, then the model's calls, and what the tools answered is
# what is said. A model this size also guesses; what it is known to get wrong
# is put right in _mend().
#
# And most sentences never reach it. What is in the book (assistant_book.py:
# the ways things are said, written out) or was learned is done at once. The
# model gets the rest, with the book's nearest sentences as examples; what it
# then did without a failure is learned (LEARNED), so the same sentence is
# immediate next time. A sentence nobody understood is kept (UNKNOWN), and
# when the next one, said another way, works, the first is learned to mean
# the same. "Špatně" after a wrong guess unlearns it for good. With no model
# to ask — the card with it not in — the nearest sentence of the book serves.
import assistant_book as _book

LEARNED = HOME / ".local/share/spotdl-helper/assistant-learned.json"
UNKNOWN = HOME / ".local/share/spotdl-helper/assistant-unknown.json"
SURE = 0.93                         # this like a sentence of the book: the same sentence, misheard
GUESS = 0.8                         # …and this like one, when there is no model to ask
_WRONG = re.compile(r"^(ne+|spatne|to je spatne|to ne|tohle ne|to jsem nechtel|to jsem nerekl|chyba|omyl|zapomen to|tak ne)$")
_turns = {}                         # by the id of a turn's first call: what was said, the calls, and who chose them
_last = {"miss": None, "learned": None}


def _read(path, empty):
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        return got if isinstance(got, type(empty)) else empty
    except (OSError, ValueError):
        return empty


def _write(path, data):
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def learned():
    """{normalised sentence: {"text", "calls", "n"}} — without what was said to be wrong."""
    return {k: v for k, v in _read(LEARNED, {}).items() if not v.get("wrong")}


def learn(text, calls):
    key = _book.norm(text)
    if not key or not calls or key in _book.BOOK:
        return False
    all_ = _read(LEARNED, {})
    if all_.get(key, {}).get("wrong"):                     # told once that this is not what it means
        return False
    all_[key] = {"text": text.strip(), "calls": calls, "n": all_.get(key, {}).get("n", 0) + 1, "at": int(time.time())}
    _write(LEARNED, all_)
    _last["learned"] = key
    return True


def unlearn_last():
    key, all_ = _last["learned"], _read(LEARNED, {})
    if not key or key not in all_:
        return None
    all_[key] = {"text": all_[key]["text"], "wrong": True, "at": int(time.time())}
    _write(LEARNED, all_)
    _last["learned"] = None
    return all_[key]["text"]


def _alike(a, b):
    """Whether two sentences may be one thing said twice (a question about the time is not a way to mute the drums)."""
    x, y = _book.norm(a), _book.norm(b)
    return difflib.SequenceMatcher(None, x, y).ratio() >= 0.45 or bool({w[:4] for w in x.split() if len(w) > 3} & {w[:4] for w in y.split() if len(w) > 3})


def _missed(text):
    rows = [r for r in _read(UNKNOWN, []) if r.get("text") != text][-199:]
    _write(UNKNOWN, rows + [{"text": text, "at": int(time.time())}])
    _last["miss"] = {"text": text, "at": time.time()}
LLM_BIN = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
LLM_FILE = "google_gemma-3-4b-it-Q4_K_M.gguf"
LLM_PATHS = [str(HOME / ".local/share/spotdl-helper/models" / LLM_FILE), "/Volumes/*/pick-models/" + LLM_FILE]
LLM_PORT = 8793
LLM_IDLE = 900                      # seconds without a request before the model is let go (it holds some 3.5 GB)
LLM_PID = HOME / ".local/share/spotdl-helper/assistant-llm.pid"
_llm = {"proc": None, "used": 0.0, "watch": None}
_llm_lock = threading.Lock()
_plans = {}                         # the calls still to come, by the id of the turn's get_state

fold = lambda t: "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def llm_model():
    env = os.environ.get("PICK_LLM_MODEL", "").strip()
    if env:
        return env if Path(env).is_file() else None
    for pat in LLM_PATHS:
        for f in sorted(glob.glob(pat)):
            if Path(f).is_file():
                return f
    return None


def _llm_alive():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{LLM_PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:                                       # noqa: BLE001 — not there, or still loading
        return False


def _llm_pid():
    """The llama-server Pick started (this run or the one before), or None."""
    try:
        pid = int(LLM_PID.read_text().strip())
        who = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
        return pid if "llama-server" in who else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def llm_stop():
    with _llm_lock:
        pid = _llm_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        LLM_PID.unlink(missing_ok=True)
        _llm["proc"] = None


def _llm_watch():
    while True:
        time.sleep(30)
        if _llm["used"] and time.time() - _llm["used"] > LLM_IDLE and _llm_pid():
            llm_stop()


def llm_wake(wait=True):
    """The model loaded and answering (it is started when it is not). → why not, or None."""
    path = llm_model()
    if not path:
        return "Model asistenta není k nalezení (je připojená karta s ním?)."
    with _llm_lock:
        _llm["used"] = time.time()
        if _llm["watch"] is None:
            _llm["watch"] = threading.Thread(target=_llm_watch, name="assistant-llm", daemon=True)
            _llm["watch"].start()
        if not _llm_alive() and not _llm_pid():
            try:
                proc = subprocess.Popen([LLM_BIN, "-m", path, "--host", "127.0.0.1", "--port", str(LLM_PORT), "-ngl", "99",
                                         "-c", "8192", "--no-webui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                return "Model asistenta se nespustil."
            _llm["proc"] = proc
            LLM_PID.write_text(str(proc.pid))
    if not wait:
        return None
    end = time.time() + 150                                # from a memory card the first load takes about a minute
    while time.time() < end:
        if _llm_alive():
            return None
        if not _llm_pid():
            return "Model asistenta se nespustil."
        time.sleep(0.5)
    return "Model asistenta se načítá příliš dlouho, zkus to za chvíli znovu."


def _strip(schema):
    out = {k: v for k, v in schema.items() if k not in ("minimum", "maximum", "description")}
    if "properties" in out:
        out["properties"] = {k: _strip(v) for k, v in out["properties"].items()}
    return out


_ACT = [t for t in TOOLS if t["name"] not in ("get_state", "command")]
LLM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pokyny", "calls", "say"], "properties": {
    # first the sentence cut into what it asks for: a call for each, and none besides
    "pokyny": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
    "calls": {"type": "array", "maxItems": 4, "items": {"anyOf": [
        {"type": "object", "additionalProperties": False, "required": ["tool", "args"],
         "properties": {"tool": {"const": t["name"]}, "args": _strip(t["input_schema"])}} for t in _ACT] + [
        {"type": "object", "additionalProperties": False, "required": ["tool", "args"],
         "properties": {"tool": {"const": "command"}, "args": {"type": "object", "additionalProperties": False, "required": ["id"],
                                                                "properties": {"id": {"type": "string", "enum": list(COMMANDS)}}}}}]}},
    "say": {"type": "string"}}}
LLM_SYSTEM = SYSTEM + "\n\nNástroje:\n" + "\n".join(
    f"- {t['name']}: {t['description']} Argumenty: {json.dumps(_strip(t['input_schema']).get('properties', {}), ensure_ascii=False)}" for t in _ACT) + """
- command: všechno ostatní, co Pick umí jedním povelem. Argument id je jedno z:
""" + "\n".join(f"    {k} — {v}" for k, v in COMMANDS.items()) + """

Odpověz jen jedním JSON objektem: {"pokyny": [...], "calls": [...], "say": "..."}.
- pokyny: věta uživatele rozdělená na jednotlivé pokyny, jeho slovy. Většinou je jen jeden.
- calls: pro každý pokyn právě jedno volání, ve stejném pořadí. Nic navíc: žádné spouštění, zastavování ani změny stop, o které uživatel neřekl.
- say: jedna krátká česká věta.
- Když pokyn nejde splnit žádným nástrojem (mazání, otázky mimo aplikaci), nech calls prázdné a v say řekni proč.
- Název písně nebo kapely piš jen tehdy, když ho uživatel řekl. Říká-li „tuhle“, „tu, co hraje“, název vynech.
- Kapela nebo interpret („něco od…“) je play_band. Jedna píseň je open_song; where je "player", pokud uživatel neřekl mix, Steems, stopy nebo cvičení.
- practice jen tehdy, když chce píseň cvičit na kytaru. Výslovnost, zpěv, nahrávání, ladička, taby, text a okna aplikace jsou command.
- Další nebo předchozí píseň je playback next / previous. Skok o kousek dál nebo zpět je command skip:+ / skip:-.

Příklady:
„vypni kytaru“ → {"pokyny": ["vypni kytaru"], "calls": [{"tool": "stem", "args": {"stem": "guitar", "action": "mute"}}], "say": "Kytara ztlumená."}
„pusť zase bicí“ → {"pokyny": ["pusť zase bicí"], "calls": [{"tool": "stem", "args": {"stem": "drums", "action": "unmute"}}], "say": "Bicí hrají."}
„nech hrát jen basu“ → {"pokyny": ["nech hrát jen basu"], "calls": [{"tool": "stem", "args": {"stem": "bass", "action": "solo"}}], "say": "Hraje jen basa."}
„pusť něco od Metalliky“ → {"pokyny": ["pusť něco od Metalliky"], "calls": [{"tool": "play_band", "args": {"name": "Metallica"}}], "say": "Pouštím Metallicu."}
„otevři ladičku a zapni metronom“ → {"pokyny": ["otevři ladičku", "zapni metronom"], "calls": [{"tool": "command", "args": {"id": "tuner"}}, {"tool": "command", "args": {"id": "metronome"}}], "say": "Ladička a metronom."}
„chci trénovat výslovnost“ → {"pokyny": ["chci trénovat výslovnost"], "calls": [{"tool": "command", "args": {"id": "words"}}], "say": "Spouštím výslovnost."}
„zapni moji kytaru“ → {"pokyny": ["zapni moji kytaru"], "calls": [{"tool": "command", "args": {"id": "myguitar"}}], "say": "Tvoje kytara je zapnutá."}
„chci cvičit tuhle písničku“ → {"pokyny": ["chci cvičit tuhle písničku"], "calls": [{"tool": "practice", "args": {}}], "say": "Připravuju cvičení."}
„smaž tuhle písničku“ → {"pokyny": ["smaž tuhle písničku"], "calls": [], "say": "Mazat hlasem nejde."}"""

_DELETE = re.compile(r"\b(smaz|vymaz|odstran|zahod|znic)")
_MIX = re.compile(r"mix|steem|stems|stop[yu]?\b|stopach|cvic")
_NEEDS_MIX = {"stem", "set_tempo", "loop_section"}
_FAILED = re.compile(r"nenasel|neni rozdelen|nenacetl|nepovedlo|nejde")
_PRACTICE = re.compile(r"cvic|practice|nauc")
_BAND = re.compile(r"\b(neco|cokoli\w*|nejak\w+|pisnicky|songy|vsechno) od\b|\bod (kapely|skupiny)\b")


def _mend(text, calls, asked=4):
    """The model's calls, with what a small model is known to get wrong put
    right: a name the user never said, the mixer where the player was meant,
    and anything at all when deleting was asked for."""
    said = fold(text)
    if _DELETE.search(said):
        return [], "Mazat hlasem nejde."
    words = re.findall(r"\w+", said)
    out = []
    for c in calls[:max(1, min(4, asked))]:                # a call for each thing asked, and none besides
        tool, args = c.get("tool"), dict(c.get("args") or {})
        if tool not in TOOL_NAMES or tool == "get_state":
            continue
        if tool == "practice" and not _PRACTICE.search(said):
            continue
        if tool == "volume":
            args["steps"] = max(-6, min(6, int(args.get("steps") or 0)))
        if "name" in args:
            # only the words of the name that were spoken ("Sepultury" is "Sepultura": the first letters decide)
            kept = [w for w in str(args["name"]).split() if len(fold(w)) > 1 and any(x[:5] == fold(re.sub(r"\W", "", w))[:5] for x in words)]
            args["name"] = " ".join(kept).strip(" -–—")
            if not args["name"]:
                del args["name"]
                if tool != "practice":
                    continue
        if tool == "open_song" and _BAND.search(said):     # "něco od Sepultury" is the band, not a song of that name
            tool, args = "play_band", {"name": args["name"]}
        if tool == "open_song" and args.get("where") == "steems" and not _MIX.search(said) \
                and not any(x.get("tool") in _NEEDS_MIX for x in calls):
            args["where"] = "player"
        out.append({"tool": tool, "args": args})
    return out, None


def _plain(result):
    """A tool's answer as a person is told it (some were written for a model that calls tools)."""
    t = str(result)
    for a, b in ((" Nehraje, dokud nezavoláš playback play.", " Zatím nehraje."),
                 ("ve Steems ji otevře jen nástroj practice (rozdělení trvá několik minut)", "řekni „chci ji cvičit“ a rozdělím ji (trvá to několik minut)"),
                 (" — nejdřív ji tam otevři (open_song, where steems)", " — nejdřív tam nějakou otevři"),
                 ("tohle teď nejde", "tohle teď nejde")):
        t = t.replace(a, b)
    return t.strip()


def _llm_calls(state, text, before):
    why = llm_wake()
    if why:
        return None, why
    like = _book.near(text, 6, learned())
    shown = "".join(f"„{t}“ → {json.dumps(c, ensure_ascii=False)}\n" for _, t, c in like)
    user = ((("Podobné věty a co znamenají (jen pro srovnání):\n" + shown) if shown else "")
            + (("Před chvílí uživatel řekl: " + " / ".join(before[-2:]) + "\n") if before else "")
            + f"Stav aplikace: {state}\nUživatel řekl: {text}")
    body = {"messages": [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": user}], "max_tokens": 400, "temperature": 0,
            "cache_prompt": True, "response_format": {"type": "json_schema", "json_schema": {"name": "act", "schema": LLM_SCHEMA, "strict": True}}}
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{LLM_PORT}/v1/chat/completions", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            got = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
    except Exception:                                       # noqa: BLE001
        return None, "Asistent neodpověděl."
    finally:
        _llm["used"] = time.time()
    return got, None


def _known(text):
    """What a sentence means without asking anybody: the book, what was learned, or one misheard. → calls or None."""
    more = learned()
    calls = _book.find(text, more)
    if calls is None:
        like = _book.near(text, 1, more)
        if like and like[0][0] >= SURE:
            calls = list(like[0][2])
    return calls


def ask_local(messages, think=None):
    """ask(), answered in this computer. `think` stands in for the model (tests)."""
    at = max(i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str))
    turn, text = messages[at:], messages[at]["content"]
    use = lambda name, args: {"type": "tool_use", "id": "l" + uuid.uuid4().hex[:10], "name": name, "input": args}
    say = lambda t: {"content": [{"type": "text", "text": t}]}

    def start(calls, who):
        """The calls go to the page — a song first and alone: when opening it fails, the rest would land on another."""
        first, rest = (calls[:1], calls[1:]) if calls[0]["tool"] in ("open_song", "practice") and len(calls) > 1 else (calls, [])
        blocks = [use(c["tool"], c["args"]) for c in first]
        tid = turn[1]["content"][0]["id"] if len(turn) > 1 else blocks[0]["id"]
        _turns[tid] = {"text": text, "calls": calls, "rest": rest, "who": who, "state": len(turn) > 1}
        while len(_turns) > 50:
            _turns.pop(next(iter(_turns)))
        return {"content": blocks}

    if len(turn) == 1:
        if _WRONG.match(_book.norm(text) or fold(text)):
            was = unlearn_last()
            return say(f"Dobře, „{was}“ už takhle chápat nebudu. Řekni mi to jinak." if was else "Dobře. Řekni mi to jinak.")
        calls = _known(text)
        if calls:
            calls, instead = _mend(text, calls, len(calls))
            if calls:
                return start(calls, "book")
        if brain() == "local" and think is None:
            threading.Thread(target=llm_wake, daemon=True).start()      # the model loads while the page says what is open
        return {"content": [use("get_state", {})]}                    # first, what is open and playing
    tid = turn[1]["content"][0].get("id")
    done = [b for b in turn[-1]["content"] if b.get("type") == "tool_result"]
    if len(turn) == 3 and turn[1]["content"][0].get("name") == "get_state":   # the state is here: what is to be done?
        before = [m["content"] for m in messages[:at] if m["role"] == "user" and isinstance(m["content"], str)]
        got, why = (think or _llm_calls)(str(done[0].get("content") or "") if done else "", text, before)
        if why:                                            # nobody to ask: the nearest sentence of the book, when one is near
            like = _book.near(text, 1, learned())
            if not (like and like[0][0] >= GUESS):
                _missed(text)
                return {"error": why}
            got = {"calls": list(like[0][2]), "pokyny": like[0][2]}
        parts = len(re.split(r"\s+a\s+|,", text.strip()))
        calls, instead = _mend(text, got.get("calls") or [], max(len(got.get("pokyny") or []), parts, 1))
        if not calls:
            if not instead:
                _missed(text)
            return say(instead or "Tomu jsem nerozuměl. Zkus to říct jinak — zapamatuju si, co jsi tím myslel.")
        return start(calls, "model")
    plan = _turns.get(tid) or {"text": text, "calls": [], "rest": [], "who": "", "state": True}
    failed = any(b.get("is_error") or _FAILED.search(fold(b.get("content") or "")) for b in done)
    if plan["rest"] and not failed:
        rest, plan["rest"] = plan["rest"], []
        return {"content": [use(c["tool"], c["args"]) for c in rest]}
    _turns.pop(tid, None)
    # what the tools answered is what was done — every round of this turn, said once each
    told = []
    for m in turn[(3 if plan["state"] else 1):]:
        if m["role"] == "user":
            for b in m["content"]:
                t = _plain(b.get("content") or "")
                if b.get("type") == "tool_result" and t and t not in told:
                    told.append(t)
    line = " ".join(t[:1].upper() + t[1:].rstrip(".") + "." for t in told) or "Hotovo."
    if not failed and plan["calls"]:
        if plan["who"] == "model":
            learn(plan["text"], plan["calls"])
        miss, _last["miss"] = _last["miss"], None
        if miss and time.time() - miss["at"] < 90 and _alike(miss["text"], plan["text"]) and learn(miss["text"], plan["calls"]):
            line += f" Zapamatoval jsem si, že tohle znamená i „{miss['text']}“."
    return say(line)


def book_page():
    """What can be said, what was learned and what was not understood — a page to read."""
    esc = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;")
    what = lambda calls: " + ".join(c["tool"] + (" " + " ".join(str(v) for v in c["args"].values()) if c["args"] else "") for c in calls)
    by = {}
    for v in _book.BOOK.values():
        by.setdefault(what(v["calls"]), []).append(v["text"])
    groups = "".join(
        f"<h2>{esc(title)}</h2>" + "".join(f"<p><b>{esc(k)}</b><br>{esc(' · '.join(says[:14]))}{' …' if len(says) > 14 else ''}</p>"
                                           for k, says in by.items() if k.split()[0] in tools)
        for title, tools in _book.GROUPS)
    mine = "".join(f"<p>„{esc(v['text'])}“ → <b>{esc(what(v['calls']))}</b></p>" for v in learned().values()) or "<p>Zatím nic.</p>"
    lost = "".join(f"<p>„{esc(r['text'])}“</p>" for r in reversed(_read(UNKNOWN, [])[-40:])) or "<p>Nic.</p>"
    return ("<!doctype html><meta charset=utf-8><title>Co Pick umí na slovo</title><style>body{background:#0b0d18;color:#dfe3f5;font:15px/1.55 -apple-system,sans-serif;"
            "max-width:860px;margin:30px auto;padding:0 18px}h1{font-size:24px}h2{color:#a98bff;margin-top:30px;font-size:17px}b{color:#fff}p{margin:9px 0}"
            ".n{color:#8f96b5}</style><h1>Co Pick umí na slovo</h1>"
            f"<p class=n>Řekni „pick listen“ a pak česky, co chceš. {len(_book.BOOK)} vět níže Pick zná hned; věty jdou spojovat pomocí „a“. "
            "Písně a kapely říkej jménem („pusť něco od Sepultury“, „otevři Blind v mixu“, „chci cvičit Roots“). "
            "Čemu nerozumí, zkusí pochopit model v počítači a co se povedlo, si zapamatuje. Po špatném pochopení řekni „špatně“.</p>"
            f"<h2>Co se Pick naučil od tebe</h2>{mine}<h2>Čemu naposledy nerozuměl</h2>{lost}{groups}")


