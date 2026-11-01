# Sportstracker+

RGB-Matrix-Display (Raspberry Pi + Adafruit RGB Matrix Bonnet, 64x64) für Ligadaten: laufende Spiele, Ergebniskarten, Tabelle, Raumklima, Kamera und Bild-Galerie. Läuft als sechs systemd-Dienste mit Web-Backend.

## Inhalt

| Ordner / Datei | Inhalt |
|---|---|
| `sportstracker/` | Python-Code, Konfigurationsbeispiele, systemd-Dienste, ESP32-Satellit, Tests – Details in [`sportstracker/README.md`](sportstracker/README.md) |
| `gehaeuse/` | 3D-Modelle und OpenSCAD-Quellen für das Gehäuse – siehe [`gehaeuse/README.md`](gehaeuse/README.md) |
| `ANLEITUNG.md` | Ausführliche Einrichtung und Betrieb |
| `BEDIENUNG.md` | Kurzreferenz für den Alltag |

## Schnellstart

```bash
cd sportstracker
cp config/config.example.json config/config.json
python3 -m sportstracker.cli check --config config/config.json
```

Hinweise: Die Anleitungen nennen eine private Heimnetz-IP (`192.168.22.22`) und die Erstanmeldung `admin / admin` – bitte für den eigenen Aufbau anpassen und das Kennwort ändern. Die lokale `config.json` ist bewusst nicht im Repository.

## Lizenz

[MIT](LICENSE). Die Schriften in `sportstracker/fonts/` stammen aus den X11-misc-fixed-Fonts (siehe dortige AUTHORS-/README-Dateien) und unterliegen deren eigenen Bedingungen.
