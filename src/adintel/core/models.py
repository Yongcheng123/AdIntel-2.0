from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class PlatformName(StrEnum):
    SENSORTOWER = "sensortower"
    OTTERLY = "otterly"
    SOCIALPETA = "socialpeta"
    APPFOLLOW = "appfollow"


class PlatformIdentifiers(BaseModel):
    advertiser_id: str | None = None
    brand_id: str | None = None
    unified_app_id: str | None = None
    publisher_id: str | None = None
    ios_app_id: str | None = None
    ios_app_ids_by_country: dict[str, str] = Field(default_factory=dict)
    android_package: str | None = None
    android_packages_by_country: dict[str, str] = Field(default_factory=dict)

    def resolve_ios_app_id(self, country: str) -> str | None:
        return self.ios_app_ids_by_country.get(country) or self.ios_app_id

    def resolve_android_package(self, country: str) -> str | None:
        return self.android_packages_by_country.get(country) or self.android_package

    @field_validator("ios_app_ids_by_country", "android_packages_by_country")
    @classmethod
    def validate_country_override_keys(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for country, identifier in value.items():
            normalized_country = cls._normalize_country_code(country)
            normalized[normalized_country] = identifier
        return normalized

    @staticmethod
    def _normalize_country_code(country: str) -> str:
        normalized = country.strip().upper()
        if len(normalized) != 2 or not normalized.isalpha():
            raise ValueError(f"Invalid country code '{country}'. Use ISO-3166 alpha-2 codes like US or TR.")
        return normalized


class AdvertiserPlatforms(BaseModel):
    sensortower: PlatformIdentifiers = Field(default_factory=PlatformIdentifiers)


class AdvertiserProfile(BaseModel):
    name: str
    domain: str | None = None
    category: str | None = None
    countries: list[str] = Field(default_factory=lambda: ["US"])
    platforms: AdvertiserPlatforms = Field(default_factory=AdvertiserPlatforms)

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for country in value or ["US"]:
            normalized_country = PlatformIdentifiers._normalize_country_code(country)
            if normalized_country in seen:
                raise ValueError(f"Duplicate country code '{normalized_country}' in advertiser countries.")
            seen.add(normalized_country)
            normalized.append(normalized_country)
        return normalized or ["US"]


class AdvertiserCatalog(BaseModel):
    advertisers: list[AdvertiserProfile] = Field(default_factory=list)


class CompetitorGroup(BaseModel):
    advertiser: str
    competitors: list[str] = Field(default_factory=list)
    country: str | None = None
    notes: str | None = None


class CompetitorGroupCatalog(BaseModel):
    groups: list[CompetitorGroup] = Field(default_factory=list)


class CollectorRunRequest(BaseModel):
    advertiser: AdvertiserProfile
    platform: PlatformName
    countries: list[str]
    metrics: list[str] | None = None
    headless: bool = False
    debug: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra: dict = Field(default_factory=dict)  # platform-specific data (e.g. appfollow_item_id)


class CollectorRunResult(BaseModel):
    platform: PlatformName
    advertiser_name: str
    status: str
    message: str
    records_written: int = 0
    metadata: dict = Field(default_factory=dict)
