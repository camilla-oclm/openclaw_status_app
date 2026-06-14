"""Tests for openclaw_status.render — deploy guard, markdown sanitize, smoke test, injection, archive."""
import os

import pytest

from openclaw_status import config, render


# ── _can_deploy ─────────────────────────────────────────────────────────────

def test_can_deploy_ok():
    ok, reasons = render._can_deploy({"assessment": {"confidence": "high"}, "validation_errors": []})
    assert ok is True
    assert reasons == []


def test_can_deploy_blocks_low_confidence():
    ok, reasons = render._can_deploy({"assessment": {"confidence": "low"}, "validation_errors": []})
    assert ok is False
    assert any("low" in r for r in reasons)


def test_can_deploy_blocks_validation_errors():
    ok, reasons = render._can_deploy(
        {"assessment": {"confidence": "high"}, "validation_errors": ["bad field"]}
    )
    assert ok is False
    assert any("validation error" in r for r in reasons)


# ── _deep_sanitize_markdown ─────────────────────────────────────────────────

def test_deep_sanitize_unescapes_markdown():
    assert render._deep_sanitize_markdown("a\\_b\\*c") == "a_b*c"


def test_deep_sanitize_recurses():
    out = render._deep_sanitize_markdown({"k": ["a\\_b", {"n": "x\\*y"}]})
    assert out == {"k": ["a_b", {"n": "x*y"}]}


# ── smoke_test_html ─────────────────────────────────────────────────────────

def _good_html(version="2026.6.1"):
    pad = "content " * 200  # push well over 1KB
    return (
        "<!DOCTYPE html><html><head><title>t</title></head>"
        f"<body><div>VERSION {version} {pad}</div></body></html>"
    )


def test_smoke_passes_on_valid_html(tmp_path):
    p = tmp_path / "index.html"
    p.write_text(_good_html())
    result = render.smoke_test_html(str(p), expected_version="2026.6.1")
    assert result["pass"] is True


def test_smoke_fails_on_missing_version(tmp_path):
    p = tmp_path / "index.html"
    p.write_text(_good_html(version="1.0.0"))
    result = render.smoke_test_html(str(p), expected_version="9.9.9")
    assert result["pass"] is False
    assert any(c["name"] == "version_present" and not c["passed"] for c in result["checks"])


