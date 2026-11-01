#!/bin/bash
# Richtet einen Taster zwischen GPIO3 (Pin 5) und GND (Pin 6) als Ein-/Aus-Schalter ein.
#
#   sudo bash deploy/power-button.sh            # einrichten (mehrfaches Ausführen ist unkritisch)
#   sudo bash deploy/power-button.sh --remove   # wieder entfernen
#
# Laufender Pi:  Taster drücken -> sauberes Herunterfahren (wie "sudo shutdown -h now")
# Heruntergefahrener Pi (Strom liegt an): Taster drücken -> Pi startet (GPIO3-Wake-Funktion des Pi)
# Ein Neustart ist danach nötig, damit das Overlay geladen wird.
set -euo pipefail

if [ -z "${CONFIG_TXT:-}" ] && [ "$(id -u)" -ne 0 ]; then   # CONFIG_TXT nur für Tests
    echo "Bitte mit sudo starten: sudo bash deploy/power-button.sh" >&2
    exit 1
fi

CONFIG="${CONFIG_TXT:-}"
if [ -z "$CONFIG" ]; then
    if [ -f /boot/firmware/config.txt ]; then
        CONFIG=/boot/firmware/config.txt      # Bookworm
    elif [ -f /boot/config.txt ]; then
        CONFIG=/boot/config.txt               # Bullseye und älter
    else
        echo "config.txt nicht gefunden." >&2
        exit 1
    fi
fi

BEGIN="# >>> sportstracker power button"
END="# <<< sportstracker power button"

# Alten Block entfernen (falls vorhanden).
if grep -qF "$BEGIN" "$CONFIG"; then
    TMP="$(mktemp)"
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$CONFIG" > "$TMP"
    cat "$TMP" > "$CONFIG"
    rm -f "$TMP"
fi

if [ "${1:-}" = "--remove" ]; then
    echo "Power-Button-Eintrag aus $CONFIG entfernt. Wirksam nach dem nächsten Neustart."
    exit 0
fi

if grep -Eq '^[[:space:]]*dtparam=([^#]*,)?i2c(_arm)?(=(on|true|1|yes))?([[:space:]]*(,|$))' "$CONFIG"; then
    echo "Hinweis: I2C ist aktiviert. GPIO3 ist die I2C-Taktleitung (SCL); dann kann der Taster nicht zuverlässig als Ein/Aus verwendet werden." >&2
    echo "         Prüfe die Zeile 'dtparam=i2c_arm=on' in $CONFIG." >&2
fi

cp -n "$CONFIG" "$CONFIG.sportstracker.bak" || true

cat >> "$CONFIG" <<CFG

$BEGIN
[all]
# Taster GPIO3 -> GND: Herunterfahren; im heruntergefahrenen Zustand weckt GPIO3 den Pi.
dtoverlay=gpio-shutdown,gpio_pin=3,active_low=1,gpio_pull=up,debounce=200
$END
CFG

echo "Eingetragen in $CONFIG (Sicherung: $CONFIG.sportstracker.bak)."
echo "Jetzt neu starten:  sudo reboot"
