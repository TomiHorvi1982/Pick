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

## Omezení fáze 1

- **Paměť:** stopy drží deck celé v paměti jako Int16: ~6 MB na minutu a stopu,
  tj. šestiminutová píseň se 6 stopami ≈ 380 MB na deck. Neměřeno na slabším PC.
- Písně bez stop DJ nenabízí (fáze 2).
- Nahrávka mixu se stáhne jako `.webm` (Opus 256 kb/s), neukládá se do knihovny.
- Bez beat gridu fungují smyčky podle odhadnutého BPM a SYNC se nezapne.
- MIDI kontroler zatím ne (navázat na učení MIDI pedálu).
- Ověřeno jen v Chromu na macOS; zvuk jsem neposlouchal, ověřeny jsou pozice,
  rychlosti a stav přes stránku.
