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

Then run either suite from the repo root:

```bash
node tests/browser/per_setup_verdict.test.js
node tests/browser/page_ui.test.js
```

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
