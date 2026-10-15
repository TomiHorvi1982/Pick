"""Bluetooth MIDI: what macOS has, and the known pedals asked to connect again — a made-up helper
stands in for the real one (tools/midi_bt.swift), nothing is asked of the real Bluetooth."""
import os
import time

import server


def _fake(tmp_path, monkeypatch, body):
    tool = tmp_path / "midi-bt"
    tool.write_text("#!/bin/sh\n" + body + "\n")
    tool.chmod(0o755)
    src = tmp_path / "midi_bt.swift"
    src.write_text("// the source")
    old = time.time() - 100
    os.utime(src, (old, old))                       # the tool is newer than its source: nothing is built
    monkeypatch.setattr(server, "MIDI_BT", tool)
    monkeypatch.setattr(server, "MIDI_BT_SRC", src)


def test_what_macos_has_and_a_pedal_asked_again(client, tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, '''if [ "$1" = connect ]; then echo '{"asked": 0, "sources": ["iRig PRO DUO", "FS-1-WL Bluetooth"], "bluetooth": [{"name": "FS-1-WL", "online": true}]}';
else echo '{"sources": ["iRig PRO DUO"], "bluetooth": [{"name": "FS-1-WL", "online": false}]}'; fi''')
    got = client.get("/api/midi/system").json()
    assert got["bluetooth"] == [{"name": "FS-1-WL", "online": False}] and "FS-1-WL Bluetooth" not in got["sources"]
    got = client.post("/api/midi/bluetooth").json()
    assert got["asked"] == 0 and got["bluetooth"][0]["online"] and "FS-1-WL Bluetooth" in got["sources"]


def test_without_the_tools_it_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MIDI_BT", tmp_path / "nothing" / "midi-bt")
    src = tmp_path / "broken.swift"
    src.write_text("this is not swift")
    monkeypatch.setattr(server, "MIDI_BT_SRC", src)
    r = client.get("/api/midi/system")
    assert r.status_code == 503 and "sestavit" in r.json()["error"]
