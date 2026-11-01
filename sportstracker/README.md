# Sportstracker+ (Stand 0.7.0: Datenschicht, Renderer, sieben Dienste, Web-Backend, Raumklima, Kamera, Bild-Galerie, Satelliten, wählbare Designs, WLAN-Setup)

Ligadaten auf einer RGB-LED-Matrix: laufende Spiele der Liga, Ergebniskarten, sonst Tabelle und Raumansicht. Sieben Dienste (Daten, Anzeige, Sensor, Web, Sicherheit, MQTT, WLAN) laufen unter systemd; Installation siehe Abschnitt „Dauerbetrieb".

## Provider

| Provider | Liga | Konfiguration |
|---|---|---|
| `openligadb` | DEL (Test: 2026/2027) | `config/config.example.json` |
| `genius_rbbl` | RBBL 1 (49807), RBBL 2 Div. A (49808) | `config/config.rbbl.example.json` |

Favorit-IDs: DEL Eisbären Berlin = `641`; RBBL RB Zwickau = `36365`.
Teams einer Liga anzeigen: `python3 -m sportstracker.cli teams --config config/config.json`.

## Installation auf dem Pi (Bookworm Lite)

```bash
sudo apt update && sudo apt install -y python3-lxml
cd ~/sportstracker
python3 -m venv --system-site-packages .venv && . .venv/bin/activate
PYTHONPATH=.:tests python3 -m unittest discover -s tests     # 520 Tests, offline
cp config/config.example.json config/config.json             # oder config.rbbl.example.json
python3 -m sportstracker.cli check --config config/config.json
```

`check` zeigt Teams, Tabelle (Favorit mit `*`) und das Spiel des Tages. Mit `--now 2026-10-30T12:00:00+01:00` lässt sich ein anderer Zeitpunkt simulieren.

## Anzeigeregel (ab 0.5.0)

Hauptspiel:

1. Spielt das Lieblingsteam (Spiel läuft), ist es immer das Hauptspiel.
2. Sonst ist das laufende Spiel der Liga mit dem bestplatzierten Team das Hauptspiel (Platzierung = bessere Tabellenposition der beiden Teams; bei Gleichstand kleinere Platzsumme, dann früherer Beginn).

Karten für die übrigen Spiele: während der Halbzeitpause (RBBL: Pause nach Q2, höchstens `halftime_max_minutes`) und `result_window_minutes` (30) nach Spielende. Hauptspiel und Karten wechseln sich ab (`rotation_main_seconds`, `rotation_card_seconds`, je 10 s). Ist das Lieblingsteam fertig, während ein anderes Spiel läuft, läuft das andere als Hauptspiel und der Endstand des Lieblingsteams ist 30 Minuten eine Karte.

Läuft nichts und gibt es keine Karte: Endstand des Lieblingsteams bis Mitternacht; sonst Wechsel aus Ankündigung (nur wenn das Lieblingsteam heute noch spielt), Tabelle und Raumansicht (`idle_mode`: `alternate`, `table`, `room`; je `idle_*_seconds`). Die Reihenfolge wird aus der Uhrzeit berechnet, der Anzeigedienst braucht dafür keinen Zustand.

Die Quellen liefern keine Endzeiten. Der Datendienst merkt sich deshalb pro Spiel, wann er es erstmals live, in der Halbzeit und beendet gesehen hat (`state.json`, Schlüssel `marks`). Nach einem Neustart des Dienstes beginnt ein bereits beendetes Spiel nicht von vorn (Karte nur, wenn der Spielbeginn weniger als 5 Stunden zurückliegt). Quellen ohne Spielliste der ganzen Liga (derzeit OpenLigaDB) fallen auf die alte Regel zurück: nur das Spiel des Lieblingsteams.

## Raumklima und Kamera (0.5.0)

`service_sensor.py` liest alle `sensor.interval_seconds` (30) den DHT22 (bis zu drei Versuche je Messung, Plausibilitätsprüfung, Ausreißer-Filter, Korrekturwerte `temp_offset`/`humidity_offset`) und schreibt `data/sensor.json`. Die Raumansicht zeigt große Uhrzeit, Temperatur und Luftfeuchte; Werte, die älter als 5 Minuten sind, erscheinen als Striche. Der DHT22 wird seit 0.5.2 ohne Adafruit-Blinka gelesen (`dht_mmap.py`: Abtasten des GPIO-Pegelregisters über `/dev/gpiomem`; Alternative `dht_native.py` mit Flanken-Zeitstempeln des Kernels über `gpiod` 2.x). Beides wird erst bei der ersten Messung geladen.

