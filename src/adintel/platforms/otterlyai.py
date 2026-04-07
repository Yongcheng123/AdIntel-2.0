from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml
from playwright.async_api import BrowserContext, Page, async_playwright

from adintel.core.settings import get_settings
from adintel.db.repositories import OtterlyRepository
from adintel.db.session import build_session_factory
from adintel.platforms.otterlyai_parsers import normalize_engine_label, refine_citation_rows, refine_prompt_rows


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "otterly"
OUTPUT_DIR = PROJECT_ROOT / "output" / "otterly"
AUTH_CACHE_PATH = PROJECT_ROOT / "state" / "otterly_auth.json"
APP_URL = "https://app.otterly.ai"
API_BASE = "https://api.otterly.ai"


async def launch_context(*, headless: bool) -> tuple[Any, BrowserContext]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        str(STATE_DIR),
        channel="chromium",
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-dev-shm-usage"],
    )
    return playwright, context


async def close_context(playwright: Any, context: BrowserContext) -> None:
    await context.close()
    await playwright.stop()


async def ensure_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else await context.new_page()


def _decode_jwt_expiry(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload + padding).decode("utf-8"))
    except Exception:
        return None
    exp = decoded.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _build_auth_headers(authorization: str, workspace_id: str) -> dict[str, str]:
    return {
        "Authorization": authorization,
        "x-workspace-id": workspace_id,
        "Content-Type": "application/json",
    }


def load_env_auth_headers() -> dict[str, str] | None:
    token = os.getenv("OTTERLY_BEARER_TOKEN")
    workspace_id = os.getenv("OTTERLY_WORKSPACE_ID")
    if not token or not workspace_id:
        return None
    authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return _build_auth_headers(authorization, workspace_id)


