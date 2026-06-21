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


def test_norm_platforms_keeps_known_tokens_and_drops_junk():
    assert render._norm_platforms(["Linux", "WIN", "osx", "discord", "haxxor"]) == \
        ["linux", "windows", "macos", "discord"]
    assert render._norm_platforms(["all", "all"]) == ["all"]       # dedup
    assert render._norm_platforms("linux") == []                   # not a list
    assert render._norm_platforms(None) == []


def test_derive_platforms_from_text():
    # specific surface by keyword (title/body/labels)
    assert render._derive_platforms({"title": "Docker self-hosted deploy fails"}) == ["linux"]
    assert render._derive_platforms({"title": "x", "body": "crashes on Windows only"}) == ["windows"]
    assert render._derive_platforms({"title": "Discord bot offline"}) == ["discord"]
    assert render._derive_platforms({"title": "x", "labels": [{"name": "platform:macos"}]}) == ["macos"]
    # serious core regression naming no surface -> "all"
    assert render._derive_platforms({"title": "memory index reindex race"},
                                    severity="critical", category="regression") == ["all"]
    # a channel-specific crash is NOT "all"
    assert render._derive_platforms({"title": "msteams channel crash-loop"},
                                    severity="critical", category="regression") == []
    # benign, unattributed -> nothing
    assert render._derive_platforms({"title": "typo in docs"}, severity="low") == []


def test_derive_platforms_no_substring_false_positives():
    # regression (issue #92843): a macOS report whose body mentions
    # `tools.exec.security` must NOT be tagged Windows — `\.exe` is a file
    # extension, not the `.exe` buried inside `.exec`.
    macos_exec = {"title": '`security: "allowlist"` exec agents lose network access',
                  "body": 'macOS host, single-gateway. Some agents tools.exec.security: '
                          '"allowlist", others run unrestricted (full).'}
    assert render._derive_platforms(macos_exec) == ["macos"]
    # a genuine Windows .exe is still caught
    assert render._derive_platforms({"title": "x", "body": "openclaw.exe crashes on launch"}) \
        == ["windows"]
    # tokens only fire as whole words, never as substrings of a larger one
    assert render._derive_platforms({"title": "x", "body": "the winner takes the macports route"}) == []
    assert render._derive_platforms({"title": "grapple with the syntax"}) == []


def test_derive_platform_impact_from_tags():
    issues = [
        {"platforms": ["linux"], "severity": "critical"},   # linux ← critical
        {"platforms": ["all"], "severity": "high"},          # everyone ← high floor
        {"platforms": ["discord"], "severity": "medium"},    # discord-specific medium
    ]
    pi = render._derive_platform_impact(issues)
    assert pi["linux"] == "high"          # critical → "high" bucket
    assert pi["discord"] == "high"        # max(medium-specific, high-from-all) → high
    assert pi["windows"] == "high"        # inherits the cross-cutting "all" high
    # worst-severity bucketing without an "all" floor
    pi2 = render._derive_platform_impact([{"platforms": ["slack"], "severity": "low"}])
    assert pi2 == {"slack": "low"}        # only slack hit, low; others absent
    # no platform tags anywhere -> {} so the caller falls back to the analyst's value
    assert render._derive_platform_impact([{"severity": "critical"}]) == {}
    assert render._derive_platform_impact([]) == {}


def test_build_derives_platforms_when_analyst_absent():
    raw = {"sources": {"github_issues": [
        {"number": 7, "title": "Docker build broken", "severity": "high", "category": "regression"}]}}
    assessment = {"assessment": {"known_issues": [
        {"number": 7, "title": "Docker build broken", "severity": "high", "category": "regression"}]},
        "version": "2.0"}
    ki = render._build_assessment_data(assessment, raw)["known_issues"][0]
    assert ki["platforms"] == ["linux"]   # derived, no analyst tag present