`camera.py`: Fotos mit `rpicam-still` (Rückfall `libcamera-still`) und Live-Ansicht als MJPEG-Strom von `rpicam-vid` (ein Prozess für alle Zuschauer, startet beim ersten, stoppt kurz nach dem letzten, höchstens 30 Minuten). `gallery.py`: Fotos mit Zeitstempel im Dateinamen unter `data/gallery/`, Speichergrenze in MB (älteste werden gelöscht), Vorschaubilder. Seiten unter `/camera` (Foto), `/camera/live`, `/camera/gallery`, alles nur nach Anmeldung. Config `camera`: `enabled`, `width`/`height` (Foto), `live_width`/`live_height`/`live_fps`, `gallery_limit_mb`.

## Kamera: Drehen, Helligkeit, Schärfe, Bewegungsmelder (0.5.7)

`camera.ImageOptions` reicht `--hflip`, `--vflip`, `--brightness` und `--sharpness` an `rpicam-still` und `rpicam-vid` weiter (Foto und Live-Ansicht, Abschnitt „Bild“ auf beiden Seiten; Änderung der Live-Einstellungen startet den Kameraprozess neu). `motion.py` ist der Bewegungsmelder der Live-Ansicht: höchstens vier Bilder pro Sekunde werden mit `Image.draft` klein dekodiert, grau gerechnet und gegen ein langsam nachgeführtes Referenzbild verglichen (nur Pillow, kein numpy; rund 1 ms je Bild auf einem PC). Fotos bei Bewegung speichert `webapp.motion_photo` in die Galerie (Live-Auflösung, Mindestpause, Speichergrenze gilt). Config `camera`: `hflip`/`vflip` (Standard beide `true` = 180°), `brightness` (-50 bis 50 %), `sharpness` (0 bis 4, 1 = Standard), `motion_enabled`, `motion_sensitivity` (1 bis 5), `motion_pause` (Sekunden). Seiten: `/camera/image/settings`, `/camera/live/motion/settings`, `/camera/live/motion` (Statusseite, lädt alle 3 s neu).

## Nachricht (0.5.3)

Seite „Nachricht“ im Backend: Text, Farbe, Schrift, Intensität, Tempo und eine von sechs Animationen (`banner.py`: Lauftext, Blinken, Pulsieren, Regenbogen, Welle, Schreibmaschine). Die Nachricht läuft in der Anzeigeliste mit; „permanent“ zeigt ausschließlich sie, auch bei laufenden Spielen. Konfiguration im Abschnitt `message`.

## Spieltag-Rotation (0.6.2)

Config `gameday_results` (Standard `true`), im Backend unter Einstellungen → „Spieltag-Anzeige“. Alle beendeten Spiele des lokalen Tages (mit Ergebnis) bleiben bis Mitternacht als Karten in der Anzeige, das Lieblingsteam zuerst, dann nach Tabellenplatz. Läuft kein Spiel mehr, wechseln Karten, Tabelle (auch bei „Livingmodus ohne Tabelle“, das gilt nur außerhalb von Spieltagen), eine Ankündigung des späteren Lieblingsspiels, die Raumansicht (je `idle_mode`), Nachricht und Bild. Läuft ein Spiel, bleibt es im Vordergrund, die Ergebnisse wechseln mit ihm, die Tabelle kommt nicht dazu. Aus: altes Verhalten (Karten nur 30 Minuten, Endstand des Lieblingsteams bis Mitternacht). Dazu lädt der RBBL-Provider den Spielplan standardmäßig alle 10 Minuten (`schedule_ttl` 600), damit spät eingetragene Ergebnisse spätestens nach einem Abfragezyklus (15 Minuten) erscheinen.

## RBBL: nachträglich eingetragene Ergebnisse (0.6.1)

Bei der RBBL liefert Genius oft keinen Live-Stand; die Ergebnisse stehen erst nach dem Spiel im Spielplan. In der Live-Datei bleiben dabei Einträge mit Status `READY` und 0:0 stehen. Seit 0.6.1 überschreibt so ein Platzhalter keinen Stand mehr, ein laufendes Spiel mit 0:0 im Spielplan zeigt „-:-“, und der Spielplan wird, solange ein Spiel angesetzt, laufend oder ohne Endstand ist (ab 10 Minuten vor Beginn bis 5 Stunden danach), jede Minute statt alle 15 Minuten geladen (`busy_schedule_ttl`, Standard 60 s).

