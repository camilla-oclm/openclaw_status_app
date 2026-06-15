#!/usr/bin/env python3
"""
OpenClaw Status — Unified CLI

Usage:
  openclaw-status collect              Run data collection pipeline
  openclaw-status assess               Run LLM assessment pipeline
  openclaw-status assess --single      Single-call mode (skip validator)
  openclaw-status render-assessment    Render the public assessment page
  openclaw-status full                 collect → assess → render-assessment
  openclaw-status notify-test [msg]    Send a test alert to ALERT_WEBHOOK_URL
"""

import sys

from openclaw_status import config
from openclaw_status.lib import acquire_pipeline_lock, release_pipeline_lock, RunLog


def cmd_collect(args):
    from .collector import collect
    collect()


def cmd_assess(args):
    from .agent import run_assessment_pipeline
    single = getattr(args, "single", False) or "--single" in sys.argv
    result = run_assessment_pipeline(single_call=single)
    if not result["success"]:
        sys.exit(1)


def cmd_render_assessment(args):
    from .render import render_assessment_page
    out = getattr(args, "output", None) or str(config.OUTPUT_HTML)
    render_assessment_page(output_path=out)


def _populate_run_log(run_log):
    """Best-effort: fill the run log from the pipeline's just-written outputs so
    run-log.json is a real audit trail (run never fails on a logging hiccup)."""
    from openclaw_status.lib import load_json
    try:
        raw = load_json(config.RAW_DATA_FILE)
        run_log.update(
            source_status=raw.get("source_status", {}),
            pipeline_aborted=bool(raw.get("pipeline_aborted", False)),
            abort_reason=raw.get("abort_reason", ""),
        )
    except Exception:
        pass
    try:
        a = load_json(config.ASSESSMENT_FILE)
        run_log.update(
            cost_usd=(a.get("usage") or {}).get("cost_usd", 0),
            model_used=a.get("primary_model", ""),
            recommendation=(a.get("assessment") or {}).get("recommendation", ""),
            validation_errors=a.get("validation_errors", []),
        )
    except Exception:
        pass


def cmd_notify_test(args):
    """Fire a one-off alert so you can confirm ALERT_WEBHOOK_URL reaches your channel."""
    from .lib import notify
    if not config.ALERT_WEBHOOK_URL:
        print("❌ ALERT_WEBHOOK_URL is not set in .env — add your webhook URL there first.")
        sys.exit(1)
    msg = getattr(args, "message", None) or \
        "✅ OpenClaw Status: test alert — your webhook is wired up correctly."
    print("→ POSTing a test alert to the configured webhook…")
    if notify(msg):
        print("✓ Delivered (HTTP OK). Check your channel for the message.")
    else:
        print("❌ Send failed — re-check the URL (see the warning above).")
        sys.exit(1)


def cmd_notify_failure(args):
    """Fire a failure alert — wired to the systemd unit's OnFailure= so a hard pipeline
    crash (git/collector/timeout) reaches the channel, not just the in-page staleness pill
    after 7 days. Best-effort and always exits 0 so it can't itself fail the failure unit."""
    from .lib import notify
    unit = getattr(args, "unit", None) or "openclaw-status.service"
    notify(f"🛑 OpenClaw Status: pipeline run FAILED ({unit}). The live page is unchanged "
           f"(last good deploy stands). Inspect `journalctl -u {unit}` on the box.")


def cmd_full(args):
    print("\n" + "=" * 60)
    print("OpenClaw Status — Full Pipeline")
    print("=" * 60)

    # Acquire pipeline lock to prevent concurrent runs
    if not acquire_pipeline_lock():
        print("❌ Another pipeline run is active (lock file exists). Aborting.")
        sys.exit(1)

    run_log = RunLog(trigger_type="manual")

    try:
        print("\n[1/3] Collecting data...")
        cmd_collect(args)

        print("\n[2/3] Running assessment...")
        cmd_assess(args)

        print("\n[3/3] Rendering page...")
        cmd_render_assessment(args)

        print("\n✅ Full pipeline complete!")
    finally:
        _populate_run_log(run_log)
        run_log.finish()
        run_log.save()
        release_pipeline_lock()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw Status — release assessment pipeline")
    sub = parser.add_subparsers(dest="command", help="Commands")

    sub.add_parser("collect", help="Run data collection pipeline")
    assess_p = sub.add_parser("assess", help="Run LLM assessment pipeline")
    assess_p.add_argument("--single", action="store_true", help="Single-call mode (skip validator)")
    sub.add_parser("render-assessment", help="Render the public assessment page")
    sub.add_parser("full", help="Full pipeline: collect → assess → render-assessment")
    nt = sub.add_parser("notify-test", help="Send a test alert to ALERT_WEBHOOK_URL")
    nt.add_argument("message", nargs="?", help="Optional custom message to send")
    nf = sub.add_parser("notify-failure", help="Send a pipeline-failure alert (systemd OnFailure=)")
    nf.add_argument("unit", nargs="?", help="Failed unit name (for the message)")

    args = parser.parse_args()

    if not config.OPENROUTER_API_KEY and args.command in ("assess", "full"):
        print("❌ OPENROUTER_API_KEY not set in .env")
        sys.exit(1)

    commands = {
        "collect": cmd_collect,
        "assess": cmd_assess,
        "render-assessment": cmd_render_assessment,
        "full": cmd_full,
        "notify-test": cmd_notify_test,
        "notify-failure": cmd_notify_failure,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
