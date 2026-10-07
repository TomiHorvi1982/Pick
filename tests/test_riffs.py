"""Riffs: stretches of songs kept in the library, sorted by band and song."""
import asyncio
import io
import json

import server


class Up:
    def __init__(self, name, data):
        self.filename, self._d = name, data

    async def read(self):
        return self._d


MIDI = b"MThd" + bytes(10) + b"MTrk" + bytes(8)


def add(meta, midi=True):
    return asyncio.run(server.riffs_add(json.dumps(meta), Up("x.wav", b"RIFF" + bytes(2000)), Up("x.mid", MIDI) if midi else None))


def test_riff_is_filed_by_band_and_song(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    r = add({"artist": "Kabát", "song": "Dole v dole", "riff": "Hlavní riff", "instrument": "Kytara", "bpm": 120.5, "key": "E moll",
             "tuning": "Drop D", "dur": 8.0, "notes": 12, "source": {"song": "Kabát - Dole v dole", "stem": "guitar", "from": 10, "to": 18}})
    assert r["audio"] == "Loops/Riffs/Kabát/Dole v dole/Hlavní riff - Kytara.wav"
    assert r["midi"] == "Loops/Riffs/Kabát/Dole v dole/Hlavní riff - Kytara.mid"
    assert (tmp_path / r["audio"]).is_file() and (tmp_path / r["midi"]).read_bytes() == MIDI
    # the same name again is another file, not the first one overwritten
    again = add({"artist": "Kabát", "song": "Dole v dole", "riff": "Hlavní riff", "instrument": "Kytara"}, midi=False)
    assert again["audio"].endswith("Hlavní riff - Kytara (2).wav") and again["midi"] is None
    add({"artist": "AC/DC", "song": "T.N.T.", "riff": "Intro: první", "instrument": "Basa"})
    names = [(x["artist"], x["riff"]) for x in server.riffs_list()]
    assert names == [("AC DC", "Intro první"), ("Kabát", "Hlavní riff"), ("Kabát", "Hlavní riff")]


def test_library_shows_what_the_riff_is(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    r = add({"artist": "Kabát", "song": "Dole v dole", "riff": "Sólo", "instrument": "Kytara", "bpm": 98, "key": "A dur", "tuning": "E Standard", "dur": 4})
    tags = server._riff_tags(r["audio"])
    assert (tags["title"], tags["artist"], tags["album"]) == ("Sólo", "Kabát", "Dole v dole")
    assert tags["riff"]["bpm"] == 98 and tags["riff"]["tuning"] == "E Standard"
    assert server._riff_tags("Loops/Riffs/else.wav") == {}


def test_deleted_files_leave_the_index(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    a = add({"artist": "A", "song": "S", "riff": "one"})
    b = add({"artist": "A", "song": "S", "riff": "two"})
    (tmp_path / a["audio"]).unlink()
    (tmp_path / b["midi"]).unlink()
    left = server.riffs_list()
    assert [x["riff"] for x in left] == ["two"] and left[0]["midi"] is None


class Req:
    def __init__(self, data):
        self._d = data

    async def body(self):
        return self._d


def test_edited_midi_goes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    r = add({"artist": "A", "song": "S", "riff": "one"})
    new = MIDI + b"\x00"
    out = asyncio.run(server.riffs_midi_save(Req(new), name="whatever", rel=r["midi"]))
    assert out == {"rel": r["midi"], "replaced": True} and (tmp_path / r["midi"]).read_bytes() == new
    # no riff named: a file of its own
    out = asyncio.run(server.riffs_midi_save(Req(new), name="Moje linka.mid", rel=""))
    assert out["rel"] == "Loops/Riffs/MIDI/Moje linka.mid" and not out["replaced"]
    # not a path out of the riffs, and not something that is no MIDI
    out = asyncio.run(server.riffs_midi_save(Req(new), name="x", rel="../../etc/passwd"))
    assert out["rel"] == "Loops/Riffs/MIDI/x.mid"
    assert asyncio.run(server.riffs_midi_save(Req(b"nope"), name="x")).status_code == 400
