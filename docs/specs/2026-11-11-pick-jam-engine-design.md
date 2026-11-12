# Pick Jam Engine — nativní zvuk pro Pick Jam (návrh)

Datum: 2026-11-11 · Stav: schváleno; fáze 1, 2 a 3 (WASAPI) hotové (2026-11-12), ASIO čeká na první běh GitHub Actions

## Proč

Pick Jam dnes posílá kytaru přes prohlížeč (WebRTC data channel, PCM16 po 2,7 ms, vlastní jitter
buffer v `static/jam-worklet.js`). Strop latence je dán prohlížečem: AudioContext + Chrome přidají
na každé straně zhruba 10–20 ms, které z JavaScriptu nejde ubrat. Nativní aplikace mluví přímo
s CoreAudio/WASAPI/ASIO a umí buffer 64–128 vzorků.

Vzor architektury: nativní audio helper + webová stránka, která ho ovládá přes lokální WebSocket
(tak to dělá i Jamodio, viz analýza z 2026-11-11). **Implementace je vlastní**, postavená na veřejných
knihovnách; nic z dekompilovaného kódu Jamodia se nepřebírá.

## Rozhodnutí (uživatel, 2026-11-11)

- **Nativní helper + fallback:** když engine na počítači běží, stránka ho najde a zvuk jde nativně.
  Když neběží, všechno funguje jako dnes v prohlížeči. Nic se neruší.
- **Platformy:** macOS (Apple Silicon) i Windows.

- **Zdroj kytary (2026-11-12):** každý si vybere, co engine posílá — *přímo ze zvukovky* (nejrychlejší,
  jen zvuk, který do zvukovky už jde: DI nebo hardwarový modeler) nebo *zvuk z Picku / ze stránky*
  (otisk, NAM, efekty, u kamaráda zesilovač a EQ; stránka posílá float vzorky enginu přes ovládací
  socket, odesílání nese latenci prohlížeče, příjem a přehrání jde nativně).

## Architektura

```
 Pick stránka (host)              kamarádova stránka (guest)
   │ ws://127.0.0.1:9877            │ ws://127.0.0.1:9877
   ▼                                ▼
 Pick Jam Engine  ◄── UDP (Opus/PCM, Tailscale) ──►  Pick Jam Engine
   │ CoreAudio                      │ WASAPI / ASIO / CoreAudio
   🎸 zvukovka                       🎸 zvukovka
```

Kamery, obrazovka, chat a hlas zůstávají ve WebRTC jako dnes. Engine přebírá jen kytaru (lane „au“).

### Engine (`engine/`, Rust, jeden binár)

| Část | Knihovna | Poznámka |
|---|---|---|
| Audio I/O | `cpal` | CoreAudio, WASAPI, ASIO (feature `asio`, vyžaduje ASIO SDK při buildu) |
| Kodek | `opus` (libopus) | 48 kHz, `RESTRICTED_LOWDELAY`, rámce 2,5/5 ms, inband FEC; volitelně PCM16 bez komprese jako dnes |
| Transport | `std::net::UdpSocket` ve vlastním vlákně | vlastní 12bajtová hlavička: seq, timestamp vzorků, ID streamu |
| Šifrování | — | jen přes Tailscale (WireGuard už šifruje); bez Tailscale se nativní režim nenabídne |
| Příjem | vlastní | adaptivní jitter buffer (odhad jitteru RFC 3550), kompenzace driftu hodin jemným resamplingem (`rubato`), PLC z Opusu |
| Ovládání | `tokio-tungstenite` | WebSocket jen na 127.0.0.1:9877, whitelist Origin (Pick, jam gateway), párovací token |
| Real-time | — | audio callback bez alokací a zámků; komunikace přes `ringbuf` |

### Protokol ovládání (JSON přes WS)

- `hello {token}` → `{version, devices, rate, buffer}`
- `devices` / `set_device {input, output, buffer}`
- `connect {peer: "100.x.y.z:port", codec: "opus"|"pcm", frame_ms}` / `disconnect`
- `levels {in, out, peer}` / `mix {self_monitor, peer_gain}`
- události z enginu: `stats {rtt, jitter, buffer_ms, loss, latency_total_ms}` 1× za sekundu

Adresu protějšku (Tailscale IP + port) si strany vymění přes stávající datový kanál „ctl“
(`{type: "native", ok, ip, port}` nebo `{ok: false, why}`). `serve --ip` oznámí jinou adresu
(domácí síť, testy). Návrat do „low“ s důvodem: engine nenalezen, bez Tailscale, kamarád nemá
engine, UDP neprošlo (nic nepřišlo 3 s), engine se odpojil.

### Změny v Picku

- `static/jam-engine.js`: detekce enginu (pokus o WS na 127.0.0.1:9877 s krátkým timeoutem),
  nový režim `native` vedle `low` a `rtc`, výměna endpointů přes „ctl“. Když nativní spojení
  selže (UDP neprojde, engine spadne), automatický návrat do `low`.
- UI: v Jam panelu stav „Engine: připojen / nenainstalován (stáhnout)“ a celková latence.
- `jam.py`: gateway nabízí ke stažení instalátor enginu (Mac .app/.dmg, Windows .exe).
- Mapa aplikace (`static/mapa.html`) dostane novou kartu.

## Fáze

1. **Jádro na Macu:** capture → Opus → UDP → jitter buffer → přehrání. Test smyčkou na jednom Macu
   (dvě instance na localhostu) a měření latence (round-trip přes kabel out→in).
2. **WS ovládání + integrace do Picku:** režim `native` v jam-engine.js, dva počítače přes Tailscale.
3. **Windows:** WASAPI build z Macu (`x86_64-pc-windows-gnu`, mingw-w64; jen systémové DLL), ASIO build
   v GitHub Actions (`.github/workflows/engine.yml`: testy na Windows, `--features asio`, SDK se stahuje
   při buildu). *Hotovo 2026-11-12:* zvukovka se otevírá v jejím vlastním formátu (f32/i16/i32/u16) a rychlosti,
   engine převzorkuje na 48 kHz a zpět (na Windows vždy rychlost zařízení: WASAPI sdílený režim jinou
   nevezme); volba ovladače (`host`: WASAPI/ASIO) v ovládacím protokolu i na stránce; `hello` odpovídá
   hned a seznam zařízení posílá zvlášť (výčet trvá na pomalých strojích sekundy); kamarád si engine stáhne
   z jam brány `/engine/pick-jam-engine.exe` (jen přihlášený), `engine/build-dist.sh` sestaví obě verze do
   `engine/dist/`. Ověřeno pod Wine (CrossOver): selftest a oba směry proti Mac enginu přes WASAPI.
   ASIO z Macu nejde (bindgen + mingw hlavičky dávají jiné rozložení struktur).
4. **Doplňky:** monitoring mix, statistiky v UI, automatické ladění velikosti bufferu, instalátor
   a podpis (Mac: bez Developer ID zatím přes „otevřít i tak“, Windows SmartScreen).

## Rizika a otevřené věci

- **Cloudflare režim (vlastní doména):** kamarád pak není v tailnetu a přímé UDP potřebuje
  NAT traversal. Ve fázi 1–3 se nativní režim nabídne jen přes Tailscale, jinak fallback.
- **Gatekeeper / Little Snitch:** nepodepsaná aplikace a nové síťové spojení. Uživatel to musí
  schválit ručně.
- **ASIO:** licence SDK od Steinbergu dovoluje použití, ale SDK se nesmí redistribuovat ve zdrojácích;
  stáhne se při buildu.
- **Ověření na Windows:** testovat půjde jen na kamarádově PC.
