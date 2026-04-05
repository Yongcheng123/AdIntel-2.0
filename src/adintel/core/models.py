from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PlatformName(StrEnum):
    ADCLARITY = "adclarity"
    SENSORTOWER = "sensortower"


class PlatformIdentifiers(BaseModel):
    advertiser_id: str | None = None
    brand_id: str | None = None
    unified_app_id: str | None = None
    publisher_id: str | None = None
    ios_app_id: str | None = None
    android_package: str | None = None


class AdvertiserPlatforms(BaseModel):
    adclarity: PlatformIdentifiers = Field(default_factory=PlatformIdentifiers)
    sensortower: PlatformIdentifiers = Field(default_factory=PlatformIdentifiers)


class AdvertiserProfile(BaseModel):
    name: str
    domain: str | None = None
    category: str | None = None
    countries: list[str] = Field(default_factory=lambda: ["US"])
    platforms: AdvertiserPlatforms = Field(default_factory=AdvertiserPlatforms)


class AdvertiserCatalog(BaseModel):
    advertisers: list[AdvertiserProfile] = Field(default_factory=list)


class CollectorRunRequest(BaseModel):
    advertiser: AdvertiserProfile
    platform: PlatformName
    countries: list[str]
    headless: bool = False
    debug: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CollectorRunResult(BaseModel):
    platform: PlatformName
    advertiser_name: str
    status: str
    message: str
    records_written: int = 0
    metadata: dict = Field(default_factory=dict)
