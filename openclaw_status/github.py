"""Direct GitHub API client + issue-scouting logic.

Preferred over Composio for GitHub reads when GITHUB_TOKEN is set: a single
GraphQL query returns issues together with reaction counts, comment counts, body
text and labels. That lets us rank candidates by community impact (👍) and flag
version relevance — neither of which the Composio path can surface — and avoids
spawning a subprocess (and scraping JSON from stdout) per call.

When no token is set, the collector falls back to Composio (see collector.py).
"""

import json
import re
import sys
import urllib.error
import urllib.request

from openclaw_status import config
from openclaw_status.lib import _retry, sanitize


# ═══════════════════════════════════════════════════════════════════════════
#  API client
# ═══════════════════════════════════════════════════════════════════════════

DIAMOND_LABEL = "issue-rating: 🦞 diamond lobster"

_SEARCH_QUERY = """
query($q: String!, $n: Int!) {
  search(query: $q, type: ISSUE, first: $n) {
    issueCount
    nodes {
      ... on Issue {
        number title url state createdAt updatedAt
        author { login }
        comments { totalCount }
        reactions { totalCount }
        thumbsUp: reactions(content: THUMBS_UP) { totalCount }
        labels(first: 20) { nodes { name } }
        bodyText
      }
    }
  }
}"""


def has_token() -> bool:
    return bool(config.GITHUB_TOKEN)


def gh_graphql(query: str, variables: dict = None, timeout: int = 30) -> dict | None:
    """Run a GraphQL query against the GitHub API. Returns the `data` object, or
    None if there's no token or the call fails."""
    if not config.GITHUB_TOKEN:
        return None
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()

    def _call():
        req = urllib.request.Request(
            config.GITHUB_GRAPHQL_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "openclaw-status",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if data.get("errors"):
            raise RuntimeError(str(data["errors"])[:200])
        return data.get("data")

    try:
        result, _ = _retry(_call)
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.readable() else ""
        print(f"  ⚠ GitHub GraphQL HTTP {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ⚠ GitHub GraphQL failed: {e}", file=sys.stderr)
        return None


def search_issues(query_string: str, limit: int = 25, timeout: int = 30) -> list | None:
    """Search issues. Returns raw Issue nodes, or None if the API is unavailable."""
    data = gh_graphql(_SEARCH_QUERY, {"q": query_string, "n": min(limit, 100)}, timeout)
    if data is None:
        return None
    nodes = (data.get("search") or {}).get("nodes") or []
    return [n for n in nodes if n]  # drop nulls (non-Issue search hits)


# ═══════════════════════════════════════════════════════════════════════════
#  Scouting logic (pure — unit tested)
# ═══════════════════════════════════════════════════════════════════════════

# Issue numbers a release/PR body claims to close, via GitHub closing keywords.
# (These reference ISSUES — unlike bare "#123" which is usually a PR number.)
_CLOSING_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)

# Label substrings that denote a high-severity issue regardless of reactions.
_HIGH_SEV_LABELS = (
    "regression", "crash", "data-loss", "data loss", "dataloss",
    "severity: high", "severity: critical", "priority: high", "priority: critical",
    "p0", "p1", "s1", "blocker",
)


def extract_closing_refs(body: str) -> set:
    """Issue numbers a release/PR body says it fixes (fixes/closes/resolves #N)."""
    return set(_CLOSING_RE.findall(body or ""))


def is_diamond(labels) -> bool:
    return any("diamond lobster" in str(l).lower() for l in (labels or []))


def version_relevant(text: str, version: str) -> bool:
    """True if `text` mentions the assessed version or its minor series (e.g. 2026.6)."""
    if not version:
        return False
    t = (text or "").lower()
    v = version.lower()
    if v in t or ("v" + v) in t:
        return True
    parts = v.split(".")
    if len(parts) >= 2:
        series = ".".join(parts[:2])
        return bool(series) and series in t
    return False


def impact_level(thumbs_up: int, comments: int) -> str:
    """Community-impact bucket from 👍 reactions + comment volume."""
    thumbs_up, comments = int(thumbs_up or 0), int(comments or 0)
    score = thumbs_up * 2 + comments
    if thumbs_up >= 10 or score >= 25:
        return "high"
    if thumbs_up >= 3 or score >= 8:
        return "medium"
    return "low"


