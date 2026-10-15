"""Poznámky (Nastavení → Další → Poznámky): what does not work in Pick and what to make better,
written down when it is noticed — a bug, an idea, a note — and handed over in one go: "Zkopírovat
pro Clauda" puts the open ones on the clipboard as a list. Kept in notes.json beside the settings,
so Claude can read them straight from there too.
"""
import json
import threading
import time
import uuid
from pathlib import Path

FILE = Path.home() / ".local" / "share" / "spotdl-helper" / "notes.json"
KINDS = {"chyba": "Chyby", "napad": "Nápady", "poznamka": "Poznámky"}
LIMIT = 4000                           # letters in one note
_LOCK = threading.Lock()


def _load() -> list:
    try:
        got = json.loads(FILE.read_text(encoding="utf-8"))
        return [n for n in got if isinstance(n, dict) and n.get("id")] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def listing() -> list:
    """The open ones first (the newest on top), then the done ones."""
    with _LOCK:
        rows = _load()
    return sorted(rows, key=lambda n: (bool(n.get("done")), -float(n.get("added") or 0)))


def add(text: str, kind: str = "poznamka") -> dict:
    text = (text or "").strip()[:LIMIT]
    if not text:
        raise ValueError("Poznámka je prázdná.")
    n = {"id": uuid.uuid4().hex[:10], "text": text, "kind": kind if kind in KINDS else "poznamka", "done": False,
         "added": time.time(), "changed": time.time()}
    with _LOCK:
        rows = _load()
        rows.append(n)
        _save(rows)
    return n


def edit(nid: str, text: str = None, kind: str = None, done: bool = None) -> dict:
    with _LOCK:
        rows = _load()
        n = next((x for x in rows if x["id"] == nid), None)
        if n is None:
            return None
        if text is not None:
            text = text.strip()[:LIMIT]
            if not text:
                raise ValueError("Poznámka je prázdná.")
            n["text"] = text
        if kind is not None:
            if kind not in KINDS:
                raise ValueError("Neznámý druh poznámky.")
            n["kind"] = kind
        if done is not None:
            n["done"] = bool(done)
        n["changed"] = time.time()
        _save(rows)
    return n


def remove(nid: str) -> bool:
    with _LOCK:
        rows = _load()
        keep = [x for x in rows if x["id"] != nid]
        if len(keep) == len(rows):
            return False
        _save(keep)
    return True


def markdown(all_of_them: bool = False) -> str:
    """The open notes (or all of them) as a message: by kind, each with its day."""
    rows = [n for n in listing() if all_of_them or not n.get("done")]
    if not rows:
        return ""
    day = lambda t: time.strftime("%-d. %-m.", time.localtime(float(t or 0)))
    out = [f"Poznámky z Picku ({time.strftime('%-d. %-m. %Y')}):"]
    for kind, title in KINDS.items():
        mine = sorted((n for n in rows if n["kind"] == kind), key=lambda n: float(n.get("added") or 0))
        if not mine:
            continue
        out += ["", f"## {title}"]
        for n in mine:
            lines = n["text"].splitlines() or [""]
            mark = "[x] " if n.get("done") else ""
            out.append(f"- {mark}{lines[0]} ({day(n.get('added'))})")
            out += [f"  {x}" for x in lines[1:]]
    return "\n".join(out)
