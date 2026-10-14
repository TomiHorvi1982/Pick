"""Words Skladatel's model understands — for the description of the sound and for the lyrics.

ACE-Step 1.5 reads a song's description (its "caption") as free text in
English: comma-separated tags and whole sentences both work. What it was
taught on, and what its makers and its users found to work best
(docs/en/Tutorial.md in its repository, the Musician's Guide, users' notes):

- a description works by combining several kinds of words — genre, instruments,
  voice, mood, sound and production, era — specific beats vague
  ("crunchy rhythm guitar, punchy snare, gravelly male vocals" over "rock");
- words that pull against each other ("classical strings" and "hardcore metal")
  make it muddy; a change is better told as a story in time ("starts with clean
  guitar, then explodes into heavy riffs"), and a word said twice weighs more;
- tempo, key and time signature are not written into the description, they
  have fields of their own;
- the lyrics are the song's script: parts in [brackets], a short hint after a
  dash ([Chorus - anthemic]), 6–10 syllables a line, CAPITALS shouted,
  (parentheses) sung by the backing voices, a blank line between parts.

Each word is (English as the model reads it, Czech for the player).
"""

WORDS = [
    {"id": "genre", "name": "Žánr", "words": [
        ("heavy metal", "heavy metal"), ("thrash metal", "thrash metal"), ("groove metal", "groove metal"),
        ("death metal", "death metal"), ("black metal", "black metal"), ("doom metal", "doom metal"),
        ("power metal", "power metal"), ("metalcore", "metalcore"), ("nu metal", "nu metal"),
        ("hard rock", "hard rock"), ("classic rock", "klasický rock"), ("punk rock", "punk"),
        ("grunge", "grunge"), ("stoner rock", "stoner rock"), ("alternative rock", "alternativní rock"),
        ("blues rock", "blues rock"), ("progressive metal", "progresivní metal"), ("industrial metal", "industriální metal"),
        ("pop rock", "pop rock"), ("folk rock", "folk rock"), ("rock ballad", "rocková balada"), ("hardcore punk", "hardcore"),
    ]},
    {"id": "guitar", "name": "Kytary", "words": [
        ("heavily distorted electric guitars", "hodně zkreslené kytary"), ("palm-muted riffs", "tlumené riffy"),
        ("chugging rhythm guitar", "sekaná doprovodná kytara"), ("crunchy overdriven guitar", "křupavý overdrive"),
        ("down-tuned seven-string guitar", "podladěná sedmistrunka"), ("tremolo picking", "tremolo"),
        ("power chords", "kvintakordy"), ("twin guitar harmonies", "dvojhlasé kytary"),
        ("shredding guitar solo", "rychlé sólo"), ("melodic guitar lead", "melodická sólovka"),
        ("wah-wah guitar", "kvákadlo"), ("fuzzy guitar", "fuzz"), ("clean arpeggiated guitar", "čisté arpeggio"),
        ("acoustic guitar strumming", "akustika — brnkání"), ("slide guitar", "slide"), ("feedback and pinch harmonics", "zpětná vazba, flažolety"),
    ]},
    {"id": "drums", "name": "Bicí a rytmus", "words": [
        ("double bass drums", "dva kopáky"), ("blast beats", "blast beaty"), ("punchy live drums", "průrazné živé bicí"),
        ("tight snare", "pevný virbl"), ("thundering toms", "dunivé tomy"), ("groovy drums", "groovy bicí"),
        ("half-time feel", "poloviční pocit"), ("shuffle groove", "shuffle"), ("driving beat", "hnací rytmus"),
        ("syncopated rhythm", "synkopy"), ("galloping rhythm", "cval"), ("four-on-the-floor", "rovný kopák na každou dobu"),
        ("crashing cymbals", "činely"), ("tribal drums", "kmenové bubny"),
    ]},
    {"id": "band", "name": "Basa a další nástroje", "words": [
        ("growling bass", "chrčivá basa"), ("thick bass", "plná basa"), ("melodic bass line", "melodická basa"),
        ("slap bass", "slap"), ("walking bass", "walking bass"), ("hammond organ", "hammondky"),
        ("piano", "klavír"), ("synth pads", "syntezátorové plochy"), ("analog synthesizers", "analogové syntezátory"),
        ("orchestral strings", "smyčce"), ("brass section", "dechy"), ("choir", "sbor"), ("harmonica", "foukací harmonika"),
        ("violin", "housle"), ("accordion", "akordeon"), ("808 bass", "808"),
    ]},
    {"id": "voice", "name": "Zpěv", "words": [
        ("male vocals", "mužský zpěv"), ("female vocals", "ženský zpěv"), ("gritty raspy vocals", "chraplavý zpěv"),
        ("powerful belting vocals", "silný, vysoko nesený zpěv"), ("aggressive screaming vocals", "řev"),
        ("deep growled vocals", "growling"), ("clean melodic vocals", "čistý melodický zpěv"), ("high-pitched falsetto", "falzet"),
        ("breathy vocals", "dýchavý zpěv"), ("gang vocals", "skupinový pokřik"), ("vocal harmonies", "vícehlas"),
        ("spoken word", "mluvené slovo"), ("rap verses", "rap"), ("operatic vocals", "operní zpěv"), ("duet", "duet"),
    ]},
    {"id": "mood", "name": "Nálada", "words": [
        ("aggressive", "agresivní"), ("dark", "temná"), ("epic", "epická"), ("anthemic", "hymnická"),
        ("energetic", "energická"), ("melancholic", "melancholická"), ("uplifting", "povznášející"),
        ("rebellious", "rebelská"), ("menacing", "hrozivá"), ("triumphant", "vítězná"), ("nostalgic", "nostalgická"),
        ("heartbreaking", "srdcervoucí"), ("hopeful", "nadějná"), ("dreamy", "snová"), ("romantic", "romantická"),
        ("intense", "napjatá"), ("playful", "hravá"), ("sad", "smutná"),
    ]},
    {"id": "sound", "name": "Zvuk a produkce", "words": [
        ("raw", "syrový"), ("polished", "vyleštěný"), ("live recording", "živá nahrávka"), ("studio-polished", "studiový"),
        ("garage", "garážový"), ("lo-fi", "lo-fi"), ("high-fidelity", "čistý, hi-fi"), ("punchy", "průrazný"),
        ("heavy low end", "těžké basy"), ("wide stereo", "široké stereo"), ("warm analog", "teplý analogový"),
        ("crisp", "ostrý, jasný"), ("airy", "vzdušný"), ("reverb-drenched", "hodně dozvuku"), ("dry mix", "suchý mix"),
        ("vintage tape saturation", "staré pásky"), ("modern production", "moderní zvuk"), ("wall of sound", "stěna zvuku"),
    ]},
    {"id": "era", "name": "Éra", "words": [
        ("70s hard rock", "70. léta hard rock"), ("80s heavy metal", "80. léta heavy metal"), ("80s thrash", "80. léta thrash"),
        ("80s glam metal", "80. léta glam"), ("90s grunge", "90. léta grunge"), ("90s alternative", "90. léta alternativa"),
        ("2000s nu metal", "nultá léta nu metal"), ("modern metal", "moderní metal"), ("retro", "retro"),
        ("vintage", "vintage"), ("Czech rock of the 90s", "český rock 90. let"),
    ]},
    {"id": "energy", "name": "Tempo a energie", "words": [
        ("slow tempo", "pomalé"), ("mid-tempo", "středně rychlé"), ("fast-paced", "rychlé"), ("laid-back", "uvolněné"),
        ("driving", "hnací"), ("building intensity", "postupně sílí"), ("explosive", "výbušné"),
        ("loud quiet dynamics", "střídání ticha a nářezu"), ("groovy", "groovy"), ("relentless", "neúprosné"),
    ]},
    {"id": "form", "name": "Stavba songu", "words": [
        ("big riff intro", "intro s velkým riffem"), ("catchy chorus", "chytlavý refrén"), ("sing-along chorus", "refrén ke zpívání"),
        ("guitar solo", "kytarové sólo"), ("heavy breakdown", "těžký breakdown"), ("dramatic bridge", "dramatický most"),
        ("quiet acoustic intro", "tiché akustické intro"), ("fade-out ending", "konec do ztracena"), ("abrupt ending", "useknutý konec"),
        ("starts with clean guitar, then explodes into heavy riffs", "začne čistě, pak vybuchne do riffů"),
    ]},
]

