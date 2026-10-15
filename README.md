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

Tónový otisk (`tone_worker.py`) má **vlastní malý** venv bez torche a NAM
pouští přes Node (`/opt/homebrew/bin/node`) stejným WebAssembly enginem, jaký
hraje živá kytara (`tone_nam.mjs` načte `static/nam/nam-worklet.js`):

```bash
uv venv --python 3.12 ~/.local/venvs/pick-tone
uv pip install --python ~/.local/venvs/pick-tone/bin/python numpy scipy soundfile
```

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

„pick page down / up“ listuje vždy stránkou (i když je nabídka povelů otevřená);
nabídka má svoje „pick help down / up“. „pick sort by band / album / title“
seřadí seznam, který je vidět (hlavičky tabulek, skupiny karet ve Steems,
seznam minipřehrávače). „pick open song <název>“ i „pick play <název>“ otevřou
píseň toho jména ze sekce, která je vidět — řádek seznamu Steems jde rovnou do
mixu; když tam není, hledá se ve Steems a pak v knihovně. Číslem stejně: „pick
play seven“, „pick open song seven“ (tabulky mají sloupec #, karty Steems číslo
vlevo).

Pod otevřeným mixem ve Steems je seznam ostatních písní — číslo, název, kapela,
ladění, délka. Klik (nebo „pick play seven“, „pick play <název>“) pustí píseň
celou jako ukázku v přehrávači dole, „pick skip“ / „pick skip back“ skočí o 10 s,
„pick next song“ pustí další v pořadí a „pick practice this song“ (nebo tlačítko
Mix na řádku) ji otevře do mixu. Řadí se hlavičkou nebo „pick sort by band /
title / tuning“. Ladění se bere z tabu písně (`tuning.json` u stop): zapíše se
při otevření tabu, při uložení písně, a „Zjistit ladění“ ho dohledá u ostatních.
„pick hide panel“ / „pick show panel“ zúží a vrátí levý panel.

Co se uloží do oblíbených, připraví server na cvičení sám: hvězdička u písně
(aplikace i minipřehrávač, „pick save song“) ji stáhne, rozdělí na stopy, najde
tab a text; hvězdička u playlistu totéž pro každou jeho píseň, po jedné
(`/api/practice/save-many`). Tlačítko „Připravit oblíbené na cvičení“ ve Steems
spočítá, co z oblíbených songů a playlistů ještě chybí, zeptá se a doplní to;
stejným tlačítkem se dá zastavit. Nalezený text se ukládá na disk
(`lyrics/` vedle nastavení), takže je příště hned a bez internetu.

Co už z písně je, ukazují čtyři značky **MP3 · STOPY · TAB · TEXT** (zelená = je,
šedá = chybí, čárkovaná = zatím se neví): v detailu playlistu u každé písně, v
knihovně jen TAB a TEXT (stopy tam mají svou značku). Ptá se na to
`/api/practice/have` — nic nehledá na internetu, říká jen, co už je na disku.
V playlistu „označit jen chybějící“ zaškrtne písně bez stop a „Připravit vybrané
na cvičení“ je stáhne, rozdělí a dohledá tab a text; v knihovně totéž „Označit,
co chybí“ a „Připravit na cvičení“ v liště výběru. Hotové se přeskakují; u
rozdělených bez tabu nebo textu se jen znovu hledá tab a text
(`save-many` s `finish`).

**Píseň se ladí podle kytary.** Ladění vybrané v ladičce (nebo „Tohle je moje
ladění“, „Zjistit z kytary“) je ladění hráčovy kytary. Každá píseň otevřená ve
Steems, jejíž ladění je známé (z tabu nebo `tuning.json`), se o rozdíl posune
sama: kytary, basa a melodické stopy, ne bicí, zpěv a vlastní nahrávky. Posouvá
se jen výška (stejný stretch jako u tóniny), čas, takty a značky zůstávají.
„Originál“ vrátí píseň do původní výšky (pamatuje se u písně), další klik ji
zase přeladí. Kde ladění známé není, nic se neposune; „Moje ladění“ ho nechá
vybrat ručně a uloží k písni. Odhad ladění ze zvuku basy se zkoušel a na
známých písních se trefil v 6 z 11 — proto se nepoužívá.

**Oficiální názvy** (`names.py`). Rozdělená píseň se jmenuje po souboru, často
po videu („BeginNewLife — Slayer- Raining Blood (lyrics)“), a s takovým názvem
se nenajde text ani tab. Každá píseň se proto jednou opraví: zeptáme se
hudebního katalogu (YouTube Music) a jeho odpovědi věříme jen tehdy, když v ní
je interpret i název, které jsme už měli; pak se vezme oficiální interpret,
název a album. Jinak se název aspoň očistí — pryč jde cokoli v závorkách, „official
video“, „with lyrics“, „HD“…, nahrávač se oddělí od kapely, jména dostanou velká
písmena. Česká jména, která jsme měli s háčky, se nevymění za verzi bez nich.
Výsledek je v `meta.json` u stop (`checked`, `official`, původní údaje ve `was`);
složka se nepřejmenovává a ručně psaná jména se nemění. Nově rozdělená píseň se
opraví sama a dohledá se jí tab a text; „Opravit názvy“ pod mixem projde ty
zbylé (`/api/stems-names`, `DELETE` vrátí původní).

Koncertní nahrávka si za názvem nechává „(Live)“ — jediná závorka, která
zůstává — a nepovažuje se za duplicitu studiové verze. Kde první katalog nic
neví, zeptáme se druhého: MusicBrainz (pomalu, jeden dotaz za vteřinu).

**Ladění ze dvou míst.** Nejdřív tab na Songsterru; kde není, zkusí se Ultimate
Guitar — stránka tabu tam ladění jmenuje („D A D G B E“), i u spousty českých
písní. Kytarový tab má přednost před akordy (ty často píšou jen „Standard“),
každé místo se u písně ptá jednou (`asked` v `tuning.json`, `from: "ug"`).

