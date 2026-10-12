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


def test_what_was_heard_of_a_line_is_kept_with_the_song(tmp_path):
    assert sing.sung_words(tmp_path) == {}
    sing.keep_sung(tmp_path, "0|Hello world", {"heard": "hello word", "ok": 1, "total": 2})
    sing.keep_sung(tmp_path, "1|Again", {"heard": "again", "ok": 1, "total": 1})
    sing.keep_sung(tmp_path, "0|Hello world", {"heard": "hello world", "ok": 2, "total": 2})     # sung again: the newer one
    got = sing.sung_words(tmp_path)
    assert list(got) == ["1|Again", "0|Hello world"] and got["0|Hello world"]["ok"] == 2
    for i in range(10):
        sing.keep_sung(tmp_path, f"k{i}", {"heard": ""}, keep=4)
    assert list(sing.sung_words(tmp_path)) == ["k6", "k7", "k8", "k9"]


def test_lines_are_read_by_the_cloned_voice_once_and_kept(client, monkeypatch):
    import time
    import server
    made = []
    monkeypatch.setattr(server, "_voicebox_say", lambda profile, lang, text, style="": made.append(text) or b"RIFF" + text.encode())
    monkeypatch.setattr(server, "_voicebox_open", lambda: None)
    monkeypatch.setattr(server, "_TTS_QUEUE", [])
    ask = lambda **kw: client.post("/api/tts/prepare", json={"lines": ["One", "Two  two", "Three"], "lang": "en", "profile": "p1", **kw}).json()
    got = ask(first="Three")
    assert got["ready"] == {} and got["mine"] == 3
    for _ in range(200):
        got = ask()
        if len(got["ready"]) == 3:
            break
        time.sleep(0.02)
    assert set(got["ready"]) == {"One", "Two two", "Three"} and got["waiting"] == 0
    assert made[0] == "Three" and sorted(made) == ["One", "Three", "Two two"]          # the wanted line first; each made once
    assert client.get(got["ready"]["One"]).content == b"RIFFOne"
    assert client.get("/api/tts/audio/00ff").status_code == 404
    # another voice is another reading; Czech is not a language this voice speaks
    assert ask(profile="p2")["ready"] == {}
    assert client.post("/api/tts/prepare", json={"lines": ["Ahoj"], "lang": "cs", "profile": "p1"}).json()["unsupported"] is True


def test_mispronounced_words_are_kept_until_learnt(client):
    assert client.get("/api/speak/words").json() == []
    client.post("/api/speak/words", json={"word": "Through,", "lang": "en", "ok": False, "heard": "true", "song": "A - B"})
    client.post("/api/speak/words", json={"word": "fine", "lang": "en", "ok": True})                 # never missed: not kept
    rows = client.post("/api/speak/words", json={"word": "through", "lang": "en", "ok": False, "heard": "threw"}).json()
    assert len(rows) == 1 and rows[0]["word"] == "through" and rows[0]["miss"] == 2 and rows[0]["heard"] == "threw" and rows[0]["song"] == "A - B"
    rows = client.post("/api/speak/words", json={"word": "through", "ok": True}).json()               # right once, inside its line
    assert rows[0]["ok"] == 1 and rows[0]["done"] is False
    client.post("/api/speak/words", json={"word": "world", "ok": False})
    rows = client.post("/api/speak/words", json={"word": "through", "ok": True, "learned": True}).json()
    assert [(r["word"], r["done"]) for r in rows] == [("world", False), ("through", True)]
    rows = client.post("/api/speak/words", json={"word": "through", "ok": False}).json()              # wrong again: back on the list
    assert all(not r["done"] for r in rows)
    client.post("/api/speak/words", json={"word": "world", "ok": True, "learned": True})
    assert [r["word"] for r in client.delete("/api/speak/words?done=true").json()] == ["through"]
    assert client.delete("/api/speak/words?word=Through").json() == []


