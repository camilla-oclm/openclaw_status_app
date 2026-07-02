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


def test_norm_release_closing_refs_from_raw_body():
    """closing_refs are extracted from the RAW body: the 'fixes #N' lines live in the
    PR-log tail that curated_changelog() drops, so re-parsing the stored body would miss
    them (the latent bug that left every issue's fixed_in inert)."""
    raw_body = (
        "### Fixes\n- Patch the thing (#42)\n\n"
        "## Pull requests\n- chore: tidy things up, fixes #777\n- feat: add x (#888)\n"
    )
    r = github._norm_release({"tag_name": "v1.2.3", "body": raw_body})
    # The closing ref from the dropped tail is captured...
    assert "777" in r["closing_refs"]
    # ...even though that line is NOT in the stored (curated) body.
    assert "fixes #777" not in r["body"].lower()
    # Bare "(#42)" / "(#888)" are not closing keywords, so they are not refs.
    assert "42" not in r["closing_refs"] and "888" not in r["closing_refs"]


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
    assert n["comments"] == 12
    assert n["author"] == "alice"
    assert n["affects_version"] is True
    assert n["impact"] == "high"          # 15 thumbs
    assert n["severity"] == "critical"    # 15👍→high, +regression label bump→critical
    assert n["category"] == "regression"  # regression label
    assert n["priority"] is None          # no P-label
    # Vestigial fields stay dropped (nothing reads them; they bloated raw-data/ledger).
    for dead in ("total_reactions", "snippet", "comments_data", "platform", "is_feature"):
        assert dead not in n
    assert n["source"] == "github_api"


def test_normalize_node_priority_and_feature():
    node = _node(labels={"nodes": [{"name": "P2"}, {"name": "enhancement"}]},
                 title="[Feature]: add thing", thumbsUp={"totalCount": 0})
    n = github.normalize_node(node, release_date="2026-06-03", version="2026.6.1")
    assert n["priority"] == "medium"      # P2
    assert n["severity"] == "medium"      # P2, no serious-impact bump
    # is_feature is a scout-time FILTER (features never reach storage), not a stored field.
    assert github.is_feature("[Feature]: add thing", ["P2", "enhancement"]) is True


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
    assert r["version"] == "2026.6.1"   # clean version, no leading "v"
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


def test_latest_prerelease_drops_beta_of_shipped_stable():
    # A pre-release of the already-shipped stable (base == stable) is the beta
    # that PRECEDED it, not a future release — it must not be surfaced as a
    # newer "staged fix" signal.
    releases = [
        {"tag": "v2026.6.8-beta.2", "prerelease": True, "draft": False, "published_at": "2026-06-16"},
        {"tag": "v2026.6.8", "prerelease": False, "draft": False, "published_at": "2026-06-16"},
    ]
    assert github.latest_prerelease(releases, stable={"tag": "v2026.6.8"}) is None
    # accepts a bare tag string too
    assert github.latest_prerelease(releases, stable="v2026.6.8") is None


def test_latest_prerelease_keeps_beta_ahead_of_stable():
    # A pre-release whose base version is strictly higher than the stable is a
    # genuine next-release beta and is kept.
    releases = [
        {"tag": "v2026.6.8-beta.2", "prerelease": True, "draft": False, "published_at": "2026-06-16"},
        {"tag": "v2026.6.9-beta.1", "prerelease": True, "draft": False, "published_at": "2026-06-18"},
        {"tag": "v2026.6.8", "prerelease": False, "draft": False, "published_at": "2026-06-16"},
    ]
    pre = github.latest_prerelease(releases, stable={"tag": "v2026.6.8"})
    assert pre["tag"] == "v2026.6.9-beta.1"


def test_release_base_nums_ignores_prerelease_suffix():
    assert github._release_base_nums("v2026.6.8-beta.2") == (2026, 6, 8)
    assert github._release_base_nums("v2026.6.9") == (2026, 6, 9)
    assert github._release_base_nums("v2026.6.9") > github._release_base_nums("v2026.6.8-beta.2")


# ── API guards (no token) ────────────────────────────────────────────────────

def test_no_token_means_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    assert github.has_token() is False
    assert github.gh_graphql("query{}") is None
    assert github.gh_rest("/rate_limit") is None
    assert github.search_issues("repo:x is:issue") is None
    assert github.scout_issues("2026-06-03", "2026.6.1") is None


def _scout_node(num, title, labels, thumbs=0, created="2026-06-25T00:00:00Z", body=""):
    return {"number": num, "title": title, "url": f"https://gh/{num}",
            "bodyText": body, "createdAt": created, "updatedAt": created,
            "labels": {"nodes": [{"name": l} for l in labels]},
            "reactions": {"totalCount": thumbs}, "thumbsUp": {"totalCount": thumbs},
            "comments": {"totalCount": 0}, "author": {"login": "u"}}


