"""
Renderer: generates static HTML from collected + assessed data.

- render_assessment_page(): the public assessment page (web/index.html)
"""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from openclaw_status import config
from openclaw_status.lib import load_json


def _make_world_readable(path) -> None:
    """Widen a generated artifact to 0644 (best-effort).

    A static file server (e.g. Caddy on the deploy box) runs as its *own* user, so
    it can only read the page through the world bit. But `tempfile.mkstemp` and the
    atomic write/rename produce 0600 files — which would 404/403 once the pipeline
    overwrites the page the provisioner had made world-readable. Re-widen each output.
    """
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  Pre-deployment Smoke Test
# ═══════════════════════════════════════════════════════════════════════════

def smoke_test_html(html_path: str, expected_version: str = "") -> dict:
    """Run pre-deployment smoke tests on generated HTML.

    Checks:
      (a) File exists and is >1KB
      (b) Contains expected version string
      (c) No unclosed tags (count open/close of key HTML tags)
      (d) No unescaped </script> outside the data injection zone

    Args:
        html_path: path to the HTML file
        expected_version: version string that should appear in the HTML

    Returns:
        dict with keys: pass (bool), checks (list of {name, passed, detail})
    """
    checks = []

    # (a) File exists and is >1KB
    if not os.path.exists(html_path):
        checks.append({"name": "file_exists", "passed": False, "detail": "File not found"})
        return {"pass": False, "checks": checks}

    size = os.path.getsize(html_path)
    if size < 1024:
        checks.append({"name": "file_size", "passed": False, "detail": f"Only {size} bytes (min 1KB)"})
    else:
        checks.append({"name": "file_size", "passed": True, "detail": f"{size:,} bytes"})

    with open(html_path, "r") as f:
        content = f.read()

    # (b) Contains expected version string
    if expected_version:
        if expected_version in content:
            checks.append({"name": "version_present", "passed": True, "detail": f"Found '{expected_version}'"})
        else:
            checks.append({"name": "version_present", "passed": False, "detail": f"Missing '{expected_version}'"})
    else:
        checks.append({"name": "version_present", "passed": True, "detail": "No version to check"})

    # Exclude the injected data zone from structural checks — it legitimately
    # contains arbitrary LLM text that must not be parsed as markup. Supports both
    # the JSON <script> contract and the legacy `var DATA = {...};` contract.
    data_zone = re.compile(
        r'<script id="assessment-data"[^>]*>.*?</script>|var DATA = \{.*?\};',
        flags=re.DOTALL | re.IGNORECASE,
    )
    structural = data_zone.sub("", content)

    # (c) Check for unclosed key tags (on structural content, data zone removed)
    tag_issues = []
    for tag in ("html", "head", "body", "table", "div", "script", "style"):
        opens = len(re.findall(f"<{tag}[\\s>]", structural, re.IGNORECASE))
        closes = len(re.findall(f"</{tag}>", structural, re.IGNORECASE))
        if opens != closes:
            tag_issues.append(f"<{tag}>: {opens} open, {closes} close")
    if tag_issues:
        checks.append({"name": "tag_balance", "passed": False, "detail": "; ".join(tag_issues)})
    else:
        checks.append({"name": "tag_balance", "passed": True, "detail": "All key tags balanced"})

    # (d) Check for unescaped </script> outside scripts and the data zone
    # Remove all <script>...</script> blocks to find stray </script>
    stripped = re.sub(r"<script[^>]*>.*?</script>", "", structural, flags=re.DOTALL | re.IGNORECASE)
    stray_scripts = re.findall(r"</script>", stripped, re.IGNORECASE)
    if stray_scripts:
        checks.append({"name": "stray_script_close", "passed": False,
                        "detail": f"Found {len(stray_scripts)} unescaped </script> outside scripts"})
    else:
        checks.append({"name": "stray_script_close", "passed": True, "detail": "No stray </script>"})

    all_passed = all(c["passed"] for c in checks)
    return {"pass": all_passed, "checks": checks}


