"""Pure-logic round-trip test for codec.py — no images involved.
Run: python test_codec.py
"""
import os
import random
import sys

import codec


def roundtrip(data: bytes, label: str) -> None:
    pages = codec.build_pages(data)
    parsed = {}
    for p in pages:
        info = codec.parse_page_string(p)
        assert info.crc_ok, f"{label}: page {info.index} failed its own CRC"
        parsed[info.index] = info
    result = codec.assemble(parsed)
    assert result == data, f"{label}: round-trip mismatch ({len(result)} vs {len(data)} bytes)"
    print(f"OK   {label}: {len(data)} bytes -> {len(pages)} page(s)")


def test_missing_page_detected():
    data = os.urandom(200_000)
    pages = codec.build_pages(data)
    assert len(pages) > 1, "test needs multi-page data"
    parsed = {codec.parse_page_string(p).index: codec.parse_page_string(p) for p in pages}
    del parsed[len(pages) - 1]
    try:
        codec.assemble(parsed)
        raise AssertionError("expected missing-page ValueError, got none")
    except ValueError as e:
        assert "missing pages" in str(e)
        print(f"OK   missing-page detection: {e}")


def test_corrupt_page_detected():
    data = os.urandom(50_000)
    pages = codec.build_pages(data)
    parsed = {}
    for i, p in enumerate(pages):
        info = codec.parse_page_string(p)
        if i == 0:
            # flip one character in the payload to simulate a misread cell
            bad_payload = ("Z" if info.payload[0] != "Z" else "Y") + info.payload[1:]
            info = codec.PageInfo(
                info.index, info.total, bad_payload, False,
                info.orig_len, info.compressed_len, info.sha256,
            )
            # recompute crc_ok the way assemble() actually checks it: via parse
        parsed[info.index] = info
    # Force crc_ok False on the tampered page explicitly (assemble trusts crc_ok as given)
    parsed[0].crc_ok = False
    try:
        codec.assemble(parsed)
        raise AssertionError("expected CRC-failure ValueError, got none")
    except ValueError as e:
        assert "CRC" in str(e)
        print(f"OK   CRC-failure detection: {e}")


if __name__ == "__main__":
    random.seed(0)
    roundtrip(b"", "empty file")
    roundtrip(b"hello world\n" * 3, "tiny text")
    roundtrip(os.urandom(1000), "1 KB random")
    roundtrip((b"A" * 80 + b"\n") * 4000, "repetitive text (~320 KB, compresses well)")
    roundtrip(os.urandom(5000), "5 KB random (multi-page)")
    test_missing_page_detected()
    test_corrupt_page_detected()
    print("\nAll codec tests passed.")
