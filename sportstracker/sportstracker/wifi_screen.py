"""Matrix screens while the setup hotspot is active (0.7.0): QR code to join the hotspot, QR code of the setup page, text.

The QR codes need the ``qrcode`` package (Debian: python3-qrcode); without it only the text page is shown.
Dark modules are drawn black on a white panel (phone scanners expect that), so the setup screens are bright on purpose.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PIL import Image

from .wifi import HOTSPOT_ADDRESS, SETUP_PORT, setup_url, wifi_qr_text

PAGE_SECONDS = 6
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def qr_modules(text: str) -> Optional[List[List[bool]]]:
    """Module matrix (True = dark) of a QR code, None if the qrcode package is missing."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_L
    except ImportError:
        return None
    code = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_L, box_size=1, border=0)
    code.add_data(text)
    code.make(fit=True)
    return [[bool(cell) for cell in row] for row in code.get_matrix()]


def draw_qr(image: Image.Image, modules: List[List[bool]]) -> bool:
    """White panel with the code in the middle, as large as whole pixels per module allow. False if it does not fit."""
    size = len(modules)
    scale = min(image.width, image.height) // (size + 2)
    if scale < 1:
        return False
    image.paste(WHITE, (0, 0, image.width, image.height))
    x0 = (image.width - size * scale) // 2
    y0 = (image.height - size * scale) // 2
    for row, cells in enumerate(modules):
        for col, dark in enumerate(cells):
            if dark:
                for dy in range(scale):
                    for dx in range(scale):
                        image.putpixel((x0 + col * scale + dx, y0 + row * scale + dy), BLACK)
    return True


def page_count(have_qr: bool) -> int:
    return 3 if have_qr else 1


def page_at(now_seconds: float, have_qr: bool) -> int:
    return int(now_seconds // PAGE_SECONDS) % page_count(have_qr)


def render_setup(image: Image.Image, fonts: Any, theme: Any, hotspot: Dict[str, Any], page: int,
                 pin_hint: bool = True) -> Image.Image:
    """Page 0: QR to join the hotspot, 1: QR of the setup page, 2 (or the only page without qrcode): text."""
    ssid = str(hotspot.get("ssid", ""))
    password = str(hotspot.get("password", ""))
    if page in (0, 1):
        text = wifi_qr_text(ssid, password) if page == 0 else setup_url()
        modules = qr_modules(text)
        if modules is not None and draw_qr(image, modules):
            return image
    _text_page(image, fonts, theme, ssid, password, pin_hint)
    return image


def _wrap(font: Any, text: str, width: int) -> List[str]:
    """Break ``text`` into lines that fit ``width`` pixels (after a hyphen if possible, else anywhere)."""
    lines: List[str] = []
    while text:
        if font.text_width(text) <= width:
            lines.append(text)
            break
        cut = len(text)
        while cut > 1 and font.text_width(text[:cut]) > width:
            cut -= 1
        hyphen = text.rfind("-", 0, cut)
        if hyphen > 0:
            cut = hyphen + 1
        lines.append(text[:cut])
        text = text[cut:]
    return lines


def _text_page(image: Image.Image, fonts: Any, theme: Any, ssid: str, password: str, pin_hint: bool) -> None:
    small = fonts.small
    medium = fonts.medium
    medium.draw(image, (image.width - medium.text_width("WLAN-SETUP")) // 2, 1, "WLAN-SETUP", theme.score)
    entries = [("WLAN verbinden:", theme.date), (ssid, theme.room_time), ("PW " + password, theme.room_time),
               ("Browser öffnen:", theme.date), ("%s:%d/" % (HOTSPOT_ADDRESS, SETUP_PORT), theme.room_time),
               ("wifi/setup", theme.room_time)]
    if pin_hint:
        entries.append(("dann PIN", theme.date))
    y = 10
    for text, color in entries:
        for line in _wrap(small, text, image.width - 1):
            if y + small.height > image.height:
                return
            small.draw(image, 1, y, line, color)
            y += 6
