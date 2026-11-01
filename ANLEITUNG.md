# Anleitung: Sportstracker+ auf den Pi übertragen (Stand 0.7.0)

Die wichtigsten Alltagsbefehle (Start/Stopp, Fehlersuche, scp) stehen kurz in `BEDIENUNG.md`.

Pi: `pi@192.168.22.22` (RGB Matrix Pi, feste IP; Web-Backend `http://192.168.22.22:8080`). Alle Befehle laufen in der Windows-PowerShell auf dem PC (`scp` und `ssh` sind unter Windows 10/11 enthalten), sofern nicht anders angegeben.

Ordnerstruktur auf dem PC:

```
D:\05-IT & PROJEKTE\07-CLAUDE\PI-RGB-Sportstracker\
├── ANLEITUNG.md
├── BEDIENUNG.md            <- Kurzreferenz: scp, Start/Stopp, Fehlerprüfung
├── sportstracker\          <- Projektordner, wird auf den Pi kopiert
│   ├── sportstracker\      <- Python-Paket (Provider, Config, CLI)
│   ├── tests\
│   ├── deploy\            <- systemd-Dienste, install.sh
│   ├── esp32\             <- Arduino-Sketch und Anleitung für die Satelliten
│   ├── config\
│   ├── hello_matrix.py     <- Messtest Matrix
│   └── README.md
└── _archiv\                <- ursprüngliches Archiv 0.1.0
```

## 1. Gesamten Stand übertragen

```powershell
cd "D:\05-IT & PROJEKTE\07-CLAUDE\PI-RGB-Sportstracker"
scp -r .\sportstracker pi@192.168.22.22:~/
```

Das legt `~/sportstracker` auf dem Pi an bzw. überschreibt vorhandene Dateien. Dateien, die es nur auf dem Pi gibt (z. B. `config/config.json`), bleiben erhalten. Auf dem PC gelöschte Dateien werden auf dem Pi nicht gelöscht.

## 2. Einzelne Datei nachschieben

```powershell
scp .\sportstracker\sportstracker\providers\genius_rbbl.py pi@192.168.22.22:~/sportstracker/sportstracker/providers/
scp .\sportstracker\hello_matrix.py pi@192.168.22.22:~/sportstracker/
```

## 3. Anmelden und einrichten (einmalig)

```powershell
ssh pi@192.168.22.22
```

Auf dem Pi:

```bash
sudo apt update && sudo apt install -y python3-lxml
cd ~/sportstracker
cp config/config.example.json config/config.json        # DEL
# oder: cp config/config.rbbl.example.json config/config.json   # RBBL
```

## 4. Datenquellen prüfen (kein Root nötig)

```bash
cd ~/sportstracker
PYTHONPATH=.:tests python3 -m unittest discover -s tests          # Tests, offline
python3 -m sportstracker.cli teams --config config/config.json    # Teams und IDs
python3 -m sportstracker.cli check --config config/config.json    # Tabelle + Spiel des Tages
```

Mit `--now 2026-10-30T12:00:00+01:00` lässt sich ein anderer Zeitpunkt simulieren.

## 5. Matrix-Messtest (Root nötig)

```bash
cd ~/sportstracker
sudo ~/venv/bin/python hello_matrix.py                               # adafruit-hat-pwm, Slowdown 1, 60 s
sudo ~/venv/bin/python hello_matrix.py --slowdown 2 --seconds 120    # andere Werte
sudo ~/venv/bin/python hello_matrix.py --mapping adafruit-hat        # ohne GPIO4-18-Brücke
```

Zweite SSH-Sitzung für die Messung: `top -H` beobachten. Währenddessen in der ersten Sitzung bzw. einer dritten den `cli check` laufen lassen, um Last zu simulieren. Das Skript gibt am Ende die CPU-Zeit des Python-Prozesses aus. Beurteilen: stabiles Bild, kein Flackern, Refresh-Rate dauerhaft ausreichend.

## 5a. Bild-Rendering testen (ab Version 0.2.0)

Zuerst den Projektordner neu übertragen (siehe 1.), dann auf dem Pi:

