"""Tests for openclaw_status.collector — SourceStatus and status threading."""
import json

import pytest

from openclaw_status import collector, config
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


def test_fetch_github_issues_marks_degraded_on_partial_scout(monkeypatch):
    """M6 regression: when the broad post-release recency sweep failed but other searches
    returned issues, the source is recorded 'degraded' (not 'ok') so the assessment's
    thin-evidence floor caps confidence — an incomplete scout can't read as genuinely clean."""
    def fake_scout(release_date, version, coverage=None, **k):
        if coverage is not None:
            coverage.update(queries_total=5, queries_ok=4, broad_ok=False)
        return [{"number": 1, "affects_version": True}]
    monkeypatch.setattr(collector.github, "scout_issues", fake_scout)
    status = SourceStatus()
    collector.fetch_github_issues(status=status)
    assert status.results["github_issues"]["status"] == "degraded"


def test_fetch_github_issues_ok_on_full_scout(monkeypatch):
    def fake_scout(release_date, version, coverage=None, **k):
        if coverage is not None:
            coverage.update(queries_total=5, queries_ok=5, broad_ok=True)
        return [{"number": 1, "affects_version": True}]
    monkeypatch.setattr(collector.github, "scout_issues", fake_scout)
    status = SourceStatus()
    collector.fetch_github_issues(status=status)
    assert status.results["github_issues"]["status"] == "ok"


def test_fetch_github_issues_marks_failed_on_wholly_failed_scout(monkeypatch):
    """D01: scout_issues returns None when EVERY search failed. That must be recorded
    'failed', NOT the clean 'empty' — otherwise a dead scout reads as a clean release and
    the assessment can publish a false 'no known issues → update now'."""
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: None)
    status = SourceStatus()
    issues = collector.fetch_github_issues(status=status)
    assert issues == []
    assert status.results["github_issues"]["status"] == "failed"


def test_fetch_github_issues_marks_failed_when_all_queries_dropped(monkeypatch):
    """D01: even when scout returns [] (not None), 0/N searches succeeding means every query
    failed — still 'failed', never a clean 'empty'."""
    def fake_scout(release_date, version, coverage=None, **k):
        if coverage is not None:
            coverage.update(queries_total=5, queries_ok=0, broad_ok=False)
        return []
    monkeypatch.setattr(collector.github, "scout_issues", fake_scout)
    status = SourceStatus()
    collector.fetch_github_issues(status=status)
    assert status.results["github_issues"]["status"] == "failed"


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


# ── collect() end-to-end + the untested fetchers (D16) ───────────────────────

def _wire_collect(monkeypatch, tmp_path, *, npm, release):
    """Monkeypatch every external call collect() makes + ledger paths → tmp (hermetic)."""
    monkeypatch.setattr(config, "ISSUE_LEDGER_FILE", tmp_path / "issue-ledger.json")
    monkeypatch.setattr(config, "LEDGER_MAX_ISSUES_PER_VERSION", 60)
    monkeypatch.setattr(config, "LEDGER_KEEP_VERSIONS", 12)
    monkeypatch.setattr(collector, "fetch_npm_version", lambda: npm)
    monkeypatch.setattr(collector.github, "list_releases", lambda n=30: [release] if release else [])
    monkeypatch.setattr(collector.github, "latest_release", lambda: release)
    monkeypatch.setattr(collector.github, "latest_prerelease", lambda *a, **k: None)
    monkeypatch.setattr(collector.github, "scout_issues", lambda *a, **k: [])
    monkeypatch.setattr(collector, "fetch_clawsweeper_state",
                        lambda: {"work_candidates": [], "recently_closed": [], "item_records": {}})
    monkeypatch.setattr(collector, "fetch_clawsweeper_records", lambda nums, status=None: {})


def test_collect_proceeds_when_release_ok(tmp_path, monkeypatch):
    """D16: with a usable release (+ npm) the completeness gate must PROCEED, wiring the
    sources + resolved version into raw-data (previously collect() was never driven e2e)."""
    release = {"tag": "v2026.6.11", "version": "2026.6.11", "body": "notes",
               "published_at": "2026-06-01T00:00:00Z", "prerelease": False}
    _wire_collect(monkeypatch, tmp_path, npm={"version": "2026.6.11"}, release=release)
    raw = collector.collect(output_path=tmp_path / "raw-data.json")
    assert not raw.get("pipeline_aborted")
    assert raw["target_version"] == "2026.6.11"
    assert raw["sources"]["latest_release"]["tag"] == "v2026.6.11"
    assert raw["source_status"]["github_release"]["status"] == "ok"


def test_collect_aborts_when_npm_and_release_both_fail(tmp_path, monkeypatch):
    """D16: the completeness gate — if BOTH npm and the GitHub release fail, collect() must
    abort (pipeline_aborted) rather than drive the LLM over empty data."""
    _wire_collect(monkeypatch, tmp_path, npm=None, release=None)
    raw = collector.collect(output_path=tmp_path / "raw-data.json")
    assert raw.get("pipeline_aborted") is True


class _NpmResp:
    def __init__(self, payload): self._p = payload
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self._p).encode()


def test_fetch_npm_version_parses_version(monkeypatch):
    monkeypatch.setattr(collector.urllib.request, "urlopen",
                        lambda *a, **k: _NpmResp({"version": "9.9.9", "name": "openclaw"}))
    assert collector.fetch_npm_version() == {"version": "9.9.9", "name": "openclaw"}


def test_fetch_npm_version_returns_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(collector.urllib.request, "urlopen", boom)
    assert collector.fetch_npm_version() is None


def test_fetch_clawsweeper_records_parses_metadata(monkeypatch):
    md = "# Record\nnumber: 42\ndecision: keep-open\nfixed_release: v2026.6.12\n"
    monkeypatch.setattr(collector.github, "fetch_raw", lambda *a, **k: md)
    recs = collector.fetch_clawsweeper_records([42])
    assert recs[42]["decision"] == "keep-open"
    assert recs[42]["fixed_release"] == "v2026.6.12"


def test_fetch_clawsweeper_state_parses_readme_tables(monkeypatch):
    readme = (
        "### Work Candidates\n"
        "| Repository | Issue | Title | Priority | Reviewed |\n"
        "|---|---|---|---|---|\n"
        "| openclaw/openclaw | #10 | Gateway crash | high | 2026-06-01 |\n"
        "\n### Recently Closed\n"
        "| Repository | Issue | Title | Reason | Closed |\n"
        "|---|---|---|---|---|\n"
        "| openclaw/openclaw | #11 | Old bug | fixed | 2026-05-01 |\n"
    )
    monkeypatch.setattr(collector.github, "fetch_raw", lambda *a, **k: readme)
    st = collector.fetch_clawsweeper_state()
    assert st["work_candidates"][0]["number"] == 10
    assert st["work_candidates"][0]["priority"] == "high"
    assert st["recently_closed"][0]["number"] == 11
