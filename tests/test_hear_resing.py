"""A text heard off the vocals, a line sung again with new words, and the watch on the memory.
Only made-up lines here — nobody's song."""
import time

import numpy as np

import hear
import server
import sysload


def _song(root, name="Kapela - Píseň"):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    (d / "vocals.mp3").write_bytes(b"x" * 2048)
    return d


def _wait(client, url, key="job"):
    for _ in range(100):
        got = client.get(url).json()
        if (got.get(key) or {}).get("status") != "running":
            return got
        time.sleep(0.05)
    return got


def test_the_sung_stretches_are_found():
    rate = hear.RATE
    x = np.zeros(rate * 8, dtype=np.float32)
    rng = np.random.default_rng(1)
    x[rate * 1:rate * 2] = rng.normal(0, 0.3, rate)                 # sung 1–2 s
    x[int(rate * 4):int(rate * 6.5)] = rng.normal(0, 0.3, int(rate * 2.5))
    got = hear.phrases(x)
    assert len(got) == 2
    assert abs(got[0][0] - 1) < 0.05 and abs(got[1][1] - 6.5) < 0.05


def test_known_words_get_the_heard_times():
    plain = "[Verse]\nthe river runs along the stone\na lantern made of ash and bone\n\nhold the line\nhold the line tonight"
    heard = [{"timeInMs": 10000, "text": "the river runs along the stone"},
             {"timeInMs": 14000, "text": "a lanter made of ash and bone"},     # misheard a little
             {"timeInMs": 30000, "text": "hold the line"},
             {"timeInMs": 34000, "text": "hold the line tonite"}]
    got = hear.timed(plain, heard)
    assert [r["text"] for r in got] == plain.splitlines()[1:3] + plain.splitlines()[4:]     # the known words, no tags
    assert [round(r["timeInMs"] / 1000) for r in got] == [10, 14, 30, 34]
    assert hear.timed(plain, []) == []


def test_long_heard_lines_are_split():
    parts = hear._split_long(0.0, 10.0, "one two three four five six, seven eight nine ten eleven twelve")
    assert len(parts) == 2 and parts[0][0] == 0.0 and 3 < parts[1][0] < 7


def test_a_text_is_heard_once_and_kept(client, tmp_music_root, monkeypatch):
    d = _song(tmp_music_root)
    calls = []

    def heard(vocals, lang="auto", prompt="", progress=None):
        calls.append(prompt)
        return {"lang": "cs", "lines": [{"timeInMs": 1000, "text": "první řádek"}, {"timeInMs": 5000, "text": "druhý řádek"}], "phrases": 2}
    monkeypatch.setattr(hear, "heard", heard)
    monkeypatch.setattr(hear, "ready", lambda: True)
    monkeypatch.setattr(sysload, "room", lambda need, what="": (True, ""))
    ask = {"title": "Píseň", "artist": "Kapela", "duration": 200}
    assert client.post("/api/stems/Kapela - Píseň/hear", json=ask).json()["status"] == "running"
    got = _wait(client, "/api/hear")["job"]
    assert got["status"] == "done" and got["lyrics"]["source"] == "odposlechnuto ze zpěvu"
    assert (d / "heard.json").is_file()
    # kept: the mixer finds it like any other text, and asking again does not listen again
    assert server._lyrics_kept("Píseň", "Kapela", 200)["lines"][1]["text"] == "druhý řádek"
    again = client.post("/api/stems/Kapela - Píseň/hear", json=ask).json()
    assert again["status"] == "done" and len(calls) == 1
    # a text known without times takes the heard ones
    server._lyrics_keep("Píseň", "Kapela", 200, {"synced": False, "lines": None, "plain": "první řádek\ndruhý řádek"})
    timed = client.post("/api/stems/Kapela - Píseň/hear", json=ask).json()["lyrics"]
    assert timed["source"] == "text z LRCLib, časy ze zpěvu" and [x["timeInMs"] for x in timed["lines"]] == [1000, 5000]


def test_nothing_heavy_starts_without_memory(client, tmp_music_root, monkeypatch):
    _song(tmp_music_root)
    monkeypatch.setattr(sysload, "now", lambda: {"avail": 0.6 * sysload.GB, "pressure": 1, "jobs": ["rozdělování (X)"]})
    ok, why = sysload.room(1.0, "odposlech")
    assert not ok and "0.6 GB" in why and "rozdělování" in why
    monkeypatch.setattr(hear, "ready", lambda: True)
    r = client.post("/api/stems/Kapela - Píseň/hear", json={})
    assert r.status_code == 503 and "paměť" in r.json()["error"]
    monkeypatch.setattr(sysload, "now", lambda: {"avail": 9 * sysload.GB, "pressure": 4, "jobs": []})
    assert sysload.room(1.0)[0] is False


def test_the_computer_is_measured(client):
    got = client.get("/api/system").json()
    assert got["total"] > 0 and 0 <= got["cpu"] <= 100 and got["level"] in ("ok", "busy", "tight") and isinstance(got["jobs"], list)


