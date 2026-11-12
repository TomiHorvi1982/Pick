# Pick DJ — dva decky nad stopami (návrh)

## Proč

Pick umí písně rozdělit na stopy (Steems, `htdemucs_6s`) a hrát je v mixpultu,
ale ne mixovat dvě písně za sebou jako DJ. Dosud to šlo jen přes „Otevřít
v Neural Mix Pro“, tedy mimo Pick. Cíl: DJ režim přímo v Picku nad vlastní
knihovnou stop.

## Rozhodnutí (uživatel, 2026-11-12)

- **Cesta A:** všechno ve webové aplikaci a na serveru Picku, žádný nativní engine.
- **Windows:** DJ stránka i v Pick knihovně na PC (Docker) nad stopami z kopie karty.
- Fáze 1 nejdřív; separace během hraní (fáze 2) a Windows (fáze 3) navazují.

## Fáze

1. **Decky nad hotovými stopami** (tato změna).
2. **Separace během hraní** pro písně bez stop: trvalý worker s `htdemucs_6s`
   v demucs venv, okna 7,8 s s náskokem před playheadem, výsledek se uloží do
   Steems. Změřeno (M1 Pro, MPS): 0,47–0,76 s na okno = ~16× real-time;
   CPU ~4×. Sladit s `audioguard` (ten Demucs během přehrávání pozastavuje).
3. **Windows:** `static/dj*.js` je čistý web bez závislosti na macOS; knihovna
   na PC ho bude servírovat nad kopií karty. Separace během hraní tam závisí
   na hardwaru PC (Docker na Windows obvykle bez GPU) — ověřit na místě.

## Fáze 1 — architektura

| Soubor | Co dělá |
|---|---|
| `static/dj-core.js` | čistá logika bez DOM: beat grid (interpolace, lokální BPM), smyčky a cue na doby, SYNC (tempo + fáze, poloviční/dvojnásobné tempo), křivky crossfaderu, EQ a filtru; testováno v node |
| `static/worklets/dj-deck.js` | AudioWorklet jednoho decku: stopy jako Int16, mix se ziskem každé stopy (plynulé změny), čtení libovolnou rychlostí, seek / smyčka / nudge přesně na vzorek, hlášení pozice |
| `static/dj.js` | DJ obrazovka (tlačítko **DJ** v hlavičce knihovny), zvukový graf, knihovna písní se stopami |
| `static/stretch/SignalsmithStretch.mjs` | už v Picku (looper, Jam); DJ ho používá v live režimu jako posun výšky pro **key lock** |

Graf jednoho decku:

```
dj-deck worklet ─▶ [Signalsmith: key lock / tónina] ─▶ Low ─▶ Mid ─▶ High ─▶ Filtr ─▶ Fader ─▶ Crossfader ─▶ mix
                                                                         └▶ Cue (před faderem) ─▶ výstupy 3–4
                                                              Crossfader ─▶ Echo (doby) / Reverb ─▶ mix
mix ─▶ Master ─▶ AudioOut (výstupy 1–2) · MediaRecorder (REC)
```

- **Data:** `GET /api/stems` (jen kompletní písně s Demucs stopami),
  `GET /api/stems/{name}/{stem}` (MP3 → `decodeAudioData` při 44,1 kHz → Int16),
  `GET /api/stems/{name}/beats` (beat_this grid; počítá se při prvním dotazu).
- **SYNC:** tempo podle lokálního BPM obou gridů; fáze v rámci doby (posun
  nejvýš o půl doby); trvalé držení jemným ohybem rychlosti (±3 %).
  Ověřeno v prohlížeči: Bryan Adams (grid 65,6) → 1979 (127,1): rychlost 1,032,
  fázová chyba 5 ms → pod 1 ms během 5 s.
- **Key lock:** rychlost mění worklet (varispeed), Signalsmith výšku vrací
  (`-12·log2(rate)`) a přidá posun tóniny ±6 půltónů. Latence 120 ms se odečítá
  od zobrazené pozice i v SYNC.
- **Cue a hot cue** se ukládají v prohlížeči (`localStorage`, klíč `pick-dj-cues:<píseň>`).
- **AudioBus:** DJ se hlásí jako přehrávač `dj`; spuštění jiného přehrávače DJ zastaví a naopak.
- **Výstup:** stejný jako zbytek Picku (`AudioOut`). Odposlech do sluchátek
  (🎧) jen u zařízení se 4+ výstupy (cue na 3–4); jinak je tlačítko neaktivní.

## Fáze 2 — separace během hraní

Knihovna DJ ukazuje všechny písně: ze Steems (štítek **stopy**) i ze `Songs`
bez stop (štítek **živě**; delší než 20 minut **dlouhá**, nelze načíst).

