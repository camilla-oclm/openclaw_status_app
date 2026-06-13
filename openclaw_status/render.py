"""
Renderer: generates static HTML from collected + assessed data.

- build_findings_page(): a raw, pre-LLM data view (data/findings.html)
- render_assessment_page(): the public assessment page (web/index.html)
"""

import json
import os
import re
import shutil
import tempfile
import html as html_mod
from pathlib import Path

from openclaw_status import config
from openclaw_status.lib import load_json


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
#  Rollback Mechanism
# ═══════════════════════════════════════════════════════════════════════════

def _backup_existing(output_path: str) -> bool:
    """Copy current index.html to index.html.prev before overwriting.

    Returns True if backup was made, False if no existing file to back up.
    """
    p = Path(output_path)
    if p.exists():
        backup = p.with_suffix(".html.prev")
        shutil.copy2(str(p), str(backup))
        print(f"  📦 Backed up existing page to: {backup}")
        return True
    return False


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

#  HTML helpers
# ═══════════════════════════════════════════════════════════════════════════

def _esc(s) -> str:
    return html_mod.escape(str(s)) if s else ""


def _safe_url(url: str) -> str:
    """Validate URL for safe use in href attributes. Blocks javascript: and data: schemes.

    The scheme is checked case-insensitively, but the original-case URL is
    returned so case-sensitive paths (e.g. Reddit permalinks) aren't corrupted.
    """
    if not url:
        return ""
    stripped = url.strip()
    if stripped.lower().startswith(("javascript:", "data:", "vbscript:")):
        return ""
    return stripped


PLATFORM_COLORS = {
    "windows": "#58a6ff", "macos": "#bc8cff", "linux": "#3fb950",
    "discord": "#5865f2", "slack": "#e01e5a", "telegram": "#26a5e4",
    "general": "#8b949e", "version": "#d29922", "unknown": "#8b949e",
}
PLATFORM_ICONS = {
    "windows": "🪟", "macos": "🍎", "linux": "🐧",
    "discord": "💬", "slack": "💼", "telegram": "✈️",
    "general": "📋", "version": "🏷️", "unknown": "❓",
}


def _platform_badge(p: str) -> str:
    c = PLATFORM_COLORS.get(p, "#8b949e")
    ic = PLATFORM_ICONS.get(p, "📋")
    return (
        f'<span style="display:inline-flex;align-items:center;gap:3px;'
        f'padding:1px 8px;border-radius:999px;font-size:0.72rem;font-weight:500;'
        f'background:{c}22;color:{c};border:1px solid {c}44">{ic} {_esc(p)}</span>'
    )


def _score_bar(score):
    if not score:
        return ""
    s = int(score)
    color = "#3fb950" if s > 100 else "#d29922" if s > 20 else "#8b949e"
    return f'<span style="color:{color};font-weight:600;font-size:0.8rem">▲ {s}</span>'