**Vlastní hlas Picku** (`tts_worker.py`). Klonovaný hlas už nepotřebuje žádný
jiný program: mluví model OmniVoice (k2-fsa, Apache-2.0, přes 600 jazyků včetně
češtiny) ve vlastním prostředí `~/.local/venvs/pick-tts`, který server spouští
jako pomocný proces a po deseti minutách nečinnosti ho nechá skončit. Hlas je
složka `voices/<id>/` vedle nastavení: `ref.wav` (pár vteřin řeči, ustřižených
v pauze), `ref.txt` (co se tam říká) a `name.txt`. 16 kroků = asi 8 s na řádek
na M1 Pro. Hlasy, které jsou jen ve Voiceboxu, jdou dál přes něj.

Nový hlas přidá „+ hlas“ ve Výslovnosti: soubor nebo 10 s z mikrofonu
(`POST /api/tts/voice`), rozpoznaný text ukázky jde opravit (`PUT`), hlas smazat
(`DELETE`).

**Vzhled.** Výchozí barvy jsou noční modrá a fialová (dokud nehraje nic, co by
dalo barvy z obalu); co je „hotovo / mám“, zůstává zelené. Zelená natvrdo
psaná ve stylech jde přes `--accent`. Dole v levém panelu jsou dvě elektrické kytary.
Ukazatel je vidět pořád (`--horns-rest`) a nad vším, na co jde kliknout, se
rozsvítí (`--horns`) — pravidla s `cursor:pointer` a `default` se při startu
přepíšou na tyhle dvě proměnné, takže se žádné nepíše ručně. V Nastavení →
Ukazatel jde vybrat jeden z deseti obrázků (rohy, trsátko, kytara, lebka, blesk,
paličky, plamen, meč, netopýr, hvězda), pět barev a velikost.

**Úpravy vzhledu** (Nastavení → Vzhled → „Upravit vzhled podrobně…“,
`static/look.js`, server `look.py`): režim, ve kterém klikneš na cokoli v
aplikaci a změníš text, pozadí (barva, přechod, obrázek, vzorek), okraj, stín,
mezery, efekty, polohu, ikonu před textem nebo napíšeš vlastní CSS — pro jeden
prvek, všechny stejné, i jen pro stav „při najetí“. Karta Aplikace mění barvy
celé aplikace (hotové palety, každá barevná proměnná zvlášť), písmo, velikost
a pozadí. Náhled je okamžitý; natrvalo až tlačítkem Uložit (`look.json` vedle
nastavení, obrázky ve složce `look/`), je tu zpět/znovu, „Původní ↔ nový“,
pojmenované vzhledy a soubor ke stažení. Vzhled je jen seznam selektorů s
úpravami, skládaný do jednoho `<style id="look-style">` — stylů aplikace se
nedotkne a „Výchozí vzhled“ je prázdný seznam. Zdroje zdarma a bez klíče:
Google Fonts a Fontshare (písma), Openverse, Wikimedia Commons, Lorem Picsum,
Art Institute of Chicago a NASA (obrázky), Iconify (ikony); vybraný obrázek se
stáhne do Picku. Karta Vzor položí vlastní obrázek průsvitně přes aplikaci
jako předlohu a nabere z něj barvy (i celou paletu).

**Zpěv mým hlasem** (Steems → Panely → „Zpěv mým hlasem…“): stopa zpěvu se
přezpívá hlasem, který sis přidal u Výslovnosti — melodie, rytmus i frázování
zůstanou z originálu, změní se jen barva hlasu. Dělá to model Seed-VC (44 kHz,
pro zpěv) ve vlastním prostředí `~/.local/venvs/pick-svc`, kód v
`~/.local/share/spotdl-helper/svc/seed-vc`, váhy asi 2,3 GB; server ho nikdy
nenačítá, pouští `svc_worker.py` jako proces na jednu práci. Na M1 Pro trvá
zhruba třikrát déle, než je délka zpěvu (čtyřminutová píseň kolem 12 minut),
běží na pozadí s ukazatelem průběhu a jde zastavit. Výsledek je další stopa
písně „Můj hlas (…)“ a originální zpěv se ztlumí. Volí se kvalita, výška (třeba
o oktávu níž) a se zapnutou smyčkou jde přezpívat jen ten úsek — vloží se do
hotové stopy. Věrnější podobu dá vzorek vlastního zpěvu (10–25 s bez hudby),
který se nahraje v tom samém okně.

**Můj zpěv téhle písně** (v okně Zpěv mým hlasem → „Zazpívat úryvek téhle
písně“): Pick vybere kus písně — smyčku, refrén, nebo místo, kde je nejvíc
textu; jde posunout a zkrátit — pustí ho, na obrazovce běží jeho text (řádek,
který se právě zpívá, svítí; text bez časů je vidět celý) a mikrofon nahrává.
Originální zpěv jde při nahrávání nechat hrát nebo vypnout. Nahrávku si
přehraješ a teprve po odsouhlasení se uloží k písni (`myvoice-sample.wav`
vedle stop, nejvýš 25 s, pauzy pryč, vyrovnaná hlasitost). Od té chvíle se
tahle píseň zpívá podle ní — je to tvůj hlas přímo v jejích tónech, a proto
věrnější vzor než obecný vzorek. Každá píseň může mít svůj. Chce to sluchátka.

**Nový hlas s průvodcem** (u Výslovnosti „+ hlas“ → Průvodce, nebo v okně
Zpěv mým hlasem → „＋ Nový hlas“): přečteš nahlas český a anglický text a —
nepovinně, ale pro zpěv to pomůže nejvíc — kousek zazpíváš. Každou nahrávku si
přehraješ a můžeš ji říct znovu; okno hlídá, jestli není moc potichu nebo
přebuzená. Z českého čtení vznikne ukázka pro čtení (`ref.wav`, `ref.txt`), ze
zpěvu a obou čtení 25 vteřin pro zpěv (`sing.wav`) — pauzy pryč, vyrovnaná
hlasitost. Je to okamžitá kopie hlasu ze vzorku, ne dlouhé učení modelu.

