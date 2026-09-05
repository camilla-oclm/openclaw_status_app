# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-09-05

### Changed
- **The page comes alive as you scroll.** Sections used to fade in as whole blocks, and only
  during the first two seconds after load; after that nothing moved. Now a section fades in
  when it reaches the viewport, and the things inside it arrive one by one, 40 ms apart, as
  they scroll into view: the stat tiles, the why cards, the flip-condition tripwires, the
  analyst's paragraphs, the evidence cards, the component rows and both meter grids (each
  meter fills as its own card appears), the trends charts, the changelog rows, the
  track-record rows, the timeline entries and the known-issue rows — including rows that
  appear later through a filter or a tab switch. The safety net is visibility-based instead
  of a global timer: without IntersectionObserver everything shows at once; anything on
  screen the observer missed is revealed 1.8 s after render and after every scroll or resize;
  focusing a control reveals its row; printing reveals the whole page. Reduced motion still
  turns all of it off, and screenshots and the contrast audit run with it off.

## [1.3.0] - 2026-09-05

The premium craft pass: four phases on the page's type, motion, brand and charts, a contrast
audit that now runs as a test, and the client rebuilt as Svelte components compiled into the
template. The DOM contract, the data keys and every public surface (JSON, feed, badge,
llms.txt) are unchanged.

### Changed
- **The page client is now Svelte** (design pass, phase 5 — an architecture change with the same
  page). The 2,100-line hand-built client is a set of Svelte 5 components under `web-src/`
  (Hero, Setup, best version, Why, flip conditions, the evidence toggle with its tabs, Impact,
  Changes, Trends charts, track record, history, the issues list) over plain modules for the
  verdict machinery, formatting, chart geometry and state. Vite compiles them into one inline
  script that `tools/build.py` writes into the template between marker lines; the deploy box
  still needs only Python, the served page still loads nothing external, and archives stay
  single files. The DOM contract is unchanged: a structural diff of the rendered page against
  the previous client is identical at rest and with the evidence open, screenshots are
  pixel-identical across the standard set, and every suite passes untouched. `package.json` and
  the lockfile are committed (pinned toolchain, `npm ci`); CI gained a `client-build` job that
  rebuilds the bundle and fails when the template has drifted from the source.
