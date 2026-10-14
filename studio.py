"""Skladatel — a song out of an idea.

The player brings a piece of guitar (or nothing but a wish), picks styles or
describes the sound in their own words, and gets whole songs to choose from:
the idea stays as it was played and a band takes it on from where it ends, in
its tempo and harmony — once per style and variant. With a text, the song is
sung; the text can be written here too (lyricist below).

The music is made by ACE-Step 1.5 (github.com/ace-step/ACE-Step-1.5), an open
model that runs on this computer: its own environment and its weights beside
the app's settings, started as its own small server only while something is
being made and put away again after a while of nothing to do (it holds several
GB of memory). Two kinds of it are offered, the quick one (turbo, 8 steps) and
the detailed one (SFT, 50 steps, slower); its planner (the 1.7B "LM") can be
let think first. The big XL models (20 GB of weights) do not fit this Mac.

Every finished variant is taken apart into its instruments (Demucs, as in
Steems) once nothing is being composed, so it can be heard instrument by
instrument right here. What is made is kept per project — the idea, each
variant as an MP3 with its stems, what was asked — so the choosing can wait.
Putting a chosen variant into the library, and having it sung in the player's
own voice, is the server's part (server.py).
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional

import httpx
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from studio_words import LYRIC_TAGS, TIPS_CAPTION, TIPS_LYRICS, WORDS

HOME = Path.home()
STUDIO_DIR = HOME / ".local" / "share" / "spotdl-helper" / "studio"
ACE_DIR = STUDIO_DIR / "ACE-Step-1.5"
PROJECTS = STUDIO_DIR / "projects"
ENGINE_PID = STUDIO_DIR / "engine.pid"
ENGINE_LOG = STUDIO_DIR / "engine.log"
PORT = int(os.environ.get("PICK_STUDIO_PORT") or 8795)
URL = f"http://127.0.0.1:{PORT}"
IDLE = 180                     # seconds with nothing to make before the model is put away (it crowds the memory)
room = lambda: True            # the server says whether the memory has room for the model to be loaded
COLD = 130                     # seconds the model takes to wake up
IDEA_MAX = 60.0                # of the idea, this much is used
LENGTHS = (30, 45, 60, 90, 120, 150, 180, 240, 300)
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
DEMUCS = str(HOME / ".local" / "bin" / "demucs")
STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
LM_MODEL = "acestep-5Hz-lm-1.7B"

# The kinds of the model there are here. rtf: seconds of work per second of music, warm (measured on an M1 Pro).
MODELS = {
    "turbo": {"dir": "acestep-v15-turbo", "name": "Rychlý (Turbo)", "steps": 8, "max_steps": 20, "cfg": False, "rtf": 1.6,
              "note": "8 kroků, nejlepší poměr kvality a rychlosti — doporučený pro běžné skládání."},
    "sft": {"dir": "acestep-v15-sft", "name": "Podrobný (SFT)", "steps": 50, "max_steps": 200, "cfg": True, "rtf": 4.5,
            "note": "50 kroků, víc detailu a lépe drží popis; jde ladit síla popisu. Asi třikrát pomalejší."},
    "base": {"dir": "acestep-v15-base", "name": "Základní (Base)", "steps": 32, "max_steps": 200, "cfg": True, "rtf": 6.0,
             "note": "Univerzální model; Pick ho používá na kapelu pod kytarou. Na celé songy je lepší SFT."},
}
TURBO_MODEL = MODELS["turbo"]["dir"]
BASE_MODEL = MODELS["base"]["dir"]
KEYS = [f"{n} {m}" for m in ("major", "minor") for n in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")]
TIMESIGS = {"4": "4/4", "3": "3/4", "6": "6/8", "2": "2/4"}

# A style is a caption the model was taught on: genre, the instruments, the feel.
STYLES = [
    {"id": "hardrock", "name": "Hard rock", "caption": "hard rock, overdriven electric guitars, punchy live drums, driving bass, powerful, anthemic"},
    {"id": "heavy", "name": "Heavy metal", "caption": "classic heavy metal, twin distorted guitars, galloping bass, powerful drums, epic, energetic"},
    {"id": "thrash", "name": "Thrash metal", "caption": "thrash metal, fast palm-muted distorted guitars, double bass drums, aggressive, tight, high energy"},
    {"id": "groove", "name": "Groove metal", "caption": "groove metal, down-tuned heavy guitar riffs, heavy syncopated drums, thick bass, aggressive, mid-tempo"},
    {"id": "numetal", "name": "Nu metal", "caption": "nu metal, down-tuned guitars, heavy groove, punchy drums, dark, modern production"},
    {"id": "death", "name": "Death metal", "caption": "death metal, tremolo picked distorted guitars, blast beats, deep bass, brutal, dark"},
    {"id": "punk", "name": "Punk rock", "caption": "punk rock, fast power chords, raw electric guitars, simple driving drums, energetic, rebellious"},
    {"id": "grunge", "name": "Grunge", "caption": "grunge, fuzzy distorted guitars, loud quiet dynamics, heavy drums, moody, raw, nineties alternative rock"},
    {"id": "stoner", "name": "Stoner rock", "caption": "stoner rock, fuzzy down-tuned guitars, slow heavy groove, thick bass, hypnotic, desert rock"},
    {"id": "alt", "name": "Alternativní rock", "caption": "alternative rock, layered electric guitars, melodic bass, dynamic drums, emotional, modern"},
    {"id": "poprock", "name": "Pop rock", "caption": "pop rock, bright electric guitars, catchy, steady drums, warm bass, radio friendly, uplifting"},
    {"id": "ballad", "name": "Rocková balada", "caption": "rock ballad, clean and overdriven guitars, slow tempo, emotional, big drums, soaring, melodic"},
    {"id": "blues", "name": "Blues rock", "caption": "blues rock, gritty overdriven guitar, shuffle groove, hammond organ, walking bass, soulful"},
    {"id": "funk", "name": "Funk rock", "caption": "funk rock, tight rhythm guitar, slap bass, syncopated drums, groovy, energetic"},
    {"id": "folk", "name": "Akustický folk", "caption": "acoustic folk rock, acoustic guitars, soft drums, upright bass, warm, intimate, storytelling"},
    {"id": "synth", "name": "Synthwave rock", "caption": "synthwave rock, analog synthesizers, electric guitar leads, electronic drums, retro eighties, driving"},
]
_STYLE = {s["id"]: s for s in STYLES}
LANGS = {"cs": "česky", "en": "anglicky", "sk": "slovensky", "de": "německy", "pl": "polsky", "es": "španělsky",
         "fr": "francouzsky", "it": "italsky", "ru": "rusky"}

router = APIRouter()
_LOCK = threading.RLock()
_STATE = {"proc": None, "last": 0.0, "worker": None, "now": None, "model": None, "live": {}}
demucs_device: Callable[[], Optional[str]] = lambda: None       # the server hands its own in (MPS when there is one)


# ------------------------------------------------------------------ engine

def ready() -> bool:
    return (ACE_DIR / ".venv" / "bin" / "acestep-api").is_file()


def model_ready(key: str) -> bool:
    m = MODELS.get(key)
    return bool(m) and (ACE_DIR / "checkpoints" / m["dir"]).is_dir() and any((ACE_DIR / "checkpoints" / m["dir"]).glob("*.safetensors"))


def lm_ready() -> bool:
    return (ACE_DIR / "checkpoints" / LM_MODEL).is_dir()


def engine_up() -> bool:
    try:
        return httpx.get(URL + "/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _free_memory() -> None:
    """Whatever else of Pick's holds a model in memory lets it go: there is room for one."""
    try:
        import assistant
        assistant.llm_stop()
    except Exception:                                      # noqa: BLE001
        pass


