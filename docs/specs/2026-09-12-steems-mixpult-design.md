# Steems — separace na stopy (Demucs) a mixpult

Datum: 2026-09-12
Stav: schváleno uživatelem, čeká na implementační plán
Navazuje na: `2026-09-12-library-and-player-design.md` (Knihovna, konfigurovatelná
cesta stahování) — Steems ukládá stopy pod stejný `MUSIC_ROOT`, a tlačítko
„Rozdělit na stopy" se objevuje jak u přehrávače, tak u řádků v Knihovně.

## Kontext a rozhodnutí z brainstormingu

- Separaci dělá **Demucs** (`htdemucs_6s`, 6 stop: vokál/bicí/basa/kytara/
  piano/ostatní) — na tomto Macu je už nainstalovaný jako CLI
  (`~/.local/bin/demucs`, `uv tool install demucs`), PyTorch běží s Apple
  Silicon (MPS) akcelerací. Realisticky desítky sekund až pár minut na
  píseň — ne bleskově jako komerční Neural Mix Pro (ten má vlastní
  optimalizovaný model pro DJ real-time použití, to Demucsu nesahá po
  kotníky rychlostí; nastavit to uživateli hned na začátku, ať nečeká
  zázrak).
- Separace se spouští **na vyžádání tlačítkem**, ne automaticky při
  každém stažení.
- Inspirace/zdroj kódu: **StemDeck** (github.com/stemdeckapp/stemdeck,
  Apache-2.0, 3.7k★) — architektonicky téměř identický stack (FastAPI
  backend + vanilla JS frontend bez build kroku), používá stejný
  `htdemucs_6s`, má přesně tenhle DAW mixpult (fadery, mute/solo, VU
  metry, waveform, export mixu do WAV). **Rozhodnutí: přenést/adaptovat
  jeho relevantní kód přímo do Spotifylisty** (jeden server, jedna appka),
  ne spouštět StemDeck jako druhou službu vedle. Porcované soubory dostanou
  komentář s creditem a odkazem na zdrojový repo (licence to vyžaduje/
  doporučuje).
- Mixpult je **plný editor s exportem** (ne jen synchronní přehrávání) —
  fadery, mute/solo, waveform, a export namixovaného výsledku do WAV.

## Cíl

1. Tlačítko u přehrávače (na aktuálně hrané/rozehrané skladbě), které
   nabídne buď normální stažení, nebo stažení + rozdělení na stopy.
2. Stejná možnost „🎧 Rozdělit na stopy" u už stažených písní v Knihovně.
3. Nová sekce/tab **„🎚️ Steems"** vedle Playlisty/Písně/Knihovna: seznam
   už rozseparovaných písní, jedním klikem se načtou do mixpultu.
4. Mixpult: fader + mute/solo + waveform pro každou stopu, synchronní
   přehrávání, export smíchaného výsledku do WAV.

## Mimo scope (YAGNI)

- Automatická separace při stahování.
- BPM/key detekce, loudness analýza (StemDeck to umí, my ne — nebylo
  požadováno).
- Karaoke lead/backing-vocal model (StemDeck má jako bonus) — jen pokud
  by o to uživatel později požádal zvlášť.
- Střih/posun v čase mezi stopami, více současně otevřených písní v
  mixpultu najednou.
- Sdílení/upload rozseparovaných stop mimo appku.

## 1. Vstupní body — tlačítko u přehrávače a v Knihovně

