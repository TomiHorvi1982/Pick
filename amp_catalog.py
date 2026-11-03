"""Katalog zesilovačů a beden — every NAM profile and cabinet impulse Pick has, sorted
into what a guitarist looks for: the amp's make and model, how much gain, what kind of
capture (a head on its own, a full rig with the cabinet and microphone, a pedal), and for
a cabinet its pack, the cabinet, speaker, microphone and size.

Nothing on disk moves: saved mixes and tone prints name files by their path, so the
sorting lives here, worked out from each file's name, folders and (for .nam) its own
metadata. One picker window asks this for a page at a time — 28 000 impulses are never
sent whole.
"""
import json
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

# set by the server
amps_dir = lambda: Path(".")   # noqa: E731  the amps folder, asked each time (tests move it)
list_items = lambda: []          # noqa: E731  every amp and cabinet: [{file, name, kind, pack, folder, category}]
path_of = lambda f: None         # noqa: E731  file id -> Path

KEEP_S = 300
_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "models": [], "irs": []}

# ------------------------------------------------------------------ amps
# (make, model, pattern) — first match wins, so the specific comes before the general.
FAMILIES = [
    ("EVH", "5150 III", r"\bevh\b|5153|5150\s*-?\s*iii|5150iii|stealth"),
    ("Peavey", "5150 / 6505", r"5150|6505|\bpv\s*5150"),
    ("Peavey", "Invective", r"invective"),
    ("Peavey", "Peavey", r"peavey|vtm\s*120|jsx|xxx"),
    ("Mesa Boogie", "Rectifier", r"rectifier|recto|dual\s*-?rec|triple\s*-?rec|\bmbdr\b|\bdr\b.*mesa|\brec\b"),
    ("Mesa Boogie", "Mark", r"mark\s*(ii|iv|v|2|4|5|iic)|\biic\+?|\bmk\s*(iic|iv|v)\b|jp-?2c"),
    ("Mesa Boogie", "Badlander", r"badlander"),
    ("Mesa Boogie", "Mesa Boogie", r"mesa|boogie"),
    ("Marshall", "JCM800", r"jcm\s*-?800|\b220[34]\b"),
    ("Marshall", "JCM900", r"jcm\s*-?900"),
    ("Marshall", "JCM2000 / DSL", r"jcm\s*-?2000|\bdsl\b"),
    ("Marshall", "JVM", r"\bjvm"),
    ("Marshall", "Silver Jubilee", r"\bjub|2555|jubilee"),
    ("Marshall", "JMP-1", r"jmp\s*-?\s*1\b"),
    ("Marshall", "Plexi / JMP / JTM", r"plexi|\bjmp|1959|super\s*lead|super\s*bass|\bjtm|bluesbreaker|blues\s*breaker|1987|1962"),
    ("Marshall", "Marshall", r"marshall|marhsall|\bmrsh|\bjcm|\bm6100|\b6100\b"),
    ("Friedman", "Friedman", r"friedman|\bbe\s*-?100|\bhbe\b|be100|dirty\s*shirley"),
    ("Bogner", "Bogner", r"bogner|\buber|ecstasy|metropolis|shiva"),
    ("Diezel", "Diezel", r"diezel|\bvh\s*-?4\b|herbert|hagen"),
    ("ENGL", "ENGL", r"\bengl|e650|e\s*-?670|powerball|fireball|savage|blackmore|invader"),
    ("Orange", "Orange", r"orange|rockerverb|dual\s*terror|thunderverb|tiny\s*terror|\bor\s*-?(15|50|100|120)\b"),
    ("Fender", "Twin Reverb", r"twin\s*reverb|\btwin\b|bf_twin"),
    ("Fender", "Deluxe", r"deluxe|\bdrri\b|5e3"),
    ("Fender", "Bassman", r"bassman"),
    ("Fender", "Super Reverb", r"super\s*reverb|\bbfsr\b"),
    ("Fender", "Princeton", r"princeton"),
    ("Fender", "Fender", r"fender|blues\s*jr|pro\s*rev|vibroverb|vibrolux|bronco|tweed"),
    ("Vox", "AC30 / AC15", r"\bvox|\bac\s*-?(30|15|10)"),
    ("Soldano", "Soldano", r"soldano|\bslo\b|\bsol\s*ch"),
    ("Randall", "Randall", r"randall|satan"),
    ("Laney", "Laney", r"laney|\bvh\s*100"),
    ("Hughes & Kettner", "Hughes & Kettner", r"hughes|grandmeister|triamp"),
    ("Splawn", "Splawn", r"splawn"),
    ("PRS", "PRS", r"\bprs\b|\bmt\s*-?100"),
    ("Revv", "Revv", r"\brevv"),
    ("Bugera", "Bugera", r"bugera"),
    ("Dumble", "Dumble", r"dumble|\bodr\b|\bods\b"),
    ("Matchless", "Matchless", r"matchless"),
    ("Bad Cat", "Bad Cat", r"bad\s*cat"),
    ("Supro", "Supro", r"supro"),
    ("Magnatone", "Magnatone", r"magnatone"),
    ("Two-Rock", "Two-Rock", r"two\s*-?rock"),
    ("Victory", "Victory", r"victory"),
    ("Synergy", "Synergy", r"synergy|\bsyn-?\d"),
    ("Suhr", "Suhr", r"\bsuhr"),
    ("Tone King", "Tone King", r"tone\s*king"),
    ("Gibson", "Gibson", r"gibson"),
    ("Kemper", "Kemper", r"kemper"),
]
PEDALS = [
    ("Ibanez", "Tube Screamer", r"tube\s*screamer|\bts\s*-?(808|9|10|mini)\b|\bts808|\bts9|\bod\s*-?808"),
    ("Boss", "SD-1", r"\bsd\s*-?1\b"),
    ("Boss", "DS-1", r"\bds\s*-?1\b"),
    ("Boss", "Metal Zone", r"metal\s*zone|\bmt\s*-?2\b"),
    ("Boss", "HM-2", r"\bhm\s*-?2\b"),
    ("Klon / King of Tone", "Klon / King of Tone", r"klon|king\s*of\s*tone|\bkot\b"),
    ("Pro Co", "RAT", r"\brat\b"),
    ("Electro-Harmonix", "Big Muff", r"big\s*muff|\bmuff\b"),
    ("Horizon", "Precision Drive", r"precision\s*-?drive"),
    ("Fortin", "Grind / 33", r"fortin|grind|\b33\b"),
    ("ZVEX", "Box of Rock", r"zvex|box\s*of\s*rock"),
    ("MXR", "MXR", r"\bmxr\b|\bm77\b"),
    ("Highwind", "Direwolf", r"direwolf"),
    ("Bondi", "Sick As", r"sick\s*as"),
    ("Way Huge", "Swollen Pickle", r"swollen\s*pickle"),
    ("Boss", "Boss", r"\bboss\b"),
]
_FAM = [(a, b, re.compile(p, re.I)) for a, b, p in FAMILIES]
_PED = [(a, b, re.compile(p, re.I)) for a, b, p in PEDALS]
CLEAN = re.compile(r"clean|\bcln\b|\bclin|\bnrm\b|\bnorm(al)?\b|blues|jazz|sparkle|glass", re.I)
CRUNCH = re.compile(r"crunch|crnch|break\s*-?up|\bdrive\b|driven|\bod\d?\b|overdrive|edge|rock|classic|plexi|blues\s*driver", re.I)
HIGAIN = re.compile(r"\blead\b|\bld\b|hi\s*-?gain|high\s*-?gain|metal|thrash|djent|\bdist|crush|uber|\bred\b|\bmdn\b|modern|chug|brutal|death|doom", re.I)
HOT_FAM = {"5150 III", "5150 / 6505", "Rectifier", "Invective", "Diezel", "ENGL", "Bogner", "Revv", "Splawn", "Soldano", "Randall", "Friedman"}
SOFT_FAM = {"Twin Reverb", "Deluxe", "Bassman", "Super Reverb", "Princeton", "Fender", "AC30 / AC15", "Matchless", "Dumble", "Tone King", "Magnatone", "Supro", "Two-Rock", "Gibson"}
TONE_GAIN = {"clean": "clean", "clean fender twin": "clean", "crunch": "crunch", "overdrive": "crunch", "rock": "crunch",
             "british crunch": "crunch", "punk": "crunch", "marshall": "crunch", "marshall jmp-1": "crunch", "voxish": "clean",
             "clean to mean": "crunch", "hi_gain": "hi_gain", "high-gain": "hi_gain", "metal": "hi_gain", "distortion": "hi_gain"}
