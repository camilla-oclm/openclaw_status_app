# OpenClaw Status Dashboard

> Public website showing the latest stable OpenClaw release with an LLM-generated assessment report.
> **Full architecture & security:** [REQUIREMENTS.md](./REQUIREMENTS.md)

---

## Decisions Made

| Decision | Choice | Date |
|----------|--------|------|
| LLM primary | DeepSeek V4 Flash (high reasoning) - $0.0011/run | 2026-05-29 |
| LLM validator | OpenRouter Owl Alpha (free) - $0.00/run | 2026-05-29 |
| Automation | Fully automated, no human review | 2026-05-29 |
| Validation | **Feedback loop**: primary → validator → (refine if disagree) | 2026-05-29 |
| Domain | `openclawstatus.io` (Fede to register) | 2026-05-29 |
| AWS | New account, S3 + CloudFront only | 2026-05-29 |
| Agent location | This machine (not Lambda) | 2026-05-29 |
| GitHub auth | Composio CLI handles it | 2026-05-29 |
| Release detection | Hourly npm registry poll | 2026-05-29 |
| Frontend | Pure HTML/CSS/JS, zero dependencies | 2026-05-29 |
| Monitoring | OpenRouter usage tracking built-in | 2026-05-29 |
| Issue tracking | Clawsweeper-state data (not custom) | 2026-05-29 |
| Data collection | Every 6h full, hourly npm poll | 2026-05-29 |
| Recommendation logic | 🔄 only when `fixed_release` is known; ⏸️ when all unfixed | 2026-05-29 |

---

## Architecture

```
This Machine (cron triggered)
  ├─ npm registry poll (hourly)
  │     └─ If version changed → trigger full pipeline
  │
  ├─ Full pipeline (every 6h):
  │     ├─ GitHub Releases (stable + pre-release)
  │     ├─ Clawsweeper-state (work candidates + closed)
  │     ├─ Clawsweeper records (per-issue metadata)
  │     ├─ GitHub API (issue bodies via Composio GraphQL)
  │     ├─ Firecrawl (releases page)
  │     ├─ Reddit (Composio)
  │     └─ npm registry
  │           ↓
  │     Input sanitization (injection defense)
  │           ↓
  │     LLM Assessment Pipeline:
  │       ├─ Step 1: Primary (DeepSeek V4 Flash, high reasoning) - $0.0011
  │       ├─ Step 2: Validator (Owl Alpha, free) - reviews primary's work
  │       └─ Step 3: If validator disagrees → Primary refines with critique - +$0.0011
  │           ↓
  │     Output validation (schema + XSS)
  │           ↓
  │     S3 upload (JSON + static files)
  │
  └─ AWS: S3 + CloudFront + Route 53 + ACM
```

---

## Data Sources

| Source | Tool | Auth | What It Provides |
|--------|------|------|-----------------|
| GitHub Releases | Composio + Firecrawl | Composio | Changelog, fix details, PR links |
| GitHub Issues | Composio GraphQL | Composio | Issue bodies, labels, comments |
| Clawsweeper-state | Firecrawl + curl | Public | Work candidates, closed issues, per-issue records |
| Reddit | Composio | Composio | Community sentiment |
| npm Registry | curl | Public | Latest published version |

---

## Issue Categories

| Category | How It's Found | What It Means |
|----------|---------------|---------------|
| **regression** | Bugs opened AFTER the stable release date | New breakage in current version |
| **diamond_lobster** | Label: `issue-rating: 🦞 diamond lobster` | Highest severity, always track |
| **active** | Bugs with recent comments/discussion | Ongoing issues being worked on |

Each issue is enriched with:
- Full body text (for LLM context)
- Clawsweeper decision (`keep_open` / `close`)
- Clawsweeper `fixed_release` (which version fixes it)
- Cross-reference against stable + pre-release PRs

---

## Model Selection

Tested 7 models on the same data (2026-05-29):