- **Charts, light theme, contrast (design pass, phase 4).** The Trends charts draw
  monotone-cubic curves (through every run, never overshooting; stacked severity bands are
  clamped so they can't cross) over gradient area fills, with quieter dotted axes, legends as
  chips, the tracking-cap line as a tagged annotation, and one shared hover tip per chart —
  date, each series' value and the version assessed, snapped to the nearest run. Impact
  meters carry a tone gradient with an end cap; the verdict-by-component line is a compact
  table (glyph · component · verdict · count). The light theme gets its parity pass: glass on
  paper, a softer mesh, shadows tinted toward the page's blue. A **contrast audit** is now a
  browser suite (`tests/browser/contrast.test.js`): it samples every visible text run on the
  rendered page in both themes and asserts WCAG AA (4.5:1, or 3:1 for large text) — 232 runs
  per theme at this release, all passing; the browser suites share a fixture module.
- **Brand (design pass, phase 3).** One logomark for everything: a lobster claw drawn as an
  open C — a thick upper jaw, a thinner lower one, a hinge at the wrist — holding the status
  dot, on a dark rounded tile. `web/logo.svg` is the source; the header mark, the favicon
  set (`favicon.ico` 16/32/48, `logo-16/32/64/512.png`), the touch icon and the badge's mark
  are the same geometry, and the PNGs are rendered from the SVG by the screenshot tool
  (`tools/shot.cjs … --transparent`). The share image `web/og.png` is now a screenshot of
  `tools/og-card.html` — the page's fonts, mesh, mark and verdict glyphs — so it follows the
  design system and is reproducible with one command. The badge takes the page's card colour
  for its label segment, the claw mark in the accent, and verdict tones deepened for white
  text (`#1f8a5b` / `#a8782a` / `#c4404f`). README heroes re-shot from the new page.
- **Motion (design pass, phase 2).** One small vocabulary: an out-quint ease for settles,
  a spring (`linear()` where the browser has it, out-quint otherwise) for lifts, three
  durations (120 / 200 / 420 ms). The entrance is orchestrated — eyebrow and title, then the
  answer with a spring, then the line and the meta line, the setup card rising alongside with
  its tiles cascading 30 ms apart, the bento row last. The stat numerals count up the first
  time they render (a registered CSS integer interpolated from a starting style; the tile's
  real value stays as text, so nothing that reads the page sees a difference). Cards and tiles
  lift with a shadow on hover, compact controls press down for a beat, one focus-ring token.
  The long-tail tab switch goes through the View Transitions API where it exists (a root
  crossfade; nothing is named, so no thousands-of-pixels panel is ever snapshotted), with the
  same synchronous path otherwise, for the initial tab and for deep links; the evidence toggle
  stays synchronous and lets its sections fade in as before. `prefers-reduced-motion: reduce`
  still turns every animation, transition and view transition off, and nothing is gated on an
  animation finishing.
- **Type system (design pass, phase 1 — ships as 1.3.0-pre).** The page's text is now set
  in Inter (self-hosted, variable weight and optical size, the subset staged in the previous
  entry), with Space Grotesk kept for the status word, version numbers, titles and tiles. A
  metric-matched fallback (`Inter Fallback`, Arial re-proportioned with `size-adjust` and the
  ascent/descent overrides) means the swap to the web font moves nothing on screen; only the
  text face is preloaded. One six-step text scale, line-height by role (display / body / UI),
  optical sizing on, tabular numerals wherever figures line up. Body copy grows to 16 px.
- **Verdict glyphs of our own.** The four verdict marks (safe / care / skip / too new) are now
  inline stroke SVGs drawn in the page's icon language and tinted by their surface's tone,
  in the hero mark, the platform tiles, the best-version chips, the per-setup badge and rows,
  the verdict-by-component line, the track-record paths and the past-verdicts timeline. The
  emoji stays in the DOM as visually hidden text beside each glyph — it is the data key shared
  with the JSON API, llms.txt, the badge and RSS, and the browser suites read it back unchanged.
- **Less chrome.** The hero's chip row is one quiet meta line (confidence · fix staged ·
  second-model review · evidence gate) with the review expander kept; uppercase mono labels
  are down to three roles (the hero eyebrow, section kickers, table heads) and every other
  label is sentence case in the text face; platform tiles carry their tone in the glyph and a
  2 px bar behind a hairline border; the header's "release health" tag is gone; more air
  between the answer, the bento row and the evidence toggle. Blur surfaces drop from three to
  two.

### Added
- `tools/preview.py --css FILE` injects an override stylesheet into the preview for A/B variants.
- **Dev tooling for the design pass** (nothing here ships in the page). `tools/preview.py`
  builds `web/proto/preview.html` from the real template plus a real payload (the live
  `latest.json` by default) through render's own injectors; `tools/shot.cjs` screenshots it
  with puppeteer-core and a system Chromium (viewport, device-pixel ratio, theme, evidence
  open/closed, scroll and click targets, animations off by default; fails on an uncaught page
  error); `tools/subset_font.py` cuts a variable font down to a self-hosted woff2. The
  subsetter needs the new `requirements-dev.txt` (fontTools), which the deploy box and CI
  never install. All three are documented in `tests/browser/README.md`.
- **Inter staged, not yet used.** `web/fonts/Inter-var.woff2` — Inter 4.1, variable
  (weight 400–800, the whole optical-size axis), latin plus the everyday slice of
  latin-ext, 63 KB — with its OFL text next to it, ahead of the type-system work. The page
  does not reference it yet. Space Grotesk's OFL text now ships next to its woff2 as well.

## [1.2.0] - 2026-09-03

