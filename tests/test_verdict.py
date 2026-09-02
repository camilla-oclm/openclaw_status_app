"""Tests for openclaw_status.verdict — the deterministic evidence gate, the best-version
pointer and the status vocabulary. Hermetic (no network)."""
from openclaw_status import verdict


def issue(**o):
    base = {"number": 1, "title": "x", "severity": "high", "state": "open",
            "affects_version": True, "priority_provenance": "bot", "impact": "low",
            "reactions": 0, "version_match": "series", "labels": [], "weight": 50}
    base.update(o)
    return base


# ── credible blockers (twin of the page's blockersFor severity gate) ─────────────

def test_bot_labeled_no_traction_high_is_not_credible():
    assert verdict.is_credible_blocker(issue(severity="high", priority_provenance="bot",
                                             impact="low")) is False


def test_high_becomes_credible_with_human_corroboration_or_traction():
    assert verdict.is_credible_blocker(issue(priority_provenance="bot-corroborated")) is True
    assert verdict.is_credible_blocker(issue(priority_provenance="human")) is True
    assert verdict.is_credible_blocker(issue(impact="medium")) is True


def test_absent_evidence_fields_fail_closed():
    # A legacy payload without provenance/impact keeps the old, stricter behaviour: blocks.
    i = issue()
    del i["priority_provenance"]
    del i["impact"]
    assert verdict.is_credible_blocker(i) is True


def test_critical_is_always_credible_when_open_and_version_confirmed():
    assert verdict.is_credible_blocker(issue(severity="critical")) is True


def test_closed_or_unconfirmed_or_low_never_blocks():
    assert verdict.is_credible_blocker(issue(severity="critical", state="closed")) is False
    assert verdict.is_credible_blocker(issue(severity="critical", affects_version=False)) is False
    assert verdict.is_credible_blocker(issue(severity="medium", impact="high")) is False


def test_staged_fix_does_not_clear_a_blocker():
    # The fix isn't in THIS release — it stays a blocker (matches the page + importance_weight).
    assert verdict.is_credible_blocker(issue(severity="critical", fixed_in="2026.9.1-beta.1")) is True


def test_credible_blockers_sorted_by_weight_then_number():
    bs = verdict.credible_blockers([issue(number=3, severity="critical", weight=60),
                                    issue(number=1, severity="critical", weight=80),
                                    issue(number=2, severity="critical", weight=60),
                                    issue(number=9, severity="low")])
    assert [b["number"] for b in bs] == [1, 2, 3]


# ── the gate ───────────────────────────────────────────────────────────────────

def test_gate_is_update_with_no_credible_blocker():
    g = verdict.evidence_gate([issue(), issue(number=2, severity="medium"),
                               issue(number=3, severity="critical", state="closed")])
    assert g["verdict"] == "✅"
    assert g["blockers"] == [] and g["blocker_count"] == 0
    assert "No credible blocking issue" in g["reason"]


def test_gate_is_care_with_contained_blockers():
    g = verdict.evidence_gate([issue(number=7, severity="critical"),
                               issue(number=8, priority_provenance="human")])
    assert g["verdict"] == "⚠️"
    assert sorted(g["blockers"]) == [7, 8]
    assert g["widespread"] == [] and g["serious"] == []
    assert "2 credible blocking issues" in g["reason"]


def test_gate_is_skip_on_a_widespread_breaker():
    g = verdict.evidence_gate([issue(number=5, severity="high", impact="high", reactions=40)])
    assert g["verdict"] == "⏸️"
    assert g["widespread"] == [5]
    assert "widespread" in g["reason"]


def test_widespread_by_reaction_count_alone():
    assert verdict.is_widespread(issue(reactions=verdict.WIDESPREAD_REACTIONS)) is True
    assert verdict.is_widespread(issue(reactions=verdict.WIDESPREAD_REACTIONS - 1)) is False


def test_gate_is_skip_on_credible_unfixed_security_issue_naming_exact_version():
    ser = issue(number=11, severity="critical", version_match="exact",
                priority_provenance="human", labels=["impact:security", "P0"])
    g = verdict.evidence_gate([ser])
    assert g["verdict"] == "⏸️" and g["serious"] == [11]
    # Any one of the guards missing → back to ⚠️ (series match, staged fix, or no person
    # behind it: bot-corroborated / unknown provenance with no engagement fails OPEN here).
    assert verdict.evidence_gate([{**ser, "version_match": "series"}])["verdict"] == "⚠️"
    assert verdict.evidence_gate([{**ser, "fixed_in": "2026.9.1"}])["verdict"] == "⚠️"
    for prov in ("bot", "bot-corroborated", "unknown", None):
        assert verdict.evidence_gate([{**ser, "priority_provenance": prov}])["verdict"] == "⚠️", prov
    # ...but community engagement corroborates it even when the P label is bot-applied.
    assert verdict.evidence_gate([{**ser, "priority_provenance": "bot", "impact": "medium"}])["verdict"] == "⏸️"


def test_gate_tolerates_junk_rows():
    assert verdict.evidence_gate([None, "x", {}, issue(severity="critical", number=None)])["verdict"] == "⚠️"


# ── floor + departure ──────────────────────────────────────────────────────────

def test_floor_raises_a_verdict_less_cautious_than_the_gate():
    a = {"recommendation": "✅"}
    note = verdict.apply_gate_floor(a, {"verdict": "⚠️", "reason": "r"})
    assert a["recommendation"] == "⚠️" and "raised" in note


def test_floor_leaves_an_equal_or_more_cautious_verdict_alone():
    for rec in ("⚠️", "⏸️"):
        a = {"recommendation": rec}
        assert verdict.apply_gate_floor(a, {"verdict": "⚠️"}) is None
        assert a["recommendation"] == rec


