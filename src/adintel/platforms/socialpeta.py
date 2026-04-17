from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Response, TimeoutError as PlaywrightTimeoutError, async_playwright

from adintel.collectors.base import PlatformCollector
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.db.repositories import SocialPetaRepository

logger = logging.getLogger("adintel.socialpeta")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = PROJECT_ROOT / "state" / "browser" / "socialpeta"
CDP_STATE_DIR = PROJECT_ROOT / "state" / "browser" / "socialpeta-cdp"
CDP_URL = "http://127.0.0.1:9223"
APP_URL = "https://socialpeta.com/modules/ecom/creative/display-ads"
LIST_ENDPOINT = "https://socialpeta.com/napi/v1/creative/list"
PAGE_EXISTENCE_ENDPOINT = "https://socialpeta.com/napi/v1/creative/page-existence"


def _resolve_state_dir() -> Path:
    if CDP_STATE_DIR.exists():
        try:
            if any(CDP_STATE_DIR.iterdir()):
                return CDP_STATE_DIR
        except OSError:
            pass
    return STATE_DIR


async def launch_context(*, headless: bool) -> tuple[Any, BrowserContext]:
    if CDP_STATE_DIR.exists():
        playwright = await async_playwright().start()
        try:
            logger.info("Trying to attach to an existing SocialPeta CDP browser session.")
            browser = await playwright.chromium.connect_over_cdp(CDP_URL)
            if browser.contexts:
                context = browser.contexts[0]
                setattr(context, "_adintel_playwright", playwright)
                setattr(context, "_adintel_browser", browser)
                setattr(context, "_adintel_attached_cdp", True)
                return playwright, context
            await browser.close()
        except Exception:
            await playwright.stop()

    profile_dir = _resolve_state_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    logger.info("Launching SocialPeta persistent browser context in %s.", profile_dir)
    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        channel="chromium",
        headless=headless,
        viewport={"width": 1440, "height": 900},
        args=["--disable-dev-shm-usage"],
    )
    return playwright, context


async def close_context(playwright: Any, context: BrowserContext) -> None:
    browser: Browser | None = getattr(context, "_adintel_browser", None)
    attached_cdp = bool(getattr(context, "_adintel_attached_cdp", False))
    if not attached_cdp:
        await context.close()
    if browser is not None and not attached_cdp:
        await browser.close()
    await playwright.stop()


async def ensure_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if APP_URL in page.url:
            return page
    return context.pages[0] if context.pages else await context.new_page()


async def ensure_display_ads_page(page: Page, *, timeout_ms: int = 60_000) -> None:
    if APP_URL in page.url:
        return

    attempts = [
        ("domcontentloaded", timeout_ms),
        ("commit", min(timeout_ms, 20_000)),
        ("load", timeout_ms),
    ]
    for wait_until, wait_timeout in attempts:
        try:
            logger.info(
                "Navigating to SocialPeta display-ads with wait_until=%s timeout=%sms",
                wait_until,
                wait_timeout,
            )
            await page.goto(APP_URL, wait_until=wait_until, timeout=wait_timeout)
            if APP_URL in page.url:
                return
        except PlaywrightTimeoutError:
            logger.warning("Timed out navigating to display-ads with wait_until=%s", wait_until)

    # The SPA can keep loading events pending even when auth + cookies are usable.
    # If we're still on socialpeta.com and not on a login page, allow API collection to proceed.
    current = page.url or ""
    if "socialpeta.com" in current and "login" not in current and "sign" not in current:
        logger.warning("Proceeding without display-ads URL after navigation timeout; current page is %s", current)
        return

    raise RuntimeError(
        "Unable to navigate to SocialPeta display-ads page. "
        f"Current page: {current or '(empty)'}"
    )


def _to_date(epoch_seconds: int | float | None) -> date | None:
    if epoch_seconds in (None, 0):
        return None
    return datetime.fromtimestamp(float(epoch_seconds), tz=UTC).date()


def _to_datetime(epoch_seconds: int | float | None) -> datetime | None:
    if epoch_seconds in (None, 0):
        return None
    return datetime.fromtimestamp(float(epoch_seconds), tz=UTC)


def _infer_creative_type(item: dict[str, Any]) -> str | None:
    resource_urls = item.get("resource_urls") or []
    if not isinstance(resource_urls, list):
        return None
    resource_types = {entry.get("type") for entry in resource_urls if isinstance(entry, dict)}
    if 2 in resource_types:
        return "Video"
    if 1 in resource_types:
        return "Image"
    ads_type = item.get("ads_type")
    if ads_type == 2:
        return "Video"
    if ads_type == 1:
        return "Image"
    return None