**Skladatel — song z nápadu** (Tools → Skladatel, `static/studio.js`, `studio.py`,
`studio_words.py`, `lyricist.py`): stránka v levém panelu pod Tools. Nahraješ nebo
rovnou zahraješ kousek kytary (10–40 s) — nebo nic — a popíšeš zvuk: hotové styly
(16 rockových a metalových), vlastní popis anglicky, slova, kterým model rozumí
(žánr, kytary, bicí, další nástroje, zpěv, nálada, zvuk a produkce, éra, tempo,
stavba songu — klik je přidá do popisu), a nepovinně zvukovou předlohu (nahrávka,
jejíž barvou a mixem se má řídit, s posuvníkem síly). Pick dohraje celý song: tvůj
nápad zůstane na začátku tak, jak jsi ho zahrál, a kapela na něj naváže ve stejném
tempu a tónině. S textem se song zpívá; text má štítky částí a hlasu na klik a rady,
jak psát, aby ho model dobře zazpíval.

*Textař* napíše celý text z pár slov („o čem“, slova, která tam musí být, styl
textu, rýmy), dopíše tvoje rozepsané verše, nebo je upraví — vždy v podobě, kterou
model zpívá nejlépe (části v [závorkách], 6–10 slabik na řádek, jeden obraz, refrén
se vrací) — a navrhne k němu popis zvuku. Píše Claude (`lyrics_worker.py` v prostředí
asistenta, model `claude-opus-5-5`); bez klíče nebo kreditu model v počítači
(Gemma), česky slabší — aplikace řekne proč.

*Model a nastavení* (vše, co ACE-Step 1.5 před spuštěním umí): Rychlý (turbo,
8 kroků), Podrobný (SFT, 50 kroků, víc detailu, asi třikrát pomalejší, stažený zvlášť
4,8 GB) a Základní (base); *Přemýšlení* — plánovač 1,7B si song nejdřív rozvrhne
(jen u songu bez nápadu); kroky, síla popisu, shift, ADG, interval vedení, postup
ODE/SDE, tempo, tónina, takt, seed (u hotové verze se dá vzít zpět do nastavení).
Velké modely XL mají 20 GB vah a do 16 GB paměti se nevejdou.

Skládá otevřený model ACE-Step 1.5 ve vlastním prostředí
(`~/.local/share/spotdl-helper/studio/ACE-Step-1.5`), který Pick spouští jako malý
server jen na dobu práce a po třech minutách nečinnosti ho uklidí. Skládá se na
serveru: stránku můžeš opustit a v horní liště vedle šipek je proužek s tím, co se
dělá, procentem (hlásí ho přímo model) a zbývajícím časem; klik vede zpět do
Skladatele. Každá hotová verze se — jakmile se nic neskládá — rozdělí na nástroje
(Demucs, jako ve Steems) a v kartě verze je přehrávač: vlna v barvách nástrojů,
klik do ní posune, hlasitost, a každý nástroj jde vypnout (dvojklik = jen ten).
Druhý režim, **kapela pod kytarou od začátku**: model base přidá k nápadu bicí a
pak basu — každý nástroj zvlášť — Pick je smíchá s tvou kytarou a zbytek songu pak
dohraje vybraný model. Vybraná varianta („Tuhle chci — do Steems“) se uloží do
knihovny (Songs) a její stopy rovnou do Steems, bez druhého rozdělování; „…a zazpívat
mým hlasem“ pak její zpěv přezpívá tvým hlasem.

**Playlisty Steems** (`static/practice.js`, `setlists.py`): v levém panelu pod Steems
si složíš playlist písní ze Steems (＋ Nový playlist, ＋ Přidat písně, ↑ ↓ pořadí, ✕
odebrat, přejmenovat, smazat; uloženo v `steems-playlists.json` vedle nastavení). ▶ ho
spustí: píseň se načte do mixu se vším, co sis u ní nastavil (fadery, mute, ladění, smyčka
— to se ukládá u každé písně samo), začne hrát, a když dohraje, načte se a spustí další.
Hlasem „pick practice playlist <název>“. Panel je vidět i při otevřeném mixu. V okně
＋ Přidat písně zůstává hledání: klikáš na jednu píseň za druhou, Hotovo okno zavře.

Pod mixem jsou v seznamu dalších písní nahoře ty od kapely otevřené písně. Tab je po
načtení každé písně **zamčený** (tažení ani kolečko ho neposunou); odemčený ručně zůstane
jen do další písně. Zbalený levý panel ukazuje jen ikony sekcí.

**Zpěvník ve Skladateli** (`songbook.py`, Skladatel → Zpěvník): rozbor písní ze Steems jako
předloha pro nové. Ke každé písni (`Stems/<píseň>/songbook.json`) zvuk slovy, kterým ACE-Step
rozumí — žánry kapely (MusicBrainz), tempo, tónina, ladění, tón kytar, jak hrají bicí, energie —
pro celou píseň i každou sekci zvlášť; a **stavba textu** po sekcích: kolik řádků, slabik, jaké
rýmy, co se opakuje. Samotná slova cizích písní se do Zpěvníku neukládají — text zůstává
v karaoke liště, textař píše do změřené stavby vlastní nová slova. **Moje texty**
(`songbook-mine.json`): tvoje texty celé, po kapelách a částech. Ze sekcí různých písní
i svých textů složíš **Stavbu nové písně** (↑ ↓ ✕): z ní „Popis zvuku ze stavby“ (s tempem
a tóninou), „Napsat text do stavby“, nebo „Jen značky do Textu“. „Rozebrat 100 písní“ bere
po jedné písni z každé kapely; běží na pozadí (sekce přes analyze.py, kde ještě nejsou,
asi půl minuty na píseň) a čeká, když běží rozdělování. Co zbývá, se po každé písni zapíše
(`songbook-queue.json`): ⏸ pozastavit dodělá rozebíranou píseň a počká i přes vypnutí Picku,
▶ Pokračovat naváže; běh přerušený vypnutím nebo výpadkem proudu se po startu rozběhne sám.
Dalších 100, nebo celou knihovnu (všechny nerozebrané).

**Mapa aplikace** (Nastavení → Mapa aplikace, `/mapa`): mapa všech funkcí přímo v Picku, i bez
internetu. `static/mapa.html` je jejím zdrojem — z něj se zveřejňuje i online verze — a po každé
nové funkci se aktualizuje.

