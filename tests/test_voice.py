"""Voice commands: an utterance is a command only as the wake word plus one known phrase."""
import shutil
import subprocess

import pytest

import voice


def test_wake_word_and_phrase():
    assert voice.match("pick stop") == "pause"
    assert voice.match("pick from the start") == "rewind"
    assert voice.match("pick seventy percent") == "tempo:70"


def test_needs_the_wake_word():
    assert voice.match("stop") is None
    assert voice.match("record") is None


def test_talk_is_not_a_command():
    assert voice.match("[unk] pick") is None
    assert voice.match("pick stop from the [unk]") is None
    assert voice.match("pick stop pick again") is None
    assert voice.match("pick") is None
    assert voice.match("pick a") is None
    assert voice.match("") is None


def test_little_words_do_not_matter():
    assert voice.match("pick a song") == voice.match("pick song") == "song"
    assert voice.match("pick playlist") == "playlist"
    assert voice.match("pick from start") == "rewind"
    assert voice.match("pick a song", [1.0, 0.4, 0.9]) == "song"       # an unsure "a" is fine


def test_a_doubled_wake_word_is_one():
    assert voice.match("pick pick stop") == "pause"
    assert voice.match("pick pick song [unk]", [0.4, 1.0, 1.0, 0.2]) == "find:song"
    assert voice.match("pick pick") is None


def test_unsure_words_are_dropped():
    assert voice.match("pick stop", [1.0, 0.5]) is None
    assert voice.match("pick stop", [1.0, 0.95]) == "pause"


def test_every_command_is_listed():
    assert all(r["say"].startswith(voice.WAKE + " ") for r in voice.help_rows())
    assert set(voice.PHRASES.values()) == {c[0] for c in voice.COMMANDS}


def test_tracks_sections_and_presets():
    assert voice.match("pick mute drums") == "mute:drums"
    assert voice.match("pick solo bass") == "solo:bass"
    assert voice.match("pick guitar louder") == "vol:guitar:+"
    assert voice.match("pick guitar left") == "pan:guitar:-1"
    assert voice.match("pick loop chorus") == "section:chorus"
    assert voice.match("pick next section") == "section:+"
    assert voice.match("pick next preset") == "preset:+"
    assert voice.match("pick my guitar") == "myguitar"


def test_a_name_after_the_command():
    assert voice.match("pick song [unk] [unk] [unk]") == "find:song"
    assert voice.match("pick band [unk]") == "find:band"
    assert voice.match("pick practice [unk] loop") == "find:practice"
    assert voice.match("pick song") == "song"                   # no name: any song
    assert voice.match("pick practice") == "practice"           # no name: the one playing
    assert voice.match("pick band") is None
    assert voice.match("pick song [unk]", [1.0, 0.5, 0.2]) is None       # the command itself unclear
    assert voice.match("pick song [unk]", [1.0, 0.9, 0.2]) == "find:song"


def test_the_name_is_what_follows_the_command():
    assert voice.spoken_name("Pick song, wait and bleed.") == "wait and bleed"
    assert voice.spoken_name("Pick Van Sepultura.") == "Sepultura"
    assert voice.spoken_name("Song down in a hole") == "down in a hole"
    assert voice.spoken_name("Pick.") == ""


def test_a_second_reading_of_an_unclear_utterance():
    assert voice.interpret("Pick song, wait and bleed.") == ("find", "song", "wait and bleed")
    assert voice.interpret("Pick Van Sepultura.") == ("find", "band", "Sepultura")
    assert voice.interpret("Pick practice Pisces") == ("find", "practice", "Pisces")
    assert voice.interpret("Pick, mute drums.") == ("command", "mute:drums")        # a command it missed
    assert voice.interpret("Pick it up from the second verse.") is None
    assert voice.interpret("") is None and voice.interpret("Pick.") is None
    mine = [{"id": "ab12", "name": "x", "phrase": "warm up", "steps": ["play"]}]
    assert voice.interpret("Pick warm up!", mine) == ("command", "custom:ab12")


