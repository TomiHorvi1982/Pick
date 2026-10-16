"""Virtuální kytara — riffs the player writes or pastes as a tab, kept to be played again
(static/vguitar.js plays them, in the sound of a song's tone print or a clean guitar).

A riff: {id, name, bpm, div (steps a beat), tuning [high e … low E, MIDI], steps, cols:
[{"s": {string: fret | "x"}, "pm": bool, "lg": [strings played legato]}], sound {song, pid},
band [{name, role, tuning, cols, on}] — the guitars that play with it (a riff from riffbook.py), part {name, role}}.

Tabs to take riffs from: Ultimate Guitar's own pages read as a browser reads them (the state
each page carries, as github.com/crmne/omarchy-ultimate-guitar does), or through Freetar
(github.com/kmille/freetar, its public instance freetar.de) when Ultimate Guitar does not answer;
opened as plain text under the guitar, and kept in "Moje taby" (vguitar-tabs.json). Searched and browsed
with Ultimate Guitar's own filters (its Explore page's left column); its paid kinds (Pro, Official) are
listed too, to be opened on its website in the player's own logged-in browser.
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

from fastapi import APIRouter, Request
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


def _cols(raw) -> list:
    cols = []
    for c in (raw or [])[:MAX_STEPS]:
        notes = {}
        for k, v in (c.get("s") or {}).items():
            i = int(k)
            if 0 <= i < 8 and (v == "x" or (isinstance(v, int) and 0 <= v <= 30)):
                notes[str(i)] = v
        cols.append({"s": notes, "pm": bool(c.get("pm")), "lg": [int(x) for x in (c.get("lg") or []) if 0 <= int(x) < 8]})
    return cols


def _tuning(raw) -> Optional[list]:
    t = [int(x) for x in raw or []][:8]
    return t if 4 <= len(t) <= 8 and all(20 <= x <= 80 for x in t) else None


def clean(data: dict) -> Optional[dict]:
    """A riff as the page sent it, kept only in its known shape. "band": the guitars that play with it
    (a riff from the catalog: the rhythm with its lead, two guitars each their own), played along."""
    try:
        cols = _cols(data.get("cols"))
        tuning = _tuning(data.get("tuning"))
        if not cols or not tuning:
            return None
        band = []
        for b in (data.get("band") or [])[:5]:
            bt, bc = _tuning(b.get("tuning")), _cols(b.get("cols"))
            if bt and bc:
                band.append({"name": " ".join(str(b.get("name") or "").split())[:60] or "Kytara", "role": str(b.get("role") or "")[:12],
                             "tuning": bt, "cols": bc, "on": b.get("on") is not False})
        snd = data.get("sound") or {}
        part = data.get("part") or {}
        return {"bpm": max(30, min(300, int(data.get("bpm") or 120))), "div": int(data.get("div") or 4) if int(data.get("div") or 4) in (2, 3, 4, 6, 8) else 4,
                "tuning": tuning, "cols": cols, "band": band,
                "part": {"name": " ".join(str(part.get("name") or "").split())[:60], "role": str(part.get("role") or "")[:12]} if part.get("name") else None,
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


SKIP_TYPES = {"Pro", "Official", "Video", "MuseScore"}                # Ultimate Guitar's own paid or player-only kinds: shown there, not as text here
KIND = {"Bass Tabs": "Bass", "Drum Tabs": "Drums", "Ukulele Chords": "Ukulele"}   # named as Freetar names them
DIFF = {"novice": "začátečník", "intermediate": "mírně pokročilý", "advanced": "pokročilý"}


def _row(r: dict) -> dict:
    kind = r.get("type") or ""
    return {"url": urllib.parse.urlparse(r["tab_url"]).path, "href": r["tab_url"], "artist": r.get("artist_name") or "", "song": r.get("song_name") or "",
            "version": int(r.get("version") or 1), "rating": round(float(r.get("rating") or 0), 1), "votes": int(r.get("votes") or 0),
            "type": KIND.get(kind, kind), "web": kind in SKIP_TYPES or r.get("tab_access_type") not in (None, "", "public"),
            "difficulty": r.get("difficulty") or "", "key": r.get("tonality_name") or "", "part": r.get("part") or ""}


def ug_search(page: str) -> dict:
    data = _ug_store(page)
    rows = [_row(r) for r in data.get("results") or [] if r.get("tab_url") and not r.get("marketing_type")]
    pg = data.get("pagination") or {}
    return {"results": rows, "pages": int(pg.get("total") or 1)}


# ---- Ultimate Guitar's own filters (the left column of its Explore page), and its Explore itself
UG_EXPLORE = "https://www.ultimate-guitar.com/explore"
GROUPS = {"type": "Druh", "difficulty": "Obtížnost", "tuning": "Ladění", "tonality": "Tónina", "decade": "Dekáda", "genres": "Žánr",
          "subgenres": "Styl", "capo": "Capo", "instruments": "Nástroj", "acoustic": "Akustická", "part": "Část", "live": "Verze"}
NAMES = {"Chords": "Akordy", "Official Tab": "Official (jen na UG)", "Tab": "Taby", "Ukulele Chords": "Ukulele",
         "Guitar Pro Tab": "Guitar Pro (jen na UG)", "Bass Tab": "Basa", "Power Tab": "Power Tab", "Video Tab": "Video (jen na UG)", "Drum Tab": "Bicí", "Musescore": "MuseScore (jen na UG)",
         "Absolute Beginner": "úplný začátečník", "Beginner": "začátečník", "Intermediate": "mírně pokročilý", "Advanced": "pokročilý",
         "No capo": "bez capa", "With capo": "s capem", "Non-acoustic": "elektrická", "Acoustic": "akustická",
         "Whole song": "celá píseň", "Intro": "intro", "Solo": "sólo", "Album": "celé album", "Original": "studiová", "Live": "živá"}
ORDERS = {"hitstotal_desc": "Nejhranější všech dob", "hitsdailygroup_desc": "Dnes nejhranější", "date_desc": "Nejnovější",
          "rating_desc": "Nejlépe hodnocené", "songname_asc": "Píseň A–Z", "artistname_asc": "Kapela A–Z"}
SEARCH_ORDERS = {"myweight": "Nejlépe odpovídá", "title_srt": "Abecedně", "rating": "Nejlépe hodnocené"}
SEARCH_TYPE = {"Chords": 300, "Official": 900, "Tabs": 200, "Ukulele Chords": 800, "Pro": 500, "Bass Tabs": 400, "Power": 600,
               "Video": 100, "Drum Tabs": 700}
ON_RESULTS = {"type", "difficulty", "tonality", "part"}   # what a search by text can still be narrowed by (the rest only browsing)
DIFF_OF = {"1": "novice", "2": "novice", "3": "intermediate", "4": "advanced"}


def ug_filters(page: str) -> dict:
    data = _ug_store(page)
    groups = []
    for f in sorted(data.get("filters") or [], key=lambda f: f.get("sort_index", 0)):
        p = f.get("param_name")
        if p not in GROUPS:
            continue
        vals = [{"v": str(v.get("url_name")), "name": NAMES.get(str(v.get("name") or "").strip(), str(v.get("name") or "").strip()),
                 "count": int(v.get("count") or 0)} for v in f.get("values") or [] if v.get("url_name") is not None and str(v["url_name"]) != ""]
        groups.append({"param": p, "name": GROUPS[p], "values": vals[:60], "onSearch": p in ON_RESULTS})
    return {"groups": groups, "orders": [{"v": k, "name": n} for k, n in ORDERS.items()],
            "searchOrders": [{"v": k, "name": n} for k, n in SEARCH_ORDERS.items()]}


def ug_explore(page: str) -> dict:
    data = _ug_store(page)
    tabs = (data.get("data") or {}).get("tabs") or []
    pg = data.get("pagination") or {}
    return {"results": [_row(r) for r in tabs if r.get("tab_url")], "pages": int(pg.get("pages") or 1), "total": int(data.get("totalResults") or 0)}


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


FILTERS_FILE = "ug-filters.json"


@router.get("/api/vguitar/tabs/filters")
def tabs_filters():
    """Ultimate Guitar's filters, as its Explore page lists them — kept a week, and kept when it does not answer."""
    cache = TAB_CACHE / FILTERS_FILE
    old = None
    try:
        old = json.loads(cache.read_text(encoding="utf-8"))
        if time.time() - old.get("at", 0) < 7 * 86400:
            return old
    except (OSError, ValueError):
        pass
    try:
        got = {**ug_filters(_get(UG_EXPLORE, 12)), "at": time.time()}
    except Exception:                                   # noqa: BLE001
        if old:
            return old
        return JSONResponse({"error": "Filtry Ultimate Guitar se teď nepodařilo načíst."}, status_code=502)
    TAB_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return got


