"""Úlohy a procesy — every process of the computer with what it costs now, so the server
can say what each of Pick's jobs takes (a separation is Demucs and what it started, a tone
print is its worker and the Node processes under it…) and pause, go on with or stop it.

macOS's own `ps` is read (nothing to install). The processor share is measured, not taken
from ps's decaying average: the CPU time each process has used, against the last look —
"100 %" is one core busy the whole time. A process paused here (SIGSTOP) is what ps shows
in state T; that is how a paused job is told after the page asks again.
"""
import os
import signal
import subprocess
import threading
import time

_LAST = {"at": 0.0, "rows": {}}
_LOCK = threading.Lock()


def _seconds(text: str) -> float:
    """ps's CPU time ("0:12.34", "16:00.51", "1:02:03.45", "2-01:02:03") in seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    total = 0.0
    for part in text.replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return days * 86400 + total


def table() -> dict:
    """{pid: {"ppid", "rss" bytes, "cpu" % of one core, "stat", "args"}} — every process now."""
    try:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid=,rss=,%cpu=,time=,stat=,args="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    now, rows = time.time(), {}
    for line in out.splitlines():
        p = line.split(None, 6)
        if len(p) < 6:
            continue
        args = p[6] if len(p) > 6 else ""
        if args.startswith("/bin/ps ") and p[1] == str(os.getpid()):
            continue                                       # this very look (and the load meter's), not a job
        try:
            rows[int(p[0])] = {"ppid": int(p[1]), "rss": int(p[2]) * 1024, "avg": float(p[3].replace(",", ".")),
                               "ct": _seconds(p[4]), "stat": p[5], "args": args}
        except ValueError:
            continue
    with _LOCK:
        prev, dt = _LAST["rows"], now - _LAST["at"]
        for pid, r in rows.items():
            q = prev.get(pid)
            if q is None or not prev:
                r["cpu"] = r["avg"]                         # first look at it: ps's own average
            elif dt < 0.7:
                r["cpu"] = q.get("cpu", r["avg"])           # asked again at once: the last measure
            else:
                r["cpu"] = max(0.0, (r["ct"] - q["ct"]) / dt * 100)
        if dt >= 0.7 or not prev:
            _LAST.update(at=now, rows=rows)
    return rows


def tree(rows: dict, root: int) -> set:
    """A process and everything it started, and what those started."""
    kids: dict = {}
    for pid, r in rows.items():
        kids.setdefault(r["ppid"], []).append(pid)
    out, todo = set(), [root]
    while todo:
        p = todo.pop()
        if p in rows and p not in out:
            out.add(p)
            todo += kids.get(p, [])
    return out


def matching(rows: dict, among: set, *words: str) -> set:
    """Of `among`, the processes whose command line has one of `words` in it — with all they started."""
    hits = {pid for pid in among if pid != os.getpid() and any(w in rows[pid]["args"] for w in words)}
    out = set()
    for pid in hits:
        out |= tree(rows, pid)
    return out


def paused(rows: dict, pids: set) -> bool:
    return bool(pids) and all(rows[p]["stat"].startswith("T") for p in pids if p in rows)


def send(pids, sig) -> int:
    """A signal to every one of them (never to this server itself); how many got it."""
    n = 0
    for pid in pids:
        if pid == os.getpid() or pid <= 1:
            continue
        try:
            os.kill(pid, sig)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    return n


def pause(pids) -> int:
    return send(pids, signal.SIGSTOP)


def resume(pids) -> int:
    return send(pids, signal.SIGCONT)


def end(pids, wait: float = 3.0) -> int:
    """Asked to end (woken first — a paused process cannot hear it), then made to."""
    pids = [p for p in pids if p != os.getpid() and p > 1]
    send(pids, signal.SIGCONT)
    n = send(pids, signal.SIGTERM)
    limit = time.time() + wait
    while time.time() < limit:
        if not any(_alive(p) for p in pids):
            return n
        time.sleep(0.1)
    send([p for p in pids if _alive(p)], signal.SIGKILL)
    return n


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:                                                   # a zombie (ended, not yet collected) is gone too
        st = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=2).stdout.strip()
        return bool(st) and not st.startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return True
