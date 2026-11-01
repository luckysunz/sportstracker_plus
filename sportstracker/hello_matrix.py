#!/usr/bin/env python3
"""Messtest fuer Sportstracker: 64x64-Panel, Pi Zero + Adafruit RGB Matrix Bonnet.

Start (Root noetig, damit die Bibliothek auf die GPIOs zugreifen darf):

    sudo ~/venv/bin/python hello_matrix.py
    sudo ~/venv/bin/python hello_matrix.py --mapping adafruit-hat --slowdown 2 --seconds 120

Waehrend der Test laeuft, in einer zweiten SSH-Sitzung `top -H` beobachten.
Am Ende gibt das Skript die verbrauchte CPU-Zeit des Python-Prozesses aus.
Der Refresh-Thread der Bibliothek zaehlt dazu.
"""

import argparse
import time

from PIL import Image, ImageDraw
from rgbmatrix import RGBMatrix, RGBMatrixOptions


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rows", type=int, default=64)
    p.add_argument("--cols", type=int, default=64)
    p.add_argument("--chain", type=int, default=1)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--mapping", default="adafruit-hat-pwm",
                   help="adafruit-hat oder adafruit-hat-pwm (nach GPIO4-GPIO18-Bruecke)")
    p.add_argument("--slowdown", type=int, default=1, help="gpio_slowdown 0..4 (Zero: meist 0-2)")
    p.add_argument("--brightness", type=int, default=60)
    p.add_argument("--pwm-bits", type=int, default=11)
    p.add_argument("--seconds", type=int, default=60)
    return p.parse_args()


def main():
    a = parse_args()

    o = RGBMatrixOptions()
    o.rows = a.rows
    o.cols = a.cols
    o.chain_length = a.chain
    o.parallel = a.parallel
    o.hardware_mapping = a.mapping
    o.gpio_slowdown = a.slowdown
    o.brightness = a.brightness
    o.pwm_bits = a.pwm_bits
    o.disable_hardware_pulsing = False
    o.show_refresh_rate = 1
    # Root-Rechte behalten, damit spaeter Logos/Config aus dem Home-Verzeichnis lesbar bleiben.
    o.drop_privileges = False

    matrix = RGBMatrix(options=o)

    w, h = a.cols * a.chain, a.rows * a.parallel
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((4, 4, 27, 27), outline=(255, 0, 0))      # Platzhalter Logo Heim
    d.rectangle((36, 4, 59, 27), outline=(0, 0, 255))     # Platzhalter Logo Gast
    d.text((8, 36), "3 : 1", fill=(255, 255, 0))
    d.text((12, 50), "Q2 08:15", fill=(0, 255, 0))
    # Rahmen um das ganze Panel: zeigt, ob alle Kanten und beide Haelften stimmen.
    d.rectangle((0, 0, w - 1, h - 1), outline=(40, 40, 40))

    cpu_start = time.process_time()
    wall_start = time.monotonic()

    matrix.SetImage(img)
    time.sleep(a.seconds)

    wall = time.monotonic() - wall_start
    cpu = time.process_time() - cpu_start
    print("Laufzeit %.1f s, CPU-Zeit Python-Prozess %.2f s (%.1f %%)" % (wall, cpu, 100.0 * cpu / wall))
    print("Hinweis: der Refresh-Thread der C++-Bibliothek ist in process_time enthalten.")


if __name__ == "__main__":
    main()