# Tags for the lyrics: [part], [part - how], and how a voice is used.
LYRIC_TAGS = [
    {"id": "parts", "name": "Části", "tags": [
        ("[Intro]", "úvod, obvykle bez zpěvu"), ("[Verse]", "sloka"), ("[Pre-Chorus]", "předrefrén, stoupá"),
        ("[Chorus]", "refrén, vrchol"), ("[Bridge]", "most, změna"), ("[Guitar Solo]", "kytarové sólo"),
        ("[Instrumental]", "jen nástroje"), ("[Breakdown]", "ztišení / těžký breakdown"), ("[Build]", "graduje"),
        ("[Final Chorus]", "poslední refrén"), ("[Outro]", "závěr"), ("[Fade Out]", "do ztracena"),
    ]},
    {"id": "how", "name": "Jak zpívat část", "tags": [
        ("[Chorus - anthemic]", "hymnicky"), ("[Verse - whispered]", "šeptem"), ("[Chorus - powerful]", "naplno"),
        ("[Verse - spoken]", "mluveně"), ("[Bridge - soft]", "jemně"), ("[Chorus - screamed]", "řvaný"),
        ("[Intro - clean guitar]", "intro s čistou kytarou"), ("[Outro - fade out]", "konec do ztracena"),
    ]},
    {"id": "voice", "name": "Hlas", "tags": [
        ("[raspy vocal]", "chraplavě"), ("[powerful belting]", "silně a vysoko"), ("[whispered]", "šeptem"),
        ("[falsetto]", "falzetem"), ("[harmonies]", "vícehlasně"), ("[spoken word]", "mluvené slovo"),
        ("[call and response]", "otázka a odpověď"), ("[ad-lib]", "volné výkřiky"),
    ]},
    {"id": "energy", "name": "Energie", "tags": [
        ("[high energy]", "naplno"), ("[low energy]", "zdrženlivě"), ("[building energy]", "graduje"),
        ("[explosive]", "výbuch"), ("[melancholic]", "smutně"), ("[aggressive]", "agresivně"),
    ]},
]