def test_smoke_fails_on_tiny_file(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html></html>")
    result = render.smoke_test_html(str(p))
    assert result["pass"] is False


def test_smoke_fails_on_unbalanced_tags(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><head></head><body><div>" + ("x " * 400) + "</body></html>")
    result = render.smoke_test_html(str(p))
    assert result["pass"] is False
    assert any(c["name"] == "tag_balance" and not c["passed"] for c in result["checks"])


def test_smoke_missing_file(tmp_path):
    result = render.smoke_test_html(str(tmp_path / "nope.html"))
    assert result["pass"] is False


# ── _inject_data ────────────────────────────────────────────────────────────

def _parse_injected_json(html):
    import re, json
    m = re.search(r'<script id="assessment-data" type="application/json">(.*?)</script>',
                  html, re.DOTALL)
    body = m.group(1).replace("<\\/", "</")  # undo the </ escaping, like a browser
    return json.loads(body)


def test_inject_json_script_contract():
    tpl = '<html><body><script id="assessment-data" type="application/json">\n{}\n</script></body></html>'
    out = render._inject_data(tpl, {"version": "2026.6.1", "recommendation": "✅"})
    data = _parse_injected_json(out)
    assert data["version"] == "2026.6.1"
    assert data["recommendation"] == "✅"


def test_inject_escapes_script_close_in_data():
    tpl = '<script id="assessment-data" type="application/json">{}</script>'
    out = render._inject_data(tpl, {"thesis": "evil </script><script>alert(1)</script>"})
    # The raw injected text must not contain an unescaped </script> from the data
    body = out.split('application/json">', 1)[1].rsplit("</script>", 1)[0]
    assert "</script>" not in body
    # ...but it round-trips back to the original string
    assert _parse_injected_json(out)["thesis"] == "evil </script><script>alert(1)</script>"


def test_inject_legacy_var_data_contract():
    tpl = "<script>var DATA = {};\nrender();</script>"
    out = render._inject_data(tpl, {"version": "9.9.9"})
    assert '"version": "9.9.9"' in out
    assert "var DATA = {" in out
    assert "render();" in out  # surrounding code preserved


def test_inject_no_marker_returns_unchanged():
    tpl = "<html><body>no data slot here</body></html>"
    assert render._inject_data(tpl, {"version": "1.0"}) == tpl


# ── archive / per-version snapshots ─────────────────────────────────────────

def _page(version, extra=None):
    """A realistic rendered page: data injected via the production contract, so
    `</script>` inside string values is escaped just like the real renderer does."""
    data = {"version": version, "thesis": "danger </script><script>x</script>"}
    if extra:
        data.update(extra)
    tpl = '<html><body><script id="assessment-data" type="application/json">{}</script></body></html>'
    return render._inject_data(tpl, data)


def test_page_version_reads_injected_version(tmp_path):
    p = tmp_path / "index.html"
    p.write_text(_page("2026.6.6"))  # version survives even past an escaped </script>
    assert render._page_version(str(p)) == "2026.6.6"


def test_page_version_none_without_data_block(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><body>legacy, no injected data</body></html>")
    assert render._page_version(str(p)) is None


def test_page_version_rejects_path_traversal(tmp_path):
    p = tmp_path / "index.html"
    p.write_text(_page("../../etc/passwd"))  # contains "/" → not a safe filename
    assert render._page_version(str(p)) is None


def test_backup_archives_by_version(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "ARCHIVE_KEEP", 30)
    out = tmp_path / "index.html"
    out.write_text(_page("2026.6.6"))

    assert render._backup_existing(str(out)) == "2026.6.6"
    snap = tmp_path / "archive" / "2026.6.6.html"
    assert snap.exists()
    assert snap.stat().st_mode & 0o004  # world-readable for the static file server
    assert render._archived_versions() == ["2026.6.6"]
    assert not (tmp_path / "index.html.prev").exists()  # no stale .prev when archived


def test_backup_falls_back_to_prev_when_version_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    out = tmp_path / "index.html"
    out.write_text("<html><body>legacy page, no version</body></html>")

    assert render._backup_existing(str(out)) is None
    assert (tmp_path / "index.html.prev").exists()
    assert not (tmp_path / "archive").exists()


def test_backup_no_existing_page_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    assert render._backup_existing(str(tmp_path / "missing.html")) is None


def test_prune_keeps_newest_by_mtime(tmp_path, monkeypatch):
    arch = tmp_path / "archive"
    arch.mkdir()
    monkeypatch.setattr(config, "ARCHIVE_DIR", arch)
    monkeypatch.setattr(config, "ARCHIVE_KEEP", 3)
    for i in range(5):
        f = arch / f"v{i}.html"
        f.write_text("x")
        os.utime(f, (1000 + i, 1000 + i))  # v4 newest, v0 oldest

    render._prune_archive()
    assert sorted(p.name for p in arch.glob("*.html")) == ["v2.html", "v3.html", "v4.html"]


def test_archived_versions_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "does-not-exist")
    assert render._archived_versions() == []


# ── latest.json (runtime-fetch payload) ─────────────────────────────────────

def test_write_latest_json_sibling_of_page(tmp_path):
    import json
    out = tmp_path / "index.html"
    data = {"version": "2026.6.6", "recommendation": "⏸️", "thesis": "x </script> y"}
    render._write_latest_json(data, str(out))
    sibling = tmp_path / "latest.json"
    assert sibling.exists()
    # Round-trips exactly (no </ escaping — it's a real .json file, not inlined HTML).
    assert json.loads(sibling.read_text()) == data
    assert sibling.stat().st_mode & 0o004  # world-readable for the static file server


def test_make_world_readable_widens_to_644(tmp_path):
    p = tmp_path / "x.html"
    p.write_text("hi")
    os.chmod(p, 0o600)
    render._make_world_readable(str(p))
    assert os.stat(p).st_mode & 0o044 == 0o044  # group + world read


def test_build_data_injects_archived_versions(tmp_path, monkeypatch):
    arch = tmp_path / "archive"
    arch.mkdir()
    (arch / "2026.6.1.html").write_text("x")
    (arch / "2026.5.28.html").write_text("x")
    monkeypatch.setattr(config, "ARCHIVE_DIR", arch)
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")

    data = render._build_assessment_data({"assessment": {}, "version": "2026.6.6"}, {"sources": {}})
    assert sorted(data["archived_versions"]) == ["2026.5.28", "2026.6.1"]


# ── shareable artifacts + changelog ──────────────────────────────────────────

def test_extract_highlights_pulls_bullets():
    body = "intro\n### Highlights\n- First thing (#1)\n- Second thing\n### Fixes\n- not this\n"
    hl = render._extract_highlights(body)
    assert hl[0].startswith("First thing")
    assert "Second thing" in hl
    assert all("not this" not in h for h in hl)


def test_write_feed_emits_rss(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    out = tmp_path / "index.html"
    data = {"version": "2.0", "recommendation": "🔄", "archived_versions": [],
            "version_history": [{"version": "2.0", "recommendation": "🔄", "headline": "wait",
                                 "assessed_at": "2026-06-14T00:00:00+00:00"}]}
    render._write_feed(data, str(out))
    feed = (tmp_path / "feed.xml").read_text()
    assert "<rss" in feed and "<item>" in feed
    assert "OpenClaw v2.0: wait for next" in feed
    assert (tmp_path / "feed.xml").stat().st_mode & 0o004   # world-readable for Caddy


def test_write_badge_emits_svg(tmp_path):
    out = tmp_path / "index.html"
    render._write_badge({"version": "2.0", "recommendation": "⏸️"}, str(out))
    svg = (tmp_path / "badge.svg").read_text()
    assert svg.startswith("<svg")
    assert "OpenClaw v2.0" in svg and "skip this version" in svg
    assert "#e05d44" in svg   # red for skip


def test_build_data_extracts_stable_release_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    raw = {"sources": {"release_history": [
        {"tag": "v2.0", "published_at": "2026-06-10T00:00:00Z", "prerelease": False,
         "body": "### Highlights\n- New thing\n- Another\n"},
        {"tag": "v2.1-beta.1", "published_at": "2026-06-11T00:00:00Z", "prerelease": True,
         "body": "### Highlights\n- beta only\n"},
    ]}}
    rh = render._build_assessment_data({"assessment": {}, "version": "2.0"}, raw)["release_history"]
    assert len(rh) == 1                       # pre-release excluded
    assert rh[0]["version"] == "2.0"
    assert "New thing" in rh[0]["highlights"][0]


def test_build_data_strips_cost_from_public_payload(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "history.json").write_text(json.dumps(
        [{"version": "2026.6.1", "headline": "h", "reason": "r", "cost_usd": 0.02}]
    ))

    data = render._build_assessment_data(
        {"assessment": {}, "version": "2026.6.6", "usage": {"cost_usd": 0.03, "api_calls": 2}},
        {"sources": {}},
    )
    # Run cost must not surface on the public frontend...
    assert "cost_usd" not in data["usage"]
    assert all("cost_usd" not in h for h in data["version_history"])
    # ...but the rest of the usage/history payload is untouched.
    assert data["usage"]["api_calls"] == 2
    assert data["version_history"][0]["headline"] == "h"