def engine_start(wait: float = 240.0, model: str = "", lm: bool = False) -> bool:
    model = model or TURBO_MODEL
    with _LOCK:
        if engine_up():
            return True
        if not ready():
            return False
        _free_memory()
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "ACESTEP_LM_BACKEND": "mlx", "TOKENIZERS_PARALLELISM": "false",
               "ACESTEP_INIT_LLM": "true" if lm else "false", "ACESTEP_LM_MODEL_PATH": LM_MODEL,
               "ACESTEP_CONFIG_PATH": model}
        log = open(ENGINE_LOG, "ab")
        proc = subprocess.Popen([str(ACE_DIR / ".venv" / "bin" / "acestep-api"), "--host", "127.0.0.1", "--port", str(PORT)],
                                cwd=str(ACE_DIR), env=env, stdout=log, stderr=log, start_new_session=True)
        _STATE["proc"] = proc
        _STATE["model"] = (model, lm)
        ENGINE_PID.write_text(str(proc.pid))
    end = time.time() + wait
    while time.time() < end:
        if engine_up():
            return True
        if proc.poll() is not None:
            return False
        time.sleep(1.0)
    return False


def engine_stop() -> None:
    with _LOCK:
        pid = None
        try:
            pid = int(ENGINE_PID.read_text().strip())
        except (OSError, ValueError):
            pass
        proc, _STATE["proc"] = _STATE["proc"], None
        _STATE["model"] = None
        for p in filter(None, [proc.pid if proc else None, pid]):
            try:
                os.killpg(p, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(p, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
        ENGINE_PID.unlink(missing_ok=True)


def engine_model(name: str, lm: bool = False) -> None:
    """The model the engine works with — one at a time, two do not fit into this
    computer's memory — so changing it (or adding the planner) means starting the engine again."""
    if _STATE.get("model") == (name, lm) and engine_up():
        return
    engine_stop()
    time.sleep(2.0)
    if not engine_start(model=name, lm=lm):
        raise RuntimeError(f"model {name} se nepodařilo spustit")


# ----------------------------------------------------------------- projects

_ID = re.compile(r"^[a-f0-9]{8,32}$")


def _dir(pid: str) -> Optional[Path]:
    return PROJECTS / pid if _ID.match(pid or "") and (PROJECTS / pid / "project.json").is_file() else None


def load(pid: str) -> Optional[dict]:
    d = _dir(pid)
    if d is None:
        return None
    try:
        return json.loads((d / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(p: dict) -> None:
    d = PROJECTS / p["id"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "project.json")


def update_variant(pid: str, vid: str, **fields) -> None:
    """Fields of one variant written into the project as it is on disk now (it may have changed meanwhile)."""
    with _LOCK:
        p = load(pid)
        v = variant(p, vid) if p else None
        if v is not None:
            v.update(fields)
            save(p)


def projects() -> list:
    out = []
    for d in (PROJECTS.iterdir() if PROJECTS.is_dir() else []):
        p = load(d.name)
        if p:
            out.append(p)
    return sorted(out, key=lambda p: -(p.get("created") or 0))


def _seconds(path: Path) -> float:
    try:
        out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=30).stdout
        return float(out.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def variant(p: dict, vid: str) -> Optional[dict]:
    return next((v for v in p.get("variants", []) if v["id"] == vid), None)


def variant_file(p: dict, vid: str) -> Optional[Path]:
    v = variant(p, vid)
    f = PROJECTS / p["id"] / f"{vid}.mp3" if v and v.get("status") == "done" else None
    return f if f is not None and f.is_file() else None


def stems_dir(pid: str, vid: str) -> Path:
    return PROJECTS / pid / vid


def stems_of(pid: str, vid: str) -> List[str]:
    d = stems_dir(pid, vid)
    return [s for s in STEMS if (d / f"{s}.mp3").is_file()]


def under_ready() -> bool:
    return model_ready("base")


def estimate(length: float, mode: str = "after", idea_len: float = 0.0, model: str = "turbo", think: bool = False) -> float:
    """Seconds one variant takes, warm — before the model itself says how far it is."""
    rtf = MODELS.get(model, MODELS["turbo"])["rtf"]
    plan = 45 if think else 0
    if mode == "under" and idea_len:
        # drums, then bass, each about 3.3 × the idea; then the other model is woken for the rest of the song
        rest = COLD + 15 + length * rtf if length >= idea_len + 15 else 0
        return 30 + idea_len * 6.8 + rest
    return 15 + plan + length * rtf


def separate_estimate(length: float) -> float:
    return 20 + length * 0.45


# -------------------------------------------------------------- the request

class Options(BaseModel):
    """Everything about how the model makes a variant — what the window's settings hold."""
    model: str = "turbo"
    think: bool = False            # the planner thinks the song through first (no idea only: it is skipped when going on from one)
    cot_caption: bool = True       # …and may write the description out in more detail
    lm_temperature: float = 0.85
    lm_cfg: float = 2.0
    steps: int = 0                 # 0 = the model's own
    guidance: float = 7.0          # how closely the description is followed (SFT, Base)
    shift: float = 3.0
    method: str = "ode"            # "ode" exact, "sde" with a little chance in it
    adg: bool = False
    cfg_start: float = 0.0
    cfg_end: float = 1.0
    bpm: int = 0                   # 0 = let it choose
    key: str = ""
    timesig: str = ""
    seed: int = -1                 # -1 = a new one each time
    ref_strength: float = 0.5      # how much of the sound reference is taken
    use_ref: bool = True
    separate: bool = True          # take the finished song apart into instruments


def clean_options(o: Options) -> dict:
    m = o.model if o.model in MODELS else "turbo"
    spec = MODELS[m]
    clamp = lambda x, lo, hi: max(lo, min(hi, x))
    return {
        "model": m, "think": bool(o.think), "cot_caption": bool(o.cot_caption),
        "lm_temperature": round(clamp(float(o.lm_temperature), 0.1, 2.0), 2), "lm_cfg": round(clamp(float(o.lm_cfg), 1.0, 5.0), 2),
        "steps": int(clamp(int(o.steps) or spec["steps"], 1, spec["max_steps"])),
        "guidance": round(clamp(float(o.guidance), 1.0, 15.0), 2), "shift": round(clamp(float(o.shift), 1.0, 5.0), 2),
        "method": o.method if o.method in ("ode", "sde") else "ode", "adg": bool(o.adg) and spec["cfg"],
        "cfg_start": round(clamp(float(o.cfg_start), 0.0, 1.0), 2), "cfg_end": round(clamp(float(o.cfg_end), 0.0, 1.0), 2),
        "bpm": int(clamp(int(o.bpm), 40, 240)) if o.bpm else 0,
        "key": o.key if o.key in KEYS else "", "timesig": o.timesig if o.timesig in TIMESIGS else "",
        "seed": int(o.seed) if int(o.seed) >= 0 else -1,
        "ref_strength": round(clamp(float(o.ref_strength), 0.0, 1.0), 2), "use_ref": bool(o.use_ref),
        "separate": bool(o.separate),
    }


def lyrics_for(text: str, has_idea: bool) -> str:
    """The text as the model reads it: parts in [brackets], the idea as the intro."""
    text = (text or "").strip()
    if not text:
        return "[Instrumental]"
    if not re.search(r"^\s*\[", text, re.M):               # plain lines: verses, split at blank lines
        parts = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        names = ["Verse", "Chorus"] if len(parts) > 1 else ["Verse"]
        text = "\n\n".join(f"[{names[i % len(names)]}]\n{b}" for i, b in enumerate(parts))
    return ("[Intro]\n\n" if has_idea else "") + text


def caption_for(style_caption: str, desc: str, sung: bool, idea: bool) -> str:
    cap = ", ".join(x.strip(" ,") for x in (style_caption, desc) if x and x.strip(" ,"))
    return cap + (", full band arrangement" if idea else "") + (", expressive lead vocals" if sung else ", instrumental, no vocals")


def payload(p: dict, v: dict) -> dict:
    o = {**clean_options(Options()), **(v.get("opts") or {})}
    sung = bool((p.get("lyrics") or "").strip())
    spec = MODELS.get(o["model"], MODELS["turbo"])
    idea = bool(p.get("idea"))
    data = {"prompt": caption_for(v.get("caption", ""), v.get("desc", ""), sung, idea),
            "lyrics": lyrics_for(p.get("lyrics") or "", idea), "vocal_language": p.get("lang") or "en",
            "audio_duration": v["length"], "inference_steps": o["steps"], "batch_size": 1, "audio_format": "mp3",
            "infer_method": o["method"], "thinking": bool(o["think"]) and not idea,
            "use_random_seed": o["seed"] < 0}
    if o["seed"] >= 0:
        data["seed"] = o["seed"]
    if spec["cfg"]:
        data.update(guidance_scale=o["guidance"], shift=o["shift"], use_adg=o["adg"],
                    cfg_interval_start=o["cfg_start"], cfg_interval_end=o["cfg_end"])
    if data["thinking"]:
        data.update(lm_temperature=o["lm_temperature"], lm_cfg_scale=o["lm_cfg"], use_cot_caption=o["cot_caption"])
    else:
        data.update(use_cot_caption=False, use_cot_language=False)
    if o["bpm"]:
        data["bpm"] = o["bpm"]
    if o["key"]:
        data["key_scale"] = o["key"]
    if o["timesig"]:
        data["time_signature"] = o["timesig"]
    ref = PROJECTS / p["id"] / "ref.wav"
    if o["use_ref"] and p.get("ref") and ref.is_file():
        data.update(reference_audio_path=str(ref), audio_cover_strength=o["ref_strength"])
    if idea:
        # the idea is kept as played and the song goes on from its end
        data.update(task_type="repaint", repainting_start=round(p["idea_len"], 2), repainting_end=v["length"],
                    chunk_mask_mode="explicit")
    return data


def _form(data: dict) -> dict:
    return {k: ("true" if x else "false") if isinstance(x, bool) else str(x) for k, x in data.items()}


def _ace(data: dict, src: Optional[Path] = None, progress: Optional[Callable[[float, str], None]] = None) -> tuple:
    """One piece of music asked of the model and waited for. → (the MP3, what the model says it made)."""
    with httpx.Client(timeout=120.0) as c:
        if src is not None:
            with open(src, "rb") as f:
                r = c.post(URL + "/release_task", data=_form(data), files={"src_audio": (src.name, f, "audio/wav")})
        else:
            r = c.post(URL + "/release_task", json=data)
        r.raise_for_status()
        task = json.loads(r.text, strict=False)["data"]["task_id"]
        end = time.time() + 4 * 3600
        while time.time() < end:
            time.sleep(2.5)
            q = c.post(URL + "/query_result", json={"task_id_list": [task]})
            row = json.loads(q.text, strict=False)["data"][0]
            res = (json.loads(row.get("result") or "[]", strict=False) or [{}])[0]
            if row.get("status") == 0:
                if progress is not None:
                    progress(float(res.get("progress") or 0.0), str(res.get("stage") or ""))
                continue
            if row.get("status") != 1 or not res.get("file"):
                raise RuntimeError(res.get("error") or "model skončil bez výsledku")
            audio = c.get(URL + res["file"])
            audio.raise_for_status()
            info = {"seed": str(res.get("seed_value") or "").split(",")[0], "metas": res.get("metas") or {}}
            return audio.content, info
    raise RuntimeError("trvalo to moc dlouho")


def _live(vid: str, pct: float, stage: str = "") -> None:
    """How far the variant is, as the model (or Demucs) says; where the counting began is kept,
    so the time left follows the speed of the work itself and not the model's waking up."""
    pct = max(0.0, min(1.0, pct))
    old = _STATE["live"].get(vid) or {}
    row = {"pct": pct, "stage": stage, "at": time.time()}
    if old.get("t0") is not None and old.get("kind") == (stage == "stems") and pct >= old.get("p0", 0):
        row.update(t0=old["t0"], p0=old["p0"], kind=old["kind"])
    elif pct > 0.0:
        row.update(t0=time.time(), p0=pct, kind=stage == "stems")
    _STATE["live"][vid] = row


# ---- "kapela pod kytarou": the band plays with the idea from its first note.
# The big model adds one instrument at a time to what it hears — the drums to
# the guitar, then the bass to guitar and drums, so each locks to the one
# before — and gives back that instrument alone; the three are mixed here,
# the guitar on top. A song longer than the idea is then carried on from that
# mix by the chosen model, which now hears a whole band to go on with.
RATE = 48000


def _pcm(path: Path):
    import numpy as np
    raw = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def _wav(samples, path: Path) -> None:
    import wave
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def mix_band(parts: list):
    """[(samples, share)] → one mix: each part brought to the same loudness, weighed by its share, never clipping."""
    import numpy as np
    n = min(len(x) for x, _ in parts)
    out = np.zeros((n, 2), dtype=np.float32)
    for x, share in parts:
        rms = float(np.sqrt(np.mean(np.square(x[:n])))) or 1.0
        out += x[:n] * (0.12 / rms) * share
    peak = float(np.max(np.abs(out))) or 1.0
    return out * (0.92 / peak) if peak > 0.92 else out


def even_out(song, at: int, fade: int = RATE // 2):
    """The part under the idea brought up to the loudness of what follows it — the
    model plays the rest of the song louder than a guitar with two instruments."""
    import numpy as np
    rms = lambda x: float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    head, rest = rms(song[:at]), rms(song[at:])
    if at <= fade or len(song) - at <= fade or head < 1e-5 or rest <= head:
        return song
    gain = min(rest * 0.85 / head, 3.0)
    env = np.full(len(song), gain, dtype=np.float32)
    env[at:at + fade] = np.linspace(gain, 1.0, fade, dtype=np.float32)
    env[at + fade:] = 1.0
    out = song * env[:, None]
    return np.tanh(out * 1.05) / np.tanh(1.05) * 0.97 if float(np.max(np.abs(out))) > 0.97 else out   # loud peaks rounded, not cut


def _lego(src: Path, track: str, caption: str, steps: int = 24, progress=None) -> bytes:
    return _ace({"task_type": "lego", "instruction": f"Generate the {track.upper()} track based on the audio context:",
                 "prompt": caption, "lyrics": "[Instrumental]", "inference_steps": steps, "guidance_scale": 7,
                 "batch_size": 1, "thinking": False, "audio_format": "mp3"}, src, progress)[0]


def _make_under(p: dict, v: dict) -> dict:
    d = PROJECTS / p["id"]
    tmp = d / f"{v['id']}.work"
    tmp.mkdir(exist_ok=True)
    o = {**clean_options(Options()), **(v.get("opts") or {})}
    whole = v["length"] >= p["idea_len"] + 15
    span = (0.0, 0.3, 0.6, 1.0) if whole else (0.0, 0.5, 1.0, 1.0)
    part = lambda i, stage: (lambda x, _s="": _live(v["id"], span[i] + (span[i + 1] - span[i]) * x, stage))
    info = {}
    try:
        _live(v["id"], 0.0, "wake")
        engine_model(BASE_MODEL)
        idea = _pcm(d / "idea.wav")
        cap = caption_for(v.get("caption", ""), v.get("desc", ""), False, False).replace(", instrumental, no vocals", "")
        (tmp / "drums.mp3").write_bytes(_lego(d / "idea.wav", "drums", cap + ", live drum kit, kick, snare, hi-hat, cymbals",
                                               progress=part(0, "drums")))
        drums = _pcm(tmp / "drums.mp3")
        _wav(mix_band([(idea, 1.0), (drums, 1.0)]), tmp / "with-drums.wav")
        (tmp / "bass.mp3").write_bytes(_lego(tmp / "with-drums.wav", "bass", cap + ", bass guitar following the chords",
                                              progress=part(1, "bass")))
        band = mix_band([(idea, 1.0), (drums, 1.1), (_pcm(tmp / "bass.mp3"), 0.8)])
        _wav(band, tmp / "band.wav")
        if whole:
            _live(v["id"], span[2], "wake")
            engine_model(MODELS[o["model"]]["dir"] if model_ready(o["model"]) else TURBO_MODEL)
            song, info = _ace(payload(p, v), tmp / "band.wav", part(2, "song"))
            (tmp / "song.mp3").write_bytes(song)
            _wav(even_out(_pcm(tmp / "song.mp3"), int(p["idea_len"] * RATE)), tmp / "song.wav")
            subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", "-i", str(tmp / "song.wav"), "-codec:a", "libmp3lame", "-b:a", "256k",
                            str(d / f"{v['id']}.mp3")], capture_output=True, timeout=180)
            if not (d / f"{v['id']}.mp3").is_file():
                shutil.copy(tmp / "song.mp3", d / f"{v['id']}.mp3")
        else:
            v["length"] = int(round(p["idea_len"]))
            subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", "-i", str(tmp / "band.wav"), "-codec:a", "libmp3lame", "-b:a", "256k",
                            str(d / f"{v['id']}.mp3")], capture_output=True, timeout=120)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return info


def _make(p: dict, v: dict) -> dict:
    d = PROJECTS / p["id"]
    if v.get("mode") == "under" and p.get("idea"):
        return _make_under(p, v)
    o = {**clean_options(Options()), **(v.get("opts") or {})}
    think = o["think"] and not p.get("idea") and lm_ready()
    _live(v["id"], 0.0, "wake")
    engine_model(MODELS[o["model"]]["dir"] if model_ready(o["model"]) else TURBO_MODEL, think)
    song, info = _ace(payload(p, v), d / "idea.wav" if p.get("idea") else None, lambda x, s: _live(v["id"], x, s or "song"))
    (d / f"{v['id']}.mp3").write_bytes(song)
    return info


# ---- the instruments of a finished variant: Demucs (htdemucs_6s, as in Steems)

def separate(pid: str, vid: str) -> None:
    src = PROJECTS / pid / f"{vid}.mp3"
    out = stems_dir(pid, vid)
    work = PROJECTS / pid / f"{vid}.sep"
    shutil.rmtree(work, ignore_errors=True)
    cmd = [DEMUCS, "-n", "htdemucs_6s", "-o", str(work), "--filename", "{stem}.{ext}", "--mp3", "--mp3-bitrate", "256"]
    dev = demucs_device()
    if dev:
        cmd += ["-d", dev]
    cmd.append(str(src))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    buf = b""
    while True:
        chunk = proc.stderr.read(256)
        if not chunk:
            break
        buf = (buf + chunk)[-2000:]
        m = re.findall(rb"(\d{1,3})%\|", buf)
        if m:
            _live(vid, int(m[-1]) / 100.0, "stems")
    if proc.wait() != 0:
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("rozdělení na nástroje se nepovedlo: " + buf.decode("utf-8", "replace").strip().splitlines()[-1][:160]
                           if buf.strip() else "rozdělení na nástroje se nepovedlo")
    made = work / "htdemucs_6s"
    out.mkdir(parents=True, exist_ok=True)
    for s in STEMS:
        f = made / f"{s}.mp3"
        if f.is_file():
            f.replace(out / f"{s}.mp3")
    shutil.rmtree(work, ignore_errors=True)
    (PROJECTS / pid / f"{vid}.peaks.json").unlink(missing_ok=True)
    if not stems_of(pid, vid):
        raise RuntimeError("Demucs nic nevytvořil")


# ------------------------------------------------------------------ the queue

def _next_compose():
    for p in sorted(projects(), key=lambda p: p.get("created") or 0):
        v = next((v for v in p.get("variants", []) if v.get("status") == "queued"), None)
        if v:
            return p, v
    return None


def _next_separate():
    for p in sorted(projects(), key=lambda p: p.get("created") or 0):
        v = next((v for v in p.get("variants", []) if v.get("status") == "done" and v.get("stems") == "queued"), None)
        if v:
            return p, v
    return None


def _compose(p: dict, v: dict) -> None:
    o = {**clean_options(Options()), **(v.get("opts") or {})}
    want = (BASE_MODEL, False) if v.get("mode") == "under" else (MODELS[o["model"]]["dir"], o["think"] and not p.get("idea"))
    cold = not (engine_up() and _STATE.get("model") == want)
    update_variant(p["id"], v["id"], status="running", started=time.time(), cold=cold)
    v.update(status="running", started=time.time(), cold=cold)
    _STATE["now"] = (p["id"], v["id"])
    try:
        info = _make(p, v) or {}                            # starts the engine with the model the variant needs
        fields = {"status": "done", "took": time.time() - v["started"], "length": v["length"]}
        if info.get("seed"):
            fields["seed"] = info["seed"]
        metas = info.get("metas") or {}
        fields["metas"] = {k: metas.get(k) for k in ("bpm", "keyscale", "timesignature") if metas.get(k) not in (None, "", "N/A", "None")}
        if o["separate"]:
            fields["stems"] = "queued"
        update_variant(p["id"], v["id"], **fields)
    except Exception as e:                                 # noqa: BLE001 — one failed variant must not stop the rest
        update_variant(p["id"], v["id"], status="error", error=str(e)[:200])
    finally:
        _STATE["now"] = None
        _STATE["live"].pop(v["id"], None)
        _STATE["last"] = time.time()


def _separate_one(p: dict, v: dict) -> None:
    if engine_up():
        engine_stop()                                       # the composing model and Demucs together do not fit the memory
    update_variant(p["id"], v["id"], stems="running", stems_started=time.time())
    _STATE["now"] = (p["id"], v["id"])
    try:
        separate(p["id"], v["id"])
        update_variant(p["id"], v["id"], stems="done")
    except Exception as e:                                 # noqa: BLE001
        update_variant(p["id"], v["id"], stems="error", stems_error=str(e)[:200])
    finally:
        _STATE["now"] = None
        _STATE["live"].pop(v["id"], None)
        _STATE["last"] = time.time()


def _work() -> None:
    """One variant after another, oldest first; then their instruments, once nothing is
    being composed; the model is put away when none is left for a while."""
    while True:
        with _LOCK:
            job = _next_compose()
            sep = None if job else _next_separate()
            if job is None and sep is None and time.time() - _STATE["last"] > IDLE:
                _STATE["worker"] = None
                engine_stop()
                return
        if job and not engine_up() and not room() and time.time() - _STATE.setdefault("room_wait", time.time()) < 300:
            time.sleep(3.0)                    # the model is not loaded and the memory is short: a while, then anyway
            continue
        if job:
            _STATE.pop("room_wait", None)
            _compose(*job)
        elif sep:
            _separate_one(*sep)
        else:
            time.sleep(2.0)


def _kick() -> None:
    with _LOCK:
        _STATE["last"] = time.time()
        if _STATE["worker"] is None or not _STATE["worker"].is_alive():
            _STATE["worker"] = threading.Thread(target=_work, daemon=True)
            _STATE["worker"].start()


def resume() -> None:
    """After a restart: what was being made is made again."""
    found = False
    for p in projects():
        for v in p.get("variants", []):
            if v.get("status") == "running":
                v["status"] = "queued"
            if v.get("stems") == "running":
                v["stems"] = "queued"
            found = found or v.get("status") == "queued" or (v.get("status") == "done" and v.get("stems") == "queued")
        save(p)
    if found and ready():
        _kick()
    else:
        engine_stop()                                       # a model left running by the server before this one


# ------------------------------------------------------------------ progress

def _eta(p: dict, v: dict) -> tuple:
    """(how far, 0–1; seconds still to go) for the variant being worked on."""
    o = {**clean_options(Options()), **(v.get("opts") or {})}
    live = _STATE["live"].get(v["id"]) or {}
    if v.get("stems") == "running":
        est = separate_estimate(v.get("length") or 60)
        el = time.time() - (v.get("stems_started") or time.time())
    else:
        wake = (COLD + (110 if o["think"] and not p.get("idea") else 0)) if v.get("cold") else 0
        est = estimate(v["length"], v.get("mode") or "after", p.get("idea_len") or 0, o["model"], o["think"]) + wake
        el = time.time() - (v.get("started") or time.time())
    pct = live.get("pct") or 0.0
    if live.get("t0") is not None and pct - live.get("p0", 0) >= 0.03:
        rate = (pct - live["p0"]) / max(1.0, time.time() - live["t0"])     # of the whole, per second, as it goes now
        return pct, max(5.0, (1 - pct) / rate)
    return (pct if pct else min(0.95, el / est) if est else 0.0), max(10.0, est - el)


def progress_now() -> Optional[dict]:
    """What is being done right now, and how much is left of all of it — for the bar at the top."""
    ps = projects()
    now, rest, count = None, 0.0, 0
    for p in ps:
        for v in p.get("variants", []):
            o = {**clean_options(Options()), **(v.get("opts") or {})}
            if v.get("status") == "running" or v.get("stems") == "running":
                pct, left = _eta(p, v)
                stage = (_STATE["live"].get(v["id"]) or {}).get("stage") or ""
                now = {"project": p["id"], "project_name": p["name"], "variant": v["id"], "name": v["name"], "pct": round(pct, 3),
                       "left": round(left), "stage": "stems" if v.get("stems") == "running" else stage,
                       "elapsed": round(time.time() - (v.get("stems_started") if v.get("stems") == "running" else v.get("started") or time.time()))}
                rest += left
                count += 1
            elif v.get("status") == "queued":
                rest += estimate(v["length"], v.get("mode") or "after", p.get("idea_len") or 0, o["model"], o["think"])
                rest += separate_estimate(v["length"]) if o["separate"] else 0
                count += 1
            elif v.get("status") == "done" and v.get("stems") == "queued":
                rest += separate_estimate(v.get("length") or 60)
                count += 1
    if not count:
        return None
    return {"now": now, "left": round(rest), "count": count}


# ---------------------------------------------------------------------- API

def view(p: dict) -> dict:
    out = {k: p.get(k) for k in ("id", "name", "created", "idea", "idea_len", "lyrics", "lang", "desc")}
    out["ref"] = bool(p.get("ref")) and (PROJECTS / p["id"] / "ref.wav").is_file()
    out["ref_name"] = p.get("ref_name") or ""
    out["variants"] = []
    for v in p.get("variants", []):
        row = {k: v.get(k) for k in ("id", "style", "name", "length", "status", "error", "took", "used", "sung", "mode", "seed",
                                     "metas", "stems", "stems_error", "desc", "caption")}
        o = {**clean_options(Options()), **(v.get("opts") or {})}
        row["model"] = o["model"]
        row["model_name"] = MODELS[o["model"]]["name"]
        row["think"] = o["think"] and not p.get("idea")
        if v.get("status") == "running" or v.get("stems") == "running":
            pct, left = _eta(p, v)
            row.update(pct=round(pct, 3), left=round(left), stage=(_STATE["live"].get(v["id"]) or {}).get("stage") or "")
        if v.get("status") == "done":
            row["url"] = f"/api/studio/audio/{p['id']}/{v['id']}.mp3"
            row["stem_list"] = stems_of(p["id"], v["id"])
        out["variants"].append(row)
    if p.get("idea"):
        out["idea_url"] = f"/api/studio/audio/{p['id']}/idea.wav"
    return out


@router.get("/api/studio")
def status():
    waiting = sum(1 for p in projects() for v in p.get("variants", []) if v.get("status") in ("queued", "running"))
    import lyricist
    return {"ready": ready(), "under": under_ready(), "engine": engine_up(), "lm": lm_ready(),
            "models": [{"id": k, "name": m["name"], "note": m["note"], "ready": model_ready(k), "steps": m["steps"],
                        "max_steps": m["max_steps"], "cfg": m["cfg"]} for k, m in MODELS.items()],
            "styles": [{"id": s["id"], "name": s["name"], "caption": s["caption"]} for s in STYLES],
            "lengths": list(LENGTHS), "langs": LANGS, "keys": KEYS, "timesigs": TIMESIGS,
            "words": [{"id": c["id"], "name": c["name"], "words": [list(w) for w in c["words"]]} for c in WORDS],
            "tags": [{"id": c["id"], "name": c["name"], "tags": [list(t) for t in c["tags"]]} for c in LYRIC_TAGS],
            "tips": {"caption": TIPS_CAPTION, "lyrics": TIPS_LYRICS}, "lyricist": lyricist.available(),
            "waiting": waiting, "progress": progress_now(), "projects": [view(p) for p in projects()]}


@router.get("/api/studio/progress")
def progress():
    """Only the bar at the top: small, asked often."""
    return {"progress": progress_now()}


@router.post("/api/studio/projects")
async def project_new(name: str = "", file: Optional[UploadFile] = File(None)):
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS / pid
    d.mkdir(parents=True)
    p = {"id": pid, "name": (name or "").strip()[:80] or "Nový song", "created": time.time(), "idea": False, "idea_len": 0,
         "lyrics": "", "lang": "cs", "variants": []}
    if file is not None:
        data = await file.read()
        if len(data) > 300 * 1024 * 1024:
            shutil.rmtree(d, ignore_errors=True)
            return JSONResponse({"error": "Soubor je příliš velký."}, status_code=400)
        raw = d / ("raw" + (Path(file.filename or "x.wav").suffix[:8] or ".wav"))
        raw.write_bytes(data)
        # the idea itself: no silence before it, at most a minute, at the model's rate
        subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", "-i", str(raw), "-af",
                        "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                        "-t", str(IDEA_MAX), "-ac", "2", "-ar", "48000", str(d / "idea.wav")], capture_output=True, timeout=180)
        raw.unlink(missing_ok=True)
        length = _seconds(d / "idea.wav")
        if length < 3.0:
            shutil.rmtree(d, ignore_errors=True)
            return JSONResponse({"error": "V nahrávce je málo hudby — aspoň tři vteřiny."}, status_code=400)
        p.update(idea=True, idea_len=length)
        if not (name or "").strip():
            p["name"] = Path(file.filename or "Nápad").stem[:80]
    save(p)
    return view(p)


@router.post("/api/studio/projects/{pid}/reference")
async def reference_set(pid: str, file: UploadFile = File(...)):
    """A song whose sound the variants should have (its timbre and mix, not its melody)."""
    with _LOCK:
        p = load(pid)
    if p is None:
        return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
    d = PROJECTS / pid
    data = await file.read()
    if len(data) > 300 * 1024 * 1024:
        return JSONResponse({"error": "Soubor je příliš velký."}, status_code=400)
    raw = d / ("ref-raw" + (Path(file.filename or "x.mp3").suffix[:8] or ".mp3"))
    raw.write_bytes(data)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", "-i", str(raw), "-t", "240", "-ac", "2", "-ar", "48000", str(d / "ref.wav")],
                   capture_output=True, timeout=180)
    raw.unlink(missing_ok=True)
    if _seconds(d / "ref.wav") < 5.0:
        (d / "ref.wav").unlink(missing_ok=True)
        return JSONResponse({"error": "Ze souboru se nepodařilo přečíst zvuk."}, status_code=400)
    with _LOCK:
        p = load(pid)
        p.update(ref=True, ref_name=Path(file.filename or "předloha").stem[:80])
        save(p)
    return view(p)


