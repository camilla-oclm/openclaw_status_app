"""
Per-version changelog freeze — capture a released version's `changes` once, replay forever.

A released version's changelog (the release body) is immutable: it won't change until the next
release. But the analyst LLM re-parses it from scratch every run, so the extracted
breaking/fixes/features lists drift run-to-run (run 2 and run 3 pull a slightly different set
of bullets from identical text) and the page's "fixes shipped" / "new features" counts move
even though nothing actually changed.

That's the same flip-flop the issue ledger fixes for known issues, and the continuity prompt
fixes for the verdict — applied here to `changes`. Policy: **first non-empty capture wins.**
The first run that produces a non-empty `changes` for a version is stored and frozen; every
later run for that version gets the stored copy back verbatim. The "non-empty" guard means a
one-off empty extraction from a hiccuped run never freezes an empty changelog — a later, fuller
run can still seed the slot. Keyed by version, pruned to the most-recently-captured versions.
Runtime state; gitignored.
"""

from openclaw_status import config
from openclaw_status.lib import load_json, now_iso, save_json

_KEYS = ("breaking", "fixes", "features")


def _norm(changes) -> dict:
    """Coerce to the canonical {breaking, fixes, features: [list]} shape (drop junk/extra keys)."""
    changes = changes if isinstance(changes, dict) else {}
    return {k: (changes[k] if isinstance(changes.get(k), list) else []) for k in _KEYS}


def is_empty(changes) -> bool:
    """True when a changes dict carries no items in any bucket."""
    n = _norm(changes)
    return not any(n[k] for k in _KEYS)


def load_store() -> dict:
    """Load the on-disk freeze store, or {} if missing/corrupt."""
    if not config.RELEASE_CHANGES_FILE.exists():
        return {}
    try:
        data = load_json(config.RELEASE_CHANGES_FILE)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _prune(store: dict) -> None:
    """Keep only the most-recently-captured LEDGER_KEEP_VERSIONS versions."""
    if len(store) <= config.LEDGER_KEEP_VERSIONS:
        return
    ordered = sorted(store.items(), key=lambda kv: kv[1].get("captured_at", ""), reverse=True)
    for v, _ in ordered[config.LEDGER_KEEP_VERSIONS:]:
        store.pop(v, None)


def freeze(version: str, changes) -> dict:
    """Return the frozen `changes` for `version`, capturing it on first non-empty sight.

    First-non-empty-wins: once a version has a stored non-empty changes, that copy is returned
    verbatim on every later run (so the fixes/features counts are static). Until then (no
    capture, or only an empty placeholder), the current run's changes are stored and returned —
    so a later, fuller extraction can still seed an empty slot. Falsy `version` → nothing to
    freeze; the input is returned normalized.
    """
    norm = _norm(changes)
    if not version:
        return norm

    store = load_store()
    existing = store.get(version)
    if existing and not is_empty(existing):
        return _norm(existing)            # already frozen — replay it verbatim (drop captured_at)

    # Not frozen yet (absent, or a stored-empty placeholder). Persist only a non-empty capture —
    # an empty extraction isn't worth a write and must not freeze the slot.
    if is_empty(norm):
        return norm
    store[version] = {**norm, "captured_at": now_iso()}
    _prune(store)
    save_json(config.RELEASE_CHANGES_FILE, store)
    return norm
