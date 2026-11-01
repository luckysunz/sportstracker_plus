# Sportstracker Gehäuse v2.1 (Stand 29.10.2026)

Montagering und Keilgehäuse, ohne separaten Fuß. Der Pi Zero mit Bonnet liegt im Keilgehäuse ganz hinten an der Rückwand, von unten verschraubt. Alle Maße in mm.

## Änderungen gegenüber v0.3

- Der Fuß entfällt. Der Montagering ist unverändert, das Keilgehäuse nimmt Pi, Bonnet, Taster, Kamera und DHT22 selbst auf.
- Die Rückwand sitzt 7 mm weiter hinten. Hinter dem Panel sind oben jetzt 21 mm Luft (vorher 14 mm), unten 49,1 mm Innentiefe. Die Rückwand bleibt senkrecht, die Standtiefe wächst auf 51,5 mm.
- Der Pi liegt mittig auf vier gedruckten Abstandshaltern (3 mm), 1,5 mm vor der Rückwand. Die vier Schrauben M2,5 × 10 gehen von unten durch den Boden und den Pi in die weißen Abstandshalter zwischen Pi und Bonnet, die Köpfe sitzen in Senkungen bündig im Boden.
- Rechte Wand (von hinten gesehen): Öffnung für den 5V-Hohlstecker mit Führungskanal und Kabelbinder-Schlitzen, darüber das DHT22-Fenster (15,6 × 20,0 mm).
- Linke Wand: Bohrung 7 mm für den Taster und Aufnahme für die Kamera IMX219-120 mit offener Linsenöffnung und Schiebeabdeckung.

## Änderungen in v2.1 (gegenüber dem ersten v2-Stand)

- DHT22-Fenster jetzt 15,6 × 20,0 mm (kürzer, nicht enger). Das Befestigungsohr des Sensors bleibt innen in einer 1 mm tiefen Mulde der Wand, nur der Hauptkörper steckt durch das Fenster.
- Befestigungsloch M2,5 des DHT22 ist durchgebohrt (Ø 2,7 mm durch Dom und Wand), außen mit Senkung Ø 5,2 × 1,5 mm für den Schraubenkopf.
- Lüftung: 9 Sechsecke (Ø 6 mm, 3 × 3) in der Rückwand auf Höhe von Pi und Bonnet (Z = 8 / 18 / 28 mm, X = -12 / 0 / 12 mm).
- Taster und Kameralinse sitzen auf gleicher Höhe (25,5 mm über dem Boden). Taster-Bohrung 7 mm, 15,2 mm hinter der Panel-Unterkante. Die Kameratasche hat vorn keine Rippe mehr, damit die Mutter Platz hat.
- Kameraöffnung wird offen gedruckt (keine Haut mehr), dafür gibt es eine Schiebeabdeckung als eigenes Teil.
- Zapfen der Kamerahalterung 0,8 mm länger (ragen jetzt 2,2 mm aus der Platine).

## Kamera IMX219-120 (neu: Lochbild aus deinen Fotos, Steckverbindung)

Die Platine hat sechs Löcher mit 1,95 mm. Aus den Fotos mit Lineal (Genauigkeit etwa ±0,3 mm) habe ich vermessen: vier Eckloch-Positionen im Abstand 21,2 × 13,6 mm (1,4 mm vom Rand) und zwei weitere Löcher in der Mitte (17,8 mm auseinander), die Linse sitzt 7,5 mm von der Kante gegenüber dem Stecker und etwa mittig. Genutzt werden die vier Eckpositionen.

Die Platine wird nicht geschraubt, sondern auf vier gedruckte Zapfen gesteckt: Pfosten Ø 3,4 mm, darauf ein kegeliger Zapfen von Ø 2,0 mm unten auf Ø 1,4 mm oben, der 1,4 mm aus der Platine herausschaut. Die Taschenwand führt die Platine seitlich, der Kegel zentriert sie in den Löchern. Zum Sichern den Zapfen mit dem Lötkolben warm verformen oder einen Tropfen Kleber nehmen. Wer lieber echte Stiftleisten oder Drähte nehmen will, kann den Zapfen in `peg_d` und `peg_tip` anpassen.

