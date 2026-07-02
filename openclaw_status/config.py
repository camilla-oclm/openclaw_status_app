"""
Central config: paths, constants, models, .env loading.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Project root ────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

# ── .env ────────────────────────────────────────────────────────────────────
load_dotenv(ROOT / ".env")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    print("⚠ WARNING: OPENROUTER_API_KEY not set. LLM calls will fail.", file=sys.stderr)

# GitHub token — REQUIRED. All GitHub data (issues + releases) is read via the
# GitHub API with this token. Needs only public read: a fine-grained PAT with
# Issues:Read + Metadata:Read, or a classic token with no scopes.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("⚠ WARNING: GITHUB_TOKEN not set. GitHub collection will fail.", file=sys.stderr)

# Optional: a Slack/Discord-style incoming webhook. When set, cost/failure alerts
# are POSTed to it (as {"text": ...}) in addition to stdout. Unset → stdout only.
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL")

# ── Repository ──────────────────────────────────────────────────────────────
REPO_OWNER = "openclaw"
REPO_NAME = "openclaw"
NPM_PACKAGE = "openclaw"
REPO_PATH = f"{REPO_OWNER}-{REPO_NAME}"

# ── Model config ────────────────────────────────────────────────────────────
# All models are served through OpenRouter. The analyst role uses deepseek-v4-pro
# (the strong sibling of the flash model this project was built on — same prompt,
# reasoning param, and JSON behaviour, a clear quality step up, still ~$0.009/run).
PRIMARY_MODEL = "deepseek/deepseek-v4-pro"
# One shared reasoning config for every role (analyst / validator / fallback). Effort is
# a parked cost lever — dropping a single role to "medium" means rebinding that role's name.
_REASONING_HIGH = {"effort": "high", "exclude": False}
PRIMARY_REASONING = _REASONING_HIGH
# Independent reviewer — deliberately a *different* model from the analyst, so it
# catches the primary's blind spots instead of rubber-stamping its own reasoning.
# qwen3.7-plus reasons, so the validator call gets the wide token budget too
# (see _step_validator) or its JSON would truncate like the analyst's did.
VALIDATOR_MODEL = "qwen/qwen3.7-plus"
VALIDATOR_REASONING = _REASONING_HIGH

# Fallback (used if the primary fails). minimax-m3 is a third distinct provider —
# different from both the deepseek analyst and the qwen validator — so a deepseek
# outage neither sinks the run nor collapses analyst+validator onto the same model.
# IDs are real OpenRouter slugs (provider/model) — a wrong slug returns HTTP 400
# and burns a retry, so keep them in sync with https://openrouter.ai/api/v1/models.
FALLBACK_MODELS = [
    {"model": "minimax/minimax-m3", "reasoning": _REASONING_HIGH},
]

# ── Retry & cost guardrails (consumed by lib.py — kept here so every tunable policy
#    knob lives in the central config, as this module's docstring promises) ─────────
MAX_RETRIES = 2
RETRY_BACKOFF = [1.0, 3.0]   # seconds between attempts
DAILY_COST_LIMIT = 2.0       # USD — alert threshold, not a hard stop
MONTHLY_COST_LIMIT = 30.0    # USD

# Assessment output budget. The analyst/refine steps emit a full JSON document
# (thesis + evidence + one known_issues entry per issue + changes), which blows
# past the 4k default and truncates mid-JSON → "Failed to parse JSON." Crucially,
# OpenRouter counts reasoning tokens against this cap too: a high-effort run burns
# ~4–6k tokens *just thinking* before any JSON, so the budget must cover reasoning
# + the full document. 16k clears both with margin (deepseek-v4-pro allows 384k
# output, qwen3.7-plus 65k). The validator reasons too, so _step_validator passes
# it this same budget (its JSON would otherwise truncate behind the reasoning tokens).
ASSESSMENT_MAX_TOKENS = 16000

# Cooperative wall-clock budget for the COLLECT phase (PipelineTimer, checked between
# fetches). Collection is normally seconds, but the issue scout now runs ~11 searches each
# with their own socket timeout, so allow headroom. See the TimeoutStartSec invariant below.
COLLECT_TIMEOUT_S = 480

# Wall-clock budget for the whole LLM pipeline (primary + validator + refine, incl.
# retries). Each openrouter_call is hard-bounded to the time left in this budget, so
# a trickling/hung response can't block forever — urllib's socket `timeout` is only a
# per-read idle timeout, not a total deadline, so a model that dribbles tokens resets
# it on every byte (this once hung a run ~17 min until systemd SIGKILLed it).
#
# INVARIANT (a `full`/`tick` runs collect+assess+render in one process):
#   COLLECT_TIMEOUT_S + PIPELINE_BUDGET_S + render margin  <  unit TimeoutStartSec
# so the in-process budgets always bow out gracefully (validator → "unreviewed" → publish
# primary, keep last good page) BEFORE systemd SIGKILLs the run with nothing published.
# With 480 + 900 + ~60 ≈ 1440 the unit's TimeoutStartSec is set to 1800 (deploy/*.service).
PIPELINE_BUDGET_S = 900

# Cap on how many issues are fed into the LLM prompt. The collector persists the
# full ranked set to raw-data.json; only the top-N by rank go to the model, which
# bounds both the input context (~1k chars/issue) and the known_issues output.
MAX_ISSUES_IN_CONTEXT = 30
# Reading tiers within the prompt's issue list (agent.build_context): tier 1 gets full
# detail and must drive the verdict, tier 2 is compact support, the rest one-liners.
CONTEXT_TIER_TOP = 8
CONTEXT_TIER_MID = 12

# Latency watch: a single LLM call at/over this many seconds gets flagged (log +
# webhook ping). High-reasoning analyst/validator calls normally run ~2–3 min;
# a call pushing past 5 min is drifting toward the PIPELINE_BUDGET_S wall, where
# runs start silently degrading (validator skipped → "unreviewed" single-model
# pages) long before anything errors. A heads-up only — never blocks the run.
SLOW_CALL_WARN_S = 300

# ── Data files ──────────────────────────────────────────────────────────────
RAW_DATA_FILE = DATA_DIR / "raw-data.json"
ASSESSMENT_FILE = DATA_DIR / "assessment.json"
USAGE_LOG_FILE = DATA_DIR / "usage.json"
HISTORY_FILE = DATA_DIR / "history.json"
# Per-RUN metric snapshots (append-only, not deduped by version) — the time series
# behind the "Trends" charts. One row every run, even when the version is unchanged.
TIMELINE_FILE = DATA_DIR / "timeline.json"
TIMELINE_KEEP = 240        # ~60 days at 4 runs/day
# ETag cache for GitHub REST responses (conditional requests → 304s don't re-download
# or count against the rate limit). Runtime state; gitignored.
ETAG_CACHE_FILE = DATA_DIR / "etag-cache.json"

# Per-version accumulating issue ledger. A released version is immutable (it won't be
# patched until the next release), so the issues affecting it only grow. Re-deriving
# "known issues" from a fresh GitHub scout every run made the list and the verdict
# flip-flop (a busy run surfaced 20 issues, a quiet one 7). The ledger upserts the
# version-relevant issues each run — reactions only climb, fix-status fills in — and
# never drops them, so the displayed set and its counts are deterministic and monotonic.
# Keyed by version. Runtime state; gitignored (data/ is ignored wholesale).
ISSUE_LEDGER_FILE = DATA_DIR / "issue-ledger.json"
LEDGER_MAX_ISSUES_PER_VERSION = 60   # cap per version (keep the highest-ranked)
LEDGER_KEEP_VERSIONS = 12            # prune the ledger to the most-recently-seen versions

# ── API endpoints ───────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_API_URL = "https://api.github.com"
GITHUB_RAW_URL = "https://raw.githubusercontent.com"

# ── Frontend ────────────────────────────────────────────────────────────────
# The renderer injects pipeline data into TEMPLATE_FILE via the
# <script id="assessment-data"> JSON contract and writes the public page to OUTPUT_HTML.
TEMPLATE_FILE = WEB_DIR / "template.html"
OUTPUT_HTML = WEB_DIR / "index.html"

# Public base URL of the deployed site — used in the RSS feed item links.
SITE_URL = "https://clawstat.us"
# This app's own public repo — the "report a problem" path (issues) + source link.
# The template carries its own copy (it's a static artifact); keep the two in sync.
APP_REPO_URL = "https://github.com/camilla-oclm/openclaw_status_app"
# Each render also writes sibling artifacts next to OUTPUT_HTML (paths derived via
# Path.with_name in render.py, so they stay together even for a custom output dir;
# Caddy serves web/, so all are reachable):
#   latest.json — the page payload, fetched at runtime so data refreshes without an
#                 HTML rebuild (the inlined copy is the file:// / offline fallback)
#   feed.xml    — RSS of verdicts (subscribe with no account)
#   badge.svg   — embeddable shields-style status badge
# All generated; gitignored.

# Browsable per-version snapshots of past pages. On each render the outgoing page
# is copied to ARCHIVE_DIR/<version>.html (recycling the old single .prev backup)
# and the history section links to it. Caddy serves web/, so /archive/<v>.html is
# reachable with no extra config. Retention is capped at ARCHIVE_KEEP (oldest pruned).
ARCHIVE_DIR = WEB_DIR / "archive"
ARCHIVE_KEEP = 30

# A just-published release is "fresh": the community hasn't filed version-specific
# bug reports yet, so the known-issues list is mostly carried over from earlier
# versions and the verdict is preliminary. We flag a release fresh for this many
# days after its publish date (relative to the assessment time) so the page can
# tell users to back up and treat the early verdict as provisional. At the ~6h run
# cadence this spans the first several re-assessments — long enough for reports to
# start landing and the picture to firm up.
FRESH_RELEASE_DAYS = 2

# Also retire the fresh-release banner once this version has been assessed MORE than
# this many times. By the 4th run (~24h at the 6h cadence) enough version-specific
# bugs have been filed that the verdict no longer leans on carried-over issues, so the
# "early read / preliminary" framing is stale even if the publish date is < 2 days old.
# Whichever fires first — this OR FRESH_RELEASE_DAYS — hides the banner. So with =3 the
# banner shows on runs 1–3 and hides from the 4th run onward.
FRESH_RELEASE_MAX_RUNS = 3

# ── Adaptive scheduling ─────────────────────────────────────────────────────
# A cheap hourly *tick* (systemd timer) polls GitHub for a new release and decides
# whether a full LLM assessment is due. Assessments are frequent while a release is
# fresh and back off as it ages and the verdict stabilizes. A genuinely new release
# is always assessed immediately (and resets the age clock to the fast tier).
#
# Tiers: (release_age_upper_bound_hours, assess_every_hours), first match wins; the
# final (None, …) tier is the floor. The 48h first boundary matches FRESH_RELEASE_DAYS.
# Intervals are tuned for cost: a NEW release is still caught within the hour by the
# tick (cheap, no LLM), so these only govern how often an ALREADY-seen release is
# re-assessed — its verdict is stable, so a fresh release gets ~3 reads/day and an aged
# one ~1/day. (Was 6/8/12; relaxed to 8/12/24 to keep monthly LLM spend in the $5–10 band.)
ASSESS_CADENCE_TIERS = [(48, 8), (96, 12), (None, 24)]
# Fire a touch early so an hourly tick never drifts a full slot late (timer jitter).
SCHEDULE_GRACE_H = 0.5
