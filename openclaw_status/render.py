"""
Renderer: generates static HTML from collected + assessed data.

- render_assessment_page(): the public assessment page (web/index.html)
"""

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
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


def _self_canonicalize(path: Path, version: str) -> None:
    """Re-point an archived snapshot's canonical + og:url at its own archive URL.

    Snapshots are frozen copies of a past homepage, so they inherit `canonical → /`.
    Left as-is, Google treats every archived version as a duplicate of the homepage
    and won't index it. Pointing each past-version page at itself lets it rank for
    its own "openclaw vX …" queries (a growing library of long-tail pages)."""
    site = config.SITE_URL.rstrip("/")
    url = f"{site}/archive/{version}.html"
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return
    html = re.sub(r'(<link rel="canonical" href=")[^"]*(">)',
                  lambda m: m.group(1) + url + m.group(2), html, count=1)
    html = re.sub(r'(<meta property="og:url" content=")[^"]*(">)',
                  lambda m: m.group(1) + url + m.group(2), html, count=1)
    path.write_text(html, encoding="utf-8")


def _backup_existing(output_path: str, new_version: str = "") -> str | None:
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
    # A snapshot of the version we're *re-rendering* is identical to the new homepage,
    # so it keeps canonical → "/" (avoid a duplicate). A snapshot of a now-superseded
    # version self-canonicalises so it can be indexed on its own.
    if version != new_version:
        _self_canonicalize(dest, version)
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


_HIGHLIGHTS_RE = re.compile(r"###?\s*Highlights\s*\n(.*?)(?=\n###? |\Z)", re.DOTALL | re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)", re.MULTILINE)


def _extract_highlights(body: str, limit: int = 6) -> list:
    """Pull the '### Highlights' bullets from a release changelog body (best-effort).

    Powers the "catching up from an older version" changelog: each past release
    contributes a few headline bullets, aggregated client-side across the span.
    """
    if not body:
        return []
    m = _HIGHLIGHTS_RE.search(body)
    section = m.group(1) if m else body
    out = []
    for b in _BULLET_RE.findall(section):
        b = re.sub(r"\s+", " ", b).strip()
        if b:
            if len(b) > 240:
                # Cut on a word boundary (not mid-word) and signal there's more.
                b = b[:240].rsplit(" ", 1)[0].rstrip(" ,.;:—-") + "…"
            out.append(b)
        if len(out) >= limit:
            break
    return out


_PLATFORM_KEYS = {"windows", "macos", "linux", "discord", "slack", "telegram", "all"}
_PLATFORM_ALIASES = {"win": "windows", "win32": "windows", "mac": "macos",
                     "osx": "macos", "mac os": "macos", "darwin": "macos"}


def _norm_platforms(value) -> list:
    """Normalize the analyst's per-issue `platforms` to known tokens (drop junk)."""
    if not isinstance(value, list):
        return []
    out = []
    for p in value:
        t = str(p or "").strip().lower()
        t = _PLATFORM_ALIASES.get(t, t)
        if t in _PLATFORM_KEYS and t not in out:
            out.append(t)
    return out


# Deterministic platform derivation from an issue's text (title + body + labels) — the
# always-on baseline that doesn't depend on the LLM (and survives a cheap re-render).
# Every token is \b-anchored so a signal only fires on a whole word, never as a
# substring of a larger one: `.exe` must be a file extension, not the `.exe` inside
# `tools.exec`; `win` must be `win32`, not `winner`; etc. (regression: issue #92843, a
# macOS report, was mis-tagged Windows because `\.exe` matched `tools.exec.security`).
_PLATFORM_SIGNALS = {
    "windows": r"\bwindows\b|\bwin32\b|\.exe\b|\bpowershell\b|\bwsl\b",
    "macos": r"\bmacos\b|\bmac os\b|\bosx\b|\bdarwin\b|\bimessage\b|\bapple\b",
    "linux": r"\blinux\b|\bdocker\b|\bcontainer\b|\bsystemd\b|\bubuntu\b|\bdebian\b|\bcgroup\b|\bself-hosted\b|\bkubernetes\b|\bk8s\b",
    "discord": r"\bdiscord\b",
    "slack": r"\bslack\b",
    "telegram": r"\btelegram\b",
}
_CORE_SIGNAL = re.compile(
    r"\b(build|compile|memory|index|reindex|engine|session|auth|gateway|database|migration|startup|worker|core)\b",
    re.IGNORECASE,
)
# A serious core regression that names a *channel* surface isn't "all platforms".
_CHANNEL_SIGNAL = re.compile(r"msteams|teams|wechat|whatsapp|signal|matrix|imessage|channel|plugin", re.IGNORECASE)


def _derive_platforms(raw_issue: dict, severity=None, category=None) -> list:
    """Heuristic surfaces an issue hits, from its title + body + labels. Specific
    platforms by keyword; a serious core regression that names no surface → ["all"]."""
    if not isinstance(raw_issue, dict):
        return []
    labels = raw_issue.get("labels") or []
    label_text = " ".join((l.get("name", "") if isinstance(l, dict) else str(l)) for l in labels)
    text = " ".join([
        str(raw_issue.get("title", "")),
        str(raw_issue.get("body", ""))[:600],
        label_text,
    ]).lower()
    found = [k for k, pat in _PLATFORM_SIGNALS.items() if re.search(pat, text)]
    if found:
        return found
    sev = str(severity or raw_issue.get("severity", "")).lower()
    cat = str(category or raw_issue.get("category", "")).lower()
    if (cat in ("regression", "post_release") or sev in ("critical", "high")) \
            and _CORE_SIGNAL.search(text) and not _CHANNEL_SIGNAL.search(text):
        return ["all"]
    return []


