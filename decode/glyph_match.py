"""
Image -> character-grid decoding for screenshot_transfer pages.

This is deliberately NOT general OCR. There are only 32 possible characters
per cell (page_format.ALPHABET), rendered at a known, controlled font/size
on the remote machine. So instead of a generic recognition model, this does
nearest-neighbor template matching against a reference glyph sheet rendered
locally from the exact same font — far more reliable for this closed
alphabet, and needs no extra installs beyond Pillow.
"""
import sys
from typing import Dict, List, Tuple

from PIL import Image, ImageChops, ImageStat

import page_format as fmt

TEMPLATE_SIZE = (32, 32)


def _corner_center(gray: Image.Image, cx: float, cy: float, search: int) -> Tuple[float, float]:
    """Centroid of dark pixels within `search` px of (cx, cy)."""
    w, h = gray.size
    x0 = max(0, int(cx - search))
    y0 = max(0, int(cy - search))
    x1 = min(w, int(cx + search))
    y1 = min(h, int(cy + search))
    region = gray.crop((x0, y0, x1, y1))
    pixels = region.load()
    sx = sy = n = 0
    threshold = 128
    rw, rh = region.size
    for yy in range(rh):
        for xx in range(rw):
            if pixels[xx, yy] < threshold:
                sx += xx
                sy += yy
                n += 1
    if n == 0:
        raise ValueError(
            f"no dark marker pixels found near ({cx:.0f},{cy:.0f}) "
            f"(search window {search}px) — is this really a page screenshot?"
        )
    return (x0 + sx / n, y0 + sy / n)


class Grid:
    """Maps ideal (row, col) cell centers to actual pixel coordinates in a
    captured image, using the 4 corner markers to correct for uniform
    scale + translation. Does not correct rotation/perspective."""

    def __init__(self, img: Image.Image):
        self.img = img
        gray = img.convert("L")
        w, h = img.size
        sx_guess = w / fmt.CANVAS_W
        sy_guess = h / fmt.CANVAS_H
        m = fmt.MARKER_SIZE
        ideal = {
            "tl": (m / 2, m / 2),
            "tr": (fmt.CANVAS_W - m / 2, m / 2),
            "bl": (m / 2, fmt.CANVAS_H - m / 2),
        }
        search = max(25, int(m * max(sx_guess, sy_guess)))

        tl = _corner_center(gray, ideal["tl"][0] * sx_guess, ideal["tl"][1] * sy_guess, search)
        tr = _corner_center(gray, ideal["tr"][0] * sx_guess, ideal["tr"][1] * sy_guess, search)
        bl = _corner_center(gray, ideal["bl"][0] * sx_guess, ideal["bl"][1] * sy_guess, search)

        self.scale_x = (tr[0] - tl[0]) / (ideal["tr"][0] - ideal["tl"][0])
        self.scale_y = (bl[1] - tl[1]) / (ideal["bl"][1] - ideal["tl"][1])
        self.off_x = tl[0] - ideal["tl"][0] * self.scale_x
        self.off_y = tl[1] - ideal["tl"][1] * self.scale_y

    def cell_box(self, row: int, col: int) -> Tuple[int, int, int, int]:
        # Same ideal box generate_pages.py centers glyphs within — see
        # page_format.ideal_cell_box docstring for why this must be
        # shared rather than each side rounding its own copy.
        ix0, iy0, ix1, iy1 = fmt.ideal_cell_box(row, col)
        x0 = self.off_x + ix0 * self.scale_x
        y0 = self.off_y + iy0 * self.scale_y
        x1 = self.off_x + ix1 * self.scale_x
        y1 = self.off_y + iy1 * self.scale_y
        return (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))

    def cell_image(self, row: int, col: int) -> Image.Image:
        return self.img.crop(self.cell_box(row, col))


def _tight_crop(img: Image.Image, threshold: int = 160, margin: int = 2) -> Image.Image:
    """Crop to the bounding box of dark (ink) pixels. Cell boundaries land
    on non-integer pixel offsets (canvas doesn't divide evenly by the grid),
    so the glyph sits at a slightly different spot within the cell box each
    time — without this, that jitter alone is enough to make visually
    similar glyphs (0/Q/D/B/8) score closer than the real character."""
    gray = img.convert("L")
    bbox = gray.point(lambda p: 255 if p < threshold else 0).getbbox()
    if bbox is None:
        return img
    w, h = img.size
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(w, x1 + margin)
    y1 = min(h, y1 + margin)
    return img.crop((x0, y0, x1, y1))


def _binarize(img: Image.Image, threshold: int = 180) -> Image.Image:
    """Collapse to pure black/white. A screenshot that's been through
    resizing and/or JPEG compression has gray ringing around every edge —
    comparing raw grayscale makes that noise count as much as real shape
    differences. Thresholding throws the noise away and compares shape."""
    return img.point(lambda p: 255 if p > threshold else 0)


class GlyphReference:
    """Reference glyph bitmaps sliced from glyph_reference.png (generated
    by GeneratePages.ps1 -ReferenceSheetOnly), one per alphabet character."""

    def __init__(self, reference_png_path: str):
        img = Image.open(reference_png_path).convert("L")
        grid = Grid(img)
        self.templates: Dict[str, Image.Image] = {}
        for i, ch in enumerate(fmt.ALPHABET):
            cell = _tight_crop(grid.cell_image(0, i)).resize(TEMPLATE_SIZE)
            self.templates[ch] = _binarize(cell)

    def best_match(self, cell_img: Image.Image) -> Tuple[str, float]:
        cell_img = _tight_crop(cell_img.convert("L")).resize(TEMPLATE_SIZE)
        cell_img = _binarize(cell_img)
        best_ch, best_score = "?", None
        for ch, tmpl in self.templates.items():
            diff = ImageChops.difference(cell_img, tmpl)
            score = ImageStat.Stat(diff).mean[0]
            if best_score is None or score < best_score:
                best_ch, best_score = ch, score
        return best_ch, best_score


def decode_row(grid: Grid, reference: GlyphReference, row: int, ncols: int) -> Tuple[str, float]:
    """Returns (decoded_string, worst_match_score_in_row)."""
    chars: List[str] = []
    worst = 0.0
    for col in range(ncols):
        cell = grid.cell_image(row, col)
        ch, score = reference.best_match(cell)
        chars.append(ch)
        worst = max(worst, score)
    return "".join(chars), worst


def decode_page_image(image_path: str, reference: GlyphReference) -> Tuple[str, float]:
    """Decode one page screenshot into the raw grid string codec.py expects
    (header row + meta row + body rows, concatenated). Returns
    (page_string, worst_cell_match_score) — a high worst-score is a red
    flag even if the page's own CRC32 happens to still pass."""
    img = Image.open(image_path)
    grid = Grid(img)
    rows = []
    worst = 0.0
    for r in range(fmt.GRID_ROWS):
        row_str, row_worst = decode_row(grid, reference, r, fmt.GRID_COLS)
        rows.append(row_str)
        worst = max(worst, row_worst)
    return "".join(rows), worst


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python glyph_match.py <glyph_reference.png> <page_image.png>")
        sys.exit(1)
    ref = GlyphReference(sys.argv[1])
    page_str, worst = decode_page_image(sys.argv[2], ref)
    print(f"worst cell match score (0=perfect): {worst:.1f}")
    print("header:", page_str[: fmt.GRID_COLS])
    print("meta:  ", page_str[fmt.GRID_COLS : fmt.GRID_COLS * 2])
