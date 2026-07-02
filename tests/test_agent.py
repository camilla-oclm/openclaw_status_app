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


# ── prompt pins: thesis voice/audience ───────────────────────────────────────
# The thesis reaches the public page + llms-full.txt verbatim. Both thesis slots must
# carry the audience constraint so the copy describes the RELEASE, never the internal
# analyst/validator exchange (a live page once opened "The validator correctly
# identifies that the original analysis omitted…").

def test_analyst_thesis_slot_has_audience_constraint():
    assert "never mention this analysis process, the validator" in agent.SYSTEM_PROMPT


def test_changes_rule_keeps_fallback_guidance():
    # `changes` is overwritten by the deterministic changelog parser, but the analyst's
    # extraction stays the fallback for an unstructured body — the (slimmed) rule must
    # keep saying so, and the schema must keep the field.
    assert "recomputed deterministically" in agent.SYSTEM_PROMPT
    assert '"changes"' in agent._OUTPUT_SCHEMA


def test_refine_prompt_is_user_facing():
    assert "never mention the validator, the original analysis" in agent.REFINEMENT_PROMPT
    # The refine HEADLINE SLOT must not be primed to say "REFINED" to end users
    # (the prompt's own job description may still say "produce a REFINED assessment").
    assert "summary of the REFINED" not in agent.REFINEMENT_PROMPT
    # And the refine pass keeps the rule that critique goes into content, not copy.
    assert "user-facing copy about the RELEASE" in agent.REFINEMENT_PROMPT


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


def test_build_context_feeds_fixes_section_past_a_long_changelog():
    # Regression: the changelog used to be head-sliced at 3000 chars, so the ### Fixes
    # section (which sits after Highlights + a long contributor tail) never reached the
    # analyst — fixes rendered as 0. The section-aware feed must include it regardless.
    body = (
        "## 1.2.3\n\n### Highlights\n\n"
        + "- **Feature:** " + ("x" * 4000) + " (#1)\n\n"      # > 3000 chars of Highlights
        + "### Fixes\n\n- Storage and migrations: avoid WAL on network FS (#42)\n"
    )
    raw = {
        "target_version": "1.2.3",
        "sources": {
            "latest_release": {"tag": "v1.2.3", "published_at": "2026-06-01T00:00:00Z",
                               "body": body},
            "latest_prerelease": None, "github_issues": [], "clawsweeper": {},
        },
    }
    ctx = agent.build_context(raw)
    assert "Storage and migrations" in ctx     # the fix survives despite the long Highlights


# ── _cap_fresh_confidence ───────────────────────────────────────────────────

_FRESH_RELEASE = {"tag": "v2026.6.9", "published_at": "2026-06-21T01:00:00Z"}


def test_cap_fresh_confidence_high_to_medium_when_fresh():
    a = {"confidence": "high"}
    capped = agent._cap_fresh_confidence(a, "2026.6.9", "2026-06-21T08:00:00Z", _FRESH_RELEASE)
    assert capped is True
    assert a["confidence"] == "medium"          # never trips the low-confidence deploy guard


def test_cap_fresh_confidence_leaves_medium_and_low_alone():
    for level in ("medium", "low"):
        a = {"confidence": level}
        assert agent._cap_fresh_confidence(a, "2026.6.9", "2026-06-21T08:00:00Z", _FRESH_RELEASE) is False
        assert a["confidence"] == level


def test_cap_fresh_confidence_no_cap_once_release_is_old():
    a = {"confidence": "high"}
    # assessed 10 days after publish → outside the fresh window → analyst's high stands
    assert agent._cap_fresh_confidence(a, "2026.6.9", "2026-07-01T08:00:00Z", _FRESH_RELEASE) is False
    assert a["confidence"] == "high"


def test_build_context_no_truncation_note_when_under_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_ISSUES_IN_CONTEXT", 30)
    ctx = agent.build_context(_raw_with_n_issues(4))
    assert "Showing the top" not in ctx
    for n in (1, 2, 3, 4):
        assert f"### #{n} " in ctx


