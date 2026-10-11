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
                # With this many words to choose from, a few phrases are heard unsure or a
                # word off ("drums up" as "drums off"); those are not acted on as heard —
                # they go to whisper for a second reading ("ask"), slower, and that
                # reading has to end at the same command.
                def settled(r):
                    if r["command"] != "ask":
                        return r["command"]
                    if voice.names_available() is not None:
                        return cid                      # no whisper here to give the second reading
                    what = voice.interpret(voice.transcribe(r["pcm"]), [], None)
                    return what[1] if what and what[0] == "command" else None
                assert [settled(r) for r in got] == [cid], (ph, who, got)


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
    out = [r for r in out if r["command"]]          # the tail of the name, heard on its own, is no command
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


def test_volume_steps_and_naming_what_plays():
    assert voice.match("pick up") == "up:1" and voice.match("pick volume up") == "up:1"
    assert voice.match("pick up up") == "up:2" and voice.match("pick down") == "down:1"
    assert voice.match("pick up up up") is None            # three in a row is not heard reliably
    assert voice.match("pick guitar up") == "vol:guitar:+"                 # a track's own, as before
    assert voice.match("pick stop mix") == "mixpause" and voice.match("pick play player") == "pbplay"
    assert voice.match("pick full screen") == "full" and voice.match("pick mini full screen") == "minifull"
    assert voice.match("pick mini player") == "mini"


def test_the_app_says_whether_it_listens(client):
    client.post("/api/remote/state", json={"playing": False, "voice": True})
    assert client.get("/api/remote/state").json()["voice"] is True


# ---- one window listens at a time; the mini player comes first

def test_the_mini_player_takes_the_listening_and_gives_it_back(client):
    import server
    silence = b"\0" * 8000
    assert client.post("/api/voice/start").status_code == 200
    assert client.post("/api/voice/start?who=mini").status_code == 200
    assert client.post("/api/voice/start").json().get("taken") == "mini"          # the app has to wait
    assert client.post("/api/voice/feed?who=app", content=silence).json().get("taken") == "mini"
    assert client.post("/api/voice/feed?who=mini", content=silence).status_code == 200
    client.post("/api/voice/stop")                                                 # the app's "stop" is not the mini player's
    assert client.post("/api/voice/feed?who=mini", content=silence).status_code == 200
    client.post("/api/voice/stop?who=mini")
    assert client.post("/api/voice/start").status_code == 200
    # a mini player that went away without saying so: the app gets in after a few quiet seconds
    client.post("/api/voice/start?who=mini")
    server._voice_owner["at"] -= 10
    assert client.post("/api/voice/start").status_code == 200
    client.post("/api/voice/stop")


def test_list_commands_and_the_playlist_search():
    assert voice.match("pick next page") == "page:+" and voice.match("pick previous page") == "page:-"
    assert voice.match("pick play all") == "playall" and voice.match("pick back") == "back"
    assert voice.match("pick next song") == "next" and voice.match("pick previous song") == "prev"
    assert voice.match("pick playlist [unk] [unk]") == "find:playlist"
    assert voice.match("pick playlist") == "playlist"                               # bare: the playlists themselves
    assert voice.interpret("Pick playlist, nineties rock") == ("find", "playlist", "nineties rock")


def test_a_title_said_aloud_picks_from_the_list(client):
    names = ["Dead Memories", "Sulfur", "Wait and Bleed", "Duality"]
    assert client.post("/api/voice/best", json={"query": "weight and bleed", "names": names}).json()["index"] == 2
    assert client.post("/api/voice/best", json={"query": "xyzzy plugh", "names": names}).json()["index"] is None
    assert client.post("/api/voice/best", json={"query": "anything", "names": []}).json()["index"] is None


