"""
AppFollow collector.

Scrapes app reviews (text, rating, sentiment, keyword tags) from AppFollow's
web UI by intercepting the internal API calls made by the SPA.

The exact API shape is discovered on first run via the debug dump mechanism.
Run with --debug to write captured payloads to state/debug/appfollow/ and
inspect the JSON files there to verify field names before tightening the parser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import yaml
from playwright.async_api import Page

from adintel.collectors.base import PlatformCollector
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.db.repositories import AppFollowRepository, ScrapeRunMetricRepository, ScrapeRunRepository
from adintel.platforms.appfollow_parsers import (
    extract_next_page_cursor,
    parse_review_rows,
    write_debug_dump,
)

logger = logging.getLogger("adintel.appfollow")

# Maximum review pages to fetch per (advertiser, country) combination.
# Prevents runaway pagination on large apps.
MAX_PAGES = 20

# Number of days of reviews to collect per run.
LOOKBACK_DAYS = 90


def load_appfollow_groups(config_path: Path) -> dict:
    """
    Load config/appfollow_groups.yaml and return a flat lookup of:
      { advertiser_name_lower: {"item_id": "...", "workspace": "...", "countries": [...]} }

    Both the primary advertiser and each competitor are included so either
    can be looked up by name.
    """
    if not config_path.exists():
        logger.warning("AppFollow groups config not found: %s", config_path)
        return {}

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    workspace = (raw.get("workspace") or "").strip()
    lookup: dict = {}

    for group in raw.get("groups") or []:
        countries = group.get("countries") or ["US"]
        # Primary advertiser
        name = (group.get("advertiser") or "").strip()
        item_id = str(group.get("appfollow_item_id") or "").strip()
        if name and item_id:
            lookup[name.lower()] = {
                "name": name,
                "item_id": item_id,
                "workspace": workspace,
                "countries": countries,
            }
        # Competitors
        for comp in group.get("competitors") or []:
            comp_name = (comp.get("name") or "").strip()
            comp_item_id = str(comp.get("appfollow_item_id") or "").strip()
            if comp_name and comp_item_id:
                lookup[comp_name.lower()] = {
                    "name": comp_name,
                    "item_id": comp_item_id,
                    "workspace": workspace,
                    "countries": countries,
                }

    return lookup


class AppFollowCollector(PlatformCollector):
    platform = PlatformName.APPFOLLOW
    state_key = "appfollow"

    # -----------------------------------------------------------------
    # Login
    # -----------------------------------------------------------------

    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        async with self.browser.session(self.state_key, headless=headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(self.settings.appfollow_base_url, wait_until="domcontentloaded")
            await asyncio.to_thread(
                input,
                "Complete the AppFollow login in the browser window, then press Enter here to save the session.",
            )

    # -----------------------------------------------------------------
    # Standard collect() — looks up item_id from config then delegates
    # -----------------------------------------------------------------

    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        # item_id can be injected by the batch script via request.extra,
        # or resolved from the config file by advertiser name.
        item_id: str | None = request.extra.get("appfollow_item_id")
        workspace: str | None = request.extra.get("appfollow_workspace")

        if not item_id:
            groups = load_appfollow_groups(self.settings.appfollow_group_config_file)
            entry = groups.get(request.advertiser.name.lower())
            if entry:
                item_id = entry["item_id"]
                workspace = workspace or entry["workspace"]

        if not item_id:
            logger.warning(
                "Skipping %s: no AppFollow item_id found in request.extra or config/appfollow_groups.yaml",
                request.advertiser.name,
            )
            return CollectorRunResult(
                platform=self.platform,
                advertiser_name=request.advertiser.name,
                status="skipped",
                message=(
                    "No AppFollow item_id configured. "
                    "Add it to config/appfollow_groups.yaml or pass via request.extra."
                ),
            )

        workspace = workspace or self.settings.appfollow_workspace
        if not workspace:
            return CollectorRunResult(
                platform=self.platform,
                advertiser_name=request.advertiser.name,
                status="error",
                message=(
                    "AppFollow workspace is not set. "
                    "Set ADINTEL_APPFOLLOW_WORKSPACE in .env or add workspace: to appfollow_groups.yaml."
                ),
            )

        return await self._run_collection(
            request=request,
            item_id=item_id,
            workspace=workspace,
            use_cdp=use_cdp,
        )

    # -----------------------------------------------------------------
    # Core collection logic (also called directly by the batch script)
    # -----------------------------------------------------------------

    async def collect_app(
        self,
        page: Page,
        advertiser_name: str,
        item_id: str,
        workspace: str,
        countries: list[str],
        *,
        headless: bool = False,
        debug: bool = False,
    ) -> dict:
        """
        Collect reviews for a single app from an already-open browser page.

        Called by the batch script which manages its own browser session.
        Returns {"records_written": int, "status": str, "message": str}.
        """
        repository = AppFollowRepository(self.session)
        scrape_runs = ScrapeRunRepository(self.session)
        run = scrape_runs.start(advertiser_name, self.platform.value)

        total_records = 0
        metric_results: dict[str, str] = {}
        date_end = datetime.now(UTC).date()
        date_start = date_end - timedelta(days=LOOKBACK_DAYS)
        metric_runs = ScrapeRunMetricRepository(self.session)

        for country in countries:
            key = f"reviews/{country}"
            metric_run = metric_runs.start(run.id, key)
            try:
                written = await self._collect_country_reviews(
                    page=page,
                    advertiser_name=advertiser_name,
                    item_id=item_id,
                    workspace=workspace,
                    country=country,
                    date_start=date_start,
                    date_end=date_end,
                    repository=repository,
                    debug=debug,
                    is_first_country=(country == countries[0]),
                )
                total_records += written
                metric_results[key] = "success" if written else "empty"
                metric_runs.finish(
                    metric_run,
                    status="success" if written else "empty",
                    records_written=written,
                    message=None if written else "No review data returned for this country.",
                )
            except Exception as exc:
                logger.error(
                    "AppFollow review collection failed for %s/%s: %s",
                    advertiser_name,
                    country,
                    exc,
                    exc_info=True,
                )
                metric_results[key] = "error"
                self.session.rollback()
                metric_runs.finish(metric_run, status="error", records_written=0, message=str(exc))

        failed = [k for k, v in metric_results.items() if v == "error"]
        if failed and total_records:
            status = "partial"
            message = f"Collected with {len(failed)} failures: {', '.join(failed)}"
        elif total_records:
            status = "success"
            message = f"Collected {total_records} AppFollow review rows."
        else:
            status = "empty"
            message = "No AppFollow review data captured."

        scrape_runs.finish(
            run,
            status=status,
            message=message,
            metadata={"item_id": item_id, "metric_results": metric_results},
        )
        return {"records_written": total_records, "status": status, "message": message}

    async def _run_collection(
        self,
        request: CollectorRunRequest,
        item_id: str,
        workspace: str,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        """Open a browser session and run collection for one app."""
        total_records = 0
        metric_results: dict[str, str] = {}
        date_end = datetime.now(UTC).date()
        date_start = date_end - timedelta(days=LOOKBACK_DAYS)
        scrape_run_id = request.extra.get("scrape_run_id")
        metric_runs = (
            ScrapeRunMetricRepository(self.session) if isinstance(scrape_run_id, int) else None
        )

        async with self.browser.session(self.state_key, headless=request.headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)

            if not await self._validate_session(page, workspace):
                return CollectorRunResult(
                    platform=self.platform,
                    advertiser_name=request.advertiser.name,
                    status="auth_expired",
                    message="AppFollow session has expired. Run: adintel login appfollow",
                )

            repository = AppFollowRepository(self.session)
            for country in request.countries:
                key = f"reviews/{country}"
                metric_run = (
                    metric_runs.start(scrape_run_id, key)
                    if metric_runs is not None and scrape_run_id is not None
                    else None
                )
                try:
                    written = await self._collect_country_reviews(
                        page=page,
                        advertiser_name=request.advertiser.name,
                        item_id=item_id,
                        workspace=workspace,
                        country=country,
                        date_start=date_start,
                        date_end=date_end,
                        repository=repository,
                        debug=request.debug,
                        is_first_country=(country == request.countries[0]),
                    )
                    total_records += written
                    metric_results[key] = "success" if written else "empty"
                    if metric_run is not None:
                        metric_runs.finish(
                            metric_run,
                            status="success" if written else "empty",
                            records_written=written,
                            message=None if written else "No review data returned for this country.",
                        )
                except Exception as exc:
                    logger.error(
                        "AppFollow review collection failed for %s/%s: %s",
                        request.advertiser.name,
                        country,
                        exc,
                        exc_info=True,
                    )
                    metric_results[key] = "error"
                    self.session.rollback()
                    if metric_run is not None:
                        metric_runs.finish(metric_run, status="error", records_written=0, message=str(exc))

        failed = [k for k, v in metric_results.items() if v == "error"]
        if failed and total_records:
            status, message = "partial", f"Collected with {len(failed)} failures: {', '.join(failed)}"
        elif total_records:
            status, message = "success", f"Collected {total_records} AppFollow review rows."
        else:
            status, message = "empty", "No AppFollow review data captured."

        return CollectorRunResult(
            platform=self.platform,
            advertiser_name=request.advertiser.name,
            status=status,
            message=message,
            records_written=total_records,
            metadata={"item_id": item_id, "metric_results": metric_results},
        )

    # -----------------------------------------------------------------
    # Per-country review collection with pagination
    # -----------------------------------------------------------------

    async def _collect_country_reviews(
        self,
        page: Page,
        advertiser_name: str,
        item_id: str,
        workspace: str,
        country: str,
        date_start: date,
        date_end: date,
        repository: AppFollowRepository,
        *,
        debug: bool = False,
        is_first_country: bool = False,
    ) -> int:
        """Collect all review pages for one (app, country) combination."""
        params = urlencode({
            "from": date_start.isoformat(),
            "to": date_end.isoformat(),
            "country": country.lower(),
            "itemId": item_id,
        })
        # Navigate to /reviews — triggers the reviews feed API call from the SPA
        reviews_page_url = f"{self.settings.appfollow_base_url}/apps/{workspace}/reviews?{params}"

        total_written = 0
        page_num = 1

        # First page: navigate to the SPA reviews page, intercept the API call
        captured = await self._navigate_and_capture(
            page, reviews_page_url, f"{advertiser_name}/{country}/p1", is_first=is_first_country
        )

        if debug:
            write_debug_dump(f"{advertiser_name}-{country}-p{page_num}", captured, self.settings.debug_dir)

        # Find the reviews feed response and its original request so we can replay it for pagination
        feed_entry: dict | None = next(
            (c for c in captured if "r2r/reviews/feed" in c["url"]),
            None,
        )
        # Reconstruct original request URL + body for use in paginated fetch calls
        feed_url: str | None = feed_entry["url"] if feed_entry else None
        feed_method: str = (feed_entry or {}).get("request_method", "GET")
        feed_post_data: str | None = (feed_entry or {}).get("request_post_data")

        while page_num <= MAX_PAGES:
            review_rows: list[dict] = []
            next_cursor = None

            for item in captured:
                rows = parse_review_rows(item["data"], advertiser_name, country, item_id)
                review_rows.extend(rows)
                if rows and next_cursor is None:
                    next_cursor = extract_next_page_cursor(item["data"])

            if review_rows:
                written = repository.upsert_reviews(review_rows)
                total_written += written
                logger.info(
                    "AppFollow %s/%s page %d: %d records upserted",
                    advertiser_name,
                    country,
                    page_num,
                    written,
                )
            else:
                logger.debug(
                    "AppFollow %s/%s page %d: no reviews in %d captured responses. URLs: %s",
                    advertiser_name,
                    country,
                    page_num,
                    len(captured),
                    [c["url"] for c in captured if "client-api" in c["url"]],
                )
                break

            if next_cursor is None:
                logger.info(
                    "AppFollow %s/%s: no next cursor, done at page %d",
                    advertiser_name,
                    country,
                    page_num,
                )
                break

            if feed_url is None:
                logger.warning(
                    "AppFollow %s/%s: cannot paginate — reviews feed URL not captured. Stopping at page %d.",
                    advertiser_name,
                    country,
                    page_num,
                )
                break

            page_num += 1
            await page.wait_for_timeout(random.randint(800, 2_000))

            # Paginate by replaying the original API request via browser fetch (reuses session cookies).
            # This avoids SPA navigation which resets state.
            paginated_data = await self._fetch_reviews_page(
                page=page,
                feed_url=feed_url,
                method=feed_method,
                post_data=feed_post_data,
                cursor=next_cursor,
            )
            if debug:
                write_debug_dump(
                    f"{advertiser_name}-{country}-p{page_num}",
                    [{"url": feed_url, "data": paginated_data}],
                    self.settings.debug_dir,
                )
            captured = [{"url": feed_url, "data": paginated_data}] if paginated_data else []

        return total_written

    async def _fetch_reviews_page(
        self,
        page: Page,
        feed_url: str,
        method: str,
        post_data: str | None,
        cursor,
    ) -> dict | None:
        """
        Fetch a paginated reviews page by replaying the API request through the browser's
        fetch() function (inherits the SPA's session cookies automatically).

        If the original request was POST with JSON body, inject cursor into the body.
        If GET, inject cursor as a query parameter.
        """
        try:
            if method.upper() == "POST" and post_data:
                try:
                    body = json.loads(post_data)
                    body["cursor"] = cursor
                    body_str = json.dumps(body)
                except (json.JSONDecodeError, TypeError):
                    body_str = post_data  # fallback: send as-is
                js = f"""
                    async () => {{
                        const r = await fetch({json.dumps(feed_url)}, {{
                            method: 'POST',
                            headers: {{'Content-Type': 'application/json'}},
                            body: {json.dumps(body_str)},
                            credentials: 'include',
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """
            else:
                # GET: append cursor as query param
                sep = "&" if "?" in feed_url else "?"
                paginated_url = f"{feed_url}{sep}cursor={cursor}"
                js = f"""
                    async () => {{
                        const r = await fetch({json.dumps(paginated_url)}, {{
                            credentials: 'include',
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """
            result = await page.evaluate(js)
            if result is None:
                logger.warning("AppFollow paginated fetch returned null for cursor=%s", cursor)
            return result
        except Exception as exc:
            logger.error("AppFollow paginated fetch failed: %s", exc)
            return None

    # -----------------------------------------------------------------
    # Network interception
    # -----------------------------------------------------------------

    async def _navigate_and_capture(
        self,
        page: Page,
        url: str,
        label: str,
        *,
        is_first: bool = False,
    ) -> list[dict]:
        """
        Navigate to `url` and capture all JSON API responses.

        Intercepts any response whose URL contains /api/, /watch/, or ends in .json,
        and whose Content-Type header includes "json". This is intentionally broad
        so we discover the real endpoint paths on the first run.
        """
        captured: list[dict] = []
        tasks: list[asyncio.Task] = []

        if not is_first:
            await page.wait_for_timeout(random.randint(800, 2_000))

        async def process_response(response) -> None:
            url_lower = response.url.lower()
            content_type = (response.headers.get("content-type") or "").lower()
            if not (
                "client-api" in url_lower       # AppFollow internal API
                or "/api/" in url_lower
                or "/watch/" in url_lower
                or ".json" in url_lower
                or "json" in content_type
            ):
                return
            try:
                data = await response.json()
            except Exception:
                return
            entry: dict = {"url": response.url, "data": data}
            # Capture request details so we can replay paginated calls directly
            req = response.request
            if req:
                entry["request_method"] = req.method
                try:
                    entry["request_post_data"] = req.post_data
                except Exception:
                    pass
            captured.append(entry)

        def handler(response) -> None:
            tasks.append(asyncio.create_task(process_response(response)))

        page.on("response", handler)
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.settings.collect_timeout_ms)
            except Exception as exc:
                if "Timeout" not in str(exc):
                    logger.error("Navigation error for %s: %s", label, exc)
                    raise
                logger.debug("Navigation timeout for %s (non-fatal, waiting for API responses)", label)

            wait_ms = 8_000 if is_first else random.randint(3_000, 5_000)
            await page.wait_for_timeout(wait_ms)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            page.remove_listener("response", handler)

        logger.debug("AppFollow captured %d API responses for %s", len(captured), label)
        return captured

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def _validate_session(self, page: Page, workspace: str) -> bool:
        """Return False if the browser is on a login/auth page."""
        try:
            response = await page.goto(
                f"{self.settings.appfollow_base_url}/apps/{workspace}",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            final_url = page.url
            if any(x in final_url for x in ("login", "signin", "sign-in", "/auth")):
                logger.error("AppFollow session expired: redirected to %s", final_url)
                return False
            status = response.status if response else 0
            if status in (401, 403):
                logger.error("AppFollow session expired: HTTP %d", status)
                return False
            logger.info("AppFollow session valid (landed on %s)", final_url)
            return True
        except Exception as exc:
            logger.debug("AppFollow session check inconclusive (%s), proceeding", exc)
            return True
