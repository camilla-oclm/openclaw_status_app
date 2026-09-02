# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
