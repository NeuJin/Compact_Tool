"""
Pure-logic encode/decode for the screenshot-transfer page format.

No image processing here — this module only deals with bytes and strings,
so it can be unit-tested without ever rendering or reading an image. The
image layer (glyph_match.py / decode_pages.py) is responsible for turning
a screenshot into the same page strings this module expects.
"""
import hashlib
import lzma
import zlib
from dataclasses import dataclass
from typing import Dict, List

import page_format as fmt

# lzma's default dictionary at preset 9 is 64 MiB — comfortably covers a
# whole multi-MB CAD/FEA file, so it can exploit repeats anywhere in the
# file. gzip/zlib's window is a fixed 32 KiB, so on a file bigger than
# that (any real .x_t/.inp) it structurally cannot see repetition beyond
# 32 KiB apart. That's the whole reason for picking lzma here, not just a
# "usually a bit smaller" default.
_COMPRESS = lambda data: lzma.compress(data, preset=9 | lzma.PRESET_EXTREME)
_DECOMPRESS = lzma.decompress

_CHAR_TO_VAL = {c: i for i, c in enumerate(fmt.ALPHABET)}


def crockford_encode(data: bytes) -> str:
    # `value` must be masked back down to just the unconsumed `bits` after
    # each byte — without it, `value` never shrinks and keeps absorbing
    # the whole input as one ever-growing Python int, turning every
    # shift/mask into an O(n)-bit operation and the whole loop into O(n^2)
    # (measured: 100 KB of incompressible data took ~26s instead of <0.1s).
    bits = 0
    value = 0
    out = []
    for byte in data:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(fmt.ALPHABET[(value >> bits) & 0x1F])
        value &= (1 << bits) - 1
    if bits > 0:
        out.append(fmt.ALPHABET[(value << (5 - bits)) & 0x1F])
    return "".join(out)


def crockford_decode(s: str) -> bytes:
    # Same fix as crockford_encode: mask `value` back down each step so it
    # stays a small int instead of silently growing across the whole input.
    bits = 0
    value = 0
    out = bytearray()
    for ch in s:
        v = _CHAR_TO_VAL.get(ch)
        if v is None:
            raise ValueError(f"character {ch!r} is not in the page alphabet")
        value = (value << 5) | v
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((value >> bits) & 0xFF)
        value &= (1 << bits) - 1
    return bytes(out)


def crc32_hex(data: bytes) -> str:
    return format(zlib.crc32(data) & 0xFFFFFFFF, "08X")


def build_pages(data: bytes) -> List[str]:
    """Encode `data` into a list of fixed-width page strings (one string
    per page = prefix + body, all concatenated). Mirrors
    remote_side/generate_pages.py exactly; keep both in sync."""
    compressed = _COMPRESS(data)
    payload = crockford_encode(compressed)
    sha256 = hashlib.sha256(data).hexdigest().upper()
    orig_len = len(data)
    compressed_len = len(compressed)

    cap = fmt.BODY_CAPACITY_PER_PAGE
    total_pages = max(1, -(-len(payload) // cap))  # ceil div

    pages = []
    for idx in range(total_pages):
        chunk = payload[idx * cap : (idx + 1) * cap]
        page_len = len(chunk)
        body = chunk.ljust(cap, fmt.FILL_CHAR)
        page_crc = crc32_hex(chunk.encode("ascii"))

        prefix = (
            fmt.MAGIC
            + format(idx, "04X")
            + format(total_pages, "04X")
            + format(page_len, "04X")
            + page_crc
            + format(orig_len, "010X")
            + format(compressed_len, "08X")
            + sha256
        )
        assert len(prefix) == fmt.PREFIX_WIDTH
        prefix = prefix.ljust(fmt.PREFIX_CAPACITY, fmt.FILL_CHAR)

        pages.append(prefix + body)
    return pages


@dataclass
class PageInfo:
    index: int
    total: int
    payload: str
    crc_ok: bool
    orig_len: int
    compressed_len: int
    sha256: str


def parse_page_string(page_str: str) -> PageInfo:
    """Parse one decoded page's raw character grid (already assembled by
    the image layer, row by row) into a PageInfo. Does NOT raise on CRC
    mismatch — caller decides what to do with a bad page."""
    prefix = page_str[: fmt.PREFIX_WIDTH]
    body = page_str[fmt.PREFIX_CAPACITY :]

    def field(name: str) -> str:
        lo, hi = fmt.PREFIX_OFFSETS[name]
        return prefix[lo:hi]

    magic = field("MAGIC")
    if magic != fmt.MAGIC:
        raise ValueError(f"bad magic {magic!r}, not a screenshot_transfer page")

    idx = int(field("PAGE_INDEX"), 16)
    total = int(field("PAGE_TOTAL"), 16)
    page_len = int(field("PAGE_LEN"), 16)
    page_crc = field("PAGE_CRC32")

    chunk = body[:page_len]
    crc_ok = crc32_hex(chunk.encode("ascii")) == page_crc

    orig_len = int(field("ORIG_LEN"), 16)
    compressed_len = int(field("COMPRESSED_LEN"), 16)
    sha256 = field("SHA256")

    return PageInfo(idx, total, chunk, crc_ok, orig_len, compressed_len, sha256)


def assemble(pages: Dict[int, PageInfo]) -> bytes:
    """Reassemble the original file from a {index: PageInfo} map that
    already contains every index in range(total), all crc_ok. Raises
    ValueError with a clear message if anything is inconsistent."""
    if not pages:
        raise ValueError("no pages to assemble")

    totals = {p.total for p in pages.values()}
    if len(totals) != 1:
        raise ValueError(f"pages disagree on total page count: {totals}")
    total = totals.pop()

    shas = {p.sha256 for p in pages.values()}
    if len(shas) != 1:
        raise ValueError("pages disagree on target SHA-256 — mixed captures from different files/runs?")
    expected_sha = shas.pop()
    expected_orig_len = {p.orig_len for p in pages.values()}.pop()
    expected_compressed_len = {p.compressed_len for p in pages.values()}.pop()

    missing = [i for i in range(total) if i not in pages]
    if missing:
        raise ValueError(f"missing pages: {missing}")

    bad = [i for i, p in pages.items() if not p.crc_ok]
    if bad:
        raise ValueError(f"pages failed CRC check, re-capture these: {sorted(bad)}")

    payload = "".join(pages[i].payload for i in range(total))
    compressed = crockford_decode(payload)[:expected_compressed_len]
    data = _DECOMPRESS(compressed)

    if len(data) != expected_orig_len:
        raise ValueError(
            f"reassembled length {len(data)} != expected {expected_orig_len}"
        )
    actual_sha = hashlib.sha256(data).hexdigest().upper()
    if actual_sha != expected_sha:
        raise ValueError(f"SHA-256 mismatch: got {actual_sha}, expected {expected_sha}")

    return data
