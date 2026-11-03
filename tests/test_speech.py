"""Words said, on this computer (speech.py): which of the names was meant."""
import speech


def test_the_name_meant_is_found_whatever_the_spelling():
    assert speech.best_match("metalika", ["Kabát", "Metallica"])[0] == 1
    assert speech.best_match("seek and destroy", ["Destroy and Seek", "Fade to Black"])[0] == 0   # the same words, any order
    assert speech.best_match("xyz", ["Kabát", "Metallica"])[0] is None
    assert speech.best_match("a", ["A"]) == (None, 0.0)                                           # too little to go on


def test_the_best_name_is_asked_for_over_the_api(client):
    got = client.post("/api/voice/best", json={"query": "kabat", "names": ["Metallica", "Kabát"]}).json()
    assert got["index"] == 1