### Changed
- **The look.** A new design system for the answer-first page 1.1.0 introduced. A
  floating glass header carries section links (Answer · Best version · Why · Evidence)
  that light up as you scroll. The answer is set in the open, on a verdict-tinted mesh
  canvas with a fading dot grid: the status word large, in a gradient of its verdict
  colour, next to its glyph; beside it the per-platform strip becomes a **platform
  matrix** card — one tile per platform and channel with its verdict at a glance, a
  colour bar for the tone, an accent ring when picked. The best-version card and the
  "why" pair sit side by side as a bento row on desktop; the flip conditions read as a
  numbered grid; the long-tail tabs are a segmented control; cards, meters, issue rows
  and tiles share one translucent-surface recipe with a hairline top highlight. The light
  theme is reworked to match. Motion is a staggered fade-in of the answer and a pulse
  on the eyebrow dot, both behind `prefers-reduced-motion`. No new dependency or asset:
  the page is still one self-contained HTML file with the self-hosted Space Grotesk.
  Every id and class the client JS, the SSR fallback and the browser suites address is
  unchanged, so the suites pass untouched (496 pytest / 31 per-setup / 56 page UI).
- README screenshots re-shot from the new page.

## [1.1.1] - 2026-09-02

### Fixed
- **A model copying a Windows path into its JSON took the whole run down.** Issue
  #136123's body carries `C:\Users\…`; the analyst *and* the fallback both wrote it into a
  string verbatim, `\U` is not a JSON escape, both responses were rejected, and the
  scheduled run failed with nothing to publish (the last good page stood). `extract_json`
  now repairs the two defects strict `json.loads` rejects outright — a backslash that begins
  no escape sequence (doubled, text kept verbatim) and a raw control character inside a
  string — but only after a strict parse has failed, so well-formed output is never
  rewritten. The brace scan that isolates the document from surrounding prose now tries
  every `{` start (bounded) instead of only the first, so a quoted `${ENV_VAR}` placeholder
  in leading commentary can't hide the real object, and an inlined `<think>…</think>`
  block (a host that puts the reasoning in `content`) is dropped before parsing. Garbage
  still fails closed.
- **Parse failures are now diagnosable.** The full text of any response `extract_json`
  rejects is kept under `data/parse-failures/` (newest `config.PARSE_FAILURE_KEEP` files);
  until now the journal carried only the reason and the error dict a 1,000-char head. Every
  usage record also carries the OpenRouter host that served the call (`provider`) and why
  the generation stopped (`finish_reason`) — a failure that only ever happens on the
  production box, on a model fanned out over twenty hosts, was unreadable without them.

## [1.1.0] - 2026-09-02

The answer-first release. User feedback on 1.0 was blunt: too much data to navigate,
and in nine assessed releases the page never once named a version as safe. Both are
structural, so this release changes what the page *is*, not just how it looks.

### Added
- **Deterministic evidence gate** (`openclaw_status/verdict.py`): the verdict now starts
  from a floor computed in code — open high/critical issues confirmed for the version
  that a person or the community stands behind. ✅ is the expected verdict when the
  gate is clear; the analyst may only be *more* cautious, with a cited
  `gate_departure_reason` (new schema field), and the pipeline enforces the floor after
  the model answers. Persisted in `assessment.json` and published as `evidence_gate`.
- **Best version to run today** (`recommended_version` in `latest.json`, `llms.txt`, SSR
  and the page): the newest assessed release that has been in the field ≥ 7 days,
  wasn't rated skip, and shows no widespread breaker in its own ledger — so the site
  always names a concrete version, with a pinned `npm install` command.
- **Plain-language `status`** (`update` / `care` / `skip` / `wait`): "Too new to call"
  replaces the verdict word inside a release's fresh window; the wait state carries the
  early-read label it stands in for.
- Tests: `tests/test_verdict.py` (26) plus gate/floor/departure coverage in the agent
  and render suites; the page UI suite grew to 56 checks.

