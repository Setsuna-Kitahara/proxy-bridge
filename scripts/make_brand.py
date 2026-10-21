#!/usr/bin/env python3
"""Generate the brand images (no third-party dependencies).

    python scripts/make_brand.py

Writes ``custom_components/proxy_bridge/brand/icon.png`` (square, opaque) and
``logo.png`` (transparent glyph) at 4x supersampling.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "custom_components" / "proxy_bridge" / "brand"
SUPERSAMPLE = 4
DECK_TOP = 0.385
DECK_BOTTOM = 0.435
ARCH_CENTERS = (0.34, 0.66)


def _rounded(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


def _shapes(u: float, v: float) -> tuple[bool, bool]:
    """Return (background, foreground) coverage for normalised coordinates."""
    bg = _rounded(u, v, 1.0, 0.235)
    fg = False
    if 0.125 <= u <= 0.875 and DECK_TOP <= v <= DECK_BOTTOM:
        fg = True
    for px in (0.125, 0.815):
        if px <= u <= px + 0.06 and DECK_BOTTOM <= v <= 0.815:
            fg = True
    for cx in ARCH_CENTERS:
        d = math.hypot(u - cx, v - DECK_BOTTOM)
        if v >= DECK_BOTTOM and 0.098 <= d <= 0.17:
            fg = True
    return bg, fg


def _render(size: int, transparent: bool) -> list[bytes]:
    rows: list[bytes] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            bg_cov = fg_cov = 0.0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    u = (px + (sx + 0.5) / SUPERSAMPLE) / size
                    v = (py + (sy + 0.5) / SUPERSAMPLE) / size
                    bg, fg = _shapes(u, v)
                    bg_cov += bg
                    fg_cov += fg
            bg_cov /= SUPERSAMPLE**2
            fg_cov /= SUPERSAMPLE**2
            if transparent:
                row += bytes((56, 189, 248, int(255 * fg_cov)))
                continue
            t = py / max(size - 1, 1)
            br, bg_, bb = 15 + 20 * t, 23 + 30 * t, 42 + 40 * t
            row += bytes(
                (
                    int(br * (1 - fg_cov) + 56 * fg_cov),
                    int(bg_ * (1 - fg_cov) + 189 * fg_cov),
                    int(bb * (1 - fg_cov) + 248 * fg_cov),
                    int(255 * bg_cov),
                )
            )
        rows.append(bytes(row))
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _write(path: Path, size: int, transparent: bool) -> None:
    raw = b"".join(b"\x00" + row for row in _render(size, transparent))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", header)
    png += _chunk(b"IDAT", zlib.compress(raw, 9))
    png += _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> None:
    _write(BRAND / "icon.png", 256, transparent=False)
    _write(BRAND / "logo.png", 512, transparent=True)


if __name__ == "__main__":
    main()
