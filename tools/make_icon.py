"""Generate app_icon.ico — a simple map-pin icon, with zero dependencies.

Pillow isn't required: this rasterises the artwork by hand (with 4x
supersampling for smooth edges) and writes a classic BMP/DIB-based .ico
container, which every Windows version and PyInstaller understand.

Run:  python tools/make_icon.py
Out:  app_icon.ico  (+ app_icon.png preview) in the project root
"""

from __future__ import annotations

import os
import struct
import sys
import zlib

# Brand colours (match the app's dark theme + green Start button)
# Read from the theme rather than repeated here: the accent moved once already
# (#22A559 could not carry white text at 4.5:1) and the icon kept the old colour,
# so the launcher and the app it launches were different greens.
def _accent():
    try:
        import sys, os
        sys.path.insert(0, ROOT_DIR)
        from ui.theme import theme
        hexv = theme("dark").color["accent.hover"].lstrip("#")
        return tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (0x1A, 0x85, 0x47)


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG = _accent()               # brand green, from ui/theme.py
FG = (0xFF, 0xFF, 0xFF)      # white pin
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4                        # supersampling factor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── geometry helpers (all in 0..1 normalised space) ──────────────────────────
def _in_rounded_rect(x, y, r):
    """Point inside the unit square with corner radius r?"""
    cx = min(max(x, r), 1.0 - r)
    cy = min(max(y, r), 1.0 - r)
    dx, dy = x - cx, y - cy
    return (dx * dx + dy * dy) <= r * r


def _in_circle(x, y, cx, cy, rad):
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= rad * rad


def _in_triangle(x, y, ax, ay, bx, by, cx, cy):
    def sign(x1, y1, x2, y2, x3, y3):
        return (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3)
    d1 = sign(x, y, ax, ay, bx, by)
    d2 = sign(x, y, bx, by, cx, cy)
    d3 = sign(x, y, cx, cy, ax, ay)
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)


# Pin proportions
HEAD_CX, HEAD_CY, HEAD_R = 0.50, 0.40, 0.205
HOLE_R = 0.078
TIP_X, TIP_Y = 0.50, 0.845
TAIL_HALF, TAIL_Y = 0.152, 0.545


def _in_pin(x, y):
    if _in_circle(x, y, HEAD_CX, HEAD_CY, HEAD_R):
        return True
    return _in_triangle(
        x, y,
        HEAD_CX - TAIL_HALF, TAIL_Y,
        HEAD_CX + TAIL_HALF, TAIL_Y,
        TIP_X, TIP_Y,
    )


def render(size: int) -> bytearray:
    """Render one size as RGBA bytes (top-down, row-major)."""
    corner = 0.22
    px = bytearray(size * size * 4)
    inv = 1.0 / (size * SS)
    samples = SS * SS

    for py in range(size):
        for pxi in range(size):
            bg_hits = pin_hits = 0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * inv
                for sx in range(SS):
                    x = (pxi * SS + sx + 0.5) * inv
                    if not _in_rounded_rect(x, y, corner):
                        continue
                    bg_hits += 1
                    if _in_pin(x, y) and not _in_circle(x, y, HEAD_CX, HEAD_CY, HOLE_R):
                        pin_hits += 1

            if bg_hits == 0:
                continue
            alpha = bg_hits / samples
            t = pin_hits / bg_hits  # how much of the covered area is pin
            r = round(BG[0] + (FG[0] - BG[0]) * t)
            g = round(BG[1] + (FG[1] - BG[1]) * t)
            b = round(BG[2] + (FG[2] - BG[2]) * t)
            i = (py * size + pxi) * 4
            px[i:i + 4] = bytes((r, g, b, round(alpha * 255)))
    return px


# ── ICO container (classic 32-bit BMP entries) ───────────────────────────────
def _bmp_entry(rgba: bytearray, size: int) -> bytes:
    """BITMAPINFOHEADER + bottom-up BGRA pixels + AND mask."""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        size,          # biWidth
        size * 2,      # biHeight (XOR + AND mask)
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression (BI_RGB)
        0,             # biSizeImage
        0, 0, 0, 0,    # ppm + palette
    )
    body = bytearray()
    for y in range(size - 1, -1, -1):       # bottom-up
        row = y * size * 4
        for x in range(size):
            i = row + x * 4
            r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
            body += bytes((b, g, r, a))     # BGRA
    # AND mask: all zero (fully "opaque"); alpha channel does the work.
    mask_row = ((size + 31) // 32) * 4
    body += bytes(mask_row * size)
    return header + bytes(body)


def write_ico(path: str, images: list) -> None:
    count = len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    offset = 6 + 16 * count
    blobs = []
    for size, rgba in images:
        blob = _bmp_entry(rgba, size)
        blobs.append(blob)
        out += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0, 0, 1, 32, len(blob), offset,
        )
        offset += len(blob)
    for blob in blobs:
        out += blob
    with open(path, "wb") as f:
        f.write(out)


def write_png(path: str, rgba: bytearray, size: int) -> None:
    """Minimal PNG writer (for a README/preview image)."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)                                  # filter: none
        raw += rgba[y * size * 4:(y + 1) * size * 4]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main() -> int:
    print("Rendering icon sizes:", ", ".join(str(s) for s in SIZES))
    images = []
    for s in SIZES:
        images.append((s, render(s)))
        print(f"  {s}x{s} done")

    ico = os.path.join(ROOT, "app_icon.ico")
    write_ico(ico, images)
    print(f"Wrote {ico}  ({os.path.getsize(ico):,} bytes)")

    preview = dict(images)[256]
    png = os.path.join(ROOT, "app_icon.png")
    write_png(png, preview, 256)
    print(f"Wrote {png}  ({os.path.getsize(png):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
