"""Tests for openclaw_status.agent — schema validation, conflict detection, diffing."""
import json

import pytest

from openclaw_status import agent, config, lib


def _valid_assessment(**overrides):
    base = {
        "recommendation": "✅",
        "headline": "v1.0 is safe to update",
        "thesis": "This release is solid. " * 10,  # > 100 chars
        "confidence": "high",
        "evidence": {"for_updating": ["x"], "against_updating": [], "neutral": []},
        "known_issues": [],
        "sentiment_summary": "Mostly positive feedback from the community.",
    }
    base.update(overrides)
    return base


# ── validate_assessment ─────────────────────────────────────────────────────

def test_validate_clean_assessment():
    assert agent.validate_assessment(_valid_assessment()) == []


def test_validate_missing_required_field():
    a = _valid_assessment()
    del a["headline"]
    errors = agent.validate_assessment(a)
    assert any("headline" in e for e in errors)


def test_validate_bad_recommendation():
    errors = agent.validate_assessment(_valid_assessment(recommendation="MAYBE"))
    assert any("Invalid recommendation" in e for e in errors)


def test_validate_bad_confidence():
    errors = agent.validate_assessment(_valid_assessment(confidence="certain"))
    assert any("Invalid confidence" in e for e in errors)


def test_validate_thesis_too_short():
    errors = agent.validate_assessment(_valid_assessment(thesis="too short"))
    assert any("too short" in e for e in errors)


def test_validate_thesis_too_long():
    errors = agent.validate_assessment(_valid_assessment(thesis="x" * 5001))
    assert any("too long" in e for e in errors)


def test_validate_detects_xss_in_headline():
    errors = agent.validate_assessment(_valid_assessment(headline="<script>alert(1)</script>"))
    assert any("XSS" in e for e in errors)


def test_validate_detects_onclick_handler():
    errors = agent.validate_assessment(
        _valid_assessment(sentiment_summary='cool onerror=alert(1) stuff here for testing')
    )
    assert any("XSS" in e for e in errors)


def test_validate_detects_xss_in_nested_known_issues():
    a = _valid_assessment(known_issues=[{"number": 1, "title": "<script>alert(1)</script>"}])
    errors = agent.validate_assessment(a)
    assert any("known_issues" in e for e in errors)


def test_validate_nested_no_false_positive_on_equals():
    # The nested check must NOT use the on*= handler pattern — ordinary prose like
    # "one =" would otherwise be flagged and needlessly block deploy.
    a = _valid_assessment(evidence={"for_updating": ["version one = good now"],
                                    "against_updating": [], "neutral": []})
    assert agent.validate_assessment(a) == []


# ── _detect_conflicts ───────────────────────────────────────────────────────

def test_detect_conflict_fixed_in_clawsweeper_but_open():
    issues = [{"number": 5, "clawsweeper": {"fixed_release": "2026.6.2", "decision": "close"},
               "fixed_in": []}]
    conflicts = agent._detect_conflicts(issues, {})
    assert len(conflicts) == 1
    assert conflicts[0]["number"] == 5


def test_detect_conflict_stable_fix_but_keep_open():
    issues = [{"number": 7, "clawsweeper": {"fixed_release": "unknown", "decision": "keep_open"},
               "fixed_in": ["stable"]}]
    conflicts = agent._detect_conflicts(issues, {})
    assert len(conflicts) == 1
    assert conflicts[0]["number"] == 7


def test_detect_no_conflict():
    issues = [{"number": 9, "clawsweeper": None, "fixed_in": []}]
    assert agent._detect_conflicts(issues, {}) == []


# ── _compute_assessment_diff ────────────────────────────────────────────────

