"""Automatic prints: songs with no print put to the night's planner, checked in the morning."""
import json

import planner
import server


def _song(root, name, guitar=True, print_id=None):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for s in ("drums", "bass", "vocals", "other") + (("guitar",) if guitar else ()):
        (d / f"{s}.mp3").write_bytes(b"x" * 2000)
    if print_id:
        p = d / server.TONE_DIR / "prints" / print_id
        p.mkdir(parents=True)
        (p / "print.json").write_text(json.dumps({"match": 70, "auto": True, "review": "pending"}))
        (p / "print-ir.wav").write_bytes(b"RIFF")
    return d


def test_plan_needs_a_calibrated_guitar(client, tmp_music_root):
    server._STEMS_ROWS.clear()
    _song(tmp_music_root, "A")
    r = client.post("/api/tone/auto/plan", json={"limit": 5})
    assert r.status_code == 400 and "kalibraci" in r.json()["error"]


def test_plan_puts_songs_without_a_print_to_the_night(client, tmp_music_root, monkeypatch):
    server._STEMS_ROWS.clear()
    monkeypatch.setattr(server, "_guitar_active", lambda: "strat")
    _song(tmp_music_root, "Has print", print_id="20261103-120000")
    _song(tmp_music_root, "No guitar", guitar=False)
    _song(tmp_music_root, "Song: One &? 100%")
    _song(tmp_music_root, "Song Two")
    r = client.post("/api/tone/auto/plan", json={"limit": 10})
    assert r.status_code == 200 and r.json()["added"] == 2
    tasks = planner.tasks()["tasks"]
    assert {t["kind"] for t in tasks} == {"tone"} and all(planner.allowed(t["path"]) for t in tasks)
    assert all(t["body"] == {"stem": "guitar", "auto": True} for t in tasks)
    again = client.post("/api/tone/auto/plan", json={"limit": 10}).json()   # not twice
    assert again["added"] == 0 and again["planned"] == 2


def test_auto_fit_over_a_print_is_skipped(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_tone_ready", lambda: True)
    _song(tmp_music_root, "Has print", print_id="20261103-120000")
    r = client.post("/api/tone/Has%20print/fit", json={"stem": "guitar", "auto": True})
    assert r.status_code == 200 and r.json() == {"skipped": "Píseň už otisk má."}


def test_review_keeps_or_throws_away(client, tmp_music_root):
    d = _song(tmp_music_root, "S", print_id="20261103-120000")
    _song(tmp_music_root, "T", print_id="20261103-130000")
    assert [p["review"] for p in client.get("/api/tone/prints").json()] == ["pending", "pending"]
    assert client.post("/api/tone/S/prints/20261103-120000/review", json={"keep": True}).json() == {"kept": True, "review": "ok"}
    assert json.loads((d / "tone/prints/20261103-120000/print.json").read_text())["review"] == "ok"
    # not right, but only marked (the quick check in Hraju s kytarou): the print stays, as "bad"
    assert client.post("/api/tone/S/prints/20261103-120000/review", json={"keep": False, "drop": False}).json() == {"kept": False, "review": "bad"}
    assert json.loads((d / "tone/prints/20261103-120000/print.json").read_text())["review"] == "bad"
    assert client.post("/api/tone/T/prints/20261103-130000/review", json={"keep": False}).json() == {"kept": False}
    assert not (tmp_music_root / "Stems/T/tone/prints/20261103-130000").exists()


def test_a_ditorig_print_does_not_count_and_goes_first_to_be_compared(client, tmp_music_root, monkeypatch):
    server._STEMS_ROWS.clear()
    server._STAMP_ROW.clear()
    monkeypatch.setattr(server, "_guitar_active", lambda: "strat")
    monkeypatch.setattr(server, "_tone_ready", lambda: True)
    d = _song(tmp_music_root, "With DITORIG", print_id="20261103-120000")
    f = d / server.TONE_DIR / "prints" / "20261103-120000" / "print.json"
    f.write_text(json.dumps({"match": 60, "ditorig": {"profile": "p"}}))
    _song(tmp_music_root, "Plain")
    _song(tmp_music_root, "Own print", print_id="20261103-130000")
    names = [r["name"] for r in server._tone_auto_candidates(ditorig_first=True)]
    assert names[0] == "With DITORIG" and "Plain" in names and "Own print" not in names
    r = client.post("/api/tone/auto/plan", json={"limit": 1, "ditorig_first": True}).json()
    assert r["added"] == 1 and [t["label"] for t in planner.tasks()["tasks"]] == ["Otisk: With DITORIG"]
    # the fit itself does not skip it either (it goes on to look for a stretch to print)
    got = client.post("/api/tone/With%20DITORIG/fit", json={"stem": "guitar", "auto": True}).json()
    assert got.get("skipped") is None