TIPS_CAPTION = [
    "Piš anglicky. Model rozumí slovům i celým větám.",
    "Kombinuj 3–5 druhů slov: žánr + nástroje + zpěv + nálada + zvuk. Konkrétní je lepší než obecné.",
    "Nepiš do popisu tempo ani tóninu — na to jsou pole v nastavení.",
    "Nekombinuj protichůdné věci (smyčcový kvartet + hardcore). Změnu popiš v čase: „starts with clean guitar, then explodes into heavy riffs“.",
    "Co chceš víc, napiš dvakrát.",
    "Málo slov = víc překvapení, hodně slov = víc kontroly.",
    "Odkaz na éru nebo zvuk pomáhá: „80s heavy metal“, „90s grunge“.",
]

TIPS_LYRICS = [
    "Části označ hranatými závorkami: [Verse], [Chorus], [Bridge]. Mezi částmi prázdný řádek.",
    "6–10 slabik na řádek, podobně dlouhé řádky na stejném místě ve slokách.",
    "VELKÁ PÍSMENA = křik, (závorky) = doprovodné hlasy.",
    "K části stačí jedno slovo navíc: [Chorus - anthemic]. Víc štítků mate.",
    "Text musí sedět s popisem: v popisu kytary → v textu [Guitar Solo], ne housle.",
    "Jeden hlavní obraz na celou píseň, žádné hromadění přívlastků.",
    "Na 1 minutu zpěvu zhruba 120–150 slov; počítej i s mezihrami.",
]
