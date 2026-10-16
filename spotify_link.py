"""Odkaz ze Spotify — a playlist, an album or a song pasted as its share link (Sdílet → Kopírovat
odkaz), shown with its songs and then downloaded as every Spotify link in Pick is (spotdl).

Only links are read: nobody is searched for. A link to a profile gets the answer to send the
playlist's own link instead.
"""
import re
import threading
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

# set by the server: the Spotify client spotdl reads with, made ready once
client = lambda: None                                # noqa: E731

LINK = re.compile(r"^(?:https?://)?open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-zA-Z]{2})?/)?(playlist|album|track|user|artist)/([A-Za-z0-9]+)")
URI = re.compile(r"^spotify:(playlist|album|track|user|artist):([A-Za-z0-9]+)$")
KEEP_S = 600
_CACHE, _LOCK = {}, threading.Lock()


def parse(text: str):
    """(kind, id) of a Spotify link or URI, or None."""
    text = (text or "").strip()
    m = LINK.match(text) or URI.match(text)
    return (m.group(1), m.group(2)) if m else None


def _song(s) -> dict:
    return {"title": s.name, "artists": ", ".join(s.artists or [s.artist]), "album": s.album_name or "",
            "duration": int(s.duration or 0), "url": s.url}


def read(kind: str, url: str) -> dict:
    client()
    if kind == "playlist":
        from spotdl.types.playlist import Playlist
        p = Playlist.from_url(url, fetch_songs=False)
        return {"name": p.name, "author": p.author_name or "", "cover": p.cover_url or "", "tracks": [_song(s) for s in p.songs]}
    if kind == "album":
        from spotdl.types.album import Album
        a = Album.from_url(url, fetch_songs=False)
        return {"name": a.name, "author": a.artist.get("name", "") if isinstance(a.artist, dict) else "",
                "cover": a.songs[0].cover_url if a.songs else "", "tracks": [_song(s) for s in a.songs]}
    from spotdl.types.song import Song
    s = Song.from_url(url)
    return {"name": s.name, "author": ", ".join(s.artists), "cover": s.cover_url or "", "tracks": [_song(s)]}


@router.get("/api/spotify/link")
def link(url: str = ""):
    got = parse(url)
    if not got:
        return JSONResponse({"error": "Tohle není odkaz ze Spotify. Vlož odkaz, který začíná https://open.spotify.com/…"}, status_code=400)
    kind, sid = got
    if kind == "user":
        return JSONResponse({"error": "To je odkaz na profil. Ať ti pošle odkaz přímo na playlist: ve Spotify u playlistu tři tečky → Sdílet → Kopírovat odkaz."},
                            status_code=400)
    if kind == "artist":
        return JSONResponse({"error": "To je odkaz na interpreta — najdi ho v Picku podle jména."}, status_code=400)
    canon = f"https://open.spotify.com/{kind}/{sid}"
    with _LOCK:
        hit = _CACHE.get(canon)
        if hit and time.time() - hit[0] < KEEP_S:
            return hit[1]
    try:
        info = read(kind, canon)
    except Exception:
        return JSONResponse({"error": "Spotify ten odkaz nevydal — je playlist veřejný? Soukromý playlist přečíst nejde."}, status_code=502)
    out = {"kind": kind, "id": sid, "url": canon, "count": len(info["tracks"]), **info}
    with _LOCK:
        _CACHE[canon] = (time.time(), out)
    return out
