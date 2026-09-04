#!/usr/bin/env python3
"""Subset a variable font to a self-hosted woff2 for web/fonts/ (dev-only tool).

The page ships its own fonts (no CDN, works from file:// and under the strict CSP), so
each face is a small subset built once here and committed. Needs fontTools with woff2
support — install `requirements-dev.txt` into the dev venv; nothing at render time uses
this, and the deploy box never installs it.

    .venv/bin/python tools/subset_font.py SRC.ttf web/fonts/Name-var.woff2
    .venv/bin/python tools/subset_font.py SRC.ttf OUT.woff2 --wght 300:800
    .venv/bin/python tools/subset_font.py SRC.ttf OUT.woff2 --ranges latin
    .venv/bin/python tools/subset_font.py SRC.ttf OUT.woff2 --features tnum,case,ss01

- `--ranges` picks the Unicode coverage: `latin` (Google Fonts' latin block: ASCII,
  Latin-1, general punctuation, € ™ arrows, minus), `latin-ext` (Google Fonts' latin-ext:
  Latin Extended-A/B, IPA, Latin Extended Additional, currency, Extended-C/D) and
  `latin-ext-core` (the everyday slice of latin-ext — Extended-A, Romanian ș/ț, spacing
  accents, ẞ, currency — at about a third of the bytes). Default: `latin,latin-ext-core`.
- Variable axes are kept (the page uses one file per family). `--wght LO:HI` narrows the
  weight axis to the range the page actually uses, which drops the deltas outside it — the
  biggest lever on a variable font's size after the character set.
- `--features` adds OpenType layout features to the subsetter's default keep-list (which
  already keeps kern, liga, calt, ccmp, locl, mark …). `tnum` is what
  `font-variant-numeric: tabular-nums` needs; `case` gives case-sensitive punctuation for
  all-caps labels.
- Hinting is dropped (variable fonts render unhinted on every modern rasteriser), the
  `head.modified` timestamp is left alone so re-running yields the same bytes, and the
  copyright + license name records are kept inside the file.

Print-out: the axes, glyph count and byte size of the result — check it against the
font budget in the design plan before committing.

Provenance of the committed subsets (source archives, not committed):
- Space Grotesk — github.com/floriankarsten/space-grotesk (OFL), latin subset (pre-dates
  this tool).
- Inter 4.1 — github.com/rsms/inter/releases/tag/v4.1, `InterVariable.ttf` (OFL):
  `--ranges latin,latin-ext-core --wght 400:800 --features tnum,case` → 63 KB with the
  whole opsz axis (14–32). For the record, the same cut with Google's full latin-ext is
  118 KB, and 170 KB with the weight axis whole — both past the fonts budget.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
except ImportError:  # pragma: no cover - guidance for a bare venv
    sys.exit("fontTools is not installed — run: .venv/bin/pip install -r requirements-dev.txt")

# Google Fonts' unicode-range definitions for the two Latin subsets, so the coverage
# matches what a `@font-face { unicode-range }` pair would declare.
RANGES = {
    "latin": (
        "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,"
        "U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD"
    ),
    "latin-ext": (
        "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,"
        "U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,"
        "U+2113,U+2C60-2C7F,U+A720-A7FF"
    ),
    # The part of latin-ext that shows up in issue titles and contributor names: Latin
    # Extended-A (Central/Eastern European, Turkish, Baltic, Welsh…), Romanian ș/ț,
    # the spacing modifier accents, ẞ, and the currency block. Leaves out IPA,
    # Extended-B, Latin Extended Additional (Vietnamese) and Extended-C/D, which are
    # ~70% of latin-ext's glyphs and bytes.
    "latin-ext-core": (
        "U+0100-017F,U+0218-021B,U+02BB-02BC,U+02C6-02CC,U+02D8-02DD,U+1E9E,U+2020,"
        "U+20A0-20C0,U+2113"
    ),
}


def parse_axis_range(spec: str) -> tuple[float, float]:
    lo, _, hi = spec.partition(":")
    if not hi:
        raise argparse.ArgumentTypeError("expected LO:HI, e.g. 300:800")
    return float(lo), float(hi)


def describe(font: TTFont) -> str:
    axes = ""
    if "fvar" in font:
        axes = ", ".join(f"{a.axisTag} {a.minValue:g}–{a.maxValue:g}" for a in font["fvar"].axes)
    return f"{len(font.getGlyphOrder())} glyphs; axes: {axes or 'none (static)'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path, help="source .ttf/.otf (a variable font is fine)")
    ap.add_argument("out", type=Path, help="output .woff2")
    ap.add_argument("--ranges", default="latin,latin-ext-core",
                    help="comma-separated subset names from: " + ", ".join(RANGES))
    ap.add_argument("--wght", type=parse_axis_range, metavar="LO:HI",
                    help="narrow the weight axis to this range (partial instancing)")
    ap.add_argument("--features", default="tnum,case",
                    help="extra OpenType features to keep on top of the defaults")
    args = ap.parse_args(argv)

    names = [r.strip() for r in args.ranges.split(",") if r.strip()]
    unknown = [n for n in names if n not in RANGES]
    if unknown:
        ap.error(f"unknown range(s): {', '.join(unknown)} — choose from {', '.join(RANGES)}")
    unicodes = subset.parse_unicodes(",".join(RANGES[n] for n in names))

    font = TTFont(args.src)
    print(f"source : {args.src} — {args.src.stat().st_size:,} bytes; {describe(font)}")
    if args.wght and "fvar" not in font:
        ap.error("--wght given but the source is not a variable font")

    opts = subset.Options()
    opts.flavor = "woff2"
    opts.hinting = False
    opts.notdef_outline = True
    opts.layout_features = sorted(set(opts.layout_features)
                                  | {f.strip() for f in args.features.split(",") if f.strip()})
    # Keep the name records the OFL asks to travel with the font (copyright, license,
    # license URL) alongside the family/style/version ones the subsetter keeps by default.
    opts.name_IDs = sorted(set(opts.name_IDs) | {0, 13, 14})
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)
    if args.wght:
        # Instance AFTER subsetting: the instancer rebuilds gvar with entries only for
        # glyphs that still vary, and the subsetter's gvar pass expects one per glyph.
        font = instancer.instantiateVariableFont(font, {"wght": args.wght}, inplace=True)
    font.flavor = "woff2"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    font.save(args.out)

    size = args.out.stat().st_size
    print(f"output : {args.out} — {size:,} bytes ({size / 1024:.1f} KB); {describe(font)}")
    print(f"ranges : {', '.join(names)} ({len(unicodes)} code points requested); "
          f"features kept on top of defaults: {args.features}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