def test_derive_components_from_labels_and_keywords():
    # authoritative repo label wins
    assert "sessions" in render._derive_components({"title": "x", "labels": ["impact:session-state"]})
    assert "auth" in render._derive_components({"title": "x", "labels": ["impact:auth-provider"]})
    assert "channels" in render._derive_components({"title": "x", "labels": ["impact:message-loss"]})
    # keyword detection
    assert render._derive_components({"title": "Gateway worker memory leak"})[:2] == ["memory", "gateway"] or \
        set(render._derive_components({"title": "Gateway worker memory leak"})) == {"memory", "gateway"}
    assert "models" in render._derive_components({"title": "DeepSeek prompt cache broken"})
    assert "tasks" in render._derive_components({"title": "Isolated cron fails"})
    # capped at 2, ordered by priority
    many = render._derive_components({"title": "auth keyed-store plugin self-hosted deploy channel"})
    assert len(many) == 2 and many[0] == "auth"
    # nothing recognizable
    assert render._derive_components({"title": "typo in readme"}) == []


def test_norm_components_drops_junk():
    assert render._norm_components(["Gateway", "models", "nope"]) == ["gateway", "models"]
    assert render._norm_components("gateway") == []


def test_build_attaches_components():
    raw = {"sources": {"github_issues": [
        {"number": 9, "title": "Gateway restart loses session", "labels": ["impact:session-state"]}]}}
    a = {"assessment": {"known_issues": [{"number": 9, "title": "Gateway restart loses session"}]}, "version": "2.0"}
    ki = render._build_assessment_data(a, raw)["known_issues"][0]
    assert "sessions" in ki["components"] and "gateway" in ki["components"]


def test_build_passes_issue_platforms_through():
    raw = {"sources": {"github_issues": [{"number": 7}]}}
    assessment = {"assessment": {"known_issues": [
        {"number": 7, "title": "boom", "severity": "high", "platforms": ["Linux", "all", "nope"]}
    ]}, "version": "2.0"}
    ki = render._build_assessment_data(assessment, raw)["known_issues"][0]
    assert ki["platforms"] == ["linux", "all"]


def test_build_detects_workaround_signal():
    raw = {"sources": {"github_issues": [
        {"number": 1, "title": "crash on start", "body": "No fix yet, but as a workaround you can downgrade."},
        {"number": 2, "title": "slow boot", "body": "just slow, nothing notable here"},
    ]}}
    a = {"assessment": {"known_issues": [
        {"number": 1, "title": "crash on start"}, {"number": 2, "title": "slow boot"}]}, "version": "2.0"}
    ki = {i["number"]: i for i in render._build_assessment_data(a, raw)["known_issues"]}
    assert ki[1]["has_workaround"] is True
    assert ki[2]["has_workaround"] is False


def test_build_exposes_schema_version_and_release_urls():
    raw = {"sources": {
        "latest_release": {"tag": "v2.0", "url": "https://gh/r/v2.0", "published_at": "2026-06-12T00:00:00Z"},
        "latest_prerelease": {"tag": "v2.1-beta", "url": "https://gh/r/v2.1-beta", "published_at": "2026-06-14T00:00:00Z"},
    }}
    data = render._build_assessment_data({"assessment": {}, "version": "2.0"}, raw)
    assert data["schema_version"] == render.SCHEMA_VERSION
    assert data["latest_release"]["url"] == "https://gh/r/v2.0"
    assert data["latest_release"]["version"] == "2.0"   # populated, not null
    assert data["latest_prerelease"]["url"] == "https://gh/r/v2.1-beta"


def test_build_normalizes_retired_wait_verdict():
    # A retired 🔄 (old data / stray model output) renders as ⏸️ so the page only
    # ever shows the 3 supported verdicts (no orphaned glyph / broken risk bar).
    assert render._norm_rec("🔄") == "⏸️"
    assert render._norm_rec("⚠️") == "⚠️"
    data = render._build_assessment_data(
        {"assessment": {"recommendation": "🔄"}, "version": "2.0"}, {"sources": {}})
    assert data["recommendation"] == "⏸️"


# ── release freshness (just-dropped → preliminary verdict) ───────────────────

