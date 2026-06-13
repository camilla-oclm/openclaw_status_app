#!/usr/bin/env python3
"""
OpenClaw Status — Unified CLI

Usage:
  openclaw-status collect              Run data collection pipeline
  openclaw-status assess               Run LLM assessment pipeline
  openclaw-status assess --single      Single-call mode (skip validator)
  openclaw-status render-assessment    Render the public assessment page
  openclaw-status full                 collect → assess → render-assessment
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

        run_log.save()
        print("\n✅ Full pipeline complete!")
    finally:
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

    args = parser.parse_args()

    if not config.OPENROUTER_API_KEY and args.command in ("assess", "full"):
        print("❌ OPENROUTER_API_KEY not set in .env")
        sys.exit(1)

    commands = {
        "collect": cmd_collect,
        "assess": cmd_assess,
        "render-assessment": cmd_render_assessment,
        "full": cmd_full,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
