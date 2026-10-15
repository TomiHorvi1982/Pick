"""Virtuální kytara: riffs kept, only in their known shape."""
import vguitar

RIFF = {"bpm": 140, "div": 2, "tuning": [64, 59, 55, 50, 45, 38],
        "cols": [{"s": {"5": 0, "4": 2}, "pm": True, "lg": []}, {"s": {"5": "x"}}, {"s": {"5": 3}, "lg": [5]}],
        "sound": {"song": "Metallica - One", "pid": "20261015-1"}}


def test_save_list_rename_delete(client):
    r = client.post("/api/vguitar/riffs", json={"name": "  Úvod  ", "riff": RIFF}).json()
    assert r["name"] == "Úvod" and r["bpm"] == 140 and r["cols"][1]["s"] == {"5": "x"} and r["cols"][2]["lg"] == [5]
    assert r["sound"] == {"song": "Metallica - One", "pid": "20261015-1"}
    again = client.post("/api/vguitar/riffs", json={"id": r["id"], "name": "Sloka", "riff": {**RIFF, "bpm": 90}}).json()
    assert again["id"] == r["id"] and again["bpm"] == 90
    rows = client.get("/api/vguitar/riffs").json()["riffs"]
    assert [x["name"] for x in rows] == ["Sloka"]
    assert client.delete(f"/api/vguitar/riffs/{r['id']}").json()["ok"]
    assert client.get("/api/vguitar/riffs").json()["riffs"] == []
    assert client.delete("/api/vguitar/riffs/nope").status_code == 404


def test_odd_riffs_are_refused_or_cleaned(client):
    assert client.post("/api/vguitar/riffs", json={"name": "x", "riff": {"cols": [], "tuning": [64] * 6}}).status_code == 400
    assert client.post("/api/vguitar/riffs", json={"name": "x", "riff": {**RIFF, "tuning": [1, 2]}}).status_code == 400
    r = client.post("/api/vguitar/riffs", json={"name": "", "riff": {**RIFF, "div": 5, "bpm": 9999,
                    "cols": [{"s": {"0": 99, "1": "q", "2": 5, "9": 1}}]}}).json()
    assert r["name"] == "Riff" and r["div"] == 4 and r["bpm"] == 300 and r["cols"][0]["s"] == {"2": 5}


SEARCH = """<table><tr><th>Artist</th></tr>
<tr><td class="artist"><a href="/search?search_term=Band">Band</a></td><td class="song"><a href="/tab/band/song-tabs-11">Song (ver 2)</a></td>
<td class="rating" data-value="4.6">4.6/5 (649)</td><td class="type">Tabs</td><td></td></tr>
<tr><td class="artist"><a href="/search?search_term=Band">Band</a></td><td class="song"><a href="/tab/band/song-chords-12">Song &amp; More</a></td>
<td class="rating" data-value="4.1">4.1/5 (3)</td><td class="type">Chords</td><td></td></tr></table>
<a href="/search?search_term=band&page=2">2</a><a href="/search?search_term=band&page=3">3</a>"""
TAB = """<html><head><title>Band - Song</title></head><body><div class="tab font-monospace">Intro<br>
e|---0---|<br>B|---0---|<br>G|---0---|<br>D|---2---|<br>A|-0-2---|<br>E|-------|<br>Mail:&nbsp;<a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="ab">[email&#160;protected]</a></div>
<h2>Alternative versions</h2><ul><li><a href="/tab/band/song-tabs-13"> Version 2 (Tabs) 4.2/5 (11) </a></li></ul></body></html>"""


def test_freetar_pages_read():
    got = vguitar.parse_search(SEARCH)
    assert got["pages"] == 3 and [r["type"] for r in got["results"]] == ["Tabs", "Chords"]
    assert got["results"][0] == {"url": "/tab/band/song-tabs-11", "artist": "Band", "song": "Song", "version": 2,
                                 "rating": 4.6, "votes": 649, "type": "Tabs"}
    assert got["results"][1]["song"] == "Song & More" and got["results"][1]["version"] == 1
    t = vguitar.parse_tab(TAB)
    assert (t["artist"], t["song"]) == ("Band", "Song") and t["versions"][0]["url"] == "/tab/band/song-tabs-13"
    lines = t["text"].split("\n")
    assert lines[1] == "e|---0---|" and lines[5] == "A|-0-2---|" and lines[-1] == "Mail:" and "protected" not in t["text"]


def test_tabs_searched_opened_and_kept(client, monkeypatch):
    asked = []

    def get(url, timeout=75):
        asked.append(url)
        return SEARCH if "/search?" in url else TAB
    assert client.get("/api/vguitar/tabs/search?q=band").status_code == 502          # no network: said
    monkeypatch.setattr(vguitar, "_get", get)
    assert client.get("/api/vguitar/tabs/search?q=b").status_code == 400
    r = client.get("/api/vguitar/tabs/search?q=Band%20%20&page=2").json()
    assert r["q"] == "Band" and r["page"] == 2 and len(r["results"]) == 2 and asked[-1] == "https://freetar.de/search?search_term=Band&page=2"
    client.get("/api/vguitar/tabs/search?q=band&page=2")
    assert len(asked) == 1                                                            # the same search: remembered
    assert client.get("/api/vguitar/tabs/open?url=/etc/passwd").status_code == 400
    assert client.get("/api/vguitar/tabs/open?url=https://evil.example/tab/a/../../x").status_code == 400
    t = client.get("/api/vguitar/tabs/open?url=/tab/band/song-tabs-11").json()
    assert t["song"] == "Song" and t["url"] == "/tab/band/song-tabs-11" and asked[-1] == "https://freetar.de/tab/band/song-tabs-11"
    client.get("/api/vguitar/tabs/open?url=/tab/band/song-tabs-11")
    assert len(asked) == 2                                                            # opened again: from the cache
    assert client.post("/api/vguitar/tabs/mine", json={"song": "x", "text": "  "}).status_code == 400
    k = client.post("/api/vguitar/tabs/mine", json={"artist": "Band", "song": "Song", "text": t["text"], "url": t["url"]}).json()
    again = client.post("/api/vguitar/tabs/mine", json={"artist": "Band", "song": "Song", "text": t["text"] + "\nmore", "url": t["url"]}).json()
    assert again["id"] == k["id"]
    client.post("/api/vguitar/tabs/mine", json={"artist": "Jiná", "song": "Píseň", "text": "e|--3--|"})
    assert [x["song"] for x in client.get("/api/vguitar/tabs/mine").json()["tabs"]] == ["Song", "Píseň"]
    assert [x["song"] for x in client.get("/api/vguitar/tabs/mine?q=jin").json()["tabs"]] == ["Píseň"]
    assert client.delete(f"/api/vguitar/tabs/mine/{k['id']}").json()["ok"]
    assert client.delete("/api/vguitar/tabs/mine/nope").status_code == 404
    assert [x["song"] for x in client.get("/api/vguitar/tabs/mine").json()["tabs"]] == ["Píseň"]
