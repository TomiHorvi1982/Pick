"""The remote control the menu bar and the mini player speak through.

Playback lives in the browser window — a second window has no access to this
one's audio element or Web Audio graph — so everything here is a message
passed between them.
"""

import server


def test_state_starts_stale(client):
    """A menu bar that claimed a song was playing before any window said so
    would be lying."""
    server._REMOTE_STATE["at"] = 0
    data = client.get("/api/remote/state").json()
    assert data["stale"] is True
    assert data["playing"] is False


def test_the_playing_window_publishes_what_it_is_doing(client):
    client.post("/api/remote/state", json={
        "title": "Erased", "artists": "Sylosis", "playing": True,
        "volume": 0.8, "position": 12.5, "duration": 287.0, "source": "player",
    })

    data = client.get("/api/remote/state").json()
    assert data["title"] == "Erased"
    assert data["playing"] is True
    assert data["volume"] == 0.8
    assert data["stale"] is False


def test_a_window_that_went_away_goes_stale(client, monkeypatch):
    client.post("/api/remote/state", json={"title": "Erased", "playing": True})
    # Twelve seconds of silence from the window is enough to stop believing it.
    server._REMOTE_STATE["at"] -= 20

    assert client.get("/api/remote/state").json()["stale"] is True


def test_commands_queue_and_come_back_once(client):
    server._REMOTE_COMMANDS.clear()
    first = client.post("/api/remote/command", json={"action": "playpause"}).json()
    client.post("/api/remote/command", json={"action": "volume", "value": 0.4})

    all_of_them = client.get("/api/remote/commands", params={"since": 0}).json()
    assert [c["action"] for c in all_of_them["commands"]] == ["playpause", "volume"]
    assert all_of_them["commands"][1]["value"] == 0.4

    # The window acknowledges by asking for what came after what it has seen.
    rest = client.get("/api/remote/commands",
                      params={"since": all_of_them["seq"]}).json()
    assert rest["commands"] == []
    assert first["seq"] < all_of_them["seq"]


def test_an_unknown_command_is_refused(client):
    """The action goes straight into a switch in the browser; anything not on
    the list has no business being queued."""
    res = client.post("/api/remote/command", json={"action": "rm -rf"})
    assert res.status_code == 400


def test_the_queue_does_not_grow_without_bound(client):
    server._REMOTE_COMMANDS.clear()
    for _ in range(server._REMOTE_KEEP + 25):
        client.post("/api/remote/command", json={"action": "next"})

    assert len(server._REMOTE_COMMANDS) == server._REMOTE_KEEP


def test_every_action_the_browser_handles_is_allowed(client):
    """The two lists have to agree, or a button in the mini player does
    nothing and says nothing."""
    for action in ("playpause", "play", "pause", "next", "prev",
                   "volume", "seek", "stop", "mute"):
        assert action in server.REMOTE_ACTIONS
        assert client.post("/api/remote/command",
                           json={"action": action}).status_code == 200


def test_the_mini_player_is_served(client):
    res = client.get("/mini")
    assert res.status_code == 200
    assert "Pick — mini" in res.text
    # It must be its own page, not the app: loading the whole app in a
    # 360px window was the thing this exists to avoid.
    assert "sp-shell" not in res.text


# ------------------------------------------------------------- the menu bar

def _menubar():
    """menubar.py without starting a status item — the label logic is the
    part worth testing, and rumps needs a run loop."""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "menubar_under_test", Path(__file__).resolve().parents[1] / "menubar.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["menubar_under_test"] = module
    spec.loader.exec_module(module)
    return module


class _Label:
    def __init__(self):
        self.title = ""


class _Fake:
    """Just enough of the app for refresh() and on_open_app() to run against."""
    def __init__(self, mb):
        self.title = mb.IDLE_TITLE
        self.item_now = _Label()
        self.item_playpause = _Label()
        self.window = None
        self.mini_window = None
        self.window_opened_at = 0.0
        self.started = 0

    def ensure_server(self):
        self.started += 1

    def app_window_open(self):
        import menubar_under_test as mb
        return mb.Pick.app_window_open(self)


def _refresh_with(mb, app, state):
    """Run refresh() against a fixed answer from the server."""
    original = mb._get
    mb._get = lambda *a, **kw: state
    try:
        mb.Pick.refresh(app, None)
    finally:
        mb._get = original


def test_a_long_title_is_clipped_to_fit_the_menu_bar():
    """A song title in the menu bar pushes every other icon along."""
    mb = _menubar()
    app = _Fake(mb)
    _refresh_with(mb, app, {
        "title": "Down with the Sickness (Remastered Deluxe)",
        "artists": "Disturbed", "playing": True, "stale": False})

    assert len(app.item_now.title) <= mb.MAX_TITLE
    assert app.item_now.title.endswith("…")
    assert app.title == mb.PLAYING_TITLE
    assert app.item_playpause.title == "Pauza"


