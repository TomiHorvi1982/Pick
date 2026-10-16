"""Katalog riffů: a tab taken apart into the parts of its song and the riffs they repeat, its guitars kept together."""
import json
import time

import pytest

import riffbook as rb
import vguitar

STD = [64, 59, 55, 50, 45, 40]


def beat(notes=(), dur=(1, 8), **kw):
    return {"duration": list(dur), "notes": [dict(string=s, fret=f, **x) for s, f, *rest in notes for x in [rest[0] if rest else {}]], **kw}


def rest(dur=(1, 1)):
    return {"duration": list(dur), "rest": True, "notes": [{"rest": True}]}


def bar(beats, marker=None):
    m = {"voices": [{"beats": beats}], "signature": [4, 4]}
    if marker:
        m["marker"] = {"text": marker}
    return m


RIFF_A = [beat([(5, 0)], palmMute=True), beat([(5, 0)], palmMute=True), beat([(5, 3, {"hp": True})]), beat([(5, 5)]),
          beat([(5, 0)]), beat([(4, 2), (5, 0)]), beat([(5, 0, {"dead": True})]), beat([(5, 7)])]
RIFF_B = [beat([(5, 1)]) for _ in range(8)]
CHORDS = [beat([(5, 3), (4, 5), (3, 5)], dur=(1, 4)) for _ in range(4)]
HIGH = [beat([(0, 12 + k % 3)], dur=(1, 4)) for k in range(4)]
SOLO = [[beat([(1, 10 + k)], dur=(1, 16)) for k in range(16)], [beat([(0, 15 - k % 5)], dur=(1, 16)) for k in range(16)]]


def song():
    """Intro: one guitar, a bar four times. Verse 1 and Verse 2: both guitars the same notes. Chorus: chords
    under a line up high. Solo: two bars that do not repeat, the other guitar riffing."""
    g1 = [bar(RIFF_A, "Intro"), bar(RIFF_A), bar(RIFF_A), bar(RIFF_A),
          bar(RIFF_B, "Verse 1"), bar(RIFF_B),
          bar(CHORDS, "[B] Chorus"), bar(CHORDS),
          bar(RIFF_B, "Verse 2"), bar(RIFF_B),
          bar(RIFF_A, "Guitar Solo"), bar(RIFF_A)]
    g2 = [bar([rest()])] * 4 + [bar(RIFF_B), bar(RIFF_B), bar(HIGH), bar(HIGH), bar(RIFF_B), bar(RIFF_B), bar(SOLO[0]), bar(SOLO[1])]
    tracks = [{"instrument": "Distortion Guitar", "instrumentId": 30, "name": "Guitar 1", "tuning": STD},
              {"instrument": "Distortion Guitar", "instrumentId": 30, "name": "Guitar 2", "tuning": STD}]
    parts = [{"i": 0, "track": tracks[0], "measures": g1, "tempo": [{"measure": 0, "bpm": 140}, {"measure": 10, "bpm": 150}]},
             {"i": 1, "track": tracks[1], "measures": g2, "tempo": [{"measure": 0, "bpm": 140}]}]
    return {"songId": 7, "tracks": tracks}, parts


