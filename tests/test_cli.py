"""Tests for openclaw_status.cli — run-log population from pipeline outputs."""
import json
import types

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
