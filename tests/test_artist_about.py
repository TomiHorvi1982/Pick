"""The artist rail's second half: biography, line-up, photos and tour dates.

Nothing here touches the network — the three outside services are stubbed, so
these test the shaping and the guards rather than Wikipedia's current copy.
"""

import json
import time

import pytest

import server


# ------------------------------------------------------ picking the right page

def test_deaccent_matches_inflected_czech():
    """The lead section says "zpěvačka"; the stem in the word list is "zpev".
    Matching has to survive both the diacritics and the inflection — an
    earlier version looked for "zpěvá" and found nothing.
    """
    flat = server._deaccent("Lucie Bílá je česká zpěvačka a herečka.")
    assert "zpev" in flat
    assert any(w in flat for w in server._MUSIC_WORDS)


def test_music_words_reject_a_homonym():
    """"Kabát" is also a coat. A page that never says it is an act is the
    wrong page."""
    coat = server._deaccent(
        "Kabát je svrchní oděv, který chrání tělo před chladem a deštěm.")
    assert not any(w in coat for w in server._MUSIC_WORDS)


# --------------------------------------------------------------- photo dedupe

@pytest.mark.parametrize("url, expected", [
    ("https://upload.wikimedia.org/wikipedia/commons/d/da/Band_01.jpg",
     "band_01.jpg"),
    ("https://thumb.wikimedia.org/wikipedia/commons/thumb/d/da/Band_01.jpg/"
     "640px-Band_01.jpg", "band_01.jpg"),
    ("https://upload.wikimedia.org/wikipedia/commons/6/6e/Sylosis_%281125%29.jpg",
     "sylosis_(1125).jpg"),
])
def test_commons_key_identifies_the_same_file(url, expected):
    """The lead image and the page thumbnail are the same photograph at two
    sizes; keyed by URL they'd both be listed."""
    assert server._commons_key(url) == expected


# ------------------------------------------------------------ response shaping

@pytest.fixture
def stub_sources(monkeypatch, tmp_path):
    """Replaces Wikipedia / MusicBrainz / Bandsintown with fixed answers."""
    monkeypatch.setattr(server, "ABOUT_CACHE_PATH", tmp_path / "about.json")
    monkeypatch.setattr(server, "_about_cache", None)

    async def wiki(client, name):
        return {
            "text": "Testovací kapela z Brna.",
            "title": name, "lang": "cs",
            "url": "https://cs.wikipedia.org/wiki/Test",
            "image": "https://upload.wikimedia.org/wikipedia/commons/a/aa/Lead.jpg",
            "photos": [
                {"url": "https://thumb.wikimedia.org/wikipedia/commons/thumb/"
                        "a/aa/Lead.jpg/640px-Lead.jpg", "title": "Lead"},
                {"url": "https://upload.wikimedia.org/wikipedia/commons/b/bb/Two.jpg",
                 "title": "Two"},
            ],
        }

    async def mb(client, name):
        return {
            "mbid": "abc", "kind": "Group", "formed": "1990", "ended": None,
            "origin": "Brno, Czechia", "genres": ["rock"],
            "members": [{"name": "A", "roles": ["guitar"], "current": True}],
        }

    async def bit(client, name):
        return [{
            "datetime": "2027-02-16T20:00:00", "venue": "ROXY",
            "city": "Prague", "region": "", "country": "Czech Republic",
            "lineup": ["Test"], "url": "https://example.test/e/1",
            "soldOut": False, "title": "",
        }]

    monkeypatch.setattr(server, "_wiki_about", wiki)
    monkeypatch.setattr(server, "_mb_about", mb)
    monkeypatch.setattr(server, "_bit_events", bit)


def test_about_returns_every_section(client, stub_sources):
    data = client.get("/api/artist/about", params={"name": "Test"}).json()

    assert data["bio"]["lang"] == "cs"
    assert data["facts"]["formed"] == "1990"
    assert data["facts"]["genres"] == ["rock"]
    assert [m["name"] for m in data["members"]] == ["A"]
    assert data["events"][0]["city"] == "Prague"


def test_about_lists_the_lead_photo_once(client, stub_sources):
    """The lead image is normally also one of the page images."""
    photos = client.get("/api/artist/about", params={"name": "Test"}).json()["photos"]

    keys = [server._commons_key(p["url"]) for p in photos]
    assert len(keys) == len(set(keys))
    assert "lead.jpg" in keys


def test_about_needs_a_name(client, stub_sources):
    assert client.get("/api/artist/about", params={"name": "  "}).status_code == 400


def test_about_is_cached_on_disk(client, stub_sources, monkeypatch):
    client.get("/api/artist/about", params={"name": "Test"})

    async def explode(*a, **kw):
        raise AssertionError("second call should have been served from cache")

    monkeypatch.setattr(server, "_wiki_about", explode)
    again = client.get("/api/artist/about", params={"name": "test"}).json()
    assert again["facts"]["origin"] == "Brno, Czechia"
    assert server.ABOUT_CACHE_PATH.exists()


def test_a_thin_answer_expires_quickly(client, monkeypatch, tmp_path):
    """One throttled minute shouldn't leave an artist blank for a day."""
    monkeypatch.setattr(server, "ABOUT_CACHE_PATH", tmp_path / "about.json")
    monkeypatch.setattr(server, "_about_cache", None)

    async def nothing(client, name):
        return None

    async def no_facts(client, name):
        return {}

    async def no_events(client, name):
        return []

    monkeypatch.setattr(server, "_wiki_about", nothing)
    monkeypatch.setattr(server, "_mb_about", no_facts)
    monkeypatch.setattr(server, "_bit_events", no_events)

    client.get("/api/artist/about", params={"name": "Nobody"})
    entry = json.loads(server.ABOUT_CACHE_PATH.read_text())["nobody"]
    assert entry["ttl"] == server.ABOUT_MISS_TTL
    assert entry["ttl"] < server.ABOUT_TTL


def test_nationality_tags_are_not_genres():
    assert "czech" in server._NOT_GENRES
    assert "rock" not in server._NOT_GENRES


# -------------------------------------------------------------- image proxying

def test_proxy_still_refuses_unknown_hosts(client):
    res = client.get("/api/thumb", params={"url": "https://evil.test/x.jpg"})
    assert res.status_code == 403


def test_proxy_allows_the_artist_photo_hosts(client):
    for host in ("upload.wikimedia.org", "photos.bandsintown.com"):
        assert any(host == h or host.endswith("." + h) for h in server._THUMB_HOSTS), host
