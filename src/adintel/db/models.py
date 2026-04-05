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
    adclarity_advertiser_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adclarity_brand_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    __tablename__ = "st_downloads"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "period_date", "granularity", "country", "os", name="uq_st_downloads"
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
    __tablename__ = "st_usage"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "period_date", "country", name="uq_st_usage"),
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
    __tablename__ = "st_retention"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "cohort_date", "country", name="uq_st_retention"),
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
    __tablename__ = "st_impression_share"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "period_date", "network", "country", name="uq_st_impression_share"
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
    __tablename__ = "st_demographics"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "country", "age_bracket", name="uq_st_demographics"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str] = mapped_column(String(8), default="US")
    age_bracket: Mapped[str] = mapped_column(String(32))
    male_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    female_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class SensorTowerRankingRecord(Base):
    __tablename__ = "st_rankings"
    __table_args__ = (
        UniqueConstraint(
            "advertiser_name", "rank_date", "country", "category", "chart_type", name="uq_st_rankings"
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
    __tablename__ = "st_reviews"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "period_date", "country", name="uq_st_reviews"),
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
    __tablename__ = "st_review_texts"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "review_id", name="uq_st_review_texts"),
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
    __tablename__ = "st_creatives"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "creative_id", name="uq_st_creatives"),
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
    __tablename__ = "st_aso_keywords"
    __table_args__ = (
        UniqueConstraint("advertiser_name", "keyword", "country", "device", name="uq_st_aso_keywords"),
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