```bash
sudo apt install -y librsvg2-bin                       # einmalig, für SVG-Logos
cd ~/sportstracker
python3 -m sportstracker.cli bench --runs 50           # Renderzeit auf dem Zero
sudo ~/venv/bin/python -m sportstracker.cli preview --config config/config.json --show --seconds 30
sudo ~/venv/bin/python -m sportstracker.cli preview --config config/config.json --show --table
```

`--now 2026-10-25T20:00:00+01:00` zeigt ein Spielbild des 25.10. (Schwenningen gegen Eisbären). Beim ersten Lauf lädt der Logo-Cache die Logos aus dem Netz, danach kommen sie aus `~/sportstracker/cache`.

## 5b. Dauerbetrieb installieren: Dienste und Web-Backend (ab Version 0.3.0)

Projektordner neu übertragen (siehe 1.), dann auf dem Pi:

```bash
cd ~/sportstracker
PYTHONPATH=.:tests python3 -m unittest discover -s tests          # 540 Tests, offline
sudo bash deploy/install.sh
```

Das Skript prüft bzw. installiert `python3-flask`, `python3-lxml`, `python3-pil` und `librsvg2-bin`, legt `config/config.json` aus der RBBL-Vorlage an (falls es noch keine gibt), erzeugt die sechs systemd-Dienste (Daten, Anzeige, Sensor, Web, Sicherheit, MQTT), installiert `paho-mqtt` (für die Satelliten) und die sudo-Regel fürs Web-Backend und startet alles. Mehrfaches Ausführen ist unkritisch. Entfernen: `sudo bash deploy/install.sh --uninstall`.

Die Dienste:

| Dienst | Aufgabe |
|---|---|
| `sportstracker-data` | fragt die Liga-Quelle ab (an Spieltagen alle 60 s, sonst alle 15 min) und schreibt `data/state.json` |
| `sportstracker-display` | läuft als root, ist der einzige Zugriff auf die Matrix, zeichnet nur bei Änderungen, wechselt um Mitternacht selbst auf die Tabelle |
| `sportstracker-sensor` | liest den DHT22 (nur wenn im Backend aktiviert) und schreibt `data/sensor.json` |
| `sportstracker-web` | Web-Backend auf Port 8080 |
| `sportstracker-safety` | Temperaturschutz (siehe 5f), läuft als root |
| `sportstracker-mqtt` | empfängt MQTT-Meldungen der Satelliten (nur wenn im Backend aktiviert, siehe 5j) |

Web-Backend öffnen: `http://192.168.22.22:8080`. Anmeldung beim ersten Mal **admin / admin**. Das Kennwort muss nicht geändert werden; es lässt sich unter „Zugang" jederzeit ändern (mindestens 8 Zeichen). Das Backend hat kein HTTPS und ist nur für das Heimnetz gedacht.

Das Backend bietet: Übersicht mit Live-Vorschau der Matrix, Einstellungen (Liga, Lieblingsteam, Teamkürzel ja/nein, Spielminuten ja/nein, Tabellenoptionen, Abfrageintervalle, Zeitzone, Matrix-Parameter), System (Status, Dienste neu starten, Protokolle, Hostname, Neustart, Herunterfahren) und Zugang. Änderungen an der Matrix (Größe, Helligkeit, Mapping …) starten die Anzeige kurz neu, alle anderen gelten sofort.

Nützliche Befehle:

```bash
systemctl status sportstracker-data sportstracker-display sportstracker-sensor sportstracker-web sportstracker-safety sportstracker-mqtt
journalctl -u sportstracker-display -f                  # Protokoll live (Strg+C beendet)
sudo systemctl restart sportstracker-display
```

Wichtig: Die Testbefehle aus 5 und 5a (`hello_matrix.py`, `cli preview --show`) greifen auf dieselbe Matrix zu wie der Anzeigedienst. Vorher den Dienst anhalten (`sudo systemctl stop sportstracker-display`) und danach wieder starten.

Falls `scp` später mit „Permission denied" auf eine `.pyc`-Datei meldet: auf dem Pi `sudo find ~/sportstracker -name __pycache__ -type d -exec rm -rf {} +`. Das Installationsskript räumt das ebenfalls auf.

## 5c. Ein/Aus-Taster (optional)

Verdrahtung: Taster (Schließer, Taster ohne Rastung) zwischen **GPIO3 (Pin 5, SCL)** und **GND (Pin 6)**. Kein Widerstand nötig (der Pi hat an GPIO3 einen Pull-up).

