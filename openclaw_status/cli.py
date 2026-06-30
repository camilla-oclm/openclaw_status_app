#!/usr/bin/env python3
"""
OpenClaw Status — Unified CLI

Usage:
  openclaw-status collect              Run data collection pipeline
  openclaw-status assess               Run LLM assessment pipeline
  openclaw-status assess --single      Single-call mode (skip validator)
  openclaw-status render-assessment    Render the public assessment page
  openclaw-status full                 collect → assess → render-assessment
  openclaw-status tick                 Adaptive scheduler: assess only if due (hourly)
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


def cmd_full(args, trigger="manual"):
    print("\n" + "=" * 60)
    print("OpenClaw Status — Full Pipeline")
    print("=" * 60)

    # Acquire pipeline lock to prevent concurrent runs. Contention is NOT a hard failure
    # (a manual run may be in progress) — return False so a scheduled tick treats it as a
    # benign skip rather than tripping the OnFailure alert.
    if not acquire_pipeline_lock():
        print("❌ Another pipeline run is active (lock held). Aborting.")
        return False

    run_log = RunLog(trigger_type=trigger)

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
    return True


def _parse_dt(s):
    """Parse an ISO timestamp to an aware datetime; None on anything unparseable."""
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _latest_assessed_version():
    """Most recently ASSESSED version (clean, no leading 'v'), or ''.

    Reads assessment.json first — it's written on EVERY completed run, deployable or not —
    so a new release whose assessment came back non-deployable (low-confidence / invalid)
    isn't re-detected as 'new' on every hourly tick, which would bypass the adaptive cadence
    and re-spend the full pipeline each hour. Falls back to history.json (deployable runs only)
    when no assessment.json exists yet.
    """
    from .lib import load_json
    try:
        a = load_json(config.ASSESSMENT_FILE)
        v = str((a or {}).get("version", "") or "")
        if v and v != "unknown":
            return v
    except Exception:
        pass
    try:
        hist = load_json(config.HISTORY_FILE)
    except Exception:
        return ""
    if not isinstance(hist, list) or not hist:
        return ""
    latest = max(hist, key=lambda h: str(h.get("assessed_at", "")))
    return str(latest.get("version", "") or "")


def _last_run_started():
    """Start time of the most recent pipeline run (any outcome) from run-log.json, or None."""
    from .lib import load_json
    try:
        rl = load_json(config.DATA_DIR / "run-log.json")
    except Exception:
        return None
    starts = [_parse_dt(r.get("start_time")) for r in rl] if isinstance(rl, list) else []
    starts = [s for s in starts if s]
    return max(starts) if starts else None


def cmd_tick(args):
    """Adaptive scheduler entry point — run hourly by the systemd timer.

    Cheaply polls for a new release and whether the adaptive cadence is due (one GitHub
    REST call, no LLM), then runs the full pipeline only when warranted. Otherwise it
    exits in seconds with zero LLM cost.
    """
    from datetime import datetime, timezone
    from . import github, scheduler

    now = datetime.now(timezone.utc)
    release = github.latest_release() or {}        # one GitHub REST call, no LLM
    rel_ver = str(release.get("version", "") or "")
    rel_pub = _parse_dt(release.get("published_at"))
    last_assessed = _latest_assessed_version()
    last_run = _last_run_started()

    run, reason = scheduler.should_run(now, rel_pub, rel_ver, last_assessed, last_run)
    print(f"[tick] release={rel_ver or '?'} · last_assessed={last_assessed or 'none'} · {reason}")
    if not run:
        return
    print("[tick] → assessment due; running full pipeline")
    if cmd_full(args, trigger="scheduled") is False:
        print("[tick] a pipeline run is already in progress — skipping (not a failure)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw Status — release assessment pipeline")
    sub = parser.add_subparsers(dest="command", help="Commands")

    sub.add_parser("collect", help="Run data collection pipeline")
    assess_p = sub.add_parser("assess", help="Run LLM assessment pipeline")
    assess_p.add_argument("--single", action="store_true", help="Single-call mode (skip validator)")
    sub.add_parser("render-assessment", help="Render the public assessment page")
    sub.add_parser("full", help="Full pipeline: collect → assess → render-assessment")
    sub.add_parser("tick", help="Adaptive scheduler: run a full assessment only if due (hourly timer)")
    nt = sub.add_parser("notify-test", help="Send a test alert to ALERT_WEBHOOK_URL")
    nt.add_argument("message", nargs="?", help="Optional custom message to send")
    nf = sub.add_parser("notify-failure", help="Send a pipeline-failure alert (systemd OnFailure=)")
    nf.add_argument("unit", nargs="?", help="Failed unit name (for the message)")

    args = parser.parse_args()

    if not config.OPENROUTER_API_KEY and args.command in ("assess", "full", "tick"):
        print("❌ OPENROUTER_API_KEY not set in .env")
        sys.exit(1)

    commands = {
        "collect": cmd_collect,
        "assess": cmd_assess,
        "render-assessment": cmd_render_assessment,
        "full": cmd_full,
        "tick": cmd_tick,
        "notify-test": cmd_notify_test,
        "notify-failure": cmd_notify_failure,
    }

    if args.command in commands:
        result = commands[args.command](args)
        # A manual `full` that couldn't get the lock returns False → exit non-zero.
        if args.command == "full" and result is False:
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
