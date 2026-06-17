"""Tests for openclaw_status.release_changes — the per-version changelog freeze."""
import json

import pytest

from openclaw_status import config, release_changes


def _changes(fixes=0, features=0, breaking=0):
    return {
        "breaking": [{"title": f"break {i}"} for i in range(breaking)],
        "fixes": [{"title": f"fix {i}", "verified": True} for i in range(fixes)],
        "features": [{"title": f"feat {i}", "value": "x"} for i in range(features)],
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELEASE_CHANGES_FILE", tmp_path / "release-changes.json")
    monkeypatch.setattr(config, "LEDGER_KEEP_VERSIONS", 12)
    return tmp_path


def test_freeze_captures_first_then_replays_verbatim(store):
    first = _changes(fixes=3, features=2)
    out1 = release_changes.freeze("2026.6.8", first)
    assert len(out1["fixes"]) == 3 and len(out1["features"]) == 2

    # A later run extracts a DIFFERENT set from the same (immutable) changelog — the frozen
    # copy must win, so the displayed counts don't move.
    out2 = release_changes.freeze("2026.6.8", _changes(fixes=5, features=1, breaking=2))
    assert len(out2["fixes"]) == 3
    assert len(out2["features"]) == 2
    assert out2["breaking"] == []
    assert out2 == out1


def test_freeze_does_not_capture_empty_then_seeds_on_first_nonempty(store):
    # An empty extraction (e.g. a hiccuped run) must NOT freeze the slot…
    assert release_changes.is_empty(release_changes.freeze("9.9.9", _changes()))
    assert not config.RELEASE_CHANGES_FILE.exists()
    # …so a later, fuller run can still seed it, and that capture then sticks.
    release_changes.freeze("9.9.9", _changes(fixes=2))
    out = release_changes.freeze("9.9.9", _changes(fixes=7))
    assert len(out["fixes"]) == 2


def test_freeze_is_per_version(store):
    release_changes.freeze("1.0", _changes(fixes=1))
    out = release_changes.freeze("2.0", _changes(features=4))
    assert len(out["features"]) == 4          # a different version captures independently
    assert len(release_changes.load_store()) == 2


def test_freeze_no_version_returns_normalized_input_without_persisting(store):
    out = release_changes.freeze("", {"fixes": [{"title": "x"}], "junk": 1})
    assert out == {"breaking": [], "fixes": [{"title": "x"}], "features": []}
    assert not config.RELEASE_CHANGES_FILE.exists()


def test_norm_coerces_shape_and_drops_junk(store):
    assert release_changes._norm(None) == {"breaking": [], "fixes": [], "features": []}
    assert release_changes._norm({"fixes": "not-a-list", "features": [1]}) == {
        "breaking": [], "fixes": [], "features": [1]}


def test_frozen_copy_drops_internal_captured_at(store):
    release_changes.freeze("3.0", _changes(fixes=1))
    # captured_at is persisted for pruning but must not leak back into the assessment payload.
    assert "captured_at" in release_changes.load_store()["3.0"]
    replayed = release_changes.freeze("3.0", _changes(fixes=9))
    assert "captured_at" not in replayed


def test_prune_keeps_most_recent_versions(store, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_KEEP_VERSIONS", 3)
    import time
    for i in range(5):
        release_changes.freeze(f"v{i}", _changes(fixes=1))
        time.sleep(0.01)   # distinct captured_at timestamps so ordering is deterministic
    kept = set(release_changes.load_store())
    assert len(kept) == 3
    assert "v4" in kept and "v0" not in kept
