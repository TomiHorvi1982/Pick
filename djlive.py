"""Pick DJ — songs played before they were separated.

A song from the library without stems opens at once as its plain mix; the stems
are separated while it plays, window by window ahead of the playhead, by one
long-lived worker (dj_separator.py, htdemucs_6s under Demucs' interpreter).
With nothing to do for the page, the worker goes on through the rest of the
song, and a song separated to the end is handed to Steems the way a finished
Demucs job is (server._finalize_stems_job), so next time it opens with stems.

Everything about a song lives in LIVE_ROOT/<id>/: mix.wav (ffmpeg, 44.1 kHz
16-bit stereo: what the page plays, what the worker separates and what the beat
grid is found on — one decode, so all three agree to the sample) and w<k>.pcm,
the windows as the worker returned them. The three most recently opened songs
are kept; older folders are removed.

The server fills in the hooks below (as it does for jam.py).
"""
import hashlib
import json
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
RATE = 44100
CHANNELS = 2
STEMS = ["vocals", "drums", "bass", "guitar", "piano", "other"]
MAX_SECONDS = 20 * 60          # a DJ deck holds the six stems in memory: ~6 MB per stem and minute
KEEP_SONGS = 3

# ---- filled in by the server
LIVE_ROOT: Path = Path.home() / ".local" / "share" / "spotdl-helper" / "dj-live"
resolve: Callable[[str], Optional[Path]] = lambda rel: None          # a library path, contained
interpreter: Callable[[], Optional[str]] = lambda: None              # Demucs' Python
ffmpeg: Callable[[], Optional[str]] = lambda: shutil.which("ffmpeg")
safe_name: Callable[[str], str] = lambda s: s
stems_ready: Callable[[str], bool] = lambda name: False
stems_busy: Callable[[str], bool] = lambda name: False                # a Steems job for it queued or running
raw_track_dir: Callable[[Path], Path] = lambda source: Path("/nonexistent")
finalize: Callable[[dict], None] = lambda job: None
run_beatgrid: Callable[[Path], object] = lambda path: "Detektor beatů není dostupný."
worker_script: Path = APP_DIR / "dj_separator.py"

router = APIRouter()


# ------------------------------------------------------------------ the worker