@router.delete("/api/studio/projects/{pid}/reference")
def reference_drop(pid: str):
    with _LOCK:
        p = load(pid)
        if p is None:
            return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
        (PROJECTS / pid / "ref.wav").unlink(missing_ok=True)
        p.update(ref=False, ref_name="")
        save(p)
    return view(p)


class MakeRequest(BaseModel):
    styles: List[str] = []
    desc: str = ""                 # the sound in the player's own words (English works best)
    custom: str = ""               # older name of the same
    per: int = 1                   # variants of each style
    length: int = 90
    lyrics: str = ""
    lang: str = "cs"
    mode: str = "after"            # "after": the band comes in when the idea ends · "under": it plays with it from the start
    opts: Options = Options()


@router.post("/api/studio/projects/{pid}/make")
def project_make(pid: str, req: MakeRequest):
    if not ready():
        return JSONResponse({"error": "Model pro skládání není nainstalovaný."}, status_code=503)
    with _LOCK:
        p = load(pid)
        if p is None:
            return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
        desc = " ".join((req.desc or req.custom or "").split())[:600]
        wanted = [dict(_STYLE[s]) for s in req.styles if s in _STYLE]
        if not wanted and desc:
            wanted = [{"id": "custom", "name": "Můj popis", "caption": ""}]
        if not wanted:
            return JSONResponse({"error": "Vyber aspoň jeden styl, nebo popiš zvuk vlastními slovy."}, status_code=400)
        opts = clean_options(req.opts)
        if not model_ready(opts["model"]):
            return JSONResponse({"error": f"Model {MODELS[opts['model']]['name']} ještě není stažený."}, status_code=503)
        length = max(10, min(600, int(req.length)))
        mode = "under" if req.mode == "under" and p.get("idea") else "after"
        # after the idea there has to be room for the band; under it, the idea's own length is a song too
        if p.get("idea") and mode == "after" and length < p["idea_len"] + 20:
            length = next((x for x in LENGTHS if x >= p["idea_len"] + 20), LENGTHS[-1])
        per = max(1, min(int(req.per), 4))
        if mode == "under" and not under_ready():
            return JSONResponse({"error": "Model pro kapelu pod kytarou není stažený."}, status_code=503)
        if len(wanted) * per + sum(1 for v in p["variants"] if v.get("status") in ("queued", "running")) > 16:
            return JSONResponse({"error": "To je moc najednou — nejvýš 16 variant ve frontě."}, status_code=400)
        p["lyrics"], p["lang"], p["desc"] = (req.lyrics or "").strip()[:6000], req.lang if req.lang in LANGS else "cs", desc
        n = len(p["variants"])
        for s in wanted:
            for k in range(per):
                n += 1
                o = dict(opts)
                if o["seed"] >= 0 and k:
                    o["seed"] = o["seed"] + k               # a fixed seed, and still not the same song twice
                p["variants"].append({"id": uuid.uuid4().hex[:10], "style": s["id"], "name": s["name"] + (f" {k + 1}" if per > 1 else ""),
                                      "caption": s["caption"], "desc": desc, "length": length, "status": "queued", "n": n,
                                      "mode": mode, "opts": o})
        save(p)
    _kick()
    return view(p)


