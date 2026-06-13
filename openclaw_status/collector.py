"""
Data collector: fetches everything from GitHub, npm, Reddit, Clawsweeper, Firecrawl.
Outputs raw-data.json.
"""

import json
import re
import subprocess
import sys

from openclaw_status import config, github
from openclaw_status.lib import (
    composio, sanitize, parse_firecrawl_markdown, save_json, now_iso,
    version_from_release, parallel_fetch,
    PipelineTimer,
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
#  GitHub: Releases
# ═══════════════════════════════════════════════════════════════════════════

def fetch_latest_release() -> dict | None:
    print("📦 Fetching latest GitHub release...")
    data = composio("GITHUB_GET_THE_LATEST_RELEASE", {"owner": config.REPO_OWNER, "repo": config.REPO_NAME})
    if not data:
        return None
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "published_at": data.get("published_at", ""),
        "body": sanitize(data.get("body", ""), 5000),
        "url": data.get("html_url", ""),
        "prerelease": data.get("prerelease", False),
        "draft": data.get("draft", False),
    }


def fetch_release_by_tag(tag: str) -> dict | None:
    print(f"  Fetching release {tag}...")
    data = composio("GITHUB_GET_A_RELEASE_BY_TAG_NAME",
                     {"owner": config.REPO_OWNER, "repo": config.REPO_NAME, "tag": tag})
    if not data:
        return None
    return {
        "tag": data.get("tag_name", ""),
        "name": data.get("name", ""),
        "published_at": data.get("published_at", ""),
        "body": sanitize(data.get("body", ""), 5000),
        "url": data.get("html_url", ""),
        "prerelease": data.get("prerelease", False),
    }


def list_releases() -> list[dict]:
    print("📦 Listing all releases...")
    data = composio("GITHUB_LIST_RELEASES",
                     {"owner": config.REPO_OWNER, "repo": config.REPO_NAME, "per_page": 30})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return [
        {"tag": r.get("tag_name", ""), "prerelease": r.get("prerelease", False),
         "published_at": r.get("published_at", ""), "draft": r.get("draft", False)}
        for r in items
    ]


def find_prerelease_tags(limit: int = 3) -> list[str]:
    """Return tags of the most recent non-draft prereleases."""
    releases = list_releases()
    pres = [r for r in releases if r.get("prerelease") and not r.get("draft")]
    pres.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    return [p["tag"] for p in pres[:limit]]


# ═══════════════════════════════════════════════════════════════════════════
#  npm
# ═══════════════════════════════════════════════════════════════════════════

