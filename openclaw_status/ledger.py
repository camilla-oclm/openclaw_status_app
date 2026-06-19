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
flagged "fixed in next pre-release" — the page surfaces that as a staged-fix signal
while the verdict still treats the shipped release as carrying the issue.
"""

from openclaw_status import config, github
from openclaw_status.lib import load_json, now_iso, save_json


def load_ledger() -> dict:
    """Load the on-disk ledger, or {} if missing/corrupt."""
    if not config.ISSUE_LEDGER_FILE.exists():
        return {}
    try:
        data = load_json(config.ISSUE_LEDGER_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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


def is_version_relevant(it: dict) -> bool:
    """Whether a scouted issue affects the assessed release — i.e. what we accumulate.
    The complement (version-agnostic "ongoing majors") is kept only as LLM context,
    never added to this version's ledger or its known-issues count."""
    return bool(it.get("affects_version") or it.get("category") == "regression")


def _lean(it: dict, now: str, prev: dict | None) -> dict:
    """A compact, accumulating record for one issue.

    Reaction/comment counts are monotonic (max) — a quieter or partial re-scout can't
    walk a community-impact signal back. Label-derived fields (severity, category,
    affects_version), by contrast, track the *current* scout: they are pure functions of
    the issue's labels/text (github.derive_*), so re-deriving them each run lets a
    scoring-formula change or a maintainer re-label self-correct instead of freezing at a
    historical worst. first_seen is preserved, last_seen bumped.
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
        # Current scout's labels win when present (even if now empty — a removed label must
        # propagate so severity/category can re-derive); fall back to prev only if absent.
        "labels": (it["labels"] if "labels" in it else (prev.get("labels") or []))[:6],
        # Re-derived from the current scout (not OR'd with prev) so a tightened match
        # walks a stale "affects this version" back; falls back to prev only if absent.
        "affects_version": bool(it["affects_version"]) if "affects_version" in it
                           else bool(prev.get("affects_version")),
        "impact": it.get("impact") or prev.get("impact"),
        "severity": it.get("severity") or prev.get("severity") or "medium",
        "category": it.get("category") or prev.get("category") or "active",
        "fixed_in": _merge_fixed(prev.get("fixed_in"), it.get("fixed_in")),
        "clawsweeper": it.get("clawsweeper") or prev.get("clawsweeper"),
        "first_seen": prev.get("first_seen", now),
        "last_seen": now,
    }


def _rederive_stored(store: dict, release_date: str) -> None:
    """Re-derive the label-derived fields (severity, category) for EVERY accumulated
    issue, not just the ones re-scouted this run, then drop any that fall out of
    relevance. Severity/category are pure functions of the issue's labels/text — all of
    which the ledger already stores — so recomputing them each run lets a scoring-formula
    change or a maintainer re-label self-correct across the whole ledger, instead of
    freezing on entries that happen not to surface in a given run's top-N scout.
    """
    for key in list(store.keys()):
        rec = store[key]
        labels = rec.get("labels") or []
        rec["severity"] = github.derive_severity(
            labels, rec.get("reactions", 0), rec.get("comments", 0))
        rec["category"] = github.categorize(
            rec.get("created_at", ""), labels, bool(rec.get("affects_version")),
            rec.get("impact") or "low", release_date, rec.get("title", ""))
        if not is_version_relevant(rec):
            del store[key]   # no longer affects this release and isn't a regression


def merge_version_issues(version: str, scouted: list, now: str | None = None,
                         release_date: str = "") -> list:
    """Upsert the version-relevant scouted issues into the ledger for `version` and
    return the accumulated, ranked list (the new source of truth for known issues).

    Issues not surfaced by this run's scout are kept as-is (still open) — an immutable
    released version doesn't lose issues just because a later, noisier search didn't
    return them in its top-N. Their label-derived fields are still refreshed each run
    (see `_rederive_stored`) so stale severities/categories can't linger.
    """
    if not version:
        return scouted
    now = now or now_iso()
    ledger = load_ledger()
    entry = ledger.setdefault(version, {"first_seen": now, "issues": {}})
    prev_run = entry.get("last_seen")   # previous run's timestamp (None on the first run)
    store = entry["issues"]

    for it in scouted:
        num = it.get("number")
        if num is None:
            continue
        key = str(num)
        if not is_version_relevant(it):
            # Re-scouted this run but no longer relevant to THIS release (e.g. a version
            # mention a tightened match no longer counts) — drop the stale entry. Issues
            # we simply didn't see this run are still left untouched below (an immutable
            # release doesn't lose an open issue just because a noisier search missed it).
            store.pop(key, None)
            continue
        store[key] = _lean(it, now, store.get(key))

    entry["last_seen"] = now
    _rederive_stored(store, release_date)   # self-correct the whole accumulated set

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
