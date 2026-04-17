from __future__ import annotations

import asyncio

from adintel.collectors.base import PlatformCollector
from adintel.core.models import CollectorRunRequest, CollectorRunResult, PlatformName
from adintel.platforms.otterlyai import APP_URL, close_context, ensure_page, launch_context


class OtterlyCollector(PlatformCollector):
    platform = PlatformName.OTTERLY
    state_key = "otterly"

    async def login(self, *, headless: bool = False, use_cdp: bool = False) -> None:
        if use_cdp:
            raise RuntimeError("Otterly login does not support --use-cdp yet. Use the persistent browser profile flow instead.")

        playwright, context = await launch_context(headless=headless)
        try:
            page = await ensure_page(context)
            await page.goto(f"{APP_URL}/sign-in", wait_until="domcontentloaded")
            await asyncio.to_thread(
                input,
                "Complete the Otterly login in the browser, then press Enter here to save the session.",
            )
        finally:
            await close_context(playwright, context)

    async def collect(
        self,
        request: CollectorRunRequest,
        *,
        use_cdp: bool = False,
    ) -> CollectorRunResult:
        return CollectorRunResult(
            platform=self.platform,
            advertiser_name=request.advertiser.name,
            status="skipped",
            message="Otterly collection is not wired into `adintel collect advertiser` yet. Use the Otterly batch/API scripts.",
        )
