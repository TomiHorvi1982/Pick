"""Folders mapped into the library: shown as "@Name", browsed and played
like any library folder, nothing copied — and never deleted as a whole."""
import pytest

import server


@pytest.fixture
def mounted(client, tmp_music_root, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    ext = tmp_path / "Google Drive" / "Hudba"
    (ext / "Alba").mkdir(parents=True)
    (ext / "Alba" / "song.mp3").write_bytes(b"x" * 100)
    res = client.post("/api/library/mounts", json={"path": str(ext)}).json()
    assert res["added"]["rel"] == "@Hudba"
    return ext


def test_a_mapped_folder_shows_at_the_library_root(client, mounted):
    root = client.get("/api/library/browse").json()
    m = next(e for e in root["entries"] if e.get("mount"))
    assert (m["name"], m["rel"], m["path"]) == ("Hudba", "@Hudba", str(mounted.resolve()))


def test_it_is_browsed_and_played_without_copying(client, mounted, tmp_music_root):
    inside = client.get("/api/library/browse", params={"rel": "@Hudba/Alba"}).json()
    assert [b["name"] for b in inside["breadcrumb"]][1:] == ["Hudba", "Alba"]
    song = next(e for e in inside["entries"] if e["name"] == "song.mp3")
    assert song["rel"] == "@Hudba/Alba/song.mp3"
    assert server._resolve_under_music_root(song["rel"]) == (mounted / "Alba" / "song.mp3").resolve()
    assert not any(tmp_music_root.rglob("song.mp3"))           # nothing copied in


def test_paths_cannot_escape_a_mapped_folder(client, mounted):
    assert server._resolve_under_music_root("@Hudba/../../etc") is None
    assert server._resolve_under_music_root("@Nope/x") is None
    assert client.get("/api/library/browse", params={"rel": "@Hudba/../.."}).status_code == 404


def test_the_mapped_folder_itself_cannot_be_deleted_or_moved(client, mounted):
    assert client.post("/api/library/fs/delete", json={"rel": "@Hudba"}).status_code == 400
    assert client.post("/api/library/fs/move", json={"rel": "@Hudba", "dest_rel": ""}).status_code == 400
    assert mounted.is_dir()


def test_mapping_twice_or_inside_the_library_is_refused_and_unmapping_keeps_files(client, mounted, tmp_music_root):
    assert client.post("/api/library/mounts", json={"path": str(mounted)}).status_code == 400
    assert client.post("/api/library/mounts", json={"path": str(tmp_music_root)}).status_code == 400
    client.post("/api/library/mounts/remove", json={"name": "Hudba"})
    assert client.get("/api/library/mounts").json()["mounts"] == []
    assert (mounted / "Alba" / "song.mp3").is_file()
