"""Staging-only providers whose ``fetch_account_usage`` hook answers from ``<HERMES_HOME>/lab-quota.json`` so the AI Usage page can be exercised against
available / failing / slow providers without a real account. Never shipped; copied into the lab home only."""
import json
import os
from datetime import datetime, timedelta, timezone

from providers import register_provider
from providers.base import ProviderProfile


def _mode(name):
    try:
        return json.load(open(os.path.join(os.environ["HERMES_HOME"], "lab-quota.json"), encoding="utf-8")).get(name, "ok")
    except (OSError, ValueError):
        return "ok"


class LabProfile(ProviderProfile):
    def fetch_account_usage(self, *, base_url=None, api_key=None):
        from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
        mode = _mode(self.name)
        if mode == "fail":
            raise RuntimeError("lab provider outage")
        now = datetime.now(timezone.utc)
        return AccountUsageSnapshot(
            provider=self.name, source="lab_usage_api", fetched_at=now, plan="Lab Pro", title="Lab limits",
            windows=(AccountUsageWindow(label="Session", used_percent=31.0, reset_at=now + timedelta(hours=2, minutes=14)),
                     AccountUsageWindow(label="Weekly", used_percent=92.5, reset_at=now + timedelta(days=3, hours=4))),
            details=("Credits balance: $4.20",))


register_provider(LabProfile(name="lab-quota", display_name="Lab Quota (staging)", base_url="http://127.0.0.1:1/v1", env_vars=("LAB_QUOTA_KEY",)))
register_provider(LabProfile(name="lab-down", display_name="Lab Outage (staging)", base_url="http://127.0.0.1:1/v1", env_vars=("LAB_DOWN_KEY",)))
