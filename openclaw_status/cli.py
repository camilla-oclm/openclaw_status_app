#!/usr/bin/env python3
"""
OpenClaw Status — Unified CLI

Usage:
  openclaw-status collect              Run data collection pipeline
  openclaw-status assess               Run LLM assessment pipeline
  openclaw-status assess --single      Single-call mode (skip validator)
  openclaw-status render               Generate findings HTML from raw data
  openclaw-status render-assessment    Inject assessment data into mockup HTML
  openclaw-status compare-models       Run model comparison on current data
  openclaw-status full                 collect → assess → render-assessment
"""

import sys
from pathlib import Path

from openclaw_status import config
from openclaw_status.lib import openrouter_call, load_json, save_json, acquire_pipeline_lock, release_pipeline_lock, RunLog


def cmd_collect(args):
    from .collector import collect
    collect()


def cmd_assess(args):
    from .agent import run_assessment_pipeline
    single = getattr(args, "single", False) or "--single" in sys.argv
    result = run_assessment_pipeline(single_call=single)
    if not result["success"]:
        sys.exit(1)


def cmd_render(args):
    from .render import build_findings_page
    html = build_findings_page()
    out = getattr(args, "output", None) or str(config.FINDINGS_HTML)
    with open(out, "w") as f:
        f.write(html)
    print(f"✅ Generated {out} ({len(html):,} bytes)")


def cmd_render_assessment(args):
    from .render import inject_assessment_into_mockup
    out = getattr(args, "output", None) or str(config.MOCKUP_DIR / "index.html")
    inject_assessment_into_mockup(output_path=out)


def cmd_compare_models(args):
    """Run model comparison test on current raw data."""
    raw = load_json(config.RAW_DATA_FILE)
    sources = raw["sources"]
    release = sources.get("latest_release", {})
    prerelease = sources.get("latest_prerelease", {})
    issues = sources.get("github_issues", [])
    cs = sources.get("clawsweeper", {})
    changelog = sources.get("changelog", "")

    context_parts = []
    context_parts.append(
        f"## Current Version\n{release.get('tag','?')} (released {release.get('published_at','?')[:10]})"
    )
    if prerelease:
        context_parts.append(f"## Pre-release\n{prerelease.get('tag','?')} — fixes pending")
    context_parts.append(f"\n## Open Issues ({len(issues)} total)")
    for i in issues[:10]:
        csd = i.get("clawsweeper", {})
        cs_info = f" [decision: {csd.get('decision','?')}]" if csd else ""
        context_parts.append(f"- #{i['number']} [{i.get('category','?')}] {i['title'][:100]}{cs_info}")
        if i.get("body"):
            context_parts.append(f"  Body: {i['body'][:300]}")
    wc = cs.get("work_candidates", [])
    if wc:
        context_parts.append(f"\n## Clawsweeper Work Candidates ({len(wc)} total)")
        for w in wc[:5]:
            context_parts.append(f"- #{w['number']} [{w.get('priority','?')}] {w['title'][:80]}")
    rc = cs.get("recently_closed", [])
    if rc:
        context_parts.append(f"\n## Recently Closed ({len(rc)} total)")
        for r in rc[:5]:
            context_parts.append(f"- #{r['number']} reason:{r.get('reason','?')} {r['title'][:80]}")
    if changelog:
        context_parts.append(f"\n## Changelog\n{changelog[:1500]}")
    context = "\n".join(context_parts)

    SYSTEM = """You are a software release analyst for OpenClaw. RULES: 1) Cross-reference against real issues. 2) Cite evidence. 3) Rec must be ✅⚠️⏸️🔄. OUTPUT: only JSON — {"recommendation":"...","headline":"...","thesis":"...","confidence":"...","evidence":{"for_updating":[],"against_updating":[],"neutral":[]},"known_issues":[],"sentiment_summary":"..."}"""

    models = [
        {"id": "deepseek/deepseek-v4-flash", "label": "DeepSeek V4 Flash (prod)", "paid": True,
         "reasoning": {"effort": "high"}},
        {"id": "openrouter/owl-alpha", "label": "Owl Alpha (validator)", "paid": False,
         "reasoning": None},
    ]

    print("=" * 60)
    print("OpenClaw Status — Model Comparison Test")
    print("=" * 60)
    print(f"\nContext: {len(context):,} chars | Models: {len(models)}\n")

    results = {}
    for model in models:
        label = model["label"]
        paid = "💰" if model["paid"] else "🆓"
        print(f"{paid} Testing: {label}...")
        result = openrouter_call(
            model["id"], SYSTEM,
            f"Analyze this OpenClaw release data:\n\n{context}",
            reasoning=model.get("reasoning"),
        )
        results[model["id"]] = result
        if result["success"]:
            a = result["parsed"]
            u = result["usage"]
            print(f"   ✅ {a.get('recommendation','?')} ({a.get('confidence','?')}) "
                  f"— {a.get('headline','?')[:60]}")
            print(f"      Tokens: {u['tokens_in']}→{u['tokens_out']} | "
                  f"Cost: ${u['cost_usd']:.4f} | Latency: {u['latency_ms']}ms")
        else:
            print(f"   ❌ {result.get('error','')[:100]}")
        print()

    save_json(config.MODEL_COMPARISON_FILE, results)
    print(f"💾 Saved to: {config.MODEL_COMPARISON_FILE}")


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

        print("\n[3/3] Rendering pages...")
        cmd_render(args)
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
    sub.add_parser("render", help="Generate findings HTML from raw data")
    sub.add_parser("render-assessment", help="Inject assessment data into mockup HTML")
    sub.add_parser("compare-models", help="Run model comparison test")
    sub.add_parser("full", help="Full pipeline: collect → assess → render")

    args = parser.parse_args()

    if not config.OPENROUTER_API_KEY and args.command in ("assess", "compare-models", "full"):
        print("❌ OPENROUTER_API_KEY not set in .env")
        sys.exit(1)

    commands = {
        "collect": cmd_collect,
        "assess": cmd_assess,
        "render": cmd_render,
        "render-assessment": cmd_render_assessment,
        "compare-models": cmd_compare_models,
        "full": cmd_full,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