def fetch_npm_version() -> dict | None:
    print("📦 Checking npm registry...")
    try:
        result = subprocess.run(
            ["curl", "-s", f"https://registry.npmjs.org/{config.NPM_PACKAGE}/latest"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return {"version": data.get("version", ""), "name": data.get("name", "")}
    except Exception as e:
        print(f"  ⚠ npm fetch failed: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Firecrawl: Releases page & Clawsweeper
# ═══════════════════════════════════════════════════════════════════════════

def fetch_releases_page() -> str:
    print("📄 Fetching releases page via Firecrawl...")
    data = composio("FIRECRAWL_SCRAPE", {
        "url": f"https://github.com/{config.REPO_OWNER}/{config.REPO_NAME}/releases",
        "formats": ["markdown"],
        "onlyMainContent": True,
    })
    if not data:
        return ""
    md = parse_firecrawl_markdown(data)
    if md:
        print(f"  Got {len(md)} chars of release data")
    return md


def fetch_clawsweeper_state() -> dict:
    """Parse the clawsweeper-state README for work candidates and recently closed."""
    print("🧹 Fetching clawsweeper-state...")
    result = {"work_candidates": [], "recently_closed": [], "item_records": {}}

    data = composio("FIRECRAWL_SCRAPE", {
        "url": "https://github.com/openclaw/clawsweeper-state/blob/main/README.md",
        "formats": ["markdown"],
        "onlyMainContent": True,
    })
    if not data:
        return result

    md = parse_firecrawl_markdown(data)
    if not md:
        return result

    # Work candidates table
    wc_match = re.search(r"### Work Candidates.*?\n\| Repository.*?\n((?:\|.*\n)*)", md, re.DOTALL)
    if wc_match:
        for line in wc_match.group(1).strip().split("\n"):
            if not line.strip() or "|" not in line:
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                item_match = re.search(r"#(\d+)", cols[1])
                if item_match:
                    result["work_candidates"].append({
                        "number": int(item_match.group(1)),
                        "title": sanitize(cols[2], 200),
                        "priority": cols[3].lower() if cols[3] else "unknown",
                        "reviewed_at": cols[4] if len(cols) > 4 else "",
                    })

    # Recently closed table
    rc_match = re.search(r"### Recently Closed.*?\n\| Repository.*?\n((?:\|.*\n)*)", md, re.DOTALL)
    if rc_match:
        for line in rc_match.group(1).strip().split("\n"):
            if not line.strip() or "|" not in line:
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                item_match = re.search(r"#(\d+)", cols[1])
                if item_match:
                    result["recently_closed"].append({
                        "number": int(item_match.group(1)),
                        "title": sanitize(cols[2], 200),
                        "reason": cols[3].lower() if cols[3] else "unknown",
                        "closed_at": cols[4] if len(cols) > 4 else "",
                    })

    print(f"  Found {len(result['work_candidates'])} work candidates, {len(result['recently_closed'])} recently closed")
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  Changelog via Tavily
# ═══════════════════════════════════════════════════════════════════════════

def fetch_changelog(version: str = "") -> str:
    print("📋 Fetching changelog...")
    tag = f"v{version}" if version and not version.startswith("v") else version
    url = (f"https://github.com/{config.REPO_OWNER}/{config.REPO_NAME}/releases/tag/{tag}"
           if tag else
           f"https://github.com/{config.REPO_OWNER}/{config.REPO_NAME}/releases/latest")

    data = composio("TAVILY_EXTRACT", {
        "urls": [url], "format": "markdown", "extract_depth": "advanced",
    })
    if not data or "results" not in data:
        return ""

    results = []
    for r in data["results"]:
        content = r.get("raw_content", r.get("content", ""))
        if content:
            results.append(sanitize(content, 5000))

    return "\n\n---\n\n".join(results)


# ═══════════════════════════════════════════════════════════════════════════
#  GitHub Issues
# ═══════════════════════════════════════════════════════════════════════════

def _gh_graphql(query: str, limit: int = 15) -> list[dict]:
    data = composio("GITHUB_SEARCH_GITHUB_GRAPHQL", {"query": query, "first": limit, "type": "ISSUE"})
    if not data:
        return []
    return [e["node"] for e in data.get("search", data).get("edges", [])]


def _gh_issue(issue_number: int) -> dict | None:
    return composio("GITHUB_GET_AN_ISSUE",
                    {"owner": config.REPO_OWNER, "repo": config.REPO_NAME, "issue_number": issue_number})


def _parse_labels(node: dict) -> list[str]:
    return [e["node"]["name"] for e in node.get("labels", {}).get("edges", [])]


def _build_issue(node: dict, labels: list) -> dict:
    """Base issue dict from a Composio GraphQL node. severity/category/impact are
    filled in by _scout_composio after body enrichment."""
    return {
        "number": node["number"],
        "title": sanitize(node.get("title", "")),
        "url": node.get("url", ""),
        "body": "",
        "snippet": "",
        "comments": node.get("comments", {}).get("totalCount", 0),
        "comments_data": [],
        "updated_at": node.get("updatedAt", ""),
        "created_at": node.get("createdAt", ""),
        "labels": labels,
        "platform": "general",
        "priority": github.priority_of(labels),
        "is_feature": False,
        "severity": "low",
        "category": "active",
        "source": "github_graphql",
    }


def _enrich_body(item: dict):
    data = _gh_issue(item["number"])
    if data:
        item["body"] = sanitize(data.get("body", ""), 2000)
        item["snippet"] = sanitize(data.get("body", ""), 500)
    return item


def fetch_github_issues(release_body: str = "", prerelease_body: str = "", release_date: str = "",
                        version: str = "", status: "SourceStatus | None" = None) -> list[dict]:
    """Scout the repo for issues impacting the assessed version.

    Prefers the direct GitHub API (token) — which ranks candidates by community
    impact (👍) and flags version relevance — and falls back to Composio when no
    token is set or the API is unavailable. If a SourceStatus is passed, the
    result is recorded on it.
    """
    import time as _time
    t0 = _time.time()
    print("🐛 Scouting GitHub issues...")

    issues = None
    if github.has_token():
        print("  Using direct GitHub API (token)")
        issues = github.scout_issues(release_date, version)
        if issues is None:
            print("  ⚠ GitHub API unavailable — falling back to Composio", file=sys.stderr)
    if issues is None:
        print("  Using Composio")
        issues = _scout_composio(release_date, version)

    # Cross-reference fixes: an issue is "fixed" only if the release/pre-release
    # body explicitly closes it (fixes/closes/resolves #N) — not any bare #N,
    # which is usually a PR number.
    print("  🔗 Cross-referencing fixes...")
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


def _scout_composio(release_date: str = "", version: str = "") -> list[dict]:
    """Fallback scout via Composio GraphQL. No reaction data, so severity comes
    from labels and impact from comment volume. Same query/filter logic as the
    direct-API path (exclude features, skip only `stale`)."""
    repo = f"{config.REPO_OWNER}/{config.REPO_NAME}"
    no_feat = "-label:enhancement"
    issues, seen = [], set()

    def _add_nodes(nodes):
        for node in nodes:
            if node["number"] in seen:
                continue
            labels = _parse_labels(node)
            if "stale" in labels or github.is_feature(node.get("title", ""), labels):
                continue
            issues.append(_build_issue(node, labels))
            seen.add(node["number"])

    if release_date:
        _add_nodes(_gh_graphql(
            f"repo:{repo} is:issue is:open created:>={release_date[:10]} {no_feat} sort:reactions-+1-desc", 20))
    _add_nodes(_gh_graphql(f"repo:{repo} is:issue is:open label:P1 {no_feat} sort:reactions-+1-desc", 15))
    _add_nodes(_gh_graphql(f"repo:{repo} is:issue is:open {no_feat} sort:reactions-+1-desc", 15))

    # Enrich top 15 with body (Composio: one API call each)
    for item in issues[:15]:
        _enrich_body(item)

    # Compute the signal fields the API path provides, so downstream is uniform.
    for item in issues:
        item.setdefault("reactions", 0)
        item.setdefault("total_reactions", 0)
        text = f"{item.get('title','')} {item.get('body','')}"
        affects = github.version_relevant(text, version)
        item["affects_version"] = affects
        item["impact"] = github.impact_level(0, item.get("comments", 0))
        item["severity"] = github.derive_severity(item["labels"], 0, item.get("comments", 0))
        item["category"] = github.categorize(item.get("created_at", ""), item["labels"],
                                             affects, item["impact"], release_date)
    return issues


# ═══════════════════════════════════════════════════════════════════════════
#  Clawsweeper per-issue records
# ═══════════════════════════════════════════════════════════════════════════

def fetch_clawsweeper_records(issue_numbers: list[int],
                              status: "SourceStatus | None" = None) -> dict:
    """Fetch clawsweeper records in parallel. Returns {number: metadata} dict."""
    import time as _time
    t0 = _time.time()
    print(f"  📋 Fetching {len(issue_numbers)} clawsweeper records (parallel)...")

    def _fetch_one(num):
        for folder in ("items", "closed"):
            try:
                raw_url = (
                    f"https://raw.githubusercontent.com/openclaw/clawsweeper-state/state/records/"
                    f"{config.REPO_PATH}/{folder}/{num}.md"
                )
                resp = subprocess.run(["curl", "-s", raw_url], capture_output=True, text=True, timeout=10)
                if resp.returncode != 0 or not resp.stdout or resp.stdout.startswith("404"):
                    continue
                meta = {}
                for line in resp.stdout.split("\n"):
                    if ":" in line and not line.startswith("#") and not line.startswith("---"):
                        key, _, val = line.partition(":")
                        meta[key.strip()] = val.strip()
                if meta.get("number"):
                    return meta
            except Exception:
                pass
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
#  Reddit sentiment
# ═══════════════════════════════════════════════════════════════════════════

def fetch_reddit(version: str = "") -> list[dict]:
    print("💬 Searching Reddit...")

    v = f" {version}" if version else ""
    queries = [
        (f"openclaw{v} bug", "general"),
        (f"openclaw{v} broken regression", "general"),
        (f"openclaw{v} update", "general"),
    ]
    if version:
        for platform in ("windows", "macos", "linux", "discord", "slack", "telegram"):
            queries.append((f"openclaw {version} {platform}", platform))

    posts = []
    seen = set()
    for query, platform in queries:
        data = composio("REDDIT_SEARCH_ACROSS_SUBREDDITS",
                         {"search_query": query, "limit": 5, "restrict_sr": False})
        if not data:
            continue

        items = data.get("data", data) if isinstance(data, dict) else data
        items = items.get("posts", []) if isinstance(items, dict) else (items if isinstance(items, list) else [])
        for post in items:
            post = post.get("data", post) if isinstance(post, dict) else post
            pid = post.get("id", post.get("permalink", ""))
            if pid in seen:
                continue
            seen.add(pid)
            posts.append({
                "title": sanitize(post.get("title", "")),
                "url": post.get("url", post.get("permalink", "")),
                "snippet": sanitize(post.get("selftext", post.get("body", "")), 500),
                "score": post.get("score", 0),
                "subreddit": post.get("subreddit", ""),
                "num_comments": post.get("num_comments", 0),
                "created_utc": post.get("created_utc", ""),
                "platform": platform,
                "source": "reddit",
            })

    # Version relevance filter
    if version:
        def _relevant(item):
            if item.get("severity") in ("critical", "high"):
                return True
            if item.get("category") in ("regression", "diamond_lobster", "active"):
                return True
            text = (item.get("title", "") + " " + item.get("snippet", "")).lower()
            return any(variant in text for variant in (version, f"v{version}", f"v {version}"))

        before = len(posts)
        posts = [p for p in posts if _relevant(p)]
        print(f"  Filtered Reddit: {before} → {len(posts)}")

    return posts


# ═══════════════════════════════════════════════════════════════════════════
#  Main collection entry point
# ═══════════════════════════════════════════════════════════════════════════

def collect(output_path=None) -> dict:
    """Run the full collection pipeline. Returns the raw data dict and saves to disk.

    Implements:
    - Pipeline timeout guard (PipelineTimer)
    - Data completeness gate (abort if critical sources fail)
    - Deduplication of GitHub issues and Reddit posts
    """
    import time as _time
    output_path = output_path or config.RAW_DATA_FILE
    now = now_iso()
    source_status = SourceStatus()

    print(f"\n{'='*60}")
    print(f"OpenClaw Status — Data Collection")
    print(f"Time: {now}")
    print(f"{'='*60}\n")

    # Use PipelineTimer for timeout budget (default 15 min)
    timer = PipelineTimer(timeout=900)
    timer.__enter__()

    try:
        # 1. npm
        npm = fetch_npm_version()
        source_status.record("npm", "ok" if npm else "failed", npm.get("version", "?") if npm else "")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after npm")

        # 2. Stable release
        release = fetch_latest_release()
        source_status.record("github_release", "ok" if release else "failed",
                             release.get("tag", "?") if release else "")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after release")

        # 3. Pre-release (dynamic, most recent 3)
        prerelease = None
        for tag in find_prerelease_tags(limit=3):
            prerelease = fetch_release_by_tag(tag)
            if prerelease:
                print(f"  Found pre-release: {prerelease['tag']}")
                break
        if not prerelease:
            print("  No pre-release found")
        source_status.record("prerelease", "ok" if prerelease else "empty")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after prerelease")

        # Determine version (guard against None release)
        version = version_from_release(release) if release else ""
        if not version:
            version = (npm or {}).get("version", "")

        print(f"\n📌 Target version: {version or 'unknown'}")
        if prerelease:
            print(f"📌 Pre-release: {prerelease['tag']} (fixes pending in stable)")
        print()

        # 4. Changelog
        changelog = fetch_changelog(version)
        source_status.record("changelog", "ok" if changelog else "empty",
                             f"{len(changelog)} chars")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after changelog")

        # 5. Releases page
        releases_page = fetch_releases_page()
        source_status.record("releases_page", "ok" if releases_page else "empty",
                             f"{len(releases_page)} chars")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after releases page")

        # 6. Clawsweeper state
        clawsweeper = fetch_clawsweeper_state()
        source_status.record("clawsweeper", "ok" if clawsweeper.get("work_candidates") else "empty")

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after clawsweeper")

        # 7. GitHub issues
        issues = fetch_github_issues(
            release_body=release.get("body", "") if release else "",
            prerelease_body=prerelease.get("body", "") if prerelease else "",
            release_date=release.get("published_at", "") if release else "",
            version=version,
            status=source_status,
        )

        # 8. Enrich with clawsweeper records
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

        if timer.check():
            return _save_partial(output_path, source_status, now, "timeout after issue enrichment")

        # 9. Reddit
        reddit = fetch_reddit(version)
        source_status.record("reddit", "ok" if reddit else "empty", f"{len(reddit)} posts")
    finally:
        timer.__exit__(None, None, None)

    # ── DATA COMPLETENESS GATE ──
    # Critical sources: npm OR github_release must be ok.
    # If both failed, abort pipeline — don't send garbage to the LLM.
    npm_ok = source_status.results.get("npm", {}).get("status") == "ok"
    release_ok = source_status.results.get("github_release", {}).get("status") == "ok"
    if not npm_ok and not release_ok:
        print("\n❌ DATA COMPLETENESS GATE: Both npm and github_release failed!", file=sys.stderr)
        print("   Aborting pipeline — insufficient data for LLM assessment.", file=sys.stderr)
        raw = {
            "collected_at": now,
            "target_version": "",
            "sources": {
                "npm": npm,
                "latest_release": release,
                "latest_prerelease": prerelease,
                "changelog": "",
                "releases_page": "",
                "clawsweeper": {},
                "github_issues": [],
                "reddit": [],
            },
            "meta": {"collector_version": "1.0.0", "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}"},
            "source_status": source_status.results,
            "pipeline_aborted": True,
            "abort_reason": "Both npm and github_release sources failed",
        }
        save_json(output_path, raw)
        print(f"💾 Saved aborted raw data to: {output_path}")
        return raw

    # ── DEDUPLICATION ──
    # Dedup GitHub issues by issue number, keeping the highest-severity category.
    # Severity priority: critical > high > medium > low
    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    _CAT_SEV = {"diamond_lobster": "critical", "regression": "critical", "active": "high"}
    seen_issues = {}
    for issue in issues:
        num = issue.get("number")
        if num is None:
            continue
        cat = issue.get("category", "active")
        sev = issue.get("severity", _CAT_SEV.get(cat, "high"))
        sev_rank = _SEV_ORDER.get(sev, 99)
        if num not in seen_issues:
            seen_issues[num] = issue
        else:
            existing_cat = seen_issues[num].get("category", "active")
            existing_sev = seen_issues[num].get("severity", _CAT_SEV.get(existing_cat, "high"))
            existing_rank = _SEV_ORDER.get(existing_sev, 99)
            if sev_rank < existing_rank:
                seen_issues[num] = issue
    deduped_issues = list(seen_issues.values())
    if len(deduped_issues) != len(issues):
        print(f"  🔄 Deduplicated issues: {len(issues)} → {len(deduped_issues)}")
    issues = deduped_issues

    # Dedup Reddit posts by id/permalink
    seen_reddit = {}
    for post in reddit:
        pid = post.get("url", post.get("title", ""))
        if pid and pid not in seen_reddit:
            seen_reddit[pid] = post
    deduped_reddit = list(seen_reddit.values())
    if len(deduped_reddit) != len(reddit):
        print(f"  🔄 Deduplicated Reddit: {len(reddit)} → {len(deduped_reddit)}")
    reddit = deduped_reddit

    # Helpers for metadata
    def _counts(items, key):
        counts = {}
        for item in items:
            val = item.get(key, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts

    raw = {
        "collected_at": now,
        "target_version": version,
        "sources": {
            "npm": npm,
            "latest_release": release,
            "latest_prerelease": prerelease,
            "changelog": changelog,
            "releases_page": releases_page,
            "clawsweeper": clawsweeper,
            "github_issues": issues,
            "reddit": reddit,
        },
        "meta": {
            "collector_version": "1.0.0",
            "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}",
            "sources_count": {"github_issues": len(issues), "reddit_posts": len(reddit)},
            "platform_coverage": {
                "github_issues": _counts(issues, "platform"),
                "reddit": _counts(reddit, "platform"),
            },
            "issue_categories": _counts(issues, "category"),
        },
    }

    # Summary
    print(f"\n{'='*60}")
    print("Collection complete:")
    print(source_status.summary())
    cats = _counts(issues, "category")
    if cats:
        print(f"  📊 Categories: {', '.join(f'{k}:{v}' for k, v in cats.items())}")
    print(f"{'='*60}\n")

    # Include source status in raw data for downstream checks
    raw["source_status"] = source_status.results

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
        "meta": {"collector_version": "1.0.0", "repo": f"{config.REPO_OWNER}/{config.REPO_NAME}"},
        "source_status": source_status.results,
        "pipeline_aborted": True,
        "abort_reason": reason,
    }
    save_json(output_path, raw)
    return raw
