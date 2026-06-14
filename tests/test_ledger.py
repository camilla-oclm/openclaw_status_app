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


def test_reactions_and_severity_are_monotonic(led):
    ledger.merge_version_issues("1.0", [_issue(1, reactions=10, severity="high")])
    out = ledger.merge_version_issues("1.0", [_issue(1, reactions=2, severity="low")])
    one = next(i for i in out if i["number"] == 1)
    assert one["reactions"] == 10          # reactions only climb
    assert one["severity"] == "high"       # severity keeps its worst


def test_only_version_relevant_issues_accumulate(led):
    out = ledger.merge_version_issues("1.0", [
        _issue(1, affects_version=True, category="active"),
        _issue(2, affects_version=False, category="regression"),       # kept (regression)
        _issue(3, affects_version=False, category="diamond_lobster"),  # excluded (not ours)
    ])
    assert {i["number"] for i in out} == {1, 2}


def test_regression_category_is_sticky(led):
    ledger.merge_version_issues("1.0", [_issue(1, category="regression")])
    out = ledger.merge_version_issues("1.0", [_issue(1, category="active")])
    assert next(i for i in out if i["number"] == 1)["category"] == "regression"


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


def test_is_new_flags_issues_first_seen_after_prior_run(led):
    # First run has no prior run, so nothing is "new" (avoids flagging the whole baseline).
    a = ledger.merge_version_issues("1.0", [_issue(1)], now="2026-01-01T00:00:00+00:00")
    assert all(not i["is_new"] for i in a)
    # Second run adds #2 — only #2 is new since the previous run.
    b = {i["number"]: i for i in
         ledger.merge_version_issues("1.0", [_issue(1), _issue(2)], now="2026-01-02T00:00:00+00:00")}
    assert b[2]["is_new"] is True
    assert b[1]["is_new"] is False
