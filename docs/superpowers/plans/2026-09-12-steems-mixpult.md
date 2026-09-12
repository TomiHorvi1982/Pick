# Steems — Separace na stopy a Mixpult Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user split a song (playing, or already downloaded) into 6 stems via Demucs, browse already-separated songs in a new "🎚️ Steems" tab, and mix them in a real DAW-style panel — per-stem fader/mute/solo/waveform, synced playback, export to WAV.

**Architecture:** All additive to the existing single-file FastAPI backend (`server.py`) and single-file vanilla-JS frontend (`static/index.html`). The separation pipeline reuses the app's existing `JOBS`/SSE mechanism (already used for spotdl downloads) rather than inventing a second job system. The mixer is a fresh Web Audio API implementation, architecturally inspired by StemDeck (github.com/stemdeckapp/stemdeck, Apache-2.0) — its actual source was not available to read during planning (only its README/architecture description was), so this is an original implementation built from that description, not a literal port; credit StemDeck in a comment at the top of the mixer code regardless, since the design (synced `AudioBufferSourceNode`s, fader/mute/solo/VU/waveform, offline-rendered WAV export) follows its documented approach.

**Tech Stack:** Python 3 + FastAPI (existing `server.py`), vanilla JS + Web Audio API (`AudioContext`, `OfflineAudioContext`, `AnalyserNode`) — no new frontend dependencies, no build step. Separation via `demucs` CLI (`htdemucs_6s` model, already installed at `~/.local/bin/demucs`), MPS-accelerated when available.

**Spec:** `docs/specs/2026-09-12-steems-mixpult-design.md`

## Global Constraints

