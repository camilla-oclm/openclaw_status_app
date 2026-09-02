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

# ── App version ─────────────────────────────────────────────────────────────
# This app's OWN release version — distinct from every other "version" in the
# codebase, which refers to the assessed OpenClaw *product*. Mirrors
# openclaw_status.__version__ (a test pins them equal); surfaced additively in
# latest.json (`app_version`) and the page footer. Bump on release, then cut the
# matching annotated git tag (e.g. `v1.0.0`) from this value.
APP_VERSION = "1.1.0"

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

# Optional: a Slack/Discord-style incoming webhook. When set, cost/failure alerts are
# POSTed to it in addition to stdout — the payload key is auto-selected (see lib.notify):
# Discord webhooks get {"content": ...}, Slack and others {"text": ...}. Unset → stdout only.
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL")

# ── Repository ──────────────────────────────────────────────────────────────
REPO_OWNER = "openclaw"
REPO_NAME = "openclaw"
NPM_PACKAGE = "openclaw"
REPO_PATH = f"{REPO_OWNER}-{REPO_NAME}"

# ── Model config ────────────────────────────────────────────────────────────
# All models are served through OpenRouter. The analyst seat is back on V4 Pro —
# the config the 2026-07-22 and 07-31 evals both said KEEP — after the 08-04
# flash-for-cost experiment failed operationally (user call, 2026-08-06): flash
# could not finish this workload in ANY tested shape — the "~…-latest" rolling
# alias failed 5 of 6 live analysis calls 08-04→06, the pinned 0731 slug failed
# identically (empty responses; a refine pass that burned the full 16k output
# budget reasoning), and effort=medium still hit the 450s wall-clock — every
# failed run published via the minimax fallback. Flash (13B-active, ~5× cheaper)
# is fine on small prompts; it just can't reason through the ~10k-token analysis
# context, so don't seat it again without an eval proving otherwise.
#
# 2026-08-13: seat moved to the dated 0813 snapshot (the V4 Pro GA release,
# 2026-08-12). The un-dated slug started failing the day 0813 GA'd, but the root
# cause was NOT the slug: the GA weights reason much longer than the July pro on
# this workload, and the old 16k ASSESSMENT_MAX_TOKENS starved them — a verified
# 16,000-token pure-reasoning burn with zero content (0813, first-party, 310s),
# the same all-reasoning "empty model response" on the un-dated slug, and a 16k
# mid-JSON truncation from minimax the same morning. Fixed by the budget raise at
# ASSESSMENT_MAX_TOKENS below. The dated pin stays right on its own merits: the
# un-dated pool lost first-party routing at GA and prices ~2.7× above the
# snapshot ($1.17/$2.34 vs $0.435/$0.87), and — same lesson as the flash
# "-latest" alias above — rolling slugs shift under you; pin the dated snapshot.
#
# 2026-08-27: seat moved to z-ai/glm-5.3-flash (user request, cost-triggered — the
# 08-20 DeepSeek re-pricing had pushed 0813 to ~$0.18/run). NOT the same situation
# as the deepseek-flash disqualification above: that was a different provider's
# lighter tier failing to finish the workload at all (empty responses, wall-clock
# runaways). Before seating this one, it was run through the same real-context
# eval method documented in the project's auto-memory: 3 independent analyst-only
# calls on today's real collected context, plus one full validator+refine cycle
# through the REAL qwen validator and a real refine call. Findings: schema-valid
# every time, zero hallucinated issue numbers (checked against the full context,
# not just the top-60 ledger), full tier-1 (top-8) coverage every time, verdict
# matched the deepseek baseline (⚠️ medium) on 2/3 runs. The 3rd run surfaced a
# real defect worth remembering: glm systematically under-reports severity by one
# tier vs the raw data's own explicit label (critical→high, high→medium, 15/15
# flagged issues, reproduced in run 1 too) — enough to flip that run's INITIAL
# verdict to an unearned ⏸️. The validator caught it in full and the refine pass
# (glm refining itself) corrected all 15 severities to match the raw data exactly,
# so the existing two-model architecture absorbs this cleanly — same design intent
# as the keep-qwen rationale below, now doing real work against the NEW seat's
# blind spot instead of deepseek's. Expect refine to fire close to every run (same
# as it already does with deepseek) — it's cheap enough not to matter: the 3-call
# eval run (analyst + validator + refine) cost $0.0179 vs deepseek's typical ~$0.18,
# roughly a 90% cut even with refine assumed universal. No first-party provider pin
# exists yet for z-ai (PRIMARY_PROVIDER dropped to None below) — there's no
# reliability history to justify pinning one of its two OpenRouter hosts over the
# other; revisit if a runaway/degraded-host pattern ever shows up, the same way the
# deepseek pin was added after the 08-07 provider-lottery incident.
PRIMARY_MODEL = "z-ai/glm-5.3-flash"
# One shared reasoning config for every role (analyst / validator / fallback). Effort is
# a parked cost lever — dropping a single role to "medium" means rebinding that role's name.
_REASONING_HIGH = {"effort": "high", "exclude": False}
PRIMARY_REASONING = _REASONING_HIGH
# OpenRouter provider routing for the analyst + refine calls (config.PRIMARY_MODEL's
# two use sites). This was a deepseek-specific first-party pin (see the seat-history
# comment above) — the un-dated pro pool was ~18 hosts with OpenRouter load-balancing
# across them, so every call was a provider lottery, and degraded hosts served the
# trickling/empty responses behind the wall-clock kills. That reasoning doesn't
# transfer to z-ai/glm-5.3-flash: its OpenRouter pool is just two hosts (Z.AI direct,
# Novita) with no reliability history yet to prefer one over the other, so this stays
# None (default routing) until evidence says otherwise — same trigger as last time:
# a runaway/degraded-host pattern on scheduled runs.
# Deliberately NOT applied to the validator/fallback seats: different pools.
PRIMARY_PROVIDER = None
# Independent reviewer — deliberately a *different* model from the analyst, so it
# catches the primary's blind spots instead of rubber-stamping its own reasoning.
# qwen3.7-plus reasons, so the validator call gets the wide token budget too
# (see _step_validator) or its JSON would truncate like the analyst's did.
VALIDATOR_MODEL = "qwen/qwen3.7-plus"
VALIDATOR_REASONING = _REASONING_HIGH

