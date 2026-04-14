#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import smtplib
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib import error, request


DEFAULT_URL = "https://yongchengmu-adintel-mcp.hf.space/"
DEFAULT_STATE_FILE = Path("state/hf_mcp_monitor.json")


@dataclass
class MonitorState:
    last_check_at: str | None = None
    last_success_at: str | None = None
    last_status: str | None = None
    last_http_status: int | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    next_check_at: str | None = None
    next_delay_seconds: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adaptive health monitor for the AdIntel Hugging Face MCP Space."
    )
    parser.add_argument("--url", default=os.getenv("HF_MCP_URL", DEFAULT_URL))
    parser.add_argument(
        "--state-file",
        default=os.getenv("HF_MCP_MONITOR_STATE", str(DEFAULT_STATE_FILE)),
        help="Path to a small JSON file that stores the last check and next scheduled check.",
    )
    parser.add_argument(
        "--success-hours",
        type=float,
        default=float(os.getenv("HF_MCP_SUCCESS_HOURS", "4")),
        help="Delay after a successful check.",
    )
    parser.add_argument(
        "--failure-minutes",
        type=float,
        default=float(os.getenv("HF_MCP_FAILURE_MINUTES", "30")),
        help="Delay after a failed check.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("HF_MCP_TIMEOUT_SECONDS", "30")),
        help="HTTP timeout for the probe request.",
    )
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="Run one probe and exit instead of looping forever.",
    )
    parser.add_argument(
        "--sleep-buffer-seconds",
        type=float,
        default=float(os.getenv("HF_MCP_SLEEP_BUFFER_SECONDS", "5")),
        help="Extra padding added to the requested sleep interval.",
    )
    parser.add_argument(
        "--slack-webhook-url",
        default=os.getenv("HF_MCP_SLACK_WEBHOOK_URL"),
        help="Optional Slack incoming webhook URL for failure and recovery alerts.",
    )
    parser.add_argument(
        "--alert-after-failures",
        type=int,
        default=int(os.getenv("HF_MCP_ALERT_AFTER_FAILURES", "2")),
        help="Send a Slack alert after this many consecutive failures.",
    )
    parser.add_argument(
        "--alert-on-recovery",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("HF_MCP_ALERT_ON_RECOVERY", "true").lower() in {"1", "true", "yes", "on"},
        help="Send a recovery alert when the MCP recovers after a failure streak.",
    )
    parser.add_argument(
        "--email-to",
        default=os.getenv("HF_MCP_EMAIL_TO"),
        help="Optional email recipient for failure and recovery alerts.",
    )
    parser.add_argument(
        "--email-from",
        default=os.getenv("HF_MCP_EMAIL_FROM"),
        help="Optional email sender address used for SMTP alerts.",
    )
    parser.add_argument(
        "--smtp-host",
        default=os.getenv("HF_MCP_SMTP_HOST"),
        help="SMTP host for email alerts.",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=int(os.getenv("HF_MCP_SMTP_PORT", "587")),
        help="SMTP port for email alerts.",
    )
    parser.add_argument(
        "--smtp-username",
        default=os.getenv("HF_MCP_SMTP_USERNAME"),
        help="SMTP username for email alerts.",
    )
    parser.add_argument(
        "--smtp-password",
        default=os.getenv("HF_MCP_SMTP_PASSWORD"),
        help="SMTP password or app password for email alerts.",
    )
    parser.add_argument(
        "--smtp-no-tls",
        action="store_true",
        help="Disable STARTTLS for SMTP servers that do not support it.",
    )
    return parser.parse_args()


def load_state(path: Path) -> MonitorState:
    if not path.exists():
        return MonitorState()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return MonitorState(last_error="could not read state file")

    return MonitorState(
        last_check_at=payload.get("last_check_at"),
        last_success_at=payload.get("last_success_at"),
        last_status=payload.get("last_status"),
        last_http_status=payload.get("last_http_status"),
        last_error=payload.get("last_error"),
        consecutive_failures=int(payload.get("consecutive_failures") or 0),
        next_check_at=payload.get("next_check_at"),
        next_delay_seconds=payload.get("next_delay_seconds"),
    )


