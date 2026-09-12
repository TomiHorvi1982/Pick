# Knihovna, konfigurovatelná cesta stahování a video v přehrávači

Datum: 2026-09-12
Stav: schváleno uživatelem, čeká na implementační plán

## Kontext

Spotifylisty (spouští se přes `~/Desktop/Spotifylisty.app`, zdrojáky v
`~/.local/share/spotdl-helper/app/`) je lokální FastAPI + vanilla-JS appka
pro procházení YT Music playlistů/písní a jejich stahování přes spotdl.
Cesta pro stažené soubory je dnes natvrdo `~/Music/spotdl` (`server.py:25`).
Appka už má:

- `Player` JS modul (`static/index.html` ~1220+) — sdílený `<audio>`,
  vlastní ovládací prvky, hlasitost a historii v `localStorage`, umí
  přehrát náhled skladby přes `/api/stream/{video_id}` (audio-only stream
  vyřešený přes yt-dlp v `_resolve_audio`/`_proxy_audio_response`).
- Playlist/frontu ke stažení (📃, `QUEUE_KEY` v `localStorage`).
- `/api/library` — vrací jen seznam stemů souborů (pro "✓ máš" značky),
  žádný skutečný průzkum struktury.
- `/api/reveal` — otevře složku ve Finderu přes `open`.
- Nastavení (`settings-overlay`) — zatím jen cookies-from-browser pro
  stahování, persistováno v `localStorage`.

## Cíl

1. Cestu pro stahování jde nastavit v appce (textové pole + nativní
   folder-picker), perzistentně napříč restarty serveru.
2. Nová sekce „Knihovna" — jedno-panelový prohlížeč struktury stažených
   souborů (ve stylu file manageru), navigace do složek, "Přehrát vše".
3. Přehrávání už stažených písní a celých playlist složek v existujícím
   přehrávači.
4. Tlačítko „🎬 Video" u právě hrané skladby (ať už jde o náhled, historii,
   nebo lokální soubor), které v appce přímo přehraje odpovídající
   YouTube video.

5. U přehrávané skladby je vidět, ze kterého playlistu pochází (pokud
   z nějakého pochází).

## Mimo scope (vědomě vynecháno, YAGNI)

- Přesun/migrace starých souborů při změně cesty stahování.
- Skutečný dvou-panelový (tree + list) Commander layout.
- Rekurzivní "přehrát vše" napříč podsložkami — jen soubory přímo ve
  zvolené složce (odpovídá tomu, jak spotdl dnes ukládá — `Playlists/<jméno>/`
  a `Songs/<interpret>/` jsou ploché).
- Čtení ID3/metadata tagů — název/interpret se parsují z názvu souboru
  (`{artist} - {title}.{output-ext}`, což přesně odpovídá šabloně, kterou
  appka sama používá při stahování playlistů/fronty — `start_download`,
  větve `kind == "playlist"`/`"queue"`).
- Auto-obnovení audia po zavření video panelu.
- Zvýraznění právě hrajícího řádku v Knihovně.

## 1. Konfigurovatelná cesta stahování

**Perzistence:** `~/.local/share/spotdl-helper/config.json`, `{"music_root": "<abs cesta>"}`.
Při startu serveru se načte (chybí-li soubor/klíč, použije se dnešní
default `~/Music/spotdl`). `MUSIC_ROOT` v `server.py` zůstává modulovou
proměnnou přepisovanou přes `global` — nic ji dnes nezachytává v closure,
takže všechny funkce, co ji čtou (`start_download`, `library`, nové browse/
stream endpointy), automaticky uvidí novou hodnotu.

**Endpointy:**
- `GET /api/settings` → `{"music_root": "<abs cesta>"}`.
- `POST /api/settings` `{"music_root": str}` → validace: `Path(...).expanduser()`
  musí být absolutní, buď existuje a je to zapisovatelný adresář, nebo ji
  appka rovnou vytvoří (`mkdir(parents=True)`); chyba → 400 s českou hláškou.
  Při úspěchu přepíše `MUSIC_ROOT`, zapíše `config.json`.
- `POST /api/settings/pick-folder` → spustí
  `osascript -e 'POSIX path of (choose folder)'` (subprocess, timeout
  ~60s kvůli čekání na uživatele); vrátí `{"path": "<abs cesta>"}`, nebo
  `{"path": null}` když uživatel dialog zavře (osascript vrátí nenulový
  exit kód — zachytit, nepovažovat za chybu).

**Frontend:** nová sekce v `settings-overlay` — text input `music-root-input`
+ tlačítko „📂 Vybrat složku" (volá pick-folder, jen vyplní input; cesta se
neukládá, dokud uživatel nedá „Uložit" — stejný vzorec jako dnešní cookies
nastavení). Otevření Nastavení prefillne input z `GET /api/settings`.
Tlačítko „Uložit" navíc k dnešnímu chování POSTne `/api/settings`.

## 2. Knihovna — prohlížeč struktury

**Nový tab** „📁 Knihovna" vedle Playlisty/Písně (`state.mode = "library"`,
stejný `setMode()` mechanismus).

