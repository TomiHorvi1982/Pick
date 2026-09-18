"""Spotifylisty in the macOS menu bar.

Owns the server process and gives it three ways in: the app itself in a
full-screen window, the mini player, and a way to quit that actually stops
everything. Whatever is playing shows in the menu, because the title of a
menu bar item is the cheapest possible now-playing display.

It deliberately does *not* play anything. Playback lives in the browser
window — one audio graph, one owner — and everything here goes through the
remote-control endpoints, so pressing pause in the menu bar and pressing it
in the window are the same press.

    ~/.local/venvs/spotdl/bin/python menubar.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import rumps

APP_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("SPOTIFYLISTY_PORT", "8765"))
BASE = f"http://{HOST}:{PORT}"
SERVER_PYTHON = Path(os.environ.get(
    "SPOTIFYLISTY_PYTHON", str(Path.home() / ".local/venvs/spotdl/bin/python")))

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MINI_SIZE = (360, 300)

# The icon is one glyph, not a word: a menu bar is short and every app in it
# is competing for the same strip.
IDLE_TITLE = "♪"
PLAYING_TITLE = "♫"
# A song title in the menu bar pushes everything else along, so it is clipped.
MAX_TITLE = 28


def _get(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _post(path: str, payload: dict, timeout: float = 2.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{BASE}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _server_alive() -> bool:
    return _get("/api/remote/state", timeout=1.0) is not None


def _chrome_window(url: str, size=None, fullscreen=False) -> bool:
    """Open a chromeless Chrome window — no tabs, no address bar.

    --app is what makes it look like an application rather than a browser
    pointed at a local port, which is the whole difference between this
    feeling like software and feeling like a bookmark.
    """
    if not Path(CHROME).is_file():
        # Without Chrome the default browser still works, just with chrome.
        subprocess.Popen(["open", url])
        return False
    args = [CHROME, f"--app={url}", f"--user-data-dir={Path.home()}/.spotifylisty-chrome"]
    if size:
        args += [f"--window-size={size[0]},{size[1]}"]
    if fullscreen:
        args += ["--start-fullscreen"]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


class Spotifylisty(rumps.App):
    def __init__(self):
        super().__init__(IDLE_TITLE, quit_button=None)
        self.server: subprocess.Popen | None = None

        self.item_now = rumps.MenuItem("Nic nehraje")
        self.item_now.set_callback(None)          # a label, not a button
        self.item_playpause = rumps.MenuItem("Přehrát / pauza", callback=self.on_playpause)
        self.menu = [
            self.item_now,
            None,
            rumps.MenuItem("Otevřít aplikaci", callback=self.on_open_app),
            rumps.MenuItem("Mini přehrávač", callback=self.on_open_mini),
            None,
            self.item_playpause,
            rumps.MenuItem("Předchozí", callback=self.on_prev),
            rumps.MenuItem("Další", callback=self.on_next),
            None,
            rumps.MenuItem("Hlasitost 100 %", callback=self.on_volume(1.0)),
            rumps.MenuItem("Hlasitost 70 %", callback=self.on_volume(0.7)),
            rumps.MenuItem("Hlasitost 40 %", callback=self.on_volume(0.4)),
            rumps.MenuItem("Ztlumit", callback=self.on_mute),
            None,
            rumps.MenuItem("Ukončit Spotifylisty", callback=self.on_quit),
        ]

        self.ensure_server()

    # ------------------------------------------------------------- server

    def ensure_server(self):
        """Start the server unless one is already answering on the port."""
        if _server_alive():
            return
        if not SERVER_PYTHON.is_file():
            rumps.alert("Spotifylisty",
                        f"Chybí interpret serveru:\n{SERVER_PYTHON}")
            return
        self.server = subprocess.Popen(
            [str(SERVER_PYTHON), "-m", "uvicorn", "server:app",
             "--host", HOST, "--port", str(PORT)],
            cwd=str(APP_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for it rather than opening a window onto a connection refused.
        for _ in range(60):
            if _server_alive():
                return
            time.sleep(0.5)

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.send_signal(signal.SIGINT)
            try:
                self.server.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.server.kill()

    # ------------------------------------------------------------ actions

    def on_open_app(self, _):
        self.ensure_server()
        _chrome_window(BASE, fullscreen=True)

    def on_open_mini(self, _):
        self.ensure_server()
        _chrome_window(f"{BASE}/mini", size=MINI_SIZE)

    def on_playpause(self, _): _post("/api/remote/command", {"action": "playpause"})
    def on_prev(self, _): _post("/api/remote/command", {"action": "prev"})
    def on_next(self, _): _post("/api/remote/command", {"action": "next"})
    def on_mute(self, _): _post("/api/remote/command", {"action": "mute"})

    def on_volume(self, level: float):
        def handler(_):
            _post("/api/remote/command", {"action": "volume", "value": level})
        return handler

    def on_quit(self, _):
        self.stop_server()
        rumps.quit_application()

    # ------------------------------------------------------- now playing

    @rumps.timer(2)
    def refresh(self, _):
        state = _get("/api/remote/state", timeout=1.5)
        if not state or state.get("stale") or not state.get("title"):
            self.title = IDLE_TITLE
            self.item_now.title = "Nic nehraje"
            self.item_playpause.title = "Přehrát / pauza"
            return

        title = state.get("title") or ""
        artists = state.get("artists") or ""
        label = f"{title} — {artists}" if artists else title
        if len(label) > MAX_TITLE:
            label = label[:MAX_TITLE - 1].rstrip() + "…"
        self.item_now.title = label
        self.title = PLAYING_TITLE if state.get("playing") else IDLE_TITLE
        self.item_playpause.title = "Pauza" if state.get("playing") else "Přehrát"


def main():
    if not shutil.which("open"):
        print("Tohle běží jen na macOS.", file=sys.stderr)
        return 1
    Spotifylisty().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
