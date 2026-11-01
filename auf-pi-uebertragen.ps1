# Sportstracker+ auf den Raspberry Pi uebertragen und installieren.
#   Rechtsklick > "Mit PowerShell ausfuehren"  oder im Ordner:  .\auf-pi-uebertragen.ps1
# Die config.json des Pi bleibt unberuehrt (sie enthaelt Liga, Team, MQTT, Satelliten). Neue Einstellungen haben Standardwerte.
$ErrorActionPreference = "Stop"
$PiHost = "pi@192.168.22.22"
$Source = Join-Path $PSScriptRoot "sportstracker"
$Stage  = Join-Path $env:TEMP "sportstracker-upload"

Write-Host "== 1/5 Erreichbarkeit pruefen"
if (-not (Test-Connection -ComputerName 192.168.22.22 -Count 1 -Quiet)) { throw "Der Pi (192.168.22.22) antwortet nicht auf ping." }

Write-Host "== 2/5 Dateien zusammenstellen (ohne config.json, __pycache__, Vorschaubilder)"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
robocopy $Source $Stage /E /XD __pycache__ .git /XF config.json *.pyc *.tmp demo.png demo_preview.png vorschau-*.png /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy ist fehlgeschlagen (Code $LASTEXITCODE)" }

Write-Host "== 3/5 Sicherung der Pi-Konfiguration"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
ssh $PiHost "mkdir -p ~/sportstracker-backup && cp ~/sportstracker/config/config.json ~/sportstracker-backup/config-$stamp.json && echo '   gesichert: ~/sportstracker-backup/config-$stamp.json'"
if ($LASTEXITCODE -ne 0) { throw "Sicherung fehlgeschlagen (existiert ~/sportstracker auf dem Pi?)" }

Write-Host "== 4/5 Uebertragen"
ssh $PiHost "rm -rf ~/sportstracker-upload"
scp -r "$Stage" "${PiHost}:~/sportstracker-upload"
if ($LASTEXITCODE -ne 0) { throw "scp ist fehlgeschlagen" }
ssh $PiHost "cp -r ~/sportstracker-upload/. ~/sportstracker/ && rm -rf ~/sportstracker-upload && echo '   Dateien kopiert nach ~/sportstracker'"
if ($LASTEXITCODE -ne 0) { throw "Kopieren auf dem Pi fehlgeschlagen" }

Write-Host "== 5/5 Installieren (sudo-Kennwort des Pi wird gefragt)"
ssh -t $PiHost "cd ~/sportstracker && sudo bash deploy/install.sh"

Write-Host ""
Write-Host "Fertig. Web-Backend: http://192.168.22.22:8080  (Seiten: Einstellungen = Designs, WLAN = Setup-Hotspot)"
Write-Host "Pruefen:  ssh $PiHost 'nmcli -t -f RUNNING general; journalctl -u sportstracker-wifi -n 20 --no-pager'"
