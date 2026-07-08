"""Tests for the adaptive run scheduler (openclaw_status/scheduler.py) and the
cmd_tick wiring."""
from datetime import datetime, timedelta, timezone

from openclaw_status import scheduler, config, cli

NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


def ago(h):
    return NOW - timedelta(hours=h)


# ── cadence_hours ────────────────────────────────────────────────────────────

def test_cadence_tiers_decay_with_age():
    assert scheduler.cadence_hours(0) == 8
    assert scheduler.cadence_hours(47) == 8
    assert scheduler.cadence_hours(48) == 12       # boundary rolls into the next tier
    assert scheduler.cadence_hours(95) == 12
    assert scheduler.cadence_hours(96) == 24
    assert scheduler.cadence_hours(10_000) == 24   # floor tier (upper is None)


def test_first_tier_matches_fresh_release_window():
    # The fast tier is intentionally the same window as the fresh-release banner.
    assert config.ASSESS_CADENCE_TIERS[0][0] == config.FRESH_RELEASE_DAYS * 24


# ── should_run ───────────────────────────────────────────────────────────────

def test_new_release_runs_on_first_detection():
    # A newly-appeared release detected after a normal cadence gap (last run well behind the
    # NEW_RELEASE_RETRY_H window) assesses promptly.
    run, why = scheduler.should_run(NOW, ago(1), "2026.7.0", "2026.6.8", ago(8))
    assert run is True and "new release" in why


def test_new_release_backs_off_after_a_recent_attempt():
    # D12: a new release whose assess keeps FAILING (assessment.json never advances) must NOT
    # re-fire every hourly tick. A run within NEW_RELEASE_RETRY_H does not re-launch — the
    # runaway (hourly re-spend / OnFailure alert storm) is bounded to one attempt per window.
    run, _ = scheduler.should_run(NOW, ago(1), "2026.7.0", "2026.6.8", ago(0.5))
    assert run is False


def test_no_prior_run_runs():
    run, _ = scheduler.should_run(NOW, ago(10), "2026.6.8", "2026.6.8", None)
    assert run is True


def test_fresh_window_paces_at_8h():
    # release age 10h → 8h cadence
    assert scheduler.should_run(NOW, ago(10), "x", "x", ago(7))[0] is False   # 7 < 8 − grace
    assert scheduler.should_run(NOW, ago(10), "x", "x", ago(8))[0] is True


def test_mid_window_paces_at_12h():
    # release age 60h → 12h cadence
    assert scheduler.should_run(NOW, ago(60), "x", "x", ago(11))[0] is False
    assert scheduler.should_run(NOW, ago(60), "x", "x", ago(12))[0] is True


def test_old_window_paces_at_24h():
    # release age 200h → 24h cadence
    assert scheduler.should_run(NOW, ago(200), "x", "x", ago(23))[0] is False
    assert scheduler.should_run(NOW, ago(200), "x", "x", ago(24))[0] is True


def test_grace_fires_a_touch_early():
    # exactly interval − grace counts as due, so an hourly tick never drifts a slot late.
    # release age 10h → 8h cadence → due at 8 − 0.5 = 7.5h since last run.
    assert scheduler.should_run(NOW, ago(10), "x", "x", ago(7.5))[0] is True


def test_unknown_publish_date_uses_floor_cadence():
    # No publish date → treat as old → 24h floor (conservative, avoids over-running).
    assert scheduler.should_run(NOW, None, "x", "x", ago(23))[0] is False
    assert scheduler.should_run(NOW, None, "x", "x", ago(24))[0] is True


# ── cmd_tick wiring ──────────────────────────────────────────────────────────

def test_cmd_tick_triggers_full_when_due(monkeypatch):
    from openclaw_status import github
    monkeypatch.setattr(github, "latest_release",
                        lambda: {"version": "2026.7.0", "tag": "v2026.7.0",
                                 "published_at": "2026-06-20T11:00:00+00:00"})
    monkeypatch.setattr(cli, "_latest_assessed_version", lambda: "2026.6.8")  # new release
    monkeypatch.setattr(cli, "_last_run_started", lambda: ago(0.5))
    seen = {}

    def fake_full(args, trigger="manual"):
        seen["trigger"] = trigger
        return True
    monkeypatch.setattr(cli, "cmd_full", fake_full)

    cli.cmd_tick(None)
    assert seen.get("trigger") == "scheduled"   # ran, labeled as a scheduled run


def test_cmd_tick_skips_when_not_due(monkeypatch):
    # cmd_tick uses the real clock, so make publish/last-run real-now-relative:
    # release ~10h old (6h cadence) with a run ~1h ago → not due.
    from openclaw_status import github
    real_now = datetime.now(timezone.utc)
    monkeypatch.setattr(github, "latest_release",
                        lambda: {"version": "2026.6.8", "tag": "v2026.6.8",
                                 "published_at": (real_now - timedelta(hours=10)).isoformat()})
    monkeypatch.setattr(cli, "_latest_assessed_version", lambda: "2026.6.8")  # not new
    monkeypatch.setattr(cli, "_last_run_started", lambda: real_now - timedelta(hours=1))
    ran = {}
    monkeypatch.setattr(cli, "cmd_full", lambda *a, **k: ran.setdefault("x", True))

    cli.cmd_tick(None)
    assert "x" not in ran   # cheap no-op tick, no full run
