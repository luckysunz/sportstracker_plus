#!/bin/bash
# Installs the seven Sportstracker+ services (data, display, sensor, web, safety, mqtt, wifi) on the Raspberry Pi.
#
#   cd ~/sportstracker
#   sudo ./deploy/install.sh                 # install or update (safe to run again)
#   sudo ./deploy/install.sh --uninstall     # remove services and sudo rule (project files stay)
#
# Optional: SPORTSTRACKER_VENV=/path/to/venv sudo -E ./deploy/install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo starten: sudo ./deploy/install.sh" >&2
    exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-pi}"
if ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Benutzer '$APP_USER' existiert nicht." >&2
    exit 1
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
VENV="${SPORTSTRACKER_VENV:-$APP_HOME/venv}"
UNITS="sportstracker-data sportstracker-display sportstracker-sensor sportstracker-web sportstracker-safety sportstracker-mqtt sportstracker-wifi"

if [ "${1:-}" = "--uninstall" ]; then
    for unit in $UNITS; do
        systemctl disable --now "$unit" 2>/dev/null || true
        rm -f "/etc/systemd/system/$unit.service"
    done
    rm -f /etc/sudoers.d/sportstracker
    systemctl daemon-reload
    echo "Dienste und sudo-Regel entfernt. Die Projektdateien in $DIR bleiben erhalten."
    exit 0
fi

echo "== Projekt: $DIR  Benutzer: $APP_USER  venv: $VENV"

if [ ! -x "$VENV/bin/python" ]; then
    echo "Python-Umgebung $VENV nicht gefunden (siehe Setup-Anleitung: python3 -m venv --system-site-packages ~/venv)." >&2
    exit 1
fi

echo "== Pakete prüfen"
if ! "$VENV/bin/python" -c "import flask, lxml, PIL" 2>/dev/null; then
    apt-get install -y python3-flask python3-lxml python3-pil
fi
if ! "$VENV/bin/python" -c "import rgbmatrix" 2>/dev/null; then
    echo "Das Python-Modul 'rgbmatrix' fehlt in $VENV. Bitte zuerst die Matrix-Bibliothek installieren (Setup-Anleitung)." >&2
    exit 1
fi
if ! command -v rsvg-convert >/dev/null 2>&1; then
    apt-get install -y librsvg2-bin
fi

echo "== MQTT-Bibliothek (Satelliten, optional)"
if ! "$VENV/bin/python" -c "import paho.mqtt.client" 2>/dev/null; then
    if apt-get install -y python3-paho-mqtt >/dev/null 2>&1 && "$VENV/bin/python" -c "import paho.mqtt.client" 2>/dev/null; then
        echo "   paho-mqtt installiert (apt)"
    elif "$VENV/bin/pip" install paho-mqtt >/dev/null 2>&1; then
        echo "   paho-mqtt installiert (pip)"
    else
        echo "   paho-mqtt konnte nicht installiert werden - der MQTT-Dienst bleibt ohne Verbindung (HTTP-Satelliten gehen trotzdem)"
    fi
fi

echo "== WLAN-Setup (Hotspot, optional)"
if ! command -v nmcli >/dev/null 2>&1; then
    echo "   NetworkManager (nmcli) fehlt - der WLAN-Dienst kann den Setup-Hotspot nicht starten (Bookworm hat ihn normalerweise)"
fi
if ! "$VENV/bin/python" -c "import qrcode" 2>/dev/null; then
    if apt-get install -y python3-qrcode >/dev/null 2>&1 && "$VENV/bin/python" -c "import qrcode" 2>/dev/null; then
        echo "   qrcode installiert (apt)"
    elif "$VENV/bin/pip" install qrcode >/dev/null 2>&1; then
        echo "   qrcode installiert (pip)"
    else
        echo "   qrcode konnte nicht installiert werden - die Matrix zeigt im Setup nur Text statt QR-Codes"
    fi
fi

echo "== Sensor-Bibliothek (DHT22, optional)"
# Der DHT22 wird ohne Blinka/Adafruit gelesen: nur gpiod 2.x (Kernel-Zeitstempel der Flanken) wird gebraucht.
if ! "$VENV/bin/python" -c "import gpiod; gpiod.LineSettings" 2>/dev/null; then
    if "$VENV/bin/pip" install 'gpiod>=2.2.2,<3' >/dev/null 2>&1; then
        echo "   gpiod installiert"
    else
        echo "   gpiod konnte nicht installiert werden - der Sensor bleibt ohne Werte (siehe ANLEITUNG, Abschnitt Sensor)"
    fi
fi
usermod -aG gpio,video "$APP_USER" 2>/dev/null || true

echo "== Konfiguration und Ordner"
if [ ! -f "$DIR/config/config.json" ]; then
    cp "$DIR/config/config.rbbl.example.json" "$DIR/config/config.json"
    echo "   config/config.json aus der RBBL-Vorlage angelegt (Liga und Team später im Web-Backend wählen)"
fi
mkdir -p "$DIR/data" "$DIR/cache" "$DIR/logos/override"
chown -R "$APP_USER":"$APP_USER" "$DIR/config" "$DIR/data" "$DIR/cache" "$DIR/logos"
# Old .pyc files (possibly root-owned from earlier tests) can block later file copies.
find "$DIR" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "== systemd-Dienste"
render() {
    sed -e "s|@USER@|$APP_USER|g" -e "s|@DIR@|$DIR|g" -e "s|@VENV@|$VENV|g" "$1"
}
for unit in $UNITS; do
    render "$DIR/deploy/$unit.service" > "/etc/systemd/system/$unit.service"
    chmod 644 "/etc/systemd/system/$unit.service"
done

echo "== sudo-Regel für das Web-Backend"
TMP="$(mktemp)"
render "$DIR/deploy/sportstracker-sudoers" > "$TMP"
if visudo -cf "$TMP" >/dev/null; then
    install -m 0440 -o root -g root "$TMP" /etc/sudoers.d/sportstracker
else
    rm -f "$TMP"
    echo "Die sudoers-Datei ist ungültig, Installation abgebrochen." >&2
    exit 1
fi
rm -f "$TMP"

systemctl daemon-reload
for unit in $UNITS; do
    systemctl enable "$unit" >/dev/null
    systemctl restart "$unit"
done

sleep 2
echo "== Status"
for unit in $UNITS; do
    printf '   %-22s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
done
IP="$(hostname -I | awk '{print $1}')"
echo
echo "Web-Backend: http://${IP:-<IP-des-Pi>}:8080   (Anmeldung admin / admin)"
echo "Protokoll:   journalctl -u sportstracker-data -u sportstracker-display -u sportstracker-sensor -u sportstracker-web -u sportstracker-safety -u sportstracker-mqtt -u sportstracker-wifi -f"
