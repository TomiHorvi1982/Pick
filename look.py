"""The look of the app as the player made it — Úpravy vzhledu.

The page's design mode changes colours, type, backgrounds and spacing of
anything on screen and shows it at once; what is kept lands here: one JSON of
the look in use, named looks beside it, and the pictures it uses (the
player's own, or ones taken from the free sources below) as plain files, so
the look still works with no internet.

Free sources, none of which needs a key:
  fonts   — Google Fonts (the whole catalogue), Fontshare
  images  — Openverse (CC), Wikimedia Commons, Lorem Picsum (Unsplash),
            Art Institute of Chicago (public domain), NASA
  icons   — Iconify (open icon sets)
"""
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

HOME = Path.home()
BASE = HOME / ".local" / "share" / "spotdl-helper"
LOOK_PATH = BASE / "look.json"
LOOK_DIR = BASE / "look"                 # pictures; kept/ = named looks; fonts.json = the catalogue
UA = "Pick/1.0 (local music practice app)"
LOOK_MAX = 2 * 1024 * 1024
IMAGE_MAX = 30 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "image/avif": ".avif", "image/svg+xml": ".svg"}
_NAME = re.compile(r"^[a-z0-9]{8,40}\.(?:jpg|png|webp|gif|avif|svg)$")
_KEPT = re.compile(r"[^\w \-.,()+&áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")
FONTS_AGE = 30 * 86400

router = APIRouter()


