"""Tests for openclaw_status.cli — run-log population from pipeline outputs."""
import json
import sys
import types

import pytest

from openclaw_status import cli, config, lib
from openclaw_status.lib import RunLog


def test_notify_failure_sends_message_with_unit(monkeypatch):
    sent = []
    monkeypatch.setattr(lib, "notify", lambda msg: sent.append(msg) or True)
    cli.cmd_notify_failure(types.SimpleNamespace(unit="openclaw-status.service"))
    assert sent, "expected a failure alert to be sent"
    assert "FAILED" in sent[0] and "openclaw-status.service" in sent[0]


def test_notify_failure_is_best_effort_when_webhook_unset(monkeypatch):
    # notify() returns False when ALERT_WEBHOOK_URL is unset — the handler must not raise
    # (it's the OnFailure= unit; it can't itself fail).
    monkeypatch.setattr(lib, "notify", lambda msg: False)
    cli.cmd_notify_failure(types.SimpleNamespace(unit=None))   # no exception = pass


def test_populate_run_log_fills_from_outputs(tmp_path, monkeypatch):
    raw = tmp_path / "raw-data.json"
    raw.write_text(json.dumps({
        "source_status": {"npm": {"status": "ok"}},
        "pipeline_aborted": False,
    }))
    assess = tmp_path / "assessment.json"
    assess.write_text(json.dumps({
        "primary_model": "deepseek/deepseek-v4-pro",
        "usage": {"cost_usd": 0.0231},
        "assessment": {"recommendation": "⏸️"},
        "validation_errors": [],
    }))
    monkeypatch.setattr(config, "RAW_DATA_FILE", raw)
    monkeypatch.setattr(config, "ASSESSMENT_FILE", assess)

    rl = RunLog(trigger_type="manual")
    cli._populate_run_log(rl)

    assert rl.recommendation == "⏸️"
    assert rl.cost_usd == 0.0231
    assert rl.model_used == "deepseek/deepseek-v4-pro"
    assert rl.source_status == {"npm": {"status": "ok"}}
    assert rl.pipeline_aborted is False


def test_populate_run_log_survives_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DATA_FILE", tmp_path / "nope-raw.json")
    monkeypatch.setattr(config, "ASSESSMENT_FILE", tmp_path / "nope-assess.json")
    rl = RunLog(trigger_type="manual")
    cli._populate_run_log(rl)  # must not raise
    assert rl.recommendation == ""  # untouched default


# ── _latest_assessed_version (M3: don't re-fire on a non-deployable new release) ──

def test_latest_assessed_version_reads_assessment_json(tmp_path, monkeypatch):
    """M3: a non-deployable run still writes assessment.json, so last_assessed reflects the
    just-assessed version — the scheduler won't re-detect it as 'new' every hourly tick and
    re-spend the full pipeline."""
    monkeypatch.setattr(config, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "assessment.json").write_text(json.dumps({"version": "2026.7.0"}))
    (tmp_path / "history.json").write_text(
        json.dumps([{"version": "2026.6.9", "assessed_at": "2026-06-21T00:00:00Z"}]))
    assert cli._latest_assessed_version() == "2026.7.0"   # the just-assessed (non-deployable) one


def test_latest_assessed_version_falls_back_to_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSESSMENT_FILE", tmp_path / "missing.json")   # no assessment yet
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "history.json").write_text(
        json.dumps([{"version": "2026.6.9", "assessed_at": "2026-06-21T00:00:00Z"}]))
    assert cli._latest_assessed_version() == "2026.6.9"


def test_latest_assessed_version_ignores_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSESSMENT_FILE", tmp_path / "assessment.json")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "assessment.json").write_text(json.dumps({"version": "unknown"}))
    (tmp_path / "history.json").write_text(
        json.dumps([{"version": "2026.6.9", "assessed_at": "2026-06-21T00:00:00Z"}]))
    assert cli._latest_assessed_version() == "2026.6.9"    # 'unknown' falls through to history


# ── L11: lock contention is a BENIGN skip, never a hard failure ──────────────
# The scheduled tick must not trip the systemd OnFailure alert just because a manual
# run holds the lock. cmd_full signals this by RETURNING False (not raising / sys.exit),
# and cmd_tick treats that False as a skip. A regression to sys.exit(1) inside cmd_full
# would fire false "FAILED" alerts on every contended tick — these pin against it.

def _boom(*a, **k):
    raise AssertionError("pipeline stages must not run when the lock is held")


def test_cmd_full_returns_false_on_lock_contention(monkeypatch):
    monkeypatch.setattr(cli, "acquire_pipeline_lock", lambda: False)
    # If contention were mishandled as "acquired", collect would run — make that loud.
    monkeypatch.setattr(cli, "cmd_collect", _boom)
    assert cli.cmd_full(types.SimpleNamespace()) is False   # returns, does not raise/exit


def test_cmd_tick_skips_benignly_when_full_returns_false(monkeypatch):
    from openclaw_status import github, scheduler
    monkeypatch.setattr(github, "latest_release",
                        lambda: {"version": "1.0", "published_at": "2026-06-30T00:00:00Z"})
    monkeypatch.setattr(scheduler, "should_run", lambda *a, **k: (True, "assessment due"))
    monkeypatch.setattr(cli, "_latest_assessed_version", lambda: "0.9")
    monkeypatch.setattr(cli, "_last_run_started", lambda: None)
    called = {"full": False}
    def _full_contended(args, trigger="scheduled"):
        called["full"] = True
        return False   # lock held
    monkeypatch.setattr(cli, "cmd_full", _full_contended)
    cli.cmd_tick(types.SimpleNamespace())   # no exception / no non-zero exit = pass
    assert called["full"] is True           # it did try, then skipped benignly


# ── CLI stage wrappers + main() dispatch (D28) ───────────────────────────────

def test_cmd_collect_invokes_collect(monkeypatch):
    from openclaw_status import collector
    called = []
    monkeypatch.setattr(collector, "collect", lambda: called.append(True))
    cli.cmd_collect(types.SimpleNamespace())
    assert called == [True]


def test_cmd_assess_invokes_pipeline_and_exits_on_failure(monkeypatch):
    from openclaw_status import agent
    monkeypatch.setattr(agent, "run_assessment_pipeline", lambda single_call=False: {"success": True})
    cli.cmd_assess(types.SimpleNamespace(single=False))                 # success → no exit
    monkeypatch.setattr(agent, "run_assessment_pipeline", lambda single_call=False: {"success": False})
    with pytest.raises(SystemExit):                                     # failure → exit 1
        cli.cmd_assess(types.SimpleNamespace(single=False))


def test_cmd_render_assessment_passes_output_path(monkeypatch):
    from openclaw_status import render
    got = {}
    monkeypatch.setattr(render, "render_assessment_page",
                        lambda output_path=None: got.update(out=output_path))
    cli.cmd_render_assessment(types.SimpleNamespace(output="/tmp/x.html"))
    assert got["out"] == "/tmp/x.html"


def test_main_dispatches_each_command_to_its_handler(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "k")             # satisfy the assess/tick key gate
    for cmd, handler in [("collect", "cmd_collect"), ("assess", "cmd_assess"),
                         ("render-assessment", "cmd_render_assessment"),
                         ("full", "cmd_full"), ("tick", "cmd_tick")]:
        seen = []
        monkeypatch.setattr(cli, handler, lambda args, _s=seen: _s.append(True))
        monkeypatch.setattr(sys, "argv", ["run.py", cmd])
        cli.main()
        assert seen == [True], f"{cmd} did not dispatch to {handler}"