```bash
cd ~/sportstracker
sudo bash deploy/power-button.sh      # trägt dtoverlay=gpio-shutdown in /boot/firmware/config.txt ein
sudo reboot
```

Funktion: Pi läuft -> Taster drücken -> sauberes Herunterfahren (die Dienste werden beendet, die Matrix wird dunkel). Pi heruntergefahren, Netzteil steckt -> Taster drücken -> Pi startet. Entfernen: `sudo bash deploy/power-button.sh --remove`.

## 5d. Pi Zero 2 W, Kamera und DHT22 (ab Version 0.5.0)

**Neuer Pi einrichten:** Setup-Anleitung (Projektdokument `claude/Setup-Anleitung`) wie beim Zero, danach Projekt übertragen und `sudo bash deploy/install.sh`. Der Zero 2 W ist deutlich schneller als der Zero: die Matrix flimmert dann eher, weil die GPIO-Signale zu schnell kommen. Im Backend unter „LED-Matrix" **GPIO-Slowdown** auf 3 oder 4 stellen (Anzeige startet kurz neu) und das Bild prüfen; den kleinsten Wert nehmen, bei dem es ruhig bleibt.

**Einzeltest vor der Inbetriebnahme:** `hardware_test.py` prüft Sensor und Kamera unabhängig von den Diensten (kein root nötig):

```bash
cd ~/sportstracker
sudo systemctl stop sportstracker-sensor              # nur falls der Sensordienst schon läuft (er belegt den GPIO)
~/venv/bin/python hardware_test.py dht                # 10 Messungen an GPIO25 (--gpio 19 für einen anderen Pin)
~/venv/bin/python hardware_test.py camera --out test.jpg
sudo systemctl start sportstracker-sensor
```

Die Ausgabe nennt jeweils, was fehlt (Bibliothek, Gruppe, Verkabelung, Kamera nicht erkannt).

**Kamera (IMX219-120):** Flachbandkabel in den CSI-Anschluss des Zero 2 W (schmaler 22-poliger Anschluss, nicht das breite 15-polige Kabel der großen Modelle; Pi vorher ausschalten, Kontakte zeigen zur Platine). Prüfen:

```bash
rpicam-hello --list-cameras        # soll den imx219 zeigen
rpicam-still -n -t 1500 -o test.jpg
```

