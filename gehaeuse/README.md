# Sportstracker Gehäuse v0.2

Rückbox mit 10° Neigung, senkrechter Rückwand und verlängertem Fuß (Standtiefe 110 mm), kein Standbein, kein Frontrahmen. Pi Zero WH mit Bonnet liegt flach im Fuß, mittig. Alle Maße in mm.

Änderungen gegenüber v0: Aussparungen für die Arretierungsstifte des Panels, Kameraöffnung mit 0,6 mm Haut verschlossen, Pi von außen durchgeschraubt.

## Teile

| Datei | Teil | Funktion |
|---|---|---|
| `01_montagering.stl` | Montagering | wird mit 6 Schrauben M3 an die Messing-Einsätze des Panels geschraubt, mit Aussparungen für die Stifte |
| `02_keilgehaeuse.stl` | Keilgehäuse | Rückschale, wird waagerecht über den Ring geschoben, Nuten für die Stifte an der Vorderkante |
| `03_fuss.stl` | Fuß | Pi, 5V-Buchse (rechts), Taster und Kamera (links) |
| `sportstracker_gehaeuse.scad` | Parametrisches Modell | Maße oben in der Datei ändern, STL neu exportieren (`part=...`) |

Die STL-Dateien liegen schon in Druckausrichtung (Rückwand bzw. Ring flach auf dem Druckbett). Alle drei passen auf das Bett der P1S (größtes Teil 160 × 158 × 59 mm).

## Druck (Empfehlung)

PETG, Düse 0,4, Schichthöhe 0,2, Wandstärke 2,4 mm (6 Perimeter), Infill 15 %, Montagering mit 100 %. Keine Stützen nötig. PLA wird ab etwa 55 °C weich.

## Schrauben

- Ring an Panel: 6 × M3 × 10, flacher Linsenkopf (ISO 7380), oben 2 und seitlich 4. Die 2 unteren Einsätze bleiben frei, weil dort der Kopf am Gehäuseboden anstehen würde. Löcher sind im Ring trotzdem vorhanden.
- Keilgehäuse an Ring: 4 × M3 × 10 seitlich (selbstschneidend in 2,6-mm-Vorbohrung).
- Fuß an Keilgehäuse: 4 × M3 × 10, von innen durch die Rückwand des Keils.
- Pi Zero von außen durchgeschraubt: 4 × M2,5 × 12 von unten durch Boden und Abstandshalter, durch den Pi (und das Bonnet), oben mit Mutter M2,5. Der Kopf sitzt in der Senkung (Ø 5,4 × 2 mm) bündig im Boden. Zwischen Pi und Bonnet je Schraube eine 2,5-mm-Hülse oder Unterlegscheiben, sonst biegt sich das Bonnet.
- Kamera V2.1: 4 × M2 × 6 oder Heißkleber auf die vier Pfosten.
- Taster: Bohrung 6,4 mm, Mutter von innen.
- Zugentlastung: Kabelbinder (max. 3,6 mm) durch die zwei Schlitze neben der 5V-Öffnung.

## Arretierungsstifte des Panels

Pro Kante zwei Stifte, je 55 mm von den Ecken entfernt (also 8 Stifte), 2,5 mm weiter außen als die M3-Einsätze, das heißt 3,5 mm von der Außenkante. Angenommen sind Ø 3,0 mm und 3,0 mm Höhe. Im Ring sind Aussparungen Ø 5,0 mm, im Keilgehäuse waagerechte Nuten an der Vorderkante, damit das Gehäuse beim Aufschieben nicht an den Stiften anstößt.

## Kamera nachrüsten

Die Öffnung für die Linse (Ø 10 mm) ist von innen eingearbeitet, außen bleibt eine 0,6 mm dünne Haut. Zum Nachrüsten die Haut mit einem Cuttermesser oder einem 10-mm-Bohrer von außen aufschneiden. Die vier Pfosten für die Kameraplatine sind schon vorhanden.

## Montagereihenfolge

1. Ring auf die Panelrückseite schrauben.
2. Pi mit Bonnet, Taster und Kamera in den Fuß einbauen, Kabel vorbereiten.
3. Fuß von innen an das Keilgehäuse schrauben (Keilgehäuse noch offen, gut erreichbar).
4. Flachkabel und Stromkabel durch das Fenster in der Rückwand führen und am Panel anstecken.
5. Keilgehäuse waagerecht auf den Ring schieben und mit den 4 Seitenschrauben sichern. Nicht entlang der Panelnormalen aufschieben, sonst stößt die Deckenwand am Ring an.

## Annahmen, die noch geprüft werden müssen

1. Stifte: Anzahl und Position wie oben beschrieben (zwei je Kante bei 55 mm von jeder Ecke), Durchmesser 3,0 und Höhe 3,0 mm sind geschätzt (`pin_d`, `pin_h`).
2. Bauhöhe hinter dem Panel: `top_clear = 28` (deine 25 mm plus 3 mm Luft). Bitte bestätigen, ob die 25 mm ab der Rückfläche des Panelrahmens oder ab der Panelvorderseite gemessen sind. Im zweiten Fall ist mehr Platz da und das Gehäuse kann flacher werden.
3. Abstand Einsatz zur Außenkante: 6 mm aus dem Foto geschätzt (`ins_edge`). Entlang der Kante 35 mm von der Ecke, wie von dir angegeben.
4. 5V-Buchse: Position (Höhe 16,5 mm über dem Boden, 14 mm hinter der Vorderkante der Bonnet) aus dem Foto geschätzt. Öffnung 18 × 16 mm mit 30 mm langem Führungskanal, damit der Hohlstecker gerade eintaucht.
5. Taster: Bohrung 6,4 mm. Bitte Bauteil prüfen (Gewinde M6? Länge hinter der Wand).
6. Kamera: Lochbild 21 × 12,5 mm zentriert um die Linse angenommen, Linsenbohrung 10 mm. Mit dem Bauteil prüfen. Die Kamera blickt zur Seite durch die linke Wand.
7. Stromseite: Mit der USB-Kante des Pi zum Panel liegt die 5V-Buchse rechts (von hinten gesehen), Taster und Kamera links. Soll der Strom links liegen, wird der Pi um 180° gedreht, dann zeigt die USB-Kante nach hinten und das Flachkabel wird etwas länger.
8. Pi-Verschraubung: Bohrbild 58 × 23 mm. Prüfen, ob die Bonnet-Löcher dasselbe Muster haben, und ob das Bonnet mit verschraubt werden soll oder nur der Pi.
9. Shutdown-Taster: laut Beschriftung auf dem Bonnet gibt es freie Lötpunkte SCL (GPIO3) und GND am Ende bei der DC-Buchse. Taster zwischen GPIO3 und GND, im Pi `dtoverlay=gpio-shutdown` setzen. Beschriftung am Bauteil gegenprüfen.
10. Gewicht und Stand: Standtiefe 110 mm bei 10° Neigung. Ob es mit Kabel stabil steht, zeigt sich erst am Prototyp.
