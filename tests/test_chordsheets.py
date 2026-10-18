"""Chord sheets from the web for "Akordy z webu": Ultimate Guitar and Supermusic pages turned into text the
page lays onto the recording. The sites are not asked here — their pages are stood in for."""

import json

import server


def test_ug_markup_becomes_chord_lines():
    text = server._ug_sheet_text("[Verse 1]\r\n[tab][ch]Am[/ch]      [ch]G[/ch]\r\nA hundred days[/tab]\r\n[Chorus]\r\n[ch]C[/ch] [ch]G/B[/ch]")
    assert text.splitlines() == ["[Verse 1]", "Am      G", "A hundred days", "[Chorus]", "C G/B"]


def test_supermusic_chords_marked_up_become_inline():
    page = ("<html><body><div id=menu>Domů Hledat</div><div class=text>"
            "<sup>Am</sup>Kdo se <a class=sup href='#'>G</a>bojí<br/>"
            "R: <sup>C</sup>Pohoda<br></div><script>var x='[Hm]';</script></body></html>")
    text = server._sm_sheet_text(page)
    assert "[Am]Kdo se [G]bojí" in text and "R: [C]Pohoda" in text
    assert "var x" not in text


def test_supermusic_plain_chord_lines_are_kept():
    page = "<html><body><pre>Ami   C\nKdo se bojí\n</pre></body></html>"
    assert "Ami   C" in server._sm_sheet_text(page)


def test_supermusic_finds_the_same_song_of_the_same_band(monkeypatch):
    asked = []

    def get(url):
        asked.append(url)
        if "najdi.php" in url:
            return ('<a href="skupina.php?idpiesne=11">Pohoda</a> <a href="skupina.php?idskupiny=1">Jiná kapela</a><br>'
                    '<a href="skupina.php?idpiesne=22">Pohoda (akordy)</a> <a href="skupina.php?idskupiny=35">Kabát</a><br>'
                    '<a href="skupina.php?idpiesne=33">Pohodička</a> Kabát')
        return "<body><sup>E</sup>Pohoda<br></body>"
    monkeypatch.setattr(server, "_sm_get", get)
    got = server._sm_sheets("Kabát", "Pohoda")
    assert [g["url"] for g in got] == ["https://supermusic.cz/skupina.php?idpiesne=22"]
    assert got[0]["text"] == "[E]Pohoda"


def test_ug_takes_the_songs_chord_pages_best_rated_first(monkeypatch):
    def page(url, **params):
        if "search.php" in url:
            return {"results": [
                {"tab_url": "u/tab", "type": "Tabs", "artist_name": "3 Doors Down", "song_name": "Here Without You", "rating": 5, "votes": 900},
                {"tab_url": "u/c1", "type": "Chords", "artist_name": "3 Doors Down", "song_name": "Here Without You", "rating": 4.2, "votes": 300},
                {"tab_url": "u/c2", "type": "Chords", "artist_name": "3 Doors Down", "song_name": "Here Without You (ver 2)", "rating": 4.9, "votes": 800},
                {"tab_url": "u/c3", "type": "Chords", "artist_name": "Someone", "song_name": "Here Without You", "rating": 5, "votes": 999}]}
        return {"tab_view": {"wiki_tab": {"content": f"[ch]Bm[/ch] {url}"}}}
    monkeypatch.setattr(server, "_ug_page", page)
    got = server._ug_sheets("3 Doors Down", "Here Without You")
    assert [g["url"] for g in got] == ["u/c2", "u/c1"]
    assert got[0]["text"] == "Bm u/c2"


def test_endpoint_asks_both_and_keeps_what_it_found(client, tmp_music_root, monkeypatch):
    stems = tmp_music_root / "Stems" / "Kabát - Pohoda"
    stems.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(server, "_sm_sheets", lambda a, t: calls.append(("sm", a, t)) or [{"source": "Supermusic", "text": "[E]x"}])

    def ug(a, t):
        calls.append(("ug", a, t))
        raise RuntimeError("blocked")
    monkeypatch.setattr(server, "_ug_sheets", ug)
    out = client.get("/api/stems/Kabát - Pohoda/chordsheets").json()
    assert ("sm", "Kabát", "Pohoda") in calls and out["sheets"][0]["source"] == "Supermusic"
    assert out["errors"] == ["Ultimate Guitar: RuntimeError"]
    assert json.loads((stems / "chordsheets.json").read_text(encoding="utf-8"))["sheets"]
    calls.clear()
    client.get("/api/stems/Kabát - Pohoda/chordsheets")
    assert calls == []                                         # the second time: what was kept


def test_analysis_chords_stay_reachable_after_an_edit(client, tmp_music_root):
    stems = tmp_music_root / "Stems" / "Song"
    stems.mkdir(parents=True)
    (stems / "analysis.json").write_text(json.dumps({"chords": [{"start": 0, "end": 2, "chord": "Am"}]}), encoding="utf-8")
    (stems / "chords.json").write_text(json.dumps({"chords": [{"start": 0, "end": 2, "chord": "C"}]}), encoding="utf-8")
    assert client.get("/api/stems/Song/chords").json()["chords"][0]["chord"] == "C"
    assert client.get("/api/stems/Song/chords?analysis=1").json()["chords"][0]["chord"] == "Am"