def parse_creative_rows(
    creative_list: list[dict[str, Any]],
    *,
    advertiser_name: str,
    target_query: str,
    country: str,
    page_analysis_map: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in creative_list:
        creative_id = item.get("ad_key")
        if not creative_id:
            continue
        channels = item.get("fb_merge_channel") or []
        primary_channel = None
        if isinstance(channels, list) and channels:
            primary_channel = str(channels[0])
        elif item.get("platform"):
            primary_channel = str(item["platform"])
        rows.append(
            {
                "advertiser_name": str(advertiser_name),
                "country": country,
                "creative_id": str(creative_id),
                "target_query": target_query,
                "advertiser_identifier": item.get("ecom_advertiser_id"),
                "page_name": item.get("page_name"),
                "creative_title": item.get("title"),
                "body": item.get("body"),
                "message": item.get("message"),
                "call_to_action": item.get("call_to_action"),
                "creative_type": _infer_creative_type(item),
                "ads_type": item.get("ads_type"),
                "primary_channel": primary_channel,
                "landing_page_url": (
                    f"https://{item['ecom_advertiser_id']}" if item.get("ecom_advertiser_id") else None
                ),
                "preview_image_url": item.get("preview_img_url"),
                "resource_urls": item.get("resource_urls"),
                "first_seen": _to_date(item.get("first_seen")),
                "last_seen": _to_date(item.get("last_seen")),
                "active_days": item.get("days_count"),
                "impression": item.get("impression"),
                "popularity": item.get("heat"),
                "creative_score": item.get("all_exposure_value"),
                "created_at_platform": _to_datetime(item.get("created_at")),
                "has_page_id": item.get("has_page_id"),
                "has_store_url": item.get("has_store_url"),
                "is_page_analysis": (page_analysis_map or {}).get(str(creative_id)),
                "is_active": bool(item.get("last_seen")),
                "raw_payload": item,
            }
        )
    return rows


def parse_channel_rows(
    creative_list: list[dict[str, Any]],
    *,
    advertiser_name: str,
    country: str,
) -> list[dict[str, Any]]:
    """Extract all channels from fb_merge_channel (multi-channel support)."""
    rows: list[dict[str, Any]] = []
    for item in creative_list:
        creative_id = item.get("ad_key")
        if not creative_id:
            continue
        # fb_merge_channel has the full channel list (facebook, instagram, audience_network, messenger)
        channels = item.get("fb_merge_channel") or []
        if not isinstance(channels, list) or not channels:
            if item.get("platform"):
                channels = [item["platform"]]
            else:
                channels = []
        for channel in channels:
            rows.append(
                {
                    "advertiser_name": str(advertiser_name),
                    "country": country,
                    "creative_id": str(creative_id),
                    "channel": str(channel),
                    "first_seen": _to_date(item.get("first_seen")),
                    "last_seen": _to_date(item.get("last_seen")),
                    "active_days": item.get("days_count"),
                }
            )
    return rows


def parse_tag_rows(
    creative_list: list[dict[str, Any]],
    *,
    advertiser_name: str,
    country: str,
) -> list[dict[str, Any]]:
    """Extract creative tags: custom_tag (user labels), ad_features (type indicators), video duration."""
    rows: list[dict[str, Any]] = []
    for item in creative_list:
        creative_id = item.get("ad_key")
        if not creative_id:
            continue

        # Custom tags (user-defined labels)
        custom_tags = item.get("custom_tag") or []
        if isinstance(custom_tags, list):
            for tag in custom_tags:
                if tag and str(tag).strip():
                    rows.append({
                        "advertiser_name": str(advertiser_name),
                        "country": country,
                        "creative_id": str(creative_id),
                        "tag_category": "custom_tag",
                        "tag_value": str(tag).strip(),
                        "scraped_at": datetime.now(UTC),
                    })

        # Ad features (feature IDs that indicate creative type)
        ad_features = item.get("ad_features") or []
        if isinstance(ad_features, list):
            for feature_id in ad_features:
                if feature_id is not None:
                    feature_str = str(feature_id).strip()
                    if feature_str:
                        rows.append({
                            "advertiser_name": str(advertiser_name),
                            "country": country,
                            "creative_id": str(creative_id),
                            "tag_category": "ad_feature",
                            "tag_value": feature_str,
                            "scraped_at": datetime.now(UTC),
                        })

        # Video duration category
        video_duration = item.get("video_duration")
        if video_duration is not None and video_duration > 0:
            duration_category = "video_short"
            if video_duration > 60:
                duration_category = "video_long"
            rows.append({
                "advertiser_name": str(advertiser_name),
                "country": country,
                "creative_id": str(creative_id),
                "tag_category": "duration",
                "tag_value": duration_category,
                "scraped_at": datetime.now(UTC),
            })

        # Creative format (image vs video)
        if item.get("video_duration", 0) > 0:
            format_type = "video"
        else:
            format_type = "image"
        rows.append({
            "advertiser_name": str(advertiser_name),
            "country": country,
            "creative_id": str(creative_id),
            "tag_category": "format",
            "tag_value": format_type,
            "scraped_at": datetime.now(UTC),
        })

    return rows


class SocialPetaCollector(PlatformCollector):
    platform = PlatformName.SOCIALPETA
    state_key = "socialpeta"

    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        if use_cdp:
            raise RuntimeError("SocialPeta login does not support --use-cdp yet. Use the persistent browser profile flow instead.")

        playwright, context = await launch_context(headless=headless)
        try:
            page = await ensure_page(context)
            logger.info("Opening SocialPeta login page: %s", APP_URL)
            await ensure_display_ads_page(page, timeout_ms=60_000)
            await asyncio.to_thread(
                input,
                "Complete the SocialPeta login in the browser, then press Enter here to save the session.",
            )
        finally:
            await close_context(playwright, context)

    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        if use_cdp:
            raise RuntimeError("SocialPeta collection does not support --use-cdp yet.")

        playwright, context = await launch_context(headless=request.headless)
        try:
            page = await ensure_page(context)
            logger.info(
                "Opening SocialPeta display-ads page for %s (%s).",
                request.advertiser.name,
                request.countries[0] if request.countries else "US",
            )
            await ensure_display_ads_page(page, timeout_ms=60_000)
            await page.wait_for_timeout(1_500)
            if "login" in page.url or "sign" in page.url:
                return CollectorRunResult(
                    platform=self.platform,
                    advertiser_name=request.advertiser.name,
                    status="auth_expired",
                    message="SocialPeta session has expired. Run 'adintel login socialpeta' to re-authenticate.",
                )
            result = await self.collect_query(
                page,
                advertiser_name=request.advertiser.name,
                target_query=request.advertiser.domain or request.advertiser.name,
                country=(request.countries[0] if request.countries else "US"),
            )
            return CollectorRunResult(
                platform=self.platform,
                advertiser_name=request.advertiser.name,
                status="success" if result["records_written"] else "empty",
                message=(
                    f"Collected {result['records_written']} SocialPeta creative rows."
                    if result["records_written"]
                    else "No SocialPeta creatives were captured."
                ),
                records_written=result["records_written"],
                metadata=result["metadata"],
            )
        finally:
            await close_context(playwright, context)

    async def collect_query(
        self,
        page: Page,
        *,
        advertiser_name: str | None = None,
        target_query: str,
        country: str,
        pages: int = 3,
    ) -> dict[str, Any]:
        repo = SocialPetaRepository(self.session)
        canonical_advertiser_name = advertiser_name or target_query

        logger.info("Collecting SocialPeta creatives for query=%s country=%s pages=%s", target_query, country, pages)
        request_body = await self._build_request_body(page, target_query)
        logger.debug("SocialPeta request body prepared: %s", {k: request_body.get(k) for k in ("page", "page_size", "keyword", "seen_begin", "seen_end")})
        await self._sleep_with_page_jitter()
        logger.info("Requesting SocialPeta creative list page 1.")
        payload = await self._post_json(page, LIST_ENDPOINT, request_body)
        creative_list = payload.get("data", {}).get("creative_list") or []
        logger.info("SocialPeta page 1 returned %d creatives.", len(creative_list))

        page_analysis_map = await self._fetch_page_existence(page, creative_list, request_body)
        creative_rows = parse_creative_rows(
            creative_list,
            advertiser_name=canonical_advertiser_name,
            target_query=target_query,
            country=country,
            page_analysis_map=page_analysis_map,
        )
        channel_rows = parse_channel_rows(
            creative_list,
            advertiser_name=canonical_advertiser_name,
            country=country,
        )
        tag_rows = parse_tag_rows(
            creative_list,
            advertiser_name=canonical_advertiser_name,
            country=country,
        )

        for page_num in range(2, pages + 1):
            request_body["page"] = page_num
            await self._sleep_with_page_jitter()
            logger.info("Requesting SocialPeta creative list page %d.", page_num)
            next_payload = await self._post_json(page, LIST_ENDPOINT, request_body)
            next_list = next_payload.get("data", {}).get("creative_list") or []
            if not next_list:
                logger.info("SocialPeta page %d returned no creatives; stopping pagination.", page_num)
                break
            logger.info("SocialPeta page %d returned %d creatives.", page_num, len(next_list))
            next_map = await self._fetch_page_existence(page, next_list, request_body)
            creative_rows.extend(
                parse_creative_rows(
                    next_list,
                    advertiser_name=canonical_advertiser_name,
                    target_query=target_query,
                    country=country,
                    page_analysis_map=next_map,
                )
            )
            channel_rows.extend(
                parse_channel_rows(
                    next_list,
                    advertiser_name=canonical_advertiser_name,
                    country=country,
                )
            )
            tag_rows.extend(
                parse_tag_rows(
                    next_list,
                    advertiser_name=canonical_advertiser_name,
                    country=country,
                )
            )

        written = repo.upsert_creatives(creative_rows)
        repo.upsert_creative_channels(channel_rows)
        # Only upsert tags if we have any (guard against empty tag_rows)
        if tag_rows:
            repo.upsert_creative_tags(tag_rows)

        return {
            "records_written": written,
            "metadata": {
                "target_query": target_query,
                "country": country,
                "pages_requested": pages,
                "creatives_captured": len(creative_rows),
                "channels_captured": len(channel_rows),
                "tags_captured": len(tag_rows),
                "request_body": request_body,
            },
        }

    async def _build_request_body(self, page: Page, target_query: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page": 1,
            "page_size": 60,
            "complete_country_match": False,
            "app_type": 3,
            "new_ads_flag": 0,
            "sort_field": "-first_seen",
            "duplicate_removal": 0,
            "search_type": "1",
            "fb_merge": False,
            "original_flag": 0,
            "is_dynamic": 0,
            "landing_page": 0,
            "keyword": target_query,
        }
        body_text = await page.locator("body").inner_text()
        range_map = {"7 Days": 7, "30 Days": 30, "90 Days": 90, "1 Year": 365}
        selected_days = 90
        for label, days in range_map.items():
            if label in body_text:
                selected_days = days
                break
        now = datetime.now(UTC)
        seen_begin = int((now.timestamp()) - (selected_days * 86400))
        seen_end = int(now.timestamp())
        payload["seen_begin"] = seen_begin
        payload["seen_end"] = seen_end
        return payload

    async def _fetch_page_existence(
        self,
        page: Page,
        creative_list: list[dict[str, Any]],
        request_body: dict[str, Any],
    ) -> dict[str, bool]:
        creative_keys = [item.get("ad_key") for item in creative_list if item.get("ad_key")]
        if not creative_keys:
            return {}
        payload = {
            "creative_keys": creative_keys,
            "app_type": request_body.get("app_type", 3),
            "created_at": request_body.get("seen_begin"),
        }
        data = await self._post_json(page, PAGE_EXISTENCE_ENDPOINT, payload)
        raw = data.get("data") or {}
        return {
            str(key): bool(value.get("is_page_analysis"))
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    async def _post_json(self, page: Page, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await page.request.post(url, data=payload, timeout=self.settings.collect_timeout_ms)
        if response.status in (401, 403):
            raise RuntimeError("SocialPeta API request was unauthorized. Re-run `adintel login socialpeta`.")
        if response.status != 200:
            raise RuntimeError(f"SocialPeta API returned HTTP {response.status} for {url}")
        return await response.json()

    async def _sleep_with_page_jitter(self) -> None:
        if not self.settings.socialpeta_jitter_enabled:
            return
        lo = self.settings.socialpeta_page_jitter_min_s
        hi = self.settings.socialpeta_page_jitter_max_s
        if hi < lo:
            lo, hi = hi, lo
        delay = random.uniform(lo, hi)
        logger.debug("SocialPeta jitter sleep before page request: %.2fs", delay)
        await asyncio.sleep(delay)