## Livingmodus ohne Tabelle (0.5.6)

Config `idle_hide_table` (Standard `false`), im Backend unter Einstellungen → „Spielfreie Zeit“: Ist der Haken gesetzt, fehlt die Tabelle in der spielfreien Anzeigeliste; es bleiben Spielankündigung, Raumansicht, Nachricht und Bild. „Nur Tabelle“ zeigt dann die Raumansicht. An Spieltagen ändert sich nichts.

## Bild-Galerie (0.5.5, Galerie ab 0.6.0)

Seite „Bild“ im Backend: bis zu 10 Bilder (JPEG, PNG, GIF, WebP, je bis 10 MB; Anfragen bis 50 MB, also mehrere Dateien auf einmal). `picture.py` wandelt jeden Upload einmalig um (`convert_upload`: EXIF-Drehung, Transparenz auf Schwarz, höchstens 128 Pixel Kantenlänge, bis 300 GIF-Bilder) und `PictureStore` verwaltet `data/pictures/<id>.png` mit der Reihenfolge und der Anzeigedauer je Bild (3 bis 120 s) in `index.json`; ein altes `data/picture.png` wird beim ersten Zugriff als erstes Galeriebild übernommen. `PictureRenderer` passt an die Matrix an (`fill`, `fit`, `stretch`, Helligkeit) und rechnet die Übergänge zwischen zwei Bildern (`fade` Überblenden, `slide` Schieben, `wipe` Wischen, `random`; Dauer `transition_ms` 300 bis 2000). Übergänge und animierte GIFs zeichnet der Anzeigedienst mit 15 Bildern pro Sekunde (`needs_fast_redraw`), sonst nur bei Änderung (`state_key`). Die Galerie läuft in der Anzeigeliste mit (Dauer = Summe der Bilddauern, nicht während laufender Spiele) oder „permanent“ allein; eine permanente Nachricht hat Vorrang. Vorschau als GIF unter `/picture/preview.gif`. Konfiguration im Abschnitt `picture` (neu: `transition`, `transition_ms`).

## Satelliten: Raumklima aus dem Heimnetz (0.6.0)

Bis zu drei ESP32-Satelliten (DHT22 + MQ-135, Sketch unter `esp32/`, Anleitung `esp32/README.md`) liefern Temperatur, Feuchte, Gas-Rohwert und Luftindex. Zwei Wege: HTTP `POST /api/satellite/<schluessel>` (Token per `Authorization: Bearer` oder `X-Token`, ohne Anmeldung/CSRF, höchstens 2 KB) und MQTT (`service_mqtt.py`, Dienst `sportstracker-mqtt`, `paho-mqtt` 1.x/2.x, Topic `<base_topic>/<schluessel>/state` oder eigenes Topic je Satellit; der Sketch meldet sich zusätzlich per Home-Assistant-Discovery an). `satellites.py` prüft die Nutzdaten (`parse_payload`), berechnet den Luftindex aus dem Gas-Rohwert (`air_quality`: linear zwischen `gas_clean` 800 und `gas_bad` 2500; ab 70 GUT, ab 40 MITTEL, sonst SCHLECHT) und schreibt `data/satellites.json` (`store_reading`, Dateisperre, atomar, fehlende Werte bleiben erhalten). Werte älter als 600 s gelten als veraltet (Striche). `render.render_satellite` zeichnet je Satellit eine Matrixseite; `views.plan` hängt sie nach der Raumansicht an die Leerlaufliste. Backend-Seite „Satelliten“ (`/satellites`, Token wird automatisch erzeugt, MQTT-Kennwort bleibt bei leerem Feld erhalten). Konfiguration im Abschnitt `satellites` (`enabled`, `seconds`, `mqtt{enabled,host,port,username,password,base_topic}`, `items[{enabled,key,name,token,topic,gas_clean,gas_bad}]`).

## Designs der Raumansicht und der Satellitenseiten (0.7.0)

