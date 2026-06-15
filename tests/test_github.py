"""Tests for openclaw_status.github — scouting logic (pure functions) + ETag cache."""
import json
import urllib.error

import pytest

from openclaw_status import github, config


# ── ETag caching in gh_rest ─────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, body, etag=None):
        self._body = json.dumps(body).encode()
        self.headers = {"ETag": etag} if etag else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gh_rest_caches_etag_then_serves_304(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(config, "ETAG_CACHE_FILE", tmp_path / "etag-cache.json")
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp({"v": 1}, etag='"abc"')  # first fetch: 200 + ETag
        raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(github.urllib.request, "urlopen", fake_urlopen)

    assert github.gh_rest("/repos/o/r/releases/latest") == {"v": 1}
    assert config.ETAG_CACHE_FILE.exists()
    # Second call → server says 304 Not Modified → served from cache, same data.
    assert github.gh_rest("/repos/o/r/releases/latest") == {"v": 1}
    assert calls["n"] == 2


def test_gh_rest_returns_none_without_token(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    assert github.gh_rest("/anything") is None


# ── extract_closing_refs ─────────────────────────────────────────────────────

def test_closing_refs_matches_keywords_only():
    body = "Fixes #123 and closes #456. Resolves #789. See also #999 and PR #1000."
    assert github.extract_closing_refs(body) == {"123", "456", "789"}


def test_closing_refs_case_insensitive_and_variants():
    assert github.extract_closing_refs("FIX #1, fixed #2, close #3, resolved #4") == {"1", "2", "3", "4"}


def test_closing_refs_empty():
    assert github.extract_closing_refs("") == set()
    assert github.extract_closing_refs(None) == set()


# ── is_diamond ───────────────────────────────────────────────────────────────

def test_is_diamond():
    assert github.is_diamond(["issue-rating: 🦞 diamond lobster", "bug"]) is True
    assert github.is_diamond(["bug", "regression"]) is False
    assert github.is_diamond([]) is False


# ── version_relevant ─────────────────────────────────────────────────────────

def test_version_relevant_exact():
    assert github.version_relevant("crash on 2026.6.1 at startup", "2026.6.1") is True
    assert github.version_relevant("upgraded to v2026.6.1", "2026.6.1") is True


def test_version_relevant_series():
    # mentions the minor series but not the exact patch
    assert github.version_relevant("broken since the 2026.6 line", "2026.6.1") is True


def test_version_relevant_negative():
    assert github.version_relevant("old issue from 2025.1", "2026.6.1") is False
    assert github.version_relevant("anything", "") is False
    assert github.version_relevant("", "2026.6.1") is False


def test_version_relevant_series_not_substring():
    # The series "2026.6" must match as a whole number-token, not as a substring of a
    # different series ("2026.60"/"2026.66") or glued to other digits.
    assert github.version_relevant("regression in 2026.60 build", "2026.6.6") is False
    assert github.version_relevant("seen on 2026.66", "2026.6.6") is False
    assert github.version_relevant("PR 12026.6 thousand", "2026.6.6") is False
    # …but a genuine series mention (and a same-series patch) still match.
    assert github.version_relevant("broke in the 2026.6 series", "2026.6.6") is True
    assert github.version_relevant("also affects 2026.6.1", "2026.6.6") is True


# ── impact_level ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("thumbs,comments,expected", [
    (10, 0, "high"), (0, 25, "high"), (15, 3, "high"),
    (3, 0, "medium"), (0, 8, "medium"), (1, 6, "medium"),
    (0, 7, "low"), (2, 2, "low"), (0, 0, "low"),
])
def test_impact_level(thumbs, comments, expected):
    assert github.impact_level(thumbs, comments) == expected


# ── is_feature / priority ────────────────────────────────────────────────────

def test_is_feature_by_label():
    assert github.is_feature("anything", ["enhancement"]) is True
    assert github.is_feature("x", ["P1", "bug"]) is False


