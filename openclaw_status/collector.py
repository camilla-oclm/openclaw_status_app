"""
Data collector. Reads everything from the GitHub API (issues + releases, via the
token) plus the npm registry and the public Clawsweeper-state repo. Outputs
raw-data.json. No third-party data brokers.
"""

import json
import re
import sys
import urllib.request

from openclaw_status import config, github
from openclaw_status.lib import (
    sanitize, save_json, now_iso, version_from_release, parallel_fetch, PipelineTimer,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Source status tracking
# ═══════════════════════════════════════════════════════════════════════════

class SourceStatus:
    """Track which sources succeeded, failed, or returned no data."""

    def __init__(self):
        self.results = {}  # source_name -> {status, detail, duration_s}

    def record(self, name: str, status: str, detail: str = "", duration_s: float = 0):
        """status: 'ok', 'empty', 'failed'"""
        self.results[name] = {"status": status, "detail": detail, "duration_s": duration_s}

    def summary(self) -> str:
        lines = []
        for name, info in self.results.items():
            icon = {"ok": "✅", "empty": "⚠️", "failed": "❌"}.get(info["status"], "❓")
            lines.append(f"  {icon} {name}: {info['status']} {info['detail']}")
        return "\n".join(lines)

    def has_failures(self) -> bool:
        return any(r["status"] == "failed" for r in self.results.values())


# ═══════════════════════════════════════════════════════════════════════════
#  npm
# ═══════════════════════════════════════════════════════════════════════════

def fetch_npm_version() -> dict | None:
    print("📦 Checking npm registry...")
    url = f"https://registry.npmjs.org/{config.NPM_PACKAGE}/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "openclaw-status"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return {"version": data.get("version", ""), "name": data.get("name", "")}
    except Exception as e:
        print(f"  ⚠ npm fetch failed: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Clawsweeper-state (public repo README + per-issue records)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_clawsweeper_state() -> dict:
    """Parse the clawsweeper-state README for work candidates and recently closed."""
    print("🧹 Fetching clawsweeper-state...")
    result = {"work_candidates": [], "recently_closed": [], "item_records": {}}

    md = github.fetch_raw("openclaw", "clawsweeper-state", "main", "README.md")
    if not md:
        return result

    def _parse_table(heading: str, key_reason: str) -> list[dict]:
        rows = []
        m = re.search(rf"### {heading}.*?\n\| Repository.*?\n((?:\|.*\n)*)", md, re.DOTALL)
        if not m:
            return rows
        for line in m.group(1).strip().split("\n"):
            if not line.strip() or "|" not in line:
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) < 5:
                continue
            num = re.search(r"#(\d+)", cols[1])
            if not num:
                continue
            rows.append({
                "number": int(num.group(1)),
                "title": sanitize(cols[2], 200),
                key_reason: cols[3].lower() if cols[3] else "unknown",
                ("reviewed_at" if key_reason == "priority" else "closed_at"):
                    cols[4] if len(cols) > 4 else "",
            })
        return rows

    result["work_candidates"] = _parse_table("Work Candidates", "priority")
    result["recently_closed"] = _parse_table("Recently Closed", "reason")
    print(f"  Found {len(result['work_candidates'])} work candidates, "
          f"{len(result['recently_closed'])} recently closed")
    return result


def fetch_clawsweeper_records(issue_numbers: list[int],
                              status: "SourceStatus | None" = None) -> dict:
    """Fetch per-issue clawsweeper records (decision, fixed_release) in parallel."""
    import time as _time
    t0 = _time.time()
    print(f"  📋 Fetching {len(issue_numbers)} clawsweeper records (parallel)...")

    def _fetch_one(num):
        for folder in ("items", "closed"):
            md = github.fetch_raw(
                "openclaw", "clawsweeper-state", "state",
                f"records/{config.REPO_PATH}/{folder}/{num}.md",
            )
            if not md or md.startswith("404"):
                continue
            meta = {}
            for line in md.split("\n"):
                if ":" in line and not line.startswith("#") and not line.startswith("---"):
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip()
            if meta.get("number"):
                return meta
        return None

    raw_results = parallel_fetch(_fetch_one, issue_numbers, max_workers=6)
    records = {num: meta for num, meta in raw_results.items() if meta}

    elapsed = _time.time() - t0
    if status is not None:
        status.record("clawsweeper_records", "ok" if records else "empty",
                      f"{len(records)}/{len(issue_numbers)} records", elapsed)
    print(f"    Got {len(records)} records in {elapsed:.1f}s")
    return records


# ═══════════════════════════════════════════════════════════════════════════
#  GitHub issues
# ═══════════════════════════════════════════════════════════════════════════

def fetch_github_issues(release_body: str = "", prerelease_body: str = "", release_date: str = "",
                        version: str = "", status: "SourceStatus | None" = None,
                        stable_closing_refs=None, prerelease_closing_refs=None) -> list[dict]:
    """Scout the repo for issues impacting the assessed version (see github.py),
    then mark which ones the release/pre-release explicitly closes.

    `*_closing_refs` are the issue numbers the release/pre-release body says it fixes,
    pre-extracted from the RAW body in github._norm_release (the "fixes #N" lines live in
    the PR-log tail that curation drops, so they're gone from `release_body` by the time it
    reaches here). When omitted, we fall back to parsing the bodies for backward compatibility.
    """
    import time as _time
    t0 = _time.time()
    print("🐛 Scouting GitHub issues...")

    coverage = {}
    issues = github.scout_issues(release_date, version, coverage=coverage)
    if issues is None:
        print("  ❌ GitHub API unavailable (no token?) — no issues collected", file=sys.stderr)
        issues = []

    # Cross-reference fixes: an issue is "fixed" only if the release/pre-release
    # body explicitly closes it (fixes/closes/resolves #N) — not any bare #N,
    # which is usually a PR number. Prefer the refs pre-extracted from the raw body
    # (see github._norm_release); fall back to parsing the passed bodies.
    stable_fixed = set(map(str, stable_closing_refs)) if stable_closing_refs is not None \
        else github.extract_closing_refs(release_body)
    prerelease_fixed = set(map(str, prerelease_closing_refs)) if prerelease_closing_refs is not None \
        else github.extract_closing_refs(prerelease_body)
    for item in issues:
        num_str = str(item["number"])
        item["fixed_in"] = []
        if num_str in stable_fixed:
            item["fixed_in"].append("stable")
        if num_str in prerelease_fixed:
            item["fixed_in"].append("prerelease")

    elapsed = _time.time() - t0
    n_relevant = sum(1 for i in issues if i.get("affects_version"))
    print(f"  Found {len(issues)} issues ({n_relevant} reference this version)")
    # A partial scout (the broad recency sweep dropped, or some searches failed) is recorded
    # as "degraded", NOT "ok", so the assessment's thin-evidence floor caps confidence and we
    # never read an incomplete issue set as a genuinely clean release. See github.scout_issues.
    broad_ok = coverage.get("broad_ok")
    some_failed = coverage.get("queries_ok", 0) < coverage.get("queries_total", 0)
    degraded = broad_ok is False or (broad_ok is None and some_failed)
    if status is not None:
        if issues and degraded:
            status.record("github_issues", "degraded",
                          f"{len(issues)} issues — PARTIAL scout "
                          f"({coverage.get('queries_ok')}/{coverage.get('queries_total')} searches ok)",
                          elapsed)
        else:
            status.record("github_issues", "ok" if issues else "empty",
                          f"{len(issues)} issues ({n_relevant} version-relevant)", elapsed)
    return issues


# ═══════════════════════════════════════════════════════════════════════════
#  Main collection entry point
# ═══════════════════════════════════════════════════════════════════════════

def collect(output_path=None) -> dict:
    """Run the full collection pipeline. Returns the raw data dict and saves to disk.

    Guards: a pipeline timeout (PipelineTimer), a completeness gate (abort if the
    critical sources fail), and issue de-duplication.
    """
    output_path = output_path or config.RAW_DATA_FILE
    now = now_iso()
    source_status = SourceStatus()

    print(f"\n{'='*60}")
    print(f"OpenClaw Status — Data Collection")
    print(f"Time: {now}")
    print(f"{'='*60}\n")

    timer = PipelineTimer(timeout=900)
    timer.__enter__()

    try:
        # 1. npm — "ok" only with a usable version. A payload that comes back without one
        # is "empty", not "ok", so the completeness gate can't count a versionless npm as a
        # live critical source.
        npm = fetch_npm_version()
        npm_ver = (npm or {}).get("version", "")
        source_status.record(
            "npm",
            "ok" if npm_ver else ("empty" if npm else "failed"),
            npm_ver or ("registry returned no version" if npm else ""),
        )
        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after npm")

        # 2. Stable release + recent release history (one release list, reused)
        print("📦 Fetching releases...")
        all_releases = github.list_releases(30)
        release = github.latest_release()
        source_status.record("github_release", "ok" if release else "failed",
                             release.get("tag", "?") if release else "")
        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after release")

        # 3. Pre-release — most recent non-draft pre-release that is actually AHEAD
        # of the stable. A beta of the already-shipped stable (v2026.6.8-beta.2 vs
        # v2026.6.8) is not a "next release" to wait for, so it's filtered out.
        prerelease = github.latest_prerelease(all_releases, stable=release)
        if prerelease:
            print(f"  Found pre-release: {prerelease['tag']}")
        else:
            print("  No pre-release found")
        source_status.record("prerelease", "ok" if prerelease else "empty",
                             prerelease.get("tag", "") if prerelease else "")

        # Determine the version being assessed
        version = version_from_release(release) if release else ""
        if not version:
            version = (npm or {}).get("version", "")
        print(f"\n📌 Target version: {version or 'unknown'}")
        if prerelease:
            print(f"📌 Pre-release: {prerelease['tag']} (fixes pending in stable)")
        print()

        # 4. Release history (for the timeline / highlights + the "catching up" changelog).
        # Keep a wide window so enough STABLE releases survive (the recent list is often
        # dominated by beta pre-releases, which the changelog section filters out).
        release_history = []
        for r in all_releases[:24]:
            release_history.append({
                "tag": r["tag"], "published_at": r["published_at"],
                "prerelease": r["prerelease"], "body": (r.get("body") or "")[:2500],
            })
        source_status.record("release_history", "ok" if release_history else "empty",
                             f"{len(release_history)} releases")
        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after release history")

        # 5. Clawsweeper state
        clawsweeper = fetch_clawsweeper_state()
        source_status.record("clawsweeper", "ok" if clawsweeper.get("work_candidates") else "empty")
        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after clawsweeper")

        # 6. GitHub issues
        issues = fetch_github_issues(
            release_body=release.get("body", "") if release else "",
            prerelease_body=prerelease.get("body", "") if prerelease else "",
            release_date=release.get("published_at", "") if release else "",
            version=version,
            status=source_status,
            stable_closing_refs=release.get("closing_refs") if release else None,
            prerelease_closing_refs=prerelease.get("closing_refs") if prerelease else None,
        )

        # 7. Enrich issues with clawsweeper records (decision, fixed_release)
        if issues:
            cs_records = fetch_clawsweeper_records([i["number"] for i in issues], status=source_status)
            clawsweeper["item_records"] = cs_records
            for item in issues:
                rec = cs_records.get(item["number"])
                if rec:
                    item["clawsweeper"] = {
                        "decision": rec.get("decision", "unknown"),
                        "fixed_release": rec.get("fixed_release", "unknown"),
                        "fixed_pr_url": rec.get("fixed_pr_url", "unknown"),
                        "fixed_at": rec.get("fixed_at", "unknown"),
                        "latest_release": rec.get("latest_release", "unknown"),
                        "review_status": rec.get("review_status", "unknown"),
                    }
                    fixed_rel = rec.get("fixed_release", "unknown")
                    if fixed_rel != "unknown":
                        existing = item.get("fixed_in") if isinstance(item.get("fixed_in"), list) else []
                        if fixed_rel not in existing:
                            item["fixed_in"] = existing + [fixed_rel]   # order-preserving (deterministic)
                else:
                    item["clawsweeper"] = None
    finally:
        timer.__exit__(None, None, None)

    # ── DATA COMPLETENESS GATE ──
    # Critical sources: npm OR github_release must be ok; otherwise abort.
    npm_ok = source_status.results.get("npm", {}).get("status") == "ok"
    release_ok = source_status.results.get("github_release", {}).get("status") == "ok"
    if not npm_ok and not release_ok:
        print("\n❌ DATA COMPLETENESS GATE: Both npm and github_release failed!", file=sys.stderr)
        print("   Aborting pipeline — insufficient data for LLM assessment.", file=sys.stderr)
        raw = {
            "collected_at": now,
            "target_version": "",
            "sources": {
                "npm": npm, "latest_release": release, "latest_prerelease": prerelease,
                "changelog": "", "release_history": [], "clawsweeper": {}, "github_issues": [],
            },
            "meta": {"collector_version": "2.0.0", "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}"},
            "source_status": source_status.results,
            "pipeline_aborted": True,
            "abort_reason": "Both npm and github_release sources failed",
        }
        save_json(output_path, raw)
        print(f"💾 Saved aborted raw data to: {output_path}")
        return raw

    # Issues already arrive de-duplicated by number — github.scout_issues dedups across its
    # three searches and drops None-numbered nodes — and the per-version ledger below upserts
    # by number, so the ledger is the single dedup point. No separate pass is needed here.

    # ── Per-version issue ledger ──
    # A released version is immutable, so its known-issue set only grows. Upsert the
    # version-relevant issues into the ledger and use the accumulated, ranked set as
    # github_issues — so the known-issues list and the verdict stop flip-flopping
    # run-to-run (see openclaw_status/ledger.py).
    from openclaw_status import ledger
    before = len(issues)
    # Version-agnostic "ongoing majors" — high-impact open issues that don't reference
    # this version and aren't post-release regressions. The ledger doesn't track them
    # (we focus on the current release), but they're handed to the analyst as context.
    ongoing_majors = sorted(
        (i for i in issues if not ledger.is_version_relevant(i)),
        key=github.rank_key,
    )[:12]
    issues = ledger.merge_version_issues(
        version, issues, now, release_date=release.get("published_at", "") if release else "")
    print(f"  📒 Ledger: {before} scouted → {len(issues)} accumulated "
          f"(+{len(ongoing_majors)} ongoing majors as context) for v{version or '?'}")

    def _counts(items, key):
        counts = {}
        for item in items:
            counts[item.get(key, "unknown")] = counts.get(item.get(key, "unknown"), 0) + 1
        return counts

    raw = {
        "collected_at": now,
        "target_version": version,
        "sources": {
            "npm": npm,
            "latest_release": release,
            "latest_prerelease": prerelease,
            "changelog": release.get("body", "") if release else "",
            "release_history": release_history,
            "clawsweeper": clawsweeper,
            "github_issues": issues,
            "ongoing_majors": ongoing_majors,
        },
        "meta": {
            "collector_version": "2.0.0",
            "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}",
            "sources_count": {"github_issues": len(issues)},
            "issue_categories": _counts(issues, "category"),
            "issue_severities": _counts(issues, "severity"),
            "version_relevant": sum(1 for i in issues if i.get("affects_version")),
        },
        "source_status": source_status.results,
    }

    print(f"\n{'='*60}")
    print("Collection complete:")
    print(source_status.summary())
    cats = _counts(issues, "category")
    if cats:
        print(f"  📊 Categories: {', '.join(f'{k}:{v}' for k, v in cats.items())}")
    print(f"{'='*60}\n")

    save_json(output_path, raw)
    print(f"💾 Saved to: {output_path}")
    print(f"   Size: {output_path.stat().st_size:,} bytes")
    return raw


def _save_partial(output_path, source_status, now, reason):
    """Save partial results when the pipeline times out or aborts early."""
    print(f"  ⚠ Saving partial results: {reason}", file=sys.stderr)
    raw = {
        "collected_at": now,
        "target_version": "",
        "sources": {},
        "meta": {"collector_version": "2.0.0", "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}"},
        "source_status": source_status.results,
        "pipeline_aborted": True,
        "abort_reason": reason,
    }
    save_json(output_path, raw)
    return raw
