"""Pluginy (plugins.py): scanned, chained per place, rendered into a new track — the host is faked."""
import json
import time

import plugins
import server


def _fake(monkeypatch, tmp_path):
    vst = tmp_path / "VST3"
    vst.mkdir()
    for n in ("Mesa Boogie Mark IIC+ Suite.vst3", "Archetype Gojira X.vst3", "Kontakt 8.vst3", "Broken.vst3", "Synth.vst3"):
        (vst / n).mkdir()
    au = tmp_path / "Components"
    au.mkdir()
    (au / "Archetype Gojira X.component").mkdir()                       # the VST3 twin is enough
    monkeypatch.setattr(plugins, "FOLDERS", [vst, au])
    monkeypatch.setattr(plugins, "DATA", tmp_path / "plugins")
    monkeypatch.setattr(plugins, "HOST_PY", tmp_path / "python")
    (tmp_path / "python").write_text("")
    calls = []

    def host(*args, timeout=120):
        calls.append(args)
        if args[0] == "probe":
            name = args[1].split("/")[-1].rsplit(".", 1)[0]
            if name == "Broken":
                return {"ok": False, "error": "ImportError"}
            return {"ok": True, "name": name, "instrument": name == "Synth", "params": 10}
        if args[0] == "render":
            chain = json.load(open(args[1]))
            open(args[3], "wb").write(b"RIFF" + json.dumps(chain).encode())
            return {"ok": True}
        return {"ok": True}
    monkeypatch.setattr(plugins, "_host", host)
    return calls


def test_plugins_are_scanned_once_each_and_offered_as_effects(client, tmp_path, monkeypatch):
    _fake(monkeypatch, tmp_path)
    plugins.SCAN["running"] = False
    client.post("/api/plugins/scan")
    for _ in range(100):
        if not plugins.SCAN["running"]:
            break
        time.sleep(0.05)
    items = client.get("/api/plugins").json()["items"]
    by = {c["name"]: c for c in items}
    assert set(by) == {"Mesa Boogie Mark IIC+ Suite", "Archetype Gojira X", "Broken", "Synth"}      # no Kontakt, no AU twin
    assert by["Archetype Gojira X"]["format"] == "VST3" and by["Archetype Gojira X"]["vendor"] == "Neural DSP"
    assert not by["Broken"]["ok"] and by["Synth"]["instrument"]
    # a chain takes effects that loaded, nothing else
    ids = [by[n]["id"] for n in ("Mesa Boogie Mark IIC+ Suite", "Broken", "Synth", "Archetype Gojira X")]
    got = client.put("/api/plugins/chain", json={"key": "live", "items": [{"id": i} for i in ids]}).json()["items"]
    assert [c["name"] for c in got] == ["Mesa Boogie Mark IIC+ Suite", "Archetype Gojira X"]
    assert "state" not in got[0]
    assert client.get("/api/plugins/chain", params={"key": "nowhere"}).status_code == 400


def test_a_track_is_rendered_through_its_chain_into_a_new_track(client, tmp_path, monkeypatch, tmp_music_root):
    _fake(monkeypatch, tmp_path)
    plugins._scan()
    mesa = next(c for c in plugins.catalog() if c["name"].startswith("Mesa"))
    d = tmp_music_root / "Stems" / "Kapela - Píseň"
    d.mkdir(parents=True)
    (d / "guitar.wav").write_bytes(b"RIFF")
    made = []
    monkeypatch.setattr(plugins, "add_track", lambda folder, src, label: made.append((folder.name, src.read_bytes()[:4], label)) or {"stem": "take-1", "label": label})
    key = "stem:Kapela - Píseň|guitar"
    client.put("/api/plugins/chain", json={"key": key, "items": [{"id": mesa["id"]}]})
    assert client.post("/api/plugins/render", json={"song": "Kapela - Píseň", "stem": "guitar"}).status_code == 200
    for _ in range(100):
        if plugins.JOB["status"] != "running":
            break
        time.sleep(0.05)
    assert plugins.JOB["status"] == "done"
    assert made == [("Kapela - Píseň", b"RIFF", "guitar · Mesa Boogie Mark IIC+ Suite")]
    assert client.post("/api/plugins/render", json={"song": "Kapela - Píseň", "stem": "bass"}).status_code == 404
    assert client.post("/api/plugins/render", json={"song": "Nic", "stem": "guitar"}).status_code == 404


def test_the_live_guitar_needs_a_chain_and_an_input(client, tmp_path, monkeypatch):
    _fake(monkeypatch, tmp_path)
    assert client.post("/api/plugins/live", json={"on": True, "input": ""}).status_code == 400
    assert client.post("/api/plugins/live", json={"on": True, "input": "iRig PRO DUO"}).status_code == 500   # no plugin yet
    assert client.post("/api/plugins/live", json={"on": False}).json()["running"] is False
