"""Skladatel's lyricist: a song's words from a few of the player's own.

Writes a whole text from what the song is about, finishes the lines the player
has started, or tidies them up — always in the shape the composing model sings
best (studio_words.TIPS_LYRICS): parts in [brackets], 6–10 syllables a line,
lines of a kind about as long as each other, one image carried through, the
chorus that comes back, no heap of adjectives. It also suggests the sound in
English words the model knows, to go with the text.

Claude writes it (the Anthropic API, through lyrics_worker.py in the
assistant's environment — the key never leaves the server); without the key,
the small model in this computer does (the assistant's llama-server), which is
weaker in Czech.
"""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import assistant

MODEL = "claude-opus-5-5"
WORKER = assistant.WORKER.with_name("lyrics_worker.py")


def available() -> list:
    """Who can write here, the best first: "claude", "local"."""
    out = []
    if assistant.WORKER_PYTHON.exists() and assistant.key():
        out.append("claude")
    if assistant.llm_model() and Path(assistant.LLM_BIN).exists():
        out.append("local")
    return out


SYSTEM = """You are a professional songwriter writing lyrics for a song that an AI music model (ACE-Step 1.5) will sing.
The player is a rock and metal guitarist from the Czech Republic. Write the way a real band's lyricist would.

How the model reads lyrics, and so how you must write them:
- Mark every part with a tag in square brackets on its own line, in English: [Intro], [Verse 1], [Pre-Chorus], [Chorus],
  [Verse 2], [Bridge], [Guitar Solo], [Breakdown], [Final Chorus], [Outro]. A blank line between parts.
- At most one short hint after a dash in a tag when it matters: [Chorus - anthemic], [Bridge - whispered]. Never more.
- 6 to 10 syllables a line (Czech counts its syllables the same way). Lines in the same place of each verse about
  the same length (±2 syllables). Every line must be singable in one breath.
- The chorus is short, strong and comes back word for word (the final chorus may change one line).
- CAPITALS only for a word or line that is shouted. (Parentheses) only for backing vocals or echoes.
- One central image or metaphor for the whole song, looked at from several sides. No heaps of adjectives,
  no clichés like "neon skies, electric hearts", no mixed metaphors.
- Rhymes natural, never forced at the cost of meaning. In Czech: natural word order, everyday spoken words where they fit,
  correct grammar and diacritics, stress falls on the first syllable — put strong syllables on the beat.
- Instrumental parts are just a tag with nothing under it ([Guitar Solo], [Intro]).
- Fit the length: roughly 2–2.5 sung words per second of singing, and leave room for the intro, solo and outro.
- If the song starts with the player's own instrumental idea, begin with [Intro] and nothing under it.

Answer in exactly this form, nothing before or after:
TITLE: <the song's title, in the song's language>
SOUND: <a description of the music in English for the model: genre, instruments, vocals, mood, production — comma-separated,
        consistent with the lyrics, no tempo or key>
LYRICS:
<the lyrics>"""


def prompt(req: dict, langs: dict) -> str:
    lang = langs.get(req.get("lang") or "cs", "česky")
    length = max(20, min(600, int(req.get("length") or 90)))
    parts = [f"Language of the lyrics: {lang} (code {req.get('lang') or 'cs'}).",
             f"Song length: about {length} seconds{' — the song begins with the player’s instrumental idea' if req.get('idea') else ''}."]
    if (req.get("desc") or "").strip():
        parts.append(f"The music already chosen (keep the lyrics consistent with it): {req['desc'].strip()}")
    if (req.get("mood") or "").strip():
        parts.append(f"Mood / style of the words: {req['mood'].strip()}")
    if (req.get("about") or "").strip():
        parts.append(f"What the song is about (the player's words): {req['about'].strip()}")
    if (req.get("words") or "").strip():
        parts.append(f"Words or lines that must appear: {req['words'].strip()}")
    parts.append("Rhyme the lines." if req.get("rhyme", True) else "No need to rhyme; rhythm and meaning first.")
    mode, text = req.get("mode") or "write", (req.get("text") or "").strip()
    if mode == "complete" and text:
        parts.append("The player has started the lyrics. Keep every line they wrote exactly as it is and in its place, "
                     "add the tags where they are missing, and write the rest of the song around it:\n\n" + text)
    elif mode == "polish" and text:
        parts.append("Rework these lyrics so the model sings them well: keep their meaning, story and best lines, fix syllable "
                     "counts, rhythm, rhymes and tags, cut what is weak:\n\n" + text)
    else:
        parts.append("Write the whole song.")
    return "\n".join(parts)


