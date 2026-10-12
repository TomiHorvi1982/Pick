"""The assistant's book of sentences: what a guitarist says to Pick in Czech,
and the calls each sentence means (the tools of assistant.py).

A small model guesses; a sentence that is in the book needs no guessing and
no waiting. The book is made here, from the ways each thing is said, and it
serves three times: a sentence that is in it is done at once; the ones most
like an unknown sentence go to the model as examples; and when there is no
model at all, the nearest one is what is done.

Add a way of saying something here and the assistant knows it. A new thing
Pick can do goes into assistant.TOOLS / COMMANDS first, then here.
"""
import difflib
import re
import unicodedata

FILL = {"prosim", "pick", "piku", "hele", "mi", "to", "ty", "tam", "tu", "ted", "uz", "teda", "tak", "jeste", "procent", "procenta", "si", "me", "nam"}


def norm(text):
    """A sentence as it is compared: no accents, no punctuation, no filler words."""
    t = "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")
    return " ".join(w for w in re.findall(r"[a-z0-9+]+", t) if w not in FILL)


def _call(tool, **args):
    return {"tool": tool, "args": args}


STEMS = {
    "drums": ["bicí", "bubny", "bubeníka"],
    "bass": ["basu", "baskytaru", "basa"],
    "guitar": ["kytaru", "kytary", "originální kytaru"],
    "vocals": ["zpěv", "zpěváka", "vokály", "hlas"],
    "piano": ["klavír", "piano", "klávesy"],
    "other": ["ostatní", "zbytek"],
}
STEM_SAY = {
    "mute": ["ztlum {x}", "vypni {x}", "umlč {x}", "bez {x}", "dej pryč {x}", "nechci slyšet {x}", "ztlumit {x}", "vypnout {x}", "{x} pryč", "{x} vypnout"],
    "unmute": ["pusť {x}", "zapni {x}", "vrať {x}", "pusť zase {x}", "zapni zase {x}", "vrať zpátky {x}", "chci zase {x}", "{x} zpátky", "{x} zapnout", "odtlum {x}"],
    "solo": ["jen {x}", "jenom {x}", "nech hrát jen {x}", "nech hrát jenom {x}", "chci slyšet jen {x}", "chci slyšet jenom {x}", "sólo {x}", "{x} sólo", "pusť jen {x}", "pusť jenom {x}", "nech jen {x}"],
    "unsolo": ["zruš sólo {x}", "vypni sólo {x}", "{x} už ne sólo", "konec sóla {x}"],
    "louder": ["zesil {x}", "přidej {x}", "{x} hlasitěji", "víc {x}", "{x} nahlas", "zvyš {x}", "přidej na {x}", "{x} trochu přidat", "{x} víc"],
    "quieter": ["ztiš {x}", "uber {x}", "{x} tišeji", "míň {x}", "sniž {x}", "uber na {x}", "{x} trochu ubrat", "{x} míň", "{x} potichu"],
}
SECTIONS = {
    "intro": ["intro", "úvod", "začátek"],
    "verse": ["sloku", "sloka"],
    "chorus": ["refrén"],
    "bridge": ["mezihru", "bridge", "mezihra"],
    "solo": ["sólo", "kytarové sólo"],
    "outro": ["outro", "konec", "závěr"],
}
SECTION_SAY = ["zacykli {x}", "smyčka na {x}", "smyčku na {x}", "dej smyčku na {x}", "opakuj {x}", "dokola {x}", "pořád dokola {x}", "{x} ve smyčce",
               "chci cvičit jen {x}", "pusť mi dokola {x}", "loop na {x}", "zacykli mi {x}", "opakuj mi {x}"]
NUMBERS = {50: "padesát", 55: "padesát pět", 60: "šedesát", 65: "šedesát pět", 70: "sedmdesát", 75: "sedmdesát pět", 80: "osmdesát",
           85: "osmdesát pět", 90: "devadesát", 95: "devadesát pět", 100: "sto", 110: "sto deset", 120: "sto dvacet"}
TEMPO_SAY = ["tempo na {n}", "tempo {n}", "dej tempo na {n}", "dej tempo {n}", "nastav tempo na {n}", "rychlost na {n}", "rychlost {n}", "dej na {n}", "hraj na {n}"]

