"""The assistant's conversation with the model: what goes to it, what comes back to the page."""
import assistant


def _model(script):
    seen = []

    def call(messages):
        seen.append([dict(m) for m in messages])
        return script[len(seen) - 1]
    return call, seen


def _use(i, tool, **inp):
    return {"type": "tool_use", "id": i, "name": tool, "input": inp}


def test_a_request_becomes_calls_then_a_sentence():
    call, seen = _model([
        {"content": [_use("a", "open_song", name="master of puppets", where="steems")]},
        {"content": [_use("b", "stem", stem="guitar", action="mute"), _use("c", "set_tempo", percent=80)]},
        {"content": [{"type": "text", "text": "Hotovo."}]}])
    r = assistant.say("s", "pusť master of puppets, ztlum kytaru, zpomal na 80", call)
    assert not r["done"] and [c["name"] for c in r["calls"]] == ["open_song"]
    r = assistant.results("s", [{"id": "a", "content": "Otevřeno ve Steems."}], call)
    assert [c["name"] for c in r["calls"]] == ["stem", "set_tempo"]
    r = assistant.results("s", [{"id": "b", "content": "kytara ztlumená"}, {"id": "c", "content": "tempo 80 %"}], call)
    assert r == {"say": "Hotovo.", "calls": [], "done": True, "usage": None}
    # what the model was shown the last time: its own calls, each answered
    last = seen[-1]
    assert [m["role"] for m in last] == ["user", "assistant", "user", "assistant", "user"]
    assert [b["tool_use_id"] for b in last[-1]["content"]] == ["b", "c"]


def test_a_tool_that_was_not_answered_is_reported_as_failed():
    call, seen = _model([{"content": [_use("a", "playback", action="play"), _use("b", "volume", steps=1)]},
                         {"content": [{"type": "text", "text": "Jen napůl."}]}])
    assistant.say("s", "pusť to a přidej", call)
    assistant.results("s", [{"id": "a", "content": "hraje"}], call)
    answered = seen[-1][-1]["content"]
    assert answered[0].get("is_error") is None and answered[1]["is_error"] is True


def test_the_next_sentence_remembers_the_last():
    call, seen = _model([{"content": [{"type": "text", "text": "Kterou píseň?"}]},
                         {"content": [{"type": "text", "text": "Dobře."}]}])
    assert assistant.say("s", "pusť tu od Metalliky", call)["say"] == "Kterou píseň?"
    assistant.say("s", "One", call)
    assert [m["content"] for m in seen[-1] if m["role"] == "user"] == ["pusť tu od Metalliky", "One"]


def test_an_error_leaves_no_half_said_request_behind():
    call, seen = _model([{"error": "Nejde se připojit k internetu."}, {"content": [{"type": "text", "text": "Ano."}]}])
    r = assistant.say("s", "pusť něco", call)
    assert r["error"] and r["done"]
    assistant.say("s", "znovu", call)
    assert [m["content"] for m in seen[-1]] == ["znovu"]


def test_it_cannot_go_round_forever():
    call, seen = _model([{"content": [_use(str(i), "get_state")]} for i in range(assistant.MAX_ROUNDS + 2)])
    r = assistant.say("s", "co hraje", call)
    for i in range(assistant.MAX_ROUNDS + 1):
        if r["done"]:
            break
        r = assistant.results("s", [{"id": r["calls"][0]["id"], "content": "{}"}], call)
    assert r["done"] and r["calls"] == [] and len(seen) == assistant.MAX_ROUNDS + 1


def test_calls_left_unanswered_void_that_request():
    call, seen = _model([{"content": [_use("a", "get_state")]}, {"content": [{"type": "text", "text": "Ano."}]}])
    assistant.say("s", "co hraje", call)
    assistant.say("s", "stop", call)                      # the page never came back with results
    assert [m["content"] for m in seen[-1]] == ["stop"]
    assert assistant.results("s", [], call)["error"]      # nothing is waiting for results


def test_tools_are_well_formed_and_nothing_destructive():
    for t in assistant.TOOLS:
        assert t["input_schema"]["type"] == "object" and t["input_schema"]["additionalProperties"] is False
        assert t["description"] and len(t["description"]) > 40
    assert not any(w in " ".join(assistant.TOOL_NAMES) for w in ("delete", "remove", "overwrite", "save"))


def test_without_a_key_it_says_what_to_do(client):
    assert "API klíč" in client.get("/api/assistant").json()["unavailable"] or "prostředí" in client.get("/api/assistant").json()["unavailable"]
    r = client.post("/api/assistant/say", json={"session": "s", "text": "pusť něco"})
    assert r.status_code == 503 and r.json()["error"]


def test_the_key_comes_from_the_file_or_the_environment(tmp_path, monkeypatch):
    assert assistant.key() == ""
    assistant.KEY_PATH.write_text("  sk-ant-test\n")
    assert assistant.key() == "sk-ant-test"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert assistant.key() == "sk-ant-env"


