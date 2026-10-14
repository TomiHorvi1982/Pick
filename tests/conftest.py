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
    # …and the songbook's own texts and band genres.
    import songbook
    monkeypatch.setattr(songbook, "MINE", tmp_path / "songbook-mine.json")
    monkeypatch.setattr(songbook, "BANDS", tmp_path / "songbook-bands.json")
    # …and the assistant never finds a real API key, nor spends anything.
    import assistant
    monkeypatch.setattr(assistant, "KEY_PATH", tmp_path / "anthropic-key.txt")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PICK_LLM_MODEL", raising=False)
    monkeypatch.setattr(assistant, "LLM_PATHS", [])        # …nor a model in this computer to load
    monkeypatch.setattr(assistant, "LLM_PID", tmp_path / "assistant-llm.pid")
    monkeypatch.setattr(assistant, "LEARNED", tmp_path / "assistant-learned.json")
    monkeypatch.setattr(assistant, "UNKNOWN", tmp_path / "assistant-unknown.json")
    assistant._turns.clear(); assistant._last.update(miss=None, learned=None)
    assistant._sessions.clear()
    # …and the look of the app is the test's own too.
    import look
    monkeypatch.setattr(look, "LOOK_PATH", tmp_path / "look.json")
    monkeypatch.setattr(look, "LOOK_DIR", tmp_path / "look")
    # …and the composer's projects; its model is never started by a test.
    import studio
    monkeypatch.setattr(studio, "PROJECTS", tmp_path / "studio-projects")
    monkeypatch.setattr(studio, "ACE_DIR", tmp_path / "no-ace")
    monkeypatch.setattr(studio, "ENGINE_PID", tmp_path / "studio-engine.pid")
    monkeypatch.setattr(studio, "ENGINE_LOG", tmp_path / "studio-engine.log")
    monkeypatch.setattr(studio, "PORT", 1)
    monkeypatch.setattr(studio, "URL", "http://127.0.0.1:1")