**Backend:**
- `GET /api/library/browse?rel=<relativní cesta>` (`rel=""` = kořen
  `MUSIC_ROOT`). Bezpečnost: `(MUSIC_ROOT / rel).resolve()` musí začínat
  `MUSIC_ROOT.resolve()`, jinak 404. Vrátí
  `{"rel": rel, "breadcrumb": [{"name","rel"}...], "entries": [...]}`,
  kde `entries` jsou nejdřív složky (abecedně), pak audio soubory
  (abecedně), každá `{"name", "type": "dir"|"file", "rel", "size_bytes"?}`.
  Z názvu souboru (`Artist - Title.ext` / uvnitř `Songs/<Artist>/Title.ext`)
  se na frontendu odvodí `artist`/`title` pro zobrazení a pro frontu
  přehrávání — parsování `{artist} - {title}` patternu, s fallbackem na
  holý název souboru bez přípony, když pattern nesedí (např. `Songs/`
  podsložky, kde je interpret už v cestě).
- `GET /api/library/stream?rel=<rel cesta k souboru>` — stejná ochrana
  proti path-traversal jako browse; vrací obsah souboru s podporou Range
  (seekování v `<audio>`/`<video>`). **Oprava po ověření:** appka běží na
  Starlette 0.27.0, jehož `FileResponse` Range vůbec neřeší (ověřeno —
  žádná zmínka Range/Accept-Ranges/If-Range v jeho zdroji) — místo
  spoléhání na framework appka dostane vlastní malý Range-aware
  streamovací helper nad `StreamingResponse` (čte jen požadovaný byte
  rozsah ze souboru), ve stejném duchu jako už existující ruční
  Range-forwarding v `_proxy_audio_response`.

**Frontend:** breadcrumb řádek nahoře, pod ním seznam řádků (`.fs-row`):
📁 řádky navigují dovnitř (celý řádek klikatelný), 🎵 řádky mají ▶
(přehrát) a velikost souboru. Když složka obsahuje ≥1 skladbu, nad
seznamem je lišta „▶ Přehrát vše (N skladeb)".

## 3. Přehrávání lokálních souborů + fronta „Přehrát vše"

`Player` modul (IIFE) dostane:
- `playLocal(track)` — `track = {relPath, title, artists}`; nastaví
  `audio.src = /api/library/stream?rel=...`, meta z `title`/`artists`
  (bez artworku, defaultní 🎵 placeholder — appka dnes stejně zobrazuje
  placeholder, když `track.thumbnail` chybí), zapíše do historie se
  `source:"local"` a `relPath`, aby šlo přehrání zopakovat z historie i
  po zavření appky.
- Interní frontu `playQueueList` + `playQueueIdx` (čistě v paměti, NEpersistuje
  se — oddělená od 📃 stahovacího playlistu podle rozhodnutí z brainstormingu).
  „Přehrát vše" v Knihovně naplní tuto frontu ze souborů aktuální
  složky a zavolá `play(queueList[0])`. Poslech `audio.addEventListener("ended")`
  se rozšíří: pokud `playQueueIdx + 1 < playQueueList.length`, přehraje
  se další; jinak beze změny (zůstane na posledním, ikona na ▶). Jednotlivé
  přehrání mimo frontu (náhled, historie, 📃 fronta) frontu vyprázdní/nastaví
  na 1 prvek, aby `ended` nevedlo k nechtěnému přeskoku.

## 4. Video v přehrávači

**Backend:** refaktor `_resolve_audio`/`_proxy_audio_response` tak, aby
sdílely Range-proxy logiku s novým `_resolve_video` (yt-dlp
`format: "best[ext=mp4]/best"` — progresivní stream se zvukem i obrazem
v jednom souboru, žádné slučování na appce). Nový `GET /api/stream-video/{video_id}`
používá stejnou proxy funkci s `_resolve_video` místo `_resolve_audio`.
Cache stream-info (`_STREAM_CACHE`) rozšířená o druhý slovník nebo klíč
`(video_id, "video")`, stejné TTL.

**Frontend:** tlačítko „🎬 Video" v přehrávači, aktivní kdykoliv něco hraje
(remote i lokální). Klik rozbalí drawer (stejný vizuální vzor jako
historie/playlist šuplíky) s `<video>` prvkem:
- Má-li aktuální track `videoId` (náhledy, historie) → rovnou
  `/api/stream-video/{videoId}`.
- Lokální track bez `videoId` → nejdřív `GET /api/preview?q=<artist title>`
  (stejný trik jako dnešní náhled), výsledný `videoId` se dopíše na track
  objekt (cache pro příští klik na 🎬 u téhož tracku).
- Otevření pauzne `<audio>` a pustí `<video>` (ten má vlastní zvuk).
  Zavření videa ho jen zastaví; `<audio>` se samo neobnovuje — zůstává
  na pozici, kde bylo pozastaveno. (Potvrzeno uživatelem: bez
  auto-obnovení, ▶ musí dát znovu ručně.)

## 5. Původní playlist u přehrávané skladby

`Player.play(track)` dostane volitelné `track.sourcePlaylist` (jméno
playlistu) — nastaví ho volající kód všude, kde se skladba pouští z
otevřeného detailu playlistu (dnes to volající kód nepředává). Zobrazí se
jako malý, tlumeně zbarvený řádek pod interpretem v přehrávači; když
`sourcePlaylist` chybí (náhled písně mimo playlist, lokální knihovna bez
kontextu playlistu), řádek se prostě nevykreslí.

## Otevřené implementační detaily (řeší až implementační plán, ne spec)

- Přesný CSS pro `.fs-row` / drawer s videem — navázat na existující
  `.drawer`/`.cart-item` třídy, ne vymýšlet nový vizuální jazyk.
- Kde přesně v `server.py` refaktorovat `_resolve_audio` (nová sdílená
  helper funkce s parametrem `format`, nebo dvě tenké wrapper funkce nad
  jedním jádrem) — implementační detail, ne architektonické rozhodnutí.
