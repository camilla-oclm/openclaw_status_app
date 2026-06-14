"""GitHub API client + issue-scouting logic.

All GitHub data — issues and releases — is read through the GitHub API with a
token (`GITHUB_TOKEN`). A single GraphQL query returns issues together with
reaction counts, comments, body text and labels, so we can rank candidates by
community impact (👍) and flag version relevance in one round trip; releases come
from the REST API.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

from openclaw_status import config
from openclaw_status.lib import _retry, sanitize, load_json, save_json


# ═══════════════════════════════════════════════════════════════════════════
#  API client
# ═══════════════════════════════════════════════════════════════════════════

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


def _load_etag_cache() -> dict:
    if config.ETAG_CACHE_FILE.exists():
        try:
            return load_json(config.ETAG_CACHE_FILE)
        except Exception:
            return {}
    return {}


def _save_etag_cache(cache: dict) -> None:
    # Bound the cache so it can't grow unbounded across many release tags.
    if len(cache) > 200:
        cache = dict(list(cache.items())[-200:])
    try:
        save_json(config.ETAG_CACHE_FILE, cache)
    except Exception:
        pass


def gh_rest(path: str, timeout: int = 30):
    """GET a GitHub REST API path (e.g. '/repos/o/r/releases'). Returns parsed
    JSON, or None on error / when no token is set.

    Uses ETag conditional requests: the previous ETag is sent as If-None-Match, and
    a 304 (Not Modified) is served from the on-disk cache — no re-download, and the
    request doesn't count against the GitHub rate limit."""
    if not config.GITHUB_TOKEN:
        return None

    cache = _load_etag_cache()
    cached = cache.get(path)

    def _call():
        headers = {
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "openclaw-status",
        }
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        req = urllib.request.Request(config.GITHUB_API_URL + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
                etag = resp.headers.get("ETag")
            if etag:
                cache[path] = {"etag": etag, "data": body}
                _save_etag_cache(cache)
            return body
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return cached["data"]  # Not Modified — serve from cache
            raise

    try:
        result, _ = _retry(_call)
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.readable() else ""
        print(f"  ⚠ GitHub REST {path} HTTP {e.code}: {body[:150]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  ⚠ GitHub REST {path} failed: {e}", file=sys.stderr)
        return None


def fetch_raw(owner: str, repo: str, ref: str, path: str, timeout: int = 20) -> str:
    """Fetch a raw file from a GitHub repo (raw.githubusercontent.com). Sends the
    token if present (helps rate limits / private). Returns text, or '' on error."""
    url = (f"{config.GITHUB_RAW_URL}/{quote(owner, safe='')}/{quote(repo, safe='')}"
           f"/{quote(ref, safe='')}/{quote(path, safe='/')}")
    headers = {"User-Agent": "openclaw-status"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  ⚠ raw {path} HTTP {e.code}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"  ⚠ raw {path} failed: {e}", file=sys.stderr)
        return ""


# ═══════════════════════════════════════════════════════════════════════════
#  Releases (REST)
# ═══════════════════════════════════════════════════════════════════════════

def _norm_release(d: dict | None) -> dict | None:
    if not d:
        return None
    return {
        "tag": d.get("tag_name", ""),
        "name": d.get("name", ""),
        "published_at": d.get("published_at", "") or "",
        "body": sanitize(d.get("body", ""), 5000),
        "url": d.get("html_url", ""),
        "prerelease": d.get("prerelease", False),
        "draft": d.get("draft", False),
    }


def latest_release() -> dict | None:
    """The latest published, non-prerelease release."""
    return _norm_release(gh_rest(f"/repos/{config.REPO_OWNER}/{config.REPO_NAME}/releases/latest"))


def list_releases(limit: int = 30) -> list[dict]:
    """Recent releases (newest first), including pre-releases and drafts."""
    data = gh_rest(f"/repos/{config.REPO_OWNER}/{config.REPO_NAME}/releases?per_page={limit}")
    if not isinstance(data, list):
        return []
    return [r for r in (_norm_release(d) for d in data) if r]


def latest_prerelease(releases: list[dict] = None) -> dict | None:
    """Most recent non-draft pre-release, from a release list (fetched if None)."""
    releases = releases if releases is not None else list_releases()
    pres = [r for r in releases if r.get("prerelease") and not r.get("draft")]
    pres.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    return pres[0] if pres else None


# ═══════════════════════════════════════════════════════════════════════════
#  Scouting logic (pure — unit tested)
# ═══════════════════════════════════════════════════════════════════════════

# Issue numbers a release/PR body claims to close, via GitHub closing keywords.
# (These reference ISSUES — unlike bare "#123" which is usually a PR number.)
_CLOSING_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)

# OpenClaw severity model (learned from the real repo's labels):
#   • Maintainer priority labels P0..P4 are the primary severity signal.
#   • "impact:*" labels mark the kind of harm; the serious ones bump severity.
#   • "issue-rating: 🦞 diamond lobster" is a quality/notability RATING (also put on
#     feature requests) — it is NOT a severity, so it no longer forces "critical".
_SEV = ["low", "medium", "high", "critical"]
_PRIORITY = {"p0": 3, "p1": 2, "p2": 1, "p3": 0, "p4": 0}
_SERIOUS_IMPACT = (
    "impact:security", "impact:data", "impact:message-loss",
    "impact:session-state", "impact:auth-provider",
)
_BUG_KEYWORDS = ("regression", "crash", "data-loss", "data loss", "dataloss")