# ---- the model in this computer, and the book of sentences around it

def _local(monkeypatch, answers):
    """The local brain with a stand-in for the model: it answers `answers` in turn; what it was asked is kept."""
    asked = []

    def think(state, text, before):
        asked.append(text)
        a = answers[len(asked) - 1]
        return (None, a) if isinstance(a, str) else ({"pokyny": [text], "calls": a, "say": "ok"}, None)
    return (lambda messages: assistant.ask_local(messages, think)), asked


def _play(sid, text, call, answer="hotovo"):
    """One request as the page plays it: every call answered. → (the calls but get_state, what was said)"""
    r, got = assistant.say(sid, text, call), []
    for _ in range(8):
        if r.get("error") or r["done"]:
            break
        got += [(c["name"], c["input"]) for c in r["calls"] if c["name"] != "get_state"]
        r = assistant.results(sid, [{"id": c["id"], "content": "{}" if c["name"] == "get_state" else (answer(c) if callable(answer) else answer)} for c in r["calls"]], call)
    return got, r.get("say") or r.get("error")


def test_the_book_knows_its_sentences_and_all_of_them_are_callable():
    import assistant_book as book
    assert len(book.BOOK) > 1500
    for v in book.BOOK.values():
        for c in v["calls"]:
            tool = next(t for t in assistant.TOOLS if t["name"] == c["tool"])
            props = tool["input_schema"]["properties"]
            assert set(c["args"]) <= set(props)
            for k, x in c["args"].items():
                assert "enum" not in props[k] or x in props[k]["enum"], (v["text"], c)
    assert book.find("Ztlum bicí!") == [{"tool": "stem", "args": {"stem": "drums", "action": "mute"}}]
    assert [c["tool"] for c in book.find("vypni kytaru a zpomal to na osmdesát, pak ukaž text")] == ["stem", "set_tempo", "command"]
    assert book.find("pusť mi Enter Sandman") is None      # a name: that is for the model
    assert book.near("utlum bubny prosím", 1)[0][2][0]["args"] == {"stem": "drums", "action": "mute"}


def test_a_sentence_of_the_book_is_done_at_once_without_the_model(monkeypatch):
    call, asked = _local(monkeypatch, [])
    got, said = _play("s", "Ztlum bicí a zapni metronom", call, lambda c: "bicí ztlumené" if c["name"] == "stem" else "metronom")
    assert got == [("stem", {"stem": "drums", "action": "mute"}), ("command", {"id": "metronome"})]
    assert said == "Bicí ztlumené. Metronom." and asked == [] and assistant.learned() == {}


def test_what_the_model_did_is_learned_and_what_was_wrong_is_forgotten(monkeypatch):
    call, asked = _local(monkeypatch, [[{"tool": "stem", "args": {"stem": "drums", "action": "quieter"}}]])
    assert _play("s", "ty bubny mi trhají uši", call)[0] == [("stem", {"stem": "drums", "action": "quieter"})]
    assert _play("s", "Ty bubny mi trhají uši!", call)[0] == [("stem", {"stem": "drums", "action": "quieter"})]
    assert asked == ["ty bubny mi trhají uši"]             # the second time nobody was asked
    got, said = _play("s", "špatně", call)
    assert got == [] and "ty bubny mi trhají uši" in said and assistant.learned() == {}
    call, asked = _local(monkeypatch, [[{"tool": "stem", "args": {"stem": "drums", "action": "mute"}}]])
    _play("s2", "ty bubny mi trhají uši", call)
    assert asked == ["ty bubny mi trhají uši"] and assistant.learned() == {}        # asked again, and never learned again


def test_a_sentence_not_understood_is_learned_from_the_next_one(monkeypatch):
    call, asked = _local(monkeypatch, [[], []])
    got, said = _play("s", "bubeník ať zmlkne", call)
    assert got == [] and "nerozuměl" in said and [r["text"] for r in assistant._read(assistant.UNKNOWN, [])] == ["bubeník ať zmlkne"]
    got, said = _play("s", "umlč bubeníka", call)           # the book knows this one
    assert got == [("stem", {"stem": "drums", "action": "mute"})] and "bubeník ať zmlkne" in said
    assert _play("s", "bubeník ať zmlkne", call)[0] == [("stem", {"stem": "drums", "action": "mute"})] and len(asked) == 1
    # …but an unrelated sentence teaches nothing
    _play("s", "kolik je hodin", call)
    _play("s", "otevři ladičku", call)
    assert "kolik je hodin" not in " ".join(v["text"] for v in assistant.learned().values())