# Fallback (used if the primary fails). minimax-m3 is a third distinct provider —
# different from both the analyst (z-ai) and the qwen validator — so a primary
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
# Alert thresholds AND the pipeline's hard budget gate (agent.py refuses to start
# a run once a limit is already exceeded). Monthly = the top of the $5-10 budget
# band (~$4-5 actual at the 8/12/24h cadence), so a runaway stops at ~2x budget —
# this IS the spend backstop; no dashboard-side cap needed. Daily stays loose on
# purpose: it must clear a fresh-release day (~3 runs) plus forced validation runs.
DAILY_COST_LIMIT = 2.0       # USD
MONTHLY_COST_LIMIT = 10.0    # USD

# Assessment output budget. The analyst/refine steps emit a full JSON document
# (thesis + evidence + one known_issues entry per issue + changes), which blows
# past the 4k default and truncates mid-JSON → "Failed to parse JSON." Crucially,
# OpenRouter counts reasoning tokens against this cap too, so the budget must
# cover reasoning + the full document. 16k cleared that for the July models
# (~4–6k reasoning burn), but the V4 Pro GA weights (0813) think 16k+ on this
# workload: on 2026-08-13 the analyst burned the entire 16k budget on reasoning
# and returned zero content, and minimax truncated mid-JSON at the same cap.
# 32k was sized for the 0813-era deepseek reasoning burn (2× its observed 16k+
# burn) — kept unchanged for the 2026-08-27 glm-5.3-flash seat, where it's a
# generous ceiling rather than a tight one: eval runs used 3.7k-7.7k tokens_out
# (18-24% of the cap), so there's no starvation risk to size against. Still far
# under every seat's output ceiling (glm-5.3-flash 131k, minimax 512k,
# qwen3.7-plus 131k) and worth < $0.03/call at every seat's current prices.
# Time is the real cost of a bigger cap — see PIPELINE_BUDGET_S, sized with it
# (that sizing, too, is now a ceiling with headroom, not a requirement). The
# validator reasons too, so _step_validator passes it this same budget (its
# JSON would otherwise truncate behind the reasoning tokens).
ASSESSMENT_MAX_TOKENS = 32000

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
# With 480 + 1200 + ~60 ≈ 1740 the unit's TimeoutStartSec of 1800 still clears
# (deploy/*.service; comment there updated, value unchanged — no reprovision needed).
# 1200 was sized with the 32k token budget: the 0813 analyst generates ~50 tok/s,
# so a full 32k response needs up to ~10 min — the derived 600s per-call cap below
# covers it, and 2×600 fits primary + fallback inside one budget as before.
PIPELINE_BUDGET_S = 1200

