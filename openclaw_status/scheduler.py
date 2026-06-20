"""Adaptive run scheduling.

A cheap hourly *tick* (driven by the systemd timer) polls for a new OpenClaw release
and decides whether a full LLM assessment is due. Assessments are frequent while a
release is fresh and back off as it matures and the verdict stabilizes:

    • a NEW release        → assess now (and the age clock resets to ~0 → fast tier)
    • else, by release age → < 48h: every 6h · 48–96h: every 8h · ≥ 96h: every 12h

All the policy lives in this one pure module so it's unit-tested and tweakable via
``config`` — instead of being baked into the systemd timer, which can't express a
decaying cadence anyway. The CLI ``tick`` command wires the real clock / GitHub
release / history / run-log into ``should_run``.
"""
from __future__ import annotations

from datetime import datetime

from openclaw_status import config


def cadence_hours(release_age_h: float, tiers=None) -> int:
    """The assessment interval (hours) for a release of ``release_age_h`` hours.

    The first tier whose upper bound the age is *under* wins; the final tier
    (``upper is None``) is the floor for everything older.
    """
    tiers = tiers if tiers is not None else config.ASSESS_CADENCE_TIERS
    for upper, interval in tiers:
        if upper is None or release_age_h < upper:
            return interval
    return tiers[-1][1]


def should_run(now: datetime, release_published: datetime | None, release_version: str,
               last_assessed_version: str, last_run: datetime | None,
               tiers=None, grace_h: float | None = None):
    """Decide whether the hourly tick should launch a full assessment.

    Pure: the caller supplies the clock, the current release (version + publish time),
    the last-assessed version, and the last run start time. Returns ``(run, reason)``.
    """
    grace_h = config.SCHEDULE_GRACE_H if grace_h is None else grace_h

    # A genuinely new stable release is the strongest signal — assess immediately.
    # (This also resets the effective age to ~0, so we drop back to the fast tier.)
    if release_version and release_version != last_assessed_version:
        return True, f"new release {release_version} (last assessed: {last_assessed_version or 'none'})"

    if last_run is None:
        return True, "no prior run on record"

    if release_published is not None:
        age_h = (now - release_published).total_seconds() / 3600
        age_label = f"{age_h:.0f}h"
    else:
        age_h = float("inf")          # unknown publish date → conservative floor cadence
        age_label = "unknown"

    interval = cadence_hours(age_h, tiers)
    since_h = (now - last_run).total_seconds() / 3600
    if since_h >= interval - grace_h:
        return True, f"cadence due: {since_h:.1f}h since last run ≥ {interval}h (release age {age_label})"
    return False, f"not due: {since_h:.1f}h since last run < {interval}h (release age {age_label})"
