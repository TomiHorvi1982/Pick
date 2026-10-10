# Pick

Lokální GUI nad [spotdl](https://github.com/spotDL/spotify-downloader) pro
procházení playlistů, stahování skladeb, separaci stemů (Demucs) a mixpult
v prohlížeči. Server běží jen na `127.0.0.1` — není dostupný ze sítě.

## Struktura

```
app/
├── server.py           # FastAPI backend
├── static/index.html   # frontend (vanilla JS, jeden soubor)
├── tests/              # pytest
├── vendor/             # git submoduly, jen jako reference (kaset, stemdeck)
└── docs/adr/           # architektonická rozhodnutí
```

## Cesty

| Co            | Kde                                                  |
|---------------|------------------------------------------------------|
| Backend       | `~/.local/share/spotdl-helper/app/server.py`          |
| Frontend      | `~/.local/share/spotdl-helper/app/static/index.html`  |
| Desktop app   | `~/Desktop/Pick.app` (menubar launcher)       |
| Venv          | `~/.local/venvs/spotdl/`                              |
| Konfigurace   | `~/.local/share/spotdl-helper/config.json`            |
| Log serveru   | `~/.local/share/spotdl-helper/server.log`             |
| Hudba         | `~/Music/spotdl/` (`Songs/`, `Stems/`, `Playlists/`)  |
| Stemy         | `~/Music/spotdl/Stems/{skladba}/{stem}.mp3`           |

Kořen hudby lze změnit v Nastavení; uloží se do `config.json`.

## Spuštění

Přes desktop app (`~/Desktop/Pick.app`) — ikona 🎧 v menu baru
nastartuje server a otevře Chrome v app režimu.

Ručně:

```bash
cd ~/.local/share/spotdl-helper/app
~/.local/venvs/spotdl/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

Pak otevřít <http://127.0.0.1:8765>.

## Závislosti

Python balíčky jsou v `requirements.txt` (`pip install -r requirements.txt`).
Mimo venv jsou potřeba ještě externí nástroje:

| Nástroj  | Cesta                    | K čemu                              |
|----------|--------------------------|-------------------------------------|
| `spotdl` | `~/.local/bin/spotdl`    | stahování                           |
| `demucs` | `~/.local/bin/demucs`    | separace stemů (vlastní venv/torch) |
| `beat_this` | v demucs venv         | detekce beatů a downbeatů           |
| `all-in-one-infer` | `~/.local/venvs/spotdl-analyze/` | sekce skladby (model `harmonix-all`) |
| `librosa` | tamtéž                  | chroma pro detekci akordů           |
| `ffmpeg` | `/opt/homebrew/bin/ffmpeg` | převody, slučování DASH videa     |

Demucs má **vlastní** izolovaný interpret (uv/pipx) s torchem — server si
z jeho shebangu přečte cestu a subprocessem ověří dostupnost MPS, aby
separace běžela na GPU. Torch se do serverového venv neinstaluje.

Analýza sekcí a akordů má **třetí** venv, protože potřebuje torch *a* librosa
*a* checkpointy All-In-One:

```bash
uv venv --python 3.12 ~/.local/venvs/spotdl-analyze
VIRTUAL_ENV=~/.local/venvs/spotdl-analyze uv pip install "all-in-one-infer>=3.1,<4"
```

Checkpoint (~10 MB) se stáhne z Hugging Face při prvním běhu. Server volá
`analyze.py` pod tímhle interpretem; když venv chybí, endpoint vrátí 503 a
zbytek mixpultu funguje dál.

Hlasové ovládání (`voice.py`, mikrofon v horní liště) rozpoznává řeč přímo
v počítači knihovnou Vosk; nic se nikam neposílá. Potřebuje malý anglický model
(~68 MB) mimo repozitář:

```bash
mkdir -p ~/.local/share/spotdl-helper/voice && cd ~/.local/share/spotdl-helper/voice
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
```

Povel je anglicky, vždy „pick“ a jedna známá fráze v jednom dechu (seznam je
v nabídce mikrofonu a ve `voice.COMMANDS`). Jména — „pick song …“, „pick band …“,
„pick practice …“ — a věty, kterým malý model nerozuměl, čte podruhé Whisper
(`whisper-cli` z `brew install whisper-cpp`, model
`~/.local/share/spotdl-helper/voice/ggml-large-v3-turbo-q5_0.bin`) a jméno se
dohledá v knihovně podle zvuku. Vlastní povely (fráze → kroky za sebou) jsou
v `~/.local/share/spotdl-helper/voice-commands.json`. Když model nebo knihovna
chybí, endpoint vrátí 503 a zbytek aplikace funguje dál.

U každého povelu jde **naučit vlastní hlas** (mikrofon u řádku, u nového vlastního
povelu hned po uložení): tři záznamy, a co z nich rozpoznávač slyší — správně
nebo špatně — se k povelu uloží (`voice-taught.json`, záznamy ve `voice/takes/`).
Nově naučené nesmí rozbít žádný dřív uložený záznam, jinak se nepřijme. Whisper
navíc čte podruhé každou větu, která začala jasným „pick“ a povel z ní nevyšel.

Zvukovka se dvěma vstupy (kytara + bezdrátový klopový mikrofon): v nabídce
mikrofonu se vybere zařízení a **Vstup 1 / 2**. Vstupy pak přijdou odděleně
(bez potlačení ozvěny) a rozpoznává se jen ten zvolený; kytara na „Automaticky“
se vstupu s mikrofonem vyhýbá.

„pick stop“ zastaví, co právě hraje (mix ve Steems i spodní přehrávač), „pick
play“ spustí to, co je vpředu; „play/stop mix“ a „play/stop player“ to řeknou
výslovně. „pick up“ / „pick down“ je hlasitost po 3 dB (i „up up“). „pick full
screen“ zvětší okno přes obrazovku — skutečný fullscreen prohlížeč bez kliknutí
nedovolí, ten je na tlačítku v horní liště a v minipřehrávači (klávesa F).
Minipřehrávač má v Hledat i **Knihovnu** a **Složky** (hraje je sám) a mikrofon,
který přepíná poslouchání aplikace.

Minipřehrávač poslouchá sám (mikrofon v jeho liště; při otevření se zapne, když
poslouchala aplikace, nebo když v něm zůstal zapnutý). Poslouchá vždy jen jedno
okno a minipřehrávač má přednost: aplikace ustoupí a vrátí se pár vteřin poté,
co minipřehrávač zmlkne. „pick song / band / playlist <název>“ v něm hledá
(knihovna, jinak internet) a nálezy vypíše; název z nabídky řečený po „pick“ je
výběr (`/api/voice/best`). „pick next page“, „pick play all“, „pick back“,
„pick next song“. U kapely se ukážou podobné (z profilu interpreta).

Hledat v minipřehrávači začíná seznamem **Steems** (písně připravené na
cvičení). Seznamy se přepínají hlasem: „pick stems list“, „pick library“, „pick
folders“, „pick internet“, „pick playlists“, „pick next list“ / „pick previous
list“. „pick play <název>“ pustí píseň toho jména — nejdřív z toho, co je
vypsané, pak ze seznamu, který je vybraný, a když tam není, z ostatních (Steems
→ knihovna → internet; řekne odkud). „pick find playlist <název>“ vypíše
playlisty (složky v knihovně i z internetu), „pick play <název> playlist“ jeden
otevře, pak „pick play <název písně>“ nebo „pick play all“. Každá položka
každého seznamu má číslo a dá se vybrat i jím: „pick play twenty five“ nebo
„pick play song seven“ pustí píseň s tím číslem, „pick playlist twenty“ otevře
dvacátý playlist (čísla 1–999, anglicky).

„pick show me stems / favorites / bands (artists) / library / folders /
playlists“ ukáže v minipřehrávači, co už máš: Steems, oblíbené songy (ty, které
jsou ve Steems, hrají bez kytary), kapely poskládané ze Steems a knihovny,
knihovnu, složky a tvoje playlisty (oblíbené a složky knihovny). „pick search
<název>“ (i „pick find <název>“) hledá nejdřív tam — Steems, oblíbené,
knihovna — a co najde, ukáže a pustí; nic se nestahuje ani znovu nerozděluje.
Teprve co nemáš, vyhledá na internetu a nechá vybrat.

V aplikaci „pick show me …“ otevírá její sekce: stems, playlists, bands /
artists (Kapely a umělci), library, favorites (rozbalí Oblíbené v levém
panelu), podcasts, tools, tabs, loops, chords, analyzer, loop studio. „pick
page down / next page“ a „pick page up / previous page“ listují stránkou.
„pick help“ (i „pick help open“) ukáže povely v panelu vpravo; zůstane tam i
při dalších povelech, listuje se v něm stejně („pick page down / up“) a zavře
ho „pick help close“ nebo „pick nothing“. Nabídka aplikace neukazuje povely,
které umí jen minipřehrávač, a naopak.

Když Pick nerozumí, uprostřed okna zabliká velké poloprůhledné logo s nápisem
(výchozí „Say it again!!!“) a zazní tvoje nahrávka. Obojí se nastavuje v nabídce
mikrofonu v řádku „Again!!!“: nápis se přepíše, „● Nahrát“ vezme 3 vteřiny
z mikrofonu (ticho se ořízne, hlasitost se vytáhne), ▶ to vyzkouší, ✕ nahrávku
smaže (pak řekne počítač „Again!“). Uloženo je to vedle vlastních povelů
(`voice-again.json`, `voice-again.wav`; `/api/voice/again`), platí i pro
minipřehrávač.

„pick save song“ (nebo hvězdička u názvu) uloží hrající píseň do oblíbených
songů a server ji připraví na cvičení (`POST /api/practice/save`, stav
`GET /api/practice/saves`): najde tab (do Pick Tabs) a text, píseň z internetu
stáhne do Songs, rozdělí na stopy. Běží to na serveru, okno může být zavřené.

„pick play in mini“ pošle hrající píseň do minipřehrávače (rozdělí ji, když ve
Steems není): hraje tam doprovod bez kytary (`/api/stems/<píseň>/backing`) a
kytara vedle něj na druhém přehrávači drženém v kroku, takže „pick guitar on /
off“ (nebo tlačítko GTR) ji přidá a ubere bez zastavení. Rozdělené písně jsou
v minipřehrávači i v Hledat → Steems. V aplikaci: „pick tabs“, „pick lyrics“,
„pick mixer“.

„pick help“ ukáže nabídku povelů (v aplikaci i v minipřehrávači). Zůstane,
dokud si nevybereš nebo neřekneš „nothing“; dokud je na obrazovce, povel se
říká bez „pick“ a jedno slovo ze slovníku nabídku zúží
(„guitar“). Čemu Pick nerozuměl, ačkoli to bylo na něj, na to odpoví nahlas
„Again!“ — a po tu dobu neposlouchá, aby neslyšel sám sebe.

**Poslouchání bez otevřeného okna** — položka „Poslouchat hlasem“ v liště. Server
pak čte mikrofon sám (ffmpeg/avfoundation, stejné zařízení a vstup jako
v nabídce mikrofonu) a krmí stejný rozpoznávač. Okno, které poslouchá, má
přednost (minipřehrávač, pak aplikace). „pick open“ otevře okno Picku, „pick
mini player“ minipřehrávač; cokoli jiného předá oknu aplikace, a když žádné
není, otevře pro to minipřehrávač. macOS musí mít pro Pick povolený mikrofon
(Nastavení systému → Soukromí a zabezpečení → Mikrofon) — bez toho přichází
úplné ticho a lišta na to upozorní.

Zapnuté poslouchání si aplikace pamatuje a po otevření ho spustí sama. Zvenku
(Siri přes Zkratky) ho přepíná `GET /api/voice/remote?on=1` / `?on=0` / bez
parametru; mikrofon čte otevřené okno Picku, takže bez něj přijde 409.

## Trenér zpěvu

Ve Steems, Panely → **Trenér zpěvu** (`sing.py`, modul `Sing` ve stránce). Server
jednou přečte melodii z odděleného zpěvu (YIN v numpy, `melody.json` u stop);
stránka čte výšku hlasu z mikrofonu (AudioWorklet, 16 kHz, čtení každých 16 ms)
a kreslí ji přes běžící tóny originálu. Tón se hodnotí celý, podle středu toho,
co na něm zaznělo: do ±25 centů trefa, do ±60 blízko; oktáva výš nebo níž se
počítá. Po každém řádku textu přečte Whisper nahraný úsek a slova se porovnají
s textem (správně / špatně / vynecháno) — pozná slova, ne výslovnost, a u zpěvu
se plete víc než u řeči. Dozpívaný pokus se uloží do `sing-history.json` u stop;
nejhorší řádky jdou pustit ve smyčce znovu. Mikrofon a vstup zvukovky se berou
z nastavení hlasového ovládání. Hudba patří do sluchátek.

**Běžící text** (`LyricRun`): text písně jako pás, který jede pod pevnou značkou
stejně jako taby — v trenéru pod tóny, ve Steems v pruhu TEXT (tlačítko
„Po řádcích“ vrátí tři řádky pod sebou). Každý řádek má svou přirozenou šířku
a pás mění rychlost: řádek přejede značku přesně za dobu, kdy se zpívá. Kdy
zpěv řádku končí a kde se nadechuje, bere z melodie (`/api/stems/<píseň>/melody/kept`
— jen už spočítaná, nic nepočítá); bez ní to odhadne z délky textu. Řádek, který
nesedí, se dá na stopě Text **chytit a posunout** (mezi sousedy; uloží se
v prohlížeči k písni, „↺ řádky“ to vrátí); „−/+“ dál posouvá celý text.

**Slyšet se ve sluchátkách**: mikrofon jde do zvukového kontextu mixu a ven
s hudbou, s efekty čistě ve Web Audio — reverb (dozvuk z vygenerovaného šumu),
delay (slapback 115 ms), echo (380 ms se zpětnou vazbou), doubler (dvě
rozladěné kopie vlevo a vpravo) a druhý hlas (kopie posunutá o interval,
AudioWorklet `pick-shift`). Hodnotí se pořád holý mikrofon, efekty na výsledek
nemají vliv. Jen se sluchátky.

**Mikrofon a nahrávka**: posuvník „mikrofon“ zesílí vstup až o 30 dB (pro
hodnocení, nahrávku i sluchátka), vedle něj ukazatel úrovně. Se zaškrtnutým
„nahrát do stopy“ se dozpívaný pokus (aspoň 8 s, ne smyčka jednoho řádku) uloží
jako běžná nahraná stopa `take-N` s názvem „Můj zpěv …“ — bez efektů, zarovnaná
na píseň přes `playLog` jako nahrávky kytary. Stopa je po uložení ztlumená;
tlačítka „já / originál / oba / já s kapelou“ ve výsledku ji pustí k porovnání.

## Asistent

„pick listen“ (nebo pole Asistent v nabídce mikrofonu) vezme jednu českou větu
— „pusť Master of Puppets, ztlum kytaru a zpomal na 80“ — a provede ji.
Whisper ji v počítači přepíše, `assistant.py` ji pošle Claudovi (Anthropic API,
`claude-haiku-4-5`) s popisem aplikace (`SYSTEM`) a nástroji (`TOOLS`); nástroje
běží ve stránce (`Voice.tool`) a jejich výsledky se vracejí, dokud model
neskončí. Když se asistent zeptá, aplikace poslouchá odpověď bez aktivačního
slova. Odpověď se zobrazí a volitelně přečte.

API klíč patří do `~/.local/share/spotdl-helper/anthropic-key.txt` (nebo
`ANTHROPIC_API_KEY`), do prohlížeče se nedostane. Knihovna `anthropic` chce
novější balíčky, než jaké drží server, proto má vlastní prostředí:

```bash
python3 -m venv ~/.local/venvs/pick-assistant
~/.local/venvs/pick-assistant/bin/python -m pip install anthropic
```

Když aplikace umí něco nového, co má asistent ovládat, patří to do `SYSTEM`
a `TOOLS` — nic jiného o aplikaci neví.

## Lišta macOS

```bash
open Pick.command          # nebo: ~/.local/venvs/spotdl/bin/python menubar.py
```

V liště se objeví ♪ (♫ když něco hraje) a v menu je aktuální skladba,
**Otevřít aplikaci** (Chrome bez lišt, na celou viditelnou plochu), **Mini
přehrávač**, transport, hlasitost a **Ukončit** — to zastaví i server.

Okno se zvětšuje explicitně přes `--window-size` podle `NSScreen.visibleFrame`,
ne přes `--start-fullscreen`: ten se v režimu `--app` dost často ignoruje a
aplikace se otevřela v malém výchozím okně. Když už okno běží, menu ho jen
vytáhne dopředu — pozná to podle toho, že se hlásí přes stav dálkového
ovládání.

Lišta ani mini přehrávač **nic nepřehrávají**. Druhé okno se nedostane k
`<audio>` elementu ani k Web Audio grafu toho prvního, takže by muselo pustit
vlastní zvuk a hrálo by se dvakrát. Místo toho okno, které hraje, publikuje
svůj stav na `POST /api/remote/state` a vybírá si příkazy z
`GET /api/remote/commands`; lišta a mini je tam ukládají přes
`POST /api/remote/command`. Zvuk tak zůstává na jednom místě.

Když by okno zmizelo bez rozloučení, stav po dvanácti vteřinách zestárne a
lišta i mini přestanou tvrdit, že něco hraje.

## Testy

```bash
cd ~/.local/share/spotdl-helper/app
~/.local/venvs/spotdl/bin/python -m pytest
```

`pytest.ini` vynechává `vendor/` — submoduly mají vlastní testy a závislosti
(numpy, torch), na kterých by holé `pytest` spadlo už při sběru testů.

## Poznámky

- **Stemy**: model `htdemucs_6s`, 6 stop (vocals, drums, bass, guitar, piano,
  other), ukládané jako **320k MP3** — šest bezeztrátových stop pětiminutové
  skladby zabere přes 300 MB a knihovna takových sad zaplní disk. Čtení
  přijímá i WAV a FLAC, takže starší sady fungují dál; `POST /api/stems/convert`
  je převede (původní WAV maže až po ověření, že MP3 vzniklo). Demucs ukládá do `Stems/.raw/htdemucs_6s/<track>/`, server výsledek
  přesune do `Stems/<název>/`.
- **Streamování**: YouTube blokuje iframe embed u spousty klipů, proto se
  přímá media URL řeší přes yt-dlp a bajty se proxují do `<audio>`/`<video>`.
  Videa bez progresivního formátu (jen DASH) se stáhnou a slijí do dočasného
  souboru; ten se uklidí po vypršení cache (45 min).
- **Texty**: nejdřív LRCLib (synchronizované), a když nemá — což u českého
  repertoáru bývá skoro vždy — spadne to na YouTube Music přes `ytmusicapi`,
  který texty nese pro většinu toho, co streamuje. Ty jsou bez časování, takže
  se jen rolují.
- **Knihovna**: dvoupanelový prohlížeč — strom složek v levém railu (děti se
  načítají až při rozbalení), tabulka stop v hlavní ploše. Sloupce Název /
  Interpret / Album / čas se čtou ze skutečných tagů přes `mutagen`, ne
  hádají z názvu souboru; výsledky se cachují podle mtime a velikosti.
- **Podcasty**: `region=cz` je výchozí. Jedno hledání „podcast" na YouTube
  Music vrátí prakticky nic českého (řadí podle globální popularity), takže se
  pouští sada českých dotazů a výsledky se slučují.
- **Beat grid**: `beatgrid.py` běží pod interpretem Demucsu (ten má torch) a
  trackuje beaty na **izolované stopě bicích** — nejčistší onset signál, jaký
  je k dispozici, a důvod, proč se to dělá až po separaci. `beat_this` je
  transformer, který vrací i *downbeaty*, takže mřížka kreslí skutečné taktové
  čáry místo počítání po čtyřech od začátku. Librosa se použít nedá: její
  tempo prior je centrovaný na 120 BPM, takže 180 BPM skladbu vyhodnotí jako
  90 a klik jde v půlce — a žádná metrika kvality to nepozná, protože
  půltaktová mřížka má pod každou dobou reálný úder bicích. Výsledek se
  cachuje do `Stems/<název>/beats.json`.
- **Dopočítávání mřížky**: tracker začíná až u prvního úderu bicích, takže
  intro (často 20–30 s) by zůstalo bez mřížky a takt 1 by padl tam, kde
  nastoupí bicí. `beatgrid.py` proto surový výstup ještě zpracuje: sjednotí
  dvojité tempo na jedno období, doplní doby přes pasáže bez bicích, natáhne
  mřížku k nule i na konec skladby a taktové čáry přepočítá na pravidelné,
  fázově zarovnané s detekovanými downbeaty. Vrací i `detected_from/to`, takže
  UI kreslí dopočítané takty slaběji než změřené.
- **Tempo křivka**: mřížka, která jde do přehrávače, je z definice pravidelná
  (jedno napasované období na segment), takže křivka z ní nakreslená je rovná
  čára a neřekne nic. `_tempo_curve()` proto kreslí ze **syrových detekcí**,
  kde je vidět, kde kapela zrychlí do refrénu nebo se zadrhne v breakdownu.
  Klouzavý medián, ne průměr: jedna vynechaná detekce zdvojnásobí mezeru a
  průměr ten špic rozmaže přes celé okno.
- **Sekce skladby**: `analyze.py` pod analytickým venvem pouští **All-In-One**
  (Kim & Nam, ISMIR 2023, checkpoint `harmonix-all`) — transformer nad
  spektrogramy čtyř stop, který vrací hranice i funkční názvy (intro, sloka,
  refrén, bridge, sólo, instrumentál, break, outro). Model chce přesně bass,
  drums, other a vocals, takže se našich šest stop nejdřív smíchá na čtyři.
  Hranice se pak přichytí na taktovou čáru, pokud je do jedné doby daleko;
  dál už ne, aby se opravdu mimomřížkový předěl neposunul.
- **Akordy**: žádný předtrénovaný model nešel použít — madmomu chybí v forku
  `madmom-infer` chordový modul, chord-extractor chce Vamp binárku a autochord
  táhne TensorFlow. Takže klasika: **beat-synchronní chroma proti šablonám
  akordů, uhlazená Viterbim** — ale s výhodou, kterou mix nemá: chroma se
  počítá jen z harmonických stop (other + guitar + piano), bez bicích
  (širokopásmový šum přes všechny třídy) a bez zpěvu (portamento a vibrato
  chroma rozmažou). Baskytara hlasuje zvlášť o basovém tónu, což je to, co
  udělá obrat viditelným. Slovník: dur, moll, 7, maj7, m7, sus2/4, dim, aug a
  **power chord** — bez něj by metal vycházel celý jako maj7.
- **Kytara naživo**: v mixpultu je nad vším kanál **Kytara** — výběr
  zvukovky, noise gate, fader, pan, sólo, mute, metr a nahrávání. Vstup se
  otevírá s **vypnutým** echo cancellation, noise suppression i AGC: to jsou
  věci stavěné na řeč, na kytaře zabijí dynamiku. Gate je vlastní
  AudioWorklet **bez lookaheadu** — lookahead by otevíral bránu před úderem,
  ale každý jeho vzorek je latence navíc, a na nástroji, který zrovna hraješ,
  stojí latence víc než měkčí náběh.
- **Zesilovač**: NAM (Neural Amp Modeler) ve WebAssembly — stejná síť jako
  desktopový plugin, běží v AudioWorkletu. Engine je vendorovaný z npm
  balíčku `neural-amp-modeler-wasm` do `static/nam/` (415 kB wasm); repozitář
  hotové artefakty neposílá a stavět ho Emscriptenem by dalo byte za byte to,
  co npm už publikuje. Kabinet je obyčejný `ConvolverNode` — impuls **je**
  konvoluce a prohlížeč ji umí nativně. Modely a impulsy se sbírají do
  `~/.local/share/spotdl-helper/amps/` a patří appce, ne jedné písni.
- **Nahrávka je stopa**: co nahraješ, projde přes ffmpeg do 320k MP3 a uloží
  se jako `take-N.mp3` vedle rozseparovaných stop. Po odpojení kytary tam
  zůstane jako normální stopa — s vlnou, mřížkou i faderem.
- **Mix se ukládá sám**: každý fader, mute, sólo, pan, pitch, posun klipu,
  smyčka, zoom i nastavení metronomu (hlasitost, pan, zvuk kliku, rychlost) se
  po vteřině nečinnosti zapíšou do `Stems/<název>/session.json`. Dřív to bylo
  tlačítko do `localStorage`, což znamenalo dvě věci: muselo se na něj myslet,
  a mix žil v jednom profilu prohlížeče — lišta otevírá appku ve vlastním
  profilu, takže mix nastavený v normálním okně tam prostě nebyl.
- **Vlastní sekce**: detekce je výchozí bod, ne verdikt. Sekce jde v mixpultu
  táhnout, měnit jim délku, přidávat a mazat a pojmenovat z nabídky názvů,
  která se dá na každé písni upravit. Hranice jsou **sdílené** — posun jedné
  sekce bere místo jednomu sousedovi a dává druhému, jinak by sekce mezi
  dvěma jinými nešla pohnout vůbec. Přichytávání jde na taktové čáry (a když
  není žádná blízko, tak na doby); Alt ho vypne. Ukládá se do
  `Stems/<název>/sections.json`, tedy **vedle** `analysis.json` — nové
  spuštění detektoru ruční úpravy nepřepíše.
- **Fit tempa skenem**: detekované časy jsou *důkaz* pro mřížku, ne mřížka
  sama. Medián rozestupů tempo neurčí — `beat_this` kvantuje výstup na svůj
  frame grid, takže rozestupy jsou násobky 20 ms a jejich medián je vychýlený
  (na jedné skladbě medián 115,38 BPM trefil 100 ze 399 dob, sken 113,68 BPM
  trefil všech 399). Regrese přes index doby má zase past celočíselného
  přiřazení: přes díru vyjdou indexy špatně a sklon je následuje. Proto se
  perioda skenuje a boduje podle toho, kolik dob na ni skutečně padne; fáze je
  cirkulární průměr. Úsek, kde se tracker chytil na 1,5× puls, se tak stane
  odlehlou hodnotou místo aby mřížku strhl.
