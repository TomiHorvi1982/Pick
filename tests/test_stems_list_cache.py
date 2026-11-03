"""The Steems list keeps each song's row until its folder changes."""
import os
import time

import server


def _song(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for s in ("drums", "bass", "vocals", "other"):
        (d / f"{s}.mp3").write_bytes(b"x" * 2000)
    return d


def test_rows_kept_until_the_folder_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    server._STEMS_ROWS.clear()
    d = _song(tmp_path, "Song A")
    first = server.list_stems()
    assert [r["name"] for r in first] == ["Song A"]
    calls = []
    real = server._stems_row
    monkeypatch.setattr(server, "_stems_row", lambda *a: calls.append(a) or real(*a))
    again = server.list_stems()
    assert again == first and not calls                    # nothing changed: nothing worked out again
    (d / "guitar.mp3").write_bytes(b"y" * 3000)             # a stem added
    third = server.list_stems()
    assert len(calls) == 1 and "guitar" in third[0]["stems"]
    (d / "meta.json").write_text('{"artist": "X", "title": "Y"}')
    os.utime(d / "meta.json", (time.time() + 5, time.time() + 5))
    assert server.list_stems()[0]["meta"]["artist"] == "X"
