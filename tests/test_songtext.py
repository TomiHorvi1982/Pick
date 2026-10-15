"""The songs' own texts for the Skladatel: parts named by the song's sections, or by its stanzas."""
import songbook


def _lyr(rows, synced=True, plain=None):
    return {"synced": synced, "plain": plain, "lines": [{"timeInMs": int(t * 1000), "text": s} for t, s in rows]}


def test_timed_lines_go_to_their_sections():
    secs = [{"start": 0, "end": 10, "kind": "intro"}, {"start": 10, "end": 30, "kind": "verse"},
            {"start": 30, "end": 45, "kind": "chorus"}, {"start": 45, "end": 60, "kind": "solo"}, {"start": 60, "end": 80, "kind": "chorus"}]
    out = songbook.tagged(_lyr([(9.8, "a one"), (15, "a two"), (31, "la la"), (40, "la lo"), (61, "la la"), (70, "la lo")]), secs)
    assert out["by"] == "sections"
    assert [p["tag"] for p in out["parts"]] == ["Intro", "Verse", "Chorus", "Guitar Solo", "Chorus"]
    assert out["parts"][1]["lines"] == ["a one", "a two"]          # sung a moment early: still the verse
    assert "[Guitar Solo]\n\n[Chorus]" in out["text"]


def test_stanzas_when_sections_are_not_known():
    plain = "one a\none b\n\nla la\nla lo\n\ntwo a\ntwo b\n\nla la\nla lo\n\nend"
    out = songbook.tagged(_lyr([], synced=False, plain=plain), [])
    assert out["by"] == "stanzas"
    assert [p["tag"] for p in out["parts"]] == ["Verse 1", "Chorus", "Verse 2", "Chorus", "Verse 3"]


def test_nothing_sung_is_no_text():
    assert songbook.tagged(None, []) is None
    assert songbook.tagged(_lyr([(1, "  ")]), []) is None
