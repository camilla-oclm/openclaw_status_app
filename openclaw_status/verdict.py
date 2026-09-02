"""
Deterministic evidence layer — the verdict rules that need no model.

Three things live here, shared by the assess step (agent.py), the render step
(render.py: latest.json / SSR / llms.txt) and — as a hand-kept twin — the page's
client JS (template.html `blockersFor`):

1. **Credible blockers + the evidence gate.** The scout is severity-seeking and the
   repo's P labels are bot-applied at scale, so "60 high/critical issues" is the
   steady state for EVERY release of a project with millions of weekly installs.
   What actually discriminates is whether anyone stands behind a report: a HIGH counts
   only when a human corroborated its priority or the community engaged with it;
   a CRITICAL always counts for what it demonstrably touches. From those blockers the
   gate derives a floor verdict — ✅ (no credible blocker), ⚠️ (credible blockers,
   none widespread) or ⏸️ (a widespread breaker, or a human-backed unfixed
   security/data-loss issue confirmed on this exact version). The analyst starts from
   the gate and may only be MORE cautious, with a cited reason; the pipeline enforces
   the floor so a published verdict is never less cautious than the evidence.

2. **The best version to run today.** Users asked "which version should I install?"
   and a page that only ever grades the newest release never answers it. A release is
   *settled* once it has been in the field for `SETTLED_MIN_DAYS`, its last verdict was
   not ⏸️, and its own ledger shows no widespread breaker. The newest settled release
   is the recommendation; the latest release is named alongside it.

3. **The plain-language status vocabulary** — the words the page, llms.txt and SSR
   all use for the same verdict, plus the "too new to call" state a fresh release
   sits in while reports are still arriving.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openclaw_status.lib import norm_rec

# Caution order of the three verdicts (higher = more cautious).
ORDER = ["✅", "⚠️", "⏸️"]
_RANK = {r: i for i, r in enumerate(ORDER)}

BLOCKING_SEVERITIES = ("critical", "high")
# Mirrors github.impact_level's "high" threshold — megathread-class engagement.
WIDESPREAD_REACTIONS = 10
# Days in the field before a release can be called settled (reports need time to accrue;
# FRESH_RELEASE_DAYS is 2, the first cadence week is where verdicts still move).
SETTLED_MIN_DAYS = 7
# Labels whose credible, exact-version, unfixed presence earns a ⏸️ on its own (rule 15b).
SERIOUS_LABELS = ("impact:security", "impact:data-loss")

# Plain-language status words. `key` is stable for consumers; `label` is the copy.
STATUS = {
    "✅": {"key": "update", "label": "Safe to update"},
    "⚠️": {"key": "care", "label": "Update with care"},
    "⏸️": {"key": "skip", "label": "Skip this version"},
}
STATUS_WAIT = {"key": "wait", "label": "Too new to call"}


# ── per-issue predicates (each fails CLOSED when its field is absent) ────────────

def is_open(issue: dict) -> bool:
    return str(issue.get("state") or "open").lower() != "closed"


def human_backed(issue: dict) -> bool:
    """Someone besides the triage bot stands behind the P label (human /
    bot-corroborated / unknown). Absent field → trust it (pre-provenance payloads)."""
    p = issue.get("priority_provenance")
    if p is None or p == "":
        return True
    return str(p).lower() != "bot"


def impact_at_least(issue: dict, level: str) -> bool:
    """Community-engagement bucket check (`impact` from github.impact_level).
    Absent field → trust it (fail closed)."""
    imp = issue.get("impact")
    if imp is None or imp == "":
        return True
    imp = str(imp).lower()
    return imp == "high" if level == "high" else imp != "low"


def is_widespread(issue: dict) -> bool:
    """Megathread class: the engagement a genuinely widespread breaker draws within days."""
    return (str(issue.get("impact") or "").lower() == "high"
            or int(issue.get("reactions") or 0) >= WIDESPREAD_REACTIONS)


def is_credible_blocker(issue: dict) -> bool:
    """An OPEN high/critical issue confirmed for this version that the evidence backs:
    a critical always; a high only when a human corroborated its priority or the
    community engaged (impact above "low"). A staged fix does NOT clear it — the fix
    isn't in this release. The exact twin of the page's `blockersFor` severity gate."""
    sev = str(issue.get("severity") or "").lower()
    if sev not in BLOCKING_SEVERITIES:
        return False
    if not is_open(issue):
        return False
    if not issue.get("affects_version"):
        return False
    if sev == "high" and not human_backed(issue) and not impact_at_least(issue, "medium"):
        return False
    return True


def is_serious_unfixed(issue: dict) -> bool:
    """Rule 15(b): a CRITICAL naming this exact version, carrying a security / data-loss
    impact label, unfixed, and corroborated by a PERSON — a human applied its priority, or
    the community engaged with it (impact above "low"). Earns a ⏸️ on its own.

    Deliberately fails OPEN on missing evidence (unlike the blocker gate): the impact
    labels are bot-applied at scale, so a bot-only data-loss critical with zero
    engagement is a ⚠️-class report, and a false ⏸️ here would silently hold every
    release — the exact failure this layer exists to prevent."""
    if not is_credible_blocker(issue):
        return False
    if str(issue.get("severity") or "").lower() != "critical":
        return False
    if issue.get("version_match") != "exact" or issue.get("fixed_in"):
        return False
    labels = [str(l).lower() for l in (issue.get("labels") or [])]
    if not any(l.startswith(s) for l in labels for s in SERIOUS_LABELS):
        return False
    imp = str(issue.get("impact") or "").lower()
    return (str(issue.get("priority_provenance") or "").lower() == "human"
            or imp in ("medium", "high"))


def credible_blockers(issues: list) -> list:
    """The credible blockers among `issues`, most important first (weight, then number)."""
    from openclaw_status import github   # weight fallback; github never imports this module
    out = [i for i in (issues or []) if isinstance(i, dict) and is_credible_blocker(i)]
    out.sort(key=lambda i: (-int(i.get("weight") or github.importance_weight(i)),
                            int(i.get("number") or 0)))
    return out


# ── the gate ─────────────────────────────────────────────────────────────────────

def evidence_gate(issues: list) -> dict:
    """Deterministic floor verdict from the issue evidence alone.

    Returns {verdict, blockers, widespread, serious, blocker_count, reason} where the
    three lists hold issue NUMBERS (compact, safe to publish) and `reason` is a one-line
    human explanation the prompt and the page both quote."""
    blockers = credible_blockers(issues)
    widespread = [b for b in blockers if is_widespread(b)]
    serious = [b for b in blockers if is_serious_unfixed(b)]
    if widespread or serious:
        verdict = "⏸️"
    elif blockers:
        verdict = "⚠️"
    else:
        verdict = "✅"
    n = len(blockers)
    if verdict == "⏸️":
        bits = []
        if widespread:
            bits.append(f"{len(widespread)} widespread breaker(s) "
                        f"(#{', #'.join(str(b['number']) for b in widespread[:3])})")
        if serious:
            bits.append(f"{len(serious)} credible unfixed security/data-loss issue(s) "
                        f"(#{', #'.join(str(b['number']) for b in serious[:3])})")
        reason = " and ".join(bits) + " confirmed for this version."
    elif verdict == "⚠️":
        reason = (f"{n} credible blocking issue{'s' if n != 1 else ''} confirmed for this "
                  f"version, none widespread.")
    else:
        reason = "No credible blocking issue is confirmed for this version."
    return {
        "verdict": verdict,
        "blockers": [int(b["number"]) for b in blockers if b.get("number") is not None],
        "widespread": [int(b["number"]) for b in widespread if b.get("number") is not None],
        "serious": [int(b["number"]) for b in serious if b.get("number") is not None],
        "blocker_count": n,
        "reason": reason,
    }


def more_cautious(a: str, b: str) -> bool:
    """True when verdict `a` is strictly more cautious than `b` (unknown → False)."""
    return _RANK.get(norm_rec(a), -1) > _RANK.get(norm_rec(b), -1)


def apply_gate_floor(assessment: dict, gate: dict) -> str | None:
    """In place: a published verdict may never be LESS cautious than the evidence gate.
    Returns a short note when the floor moved the verdict, else None. The reverse
    direction (the model more cautious than the gate) is allowed — it is a *departure*
    the model must justify in `gate_departure_reason`; see `departure_note`."""
    rec = norm_rec(str(assessment.get("recommendation") or ""))
    floor = gate.get("verdict", "✅")
    if rec in _RANK and more_cautious(floor, rec):
        assessment["recommendation"] = floor
        return f"verdict {rec} raised to the evidence-gate floor {floor} — {gate.get('reason', '')}"
    return None


def departure_note(assessment: dict, gate: dict) -> dict:
    """Describe how the model's verdict relates to the gate (for the record + the page)."""
    rec = norm_rec(str(assessment.get("recommendation") or ""))
    floor = gate.get("verdict", "✅")
    departed = more_cautious(rec, floor)
    reason = str(assessment.get("gate_departure_reason") or "").strip() if departed else ""
    return {"departed": departed, "reason": reason[:400]}