def test_the_models_slips_are_put_right(monkeypatch):
    mend = lambda text, *calls: assistant._mend(text, [{"tool": t, "args": a} for t, a in calls], len(calls))[0]
    assert assistant._mend("smaž tuhle písničku", [{"tool": "practice", "args": {}}])[1] == "Mazat hlasem nejde."
    # a name nobody said; the mixer where the player was meant; the band, not a song
    assert mend("chci cvičit tuhle písničku", ("practice", {"name": "Sepultura – Roots"})) == [{"tool": "practice", "args": {}}]
    assert mend("pusť mi Enter Sandman", ("open_song", {"name": "Metallica - Enter Sandman", "where": "steems"})) == [{"tool": "open_song", "args": {"name": "Enter Sandman", "where": "player"}}]
    assert mend("otevři Blind v mixu", ("open_song", {"name": "Blind", "where": "steems"}))[0]["args"]["where"] == "steems"
    assert mend("pusť mi něco od Sepultury", ("open_song", {"name": "Sepultura", "where": "player"})) == [{"tool": "play_band", "args": {"name": "Sepultura"}}]
    # practice for something that is no practice; more calls than things asked; a volume out of range
    assert mend("otevři minipřehrávač", ("practice", {})) == []
    assert assistant._mend("zacykli refrén", [{"tool": "loop_section", "args": {"section": "chorus"}}, {"tool": "playback", "args": {"action": "play"}}], 1)[0] == [{"tool": "loop_section", "args": {"section": "chorus"}}]
    assert mend("dej to naplno", ("volume", {"steps": 10}))[0]["args"]["steps"] == 6


def test_a_song_is_opened_first_and_the_rest_waits_for_it(monkeypatch):
    plan = [{"tool": "open_song", "args": {"name": "Blind", "where": "steems"}}, {"tool": "stem", "args": {"stem": "vocals", "action": "mute"}}]
    call, _ = _local(monkeypatch, [plan, plan])
    got, said = _play("s", "otevři Blind v mixu a ztlum zpěv", call, lambda c: "Píseň „Blind“ jsem v knihovně nenašel." if c["name"] == "open_song" else "zpěv ztlumený")
    assert [g[0] for g in got] == ["open_song"] and "nenašel" in said and assistant.learned() == {}      # the vocals of another song were left alone
    got, said = _play("s2", "otevři Blind v mixu a ztlum zpěv", call, lambda c: "Otevřeno ve Steems: Korn — Blind. Nehraje, dokud nezavoláš playback play." if c["name"] == "open_song" else "zpěv ztlumený")
    assert [g[0] for g in got] == ["open_song", "stem"] and "playback" not in said and "Zpěv ztlumený" in said


def test_with_no_model_to_ask_the_nearest_sentence_serves(monkeypatch):
    call, _ = _local(monkeypatch, ["Model asistenta není k nalezení.", "Model asistenta není k nalezení."])
    assert _play("s", "prosím tě ztlum ty bicí", call)[0] == [("stem", {"stem": "drums", "action": "mute"})]
    got, said = _play("s2", "jaké bude zítra počasí v Brně", call)
    assert got == [] and "není k nalezení" in said


def test_the_page_of_what_can_be_said(client):
    page = client.get("/assistant/book").text
    assert "Co Pick umí na slovo" in page and "ztlum bicí" in page and "Zatím nic." in page and "bicí: ztlumit" in page


def test_a_sentence_is_taught_by_hand_and_forgotten(client, monkeypatch):
    mute = {"tool": "stem", "args": {"stem": "bass", "action": "louder"}}
    click = {"tool": "command", "args": {"id": "metronome"}}
    assert client.post("/api/assistant/teach", json={"text": "", "calls": [mute]}).status_code == 400
    assert client.post("/api/assistant/teach", json={"text": "dej tam víc basy", "calls": []}).status_code == 400
    # only what Pick does can be taught: nothing made up, nothing with a name in it
    assert client.post("/api/assistant/teach", json={"text": "dej tam víc basy", "calls": [{"tool": "delete_song", "args": {}}]}).status_code == 400
    assert client.post("/api/assistant/teach", json={"text": "dej tam víc basy", "calls": [{"tool": "open_song", "args": {"name": "x", "where": "player"}}]}).status_code == 400
    assert "už Pick zná" in client.post("/api/assistant/teach", json={"text": "Ztlum bicí!", "calls": [mute]}).json()["error"]
    assistant._missed("dej tam víc basy")
    assert client.post("/api/assistant/teach", json={"text": "Dej tam víc basy", "calls": [mute, click]}).json() == {"ok": True}
    assert assistant._read(assistant.UNKNOWN, []) == []          # no longer a sentence nobody understood
    call, asked = _local(monkeypatch, [])
    assert _play("s", "dej tam víc basy!", call)[0] == [("stem", {"stem": "bass", "action": "louder"}), ("command", {"id": "metronome"})] and asked == []
    page = client.get("/assistant/book").text
    assert "Naučit novou větu" in page and "basa: hlasitěji + metronom zap / vyp" in page and "data-forget" in page
    assert client.delete("/api/assistant/learned", params={"text": "dej tam víc basy"}).json() == {"ok": True}
    assert client.delete("/api/assistant/learned", params={"text": "dej tam víc basy"}).json() == {"ok": False}
    assert assistant.learned() == {}