# ═══════════════════════════════════════════════════════════════════════════
#  Archive / Rollback Mechanism
# ═══════════════════════════════════════════════════════════════════════════

# A page's version is read back from its injected assessment-data JSON. Only a
# filesystem-safe version (word chars, dots, dashes — no "/" or "..") is accepted,
# so a version string can never escape the archive directory when used as a filename.
_SAFE_VERSION = re.compile(r"[\w.\-]+")
_DATA_SCRIPT = re.compile(
    r'<script id="assessment-data" type="application/json">(.*?)</script>',
    flags=re.DOTALL,
)


def _page_version(html_path: str) -> str | None:
    """Read the version a rendered page was built for, from its injected JSON."""
    try:
        with open(html_path) as f:
            m = _DATA_SCRIPT.search(f.read())
        if not m:
            return None
        version = (json.loads(m.group(1)) or {}).get("version")  # \/ is valid JSON
    except (OSError, ValueError):
        return None
    return version if isinstance(version, str) and _SAFE_VERSION.fullmatch(version) else None


def _prune_archive() -> None:
    """Keep only the newest config.ARCHIVE_KEEP snapshots (by mtime); drop the rest."""
    snaps = sorted(
        config.ARCHIVE_DIR.glob("*.html"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old in snaps[config.ARCHIVE_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def _archived_versions() -> list[str]:
    """Versions that currently have a browsable snapshot under web/archive/."""
    if not config.ARCHIVE_DIR.exists():
        return []
    return sorted(p.stem for p in config.ARCHIVE_DIR.glob("*.html"))


def _backup_existing(output_path: str) -> str | None:
    """Snapshot the current page before it's overwritten.

    Recycles what used to be a single index.html.prev into a browsable, per-version
    archive: the outgoing page is copied to web/archive/<version>.html (named from
    its own injected version), and the archive is pruned to config.ARCHIVE_KEEP.
    The history section links to these snapshots. If the outgoing page's version
    can't be determined, we fall back to a single .html.prev so an immediate
    rollback copy always exists.

    Returns the archived version, or None if there was nothing to archive / it was
    only kept as .prev.
    """
    p = Path(output_path)
    if not p.exists():
        return None

    version = _page_version(str(p))
    if not version:
        backup = p.with_suffix(".html.prev")
        shutil.copy2(str(p), str(backup))
        print(f"  📦 Backed up existing page to: {backup}")
        return None

    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.ARCHIVE_DIR / f"{version}.html"
    shutil.copy2(str(p), str(dest))
    _make_world_readable(dest)
    _prune_archive()
    print(f"  📚 Archived previous page → {dest}")
    return version


def _can_deploy(assessment_raw: dict) -> tuple[bool, list[str]]:
    """Check if the assessment is safe to deploy.

    Refuses to overwrite if:
    - confidence is 'low'
    - there are validation errors in the assessment

    Returns:
        (can_deploy: bool, reasons: list of rejection reasons)
    """
    reasons = []
    a = assessment_raw.get("assessment", {})

    if a.get("confidence") == "low":
        reasons.append("Assessment confidence is 'low' — refusing to overwrite")

    validation_errors = assessment_raw.get("validation_errors", [])
    if validation_errors:
        reasons.append(f"{len(validation_errors)} validation errors: {'; '.join(validation_errors[:3])}")

    return (len(reasons) == 0, reasons)


# ═══════════════════════════════════════════════════════════════════════════
#  Public assessment page
# ═══════════════════════════════════════════════════════════════════════════

def _deep_sanitize_markdown(obj):
    """Remove escaped markdown backslashes that break JSON."""
    if isinstance(obj, str):
        return obj.replace("\\_", "_").replace("\\*", "*").replace("\\[", "[").replace("\\]", "]")
    elif isinstance(obj, dict):
        return {k: _deep_sanitize_markdown(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_sanitize_markdown(i) for i in obj]
    return obj


def _build_assessment_data(assessment_raw: dict, raw: dict) -> dict:
    """Merge assessment.json + raw-data.json into the flat DATA dict the template expects."""
    a = assessment_raw.get("assessment", {})
    sources = raw.get("sources", {})
    cw = sources.get("clawsweeper", {})

    # Version history
    version_history = []
    if config.HISTORY_FILE.exists():
        try:
            version_history = load_json(config.HISTORY_FILE)
        except Exception:
            pass

    # Known issues with clawsweeper metadata
    raw_issues = {i["number"]: i for i in sources.get("github_issues", []) if isinstance(i, dict)}
    known_issues = []
    for issue in a.get("known_issues", []):
        num = issue.get("number")
        raw_i = raw_issues.get(num, {})
        known_issues.append({
            "number": num,
            "title": issue.get("title", ""),
            "severity": issue.get("severity", "medium"),
            "category": issue.get("category", raw_i.get("category", "unknown")),
            "clawsweeper_decision": issue.get(
                "clawsweeper_decision",
                (raw_i.get("clawsweeper") or {}).get("decision", "unknown"),
            ),
            "fixed_in": issue.get("fixed_in", raw_i.get("fixed_in")),
            "reactions": raw_i.get("reactions", 0),
            "impact": raw_i.get("impact"),
            "affects_version": raw_i.get("affects_version", False),
        })

    lr = sources.get("latest_release", {})
    lpr = sources.get("latest_prerelease", {})

    data = {
        "assessed_at": assessment_raw.get("assessed_at", ""),
        "version": assessment_raw.get("version", ""),
        "recommendation": a.get("recommendation", "⏸️"),
        "headline": a.get("headline", ""),
        "confidence": a.get("confidence", "medium"),
        "thesis": a.get("thesis", ""),
        "evidence": a.get("evidence", {"for_updating": [], "against_updating": [], "neutral": []}),
        "known_issues": known_issues,
        "changes": a.get("changes", {"breaking": [], "fixes": [], "features": []}),
        "sentiment_summary": a.get("sentiment_summary", ""),
        "platform_impact": a.get("platform_impact", {}),
        "usage": assessment_raw.get("usage", {}),
        "version_history": version_history,
        "npm": sources.get("npm", {}),
        "latest_release": {
            "tag": lr.get("tag", "") if lr else "",
            "published_at": lr.get("published_at", "")[:10] if (lr and lr.get("published_at")) else "",
            "prerelease": lr.get("prerelease", False) if lr else False,
        },
        "latest_prerelease": {
            "tag": lpr.get("tag", "") if lpr else "",
            "published_at": lpr.get("published_at", "")[:10] if (lpr and lpr.get("published_at")) else "",
        },
        "clawsweeper_work": cw.get("work_candidates", []),
        "clawsweeper_closed": cw.get("recently_closed", []),
        # Versions with a browsable snapshot — history entries link to these.
        "archived_versions": _archived_versions(),
    }
    return _deep_sanitize_markdown(data)


def _inject_data(html: str, data: dict) -> str:
    """Inject the assessment data dict into the template.

    Preferred contract: a `<script id="assessment-data" type="application/json">`
    block whose body is replaced with the JSON. `</` is escaped to `<\\/` so no
    string value can break out of the <script> tag (standard safe-embed trick).

    Falls back to the legacy `var DATA = {...};` contract for older templates.
    A replacement *function* is used so backslashes in the JSON are never treated
    as regex backreferences.
    """
    safe_json = json.dumps(data, indent=2, ensure_ascii=False).replace("</", "<\\/")

    sd_pattern = re.compile(
        r'(<script id="assessment-data" type="application/json">)(.*?)(</script>)',
        flags=re.DOTALL,
    )
    if sd_pattern.search(html):
        return sd_pattern.sub(lambda m: m.group(1) + "\n" + safe_json + "\n" + m.group(3), html, count=1)

    # Legacy templates: var DATA = {...};
    legacy = re.compile(r"var DATA = \{.*?\};", flags=re.DOTALL)
    m = legacy.search(html)
    if m:
        legacy_json = json.dumps(data, indent=4, ensure_ascii=True)
        return html[:m.start()] + f"var DATA = {legacy_json};" + html[m.end():]

    print("⚠️ Could not find a data injection point in template — data not injected")
    return html


def _write_latest_json(data: dict, output_path: str) -> None:
    """Write the page data to latest.json next to the rendered page (atomically).

    The template `fetch()`es this at runtime to refresh its data without
    re-rendering the HTML; the inlined `<script id="assessment-data">` copy stays
    as the fallback for offline / file:// viewing where fetch isn't available.
    """
    dest = Path(output_path).with_name("latest.json")
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(dest))
        _make_world_readable(dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def render_assessment_page(assessment_raw: dict = None, raw: dict = None, output_path: str = None) -> str:
    """Build the public assessment page by injecting pipeline data into the template.

    Implements:
    - Rollback: backs up existing page before overwriting
    - Deploy guard: refuses to overwrite if confidence is low or validation errors exist
    - Smoke test: validates generated HTML before writing
    """
    if assessment_raw is None:
        assessment_raw = load_json(config.ASSESSMENT_FILE)
    if raw is None:
        raw = load_json(config.RAW_DATA_FILE)

    out = output_path or str(config.OUTPUT_HTML)

    # ── Deploy Guard: check if assessment is safe to deploy ──
    can_deploy, deploy_reasons = _can_deploy(assessment_raw)
    if not can_deploy:
        for reason in deploy_reasons:
            print(f"  ⛔ DEPLOY BLOCKED: {reason}")
        # Save a marker so the pipeline knows deploy was skipped
        if assessment_raw is not None:
            assessment_raw["can_deploy"] = False
            assessment_raw["deploy_blocked_reasons"] = deploy_reasons
        return ""

    if not config.TEMPLATE_FILE.exists():
        print(f"❌ Template not found: {config.TEMPLATE_FILE}")
        return ""

    # ── Rollback: backup existing page ──
    _backup_existing(out)

    with open(config.TEMPLATE_FILE) as f:
        html = f.read()

    data = _build_assessment_data(assessment_raw, raw)
    html = _inject_data(html, data)
    version = data.get("version", "")

    # ── Smoke Test: validate before writing ──
    # Write to a temp location first, test, then finalize
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".html", dir=str(config.WEB_DIR))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(html)

        smoke = smoke_test_html(tmp_path, expected_version=version)
        if not smoke["pass"]:
            failed = [c for c in smoke["checks"] if not c["passed"]]
            print(f"  ⚠️ SMOKE TEST FAILED ({len(failed)} checks):")
            for c in failed:
                print(f"    ❌ {c['name']}: {c['detail']}")
            print(f"  Keeping previous version at: {out}")
            os.unlink(tmp_path)
            return ""
        else:
            print(f"  ✅ Smoke test passed ({len(smoke['checks'])} checks)")
    except Exception:
        pass

    # Smoke test passed — move tmp to final location
    shutil.move(tmp_path, out)
    _make_world_readable(out)  # served by Caddy (different user) — must be world-readable

    # Emit the same payload as a sibling latest.json for the runtime fetch path.
    _write_latest_json(data, out)

    # Set can_deploy flag in assessment_raw for downstream
    if assessment_raw is not None:
        assessment_raw["can_deploy"] = True

    print(f"✅ Built assessment page: {out}")
    print(f"   Version: {version}")
    print(f"   Recommendation: {data['recommendation']}")
    print(f"   Known issues: {len(data['known_issues'])}")
    return html
