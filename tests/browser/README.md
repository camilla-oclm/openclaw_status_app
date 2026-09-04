# Browser tests (on-demand)

Headless tests that exercise the **real** client-side JS in `web/template.html` — logic that
can't be covered by the hermetic Python `pytest` suite (the deploy box has no Node, and a Python
re-implementation would risk drifting from the shipped JS).

These are **not** run by `pytest`. They need Node plus a puppeteer install and a
Chrome/Chromium binary — neither is committed (`package.json` is gitignored), so on a
fresh clone install one of the two supported shapes first:

```bash
npm i puppeteer            # bundles its own Chrome
npm i puppeteer-core       # lighter; drives a Chrome/Chromium you already have
```

Then run any suite from the repo root:

```bash
node tests/browser/per_setup_verdict.test.js
node tests/browser/page_ui.test.js
node tests/browser/contrast.test.js
```

- **`contrast.test.js`** — the accessibility floor as a test. It renders the real template with
  the shared payload in both themes, opens every panel, then samples every visible text run:
  the text colour (with effective opacity folded in) against the background found by
  alpha-compositing the ancestors' background colours, with the few gradient surfaces
  flattened to their base colour first. Every pair must reach WCAG AA — 4.5:1, or 3:1 for
  large text (≥ 24 px, or ≥ 18.66 px bold). Failures print the selector, the pair, the ratio
  and the size. `fixture.js` holds the toolchain discovery, the template and the payload the
  page-UI and contrast suites share.

- **`per_setup_verdict.test.js`** — audit **M8**. Drives the real `setupVerdict()` / `setupBlockers()`
  (via the guarded `window.__perSetupTest` hook) across a matrix and asserts the conservative
  per-setup invariants: softens by **at most one notch**, **never harsher** than the global verdict,
  **never ⏸️→✅**, **fresh never softens**, **no stack never softens**, and a **version-confirmed
  high/critical blocker** that hits the picked stack (or is cross-cutting `"all"`) blocks softening.

The toolchain is auto-discovered, in this order: an npx-cached `puppeteer`, a repo-local
`node_modules/puppeteer{,-core}`, a `~/.cache/puppeteer` Chrome, then system Chromium
(`/usr/bin/chromium` and friends). If discovery misses — an unusual install location, or
several Chromes and you want a specific one — point at them explicitly:

```bash
PUPPETEER_PATH=$PWD/node_modules/puppeteer-core CHROME_PATH=/usr/bin/chromium \
  node tests/browser/per_setup_verdict.test.js
```

`PUPPETEER_PATH` / `CHROME_PATH` always win over discovery; CI sets both.

## Building the client (`web-src/` → `web/template.html`)

The page's JavaScript is Svelte 5 components plus plain modules under `web-src/`, compiled by
Vite into one self-contained IIFE and inlined into the template between the `app-js` marker
lines. The suites drive the built template, so after any change under `web-src/`:

```bash
npm ci                          # once — the pinned toolchain (svelte, vite, the svelte plugin)
python3 tools/build.py          # vite build + inline; then commit web-src/ AND web/template.html
python3 tools/build.py --check  # what CI runs: rebuild and fail if the template has drifted
```

The deploy box never runs any of this: render.py only injects JSON into the committed
template. Static markup lives in the components; every data field is bound as text, never as
HTML (`{@html}` is not used anywhere), so the page stays XSS-safe by construction.

## Preview & screenshot harness (`tools/`)

Design work needs the real page with real data on screen. Two small dev tools do that —
neither runs at render time, and the deploy box never installs anything for them:

- **`tools/preview.py`** builds `web/proto/preview.html` (gitignored) from `web/template.html`
  plus a payload — the live `https://clawstat.us/latest.json` by default, or
  `--data <path-or-url>` — through render's own injectors, so it is exactly what
  `render_assessment_page` would publish for that payload (minus the deploy guard, the smoke
  test and the sibling files). The payload is written beside it as `latest.json`; offline,
  `--data web/proto/latest.json` re-uses the last fetch. `--css FILE` injects an override
  stylesheet after the page's own — the A/B hook (`--out web/proto/variant.html` keeps the
  base preview intact); nothing injected this way ships.
