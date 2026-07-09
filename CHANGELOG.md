# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