def test_diff_recommendation_changed(tmp_path, monkeypatch):
    prev = tmp_path / "assessment.json"
    prev.write_text(json.dumps({"assessment": {
        "recommendation": "✅", "confidence": "high", "headline": "old",
        "known_issues": [{"number": 1}],
    }}))
    monkeypatch.setattr(config, "ASSESSMENT_FILE", prev)
    new = {"recommendation": "⏸️", "confidence": "high", "headline": "old",
           "known_issues": [{"number": 1}]}
    diff = agent._compute_assessment_diff(new)
    assert diff is not None
    assert diff["recommendation_changed"] is True
    assert diff["old_recommendation"] == "✅"
    assert diff["new_recommendation"] == "⏸️"


def test_diff_none_when_identical(tmp_path, monkeypatch):
    payload = {"recommendation": "✅", "confidence": "high", "headline": "same",
               "known_issues": [{"number": 1}]}
    prev = tmp_path / "assessment.json"
    prev.write_text(json.dumps({"assessment": payload}))
    monkeypatch.setattr(config, "ASSESSMENT_FILE", prev)
    assert agent._compute_assessment_diff(dict(payload)) is None


def test_diff_detects_new_and_resolved_issues(tmp_path, monkeypatch):
    prev = tmp_path / "assessment.json"
    prev.write_text(json.dumps({"assessment": {
        "recommendation": "✅", "confidence": "high", "headline": "h",
        "known_issues": [{"number": 1}, {"number": 2}],
    }}))
    monkeypatch.setattr(config, "ASSESSMENT_FILE", prev)
    new = {"recommendation": "✅", "confidence": "high", "headline": "h",
           "known_issues": [{"number": 2}, {"number": 3}]}
    diff = agent._compute_assessment_diff(new)
    assert diff is not None
    assert diff["new_issues"] == [3]
    assert diff["resolved_issues"] == [1]


# ── build_context ───────────────────────────────────────────────────────────

def test_build_context_includes_version_and_issue():
    raw = {
        "target_version": "2026.6.1",
        "sources": {
            "latest_release": {"tag": "v2026.6.1", "published_at": "2026-06-01T00:00:00Z",
                               "body": "### Fixes\n- fixed thing (#42)"},
            "latest_prerelease": None,
            "github_issues": [{"number": 99, "title": "crash on launch", "category": "regression",
                               "severity": "critical", "url": "https://x/99"}],
            "clawsweeper": {},
            "changelog": "",
            "releases_page": "",
            "reddit": [],
        },
    }
    ctx = agent.build_context(raw)
    assert "2026.6.1" in ctx
    assert "#99" in ctx
    assert "crash on launch" in ctx


def _raw_with_n_issues(n):
    return {
        "target_version": "2026.6.1",
        "sources": {
            "latest_release": {"tag": "v2026.6.1", "published_at": "2026-06-01T00:00:00Z"},
            "latest_prerelease": None,
            "github_issues": [
                {"number": i, "title": f"issue number {i}", "category": "active",
                 "severity": "high", "url": f"https://x/{i}"}
                for i in range(1, n + 1)
            ],
            "clawsweeper": {},
        },
    }


def test_build_context_caps_issues_to_top_n(monkeypatch):
    # Feed more issues than the cap: only the top-N (by their pre-ranked order)
    # reach the prompt, but the header still reports the true total.
    monkeypatch.setattr(config, "MAX_ISSUES_IN_CONTEXT", 3)
    ctx = agent.build_context(_raw_with_n_issues(5))
    assert "5 total" in ctx
    assert "Showing the top 3" in ctx
    for n in (1, 2, 3):
        assert f"### #{n} " in ctx
    for n in (4, 5):
        assert f"### #{n} " not in ctx


def test_build_context_no_truncation_note_when_under_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_ISSUES_IN_CONTEXT", 30)
    ctx = agent.build_context(_raw_with_n_issues(4))
    assert "Showing the top" not in ctx
    for n in (1, 2, 3, 4):
        assert f"### #{n} " in ctx


# ── model config ────────────────────────────────────────────────────────────