- **`tools/shot.cjs`** screenshots a local file or a URL with the same puppeteer/Chromium
  discovery as the suites (`PUPPETEER_PATH` / `CHROME_PATH` win). A local file is served over
  http from its own directory with `web/` as the fallback root, so `fonts/…` and
  `/logo.svg`-style paths resolve as in production (`file://` blocks the font) and the page's
  runtime `latest.json` fetch finds the copy `preview.py` wrote. Flags: `--size WxH`,
  `--dpr N`, `--theme dark|light`, `--full`, `--open` (the evidence toggle), `--click SEL`
  (repeatable, in-page click), `--scroll SEL`, `--wait MS`, `--motion` (animations are off by
  default through `prefers-reduced-motion`, so every `.reveal` is visible and shots are
  deterministic), `--allow-errors`. It exits 1 on an uncaught page error — the "no page
  errors" check of the per-phase loop comes for free.

The standard set the design plan asks for before a phase ships (paste-able; `/tmp/shots`
is scratch):

```bash
export PATH="$HOME/.local/bin:$PATH"          # if node is only on the login-shell PATH
.venv/bin/python tools/preview.py
P=web/proto/preview.html; O=/tmp/shots; S="node tools/shot.cjs"
for t in dark light; do
  $S $P $O/desktop-$t.png --theme $t
  $S $P $O/desktop-$t-evidence.png --theme $t --open --scroll "#details-body"
  $S "$P?stack=linux,discord,gateway" $O/desktop-$t-picked.png --theme $t
  $S $P $O/mobile-$t.png --theme $t --size 390x844 --dpr 2 --full
done
for i in 1 2 3 4; do
  $S $P $O/tab-$i.png --open --click ".ltabs[role=tablist] .ltab:nth-child($i)" --scroll ".ltabs[role=tablist]"
done
```

The README heroes (`docs/hero-*.png`) are the same tool at `--size 1200x1000 --dpr 2`.

### Fonts

`tools/subset_font.py` cuts a variable font down to the self-hosted woff2 the page ships from
`web/fonts/` — Unicode ranges (`latin`, `latin-ext`, `latin-ext-core`), an optional weight-axis
narrowing (`--wght 400:800`), extra OpenType features (`tnum`, `case`) — and prints the size to
check against the plan's 90 KB all-fonts budget. It needs fontTools with woff2 support:

```bash
.venv/bin/pip install -r requirements-dev.txt      # dev seat only — never the box, never CI
.venv/bin/python tools/subset_font.py InterVariable.ttf web/fonts/Inter-var.woff2 --wght 400:800
```

Every committed face is OFL and ships with its license text next to it
(`web/fonts/<Family>-OFL.txt`); the exact cut of each one is recorded in the tool's docstring.

### Brand assets

`web/logo.svg` is the one source of the mark. Everything else is rendered from it or from a
small HTML card, so a brand change is a re-run, not a redraw:

```bash
S="node tools/shot.cjs"
for s in 16 32 64 512; do $S web/logo.svg web/logo-$s.png --size ${s}x${s} --transparent; done
$S web/logo.svg /tmp/icon-48.png --size 48x48 --transparent        # + 16/32 → favicon.ico (Pillow, sizes 16/32/48)
printf '<!doctype html><style>html,body{margin:0;background:#0c1017}img{width:180px;height:180px;display:block}</style><img src="/logo.svg">' > web/proto/touch.html
$S web/proto/touch.html web/apple-touch-icon.png --size 180x180     # full-bleed: iOS masks its own corners
$S tools/og-card.html web/og.png --size 1200x630                    # the share card
$S web/proto/preview.html docs/hero-dark.png  --size 1200x1000 --dpr 2 --theme dark   # README heroes
$S web/proto/preview.html docs/hero-light.png --size 1200x1000 --dpr 2 --theme light  # (quantize to 256 colours after)
```

The badge (`render._badge_svg`) carries the same mark geometry inline — keep it in step with
`logo.svg` when the mark changes.
