# Sportstracker+ – Bedienung (Kurzreferenz)

Pi: **`pi@192.168.22.22`** (MatrixRGB, feste IP) · Web-Backend: **http://192.168.22.22:8080** (Erstanmeldung admin / admin)

PC-Befehle laufen in der Windows-PowerShell im Ordner `D:\05-IT & PROJEKTE\07-CLAUDE\PI-RGB-Sportstracker`, Pi-Befehle nach `ssh pi@192.168.22.22`. Ausführlich: `ANLEITUNG.md`.

## 1. Verbinden

```powershell
ping 192.168.22.22
ssh pi@192.168.22.22
```

## 2. Dateien übertragen (scp)

```powershell
# Gesamten Projektordner (Standard nach Änderungen)
scp -r .\sportstracker pi@192.168.22.22:~/

# Nur eine Datei
scp .\sportstracker\sportstracker\webapp.py pi@192.168.22.22:~/sportstracker/sportstracker/
scp .\sportstracker\sportstracker\templates\picture.html pi@192.168.22.22:~/sportstracker/sportstracker/templates/

# Nur den ESP32-Ordner
scp -r .\sportstracker\esp32 pi@192.168.22.22:~/sportstracker/

# Konfiguration vom Pi zurückholen (Sicherung)
scp pi@192.168.22.22:~/sportstracker/config/config.json .\sportstracker\config\config.json
```

Achtung: `scp -r` des ganzen Ordners überschreibt auch `config/config.json` mit der Version vom PC. Vorher die Pi-Konfiguration zurückholen (letzter Befehl), falls im Backend etwas eingestellt wurde.

Nach dem Übertragen (auf dem Pi):

```bash
# Nur Code/Vorlagen geändert:
sudo systemctl restart sportstracker-web sportstracker-display

# Neue Dienste/Dienstdateien (z. B. erstmals 0.6.0) oder sudo-Regel:
cd ~/sportstracker && sudo bash deploy/install.sh
```

Bei „Permission denied" auf `.pyc`-Dateien: `sudo find ~/sportstracker -name __pycache__ -type d -exec rm -rf {} +`

## 3. Dienste starten, stoppen, neu starten

Dienste: `sportstracker-data`, `sportstracker-display`, `sportstracker-sensor`, `sportstracker-web`, `sportstracker-safety`, `sportstracker-mqtt`

```bash
sudo systemctl restart sportstracker-display          # einen Dienst neu starten
sudo systemctl stop sportstracker-display             # anhalten (Matrix wird dunkel)
sudo systemctl start sportstracker-display            # starten

# alle fünf wichtigen auf einmal
sudo systemctl restart sportstracker-data sportstracker-display sportstracker-sensor sportstracker-web sportstracker-mqtt
sudo systemctl stop sportstracker-data sportstracker-display sportstracker-sensor sportstracker-web sportstracker-mqtt

# Autostart beim Booten
sudo systemctl enable sportstracker-display
sudo systemctl disable sportstracker-display
```

Vor Matrix-Tests (`hello_matrix.py`, `cli preview --show`) immer erst die Anzeige anhalten, danach wieder starten:

```bash
sudo systemctl stop sportstracker-display
sudo ~/venv/bin/python ~/sportstracker/hello_matrix.py
sudo systemctl start sportstracker-display
```

## 4. Status prüfen

```bash
systemctl is-active sportstracker-data sportstracker-display sportstracker-sensor sportstracker-web sportstracker-safety sportstracker-mqtt
systemctl status sportstracker-display --no-pager
systemctl --failed                                    # alle fehlgeschlagenen Dienste
vcgencmd measure_temp                                 # CPU-Temperatur (Abschaltung bei 84 °C)
uptime; free -h; df -h /                              # Last, Speicher, Platz
```

Im Backend: **System** (Dienststatus, Protokolle, Temperatur) und **Übersicht** (Live-Vorschau der Matrix).

## 5. Fehlersuche

```bash
journalctl -u sportstracker-display -n 100 --no-pager   # letzte 100 Zeilen
journalctl -u sportstracker-display -f                  # live mitlesen (Strg+C beendet)
journalctl -u sportstracker-web -n 100 --no-pager
journalctl -u sportstracker-data -n 100 --no-pager
journalctl -u sportstracker-sensor -n 100 --no-pager
journalctl -u sportstracker-mqtt -n 100 --no-pager
journalctl -p err -b --no-pager                         # alle Fehler seit dem Start
```

| Problem | Prüfen |
|---|---|
| Matrix dunkel | `systemctl is-active sportstracker-display`; Log der Anzeige; Netzteil/Verkabelung; läuft ein Test noch? |
| Keine aktuellen Spieldaten | Log von `sportstracker-data`; roter Punkt oben rechts auf der Matrix = Abfrage fehlgeschlagen; `cd ~/sportstracker && python3 -m sportstracker.cli check --config config/config.json` |
| Web-Backend nicht erreichbar | `ping 192.168.22.22`; `systemctl status sportstracker-web`; Port: `ss -tlnp \| grep 8080` |
| Änderung im Backend wirkt nicht | `sudo systemctl restart sportstracker-web sportstracker-display` |
| Raumwerte zeigen Striche | Log von `sportstracker-sensor`; DHT22 im Backend aktiviert? Werte älter als 5 Minuten werden als Striche gezeigt |
| Satellit zeigt Striche | Werte älter als 10 Minuten; `cat ~/sportstracker/data/satellites.json`; Log von `sportstracker-mqtt`; Token/Schlüssel/WLAN des ESP32 prüfen |
| Kamera | `rpicam-hello --list-cameras`; `dmesg \| grep -i imx219`; Flachbandkabel prüfen |
| Pi schaltete sich ab | Backend-Übersicht (Warnung) oder `cat ~/sportstracker/data/safety.json` – Gehäuse/Lüftung prüfen |
| Alles hängt | `sudo reboot` |

Satelliten testen (HTTP vom PC, Token aus der Seite „Satelliten"):

```powershell
curl.exe -X POST http://192.168.22.22:8080/api/satellite/kueche -H "Authorization: Bearer DEIN_TOKEN" -H "Content-Type: application/json" -d "{\"temperature\":21.5,\"humidity\":48,\"gas\":900}"
```

Satelliten testen (MQTT, Mosquitto auf dem Home-Assistant-Raspi; `mosquitto-clients` nötig):

```bash
mosquitto_sub -h <HA-IP> -t 'sportstracker/#' -v        # mithören
mosquitto_pub -h <HA-IP> -t sportstracker/kueche/state -m '{"temperature":21.5,"humidity":48,"gas":900}'
```

Tests auf dem Pi (offline, 520 Tests):

```bash
cd ~/sportstracker && PYTHONPATH=.:tests python3 -m unittest discover -s tests
```

## 6. Neustart und Herunterfahren

```bash
sudo reboot
sudo shutdown -h now          # danach Netzteil erst nach Erlöschen der grünen LED trennen
```

Auch im Backend unter **System**.

## 7. Installation und Entfernen

```bash
cd ~/sportstracker
sudo bash deploy/install.sh               # installieren/aktualisieren (mehrfach ausführbar)
sudo bash deploy/install.sh --uninstall   # Dienste entfernen
```

## 8. Wichtige Pfade auf dem Pi

| Pfad | Inhalt |
|---|---|
| `~/sportstracker/config/config.json` | Konfiguration (wird vom Backend geschrieben) |
| `~/sportstracker/data/` | `state.json`, `sensor.json`, `satellites.json`, `pictures/`, `gallery/`, `safety.json` |
| `~/sportstracker/esp32/` | Sketch und Anleitung für die Satelliten |
