# Tónový otisk — zvuk kytary z nahrávky na živé kytaře

Datum: 2026-10-15
Stav: uživatel schválil směr („postav to, jak říkáš“), staví se fáze 1
Velký celek „hybridní kytara“ má tři projekty: **1. Tónový otisk** (tento
dokument), 2. Kytara → taby, 3. Virtuální kytara (tab zahraný zvukem otisku).
Pořadí: 1 → 2 → 3; 3 použije otisk z 1.

## Cíl a hranice

- Živá kytara (iRig PRO DUO → Pick) zní jako rozseparovaná kytara vybrané písně.
- Hráč neřeší aparáty ani efekty: jádro zkreslení (NAM profil z jeho knihovny
  `~/.local/share/spotdl-helper/amps`, 86 modelů) a efekty vybírá engine sám.
- Vše lokálně a zdarma. Měří se, neodhaduje: shoda v %, křivky, slepý test.
- Neumí (zatím): delay, chorus/flanger, wah, oktávery; dvě kytary přes sebe
  napodobí jen zdvojením. Nahrávka originálu je po separaci (drobné vady).

## Řetězec (stejný offline i živě)

Čistý signál kytary (za GAIN, před gate) →
1. `drive_db` — GainNode,
2. předekvalizace (BiquadFilterNode, vzorce Web Audio):
   highpass `pre.hp` Hz (Q = −3.0103 dB, tj. Butterworth — u Web Audio je Q
   u high/lowpass v dB), peaking 750 Hz Q 0.7 `pre.mid_db`, lowshelf 250 Hz
   −`pre.tilt_db`/2, highshelf 2 kHz +`pre.tilt_db`/2 (shelfy se S = 1),
3. jádro: NAM model `core.id` (nebo žádné = čistý zvuk),
4. `print-ir.wav` — ConvolverNode `normalize = false`: všechno lineární za
   jádrem (bedna, mikrofon, mix i výstupní hlasitost) jako jedna minimální-fázová
   IR (2048 vzorků),
5. `room` — dozvuk: deterministická IR (šum × exp. útlum, PRNG mulberry32,
   seed 1), `mix`, `decay` s,
6. `width` — zapne doubler živé kytary (už v Picku je).

Žádný blok nepřidává zpoždění kromě zvukové karty. Kompresor kytary je
s otiskem obejitý (nahrávka DI ho neobsahuje). Změna GAIN po nahrání se
vyrovná: `drive` živě = `drive_db` + (GAIN při nahrávání − GAIN teď).

NAM se offline počítá **stejným WebAssembly enginem** jako živě
(`tone_nam.mjs` v Node načte `static/nam/nam-worklet.js` + `.wasm`), tedy
bez rozdílu mezi tím, co engine slyšel při hledání, a tím, co hraje živě.

## Postup (fáze 1 = varianta A „hledání a doladění“)

1. **Vzor**: píseň + úsek 4–40 s (smyčka v mixu); server navrhne úseky, kde
   kytara hraje nejvíc a ostatní nejméně (`/sections`). Stopa: `guitar`, nebo
   `guitar_solo` / jiná kytarová, když je.
2. **Kalibrace**: hráč zahraje úsek s písní (předehra 3 s). Nahrává se čistý
   signál (take-recorder za GAIN) a přes `songAtCtx` + latenci se zarovná na
   čas písně → `tone/take-N.wav` + `takes.json` (start, sr, GAIN).
3. **Fit** (`tone_worker.py` v `~/.local/venvs/pick-tone`):
   - dorovnání zpoždění take vs. originál (křížová korelace náběhů, ±150 ms),
   - aktivní rámce (hrají oba), vlastnosti: LTAS po 1/6 oktávy; v 8 oktávových
     pásmech rozptyl hlasitosti (p90−p10), plochost spektra; crest; sustain po
     náběhu (+40/150/300 ms); souběh obálek; dozvuk na koncích tónů; šířka L/P,
   - pro každého kandidáta: vyrenderovat jádro, **barvu dopočítat přímo**
     (poměr LTAS → min-fázová IR, vyhlazení, ±24 dB), porovnat vlastnosti
     nezávislé na barvě → ztráta,
   - síto: všechna jádra + „bez jádra“ × drive {−9, 0, +9} dB na ≤ 8 s,
   - doladění: 4 nejlepší × Nelder–Mead přes (drive, hp, mid, tilt), ~36 kroků,
   - dozvuk: mřížka mix × decay podle dozvuku na koncích tónů; šířka z korelace L/P,
   - výstup: `print.json` (řetězec + zpráva + pořadí jader), `print-ir.wav`,
     `you.wav` (take přes otisk).
4. **Živě**: `static/tone.js` načte jádro (Amp), IR, předekvalizaci a dozvuk do
   řetězce kytary; otisk se dá vybrat i u jiné písně (seznam otisků).
5. **Kontrola**: shoda v %, dvě křivky spektra, ukazatele (zkreslení, sustain,
   jas, basy, prostor, šířka); „Doladit“ = další take a nový fit od minulého
   výsledku; A/B a **slepý test** z mixů úseku, které složí server
   (`/abx`: ostatní stopy + originální kytara, nebo + `you.wav`).

## Rozhraní

Server (`server.py`, worker přes subprocess jako ostatní modely):
`GET /api/tone/{song}` · `GET /api/tone/{song}/sections` ·
`POST /api/tone/{song}/take` · `DELETE /api/tone/{song}/take/{n}` ·
`POST /api/tone/{song}/fit` · `GET|DELETE /api/tone/job` ·
`GET /api/tone/{song}/file/{name}` · `POST /api/tone/{song}/abx` ·
`GET /api/tone/prints`. Hlídání paměti `_sysload.room`.
Data: `Stems/<píseň>/tone/`.

## Testy

- `tests/test_tone.py`: endpointy s podstrčeným workerem, validace, úseky na
  umělých stopách, mix pro slepý test.
- `tone_worker.py --selftest`: umělá kytara (Karplus–Strong) přes známý řetězec
  (jádro z knihovny, drive, předekvalizace, barva, dozvuk) → fit musí najít
  stejné jádro, drive ±4 dB, shodu ≥ 85 %; běží z pytestu, když je venv, Node
  a modely.

## Další fáze (ne teď)

- B „Přesný otisk“: natrénovat vlastní NAM z párů (hráčův DI ↔ originál po
  notách), export .nam + IR (ToneRig, NAM plugin).
- Nativní engine (C++ NAM core + CoreAudio, ~5–8 ms), jen bude-li latence
  v prohlížeči málo.
- Projekty 2 (taby) a 3 (virtuální kytara).
