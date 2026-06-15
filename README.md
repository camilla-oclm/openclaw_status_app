# OpenClaw Status

**Should you update to the latest OpenClaw release?** This tool answers that.

[![OpenClaw release status](https://clawstat.us/badge.svg)](https://clawstat.us)

<p align="center">
  <img src="docs/hero-dark.png" alt="The OpenClaw Status decision page: a Skip-this-version verdict for v2026.6.6, with stats and an evidence-backed thesis" width="820">
  <br>
  <em>The generated decision page — a verdict-first dashboard backed by scored, version-relevant bug evidence (<a href="docs/hero-light.png">light theme</a>).</em>
</p>

It watches the [`openclaw/openclaw`](https://github.com/openclaw/openclaw) repo, scouts
the bugs people are actually hitting after a release — ranked by community impact and by
whether they affect the version being assessed — asks an LLM to weigh the evidence, and
renders a single decision page with a clear verdict:

| Verdict | Meaning |
|---------|---------|
| ✅ Update now | No blocking issues found |
| ⚠️ Update with precautions | Worthwhile, but back up first — real risk remains |
| ⏸️ Skip this version | The open issues outweigh the benefits |
| 🔄 Wait for next release | Fixes are already lined up in a pre-release |

The verdict isn't a vibe — it's the end of an evidence pipeline that scouts real post-release
bug reports, scores them against the repo's own severity labels, and has **two different LLM
providers** argue it out before anything ships.

### Highlights

- **Independent multi-model review.** The analyst and validator are *different* providers
  (DeepSeek + Qwen), with a third (MiniMax) as fallback — so no model rubber-stamps its own
  reasoning, and a single-vendor outage can't sink a run.
- **Evidence-ranked issue scouting.** Three GitHub searches (all sorted by 👍) are scored from
  the repo's real `P0…P4` / breakage / harm-area labels and ranked by severity *blended with
  whether the bug affects the assessed version* — a confirmed regression outranks a critical
  about some other release.
- **Safe-by-construction frontend.** A zero-dependency static page that builds its DOM with
  `textContent` (XSS-safe) and no inline handlers (CSP-clean) — even though every field is
  untrusted LLM text.
- **Ships only trustworthy pages.** A deploy guard refuses low-confidence or invalid
  assessments, an HTML smoke test runs *before* the old page is overwritten, and each outgoing
  page is archived to a browsable per-version snapshot.
- **Built for humans *and* machines.** The same verdict ships as an interactive page, a JSON API
  (`latest.json`), an RSS feed, a status badge, and an **agent-readable mirror** (`llms.txt` /
  `llms-full.txt`) — plus server-rendered HTML + JSON-LD so search engines and LLM agents can read
  the answer without executing JavaScript.
- **Cost-aware.** Every run logs cost + latency with daily/monthly budget alerts (a few cents/run
  typically, up to ~$0.08 when the validator disagrees and the analyst refines).
- **Hermetic test suite.** 165+ network-free tests gate CI on every push.

**Live demo: <https://clawstat.us>** — running on an AWS Lightsail box: a systemd timer pulls
the latest code and runs the full collect → assess → render pipeline every few hours, and Caddy
serves the result over auto-HTTPS. The whole host is scripted in [`deploy/`](deploy/) (one
`provision.sh` run).

---

## How it works

The app is a small Python package (`openclaw_status/`) driven by one CLI. Each run flows
through three stages: **collect → assess → render**.

```
 GitHub API (issues + releases, via token)
 npm registry                                ┐
 Clawsweeper-state (raw GitHub files)        │
        │                                    │
        ▼                                    │
 collect ──► data/raw-data.json              │  data sources
        │                                    │
        ▼                                    ┘
 assess  ──► data/assessment.json   (OpenRouter LLM: analyst → validator → refine)
        │            └─► data/history.json (per-version verdicts), data/timeline.json
        │                (per-run metric snapshots → Trends), data/usage.json (cost log)
        ▼
 render  ──► web/index.html         (public decision page)
```

### 1. Collect — `openclaw_status/collector.py`

Gathers everything from first-party sources only (no third-party data brokers):

- **GitHub issues** — scouted and scored (see below). *GitHub API, token.*
- **GitHub releases** — the latest stable, the most recent pre-release, and a short
  release history for the timeline. *GitHub REST API, token.*
- **npm registry** — the latest published version (release-detection signal). *public.*
- **Clawsweeper-state** — an automated triage bot's per-issue verdicts (`decision`,
  `fixed_release`) plus its work-candidate / recently-closed lists. *raw GitHub files.*

Output: `data/raw-data.json`, with a completeness gate (aborts if both npm and the GitHub
release fail) and a pipeline timeout.

### 2. Issue scouting — `openclaw_status/github.py` (the core)

This is what makes the verdict trustworthy. For the assessed version it runs three GitHub
searches, **all sorted by 👍 reactions** and excluding feature requests:

1. issues opened **since the release** (candidate regressions — not gated on any `bug`
   label, so freshly-filed, un-triaged breakage is still caught),
2. issues the maintainers flagged **top priority** (`label:P1`),
3. the **most-reacted open issues** overall (ongoing majors of any age).

Each issue is then scored from the repo's real labels:

- **Severity** comes from the maintainer **priority labels** `P0…P4`, bumped one level for
  a breakage label (`regression`/`crash`/`data-loss`) and floored at *high* for a serious
  harm area (`impact:security` / `data` / `message-loss` / `session-state` / `auth-provider`).
  *(The `issue-rating: 🦞 diamond lobster` label is a quality rating — it appears on feature
  requests too — so it is **not** treated as a severity.)*
- **Impact** = a bucket from 👍 reactions + comment volume.
- **`affects_version`** = the issue text mentions the assessed version or its minor series.
- **Category** = `regression` (a *confirmed* regression — a `regression` label or a
  "regression" title; not merely any post-release bug) / `post_release` (filed after the
  release and affects this version, but not confirmed as a regression) / `diamond_lobster` /
  `active`.

Results are **ranked by severity blended with version-relevance** (an issue confirmed in the
assessed version outranks a critical about some *other* version, but a trivial version
mention can't outrank a real critical), tie-broken by community impact. Feature requests and
proposals are dropped — a wished-for feature is no reason to skip an update. Finally, an
issue is marked **fixed** only if the release/pre-release body explicitly closes it
(`fixes/closes/resolves #N`), not for any bare `#N` (usually a PR number).

### 3. Assess — `openclaw_status/agent.py`

A multi-step LLM pipeline over [OpenRouter](https://openrouter.ai):

1. **Analyst** (`deepseek/deepseek-v4-pro`, high reasoning) produces a structured
   assessment from the collected data. Only the top-N issues by rank are fed to the
   prompt (`config.MAX_ISSUES_IN_CONTEXT`) and the output budget is widened
   (`config.ASSESSMENT_MAX_TOKENS`) so the JSON doesn't truncate on busy releases.
2. **Validator** (`qwen/qwen3.7-plus`) — a *different* provider from the analyst, so it's
   an independent second opinion, not the model checking its own work. Flags missed issues /
   unsupported claims.
3. **Refine** (analyst again) — only if the validator disagrees.

If the analyst call fails, it falls back to `minimax/minimax-m3` — a third distinct provider,
so a single-vendor outage doesn't sink the run (and the analyst and validator stay on
different models). All models are served via OpenRouter.

The output is schema- and XSS-validated, appended to `data/history.json`, and cost/latency
is logged to `data/usage.json` (with daily/monthly budget alerts). Result shape:
`recommendation`, `confidence`, `thesis`, `evidence` (for/against/neutral), `known_issues`,
`changes` (fixes/features/breaking), `sentiment_summary`, `platform_impact`.

### 4. Render — `openclaw_status/render.py`

- **`web/index.html`** — the public decision page. Pipeline data is injected into the
  `web/template.html` template via a `<script type="application/json">` block. The template
  is a zero-dependency, dark/light, mobile-responsive page that builds its DOM with
  `textContent` (XSS-safe) and no inline handlers (CSP-clean). A deploy guard refuses to
  publish a low-confidence or invalid assessment, and a smoke test validates the HTML
  before it overwrites the previous page. Beyond the verdict + key-metric tiles, the page
  carries data-viz sections derived from the scored issues:
  - **Platform impact** and **Component health** — per-surface (Windows/macOS/Linux/Discord/
    Slack/Telegram) and per-subsystem (Gateway/Models/Memory/Sessions/Auth/Channels/Plugins/
    Agents/Tasks/Tools/Build) meters, each encoding **issue volume** (bar length) × **worst
    severity** (colour). Platform tags come from the analyst when present, else a deterministic
    `render._derive_*` backfill.
  - **Your setup** — pick the platforms, channels and components you run; the verdict is
    re-scored to your stack and the matching issues highlighted (cross-cutting "all-platform"
    issues shown once in a shared row).
  - **Trends** — a 2×2 grid of time-series charts (issue pressure, severity mix, verdict, and
    regression share) built from the per-run `timeline.json` (below).
- **`web/latest.json`** — the same payload written as a sibling file. The page renders from the
  inlined copy instantly, then `fetch()`es `latest.json` and re-renders if it's fresher — so a
  data refresh doesn't need a full HTML rebuild, while `file://` / offline viewing still works
  from the inlined copy.
- **Per-run time series.** `data/timeline.json` gets one append-only snapshot every run (version,
  verdict, confidence, issue/regression/severity counts) — *not* deduped by version, so a release
  re-assessed each 6h becomes a curve, not a point. It's the data behind the Trends charts; until it
  has ≥2 points the charts fall back to a coarse per-version series from `history.json`.
  (`history.json` stays one row per version — it powers "Past verdicts".) Per-run cost & latency are
  also logged on disk for budget tracking but are **kept out of the public payload**.
- **Browsable history.** Instead of discarding the outgoing page, each render snapshots it to
  `web/archive/<version>.html` (named from the version it was built for) and the "Past verdicts"
  timeline links every entry that has a snapshot. Past-version snapshots **self-canonicalise** (so
  each can be indexed for its own "openclaw vX" query); the snapshot of the current version keeps
  `canonical → /` to avoid a homepage duplicate. Retention is capped (`config.ARCHIVE_KEEP`,
  default 30); if a page's version can't be read, it falls back to a single `*.html.prev`
  rollback copy. Caddy already serves `web/`, so the archive is reachable with no extra config.
- **Shareable artifacts.** Each render also writes an RSS feed and an embeddable badge next to the
  page (all static, served by Caddy):
  - **`web/feed.xml`** — an RSS feed of verdicts (one item per tracked version). Subscribe at
    `https://clawstat.us/feed.xml`.
  - **`web/badge.svg`** — a self-contained shields-style status badge. Embed the live verdict in a
    README: `[![OpenClaw status](https://clawstat.us/badge.svg)](https://clawstat.us)`.
  - **`web/latest.json`** is also a documented public **JSON API** — the full assessment payload
    (`version`, `recommendation`, `confidence`, `thesis`, `known_issues`, `changes`, …). Poll it
    instead of scraping the page.
  - **`web/llms.txt`** + **`web/llms-full.txt`** — an **agent-readable mirror** ([llms.txt](https://llmstxt.org)
    convention). The page is JS-rendered, so an LLM/agent (e.g. an OpenClaw agent deciding whether to
    self-update) can read `https://clawstat.us/llms.txt` for the current verdict + links, or
    `llms-full.txt` for the entire assessment as clean markdown — no HTML/JS to parse. The page's
    `<head>` advertises both via `<link rel="alternate">`.
- **SEO / crawlability.** Because the page builds its body in JS, each render also injects
  search-engine signals into the static HTML (every field HTML-escaped — same XSS-safe rule as the
  DOM side):
  - a **dynamic `<title>` + `<meta name="description">`** carrying the version + verdict + headline,
    a **canonical** link, and **Open Graph / Twitter** cards (preview image `web/og.png`);
  - a **server-rendered answer** inside `#app` (a real `<h1>Should you update OpenClaw vX? — …</h1>`,
    the headline, why-this-verdict, and top issues) so the verdict is crawlable without running JS —
    the script clears `#app` and rebuilds the interactive page on load;
  - **JSON-LD** structured data (`WebSite` + `WebPage` + a `FAQPage`) — version-specific questions
    ("Should you update OpenClaw vX?") plus evergreen, version-agnostic ones ("Should I update
    OpenClaw?" / "How do I know if a new release is safe to update to?") to match generic intent;
  - **`web/robots.txt`** + **`web/sitemap.xml`** (homepage + every archived version), emitted each
    render. Caddy serves unknown paths as real `404`s (no SPA fallback) to avoid soft-404 duplicates.

---

## Setup

Requires **Python 3.10+** (no other system tools — all HTTP uses the standard library).

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the two keys
```

`.env` (gitignored) needs:

- **`OPENROUTER_API_KEY`** — for the LLM assessment. Get one at openrouter.ai.
- **`GITHUB_TOKEN`** — for all GitHub reads. Least privilege is a **fine-grained PAT** with
  *Repository access → Public repositories (read-only)* and *Issues: Read-only* +
  *Metadata: Read-only* (or a classic token with **no scopes**). See `.env.example`.
- **`ALERT_WEBHOOK_URL`** *(optional)* — a Slack or Discord incoming webhook. When set,
  cost/budget/failure alerts are POSTed there (the payload key is auto-selected: Discord
  gets `content`, everything else `text`). Leave blank for stdout-only alerts. **It's a
  secret** (the URL embeds a token) — keep it in `.env`, never in git.

> Note: GitHub's UI only exposes the per-permission tab once you pick a repository scope, so
> selecting *All repositories* (read-only) is fine too — it grants no more than public read.

### Usage

```bash
python3 run.py collect             # gather data            → data/raw-data.json
python3 run.py assess              # LLM assessment         → data/assessment.json
python3 run.py render-assessment   # public page            → web/index.html
python3 run.py full                # collect → assess → render-assessment (concurrency-locked)
python3 run.py notify-test ["msg"] # send a test alert to ALERT_WEBHOOK_URL (verify the webhook)
```

A full run takes **~2–5 min** end-to-end, almost all of it the analyst/validator LLM
reasoning (longest when the validator disagrees and the analyst refines); collect and
render are seconds. Cost is a few cents/run typically, up to ~$0.08 on a refinement run.

To preview the page, open `web/index.html` in a browser.

### Tests

```bash
python3 -m pytest        # 165+ tests, hermetic (no network)
```

The suite covers the scouting/scoring logic, input sanitization, the assessment-output
validator, the data-injection contract, and the HTML smoke test.

---

## Deploy (self-host)

The live site at <https://clawstat.us> runs on a small Ubuntu VM (AWS Lightsail): a systemd
timer rebuilds the page every 6h and Caddy terminates HTTPS. The whole host is scripted in
[`deploy/`](deploy/) (provision script + systemd unit/timer + Caddyfile). On a fresh box with
this repo cloned to `/opt/openclaw_status_app`:

```bash
sudo deploy/provision.sh <your-domain>     # deps + Caddy + venv + the systemd timer
sudo nano /opt/openclaw_status_app/.env    # OPENROUTER_API_KEY + GITHUB_TOKEN (+ ALERT_WEBHOOK_URL)
sudo -u openclaw /opt/openclaw_status_app/.venv/bin/python run.py full   # seed the first page
```

Point the domain's DNS A-record at the box and open ports 80/443 — Caddy issues the TLS cert
automatically. After that, the timer pulls `main` and reruns the pipeline every 6h, so shipping
a change is just `git push`. Useful on-box commands: `journalctl -u openclaw-status.service -f`
(logs), `systemctl list-timers openclaw-status.timer` (schedule), `sudo systemctl start
openclaw-status.service` (deploy now). Changes under `deploy/` need a re-run of `provision.sh`
to reinstall the `/etc` copies.

---

## Project layout

```
openclaw_status_app/
├── run.py                  entry point
├── requirements.txt
├── .env.example            template for the two API keys
├── openclaw_status/
│   ├── cli.py              the unified CLI
│   ├── collector.py        stage 1 — gather data
│   ├── github.py           GitHub API client + issue scouting/scoring
│   ├── agent.py            stage 2 — LLM assessment pipeline
│   ├── render.py           stage 3 — public decision page
│   ├── lib.py              shared utils (OpenRouter, sanitize, locks, usage, timer)
│   └── config.py           paths, models, env
├── web/
│   ├── template.html       production frontend template (data injected here)
│   ├── index.html          generated public page (gitignored)
│   ├── latest.json         generated runtime-fetch payload (gitignored)
│   └── archive/            per-version page snapshots (gitignored)
├── docs/                   README screenshots (hero-dark.png / hero-light.png)
├── deploy/                 AWS provisioning: provision.sh, systemd unit+timer, Caddyfile
├── .github/workflows/      ci.yml (hermetic tests on every push)
├── tests/                  pytest suite
└── data/                   pipeline outputs (gitignored)
```

---

## Status / next steps

- **Live — done.** Deployed at **<https://clawstat.us>** on an AWS Lightsail VM (Route53 DNS,
  Caddy auto-HTTPS), self-updating every 6h via a systemd timer that pulls + runs `run.py full`.
- **Alerting — live.** A Discord webhook (`ALERT_WEBHOOK_URL`) gets a run-completion confirmation
  (verdict + this-run cost + running daily/monthly totals) plus alerts on cost thresholds, the
  budget gate, and assessment failures. A hard budget gate stops runaway spend.
  Verify any time with `run.py notify-test`.
- **Runtime data refresh — done.** The page reads `latest.json` at runtime (inlined copy as
  fallback), so data refreshes without rebuilding the whole HTML.
- **Reproducible host.** The whole box is scripted in [`deploy/`](deploy/) (provision script +
  systemd unit/timer + Caddyfile) — one `sudo deploy/provision.sh clawstat.us` from a fresh
  Ubuntu instance.
