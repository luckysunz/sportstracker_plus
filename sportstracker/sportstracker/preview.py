"""Enlarged LED-style previews of frames and a contact sheet (for layout checks on a PC)."""

from __future__ import annotations

from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont


def upscale(image: Image.Image, factor: int, led: bool = True) -> Image.Image:
    w, h = image.size
    if not led:
        return image.resize((w * factor, h * factor), Image.NEAREST)
    out = Image.new("RGB", (w * factor, h * factor), (12, 12, 12))
    draw = ImageDraw.Draw(out)
    pixels = image.load()
    pad = max(1, factor // 8)
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            dim = (28, 28, 28)
            color = (r, g, b) if (r or g or b) else dim
            draw.ellipse((x * factor + pad, y * factor + pad, (x + 1) * factor - pad - 1, (y + 1) * factor - pad - 1), fill=color)
    return out


def contact_sheet(frames: List[Tuple[str, Image.Image]], factor: int = 6, columns: int = 3) -> Image.Image:
    font = ImageFont.load_default()
    tiles = [(title, upscale(img, factor)) for title, img in frames]
    tw, th = tiles[0][1].size
    margin, caption = 10, 16
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (tw + margin) + margin, rows * (th + caption + margin) + margin), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for i, (title, tile) in enumerate(tiles):
        x = margin + (i % columns) * (tw + margin)
        y = margin + (i // columns) * (th + caption + margin)
        draw.text((x, y), title, fill=(220, 220, 220), font=font)
        sheet.paste(tile, (x, y + caption))
    return sheet