def test_names_are_matched_by_sound():
    names = ["Wait and Bleed", "Sepultura", "Jinjer", "Kabát", "Alice In Chains", "Down In A Hole"]
    pick = lambda q: (lambda i: names[i] if i is not None else None)(voice.best_match(q, names)[0])
    assert pick("wait and bleed") == "Wait and Bleed"
    assert pick("Cabot") == "Kabát"                    # what whisper makes of a Czech name
    assert pick("alice in chains") == "Alice In Chains"
    assert pick("chains alice") == "Alice In Chains"   # word order does not matter
    assert pick("yesterday") is None
    assert pick("") is None


def test_steps_can_press_a_button_or_open_a_name():
    assert voice.valid_step("click:#mixer-export")
    assert voice.valid_step("find:band:Sepultura") and voice.valid_step("find:practice:Wait and Bleed")
    assert not voice.valid_step("click:") and not voice.valid_step("find:song:") and not voice.valid_step("find:album:x")


KNOWS_ALL = lambda words: []          # stands in for the model's vocabulary


def test_own_command_runs_its_steps_in_order(tmp_path):
    f = tmp_path / "c.json"
    row = voice.add_custom("Rozcvička", "pick warm up", ["tempo:65", "loop", "wait:2", "play"], f, KNOWS_ALL)
    assert row["phrase"] == "warm up" and row["steps"] == ["tempo:65", "loop", "wait:2", "play"]
    custom = voice.load_custom(f)
    assert voice.match("pick warm up", None, custom) == "custom:" + row["id"]
    assert voice.match("pick warm", None, custom) is None


def test_own_command_is_checked(tmp_path):
    f = tmp_path / "c.json"
    for phrase, steps in [("", ["play"]), ("pick", ["play"]), ("pick stop", ["play"]),          # empty, taken
                          ("solo time", []), ("solo time", ["fly"]), ("solo time", ["tempo:900"]),
                          ("solo time", ["wait:99"]), ("one two three four five six seven", ["play"])]:
        with pytest.raises(ValueError):
            voice.add_custom("x", phrase, steps, f, KNOWS_ALL)
    with pytest.raises(ValueError, match="xyzzy"):
        voice.add_custom("x", "xyzzy time", ["play"], f, lambda words: [w for w in words if w == "xyzzy"])
    assert voice.load_custom(f) == []


def test_own_command_cannot_be_saved_twice_and_can_be_deleted(tmp_path):
    f = tmp_path / "c.json"
    row = voice.add_custom("", "solo time", ["play"], f, KNOWS_ALL)
    assert row["name"] == "solo time"
    with pytest.raises(ValueError):
        voice.add_custom("", "the solo time", ["stop"], f, KNOWS_ALL)      # same but for a little word
    assert voice.delete_custom(row["id"], f) and not voice.delete_custom(row["id"], f)
    assert voice.load_custom(f) == []


def test_a_damaged_file_is_no_commands(tmp_path):
    f = tmp_path / "c.json"
    f.write_text("{not json")
    assert voice.load_custom(f) == []


needs_speech = pytest.mark.skipif(
    voice.available() is not None or not shutil.which("say") or not shutil.which("ffmpeg")
    or "Samantha" not in subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout,
    reason="needs the speech model, macOS `say` with an English voice, and ffmpeg")


def _speak(tmp_path, text, voice_name="Samantha"):
    aiff = tmp_path / "t.aiff"
    subprocess.run(["say", "-v", voice_name, "-o", str(aiff), text], check=True)
    return subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(aiff), "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
                          capture_output=True, check=True).stdout


def _listen(pcm):
    lis, out = voice.Listener(), []
    pcm = b"\0" * 8000 + pcm + b"\0" * 48000          # silence around it, as when spoken
    for i in range(0, len(pcm), 8000):                 # a quarter of a second at a time
        r = lis.feed(pcm[i:i + 8000])
        if r:
            out.append(r)
    return out


