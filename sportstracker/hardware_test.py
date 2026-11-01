#!/usr/bin/env python3
"""Einzeltest für DHT22 und Kamera (IMX219), unabhängig von den Sportstracker-Diensten.

    python3 hardware_test.py dht [--gpio 25] [--count 10]
    python3 hardware_test.py camera [--out test.jpg] [--width 1280 --height 960]
    python3 hardware_test.py all

Vor dem Sensortest den Sensordienst anhalten (er belegt den GPIO):
    sudo systemctl stop sportstracker-sensor
Danach wieder starten:
    sudo systemctl start sportstracker-sensor

Das Skript braucht kein root; für den Sensor genügt das Python-Modul gpiod (Version 2), es wird erst im
Test geladen. Nutze am besten die venv:
    ~/venv/bin/python hardware_test.py dht
"""

import argparse
import grp
import os
import shutil
import subprocess
import sys
import tempfile
import time

OK, FAIL, WARN = "[ OK ]", "[FEHLT]", "[ ?? ]"


def say(tag, text):
    print("%s %s" % (tag, text))


def user_groups():
    try:
        return {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except Exception:  # noqa: BLE001
        return set()


def test_dht(gpio, count, interval, backend="auto"):
    print("== DHT22 an GPIO%d ==" % gpio)
    groups = user_groups()
    if "gpio" in groups:
        say(OK, "Benutzer ist in der Gruppe gpio")
    else:
        say(WARN, "Benutzer nicht in der Gruppe gpio (Installer + Neustart oder: sudo usermod -aG gpio $USER, neu anmelden)")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sportstracker.dht_mmap import open_dht22
    from sportstracker.dht_native import DhtError, high_pulses
    try:
        device = open_dht22(gpio, backend)
    except DhtError as exc:
        say(FAIL, str(exc))
        return False
    say(OK, "Backend: %s" % ("GPIO-Register (mmap)" if device.__class__.__name__ == "MmapDht22" else "libgpiod-Flanken (gpiod)"))
    if device.__class__.__name__ == "NativeDht22":
        try:
            import gpiod  # type: ignore
        except ImportError as exc:
            say(FAIL, "Python-Modul fehlt: %s" % exc)
            print("       Installation:  ~/venv/bin/pip install 'gpiod>=2.2.2,<3'")
            return False
        if not hasattr(gpiod, "LineSettings"):
            say(FAIL, "gpiod %s ist zu alt (libgpiod 1.x), benötigt wird Version 2." % getattr(gpiod, "__version__", "1.x"))
            return False
    good = []
    for number in range(1, count + 1):
        try:
            temperature, humidity = device.read()
            good.append((temperature, humidity))
            print("  %2d/%d  %5.1f °C  %5.1f %%" % (number, count, temperature, humidity))
        except DhtError as exc:
            edges = device.last_edges
            pulses = high_pulses(edges)
            detail = ""
            if edges:
                detail = "  [%d Flanken, %d High-Pulse, Länge min/max %.0f/%.0f us]" % (
                    len(edges), len(pulses), min(pulses) / 1000.0 if pulses else 0, max(pulses) / 1000.0 if pulses else 0)
            print("  %2d/%d  Lesefehler: %s%s" % (number, count, exc, detail))
            if "belegt" in str(exc):
                print("       Läuft sportstracker-sensor noch? Dann: sudo systemctl stop sportstracker-sensor")
                return False
        except Exception as exc:  # noqa: BLE001
            say(FAIL, "Harter Fehler: %s" % exc)
            return False
        if number < count:
            time.sleep(interval)                              # DHT22: höchstens 0,5 Hz
    if not good:
        say(FAIL, "Keine einzige gültige Messung. Verdrahtung prüfen: + an 3,3 V, - an GND, OUT an GPIO%d." % gpio)
        print("       0 Flanken = der Sensor antwortet gar nicht (Verdrahtung, Pin, Spannung; fehlt der Pull-up?).")
        print("       Viele Flanken, aber falsche Bits/Prüfsumme = Störungen oder zu langes Kabel.")
        return False
    temps = [t for t, _ in good]
    hums = [h for _, h in good]
    rate = 100.0 * len(good) / count
    print("  Ergebnis: %d von %d Messungen gültig (%.0f %%), Temperatur %.1f bis %.1f °C, Feuchte %.0f bis %.0f %%"
          % (len(good), count, rate, min(temps), max(temps), min(hums), max(hums)))
    if max(temps) - min(temps) > 2.0 or max(hums) - min(hums) > 8.0:
        say(WARN, "Werte schwanken stark; Kabel/Kontakte prüfen.")
    if rate < 50:
        say(WARN, "Viele Lesefehler. Kabel kürzer/fester stecken, Stromversorgung prüfen, ggf. anderen GPIO testen.")
    say(OK, "Der Sensor liefert Werte. Im Backend darf er jetzt aktiviert werden.")
    return True


def run(cmd, timeout):
    try:
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              timeout=timeout)
        return done.returncode, done.stdout or ""
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung"


