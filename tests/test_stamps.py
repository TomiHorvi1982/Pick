"""GuitarStamps: the overview and the map of the NAM cores."""
import json
import server


def test_overview_counts_prints_and_review(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_tone_models", lambda: [{"id": "a.nam", "name": "A", "path": "/x/a.nam"}])
    d = tmp_music_root / "Stems" / "S" / "tone" / "prints" / "20261103-120000"
    d.mkdir(parents=True)
    (d / "print.json").write_text(json.dumps({"match": 60, "review": "pending"}))
    (d / "print-ir.wav").write_bytes(b"RIFF")
    got = client.get("/api/stamps").json()
    assert got["prints"] == 1 and got["songs"] == 1 and got["review"] == 1 and got["cores"] == 1
    assert {"guitar", "catalog", "groups", "ready"} <= set(got)


def test_map_needs_the_engine(client, monkeypatch):
    monkeypatch.setattr(server, "_tone_ready", lambda: False)
    assert client.post("/api/stamps/map").status_code == 503


def test_map_result_kept_with_names(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "AMPS_DIR", tmp_path)
    monkeypatch.setattr(server, "_tone_ready", lambda: True)
    monkeypatch.setattr(server._sysload, "room", lambda *a: (True, ""))
    monkeypatch.setattr(server, "_tone_models", lambda: [{"id": "a", "name": "Amp A", "path": "/a"}, {"id": "b", "name": "Amp B", "path": "/b"}])

    class Proc:
        def __init__(self, args, **kw):
            out = json.loads(open(args[-1]).read())["out"]
            open(out, "w").write(json.dumps({"chosen": [{"id": "a", "name": "Amp A", "stands_for": ["a", "b"]}], "known": 2, "bad": []}))
            self.stdout = iter(["STAGE cores 2 2\n"])

        def wait(self):
            return 0
    monkeypatch.setattr(server.subprocess, "Popen", Proc)
    job = {"status": "running", "done": 0, "of": 0, "started": 0.0}
    server._STAMP_MAP["job"] = job
    server._stamp_map_run(job)
    got = client.get("/api/stamps/map").json()
    assert got["job"]["status"] == "done" and got["map"]["chosen"][0]["twins"] == ["Amp B"] and got["map"]["known"] == 2
    server._STAMP_MAP["job"] = None
