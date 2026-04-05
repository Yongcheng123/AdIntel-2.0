from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from playwright.async_api import BrowserContext, Page, async_playwright

from adintel.core.settings import AppSettings

logger = logging.getLogger("adintel.browser")

try:
    from playwright_stealth import Stealth
except ImportError:  # pragma: no cover - optional dependency during bootstrap
    Stealth = None


STEALTH_FALLBACK_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""


class BrowserManager:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    @staticmethod
    def _is_profile_lock_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "processsingleton" in text
            or "singletonlock" in text
            or "profile directory is already in use" in text
        )

    @staticmethod
    def _clear_profile_lock_files(profile_dir) -> int:
        removed = 0
        for filename in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = profile_dir / filename
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                logger.debug("Could not remove lock artifact %s: %s", path, exc)
        return removed

    async def apply_stealth(self, page: Page) -> None:
        if Stealth is not None:
            logger.debug("Applying playwright-stealth plugin")
            await Stealth().apply_stealth_async(page)
            return

        logger.debug("playwright-stealth not installed, using fallback script")
        await page.add_init_script(STEALTH_FALLBACK_SCRIPT)

    async def launch_persistent_context(
        self,
        state_key: str,
        *,
        headless: bool | None = None,
    ) -> BrowserContext:
        profile_dir = self.settings.browser_state_dir / state_key
        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Launching persistent browser context: %s (headless=%s)", state_key, headless)

        for attempt in (1, 2):
            playwright = await async_playwright().start()
            try:
                context = await playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    channel=self.settings.browser_channel,
                    headless=self.settings.default_headless if headless is None else headless,
                    viewport={"width": 1440, "height": 900},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                setattr(context, "_adintel_playwright", playwright)
                return context
            except Exception as exc:
                await playwright.stop()
                if attempt == 1 and self._is_profile_lock_error(exc):
                    removed = self._clear_profile_lock_files(profile_dir)
                    logger.warning(
                        "Profile lock detected for %s. Removed %d lock file(s); retrying once.",
                        profile_dir,
                        removed,
                    )
                    await asyncio.sleep(1)
                    continue
                raise

        raise RuntimeError("Failed to launch browser context after lock recovery retry.")

    async def connect_cdp(self) -> BrowserContext:
        logger.info("Connecting to browser via CDP: %s", self.settings.cdp_url)
        playwright = await async_playwright().start()
        browser = await playwright.chromium.connect_over_cdp(self.settings.cdp_url)
        if not browser.contexts:
            await browser.close()
            await playwright.stop()
            raise RuntimeError("No browser context is available on the configured CDP target.")

        context = browser.contexts[0]
        setattr(context, "_adintel_playwright", playwright)
        setattr(context, "_adintel_browser", browser)
        return context

    async def close(self, context: BrowserContext) -> None:
        browser = getattr(context, "_adintel_browser", None)
        playwright = getattr(context, "_adintel_playwright", None)

        await context.close()
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    @asynccontextmanager
    async def session(
        self,
        state_key: str,
        *,
        headless: bool | None = None,
        use_cdp: bool = False,
    ):
        context = await (self.connect_cdp() if use_cdp else self.launch_persistent_context(state_key, headless=headless))
        try:
            yield context
        finally:
            await self.close(context)