def diagnose_camera():
    """Hints for 'no camera found': boot configuration, kernel messages, device nodes."""
    print("  -- Diagnose --")
    config = "/boot/firmware/config.txt"
    try:
        with open(config, encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip() for line in handle]
    except OSError:
        lines = []
        say(WARN, "%s nicht lesbar" % config)
    interesting = [line for line in lines if any(k in line.lower() for k in ("camera", "imx", "ov5647", "[", "start_x"))]
    print("  config.txt (Kamera/Abschnitte):")
    for line in interesting or ["(keine passenden Zeilen)"]:
        print("     " + line)
    joined = "\n".join(lines)
    if "camera_auto_detect=0" in joined and "dtoverlay=imx219" not in joined:
        say(WARN, "camera_auto_detect=0 gesetzt, aber kein dtoverlay=imx219 -> Kamera wird nicht geladen.")
    if "camera_auto_detect=1" not in joined and "dtoverlay=imx219" not in joined:
        say(WARN, "Weder camera_auto_detect=1 noch dtoverlay=imx219 in der config.txt.")
    if "start_x=1" in joined.replace(" ", ""):
        say(WARN, "start_x=1 (alter Kameramodus) ist gesetzt; mit libcamera entfernen.")
    code, text = run(["dmesg"], 10)
    hits = [l for l in text.splitlines() if any(k in l.lower() for k in ("imx219", "unicam", "csi", "ov5647", "rp1-cfe"))]
    print("  Kernelmeldungen zur Kamera:")
    for line in hits[-12:] or ["     (keine; ggf. mit sudo dmesg versuchen)"]:
        print("     " + line.strip())
    joined_hits = " ".join(hits).lower()
    if "imx219" in joined_hits and ("fail" in joined_hits or "error" in joined_hits or "-121" in joined_hits or "-110" in joined_hits):
        say(WARN, "Der Treiber findet den Sensor nicht auf dem I2C-Bus: meist Kabel/Stecker (Kontaktseite, nicht ganz eingerastet) oder defektes Kabel.")
    nodes = sorted(n for n in os.listdir("/dev") if n.startswith(("video", "media")))
    print("  /dev/video*, /dev/media*: %s" % (", ".join(nodes) or "keine"))


def test_camera(out, width, height):
    print("== Kamera (IMX219) ==")
    if "video" in user_groups():
        say(OK, "Benutzer ist in der Gruppe video")
    else:
        say(WARN, "Benutzer nicht in der Gruppe video (Installer + Neustart oder: sudo usermod -aG video $USER)")
    lister = still = None
    for tool in ("rpicam-hello", "libcamera-hello"):
        if shutil.which(tool):
            lister = tool
            break
    for tool in ("rpicam-still", "libcamera-still"):
        if shutil.which(tool):
            still = tool
            break
    if not lister or not still:
        say(FAIL, "rpicam-Werkzeuge fehlen:  sudo apt install -y rpicam-apps")
        return False
    code, text = run([lister, "--list-cameras"], 20)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if code != 0 or "Available cameras" not in text:
        say(FAIL, "Keine Kamera erkannt.")
        for line in lines[-6:]:
            print("       " + line)
        print("       Prüfen: Flachbandkabel (22-polig am Pi), Pi ausschalten, beide Enden neu stecken, dann neu starten.")
        diagnose_camera()
        return False
    found = [line for line in lines if line[:1].isdigit() and ":" in line]
    say(OK, "Kamera erkannt: %s" % (found[0] if found else "ja"))
    if "imx219" not in text.lower():
        say(WARN, "Erkannt, aber nicht als imx219. Anderes Modul?")
    target = out or os.path.join(tempfile.gettempdir(), "kameratest.jpg")
    started = time.time()
    code, text = run([still, "-n", "-t", "1500", "--width", str(width), "--height", str(height), "-o", target], 40)
    if code != 0:
        say(FAIL, "Aufnahme fehlgeschlagen (Code %d)" % code)
        for line in text.strip().splitlines()[-6:]:
            print("       " + line)
        return False
    try:
        with open(target, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        say(FAIL, "Bilddatei nicht lesbar: %s" % exc)
        return False
    if not data.startswith(b"\xff\xd8"):
        say(FAIL, "Die Datei ist kein JPEG.")
        return False
    size = ""
    try:
        from PIL import Image  # type: ignore
        with Image.open(target) as image:
            size = ", %d x %d Pixel" % image.size
            gray = image.convert("L").resize((32, 32))
            pixels = list(gray.getdata())
            mean = sum(pixels) / len(pixels)
            spread = max(pixels) - min(pixels)
            if mean < 8 or spread < 6:
                say(WARN, "Das Bild ist fast einfarbig/dunkel (Objektivschutzfolie noch drauf? Licht?).")
    except Exception:  # noqa: BLE001
        pass
    say(OK, "Bild gespeichert: %s (%d kB%s, %.1f s)" % (target, len(data) // 1024, size, time.time() - started))
    print("       Ansehen: im Backend unter 'Kamera' oder per scp auf den PC holen:")
    print("       scp pi@<IP>:%s ." % target)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hardware-Test für DHT22 und IMX219")
    sub = parser.add_subparsers(dest="what", required=True)
    dht = sub.add_parser("dht", help="DHT22 lesen")
    dht.add_argument("--gpio", type=int, default=25)
    dht.add_argument("--count", type=int, default=10)
    dht.add_argument("--interval", type=float, default=2.5)
    dht.add_argument("--backend", choices=["auto", "mmap", "gpiod"], default="auto",
                     help="mmap = GPIO-Register (Standard), gpiod = Kernel-Flankenereignisse")
    cam = sub.add_parser("camera", help="Kamera erkennen und ein Foto aufnehmen")
    cam.add_argument("--out", default="")
    cam.add_argument("--width", type=int, default=1280)
    cam.add_argument("--height", type=int, default=960)
    everything = sub.add_parser("all", help="beides nacheinander")
    everything.add_argument("--gpio", type=int, default=25)
    args = parser.parse_args(argv)
    results = []
    if args.what in ("dht", "all"):
        results.append(test_dht(args.gpio, getattr(args, "count", 10), getattr(args, "interval", 2.5),
                                 getattr(args, "backend", "auto")))
    if args.what in ("camera", "all"):
        results.append(test_camera(getattr(args, "out", ""), getattr(args, "width", 1280), getattr(args, "height", 960)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