def test_within_fresh_window_basic():
    lr = {"tag": "v2026.6.8", "published_at": "2026-06-16"}
    assert render._within_fresh_window("2026.6.8", "2026-06-16T10:00:00+00:00", lr) is True
    # outside the publish-date window
    assert render._within_fresh_window("2026.6.8", "2026-06-20", lr) is False
    # not the latest release
    assert render._within_fresh_window("2026.6.6", "2026-06-16", lr) is False
    # unknown publish date
    assert render._within_fresh_window("2026.6.8", "2026-06-16",
                                       {"tag": "v2026.6.8", "published_at": ""}) is False


def test_release_freshness_fresh_within_window():
    ki = [{"affects_version": True}, {"affects_version": False}, {"affects_version": False}]
    f = render._release_freshness(
        "2026.6.8", "2026-06-16T10:00:00+00:00",
        {"tag": "v2026.6.8", "published_at": "2026-06-16"}, ki)
    assert f["fresh"] is True
    assert f["days_since_release"] == 0
    assert f["version_specific_issues"] == 1
    assert f["carried_over_issues"] == 2


def test_release_freshness_expires_after_window():
    # FRESH_RELEASE_DAYS days after publish is still fresh; one day past is not.
    edge = render._release_freshness(
        "2026.6.8", "2026-06-18", {"tag": "v2026.6.8", "published_at": "2026-06-16"}, [])
    past = render._release_freshness(
        "2026.6.8", "2026-06-19", {"tag": "v2026.6.8", "published_at": "2026-06-16"}, [])
    assert config.FRESH_RELEASE_DAYS == 2
    assert edge["fresh"] is True and edge["days_since_release"] == 2
    assert past["fresh"] is False and past["days_since_release"] == 3


def test_release_freshness_requires_matching_version_and_known_date():
    # The assessed page must be about the latest release, with a parseable publish date.
    mismatch = render._release_freshness(
        "2026.6.6", "2026-06-16", {"tag": "v2026.6.8", "published_at": "2026-06-16"}, [])
    no_date = render._release_freshness(
        "2026.6.8", "2026-06-16", {"tag": "v2026.6.8", "published_at": ""}, [])
    assert mismatch["fresh"] is False
    assert no_date["fresh"] is False and no_date["days_since_release"] is None


def test_release_freshness_clamps_future_publish_to_zero():
    # A publish date "after" the assessment clock shouldn't go negative — treat as same-day.
    f = render._release_freshness(
        "2026.6.8", "2026-06-16", {"tag": "v2026.6.8", "published_at": "2026-06-17"}, [])
    assert f["fresh"] is True and f["days_since_release"] == 0


def test_release_freshness_retires_after_max_runs():
    # Even inside the publish-date window, the banner retires once the version has been
    # assessed MORE than FRESH_RELEASE_MAX_RUNS times (the 4th run ≈ 24h at 6h cadence) —
    # by then enough version-specific bugs are filed that "early read" is stale.
    lr = {"tag": "v2026.6.8", "published_at": "2026-06-16"}
    assert config.FRESH_RELEASE_MAX_RUNS == 3
    third = render._release_freshness("2026.6.8", "2026-06-16", lr, [], run_count=3)
    fourth = render._release_freshness("2026.6.8", "2026-06-16", lr, [], run_count=4)
    assert third["fresh"] is True and third["runs_assessed"] == 3
    assert fourth["fresh"] is False and fourth["runs_assessed"] == 4
    # run_count=0 means "unknown" (e.g. no timeline yet) and must NOT retire the banner.
    assert render._release_freshness("2026.6.8", "2026-06-16", lr, [], run_count=0)["fresh"] is True


def test_build_exposes_freshness():
    raw = {"sources": {"latest_release": {
        "tag": "v9.9", "url": "https://gh/r/v9.9", "published_at": "2026-06-16T00:00:00Z"}}}
    data = render._build_assessment_data(
        {"assessment": {}, "version": "9.9", "assessed_at": "2026-06-16T12:00:00+00:00"}, raw)
    assert data["freshness"]["fresh"] is True