def test_a_line_is_sung_again_only_when_it_can_be(client, tmp_music_root, monkeypatch):
    _song(tmp_music_root)
    monkeypatch.setattr(server, "_svc_ready", lambda: True)
    monkeypatch.setattr(sysload, "room", lambda need, what="": (True, ""))
    url = "/api/stems/Kapela - Píseň/resing"
    assert client.post(url, json={"start": 10, "end": 14, "text": ""}).status_code == 400              # no words
    assert client.post(url, json={"start": 10, "end": 14, "take": True}).status_code == 400            # nothing recorded
    assert client.post(url, json={"start": 10, "end": 40, "text": "nová slova"}).status_code == 400     # not a line
    monkeypatch.setattr(sysload, "room", lambda need, what="": (False, "Volná paměť je teď 1.0 GB"))
    r = client.post(url, json={"start": 10, "end": 14, "text": "nová slova"})
    assert r.status_code == 503 and "paměť" in r.json()["error"]
    assert client.post("/api/stems/Nic - Tu/resing", json={"start": 1, "end": 3, "text": "x"}).status_code == 404


def test_a_line_sung_again_goes_into_its_own_track(client, tmp_music_root, monkeypatch):
    d = _song(tmp_music_root)
    monkeypatch.setattr(server, "_svc_ready", lambda: True)
    monkeypatch.setattr(sysload, "room", lambda need, what="": (True, ""))
    rate = server.SVC_SR
    tone = (0.2 * np.sin(np.arange(rate * 20) * 2 * np.pi * 220 / rate)).astype(np.float32)
    monkeypatch.setattr(server, "_svc_pcm", lambda src, start=0.0, length=None: tone[int(start * rate):int((start + (length or 20)) * rate)].copy())
    monkeypatch.setattr(server, "_resing_refs", lambda *a: (d / "r.wav", d / "s.wav", "slova"))
    monkeypatch.setattr(server, "_own_say", lambda *a, **k: b"RIFF")
    monkeypatch.setattr(server, "_resing_step", lambda job, cmd: (open(cmd[cmd.index("--out") + 1], "wb").write(b"x"), "DONE")[1])
    monkeypatch.setattr(server, "_encode_to_stem", lambda src, target: target.write_bytes(b"x" * 2048) and None)
    r = client.post("/api/stems/Kapela - Píseň/resing", json={"start": 5, "end": 8, "text": "úplně nová slova", "old": "starý řádek"})
    assert r.status_code == 200, r.text
    got = _wait(client, "/api/resing?song=Kapela - Píseň")
    assert got["job"]["status"] == "done", got
    assert got["kept"]["stem"] == got["job"]["stem"] and got["kept"]["edits"][0]["text"] == "úplně nová slova"
    assert server._track_labels(d)[got["job"]["stem"]] == "Upravený zpěv"


def test_a_corrected_text_is_kept(client):
    lines = [{"timeInMs": 1000, "text": "opravený řádek"}]
    assert client.post("/api/lyrics/keep", json={"title": "Píseň", "artist": "Kapela", "duration": 120, "lines": lines}).json()["ok"]
    assert server._lyrics_kept("Píseň", "Kapela", 120)["lines"] == lines
    assert client.post("/api/lyrics/keep", json={"title": "", "lines": lines}).status_code == 400


def test_a_line_sung_again_can_be_rewritten_and_undone(client, tmp_music_root, monkeypatch):
    d = _song(tmp_music_root)
    rate = server.SVC_SR
    tone = (0.2 * np.sin(np.arange(rate * 20) * 2 * np.pi * 220 / rate)).astype(np.float32)
    monkeypatch.setattr(server, "_svc_pcm", lambda src, start=0.0, length=None: tone[int(start * rate):int((start + (length or 20)) * rate)].copy())
    monkeypatch.setattr(server, "_encode_to_stem", lambda src, target: target.write_bytes(b"x" * 2048) and None)
    (d / "take-1.mp3").write_bytes(b"x" * 2048)
    server._set_track_label(d, "take-1", "Upravený zpěv")
    edits = [{"start": 2.0, "end": 4.0, "old": "starý první", "text": "nový první"}, {"start": 9.0, "end": 11.0, "old": "starý druhý", "text": "nový druhý"}]
    (d / server.RESING_FILE).write_text(server.json.dumps({"stem": "take-1", "edits": edits}), encoding="utf-8")
    url = "/api/stems/Kapela - Píseň/resing/edit"
    r = client.post(url, json={"start": 9.0, "text": "jinak napsaný"})
    assert r.status_code == 200 and r.json()["kept"]["edits"][1]["text"] == "jinak napsaný"
    assert client.post(url, json={"start": 5.0, "text": "x"}).status_code == 404
    r = client.delete(url + "?start=2.0")
    assert r.status_code == 200 and [e["old"] for e in r.json()["kept"]["edits"]] == ["starý druhý"]
    r = client.delete(url + "?start=9.0")
    assert r.json()["gone"] == "take-1" and not (d / "take-1.mp3").exists() and not (d / server.RESING_FILE).exists()
    assert "take-1" not in server._track_labels(d)
