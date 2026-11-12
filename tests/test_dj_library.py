"""Pick DJ's library: every song with band, tuning, year, genre and playlists, from Steems, Songs/Playlists and mapped folders."""
import json
import subprocess

import server


def _mp3(path, artist, title, year):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([server.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=1", "-metadata", f"artist={artist}",
                    "-metadata", f"title={title}", "-metadata", f"date={year}-05-01", str(path)], check=True)


def test_dj_library_sorts_by_what_pick_knows(client, tmp_music_root, tmp_path, monkeypatch):
    import songbook, setlists
    bands = tmp_path / "bands.json"
    bands.write_text(json.dumps({"sepultura": {"genres": ["thrash metal", "groove metal"], "at": 9e9}}))
    monkeypatch.setattr(songbook, "BANDS", bands)
    monkeypatch.setattr(setlists, "PATH", tmp_path / "setlists.json")
    monkeypatch.setattr(server, "_DJ_GENRES_FILL", {"busy": True})             # no MusicBrainz in a test
    monkeypatch.setattr(server, "_DJ_LIB", {"at": 0.0, "data": None, "busy": False})
    _mp3(tmp_music_root / "Playlists" / "Thrash" / "Sepultura - Roots.mp3", "Sepultura", "Roots", 1996)
    st = tmp_music_root / "Stems" / "Sepultura - Roots"
    for s in server.STEM_NAMES[:6]:
        _mp3(st / f"{s}.mp3", "", "", 1996) if s in ("drums", "bass", "guitar", "vocals", "piano", "other") else None
    (st / "meta.json").write_text(json.dumps({"artist": "Sepultura", "title": "Roots"}))
    (st / "tuning.json").write_text(json.dumps({"tuning": [62, 57, 53, 48, 43, 38]}))
    outside = tmp_path / "Moje"
    _mp3(outside / "Live" / "Band - Jam.mp3", "Band", "Jam", 2020)
    assert client.post("/api/library/mounts", json={"path": str(outside)}).status_code == 200
    d = client.get("/api/dj/library").json()
    by = {x["title"]: x for x in d["items"]}
    roots, jam = by["Roots"], by["Jam"]
    assert roots["source"] == "stems" and not roots["live"] and roots["year"] == 1996 and roots["genres"][0] == "thrash metal"
    assert roots["playlists"] == ["Thrash"] and roots["tuning"] == [62, 57, 53, 48, 43, 38] and roots["band"] == "Sepultura"
    assert sum(1 for x in d["items"] if x["title"] == "Roots") == 1                  # the playlist copy is the same song
    assert jam["source"] == "@Moje" and jam["live"] and jam["rel"] == "@Moje/Live/Band - Jam.mp3" and jam["year"] == 2020 and jam["playlists"] == ["Live"]
    assert d["mounts"] == [{"name": "Moje", "path": str(outside.resolve()), "exists": True, "count": 1}]
    assert server._resolve_under_music_root(jam["rel"]) == (outside / "Live" / "Band - Jam.mp3").resolve()   # live separation opens it