def test_guitar_on_off_and_the_hand_over_to_the_mini_player(client, tmp_music_root):
    assert voice.match("pick guitar on") == "gtr:on" and voice.match("pick guitar off") == "gtr:off"
    assert voice.match("pick play in mini") == "tomini" and voice.match("pick mini player") == "mini"
    assert voice.match("pick tabs") == "tabs" and voice.match("pick lyrics") == "lyrics" and voice.match("pick mixer") == "steems"
    assert voice.match("pick guitar louder") == "vol:guitar:+"                      # a track's own, as before
    # a song handed over is in the state the mini player polls, for a while
    (tmp_music_root / "Stems" / "Zkouska").mkdir(parents=True)
    assert client.post("/api/mini/play", json={"stems": "Neni"}).status_code == 404
    assert "mini" not in client.get("/api/remote/state").json() or client.get("/api/remote/state").json()["mini"].get("stems") != "Zkouska"
    assert client.post("/api/mini/play", json={"stems": "Zkouska", "title": "Zkouška", "artists": "Já"}).status_code == 200
    got = client.get("/api/remote/state").json()["mini"]
    assert got["stems"] == "Zkouska" and got["title"] == "Zkouška" and got["seq"] > 0


# ---- the prompter: "pick" by itself, then a word or a command without it

@needs_speech
def test_pick_alone_asks_for_the_list_and_the_list_takes_bare_words(tmp_path):
    def hear(text, menu):
        lis, out = voice.Listener(custom=[], taught={}), []
        pcm = b"\0" * 16000 + _speak(tmp_path, text) + b"\0" * 48000
        for i in range(0, len(pcm), 8000):
            r = lis.feed(pcm[i:i + 8000], menu)
            if r:
                out.append({k: v for k, v in r.items() if k != "pcm"})
        return out
    assert [r["command"] for r in hear("pick help", False)] == ["help"]
    assert [r["command"] for r in hear("pick", False)] == [None]             # the wake word alone is nothing
    assert hear("nothing", True)[0]["command"] == "nothing"
    assert hear("guitar", False)[0]["command"] is None and "filter" not in hear("guitar", False)[0]     # no list, no meaning
    assert hear("guitar", True)[0].get("filter") == "guitar"
    assert hear("loop", True)[0]["command"] == "loop"                    # a whole command needs no "pick" now
    assert hear("guitar louder", True)[0]["command"] == "vol:guitar:+"
    got = hear("pick stop", True)
    assert got[0]["command"] == "pause"                                  # …and with it still works
    # talk that is none of Pick's words: nothing at all, or a plain "again" — never a command
    for r in hear("what a lovely day it is today my friends", True) + hear("guitar banana telephone", True):
        assert r["command"] is None and "filter" not in r


# ---- listening from the menu bar: the server reads the microphone itself

class _FakeMic:
    """Stands in for ffmpeg: a recording instead of the microphone."""
    def __init__(self, pcm):
        import io
        self.stdout, self.stderr, self.dead = io.BytesIO(pcm), io.BytesIO(b""), False
    def poll(self):
        return 0 if self.dead else None
    def kill(self):
        self.dead = True


@needs_speech
def test_the_menu_bar_listening_opens_windows_and_passes_the_rest_on(tmp_path, monkeypatch):
    import threading
    import server
    opened = []
    monkeypatch.setattr(server, "mini_open", lambda: opened.append("mini") or {"ok": True})
    monkeypatch.setattr(server, "_widget_again", lambda: opened.append("again"))

    def hear(text, app_open=False, mini_open=False, window_listens=False):
        server._voice_listener = object() if window_listens else None
        server._voice_owner.update(who="app", at=server.time.time() if window_listens else 0.0)
        server._REMOTE_STATE["at"] = server.time.time() if app_open else 0.0
        server._MINI_SEEN["at"] = server.time.time() if mini_open else 0.0
        proc = _FakeMic(b"\0" * 16000 + _speak(tmp_path, text) + b"\0" * 64000)
        server._WIDGET.update(proc=proc, running=True)
        server._widget_loop(proc, threading.Event())
        server._voice_listener = None

    seq = server._WIDGET_OPEN["seq"]
    hear("pick open")
    assert server._WIDGET_OPEN["seq"] != seq and opened == []                # the launcher opens the app's window
    hear("pick mini player")
    assert opened == ["mini"]
    # anything else needs a window: the mini player is opened for it and handed the command
    opened.clear(); server._MINI_VOICE.update(seq=0, at=0.0)
    hear("pick next")
    assert opened == ["mini"] and server._MINI_VOICE["r"]["command"] == "next"
    # the app's window is there (just not listening): it gets the command, nothing is opened
    opened.clear(); n = server._REMOTE_SEQ
    hear("pick stop", app_open=True)
    assert opened == [] and server._REMOTE_COMMANDS[-1][1]["action"] == "heard" and server._REMOTE_SEQ == n + 1
    assert server.json.loads(server._REMOTE_COMMANDS[-1][1]["text"])["command"] == "pause"
    # a window that is listening itself comes first: the menu bar hears nothing
    opened.clear(); seq = server._WIDGET_OPEN["seq"]
    hear("pick open", window_listens=True)
    assert server._WIDGET_OPEN["seq"] == seq and opened == []
    # talk is nothing
    hear("that was pretty good let's try it one more time")
    assert opened == []
    assert server._WIDGET["running"] is False                                # the reader ended with its microphone