Einstellungen → Spielfreie Zeit: `room_design` (`c` Klima im Fokus = Standard, `a` Klassisch mit großer Digitaluhr, `b` Analoguhr) und `satellite_design` (`b` Halbkreis-Anzeige = Standard, `a` Liste mit Segment-Balken, `c` Luftindex groß). Die Zeichenfunktionen stehen in `roomviews.py` (nur für 64x64; andere Panelgrößen behalten das schlichte Layout). Design C zeigt einen Komfortpunkt (grün/gelb/rot) und einen Hinweis (KOMFORT, ZU FEUCHT, ZU TROCKEN, ZU WARM, ZU KÜHL), Bereich 20–24 °C und 40–60 % Luftfeuchte. Ohne frischen Messwert erscheinen Striche. Der Luftindex des MQ-135 ist ein relativer Wert (0–100), keine ppm.

## WLAN-Setup: Hotspot, wenn das WLAN fehlt (0.7.0)

Neuer Dienst `sportstracker-wifi` (root, NetworkManager/`nmcli`, Bookworm-Standard). Ist der Pi `wifi.grace_seconds` (Standard 180 s) ohne WLAN, öffnet er den Hotspot `Sportstracker-Setup` (Kennwort `setup1234`, WPA2 verlangt mindestens 8 Zeichen). Die Matrix zeigt abwechselnd (je 6 s) einen QR-Code zum Verbinden, einen QR-Code der Setup-Seite und den Text mit Adresse. Am Handy `http://10.42.0.1:8080/wifi/setup` öffnen, PIN (Standard `1234`) eingeben, Netz wählen, Kennwort eintragen. Danach schließt der Hotspot; klappt die Verbindung nicht, kommt er zurück und die Seite nennt den Grund. Ohne Bedienung sucht der Dienst alle 10 Minuten nach dem alten WLAN (nur wenn niemand mit dem Hotspot verbunden ist).

- Im Web-Backend gibt es die Seite **WLAN**: Status, Netz wechseln oder Kennwort ändern (auch im laufenden Betrieb), Hotspot-Name, Hotspot-Kennwort, PIN und Wartezeit. **PIN und Hotspot-Kennwort ändern: die Voreinstellungen stehen im öffentlichen Quelltext.**
- Das Web-Backend bekommt keine Netzwerk-Rechte: es schreibt eine Anfrage (`data/wifi_request.json`, nur für den Benutzer lesbar), der Dienst führt sie aus und löscht sie. Status: `data/wifi.json`.
- Die Setup-Seite gibt es nur, solange der Hotspot aktiv ist; falsche PIN-Eingaben werden gebremst (5 Fehlversuche, dann 60 s Pause je Gerät).
- Grenzen: Der Pi Zero 2 W hat ein Funkmodul; solange der Hotspot offen ist, sind WLAN, Satelliten und Home Assistant nicht erreichbar. QR-Codes brauchen `python3-qrcode` (der Installer versucht es, sonst nur Text).
- Zusätzliche Absicherung ohne Code: Zweitnetz (z. B. Handy-Hotspot) mit niedrigerer Priorität speichern: `sudo nmcli connection modify "<Name>" connection.autoconnect-priority 5`.

## Temperaturschutz (0.5.4)

`service_safety.py` (eigener Dienst `sportstracker-safety`, läuft als root) liest alle 5 s die CPU-Temperatur (`/sys/class/thermal/thermal_zone0/temp`). Liegt sie dreimal hintereinander bei oder über `safety.shutdown_temp_c` (Standard 84 °C, erlaubt 60–85), schreibt er `data/safety.json` (Zeitpunkt, Temperatur, Grenze) und führt `systemctl poweroff` aus. Ein defekter oder fehlender Konfigurationsstand fällt auf die Vorgabe (aktiv, 84 °C) zurück; nur `"enabled": false` schaltet den Schutz ab. Im Backend: Abschnitt „Sicherheit“ unter Einstellungen, Block „Temperaturschutz“ auf der Seite System, Warnung auf der Übersicht für 72 Stunden nach einer Abschaltung. Konfiguration im Abschnitt `safety`.

## Noch nicht live verifiziert (bitte auf dem Pi prüfen)

- OpenLigaDB: Verzögerung der Ergebnisse, ob laufende Spiele über `getlastmatch`/`getnextmatch` kommen.
- Genius (RBBL): Zeitzone der Spielzeiten (angenommen Europe/Berlin), Format des Live-JSON bei einem echten laufenden Spiel, ob ein einfacher GET vom Pi durchgelassen wird. Falls blockiert: Relay auf rbzwickau.de (Domain-Freigabe in der Genius-Konfiguration).
- Nutzung der Daten: Bestätigung durch den Verband (DRS) einholen.

## Bild-Rendering (neu in 0.2.0)

