"""Tests for openclaw_status.ledger — the per-version accumulating issue ledger."""
import json

import pytest

from openclaw_status import config, ledger


def _issue(number, **kw):
    base = {"number": number, "title": f"issue {number}", "severity": "medium",
            "category": "active", "reactions": 1, "comments": 0, "affects_version": True,
            "impact": "low", "fixed_in": []}
    base.update(kw)
    return base


@pytest.fixture
def led(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ISSUE_LEDGER_FILE", tmp_path / "issue-ledger.json")
    monkeypatch.setattr(config, "LEDGER_MAX_ISSUES_PER_VERSION", 60)
    monkeypatch.setattr(config, "LEDGER_KEEP_VERSIONS", 12)
    return tmp_path


def test_merge_accumulates_across_runs(led):
    a = ledger.merge_version_issues("1.0", [_issue(1), _issue(2)])
    assert {i["number"] for i in a} == {1, 2}
    # A later run surfaces a different subset — old issues must NOT disappear.
    b = ledger.merge_version_issues("1.0", [_issue(2), _issue(3)])
    assert {i["number"] for i in b} == {1, 2, 3}


def test_reactions_monotonic_but_severity_tracks_current_labels(led):
    # Reaction counts only climb (a partial re-scout can't walk a community signal back)…
    ledger.merge_version_issues("1.0", [_issue(1, reactions=10, labels=["P0"])])
    out = ledger.merge_version_issues("1.0", [_issue(1, reactions=2, labels=["P2"])])
    one = next(i for i in out if i["number"] == 1)
    assert one["reactions"] == 10          # reactions only climb
    # …but severity is re-derived from current labels each run, so a maintainer downgrade
    # (or a scoring-formula change) self-corrects instead of freezing at the worst.
    assert one["severity"] == "medium"     # P2 now, not the frozen P0/critical


def test_severity_label_past_index_six_is_not_truncated(led):
    # D04: _lean stored labels[:6] and _rederive_stored recomputes severity from the STORED
    # set — so a P0/impact:* label sitting past the 6th position was silently dropped and the
    # issue downgraded (critical → low). Heavily-triaged issues routinely carry 5-8 labels.
    labels = ["area:gateway", "channel:whatsapp", "needs-triage",
              "good-first-issue", "help-wanted", "documentation", "P0"]   # P0 at index 6
    out = ledger.merge_version_issues("1.0", [_issue(1, labels=labels)])
    one = next(i for i in out if i["number"] == 1)
    assert one["severity"] == "critical"    # full set preserved → P0 → critical (was "low")
    assert "P0" in one["labels"]            # the severity-bearing label survived storage


def test_stored_issue_severity_re_derived_even_when_not_re_scouted(led):
    # The bug a pre-launch review caught: an issue accumulated as critical under an old
    # formula and was then NOT in a later run's top-N scout. Its severity must still
    # self-correct from its stored labels, not stay frozen because it wasn't re-scouted.
    ledger.merge_version_issues("1.0", [_issue(1, labels=["P1"]), _issue(2, labels=["P1"])])
    raw = json.loads(config.ISSUE_LEDGER_FILE.read_text())
    raw["1.0"]["issues"]["1"]["severity"] = "critical"            # stale value from old formula
    config.ISSUE_LEDGER_FILE.write_text(json.dumps(raw))
    out = ledger.merge_version_issues("1.0", [_issue(2, labels=["P1"])])  # only #2 re-scouted
    one = next(i for i in out if i["number"] == 1)
    assert one["severity"] == "high"       # re-derived from P1, not the frozen 'critical'


def test_only_version_relevant_issues_accumulate(led):
    out = ledger.merge_version_issues("1.0", [
        _issue(1, affects_version=True),                                            # kept (affects)
        _issue(2, affects_version=False, category="regression", labels=["regression"]),  # kept (regression)
        _issue(3, affects_version=False, category="diamond_lobster"),               # excluded (not ours)
    ])
    assert {i["number"] for i in out} == {1, 2}


def test_category_tracks_current_labels(led):
    # Category is re-derived from labels each run (not sticky): drop the regression label
    # and it re-derives to active (kept here because it still affects the version).
    ledger.merge_version_issues("1.0", [_issue(1, category="regression", labels=["regression"])])
    out = ledger.merge_version_issues("1.0", [_issue(1, category="active", labels=[])])
    assert next(i for i in out if i["number"] == 1)["category"] == "active"


def test_rescout_as_no_longer_relevant_drops_entry(led):
    # First run: #1 affects this release → accumulated.
    a = ledger.merge_version_issues("1.0", [_issue(1, affects_version=True)])
    assert {i["number"] for i in a} == {1}
    # Re-scouted but now neither version-relevant nor a regression (e.g. a tightened
    # version match): it must be dropped, not frozen in at its old worst.
    b = ledger.merge_version_issues("1.0", [_issue(1, affects_version=False, category="active", labels=[])])
    assert {i["number"] for i in b} == set()


def test_affects_version_re_derived_kept_via_regression_label(led):
    ledger.merge_version_issues("1.0", [_issue(1, affects_version=True, category="regression", labels=["regression"])])
    # Re-scouted: no longer matches the version but still a labelled regression → kept,
    # yet affects_version reflects the current scout (False), not a frozen True.
    out = ledger.merge_version_issues("1.0", [_issue(1, affects_version=False, category="regression", labels=["regression"])])
    one = next(i for i in out if i["number"] == 1)
    assert one["affects_version"] is False
    assert one["category"] == "regression"


def test_fixed_status_merges_and_persists(led):
    ledger.merge_version_issues("1.0", [_issue(1)])
    out = ledger.merge_version_issues("1.0", [_issue(1, fixed_in=["prerelease"])])
    assert next(i for i in out if i["number"] == 1)["fixed_in"] == ["prerelease"]


def test_per_version_cap(led, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_MAX_ISSUES_PER_VERSION", 3)
    out = ledger.merge_version_issues("1.0", [_issue(n, reactions=n) for n in range(1, 8)])
    assert len(out) == 3
    assert {i["number"] for i in out} == {7, 6, 5}   # highest-impact survive the cap


def test_versions_are_separate_and_old_ones_pruned(led, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_KEEP_VERSIONS", 2)
    for i, v in enumerate(("1.0", "1.1", "1.2"), start=1):
        ledger.merge_version_issues(v, [_issue(1)], now=f"2026-01-0{i}T00:00:00+00:00")
    stored = json.loads(config.ISSUE_LEDGER_FILE.read_text())
    assert set(stored) == {"1.1", "1.2"}             # oldest version pruned


def test_display_known_issues_shapes_and_labels(led):
    acc = ledger.merge_version_issues("1.0", [
        _issue(1, fixed_in=["prerelease"], clawsweeper={"decision": "keep_open"}),
        _issue(2),
    ])
    by = {d["number"]: d for d in ledger.display_known_issues(acc)}
    assert by[1]["fixed_in"] == "next pre-release"
    assert by[1]["clawsweeper_decision"] == "keep_open"
    assert by[2]["fixed_in"] is None


def test_empty_version_returns_input_unchanged(led):
    scouted = [_issue(1)]
    assert ledger.merge_version_issues("", scouted) is scouted
    assert not config.ISSUE_LEDGER_FILE.exists()


def test_is_version_relevant_predicate():
    assert ledger.is_version_relevant({"affects_version": True, "category": "active"})
    assert ledger.is_version_relevant({"affects_version": False, "category": "regression"})
    # version-agnostic major → not relevant to this release (kept only as context)
    assert not ledger.is_version_relevant({"affects_version": False, "category": "diamond_lobster"})


def test_is_new_flags_issues_first_seen_after_prior_run(led):
    # First run has no prior run, so nothing is "new" (avoids flagging the whole baseline).
    a = ledger.merge_version_issues("1.0", [_issue(1)], now="2026-01-01T00:00:00+00:00")
    assert all(not i["is_new"] for i in a)
    # Second run adds #2 — only #2 is new since the previous run.
    b = {i["number"]: i for i in
         ledger.merge_version_issues("1.0", [_issue(1), _issue(2)], now="2026-01-02T00:00:00+00:00")}
    assert b[2]["is_new"] is True
    assert b[1]["is_new"] is False


# ── importance weight + version specificity in the ledger ────────────────────

def test_weight_and_version_match_persist_and_rederive(led):
    from openclaw_status import github
    out = ledger.merge_version_issues(
        "2026.6.11",
        [_issue(1, version_match="exact", labels=["regression"])],
        release_date="2026-06-30")
    one = next(i for i in out if i["number"] == 1)
    assert one["version_match"] == "exact"
    assert one["weight"] == github.importance_weight(one)
    disp = ledger.display_known_issues(out)
    assert disp[0]["weight"] == one["weight"]
    assert disp[0]["version_match"] == "exact"


def test_priority_provenance_persists_and_keeps_bot_p0_discounted(led):
    # A bot-triaged P0 stays "high" (not critical) across runs: the stored provenance
    # feeds _rederive_stored's severity recomputation even when the issue isn't
    # re-scouted; display exposes the field for the page/API.
    out = ledger.merge_version_issues(
        "2026.6.11",
        [_issue(1, labels=["P0"], priority_provenance="bot")],
        release_date="2026-06-30")
    one = next(i for i in out if i["number"] == 1)
    assert one["priority_provenance"] == "bot"
    assert one["severity"] == "high"          # notched — not critical
    disp = ledger.display_known_issues(out)
    assert disp[0]["priority_provenance"] == "bot"
    # …and a later run that does NOT re-scout it still re-derives the same discount.
    out2 = ledger.merge_version_issues("2026.6.11", [], release_date="2026-06-30")
    one2 = next(i for i in out2 if i["number"] == 1)
    assert one2["severity"] == "high"
    # A pre-migration record (no provenance stored) trusts the label — fail-closed.
    out3 = ledger.merge_version_issues(
        "2026.6.11", [_issue(2, labels=["P0"])], release_date="2026-06-30")
    two = next(i for i in out3 if i["number"] == 2)
    assert two["severity"] == "critical"


def test_ledger_migrates_missing_version_match(led):
    # A record stored before the field existed self-heals from its stored text.
    ledger.merge_version_issues(
        "2026.6.11", [_issue(1, body="crashes on v2026.6.11 at boot")],
        release_date="2026-06-30")
    raw = json.loads(config.ISSUE_LEDGER_FILE.read_text())
    raw["2026.6.11"]["issues"]["1"].pop("version_match", None)
    raw["2026.6.11"]["issues"]["1"].pop("weight", None)
    config.ISSUE_LEDGER_FILE.write_text(json.dumps(raw))
    out = ledger.merge_version_issues("2026.6.11", [], release_date="2026-06-30")
    one = next(i for i in out if i["number"] == 1)
    assert one["version_match"] == "exact"          # re-derived from stored title+body
    assert isinstance(one["weight"], int) and one["weight"] > 0


def test_ledger_orders_by_importance_weight(led):
    out = ledger.merge_version_issues("2026.6.11", [
        _issue(1, version_match="series"),
        _issue(2, version_match="exact"),
        _issue(3, version_match="exact", labels=["regression"]),
    ], release_date="2026-06-30")
    assert [i["number"] for i in out] == [3, 2, 1]   # regression+exact > exact > series


def test_closed_issue_handling(led):
    from openclaw_status import github
    # not-planned / duplicate closures are dropped at admission…
    out = ledger.merge_version_issues(
        "2026.7.1",
        [_issue(1, labels=["P0"], state="closed", state_reason="duplicate"),
         _issue(2, labels=["P0"], state="closed", state_reason="completed"),
         _issue(3, labels=["P0"])],
        release_date="2026-07-13")
    nums = sorted(i["number"] for i in out)
    assert nums == [2, 3]
    # …completed-closed stays, discounted, state persisted + displayed
    two = next(i for i in out if i["number"] == 2)
    three = next(i for i in out if i["number"] == 3)
    assert two["state"] == "closed" and two["state_reason"] == "completed"
    assert two["weight"] == three["weight"] - 25
    disp = ledger.display_known_issues(out)
    assert {d["number"]: d["state"] for d in disp} == {2: "closed", 3: "open"}
    # a stored record later re-learned as a noise closure is dropped on re-derive
    # (e.g. the refresh returned it this run with the new reason)
    out = ledger.merge_version_issues(
        "2026.7.1", [_issue(3, labels=["P0"], state="closed", state_reason="not_planned")],
        release_date="2026-07-13")
    assert sorted(i["number"] for i in out) == [2]
    # a reopened issue heals back to open via scout-wins
    out = ledger.merge_version_issues(
        "2026.7.1", [_issue(2, labels=["P0"], state="open", state_reason=None)],
        release_date="2026-07-13")
    two = next(i for i in out if i["number"] == 2)
    assert two["state"] == "open" and two["weight"] == three["weight"]
