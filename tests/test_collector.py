"""Tests for openclaw_status.collector — SourceStatus and status threading."""
import pytest

from openclaw_status import collector
from openclaw_status.collector import SourceStatus


# ── SourceStatus ────────────────────────────────────────────────────────────

def test_source_status_record_and_results():
    s = SourceStatus()
    s.record("npm", "ok", "2026.6.1", 0.5)
    assert s.results["npm"]["status"] == "ok"
    assert s.results["npm"]["detail"] == "2026.6.1"


def test_source_status_has_failures():
    s = SourceStatus()
    s.record("npm", "ok")
    s.record("github", "failed", "timeout")
    assert s.has_failures() is True


def test_source_status_no_failures():
    s = SourceStatus()
    s.record("npm", "ok")
    s.record("reddit", "empty")
    assert s.has_failures() is False


def test_source_status_summary_has_icons():
    s = SourceStatus()
    s.record("npm", "ok")
    s.record("github", "failed")
    summary = s.summary()
    assert "✅" in summary
    assert "❌" in summary


# ── Regression: status threading (the global/local shadowing bug) ───────────

def test_fetch_github_issues_records_on_passed_status(monkeypatch):
    """fetch_github_issues must record into the SourceStatus it's given.

    Previously it recorded into a dead module-level global while collect()
    saved a shadowing local, so 'github_issues' never appeared in the output.
    """
    # force the (hermetic) Composio fallback path — no live API
    monkeypatch.setattr(collector.github, "has_token", lambda: False)
    monkeypatch.setattr(collector, "_gh_graphql", lambda *a, **k: [])
    status = SourceStatus()
    issues = collector.fetch_github_issues(status=status)
    assert issues == []
    assert "github_issues" in status.results
    assert status.results["github_issues"]["status"] == "empty"


def test_fetch_github_issues_no_status_does_not_crash(monkeypatch):
    monkeypatch.setattr(collector.github, "has_token", lambda: False)
    monkeypatch.setattr(collector, "_gh_graphql", lambda *a, **k: [])
    # status defaults to None — must not raise
    assert collector.fetch_github_issues() == []
