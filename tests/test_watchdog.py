"""deploy/watchdog.py — the external uptime watchdog's check + alert logic.

The script is standalone (stdlib-only, lives outside the package so a bare CI
runner can execute it without installing anything), so it's loaded by path.
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "watchdog", Path(__file__).resolve().parent.parent / "deploy" / "watchdog.py")
watchdog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(watchdog)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _fetcher(page=(200, f'<html>{watchdog.PAGE_MARKER}</html>'), latest=None,
             assessed_age_h=2.0):
    """Fake fetch: serves / and /latest.json from canned responses."""
    if latest is None:
        latest = (200, json.dumps({
            "recommendation": "⏸️", "version": "2026.6.11",
            "assessed_at": (NOW - timedelta(hours=assessed_age_h)).isoformat(),
        }))

    def fake(url, timeout=20):
        if url.endswith("/latest.json"):
            resp = latest
        else:
            resp = page
        if isinstance(resp, Exception):
            raise resp
        return resp
    return fake


# ── check_site ───────────────────────────────────────────────────────────────

def test_check_site_healthy():
    ok, reason = watchdog.check_site(_fetcher(), "https://x", 30, now=NOW)
    assert ok is True
    assert "⏸️" in reason and "2026.6.11" in reason


def test_check_site_page_failures():
    down = watchdog.check_site(_fetcher(page=(500, "boom")), "https://x", 30, now=NOW)
    assert down == (False, "page returned HTTP 500")
    ok, reason = watchdog.check_site(
        _fetcher(page=(200, "<html>default vhost</html>")), "https://x", 30, now=NOW)
    assert ok is False and "assessment-data" in reason
    ok, reason = watchdog.check_site(
        _fetcher(page=OSError("connection refused")), "https://x", 30, now=NOW)
    assert ok is False and "page fetch failed" in reason


def test_check_site_latest_json_failures():
    ok, reason = watchdog.check_site(
        _fetcher(latest=(200, "not json")), "https://x", 30, now=NOW)
    assert ok is False and "not valid JSON" in reason
    ok, reason = watchdog.check_site(
        _fetcher(latest=(200, json.dumps({"assessed_at": NOW.isoformat()}))),
        "https://x", 30, now=NOW)
    assert ok is False and "no recommendation" in reason


def test_check_site_staleness_boundary():
    # 29h old under a 30h limit → healthy; 31h → stale (the outside-watcher case:
    # box alive and serving, pipeline silently dead)
    ok, _ = watchdog.check_site(_fetcher(assessed_age_h=29), "https://x", 30, now=NOW)
    assert ok is True
    ok, reason = watchdog.check_site(_fetcher(assessed_age_h=31), "https://x", 30, now=NOW)
    assert ok is False and "STALE" in reason


def test_check_with_retry_recovers_on_blip(monkeypatch):
    calls = {"n": 0}
    good = _fetcher()

    def flaky(url, timeout=20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("blip")
        return good(url, timeout)
    ok, _ = watchdog.check_with_retry(flaky, "https://x", 30, retry_wait=0, now=NOW)
    assert ok is True          # one transient failure never alerts


# ── decide_alert (the transition matrix) ─────────────────────────────────────

def test_alert_transitions():
    d = watchdog.decide_alert
    # steady ok → silence
    assert d(True, "ok", ["success", "success"], 15, 24) is None
    # fresh outage (prev success, or first run ever) → DOWN ping
    assert "DOWN" in d(False, "HTTP 502", ["success"], 15, 24)
    assert "DOWN" in d(False, "HTTP 502", [], 15, 24)
    # ongoing outage → silence between heartbeats…
    assert d(False, "HTTP 502", ["failure"] * 3, 15, 24) is None
    # …heartbeat on the realert boundary (24th consecutive check ≈ 6h at 15min)
    assert "STILL down" in d(False, "HTTP 502", ["failure"] * 23, 15, 24)
    # recovery → ping with an approximate duration
    msg = d(True, "ok", ["failure"] * 8, 15, 24)
    assert "recovered" in msg and "2.0h" in msg
    # a non-failure conclusion (cancelled run) breaks the streak → treated as fresh
    assert "DOWN" in d(False, "HTTP 502", ["cancelled", "failure"], 15, 24)


def test_realert_never_when_disabled():
    assert watchdog.decide_alert(False, "x", ["failure"] * 23, 15, 0) is None


# ── webhook payload + main wiring ────────────────────────────────────────────

def test_webhook_payload_key_selection():
    assert json.loads(watchdog.webhook_payload(
        "https://discord.com/api/webhooks/1/x", "m")) == {"content": "m"}
    assert json.loads(watchdog.webhook_payload(
        "https://hooks.slack.com/services/x", "m")) == {"text": "m"}


def test_state_file_lifecycle(tmp_path, monkeypatch, capsys):
    """Cron mode end-to-end: fresh outage alerts once, stays quiet while down,
    alerts again on recovery, and the state file tracks the consecutive count."""
    state = str(tmp_path / "state.json")
    monkeypatch.delenv("WATCHDOG_WEBHOOK", raising=False)
    args = ["--retry-wait", "0", "--state-file", state]

    monkeypatch.setattr(watchdog, "fetch", _fetcher(page=(502, "bad gateway")))
    assert watchdog.main(args) == 1                      # ok → down: alert
    assert "would alert" in capsys.readouterr().out
    saved = json.loads(open(state).read())
    assert (saved["status"], saved["fails"]) == ("down", 1)

    assert watchdog.main(args) == 1                      # still down: silence
    assert "would alert" not in capsys.readouterr().out
    assert json.loads(open(state).read())["fails"] == 2

    monkeypatch.setattr(watchdog, "fetch", _fetcher())
    assert watchdog.main(args) == 0                      # down → ok: recovery alert
    out = capsys.readouterr().out
    assert "recovered" in out
    saved = json.loads(open(state).read())
    assert (saved["status"], saved["fails"]) == ("ok", 0)


def test_state_file_missing_or_corrupt_reads_as_ok(tmp_path):
    missing = str(tmp_path / "nope.json")
    assert watchdog.load_state(missing) == {"status": "ok", "fails": 0}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    # fail-toward-alerting: a lost state file means the next real outage still pings
    assert watchdog.load_state(str(bad)) == {"status": "ok", "fails": 0}


def test_webhook_file_wins_over_env(tmp_path, monkeypatch):
    hook = tmp_path / "webhook"
    hook.write_text("https://discord.com/api/webhooks/1/from-file\n")
    monkeypatch.setenv("WATCHDOG_WEBHOOK", "https://discord.com/api/webhooks/1/from-env")
    sent = []
    monkeypatch.setattr(watchdog, "send_webhook", lambda url, msg: sent.append(url) or True)
    assert watchdog.main(["--test", "--webhook-file", str(hook)]) == 0
    assert sent == ["https://discord.com/api/webhooks/1/from-file"]


def test_main_exit_codes_and_no_send_without_webhook(monkeypatch, capsys):
    monkeypatch.delenv("WATCHDOG_WEBHOOK", raising=False)
    monkeypatch.setattr(watchdog, "send_webhook",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no send")))
    monkeypatch.setattr(watchdog, "fetch", _fetcher())
    assert watchdog.main(["--retry-wait", "0"]) == 0
    monkeypatch.setattr(watchdog, "fetch", _fetcher(page=(503, "down")))
    assert watchdog.main(["--retry-wait", "0", "--history", "success"]) == 1
    out = capsys.readouterr().out
    assert "would alert" in out       # decision made, send correctly skipped
