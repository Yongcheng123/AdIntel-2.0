from __future__ import annotations

import json
import os
from pathlib import Path

from datetime import date

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import desc, select

from adintel.core.settings import ROOT_DIR, get_settings
from adintel.db.models import (
    RequestedAdvertiserRecord,
    SensorTowerAsoKeywordRecord,
    SensorTowerCreativeRecord,
    SensorTowerDemographicRecord,
    SensorTowerDownloadRecord,
    SensorTowerImpressionShareRecord,
    SensorTowerRankingRecord,
    SensorTowerReviewRecord,
    SensorTowerReviewTextRecord,
    SensorTowerRetentionRecord,
    SensorTowerUsageRecord,
)
from adintel.db.repositories import AdvertiserRepository, CollectionHealthRepository, RequestedAdvertiserRepository
from adintel.db.repositories import ScrapeRunRepository
from adintel.db.session import build_session_factory


SCHEMA_PATH = ROOT_DIR / "sql" / "schema.sql"


def _session_factory():
    return build_session_factory(get_settings())


def _schema_text() -> str:
    if not SCHEMA_PATH.exists():
        return "Schema file not found."
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _serialize_requested(row: RequestedAdvertiserRecord) -> dict:
    return {
        "name": row.name,
        "requested_by": row.requested_by,
        "context": row.context,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_scrape_run(row) -> dict:
    return {
        "id": row.id,
        "advertiser_name": row.advertiser_name,
        "platform": row.platform,
        "status": row.status,
        "message": row.message,
        "metadata": row.result_metadata,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


# Maps metric names to (model, date_column, value_columns) for time-series queries
_METRIC_MAP = {
    "downloads": (
        SensorTowerDownloadRecord,
        "period_date",
        ["downloads", "revenue", "os"],
    ),
    "usage": (
        SensorTowerUsageRecord,
        "period_date",
        ["avg_dau", "time_spent_min", "sessions_per_day"],
    ),
    "retention": (
        SensorTowerRetentionRecord,
        "cohort_date",
        ["d1", "d3", "d7", "d14", "d30", "d60"],
    ),
    "impression_share": (
        SensorTowerImpressionShareRecord,
        "period_date",
        ["network", "sov_pct"],
    ),
    "rankings": (
        SensorTowerRankingRecord,
        "rank_date",
        ["category", "chart_type", "rank", "is_featured"],
    ),
    "reviews": (
        SensorTowerReviewRecord,
        "period_date",
        ["avg_rating", "rating_count", "star_1_count", "star_2_count", "star_3_count", "star_4_count", "star_5_count"],
    ),
}


def _build_summary(advertiser_name: str, country: str | None = None) -> dict:
    with _session_factory()() as session:
        advertisers = AdvertiserRepository(session)
        advertiser = advertisers.get(advertiser_name)
        if advertiser is None:
            return {
                "found": False,
                "advertiser_name": advertiser_name,
                "message": "Advertiser not found.",
            }

        def _q(model):
            """Build a base query with advertiser + optional country filter."""
            q = select(model).where(model.advertiser_name == advertiser_name)
            if country and hasattr(model, "country"):
                q = q.where(model.country == country)
            return q

        latest_download = session.scalar(
            _q(SensorTowerDownloadRecord)
            .order_by(desc(SensorTowerDownloadRecord.period_date))
        )
        latest_usage = session.scalar(
            _q(SensorTowerUsageRecord)
            .order_by(desc(SensorTowerUsageRecord.period_date))
        )
        latest_retention = session.scalar(
            _q(SensorTowerRetentionRecord)
            .order_by(desc(SensorTowerRetentionRecord.cohort_date))
        )
        latest_ranking = session.scalar(
            _q(SensorTowerRankingRecord)
            .order_by(desc(SensorTowerRankingRecord.rank_date))
        )
        q_imp = _q(SensorTowerImpressionShareRecord).where(
            SensorTowerImpressionShareRecord.network == "all",
        )
        latest_impression_share = session.scalar(
            q_imp.order_by(desc(SensorTowerImpressionShareRecord.period_date))
        )
        demographics = session.scalars(
            _q(SensorTowerDemographicRecord)
            .order_by(SensorTowerDemographicRecord.age_bracket)
        ).all()
        latest_reviews = session.scalar(
            _q(SensorTowerReviewRecord)
            .order_by(desc(SensorTowerReviewRecord.period_date))
        )
        recent_review_texts = session.scalars(
            _q(SensorTowerReviewTextRecord)
            .order_by(desc(SensorTowerReviewTextRecord.review_date))
            .limit(5)
        ).all()
        recent_creatives = session.scalars(
            _q(SensorTowerCreativeRecord)
            .order_by(desc(SensorTowerCreativeRecord.first_seen))
            .limit(5)
        ).all()
        aso_keywords = session.scalars(
            _q(SensorTowerAsoKeywordRecord)
            .order_by(SensorTowerAsoKeywordRecord.rank)
            .limit(10)
        ).all()

    return {
        "found": True,
        "advertiser": advertiser.model_dump(),
        "sensortower": {
            "latest_download": (
                {
                    "period_date": latest_download.period_date.isoformat(),
                    "downloads": latest_download.downloads,
                    "revenue": float(latest_download.revenue) if latest_download.revenue is not None else None,
                    "country": latest_download.country,
                    "os": latest_download.os,
                }
                if latest_download
                else None
            ),
            "latest_usage": (
                {
                    "period_date": latest_usage.period_date.isoformat(),
                    "avg_dau": latest_usage.avg_dau,
                    "time_spent_min": latest_usage.time_spent_min,
                    "sessions_per_day": latest_usage.sessions_per_day,
                    "country": latest_usage.country,
                }
                if latest_usage
                else None
            ),
            "latest_retention": (
                {
                    "cohort_date": latest_retention.cohort_date.isoformat(),
                    "d1": latest_retention.d1,
                    "d7": latest_retention.d7,
                    "d30": latest_retention.d30,
                    "country": latest_retention.country,
                }
                if latest_retention
                else None
            ),
            "latest_impression_share": (
                {
                    "period_date": latest_impression_share.period_date.isoformat(),
                    "network": latest_impression_share.network,
                    "sov_pct": latest_impression_share.sov_pct,
                    "country": latest_impression_share.country,
                }
                if latest_impression_share
                else None
            ),
            "latest_ranking": (
                {
                    "rank_date": latest_ranking.rank_date.isoformat(),
                    "category": latest_ranking.category,
                    "chart_type": latest_ranking.chart_type,
                    "rank": latest_ranking.rank,
                    "country": latest_ranking.country,
                }
                if latest_ranking
                else None
            ),
            "demographics": [
                {
                    "age_bracket": row.age_bracket,
                    "male_pct": row.male_pct,
                    "female_pct": row.female_pct,
                    "country": row.country,
                }
                for row in demographics
            ],
            "latest_reviews": (
                {
                    "period_date": latest_reviews.period_date.isoformat(),
                    "avg_rating": latest_reviews.avg_rating,
                    "rating_count": latest_reviews.rating_count,
                    "star_1_count": latest_reviews.star_1_count,
                    "star_5_count": latest_reviews.star_5_count,
                    "country": latest_reviews.country,
                }
                if latest_reviews
                else None
            ),
            "recent_review_texts": [
                {
                    "review_id": row.review_id,
                    "review_date": row.review_date.isoformat(),
                    "country": row.country,
                    "star_rating": row.star_rating,
                    "title": row.title,
                    "body": row.body,
                    "sentiment": row.sentiment,
                    "tags": row.tags,
                }
                for row in recent_review_texts
            ],
            "recent_creatives": [
                {
                    "creative_id": row.creative_id,
                    "creative_type": row.creative_type,
                    "network": row.network,
                    "thumbnail_url": row.thumbnail_url,
                    "duration_bucket": row.duration_bucket,
                    "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                }
                for row in recent_creatives
            ],
            "aso_keywords": [
                {
                    "keyword": row.keyword,
                    "keyword_type": row.keyword_type,
                    "rank": row.rank,
                    "traffic_score": row.traffic_score,
                    "opportunity_score": row.opportunity_score,
                    "country": row.country,
                    "device": row.device,
                }
                for row in aso_keywords
            ],
        },
    }


def create_mcp_server() -> FastMCP:
    # For the public Vercel deployment, serve Streamable HTTP at the function
    # root and disable localhost-only host validation. Local stdio usage is
    # unaffected because these HTTP settings are only relevant for HTTP
    # transports.
    is_vercel = bool(os.getenv("VERCEL"))
    server = FastMCP(
        name="AdIntel",
        instructions=(
            "AdIntel exposes structured advertiser metadata and SensorTower-derived intelligence "
            "from the shared PostgreSQL database."
        ),
        streamable_http_path="/",
        transport_security=(
            TransportSecuritySettings(enable_dns_rebinding_protection=False)
            if is_vercel
            else None
        ),
    )

    @server.resource(
        "schema://adintel",
        name="adintel_schema",
        description="Canonical SQL schema for the AdIntel rewrite.",
        mime_type="text/sql",
    )
    def schema_resource() -> str:
        return _schema_text()

    @server.tool(
        name="list_advertisers",
        description="List advertisers currently stored in AdIntel.",
    )
    def list_advertisers() -> str:
        with _session_factory()() as session:
            advertisers = AdvertiserRepository(session).list()
        return json.dumps(
            {"advertisers": [advertiser.model_dump() for advertiser in advertisers]},
            indent=2,
        )

    @server.tool(
        name="get_advertiser_summary",
        description=(
            "Get the latest SensorTower summary for a specific advertiser. "
            "Optionally filter by country code (e.g., 'US', 'BR')."
        ),
    )
    def get_advertiser_summary(advertiser_name: str, country: str | None = None) -> str:
        return json.dumps(_build_summary(advertiser_name, country=country), indent=2)

    @server.tool(
        name="request_advertiser",
        description="Log a missing advertiser request for later onboarding.",
    )
    def request_advertiser(
        name: str,
        requested_by: str | None = None,
        context: str | None = None,
    ) -> str:
        with _session_factory()() as session:
            RequestedAdvertiserRepository(session).request(
                name=name,
                requested_by=requested_by,
                context=context,
            )
        return json.dumps(
            {
                "status": "logged",
                "name": name,
                "requested_by": requested_by,
                "context": context,
            },
            indent=2,
        )

    @server.tool(
        name="list_requested_advertisers",
        description="List advertisers that have been requested but not yet onboarded.",
    )
    def list_requested_advertisers() -> str:
        with _session_factory()() as session:
            rows = session.scalars(
                select(RequestedAdvertiserRecord).order_by(desc(RequestedAdvertiserRecord.created_at))
            ).all()
        return json.dumps(
            {"requested_advertisers": [_serialize_requested(row) for row in rows]},
            indent=2,
        )

    @server.tool(
        name="read_schema_text",
        description="Return the canonical SQL schema as text when the client needs raw schema content.",
    )
    def read_schema_text() -> str:
        return _schema_text()

    @server.tool(
        name="get_collection_health",
        description=(
            "Get collection health for an advertiser (or all advertisers). "
            "Shows last successful run, consecutive failures, data staleness, and recent errors."
        ),
    )
    def get_collection_health(advertiser_name: str | None = None) -> str:
        with _session_factory()() as session:
            repo = CollectionHealthRepository(session)
            if advertiser_name:
                health = repo.get_health_for_advertiser(advertiser_name)
            else:
                health = repo.get_all_health()
        return json.dumps({"collection_health": health}, indent=2)

    @server.tool(
        name="get_collection_alerts",
        description=(
            "Get active collection alerts: stale data, consecutive failures, and advertisers that have never succeeded. "
            "Use this to check if data is trustworthy and up-to-date."
        ),
    )
    def get_collection_alerts(
        stale_hours: float = 48,
        max_consecutive_failures: int = 3,
    ) -> str:
        with _session_factory()() as session:
            repo = CollectionHealthRepository(session)
            alerts = repo.get_alerts(
                stale_hours=stale_hours,
                max_consecutive_failures=max_consecutive_failures,
            )
        return json.dumps(
            {
                "alerts": alerts,
                "alert_count": len(alerts),
                "thresholds": {
                    "stale_hours": stale_hours,
                    "max_consecutive_failures": max_consecutive_failures,
                },
            },
            indent=2,
        )

    @server.tool(
        name="get_recent_collection_runs",
        description=(
            "Get recent collection runs saved in the server database, including persisted result metadata "
            "such as records written and per-metric outcomes."
        ),
    )
    def get_recent_collection_runs(
        advertiser_name: str | None = None,
        platform: str | None = None,
        limit: int = 20,
    ) -> str:
        with _session_factory()() as session:
            runs = ScrapeRunRepository(session).list_recent(
                advertiser_name=advertiser_name,
                platform=platform,
                limit=max(1, min(limit, 100)),
            )
        return json.dumps({"runs": [_serialize_scrape_run(row) for row in runs]}, indent=2)

    @server.tool(
        name="get_metric_timeseries",
        description=(
            "Get daily time-series data for a specific metric and advertiser. "
            "Available metrics: downloads, usage, retention, impression_share, rankings, reviews. "
            "Returns up to 90 days of daily data points for trend analysis."
        ),
    )
    def get_metric_timeseries(
        advertiser_name: str,
        metric: str,
        country: str = "US",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 90,
    ) -> str:
        if metric not in _METRIC_MAP:
            return json.dumps({
                "error": f"Unknown metric '{metric}'. Available: {', '.join(_METRIC_MAP.keys())}",
            })

        model, date_col_name, value_cols = _METRIC_MAP[metric]
        date_col = getattr(model, date_col_name)

        with _session_factory()() as session:
            q = (
                select(model)
                .where(model.advertiser_name == advertiser_name)
            )
            if hasattr(model, "country"):
                q = q.where(model.country == country)
            if start_date:
                q = q.where(date_col >= date.fromisoformat(start_date))
            if end_date:
                q = q.where(date_col <= date.fromisoformat(end_date))

            rows = session.scalars(
                q.order_by(desc(date_col)).limit(limit)
            ).all()

        data_points = []
        for row in reversed(rows):
            point = {"date": getattr(row, date_col_name).isoformat()}
            if hasattr(row, "country"):
                point["country"] = row.country
            for col in value_cols:
                val = getattr(row, col, None)
                if val is not None and hasattr(val, "__float__"):
                    val = float(val)
                point[col] = val
            data_points.append(point)

        return json.dumps({
            "advertiser_name": advertiser_name,
            "metric": metric,
            "country": country,
            "count": len(data_points),
            "data": data_points,
        }, indent=2)

    @server.tool(
        name="compare_advertisers",
        description=(
            "Compare the latest metrics for two or more advertisers side by side. "
            "Available metrics: downloads, usage, retention, impression_share, rankings, reviews. "
            "Useful for competitive analysis."
        ),
    )
    def compare_advertisers(
        advertiser_names: str,
        metric: str = "downloads",
        country: str = "US",
    ) -> str:
        names = [n.strip() for n in advertiser_names.split(",") if n.strip()]
        if metric not in _METRIC_MAP:
            return json.dumps({
                "error": f"Unknown metric '{metric}'. Available: {', '.join(_METRIC_MAP.keys())}",
            })

        model, date_col_name, value_cols = _METRIC_MAP[metric]
        date_col = getattr(model, date_col_name)
        results = {}

        with _session_factory()() as session:
            for name in names:
                q = (
                    select(model)
                    .where(model.advertiser_name == name)
                )
                if hasattr(model, "country"):
                    q = q.where(model.country == country)

                row = session.scalar(q.order_by(desc(date_col)))
                if row is None:
                    results[name] = None
                    continue

                point = {"date": getattr(row, date_col_name).isoformat()}
                for col in value_cols:
                    val = getattr(row, col, None)
                    if val is not None and hasattr(val, "__float__"):
                        val = float(val)
                    point[col] = val
                results[name] = point

        return json.dumps({
            "metric": metric,
            "country": country,
            "comparison": results,
        }, indent=2)

    return server


def serve_stdio() -> None:
    create_mcp_server().run("stdio")


def main() -> None:
    serve_stdio()


if __name__ == "__main__":
    main()
