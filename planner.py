"""Naplánované úlohy — heavy jobs put off to the night (Nastavení → Systém → Naplánované úlohy).

Where Pick starts something heavy (a separation, a playlist to download and take apart, the
Zpěvník, hearing a text, a tone print, singing in another voice) the player is asked: now, or
tonight? Tonight keeps the very request the page would have sent (method, path, body) in
plan.json; in the night window (23:00–07:00 unless set otherwise) — or at once with "Spustit
teď" — the scheduler sends them to the server itself, one after another, each only when the
one before is done and nothing heavy runs (the separation queue empty, no song being saved, no
model busy). In the window it also waits while music plays. A job that has begun runs to its
end even past the window; the next one waits for the next night. Only the heavy jobs' own
paths can be planned. The server fills in the hooks below.
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

FILE = Path.home() / ".local" / "share" / "spotdl-helper" / "plan.json"
API = "http://127.0.0.1:8765"
ALLOWED = [re.compile(p) for p in (r"/api/stems/separate", r"/api/stems/separate-playlist", r"/api/stems/[^/]+/hear",
                                   r"/api/songbook/analyse", r"/api/practice/save-many", r"/api/tone/[^/]+/fit",
                                   r"/api/stems/[^/]+/myvoice")]
KINDS = {"separate": "Rozdělení na stopy", "saves": "Stažení a příprava písní", "songbook": "Rozbor Zpěvníku",
         "hear": "Odposlech textu", "tone": "Tónový otisk", "myvoice": "Zpěv jiným hlasem", "other": "Úloha"}
EVERY = 20.0
TRIES = 3                              # refused for want of room (503) or because something runs (409): again later
AGAIN_AFTER = 600.0
STATE = {"run_now": False, "current": None, "stop": False}
_LOCK = threading.Lock()
_STARTED = {"thread": None}
_AWAKE = {"proc": None}
CAFFEINATE = "/usr/bin/caffeinate"

idle = lambda: True                    # nothing heavy runs, no song waits to be saved
quiet = lambda: True                   # no music plays in a Pick window
settings = lambda: {}                  # {"on", "start", "end"}
outcome = lambda task, answer: (True, "")      # how the job just done ended, with a word on it


def send(method: str, path: str, body) -> tuple:
    r = httpx.request(method, API + path, json=body, timeout=900)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


def allowed(path: str) -> bool:
    return any(p.fullmatch(path) for p in ALLOWED)


def _load() -> dict:
    try:
        got = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(got, dict):
            got.setdefault("tasks", [])
            got.setdefault("history", [])
            return got
    except (OSError, ValueError):
        pass
    return {"tasks": [], "history": []}


def _save(d: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    d["history"] = d["history"][-40:]
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def tasks() -> dict:
    with _LOCK:
        return _load()


def add(kind: str, label: str, method: str, path: str, body) -> dict:
    kind = kind if kind in KINDS else "other"
    t = {"id": uuid.uuid4().hex[:10], "kind": kind, "label": (label or KINDS[kind])[:160], "method": method.upper(),
         "path": path, "body": body, "added": time.time(), "status": "waiting", "tries": 0}
    with _LOCK:
        d = _load()
        d["tasks"].append(t)
        _save(d)
    return t


def remove(tid: str) -> bool:
    """A waiting job let go (one under way is stopped where it runs, not here)."""
    with _LOCK:
        d = _load()
        keep = [t for t in d["tasks"] if t["id"] != tid or t["status"] == "running"]
        gone = len(keep) != len(d["tasks"])
        d["tasks"] = keep
        _save(d)
    return gone


def move(tid: str, delta: int) -> bool:
    with _LOCK:
        d = _load()
        ts = d["tasks"]
        i = next((k for k, t in enumerate(ts) if t["id"] == tid), None)
        if i is None:
            return False
        ts.insert(max(0, min(len(ts) - 1, i + delta)), ts.pop(i))
        _save(d)
    return True


def now(tid: str = "") -> bool:
    """Without waiting for the night: one job (it goes first), or all that wait."""
    if not tid:
        with _LOCK:
            d = _load()
            for t in d["tasks"]:
                t.pop("not_before", None)
            _save(d)
        STATE["run_now"] = True
        return True
    with _LOCK:
        d = _load()
        i = next((k for k, t in enumerate(d["tasks"]) if t["id"] == tid and t["status"] == "waiting"), None)
        if i is None:
            return False
        t = d["tasks"].pop(i)
        t["now"] = True
        t.pop("not_before", None)
        first = next((k for k, x in enumerate(d["tasks"]) if x["status"] != "running"), len(d["tasks"]))
        d["tasks"].insert(first, t)
        _save(d)
    return True


def clear_history() -> None:
    with _LOCK:
        d = _load()
        d["history"] = []
        _save(d)


def _minutes(hhmm: str, default: int) -> int:
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except ValueError:
        return default
    return default if not (0 <= h < 24 and 0 <= m < 60) else h * 60 + m


def in_window(at: float = None) -> bool:
    """Whether it is the night the player gave (start–end, over midnight or not)."""
    cfg = settings() or {}
    lt = time.localtime(time.time() if at is None else at)
    t = lt.tm_hour * 60 + lt.tm_min
    a, b = _minutes(cfg.get("start", "23:00"), 23 * 60), _minutes(cfg.get("end", "07:00"), 7 * 60)
    if a == b:
        return True                                     # the whole day
    return a <= t < b if a < b else (t >= a or t < b)


def _finish(tid: str, status: str, note: str = "") -> None:
    with _LOCK:
        d = _load()
        for t in d["tasks"]:
            if t["id"] == tid:
                t.update(status=status, finished=time.time(), note=(note or "")[:300])
                t.pop("now", None)
                d["history"].append(t)
        d["tasks"] = [t for t in d["tasks"] if t["id"] != tid]
        _save(d)
    STATE["current"] = None


def _later(tid: str, note: str) -> None:
    """Refused for now: back among the waiting — after a few times, given up."""
    with _LOCK:
        d = _load()
        t = next((x for x in d["tasks"] if x["id"] == tid), None)
        if t is not None:
            t["tries"] = int(t.get("tries") or 0) + 1
            t.update(status="waiting", note=note[:300], not_before=time.time() + AGAIN_AFTER)
            if t["tries"] >= TRIES:
                t.update(status="error", finished=time.time(), note=f"Nešlo spustit ani na {TRIES}. pokus: {note}"[:300])
                t.pop("now", None)
                d["history"].append(t)
                d["tasks"] = [x for x in d["tasks"] if x["id"] != tid]
        _save(d)
    STATE["current"] = None


def _awake(on: bool) -> None:
    """While planned jobs are due, the Mac does not fall asleep (caffeinate -i, gone with Pick)."""
    p = _AWAKE["proc"]
    if on and (p is None or p.poll() is not None):
        try:
            _AWAKE["proc"] = subprocess.Popen([CAFFEINATE, "-i", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            _AWAKE["proc"] = None
    elif not on and p is not None:
        if p.poll() is None:
            p.terminate()
        _AWAKE["proc"] = None


def _wait_idle(limit: float = 36 * 3600, settle: float = 8, look: float = 15) -> bool:
    """Until the job just started is done: nothing heavy for two looks in a row."""
    end, calm = time.time() + limit, 0
    time.sleep(settle)                              # what was asked for takes a moment to show
    while time.time() < end and not STATE["stop"]:
        calm = calm + 1 if idle() else 0
        if calm >= 2:
            return True
        time.sleep(look)
    return False


def _pick() -> dict:
    """The job to start now, if any: the first waiting one when it is time, or one asked for now."""
    cfg = settings() or {}
    with _LOCK:
        d = _load()
        stale = [t for t in d["tasks"] if t["status"] == "running"]     # cut off by a restart: again
        for t in stale:
            t["status"] = "waiting"
        if stale:
            _save(d)
        waiting = [t for t in d["tasks"] if t["status"] == "waiting"]
    if not waiting:
        STATE["run_now"] = False
        _awake(False)
        return None
    night = cfg.get("on", True) and in_window()
    _awake(bool(night or STATE["run_now"] or any(t.get("now") for t in waiting)))
    ready = [t for t in waiting if float(t.get("not_before") or 0) <= time.time()]
    asked = [t for t in ready if t.get("now")]
    if asked:
        t = asked[0]
    elif ready and (STATE["run_now"] or (night and quiet())):
        t = ready[0]
    else:
        return None
    return t if idle() else None


def step() -> None:
    """The next planned job, when it is time and nothing heavy runs."""
    if STATE["current"]:
        return
    t = _pick()
    if t is None:
        return
    with _LOCK:
        d = _load()
        for x in d["tasks"]:
            if x["id"] == t["id"]:
                x.update(status="running", started=time.time())
        _save(d)
    STATE["current"] = t["id"]
    try:
        code, answer = send(t["method"], t["path"], t.get("body"))
    except httpx.HTTPError as e:
        _later(t["id"], f"Server neodpověděl ({e.__class__.__name__}).")
        return
    if code in (409, 503):
        _later(t["id"], str((answer or {}).get("error") or f"chyba {code}"))
        return
    if code >= 400:
        _finish(t["id"], "error", str((answer or {}).get("error") or f"chyba {code}"))
        return
    done = _wait_idle()
    if not done:
        _finish(t["id"], "error", "Nedoběhlo ani za 36 hodin.")
        return
    ok, note = outcome(t, answer if isinstance(answer, dict) else {})
    _finish(t["id"], "done" if ok else "error", note)


def _loop() -> None:
    while not STATE["stop"]:
        try:
            step()
        except Exception:                               # noqa: BLE001 — the plan never stops planning
            STATE["current"] = None
        time.sleep(EVERY)
    _awake(False)


def start() -> None:
    t = _STARTED["thread"]
    if t is None or not t.is_alive():
        STATE["stop"] = False
        t = threading.Thread(target=_loop, name="planner", daemon=True)
        _STARTED["thread"] = t
        t.start()


def view() -> dict:
    d = tasks()
    cfg = settings() or {}
    return {"tasks": d["tasks"], "history": list(reversed(d["history"])),
            "settings": {"on": bool(cfg.get("on", True)), "ask": bool(cfg.get("ask", True)),
                         "start": cfg.get("start", "23:00"), "end": cfg.get("end", "07:00")},
            "window": in_window(), "run_now": STATE["run_now"], "current": STATE["current"], "quiet": quiet()}
