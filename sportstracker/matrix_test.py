#!/usr/bin/env python3
"""Testbild fuer das LED-Panel: prueft Zeilen, Spalten, Ecken und tote Pixel.

    sudo ~/venv/bin/python matrix_test.py                 # Gitter (Standard)
    sudo ~/venv/bin/python matrix_test.py --mode fill     # Vollflaechen rot/gruen/blau/weiss
    sudo ~/venv/bin/python matrix_test.py --mapping adafruit-hat --slowdown 2

Gitter-Testbild (Pixelkoordinaten, Ursprung oben links):
  - weisser Rahmen um das ganze Panel
  - Ecken: oben links ROT, oben rechts GRUEN, unten links BLAU, unten rechts GELB (je 3x3 Pixel)
  - waagerechte Linien bei y = 8, 16, 24, 32, 40, 48, 56 in rot, gruen, blau, gelb, tuerkis, magenta, weiss
  - senkrechte Linien bei x = 8, 16, ... mit denselben Farben
  - weisse Diagonale von oben links nach unten rechts
Fehlt eine Linie oder ist sie verschoben/doppelt, ist die Zeilen- oder Spaltenzuordnung des Panels falsch.
"""

import argparse
import time

from PIL import Image, ImageDraw
from rgbmatrix import RGBMatrix, RGBMatrixOptions

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (255, 255, 255)]


def grid_image(w, h):
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, pos in enumerate(range(8, h, 8)):
        d.line((0, pos, w - 1, pos), fill=COLORS[i % len(COLORS)])
    for i, pos in enumerate(range(8, w, 8)):
        d.line((pos, 0, pos, h - 1), fill=COLORS[i % len(COLORS)])
    d.line((0, 0, w - 1, h - 1), fill=(255, 255, 255))
    d.rectangle((0, 0, w - 1, h - 1), outline=(255, 255, 255))
    d.rectangle((0, 0, 2, 2), fill=(255, 0, 0))
    d.rectangle((w - 3, 0, w - 1, 2), fill=(0, 255, 0))
    d.rectangle((0, h - 3, 2, h - 1), fill=(0, 0, 255))
    d.rectangle((w - 3, h - 3, w - 1, h - 1), fill=(255, 255, 0))
    return img


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rows", type=int, default=64)
    p.add_argument("--cols", type=int, default=64)
    p.add_argument("--chain", type=int, default=1)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--mapping", default="adafruit-hat-pwm")
    p.add_argument("--slowdown", type=int, default=1)
    p.add_argument("--brightness", type=int, default=60)
    p.add_argument("--pwm-bits", type=int, default=11)
    p.add_argument("--mode", choices=["grid", "fill"], default="grid")
    p.add_argument("--seconds", type=int, default=60, help="Dauer je Bild (grid) bzw. je Farbe (fill: 5 s)")
    a = p.parse_args()

    o = RGBMatrixOptions()
    o.rows, o.cols, o.chain_length, o.parallel = a.rows, a.cols, a.chain, a.parallel
    o.hardware_mapping = a.mapping
    o.gpio_slowdown = a.slowdown
    o.brightness = a.brightness
    o.pwm_bits = a.pwm_bits
    o.drop_privileges = False
    matrix = RGBMatrix(options=o)

    w, h = a.cols * a.chain, a.rows * a.parallel
    if a.mode == "grid":
        matrix.SetImage(grid_image(w, h))
        time.sleep(a.seconds)
    else:
        for color in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]:
            matrix.SetImage(Image.new("RGB", (w, h), color))
            time.sleep(5)


if __name__ == "__main__":
    main()