# ── the best version to run today ────────────────────────────────────────────────

def _date(s) -> datetime | None:
    s = str(s or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _days_between(later, earlier) -> int | None:
    a, b = _date(later), _date(earlier)
    if a is None or b is None:
        return None
    return max((a - b).days, 0)


def _ledger_issues(entry) -> list:
    store = (entry or {}).get("issues") if isinstance(entry, dict) else None
    if isinstance(store, dict):
        return [i for i in store.values() if isinstance(i, dict)]
    if isinstance(store, list):
        return [i for i in store if isinstance(i, dict)]
    return []


def _brief(issue: dict) -> dict:
    return {
        "number": issue.get("number"),
        "title": str(issue.get("title") or "")[:140],
        "severity": issue.get("severity"),
    }


def recommended_version(*, release_history: list, version_history: list, ledger: dict,
                        current_version: str, today: str | None = None) -> dict:
    """Pick the best version to run today from what the pipeline has actually assessed.

    A candidate is a STABLE release this tool has assessed (a version_history entry) AND
    still holds a ledger for (so the blocker evidence is real, not an empty list). It is
    *settled* when it has aged ≥ SETTLED_MIN_DAYS, its last verdict wasn't ⏸️, and its
    ledger shows no widespread / serious blocker (gate ≠ ⏸️). The newest settled release
    wins; `kind` says whether that is the latest release itself ("latest"), an older one
    ("settled"), or nothing yet ("none"). Deterministic: no model, no network."""
    today = today or datetime.now(timezone.utc).isoformat()
    hist = {str(h.get("version")): h for h in (version_history or []) if isinstance(h, dict)}
    cands = []
    for r in release_history or []:
        if not isinstance(r, dict) or r.get("prerelease"):
            continue
        v = str(r.get("version") or (r.get("tag") or "").lstrip("v"))
        if not v or v not in hist:
            continue                              # never assessed — can't vouch for it
        entry = (ledger or {}).get(v)
        if not isinstance(entry, dict):
            continue                              # ledger pruned — no evidence to judge on
        issues = _ledger_issues(entry)
        gate = evidence_gate(issues)
        last_rec = norm_rec(str(hist[v].get("recommendation") or ""))
        age = _days_between(today, r.get("published_at"))
        settled = (age is not None and age >= SETTLED_MIN_DAYS
                   and last_rec != "⏸️" and gate["verdict"] != "⏸️")
        blockers = credible_blockers(issues)
        cands.append({
            "version": v,
            "published_at": str(r.get("published_at") or "")[:10],
            "age_days": age,
            "recommendation": last_rec,
            "gate": gate["verdict"],
            "settled": settled,
            "blockers": [_brief(b) for b in blockers[:5]],
            "blocker_count": len(blockers),
        })
    cands.sort(key=lambda c: c["published_at"], reverse=True)
    latest = next((c for c in cands if c["version"] == current_version), None)
    pick = next((c for c in cands if c["settled"]), None)
    out = {
        "version": pick["version"] if pick else None,
        "kind": ("latest" if pick and pick["version"] == current_version
                 else "settled" if pick else "none"),
        "published_at": pick["published_at"] if pick else None,
        "age_days": pick["age_days"] if pick else None,
        "recommendation": pick["recommendation"] if pick else None,
        "gate": pick["gate"] if pick else None,
        "blockers": pick["blockers"] if pick else [],
        "blocker_count": pick["blocker_count"] if pick else 0,
        "min_days": SETTLED_MIN_DAYS,
        "latest": ({"version": latest["version"], "age_days": latest["age_days"],
                    "recommendation": latest["recommendation"], "settled": latest["settled"]}
                   if latest else {"version": current_version}),
        "considered": len(cands),
    }
    return out


# ── status vocabulary ────────────────────────────────────────────────────────────

def status_for(recommendation: str, fresh: bool = False) -> dict:
    """The plain-language status for a verdict: the three verdict words, or "Too new to
    call" while a non-skip release is still inside the fresh window (a fresh ⏸️ stays a
    skip — that evidence is early but already negative). `early_read` carries the verdict
    word the wait-state is standing in for."""
    rec = norm_rec(str(recommendation or ""))
    base = STATUS.get(rec)
    if base is None:
        return {"key": "unknown", "label": "Assessed", "recommendation": rec, "early_read": None}
    if fresh and rec != "⏸️":
        return {**STATUS_WAIT, "recommendation": rec, "early_read": base["label"]}
    return {**base, "recommendation": rec, "early_read": None}