def _read(path: Path, default):
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        return got if isinstance(got, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _get(url: str, **params):
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = c.get(url, params=params or None)
        r.raise_for_status()
        return r


# ------------------------------------------------------------ the look itself

async def _body(request: Request):
    raw = await request.body()
    if len(raw) > LOOK_MAX:
        return None, JSONResponse({"error": "Vzhled je moc velký."}, status_code=413)
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, JSONResponse({"error": "Tohle není vzhled."}, status_code=400)
    return data, None


@router.get("/api/look")
def look_get():
    return {"look": _read(LOOK_PATH, {}), "kept": kept_names()}


@router.put("/api/look")
async def look_put(request: Request):
    data, bad = await _body(request)
    if bad:
        return bad
    _write(LOOK_PATH, data)
    return {"ok": True}


def kept_names() -> list:
    d = LOOK_DIR / "kept"
    return sorted((p.stem for p in d.glob("*.json")), key=str.lower) if d.is_dir() else []


def _kept_file(name: str) -> Optional[Path]:
    name = _KEPT.sub("", name or "").strip(" .")[:60]
    return LOOK_DIR / "kept" / f"{name}.json" if name else None


@router.get("/api/look/kept/{name}")
def kept_get(name: str):
    f = _kept_file(name)
    if f is None or not f.is_file():
        return JSONResponse({"error": "Takový vzhled tu není."}, status_code=404)
    return {"name": f.stem, "look": _read(f, {})}


@router.put("/api/look/kept/{name}")
async def kept_put(name: str, request: Request):
    f = _kept_file(name)
    if f is None:
        return JSONResponse({"error": "Pojmenuj ho."}, status_code=400)
    data, bad = await _body(request)
    if bad:
        return bad
    _write(f, data)
    return {"ok": True, "name": f.stem, "kept": kept_names()}


@router.delete("/api/look/kept/{name}")
def kept_delete(name: str):
    f = _kept_file(name)
    if f is not None and f.is_file():
        f.unlink()
    return {"ok": True, "kept": kept_names()}


# ------------------------------------------------------------------ pictures

def _notes() -> dict:
    return _read(LOOK_DIR / "images.json", {})


def keep_image(data: bytes, mime: str, note: Optional[dict] = None) -> Optional[dict]:
    """A picture among the look's own files, under a name made of its content."""
    mime = (mime or "").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    ext = IMAGE_TYPES.get(mime)
    if not ext or not data or len(data) > IMAGE_MAX:
        return None
    name = hashlib.sha1(data).hexdigest()[:20] + ext
    LOOK_DIR.mkdir(parents=True, exist_ok=True)
    f = LOOK_DIR / name
    if not f.is_file():
        f.write_bytes(data)
    notes = _notes()
    notes[name] = {**(notes.get(name) or {}), **(note or {}), "at": time.time()}
    _write(LOOK_DIR / "images.json", notes)
    return {"name": name, "url": f"/api/look/image/{name}", **notes[name]}


@router.post("/api/look/image")
async def image_add(file: UploadFile = File(...), kind: str = "own"):
    data = await file.read(IMAGE_MAX + 1)
    got = keep_image(data, file.content_type or "", {"title": (file.filename or "")[:120], "src": "own",
                                                       "kind": kind if kind in ("own", "ref") else "own"})
    if got is None:
        return JSONResponse({"error": "Tohle není obrázek, který umím použít (JPG, PNG, WebP, GIF, AVIF, SVG do 30 MB)."},
                            status_code=400)
    return got


@router.get("/api/look/image/{name}")
def image_get(name: str):
    f = LOOK_DIR / name
    if not _NAME.match(name) or not f.is_file():
        return JSONResponse({"error": "Nenalezeno."}, status_code=404)
    mime = next(m for m, e in IMAGE_TYPES.items() if e == f.suffix)
    # An SVG is only ever a picture here — it must not run anything when opened by itself.
    return FileResponse(f, media_type=mime, headers={
        "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.get("/api/look/images")
def image_list():
    notes = _notes()
    out = []
    for name, n in notes.items():
        if _NAME.match(name) and (LOOK_DIR / name).is_file():
            out.append({"name": name, "url": f"/api/look/image/{name}", **n})
    return sorted(out, key=lambda x: -(x.get("at") or 0))


@router.delete("/api/look/image/{name}")
def image_delete(name: str):
    if _NAME.match(name):
        f = LOOK_DIR / name
        if f.is_file():
            f.unlink()
        notes = _notes()
        if notes.pop(name, None) is not None:
            _write(LOOK_DIR / "images.json", notes)
    return {"ok": True}


# ------------------------------------------------------------- free sources

SOURCES = [
    {"id": "openverse", "name": "Openverse", "what": "fotky a kresby s volnou licencí (CC)", "search": True},
    {"id": "commons", "name": "Wikimedia Commons", "what": "volné obrázky z Wikipedie", "search": True},
    {"id": "picsum", "name": "Lorem Picsum", "what": "fotky z Unsplash — k prolistování", "search": False},
    {"id": "artic", "name": "Art Institute of Chicago", "what": "obrazy, volná díla (CC0)", "search": True},
    {"id": "nasa", "name": "NASA", "what": "vesmír — volné snímky", "search": True},
    {"id": "iconify", "name": "Iconify", "what": "ikony z otevřených sad", "search": True, "icons": True},
]


def _find_openverse(q: str, page: int) -> list:
    d = _get("https://api.openverse.org/v1/images/", q=q, page_size=20, page=page).json()
    return [{"id": x["id"], "thumb": x.get("thumbnail") or x.get("url"), "title": x.get("title") or "",
             "credit": x.get("creator") or "", "license": ("CC " + (x.get("license") or "")).upper().strip(),
             "page": x.get("foreign_landing_url") or "", "w": x.get("width"), "h": x.get("height")}
            for x in d.get("results") or [] if x.get("id")]


def _find_commons(q: str, page: int) -> list:
    d = _get("https://commons.wikimedia.org/w/api.php", action="query", generator="search", gsrnamespace=6,
             gsrsearch=f"{q} filetype:bitmap", gsrlimit=20, gsroffset=(page - 1) * 20, prop="imageinfo",
             iiprop="url|size|extmetadata", iiurlwidth=320, format="json").json()
    out = []
    for p in sorted((d.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        em = ii.get("extmetadata") or {}
        if not ii.get("thumburl"):
            continue
        out.append({"id": str(p["pageid"]), "thumb": ii["thumburl"],
                    "title": re.sub(r"^File:|\.\w+$", "", p.get("title") or ""),
                    "credit": re.sub(r"<[^>]+>", "", (em.get("Artist") or {}).get("value") or "")[:80],
                    "license": (em.get("LicenseShortName") or {}).get("value") or "",
                    "page": ii.get("descriptionurl") or "", "w": ii.get("width"), "h": ii.get("height")})
    return out


def _find_picsum(q: str, page: int) -> list:
    d = _get("https://picsum.photos/v2/list", page=page, limit=30).json()
    return [{"id": str(x["id"]), "thumb": f"https://picsum.photos/id/{x['id']}/320/200", "title": "",
             "credit": x.get("author") or "", "license": "Unsplash", "page": x.get("url") or "",
             "w": x.get("width"), "h": x.get("height")} for x in d]


def _find_artic(q: str, page: int) -> list:
    d = _get("https://api.artic.edu/api/v1/artworks/search", **{
        "q": q, "query[term][is_public_domain]": "true", "fields": "id,title,image_id,artist_title",
        "limit": 20, "page": page}).json()
    return [{"id": x["image_id"], "thumb": f"https://www.artic.edu/iiif/2/{x['image_id']}/full/400,/0/default.jpg",
             "title": x.get("title") or "", "credit": x.get("artist_title") or "", "license": "CC0",
             "page": f"https://www.artic.edu/artworks/{x['id']}"} for x in d.get("data") or [] if x.get("image_id")]


def _find_nasa(q: str, page: int) -> list:
    d = _get("https://images-api.nasa.gov/search", q=q, media_type="image", page_size=20, page=page).json()
    out = []
    for it in (d.get("collection") or {}).get("items") or []:
        meta = (it.get("data") or [{}])[0]
        thumb = next((l.get("href") for l in it.get("links") or [] if l.get("render") == "image"), None)
        if meta.get("nasa_id") and thumb:
            out.append({"id": meta["nasa_id"], "thumb": thumb, "title": meta.get("title") or "",
                        "credit": meta.get("secondary_creator") or meta.get("center") or "NASA", "license": "volné",
                        "page": f"https://images.nasa.gov/details/{meta['nasa_id']}"})
    return out


def _find_iconify(q: str, page: int) -> list:
    d = _get("https://api.iconify.design/search", query=q, limit=96, start=(page - 1) * 96).json()
    sets = d.get("collections") or {}
    return [{"id": i, "thumb": f"https://api.iconify.design/{i.replace(':', '/')}.svg?color=%23e8e8f4",
             "title": i.split(":", 1)[1], "credit": (sets.get(i.split(":", 1)[0]) or {}).get("name") or "",
             "license": ((sets.get(i.split(":", 1)[0]) or {}).get("license") or {}).get("title") or "", "icon": True}
            for i in d.get("icons") or [] if ":" in i]


_FIND = {"openverse": _find_openverse, "commons": _find_commons, "picsum": _find_picsum, "artic": _find_artic,
         "nasa": _find_nasa, "iconify": _find_iconify}


@router.get("/api/look/sources")
def sources():
    return SOURCES


@router.get("/api/look/find")
def find(src: str, q: str = "", page: int = 1):
    fn = _FIND.get(src)
    if fn is None:
        return JSONResponse({"error": "Takový zdroj neznám."}, status_code=404)
    q = (q or "").strip()[:120]
    if not q and src != "picsum":
        return {"items": []}
    try:
        return {"items": fn(q, max(1, min(int(page), 50)))}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return JSONResponse({"error": f"Zdroj teď neodpovídá ({type(e).__name__})."}, status_code=502)


_ICON_ID = re.compile(r"^[a-z0-9-]+:[a-z0-9-]+$")
_PLAIN_ID = re.compile(r"^[\w\-~.]{1,80}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _full_url(src: str, id_: str, color: str = "") -> Optional[str]:
    """Where the picture itself is. Always worked out here from the source's
    own answer — the page only ever names a source and an id."""
    if src == "iconify":
        if not _ICON_ID.match(id_):
            return None
        tint = f"?color=%23{color[1:]}" if _COLOR.match(color or "") else ""
        return f"https://api.iconify.design/{id_.replace(':', '/')}.svg{tint}"
    if not _PLAIN_ID.match(id_):
        return None
    if src == "openverse":
        return _get(f"https://api.openverse.org/v1/images/{id_}/").json().get("url")
    if src == "commons":
        d = _get("https://commons.wikimedia.org/w/api.php", action="query", pageids=id_, prop="imageinfo",
                 iiprop="url", iiurlwidth=2560, format="json").json()
        ii = (next(iter((d.get("query") or {}).get("pages", {}).values()), {}).get("imageinfo") or [{}])[0]
        return ii.get("thumburl") or ii.get("url")
    if src == "picsum":
        return f"https://picsum.photos/id/{id_}/2560/1600"
    if src == "artic":
        return f"https://www.artic.edu/iiif/2/{id_}/full/1686,/0/default.jpg"
    if src == "nasa":
        items = (_get(f"https://images-api.nasa.gov/asset/{id_}").json().get("collection") or {}).get("items") or []
        hrefs = [i.get("href") or "" for i in items]
        for want in ("~large.", "~medium.", "~orig."):
            for h in hrefs:
                if want in h and h.lower().endswith((".jpg", ".jpeg", ".png")):
                    return h.replace("http://", "https://")
    return None


@router.post("/api/look/take")
async def take(request: Request):
    """A found picture into the look's own files."""
    data, bad = await _body(request)
    if bad:
        return bad
    src, id_ = str(data.get("src") or ""), str(data.get("id") or "")
    if src not in _FIND:
        return JSONResponse({"error": "Takový zdroj neznám."}, status_code=404)
    try:
        url = _full_url(src, id_, str(data.get("color") or ""))
        if not url or not url.startswith("https://"):
            return JSONResponse({"error": "Obrázek se nepodařilo najít."}, status_code=404)
        with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": UA}) as c:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                mime = r.headers.get("content-type", "")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > IMAGE_MAX:
                        return JSONResponse({"error": "Obrázek je moc velký."}, status_code=413)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return JSONResponse({"error": f"Zdroj teď neodpovídá ({type(e).__name__})."}, status_code=502)
    got = keep_image(bytes(buf), mime, {"src": src, "id": id_, "kind": "icon" if src == "iconify" else "found",
                                        **{k: str(data.get(k) or "")[:160] for k in ("title", "credit", "license", "page")}})
    if got is None:
        return JSONResponse({"error": "Tohle není obrázek, který umím použít."}, status_code=400)
    return got


# ---------------------------------------------------------------------- fonts

# What is offered when the catalogues cannot be reached.
FONTS_BASIC = ["Inter", "Roboto", "Open Sans", "Montserrat", "Poppins", "Oswald", "Bebas Neue", "Anton",
               "Rubik", "Barlow Condensed", "Teko", "Orbitron", "Rajdhani", "Metal Mania", "New Rocker",
               "Pirata One", "UnifrakturCook", "Creepster", "Nosifer", "Butcherman", "Eater", "Permanent Marker",
               "Rock Salt", "Special Elite", "Cinzel", "Playfair Display", "JetBrains Mono", "Space Grotesk"]


def _fonts_fetch() -> list:
    out = []
    try:
        raw = _get("https://fonts.google.com/metadata/fonts").text
        d = json.loads(raw[raw.index("{"):])
        for f in d.get("familyMetadataList") or []:
            w = sorted({int(k[:3]) for k in (f.get("fonts") or {}) if k[:3].isdigit()})
            out.append({"f": f["family"], "c": (f.get("category") or "").lower(), "w": w or [400],
                        "p": f.get("popularity") or 9999, "s": "google",
                        "cz": "latin-ext" in (f.get("subsets") or [])})
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    try:
        for off in range(0, 400, 100):
            d = _get("https://api.fontshare.com/v2/fonts", limit=100, offset=off).json()
            for f in d.get("fonts") or []:
                w = sorted({int((s.get("weight") or {}).get("weight") or 400) for s in f.get("styles") or []
                            if not s.get("is_italic")})
                out.append({"f": f["name"], "c": (f.get("category") or "").lower(), "w": w or [400],
                            "p": 5000, "s": "fontshare", "slug": f.get("slug"), "cz": True})
            if not d.get("has_more"):
                break
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    return out


@router.get("/api/look/fonts")
def fonts(refresh: bool = False):
    f = LOOK_DIR / "fonts.json"
    kept = _read(f, {})
    if not refresh and kept.get("fonts") and time.time() - (kept.get("at") or 0) < FONTS_AGE:
        return kept["fonts"]
    got = _fonts_fetch()
    if got:
        _write(f, {"at": time.time(), "fonts": got})
        return got
    return kept.get("fonts") or [{"f": n, "c": "", "w": [400, 700], "p": i, "s": "google", "cz": True}
                                 for i, n in enumerate(FONTS_BASIC)]
