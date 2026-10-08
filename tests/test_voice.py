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


def test_unsure_words_are_dropped():
    assert voice.match("pick stop", [1.0, 0.5]) is None
    assert voice.match("pick stop", [1.0, 0.95]) == "pause"


def test_every_command_is_listed():
    assert all(r["say"].startswith(voice.WAKE + " ") for r in voice.help_rows())
    assert set(voice.PHRASES.values()) == {c[0] for c in voice.COMMANDS}


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
    for cid, phrases, _, _ in voice.COMMANDS:
        for ph in phrases:
            for who in ("Samantha", "Daniel"):
                got = _listen(_speak(tmp_path, f"pick {ph}", who))
                assert [r["command"] for r in got] == [cid], (ph, who, got)


@needs_speech
def test_ordinary_talk_does_nothing(tmp_path):
    for talk in ["that was pretty good let's try it one more time", "where did i put my pick",
                 "pick it up from the second verse", "i think the next part is faster",
                 "okay play it again please", "stop", "record"]:
        got = _listen(_speak(tmp_path, talk))
        assert not [r for r in got if r["command"]], (talk, got)


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
