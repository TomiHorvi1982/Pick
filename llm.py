"""The language models Skladatel's lyricist writes with (lyricist.py): Claude over the Anthropic
API — the key stays on this computer, in KEY_PATH or ANTHROPIC_API_KEY, and the call is made in
an environment of its own (WORKER_PYTHON, where the `anthropic` package is) — or, without the
key, a small model in this computer under llama-server, started when asked and let go when idle.

What is left of Pick's assistant (removed 3. 11.: it answered spoken requests); the models stayed.
"""
import glob
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
KEY_PATH = HOME / ".local/share/spotdl-helper/anthropic-key.txt"
WORKER_PYTHON = HOME / ".local/venvs/pick-assistant/bin/python"
WORKER = Path(__file__).with_name("lyrics_worker.py")


def key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k:
        try:
            k = KEY_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            k = ""
    return k



# ---- the model in this computer
LLM_BIN = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
LLM_FILE = "google_gemma-3-4b-it-Q4_K_M.gguf"
LLM_PATHS = [str(HOME / ".local/share/spotdl-helper/models" / LLM_FILE), "/Volumes/*/pick-models/" + LLM_FILE]
LLM_PORT = 8793
LLM_IDLE = 900                      # seconds without a request before the model is let go (it holds some 3.5 GB)
LLM_PID = HOME / ".local/share/spotdl-helper/assistant-llm.pid"
_llm = {"proc": None, "used": 0.0, "watch": None}
_llm_lock = threading.Lock()


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