**Text odposlechnutý ze zpěvu** (`hear.py`): když k písni ve Steems není text s časy, Pick si ho
naposlouchá ze stopy zpěvu — sám po otevření písně, nebo v liště Text → ⋯ → Odposlechnout ze
zpěvu (i v okně Jiný text). Zpěv se rozdělí na úseky, kde se zpívá, whisper (`whisper-cli`,
large-v3-turbo, na tomhle počítači) je přepíše najednou; jazyk písně je ten, ve kterém zazněla
většina úseků, a úseky slyšené jinak se přečtou znovu v něm. Asi minuta na píseň; uloží se
k stopám (`heard.json`) i jako nalezený text. Text bez časů (LRCLib ho často má) dostane časy
z odposlechu a slova zůstanou správná. Chybně slyšený řádek opravíš dvojklikem na stopě Text
→ Jen opravit text (`/api/lyrics/keep`).

**Přezpívat řádek** (`resing_worker.py`, dvojklik na řádek na stopě Text, nebo ⋯ → Upravit /
přezpívat řádek): nová slova zazpívá hlas zpěváka v melodii řádku. Slova přečte OmniVoice
hlasem zpěváka (ukázka z jeho stopy, přepsaná whisperem), nebo je namluvíš sám (🎤);
vokodér WORLD (`pyworld` v prostředí pick-svc) položí každou slabiku na tón řádku a dá jí
jeho výšku, Seed-VC to zazpívá barvou zpěváka (ukázka ~16 s jeho nejsilnějšího zpěvu).
Každý přezpívaný řádek jde do jedné stopy „Upravený zpěv“ (`resing.json`: co bylo a co je),
originál zůstane a ztlumí se. Asi minuta na řádek. Výslovnost bývá místy rozmazaná — zkus
znovu, nebo slova namluv.
Nad stopou „Upravený zpěv“ je pruh **Nový text**: u každého přezpívaného řádku nová slova
a pod nimi přeškrtnutá původní (původní text zůstává i na stopě Text pod zpěvem). Dvojklik
na blok: **Jen opravit zápis** (`POST /api/stems/{name}/resing/edit`, zvuk zůstane),
**Přezpívat znovu** s jinými slovy, nebo **Vrátit původní** (`DELETE …/resing/edit?start=`:
na to místo se vrátí původní zpěv; po posledním řádku zmizí i stopa).

**Výkon počítače** (`sysload.py`, Nastavení → Výkon počítače, `/api/system`): paměť, procesor,
kolik bere Pick sám, tlak na paměť a odkládání na disk (macOS: sysctl, vm_stat, ps), měřené
každé 3 s; v horní liště se ukáže upozornění, když je počítač vytížený nebo dochází paměť.
Náročná úloha se zeptá, jestli je místo: rozdělení písně ve frontě a nový model Skladatele
počkají (nejvýš 5 minut), odposlech, zpěv jiným hlasem a přezpívání řeknou proč ne.

**Úlohy a procesy** (`tasks.py`, Nastavení → Systém, `GET /api/tasks?sort=cpu|mem`): co Pick
právě dělá a co má ve frontě, od nejvytíženějšího — rozdělení na stopy (běžící a fronta),
ukládání písní, rozbor Zpěvníku, odposlech, tónový otisk, přezpívání, zpěv jiným hlasem,
Skladatel a jeho model, hlas OmniVoice, noční plán, další procesy, které server spustil,
server a okno Picku. Procesor se měří z času, který proces spotřeboval od minulého pohledu
(ps, 100 % = jedno jádro; v okně přepočteno na celý počítač), paměť je RSS; každý proces se
počítá jen u jedné úlohy. `POST /api/tasks/{id}/pause|resume|stop`: fronty rozdělování a
ukládání se pozastaví příznakem (rozdělaná píseň se dodělá, další nezačne; Ukončit zruší
čekající), Zpěvník svým vlastním pozastavením, běžící procesy (Demucs, whisper, otisk,
noční plán) signálem SIGSTOP/SIGCONT (pozastavené drží paměť), ukončení je SIGTERM
(a SIGKILL, když nepomůže); přezpívání, zpěv jiným hlasem a otisk mají vlastní zastavení,
odposlech nové `DELETE /api/hear/job`. Ukončit chce v okně dvě kliknutí.

**MIDI pedál** (Nastavení → MIDI pedál): nožní spínač přes MIDI — Bluetooth Ampero Switch
se v macOS připojí v Nastavení Audio MIDI (Okno → MIDI studio → Bluetooth → Připojit),
pak ho Chrome vidí jako MIDI vstup. Funkci (přehrát/zastavit, další / předchozí sekce,
originální kytary vyp/zap, smyčka, další píseň playlistu, ±10 s, tempo, metronom,
nahrávání, posun smyčky) přiřadíš ke **šlápnutí**, **dvojkliku** (dvě šlápnutí do 0,3 s)
nebo **podržení** (déle než 0,6 s): vybereš ji, dáš Naučit a šlápneš (MIDI learn — Pick
pozná CC, notu i Program Change, a jestli spínač hlásí i uvolnění; bez něj podržení nejde).
Uloženo v prohlížeči (`pick-pedal`). Pick MIDI poslouchá hned, když je „Pedál ovládá Pick“
zapnuté, a vidí i pedál, který se připojí později. macOS Bluetooth MIDI pedál, který už jednou
znal (BOSS FS-1-WL, Ampero Control…), po uspání nebo restartu sám znovu nepřipojí: **Připojit
MIDI / Obnovit** ho o to požádá (`POST /api/midi/bluetooth` → `tools/midi_bt.swift`,
`MIDIBluetoothDriverActivateAllConnections`, sestaví se jednou přes `swiftc` do
`~/.local/share/spotdl-helper/bin/midi-bt`) a nastavení ukazuje, které Bluetooth MIDI
zařízení macOS zná a jestli je připojené (`GET /api/midi/system`). Pedál spojený s jiným
zařízením (telefon) nevysílá a Mac ho nevidí — tam ho odpoj.

