from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode, urljoin

from playwright.async_api import Page

logger = logging.getLogger("adintel.sensortower")

from adintel.collectors.base import PlatformCollector
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.db.repositories import ScrapeRunMetricRepository, SensorTowerRepository
from adintel.platforms.sensortower_parsers import (
    build_creative_metadata_map,
    detect_category,
    merge_engagement_rows,
    normalize_array,
    parse_aso_keyword_rows,
    parse_creative_rows,
    parse_demographic_rows,
    parse_download_rows,
    parse_impression_share_rows,
    parse_ranking_row,
    parse_review_rows,
    parse_review_text_rows,
    parse_retention_rows,
)


NETWORKS = [
    "Admob",
    "Applovin",
    "BidMachine",
    "Chartboost",
    "Digital+Turbine",
    "InMobi",
    "Supersonic",
    "Vungle",
    "Meta+Audience+Network",
    "Mintegral",
    "Moloco",
    "Pangle",
    "Smaato",
    "Unity",
    "Verve",
    "Facebook",
    "Instagram",
    "Line",
    "Pinterest",
    "Snapchat",
    "TikTok",
    "Twitter",
    "Youtube",
]


class SensorTowerCollector(PlatformCollector):
    platform = PlatformName.SENSORTOWER
    state_key = "sensortower"

    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        async with self.browser.session(self.state_key, headless=headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(self.settings.sensortower_base_url, wait_until="domcontentloaded")
            await asyncio.to_thread(
                input,
                "Complete the SensorTower login in the browser, then press Enter here to close the session.",
            )

    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        unified_app_id = request.advertiser.platforms.sensortower.unified_app_id
        if not unified_app_id:
            logger.warning("Skipping %s: no SensorTower unified_app_id configured", request.advertiser.name)
            return CollectorRunResult(
                platform=self.platform,
                advertiser_name=request.advertiser.name,
                status="skipped",
                message="No SensorTower unified app identifier is configured.",
            )

        logger.info("Starting SensorTower collection for %s (countries=%s)", request.advertiser.name, request.countries)
        repository = SensorTowerRepository(self.session)
        total_records = 0
        category_state = {"id": None, "name": None}
        metric_results: dict[str, str] = {}

        async with self.browser.session(self.state_key, headless=request.headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(self.settings.sensortower_base_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(4_000)

            # Session validation: check if we're still authenticated
            if not await self._validate_session(page):
                return CollectorRunResult(
                    platform=self.platform,
                    advertiser_name=request.advertiser.name,
                    status="auth_expired",
                    message="SensorTower session has expired. Run 'adintel login sensortower' to re-authenticate.",
                )

            date_start = self._days_ago(90)
            date_end = self._today()

            metrics = [
                ("downloads", lambda c: self._collect_downloads(page, request, repository, c, date_start, date_end, category_state)),
                ("retention", lambda c: self._collect_retention(page, request, repository, c, category_state)),
                ("impression_share", lambda c: self._collect_impression_share(page, request, repository, c, date_start, date_end, category_state)),
                ("demographics", lambda c: self._collect_demographics(page, request, repository, c, category_state)),
                ("engagement", lambda c: self._collect_engagement(page, request, repository, c, date_start, date_end, category_state)),
                ("reviews", lambda c: self._collect_reviews(page, request, repository, c, date_start, date_end, category_state)),
                ("rankings", lambda c: self._collect_rankings(page, request, repository, c, category_state)),
                ("creatives", lambda c: self._collect_creatives(page, request, repository, c, date_start, date_end, category_state)),
                ("aso_keywords", lambda c: self._collect_aso_keywords(page, request, repository, c, category_state)),
            ]

            for country in request.countries:
                logger.info("Collecting metrics for %s/%s", request.advertiser.name, country)
                for metric_name, collector_fn in metrics:
                    key = f"{metric_name}/{country}"
                    try:
                        written = await collector_fn(country)
                        total_records += written
                        metric_results[key] = "success" if written else "empty"
                    except Exception as exc:
                        logger.error("Metric %s failed for %s: %s", key, request.advertiser.name, exc, exc_info=True)
                        metric_results[key] = "error"
                        self.session.rollback()

        failed_metrics = [k for k, v in metric_results.items() if v == "error"]
        empty_metrics = [k for k, v in metric_results.items() if v == "empty"]

        if total_records:
            logger.info("SensorTower collection complete for %s: %d records written", request.advertiser.name, total_records)
        else:
            logger.warning("SensorTower collection for %s captured zero records", request.advertiser.name)

        if failed_metrics:
            logger.warning("Failed metrics: %s", ", ".join(failed_metrics))
        if empty_metrics:
            logger.warning(
                "No data returned from API for %s (likely no data available for this period): %s",
                request.advertiser.name,
                ", ".join(empty_metrics),
            )

        if failed_metrics and total_records:
            status = "partial"
            message = f"Collected with {len(failed_metrics)} metric failures: {', '.join(failed_metrics)}"
        elif total_records:
            status = "success"
            message = "Collected SensorTower core metrics."
        else:
            status = "empty"
            message = "No SensorTower data was captured."

        return CollectorRunResult(
            platform=self.platform,
            advertiser_name=request.advertiser.name,
            status=status,
            message=message,
            records_written=total_records,
            metadata={
                "unified_app_id": unified_app_id,
                "category_id": category_state["id"],
                "category_name": category_state["name"],
                "metric_results": metric_results,
            },
        )

    async def _validate_session(self, page: Page) -> bool:
        """Check if the SensorTower session is still authenticated.

        Navigates to a lightweight authenticated endpoint and checks whether
        we get redirected to a login page or receive a 401/403.
        """
        try:
            response = await page.request.get(
                f"{self.settings.sensortower_base_url}/api/auth/me",
                timeout=10_000,
            )
            if response.status in (401, 403):
                logger.error("Session expired: got HTTP %d from auth check", response.status)
                return False
            # Check for login page redirect (URL changed to contain 'login' or 'signin')
            if "login" in response.url.lower() or "signin" in response.url.lower():
                logger.error("Session expired: redirected to login page (%s)", response.url)
                return False
            logger.info("Session validated (HTTP %d)", response.status)
            return True
        except Exception as exc:
            # If the auth endpoint doesn't exist, assume session is OK and proceed
            logger.debug("Session validation inconclusive (%s), proceeding", exc)
            return True

    def _today(self) -> date:
        return datetime.now(UTC).date()

    def _days_ago(self, days: int) -> date:
        return (datetime.now(UTC) - timedelta(days=days)).date()

    def _first_of_last_month(self) -> date:
        today = self._today()
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        return last_month_end.replace(day=1)

    def _last_of_last_month(self) -> date:
        today = self._today()
        return today.replace(day=1) - timedelta(days=1)

    def _build_url(self, path: str, params: dict) -> str:
        items: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, list):
                items.extend((key, str(item)) for item in value)
            else:
                items.append((key, str(value)))
        query = urlencode(items, doseq=True)
        base = urljoin(f"{self.settings.sensortower_base_url}/", path.lstrip("/"))
        return f"{base}?{query}" if query else base

    def _common_params(self, request: CollectorRunRequest, country: str) -> dict:
        profile = request.advertiser.platforms.sensortower
        return {
            "uai": profile.unified_app_id,
            "sia": profile.ios_app_id,
            "saa": profile.android_package,
            "country": country,
            "breakdown_attribute": "appId",
            "chart_plotting_type": "line",
            "granularity": "auto",
            "metricType": "absolute",
            "time_period": "day",
            "device": ["android", "iphone", "ipad"],
        }

    def _store_marketing_params(self, request: CollectorRunRequest, country: str, *, os: str = "unified") -> dict:
        profile = request.advertiser.platforms.sensortower
        return {
            "uai": profile.unified_app_id,
            "ssia": profile.ios_app_id,
            "ssaa": profile.android_package,
            "sia": profile.ios_app_id,
            "saa": profile.android_package,
            "os": os,
            "country": country,
        }

    async def _navigate_and_collect(
        self,
        page: Page,
        url: str,
        label: str,
        request: CollectorRunRequest,
        category_state: dict[str, str | None],
        *,
        is_first: bool = False,
        _retry: int = 0,
    ) -> list[dict]:
        """Navigate to a SPA route and intercept API responses.

        When is_first=True (first navigation of the run), uses a longer wait
        for the page to fully load. Subsequent navigations use shorter waits
        with randomized jitter to reduce detection risk.

        Retries once on network errors with exponential backoff.
        """
        if not is_first:
            await self._jitter(page)

        logger.debug("Navigating: %s [%s]", label, url[:120])
        captured: list[dict] = []
        tasks: list[asyncio.Task] = []

        async def process_response(response) -> None:
            if "/api/" not in response.url:
                return
            try:
                data = await response.json()
            except Exception:
                return
            payload = {"url": response.url, "data": data}
            captured.append(payload)
            self._update_detected_category(request, category_state, data)

        def handler(response) -> None:
            tasks.append(asyncio.create_task(process_response(response)))

        page.on("response", handler)
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.settings.collect_timeout_ms)
            except Exception as exc:
                if "Timeout" not in str(exc):
                    if _retry < 1:
                        logger.warning("Navigation error for %s: %s — retrying in %ds", label, exc, 3 * (_retry + 1))
                        page.remove_listener("response", handler)
                        await page.wait_for_timeout(3_000 * (_retry + 1))
                        return await self._navigate_and_collect(
                            page, url, label, request, category_state, is_first=is_first, _retry=_retry + 1
                        )
                    logger.error("Navigation error for %s after retry: %s", label, exc)
                    raise
                logger.debug("Navigation timeout for %s (non-fatal, waiting for API responses)", label)
            # First load needs longer wait; subsequent loads are faster since the SPA is cached
            wait_ms = 8_000 if is_first else random.randint(3_000, 5_000)
            await page.wait_for_timeout(wait_ms)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            page.remove_listener("response", handler)

        logger.debug("Captured %d API responses for %s", len(captured), label)
        if request.debug and captured:
            self._write_debug_dump(label, captured)

        return captured

    async def _api_fetch(
        self,
        page: Page,
        api_url: str,
        label: str,
        request: CollectorRunRequest,
        category_state: dict[str, str | None],
    ) -> list[dict]:
        """Fetch an API URL directly using the page's cookie jar, skipping page navigation.

        Returns a list of captured payloads in the same format as _navigate_and_collect
        so callers don't need to change their parsing logic.
        """
        await self._jitter(page)
        logger.debug("API fetch: %s [%s]", label, api_url[:120])
        try:
            response = await page.request.get(api_url, timeout=self.settings.collect_timeout_ms)
            if response.status in (401, 403):
                logger.error("API fetch %s returned HTTP %d (auth expired?)", label, response.status)
                return []
            if response.status == 429:
                logger.warning("API fetch %s returned HTTP 429 (rate limited), backing off", label)
                await page.wait_for_timeout(random.randint(5_000, 10_000))
                response = await page.request.get(api_url, timeout=self.settings.collect_timeout_ms)
                if response.status != 200:
                    logger.warning("API fetch %s retry failed with HTTP %d", label, response.status)
                    return []
            data = await response.json()
        except Exception as exc:
            logger.warning("API fetch %s failed: %s, falling back to navigation", label, exc)
            return await self._navigate_and_collect(page, api_url, label, request, category_state)

        payload = {"url": api_url, "data": data}
        self._update_detected_category(request, category_state, data)

        if request.debug:
            self._write_debug_dump(label, [payload])

        return [payload]

    async def _jitter(self, page: Page) -> None:
        """Random delay between API calls to mimic human pacing."""
        delay = random.randint(500, 2_000)
        await page.wait_for_timeout(delay)

    def _write_debug_dump(self, label: str, captured: list[dict]) -> None:
        debug_dir = self.settings.debug_dir / "sensortower"
        debug_dir.mkdir(parents=True, exist_ok=True)
        dump = [
            {
                "url": item["url"],
                "keys": list(item["data"].keys()) if isinstance(item["data"], dict) else [],
                "sample": json.dumps(item["data"])[:600],
            }
            for item in captured
        ]
        target = debug_dir / f"{label}-{int(datetime.now(UTC).timestamp())}.json"
        target.write_text(json.dumps(dump, indent=2), encoding="utf-8")

    def _update_detected_category(
        self,
        request: CollectorRunRequest,
        category_state: dict[str, str | None],
        data: dict,
    ) -> None:
        if category_state["id"] is not None:
            return

        apps = data.get("apps") if isinstance(data, dict) else None
        if not isinstance(apps, list):
            return

        identifiers = {
            str(value)
            for value in [
                request.advertiser.platforms.sensortower.unified_app_id,
                request.advertiser.platforms.sensortower.publisher_id,
                request.advertiser.platforms.sensortower.ios_app_id,
                request.advertiser.platforms.sensortower.android_package,
            ]
            if value
        }
        category_id, category_name = detect_category(identifiers, data)
        if category_id:
            category_state["id"] = category_id
            category_state["name"] = category_name

    async def _collect_downloads(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        date_start: date,
        date_end: date,
        category_state: dict[str, str | None],
    ) -> int:
        url = self._build_url(
            "/app-analysis/downloads",
            {
                **self._common_params(request, country),
                "os": "unified",
                "start_date": date_start.isoformat(),
                "end_date": date_end.isoformat(),
                "measure": "units",
                "chart_plotting_type": "area",
            },
        )
        # First navigation of the run: longer wait for SPA bootstrap + category detection
        captured = await self._navigate_and_collect(page, url, "downloads", request, category_state, is_first=True)

        for item in captured:
            data = item["data"]
            if "timeseries/apps" not in item["url"] or not data.get("apps"):
                continue
            download_rows, usage_rows = parse_download_rows(data, request.advertiser.name, country)
            written = repository.upsert_downloads(download_rows)
            written += repository.upsert_usage(usage_rows)
            logger.info("downloads/%s: %d records", country, written)
            return written
        logger.warning("downloads/%s: no matching API response", country)
        return 0

    async def _collect_retention(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        category_state: dict[str, str | None],
    ) -> int:
        start = self._first_of_last_month()
        end = self._last_of_last_month()
        url = self._build_url(
            "/app-analysis/retention",
            {
                **self._common_params(request, country),
                "os": "unified",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "granularity": "daily",
                "retention_period": "day",
                "retention_measure": "retentionD1",
                "retention_chart_type": "curve",
            },
        )
        captured = await self._navigate_and_collect(page, url, "retention", request, category_state)

        for item in captured:
            data = item["data"]
            if "retention_chart" not in item["url"]:
                continue
            rows = parse_retention_rows(data, request.advertiser.name, country, start)
            written = repository.upsert_retention(rows)
            logger.info("retention/%s: %d records", country, written)
            return written
        logger.warning("retention/%s: no matching API response", country)
        return 0

    async def _collect_impression_share(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        date_start: date,
        date_end: date,
        category_state: dict[str, str | None],
    ) -> int:
        base_params = {
            **self._common_params(request, country),
            "os": "unified",
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "granularity": "auto",
            "platform_type": "networks",
            "impression_share_metric_option": "all",
            "network": NETWORKS,
        }

        aggregate_url = self._build_url(
            "/app-analysis/impression-share",
            {**base_params, "breakdown_attribute": "appId"},
        )
        network_url = self._build_url(
            "/app-analysis/impression-share",
            {**base_params, "breakdown_attribute": "network"},
        )

        aggregate = await self._navigate_and_collect(
            page, aggregate_url, "impression-share", request, category_state
        )
        per_network = await self._navigate_and_collect(
            page, network_url, "impression-share-network", request, category_state
        )

        rows = []
        for collection, default_network in [(aggregate, "all"), (per_network, None)]:
            for item in collection:
                if "impression_share_chart" not in item["url"]:
                    continue
                rows.extend(
                    parse_impression_share_rows(
                        item["data"], request.advertiser.name, country, default_network=default_network
                    )
                )
        written = repository.upsert_impression_share(rows)
        if written:
            logger.info("impression_share/%s: %d records", country, written)
        else:
            logger.warning("impression_share/%s: no data captured", country)
        return written

    async def _collect_demographics(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        category_state: dict[str, str | None],
    ) -> int:
        profile = request.advertiser.platforms.sensortower
        url = self._build_url(
            "/usage-intel/demographics",
            {
                "uai": profile.unified_app_id,
                "sia": profile.ios_app_id,
                "saa": profile.android_package,
                "os": "unified",
                "country": country,
            },
        )
        captured = await self._navigate_and_collect(page, url, "usage", request, category_state)

        for item in captured:
            if "/usage/demographics" not in item["url"]:
                continue
            rows = parse_demographic_rows(item["data"], request.advertiser.name, country)
            written = repository.upsert_demographics(rows)
            logger.info("demographics/%s: %d records", country, written)
            return written
        logger.warning("demographics/%s: no matching API response", country)
        return 0

    async def _collect_engagement(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        date_start: date,
        date_end: date,
        category_state: dict[str, str | None],
    ) -> int:
        rows_by_key: dict[tuple[str, date, str], dict] = {}

        for measure in ["timeSpent", "sessionCount"]:
            url = self._build_url(
                "/usage-intel/active-users",
                {
                    "uai": request.advertiser.platforms.sensortower.unified_app_id,
                    "sia": request.advertiser.platforms.sensortower.ios_app_id,
                    "saa": request.advertiser.platforms.sensortower.android_package,
                    "os": "unified",
                    "country": country,
                    "start_date": date_start.isoformat(),
                    "end_date": date_end.isoformat(),
                    "active_user_measure": measure,
                    "granularity": "auto",
                    "time_period": "day",
                },
            )
            captured = await self._navigate_and_collect(
                page, url, f"engagement-{measure}", request, category_state
            )
            for item in captured:
                if "usage" not in item["url"] and "active_user" not in item["url"]:
                    continue
                values = item["data"].get("app_data") or item["data"].get("data") or normalize_array(item["data"])
                if not isinstance(values, list):
                    continue
                rows_by_key = merge_engagement_rows(
                    values, request.advertiser.name, country, existing=rows_by_key
                )

        written = repository.upsert_usage(list(rows_by_key.values()))
        if written:
            logger.info("engagement/%s: %d records", country, written)
        else:
            logger.warning("engagement/%s: no data captured", country)
        return written

    async def _collect_rankings(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        category_state: dict[str, str | None],
    ) -> int:
        category_id = category_state["id"]
        if category_id is None:
            logger.debug("rankings/%s: skipped (no category detected yet)", country)
            return 0

        url = self._build_url(
            "/ad-intel/advertisers/top-apps",
            {
                "uai": request.advertiser.platforms.sensortower.unified_app_id,
                "sia": request.advertiser.platforms.sensortower.ios_app_id,
                "saa": request.advertiser.platforms.sensortower.android_package,
                "os": "unified",
                "country": country,
                "category": category_id,
                "period": "month",
                "network": NETWORKS[:15],
            },
        )
        captured = await self._navigate_and_collect(page, url, "rankings", request, category_state)
        target_id = request.advertiser.platforms.sensortower.unified_app_id

        for item in captured:
            if "top_apps" not in item["url"] and "top-apps" not in item["url"]:
                continue
            row = parse_ranking_row(
                item["data"],
                request.advertiser.name,
                country,
                target_id,
                self._today(),
                category_state["name"] or category_id,
            )
            if row is not None:
                written = repository.upsert_rankings([row])
                logger.info("rankings/%s: %d records", country, written)
                return written
        logger.warning("rankings/%s: no matching API response", country)
        return 0

    async def _collect_reviews(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        date_start: date,
        date_end: date,
        category_state: dict[str, str | None],
    ) -> int:
        url = self._build_url(
            "/store-marketing/reviews",
            {
                **self._store_marketing_params(request, country),
                "start_date": date_start.isoformat(),
                "end_date": date_end.isoformat(),
                "granularity": "daily",
                "breakdown_attribute": "starRating",
                "chart_plotting_type": "line",
                "metric": "ratingCount",
                "rating": ["5", "4", "3", "2", "1"],
            },
        )
        captured = await self._navigate_and_collect(page, url, "reviews", request, category_state)

        written = 0
        for item in captured:
            if "reviews_chart" in item["url"]:
                written += repository.upsert_reviews(
                    parse_review_rows(item["data"], request.advertiser.name, country)
                )
            if "get_reviews" in item["url"]:
                written += repository.upsert_review_texts(
                    parse_review_text_rows(item["data"], request.advertiser.name)
                )
        if written:
            logger.info("reviews/%s: %d records", country, written)
        else:
            logger.warning("reviews/%s: no data captured", country)
        return written

    async def _collect_creatives(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        date_start: date,
        date_end: date,
        category_state: dict[str, str | None],
    ) -> int:
        url = self._build_url(
            "/app-analysis/creative-gallery",
            {
                "uai": request.advertiser.platforms.sensortower.unified_app_id,
                "sia": request.advertiser.platforms.sensortower.ios_app_id,
                "saa": request.advertiser.platforms.sensortower.android_package,
                "os": "unified",
                "country": country,
                "start_date": date_start.isoformat(),
                "end_date": date_end.isoformat(),
                "granularity": "weekly",
                "page": 1,
                "page_size": 25,
                "report_type": "masonry",
            },
        )
        captured = await self._navigate_and_collect(page, url, "creatives", request, category_state)
        metadata_map = build_creative_metadata_map(captured)

        for item in captured:
            if "creative_gallery_creatives" not in item["url"]:
                continue
            rows = parse_creative_rows(item["data"], request.advertiser.name, metadata_map)
            written = repository.upsert_creatives(rows)
            logger.info("creatives/%s: %d records", country, written)
            return written
        logger.warning("creatives/%s: no matching API response", country)
        return 0

    async def _collect_aso_keywords(
        self,
        page: Page,
        request: CollectorRunRequest,
        repository: SensorTowerRepository,
        country: str,
        category_state: dict[str, str | None],
    ) -> int:
        total_written = 0
        for device in ("iphone", "android"):
            url = self._build_url(
                "/store-marketing/aso/performance-tracking",
                {
                    **self._store_marketing_params(request, country),
                    "device": device,
                },
            )
            captured = await self._navigate_and_collect(page, url, f"aso-{device}", request, category_state)

            for item in captured:
                if (
                    "aso_keywords_management_table" not in item["url"]
                    and "aso_performance_tracking_kpi" not in item["url"]
                ):
                    continue
                rows = parse_aso_keyword_rows(item["data"], request.advertiser.name, country, device=device)
                if rows:
                    total_written += repository.upsert_aso_keywords(rows)
                    break

        if total_written:
            logger.info("aso_keywords/%s: %d records", country, total_written)
        else:
            logger.warning("aso_keywords/%s: no matching API response", country)
        return total_written
