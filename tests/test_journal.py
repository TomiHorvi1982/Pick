"""Cvičební deník: what was practised is added up per day and song; the week's summary says how long,
what got better against the time before and what has not been played for a while."""
import time
from datetime import date, timedelta

import pytest

import journal


@pytest.fixture(autouse=True)
def _own_file(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "PATH", tmp_path / "practice-journal.json")


def _add(client, day, song, secs, sections=(), event=None, guitar=0):
    r = client.post("/api/journal", json={"day": day.isoformat(), "song": song, "title": song.upper(), "secs": secs, "guitar": guitar,
                                          "sections": [{"name": n, "secs": s, "speed": v} for n, s, v in sections], "event": event})
    assert r.status_code == 200, r.text


def test_minutes_add_up_per_day_and_song(client):
    today = date.today()
    _add(client, today, "Riff", 60, [("Refrén", 40, 0.8)], guitar=50)
    _add(client, today, "Riff", 60, [("Refrén", 30, 0.9), ("Sloka", 30, 0)], guitar=60)
    d = client.get("/api/journal").json()
    assert d["total"] == 120 and d["days"][today.weekday()] == 120
    s = d["songs"][0]
    assert s["song"] == "Riff" and s["title"] == "RIFF" and s["guitar"] == 110
    refren = next(x for x in s["sections"] if x["name"] == "Refrén")
    assert refren["secs"] == 70 and refren["best"] == 0.9


def test_what_got_better_and_what_was_forgotten(client):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    old = monday - timedelta(days=30)
    _add(client, old, "Riff", 900, [("Sólo", 600, 0.7)], event={"kind": "timing", "within": 40, "median": -30})
    _add(client, old, "Stará", 1200, [])
    _add(client, monday, "Riff", 300, [("Sólo", 200, 0.85)], event={"kind": "timing", "within": 62, "median": -8})
    _add(client, monday, "Riff", 0, [], event={"kind": "trainer", "section": "Sólo", "from": 70, "to": 100, "reached": 100, "done": True})
    d = client.get("/api/journal").json()
    kinds = {(b["kind"], b["what"]) for b in d["better"]}
    assert ("tempo", "Sólo") in kinds and ("timing", "časování") in kinds and ("trainer", "Sólo") in kinds
    tempo = next(b for b in d["better"] if b["kind"] == "tempo")
    assert tempo["from"] == 70 and tempo["to"] == 100              # the trainer's 100 % counts as the tempo got to
    assert [f["song"] for f in d["forgotten"]] == ["Stará"]          # Riff was played this week
    assert d["older"] and client.get("/api/journal?week=5").json()["total"] == 2100      # the week of `old`


def test_weekly_reminder_once(client):
    today = date.today()
    _add(client, today - timedelta(days=today.weekday() + 3), "Riff", 600)
    assert client.get("/api/journal?week=1").json()["remind"] is True
    client.post("/api/journal/reminded")
    assert client.get("/api/journal?week=1").json()["remind"] is False
    j = journal.load(); j["reminded"] = time.time() - 8 * 86400; journal._save(j)
    assert client.get("/api/journal?week=1").json()["remind"] is True


def test_bad_entries_are_refused_and_the_journal_cleared(client):
    assert client.post("/api/journal", json={"day": "včera", "song": "Riff"}).status_code == 400
    assert client.post("/api/journal", json={"day": "2026-02-30", "song": "Riff"}).status_code == 400
    assert client.post("/api/journal", json={"day": date.today().isoformat(), "song": "../x"}).status_code == 400
    _add(client, date.today(), "Riff", 99999)                          # one call is at most an hour
    assert client.get("/api/journal").json()["total"] == 3600
    client.delete("/api/journal")
    d = client.get("/api/journal").json()
    assert d["total"] == 0 and not d["any"]
