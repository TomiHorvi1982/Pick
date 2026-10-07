"""Loop Studio — a song taken apart into a Loop Plan for the player's own rig.

Phase 1: the facts of the song (tempo, key, bars, sections, chords), which
sections to loop and in what order to record the layers, the Loopy Pro setup
card, and the loops themselves — every stem of every looped section cut to a
whole number of bars.

Nothing here separates or tracks beats: that is Steems' work (Demucs, the beat
grid, All-In-One sections, the chord estimate) and this reads its results.
What this adds is the arithmetic that turns seconds into bars, the key, which
stems actually play where, the plan, and the cutting.

Everything measured carries a confidence or is listed under "unavailable";
nothing is filled in to look complete.
"""

from __future__ import annotations

import json
import math
import subprocess
import zipfile
from pathlib import Path

import numpy as np

PLAN_VERSION = 1
AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")
# The order layers are recorded in: the groove first, because every other
# loop's length hangs on it; the voice last.
LAYER_ORDER = ["drums", "bass", "guitar", "piano", "other", "vocals"]
LAYER_NAMES = {"drums": "bicí / groove", "bass": "basa", "guitar": "rytmická kytara", "piano": "klávesy",
               "other": "melodie / ostatní", "vocals": "zpěv"}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

DEFAULT_PROFILE = {
    "looping": {"app": "Loopy Pro 2", "device": "iPhone", "keys": "Akai MPK Mini Play MK3",
                "footswitch": {"name": "Hotone (Bluetooth MIDI)", "buttons": 4, "ccStart": 20},
                "guitar": True, "mic": True},
    "tone": {"modeler": "NanoCortex", "plugins": ["Neural DSP"]},
    "studio": {"daw": "Logic Pro", "drums": "Toontrack"},
    "extras": ["Ampero", "FBV MkII"],
}


# ------------------------------------------------------------------ bars
def bar_starts(grid: dict) -> list:
    """Where each bar begins, in seconds: the tracked downbeats, carried on
    by one more bar so the last bar has an end."""
    db = [float(t) for t in (grid.get("downbeats") or [])]
    if len(db) < 2:
        return db
    return db + [db[-1] + (db[-1] - db[-2])]


def nearest_bar(starts: list, t: float) -> int:
    """Index of the bar line closest to a time."""
    if not starts:
        return 0
    return int(np.argmin(np.abs(np.asarray(starts) - t)))


def plan_bpm(bpm: float) -> float:
    """The tempo the loops are made at: the song's own, to the whole number
    when it is that close to one (a click of 89.97 is 90), else one decimal."""
    return float(round(bpm)) if abs(bpm - round(bpm)) < 0.35 else round(bpm, 1)


# ------------------------------------------------------------------- audio
def decode(path: Path, ffmpeg: str, rate: int = 11025) -> np.ndarray:
    """A file as mono float samples (through ffmpeg, so any format Steems holds)."""
    out = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True, timeout=180)
    return np.frombuffer(out.stdout, dtype=np.float32) if out.returncode == 0 else np.zeros(0, np.float32)


