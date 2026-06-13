"""Tests for openclaw_status.cli — run-log population from pipeline outputs."""
import json

from openclaw_status import cli, config
from openclaw_status.lib import RunLog


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