def test_is_feature_by_title():
    assert github.is_feature("Feature Request: rename sessions", []) is True
    assert github.is_feature("[Feature]: collapse panel", []) is True
    assert github.is_feature("feat(cli): skills uninstall design proposal", []) is True
    assert github.is_feature("[Bug]: Telegram reconnect drain re-enters", []) is False
    assert github.is_feature("Regression: sidebar shown by default", []) is False


def test_priority_of():
    assert github.priority_of(["P1", "impact:security"]) == "high"
    assert github.priority_of(["P2"]) == "medium"
    assert github.priority_of(["P3"]) == "low"
    assert github.priority_of(["bug"]) is None


# ── derive_severity (priority + impact model) ────────────────────────────────

def test_derive_severity_diamond_is_not_critical():
    # diamond lobster is a quality rating, NOT a severity
    assert github.derive_severity(["issue-rating: 🦞 diamond lobster"], 0, 0) == "low"


def test_derive_severity_from_priority():
    assert github.derive_severity(["P1"], 0, 0) == "high"
    assert github.derive_severity(["P2"], 0, 0) == "medium"
    assert github.derive_severity(["P3"], 0, 0) == "low"


def test_derive_severity_breakage_label_bumps_to_critical():
    assert github.derive_severity(["P1", "regression"], 0, 0) == "critical"
    assert github.derive_severity(["P2", "crash"], 0, 0) == "high"


def test_derive_severity_serious_impact_floors_at_high_not_critical():
    # serious harm area alone shouldn't push a P1 to critical
    assert github.derive_severity(["P1", "impact:auth-provider"], 0, 0) == "high"
    assert github.derive_severity(["P2", "impact:message-loss"], 0, 0) == "high"


def test_derive_severity_from_reactions_when_no_priority():
    assert github.derive_severity(["impact:other"], 12, 0) == "high"
    assert github.derive_severity(["impact:other"], 4, 0) == "medium"
    assert github.derive_severity(["impact:other"], 0, 0) == "low"


# ── categorize ───────────────────────────────────────────────────────────────

def test_categorize_diamond():
    assert github.categorize("2026-01-01", ["issue-rating: 🦞 diamond lobster"], False, "low", "2026-06-03") == "diamond_lobster"


def test_categorize_regression_requires_confirmation():
    # A CONFIRMED regression — `regression` label or a "regression" title — regardless of timing.
    assert github.categorize("2026-06-05", ["regression"], False, "low", "2026-06-03") == "regression"
    assert github.categorize("2026-06-05", ["bug"], True, "low", "2026-06-03",
                             title="Regression: build fails") == "regression"
    # Post-release + affects this version but NOT confirmed as a regression → post_release,
    # NOT "regression" (a bug filed after a release isn't automatically a regression).
    assert github.categorize("2026-06-05", ["bug"], True, "low", "2026-06-03") == "post_release"


def test_categorize_active_when_post_release_but_not_version_relevant():
    # post-release + high impact but NOT version-relevant and no regression label → active
    assert github.categorize("2026-06-05", ["bug"], False, "high", "2026-06-03") == "active"


def test_categorize_active_when_pre_release_or_irrelevant():
    assert github.categorize("2026-05-01", ["bug"], False, "low", "2026-06-03") == "active"
    assert github.categorize("2026-06-05", ["bug"], False, "low", "") == "active"


# ── normalize_node ───────────────────────────────────────────────────────────

def _node(**kw):
    base = {
        "number": 42, "title": "Crash on 2026.6.1 startup", "url": "https://x/42",
        "createdAt": "2026-06-05T10:00:00Z", "updatedAt": "2026-06-06T10:00:00Z",
        "author": {"login": "alice"},
        "comments": {"totalCount": 12},
        "reactions": {"totalCount": 22},
        "thumbsUp": {"totalCount": 15},
        "labels": {"nodes": [{"name": "bug"}, {"name": "regression"}]},
        "bodyText": "It crashes on 2026.6.1 every time.",
    }
    base.update(kw)
    return base