def test_a_song_taken_apart():
    meta, parts = song()
    es = rb.analyze(meta, parts)
    assert [(e["labels"], e["kind"]) for e in es] == [(["Intro"], "intro"), (["Verse 1", "Verse 2"], "verse"),
                                                     (["[B] Chorus"], "chorus"), (["Guitar Solo"], "solo")]
    intro, verse, chorus, solo = es
    # the intro: one bar, played four times, by the one guitar that plays
    assert (intro["start"], intro["bars"], intro["of"], intro["repeats"], intro["bpm"]) == (1, 1, 4, 4, 140)
    assert len(intro["parts"]) == 1 and intro["parts"][0]["name"] == "Guitar 1"
    cols = rb.decode(intro["parts"][0]["cols"])
    assert len(cols) == 8 and intro["div"] == 2
    assert cols[0] == {"s": {"5": 0}, "pm": True, "lg": []} and cols[1]["pm"]
    assert cols[3]["lg"] == [5] and not cols[2]["lg"]               # the hammer-on lands on the next note
    assert cols[6]["s"] == {"5": "x"} and cols[5]["s"] == {"4": 2, "5": 0}
    # the verse: two guitars the same notes — one part, doubled; Verse 2 the same entry
    assert len(verse["parts"]) == 1 and verse["parts"][0]["double"] == 2 and verse["parts"][0]["name"] == "Guitar 1 + Guitar 2"
    assert verse["bars"] == 1 and verse["repeats"] == 2
    # the chorus: chords and a line above them stay together, a rhythm and a lead
    assert [(p["name"], p["role"]) for p in chorus["parts"]] == [("Guitar 1", "rhythm"), ("Guitar 2", "lead")]
    # the solo does not repeat: whole, both guitars, on a grid fine enough for its 16ths; its tempo from its bar
    assert solo["whole"] and solo["bars"] == 2 and solo["div"] == 4 and solo["bpm"] == 150
    assert {p["role"] for p in solo["parts"]} == {"rhythm", "lead"}
    assert all(len(rb.decode(p["cols"])) == 32 for p in solo["parts"])


def test_parts_named_as_tabs_name_them():
    assert rb.kind_of("[A] Intro") == "intro" and rb.kind_of("Verse 3") == "verse" and rb.kind_of("Pre-Chorus") == "prechorus"
    assert rb.kind_of("Chorus 2") == "chorus" and rb.kind_of("Guitar Solo") == "solo" and rb.kind_of("[E] Outro") == "outro"
    assert rb.kind_of("[D] Rock: Riff") == "riff" and rb.kind_of("Bridge Part II") == "bridge" and rb.kind_of("Breakdown") == "breakdown"
    assert rb.kind_of("Interlude") == "interlude" and rb.kind_of("Fade Out") == "outro" and rb.kind_of("Slow-Down") == "other"
    # a tab without markers: runs of eight bars
    bars = rb.bars_of([bar(RIFF_A)] * 20)
    assert rb.sections(bars) == [("Úsek 1", 0, 8), ("Úsek 2", 8, 16), ("Úsek 3", 16, 20)]
    assert rb.tuning_name([64, 59, 55, 50, 45, 38]) == "Drop D" and rb.tuning_name([62, 57, 53, 48, 43, 37]) == "C# G C F A D"


def test_only_guitars_are_read():
    assert rb.is_guitar({"instrumentId": 29, "instrument": "Overdriven Guitar", "tuning": STD})
    assert not rb.is_guitar({"instrumentId": 33, "instrument": "Electric Bass", "tuning": [43, 38, 33, 28]})
    assert not rb.is_guitar({"instrumentId": 1024, "instrument": "Drums", "tuning": None})
    assert not rb.is_guitar({"instrumentId": 30, "instrument": "Distortion Guitar", "tuning": STD, "isEmpty": True})


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "ROOT", tmp_path)
    monkeypatch.setattr(rb, "_SONGS", None)
    monkeypatch.setattr(rb, "library", lambda: [{"songId": 7, "artist": "Kapela", "title": "Píseň"}, {"songId": 8, "artist": "Jiná", "title": "Druhá"}])
    meta, parts = song()
    rb.keep_song({"songId": 7, "artist": "Kapela", "title": "Píseň", "revisionId": 1, "guitars": [], "read": 1, "entries": rb.analyze(meta, parts)})
    return tmp_path