Zusätzlich zu den Providern: `logos.py` (Logo-Download, SVG/PNG/JPEG, Cache, Override `logos/override/<team_id>.png`), `render.py` (Spielbild und Tabelle als Pillow-Bild), `bdf.py` (Bitmap-Schriften aus `fonts/`), `display.py` (Ausgabe an die Matrix).

```bash
sudo apt install -y librsvg2-bin                                   # nötig für SVG-Logos (DEL)
python3 -m sportstracker.cli demo --out demo.png                   # Demo-Bilder ohne Netzwerk
python3 -m sportstracker.cli bench --runs 50                       # Renderzeit pro Bild
python3 -m sportstracker.cli preview --config config/config.json --out vorschau.png      # echte Daten, als Datei
sudo ~/venv/bin/python -m sportstracker.cli preview --config config/config.json --show   # auf der Matrix
```

Weitere Config-Schlüssel (optional): `cache_dir`, `fonts_dir`, `logo_override_dir`, `team_labels` (z. B. `{"36365": "RBZ"}` für eigene Kürzel), `logo_backing` (`auto` = helle Scheibe hinter dunklen Logos, `off`, `always`), `logo_backing_teams` (z. B. `{"36911": "always"}`), `table_logo_size` (Standard 6; 0 = keine Logos in der Tabelle, sonst bis 16 Pixel), `table_label` (`city` = Städtename, `short` = Kürzel), `team_cities` (eigene Städtenamen je Team, z. B. `{"36911": "Münsterland"}`). Relative Pfade gelten ab dem Projektordner.

## Dauerbetrieb (ab 0.3.0, Sensordienst ab 0.5.0)

```text
sportstracker-data     -> pollt die Quelle, schreibt data/state.json (atomar)
sportstracker-display  -> liest state.json + sensor.json, einziger Matrix-Zugriff (root), zeichnet nur bei Änderung
sportstracker-sensor   -> liest den DHT22, schreibt data/sensor.json (atomar)
sportstracker-web      -> Flask-Backend, Port 8080, bearbeitet nur config/config.json
sportstracker-safety   -> Temperaturschutz (root), schreibt data/safety.json
sportstracker-mqtt     -> MQTT-Brücke für die Satelliten, schreibt data/satellites.json
```

Die Dienste sind nur über Dateien gekoppelt (`config/config.json`, `data/state.json`) und beobachten deren Änderungszeit. Matrix-Einstellungen gelten erst beim Start der Anzeige: der Anzeigedienst beendet sich bei Änderung, systemd startet ihn neu.

Abfragerhythmus: an Spieltagen (Spiel angesetzt oder live) alle `poll_game_seconds` (60), sonst alle `poll_idle_seconds` (900); nach Mitternacht (lokale Zeit) wird sofort neu abgefragt. Ist die letzte Abfrage fehlgeschlagen oder zu alt, zeigt die Matrix einen schwachen roten Punkt oben rechts.

Installation auf dem Pi: `sudo bash deploy/install.sh` (Web-Backend: `http://<IP>:8080`, Anmeldung admin/admin, Änderung optional; der Pi hat die feste IP 192.168.22.22). Kurzreferenz für den Alltag: `../BEDIENUNG.md`. Dateien in `deploy/`: sechs Service-Units, `sportstracker-sudoers` (erlaubt dem Web-Backend genau: Neustart, Herunterfahren, Neustart der Dienste, Zeitzone, Hostname), `install.sh`.

Module: `satellites.py`, `service_mqtt.py`, `service_safety.py`, `picture.py`, `state.py` (Zustandsdatei), `views.py` (Anzeigeplan: Hauptspiel, Karten, Leerlauf-Wechsel), `service_data.py`, `service_display.py`, `service_sensor.py`, `sensor.py`, `camera.py`, `service_web.py` mit `webapp.py` (Routen), `auth.py` (Kennwort-Hash, Sitzungsschlüssel, Login-Sperre), `settings.py` (Formular <-> Konfigdatei), `leagues.py` (Liga-Voreinstellungen), `system.py` (Systembefehle über sudo, im Test ersetzbar).

## Noch nicht auf echter Hardware geprüft (0.6.0)

Übergangsanimationen mit 15 Bildern pro Sekunde auf dem Pi Zero 2 W (CPU-Last), MQTT mit echtem Mosquitto-Broker, Kompilierung und Betrieb des ESP32-Sketches, Kalibrierung des MQ-135.
