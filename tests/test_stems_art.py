"""Pictures of separated songs: the source file's own cover first, then the
album's cover by artist + album (shared by the whole album)."""
import server


def _stems(root, name):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    return d


def test_the_cover_inside_the_source_file_is_used(client, tmp_music_root, monkeypatch):
    from mutagen.id3 import ID3, APIC
    monkeypatch.setattr(server, "_STEMS_SOURCE_INDEX", {"at": 0.0, "map": {}})
    _stems(tmp_music_root, "Song")
    (tmp_music_root / "Songs").mkdir()
    src = tmp_music_root / "Songs" / "Song.mp3"
    src.write_bytes(b"")
    tags = ID3()
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 2000
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg))
    tags.save(str(src))
    art = client.get("/api/stems/Song/art").json()
    assert art["source"] == "file" and art["url"] == "/api/stems/Song/art/image"
    img = client.get("/api/stems/Song/art/image")
    assert img.status_code == 200 and img.content == jpeg and img.headers["content-type"] == "image/jpeg"


class _FakeYT:
    def __init__(self):
        self.calls = []

    def search(self, query, filter=None, limit=1):
        self.calls.append((query, filter))
        if filter == "albums":
            return [{"artists": [{"name": "Somebody Else"}], "thumbnails": [{"url": "https://lh3.googleusercontent.com/wrong", "width": 500}]},
                    {"artists": [{"name": "Sepultura"}], "thumbnails": [{"url": "https://lh3.googleusercontent.com/chaos", "width": 500}]}]
        return []


def test_an_album_cover_is_found_by_artist_and_album_and_shared(client, tmp_music_root, monkeypatch):
    monkeypatch.setattr(server, "_STEMS_SOURCE_INDEX", {"at": 0.0, "map": {}})
    fake = _FakeYT()
    monkeypatch.setattr(server, "yt", lambda: fake)
    monkeypatch.setattr(server, "_THUMB_CACHE", None, raising=False)
    monkeypatch.setattr(server, "_load_thumb_cache", lambda: {})
    monkeypatch.setattr(server, "_save_thumb_cache", lambda: None)
    for name in ("Sepultura - Amen", "Sepultura - Manifest"):
        d = _stems(tmp_music_root, name)
        title = name.split(" - ")[1]
        (d / "meta.json").write_text('{"artist": "Sepultura", "title": "%s", "album": "Chaos A.D."}' % title)
    a = client.get("/api/stems/Sepultura - Amen/art").json()
    b = client.get("/api/stems/Sepultura - Manifest/art").json()
    assert a["url"] == b["url"] == "https://lh3.googleusercontent.com/chaos"   # the right artist, not the first hit
    assert ("Sepultura Chaos A.D.", "albums") in fake.calls
