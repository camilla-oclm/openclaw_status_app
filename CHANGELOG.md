# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Preparing the first stable release (`1.0.0`). OpenClaw Status watches the
`openclaw/openclaw` repo, scouts and scores post-release bug reports, has two
independent LLM providers argue out a verdict, and renders a single decision
page — "should you update?" — plus machine surfaces (`latest.json`, `feed.xml`,
`badge.svg`, `llms.txt`, SSR + JSON-LD). It self-hosts on Ubuntu via `deploy/`
(a systemd tick timer + Caddy auto-HTTPS).

### Added
- MIT `LICENSE` and a README License section.
- `app_version` field in `latest.json`, plus an `APP_VERSION` / `__version__`
  constant naming the tool build that produced a verdict.

### Changed
- `requirements.txt` dependencies are upper-bounded to block a breaking major
  release from slipping in on a fresh install.

_A full pre-1.0 release-gate review is being addressed; security, correctness,
and robustness fixes are folded in as they land (see git history for detail).
On the `1.0.0` tag, rename this `[Unreleased]` section to `[1.0.0] - <date>`._

[Unreleased]: https://github.com/camilla-oclm/openclaw_status_app/commits/main
