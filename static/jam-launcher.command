#!/bin/bash
# Pick Jam pro Mac: na plochu dá aplikaci "Pick Jam" s ikonou a otevře ji.
# Aplikace otevře knihovnu v Chromu (nebo v Edge či Brave) jako samostatné okno.
# Je v ní jen adresa. Žádné heslo ani klíč: jméno a heslo se zadá v okně.
URL="{{URL}}"
APP="$HOME/Desktop/Pick Jam.app"

echo "Pick Jam: připravuji aplikaci na ploše…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" || { echo "Na plochu nejde zapisovat."; exit 1; }
curl -fsSL "${URL}static/jam-icon.icns" -o "$APP/Contents/Resources/PickJam.icns" || echo "(ikonu se nepodařilo stáhnout, aplikace bude bez ní)"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Pick Jam</string>
  <key>CFBundleDisplayName</key><string>Pick Jam</string>
  <key>CFBundleIdentifier</key><string>cz.neverlast.pickjam</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>PickJam</string>
  <key>CFBundleIconFile</key><string>PickJam</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/PickJam" <<EOF
#!/bin/bash
# nejdřív Pick Jam Engine (nativní zvuk, nejmenší zpoždění): čerstvá kopie z knihovny, spuštěná na pozadí
E="\$HOME/Library/Application Support/PickJam"
mkdir -p "\$E"
if ! pgrep -f "pick-jam-engine-mac serve" >/dev/null; then
  if curl -fsSL --max-time 30 "${URL}engine/pick-jam-engine-mac" -o "\$E/engine.new" && [ \$(stat -f%z "\$E/engine.new") -gt 100000 ]; then
    mv -f "\$E/engine.new" "\$E/pick-jam-engine-mac"
  fi
  chmod +x "\$E/pick-jam-engine-mac" 2>/dev/null
  xattr -d com.apple.quarantine "\$E/pick-jam-engine-mac" 2>/dev/null
  [ -x "\$E/pick-jam-engine-mac" ] && ("\$E/pick-jam-engine-mac" serve >/dev/null 2>&1 &)
fi
# pak Pick Jam jako aplikace v prvním prohlížeči, který tu je (Chrome umí mikrofon i zvukovku nejlíp)
for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
  if [ -d "/Applications/\$b.app" ] || [ -d "\$HOME/Applications/\$b.app" ]; then
    exec open -na "\$b" --args --app="$URL"
  fi
done
exec open "$URL"
EOF
chmod +x "$APP/Contents/MacOS/PickJam"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
touch "$APP"

if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  echo "Tip: s Google Chrome (google.com/chrome) funguje mikrofon a zvukovka nejlíp."
fi
open "$APP"
echo "Hotovo: na ploše je Pick Jam. Příště ho spouštěj jen přes tu ikonu (klidně si ji dej do Docku)."
sleep 3