RIG = re.compile(r"full\s*-?rig|fullrig|\bfr\b|_full_|\bsm\s*-?5[78]|\bmd\s*-?421|\br\s*-?121|\be\s*-?906|\bm160|\bmic\b|[124]\s*x\s*1[02]|\bv30\b|\bcab\b|greenback|\bg12", re.I)
GAIN_LABEL = {"clean": "Čistý", "crunch": "Crunch", "hi_gain": "Hi-gain", "": "?"}
TYPE_ORDER = ["Zesilovač", "Celý aparát", "Předzesilovač", "Koncový zesilovač", "Krabička", "Jiné"]


def _meta(path: Path) -> dict:
    """A .nam file's own metadata: the first 24 kB, braces counted (it holds nested objects)."""
    try:
        with open(path, "rb") as h:
            s = h.read(24000).decode("utf-8", "ignore")
    except OSError:
        return {}
    i = s.find('"metadata"')
    j = s.find("{", i) if i >= 0 else -1
    if j < 0:
        return {}
    depth = 0
    for k in range(j, len(s)):
        c = s[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(s[j:k + 1])
                    return d if isinstance(d, dict) else {}
                except ValueError:
                    return {}
    return {}


def _clean(v) -> str:
    v = str(v or "").strip()
    return "" if v.lower() in ("", "t3k-null", "tz-make", "tz-model", "none", "n/a") else v


def classify_model(name: str, folder: str, meta: dict) -> dict:
    make, model, gtype, tone = _clean(meta.get("gear_make")), _clean(meta.get("gear_model")), _clean(meta.get("gear_type")), _clean(meta.get("tone_type")).lower()
    text = " ".join([name, folder, make, model, _clean(meta.get("name"))])
    brand = family = ""
    for b, f, rx in _FAM:
        if rx.search(text):
            brand, family = b, f
            break
    pedal = None
    for b, f, rx in _PED:
        if rx.search(text):
            pedal = (b, f)
            break
    # type: what was captured
    if re.search(r"\[pre\]", name, re.I):
        kind = "Předzesilovač"
    elif re.search(r"\[pow\]|poweramp", name, re.I):
        kind = "Koncový zesilovač"
    elif gtype == "amp_cab" or RIG.search(name + " " + folder):
        kind = "Celý aparát"
    elif gtype == "pedal" or (pedal and not brand):
        kind = "Krabička"
    elif gtype in ("amp", "") or brand:
        kind = "Zesilovač"
    else:
        kind = "Jiné"
    if kind == "Krabička" and pedal:
        brand, family = pedal
    if not brand and make and re.match(r"[A-Za-z]", make) and len(make) <= 30 and not re.search(r"shure|sm5|suhr reactive|load|neve|cadillac|\d+x\d+", make, re.I):
        brand = make.split(" ")[0].strip(" -").title() if make.isupper() or make.islower() else make.split(" ")[0].strip(" -")
        family = model or make
    # gain: the name says it best, then the capture's own word, then what the amp usually is
    n = name + " " + folder
    if CLEAN.search(n) and not HIGAIN.search(n):
        gain = "clean"
    elif HIGAIN.search(n):
        gain = "hi_gain"
    elif CRUNCH.search(n):
        gain = "crunch"
    elif tone in TONE_GAIN:
        gain = TONE_GAIN[tone]
    elif family in HOT_FAM or brand in HOT_FAM:
        gain = "hi_gain"
    elif family in SOFT_FAM:
        gain = "clean"
    elif kind == "Krabička":
        gain = "crunch"
    else:
        gain = ""
    by = _clean(meta.get("modeled_by"))
    picture = " ".join(x for x in ([brand, family if family != brand else ""] if brand else [model or name]) if x)
    picture += " pedal" if kind == "Krabička" else " amp"
    return {"brand": brand or "Ostatní", "family": family or brand or "Ostatní", "gain": gain, "type": kind, "by": by, "picture": picture.strip()}


# ------------------------------------------------------------------ cabinets
CAB_BRANDS = [
    ("Mesa Boogie", r"mesa|boogie|recto|\bmb\b|oversized|\bos\b"), ("Marshall", r"marshall|\b19[36]0|\bma-|vh-ma|jcm|1960a|1960b"),
    ("Orange", r"orange|ppc\s*-?\d|\bor\b"), ("ENGL", r"\bengl"), ("Bogner", r"bogner|uber"), ("Diezel", r"diezel"),
    ("Friedman", r"friedman"), ("EVH / Peavey", r"evh|5150|6505|peavey"), ("Fender", r"fender|twin|deluxe|bassman"),
    ("Vox", r"\bvox|ac30"), ("Zilla", r"zilla"), ("Avatar", r"avatar"), ("Randall", r"randall"), ("Laney", r"laney"),
    ("Krank", r"krank"), ("Hughes & Kettner", r"hughes"), ("Port City", r"port\s*city"), ("Framus", r"framus"),
]
SPEAKERS = [
    ("Vintage 30", r"v\s*-?30|vintage\s*30"), ("Greenback / G12M", r"greenback|g12m|\bgb\b"), ("G12T-75", r"g12t|t\s*-?75\b|\bt75\b"),
    ("G12H", r"g12h"), ("G12-65", r"g12\s*-?65|\b65\b"), ("Creamback", r"cream\s*back|creamback"), ("Classic Lead 80", r"classic\s*lead|\bcl80\b"),
    ("Alnico Blue", r"alnico\s*blue|\bblue\b"), ("Gold", r"\bgold\b"), ("K100", r"k\s*-?100"), ("Jensen", r"jensen|c12n|p12"),
    ("EVM12L", r"evm"), ("Eminence", r"eminence|swamp|texas\s*heat|cannabis"), ("WGS", r"\bwgs\b|veteran|reaper|et65"),
]
MICS = [
    ("SM57", r"sm\s*-?57|\b57\b"), ("SM7B", r"sm\s*-?7"), ("MD421", r"md\s*-?421|\b421\b"), ("MD441", r"md\s*-?441|\b441\b"),
    ("R-121", r"r\s*-?121|royer|\b121\b"), ("e906", r"e\s*-?906|\b906\b"), ("e609", r"e\s*-?609|\b609\b"), ("M160", r"m\s*-?160|\b160\b"),
    ("M201", r"m\s*-?201"), ("U87", r"u\s*-?87"), ("C414", r"c\s*-?414|\b414\b"), ("SM58", r"sm\s*-?58|\b58\b"), ("i5", r"\bi\s*-?5\b"),
    ("Fredman", r"fredman"), ("4038", r"4038"), ("KSM32", r"ksm"), ("TLM", r"tlm"), ("Ribbon", r"ribbon"), ("X1R", r"x1r"),
    ("Mix", r"\bmix(ed)?\b|blend|combo\b"),
]
_CB = [(a, re.compile(p, re.I)) for a, p in CAB_BRANDS]
_SP = [(a, re.compile(p, re.I)) for a, p in SPEAKERS]
_MI = [(a, re.compile(p, re.I)) for a, p in MICS]
SIZE = re.compile(r"\b([1-4])\s*x\s*(10|12|15)\b|\b(1|2|4)(10|12)\b", re.I)
HZ = re.compile(r"(\d{5,6})\s*hz", re.I)


def classify_ir(name: str, pack: str, folder: str) -> dict:
    text = " ".join([pack, folder, name])
    brand = next((b for b, rx in _CB if rx.search(text)), "")
    speaker = next((s for s, rx in _SP if rx.search(folder + " " + name)), "")
    mic = next((m for m, rx in _MI if rx.search(name)), "") or next((m for m, rx in _MI if rx.search(folder)), "")
    m = SIZE.search(text)
    size = f"{m.group(1)}x{m.group(2)}" if m and m.group(1) else (f"{m.group(3)}x{m.group(4)}" if m else "")
    hz = HZ.search(folder)
    # the cabinet itself: the first folder under the pack that is not a sample-rate, mic or "mix" folder
    parts = [p for p in folder.split("/") if p and not HZ.search(p) and not re.fullmatch(r"(mp|raw|mix.*|.*mic.*|[a-z]{1,3}\d*|vol \d+)", p, re.I)]
    cab = parts[0] if parts else (pack or "Bez balíčku")
    picture = " ".join(x for x in [brand, size, speaker, "cabinet"] if x) if (brand or size or speaker) else f"{cab} guitar cabinet"
    return {"brand": brand or "Ostatní", "cab": cab, "speaker": speaker, "mic": mic, "size": size,
            "hz": int(hz.group(1)) if hz else 0, "picture": picture}


# ------------------------------------------------------------------ the catalogue
def _meta_cache_file() -> Path:
    return amps_dir() / ".amp-catalog.json"


def _build():
    try:
        old = json.loads(_meta_cache_file().read_text())
    except (OSError, ValueError):
        old = {}
    metas, models, irs, twins = {}, [], [], set()
    for it in list_items():
        f, name = it.get("file") or "", it.get("name") or ""
        if not f:
            continue
        if it.get("kind") == "model":
            # the same capture copied in twice (the collection and the music library): once is enough
            twin = (name.lower(), it.get("bytes"))
            if twin in twins:
                continue
            twins.add(twin)
            p = path_of(f)
            try:
                st = p.stat() if p else None
            except OSError:
                st = None
            sig = f"{st.st_size}:{int(st.st_mtime)}" if st else ""
            got = old.get(f)
            meta = got["meta"] if got and got.get("sig") == sig else (_meta(p) if p else {})
            metas[f] = {"sig": sig, "meta": {k: meta.get(k) for k in ("gear_make", "gear_model", "gear_type", "tone_type", "modeled_by", "name")}}
            where = "/".join(f.split(":", 1)[-1].split("/")[:-1])          # the folders it sits in say what it is, too
            c = classify_model(name, where + " " + (it.get("category") or ""), metas[f]["meta"])
            models.append({"file": f, "name": name, "pack": it.get("pack") or {"clean": "Clean", "crunch": "Crunch", "hi_gain": "Hi-gain",
                           "library": "Knihovna"}.get(it.get("category") or "", "Moje"), **c})
        elif it.get("kind") == "ir":
            c = classify_ir(name, it.get("pack") or "", it.get("folder") or "")
            irs.append({"file": f, "name": name, "pack": it.get("pack") or "Moje", "folder": it.get("folder") or "", **c})
    try:
        _meta_cache_file().write_text(json.dumps(metas))
    except OSError:
        pass
    return models, irs


def catalogue(fresh: bool = False):
    with _LOCK:
        if fresh or time.time() - _CACHE["at"] > KEEP_S or not (_CACHE["models"] or _CACHE["irs"]):
            _CACHE["models"], _CACHE["irs"] = _build()
            _CACHE["at"] = time.time()
        return _CACHE["models"], _CACHE["irs"]


def forget():
    _CACHE["at"] = 0.0


def _norm(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


GAIN_ORDER = {"clean": 0, "crunch": 1, "hi_gain": 2, "": 3}
FILTERS = {"model": ("brand", "family", "gain", "type", "pack"), "ir": ("pack", "brand", "cab", "speaker", "mic", "size")}
SORTS = {
    # "my key": make, then model, then from clean to hot, then the name
    "model": {"key": lambda x: (x["brand"] == "Ostatní", _norm(x["brand"]), _norm(x["family"]), GAIN_ORDER[x["gain"]], _norm(x["name"])),
              "name": lambda x: _norm(x["name"]),
              "gain": lambda x: (GAIN_ORDER[x["gain"]], _norm(x["brand"]), _norm(x["name"])),
              "type": lambda x: (TYPE_ORDER.index(x["type"]), _norm(x["brand"]), _norm(x["name"])),
              "pack": lambda x: (_norm(x["pack"]), _norm(x["brand"]), _norm(x["name"]))},
    "ir": {"key": lambda x: (_norm(x["pack"]), _norm(x["cab"]), _norm(x["speaker"] or "~"), _norm(x["mic"] or "~"), _norm(x["name"])),
           "name": lambda x: _norm(x["name"]),
           "brand": lambda x: (x["brand"] == "Ostatní", _norm(x["brand"]), x["size"], _norm(x["speaker"] or "~"), _norm(x["name"])),
           "speaker": lambda x: (not x["speaker"], _norm(x["speaker"]), _norm(x["pack"]), _norm(x["name"])),
           "mic": lambda x: (not x["mic"], _norm(x["mic"]), _norm(x["pack"]), _norm(x["name"]))},
}
# the heading a row falls under, per order
GROUP = {
    "model": {"key": lambda x: f"{x['brand']} · {x['family']}" if x["family"] != x["brand"] else x["brand"],
              "name": lambda x: (x["name"][:1] or "#").upper(), "gain": lambda x: GAIN_LABEL[x["gain"]],
              "type": lambda x: x["type"], "pack": lambda x: x["pack"]},
    "ir": {"key": lambda x: f"{x['pack']} · {x['cab']}" if x["cab"] != x["pack"] else x["pack"],
           "name": lambda x: (x["name"][:1] or "#").upper(),
           "brand": lambda x: " ".join(v for v in (x["brand"], x["size"]) if v),
           "speaker": lambda x: x["speaker"] or "Neuvedený reproduktor", "mic": lambda x: x["mic"] or "Neuvedený mikrofon"},
}


@router.get("/api/amp-catalog")
def amp_catalog(kind: str = "model", q: str = "", sort: str = "key", offset: int = 0, limit: int = 150,
                brand: str = "", family: str = "", gain: str = "", type: str = "", pack: str = "",
                cab: str = "", speaker: str = "", mic: str = "", size: str = "", rates: int = 0, fresh: int = 0):
    kind = "ir" if kind == "ir" else "model"
    models, irs = catalogue(bool(fresh))
    items = irs if kind == "ir" else models
    if kind == "ir" and not rates:
        # one sample rate of each impulse is enough to choose from: 48 kHz where a pack has several
        items = [x for x in items if not x["hz"] or x["hz"] == 48000]
    words = _norm(q).split()
    if words:
        items = [x for x in items if all(w in _norm(" ".join(str(x.get(k) or "") for k in
                 ("name", "brand", "family", "pack", "cab", "speaker", "mic", "size", "folder", "type", "by"))) for w in words)]
    want = {"brand": brand, "family": family, "gain": gain, "type": type, "pack": pack, "cab": cab, "speaker": speaker, "mic": mic, "size": size}
    keys = FILTERS[kind]
    facets = {}
    for k in keys:
        rest = [x for x in items if all(not want[o] or x.get(o) == want[o] for o in keys if o != k)]
        cnt = {}
        for x in rest:
            v = x.get(k) or ""
            if v:
                cnt[v] = cnt.get(v, 0) + 1
        facets[k] = sorted(cnt.items(), key=lambda kv: (-kv[1], _norm(kv[0])))[:80]
    items = [x for x in items if all(not want[k] or x.get(k) == want[k] for k in keys)]
    sort = sort if sort in SORTS[kind] else "key"
    items = sorted(items, key=SORTS[kind][sort])
    g = GROUP[kind][sort]
    offset, limit = max(0, offset), max(1, min(500, limit))
    page = [{**x, "group": g(x)} for x in items[offset:offset + limit]]
    return {"kind": kind, "total": len(items), "offset": offset, "items": page, "facets": facets,
            "gains": GAIN_LABEL, "all": len(models) if kind == "model" else len(irs)}