def test_floor_normalizes_retired_wait_glyph():
    a = {"recommendation": "🔄"}   # retired → ⏸️, already above a ⚠️ floor
    assert verdict.apply_gate_floor(a, {"verdict": "⚠️"}) is None


def test_departure_note_only_when_more_cautious_than_gate():
    d = verdict.departure_note({"recommendation": "⏸️", "gate_departure_reason": "cluster #1 #2"},
                               {"verdict": "⚠️"})
    assert d == {"departed": True, "reason": "cluster #1 #2"}
    d2 = verdict.departure_note({"recommendation": "⚠️", "gate_departure_reason": "ignored"},
                                {"verdict": "⚠️"})
    assert d2 == {"departed": False, "reason": ""}


# ── best version to run today ──────────────────────────────────────────────────

def _ledger(**versions):
    return {v: {"issues": {str(i["number"]): i for i in iss}} for v, iss in versions.items()}


RH = [
    {"tag": "v2.3", "version": "2.3", "published_at": "2026-09-01", "prerelease": False},
    {"tag": "v2.2", "version": "2.2", "published_at": "2026-08-31", "prerelease": False},
    {"tag": "v2.1", "version": "2.1", "published_at": "2026-08-04", "prerelease": False},
    {"tag": "v2.1-beta.9", "version": "2.1-beta.9", "published_at": "2026-08-02", "prerelease": True},
    {"tag": "v2.0", "version": "2.0", "published_at": "2026-07-13", "prerelease": False},
]
VH = [
    {"version": "2.3", "recommendation": "⚠️"},
    {"version": "2.2", "recommendation": "⏸️"},
    {"version": "2.1", "recommendation": "⚠️"},
    {"version": "2.0", "recommendation": "⚠️"},
]


def test_recommends_newest_settled_release_over_a_fresh_latest():
    led = _ledger(**{"2.3": [issue(severity="critical")], "2.2": [issue(severity="critical")],
                     "2.1": [issue(number=44, severity="critical", weight=70)],
                     "2.0": []})
    r = verdict.recommended_version(release_history=RH, version_history=VH, ledger=led,
                                    current_version="2.3", today="2026-09-02")
    assert r["version"] == "2.1" and r["kind"] == "settled"
    assert r["age_days"] == 29 and r["recommendation"] == "⚠️" and r["gate"] == "⚠️"
    assert r["blockers"][0]["number"] == 44 and r["blocker_count"] == 1
    assert r["latest"] == {"version": "2.3", "age_days": 1, "recommendation": "⚠️", "settled": False}
    assert r["considered"] == 4       # the beta is skipped


def test_latest_itself_is_the_pick_once_it_has_settled():
    led = _ledger(**{"2.3": [], "2.2": [], "2.1": [], "2.0": []})
    r = verdict.recommended_version(release_history=RH, version_history=VH, ledger=led,
                                    current_version="2.3", today="2026-09-20")
    assert r["version"] == "2.3" and r["kind"] == "latest" and r["gate"] == "✅"


def test_skip_verdict_or_widespread_blocker_disqualifies():
    # 2.2 is ⏸️ by verdict; 2.1 carries a widespread breaker → falls through to 2.0.
    led = _ledger(**{"2.3": [], "2.2": [], "2.1": [issue(severity="high", impact="high")], "2.0": []})
    r = verdict.recommended_version(release_history=RH, version_history=VH, ledger=led,
                                    current_version="2.3", today="2026-09-02")
    assert r["version"] == "2.0" and r["kind"] == "settled"


def test_nothing_settled_yields_none_kind():
    vh = [dict(h, recommendation="⏸️") for h in VH]
    led = _ledger(**{"2.3": [], "2.2": [], "2.1": [], "2.0": []})
    r = verdict.recommended_version(release_history=RH, version_history=vh, ledger=led,
                                    current_version="2.3", today="2026-09-02")
    assert r["version"] is None and r["kind"] == "none" and r["blockers"] == []
    assert r["latest"]["version"] == "2.3"


def test_unassessed_or_ledger_pruned_versions_are_never_recommended():
    # 2.1 has no ledger entry (pruned) and 2.0 was never assessed → neither can be vouched for.
    led = _ledger(**{"2.3": [issue(severity="critical")], "2.2": []})
    vh = [h for h in VH if h["version"] != "2.0"]
    r = verdict.recommended_version(release_history=RH, version_history=vh, ledger=led,
                                    current_version="2.3", today="2026-09-02")
    assert r["version"] is None and r["considered"] == 2


def test_recommended_version_handles_empty_inputs():
    r = verdict.recommended_version(release_history=[], version_history=[], ledger={},
                                    current_version="9.9")
    assert r["kind"] == "none" and r["latest"] == {"version": "9.9"}


# ── status vocabulary ──────────────────────────────────────────────────────────

def test_status_words_for_the_three_verdicts():
    assert verdict.status_for("✅")["label"] == "Safe to update"
    assert verdict.status_for("⚠️") == {"key": "care", "label": "Update with care",
                                        "recommendation": "⚠️", "early_read": None}
    assert verdict.status_for("⏸️")["key"] == "skip"


def test_fresh_release_reads_too_new_to_call_except_a_skip():
    s = verdict.status_for("⚠️", fresh=True)
    assert s["key"] == "wait" and s["label"] == "Too new to call"
    assert s["early_read"] == "Update with care" and s["recommendation"] == "⚠️"
    assert verdict.status_for("⏸️", fresh=True)["key"] == "skip"   # early evidence already negative


def test_status_normalizes_retired_glyph_and_survives_junk():
    assert verdict.status_for("🔄")["key"] == "skip"
    assert verdict.status_for("")["key"] == "unknown"