@needs_speech
def test_spoken_commands_are_heard(tmp_path):
    # every phrase in one voice, every fourth command in a second one (it is slow)
    for n, (cid, phrases, _, _) in enumerate(voice.COMMANDS):
        for ph in phrases:
            for who in ("Samantha", "Daniel")[:2 if n % 4 == 0 else 1]:
                got = _listen(_speak(tmp_path, f"pick {ph}", who))
                assert [r["command"] for r in got] == [cid], (ph, who, got)


@needs_speech
def test_ordinary_talk_does_nothing(tmp_path):
    for talk in ["that was pretty good let's try it one more time", "where did i put my pick",
                 "pick it up from the second verse", "i think the next part is faster",
                 "okay play it again please", "stop", "record"]:
        got = _listen(_speak(tmp_path, talk))
        # "ask" is no command yet: a sentence that began with a clear "pick" goes
        # to whisper for a second reading, and that reading is no command either.
        assert not [r for r in got if r["command"] and r["command"] != "ask"], (talk, got)
        if any(r["command"] == "ask" for r in got):
            assert voice.interpret(talk) is None


@needs_speech
def test_own_spoken_command_is_heard(tmp_path):
    f = tmp_path / "c.json"
    assert voice.unknown_words(["warm", "up", "xyzzy"]) == ["xyzzy"]
    row = voice.add_custom("Rozcvička", "warm up", ["tempo:65", "play"], f)
    lis, out = voice.Listener(voice.load_custom(f)), []
    pcm = b"\0" * 8000 + _speak(tmp_path, "pick warm up") + b"\0" * 48000
    for i in range(0, len(pcm), 8000):
        r = lis.feed(pcm[i:i + 8000])
        if r:
            out.append(r)
    assert [(r["command"], r["steps"]) for r in out] == [("custom:" + row["id"], ["tempo:65", "play"])]


needs_names = pytest.mark.skipif(voice.names_available() is not None, reason="needs whisper-cli and its model")


@needs_speech
@needs_names
def test_a_spoken_name_reaches_the_library(tmp_path):
    lis, out = voice.Listener([]), []
    pcm = b"\0" * 16000 + _speak(tmp_path, "pick song wait and bleed") + b"\0" * 48000
    for i in range(0, len(pcm), 8000):
        r = lis.feed(pcm[i:i + 8000])
        if r:
            out.append(r)
    assert len(out) == 1 and out[0]["command"] in ("find:song", "ask") and out[0]["pcm"], out
    what = voice.interpret(voice.transcribe(out[0]["pcm"]))
    assert what[:2] == ("find", "song"), what
    assert voice.best_match(what[2], ["Duality", "Wait and Bleed", "Psychosocial"])[0] == 1, what


def test_listening_is_switched_from_outside_only_when_pick_is_open(client):
    import server
    server._REMOTE_STATE["at"] = 0.0
    assert client.get("/api/voice/remote?on=1").status_code == 409
    client.post("/api/remote/state", json={"playing": False})
    seq = client.get("/api/remote/commands").json()["seq"]
    assert client.get("/api/voice/remote?on=1").status_code == 200
    assert client.get("/api/voice/remote?on=0").status_code == 200
    assert client.get("/api/voice/remote").status_code == 200
    got = client.get(f"/api/remote/commands?since={seq}").json()["commands"]
    assert got == [{"action": "voice", "value": 1.0}, {"action": "voice", "value": 0.0}, {"action": "voice", "value": None}]


# ---- taught by voice: what the recogniser hears from this user counts as the command

def test_what_was_heard_becomes_the_command(tmp_path):
    p = tmp_path / "t.json"
    r = voice.teach("slower", "pixel better", path=p, custom=[])
    assert r["added"] and r["takes"] == 1
    idx = voice.taught_index(voice.load_taught(p))
    assert voice.match("pixel better", None, [], idx) == "slower"
    assert voice.match("pixel better", [0.9, 0.5], [], idx) is None          # still has to be heard clearly
    assert voice.match("pixel", None, [], idx) is None
    assert voice.match("pick slower", None, [], idx) == "slower"              # the plain way stays
    assert voice.teach("slower", "pixel better", path=p, custom=[])["added"] is False   # nothing new


