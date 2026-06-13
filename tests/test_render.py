"""Tests for openclaw_status.render — escaping, URL safety, deploy guard, smoke test."""
import pytest

from openclaw_status import render


# ── _esc ────────────────────────────────────────────────────────────────────

def test_esc_escapes_angle_brackets():
    assert render._esc("<b>") == "&lt;b&gt;"


def test_esc_empty_and_none():
    assert render._esc("") == ""
    assert render._esc(None) == ""


# ── _safe_url ───────────────────────────────────────────────────────────────

def test_safe_url_blocks_javascript():
    assert render._safe_url("javascript:alert(1)") == ""
    assert render._safe_url("  JavaScript:alert(1)") == ""


def test_safe_url_blocks_data_and_vbscript():
    assert render._safe_url("data:text/html,<script>") == ""
    assert render._safe_url("vbscript:msgbox") == ""


def test_safe_url_allows_https_and_preserves_case():
    url = "https://www.reddit.com/r/OpenClaw/comments/AbC123/Title"
    assert render._safe_url(url) == url


def test_safe_url_empty():
    assert render._safe_url("") == ""


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