def load_cached_auth_headers() -> dict[str, str] | None:
    if not AUTH_CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(AUTH_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    authorization = payload.get("authorization")
    workspace_id = payload.get("workspace_id")
    expires_at = payload.get("expires_at")
    if not isinstance(authorization, str) or not isinstance(workspace_id, str):
        return None
    if isinstance(expires_at, (int, float)) and time.time() >= (float(expires_at) - 300):
        return None
    return _build_auth_headers(authorization, workspace_id)


def save_cached_auth_headers(headers: dict[str, str]) -> None:
    authorization = headers["Authorization"]
    token = authorization.removeprefix("Bearer ").strip()
    expires_at = _decode_jwt_expiry(token)
    AUTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_CACHE_PATH.write_text(
        json.dumps(
            {
                "authorization": authorization,
                "workspace_id": headers["x-workspace-id"],
                "expires_at": expires_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def refresh_auth_headers_from_browser(context: BrowserContext) -> dict[str, str]:
    page = await ensure_page(context)
    captured: dict[str, str] = {}

    def on_request(request: Any) -> None:
        if "api.otterly.ai" not in request.url:
            return
        auth = request.headers.get("authorization")
        workspace = request.headers.get("x-workspace-id")
        if auth and workspace and not captured:
            captured["Authorization"] = auth
            captured["x-workspace-id"] = workspace

    page.on("request", on_request)
    try:
        await page.goto(f"{APP_URL}/reports", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
    finally:
        page.remove_listener("request", on_request)

    if not captured:
        raise RuntimeError("Could not capture Otterly auth headers from live page requests. Re-run login.")

    headers = _build_auth_headers(captured["Authorization"], captured["x-workspace-id"])
    save_cached_auth_headers(headers)
    return headers


async def get_auth_headers(context: BrowserContext, *, force_refresh: bool = False) -> dict[str, str]:
    if not force_refresh:
        env_headers = load_env_auth_headers()
        if env_headers is not None:
            return env_headers

        cached_headers = load_cached_auth_headers()
        if cached_headers is not None:
            return cached_headers

    return await refresh_auth_headers_from_browser(context)


async def api_get(context: BrowserContext, path_or_url: str) -> Any:
    page = await ensure_page(context)
    url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}{path_or_url}"
    headers = await get_auth_headers(context)
    response = await page.request.get(url, headers=headers, timeout=30_000)
    if response.status == 401:
        headers = await get_auth_headers(context, force_refresh=True)
        response = await page.request.get(url, headers=headers, timeout=30_000)
    if response.status != 200:
        raise RuntimeError(f"Otterly API returned HTTP {response.status} for {url}")
    return await response.json()


async def fetch_report_payload(context: BrowserContext, report_id: str) -> dict[str, Any]:
    return await api_get(context, f"/brands/reports/{report_id}")


async def fetch_prompts_payload(
    context: BrowserContext,
    *,
    report_id: str,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
) -> dict[str, Any]:
    query = urlencode(
        {
            "startDate": f"{start_date}T00:00:00.000Z",
            "endDate": f"{end_date}T23:59:59.999Z",
            "country": country.lower(),
            "groupByPeriod": "day",
            "aggregatePeriodData": "true",
        }
    )
    if service:
        query += f"&services={service}"
    return await api_get(context, f"/brands/reports/{report_id}/prompts?{query}")


async def fetch_citations_payload(
    context: BrowserContext,
    *,
    report_id: str,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
    page_size: int,
    page: int = 1,
) -> dict[str, Any]:
    query = urlencode(
        {
            "startDate": start_date,
            "endDate": end_date,
            "country": country.lower(),
            "groupByPeriod": "day",
            "aggregatePeriodData": "true",
            "page": page,
            "pageSize": page_size,
            "sortBy": "citations",
            "sortOrder": "desc",
        }
    )
    if service:
        query += f"&services={service}"
    return await api_get(context, f"/brands/reports/{report_id}/citations?{query}")


async def fetch_all_citations_payload(
    context: BrowserContext,
    *,
    report_id: str,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
    page_size: int,
) -> dict[str, Any]:
    first_payload = await fetch_citations_payload(
        context,
        report_id=report_id,
        country=country,
        start_date=start_date,
        end_date=end_date,
        service=service,
        page_size=page_size,
        page=1,
    )

    cited_urls = list(first_payload.get("citedUrls") or [])
    page = 2
    while len(first_payload.get("citedUrls") or []) == page_size:
        next_payload = await fetch_citations_payload(
            context,
            report_id=report_id,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service,
            page_size=page_size,
            page=page,
        )
        next_rows = next_payload.get("citedUrls") or []
        if not next_rows:
            break
        cited_urls.extend(next_rows)
        if len(next_rows) < page_size:
            break
        page += 1

    merged_payload = dict(first_payload)
    merged_payload["citedUrls"] = cited_urls
    return merged_payload


def lookup_report_id(reports: list[dict[str, Any]], brand_or_domain: str) -> str | None:
    needle = brand_or_domain.strip().lower()
    for report in reports:
        brand_domain = str(report.get("brandDomain") or "").strip().lower()
        brand_name = str(report.get("brand") or "").strip().lower()
        if needle in {brand_domain, brand_name}:
            report_id = report.get("id")
            if isinstance(report_id, str) and report_id:
                return report_id
    return None


async def expect_enabled(locator: Any, timeout: int = 10_000) -> None:
    """Poll until a button/element is enabled."""
    deadline = asyncio.get_event_loop().time() + timeout / 1000
    while True:
        if await locator.is_enabled():
            return
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Element still disabled after {timeout}ms")
        await asyncio.sleep(0.3)


async def create_report(
    brand_name: str,
    domain: str,
    *,
    select_all_prompts: bool = True,
    headless: bool = True,
) -> str:
    """Navigate Otterly's 3-step wizard to create a brand report. Returns the new report ID."""
    playwright, context = await launch_context(headless=headless)
    try:
        await get_auth_headers(context)  # ensure auth is valid / cached
        page = await ensure_page(context)

        # Step 1 — Brand details
        # Navigate to reports page first to find the create button
        print(f"[create_report] Navigating to {APP_URL}/reports")
        await page.goto(f"{APP_URL}/reports", wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        print(f"[create_report] Current URL after /reports: {page.url}")

        # Save a screenshot to debug what the UI looks like
        screenshot_path = PROJECT_ROOT / "state" / "create_report_debug.png"
        await page.screenshot(path=screenshot_path)
        print(f"[create_report] Saved screenshot to {screenshot_path}")

        # Try to find and click the create button
        print("[create_report] Looking for create button...")
        # Try different selectors
        all_buttons = page.get_by_role("button")
        count = await all_buttons.count()
        print(f"[create_report] Found {count} buttons on the page")
        for i in range(min(10, count)):
            btn = all_buttons.nth(i)
            name = await btn.get_attribute("aria-label") or await btn.text_content()
            print(f"  Button {i}: {name}")

        # Try to navigate directly to create page
        print("[create_report] Trying direct navigation to /reports/create")
        await page.goto(f"{APP_URL}/reports/create", wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        print(f"[create_report] Current URL after create redirect: {page.url}")

        # Wait for the form to be ready
        report_title_field = page.get_by_placeholder("Enter report title")
        await report_title_field.wait_for(state="visible", timeout=15_000)

        await report_title_field.fill(brand_name)
        await page.get_by_placeholder("Enter brand name").fill(brand_name)
        await page.get_by_placeholder("Enter brand domain (e.g., example.com)").fill(domain)
        next_btn = page.get_by_role("button", name="Next step")
        await next_btn.wait_for(state="visible")
        await expect_enabled(next_btn)
        await next_btn.click()

        # Step 2 — Add prompts
        await page.wait_for_selector("text=All prompts", timeout=10_000)
        if select_all_prompts:
            await page.get_by_role("checkbox", name="Select all").check()
            # Move selected prompts to the report using the right-arrow transfer button
            transfer_right = page.locator("button:has(img[alt='right'])").first
            await transfer_right.wait_for(state="visible")
            await transfer_right.click()
        next_btn2 = page.get_by_role("button", name="Next")
        await expect_enabled(next_btn2)
        await next_btn2.click()

        # Step 3 — Competitors (skip, just save)
        save_btn = page.get_by_role("button", name="Save")
        await save_btn.wait_for(state="visible")
        await save_btn.click()

        # Extract new report ID from URL
        await page.wait_for_url(re.compile(r"/reports/[^/]+$"), timeout=15_000)
        report_id = page.url.split("/reports/")[-1]
        return report_id
    finally:
        await close_context(playwright, context)


def _normalize_targets_payload(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, dict) and isinstance(payload.get("targets"), list):
        payload = payload["targets"]

    if not isinstance(payload, list):
        raise ValueError("Targets payload must be a list or a mapping with a `targets` list.")

    normalized: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each target must be an object.")
        brand = item.get("brand") or item.get("domain")
        country = item.get("country")
        if not isinstance(brand, str) or not isinstance(country, str):
            raise ValueError("Each target requires string fields `brand` and `country`.")
        normalized.append({"brand": brand.strip(), "country": country.strip().lower()})
    return normalized


def load_targets(targets_file: Path | None, targets_json: str | None) -> list[dict[str, str]]:
    if targets_json:
        payload = json.loads(targets_json)
        return _normalize_targets_payload(payload)
    if targets_file:
        suffix = targets_file.suffix.lower()
        raw_text = targets_file.read_text(encoding="utf-8")
        if suffix in {".yaml", ".yml"}:
            payload = yaml.safe_load(raw_text) or {}
        else:
            payload = json.loads(raw_text)
        return _normalize_targets_payload(payload)
    raise ValueError("Provide either targets_file or targets_json.")


async def list_reports_payload(*, headless: bool = True) -> Any:
    playwright, context = await launch_context(headless=headless)
    try:
        return await api_get(context, "/brands/reports")
    finally:
        await close_context(playwright, context)


async def export_prompt_rows(
    *,
    report_id: str,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
    headless: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    playwright, context = await launch_context(headless=headless)
    try:
        report_payload = await fetch_report_payload(context, report_id)
        prompts_payload = await fetch_prompts_payload(
            context,
            report_id=report_id,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service,
        )
        rows = refine_prompt_rows(
            report_payload,
            prompts_payload,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service,
        )
        return report_payload, rows
    finally:
        await close_context(playwright, context)


async def export_citation_rows(
    *,
    report_id: str,
    country: str,
    start_date: str,
    end_date: str,
    service: str | None,
    page_size: int,
    headless: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    playwright, context = await launch_context(headless=headless)
    try:
        report_payload = await fetch_report_payload(context, report_id)
        citations_payload = await fetch_all_citations_payload(
            context,
            report_id=report_id,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service,
            page_size=page_size,
        )
        rows = refine_citation_rows(
            report_payload,
            citations_payload,
            country=country,
            start_date=start_date,
            end_date=end_date,
            service=service,
        )
        return report_payload, rows
    finally:
        await close_context(playwright, context)


async def collect_batch(
    *,
    targets: list[dict[str, str]],
    start_date: str,
    end_date: str,
    services: list[str],
    page_size: int = 100,
    save_files: bool = False,
    write_db: bool = True,
    headless: bool = True,
    sleep_range_seconds: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    playwright, context = await launch_context(headless=headless)
    try:
        reports = await api_get(context, "/brands/reports")
        if not isinstance(reports, list):
            raise RuntimeError("Otterly /brands/reports did not return a list.")

        batch_rows: dict[str, list[dict[str, Any]]] = {"prompts": [], "citations": []}
        summaries: list[dict[str, Any]] = []

        for target in targets:
            brand = target["brand"]
            country = target["country"]
            report_id = lookup_report_id(reports, brand)
            if report_id is None:
                summaries.append({"brand": brand, "country": country, "status": "missing_report"})
                continue

            try:
                report_payload = await fetch_report_payload(context, report_id)
            except Exception as exc:
                summaries.append({"brand": brand, "country": country, "status": "error", "error": str(exc)})
                continue

            for service in services:
                try:
                    prompts_payload = await fetch_prompts_payload(
                        context,
                        report_id=report_id,
                        country=country,
                        start_date=start_date,
                        end_date=end_date,
                        service=service,
                    )
                    citations_payload = await fetch_all_citations_payload(
                        context,
                        report_id=report_id,
                        country=country,
                        start_date=start_date,
                        end_date=end_date,
                        service=service,
                        page_size=page_size,
                    )

                    prompt_rows = refine_prompt_rows(
                        report_payload,
                        prompts_payload,
                        country=country,
                        start_date=start_date,
                        end_date=end_date,
                        service=service,
                    )
                    citation_rows = refine_citation_rows(
                        report_payload,
                        citations_payload,
                        country=country,
                        start_date=start_date,
                        end_date=end_date,
                        service=service,
                    )

                    batch_rows["prompts"].extend(prompt_rows)
                    batch_rows["citations"].extend(citation_rows)
                    summaries.append(
                        {
                            "brand": brand,
                            "country": country,
                            "service": normalize_engine_label(service),
                            "report_id": report_id,
                            "prompt_rows": len(prompt_rows),
                            "citation_rows": len(citation_rows),
                            "status": "ok",
                        }
                    )

                    if save_files:
                        service_suffix = f".{service}" if service else ""
                        prompt_target = OUTPUT_DIR / f"{report_id}.{country}.{end_date}{service_suffix}.refined.json"
                        citation_target = OUTPUT_DIR / f"{report_id}.{country}.{end_date}{service_suffix}.citations.refined.json"
                        prompt_target.parent.mkdir(parents=True, exist_ok=True)
                        prompt_target.write_text(json.dumps(prompt_rows, indent=2), encoding="utf-8")
                        citation_target.write_text(json.dumps(citation_rows, indent=2), encoding="utf-8")
                except Exception as exc:
                    summaries.append(
                        {
                            "brand": brand,
                            "country": country,
                            "service": normalize_engine_label(service),
                            "report_id": report_id,
                            "status": "error",
                            "error": str(exc),
                        }
                    )

                if sleep_range_seconds is not None:
                    min_seconds, max_seconds = sleep_range_seconds
                    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
        if write_db:
            settings = get_settings()
            session_factory = build_session_factory(settings)
            with session_factory() as session:
                repo = OtterlyRepository(session)
                repo.upsert_prompts(batch_rows["prompts"])
                repo.upsert_citations(batch_rows["citations"])
        return summaries
    finally:
        await close_context(playwright, context)
