"""How hard the computer is working — memory, processor, Pick's own share — and
whether there is room for one more heavy job.

The models Pick runs (separating a song, singing in another voice, listening
to the vocals, composing) each want gigabytes. Two at once on a 16 GB Mac
push the system into swapping, and then everything, Pick included, stops
answering. So a sample is taken every few seconds (macOS's own tools: sysctl,
vm_stat, ps — nothing to install), the settings show it, and a heavy job asks
room() before it starts: a separation waits in its queue, one started by hand
is told why not yet.
"""
import os
import re
import subprocess
import threading
import time

GB = 1024 ** 3
EVERY = 3.0
_NOW: dict = {}
_LOCK = threading.Lock()
_STARTED = {"thread": None}
jobs = lambda: []          # the server says which heavy jobs are running: [Czech name]


def _sysctl(*names):
    try:
        out = subprocess.run(["/usr/sbin/sysctl", *names], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    got = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        got[k.strip()] = v.strip()
    return got


def _vm():
    try:
        out = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}, 16384
    page = int((re.search(r"page size of (\d+)", out) or [0, 16384])[1])
    rows = {}
    for line in out.splitlines()[1:]:
        k, _, v = line.partition(":")
        try:
            rows[k.strip().strip('"')] = int(v.strip().rstrip("."))
        except ValueError:
            pass
    return rows, page


def _procs():
    """[(pid, ppid, rss bytes, cpu %, name)] of every process."""
    try:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid=,rss=,%cpu=,comm="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        p = line.split(None, 4)
        if len(p) == 5:
            try:
                rows.append((int(p[0]), int(p[1]), int(p[2]) * 1024, float(p[3].replace(",", ".")), p[4]))
            except ValueError:
                pass
    return rows


def _swap(text):
    m = re.search(r"used = ([\d.,]+)M", text or "")
    return float(m[1].replace(",", ".")) * 1024 * 1024 if m else 0.0


def sample():
    s = _sysctl("hw.memsize", "hw.ncpu", "kern.memorystatus_vm_pressure_level", "vm.swapusage", "vm.loadavg")
    total = int(s.get("hw.memsize") or 0) or 16 * GB
    cpus = int(s.get("hw.ncpu") or 0) or os.cpu_count() or 1
    vm, page = _vm()
    avail = (vm.get("Pages free", 0) + vm.get("Pages inactive", 0) + vm.get("Pages speculative", 0)
             + vm.get("Pages purgeable", 0)) * page
    procs = _procs()
    # Pick: this server and everything it started (whisper, the models, ffmpeg…)
    kids, me = {}, os.getpid()
    for pid, ppid, *_ in procs:
        kids.setdefault(ppid, []).append(pid)
    mine, todo = set(), [me]
    while todo:
        p = todo.pop()
        if p not in mine:
            mine.add(p)
            todo += kids.get(p, [])
    pick_rss = sum(r for pid, _, r, _, _ in procs if pid in mine)
    pick_cpu = sum(c for pid, _, _, c, _ in procs if pid in mine)
    cpu = min(100.0, sum(c for *_, c, _ in procs) / cpus)
    pressure = {"1": 1, "2": 2, "4": 4}.get(s.get("kern.memorystatus_vm_pressure_level", "1"), 1)
    swap = _swap(s.get("vm.swapusage"))
    level = "ok"
    if pressure >= 4 or avail < 1.0 * GB:
        level = "tight"
    elif pressure >= 2 or avail < 2.5 * GB or cpu > 90:
        level = "busy"
    return {"at": time.time(), "total": total, "avail": avail, "used": max(0, total - avail), "pressure": pressure,
            "swap": swap, "cpu": round(cpu, 1), "cpus": cpus, "pick_rss": pick_rss, "pick_cpu": round(pick_cpu, 1),
            "load": [float(x.replace(",", ".")) for x in re.findall(r"[\d.,]+", s.get("vm.loadavg", ""))[:3]],
            "level": level}


def _loop():
    while True:
        try:
            got = sample()
            with _LOCK:
                prev = _NOW.get("swap")
                _NOW.clear()
                _NOW.update(got)
                _NOW["swap_growing"] = prev is not None and got["swap"] - prev > 200 * 1024 * 1024
        except Exception:
            pass
        time.sleep(EVERY)


def start():
    t = _STARTED["thread"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_loop, name="sysload", daemon=True)
        _STARTED["thread"] = t
        t.start()


def now():
    with _LOCK:
        got = dict(_NOW)
    if not got:
        got = sample()
    got["jobs"] = list(jobs())
    return got


def room(need_gb, what=""):
    """(True, "") when a job wanting `need_gb` may start now; (False, a Czech sentence why not) otherwise."""
    s = now()
    free = s["avail"] / GB
    if s["pressure"] >= 4:
        why = "Počítač má kriticky málo paměti"
    elif free < need_gb:
        why = f"Volná paměť je teď {free:.1f} GB{f', {what} potřebuje asi {need_gb:g} GB' if what else ''}"
    else:
        return True, ""
    busy = s.get("jobs") or []
    return False, why + (f" — běží: {', '.join(busy)}. Počkej, až doběhne." if busy else " — zavři něco náročného a zkus to znovu.")