**Wird der IMX219 nicht erkannt** (so war es beim ersten Zero 2 W, 28.10.2026: Modul „IMX219-120" eines Fremdherstellers, Bookworm 32-bit): Die Autoerkennung greift nicht, der Treiber muss von Hand geladen werden. In `/boot/firmware/config.txt` im Abschnitt `[all]` (am Dateiende) eintragen, danach neu starten:

```
camera_auto_detect=0
dtoverlay=imx219
```

Danach zeigt `rpicam-hello --list-cameras` den `imx219` (bestätigt). Erscheint er nicht: `dmesg | grep -i imx219` ansehen (Fehler wie `-121` = Kabel/Stecker), Kabel auf beiden Seiten neu stecken. `hardware_test.py camera` zeigt diese Diagnose automatisch.

**Kamera im Backend (ab 0.5.1):** Unter „Einstellungen“ gibt es nur noch den Schalter Kamera ein/aus. Der Menüpunkt „Kamera“ hat drei Seiten:

- **Foto:** Auflösung wählen (640×480 bis 3280×2464), „Foto aufnehmen und speichern“. Das Foto landet mit Datum und Uhrzeit im Dateinamen (`2026-10-30_13-00-00.jpg`) in der Galerie; die gewählte Auflösung merkt sich das Backend.
- **Live-Ansicht:** Auflösung (bis 1280×720) und Bilder pro Sekunde wählen, „Live-Ansicht starten“. Das ist ein MJPEG-Strom von `rpicam-vid`, ohne ffmpeg. Die Kamera läuft nur, solange die Seite offen ist (wenige Sekunden danach stoppt sie), spätestens nach 30 Minuten. „Aktuelles Bild in der Galerie speichern“ übernimmt das Live-Bild (mit Live-Auflösung). Die Live-Ansicht belastet die CPU: flimmert die Matrix dabei, kleinere Auflösung oder weniger Bilder wählen.
- **Galerie:** Vorschaubilder, Einzelansicht mit Blättern, Herunterladen und Löschen (mit Ankreuzfeld), „Galerie leeren“ und die Speichergrenze in MB (Standard 200). Ist sie erreicht, werden die ältesten Fotos automatisch gelöscht. Die Fotos liegen auf dem Pi unter `~/sportstracker/data/gallery/`.

Die Kamera wird nur von einem Vorgang gleichzeitig benutzt: Foto und Live-Ansicht stören sich nicht, ein Foto während der Live-Ansicht ist einfach das aktuelle Live-Bild. Fehlt `rpicam-hello`: `sudo apt install rpicam-apps`.

**DHT22 (AM2302 mit Platine):** nur mit **3,3 V** betreiben (nie 5 V, sonst liegt der Datenpegel zu hoch für den Pi). Die Platine hat den 10-kΩ-Pull-up schon.

| DHT22-Platine | Pi |
|---|---|
| `+` (VCC) | 3,3 V (Pin 1) |
| `OUT` (Daten) | GPIO25 (Pin 22) |
| `-` (GND) | GND (Pin 9) |

Die Pins 1, 9 und 22 liegen unter dem Bonnet: ob sie dort herausgeführt sind bzw. wie du sie abgreifst (Stapelbuchse, Lötpunkte), bitte am Bonnet prüfen. Alternativ im Backend einen anderen freien GPIO wählen (Auswahl: 25, 19, 14, 15, 2, 7, 8, 9, 10, 11; GPIO3 ist der Ein/Aus-Taster). Dann im Backend unter „Temperatursensor" den Haken setzen, GPIO wählen, speichern. Prüfen:

```bash
systemctl status sportstracker-sensor
journalctl -u sportstracker-sensor -f          # "measurement failed" gelegentlich ist beim DHT22 normal
cat ~/sportstracker/data/sensor.json
```

Der Sensor sitzt hinter dem warmen Panel und misst zu hoch. Besser abgesetzt anbringen; sonst mit einem Referenzthermometer vergleichen und die Differenz als Temperatur-Korrektur (z. B. -2,5) eintragen. Läuft der Sensordienst nicht, zeigt die Raumansicht Striche statt Werten. Seit 0.5.2 wird der DHT22 ohne Adafruit-Blinka gelesen: Standard ist das Abtasten des GPIO-Pegelregisters über `/dev/gpiomem` (Backend `mmap`, Gruppe `gpio` genügt); als Alternative gibt es die Flanken-Zeitstempel des Kernels über `gpiod` 2.x (`hardware_test.py dht --backend gpiod`, im Dienst per `SPORTSTRACKER_DHT_BACKEND=gpiod`); `adafruit-circuitpython-dht` wird nicht mehr gebraucht und darf nicht per Rückfrage „Y" nachinstalliert werden. Der Installer ergänzt den Benutzer außerdem in den Gruppen `gpio` und `video` (Sensor- und Kamerazugriff ohne root); nach der ersten Installation einmal neu starten.

**Neu in der Anzeige (0.5.0):** Mehrere Spiele der Liga (Hauptspiel, Halbzeit- und Ergebniskarten), spielfreie Zeit mit Tabelle und Raumansicht im Wechsel; alles im Backend unter „Spieltag-Anzeige" und „Spielfreie Zeit" einstellbar, die Übersicht zeigt die aktuelle „Anzeigeliste". Nachtabsenkung ist noch nicht enthalten.

## 5e. Nachricht (ab Version 0.5.3)

Im Web-Backend gibt es oben die Seite **Nachricht**: ein Text mit Farbe, Schrift (14 Schriften von winzig bis 10x20), Intensität, Tempo und einer von sechs Animationen (Lauftext, Blinken, Pulsieren, Regenbogen, Welle, Schreibmaschine) oder stehend. Die Vorschau zeigt die Animation als GIF; „Vorschau aktualisieren“ zeigt die eingegebenen Werte, ohne zu speichern.

* **Nachricht aktiv** schaltet sie ein. Ohne weitere Haken läuft sie in der Anzeigeliste im Wechsel mit Tabelle, Raumansicht und Spielankündigung (nicht, solange ein Spiel läuft), je „Anzeigedauer im Wechsel“ Sekunden. Ein Lauftext bekommt mindestens die Zeit für einen Durchlauf.
* **Permanent anzeigen**: nur die Nachricht, alles andere ruht, auch bei laufenden Spielen.
* **Im Wechsel auch zwischen laufenden Spielen zeigen**: die Nachricht kommt dann auch zwischen Hauptspiel und Ergebniskarten dran.
* Passt der Text nicht auf die Matrix, wählt die Anzeige automatisch eine kleinere Schrift. Während einer Animation zeichnet der Anzeigedienst 15 Bilder pro Sekunde (sonst nur bei Änderung); das belastet die CPU etwas mehr.

Neue Dateien: `sportstracker/banner.py`, `sportstracker/templates/message.html`, die Schriften in `fonts/`. Nach dem Übertragen `sportstracker-web` und `sportstracker-display` neu starten.

## 5f. Sicherheits-Shutdown (ab Version 0.5.4)

Der Dienst `sportstracker-safety` fährt den Pi sauber herunter, wenn die CPU-Temperatur dreimal hintereinander (ca. 10 s) die eingestellte Grenze erreicht. Vorgabe: **84 °C**; einstellbar im Backend unter **Einstellungen → Sicherheit (Temperaturschutz)** (60 bis 85 °C, Haken zum Ein-/Ausschalten). Die Seite **System** zeigt Status, aktuelle CPU-Temperatur und die letzte Abschaltung; die Übersicht warnt 72 Stunden lang nach einer Abschaltung.

* Der Pi drosselt selbst ab ca. 80 °C und begrenzt hart bei 85 °C. Die Abschaltung bei 84 °C ist die letzte Sicherung, nicht der Normalbetrieb – bei regelmäßigem Erreichen: Kühlung/Lüftung im Gehäuse verbessern.
* Nach einer Abschaltung bleibt der Pi Zero 2 W aus, bis die Stromversorgung kurz getrennt wird. Startet er noch heiß, schaltet er nach wenigen Sekunden wieder ab: erst abkühlen lassen.
* Der Dienst läuft unabhängig vom Web-Backend (auch wenn dieses hängt) und schreibt nur bei einer Abschaltung auf die Karte.
* Test ohne Hitze: Grenze auf 45 °C stellen (Pi liegt im Betrieb meist darüber) – er fährt dann ca. 10 s später herunter. Danach Grenze zurück auf 84 °C und Strom trennen/anstecken.

Neue Dateien: `sportstracker/safety.py`, `sportstracker/service_safety.py`, `deploy/sportstracker-safety.service`. Installation: `sudo ./deploy/install.sh` (legt den Dienst an, ergänzt die sudo-Regel).

## 5g. Bild-Galerie (ab Version 0.5.5, Galerie ab 0.6.0)

Im Web-Backend gibt es oben die Seite **Bild**: bis zu **10 Bilder** (JPEG, PNG, GIF, WebP, je bis **10 MB**) hochladen; mehrere Dateien auf einmal sind möglich. Die Galerie ersetzt das frühere Einzelbild; ein vorhandenes `data/picture.png` wird beim ersten Start automatisch als erstes Galeriebild übernommen.

* **Pro Bild**: Anzeigedauer in Sekunden (3 bis 120), Reihenfolge (hoch/runter) und Löschen. Die Bilder laufen nacheinander in einer Schleife.
* **Übergang** zwischen den Bildern: *Überblenden*, *Schieben*, *Wischen* oder *Zufällig*; dazu die Dauer des Übergangs (0,3 bis 2 s). Während eines Übergangs zeichnet die Anzeige mit 15 Bildern pro Sekunde, sonst nur bei Änderung.
* **Anpassung**: *Ausfüllen* schneidet die Mitte quadratisch zu, *Einpassen* zeigt das ganze Bild mit schwarzen Rändern, *Strecken* verzerrt es auf die ganze Matrix. **Helligkeit (%)** dunkelt ab. Gilt für alle Bilder.
* **Bild aktiv** nimmt die Galerie in die Anzeigeliste auf (Tabelle, Raumansicht, Satelliten, Spielankündigung, Nachricht, Bild); die Zeit richtet sich nach der Summe der Bilddauern; nicht, solange ein Spiel läuft. **Permanent anzeigen**: nur die Galerie, auch bei laufenden Spielen. Eine permanente Nachricht hat Vorrang vor einem permanenten Bild.
* Animierte GIFs laufen in ihrem eigenen Tempo, solange ihr Bild an der Reihe ist. Die Vorschau (GIF) zeigt Übergang und Ablauf verkleinert.
* Uploads werden auf dem Pi auf höchstens 128 Pixel Kantenlänge verkleinert unter `data/pictures/` gespeichert (`index.json` enthält die Reihenfolge). Kräftige Farben und einfache Motive wirken am besten.

Neue Dateien: `sportstracker/picture.py`, `sportstracker/templates/picture.html`. Nach dem Übertragen `sportstracker-web` und `sportstracker-display` neu starten. Noch nicht auf echter Hardware geprüft: Ruckelfreiheit der Übergänge und CPU-Last des Pi Zero 2 W bei 15 Bildern pro Sekunde. Falls es ruckelt: kürzere/keine Übergänge oder kleinere Anzeigedauer-Sprünge verwenden und `top` beobachten.

## 5h. Livingmodus ohne Tabelle (ab Version 0.5.6)

Unter **Einstellungen → Spielfreie Zeit** gibt es den Haken **Livingmodus ohne Tabelle**. Gesetzt, zeigt die Anzeige außerhalb von Spieltagen keine Tabelle mehr, sondern Raumansicht (Uhrzeit, Temperatur, Luftfeuchte) sowie – falls aktiv – Spielankündigung, Nachricht und Bild. Die Auswahl „Nur Tabelle“ wird dann wie „Nur Raumansicht“ behandelt. Laufende Spiele, Ergebniskarten und der Endstand des Lieblingsteams bleiben unverändert. Nur `sportstracker-web` und `sportstracker-display` neu starten.

## 5i. Kamera: Drehen, Helligkeit, Schärfe, Bewegungsmelder (ab Version 0.5.7)

Auf den Seiten **Foto** und **Live-Ansicht** gibt es den Abschnitt **Bild**:

- **Horizontal / Vertikal spiegeln:** Beide Haken an = Bild um 180° gedreht. Das ist der Standard (Kamera im Gehäuse steht auf dem Kopf). Nur „Horizontal“ = Spiegelbild. Gilt für Fotos und Live-Ansicht.
- **Helligkeit:** -50 % bis +50 % (0 = unverändert). **Schärfe:** 0 (aus) bis 4, Standard 1.
- Nach „Bild-Einstellungen übernehmen“ startet die laufende Live-Ansicht mit den neuen Werten neu.

Auf der Seite **Live-Ansicht** gibt es den Abschnitt **Bewegungsmelder** (Standard: aus):

- **Empfindlichkeit** 1 bis 5 und **Pause zwischen zwei Fotos** (10 Sekunden bis 10 Minuten).
- Bei erkannter Bewegung wird das aktuelle Live-Bild automatisch in der Galerie gespeichert (Live-Auflösung, Dateiname mit Zeitstempel). Die Speichergrenze der Galerie gilt weiter.
- Der Bewegungsmelder arbeitet nur, solange die Live-Ansicht läuft (höchstens 30 Minuten, danach „Live-Ansicht starten“ erneut). Der Link **Bewegungsstatus** unter dem Live-Bild öffnet eine Seite, die alle 3 Sekunden zeigt, wie stark sich das Bild gerade verändert (in Prozent, mit Schwelle), wie viele Fotos gemacht wurden und wann zuletzt. Damit lässt sich die Empfindlichkeit einstellen: im ruhigen Bild soll der Wert deutlich unter der Schwelle liegen.
- Die ersten Sekunden nach dem Start löst der Melder nicht aus (Belichtung stellt sich ein).

Nach dem Übertragen nur `sportstracker-web` neu starten. Bestehende `config.json` braucht keine Änderung (fehlende Werte werden mit den Standards ergänzt; Standard ist 180° gedreht).

## 5j. Satelliten: Temperatur und Luftqualität aus dem Heimnetz (ab Version 0.6.0)

Bis zu **3 Satelliten** (ESP32 mit DHT22 und MQ-135) liefern Temperatur, Luftfeuchte, Gas-Rohwert und einen Luftindex. Die Anzeige zeigt im Leerlauf (Livingmodus) **eine Matrixseite je Satellit** (Name, Temperatur, Feuchte, Luftqualität GUT/MITTEL/SCHLECHT mit Balken), nach der Raumansicht des Pi.

**Einrichten im Backend (Seite „Satelliten“):**

1. *Satelliten anzeigen* aktivieren, Anzeigedauer je Seite einstellen (Standard 8 s).
2. Je Satellit: *aktiv*, **Name** (bis 10 Zeichen, daraus wird der Schlüssel abgeleitet, z. B. „Kueche“ -> `kueche`), Zugangs-**Token** (wird automatisch erzeugt) und die Gas-Kalibrierung: `gas_clean` (Rohwert bei guter Luft, Standard 800) und `gas_bad` (Rohwert bei schlechter Luft, Standard 2500). Der Index ist linear dazwischen: ab 70 GUT, ab 40 MITTEL, sonst SCHLECHT. Werte, die älter als 10 Minuten sind, erscheinen als Striche.
3. Speichern. Der Token steht danach im ESP32-Sketch (`esp32/sportstracker_satellite/`).

**Übertragungsweg A: HTTP (einfach).** Der ESP32 sendet alle 30 s per `POST http://192.168.22.22:8080/api/satellite/<schluessel>` mit Kopf `Authorization: Bearer <token>` (oder `X-Token`) und JSON `{"temperature":21.5,"humidity":48,"gas":900,"air_index":78}`. Test vom PC:

```powershell
curl.exe -X POST http://192.168.22.22:8080/api/satellite/kueche -H "Authorization: Bearer DEIN_TOKEN" -H "Content-Type: application/json" -d "{\"temperature\":21.5,\"humidity\":48,\"gas\":900}"
```

**Übertragungsweg B: MQTT (Mosquitto, z. B. auf dem Home-Assistant-Raspi).** Auf der Seite „Satelliten“ unter *MQTT*: Host, Port (1883), optional Benutzer/Kennwort und Basis-Topic (Standard `sportstracker`). Der Dienst `sportstracker-mqtt` abonniert `<basis_topic>/<schluessel>/state` (oder ein eigenes Topic je Satellit) und schreibt die Werte wie der HTTP-Weg. Bei Änderung der MQTT-Daten verbindet er sich selbst neu. Test:

```bash
mosquitto_pub -h <HA-IP> -t sportstracker/kueche/state -m '{"temperature":21.5,"humidity":48,"gas":900}'
journalctl -u sportstracker-mqtt -n 50
```

Der Sketch meldet sich außerdem per **Home-Assistant-MQTT-Discovery** an, sodass die Sensoren dort automatisch erscheinen. Verkabelung (DHT22 an GPIO4, MQ-135 an GPIO34 über Spannungsteiler 10k/20k), Einrichtung und MQ-135-Hinweise (3 Minuten Aufwärmzeit, Kalibrierung) stehen in `esp32/README.md`.

**Einmalig auf dem Pi** (neuer Dienst und neue sudo-Regel):

```bash
cd ~/sportstracker
sudo bash deploy/install.sh
sudo systemctl restart sportstracker-web sportstracker-display
```

Neue Dateien: `sportstracker/satellites.py`, `sportstracker/service_mqtt.py`, `sportstracker/templates/satellites.html`, `deploy/sportstracker-mqtt.service`, `esp32/`. Werte landen in `data/satellites.json`. **Nicht auf echter Hardware geprüft:** Sketch-Kompilierung, echter MQTT-Broker, MQ-135-Kalibrierung.

## 5k. Spieltag-Rotation und späte RBBL-Ergebnisse (ab Version 0.6.1/0.6.2)

* **Alle Ergebnisse des Tages**: Unter **Einstellungen → Spieltag-Anzeige** gibt es den Haken „Alle Ergebnisse des Spieltags bis Mitternacht zeigen“ (Standard an). Beendete Spiele des Tages bleiben bis Mitternacht als Ergebniskarten stehen (Lieblingsteam zuerst). Sobald kein Spiel mehr läuft, wechseln Ergebnisse und Tabelle (dazu Raumansicht je Auswahl „Spielfreie Zeit“, Nachricht, Bild). Während ein Spiel läuft, bleibt es im Vordergrund, die Ergebnisse wechseln mit ihm. Ohne Haken: wie früher (Karte 30 Minuten, Endstand des Lieblingsteams bis Mitternacht).
* **Späte RBBL-Ergebnisse**: Genius liefert bei der RBBL oft keinen Live-Stand; die Ergebnisse stehen erst nach dem Spiel im Spielplan. Der Spielplan wird deshalb von 10 Minuten vor Beginn bis 5 Stunden danach jede Minute geladen, sonst alle 10 Minuten. Ein Platzhalter „0:0“ wird nicht mehr angezeigt, solange kein Stand vorliegt (Anzeige „-:-“).
* Nur `sportstracker-web`, `sportstracker-data` und `sportstracker-display` neu starten (kein `install.sh` nötig).

## 5l. Designs und WLAN-Setup (ab Version 0.7.0)

Einmalig nach dem Übertragen: `cd ~/sportstracker && sudo ./deploy/install.sh` (legt den siebten Dienst `sportstracker-wifi` an, installiert `python3-qrcode`, ergänzt die sudo-Regel).

**Designs:** Backend → Einstellungen → Spielfreie Zeit: „Design der Raumansicht“ (C Klima im Fokus, A Klassisch, B Analoguhr) und „Design der Satellitenseiten“ (B Halbkreis, A Liste, C Luftindex groß). Die Vorschau auf der Übersicht zeigt das Ergebnis sofort.

**WLAN-Setup:** Backend → WLAN. Dort **zuerst PIN und Hotspot-Kennwort ändern** (Voreinstellung 1234 / setup1234). Prüfen: `nmcli -t -f RUNNING general` muss `running` zeigen, `journalctl -u sportstracker-wifi -f` zeigt den Ablauf.

Wenn sich das WLAN-Kennwort geändert hat und der Pi nicht mehr erreichbar ist:
1. Nach etwa 3 Minuten zeigt die Matrix einen QR-Code. Mit der Handykamera scannen und mit `Sportstracker-Setup` verbinden (oder von Hand mit dem Kennwort verbinden).
2. Im Handy-Browser `http://10.42.0.1:8080/wifi/setup` öffnen (zweiter QR-Code), PIN eingeben.
3. Das eigene Netz wählen (oder bei einem versteckten Netz den Namen eintragen), neues Kennwort eingeben, „Verbinden“. Das Handy verliert dabei den Hotspot; nach etwa 20 Sekunden zeigt die Matrix wieder die normale Anzeige.
4. Falsches Kennwort: der Hotspot erscheint nach etwa einer Minute wieder, die Setup-Seite zeigt „Letzter Versuch: …“.

Test ohne Ausfall: Backend → WLAN → Wartezeit auf 60 s stellen, im Router das WLAN-Kennwort des Pi kurz sperren oder `sudo nmcli device disconnect wlan0` ausführen; nach Ablauf muss der Hotspot erscheinen. Danach Wartezeit zurück auf 180 s.

Nicht auf echter Hardware geprüft: Hotspot auf dem Pi Zero 2 W (Dnsmasq im NetworkManager), Lesbarkeit der QR-Codes auf dem Panel (Helligkeit, Abstand 30 bis 50 cm), Verhalten bei verstecktem WLAN.

## 6. Dateien vom Pi zurückholen (falls nötig)

```powershell
scp pi@192.168.22.22:~/sportstracker/config/config.json .\sportstracker\config\config.json
```

## Hinweise

- Beim ersten Verbinden fragt SSH nach dem Host-Fingerprint: mit `yes` bestätigen.
- Passwortfrei per Schlüssel (optional, einmalig in PowerShell):
  ```powershell
  ssh-keygen -t ed25519
  type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@192.168.22.22 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
  ```
- Wenn `scp` mit „No route to host" oder Timeout antwortet: IP prüfen (`ping 192.168.22.22`), WLAN des Pi und die feste IP im Router (192.168.22.22).
- Der Pi braucht für das Verzeichnis `~/venv` die Python-Bindings aus der Setup-Anleitung (Projektdokument `claude/Setup-Anleitung`, Schritt 4).