def test_scout_fresh_window_ranks_by_recency_not_reactions(monkeypatch):
    # On a fresh release every issue sits at ~0 reactions, so the post-release window must
    # be fetched by RECENCY (else the limit cut drops severe regressions arbitrarily), and
    # confirmed-breakage labels must get their own guaranteed searches.
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    seen_queries = []

    def fake_search(q, limit, timeout=30):
        seen_queries.append(q)
        if "label:regression" in q:
            return [_scout_node(101, "Build breaks on startup", ["regression"], thumbs=0)]
        if 'label:"bug:crash"' in q:
            return []
        if "label:P1" in q:
            return [_scout_node(102, "P1 thing", ["P1"], thumbs=0)]
        # plain post-release recency window + all-open reactions
        return [_scout_node(200, "Some minor cosmetic glitch", ["bug"], thumbs=1)]

    monkeypatch.setattr(github, "search_issues", fake_search)
    out = github.scout_issues("2026-06-24", "2026.6.10")

    # The post-release window is fetched newest-first, not by reactions.
    fresh = [q for q in seen_queries if "created:>=2026-06-24" in q]
    assert fresh and all("sort:created-desc" in q for q in fresh)
    assert not any("created:>=2026-06-24" in q and "sort:reactions" in q for q in seen_queries)
    # Confirmed-breakage labels get dedicated guaranteed searches.
    assert any("label:regression" in q for q in fresh)
    assert any('label:"bug:crash"' in q for q in fresh)
    # The zero-reaction confirmed regression survives and outranks the 1-thumb cosmetic bug.
    nums = [i["number"] for i in out]
    assert 101 in nums
    assert nums.index(101) < nums.index(200)


def test_scout_reports_coverage_and_flags_broad_failure(monkeypatch):
    # M6: a dropped broad post-release sweep (the only un-triaged-breakage catcher) must be
    # reported via `coverage` so the caller can fail closed, even though other searches
    # succeed and the function still returns a (partial) list.
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")

    def is_broad(q):
        return ("created:>=2026-06-24" in q and "sort:created-desc" in q
                and "label:regression" not in q and 'label:"bug:crash"' not in q
                and "label:P1" not in q)

    def fake_search(q, limit, timeout=30):
        if is_broad(q):
            return None                      # the broad recency sweep transiently fails
        return [_scout_node(1, "x", ["bug"])]

    monkeypatch.setattr(github, "search_issues", fake_search)
    cov = {}
    out = github.scout_issues("2026-06-24", "2026.6.10", coverage=cov)
    assert out is not None                   # other queries returned issues — looks "clean"…
    assert cov["broad_ok"] is False          # …but the critical broad sweep was dropped
    assert cov["queries_ok"] < cov["queries_total"]


def test_scout_coverage_all_ok_when_every_query_succeeds(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github, "search_issues",
                        lambda q, limit, timeout=30: [_scout_node(1, "x", ["bug"])])
    cov = {}
    github.scout_issues("2026-06-24", "2026.6.10", coverage=cov)
    assert cov["broad_ok"] is True
    assert cov["queries_ok"] == cov["queries_total"]


def test_scout_parallel_keeps_first_query_dedup_winner(monkeypatch):
    # The searches now run concurrently; aggregation must still walk the ORIGINAL query
    # order, so when two searches return the same issue number, the broad sweep's node
    # (query #1) wins — byte-identical to the old serial loop.
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")

    def is_broad(q):
        # every query carries "-label:enhancement" — broad = no OTHER label filter
        return ("created:>=" in q and "sort:created-desc" in q
                and "label:" not in q.replace("-label:enhancement", ""))

    def fake_search(q, limit, timeout=30):
        if is_broad(q):
            return [_scout_node(300, "broad wins", ["bug"])]
        if "label:regression" in q:
            return [_scout_node(300, "label version", ["regression"])]
        return []

    monkeypatch.setattr(github, "search_issues", fake_search)
    out = github.scout_issues("2026-06-24", "2026.6.10")
    assert [i["number"] for i in out] == [300]
    assert out[0]["title"] == "broad wins"


def test_scout_raising_query_counts_as_dropped_not_fatal(monkeypatch):
    # A query that RAISES (not just returns None) is converted to a dropped query by the
    # parallel runner — coverage reflects the drop and the scout still returns the rest
    # (the serial loop would have crashed the whole collect).
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")

    def fake_search(q, limit, timeout=30):
        if "label:P0" in q:
            raise RuntimeError("boom")
        return [_scout_node(1, "x", ["bug"])]

    monkeypatch.setattr(github, "search_issues", fake_search)
    cov = {}
    out = github.scout_issues("2026-06-24", "2026.6.10", coverage=cov)
    assert out is not None
    assert cov["queries_ok"] == cov["queries_total"] - 1