| Model | Rec | Confidence | Cost | Latency | Notes |
|-------|-----|------------|------|---------|-------|
| DeepSeek V4 Flash | 🔄 | high | $0.0011 | 30s | Primary (no reasoning) |
| DeepSeek V4 Flash (high reasoning) | 🔄 | high | $0.0011 | 90s | **Production primary** |
| DeepSeek V4 Pro | 🔄 | medium | $0.0027 | 65s | 2.5x cost, similar quality |
| MiMo v2.5 | 🔄 | high | $0.0007 | 32s | Good but less detailed |
| Qwen 235B | ⏸️ | high | $0.0008 | 20s | Cheapest paid, good quality |
| Qwen 3.7 Max | 🔄 | high | $0.028 | 103s | Best quality, 25x cost |
| **Owl Alpha (free)** | **🔄** | **high** | **$0.00** | **62s** | **Production validator** |
| GPT-4.1 Nano | 🔄 | medium | $0.0004 | 4s | Fastest, medium confidence |
| Gemini 2.0 Flash | ⏸️ | high | $0.0006 | 8s | Good quality, fast |

**Production choice:** DeepSeek V4 Flash (high reasoning) + Owl Alpha (free validator)
**Validation pipeline:** primary → validator → (refine if disagree)
**Cost per run:** ~$0.0011 (agreement) | ~$0.0022 (disagreement, rare) | **Daily cost (4 runs):** ~$0.005

---

## Recommendation Logic

- ✅ Update now: critical fix or high-value feature, no risky bugs, no open regressions
- ⚠️ Update with precautions: valuable changes but risky bugs exist; back up first
- ⏸️ Skip this version: no significant value, or risky bugs present with no fix in sight
- 🔄 Wait for next release: fixes ARE confirmed in pre-release (`fixed_release` is NOT "unknown")

**Key rule:** 🔄 only when at least one critical issue has `fixed_release` set to a specific version. Otherwise ⏸️.

## Validation Pipeline

The assessment runs through a 3-step feedback loop:

1. **Primary** (DeepSeek V4 Flash, high reasoning) - produces the initial assessment
2. **Validator** (Owl Alpha, free) - reviews the primary's work as a critic, checks for missed issues, logical errors, unsupported claims
3. **Refinement** (only if validator disagrees) - validator's critique is sent back to the primary, which produces a refined assessment

The validator is a **reviewer, not a duplicate analyst** - it checks the primary's reasoning without redoing the full analysis. If they agree, the primary's answer is published. If they disagree, the primary gets a chance to incorporate the feedback and correct itself.

**Cost impact:** Agreement = 1 primary + 1 validator (free) = $0.0011. Disagreement adds 1 refinement call = $0.0022 total.

---

## Roadmap

### Phase 0 - Plan & Requirements ✅
- [x] Architecture defined
- [x] Data model designed
- [x] Security threat model
- [x] All decisions locked

### Phase 1 - Data Collector ✅
- [x] npm registry check
- [x] GitHub Releases (stable + pre-release)
- [x] GitHub Issues via Composio GraphQL (categorized)
- [x] Clawsweeper-state integration (work candidates + closed)
- [x] Clawsweeper per-issue records (decision, fixed_release)
- [x] Releases page via Firecrawl (fix patterns)
- [x] Reddit sentiment via Composio
- [x] ~~Web reports via Tavily~~ removed (low signal, not useful)
- [x] Input sanitization (injection defense)
- [x] Cross-reference fixes against releases
- [x] Version relevance filter
- [x] Findings viewer (PRE-LLM HTML)

