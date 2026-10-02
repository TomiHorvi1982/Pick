import sys
from pathlib import Path

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