```
page ──POST /api/dj/live/open {rel}──▶ djlive.py ──ffmpeg──▶ dj-live/<id>/mix.wav (44,1 kHz s16 stereo)
     ◀── {id, frames, core, windows, stems} (nebo {separated} když už je ve Steems)
page ──GET …/mix──▶ hraje hned celý mix
page ──GET …/window/k (nejbližší chybějící od playheadu)──▶ worker ──▶ w<k>.pcm
page ──GET …/beats──▶ beatgrid.py nad mix.wav (sdílené _run_beatgrid)
filler (na pozadí, ve volném čase workeru) ──▶ všechna okna ──▶ 6× MP3 320k do Stems/.raw/htdemucs_6s/<píseň>/
                                            ──▶ _finalize_stems_job (stejné uložení jako po Demucs úloze)
```

- **Jedno dekódování:** `mix.wav` hraje stránka, odděluje worker a z něj se
  hledají beaty, takže si všechny tři odpovídají přesně na vzorek
  (dva MP3 dekodéry se jinak liší o stovky vzorků).
- **Okna** na pevné mřížce: jádro 282 242 vzorků (6,4 s), model vidí navíc
  0,7 s na každé straně. Hranice oken jsou natvrdo (Demucs CLI je prolíná),
  ale každý vzorek má kontext ≥ 0,7 s z obou stran.
- **Worker** (`dj_separator.py`, interpret Demucs): `htdemucs_6s` načtený jednou,
  JSON řádek + binární PCM přes stdin/stdout. Požadavky stránky mají přednost
  před dopočítáváním. Když server spadne, worker skončí sám (zavřený vstup).
- **Deck** (`dj-deck.js`): kde okno ještě není, hraje mix se ziskem rovným
  průměru stop; po příchodu okna hraje stopy.
- **Ochrana zvuku** (`audioguard`) worker nezpomaluje ani nepozastavuje
  (`KEEP`): krmí deck, který právě hraje.
- **Disk:** `~/.local/share/spotdl-helper/dj-live/`, drží se 3 poslední písně
  (~250 MB oken + 40 MB mixu na čtyřminutovou píseň); beat gridy v `dj-live/beats/`.

Změřeno (M1 Pro, MPS):

| Krok | Čas |
|---|---|
| start workeru + model | 1,8 s (jednou) |
| otevření nové písně (ffmpeg) | 0,5 s |
| mix do stránky | 0,1 s |
| jedno okno (6,4 s audia) | 0,6 s; s předností před dopočítáváním 1,5 s |
| celá čtyřminutová píseň do Steems | ~1 min |
| beat grid (beat_this nad mixem, souběžně s workerem) | 16,5 s |

## Fáze 3 — DJ v Pick knihovně na PC

Knihovna (`deploy/pick-library/library.py`, Docker na PC, za přihlášením) dává
do své stránky (`jam.html`, kterou sdílí s Jam bránou na Macu, se nemění) tři
skripty: `dj-core.js`, `dj.js` a `dj-library.js`. Ten po přihlášení přidá do
hlavičky tlačítko DJ a přepne DJ na zdroj knihovny (`PickDJ.setSource`):

| | Pick na Macu | Knihovna na PC |
|---|---|---|
| seznam | `/api/stems` + `/api/library/browse` (i písně bez stop) | `/g/songs` (jen se stopami) |
| stopa | `/api/stems/{name}/{stem}` (MP3) | `/g/stem` (Opus 128k, připravený dopředu) |
| beat grid | `/api/stems/{name}/beats` (spočítá) | `/g/beats` (jen čte `beats.json` z karty) |
| separace během hraní | ano (`djlive.py`) | ne: v kontejneru není Demucs ani GPU |

Ověřeno: knihovna spuštěná lokálně nad kartou (bez Dockeru, `PICK_PREPARE=0`),
přihlášení testovacím uživatelem, dvě písně ze Steems, SYNC 1979 → Bryan Adams
s fázovou chybou pod 1 ms. Na Windows a v Dockeru neověřeno.

## Omezení fáze 1

- **Paměť:** stopy (a u fáze 2 i mix) drží deck celé v paměti jako Int16: ~6 MB na minutu a stopu,
  tj. šestiminutová píseň se 6 stopami ≈ 380 MB na deck. Neměřeno na slabším PC.
- Nahrávka mixu se stáhne jako `.webm` (Opus 256 kb/s), neukládá se do knihovny.
- Bez beat gridu fungují smyčky podle odhadnutého BPM a SYNC se nezapne.
- MIDI kontroler zatím ne (navázat na učení MIDI pedálu).
- Ověřeno jen v Chromu na macOS; zvuk jsem neposlouchal, ověřeny jsou pozice,
  rychlosti a stav přes stránku.