# One call each, said in these ways.
PLAIN = [
    (_call("playback", action="play"), ["pusť", "hraj", "přehraj", "přehrát", "spusť", "pokračuj", "jedeme", "play", "start", "pusť hudbu", "pusť písničku", "zapni přehrávání", "hraj dál"]),
    (_call("playback", action="stop"), ["zastav", "stop", "pauza", "stůj", "stačí", "ticho", "zastav hudbu", "vypni hudbu", "pozastav", "dej pauzu", "zastav přehrávání", "přestaň hrát"]),
    (_call("playback", action="restart"), ["od začátku", "znovu", "ještě jednou", "pusť od začátku", "hraj od začátku", "znova", "zpátky na začátek", "na začátek", "znovu od začátku", "dej znovu"]),
    (_call("playback", action="next"), ["další", "další písnička", "další píseň", "další song", "další skladba", "přeskoč písničku", "pusť další", "dej další", "následující", "následující písnička"]),
    (_call("playback", action="previous"), ["předchozí", "předchozí písnička", "předchozí píseň", "předchozí song", "pusť předchozí", "vrať předchozí písničku", "minulá písnička", "zpátky na předchozí"]),
    (_call("volume", steps=1), ["přidej hlasitost", "hlasitěji", "zesil", "nahlas", "víc nahlas", "přidej", "zvyš hlasitost", "dej nahlas", "trochu zesil", "trochu hlasitěji", "přidej trochu"]),
    (_call("volume", steps=3), ["hodně zesil", "hodně hlasitěji", "pořádně nahlas", "hodně přidej", "dej hodně nahlas", "naplno", "hlasitost naplno"]),
    (_call("volume", steps=-1), ["uber hlasitost", "tišeji", "ztiš", "potichu", "míň nahlas", "uber", "sniž hlasitost", "dej potichu", "trochu ztiš", "trochu tišeji", "uber trochu", "ztlum"]),
    (_call("volume", steps=-3), ["hodně ztiš", "hodně tišeji", "hodně uber", "dej hodně potichu", "úplně potichu", "ztiš hodně"]),
    (_call("set_tempo", percent=100), ["normální tempo", "původní tempo", "normální rychlost", "původní rychlost", "vrať tempo", "vrať tempo na normál", "tempo na normál", "plné tempo", "plná rychlost", "tempo zpátky", "hraj normálně"]),
    (_call("set_tempo", percent=50), ["zpomal na polovinu", "poloviční tempo", "poloviční rychlost", "tempo na polovinu", "na půl rychlosti", "hraj polovičně"]),
    (_call("loop_section", section="current"), ["zacykli tuhle část", "zacykli část", "smyčka tady", "opakuj tuhle část", "zacykli co hraje", "zacykli část co hraje", "opakuj co hraje", "dokola tuhle část", "smyčku na tuhle část", "zacykli tohle", "opakuj tohle", "tohle dokola"]),
    (_call("loop_section", section="next"), ["další část", "smyčka na další část", "zacykli další část", "další sekce", "posuň smyčku dál"]),
    (_call("loop_section", section="previous"), ["předchozí část", "smyčka na předchozí část", "zacykli předchozí část", "předchozí sekce", "posuň smyčku zpátky"]),
    (_call("loop_section", section="off"), ["zruš smyčku", "vypni smyčku", "konec smyčky", "neopakuj", "bez smyčky", "smyčku pryč", "hraj dál bez smyčky", "zruš opakování", "přestaň opakovat", "vypni loop"]),
    (_call("practice"), ["chci cvičit tuhle písničku", "chci cvičit", "pojďme cvičit", "procvičit tuhle písničku", "chci tuhle cvičit", "cvičit tuhle píseň", "dej do cvičení", "chci hrát s tím", "režim cvičení", "chci ji cvičit"]),
]
COMMAND_SAY = {
    "tuner": ["otevři ladičku", "ladička", "chci ladit", "chci naladit kytaru", "nalaď kytaru", "zapni ladičku", "ukaž ladičku", "potřebuju naladit"],
    "metronome": ["zapni metronom", "vypni metronom", "metronom", "pusť metronom", "chci metronom", "klik", "zapni klik", "vypni klik"],
    "record": ["nahrávej", "nahraj mě", "nahraj kytaru", "začni nahrávat", "zapni nahrávání", "vypni nahrávání", "přestaň nahrávat", "nahraj mě jak hraju", "nahrávání", "nahraj to", "chci se nahrát"],
    "loop": ["smyčka", "zapni smyčku", "loop", "zacykli", "opakuj", "dokola"],
    "tabs": ["ukaž taby", "taby", "tabulatura", "zapni taby", "vypni taby", "chci taby", "ukaž tabulaturu", "otevři taby", "schovej taby", "kde jsou taby"],
    "lyrics": ["ukaž text", "text", "zapni text", "vypni text", "ukaž text písničky", "chci text", "ukaž slova", "otevři text", "schovej text", "ukaž lyrics"],
    "see:lyrics": ["text přes celé okno", "ukaž jen text", "text na celou obrazovku", "velký text", "zvětši text"],
    "see:mix": ["ukaž mix", "zpátky na mix", "ukaž stopy", "vrať mix", "chci vidět mix", "ukaž mixážní pult"],
    "words": ["chci trénovat výslovnost", "výslovnost", "trénuj se mnou výslovnost", "čti text", "přečti text", "procvič výslovnost", "zapni výslovnost", "uč mě vyslovovat", "trénink výslovnosti", "chci se učit text"],
    "mywords": ["procvič moje slova", "moje slova", "slova co mi nejdou", "těžká slova", "trénuj slova co neumím", "moje těžká slova"],
    "melody": ["chci zpívat", "trénink zpěvu", "chci zazpívat po řádcích", "zpívej se mnou", "uč mě zpívat", "trénuj se mnou zpěv", "zpěv po řádcích", "chci trénovat zpěv", "procvič melodii", "chci trénovat melodii"],
    "myguitar": ["zapni moji kytaru", "vypni moji kytaru", "moje kytara", "zapni živou kytaru", "vypni živou kytaru", "chci slyšet svoji kytaru", "zapni mou kytaru", "pusť moji kytaru", "živá kytara"],
    "preset:+": ["další preset", "další zvuk", "další zesilovač", "jiný zvuk kytary", "přepni preset", "další aparát", "změň zvuk kytary"],
    "preset:-": ["předchozí preset", "předchozí zvuk", "předchozí zesilovač", "vrať preset", "vrať zvuk kytary", "předchozí aparát"],
    "gtrs:all": ["ukaž všechny kytary", "všechny kytary v tabu", "ukaž všechny kytary v tabu", "chci vidět všechny kytary", "všechny kytary"],
    "gtrs:more": ["přidej další kytaru", "ukaž další kytaru", "další kytara v tabu", "přidej kytaru do tabu"],
    "skip:+": ["přeskoč kousek", "kousek dál", "posuň dál", "přeskoč dopředu", "o kousek dál", "přeskoč o kousek dál", "posuň dopředu", "skoč dopředu", "o deset vteřin dál", "přetoč dopředu"],
    "skip:-": ["kousek zpátky", "posuň zpátky", "přeskoč dozadu", "o kousek zpátky", "vrať kousek", "skoč zpátky", "o deset vteřin zpátky", "přetoč zpátky", "vrať se kousek"],
    "song": ["pusť něco", "pusť nějakou písničku", "pusť náhodnou písničku", "vyber písničku", "vyber něco", "něco zahraj", "pusť cokoliv", "náhodná písnička", "pusť nějakou náhodnou písničku", "překvap mě"],
    "save": ["ulož písničku", "ulož do oblíbených", "přidej do oblíbených", "ulož tuhle písničku", "tuhle chci", "ulož tuhle písničku do oblíbených", "dej do oblíbených", "stáhni tuhle písničku", "ulož ji"],
    "tomini": ["pusť v minipřehrávači", "dej do minipřehrávače", "otevři v mini", "pošli do minipřehrávače"],
    "mini": ["otevři minipřehrávač", "minipřehrávač", "malý přehrávač", "ukaž minipřehrávač", "zapni minipřehrávač"],
    "full": ["celá obrazovka", "přes celou obrazovku", "zvětši okno", "na celou obrazovku", "zmenši okno", "zruš celou obrazovku", "fullscreen"],
    "panel:off": ["schovej panel", "schovej levý panel", "zavři panel", "skryj panel", "panel pryč", "skryj levý panel"],
    "panel:on": ["ukaž panel", "ukaž levý panel", "otevři panel", "vrať panel", "vrať levý panel"],
    "steems": ["otevři steems", "ukaž steems", "otevři mix", "otevři mixážní pult", "ukaž rozdělené písničky", "steems", "jdi do steems", "ukaž stopy písniček"],
    "playlist": ["otevři playlisty", "ukaž playlisty", "playlisty", "jdi na playlisty"],
    "list:favs": ["ukaž oblíbené", "oblíbené", "otevři oblíbené", "moje oblíbené", "ukaž moje oblíbené písničky"],
    "list:bands": ["ukaž kapely", "kapely", "ukaž interprety", "otevři kapely", "ukaž umělce", "seznam kapel"],
    "list:library": ["ukaž knihovnu", "knihovna", "otevři knihovnu", "ukaž stažené písničky", "moje písničky"],
    "show:tabs": ["ukaž knihovnu tabů", "knihovna tabů", "otevři knihovnu tabů", "všechny taby"],
    "show:chords": ["ukaž akordy", "akordy", "stupnice", "ukaž stupnice", "stupnice a akordy", "otevři akordy"],
    "show:loops": ["ukaž loopy", "loopy", "otevři loopy", "ukaž smyčky bicích"],
    "show:tools": ["ukaž nástroje", "nástroje", "otevři nástroje"],
    "show:podcasts": ["ukaž podcasty", "podcasty", "otevři podcasty"],
    "show:analyzer": ["otevři analyzátor", "analyzátor", "ukaž analyzátor hraní", "performance analyzer", "zhodnoť moje hraní", "otevři analyzer"],
    "show:loopstudio": ["otevři loop studio", "loop studio", "ukaž loop studio"],
    "sort:artist": ["seřaď podle kapely", "seřaď podle interpreta", "řaď podle kapely", "podle kapely"],
    "sort:album": ["seřaď podle alba", "řaď podle alba", "podle alba"],
    "sort:title": ["seřaď podle názvu", "řaď podle názvu", "podle názvu", "seřaď podle abecedy", "podle abecedy"],
    "sort:tuning": ["seřaď podle ladění", "řaď podle ladění", "podle ladění", "seřaď podle ladění kytary"],
    "slower": ["zpomal", "pomaleji", "trochu zpomal", "trochu pomaleji", "uber tempo", "sniž tempo", "zpomal trochu", "moc rychlé", "je to rychlé"],
    "faster": ["zrychli", "rychleji", "trochu zrychli", "trochu rychleji", "přidej tempo", "zvyš tempo", "zrychli trochu", "moc pomalé", "je to pomalé"],
    "help": ["co umíš", "nápověda", "ukaž povely", "co můžu říct", "pomoc", "jaké jsou povely", "co všechno umíš"],
}
GROUPS = [("Přehrávání", ("playback",)), ("Hlasitost", ("volume",)), ("Tempo", ("set_tempo",)), ("Smyčky", ("loop_section",)),
          ("Stopy v mixu", ("stem",)), ("Cvičení", ("practice",)), ("Ostatní povely", ("command",))]


