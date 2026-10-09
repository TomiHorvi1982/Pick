"""The singing trainer: the tune read off a voice, the words of a sung line, the history of takes."""
import shutil
import wave

import numpy as np
import pytest

import sing


def _tone(freqs, each=0.6, level=0.3):
    t = np.arange(int(sing.RATE * each)) / sing.RATE
    return np.concatenate([np.sin(2 * np.pi * f * t) * level if f else np.zeros(len(t)) for f in freqs]).astype(np.float32)


def test_the_notes_of_a_tune_are_found():
    midi, loud = sing.pitch_track(_tone([220.0, 261.63, 0, 329.63, 0]))
    notes = sing.notes_from(midi, loud)
    assert [round(n[2]) for n in notes] == [57, 60, 64]
    assert all(abs(n[2] - round(n[2])) < 0.1 for n in notes)            # within ten cents
    assert abs(notes[0][0] - 0.0) < 0.06 and abs(notes[2][0] - 1.8) < 0.06   # where each begins
    assert all(0.4 < n[1] - n[0] < 0.7 for n in notes)


def test_silence_and_noise_are_no_notes():
    rng = np.random.default_rng(1)
    for x in (np.zeros(sing.RATE * 2, dtype=np.float32), (rng.standard_normal(sing.RATE * 2) * 0.2).astype(np.float32)):
        midi, loud = sing.pitch_track(x)
        assert sing.notes_from(midi, loud) == []


def test_a_low_and_a_high_voice_are_both_read():
    for f, want in ((82.41, 40), (880.0, 81)):
        midi, loud = sing.pitch_track(_tone([f]))
        notes = sing.notes_from(midi, loud)
        assert len(notes) == 1 and round(notes[0][2]) == want, (f, notes)


def test_a_quiet_bleed_under_the_singing_is_dropped():
    x = np.concatenate([_tone([440.0]), _tone([330.0], level=0.004)])      # far quieter than the voice
    midi, loud = sing.pitch_track(x)
    assert [round(n[2]) for n in sing.notes_from(midi, loud)] == [69]


def test_words_right_wrong_and_left_out():
    r = sing.compare("I can't stand it, I know you planned it", "i can't stand it i know you plan it")
    assert [w["s"] for w in r["words"]] == ["ok"] * 9 and r["ok"] == r["total"] == 9
    r = sing.compare("Somebody's Heine is crowding my icebox", "somebody's hiney is crowdin my ice box")
    assert r["ok"] == 6
    r = sing.compare("Flip on the telly, wrestle with Jimmy", "flip on the telly")
    assert [w["s"] for w in r["words"]] == ["ok"] * 4 + ["miss"] * 3
    r = sing.compare("Say it ain't so", "say it is no")
    assert [w["s"] for w in r["words"]] == ["ok", "ok", "bad", "bad"]
    assert sing.compare("Say it ain't so", "")["ok"] == 0


def test_what_is_in_brackets_is_not_yours_to_sing():
    assert sing.words("Oh yeah (alright) [chorus] feels good") == ["Oh", "yeah", "feels", "good"]
    assert sing.compare("Feels good (oh yeah)", "feels good")["total"] == 2


def test_czech_words_compare_without_their_accents():
    assert sing.compare("Žlutý kůň úpěl", "zluty kun upel")["ok"] == 3


def test_takes_are_kept_in_order(tmp_path):
    assert sing.history(tmp_path) == []
    sing.add_history(tmp_path, {"pitch": 40})
    rows = sing.add_history(tmp_path, {"pitch": 55})
    assert [r["pitch"] for r in rows] == [40, 55] == [r["pitch"] for r in sing.history(tmp_path)]
    for i in range(10):
        sing.add_history(tmp_path, {"pitch": i}, keep=5)
    assert len(sing.history(tmp_path)) == 5


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _song(root, name="Zkouska"):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    with wave.open(str(d / "vocals.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sing.RATE)
        w.writeframes((_tone([220.0, 0, 261.63]) * 32767).astype("<i2").tobytes())
    return d


@needs_ffmpeg
def test_a_songs_melody_is_computed_once_and_kept(client, tmp_music_root):
    d = _song(tmp_music_root)
    assert client.get("/api/stems/Zkouska/melody/kept").json() == {"notes": []}
    assert not (d / "melody.json").exists()                  # asking for the kept one computes nothing
    r = client.get("/api/stems/Zkouska/melody").json()
    assert client.get("/api/stems/Zkouska/melody/kept").json()["notes"] == r["notes"]
    assert client.get("/api/stems/Neni/melody/kept").json() == {"notes": []}
    assert [round(n[2]) for n in r["notes"]] == [57, 60]
    assert (d / "melody.json").exists()
    (d / "melody.json").write_text((d / "melody.json").read_text().replace("57.0", "58.0"))
    assert round(client.get("/api/stems/Zkouska/melody").json()["notes"][0][2]) == 58        # served from the file
    assert round(client.get("/api/stems/Zkouska/melody?refresh=true").json()["notes"][0][2]) == 57
    assert client.get("/api/stems/Neni/melody").status_code == 404


def test_a_song_without_vocals_says_so(client, tmp_music_root):
    (tmp_music_root / "Stems" / "Bez").mkdir(parents=True)
    assert client.get("/api/stems/Bez/melody").status_code == 422


def test_takes_are_saved_with_the_song(client, tmp_music_root):
    (tmp_music_root / "Stems" / "Zkouska").mkdir(parents=True)
    assert client.get("/api/stems/Zkouska/sing").json() == []
    rows = client.post("/api/stems/Zkouska/sing", json={"pitch": 61.5, "near": 12, "text": 80, "octave": -1, "lines": [{"t": 1.0, "p": 50, "w": None}]}).json()
    assert len(rows) == 1 and rows[0]["pitch"] == 61.5 and rows[0]["octave"] == -1 and rows[0]["at"] > 0
    assert client.get("/api/stems/Zkouska/sing").json()[0]["lines"][0]["p"] == 50
    assert client.post("/api/stems/Neni/sing", json={"pitch": 1}).status_code == 404


def test_a_sung_line_must_be_real_audio(client):
    assert client.post("/api/sing/line?text=hello", content=b"\0" * 10).status_code in (400, 503)
