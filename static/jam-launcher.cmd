@echo off
rem Pick Jam - da na plochu ikonu "Pick Jam": ta zapne Pick Jam Engine (nativni zvuk) a otevre Pick Jam v Chrome (nebo v Edge).
rem Neobsahuje zadne heslo ani klic, jen adresu. Jmeno a heslo se zada v okne aplikace.
title Pick Jam
set "PJ_URL={{URL}}"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Get-Content -LiteralPath '%~f0' -Raw -Encoding UTF8; Invoke-Expression ($t -split ('#' + 'PICKJAM' + '#'))[1]"
if errorlevel 1 pause
exit /b
#PICKJAM#
$url = $env:PJ_URL
$dir = "$env:LOCALAPPDATA\PickJam"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$ico = "$dir\pick-jam.ico"
try { Invoke-WebRequest -UseBasicParsing -Uri ($url + 'static/jam-icon.ico') -OutFile $ico } catch { }
$cands = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
           "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
           "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
           "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
           "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
$browser = $cands | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browser) {
  Write-Host 'Nenasel jsem Google Chrome ani Microsoft Edge. Nainstaluj Chrome (google.com/chrome) a spust tenhle soubor znovu.'
  exit 1
}
# the start script the desktop icon runs: Pick Jam Engine (the native sound) first, then the app window
$start = "$dir\start.ps1"
@"
`$dir = "$dir"
`$exe = "`$dir\pick-jam-engine.exe"
`$url = "$url"
`$running = Get-Process pick-jam-engine -ErrorAction SilentlyContinue
if (-not `$running) {
  try {
    `$tmp = "`$exe.new"
    Invoke-WebRequest -UseBasicParsing -Uri (`$url + 'engine/pick-jam-engine.exe') -OutFile `$tmp -TimeoutSec 30
    if ((Get-Item `$tmp).Length -gt 100000) { Move-Item -Force `$tmp `$exe } else { Remove-Item `$tmp -ErrorAction SilentlyContinue }
  } catch { }
  if (Test-Path `$exe) { Start-Process -FilePath `$exe -ArgumentList 'serve' -WindowStyle Hidden }
}
Start-Process -FilePath "$browser" -ArgumentList "--app=`$url"
"@ | Set-Content -LiteralPath $start -Encoding UTF8
$desk = [Environment]::GetFolderPath('Desktop')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\Pick Jam.lnk")
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
$lnk.Description = 'Pick Jam - pisne, zvuky a jam s {{HOST}}'
if (Test-Path -LiteralPath $ico) { $lnk.IconLocation = "$ico,0" }
$lnk.Save()
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`""
Write-Host 'Hotovo: na plose je ikona Pick Jam. Priste spoustej Pick Jam jen pres ni (zapne i engine pro nejmensi zpozdeni).'
Write-Host 'Kdyz se Windows zepta na firewall pro pick-jam-engine, klikni Povolit.'
Start-Sleep -Seconds 5