**Přehrávač:** nové tlačítko „⬇️" vedle „🎬 Video" (viz spec #1), klik
rozbalí malé menu se 2 položkami:
- „⬇️ Stáhnout píseň" — normální stažení, `/api/download` `kind:"songs"`
  (skryté/disabled, pokud aktuální skladba už je lokální soubor z
  Knihovny — tu už přece máš).
- „🎧 Rozdělit na stopy" — pokud skladba ještě nemá lokální soubor
  (`track.relPath` chybí), appka ji nejdřív normálně stáhne (stejný
  download job jako výše), a jakmile doběhne, navazuje separační job na
  výsledný soubor. Má-li skladba `relPath` už (hraje se z Knihovny),
  jde se rovnou na separaci.

**Knihovna:** u každého 🎵 řádku vedle ▶ přibude „🎧" ikon-tlačítko,
volá stejný separační endpoint s `rel` daného souboru.

## 2. Backend — separační pipeline

Znovupoužije existující `JOBS`/SSE mechanismus, který appka už má pro
stahovací joby (`/api/jobs/{job_id}/events`) — separace je jen jiný
příkaz ve stejné frontě, žádný nový job-systém.

- `POST /api/stems/separate` `{"rel": "<cesta k source audio souboru
  pod MUSIC_ROOT>"}`:
  - Ochrana cesty stejná jako u `/api/library/*` (musí zůstat pod
    `MUSIC_ROOT`).
  - Pokud `MUSIC_ROOT/Stems/<safe-name>/` už obsahuje všech 6 stop
    (soubor pro každou), rovnou vrátí `{"job_id": null, "ready": true,
    "stems_dir": "..."}` — žádné zbytečné přeseparování.
  - Jinak založí job v `JOBS` s `kind: "stems"` a příkazem
    `demucs -n htdemucs_6s -o <MUSIC_ROOT/Stems/.raw> --filename
    "{track}/{stem}.{ext}" <source_path>` (přes stejný
    `asyncio.create_subprocess_exec` + SSE log streaming jako spotdl
    download joby), po doběhnutí přesune/přejmenuje výstup do
    `MUSIC_ROOT/Stems/<safe-name>/`.
  - Frontend na dokončení joba (stejná SSE událost, co dnes spouští
    „✓ hotovo" v download šuplíku) zareaguje tlačítkem „🎧 Otevřít v
    Steems" místo dnešního „📁 Otevřít složku".
- `GET /api/stems` → seznam už rozseparovaných písní:
  `[{"name", "sourceRel", "stems": ["vocals","drums","bass","guitar",
  "piano","other"]}]`, skenuje `MUSIC_ROOT/Stems/*` (`Path.glob("*")`
  přeskakuje položky začínající tečkou, takže dočasná `.raw` staging
  složka se do výpisu sama nedostane, žádné explicitní filtrování
  navíc netřeba).
- `GET /api/stems/{name}/{stem}.wav` — servíruje jeden stopový soubor,
  přes stejný vlastní Range-aware streamovací helper jako
  `/api/library/stream` ve spec #1 (Starlette 0.27.0 v appce Range na
  `FileResponse` nativně nepodporuje — ověřeno při psaní spec #1).

`_safe_name()` (appka ji už má pro playlist složky) se použije i pro
odvození `<safe-name>` z názvu zdrojového souboru.

## 3. Frontend — tab „🎚️ Steems"

Čtvrtý tab vedle Playlisty/Písně/Knihovna. Nahoře seznam rozseparovaných
písní (`GET /api/stems`) — klik na položku načte její stopy do mixpultu
pod seznamem.

**Mixpult (adaptováno ze StemDeck):**
- Jeden sdílený `AudioContext`; každá stopa se dekóduje
  (`decodeAudioData`) do vlastního `AudioBuffer`, routovaná přes vlastní
  `GainNode` (fader) do společného výstupu — `AudioBufferSourceNode.start(when)`
  spuštěný pro všechny stopy se stejným `when` drží vzorkově přesnou
  synchronizaci (na rozdíl od více `<audio>` tagů, které se rozjíždí).
- Kanálový pruh na stopu: název, svislý fader (`<input type=range>` →
  `gainNode.gain`), Mute/Solo tlačítka, živý VU metr
  (`AnalyserNode.getByteFrequencyData`/`getByteTimeDomainData` na
  `requestAnimationFrame`), waveform nad/za faderem vykreslený jednou do
  `<canvas>` (min/max peaky ze zdekódovaného bufferu — stejný princip
  jako StemDeck).
- Sdílený transport: play/pause/seek/loop-region, posouvá všechny stopy
  zároveň (přepočet `when` a `offset` při seeku/loopu).
- „⬇️ Exportovat mix" — `OfflineAudioContext` stejné délky, aplikuje
  aktuální gain/mute na každou stopu, vyrenderuje do jednoho bufferu →
  zakóduje na WAV (ruční WAV writer nad `Float32Array`, žádná externí
  JS knihovna potřeba) → nabídne ke stažení přes `<a download>` blob
  link.

## Otevřené implementační detaily (řeší implementační plán)

- Přesné mapování, které konkrétní soubory/funkce ze StemDeck repa se
  portují 1:1 vs. přepisují na míru našemu stylu (uvidí se až při čtení
  jeho zdrojáků během plánování).
- Přesný CSS vzhled kanálových pruhů — navázat na existující barevnou
  paletu (`--accent`, `--card`, `--border` proměnné, co appka už používá),
  ne vymýšlet nový vizuální jazyk.
- Chování Mute/Solo kombinací (typické DAW pravidlo: aktivní solo na
  libovolné stopě ztlumí všechny ne-solo stopy bez ohledu na jejich
  vlastní mute).
