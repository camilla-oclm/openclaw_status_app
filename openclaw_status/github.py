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

from openclaw_status import config, release_changes
from openclaw_status.lib import _retry, sanitize, load_json, save_json, parallel_fetch


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
    tag = d.get("tag_name", "")
    return {
        "tag": tag,
        # Clean version (no leading "v"), so consumers don't have to re-derive it
        # from the tag/url. Matches the `lstrip("v")` convention used elsewhere.
        "version": tag.lstrip("v"),
        "name": d.get("name", ""),
        "published_at": d.get("published_at", "") or "",
        # Store the curated sections (Highlights/Changes/Fixes/Breaking), not a flat head-slice:
        # the old 5000-char cut dropped the whole ### Fixes section on big releases, so "fixes
        # shipped" rendered as 0. Curate first (the raw body still has the ### headers), then
        # sanitize.
        "body": sanitize(release_changes.curated_changelog(d.get("body", "")), 20000),
        # Issue-closing "fixes/closes/resolves #N" refs live in the PR-log tail that
        # curated_changelog() drops, so they must be extracted from the RAW body HERE —
        # re-parsing the curated `body` downstream finds nothing (the latent bug that left
        # every issue's fixed_in inert). Strings, sorted for a stable/serializable value.
        "closing_refs": sorted(extract_closing_refs(d.get("body", ""))),
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


def _release_base_nums(tag: str) -> tuple:
    """The numeric (major, minor, patch, …) of a release tag, ignoring any
    ``-prerelease`` suffix: ``v2026.6.8-beta.2`` → ``(2026, 6, 8)``. Non-numeric
    parts collapse to 0 so the result is always tuple-comparable."""
    base = (tag or "").strip().lstrip("v").partition("-")[0]
    return tuple(int(p) if p.isdigit() else 0 for p in base.split(".") if p != "")


def latest_prerelease(releases: list[dict] = None, stable: dict | str | None = None) -> dict | None:
    """Most recent non-draft pre-release that is genuinely AHEAD of the stable.

    A pre-release whose base version is <= the current stable — e.g.
    ``v2026.6.8-beta.2`` once ``v2026.6.8`` has shipped — is the beta that
    *preceded* that stable, not a future fix-bearing release. Surfacing it would
    flag a "staged fix" for something already out, so such pre-releases are
    dropped. ``stable`` may be a release dict or a tag string; when omitted, no
    version filter is applied (legacy behaviour)."""
    releases = releases if releases is not None else list_releases()
    pres = [r for r in releases if r.get("prerelease") and not r.get("draft")]
    stable_tag = stable.get("tag", "") if isinstance(stable, dict) else (stable or "")
    if stable_tag:
        base = _release_base_nums(stable_tag)
        pres = [r for r in pres if _release_base_nums(r.get("tag", "")) > base]
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
# The repo's REAL serious-harm labels (verified against the live label list —
# there is no bare "impact:data"; the actual label is "impact:data-loss", and
# "impact:crash-loop" exists too). These names feed BOTH the severity floor
# (substring match, tolerant) and the guaranteed scout searches (GitHub label
# search is EXACT — a wrong name there is a dead search that returns nothing).
_SERIOUS_IMPACT = (
    "impact:security", "impact:data-loss", "impact:crash-loop",
    "impact:message-loss", "impact:session-state", "impact:auth-provider",
)
_BUG_KEYWORDS = ("regression", "crash", "data-loss", "data loss", "dataloss")

# Labels the severity model trusts as critical/high signals. Each gets its OWN guaranteed
# inclusion search in the scout so a severe-but-unpopular issue can't fall outside the broad
# recency cut (audit H2: P0 — the most-severe priority — and the serious-impact labels were
# missing as guaranteed searches while the LESS-severe P1 was guaranteed in).
_GUARANTEED_LABELS = ("regression", "bug:crash", "P0", "P1") + _SERIOUS_IMPACT


def _label_q(name: str) -> str:
    """A GitHub search label qualifier, quoting names that contain a colon (bug:crash, impact:*)."""
    return f'label:"{name}"' if ":" in name else f"label:{name}"

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


def version_match(text: str, version: str) -> str:
    """How specifically `text` pins the assessed version: "exact" (names this exact
    version), "series" (names only its minor series, e.g. 2026.6), or "none".

    The distinction matters for weighting: on a mature series nearly EVERY open issue
    mentions the series somewhere, so a series match barely discriminates — while an
    issue that names this exact release is direct confirmation it applies.
    """
    if not version:
        return "none"
    t = (text or "").lower()
    v = version.lower()
    # Exact: the full version as a number-token ("2026.6.11"/"v2026.6.11"), not a
    # prefix of a longer version ("2026.6.11-beta.1" still counts — same version).
    if re.search(r"(?<!\d)" + re.escape(v) + r"(?!\.?\d)", t) is not None:
        return "exact"
    parts = v.split(".")
    if len(parts) >= 2:
        series = ".".join(parts[:2])
        if not series:
            return "none"
        # Match the minor series as a whole number-token so "2026.6" doesn't also
        # swallow "2026.60" / "2026.66" (different series) or digits inside a larger
        # number. A same-series patch ("2026.6.1") still matches — a regression in the
        # series may persist in this release — but a different series no longer does.
        if re.search(r"(?<!\d)" + re.escape(series) + r"(?!\d)", t) is not None:
            return "series"
    return "none"


def version_relevant(text: str, version: str) -> bool:
    """True if `text` mentions the assessed version or its minor series (e.g. 2026.6)."""
    return version_match(text, version) != "none"


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
    community impact, bumped one level for serious harm (security / data-loss /
    crash-loop / message-loss / session-state) or a regression/crash label."""
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


def categorize(created_at: str, labels, affects_version: bool, impact: str,
               release_date: str, title: str = "") -> str:
    """regression (a CONFIRMED regression — `regression` label or a "regression" title)
    > post_release (filed after the release & affects this version, but not confirmed as a
    regression) > diamond_lobster (top-rated tracked issue) > active.

    A bug merely filed after a release isn't necessarily a *regression* (worked before,
    now broken) — calling every post-release issue a "regression" overstates the breakage.
    So a regression must be explicitly flagged; the rest are honestly "post-release"."""
    low = [str(l).lower() for l in (labels or [])]
    after_release = bool(release_date) and (created_at or "")[:10] >= release_date[:10]
    if "regression" in low or "regression" in (title or "").lower():
        return "regression"
    if after_release and affects_version:
        return "post_release"
    if is_diamond(labels):
        return "diamond_lobster"
    return "active"


def normalize_node(node: dict, release_date: str = "", version: str = "") -> dict:
    """Turn a raw GraphQL Issue node into the collector's issue dict, computing
    reactions, version relevance, impact, severity and category."""
    labels = [l["name"] for l in (node.get("labels") or {}).get("nodes", []) if l.get("name")]
    thumbs = (node.get("thumbsUp") or {}).get("totalCount", 0)
    comments = (node.get("comments") or {}).get("totalCount", 0)
    title = node.get("title", "") or ""
    body = node.get("bodyText", "") or ""
    created = node.get("createdAt", "")
    vm = version_match(title + " " + body, version)
    affects = vm != "none"
    impact = impact_level(thumbs, comments)
    # (Vestigial fields dropped 2026-07: snippet / comments_data / platform:"general" /
    # is_feature / total_reactions had no readers anywhere — features are filtered out
    # in scout_issues before storage, and the taxonomy uses per-issue platforms/components.)
    return {
        "number": node.get("number"),
        "title": sanitize(title),
        "url": node.get("url", ""),
        "body": sanitize(body, 2000),
        "comments": comments,
        "reactions": thumbs,
        "updated_at": node.get("updatedAt", ""),
        "created_at": created,
        "labels": labels,
        "author": (node.get("author") or {}).get("login", ""),
        "priority": priority_of(labels),
        "affects_version": affects,
        "version_match": vm,
        "impact": impact,
        "severity": derive_severity(labels, thumbs, comments),
        "category": categorize(created, labels, affects, impact, release_date, title),
        "source": "github_api",
    }


_SEV_WEIGHT = {"critical": 3, "high": 2, "medium": 1, "low": 0}

# importance_weight point tables. Severity anchors the scale; version specificity is
# the next-strongest signal (an issue naming THIS exact release is direct confirmation;
# a bare series mention barely discriminates — on a mature series almost every open
# issue carries one); a CONFIRMED regression outweighs a plain post-release report; a
# shipped/staged fix is relief, not a current blocker. Community engagement is
# deliberately NOT part of the weight: it breaks ties WITHIN a weight (rank_key), so a
# viral thread can't cross a severity/version tier — preserving the documented
# invariants (a version-relevant high above an off-version critical however loud; a
# version-relevant low still below a genuine critical).
_W_SEV = {"critical": 40, "high": 26, "medium": 14, "low": 5}
_W_VER = {"exact": 26, "series": 16, "none": 0}
_W_CAT = {"regression": 12, "post_release": 6, "diamond_lobster": 4, "active": 0}


def importance_weight(issue: dict) -> int:
    """Structural importance score (0–100, ~82 practical max) — the ranking signal.

    Built to DISCRIMINATE where the coarse fields saturate: on a busy release the
    ledger converges on "high severity / affects this version" for nearly everything,
    which reads as 60 equal issues. The blend separates them by version specificity,
    confirmed-regression status and triage signals; it's a pure function of stored
    fields so the ledger re-derives it every run (like severity/category).
    """
    pts = _W_SEV.get(str(issue.get("severity") or "").lower(), 5)
    vm = issue.get("version_match")
    if vm not in _W_VER:  # older records / fixtures: fall back to the boolean flag
        vm = "series" if issue.get("affects_version") else "none"
    pts += _W_VER[vm]
    pts += _W_CAT.get(str(issue.get("category") or "").lower(), 0)
    cs = issue.get("clawsweeper")
    if isinstance(cs, dict) and cs.get("decision") == "keep_open":
        pts += 4  # expert triage confirms it's real and unresolved
    if issue.get("fixed_in"):
        pts -= 25  # staged/shipped fix: surface as relief, don't let it drive the verdict
    return max(0, min(100, pts))


def rank_key(issue: dict):
    """Sort key (ascending) — importance weight first, community impact (👍·2 +
    comments) breaking ties WITHIN a weight, then issue number for determinism.
    Engagement can order equally-important issues but never cross a tier."""
    impact = int(issue.get("reactions") or 0) * 2 + int(issue.get("comments") or 0)
    return (-importance_weight(issue), -impact, int(issue.get("number") or 0))


def scout_issues(release_date: str = "", version: str = "", limit: int = 25,
                 coverage: dict | None = None) -> list | None:
    """Scout the repo's issues via the direct API, ranked by impact.

    These searches decide only which issues are ELIGIBLE to rank — the severity-aware
    rank_key (below) does the final ordering. All exclude `enhancement` so feature
    requests don't drown out defects:
      1. opened since the release, newest first — the broad sweep; candidate regressions
         (NOT gated on label:bug, so freshly-filed un-triaged breakage is still caught)
      2. one guaranteed-inclusion search per severity-critical label (`_GUARANTEED_LABELS`:
         regression, bug:crash, P0, P1, and the serious `impact:*` labels), newest first —
         so a severe issue can't be dropped just for being unpopular or past the broad cut
      3. most-reacted open issues overall (ongoing majors of any age)

    The post-release window (1–2) is sorted by RECENCY, not reactions: on a fresh release
    every new issue still sits at ~0 👍, so a reaction sort there degenerates into an
    arbitrary cut that drops severe regressions purely because nobody has thumbed them yet
    (the `limit` cap then makes it worse). Recency is the real signal for "what just broke";
    the per-label searches (2) backstop it so a severe-but-unpopular issue can't fall outside
    the cut. For the aged most-reacted search (3), reactions ARE meaningful — issues have had
    time to accumulate them. When no release date is known, the priority labels (P0 then P1)
    are searched by reactions instead.

    Feature requests / proposals are dropped (a wished-for feature is no reason to
    skip an update). Only `stale` issues are skipped — NOT `clawsweeper:no-new-fix-pr`,
    which marks issues that have no fix yet (exactly the ones we care about).

    Returns ranked, de-duplicated issue dicts, or None if the API is unavailable.

    `coverage` (optional, mutated in place) reports search completeness so the caller can
    fail closed on a partial scout: `queries_total`, `queries_ok`, and `broad_ok` (whether
    the broad post-release recency sweep — query #1, the only one that catches freshly-filed,
    un-triaged, 0-reaction breakage — succeeded; None when no release date is known). A
    GitHub search secondary-rate-limit returns an `errors` payload with HTTP 200, so a single
    dropped query would otherwise look identical to a genuinely clean release.
    """
    repo = f"repo:{config.REPO_OWNER}/{config.REPO_NAME}"
    no_feat = "-label:enhancement"
    queries = []
    if release_date:
        since = f"created:>={release_date[:10]}"
        # Query #0: the broad, no-label recency sweep — the only search that surfaces
        # freshly-filed, un-triaged, 0-reaction breakage. (Tracked apart for coverage below.)
        queries.append(f"{repo} is:issue is:open {since} {no_feat} sort:created-desc")
        # Guaranteed-inclusion: one recency search per severity-critical label, so a severe
        # issue can't be dropped just because it's unpopular or sits past the broad cut. Repo
        # priority labels are reaction-flat on a fresh release, so sort these by recency too;
        # aged ones that still matter resurface via the most-reacted search below.
        for lbl in _GUARANTEED_LABELS:
            queries.append(f"{repo} is:issue is:open {since} {_label_q(lbl)} {no_feat} sort:created-desc")
    else:
        # No release date known: rank the top priority labels by reactions (aged issues have
        # had time to accumulate them). P0 first so the most-severe is guaranteed in.
        queries.append(f"{repo} is:issue is:open {_label_q('P0')} {no_feat} sort:reactions-+1-desc")
        queries.append(f"{repo} is:issue is:open {_label_q('P1')} {no_feat} sort:reactions-+1-desc")
    queries.append(f"{repo} is:issue is:open {no_feat} sort:reactions-+1-desc")

    # Run the searches concurrently — the queries are independent, and up to 11 sequential
    # GraphQL round-trips dominated collect wall-time (the reason COLLECT_TIMEOUT_S sits at
    # 480s). parallel_fetch returns results POSITION-ALIGNED with `queries`, so aggregation
    # walks the ORIGINAL query order and coverage counts one outcome per query occurrence —
    # dedup winners, coverage counts, and the final ranking are identical to the serial path
    # (and correct even if two queries were ever identical). max_workers stays low for
    # GitHub's search secondary rate limit; per-call retries live inside search_issues,
    # and a query that fails (None, or an unexpected raise → None) counts as dropped.
    results = parallel_fetch(lambda q: search_issues(q, limit), queries, max_workers=3)

    seen, issues, any_ok = set(), [], False
    ok_count, broad_ok = 0, None
    for idx, q in enumerate(queries):
        nodes = results[idx]
        # Query #1 in the post-release window is the broad, no-label recency sweep — the only
        # search that surfaces un-triaged, un-thumbed, freshly-filed breakage. Track it apart.
        if release_date and idx == 0:
            broad_ok = nodes is not None
        if nodes is None:
            continue
        any_ok = True
        ok_count += 1
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

    if coverage is not None:
        coverage["queries_total"] = len(queries)
        coverage["queries_ok"] = ok_count
        coverage["broad_ok"] = broad_ok

    if not any_ok:
        return None  # API totally unavailable
    issues.sort(key=rank_key)
    return issues