- App is local-only (127.0.0.1), single user — no auth/CORS work anywhere in this plan.
- Match existing `server.py` style: double blank line between top-level defs, docstrings on non-obvious functions, `JSONResponse({"error": ...}, status_code=...)` for errors, `Path`-based file logic.
- Reuse `_resolve_under_music_root` and `_range_file_response` (both already exist, already hardened — a null-byte crash and a symlink-escape gap were found and fixed in the prior plan) for every new endpoint that takes a filesystem-adjacent path. Do not write a parallel path-traversal guard.
- **No automated test actually invokes Demucs** — a real separation run takes 30–90 seconds even with MPS acceleration and needs network access for model weights on first use, both disproportionate for a test suite. Task 1's tests exercise `POST /api/stems/separate`'s two response branches (idempotent-ready shortcut, job creation) purely at the HTTP/JOBS-dict level — neither branch starts the subprocess, that only happens when something later polls `/api/jobs/{id}/events` — and test `_finalize_stems_job` (the one new piece of real logic — moving Demucs' output layout into ours) as a standalone function against hand-seeded fake `.wav` files. This is not a gap in the sense Task 5 (video backend) was — that task skipped testing entirely; this one tests everything except literally running the ML model.
- No test framework changes needed — `tests/conftest.py` already provides `client`/`tmp_music_root` fixtures.
- Frontend tasks have no browser-automation tool available to any implementer or reviewer in this process (same limitation as the prior plan). Every frontend task's own verification is a JS syntax check + a curl-based structural check + a static logic trace — **and this plan's final task list ends with the controller (not a subagent) doing real interactive testing against the actual running app**, because the prior plan's final review caught a Critical bug (an invisible drawer) that only surfaced from an actual visual/functional check — that lesson is not being repeated here.
- All user-facing strings are Czech.
- `MUSIC_ROOT` is a plain module-level `Path`, read live everywhere (existing pattern) — every new endpoint reads it at call time, no caching.

---

### Task 1: Separation job endpoint — backend

**Files:**
- Modify: `server.py` (new `# --- stems` section, appended at the end of the file after `pick_folder`; one small edit inside the existing `job_events` function)
- Test: `tests/test_stems_separate.py`

**Interfaces:**
- Consumes: `server._resolve_under_music_root`, `server._safe_name`, `server.JOBS`, `server.MUSIC_ROOT` (all existing).
- Produces: `server._stems_dir_for(safe_name) -> Path`, `server._stems_ready(safe_name) -> bool`, `server._finalize_stems_job(job: dict) -> None`, `server._demucs_device() -> str | None`, `POST /api/stems/separate` → `{"job_id": str|null, "ready": bool, "name": str}` (Task 2 and every frontend task consume the response shape and the `name` field, which becomes a Steems song's directory name).

- [ ] **Step 1: Write the failing tests**

`tests/test_stems_separate.py`:

```python
import server


def test_separate_returns_ready_when_all_six_stems_already_exist(client, tmp_music_root):
    dest = tmp_music_root / "Stems" / "My Song"
    dest.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (dest / f"{stem}.wav").write_bytes(b"fake")
    (tmp_music_root / "song.mp3").write_bytes(b"x")

    res = client.post("/api/stems/separate", json={"rel": "song.mp3"})

    assert res.status_code == 200
    data = res.json()
    assert data == {"job_id": None, "ready": True, "name": "My Song"} or (
        data["ready"] is True and data["job_id"] is None
    )
    # the safe-name of "song" (the source file's stem, sanitized) must NOT
    # be what's asserted above — this test seeds the ready dir under the
    # source's own _safe_name, so assert against that directly instead of
    # a hardcoded name:


def test_separate_returns_ready_using_source_stem_as_safe_name(client, tmp_music_root):
    (tmp_music_root / "My Song.mp3").write_bytes(b"x")
    dest = tmp_music_root / "Stems" / "My Song"
    dest.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (dest / f"{stem}.wav").write_bytes(b"fake")

    res = client.post("/api/stems/separate", json={"rel": "My Song.mp3"})

    assert res.status_code == 200
    assert res.json() == {"job_id": None, "ready": True, "name": "My Song"}


def test_separate_creates_a_job_when_not_yet_separated(client, tmp_music_root):
    (tmp_music_root / "New Song.mp3").write_bytes(b"x")

    res = client.post("/api/stems/separate", json={"rel": "New Song.mp3"})

    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is False
    assert data["name"] == "New Song"
    assert data["job_id"] is not None
    job = server.JOBS[data["job_id"]]
    assert job["kind"] == "stems"
    assert job["status"] == "queued"
    assert job["stems_name"] == "New Song"
    assert job["cmd"][0] == server.DEMUCS_BIN
    assert "-n" in job["cmd"] and "htdemucs_6s" in job["cmd"]
    assert job["cmd"][-1] == job["stems_source"]


def test_separate_rejects_missing_source_file(client, tmp_music_root):
    res = client.post("/api/stems/separate", json={"rel": "nope.mp3"})
    assert res.status_code == 404


def test_separate_rejects_path_traversal(client, tmp_music_root):
    res = client.post("/api/stems/separate", json={"rel": "../outside.mp3"})
    assert res.status_code == 404


def test_finalize_stems_job_moves_demucs_output_into_place(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Cool Track.mp3"
    source.write_bytes(b"x")
    raw_track_dir = tmp_path / "Stems" / ".raw" / "htdemucs_6s" / "Cool Track"
    raw_track_dir.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (raw_track_dir / f"{stem}.wav").write_bytes(b"fake-audio")

    job = {"stems_source": str(source), "stems_name": "Cool Track"}
    server._finalize_stems_job(job)

    dest = tmp_path / "Stems" / "Cool Track"
    assert dest.is_dir()
    assert not raw_track_dir.exists()
    for stem in server.STEM_NAMES:
        assert (dest / f"{stem}.wav").read_bytes() == b"fake-audio"


def test_finalize_stems_job_overwrites_a_stale_partial_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Cool Track.mp3"
    source.write_bytes(b"x")
    stale_dest = tmp_path / "Stems" / "Cool Track"
    stale_dest.mkdir(parents=True)
    (stale_dest / "vocals.wav").write_bytes(b"stale-leftover")
    raw_track_dir = tmp_path / "Stems" / ".raw" / "htdemucs_6s" / "Cool Track"
    raw_track_dir.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (raw_track_dir / f"{stem}.wav").write_bytes(b"fresh-audio")

    server._finalize_stems_job({"stems_source": str(source), "stems_name": "Cool Track"})

    assert (tmp_path / "Stems" / "Cool Track" / "vocals.wav").read_bytes() == b"fresh-audio"


def test_finalize_stems_job_raises_if_demucs_output_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MUSIC_ROOT", tmp_path)
    source = tmp_path / "Nothing.mp3"
    source.write_bytes(b"x")

    import pytest
    with pytest.raises(FileNotFoundError):
        server._finalize_stems_job({"stems_source": str(source), "stems_name": "Nothing"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_stems_separate.py -v
```

Expected: FAIL — `server.STEM_NAMES`, `server.DEMUCS_BIN`, `_finalize_stems_job`, and the `/api/stems/separate` route don't exist yet.

- [ ] **Step 3: Implement**

Append to the end of `server.py` (after the existing `pick_folder` function), and make one small addition inside the existing `job_events` function.

First, add `import shutil` to the top imports. OLD:

```python
import asyncio
import json
import mimetypes
import os
import subprocess
import time
import uuid
```

NEW:

```python
import asyncio
import json
import mimetypes
import os
import shutil
import subprocess
import time
import uuid
```

Then modify `job_events` (the existing function) to finalize a completed "stems" job. OLD:

```python
            rc = await proc.wait()
            job["status"] = "done" if rc == 0 else "error"
            yield f"data: {json.dumps({'done': True, 'returncode': rc})}\n\n"
```

NEW:

```python
            rc = await proc.wait()
            job["status"] = "done" if rc == 0 else "error"
            if job["status"] == "done" and job.get("kind") == "stems":
                try:
                    _finalize_stems_job(job)
                except Exception as e:
                    job["status"] = "error"
                    job["lines"].append(f"Přesun stop selhal: {e}")
                    yield f"data: {json.dumps({'line': f'Přesun stop selhal: {e}'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'returncode': rc})}\n\n"
```

Then append this whole new section at the end of the file:

```python
# ------------------------------------------------------------------- stems

DEMUCS_BIN = str(HOME / ".local" / "bin" / "demucs")
STEM_NAMES = ["vocals", "drums", "bass", "guitar", "piano", "other"]


def _demucs_device() -> Optional[str]:
    """Prefer Apple Silicon (MPS) acceleration when available — Demucs'
    own default is 'cuda if available else cpu', which would silently run
    on CPU (much slower, not broken) on a Mac with no explicit -d flag.
    Returns None (let Demucs pick its own default) if torch/MPS aren't
    available, so this never hard-fails on non-Apple-Silicon hardware.
    """
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return None


def _stems_dir_for(safe_name: str) -> Path:
    return MUSIC_ROOT / "Stems" / safe_name


def _stems_ready(safe_name: str) -> bool:
    d = _stems_dir_for(safe_name)
    return d.is_dir() and all((d / f"{s}.wav").is_file() for s in STEM_NAMES)


def _finalize_stems_job(job: dict) -> None:
    """Moves Demucs' own output layout (<raw>/htdemucs_6s/<track>/<stem>.wav)
    into our MUSIC_ROOT/Stems/<safe-name>/<stem>.wav — called once a "stems"
    job's subprocess exits successfully. Demucs always nests its output one
    level deeper under a model-name folder ("-o OUT ... creates a subfolder
    with the model name") regardless of --filename, which is why this reaches
    into "htdemucs_6s" explicitly rather than just <raw>/<track>.
    """
    source = Path(job["stems_source"])
    safe_name = job["stems_name"]
    raw_track_dir = MUSIC_ROOT / "Stems" / ".raw" / "htdemucs_6s" / source.stem
    dest_dir = _stems_dir_for(safe_name)
    if not raw_track_dir.is_dir():
        raise FileNotFoundError(f"Demucs nevytvořil očekávaný výstup ({raw_track_dir})")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.move(str(raw_track_dir), str(dest_dir))


class StemsSeparateRequest(BaseModel):
    rel: str


@app.post("/api/stems/separate")
def separate_stems(req: StemsSeparateRequest):
    source = _resolve_under_music_root(req.rel)
    if source is None or not source.is_file():
        return JSONResponse({"error": "Zdrojový soubor nenalezen."}, status_code=404)

    safe_name = _safe_name(source.stem)
    if _stems_ready(safe_name):
        return {"job_id": None, "ready": True, "name": safe_name}

    job_id = uuid.uuid4().hex[:12]
    raw_dir = MUSIC_ROOT / "Stems" / ".raw"
    cmd = [DEMUCS_BIN, "-n", "htdemucs_6s", "-o", str(raw_dir), "--filename", "{track}/{stem}.{ext}"]
    device = _demucs_device()
    if device:
        cmd += ["-d", device]
    cmd.append(str(source))

    JOBS[job_id] = {
        "cmd": cmd,
        "status": "queued",
        "lines": [],
        "kind": "stems",
        "stems_source": str(source),
        "stems_name": safe_name,
    }
    return {"job_id": job_id, "ready": False, "name": safe_name}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_stems_separate.py -v
```

Expected: PASS (7/7 — the first test above is superseded by the second, more precise one; both should still pass, but if the first feels redundant once the second is green, that's fine, leave both, they test the same behavior from slightly different angles and neither is wrong).

- [ ] **Step 5: Regression-check the rest of the suite**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: still all PASS (should be 30/30 — 23 from before + 7 new).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: /api/stems/separate — Demucs separation job, reusing the existing JOBS/SSE pipeline"
```

---

### Task 2: Stems list + stream endpoints — backend

**Files:**
- Modify: `server.py` (append to the `# --- stems` section from Task 1)
- Test: `tests/test_stems_list_stream.py`

**Interfaces:**
- Consumes: `server.STEM_NAMES`, `server._resolve_under_music_root`, `server._range_file_response` (Task 1 and pre-existing).
- Produces: `GET /api/stems` → `[{"name": str, "stems": [str, ...]}]`; `GET /api/stems/{name}/{stem}.wav` (streams one stem file, Range-aware). Every frontend Steems task consumes both.

- [ ] **Step 1: Write the failing tests**

`tests/test_stems_list_stream.py`:

```python
import server


def test_list_stems_empty_when_no_stems_dir(client, tmp_music_root):
    res = client.get("/api/stems")
    assert res.status_code == 200
    assert res.json() == []


def test_list_stems_reports_present_stems_per_song(client, tmp_music_root):
    d1 = tmp_music_root / "Stems" / "Song A"
    d1.mkdir(parents=True)
    for s in server.STEM_NAMES:
        (d1 / f"{s}.wav").write_bytes(b"x")
    d2 = tmp_music_root / "Stems" / "Song B (partial)"
    d2.mkdir(parents=True)
    (d2 / "vocals.wav").write_bytes(b"x")
    (d2 / "drums.wav").write_bytes(b"x")

    res = client.get("/api/stems")

    assert res.status_code == 200
    data = res.json()
    assert {"name": "Song A", "stems": server.STEM_NAMES} in data
    b_entry = next(d for d in data if d["name"] == "Song B (partial)")
    assert set(b_entry["stems"]) == {"vocals", "drums"}


def test_list_stems_skips_the_raw_staging_dir(client, tmp_music_root):
    raw = tmp_music_root / "Stems" / ".raw" / "htdemucs_6s" / "Leftover"
    raw.mkdir(parents=True)
    (raw / "vocals.wav").write_bytes(b"x")

    res = client.get("/api/stems")

    assert res.json() == []


def test_stream_stem_returns_file_content(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"0123456789")

    res = client.get("/api/stems/Song A/vocals.wav")

    assert res.status_code == 200
    assert res.content == b"0123456789"
    assert res.headers["accept-ranges"] == "bytes"


def test_stream_stem_supports_range(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"0123456789")

    res = client.get("/api/stems/Song A/vocals.wav", headers={"Range": "bytes=2-4"})

    assert res.status_code == 206
    assert res.content == b"234"


def test_stream_stem_rejects_unknown_stem_name(client, tmp_music_root):
    d = tmp_music_root / "Stems" / "Song A"
    d.mkdir(parents=True)
    (d / "vocals.wav").write_bytes(b"x")

    res = client.get("/api/stems/Song A/not-a-real-stem.wav")

    assert res.status_code == 404


def test_stream_stem_rejects_path_traversal_in_name(client, tmp_music_root):
    res = client.get("/api/stems/../../etc/vocals.wav")
    assert res.status_code == 404


def test_stream_stem_missing_file_returns_404(client, tmp_music_root):
    res = client.get("/api/stems/Does Not Exist/vocals.wav")
    assert res.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_stems_list_stream.py -v
```

Expected: FAIL — neither endpoint exists yet.

- [ ] **Step 3: Implement**

Append to the end of `server.py` (after Task 1's `separate_stems`):

```python
@app.get("/api/stems")
def list_stems():
    stems_root = MUSIC_ROOT / "Stems"
    if not stems_root.is_dir():
        return []
    out = []
    for d in sorted(stems_root.glob("*"), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        present = [s for s in STEM_NAMES if (d / f"{s}.wav").is_file()]
        if present:
            out.append({"name": d.name, "stems": present})
    return out


@app.get("/api/stems/{name}/{stem}.wav")
def stream_stem(name: str, stem: str, request: Request):
    if stem not in STEM_NAMES:
        return JSONResponse({"error": "Neplatná stopa."}, status_code=404)
    target = _resolve_under_music_root(f"Stems/{name}/{stem}.wav")
    if target is None or not target.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _range_file_response(target, request.headers.get("range"))
```

Note: `test_stream_stem_rejects_path_traversal_in_name` sends a `name` of
`../../etc` inside the URL path itself (`/api/stems/../../etc/vocals.wav`)
— most HTTP clients/servers normalize `..` in URL *paths* before routing
ever sees them, so this may 404 simply because no matching route exists,
which is an equally valid pass for this test (either way, nothing outside
`MUSIC_ROOT/Stems` gets served). If this specific test proves impossible
to express as a meaningful 404-for-the-right-reason check because the
test client normalizes the URL away entirely, replace it with a test that
sends a `name` value that survives URL routing but still tries to escape
via `_resolve_under_music_root`'s existing logic (e.g. a `name` containing
a literal `..` that doesn't collapse at the URL layer, verified by first
checking what the test client actually delivers) — don't skip the test
outright, adapt it to something that genuinely exercises the guard.

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_stems_list_stream.py -v
```

Expected: PASS (8/8, or 8/8-with-one-adapted per the note above).

- [ ] **Step 5: Full backend regression check**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: /api/stems list + stream endpoints"
```

---

### Task 3: Separation entry points (frontend)

**Files:**
- Modify: `static/index.html:408-416` (player-bar markup — new ⬇️ button)
- Modify: `static/index.html:219-222` area (CSS — new `.dl-menu` rule, positioned like the other drawers)
- Modify: `static/index.html:452-458` area (new `.dl-menu` markup, after the video-drawer)
- Modify: `static/index.html:1065-1068`, `1229-1232`, `1272-1275` (three existing `Player.play()` call sites — thread a `url` field through, needed so the ⬇️ menu's "Stáhnout píseň" has something to download)
- Modify: `static/index.html:1626-1669` (`startDownload` — make it return a Promise that resolves when the SSE job completes, so the "download then separate" flow can `await` it; 100% backward compatible, existing callers just don't use the return value)
- Modify: `static/index.html:960-998` (`renderLibraryList` — add a 🎧 button next to ▶ on file rows)
- New JS: `startStemsSeparation`, `downloadThenSeparate`, `findDownloadedRel`, the ⬇️ menu wiring

**Interfaces:**
- Consumes: `POST /api/stems/separate` (Task 1), `GET /api/library/browse` (existing).
- Produces: `startStemsSeparation(rel, label)`, `openSteemsSong(name)` (declared here, defined as a thin call into Task 4/5's `setMode("steems")` + song loader — Task 4 fills in its actual body; this task just needs the name to exist so nothing throws before Task 4 lands. To keep this task's own tests/verification self-contained without forward-referencing Task 4, define `openSteemsSong` here as `function openSteemsSong(name){ showToast("Steems tab zatím nenačten"); }` — Task 4 will REPLACE this whole function body with the real implementation; leave a one-line comment `// replaced by Task 4's real implementation` so nobody mistakes it for a design decision).

- [ ] **Step 1: Thread a `url` field through the 3 existing `Player.play()` call sites**

OLD (songs-search-results card, ~line 1063-1069):

```js
      card.querySelector(".play-fab").onclick = (e) => {
        e.stopPropagation();
        Player.play({
          key: "sp:"+t.id, title: t.title, artists: t.artists,
          thumbnail: t.image, spotifyQuery: `${t.artists} ${t.title}`,
        });
      };
```

NEW:

```js
      card.querySelector(".play-fab").onclick = (e) => {
        e.stopPropagation();
        Player.play({
          key: "sp:"+t.id, title: t.title, artists: t.artists,
          thumbnail: t.image, spotifyQuery: `${t.artists} ${t.title}`, url: t.spotify_url,
        });
      };
```

OLD (playlist `trackRow`, ~line 1229-1232):

```js
  playBtn.onclick = () => Player.play({
    key: "yt:"+t.videoId, title: t.title, artists: t.artists,
    thumbnail: t.thumbnail, videoId: t.videoId, sourcePlaylist,
  });
```

NEW:

```js
  playBtn.onclick = () => Player.play({
    key: "yt:"+t.videoId, title: t.title, artists: t.artists,
    thumbnail: t.thumbnail, videoId: t.videoId, sourcePlaylist,
    url: `https://music.youtube.com/watch?v=${t.videoId}`,
  });
```

OLD (song detail `preview-song`, ~line 1272-1275):

```js
  el("preview-song").onclick = () => Player.play({
    key: "sp:"+t.id, title: t.title, artists: t.artists,
    thumbnail: t.image, spotifyQuery: `${t.artists} ${t.title}`,
  });
```

NEW:

```js
  el("preview-song").onclick = () => Player.play({
    key: "sp:"+t.id, title: t.title, artists: t.artists,
    thumbnail: t.image, spotifyQuery: `${t.artists} ${t.title}`, url: t.spotify_url,
  });
```

- [ ] **Step 2: Make `startDownload` return a Promise that resolves when the job finishes**

OLD (the whole function):

```js
function startDownload(payload, label){
  const s = loadSettings();
  if(s.cookies_browser) payload.cookies_browser = s.cookies_browser;
  if(s.cookie_file) payload.cookie_file = s.cookie_file;

  fetch("/api/download", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(data => {
    drawer.hidden = false;
    el("dl-title").textContent = `⬇️ ${label}`;
    el("dl-status").innerHTML = `<span class="spinner"></span> běží…`;
    el("dl-log").innerHTML = "";
    el("dl-reveal").disabled = true;

    if(currentEventSource) currentEventSource.close();
    const es = new EventSource(`/api/jobs/${data.job_id}/events`);
    currentEventSource = es;
    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if(msg.line){
        const log = el("dl-log");
        const line = document.createElement("div");
        line.textContent = msg.line;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
      }
      if(msg.done){
        es.close();
        const ok = (msg.returncode === 0) || msg.status === "done";
        el("dl-status").textContent = ok ? "✅ hotovo" : "❌ chyba";
        el("dl-reveal").disabled = false;
        el("dl-reveal").onclick = () => fetch("/api/reveal", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({path: data.output_dir})
        });
        loadLibrary().then(refreshOwnedIndicators); // "✓ máš" now shows on the songs just downloaded
      }
    };
  });
}
```

NEW:

```js
function startDownload(payload, label){
  const s = loadSettings();
  if(s.cookies_browser) payload.cookies_browser = s.cookies_browser;
  if(s.cookie_file) payload.cookie_file = s.cookie_file;

  return fetch("/api/download", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(data => new Promise(resolve => {
    drawer.hidden = false;
    el("dl-title").textContent = `⬇️ ${label}`;
    el("dl-status").innerHTML = `<span class="spinner"></span> běží…`;
    el("dl-log").innerHTML = "";
    el("dl-reveal").disabled = true;
    el("dl-reveal").textContent = "📁 Otevřít složku";

    if(currentEventSource) currentEventSource.close();
    const es = new EventSource(`/api/jobs/${data.job_id}/events`);
    currentEventSource = es;
    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if(msg.line){
        const log = el("dl-log");
        const line = document.createElement("div");
        line.textContent = msg.line;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
      }
      if(msg.done){
        es.close();
        const ok = (msg.returncode === 0) || msg.status === "done";
        el("dl-status").textContent = ok ? "✅ hotovo" : "❌ chyba";
        el("dl-reveal").disabled = false;
        el("dl-reveal").onclick = () => fetch("/api/reveal", {
          method:"POST", headers:{"Content-Type":"application/json"},
          body: JSON.stringify({path: data.output_dir})
        });
        loadLibrary().then(refreshOwnedIndicators); // "✓ máš" now shows on the songs just downloaded
        resolve(ok);
      }
    };
  }));
}
```

(The only changes: `return fetch(...)` instead of `fetch(...)`; the inner
`.then(data => {...})` body is now `.then(data => new Promise(resolve => {...}))`
with a single `resolve(ok);` added at the very end, right after the
existing `loadLibrary().then(...)` line; and a defensive
`el("dl-reveal").textContent = "📁 Otevřít složku";` reset near the top,
since Step 4 below will repurpose this same button's text for a different
completion action and this guards against a stale label if the two flows
interleave.)

- [ ] **Step 3: Add the ⬇️ button and its dropdown menu**

Modify player-bar markup. OLD:

```html
  <div class="pb-right">
    <div class="pb-volume">
      <span id="pb-mute" title="Ztlumit">🔊</span>
      <input type="range" id="pb-volume" min="0" max="1" step="0.01" value="1" title="Hlasitost">
    </div>
    <button class="icon-btn-flat" id="pb-video-btn" title="Zobrazit video">🎬</button>
    <button class="icon-btn-flat" id="pb-history-btn" title="Historie přehrávání">🕘</button>
    <button class="icon-btn-flat" id="pb-queue-btn" title="Playlist k přehrávači">📃<span class="pb-badge" id="pb-queue-count">0</span></button>
  </div>
  <audio id="pb-audio" hidden></audio>
</div>
```

NEW:

```html
  <div class="pb-right">
    <div class="pb-volume">
      <span id="pb-mute" title="Ztlumit">🔊</span>
      <input type="range" id="pb-volume" min="0" max="1" step="0.01" value="1" title="Hlasitost">
    </div>
    <button class="icon-btn-flat" id="pb-video-btn" title="Zobrazit video">🎬</button>
    <button class="icon-btn-flat" id="pb-download-btn" title="Stáhnout / rozdělit na stopy">⬇️</button>
    <button class="icon-btn-flat" id="pb-history-btn" title="Historie přehrávání">🕘</button>
    <button class="icon-btn-flat" id="pb-queue-btn" title="Playlist k přehrávači">📃<span class="pb-badge" id="pb-queue-count">0</span></button>
  </div>
  <audio id="pb-audio" hidden></audio>
</div>
```

Add the menu markup right after the video-drawer (`#video-drawer`) closes,
before the `<!-- Download progress drawer -->` comment. OLD:

```html
  <video id="pb-video" controls style="width:100%;border-radius:8px;background:#000"></video>
</div>

<!-- Download progress drawer -->
```

NEW:

```html
  <video id="pb-video" controls style="width:100%;border-radius:8px;background:#000"></video>
</div>

<!-- "⬇️" popover — download the current track, or split it into stems. -->
<div class="dl-menu" id="dl-menu" hidden>
  <button id="dl-menu-song">⬇️ Stáhnout píseň</button>
  <button id="dl-menu-stems">🎧 Rozdělit na stopy</button>
</div>

<!-- Download progress drawer -->
```

Add CSS right after the existing drawer position rules (this deliberately
follows the exact same fixed/right/bottom anchoring pattern the prior
plan's final review had to add for `#video-drawer` after it shipped
invisible — do not omit the position rule here too). OLD:

```css
  #dl-drawer{left:20px;bottom:84px}
  #queue-drawer{right:20px;bottom:84px}
  #history-panel{right:20px;bottom:84px}
  #video-drawer{right:20px;bottom:84px;width:420px;padding:10px}
```

NEW:

```css
  #dl-drawer{left:20px;bottom:84px}
  #queue-drawer{right:20px;bottom:84px}
  #history-panel{right:20px;bottom:84px}
  #video-drawer{right:20px;bottom:84px;width:420px;padding:10px}
  .dl-menu{position:fixed;right:20px;bottom:84px;background:var(--card);
    border:1px solid var(--border);border-radius:10px;padding:6px;
    display:flex;flex-direction:column;gap:2px;min-width:220px;
    box-shadow:0 8px 24px rgba(0,0,0,.35);z-index:30}
  .dl-menu[hidden]{display:none}
  .dl-menu button{background:transparent;border:none;color:var(--text);
    text-align:left;padding:9px 12px;border-radius:7px;cursor:pointer;
    font-size:13px;width:100%}
  .dl-menu button:hover:not(:disabled){background:var(--card-hi)}
  .dl-menu button:disabled{opacity:.4;cursor:default}
```

- [ ] **Step 4: Wire the ⬇️ button, its menu, and the two trigger functions**

Insert inside the `Player` IIFE, right after the existing video-toggle
wiring block (after `el("video-close").onclick = ...`) and before the
final `return { play, playLocal, playLocalQueue };`:

```js
  // "⬇️" — download the current track normally, or split it into stems.
  // Lives here (inside Player) because it needs `current`, same as the
  // video toggle above.
  const downloadBtn = el("pb-download-btn");
  const dlMenu = el("dl-menu");

  downloadBtn.onclick = () => {
    if(!current){ showToast("Nic nehraje"); return; }
    el("dl-menu-song").disabled = !!current.relPath; // already a local file — nothing to download
    dlMenu.hidden = !dlMenu.hidden;
  };
  document.addEventListener("click", (e) => {
    if(!dlMenu.hidden && !dlMenu.contains(e.target) && e.target !== downloadBtn){
      dlMenu.hidden = true;
    }
  });
  el("dl-menu-song").onclick = () => {
    dlMenu.hidden = true;
    if(!current || !current.url) return;
    startDownload({
      urls: [current.url], output_name: "Songs", kind: "songs", bitrate: bitrate(),
    }, `${current.title} — ${current.artists}`);
  };
  el("dl-menu-stems").onclick = async () => {
    dlMenu.hidden = true;
    if(!current) return;
    if(current.relPath){
      startStemsSeparation(current.relPath, current.title || current.relPath);
      return;
    }
    if(!current.url){ showToast("Píseň se nedá rozdělit (chybí zdroj)"); return; }
    await downloadThenSeparate(current);
  };
```

Then, in the top-level script scope (not inside any IIFE — put it right
after `startDownload` and `refreshOwnedIndicators`, in the same "Download
(SSE progress)" section), add:

```js
// ======================================================================
// Stems separation — reuses the same #dl-drawer SSE-progress UI as
// downloads (same JOBS/events mechanism on the backend, just a
// different command), then hands off to the Steems tab.
// ======================================================================
function startStemsSeparation(rel, label){
  fetch("/api/stems/separate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({rel}),
  })
  .then(r => r.json())
  .then(data => {
    if(data.ready){
      showToast("✓ Už je rozdělené na stopy");
      openSteemsSong(data.name);
      return;
    }
    drawer.hidden = false;
    el("dl-title").textContent = `🎧 ${label}`;
    el("dl-status").innerHTML = `<span class="spinner"></span> odděluji stopy (může trvat pár minut)…`;
    el("dl-log").innerHTML = "";
    el("dl-reveal").disabled = true;

    if(currentEventSource) currentEventSource.close();
    const es = new EventSource(`/api/jobs/${data.job_id}/events`);
    currentEventSource = es;
    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if(msg.line){
        const log = el("dl-log");
        const line = document.createElement("div");
        line.textContent = msg.line;
        log.appendChild(line);
        log.scrollTop = log.scrollHeight;
      }
      if(msg.done){
        es.close();
        const ok = (msg.returncode === 0) || msg.status === "done";
        el("dl-status").textContent = ok ? "✅ hotovo" : "❌ chyba";
        if(ok){
          el("dl-reveal").disabled = false;
          el("dl-reveal").textContent = "🎧 Otevřít v Steems";
          el("dl-reveal").onclick = () => {
            drawer.hidden = true;
            openSteemsSong(data.name);
          };
        }
      }
    };
  });
}

// "Songs" downloads land at Songs/{artist}/{title}.ext (spotdl's own
// metadata for {artist}/{title}, not necessarily byte-identical to what
// the UI displayed) — the download job only returns the containing
// folder, not the exact saved filename, so find it by browsing that
// folder and fuzzy-matching on title the same way isOwned() already does
// for "✓ máš" badges.
async function findDownloadedRel(artists, title){
  try{
    const res = await fetch(`/api/library/browse?rel=${encodeURIComponent("Songs/" + artists)}`);
    if(!res.ok) return null;
    const data = await res.json();
    const t = norm(title);
    const match = (data.entries || []).find(e => e.type === "file" && norm(e.name).includes(t));
    return match ? match.rel : null;
  }catch(e){ return null; }
}

async function downloadThenSeparate(track){
  showToast("Nejdřív stahuju píseň…");
  await startDownload({
    urls: [track.url], output_name: "Songs", kind: "songs", bitrate: bitrate(),
  }, `${track.title} — ${track.artists}`);
  const rel = await findDownloadedRel(track.artists, track.title);
  if(!rel){ showToast("Píseň se stáhla, ale nepodařilo se ji najít pro rozdělení."); return; }
  startStemsSeparation(rel, track.title || rel);
}

// Placeholder — Task 4 replaces this whole function with the real
// "switch to Steems tab and load this song into the mixer" behavior.
function openSteemsSong(name){
  showToast("Steems tab zatím nenačten"); // replaced by Task 4's real implementation
}
```

- [ ] **Step 5: Add the 🎧 button to Knihovna file rows**

OLD:

```js
    row.innerHTML = `
      <span class="fs-icon">${fsIcon(entry)}</span>
      <span class="fs-name">${escapeHtml(entry.name)}</span>
      <span class="fs-size">${sizeLabel}</span>
      ${entry.type === "file" ? '<button class="icon-btn fs-play" title="Přehrát">▶</button>' : ''}
    `;
    if(entry.type === "dir"){
      row.onclick = () => loadLibraryFolder(entry.rel);
    } else {
      const meta = libraryTrackMeta(entry.name);
      const playIt = () => Player.playLocal({ relPath: entry.rel, ...meta });
      row.querySelector(".fs-play").onclick = (e) => { e.stopPropagation(); playIt(); };
      row.onclick = playIt;
    }
```

NEW:

```js
    row.innerHTML = `
      <span class="fs-icon">${fsIcon(entry)}</span>
      <span class="fs-name">${escapeHtml(entry.name)}</span>
      <span class="fs-size">${sizeLabel}</span>
      ${entry.type === "file" ? '<button class="icon-btn fs-play" title="Přehrát">▶</button><button class="icon-btn fs-stems" title="Rozdělit na stopy">🎧</button>' : ''}
    `;
    if(entry.type === "dir"){
      row.onclick = () => loadLibraryFolder(entry.rel);
    } else {
      const meta = libraryTrackMeta(entry.name);
      const playIt = () => Player.playLocal({ relPath: entry.rel, ...meta });
      row.querySelector(".fs-play").onclick = (e) => { e.stopPropagation(); playIt(); };
      row.querySelector(".fs-stems").onclick = (e) => {
        e.stopPropagation();
        startStemsSeparation(entry.rel, meta.title || entry.name);
      };
      row.onclick = playIt;
    }
```

- [ ] **Step 6: Manual verification**

```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach((s,i)=>{ try{ new Function(s); }catch(e){ console.log('script',i,'ERROR:',e.message); process.exitCode=1; } });
console.log('checked', scripts.length, 'inline scripts');
"
```

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
curl -s http://127.0.0.1:8799/ | grep -o 'id="pb-download-btn"\|id="dl-menu"\|id="dl-menu-song"\|id="dl-menu-stems"\|class="icon-btn fs-stems"'
curl -s http://127.0.0.1:8799/ | grep -o '\.dl-menu{[^}]*}'
kill %1 2>/dev/null
```

All 4 ids/class must appear, and the `.dl-menu{...}` rule must include
`position:fixed`, `right:20px`, and `bottom:84px` — confirm this
specifically, since an unpositioned popover is exactly the bug the prior
plan shipped and only caught in its final review.

State plainly in your report that clicking through the actual menu (open
it, see "Stáhnout píseň" disabled for a local track, trigger a real
separation) still needs a human — no browser tool is available here.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: separation entry points — player ⬇️ menu, Knihovna 🎧 button"
```

---

### Task 4: Steems tab shell (frontend)

**Files:**
- Modify: `static/index.html:264` area (tab button)
- Modify: `static/index.html:367-374` area (new `#steems-section` markup, sibling of `#library-section`)
- Modify: `static/index.html:878-901` (`setMode` — add a `"steems"` branch)
- Modify: `static/index.html:1009-1017` area (`runSearch` — also hide `#steems-section`)
- Modify: `static/index.html` (replace Task 3's `openSteemsSong` placeholder with the real implementation)
- New JS: `loadSteemsList`, `loadMixerSong`
- New CSS: `.mixer` scaffold (empty until Task 5 fills in channel-strip rules — just enough here for the song list + an empty mixer container to lay out correctly)

**Interfaces:**
- Consumes: `GET /api/stems` (Task 2).
- Produces: `steemsSongs` (module-level array — Task 5's `Mixer.load()` is called with an entry from this array), `loadMixerSong(name)` (Task 5 consumes — its body currently calls a not-yet-defined `Mixer.load(song)`; Task 5 defines `Mixer`, so until Task 5 lands this function will throw if actually clicked — acceptable within-plan sequencing, same pattern as Task 3's `openSteemsSong` placeholder).

- [ ] **Step 1: Add the tab button**

OLD:

```html
    <button id="tab-library">📁 Knihovna</button>
```

NEW:

```html
    <button id="tab-library">📁 Knihovna</button>
    <button id="tab-steems">🎚️ Steems</button>
```

- [ ] **Step 2: Add the section markup**

OLD (right after `#library-section` closes, before `#results-title`):

```html
  <div id="library-section" hidden>
    <div class="breadcrumb" id="library-breadcrumb"></div>
    <div id="library-play-all" class="library-play-all" hidden>
      <button class="btn secondary" id="library-play-all-btn">▶ Přehrát vše</button>
      <span id="library-play-all-summary"></span>
    </div>
    <div id="library-list" class="fs-list"></div>
  </div>

  <div class="section-title" id="results-title" hidden>Výsledky</div>
```

NEW:

```html
  <div id="library-section" hidden>
    <div class="breadcrumb" id="library-breadcrumb"></div>
    <div id="library-play-all" class="library-play-all" hidden>
      <button class="btn secondary" id="library-play-all-btn">▶ Přehrát vše</button>
      <span id="library-play-all-summary"></span>
    </div>
    <div id="library-list" class="fs-list"></div>
  </div>

  <div id="steems-section" hidden>
    <div class="section-title">🎚️ Rozseparované písně</div>
    <div id="steems-list" class="fs-list"></div>
    <div id="mixer" class="mixer" hidden></div>
  </div>

  <div class="section-title" id="results-title" hidden>Výsledky</div>
```

(`#mixer`'s inner markup — transport bar and channel strips — is Task 5's
job; this task only needs the empty, hidden container to exist so
`setMode`/`loadSteemsList` have something to show/hide and render into.)

- [ ] **Step 3: Wire the tab and extend `setMode`/`runSearch`**

OLD:

```js
el("tab-library").onclick = () => setMode("library");

function setMode(mode){
  state.mode = mode;
  el("tab-playlists").classList.toggle("active", mode==="playlists");
  el("tab-songs").classList.toggle("active", mode==="songs");
  el("tab-library").classList.toggle("active", mode==="library");
  el("search-input").disabled = mode === "library";
  el("search-btn").disabled = mode === "library";
  el("search-input").placeholder = mode==="playlists"
    ? "Hledej žánr, náladu, éru… (např. '90s hip hop')"
    : mode==="songs" ? "Hledej píseň nebo interpreta…" : "";
  regionToggle.style.display = mode === "playlists" ? "flex" : "none";
  grid.innerHTML = "";
  resultsTitle.hidden = true;
  el("load-more-wrap").hidden = true;
  browseSection.hidden = mode === "library";
  el("library-section").hidden = mode !== "library";
  if(mode === "library"){
    loadLibraryFolder("");
  } else {
    renderCustomChips();
    renderFavPlaylists();
  }
  updateArtistSuggestions();
}
```

NEW:

```js
el("tab-library").onclick = () => setMode("library");
el("tab-steems").onclick = () => setMode("steems");

function setMode(mode){
  state.mode = mode;
  el("tab-playlists").classList.toggle("active", mode==="playlists");
  el("tab-songs").classList.toggle("active", mode==="songs");
  el("tab-library").classList.toggle("active", mode==="library");
  el("tab-steems").classList.toggle("active", mode==="steems");
  el("search-input").disabled = mode === "library" || mode === "steems";
  el("search-btn").disabled = mode === "library" || mode === "steems";
  el("search-input").placeholder = mode==="playlists"
    ? "Hledej žánr, náladu, éru… (např. '90s hip hop')"
    : mode==="songs" ? "Hledej píseň nebo interpreta…" : "";
  regionToggle.style.display = mode === "playlists" ? "flex" : "none";
  grid.innerHTML = "";
  resultsTitle.hidden = true;
  el("load-more-wrap").hidden = true;
  browseSection.hidden = mode === "library" || mode === "steems";
  el("library-section").hidden = mode !== "library";
  el("steems-section").hidden = mode !== "steems";
  if(mode === "library"){
    loadLibraryFolder("");
  } else if(mode === "steems"){
    loadSteemsList();
  } else {
    renderCustomChips();
    renderFavPlaylists();
  }
  updateArtistSuggestions();
}
```

OLD (inside `runSearch`, near its top):

```js
  browseSection.hidden = true;
  el("library-section").hidden = true;
```

NEW:

```js
  browseSection.hidden = true;
  el("library-section").hidden = true;
  el("steems-section").hidden = true;
```

- [ ] **Step 4: Add the list-loading functions and the real `openSteemsSong`**

Insert right after `renderLibraryList` closes (before the "Search + render
results" section comment):

```js
// ======================================================================
// Steems — list of already-separated songs, and the mixpult they load
// into. Mixer playback engine and channel-strip UI are Task 5/6; this
// section only owns the song list and tab plumbing.
//
// Architecturally inspired by StemDeck (github.com/stemdeckapp/stemdeck,
// Apache-2.0) — same shape of problem (Demucs-separated stems, a synced
// multi-track Web Audio mixer), written fresh for this codebase rather
// than ported, since only its documented architecture was available
// during planning, not its literal source.
// ======================================================================
let steemsSongs = [];

async function loadSteemsList(){
  const list = el("steems-list");
  list.innerHTML = `<div class="empty" style="padding:24px 10px"><span class="spinner"></span> Načítám…</div>`;
  el("mixer").hidden = true;
  try{
    const res = await fetch("/api/stems");
    steemsSongs = await res.json();
  }catch(e){
    list.innerHTML = `<div class="empty" style="padding:24px 10px">Seznam se nepodařilo načíst.</div>`;
    return;
  }
  if(!steemsSongs.length){
    list.innerHTML = `<div class="empty" style="padding:24px 10px">Zatím nemáš žádné rozseparované písně — u přehrávané nebo stažené písně použij „🎧 Rozdělit na stopy".</div>`;
    return;
  }
  list.innerHTML = "";
  steemsSongs.forEach(song => {
    const row = document.createElement("div");
    row.className = "fs-row";
    row.innerHTML = `
      <span class="fs-icon">🎚️</span>
      <span class="fs-name">${escapeHtml(song.name)}</span>
      <span class="fs-size">${song.stems.length} stop</span>
    `;
    row.onclick = () => loadMixerSong(song.name);
    list.appendChild(row);
  });
}

function loadMixerSong(name){
  const song = steemsSongs.find(s => s.name === name);
  if(!song){ showToast("Píseň nenalezena — zkus přepnout na Steems znovu"); return; }
  Mixer.load(song);
}
```

Then replace Task 3's placeholder. OLD:

```js
// Placeholder — Task 4 replaces this whole function with the real
// "switch to Steems tab and load this song into the mixer" behavior.
function openSteemsSong(name){
  showToast("Steems tab zatím nenačten"); // replaced by Task 4's real implementation
}
```

NEW:

```js
function openSteemsSong(name){
  setMode("steems");
  loadMixerSong(name);
}
```

- [ ] **Step 5: Add scaffold CSS**

Insert near the other `.fs-*`/section CSS (right after the existing
`.fs-row .fs-size{...}` rule, or wherever the file's existing `.fs-*`
block ends):

```css
  .mixer{margin-top:18px}
  .mixer[hidden]{display:none}
```

(Real channel-strip/transport CSS is Task 5's job — this is just enough
so an empty, hidden `#mixer` doesn't collapse oddly once Task 5 starts
filling it in.)

- [ ] **Step 6: Manual verification**

```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach((s,i)=>{ try{ new Function(s); }catch(e){ console.log('script',i,'ERROR:',e.message); process.exitCode=1; } });
console.log('checked', scripts.length, 'inline scripts');
"
```

Note: the syntax check will PASS even though `loadMixerSong` references
`Mixer.load` which doesn't exist until Task 5 — `new Function(s)` only
parses, it doesn't execute, so an undefined runtime reference is invisible
to this check. This is expected and fine for this task; Task 5/6's own
verification is where `Mixer` actually needs to exist and work.

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
curl -s http://127.0.0.1:8799/ | grep -o 'id="tab-steems"\|id="steems-section"\|id="steems-list"\|id="mixer"'
curl -s "http://127.0.0.1:8799/api/stems"
kill %1 2>/dev/null
```

All 4 ids must appear, and `/api/stems` must return valid JSON (an empty
array `[]` is fine if nothing's been separated yet on this machine).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: Steems tab shell — song list, tab wiring"
```

---

### Task 5: Mixpult audio engine + channel strips (frontend)

**Files:**
- Modify: `static/index.html` (new CSS block for `.mixer-transport`/`.channel*`)
- Modify: `static/index.html:367-374` area's `#mixer` div — replace the empty `<div id="mixer" class="mixer" hidden></div>` with the transport markup (channel strips are rendered dynamically by JS, not static markup)
- New JS: the `Mixer` IIFE (load/play/pause/seek/gain logic, channel-strip rendering, waveform drawing, VU metering) — this is the largest single piece of code in this plan

**Interfaces:**
- Consumes: `GET /api/stems/{name}/{stem}.wav` (Task 2), `steemsSongs`/`loadMixerSong` (Task 4).
- Produces: `Mixer.load(song)` (Task 4 calls this); `Mixer` exposes nothing else publicly in this task — Task 6 will add an export method to the same object and wire a button to it, extending this file in place.

- [ ] **Step 1: Replace the empty `#mixer` div with real transport markup**

OLD:

```html
  <div id="steems-section" hidden>
    <div class="section-title">🎚️ Rozseparované písně</div>
    <div id="steems-list" class="fs-list"></div>
    <div id="mixer" class="mixer" hidden></div>
  </div>
```

NEW:

```html
  <div id="steems-section" hidden>
    <div class="section-title">🎚️ Rozseparované písně</div>
    <div id="steems-list" class="fs-list"></div>
    <div id="mixer" class="mixer" hidden>
      <div class="mixer-transport">
        <button class="btn secondary" id="mixer-play">▶</button>
        <div class="mixer-seek" id="mixer-seek">
          <div class="mixer-seek-fill" id="mixer-seek-fill"></div>
        </div>
        <span class="pb-time" id="mixer-time-cur">0:00</span>
        <span class="pb-time">/</span>
        <span class="pb-time" id="mixer-time-dur">0:00</span>
      </div>
      <div id="mixer-channels" class="mixer-channels"></div>
    </div>
  </div>
```

- [ ] **Step 2: Add the channel-strip/transport CSS**

Insert right after the `.mixer{margin-top:18px} .mixer[hidden]{display:none}`
rule Task 4 added:

```css
  .mixer-transport{display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap}
  .mixer-seek{flex:1;min-width:120px;height:8px;background:var(--bg2);
    border-radius:4px;position:relative;cursor:pointer}
  .mixer-seek-fill{position:absolute;left:0;top:0;bottom:0;background:var(--accent);
    border-radius:4px;width:0%;pointer-events:none}
  .mixer-channels{display:flex;gap:12px;flex-wrap:wrap}
  .channel{background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:12px;width:150px;display:flex;flex-direction:column;gap:8px;flex-shrink:0}
  .channel-name{font-size:12.5px;font-weight:700;text-transform:capitalize}
  .channel-wave{width:100%;height:60px;background:var(--bg2);border-radius:6px;display:block}
  .channel-controls{display:flex;align-items:center;gap:6px}
  .channel-fader{writing-mode:vertical-lr;direction:rtl;width:24px;height:90px}
  .channel-vu{width:6px;height:90px;background:var(--bg2);border-radius:3px;
    position:relative;overflow:hidden;flex-shrink:0}
  .channel-vu-fill{position:absolute;bottom:0;left:0;right:0;background:var(--ok);
    height:0%}
  .channel-buttons{display:flex;flex-direction:column;gap:4px}
  .channel-mute,.channel-solo{width:26px;height:22px;border-radius:5px;
    border:1px solid var(--border);background:var(--bg2);color:var(--sub);
    font-size:10px;font-weight:700;cursor:pointer}
  .channel-mute.active{background:var(--danger);color:#fff;border-color:var(--danger)}
  .channel-solo.active{background:var(--gold);color:#2a1d00;border-color:var(--gold)}
```

- [ ] **Step 3: Write the `Mixer` module**

Insert right after `loadMixerSong` (the last function Task 4 added):

```js
const Mixer = (() => {
  let audioCtx = null;
  let channels = [];   // [{stem, buffer, gainNode, analyser, muted, solo, faderValue, source, vuFill}]
  let duration = 0;
  let isPlaying = false;
  let startedAt = 0;   // audioCtx.currentTime this playback run effectively started at (t=0 mapped here)
  let pausedAt = 0;    // playhead position in seconds while paused
  let rafId = null;
  let loadToken = 0;   // guards a slow load() from clobbering state a newer load() already set up

  function ctx(){
    if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return audioCtx;
  }

  function stopAllSources(){
    channels.forEach(c => { if(c.source){ try{ c.source.stop(); }catch(e){} c.source = null; } });
  }

  async function load(song){
    const myToken = ++loadToken;
    stopAllSources();
    isPlaying = false;
    pausedAt = 0;
    duration = 0;
    el("mixer").hidden = false;
    el("mixer-play").textContent = "▶";
    el("mixer-channels").innerHTML = `<div class="empty" style="padding:24px 10px"><span class="spinner"></span> Načítám stopy…</div>`;

    const loaded = [];
    for(const stem of song.stems){
      const res = await fetch(`/api/stems/${encodeURIComponent(song.name)}/${stem}.wav`);
      const arrayBuf = await res.arrayBuffer();
      if(myToken !== loadToken) return; // a newer load() call has since taken over
      const audioBuf = await ctx().decodeAudioData(arrayBuf);
      if(myToken !== loadToken) return;
      const gainNode = ctx().createGain();
      const analyser = ctx().createAnalyser();
      analyser.fftSize = 256;
      gainNode.connect(analyser);
      analyser.connect(ctx().destination);
      loaded.push({
        stem, buffer: audioBuf, gainNode, analyser,
        muted: false, solo: false, faderValue: 1, source: null, vuFill: null,
      });
      duration = Math.max(duration, audioBuf.duration);
    }
    if(myToken !== loadToken) return;
    channels = loaded;
    renderChannels();
    updateSeekUI(0);
  }

  function anySolo(){ return channels.some(c => c.solo); }

  function applyGains(){
    const solo = anySolo();
    channels.forEach(c => {
      const active = solo ? c.solo : !c.muted;
      c.gainNode.gain.value = active ? c.faderValue : 0;
    });
  }

  function drawWaveform(canvas, buffer){
    const ctx2d = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    const data = buffer.getChannelData(0);
    const step = Math.max(1, Math.ceil(data.length / w));
    ctx2d.clearRect(0, 0, w, h);
    ctx2d.fillStyle = "#1b8f80"; // --accent-dim
    for(let x = 0; x < w; x++){
      let min = 1, max = -1;
      const start = x * step;
      for(let i = 0; i < step && start + i < data.length; i++){
        const v = data[start + i];
        if(v < min) min = v;
        if(v > max) max = v;
      }
      const y1 = (1 - (max + 1) / 2) * h;
      const y2 = (1 - (min + 1) / 2) * h;
      ctx2d.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
  }

  function renderChannels(){
    const box = el("mixer-channels");
    box.innerHTML = "";
    channels.forEach(c => {
      const strip = document.createElement("div");
      strip.className = "channel";
      strip.innerHTML = `
        <div class="channel-name">${escapeHtml(c.stem)}</div>
        <canvas class="channel-wave" width="126" height="60"></canvas>
        <div class="channel-controls">
          <input type="range" class="channel-fader" min="0" max="1.5" step="0.01" value="1">
          <div class="channel-vu"><div class="channel-vu-fill"></div></div>
          <div class="channel-buttons">
            <button class="channel-mute" title="Mute">M</button>
            <button class="channel-solo" title="Solo">S</button>
          </div>
        </div>
      `;
      c.vuFill = strip.querySelector(".channel-vu-fill");
      drawWaveform(strip.querySelector(".channel-wave"), c.buffer);

      strip.querySelector(".channel-fader").addEventListener("input", (e) => {
        c.faderValue = parseFloat(e.target.value);
        applyGains();
      });
      const muteBtn = strip.querySelector(".channel-mute");
      muteBtn.onclick = () => { c.muted = !c.muted; muteBtn.classList.toggle("active", c.muted); applyGains(); };
      const soloBtn = strip.querySelector(".channel-solo");
      soloBtn.onclick = () => { c.solo = !c.solo; soloBtn.classList.toggle("active", c.solo); applyGains(); };

      box.appendChild(strip);
    });
    applyGains();
  }

  function currentTime(){
    return isPlaying ? Math.min(duration, ctx().currentTime - startedAt) : pausedAt;
  }

  function scheduleAll(offset){
    const when = ctx().currentTime + 0.05; // small shared lead so every source starts in lockstep
    channels.forEach(c => {
      const source = ctx().createBufferSource();
      source.buffer = c.buffer;
      source.connect(c.gainNode);
      source.start(when, offset);
      c.source = source;
    });
    startedAt = when - offset;
  }

  function play(){
    if(!channels.length || isPlaying) return;
    if(pausedAt >= duration) pausedAt = 0;
    scheduleAll(pausedAt);
    isPlaying = true;
    el("mixer-play").textContent = "⏸";
    tick();
  }

  function pause(){
    if(!isPlaying) return;
    pausedAt = currentTime();
    stopAllSources();
    isPlaying = false;
    el("mixer-play").textContent = "▶";
    if(rafId) cancelAnimationFrame(rafId);
  }

  function seek(t){
    t = Math.max(0, Math.min(duration, t));
    if(isPlaying){
      stopAllSources();
      scheduleAll(t);
    } else {
      pausedAt = t;
    }
    updateSeekUI(t);
  }

  function updateSeekUI(t){
    const pct = duration ? (t / duration) * 100 : 0;
    el("mixer-seek-fill").style.width = pct + "%";
    el("mixer-time-cur").textContent = fmtDur(t);
    el("mixer-time-dur").textContent = fmtDur(duration);
  }

  function tick(){
    if(!isPlaying) return;
    const t = currentTime();
    updateSeekUI(t);
    channels.forEach(c => {
      const data = new Uint8Array(c.analyser.frequencyBinCount);
      c.analyser.getByteFrequencyData(data);
      let peak = 0;
      for(let i = 0; i < data.length; i++) if(data[i] > peak) peak = data[i];
      if(c.vuFill) c.vuFill.style.height = ((peak / 255) * 100) + "%";
    });
    if(t >= duration){
      pause();
      pausedAt = 0;
      updateSeekUI(0);
      return;
    }
    rafId = requestAnimationFrame(tick);
  }

  el("mixer-play").onclick = () => isPlaying ? pause() : play();
  el("mixer-seek").addEventListener("click", (e) => {
    if(!duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seek(pct * duration);
  });

  return { load };
})();
```

- [ ] **Step 4: Manual verification**

```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach((s,i)=>{ try{ new Function(s); }catch(e){ console.log('script',i,'ERROR:',e.message); process.exitCode=1; } });
console.log('checked', scripts.length, 'inline scripts');
"
```

Must report no errors — this now also implicitly confirms `Mixer` parses
correctly and `loadMixerSong`'s `Mixer.load(song)` call resolves to a real
function (still only a parse check, not an execution check — no browser
here, same limitation as every other frontend task in this process).

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
curl -s http://127.0.0.1:8799/ | grep -o 'id="mixer-play"\|id="mixer-seek"\|id="mixer-channels"\|class="channel-fader"'
kill %1 2>/dev/null
```

`id="mixer-play"`, `id="mixer-seek"`, `id="mixer-channels"` must appear
(the `class="channel-fader"` grep is expected to find nothing — channel
strips are rendered dynamically by JS from `renderChannels()`, never
present in the static served HTML; note this in your report rather than
treating a miss on that one grep as a failure).

Read your own diff as if you were the browser executing it and trace:
does `load(song)` correctly guard against a slower earlier `load()` call
resolving after a newer one started (the `loadToken` checks)? Does
`applyGains()`'s solo logic match the spec exactly ("an active solo on
any channel mutes every non-solo channel regardless of its own mute
state")? Does `scheduleAll`/`play`/`pause`/`seek` keep `startedAt`
consistent so `currentTime()` stays correct across a pause→seek→play
sequence (trace through at least one full cycle by hand in your report)?

State plainly that actually hearing synced playback, seeing waveforms
render, and confirming mute/solo/fader audibly work still needs a human
with speakers — nothing in this process can verify that.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: Mixpult audio engine — synced Web Audio playback, fader/mute/solo/waveform/VU per channel"
```

---

### Task 6: Mixpult WAV export (frontend)

**Files:**
- Modify: `static/index.html` (add an export button to the transport markup; extend the `Mixer` module with an export function)

**Interfaces:**
- Consumes: `Mixer`'s internal `channels`/`duration`/`anySolo` (Task 5 — this task is a same-file, same-module extension, not a new public interface for anyone else to consume).

- [ ] **Step 1: Add the export button**

OLD:

```html
      <div class="mixer-transport">
        <button class="btn secondary" id="mixer-play">▶</button>
        <div class="mixer-seek" id="mixer-seek">
          <div class="mixer-seek-fill" id="mixer-seek-fill"></div>
        </div>
        <span class="pb-time" id="mixer-time-cur">0:00</span>
        <span class="pb-time">/</span>
        <span class="pb-time" id="mixer-time-dur">0:00</span>
      </div>
```

NEW:

```html
      <div class="mixer-transport">
        <button class="btn secondary" id="mixer-play">▶</button>
        <div class="mixer-seek" id="mixer-seek">
          <div class="mixer-seek-fill" id="mixer-seek-fill"></div>
        </div>
        <span class="pb-time" id="mixer-time-cur">0:00</span>
        <span class="pb-time">/</span>
        <span class="pb-time" id="mixer-time-dur">0:00</span>
        <button class="btn secondary" id="mixer-export">⬇️ Exportovat mix</button>
      </div>
```

- [ ] **Step 2: Add the WAV encoder and export function inside the `Mixer` IIFE**

Insert right before the final `return { load };` line of the `Mixer`
module (i.e. after the `el("mixer-seek").addEventListener(...)` block
Task 5 added):

```js
  function encodeWav(audioBuffer){
    const numChannels = audioBuffer.numberOfChannels;
    const sampleRate = audioBuffer.sampleRate;
    const length = audioBuffer.length;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const dataSize = length * blockAlign;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    function writeString(offset, str){
      for(let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);            // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);           // bits per sample
    writeString(36, "data");
    view.setUint32(40, dataSize, true);

    const channelData = [];
    for(let ch = 0; ch < numChannels; ch++) channelData.push(audioBuffer.getChannelData(ch));

    let offset = 44;
    for(let i = 0; i < length; i++){
      for(let ch = 0; ch < numChannels; ch++){
        let sample = Math.max(-1, Math.min(1, channelData[ch][i]));
        sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
        view.setInt16(offset, sample, true);
        offset += 2;
      }
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  async function exportMix(){
    if(!channels.length) return;
    const exportBtn = el("mixer-export");
    exportBtn.disabled = true;
    exportBtn.textContent = "⏳ Exportuji…";
    try{
      const sampleRate = ctx().sampleRate;
      const length = Math.max(1, Math.ceil(duration * sampleRate));
      const offlineCtx = new OfflineAudioContext(2, length, sampleRate);
      const solo = anySolo();
      channels.forEach(c => {
        const active = solo ? c.solo : !c.muted;
        if(!active) return;
        const source = offlineCtx.createBufferSource();
        source.buffer = c.buffer;
        const gain = offlineCtx.createGain();
        gain.gain.value = c.faderValue;
        source.connect(gain);
        gain.connect(offlineCtx.destination);
        source.start(0);
      });
      const rendered = await offlineCtx.startRendering();
      const blob = encodeWav(rendered);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "mix.wav";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }catch(e){
      showToast("Export selhal");
    }finally{
      exportBtn.disabled = false;
      exportBtn.textContent = "⬇️ Exportovat mix";
    }
  }

  el("mixer-export").onclick = () => exportMix();
```

- [ ] **Step 3: Manual verification**

```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
scripts.forEach((s,i)=>{ try{ new Function(s); }catch(e){ console.log('script',i,'ERROR:',e.message); process.exitCode=1; } });
console.log('checked', scripts.length, 'inline scripts');
"
```

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
curl -s http://127.0.0.1:8799/ | grep -o 'id="mixer-export"'
kill %1 2>/dev/null
```

Trace the WAV header math by hand in your report for one concrete case
(e.g. a 2-channel, 44100Hz, 5-second buffer): confirm `dataSize`,
`blockAlign`, and the RIFF chunk size (`36 + dataSize`) come out to the
values a real WAV file for that buffer should have. State plainly that
actually opening the exported file in a player to confirm it sounds
right still needs a human — no audio playback is possible here.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Mixpult WAV export via OfflineAudioContext"
```

---

## Plan Self-Review

**Spec coverage** (against `docs/specs/2026-09-12-steems-mixpult-design.md`):
1. Player ⬇️ menu (download / split to stems) → Task 3. ✅
2. Knihovna 🎧 button → Task 3. ✅
3. Backend separation pipeline reusing JOBS/SSE → Task 1. ✅
4. `GET /api/stems`, `GET /api/stems/{name}/{stem}.wav` → Task 2. ✅
5. "🎚️ Steems" tab with song list → Task 4. ✅
6. Mixpult: fader/mute/solo/waveform/VU per stem, synced playback → Task 5. ✅
7. Export mix to WAV → Task 6. ✅

**Placeholder scan:** no TBD/TODO; every step has concrete code. The one
intentional forward-reference (`openSteemsSong` in Task 3, `Mixer.load`
in Task 4) is explicitly labeled as a placeholder-to-be-replaced with a
comment pointing at the task that fills it in, not left ambiguous.

**Type/name consistency check:** `STEM_NAMES` (Python) and the stem
strings used in `Mixer`'s channel objects (`vocals`/`drums`/`bass`/
`guitar`/`piano`/`other`) match exactly — both ultimately come from the
same Demucs `htdemucs_6s` model. `song.name`/`song.stems` (from `GET
/api/stems`) are consumed with those exact field names in
`loadSteemsList`/`loadMixerSong`/`Mixer.load`. `startStemsSeparation`,
`downloadThenSeparate`, `findDownloadedRel`, `openSteemsSong` are each
defined once (Task 3, except `openSteemsSong`'s real body which Task 4
overwrites) and called with matching signatures everywhere they're used.