### Phase 2 - LLM Assessment Agent ✅
- [x] OpenRouter API integration
- [x] DeepSeek V4 Flash with high reasoning
- [x] Owl Alpha free validator
- [x] System prompt (hardened, explicit rules)
- [x] Structured output schema
- [x] Output validation (schema + XSS)
- [x] Usage monitoring (tokens, cost, latency)
- [x] Model comparison test script
- [x] Recommendation logic (🔄 vs ⏸️ threshold)
- [x] **Validation feedback loop**: primary → validator → refine if disagree
- [x] Validator prompt (reviewer, not duplicate analyst)
- [x] Refinement prompt (incorporates validator critique)
- [x] Pipeline logging (agrees/disagrees, refined Y/N, per-step usage)

### Phase 3 — Static Frontend 🔄
- [x] Design mockup: terminal/CLI aesthetic (chosen from 4 options)
- [x] Self-contained HTML, zero dependencies
- [x] Dark mode + light mode support
- [x] Mobile responsive
- [x] XSS safe (textContent only)
- [x] Assessment data rendering (thesis, evidence, issues, platform impact)
- [x] Raw findings data (npm, clawsweeper work/closed, release history with highlights)
- [x] Auto-linking issue numbers to GitHub
- [x] Collapsible sections
- [x] Typewriter thesis reveal
- [x] "What is this" intro for new visitors
- [ ] Refine visual design
- [ ] Error states (fetch fails, stale data)
- [ ] CSP headers
- [ ] Production-ready (load from JSON, not embedded data)

### Phase 4 — AWS Deployment 🔲
- [ ] S3 bucket (static hosting + JSON data)
- [ ] CloudFront (CDN + HTTPS + OAI)
- [ ] Route 53 (DNS)
- [ ] ACM (SSL cert)
- [ ] IAM user (S3 write only)
- [ ] Deploy frontend
- [ ] Deploy data pipeline output

### Phase 5 - Cron & Monitoring 🔲
- [ ] Hourly npm poll (trigger on version change)
- [ ] Every 6h full data collection
- [ ] OpenRouter cost tracking via analytics API
- [ ] Alert on cost threshold ($2/day, $30/month)
- [ ] Notification on failure
- [ ] Usage dashboard

### Phase 6 - Polish 🔲
- [ ] Design refinement
- [ ] Error state handling
- [ ] Performance tuning
- [ ] Cache headers

---

## Cost Estimate

| Service | Monthly Cost |
|---------|-------------|
| S3 | ~$0.50 |
| CloudFront | Free tier |
| Route 53 | $0.50 |
| ACM | Free |
| OpenRouter (DeepSeek V4 Flash + Owl Alpha) | ~$0.15 |
| **Total** | **~$1.15/mo** |

---

## File Structure

```
projects/openclaw-status/
├── PLAN.md              ← this file
├── REQUIREMENTS.md      ← architecture & security
├── .env                 ← API keys (gitignored)
├── .env.example         ← template
├── .gitignore
├── mockups/
│   └── mockup-terminal.html    ← chosen design (terminal/CLI)
├── src/
│   ├── collector.py     ✅ data collection pipeline
│   ├── agent.py         ✅ LLM assessment agent
│   ├── gen_findings.py  ✅ PRE-LLM findings viewer
│   ├── build_mockup.py  ✅ injects real pipeline data into mockup HTML
│   └── test_models.py   ✅ model comparison tool
└── data/
    ├── raw-data.json    ← collector output
    ├── assessment.json  ← agent output
    ├── model-comparison.json
    └── usage.json       ← cost tracking
```

---

## References

- [ClawSweeper](https://github.com/openclaw/clawsweeper) - issue review bot
- [ClawSweeper State](https://github.com/openclaw/clawsweeper-state) - pre-reviewed issue data
- [OpenClaw GitHub Releases](https://github.com/openclaw/openclaw/releases)
- [OpenClaw npm Registry](https://registry.npmjs.org/openclaw)
- [OpenRouter API](https://openrouter.ai/docs/api)
- [OpenRouter Models](https://openrouter.ai/docs/api/api-reference/models/get-models)
- [OpenRouter Analytics](https://openrouter.ai/docs/api/api-reference/analytics/get-user-activity)