def test_seo_body_includes_fresh_note_only_when_fresh():
    base = {"version": "2026.6.8", "recommendation": "⚠️", "known_issues": []}
    fresh = render._seo_body({**base, "freshness": {
        "fresh": True, "days_since_release": 0, "version_specific_issues": 0}})
    stale = render._seo_body({**base, "freshness": {"fresh": False}})
    assert "Fresh release." in fresh and "Back up before you update" in fresh
    assert "Fresh release." not in stale


# ── shareable artifacts + changelog ──────────────────────────────────────────

def test_extract_highlights_pulls_bullets():
    body = "intro\n### Highlights\n- First thing (#1)\n- Second thing\n### Fixes\n- not this\n"
    hl = render._extract_highlights(body)
    assert hl[0].startswith("First thing")
    assert "Second thing" in hl
    assert all("not this" not in h for h in hl)


def test_extract_highlights_truncates_on_word_boundary():
    # A long bullet must not be sliced mid-word; it gets an ellipsis instead.
    long = "word " * 80  # ~400 chars, well over the 240 cap
    body = "### Highlights\n- " + long + "\n"
    h = render._extract_highlights(body)[0]
    assert len(h) <= 241          # 240 cap + the ellipsis
    assert h.endswith("…")
    assert h.replace("…", "").endswith("word")   # cut landed on a word boundary, not "wo…"


