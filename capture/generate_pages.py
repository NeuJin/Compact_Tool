"""
Turn a file into a sequence of dense, self-checking PNG "pages" meant to
be displayed full-screen and screenshotted, then decoded back on another
machine with decode_pages.py.

Python 3.8, stdlib only (lzma/hashlib/zlib/struct) — no Pillow, no pip
installs, nothing beyond what ships with a bare Python install. Needs
codec.py and page_format.py — either copied into this same folder, or one
directory up in a sibling folder named "local_side" or "decode" (the two
folder-naming conventions used across the repos this file lives in);
works unmodified in any of them.

Usage:
    python generate_pages.py --input-file part.x_t --out-dir pages_out
    python generate_pages.py --reference-sheet-only --out-dir pages_out
"""
import argparse
import hashlib
import os
import sys
import time

try:
    import codec
    import page_format as fmt
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    for _sibling in ("local_side", "decode"):
        sys.path.insert(0, os.path.join(_here, "..", _sibling))
    import codec
    import page_format as fmt

import bitmap_font
import png_writer


def _fitting_block_size(cell_w: float, cell_h: float) -> int:
    """Largest integer px-per-font-pixel that keeps the glyph within 80%
    of the cell in both dimensions."""
    block = min(cell_w * 0.8 / bitmap_font.FONT_W, cell_h * 0.8 / bitmap_font.FONT_H)
    return max(1, int(block))


def render_page(rows) -> bytearray:
    """rows: fmt.GRID_ROWS strings, each fmt.GRID_COLS chars. Returns a
    CANVAS_W*CANVAS_H grayscale pixel buffer (0=black, 255=white)."""
    if len(rows) != fmt.GRID_ROWS:
        raise ValueError(f"expected {fmt.GRID_ROWS} rows, got {len(rows)}")

    W, H = fmt.CANVAS_W, fmt.CANVAS_H
    pixels = bytearray([255]) * (W * H)

    def fill_rect(x0, y0, x1, y1, value):
        x0 = max(0, x0); y0 = max(0, y0)
        x1 = min(W, x1); y1 = min(H, y1)
        for y in range(y0, y1):
            row_start = y * W
            for x in range(x0, x1):
                pixels[row_start + x] = value

    m = fmt.MARKER_SIZE
    for (cx, cy) in [(0, 0), (W - m, 0), (0, H - m), (W - m, H - m)]:
        fill_rect(cx, cy, cx + m, cy + m, 0)

    block = _fitting_block_size(fmt.CELL_W, fmt.CELL_H)
    glyph_w = bitmap_font.FONT_W * block
    glyph_h = bitmap_font.FONT_H * block

    for r, row_str in enumerate(rows):
        if len(row_str) != fmt.GRID_COLS:
            raise ValueError(f"row {r} has length {len(row_str)}, expected {fmt.GRID_COLS}")
        for c, ch in enumerate(row_str):
            glyph = bitmap_font.GLYPHS[ch]
            # Use the SAME integer cell box glyph_match.py will slice out on
            # decode (see page_format.ideal_cell_box docstring) instead of
            # separately rounding cell position here — otherwise the two
            # sides can silently disagree by a pixel once cells are small.
            cx0, cy0, cx1, cy1 = fmt.ideal_cell_box(r, c)
            gx0 = cx0 + (cx1 - cx0 - glyph_w) // 2
            gy0 = cy0 + (cy1 - cy0 - glyph_h) // 2
            for gy, glyph_row in enumerate(glyph):
                for gx, mark in enumerate(glyph_row):
                    if mark == "#":
                        px0 = gx0 + gx * block
                        py0 = gy0 + gy * block
                        fill_rect(px0, py0, px0 + block, py0 + block, 0)

    return pixels


def rows_from_page_string(page_str: str):
    return [
        page_str[i * fmt.GRID_COLS : (i + 1) * fmt.GRID_COLS]
        for i in range(fmt.GRID_ROWS)
    ]


def generate_reference_sheet(out_dir: str) -> str:
    row0 = fmt.ALPHABET.ljust(fmt.GRID_COLS, fmt.FILL_CHAR)
    blank_row = "".ljust(fmt.GRID_COLS, fmt.FILL_CHAR)
    rows = [row0] + [blank_row] * (fmt.GRID_ROWS - 1)
    pixels = render_page(rows)
    out_path = os.path.join(out_dir, "glyph_reference.png")
    png_writer.write_png_gray(out_path, fmt.CANVAS_W, fmt.CANVAS_H, pixels)
    return out_path


def generate_data_pages(input_file: str, out_dir: str) -> int:
    print(f"Input: {input_file}", flush=True)
    print("  reading + lzma + encoding ...", flush=True)
    t0 = time.time()

    with open(input_file, "rb") as f:
        data = f.read()

    pages = codec.build_pages(data)
    sha256 = hashlib.sha256(data).hexdigest().upper()
    print(f"  original:   {len(data)} bytes", flush=True)
    print(f"  SHA-256:    {sha256}", flush=True)
    print(f"  -> {len(pages)} page(s) ({time.time() - t0:.1f}s so far)", flush=True)

    render_start = time.time()
    report_every = max(1, min(10, len(pages)))
    for idx, page_str in enumerate(pages):
        rows = rows_from_page_string(page_str)
        pixels = render_page(rows)
        out_path = os.path.join(out_dir, f"page_{idx:04d}.png")
        png_writer.write_png_gray(out_path, fmt.CANVAS_W, fmt.CANVAS_H, pixels)
        if idx % report_every == 0 or idx == len(pages) - 1:
            elapsed = time.time() - render_start
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(pages) - idx - 1) / rate if rate > 0 else 0
            print(
                f"  rendered page {idx + 1} / {len(pages)}"
                f"  ({elapsed:.0f}s elapsed, ~{remaining:.0f}s left)",
                flush=True,
            )

    return len(pages)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-file", help="file to encode (e.g. a .x_t or .inp file)")
    ap.add_argument("--out-dir", default="pages_out", help="folder to write page PNGs into")
    ap.add_argument(
        "--reference-sheet-only", action="store_true",
        help="only render glyph_reference.png (no input file needed)",
    )
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    if args.reference_sheet_only:
        out_path = generate_reference_sheet(args.out_dir)
        print(f"Wrote reference sheet: {out_path}", flush=True)
        return

    if not args.input_file:
        print("Provide --input-file <path>, or use --reference-sheet-only.", flush=True)
        sys.exit(1)

    total = generate_data_pages(args.input_file, args.out_dir)
    print(f"Done. {total} page(s) written to {args.out_dir}", flush=True)
    print("Next steps:", flush=True)
    print("  1. If glyph_reference.png isn't already on the decoding machine, generate it "
          "there: python generate_pages.py --reference-sheet-only --out-dir <dir>", flush=True)
    print(f"  2. Open {args.out_dir} as a full-screen slideshow (any image viewer) and "
          "screenshot each page, in any order.", flush=True)
    print("  3. On the decoding machine: python decode_pages.py --pages-dir <screenshots_dir> "
          "--reference glyph_reference.png --out <reconstructed_file>", flush=True)


if __name__ == "__main__":
    main()
