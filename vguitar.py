"""Virtuální kytara — riffs the player writes or pastes as a tab, kept to be played again
(static/vguitar.js plays them, in the sound of a song's tone print or a clean guitar).

A riff: {id, name, bpm, div (steps a beat), tuning [high e … low E, MIDI], steps, cols:
[{"s": {string: fret | "x"}, "pm": bool, "lg": [strings played legato]}], sound {song, pid}}.

Tabs to take riffs from: Ultimate Guitar's own pages read as a browser reads them (the state
each page carries, as github.com/crmne/omarchy-ultimate-guitar does), or through Freetar
(github.com/kmille/freetar, its public instance freetar.de) when Ultimate Guitar does not answer;
opened as plain text under the guitar, and kept in "Moje taby" (vguitar-tabs.json).
"""
import gzip
import hashlib
import html as _html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
FILE = Path.home() / ".local" / "share" / "spotdl-helper" / "vguitar.json"
MAX_STEPS = 1024


def riffs() -> list:
    try:
        got = json.loads(FILE.read_text(encoding="utf-8"))
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def clean(data: dict) -> Optional[dict]:
    """A riff as the page sent it, kept only in its known shape."""
    try:
        cols = []
        for c in (data.get("cols") or [])[:MAX_STEPS]:
            notes = {}
            for k, v in (c.get("s") or {}).items():
                i = int(k)
                if 0 <= i < 8 and (v == "x" or (isinstance(v, int) and 0 <= v <= 30)):
                    notes[str(i)] = v
            cols.append({"s": notes, "pm": bool(c.get("pm")), "lg": [int(x) for x in (c.get("lg") or []) if 0 <= int(x) < 8]})
        tuning = [int(x) for x in data.get("tuning") or []][:8]
        if not cols or not (4 <= len(tuning) <= 8) or not all(20 <= t <= 80 for t in tuning):
            return None
        snd = data.get("sound") or {}
        return {"bpm": max(30, min(300, int(data.get("bpm") or 120))), "div": int(data.get("div") or 4) if int(data.get("div") or 4) in (2, 3, 4, 6, 8) else 4,
                "tuning": tuning, "cols": cols,
                "sound": {"song": str(snd.get("song") or "")[:300], "pid": str(snd.get("pid") or "")[:60]} if snd.get("song")
                else {"kontakt": str(snd["kontakt"])[:10]} if re.fullmatch(r"[0-9a-f]{10}", str(snd.get("kontakt") or ""))
                else {"midi": str(snd["midi"])[:200]} if snd.get("midi") else None}
    except (TypeError, ValueError, AttributeError):
        return None


@router.get("/api/vguitar/riffs")
def riff_list():
    return {"riffs": sorted(riffs(), key=lambda r: -r.get("changed", 0))}


class RiffIn(BaseModel):
    id: str = ""
    name: str = ""
    riff: dict = {}


@router.post("/api/vguitar/riffs")
def riff_save(req: RiffIn):
    body = clean(req.riff)
    if not body:
        return JSONResponse({"error": "Riff je prázdný nebo se nedá přečíst."}, status_code=400)
    name = " ".join((req.name or "").split())[:80] or "Riff"
    rows = riffs()
    row = next((r for r in rows if req.id and r.get("id") == req.id), None)
    if row is None:
        row = {"id": uuid.uuid4().hex[:10], "created": time.time()}
        rows.append(row)
    row.update(body, name=name, changed=time.time())
    _save(rows)
    return row


@router.delete("/api/vguitar/riffs/{rid}")
def riff_delete(rid: str):
    rows = riffs()
    if not any(r.get("id") == rid for r in rows):
        return JSONResponse({"error": "Riff nenalezen."}, status_code=404)
    _save([r for r in rows if r.get("id") != rid])
    return {"ok": True}


# ---------------------------------------------------------------- tabs to take riffs from
TABS_FILE = Path.home() / ".local" / "share" / "spotdl-helper" / "vguitar-tabs.json"
TAB_CACHE = Path.home() / ".local" / "share" / "spotdl-helper" / "tab-cache"
FREETAR = "https://freetar.de"
UA = "Pick/1.0 (https://github.com/TomiHorvi1982/Pick; personal music app)"
_SEARCHED: dict = {}                                    # (q, page) → (time, result)


UG_SEARCH = "https://www.ultimate-guitar.com/search.php"
UG_TABS = "https://tabs.ultimate-guitar.com"
UG_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36", "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}