def test_write_feed_emits_rss(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    out = tmp_path / "index.html"
    data = {"version": "2.0", "recommendation": "⏸️", "archived_versions": [],
            "version_history": [{"version": "2.0", "recommendation": "⏸️", "headline": "skip",
                                 "assessed_at": "2026-06-14T00:00:00+00:00"}]}
    render._write_feed(data, str(out))
    feed = (tmp_path / "feed.xml").read_text()
    assert "<rss" in feed and "<item>" in feed
    assert "OpenClaw v2.0: skip this version" in feed
    assert (tmp_path / "feed.xml").stat().st_mode & 0o004   # world-readable for Caddy


def test_feed_links_are_individually_addressable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    out = tmp_path / "index.html"
    data = {"version": "2.0", "recommendation": "🔄", "archived_versions": ["1.9"],
            "version_history": [
                {"version": "2.0", "recommendation": "🔄", "headline": "now", "assessed_at": "2026-06-14T03:00:00+00:00"},
                {"version": "1.9", "recommendation": "⏸️", "headline": "old", "assessed_at": "2026-06-13T00:00:00+00:00"},
                {"version": "1.5", "recommendation": "✅", "headline": "older", "assessed_at": "2026-06-10T00:00:00+00:00"},
            ]}
    render._write_feed(data, str(out))
    feed = (tmp_path / "feed.xml").read_text()
    assert "https://example.test/archive/1.9.html" in feed                   # snapshotted past → archive
    assert "https://github.com/openclaw/openclaw/releases/tag/v1.5" in feed   # un-snapshotted past → GH release


def test_write_badge_emits_svg(tmp_path):
    out = tmp_path / "index.html"
    render._write_badge({"version": "2.0", "recommendation": "⏸️"}, str(out))
    svg = (tmp_path / "badge.svg").read_text()
    assert svg.startswith("<svg")
    assert "OpenClaw v2.0" in svg and "skip this version" in svg
    assert "#e05d44" in svg   # red for skip


def test_write_llms_emits_agent_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    out = tmp_path / "index.html"
    data = {
        "version": "2.0", "recommendation": "⏸️", "confidence": "high",
        "assessed_at": "2026-06-14T00:00:00+00:00",
        "headline": "Skip it for now.", "thesis": "Multiple regressions.",
        "evidence": {"against_updating": ["build breaks #123"]},
        "known_issues": [{"number": 123, "title": "Build fails", "severity": "high",
                          "category": "regression", "reactions": 9}],
        "changes": {"features": [{"title": "New GUI"}], "fixes": [], "breaking": []},
        "platform_impact": {"windows": "high"}, "sentiment_summary": "grumpy",
    }
    render._write_llms(data, str(out))

    txt = (tmp_path / "llms.txt").read_text()
    assert txt.startswith("# ClawStat.us")
    assert "Skip this version" in txt and "OpenClaw v2.0" in txt
    assert "https://example.test/llms-full.txt" in txt and "https://example.test/latest.json" in txt
    assert (tmp_path / "llms.txt").stat().st_mode & 0o004   # world-readable for Caddy

    full = (tmp_path / "llms-full.txt").read_text()
    assert "# ClawStat.us — OpenClaw v2.0" in full
    assert "## Why this verdict" in full and "Multiple regressions." in full
    assert "#123" in full and "Build fails" in full and "unfixed" in full   # regression w/o fix
    assert "New GUI" in full and "windows: high" in full


def test_inject_seo_fills_title_meta_jsonld_and_ssr():
    tpl = ('<html><head><title>x</title><!--SEO-HEAD--></head>'
           '<body><main><div id="app"><!--SSR--></div></main></body></html>')
    data = {"version": "2.0", "recommendation": "⏸️", "confidence": "high",
            "headline": "Skip it for now.", "thesis": "Multiple regressions.\n\nSecond para.",
            "known_issues": [{"number": 123, "title": "Boom", "severity": "high"}],
            "assessed_at": "2026-06-14T00:00:00+00:00"}
    out = render._inject_seo(tpl, data)
    assert "<title>Should you update OpenClaw v2.0? Skip this version — ClawStat.us</title>" in out
    assert 'name="description"' in out and 'property="og:title"' in out and 'name="twitter:card"' in out
    assert 'rel="canonical"' in out and 'application/ld+json' in out
    assert "<h1>Should you update OpenClaw v2.0? — Skip this version</h1>" in out
    assert "Multiple regressions." in out and "Second para." not in out  # only first thesis para
    assert "#123" in out and "Boom" in out
    assert "<!--SSR-->" not in out and "<!--SEO-HEAD-->" not in out


def test_seo_escapes_untrusted_text():
    data = {"version": "2.0", "recommendation": "⏸️",
            "headline": "<script>alert(1)</script>",
            "known_issues": [{"number": 1, "title": "<img src=x onerror=alert(1)>", "severity": "high"}]}
    body, head = render._seo_body(data), render._seo_head(data)
    assert "<script>alert" not in body and "&lt;script&gt;" in body     # headline escaped in body
    assert "<img src=x" not in body and "&lt;img" in body               # issue title escaped in body
    assert "<script>alert" not in head and "&lt;script&gt;" in head     # headline reused in head meta, escaped
    # JSON-LD can't be broken out of its <script> even with a hostile version string.
    ld = render._json_ld({"version": "x</script><script>evil", "recommendation": "⏸️"})
    assert ld.count("</script>") == 1                  # only the real closing tag
    assert "\\u003c/script" in ld                      # the hostile one was \u-escaped


def test_seo_includes_evergreen_targeting():
    data = {"version": "2026.6.6", "recommendation": "⏸️", "confidence": "medium",
            "headline": "Skip this version.", "known_issues": []}
    # Evergreen, version-agnostic Q&A in the JSON-LD (matches generic search intent).
    ld = render._json_ld(data)
    assert "Should I update OpenClaw?" in ld
    assert "How do I know if a new OpenClaw release is safe to update to?" in ld
    assert "Should you update OpenClaw v2026.6.6?" in ld   # version-specific still present
    # Evergreen copy in the crawlable server-rendered body.
    body = render._seo_body(data)
    assert "Is the latest OpenClaw update safe" in body
    assert "whether you should update OpenClaw" in body and "wait for the next one" in body


def test_archive_self_canonicalizes_past_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "ARCHIVE_KEEP", 30)
    out = tmp_path / "index.html"
    # An existing page built for v1.9 with the usual homepage canonical/og:url.
    page = ('<html><head><link rel="canonical" href="https://example.test/">'
            '<meta property="og:url" content="https://example.test/">'
            '<script id="assessment-data" type="application/json">{"version": "1.9"}</script>'
            '</head><body></body></html>')
    out.write_text(page)

    # Rendering a *newer* version (2.0) archives v1.9 → it must self-canonicalise.
    assert render._backup_existing(str(out), new_version="2.0") == "1.9"
    arch = (tmp_path / "archive" / "1.9.html").read_text()
    assert '<link rel="canonical" href="https://example.test/archive/1.9.html">' in arch
    assert '<meta property="og:url" content="https://example.test/archive/1.9.html">' in arch

    # Re-rendering the *same* version keeps canonical → "/" (snapshot == homepage).
    out.write_text(page.replace('"1.9"', '"2.0"'))
    assert render._backup_existing(str(out), new_version="2.0") == "2.0"
    arch2 = (tmp_path / "archive" / "2.0.html").read_text()
    assert '<link rel="canonical" href="https://example.test/">' in arch2


