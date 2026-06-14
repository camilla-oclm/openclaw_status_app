"""
Per-version issue ledger — accumulates the issues that affect each released version.

A released version is immutable: it won't be patched until the next release, so the
set of issues affecting it only grows. Re-deriving "known issues" from a fresh GitHub
scout every run made the list (and the verdict) flip-flop — a busy run surfaced 20
issues, a quiet one 7. The ledger fixes that: each run UPSERTS the version-relevant
issues it scouts (new ones added, existing ones updated — reactions only climb,
severity keeps its worst, fix-status fills in) and never drops them. Downstream reads
the accumulated set, so the Known-issues list and its counts are deterministic and
monotonic instead of a coin flip.

Fix detection is cheap by design: an issue is flagged fixed only when a release /
pre-release explicitly closes it or Clawsweeper says so — we never silently drop it.
A pre-release fix does NOT fix the *current* (released) version, so the issue stays,
flagged "fixed in next pre-release" — which is exactly the 🔄 "wait for next" signal.
"""

from openclaw_status import config, github
from openclaw_status.lib import load_json, now_iso, save_json

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load_ledger() -> dict:
    """Load the on-disk ledger, or {} if missing/corrupt."""
    if not config.ISSUE_LEDGER_FILE.exists():
        return {}
    try:
        data = load_json(config.ISSUE_LEDGER_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _worst_severity(a, b):
    """The more severe of two severity strings (missing/unknown ranks lowest)."""
    return a if _SEV_ORDER.get(a, 99) <= _SEV_ORDER.get(b, 99) else b


def _merge_fixed(prev, new):
    """Union of two fixed_in lists (each may be a str, list, or None)."""
    out = []
    for src in (prev, new):
        if isinstance(src, str):
            src = [src]
        for v in (src or []):
            if v and v not in out:
                out.append(v)
    return out


def _affects_release(it: dict) -> bool:
    """Whether a scouted issue affects the assessed release — i.e. what we accumulate.
    Version-agnostic majors stay as LLM context but don't enter this version's ledger."""
    return bool(it.get("affects_version") or it.get("category") == "regression")


def _lean(it: dict, now: str, prev: dict | None) -> dict:
    """A compact, accumulating record for one issue.

    Monotonic fields (reactions, severity) only ever move toward "worse" so a quieter
    re-scout can't walk an issue back; first_seen is preserved, last_seen bumped.
    """
    prev = prev or {}
    return {
        "number": it.get("number"),
        "title": it.get("title") or prev.get("title", ""),
        "url": it.get("url") or prev.get("url", ""),
        "body": (it.get("body") or prev.get("body", ""))[:600],
        "comments": max(int(it.get("comments") or 0), int(prev.get("comments") or 0)),
        "reactions": max(int(it.get("reactions") or 0), int(prev.get("reactions") or 0)),
        "total_reactions": max(int(it.get("total_reactions") or 0), int(prev.get("total_reactions") or 0)),
        "created_at": it.get("created_at") or prev.get("created_at", ""),
        "labels": (it.get("labels") or prev.get("labels") or [])[:6],
        "affects_version": bool(it.get("affects_version") or prev.get("affects_version")),
        "impact": it.get("impact") or prev.get("impact"),
        "severity": _worst_severity(prev.get("severity"), it.get("severity")),
        # "regression" is sticky — once post-release breakage, always flagged so.
        "category": "regression" if "regression" in (it.get("category"), prev.get("category"))
                    else (it.get("category") or prev.get("category") or "active"),
        "fixed_in": _merge_fixed(prev.get("fixed_in"), it.get("fixed_in")),
        "clawsweeper": it.get("clawsweeper") or prev.get("clawsweeper"),
        "first_seen": prev.get("first_seen", now),
        "last_seen": now,
    }


def merge_version_issues(version: str, scouted: list, now: str | None = None) -> list:
    """Upsert the version-relevant scouted issues into the ledger for `version` and
    return the accumulated, ranked list (the new source of truth for known issues).

    Issues not surfaced by this run's scout are kept as-is (still open) — an immutable
    released version doesn't lose issues just because a later, noisier search didn't
    return them in its top-N.
    """
    if not version:
        return scouted
    now = now or now_iso()
    ledger = load_ledger()
    entry = ledger.setdefault(version, {"first_seen": now, "issues": {}})
    prev_run = entry.get("last_seen")   # previous run's timestamp (None on the first run)
    store = entry["issues"]

    for it in scouted:
        if not _affects_release(it):
            continue
        num = it.get("number")
        if num is None:
            continue
        key = str(num)
        store[key] = _lean(it, now, store.get(key))

    entry["last_seen"] = now

    # Cap per version (keep the highest-ranked) so the ledger / prompt can't grow without
    # bound, then prune to the most-recently-seen versions.
    items = sorted(store.values(), key=github.rank_key)
    if len(items) > config.LEDGER_MAX_ISSUES_PER_VERSION:
        items = items[: config.LEDGER_MAX_ISSUES_PER_VERSION]
        entry["issues"] = {str(i["number"]): i for i in items}

    _prune_versions(ledger)
    save_json(config.ISSUE_LEDGER_FILE, ledger)

    # Return copies flagged "new since the previous run" (transient — never persisted).
    return [dict(it, is_new=bool(prev_run and it.get("first_seen", "") > prev_run)) for it in items]


def _prune_versions(ledger: dict) -> None:
    """Keep only the most-recently-seen LEDGER_KEEP_VERSIONS versions."""
    if len(ledger) <= config.LEDGER_KEEP_VERSIONS:
        return
    ordered = sorted(ledger.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True)
    for v, _ in ordered[config.LEDGER_KEEP_VERSIONS:]:
        ledger.pop(v, None)


def _fixed_label(fixed_in):
    """Friendly display string for a fixed_in list (or None if not fixed)."""
    if isinstance(fixed_in, str):
        fixed_in = [fixed_in]
    fixed_in = fixed_in or []
    if not fixed_in:
        return None
    joined = " ".join(str(f).lower() for f in fixed_in)
    if any(t in joined for t in ("prerelease", "pre-release", "beta", "rc", "next")):
        return "next pre-release"
    return "this release"


def display_known_issues(accumulated: list) -> list:
    """Shape the accumulated ledger issues into the assessment's known_issues list —
    the deterministic, monotonic set the page renders (replaces the model's hand-pick)."""
    out = []
    for it in accumulated:
        cs = it.get("clawsweeper")
        out.append({
            "number": it.get("number"),
            "title": it.get("title", ""),
            "severity": it.get("severity", "medium"),
            "category": it.get("category", "active"),
            "clawsweeper_decision": cs.get("decision", "unknown") if isinstance(cs, dict) else "unknown",
            "fixed_in": _fixed_label(it.get("fixed_in")),
            "reactions": it.get("reactions", 0),
            "impact": it.get("impact"),
            "affects_version": bool(it.get("affects_version")),
            "first_seen": it.get("first_seen"),
            "is_new": bool(it.get("is_new")),
        })
    return out
