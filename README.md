# Compact_Tool

Encodes a file into a sequence of dense, self-checking PNG "pages" meant
to be displayed full-screen and screenshotted off a machine where the
only allowed channel is "look at the screen" — no file transfer, no
clipboard, no network, but scripts can run locally.

**Python 3.8, standard library only.** No Pillow, no pip installs,
nothing beyond a bare Python install — this is meant to run on a
locked-down machine you can't install anything on.

## What it does

Compresses your file (gzip), encodes it as a 32-symbol alphabet
(Crockford base32 — picked to avoid visually-confusable characters like
I/L/O/U), splits it into pages sized to fill a 1920x1080 canvas, and
renders each page as a PNG: 4 corner alignment markers + a data grid drawn
with a small hand-built bitmap font (`bitmap_font.py`) via a from-scratch
PNG encoder (`png_writer.py` — just `struct` + `zlib`, no image library).
Every page carries its own index, page count, a CRC32 of its own content,
and the whole file's length + SHA-256 — so a decoder on the other end can
tell you exactly which screenshots are missing or came out wrong, instead
of you finding out only when the reconstructed file won't open.

This repo is the **encode/render half only**. The matching decoder (reads
screenshots back into a file) lives in the companion project this was
extracted from — copy `glyph_reference.png` (see below) over to wherever
you run the decoder.

## Usage

```bash
# Encode a real file into page images
python generate_pages.py --input-file part.x_t --out-dir pages_out

# Generate the glyph reference sheet the decoder needs (run once, doesn't
# need a real input file — can be run on the decoding machine instead if
# that's more convenient)
python generate_pages.py --reference-sheet-only --out-dir pages_out
```

Then: open the `pages_out` folder as a full-screen slideshow (any image
viewer) on this machine and screenshot each page, in any order — the page
index is embedded in the image itself, not the filename.

**Calibrate before capturing a real file.** Run the encode command above
against a small throwaway file first, screenshot just that one page
through your actual remote-desktop/screenshot pipeline, and decode it. If
it comes back byte-identical, your capture settings are good. If you're
seeing decode trouble, the two most common causes are a screenshot taken
at a different resolution than 1920x1080 (fixable — see below) or a grid
too dense for how much your screenshot path compresses things.

## Tuning

`GRID_COLS` / `GRID_ROWS` in `page_format.py` control how many characters
fit per page — default 48x18 (~450 bytes of compressed data per page,
chosen conservatively so each character renders as a large, robust
block). Raise them for fewer/denser pages once calibration confirms your
pipeline handles it; lower them if calibration shows read errors.
`CANVAS_W` / `CANVAS_H` (default 1920x1080) should match your actual
remote display resolution.

## Files

- `generate_pages.py` — the CLI (see Usage above).
- `bitmap_font.py` — hand-drawn 5x7 bitmap glyphs for the 32-character
  alphabet.
- `png_writer.py` — minimal stdlib PNG encoder.
- `codec.py` — gzip/base32/page-framing logic (CRC32 per page, SHA-256
  for the whole file).
- `page_format.py` — shared constants: alphabet, canvas/grid geometry,
  page field layout.
