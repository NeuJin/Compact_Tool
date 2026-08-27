# Compact_Tool

Move a file off a machine where the only allowed channel is "look at the
screen" (remote desktop with file transfer and network disabled, but you
can run scripts locally there) — without manually re-typing dense CAD/FEA
text and without relying on generic OCR.

Instead of screenshotting Notepad scrolled through a raw file, the remote
machine renders it as a sequence of dense, self-checking "pages": big
blocky characters from a 32-symbol alphabet, lzma-compressed first, with
a page index/count, a per-page CRC32, and the whole file's SHA-256 baked
into every page. You screenshot each page (any order, any filenames), and
the decoder tells you exactly which pages are missing or came out wrong —
no guessing, no silent corruption.

Two folders, one per machine:

- **`capture/`** — runs on the locked-down remote machine. Reads your
  file, renders it as page images.
- **`decode/`** — runs on your own machine. Turns captured screenshots
  back into the original file.

Both are **Python 3.8+, standard library only**, except `decode/capture_gui.py`
which additionally needs Pillow (only on your own machine, never on the
remote one — see "Why not OCR").

## Why not OCR

Tesseract-style OCR is a general handwriting/print recognizer — overkill
and less reliable here, since we control exactly what gets rendered.
Instead, `decode/decode_pages.py` does nearest-neighbor **template
matching** against a reference glyph sheet rendered from this project's
own hand-drawn 5x7 bitmap font (`capture/bitmap_font.py`): there are only
32 possible characters per cell, so this is both simpler and far more
reliable than a general OCR engine, and it needs nothing beyond what
ships with Python.

## Quick start

1. **Copy the whole repo** to the remote machine (or at minimum the
   `capture/` folder, which is self-contained).

