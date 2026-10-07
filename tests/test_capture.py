"""Recording past the browser: the inputs of an interface a page never gets."""
import io
import wave

import server


def _wav(path, channels=3, seconds=2.0, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels); w.setsampwidth(3); w.setframerate(rate)
        frame = b"".join(bytes([c + 1, 0, 0]) for c in range(channels))
        w.writeframes(frame * int(seconds * rate))


def test_capture_keeps_the_shared_input_first():
    cmd = server._capture_cmd("Nano Cortex", [2, 3], 0, server.CAPTURE_DIR / "x.wav", 5)
    assert "pan=3c|c0=c0|c1=c2|c2=c3" in cmd
    assert ":Nano Cortex" in cmd


def test_device_is_matched_by_the_browser_label(monkeypatch):
    monkeypatch.setattr(server, "_capture_devices", lambda: ["BlackHole 2ch", "Nano Cortex", "Nano"])
    assert server._capture_device("Nano Cortex (1234:abcd)") == "Nano Cortex"
    assert server._capture_device("Scarlett 2i2") is None


def test_slice_is_cut_to_the_sample(tmp_path, monkeypatch):
    path = tmp_path / "abc.wav"
    _wav(path)
    monkeypatch.setitem(server._CAPTURE, "path", path)
    monkeypatch.setitem(server._CAPTURE, "id", "abc")
    monkeypatch.setitem(server._CAPTURE, "proc", None)
    r = server.capture_slice("abc", start=0.5, dur=1.0)
    with wave.open(io.BytesIO(r.body)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getnframes()) == (3, 3, 48000)
    assert r.headers["x-start"] == "0.500000"
    # past the end: what there is
    r = server.capture_slice("abc", start=1.5, dur=5.0)
    with wave.open(io.BytesIO(r.body)) as w:
        assert w.getnframes() == 24000
    assert server.capture_slice("other", start=0, dur=1).status_code == 404
