"""Skladatel — a song out of an idea.

The player brings a piece of guitar (or nothing but a wish), picks a few
styles, and gets whole songs to choose from: the idea stays as it was played
and a band takes it on from where it ends, in its tempo and harmony — once per
style and variant. With a text, the song is sung.

The music is made by ACE-Step 1.5 (github.com/ace-step/ACE-Step-1.5), an open
model that runs on this computer: its own environment and about 10 GB of
weights beside the app's settings, started as its own small server only while
something is being made and put away again after a while of nothing to do
(it holds several GB of memory). Measured on an M1 Pro: a minute of music
takes a little over a minute and a half, and waking the model two more.

What is made is kept per project — the idea, each variant as an MP3, what was
asked — so the choosing can wait. Putting a chosen variant into the library,
and having it sung in the player's own voice, is the server's part (server.py).
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
from typing import List, Optional

import httpx
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

HOME = Path.home()
STUDIO_DIR = HOME / ".local" / "share" / "spotdl-helper" / "studio"
ACE_DIR = STUDIO_DIR / "ACE-Step-1.5"
PROJECTS = STUDIO_DIR / "projects"
ENGINE_PID = STUDIO_DIR / "engine.pid"
ENGINE_LOG = STUDIO_DIR / "engine.log"
PORT = int(os.environ.get("PICK_STUDIO_PORT") or 8795)
URL = f"http://127.0.0.1:{PORT}"
IDLE = 180                     # seconds with nothing to make before the model is put away (it crowds the memory)
TURBO_MODEL = "acestep-v15-turbo"
BASE_MODEL = "acestep-v15-base"        # slower; the only one that adds instruments under what is played
COLD = 130                     # seconds the model takes to wake up
IDEA_MAX = 60.0                # of the idea, this much is used
LENGTHS = (45, 60, 90, 120, 180)
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

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
LANGS = {"cs": "česky", "en": "anglicky", "sk": "slovensky", "de": "německy", "es": "španělsky"}

router = APIRouter()
_LOCK = threading.RLock()
_STATE = {"proc": None, "last": 0.0, "worker": None, "now": None, "model": None}


# ------------------------------------------------------------------ engine

def ready() -> bool:
    return (ACE_DIR / ".venv" / "bin" / "acestep-api").is_file()


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


def engine_start(wait: float = 180.0, model: str = "") -> bool:
    model = model or TURBO_MODEL
    with _LOCK:
        if engine_up():
            return True
        if not ready():
            return False
        _free_memory()
        STUDIO_DIR.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "ACESTEP_LM_BACKEND": "mlx", "TOKENIZERS_PARALLELISM": "false",
               "ACESTEP_INIT_LLM": "false", "ACESTEP_CONFIG_PATH": model}                # the planning model stays out: twice the memory for little gain here
        log = open(ENGINE_LOG, "ab")
        proc = subprocess.Popen([str(ACE_DIR / ".venv" / "bin" / "acestep-api"), "--host", "127.0.0.1", "--port", str(PORT)],
                                cwd=str(ACE_DIR), env=env, stdout=log, stderr=log, start_new_session=True)
        _STATE["proc"] = proc
        _STATE["model"] = model
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


def under_ready() -> bool:
    return (ACE_DIR / "checkpoints" / BASE_MODEL).is_dir()


def estimate(length: float, mode: str = "after", idea_len: float = 0.0) -> float:
    if mode == "under" and idea_len:
        # drums, then bass, each about 3.3 × the idea; then the other model is woken for the rest of the song
        rest = COLD + 15 + length * 1.6 if length >= idea_len + 15 else 0
        return 30 + idea_len * 6.8 + rest
    return 15 + length * 1.6


# -------------------------------------------------------------------- making

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


def payload(p: dict, v: dict) -> dict:
    sung = bool((p.get("lyrics") or "").strip())
    caption = v["caption"] + (", full band arrangement" if p.get("idea") else "") + \
        (", expressive lead vocals" if sung else ", instrumental, no vocals")
    data = {"prompt": caption, "lyrics": lyrics_for(p.get("lyrics") or "", bool(p.get("idea"))),
            "vocal_language": p.get("lang") or "en", "audio_duration": v["length"], "inference_steps": 8, "batch_size": 1,
            "thinking": "false", "use_random_seed": "true", "audio_format": "mp3"}
    if p.get("idea"):
        # the idea is kept as played and the song goes on from its end
        data.update(task_type="repaint", repainting_start=round(p["idea_len"], 2), repainting_end=v["length"],
                    chunk_mask_mode="explicit")
    return data


def _ace(data: dict, src: Optional[Path] = None) -> bytes:
    """One piece of music asked of the model and waited for. → the MP3."""
    with httpx.Client(timeout=120.0) as c:
        if src is not None:
            with open(src, "rb") as f:
                r = c.post(URL + "/release_task", data={k: str(x) for k, x in data.items()}, files={"src_audio": (src.name, f, "audio/wav")})
        else:
            r = c.post(URL + "/release_task", json={**data, "thinking": False, "use_random_seed": True})
        r.raise_for_status()
        task = json.loads(r.text, strict=False)["data"]["task_id"]
        end = time.time() + 3600
        while time.time() < end:
            time.sleep(3.0)
            q = c.post(URL + "/query_result", json={"task_id_list": [task]})
            row = json.loads(q.text, strict=False)["data"][0]
            if row.get("status") == 0:
                continue
            res = (json.loads(row.get("result") or "[]", strict=False) or [{}])[0]
            if row.get("status") != 1 or not res.get("file"):
                raise RuntimeError(res.get("error") or "model skončil bez výsledku")
            audio = c.get(URL + res["file"])
            audio.raise_for_status()
            return audio.content
    raise RuntimeError("trvalo to moc dlouho")


def engine_model(name: str) -> None:
    """The model the engine works with: the quick one for whole songs, the big one for adding instruments.
    One at a time — two do not fit into this computer's memory — so changing it means starting the engine again."""
    if _STATE.get("model") == name and engine_up():
        return
    engine_stop()
    time.sleep(2.0)
    if not engine_start(model=name):
        raise RuntimeError(f"model {name} se nepodařilo spustit")


