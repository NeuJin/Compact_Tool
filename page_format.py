"""
Shared layout constants for the screenshot-transfer page format.

Used by BOTH sides: local_side/codec.py + glyph_match.py (decode), and
remote_side/generate_pages.py (encode). Both sides are plain Python 3.8
stdlib now, so this one file is the single source of truth — no more
keeping a second copy in sync in a different language.

The page is: a fixed-width PREFIX (magic + per-page framing + whole-file
metadata, all concatenated with no separators) padded out to a whole
number of grid rows, followed by BODY rows carrying the actual payload.
PREFIX_ROWS is derived from GRID_COLS, so the grid can be made narrower
or wider (see "tuning" in README) without the metadata fields needing to
fit on one specific row.
"""

# Crockford base32: 32 visually-distinct symbols (no I, L, O, U — avoids
# confusion with 1, 1, 0, V under template matching / human reading).
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

MAGIC = "5B02"  # format tag + version (v2: generalized multi-row prefix)

# Canvas / grid geometry
CANVAS_W = 1920
CANVAS_H = 1080
MARGIN = 60
MARKER_SIZE = 40  # px, solid black square corner markers

GRID_COLS = 48
GRID_ROWS = 18

# Fixed-width prefix fields, in order, concatenated with no separators.
# (name, width)
PREFIX_FIELDS = [
    ("MAGIC", 4),
    ("PAGE_INDEX", 4),
    ("PAGE_TOTAL", 4),
    ("PAGE_LEN", 4),
    ("PAGE_CRC32", 8),
    ("ORIG_LEN", 10),
    ("COMPRESSED_LEN", 8),
    ("SHA256", 64),
]
PREFIX_WIDTH = sum(w for _, w in PREFIX_FIELDS)  # 106


def _field_offsets():
    offsets = {}
    pos = 0
    for name, width in PREFIX_FIELDS:
        offsets[name] = (pos, pos + width)
        pos += width
    return offsets


PREFIX_OFFSETS = _field_offsets()  # name -> (start, end) within the prefix string

PREFIX_ROWS = -(-PREFIX_WIDTH // GRID_COLS)  # ceil div
PREFIX_CAPACITY = PREFIX_ROWS * GRID_COLS

BODY_ROWS = GRID_ROWS - PREFIX_ROWS
BODY_COLS = GRID_COLS
BODY_CAPACITY_PER_PAGE = BODY_COLS * BODY_ROWS

FILL_CHAR = "0"  # pads unused tail of the prefix / last body page; decoder ignores it

if BODY_ROWS < 1:
    raise ValueError(
        f"GRID_COLS={GRID_COLS} too narrow: prefix alone needs {PREFIX_ROWS} of "
        f"GRID_ROWS={GRID_ROWS} rows, leaving no room for body payload"
    )