def test_the_menu_bar_listening_is_remembered(client, monkeypatch):
    import server
    started = []
    monkeypatch.setattr(server, "_widget_start", lambda: started.append(1) or True)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    assert client.get("/api/voice/widget").json()["on"] is False
    r = client.post("/api/voice/widget", json={"on": True, "device": "iRig Pro Duo (1963:0046)", "channel": "2"}).json()
    assert r["on"] is True and r["channel"] == "2" and started == [1]
    assert voice.widget_config() == {"on": True, "device": "iRig Pro Duo (1963:0046)", "channel": "2"}
    client.post("/api/voice/widget", json={"on": False})
    assert voice.widget_config()["on"] is False and voice.widget_config()["channel"] == "2"
    assert voice.match("pick open") == "open" and voice.match("pick open in mini") == "tomini"


def test_play_with_a_name_picks_a_song_or_a_playlist():
    # "play" alone and its own phrases stay what they were…
    assert voice.match("pick play") == "play"
    assert voice.match("pick play all") == "playall"
    assert voice.match("pick play mix") == "mixplay"
    assert voice.match("pick play in mini") == "tomini"
    # …a name after it is a song of the list that is on, "playlist" after the name a playlist.
    assert voice.match("pick play [unk] [unk]") == "find:play"
    assert voice.match("pick play [unk] playlist") == "find:open"
    assert voice.match("pick find playlist [unk]") == "find:playlist"
    assert voice.match("pick play [unk]", [0.9, 0.5, 0.2]) is None          # "play" itself was not clear
    assert voice.interpret("Pick play Wait and Bleed.", [], None) == ("find", "play", "Wait and Bleed")
    assert voice.interpret("Pick, play Metallica playlist", [], "play") == ("find", "open", "Metallica")
    assert voice.interpret("Pick find playlist heavy metal", [], "playlist") == ("find", "playlist", "heavy metal")
    assert voice.interpret("Pick play.", [], "play") == ("command", "play")
    assert voice.interpret("Wait and bleed", [], "play") == ("find", "play", "Wait and bleed")


def test_lists_are_switched_and_the_song_is_saved():
    assert voice.match("pick library") == "list:library"
    assert voice.match("pick stems list") == "list:steems"
    assert voice.match("pick internet") == "list:songs"
    assert voice.match("pick next list") == "list:+"
    assert voice.match("pick stems") == "steems"                              # the app's Steems, as before
    assert voice.match("pick save song") == "save"
    says = {r["say"] for r in voice.help_rows()}
    assert {"pick play <název>", "pick play <název> playlist", "pick find playlist <název>", "pick save song"} <= says


