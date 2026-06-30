# Browser tests (on-demand)

Headless tests that exercise the **real** client-side JS in `web/template.html` — logic that
can't be covered by the hermetic Python `pytest` suite (the deploy box has no Node, and a Python
re-implementation would risk drifting from the shipped JS).

These are **not** run by `pytest`. Run them on demand where Node + Chrome/puppeteer exist:

```bash
node tests/browser/per_setup_verdict.test.js
```

- **`per_setup_verdict.test.js`** — audit **M8**. Drives the real `setupVerdict()` / `setupBlockers()`
  (via the guarded `window.__perSetupTest` hook) across a matrix and asserts the conservative
  per-setup invariants: softens by **at most one notch**, **never harsher** than the global verdict,
  **never ⏸️→✅**, **fresh never softens**, **no stack never softens**, and a **version-confirmed
  high/critical blocker** that hits the picked stack (or is cross-cutting `"all"`) blocks softening.

Override the puppeteer/Chrome paths with `PUPPETEER_PATH` / `CHROME_PATH` env vars if needed.