# ---- "kapela pod kytarou": the band plays with the idea from its first note.
# The big model adds one instrument at a time to what it hears — the drums to
# the guitar, then the bass to guitar and drums, so each locks to the one
# before — and gives back that instrument alone; the three are mixed here,
# the guitar on top. A song longer than the idea is then carried on from that
# mix by the quick model, which now hears a whole band to go on with.
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


def _lego(src: Path, track: str, caption: str, steps: int = 24) -> bytes:
    return _ace({"task_type": "lego", "instruction": f"Generate the {track.upper()} track based on the audio context:",
                 "prompt": caption, "lyrics": "[Instrumental]", "inference_steps": steps, "guidance_scale": 7,
                 "batch_size": 1, "thinking": "false", "audio_format": "mp3"}, src)


def _make_under(p: dict, v: dict) -> None:
    d = PROJECTS / p["id"]
    tmp = d / f"{v['id']}.work"
    tmp.mkdir(exist_ok=True)
    try:
        engine_model(BASE_MODEL)
        idea = _pcm(d / "idea.wav")
        v["stage"] = "drums"; save(p)
        (tmp / "drums.mp3").write_bytes(_lego(d / "idea.wav", "drums", v["caption"] + ", live drum kit, kick, snare, hi-hat, cymbals"))
        drums = _pcm(tmp / "drums.mp3")
        _wav(mix_band([(idea, 1.0), (drums, 1.0)]), tmp / "with-drums.wav")
        v["stage"] = "bass"; save(p)
        (tmp / "bass.mp3").write_bytes(_lego(tmp / "with-drums.wav", "bass", v["caption"] + ", bass guitar following the chords"))
        band = mix_band([(idea, 1.0), (drums, 1.1), (_pcm(tmp / "bass.mp3"), 0.8)])
        _wav(band, tmp / "band.wav")
        if v["length"] >= p["idea_len"] + 15:
            v["stage"] = "song"; save(p)
            engine_model(TURBO_MODEL)
            (tmp / "song.mp3").write_bytes(_ace(payload(p, v), tmp / "band.wav"))
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
        v.pop("stage", None)


def _make(p: dict, v: dict) -> None:
    d = PROJECTS / p["id"]
    if v.get("mode") == "under" and p.get("idea"):
        _make_under(p, v)
        return
    engine_model(TURBO_MODEL)
    (d / f"{v['id']}.mp3").write_bytes(_ace(payload(p, v), d / "idea.wav" if p.get("idea") else None))


def _work() -> None:
    """One variant after another, oldest first; the model is put away when none is left for a while."""
    while True:
        job = None
        with _LOCK:
            for p in sorted(projects(), key=lambda p: p.get("created") or 0):
                v = next((v for v in p.get("variants", []) if v.get("status") == "queued"), None)
                if v:
                    job = (p, v)
                    break
            if job is None:
                if time.time() - _STATE["last"] > IDLE:
                    _STATE["worker"] = None
                    engine_stop()
                    return
        if job is None:
            time.sleep(2.0)
            continue
        p, v = job
        want = BASE_MODEL if v.get("mode") == "under" else TURBO_MODEL
        v.update(status="running", started=time.time(), cold=not (engine_up() and _STATE.get("model") == want))
        _STATE["now"] = (p["id"], v["id"])
        save(p)
        try:
            _make(p, v)                                     # starts the engine with the model the variant needs
            if v.get("status") != "stopped":
                v.update(status="done", took=time.time() - v["started"])
        except Exception as e:                             # noqa: BLE001 — one failed variant must not stop the rest
            v.update(status="error", error=str(e)[:200])
        _STATE["now"] = None
        _STATE["last"] = time.time()
        fresh = load(p["id"])                               # the project may have been changed meanwhile (a variant removed…)
        if fresh is not None:
            for i, x in enumerate(fresh.get("variants", [])):
                if x["id"] == v["id"]:
                    fresh["variants"][i] = v
            save(fresh)


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
                found = True
            found = found or v.get("status") == "queued"
        save(p)
    if found and ready():
        _kick()
    else:
        engine_stop()                                       # a model left running by the server before this one