def stem_files(stems_dir: Path) -> dict:
    """role → file. Demucs' six by name; anything else (a project of the
    player's own takes) keeps its own name."""
    found = {}
    for f in sorted(stems_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("."):
            found.setdefault(f.stem, f)
    return found


_KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


_QUALITY_TONES = {"maj": (0, 4, 7), "min": (0, 3, 7), "5": (0, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
                  "dim": (0, 3, 6), "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7)}


def _key_scores(hist: np.ndarray) -> np.ndarray:
    """How well a pitch-class histogram fits each of the 24 keys (12 major, then 12 minor)."""
    out = np.zeros(24)
    if hist.sum() <= 0 or np.allclose(hist, hist[0]):
        return out
    for r in range(12):
        rolled = np.roll(hist, -r)
        out[r] = np.corrcoef(rolled, _KS_MAJ)[0, 1]
        out[12 + r] = np.corrcoef(rolled, _KS_MIN)[0, 1]
    return out


def estimate_key(samples: np.ndarray, rate: int = 11025, chords: list = None):
    """The key, from two witnesses: the sound itself (Krumhansl profiles over
    the summed chroma) and the chords already found (their tones, by how long
    each lasts). The sound alone often lands a fifth off — a song in C minor
    spends a long time on G. Where the two agree the answer is trusted; where
    they do not, the key the two fit best together is given, both single
    answers are reported next to it, and the confidence is capped low. The
    confidence is otherwise how far the winner stands above the runner-up.
    None when there is nothing to go on.
    """
    n = 4096
    audio = np.zeros(24)
    if samples is not None and samples.size >= n * 4:
        hop = n // 2
        frames = 1 + (samples.size - n) // hop
        win = np.hanning(n)
        freqs = np.fft.rfftfreq(n, 1 / rate)
        ok = (freqs >= 65) & (freqs <= 2000)
        pcs = (np.round(69 + 12 * np.log2(np.maximum(freqs, 1e-9) / 440)).astype(int) % 12)[ok]
        chroma = np.zeros(12)
        for i in range(0, frames, 2):                          # every other frame is plenty for a key
            mag = np.abs(np.fft.rfft(samples[i * hop:i * hop + n] * win))[ok]
            np.add.at(chroma, pcs, mag ** 2)
        audio = _key_scores(chroma)
    hist = np.zeros(12)
    for c in chords or []:
        root = c.get("root")
        if root not in NOTE_NAMES:
            continue
        dur = max(0.0, float(c.get("end", 0)) - float(c.get("start", 0)))
        for k, t in enumerate(_QUALITY_TONES.get(c.get("quality") or "maj", (0, 4, 7))):
            hist[(NOTE_NAMES.index(root) + t) % 12] += dur * (1.0 if k == 0 else 0.6)
    harm = _key_scores(hist)
    if not audio.any() and not harm.any():
        return None
    name = lambda i: NOTE_NAMES[i % 12] + (" dur" if i < 12 else " moll")
    both = audio.any() and harm.any()
    total = audio + harm if both else (harm if harm.any() else audio)
    order = np.argsort(-total)
    best, second = int(order[0]), int(order[1])
    margin = float(total[best] - total[second]) / (2.0 if both else 1.0)
    agree = (not both) or int(np.argmax(audio)) == int(np.argmax(harm))
    conf = max(0.0, min(1.0, margin * 5))
    if not agree:
        conf = min(conf, 0.35)                                 # two answers: say so
    return {"key": name(best), "confidence": round(conf, 2), "agree": bool(agree),
            "fromSound": name(int(np.argmax(audio))) if audio.any() else None,
            "fromChords": name(int(np.argmax(harm))) if harm.any() else None}


def stem_activity(stems_dir: Path, sections: list, ffmpeg: str) -> dict:
    """Which stems really play in which section: role → [bool per section].
    A separated stem is never quite silent — it carries the bleed of the
    others — so "plays" means a level within reach of that stem's own loudest
    section, and above a floor."""
    out = {}
    for role, f in stem_files(stems_dir).items():
        x = decode(f, ffmpeg, 8000)
        levels = []
        for s in sections:
            seg = x[int(s["startSec"] * 8000):int(s["endSec"] * 8000)]
            levels.append(float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0)
        top = max(levels, default=0.0)
        out[role] = [bool(top > 0 and lv > max(top * 0.18, 0.004)) for lv in levels]
    return out


# -------------------------------------------------------------------- plan
def chord_per_bar(chords: list, starts: list, first_bar: int, bars: int) -> list:
    """The chord that fills most of each bar."""
    out = []
    for b in range(first_bar, first_bar + bars):
        if b + 1 >= len(starts):
            break
        a, e = starts[b], starts[b + 1]
        best, best_len = None, 0.0
        for c in chords:
            ov = min(e, float(c.get("end", 0))) - max(a, float(c.get("start", 0)))
            name = c.get("chord") or c.get("name") or c.get("label")
            if ov > best_len and name and name not in ("N", "N.C."):
                best, best_len = name, ov
        if best and best_len >= (e - a) * 0.35:
            out.append({"bar": b + 1, "chord": best})
    return out


def repeat_unit(chords: list, first_bar: int, bars: int) -> int:
    """The shortest run of bars whose chords the whole section repeats —
    what is worth looping. The section itself when nothing repeats, or when
    the chords are not known well enough to say."""
    seq = {c["bar"]: c["chord"] for c in chords}
    row = [seq.get(first_bar + 1 + i) for i in range(bars)]
    if sum(1 for c in row if c) < bars * 0.75:
        return bars
    for unit in (1, 2, 4, 8, 16):
        if unit >= bars or bars % unit:
            continue
        if all(row[i] is None or row[i % unit] is None or row[i] == row[i % unit] for i in range(bars)):
            return unit if unit >= 2 else 2 if bars % 2 == 0 else bars      # a one-bar vamp is looped as two
    return bars


def build_sections(grid: dict, analysis: dict) -> list:
    """All-In-One's sections (seconds) on the bar grid."""
    starts = bar_starts(grid)
    chords = analysis.get("chords") or []
    out = []
    for i, s in enumerate(analysis.get("sections") or []):
        a, b = nearest_bar(starts, float(s["start"])), nearest_bar(starts, float(s["end"]))
        if b <= a:
            continue
        out.append({"id": f"s{i + 1}", "label": s.get("name") or s.get("kind") or f"Část {i + 1}", "kind": s.get("kind") or "other",
                    "startBar": a + 1, "bars": b - a, "startSec": round(starts[a], 3), "endSec": round(starts[b], 3),
                    "chords": chord_per_bar(chords, starts, a, b - a)})
    return out


def build_loops(sections: list, activity: dict) -> list:
    """One loop per kind of section (the first verse stands for the verses):
    how many bars, which layers, in what order."""
    loops, seen = [], set()
    for k, s in enumerate(sections):
        kind = s["kind"]
        if kind in seen or kind in ("start", "end", "silence"):
            continue
        seen.add(kind)
        unit = repeat_unit(s["chords"], s["startBar"] - 1, s["bars"])
        roles = [r for r in activity if activity[r][k]] if activity else []
        layers = [r for r in LAYER_ORDER if r in roles] + sorted(r for r in roles if r not in LAYER_ORDER)
        loops.append({"id": f"l{len(loops) + 1}", "sectionId": s["id"], "label": s["label"], "kind": kind,
                      "startBar": s["startBar"], "bars": unit, "sectionBars": s["bars"], "enabled": True,
                      "layers": layers, "files": []})
    return loops


def _gcd_all(values):
    g = 0
    for v in values:
        g = math.gcd(g, int(v))
    return g or 1


def loopy_card(facts: dict, loops: list, profile: dict) -> dict:
    """The Loopy Pro setup the plan asks for, from the loops and the rig."""
    on = [l for l in loops if l.get("enabled", True)]
    bars = [l["bars"] for l in on] or [4]
    # The clock is the shortest loop; the others lock to it when they are
    # whole multiples of it. A common divisor of one bar is no clock at all —
    # then the loops run free.
    master = min(bars)
    locked = all(b % master == 0 for b in bars)
    if not locked:
        g = _gcd_all(bars)
        if g >= 2:
            master, locked = g, True
    kinds = {l["kind"] for l in on}
    looping = (profile or {}).get("looping") or {}
    fs = looping.get("footswitch") or {}
    n, cc0 = int(fs.get("buttons") or 0), int(fs.get("ccStart") or 20)
    song_form = "verse" in kinds and "chorus" in kinds
    template = "Verse-Chorus Looper" if song_form else "Simple 4 Track Looper"
    actions = (["Sloka (Verse)", "Refrén (Chorus)", "Groove", "Přepínač módu (Mode)"] if song_form
               else ["Stopa 1", "Stopa 2", "Stopa 3", "Stopa 4"])
    foot = [{"button": i + 1, "cc": cc0 + i, "action": actions[i] if i < len(actions) else "volné"} for i in range(n)]
    notes = []
    if song_form:
        notes.append("Verse-Chorus Looper je z výroby nastavený na MIDI CC 20–23 pro tlačítka 1–4 — footswitch nastav na tyhle CC, "
                     "nebo použij MIDI Learn v Loopy Pro (dlouhé podržení se naučí jako akce Hold, třeba pro Clear).")
    else:
        notes.append("Píseň nemá jasně rozlišenou sloku a refrén — vrstvi do Simple 4 Track Looper a pedály namapuj přes MIDI Learn.")
    if not locked:
        notes.append("Smyčky nemají délky, které by se dělily společným základem — nech je volné (free), ne phase-locked.")
    if looping.get("keys") and any("piano" in l["layers"] for l in on):
        notes.append(f"V písni hrají klávesy: {looping['keys']} zapoj jako MIDI zdroj do AUv3 nástroje (šablona MIDI Showcase ukazuje jak), pady na spouštění.")
    inputs = []
    if looping.get("guitar"):
        modeler = ((profile or {}).get("tone") or {}).get("modeler")
        inputs.append("Kytara" + (f" přes {modeler}" if modeler else " přes zvukovou kartu"))
    if looping.get("mic"):
        inputs.append("Mikrofon na samostatnou stopu")
    order = []
    for r in LAYER_ORDER:
        if any(r in l["layers"] for l in on):
            order.append(LAYER_NAMES[r])
    return {"bpm": facts["bpm"], "timeSignature": facts["timeSignature"], "masterBars": master, "phaseLocked": locked,
            "countInBars": 1, "quantize": "bar", "template": template, "alsoSee": ["MIDI Showcase"] if looping.get("keys") else [],
            "footswitch": foot, "keys": looping.get("keys") or "", "inputs": inputs, "recordOrder": order, "notes": notes}


def build_plan(name: str, grid: dict, analysis: dict, key, activity: dict, profile: dict, meta: dict = None) -> dict:
    unavailable = []
    bpm = float(grid.get("bpm") or 0)
    sections = build_sections(grid, analysis)
    if not sections:
        unavailable.append("struktura (sekce se nepodařilo určit)")
    if not any(s["chords"] for s in sections):
        unavailable.append("akordy")
    if not key:
        unavailable.append("tónina")
    segs = grid.get("tempo_segments") or []
    facts = {"bpm": plan_bpm(bpm), "bpmMeasured": round(bpm, 2),
             # how steady the song's own tempo is: one segment and a flat curve is a click track
             "tempoSteady": len(segs) <= 1, "tempoSegments": len(segs) or 1,
             "key": key["key"] if key else None, "keyConfidence": key["confidence"] if key else None,
             "keyFromSound": key.get("fromSound") if key else None, "keyFromChords": key.get("fromChords") if key else None,
             "keyAgree": key.get("agree") if key else None,
             "timeSignature": f"{int(grid.get('beats_per_bar') or 4)}/4", "beatsPerBar": int(grid.get("beats_per_bar") or 4),
             "bars": max(0, len(bar_starts(grid)) - 1), "sections": sections, "detector": grid.get("detector")}
    loops = build_loops(sections, activity)
    plan = {"version": PLAN_VERSION, "song": {"name": name, "artist": (meta or {}).get("artist"), "title": (meta or {}).get("title"),
                                                "durationSec": round(float(grid.get("duration") or 0), 1)},
            "facts": facts, "loops": loops, "loopy": loopy_card(facts, loops, profile),
            "fixedTempo": True, "edited": False, "unavailable": unavailable,
            "later": {"tone": "Fáze 2 — analýza kytarového stemu, karty NanoCortex a Neural DSP",
                      "studio": "Fáze 3 — bicí do MIDI, karta Logic Pro + Toontrack"}}
    return plan


def apply_edits(plan: dict, edits: dict, grid: dict, profile: dict) -> dict:
    """The player's corrections over the measured plan: tempo, key, a
    section's name, bars and chords, a loop's length or whether it is used.
    Seconds follow the bars; the Loopy card is worked out again."""
    starts = bar_starts(grid)
    facts = plan["facts"]
    f = edits.get("facts") or {}
    if f.get("bpm"):
        facts["bpm"] = float(f["bpm"])
    if "key" in f:
        facts["key"], facts["keyConfidence"] = f["key"] or None, None
    by_id = {s["id"]: s for s in facts["sections"]}
    for e in f.get("sections") or []:
        s = by_id.get(e.get("id"))
        if not s:
            continue
        if e.get("label"):
            s["label"] = str(e["label"])[:60]
        if e.get("kind"):
            s["kind"] = str(e["kind"])
        if e.get("startBar") or e.get("bars"):
            a = max(1, int(e.get("startBar") or s["startBar"]))
            n = max(1, int(e.get("bars") or s["bars"]))
            a = min(a, max(1, len(starts) - 1))
            n = min(n, len(starts) - a)
            s.update(startBar=a, bars=n, startSec=round(starts[a - 1], 3), endSec=round(starts[a - 1 + n], 3))
        if isinstance(e.get("chords"), list):
            s["chords"] = [{"bar": int(c["bar"]), "chord": str(c["chord"])[:12]} for c in e["chords"] if c.get("chord")]
    loops = {l["id"]: l for l in plan["loops"]}
    for e in edits.get("loops") or []:
        l = loops.get(e.get("id"))
        if not l:
            continue
        if "enabled" in e:
            l["enabled"] = bool(e["enabled"])
        if e.get("bars"):
            l["bars"] = max(1, int(e["bars"]))
        if e.get("startBar"):
            l["startBar"] = max(1, min(int(e["startBar"]), max(1, len(starts) - 1)))
    if "fixedTempo" in edits:
        plan["fixedTempo"] = bool(edits["fixedTempo"])
    plan["loopy"] = loopy_card(facts, plan["loops"], profile)
    if any(k in edits for k in ("facts", "loops", "fixedTempo")):
        plan["edited"] = True                 # an empty edit only refreshes the card (a new rig)
    return plan


# -------------------------------------------------------------------- loops
def loop_span(loop: dict, grid: dict):
    """(start, end) of a loop in the recording, on its bar lines."""
    starts = bar_starts(grid)
    a = loop["startBar"] - 1
    b = min(a + loop["bars"], len(starts) - 1)
    return (starts[a], starts[b]) if 0 <= a < b else None


def _atempo(ratio: float) -> str:
    """ffmpeg's atempo takes 0.5–2 per stage."""
    stages = []
    while ratio > 2.0:
        stages.append(2.0)
        ratio /= 2.0
    while ratio < 0.5:
        stages.append(0.5)
        ratio /= 0.5
    stages.append(ratio)
    return ",".join(f"atempo={r:.6f}" for r in stages)


def cut_loops(plan: dict, grid: dict, stems_dir: Path, ffmpeg: str, on_progress=None) -> list:
    """Every stem of every loop in use, as a WAV of exactly the loop's bars.

    With fixedTempo the cut is fitted to the plan's tempo: a band drifts a few
    milliseconds per bar, and a loop a hair long or short walks off the
    looper's clock within a minute. The fit changes speed, not pitch, and by a
    percent or two at most — the file is then that many bars at that BPM to
    the sample. Without it the cut is the recording's own bars as played.
    """
    rate = 44100
    out_root = stems_dir / "Loops"
    if out_root.is_dir():
        for f in out_root.rglob("*.wav"):
            f.unlink()
    files = stem_files(stems_dir)
    bpb, bpm = plan["facts"]["beatsPerBar"], plan["facts"]["bpm"]
    todo = [l for l in plan["loops"] if l.get("enabled", True)]
    made, k, total = [], 0, sum(len(l["layers"]) + 1 for l in todo) or 1
    for loop in todo:
        loop["files"] = []
        span = loop_span(loop, grid)
        if not span:
            continue
        start, end = span
        dur = end - start
        target = loop["bars"] * bpb * 60.0 / bpm if plan.get("fixedTempo", True) else dur
        ratio = dur / target
        if not 0.8 <= ratio <= 1.25:                       # the plan's tempo is not this section's tempo
            loop["warning"] = "Tempo téhle části se od tempa plánu liší o víc než pětinu — smyčka je vyříznutá bez úpravy tempa."
            target, ratio = dur, 1.0
        else:
            loop.pop("warning", None)
        samples = int(round(target * rate))
        folder = out_root / f"{loop['startBar']:03d} {_safe(loop['label'])} ({loop['bars']} taktů)"
        folder.mkdir(parents=True, exist_ok=True)
        chain = (_atempo(ratio) + "," if abs(ratio - 1) > 1e-4 else "") + f"apad,atrim=end_sample={samples}"
        for role in loop["layers"]:
            src = files.get(role)
            if src is None:
                continue
            dst = folder / f"{role}.wav"
            r = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{start:.6f}", "-t", f"{dur:.6f}", "-i", str(src),
                                "-af", chain, "-ar", str(rate), "-ac", "2", "-c:a", "pcm_s16le", str(dst)],
                               capture_output=True, timeout=120)
            if r.returncode == 0 and dst.is_file():
                loop["files"].append({"stem": role, "path": str(dst.relative_to(stems_dir)), "seconds": round(samples / rate, 4)})
                made.append(dst)
            k += 1
            if on_progress:
                on_progress(k / total)
        # the layers together, to hear what the loop should add up to
        parts = [folder / f"{r}.wav" for r in loop["layers"] if (folder / f"{r}.wav").is_file()]
        if len(parts) > 1:
            dst = folder / "_mix.wav"
            cmd = [ffmpeg, "-v", "error", "-y"]
            for p in parts:
                cmd += ["-i", str(p)]
            cmd += ["-filter_complex", f"amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.97,atrim=end_sample={samples}",
                    "-ar", str(rate), "-ac", "2", "-c:a", "pcm_s16le", str(dst)]
            if subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0:
                loop["files"].append({"stem": "_mix", "path": str(dst.relative_to(stems_dir)), "seconds": round(samples / rate, 4)})
                made.append(dst)
        loop["seconds"] = round(samples / rate, 4)
        loop["stretch"] = round((ratio - 1) * 100, 2)        # percent the cut was sped up (+) or slowed (−)
        k += 1
        if on_progress:
            on_progress(k / total)
    return made


def _safe(name: str) -> str:
    return "".join(c for c in (name or "") if c.isalnum() or c in " -_()").strip()[:40] or "smycka"


def zip_loops(plan: dict, stems_dir: Path) -> Path:
    """The loops and a card of the setup, in one file to AirDrop."""
    out = stems_dir / "Loops" / f"{_safe(plan['song']['name'])} - smycky.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    ly = plan["loopy"]
    card = [f"{plan['song']['name']}", "",
            f"BPM: {ly['bpm']}   takt: {ly['timeSignature']}   tónina: {plan['facts']['key'] or 'neurčena'}",
            f"Master clock: {ly['masterBars']} taktů   smyčky: {'phase-locked' if ly['phaseLocked'] else 'free'}",
            f"Count-in: {ly['countInBars']} takt   quantizace: po taktech", f"Šablona Loopy Pro: {ly['template']}", "",
            "Pořadí nahrávání: " + " → ".join(ly["recordOrder"]), "", "Footswitch:"]
    card += [f"  tlačítko {f['button']} = CC {f['cc']} — {f['action']}" for f in ly["footswitch"]]
    card += ["", "Smyčky:"]
    card += [f"  {l['label']}: {l['bars']} taktů od taktu {l['startBar']} ({', '.join(l['layers'])})" for l in plan["loops"] if l.get("enabled", True)]
    card += [""] + ly["notes"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        z.writestr("Loop Plan.txt", "\n".join(card))
        for loop in plan["loops"]:
            for f in loop.get("files") or []:
                p = stems_dir / f["path"]
                if p.is_file():
                    z.write(p, str(Path(f["path"]).relative_to("Loops")))
    return out


def load_plan(stems_dir: Path):
    try:
        return json.loads((stems_dir / "loopplan.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_plan(stems_dir: Path, plan: dict) -> None:
    (stems_dir / "loopplan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