def _get(url: str, timeout: float = 75) -> str:
    ug = urllib.parse.urlparse(url).hostname.endswith("ultimate-guitar.com")
    req = urllib.request.Request(url, headers=UG_HEADERS if ug else {"User-Agent": UA})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(3_000_000)
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:              # their gateway now and then says 502 — once more, a moment later
            if e.code < 500 or attempt == 1 or not ug:
                raise
            time.sleep(1.5)
    raise OSError("unreachable")


def _ug_store(page: str) -> dict:
    m = re.search(r'<div class="js-store" data-content="(.*?)"></div>', page, re.S)
    if not m:
        raise ValueError("no page state")
    return json.loads(_html.unescape(m.group(1)))["store"]["page"]["data"]


SKIP_TYPES = {"Pro", "Official", "Video"}                # Ultimate Guitar's own paid or player-only kinds
KIND = {"Bass Tabs": "Bass", "Drum Tabs": "Drums", "Ukulele Chords": "Ukulele"}   # named as Freetar names them


def ug_search(page: str) -> dict:
    data = _ug_store(page)
    rows = []
    for r in data.get("results") or []:
        kind = r.get("type") or ""
        if not r.get("tab_url") or kind in SKIP_TYPES or r.get("marketing_type"):
            continue
        rows.append({"url": urllib.parse.urlparse(r["tab_url"]).path, "artist": r.get("artist_name") or "", "song": r.get("song_name") or "",
                     "version": int(r.get("version") or 1), "rating": round(float(r.get("rating") or 0), 1), "votes": int(r.get("votes") or 0),
                     "type": KIND.get(kind, kind)})
    pg = data.get("pagination") or {}
    return {"results": rows, "pages": int(pg.get("total") or 1)}


def ug_tab(page: str) -> dict:
    data = _ug_store(page)
    tab, view = data["tab"], data["tab_view"]
    text = view["wiki_tab"]["content"].replace("\r\n", "\n")
    text = re.sub(r"\[/?(tab|ch)\]", "", text)
    meta = view.get("meta") if isinstance(view.get("meta"), dict) else {}
    tun = meta.get("tuning") or {}
    versions = [{"url": urllib.parse.urlparse(v["tab_url"]).path, "label": f"Verze {v.get('version')} ({v.get('type')}) {round(float(v.get('rating') or 0), 1)}/5 ({v.get('votes') or 0})"}
                for v in view.get("versions") or [] if v.get("tab_url") and v.get("type") not in SKIP_TYPES]
    return {"artist": tab.get("artist_name") or "", "song": tab.get("song_name") or "", "text": text.strip("\n"),
            "tuning": f"{tun.get('value')} ({tun.get('name')})" if tun.get("value") else "", "versions": versions[:30]}


