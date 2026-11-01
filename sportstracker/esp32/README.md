# Sportstracker+ Satellit (ESP32, DHT22, MQ-135)

Kleine Sensorstation für andere Räume. Sie misst Temperatur, Luftfeuchte und einen Luftqualitäts-Rohwert und meldet sie an den Sportstracker+ Pi (HTTP oder MQTT). Der Pi zeigt je Satellit eine Seite im Wechsel nach der Raumansicht; Home Assistant bekommt dieselben Sensoren per MQTT-Discovery.

Der Sketch ist **nicht auf Hardware getestet**. Zuerst mit dem Serial Monitor (115200 Baud) prüfen, ob die Werte plausibel kommen.

## Material je Satellit

- ESP32-Entwicklungsboard (z. B. ESP32-DevKitC, 30 Pins), USB-Netzteil 5 V
- DHT22 (AM2302), am besten als Platine mit Pull-up (3 Pins)
- MQ-135-Modul (4 Pins: VCC, GND, DO, AO)
- zwei Widerstände für den Spannungsteiler (10 kΩ und 20 kΩ), Jumperkabel

## Verdrahtung

| Sensor | Anschluss | ESP32 |
| --- | --- | --- |
| DHT22 | VCC | 3V3 |
| DHT22 | GND | GND |
| DHT22 | DATA | GPIO 4 (bei nacktem Sensor 10 kΩ von DATA nach 3V3) |
| MQ-135 | VCC | 5V (VIN/USB) – die Heizung braucht 5 V |
| MQ-135 | GND | GND |
| MQ-135 | AO | Spannungsteiler, Mitte nach GPIO 34 |

**Wichtig:** AO des MQ-135 liefert bis ca. 5 V, der ESP32 verträgt am ADC höchstens 3,3 V. Darum AO über einen Teiler führen: AO – 10 kΩ – (Abgriff nach GPIO 34) – 20 kΩ – GND. Das ergibt rund 2/3 der Spannung (5 V → 3,3 V). GPIO 34 gehört zum ADC1; die Pins des ADC2 funktionieren bei aktivem WLAN nicht. Alle Massen (GND) verbinden.

## Sketch einspielen

1. Arduino-IDE: Boardverwalter → „esp32“ von Espressif installieren, Board „ESP32 Dev Module“.
2. Bibliotheksverwalter: „DHT sensor library“ (Adafruit, inklusive „Adafruit Unified Sensor“) und „PubSubClient“ installieren.
3. `sportstracker_satellite/sportstracker_satellite.ino` öffnen und oben die Einstellungen anpassen: WLAN, `SAT_KEY` und `SAT_NAME`, bei HTTP Pi-Adresse (`192.168.22.22`) und `SAT_TOKEN`, bei MQTT Broker-Adresse, Benutzer, Kennwort.
4. Im Sportstracker+ Backend die Seite **Satelliten** öffnen, dort denselben Namen und dieselbe Kennung eintragen und speichern. Das Backend erzeugt den Token; ihn in den Sketch eintragen (nur für HTTP nötig).
5. Hochladen, Serial Monitor öffnen. Alle 30 Sekunden erscheint das JSON mit den Werten. Das Backend zeigt unter „Satelliten“ die letzten Werte.

## JSON-Format

```json
{"temperature": 22.4, "humidity": 45.1, "gas": 812}
```

Alle Werte sind optional, mindestens einer muss vorhanden sein. Zusätzlich möglich: `"air_index": 78` (Luftqualität 0 = schlecht bis 100 = gut, wenn der Satellit sie selbst berechnet; sonst rechnet der Pi aus `gas`). Die Zeitangabe stammt vom Pi (Zeitpunkt des Empfangs).

- **HTTP:** `POST http://192.168.22.22:8080/api/satellite/<Kennung>` mit `Authorization: Bearer <Token>` (oder `X-Token: <Token>`) und `Content-Type: application/json`. Antwort `{"ok": true}`; 401 = falscher Token oder Satellit nicht aktiv, 400 = Werte unbrauchbar.
- **MQTT:** Topic `<Basis-Topic>/<Kennung>/state` (Standard `sportstracker/<Kennung>/state`); der Sketch sendet außerdem `…/status` (online/offline, retained) und die Discovery-Meldungen unter `homeassistant/sensor/…`.

Test ohne ESP32 (Pi oder PC im Heimnetz):

```
curl -X POST http://192.168.22.22:8080/api/satellite/wohnzimmer -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d "{\"temperature\":22.4,\"humidity\":45,\"gas\":900}"
mosquitto_pub -h 192.168.22.30 -t sportstracker/wohnzimmer/state -m "{\"temperature\":22.4,\"humidity\":45,\"gas\":900}"
```

## Hinweise zum MQ-135

- Der Sensor braucht nach dem Einschalten einige Minuten Aufwärmzeit (der Sketch sendet den Gaswert erst nach 3 Minuten) und bei Erstinbetriebnahme einige Stunden „Einbrennen“.
- Er liefert keinen genauen ppm-Wert und reagiert auf viele Gase, Temperatur und Feuchte. Die Anzeige ist ein **Trend**: Der Rohwert in frischer Luft kommt als „saubere Luft“, der Wert bei deutlich verbrauchter Luft (z. B. nach längerem Aufenthalt im geschlossenen Raum, Alkohol-Tupfer nahe am Sensor) als „schlechte Luft“ ins Backend; dazwischen wird linear umgerechnet.
- Zwei ESP32 am selben Pi brauchen jeweils eine eigene Kennung und einen eigenen Token.