def _plat_table(counts: dict, label: str) -> str:
    if not counts:
        return ""
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:3px 0">'
        f'<span>{_platform_badge(k)}</span><span style="font-weight:600">{v}</span></div>'
        for k, v in sorted(counts.items(), key=lambda x: -x[1])
    )
    return (
        f'<div style="background:#161b22;border:1px solid #30363d;'
        f'border-radius:6px;padding:0.75rem;margin-bottom:0.5rem">'
        f'<div style="font-size:0.75rem;color:#8b949e;text-transform:uppercase;'
        f'margin-bottom:0.5rem">{label}</div>{rows}</div>'
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Section builders
# ═══════════════════════════════════════════════════════════════════════════

def _build_issues_html(issues: list) -> str:
    """Build the GitHub issues table rows."""
    if not issues:
        return '<tr><td colspan="7" style="color:#8b949e;padding:1rem">No issues found</td></tr>'

    html = ""
    for i in issues:
        num = i.get("number", "")
        p = i.get("platform", "general")
        sev = i.get("severity", "high")
        cat = i.get("category", "general")
        cat_icon = {"diamond_lobster": "💎", "regression": "🔄", "active": "🟢"}.get(cat, "📋")
        cat_label = cat.replace("_", " ").title()
        sev_color = "#f85149" if sev == "critical" else "#d29922"
        sev_icon = "🦞" if sev == "critical" else "⚠️"

        label_tags = "".join(
            f'<span style="font-size:0.65rem;padding:1px 5px;border-radius:3px;'
            f'background:#30363d;color:#8b949e;margin-right:2px">{_esc(l)}</span>'
            for l in i.get("labels", [])[:4]
        )

        # Clawsweeper badge
        cs = i.get("clawsweeper")
        cs_html = ""
        if cs:
            decision = cs.get("decision", "?")
            fixed_rel = cs.get("fixed_release", "unknown")
            d_color = "#3fb950" if decision == "close" else "#d29922" if decision == "keep_open" else "#8b949e"
            cs_html = (
                f'<div style="margin-top:4px;font-size:0.72rem">'
                f'<span style="color:{d_color};font-weight:600">{decision}</span> '
            )
            if fixed_rel != "unknown":
                cs_html += f'<span style="color:#3fb950">fixed:{_esc(fixed_rel)}</span> '
            cs_html += "</div>"

        # Comments
        comments_html = ""
        comments_data = i.get("comments_data", [])
        if comments_data:
            comments_html = (
                '<div style="margin-top:6px;padding:6px 8px;background:#1c2129;'
                'border-radius:4px;font-size:0.78rem">'
            )
            for c in comments_data[:3]:
                comments_html += (
                    '<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #30363d">'
                    f'<span style="color:#58a6ff">@{_esc(c.get("author","?"))}</span> '
                    f'<span style="color:#8b949e">{_esc(c.get("created_at","")[:10])}</span><br>'
                    f'<span style="color:#e6edf3">{_esc(c.get("body","")[:200])}</span></div>'
                )
            comments_html += "</div>"

        title = i.get("title", "")
        url = i.get("url", "")

        html += "<tr>"
        html += (
            f'<td style="white-space:nowrap">{sev_icon} '
            f'<a href="{_safe_url(url)}" target="_blank" style="color:#58a6ff;text-decoration:none">'
            f'#{num}</a></td>'
        )
        html += (
            f'<td><a href="{_safe_url(url)}" target="_blank" style="color:#58a6ff;text-decoration:none">'
            f'{_esc(title)}</a>{cs_html}{comments_html}</td>'
        )
        html += f"<td>{_platform_badge(p)}</td>"
        html += f'<td style="font-size:0.8rem;color:#e6edf3">{cat_icon} {cat_label}</td>'
        html += f'<td style="color:{sev_color};font-weight:600;font-size:0.8rem">{sev}</td>'
        html += f'<td style="font-size:0.8rem;color:#8b949e">{i.get("comments",0)} 💬</td>'
        html += f"<td>{label_tags}</td>"
        html += "</tr>"

    return html


def _build_reddit_html(posts: list) -> str:
    """Build Reddit table rows."""
    if not posts:
        return '<tr><td colspan="6" style="color:#8b949e;padding:1rem">No Reddit posts found</td></tr>'

    html = ""
    for r in posts:
        snippet = _esc(r.get("snippet", "")[:150])
        html += (
            f'<tr>'
            f'<td><a href="{_safe_url(r.get("url",""))}" target="_blank" '
            f'style="color:#58a6ff;text-decoration:none">{_esc(r.get("title",""))}</a></td>'
            f'<td>{_platform_badge(r.get("platform","general"))}</td>'
            f'<td>{_score_bar(r.get("score",0))}</td>'
            f'<td style="font-size:0.8rem;color:#8b949e">r/{_esc(r.get("subreddit",""))}</td>'
            f'<td style="font-size:0.8rem;color:#8b949e">{r.get("num_comments",0)} 💬</td>'
            f'<td style="font-size:0.8rem;color:#8b949e;max-width:250px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{snippet}</td>'
            f'</tr>'
        )
    return html


def _build_releases_html(releases_page: str) -> str:
    """Build release history section from Firecrawl markdown."""
    if not releases_page:
        return ""

    sections = re.split(r"(?=## openclaw \d+\.\d+\.\d+)", releases_page)
    html = ""
    for section in sections[1:11]:
        lines = section.strip().split("\n")
        vm = re.search(r"openclaw (\d+\.\d+\.\d+[^\s]*)", lines[0] if lines else "")
        ver = vm.group(1) if vm else "?"
        pr_refs = re.findall(r"#(\d+)", section)

        # Parse highlights
        highlights = []
        in_hi = False
        for line in lines:
            if "### Highlights" in line:
                in_hi = True
                continue
            if in_hi:
                if line.startswith("### ") or line.startswith("## "):
                    break
                if line.strip().startswith("- "):
                    highlights.append(line.strip())

        html += (
            '<div style="margin-bottom:1.25rem;padding:0.75rem;background:#161b22;'
            f'border:1px solid #30363d;border-radius:6px">'
            f'<div style="font-weight:600;font-size:1rem;color:#58a6ff">v{_esc(ver)}</div>'
            f'<div style="font-size:0.75rem;color:#8b949e;margin-bottom:0.5rem">'
            f'{len(pr_refs)} PR references</div>'
        )
        if highlights:
            html += '<div style="margin-top:0.5rem">'
            for h in highlights[:5]:
                clean = re.sub(r"\(\s*#\d+[^)]*\)", "", h).strip()
                clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
                html += (
                    f'<div style="font-size:0.82rem;color:#e6edf3;margin-bottom:0.35rem;'
                    f'padding-left:0.75rem;border-left:2px solid #30363d">{_esc(clean[:200])}</div>'
                )
            html += "</div>"
        html += "</div>"

    return html


def _build_prerelease_html(prerelease: dict, version: str) -> str:
    """Build pre-release section."""
    if not prerelease:
        return '<div style="color:#8b949e;font-size:0.9rem">No pre-release found</div>'

    body = prerelease.get("body", "")
    tag = prerelease.get("tag", "?")
    published = prerelease.get("published_at", "")[:10]

    def _parse_section(md: str, heading: str) -> list:
        items, inside = [], False
        for line in md.split("\n"):
            if heading in line:
                inside = True
                continue
            if inside:
                if line.startswith("### ") or line.startswith("## "):
                    break
                if line.strip().startswith("- "):
                    items.append(line.strip())
        return items

    changes = _parse_section(body, "### Changes")
    highlights = _parse_section(body, "### Highlights")

    html = (
        f'<div style="background:#161b22;border:1px solid #d29922;border-radius:8px;padding:1rem;margin-bottom:1rem">'
        f'<div style="font-weight:600;font-size:1.1rem;color:#d29922">🏷️ {_esc(tag)} '
        f'<span style="font-size:0.8rem;color:#8b949e;font-weight:400">(pre-release, {published})</span></div>'
        f'<div style="font-size:0.8rem;color:#8b949e;margin-top:0.25rem">'
        f'Fixes pending for next stable release — addresses issues in current {_esc(version)}</div>'
    )

    if changes:
        html += (
            '<div style="margin-top:0.75rem">'
            '<div style="font-size:0.8rem;color:#8b949e;text-transform:uppercase;margin-bottom:0.5rem">Changes</div>'
        )
        for c in changes[:10]:
            clean = re.sub(r"\(\s*#\d+[^)]*\)", "", c).strip()
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
            html += (
                f'<div style="font-size:0.82rem;color:#e6edf3;margin-bottom:0.35rem;'
                f'padding-left:0.75rem;border-left:2px solid #d29922">{_esc(clean[:200])}</div>'
            )
        html += "</div>"

    if highlights:
        html += (
            '<div style="margin-top:0.75rem">'
            '<div style="font-size:0.8rem;color:#8b949e;text-transform:uppercase;margin-bottom:0.5rem">Highlights</div>'
        )
        for h in highlights[:5]:
            clean = re.sub(r"\(\s*#\d+[^)]*\)", "", h).strip()
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
            html += (
                f'<div style="font-size:0.82rem;color:#e6edf3;margin-bottom:0.35rem;'
                f'padding-left:0.75rem;border-left:2px solid #30363d">{_esc(clean[:200])}</div>'
            )
        html += "</div>"

    html += "</div>"
    return html


def _build_cs_html(work: list, closed: list) -> str:
    """Build Clawsweeper cards."""

    def _work_items():
        h = ""
        for wc in work[:10]:
            pc = "#f85149" if wc.get("priority") == "high" else "#d29922"
            h += (
                f'<div style="margin-bottom:0.5rem;padding:0.4rem 0.6rem;background:#1c2129;border-radius:4px;'
                f'font-size:0.82rem">'
                f'<a href="https://github.com/openclaw/openclaw/issues/{wc["number"]}" '
                f'target="_blank" style="color:#58a6ff">#{wc["number"]}</a> '
                f'<span style="color:{pc};font-weight:600">{wc.get("priority","?")}</span> '
                f'<span style="color:#8b949e">{_esc(wc.get("decision","?"))}</span> '
                f'<span style="color:#e6edf3">{_esc(wc["title"][:60])}</span></div>'
            )
        return h

    def _closed_items():
        h = ""
        for rc in closed[:10]:
            reason = rc.get("reason", "?")
            rc_color = "#3fb950" if "implemented" in reason else "#8b949e"
            h += (
                f'<div style="margin-bottom:0.5rem;padding:0.4rem 0.6rem;background:#1c2129;border-radius:4px;'
                f'font-size:0.82rem">'
                f'<a href="https://github.com/openclaw/openclaw/issues/{rc["number"]}" '
                f'target="_blank" style="color:#58a6ff">#{rc["number"]}</a> '
                f'<span style="color:{rc_color}">{reason}</span> '
                f'<span style="color:#8b949e">fixed:{_esc(rc.get("fixed_release","?"))}</span> '
                f'<span style="color:#e6edf3">{_esc(rc["title"][:60])}</span></div>'
            )
        return h

    return (
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">'
        '<div class="card">'
        f'<h3 style="color:#58a6ff">Work Candidates ({len(work)})</h3>'
        '<div style="font-size:0.85rem;color:#8b949e;margin-bottom:0.5rem">'
        'Issues/PRs being actively worked on</div>'
        f'{_work_items()}</div>'
        '<div class="card">'
        f'<h3 style="color:#3fb950">Recently Closed ({len(closed)})</h3>'
        '<div style="font-size:0.85rem;color:#8b949e;margin-bottom:0.5rem">'
        'Issues resolved with reason</div>'
        f'{_closed_items()}</div></div>'
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Page builder
# ═══════════════════════════════════════════════════════════════════════════

_CSS = """
:root { --bg:#0d1117; --surface:#161b22; --border:#30363d; --text:#e6edf3;
        --muted:#8b949e; --accent:#58a6ff; --green:#3fb950; --yellow:#d29922; --red:#f85149; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
       background:var(--bg); color:var(--text); line-height:1.6; padding:1.5rem; }
h1 { font-size:1.6rem; margin-bottom:0.25rem; }
h2 { font-size:1.2rem; color:var(--accent); border-bottom:1px solid var(--border);
     padding-bottom:0.4rem; margin-bottom:0.75rem; margin-top:1.5rem; }
.subtitle { color:var(--muted); font-size:0.9rem; margin-bottom:1.5rem; }
.stats { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem; }
.stat { background:var(--surface); border:1px solid var(--border); border-radius:8px;
        padding:0.75rem 1rem; min-width:120px; }
.stat .val { font-size:1.5rem; font-weight:700; }
.stat .label { font-size:0.75rem; color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:0.85rem; }
th { text-align:left; padding:0.5rem; border-bottom:2px solid var(--border);
     color:var(--muted); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em; }
td { padding:0.5rem; border-bottom:1px solid var(--border); vertical-align:top; }
tr:hover { background:#1c2129; }
.changelog { background:var(--surface); border:1px solid var(--border); border-radius:8px;
             padding:1rem; font-size:0.85rem; color:var(--muted); white-space:pre-wrap;
             max-height:400px; overflow-y:auto; }
.filter-bar { display:flex; gap:0.5rem; margin-bottom:0.75rem; flex-wrap:wrap; }
.filter-btn { padding:0.25rem 0.65rem; border-radius:999px; font-size:0.75rem; cursor:pointer;
              border:1px solid var(--border); background:transparent; color:var(--muted); }
.filter-btn:hover, .filter-btn.active { background:var(--accent); color:#fff; border-color:var(--accent); }
a { color:var(--accent); }
.card { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:1rem; }
"""


def build_findings_page(raw: dict = None) -> str:
    """Generate the PRE-LLM findings HTML from raw data."""
    if raw is None:
        raw = load_json(config.RAW_DATA_FILE)

    sources = raw["sources"]
    meta = raw.get("meta", {})
    version = raw.get("target_version", "?")
    collected = raw.get("collected_at", "")
    pc = meta.get("platform_coverage", {})
    issues = sources.get("github_issues", [])
    reddit = sources.get("reddit", [])
    changelog = _esc(sources.get("changelog", "No changelog fetched"))
    releases_page = sources.get("releases_page", "")
    cs = sources.get("clawsweeper", {})
    prerelease = sources.get("latest_prerelease")
    rel = sources.get("latest_release", {})
    npm = sources.get("npm", {})

    issues_html = _build_issues_html(issues)
    reddit_html = _build_reddit_html(reddit)
    releases_html = _build_releases_html(releases_page)
    release_count = len(re.findall(r"(?=## openclaw \d+\.\d+\.\d+)", releases_page))
    prerelease_html = _build_prerelease_html(prerelease, version)
    cs_html = _build_cs_html(cs.get("work_candidates", []), cs.get("recently_closed", []))

    collected_display = collected[:19].replace("T", " ") if collected else "?"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenClaw Status — Findings ({_esc(version)})</title>
<style>{_CSS}</style>
</head>
<body>

<h1>🦞 OpenClaw Status — Findings</h1>
<div style="display:inline-flex;align-items:center;gap:0.5rem;padding:0.4rem 1rem;border-radius:6px;
background:#d2992222;color:#d29922;border:1px solid #d2992244;font-size:0.85rem;font-weight:600;
margin-bottom:0.75rem">⚠️ PRE-LLM — Raw collected data, no assessment yet</div>
<div class="subtitle">
Version <strong>{_esc(version)}</strong> · Collected {collected_display} UTC
· npm: {_esc(npm.get('version','?'))} · Release: {_esc(rel.get('name','?'))}
</div>

<div class="stats">
<div class="stat"><div class="val" style="color:var(--accent)">{len(issues)}</div><div class="label">GitHub Issues</div></div>
<div class="stat"><div class="val" style="color:var(--yellow)">{len(reddit)}</div><div class="label">Reddit Posts</div></div>
<div class="stat"><div class="val">{len(changelog)}</div><div class="label">Changelog Chars</div></div>
<div class="stat"><div class="val" style="color:#bc8cff">{len(releases_page):,}</div><div class="label">Releases Page</div></div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem">
{_plat_table(pc.get('github_issues',{}), 'Issues by Platform')}
{_plat_table(pc.get('reddit',{}), 'Reddit by Platform')}
</div>

<h2>🐛 GitHub Issues ({len(issues)})</h2>
<div class="filter-bar" id="issues-filter">
<button class="filter-btn active" onclick="filterTable('issues','all')">All</button>
<button class="filter-btn" onclick="filterTable('issues','windows')">🪟 Windows</button>
<button class="filter-btn" onclick="filterTable('issues','macos')">🍎 macOS</button>
<button class="filter-btn" onclick="filterTable('issues','linux')">🐧 Linux</button>
<button class="filter-btn" onclick="filterTable('issues','version')">🏷️ Version</button>
<button class="filter-btn" onclick="filterTable('issues','general')">📋 General</button>
</div>
<table id="issues-table"><thead><tr><th>#</th><th>Title</th><th>Platform</th><th>Category</th><th>Severity</th><th>💬</th><th>Labels</th></tr></thead><tbody>{issues_html}</tbody></table>

<h2>💬 Reddit ({len(reddit)})</h2>
<div class="filter-bar" id="reddit-filter">
<button class="filter-btn active" onclick="filterTable('reddit','all')">All</button>
<button class="filter-btn" onclick="filterTable('reddit','windows')">🪟 Windows</button>
<button class="filter-btn" onclick="filterTable('reddit','macos')">🍎 macOS</button>
<button class="filter-btn" onclick="filterTable('reddit','linux')">🐧 Linux</button>
<button class="filter-btn" onclick="filterTable('reddit','discord')">💬 Discord</button>
<button class="filter-btn" onclick="filterTable('reddit','slack')">💼 Slack</button>
<button class="filter-btn" onclick="filterTable('reddit','general')">📋 General</button>
</div>
<table id="reddit-table"><thead><tr><th>Title</th><th>Platform</th><th>Score</th><th>Sub</th><th>💬</th><th>Snippet</th></tr></thead><tbody>{reddit_html}</tbody></table>

<h2>📋 Changelog</h2>
<div class="changelog">{changelog}</div>

<h2 style="margin-top:1.5rem">🏷️ Latest Pre-Release Fixes</h2>
{prerelease_html}

<h2 style="margin-top:1.5rem">🧹 Clawsweeper State</h2>
{cs_html}

<h2 style="margin-top:1.5rem">📦 Release History ({release_count} releases)</h2>
<div class="changelog" style="max-height:600px">{releases_html}</div>

<div style="margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border);
text-align:center;color:var(--muted);font-size:0.8rem">
OpenClaw Status · Findings Viewer · {_esc(version)} · {_esc(collected_display)}
</div>

<script>
function filterTable(table, platform) {{
  const tableId = table === 'issues' ? 'issues-table' : 'reddit-table';
  const filterId = table === 'issues' ? 'issues-filter' : 'reddit-filter';
  const rows = document.querySelectorAll('#' + tableId + ' tbody tr');
  const btns = document.querySelectorAll('#' + filterId + ' .filter-btn');
  btns.forEach(function(b) {{ b.classList.remove('active'); }});
  event.target.classList.add('active');
  rows.forEach(function(row) {{
    if (platform === 'all') {{ row.style.display = ''; return; }}
    var badge = (row.children[colIndex(table)]?.textContent || '').toLowerCase();
    row.style.display = badge.includes(platform) ? '' : 'none';
  }});
}}
function colIndex(table) {{ return table === 'issues' ? 2 : 1; }}
</script>
</body>
</html>"""


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

    # Set can_deploy flag in assessment_raw for downstream
    if assessment_raw is not None:
        assessment_raw["can_deploy"] = True

    print(f"✅ Built assessment page: {out}")
    print(f"   Version: {version}")
    print(f"   Recommendation: {data['recommendation']}")
    print(f"   Known issues: {len(data['known_issues'])}")
    return html
