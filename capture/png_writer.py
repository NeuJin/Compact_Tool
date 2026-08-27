"""
Minimal PNG encoder, stdlib only (struct + zlib) — no Pillow. Writes an
8-bit grayscale, non-interlaced, single-IDAT PNG. That's all this project
needs (flat black/white pages), so no filtering beyond "None" and no
palette/color-type support.
"""
import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png_gray(path: str, width: int, height: int, pixels: bytearray) -> None:
    """pixels: row-major bytes, one per pixel, 0=black .. 255=white,
    length must be exactly width*height."""
    if len(pixels) != width * height:
        raise ValueError(f"pixels length {len(pixels)} != width*height {width * height}")

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        raw.extend(pixels[y * width : (y + 1) * width])
    idat = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(signature)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", idat))
        f.write(_chunk(b"IEND", b""))


if __name__ == "__main__":
    import sys

    w, h = 64, 64
    px = bytearray([255]) * (w * h)
    for y in range(h):
        for x in range(w):
            if 16 <= x < 48 and 16 <= y < 48:
                px[y * w + x] = 0
    out = sys.argv[1] if len(sys.argv) > 1 else "test_png_writer_output.png"
    write_png_gray(out, w, h, px)
    print(f"wrote {out}: {w}x{h} with a black square in the middle")