def test_timeline_from_history_maps_fields():
    h = {"version": "2.0", "assessed_at": "2026-06-14T00:00:00+00:00", "recommendation": "⏸️",
         "confidence": "high", "issues": 10, "regressions": 7, "high": 6, "cost_usd": 0.05}
    r = render._timeline_from_history(h)
    assert r["version"] == "2.0" and r["issues"] == 10 and r["regressions"] == 7
    # history only stores combined high+critical, no med/low split → approximate
    assert r["high"] == 6 and r["low"] == 4 and r["critical"] == 0 and r["medium"] == 0
    assert r["cost_usd"] == 0.05 and r["approx"] is True


def test_write_sitemap_and_robots(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_URL", "https://example.test")
    out = tmp_path / "index.html"
    render._write_sitemap({"assessed_at": "2026-06-14T00:00:00+00:00",
                           "archived_versions": ["2.0", "1.9"]}, str(out))
    sm = (tmp_path / "sitemap.xml").read_text()
    assert "<loc>https://example.test/</loc>" in sm
    assert "archive/2.0.html" in sm and "archive/1.9.html" in sm
    assert (tmp_path / "sitemap.xml").stat().st_mode & 0o004
    render._write_robots(str(out))
    assert "Sitemap: https://example.test/sitemap.xml" in (tmp_path / "robots.txt").read_text()


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
        {"assessment": {}, "version": "2026.6.6",
         "usage": {"cost_usd": 0.03, "latency_ms": 185889, "api_calls": 2, "tokens_in": 9}},
        {"sources": {}},
    )
    # Run cost and latency are internal — they must not surface on the public frontend...
    assert "cost_usd" not in data["usage"]
    assert "latency_ms" not in data["usage"]
    assert all("cost_usd" not in h for h in data["version_history"])
    # ...but token/model-call counts (which evidence the real pipeline) are untouched.
    assert data["usage"]["api_calls"] == 2
    assert data["usage"]["tokens_in"] == 9
    assert data["version_history"][0]["headline"] == "h"


def test_build_data_normalizes_retired_wait_in_history_and_timeline(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(config, "TIMELINE_FILE", tmp_path / "timeline.json")
    (tmp_path / "history.json").write_text(json.dumps(
        [{"version": "2026.6.6", "recommendation": "🔄", "headline": "h",
          "assessed_at": "2026-06-16T00:00:00+00:00"}]))
    (tmp_path / "timeline.json").write_text(json.dumps(
        [{"t": "2026-06-16T00:00:00+00:00", "recommendation": "🔄", "issues": 5},
         {"t": "2026-06-18T00:00:00+00:00", "recommendation": "⏸️", "issues": 7}]))
    data = render._build_assessment_data({"assessment": {}, "version": "2026.6.6"}, {"sources": {}})
    # Both the past-verdicts list and the Trends timeline drop the retired 🔄.
    assert [h["recommendation"] for h in data["version_history"]] == ["⏸️"]
    assert [t["recommendation"] for t in data["timeline"]] == ["⏸️", "⏸️"]
