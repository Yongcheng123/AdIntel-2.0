from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger("adintel.service")

from adintel.core.alerts import check_and_notify
from adintel.core.browser import BrowserManager
from adintel.core.models import AdvertiserProfile, CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.core.settings import AppSettings
from adintel.db.repositories import CollectionHealthRepository, ScrapeRunRepository
from adintel.platforms.adclarity import AdClarityCollector
from adintel.platforms.sensortower import SensorTowerCollector


class CollectorService:
    def __init__(self, settings: AppSettings, session: Session) -> None:
        self.settings = settings
        self.session = session
        browser = BrowserManager(settings)
        self.collectors = {
            PlatformName.ADCLARITY: AdClarityCollector(settings, browser, session),
            PlatformName.SENSORTOWER: SensorTowerCollector(settings, browser, session),
        }
        self.runs = ScrapeRunRepository(session)

    async def login(self, platform: PlatformName, *, headless: bool = False, use_cdp: bool = False) -> None:
        await self.collectors[platform].login(headless=headless, use_cdp=use_cdp)

    async def collect_one(
        self,
        advertiser: AdvertiserProfile,
        platform: PlatformName,
        *,
        countries: list[str] | None = None,
        headless: bool = False,
        debug: bool = False,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        logger.info("Starting collection: %s on %s", advertiser.name, platform.value)
        run = self.runs.start(advertiser.name, platform.value)
        request = CollectorRunRequest(
            advertiser=advertiser,
            platform=platform,
            countries=countries or advertiser.countries,
            headless=headless,
            debug=debug,
        )
        try:
            result = await self.collectors[platform].collect(request, use_cdp=use_cdp)
        except Exception as exc:
            logger.error("Collection failed for %s on %s: %s", advertiser.name, platform.value, exc)
            self.session.rollback()
            self.runs.finish(run, status="error", message=str(exc))
            raise

        logger.info("Collection finished: %s on %s → %s (%d records)", advertiser.name, platform.value, result.status, result.records_written)
        self.runs.finish(
            run,
            status=result.status,
            message=result.message,
            metadata={
                "records_written": result.records_written,
                "result": result.metadata,
            },
        )

        # Check health thresholds and send webhook alerts if configured
        try:
            health_repo = CollectionHealthRepository(self.session)
            alerts = check_and_notify(self.settings, health_repo, advertiser.name, platform.value)
            if alerts:
                logger.warning("%d alert(s) for %s/%s", len(alerts), advertiser.name, platform.value)
        except Exception as exc:
            logger.debug("Alert check failed (non-fatal): %s", exc)

        return result

    async def collect_many(
        self,
        advertiser: AdvertiserProfile,
        *,
        platforms: list[PlatformName],
        countries: list[str] | None = None,
        headless: bool = False,
        debug: bool = False,
        use_cdp: bool = False,
    ) -> list[CollectorRunResult]:
        # Run sequentially to avoid concurrent access to the shared DB session
        results: list[CollectorRunResult] = []
        for platform in platforms:
            result = await self.collect_one(
                advertiser,
                platform,
                countries=countries,
                headless=headless,
                debug=debug,
                use_cdp=use_cdp,
            )
            results.append(result)
        return results
