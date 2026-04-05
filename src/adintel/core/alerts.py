from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen

from adintel.core.settings import AppSettings
from adintel.db.repositories import CollectionHealthRepository

logger = logging.getLogger("adintel.alerts")


def check_and_notify(
    settings: AppSettings,
    health_repo: CollectionHealthRepository,
    advertiser_name: str,
    platform: str,
) -> list[dict]:
    """Check collection health for an advertiser and send webhook alerts if thresholds are exceeded.

    Returns the list of alerts found (empty list if healthy).
    """
    health = health_repo.get_health(advertiser_name, platform)

    alerts: list[dict] = []

    if settings.alert_on_failure and health["consecutive_failures"] >= settings.max_consecutive_failures:
        alerts.append({
            "advertiser_name": advertiser_name,
            "platform": platform,
            "alert_type": "consecutive_failures",
            "severity": "critical",
            "message": (
                f"{health['consecutive_failures']} consecutive failures for {advertiser_name}/{platform}. "
                f"Last error: {health['last_error_message']}"
            ),
        })

    if settings.alert_on_staleness and health["hours_since_success"] is not None:
        if health["hours_since_success"] >= settings.stale_critical_hours:
            alerts.append({
                "advertiser_name": advertiser_name,
                "platform": platform,
                "alert_type": "stale_data",
                "severity": "critical",
                "message": f"Data for {advertiser_name}/{platform} is {health['hours_since_success']}h old (critical threshold: {settings.stale_critical_hours}h).",
            })
        elif health["hours_since_success"] >= settings.stale_warning_hours:
            alerts.append({
                "advertiser_name": advertiser_name,
                "platform": platform,
                "alert_type": "stale_data",
                "severity": "warning",
                "message": f"Data for {advertiser_name}/{platform} is {health['hours_since_success']}h old (warning threshold: {settings.stale_warning_hours}h).",
            })

    if alerts and settings.alert_webhook_url:
        _send_webhook(settings.alert_webhook_url, alerts)

    return alerts


def _send_webhook(url: str, alerts: list[dict]) -> None:
    """POST alerts to a webhook URL (Slack, Discord, generic JSON)."""
    payload = {
        "text": f"AdIntel: {len(alerts)} alert(s)",
        "alerts": alerts,
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            logger.info("Webhook delivered: HTTP %d", resp.status)
    except Exception as exc:
        logger.error("Webhook delivery failed: %s", exc)
