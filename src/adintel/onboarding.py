from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import yaml
from playwright.async_api import Page

from adintel.core.browser import BrowserManager
from adintel.core.models import AdvertiserCatalog, AdvertiserProfile
from adintel.core.settings import AppSettings
from adintel.platforms.sensortower_parsers import CATEGORY_NAMES


@dataclass
class OnboardingResult:
    name: str
    status: str
    message: str
    advertiser: AdvertiserProfile | None = None
    candidates: list[dict] | None = None


def save_catalog(path: Path, catalog: AdvertiserCatalog) -> None:
    payload = {"advertisers": [advertiser.model_dump(mode="json", exclude_none=True) for advertiser in catalog.advertisers]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def upsert_catalog_advertiser(catalog: AdvertiserCatalog, advertiser: AdvertiserProfile) -> None:
    for index, existing in enumerate(catalog.advertisers):
        if existing.name == advertiser.name:
            catalog.advertisers[index] = advertiser
            return
    catalog.advertisers.append(advertiser)


def load_onboarding_requests(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Onboarding input file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    requests = data.get("advertisers")
    if not isinstance(requests, list):
        raise ValueError("Onboarding input must contain an 'advertisers' list.")
    return requests


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _candidate_score(request_name: str, entity: dict) -> tuple[int, int, int]:
    target = _normalize_name(request_name)
    name = _normalize_name(entity.get("name") or "")
    publisher = _normalize_name(entity.get("publisher_name") or "")
    exact = int(name == target)
    prefix = int(name.startswith(target) or target.startswith(name))
    publisher_bonus = int(target in publisher or publisher in target)
    return (exact, prefix, publisher_bonus)


def _extract_profile(entity: dict, request: dict) -> AdvertiserProfile:
    ios_apps = entity.get("ios_apps") or []
    android_apps = entity.get("android_apps") or []
    primary_ios = ios_apps[0] if ios_apps else {}
    primary_android = android_apps[0] if android_apps else {}
    categories = entity.get("categories") or []
    category = request.get("category")
    if not category and categories:
        category = CATEGORY_NAMES.get(str(categories[0]))

    return AdvertiserProfile.model_validate(
        {
            "name": request["name"],
            "domain": request.get("domain"),
            "category": category,
            "countries": request.get("countries") or ["US"],
            "platforms": {
                "sensortower": {
                    "unified_app_id": _string_or_none(entity.get("app_id") or entity.get("id")),
                    "publisher_id": _string_or_none(entity.get("publisher_id")),
                    "ios_app_id": _string_or_none(primary_ios.get("app_id")),
                    "android_package": _string_or_none(primary_android.get("app_id")),
                },
                "adclarity": {
                    "advertiser_id": _string_or_none(request.get("adclarity_advertiser_id")),
                    "brand_id": _string_or_none(request.get("adclarity_brand_id")),
                },
            },
        }
    )


class SensorTowerOnboardingService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.browser = BrowserManager(settings)

    async def onboard_batch(
        self,
        requests: list[dict],
        *,
        headless: bool = True,
        use_cdp: bool = False,
    ) -> list[OnboardingResult]:
        async with self.browser.session("sensortower", headless=headless, use_cdp=use_cdp) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await self.browser.apply_stealth(page)
            await page.goto(self.settings.sensortower_base_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2_000)
            if not await self._validate_session(page):
                raise RuntimeError("SensorTower session has expired. Run 'adintel login sensortower' first.")

            results: list[OnboardingResult] = []
            for request in requests:
                results.append(await self._resolve_one(page, request))
            return results

    async def _validate_session(self, page: Page) -> bool:
        response = await page.request.get(f"{self.settings.sensortower_base_url}/api/auth/me", timeout=10_000)
        return response.status not in (401, 403)

    async def _resolve_one(self, page: Page, request: dict) -> OnboardingResult:
        name = request.get("name")
        if not name:
            return OnboardingResult(name="<missing>", status="invalid", message="Missing advertiser name.")

        query = urlencode(
            {
                "entity_type": "app",
                "expand_entities": "true",
                "flags": "false",
                "limit": 10,
                "mark_usage_disabled_apps": "false",
                "os": "unified",
                "term": name,
            }
        )
        response = await page.request.get(f"{self.settings.sensortower_base_url}/api/autocomplete_search?{query}", timeout=15_000)
        if response.status != 200:
            return OnboardingResult(name=name, status="error", message=f"Search failed with HTTP {response.status}.")

        data = await response.json()
        entities = ((data or {}).get("data") or {}).get("entities") or []
        if not entities:
            return OnboardingResult(name=name, status="not_found", message="No SensorTower app match found.")

        ranked = sorted(entities, key=lambda entity: _candidate_score(name, entity), reverse=True)
        best = ranked[0]
        best_score = _candidate_score(name, best)
        second_score = _candidate_score(name, ranked[1]) if len(ranked) > 1 else None
        if best_score[0] == 0 and best_score[1] == 0:
            return OnboardingResult(
                name=name,
                status="ambiguous",
                message="No high-confidence match found.",
                candidates=[{"name": entity.get("name"), "publisher_name": entity.get("publisher_name"), "app_id": entity.get("app_id")} for entity in ranked[:5]],
            )
        if second_score is not None and second_score == best_score:
            return OnboardingResult(
                name=name,
                status="ambiguous",
                message="Multiple SensorTower matches look equally likely.",
                candidates=[{"name": entity.get("name"), "publisher_name": entity.get("publisher_name"), "app_id": entity.get("app_id")} for entity in ranked[:5]],
            )

        advertiser = _extract_profile(best, request)
        return OnboardingResult(
            name=name,
            status="matched",
            message=f"Matched SensorTower app '{best.get('name')}'.",
            advertiser=advertiser,
        )


def onboard_batch_sync(
    settings: AppSettings,
    requests: list[dict],
    *,
    headless: bool = True,
    use_cdp: bool = False,
) -> list[OnboardingResult]:
    service = SensorTowerOnboardingService(settings)
    return asyncio.run(service.onboard_batch(requests, headless=headless, use_cdp=use_cdp))