def test_a_number_picks_the_row_of_the_list():
    assert voice.match("pick play twenty five") == "num:25"
    assert voice.match("pick play number seven") == "num:7"
    assert voice.match("pick number three hundred and twelve") == "num:312"
    assert voice.match("pick play one hundred") == "num:100"
    assert voice.match("pick play nineteen") == "num:19"
    assert voice.match("pick play 25") == "num:25"
    # one unclear word and it is not taken for a number here
    assert voice.match("pick play twenty five", [0.9, 0.9, 0.9, 0.5]) == "find:play"      # whisper is asked
    # a name that only begins with a number stays a name; the commands stay commands
    assert voice.match("pick play seven [unk] [unk]") == "find:play"
    assert voice.match("pick play twenty twenty") == "find:play"
    assert voice.match("pick play zero") == "find:play"
    assert voice.match("pick play all") == "playall"
    assert voice.match("pick play") == "play"
    assert voice.interpret("Pick play 25.") == ("command", "num:25")
    assert voice.interpret("Pick, play twenty-five") == ("command", "num:25")
    assert any(r["id"] == "num" for r in voice.help_rows())
    # "pick play song seven" is the seventh song; "pick playlist twenty" the twentieth playlist
    assert voice.match("pick play song seven") == "num:7"
    assert voice.match("pick playlist twenty") == "plnum:20"
    assert voice.match("pick play playlist twenty one") == "plnum:21"
    assert voice.match("pick play playlist") == "playall"
    assert voice.match("pick playlist [unk]") == "find:playlist"
    assert voice.match("pick song seven") == "find:song"          # a song called "Seven"
    assert voice.interpret("Pick playlist 20.") == ("command", "plnum:20")


def test_show_me_walks_the_lists_and_search_takes_a_name():
    assert voice.match("pick show me stems") == "list:steems"
    assert voice.match("pick show me favorites") == "list:favs"
    assert voice.match("pick show me bands") == "list:bands"
    assert voice.match("pick show me artists") == "list:bands"
    assert voice.match("pick show me library") == "list:library"
    assert voice.match("pick show me playlists") == "list:playlists"
    assert voice.match("pick show playlists") == "list:playlists"
    assert voice.match("pick stems list") == "list:steems" and voice.match("pick library") == "list:library"
    assert voice.match("pick stems") == "steems"                       # the app's Steems, as before
    assert voice.match("pick search [unk] [unk]") == "find:search"
    assert voice.match("pick find [unk]") == "find:search"
    assert voice.match("pick find playlist [unk]") == "find:playlist"
    assert voice.match("pick search") is None
    assert voice.interpret("Pick search Master of Puppets.") == ("find", "search", "Master of Puppets")
    assert voice.interpret("Pick, find Slipknot") == ("find", "search", "Slipknot")
    assert voice.interpret("Pick search playlist metal") == ("find", "playlist", "metal")
    assert voice.interpret("Slipknot", kind="search") == ("find", "search", "Slipknot")
    assert voice.interpret("Pick show me favorites.") == ("command", "list:favs")
    # the app's own sections, and its pages
    assert voice.match("pick show me podcasts") == "show:podcasts"
    assert voice.match("pick show me tools") == "show:tools"
    assert voice.match("pick show me tabs") == "show:tabs" and voice.match("pick tabs") == "tabs"
    assert voice.match("pick show me loop studio") == "show:loopstudio"
    assert voice.match("pick page down") == "page:+" and voice.match("pick next page") == "page:+"
    assert voice.match("pick page up") == "page:-" and voice.match("pick up") == "up:1"
    assert voice.match("pick help") == "help" and voice.match("pick help open") == "help"
    assert voice.match("pick help close") == "nothing" and voice.match("pick nothing") == "nothing"
    assert voice.match("pick open") == "open"


def test_the_again_shout_is_the_users_own_words_and_voice(tmp_path):
    import math, struct, wave
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    assert c.get("/api/voice/again").json() == {"text": "Say it again!!!", "audio": False, "v": 0}
    assert c.get("/api/voice/again/audio").status_code == 404
    assert c.put("/api/voice/again", json={"text": "  Co?! Znovu!  "}).json()["text"] == "Co?! Znovu!"
    assert c.put("/api/voice/again", json={"text": ""}).json()["text"] == "Say it again!!!"       # emptied: the usual words
    # silence is no shout
    assert c.post("/api/voice/again/audio", content=b"\0" * 32000).status_code == 400
    assert c.post("/api/voice/again/audio", content=b"\0" * 3).status_code == 400
    # one second of quiet, half a second of voice, quiet again: only the voice is kept, and made loud
    tone = b"".join(struct.pack("<h", int(4000 * math.sin(i * 0.2))) for i in range(8000))
    r = c.post("/api/voice/again/audio", content=b"\0" * 32000 + tone + b"\0" * 32000)
    assert r.status_code == 200 and r.json()["audio"] and 0.5 <= r.json()["seconds"] <= 0.7
    got = c.get("/api/voice/again/audio")
    assert got.status_code == 200 and got.headers["content-type"] == "audio/wav"
    (tmp_path / "a.wav").write_bytes(got.content)
    with wave.open(str(tmp_path / "a.wav")) as w:
        frames = w.readframes(w.getnframes())
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    assert max(abs(v) for (v,) in struct.iter_unpack("<h", frames)) > 29000
    assert c.get("/api/voice/again").json()["audio"] is True
    assert c.delete("/api/voice/again/audio").json()["audio"] is False
    assert c.get("/api/voice/again/audio").status_code == 404