# ── _cap_thin_evidence_confidence ────────────────────────────────────────────

def test_cap_thin_evidence_when_validator_unreviewed():
    a = {"confidence": "high"}
    reason = agent._cap_thin_evidence_confidence(a, validator_unreviewed=True, scout_degraded=False)
    assert reason == "validator unavailable"
    assert a["confidence"] == "medium"          # single-model → never "high"


def test_cap_thin_evidence_when_scout_degraded():
    a = {"confidence": "high"}
    reason = agent._cap_thin_evidence_confidence(a, validator_unreviewed=False, scout_degraded=True)
    assert reason == "issue scout was incomplete"
    assert a["confidence"] == "medium"


def test_cap_thin_evidence_no_cap_when_full_evidence():
    a = {"confidence": "high"}
    assert agent._cap_thin_evidence_confidence(
        a, validator_unreviewed=False, scout_degraded=False) is None
    assert a["confidence"] == "high"            # a fully-reviewed, fully-scouted high stands


def test_cap_thin_evidence_leaves_medium_and_low_alone():
    for level in ("medium", "low"):
        a = {"confidence": level}
        assert agent._cap_thin_evidence_confidence(
            a, validator_unreviewed=True, scout_degraded=True) is None
        assert a["confidence"] == level


# ── _degraded_input_reason / fail closed on degraded collection ──────────────

def _good_raw():
    return {"target_version": "1.0",
            "sources": {"latest_release": {"tag": "v1.0"}, "github_issues": []}}


def test_degraded_input_none_when_usable():
    assert agent._degraded_input_reason(_good_raw(), "1.0") is None


@pytest.mark.parametrize("mutate,version", [
    (lambda r: r.update(pipeline_aborted=True, abort_reason="timeout after npm"), "1.0"),
    (lambda r: r.update(sources={}), "1.0"),
    (lambda r: r.update(sources={"latest_release": {}}), "1.0"),  # release without a tag
    (lambda r: None, ""),          # empty version
    (lambda r: None, "unknown"),   # unresolved version
])
def test_degraded_input_flags_bad_collections(mutate, version):
    raw = _good_raw()
    mutate(raw)
    assert agent._degraded_input_reason(raw, version) is not None


