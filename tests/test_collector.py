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
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: [])  # no live API
    status = SourceStatus()
    issues = collector.fetch_github_issues(status=status)
    assert issues == []
    assert "github_issues" in status.results
    assert status.results["github_issues"]["status"] == "empty"


def test_fetch_github_issues_no_status_does_not_crash(monkeypatch):
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: [])
    # status defaults to None — must not raise
    assert collector.fetch_github_issues() == []


def test_fetch_github_issues_marks_fixed_from_release_body(monkeypatch):
    issues = [{"number": 42, "affects_version": True}, {"number": 99, "affects_version": False}]
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: issues)
    out = collector.fetch_github_issues(release_body="release notes — fixes #42", prerelease_body="")
    fixed = {i["number"]: i["fixed_in"] for i in out}
    assert fixed[42] == ["stable"]
    assert fixed[99] == []


def test_fetch_github_issues_uses_pre_extracted_closing_refs(monkeypatch):
    """Pre-extracted closing refs (from the raw body, via _norm_release) take precedence
    over the curated release_body — which no longer carries the 'fixes #N' tail. This is
    the path that fixes the latent inert-fixed_in bug."""
    issues = [{"number": 777, "affects_version": True}, {"number": 42, "affects_version": True}]
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: issues)
    out = collector.fetch_github_issues(
        release_body="### Fixes\n- tidy things (#42)",   # curated body — no 'fixes #777'
        stable_closing_refs=["777"],                     # recovered from the dropped tail
    )
    fixed = {i["number"]: i["fixed_in"] for i in out}
    assert fixed[777] == ["stable"]   # picked up despite not being in release_body
    assert fixed[42] == []            # bare "(#42)" is not a closing keyword