**Okna Picku místo okének prohlížeče** (`Dialog` v `index.html`): potvrzení, zadání názvu
i výběr ze seznamu se otevírají uprostřed v barvách aplikace (Enter / Esc), ne jako šedé
„127.0.0.1 říká…“ nahoře; i z Analyzéru a Loop Studia.

**Text, který se nenašel**: když k písni není text s časy, Pick nabídne písně téže kapely,
ke kterým text je (LRCLib, s časy nahoře, i s hledáním jiného názvu); vybraný se stáhne,
běží v liště i ve stopě Text a uloží se k písni. Totéž dělá „⋯ → Jiný text…“. Uložený text
bez časů se při dalším otevření zkusí najít znovu s časy (i pro délku o vteřinu dvě jinou).

**Ručně přidané stopy** (`stems_import.py`): písně rozdělené jinde (Moises…) stačí
nakopírovat do složky `Stems` — jako složku kapely se složkami písní (`Sepultura/01
Arise/vocals.wav, lead_guitar.wav…`), jako jednu složku se soubory „01 Bass - Název.mp3“,
nebo album / píseň / „Název-Bass-B minor-160bpm-440hz.mp3“. Při dalším načtení Steems je
Pick najde, každou píseň dá do vlastní složky „Kapela - Název“ a soubory do ní jen
*propojí* (hard link — žádná kopie navíc, tvoje složky zůstanou, jak jsou). Co který soubor
je, pozná podle slov v názvu: zpěv, bicí, basa, kytara, hlavní kytara (`guitar_solo`),
doprovodná (`guitar_rhythm`), klávesy, ostatní; celý mix, metronom a podklad vynechá.
Píseň, kterou Pick už rozdělil sám, přibude vedle s „(Moises)“. Co bylo jednou převzato,
je v `Stems/.imported.json` — smazaná se nevrátí.
Album si takové písně dohledají samy: diskografie kapely se projde jednou a píseň se najde
podle názvu na jejím studiovém albu (koncerty a „Remaster“ v názvu se vynechají; co se
nenajde, nehledá se znovu — album jde dopsat tužkou). Ve sloupci Playlist mají „Ručně
přidáno“ a u alb kapely vedle mixu se počítají jako „máš“.

Ve **Steems** má seznam sloupce **Původ** (CZ / SK nebo Zahraniční) a
**Playlist** (ve kterém staženém playlistu píseň je); podle obou jde řadit,
seskupovat karty a nahoře filtrovat. Původ je odhad — česká a slovenská
písmena ve jménech, název playlistu, uložený text písně, další písně téhož
interpreta — a kliknutím na značku ho opravíš (`origin.json` u stop).

**Asistent běží v počítači.** Volná česká věta („pick listen“, nebo napsaná do
pole Asistent) se provede bez internetu a bez placení (`assistant.py`):

1. *Kniha vět* (`assistant_book.py`, přes 1 700 vět vygenerovaných ze způsobů,
   jak se co říká; věty jdou spojovat „a“) — co v ní je, udělá se hned, bez modelu.
2. *Model* — Gemma 3 4B pod `llama-server` (soubor `pick-models/` na kartě nebo
   `~/.local/share/spotdl-helper/models/`; spustí se při prvním povelu, po 15
   minutách klidu uvolní paměť). Dostane stav aplikace, větu a nejpodobnější věty
   z knihy; odpovídá jen do pevného formuláře (JSON schema). Co je o malém
   modelu známo, že plete, opravuje `_mend` (jméno, které nikdo neřekl, mix
   místo přehrávače, kroky navíc); mazání hlasem nejde vůbec.
3. *Paměť* — co model udělal bez chyby, se zapíše (`assistant-learned.json`) a
   příště je hned. Čemu nerozuměl (`assistant-unknown.json`), to se naučí z další,
   jinak řečené věty, která se povedla. „Špatně“ poslední naučené zapomene navždy.

Bez modelu (karta není připojená) poslouží nejbližší věta z knihy. Když model
v počítači není, použije se Claude přes API jako dřív. Asistent umí i dalších
44 povelů Picku (nástroj `command`: ladička, metronom, taby, text, výslovnost,
zpěv, řazení, okna…). Co umí, co se naučil a čemu nerozuměl: `/assistant/book`
(odkaz „co umí“ u pole Asistent). Na téže stránce jde větu naučit ručně (věta + jedna
nebo dvě věci, které Pick umí; `teach`) a naučenou zapomenout.

**Charakter a hlasitost hlasu.** Ve Výslovnosti jde zvolit, jak hlas čte
(`TTS_STYLES`: Přirozený, Metalový, Agresivní, Naštvaný, Romantický, Klidný,
Šeptaný) a jak hlasitě. Model klonuje hlas, ne náladu: umí jen tempo a šepot,
zbytek charakteru dělá ffmpeg až na hotovém řádku (hloubka, zkreslení,
komprese, ekvalizér, ozvěna) — opravdovou náladu dá jen vzorek namluvený v ní.
Každý řádek, i přirozený, odchází stejně hlasitý (`_tts_finish`, RMS
`TTS_LOUD`); tichý vzorek dřív dělal tichý hlas. Charakter je součást klíče
řádku na disku; `TTS_MADE` se zvedá, když se změní výroba. Hlasu počítače se
charakter dělá rychlostí a výškou. Řádek, před který model přidal slovo navíc
(„Yeah, …“), se bere jako přeřeknutí a zkusí se znovu.

