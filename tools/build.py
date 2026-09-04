#!/usr/bin/env python3
"""Build the page client (web-src/ → one IIFE) and inline it into web/template.html.

    npm ci                              # once: the pinned toolchain (Svelte + Vite)
    python3 tools/build.py              # vite build, then inline into the template
    python3 tools/build.py --check      # rebuild and compare with the committed template (CI)
    python3 tools/build.py --no-build   # inline the existing web-src/dist/app.js only

The template ships with the bundle already inlined — the deploy box has no Node and never
runs this; render.py only injects JSON into the committed template. Edit web-src/, run this,
commit both. `--check` exits 1 when the committed template does not match a fresh build, so
CI catches a forgotten rebuild. The bundle sits between the two marker lines below; nothing
else in the template is touched.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "template.html"
BUNDLE = ROOT / "web-src" / "dist" / "app.js"
START = "<script>/* app-js:start — built from web-src/ by tools/build.py; do not edit by hand */"
END = "/* app-js:end */</script>"


def build_bundle() -> None:
    print("→ vite build")
    subprocess.run(["npx", "vite", "build", "--logLevel", "warn"], cwd=ROOT, check=True)


def inlined(template: str, bundle: str) -> str:
    a, b = template.find(START), template.find(END)
    if a < 0 or b < 0 or b < a:
        sys.exit("template markers not found — expected the app-js:start / app-js:end lines")
    # A literal "</script" inside the bundle would end the block early; escape it (harmless in JS).
    js = bundle.strip().replace("</script", "<\\/script")
    return template[:a] + START + "\n" + js + "\n" + END + template[b + len(END):]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="rebuild and fail if the committed template differs")
    ap.add_argument("--no-build", action="store_true", help="skip vite; inline web-src/dist/app.js as is")
    args = ap.parse_args(argv)

    if not args.no_build:
        build_bundle()
    if not BUNDLE.exists():
        sys.exit(f"no bundle at {BUNDLE} — run without --no-build")
    template = TEMPLATE.read_text(encoding="utf-8")
    out = inlined(template, BUNDLE.read_text(encoding="utf-8"))
    size = len(out.encode("utf-8"))
    if args.check:
        if out != template:
            sys.exit(f"web/template.html is out of date with web-src/ — run tools/build.py and commit the result "
                     f"(built {size:,} bytes vs committed {len(template.encode('utf-8')):,})")
        print(f"✓ web/template.html matches a fresh build ({size:,} bytes)")
        return 0
    if out == template:
        print(f"template unchanged ({size:,} bytes)")
        return 0
    TEMPLATE.write_text(out, encoding="utf-8")
    print(f"wrote {TEMPLATE.relative_to(ROOT)} ({size:,} bytes; bundle {BUNDLE.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
