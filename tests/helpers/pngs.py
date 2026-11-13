"""Write small valid PNG files for the image tests (no Pillow)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def png_bytes(width: int, height: int, shade: int = 0) -> bytes:
    """A grey PNG of `width` x `height`; a different `shade` gives different bytes."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes([(shade + x) % 256 for x in range(width)]) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def write_png(path: Path, width: int = 100, height: int = 100, shade: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(width, height, shade))
    return path