_PLATFORM_IMPACT_KEYS = ("windows", "macos", "linux", "discord", "slack", "telegram")
_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _derive_platform_impact(known_issues: list) -> dict:
    """Per-platform impact level derived from the issues' resolved `platforms` tags.
    A platform's level is the worst severity among the issues that hit it (its own tag
    or a cross-cutting "all"). Evidence-grounded — supersedes the analyst's free-text
    `platform_impact`, which tends to saturate at "high". Returns {} when no issue
    carries any platform tag, so the caller can fall back to the analyst's value."""
    issues = known_issues or []
    if not any((i.get("platforms") or []) for i in issues):
        return {}
    out = {}
    for k in _PLATFORM_IMPACT_KEYS:
        worst = 0
        for i in issues:
            plats = [str(p).lower() for p in (i.get("platforms") or [])]
            if k in plats or "all" in plats:
                worst = max(worst, _SEVERITY_WEIGHT.get(str(i.get("severity", "")).lower(), 0))
        if worst >= 3:
            out[k] = "high"
        elif worst == 2:
            out[k] = "medium"
        elif worst == 1:
            out[k] = "low"
    return out


# ── Component (subsystem) taxonomy — *what part of OpenClaw* an issue touches ──
# Orthogonal to platforms ("where it runs / who's hit"). Repo labels are
# authoritative; a keyword pass over title+body+labels is the fallback.
_COMPONENT_KEYS = ["gateway", "models", "memory", "sessions", "auth", "channels",
                   "plugins", "agents", "tasks", "tools", "build"]
# Priority order when an issue matches several (we cap the pills). Harm-area auth
# first, then concrete subsystems, then the broad/label-driven channels/models/
# sessions last so a distinctive match (cron→tasks, gateway, memory) isn't crowded out.
_COMPONENT_ORDER = ["auth", "memory", "gateway", "tasks", "agents", "plugins",
                    "tools", "build", "channels", "models", "sessions"]
_COMPONENT_LABELS = {                       # maintainer-assigned repo labels → component
    "impact:session-state": "sessions",
    "impact:auth-provider": "auth",
    "impact:security": "auth",
    "impact:message-loss": "channels",
}
_COMPONENT_SIGNALS = {                       # keyword fallback, scanned over the title
    "gateway":  r"gateway|\bworker\b|bootstrap|daemon|supervisor",
    "models":   r"\bmodels?\b|prompt cache|model fallback|adapters?|deepseek|openai|anthropic|qwen|minimax|inference|llm request",
    "memory":   r"\bmemory\b|reindex|\bindex(ing|ed)?\b|embedding|vector store|knowledge base",
    "sessions": r"\bsessions?\b|conversation|context window",
    "auth":     r"\bauth\b|oauth|credential|trust gate|keyed-?store|api key|\blogin\b|permission",
    "channels": r"discord|slack|telegram|whatsapp|wechat|feishu|msteams|\bteams\b|matrix|imessage|\bchannels?\b|dispatch|deliver(y)?|webhook|inbound|outbound",
    "plugins":  r"\bplugins?\b|clawhub|\bmcp\b|\bskills?\b|extensions?|marketplace|catalog",
    "agents":   r"subagent|sub-agent|\bspawn\b|depth-?\d|orchestrat|\bdelegate\b",
    "tasks":    r"\bcron\b|scheduler|scheduled|\bjob\b|task queue|tasks? audit",
    "tools":    r"tool\.call|tool[- ]call|tool dispatch|tool result|function call",
    "build":    r"\bbuild\b|compile|docker|container|self-hosted|kubernetes|\bk8s\b|deploy|provision",
}
_COMPONENT_SIGNALS = {k: re.compile(v, re.IGNORECASE) for k, v in _COMPONENT_SIGNALS.items()}


def _norm_components(value) -> list:
    """Normalize an analyst-supplied `components` list to known keys (drop junk)."""
    if not isinstance(value, list):
        return []
    out = []
    for c in value:
        t = str(c or "").strip().lower()
        if t in _COMPONENT_KEYS and t not in out:
            out.append(t)
    return out


def _derive_components(raw_issue: dict, max_n: int = 2) -> list:
    """The subsystem(s) an issue touches, from labels + title/body keywords.
    Capped at `max_n`, ordered by _COMPONENT_ORDER so pills stay legible."""
    if not isinstance(raw_issue, dict):
        return []
    labels = raw_issue.get("labels") or []
    label_names = [(l.get("name", "") if isinstance(l, dict) else str(l)).lower() for l in labels]
    found = set()
    for ln in label_names:                       # labels are authoritative
        if ln in _COMPONENT_LABELS:
            found.add(_COMPONENT_LABELS[ln])
    # Keyword scan on the TITLE only — the body is too noisy (generic "session",
    # "model", "provider" words) and label text would double-trigger keywords.
    title = str(raw_issue.get("title", "")).lower()
    for key, pat in _COMPONENT_SIGNALS.items():
        if pat.search(title):
            found.add(key)
    return [c for c in _COMPONENT_ORDER if c in found][:max_n]


def _timeline_from_history(h: dict) -> dict:
    """Map a per-version history row to a timeline row (coarse fallback before the real
    per-run series accumulates). history stores `high` as high+critical combined and has
    no medium/low split, so we approximate: high band = `high`, the rest → low."""
    issues = h.get("issues", 0) or 0
    sev_hi = h.get("high", 0) or 0
    return {
        "t": h.get("assessed_at", ""),
        "version": h.get("version", ""),
        "recommendation": _norm_rec(h.get("recommendation", "?")),
        "confidence": h.get("confidence", "medium"),
        "issues": issues,
        "regressions": h.get("regressions", 0) or 0,
        "critical": 0, "high": sev_hi, "medium": 0, "low": max(0, issues - sev_hi),
        "cost_usd": round(h.get("cost_usd", 0) or 0, 6),
        "latency_ms": 0,
        "approx": True,   # flags the coarse per-version fallback (no per-run granularity yet)
    }


