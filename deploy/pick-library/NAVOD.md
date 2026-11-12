# Pick knihovna na PC — návod

Stránka pro kamaráda **mezi jamy** běží na tvém PC v Dockeru, ne na Macu. Kamarád v ní prochází tvoje písně, poslouchá je, čte text a akordy, zkouší otisky a NAM a dává písním ★ **Na příště**. Samotný jam (kytary, kamery, společné hraní) zůstává na Macu a běží jen tehdy, když ho zapneš.

## Jak je to zabezpečené
- Kontejner čte kopii karty a dat Picku **jen pro čtení**: nic nesmaže ani nezmění. Zapisuje jen do vlastního úložiště (`library-state`), kde jsou kamarádova jména se zašifrovanými hesly, Na příště a náhledy písní.
- Kontejner **nemá žádný port** do sítě ani do internetu a je v síti bez přístupu ven. Ven vede jen **Cloudflare Tunnel** ve druhém kontejneru.
- Běží jako omezený uživatel, bez práv, s pevným (jen pro čtení) systémem souborů a omezenou pamětí.
- Bez přihlášení se nic nezobrazí. Po 8 špatných heslech musí člověk 10 minut počkat.
- **Mac se knihovny jen ptá:** jednou za minutu si vymění Na příště a řekne jí, jestli hraješ. Knihovna se k Macu nedostane, na Macu se nic neotevírá.

## Data na PC
| Co | Cesta na PC |
|---|---|
| Kopie karty (složka se `Stems`) | `F:\DITORIG-KNIHOVNA\PICKSD\spotdl` |
| NAM profily a kabinety | `F:\DITORIG-KNIHOVNA\PICK-SUPPORT\amps` |
| Data Picku (texty, playlisty) | `F:\DITORIG-KNIHOVNA\PICK-SUPPORT` |

V Dockeru (Docker Desktop) jsou tyhle složky vidět jako `/run/desktop/mnt/host/f/DITORIG-KNIHOVNA/...`. Tak je to nastavené ve výchozím stavu. Pokud Docker běží přímo v Ubuntu (WSL), použij místo toho `/mnt/f/DITORIG-KNIHOVNA/...`.

## 1. Cloudflare Tunnel (jednou, asi 3 minuty)
1. Otevři **dash.cloudflare.com** → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** → **Cloudflared**.
2. Pojmenuj ho `pick-knihovna` → **Save**. Zobrazí se příkaz s dlouhým **tokenem** (text za `--token`). Zkopíruj **jen ten token**, příkaz nespouštěj.
3. Pokračuj na **Public Hostname**:
   - Subdomain: `jam`
   - Domain: `neverlast.cz`
   - Service: **HTTP**, `library:8080`

   Dej **Save**. Kamarád pak bude chodit na **https://jam.neverlast.cz**.

## 2. Klíč pro Mac
V Picku na Macu: **Nastavení → Jam → Knihovna na PC** → **Nový klíč**. Klíč se zkopíruje. Adresu `https://jam.neverlast.cz` vyplň do pole nad ním a zatím **neukládej**. Klíč použiješ v dalším kroku.

## 3. Spuštění v Dockeru
Knihovna běží v **Docker Desktopu** jako projekt `pick-knihovna`, ne v Coolify. Na tvém PC je ve složce `C:\Users\Tomas\pick-knihovna`.
1. Spusť **Docker Desktop**.
2. Stáhni repozitář **TomiHorvi1982/Pick** do `C:\Users\Tomas\pick-knihovna` (`git clone`).
3. Do `deploy\pick-library` dej soubor `.env` s proměnnými:
   - `PICK_SYNC_TOKEN` = klíč z kroku 2
   - `CF_TUNNEL_TOKEN` = token z kroku 1
   - `PICK_HOST_NAME` = `Tomi` (nebo jak chceš, aby ti kamarád v knihovně říkal)
   - Jen když je Docker v Ubuntu (WSL): `PICK_DATA_DIR`, `PICK_AMPS_DIR` a `PICK_HELPER_DIR` s cestami `/mnt/f/...` z tabulky výše.
   - Volitelně `PICK_CACHE_MAX_GB` (kolik smí zabrat převedené písně, výchozí 65).
4. Ve složce `deploy\pick-library` spusť `docker compose -p pick-knihovna up -d --build`.

**Aktualizace na novou verzi:** ve složce `C:\Users\Tomas\pick-knihovna` spusť `git pull --ff-only`, pak v `deploy\pick-library` znovu `docker compose -p pick-knihovna up -d --build`. Soubor `.env` i úložiště `library-state` (účty, Na příště, převedené písně) zůstanou. Klidně o to požádej Clauda na PC.

## 4. Účet pro kamaráda
V Docker Desktopu u kontejneru **library** → **Exec** (nebo `docker exec -it <kontejner> sh`):
```
python library.py adduser Pavel
```
Zeptá se na heslo (aspoň 6 znaků). Seznam účtů zobrazí `python library.py users`, odebrání `python library.py deluser Pavel`.