Die Platine ist um 180° gedreht eingeplant: Die Anschlusskante mit dem 15-poligen Stecker zeigt nach oben, die Tasche ist dort 20 mm breit offen. Das Flachkabel kommt von oben, macht einen Bogen und läuft zum Pi (mitgeliefert ist ein FFC von 15 auf 22 Pin für den Pi Zero). Unterkante der Platine 18 mm über dem Boden, Oberkante 42 mm, die Linse sitzt bei 25,5 mm Höhe. Der Taster sitzt auf derselben Höhe wie die Linse, davor (Richtung Panel) ohne Rippe an der Tasche.

Linsenöffnung: Das Objektiv hat 6,2 mm, die Öffnung ist innen 6,5 mm groß und weitet sich nach außen leicht auf (40° Halbwinkel, an der Außenseite rund Ø 10,5 mm). Sie geht jetzt offen durch die Wand, die Linse sitzt 0,6 mm dahinter. Wer eine glatte Bohrung will, setzt `lens_cone = 0`.

Schiebeabdeckung (`05_kamera_abdeckung.stl`): Zwei Schwalbenschwanz-Schienen (senkrecht, Hinterschnitt 45°, ohne Stützen druckbar) sitzen außen an der linken Wand links und rechts der Linse. Die Abdeckung (23,2 × 16 × 3,6 mm) wird von oben aufgeschoben und rutscht bis zum Anschlag unten, dort verdeckt sie die Linse. Zum Öffnen 14 mm nach oben schieben; sie hält per Reibsitz (Spiel `sl_clr` = 0,2 mm, bei zu strammem oder zu losem Sitz anpassen). Oben auf der Abdeckung ist eine Fingermulde. Druck: Außenfläche auf dem Bett, Nut zeigt nach oben, keine Stützen.

## DHT22 (AM2302 mit Platine)

Rechte Wand, direkt über dem Führungskanal der 5V-Buchse. Die Platine liegt innen an der Wand in einer Tasche, 2,2 mm von der Wand entfernt auf zwei Auflagen und einem Schraubdom. Der Hauptkörper des Sensors (weißer Käfig) steckt durch ein Fenster von 15,6 × 20,0 mm (Z = 44,0 bis 64,0 mm) und steht etwa 3,1 mm über die Wand hinaus. Das untere Befestigungsohr (angenommen 5,1 mm lang, 3,0 mm dick) bleibt innen und liegt in einer 1 mm tiefen Mulde der Wandinnenseite. Befestigung: M2,5 × 8 von außen durch Wand und Dom, Mutter auf der Platinenseite (Kopf in der Senkung). Die Pins zeigen nach oben, dort ist die Tasche offen für den Dupont-Stecker.

## Lüftung

9 Sechsecke (Ø 6 mm über die Ecken, Spitzen links/rechts) in der Rückwand, 1,5 mm hinter dem Bonnet. Die Form ist bewusst einfach, damit der Druck sauber wird (Rückwand liegt flach auf dem Bett, keine Überhänge). Die Luft strömt durch den Spalt zwischen Pi und Rückwand. Soll mehr Luft von unten kommen, kann man später Gummifüße ergänzen und Schlitze im Boden ergänzen (derzeit nicht vorgesehen, weil dort die Schraubenköpfe sitzen).

## Dateien

| Datei | Teil | Funktion |
|---|---|---|
| `01_montagering.stl` | Montagering | unverändert, 6 × M3 an die Messing-Einsätze, Aussparungen für die Arretierungsstifte |
| `02_keilgehaeuse_v2.stl` | Keilgehäuse | Rückschale mit Pi-Aufnahme, 5V-Öffnung, DHT22, Taster und Kamera, Druckausrichtung (Rückwand flach) |
| `03_kamera_taster_probestueck.stl` | Probestück links | Kameraaufnahme mit Zapfen, Tasterbohrung und Schienen zum Testen (Abdeckung dazu drucken) |
| `04_dht22_probestueck.stl` | Probestück rechts | DHT22-Fenster, Mulde und -Tasche zum Testen |
| `05_kamera_abdeckung.stl` | Schiebeabdeckung | Deckel für die Kameraöffnung, Außenfläche auf dem Bett |
| `sportstracker_gehaeuse_v2.scad` | Modell | Maße oben ändern, STL neu exportieren (`part=wedge_print`, `ring_print`, `camtest`, `dhttest`, `cover_print`) |
| `uebersicht_v2.png` | Bild | Ansichten und Schnitte |