# Cap on any SINGLE budgeted LLM attempt, as a fraction of the whole budget. Without
# it one runaway attempt can legally eat nearly the full PIPELINE_BUDGET_S and leave
# every later attempt — including the entire fallback model — dead on arrival with
# "budget already exhausted" (2026-08-04: a deepseek reasoning burn ran 555s, its
# retry got the 344s remainder, minimax got 0, run failed). Half the budget keeps a
# healthy call's normal 2–5 min completely untouched while guaranteeing whatever
# comes after a runaway at least the same slice it had.
LLM_CALL_CAP_S = PIPELINE_BUDGET_S // 2

# Cap on how many issues are fed into the LLM prompt. The collector persists the
# full ranked set to raw-data.json; only the top-N by rank go to the model, which
# bounds both the input context (~1k chars/issue) and the known_issues output.
MAX_ISSUES_IN_CONTEXT = 30
# Reading tiers within the prompt's issue list (agent.build_context): tier 1 gets full
# detail and must drive the verdict, tier 2 is compact support, the rest one-liners.
CONTEXT_TIER_TOP = 8
CONTEXT_TIER_MID = 12

# Latency watch: a single LLM call at/over this many seconds gets flagged (log +
# webhook ping). Sized for the 0813-era analyst, which legitimately ran ~4–7 min
# at high effort (~50 tok/s against the 32k budget) — kept unchanged for the
# 2026-08-27 glm-5.3-flash seat, whose eval calls finished in ~1.7–5.4 min, well
# under this. A call pushing past 8 min is genuinely drifting toward the 600s
# per-call cap, where runs start silently degrading (validator skipped →
# "unreviewed" single-model pages) long before anything errors. A heads-up only —
# never blocks the run.
SLOW_CALL_WARN_S = 480

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

# Label-drift tripwire (observability only — never blocks a run or changes a verdict):
# ping Discord once per unknown label that starts trending on scouted issues, so a
# taxonomy shift in the watched repo (a new impact:* name, a new label family) surfaces
# instead of silently mis-scoring severity. State file remembers what was already
# alerted; delete an entry (or the file) to re-arm a label.
LABEL_DRIFT_FILE = DATA_DIR / "label-drift.json"
LABEL_DRIFT_MIN_SHARE = 0.15   # unknown label must sit on ≥15% of this run's scout…
LABEL_DRIFT_MIN_COUNT = 5      # …and on at least this many issues (small-scout noise guard)
SOURCE_EMPTY_FILE = DATA_DIR / "source-empty.json"
SOURCE_EMPTY_RUNS = 3          # consecutive completed collects a source may be empty before the one-time ping

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
# tell users to back up and treat the early verdict as provisional. At the ~8h fresh-tier
# run cadence this spans the first several re-assessments — long enough for reports to
# start landing and the picture to firm up.
FRESH_RELEASE_DAYS = 2

# Also retire the fresh-release banner once this version has been assessed MORE than
# this many times. By the 4th run (~24h at the 8h fresh-tier cadence) enough version-specific
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
# re-assessed — its verdict is stable, so a fresh release gets ~3 reads/day and one
# that's been out a fortnight gets one every other day. (Was 6/8/12, then 8/12/24 to
# keep monthly LLM spend in the $5–10 band.)
#
# The two tail tiers were added 2026-08-26: past the first week the assessment stops
# moving — the ledger has saturated, the verdict has held for days, and each run re-pays
# the full analyst+validator+refine cost to restate it. So the decay continues instead of
# flooring at a day: 24h through week one, 36h in week two, 48h from then on. A genuinely
# new release resets the age clock to the 8h tier, so responsiveness where it matters is
# untouched — only the stale tail gets cheaper.
#
# ⚠️ COUPLED to the external watchdog: it alerts when latest.json's `assessed_at` is older
# than its --stale-hours, so that threshold must clear the SLOWEST tier here plus a run's
# own duration (48h + the 0.5h grace + hourly tick granularity + ~15min of run ≈ 48.75h
# worst case while perfectly healthy). deploy/watchdog.py defaults to 56h for exactly this
# reason — raising the floor here means raising it there AND in the live cron line, or
# every aged release trips a false STALE alert.
ASSESS_CADENCE_TIERS = [(48, 8), (96, 12), (168, 24), (336, 36), (None, 48)]
# Fire a touch early so an hourly tick never drifts a full slot late (timer jitter).
SCHEDULE_GRACE_H = 0.5
# New-release retry backoff. A new release fires an immediate assessment, but if that assess
# persistently FAILS, assessment.json never advances past the old version, so the "new release"
# signal would re-fire every hourly tick and re-spend / alert-storm. Only re-fire once this many
# hours have passed since the last run (a genuinely new release is normally detected after a
# cadence gap ≫ this, so first detection stays prompt; only rapid re-attempts back off).
NEW_RELEASE_RETRY_H = 6