def test_a_take_that_is_another_command_is_refused(tmp_path):
    p = tmp_path / "t.json"
    with pytest.raises(ValueError):
        voice.teach("slower", "pick faster", path=p, custom=[])
    voice.teach("slower", "pixel better", path=p, custom=[])
    with pytest.raises(ValueError):
        voice.teach("faster", "pixel better", path=p, custom=[])             # already means "slower"
    for useless in ("", "pick", "the pick", "one two three four five six seven eight nine"):
        with pytest.raises(ValueError):
            voice.teach("slower", useless, path=p, custom=[])
    with pytest.raises(ValueError):
        voice.teach("no-such-command", "pixel better", path=p, custom=[])


def test_understood_already_teaches_nothing(tmp_path):
    p = tmp_path / "t.json"
    assert voice.teach("slower", "pick slower", path=p, custom=[])["added"] is False
    assert voice.load_taught(p) == {}


def test_own_commands_are_taught_and_forgotten_with_them(tmp_path):
    c, t, d = tmp_path / "c.json", tmp_path / "t.json", tmp_path / "takes"
    row = voice.add_custom("Rozcvička", "warm up", ["play"], c, known=lambda w: [])
    cid = "custom:" + row["id"]
    voice.teach(cid, "big worm app", path=t, custom=voice.load_custom(c))
    voice.save_take(cid, b"\0\0", d)
    assert voice.match("big worm app", None, voice.load_custom(c), voice.taught_index(voice.load_taught(t), voice.load_custom(c))) == cid
    # a command that is gone leaves nothing behind that could still fire
    assert voice.taught_index(voice.load_taught(t), []) == {}
    voice.forget(cid, t, d)
    assert voice.load_taught(t) == {} and not list(d.glob("*"))


def test_whisper_reading_of_my_voice_counts_too(tmp_path):
    p = tmp_path / "t.json"
    voice.teach("slower", "pixel better", "Pick's lower.", path=p, custom=[])
    taught = voice.load_taught(p)
    assert taught["slower"]["whisper"]
    assert voice.interpret("Picks lower", [], None, taught) == ("command", "slower")
    assert voice.interpret("Pick something else entirely", [], None, taught) is None


def test_only_the_newest_takes_are_kept(tmp_path):
    for i in range(voice.MAX_TAUGHT + 3):
        voice.save_take("slower", bytes([i, 0]), tmp_path)
    kept = sorted(tmp_path.glob("*.pcm"))
    assert len(kept) == voice.MAX_TAUGHT and kept[-1].read_bytes() == bytes([voice.MAX_TAUGHT + 2, 0])


needs_accent = pytest.mark.skipif(
    not shutil.which("say") or "Zuzana" not in subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout,
    reason="needs the Czech macOS voice, for English said with an accent")


@needs_speech
@needs_accent
def test_an_accent_is_learned_from_a_take(tmp_path, client):
    said = _speak(tmp_path, "pick slower", "Zuzana")
    assert voice.Listener().command(said) is None                           # not understood as it is
    r = client.post("/api/voice/teach?cmd=slower", content=said).json()
    assert r["added"] and r["works"]
    assert voice.Listener().command(said) == "slower"
    assert client.get("/api/voice/commands").json()["taught"]["slower"] >= 1
    # and a command that was fine is still fine
    assert voice.Listener().command(_speak(tmp_path, "pick stop")) == "pause"
    # a second take that is understood by now is only kept
    assert client.post("/api/voice/teach?cmd=slower", content=said).json()["understood"]
    client.delete("/api/voice/teach?cmd=slower")
    assert voice.Listener().command(said) is None
    assert not list(voice.TAKES_DIR.glob("*"))
