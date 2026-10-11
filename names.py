"""Song names as they are officially written.

What a separated song is called comes from the file it was made of — often a
video: "BeginNewLife | Slayer- Raining Blood (lyrics)". Here is what tidies
that: brackets and video words taken off, the uploader told from the band,
and — given what a music catalogue answers for the song — the official
artist, title and album picked, but only when both the artist and the title
it names are really in what we had.
"""
import re
import unicodedata

_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*(?:[\)\]\}]|$)")
_WORDS = (r"official\s+(?:music\s+|hd\s+|4k\s+|lyrics?\s+)?(?:video|audio|videoclip|videoklip|clip|klip)|"
          r"(?:music|lyrics?)\s+video|videoklip|videoclip|video\s*clip|with\s+lyrics|lyrics?|synched|"
          r"hd|hq|4k|1080p|720p|full\s+hd|(?:full\s+)?album\s+version|studio\s+version|original\s+version|"
          r"closed\s+captioned|(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?|explicit|edited|visuali[sz]er")
_NOISE_ONLY = re.compile(rf"(?i)^(?:\W|\b(?:{_WORDS}|live)\b)*$")
_TRAIL = re.compile(rf"(?i)[\s,\-–—|:·]*\b(?:{_WORDS})\s*$")
_SPLIT = re.compile(r"\s+[-–—|]\s+|(?<=\w)[-–—:]\s+")


_LIVE = re.compile(r"(?i)\blive\b|\bunplugged\b|\bživě\b")


def is_live(*texts) -> bool:
    """A concert recording says so somewhere in its name."""
    # (the band Live is not a concert: "Live - I Alone")
    return any(_LIVE.search(re.sub(r"(?i)^\s*live\s*[-–—|:]", "", t or "")) for t in texts)


def with_live(title: str, *texts) -> str:
    """Brackets go — but that it is not the studio version stays: "One (Live)"."""
    return f"{title} (Live)" if title and is_live(*texts) and not title.endswith("(Live)") else title


def key(text: str) -> str:
    """"Kabát" and "KABAT" alike: letters and digits only, no accents."""
    n = unicodedata.normalize("NFKD", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in n if not unicodedata.combining(c)))


def no_brackets(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _BRACKETS.sub("", text or "")).strip()


def clean(text: str) -> str:
    """A title without what a video adds to it: anything in brackets,
    "official video", "with lyrics", "HD" and their like, quotes."""
    t = no_brackets(text).replace("“", "").replace("”", "").replace('"', "")
    parts = [p for p in _SPLIT.split(t) if p.strip()]
    keep = [p for p in parts if not _NOISE_ONLY.match(p)]
    if keep and len(keep) < len(parts):
        t = " - ".join(keep)
    for _ in range(4):
        cut = _TRAIL.sub("", t)
        if cut == t or not cut.strip():
            break
        t = cut
    return re.sub(r"\s{2,}", " ", t).strip(" -–—|:,.·") or (text or "").strip()


def capital(name: str) -> str:
    """A name starts with a capital — each of its words, when it has none."""
    return " ".join(w[:1].upper() + w[1:] if w[:1].islower() else w for w in (name or "").split())


def tidy(artist: str, title: str) -> dict:
    """The best that can be said without asking anyone: the title cleaned,
    and "Band - Song" in a title taken apart when the artist is not in it
    (a video's uploader stands where the band should)."""
    title, artist = clean(title), no_brackets(artist)
    parts = [p.strip() for p in _SPLIT.split(title, maxsplit=1)]
    if len(parts) == 2 and all(parts) and key(artist) not in key(title):
        artist, title = parts
    elif len(parts) == 2 and all(parts) and key(parts[0]) == key(artist):
        title = parts[1]
    title = title[:1].upper() + title[1:]
    return {"artist": capital(artist) or None, "title": title or None}


def tidy_live(artist: str, title: str, name: str = "") -> dict:
    """tidy(), and "(Live)" behind the title of a concert recording."""
    t = tidy(artist, title)
    words = _LIVE.sub("", t["title"] or "").strip(" -–—|:,.·")
    if is_live(title) and words:                       # "Sober Live" → "Sober (Live)"
        t["title"] = re.sub(r"\s{2,}", " ", words)
    return {**t, "title": with_live(t["title"], title, name)}


def pick(found: list, artist: str, title: str, name: str = ""):
    """Of a catalogue's answers ({"title", "artists": [..], "album"}), the one
    that is this song: its title and one of its artists are both in what we
    had. The answer whose title is exactly ours goes first."""
    had = key(" ".join((name or "", artist or "", title or "")))
    ours = key(tidy(artist, title)["title"] or "")
    good = []
    for r in found or []:
        t = key(no_brackets(r.get("title") or ""))
        who = [key(a) for a in r.get("artists") or [] if key(a)]
        if len(t) < 2 or t not in had or not any(a in had for a in who):
            continue
        # a one-word title hides in anything ("one" in "someone"): then it has to be ours whole
        if len(t) < 5 and t != ours and not ours.endswith(t):
            continue
        good.append((0 if t == ours else 1, len(good), r))
    if not good:
        return None
    r = min(good, key=lambda g: g[:2])[2]
    first = next((a for a in r.get("artists") or [] if key(a) and key(a) in had), (r.get("artists") or [None])[0])
    # What we had, in pieces: a catalogue sometimes writes a Czech name without its accents, and we had it right.
    t = tidy(artist, title)
    pieces = [t["artist"], t["title"], no_brackets(artist)] + [x.strip() for x in _SPLIT.split(clean(title))]
    return {"artist": accents(first, pieces), "album": r.get("album") or None,
            "title": with_live(accents(no_brackets(r.get("title") or ""), pieces), title, name)}


def accents(official: str, ours: list) -> str:
    """The official name — unless one of ours is the same name with more accents in it."""
    marks = lambda x: sum(1 for c in unicodedata.normalize("NFKD", x or "") if unicodedata.combining(c))
    best = official
    for o in ours:
        if o and key(o) == key(official) and marks(o) > marks(best):
            best = capital(o) if o == o.lower() else o
    return best