def test_pipeline_fails_closed_on_aborted_collection_without_spending(tmp_path, monkeypatch):
    """H3 regression: a timed-out/aborted collect (pipeline_aborted, empty sources) must NOT
    drive the LLM — the pipeline returns success:False before any openrouter_call, so the
    last good page is kept rather than overwritten with a verdict over empty data."""
    monkeypatch.setattr(config, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(agent, "openrouter_call",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM on degraded input")))
    raw = {"target_version": "", "sources": {}, "pipeline_aborted": True,
           "abort_reason": "timeout after npm"}
    result = agent.run_assessment_pipeline(raw=raw)
    assert result["success"] is False
    assert "degraded input" in result["error"]
    assert not config.ASSESSMENT_FILE.exists()   # nothing published / overwritten


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


# ── shared assessed_at (one run stamp across output / history / timeline) ──────

def test_append_history_uses_passed_assessed_at(tmp_path, monkeypatch):
    # The pipeline threads its single assessed_at so the history entry, the timeline
    # point, and assessment.json all carry the SAME instant (no 3-way now() drift).
    hist = tmp_path / "history.json"
    monkeypatch.setattr(config, "HISTORY_FILE", hist)
    stamp = "2026-06-30T12:00:00+00:00"
    agent.append_history("1.0", _valid_assessment(), {"cost_usd": 0.0}, assessed_at=stamp)
    assert json.loads(hist.read_text())[0]["assessed_at"] == stamp


def test_append_timeline_uses_passed_assessed_at(tmp_path, monkeypatch):
    tl = tmp_path / "timeline.json"
    monkeypatch.setattr(config, "TIMELINE_FILE", tl)
    stamp = "2026-06-30T12:00:00+00:00"
    agent.append_timeline("1.0", _valid_assessment(), {"cost_usd": 0.0, "latency_ms": 0},
                          assessed_at=stamp)
    assert json.loads(tl.read_text())[0]["t"] == stamp


# ── soft continuity/count contradiction (non-overriding) ──────────────────────

def _prev(rec, high, version="1.0"):
    return {"version": version, "recommendation": rec, "high": high}


def _ki(n_high):
    return [{"severity": "high", "category": "active"} for _ in range(n_high)]


def test_continuity_flags_upgrade_without_evidence_improving():
    # Verdict eased ⏸️ → ✅ but the high/critical count did NOT fall → soft flag.
    prev = _prev("⏸️", high=3)
    a = _valid_assessment(recommendation="✅", known_issues=_ki(3))
    note = agent._continuity_contradiction(prev, a)
    assert note and "⏸️→✅" in note and "3→3" in note


def test_continuity_silent_when_high_count_falls():
    # An upgrade justified by fewer high issues (debunked/downgraded) is NOT flagged.
    prev = _prev("⏸️", high=3)
    a = _valid_assessment(recommendation="⚠️", known_issues=_ki(1))
    assert agent._continuity_contradiction(prev, a) is None


def test_continuity_silent_on_downgrade():
    # Getting MORE cautious can never contradict continuity.
    prev = _prev("⚠️", high=1)
    a = _valid_assessment(recommendation="⏸️", known_issues=_ki(5))
    assert agent._continuity_contradiction(prev, a) is None


def test_continuity_silent_when_verdict_unchanged():
    prev = _prev("⚠️", high=2)
    a = _valid_assessment(recommendation="⚠️", known_issues=_ki(2))
    assert agent._continuity_contradiction(prev, a) is None


def test_continuity_silent_without_prev():
    a = _valid_assessment(recommendation="✅", known_issues=_ki(3))
    assert agent._continuity_contradiction(None, a) is None


def test_continuity_normalizes_retired_verdict():
    # A prior 🔄 (retired) reads as ⏸️, so ⏸️→⚠️ with a flat high count still flags.
    prev = _prev("🔄", high=2)
    a = _valid_assessment(recommendation="⚠️", known_issues=_ki(2))
    note = agent._continuity_contradiction(prev, a)
    assert note and "⏸️→⚠️" in note


def test_continuity_silent_when_prev_count_missing():
    # No comparable prior count → nothing to contradict (fail open).
    prev = {"version": "1.0", "recommendation": "⏸️"}   # no "high"
    a = _valid_assessment(recommendation="✅", known_issues=_ki(3))
    assert agent._continuity_contradiction(prev, a) is None


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
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [],
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
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [],
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

def test_discarded_primary_spend_is_logged_and_counted(tmp_path, monkeypatch):
    """M5: a primary that HTTP-succeeds but parse-fails, then a fallback succeeds — the
    primary's real billed cost must still reach the usage log / budget tracker and the run's
    reported total, not vanish because only the final attempt is returned."""
    for name in ("ASSESSMENT_FILE", "HISTORY_FILE", "TIMELINE_FILE", "USAGE_LOG_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    valid = _valid_assessment()

    def fake_call(model, system, user, **kw):
        if model == config.PRIMARY_MODEL:                       # billed, but unparseable
            return {"success": True, "parsed": {"error": "unparseable"}, "model": model,
                    "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.04, "latency_ms": 1}}
        return {"success": True, "parsed": valid, "model": model,   # fallback succeeds
                "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.03, "latency_ms": 1}}
    monkeypatch.setattr(agent, "openrouter_call", fake_call)

    raw = {"target_version": "1.0", "sources": {
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [], "clawsweeper": {}, "release_history": [],
    }}
    result = agent.run_assessment_pipeline(raw=raw, single_call=True)
    assert result["success"] is True

    costs = sorted(e.get("cost_usd", 0) for e in json.loads(config.USAGE_LOG_FILE.read_text()))
    assert 0.04 in costs and 0.03 in costs           # discarded primary AND kept fallback logged
    assert result["usage"]["cost_usd"] == pytest.approx(0.07)   # run total includes both


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


# ── flip conditions ("what would change this verdict") ──────────────────────

def test_prompt_asks_for_flip_conditions():
    # The schema carries the field and the rules explain it (concrete, direction-named,
    # evidence-cited) — the page's falsifiability tripwires depend on both.
    assert '"flip_conditions"' in agent._OUTPUT_SCHEMA
    assert "flip_conditions" in agent.SYSTEM_PROMPT
    assert "naming the direction" in agent.SYSTEM_PROMPT
    # The shared schema flows to the refine pass too.
    assert '"flip_conditions"' in agent.REFINEMENT_PROMPT


def test_validate_detects_xss_in_flip_conditions():
    a = _valid_assessment()
    a["flip_conditions"] = ["fine", "<script>alert(1)</script> lands a fix"]
    errors = agent.validate_assessment(a)
    assert any("flip_conditions" in e for e in errors)


def test_validate_flip_conditions_optional():
    # Absent on older assessments / terse fallback models — must not block a deploy.
    a = _valid_assessment()
    a.pop("flip_conditions", None)
    assert agent.validate_assessment(a) == []


# ── compact validator review (the ⚖︎-chip expander payload) ──────────────────

def test_compact_review_none_when_unreviewed_or_junk():
    assert agent._compact_validator_review({"unreviewed": True, "agrees": True}) is None
    assert agent._compact_validator_review(None) is None
    assert agent._compact_validator_review("nope") is None


def test_compact_review_screens_and_caps():
    review = {
        "agrees": True,
        "critique": "  solid   work\n overall  ",
        "confidence_in_review": "high",
        "suggested_recommendation": "🔄",          # retired glyph → normalized ⏸️
        "miscategorized_issues": [
            "#1: windows -> macos",
            "<script>alert(1)</script>",           # XSS → dropped, doesn't block
            "x" * 500,                             # over-long → truncated
            "#4", "#5", "#6", "#7",                # beyond cap of 5 (after the drop)
        ],
        "missed_issues": ["#42"],
        "logical_errors": [123],                   # junk type → stringified
        "overruled_claims": [],
    }
    d = agent._compact_validator_review(review)
    assert d["critique"] == "solid work overall"
    assert d["suggested_recommendation"] == "⏸️"
    assert d["confidence"] == "high"
    assert "#1: windows -> macos" in d["miscategorized_issues"]
    assert all("<script" not in s for s in d["miscategorized_issues"])
    assert len(d["miscategorized_issues"]) == 5
    assert max(len(s) for s in d["miscategorized_issues"]) <= 240
    assert d["missed_issues"] == ["#42"]
    assert d["logical_errors"] == ["123"]


def test_compact_review_drops_non_verdict_suggestion_and_xss_critique():
    d = agent._compact_validator_review(
        {"agrees": False, "critique": "<script>x</script>", "suggested_recommendation": "null"})
    assert d["critique"] == ""                     # screened out, not blocking
    assert d["suggested_recommendation"] == ""


def test_pipeline_persists_validator_review(tmp_path, monkeypatch):
    """The compact review must land in assessment.json (render ships it as review.detail)."""
    for name in ("ASSESSMENT_FILE", "HISTORY_FILE", "TIMELINE_FILE", "USAGE_LOG_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    valid = _valid_assessment()
    review = {"agrees": True, "critique": "checked the labels, sound",
              "confidence_in_review": "high", "suggested_recommendation": None,
              "miscategorized_issues": [], "missed_issues": ["#77"]}

    def fake_call(model, system, user, **kw):
        parsed = review if "VALIDATOR" in system else valid
        return {"success": True, "parsed": parsed, "model": model,
                "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "latency_ms": 1}}
    monkeypatch.setattr(agent, "openrouter_call", fake_call)

    raw = {"target_version": "1.0", "sources": {
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [], "clawsweeper": {}, "release_history": [],
    }}
    assert agent.run_assessment_pipeline(raw=raw)["success"] is True
    saved = json.loads(config.ASSESSMENT_FILE.read_text())
    assert saved["validator_review"]["critique"] == "checked the labels, sound"
    assert saved["validator_review"]["missed_issues"] == ["#77"]


def test_pipeline_validator_review_none_in_single_call(tmp_path, monkeypatch):
    for name in ("ASSESSMENT_FILE", "HISTORY_FILE", "TIMELINE_FILE", "USAGE_LOG_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    valid = _valid_assessment()
    monkeypatch.setattr(agent, "openrouter_call", lambda *a, **kw: {
        "success": True, "parsed": valid, "model": "m",
        "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "latency_ms": 1}})
    raw = {"target_version": "1.0", "sources": {
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [], "clawsweeper": {}, "release_history": [],
    }}
    assert agent.run_assessment_pipeline(raw=raw, single_call=True)["success"] is True
    assert json.loads(config.ASSESSMENT_FILE.read_text())["validator_review"] is None


# ── latency watch (slow-model heads-up) ──────────────────────────────────────

def test_latency_watch_quiet_under_threshold():
    steps = [{"step": "primary", "model": "m1", "usage": {"latency_ms": 150_000}},
             {"step": "validator", "model": "m2", "usage": {"latency_ms": 299_999}}]
    assert agent._latency_watch(steps) is None
    assert agent._latency_watch([]) is None
    assert agent._latency_watch(None) is None


def test_latency_watch_flags_slow_steps_with_names():
    steps = [{"step": "primary", "model": "deepseek/deepseek-v4-pro",
              "usage": {"latency_ms": 301_000}},
             {"step": "validator", "model": "qwen/qwen3.7-plus",
              "usage": {"latency_ms": 5_000}},
             {"step": "refinement", "model": "deepseek/deepseek-v4-pro",
              "usage": {"latency_ms": 412_000}}]
    msg = agent._latency_watch(steps)
    assert "primary (deepseek/deepseek-v4-pro) took 301s" in msg
    assert "refinement" in msg and "412s" in msg
    assert "validator" not in msg                       # the fast step isn't named
    assert "slow model calls" in msg                    # plural form
    assert f"≥{config.SLOW_CALL_WARN_S}s" in msg


def test_latency_watch_tolerates_missing_usage():
    assert agent._latency_watch([{"step": "primary", "model": "m"}]) is None


def test_pipeline_notifies_on_slow_call(tmp_path, monkeypatch):
    """A slow (but successful) primary must fire the latency heads-up webhook."""
    for name in ("ASSESSMENT_FILE", "HISTORY_FILE", "TIMELINE_FILE", "USAGE_LOG_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name.lower()}.json")
    valid = _valid_assessment()
    monkeypatch.setattr(agent, "openrouter_call", lambda *a, **kw: {
        "success": True, "parsed": valid, "model": "m",
        "usage": {"tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0,
                  "latency_ms": config.SLOW_CALL_WARN_S * 1000 + 1}})
    sent = []
    monkeypatch.setattr(agent, "notify", lambda text: sent.append(text) or True)
    raw = {"target_version": "1.0", "sources": {
        "latest_release": {"tag": "v1.0", "published_at": "2026-01-01T00:00:00Z"},
        "latest_prerelease": None, "github_issues": [], "clawsweeper": {}, "release_history": [],
    }}
    assert agent.run_assessment_pipeline(raw=raw, single_call=True)["success"] is True
    assert any("🐢 slow model call" in m for m in sent)
