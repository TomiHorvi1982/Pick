@echo off
rem Pick Jam - da na plochu ikonu "Pick Jam" a otevre Pick Jam jako aplikaci v Chrome (nebo v Edge).
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
$desk = [Environment]::GetFolderPath('Desktop')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\Pick Jam.lnk")
$lnk.TargetPath = $browser
$lnk.Arguments = "--app=$url"
$lnk.Description = 'Pick Jam - pisne, zvuky a jam s {{HOST}}'
if (Test-Path -LiteralPath $ico) { $lnk.IconLocation = "$ico,0" }
$lnk.Save()
Start-Process -FilePath $browser -ArgumentList "--app=$url"
Write-Host 'Hotovo: na plose je ikona Pick Jam. Priste spoustej Pick Jam jen pres ni.'
Start-Sleep -Seconds 3