class Worker:
    """The separator process: started on first use, restarted after it dies.
    One request at a time; the page's requests go before the background fill."""

    def __init__(self):
        self.proc = None
        self.info = None
        self.lock = threading.Lock()
        self.waiting = 0               # page requests waiting for the lock
        self.count_lock = threading.Lock()

    def _start(self):
        py = interpreter()
        if not py:
            raise RuntimeError("Demucs není nainstalovaný.")
        LIVE_ROOT.mkdir(parents=True, exist_ok=True)
        log = open(LIVE_ROOT / "worker.log", "ab")
        self.proc = subprocess.Popen([py, str(worker_script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=log, cwd=str(APP_DIR))
        log.close()
        line = self.proc.stdout.readline()
        if not line:
            self.proc = None
            raise RuntimeError("Separace se nespustila (podrobnosti v dj-live/worker.log).")
        self.info = json.loads(line)

    def window(self, path: Path, k: int, page: bool) -> tuple:
        """(start, frames, pcm bytes) of window k. page=True jumps the background fill's queue."""
        if page:
            with self.count_lock:
                self.waiting += 1
        try:
            with self.lock:
                for attempt in (0, 1):
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    try:
                        self.proc.stdin.write((json.dumps({"op": "window", "path": str(path), "k": k}) + "\n").encode())
                        self.proc.stdin.flush()
                        head = self.proc.stdout.readline()
                        if not head:
                            raise BrokenPipeError
                        h = json.loads(head)
                        if not h.get("ok"):
                            raise RuntimeError(h.get("error") or "Separace selhala.")
                        data = self.proc.stdout.read(h["bytes"])
                        if len(data) != h["bytes"]:
                            raise BrokenPipeError
                        return h["start"], h["frames"], data
                    except (BrokenPipeError, OSError, ValueError):
                        self.stop()
                        if attempt:
                            raise RuntimeError("Separace se přerušila.")
        finally:
            if page:
                with self.count_lock:
                    self.waiting -= 1

    def page_waiting(self) -> bool:
        with self.count_lock:
            return self.waiting > 0

    def core(self) -> int:
        if self.info is None:
            with self.lock:
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
        return int(self.info["core"])

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(timeout=3)
            except Exception:
                p.kill()


WORKER = Worker()


# ------------------------------------------------------------------ songs on disk

def song_id(source: Path) -> str:
    st = source.stat()
    return hashlib.sha1(f"{source}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def song_dir(sid: str) -> Optional[Path]:
    if not sid.isalnum():
        return None
    d = LIVE_ROOT / sid
    return d if (d / "meta.json").is_file() else None


def read_meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text(encoding="utf-8"))


def write_meta(d: Path, meta: dict) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "meta.json")


def window_count(frames: int, core: int) -> int:
    return (frames + core - 1) // core


def cached_windows(d: Path, count: int) -> list:
    return [k for k in range(count) if (d / f"w{k}.pcm").is_file()]


def prune(keep: str) -> None:
    """Only the most recently opened songs stay (the one being opened always does)."""
    if not LIVE_ROOT.is_dir():
        return
    dirs = [d for d in LIVE_ROOT.iterdir() if d.is_dir() and (d / "meta.json").is_file()]
    dirs.sort(key=lambda d: (d / "meta.json").stat().st_mtime, reverse=True)
    for d in dirs[KEEP_SONGS:]:
        if d.name != keep and d.name not in FILL.active():
            shutil.rmtree(d, ignore_errors=True)


def make_mix(source: Path, target: Path) -> None:
    exe = ffmpeg()
    if not exe:
        raise RuntimeError("ffmpeg není k dispozici.")
    tmp = target.with_suffix(".tmp.wav")
    r = subprocess.run([exe, "-v", "error", "-y", "-i", str(source), "-vn", "-ac", str(CHANNELS), "-ar", str(RATE),
                        "-c:a", "pcm_s16le", "-f", "wav", str(tmp)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Píseň se nepodařilo převést: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    tmp.replace(target)


def wav_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


def compute_window(d: Path, k: int, page: bool) -> bytes:
    """Window k from disk, or separated now (and kept)."""
    f = d / f"w{k}.pcm"
    if f.is_file():
        return f.read_bytes()
    _, _, data = WORKER.window(d / "mix.wav", k, page)
    tmp = d / f"w{k}.pcm.tmp"
    tmp.write_bytes(data)
    tmp.replace(f)
    return data


def assemble(d: Path, meta: dict) -> None:
    """All windows → six MP3s where Demucs would leave them → Steems (the owner's own finalisation)."""
    source = Path(meta["source"])
    name = meta["name"]
    if stems_ready(name) or stems_busy(name):
        return
    exe = ffmpeg()
    if not exe:
        return
    out_dir = raw_track_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    count, frames = meta["windows"], meta["frames"]
    for si, stem in enumerate(STEMS):
        enc = subprocess.Popen([exe, "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
                                "-c:a", "libmp3lame", "-b:a", "320k", str(out_dir / f"{stem}.mp3")],
                               stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for k in range(count):
            n = min(meta["core"], frames - k * meta["core"])
            raw = (d / f"w{k}.pcm").read_bytes()
            plane = n * 2                                   # int16 samples per channel plane, in bytes
            left = raw[(si * 2) * plane:(si * 2 + 1) * plane]
            right = raw[(si * 2 + 1) * plane:(si * 2 + 2) * plane]
            enc.stdin.write(interleave(left, right))
        enc.stdin.close()
        if enc.wait() != 0:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise RuntimeError("Zápis stop selhal: " + enc.stderr.read().decode(errors="replace")[-200:])
    finalize({"stems_source": str(source), "stems_name": name})


def interleave(left: bytes, right: bytes) -> bytes:
    """Two planar int16 channels → interleaved stereo."""
    out = bytearray(len(left) * 2)
    lv, rv, ov = memoryview(left).cast("h"), memoryview(right).cast("h"), memoryview(out).cast("h")
    ov[0::2] = lv
    ov[1::2] = rv
    return bytes(out)


# ------------------------------------------------------------------ background fill

class Filler:
    """Separates opened songs to the end in the worker's spare time, then saves them to Steems."""

    def __init__(self):
        self.queue = []
        self.cond = threading.Condition()
        self.thread = None
        self.current = None
        self.errors = {}

    def add(self, sid: str):
        with self.cond:
            if sid not in self.queue and sid != self.current:
                self.queue.append(sid)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.run, name="dj-live-fill", daemon=True)
                self.thread.start()
            self.cond.notify()

    def active(self) -> set:
        with self.cond:
            return set(self.queue) | ({self.current} if self.current else set())

    def run(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.current = None
                    if not self.cond.wait(timeout=60):
                        self.thread = None
                        return
                self.current = self.queue.pop(0)
            sid = self.current
            try:
                self.fill(sid)
            except Exception as exc:
                self.errors[sid] = str(exc)

    def fill(self, sid: str):
        d = song_dir(sid)
        if d is None:
            return
        meta = read_meta(d)
        for k in range(meta["windows"]):
            if (d / f"w{k}.pcm").is_file():
                continue
            while WORKER.page_waiting():          # the page first
                time.sleep(0.02)
            compute_window(d, k, page=False)
        if not meta.get("saved"):
            assemble(d, meta)
            meta["saved"] = True
            write_meta(d, meta)


FILL = Filler()


def shutdown():
    WORKER.stop()


# ------------------------------------------------------------------ endpoints

class OpenRequest(BaseModel):
    rel: str


@router.post("/api/dj/live/open")
def live_open(req: OpenRequest):
    source = resolve(req.rel)
    if source is None or not source.is_file():
        return JSONResponse({"error": "Píseň se nenašla."}, status_code=404)
    name = safe_name(source.stem)
    if stems_ready(name):
        return {"separated": name}                     # already in Steems: the page loads those
    sid = song_id(source)
    d = LIVE_ROOT / sid
    try:
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "mix.wav").is_file():
            make_mix(source, d / "mix.wav")
        frames = wav_frames(d / "mix.wav")
        if frames > MAX_SECONDS * RATE:
            shutil.rmtree(d, ignore_errors=True)
            return JSONResponse({"error": f"Píseň je delší než {MAX_SECONDS // 60} minut, DJ ji neudrží v paměti."}, status_code=413)
        core = WORKER.core()
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    meta = read_meta(d) if (d / "meta.json").is_file() else {}
    meta.update({"rel": req.rel, "source": str(source), "name": name, "frames": frames, "core": core,
                 "windows": window_count(frames, core), "opened": time.time()})
    write_meta(d, meta)
    prune(sid)
    FILL.add(sid)
    return {"id": sid, "name": name, "frames": frames, "rate": RATE, "core": core, "windows": meta["windows"],
            "stems": STEMS, "cached": cached_windows(d, meta["windows"])}


@router.get("/api/dj/live/{sid}/mix")
def live_mix(sid: str):
    d = song_dir(sid)
    if d is None:
        return JSONResponse({"error": "Neznámá píseň."}, status_code=404)
    return FileResponse(d / "mix.wav", media_type="audio/wav")


@router.get("/api/dj/live/{sid}/window/{k}")
def live_window(sid: str, k: int):
    d = song_dir(sid)
    if d is None:
        return JSONResponse({"error": "Neznámá píseň."}, status_code=404)
    meta = read_meta(d)
    if not 0 <= k < meta["windows"]:
        return JSONResponse({"error": "Mimo píseň."}, status_code=416)
    try:
        data = compute_window(d, k, page=True)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    frames = min(meta["core"], meta["frames"] - k * meta["core"])
    return Response(data, media_type="application/octet-stream",
                    headers={"X-DJ-Start": str(k * meta["core"]), "X-DJ-Frames": str(frames), "Cache-Control": "no-store"})


@router.get("/api/dj/live/{sid}/beats")
def live_beats(sid: str):
    d = song_dir(sid)
    if d is None:
        return JSONResponse({"error": "Neznámá píseň."}, status_code=404)
    cache = LIVE_ROOT / "beats" / f"{sid}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    grid = run_beatgrid(d / "mix.wav")
    if isinstance(grid, str):
        return JSONResponse({"error": grid}, status_code=503)
    if grid is None:
        return JSONResponse({"error": "Beaty se nepodařilo detekovat."}, status_code=422)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(grid), encoding="utf-8")
    return grid


@router.get("/api/dj/live/{sid}/status")
def live_status(sid: str):
    d = song_dir(sid)
    if d is None:
        return JSONResponse({"error": "Neznámá píseň."}, status_code=404)
    meta = read_meta(d)
    return {"windows": meta["windows"], "cached": len(cached_windows(d, meta["windows"])),
            "saved": bool(meta.get("saved")), "error": FILL.errors.get(sid)}