def _chosen(request: Request) -> dict:
    """The filters asked for: {param: [values]}, only Ultimate Guitar's own and only plain values."""
    out = {}
    for p in GROUPS:
        vals = [v for v in request.query_params.getlist(p) if re.fullmatch(r"[\w &.\-]{1,40}", v)][:20]
        if vals:
            out[p] = vals
    return out


def _narrow(rows: list, chosen: dict) -> list:
    """A text search's results narrowed by what they say of themselves (Ultimate Guitar's search filters only by kind)."""
    want_d = {DIFF_OF.get(v) for v in chosen.get("difficulty", [])} - {None}
    keys = set(chosen.get("tonality", []))
    names = {}
    if keys:
        try:
            g = next((g for g in tabs_filters()["groups"] if g["param"] == "tonality"), {"values": []})
            names = {v["v"]: v["name"] for v in g["values"]}
        except Exception:                               # noqa: BLE001
            pass
    want_k = {names.get(k, k) for k in keys}
    parts = set(chosen.get("part", []))
    return [r for r in rows if (not want_d or r["difficulty"] in want_d) and (not want_k or r["key"] in want_k)
            and (not parts or r["part"] in parts)]


@router.get("/api/vguitar/tabs/search")
def tabs_search(request: Request, q: str = "", page: int = 1, order: str = ""):
    q = " ".join(q.split())[:120]
    chosen = _chosen(request)
    if q and len(q) < 2:
        return JSONResponse({"error": "Napiš aspoň dvě písmena."}, status_code=400)
    page = max(1, min(50, page))
    key = (q.lower(), page, order, json.dumps(chosen, sort_keys=True))
    hit = _SEARCHED.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    if not q:                                           # no text: Ultimate Guitar's Explore, every filter applied there
        params = [(p + "[]", v) for p, vals in chosen.items() for v in vals]
        params += [("order", order if order in ORDERS else "hitstotal_desc"), ("page", page)]
        try:
            got = ug_explore(_get(f"{UG_EXPLORE}?" + urllib.parse.urlencode(params), 12))
        except Exception:                               # noqa: BLE001
            return JSONResponse({"error": "Ultimate Guitar teď neodpovídá — zkus to za chvilku."}, status_code=502)
        got.update(via="Ultimate Guitar", q=q, page=page, browse=True)
        _SEARCHED[key] = (time.time(), got)
        return got
    try:
        params = [("search_type", "title"), ("value", q), ("page", page)]
        params += [("type[]", SEARCH_TYPE[t]) for t in chosen.get("type", []) if t in SEARCH_TYPE]
        if order == "rating":
            params += [("rating[]", 4), ("rating[]", 5), ("order", "myweight")]
        elif order in SEARCH_ORDERS:
            params.append(("order", order))
        got = ug_search(_get(f"{UG_SEARCH}?" + urllib.parse.urlencode(params), 12))
        got["results"] = _narrow(got["results"], chosen)
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
            got["via"] = "Freetar (bez filtrů)" if chosen else "Freetar"
        except Exception:                               # noqa: BLE001 — the far side, said simply
            return JSONResponse({"error": "Ultimate Guitar ani Freetar teď neodpovídají — zkus to za chvilku."}, status_code=502)
    got.update(q=q, page=page)
    _SEARCHED[key] = (time.time(), got)
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
