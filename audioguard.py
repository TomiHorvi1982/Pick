"""Ochrana zvuku — while music or the guitar sounds in Pick, the heavy jobs step back, so the
sound never breaks up because the computer is busy with something else.

Every heavy process Pick started (Demucs, whisper, the tone print's worker and its Node,
the singing and voice models, the analyses, the downloads, Skladatel's model) goes into
macOS's background band — `taskpolicy -b`: the efficiency cores, its disk and network
throttled, below everything the player is doing — and is given back (`taskpolicy -B`) the
moment the sound stops. In the "pause" mode the ones that can wait without harm (separation,
hearing, the tone print, analyses, downloads) are stopped outright (SIGSTOP) and go on where
they were afterwards; the rest is only slowed. Nothing needs root, nothing is lost: a slowed
job takes longer, a paused one picks up again.

The page says it is sounding (POST /api/audio/active) every few seconds; twelve seconds
without a word from any window count as silence.
"""
import os
import signal
import subprocess
import threading
import time

import tasks as _tasks

# what steps back: Pick's own heavy programs, by their command line
SLOW = ("demucs", "whisper-cli", "tone_worker.py", "tone_nam.mjs", "svc_worker.py", "tts_worker.py", "resing_worker.py",
        "analyze.py", "audio2midi.py", "assistant_worker.py", "lyrics_worker.py", "/bin/spotdl", "yt-dlp", "yt_dlp")
# …and of those, what may be stopped outright — nothing waits on them with a clock running
PAUSABLE = ("demucs", "whisper-cli", "tone_worker.py", "tone_nam.mjs", "analyze.py", "audio2midi.py", "/bin/spotdl", "yt-dlp", "yt_dlp")
TASKPOLICY = "/usr/sbin/taskpolicy"
EVERY = 2.0
QUIET_AFTER = 12.0

STATE = {"on": True, "mode": "slow", "clients": {}, "held": {}, "what": {}}
_LOCK = threading.Lock()
_STARTED = {"thread": None}
extra = lambda: []          # the server: heavy processes it did not start itself (Skladatel's model)


def ping(client: str, on: bool = True) -> None:
    """A window says it is sounding (or has stopped)."""
    with _LOCK:
        if on:
            STATE["clients"][client] = time.time() + QUIET_AFTER
        else:
            STATE["clients"].pop(client, None)
    if not on:
        step()                                             # given back at once, not in two seconds


def active() -> bool:
    now = time.time()
    with _LOCK:
        STATE["clients"] = {c: t for c, t in STATE["clients"].items() if t > now}
        return bool(STATE["on"] and STATE["clients"])


def configure(on=None, mode=None) -> None:
    if on is not None:
        STATE["on"] = bool(on)
    if mode in ("slow", "pause"):
        STATE["mode"] = mode
    step()


def _policy(pid: int, background: bool) -> None:
    try:
        subprocess.run([TASKPOLICY, "-b" if background else "-B", "-p", str(pid)], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


def _heavy(rows: dict) -> dict:
    """{pid: "pause" | "slow"} — what should step back now, and how."""
    me = os.getpid()
    mine = _tasks.tree(rows, me)
    slow = _tasks.matching(rows, mine, *SLOW)
    for p in extra() or []:
        if p in rows:
            slow |= _tasks.tree(rows, p)
    slow.discard(me)
    if STATE["mode"] != "pause":
        return {p: "slow" for p in slow}
    pause = _tasks.matching(rows, mine, *PAUSABLE)
    return {p: "pause" if p in pause else "slow" for p in slow}


def step() -> None:
    """Put back what no longer has to step back, and send back what has to."""
    want = {}
    rows = _tasks.table()
    if active():
        want = _heavy(rows)
    with _LOCK:
        held = STATE["held"]
        for pid in list(held):
            if want.get(pid) != held[pid]:
                if pid in rows:
                    if held[pid] == "pause":
                        _tasks.resume([pid])
                    else:
                        _policy(pid, False)
                held.pop(pid, None)
                STATE["what"].pop(pid, None)
        for pid, how in want.items():
            if pid in held:
                continue
            if how == "pause":
                _tasks.send([pid], signal.SIGSTOP)
            else:
                _policy(pid, True)
            held[pid] = how
            STATE["what"][pid] = rows[pid]["args"] if pid in rows else ""


def wake_orphans() -> None:
    """A Pick that ended without giving back (killed, crashed) may have left a job of its own
    stopped: one whose parent is gone and that is still in state T is let go on."""
    rows = _tasks.table()
    for pid, r in rows.items():
        if r["ppid"] == 1 and r["stat"].startswith("T") and any(w in r["args"] for w in PAUSABLE):
            _tasks.resume([pid])


def release() -> None:
    """Everything given back (the server going down)."""
    with _LOCK:
        STATE["clients"].clear()
    step()


def view(label=lambda args: args) -> dict:
    with _LOCK:
        held = [{"pid": p, "name": label(STATE["what"].get(p, "")), "how": h} for p, h in STATE["held"].items()]
    return {"on": STATE["on"], "mode": STATE["mode"], "active": active(), "held": held}


def _loop() -> None:
    while True:
        try:
            step()
        except Exception:                                    # noqa: BLE001 — the guard never stops guarding
            pass
        time.sleep(EVERY)


def start() -> None:
    t = _STARTED["thread"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_loop, name="audioguard", daemon=True)
        _STARTED["thread"] = t
        t.start()