def parse(text: str) -> dict:
    """The answer in its form → {title, sound, lyrics}."""
    text = (text or "").strip()
    title = re.search(r"^\s*TITLE:\s*(.+)$", text, re.M)
    sound = re.search(r"^\s*SOUND:\s*(.+)$", text, re.M)
    body = re.split(r"^\s*LYRICS:\s*$", text, maxsplit=1, flags=re.M)
    lyrics = body[1].strip() if len(body) > 1 else re.sub(r"^\s*(TITLE|SOUND):.*$", "", text, flags=re.M).strip()
    lyrics = re.sub(r"^```\w*\s*|\s*```$", "", lyrics).strip()
    lyrics = tidy(lyrics)
    return {"title": title.group(1).strip().strip('"„“') if title else "", "sound": sound.group(1).strip() if sound else "",
            "lyrics": lyrics}


def tidy(lyrics: str) -> str:
    """What a weaker model leaves in that the singer would sing: a language mark after a line
    ("… [cs]"), stage directions in parentheses ("(Fast, melodic solo …)"), a part tagged twice."""
    out = []
    for line in lyrics.splitlines():
        line = re.sub(r"\s*\[(cs|en|sk|de|pl|es|fr|it|ru)\]\s*$", "", line.rstrip(), flags=re.I)
        bare = line.strip()
        n = len(bare.split())
        if re.fullmatch(r"\(([^()]*)\)", bare) and (n > 5 or (n >= 4 and "," in bare) or "–" in bare or " - " in bare):
            continue                                       # a direction, not a backing vocal
        if bare.startswith("[") and out and out[-1].strip().startswith("[") and out[-1].strip().split(" -")[0].rstrip("]") == bare.split(" -")[0].rstrip("]"):
            out[-1] = line                                 # "[Bridge]" then "[Bridge - whispered]": the second one
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _claude(req: dict, langs: dict) -> dict:
    try:
        out = subprocess.run([str(assistant.WORKER_PYTHON), str(WORKER)],
                             input=json.dumps({"system": SYSTEM, "prompt": prompt(req, langs), "model": MODEL}),
                             capture_output=True, text=True, timeout=240,
                             env={**os.environ, "ANTHROPIC_API_KEY": assistant.key()})
        got = json.loads(out.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "Psaní textu trvalo moc dlouho."}
    except (ValueError, OSError):
        return {"error": "Textař se nespustil."}
    if got.get("error"):
        return got
    return {**parse(got.get("text") or ""), "by": "Claude"}


def _local(req: dict, langs: dict) -> dict:
    why = assistant.llm_wake(wait=True)
    if why:
        return {"error": why}
    body = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(req, langs)}],
            "temperature": 0.8, "max_tokens": 1800}
    try:
        r = urllib.request.Request(f"http://127.0.0.1:{assistant.LLM_PORT}/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                   {"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=300) as resp:
            text = json.loads(resp.read())["choices"][0]["message"]["content"]
    except (OSError, ValueError, KeyError, IndexError):
        return {"error": "Model v počítači neodpověděl."}
    finally:
        import studio
        if studio.engine_up():                             # the composing model needs the memory more
            assistant.llm_stop()
    return {**parse(text), "by": "model v počítači"}


def write(req: dict, langs: dict) -> dict:
    who = available()
    if not who:
        return {"error": "Text nemá kdo napsat: chybí API klíč i model v počítači."}
    out = _claude(req, langs) if who[0] == "claude" else _local(req, langs)
    if out.get("error") and who[0] == "claude" and "local" in who:
        why = out["error"]
        out = _local(req, langs)
        if not out.get("error"):
            out["note"] = f"Claude nepsal ({why}) — napsal to model v počítači, česky je slabší."
    if not out.get("error") and not out.get("lyrics"):
        return {"error": "Textař nevrátil žádný text, zkus to znovu."}
    return out
