"""
Reassemble a file from a folder of page screenshots.

Usage:
    python decode_pages.py --pages-dir <folder of screenshots> \
                            --reference glyph_reference.png \
                            --out reconstructed_file.bin

Screenshots can be named anything and given in any order — each page's
index is read from the image itself, not the filename. Re-run this after
adding/replacing screenshots for any pages it reports missing or failed.
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import codec
import glyph_match as gm


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages-dir", required=True, help="folder of captured page screenshots")
    ap.add_argument("--reference", required=True, help="glyph_reference.png path")
    ap.add_argument("--out", required=True, help="path to write the reconstructed file")
    ap.add_argument(
        "--warn-threshold", type=float, default=15.0,
        help="flag a page if its worst per-cell match score exceeds this, even if CRC passed (default: 15)",
    )
    args = ap.parse_args()

    reference_abspath = os.path.abspath(args.reference)
    image_paths = sorted(
        p for p in glob.glob(os.path.join(args.pages_dir, "*"))
        if p.lower().endswith((".png", ".bmp", ".jpg", ".jpeg", ".tif", ".tiff"))
        and os.path.abspath(p) != reference_abspath
    )
    if not image_paths:
        print(f"No images found in {args.pages_dir}")
        sys.exit(1)

    print(f"Loading reference glyphs from {args.reference} ...")
    reference = gm.GlyphReference(args.reference)

    pages = {}
    problems = []
    for path in image_paths:
        name = os.path.basename(path)
        try:
            page_str, worst = gm.decode_page_image(path, reference)
        except Exception as e:
            problems.append(f"{name}: could not read as a page image ({e})")
            print(f"  {name}: FAILED to read ({e})")
            continue

        try:
            info = codec.parse_page_string(page_str)
        except ValueError as e:
            problems.append(f"{name}: {e}")
            print(f"  {name}: FAILED to parse ({e})")
            continue

        flag = " [LOW CONFIDENCE]" if worst > args.warn_threshold else ""
        status = "OK" if info.crc_ok else "CRC MISMATCH"
        print(f"  {name}: page {info.index + 1}/{info.total} - {status} (worst cell score {worst:.1f}){flag}")

        existing = pages.get(info.index)
        if existing is None or (not existing.crc_ok and info.crc_ok):
            pages[info.index] = info
        elif existing.crc_ok and info.crc_ok and existing.payload != info.payload:
            problems.append(
                f"page {info.index}: two images both pass CRC but disagree on content - "
                f"unlikely CRC collision or a stale/mismatched screenshot, check {name}"
            )

    print()
    total_expected = None
    if pages:
        total_expected = next(iter(pages.values())).total

    if total_expected is not None:
        missing = [i for i in range(total_expected) if i not in pages]
        bad = sorted(i for i, p in pages.items() if not p.crc_ok)
        if missing:
            print(f"Missing {len(missing)}/{total_expected} page(s): {missing}")
        if bad:
            print(f"{len(bad)} page(s) failed CRC, re-capture: {bad}")

    if problems:
        print(f"\n{len(problems)} image(s) had problems:")
        for p in problems:
            print(f"  - {p}")

    try:
        data = codec.assemble(pages)
    except ValueError as e:
        print(f"\nNot reconstructed yet: {e}")
        sys.exit(1)

    with open(args.out, "wb") as f:
        f.write(data)
    print(f"\nSuccess: wrote {len(data)} bytes to {args.out} (SHA-256 verified).")


if __name__ == "__main__":
    main()