_WORKAROUND_RE = re.compile(
    r"\b(work[\s-]?around|mitigat(?:e|ed|ion)|temporary fix|temp fix|stopgap)\b", re.I)


def _has_workaround(raw_i: dict) -> bool:
    """Best-effort: does the issue's own text mention a workaround/mitigation?

    Only the issue title + (truncated) body are available — comments come back as a
    count, not text — so this is a sparse, honest signal: it flags reports that *note*
    a workaround, not a guarantee that one exists or is official."""
    if not isinstance(raw_i, dict):
        return False
    return bool(_WORKAROUND_RE.search(f"{raw_i.get('title','')} {raw_i.get('body','')}"))


def _days_between(later_iso: str, earlier_iso: str):
    """Whole days from `earlier_iso` to `later_iso` (date-only). None if unparseable.

    Negative spans clamp to 0 (a release "published in the future" relative to the
    assessment clock is treated as same-day, not negative)."""
    def _date(s):
        try:
            return datetime.fromisoformat(str(s)[:10]).date()
        except (ValueError, TypeError):
            return None
    a, b = _date(later_iso), _date(earlier_iso)
    if a is None or b is None:
        return None
    return max((a - b).days, 0)


def _within_fresh_window(version: str, assessed_at: str, latest_release: dict) -> bool:
    """The publish-date 'early-read window': this IS the latest release AND it was published
    within `config.FRESH_RELEASE_DAYS` of the assessment. The assessment pipeline caps a fresh
    release's confidence to 'medium' for this whole window (a release <2 days old shouldn't read
    'high'). `_release_freshness` builds on this for the page banner but ALSO retires the banner
    after `FRESH_RELEASE_MAX_RUNS` runs — so confidence can stay capped slightly longer than the
    banner is shown."""
    rel_ver = (latest_release.get("tag", "") or "").lstrip("v")
    days = _days_between(assessed_at, latest_release.get("published_at", ""))
    return bool(version and rel_ver == version and days is not None
                and days <= config.FRESH_RELEASE_DAYS)


def _release_freshness(version: str, assessed_at: str, latest_release: dict,
                       known_issues: list, run_count: int = 0) -> dict:
    """Is this a just-dropped release we don't have version-specific data on yet?

    A fresh release matches the assessed version AND is still early — early meaning
    BOTH published within `config.FRESH_RELEASE_DAYS` AND assessed no more than
    `config.FRESH_RELEASE_MAX_RUNS` times. The verdict then leans on issues carried
    over from earlier versions rather than reports filed against *this* one, so the
    page tells users to back up and treat the early verdict as preliminary (sparse
    early data is a community-reporting lag, not a model error).

    Whichever gate trips first retires the banner. The run-count gate is the real
    signal of "enough data in hand": by the 4th run (~24h at the 6h cadence) the
    community has filed version-specific bugs, so the banner hides even though the
    publish date is still < 2 days old. `run_count` = times this version has been
    assessed so far (incl. this run); 0 = unknown, so it never retires the banner."""
    days = _days_between(assessed_at, latest_release.get("published_at", ""))
    runs_exhausted = run_count > config.FRESH_RELEASE_MAX_RUNS
    fresh = bool(_within_fresh_window(version, assessed_at, latest_release) and not runs_exhausted)
    # Of the issues we *are* showing, how many actually name this release vs. are
    # carried over from prior versions — drives the honest "N mention this version".
    specific = sum(1 for i in known_issues if i.get("affects_version"))
    return {
        "fresh": fresh,
        "days_since_release": days,
        "runs_assessed": run_count,
        "version_specific_issues": specific,
        "carried_over_issues": max(len(known_issues) - specific, 0),
    }


# Bump when the public latest.json shape changes in a breaking way (field removed /
# renamed / retyped). Additive fields don't require a bump.
SCHEMA_VERSION = 1


