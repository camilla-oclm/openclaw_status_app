#!/usr/bin/env bash
# Pull the latest code before the scheduled tick.
#
# A TRANSIENT failure (the box is briefly offline) is tolerated SILENTLY so the tick still runs
# on the current checkout — that's the benign, expected case. A PERSISTENT divergence (local
# HEAD stuck behind origin/main — a manual commit on the box, an upstream force-push/rebase, or
# a dirty tree that blocks --ff-only) is SURFACED via the alert webhook: without this the box
# would run STALE code indefinitely with zero signal (release-gate finding D30).
#
# Always exits 0, so it can never block ExecStart (the unit also keeps the "-" prefix as a
# second belt).
set -u
APP_DIR=/opt/openclaw_status_app
cd "$APP_DIR" || exit 0

if git pull --ff-only; then
    exit 0
fi

# The pull failed. Distinguish "can't reach origin" (transient) from a real divergence.
if ! git fetch --quiet origin main 2>/dev/null; then
    exit 0   # origin unreachable → transient, tolerate silently
fi
LOCAL=$(git rev-parse HEAD 2>/dev/null || echo '?')
REMOTE=$(git rev-parse origin/main 2>/dev/null || echo '?')
if [ "$REMOTE" != '?' ] && [ "$LOCAL" != "$REMOTE" ]; then
    # Reached origin and we ARE behind, but --ff-only refused → a non-fast-forward divergence.
    # Surface it out-of-band and keep running the stale checkout rather than blocking the tick.
    "$APP_DIR/.venv/bin/python" "$APP_DIR/run.py" notify-test \
        "⚠️ OpenClaw Status: 'git pull --ff-only' diverged on the box (local ${LOCAL:0:9} vs origin/main ${REMOTE:0:9}) — running STALE code until reconciled" || true
fi
exit 0
