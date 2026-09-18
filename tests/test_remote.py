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
    assert "Spotifylisty" in res.text
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
    """Just enough of the app for refresh() to run against."""
    def __init__(self, mb):
        self.title = mb.IDLE_TITLE
        self.item_now = _Label()
        self.item_playpause = _Label()


def _refresh_with(mb, app, state):
    """Run refresh() against a fixed answer from the server."""
    original = mb._get
    mb._get = lambda *a, **kw: state
    try:
        mb.Spotifylisty.refresh(app, None)
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