def test_catalog_filtered_as_ultimate_guitar_filters(client, book):
    d = client.get("/api/riffbook").json()
    assert d["total"] == 4 and d["status"]["songs"] == 1 and d["status"]["unread"] == 1 and d["status"]["entries"] == 4
    g = {x["param"]: {v["v"]: v["count"] for v in x["values"]} for x in d["groups"]}
    assert g["kind"] == {"intro": 1, "verse": 1, "chorus": 1, "solo": 1} and g["guitars"] == {"1": 2, "2": 2}
    assert g["role"]["lead"] == 2 and g["tuning"] == {"Standard E": 4}
    # two guitars together: the chorus and the solo; their counts of parts follow the other filters
    two = client.get("/api/riffbook?guitars=2").json()
    assert [r["labels"] for r in two["rows"]] == [["[B] Chorus"], ["Guitar Solo"]]
    assert {v["v"]: v["count"] for v in next(x for x in two["groups"] if x["param"] == "kind")["values"]} == {"chorus": 1, "solo": 1}
    assert {v["v"]: v["count"] for v in next(x for x in two["groups"] if x["param"] == "guitars")["values"]} == {"1": 2, "2": 2}
    assert client.get("/api/riffbook?kind=verse&kind=intro").json()["total"] == 2
    assert client.get("/api/riffbook?q=kapela solo").json()["total"] == 1
    assert client.get("/api/riffbook?song=8").json()["total"] == 0
    # a riff opened: its guitars, each on the guitar's own grid
    r = client.get("/api/riffbook/riff/7-3").json()
    assert r["artist"] == "Kapela" and [p["role"] for p in r["parts"]] == ["rhythm", "lead"]
    assert r["parts"][0]["cols"][0]["s"] == {"5": 3, "4": 5, "3": 5}
    assert vguitar.clean({**r, "tuning": r["parts"][0]["tuning"], "cols": r["parts"][0]["cols"], "band": r["parts"][1:]})["band"][0]["role"] == "lead"
    assert client.get("/api/riffbook/riff/7-99").status_code == 404 and client.get("/api/riffbook/riff/x").status_code == 404


def test_stars_and_labels_sort_them(client, book):
    assert client.post("/api/riffbook/mark", json={"id": "7-2", "star": True, "tags": [" na  zkoušku ", "Na zkoušku", "rozcvička"]}).json()["tags"] == ["na zkoušku", "rozcvička"]
    assert [r["id"] for r in client.get("/api/riffbook?star=true").json()["rows"]] == ["7-2"]
    d = client.get("/api/riffbook?tag=rozcvička").json()
    assert d["total"] == 1 and {v["v"] for v in next(x for x in d["groups"] if x["param"] == "tag")["values"]} == {"na zkoušku", "rozcvička"}
    client.post("/api/riffbook/mark", json={"id": "7-2", "star": False, "tags": []})
    assert json.loads((book / "marks.json").read_text()) == {}
    assert client.post("/api/riffbook/mark", json={"id": "../x"}).status_code == 400


def test_the_library_read_in_the_background(client, book, monkeypatch):
    seen = []

    def read(sid, pause):
        seen.append(sid)
        if sid == 9:
            raise RuntimeError("Songsterr neodpověděl")
        return {"songId": sid, "artist": "Jiná", "title": "Druhá", "entries": []}
    monkeypatch.setattr(rb, "read_song", read)
    monkeypatch.setattr(rb, "PAUSE", 0)
    rb.start([], 0)
    # only what is not read yet; a song asked for by itself, again
    st = client.post("/api/riffbook/scan", json={}).json()
    for _ in range(100):
        if not rb.status()["running"]:
            break
        time.sleep(0.02)
    assert seen == [8] and rb.status()["unread"] == 0 and (book / "songs" / "8.json").is_file()
    assert st["total"] >= 1
    rb.start([(9, "x")], 0)
    for _ in range(100):
        if not rb.status()["running"]:
            break
        time.sleep(0.02)
    assert rb.status()["errors"] == 1 and "neodpověděl" in rb.status()["error"]
    # cut off by a stop of the app: it goes on where it was
    (book / "scan.json").write_text(json.dumps({"running": True, "queue": [[7, "Kapela – Píseň"]], "done": 3, "total": 4}))
    started = []
    monkeypatch.setattr(rb, "start", lambda items, pause=0: started.append(items))
    rb.resume_after_restart()
    assert started == [[(7, "Kapela – Píseň")]]