## 5. Propojit s Macem
V Picku: **Nastavení → Jam → Knihovna na PC** → **Uložit** (adresa a klíč) → **Vyzkoušet spojení**. Má se ukázat „Spojeno ✓“.

## Jak to funguje pro kamaráda
- **https://jam.neverlast.cz:** přihlásí se a uvidí písně (★ Na příště, Vše, Kapely, Ladění, Playlisty), náhledy, text, akordy, otisky a NAM.
- **Když zapneš jam**, knihovna mu do minuty ukáže „**Tomi právě hraje — Připojit se k jamu**“ a odkaz na neverlast.cz.
- **Na příště** je i u tebe v Picku jako playlist ve Steems „**Jam — na příště**“. Co přidá kamarád nebo ty, objeví se na obou místech.

## Co je dobré vědět
- Knihovna ukazuje to, co je na **kopii** karty. Nové písně z Macu se v ní objeví, až kopii aktualizuješ.
- **Obaly písní** se ukážou jen u těch, které Pick už někdy dohledal (`chip-thumbs.json` v datech Picku). Jinak se místo nich zobrazí ikona.
- **Vlastní otisk** si kamarád nahraje jen během jamu, protože se počítá na Macu.
- Disk F: má málo volného místa (13 GB). Náhledy se proto ukládají do úložiště Dockeru na disku C:.
- **Písně se připravují předem.** Po startu knihovna na pozadí postupně převede všechny stopy a náhledy (nejdřív ★ Na příště), aby kamarád na nic nečekal. Napoprvé to trvá asi 8–10 hodin a zabere kolem 55 GB na disku C:. Zabere nejvýš 65 GB (jde změnit proměnnou `PICK_CACHE_MAX_GB` v `.env`) a přestane i tehdy, když na disku Dockeru zbývá méně než 15 GB. Zbytek pak převede, až si o píseň kamarád řekne. Nové písně z kopie karty převede do půl hodiny. Kolik je hotovo, uvidíš v Picku v **Nastavení → Jam → Knihovna na PC → Vyzkoušet spojení**.

## DJ na PC

Po přihlášení je v hlavičce stránky tlačítko **DJ**: dva decky nad písněmi, které už mají stopy
(stejný DJ jako v Picku na Macu: 6 stop, SYNC, key lock, smyčky, cue, mixer, FX, nahrávání).

- Stopy jdou z kopie karty jako Opus (`/g/stem`), beat grid z `beats.json`, který na kartu zapsal Pick.
  Píseň bez `beats.json` hraje taky, jen bez SYNC.
- Písně **bez stop** tu DJ nenabízí: v kontejneru není Demucs a Docker na Windows obvykle nemá přístup ke GPU.
  Rozděl je v Picku na Macu (nebo je tam pusť v DJ) a po další synchronizaci karty jsou tu.
- Stopy připravené dopředu (Opus v `library-state`) se načtou hned; ostatní se převádějí při prvním načtení
  (6 stop ≈ desítky sekund).
- Zvuk běží v prohlížeči: paměť zabírá počítač, na kterém je stránka otevřená (~6 MB na minutu a stopu,
  šestiminutová píseň ≈ 380 MB na deck), ne kontejner.

## Aplikace na plochu pro kamaráda (Windows)
V Picku: **Nastavení → Jam → Knihovna na PC → Pick Jam.cmd** (nebo přímo `https://jam.neverlast.cz/pick-jam.cmd`). Soubor mu pošli. Když ho spustí, objeví se mu na ploše ikona **Pick Jam** a knihovna se otevře v Chromu (nebo v Edge) jako aplikace ve vlastním okně. V souboru je jen adresa, žádné heslo ani klíč.
- Přihlásí se jménem a heslem. Se zaškrtnutým **Zapamatovat si mě** zůstane přihlášený rok, i když se knihovna restartuje.
- **Zapnout zvuk a hrát:** vybere píseň, stopy se načtou do jeho mixu a hraje s nimi. Má ▶, posun, tempo, tóninu, otisky, NAM, gate, EQ a ladičku, všechno bez tebe.
- **Když zapneš jam,** do 15 sekund mu přijde upozornění Windows „Tomi je online a hraje“ a nahoře se rozsvítí **Připojit se k jamu**.
- Windows se může zeptat „Systém Windows ochránil váš počítač“. Stačí kliknout na **Další informace → Přesto spustit**.
- Místo souboru může v Chromu použít i ikonu instalace v adresním řádku nebo tlačítko **⤓ Na plochu** v aplikaci.

## Aplikace na plochu pro kamaráda (Mac)
V Picku: **Nastavení → Jam → Knihovna na PC → Pick Jam pro Mac.zip** (nebo `https://jam.neverlast.cz/pick-jam-mac.zip`). Kamarád ZIP rozbalí a na **Pick Jam.command** klikne pravým tlačítkem → **Otevřít**. Mac se poprvé zeptá, protože skript nepochází z App Storu. Na ploše pak vznikne aplikace **Pick Jam** s ikonou, která otevře knihovnu v Chromu (nebo v Edge či Brave) jako samostatné okno. Jméno a heslo se zadá v okně, se zaškrtnutým „Zapamatovat si mě“.