def test_a_line_the_voice_got_wrong_is_read_once_more(client, monkeypatch):
    import time
    import server
    made = []
    monkeypatch.setattr(server, "_voicebox_say", lambda profile, lang, text, style="": made.append(text) or b"RIFF%d" % len(made))
    monkeypatch.setattr(server, "_tts_sounds_right", lambda audio, lang, text: audio != b"RIFF1")
    monkeypatch.setattr(server, "_voicebox_open", lambda: None)
    monkeypatch.setattr(server, "_TTS_QUEUE", [])
    ask = lambda: client.post("/api/tts/prepare", json={"lines": ["One"], "lang": "en", "profile": "p1"}).json()
    for _ in range(200):
        got = ask()
        if got["ready"]:
            break
        time.sleep(0.02)
    assert made == ["One", "One"]
    assert client.get(got["ready"]["One"]).content == b"RIFF2"
    assert server._tts_sounds_right(b"not audio at all", "en", "One") is True


def test_pronunciation_is_judged_strictly_a_word_that_only_looks_alike_does_not_pass():
    c = lambda want, heard: [w["s"] for w in sing.compare(want, heard, strict=True)["words"]]
    assert c("I believe in our hate", "I believe in our fate") == ["ok", "ok", "ok", "ok", "bad"]
    assert c("we planned it", "we plan it") == ["ok", "bad", "ok"]              # a dropped ending is a slip when spoken
    assert c("through the night", "threw the knight") == ["ok", "ok", "ok"]     # sounds the same: not a slip
    assert c("runnin' to you", "running too you") == ["ok", "ok", "ok"]
    assert c("three trees", "tree trees") == ["bad", "ok"]
    assert c("think about it", "sink about it") == ["bad", "ok", "ok"]
    assert c("icebox", "ice box") == ["ok"]
    # sung, the same takes are forgiven as before
    assert [w["s"] for w in sing.compare("we planned it", "we plan it")["words"]] == ["ok", "ok", "ok"]


def test_picks_own_voices_come_first_and_speak_any_language(client, tmp_path, monkeypatch):
    import server
    py = tmp_path / "python"; py.write_text("")
    monkeypatch.setattr(server, "OWN_TTS_PYTHON", py)
    v = tmp_path / "voices" / "me"; v.mkdir(parents=True)
    (v / "ref.wav").write_bytes(b"RIFF"); (v / "ref.txt").write_text("ahoj"); (v / "name.txt").write_text("Já")
    monkeypatch.setattr(server, "_own_voices_dir", lambda: tmp_path / "voices")
    monkeypatch.setattr(server, "_voicebox_only", lambda: [{"id": "me", "name": "old", "kind": "cloned"}, {"id": "other", "name": "Other", "kind": "cloned"}])
    monkeypatch.setattr(server, "_TTS_QUEUE", [])
    said = []
    monkeypatch.setattr(server, "_own_say", lambda voice, lang, text, style="": said.append((voice["name"], lang, text)) or _wav())
    monkeypatch.setattr(server, "_tts_sounds_right", lambda *a: True)
    got = client.get("/api/tts/voices").json()
    assert [(p["id"], p["name"], p.get("engine")) for p in got["profiles"]] == [("me", "Já", "pick"), ("other", "Other", None)]
    assert got["own"] and not got["installed"]
    # Czech: Voicebox cannot, Pick's own voice can
    assert client.post("/api/tts/prepare", json={"lines": ["Ahoj světe"], "lang": "cs", "profile": "other"}).json()["unsupported"]
    assert "unsupported" not in client.post("/api/tts/prepare", json={"lines": ["Ahoj světe"], "lang": "cs", "profile": "me"}).json()
    for _ in range(200):
        if said:
            break
        import time; time.sleep(0.02)
    assert said == [("Já", "cs", "Ahoj světe")]


def _wav():
    import io, wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * 2400)
    return b.getvalue()