def test_open_song_sort_and_the_pages_of_the_help():
    assert voice.match("pick open song [unk] [unk]") == "find:here"
    assert voice.match("pick open song seven") == "num:7"
    assert voice.match("pick open twenty one") == "num:21"
    assert voice.match("pick open") == "open" and voice.match("pick open pick") == "open"
    assert voice.match("pick open in mini") == "tomini"
    assert voice.interpret("Pick open song Wait and Bleed.") == ("find", "here", "Wait and Bleed")
    assert voice.interpret("Wait and Bleed", kind="here") == ("find", "here", "Wait and Bleed")
    assert voice.match("pick sort by band") == "sort:artist" and voice.match("pick sort by artist") == "sort:artist"
    assert voice.match("pick sort by album") == "sort:album"
    assert voice.match("pick sort by title") == "sort:title" and voice.match("pick sort by name") == "sort:title"
    assert voice.match("pick help down") == "helppage:+" and voice.match("pick help up") == "helppage:-"
    assert voice.match("pick page down") == "page:+" and voice.match("pick help") == "help"


def test_preview_commands_and_the_side_panel():
    assert voice.match("pick skip") == "skip:+" and voice.match("pick skip back") == "skip:-"
    assert voice.match("pick practice this song") == "practicethis"
    assert voice.match("pick practice this") == "practicethis"
    assert voice.match("pick practice") == "practice"
    assert voice.match("pick practice [unk] [unk]") == "find:practice"
    assert voice.interpret("Pick, practise this song.") == ("command", "practicethis")
    assert voice.match("pick hide panel") == "panel:off" and voice.match("pick show panel") == "panel:on"
    assert voice.match("pick sort by tuning") == "sort:tuning"
    assert voice.match("pick next song") == "next"


def test_what_of_the_mix_is_looked_at():
    assert voice.match("pick see mix") == "see:mix"
    assert voice.match("pick see lyrics") == "see:lyrics"
    assert voice.match("pick see more guitars") == "gtrs:more"
    assert voice.match("pick show me all guitars") == "gtrs:all"
    assert voice.match("pick see only guitar two") == "gtrs:only:2"
    assert voice.match("pick only guitar three") == "gtrs:only:3"
    assert voice.match("pick see only guitar") == "gtrs:only:0"
    assert voice.match("pick guitar on") == "gtr:on" and voice.match("pick mute guitar") == "mute:guitar"
    assert voice.interpret("Pick, see only guitar 2.") == ("command", "gtrs:only:2")
    assert voice.match("pick practice words") == "words" and voice.match("pick read lyrics") == "words"
    assert voice.match("pick practice my words") == "mywords" and voice.match("pick hard words") == "mywords"
    assert voice.match("pick practice melody") == "melody" and voice.match("pick sing lines") == "melody"


def test_the_wake_word_alone_asks_what_can_be_said():
    assert voice.wake_call(["pick"], [0.95]) == (["pick"], [0.95], True)
    assert voice.wake_call(["hey", "pick"], [0.7, 0.9]) == (["pick"], [0.9], True)
    assert voice.wake_call(["pick", "pick"], [0.9, 0.5])[2] is True
    assert voice.wake_call(["pick"], [0.4])[2] is False                       # a mumble, or the music
    assert voice.wake_call(["hey", "pick", "play"], [1, 1, 1]) == (["pick", "play"], [1, 1], False)
    assert voice.wake_call(["hey"], [1.0]) == (["hey"], [1.0], False)
    assert voice.wake_call(["hey", "play"], [0.6, 1.0]) == (["pick", "play"], [0.6, 1.0], False)   # "pick" misheard as "hey"
    assert voice.wake_call([], []) == ([], [], False)
