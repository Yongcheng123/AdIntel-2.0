from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AdvertiserRecord(Base):
    __tablename__ = "advertisers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    countries_csv: Mapped[str] = mapped_column(String(255), default="US")
    sensortower_unified_app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sensortower_publisher_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sensortower_ios_app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sensortower_android_package: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=lambda: datetime.now(UTC)
    )


class ScrapeRunRecord(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="running")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScrapeRunMetricRecord(Base):
    __tablename__ = "scrape_run_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scrape_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_runs.id"), index=True)
    metric_name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    records_written: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequestedAdvertiserRecord(Base):
    __tablename__ = "requested_advertisers"
    __table_args__ = (UniqueConstraint("name", name="uq_requested_advertisers_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerDownloadRecord(Base):
    __tablename__ = "sensortower_downloads"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "period_date", "granularity", "country", "os", name="uq_sensortower_downloads"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    period_date: Mapped[date] = mapped_column(Date)
    granularity: Mapped[str] = mapped_column(String(32), default="day")
    country: Mapped[str] = mapped_column(String(8), default="US")
    os: Mapped[str] = mapped_column(String(32), default="unified")
    downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerUsageRecord(Base):
    __tablename__ = "sensortower_usage"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "period_date", "country", name="uq_sensortower_usage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    period_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    avg_dau: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_spent_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    sessions_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerRetentionRecord(Base):
    __tablename__ = "sensortower_retention"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "cohort_date", "country", name="uq_sensortower_retention"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    cohort_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    d1: Mapped[float | None] = mapped_column(Float, nullable=True)
    d3: Mapped[float | None] = mapped_column(Float, nullable=True)
    d7: Mapped[float | None] = mapped_column(Float, nullable=True)
    d14: Mapped[float | None] = mapped_column(Float, nullable=True)
    d30: Mapped[float | None] = mapped_column(Float, nullable=True)
    d60: Mapped[float | None] = mapped_column(Float, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerImpressionShareRecord(Base):
    __tablename__ = "sensortower_impression_share"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "period_date", "network", "country", name="uq_sensortower_impression_share"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    period_date: Mapped[date] = mapped_column(Date)
    network: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(8), default="US")
    sov_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerDemographicRecord(Base):
    __tablename__ = "sensortower_demographics"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "country", "age_bracket", name="uq_sensortower_demographics"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    age_bracket: Mapped[str] = mapped_column(String(32))
    male_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    female_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerRankingRecord(Base):
    __tablename__ = "sensortower_rankings"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "rank_date", "country", "category", "chart_type", name="uq_sensortower_rankings"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    rank_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    category: Mapped[str] = mapped_column(String(255))
    chart_type: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerReviewRecord(Base):
    __tablename__ = "sensortower_reviews"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "period_date", "country", name="uq_sensortower_reviews"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    period_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    star_1_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    star_2_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    star_3_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    star_4_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    star_5_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerReviewTextRecord(Base):
    __tablename__ = "sensortower_review_texts"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "review_id", name="uq_sensortower_review_texts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    review_id: Mapped[int] = mapped_column(BigInteger)
    review_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    star_rating: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str] = mapped_column(String(16), default="ios")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerCreativeRecord(Base):
    __tablename__ = "sensortower_creatives"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "creative_id", name="uq_sensortower_creatives"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    creative_id: Mapped[str] = mapped_column(String(255))
    creative_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    network: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerAsoKeywordRecord(Base):
    __tablename__ = "sensortower_aso_keywords"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "keyword", "country", "device", name="uq_sensortower_aso_keywords"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    keyword: Mapped[str] = mapped_column(String(255))
    keyword_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    traffic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    opportunity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    device: Mapped[str] = mapped_column(String(32), default="iphone")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerMarketTopAppRecord(Base):
    __tablename__ = "sensortower_market_top_apps"
    __table_args__ = (
        UniqueConstraint(
            "scrape_month", "country", "category", "os", "rank",
            name="uq_sensortower_market_top_apps",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scrape_month: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    category: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(32), default="unified")
    rank: Mapped[int] = mapped_column(Integer)
    app_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publisher_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unified_app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    downloads: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revenue: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    dau: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impression_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    ad_on_admob: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_facebook: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_instagram: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_tiktok: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_youtube: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_snapchat: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_applovin: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_unity: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_on_mintegral: Mapped[bool] = mapped_column(Boolean, default=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SocialPetaCreativeRecord(Base):
    __tablename__ = "socialpeta_creatives"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "country", "creative_id", name="uq_socialpeta_creatives"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    creative_id: Mapped[str] = mapped_column(String(255))
    target_query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    advertiser_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creative_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_to_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creative_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ads_type: Mapped[int | None] = mapped_column(Integer, nullable=True)
    primary_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    landing_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_urls: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    first_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    active_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impression: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    popularity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creative_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at_platform: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_page_id: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_store_url: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_page_analysis: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SocialPetaCreativeChannelRecord(Base):
    __tablename__ = "socialpeta_creative_channels"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "country", "creative_id", "channel", name="uq_socialpeta_creative_channels"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    creative_id: Mapped[str] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(64))
    first_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen: Mapped[date | None] = mapped_column(Date, nullable=True)
    active_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SocialPetaCreativeTagRecord(Base):
    __tablename__ = "socialpeta_creative_tags"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "country", "creative_id", "tag_category", "tag_value",
            name="uq_socialpeta_creative_tags",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    creative_id: Mapped[str] = mapped_column(String(255))
    tag_category: Mapped[str] = mapped_column(String(64), default="creative_type")
    tag_value: Mapped[str] = mapped_column(String(128))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class OtterlyPromptRecord(Base):
    __tablename__ = "otterlyai_prompts"
    __table_args__ = (
        UniqueConstraint(
            "target_brand_or_domain_name",
            "country_code",
            "ai_engine",
            "prompt_text",
            "query_window_end_date",
            name="uq_otterlyai_prompts",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_brand_or_domain_name: Mapped[str] = mapped_column(String(255), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    query_window_start_date: Mapped[date] = mapped_column(Date)
    query_window_end_date: Mapped[date] = mapped_column(Date)
    prompt_text: Mapped[str] = mapped_column(Text)
    prompt_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_engine: Mapped[str] = mapped_column(String(64), index=True)
    domain_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    competitors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class OtterlyCitationRecord(Base):
    __tablename__ = "otterlyai_citations"
    __table_args__ = (
        UniqueConstraint(
            "target_brand_or_domain_name",
            "country_code",
            "ai_engine",
            "cited_url",
            "query_window_end_date",
            name="uq_otterlyai_citations",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_brand_or_domain_name: Mapped[str] = mapped_column(String(255), index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    query_window_start_date: Mapped[date] = mapped_column(Date)
    query_window_end_date: Mapped[date] = mapped_column(Date)
    ai_engine: Mapped[str] = mapped_column(String(64), index=True)
    cited_url: Mapped[str] = mapped_column(Text)
    cited_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    competitors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class AppFollowReviewRecord(Base):
    __tablename__ = "appfollow_reviews"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "review_id", name="uq_appfollow_reviews"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    review_id: Mapped[str] = mapped_column(String(255))
    review_date: Mapped[date] = mapped_column(Date)
    country: Mapped[str] = mapped_column(String(8), default="US")
    star_rating: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str] = mapped_column(String(16), default="ios")
    appfollow_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