def test_normalize_node_full():
    n = github.normalize_node(_node(), release_date="2026-06-03", version="2026.6.1")
    assert n["number"] == 42
    assert n["reactions"] == 15           # thumbsUp, not total
    assert n["total_reactions"] == 22
    assert n["comments"] == 12
    assert n["author"] == "alice"
    assert n["affects_version"] is True
    assert n["impact"] == "high"          # 15 thumbs
    assert n["severity"] == "critical"    # 15👍→high, +regression label bump→critical
    assert n["category"] == "regression"  # regression label
    assert n["is_feature"] is False
    assert n["priority"] is None          # no P-label
    assert n["source"] == "github_api"


def test_normalize_node_priority_and_feature():
    node = _node(labels={"nodes": [{"name": "P2"}, {"name": "enhancement"}]},
                 title="[Feature]: add thing", thumbsUp={"totalCount": 0})
    n = github.normalize_node(node, release_date="2026-06-03", version="2026.6.1")
    assert n["priority"] == "medium"      # P2
    assert n["is_feature"] is True        # enhancement label
    assert n["severity"] == "medium"      # P2, no serious-impact bump


def test_normalize_node_handles_missing_fields():
    n = github.normalize_node({"number": 1, "title": "x", "createdAt": ""}, version="9.9.9")
    assert n["reactions"] == 0
    assert n["severity"] == "low"
    assert n["affects_version"] is False
    assert n["category"] == "active"


# ── ranking ──────────────────────────────────────────────────────────────────

def test_rank_key_orders_critical_relevant_high_impact_first():
    a = {"severity": "low", "affects_version": False, "reactions": 0, "comments": 0}
    b = {"severity": "critical", "affects_version": True, "reactions": 5, "comments": 2}
    c = {"severity": "high", "affects_version": True, "reactions": 30, "comments": 10}
    ordered = sorted([a, b, c], key=github.rank_key)
    assert ordered[0] is b   # critical + version-relevant first
    assert ordered[1] is c   # high + version-relevant
    assert ordered[2] is a


def test_rank_key_version_relevance_lifts_high_above_offversion_critical():
    # a high issue confirmed in THIS version outranks a critical about another version
    off_version_critical = {"severity": "critical", "affects_version": False, "reactions": 9, "comments": 9}
    version_high = {"severity": "high", "affects_version": True, "reactions": 1, "comments": 0}
    ordered = sorted([off_version_critical, version_high], key=github.rank_key)
    assert ordered[0] is version_high
    # ...but a version-relevant LOW still cannot outrank a real critical
    version_low = {"severity": "low", "affects_version": True, "reactions": 0, "comments": 0}
    assert sorted([off_version_critical, version_low], key=github.rank_key)[0] is off_version_critical


# ── releases ─────────────────────────────────────────────────────────────────

def test_norm_release():
    raw = {"tag_name": "v2026.6.1", "name": "2026.6.1", "published_at": "2026-06-03T00:00:00Z",
           "body": "notes", "html_url": "https://x/r", "prerelease": False, "draft": False}
    r = github._norm_release(raw)
    assert r["tag"] == "v2026.6.1"
    assert r["prerelease"] is False
    assert r["body"] == "notes"
    assert github._norm_release(None) is None


def test_latest_prerelease_picks_newest_nondraft():
    releases = [
        {"tag": "v2026.6.2-beta.1", "prerelease": True, "draft": False, "published_at": "2026-06-09"},
        {"tag": "v2026.6.2-beta.2", "prerelease": True, "draft": False, "published_at": "2026-06-11"},
        {"tag": "v2026.6.3-beta.draft", "prerelease": True, "draft": True, "published_at": "2026-06-12"},
        {"tag": "v2026.6.1", "prerelease": False, "draft": False, "published_at": "2026-06-03"},
    ]
    pre = github.latest_prerelease(releases)
    assert pre["tag"] == "v2026.6.2-beta.2"   # newest non-draft pre-release


def test_latest_prerelease_none_when_no_prereleases():
    assert github.latest_prerelease([{"tag": "v1", "prerelease": False, "draft": False}]) is None


# ── API guards (no token) ────────────────────────────────────────────────────

def test_no_token_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    assert github.has_token() is False
    assert github.gh_graphql("query{}") is None
    assert github.gh_rest("/rate_limit") is None
    assert github.search_issues("repo:x is:issue") is None
    assert github.scout_issues("2026-06-03", "2026.6.1") is None