def test_a_short_title_keeps_its_artist():
    mb = _menubar()
    app = _Fake(mb)
    _refresh_with(mb, app, {"title": "Erased", "artists": "Sylosis",
                            "playing": False, "stale": False})

    assert app.item_now.title == "Erased — Sylosis"
    assert app.title == mb.IDLE_TITLE          # paused is not playing
    assert app.item_playpause.title == "Přehrát"


def test_nothing_playing_shows_the_idle_glyph():
    mb = _menubar()
    app = _Fake(mb)
    _refresh_with(mb, app, {"stale": True})

    assert app.title == mb.IDLE_TITLE
    assert app.item_now.title == "Nic nehraje"


def test_a_server_that_is_not_answering_does_not_crash_the_menu():
    mb = _menubar()
    app = _Fake(mb)
    _refresh_with(mb, app, None)

    assert app.item_now.title == "Nic nehraje"


def test_the_app_window_is_sized_to_the_screen():
    """--start-fullscreen is ignored often enough in --app mode that the app
    opened in a small default window, which is no way to look at a mixer."""
    mb = _menubar()
    w, h, x, y = mb._screen_area()

    assert w >= 800 and h >= 400          # a real screen, not a fallback of zeros
    # Whatever the screen is, the window is never smaller than the mixer needs.
    assert max(w, mb.MIN_APP_SIZE[0]) >= mb.MIN_APP_SIZE[0]
    assert max(h, mb.MIN_APP_SIZE[1]) >= mb.MIN_APP_SIZE[1]


def test_an_open_window_is_raised_not_duplicated():
    """Opening a second window on top of the first helps nobody."""
    mb = _menubar()
    app = _Fake(mb)
    app.window = None
    app.mini_window = None
    app.window_opened_at = 0.0

    opened = []
    raised = []
    original_window, original_raise, original_get = (
        mb._chrome_window, mb._raise_chrome, mb._get)
    mb._chrome_window = lambda *a, **kw: opened.append(a) or None
    mb._raise_chrome = lambda: raised.append(True)
    # A window that is publishing state is a window that is open.
    mb._get = lambda *a, **kw: {"stale": False, "title": "Erased"}
    try:
        mb.Pick.on_open_app(app, None)
    finally:
        mb._chrome_window, mb._raise_chrome, mb._get = (
            original_window, original_raise, original_get)

    assert raised == [True]
    assert opened == []


def test_with_no_window_open_one_is_launched():
    mb = _menubar()
    app = _Fake(mb)
    app.window = None
    app.mini_window = None
    app.window_opened_at = 0.0

    opened = []
    original_window, original_raise, original_get = (
        mb._chrome_window, mb._raise_chrome, mb._get)
    mb._chrome_window = lambda *a, **kw: opened.append(kw) or None
    mb._raise_chrome = lambda: None
    mb._get = lambda *a, **kw: {"stale": True}
    try:
        mb.Pick.on_open_app(app, None)
    finally:
        mb._chrome_window, mb._raise_chrome, mb._get = (
            original_window, original_raise, original_get)

    assert len(opened) == 1
    assert opened[0]["size"][0] >= mb.MIN_APP_SIZE[0]
    assert opened[0]["size"][1] >= mb.MIN_APP_SIZE[1]


def test_state_carries_the_video_and_queue_key(client):
    # The mini player shows the playing video and marks the queue entry by these.
    client.post("/api/remote/state", json={"title": "T", "playing": True,
                                           "videoId": "abc123", "key": "yt:abc123"})
    st = client.get("/api/remote/state").json()
    assert st["videoId"] == "abc123" and st["key"] == "yt:abc123"


def test_the_mini_player_can_start_a_queue_entry(client):
    # Clicking a song in the mini player's queue sends this; it used to be
    # refused as an unknown command, so nothing ever played.
    res = client.post("/api/remote/command", json={"action": "playqueue", "value": 2})
    assert res.status_code == 200
    cmds = client.get("/api/remote/commands?since=0").json()["commands"]
    assert {"action": "playqueue", "value": 2} in cmds


def test_mini_resize_asks_chrome_for_the_mini_window(client, monkeypatch):
    # The size buttons of the mini player: the server tells Chrome (via
    # AppleScript) to resize the window showing /mini, sizes kept sane.
    import server
    calls = []

    class Done:
        stdout = "ok"

    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    res = client.post("/api/mini/resize", json={"width": 10, "height": 99999})
    assert res.json() == {"ok": True}
    script = calls[0][2]
    assert "/mini" in script and "- 240" in script and "+ 1600" in script
