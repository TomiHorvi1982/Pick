# Spotifylisty

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
| Desktop app   | `~/Desktop/Spotifylisty.app` (menubar launcher)       |
| Venv          | `~/.local/venvs/spotdl/`                              |
| Konfigurace   | `~/.local/share/spotdl-helper/config.json`            |
| Log serveru   | `~/.local/share/spotdl-helper/server.log`             |
| Hudba         | `~/Music/spotdl/` (`Songs/`, `Stems/`, `Playlists/`)  |
| Stemy         | `~/Music/spotdl/Stems/{skladba}/{stem}.mp3`           |

Kořen hudby lze změnit v Nastavení; uloží se do `config.json`.

## Spuštění

Přes desktop app (`~/Desktop/Spotifylisty.app`) — ikona 🎧 v menu baru
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

## Lišta macOS

```bash
open Spotifylisty.command          # nebo: ~/.local/venvs/spotdl/bin/python menubar.py
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