def _text(h: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", h)).replace("\xa0", " ").strip()


def parse_search(page: str) -> dict:
    """Freetar's search page: a row a tab — artist, song (ver n), rating (votes), type."""
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        link = re.search(r'<td class="song"><a href="(/tab/[^"]+)">(.*?)</a>', tr, re.S)
        if not link:
            continue
        artist = re.search(r'<td class="artist">(.*?)</td>', tr, re.S)
        rating = re.search(r'<td class="rating" data-value="([\d.]+)">[^(]*\((\d+)\)', tr)
        kind = re.search(r'<td class="type">(.*?)</td>', tr, re.S)
        song = _text(link.group(2))
        ver = re.search(r"\(ver (\d+)\)\s*$", song)
        rows.append({"url": link.group(1), "artist": _text(artist.group(1)) if artist else "",
                     "song": re.sub(r"\s*\(ver \d+\)\s*$", "", song), "version": int(ver.group(1)) if ver else 1,
                     "rating": float(rating.group(1)) if rating else 0.0, "votes": int(rating.group(2)) if rating else 0,
                     "type": _text(kind.group(1)) if kind else ""})
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", page)]
    return {"results": rows, "pages": max(pages + [1])}


def parse_tab(page: str) -> dict:
    """A Freetar tab page: its title and the tab itself as text (line breaks kept, the chords plain)."""
    m = re.search(r'<div class="tab font-monospace">(.*?)</div>', page, re.S)
    if not m:
        raise ValueError("no tab")
    body = re.sub(r"<br\s*/?>", "\n", re.sub(r"[\r\n]", "", m.group(1)))     # its spaces are &nbsp;, its breaks <br>
    body = re.sub(r'<a [^>]*class="__cf_email__"[^>]*>.*?</a>', "", body, flags=re.S)  # an address hidden from robots
    text = "\n".join(line.rstrip() for line in _text(body).split("\n"))
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    head = _text(title.group(1)) if title else ""
    artist, _, song = head.partition(" - ")
    versions = [{"url": u, "label": _text(t)} for u, t in re.findall(r'<li><a href="(/tab/[^"]+)">(.*?)</a></li>', page, re.S)]
    return {"artist": artist.strip(), "song": song.strip() or head, "text": text.strip("\n"), "versions": versions[:30]}


def _tab_path(url: str) -> str:
    path = urllib.parse.urlparse(url).path if url.startswith("http") else url
    if not re.fullmatch(r"/tab/[\w.\-%]+/[\w.\-%]+", path or ""):
        raise ValueError("bad tab path")
    return path


@router.get("/api/vguitar/tabs/search")
def tabs_search(q: str = "", page: int = 1):
    q = " ".join(q.split())[:120]
    if len(q) < 2:
        return JSONResponse({"error": "Napiš aspoň dvě písmena."}, status_code=400)
    page = max(1, min(50, page))
    hit = _SEARCHED.get((q.lower(), page))
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    try:
        got = ug_search(_get(f"{UG_SEARCH}?" + urllib.parse.urlencode({"search_type": "title", "value": q, "page": page}), 12))
        got["via"] = "Ultimate Guitar"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            got = None
        else:                                           # nothing matched: Ultimate Guitar says it with a 404
            got = {"results": [], "pages": 1, "via": "Ultimate Guitar"}
    except Exception:                                   # noqa: BLE001
        got = None
    if got is None:
        try:
            got = parse_search(_get(f"{FREETAR}/search?" + urllib.parse.urlencode({"search_term": q, "page": page})))
            got["via"] = "Freetar"
        except Exception:                               # noqa: BLE001 — the far side, said simply
            return JSONResponse({"error": "Ultimate Guitar ani Freetar teď neodpovídají — zkus to za chvilku."}, status_code=502)
    got.update(q=q, page=page)
    _SEARCHED[(q.lower(), page)] = (time.time(), got)
    return got


@router.get("/api/vguitar/tabs/open")
def tabs_open(url: str):
    try:
        path = _tab_path(url)
    except ValueError:
        return JSONResponse({"error": "Neplatný odkaz na tab."}, status_code=400)
    cache = TAB_CACHE / (hashlib.sha1(path.encode()).hexdigest()[:20] + ".json")
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        got = ug_tab(_get(UG_TABS + path, 12))
    except Exception:                                   # noqa: BLE001 — then through Freetar
        try:
            got = parse_tab(_get(FREETAR + path))
        except ValueError:
            return JSONResponse({"error": "Na té stránce tab není."}, status_code=404)
        except Exception:                               # noqa: BLE001
            return JSONResponse({"error": "Ultimate Guitar ani Freetar teď neodpovídají — zkus to za chvilku."}, status_code=502)
    got["url"] = path
    TAB_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return got


def my_tabs() -> list:
    try:
        got = json.loads(TABS_FILE.read_text(encoding="utf-8"))
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


@router.get("/api/vguitar/tabs/mine")
def tabs_mine(q: str = ""):
    words = q.lower().split()
    rows = [t for t in my_tabs() if all(w in f"{t.get('artist', '')} {t.get('song', '')}".lower() for w in words)]
    return {"tabs": sorted(rows, key=lambda t: (t.get("artist", "").lower(), t.get("song", "").lower()))}


class TabIn(BaseModel):
    artist: str = ""
    song: str = ""
    text: str = ""
    url: str = ""


@router.post("/api/vguitar/tabs/mine")
def tabs_keep(req: TabIn):
    text = (req.text or "").replace("\r\n", "\n")[:200_000]
    if not text.strip():
        return JSONResponse({"error": "Tab je prázdný."}, status_code=400)
    rows = my_tabs()
    url = req.url if re.fullmatch(r"/tab/[\w.\-%]+/[\w.\-%]+", req.url or "") else ""
    row = next((t for t in rows if url and t.get("url") == url), None)
    if row is None:
        row = {"id": uuid.uuid4().hex[:10], "added": time.time()}
        rows.append(row)
    row.update(artist=" ".join(req.artist.split())[:120], song=" ".join(req.song.split())[:160] or "Tab", text=text, url=url)
    TABS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TABS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(TABS_FILE)
    return row


@router.delete("/api/vguitar/tabs/mine/{tid}")
def tabs_forget(tid: str):
    rows = my_tabs()
    if not any(t.get("id") == tid for t in rows):
        return JSONResponse({"error": "Tab nenalezen."}, status_code=404)
    tmp = TABS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([t for t in rows if t.get("id") != tid], ensure_ascii=False), encoding="utf-8")
    tmp.replace(TABS_FILE)
    return {"ok": True}
