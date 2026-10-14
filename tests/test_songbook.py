"""Zpěvník: the songs in Steems as material for new ones — their sound and the shape of their texts, never the words."""
import json

import songbook as sb

# made-up lines, nobody's song
VERSE = ["the river runs along the stone", "a lantern made of ash and bone", "we walk the road we never knew", "and every mile is something new"]
CHORUS = ["hold the line", "hold the line tonight"]


def _timed(rows):
    return {"synced": True, "lines": [{"timeInMs": int(t * 1000), "text": x} for t, x in rows]}


def test_the_shape_of_a_text_and_nothing_of_its_words():
    secs = [{"start": 0, "end": 10}, {"start": 10, "end": 30}, {"start": 30, "end": 40}, {"start": 40, "end": 60}, {"start": 60, "end": 70}]
    lyr = _timed([(11 + i * 4, x) for i, x in enumerate(VERSE)] + [(31, CHORUS[0]), (35, CHORUS[1])]
                 + [(41 + i * 4, x) for i, x in enumerate(VERSE)] + [(61, CHORUS[0]), (65, CHORUS[1])])
    got = sb.shape(lyr, secs)
    rows = got["sections"]
    assert got["lang"] == "en" and got["synced"]
    assert [r["lines"] for r in rows] == [0, 4, 2, 4, 2]
    assert rows[1]["rhyme"] == "AABB" and 7 <= rows[1]["avg"] <= 9
    assert rows[3]["repeat_of"] == 1 and rows[4]["repeat_of"] == 2         # the parts that come back word for word
    assert "river" not in json.dumps(got) and "lantern" not in json.dumps(got)


def test_untimed_texts_by_their_stanzas():
    text = "\n".join(VERSE) + "\n\n" + "\n".join(CHORUS) + "\n\n" + "\n".join(VERSE[:2]) + "\n\n" + "\n".join(CHORUS)
    got = sb.shape({"synced": False, "plain": text, "lines": []}, [])
    assert [(r["kind"], r["lines"]) for r in got["sections"]] == [("verse", 4), ("chorus", 2), ("verse", 2), ("chorus", 2)]


def test_syllables_rhymes_and_czech():
    assert sb.syllables("hold the line tonight") == 5 and sb.syllables("vlk a krk") == 3
    assert sb.scheme(["jde to dál", "kdo to hrál", "noc je tmavá", "vítr mává"]) == "AABB"
    assert sb._lang("řeka teče dál") == "cs"
    assert sb._key_name("D# dur") == "Eb major" and sb._key_name("A moll") == "A minor"
    assert sb.tuning_words([62, 57, 53, 48, 43, 38])[0] == "downtuned guitars"
    assert sb.tuning_words([64, 59, 55, 50, 45, 38])[0].startswith("drop D")


def test_own_texts_are_kept_whole_and_by_parts(client):
    text = "[Verse 1]\n" + "\n".join(VERSE) + "\n\n[Chorus - big]\n" + "\n".join(CHORUS) + "\n\n[Guitar Solo]\n"
    row = client.post("/api/songbook/mine", json={"title": "Řeka", "band": "Naše kapela", "text": text}).json()
    assert [x["tag"] for x in row["sections"]] == ["Verse 1", "Chorus - big", "Guitar Solo"]
    assert row["sections"][1]["text"] == "\n".join(CHORUS) and row["sections"][2]["text"] == ""
    listed = client.get("/api/songbook").json()
    assert [m["title"] for m in listed["mine"]] == ["Řeka"]
    client.delete(f"/api/songbook/mine/{row['id']}")
    assert client.get("/api/songbook").json()["mine"] == []
    assert client.post("/api/songbook/mine", json={"text": "  "}).status_code == 400


def test_a_hundred_songs_across_bands(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "stems_root", lambda: tmp_path)
    for band, n in (("A", 5), ("B", 1), ("C", 3)):
        for i in range(n):
            d = tmp_path / f"{band} - {i}"
            d.mkdir()
            (d / "meta.json").write_text(json.dumps({"artist": band, "title": str(i)}))
            (d / "drums.mp3").write_bytes(b"x")
    picked = sb.pick_songs(5)
    assert [p.split(" - ")[0] for p in picked] == ["A", "B", "C", "A", "C"]     # one from each band in turn


def test_the_lyricist_writes_into_a_shape(monkeypatch):
    import lyricist
    p = lyricist.prompt({"lang": "en", "structure": "[Verse 1] 4 lines, about 8 syllables a line, rhyme scheme AABB"}, {"en": "English"})
    assert "[Verse 1] 4 lines" in p and "Every word must be your own" in p


def test_a_batch_paused_goes_on_where_it_stopped(client, tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(sb, "stems_root", lambda: tmp_path)
    monkeypatch.setattr(sb, "busy", lambda: False)
    for i in range(5):
        (tmp_path / f"A - {i}").mkdir()
    did = []

    def analyse(n):
        did.append(n)
        (tmp_path / n / "songbook.json").write_text(json.dumps({"version": sb.VERSION}))
        if len(did) == 2:
            sb.JOB["running"] = False                    # paused after the second song
    monkeypatch.setattr(sb, "analyse", analyse)
    sb.run([f"A - {i}" for i in range(5)])
    left = sb.left()
    assert did == ["A - 0", "A - 1"] and left["names"] == ["A - 2", "A - 3", "A - 4"] and left["paused"] and left["done"] == 2
    sb.resume_after_restart()                            # a paused batch waits for Pokračovat
    assert not sb.JOB["running"]
    monkeypatch.setattr(sb, "analyse", lambda n: did.append(n))
    assert client.post("/api/songbook/analyse/resume").json()["started"] == 3
    for _ in range(100):
        if not sb.JOB["running"]:
            break
        time.sleep(0.02)
    assert did[2:] == ["A - 2", "A - 3", "A - 4"] and sb.JOB["done"] == 5 and sb.left() == {}
    # cut off while running (a restart, the power): it goes on by itself
    sb._left_save(["A - 4"], 5, 4, False)
    sb.resume_after_restart()
    for _ in range(100):
        if not sb.JOB["running"]:
            break
        time.sleep(0.02)
    assert did[-1] == "A - 4" and sb.left() == {}
    assert client.post("/api/songbook/analyse/resume").status_code == 404
