# Knihovna, konfigurovatelná cesta stahování a video v přehrávači — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user configure where Pick downloads to, browse that folder like a file manager, play what's already there (including whole folders back-to-back) in the existing player, and pop open the actual YouTube video for whatever's playing.

**Architecture:** All additive to the existing single-file FastAPI backend (`server.py`) and single-file vanilla-JS frontend (`static/index.html`) — no new services, no build step, no new frontend dependencies. New backend endpoints follow the same patterns already in the file (Pydantic request models, `JSONResponse` error bodies, module-level caches). New frontend behavior extends the existing `Player` IIFE and `setMode()` tab system rather than introducing a second UI paradigm.

**Tech Stack:** Python 3 + FastAPI 0.103.2 / Starlette 0.27.0 (venv: `~/.local/venvs/spotdl`), vanilla JS + `<audio>`/`<video>` elements, no frontend build step.

**Spec:** `docs/specs/2026-09-12-library-and-player-design.md`

## Global Constraints

- App is local-only (binds 127.0.0.1), single user — no auth/CORS work needed anywhere in this plan.
- **Starlette 0.27.0 (pinned in this venv) does not support HTTP Range on `FileResponse`** — verified directly against its source (no Range/Accept-Ranges/If-Range handling exists in that version). Do not reach for `FileResponse` for anything that needs seeking; Task 4 hand-rolls a small Range-aware `StreamingResponse` helper instead.
- `MUSIC_ROOT` is a plain module-level `Path` in `server.py`, reassigned via `global` when settings change. Every function that reads it (existing and new) reads the module global at call time — no caching/closures over its value anywhere.
- No test framework exists yet (Task 1 adds pytest + FastAPI's `TestClient`). Backend endpoints get real automated tests. Frontend changes (vanilla JS in a single static HTML file, no test runner, no build step — adding one would be disproportionate infrastructure for a personal local tool) get explicit **manual verification** steps instead — run the server, do the exact thing described, confirm the exact result described.
- All user-facing strings are Czech, matching the app's existing tone (short, direct, occasional emoji in labels).
- Match existing `server.py` style: double blank line between top-level defs, docstrings on non-obvious functions, Pydantic `BaseModel` request bodies, `JSONResponse({"error": ...}, status_code=...)` for error responses.
- This project has no git history yet. Task 1 runs `git init` so the commit steps below actually apply; if you're executing this plan and the repo already exists by the time you get here, skip that one sub-step.

---

### Task 1: Test infrastructure + git init

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: `client` fixture (FastAPI `TestClient` over `server.app`), `tmp_music_root` fixture (patches `server.MUSIC_ROOT` to a fresh empty `tmp_path` directory) — every later backend test task consumes both.

- [ ] **Step 1: Initialize git and add a `.gitignore`**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
git init
```

Write `.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 2: Install pytest into the app's venv**

```bash
"$HOME/.local/venvs/spotdl/bin/pip" install pytest
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def tmp_music_root(tmp_path, monkeypatch):
    """Points server.MUSIC_ROOT at a throwaway directory for the test, so
    tests never touch the real ~/Music/spotdl (or whatever the user has
    configured).
    """
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setattr(server, "MUSIC_ROOT", root)
    return root
```

- [ ] **Step 4: Write a smoke test**

`tests/test_smoke.py`:

```python
def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
```

- [ ] **Step 5: Run it**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: `test_health` PASSES (this exercises real, already-working code —
it's here to prove the harness itself works before anything else depends
on it).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test: add pytest harness with TestClient + tmp_music_root fixtures"
```

---

### Task 2: Configurable download path — backend

**Files:**
- Modify: `server.py:11-28` (imports, `MUSIC_ROOT` init)
- Modify: `server.py` (end of file — new endpoints, after `/api/reveal`)
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `GET /api/settings` → `{"music_root": str}`; `POST /api/settings` `{"music_root": str}` → `{"music_root": str}` or `{"error": str}` (400); `POST /api/settings/pick-folder` → `{"path": str|null}`. `server.CONFIG_PATH` (module-level `Path`), `server.DEFAULT_MUSIC_ROOT` (module-level `Path`).

- [ ] **Step 1: Write the failing tests**

`tests/test_settings.py`:

```python
import json

import server


def test_get_settings_returns_current_music_root(client, tmp_music_root):
    res = client.get("/api/settings")
    assert res.status_code == 200
    assert res.json() == {"music_root": str(tmp_music_root)}


def test_update_settings_persists_and_updates_music_root(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    new_root = tmp_path / "new-music-folder"

    res = client.post("/api/settings", json={"music_root": str(new_root)})

    assert res.status_code == 200
    assert res.json() == {"music_root": str(new_root)}
    assert server.MUSIC_ROOT == new_root
    assert new_root.is_dir()  # created if missing
    assert json.loads((tmp_path / "config.json").read_text()) == {"music_root": str(new_root)}


def test_update_settings_rejects_relative_path(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")

    res = client.post("/api/settings", json={"music_root": "relative/path"})

    assert res.status_code == 400
    assert "error" in res.json()


def test_pick_folder_returns_chosen_path(client, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return type("R", (), {"returncode": 0, "stdout": "/Users/max/Music\n"})()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    res = client.post("/api/settings/pick-folder")

    assert res.json() == {"path": "/Users/max/Music"}


def test_pick_folder_returns_none_on_cancel(client, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return type("R", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    res = client.post("/api/settings/pick-folder")

    assert res.json() == {"path": None}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_settings.py -v
```

Expected: FAIL — `/api/settings` and `/api/settings/pick-folder` don't exist yet (404s / AttributeError on `server.CONFIG_PATH`).

- [ ] **Step 3: Implement**

Modify `server.py:11-28`. OLD:

```python
import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

HOME = Path.home()
MUSIC_ROOT = HOME / "Music" / "spotdl"
APP_DIR = Path(__file__).parent
SPOTDL_BIN = str(HOME / ".local" / "bin" / "spotdl")
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"}
```

NEW:

```python
import asyncio
import json
import mimetypes
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

HOME = Path.home()
APP_DIR = Path(__file__).parent
SPOTDL_BIN = str(HOME / ".local" / "bin" / "spotdl")
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav"}
CONFIG_PATH = HOME / ".local" / "share" / "spotdl-helper" / "config.json"
DEFAULT_MUSIC_ROOT = HOME / "Music" / "spotdl"


def _load_music_root() -> Path:
    """Reads the configured download folder from CONFIG_PATH, falling back
    to the original hardcoded default when there's no config yet (first
    run) or it's unreadable.
    """
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw = data.get("music_root")
        if raw:
            return Path(raw).expanduser()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return DEFAULT_MUSIC_ROOT


MUSIC_ROOT = _load_music_root()
```

(`mimetypes` and `os` are added here because Task 4 and this task need them
respectively — importing both now avoids touching this import block twice.)

Append at the end of `server.py`, after the existing `/api/reveal` endpoint:

```python
# --------------------------------------------------------------- settings


class SettingsRequest(BaseModel):
    music_root: str


@app.get("/api/settings")
def get_settings():
    return {"music_root": str(MUSIC_ROOT)}


@app.post("/api/settings")
def update_settings(req: SettingsRequest):
    global MUSIC_ROOT
    path = Path(req.music_root).expanduser()
    if not path.is_absolute():
        return JSONResponse({"error": "Cesta musí být absolutní."}, status_code=400)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return JSONResponse({"error": f"Složku nejde vytvořit ({e})."}, status_code=400)
    if not os.access(path, os.W_OK):
        return JSONResponse({"error": "Do složky se nedá zapisovat."}, status_code=400)

    MUSIC_ROOT = path
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"music_root": str(path)}), encoding="utf-8")
    return {"music_root": str(MUSIC_ROOT)}


@app.post("/api/settings/pick-folder")
def pick_folder():
    """Native macOS folder picker via AppleScript — the browser sandbox
    can't hand back an absolute filesystem path itself.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", "POSIX path of (choose folder)"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"path": None}
    if result.returncode != 0:
        return {"path": None}  # user cancelled the dialog
    return {"path": result.stdout.strip()}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_settings.py -v
```

Expected: PASS (5/5).

- [ ] **Step 5: Regression-check the rest of the suite**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: still all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: configurable download path via /api/settings, persisted to config.json"
```

---

### Task 3: Knihovna browse endpoint — backend

**Files:**
- Modify: `server.py` (new section after `/api/library`, before `/api/preview`)
- Test: `tests/test_library_browse.py`

**Interfaces:**
- Consumes: `server.MUSIC_ROOT`, `server.AUDIO_EXTS` (existing).
- Produces: `server._resolve_under_music_root(rel: str) -> Path | None` (Task 4 reuses this for the stream endpoint); `GET /api/library/browse?rel=<str>` → `{"rel": str, "breadcrumb": [{"name","rel"}], "entries": [{"name","type","rel","size_bytes"?}]}` or 404.

- [ ] **Step 1: Write the failing tests**

`tests/test_library_browse.py`:

```python
def test_browse_root_lists_dirs_and_audio_files(client, tmp_music_root):
    (tmp_music_root / "Songs").mkdir()
    (tmp_music_root / "track.mp3").write_bytes(b"x")
    (tmp_music_root / "notes.txt").write_bytes(b"x")  # not audio — must be excluded

    res = client.get("/api/library/browse")

    assert res.status_code == 200
    data = res.json()
    assert data["rel"] == ""
    names = [e["name"] for e in data["entries"]]
    assert names == ["Songs", "track.mp3"]  # dirs first, then files, both alphabetical
    assert data["entries"][0]["type"] == "dir"
    assert data["entries"][1]["type"] == "file"
    assert data["entries"][1]["size_bytes"] == 1


def test_browse_subfolder_returns_breadcrumb(client, tmp_music_root):
    sub = tmp_music_root / "Playlists" / "80s Rock"
    sub.mkdir(parents=True)
    (sub / "song.mp3").write_bytes(b"x")

    res = client.get("/api/library/browse", params={"rel": "Playlists/80s Rock"})

    data = res.json()
    assert data["rel"] == "Playlists/80s Rock"
    assert [b["name"] for b in data["breadcrumb"]] == [tmp_music_root.name, "Playlists", "80s Rock"]
    assert data["entries"][0]["rel"] == "Playlists/80s Rock/song.mp3"


def test_browse_rejects_path_traversal_with_dotdot(client, tmp_music_root):
    res = client.get("/api/library/browse", params={"rel": "../../../etc"})
    assert res.status_code == 404


def test_browse_rejects_absolute_path_injection(client, tmp_music_root):
    # Path("/root") / "/etc" collapses to just "/etc" in plain pathlib — the
    # guard has to catch this explicitly, not just check for "..".
    res = client.get("/api/library/browse", params={"rel": "/etc"})
    assert res.status_code == 404


def test_browse_missing_folder_returns_404(client, tmp_music_root):
    res = client.get("/api/library/browse", params={"rel": "does-not-exist"})
    assert res.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_library_browse.py -v
```

Expected: FAIL — endpoint doesn't exist (404 on all, but the traversal
tests will "pass" for the wrong reason since a nonexistent route also
404s; that's fine, they'll be re-verified once the route exists in Step 4).

- [ ] **Step 3: Implement**

Insert into `server.py` right after the existing `/api/library` endpoint
(before the `/api/preview` endpoint and its `# --- preview stream` comment):

```python
# ----------------------------------------------------------------- browse (local files)


def _resolve_under_music_root(rel: str) -> Optional[Path]:
    """Resolves `rel` under MUSIC_ROOT, refusing anything that would escape
    it — a leading '/' (pathlib silently drops the left side when you `/`
    it with an absolute path, so this has to be checked explicitly), '..'
    segments, or a symlink pointing outside. Returns None on any violation.
    """
    if not rel:
        return MUSIC_ROOT.resolve()
    if Path(rel).is_absolute():
        return None
    candidate = (MUSIC_ROOT / rel).resolve()
    root = MUSIC_ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


@app.get("/api/library/browse")
def library_browse(rel: str = ""):
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_dir():
        return JSONResponse({"error": "Složka nenalezena."}, status_code=404)

    root = MUSIC_ROOT.resolve()
    parts = target.relative_to(root).parts
    breadcrumb = [{"name": root.name or str(root), "rel": ""}]
    acc = []
    for part in parts:
        acc.append(part)
        breadcrumb.append({"name": part, "rel": "/".join(acc)})

    dirs, files = [], []
    for p in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        entry_rel = "/".join((*parts, p.name))
        if p.is_dir():
            dirs.append({"name": p.name, "type": "dir", "rel": entry_rel})
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append({
                "name": p.name, "type": "file", "rel": entry_rel,
                "size_bytes": p.stat().st_size,
            })

    return {"rel": "/".join(parts), "breadcrumb": breadcrumb, "entries": dirs + files}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_library_browse.py -v
```

Expected: PASS (5/5).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: /api/library/browse with a path-traversal-safe resolver"
```

---

### Task 4: Local file streaming with Range support — backend

**Files:**
- Modify: `server.py` (right after `_resolve_under_music_root`/`library_browse`)
- Test: `tests/test_library_stream.py`

**Interfaces:**
- Consumes: `server._resolve_under_music_root` (Task 3).
- Produces: `server._range_file_response(path: Path, range_header: str | None) -> StreamingResponse`; `GET /api/library/stream?rel=<str>` (used by the frontend `<audio>`/`<video>` `src`).

- [ ] **Step 1: Write the failing tests**

`tests/test_library_stream.py`:

```python
def test_stream_full_file_without_range_header(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get("/api/library/stream", params={"rel": "song.mp3"})

    assert res.status_code == 200
    assert res.content == b"0123456789"
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["content-length"] == "10"


def test_stream_partial_range_returns_206_with_requested_bytes(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=2-4"}
    )

    assert res.status_code == 206
    assert res.content == b"234"
    assert res.headers["content-range"] == "bytes 2-4/10"
    assert res.headers["content-length"] == "3"


def test_stream_open_ended_range_returns_rest_of_file(client, tmp_music_root):
    (tmp_music_root / "song.mp3").write_bytes(b"0123456789")

    res = client.get(
        "/api/library/stream", params={"rel": "song.mp3"}, headers={"Range": "bytes=7-"}
    )

    assert res.status_code == 206
    assert res.content == b"789"
    assert res.headers["content-range"] == "bytes 7-9/10"


def test_stream_rejects_path_traversal(client, tmp_music_root):
    res = client.get("/api/library/stream", params={"rel": "../outside.mp3"})
    assert res.status_code == 404


def test_stream_missing_file_returns_404(client, tmp_music_root):
    res = client.get("/api/library/stream", params={"rel": "nope.mp3"})
    assert res.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_library_stream.py -v
```

Expected: FAIL — endpoint doesn't exist yet.

- [ ] **Step 3: Implement**

Insert right after `library_browse` in `server.py`:

```python
def _range_file_response(path: Path, range_header: Optional[str]):
    """Serves a local file with byte-range support so <audio>/<video> can
    seek. Starlette 0.27.0 (pinned in this venv) doesn't implement Range on
    its own FileResponse, so this fills that gap by hand — same idea as the
    existing Range-forwarding in _proxy_audio_response, just reading from
    disk instead of an upstream response.
    """
    size = path.stat().st_size
    start, end = 0, size - 1
    status_code = 200
    if range_header and range_header.startswith("bytes="):
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        if start_s:
            start = int(start_s)
        if end_s:
            end = int(end_s)
        end = min(end, size - 1)
        if start > end:
            return JSONResponse({"error": "Neplatný Range požadavek."}, status_code=416)
        status_code = 206

    length = end - start + 1
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    def body():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "accept-ranges": "bytes",
        "content-length": str(length),
        "cache-control": "no-store",
        "content-type": content_type,
    }
    if status_code == 206:
        headers["content-range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(body(), status_code=status_code, headers=headers)


@app.get("/api/library/stream")
def library_stream(rel: str, request: Request):
    target = _resolve_under_music_root(rel)
    if target is None or not target.is_file():
        return JSONResponse({"error": "Soubor nenalezen."}, status_code=404)
    return _range_file_response(target, request.headers.get("range"))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/test_library_stream.py -v
```

Expected: PASS (5/5).

- [ ] **Step 5: Full backend regression check**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: /api/library/stream with hand-rolled Range support"
```

---

### Task 5: In-app video stream — backend

**Files:**
- Modify: `server.py:286-360` (approx — `_resolve_audio` through the `/api/stream/{video_id}` route; exact old/new blocks below)

**Interfaces:**
- Consumes: `server._STREAM_CACHE` (existing).
- Produces: `server._resolve_video(video_id: str) -> dict`; `server._proxy_media_response(request, video_id, resolve, cache_key, default_content_type) -> Response`; `GET /api/stream-video/{video_id}` (used by the frontend `<video>` `src`). `/api/stream/{video_id}` (existing) keeps its exact external behavior — only its internal cache-key format changes, which is invisible outside this file.

No automated test for this task — it needs a live YouTube ID resolved
through real yt-dlp + a real upstream HTTP fetch, which isn't something
worth mocking up for a personal local tool (see Global Constraints). Manual
verification replaces the usual TDD steps.

- [ ] **Step 1: Refactor `_resolve_audio`'s cache key and add `_resolve_video`**

Modify `server.py`. OLD:

```python
def _resolve_audio(video_id: str):
    """Direct audio-only stream URL — proxied byte-for-byte, supports Range/seeking.

    Deliberately never uses the browser-cookies setting: that setting exists for
    the occasional download that hits YouTube's bot-check, and forwarding it to
    every single preview click made yt-dlp decrypt Chrome's cookies (via macOS
    Keychain) on every play — which is what caused the repeated Keychain popups.
    """
    cached = _STREAM_CACHE.get(video_id)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp

    ydl_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    result = {
        "url": info["url"],
        "headers": info.get("http_headers") or {},
        "expires": time.time() + _STREAM_CACHE_TTL,
    }
    _STREAM_CACHE[video_id] = result
    return result
```

NEW:

```python
def _resolve_audio(video_id: str):
    """Direct audio-only stream URL — proxied byte-for-byte, supports Range/seeking.

    Deliberately never uses the browser-cookies setting: that setting exists for
    the occasional download that hits YouTube's bot-check, and forwarding it to
    every single preview click made yt-dlp decrypt Chrome's cookies (via macOS
    Keychain) on every play — which is what caused the repeated Keychain popups.
    """
    cache_key = ("audio", video_id)
    cached = _STREAM_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp

    ydl_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    result = {
        "url": info["url"],
        "headers": info.get("http_headers") or {},
        "expires": time.time() + _STREAM_CACHE_TTL,
    }
    _STREAM_CACHE[cache_key] = result
    return result


def _resolve_video(video_id: str):
    """Progressive (single file, video+audio combined) stream — good enough
    for the in-app video toggle, not used for downloads. Combined
    progressive formats top out well below 1080p on YouTube these days;
    that's fine here.
    """
    cache_key = ("video", video_id)
    cached = _STREAM_CACHE.get(cache_key)
    if cached and cached["expires"] > time.time():
        return cached

    import yt_dlp

    ydl_opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "best[ext=mp4]/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    result = {
        "url": info["url"],
        "headers": info.get("http_headers") or {},
        "expires": time.time() + _STREAM_CACHE_TTL,
    }
    _STREAM_CACHE[cache_key] = result
    return result
```

- [ ] **Step 2: Share the proxy logic between audio and video**

OLD:

```python
async def _proxy_audio_response(request: Request, video_id: str):
    try:
        info = await asyncio.to_thread(_resolve_audio, video_id)
    except Exception as e:
        return JSONResponse({"error": f"Náhled se nepodařilo najít ({e})"}, status_code=502)

    upstream_headers = dict(info["headers"])
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0)
    try:
        req = client.build_request("GET", info["url"], headers=upstream_headers)
        upstream = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        return JSONResponse({"error": f"Streamování selhalo ({e})"}, status_code=502)

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        _STREAM_CACHE.pop(video_id, None)  # link may have expired — drop it so the next click re-resolves
        return JSONResponse({"error": "YouTube odmítlo přehrání (zkus to znovu)"}, status_code=upstream.status_code)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    out_headers = {"accept-ranges": "bytes", "cache-control": "no-store"}
    for h in ("content-length", "content-range", "content-type"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]
    out_headers.setdefault("content-type", "audio/mp4")

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


@app.get("/api/stream/{video_id}")
async def stream(video_id: str, request: Request):
    return await _proxy_audio_response(request, video_id)
```

NEW:

```python
async def _proxy_media_response(request: Request, video_id: str, resolve, cache_key, default_content_type: str):
    try:
        info = await asyncio.to_thread(resolve, video_id)
    except Exception as e:
        return JSONResponse({"error": f"Náhled se nepodařilo najít ({e})"}, status_code=502)

    upstream_headers = dict(info["headers"])
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=30.0)
    try:
        req = client.build_request("GET", info["url"], headers=upstream_headers)
        upstream = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        return JSONResponse({"error": f"Streamování selhalo ({e})"}, status_code=502)

    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        _STREAM_CACHE.pop(cache_key, None)  # link may have expired — drop it so the next click re-resolves
        return JSONResponse({"error": "YouTube odmítlo přehrání (zkus to znovu)"}, status_code=upstream.status_code)

    async def body():
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    out_headers = {"accept-ranges": "bytes", "cache-control": "no-store"}
    for h in ("content-length", "content-range", "content-type"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]
    out_headers.setdefault("content-type", default_content_type)

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


async def _proxy_audio_response(request: Request, video_id: str):
    return await _proxy_media_response(request, video_id, _resolve_audio, ("audio", video_id), "audio/mp4")


async def _proxy_video_response(request: Request, video_id: str):
    return await _proxy_media_response(request, video_id, _resolve_video, ("video", video_id), "video/mp4")


@app.get("/api/stream/{video_id}")
async def stream(video_id: str, request: Request):
    return await _proxy_audio_response(request, video_id)


@app.get("/api/stream-video/{video_id}")
async def stream_video(video_id: str, request: Request):
    return await _proxy_video_response(request, video_id)
```

- [ ] **Step 3: Manual verification — run the real server**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
```

- [ ] **Step 4: Manual verification — audio endpoint still works (regression)**

```bash
curl -sI "http://127.0.0.1:8799/api/stream/dQw4w9WgXcQ" | head -5
```

Expected: `HTTP/1.1 200 OK` (or `206`), `content-type: audio/mp4` (or
whatever the resolved format's real content-type is — it must start with
`audio/`).

- [ ] **Step 5: Manual verification — new video endpoint works**

```bash
curl -sI "http://127.0.0.1:8799/api/stream-video/dQw4w9WgXcQ" | head -5
```

Expected: `HTTP/1.1 200 OK`, `content-type: video/mp4`.

- [ ] **Step 6: Stop the test server**

```bash
kill %1 2>/dev/null
```

- [ ] **Step 7: Run the automated suite too (regression)**

```bash
"$HOME/.local/venvs/spotdl/bin/python3" -m pytest tests/ -v
```

Expected: all PASS (this task didn't touch anything the automated tests
cover, but confirm nothing broke).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: /api/stream-video, sharing the Range-proxy logic with /api/stream"
```

---

### Task 6: Settings UI — configurable download path (frontend)

**Files:**
- Modify: `static/index.html:280-292` (settings overlay markup)
- Modify: `static/index.html:1480-1496` (settings JS)

**Interfaces:**
- Consumes: `GET/POST /api/settings`, `POST /api/settings/pick-folder` (Task 2).

- [ ] **Step 1: Add the music-root field to the Settings overlay**

Modify `static/index.html`. OLD (inside `#settings-overlay`, right after the
`cookie-file` input, before `<div class="actions">`):

```html
      <div class="section-title">…nebo cesta k exportovanému cookies.txt</div>
      <input type="text" id="cookie-file" placeholder="/Users/.../cookies.txt" style="width:100%">
      <div class="actions">
        <button class="btn" id="settings-save">Uložit</button>
      </div>
```

NEW:

```html
      <div class="section-title">…nebo cesta k exportovanému cookies.txt</div>
      <input type="text" id="cookie-file" placeholder="/Users/.../cookies.txt" style="width:100%">

      <div class="section-title" style="margin-top:18px">📁 Kam se stahují songy</div>
      <div style="display:flex;gap:8px">
        <input type="text" id="music-root-input" placeholder="/Users/…/Music/spotdl" style="flex:1;padding:10px">
        <button class="btn secondary" id="music-root-pick" style="white-space:nowrap">📂 Vybrat</button>
      </div>
      <div id="music-root-error" style="color:var(--danger);font-size:12px;margin-top:4px"></div>

      <div class="actions">
        <button class="btn" id="settings-save">Uložit</button>
      </div>
```

- [ ] **Step 2: Wire it up in JS**

OLD (the whole Settings JS block):

```js
const settingsOverlay = el("settings-overlay");
el("settings-btn").onclick = () => {
  const s = loadSettings();
  el("cookies-browser").value = s.cookies_browser || "";
  el("cookie-file").value = s.cookie_file || "";
  settingsOverlay.hidden = false;
};
el("settings-close").onclick = () => settingsOverlay.hidden = true;
settingsOverlay.addEventListener("click", e => { if(e.target === settingsOverlay) settingsOverlay.hidden = true; });
el("settings-save").onclick = () => {
  saveSettingsToStorage({
    cookies_browser: el("cookies-browser").value || null,
    cookie_file: el("cookie-file").value.trim() || null,
  });
  settingsOverlay.hidden = true;
  showToast("Nastavení uloženo");
};
```

NEW:

```js
const settingsOverlay = el("settings-overlay");
el("settings-btn").onclick = async () => {
  const s = loadSettings();
  el("cookies-browser").value = s.cookies_browser || "";
  el("cookie-file").value = s.cookie_file || "";
  el("music-root-error").textContent = "";
  try{
    const res = await fetch("/api/settings");
    const data = await res.json();
    el("music-root-input").value = data.music_root || "";
  }catch(e){}
  settingsOverlay.hidden = false;
};
el("settings-close").onclick = () => settingsOverlay.hidden = true;
settingsOverlay.addEventListener("click", e => { if(e.target === settingsOverlay) settingsOverlay.hidden = true; });

el("music-root-pick").onclick = async () => {
  const res = await fetch("/api/settings/pick-folder", { method: "POST" });
  const data = await res.json();
  if(data.path) el("music-root-input").value = data.path;
};

el("settings-save").onclick = async () => {
  saveSettingsToStorage({
    cookies_browser: el("cookies-browser").value || null,
    cookie_file: el("cookie-file").value.trim() || null,
  });

  const musicRoot = el("music-root-input").value.trim();
  el("music-root-error").textContent = "";
  if(musicRoot){
    try{
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ music_root: musicRoot }),
      });
      const data = await res.json();
      if(!res.ok){
        el("music-root-error").textContent = data.error || "Nastavení cesty selhalo.";
        return; // keep the overlay open so the user can fix it
      }
    }catch(e){
      el("music-root-error").textContent = "Nastavení cesty selhalo (appka neběží?).";
      return;
    }
  }

  settingsOverlay.hidden = true;
  showToast("Nastavení uloženo");
};
```

- [ ] **Step 3: Manual verification**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
open -a "Google Chrome" --args --app="http://127.0.0.1:8799"
```

In the browser: open ⚙️ Nastavení, confirm the „Kam se stahují songy" field
is prefilled with the current path. Click „📂 Vybrat", pick a folder in the
native dialog, confirm the field updates. Click „Uložit", confirm the toast
appears and the overlay closes. Reopen Settings — the field should still
show the folder you picked (proves it round-tripped through
`/api/settings`, not just left in the input). Try saving a relative path
(e.g. `foo`) — confirm the red error text appears and the overlay stays
open.

```bash
kill %1 2>/dev/null
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Settings UI for the configurable download path"
```

---

### Task 7: Player — local file playback + auto-advancing queue (frontend)

**Files:**
- Modify: `static/index.html:1235-1236` (Player state)
- Modify: `static/index.html:1266` (`play()` — reset queue to itself)
- Modify: `static/index.html:1289` area (new `playLocal`/`playLocalQueue`/`playLocalAt` functions)
- Modify: `static/index.html:1300` (`ended` listener — auto-advance)
- Modify: `static/index.html:1367` (history replay — route local tracks correctly)
- Modify: `static/index.html:1381` (Player's public return)

**Interfaces:**
- Produces: `Player.playLocal({relPath, title, artists, sourcePlaylist?})`,
  `Player.playLocalQueue([{relPath, title, artists}, ...])` — Task 8
  (Knihovna tab) consumes both.
- Consumes: `GET /api/library/stream?rel=...` (Task 4).

- [ ] **Step 1: Add queue state**

OLD:

```js
  let current = null;
  let seeking = false;
```

NEW:

```js
  let current = null;
  let seeking = false;
  let playQueueList = [];  // only non-empty during a playLocalQueue() "Přehrát vše"
  let playQueueIdx = -1;
```

- [ ] **Step 2: Reset the queue whenever a single (non-queue) track plays**

OLD (start of `play()`):

```js
  async function play(track){
    current = track;
    setMeta(`⏳ ${track.title || ""}`, "Hledám…", track.thumbnail);
```

NEW:

```js
  async function play(track){
    playQueueList = [track];
    playQueueIdx = 0;
    current = track;
    setMeta(`⏳ ${track.title || ""}`, "Hledám…", track.thumbnail);
```

(The rest of `play()` — the `/api/preview` lookup, `audio.src`, the two
other `setMeta(...)` calls, `pushHistory(...)` — is unchanged by this
task; Task 9 touches those same lines again for the sourcePlaylist
parameter, so don't be surprised to see this function edited twice across
the two tasks.)

- [ ] **Step 3: Add `playLocal`/`playLocalQueue`/`playLocalAt`**

Insert right after the closing `}` of `play()` (before the
`audio.addEventListener("error", ...)` line):

```js
  function playLocal(track){
    playQueueList = [track];
    return playLocalAt(0);
  }

  function playLocalQueue(tracks){
    if(!tracks.length) return;
    playQueueList = tracks;
    return playLocalAt(0);
  }

  function playLocalAt(idx){
    const track = playQueueList[idx];
    playQueueIdx = idx;
    current = track;
    audio.src = `/api/library/stream?rel=${encodeURIComponent(track.relPath)}`;
    audio.play().catch(()=>{});
    setMeta(track.title || track.relPath, track.artists || "", null);
    pushHistory({...track, source: "local", key: "local:"+track.relPath});
  }
```

- [ ] **Step 4: Auto-advance on `ended`**

OLD:

```js
  audio.addEventListener("ended", () => playBtn.textContent = "▶");
```

NEW:

```js
  audio.addEventListener("ended", () => {
    if(playQueueIdx > -1 && playQueueIdx + 1 < playQueueList.length){
      playLocalAt(playQueueIdx + 1);
    } else {
      playBtn.textContent = "▶";
    }
  });
```

- [ ] **Step 5: Route history replay to the right player for local tracks**

OLD:

```js
      row.querySelector(".play").onclick = () => play(h);
```

NEW:

```js
      row.querySelector(".play").onclick = () => h.source === "local" ? playLocal(h) : play(h);
```

- [ ] **Step 6: Expose the new methods**

OLD:

```js
  return { play };
```

NEW:

```js
  return { play, playLocal, playLocalQueue };
```

- [ ] **Step 7: Manual verification**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
```

Put two short audio files directly under the configured music root (or
`~/Music/spotdl` if you haven't changed it yet), e.g.:

```bash
cp /System/Library/Sounds/Glass.aiff "$HOME/Music/spotdl/a.mp3" 2>/dev/null
cp /System/Library/Sounds/Ping.aiff "$HOME/Music/spotdl/b.mp3" 2>/dev/null
```

In the browser console (`open -a "Google Chrome" --args --app="http://127.0.0.1:8799"`,
then DevTools console):

```js
Player.playLocalQueue([
  {relPath: "a.mp3", title: "A", artists: ""},
  {relPath: "b.mp3", title: "B", artists: ""},
]);
```

Confirm "A" starts playing and the player bar shows its title. Let it play
to the end (or manually seek near the end and let it finish) — confirm "B"
starts automatically without any further clicks. Then click 🕘 (history),
confirm both A and B are listed, and clicking ▶ next to one of them
replays it correctly (not a 404/blank).

```bash
kill %1 2>/dev/null
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: Player.playLocal/playLocalQueue with auto-advance"
```

---

### Task 8: Knihovna tab (frontend)

**Files:**
- Modify: `static/index.html:244-245` (tab button)
- Modify: `static/index.html` CSS block (new `.breadcrumb`/`.fs-*` rules)
- Modify: `static/index.html:338-340` area (new `#library-section` markup)
- Modify: `static/index.html:825-827` (tab click wiring)
- Modify: `static/index.html:828-843` (`setMode`)
- New JS: Knihovna loading/rendering functions

**Interfaces:**
- Consumes: `GET /api/library/browse` (Task 3), `Player.playLocal`/`Player.playLocalQueue` (Task 7).

- [ ] **Step 1: Add the tab button**

OLD:

```html
    <button id="tab-playlists" class="active">Playlisty</button>
    <button id="tab-songs">Písně</button>
```

NEW:

```html
    <button id="tab-playlists" class="active">Playlisty</button>
    <button id="tab-songs">Písně</button>
    <button id="tab-library">📁 Knihovna</button>
```

- [ ] **Step 2: Add CSS**

Insert after the existing `.chip.empty-hint{...}` rule:

```css
  .breadcrumb{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:13px;
    color:var(--sub);margin:6px 0 14px}
  .breadcrumb a{color:var(--sub);cursor:pointer}
  .breadcrumb a:hover{color:var(--accent)}
  .breadcrumb .sep{opacity:.5}
  .breadcrumb .current{color:var(--text);font-weight:600}
  .library-play-all{display:flex;align-items:center;gap:10px;margin-bottom:12px}
  .library-play-all span{font-size:12.5px;color:var(--sub)}
  .fs-list{display:flex;flex-direction:column;gap:2px}
  .fs-row{display:flex;align-items:center;gap:10px;padding:9px 8px;border-radius:9px;cursor:pointer}
  .fs-row:hover{background:var(--card-hi)}
  .fs-row .fs-icon{font-size:18px;width:24px;text-align:center;flex-shrink:0}
  .fs-row .fs-name{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13.5px}
  .fs-row .fs-size{font-size:12px;color:var(--sub);flex-shrink:0}
```

- [ ] **Step 3: Add the section markup**

OLD (inside `<main>`, right after `#browse-section` closes, before the
`#results-title` line):

```html
  </div>

  <div class="section-title" id="results-title" hidden>Výsledky</div>
```

NEW:

```html
  </div>

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

- [ ] **Step 4: Wire the tab click and update `setMode`**

OLD:

```js
el("tab-playlists").onclick = () => setMode("playlists");
el("tab-songs").onclick = () => setMode("songs");

function setMode(mode){
  state.mode = mode;
  el("tab-playlists").classList.toggle("active", mode==="playlists");
  el("tab-songs").classList.toggle("active", mode==="songs");
  el("search-input").placeholder = mode==="playlists"
    ? "Hledej žánr, náladu, éru… (např. '90s hip hop')"
    : "Hledej píseň nebo interpreta…";
  regionToggle.style.display = mode === "playlists" ? "flex" : "none";
  grid.innerHTML = "";
  resultsTitle.hidden = true;
  el("load-more-wrap").hidden = true;
  browseSection.hidden = false;
  renderCustomChips();
  renderFavPlaylists();
  updateArtistSuggestions();
}
```

NEW:

```js
el("tab-playlists").onclick = () => setMode("playlists");
el("tab-songs").onclick = () => setMode("songs");
el("tab-library").onclick = () => setMode("library");

function setMode(mode){
  state.mode = mode;
  el("tab-playlists").classList.toggle("active", mode==="playlists");
  el("tab-songs").classList.toggle("active", mode==="songs");
  el("tab-library").classList.toggle("active", mode==="library");
  el("search-input").disabled = mode === "library";
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

- [ ] **Step 5: Add the loading/rendering functions**

Insert right after the `setMode` function closes:

```js
// ======================================================================
// Knihovna — file-manager style browser over MUSIC_ROOT, plus "Přehrát
// vše" for whatever folder you're looking at (its own throwaway play
// queue — see Player.playLocalQueue — separate from the 📃 download
// playlist).
// ======================================================================
function fsIcon(entry){ return entry.type === "dir" ? "📁" : "🎵"; }

function libraryTrackMeta(name){
  // spotdl's own naming templates are "{artist} - {title}.ext" for
  // playlists/queue, and "{artist}/{title}.ext" (artist already in the
  // path) for the Songs folder — fall back to the bare filename when
  // neither pattern matches.
  const stem = name.replace(/\.[^.]+$/, "");
  const idx = stem.indexOf(" - ");
  if(idx > 0) return { artists: stem.slice(0, idx), title: stem.slice(idx + 3) };
  return { artists: "", title: stem };
}

async function loadLibraryFolder(rel){
  const list = el("library-list");
  list.innerHTML = `<div class="empty" style="padding:24px 10px"><span class="spinner"></span> Načítám…</div>`;
  el("library-play-all").hidden = true;

  let data;
  try{
    const res = await fetch(`/api/library/browse?rel=${encodeURIComponent(rel)}`);
    data = await res.json();
    if(!res.ok) throw new Error(data.error || "Chyba");
  }catch(e){
    list.innerHTML = `<div class="empty" style="padding:24px 10px">Složku se nepodařilo načíst.</div>`;
    return;
  }

  renderLibraryBreadcrumb(data.breadcrumb);
  renderLibraryList(data.entries);
}

function renderLibraryBreadcrumb(breadcrumb){
  const box = el("library-breadcrumb");
  box.innerHTML = "";
  breadcrumb.forEach((b, i) => {
    if(i > 0){
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "›";
      box.appendChild(sep);
    }
    const isLast = i === breadcrumb.length - 1;
    const node = document.createElement(isLast ? "span" : "a");
    node.textContent = b.name;
    if(isLast) node.className = "current";
    else node.onclick = () => loadLibraryFolder(b.rel);
    box.appendChild(node);
  });
}

function renderLibraryList(entries){
  const list = el("library-list");
  list.innerHTML = "";
  if(!entries.length){
    list.innerHTML = `<div class="empty" style="padding:24px 10px">Prázdná složka.</div>`;
    return;
  }

  const tracks = entries.filter(e => e.type === "file");
  if(tracks.length){
    el("library-play-all").hidden = false;
    const totalBytes = tracks.reduce((s,t) => s + (t.size_bytes||0), 0);
    el("library-play-all-summary").textContent = `${tracks.length} skladeb · ~${fmtSize(totalBytes)}`;
    el("library-play-all-btn").onclick = () => Player.playLocalQueue(tracks.map(t => ({
      relPath: t.rel, ...libraryTrackMeta(t.name),
    })));
  }

  entries.forEach(entry => {
    const row = document.createElement("div");
    row.className = "fs-row";
    const sizeLabel = entry.type === "file" ? fmtSize(entry.size_bytes || 0) : "";
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
    list.appendChild(row);
  });
}
```

- [ ] **Step 6: Manual verification**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
open -a "Google Chrome" --args --app="http://127.0.0.1:8799"
```

Click „📁 Knihovna". Confirm you see the root folder's contents (whatever
you downloaded/copied in earlier tasks). Click into a subfolder — confirm
the breadcrumb grows and shows the right path, and clicking an earlier
breadcrumb segment jumps back. Click ▶ on a file row — confirm it plays in
the player bar. If the folder has ≥1 track, confirm „▶ Přehrát vše" is
visible with a correct count/size, and clicking it starts playback and
auto-advances through the folder (per Task 7's `ended` behavior). Confirm
switching to the Playlisty/Písně tabs and back to Knihovna doesn't lose or
corrupt anything (re-loads the root cleanly each time).

```bash
kill %1 2>/dev/null
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: Knihovna tab — file-manager browser + Přehrát vše"
```

---

### Task 9: Player — video toggle + originating playlist (frontend)

**Files:**
- Modify: `static/index.html:355-358` (`pb-meta` markup — add `pb-playlist`)
- Modify: `static/index.html:371-379` (`pb-right` markup — add video button)
- Modify: `static/index.html` (new video drawer markup, near the other drawers)
- Modify: `static/index.html` CSS (new `.pb-playlist` rule)
- Modify: `static/index.html:1258-1264` (`setMeta` — 4th param)
- Modify: `static/index.html` (the 3 `setMeta(...)` call sites inside `play()`, plus `playLocalAt`)
- Modify: `static/index.html` (new video-toggle wiring inside the Player IIFE)
- Modify: `static/index.html:990,1048,1073-1076` (`renderPlaylistPanel`/`trackRow` — thread `sourcePlaylist`)

**Interfaces:**
- Consumes: `GET /api/stream-video/{video_id}`, `GET /api/preview?q=` (existing/Task 5).

- [ ] **Step 1: Add the playlist-origin line to the player bar**

OLD:

```html
    <div class="pb-meta">
      <div class="pb-title" id="pb-title">Nic nehraje</div>
      <div class="pb-artist" id="pb-artist">Vyber píseň a stiskni ▶</div>
    </div>
```

NEW:

```html
    <div class="pb-meta">
      <div class="pb-title" id="pb-title">Nic nehraje</div>
      <div class="pb-artist" id="pb-artist">Vyber píseň a stiskni ▶</div>
      <div class="pb-playlist" id="pb-playlist" hidden></div>
    </div>
```

CSS — insert right after the existing `.pb-artist{...}` rule:

```css
  .pb-playlist{font-size:11px;color:var(--sub);opacity:.75;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;margin-top:1px}
```

- [ ] **Step 2: Add the video button**

OLD:

```html
  <div class="pb-right">
    <div class="pb-volume">
      <span id="pb-mute" title="Ztlumit">🔊</span>
      <input type="range" id="pb-volume" min="0" max="1" step="0.01" value="1" title="Hlasitost">
    </div>
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
    <button class="icon-btn-flat" id="pb-history-btn" title="Historie přehrávání">🕘</button>
    <button class="icon-btn-flat" id="pb-queue-btn" title="Playlist k přehrávači">📃<span class="pb-badge" id="pb-queue-count">0</span></button>
  </div>
  <audio id="pb-audio" hidden></audio>
</div>
```

- [ ] **Step 3: Add the video drawer**

Insert after the `#history-panel` drawer closes (before `#dl-drawer`):

```html
<!-- In-app video for whatever's currently playing — works for both remote
     previews/history (which already carry a videoId) and local library
     tracks (resolved on demand, same trick as the audio preview). -->
<div class="drawer" id="video-drawer" hidden>
  <div class="dl-head">
    <span>🎬 Video</span>
    <button class="close-btn" id="video-close" style="position:static;width:24px;height:24px;font-size:11px">✕</button>
  </div>
  <video id="pb-video" controls style="width:100%;border-radius:8px;background:#000"></video>
</div>
```

- [ ] **Step 4: `setMeta` gains a `sourcePlaylist` param**

OLD:

```js
  function setMeta(title, artist, art){
    titleEl.textContent = title;
    titleEl.title = title;
    artistEl.textContent = artist || "";
    artEl.style.backgroundImage = art ? `url('${art}')` : "";
    artEl.textContent = art ? "" : "🎵";
  }
```

NEW:

```js
  function setMeta(title, artist, art, sourcePlaylist){
    titleEl.textContent = title;
    titleEl.title = title;
    artistEl.textContent = artist || "";
    artEl.style.backgroundImage = art ? `url('${art}')` : "";
    artEl.textContent = art ? "" : "🎵";
    const plEl = el("pb-playlist");
    plEl.textContent = sourcePlaylist ? `z playlistu: ${sourcePlaylist}` : "";
    plEl.hidden = !sourcePlaylist;
  }
```

- [ ] **Step 5: Pass `sourcePlaylist` through at every `setMeta` call site**

In `play()` (three call sites — loading, success, error):

OLD:

```js
    setMeta(`⏳ ${track.title || ""}`, "Hledám…", track.thumbnail);
```

NEW:

```js
    setMeta(`⏳ ${track.title || ""}`, "Hledám…", track.thumbnail, track.sourcePlaylist);
```

OLD:

```js
    if(!videoId){
      setMeta("⚠ Náhled nenalezen", track.title || "", track.thumbnail);
      return;
    }
```

NEW:

```js
    if(!videoId){
      setMeta("⚠ Náhled nenalezen", track.title || "", track.thumbnail, track.sourcePlaylist);
      return;
    }
```

OLD:

```js
    audio.src = `/api/stream/${encodeURIComponent(videoId)}`;
    audio.play().catch(()=>{});
    setMeta(track.title || "", track.artists || "", track.thumbnail);
    pushHistory({...track, videoId});
```

NEW:

```js
    audio.src = `/api/stream/${encodeURIComponent(videoId)}`;
    audio.play().catch(()=>{});
    track.videoId = videoId; // so the 🎬 video button doesn't have to re-resolve it
    setMeta(track.title || "", track.artists || "", track.thumbnail, track.sourcePlaylist);
    pushHistory({...track, videoId});
```

And in `playLocalAt` (Task 7):

OLD:

```js
    setMeta(track.title || track.relPath, track.artists || "", null);
```

NEW:

```js
    setMeta(track.title || track.relPath, track.artists || "", null, track.sourcePlaylist);
```

- [ ] **Step 6: Wire the video button**

Insert inside the Player IIFE, right before the final `return { play,
playLocal, playLocalQueue };`:

```js
  // "🎬 Video" — resolves (and caches on the track) the same video the
  // audio came from, then plays it in a slide-up panel. Opening it pauses
  // the audio (the video stream carries its own audio track); closing it
  // just stops the video — the audio does NOT auto-resume, by design.
  const videoBtn = el("pb-video-btn");
  const videoDrawer = el("video-drawer");
  const videoEl = el("pb-video");

  async function resolveCurrentVideoId(){
    if(!current) return null;
    if(current.videoId) return current.videoId;
    const query = current.spotifyQuery || `${current.artists||""} ${current.title||""}`.trim();
    if(!query) return null;
    try{
      const res = await fetch(`/api/preview?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      if(data.videoId) current.videoId = data.videoId; // cache on the track
      return data.videoId || null;
    }catch(e){ return null; }
  }

  videoBtn.onclick = async () => {
    if(!current){ showToast("Nic nehraje"); return; }
    videoBtn.disabled = true;
    const videoId = await resolveCurrentVideoId();
    videoBtn.disabled = false;
    if(!videoId){ showToast("Video se nepodařilo najít"); return; }
    audio.pause();
    videoEl.src = `/api/stream-video/${encodeURIComponent(videoId)}`;
    videoDrawer.hidden = false;
    videoEl.play().catch(()=>{});
  };
  el("video-close").onclick = () => {
    videoEl.pause();
    videoEl.src = "";
    videoDrawer.hidden = true;
  };
```

- [ ] **Step 7: Thread `sourcePlaylist` from the playlist panel through to `Player.play`**

OLD:

```js
  p.tracks.forEach((t, i) => container.appendChild(trackRow(t, i)));
```

NEW:

```js
  p.tracks.forEach((t, i) => container.appendChild(trackRow(t, i, p.title)));
```

OLD:

```js
function trackRow(t, idx){
```

NEW:

```js
function trackRow(t, idx, sourcePlaylist){
```

OLD:

```js
  playBtn.onclick = () => Player.play({
    key: "yt:"+t.videoId, title: t.title, artists: t.artists,
    thumbnail: t.thumbnail, videoId: t.videoId,
  });
```

NEW:

```js
  playBtn.onclick = () => Player.play({
    key: "yt:"+t.videoId, title: t.title, artists: t.artists,
    thumbnail: t.thumbnail, videoId: t.videoId, sourcePlaylist,
  });
```

- [ ] **Step 8: Manual verification**

```bash
cd "$HOME/.local/share/spotdl-helper/app"
"$HOME/.local/venvs/spotdl/bin/python3" -m uvicorn server:app --host 127.0.0.1 --port 8799 &
sleep 2
open -a "Google Chrome" --args --app="http://127.0.0.1:8799"
```

Open a playlist, play a track from its track list — confirm the player bar
shows a small „z playlistu: <jméno>" line under the artist. Play a track
from plain song search (no playlist context) — confirm that line is gone
(not just empty — actually hidden, no leftover gap). Click 🎬 — confirm the
video drawer opens and plays. Close it — confirm audio does not
auto-resume (matches the agreed behavior). Go to Knihovna, play a local
track, click 🎬 — confirm it resolves a video for it too (this is the path
with no pre-existing `videoId`, exercising `resolveCurrentVideoId`'s
`spotifyQuery`-less branch built from `artists`/`title`).

```bash
kill %1 2>/dev/null
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: in-app video toggle + originating playlist shown in the player"
```

---

## Plan Self-Review

**Spec coverage** (against `docs/specs/2026-09-12-library-and-player-design.md`):
1. Configurable download path (textbox + native picker, persisted) → Task 2 (backend) + Task 6 (frontend). ✅
2. Knihovna browser (breadcrumb + list, navigate, Přehrát vše) → Task 3 (backend) + Task 8 (frontend). ✅
3. Play already-downloaded songs and whole playlist folders → Task 7 (`playLocal`/`playLocalQueue` + auto-advance) + Task 4 (streaming with seek support). ✅
4. 🎬 Video button, works for both remote and local tracks → Task 5 (backend) + Task 9 (frontend). ✅
5. Originating playlist shown on the currently playing track → Task 9. ✅

**Placeholder scan:** no TBD/TODO; every step has concrete code, not a
description of code.

**Type/name consistency check:** `relPath`/`title`/`artists`/`sourcePlaylist`
are the field names used consistently from Task 7 through Task 9 (Knihovna
building track objects, Player consuming them, history round-tripping
them). `Player.playLocal`/`Player.playLocalQueue` are named identically in
their Task 7 definition and their Task 8/9 call sites. Backend
`_resolve_under_music_root` is defined once in Task 3 and reused unchanged
in Task 4.
