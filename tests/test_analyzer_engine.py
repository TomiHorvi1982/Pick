"""Guitar Performance Analyzer: the engine finds the mistakes written into its
own demo performance — and nothing where the playing is clean. The engine is
JavaScript (it runs in the browser), so this runs it under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_engine_finds_the_mistakes_in_the_demo_performance():
    script = Path(__file__).parent / "js" / "analyzer_engine_check.js"
    out = json.loads(subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True).stdout)

    assert out["refNotes"] == out["written"]                  # every note of the riff is heard, none invented
    assert abs(out["bpm"] - 112) < 1.5
    assert any("G#2 instead of A2" in t for t in out["wrongAt5"])
    assert out["missedAt11"]                                   # reported as missed, not as its neighbour played wrong
    assert any("late" in t for t in out["lateAt18"])
    assert any("early" in t for t in out["earlyAt26"])
    assert any("shorter" in t for t in out["shortAt30"])
    assert any("cents" in t for t in out["sharpAt37"])         # 45 cents sharp: out of tune, not a wrong note
    assert out["extras"] == 1 and out["counts"]["missed"] == 1 and out["counts"]["wrong"] == 1
    assert any("Rushing" in t for t in out["rushing"])
    assert out["cleanIssues"] == 0                             # the clean opening has no errors
    assert out["scores"]["articulation"] is None               # not analysed, so not scored
    assert 80 < out["scores"]["overall"] < 99
