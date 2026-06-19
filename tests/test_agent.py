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


def test_build_context_includes_ongoing_majors_as_context():
    raw = _raw_with_n_issues(2)
    raw["sources"]["ongoing_majors"] = [
        {"number": 555, "severity": "critical", "title": "old major bug",
         "reactions": 99, "category": "diamond_lobster"},
    ]
    ctx = agent.build_context(raw)
    assert "Ongoing Majors" in ctx
    assert "#555" in ctx
    assert "NOT specific to this release" in ctx


def test_build_context_continuity_is_symmetric_not_a_lock():
    raw = _raw_with_n_issues(2)
    assert "Continuity" not in agent.build_context(raw)  # no prior verdict → no anchor
    ctx = agent.build_context(raw, {"recommendation": "⏸️", "confidence": "high",
                                     "assessed_at": "2026-06-10T00:00:00+00:00"})
    assert "Continuity" in ctx
    assert "⏸️" in ctx  # prior verdict surfaced for continuity
    # Holds against NOISE only...
    assert "NOISE" in ctx
    # ...but the verdict must still track the current broken-state and can move DOWN
    # when severity worsens — not a one-directional "keep". Guards the symmetric-anchor
    # fix against a regression back to a sticky verdict.
    assert "BROKEN" in ctx
    assert "DOWNGRADE" in ctx
    assert "KEEP the previous verdict" not in ctx


def test_build_context_pre_release_framed_as_staged_fix():
    # With no pre-release, the context just says there is none (no retired 🔄 copy).
    ctx = agent.build_context(_raw_with_n_issues(2))  # latest_prerelease is None
    assert "no pre-release ahead of the current stable" in ctx
    assert "🔄" not in ctx
    # When an ahead-of-stable pre-release exists, it's framed as a STAGED fix that
    # does not lift the verdict (the fix isn't shipped yet).
    raw = _raw_with_n_issues(2)
    raw["sources"]["latest_prerelease"] = {"tag": "v2026.6.9-beta.1",
                                           "published_at": "2026-06-18T00:00:00Z"}
    ctx2 = agent.build_context(raw)
    assert "v2026.6.9-beta.1" in ctx2
    assert "staged fix" in ctx2 and "🔄" not in ctx2


def test_normalize_recommendation_collapses_retired_wait():
    # 🔄 "wait for next release" is retired (3-verdict rubric ✅/⚠️/⏸️). Any stray 🔄
    # — model output or old history — collapses to ⏸️.
    assert agent._norm_rec("🔄") == "⏸️"
    for v in ("✅", "⚠️", "⏸️"):
        assert agent._norm_rec(v) == v
    a = {"recommendation": "🔄"}
    assert agent._normalize_recommendation(a) is True
    assert a["recommendation"] == "⏸️"
    a = {"recommendation": "⚠️"}
    assert agent._normalize_recommendation(a) is False
    assert a["recommendation"] == "⚠️"


def test_validate_assessment_rejects_retired_wait_verdict():
    # 🔄 is no longer a valid recommendation.
    base = {"recommendation": "🔄", "headline": "h", "thesis": "t", "confidence": "medium"}
    assert any("recommendation" in e.lower() for e in agent.validate_assessment(base))
    base["recommendation"] = "⏸️"
    assert not any("recommendation" in e.lower() for e in agent.validate_assessment(base))


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


def test_append_history_records_issue_counts(tmp_path, monkeypatch):
    # The per-release counts power the release-health trend chart on the frontend.
    hist = tmp_path / "history.json"
    monkeypatch.setattr(config, "HISTORY_FILE", hist)
    a = _valid_assessment(known_issues=[
        {"severity": "high", "category": "regression"},
        {"severity": "medium", "category": "regression"},
        {"severity": "low", "category": "active"},
    ])
    agent.append_history("1.0", a, {"cost_usd": 0.0})
    e = json.loads(hist.read_text())[0]
    assert e["issues"] == 3
    assert e["regressions"] == 2
    assert e["high"] == 1


# ── append_timeline (per-run series for the Trends charts) ───────────────────

