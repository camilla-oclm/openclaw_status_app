# OpenClaw Status

**Should you update to the latest OpenClaw release?** This tool answers that.

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
- **Cost-aware.** Every run logs cost + latency with daily/monthly budget alerts (~$0.02–0.05/run).
- **Hermetic test suite.** ~100 network-free tests gate CI on every push.

**Live demo: <https://camilla-oclm.github.io/openclaw_status_app/>** — the decision page
rebuilds itself every few hours and deploys to GitHub Pages straight from CI: the full
collect → assess → render pipeline runs *inside the workflow*, so there's no always-on server.
The custom domain **[clawstat.us](https://clawstat.us)** is being pointed at it. An AWS Lightsail
host is also fully scripted as an alternative — see [`deploy/`](deploy/).

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
        │            └─► data/history.json (past verdicts), data/usage.json (cost log)
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
- **Category** = `regression` (post-release & version-relevant, or labelled regression) /
  `diamond_lobster` / `active`.

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
  before it overwrites the previous page.
- **`web/latest.json`** — the same payload written as a sibling file. The page renders from the
  inlined copy instantly, then `fetch()`es `latest.json` and re-renders if it's fresher — so a
  data refresh doesn't need a full HTML rebuild, while `file://` / offline viewing still works
  from the inlined copy.
- **Browsable history.** Instead of discarding the outgoing page, each render snapshots it to
  `web/archive/<version>.html` (named from the version it was built for) and the "Past verdicts"
  timeline links every entry that has a snapshot. Retention is capped (`config.ARCHIVE_KEEP`,
  default 30); if a page's version can't be read, it falls back to a single `*.html.prev`
  rollback copy. Caddy already serves `web/`, so the archive is reachable with no extra config.

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

A full run takes **~2–3 min** end-to-end (measured ~143 s), almost all of it the
analyst/validator LLM reasoning; collect and render are seconds. Cost ~$0.02–0.05/run.

To preview the page, open `web/index.html` in a browser.

### Tests

```bash
python3 -m pytest        # ~100 tests, hermetic (no network)
```

The suite covers the scouting/scoring logic, input sanitization, the assessment-output
validator, the data-injection contract, and the HTML smoke test.

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
├── .github/workflows/      ci.yml (tests) + pages.yml (build & deploy the live demo)
├── tests/                  pytest suite
└── data/                   pipeline outputs (gitignored)
```

---

## Status / next steps

- **Live demo — done.** The page deploys to GitHub Pages from CI on every push and every ~6h
  (`.github/workflows/pages.yml`), reachable at
  <https://camilla-oclm.github.io/openclaw_status_app/>. Pointing **clawstat.us** at it is a DNS step.
- **Alerting — live.** Cost/budget/failure alerts POST to a Discord webhook (`ALERT_WEBHOOK_URL`),
  with a hard budget gate to stop runaway spend. Verify any time with `run.py notify-test`.
- **Runtime data refresh — done.** The page reads `latest.json` at runtime (inlined copy as
  fallback), so data refreshes without rebuilding the whole HTML.
- **AWS host (optional alternative).** A self-updating Lightsail VM is fully scripted in
  [`deploy/`](deploy/) (provision script + systemd timer + Caddyfile) for when shell access on
  the host is wanted; it needs the operator's one-time AWS account + box.