# Feature/proposal markers — these are NOT issues impacting the version, so the
# scout drops them (a wished-for feature is no reason to skip an update).
_FEATURE_LABELS = ("enhancement", "feature", "feature request", "proposal")
_FEATURE_TITLE = ("[feature]", "feature request", "feat(", "[rfc]", "design proposal", "proposal:")


def extract_closing_refs(body: str) -> set:
    """Issue numbers a release/PR body says it fixes (fixes/closes/resolves #N)."""
    return set(_CLOSING_RE.findall(body or ""))


def is_feature(title: str, labels) -> bool:
    """True if the issue is a feature request / proposal rather than a defect."""
    low = [str(l).lower() for l in (labels or [])]
    if any(f in low for f in _FEATURE_LABELS):
        return True
    t = (title or "").lower()
    return any(k in t for k in _FEATURE_TITLE)


def priority_of(labels) -> str | None:
    """Maintainer priority label (P0..P4) mapped to severity, or None if absent."""
    ranks = [_PRIORITY[str(l).lower()] for l in (labels or []) if str(l).lower() in _PRIORITY]
    return _SEV[max(ranks)] if ranks else None


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


def derive_severity(labels, thumbs_up: int = 0, comments: int = 0) -> str:
    """Severity from the maintainer priority label (P0..P4) when present, else
    community impact, bumped one level for serious harm (security / data /
    message-loss / session-state) or a regression/crash label."""
    low = [str(l).lower() for l in (labels or [])]
    thumbs_up, comments = int(thumbs_up or 0), int(comments or 0)

    ranks = [_PRIORITY[l] for l in low if l in _PRIORITY]
    if ranks:
        base = max(ranks)
    elif thumbs_up >= 10 or comments >= 25:
        base = 2
    elif thumbs_up >= 3 or comments >= 8:
        base = 1
    else:
        base = 0

    # Breakage labels (regression/crash/data-loss) bump one level toward critical.
    # A serious harm area (security/data/message-loss/…) only floors at "high"
    # — it shouldn't, on its own, turn every high-priority bug into critical.
    if any(k in l for l in low for k in _BUG_KEYWORDS):
        base = min(base + 1, 3)
    elif any(any(s in l for s in _SERIOUS_IMPACT) for l in low):
        base = max(base, 2)
    return _SEV[base]


def categorize(created_at: str, labels, affects_version: bool, impact: str, release_date: str) -> str:
    """regression (post-release & affects this version, or labelled regression)
    > diamond_lobster (top-rated tracked issue) > active."""
    low = [str(l).lower() for l in (labels or [])]
    after_release = bool(release_date) and (created_at or "")[:10] >= release_date[:10]
    if "regression" in low or (after_release and affects_version):
        return "regression"
    if is_diamond(labels):
        return "diamond_lobster"
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
        "priority": priority_of(labels),
        "is_feature": is_feature(title, labels),
        "affects_version": affects,
        "impact": impact,
        "severity": derive_severity(labels, thumbs, comments),
        "category": categorize(created, labels, affects, impact, release_date),
        "source": "github_api",
    }


_SEV_WEIGHT = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def rank_key(issue: dict):
    """Sort key (ascending) blending severity and version relevance, tie-broken by
    community impact. Affecting THIS version is worth a strong boost: a version-
    relevant high ranks above a critical that's about some other version, but a
    version-relevant low still can't outrank a genuine critical."""
    score = _SEV_WEIGHT.get(issue.get("severity"), 0) * 2 + (3 if issue.get("affects_version") else 0)
    impact = int(issue.get("reactions") or 0) * 2 + int(issue.get("comments") or 0)
    return (-score, -impact)


def scout_issues(release_date: str = "", version: str = "", limit: int = 25) -> list | None:
    """Scout the repo's issues via the direct API, ranked by impact.

    Runs three searches (all sorted by 👍 so the most-felt issues come first, and
    all excluding `enhancement` so feature requests don't drown out defects):
      1. opened since the release  — candidate regressions (NOT gated on label:bug,
         so freshly-filed un-triaged breakage is still caught)
      2. maintainer-flagged top priority (label:P1)
      3. most-reacted open issues overall (ongoing majors of any age)

    Feature requests / proposals are dropped (a wished-for feature is no reason to
    skip an update). Only `stale` issues are skipped — NOT `clawsweeper:no-new-fix-pr`,
    which marks issues that have no fix yet (exactly the ones we care about).

    Returns ranked, de-duplicated issue dicts, or None if the API is unavailable.
    """
    repo = f"repo:{config.REPO_OWNER}/{config.REPO_NAME}"
    no_feat = "-label:enhancement"
    queries = []
    if release_date:
        queries.append(f"{repo} is:issue is:open created:>={release_date[:10]} {no_feat} sort:reactions-+1-desc")
    queries.append(f"{repo} is:issue is:open label:P1 {no_feat} sort:reactions-+1-desc")
    queries.append(f"{repo} is:issue is:open {no_feat} sort:reactions-+1-desc")

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
            if "stale" in labels:
                continue
            if is_feature(node.get("title", ""), labels):
                continue
            seen.add(num)
            issues.append(normalize_node(node, release_date, version))

    if not any_ok:
        return None  # API totally unavailable
    issues.sort(key=rank_key)
    return issues
