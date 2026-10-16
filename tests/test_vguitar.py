"""Virtuální kytara: riffs kept, only in their known shape."""
import json

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


def _store(data):
    import html
    return '<div class="js-store" data-content="' + html.escape(json.dumps({"store": {"page": {"data": data}}})) + '"></div>'


UG_SEARCH = _store({"results": [
    {"type": "Tabs", "artist_name": "Band", "song_name": "Song", "tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-tabs-11", "version": 2, "votes": 649, "rating": 4.63,
     "difficulty": "intermediate", "tonality_name": "Em", "tab_access_type": "public"},
    {"type": "Bass Tabs", "artist_name": "Band", "song_name": "Song", "tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-bass-12", "version": 1, "votes": 3, "rating": 4},
    {"type": "Pro", "artist_name": "Band", "song_name": "Song", "tab_url": "https://tabs.ultimate-guitar.com/pro/x", "version": 1},
    {"type": "Tabs", "marketing_type": "TabPro", "artist_name": "Band", "song_name": "Song", "tab_url": "https://tabs.ultimate-guitar.com/tab/x/y-1"}],
    "pagination": {"current": 1, "total": 3}})
UG_TAB = _store({"tab": {"artist_name": "Band", "song_name": "Song"}, "tab_view": {
    "wiki_tab": {"content": "[tab]e|---0---|\r\nB|---[ch]Am[/ch]---|[/tab]"}, "meta": {"tuning": {"name": "Standard", "value": "E A D G B E"}},
    "versions": [{"tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-tabs-13", "version": 3, "type": "Tabs", "rating": 4.21, "votes": 11},
                 {"tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-official-1", "version": 1, "type": "Official"}]}})


def test_ultimate_guitar_pages_read():
    got = vguitar.ug_search(UG_SEARCH)
    # Pro is listed, to be opened on Ultimate Guitar itself; an advert is not
    assert got["pages"] == 3 and [(r["url"], r["type"], r["web"]) for r in got["results"]] == [
        ("/tab/band/song-tabs-11", "Tabs", False), ("/tab/band/song-bass-12", "Bass", False), ("/pro/x", "Pro", True)]
    assert got["results"][0]["rating"] == 4.6 and got["results"][2]["href"] == "https://tabs.ultimate-guitar.com/pro/x"
    t = vguitar.ug_tab(UG_TAB)
    assert t["text"] == "e|---0---|\nB|---Am---|" and t["tuning"] == "E A D G B E (Standard)"
    assert [v["url"] for v in t["versions"]] == ["/tab/band/song-tabs-13"]


def test_ultimate_guitar_first_freetar_when_it_fails(client, monkeypatch):
    import urllib.error
    asked = []

    def get(url, timeout=75):
        asked.append(url)
        if "ultimate-guitar.com" in url:
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)
        return SEARCH if "/search?" in url else TAB
    monkeypatch.setattr(vguitar, "_get", get)
    r = client.get("/api/vguitar/tabs/search?q=band").json()
    assert r["via"] == "Freetar" and len(r["results"]) == 2 and asked[0].startswith("https://www.ultimate-guitar.com/search.php?")
    assert client.get("/api/vguitar/tabs/open?url=/tab/band/song-tabs-11").json()["song"] == "Song" and asked[-1].startswith("https://freetar.de/")

    def nothing(url, timeout=75):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(vguitar, "_get", nothing)
    assert client.get("/api/vguitar/tabs/search?q=nic%20takoveho").json()["results"] == []


