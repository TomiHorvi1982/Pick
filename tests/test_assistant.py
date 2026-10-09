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