# ---------------------------------------------------------------------- API

def view(p: dict) -> dict:
    out = {k: p.get(k) for k in ("id", "name", "created", "idea", "idea_len", "lyrics", "lang")}
    out["variants"] = []
    for v in p.get("variants", []):
        row = {k: v.get(k) for k in ("id", "style", "name", "length", "status", "error", "took", "used", "sung", "mode")}
        if v.get("status") == "running":
            row["elapsed"] = time.time() - (v.get("started") or time.time())
            row["estimate"] = estimate(v["length"], v.get("mode") or "after", p.get("idea_len") or 0) + (COLD if v.get("cold") else 0)
            row["stage"] = v.get("stage")
        if v.get("status") == "done":
            row["url"] = f"/api/studio/audio/{p['id']}/{v['id']}.mp3"
        out["variants"].append(row)
    if p.get("idea"):
        out["idea_url"] = f"/api/studio/audio/{p['id']}/idea.wav"
    return out


@router.get("/api/studio")
def status():
    waiting = sum(1 for p in projects() for v in p.get("variants", []) if v.get("status") in ("queued", "running"))
    return {"ready": ready(), "under": under_ready(), "engine": engine_up(), "styles": [{"id": s["id"], "name": s["name"]} for s in STYLES],
            "lengths": list(LENGTHS), "langs": LANGS, "waiting": waiting, "projects": [view(p) for p in projects()]}


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


class MakeRequest(BaseModel):
    styles: List[str] = []
    custom: str = ""               # a style in the player's own words (English works best)
    per: int = 1                   # variants of each style
    length: int = 90
    lyrics: str = ""
    lang: str = "cs"
    mode: str = "after"            # "after": the band comes in when the idea ends · "under": it plays with it from the start


@router.post("/api/studio/projects/{pid}/make")
def project_make(pid: str, req: MakeRequest):
    if not ready():
        return JSONResponse({"error": "Model pro skládání není nainstalovaný."}, status_code=503)
    with _LOCK:
        p = load(pid)
        if p is None:
            return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
        wanted = [dict(_STYLE[s]) for s in req.styles if s in _STYLE]
        custom = " ".join((req.custom or "").split())[:300]
        if custom:
            wanted.append({"id": "custom", "name": custom[:40], "caption": custom})
        if not wanted:
            return JSONResponse({"error": "Vyber aspoň jeden styl."}, status_code=400)
        length = min(LENGTHS, key=lambda x: abs(x - int(req.length)))
        mode = "under" if req.mode == "under" and p.get("idea") else "after"
        # after the idea there has to be room for the band; under it, the idea's own length is a song too
        if p.get("idea") and mode == "after" and length < p["idea_len"] + 20:
            length = next((x for x in LENGTHS if x >= p["idea_len"] + 20), LENGTHS[-1])
        per = max(1, min(int(req.per), 4))
        if mode == "under" and not under_ready():
            return JSONResponse({"error": "Model pro kapelu pod kytarou není stažený."}, status_code=503)
        if len(wanted) * per + sum(1 for v in p["variants"] if v.get("status") in ("queued", "running")) > 16:
            return JSONResponse({"error": "To je moc najednou — nejvýš 16 variant ve frontě."}, status_code=400)
        p["lyrics"], p["lang"] = (req.lyrics or "").strip()[:4000], req.lang if req.lang in LANGS else "cs"
        n = len(p["variants"])
        for s in wanted:
            for k in range(per):
                n += 1
                p["variants"].append({"id": uuid.uuid4().hex[:10], "style": s["id"], "name": s["name"] + (f" {k + 1}" if per > 1 else ""),
                                      "caption": s["caption"], "length": length, "status": "queued", "n": n, "mode": mode})
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
                return JSONResponse({"error": "Tenhle projekt se právě skládá — počkej, až doběhne."}, status_code=409)
            shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@router.delete("/api/studio/projects/{pid}/variants/{vid}")
def variant_delete(pid: str, vid: str):
    with _LOCK:
        p = load(pid)
        if p is None:
            return JSONResponse({"error": "Takový projekt tu není."}, status_code=404)
        v = variant(p, vid)
        if v and v.get("status") == "running":
            return JSONResponse({"error": "Tahle varianta se právě skládá."}, status_code=409)
        p["variants"] = [x for x in p["variants"] if x["id"] != vid]
        (PROJECTS / pid / f"{vid}.mp3").unlink(missing_ok=True)
        save(p)
    return view(p)


@router.get("/api/studio/audio/{pid}/{name}")
def audio(pid: str, name: str):
    d = _dir(pid)
    if d is None or not re.match(r"^(idea\.wav|[a-f0-9]{10}\.mp3)$", name) or not (d / name).is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    return FileResponse(d / name, media_type="audio/wav" if name.endswith(".wav") else "audio/mpeg")


@router.post("/api/studio/stop")
def stop_engine():
    """Puts the model away now (what waits in the queue stays, and starts it again)."""
    with _LOCK:
        if _STATE["now"]:
            return JSONResponse({"error": "Právě se skládá."}, status_code=409)
        engine_stop()
    return {"ok": True}
