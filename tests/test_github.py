"""Tests for openclaw_status.github — scouting logic (pure functions)."""
import pytest

from openclaw_status import github, config


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


# ── impact_level ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("thumbs,comments,expected", [
    (10, 0, "high"), (0, 25, "high"), (15, 3, "high"),
    (3, 0, "medium"), (0, 8, "medium"), (1, 6, "medium"),
    (0, 7, "low"), (2, 2, "low"), (0, 0, "low"),
])
def test_impact_level(thumbs, comments, expected):
    assert github.impact_level(thumbs, comments) == expected


# ── derive_severity ──────────────────────────────────────────────────────────

def test_derive_severity_diamond_wins():
    assert github.derive_severity(["issue-rating: 🦞 diamond lobster"], 0, 0) == "critical"


def test_derive_severity_from_labels():
    assert github.derive_severity(["regression"], 0, 0) == "high"
    assert github.derive_severity(["priority: high"], 0, 0) == "high"
    assert github.derive_severity(["crash"], 0, 0) == "high"


def test_derive_severity_from_reactions():
    assert github.derive_severity(["bug"], 12, 0) == "high"
    assert github.derive_severity(["bug"], 4, 0) == "medium"
    assert github.derive_severity(["bug"], 0, 0) == "low"


# ── categorize ───────────────────────────────────────────────────────────────

def test_categorize_diamond():
    assert github.categorize("2026-01-01", ["issue-rating: 🦞 diamond lobster"], False, "low", "2026-06-03") == "diamond_lobster"


def test_categorize_regression_when_post_release_and_relevant():
    assert github.categorize("2026-06-05", ["bug"], True, "low", "2026-06-03") == "regression"
    assert github.categorize("2026-06-05", ["bug"], False, "high", "2026-06-03") == "regression"
    assert github.categorize("2026-06-05", ["regression"], False, "low", "2026-06-03") == "regression"


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
    assert n["severity"] == "high"        # regression label
    assert n["category"] == "regression"  # post-release + affects version
    assert n["source"] == "github_api"


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
    assert ordered[0] is b   # critical first
    assert ordered[1] is c   # high before low
    assert ordered[2] is a


# ── API guards (no token) ────────────────────────────────────────────────────

def test_no_token_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    assert github.has_token() is False
    assert github.gh_graphql("query{}") is None
    assert github.search_issues("repo:x is:issue") is None
    assert github.scout_issues("2026-06-03", "2026.6.1") is None