def test_tabs_searched_opened_and_kept(client, monkeypatch):
    asked = []

    def get(url, timeout=75):
        asked.append(url)
        if "ultimate-guitar.com" in url:
            return UG_SEARCH if "/search.php?" in url else UG_TAB
        return SEARCH if "/search?" in url else TAB
    assert client.get("/api/vguitar/tabs/search?q=band").status_code == 502          # no network: said
    monkeypatch.setattr(vguitar, "_get", get)
    assert client.get("/api/vguitar/tabs/search?q=b").status_code == 400
    r = client.get("/api/vguitar/tabs/search?q=Band%20%20&page=2").json()
    assert r["q"] == "Band" and r["page"] == 2 and len(r["results"]) == 3 and r["via"] == "Ultimate Guitar"
    assert asked[-1] == "https://www.ultimate-guitar.com/search.php?search_type=title&value=Band&page=2"
    client.get("/api/vguitar/tabs/search?q=band&page=2")
    assert len(asked) == 1                                                            # the same search: remembered
    assert client.get("/api/vguitar/tabs/open?url=/etc/passwd").status_code == 400
    assert client.get("/api/vguitar/tabs/open?url=https://evil.example/tab/a/../../x").status_code == 400
    t = client.get("/api/vguitar/tabs/open?url=/tab/band/song-tabs-11").json()
    assert t["song"] == "Song" and t["url"] == "/tab/band/song-tabs-11" and asked[-1] == "https://tabs.ultimate-guitar.com/tab/band/song-tabs-11"
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


UG_EXPLORE = _store({"filters": [
    {"name": "Type", "param_name": "type", "sort_index": 10, "values": [{"name": "Tab", "url_name": "Tabs", "count": 366391}, {"name": "Guitar Pro Tab", "url_name": "Pro", "count": 5}]},
    {"name": "Difficulty", "param_name": "difficulty", "sort_index": 0, "values": [{"name": "Intermediate", "url_name": 3, "count": 7}]},
    {"name": "Capo", "param_name": "capo", "sort_index": 70, "values": [{"name": "No capo", "url_name": 0, "count": 9}]},
    {"name": "Key", "param_name": "tonality", "sort_index": 100, "values": [{"name": "Em", "url_name": 18, "count": 3}]},
    {"name": "Part", "param_name": "part", "sort_index": 110, "values": [{"name": "Whole song", "url_name": "", "count": 9}]},
    {"name": "Something new", "param_name": "whatever", "values": []}],
    "totalResults": 3399, "pagination": {"pages": 20, "current": 0},
    "data": {"tabs": [{"type": "Tabs", "artist_name": "Tool", "song_name": "Schism", "tab_url": "https://tabs.ultimate-guitar.com/tab/tool/schism-tabs-1", "rating": 4.9, "votes": 10}]}})


def test_ultimate_guitars_own_filters(client, monkeypatch):
    asked = []

    def get(url, timeout=75):
        asked.append(url)
        return UG_EXPLORE if "/explore" in url else UG_SEARCH
    monkeypatch.setattr(vguitar, "_get", get)
    f = client.get("/api/vguitar/tabs/filters").json()
    assert [g["param"] for g in f["groups"]] == ["difficulty", "type", "capo", "tonality", "part"]
    assert f["groups"][1]["values"][0] == {"v": "Tabs", "name": "Taby", "count": 366391} and f["groups"][2]["values"][0]["v"] == "0"
    assert f["groups"][4]["values"] == [] and f["groups"][1]["onSearch"] and not f["groups"][2]["onSearch"]
    client.get("/api/vguitar/tabs/filters")
    assert len(asked) == 1                                                           # kept a week
    # no text: Explore, with every filter there
    r = client.get("/api/vguitar/tabs/search?type=Tabs&tuning=12&genres=8&order=rating_desc&tuning=../x").json()
    assert r["browse"] and r["total"] == 3399 and r["pages"] == 20 and r["results"][0]["song"] == "Schism"
    assert asked[-1] == "https://www.ultimate-guitar.com/explore?type%5B%5D=Tabs&tuning%5B%5D=12&genres%5B%5D=8&order=rating_desc&page=1"
    # text: its search, by kind there and by what the rows say here
    r = client.get("/api/vguitar/tabs/search?q=band&type=Tabs&type=Pro&difficulty=3&tonality=18&order=rating").json()
    assert [x["url"] for x in r["results"]] == ["/tab/band/song-tabs-11"]
    assert asked[-1] == ("https://www.ultimate-guitar.com/search.php?search_type=title&value=band&page=1&type%5B%5D=200&type%5B%5D=500"
                         "&rating%5B%5D=4&rating%5B%5D=5&order=myweight")
