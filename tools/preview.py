#!/usr/bin/env python3
"""Build web/proto/preview.html — the real template with a real payload — for the design loop.

    .venv/bin/python tools/preview.py                          # live https://clawstat.us/latest.json
    .venv/bin/python tools/preview.py --data web/latest.json   # a local payload, or any URL
    .venv/bin/python tools/preview.py --data web/proto/latest.json   # re-use the last fetch (offline)
    .venv/bin/python tools/preview.py --out web/proto/other.html
    .venv/bin/python tools/preview.py --css web/proto/variant.css --out web/proto/variant.html   # A/B

The page is built with render's own injectors (`_inject_data` + `_inject_seo`), so it is
what `render_assessment_page` would publish for that payload, minus the deploy guard, the
smoke test and the sibling files (feed, badge, llms.txt …). The payload is also written
next to the page as `latest.json`: when the preview is served over http (tools/shot.cjs
does that) the page's runtime refresh then reads the same data instead of a stale copy.

`web/proto/` is gitignored — nothing built here is ever committed. Pair it with
tools/shot.cjs for screenshots; both are documented in tests/browser/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openclaw_status import config, render  # noqa: E402  (after the sys.path insert)

LIVE_URL = config.SITE_URL.rstrip("/") + "/latest.json"
DEFAULT_OUT = config.WEB_DIR / "proto" / "preview.html"


def load_payload(source: str) -> dict:
    """A local path or an http(s) URL → the parsed latest.json payload."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "openclaw-status-preview/1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as e:
            sys.exit(f"could not fetch {source}: {e}\n"
                     f"offline? re-use the last fetch with --data {DEFAULT_OUT.with_name('latest.json')}")
    return json.loads(Path(source).read_text(encoding="utf-8"))


def build_preview(data: dict, out: Path, extra_css: str = "") -> Path:
    html = config.TEMPLATE_FILE.read_text(encoding="utf-8")
    html = render._inject_data(html, data)
    html = render._inject_seo(html, data)
    if extra_css:
        # A/B hook: an override stylesheet after the page's own, never shipped.
        html = html.replace("</head>", "<style id=\"ab-css\">\n" + extra_css + "\n</style>\n</head>", 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    out.with_name("latest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=LIVE_URL,
                    help=f"payload: a latest.json path or URL (default: {LIVE_URL})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"where to write the page (default: {DEFAULT_OUT.relative_to(ROOT)})")
    ap.add_argument("--css", type=Path,
                    help="an override stylesheet to inject after the page's own (A/B variants; never shipped)")
    args = ap.parse_args(argv)

    data = load_payload(args.data)
    if not isinstance(data, dict) or not data.get("recommendation"):
        sys.exit(f"{args.data} does not look like a latest.json payload (no 'recommendation')")
    out = build_preview(data, args.out, args.css.read_text(encoding="utf-8") if args.css else "")

    status = (data.get("status") or {}).get("label") or data.get("recommendation")
    print(f"payload: v{data.get('version')} · {status} · assessed {data.get('assessed_at')} "
          f"· app {data.get('app_version', '?')} ← {args.data}")
    print(f"wrote  : {out} ({out.stat().st_size:,} bytes) + {out.with_name('latest.json').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
