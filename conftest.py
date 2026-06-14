"""Pytest-wide safety fixtures.

`config.py` calls `load_dotenv()` on import, so when the suite runs on a machine
whose `.env` has `ALERT_WEBHOOK_URL` set, any test that reaches `lib.notify()` (the
budget gate, the failure paths, the cost-threshold alert) would fire a **real**
webhook POST — breaking the "hermetic, no network" guarantee and spamming the
channel (e.g. the $99 budget-gate fixture). This autouse fixture neutralises the
webhook for every test; the handful of tests that exercise `notify()` set their own
fake URL and stub `urlopen`, so they still work.
"""
import pytest

from openclaw_status import config


@pytest.fixture(autouse=True)
def _no_real_webhook(monkeypatch):
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", None)