def test_a_new_voice_is_made_of_a_recording_and_can_be_put_right_or_removed(client, tmp_path, monkeypatch):
    import server
    py = tmp_path / "python"; py.write_text("")
    monkeypatch.setattr(server, "OWN_TTS_PYTHON", py)
    monkeypatch.setattr(server, "_own_voices_dir", lambda: tmp_path / "voices")
    monkeypatch.setattr(server, "_voicebox_only", lambda: None)

    def make(src, dest, lang="auto"):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "ref.wav").write_bytes(b"RIFF" + b"\0" * 30000)
        (dest / "ref.txt").write_text("hello there")
        return "hello there"
    monkeypatch.setattr(server, "_voice_ref_make", make)
    got = client.post("/api/tts/voice?name=Bro&lang=en", files={"file": ("me.m4a", b"x" * 9000, "audio/mp4")}).json()
    assert got["name"] == "Bro" and got["ref_text"] == "hello there"
    assert [p["name"] for p in client.get("/api/tts/voices").json()["profiles"]] == ["Bro"]
    v = client.put(f"/api/tts/voice/{got['id']}", json={"ref_text": "Hello,   there!", "name": "Brother"}).json()
    assert (v["name"], v["ref_text"]) == ("Brother", "Hello, there!")
    assert client.post("/api/tts/voice", files={"file": ("a.wav", b"x", "audio/wav")}).status_code == 400
    assert client.delete(f"/api/tts/voice/{got['id']}").json() == {"ok": True}
    assert client.get("/api/tts/voices").json()["profiles"] == []
    assert client.delete("/api/tts/voice/nope").status_code == 404


def test_a_line_is_said_in_the_manner_asked_for_and_as_loud_as_any_other(client, tmp_path, monkeypatch):
    import io, wave
    import numpy as np
    import server
    names = [s["name"] for s in client.get("/api/tts/voices").json()["styles"]]
    assert names[0] == "Přirozený" and {"Metalový", "Romantický", "Naštvaný", "Agresivní", "Klidný"} <= set(names)
    # each manner is a line of its own on disk; one that is not known is the plain one
    plain = server._tts_file("p1", "en", "One")
    assert server._tts_file("p1", "en", "One", "metal") != plain
    assert server._tts_file("p1", "en", "One", "nonsense") == plain
    # the manner reaches the voice
    asked = []
    monkeypatch.setattr(server, "_voicebox_say", lambda profile, lang, text, style="": asked.append(style) or b"RIFF1")
    monkeypatch.setattr(server, "_voicebox_profiles", lambda: [{"id": "p1", "name": "Me", "kind": "cloned"}])
    monkeypatch.setattr(server, "_tts_sounds_right", lambda *a: True)
    monkeypatch.setattr(server, "_TTS_QUEUE", [])
    for _ in range(200):
        if client.post("/api/tts/prepare", json={"lines": ["One"], "lang": "en", "profile": "p1", "style": "angry"}).json()["ready"]:
            break
        import time; time.sleep(0.02)
    assert asked == ["angry"]

    # a quiet line comes out loud, a loud one no louder; what is no sound is left alone
    def wav(amp):
        t = np.arange(24000) / 24000
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes((np.sin(2 * np.pi * 220 * t) * amp * 32767).astype(np.int16).tobytes())
        return out.getvalue()

    def rms(b):
        with wave.open(io.BytesIO(b)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768
        return float(np.sqrt(np.mean(x ** 2)))
    assert rms(wav(0.01)) < 0.01
    for amp in (0.01, 0.9):
        assert abs(rms(server._tts_finish(wav(amp))) - server.TTS_LOUD) < 0.02
    assert abs(rms(server._tts_finish(wav(0.01), "metal")) - server.TTS_LOUD) < 0.03
    assert server._tts_finish(b"RIFF1", "metal") == b"RIFF1"
