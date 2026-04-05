from __future__ import annotations

import asyncio
import logging

from adintel.collectors.base import PlatformCollector
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName

logger = logging.getLogger("adintel.adclarity")


class AdClarityCollector(PlatformCollector):
    """AdClarity collector stub.

    Real data extraction is deferred until account access is available.
    The login flow works, but collect() returns a pending_port status
    without parsing any data.
    """

    platform = PlatformName.ADCLARITY
    state_key = "adclarity"

    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        async with self.browser.session(self.state_key, headless=headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(self.settings.adclarity_base_url, wait_until="domcontentloaded")
            await asyncio.to_thread(
                input,
                "Complete the AdClarity login in the browser, then press Enter here to close the session.",
            )

    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        advertiser_id = request.advertiser.platforms.adclarity.advertiser_id
        if not advertiser_id:
            return CollectorRunResult(
                platform=self.platform,
                advertiser_name=request.advertiser.name,
                status="skipped",
                message="No AdClarity advertiser identifier is configured.",
            )

        logger.warning(
            "AdClarity collection for %s is not yet implemented — returning pending_port status",
            request.advertiser.name,
        )

        async with self.browser.session(self.state_key, headless=request.headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(
                f"{self.settings.adclarity_base_url}/ad-intelligence/advertiser/{advertiser_id}",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(8_000)

        return CollectorRunResult(
            platform=self.platform,
            advertiser_name=request.advertiser.name,
            status="pending_port",
            message="AdClarity browser workflow is wired; dataset extraction still needs a port into normalized tables.",
            metadata={"advertiser_id": advertiser_id},
        )