@router.get("/api/studio/projects/{pid}")
def project_get(pid: str):
    p = load(pid)
    return view(p) if p else JSONResponse({"error": "Takový projekt tu není."}, status_code=404)


@router.delete("/api/studio/projects/{pid}")
def project_delete(pid: str):
    d = _dir(pid)
    if d is not None:
        with _LOCK:
            now = _STATE["now"]
            if now and now[0] == pid:
                return JSONResponse({"error": "Na tomhle projektu se právě pracuje — počkej, až doběhne."}, status_code=409)
            shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@router.delete("/api/studio/projects/{pid}/variants/{vid}")
def variant_delete(pid: str, vid: str):
    with _LOCK:
        p = load(pid)
        if p is None:
            return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
        v = variant(p, vid)
        if v and (v.get("status") == "running" or v.get("stems") == "running"):
            return JSONResponse({"error": "Na téhle variantě se právě pracuje."}, status_code=409)
        p["variants"] = [x for x in p["variants"] if x["id"] != vid]
        (PROJECTS / pid / f"{vid}.mp3").unlink(missing_ok=True)
        (PROJECTS / pid / f"{vid}.peaks.json").unlink(missing_ok=True)
        if _ID.match(vid or ""):
            shutil.rmtree(stems_dir(pid, vid), ignore_errors=True)
        save(p)
    return view(p)