def test_fallback_models_have_valid_slug_shape():
    # An OpenRouter slug is exactly provider/model (one slash). The old config
    # shipped a double-prefixed "openrouter/deepseek/deepseek-v4-flash" that 400s.
    assert config.FALLBACK_MODELS, "expected at least one fallback"
    for fb in config.FALLBACK_MODELS:
        assert set(fb) >= {"model", "reasoning"}
        assert fb["model"].count("/") == 1, f"bad slug: {fb['model']}"


def test_fallbacks_do_not_repeat_primary():
    # No point falling back to the model that just failed.
    assert config.PRIMARY_MODEL not in {fb["model"] for fb in config.FALLBACK_MODELS}


def test_assessment_max_tokens_exceeds_default():
    # The whole point is to clear the 4k openrouter_call default that truncated JSON.
    assert config.ASSESSMENT_MAX_TOKENS > 4000


# ── append_history ──────────────────────────────────────────────────────────

def test_append_history_writes_entry(tmp_path, monkeypatch):
    hist = tmp_path / "history.json"
    monkeypatch.setattr(config, "HISTORY_FILE", hist)
    agent.append_history("1.0", _valid_assessment(headline="first"), {"cost_usd": 0.001})
    data = json.loads(hist.read_text())
    assert len(data) == 1
    assert data[0]["version"] == "1.0"
    assert data[0]["recommendation"] == "✅"


def test_append_history_dedupes_same_version(tmp_path, monkeypatch):
    hist = tmp_path / "history.json"
    monkeypatch.setattr(config, "HISTORY_FILE", hist)
    agent.append_history("1.0", _valid_assessment(), {"cost_usd": 0.001})
    agent.append_history("1.0", _valid_assessment(recommendation="⏸️"), {"cost_usd": 0.001})
    data = json.loads(hist.read_text())
    assert len(data) == 1
    assert data[0]["recommendation"] == "⏸️"


# ── budget gate ─────────────────────────────────────────────────────────────

def test_budget_gate_aborts_without_spending(tmp_path, monkeypatch):
    # Today's spend already exceeds the daily limit → pipeline must refuse to start
    # and make NO LLM call.
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps([
        {"timestamp": lib.now_iso(), "cost_usd": 99.0, "success": True},
    ]))
    monkeypatch.setattr(config, "USAGE_LOG_FILE", usage)

    def _boom(*a, **k):
        raise AssertionError("openrouter_call must not run when the budget gate trips")
    monkeypatch.setattr(agent, "openrouter_call", _boom)

    raw = {"target_version": "1.0", "sources": {
        "latest_release": {}, "latest_prerelease": None, "github_issues": [],
        "clawsweeper": {}, "release_history": [],
    }}
    result = agent.run_assessment_pipeline(raw=raw)
    assert result["success"] is False
    assert "budget exceeded" in result["error"]


def test_budget_gate_emits_no_real_webhook_post(tmp_path, monkeypatch):
    # The budget gate calls notify(); the suite must never POST to a real webhook,
    # even when the dev's .env populated ALERT_WEBHOOK_URL (the autouse
    # _no_real_webhook fixture nulls it). Spy on the network to prove no POST.
    usage = tmp_path / "usage.json"
    usage.write_text(json.dumps([
        {"timestamp": lib.now_iso(), "cost_usd": 99.0, "success": True},
    ]))
    monkeypatch.setattr(config, "USAGE_LOG_FILE", usage)
    monkeypatch.setattr(agent, "openrouter_call",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM")))
    posted = []
    monkeypatch.setattr(lib.urllib.request, "urlopen",
                        lambda *a, **k: posted.append(a))

    raw = {"target_version": "1.0", "sources": {
        "latest_release": {}, "latest_prerelease": None, "github_issues": [],
        "clawsweeper": {}, "release_history": [],
    }}
    result = agent.run_assessment_pipeline(raw=raw)
    assert result["success"] is False
    assert posted == []  # no webhook POST happened
