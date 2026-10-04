"""Czech bands from more than YouTube Music: Bandzone shelves, and links to
Bandzone and Supermusic on an artist's page. The network is faked."""

import server

BZ = ('<div class="profileLink band x"><a href="/{slug}" title="{n}"><img src="https://bzmedia.cz/{slug}.jpg" alt=""/>'
      '<div class="description cutter"><h4 class="title cutter">{n}</h4><span class="genre cutter">{g}</span>')


def _bz_html(*bands):
    return "".join(BZ.format(slug=n.lower().replace(" ", ""), n=n, g=g) for n, g in bands)


def test_rock_and_metal_is_one_czech_genre_over_many_bandzone_genres():
    ids = server._bz_ids_for("Rock & Metal")
    assert 40 in ids and 31 in ids and len(ids) > 5     # rock, metal and their kin
    assert server._bz_ids_for("nothing like it") == []


def test_czech_shelf_mixes_bandzone_with_youtube_without_duplicates(monkeypatch):
    server._BROWSE_ARTISTS_CACHE.clear()
    pages = {(40, 1): [{"name": "Kabát", "image": "", "bandzone": "https://bandzone.cz/kabat", "source": "bandzone"}],
             (31, 1): [{"name": "Dymytry", "image": "", "bandzone": "https://bandzone.cz/dymytry", "source": "bandzone"}]}
    monkeypatch.setattr(server, "_bz_page", lambda gid, page: pages.get((gid, page), []))

    class YT:
        def search(self, phrase, filter=None, limit=30):
            return [{"artist": "Kabat", "browseId": "UC1"}, {"artist": "Arakain", "browseId": "UC2"}]
    monkeypatch.setattr(server, "yt", lambda: YT())

    res = server.browse_artists("Rock & Metal", region="cz", offset=0, limit=4)
    import json
    names = [a["name"] for a in json.loads(res.body)["artists"]] if hasattr(res, "body") else [a["name"] for a in res["artists"]]
    assert "Dymytry" in names and "Arakain" in names     # both sources, early
    assert ("Kabát" in names) + ("Kabat" in names) == 1  # one band, not two spellings


def test_bandzone_page_is_parsed(monkeypatch):
    server._BZ_CACHE.clear()

    class Resp:
        def read(self):
            return _bz_html(("Törr", "black-metal"), ("Čad", "rock")).encode()

    class Opener:
        addheaders = []

        def open(self, url, timeout=0):
            assert "g=31" in url and "orderType=fan_count" in url and "p=2" in url
            return Resp()
    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    bands = server._bz_page(31, 2)
    assert [b["name"] for b in bands] == ["Törr", "Čad"]
    assert bands[0]["bandzone"] == "https://bandzone.cz/törr" and bands[0]["source"] == "bandzone"


def test_artist_links_only_for_the_same_band(monkeypatch):
    server._CZ_LINKS.clear()

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body.encode()

    def urlopen(req, timeout=0):
        url = req.full_url
        if "bandzone" in url:
            return Resp(_bz_html(("Kabát revival", "rock"), ("Kabát", "rock")))
        return Resp('<a href="skupina.php?idskupiny=35">Kabát</a>')
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    out = server.artist_cz_links("Kabát")
    assert out["bandzone"] == "https://bandzone.cz/kabát"
    assert out["supermusic"].endswith("idskupiny=35")


def test_ten_czech_genres_each_search_all_their_styles(monkeypatch):
    assert len(server._CZ_GENRES) == 10
    server._BROWSE_ARTISTS_CACHE.clear()
    monkeypatch.setattr(server, "_bz_page", lambda gid, page: [])
    asked = []

    class YT:
        def search(self, phrase, filter=None, limit=30):
            asked.append(phrase)
            return [{"artist": f"Band {len(asked)}", "browseId": f"UC{len(asked)}"}]
    monkeypatch.setattr(server, "yt", lambda: YT())
    server.browse_artists("Hip Hop & Rap", region="cz", offset=0, limit=3)
    assert any(" hip hop " in a for a in asked) and any(" rap " in a for a in asked)
    assert not any("&" in a for a in asked)            # the label itself is never searched
