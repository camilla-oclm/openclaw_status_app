#!/usr/bin/env python3
"""External uptime watchdog for clawstat.us — run OFF the serving box.

The box's own Discord alerts die with the box, so this watches from outside
(the repo's scheduled GitHub Actions workflow, .github/workflows/watchdog.yml —
but it runs anywhere with Python 3.10+; stdlib only, no repo imports).

Checks, in order (any failure = DOWN, with one retry to ride out blips):
  1. GET /            → HTTP 200 and the page carries the assessment-data block
                        (the data-inject contract — proves a *rendered* page, not
                        a default vhost or an error page that happens to be 200).
  2. GET /latest.json → HTTP 200, valid JSON, a non-empty recommendation, and an
                        assessed_at younger than --stale-hours (default 30h: the
                        slowest healthy cadence is 24h + refine/latency margin).
                        A stale page with a live box means runs are silently
                        failing — exactly the state an outside watcher must catch.

Alerting is TRANSITION-based so a long outage doesn't spam: the caller passes the
prior completed run conclusions (newest first) via --history, and we ping the
webhook only on ok→down, down→ok, or every --realert-every-th consecutive failure
(a heartbeat so one missed ping can't mean silence). The webhook URL comes from
the WATCHDOG_WEBHOOK env var (a secret — never a flag, flags leak into process
lists and CI logs). Payload key mirrors lib.notify: Discord gets "content",
everything else "text". Exit code: 0 up, 1 down — so a scheduler that shows red
runs (GitHub Actions emails the owner on failure) is a second alert channel for
free. --test sends one labeled test ping and exits, to verify the webhook path.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "https://clawstat.us"
PAGE_MARKER = 'id="assessment-data"'
UA = "clawstat-watchdog/1 (+https://github.com/camilla-oclm/openclaw_status_app)"


def fetch(url: str, timeout: int = 20):
    """GET url → (status, text body). Raises on network/TLS/HTTP errors."""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def check_site(fetch_fn, base_url: str, stale_hours: float, now=None):
    """Run both checks. Returns (ok, reason) — reason is human-readable either way."""
    try:
        status, body = fetch_fn(base_url + "/")
    except Exception as e:
        return False, f"page fetch failed: {e.__class__.__name__}: {e}"[:300]
    if status != 200:
        return False, f"page returned HTTP {status}"
    if PAGE_MARKER not in body:
        return False, "page is missing the assessment-data block (not a rendered page)"

    try:
        status, body = fetch_fn(base_url + "/latest.json")
    except Exception as e:
        return False, f"latest.json fetch failed: {e.__class__.__name__}: {e}"[:300]
    if status != 200:
        return False, f"latest.json returned HTTP {status}"
    try:
        data = json.loads(body)
    except ValueError:
        return False, "latest.json is not valid JSON"
    if not data.get("recommendation"):
        return False, "latest.json carries no recommendation"
    assessed = _parse_iso(data.get("assessed_at") or "")
    if assessed is None:
        return False, "latest.json has no parseable assessed_at"
    if assessed.tzinfo is None:
        assessed = assessed.replace(tzinfo=timezone.utc)
    age_h = ((now or datetime.now(timezone.utc)) - assessed).total_seconds() / 3600
    if age_h > stale_hours:
        return False, f"assessment is STALE: {age_h:.0f}h old (limit {stale_hours:.0f}h) — runs may be failing"
    return True, (f"ok — {data.get('recommendation')} v{data.get('version', '?')}, "
                  f"assessed {age_h:.0f}h ago")


def check_with_retry(fetch_fn, base_url: str, stale_hours: float,
                     retry_wait: float = 10, now=None):
    """One retry before declaring DOWN, so a transient blip can't fire an alert."""
    ok, reason = check_site(fetch_fn, base_url, stale_hours, now=now)
    if ok:
        return ok, reason
    time.sleep(retry_wait)
    return check_site(fetch_fn, base_url, stale_hours, now=now)


def leading_failures(history: list) -> int:
    """Consecutive 'failure' conclusions at the head of the (newest-first) history."""
    n = 0
    for c in history:
        if c == "failure":
            n += 1
        else:
            break
    return n


def decide_alert(ok: bool, reason: str, history: list,
                 cadence_min: float, realert_every: int):
    """The transition logic. Returns the message to send, or None for silence."""
    fails = leading_failures(history)
    if not ok and fails == 0:
        return f"🔴 clawstat.us watchdog: DOWN — {reason}"
    if not ok:
        n = fails + 1                       # including this check
        if realert_every > 0 and n % realert_every == 0:
            return (f"🔴 clawstat.us watchdog: STILL down "
                    f"(~{n * cadence_min / 60:.0f}h, {n} checks) — {reason}")
        return None
    if ok and fails > 0:
        return (f"🟢 clawstat.us watchdog: recovered after "
                f"~{fails * cadence_min / 60:.1f}h ({fails} failed checks) — {reason}")
    return None


def webhook_payload(url: str, message: str) -> bytes:
    """Discord wants {"content"}, Slack/others {"text"} — same pick as lib.notify."""
    key = ("content" if "discord.com/api/webhooks" in url
           or "discordapp.com/api/webhooks" in url else "text")
    return json.dumps({key: message}).encode()


def send_webhook(url: str, message: str) -> bool:
    """Best-effort ping; never raises — the exit code must reflect the SITE state."""
    try:
        req = urllib.request.Request(
            url, data=webhook_payload(url, message),
            headers={"Content-Type": "application/json", "User-Agent": UA})
        urllib.request.urlopen(req, timeout=20).close()
        return True
    except Exception as e:
        print(f"⚠ webhook send failed: {e}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="External uptime watchdog for clawstat.us")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--stale-hours", type=float, default=30.0)
    p.add_argument("--history", default="",
                   help="prior completed run conclusions, newest first, comma-separated")
    p.add_argument("--cadence-min", type=float, default=15.0,
                   help="scheduler cadence, for duration estimates in alerts")
    p.add_argument("--realert-every", type=int, default=24,
                   help="heartbeat re-alert every Nth consecutive failure (0 = never)")
    p.add_argument("--retry-wait", type=float, default=10.0)
    p.add_argument("--test", action="store_true",
                   help="send one labeled test ping to the webhook and exit")
    args = p.parse_args(argv)

    webhook = os.environ.get("WATCHDOG_WEBHOOK", "")

    if args.test:
        if not webhook:
            print("--test: WATCHDOG_WEBHOOK is not set", file=sys.stderr)
            return 1
        sent = send_webhook(webhook, "🧪 clawstat.us watchdog: test ping — "
                                     "the alert path works. (Ignore.)")
        print("test ping sent" if sent else "test ping FAILED")
        return 0 if sent else 1

    ok, reason = check_with_retry(fetch, args.url, args.stale_hours,
                                  retry_wait=args.retry_wait)
    history = [c for c in args.history.split(",") if c]
    print(f"{'UP' if ok else 'DOWN'}: {reason}")

    message = decide_alert(ok, reason, history, args.cadence_min, args.realert_every)
    if message:
        if webhook:
            send_webhook(webhook, message)
            print(f"alerted: {message}")
        else:
            print(f"(no WATCHDOG_WEBHOOK set — would alert: {message})")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