@router.post("/api/studio/projects/{pid}/variants/{vid}/separate")
def variant_separate(pid: str, vid: str):
    """Take a finished variant apart into instruments (again, or the first time when it was left out)."""
    with _LOCK:
        p = load(pid)
        v = variant(p, vid) if p else None
        if v is None or v.get("status") != "done":
            return JSONResponse({"error": "Tahle varianta ještě není hotová."}, status_code=404)
        if v.get("stems") in ("queued", "running"):
            return view(p)
        v["stems"] = "queued"
        v.pop("stems_error", None)
        save(p)
    _kick()
    return view(p)


def peaks(path: Path, n: int = 1200) -> list:
    """The loudness outline of a file: n numbers 0–1, the highest swing in each slice."""
    import numpy as np
    raw = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True, timeout=120).stdout
    x = np.abs(np.frombuffer(raw, dtype=np.float32))
    if not len(x):
        return []
    edges = np.linspace(0, len(x), n + 1).astype(int)
    out = [float(x[a:b].max()) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
    return [round(min(1.0, v), 3) for v in out]


@router.get("/api/studio/peaks/{pid}/{vid}")
def variant_peaks(pid: str, vid: str):
    p = load(pid)
    f = variant_file(p, vid) if p else None
    if f is None:
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    cache = PROJECTS / pid / f"{vid}.peaks.json"
    have = stems_of(pid, vid)
    try:
        got = json.loads(cache.read_text(encoding="utf-8"))
        if sorted(got.get("stems", {})) == sorted(have):
            return got
    except (OSError, ValueError):
        pass
    got = {"mix": peaks(f), "stems": {s: peaks(stems_dir(pid, vid) / f"{s}.mp3") for s in have}, "length": _seconds(f)}
    cache.write_text(json.dumps(got), encoding="utf-8")
    return got


@router.get("/api/studio/audio/{pid}/{name}")
def audio(pid: str, name: str):
    d = _dir(pid)
    if d is None or not re.match(r"^(idea\.wav|ref\.wav|[a-f0-9]{10}\.mp3)$", name) or not (d / name).is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(d / name, media_type="audio/wav" if name.endswith(".wav") else "audio/mpeg")


@router.get("/api/studio/audio/{pid}/{vid}/{stem}.mp3")
def audio_stem(pid: str, vid: str, stem: str):
    d = _dir(pid)
    f = d / vid / f"{stem}.mp3" if d is not None and re.match(r"^[a-f0-9]{10}$", vid) and stem in STEMS else None
    if f is None or not f.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(f, media_type="audio/mpeg")


@router.post("/api/studio/stop")
def stop_engine():
    """Puts the model away now (what waits in the queue stays, and starts it again)."""
    with _LOCK:
        if _STATE["now"]:
            return JSONResponse({"error": "Právě se skládá."}, status_code=409)
        engine_stop()
    return {"ok": True}


class LyricsRequest(BaseModel):
    mode: str = "write"            # "write" a whole song · "complete" the player's lines · "polish" them
    about: str = ""                # what it is about: a sentence or a few words
    words: str = ""                # words that must be in it
    text: str = ""                 # what the player has written so far
    lang: str = "cs"
    mood: str = ""
    desc: str = ""                 # the description of the sound, so the text fits it
    length: int = 90
    rhyme: bool = True
    idea: bool = False             # the song begins with the player's own instrumental idea
    structure: str = ""            # the shape to write into — sections, lines, syllables, rhymes (the songbook)


@router.post("/api/studio/lyrics")
def lyrics_write(req: LyricsRequest):
    import lyricist
    if not (req.about.strip() or req.words.strip() or req.text.strip() or req.structure.strip()):
        return JSONResponse({"error": "Napiš aspoň pár slov, o čem má píseň být."}, status_code=400)
    out = lyricist.write(req.model_dump(), LANGS)
    if out.get("error"):
        return JSONResponse(out, status_code=503)
    return out
