# Übertragung auf den Pi: Version 0.7.0

## Ablauf (PowerShell im Projektordner)

```powershell
.\auf-pi-uebertragen.ps1
```

Das Skript prüft ping, sichert die `config.json` des Pi nach `~/sportstracker-backup/`, überträgt den Code **ohne** die lokale `config.json`, führt `deploy/install.sh` aus (siebter Dienst `sportstracker-wifi`, `python3-qrcode`, sudo-Regel) und zeigt den Dienststatus. Lokal kein Python nötig.

Ist die Skriptausführung gesperrt: `powershell -ExecutionPolicy Bypass -File .\auf-pi-uebertragen.ps1`

## Danach prüfen (ca. 5 Minuten)

1. `nmcli -t -f RUNNING general` auf dem Pi zeigt `running` (NetworkManager).
2. Backend http://192.168.22.22:8080 → **WLAN**: Status „verbunden“. Dort **PIN und Hotspot-Kennwort ändern** (Standard 1234 / setup1234 steht im öffentlichen Repo).
3. Backend → Einstellungen → Spielfreie Zeit: Raumansicht C, Satellitenseite B (Standard) ansehen, A/B der Raumansicht probieren; Vorschau auf der Übersicht.
4. Ein Satellit mit Luftwerten sollte die Halbkreis-Seite zeigen.
5. Hotspot-Test: Backend → WLAN → Wartezeit 60 s, am Pi `sudo nmcli device disconnect wlan0`; nach etwa einer Minute erscheint der QR-Code. Handy verbinden, `http://10.42.0.1:8080/wifi/setup`, PIN, eigenes Netz wählen. Danach Wartezeit wieder auf 180 s.

Achtung bei Schritt 5: Läuft der Test per SSH über dasselbe WLAN, bricht die Sitzung ab. Besser am Pi-Bildschirm/Tastatur oder über das Backend; der Dienst bringt den Pi nach dem Hotspot wieder in das bekannte WLAN, wenn nichts eingegeben wird (spätestens nach 10 Minuten).

## Zurückrollen

```bash
cp ~/sportstracker-backup/config-<Zeitstempel>.json ~/sportstracker/config/config.json
sudo systemctl disable --now sportstracker-wifi
sudo systemctl restart sportstracker-display sportstracker-web
```