Výslovnost se hodnotí přísně (`compare(strict=True)`): slovo projde, jen když
bylo slyšet samo, nebo jako slovo, které zní stejně (to/too/two, runnin'/running)
— ne jako slovo podobné („fate“ za „hate“). Zpěv se hodnotí shovívavěji jako dřív.

**Hlas z Voiceboxu.** Anglické řádky čte model Chatterbox Turbo, když ho
Voicebox má stažený (na M1 Pro asi 8 s na řádek; vícejazyčný Chatterbox 30–50 s
a zůstává pro ostatní jazyky; LuxTTS je ještě rychlejší, ale polyká začátky
vět, Qwen do řádku mluví vlastní text). Když Voicebox běží bez svého hlasového
serveru, Pick ho sám zavře a spustí znovu; výslovnost ho při otevření sama budí.

**Melodie zpěvu** (`sing.py`, verze 3): kde si YIN není jistý — chraplavý
rockový hlas — bere se jeho nejhlubší propad místo ničeho, oktávové přeskoky
se srovnají podle okolní melodie a tóny daleko od ní (prosakující kytara, basa)
se zahodí. Zachytí tak zhruba dvojnásobek zpěvu.

Server spuštěný ikonou dostává holou systémovou cestu bez Homebrew; přidává si
ji sám, jinak nenajde ffmpeg (melodie zpěvu pak hlásila chybu). Text písně se v
mixu hledá podle oficiálního interpreta a názvu, ne podle názvu složky.

**Fadery při otevření.** Píseň, která má všechny fadery na 0 dB, se při otevření
změří (nejhlasitější místo součtu stop) a všechny fadery se stáhnou stejně, aby
zůstala rezerva asi 5 dB. Fadery jednou pohnuté rukou se nemění.

**Levý panel u otevřeného mixu** ukazuje, co je otevřené, a všechna alba toho
interpreta s písněmi (`/api/artist/discography`): co už je ve Steems (klik
otevře), co ne (zaškrtnout → „Stáhnout a rozdělit“ je stáhne, rozdělí a
dohledá tab a text). Studiová alba jsou první, podle roku prvního vydání
(co je studiové album a kdy vyšlo, říká MusicBrainz); živé nahrávky, výběry a
ostatní pod nimi. Stejné album ve více edicích je jednou. Nadpisy sekcí nahoře zůstávají jako cesta jinam; seznam
všech písní je dál pod mixem, nově se sloupcem Album.

Tab v mixu má jedno tlačítko **Sync / Lock sync**: odemčený („Sync“) se tab
posune rukou, klik ho připne k času písně a zamkne („Lock sync“, zámek); další
klik odemkne.

V otevřeném mixu: „pick see lyrics“ dá text písně do okna přes celou stránku,
„pick see mix“ ho vrátí a posune na stopy. „pick see more guitars“ přidá do tabu
další kytarovou stopu, „pick show me all guitars“ všechny, „pick see only guitar
two“ nechá jen tu s daným číslem (bez čísla hlavní). Běžící text jede plynule
(rychlost se mezi řádky mění postupně) a pod dozpívaný řádek se píše, co trenér
zpěvu slyšel.

Co trenér zpěvu u každého řádku slyšel, se ukládá k písni (`sing-words.json` u
stop; `/api/stems/<název>/sing/words`). V mixu je pod stopou „Text“ stopa „Můj
zpěv“: každý zazpívaný řádek se slovy zeleně (sedí), červeně (jinak) a šedě
(vynecháno) a s tím, co bylo slyšet místo nich; klik na blok tam skočí.

„Výslovnost“ (tlačítko u textu, „pick practice words“ / „pick read lyrics“)
otevře okno s celým textem písně. Po „Začít“ počítač řádek přečte (hlas
systému, anglicky nebo česky podle textu), poslechne si tě, whisper napíše, co
slyšel, a na další řádek pustí, až slova sedí (u řádku od deseti slov smí být
jedno vedle). „Přečti“ řádek zopakuje, „Přeskočit“ nebo „pick skip“ jde dál.
Číst může tvůj vlastní hlas naklonovaný v aplikaci Voicebox (její místní server
na `127.0.0.1:17493`, profil se vybírá v okně): řádek mu trvá asi 20 s, takže
server nechá každý řádek přečíst jednou na pozadí a zvuk si nechá (`tts/` vedle
nastavení; `/api/tts/prepare`, `/api/tts/audio/…`). Co ještě není hotové — a
české texty, které Voicebox neumí — čte hlas počítače (Samantha / Zuzana).
Ve výběru jsou všechny profily Voiceboxu (i další naklonované hlasy). Co je v textu
v závorkách — „(yeah)“, „[Chorus]“ — se ukáže šedě, ale nečte se ani nezkouší;
řádek jen ze závorek se vynechá.
Čte model Chatterbox (`VOICEBOX_ENGINE` v `server.py`): Qwen klonuje i podle přepisu
vzorku a s nesedícím přepisem říkal před každým řádkem jeho zbytky. Hotový zvuk si
server ještě nechá přečíst whisperem a co nesedí s textem, vyrobí jednou znovu.

Když řádek nesedí, projdou se špatná slova po jednom: aplikace slovo přečte, ty ho
zopakuješ, a teprve pak se zkouší celý řádek znovu („Přeskočit“ / „pick skip“ tu
přeskočí jen slovo). Každé nepovedené slovo se ukládá do jednoho seznamu pro všechny
písně (`speak-words.json` vedle nastavení; `/api/speak/words`). Tlačítko „Moje slova“
nebo „pick practice my words“ je pustí všechna najednou; co se povede, ze seznamu
zmizí. V okně je i zesílení mikrofonu s ukazatelem (čárka = odkud se bere, že mluvíš).

**Cvičení s kamarádem hlasem.** „pick stems sepultura“ ukáže ve Steems jen písně
té kapely (očíslované; i „pick band …“, když je Steems vpředu), „pick play two“ jednu
otevře — v *cvičném režimu* (výchozí; „pick practice mode on / off“) se načte se
ztlumenými kytarami, „pick guitars on / off“ je vrátí a vezme. Ladění ke kamarádovu
nástroji: „pick tune down / half step up / standard tuning“ posune kytary i basu o půltón,
„pick guitar lower / higher“, „pick bass lower / higher / pitch normal“ každé zvlášť.
Hlasitost a strany: „pick drums louder / quieter / normal“, „pick guitar left / right
/ center“, celková „pick up / down“. Text a tab: „pick lyrics“, „pick tabs“. Sekce:
„pick from chorus“ (i „go to solo“) hraje od další takové sekce bez smyčky, „pick loop
verse“ ji dá do smyčky; „pick loop start left / right“ a „pick loop end left / right“
(i „start earlier / later“, „end earlier / later“) posune okraj smyčky o půl vteřiny,
„pick loop on / off“.

**Zesílení mikrofonu** (nabídka mikrofonu → Zesílení, 0–30 dB): když zvukovka dává
mikrofon moc potichu, zesílí ho Pick sám — pro poslouchání v okně i z lišty
(`gain` ve `voice-widget.json`, ffmpeg `volume` + `alimiter`). Ukazatel vedle Vstupu
má při řeči skákat zhruba do půlky.

**„hey pick“** (jen budicí slovo, nic za ním): u všeho na obrazovce, co jde ovládat
hlasem, se objeví malý žlutý nápis s tím, co říct; nahoře je řádek povelů, které
tlačítko nemají. Dokud nápisy svítí (asi 10 s, po každém povelu se obnoví), stačí
nápis přečíst bez „pick“. Klik nebo Esc je schová. Co mluví místnost, se v tu chvíli
neřeší. (`voice.wake_call`, v aplikaci `hintList()` — nový povel se přidá tam.)
Po povelu řekne Pick jednu z deseti hlášek, při nerozumění jednu z deseti jiných —
hlasem „Max Cavalera“ z Voiceboxu (`SAY_OK` / `SAY_BAD`, stejná cesta `/api/tts/prepare`).
Vypíná se v nabídce mikrofonu; dokud hlášky nejsou připravené, platí „Again!!!“.

**Melodie po řádcích** (Trenér zpěvu → „Po řádcích“, „pick practice melody“): řádek
nejdřív zazpívá originál, pak ho pustí bez originálního zpěvu a zpíváš ty; hned se
ukáže, kolik tónů sedělo. Od 60 % (blízké tóny za půl) jde dál, jinak znovu od
poslechu. „↻ znovu“, „další ›“ / „pick skip“. Berou se jen řádky, kde je v originále
zpívaná melodie (křik a mluvení výšku nemají). Výsledky se neukládají.

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

## Tónový otisk

Tvoje živá kytara zní jako kytara z písně (tlačítko **OTISK** na kanálu Live
kytara; návrh v `docs/specs/2026-10-15-tonovy-otisk-design.md`). Aparáty ani
efekty neřešíš — engine si z knihovny NAM modelů (`amps/`) sám vybere jádro
zkreslení a zbytek dopočítá.

1. **Úsek** — smyčka z mixu, nebo jeden z návrhů `GET /api/tone/{song}/sections`
   (kde kytara hraje nejvíc a ostatní nejméně; u rozdělené kytary sólová/rytmická).
2. **Kalibrace** — píseň začne 3 s před úsekem a ty ho zahraješ s ní. Nahrává
   se čistý signál kytary (za GAIN, před gate a zesilovačem) a přes hodiny
   písně a latenci se zarovná na čas písně → `Stems/<píseň>/tone/take-N.wav`
   (`POST /api/tone/{song}/take?start=…&gain_db=…`, drží se posledních 8).
3. **Otisk** (`POST /api/tone/{song}/fit`, ~2 min, hlídá paměť) — `tone_worker.py`:
   dorovná zpoždění nahrávky proti originálu, změří, co barva spravit neumí
   (rozptyl hlasitosti v pásmech, plochost spektra mezi harmonickými, crest,
   sustain po úderu, souběh obálek), a zkouší jádra: všechna × 2 drive na 4 s,
   nejlepších 20 × 4 drive na 8 s, nejlepší 4 doladí Nelder–Meadem (drive,
   ořez basů, středy, sklon před jádrem). **Barvu nehledá** — pro každé jádro
   ji spočítá přímo (dlouhodobé spektrum originálu / jádra po 1/6 oktávy →
   minimální-fázová IR `print-ir.wav`, i s hlasitostí). Dozvuk odhadne z toho,
   jak tóny originálu doznívají tam, kde jsi přestal hrát; šířku z korelace L/P
   (zapne doubler). Výsledek `print.json`: řetězec, shoda v %, porovnání
   (zkreslení, sustain, jas, basy, prostor), křivky spekter, pořadí jader.
4. **Živě** — `static/tone.js` postaví v řetězci kytary totéž, co slyšel fit:
   drive → highpass/peaking/shelfy (vzorce Web Audio, stejné v Pythonu) →
   NAM jádro → `ConvolverNode` (IR, `normalize = false`) → dozvuk (IR ze
   stejného generátoru šumu mulberry32 jako v Pythonu) → doubler. Kompresor
   kytary je s otiskem obejitý. Změnu GAIN od nahrávky vyrovná drive. Otisk
   jedné písně jde zapnout u kterékoli jiné (`GET /api/tone/prints`) a po
   znovunačtení se vrátí, jakmile zapneš vstup kytary.
5. **Kontrola** — ▶ Originál / ▶ Ty a **slepý test** (5 kol) z mixů úseku,
   které složí server (`POST /api/tone/{song}/abx`: kapela + originální
   kytara, nebo kapela + `you.wav`). **Doladit** = nový fit i s novými
   nahrávkami, jen z 12 nejlepších jader minula; **Vrátit předchozí**
   (`POST /api/tone/{song}/undo`).

**Otisků může mít píseň víc** (rytmická kytara, sólo, jiný úsek): každý ve vlastní složce
`Stems/<píseň>/tone/prints/<datum-čas>/` (`print.json`, `print-ir.wav`, `you.wav`, `prev-*`).
Jméno dá Pick sám — „Kapela – Píseň“, u sóla / rytmiky „· sólo“ / „· rytmika“, a když je
otisků jedné písně víc, i začátek úseku — dokud ho nepřejmenuješ (`PATCH
/api/tone/{song}/prints/{id}` `{"name": …}`, prázdné jméno ho vrátí Picku). **Kategorie**
(Clean, Crunch, Hi-Gain, Sólo, Akustická, Ambient, Fuzz, Jiné): Pick ji odhadne ze zkreslení
originálu (< 30 clean, < 60 crunch, jinak hi-gain), změníš ji tamtéž `{"category": …}`.
Otisk z doby před více otisky (`tone/print.json`) se při prvním čtení sám přestěhuje.

**Knihovna otisků** (záložka v okně otisku, nebo Knihovna → Otisky zvuku v levém panelu,
`GET /api/tone/prints`): všechny otisky všech písní jako karty — obal písně, jméno (klik ho
přepíše na místě), kapela a píseň, shoda v kroužku, kategorie (klik ji změní), úsek, jádro,
křivka zvuku a ukazatele zkreslení, jasu a sustainu. Hledání, filtr podle kategorie, řazení
podle kapely / nejnovější / shody. 🎧 pustí krátkou ukázku otisku (`/preview/you`, tvoje
nahrávka přes otisk jako MP3) a 💿 originál ve stejném úseku (`/preview/record`), **Hrát**
otisk zapne na živé kytaře (s kteroukoli písní), ↗ otevře píseň a její otisky, 🗑 smaže
(dvakrát klikni).

**Moje kytara — kalibrace** (záložka v okně otisku, `/api/tone/guitars…`, data v
`~/.local/share/spotdl-helper/tone-guitars/<id>/`): jednou čtyři krátké nahrávky čisté kytary —
prázdné struny, 5. a 12. pražec, akordy (E, A, D, G, C, E5, A5) a riff s tlumením dlaní i naplno,
potichu i nahlas. basic-pitch (pick-midi) je převede na noty a `tone_worker.py --profile` z nich
udělá knihovnu tvých tónů a akordů (ladění, dusané údery, obvyklá síla úhozu). Při otisku pak
Pick převede kytaru originálu v úseku na noty a z knihovny složí **tvou kytaru hrající přesně to,
co originál** (`pseudo_take`): ke každému úderu nejbližší tón nebo akord, posunutý na výšku
originálu (píseň laděná o tón níž → E2 zahraje D2), dusaný tam, kde originál dusá, stejně hlasitě
a na milisekundu ve stejnou chvíli. Fit ho má jako první nahrávku (bez posunu), tvoje nahrávky s
písní k němu, srovnané na tvou obvyklou sílu úhozu. Otisk pak jde **i bez hraní s písní**.
Výsledek ukáže i **tvůj zvuk teď** (zesilovač a bedna, které máš v Picku) proti otisku: „Tvůj zvuk
teď: 41 % → s otiskem 78 %“. Ověřuje `tone_worker.py --selftest-guitar` (struny, ladění, dusání,
údery na čase originálu, D2 z E2).

Zatím neumí delay, chorus/flanger, wah ani oktávery a dvě kytary přes sebe
napodobí jen doublerem. Engine se testuje sám: `tone_worker.py --selftest`
pošle vymyšlený riff známým řetězcem a fit musí najít stejné jádro, drive
±4,5 dB, dozvuk a zpoždění nahrávky (běží i v `pytest`, když je venv a Node).

## Zvuk bez výpadků

Nastavení → Zvuk a MIDI → **Zvuk bez výpadků**. Zvuk (mix ve Steems, živá kytara) se renderuje
v prohlížeči a trhá se, když mu procesor nebo paměť sebere něco jiného — u Picku hlavně jeho
vlastní těžké úlohy. Proto:

- **Ochrana zvuku** (`audioguard.py`, `POST /api/audio/active`, `GET|POST /api/audio/guard`):
  každé okno Picku hlásí každé 4 s (a hned při startu mixu), jestli hraje — mix, dolní
  přehrávač, nebo zapnutý vstup kytary. Dokud hraje (12 s bez hlášení = ticho), server pošle
  své těžké procesy (Demucs, whisper, tónový otisk a jeho Node, Seed-VC, OmniVoice, analýzy,
  stahování, model Skladatele) do pásma pozadí macOS (`taskpolicy -b`: úsporná jádra,
  přiškrcený disk a síť, pod vším, co dělá hráč) a po dohrání je vrátí (`taskpolicy -B`) —
  bez roota, nic se neztratí. Režim **Pozastavit** ty, které můžou počkat (rozdělování,
  odposlech, otisk, analýzy, stahování), úplně zastaví (SIGSTOP) a po dohrání pustí dál.
  Nastavení se drží v `config.json` (`audio_guard`); Úlohy a procesy u nich ukážou
  „ustupuje hudbě“. Úloha nechaná zastavená spadlým serverem se při startu pustí.
- **Vyrovnávací paměť zvuku** (`AudioPrefs`): Nejnižší = `latencyHint: "interactive"` (živá
  kytara), Vyvážená = 25 ms, Velká = 60 ms (nejstabilnější, kytara opožděná) — pro kontext
  mixu, kytary v dolním přehrávači a kláves; platí po znovunačtení (tlačítko Použít).
- **Výpadky zvuku**: malý worklet v audiovlákně mixu (`pick-glitch`) si všimne, když mezi dvěma
  voláními uběhlo víc času, než dovolí buffer (max(60 ms, 2,5 × baseLatency + 20 ms)) — počet
  za posledních 30 minut je v Nastavení; při třech za dvě minuty Pick jednou poradí.

## Mikrofon pro zpěv a hlas

Nastavení → Zvuk a MIDI → **Mikrofon pro zpěv a hlas**: jeden výběr mikrofonu (a jeho vstupu)
pro trenéra zpěvu, Výslovnost, nový hlas, úryvek zpěvu k písni, Namluvit u přezpívání řádku a
hlasové ovládání (klíče `pick-voice-mic` / `pick-voice-ch`, stejné ukazuje i výběr v horní liště).
Kytara má svůj vstup dál v nastavení kytary — prohlížeč poslouchá obě zařízení zároveň (USB
mikrofon i zvukovku), nic se spojovat nemusí. `VoiceMic.stream()` vezme zvolený vstup samotný
(zvukovka 1/2, agregované zařízení 3…; zastavení jeho stopy pustí i mikrofon), `VoiceMic.open()`
celé zařízení pro worklety trenéra a ovládání, odpojený mikrofon nahradí výchozím. Vyzkoušet:
ukazatel hlasitosti a počet vstupů zařízení. Návod na agregované zařízení v macOS (Nastavení
Audio MIDI) je v sekci rozbalovací — pro aplikace, které umí jen jeden vstup.

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