def test_scout_guarantees_p0_and_impact_searches(monkeypatch):
    # H2: P0 (the most-severe priority) and the serious-impact labels each get a dedicated
    # recency search in the post-release window — not just regression/crash/P1.
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    seen = []
    monkeypatch.setattr(github, "search_issues",
                        lambda q, limit, timeout=30: seen.append(q) or [])
    github.scout_issues("2026-06-24", "2026.6.10")
    fresh = [q for q in seen if "created:>=2026-06-24" in q]
    assert any("label:P0" in q and "sort:created-desc" in q for q in fresh)
    for lbl in ("impact:security", "impact:data", "impact:message-loss",
                "impact:session-state", "impact:auth-provider"):
        assert any(f'label:"{lbl}"' in q for q in fresh), f"no guaranteed search for {lbl}"


def test_scout_no_release_date_includes_p0(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    seen = []
    monkeypatch.setattr(github, "search_issues",
                        lambda q, limit, timeout=30: seen.append(q) or [])
    github.scout_issues("", "2026.6.10")          # no release date
    assert any("label:P0" in q for q in seen)


def test_scout_surfaces_unpopular_p0_outside_broad_cut(monkeypatch):
    """H2 failure scenario: a P0 data-loss issue with 0 reactions, crowded out of the broad
    recency results and lacking a regression/crash label, must still enter the scout via the
    guaranteed P0 search — and be scored 'critical'."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")

    def is_unlabeled(q):                            # broad sweep / all-open: no positive label
        return "label:" not in q.replace("-label:enhancement", "")

    def fake_search(q, limit, timeout=30):
        if "label:P0" in q:                         # the only search that returns the critical
            return [_scout_node(555, "Data loss on sync", ["P0", "impact:data"], thumbs=0)]
        if is_unlabeled(q):                         # broad/all-open: 25 newer, noisier issues
            return [_scout_node(n, f"noise {n}", ["bug"], thumbs=0) for n in range(600, 625)]
        return []

    monkeypatch.setattr(github, "search_issues", fake_search)
    out = github.scout_issues("2026-06-24", "2026.6.10")
    nums = [i["number"] for i in out]
    assert 555 in nums                              # the unpopular P0 is captured…
    crit = next(i for i in out if i["number"] == 555)
    assert crit["severity"] == "critical"           # …and correctly scored as critical


# ── version specificity + importance weight ──────────────────────────────────

def test_version_match_levels():
    v = "2026.6.11"
    assert github.version_match("broken on v2026.6.11 at boot", v) == "exact"
    assert github.version_match("started in 2026.6.9, still broken", v) == "series"
    assert github.version_match("no versions mentioned here", v) == "none"
    # token boundaries: a longer patch number isn't THIS version (but is its series);
    # a different series ("2026.60") isn't relevant at all
    assert github.version_match("seen on 2026.6.110", v) == "series"
    assert github.version_match("this is about 2026.60 only", v) == "none"
    assert github.version_match("regressed in v2026.6.1", v) == "series"
    assert github.version_relevant("the 2026.6 series has this bug", v) is True
    assert github.version_relevant("nothing relevant", v) is False


def test_importance_weight_discriminates_within_a_severity():
    base = {"severity": "high", "category": "post_release", "reactions": 3, "comments": 2}
    exact = github.importance_weight(dict(base, version_match="exact"))
    series = github.importance_weight(dict(base, version_match="series"))
    none = github.importance_weight(dict(base, version_match="none"))
    assert exact > series > none                     # version specificity separates equals
    regr = github.importance_weight(dict(base, version_match="series", category="regression"))
    assert regr > series                             # confirmed regression outranks post-release
    fixed = github.importance_weight(dict(base, version_match="series", fixed_in=["v9.9"]))
    assert fixed < series - 20                       # a staged fix stops driving the verdict
    kept = github.importance_weight(dict(base, version_match="series",
                                         clawsweeper={"decision": "keep_open"}))
    assert kept > series                             # expert keep-open bumps


def test_importance_weight_falls_back_to_affects_version_flag():
    # Older ledger records / fixtures carry only the boolean — treated as a series match.
    w_flag = github.importance_weight({"severity": "high", "affects_version": True})
    w_series = github.importance_weight({"severity": "high", "version_match": "series"})
    assert w_flag == w_series


def test_rank_key_engagement_orders_within_a_tier_never_across():
    # A loud lower-tier issue can NOT cross above a quiet top-tier one…
    quiet_top = {"severity": "critical", "version_match": "exact", "reactions": 0, "comments": 0}
    loud_mid = {"severity": "high", "version_match": "series", "reactions": 80, "comments": 40}
    assert sorted([loud_mid, quiet_top], key=github.rank_key)[0] is quiet_top
    # …but WITHIN the same weight, engagement decides the order.
    a = {"severity": "high", "version_match": "series", "reactions": 9, "comments": 0, "number": 2}
    b = {"severity": "high", "version_match": "series", "reactions": 2, "comments": 0, "number": 1}
    assert sorted([b, a], key=github.rank_key)[0] is a
