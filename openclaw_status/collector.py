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
                        version: str = "", status: "SourceStatus | None" = None) -> list[dict]:
    """Scout the repo for issues impacting the assessed version (see github.py),
    then mark which ones the release/pre-release explicitly closes."""
    import time as _time
    t0 = _time.time()
    print("🐛 Scouting GitHub issues...")

    issues = github.scout_issues(release_date, version)
    if issues is None:
        print("  ❌ GitHub API unavailable (no token?) — no issues collected", file=sys.stderr)
        issues = []

    # Cross-reference fixes: an issue is "fixed" only if the release/pre-release
    # body explicitly closes it (fixes/closes/resolves #N) — not any bare #N,
    # which is usually a PR number.
    stable_fixed = github.extract_closing_refs(release_body)
    prerelease_fixed = github.extract_closing_refs(prerelease_body)
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
    if status is not None:
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
        # 1. npm
        npm = fetch_npm_version()
        source_status.record("npm", "ok" if npm else "failed", npm.get("version", "?") if npm else "")
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

        # 3. Pre-release (most recent non-draft pre-release)
        prerelease = github.latest_prerelease(all_releases)
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

        # 4. Release history (for the timeline / highlights)
        release_history = []
        for r in all_releases[:12]:
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
                        existing = item.get("fixed_in", [])
                        item["fixed_in"] = list(set((existing if isinstance(existing, list) else []) + [fixed_rel]))
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

    # ── DEDUPLICATION ── by issue number, keeping the highest severity.
    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    seen_issues = {}
    for issue in issues:
        num = issue.get("number")
        if num is None:
            continue
        rank = _SEV_ORDER.get(issue.get("severity", "low"), 99)
        if num not in seen_issues or rank < _SEV_ORDER.get(seen_issues[num].get("severity", "low"), 99):
            seen_issues[num] = issue
    if len(seen_issues) != len(issues):
        print(f"  🔄 Deduplicated issues: {len(issues)} → {len(seen_issues)}")
    issues = list(seen_issues.values())

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