def derive_severity(labels, thumbs_up: int, comments: int) -> str:
    """Severity from maintainer labels first, then community-impact signals."""
    if is_diamond(labels):
        return "critical"
    low = [str(l).lower() for l in (labels or [])]
    if any(any(k in l for k in _HIGH_SEV_LABELS) for l in low):
        return "high"
    thumbs_up, comments = int(thumbs_up or 0), int(comments or 0)
    if thumbs_up >= 10 or comments >= 25:
        return "high"
    if thumbs_up >= 3 or comments >= 8:
        return "medium"
    return "low"


def categorize(created_at: str, labels, affects_version: bool, impact: str, release_date: str) -> str:
    """diamond_lobster (severity label) > regression (post-release & relevant) > active."""
    if is_diamond(labels):
        return "diamond_lobster"
    low = [str(l).lower() for l in (labels or [])]
    after_release = bool(release_date) and (created_at or "")[:10] >= release_date[:10]
    if after_release and (affects_version or "regression" in low or impact == "high"):
        return "regression"
    return "active"


def normalize_node(node: dict, release_date: str = "", version: str = "") -> dict:
    """Turn a raw GraphQL Issue node into the collector's issue dict, computing
    reactions, version relevance, impact, severity and category."""
    labels = [l["name"] for l in (node.get("labels") or {}).get("nodes", []) if l.get("name")]
    thumbs = (node.get("thumbsUp") or {}).get("totalCount", 0)
    total_reactions = (node.get("reactions") or {}).get("totalCount", 0)
    comments = (node.get("comments") or {}).get("totalCount", 0)
    title = node.get("title", "") or ""
    body = node.get("bodyText", "") or ""
    created = node.get("createdAt", "")
    affects = version_relevant(title + " " + body, version)
    impact = impact_level(thumbs, comments)
    return {
        "number": node.get("number"),
        "title": sanitize(title),
        "url": node.get("url", ""),
        "body": sanitize(body, 2000),
        "snippet": sanitize(body, 500),
        "comments": comments,
        "comments_data": [],
        "reactions": thumbs,
        "total_reactions": total_reactions,
        "updated_at": node.get("updatedAt", ""),
        "created_at": created,
        "labels": labels,
        "author": (node.get("author") or {}).get("login", ""),
        "platform": "general",
        "affects_version": affects,
        "impact": impact,
        "severity": derive_severity(labels, thumbs, comments),
        "category": categorize(created, labels, affects, impact, release_date),
        "source": "github_api",
    }


_SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def rank_key(issue: dict):
    """Sort key: severity, then version relevance, then community impact."""
    return (
        _SEV_RANK.get(issue.get("severity"), 9),
        not issue.get("affects_version"),
        -(int(issue.get("reactions") or 0) * 2 + int(issue.get("comments") or 0)),
    )


def scout_issues(release_date: str = "", version: str = "", limit: int = 25) -> list | None:
    """Scout the repo's issues via the direct API, ranked by impact.

    Runs three searches (all sorted by 👍 so the most-felt issues come first):
      1. opened since the release  — candidate regressions (NOT gated on label:bug,
         so freshly-filed un-triaged breakage is still caught)
      2. the diamond-lobster severity label
      3. most-reacted open issues overall (ongoing majors of any age)

    Returns ranked, de-duplicated issue dicts, or None if the API is unavailable
    (so the caller can fall back to Composio).
    """
    repo = f"repo:{config.REPO_OWNER}/{config.REPO_NAME}"
    queries = []
    if release_date:
        queries.append(f"{repo} is:issue is:open created:>={release_date[:10]} sort:reactions-+1-desc")
    queries.append(f'{repo} is:issue is:open label:"{DIAMOND_LABEL}" sort:reactions-+1-desc')
    queries.append(f"{repo} is:issue is:open sort:reactions-+1-desc")

    seen, issues, any_ok = set(), [], False
    for q in queries:
        nodes = search_issues(q, limit)
        if nodes is None:
            continue
        any_ok = True
        for node in nodes:
            num = node.get("number")
            if num is None or num in seen:
                continue
            labels = [l.get("name", "") for l in (node.get("labels") or {}).get("nodes", [])]
            if any(t in labels for t in ("stale", "clawsweeper:no-new-fix-pr")):
                continue
            seen.add(num)
            issues.append(normalize_node(node, release_date, version))

    if not any_ok:
        return None  # API totally unavailable
    issues.sort(key=rank_key)
    return issues