### Changed
- **The page.** The hero is the answer: a status word, one sentence naming which
  platforms should wait, and a **per-platform verdict strip** (the conservative
  per-setup verdict rendered for every surface) that doubles as the setup picker.
  Then the best version to run today, a "why" pair (credible blockers vs. what the
  release brings), and the flip conditions. Everything else — full reasoning, the
  second-model review, metric tiles, Impact meters, changelog, Trends, track record,
  past verdicts and the filterable known-issues list — sits behind one "Show the full
  evidence" toggle. Desktop page height dropped from ~6,600 px to ~2,500 px, mobile
  from ~10,600 px to ~4,300 px. The signal panel (gauge, platform sparkbars, risk
  sparkline), the fresh-release banner and the old "safest version" bar are gone;
  their facts moved into the answer.
- **Analyst rubric.** ⚠️ is no longer "the honest default": rule 15 and the
  recommendation guidelines now start from the Evidence gate, and the validator flags a
  departure from it with no cited reason as a logical error. The headline is asked for
  as one plain sentence ≤ 140 characters (it is now the hero's analyst line).
- One name per verdict on every surface, sourced from `verdict.STATUS`: **Safe to
  update / Update with care / Skip this version** (badge, RSS, llms, SSR, page).
  "Update now" and "Update with precautions" are retired spellings.

### Fixed
- In single-call mode the recorded `primary_recommendation` could be rewritten by a
  later in-place edit of the same dict; the model's first read is now captured up front.

## [1.0.0] - 2026-07-09

The first stable release. OpenClaw Status watches the
`openclaw/openclaw` repo, scouts and scores post-release bug reports, has two
independent LLM providers argue out a verdict, and renders a single decision
page — "should you update?" — plus machine surfaces (`latest.json`, `feed.xml`,
`badge.svg`, `llms.txt`, SSR + JSON-LD). It self-hosts on Ubuntu via `deploy/`
(a systemd tick timer + Caddy auto-HTTPS).

A full pre-1.0 release-gate review (33 findings) has been addressed — the changes
below.

### Added
- MIT `LICENSE` + README License section, this `CHANGELOG`, and an
  `APP_VERSION` / `__version__` constant surfaced as `latest.json`'s `app_version`.
- CI now runs the Node browser suites (per-setup verdict + page UI) on a
  Node+Chromium job, gating merges — the client runtime was previously untested
  in CI.
- Test coverage for the render happy path + all sibling artifacts, `collect()`
  end-to-end and the npm/clawsweeper fetchers, and the CLI wrappers/dispatch.
- `issues_capped` in `latest.json`: `true` when the known-issues list is
  saturated at the per-version ledger cap; the page, llms and SSR count
  surfaces then read "60+" so a pinned count doesn't read as "nothing new".

### Security
- The inline assessment-data `<script>` now escapes every `<` (not just `</`),
  closing a `<!--<script` breakout in a hostile GitHub issue title that could
  break the page. XML surfaces (feed/sitemap/badge) strip XML-illegal control
  bytes. Clawsweeper record fields are sanitized before the analyst prompt.
  `llms.txt` frames third-party text as data, not instructions.

### Fixed
- Verdict correctness: a wholly-failed issue scout now fails closed instead of
  reading as a clean release; `parallel_fetch` can't miscount coverage on a
  duplicate query; the ledger no longer truncates severity-bearing labels; a
  component-only, platform-empty blocker no longer false-spares a stack (server
  + client); the channel regex no longer false-fires on ordinary prose; the
  guaranteed-inclusion scout no longer drops a severe issue as a "feature".
- Cost/robustness: every billed LLM attempt is logged (non-string output and
  unparseable refine no longer drop spend); the pipeline lock race is closed and
  atomic writes are crash-durable; a corrupt etag cache degrades gracefully; the
  archive snapshot is best-effort (can't freeze the live page); the new-release
  scheduler backs off to bound a runaway.
- Frontend/consumer: the copy-link chip shares the actual picked stack; the
  per-component verdict is exposed to assistive tech; the documented permissive
  update-gate recipe is fail-closed.

### Changed
- `requirements.txt` dependencies are upper-bounded to block a breaking major.
- Docs corrected: PAT scope (`Contents: Read-only`), the adaptive cadence
  (8/12/24h), the webhook payload key, and the module layout.

[1.0.0]: https://github.com/camilla-oclm/openclaw_status_app/releases/tag/v1.0.0
