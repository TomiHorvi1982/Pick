import os
import sys
from pathlib import Path

# No background watchers (the Moises inbox) in tests.
os.environ.setdefault("SPOTDL_NO_WATCHERS", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def tmp_music_root(tmp_path, monkeypatch):
    """Points server.MUSIC_ROOT at a throwaway directory for the test, so
    tests never touch the real ~/Music/spotdl (or whatever the user has
    configured).
    """
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setattr(server, "MUSIC_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _no_real_separations(monkeypatch):
    """Separations queue up but never start Demucs inside a test."""
    monkeypatch.setattr(server, "_STEMS_AUTOSTART", False)
    monkeypatch.setattr(server, "_STEMS_QUEUE", [])


@pytest.fixture(autouse=True)
def _own_config(tmp_path, monkeypatch):
    """Every test gets an empty config of its own — the user's real one
    (mapped folders, ToneRig, …) must neither leak in nor be written to."""
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    # …and so do the user's own voice commands and what they taught by voice.
    import voice
    monkeypatch.setattr(voice, "CUSTOM_PATH", tmp_path / "voice-commands.json")
    monkeypatch.setattr(voice, "TAUGHT_PATH", tmp_path / "voice-taught.json")
    monkeypatch.setattr(voice, "TAKES_DIR", tmp_path / "voice-takes")
    monkeypatch.setattr(voice, "WIDGET_PATH", tmp_path / "voice-widget.json")
    # …and the assistant never finds a real API key, nor spends anything.
    import assistant
    monkeypatch.setattr(assistant, "KEY_PATH", tmp_path / "anthropic-key.txt")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assistant._sessions.clear()