2. **On the remote machine:**
   ```bash
   cd capture
   python generate_pages.py --input-file C:\path\to\part.x_t --out-dir pages_out
   ```
   This writes `page_0000.png`, `page_0001.png`, ... to `pages_out\`.

3. **Calibrate before capturing hundreds of real pages.** Run step 2
   against a small throwaway file first (a few KB). Display `page_0000.png`
   full-screen on the remote machine exactly the way you'll do it for
   real, capture it the way you plan to capture the rest (screenshot, or
   Ctrl+V into `capture_gui.py` if your remote client mirrors the
   clipboard), and decode it (step 5). If it decodes with 0 CRC
   failures, your settings are good — proceed to the real file. If not,
   see "Tuning" below before spending time on hundreds of pages.

4. **Generate the glyph reference sheet** (run on your own machine —
   doesn't need the remote machine or any real data):
   ```bash
   cd capture
   python generate_pages.py --reference-sheet-only --out-dir ../decode
   ```
   This writes `glyph_reference.png` next to the decoder, which uses it
   to know what each of the 32 characters looks like.

5. **Decode.** Put all your captured screenshots in one folder (any
   filenames, any order — each page's index is read from the image
   itself) and run:
   ```bash
   cd decode
   python decode_pages.py --pages-dir <screenshots_dir> \
       --reference glyph_reference.png \
       --out reconstructed_file
   ```
   It reports each page as OK / CRC MISMATCH / unreadable, then either
   writes the reconstructed file (SHA-256-verified against the original)
   or tells you exactly which page indices to re-capture. Add the
   retakes to the same folder and re-run — already-good pages don't need
   to be redone.

   **Or use `decode/capture_gui.py`** instead of manually saving
   screenshots — if your remote-desktop client mirrors the remote
   clipboard to this machine's clipboard (common with RDP/VNC clipboard
   redirection even when file transfer is blocked), take a
   screenshot/snip on the remote machine, switch to this window, Ctrl+V.
   It saves straight into a capture folder (auto-numbered, safe to
   close/reopen mid-session) with a button to decode without touching
   the command line.

## Important: capture at native resolution

The single biggest thing that hurts reliability is the **screenshot being
a different resolution than the rendered canvas** (1920x1080 by default).
A uniform resize is corrected for automatically (via the 4 corner
markers), but resizing still blurs fine detail. Keep the remote display
at 1920x1080 (or change `CANVAS_W`/`CANVAS_H` in `page_format.py` in
*both* folders to match your actual remote resolution — see "Tuning"),
turn off any "fit to window" scaling in your remote-desktop client, and
screenshot at 1:1. The calibration step above is what catches resolution
trouble before you invest time capturing the real file.

## Tuning density vs. robustness

`GRID_COLS` / `GRID_ROWS` in `page_format.py` control how many characters
fit per page. **This file exists in both `capture/` and `decode/` and
must be changed identically in both** — there's no shared copy across
the two folders (each is meant to be deployable on its own). Bigger grid
= fewer screenshots but smaller, more fragile characters; smaller grid =
more screenshots but more margin against whatever compression your
remote-desktop/screenshot path applies.

Measured on one real capture pipeline (clean render / 83%-resolution
resize / that same resize plus JPEG quality 85, then confirmed against
an actual remote-desktop clipboard round trip):

| grid | bytes/page | resize | resize+JPEG | real capture |
|---|---|---|---|---|
| 48x18  | ~450  | OK | OK | — |
| 68x26  | ~1020 | OK | OK | — |
| 88x32  | ~1650 | OK | fails | — |
| 112x43 | ~2940 | OK | fails | **fails** |

112x43 failing under real capture lined up with it already failing the
synthetic resize+JPEG test — that synthetic battery turned out to be a
reasonable proxy for at least this capture pipeline. Start from 68x26 and
recalibrate (step 3) if you change it; don't assume a grid that passed
clean/resize-only testing will survive your real pipeline without the
resize+JPEG case too. There's also a hard floor around 9x this density
where cells shrink to a handful of pixels and even sub-pixel
corner-registration noise misreads characters on a clean render,
independent of compression — more tuning can't fix that, only a smaller
grid can.

## How it works

- **Capture** (`capture/generate_pages.py`, run on the remote machine):
  lzma-compress the file (a much bigger dictionary than gzip/zlib, so it
  can exploit repetition anywhere in a multi-MB file, not just within a
  32 KiB window), encode the compressed bytes as Crockford base32 (32
  symbols, deliberately excludes visually-confusable I/L/O/U), split into
  fixed-size pages, render each as a PNG: 4 solid-black corner markers +
  a fixed-width "prefix" (magic / page index / page count / page length /
  page CRC32 / whole-file length / whole-file SHA-256, repeated on every
  page) + a grid of body characters. The prefix spreads across as many
  rows as it needs at the current `GRID_COLS` — it isn't tied to one
  specific row width.
- **Decode** (`decode/decode_pages.py` + `glyph_match.py`, run on your
  machine): for each screenshot, find the 4 corner markers to correct for
  uniform scale/offset (handles a different capture resolution, not
  rotation), slice out each cell, match it against the 32 reference
  glyphs (tight crop to the ink bounding box + binarize first, so cell
  jitter and JPEG gray-noise don't get compared as if they were shape
  differences), reassemble by the page index embedded in the image
  (filenames/order don't matter), verify each page's CRC32, then verify
  the whole file's length + SHA-256 before writing anything out.
- Nothing is trusted blindly: a corrupted or missing page is always
  reported by index, never silently merged in.

## Files

**`capture/`** (remote machine):
- `generate_pages.py` — the CLI. Tries to import `codec`/`page_format`
  from its own folder first, then falls back to a sibling `local_side/`
  or `decode/` folder — works whichever way you've laid the files out.
- `bitmap_font.py` — the hand-drawn 5x7 bitmap font for the 32 alphabet
  characters.
- `png_writer.py` — a ~40-line stdlib-only PNG encoder (struct + zlib),
  since Pillow isn't assumed to be on the remote machine.
- `codec.py` / `page_format.py` — see below (identical copies live in
  `decode/` too).

**`decode/`** (your machine):
- `decode_pages.py` — the CLI.
- `glyph_match.py` — image -> character grid (corner detection, cell
  slicing, template matching). Font-agnostic: doesn't know or care how
  the reference sheet's glyphs were drawn.
- `capture_gui.py` — optional GUI, Ctrl+V a screenshot straight into the
  capture folder and decode without a terminal. Needs Pillow.
- `test_codec.py` — pure-logic round-trip tests, no images needed:
  `python test_codec.py`.
- `codec.py` / `page_format.py` — see below.

**Shared logic, duplicated in both folders on purpose** (so each folder
can be copied/deployed standalone — keep them identical if you edit one):
- `page_format.py` — the alphabet, canvas/grid geometry, page-prefix
  field layout.
- `codec.py` — pure byte/string logic: crockford encode/decode, page
  build/parse/assemble, CRC32/SHA-256 checks.

## Known limits

- Max 65,535 pages per file (plenty for KB-MB scale CAD/FEA files; this
  tool isn't meant for anything where that's a real ceiling — at that
  point screenshotting isn't a reasonable transfer method regardless).
- Corrects uniform scale + translation only, not rotation or perspective
  — screenshot the page straight-on, not photographed at an angle.
- `page_format.py` must match between `capture/` and `decode/` — if you
  change `GRID_COLS`/`GRID_ROWS`/`CANVAS_W`/`CANVAS_H`, change it in both.