def save_state(path: Path, state: MonitorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_headers(api_key: str | None) -> dict[str, str]:
    headers = {
        "Accept": "text/event-stream, application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "adintel-hf-mcp-monitor/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    return headers


def probe(url: str, timeout_seconds: float, api_key: str | None) -> tuple[bool, int | None, str, str | None]:
    req = request.Request(url, headers=build_headers(api_key), method="GET")
    start = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            content_type = resp.headers.get_content_type()
            charset = resp.headers.get_content_charset() or "utf-8"
            elapsed = time.perf_counter() - start
            if resp.status == 200 and content_type == "text/event-stream":
                return True, resp.status, f"{elapsed:.2f}s", content_type
            body = resp.read(2048).decode(charset, errors="replace").strip()
            detail = f"unexpected response: content-type={content_type}"
            if body:
                detail = f"{detail} body={body[:180]}"
            return False, resp.status, f"{elapsed:.2f}s", detail
    except error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        charset = exc.headers.get_content_charset() if exc.headers else None
        content_type = exc.headers.get_content_type() if exc.headers else None
        body = ""
        try:
            raw = exc.read(2048)
            if raw:
                body = raw.decode(charset or "utf-8", errors="replace").strip()
        except Exception:
            body = ""
        detail = f"{exc.reason}"
        if content_type:
            detail = f"{detail} content-type={content_type}"
        if body:
            detail = f"{detail} body={body[:180]}"
        return False, exc.code, f"{elapsed:.2f}s", detail
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return False, None, f"{elapsed:.2f}s", str(exc)


def schedule_delay_seconds(ok: bool, success_hours: float, failure_minutes: float, sleep_buffer_seconds: float) -> int:
    base = success_hours * 3600 if ok else failure_minutes * 60
    return max(1, int(base + sleep_buffer_seconds))


def slack_post(webhook_url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = request.Request(webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=10) as resp:
        print(f"  slack: delivered HTTP {resp.status}")


def build_slack_message(
    *,
    ok: bool,
    url: str,
    consecutive_failures: int,
    http_status: int | None,
    detail: str | None,
    checked_at: datetime,
    previous_failures: int,
    alert_after_failures: int,
) -> str | None:
    if ok:
        if previous_failures >= alert_after_failures:
            return (
                f":large_green_circle: AdIntel HF MCP recovered at {isoformat(checked_at)}\n"
                f"URL: {url}\n"
                f"Previous failure streak: {previous_failures}"
            )
        return None

    if previous_failures >= alert_after_failures:
        return None

    if consecutive_failures < alert_after_failures:
        return None

    return (
        f":red_circle: AdIntel HF MCP unhealthy at {isoformat(checked_at)}\n"
        f"URL: {url}\n"
        f"Consecutive failures: {consecutive_failures}\n"
        f"HTTP status: {http_status if http_status is not None else 'n/a'}\n"
        f"Detail: {detail or 'unknown'}"
    )


def build_email_alert(
    *,
    ok: bool,
    url: str,
    consecutive_failures: int,
    http_status: int | None,
    detail: str | None,
    checked_at: datetime,
    previous_failures: int,
    alert_after_failures: int,
) -> tuple[str, str] | None:
    if ok:
        if previous_failures >= alert_after_failures:
            return (
                "AdIntel HF MCP recovered",
                (
                    f"AdIntel HF MCP recovered at {isoformat(checked_at)}\n\n"
                    f"URL: {url}\n"
                    f"Previous failure streak: {previous_failures}\n"
                ),
            )
        return None

    if previous_failures >= alert_after_failures:
        return None
    if consecutive_failures < alert_after_failures:
        return None

    return (
        "AdIntel HF MCP unhealthy",
        (
            f"AdIntel HF MCP unhealthy at {isoformat(checked_at)}\n\n"
            f"URL: {url}\n"
            f"Consecutive failures: {consecutive_failures}\n"
            f"HTTP status: {http_status if http_status is not None else 'n/a'}\n"
            f"Detail: {detail or 'unknown'}\n"
        ),
    )


def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str | None,
    smtp_password: str | None,
    email_from: str,
    email_to: str,
    subject: str,
    body: str,
    use_tls: bool,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
        if smtp_username and smtp_password:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(msg)
    print(f"  email: delivered to {email_to}")


def main() -> int:
    args = parse_args()
    url = args.url.rstrip("/") + "/"
    state_path = Path(args.state_file)
    api_key = os.getenv("MCP_API_KEY") or os.getenv("ADINTEL_MCP_API_KEY")

    while True:
        checked_at = utc_now()
        previous = load_state(state_path)
        ok, http_status, elapsed, detail = probe(url, args.timeout_seconds, api_key)
        next_failures = 0 if ok else previous.consecutive_failures + 1
        next_delay = schedule_delay_seconds(
            ok=ok,
            success_hours=args.success_hours,
            failure_minutes=args.failure_minutes,
            sleep_buffer_seconds=args.sleep_buffer_seconds,
        )
        next_at = checked_at.timestamp() + next_delay

        state = MonitorState(
            last_check_at=isoformat(checked_at),
            last_success_at=isoformat(checked_at) if ok else previous.last_success_at,
            last_status="ok" if ok else "fail",
            last_http_status=http_status,
            last_error=None if ok else detail,
            consecutive_failures=next_failures,
            next_check_at=isoformat(datetime.fromtimestamp(next_at, tz=timezone.utc)),
            next_delay_seconds=next_delay,
        )
        save_state(state_path, state)

        status_label = "OK" if ok else "FAIL"
        http_label = http_status if http_status is not None else "n/a"
        print(
            f"{isoformat(checked_at)} {status_label} http={http_label} elapsed={elapsed} "
            f"failures={state.consecutive_failures} next_in={next_delay}s"
        )
        if not ok:
            print(f"  detail: {detail}")
        print(f"  state: {state_path}")

        if args.slack_webhook_url:
            slack_message = build_slack_message(
                ok=ok,
                url=url,
                consecutive_failures=next_failures,
                http_status=http_status,
                detail=detail,
                checked_at=checked_at,
                previous_failures=previous.consecutive_failures,
                alert_after_failures=max(1, args.alert_after_failures),
            )
            if slack_message:
                try:
                    slack_post(args.slack_webhook_url, slack_message)
                except Exception as exc:
                    print(f"  slack: failed to deliver alert: {exc}")

        if args.email_to and args.email_from and args.smtp_host:
            email_alert = build_email_alert(
                ok=ok,
                url=url,
                consecutive_failures=next_failures,
                http_status=http_status,
                detail=detail,
                checked_at=checked_at,
                previous_failures=previous.consecutive_failures,
                alert_after_failures=max(1, args.alert_after_failures),
            )
            if email_alert:
                subject, body = email_alert
                try:
                    send_email(
                        smtp_host=args.smtp_host,
                        smtp_port=args.smtp_port,
                        smtp_username=args.smtp_username,
                        smtp_password=args.smtp_password,
                        email_from=args.email_from,
                        email_to=args.email_to,
                        subject=subject,
                        body=body,
                        use_tls=not args.smtp_no_tls,
                    )
                except Exception as exc:
                    print(f"  email: failed to deliver alert: {exc}")

        if args.single_run:
            return 0 if ok else 1

        sleep_for = next_delay
        print(f"  sleeping {sleep_for}s until next check")
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            print("\nmonitor stopped")
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
