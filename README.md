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
| Stemy         | `~/Music/spotdl/Stems/{skladba}/{stem}.wav`           |

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
| `ffmpeg` | `/opt/homebrew/bin/ffmpeg` | převody, slučování DASH videa     |

Demucs má **vlastní** izolovaný interpret (uv/pipx) s torchem — server si
z jeho shebangu přečte cestu a subprocessem ověří dostupnost MPS, aby
separace běžela na GPU. Torch se do serverového venv neinstaluje.

## Testy

```bash
cd ~/.local/share/spotdl-helper/app
~/.local/venvs/spotdl/bin/python -m pytest
```

`pytest.ini` vynechává `vendor/` — submoduly mají vlastní testy a závislosti
(numpy, torch), na kterých by holé `pytest` spadlo už při sběru testů.

## Poznámky

- **Stemy**: model `htdemucs_6s`, 6 stop (vocals, drums, bass, guitar, piano,
  other). Demucs ukládá do `Stems/.raw/htdemucs_6s/<track>/`, server výsledek
  přesune do `Stems/<název>/`.
- **Streamování**: YouTube blokuje iframe embed u spousty klipů, proto se
  přímá media URL řeší přes yt-dlp a bajty se proxují do `<audio>`/`<video>`.
  Videa bez progresivního formátu (jen DASH) se stáhnou a slijí do dočasného
  souboru; ten se uklidí po vypršení cache (45 min).
- **Texty**: LRCLib, synced i plain.
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
- **Fit tempa skenem**: detekované časy jsou *důkaz* pro mřížku, ne mřížka
  sama. Medián rozestupů tempo neurčí — `beat_this` kvantuje výstup na svůj
  frame grid, takže rozestupy jsou násobky 20 ms a jejich medián je vychýlený
  (na jedné skladbě medián 115,38 BPM trefil 100 ze 399 dob, sken 113,68 BPM
  trefil všech 399). Regrese přes index doby má zase past celočíselného
  přiřazení: přes díru vyjdou indexy špatně a sklon je následuje. Proto se
  perioda skenuje a boduje podle toho, kolik dob na ni skutečně padne; fáze je
  cirkulární průměr. Úsek, kde se tracker chytil na 1,5× puls, se tak stane
  odlehlou hodnotou místo aby mřížku strhl.