def test_append_timeline_appends_every_run(tmp_path, monkeypatch):
    tl = tmp_path / "timeline.json"
    monkeypatch.setattr(config, "TIMELINE_FILE", tl)
    a = _valid_assessment(known_issues=[
        {"severity": "critical", "category": "regression"},
        {"severity": "high", "category": "regression"},
        {"severity": "medium", "category": "active"},
        {"severity": "low", "category": "active"},
    ])
    # Same version twice — unlike append_history this must NOT dedupe (it's a time series).
    agent.append_timeline("1.0", a, {"cost_usd": 0.02, "latency_ms": 120000})
    agent.append_timeline("1.0", a, {"cost_usd": 0.03, "latency_ms": 90000})
    rows = json.loads(tl.read_text())
    assert len(rows) == 2                          # appended, not deduped
    e = rows[0]
    assert e["version"] == "1.0" and e["issues"] == 4 and e["regressions"] == 2
    assert e["critical"] == 1 and e["high"] == 1 and e["medium"] == 1 and e["low"] == 1
    assert e["cost_usd"] == 0.02 and e["latency_ms"] == 120000
    assert "t" in e


def test_append_timeline_prunes_to_keep(tmp_path, monkeypatch):
    tl = tmp_path / "timeline.json"
    monkeypatch.setattr(config, "TIMELINE_FILE", tl)
    monkeypatch.setattr(config, "TIMELINE_KEEP", 5)
    for _ in range(8):
        agent.append_timeline("1.0", _valid_assessment(), {"cost_usd": 0.01, "latency_ms": 1000})
    assert len(json.loads(tl.read_text())) == 5


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


def test_refined_validation_errors_reflect_final_not_primary(tmp_path, monkeypatch):
    """Regression: when the validator forces a refine, the published validation_errors
    (and the deploy gate that reads them) must describe the REFINED assessment, not the
    now-discarded primary. A primary with a validation error + a clean refinement must
    deploy with NO errors — previously the stale primary errors blocked the good page."""
    for name in ("ASSESSMENT_FILE", "HISTORY_FILE", "TIMELINE_FILE", "USAGE_LOG_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")

    primary = _valid_assessment(thesis="too short")          # < 100 chars → a validation error
    refined = _valid_assessment(thesis="This release is solid. " * 10)   # clean
    review = {"agrees": False, "critique": "thesis is too thin", "suggested_recommendation": "✅"}

    def fake_call(model, system, user, **kw):
        if "VALIDATOR" in system:                            # validator step
            parsed = review
        elif "previously produced an assessment" in system:  # refinement step
            parsed = refined
        else:                                                # primary step
            parsed = primary
        return {"success": True, "parsed": parsed, "model": model,
                "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "latency_ms": 1}}
    monkeypatch.setattr(agent, "openrouter_call", fake_call)

    raw = {"target_version": "1.0", "sources": {
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [],
        "clawsweeper": {}, "release_history": [],
    }}
    result = agent.run_assessment_pipeline(raw=raw)
    assert result["success"] is True

    saved = json.loads(config.ASSESSMENT_FILE.read_text())
    assert saved["refined"] is True
    assert saved["validation_errors"] == []      # the clean REFINED assessment, not the primary's
    assert config.HISTORY_FILE.exists()          # deployable → folded into the persistent record


# ── run-completion summary message ───────────────────────────────────────────

def test_run_summary_message_includes_run_and_total_cost():
    msg = agent._run_summary_message("2026.6.6", "⏸️", 0.022, 0.13, 0.45, 7)
    assert "v2026.6.6" in msg
    assert "⏸️" in msg
    assert "7 known issues" in msg
    assert "this run $0.0220" in msg   # the cost of this run
    assert "today $0.13" in msg        # running daily total
    assert "month $0.45" in msg        # running monthly total


def test_run_summary_message_singular_issue():
    assert "(1 known issue)" in agent._run_summary_message("1.0", "✅", 0.0, 0.0, 0.0, 1)


def test_validator_disagrees_triggers_refine():
    # explicit disagreement → refine
    assert agent._validator_disagrees({"agrees": False}) is True
    # clean agreement → no refine
    assert agent._validator_disagrees({"agrees": True}) is False
    assert agent._validator_disagrees({"agrees": True, "miscategorized_issues": []}) is False
    # a concrete mis-categorization overrides a soft "agrees" — the validator can't
    # rubber-stamp the analyst's labels (e.g. the #92843 macOS-tagged-Windows class)
    assert agent._validator_disagrees(
        {"agrees": True, "miscategorized_issues": ["#92843: windows -> macos"]}) is True
    # a failed/unreviewed validator never forces a refine
    assert agent._validator_disagrees({"agrees": False, "unreviewed": True}) is False