def _build_assessment_data(assessment_raw: dict, raw: dict) -> dict:
    """Merge assessment.json + raw-data.json into the flat DATA dict the template expects."""
    a = assessment_raw.get("assessment", {})
    sources = raw.get("sources", {})
    cw = sources.get("clawsweeper", {})

    # Version history
    raw_history = []
    if config.HISTORY_FILE.exists():
        try:
            loaded = load_json(config.HISTORY_FILE)
            raw_history = [h for h in loaded if isinstance(h, dict)] if isinstance(loaded, list) else []
        except Exception:
            raw_history = []
    # Run cost is internal — keep it out of the public per-version payload.
    version_history = [{**{k: v for k, v in h.items() if k != "cost_usd"},
                        "recommendation": _norm_rec(h.get("recommendation", "?"))}
                       for h in raw_history]

    # Per-run metric time series for the Trends charts (cost & latency are stripped below
    # — they're internal pipeline metrics, kept on disk but never shipped to the page).
    timeline = []
    # How many times THIS version has been assessed (counts the current run, which the
    # assess step already appended to timeline.json before render). Gates the
    # fresh-release banner — see _release_freshness / config.FRESH_RELEASE_MAX_RUNS.
    version_run_count = 0
    if config.TIMELINE_FILE.exists():
        try:
            raw_tl = load_json(config.TIMELINE_FILE)
            if isinstance(raw_tl, list):
                rows = [r for r in raw_tl if isinstance(r, dict)]
                timeline = rows[-config.TIMELINE_KEEP:]
                version_run_count = sum(
                    1 for r in rows if r.get("version") == assessment_raw.get("version", ""))
        except Exception:
            timeline = []
    # Until the real per-run series has ≥2 points, synthesize a coarse per-version
    # fallback from history so the charts aren't empty (one point per release).
    if len(timeline) < 2 and raw_history:
        timeline = [_timeline_from_history(h) for h in raw_history]
    # Cost & latency are internal pipeline metrics — kept on disk (timeline.json), but
    # never shipped in the public payload (page source / latest.json).
    # Strip internal cost/latency, and normalize any retired 🔄 (older per-run
    # snapshots predate the 3-verdict rubric) so the Trends chart never plots an
    # unknown verdict.
    timeline = [{**{k: v for k, v in r.items() if k not in ("cost_usd", "latency_ms")},
                 **({"recommendation": _norm_rec(r["recommendation"])} if "recommendation" in r else {})}
                for r in timeline]

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
            # Surfaces this issue hits: the analyst's tags if it emitted any, else a
            # deterministic derivation from the issue's title/body/labels.
            "platforms": _norm_platforms(issue.get("platforms"))
                         or _derive_platforms(raw_i, issue.get("severity"), issue.get("category")),
            # Subsystem(s) this issue touches (orthogonal facet) — analyst tags ∥ derivation.
            "components": _norm_components(issue.get("components")) or _derive_components(raw_i),
            # Ledger-derived: "new since last run" badge + issue age.
            "is_new": bool(issue.get("is_new")),
            "first_seen": issue.get("first_seen") or raw_i.get("first_seen"),
            # Sparse, honest signal — the report's text mentions a workaround/mitigation.
            "has_workaround": _has_workaround(raw_i),
        })

    lr = sources.get("latest_release", {})
    lpr = sources.get("latest_prerelease", {})

    data = {
        # `impact` (per known-issue) is a community-engagement bucket from 👍 + comment
        # volume — NOT a second severity axis; it intentionally differs from `severity`.
        "schema_version": SCHEMA_VERSION,
        "assessed_at": assessment_raw.get("assessed_at", ""),
        "version": assessment_raw.get("version", ""),
        "recommendation": _norm_rec(a.get("recommendation", "⏸️")),
        "headline": a.get("headline", ""),
        "confidence": a.get("confidence", "medium"),
        "thesis": a.get("thesis", ""),
        "evidence": a.get("evidence", {"for_updating": [], "against_updating": [], "neutral": []}),
        "known_issues": known_issues,
        "changes": a.get("changes", {"breaking": [], "fixes": [], "features": []}),
        "sentiment_summary": a.get("sentiment_summary", ""),
        # Evidence-grounded per-platform impact (worst severity per surface from the
        # issue tags); the analyst's free-text value is only a fallback (it saturates).
        "platform_impact": _derive_platform_impact(known_issues) or a.get("platform_impact", {}),
        # Token counts + model-call count are surfaced (they evidence the real multi-model
        # pipeline); cost and latency are internal — kept off the public payload.
        "usage": {k: v for k, v in (assessment_raw.get("usage") or {}).items()
                  if k not in ("cost_usd", "latency_ms")},
        "version_history": version_history,
        "timeline": timeline,
        # Stable-release changelog (newest first) with extracted Highlights — the
        # "catching up from an older version" section aggregates these client-side.
        "release_history": [
            {
                "tag": r.get("tag", ""),
                "version": (r.get("tag", "") or "").lstrip("v"),
                "published_at": (r.get("published_at", "") or "")[:10],
                "prerelease": bool(r.get("prerelease")),
                "highlights": _extract_highlights(r.get("body", "") or ""),
            }
            for r in (sources.get("release_history") or [])
            if not r.get("prerelease")
        ],
        "npm": sources.get("npm", {}),
        "latest_release": {
            "tag": lr.get("tag", "") if lr else "",
            "version": (lr.get("version") or (lr.get("tag", "") or "").lstrip("v")) if lr else "",
            "url": lr.get("url", "") if lr else "",
            "published_at": lr.get("published_at", "")[:10] if (lr and lr.get("published_at")) else "",
            "prerelease": lr.get("prerelease", False) if lr else False,
        },
        "latest_prerelease": {
            "tag": lpr.get("tag", "") if lpr else "",
            "url": lpr.get("url", "") if lpr else "",
            "published_at": lpr.get("published_at", "")[:10] if (lpr and lpr.get("published_at")) else "",
        },
        "clawsweeper_work": cw.get("work_candidates", []),
        "clawsweeper_closed": cw.get("recently_closed", []),
        # Versions with a browsable snapshot — history entries link to these.
        "archived_versions": _archived_versions(),
        # "Just dropped" signal: a fresh release has little version-specific evidence
        # yet, so the page flags the early verdict as preliminary + says to back up.
        "freshness": _release_freshness(
            assessment_raw.get("version", ""),
            assessment_raw.get("assessed_at", ""),
            {"tag": lr.get("tag", "") if lr else "",
             "published_at": lr.get("published_at", "") if lr else ""},
            known_issues,
            version_run_count,
        ),
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
        # Escape "</" exactly like the preferred path so no string value can close the
        # <script> early (e.g. a thesis containing "</script>").
        legacy_json = json.dumps(data, indent=4, ensure_ascii=True).replace("</", "<\\/")
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


# ═══════════════════════════════════════════════════════════════════════════
#  Shareable artifacts: RSS feed + embeddable status badge
# ═══════════════════════════════════════════════════════════════════════════

# (short message, shields-style colour) per verdict — used by the feed + badge.
def _norm_rec(rec: str) -> str:
    """Map the retired 🔄 "wait for next release" verdict onto ⏸️ for display, so
    old history entries (or any stray value) render with a known 3-verdict label
    instead of an orphaned glyph / a broken risk bar."""
    return "⏸️" if rec == "🔄" else rec


_VERDICT_TEXT = {
    "✅": ("update now", "#4c1"),
    "⚠️": ("update with care", "#dfb317"),
    "⏸️": ("skip this version", "#e05d44"),
}


def _xml_escape(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _rfc822(iso: str) -> str:
    """ISO timestamp → RFC-822 date for RSS <pubDate> (best-effort)."""
    if not iso:
        return ""
    try:
        from email.utils import format_datetime
        return format_datetime(datetime.fromisoformat(iso))
    except Exception:
        return ""


def _atomic_write_text(dest: Path, text: str) -> None:
    """Write text atomically and make it world-readable (Caddy serves it)."""
    fd, tmp = tempfile.mkstemp(suffix=dest.suffix, dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, str(dest))
        _make_world_readable(dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_feed(data: dict, output_path: str) -> None:
    """RSS 2.0 feed of verdicts (one item per tracked version) → web/feed.xml."""
    dest = Path(output_path).with_name("feed.xml")
    site = config.SITE_URL.rstrip("/")
    archived = set(data.get("archived_versions") or [])
    cur = data.get("version", "")
    hist = sorted(data.get("version_history", []) or [],
                  key=lambda e: str(e.get("assessed_at", "")), reverse=True)
    items = []
    for e in hist[:20]:
        ver = e.get("version", "")
        label = _VERDICT_TEXT.get(e.get("recommendation", ""), ("assessed", ""))[0]
        # Make every item individually addressable: current → the live homepage; a past
        # version we snapshotted → its archive page; otherwise → its GitHub release tag
        # (better than dumping every old item on the homepage).
        if ver and ver == cur:
            link = site + "/"
        elif ver in archived:
            link = f"{site}/archive/{ver}.html"
        elif ver:
            link = f"https://github.com/openclaw/openclaw/releases/tag/v{ver}"
        else:
            link = site + "/"
        pub = _rfc822(e.get("assessed_at", ""))
        items.append(
            "    <item>\n"
            f"      <title>{_xml_escape(f'OpenClaw v{ver}: {label}')}</title>\n"
            f"      <link>{_xml_escape(link)}</link>\n"
            f"      <guid isPermaLink=\"false\">v{_xml_escape(ver)}-{_xml_escape(str(e.get('assessed_at',''))[:19])}</guid>\n"
            + (f"      <pubDate>{pub}</pubDate>\n" if pub else "")
            + f"      <description>{_xml_escape(e.get('headline') or e.get('reason') or '')}</description>\n"
            "    </item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n  <channel>\n'
        "    <title>OpenClaw Status — should you update?</title>\n"
        f"    <link>{_xml_escape(site)}/</link>\n"
        "    <description>A verdict on every OpenClaw release: update now, update with care, "
        "or skip this version.</description>\n"
        + ("\n".join(items) + "\n" if items else "")
        + "  </channel>\n</rss>\n"
    )
    _atomic_write_text(dest, xml)


def _badge_svg(label: str, message: str, color: str) -> str:
    """A self-contained shields-style SVG badge (no external dependency).

    Carries the ClawStat mark (white, mono) on the left of the label segment.
    """
    def w(s):
        return int(len(s) * 6.6) + 12
    logo_w = 22                          # left gutter for the ClawStat mark
    tlw, mw = w(label), w(message)
    lw = logo_w + tlw                    # label (left) segment, logo + text
    total = lw + mw
    lx, mx = logo_w + tlw / 2, lw + mw / 2
    le, me = _xml_escape(label), _xml_escape(message)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{le}: {me}">\n'
        f'<title>{le}: {me}</title>\n'
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>\n'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>\n'
        '<g clip-path="url(#r)">\n'
        f'<rect width="{lw}" height="20" fill="#444"/>\n'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{color}"/>\n'
        f'<rect width="{total}" height="20" fill="url(#s)"/>\n'
        '</g>\n'
        '<g transform="translate(5,3) scale(0.14)">\n'
        '<path d="M73 30.7 A30 30 0 1 0 73 69.3" fill="none" stroke="#fff" stroke-width="13" stroke-linecap="round"/>\n'
        '<circle cx="50" cy="50" r="8" fill="#fff"/>\n'
        '</g>\n'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">\n'
        f'<text x="{lx:.0f}" y="15" fill="#010101" fill-opacity=".3">{le}</text>\n'
        f'<text x="{lx:.0f}" y="14">{le}</text>\n'
        f'<text x="{mx:.0f}" y="15" fill="#010101" fill-opacity=".3">{me}</text>\n'
        f'<text x="{mx:.0f}" y="14">{me}</text>\n'
        '</g>\n</svg>\n'
    )


def _write_badge(data: dict, output_path: str) -> None:
    """Embeddable SVG status badge → web/badge.svg."""
    dest = Path(output_path).with_name("badge.svg")
    ver = data.get("version", "")
    label = f"OpenClaw v{ver}" if ver else "OpenClaw"
    msg, color = _VERDICT_TEXT.get(data.get("recommendation", ""), ("assessed", "#6e7681"))
    _atomic_write_text(dest, _badge_svg(label, msg, color))


# ═══════════════════════════════════════════════════════════════════════════
#  Agent-readable layer: llms.txt + a full markdown mirror of the verdict
#  (the LLM/agent counterpart to the human page — same data, no HTML/JS to parse)
# ═══════════════════════════════════════════════════════════════════════════

# Human-readable verdict labels (match the page's VERDICTS map).
_VERDICT_LABEL = {
    "✅": "Update now",
    "⚠️": "Update with precautions",
    "⏸️": "Skip this version",
}


def _verdict_phrase(rec: str) -> str:
    return _VERDICT_LABEL.get(rec, "Assessed")


def _md_issue_line(i: dict) -> str:
    """One known-issue as a markdown bullet: `- #N [sev · category · status] title — 👍`."""
    cat = (i.get("category") or "").strip()
    if i.get("fixed_in"):
        status = f"fixed in {i['fixed_in']}"
    elif cat == "regression":
        status = "unfixed"
    else:
        status = "open"
    meta = " · ".join(x for x in [i.get("severity") or "?", cat, status] if x)
    line = f"- #{i.get('number')} [{meta}] {(i.get('title') or '').strip()}"
    if i.get("reactions"):
        line += f" — {i['reactions']} 👍"
    return line


def _llms_txt(data: dict) -> str:
    """Concise /llms.txt (llmstxt.org): what the site is + the current verdict + resource links."""
    site = config.SITE_URL.rstrip("/")
    ver = data.get("version", "")
    rec = data.get("recommendation", "")
    L = [
        "# ClawStat.us",
        "",
        "> Should you update to the latest OpenClaw release? ClawStat.us publishes an automated, "
        "evidence-backed verdict for each OpenClaw release — scouted from post-release bug reports "
        "and weighed by an independent multi-model LLM pipeline.",
        "",
        "_Independent, unofficial project — not affiliated with or endorsed by OpenClaw or its "
        "maintainers. Verdicts are generated automatically and may be wrong; confirm against the "
        "linked issues before updating._",
        "",
        "## Current verdict",
        "",
        f"- Subject: OpenClaw v{ver}",
        f"- Recommendation: {rec} {_verdict_phrase(rec)}",
        f"- Confidence: {data.get('confidence', '')}",
        f"- Assessed: {(data.get('assessed_at', '') or '')[:10]}",
    ]
    if data.get("headline"):
        L.append(f"- Summary: {data['headline'].strip()}")
    fr = data.get("freshness") or {}
    if fr.get("fresh"):
        spec = fr.get("version_specific_issues") or 0
        L.append(f"- Note: Fresh release — early read. {spec} issue(s) so far name this exact "
                 "version; the rest are carried over from earlier releases. Bug reports keep "
                 "arriving after a release, so the verdict firms up over the next few "
                 "re-assessments as users report in. Back up before updating.")
    L += [
        "",
        "## Resources",
        "",
        f"- [Full assessment (markdown)]({site}/llms-full.txt): the complete current verdict — "
        "thesis, known issues, what's new, platform impact.",
        f"- [Full assessment (JSON API)]({site}/latest.json): the same payload as structured JSON.",
        f"- [RSS feed of verdicts]({site}/feed.xml): one item per assessed version.",
        f"- [Status badge (SVG)]({site}/badge.svg)",
        f"- [Human page]({site}/)",
        "",
    ]
    return "\n".join(L)


def _llms_full_md(data: dict) -> str:
    """Full /llms-full.txt: the entire current assessment as clean, parseable markdown."""
    site = config.SITE_URL.rstrip("/")
    ver = data.get("version", "")
    rec = data.get("recommendation", "")
    phrase = _verdict_phrase(rec)
    L = [
        f"# ClawStat.us — OpenClaw v{ver}",
        "",
        f"> Should you update to OpenClaw v{ver}? Verdict: {rec} {phrase} "
        f"({data.get('confidence', '')} confidence). Assessed {(data.get('assessed_at','') or '')[:10]}.",
        "",
    ]
    if data.get("headline"):
        L += [data["headline"].strip(), ""]

    L += ["## Verdict", "",
          f"- Subject: OpenClaw v{ver}",
          f"- Recommendation: {rec} {phrase}",
          f"- Confidence: {data.get('confidence', '')}",
          f"- Assessed at: {data.get('assessed_at', '')}"]
    lr = data.get("latest_release") or {}
    if lr.get("tag"):
        pub = f" (published {lr.get('published_at')})" if lr.get("published_at") else ""
        L.append(f"- Latest release: {lr['tag']}{pub}")
    lpr = data.get("latest_prerelease") or {}
    if lpr.get("tag"):
        L.append(f"- Latest pre-release: {lpr['tag']}")
    npm = data.get("npm") or {}
    if npm.get("version"):
        L.append(f"- npm: {npm['version']}")
    L.append("")

    if data.get("thesis"):
        L += ["## Why this verdict", "", data["thesis"].strip(), ""]

    ev = data.get("evidence") or {}
    for title, key in [("Reasons to update", "for_updating"),
                       ("Reasons to hold off", "against_updating"),
                       ("Context", "neutral")]:
        items = ev.get(key) or []
        if items:
            L += [f"## {title}", ""] + [f"- {str(it).strip()}" for it in items] + [""]

    ki = data.get("known_issues") or []
    if ki:
        L += [f"## Known issues ({len(ki)})", ""] + [_md_issue_line(i) for i in ki] + [""]

    ch = data.get("changes") or {}
    change_lines = []
    for label, key in [("Features", "features"), ("Fixes", "fixes"), ("Breaking", "breaking")]:
        items = ch.get(key) or []
        if items:
            change_lines += [f"### {label}", ""] + [f"- {(it.get('title') or '').strip()}" for it in items] + [""]
    if change_lines:
        L += ["## What's new", ""] + change_lines

    pi = data.get("platform_impact") or {}
    if pi:
        L += ["## Platform impact", ""] + [f"- {k}: {v}" for k, v in pi.items()] + [""]

    if data.get("sentiment_summary"):
        L += ["## Community sentiment", "", data["sentiment_summary"].strip(), ""]

    L += ["---",
          f"Machine-readable JSON: {site}/latest.json · RSS: {site}/feed.xml · Human page: {site}/",
          "Generated automatically by ClawStat.us — every field is an automated assessment; "
          "verify against the linked GitHub issues.",
          ""]
    return "\n".join(L)


def _write_llms(data: dict, output_path: str) -> None:
    """Write the agent-readable layer beside the page: llms.txt + llms-full.txt."""
    base = Path(output_path)
    _atomic_write_text(base.with_name("llms.txt"), _llms_txt(data))
    _atomic_write_text(base.with_name("llms-full.txt"), _llms_full_md(data))


# ═══════════════════════════════════════════════════════════════════════════
#  On-page SEO: dynamic <title>/description, OG/Twitter, JSON-LD, server-rendered
#  answer (crawlable without JS), robots.txt + sitemap.xml.
#  EVERY field here is untrusted LLM/GitHub text injected into HTML/attrs/JSON —
#  so all of it is HTML-escaped (and `<` is \u-escaped inside the JSON-LD) to keep
#  the page XSS-safe, exactly like the textContent rule on the JS side.
# ═══════════════════════════════════════════════════════════════════════════

def _html_escape(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _truncate(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _seo_title(data: dict) -> str:
    ver = data.get("version", "")
    if not ver:
        return "ClawStat.us — Should you update OpenClaw?"
    return f"Should you update OpenClaw v{ver}? {_verdict_phrase(data.get('recommendation', ''))} — ClawStat.us"


def _seo_description(data: dict) -> str:
    h = (data.get("headline") or "").strip()
    if not h:
        h = (f"An automated, evidence-backed verdict on whether to update OpenClaw "
             f"v{data.get('version', '')}.")
    return _truncate(h, 160)


def _json_ld(data: dict) -> str:
    site = config.SITE_URL.rstrip("/")
    ver = data.get("version", "")
    phrase = _verdict_phrase(data.get("recommendation", ""))
    desc = _seo_description(data)
    assessed = data.get("assessed_at", "") or ""
    webpage = {
        "@type": "WebPage", "@id": f"{site}/#webpage", "url": f"{site}/",
        "name": _seo_title(data), "isPartOf": {"@id": f"{site}/#website"},
        "description": desc,
        "about": {"@type": "SoftwareApplication", "name": "OpenClaw",
                  "applicationCategory": "DeveloperApplication",
                  **({"softwareVersion": ver} if ver else {})},
    }
    if assessed:
        webpage["datePublished"] = assessed
        webpage["dateModified"] = assessed
    answer = f"{phrase}. {desc}"
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": f"{site}/#website", "url": f"{site}/", "name": "ClawStat.us",
         "description": "Automated, evidence-backed verdicts on whether to update each OpenClaw release."},
        webpage,
        {"@type": "FAQPage", "@id": f"{site}/#faq", "isPartOf": {"@id": f"{site}/#webpage"},
         "mainEntity": [
             {"@type": "Question", "name": f"Should you update OpenClaw v{ver}?",
              "acceptedAnswer": {"@type": "Answer", "text": answer}},
             {"@type": "Question", "name": f"Is OpenClaw v{ver} safe to update?",
              "acceptedAnswer": {"@type": "Answer", "text": answer}},
             # Evergreen (version-agnostic) Q&A so the page matches generic intent —
             # "should I update OpenClaw", "is OpenClaw safe to update" — every release.
             {"@type": "Question", "name": "Should I update OpenClaw?",
              "acceptedAnswer": {"@type": "Answer", "text":
               ("It depends on the release. ClawStat.us publishes a fresh, evidence-based verdict for "
                "the latest OpenClaw version"
                + (f" — currently {phrase} for v{ver}" if ver else "")
                + ". It scouts the bugs people hit after a release, scores them by severity, and has "
                "two independent AI models weigh the evidence before giving a clear answer: update "
                "now, update with care, or skip this version.")}},
             {"@type": "Question", "name": "How do I know if a new OpenClaw release is safe to update to?",
              "acceptedAnswer": {"@type": "Answer", "text":
               ("Check ClawStat.us before you upgrade. For each OpenClaw release it gathers post-release "
                "bug reports, scores them against the repository's own severity labels, and runs a "
                "multi-model review to produce a single verdict on whether the update is safe. It "
                "refreshes automatically every few hours.")}},
         ]},
    ]}
    # \u-escape `<` so the JSON can never break out of the <script> (and stays valid JSON-LD).
    return ('<script type="application/ld+json">'
            + json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c")
            + "</script>")


def _seo_head(data: dict) -> str:
    site = config.SITE_URL.rstrip("/")
    e = _html_escape
    title, desc = _seo_title(data), _seo_description(data)
    return "\n".join([
        f'<meta name="description" content="{e(desc)}">',
        f'<link rel="canonical" href="{site}/">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="ClawStat.us">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(desc)}">',
        f'<meta property="og:url" content="{site}/">',
        f'<meta property="og:image" content="{site}/og.png">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(title)}">',
        f'<meta name="twitter:description" content="{e(desc)}">',
        f'<meta name="twitter:image" content="{site}/og.png">',
        _json_ld(data),
    ])


def _seo_body(data: dict) -> str:
    """Server-rendered crawlable answer placed inside #app; the JS replaces it on load."""
    e = _html_escape
    ver = data.get("version", "")
    phrase = _verdict_phrase(data.get("recommendation", ""))
    conf = data.get("confidence", "")
    assessed = (data.get("assessed_at", "") or "")[:10]
    h1 = f"Should you update OpenClaw v{ver}? — {phrase}" if ver else "Should you update OpenClaw?"
    out = ['<article class="ssr">', f"<h1>{e(h1)}</h1>"]
    verdict_line = f"<strong>Verdict:</strong> {e(phrase)}"
    if conf:
        verdict_line += f" ({e(conf)} confidence)"
    if assessed:
        verdict_line += f", assessed {e(assessed)}"
    out.append(f"<p>{verdict_line}.</p>")
    fr = data.get("freshness") or {}
    if fr.get("fresh"):
        spec = fr.get("version_specific_issues") or 0
        when = "today" if (fr.get("days_since_release") or 0) == 0 else "in the last few days"
        body = (f"So far {spec} of the issues below name this exact release; the rest are "
                "carried over from earlier versions."
                if spec else "No issues have been filed against this exact release yet, so the "
                "list below is carried over from earlier versions — a reporting lag as people "
                "upgrade, not a gap in the analysis.")
        out.append(f"<p><strong>Fresh release.</strong> OpenClaw v{e(ver)} was published "
                   f"{when} — an early read. {body} Bug reports keep arriving in the days after "
                   "a release, so the verdict firms up over the next few re-assessments. Back up "
                   "before you update.</p>")
    if data.get("headline"):
        out.append(f"<p>{e(data['headline'].strip())}</p>")
    thesis = (data.get("thesis") or "").strip()
    if thesis:
        out += ["<h2>Why this verdict</h2>", f"<p>{e(thesis.split(chr(10) + chr(10))[0].strip())}</p>"]
    ki = data.get("known_issues") or []
    if ki:
        out.append(f"<h2>Known issues ({len(ki)})</h2>")
        out.append("<ul>")
        for i in ki[:8]:
            out.append(f"<li>#{e(i.get('number'))} ({e(i.get('severity') or '')}) "
                       f"{e((i.get('title') or '').strip())}</li>")
        out.append("</ul>")
    # Evergreen, version-agnostic summary — matches generic search intent (whether you
    # should update OpenClaw / is the latest update safe) on every release, not just this one.
    out.append("<p>ClawStat.us gives an independent, evidence-based answer to whether you should "
               "update OpenClaw — for every release. Is the latest OpenClaw update safe, or should "
               "you wait for the next one? Each verdict is built from post-release bug reports and a "
               "two-model review, refreshed every few hours.</p>")
    out.append('<p>Machine-readable: <a href="latest.json">JSON API</a> · '
               '<a href="llms.txt">llms.txt</a> · <a href="llms-full.txt">full markdown</a></p>')
    out.append('<p><small>Independent, unofficial project — not affiliated with or endorsed by '
               'OpenClaw or its maintainers. Verdicts are generated automatically and may be wrong; '
               'confirm against the linked issues before updating. Data is drawn from the public '
               'github.com/openclaw/openclaw issue tracker and releases via the GitHub API.</small></p>')
    out.append("</article>")
    return "\n".join(out)


def _inject_seo(html: str, data: dict) -> str:
    """Fill the SEO placeholders: dynamic <title>, the head meta/JSON-LD block, and the
    server-rendered answer inside #app. No-ops cleanly if a placeholder is absent."""
    title = _html_escape(_seo_title(data))
    html = re.sub(r"<title>.*?</title>", lambda m: f"<title>{title}</title>", html,
                  count=1, flags=re.DOTALL)
    html = html.replace("<!--SEO-HEAD-->", _seo_head(data), 1)
    html = html.replace("<!--SSR-->", _seo_body(data), 1)
    return html


def _write_robots(output_path: str) -> None:
    """robots.txt → allow all + advertise the sitemap."""
    site = config.SITE_URL.rstrip("/")
    body = f"User-agent: *\nAllow: /\n\nSitemap: {site}/sitemap.xml\n"
    _atomic_write_text(Path(output_path).with_name("robots.txt"), body)


def _write_sitemap(data: dict, output_path: str) -> None:
    """sitemap.xml → the homepage + every archived per-version snapshot."""
    site = config.SITE_URL.rstrip("/")
    lastmod = (data.get("assessed_at", "") or "")[:10]
    urls = [("    <url>\n"
             f"      <loc>{site}/</loc>\n"
             + (f"      <lastmod>{_xml_escape(lastmod)}</lastmod>\n" if lastmod else "")
             + "      <changefreq>daily</changefreq>\n      <priority>1.0</priority>\n"
             "    </url>")]
    for ver in (data.get("archived_versions") or []):
        urls.append("    <url>\n"
                    f"      <loc>{site}/archive/{_xml_escape(ver)}.html</loc>\n"
                    "      <changefreq>monthly</changefreq>\n      <priority>0.4</priority>\n"
                    "    </url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(urls) + "\n</urlset>\n")
    _atomic_write_text(Path(output_path).with_name("sitemap.xml"), xml)


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
        return ""

    if not config.TEMPLATE_FILE.exists():
        print(f"❌ Template not found: {config.TEMPLATE_FILE}")
        return ""

    # ── Rollback: backup existing page ──
    _backup_existing(out, new_version=assessment_raw.get("version", ""))

    with open(config.TEMPLATE_FILE) as f:
        html = f.read()

    data = _build_assessment_data(assessment_raw, raw)
    html = _inject_data(html, data)
    html = _inject_seo(html, data)   # dynamic title/meta/JSON-LD + server-rendered answer
    version = data.get("version", "")

    # ── Smoke Test: validate before writing ──
    # Write to a temp location first, test, then finalize
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".html", dir=str(config.WEB_DIR))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(html)
        smoke = smoke_test_html(tmp_path, expected_version=version)
    except Exception as e:
        # A write/smoke-test error must NOT publish: treat it as a failed deploy and
        # keep the previous page rather than overwriting it with an untested one.
        print(f"  ⚠️ SMOKE TEST ERRORED ({e}) — keeping previous version at: {out}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return ""

    if not smoke["pass"]:
        failed = [c for c in smoke["checks"] if not c["passed"]]
        print(f"  ⚠️ SMOKE TEST FAILED ({len(failed)} checks):")
        for c in failed:
            print(f"    ❌ {c['name']}: {c['detail']}")
        print(f"  Keeping previous version at: {out}")
        os.unlink(tmp_path)
        return ""
    print(f"  ✅ Smoke test passed ({len(smoke['checks'])} checks)")

    # Smoke test passed — move tmp to final location
    shutil.move(tmp_path, out)
    _make_world_readable(out)  # served by Caddy (different user) — must be world-readable

    # Emit the same payload as a sibling latest.json for the runtime fetch path.
    _write_latest_json(data, out)

    # Shareable static artifacts: an RSS feed of verdicts and an embeddable badge.
    _write_feed(data, out)
    _write_badge(data, out)
    # Agent-readable layer: llms.txt + a full markdown mirror of the verdict.
    _write_llms(data, out)
    # SEO crawl infrastructure: robots.txt + sitemap.xml.
    _write_robots(out)
    _write_sitemap(data, out)

    print(f"✅ Built assessment page: {out}")
    print(f"   Version: {version}")
    print(f"   Recommendation: {data['recommendation']}")
    print(f"   Known issues: {len(data['known_issues'])}")
    return html
