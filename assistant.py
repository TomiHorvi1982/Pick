"""Pick's assistant: a sentence said in Czech becomes calls of the app's own actions.

Claude (Anthropic API) reads what was said, together with SYSTEM — the one
description of Pick it ever sees — and TOOLS, and answers with the tools to
call. The tools themselves run in the page (static/index.html, Voice.assist):
that is where the player and the mixer live. So one spoken request is a few
round trips: say() → the page runs the calls → results() → … until the model
has nothing more to call and says what it did.

The API key stays on this computer, in KEY_PATH or ANTHROPIC_API_KEY, and is
handed only to the worker process that talks to the API. The Anthropic library
wants newer versions of packages the server itself pins, so it lives in an
environment of its own (WORKER_PYTHON) and each call runs assistant_worker.py
there.

Keep SYSTEM and TOOLS true to the app: when Pick learns something new that the
assistant should be able to do, it goes in here, or the assistant will not
know it exists.
"""
import json
import os
import subprocess
import threading
import time
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
TOOL_NAMES = {t["name"] for t in TOOLS}


def key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k:
        try:
            k = KEY_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            k = ""
    return k


def available():
    """Why the assistant can't run here, or None when it can."""
    if not WORKER_PYTHON.exists():
        return "Chybí prostředí asistenta (~/.local/venvs/pick-assistant)."
    if not key():
        return f"Chybí API klíč. Ulož ho do souboru {KEY_PATH} (jen klíč, nic dalšího)."
    return None


def ask(messages, call=None):
    """One request to the model: {"content": [blocks], "stop_reason", "usage"} or {"error"}."""
    if call:
        return call(messages)
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