def build():
    """→ {normalised sentence: {"text": the sentence, "calls": [call]}}"""
    book = {}

    def put(text, call):
        book.setdefault(norm(text), {"text": text, "calls": [call]})
    for call, says in PLAIN:
        for t in says:
            put(t, call)
    for cid, says in COMMAND_SAY.items():
        for t in says:
            put(t, _call("command", id=cid))
    for stem, names in STEMS.items():
        for action, says in STEM_SAY.items():
            for t in says:
                for x in names:
                    put(t.format(x=x), _call("stem", stem=stem, action=action))
    for sec, names in SECTIONS.items():
        for t in SECTION_SAY:
            for x in names:
                put(t.format(x=x), _call("loop_section", section=sec))
    for n, word in NUMBERS.items():
        for t in TEMPO_SAY + (["zpomal na {n}"] if n < 100 else ["zrychli na {n}"] if n > 100 else []):
            put(t.format(n=word), _call("set_tempo", percent=n))
            put(t.format(n=n), _call("set_tempo", percent=n))
    return book


BOOK = build()
_JOIN = re.compile(r"\s+(?:a a pak|a a potom|a pak|a potom|a taky|a jeste|potom|pak|a)\s+")


def find(text, more=None):
    """The calls of a sentence that is in the book (or in `more`, what was
    learned), also when it is several known ones joined by "a" — or None."""
    known = lambda k: (more or {}).get(k) or BOOK.get(k)
    key = norm(text)
    hit = known(key)
    if hit:
        return list(hit["calls"])
    # "ztlum zpěv a zpomal na osmdesát": each part by itself
    raw = "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")
    parts = [p for p in _JOIN.split(" ".join(re.sub(r"[,;.]+", " a ", raw).split())) if norm(p)]
    if 2 <= len(parts) <= 4:
        hits = [known(norm(p)) for p in parts]
        if all(hits):
            return [c for h in hits for c in h["calls"]]
    return None


def near(text, n=6, more=None):
    """The sentences most like this one: [(likeness 0–1, sentence, calls)]."""
    key = norm(text)
    rows = list(BOOK.items()) + list((more or {}).items())
    m = difflib.SequenceMatcher(None, "", key)
    scored = []
    for k, v in rows:
        m.set_seq1(k)
        if m.real_quick_ratio() > 0.5 and m.quick_ratio() > 0.5:
            scored.append((m.ratio(), v["text"], v["calls"]))
    scored.sort(key=lambda r: -r[0])
    out, seen = [], set()
    for r in scored:                                       # one of each meaning: six ways to mute the drums teach nothing
        sig = repr(r[2])
        if sig not in seen:
            seen.add(sig); out.append(r)
        if len(out) >= n:
            break
    return out