Größtes Teil: 160 × 158 × 51,5 mm, passt auf die P1S. PETG, Düse 0,4, Schichthöhe 0,2, Wandstärke 2,4 mm (6 Perimeter), Infill 15 %, keine Stützen nötig. Erst die beiden Probestücke und die Abdeckung drucken (je etwa 20 Minuten), prüfen ob die Abdeckung gut gleitet, dann das große Teil.

## Schrauben und Teile

- Ring an Panel: 6 × M3 × 10 (ISO 7380), wie in v0.3.
- Keilgehäuse an Ring: 4 × M3 × 10 seitlich.
- Pi: 4 × M2,5 × 10 von unten.
- Kamera: gesteckt auf vier Zapfen, keine Schrauben.
- Taster: Bohrung 7 mm, Mutter von innen (Platz bis etwa SW 9,5 mm geprüft).
- DHT22: M2,5 × 8 + Mutter statt M3 (siehe oben).
- Zugentlastung: Kabelbinder durch die zwei Schlitze neben der 5V-Öffnung.

## Montagereihenfolge

1. Ring auf die Panelrückseite schrauben.
2. Pi mit Bonnet von unten in das Keilgehäuse schrauben, Taster, DHT22 und später die Kamera einbauen, 5V-Kabel einstecken.
3. Flachkabel und Stromkabel am Panel anstecken, mit etwas Zugabe.
4. Keilgehäuse waagerecht auf den Ring schieben (nicht entlang der Panelnormalen, sonst stößt die Deckenwand am Ring an), Kabel dabei in den Hohlraum schieben, 4 Seitenschrauben eindrehen.

## Annahmen, die noch geprüft werden müssen

1. Kamera: Lochpositionen und Linsenlage stammen aus Fotos (±0,3 mm). Der kegelige Zapfen gleicht kleine Abweichungen aus, das Probestück zeigt es vor dem großen Druck. Bauhöhe der Linse 5,5 mm und Platinendicke 1 mm geschätzt (`cam_lens_h`, `cam_pcb`).
2. DHT22: Ohrlänge (5,1 mm) und Ohrdicke (3,0 mm) sind geschätzt (`dht_ear_l`, `dht_ear_t`), bitte nachmessen; die Mulde ist 1 mm tief (`dht_recess`). Ich kenne nur die Gesamtmaße des Moduls (15 × 38 × 9 mm) und den Sensorkörper aus dem Datenblatt. Die Platinenlänge (35 mm), Platinenbreite (15,5 mm) und die Lage des Montagelochs (30,5 mm von der Unterkante des Sensors) sind geschätzt (`dht_pl`, `dht_pw`, `dht_hole_s`). Bitte an deinem Modul nachmessen. Das Modell nimmt an, dass der Sensor flach auf der Platine sitzt und die Gitterfläche parallel zur Platine ist, wie auf dem Produktfoto.
3. Pi-Orientierung: USB-Kante zum Panel, 5V-Buchse rechts. Die Position der Buchse (14 mm hinter der Vorderkante des Bonnets) ist geschätzt (`jack_dfront`).
4. Abstandshalter zwischen Pi und Bonnet: 11 mm angenommen (`pi_spacer`), Höhe der Buchse 25 mm über dem Boden (`jack_z`). Bitte nachmessen, ebenso das M2,5-Gewinde (mindestens 5 mm tief).
5. Taster: 7 mm Bohrung, 25,5 mm über dem Boden (Höhe der Linse), 15,2 mm hinter der Panel-Unterkante. Gewindelänge und Mutterngröße deines Tasters prüfen. Die Leitungen laufen am Boden entlang zu den Lötpunkten SCL (GPIO3) und GND am Hohlstecker-Ende des Bonnets.
6. Zapfenhöhe der Panelstifte 3 mm und Abstand der M3-Einsätze zur Außenkante 6 mm sind weiter geschätzt (wie in v0.3).
7. Stand: Standtiefe 51,5 mm. Ohne Fuß ist die Kippreserve nach vorn kleiner als bei v0.3, auf dem Prototyp mit Kabeln prüfen (notfalls Gummifüße oder etwas Gewicht im Boden).
8. DHT22 elektrisch: Die Datenleitung braucht einen freien GPIO am Pi. Welcher frei ist, hängt von der Matrix-Belegung (adafruit-hat-pwm mit der GPIO4-GPIO18-Brücke) ab und klären wir separat.
