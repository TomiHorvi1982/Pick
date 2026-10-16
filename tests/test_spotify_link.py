"""Odkaz ze Spotify: what a pasted link is, and what Pick says of links it does not read."""
import spotify_link as sl


def test_links_and_uris_are_read_for_what_they_are():
    assert sl.parse("https://open.spotify.com/playlist/29qQfxhdbPh6DfvIFienJ0?si=abc123") == ("playlist", "29qQfxhdbPh6DfvIFienJ0")
    assert sl.parse("  open.spotify.com/intl-cs/album/1ATL5GLyefJaxhQzSPVrLX ") == ("album", "1ATL5GLyefJaxhQzSPVrLX")
    assert sl.parse("spotify:track:12lqar9nTaPDeIk23xdLeB") == ("track", "12lqar9nTaPDeIk23xdLeB")
    assert sl.parse("https://open.spotify.com/user/31ov4stq6duefmoofvuxf4bdgrsu") == ("user", "31ov4stq6duefmoofvuxf4bdgrsu")
    assert sl.parse("https://evil.example/open.spotify.com/playlist/x") is None and sl.parse("Bruslení") is None


def test_a_playlist_is_shown_with_its_songs(client, monkeypatch):
    calls = []

    def read(kind, url):
        calls.append((kind, url))
        return {"name": "Bruslení", "author": "Někdo", "cover": "", "tracks": [{"title": "A", "artists": "B", "album": "C", "duration": 200, "url": "u"}]}
    monkeypatch.setattr(sl, "read", read)
    monkeypatch.setattr(sl, "_CACHE", {})
    got = client.get("/api/spotify/link", params={"url": "https://open.spotify.com/playlist/AbC123?si=x"}).json()
    assert got["url"] == "https://open.spotify.com/playlist/AbC123" and got["count"] == 1 and got["name"] == "Bruslení" and got["kind"] == "playlist"
    client.get("/api/spotify/link", params={"url": "spotify:playlist:AbC123"})
    assert calls == [("playlist", "https://open.spotify.com/playlist/AbC123")]          # the second time from memory


def test_links_pick_does_not_read_say_what_to_send(client, monkeypatch):
    monkeypatch.setattr(sl, "read", lambda kind, url: (_ for _ in ()).throw(RuntimeError("private")))
    monkeypatch.setattr(sl, "_CACHE", {})
    r = client.get("/api/spotify/link", params={"url": "https://open.spotify.com/user/abc"})
    assert r.status_code == 400 and "Kopírovat odkaz" in r.json()["error"]
    assert client.get("/api/spotify/link", params={"url": "Bruslení"}).status_code == 400
    r = client.get("/api/spotify/link", params={"url": "https://open.spotify.com/playlist/Priv4te"})
    assert r.status_code == 502 and "veřejný" in r.json()["error"]
