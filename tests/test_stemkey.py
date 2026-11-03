"""The key of a song in Steems (stemkey.py, what the songbook asks for)."""
import stemkey as ls


def test_the_key_listens_to_the_chords_and_admits_when_it_is_unsure():
    # A song in C minor that sits on G a lot: the chords say C minor.
    chords = [{"root": "C", "quality": "min", "start": 0, "end": 40}, {"root": "F", "quality": "min", "start": 40, "end": 60},
              {"root": "G", "quality": "maj", "start": 60, "end": 80}, {"root": "G#", "quality": "maj", "start": 80, "end": 95}]
    k = ls.estimate_key(None, chords=chords)
    assert k["key"] == "C moll" and k["fromChords"] == "C moll" and k["fromSound"] is None
    assert ls.estimate_key(None, chords=[]) is None                     # nothing to go on: no key, not a guess
    assert 0 <= k["confidence"] <= 1


def test_stem_files_by_role(tmp_path):
    for n in ("drums.wav", "vocals.mp3", ".hidden.wav", "notes.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert set(ls.stem_files(tmp_path)) == {"drums", "vocals"}
